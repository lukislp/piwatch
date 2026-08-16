"""In-memory cluster state with ring-buffer history and WebSocket pub/sub.

Each backend replica maintains its own full copy of the state by watching
the cluster independently. Nothing is persisted -- on failover the client
simply reconnects to the surviving replica and receives its full state.
"""
from __future__ import annotations

import asyncio
import csv
import io
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

# ~3h of history at one sample every 10s
HISTORY_LEN = 1080
EVENTS_LEN = 200
CHECK_HISTORY_LEN = 500

# Fields captured per node_history sample -- shared with collectors/history.py, which
# persists exactly this shape to survive a pod restart (see its module docstring).
NODE_HISTORY_FIELDS = (
    "cpu_pct", "mem_pct", "temp_c",
    "nvme_read_bytes_per_s", "nvme_write_bytes_per_s",
    "net_rx_bytes_per_s", "net_tx_bytes_per_s",
)


def now() -> float:
    return time.time()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


class ClusterState:
    def __init__(self) -> None:
        self.started_at = now()
        self.demo_mode = False

        # Live objects, keyed by name / uid
        self.nodes: dict[str, dict[str, Any]] = {}
        self.pods: dict[str, dict[str, Any]] = {}         # key: ns/name
        self.deployments: dict[str, dict[str, Any]] = {}  # key: ns/name
        self.statefulsets: dict[str, dict[str, Any]] = {}  # key: ns/name
        self.daemonsets: dict[str, dict[str, Any]] = {}    # key: ns/name
        self.services: dict[str, dict[str, Any]] = {}      # key: ns/name; LoadBalancer-type only
        self.hpas: dict[str, dict[str, Any]] = {}           # key: ns/name
        self.network_policies: dict[str, dict[str, Any]] = {}  # key: ns/name
        self.orphaned_pvs: dict[str, dict[str, Any]] = {}  # key: PV name; Released/Failed only
        # key: ns/name; metadata only (name/type/key count/age), never .data -- see
        # collectors/k8s_watch.py's map_secret. Noisy auto-managed types (ServiceAccount
        # tokens, Helm release storage) are filtered out before ever reaching this dict.
        self.secrets: dict[str, dict[str, Any]] = {}
        # key: ns/name; kube-root-ca.crt (auto-created in every namespace) filtered out.
        self.configmaps: dict[str, dict[str, Any]] = {}
        self.events: deque[dict[str, Any]] = deque(maxlen=EVENTS_LEN)

        # Hardware + metrics per node (latest sample)
        self.node_metrics: dict[str, dict[str, Any]] = {}
        self.hardware: dict[str, dict[str, Any]] = {}

        # CPU/RAM usage per pod (latest sample only -- unlike nodes, pods churn
        # often enough that a per-pod ring buffer would be an unbounded memory sink)
        self.pod_metrics: dict[str, dict[str, Any]] = {}

        # Time series: node -> deque[{t, cpu_pct, mem_pct, temp_c, nvme_read_bytes_per_s,
        # nvme_write_bytes_per_s, net_rx_bytes_per_s, net_tx_bytes_per_s}]
        self.node_history: dict[str, deque[dict[str, Any]]] = {}

        # Healthchecks: name -> {config, last, history: deque[{t, ok, ms}]}
        self.healthchecks: dict[str, dict[str, Any]] = {}

        # Flux GitOps status (all ns/name -> mapped status dict). All optional -- stay
        # empty on clusters that don't run Flux, or don't use image automation; see
        # collectors/flux.py.
        self.flux_kustomizations: dict[str, dict[str, Any]] = {}
        self.flux_git_repositories: dict[str, dict[str, Any]] = {}
        self.flux_image_policies: dict[str, dict[str, Any]] = {}
        self.flux_image_automations: dict[str, dict[str, Any]] = {}

        # PersistentVolumeClaims (ns/name -> mapped dict). Capacity/binding metadata is
        # always populated; usage_bytes/usage_pct stay None unless PIWATCH_PROMETHEUS_URL
        # is set -- see collectors/pvc.py.
        self.pvcs: dict[str, dict[str, Any]] = {}

        # Gateway API routing status (ns/name -> mapped dict). Optional -- stays empty on
        # clusters that don't use the Gateway API; see collectors/gateway.py.
        self.gateways: dict[str, dict[str, Any]] = {}
        self.http_routes: dict[str, dict[str, Any]] = {}
        self.rate_limit_policies: dict[str, dict[str, Any]] = {}

        self._subscribers: set[asyncio.Queue] = set()

    # ---------------- pub/sub ----------------

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=512)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, msg_type: str, data: Any) -> None:
        """Broadcast a delta message to all connected WebSocket clients."""
        msg = {"type": msg_type, "t": now(), "data": data}
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # Slow consumer: drop oldest to keep the stream moving.
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except Exception:
                    pass

    # ---------------- mutations (used by collectors) ----------------

    def upsert_node(self, name: str, obj: dict) -> None:
        self.nodes[name] = obj
        self.node_history.setdefault(name, deque(maxlen=HISTORY_LEN))
        self.publish("node", obj)

    def remove_node(self, name: str) -> None:
        self.nodes.pop(name, None)
        self.publish("node_deleted", {"name": name})

    def upsert_pod(self, key: str, obj: dict) -> None:
        self.pods[key] = obj
        self.publish("pod", obj)

    def remove_pod(self, key: str) -> None:
        self.pods.pop(key, None)
        self.pod_metrics.pop(key, None)
        self.publish("pod_deleted", {"key": key})

    def upsert_deployment(self, key: str, obj: dict) -> None:
        self.deployments[key] = obj
        self.publish("deployment", obj)

    def remove_deployment(self, key: str) -> None:
        self.deployments.pop(key, None)
        self.publish("deployment_deleted", {"key": key})

    def upsert_statefulset(self, key: str, obj: dict) -> None:
        self.statefulsets[key] = obj
        self.publish("statefulset", obj)

    def remove_statefulset(self, key: str) -> None:
        self.statefulsets.pop(key, None)
        self.publish("statefulset_deleted", {"key": key})

    def upsert_daemonset(self, key: str, obj: dict) -> None:
        self.daemonsets[key] = obj
        self.publish("daemonset", obj)

    def remove_daemonset(self, key: str) -> None:
        self.daemonsets.pop(key, None)
        self.publish("daemonset_deleted", {"key": key})

    def upsert_service(self, key: str, obj: dict) -> None:
        self.services[key] = obj
        self.publish("service", obj)

    def remove_service(self, key: str) -> None:
        self.services.pop(key, None)
        self.publish("service_deleted", {"key": key})

    def upsert_hpa(self, key: str, obj: dict) -> None:
        self.hpas[key] = obj
        self.publish("hpa", obj)

    def remove_hpa(self, key: str) -> None:
        self.hpas.pop(key, None)
        self.publish("hpa_deleted", {"key": key})

    def upsert_network_policy(self, key: str, obj: dict) -> None:
        self.network_policies[key] = obj
        self.publish("network_policy", obj)

    def remove_network_policy(self, key: str) -> None:
        self.network_policies.pop(key, None)
        self.publish("network_policy_deleted", {"key": key})

    def upsert_secret(self, key: str, obj: dict) -> None:
        self.secrets[key] = obj
        self.publish("secret", obj)

    def remove_secret(self, key: str) -> None:
        self.secrets.pop(key, None)
        self.publish("secret_deleted", {"key": key})

    def upsert_configmap(self, key: str, obj: dict) -> None:
        self.configmaps[key] = obj
        self.publish("configmap", obj)

    def remove_configmap(self, key: str) -> None:
        self.configmaps.pop(key, None)
        self.publish("configmap_deleted", {"key": key})

    def upsert_orphaned_pv(self, key: str, obj: dict) -> None:
        self.orphaned_pvs[key] = obj
        self.publish("orphaned_pv", obj)

    def remove_orphaned_pv(self, key: str) -> None:
        self.orphaned_pvs.pop(key, None)
        self.publish("orphaned_pv_deleted", {"key": key})

    def add_event(self, obj: dict) -> None:
        self.events.append(obj)
        self.publish("event", obj)

    def record_node_sample(self, node: str, sample: dict) -> None:
        """Merge a metrics/hardware sample into the node time series."""
        hist = self.node_history.setdefault(node, deque(maxlen=HISTORY_LEN))
        merged = {**self.node_metrics.get(node, {}), **sample, "t": now()}
        self.node_metrics[node] = merged
        hist.append({"t": merged["t"], **{f: merged.get(f) for f in NODE_HISTORY_FIELDS}})
        self.publish("node_metrics", {"node": node, **merged})

    def record_pod_sample(self, key: str, sample: dict) -> None:
        """Latest CPU/RAM usage sample for one pod (ns/name key)."""
        self.pod_metrics[key] = {**sample, "t": now()}
        self.publish("pod_metrics", {"key": key, **self.pod_metrics[key]})

    def record_hardware(self, node: str, data: dict) -> None:
        self.hardware[node] = {**data, "t": now()}
        # Forward the whole sample (not a whitelist): record_node_sample publishes it as the
        # live "node_metrics" WebSocket delta too, and a client's `hardware` dict is built by
        # merging that same delta in (see store.ts) -- filtering fields out here would silently
        # freeze them on connected clients after the first full_state snapshot. The ring-buffer
        # history itself still only picks out a few chartable fields regardless of what's here.
        self.record_node_sample(node, data)

    def set_flux_kustomizations(self, items: dict[str, dict]) -> None:
        """Full replace on every poll -- the live set is small (a handful of
        Kustomizations at most), so this is simpler than incremental add/remove
        tracking and self-heals deletions without extra bookkeeping."""
        self.flux_kustomizations = items
        self.publish("flux_kustomizations", items)

    def set_flux_git_repositories(self, items: dict[str, dict]) -> None:
        self.flux_git_repositories = items
        self.publish("flux_git_repositories", items)

    def set_flux_image_policies(self, items: dict[str, dict]) -> None:
        self.flux_image_policies = items
        self.publish("flux_image_policies", items)

    def set_flux_image_automations(self, items: dict[str, dict]) -> None:
        self.flux_image_automations = items
        self.publish("flux_image_automations", items)

    def set_pvcs(self, items: dict[str, dict]) -> None:
        """Full replace on every poll, same reasoning as set_flux_kustomizations."""
        self.pvcs = items
        self.publish("pvcs", items)

    def set_gateways(self, items: dict[str, dict]) -> None:
        self.gateways = items
        self.publish("gateways", items)

    def set_http_routes(self, items: dict[str, dict]) -> None:
        self.http_routes = items
        self.publish("http_routes", items)

    def set_rate_limit_policies(self, items: dict[str, dict]) -> None:
        self.rate_limit_policies = items
        self.publish("rate_limit_policies", items)

    def record_check(self, name: str, config: dict, ok: bool, latency_ms: float | None, detail: str = "") -> None:
        entry = self.healthchecks.setdefault(
            name, {"config": config, "history": deque(maxlen=CHECK_HISTORY_LEN)}
        )
        entry["config"] = config
        result = {"t": now(), "ok": ok, "ms": latency_ms, "detail": detail}
        entry["last"] = result
        entry["history"].append(result)
        history = entry["history"]
        up = sum(1 for r in history if r["ok"])
        entry["uptime_pct"] = round(100.0 * up / len(history), 2)
        # config included every time, not just in the full snapshot: a check whose target was
        # only just discovered (autochecks.py) can have its first-ever result delivered as a
        # WS delta before any client has seen a full snapshot containing it, so the config
        # can't be assumed already known client-side.
        self.publish(
            "healthcheck", {"name": name, "config": config, **result, "uptime_pct": entry["uptime_pct"]}
        )

    def remove_check(self, name: str) -> None:
        """Only used by collectors/autochecks.py: an auto-discovered check (its target
        route/Service disappeared) needs to vanish from the UI, unlike YAML-configured
        checks (a fixed list for the process lifetime, nothing ever removes those)."""
        if self.healthchecks.pop(name, None) is not None:
            self.publish("healthcheck_deleted", {"name": name})

    def healthchecks_report_csv(self) -> str:
        """CSV summary of every healthcheck: uptime %, sample counts, first/last seen,
        latest result. Built here rather than in main.py's route handler so it's testable
        without spinning up the FastAPI app. Covers whatever history is currently held in
        memory (bounded to CHECK_HISTORY_LEN samples per check, or less for a check that's
        only just started) -- not a fixed calendar window."""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "name", "type", "target", "uptime_pct", "samples", "up", "down",
            "first_seen", "last_seen", "last_ok", "last_detail",
        ])
        for name, entry in sorted(self.healthchecks.items()):
            history = list(entry.get("history") or [])
            config = entry.get("config") or {}
            target = config.get("url") or (
                f"{config.get('host')}:{config.get('port')}" if config.get("host") else ""
            )
            up = sum(1 for r in history if r.get("ok"))
            last = entry.get("last") or {}
            writer.writerow([
                name,
                config.get("type", ""),
                target,
                entry.get("uptime_pct", ""),
                len(history),
                up,
                len(history) - up,
                _iso(history[0]["t"]) if history else "",
                _iso(history[-1]["t"]) if history else "",
                last.get("ok", ""),
                last.get("detail", ""),
            ])
        return buf.getvalue()

    # ---------------- snapshot ----------------

    def snapshot(self) -> dict:
        """Full state sent to a client right after it connects."""
        return {
            "demo_mode": self.demo_mode,
            "started_at": self.started_at,
            "nodes": self.nodes,
            "pods": self.pods,
            "deployments": self.deployments,
            "statefulsets": self.statefulsets,
            "daemonsets": self.daemonsets,
            "services": self.services,
            "hpas": self.hpas,
            "network_policies": self.network_policies,
            "secrets": self.secrets,
            "configmaps": self.configmaps,
            "orphaned_pvs": self.orphaned_pvs,
            "events": list(self.events),
            "node_metrics": self.node_metrics,
            "hardware": self.hardware,
            "pod_metrics": self.pod_metrics,
            "flux_kustomizations": self.flux_kustomizations,
            "flux_git_repositories": self.flux_git_repositories,
            "flux_image_policies": self.flux_image_policies,
            "flux_image_automations": self.flux_image_automations,
            "pvcs": self.pvcs,
            "gateways": self.gateways,
            "http_routes": self.http_routes,
            "rate_limit_policies": self.rate_limit_policies,
            "node_history": {k: list(v) for k, v in self.node_history.items()},
            "healthchecks": {
                name: {
                    "config": e.get("config"),
                    "last": e.get("last"),
                    "uptime_pct": e.get("uptime_pct"),
                    "history": list(e["history"]),
                }
                for name, e in self.healthchecks.items()
            },
        }


# Single instance shared across the app (one per replica).
state = ClusterState()

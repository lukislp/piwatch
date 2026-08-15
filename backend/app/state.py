"""In-memory cluster state with ring-buffer history and WebSocket pub/sub.

Each backend replica maintains its own full copy of the state by watching
the cluster independently. Nothing is persisted -- on failover the client
simply reconnects to the surviving replica and receives its full state.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

# ~3h of history at one sample every 10s
HISTORY_LEN = 1080
EVENTS_LEN = 200
CHECK_HISTORY_LEN = 500


def now() -> float:
    return time.time()


class ClusterState:
    def __init__(self) -> None:
        self.started_at = now()
        self.demo_mode = False

        # Live objects, keyed by name / uid
        self.nodes: dict[str, dict[str, Any]] = {}
        self.pods: dict[str, dict[str, Any]] = {}         # key: ns/name
        self.deployments: dict[str, dict[str, Any]] = {}  # key: ns/name
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

        # Flux Kustomization sync status (ns/name -> mapped status dict). Optional --
        # stays empty on clusters that don't run Flux; see collectors/flux.py.
        self.flux_kustomizations: dict[str, dict[str, Any]] = {}

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

    def add_event(self, obj: dict) -> None:
        self.events.append(obj)
        self.publish("event", obj)

    def record_node_sample(self, node: str, sample: dict) -> None:
        """Merge a metrics/hardware sample into the node time series."""
        hist = self.node_history.setdefault(node, deque(maxlen=HISTORY_LEN))
        merged = {**self.node_metrics.get(node, {}), **sample, "t": now()}
        self.node_metrics[node] = merged
        hist.append(
            {
                "t": merged["t"],
                "cpu_pct": merged.get("cpu_pct"),
                "mem_pct": merged.get("mem_pct"),
                "temp_c": merged.get("temp_c"),
                "nvme_read_bytes_per_s": merged.get("nvme_read_bytes_per_s"),
                "nvme_write_bytes_per_s": merged.get("nvme_write_bytes_per_s"),
                "net_rx_bytes_per_s": merged.get("net_rx_bytes_per_s"),
                "net_tx_bytes_per_s": merged.get("net_tx_bytes_per_s"),
            }
        )
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
        self.publish("healthcheck", {"name": name, **result, "uptime_pct": entry["uptime_pct"]})

    # ---------------- snapshot ----------------

    def snapshot(self) -> dict:
        """Full state sent to a client right after it connects."""
        return {
            "demo_mode": self.demo_mode,
            "started_at": self.started_at,
            "nodes": self.nodes,
            "pods": self.pods,
            "deployments": self.deployments,
            "events": list(self.events),
            "node_metrics": self.node_metrics,
            "hardware": self.hardware,
            "pod_metrics": self.pod_metrics,
            "flux_kustomizations": self.flux_kustomizations,
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

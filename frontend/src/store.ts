/**
 * WebSocket store: connects to /ws, applies the initial full_state and all
 * subsequent delta messages to a local Snapshot, and reconnects with backoff.
 *
 * The auto-reconnect is the failover mechanism: if the node running the
 * current backend replica dies, the browser reconnects through the Service
 * to the surviving replica and receives a fresh full_state.
 */
import { useEffect, useRef, useState } from "react";
import type { ConnStatus, Snapshot } from "./types";

const TOKEN_KEY = "piwatch_token";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}
export function setToken(t: string) {
  localStorage.setItem(TOKEN_KEY, t);
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export async function login(password: string): Promise<boolean> {
  const resp = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!resp.ok) return false;
  const data = await resp.json();
  setToken(data.token ?? "");
  return true;
}

export async function authRequired(): Promise<boolean> {
  try {
    const resp = await fetch("/api/auth");
    const data = await resp.json();
    return !!data.auth;
  } catch {
    return false;
  }
}

const HISTORY_LEN = 1080;

function applyDelta(snap: Snapshot, msg: { type: string; t?: number; data?: any }): Snapshot {
  const d = msg.data;
  switch (msg.type) {
    case "node":
      return { ...snap, nodes: { ...snap.nodes, [d.name]: d } };
    case "node_deleted": {
      const nodes = { ...snap.nodes };
      delete nodes[d.name];
      return { ...snap, nodes };
    }
    case "pod":
      return { ...snap, pods: { ...snap.pods, [d.key]: d } };
    case "pod_deleted": {
      const pods = { ...snap.pods };
      delete pods[d.key];
      const pod_metrics = { ...snap.pod_metrics };
      delete pod_metrics[d.key];
      return { ...snap, pods, pod_metrics };
    }
    case "pod_metrics":
      return { ...snap, pod_metrics: { ...snap.pod_metrics, [d.key]: d } };
    case "deployment":
      return { ...snap, deployments: { ...snap.deployments, [d.key]: d } };
    case "deployment_deleted": {
      const deployments = { ...snap.deployments };
      delete deployments[d.key];
      return { ...snap, deployments };
    }
    case "statefulset":
      return { ...snap, statefulsets: { ...snap.statefulsets, [d.key]: d } };
    case "statefulset_deleted": {
      const statefulsets = { ...snap.statefulsets };
      delete statefulsets[d.key];
      return { ...snap, statefulsets };
    }
    case "daemonset":
      return { ...snap, daemonsets: { ...snap.daemonsets, [d.key]: d } };
    case "daemonset_deleted": {
      const daemonsets = { ...snap.daemonsets };
      delete daemonsets[d.key];
      return { ...snap, daemonsets };
    }
    case "service":
      return { ...snap, services: { ...snap.services, [d.key]: d } };
    case "service_deleted": {
      const services = { ...snap.services };
      delete services[d.key];
      return { ...snap, services };
    }
    case "orphaned_pv":
      return { ...snap, orphaned_pvs: { ...snap.orphaned_pvs, [d.key]: d } };
    case "orphaned_pv_deleted": {
      const orphaned_pvs = { ...snap.orphaned_pvs };
      delete orphaned_pvs[d.key];
      return { ...snap, orphaned_pvs };
    }
    case "event":
      return { ...snap, events: [...snap.events.slice(-199), d] };
    case "node_metrics": {
      const node = d.node as string;
      const hist = snap.node_history[node] ?? [];
      const point = {
        t: msg.t ?? Date.now() / 1000,
        cpu_pct: d.cpu_pct,
        mem_pct: d.mem_pct,
        temp_c: d.temp_c,
        nvme_read_bytes_per_s: d.nvme_read_bytes_per_s,
        nvme_write_bytes_per_s: d.nvme_write_bytes_per_s,
        net_rx_bytes_per_s: d.net_rx_bytes_per_s,
        net_tx_bytes_per_s: d.net_tx_bytes_per_s,
      };
      return {
        ...snap,
        node_metrics: { ...snap.node_metrics, [node]: { ...snap.node_metrics[node], ...d } },
        hardware: { ...snap.hardware, [node]: { ...snap.hardware[node], ...d } },
        node_history: { ...snap.node_history, [node]: [...hist.slice(-(HISTORY_LEN - 1)), point] },
      };
    }
    case "flux_kustomizations":
      return { ...snap, flux_kustomizations: d };
    case "flux_git_repositories":
      return { ...snap, flux_git_repositories: d };
    case "flux_image_policies":
      return { ...snap, flux_image_policies: d };
    case "flux_image_automations":
      return { ...snap, flux_image_automations: d };
    case "pvcs":
      return { ...snap, pvcs: d };
    case "gateways":
      return { ...snap, gateways: d };
    case "http_routes":
      return { ...snap, http_routes: d };
    case "healthcheck": {
      const name = d.name as string;
      const entry = snap.healthchecks[name] ?? { config: { name }, history: [] };
      const result = { t: d.t ?? msg.t, ok: d.ok, ms: d.ms, detail: d.detail };
      return {
        ...snap,
        healthchecks: {
          ...snap.healthchecks,
          [name]: {
            ...entry,
            last: result,
            uptime_pct: d.uptime_pct ?? entry.uptime_pct,
            history: [...entry.history.slice(-499), result],
          },
        },
      };
    }
    case "healthcheck_deleted": {
      const healthchecks = { ...snap.healthchecks };
      delete healthchecks[d.name];
      return { ...snap, healthchecks };
    }
    default:
      return snap;
  }
}

export function useClusterStore() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [status, setStatus] = useState<ConnStatus>("connecting");
  const [generation, setGeneration] = useState(0); // bump to force reconnect
  const backoff = useRef(1000);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    let timer: number | undefined;

    const connect = () => {
      setStatus("connecting");
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const token = encodeURIComponent(getToken());
      ws = new WebSocket(`${proto}://${location.host}/ws?token=${token}`);

      ws.onopen = () => {
        backoff.current = 1000;
        setStatus("open");
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        if (msg.type === "ping") return;
        if (msg.type === "full_state") setSnapshot(msg.data);
        else setSnapshot((s) => (s ? applyDelta(s, msg) : s));
      };
      ws.onclose = (ev) => {
        if (closed) return;
        if (ev.code === 4401) {
          setStatus("unauthorized");
          return; // no auto-retry until re-login
        }
        setStatus("closed");
        timer = window.setTimeout(connect, backoff.current);
        backoff.current = Math.min(backoff.current * 1.7, 10000);
      };
    };

    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, [generation]);

  return { snapshot, status, reconnect: () => setGeneration((g) => g + 1) };
}

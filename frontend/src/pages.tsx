import { useEffect, useRef, useState } from "react";
import { NodeChart, Dot, StatusBadge, Tile } from "./components";
import { getToken } from "./store";
import { STATUS, seriesColor, type Mode } from "./theme";
import type { Snapshot } from "./types";

const fmtAge = (t?: number) => {
  if (!t) return "–";
  const s = Date.now() / 1000 - t;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
};
const fmtUptime = (s?: number) => (s ? `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h` : "–");

// ---------------- Overview ----------------
export function Overview({ snap, mode }: { snap: Snapshot; mode: Mode }) {
  const nodes = Object.values(snap.nodes);
  const pods = Object.values(snap.pods);
  const deps = Object.values(snap.deployments);
  const checks = Object.values(snap.healthchecks);
  const nodesReady = nodes.filter((n) => n.ready).length;
  const podsRunning = pods.filter((p) => p.phase === "Running").length;
  const depsReady = deps.filter((d) => d.ready >= d.replicas).length;
  const checksUp = checks.filter((c) => c.last?.ok).length;
  const warnings = snap.events.filter((e) => e.type === "Warning").length;
  return (
    <>
      <div className="grid tiles">
        <Tile value={`${nodesReady}/${nodes.length}`} label="Nodes ready" tone={nodesReady === nodes.length ? "good" : "critical"} />
        <Tile value={`${podsRunning}/${pods.length}`} label="Pods running" tone={podsRunning === pods.length ? "good" : "warning"} />
        <Tile value={`${depsReady}/${deps.length}`} label="Deployments ready" tone={depsReady === deps.length ? "good" : "warning"} />
        {checks.length > 0 && <Tile value={`${checksUp}/${checks.length}`} label="Healthchecks OK" tone={checksUp === checks.length ? "good" : "critical"} />}
        <Tile value={String(warnings)} label="Warning events" tone={warnings === 0 ? "good" : "warning"} />
      </div>
      <div className="grid cards">
        {nodes.sort((a, b) => a.name.localeCompare(b.name)).map((n) => {
          const m = snap.node_metrics[n.name] ?? {};
          return (
            <div className="card" key={n.name}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <strong><Dot color={seriesColor(n.name, mode)} />{n.name}</strong>
                <StatusBadge ok={n.ready} okText="Ready" badText="NotReady" />
              </div>
              <table style={{ marginTop: 8 }}>
                <tbody>
                  <tr><td className="muted">Role / IP</td><td className="num">{n.roles.join(", ")} · {n.internal_ip ?? "–"}</td></tr>
                  <tr><td className="muted">CPU / RAM</td><td className="num">{m.cpu_pct?.toFixed(0) ?? "–"} % / {m.mem_pct?.toFixed(0) ?? "–"} %</td></tr>
                  <tr><td className="muted">Temperature</td><td className="num">{m.temp_c != null ? `${m.temp_c.toFixed(1)} °C` : "–"}</td></tr>
                  <tr><td className="muted">Disk / Uptime</td><td className="num">{m.disk_used_pct != null ? `${m.disk_used_pct.toFixed(0)} %` : "–"} / {fmtUptime(m.uptime_s)}</td></tr>
                </tbody>
              </table>
            </div>
          );
        })}
      </div>
    </>
  );
}

// ---------------- Nodes (charts) ----------------
export function Nodes({ snap, mode }: { snap: Snapshot; mode: Mode }) {
  return (
    <>
      <NodeChart histories={snap.node_history} field="cpu_pct" mode={mode} unit="%" domain={[0, 100]} title="CPU usage" />
      <NodeChart histories={snap.node_history} field="mem_pct" mode={mode} unit="%" domain={[0, 100]} title="RAM usage" />
      <NodeChart histories={snap.node_history} field="temp_c" mode={mode} unit="°C" domain={[30, 90]} title="CPU temperature" />
    </>
  );
}

// ---------------- Workloads ----------------
export function Workloads({ snap, mode }: { snap: Snapshot; mode: Mode }) {
  const deps = Object.values(snap.deployments).sort((a, b) => a.key.localeCompare(b.key));
  const pods = Object.values(snap.pods).sort((a, b) => a.key.localeCompare(b.key));
  return (
    <>
      <div className="card">
        <h2>Deployments</h2>
        <table>
          <thead><tr><th>Name</th><th>Namespace</th><th className="num">Ready</th><th>Image</th></tr></thead>
          <tbody>
            {deps.map((d) => (
              <tr key={d.key}>
                <td><Dot color={d.ready >= d.replicas ? STATUS.good : STATUS.warning} />{d.name}</td>
                <td className="muted">{d.namespace}</td>
                <td className="num">{d.ready}/{d.replicas}</td>
                <td className="mono muted">{d.images.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card">
        <h2>Pods</h2>
        <table>
          <thead><tr><th>Pod</th><th>Namespace</th><th>Node</th><th>Status</th><th className="num">Ready</th><th className="num">Restarts</th><th className="num">Age</th></tr></thead>
          <tbody>
            {pods.map((p) => {
              const ok = p.phase === "Running" || p.phase === "Succeeded";
              return (
                <tr key={p.key}>
                  <td>{p.name}</td>
                  <td className="muted">{p.namespace}</td>
                  <td>{p.node && <><Dot color={seriesColor(p.node, mode)} />{p.node}</>}</td>
                  <td><Dot color={ok ? STATUS.good : STATUS.critical} />{p.reason ?? p.phase}</td>
                  <td className="num">{p.ready}</td>
                  <td className="num" style={p.restarts > 3 ? { color: STATUS.warning } : undefined}>{p.restarts}</td>
                  <td className="num muted">{fmtAge(p.created)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

// ---------------- Healthchecks ----------------
export function Healthchecks({ snap }: { snap: Snapshot }) {
  const checks = Object.entries(snap.healthchecks).sort(([a], [b]) => a.localeCompare(b));
  if (checks.length === 0) return <div className="card muted">No healthchecks configured (ConfigMap piwatch-healthchecks).</div>;
  return (
    <div className="grid cards">
      {checks.map(([name, c]) => (
        <div className="card" key={name}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong>{name}</strong>
            <StatusBadge ok={!!c.last?.ok} okText="Up" badText="Down" />
          </div>
          <div className="muted mono" style={{ marginTop: 4 }}>{c.config?.url ?? `${c.config?.host}:${c.config?.port}`}</div>
          <div className="row" style={{ marginTop: 8, justifyContent: "space-between" }}>
            <span className="muted">Uptime: <strong style={{ color: "var(--ink)" }}>{c.uptime_pct?.toFixed(1) ?? "–"} %</strong></span>
            <span className="muted">Latency: {c.last?.ms != null ? `${c.last.ms} ms` : "–"}</span>
          </div>
          <div className="uptime-strip" title="History (oldest → newest check)">
            {c.history.slice(-60).map((r, i) => (
              <i key={i} style={{ background: r.ok ? STATUS.good : STATUS.critical, opacity: r.ok ? 0.75 : 1 }} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------- Logs ----------------
export function Logs({ snap }: { snap: Snapshot }) {
  const pods = Object.values(snap.pods).sort((a, b) => a.key.localeCompare(b.key));
  const [selected, setSelected] = useState("");
  const [lines, setLines] = useState<string[]>([]);
  const boxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setLines([]);
    if (!selected) return;
    const [ns, pod] = selected.split("/");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/logs/${ns}/${pod}?token=${encodeURIComponent(getToken())}`);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "log") setLines((l) => [...l.slice(-800), msg.line]);
      if (msg.type === "log_error") setLines((l) => [...l, `⚠ ${msg.error}`]);
    };
    return () => ws.close();
  }, [selected]);
  useEffect(() => {
    boxRef.current?.scrollTo({ top: boxRef.current.scrollHeight });
  }, [lines]);
  return (
    <>
      <div className="row" style={{ marginBottom: 12 }}>
        <label className="muted">Pod:</label>
        <select value={selected} onChange={(e) => setSelected(e.target.value)}>
          <option value="">– select –</option>
          {pods.map((p) => <option key={p.key} value={p.key}>{p.key}</option>)}
        </select>
      </div>
      <div className="logbox" ref={boxRef}>
        {lines.length ? lines.join("\n") : <span className="muted">Select a pod to stream live logs …</span>}
      </div>
    </>
  );
}

// ---------------- Events ----------------
export function Events({ snap }: { snap: Snapshot }) {
  const events = [...snap.events].reverse();
  if (!events.length) return <div className="card muted">No events.</div>;
  return (
    <div className="card">
      <h2>Cluster events</h2>
      <table>
        <thead><tr><th>Time</th><th>Type</th><th>Object</th><th>Reason</th><th>Message</th></tr></thead>
        <tbody>
          {events.map((e, i) => (
            <tr key={e.uid + i}>
              <td className="muted num">{e.t ? new Date(e.t * 1000).toLocaleTimeString("en-US") : "–"}</td>
              <td><Dot color={e.type === "Warning" ? STATUS.warning : STATUS.good} />{e.type}</td>
              <td className="mono">{e.object}</td>
              <td>{e.reason}</td>
              <td className="muted">{e.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

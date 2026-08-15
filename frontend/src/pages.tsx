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
          const hw = snap.hardware[n.name] ?? {};
          const nvmeError =
            !!hw.nvme_critical_warning || !!hw.nvme_media_errors || !!hw.nvme_num_err_log_entries;
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
                  {hw.undervoltage != null && (
                    <tr>
                      <td className="muted">Power</td>
                      <td className="num">
                        {hw.undervoltage ? <span style={{ color: STATUS.critical }}>⚠ Undervoltage</span> : "OK"}
                      </td>
                    </tr>
                  )}
                  {hw.nvme_temp_c != null && (
                    <tr>
                      <td className="muted">NVMe</td>
                      <td className="num">
                        {nvmeError ? (
                          <span style={{ color: STATUS.critical }}>⚠ Errors</span>
                        ) : (
                          <span style={{ color: STATUS.good }}>OK</span>
                        )}
                        {" "}(see NVMe tab)
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          );
        })}
      </div>
      <GitOpsStatus snap={snap} />
    </>
  );
}

// ---------------- GitOps (Flux Kustomization sync status) ----------------
// Hidden entirely when empty: most piwatch users don't run Flux, and an
// empty "GitOps" card would just be confusing clutter for them.
function GitOpsStatus({ snap }: { snap: Snapshot }) {
  const items = Object.values(snap.flux_kustomizations).sort((a, b) => a.name.localeCompare(b.name));
  if (items.length === 0) return null;
  return (
    <div className="card">
      <h2>GitOps (Flux)</h2>
      <table>
        <thead><tr><th>Kustomization</th><th>Namespace</th><th>Status</th><th>Revision</th></tr></thead>
        <tbody>
          {items.map((k) => (
            <tr key={k.key}>
              <td>{k.name}</td>
              <td className="muted">{k.namespace}</td>
              <td>
                <Dot color={k.ready ? STATUS.good : STATUS.critical} />
                {k.ready ? "Synced" : (k.reason ?? "Not synced")}
              </td>
              <td className="mono muted">{k.last_applied_revision ?? "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {items.some((k) => !k.ready && k.message) && (
        <div className="muted" style={{ marginTop: 8 }}>
          {items.filter((k) => !k.ready && k.message).map((k) => (
            <div key={k.key}>
              <strong>{k.name}:</strong> {k.message}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------- Nodes (charts) ----------------
export function Nodes({ snap, mode }: { snap: Snapshot; mode: Mode }) {
  const mbFormatter = (v: number) => `${v.toFixed(v < 10 ? 1 : 0)} MB/s`;
  return (
    <>
      <NodeChart histories={snap.node_history} field="cpu_pct" mode={mode} unit="%" domain={[0, 100]} title="CPU usage" />
      <NodeChart histories={snap.node_history} field="mem_pct" mode={mode} unit="%" domain={[0, 100]} title="RAM usage" />
      <NodeChart histories={snap.node_history} field="temp_c" mode={mode} unit="°C" domain={[30, 90]} title="CPU temperature" />
      <NodeChart
        histories={scaledHistory(snap.node_history, "net_rx_bytes_per_s", 1024 * 1024, "rx_mb")}
        field="rx_mb" mode={mode} unit=" MB/s" title="Network receive"
        axisWidth={70} yTickFormatter={mbFormatter}
      />
      <NodeChart
        histories={scaledHistory(snap.node_history, "net_tx_bytes_per_s", 1024 * 1024, "tx_mb")}
        field="tx_mb" mode={mode} unit=" MB/s" title="Network transmit"
        axisWidth={70} yTickFormatter={mbFormatter}
      />
    </>
  );
}

// ---------------- NVMe ----------------
function fmtBytes(b?: number): string {
  if (b == null) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = b;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

// NVMe "data units" are 512000-byte units per the NVMe spec (not 512-byte sectors).
const fmtDataUnits = (units?: number) => (units == null ? "–" : fmtBytes(units * 512_000));

function scaledHistory(
  history: Snapshot["node_history"],
  field: string,
  divisor: number,
  outKey: string
): Snapshot["node_history"] {
  const out: Snapshot["node_history"] = {};
  for (const [node, points] of Object.entries(history)) {
    out[node] = points.map((p) => ({ t: p.t, [outKey]: p[field] != null ? p[field]! / divisor : undefined }));
  }
  return out;
}

export function Nvme({ snap, mode }: { snap: Snapshot; mode: Mode }) {
  const nodes = Object.values(snap.nodes)
    .filter((n) => snap.hardware[n.name]?.nvme_temp_c != null || snap.hardware[n.name]?.nvme_model != null)
    .sort((a, b) => a.name.localeCompare(b.name));

  if (nodes.length === 0) {
    return <div className="card muted">No NVMe drive detected on any node.</div>;
  }

  return (
    <>
      <div className="grid cards">
        {nodes.map((n) => {
          const hw = snap.hardware[n.name] ?? {};
          const errorCount = (hw.nvme_media_errors ?? 0) + (hw.nvme_num_err_log_entries ?? 0);
          const hasError = !!hw.nvme_critical_warning || errorCount > 0;
          const spareLow =
            hw.nvme_avail_spare != null && hw.nvme_spare_thresh != null && hw.nvme_avail_spare <= hw.nvme_spare_thresh;
          return (
            <div className="card" key={n.name}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <strong><Dot color={seriesColor(n.name, mode)} />{n.name}</strong>
                <StatusBadge ok={!hasError && !spareLow} okText="OK" badText="Attention" />
              </div>
              <div className="muted" style={{ marginTop: 2 }}>
                {hw.nvme_model ?? "Unknown model"}
                {hw.nvme_capacity_bytes ? ` · ${fmtBytes(hw.nvme_capacity_bytes)}` : ""}
              </div>
              <table style={{ marginTop: 8 }}>
                <tbody>
                  <tr><td className="muted">Firmware / Serial</td><td className="num">{hw.nvme_firmware ?? "–"} / {hw.nvme_serial ?? "–"}</td></tr>
                  <tr><td className="muted">Temperature</td><td className="num">{hw.nvme_temp_c != null ? `${hw.nvme_temp_c.toFixed(1)} °C` : "–"}</td></tr>
                  <tr>
                    <td className="muted">Wear / Spare</td>
                    <td className="num">
                      {hw.nvme_percent_used != null ? `${hw.nvme_percent_used}% used` : "–"} ·{" "}
                      <span style={spareLow ? { color: STATUS.critical } : undefined}>
                        {hw.nvme_avail_spare != null ? `${hw.nvme_avail_spare}% spare` : "–"}
                      </span>
                    </td>
                  </tr>
                  <tr><td className="muted">Power-on / Cycles</td><td className="num">{hw.nvme_power_on_hours != null ? `${hw.nvme_power_on_hours}h` : "–"} / {hw.nvme_power_cycles ?? "–"}</td></tr>
                  <tr>
                    <td className="muted">Unsafe shutdowns</td>
                    <td className="num" style={(hw.nvme_unsafe_shutdowns ?? 0) > 0 ? { color: STATUS.warning } : undefined}>
                      {hw.nvme_unsafe_shutdowns ?? "–"}
                    </td>
                  </tr>
                  <tr><td className="muted">Data read / written</td><td className="num">{fmtDataUnits(hw.nvme_data_units_read)} / {fmtDataUnits(hw.nvme_data_units_written)}</td></tr>
                  <tr><td className="muted">Host read / write cmds</td><td className="num">{hw.nvme_host_read_commands?.toLocaleString() ?? "–"} / {hw.nvme_host_write_commands?.toLocaleString() ?? "–"}</td></tr>
                  <tr>
                    <td className="muted">Errors</td>
                    <td className="num" style={hasError ? { color: STATUS.critical } : undefined}>
                      {hasError
                        ? `${hw.nvme_media_errors ?? 0} media, ${hw.nvme_num_err_log_entries ?? 0} logged, warning flag ${hw.nvme_critical_warning ?? 0}`
                        : "None"}
                    </td>
                  </tr>
                  {(hw.nvme_warning_temp_time || hw.nvme_critical_comp_time) ? (
                    <tr>
                      <td className="muted">Thermal throttle time</td>
                      <td className="num">{hw.nvme_warning_temp_time ?? 0}s warn / {hw.nvme_critical_comp_time ?? 0}s critical</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          );
        })}
      </div>
      <NodeChart
        histories={scaledHistory(snap.node_history, "nvme_read_bytes_per_s", 1024 * 1024, "read_mb")}
        field="read_mb" mode={mode} unit=" MB/s" title="NVMe read throughput"
        axisWidth={70} yTickFormatter={(v) => `${v.toFixed(v < 10 ? 1 : 0)} MB/s`}
      />
      <NodeChart
        histories={scaledHistory(snap.node_history, "nvme_write_bytes_per_s", 1024 * 1024, "write_mb")}
        field="write_mb" mode={mode} unit=" MB/s" title="NVMe write throughput"
        axisWidth={70} yTickFormatter={(v) => `${v.toFixed(v < 10 ? 1 : 0)} MB/s`}
      />
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
          <thead><tr><th>Pod</th><th>Namespace</th><th>Node</th><th>Status</th><th className="num">Ready</th><th className="num">Restarts</th><th className="num">CPU</th><th className="num">RAM</th><th className="num">Age</th></tr></thead>
          <tbody>
            {pods.map((p) => {
              const ok = p.phase === "Running" || p.phase === "Succeeded";
              const m = snap.pod_metrics[p.key];
              return (
                <tr key={p.key}>
                  <td>{p.name}</td>
                  <td className="muted">{p.namespace}</td>
                  <td>{p.node && <><Dot color={seriesColor(p.node, mode)} />{p.node}</>}</td>
                  <td><Dot color={ok ? STATUS.good : STATUS.critical} />{p.reason ?? p.phase}</td>
                  <td className="num">{p.ready}</td>
                  <td className="num" style={p.restarts > 3 ? { color: STATUS.warning } : undefined}>{p.restarts}</td>
                  <td className="num muted">{m?.cpu_cores != null ? `${Math.round(m.cpu_cores * 1000)}m` : "–"}</td>
                  <td className="num muted">{m?.mem_bytes != null ? `${Math.round(m.mem_bytes / 1024 / 1024)}Mi` : "–"}</td>
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

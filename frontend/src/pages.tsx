import { useEffect, useRef, useState } from "react";
import { NodeChart, Dot, StatusBadge, Tile, useBalancedTileColumns } from "./components";
import { getToken } from "./store";
import { STATUS, seriesColor, type Mode } from "./theme";
import type { DeploymentInfo, HpaInfo, PodInfo, Snapshot } from "./types";

const fmtAge = (t?: number) => {
  if (!t) return "–";
  const s = Date.now() / 1000 - t;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
};
const fmtUptime = (s?: number) => (s ? `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h` : "–");

// Kubernetes resource quantity parsing, mirroring backend/app/collectors/metrics.py's
// parse_cpu/parse_mem -- cpu_capacity/mem_capacity arrive as raw quantity strings
// (e.g. "4", "8Gi") since they come straight from the node spec, not metrics-server.
function parseCpuQty(v?: string): number {
  if (!v) return 0;
  const s = v.trim();
  if (s.endsWith("n")) return parseFloat(s) / 1e9;
  if (s.endsWith("u")) return parseFloat(s) / 1e6;
  if (s.endsWith("m")) return parseFloat(s) / 1e3;
  return parseFloat(s) || 0;
}

const MEM_QTY_FACTORS: [string, number][] = [
  ["Ki", 1024], ["Mi", 1024 ** 2], ["Gi", 1024 ** 3], ["Ti", 1024 ** 4],
  ["K", 1e3], ["M", 1e6], ["G", 1e9], ["T", 1e12],
];

function parseMemQty(v?: string): number {
  if (!v) return 0;
  const s = v.trim();
  for (const [suffix, factor] of MEM_QTY_FACTORS) {
    if (s.endsWith(suffix)) return (parseFloat(s.slice(0, -suffix.length)) || 0) * factor;
  }
  return parseFloat(s) || 0;
}

function capacityTone(pct: number): "good" | "warning" | "critical" {
  if (pct >= 90) return "critical";
  if (pct >= 75) return "warning";
  return "good";
}

// Kubelet-reported node conditions where "True" is bad (unlike Ready, where "True" is
// good) -- an early-warning signal for node health beyond plain Ready/NotReady.
const PRESSURE_CONDITIONS = ["MemoryPressure", "DiskPressure", "PIDPressure"];

function activePressure(conditions: Record<string, string>): string[] {
  return PRESSURE_CONDITIONS.filter((c) => conditions[c] === "True");
}

// Both numbers share one unit (e.g. "11/24 GB") so the tile value stays short
// enough not to wrap in the fixed-width tiles grid.
function fmtBytesPair(used: number, total: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let u = used;
  let t = total;
  let i = 0;
  while (t >= 1024 && i < units.length - 1) {
    u /= 1024;
    t /= 1024;
    i++;
  }
  const fmt = (v: number) => (i > 0 && v % 1 !== 0 ? v.toFixed(1) : v.toFixed(0));
  return `${fmt(u)}/${fmt(t)} ${units[i]}`;
}

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

  const cpuCapacity = nodes.reduce((sum, n) => sum + parseCpuQty(n.cpu_capacity), 0);
  const memCapacity = nodes.reduce((sum, n) => sum + parseMemQty(n.mem_capacity), 0);
  const cpuUsed = nodes.reduce((sum, n) => sum + (snap.node_metrics[n.name]?.cpu_cores ?? 0), 0);
  const memUsed = nodes.reduce((sum, n) => sum + (snap.node_metrics[n.name]?.mem_bytes ?? 0), 0);
  const cpuPct = cpuCapacity > 0 ? (100 * cpuUsed) / cpuCapacity : 0;
  const memPct = memCapacity > 0 ? (100 * memUsed) / memCapacity : 0;

  const tiles = [
    <Tile key="nodes" value={`${nodesReady}/${nodes.length}`} label="Nodes ready" tone={nodesReady === nodes.length ? "good" : "critical"} />,
    <Tile key="pods" value={`${podsRunning}/${pods.length}`} label="Pods running" tone={podsRunning === pods.length ? "good" : "warning"} />,
    <Tile key="deps" value={`${depsReady}/${deps.length}`} label="Deployments ready" tone={depsReady === deps.length ? "good" : "warning"} />,
    checks.length > 0 && (
      <Tile key="checks" value={`${checksUp}/${checks.length}`} label="Healthchecks OK" tone={checksUp === checks.length ? "good" : "critical"} />
    ),
    <Tile key="warnings" value={String(warnings)} label="Warning events" tone={warnings === 0 ? "good" : "warning"} />,
    cpuCapacity > 0 && (
      <Tile key="cpu" value={`${cpuUsed.toFixed(1)}/${cpuCapacity.toFixed(0)}`} label={`CPU cores (${cpuPct.toFixed(0)}%)`} tone={capacityTone(cpuPct)} />
    ),
    memCapacity > 0 && (
      <Tile key="mem" value={fmtBytesPair(memUsed, memCapacity)} label={`RAM used (${memPct.toFixed(0)}%)`} tone={capacityTone(memPct)} />
    ),
  ].filter((t): t is JSX.Element => Boolean(t));

  const tilesGrid = useBalancedTileColumns(tiles.length);

  return (
    <>
      <div className="grid tiles" ref={tilesGrid.ref} style={tilesGrid.style}>
        {tiles}
      </div>
      <div className="grid cards">
        {nodes.sort((a, b) => a.name.localeCompare(b.name)).map((n) => {
          const m = snap.node_metrics[n.name] ?? {};
          const hw = snap.hardware[n.name] ?? {};
          const nvmeError =
            !!hw.nvme_critical_warning || !!hw.nvme_media_errors || !!hw.nvme_num_err_log_entries;
          const pressure = activePressure(n.conditions);
          return (
            <div className="card" key={n.name}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <strong><Dot color={seriesColor(n.name, mode)} />{n.name}</strong>
                <StatusBadge ok={n.ready} okText="Ready" badText="NotReady" />
              </div>
              <table style={{ marginTop: 8 }}>
                <tbody>
                  <tr><td className="muted">Role / IP</td><td className="num">{n.roles.join(", ")} · {n.internal_ip ?? "–"}</td></tr>
                  <tr>
                    <td className="muted">Schedulable</td>
                    <td
                      className="num"
                      title={n.taints.map((t) => `${t.key}${t.value ? `=${t.value}` : ""}:${t.effect}`).join(", ") || undefined}
                    >
                      {n.unschedulable || n.taints.length > 0 ? (
                        <span style={{ color: STATUS.warning }}>
                          ⚠ {n.unschedulable ? "Cordoned" : "Tainted"}
                          {n.taints.length > 0 && ` (${n.taints.length} taint${n.taints.length > 1 ? "s" : ""})`}
                        </span>
                      ) : (
                        <span style={{ color: STATUS.good }}>OK</span>
                      )}
                    </td>
                  </tr>
                  <tr><td className="muted">CPU / RAM</td><td className="num">{m.cpu_pct?.toFixed(0) ?? "–"} % / {m.mem_pct?.toFixed(0) ?? "–"} %</td></tr>
                  <tr><td className="muted">Temperature</td><td className="num">{m.temp_c != null ? `${m.temp_c.toFixed(1)} °C` : "–"}</td></tr>
                  <tr><td className="muted">Disk / Uptime</td><td className="num">{m.disk_used_pct != null ? `${m.disk_used_pct.toFixed(0)} %` : "–"} / {fmtUptime(m.uptime_s)}</td></tr>
                  <tr>
                    <td className="muted">Pressure</td>
                    <td className="num">
                      {pressure.length === 0 ? (
                        <span style={{ color: STATUS.good }}>OK</span>
                      ) : (
                        <span style={{ color: STATUS.critical }}>⚠ {pressure.join(", ")}</span>
                      )}
                    </td>
                  </tr>
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
      <ImageAutomationStatus snap={snap} />
      <GatewayStatus snap={snap} />
      <LoadBalancerStatus snap={snap} />
    </>
  );
}

// ---------------- GitOps (Flux Kustomization sync status) ----------------
// Hidden entirely when empty: most piwatch users don't run Flux, and an
// empty "GitOps" card would just be confusing clutter for them.
/** Ticks once a second so countdowns re-render without needing a fresh snapshot. */
function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

function fmtCountdown(remainingS: number): string {
  if (remainingS <= 0) return "due";
  const m = Math.floor(remainingS / 60);
  const s = Math.floor(remainingS % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function GitOpsStatus({ snap }: { snap: Snapshot }) {
  const now = useNow();
  const items = Object.values(snap.flux_kustomizations).sort((a, b) => a.name.localeCompare(b.name));
  if (items.length === 0) return null;
  const sourceFor = (k: (typeof items)[number]) => {
    if (k.source_kind !== "GitRepository" || !k.source_name) return undefined;
    const ns = k.source_namespace ?? k.namespace;
    return snap.flux_git_repositories[`${ns}/${k.source_name}`];
  };
  return (
    <div className="card">
      <h2>GitOps (Flux)</h2>
      <table>
        <thead>
          <tr>
            <th>Kustomization</th>
            <th>Namespace</th>
            <th>Status</th>
            <th>Source</th>
            <th>Revision</th>
            <th className="num">Resources</th>
            <th className="num">Next sync</th>
          </tr>
        </thead>
        <tbody>
          {items.map((k) => {
            const src = sourceFor(k);
            return (
              <tr key={k.key}>
                <td>{k.name}</td>
                <td className="muted">{k.namespace}</td>
                <td>
                  <Dot color={k.ready ? STATUS.good : STATUS.critical} />
                  {k.ready ? "Synced" : (k.reason ?? "Not synced")}
                  {k.apply_pending && (
                    <span style={{ color: STATUS.warning, marginLeft: 6 }} title="Apply attempt in progress or stuck">
                      ⚠ apply pending
                    </span>
                  )}
                </td>
                <td className="muted">
                  {src ? (
                    <>
                      <Dot color={src.ready ? STATUS.good : STATUS.critical} />
                      {k.source_name}
                    </>
                  ) : k.source_name ? (
                    `${k.source_kind ?? ""} ${k.source_name}`.trim()
                  ) : (
                    "–"
                  )}
                </td>
                <td className="mono muted">{k.last_applied_revision ?? "–"}</td>
                <td className="num muted">{k.managed_resource_count ?? "–"}</td>
                <td className="num muted">
                  {k.next_reconcile_t != null ? fmtCountdown(k.next_reconcile_t - now) : "–"}
                </td>
              </tr>
            );
          })}
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

// ---------------- GitOps (Flux image automation) ----------------
// Hidden entirely when empty: only relevant to clusters using Flux's
// image-reflector/image-automation controllers, not plain Flux users.
// Best-effort join, same spirit as rolloutDrift below: matches a policy's image repository
// (everything before the last ":") against each Deployment's own image refs, since PiWatch
// doesn't track which Deployment a Flux ImagePolicy is meant for -- there's no field for that,
// policies just scan a repository.
function installedTagsFor(image: string | null | undefined, deployments: DeploymentInfo[]): string[] {
  if (!image) return [];
  const tags = new Set<string>();
  for (const d of deployments) {
    for (const img of d.images) {
      const idx = img.lastIndexOf(":");
      if (idx <= 0) continue;
      if (img.slice(0, idx) === image) tags.add(img.slice(idx + 1));
    }
  }
  return [...tags];
}

function ImageAutomationStatus({ snap }: { snap: Snapshot }) {
  const policies = Object.values(snap.flux_image_policies).sort((a, b) => a.name.localeCompare(b.name));
  const automations = Object.values(snap.flux_image_automations).sort((a, b) => a.name.localeCompare(b.name));
  const deployments = Object.values(snap.deployments);
  if (policies.length === 0 && automations.length === 0) return null;
  return (
    <div className="card">
      <h2>Image Automation (Flux)</h2>
      {policies.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Policy</th>
              <th>Image</th>
              <th>Installed</th>
              <th>Latest tag</th>
              <th>Previous tag</th>
              <th className="num">Tags scanned</th>
              <th className="num">Last scan</th>
            </tr>
          </thead>
          <tbody>
            {policies.map((p) => {
              const installed = installedTagsFor(p.image, deployments);
              return (
                <tr key={p.key}>
                  <td>
                    <Dot color={p.ready ? STATUS.good : STATUS.critical} />
                    {p.name}
                  </td>
                  <td className="mono muted">{p.image ?? "–"}</td>
                  <td className="mono">
                    {installed.length === 0 ? (
                      <span className="muted" title="No Deployment running this image found">–</span>
                    ) : installed.length > 1 ? (
                      <span style={{ color: STATUS.warning }} title="Replicas running different image tags">
                        {installed.join(" / ")}
                      </span>
                    ) : (
                      <span
                        style={{ color: p.latest_tag && installed[0] !== p.latest_tag ? STATUS.warning : undefined }}
                        title={p.latest_tag && installed[0] !== p.latest_tag ? "Update available" : undefined}
                      >
                        {installed[0]}
                      </span>
                    )}
                  </td>
                  <td className="mono">{p.latest_tag ?? "–"}</td>
                  <td className="mono muted">{p.previous_tag ?? "–"}</td>
                  <td className="num muted">{p.tag_count ?? "–"}</td>
                  <td className="num muted">{fmtAge(p.last_scan_time ? Date.parse(p.last_scan_time) / 1000 : undefined)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {automations.length > 0 && (
        <table style={{ marginTop: policies.length > 0 ? 12 : 0 }}>
          <thead>
            <tr>
              <th>Automation</th>
              <th>Status</th>
              <th className="num">Last run</th>
              <th>Last push</th>
              <th className="num">Pushed</th>
            </tr>
          </thead>
          <tbody>
            {automations.map((a) => (
              <tr key={a.key}>
                <td>{a.name}</td>
                <td>
                  <Dot color={a.ready ? STATUS.good : STATUS.critical} />
                  {a.ready ? "OK" : (a.reason ?? "Not ready")}
                </td>
                <td className="num muted">{fmtAge(a.last_automation_run_time ? Date.parse(a.last_automation_run_time) / 1000 : undefined)}</td>
                <td className="mono muted">{a.last_push_commit ? a.last_push_commit.slice(0, 7) : "–"}</td>
                <td className="num muted">{fmtAge(a.last_push_time ? Date.parse(a.last_push_time) / 1000 : undefined)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ---------------- Gateway API routing status ----------------
// Hidden entirely when empty, same reasoning as GitOpsStatus: most piwatch users don't
// use the Gateway API.
function GatewayStatus({ snap }: { snap: Snapshot }) {
  const gateways = Object.values(snap.gateways).sort((a, b) => a.name.localeCompare(b.name));
  const routes = Object.values(snap.http_routes).sort((a, b) => a.key.localeCompare(b.key));
  if (gateways.length === 0 && routes.length === 0) return null;
  return (
    <div className="card">
      <h2>Gateway API</h2>
      {gateways.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Gateway</th>
              <th>Namespace</th>
              <th>Class</th>
              <th>Status</th>
              <th>Address</th>
              <th className="num">Listeners</th>
            </tr>
          </thead>
          <tbody>
            {gateways.map((g) => (
              <tr key={g.key}>
                <td>
                  <Dot color={g.ready ? STATUS.good : STATUS.critical} />
                  {g.name}
                </td>
                <td className="muted">{g.namespace}</td>
                <td className="muted">{g.gateway_class_name ?? "–"}</td>
                <td>
                  {g.ready ? "Programmed" : (g.reason ?? "Not programmed")}
                </td>
                <td className="mono muted">{g.addresses.join(", ") || "–"}</td>
                <td className="num muted">
                  {g.listeners_ready}/{g.listener_count} ({g.attached_routes} routes)
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {routes.length > 0 && (
        <table style={{ marginTop: gateways.length > 0 ? 12 : 0 }}>
          <thead>
            <tr>
              <th>Route</th>
              <th>Namespace</th>
              <th>Hostnames</th>
              <th>Gateway</th>
              <th>Backends</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {routes.map((r) => {
              const problem = !r.accepted || !r.resolved_refs;
              return (
                <tr key={r.key}>
                  <td>{r.name}</td>
                  <td className="muted">{r.namespace}</td>
                  <td className="mono muted">{r.hostnames.join(", ") || "–"}</td>
                  <td className="muted">{r.parent_names.join(", ") || "–"}</td>
                  <td className="mono muted">{r.backend_names.join(", ") || "–"}</td>
                  <td>
                    <Dot color={!problem ? STATUS.good : STATUS.critical} />
                    {!problem ? (
                      "OK"
                    ) : (
                      <span title={r.message ?? undefined}>
                        {!r.resolved_refs ? "⚠ backend not resolved" : (r.reason ?? "⚠ not accepted")}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ---------------- LoadBalancer Services ----------------
// Hidden entirely when empty, same reasoning as GatewayStatus: not every cluster
// runs a LoadBalancer controller (e.g. MetalLB).
function LoadBalancerStatus({ snap }: { snap: Snapshot }) {
  const services = Object.values(snap.services).sort((a, b) => a.key.localeCompare(b.key));
  if (services.length === 0) return null;
  return (
    <div className="card">
      <h2>LoadBalancer Services</h2>
      <table>
        <thead>
          <tr>
            <th>Service</th>
            <th>Namespace</th>
            <th>Cluster IP</th>
            <th>External IP</th>
            <th>Ports</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {services.map((s) => {
            const pending = s.external_ips.length === 0;
            return (
              <tr key={s.key}>
                <td>{s.name}</td>
                <td className="muted">{s.namespace}</td>
                <td className="mono muted">{s.cluster_ip ?? "–"}</td>
                <td className="mono muted">{s.external_ips.join(", ") || "–"}</td>
                <td className="mono muted">
                  {s.ports.map((p) => `${p.port}/${p.protocol}`).join(", ") || "–"}
                </td>
                <td>
                  <Dot color={pending ? STATUS.warning : STATUS.good} />
                  {pending ? "⚠ Pending" : "OK"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------- Autoscalers (HPA) ----------------
// Hidden entirely when empty, same reasoning as Storage/OrphanedPvs: not every cluster
// uses autoscaling.
// Pairs each metric's current and target reading by name (they arrive as two separate
// lists from the backend -- current_metrics/metrics -- since that's the shape of the
// underlying HPA status/spec objects).
function fmtHpaMetrics(h: HpaInfo): string {
  return h.metrics
    .map((target) => {
      const current = h.current_metrics.find((c) => c.name === target.name);
      const cur = current?.current_pct != null ? `${current.current_pct}%` : "–";
      return `${target.name} ${cur}/${target.target_pct}%`;
    })
    .join(", ") || "–";
}

function Autoscalers({ snap }: { snap: Snapshot }) {
  const hpas = Object.values(snap.hpas).sort((a, b) => a.key.localeCompare(b.key));
  if (hpas.length === 0) return null;
  return (
    <div className="card">
      <h2>Autoscalers</h2>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Namespace</th>
            <th>Target</th>
            <th className="num">Replicas</th>
            <th>Current / Target</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {hpas.map((h) => {
            const limited = h.scaling_limited === "True";
            const notActive = h.scaling_active === "False" || h.able_to_scale === "False";
            const problem = notActive || limited;
            return (
              <tr key={h.key}>
                <td>{h.name}</td>
                <td className="muted">{h.namespace}</td>
                <td className="mono muted">{h.target_kind ? `${h.target_kind}/${h.target_name}` : "–"}</td>
                <td className="num">
                  {h.current_replicas}/{h.min_replicas ?? "–"}-{h.max_replicas ?? "–"}
                </td>
                <td className="mono muted">{fmtHpaMetrics(h)}</td>
                <td>
                  <Dot color={!problem ? STATUS.good : notActive ? STATUS.critical : STATUS.warning} />
                  {!problem ? "OK" : notActive ? "⚠ not scaling" : "⚠ limited"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
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
// Best-effort: PiWatch doesn't watch ReplicaSets/ownerReferences, so pod-to-Deployment
// ownership is inferred from the standard Deployment->ReplicaSet->Pod naming convention:
// "<deployment-name>-<template-hash>-<random-suffix>", i.e. exactly two more hyphen-
// separated segments after the deployment-name prefix. A plain startsWith(dep.name + "-")
// isn't enough -- it also matches OTHER deployments in the same namespace whose name
// happens to start with this one's (e.g. "cert-manager" wrongly absorbing
// "cert-manager-webhook-<hash>-<suffix>" and "cert-manager-cainjector-..." pods, which
// run different images and used to trigger a false "rollout in progress").
function rolloutDrift(dep: DeploymentInfo, pods: PodInfo[]): string | null {
  if (dep.updated < dep.replicas) {
    return `${dep.updated}/${dep.replicas} replicas updated to the current revision`;
  }
  const prefix = `${dep.name}-`;
  const owned = pods.filter((p) => {
    if (p.namespace !== dep.namespace || !p.name.startsWith(prefix)) return false;
    return p.name.slice(prefix.length).split("-").length === 2;
  });
  const images = new Set(owned.flatMap((p) => p.images ?? []));
  if (images.size > 1) {
    return `replicas running different image tags: ${[...images].join(", ")}`;
  }
  return null;
}

function pvcUsageColor(pct: number): string {
  if (pct >= 90) return STATUS.critical;
  if (pct >= 75) return STATUS.warning;
  return STATUS.good;
}

// Hidden entirely when empty, same reasoning as GitOpsStatus: most clusters
// this dashboard runs against don't necessarily use PVCs (e.g. hostPath-only
// setups), so an empty "Storage" card would just be clutter.
function Storage({ snap }: { snap: Snapshot }) {
  const pvcs = Object.values(snap.pvcs).sort((a, b) => a.key.localeCompare(b.key));
  if (pvcs.length === 0) return null;
  return (
    <div className="card">
      <h2>Storage (PVCs)</h2>
      <table>
        <thead>
          <tr>
            <th>Claim</th>
            <th>Namespace</th>
            <th>Status</th>
            <th>Storage class</th>
            <th className="num">Capacity</th>
            <th className="num">Usage</th>
          </tr>
        </thead>
        <tbody>
          {pvcs.map((p) => (
            <tr key={p.key}>
              <td>
                <Dot color={p.phase === "Bound" ? STATUS.good : STATUS.warning} />
                {p.name}
              </td>
              <td className="muted">{p.namespace}</td>
              <td className="muted">{p.phase ?? "–"}</td>
              <td className="muted">{p.storage_class ?? "–"}</td>
              <td className="num muted">{fmtBytes(p.capacity_bytes ?? p.requested_bytes ?? undefined)}</td>
              <td
                className="num"
                title={
                  p.usage_pct == null
                    ? "Not available -- needs PIWATCH_PROMETHEUS_URL, and a storage class that reports real per-volume usage (some, like local-path-provisioner, don't)"
                    : undefined
                }
              >
                {p.usage_pct != null ? (
                  <span style={{ color: pvcUsageColor(p.usage_pct) }}>
                    {p.usage_pct.toFixed(0)}% ({fmtBytes(p.usage_bytes ?? undefined)})
                  </span>
                ) : (
                  <span className="muted">–</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Hidden entirely when empty (the common case, and the point of the feature -- a
// PersistentVolume in a healthy Bound/Available phase is never shown here at all,
// only ones stuck Released/Failed after their PVC is long gone).
function OrphanedPvs({ snap }: { snap: Snapshot }) {
  const pvs = Object.values(snap.orphaned_pvs).sort((a, b) => a.key.localeCompare(b.key));
  if (pvs.length === 0) return null;
  return (
    <div className="card">
      <h2>Orphaned PersistentVolumes</h2>
      <table>
        <thead>
          <tr>
            <th>Volume</th>
            <th>Phase</th>
            <th className="num">Capacity</th>
            <th>Storage class</th>
            <th>Reclaim policy</th>
            <th>Last claim</th>
          </tr>
        </thead>
        <tbody>
          {pvs.map((v) => (
            <tr key={v.key}>
              <td>
                <Dot color={STATUS.warning} />
                {v.name}
              </td>
              <td className="muted">{v.phase ?? "–"}</td>
              <td className="num muted">{v.capacity ?? "–"}</td>
              <td className="muted">{v.storage_class ?? "–"}</td>
              <td className="muted">{v.reclaim_policy ?? "–"}</td>
              <td className="mono muted">
                {v.claim_namespace && v.claim_name ? `${v.claim_namespace}/${v.claim_name}` : "–"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Hidden entirely when empty, same reasoning as Storage/GitOpsStatus: not every cluster
// runs StatefulSets (they're common for stateful apps like databases/caches, but far from
// universal in a small homelab-style deployment).
function StatefulSets({ snap }: { snap: Snapshot }) {
  const sets = Object.values(snap.statefulsets).sort((a, b) => a.key.localeCompare(b.key));
  if (sets.length === 0) return null;
  return (
    <div className="card">
      <h2>StatefulSets</h2>
      <table>
        <thead><tr><th>Name</th><th>Namespace</th><th className="num">Ready</th><th>Image</th></tr></thead>
        <tbody>
          {sets.map((s) => (
            <tr key={s.key}>
              <td><Dot color={s.ready >= s.replicas ? STATUS.good : STATUS.warning} />{s.name}</td>
              <td className="muted">{s.namespace}</td>
              <td className="num">{s.ready}/{s.replicas}</td>
              <td className="mono muted">
                {s.images.join(", ")}
                {s.updated < s.replicas && (
                  <span style={{ color: STATUS.warning }} title={`${s.updated}/${s.replicas} replicas updated to the current revision`}>
                    {" "}⚠ rollout in progress
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DaemonSets({ snap }: { snap: Snapshot }) {
  const sets = Object.values(snap.daemonsets).sort((a, b) => a.key.localeCompare(b.key));
  if (sets.length === 0) return null;
  return (
    <div className="card">
      <h2>DaemonSets</h2>
      <table>
        <thead><tr><th>Name</th><th>Namespace</th><th className="num">Ready</th><th>Image</th></tr></thead>
        <tbody>
          {sets.map((s) => (
            <tr key={s.key}>
              <td><Dot color={s.ready >= s.desired ? STATUS.good : STATUS.warning} />{s.name}</td>
              <td className="muted">{s.namespace}</td>
              <td className="num">{s.ready}/{s.desired}</td>
              <td className="mono muted">
                {s.images.join(", ")}
                {s.updated < s.desired && (
                  <span style={{ color: STATUS.warning }} title={`${s.updated}/${s.desired} scheduled to the current revision`}>
                    {" "}⚠ rollout in progress
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

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
            {deps.map((d) => {
              const drift = rolloutDrift(d, pods);
              return (
                <tr key={d.key}>
                  <td><Dot color={d.ready >= d.replicas ? STATUS.good : STATUS.warning} />{d.name}</td>
                  <td className="muted">{d.namespace}</td>
                  <td className="num">{d.ready}/{d.replicas}</td>
                  <td className="mono muted">
                    {d.images.join(", ")}
                    {drift && (
                      <span style={{ color: STATUS.warning }} title={drift}> ⚠ rollout in progress</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <StatefulSets snap={snap} />
      <DaemonSets snap={snap} />
      <Autoscalers snap={snap} />
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
                  <td>
                    <Dot color={ok ? STATUS.good : STATUS.critical} />{p.reason ?? p.phase}
                    {p.oom_killed && (
                      <span style={{ color: STATUS.critical }} title="A container in this pod was OOMKilled"> ⚠ OOM</span>
                    )}
                  </td>
                  <td className="num">{p.ready}</td>
                  <td
                    className="num"
                    style={p.restarts > 3 ? { color: STATUS.warning } : undefined}
                    title={
                      p.restarts > 0 && p.last_exit_reason
                        ? `Last: ${p.last_exit_reason}${p.last_exit_code != null ? ` (exit ${p.last_exit_code})` : ""}`
                        : undefined
                    }
                  >
                    {p.restarts}
                  </td>
                  <td className="num muted">{m?.cpu_cores != null ? `${Math.round(m.cpu_cores * 1000)}m` : "–"}</td>
                  <td className="num muted">{m?.mem_bytes != null ? `${Math.round(m.mem_bytes / 1024 / 1024)}Mi` : "–"}</td>
                  <td className="num muted">{fmtAge(p.created)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <Storage snap={snap} />
      <OrphanedPvs snap={snap} />
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

import { useEffect, useRef, useState } from "react";
import { Line, LineChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend } from "recharts";
import { CHROME, STATUS, seriesColor, type Mode } from "./theme";

// Must match .grid.tiles's gap and Tile's min width in styles.css.
const TILE_MIN_WIDTH = 160;
const TILE_GAP = 14;

// Balances tile count across rows instead of leaving a lone remainder tile alone in the
// last row (e.g. 7 tiles at 6-per-row -> 4+3 instead of 6+1): first finds how many tiles
// fit per row at the container's current width (what CSS auto-fill would do on its own),
// then spreads the tiles evenly across however many rows that implies.
export function useBalancedTileColumns(count: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [columns, setColumns] = useState(count || 1);

  useEffect(() => {
    const el = ref.current;
    if (!el || count === 0) return;
    const update = () => {
      const maxCols = Math.max(1, Math.floor((el.clientWidth + TILE_GAP) / (TILE_MIN_WIDTH + TILE_GAP)));
      const rows = Math.ceil(count / Math.min(count, maxCols));
      setColumns(Math.ceil(count / rows));
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [count]);

  return { ref, style: { gridTemplateColumns: `repeat(${columns}, minmax(${TILE_MIN_WIDTH}px, 1fr))` } };
}

export function Dot({ color }: { color: string }) {
  return <span className="dot" style={{ background: color }} />;
}

export function StatusBadge({ ok, okText, badText }: { ok: boolean; okText: string; badText: string }) {
  return (
    <span className="badge">
      <Dot color={ok ? STATUS.good : STATUS.critical} />
      {ok ? `✓ ${okText}` : `✕ ${badText}`}
    </span>
  );
}

export function Tile({ value, label, tone }: { value: string; label: string; tone?: "good" | "critical" | "warning" }) {
  const color = tone ? STATUS[tone] : undefined;
  return (
    <div className="card tile">
      <div className="v" style={color ? { color } : undefined}>{value}</div>
      <div className="l">{label}</div>
    </div>
  );
}

function fmtTime(t: number) {
  return new Date(t * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}

/** Multi-node line chart over the shared history (one series per node). */
export function NodeChart({
  histories, field, mode, unit, domain, title, axisWidth, yTickFormatter,
}: {
  histories: Record<string, { t: number; [k: string]: number | undefined }[]>;
  field: string; mode: Mode; unit: string; domain?: [number, number]; title: string;
  /** Wider axis reservation for longer tick labels (default 54px fits "100 %"/"90 °C"
   * but clips e.g. "12.5 MB/s"). */
  axisWidth?: number;
  /** Custom Y-axis tick label formatting -- default auto-ticks can land on ugly
   * fractions (e.g. "1.05") when the domain isn't a fixed round range. */
  yTickFormatter?: (v: number) => string;
}) {
  const chrome = CHROME[mode];
  const nodes = Object.keys(histories).sort();
  // merge on timestamp buckets (10s)
  const byBucket = new Map<number, any>();
  for (const n of nodes) {
    for (const p of histories[n]) {
      if (p[field] == null) continue;
      const b = Math.round(p.t / 10) * 10;
      const row = byBucket.get(b) ?? { t: b };
      row[n] = p[field];
      byBucket.set(b, row);
    }
  }
  const data = [...byBucket.values()].sort((a, b) => a.t - b.t).slice(-360);
  return (
    <div className="card">
      <h2>{title}</h2>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -14 }}>
          <CartesianGrid stroke={chrome.grid} strokeWidth={1} vertical={false} />
          <XAxis dataKey="t" tickFormatter={fmtTime} stroke={chrome.axis} tick={{ fill: chrome.muted, fontSize: 11 }} tickLine={false} minTickGap={60} />
          <YAxis
            domain={domain ?? [0, "auto"]}
            stroke={chrome.axis}
            tick={{ fill: chrome.muted, fontSize: 11 }}
            tickLine={false}
            unit={yTickFormatter ? undefined : unit}
            tickFormatter={yTickFormatter}
            width={axisWidth ?? 54}
          />
          <Tooltip
            contentStyle={{ background: chrome.surface, border: `1px solid ${chrome.border}`, borderRadius: 8, color: chrome.inkPrimary, fontSize: 12 }}
            labelFormatter={(t) => fmtTime(Number(t))}
            formatter={(v: any, name: any) => [`${Number(v).toFixed(1)}${unit}`, name]}
          />
          <Legend wrapperStyle={{ fontSize: 12, color: chrome.inkSecondary }} iconType="plainline" />
          {nodes.map((n) => (
            <Line key={n} type="monotone" dataKey={n} stroke={seriesColor(n, mode)} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

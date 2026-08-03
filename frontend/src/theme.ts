/**
 * Theme + chart palette (validated reference palette from the dataviz method).
 * Series colors follow the entity (node name), never the rank: each node name
 * is hashed to a stable slot so filtering never repaints survivors.
 */
import { useEffect, useState } from "react";

export type Mode = "light" | "dark";

export const CHROME = {
  light: {
    surface: "#fcfcfb",
    page: "#f9f9f7",
    inkPrimary: "#0b0b0b",
    inkSecondary: "#52514e",
    muted: "#898781",
    grid: "#e1e0d9",
    axis: "#c3c2b7",
    border: "rgba(11,11,11,0.10)",
  },
  dark: {
    surface: "#1a1a19",
    page: "#0d0d0d",
    inkPrimary: "#ffffff",
    inkSecondary: "#c3c2b7",
    muted: "#898781",
    grid: "#2c2c2a",
    axis: "#383835",
    border: "rgba(255,255,255,0.10)",
  },
};

// Categorical slots (fixed order — the ordering is the CVD-safety mechanism)
export const SERIES = {
  light: ["#2a78d6", "#008300", "#e87ba4", "#eda100", "#1baf7a", "#eb6834", "#4a3aa7", "#e34948"],
  dark: ["#3987e5", "#008300", "#d55181", "#c98500", "#199e70", "#d95926", "#9085e9", "#e66767"],
};

// Status palette (reserved; always paired with icon + label, never color alone)
export const STATUS = {
  good: "#0ca30c",
  warning: "#fab219",
  serious: "#ec835a",
  critical: "#d03b3b",
};

const seriesAssignments = new Map<string, number>();

/** Stable slot per entity name (first come, first slot; never re-assigned). */
export function seriesColor(name: string, mode: Mode): string {
  if (!seriesAssignments.has(name)) {
    seriesAssignments.set(name, seriesAssignments.size % SERIES.light.length);
  }
  return SERIES[mode][seriesAssignments.get(name)!];
}

const THEME_KEY = "piwatch_theme";

export function useTheme(): [Mode, () => void] {
  const [mode, setMode] = useState<Mode>(() => {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "light" || saved === "dark") return saved;
    return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
  });

  useEffect(() => {
    document.documentElement.dataset.theme = mode;
    localStorage.setItem(THEME_KEY, mode);
  }, [mode]);

  return [mode, () => setMode((m) => (m === "dark" ? "light" : "dark"))];
}

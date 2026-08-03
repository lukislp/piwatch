export interface NodeInfo {
  name: string;
  ready: boolean;
  conditions: Record<string, string>;
  roles: string[];
  arch?: string;
  kubelet?: string;
  os_image?: string;
  internal_ip?: string;
  cpu_capacity?: string;
  mem_capacity?: string;
  unschedulable?: boolean;
  created?: number;
}

export interface PodInfo {
  key: string;
  name: string;
  namespace: string;
  node?: string;
  phase: string;
  reason?: string | null;
  ready: string;
  restarts: number;
  containers: string[];
  created?: number;
}

export interface DeploymentInfo {
  key: string;
  name: string;
  namespace: string;
  replicas: number;
  ready: number;
  available: number;
  updated: number;
  images: string[];
}

export interface EventInfo {
  uid: string;
  type: string;
  reason: string;
  message: string;
  object: string;
  namespace?: string;
  count: number;
  t?: number;
}

export interface NodeMetrics {
  node?: string;
  cpu_pct?: number;
  mem_pct?: number;
  temp_c?: number;
  cpu_cores?: number;
  disk_used_pct?: number;
  load1?: number;
  uptime_s?: number;
  t?: number;
}

export interface HistoryPoint {
  t: number;
  cpu_pct?: number;
  mem_pct?: number;
  temp_c?: number;
  [k: string]: number | undefined;
}

export interface CheckResult {
  t: number;
  ok: boolean;
  ms?: number | null;
  detail?: string;
}

export interface HealthcheckEntry {
  config: { name: string; type?: string; url?: string; host?: string; port?: number; interval?: number };
  last?: CheckResult;
  uptime_pct?: number;
  history: CheckResult[];
}

export interface Snapshot {
  demo_mode: boolean;
  started_at: number;
  nodes: Record<string, NodeInfo>;
  pods: Record<string, PodInfo>;
  deployments: Record<string, DeploymentInfo>;
  events: EventInfo[];
  node_metrics: Record<string, NodeMetrics>;
  hardware: Record<string, NodeMetrics>;
  node_history: Record<string, HistoryPoint[]>;
  healthchecks: Record<string, HealthcheckEntry>;
}

export type ConnStatus = "connecting" | "open" | "closed" | "unauthorized";

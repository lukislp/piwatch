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
  images: string[];
  oom_killed: boolean;
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
  // Pi hardware add-ons (PoE+ M.2 HAT): all optional, present only when the
  // underlying sysfs/nvme-cli source is available on that node -- see
  // backend/app/node_agent.py's read_nvme_*/read_undervoltage().
  undervoltage?: boolean;
  nvme_temp_c?: number;
  nvme_model?: string;
  nvme_firmware?: string;
  nvme_serial?: string;
  nvme_capacity_bytes?: number;
  nvme_percent_used?: number;
  nvme_avail_spare?: number;
  nvme_spare_thresh?: number;
  nvme_power_on_hours?: number;
  nvme_unsafe_shutdowns?: number;
  nvme_media_errors?: number;
  nvme_critical_warning?: number;
  nvme_power_cycles?: number;
  nvme_data_units_read?: number;
  nvme_data_units_written?: number;
  nvme_host_read_commands?: number;
  nvme_host_write_commands?: number;
  nvme_controller_busy_time?: number;
  nvme_warning_temp_time?: number;
  nvme_critical_comp_time?: number;
  nvme_num_err_log_entries?: number;
  nvme_read_bytes_per_s?: number;
  nvme_write_bytes_per_s?: number;
  net_rx_bytes_per_s?: number;
  net_tx_bytes_per_s?: number;
}

export interface PodMetrics {
  cpu_cores?: number;
  mem_bytes?: number;
  t?: number;
}

export interface HistoryPoint {
  t: number;
  cpu_pct?: number;
  mem_pct?: number;
  temp_c?: number;
  nvme_read_bytes_per_s?: number;
  nvme_write_bytes_per_s?: number;
  net_rx_bytes_per_s?: number;
  net_tx_bytes_per_s?: number;
  [k: string]: number | undefined;
}

export interface CheckResult {
  t: number;
  ok: boolean;
  ms?: number | null;
  detail?: string;
}

export interface FluxKustomization {
  key: string;
  name: string;
  namespace: string;
  ready: boolean;
  reason?: string | null;
  message?: string | null;
  last_applied_revision?: string | null;
  last_transition_time?: string | null;
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
  pod_metrics: Record<string, PodMetrics>;
  node_history: Record<string, HistoryPoint[]>;
  healthchecks: Record<string, HealthcheckEntry>;
  flux_kustomizations: Record<string, FluxKustomization>;
}

export type ConnStatus = "connecting" | "open" | "closed" | "unauthorized";

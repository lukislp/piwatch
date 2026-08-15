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
  /** Reason/exit code of the most recent container termination (current or
   * last_state, whichever is more current) -- null if never terminated. */
  last_exit_reason?: string | null;
  last_exit_code?: number | null;
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

export interface StatefulSetInfo {
  key: string;
  name: string;
  namespace: string;
  replicas: number;
  ready: number;
  updated: number;
  images: string[];
}

export interface DaemonSetInfo {
  key: string;
  name: string;
  namespace: string;
  desired: number;
  ready: number;
  updated: number;
  images: string[];
}

export interface ServicePort {
  port: number;
  protocol: string;
  name?: string | null;
}

export interface ServiceInfo {
  key: string;
  name: string;
  namespace: string;
  type: string;
  cluster_ip?: string | null;
  /** Empty while the LoadBalancer controller hasn't assigned an address yet. */
  external_ips: string[];
  ports: ServicePort[];
}

export interface HpaMetric {
  name: string;
  target_pct?: number | null;
  current_pct?: number | null;
}

export interface HpaInfo {
  key: string;
  name: string;
  namespace: string;
  target_kind?: string | null;
  target_name?: string | null;
  min_replicas?: number | null;
  max_replicas?: number | null;
  current_replicas: number;
  desired_replicas: number;
  /** Only Resource-type metrics (CPU/memory utilization) -- see collectors/k8s_watch.py. */
  metrics: HpaMetric[];
  current_metrics: HpaMetric[];
  able_to_scale?: string | null;
  scaling_active?: string | null;
  scaling_limited?: string | null;
}

export interface NetworkPolicyInfo {
  key: string;
  name: string;
  namespace: string;
  pod_selector: string;
  policy_types: string[];
  ingress_rules: number;
  egress_rules: number;
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
  mem_bytes?: number;
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
  /** Unix seconds. Derived server-side (last reconcile + spec.interval); null if
   * either input is missing (e.g. the resource was just created). */
  next_reconcile_t?: number | null;
  managed_resource_count?: number;
  /** True while an apply attempt is in flight or stuck failing after a prior success. */
  apply_pending?: boolean;
  source_kind?: string | null;
  source_name?: string | null;
  source_namespace?: string | null;
}

export interface FluxGitRepository {
  key: string;
  name: string;
  namespace: string;
  ready: boolean;
  reason?: string | null;
  message?: string | null;
  url?: string | null;
  ref?: string | null;
  revision?: string | null;
  last_update_time?: string | null;
}

export interface FluxImagePolicy {
  key: string;
  name: string;
  namespace: string;
  ready: boolean;
  image?: string | null;
  latest_tag?: string | null;
  previous_tag?: string | null;
  tag_count?: number | null;
  last_scan_time?: string | null;
}

export interface FluxImageAutomation {
  key: string;
  name: string;
  namespace: string;
  ready: boolean;
  reason?: string | null;
  message?: string | null;
  last_automation_run_time?: string | null;
  last_push_commit?: string | null;
  last_push_time?: string | null;
}

export interface PvcInfo {
  key: string;
  name: string;
  namespace: string;
  phase?: string | null;
  storage_class?: string | null;
  access_modes: string[];
  volume_name?: string | null;
  requested_bytes?: number | null;
  capacity_bytes?: number | null;
  /** Only populated when the backend has PIWATCH_PROMETHEUS_URL configured. */
  usage_bytes?: number | null;
  usage_pct?: number | null;
}

export interface OrphanedPvInfo {
  key: string;
  name: string;
  /** Always "Released" or "Failed" -- the only phases the backend surfaces here. */
  phase?: string | null;
  capacity?: string | null;
  storage_class?: string | null;
  reclaim_policy?: string | null;
  /** The PVC that used to claim this volume, for context on what it was for. */
  claim_namespace?: string | null;
  claim_name?: string | null;
}

export interface GatewayInfo {
  key: string;
  name: string;
  namespace: string;
  gateway_class_name?: string | null;
  ready: boolean;
  reason?: string | null;
  message?: string | null;
  addresses: string[];
  listener_count: number;
  listeners_ready: number;
  attached_routes: number;
}

export interface HttpRouteInfo {
  key: string;
  name: string;
  namespace: string;
  hostnames: string[];
  parent_names: string[];
  backend_names: string[];
  accepted: boolean;
  resolved_refs: boolean;
  reason?: string | null;
  message?: string | null;
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
  statefulsets: Record<string, StatefulSetInfo>;
  daemonsets: Record<string, DaemonSetInfo>;
  services: Record<string, ServiceInfo>;
  hpas: Record<string, HpaInfo>;
  network_policies: Record<string, NetworkPolicyInfo>;
  orphaned_pvs: Record<string, OrphanedPvInfo>;
  events: EventInfo[];
  node_metrics: Record<string, NodeMetrics>;
  hardware: Record<string, NodeMetrics>;
  pod_metrics: Record<string, PodMetrics>;
  node_history: Record<string, HistoryPoint[]>;
  healthchecks: Record<string, HealthcheckEntry>;
  flux_kustomizations: Record<string, FluxKustomization>;
  flux_git_repositories: Record<string, FluxGitRepository>;
  flux_image_policies: Record<string, FluxImagePolicy>;
  flux_image_automations: Record<string, FluxImageAutomation>;
  pvcs: Record<string, PvcInfo>;
  gateways: Record<string, GatewayInfo>;
  http_routes: Record<string, HttpRouteInfo>;
}

export type ConnStatus = "connecting" | "open" | "closed" | "unauthorized";

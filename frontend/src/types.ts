// Mirrors server/plant_service.py and agents/*. Non-finite floats arrive as null.
export type Num = number | null;

export interface Controller {
  mode: "AUTO" | "MANUAL" | string;
  setpoint: number;
  manual_output: Num;
  last_output: number;
  sp_min: number;
  sp_max: number;
  sp_unit: string;
  mv_min: number;
  mv_max: number;
  mv_unit: string;
  pv_tag: string;
  pv: Num;
}

export interface PumpTrain {
  primary: string;
  spare: string;
  active_pump: string;
  primary_failed: boolean;
  spare_failed: boolean;
  selected_failed: boolean;
}

export interface Alarm {
  id: string;
  tag: string;
  state: "NORMAL" | "ACTIVE_UNACK" | "ACTIVE_ACK" | "RTN_UNACK";
  value: Num;
  limit: number;
  unit: string;
  priority: number;
  description: string;
}

export interface AlarmEvent {
  time_min: number;
  id: string;
  transition: string;
  priority: number;
  value: Num;
  description: string;
  by?: string;
}

export type TrendPoint = Record<string, Num> & { t: number };

export interface Snapshot {
  running: boolean;
  done: boolean;
  time_min: number;
  episode: string;
  episode_status: string;
  config: { scenario: string; horizon_min: number; seed: number; stop_on_trip: boolean };
  tick_delay_ms: number;
  pace?: { every: number; sleep_s: number };
  hold_for_agents?: boolean;
  held?: boolean;
  trend: TrendPoint;
  measurements: Record<string, Num>;
  controllers: Record<string, Controller>;
  throughput_target: number;
  auxiliaries: Record<string, number>;
  pumps: Record<string, PumpTrain>;
  alarms: Alarm[];
  alarm_history: AlarmEvent[];
  sif_latched: string[];
  plant_trip: boolean;
  last_action: string;
  truth: {
    schedule: { name: string; phase: string; start_min: number; end_min: Num }[];
    runtime: string[];
    actuator: string[];
    sensor: string[];
    pump: string[];
  };
}

export interface Meta {
  scenarios: string[];
  campaigns?: Record<string, string>;
  fault_library?: string[];
  loops: Record<string, { sp_min: number; sp_max: number; sp_unit: string; mv_unit: string; pv_tag: string }>;
  auxiliaries: Record<string, [number, number]>;
  throughput_bounds: [number, number];
  pump_trains: Record<string, { primary: string; spare: string; description: string }>;
  pump_ids: string[];
  injections: {
    disturbances: Record<string, string>;
    disturbance_steps?: Record<string, string>;
    actuator_targets: string[];
    actuator_modes: string[];
    sensor_targets: string[];
    process_faults: string[];
  };
}

export interface Contributor { variable: string; score: number; t2: number; spe: number }

export interface MonitorEvent {
  type: "monitor";
  time_min: number;
  fault: boolean;
  should_act: boolean;
  pca: null | {
    t2: number; spe: number; t2_limit: number; spe_limit: number;
    t2_violation: boolean; spe_violation: boolean; top_contributors: Contributor[];
  };
  pca_persistent: boolean;
  consecutive_pca: number;
  advisory?: string[];
  verifying_until?: number | null;
  rules: { failed_trains: string[]; active_alarms: string[]; saturated_loops: string[]; sif_latched: string[]; plant_trip: boolean };
  detectors: Record<string, boolean>;
  diagnosis: {
    hypotheses: { fault: string; score: number; evidence: string[] }[];
    sections: { section: string; share: number }[];
    contributors: Contributor[];
    summary: string;
  };
}

export interface Plan { commands: Record<string, unknown>[]; reasoning: string }

export interface Brief {
  target_fault: string;
  fault_label?: string;
  symptoms?: string[];
  alternatives?: string[];
  objective: string;
  candidate_levers: { type: string; target?: string; why?: string }[];
  constraints: string[];
  kg_nodes?: string[];
  planner_error?: string;
}

export interface AgentEvent {
  type: "agent";
  stage: "node" | "kg" | "plan" | "proposal" | "invalid" | "twin" | "twin_skipped" | "validation" | "llm_error" | "verification"
    | "reprompt" | "fallback" | "rejected" | "applied" | "pending" | "advisory" | "approved" | "dismissed"
    | "alarm_ack" | "llm_diagnosis" | "judge" | "sif_reset" | "admit";
  alarms?: string[];
  // admit (ADMIT gate verdict)
  admissible?: boolean;
  accept?: boolean;
  hazard_id?: string | null;
  simulate?: boolean;
  wall_s?: number;
  records?: AdmitRecordResult[];
  context?: AdmitContext;
  ledger?: AdmitEvidence[];
  ledger_head?: string;
  remedies?: Record<string, string>;
  // llm_diagnosis
  ok?: boolean;
  summary?: string;
  most_likely?: string | null;
  hypotheses?: { fault: string; probability: number; evidence: string[]; against: string[] }[];
  fault_class?: string;
  t2_spe_reading?: string;
  operator_checks?: string[];
  on_demand?: boolean;
  safe_reached?: boolean;
  restored?: boolean;
  time_to_restore_min?: number | null;
  final?: { active_alarms?: string[]; saturated_loops?: string[]; failed_trains?: string[]; sif_latched?: string[];
            unsafe_tags?: string[]; t2_ratio?: number | null; spe_ratio?: number | null;
            targets?: { name: string; ok: boolean; value: number | string; limit: string }[] };
  error?: string;
  checks?: { check: string; ok: boolean; detail: string }[];
  criterion?: string;
  kind?: string;
  cycle?: number;
  node?: string;
  attempt?: number;
  reprompt_max?: number;
  feedback?: Record<string, unknown>;
  rejected_plan?: Plan;
  time_to_safe_min?: number | null;
  unsafe_fraction?: number;
  terminal_status?: string;
  horizon_min?: number;
  final_unsafe_tags?: string[];
  t_obs?: number;
  time_min: number;
  plan?: Plan;
  errors?: string[];
  result?: { ok: boolean; summary: string };
  diagnosis?: string;
  proposal_id?: number;
  latency_s?: number;
  brief?: Brief;
  nodes?: string[];
  accepted?: boolean;
  fail_reason?: string;
  reason?: string;
  kg_citations?: string[];
}

export interface AgentInfo {
  mode: string;
  modes: string[];
  preset?: string;
  planner?: string;
  llm_model?: string | null;
  llm_usage?: LlmUsage;
  twin?: boolean;
  twin_horizon_min?: number;
  reprompt_max?: number;
  acceptance?: string;
  fallback_to_safe?: boolean;
  kg?: boolean;
  pca_loaded: boolean;
  pca_meta: null | { n_components: number; n_samples: number; explained_variance: number };
  actor: string | null;
  pending: (AgentEvent & { proposal_id: number })[];
}

export interface LlmUsage { calls: number; input_tokens: number; output_tokens: number; cost_usd: number }

export interface Message { type: "message"; level: "info" | "warn" | "error"; text: string; time_min: number }

export interface CampaignItem {
  label: string; name: string; kind: string; due_min: number; duration_min: number | null;
  status: "pending" | "active" | "cleared" | "failed"; injected_min?: number | null; end_min?: number | null; error?: string | null;
  waiting?: boolean;
}

export interface Campaign {
  name: string; t0: number; state: string; started: string; report_path: string | null; items: CampaignItem[];
}

export interface ChatMessage { role: "user" | "assistant"; text: string; tools?: string[]; pending?: boolean; id?: number }

export interface Hello {
  api_version?: number;
  meta: Meta;
  snapshot: Snapshot;
  trend: TrendPoint[];
  monitor: MonitorEvent[];
  agent_log: AgentEvent[];
  messages: Message[];
  agent?: AgentInfo;
  campaign?: Campaign | null;
  chat?: { mode: string; model?: string | null };
  session?: SessionInfo;
}

export interface SessionInfo {
  id: string;
  hosted: boolean;
  models: string[];
  llm: { model: string; key_source: "visitor" | "server" | null; active: boolean; reason: string;
         preset: string; presets: Record<string, string>;
         connection: { provider: string; label: string; model: string; free: boolean; url: string | null } | null;
         providers: Record<string, LlmProvider> };
}

export interface LlmProvider { label: string; models: string[]; free: boolean; key: boolean; signup: string; url: boolean }

// ADMIT gate trace (stage "admit"): what each action is checked on
export interface AdmitEvidence { id: string; tag: string | null; source: string; trust: string; quality: string; content: string }
export interface AdmitContext {
  decided_at_min: number; mode: string; authority_scope: string[]; hazard_id: string | null;
  diagnosis_accept: string[]; response_time_s: number; horizon_min: number | null; simulate: boolean;
}
export interface AdmitGateResult {
  tier: string; name: string; passed: boolean; skipped: boolean;
  violations: { code: string; message: string }[]; info: Record<string, unknown>;
}
export interface AdmitRecordResult {
  command: Record<string, unknown>; action: string; params: Record<string, unknown>;
  admissible: boolean; first_failing: string | null; aas_code: string | null;
  record: { filed: boolean; cited_evidence: string[]; checks_performed: string[]; confidence: number;
            reversibility: string; recovery_plan: string; retry_key: string | null; rationale: string;
            hazard_id: string; diagnosis: string };
  rule: { class?: string; reversibility?: string; required_authority?: string[]; required_evidence_tags?: string[];
          required_steps?: string[]; step_evidence_tags?: Record<string, string[]>; confidence_floor?: number;
          allowed_modes?: string[]; forbidden?: boolean; description?: string };
  gates?: AdmitGateResult[];
  violations: { code: string; gate: string; message: string }[];
  trajectories?: Record<string, { crossed?: boolean; crossed_at_s?: number | null; crossed_var?: string | null;
                                   min_margin?: number; diverging?: boolean; recovered?: boolean; recovery_action?: string | null }>;
}

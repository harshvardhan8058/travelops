/**
 * API types.
 *
 * Wave 0 hand-writes these to match backend/app contracts exactly. Once the backend is
 * serving real endpoints, generate them from docs/openapi.json (`make openapi`) and delete
 * this file — do not maintain two sources of truth.
 *
 * Owner: Stream E.
 */

// ---------------------------------------------------------------- provenance
/** Rendered by the UI, never inferred from a provider name. */
export type ProvenanceKind = 'real' | 'simulated' | 'synthetic' | 'fixture' | 'unavailable';

export interface Provenance {
  kind: ProvenanceKind;
  provider: string;
  source_ref?: string | null;
  observed_at?: string | null;
  retrieved_at?: string | null;
  is_stale?: boolean;
}

// ---------------------------------------------------------------- enums
export type IncidentState =
  | 'detected'
  | 'assessing'
  | 'planning'
  | 'assuring'
  | 'awaiting_approval'
  | 'executing'
  | 'resolved'
  | 'blocked'
  | 'failed';

/** A band, not a probability. Nothing here is calibrated. */
export type RiskLevel = 'low' | 'elevated' | 'high' | 'severe';

export type TaskState =
  | 'pending'
  | 'proposed'
  | 'assured'
  | 'needs_human'
  | 'rejected'
  | 'executing'
  | 'succeeded'
  | 'failed'
  | 'skipped';

/** Three states. A WARN must never be collapsed into a boolean. */
export type CheckState = 'PASS' | 'WARN' | 'FAIL';
export type AssuranceDecision = 'execute' | 'execute_flagged' | 'needs_human';
export type RiskTier = 'low' | 'medium' | 'high';

export type CheckName =
  | 'evidence_complete'
  | 'sources_fresh'
  | 'entities_valid'
  | 'policy_compliant'
  | 'no_conflicts'
  | 'action_risk';

/** Fixed order, so the panel and the audit record always agree. */
export const CHECK_ORDER: readonly CheckName[] = [
  'evidence_complete',
  'sources_fresh',
  'entities_valid',
  'policy_compliant',
  'no_conflicts',
  'action_risk',
] as const;

// ---------------------------------------------------------------- system
export interface SystemMode {
  llm_mode: 'live' | 'fixture' | 'off';
  weather_mode: 'live' | 'fixture';
  notification_mode: 'console' | 'mailtrap' | 'gmail';
  policy_mode: 'demo' | 'charter' | 'verified';
  real_email_enabled: boolean;
  app_env: string;
  assurance: {
    config_present: boolean;
    config_version: string | null;
    config_hash: string | null;
    workflow_executable: boolean;
  };
  degradations: string[];
  policy_pack: { id: string; version: string; ui_label: string };
  limits: { max_workflow_steps: number; action_timeout_seconds: number };
  data_seed: number;
}

export interface ReadyStatus {
  status: 'ready' | 'not_ready';
  dependencies: Record<string, { status: 'up' | 'down'; detail?: string }>;
  assurance: { config_present: boolean; workflow_executable: boolean };
  degradations: string[];
}

// ---------------------------------------------------------------- flights
export interface AirportConditions {
  airport_icao: string;
  iata: string;
  city: string;
  wind_speed_kt: number | null;
  wind_direction_deg: number | null;
  visibility_m: number | null;
  ceiling_ft: number | null;
  precipitation: string | null;
  risk_index: number;
  risk_level: RiskLevel;
  observation_age_minutes: number;
  provenance: Provenance;
}

export interface FlightRow {
  id: number;
  flight_number: string;
  airline_code: string;
  origin_icao: string;
  destination_icao: string;
  scheduled_departure: string;
  estimated_departure: string | null;
  delay_minutes: number;
  block_time_minutes: number;
  status: string;
  risk_index: number;
  risk_level: RiskLevel;
  passengers: number;
  connections_at_risk: number;
  incident_reference: string | null;
  provenance: Provenance;
}

export interface FlightsResponse {
  network: AirportConditions[];
  flights: FlightRow[];
}

// ---------------------------------------------------------------- assurance
export interface CheckResult {
  name: CheckName;
  state: CheckState;
  reason_code: string;
  reason?: string;
  tier?: RiskTier;
  evidence_refs?: string[];
}

export interface AssuranceEvaluation {
  id: number;
  plan_task_id: number;
  action_type: string;
  decision: AssuranceDecision;
  risk_tier: RiskTier;
  evaluated_at: string;
  checks: CheckResult[];
  blocking: CheckName[];
  evidence_refs: string[];
  /** Always displayed: a replay must prove which semantics applied. */
  config_version: string;
  config_hash: string;
  human_decision?: unknown | null;
  note?: string;
  warn_permitted_by_config?: boolean;
}

export interface AssuranceResponse {
  config_version: string;
  config_hash: string;
  evaluations: AssuranceEvaluation[];
  awaiting_approval_count: number;
}

// ---------------------------------------------------------------- incidents
export interface RiskFactor {
  name: string;
  value: string;
  threshold?: string;
  runway?: string;
}

export interface TimelineEntry {
  id: number;
  occurred_at: string;
  stage: string;
  actor: string;
  actor_kind: 'orchestrator' | 'agent' | 'service' | 'human' | 'provider';
  event_type: string;
  summary: string;
  detail?: Record<string, unknown>;
}

export interface TimelineResponse {
  incident_reference: string;
  entries: TimelineEntry[];
}

export interface PlanTaskRow {
  id: number;
  task_order: number;
  action_type: string;
  state: TaskState;
  depends_on: string[];
  assurance_id: number | null;
}

/**
 * The flight summary embedded in an incident. Every field beyond the number is optional
 * because the UI must render whatever the endpoint actually returns and name what it does
 * not, rather than assuming a field exists.
 */
export interface IncidentFlightSummary {
  id?: number;
  flight_number: string;
  route?: string;
  scheduled_departure?: string;
  estimated_departure?: string | null;
  delay_minutes?: number;
  block_time_minutes?: number;
  passengers?: number;
}

export interface WeatherObservation {
  airport_icao?: string;
  observed_at?: string;
  wind_speed_kt?: number | null;
  wind_direction_deg?: number | null;
  visibility_m?: number | null;
  ceiling_ft?: number | null;
  precipitation?: string | null;
  /** Present only when the endpoint records it. The UI never computes an age from now(). */
  observation_age_minutes?: number;
  provenance: Provenance;
}

/** Matched by explainable SQL filtering, not vector similarity. */
export interface RetrievedPrecedent {
  incident_reference?: string;
  matched_on?: string[];
  outcome?: string;
  note?: string;
}

/** A side effect that actually happened, with the authorisation that permitted it. */
export interface ActionRecord {
  id: number;
  plan_task_id: number;
  assurance_id: number;
  human_decision_id: number | null;
  actor: string;
  status: string;
  reason: string;
  cost_inr: number | null;
  idempotency_key: string;
  executed_at: string | null;
  provenance_kind?: ProvenanceKind;
}

/**
 * Append-only, unique per evaluation (docs/11-data-model.md). Correcting a decision requires
 * a new evaluation rather than mutating history, so the UI never edits one of these.
 */
export interface HumanDecision {
  assurance_id: number;
  decision: 'approved' | 'rejected';
  actor_id: string;
  reason: string;
  decided_at: string;
  /**
   * True when the decision exists only in this browser session because fixtures are being
   * served and no endpoint accepted the write. Rendered explicitly: a demo must never imply
   * an audit record that does not exist.
   */
  persisted: boolean;
}

export interface IncidentDetail {
  id: number;
  reference: string;
  group_reference: string | null;
  flight: IncidentFlightSummary;
  trigger_type: string;
  severity: string;
  state: IncidentState;
  opened_at: string;
  state_rail: { state: IncidentState; reached_at: string | null }[];
  evidence: {
    risk: {
      risk_index: number;
      risk_level: RiskLevel;
      rule_version: string;
      factors: RiskFactor[];
      note?: string;
    };
    weather: WeatherObservation;
    affected_entities: Record<string, number>;
    retrieved_precedent?: RetrievedPrecedent | null;
  };
  plan: {
    id: number;
    /** 'groq:llama-3.3-70b' or 'fallback-playbook'. Never ambiguous in the UI. */
    generator: string;
    prompt_version: string | null;
    /** Diagnostic metadata only. Never drives a decision. */
    model_self_report: number | null;
    generated_at?: string;
    rationale?: string;
    tasks: PlanTaskRow[];
  };
  actions: ActionRecord[];
  provenance: Provenance;
}

// ---------------------------------------------------------------- cascade
export type PairingMechanism = 'operating' | 'onward_duty' | 'second_pairing' | 'positioning';

export interface CrewPairingImpact {
  pairing_reference: string;
  base_icao: string;
  source_flight: string;
  affected_leg: string;
  /** The edge label. This is what makes 8 flights -> 9 pairings readable. */
  mechanism: PairingMechanism;
  detail: string;
  at_risk: boolean;
}

export interface IncidentGroupDetail {
  id: number;
  reference: string;
  root_cause: string;
  airport_icao: string;
  severity: string;
  state: IncidentState;
  /** Derived server-side from the arrays. The UI never hardcodes a total. */
  rollups: Record<string, number | string>;
  flights: Record<string, unknown>[];
  crew_pairings: CrewPairingImpact[];
  mechanism_legend: Record<PairingMechanism, string>;
  why_nine_not_eight: string;
  provenance: Provenance;
}

// ---------------------------------------------------------------- policy
export interface PolicyPackInfo {
  id: string;
  version: string;
  status: 'draft' | 'official_guidance_dated' | 'approved' | 'retired';
  verified_mode_eligible: boolean;
  /** Rendered verbatim. There is no manual override. */
  ui_label: string;
  authority: string;
  document: string;
  pack_hash: string;
  source_hash: string;
}

export interface Entitlement {
  type: string;
  outcome: string;
  amount_inr?: number;
  currency?: string;
  cash?: boolean;
  options?: string[];
  reason_codes?: string[];
  explanation: string;
  rules_fired: string[];
  source_clause_refs: string[];
  input_facts?: Record<string, unknown>;
}

export interface PolicyResponse {
  policy_mode: 'demo' | 'charter' | 'verified';
  pack: PolicyPackInfo;
  applicability: {
    status: 'applicable' | 'not_applicable' | 'undetermined';
    missing_facts: string[];
    [key: string]: unknown;
  }[];
  event: Record<string, unknown>;
  entitlements: Entitlement[];
  cause_assessment: Record<string, unknown>;
  cause_comparison?: Record<string, unknown>;
  excluded_rules: { rule_key: string; status: string; reason: string; evaluated: boolean }[];
  disclaimer: string;
}

// ---------------------------------------------------------------- errors
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    correlation_id: string | null;
    details: Record<string, unknown>;
  };
}

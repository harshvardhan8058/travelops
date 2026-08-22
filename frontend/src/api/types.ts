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

export const PROVENANCE_KINDS: readonly ProvenanceKind[] = [
  'real',
  'simulated',
  'synthetic',
  'fixture',
  'unavailable',
] as const;

/**
 * Narrows a provenance string from an untyped field, e.g. `ActionSummary.provenance_kind`.
 *
 * Returns null for anything unrecognised rather than guessing a kind, because a wrong
 * provenance dot is worse than an absent one: a controller who cannot tell live data from
 * simulated cannot make a decision, and a confidently wrong dot removes the chance to notice.
 */
export function asProvenanceKind(value: string | undefined | null): ProvenanceKind | null {
  if (!value) return null;
  return PROVENANCE_KINDS.includes(value as ProvenanceKind) ? (value as ProvenanceKind) : null;
}

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
  /** Real API always sends the key, null when there is nothing to say. */
  reason?: string | null;
  tier?: RiskTier | null;
  /**
   * Always `[]` from the real API today: the engine persists only state, reason code, reason
   * and tier per check, so refs live at evaluation level.
   */
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
  /** The persisted operator response, or null. Authoritative over any session-only copy. */
  human_decision?: HumanDecisionOut | null;
  /** Fixture-only commentary; the real API does not return it. */
  note?: string;
  /** Null unless a WARN was recorded. */
  warn_permitted_by_config?: boolean | null;
}

/** As embedded on an evaluation by the real API. */
export interface HumanDecisionOut {
  id: number;
  decision: 'approved' | 'rejected';
  actor_id: string;
  reason: string;
  decided_at: string;
}

export interface AssuranceResponse {
  /**
   * Which incident these evaluations belong to. Optional only because the committed fixture
   * predates the field; the real API always sends it. Consumed rather than decorative: the
   * panel states the scope and flags a mismatch against the incident on screen, because
   * rendering another incident's gate records next to this one would be the worst kind of
   * quiet bug.
   */
  incident_reference?: string;
  /** Literally 'unavailable' when nothing has been evaluated yet. */
  config_version: string;
  config_hash: string;
  evaluations: AssuranceEvaluation[];
  awaiting_approval_count: number;
}

// ---------------------------------------------------------------- incidents
export interface RiskFactor {
  name: string;
  /**
   * The observed figure, e.g. `"1.5"`. Empty string when the rule recorded no observed value,
   * so a renderer must fall back rather than leaving a blank cell.
   */
  value: string;
  /**
   * Why this factor says what it says, in the rule's own words. Added by Stream C alongside
   * `points`; without it a factor reads as an unexplained number.
   */
  detail?: string | null;
  /** Contribution to the index. This is what makes an index explainable rather than asserted. */
  points?: number | null;
  threshold?: string | null;
  runway?: string | null;
}

export interface TimelineEntry {
  id: number;
  occurred_at: string;
  stage: string;
  actor: string;
  actor_kind: 'orchestrator' | 'agent' | 'service' | 'human' | 'provider';
  event_type: string;
  summary: string;
  detail?: Record<string, unknown> | null;
  correlation_id?: string | null;
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
 *
 * `route` arrives as ICAO codes from the real API (`VOBL → VIDP`) and as IATA in the
 * committed fixture (`BLR → DEL`). It is rendered as returned; the UI never translates one
 * into the other, because it has no airport table to do that correctly.
 */
export interface IncidentFlightSummary {
  id?: number;
  flight_number: string;
  route?: string;
  scheduled_departure?: string;
  estimated_departure?: string | null;
  delay_minutes?: number;
  block_time_minutes?: number;
  /** Null when no booking records exist. Deliberately not 0 — see backend FlightSummary. */
  passengers?: number | null;
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
  /** Present on the real API, absent from the committed fixture. */
  action_type?: string;
  assurance_id: number;
  human_decision_id: number | null;
  actor: string;
  status: string;
  /**
   * Free text, but it begins with a stable reason code when execution was refused — e.g.
   * `SERVICE_NOT_IMPLEMENTED: …`. dispatch.py states the code is stable precisely so the UI
   * maps it to copy instead of paraphrasing the message.
   */
  reason: string;
  cost_inr: number | null;
  idempotency_key: string;
  executed_at: string | null;
  provenance_kind?: ProvenanceKind | string;
}

/**
 * Append-only, unique per evaluation (docs/11-data-model.md). Correcting a decision requires
 * a new evaluation rather than mutating history, so the UI never edits one of these.
 *
 * Matches the backend's `HumanDecisionOut`, which is embedded on each evaluation as
 * `human_decision`. `id` is absent on a session-only record, which is exactly the difference
 * `persisted` records.
 */
export interface HumanDecision {
  id?: number;
  assurance_id: number;
  decision: 'approved' | 'rejected';
  actor_id: string;
  reason: string;
  decided_at: string;
  /**
   * True when the API accepted the write. False when the decision exists only in this
   * browser session because fixtures are being served and no endpoint took it. Rendered
   * explicitly: a demo must never imply an audit record that does not exist.
   */
  persisted: boolean;
}

/** Result of `POST /incidents/{id}/run` — the real "advance the workflow" call. */
export interface RunResponse {
  incident_reference: string;
  state: IncidentState;
  previous_state: IncidentState;
  steps_taken: number;
  is_terminal: boolean;
  /** Why the run stopped short of a terminal state. Rendered verbatim, never summarised. */
  note: string | null;
  replayed: boolean;
  idempotency_key: string | null;
}

/** Result of `POST /assurance/{id}/decision`. */
export interface DecisionResponse {
  assurance_id: number;
  decision: 'approved' | 'rejected';
  actor_id: string;
  reason: string;
  decided_at: string;
  /** True when a matching decision already existed; the original is returned unchanged. */
  replayed: boolean;
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
  closed_at?: string | null;
  /**
   * Six canonical states, PLUS an appended `awaiting_approval` / `blocked` / `failed` entry
   * when one was actually reached. Verified against the real API: a blocked incident returns
   * seven entries. Render it as a list, never as six fixed slots.
   */
  state_rail: { state: IncidentState; reached_at: string | null }[];
  evidence: {
    /**
     * NULL until the Delay Risk service has recorded a Prediction — which is the normal state
     * of a freshly opened incident, confirmed against the live API. Never a fabricated index.
     */
    risk: RiskEvidence | null;
    /** Null until a weather observation exists for the origin airport. */
    weather: WeatherObservation | null;
    /**
     * Only keys derived from records. An absent key means "not computed", NOT zero, so the UI
     * renders an em dash for anything missing. The real API returns at most `passengers` and
     * `bookings`; the committed fixture also carries connections, crew and hotel counts.
     */
    affected_entities?: Record<string, number>;
    retrieved_precedent?: RetrievedPrecedent | null;
  };
  /** NULL before the orchestrator has proposed a plan. */
  plan: PlanSummary | null;
  actions: ActionRecord[];
  provenance: Provenance;
}

export interface RiskEvidence {
  risk_index: number;
  risk_level: RiskLevel;
  rule_version: string;
  factors: RiskFactor[];
  evidence_refs?: string[];
  /** Fixture-only commentary; the real API does not return it. */
  note?: string;
}

export interface PlanSummary {
  id: number;
  /**
   * 'fallback-playbook' or 'groq:llama-3.3-70b'. The real API returns the bare token; the
   * committed fixture appends ' · deterministic'. Classify on the token, never on the prose.
   */
  generator: string;
  prompt_version: string | null;
  /** Diagnostic metadata only. Never drives a decision. */
  model_self_report: number | null;
  generated_at?: string;
  rationale?: string | null;
  tasks: PlanTaskRow[];
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

/** One row of `GET /incident-groups`. Rollups are counts computed server-side. */
export interface IncidentGroupSummary {
  id: number;
  reference: string;
  root_cause: string;
  airport_icao: string;
  severity: string;
  state: IncidentState;
  opened_at: string;
  rollups: Record<string, number>;
  awaiting_approval_count: number;
  provenance: Provenance;
}

export interface IncidentGroupsResponse {
  groups: IncidentGroupSummary[];
}

/** One row of the provenance ledger. The definitive answer to "is any of this real?". */
export interface SourceRow {
  name: string;
  kind: ProvenanceKind;
  provider: string;
  current_mode: string;
  last_checked?: string | null;
  licence: string;
  attribution_required?: boolean;
  health: string;
  note?: string;
}

export interface SourcesResponse {
  sources: SourceRow[];
}

/** Metrics are derived from recorded rows. An absent metric is absent, never estimated. */
export interface ReportResponse {
  incident_reference: string;
  metrics: Record<string, number | string | null>;
  narrative: { generated_by: string | null; text: string | null; note?: string };
  caveats?: string[];
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

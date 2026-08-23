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
 * `route` arrives as ICAO codes from the real API (`VOBL -> VIDP`) and as IATA in the
 * committed fixture (`BLR -> DEL`). ASCII, not U+2192: `Inter` and `JetBrains Mono` are webfonts and
 * the fallback draws the arrow as a tofu box. It is rendered as returned; the UI never translates one
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
  /**
   * The stable token behind `reason`, so a consumer classifies on a field instead of a prefix.
   * The real API sends it on this list contract; it is absent from the committed fixture.
   */
  reason_code?: string | null;
  /**
   * Always `null` on THIS contract even when a decision exists: the list endpoint does not
   * resolve it. `GET /incidents/{ref}/actions/{id}` is the only place the real scope appears, so
   * nothing may conclude "no person authorised this" from the list alone.
   */
  decision_scope?: string | null;
  plan_approval_id?: number | null;
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

/** One declared member flight of the cascade. */
export interface GroupFlight {
  id: number;
  flight_number: string;
  route: string;
  delay_minutes: number;
  passengers: number;
  state: string;
  /** `primary`, `affected_departure`, `affected_arrival`. Declared, never inferred. */
  role: string;
  /**
   * `null` means the group declares this flight but no incident is open for it yet — "affected,
   * not yet being worked". Meaningful, and different from missing.
   */
  incident_reference: string | null;
}

/**
 * Whether the rollup describes the whole group or only part of it.
 *
 * A partial rollup must render as partial. Eight flights' worth of caption over six flights' worth
 * of evidence is the failure mode this block exists to prevent.
 */
export interface GroupRollupStatus {
  is_complete: boolean;
  computed_at: string | null;
  note: string;
  /** Named, not counted, so the gap is actionable. */
  flights_without_incident: number[];
  membership_is_declared: boolean;
}

/** One measured dimension of reach, with the service that measured it named. */
export interface BlastRadiusDimension {
  key: string;
  label: string;
  value: number;
  unit: string;
  /** Empty means declared data rather than a service finding. */
  measured_by: string;
  /** False makes the value a floor rather than a total. */
  is_complete: boolean;
  note: string;
}

/**
 * The server's blast radius. Composed from recorded findings; nothing originates in the UI.
 *
 * `basis` is a literal on the server contract, so a `confidence` field cannot appear without a
 * deliberate type change. `headline` carries its own caveat inside one string on purpose — split
 * into two fields, a UI renders the totals and drops the qualification.
 */
export interface ServerBlastRadius {
  group_reference: string;
  headline: string;
  basis: 'composed_from_recorded_findings';
  dimensions: BlastRadiusDimension[];
  completeness: {
    flights_declared: number;
    flights_assessed: number;
    ratio: string;
    is_complete: boolean;
  };
  /** Named, countable gaps. Never a score. */
  gaps: string[];
}

export interface CascadeGraphNode {
  ref: string;
  kind: string;
  label: string;
  sublabel?: string | null;
  depth: number;
  at_risk: boolean;
  /** False when declared but not yet assessed. Rendered as a gap, never dropped. */
  has_evidence: boolean;
  role?: string | null;
}

export interface CascadeGraphEdge {
  source_ref: string;
  target_ref: string;
  edge_kind: string;
  mechanism?: string | null;
  detail?: string | null;
  depth: number;
  is_at_risk: boolean;
  /** Exactly one is set. An edge without provenance is an assertion, not evidence. */
  derived_from_action_id: number | null;
  derived_from_prediction_id: number | null;
}

export interface CascadeGraph {
  group_reference: string;
  rule_version: string;
  nodes: CascadeGraphNode[];
  edges: CascadeGraphEdge[];
  edge_counts_by_kind: Record<string, number>;
  completeness: {
    member_flight_count: number;
    flights_with_evidence: number;
    is_complete: boolean;
    note: string;
  };
  source_action_ids: number[];
  source_prediction_ids: number[];
  snapshot_hash: string;
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
  flights: GroupFlight[];
  crew_pairings: CrewPairingImpact[];
  mechanism_legend: Record<PairingMechanism, string>;
  why_nine_not_eight: string;
  provenance: Provenance;
  /** Present on the real endpoint; absent from the committed fixture. */
  rollup_status?: GroupRollupStatus;
  blast_radius?: ServerBlastRadius;
  graph?: CascadeGraph;
  awaiting_approval_count?: number;
}

export interface GroupMember {
  flight_id: number;
  flight_number: string;
  role: string;
  incident_id: number | null;
  incident_reference: string | null;
  state: string | null;
  note: string | null;
}

export interface GroupRunResponse {
  group_reference: string;
  state: IncidentState;
  members: GroupMember[];
  opened_incident_ids: number[];
  blocked_reason: string | null;
  awaiting_approval_count: number;
  replayed: boolean;
}

// ---------------------------------------------------------------- candidate plans

export interface CandidatePlanTask {
  id: number;
  action_type: string;
  task_order: number;
  state: string;
  target_refs: string[];
  depends_on: number[];
}

export interface CandidatePlan {
  id: number;
  incident_reference: string;
  variant_key: string | null;
  generator: string;
  generated_at: string;
  rationale: string | null;
  selection_state: string;
  selected_at: string | null;
  selected_by: string | null;
  plan_hash: string | null;
  tasks: CandidatePlanTask[];
}

export interface CandidatePlansResponse {
  incident_reference: string;
  plans: CandidatePlan[];
  selected_plan_id: number | null;
}

/**
 * One candidate's figures. Arithmetic only.
 *
 * There is deliberately no rank, score or `recommended` flag: choosing between recovery plans is a
 * judgement, and a judgement has an owner. The UI must not invent one either.
 */
export interface CandidateComparisonRow {
  candidate_id: string;
  variant_key: string;
  plan_id: number | null;
  plan_hash: string;
  admissible: boolean;
  decision: string;
  plan_risk_tier: string;
  task_count: number;
  exposure_inr: number | null;
  passengers_affected: number | null;
  rooms_committed: number | null;
  external_effects: number | null;
  high_risk_actions: number;
  approvals_required: number;
  uncovered_entities: number;
  blocking_checks: string[];
  unresolved_cohorts: string[];
  selection_state: string | null;
  rationale: string | null;
}

export interface CandidateComparisonResponse {
  incident_reference: string;
  /** A literal on the server contract: this response cannot express a projection. */
  basis: 'recorded_evidence';
  not_a_forecast: string;
  decision: string;
  admissible: string[];
  blocking_reasons: string[];
  seed: number | null;
  what_if: Record<string, unknown> | null;
  candidates: CandidateComparisonRow[];
}

// ---------------------------------------------------------------- group assurance

export interface PlanCheck {
  name: string;
  state: CheckState;
  reason_code: string;
  reason: string | null;
  tier: string | null;
  offending_refs: string[];
}

export interface PlanTaskOutcome {
  task_id: string;
  action_type: string;
  decision: string;
  risk_tier: string;
  blocking_kinds: string[];
  approvable: boolean;
  evaluation_id: number | null;
  target_refs: string[];
  depends_on: string[];
}

export interface IncidentPlanAssurance {
  incident_reference: string;
  plan_id: number;
  variant_key: string | null;
  task_count: number;
  tasks: PlanTaskOutcome[];
  awaiting_approval_count: number;
  config_version: string;
  config_hash: string;
}

export interface CoveredEvaluation {
  evaluation_id: number;
  plan_task_id: number;
  incident_reference: string;
  action_type: string;
  risk_tier: string;
  human_decision_id: number;
}

export interface ExcludedEvaluation {
  evaluation_id: number;
  plan_task_id: number;
  incident_reference: string;
  action_type: string;
  risk_tier: string;
  reason_code: string;
  reason: string;
}

export interface PlanApprovalPreview {
  plan_id: number | null;
  plan_hash: string;
  covered: CoveredEvaluation[];
  excluded: ExcludedEvaluation[];
  covered_count: number;
  excluded_count: number;
  refusal: string | null;
  refusal_reason: string | null;
}

/**
 * Group-scoped plan assurance.
 *
 * `authorises_no_action` is a literal `true` on the server contract. This response aggregates for
 * display and grants nothing — every action still passes its own gate at execution time. There is
 * no aggregate score at any level: a fail-closed, ordered gate has no average.
 */
export interface GroupAssuranceResponse {
  group_reference: string;
  decision: string;
  plan_risk_tier: string;
  task_count: number;
  checks: PlanCheck[];
  blocking: string[];
  admissible: boolean;
  requires_human: boolean;
  authorises_no_action: true;
  plan_hash: string;
  config_version: string;
  config_hash: string;
  /** False when member incidents were judged under different config hashes. Must be surfaced. */
  config_hash_uniform: boolean;
  evaluated_at: string;
  /**
   * The figures the exposure check measured, exactly as the gate received them.
   *
   * `null` means **not established**, and the gate treats that as a breach rather than as zero — a
   * plan whose cost is unknown is not a plan whose cost is nothing. A client must therefore never
   * render a null here as `0`.
   */
  exposure: GroupExposure;
  incidents: IncidentPlanAssurance[];
  approval_preview: PlanApprovalPreview | null;
}

export interface PlanApprovalResponse extends PlanApprovalPreview {
  plan_approval_id: number | null;
  replayed: boolean;
}

// ---------------------------------------------------------------- what-if and replay

export interface GroupExposure {
  total_exposure_inr?: number | null;
  passengers_affected?: number | null;
  rooms_committed?: number | null;
  external_effects?: number | null;
  /** Cohorts the entitlement engine could not resolve. Any entry makes exposure unknown. */
  unresolved_cohorts?: string[];
}

export interface WhatIfDelta {
  key: string;
  label: string;
  baseline: number;
  scenario: number;
  delta: number;
  summary: string;
}

// ------------------------------------------------------------------ per-passenger impact

/** One named reason a passenger scored where they did. Every point is attributable to one. */
export interface ImpactFactor {
  factor: string;
  weight: number;
  /** The column or recorded finding the factor was read from. */
  source: string;
}

export interface PassengerImpact {
  passenger_id: number;
  passenger_reference: string;
  booking_id: number;
  pnr: string;
  priority_index: number;
  priority_band: string;
  factors: ImpactFactor[];
  rule_version: string;
  ruleset_hash: string;
}

export interface ImpactCohort {
  band: string;
  passenger_count: number;
  lowest_index: number;
  highest_index: number;
  /** How many passengers in this band carry each factor. The operational shopping list. */
  factor_counts: Record<string, number>;
  booking_ids: number[];
}

/**
 * A factor the ruleset declares that no service has established yet.
 *
 * The distinction the whole surface turns on. "Absent because its input has not been produced" is
 * not "false", and rendering the first as the second tells an operator that nobody needs rebooking
 * when in truth nobody has looked.
 */
export interface UnassessedFactor {
  factor: string;
  reason: string;
  /** The service that would establish it. */
  established_by: string;
}

/** Read from `passenger_impact`. A constraint ranking, not a probability. Authorises nothing. */
export interface GroupImpactResponse {
  group_reference: string;
  rule_version: string;
  ruleset_hash: string;
  computed_at: string | null;
  passengers_assessed: number;
  cohorts: ImpactCohort[];
  /** Highest priority first, capped. `passengers_assessed` carries the true total. */
  passengers: PassengerImpact[];
  returned: number;
  unassessed_factors: UnassessedFactor[];
  basis: 'persisted_records';
  note: string;
}

/**
 * A bounded, zero-write, deterministic re-evaluation.
 *
 * `basis` and `wrote_rows` are literals on the server contract, so this cannot claim a projection
 * or a write. It is explicitly not a simulation engine and not a digital twin.
 */
export interface WhatIfResponse {
  group_reference: string;
  rule_version: string;
  basis: 'recorded_evidence';
  wrote_rows: false;
  boundary_note: string;
  headline: string;
  permitted: boolean;
  refusals: string[];
  seed: number | null;
  recorded_baseline: Record<string, number>;
  levers_applied: Record<string, unknown>;
  levers_available: string[];
  levers_rejected: { lever: string; reason: string }[];
  deltas: WhatIfDelta[];
}

export interface ReplayFrame {
  sequence: number;
  occurred_at: string;
  stage: string;
  actor: string;
  actor_kind: string;
  event_type: string;
  summary: string;
  state_before: string | null;
  state_after: string | null;
  incident_reference: string | null;
  evidence_refs: string[];
  assurance_id: number | null;
  human_decision_id: number | null;
  /** `action` or `plan`. Both are a person's act; an auditor tells them apart. */
  decision_scope: string | null;
  plan_approval_id: number | null;
  detail: Record<string, unknown>;
}

export interface ServerReplayResponse {
  incident_reference: string | null;
  group_reference: string | null;
  frame_count: number;
  frames: ReplayFrame[];
  is_read_only: boolean;
  note: string;
}

export interface ActionDetail {
  id: number;
  plan_task_id: number;
  action_type: string;
  assurance_id: number;
  human_decision_id: number | null;
  actor: string;
  status: string;
  reason: string;
  cost_inr: number | null;
  provenance_kind: string;
  executed_at: string | null;
  idempotency_key: string;
  reason_code: string | null;
  decision_scope: string | null;
  plan_approval_id: number | null;
  /** Recorded verbatim by the service. Service-shaped; version-gated by the field below. */
  payload: Record<string, unknown>;
  payload_schema_version: number;
  incident_reference: string;
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

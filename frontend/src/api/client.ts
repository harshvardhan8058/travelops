/**
 * Typed API client.
 *
 * `VITE_USE_FIXTURES=true` serves the committed fixtures instead of calling the backend,
 * so Streams E and F can build the entire UI before a single endpoint is real. Switching
 * to live data is a change of one environment variable, not a rewrite.
 *
 * Owner: Stream E.
 */

import type {
  ActionDetail,
  AssuranceResponse,
  CandidateComparisonResponse,
  CandidatePlansResponse,
  CascadeGraph,
  DecisionResponse,
  ExplanationResponse,
  FlightsResponse,
  GroupAssuranceResponse,
  GroupImpactResponse,
  GroupRunResponse,
  IncidentDetail,
  IncidentGroupDetail,
  IncidentGroupsResponse,
  IncidentGroupSummary,
  PlanApprovalResponse,
  PolicyResponse,
  ReadyStatus,
  ReportResponse,
  RunResponse,
  ScenarioCreateRequest,
  ScenarioCreateResponse,
  ScenarioStartResponse,
  ServerBlastRadius,
  ServerReplayResponse,
  SourcesResponse,
  SystemMode,
  TimelineResponse,
  WhatIfResponse,
} from './types';

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000/api/v1';
const USE_FIXTURES = import.meta.env.VITE_USE_FIXTURES === 'true';

/** Thrown with the server's stable error code so the UI can branch on it. */
export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly correlationId: string | null,
    readonly status: number,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/** Fixture files live under public/fixtures/ so Vite serves them statically. */
const FIXTURE_MAP: Record<string, string> = {
  '/flights': 'flights',
  '/sources': 'sources',
  '/incident-groups': 'incident_groups',
  '/incident-groups/current': 'incident_group_detail',
  '/system/mode': 'system_mode',
  '/health/ready': 'health_ready',
};

function fixtureNameFor(path: string): string | null {
  if (FIXTURE_MAP[path]) return FIXTURE_MAP[path];
  if (/^\/incidents\/[^/]+$/.test(path)) return 'incident_detail';
  if (/^\/incidents\/[^/]+\/timeline$/.test(path)) return 'timeline';
  if (/^\/incidents\/[^/]+\/assurance$/.test(path)) return 'assurance';
  if (/^\/incidents\/[^/]+\/policy$/.test(path)) return 'policy';
  if (/^\/incident-groups\/[^/]+$/.test(path)) return 'incident_group_detail';
  if (/^\/reports\/[^/]+$/.test(path)) return 'report';
  return null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (USE_FIXTURES) {
    const name = fixtureNameFor(path);
    if (!name) {
      throw new ApiError('ENTITY_NOT_FOUND', `No fixture mapped for ${path}`, null, 404);
    }
    const response = await fetch(`/fixtures/${name}.json`);
    if (!response.ok) {
      throw new ApiError('ENTITY_NOT_FOUND', `Fixture ${name}.json missing`, null, 404);
    }
    return (await response.json()) as T;
  }

  const response = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });

  if (!response.ok) {
    let code = 'INTERNAL_ERROR';
    let message = response.statusText;
    let correlationId = response.headers.get('X-Correlation-Id');
    let details: Record<string, unknown> = {};
    try {
      const body = await response.json();
      if (body?.error) {
        code = body.error.code ?? code;
        message = body.error.message ?? message;
        correlationId = body.error.correlation_id ?? correlationId;
        details = body.error.details ?? {};
      }
    } catch {
      // Non-JSON error body; keep the status text.
    }
    throw new ApiError(code, message, correlationId, response.status, details);
  }

  return (await response.json()) as T;
}

export const api = {
  usingFixtures: USE_FIXTURES,

  /**
   * Writes need the live API. There is no fixture for a POST, and synthesising a plausible
   * response would put a state transition on screen that never happened — the one thing an
   * audit-trail product must not do. The UI disables write affordances and says why instead.
   */
  canWrite: !USE_FIXTURES,

  systemMode: () => request<SystemMode>('/system/mode'),
  ready: () => request<ReadyStatus>('/health/ready'),
  flights: () => request<FlightsResponse>('/flights'),
  sources: () => request<SourcesResponse>('/sources'),
  incidentGroups: () => request<IncidentGroupsResponse>('/incident-groups'),

  /** Persist an authored scenario. The key is retained by the caller across safe retries. */
  createScenario: (payload: ScenarioCreateRequest, idempotencyKey: string) =>
    request<ScenarioCreateResponse>('/scenarios', {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify(payload),
    }),

  /** Open the persisted scenario's canonical incidents without advancing their workflows. */
  startScenario: (scenarioReference: string, actorId: string, idempotencyKey: string) =>
    request<ScenarioStartResponse>(`/scenarios/${encodeURIComponent(scenarioReference)}/start`, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
      body: JSON.stringify({ actor_id: actorId }),
    }),

  incident: (id: string) => request<IncidentDetail>(`/incidents/${id}`),
  timeline: (id: string) => request<TimelineResponse>(`/incidents/${id}/timeline`),
  assurance: (id: string) => request<AssuranceResponse>(`/incidents/${id}/assurance`),
  policy: (id: string) => request<PolicyResponse>(`/incidents/${id}/policy`),
  incidentGroup: (id: string) => request<IncidentGroupDetail>(`/incident-groups/${id}`),
  report: (id: string) => request<ReportResponse>(`/reports/${id}`),

  /** Phase 3: natural-language explanation from the Explainer agent. */
  explanation: (incidentId: string) =>
    request<ExplanationResponse>(`/incidents/${incidentId}/explanation`),

  /**
   * Advance the workflow. Stopping at `awaiting_approval` is a SUCCESS response with
   * `is_terminal: false` and a `note` — not an error — so callers must read the body rather
   * than just the status.
   *
   * An `Idempotency-Key` makes a repeat return the recorded result with `replayed: true`
   * instead of taking another step, which is what makes a double-clicked button safe.
   */
  runIncident: (id: string, idempotencyKey?: string) =>
    request<RunResponse>(`/incidents/${id}/run`, {
      method: 'POST',
      headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {},
    }),

  /**
   * Operator approve/reject. `reason` is mandatory server-side (1..2000 chars) and `actor_id`
   * is pseudonymous. A second, matching decision replays; a CONFLICTING one is refused with
   * 409 INVALID_STATE_TRANSITION, because the record is immutable.
   */
  submitDecision: (
    assuranceId: number,
    decision: 'approved' | 'rejected',
    reason: string,
    actorId = 'operator-1',
  ) =>
    request<DecisionResponse>(`/assurance/${assuranceId}/decision`, {
      method: 'POST',
      body: JSON.stringify({ decision, reason, actor_id: actorId }),
    }),

  // ------------------------------------------------------------------ Phase 2: cascade

  /** The most recently opened group. 404 when nothing is open, never an empty placeholder. */
  currentGroup: () => request<IncidentGroupSummary>('/incident-groups/current'),

  blastRadius: (groupRef: string) =>
    request<ServerBlastRadius>(`/incident-groups/${groupRef}/blast-radius`),

  cascadeGraph: (groupRef: string) => request<CascadeGraph>(`/incident-groups/${groupRef}/graph`),

  /**
   * Per-passenger recorded priorities. Read from `passenger_impact`; derives nothing here.
   *
   * `limit` caps the passenger list only. `passengers_assessed` always carries the true total, so a
   * truncated list can never be mistaken for the whole population.
   */
  groupImpacts: (groupRef: string, limit = 100) =>
    request<GroupImpactResponse>(`/incident-groups/${groupRef}/impacts?limit=${limit}`),

  /** Opens one incident per declared member flight. Idempotent: a repeat opens nothing new. */
  openGroup: (groupRef: string, idempotencyKey?: string) =>
    request<GroupRunResponse>(`/incident-groups/${groupRef}/open`, {
      method: 'POST',
      headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {},
    }),

  /**
   * Advance every non-terminal member.
   *
   * A member whose service refuses does not stop the others: the group ends `blocked` naming
   * what did not resolve. One flight without a hotel must not strand the other seven.
   */
  runGroup: (groupRef: string, idempotencyKey?: string) =>
    request<GroupRunResponse>(`/incident-groups/${groupRef}/run`, {
      method: 'POST',
      headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : {},
    }),

  // ---------------------------------------------------------- Phase 2: plan assurance

  /**
   * Group-scoped plan assurance. One request, not client-side fan-out over N incidents: a
   * fan-out makes the group view N+1 requests and lets a partial failure read as a pass.
   */
  groupAssurance: (groupRef: string) =>
    request<GroupAssuranceResponse>(`/incident-groups/${groupRef}/assurance`),

  /**
   * Plan approval. Covers low and medium risk only; high risk always needs its own decision,
   * and no approval ever covers a failed check.
   *
   * The server partitions and returns both lists. The UI must render the excluded set — a
   * reviewer has to see that the control was *unable* to cover something.
   */
  approveGroupPlan: (groupRef: string, reason: string, actorId = 'operator-1') =>
    request<PlanApprovalResponse>(`/incident-groups/${groupRef}/assurance/decision`, {
      method: 'POST',
      body: JSON.stringify({ reason, actor_id: actorId }),
    }),

  // ------------------------------------------------------------ Phase 2: candidate plans

  plans: (incidentId: string) => request<CandidatePlansResponse>(`/incidents/${incidentId}/plans`),

  /** Re-evaluation over the same recorded facts. Writes nothing, projects nothing. */
  planComparison: (incidentId: string) =>
    request<CandidateComparisonResponse>(`/incidents/${incidentId}/plans/comparison`),

  /** Immutable once made: a second, different selection is a 409. */
  selectPlan: (incidentId: string, planId: number, reason: string, actorId = 'operator-1') =>
    request<CandidatePlansResponse>(`/incidents/${incidentId}/plans/${planId}/select`, {
      method: 'POST',
      body: JSON.stringify({ reason, actor_id: actorId }),
    }),

  // --------------------------------------------------------- Phase 2: what-if and replay

  /** Bounded, zero-write, deterministic. Not a simulation engine and not a digital twin. */
  groupWhatIf: (groupRef: string, levers: Record<string, unknown>) =>
    request<WhatIfResponse>(`/incident-groups/${groupRef}/what-if`, {
      method: 'POST',
      body: JSON.stringify(levers),
    }),

  incidentReplay: (incidentId: string) =>
    request<ServerReplayResponse>(`/incidents/${incidentId}/replay`),

  groupReplay: (groupRef: string) =>
    request<ServerReplayResponse>(`/incident-groups/${groupRef}/replay`),

  /** The per-entity impact the services recorded. Without it the UI can only see a sentence. */
  actionDetail: (incidentId: string, actionId: number) =>
    request<ActionDetail>(`/incidents/${incidentId}/actions/${actionId}`),
};

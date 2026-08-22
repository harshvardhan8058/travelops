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
  AssuranceResponse,
  DecisionResponse,
  FlightsResponse,
  GroupReplayResponse,
  GroupRunResponse,
  IncidentDetail,
  IncidentGroupDetail,
  IncidentGroupsResponse,
  PlanApprovalRow,
  PlanAssuranceResponse,
  ReportResponse,
  SourcesResponse,
  PolicyResponse,
  ReadyStatus,
  RunResponse,
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

  incident: (id: string) => request<IncidentDetail>(`/incidents/${id}`),
  timeline: (id: string) => request<TimelineResponse>(`/incidents/${id}/timeline`),
  assurance: (id: string) => request<AssuranceResponse>(`/incidents/${id}/assurance`),
  policy: (id: string) => request<PolicyResponse>(`/incidents/${id}/policy`),
  incidentGroup: (id: string) => request<IncidentGroupDetail>(`/incident-groups/${id}`),

  /**
   * Advance every member incident of a disruption.
   *
   * Approves nothing: each member still passes its own per-flight gate. The group is a scope, not
   * an authorisation.
   */
  runIncidentGroup: (id: string, idempotencyKey?: string) =>
    request<GroupRunResponse>(`/incident-groups/${id}/run`, {
      method: 'POST',
      headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
    }),

  planAssurance: (id: string) =>
    request<PlanAssuranceResponse>(`/incident-groups/${id}/plan-assurance`),

  approvePlan: (id: string, planId: number, body: { reason: string; actor_id?: string }) =>
    request<PlanApprovalRow>(`/incident-groups/${id}/plans/${planId}/approval`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  /** Bounded zero-write re-evaluation. Undeclared levers come back refused by name. */
  whatIf: (id: string, levers: Record<string, unknown>) =>
    request<WhatIfResponse>(`/incident-groups/${id}/what-if`, {
      method: 'POST',
      body: JSON.stringify({ levers }),
    }),

  groupReplay: (id: string) => request<GroupReplayResponse>(`/incident-groups/${id}/replay`),
  report: (id: string) => request<ReportResponse>(`/reports/${id}`),

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
};

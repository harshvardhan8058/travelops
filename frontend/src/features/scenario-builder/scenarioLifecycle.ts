import type {
  FlightRow,
  IncidentGroupDetail,
  IncidentGroupSummary,
  ScenarioCreateRequest,
  ScenarioCreateResponse,
  ScenarioMemberRole,
  ScenarioStartResponse,
} from '@/api/types';
import type { ScenarioDraft } from './scenarioContracts';
import { validateDraft } from './scenarioDraft';

export class ScenarioSubmissionError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = 'ScenarioSubmissionError';
  }
}

export class ScenarioLifecycleFailure extends Error {
  constructor(
    message: string,
    readonly progress: ScenarioLifecycleResult,
    readonly cause: Error,
  ) {
    super(message);
    this.name = 'ScenarioLifecycleFailure';
  }
}

export interface ScenarioLifecycleClient {
  createScenario(
    payload: ScenarioCreateRequest,
    idempotencyKey?: string,
  ): Promise<ScenarioCreateResponse>;
  startScenario(
    scenarioReference: string,
    actorId?: string,
    idempotencyKey?: string,
  ): Promise<ScenarioStartResponse>;
  currentGroup(): Promise<IncidentGroupSummary>;
  incidentGroup(reference: string): Promise<IncidentGroupDetail>;
}

export interface ScenarioLifecycleResult {
  created: ScenarioCreateResponse;
  started: ScenarioStartResponse | null;
  selected: IncidentGroupSummary | null;
  detail: IncidentGroupDetail | null;
  route: '/cascade/current' | null;
}

function effectiveAtUtc(startsAt: string): string {
  const aware = /(?:Z|[+-]\d{2}:\d{2})$/.test(startsAt) ? startsAt : `${startsAt}Z`;
  const parsed = new Date(aware);
  if (Number.isNaN(parsed.getTime())) {
    throw new ScenarioSubmissionError('START_UNPARSEABLE', 'The scenario start time is invalid.');
  }
  return parsed.toISOString();
}

function roleFor(flight: FlightRow, draft: ScenarioDraft): ScenarioMemberRole {
  const airport = draft.airportIcao.trim().toUpperCase();
  if (flight.flight_number === draft.primaryFlight) {
    if (flight.origin_icao !== airport) {
      throw new ScenarioSubmissionError(
        'PRIMARY_ROUTE_MISMATCH',
        `${flight.flight_number} cannot be primary because it does not depart ${airport}.`,
        { flight_number: flight.flight_number, airport_icao: airport },
      );
    }
    return 'primary';
  }
  if (flight.origin_icao === airport) return 'affected_departure';
  if (flight.destination_icao === airport) return 'affected_arrival';
  throw new ScenarioSubmissionError(
    'MEMBER_ROUTE_MISMATCH',
    `${flight.flight_number} neither departs nor arrives at ${airport}.`,
    { flight_number: flight.flight_number, airport_icao: airport },
  );
}

/** Resolve operator-entered designators against recorded flight IDs and delays. */
export function buildPublishedScenarioRequest(
  draft: ScenarioDraft,
  flights: readonly FlightRow[],
  actorId = 'operator-1',
): ScenarioCreateRequest {
  const report = validateDraft(draft);
  if (!report.ok) {
    throw new ScenarioSubmissionError(
      'DRAFT_INVALID',
      'Fix the validation errors before creating.',
      {
        issues: report.errors.map((issue) => ({ field: issue.field, code: issue.code })),
      },
    );
  }

  const byNumber = new Map(flights.map((flight) => [flight.flight_number, flight]));
  const missing = draft.flightNumbers.filter((number) => !byNumber.has(number));
  if (missing.length > 0) {
    throw new ScenarioSubmissionError(
      'FLIGHTS_NOT_FOUND',
      `The current flight board does not contain: ${missing.join(', ')}.`,
      { flight_numbers: missing },
    );
  }

  return {
    root_cause: draft.disruptionType,
    airport_icao: draft.airportIcao.trim().toUpperCase(),
    severity: draft.severity,
    effective_at: effectiveAtUtc(draft.startsAt),
    actor_id: actorId,
    members: draft.flightNumbers.map((number) => {
      const flight = byNumber.get(number)!;
      return {
        flight_id: flight.id,
        role: roleFor(flight, draft),
        delay_minutes: flight.delay_minutes,
      };
    }),
  };
}

/**
 * Execute the published create/start lifecycle and prove `/cascade/current` resolves to its detail.
 * Navigation is returned only after the current selector names the created scenario and the dynamic
 * detail endpoint has supplied the arrays required by the cascade screen.
 */
export async function submitScenario(
  client: ScenarioLifecycleClient,
  draft: ScenarioDraft,
  flights: readonly FlightRow[],
  options: { runAfterCreate: boolean; actorId?: string; operationKey: string },
): Promise<ScenarioLifecycleResult> {
  const actorId = options.actorId ?? 'operator-1';
  return runScenarioLifecycle(client, buildPublishedScenarioRequest(draft, flights, actorId), {
    ...options,
    actorId,
  });
}

/**
 * The lifecycle itself, over an already-built request.
 *
 * Split out of `submitScenario` so a caller that did not author a draft can run the SAME path. The
 * Scenario Center starts a catalogued simulation, whose payload comes from `GET /demo/simulations`
 * fully formed — there is no draft to validate and no designator to resolve, because the backend
 * already resolved the selection against recorded rows. Reimplementing create-then-start-then-verify
 * for that caller would be the "second lifecycle" this codebase exists not to have, and the two
 * copies would drift on precisely the `/incident-groups/current` confirmation below, which is the
 * step that makes navigation honest.
 *
 * `submitScenario` keeps its signature, so the Scenario Builder is unchanged.
 */
export async function runScenarioLifecycle(
  client: ScenarioLifecycleClient,
  payload: ScenarioCreateRequest,
  options: { runAfterCreate: boolean; actorId?: string; operationKey: string },
): Promise<ScenarioLifecycleResult> {
  const actorId = options.actorId ?? 'operator-1';
  const created = await client.createScenario(payload, `${options.operationKey}-create`);
  if (!options.runAfterCreate) {
    return { created, started: null, selected: null, detail: null, route: null };
  }

  let started: ScenarioStartResponse | null = null;
  let selected: IncidentGroupSummary | null = null;
  let detail: IncidentGroupDetail | null = null;
  try {
    started = await client.startScenario(
      created.scenario_reference,
      actorId,
      `${options.operationKey}-start`,
    );
    selected = await client.currentGroup();
    if (selected.reference !== created.scenario_reference) {
      throw new ScenarioSubmissionError(
        'CURRENT_GROUP_MISMATCH',
        `Scenario ${created.scenario_reference} started, but /incident-groups/current selected ${selected.reference}.`,
        {
          scenario_reference: created.scenario_reference,
          current_reference: selected.reference,
        },
      );
    }
    detail = await client.incidentGroup(selected.reference);
    return { created, started, selected, detail, route: '/cascade/current' };
  } catch (error) {
    const cause = error instanceof Error ? error : new Error('Scenario lifecycle did not finish.');
    throw new ScenarioLifecycleFailure(
      `${created.scenario_reference} was created${started ? ' and started' : ''}, but the next step failed: ${cause.message}`,
      { created, started, selected, detail, route: null },
      cause,
    );
  }
}

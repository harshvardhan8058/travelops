/**
 * Scenario draft state, validation and preview. Pure, dependency-free, unit-testable.
 *
 * Every decision the builder makes lives here rather than in the component, for the reason the
 * replay and cascade features already follow: `vitest.config.ts` runs in a node environment and
 * collects `*.test.ts` only, so logic inside a `.tsx` component is logic no test can reach. A wizard
 * that silently accepts a malformed flight designator or lets an invalid draft reach the create step
 * is exactly the failure worth covering.
 *
 * Three rules this module holds:
 *
 *   1. **Nothing is computed that only the engine can know.** No passenger counts, no connection
 *      counts, no entitlement figures. The preview reports what the operator declared and the length
 *      of arrays the operator typed — the one aggregate the UI is permitted to compute.
 *   2. **An invalid draft cannot produce a request.** `prepareScenarioRequest` refuses rather than
 *      emitting a payload the backend would reject, so the review step cannot show a body that could
 *      never be sent.
 *   3. **Errors name the field.** A validation message that does not say which input is wrong is a
 *      message an operator has to guess at.
 *
 * Owner: Stream D.
 */

import type {
  FlightRow,
  ScenarioCreateRequest,
  ScenarioCreateResponse,
  ScenarioStartResponse,
  TriggerType,
} from '@/api/types';
import {
  DISRUPTION_TYPES,
  SCENARIO_SEVERITIES,
  findTemplate,
  type DisruptionType,
  type ScenarioDraft,
  type ScenarioSeverity,
  type ScenarioTemplate,
} from './scenarioContracts';

// ---------------------------------------------------------------- steps

export type ScenarioStepId = 'template' | 'details' | 'review';

export const SCENARIO_STEPS: readonly { id: ScenarioStepId; label: string }[] = [
  { id: 'template', label: 'Template' },
  { id: 'details', label: 'Disruption details' },
  { id: 'review', label: 'Validate & preview' },
] as const;

/**
 * `blocked` is distinct from `todo` on purpose: a step an operator cannot reach yet and a step that
 * is reachable but failing validation are different situations, and collapsing them into one greyed
 * row is how a wizard ends up unexplainable.
 */
export type ScenarioStepState = 'done' | 'current' | 'todo' | 'blocked';

// ---------------------------------------------------------------- validation

export type ValidationSeverity = 'error' | 'warning';

export interface ValidationIssue {
  field: keyof ScenarioDraft | 'draft';
  code: string;
  message: string;
  severity: ValidationSeverity;
}

export interface ValidationReport {
  issues: ValidationIssue[];
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
  /** True only when there is no error. Warnings never block. */
  ok: boolean;
}

/** ICAO location indicators are four letters. Anything else is not an aerodrome. */
const ICAO = /^[A-Z]{4}$/;

/**
 * IATA-style designator: a two-character airline code then one to four digits.
 *
 * Accepts `6E2134` and `6E 2134` and normalises to the spaced form, because an operator typing from
 * a strip will produce both and rejecting one of them is friction with no safety value.
 *
 * The airline code must contain a letter. Written as `[A-Z0-9]{2}` first, this accepted a bare
 * `2134` — the two-character class took `21`, the digit group took `34`, and the console reported a
 * flight called `21 34` that no airline operates. A designator invented by a regex is worse than a
 * rejected one, because it reaches the review step looking legitimate.
 */
const FLIGHT_DESIGNATOR = /^([A-Z][A-Z0-9]|[0-9][A-Z])\s?(\d{1,4})$/;

export const MIN_DURATION_MINUTES = 15;
export const MAX_DURATION_MINUTES = 1440;
export const MAX_NOTES_LENGTH = 280;
/** Above this the scenario is large enough to be worth a second look, never rejected. */
export const LARGE_SCENARIO_FLIGHTS = 8;

/** Upper-cases and collapses the optional space, or returns null when it is not a designator. */
export function normaliseFlightNumber(raw: string): string | null {
  const match = FLIGHT_DESIGNATOR.exec(raw.trim().toUpperCase());
  if (!match) return null;
  return `${match[1]} ${match[2]}`;
}

/**
 * Splits an operator's free-text flight list on commas, spaces having meaning inside a designator.
 *
 * Returns both the accepted designators and the rejected fragments, because dropping what could not
 * be parsed would let a typo silently shrink the scenario.
 */
export function parseFlightList(raw: string): { flights: string[]; rejected: string[] } {
  const fragments = raw
    .split(',')
    .map((part) => part.trim())
    .filter((part) => part !== '');

  const flights: string[] = [];
  const rejected: string[] = [];
  for (const fragment of fragments) {
    const normalised = normaliseFlightNumber(fragment);
    if (normalised === null) {
      rejected.push(fragment);
      continue;
    }
    if (!flights.includes(normalised)) flights.push(normalised);
  }
  return { flights, rejected };
}

function isFiniteInteger(value: number): boolean {
  return Number.isFinite(value) && Number.isInteger(value);
}

/**
 * Validates a draft against the shape the backend would require.
 *
 * Reports every issue rather than the first, so an operator fixes a form once instead of discovering
 * problems one reload at a time.
 */
export function validateDraft(draft: ScenarioDraft): ValidationReport {
  const issues: ValidationIssue[] = [];
  const add = (
    field: ValidationIssue['field'],
    code: string,
    message: string,
    severity: ValidationSeverity = 'error',
  ) => issues.push({ field, code, message, severity });

  if (draft.templateId === null) {
    add('templateId', 'TEMPLATE_NOT_CHOSEN', 'Choose a template before entering details.');
  } else if (findTemplate(draft.templateId) === null) {
    add(
      'templateId',
      'TEMPLATE_UNKNOWN',
      `No template is declared with id ${draft.templateId}, so its defaults cannot be resolved.`,
    );
  }

  if (draft.name.trim() === '') {
    add('name', 'NAME_REQUIRED', 'Give this local draft a name for the review step.');
  }

  if (!DISRUPTION_TYPES.includes(draft.disruptionType)) {
    add(
      'disruptionType',
      'DISRUPTION_TYPE_UNKNOWN',
      `${draft.disruptionType} is not one of the disruption types the generator understands.`,
    );
  }

  const airport = draft.airportIcao.trim().toUpperCase();
  if (airport === '') {
    add('airportIcao', 'AIRPORT_REQUIRED', 'Name the airport the disruption starts at.');
  } else if (!ICAO.test(airport)) {
    add(
      'airportIcao',
      'AIRPORT_NOT_ICAO',
      `${draft.airportIcao} is not a four-letter ICAO indicator, e.g. VOBL.`,
    );
  }

  if (draft.startsAt.trim() === '') {
    add('startsAt', 'START_REQUIRED', 'Set when the disruption starts.');
  } else if (Number.isNaN(Date.parse(draft.startsAt))) {
    add('startsAt', 'START_UNPARSEABLE', `${draft.startsAt} is not a date and time this can read.`);
  }

  if (!isFiniteInteger(draft.durationMinutes)) {
    add('durationMinutes', 'DURATION_NOT_A_NUMBER', 'Duration must be a whole number of minutes.');
  } else if (
    draft.durationMinutes < MIN_DURATION_MINUTES ||
    draft.durationMinutes > MAX_DURATION_MINUTES
  ) {
    add(
      'durationMinutes',
      'DURATION_OUT_OF_RANGE',
      `Duration must be between ${MIN_DURATION_MINUTES} and ${MAX_DURATION_MINUTES} minutes.`,
    );
  } else if (draft.durationMinutes > MAX_DURATION_MINUTES / 2) {
    add(
      'durationMinutes',
      'DURATION_LONG',
      'Over twelve hours. The cascade will reach a second operating day, which is worth intending.',
      'warning',
    );
  }

  if (!SCENARIO_SEVERITIES.includes(draft.severity)) {
    add('severity', 'SEVERITY_UNKNOWN', `${draft.severity} is not one of the four severity bands.`);
  }

  if (draft.flightNumbers.length === 0) {
    add('flightNumbers', 'FLIGHTS_REQUIRED', 'Name at least one flight the disruption affects.');
  } else {
    const malformed = draft.flightNumbers.filter(
      (flight) => normaliseFlightNumber(flight) === null,
    );
    if (malformed.length > 0) {
      add(
        'flightNumbers',
        'FLIGHT_MALFORMED',
        `Not a flight designator: ${malformed.join(', ')}. Expected a form like 6E 2134.`,
      );
    }
    const seen = new Set<string>();
    const duplicates = new Set<string>();
    for (const flight of draft.flightNumbers) {
      if (seen.has(flight)) duplicates.add(flight);
      seen.add(flight);
    }
    if (duplicates.size > 0) {
      add(
        'flightNumbers',
        'FLIGHT_DUPLICATED',
        `Listed more than once: ${[...duplicates].join(', ')}.`,
      );
    }
    if (draft.flightNumbers.length > LARGE_SCENARIO_FLIGHTS) {
      add(
        'flightNumbers',
        'SCENARIO_LARGE',
        `${draft.flightNumbers.length} flights is a large scenario; every one opens its own incident and its own gate.`,
        'warning',
      );
    }
  }

  if (draft.primaryFlight.trim() === '') {
    add('primaryFlight', 'PRIMARY_REQUIRED', 'Choose which flight is the primary disruption.');
  } else if (!draft.flightNumbers.includes(draft.primaryFlight)) {
    add(
      'primaryFlight',
      'PRIMARY_NOT_LISTED',
      `${draft.primaryFlight} is the primary flight but is not in the affected list.`,
    );
  }

  if (draft.notes.length > MAX_NOTES_LENGTH) {
    add(
      'notes',
      'NOTES_TOO_LONG',
      `Notes are ${draft.notes.length} characters; the limit is ${MAX_NOTES_LENGTH}.`,
    );
  }

  if (draft.severity === 'critical' && draft.notes.trim() === '') {
    add(
      'notes',
      'CRITICAL_WITHOUT_NOTE',
      'Critical severity with no draft note leaves the local review context unexplained.',
      'warning',
    );
  }

  const errors = issues.filter((issue) => issue.severity === 'error');
  const warnings = issues.filter((issue) => issue.severity === 'warning');
  return { issues, errors, warnings, ok: errors.length === 0 };
}

/** Issues for one field, so an input can render its own problem beside itself. */
export function issuesForField(
  report: ValidationReport,
  field: ValidationIssue['field'],
): ValidationIssue[] {
  return report.issues.filter((issue) => issue.field === field);
}

// ---------------------------------------------------------------- drafts

/** An empty draft. Deliberately not valid: a wizard that starts valid teaches nobody anything. */
export function emptyDraft(): ScenarioDraft {
  return {
    templateId: null,
    name: '',
    disruptionType: 'weather',
    airportIcao: '',
    startsAt: '',
    durationMinutes: 120,
    severity: 'medium',
    flightNumbers: [],
    primaryFlight: '',
    notes: '',
  };
}

/**
 * Applies a template over a draft.
 *
 * The operator's notes survive, because they are the one field a template has no opinion about and
 * silently discarding typed prose when a template is re-picked is the kind of small betrayal that
 * makes people distrust a form.
 */
export function applyTemplate(draft: ScenarioDraft, template: ScenarioTemplate): ScenarioDraft {
  return {
    ...draft,
    templateId: template.id,
    name: template.name,
    disruptionType: template.disruptionType,
    airportIcao: template.airportIcao,
    severity: template.severity,
    durationMinutes: template.durationMinutes,
    flightNumbers: [...template.flightNumbers],
    primaryFlight: template.flightNumbers[0] ?? '',
    notes: draft.notes,
  };
}

/**
 * Replaces the flight list, keeping the primary consistent.
 *
 * When the primary is removed the first remaining flight takes over rather than the field going
 * blank, so the draft cannot pass through a state where a primary exists but names nothing.
 */
export function setFlightNumbers(draft: ScenarioDraft, flights: string[]): ScenarioDraft {
  const primary = flights.includes(draft.primaryFlight) ? draft.primaryFlight : (flights[0] ?? '');
  return { ...draft, flightNumbers: flights, primaryFlight: primary };
}

// ---------------------------------------------------------------- preview

export interface ScenarioPreview {
  name: string;
  disruptionType: DisruptionType;
  airportIcao: string;
  severity: ScenarioSeverity;
  startsAt: string;
  /** Derived by adding the declared duration to the declared start. Not a prediction. */
  endsAt: string | null;
  durationMinutes: number;
  /** Length of the list the operator typed. The only aggregate computed here. */
  affectedFlightCount: number;
  affectedFlights: string[];
  primaryFlight: string;
  /** Flights that are not the primary, in the order given. */
  downstreamFlights: string[];
  notes: string;
  templateName: string | null;
  /** What the engine will decide rather than the console. Rendered as an absence, never as zero. */
  computedByEngine: readonly string[];
}

/**
 * Figures this console will not produce, named so the preview can say so out loud.
 *
 * Every one of these exists in the real contract and is computed from the seeded dataset. A builder
 * that guessed them would put four numbers on a review screen that no record supports.
 */
export const ENGINE_COMPUTED_FIGURES = [
  'passengers affected',
  'connections at risk',
  'crew pairings affected',
  'candidate hotels',
] as const;

export function buildPreview(draft: ScenarioDraft): ScenarioPreview {
  const template = findTemplate(draft.templateId);
  const startMs = Date.parse(draft.startsAt);
  const endsAt =
    Number.isNaN(startMs) || !isFiniteInteger(draft.durationMinutes)
      ? null
      : new Date(startMs + draft.durationMinutes * 60_000).toISOString();

  return {
    name: draft.name.trim(),
    disruptionType: draft.disruptionType,
    airportIcao: draft.airportIcao.trim().toUpperCase(),
    severity: draft.severity,
    startsAt: draft.startsAt,
    endsAt,
    durationMinutes: draft.durationMinutes,
    affectedFlightCount: draft.flightNumbers.length,
    affectedFlights: [...draft.flightNumbers],
    primaryFlight: draft.primaryFlight,
    downstreamFlights: draft.flightNumbers.filter((flight) => flight !== draft.primaryFlight),
    notes: draft.notes.trim(),
    templateName: template?.name ?? null,
    computedByEngine: ENGINE_COMPUTED_FIGURES,
  };
}

// ---------------------------------------------------------------- step state

/**
 * Which steps are done, current, reachable or blocked.
 *
 * Derived from the draft rather than tracked in state, so a step cannot report itself complete after
 * the operator goes back and empties a field it depended on.
 */
export function stepStates(
  draft: ScenarioDraft,
  current: ScenarioStepId,
  report: ValidationReport,
): { id: ScenarioStepId; label: string; state: ScenarioStepState }[] {
  const templateChosen = findTemplate(draft.templateId) !== null;
  const detailErrors = report.errors.some((issue) => issue.field !== 'templateId');

  return SCENARIO_STEPS.map(({ id, label }) => {
    if (id === current) return { id, label, state: 'current' as ScenarioStepState };
    if (id === 'template') {
      return { id, label, state: templateChosen ? 'done' : 'blocked' };
    }
    if (id === 'details') {
      if (!templateChosen) return { id, label, state: 'todo' };
      return { id, label, state: detailErrors ? 'blocked' : 'done' };
    }
    // review
    if (!templateChosen) return { id, label, state: 'todo' };
    return { id, label, state: report.ok ? 'done' : 'todo' };
  });
}

/** Whether a step can be opened. The review step stays shut until a template exists. */
export function canOpenStep(draft: ScenarioDraft, step: ScenarioStepId): boolean {
  if (step === 'template') return true;
  return findTemplate(draft.templateId) !== null;
}

// ---------------------------------------------------------------- real API adapter and lifecycle

const TRIGGER_BY_DISRUPTION: Record<DisruptionType, TriggerType> = {
  weather: 'weather',
  crew: 'crew_rostering',
  technical: 'technical',
  airport_closure: 'other',
};

function effectiveAtUtc(value: string): string {
  const includesZone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value);
  const instant = new Date(includesZone ? value : `${value}Z`);
  return instant.toISOString();
}

export type ScenarioRequestOutcome =
  { request: ScenarioCreateRequest } | { refused: ValidationReport };

export const DEMO_SCENARIO_ACTOR_ID = 'operator-1';

/** Resolve operator-entered designators to the exact flight-board rows the API publishes. */
export function buildCreateRequest(
  draft: ScenarioDraft,
  flights: readonly FlightRow[],
  actorId = DEMO_SCENARIO_ACTOR_ID,
): ScenarioRequestOutcome {
  const report = validateDraft(draft);
  if (!report.ok) return { refused: report };

  const issues: ValidationIssue[] = [];
  const members: ScenarioCreateRequest['members'] = [];
  const airport = draft.airportIcao.trim().toUpperCase();

  for (const flightNumber of draft.flightNumbers) {
    const matches = flights.filter((flight) => flight.flight_number === flightNumber);
    if (matches.length === 0) {
      issues.push({
        field: 'flightNumbers',
        code: 'FLIGHT_NOT_FOUND',
        message: `${flightNumber} is not in the current flight dataset.`,
        severity: 'error',
      });
      continue;
    }
    if (matches.length > 1) {
      issues.push({
        field: 'flightNumbers',
        code: 'FLIGHT_AMBIGUOUS',
        message: `${flightNumber} resolves to more than one current flight.`,
        severity: 'error',
      });
      continue;
    }

    const flight = matches[0]!;
    const departsRoot = flight.origin_icao === airport;
    const arrivesRoot = flight.destination_icao === airport;
    if (!departsRoot && !arrivesRoot) {
      issues.push({
        field: 'flightNumbers',
        code: 'FLIGHT_OUTSIDE_ROOT_AIRPORT',
        message: `${flightNumber} neither departs from nor arrives at ${airport}.`,
        severity: 'error',
      });
      continue;
    }

    if (flightNumber === draft.primaryFlight && !departsRoot) {
      issues.push({
        field: 'primaryFlight',
        code: 'PRIMARY_NOT_DEPARTING_ROOT',
        message: `${flightNumber} is primary but does not depart from ${airport}.`,
        severity: 'error',
      });
      continue;
    }

    members.push({
      flight_id: flight.id,
      role:
        flightNumber === draft.primaryFlight
          ? 'primary'
          : departsRoot
            ? 'affected_departure'
            : 'affected_arrival',
      delay_minutes: flight.delay_minutes,
    });
  }

  if (issues.length > 0) {
    return {
      refused: {
        issues: [...report.issues, ...issues],
        errors: [...report.errors, ...issues],
        warnings: report.warnings,
        ok: false,
      },
    };
  }

  return {
    request: {
      root_cause: TRIGGER_BY_DISRUPTION[draft.disruptionType],
      airport_icao: airport,
      severity: draft.severity,
      effective_at: effectiveAtUtc(draft.startsAt),
      actor_id: actorId,
      members,
    },
  };
}

export interface ScenarioIdempotencyKeys {
  create: string;
  start: string;
}

/** Generated once per unchanged draft and retained by the component across retries. */
export function createScenarioIdempotencyKeys(randomId: () => string): ScenarioIdempotencyKeys {
  return {
    create: `scenario-create-${randomId()}`,
    start: `scenario-start-${randomId()}`,
  };
}

export interface ScenarioApiPort {
  createScenario: (
    request: ScenarioCreateRequest,
    idempotencyKey: string,
  ) => Promise<ScenarioCreateResponse>;
  startScenario: (
    scenarioReference: string,
    actorId: string,
    idempotencyKey: string,
  ) => Promise<ScenarioStartResponse>;
}

/** Total currently associated incidents, distinct from those newly opened by this one request. */
export function startedMemberIncidentCount(response: ScenarioStartResponse): number {
  return response.members.filter((member) => member.incident_reference !== null).length;
}

export type ScenarioSubmissionResult =
  | {
      ok: true;
      created: ScenarioCreateResponse;
      started: ScenarioStartResponse | null;
      navigateTo: string | null;
    }
  | {
      ok: false;
      stage: 'create' | 'start';
      error: unknown;
      created: ScenarioCreateResponse | null;
    };

/**
 * Execute create and optional start in order. A start failure keeps the real create response so a
 * retry never creates a second scenario and never hides the `SCN-*` that already exists.
 */
export async function submitScenario(
  request: ScenarioCreateRequest,
  runAfterCreate: boolean,
  keys: ScenarioIdempotencyKeys,
  apiPort: ScenarioApiPort,
  existingCreate: ScenarioCreateResponse | null = null,
): Promise<ScenarioSubmissionResult> {
  let created = existingCreate;
  if (!created) {
    try {
      created = await apiPort.createScenario(request, keys.create);
    } catch (error) {
      return { ok: false, stage: 'create', error, created: null };
    }
  }

  if (!runAfterCreate) {
    return { ok: true, created, started: null, navigateTo: null };
  }

  try {
    const started = await apiPort.startScenario(
      created.scenario_reference,
      request.actor_id,
      keys.start,
    );
    return {
      ok: true,
      created,
      started,
      navigateTo: `/cascade/${encodeURIComponent(started.scenario_reference)}`,
    };
  } catch (error) {
    return { ok: false, stage: 'start', error, created };
  }
}

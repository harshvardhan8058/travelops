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

import {
  DISRUPTION_TYPES,
  SCENARIO_CREATE_ENDPOINT,
  SCENARIO_SEVERITIES,
  findTemplate,
  type DisruptionType,
  type ScenarioCreateRequest,
  type ScenarioDraft,
  type ScenarioRequestReceipt,
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
    add('name', 'NAME_REQUIRED', 'Give the scenario a name so it can be told apart in a replay.');
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
      'Critical severity with no note leaves a replay unable to say why it was critical.',
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

// ---------------------------------------------------------------- request

/**
 * A stable id for a prepared request: FNV-1a over the canonical payload.
 *
 * Deterministic on purpose. A random id would change on every render and could not be matched
 * against a payload an operator copied out five minutes earlier, and the house rule for replayable
 * surfaces is that identical inputs produce identical output.
 */
export function stableRequestId(payload: ScenarioCreateRequest): string {
  const canonical = JSON.stringify([
    payload.name,
    payload.disruption_type,
    payload.airport_icao,
    payload.starts_at,
    payload.duration_minutes,
    payload.severity,
    payload.flight_numbers,
    payload.primary_flight,
    payload.notes,
    payload.template_id,
    payload.run_after_create,
  ]);

  let hash = 0x811c9dc5;
  for (let index = 0; index < canonical.length; index += 1) {
    hash ^= canonical.charCodeAt(index);
    // FNV prime, applied with >>> 0 so this stays an unsigned 32-bit value in JavaScript.
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `scn-${hash.toString(16).padStart(8, '0')}`;
}

export function buildCreateRequest(
  draft: ScenarioDraft,
  options: { runAfterCreate: boolean },
): ScenarioCreateRequest {
  return {
    name: draft.name.trim(),
    disruption_type: draft.disruptionType,
    airport_icao: draft.airportIcao.trim().toUpperCase(),
    starts_at: draft.startsAt,
    duration_minutes: draft.durationMinutes,
    severity: draft.severity,
    flight_numbers: [...draft.flightNumbers],
    primary_flight: draft.primaryFlight,
    notes: draft.notes.trim(),
    template_id: draft.templateId,
    run_after_create: options.runAfterCreate,
  };
}

/**
 * The command that reproduces this draft today, or null when there is not one.
 *
 * Offered only for a draft still matching a template the repository actually seeds, and only when
 * the operator has not edited the parts the seed fixes. An "equivalent" command that produced a
 * different disruption would be worse than no command at all.
 */
export function equivalentCommandFor(
  draft: ScenarioDraft,
  options: { runAfterCreate: boolean },
): string | null {
  const template = findTemplate(draft.templateId);
  if (!template?.seedScenarioId) return null;

  const unchanged =
    draft.airportIcao.trim().toUpperCase() === template.airportIcao &&
    draft.disruptionType === template.disruptionType &&
    draft.severity === template.severity &&
    draft.durationMinutes === template.durationMinutes &&
    draft.flightNumbers.length === template.flightNumbers.length &&
    draft.flightNumbers.every((flight, index) => flight === template.flightNumbers[index]);

  if (!unchanged) return null;

  const cascade = options.runAfterCreate ? ' --cascade' : '';
  return `python -m app.cli inject --scenario ${template.seedScenarioId}${cascade}`;
}

export const UNSUBMITTED_REASON =
  'Prepared in the console and not sent. Scenario authoring has no endpoint yet, and inventing a created scenario would put a state change on screen that never happened.';

/**
 * Builds the request, or refuses because the draft is invalid.
 *
 * Refusing here rather than in the component is what stops the review step rendering a payload the
 * backend would reject. `now` is a parameter so the receipt is deterministic under test.
 */
export function prepareScenarioRequest(
  draft: ScenarioDraft,
  options: { runAfterCreate: boolean; now: Date },
): { receipt: ScenarioRequestReceipt } | { refused: ValidationReport } {
  const report = validateDraft(draft);
  if (!report.ok) return { refused: report };

  const payload = buildCreateRequest(draft, { runAfterCreate: options.runAfterCreate });
  return {
    receipt: {
      requestId: stableRequestId(payload),
      preparedAt: options.now.toISOString(),
      targetEndpoint: SCENARIO_CREATE_ENDPOINT,
      payload,
      submitted: false,
      unsubmittedReason: UNSUBMITTED_REASON,
      equivalentCommand: equivalentCommandFor(draft, {
        runAfterCreate: options.runAfterCreate,
      }),
    },
  };
}

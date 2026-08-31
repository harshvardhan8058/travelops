import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import type { FlightRow, ScenarioCreateResponse, ScenarioStartResponse } from '@/api/types';
import { SCENARIO_TEMPLATES, findTemplate, type ScenarioDraft } from './scenarioContracts';
import {
  LARGE_SCENARIO_FLIGHTS,
  MAX_DURATION_MINUTES,
  MAX_NOTES_LENGTH,
  MIN_DURATION_MINUTES,
  SCENARIO_STEPS,
  applyTemplate,
  buildCreateRequest,
  buildPreview,
  canOpenStep,
  createScenarioIdempotencyKeys,
  emptyDraft,
  issuesForField,
  normaliseFlightNumber,
  parseFlightList,
  setFlightNumbers,
  startedMemberIncidentCount,
  stepStates,
  submitScenario,
  validateDraft,
} from './scenarioDraft';

/** A draft that passes validation, so each test can break exactly one thing. */
function validDraft(overrides: Partial<ScenarioDraft> = {}): ScenarioDraft {
  return {
    templateId: 'bengaluru-monsoon-storm',
    name: 'Evening storm at Bengaluru',
    disruptionType: 'weather',
    airportIcao: 'VOBL',
    startsAt: '2026-08-20T15:36',
    durationMinutes: 180,
    severity: 'high',
    flightNumbers: ['6E 2134', '6E 811'],
    primaryFlight: '6E 2134',
    notes: 'Runways closed for the evening peak.',
    ...overrides,
  };
}

const FLIGHTS: FlightRow[] = [
  {
    id: 1,
    flight_number: '6E 2134',
    airline_code: '6E',
    origin_icao: 'VOBL',
    destination_icao: 'VIDP',
    scheduled_departure: '2026-08-20T15:40:00Z',
    estimated_departure: null,
    delay_minutes: 420,
    block_time_minutes: 150,
    status: 'delayed',
    risk_index: 80,
    risk_level: 'high',
    passengers: 180,
    connections_at_risk: 12,
    incident_reference: null,
    provenance: { kind: 'simulated', provider: 'seed' },
  },
  {
    id: 2,
    flight_number: '6E 811',
    airline_code: '6E',
    origin_icao: 'VIDP',
    destination_icao: 'VOBL',
    scheduled_departure: '2026-08-20T16:40:00Z',
    estimated_departure: null,
    delay_minutes: 180,
    block_time_minutes: 150,
    status: 'delayed',
    risk_index: 60,
    risk_level: 'elevated',
    passengers: 160,
    connections_at_risk: 8,
    incident_reference: null,
    provenance: { kind: 'simulated', provider: 'seed' },
  },
];

const CREATED: ScenarioCreateResponse = {
  scenario_reference: 'SCN-20260820-001',
  state: 'detected',
  root_cause: 'weather',
  airport_icao: 'VOBL',
  severity: 'high',
  effective_at: '2026-08-20T15:36:00Z',
  members: [
    { flight_id: 1, flight_number: '6E 2134', role: 'primary', delay_minutes: 420 },
    {
      flight_id: 2,
      flight_number: '6E 811',
      role: 'affected_arrival',
      delay_minutes: 180,
    },
  ],
  created_by: 'operator-1',
  created_at: '2026-08-20T12:00:00Z',
  provenance: { kind: 'simulated', provider: 'scenario-builder' },
  replayed: false,
};

const STARTED: ScenarioStartResponse = {
  scenario_reference: CREATED.scenario_reference,
  state: 'detected',
  members: [],
  opened_incident_ids: [10, 11],
  blocked_reason: null,
  awaiting_approval_count: 0,
  started_by: 'operator-1',
  started_at: '2026-08-20T12:01:00Z',
  provenance: CREATED.provenance,
  replayed: false,
};

describe('normaliseFlightNumber', () => {
  it('accepts the spaced and unspaced forms an operator actually types', () => {
    expect(normaliseFlightNumber('6E 2134')).toBe('6E 2134');
    expect(normaliseFlightNumber('6E2134')).toBe('6E 2134');
    expect(normaliseFlightNumber('  ai503 ')).toBe('AI 503');
    expect(normaliseFlightNumber('uk705')).toBe('UK 705');
  });

  it('refuses anything that is not a designator rather than guessing', () => {
    // A full airline name is spelled out rather than abbreviated here on purpose: `check-tokens.mjs`
    // bans four hue words anywhere in src/, and one major carrier's name is one of them.
    for (const raw of ['', '6E', 'AIRLINE 2134', '6E 21345', '6E-2134', '⚠ 2134']) {
      expect(normaliseFlightNumber(raw)).toBeNull();
    }
  });

  it('refuses a bare number, which an earlier regex split into a flight nobody operates', () => {
    /*
     * `[A-Z0-9]{2}` for the airline code matched the first two digits of `2134` and left `34` for the
     * number, so the console accepted a flight called "21 34". An airline designator carries a
     * letter, and a designator invented by a regex reaches the review step looking legitimate.
     */
    expect(normaliseFlightNumber('2134')).toBeNull();
    expect(normaliseFlightNumber('21 34')).toBeNull();
    expect(normaliseFlightNumber('123456')).toBeNull();
    // Codes with a digit in either position are real, and must still be accepted.
    expect(normaliseFlightNumber('6E2134')).toBe('6E 2134');
    expect(normaliseFlightNumber('9W101')).toBe('9W 101');
  });
});

describe('parseFlightList', () => {
  it('splits on commas, normalises, and de-duplicates', () => {
    const { flights, rejected } = parseFlightList('6E2134, 6e 2134 , ai503');
    expect(flights).toEqual(['6E 2134', 'AI 503']);
    expect(rejected).toEqual([]);
  });

  it('names what it could not read instead of silently shrinking the scenario', () => {
    // A typo that vanished would let an operator authorise fewer flights than they meant to.
    const { flights, rejected } = parseFlightList('6E 2134, notaflight, UK 705');
    expect(flights).toEqual(['6E 2134', 'UK 705']);
    expect(rejected).toEqual(['notaflight']);
  });

  it('treats an empty list as empty rather than as one blank entry', () => {
    expect(parseFlightList('   ')).toEqual({ flights: [], rejected: [] });
    expect(parseFlightList(',,')).toEqual({ flights: [], rejected: [] });
  });
});

describe('validateDraft', () => {
  it('passes a complete draft and reports nothing', () => {
    const report = validateDraft(validDraft());
    expect(report.ok).toBe(true);
    expect(report.errors).toEqual([]);
    expect(report.warnings).toEqual([]);
  });

  it('reports every problem at once, not just the first', () => {
    const report = validateDraft(
      validDraft({ name: '', airportIcao: '', flightNumbers: [], primaryFlight: '' }),
    );
    expect(report.ok).toBe(false);
    const codes = report.errors.map((issue) => issue.code);
    expect(codes).toContain('NAME_REQUIRED');
    expect(codes).toContain('AIRPORT_REQUIRED');
    expect(codes).toContain('FLIGHTS_REQUIRED');
    expect(codes).toContain('PRIMARY_REQUIRED');
  });

  it('requires a template before anything else can be trusted', () => {
    const report = validateDraft(validDraft({ templateId: null }));
    expect(report.ok).toBe(false);
    expect(report.errors.map((issue) => issue.code)).toContain('TEMPLATE_NOT_CHOSEN');
  });

  it('refuses a template id no template declares', () => {
    const report = validateDraft(validDraft({ templateId: 'invented-template' }));
    expect(report.errors.map((issue) => issue.code)).toContain('TEMPLATE_UNKNOWN');
  });

  it('requires a four-letter ICAO indicator, not an IATA code', () => {
    expect(validateDraft(validDraft({ airportIcao: 'BLR' })).errors.map((i) => i.code)).toContain(
      'AIRPORT_NOT_ICAO',
    );
    expect(validateDraft(validDraft({ airportIcao: 'VOBLX' })).errors.map((i) => i.code)).toContain(
      'AIRPORT_NOT_ICAO',
    );
    expect(validateDraft(validDraft({ airportIcao: 'VOBL' })).ok).toBe(true);
  });

  it('rejects a start time it cannot read', () => {
    expect(validateDraft(validDraft({ startsAt: 'tomorrow' })).errors.map((i) => i.code)).toContain(
      'START_UNPARSEABLE',
    );
    expect(validateDraft(validDraft({ startsAt: '' })).errors.map((i) => i.code)).toContain(
      'START_REQUIRED',
    );
  });

  it('bounds the duration and rejects a non-integer', () => {
    expect(
      validateDraft(validDraft({ durationMinutes: MIN_DURATION_MINUTES - 1 })).errors.map(
        (i) => i.code,
      ),
    ).toContain('DURATION_OUT_OF_RANGE');
    expect(
      validateDraft(validDraft({ durationMinutes: MAX_DURATION_MINUTES + 1 })).errors.map(
        (i) => i.code,
      ),
    ).toContain('DURATION_OUT_OF_RANGE');
    expect(
      validateDraft(validDraft({ durationMinutes: Number.NaN })).errors.map((i) => i.code),
    ).toContain('DURATION_NOT_A_NUMBER');
    expect(
      validateDraft(validDraft({ durationMinutes: 90.5 })).errors.map((i) => i.code),
    ).toContain('DURATION_NOT_A_NUMBER');
  });

  it('warns rather than blocks on a very long disruption', () => {
    const report = validateDraft(validDraft({ durationMinutes: 900 }));
    expect(report.ok).toBe(true);
    expect(report.warnings.map((issue) => issue.code)).toContain('DURATION_LONG');
  });

  it('rejects a malformed or duplicated flight', () => {
    expect(
      validateDraft(
        validDraft({ flightNumbers: ['6E 2134', 'nope'], primaryFlight: '6E 2134' }),
      ).errors.map((i) => i.code),
    ).toContain('FLIGHT_MALFORMED');
    expect(
      validateDraft(validDraft({ flightNumbers: ['6E 2134', '6E 2134'] })).errors.map(
        (i) => i.code,
      ),
    ).toContain('FLIGHT_DUPLICATED');
  });

  it('requires the primary flight to be one of the affected flights', () => {
    const report = validateDraft(validDraft({ primaryFlight: 'AI 999' }));
    expect(report.ok).toBe(false);
    expect(report.errors.map((issue) => issue.code)).toContain('PRIMARY_NOT_LISTED');
  });

  it('warns on a large scenario because every flight opens its own gate', () => {
    const flights = Array.from(
      { length: LARGE_SCENARIO_FLIGHTS + 1 },
      (_, index) => `6E ${1000 + index}`,
    );
    const report = validateDraft(
      validDraft({ flightNumbers: flights, primaryFlight: flights[0]! }),
    );
    expect(report.ok).toBe(true);
    expect(report.warnings.map((issue) => issue.code)).toContain('SCENARIO_LARGE');
  });

  it('bounds notes and warns when critical severity is unexplained', () => {
    expect(
      validateDraft(validDraft({ notes: 'x'.repeat(MAX_NOTES_LENGTH + 1) })).errors.map(
        (i) => i.code,
      ),
    ).toContain('NOTES_TOO_LONG');

    const critical = validateDraft(validDraft({ severity: 'critical', notes: '' }));
    expect(critical.ok).toBe(true);
    expect(critical.warnings.map((issue) => issue.code)).toContain('CRITICAL_WITHOUT_NOTE');
  });

  it('names the field on every issue, so no message is a guessing game', () => {
    const report = validateDraft(emptyDraft());
    expect(report.issues.length).toBeGreaterThan(0);
    for (const issue of report.issues) {
      expect(issue.field).toBeTruthy();
      expect(issue.code).toBeTruthy();
      expect(issue.message.trim().length).toBeGreaterThan(0);
    }
  });

  it('groups issues by field for rendering beside the input', () => {
    const report = validateDraft(validDraft({ airportIcao: 'BLR' }));
    expect(issuesForField(report, 'airportIcao').map((issue) => issue.code)).toEqual([
      'AIRPORT_NOT_ICAO',
    ]);
    expect(issuesForField(report, 'name')).toEqual([]);
  });
});

describe('templates and drafts', () => {
  it('starts invalid, so the wizard has something to teach', () => {
    expect(validateDraft(emptyDraft()).ok).toBe(false);
  });

  it('every declared template produces a valid draft once a start time is set', () => {
    for (const template of SCENARIO_TEMPLATES) {
      const draft = applyTemplate(emptyDraft(), template);
      const report = validateDraft({ ...draft, startsAt: '2026-08-20T15:36' });
      expect(report.errors, template.id).toEqual([]);
    }
  });

  it('keeps the operator notes when a template is re-applied', () => {
    const typed = { ...emptyDraft(), notes: 'Checking the crew rotation case.' };
    const applied = applyTemplate(typed, SCENARIO_TEMPLATES[0]!);
    expect(applied.notes).toBe('Checking the crew rotation case.');
    expect(applied.templateId).toBe(SCENARIO_TEMPLATES[0]!.id);
  });

  it('makes the first template flight the primary', () => {
    const applied = applyTemplate(emptyDraft(), SCENARIO_TEMPLATES[0]!);
    expect(applied.primaryFlight).toBe(SCENARIO_TEMPLATES[0]!.flightNumbers[0]);
  });

  it('never leaves a primary flight naming a flight that is gone', () => {
    const draft = validDraft();
    const reduced = setFlightNumbers(draft, ['6E 811']);
    expect(reduced.primaryFlight).toBe('6E 811');

    const emptied = setFlightNumbers(draft, []);
    expect(emptied.primaryFlight).toBe('');
    // And the invariant holds for the validator too.
    expect(validateDraft(emptied).errors.map((i) => i.code)).toContain('FLIGHTS_REQUIRED');
  });

  it('keeps the primary when it survives the edit', () => {
    const kept = setFlightNumbers(validDraft(), ['6E 811', '6E 2134']);
    expect(kept.primaryFlight).toBe('6E 2134');
  });

  it('resolves only templates it declares', () => {
    expect(findTemplate('bengaluru-monsoon-storm')?.airportIcao).toBe('VOBL');
    expect(findTemplate('nope')).toBeNull();
    expect(findTemplate(null)).toBeNull();
  });
});

describe('buildPreview', () => {
  it('reports what the operator declared and counts only the list they typed', () => {
    const preview = buildPreview(validDraft());
    expect(preview.affectedFlightCount).toBe(2);
    expect(preview.affectedFlights).toEqual(['6E 2134', '6E 811']);
    expect(preview.downstreamFlights).toEqual(['6E 811']);
    expect(preview.airportIcao).toBe('VOBL');
    expect(preview.templateName).toBe('Bengaluru monsoon storm');
  });

  it('derives the end from the declared start and duration', () => {
    const preview = buildPreview(
      validDraft({ startsAt: '2026-08-20T15:00:00.000Z', durationMinutes: 90 }),
    );
    expect(preview.endsAt).toBe('2026-08-20T16:30:00.000Z');
  });

  it('reports no end rather than a wrong one when the start cannot be read', () => {
    expect(buildPreview(validDraft({ startsAt: 'soon' })).endsAt).toBeNull();
    expect(buildPreview(validDraft({ durationMinutes: Number.NaN })).endsAt).toBeNull();
  });

  it('names the engine-computed figures instead of inventing them', () => {
    const preview = buildPreview(validDraft());
    expect(preview.computedByEngine).toContain('passengers affected');
    expect(preview.computedByEngine).toContain('connections at risk');
    // The preview must expose no figure of its own beyond the count of what was typed.
    const numeric = Object.entries(preview).filter(([, value]) => typeof value === 'number');
    expect(numeric.map(([key]) => key).sort()).toEqual(['affectedFlightCount', 'durationMinutes']);
  });

  it('upper-cases the airport and trims the name, matching what would be sent', () => {
    const preview = buildPreview(validDraft({ airportIcao: 'vobl', name: '  Storm  ' }));
    expect(preview.airportIcao).toBe('VOBL');
    expect(preview.name).toBe('Storm');
  });
});

describe('stepStates', () => {
  it('marks the current step current, whatever else is true', () => {
    const report = validateDraft(validDraft());
    for (const { id } of SCENARIO_STEPS) {
      const states = stepStates(validDraft(), id, report);
      expect(states.find((step) => step.id === id)?.state).toBe('current');
    }
  });

  it('blocks the template step until a template is chosen', () => {
    const draft = emptyDraft();
    const states = stepStates(draft, 'details', validateDraft(draft));
    expect(states.find((step) => step.id === 'template')?.state).toBe('blocked');
  });

  it('distinguishes a step not yet reachable from one that is failing', () => {
    // Unreachable: no template, so details has nothing to edit.
    const unreachable = stepStates(emptyDraft(), 'template', validateDraft(emptyDraft()));
    expect(unreachable.find((step) => step.id === 'details')?.state).toBe('todo');

    // Reachable and wrong: a template is chosen but a field is invalid.
    const broken = validDraft({ airportIcao: 'BLR' });
    const failing = stepStates(broken, 'review', validateDraft(broken));
    expect(failing.find((step) => step.id === 'details')?.state).toBe('blocked');
  });

  it('marks details and review done once the draft validates', () => {
    const draft = validDraft();
    const states = stepStates(draft, 'template', validateDraft(draft));
    expect(states.find((step) => step.id === 'details')?.state).toBe('done');
    expect(states.find((step) => step.id === 'review')?.state).toBe('done');
  });

  it('keeps every step beyond the template shut until one is chosen', () => {
    expect(canOpenStep(emptyDraft(), 'template')).toBe(true);
    expect(canOpenStep(emptyDraft(), 'details')).toBe(false);
    expect(canOpenStep(emptyDraft(), 'review')).toBe(false);
    expect(canOpenStep(validDraft(), 'review')).toBe(true);
  });
});

describe('buildCreateRequest', () => {
  it('maps the draft to exact persisted ids, roles, delays, trigger, and UTC time', () => {
    const outcome = buildCreateRequest(validDraft({ airportIcao: 'vobl' }), FLIGHTS);
    if (!('request' in outcome)) throw new Error('expected a request');

    expect(outcome.request).toEqual({
      root_cause: 'weather',
      airport_icao: 'VOBL',
      severity: 'high',
      effective_at: '2026-08-20T15:36:00.000Z',
      actor_id: 'operator-1',
      members: [
        { flight_id: 1, role: 'primary', delay_minutes: 420 },
        { flight_id: 2, role: 'affected_arrival', delay_minutes: 180 },
      ],
    });
  });

  it.each([
    ['crew', 'crew_rostering'],
    ['technical', 'technical'],
    ['airport_closure', 'other'],
  ] as const)('maps %s to the backend trigger %s', (disruptionType, expected) => {
    const outcome = buildCreateRequest(validDraft({ disruptionType }), FLIGHTS);
    if (!('request' in outcome)) throw new Error('expected a request');
    expect(outcome.request.root_cause).toBe(expected);
  });

  it('refuses an arriving primary before the backend has to reject its role', () => {
    const outcome = buildCreateRequest(validDraft({ primaryFlight: '6E 811' }), FLIGHTS);
    expect('refused' in outcome && outcome.refused.errors.map((issue) => issue.code)).toContain(
      'PRIMARY_NOT_DEPARTING_ROOT',
    );
  });

  it('refuses unresolved, ambiguous, and root-airport-mismatched flights locally', () => {
    const missing = buildCreateRequest(
      validDraft({ flightNumbers: ['6E 999'], primaryFlight: '6E 999' }),
      FLIGHTS,
    );
    expect('refused' in missing && missing.refused.errors.map((issue) => issue.code)).toContain(
      'FLIGHT_NOT_FOUND',
    );

    const ambiguous = buildCreateRequest(validDraft(), [...FLIGHTS, { ...FLIGHTS[0]!, id: 99 }]);
    expect('refused' in ambiguous && ambiguous.refused.errors.map((issue) => issue.code)).toContain(
      'FLIGHT_AMBIGUOUS',
    );

    const outside = buildCreateRequest(validDraft({ airportIcao: 'VABB' }), FLIGHTS);
    expect('refused' in outside && outside.refused.errors.map((issue) => issue.code)).toContain(
      'FLIGHT_OUTSIDE_ROOT_AIRPORT',
    );
  });
});

describe('scenario submission lifecycle', () => {
  const request = () => {
    const outcome = buildCreateRequest(validDraft(), FLIGHTS);
    if (!('request' in outcome)) throw new Error('expected a request');
    return outcome.request;
  };

  it('counts all associated member incidents rather than only those opened by the retry', () => {
    const partialRetry = {
      ...STARTED,
      opened_incident_ids: [11],
      members: [
        {
          flight_id: 1,
          flight_number: '6E 2134',
          role: 'primary',
          incident_id: 10,
          incident_reference: 'INC-1',
          state: 'detected',
          note: null,
        },
        {
          flight_id: 2,
          flight_number: '6E 811',
          role: 'affected_arrival',
          incident_id: 11,
          incident_reference: 'INC-2',
          state: 'detected',
          note: null,
        },
      ],
    };
    expect(startedMemberIncidentCount(partialRetry)).toBe(2);
    expect(partialRetry.opened_incident_ids).toHaveLength(1);
  });

  it('creates only when run was not requested', async () => {
    const calls: string[] = [];
    const result = await submitScenario(
      request(),
      false,
      { create: 'create-key', start: 'start-key' },
      {
        createScenario: async (_payload, key) => {
          calls.push(`create:${key}`);
          return CREATED;
        },
        startScenario: async () => {
          calls.push('start');
          return STARTED;
        },
      },
    );

    expect(result).toEqual({ ok: true, created: CREATED, started: null, navigateTo: null });
    expect(calls).toEqual(['create:create-key']);
  });

  it('creates then starts with the returned SCN reference and yields its concrete route', async () => {
    const calls: string[] = [];
    const result = await submitScenario(
      request(),
      true,
      { create: 'create-key', start: 'start-key' },
      {
        createScenario: async (_payload, key) => {
          calls.push(`create:${key}`);
          return CREATED;
        },
        startScenario: async (reference, actorId, key) => {
          calls.push(`start:${reference}:${actorId}:${key}`);
          return STARTED;
        },
      },
    );

    expect(calls).toEqual([
      'create:create-key',
      `start:${CREATED.scenario_reference}:operator-1:start-key`,
    ]);
    expect(result).toEqual({
      ok: true,
      created: CREATED,
      started: STARTED,
      navigateTo: `/cascade/${CREATED.scenario_reference}`,
    });
  });

  it('stops after create failure and never implies a reference exists', async () => {
    let starts = 0;
    const failure = new Error('create unavailable');
    const result = await submitScenario(
      request(),
      true,
      { create: 'create-key', start: 'start-key' },
      {
        createScenario: async () => Promise.reject(failure),
        startScenario: async () => {
          starts += 1;
          return STARTED;
        },
      },
    );

    expect(result).toEqual({
      ok: false,
      stage: 'create',
      error: failure,
      created: null,
    });
    expect(starts).toBe(0);
  });

  it('preserves create success and retries only start with the same idempotency key', async () => {
    let creates = 0;
    const startKeys: string[] = [];
    const failure = new Error('active conflict');
    const port = {
      createScenario: async () => {
        creates += 1;
        return CREATED;
      },
      startScenario: async (_reference: string, _actor: string, key: string) => {
        startKeys.push(key);
        if (startKeys.length === 1) throw failure;
        return STARTED;
      },
    };
    const keys = { create: 'stable-create', start: 'stable-start' };

    const first = await submitScenario(request(), true, keys, port);
    expect(first).toEqual({
      ok: false,
      stage: 'start',
      error: failure,
      created: CREATED,
    });

    const second = await submitScenario(request(), true, keys, port, CREATED);
    expect(second.ok).toBe(true);
    expect(creates).toBe(1);
    expect(startKeys).toEqual(['stable-start', 'stable-start']);
  });

  it('generates distinct create/start keys once for the component to retain', () => {
    const ids = ['one', 'two'];
    const keys = createScenarioIdempotencyKeys(() => ids.shift()!);
    expect(keys).toEqual({
      create: 'scenario-create-one',
      start: 'scenario-start-two',
    });
  });
});

/**
 * Guards, not unit tests.
 *
 * Both Phase 5 screens shipped a defect the product has had twice before: `uppercase` on a wrapper
 * that contained a value, so the console displayed a string the contract never produced. The request
 * id `scn-1a2b3c4d` rendered as `SCN-1A2B3C4D` and the recorded cause `weather` as `WEATHER`. A
 * browser check caught it; a unit test could not, because the transform is CSS. This reads the source
 * so it cannot come back.
 *
 * Covers both Phase 5 screens from one place rather than duplicating the scanner into two files.
 */
describe('no Phase 5 surface case-transforms a contract value', () => {
  /** Comments are blanked first, the same way `check-tokens.mjs` and the policy guards do it. */
  const stripComments = (source: string) =>
    source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');

  const read = (path: string) =>
    stripComments(readFileSync(new URL(path, import.meta.url), 'utf8'));

  const SCREENS = [
    ['ScenarioBuilder.tsx', read('./ScenarioBuilder.tsx')],
    ['PassengerDisruptionView.tsx', read('../passenger/PassengerDisruptionView.tsx')],
  ] as const;

  /**
   * Every element whose className carries `uppercase`, paired with its own content.
   *
   * Content ends at the first closing tag that is not `</MonoValue>`, which is what makes a nested
   * value detectable: a correct row closes its label element immediately, while the broken shape
   * reaches a `<MonoValue>` before its wrapper closes.
   */
  function uppercasedElementsWithNestedValue(source: string): string[] {
    const offenders: string[] = [];
    const opening = /className=\{?["'`][^"'`]*\buppercase\b[^"'`]*["'`][^>]*>/g;
    let match: RegExpExecArray | null;
    while ((match = opening.exec(source)) !== null) {
      const after = source.slice(match.index + match[0].length);
      const closer = /<\/(?!MonoValue)[a-zA-Z]/.exec(after);
      const content = closer ? after.slice(0, closer.index) : after.slice(0, 400);
      if (content.includes('<MonoValue')) {
        offenders.push(content.replace(/\s+/g, ' ').trim().slice(0, 90));
      }
    }
    return offenders;
  }

  it.each(SCREENS)('%s puts uppercase on the label, never around the value', (_name, source) => {
    expect(uppercasedElementsWithNestedValue(source)).toEqual([]);
  });

  it('the scanner actually detects the shape that shipped, so it is not vacuous', () => {
    const broken = `
      <span className="text-caption uppercase text-fg-muted">
        request <MonoValue muted>{receipt.requestId}</MonoValue>
      </span>
    `;
    expect(uppercasedElementsWithNestedValue(broken)).toHaveLength(1);

    const correct = `
      <span className="flex items-baseline gap-1.5">
        <span className="text-caption uppercase text-fg-muted">{label}</span>
        <MonoValue muted>{value}</MonoValue>
      </span>
    `;
    expect(uppercasedElementsWithNestedValue(correct)).toEqual([]);
  });

  it.each(SCREENS)('%s routes labelled values through the shared helper', (_name, source) => {
    expect(source).toContain('function Labelled(');
    expect(source).toContain('<Labelled label=');
  });
});

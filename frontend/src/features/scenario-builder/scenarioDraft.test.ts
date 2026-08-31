import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import {
  SCENARIO_TEMPLATES,
  findTemplate,
  scenarioApi,
  type ScenarioDraft,
} from './scenarioContracts';
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
  emptyDraft,
  equivalentCommandFor,
  issuesForField,
  normaliseFlightNumber,
  parseFlightList,
  prepareScenarioRequest,
  setFlightNumbers,
  stableRequestId,
  stepStates,
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

const NOW = new Date('2026-08-20T12:00:00.000Z');

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
    const applied = applyTemplate(typed, SCENARIO_TEMPLATES[2]!);
    expect(applied.notes).toBe('Checking the crew rotation case.');
    expect(applied.templateId).toBe(SCENARIO_TEMPLATES[2]!.id);
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
  it('sends the wire shape, normalised the way the preview showed it', () => {
    const payload = buildCreateRequest(validDraft({ airportIcao: 'vobl', name: ' Storm ' }), {
      runAfterCreate: true,
    });
    expect(payload).toEqual({
      name: 'Storm',
      disruption_type: 'weather',
      airport_icao: 'VOBL',
      starts_at: '2026-08-20T15:36',
      duration_minutes: 180,
      severity: 'high',
      flight_numbers: ['6E 2134', '6E 811'],
      primary_flight: '6E 2134',
      notes: 'Runways closed for the evening peak.',
      template_id: 'bengaluru-monsoon-storm',
      run_after_create: true,
    });
  });

  it('does not alias the draft flight list, so later edits cannot mutate a sent payload', () => {
    const draft = validDraft();
    const payload = buildCreateRequest(draft, { runAfterCreate: false });
    draft.flightNumbers.push('AI 503');
    expect(payload.flight_numbers).toEqual(['6E 2134', '6E 811']);
  });
});

describe('stableRequestId', () => {
  it('is identical for identical payloads', () => {
    const first = buildCreateRequest(validDraft(), { runAfterCreate: false });
    const second = buildCreateRequest(validDraft(), { runAfterCreate: false });
    expect(stableRequestId(first)).toBe(stableRequestId(second));
  });

  it('changes when any part of the request changes, including the run flag', () => {
    const base = buildCreateRequest(validDraft(), { runAfterCreate: false });
    const withRun = buildCreateRequest(validDraft(), { runAfterCreate: true });
    const renamed = buildCreateRequest(validDraft({ name: 'Other' }), { runAfterCreate: false });
    const ids = new Set([
      stableRequestId(base),
      stableRequestId(withRun),
      stableRequestId(renamed),
    ]);
    expect(ids.size).toBe(3);
  });

  it('is a stable, readable token rather than a random one', () => {
    expect(stableRequestId(buildCreateRequest(validDraft(), { runAfterCreate: false }))).toMatch(
      /^scn-[0-9a-f]{8}$/,
    );
  });
});

describe('equivalentCommandFor', () => {
  it('offers the seed command only for a template the repository actually ships', () => {
    const command = equivalentCommandFor(applyTemplate(emptyDraft(), SCENARIO_TEMPLATES[0]!), {
      runAfterCreate: true,
    });
    expect(command).toBe('python -m app.cli inject --scenario bengaluru_storm --cascade');
  });

  it('omits --cascade when the operator did not ask for a run', () => {
    expect(
      equivalentCommandFor(applyTemplate(emptyDraft(), SCENARIO_TEMPLATES[0]!), {
        runAfterCreate: false,
      }),
    ).toBe('python -m app.cli inject --scenario bengaluru_storm');
  });

  it('offers nothing for a template with no seeded equivalent', () => {
    for (const template of SCENARIO_TEMPLATES.filter((entry) => entry.seedScenarioId === null)) {
      expect(
        equivalentCommandFor(applyTemplate(emptyDraft(), template), { runAfterCreate: false }),
        template.id,
      ).toBeNull();
    }
  });

  it('withdraws the command as soon as the draft diverges from the seed', () => {
    /*
     * The whole point of the check. A command labelled "equivalent" that produced a different
     * disruption would be worse than offering none, because an operator would trust the output.
     */
    const seeded = applyTemplate(emptyDraft(), SCENARIO_TEMPLATES[0]!);
    for (const divergence of [
      { airportIcao: 'VIDP' },
      { severity: 'low' as const },
      { durationMinutes: 45 },
      { disruptionType: 'crew' as const },
      { flightNumbers: ['6E 2134'] },
    ]) {
      expect(
        equivalentCommandFor({ ...seeded, ...divergence }, { runAfterCreate: false }),
        JSON.stringify(divergence),
      ).toBeNull();
    }
  });
});

describe('prepareScenarioRequest', () => {
  it('refuses an invalid draft instead of emitting a body the backend would reject', () => {
    const outcome = prepareScenarioRequest(emptyDraft(), { runAfterCreate: false, now: NOW });
    expect('refused' in outcome).toBe(true);
    if ('refused' in outcome) expect(outcome.refused.ok).toBe(false);
  });

  it('prepares a request that is explicitly not submitted', () => {
    const outcome = prepareScenarioRequest(validDraft(), { runAfterCreate: false, now: NOW });
    expect('receipt' in outcome).toBe(true);
    if (!('receipt' in outcome)) return;

    const { receipt } = outcome;
    expect(receipt.submitted).toBe(false);
    // Never blank while unsubmitted: the screen renders this instead of implying a creation.
    expect(receipt.unsubmittedReason.trim().length).toBeGreaterThan(0);
    expect(receipt.targetEndpoint).toBe(scenarioApi.createEndpoint);
    expect(receipt.preparedAt).toBe(NOW.toISOString());
  });

  it('is deterministic for the same draft and clock', () => {
    const first = prepareScenarioRequest(validDraft(), { runAfterCreate: true, now: NOW });
    const second = prepareScenarioRequest(validDraft(), { runAfterCreate: true, now: NOW });
    expect(first).toEqual(second);
  });

  it('records the run intent on the payload rather than acting on it', () => {
    const outcome = prepareScenarioRequest(validDraft(), { runAfterCreate: true, now: NOW });
    if (!('receipt' in outcome)) throw new Error('expected a receipt');
    expect(outcome.receipt.payload.run_after_create).toBe(true);
    expect(outcome.receipt.submitted).toBe(false);
  });

  it('warnings do not stop a request being prepared', () => {
    const outcome = prepareScenarioRequest(validDraft({ severity: 'critical', notes: '' }), {
      runAfterCreate: false,
      now: NOW,
    });
    expect('receipt' in outcome).toBe(true);
  });
});

describe('the authoring endpoint is reported as absent, never as available', () => {
  it('keeps canCreate false while no endpoint exists', () => {
    // If this ever flips to true, the screen must stop describing the request as merely prepared.
    expect(scenarioApi.canCreate).toBe(false);
    expect(scenarioApi.createEndpoint).toMatch(/^POST /);
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

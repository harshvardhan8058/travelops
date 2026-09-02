import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import {
  datasetHeadlines,
  datasetStatus,
  groupIsStarted,
  RESET_CONFIRMATION,
  resetBlockedReason,
  resetConfirmationMatches,
  simulationReadiness,
  simulationToScenarioRequest,
  startBlockedReason,
} from './demoControl';
import type { DemoDatasetResponse, SimulationDefinition } from '@/api/types';

function simulation(overrides: Partial<SimulationDefinition> = {}): SimulationDefinition {
  return {
    id: 'bengaluru_severe_weather',
    name: 'Bengaluru severe-weather disruption',
    summary: 'Severe weather at Bengaluru delays departures.',
    root_cause: 'weather',
    airport_icao: 'VOBL',
    severity: 'high',
    // The RECORDED scenario clock, as the backend publishes it.
    effective_at: '2026-08-20T15:40:00Z',
    members: [
      {
        flight_id: 1,
        flight_number: '6E 2134',
        role: 'primary',
        origin_icao: 'VOBL',
        destination_icao: 'VIDP',
        delay_minutes: 420,
      },
      {
        flight_id: 2,
        flight_number: '6E 2140',
        role: 'affected_departure',
        origin_icao: 'VOBL',
        destination_icao: 'VABB',
        delay_minutes: 110,
      },
    ],
    passengers_affected: 604,
    runnable: true,
    blocked_reason: null,
    provenance: { kind: 'simulated', provider: 'demo.simulation_catalogue' },
    ...overrides,
  };
}

function dataset(overrides: Partial<DemoDatasetResponse> = {}): DemoDatasetResponse {
  return {
    is_seeded: true,
    tables: [
      { table: 'airport', rows: 6 },
      { table: 'flight', rows: 22 },
    ],
    flights: 22,
    bookings: 604,
    booking_segments: 700,
    airports: 6,
    incident_groups: 1,
    incidents: 4,
    current_group_reference: 'GRP-2026-0820-VOBL',
    reset_allowed: true,
    app_env: 'demo',
    note: 'Row counts read back from the database, not cached.',
    ...overrides,
  };
}

describe('the reset phrase agrees with the server', () => {
  // A drifted phrase would present a control that can never succeed, and no frontend test in
  // isolation could tell. So the backend constant is read rather than restated.
  it('matches RESET_CONFIRMATION in backend/app/schemas/demo.py', () => {
    const source = readFileSync(
      new URL('../../../../backend/app/schemas/demo.py', import.meta.url),
      'utf8',
    );
    const match = /^RESET_CONFIRMATION\s*=\s*"([^"]+)"/m.exec(source);

    expect(match, 'RESET_CONFIRMATION not found in backend/app/schemas/demo.py').not.toBeNull();
    expect(RESET_CONFIRMATION).toBe(match?.[1]);
  });
});

describe('resetConfirmationMatches', () => {
  it('accepts the exact phrase', () => {
    expect(resetConfirmationMatches('reset demo data')).toBe(true);
  });

  it.each([
    ['surrounding whitespace', '  reset demo data  '],
    ['different case', 'Reset Demo Data'],
    ['shouting', 'RESET DEMO DATA'],
  ])('accepts %s, matching the server comparison', (_why, typed) => {
    // The server compares `payload.confirm.strip().lower()`. A stricter client check would disable
    // a control the server would have honoured.
    expect(resetConfirmationMatches(typed)).toBe(true);
  });

  it.each(['', '   ', 'yes', 'reset', 'reset demo', 'delete demo data', 'reset  demo  data'])(
    'refuses %o',
    (typed) => {
      expect(resetConfirmationMatches(typed)).toBe(false);
    },
  );
});

describe('simulationToScenarioRequest', () => {
  it('copies every published member value verbatim', () => {
    const source = simulation();
    const request = simulationToScenarioRequest(source);

    expect(request.members).toEqual([
      { flight_id: 1, role: 'primary', delay_minutes: 420 },
      { flight_id: 2, role: 'affected_departure', delay_minutes: 110 },
    ]);
  });

  it('never adjusts a recorded delay, which POST /scenarios would refuse', () => {
    const request = simulationToScenarioRequest(simulation());

    expect(request.members.map((m) => m.delay_minutes)).toEqual([420, 110]);
  });

  it('carries the published root cause, airport and severity unchanged', () => {
    const request = simulationToScenarioRequest(
      simulation({ root_cause: 'weather', airport_icao: 'VOBL', severity: 'medium' }),
    );

    expect(request.root_cause).toBe('weather');
    expect(request.airport_icao).toBe('VOBL');
    expect(request.severity).toBe('medium');
  });

  it('preserves member order, so the primary stays first', () => {
    const request = simulationToScenarioRequest(simulation());

    expect(request.members[0]?.role).toBe('primary');
  });

  it('uses the published instant and the supplied actor', () => {
    const request = simulationToScenarioRequest(simulation(), { actorId: 'operator-7' });

    // The instant comes from the definition; only the actor is the caller's to supply.
    expect(request.effective_at).toBe('2026-08-20T15:40:00Z');
    expect(request.actor_id).toBe('operator-7');
  });

  it('defaults the actor rather than sending an empty one', () => {
    expect(simulationToScenarioRequest(simulation()).actor_id).toBe('operator-1');
  });

  it('adds no field the scenario contract does not declare', () => {
    const request = simulationToScenarioRequest(simulation());

    // `extra="forbid"` on the backend model means an invented key is a 422, not a silent ignore.
    expect(Object.keys(request).sort()).toEqual([
      'actor_id',
      'airport_icao',
      'effective_at',
      'members',
      'root_cause',
      'severity',
    ]);
    const member = request.members[0];
    expect(member).toBeDefined();
    expect(Object.keys(member ?? {}).sort()).toEqual(['delay_minutes', 'flight_id', 'role']);
  });

  it('does not carry flight_number, which the scenario contract does not accept', () => {
    const request = simulationToScenarioRequest(simulation());

    expect(request.members[0]).not.toHaveProperty('flight_number');
  });
});

describe('simulationReadiness', () => {
  it('lets a runnable simulation with members start', () => {
    expect(simulationReadiness(simulation())).toEqual({ canStart: true, reason: null });
  });

  it("reports the server's own reason when the dataset cannot support it", () => {
    const readiness = simulationReadiness(
      simulation({
        runnable: false,
        blocked_reason: 'no departure from VOBL has an onward connection recorded',
        members: [],
      }),
    );

    expect(readiness.canStart).toBe(false);
    expect(readiness.reason).toBe('no departure from VOBL has an onward connection recorded');
  });

  it('still says something when the server blocked it without a reason', () => {
    const readiness = simulationReadiness(simulation({ runnable: false, blocked_reason: null }));

    expect(readiness.canStart).toBe(false);
    expect(readiness.reason).toBeTruthy();
  });

  it('refuses a runnable definition that declared no flights', () => {
    // Would otherwise POST an empty members array from a button that looked ready.
    const readiness = simulationReadiness(simulation({ runnable: true, members: [] }));

    expect(readiness.canStart).toBe(false);
    expect(readiness.reason).toMatch(/declared no flights/i);
  });
});

describe('startBlockedReason', () => {
  const ready = { canWrite: true, isSeeded: true, isBusy: false };

  it('is null when everything is satisfied', () => {
    expect(startBlockedReason(simulation(), ready)).toBeNull();
  });

  it('names fixture mode first, because it outranks every other cause', () => {
    const reason = startBlockedReason(simulation({ runnable: false, blocked_reason: 'no rows' }), {
      canWrite: false,
      isSeeded: false,
      isBusy: true,
    });

    expect(reason).toMatch(/live API/i);
  });

  it('names an unseeded dataset before a definition problem', () => {
    const reason = startBlockedReason(simulation({ runnable: false, blocked_reason: 'no rows' }), {
      ...ready,
      isSeeded: false,
    });

    expect(reason).toMatch(/not seeded/i);
  });

  it("passes through the server's blocked reason", () => {
    const reason = startBlockedReason(
      simulation({ runnable: false, blocked_reason: 'no onward connection recorded' }),
      ready,
    );

    expect(reason).toBe('no onward connection recorded');
  });

  it('reports a busy operation last, so a real obstacle is never masked by it', () => {
    expect(startBlockedReason(simulation(), { ...ready, isBusy: true })).toMatch(/in progress/i);
  });
});

describe('resetBlockedReason', () => {
  const ready = { canWrite: true, resetAllowed: true, isBusy: false, typed: RESET_CONFIRMATION };

  it('is null once the phrase is typed and nothing else objects', () => {
    expect(resetBlockedReason(ready)).toBeNull();
  });

  it('names fixture mode first', () => {
    expect(resetBlockedReason({ ...ready, canWrite: false, typed: '' })).toMatch(/live API/i);
  });

  it('names an environment that forbids destructive controls before asking for a phrase', () => {
    expect(resetBlockedReason({ ...ready, resetAllowed: false, typed: '' })).toMatch(
      /does not permit/i,
    );
  });

  it('asks for the phrase last, and quotes it exactly', () => {
    const reason = resetBlockedReason({ ...ready, typed: 'reset' });

    expect(reason).toContain(RESET_CONFIRMATION);
  });

  it('does not ask for a phrase in an environment where it would not help', () => {
    expect(resetBlockedReason({ ...ready, canWrite: false, typed: '' })).not.toContain(
      RESET_CONFIRMATION,
    );
  });
});

describe('datasetHeadlines', () => {
  it('reads every figure straight from the contract', () => {
    const headlines = datasetHeadlines(dataset());

    expect(headlines.map((h) => [h.label, h.value])).toEqual([
      ['Airports', 6],
      ['Flights', 22],
      ['Bookings', 604],
      ['Booking segments', 700],
      ['Disruption groups', 1],
      ['Incidents', 4],
    ]);
  });

  it('separates reference rows from the workflow output a reset removes', () => {
    const headlines = datasetHeadlines(dataset());
    const workflow = headlines.filter((h) => h.origin === 'workflow').map((h) => h.label);

    expect(workflow).toEqual(['Disruption groups', 'Incidents']);
  });

  it('shows a genuine zero rather than hiding it', () => {
    const headlines = datasetHeadlines(dataset({ incidents: 0, incident_groups: 0 }));

    expect(headlines.find((h) => h.label === 'Incidents')?.value).toBe(0);
  });
});

describe('the recorded clock, not the wall clock', () => {
  it('declares the simulation at the instant the server published', () => {
    const request = simulationToScenarioRequest(
      simulation({ effective_at: '2026-08-20T15:40:00Z' }),
    );

    expect(request.effective_at).toBe('2026-08-20T15:40:00Z');
  });

  it('is not near the current time, which is what the defect produced', () => {
    const request = simulationToScenarioRequest(simulation());
    const declared = Date.parse(request.effective_at);

    // The recorded dataset is anchored to its own date. A value close to now means a clock was read.
    expect(Math.abs(Date.now() - declared)).toBeGreaterThan(3600_000);
  });

  it('offers no way for a caller to supply an instant', () => {
    /*
     * A type-level guarantee, asserted at runtime too. The parameter used to exist and the Scenario
     * Center passed `new Date().toISOString()` into it, which is the single line that made a
     * browser-started demo unfinishable: the recorded METAR is anchored to the dataset's date, an
     * incident opened now fails `sources_fresh`, and a stale-evidence refusal is one no operator is
     * allowed to approve.
     */
    const withAttemptedOverride = simulationToScenarioRequest(
      simulation({ effective_at: '2026-08-20T15:40:00Z' }),
      // @ts-expect-error the option does not exist, and must not
      { effectiveAt: '2099-01-01T00:00:00Z' },
    );

    expect(withAttemptedOverride.effective_at).toBe('2026-08-20T15:40:00Z');
  });

  it('reads no clock anywhere in the module', () => {
    // Source-level, because the honest implementation is one that *cannot* express a wall clock.
    const source = readFileSync(new URL('./demoControl.ts', import.meta.url), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/(^|[^:])\/\/.*$/gm, '$1');

    expect(source).not.toMatch(/new Date\(/);
    expect(source).not.toMatch(/Date\.now\(/);
    expect(source).not.toMatch(/toISOString\(/);
  });

  it('the screen reads no clock for the scenario instant either', () => {
    const source = readFileSync(new URL('./ScenarioCenter.tsx', import.meta.url), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/(^|[^:])\/\/.*$/gm, '$1');

    expect(source).not.toMatch(/new Date\(/);
    expect(source).not.toMatch(/toISOString\(/);
  });

  it('the published instant is carried through unchanged for every definition shape', () => {
    for (const instant of ['2026-08-20T15:40:00Z', '2026-01-02T03:04:05Z']) {
      expect(simulationToScenarioRequest(simulation({ effective_at: instant })).effective_at).toBe(
        instant,
      );
    }
  });
});

describe('datasetStatus', () => {
  const started = (state: string) => ({ state, flightsAffected: 4, flightsWithoutIncident: 1 });
  const unstarted = (state = 'detected') => ({
    state,
    flightsAffected: 8,
    flightsWithoutIncident: 8,
  });

  it('is CLEAN when the dataset is seeded and no group is open', () => {
    const status = datasetStatus({ isSeeded: true, groups: [] });
    expect(status.status).toBe('CLEAN');
    expect(status.resetWouldClear).toBe(false);
  });

  it('is CLEAN when the only group is seeded but never started', () => {
    // The regression that mattered. A reset re-seeds the group, which then sits in `detected`
    // with no incidents at all — so folding raw states reported a freshly reset dataset as ACTIVE
    // and pointed the operator at the very reset they had just performed.
    expect(datasetStatus({ isSeeded: true, groups: [unstarted()] }).status).toBe('CLEAN');
  });

  it('is ACTIVE while a started group is still being worked, and says reset would clear it', () => {
    const status = datasetStatus({
      isSeeded: true,
      groups: [started('detected'), started('resolved')],
    });
    expect(status.status).toBe('ACTIVE');
    expect(status.resetWouldClear).toBe(true);
    expect(status.detail).toContain('1 disruption');
  });

  it.each([['assessing'], ['planning'], ['assuring'], ['awaiting_approval'], ['executing']])(
    'treats %s as active work',
    (state) => {
      expect(datasetStatus({ isSeeded: true, groups: [started(state)] }).status).toBe('ACTIVE');
    },
  );

  it('is RESOLVED only when every started group resolved', () => {
    expect(
      datasetStatus({ isSeeded: true, groups: [started('resolved'), started('resolved')] }).status,
    ).toBe('RESOLVED');
  });

  it('ignores an unstarted group when judging the started ones', () => {
    expect(
      datasetStatus({ isSeeded: true, groups: [started('resolved'), unstarted()] }).status,
    ).toBe('RESOLVED');
  });

  it('is PARTIALLY_PROCESSED when every started group stopped but not all resolved', () => {
    // `resolved` requires every member resolved — seven of eight is not success — and the same
    // rule has to hold one level up, or a blocked group would read as a finished one.
    expect(
      datasetStatus({ isSeeded: true, groups: [started('resolved'), started('blocked')] }).status,
    ).toBe('PARTIALLY_PROCESSED');
    expect(datasetStatus({ isSeeded: true, groups: [started('failed')] }).status).toBe(
      'PARTIALLY_PROCESSED',
    );
  });

  it('reports UNKNOWN rather than guessing when the group list has not arrived', () => {
    // An unstarted dataset and an unanswered query are different things, and only one of them
    // means "go ahead".
    expect(datasetStatus({ isSeeded: true, groups: undefined }).status).toBe('UNKNOWN');
    expect(datasetStatus({ isSeeded: false, groups: [] }).status).toBe('UNKNOWN');
  });
});

describe('groupIsStarted', () => {
  it('prefers the incident counter when the server sends one', () => {
    // `detected` with zero incidents is a seeded group nobody has touched. Its state is identical
    // to a group that has just been started, which is why state alone can never answer this.
    expect(
      groupIsStarted({
        state: 'detected',
        incidentsInGroup: 0,
        flightsAffected: 8,
        flightsWithoutIncident: 0,
      }),
    ).toBe(false);
    expect(
      groupIsStarted({
        state: 'detected',
        incidentsInGroup: 1,
        flightsAffected: 8,
        flightsWithoutIncident: 8,
      }),
    ).toBe(true);
  });

  it('is false when every declared flight lacks an incident', () => {
    expect(
      groupIsStarted({ state: 'detected', flightsAffected: 8, flightsWithoutIncident: 8 }),
    ).toBe(false);
  });

  it('is true as soon as one declared flight has an incident', () => {
    expect(
      groupIsStarted({ state: 'detected', flightsAffected: 8, flightsWithoutIncident: 7 }),
    ).toBe(true);
  });

  it('is false for a group declaring no flights at all', () => {
    expect(
      groupIsStarted({ state: 'detected', flightsAffected: 0, flightsWithoutIncident: 0 }),
    ).toBe(false);
  });
});

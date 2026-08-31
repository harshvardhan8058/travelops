import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

import type {
  PassengerAction,
  PassengerDisruptionResponse,
  PassengerOption,
  PassengerSegmentOut,
} from '@/api/types';
import {
  actionLabel,
  deriveActionProgress,
  deriveConsequences,
  deriveNextStep,
  deriveTripStatus,
  formatDelay,
  hasRevisedTime,
  openConsequenceCount,
  optionBasisNote,
  summariseOptions,
} from './passengerView';

/*
 * These tests are built on locally constructed fixtures, NOT on a shipped sample.
 *
 * That distinction is the point of the change they cover. The screen used to render
 * `PASSENGER_SAMPLE` — a hand-written payload with an invented passenger name, three plausible
 * consequences no row supported, and four options including a refund and a meal that nothing in the
 * system records. The file is gone, and the endpoint it stood in for is real.
 *
 * So the fixtures below are test data, declared in the test, and the assertions are about how the
 * derivations behave on shapes the API can actually produce — including the empty and absent ones,
 * which is where a passenger-facing screen does the most damage when it guesses.
 */

function segment(overrides: Partial<PassengerSegmentOut> = {}): PassengerSegmentOut {
  return {
    segment_id: 1,
    segment_order: 1,
    flight_id: 1,
    flight_number: '6E 2134',
    origin_icao: 'VOBL',
    destination_icao: 'VIDP',
    scheduled_departure: '2026-08-20T15:40:00Z',
    scheduled_arrival: '2026-08-20T18:25:00Z',
    estimated_departure: '2026-08-20T22:40:00Z',
    delay_minutes: 420,
    status: 'delayed',
    gate: null,
    is_disrupted: true,
    ...overrides,
  };
}

function view(overrides: Partial<PassengerDisruptionResponse> = {}): PassengerDisruptionResponse {
  return {
    booking_ref: 'QT7HJ2',
    passenger_reference: 'PAX-00001',
    cabin: 'economy',
    tier: 'gold',
    has_special_needs: false,
    trip: { origin_icao: 'VOBL', destination_icao: 'VOBL', segments: [segment()] },
    disruption: {
      incident_reference: 'INC-2026-0820-VOBL-01',
      group_reference: 'GRP-2026-0820-VOBL',
      flight_id: 1,
      flight_number: '6E 2134',
      airport_icao: 'VOBL',
      cause_category: 'weather',
      severity: 'high',
      state: 'detected',
      opened_at: '2026-08-20T15:36:00Z',
      closed_at: null,
    },
    connection: null,
    priority: null,
    options: [],
    actions: [],
    next_step: { state: 'monitoring', driven_by_action_type: null, respond_by: null },
    unassessed_factors: [],
    basis: 'recorded_rows',
    note: 'Read from recorded rows for this booking only.',
    provenance: { kind: 'synthetic', provider: 'booking_records', source_ref: 'booking:1' },
    ...overrides,
  };
}

function action(overrides: Partial<PassengerAction> = {}): PassengerAction {
  return {
    action_type: 'check_connections',
    state: 'succeeded',
    applies_to: 'incident',
    at: '2026-08-20T15:45:00Z',
    reason_code: null,
    approval_scope: null,
    awaiting_human: false,
    ...overrides,
  };
}

function option(overrides: Partial<PassengerOption> = {}): PassengerOption {
  return {
    kind: 'alternative_flight',
    label: 'AI 305 VIDP to VOBL',
    basis: 'schedule_feasible_only',
    flight_id: 3,
    flight_number: 'AI 305',
    scheduled_departure: '2026-08-21T05:00:00Z',
    hotel_name: null,
    nights: null,
    requires_agent: true,
    ...overrides,
  };
}

// ---------------------------------------------------------------- trip status

describe('deriveTripStatus', () => {
  it('reports the worst status across the trip, not the first', () => {
    const status = deriveTripStatus(
      view({
        trip: {
          origin_icao: 'VOBL',
          destination_icao: 'VOBL',
          segments: [
            segment({ segment_id: 1, status: 'on_time', delay_minutes: 0, is_disrupted: false }),
            segment({ segment_id: 2, status: 'cancelled', is_disrupted: true }),
          ],
        },
      }),
    );

    expect(status.token).toBe('cancelled');
    expect(status.drivenBy?.segment_id).toBe(2);
  });

  it('carries a word alongside the token so colour is never the only signal', () => {
    expect(deriveTripStatus(view()).label).toBe('running late');
  });

  it('counts only the legs an incident was opened against', () => {
    const status = deriveTripStatus(
      view({
        trip: {
          origin_icao: 'VOBL',
          destination_icao: 'VOBL',
          segments: [
            segment({ segment_id: 1, is_disrupted: true }),
            segment({ segment_id: 2, is_disrupted: false }),
          ],
        },
      }),
    );

    expect(status.disruptedSegments).toBe(1);
    expect(status.totalSegments).toBe(2);
  });

  it('treats an empty trip as on time rather than throwing', () => {
    const status = deriveTripStatus(
      view({ trip: { origin_icao: '', destination_icao: '', segments: [] } }),
    );

    expect(status.token).toBe('on_time');
    expect(status.drivenBy).toBeNull();
  });
});

// ---------------------------------------------------------------- delay

describe('formatDelay', () => {
  it('distinguishes an unpublished delay from an on-time flight', () => {
    expect(formatDelay(null)).toBeNull();
    expect(formatDelay(0)).toBe('on time');
  });

  it('formats hours and minutes', () => {
    expect(formatDelay(420)).toBe('7h late');
    expect(formatDelay(95)).toBe('1h 35m late');
    expect(formatDelay(20)).toBe('20m late');
  });

  it('names an early revision as early rather than negative', () => {
    expect(formatDelay(-15)).toBe('15m early');
  });
});

describe('hasRevisedTime', () => {
  it('is false when no estimate is published', () => {
    expect(hasRevisedTime(segment({ estimated_departure: null }))).toBe(false);
  });

  it('is true once an estimate exists', () => {
    expect(hasRevisedTime(segment())).toBe(true);
  });
});

// ---------------------------------------------------------------- consequences

describe('deriveConsequences', () => {
  it('returns nothing when no finding is recorded', () => {
    /* The change this test exists for: the old screen listed three invented consequences here. */
    expect(deriveConsequences(view())).toEqual([]);
  });

  it('states the recorded connection break with the recorded numbers', () => {
    const consequences = deriveConsequences(
      view({
        connection: {
          inbound_flight_number: '6E 2134',
          onward_flight_number: 'AI 101',
          connection_airport_icao: 'VIDP',
          inbound_scheduled_arrival: '2026-08-20T18:25:00Z',
          inbound_revised_arrival: '2026-08-20T22:45:00Z',
          onward_scheduled_departure: '2026-08-20T20:40:00Z',
          minimum_connection_minutes: 45,
          shortfall_minutes: -170,
          recovered_by_onward_delay: false,
          established_by_action_id: 7,
        },
      }),
    );

    expect(consequences).toHaveLength(1);
    const [broken] = consequences;
    expect(broken?.label).toContain('AI 101');
    expect(broken?.label).toContain('VIDP');
    expect(broken?.detail).toContain('170');
    expect(broken?.detail).toContain('45');
  });

  it('does not treat an onward delay as a fix', () => {
    const consequences = deriveConsequences(
      view({
        connection: {
          inbound_flight_number: '6E 2134',
          onward_flight_number: 'AI 101',
          connection_airport_icao: 'VIDP',
          inbound_scheduled_arrival: '2026-08-20T18:25:00Z',
          inbound_revised_arrival: '2026-08-20T22:45:00Z',
          onward_scheduled_departure: '2026-08-20T20:40:00Z',
          minimum_connection_minutes: 45,
          shortfall_minutes: -170,
          recovered_by_onward_delay: true,
          established_by_action_id: 7,
        },
      }),
    );

    expect(consequences.every((entry) => !entry.resolved)).toBe(true);
    expect(consequences.some((entry) => entry.key === 'connection-onward-delay')).toBe(true);
  });

  it('lists recorded priority factors without re-weighting them', () => {
    const consequences = deriveConsequences(
      view({
        priority: {
          priority_index: 52,
          priority_band: 'high',
          factors: [
            { factor: 'special_needs_recorded', weight: 20, source: 'passenger' },
            { factor: 'unreachable_contact', weight: 15, source: 'booking' },
          ],
          rule_version: 'passenger-impact-v1',
          ruleset_hash: 'abc123',
        },
      }),
    );

    expect(consequences.map((entry) => entry.key)).toEqual([
      'factor-special_needs_recorded',
      'factor-unreachable_contact',
    ]);
    /* No score reaches the passenger: the index orders resources, it does not describe the person. */
    expect(JSON.stringify(consequences)).not.toContain('52');
  });

  it('does not state the connection twice when it is also a priority factor', () => {
    const consequences = deriveConsequences(
      view({
        connection: {
          inbound_flight_number: '6E 2134',
          onward_flight_number: 'AI 101',
          connection_airport_icao: 'VIDP',
          inbound_scheduled_arrival: '2026-08-20T18:25:00Z',
          inbound_revised_arrival: '2026-08-20T22:45:00Z',
          onward_scheduled_departure: '2026-08-20T20:40:00Z',
          minimum_connection_minutes: 45,
          shortfall_minutes: -170,
          recovered_by_onward_delay: false,
          established_by_action_id: 7,
        },
        priority: {
          priority_index: 52,
          priority_band: 'high',
          factors: [{ factor: 'broken_connection', weight: 30, source: 'connection' }],
          rule_version: 'passenger-impact-v1',
          ruleset_hash: 'abc123',
        },
      }),
    );

    expect(consequences.filter((entry) => entry.label.includes('AI 101'))).toHaveLength(1);
  });
});

describe('openConsequenceCount', () => {
  it('counts the unresolved entries only', () => {
    expect(
      openConsequenceCount([
        { key: 'a', label: 'a', detail: 'a', resolved: false },
        { key: 'b', label: 'b', detail: 'b', resolved: true },
      ]),
    ).toBe(1);
  });
});

// ---------------------------------------------------------------- options

describe('summariseOptions', () => {
  it('partitions by recorded kind', () => {
    const summary = summariseOptions([
      option(),
      option({ kind: 'hotel_room', hotel_name: 'Airport Inn', nights: 1, flight_id: null }),
    ]);

    expect(summary.flights).toHaveLength(1);
    expect(summary.rooms).toHaveLength(1);
    expect(summary.total).toBe(2);
  });

  it('treats anything short of a recorded reservation as provisional', () => {
    const summary = summariseOptions([
      option(),
      option({ kind: 'hotel_room', basis: 'simulated_reservation' }),
      option({ kind: 'hotel_room', basis: 'recorded_reservation' }),
    ]);

    expect(summary.provisional).toHaveLength(2);
  });

  it('is empty for a booking with nothing recorded', () => {
    expect(summariseOptions([]).total).toBe(0);
  });
});

describe('optionBasisNote', () => {
  it('never describes a reachable departure as an available seat', () => {
    const note = optionBasisNote(option());

    expect(note.toLowerCase()).toContain('no seat');
    expect(note.toLowerCase()).not.toContain('available seat');
  });

  it('marks a simulated hold as not confirmed', () => {
    expect(optionBasisNote(option({ basis: 'simulated_reservation' })).toLowerCase()).toContain(
      'simulated',
    );
  });

  it('states a real reservation plainly', () => {
    expect(optionBasisNote(option({ basis: 'recorded_reservation' })).toLowerCase()).toContain(
      'recorded',
    );
  });
});

// ---------------------------------------------------------------- actions

describe('deriveActionProgress', () => {
  it('counts each recorded state separately', () => {
    const progress = deriveActionProgress([
      action(),
      action({ state: 'executing', awaiting_human: false }),
      action({ state: 'awaiting_approval', awaiting_human: true, at: null }),
      action({ state: 'pending', at: null }),
      action({ state: 'needs_human', reason_code: 'SERVICE_NOT_IMPLEMENTED' }),
    ]);

    expect(progress.done).toBe(1);
    expect(progress.inFlight).toBe(1);
    expect(progress.awaitingApproval).toBe(1);
    expect(progress.pending).toBe(1);
    expect(progress.refused).toBe(1);
    expect(progress.total).toBe(5);
  });

  it('does not count a refusal as progress', () => {
    const progress = deriveActionProgress([action({ state: 'failed' })]);

    expect(progress.done).toBe(0);
    expect(progress.refused).toBe(1);
  });

  it('surfaces the human-blocked action ahead of anything running', () => {
    const progress = deriveActionProgress([
      action({ action_type: 'notify_passengers', state: 'executing' }),
      action({
        action_type: 'rebook_passengers',
        state: 'awaiting_approval',
        awaiting_human: true,
      }),
    ]);

    expect(progress.current?.action_type).toBe('rebook_passengers');
    expect(progress.blockedOnPerson).toBe(true);
  });

  it('reports nothing current once everything has finished', () => {
    const progress = deriveActionProgress([action(), action({ action_type: 'notify_passengers' })]);

    expect(progress.current).toBeNull();
    expect(progress.blockedOnPerson).toBe(false);
  });
});

describe('actionLabel', () => {
  it('uses passenger wording for a known action type', () => {
    expect(actionLabel(action())).toBe('Checked your connection');
  });

  it('falls back to the recorded token rather than inventing a label', () => {
    expect(actionLabel(action({ action_type: 'some_new_service' }))).toBe('some new service');
  });
});

// ---------------------------------------------------------------- next step

describe('deriveNextStep', () => {
  it('says nothing has changed while a person is deciding', () => {
    const step = deriveNextStep(
      view({
        next_step: {
          state: 'awaiting_approval',
          driven_by_action_type: 'rebook_passengers',
          respond_by: null,
        },
      }),
    );

    expect(step.token).toBe('awaiting_approval');
    expect(step.awaitingDecision).toBe(true);
    expect(step.detail.toLowerCase()).toContain('nothing has changed');
  });

  it('never implies a confirmed rebooking while awaiting approval', () => {
    const step = deriveNextStep(
      view({
        next_step: { state: 'awaiting_approval', driven_by_action_type: null, respond_by: null },
      }),
    );

    const copy = `${step.headline} ${step.detail}`.toLowerCase();
    expect(copy).not.toContain('confirmed');
    expect(copy).not.toContain('rebooked');
  });

  it('maps monitoring onto a neutral badge token', () => {
    expect(deriveNextStep(view()).token).toBe('scheduled');
  });

  it('reports an undisrupted trip as running as booked', () => {
    const step = deriveNextStep(
      view({
        disruption: null,
        next_step: { state: 'no_disruption', driven_by_action_type: null, respond_by: null },
      }),
    );

    expect(step.token).toBe('scheduled');
    expect(step.awaitingDecision).toBe(false);
    expect(step.headline.toLowerCase()).toContain('as booked');
  });

  it('reports a closed disruption as closed', () => {
    const step = deriveNextStep(
      view({ next_step: { state: 'resolved', driven_by_action_type: null, respond_by: null } }),
    );

    expect(step.token).toBe('resolved');
  });

  it('never produces a deadline, because none is recorded', () => {
    for (const state of ['awaiting_approval', 'executing', 'resolved', 'monitoring'] as const) {
      const step = deriveNextStep(
        view({ next_step: { state, driven_by_action_type: null, respond_by: null } }),
      );
      expect(step.respondBy).toBeNull();
    }
  });
});

// ---------------------------------------------------------------- the fake state is gone

describe('the screen no longer carries a hardcoded passenger', () => {
  const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');

  it('the speculative contract module is deleted', () => {
    expect(() => read('./passengerContracts.ts')).toThrow();
  });

  it('the screen reads the real endpoint', () => {
    const source = read('./PassengerDisruptionView.tsx');

    expect(source).toContain('api.passengerDisruption');
    expect(source).not.toContain('PASSENGER_SAMPLE');
  });

  it('neither the screen nor the derivations name a passenger', () => {
    /*
     * The endpoint has no name field, so the only way one could reach this screen is a literal.
     * Pinned because a friendly-looking greeting is exactly the change someone would make later.
     */
    for (const path of ['./PassengerDisruptionView.tsx', './passengerView.ts']) {
      expect(read(path)).not.toContain('passenger_name');
    }
  });

  it('the derivations state no money figure', () => {
    const source = read('./passengerView.ts');

    expect(source).not.toContain('inr');
    expect(source).not.toContain('₹');
  });
});

describe('a finished workflow is never presented as a changed booking', () => {
  /*
   * Absorbed from `passengerJourney.ts`, which derived this warning from the GROUP record and is
   * superseded by the per-booking endpoint. The guarantee it existed to hold is the one thing on
   * this screen that would do real harm if it slipped, so it is re-asserted here rather than
   * retired with the module: "the recovery workflow finished" must never read as "your ticket
   * changed".
   */
  const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');

  it('resolved copy claims only that the recorded steps finished', () => {
    const step = deriveNextStep(
      view({ next_step: { state: 'resolved', driven_by_action_type: null, respond_by: null } }),
    );

    const copy = `${step.headline} ${step.detail}`.toLowerCase();
    expect(copy).toContain('recorded');
    for (const overclaim of ['rebooked', 'confirmed', 'ticketed', 'refunded', 'your new flight']) {
      expect(copy).not.toContain(overclaim);
    }
  });

  it('the screen states that no confirmed booking change is published', () => {
    const source = read('./PassengerDisruptionView.tsx');

    expect(source).toContain('No confirmed booking change is published');
    expect(source).toContain('does not mean your');
  });

  it('the screen names what no contract records, so absence is explicit', () => {
    const source = read('./PassengerDisruptionView.tsx');

    for (const absent of ['rebooking', 'seat', 'refund', 'entitlement amount']) {
      expect(source).toContain(absent);
    }
  });
});

import { describe, expect, it } from 'vitest';

import {
  PASSENGER_SAMPLE,
  passengerApi,
  type PassengerActionRecord,
  type PassengerDisruptionResponse,
  type PassengerOption,
  type PassengerSegment,
} from './passengerContracts';
import {
  deriveActionProgress,
  deriveNextStep,
  deriveTripStatus,
  formatDelay,
  hasRevisedTime,
  openImpactCount,
  orderImpacts,
  summariseOptions,
} from './passengerView';

function segment(overrides: Partial<PassengerSegment> = {}): PassengerSegment {
  return {
    segment_ref: 'seg-1',
    flight_number: '6E 2134',
    origin_iata: 'BLR',
    destination_iata: 'DEL',
    scheduled_departure: '2026-08-20T16:10:00Z',
    revised_departure: null,
    scheduled_arrival: '2026-08-20T18:55:00Z',
    revised_arrival: null,
    status: 'on_time',
    delay_minutes: null,
    gate: null,
    is_disrupted: false,
    ...overrides,
  };
}

function option(overrides: Partial<PassengerOption> = {}): PassengerOption {
  return {
    option_ref: 'opt-1',
    kind: 'rebook',
    label: 'Rebook',
    detail: 'Take the next service.',
    available: true,
    unavailable_reason: null,
    requires_agent: false,
    ...overrides,
  };
}

function action(overrides: Partial<PassengerActionRecord> = {}): PassengerActionRecord {
  return {
    action_ref: 'act-1',
    label: 'Checked your connection',
    state: 'succeeded',
    at: '2026-08-20T15:41:00Z',
    detail: 'Compared arrival against the onward departure.',
    ...overrides,
  };
}

function view(overrides: Partial<PassengerDisruptionResponse> = {}): PassengerDisruptionResponse {
  return { ...PASSENGER_SAMPLE, ...overrides };
}

describe('deriveTripStatus', () => {
  it('reports the worst segment, not the first', () => {
    /*
     * The defect this rule prevents: a trip whose first leg is fine and whose second is cancelled is
     * not an on-time trip, and a first-wins rule would tell the passenger it was.
     */
    const status = deriveTripStatus(
      view({
        trip: {
          origin_iata: 'BLR',
          destination_iata: 'JFK',
          segments: [
            segment({ segment_ref: 'a', status: 'on_time' }),
            segment({ segment_ref: 'b', status: 'cancelled', flight_number: 'AI 101' }),
          ],
        },
      }),
    );
    expect(status.token).toBe('cancelled');
    expect(status.drivenBy?.flight_number).toBe('AI 101');
  });

  it('ranks delayed above at risk, and at risk above scheduled', () => {
    const of = (statuses: PassengerSegment['status'][]) =>
      deriveTripStatus(
        view({
          trip: {
            origin_iata: 'BLR',
            destination_iata: 'JFK',
            segments: statuses.map((status, index) =>
              segment({ segment_ref: `s${index}`, status }),
            ),
          },
        }),
      ).token;

    expect(of(['at_risk', 'delayed'])).toBe('delayed');
    expect(of(['scheduled', 'at_risk'])).toBe('at_risk');
    expect(of(['on_time', 'scheduled'])).toBe('scheduled');
    expect(of(['on_time', 'on_time'])).toBe('on_time');
  });

  it('names which segment set the status, so the screen can point at it', () => {
    const status = deriveTripStatus(view());
    expect(status.drivenBy).not.toBeNull();
    expect(status.token).toBe('delayed');
    expect(status.drivenBy?.flight_number).toBe('6E 2134');
  });

  it('counts segments and disrupted segments from the returned array only', () => {
    const status = deriveTripStatus(view());
    expect(status.totalSegments).toBe(2);
    expect(status.disruptedSegments).toBe(1);
  });

  it('always carries a word, so colour is never the only signal', () => {
    const status = deriveTripStatus(view());
    expect(status.label.trim().length).toBeGreaterThan(0);
  });
});

describe('formatDelay', () => {
  it('tells an unpublished delay apart from an on-time flight', () => {
    // The whole point: null means nothing is known, 0 means it is running to schedule.
    expect(formatDelay(null)).toBeNull();
    expect(formatDelay(0)).toBe('on time');
  });

  it('reads minutes and hours the way a person would say them', () => {
    expect(formatDelay(15)).toBe('15m late');
    expect(formatDelay(60)).toBe('1h late');
    expect(formatDelay(195)).toBe('3h 15m late');
  });

  it('handles a flight running early without calling it late', () => {
    expect(formatDelay(-20)).toBe('20m early');
    expect(formatDelay(-90)).toBe('1h 30m early');
  });

  it('never returns an empty string for a real number', () => {
    for (const minutes of [-1, 0, 1, 59, 60, 61, 1440]) {
      expect(formatDelay(minutes)?.trim().length ?? 0).toBeGreaterThan(0);
    }
  });
});

describe('hasRevisedTime', () => {
  it('is false only when neither end has been revised', () => {
    expect(hasRevisedTime(segment())).toBe(false);
    expect(hasRevisedTime(segment({ revised_departure: '2026-08-20T19:25:00Z' }))).toBe(true);
    expect(hasRevisedTime(segment({ revised_arrival: '2026-08-20T22:10:00Z' }))).toBe(true);
  });
});

describe('summariseOptions', () => {
  it('partitions available from unavailable and self-service from agent-assisted', () => {
    const summary = summariseOptions([
      option({ option_ref: 'a' }),
      option({ option_ref: 'b', requires_agent: true }),
      option({
        option_ref: 'c',
        available: false,
        unavailable_reason: 'Beyond the ground-transport limit.',
      }),
    ]);
    expect(summary.total).toBe(3);
    expect(summary.available.map((entry) => entry.option_ref)).toEqual(['a', 'b']);
    expect(summary.unavailable.map((entry) => entry.option_ref)).toEqual(['c']);
    expect(summary.selfService.map((entry) => entry.option_ref)).toEqual(['a']);
    expect(summary.needsAgent.map((entry) => entry.option_ref)).toEqual(['b']);
  });

  it('surfaces an unavailable option that failed to say why', () => {
    /*
     * A greyed row with no reason reads as a broken page rather than a fact about the trip, so the
     * contract defect is reported rather than rendered as a dead row.
     */
    const summary = summariseOptions([
      option({ option_ref: 'x', available: false, unavailable_reason: null }),
      option({ option_ref: 'y', available: false, unavailable_reason: '   ' }),
      option({ option_ref: 'z', available: false, unavailable_reason: 'Not on this route.' }),
    ]);
    expect(summary.missingReason.map((entry) => entry.option_ref)).toEqual(['x', 'y']);
  });

  it('is empty and consistent for no options', () => {
    const summary = summariseOptions([]);
    expect(summary).toMatchObject({ total: 0 });
    expect(summary.available).toEqual([]);
    expect(summary.missingReason).toEqual([]);
  });

  it('finds no missing reasons in the shipped sample', () => {
    expect(summariseOptions(PASSENGER_SAMPLE.options).missingReason).toEqual([]);
  });
});

describe('deriveActionProgress', () => {
  it('counts each state from the returned array', () => {
    const progress = deriveActionProgress([
      action({ action_ref: '1', state: 'succeeded' }),
      action({ action_ref: '2', state: 'succeeded' }),
      action({ action_ref: '3', state: 'executing' }),
      action({ action_ref: '4', state: 'awaiting_approval' }),
      action({ action_ref: '5', state: 'pending', at: null }),
    ]);
    expect(progress).toMatchObject({
      done: 2,
      inFlight: 1,
      awaitingApproval: 1,
      pending: 1,
      total: 5,
      blockedOnPerson: true,
    });
  });

  it('puts a decision waiting on a person ahead of anything running', () => {
    /*
     * A passenger told "sending your confirmation" while a human has not approved the rebooking has
     * been told the wrong thing about their trip, so awaiting_approval wins.
     */
    const progress = deriveActionProgress([
      action({ action_ref: 'run', state: 'executing' }),
      action({ action_ref: 'wait', state: 'awaiting_approval' }),
    ]);
    expect(progress.current?.action_ref).toBe('wait');
    expect(progress.blockedOnPerson).toBe(true);
  });

  it('falls through to running, then to queued', () => {
    expect(
      deriveActionProgress([
        action({ action_ref: 'done', state: 'succeeded' }),
        action({ action_ref: 'run', state: 'executing' }),
        action({ action_ref: 'next', state: 'pending', at: null }),
      ]).current?.action_ref,
    ).toBe('run');

    expect(
      deriveActionProgress([
        action({ action_ref: 'done', state: 'succeeded' }),
        action({ action_ref: 'next', state: 'pending', at: null }),
      ]).current?.action_ref,
    ).toBe('next');
  });

  it('reports nothing current once everything has finished', () => {
    const progress = deriveActionProgress([action({ state: 'succeeded' })]);
    expect(progress.current).toBeNull();
    expect(progress.blockedOnPerson).toBe(false);
  });

  it('handles an empty ledger without inventing a state', () => {
    expect(deriveActionProgress([])).toMatchObject({
      done: 0,
      total: 0,
      current: null,
      blockedOnPerson: false,
    });
  });
});

describe('deriveNextStep', () => {
  it('keeps an unapproved plan reading as unapproved', () => {
    const step = deriveNextStep(view());
    expect(step.token).toBe('awaiting_approval');
    expect(step.awaitingDecision).toBe(true);
    expect(step.passengerMustAct).toBe(false);
  });

  it('maps action_required onto a token the badge already knows', () => {
    const step = deriveNextStep(
      view({
        next_step: {
          state: 'action_required',
          headline: 'Choose an option',
          detail: 'Pick a rebooking or a refund.',
          respond_by: '2026-08-20T21:00:00Z',
        },
      }),
    );
    // No new status vocabulary: `needs_human` already exists in the shared badge map.
    expect(step.token).toBe('needs_human');
    expect(step.passengerMustAct).toBe(true);
    expect(step.awaitingDecision).toBe(false);
    expect(step.respondBy).toBe('2026-08-20T21:00:00Z');
  });

  it('maps monitoring onto scheduled and resolves cleanly', () => {
    expect(
      deriveNextStep(
        view({
          next_step: {
            state: 'monitoring',
            headline: 'Watching',
            detail: 'No action.',
            respond_by: null,
          },
        }),
      ).token,
    ).toBe('scheduled');

    const resolved = deriveNextStep(
      view({
        next_step: {
          state: 'resolved',
          headline: 'Sorted',
          detail: 'You are rebooked.',
          respond_by: null,
        },
      }),
    );
    expect(resolved.token).toBe('resolved');
    expect(resolved.awaitingDecision).toBe(false);
    expect(resolved.passengerMustAct).toBe(false);
  });

  it('carries no deadline when the contract published none', () => {
    expect(deriveNextStep(view()).respondBy).toBeNull();
  });
});

describe('impacts', () => {
  it('puts unresolved consequences first, keeping order within each group', () => {
    const ordered = orderImpacts([
      { impact_ref: 'a', label: 'A', detail: '', resolved: true },
      { impact_ref: 'b', label: 'B', detail: '', resolved: false },
      { impact_ref: 'c', label: 'C', detail: '', resolved: true },
      { impact_ref: 'd', label: 'D', detail: '', resolved: false },
    ]);
    expect(ordered.map((impact) => impact.impact_ref)).toEqual(['b', 'd', 'a', 'c']);
  });

  it('counts only what is still open', () => {
    expect(openImpactCount(PASSENGER_SAMPLE.impacts)).toBe(2);
    expect(openImpactCount([])).toBe(0);
  });
});

describe('the sample payload keeps the promises the screen relies on', () => {
  it('states no monetary amount anywhere', () => {
    /*
     * An entitlement is computed by the policy engine from a reviewed pack. A rupee figure rendered
     * from this sample would be a locally computed entitlement, which the design system forbids and
     * which this screen is the worst possible place to get wrong.
     */
    const serialised = JSON.stringify(PASSENGER_SAMPLE);
    expect(serialised).not.toMatch(/inr/i);
    expect(serialised).not.toMatch(/\u20b9/);
    expect(serialised).not.toMatch(/amount/i);
    expect(serialised).not.toMatch(/compensation_/i);
  });

  it('states no percentage or probability', () => {
    const serialised = JSON.stringify(PASSENGER_SAMPLE);
    expect(serialised).not.toMatch(/%/);
    expect(serialised).not.toMatch(/probability|likelihood|confidence/i);
  });

  it('points at the policy surface for entitlements rather than asserting one', () => {
    expect(PASSENGER_SAMPLE.entitlement_note.trim().length).toBeGreaterThan(0);
    expect(PASSENGER_SAMPLE.entitlement_note.toLowerCase()).toContain('airline');
    expect(PASSENGER_SAMPLE.entitlement_note.toLowerCase()).not.toContain('current law');
  });

  it('uses none of the words the browser gate treats as an unbuilt screen', () => {
    // `verify-console.mjs` fails a route whose DOM matches /not yet built|placeholder/i.
    const reserved = /not yet built|placeholder/i;
    const serialised = JSON.stringify(PASSENGER_SAMPLE);
    expect(serialised).not.toMatch(reserved);
  });

  it('marks itself as not coming from a service', () => {
    expect(passengerApi.isLive).toBe(false);
    expect(PASSENGER_SAMPLE.provenance.kind).toBe('synthetic');
    expect(passengerApi.endpoint).toMatch(/^GET /);
  });

  it('gives every unavailable option a reason', () => {
    for (const entry of PASSENGER_SAMPLE.options.filter((candidate) => !candidate.available)) {
      expect(entry.unavailable_reason?.trim().length ?? 0, entry.option_ref).toBeGreaterThan(0);
    }
  });

  it('never publishes a revised time without a matching status change', () => {
    for (const entry of PASSENGER_SAMPLE.trip.segments) {
      if (hasRevisedTime(entry)) {
        expect(entry.status, entry.segment_ref).not.toBe('on_time');
      }
    }
  });
});

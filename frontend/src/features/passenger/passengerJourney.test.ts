import { describe, expect, it } from 'vitest';

import type { IncidentGroupSummary, PassengerImpact } from '@/api/types';
import { passengerForPnr, passengerJourneyState, passengerLookup } from './passengerJourney';

function group(overrides: Partial<IncidentGroupSummary> = {}): IncidentGroupSummary {
  return {
    id: 1,
    reference: 'GRP-1',
    root_cause: 'weather',
    airport_icao: 'VOBL',
    severity: 'severe',
    state: 'planning',
    opened_at: '2026-08-20T15:36:00Z',
    rollups: {},
    awaiting_approval_count: 0,
    provenance: { kind: 'derived', provider: 'scenario_queries.cascade_rollup' },
    ...overrides,
  };
}

function passenger(overrides: Partial<PassengerImpact> = {}): PassengerImpact {
  return {
    passenger_id: 1,
    passenger_reference: 'PAX-1',
    booking_id: 2,
    pnr: 'K4X8YR',
    priority_index: 37,
    priority_band: 'elevated',
    factors: [],
    rule_version: 'passenger-priority-v1',
    ruleset_hash: 'hash',
    ...overrides,
  };
}

describe('passenger journey projection', () => {
  it('describes a pending decision without operator or booking-change language', () => {
    const state = passengerJourneyState(group({ state: 'planning', awaiting_approval_count: 8 }));
    expect(state).toMatchObject({
      token: 'awaiting_approval',
      label: 'waiting for a decision',
      headline: 'A decision is still needed',
    });
    expect(`${state.headline} ${state.detail}`.toLowerCase()).not.toMatch(
      /operator|orchestrat|rebooked|confirmed booking/,
    );
    expect(state.detail).toContain('You do not need to do anything');
  });

  it('calls the disruption review complete without calling the booking rebooked or confirmed', () => {
    const state = passengerJourneyState(group({ state: 'resolved' }));
    expect(state).toMatchObject({
      token: 'resolved',
      label: 'review complete',
      headline: 'Our review of this disruption is complete',
    });
    expect(`${state.headline} ${state.detail}`.toLowerCase()).not.toMatch(
      /rebooked|ticketed|room assigned|workflow/,
    );
    expect(state.detail).toContain('does not mean your booking changed');
  });

  it('uses critical states and plain language for blocked or failed reviews', () => {
    for (const terminal of ['blocked', 'failed'] as const) {
      const state = passengerJourneyState(group({ state: terminal }));
      expect(state.token).toBe(terminal);
      expect(state.headline).toBe('We could not complete the disruption review');
      expect(state.label).not.toBe('review complete');
      expect(`${state.label} ${state.detail}`.toLowerCase()).not.toMatch(
        /\bsuccess\b|operator|terminal exception/,
      );
    }
  });

  it('uses plain in-progress wording while work continues', () => {
    expect(passengerJourneyState(group({ state: 'executing' }))).toMatchObject({
      token: 'executing',
      label: 'work in progress',
      headline: 'Work on this disruption is in progress',
    });
    expect(passengerJourneyState(group({ state: 'planning' }))).toMatchObject({
      token: 'planning',
      label: 'review in progress',
      headline: 'We are reviewing this disruption',
    });
  });

  it('matches the recorded PNR case-insensitively and returns no synthetic fallback', () => {
    const recorded = passenger();
    expect(passengerForPnr([recorded], 'k4x8yr')).toBe(recorded);
    expect(passengerForPnr([recorded], 'NOTREAL')).toBeNull();
  });

  it('distinguishes a complete PNR lookup from a capped response', () => {
    expect(passengerLookup([], 'NOTREAL', 604, 604)).toEqual({
      passenger: null,
      responseIsComplete: true,
    });
    expect(passengerLookup([], 'NOTREAL', 1000, 1400)).toEqual({
      passenger: null,
      responseIsComplete: false,
    });
  });
});

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
  it('reports incidents awaiting approval from the group count without claiming a booking change', () => {
    const state = passengerJourneyState(group({ state: 'planning', awaiting_approval_count: 8 }));
    expect(state).toMatchObject({ token: 'awaiting_approval', pendingHuman: true });
    expect(state.detail).toContain('8 incidents');
    expect(state.detail.toLowerCase()).not.toMatch(/rebooked|confirmed booking/);
  });

  it('calls the workflow resolved without calling the booking rebooked or confirmed', () => {
    const state = passengerJourneyState(group({ state: 'resolved' }));
    expect(state).toMatchObject({ token: 'resolved', workflowComplete: true });
    expect(`${state.headline} ${state.detail}`.toLowerCase()).not.toMatch(
      /rebooked|ticketed|room assigned/,
    );
    expect(state.detail).toContain('does not confirm a booking change');
  });

  it('does not convert blocked or failed workflow state into a passenger outcome', () => {
    for (const terminal of ['blocked', 'failed'] as const) {
      const state = passengerJourneyState(group({ state: terminal }));
      expect(state.token).toBe(terminal);
      expect(state.detail).toContain('No booking change');
    }
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

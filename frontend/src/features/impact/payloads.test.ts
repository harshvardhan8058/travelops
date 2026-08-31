import { describe, expect, it } from 'vitest';

import type { ActionDetail } from '@/api/types';
import { connectionImpact, IMPACT_ACTION_TYPES, latestOfType } from './payloads';

/**
 * A recorded `check_connections` payload, keyed exactly as the service writes it.
 *
 * The key list is the one `payloads.ts` documents against the real payloads: alternative_flight_ids,
 * alternatives_basis, booking_id, connection_airport_icao, has_special_needs, inbound_delay_minutes,
 * inbound_flight_id, inbound_flight_number, inbound_revised_arrival, inbound_scheduled_arrival,
 * inbound_segment_id, minimum_connection_minutes, onward_flight_id, onward_flight_number,
 * onward_scheduled_departure, onward_segment_id, passenger_id, passenger_reference, pnr,
 * recovered_by_onward_delay, shortfall_minutes, slack_minutes, tier.
 */
function connectionsAction(overrides: Partial<ActionDetail> = {}): ActionDetail {
  return {
    id: 57,
    incident_reference: 'INC-2026-0820-VOBL-01',
    action_type: IMPACT_ACTION_TYPES.connections,
    payload: {
      rule_version: 'connection-v1',
      minimum_connection_minutes: 45,
      near_miss_minutes: 15,
      connecting_itineraries_examined: 642,
      single_segment_itineraries: 540,
      at_risk_count: 22,
      near_miss_count: 3,
      recovered_by_onward_delay_count: 4,
      // Recorded by the service and deliberately not read by `connectionImpact`. Present here so the
      // parser is exercised against a payload carrying more than it consumes.
      at_risk_by_flight: { '6E 2134': 8 },
      margin_note: 'Slack is measured against the revised inbound arrival.',
      alternatives_note: 'Alternatives are same-day departures on the recorded route.',
      at_risk: [
        {
          booking_id: 176,
          pnr: 'K4X8YR',
          passenger_id: 176,
          passenger_reference: 'PAX-00176',
          inbound_flight_id: 1,
          inbound_flight_number: '6E 2134',
          onward_flight_id: 9,
          onward_flight_number: 'AI 101',
          connection_airport_icao: 'VIDP',
          inbound_scheduled_arrival: '2026-08-20T18:55:00Z',
          inbound_revised_arrival: '2026-08-20T22:10:00Z',
          onward_scheduled_departure: '2026-08-20T20:40:00Z',
          inbound_segment_id: 411,
          onward_segment_id: 412,
          minimum_connection_minutes: 45,
          inbound_delay_minutes: 195,
          slack_minutes: -150,
          shortfall_minutes: 195,
          alternative_flight_ids: [12, 14],
          alternatives_basis: 'same-day, same route',
          tier: 'gold',
          has_special_needs: true,
          recovered_by_onward_delay: false,
        },
        {
          booking_id: 219,
          pnr: 'M2QW7T',
          passenger_id: 219,
          passenger_reference: 'PAX-00219',
          inbound_flight_number: '6E 2134',
          onward_flight_number: 'UK 705',
          connection_airport_icao: 'VIDP',
          // Absent on purpose: the service recorded no shortfall for this row.
          slack_minutes: -20,
          alternative_flight_ids: [],
          tier: 'economy',
          has_special_needs: false,
          recovered_by_onward_delay: false,
        },
      ],
      near_misses: [
        {
          booking_id: 300,
          pnr: 'Z9PL2K',
          inbound_flight_number: '6E 811',
          onward_flight_number: 'AI 503',
          connection_airport_icao: 'VIDP',
          slack_minutes: 12,
          alternative_flight_ids: [],
          recovered_by_onward_delay: true,
        },
      ],
    },
    ...overrides,
  } as unknown as ActionDetail;
}

describe('connectionImpact — the successful payload', () => {
  it('reads the rows and the service’s own totals from the recorded payload', () => {
    const impact = connectionImpact(connectionsAction());
    expect(impact.actionId).toBe(57);
    expect(impact.incidentReference).toBe('INC-2026-0820-VOBL-01');
    expect(impact.ruleVersion).toBe('connection-v1');
    expect(impact.atRisk).toHaveLength(2);
    expect(impact.nearMisses).toHaveLength(1);
    // The server's totals, alongside the row counts. Both are shown so a disagreement is visible.
    expect(impact.atRiskCount).toBe(22);
    expect(impact.nearMissCount).toBe(3);
    expect(impact.examined).toBe(642);
    expect(impact.minimumConnectionMinutes).toBe(45);
    expect(impact.nearMissMinutes).toBe(15);
  });

  it('surfaces a disagreement between the recorded total and the rows rather than reconciling it', () => {
    /*
     * 22 recorded at risk, 2 rows returned. The screen shows both; nothing here picks a winner or
     * sums its way to a third number.
     */
    const impact = connectionImpact(connectionsAction());
    expect(impact.atRiskCount).not.toBe(impact.atRisk.length);
  });

  it('maps a row field by field, keeping the service’s own tier and flags', () => {
    const [first] = connectionImpact(connectionsAction()).atRisk;
    expect(first).toMatchObject({
      bookingId: 176,
      pnr: 'K4X8YR',
      passengerReference: 'PAX-00176',
      inboundFlight: '6E 2134',
      onwardFlight: 'AI 101',
      connectionAirport: 'VIDP',
      shortfallMinutes: 195,
      slackMinutes: -150,
      inboundDelayMinutes: 195,
      tier: 'gold',
      hasSpecialNeeds: true,
      recoveredByOnwardDelay: false,
      alternativesBasis: 'same-day, same route',
    });
    expect(first?.alternativeFlightIds).toEqual([12, 14]);
  });

  it('keeps an absent field absent instead of turning it into zero', () => {
    /*
     * The rule that makes this screen trustworthy: `shortfall_minutes: null` renders as "not
     * recorded", where a zero would claim the service measured no shortfall.
     */
    const [, second] = connectionImpact(connectionsAction()).atRisk;
    expect(second?.shortfallMinutes).toBeNull();
    expect(second?.inboundDelayMinutes).toBeNull();
    expect(second?.passengerReference).toBe('PAX-00219');
    expect(second?.alternativeFlightIds).toEqual([]);
  });

  it('reads near misses through the same row parser', () => {
    const [near] = connectionImpact(connectionsAction()).nearMisses;
    expect(near?.pnr).toBe('Z9PL2K');
    expect(near?.slackMinutes).toBe(12);
    expect(near?.recoveredByOnwardDelay).toBe(true);
    // Not recorded on a near miss, and not invented here.
    expect(near?.shortfallMinutes).toBeNull();
    expect(near?.tier).toBeNull();
  });

  it('renders the service’s notes verbatim rather than composing its own', () => {
    const impact = connectionImpact(connectionsAction());
    expect(impact.marginNote).toBe('Slack is measured against the revised inbound arrival.');
    expect(impact.alternativesNote).toBe(
      'Alternatives are same-day departures on the recorded route.',
    );
  });
});

describe('connectionImpact — a payload that carries nothing', () => {
  it('produces empty arrays and null totals for an empty payload, never a fabricated zero', () => {
    /*
     * Distinct from the not-assessed case, which the screen reports through `availability.ts`: here
     * an action DID run, so empty arrays are a genuine finding while the absent totals stay absent.
     */
    const impact = connectionImpact(connectionsAction({ payload: {} } as Partial<ActionDetail>));
    expect(impact.atRisk).toEqual([]);
    expect(impact.nearMisses).toEqual([]);
    expect(impact.atRiskCount).toBeNull();
    expect(impact.examined).toBeNull();
    expect(impact.ruleVersion).toBeNull();
    expect(impact.marginNote).toBeNull();
  });

  it('survives a payload whose arrays hold the wrong type rather than crashing the screen', () => {
    const impact = connectionImpact(
      connectionsAction({
        payload: { at_risk: 'not-an-array', near_misses: [null, 7, 'x'] },
      } as unknown as Partial<ActionDetail>),
    );
    expect(impact.atRisk).toEqual([]);
    expect(impact.nearMisses).toEqual([]);
  });
});

describe('latestOfType', () => {
  it('finds the connections action among a mixed ledger', () => {
    const found = latestOfType(
      [
        connectionsAction({ id: 10, action_type: 'notify_passengers' } as Partial<ActionDetail>),
        connectionsAction({ id: 11 }),
      ],
      IMPACT_ACTION_TYPES.connections,
    );
    expect(found?.id).toBe(11);
  });

  it('returns null when the action never ran, which is what drops the finding', () => {
    // This null is exactly the input that used to remove every mention of connections from the screen.
    expect(
      latestOfType(
        [connectionsAction({ action_type: 'notify_passengers' } as Partial<ActionDetail>)],
        IMPACT_ACTION_TYPES.connections,
      ),
    ).toBeNull();
    expect(latestOfType([], IMPACT_ACTION_TYPES.connections)).toBeNull();
  });
});

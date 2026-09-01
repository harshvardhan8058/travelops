import { describe, expect, it } from 'vitest';

import { incidentNavTarget } from './navScope';
import type { FlightRow } from '@/api/types';

function flight(over: Partial<FlightRow> = {}): FlightRow {
  return {
    id: 1,
    flight_number: '6E 2134',
    airline_code: '6E',
    origin_icao: 'VOBL',
    destination_icao: 'VIDP',
    scheduled_departure: '2026-08-20T15:36:00Z',
    estimated_departure: null,
    status: 'delayed',
    delay_minutes: 0,
    passengers: 174,
    incident_reference: null,
    ...over,
  } as unknown as FlightRow;
}

describe('incidentNavTarget', () => {
  it('opens the most delayed incident, not whichever row the board returned first', () => {
    /*
     * The seeded cascade returns a 70-minute inbound carrying 12 passengers ahead of the
     * 420-minute departure carrying 174 that the demo is actually about. Board order alone put
     * the rail on the smallest incident in the cascade.
     */
    const target = incidentNavTarget([
      flight({ id: 1, incident_reference: 'INC-SMALL', delay_minutes: 70 }),
      flight({ id: 2 }),
      flight({ id: 3, incident_reference: 'INC-PRIMARY', delay_minutes: 420 }),
      flight({ id: 4, incident_reference: 'INC-MID', delay_minutes: 140 }),
    ]);

    expect(target).toBe('INC-PRIMARY');
  });

  it('breaks a tie by board order, so the target is stable', () => {
    const target = incidentNavTarget([
      flight({ id: 1, incident_reference: 'INC-FIRST', delay_minutes: 60 }),
      flight({ id: 2, incident_reference: 'INC-SECOND', delay_minutes: 60 }),
    ]);

    expect(target).toBe('INC-FIRST');
  });

  it('still finds an incident when no delay is recorded', () => {
    const target = incidentNavTarget([
      flight({ id: 1 }),
      flight({ id: 2, incident_reference: 'INC-A', delay_minutes: 0 }),
    ]);

    expect(target).toBe('INC-A');
  });

  it('answers null when nothing is open, so the rail can disable rather than dead-end', () => {
    /*
     * The state a dataset restore leaves. The rail used to keep offering a hardcoded reference
     * here, and each of the seven incident-scoped entries led to a screen that issued three or
     * four requests for an entity the operator had just deleted.
     */
    expect(incidentNavTarget([flight(), flight({ id: 2 })])).toBeNull();
  });

  it('answers null before the board has loaded, rather than guessing', () => {
    expect(incidentNavTarget(undefined)).toBeNull();
  });

  it('ignores an empty reference as carefully as a missing one', () => {
    expect(incidentNavTarget([flight({ incident_reference: '' })])).toBeNull();
  });
});

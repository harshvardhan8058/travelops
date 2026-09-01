import { describe, expect, it } from 'vitest';

import type { ActionRecord, BookingSegment, Provenance } from '@/api/types';
import { operationalStatus, summariseTrip } from './tripSummary';

const PROVENANCE: Provenance = { kind: 'synthetic', provider: 'generator', source_ref: null };

function segment(overrides: Partial<BookingSegment>): BookingSegment {
  return {
    segment_order: 1,
    flight_id: 1,
    flight_number: '6E 811',
    origin_icao: 'VOBL',
    destination_icao: 'VABB',
    scheduled_departure: '2026-08-20T15:55:00Z',
    estimated_departure: null,
    delay_minutes: 0,
    status: 'scheduled',
    incident_reference: null,
    provenance: PROVENANCE,
    ...overrides,
  };
}

function action(overrides: Partial<ActionRecord>): ActionRecord {
  return {
    id: 1,
    plan_task_id: 1,
    assurance_id: 1,
    human_decision_id: null,
    actor: 'hotel_service',
    status: 'success',
    reason: 'done',
    cost_inr: null,
    idempotency_key: 'k',
    executed_at: '2026-08-20T16:00:00Z',
    provenance_kind: 'synthetic',
    ...overrides,
  };
}

describe('summariseTrip', () => {
  it('orders segments by segment_order, not array order', () => {
    const trip = summariseTrip([
      segment({ segment_order: 2, origin_icao: 'VABB', destination_icao: 'VIDP' }),
      segment({ segment_order: 1, origin_icao: 'VOBL', destination_icao: 'VABB' }),
    ]);
    expect(trip.segments.map((s) => s.segment_order)).toEqual([1, 2]);
  });

  it('builds a route label through every stop, direct trip', () => {
    const trip = summariseTrip([segment({ origin_icao: 'VOBL', destination_icao: 'VIDP' })]);
    expect(trip.routeLabel).toBe('VOBL → VIDP');
    expect(trip.isConnecting).toBe(false);
  });

  it('builds a route label through every stop, connecting trip', () => {
    const trip = summariseTrip([
      segment({ segment_order: 1, origin_icao: 'VOBL', destination_icao: 'VABB' }),
      segment({ segment_order: 2, origin_icao: 'VABB', destination_icao: 'VIDP' }),
    ]);
    expect(trip.routeLabel).toBe('VOBL → VABB → VIDP');
    expect(trip.isConnecting).toBe(true);
  });

  it('finds the disrupted segment in journey order, not by delay size', () => {
    // The first leg is fine; the connection is what is actually broken. A passenger reading
    // their trip in order should see the disruption on the leg where it is, not on whichever
    // leg happens to carry the largest delay figure.
    const trip = summariseTrip([
      segment({ segment_order: 1, incident_reference: null, delay_minutes: 0 }),
      segment({
        segment_order: 2,
        incident_reference: 'INC-2026-0820-VOBL-04',
        delay_minutes: 200,
      }),
    ]);
    expect(trip.disruptedSegment?.segment_order).toBe(2);
  });

  it('reports no disrupted segment when none carries an incident', () => {
    const trip = summariseTrip([segment({}), segment({ segment_order: 2 })]);
    expect(trip.disruptedSegment).toBeNull();
  });

  it('returns an empty route label for no segments, rather than throwing', () => {
    expect(summariseTrip([]).routeLabel).toBe('');
  });
});

describe('operationalStatus', () => {
  it('reads reviewing, not resolved, before the incident reaches a terminal state', () => {
    const status = operationalStatus('executing', [action({ status: 'success' })]);
    expect(status.workflowResolved).toBe(false);
    expect(status.passengerImpactOutstanding).toBe(false);
  });

  it('is fully resolved when the workflow finished with nothing left needing a person', () => {
    const status = operationalStatus('resolved', [action({ status: 'success' })]);
    expect(status.workflowResolved).toBe(true);
    expect(status.passengerImpactOutstanding).toBe(false);
    expect(status.outstanding).toHaveLength(0);
  });

  it('distinguishes workflow-resolved from passenger-impact-outstanding, using the same rule the operator screen uses', () => {
    const status = operationalStatus('resolved', [
      action({ id: 1, status: 'success' }),
      action({ id: 2, status: 'needs_human', reason: 'capacity short by 16 rooms' }),
    ]);
    expect(status.workflowResolved).toBe(true);
    expect(status.passengerImpactOutstanding).toBe(true);
    expect(status.outstanding).toEqual([
      { actionId: 2, actionType: null, reason: 'capacity short by 16 rooms' },
    ]);
  });

  it('has no open incident to report as null, not as a guessed state', () => {
    const status = operationalStatus(null, undefined);
    expect(status.incidentState).toBeNull();
    expect(status.workflowResolved).toBe(false);
    expect(status.passengerImpactOutstanding).toBe(false);
    expect(status.outstanding).toHaveLength(0);
  });
});

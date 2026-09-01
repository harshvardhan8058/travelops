/**
 * Trip-level derivations for the passenger view. Pure, dependency-free, unit-testable.
 *
 * The Passenger View's first section is the trip itself — which this repository could not show
 * until `GET /bookings/{pnr}` existed, because `PassengerImpact` (the only contract the screen had
 * before) carries a priority ranking and no flight facts at all. This module turns the ordered
 * segment rows that endpoint returns into what the screen needs: which segment (if any) is the one
 * with an open incident, and the "operational workflow resolved vs passenger impact still
 * outstanding" distinction the spec requires — computed from the SAME `IncidentState` and
 * `ActionRecord` rows the operator's Recovery Workspace reads, via the same `outstandingDemand`
 * helper, so passenger and operator can never disagree about what "resolved" means.
 *
 * Owner: Stream D.
 */

import type { ActionRecord, BookingSegment } from '@/api/types';
import { outstandingDemand, type OutstandingItem } from '@/features/incident/outstandingDemand';

export interface TripSummary {
  /** Ordered by `segment_order`, never insertion order. */
  segments: readonly BookingSegment[];
  /**
   * The first segment (in journey order) with an open incident, or `null` when none of this
   * trip's flights has one. A connecting itinerary can have a fine first leg and a delayed
   * second one; journey order, not delay size, is what a passenger reading their trip expects.
   */
  disruptedSegment: BookingSegment | null;
  /** `VOBL -> VABB -> VIDP` for a two-segment trip; `VOBL -> VIDP` for a direct one. */
  routeLabel: string;
  isConnecting: boolean;
}

export function summariseTrip(segments: readonly BookingSegment[]): TripSummary {
  const ordered = [...segments].sort((a, b) => a.segment_order - b.segment_order);
  const disruptedSegment = ordered.find((segment) => segment.incident_reference !== null) ?? null;
  const first = ordered[0];
  const stops = first ? [first.origin_icao, ...ordered.map((s) => s.destination_icao)] : [];

  return {
    segments: ordered,
    disruptedSegment,
    routeLabel: stops.join(' → '),
    isConnecting: ordered.length > 1,
  };
}

export interface OperationalStatus {
  /** The incident's own state, verbatim, or `null` when this trip has no open incident to read. */
  incidentState: string | null;
  /**
   * The workflow behind this trip's disruption has finished — every task was dispatched and the
   * run has nowhere left to go. This is the operator's `resolved`, read from the same field.
   */
  workflowResolved: boolean;
  /**
   * The workflow finished, but at least one recorded action still needs a person — the hotel
   * shortfall case, among others. `workflowResolved` alone would overstate this trip's outcome,
   * exactly as it would on the operator's own Recovery Workspace.
   */
  passengerImpactOutstanding: boolean;
  /** The specific outstanding items, backend reasons verbatim. Empty when nothing is owed. */
  outstanding: OutstandingItem[];
}

/**
 * `null` inputs (no open incident, or its detail not yet loaded) is a real, renderable state —
 * "reviewing" — not a failure, so this never throws and never guesses.
 */
export function operationalStatus(
  incidentState: string | null | undefined,
  actions: readonly ActionRecord[] | undefined,
): OperationalStatus {
  const state = incidentState ?? null;
  const workflowResolved = state === 'resolved';
  const outstanding = outstandingDemand(actions);
  return {
    incidentState: state,
    workflowResolved,
    passengerImpactOutstanding: workflowResolved && outstanding.length > 0,
    outstanding,
  };
}

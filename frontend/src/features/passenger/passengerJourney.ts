import type { IncidentGroupSummary, PassengerImpact } from '@/api/types';

export interface PassengerJourneyState {
  token: string;
  label: string;
  headline: string;
  detail: string;
}

/**
 * Passenger-friendly wording derived only from the current group record.
 *
 * `resolved` means the disruption review finished. It does not mean this booking was rebooked,
 * refunded, ticketed, or assigned a room; no endpoint publishes those outcomes today.
 */
export function passengerJourneyState(group: IncidentGroupSummary): PassengerJourneyState {
  if (group.state === 'resolved') {
    return {
      token: 'resolved',
      label: 'review complete',
      headline: 'Our review of this disruption is complete',
      detail: 'The review has finished, but that does not mean your booking changed.',
    };
  }

  if (group.state === 'blocked') {
    return {
      token: 'blocked',
      label: 'review needs attention',
      headline: 'We could not complete the disruption review',
      detail: 'A problem stopped the review before it could be completed.',
    };
  }

  if (group.state === 'failed') {
    return {
      token: 'failed',
      label: 'review unsuccessful',
      headline: 'We could not complete the disruption review',
      detail: 'A problem stopped the review before it could be completed.',
    };
  }

  if (group.awaiting_approval_count > 0 || group.state === 'awaiting_approval') {
    return {
      token: 'awaiting_approval',
      label: 'waiting for a decision',
      headline: 'A decision is still needed',
      detail:
        'The review is paused while a decision is made. You do not need to do anything in TravelOps right now.',
    };
  }

  if (group.state === 'executing') {
    return {
      token: 'executing',
      label: 'work in progress',
      headline: 'Work on this disruption is in progress',
      detail: 'The review is moving forward.',
    };
  }

  return {
    token: group.state,
    label: 'review in progress',
    headline: 'We are reviewing this disruption',
    detail: 'We are still working through the available information.',
  };
}

export interface PassengerLookup {
  passenger: PassengerImpact | null;
  responseIsComplete: boolean;
}

export function passengerLookup(
  passengers: readonly PassengerImpact[],
  pnr: string,
  returned: number,
  passengersAssessed: number,
): PassengerLookup {
  const normalised = pnr.trim().toUpperCase();
  return {
    passenger: passengers.find((passenger) => passenger.pnr.toUpperCase() === normalised) ?? null,
    responseIsComplete: returned >= passengersAssessed,
  };
}

export function passengerForPnr(
  passengers: readonly PassengerImpact[],
  pnr: string,
): PassengerImpact | null {
  return passengerLookup(passengers, pnr, passengers.length, passengers.length).passenger;
}

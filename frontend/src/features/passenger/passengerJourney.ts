import type { IncidentGroupSummary, PassengerImpact } from '@/api/types';

export interface PassengerJourneyState {
  token: string;
  label: string;
  headline: string;
  detail: string;
}

/**
 * Passenger-friendly wording derived from a recorded state and whether a decision is pending.
 *
 * `resolved` means the disruption review finished. It does not mean this booking was rebooked,
 * refunded, ticketed, or assigned a room; no endpoint publishes those outcomes today.
 *
 * Shared by both callers that need this wording — the group-level projection below, and the
 * booking's own linked incident in `PassengerDisruptionView` — so there is exactly one mapping
 * from a recorded state to passenger words, not two copies that could drift apart.
 */
export function journeyStateFor(
  state: string,
  awaitingApprovalCount: number,
): PassengerJourneyState {
  if (state === 'resolved') {
    return {
      token: 'resolved',
      label: 'review complete',
      headline: 'Our review of this disruption is complete',
      detail: 'The review has finished, but that does not mean your booking changed.',
    };
  }

  if (state === 'blocked') {
    // `blocked` is not a malfunction. It is most often a person needing to decide something the
    // automated review is not allowed to decide alone — an accommodation shortfall, a rejected
    // approval, a conflict the gate will not wave through — the same everyday case
    // `awaiting_approval` describes, just one step further along. Wording this like a system
    // failure (as it read before, identically to `failed` below) told a passenger something had
    // gone wrong when nothing had; the operator console never makes that claim for the same state.
    return {
      token: 'blocked',
      label: "needs a person's decision",
      headline: "Your booking's review is paused for a decision",
      detail:
        'The automated review reached a point that needs a person to decide the next step. ' +
        'That is a normal part of some reviews, not a sign that anything went wrong. You do not ' +
        'need to do anything here unless your airline contacts you directly.',
    };
  }

  if (state === 'failed') {
    // Genuinely distinct from `blocked`: `failed` is reserved for the review itself breaking,
    // not for a decision a person still needs to make. This is the one state where "a problem
    // stopped the review" is an accurate sentence.
    return {
      token: 'failed',
      label: 'review unsuccessful',
      headline: 'We could not complete the disruption review',
      detail: 'A problem stopped the review before it could be completed.',
    };
  }

  if (awaitingApprovalCount > 0 || state === 'awaiting_approval') {
    return {
      token: 'awaiting_approval',
      label: 'waiting for a decision',
      headline: 'A decision is still needed',
      detail:
        'The review is paused while a decision is made. You do not need to do anything in TravelOps right now.',
    };
  }

  if (state === 'executing') {
    return {
      token: 'executing',
      label: 'work in progress',
      headline: 'Work on this disruption is in progress',
      detail: 'The review is moving forward.',
    };
  }

  return {
    token: state,
    label: 'review in progress',
    headline: 'We are reviewing this disruption',
    detail: 'We are still working through the available information.',
  };
}

/** Thin wrapper over {@link journeyStateFor} for the current-group record. */
export function passengerJourneyState(group: IncidentGroupSummary): PassengerJourneyState {
  return journeyStateFor(group.state, group.awaiting_approval_count);
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

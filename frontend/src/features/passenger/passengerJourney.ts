import type { IncidentGroupSummary, PassengerImpact } from '@/api/types';

export interface PassengerJourneyState {
  token: string;
  label: string;
  headline: string;
  detail: string;
  pendingHuman: boolean;
  workflowComplete: boolean;
}

/**
 * Passenger-friendly wording derived only from the current group record.
 *
 * `resolved` means the recovery workflow finished. It does not mean this booking was rebooked,
 * refunded, ticketed, or assigned a room; no endpoint publishes those outcomes today.
 */
export function passengerJourneyState(group: IncidentGroupSummary): PassengerJourneyState {
  if (group.state === 'resolved') {
    return {
      token: 'resolved',
      label: 'recovery workflow resolved',
      headline: 'The disruption recovery workflow is complete',
      detail:
        'TravelOps has finished the recorded operational workflow. This does not confirm a booking change; no passenger outcome contract has published one.',
      pendingHuman: false,
      workflowComplete: true,
    };
  }

  if (group.state === 'blocked' || group.state === 'failed') {
    return {
      token: group.state,
      label: `recovery ${group.state}`,
      headline: `The recovery workflow is ${group.state}`,
      detail:
        'The operational workflow reached a terminal exception. No booking change or passenger option is inferred from that state.',
      pendingHuman: false,
      workflowComplete: true,
    };
  }

  if (group.awaiting_approval_count > 0 || group.state === 'awaiting_approval') {
    return {
      token: 'awaiting_approval',
      label: 'incidents awaiting operator approval',
      headline: 'The recovery is waiting on an operator',
      detail: `${group.awaiting_approval_count} incident${group.awaiting_approval_count === 1 ? '' : 's'} in this disruption group still await operator approval. Nothing on this booking is represented as confirmed.`,
      pendingHuman: true,
      workflowComplete: false,
    };
  }

  if (group.state === 'executing') {
    return {
      token: 'executing',
      label: 'recovery executing',
      headline: 'TravelOps is executing the approved recovery',
      detail:
        'Operational actions are in progress. No booking outcome is shown until a passenger contract publishes one.',
      pendingHuman: false,
      workflowComplete: false,
    };
  }

  return {
    token: group.state,
    label: `recovery ${group.state.replace(/_/g, ' ')}`,
    headline: 'TravelOps is assessing the disruption',
    detail:
      'The operational workflow is still being prepared or assured. No booking change has been published.',
    pendingHuman: false,
    workflowComplete: false,
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

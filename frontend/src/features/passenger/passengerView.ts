/**
 * Derivation for the passenger disruption view. Pure, dependency-free, unit-testable.
 *
 * The screen renders; this decides. Same reason as every other feature here: the test runner is a
 * node environment collecting `*.test.ts`, so anything living inside the component is untested by
 * construction — and the decisions on this screen are the ones where a silent wrong answer is worst,
 * because the reader is a passenger who cannot cross-check it against the ledger.
 *
 * Everything below operates on `PassengerDisruptionResponse`, which is a projection of recorded
 * rows. So the rule for this module is narrow and absolute: **it may phrase what the contract says,
 * and it may not add to it.** Composing "your connection at VIDP no longer holds" out of a recorded
 * airport code and a recorded shortfall is rendering. Deciding that a room is held, or that a seat
 * exists, or that a rebooking is confirmed, would be inventing — and each of those is something the
 * backend deliberately cannot tell us.
 *
 * Four rules:
 *
 *   1. **Absent is not zero.** `delay_minutes: null` means no revision has been published;
 *      `delay_minutes: 0` means on time. Rendering both as "0m" would tell someone their flight is
 *      fine when nothing is known about it.
 *   2. **Nothing is confirmed until it is.** Work behind a `needs_human` gate is not in progress.
 *      `deriveNextStep` keeps the distinction the orchestrator makes.
 *   3. **No aggregate beyond a count of what was returned.** Counting an array is the one aggregate
 *      permitted; nothing here averages, scores or ranks.
 *   4. **An option states its basis.** A reachable departure is not an available seat, and the copy
 *      this module produces says which one it is.
 *
 * Owner: Stream D.
 */

import type {
  PassengerAction,
  PassengerActionState,
  PassengerDisruptionResponse,
  PassengerOption,
  PassengerSegmentOut,
} from '@/api/types';

// ---------------------------------------------------------------- trip status

/**
 * A status token that `StateBadge` already knows, so this feature adds no colour vocabulary.
 *
 * The order is the severity order the badge map already encodes: a cancelled segment outranks a
 * delayed one, which outranks an at-risk one.
 */
export type TripStatusToken = 'cancelled' | 'delayed' | 'at_risk' | 'scheduled' | 'on_time';

const STATUS_RANK: Record<TripStatusToken, number> = {
  cancelled: 4,
  delayed: 3,
  at_risk: 2,
  scheduled: 1,
  on_time: 0,
};

const TRIP_STATUS_LABEL: Record<TripStatusToken, string> = {
  cancelled: 'flight cancelled',
  delayed: 'running late',
  at_risk: 'connection at risk',
  scheduled: 'scheduled',
  on_time: 'on time',
};

export interface TripStatus {
  token: TripStatusToken;
  /** Words, because colour is never the only signal. */
  label: string;
  /** The segment that set the status, so the screen can point at it. */
  drivenBy: PassengerSegmentOut | null;
  disruptedSegments: number;
  totalSegments: number;
}

/**
 * The worst thing happening to the trip, and which segment caused it.
 *
 * Worst rather than first: a passenger whose second leg is cancelled while the first runs on time is
 * not having an on-time trip, and a first-segment-wins rule would say they were.
 */
export function deriveTripStatus(view: PassengerDisruptionResponse): TripStatus {
  const segments = view.trip.segments;
  let token: TripStatusToken = 'on_time';
  let drivenBy: PassengerSegmentOut | null = null;

  for (const segment of segments) {
    const candidate = segment.status as TripStatusToken;
    const rank = STATUS_RANK[candidate];
    if (rank === undefined) continue;
    if (drivenBy === null || rank > STATUS_RANK[token]) {
      token = candidate;
      drivenBy = segment;
    }
  }

  return {
    token,
    label: TRIP_STATUS_LABEL[token],
    drivenBy,
    disruptedSegments: segments.filter((segment) => segment.is_disrupted).length,
    totalSegments: segments.length,
  };
}

// ---------------------------------------------------------------- delay

/**
 * A delay in words, distinguishing "no revision published" from "on time".
 *
 * Returns `null` for an unpublished delay so the caller renders an absence through the same component
 * every other absent contract value goes through, rather than printing a zero.
 */
export function formatDelay(minutes: number | null): string | null {
  if (minutes === null) return null;
  if (minutes === 0) return 'on time';
  const magnitude = Math.abs(minutes);
  const hours = Math.floor(magnitude / 60);
  const remainder = magnitude % 60;
  const parts: string[] = [];
  if (hours > 0) parts.push(`${hours}h`);
  if (remainder > 0 || hours === 0) parts.push(`${remainder}m`);
  const span = parts.join(' ');
  return minutes < 0 ? `${span} early` : `${span} late`;
}

/** Whether a revised departure was published for this segment at all. */
export function hasRevisedTime(segment: PassengerSegmentOut): boolean {
  return segment.estimated_departure !== null;
}

// ---------------------------------------------------------------- consequences

export interface ConsequenceView {
  /** Stable key for rendering. Derived from the recorded fact, not an index. */
  key: string;
  label: string;
  detail: string;
  /** True when TravelOps has already dealt with this. */
  resolved: boolean;
}

/**
 * The consequences this booking actually carries, from recorded findings only.
 *
 * There is no `impacts` array on the contract, and deliberately so: the previous screen had one and
 * it was filled by hand with plausible-sounding entries ("bags stay checked through"), none of which
 * any row supported. This builds the list from the two things that ARE recorded — the connection
 * assessment and the priority factors — and returns an empty list when neither is present. An empty
 * consequence list is a true statement about a trip nobody has assessed yet.
 */
export function deriveConsequences(view: PassengerDisruptionResponse): ConsequenceView[] {
  const out: ConsequenceView[] = [];

  const connection = view.connection;
  if (connection) {
    const short = Math.abs(connection.shortfall_minutes);
    out.push({
      key: 'connection',
      label: `${connection.onward_flight_number} connection at ${connection.connection_airport_icao} no longer holds`,
      detail:
        `The revised arrival leaves ${short} minute${short === 1 ? '' : 's'} less than the ` +
        `${connection.minimum_connection_minutes}-minute minimum this airport requires.`,
      // Recorded as broken as sold. `recovered_by_onward_delay` says the onward flight is itself
      // late enough that it may still be caught — reported, never treated as a fix.
      resolved: false,
    });
    if (connection.recovered_by_onward_delay) {
      out.push({
        key: 'connection-onward-delay',
        label: `${connection.onward_flight_number} is itself delayed`,
        detail:
          'The onward flight is running late enough that the connection may still be made. ' +
          'It is still recorded as broken as sold, so nothing has been changed on your booking.',
        resolved: false,
      });
    }
  }

  // Priority factors are the ruleset's own named reasons. Rendered as recorded, never re-weighted.
  for (const factor of view.priority?.factors ?? []) {
    if (factor.factor === 'broken_connection') continue; // Already stated above, from the source.
    out.push({
      key: `factor-${factor.factor}`,
      label: factor.factor.replace(/_/g, ' '),
      detail: `Recorded from ${factor.source} when your handling priority was assessed.`,
      resolved: false,
    });
  }

  return out;
}

/** How many consequences are still open. A count of a returned array, nothing more. */
export function openConsequenceCount(consequences: readonly ConsequenceView[]): number {
  return consequences.filter((entry) => !entry.resolved).length;
}

// ---------------------------------------------------------------- options

export interface OptionSummary {
  flights: PassengerOption[];
  rooms: PassengerOption[];
  needsAgent: PassengerOption[];
  total: number;
  /**
   * Options whose basis is not a firm reservation. Surfaced rather than smoothed over: a reachable
   * departure and a held room are different promises and the screen must not merge them.
   */
  provisional: PassengerOption[];
}

export function summariseOptions(options: readonly PassengerOption[]): OptionSummary {
  return {
    flights: options.filter((option) => option.kind === 'alternative_flight'),
    rooms: options.filter((option) => option.kind === 'hotel_room'),
    needsAgent: options.filter((option) => option.requires_agent),
    total: options.length,
    provisional: options.filter((option) => option.basis !== 'recorded_reservation'),
  };
}

/** The sentence that must accompany an option, keyed on its recorded basis. */
export function optionBasisNote(option: PassengerOption): string {
  switch (option.basis) {
    case 'schedule_feasible_only':
      return 'Reachable by the timetable. No seat has been held and none is promised.';
    case 'simulated_reservation':
      return 'Recorded as a simulated hold in this environment, not a confirmed booking.';
    case 'recorded_reservation':
      return 'A reservation is recorded against your booking.';
  }
}

// ---------------------------------------------------------------- actions

export interface ActionProgress {
  done: number;
  inFlight: number;
  pending: number;
  awaitingApproval: number;
  refused: number;
  total: number;
  /**
   * The action a reader should look at: whatever is blocked on a person first, then whatever is
   * running, then the next thing queued. Null when everything has finished.
   */
  current: PassengerAction | null;
  /** True when at least one action cannot proceed without a human decision. */
  blockedOnPerson: boolean;
}

/**
 * Folds the action ledger into counts and a single "what matters now".
 *
 * Awaiting-approval outranks executing on purpose: a passenger reading "sending your confirmation"
 * while a human has not approved the rebooking has been told the wrong thing about their trip.
 */
export function deriveActionProgress(actions: readonly PassengerAction[]): ActionProgress {
  const count = (state: PassengerActionState) =>
    actions.filter((action) => action.state === state).length;

  const awaiting = actions.find((action) => action.awaiting_human) ?? null;
  const executing = actions.find((action) => action.state === 'executing') ?? null;
  const pending = actions.find((action) => action.state === 'pending') ?? null;

  return {
    done: count('succeeded'),
    inFlight: count('executing'),
    pending: count('pending'),
    awaitingApproval: count('awaiting_approval'),
    // A service that declined is neither done nor pending, and collapsing it into either would
    // report a refusal as progress.
    refused: count('failed') + count('needs_human'),
    total: actions.length,
    current: awaiting ?? executing ?? pending,
    blockedOnPerson: awaiting !== null,
  };
}

/** Passenger-facing wording for a recorded action type. Unknown types read as themselves. */
export function actionLabel(action: PassengerAction): string {
  const known: Record<string, string> = {
    check_connections: 'Checked your connection',
    find_hotel_options: 'Looked for hotel rooms',
    reserve_hotel_block: 'Held hotel rooms',
    assess_crew_impact: 'Checked crew availability',
    notify_passengers: 'Contacted affected passengers',
    prepare_notifications: 'Prepared your notification',
    rebook_passengers: 'Prepared a rebooking',
    evaluate_entitlements: 'Assessed what you are owed',
    arrange_ground_transport: 'Looked at ground transport',
    reassign_gate: 'Reassigned the gate',
    record_outcome: 'Recorded the outcome',
  };
  return known[action.action_type] ?? action.action_type.replace(/_/g, ' ');
}

// ---------------------------------------------------------------- next step

export interface NextStepView {
  /** A token `StateBadge` already understands. */
  token: 'awaiting_approval' | 'executing' | 'resolved' | 'scheduled';
  headline: string;
  detail: string;
  respondBy: null;
  /** True when nothing is confirmed yet, so the screen must not imply it is. */
  awaitingDecision: boolean;
}

/**
 * Maps the recorded next-step state onto a badge token and the copy that goes with it.
 *
 * The copy is composed here rather than sent by the backend, because prose is presentation — but
 * every branch is selected by a recorded state and none of them claims an outcome. The
 * `awaiting_approval` wording in particular tells the reader that nothing has changed on their
 * booking, which is the true and least comfortable thing to say.
 */
export function deriveNextStep(view: PassengerDisruptionResponse): NextStepView {
  const step = view.next_step;

  if (step.state === 'no_disruption') {
    return {
      token: 'scheduled',
      headline: 'Your trip is running as booked',
      detail: 'Nothing is recorded against any flight on this booking.',
      respondBy: null,
      awaitingDecision: false,
    };
  }

  if (step.state === 'awaiting_approval') {
    return {
      token: 'awaiting_approval',
      headline: 'An airline colleague is reviewing your recovery',
      detail:
        'Nothing has changed on your booking yet and you do not need to do anything. ' +
        'A change of this kind is not made automatically, so it is waiting for a person to decide.',
      respondBy: null,
      awaitingDecision: true,
    };
  }

  if (step.state === 'executing') {
    return {
      token: 'executing',
      headline: 'Work on your trip is under way',
      detail: 'An approved step is running now. This page reflects it once it is recorded.',
      respondBy: null,
      awaitingDecision: false,
    };
  }

  if (step.state === 'resolved') {
    return {
      token: 'resolved',
      headline: 'The disruption is closed',
      detail: 'Every recorded step for this incident has finished.',
      respondBy: null,
      awaitingDecision: false,
    };
  }

  return {
    token: 'scheduled',
    headline: 'Your trip is being monitored',
    detail:
      'The disruption is recorded and open. Nothing is waiting on you, and this page updates as ' +
      'steps are recorded.',
    respondBy: null,
    awaitingDecision: false,
  };
}

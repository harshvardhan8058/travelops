/**
 * Derivation for the passenger disruption view. Pure, dependency-free, unit-testable.
 *
 * The screen renders; this decides. Same reason as every other feature here: the test runner is a
 * node environment collecting `*.test.ts`, so anything living inside the component is untested by
 * construction — and the decisions on this screen are the ones where a silent wrong answer is worst,
 * because the reader is a passenger who cannot cross-check it against the ledger.
 *
 * Four rules:
 *
 *   1. **Absent is not zero.** `delay_minutes: null` means no revision has been published;
 *      `delay_minutes: 0` means on time. Rendering both as "0m" would tell someone their flight is
 *      fine when nothing is known about it.
 *   2. **Nothing is confirmed until it is.** The trip status of a plan awaiting a human is not
 *      "rebooked". `deriveNextStep` keeps the distinction the orchestrator makes.
 *   3. **No aggregate beyond a count of what was returned.** Counting an array is the one aggregate
 *      permitted; nothing here averages, scores or ranks.
 *   4. **An unavailable option states its reason.** A greyed row without one is a broken screen.
 *
 * Owner: Stream D.
 */

import type {
  PassengerActionRecord,
  PassengerDisruptionResponse,
  PassengerOption,
  PassengerSegment,
} from './passengerContracts';

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

export interface TripStatus {
  token: TripStatusToken;
  /** Words, because colour is never the only signal. */
  label: string;
  /** The segment that set the status, so the screen can point at it. */
  drivenBy: PassengerSegment | null;
  disruptedSegments: number;
  totalSegments: number;
}

const TRIP_STATUS_LABEL: Record<TripStatusToken, string> = {
  cancelled: 'flight cancelled',
  delayed: 'running late',
  at_risk: 'connection at risk',
  scheduled: 'scheduled',
  on_time: 'on time',
};

/**
 * The worst thing happening to the trip, and which segment caused it.
 *
 * Worst rather than first: a passenger whose second leg is cancelled while the first runs on time is
 * not having an on-time trip, and a first-segment-wins rule would say they were.
 */
export function deriveTripStatus(view: PassengerDisruptionResponse): TripStatus {
  const segments = view.trip.segments;
  let token: TripStatusToken = 'on_time';
  let drivenBy: PassengerSegment | null = null;

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

/** Whether a revised time was published for this segment at all. */
export function hasRevisedTime(segment: PassengerSegment): boolean {
  return segment.revised_departure !== null || segment.revised_arrival !== null;
}

// ---------------------------------------------------------------- options

export interface OptionSummary {
  available: PassengerOption[];
  unavailable: PassengerOption[];
  selfService: PassengerOption[];
  needsAgent: PassengerOption[];
  total: number;
  /** Options marked unavailable with no reason given. Should always be empty. */
  missingReason: PassengerOption[];
}

/**
 * Partitions the options.
 *
 * `missingReason` is surfaced rather than tolerated: an unavailable option with a null reason is a
 * contract defect, and the screen renders it as one instead of showing a dead row.
 */
export function summariseOptions(options: readonly PassengerOption[]): OptionSummary {
  const available = options.filter((option) => option.available);
  const unavailable = options.filter((option) => !option.available);
  return {
    available,
    unavailable,
    selfService: available.filter((option) => !option.requires_agent),
    needsAgent: available.filter((option) => option.requires_agent),
    total: options.length,
    missingReason: unavailable.filter(
      (option) => option.unavailable_reason === null || option.unavailable_reason.trim() === '',
    ),
  };
}

// ---------------------------------------------------------------- actions

export interface ActionProgress {
  done: number;
  inFlight: number;
  pending: number;
  awaitingApproval: number;
  total: number;
  /**
   * The action a reader should look at: whatever is blocked on a person first, then whatever is
   * running, then the next thing queued. Null when everything has finished.
   */
  current: PassengerActionRecord | null;
  /** True when at least one action cannot proceed without a human decision. */
  blockedOnPerson: boolean;
}

/**
 * Folds the action ledger into counts and a single "what matters now".
 *
 * Awaiting-approval outranks executing on purpose: a passenger reading "sending your confirmation"
 * while a human has not approved the rebooking has been told the wrong thing about their trip.
 */
export function deriveActionProgress(actions: readonly PassengerActionRecord[]): ActionProgress {
  const count = (state: PassengerActionRecord['state']) =>
    actions.filter((action) => action.state === state).length;

  const awaiting = actions.find((action) => action.state === 'awaiting_approval') ?? null;
  const executing = actions.find((action) => action.state === 'executing') ?? null;
  const pending = actions.find((action) => action.state === 'pending') ?? null;

  return {
    done: count('succeeded'),
    inFlight: count('executing'),
    pending: count('pending'),
    awaitingApproval: count('awaiting_approval'),
    total: actions.length,
    current: awaiting ?? executing ?? pending,
    blockedOnPerson: awaiting !== null,
  };
}

// ---------------------------------------------------------------- next step

export interface NextStepView {
  /** A token `StateBadge` already understands. */
  token: 'awaiting_approval' | 'needs_human' | 'executing' | 'resolved' | 'scheduled';
  headline: string;
  detail: string;
  respondBy: string | null;
  /** True when the passenger has something to do. False when the system does. */
  passengerMustAct: boolean;
  /** True when nothing is confirmed yet, so the screen must not imply it is. */
  awaitingDecision: boolean;
}

/**
 * Maps the contract's next-step state onto a badge token and two booleans the screen branches on.
 *
 * `action_required` becomes `needs_human` rather than a new token, so this feature adds nothing to
 * the shared status vocabulary — the rule that keeps two screens from disagreeing about what amber
 * means.
 */
export function deriveNextStep(view: PassengerDisruptionResponse): NextStepView {
  const step = view.next_step;
  const token =
    step.state === 'action_required'
      ? 'needs_human'
      : step.state === 'monitoring'
        ? 'scheduled'
        : step.state;

  return {
    token,
    headline: step.headline,
    detail: step.detail,
    respondBy: step.respond_by,
    passengerMustAct: step.state === 'action_required',
    awaitingDecision: step.state === 'awaiting_approval',
  };
}

// ---------------------------------------------------------------- impacts

/** Unresolved first, because those are the ones a reader can still do something about. */
export function orderImpacts(
  impacts: readonly PassengerDisruptionResponse['impacts'][number][],
): PassengerDisruptionResponse['impacts'] {
  return [
    ...impacts.filter((impact) => !impact.resolved),
    ...impacts.filter((impact) => impact.resolved),
  ];
}

/** How many consequences are still open. A count of a returned array, nothing more. */
export function openImpactCount(
  impacts: readonly PassengerDisruptionResponse['impacts'][number][],
): number {
  return impacts.filter((impact) => !impact.resolved).length;
}

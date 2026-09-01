/**
 * What a resolved incident did not finish.
 *
 * `resolved` is the workflow's word, and it is correct: every task was dispatched and the run has
 * nowhere left to go. It is not a claim that every passenger was accommodated, and on the seeded
 * cascade those two readings come apart. The hotel allocation secures 71 of 87 rooms, records
 * `needs_human` with the shortfall named on the action, and the engine deliberately continues —
 * abandoning 71 real rooms and stopping the connection, crew and notification work for 604
 * passengers because 32 of them lack a bed would be the worse outcome, and the refusal is recorded
 * rather than hidden.
 *
 * So the header read `WORKFLOW RESOLVED` while a task on the same screen read `NEEDS HUMAN`, and
 * nothing reconciled them. The operator has to scroll to the task table to discover that something
 * is still owed. This derives that fact from the recorded actions and puts it beside the state.
 *
 * Nothing here is inferred. An outstanding item is an `action` row the backend wrote with
 * `status: needs_human`; its `reason` is the service's own sentence and is rendered verbatim.
 *
 * Owner: Stream D.
 */

import type { ActionRecord } from '@/api/types';

export interface OutstandingItem {
  actionId: number;
  /** `reserve_hotel_block`, etc. Absent on the committed fixture's action rows. */
  actionType: string | null;
  /** The service's own sentence. Never paraphrased. */
  reason: string;
}

/**
 * Actions the backend recorded as needing a person, in the order they were executed.
 *
 * Only `needs_human` counts. A `failure` is a different fact with a different remedy, and the
 * screens that render failures already say so; folding the two together here would put a broken
 * service and an operational choice under one heading.
 */
export function outstandingDemand(actions: readonly ActionRecord[] | undefined): OutstandingItem[] {
  if (!actions) return [];
  return actions
    .filter((action) => action.status === 'needs_human')
    .map((action) => ({
      actionId: action.id,
      actionType: action.action_type ?? null,
      reason: action.reason,
    }));
}

/** Whether a state word would overstate what happened. True only for a finished-but-owing run. */
export function resolvedWithOutstandingDemand(
  state: string | undefined,
  actions: readonly ActionRecord[] | undefined,
): boolean {
  return state === 'resolved' && outstandingDemand(actions).length > 0;
}

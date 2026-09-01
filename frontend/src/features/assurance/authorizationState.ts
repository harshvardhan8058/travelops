/**
 * Where an action stands between "a person authorised it" and "it actually happened".
 *
 * These are not the same event, and the product had no way to say so. An operator pressed Approve,
 * the form was replaced by an audit record, and nothing indicated that the action was still sitting
 * there un-executed — the control that runs it lives four hundred lines up the page and looks
 * identical before and after a decision. The reasonable inference from that screen is "approving it
 * did it", which is the single most dangerous thing an audit-trail product can imply.
 *
 * So this module names the stages, derived entirely from records the API already returns:
 *
 *   awaiting_decision       the gate held the action and no person has answered
 *   authorized_not_executed a person approved it; the workflow has NOT run it yet
 *   executing               the run is in flight
 *   executed                the workflow recorded a completed action
 *   execution_failed        it ran and did not succeed
 *   refused                 a person rejected it; it stays blocked
 *   not_gated               the gate authorised it without a person
 *
 * Two invariants make this safe to render, and both are asserted in the tests.
 *
 * **`claimsExecuted` is true for exactly one stage.** Approval is not execution, and no amount of
 * approving moves this flag. It comes from the workflow's own task state, never from the presence of
 * a decision.
 *
 * **`offersRun` is true for exactly one stage.** The next step belongs to the operator only when
 * something is authorised and un-run. Offering it while executing would invite a double run; offering
 * it after a refusal would invite working around the refusal.
 *
 * ## Why the task state is the authority, not the decision
 *
 * `POST /assurance/{id}/decision` writes a `human_decision` and executes nothing —
 * `backend/app/api/assurance_router.py` — and `evaluation.decision` is never mutated, so it reads
 * `needs_human` forever. The state that actually moves is the plan task's: the engine's
 * `_step_awaiting_approval` flips it to `assured` and the incident to `executing` on an approval, or
 * to `rejected` on a refusal, and only on the NEXT run. That asymmetry is precisely the gap an
 * operator cannot see, so it is what this module reads.
 *
 * A consequence worth stating: immediately after a rejection the task is still `needs_human`,
 * because the refusal has not been applied yet either. That is reported as refused-and-pending
 * rather than as refused-and-done, because claiming the work is already stopped would be as
 * inaccurate as claiming an approval already ran.
 *
 * Owner: Stream D.
 */

import type { ActionRecord, AssuranceEvaluation, HumanDecision, PlanTaskRow } from '@/api/types';

export type AuthorizationStage =
  | 'awaiting_decision'
  | 'authorized_not_executed'
  | 'executing'
  | 'executed'
  | 'execution_failed'
  | 'refused'
  | 'not_gated';

export interface AuthorizationView {
  stage: AuthorizationStage;
  /** Badge text. Written here rather than taken from a contract value, so it is prose, not a token. */
  label: string;
  /** One sentence stating what is true and, where it matters, what is not yet true. */
  detail: string;
  /**
   * Whether to offer the run control. True for `authorized_not_executed` only.
   *
   * This is the affordance the product was missing: the operator has authorised the action and the
   * workflow will not move until someone advances it.
   */
  offersRun: boolean;
  /**
   * Whether this stage asserts the action has actually happened. True for `executed` only.
   *
   * Read from the workflow's recorded task state, so an approval alone can never set it.
   */
  claimsExecuted: boolean;
  /** Tone for the surrounding band. `refused` and `execution_failed` are never presented as ok. */
  tone: 'ok' | 'warn' | 'crit' | 'info' | 'muted';
}

/**
 * States in which the workflow itself has moved past the human gate.
 *
 * `assured` is included deliberately: the engine sets it at the moment it accepts an approval, in the
 * same run that transitions the incident to `executing`. So a task sitting at `assured` has been
 * picked up — it is no longer waiting for a person.
 */
const PAST_THE_GATE: ReadonlySet<PlanTaskRow['state']> = new Set([
  'assured',
  'executing',
  'succeeded',
  'failed',
  'skipped',
  'rejected',
]);

export function deriveAuthorizationState(
  evaluation: AssuranceEvaluation,
  task: PlanTaskRow | undefined,
  action?: ActionRecord,
  /**
   * The decision as the screen knows it, which may be a session-only record in fixture mode. Falls
   * back to the evaluation's own persisted `human_decision`.
   */
  decision?: HumanDecision,
): AuthorizationView {
  const verdict = decision?.decision ?? evaluation.human_decision?.decision ?? null;
  const taskState = task?.state;

  // Terminal facts about the work come first: once the workflow has recorded an outcome, that
  // outcome is the answer regardless of how the action came to be authorised.
  if (taskState === 'succeeded') {
    return {
      stage: 'executed',
      label: 'Executed',
      detail: action?.executed_at
        ? 'The workflow executed this action and recorded the result.'
        : 'The workflow recorded this task as succeeded.',
      offersRun: false,
      claimsExecuted: true,
      tone: 'ok',
    };
  }

  if (taskState === 'failed') {
    return {
      stage: 'execution_failed',
      label: 'Execution failed',
      // The service's own sentence where there is one. Never softened into "partially completed".
      detail: action?.reason
        ? `This action was attempted and did not succeed: ${action.reason}`
        : 'This action was attempted and did not succeed.',
      offersRun: false,
      claimsExecuted: false,
      tone: 'crit',
    };
  }

  if (taskState === 'executing') {
    return {
      stage: 'executing',
      label: 'Executing',
      detail:
        'The workflow is running this action now. Nothing is confirmed until it reports back.',
      offersRun: false,
      claimsExecuted: false,
      tone: 'info',
    };
  }

  if (verdict === 'rejected') {
    return {
      stage: 'refused',
      label: 'Refused by operator',
      detail:
        taskState === 'rejected'
          ? 'An operator refused this action. It is blocked and will not be executed.'
          : 'An operator refused this action. It is blocked; the refusal is applied on the next run.',
      offersRun: false,
      claimsExecuted: false,
      tone: 'crit',
    };
  }

  if (verdict === 'approved') {
    // The state this whole module exists for.
    if (taskState !== undefined && PAST_THE_GATE.has(taskState)) {
      return {
        stage: 'executing',
        label: 'Authorised, in progress',
        detail: 'The approval has been picked up and the workflow is advancing this action.',
        offersRun: false,
        claimsExecuted: false,
        tone: 'info',
      };
    }
    return {
      stage: 'authorized_not_executed',
      label: 'Approved by operator',
      detail: 'This action is authorized but has not executed yet.',
      offersRun: true,
      claimsExecuted: false,
      tone: 'warn',
    };
  }

  if (evaluation.decision !== 'needs_human') {
    return {
      stage: 'not_gated',
      label: 'Authorised by the gate',
      detail: `The gate returned ${evaluation.decision}, so no operator decision is required.`,
      offersRun: false,
      claimsExecuted: false,
      tone: 'muted',
    };
  }

  return {
    stage: 'awaiting_decision',
    label: 'Awaiting operator decision',
    detail: 'The gate held this action for a person. Nothing will run until someone decides.',
    offersRun: false,
    claimsExecuted: false,
    tone: 'warn',
  };
}

/**
 * The task an operator opening the workspace should land on.
 *
 * `task.state === 'needs_human'` looks like the answer and is not. It is also the state of a task
 * whose gate returned `execute` and which is merely stuck because its service could not complete —
 * `reserve_hotel_block` sitting on a `SERVICE_NOT_IMPLEMENTED` refusal, for instance. Selecting that
 * one lands the operator on a task they cannot act on, with the panel explaining a stall, while a
 * different task quietly waits for the decision they actually came to make.
 *
 * A task awaits a PERSON only when its own evaluation asked for one and nobody has answered yet. That
 * is what this prefers; the stalled-task fallback is kept so the screen still selects something
 * sensible when nothing needs a decision.
 */
export function preferredTaskId(
  tasks: readonly PlanTaskRow[],
  evaluations: readonly AssuranceEvaluation[],
): number | null {
  const awaitingPerson = new Set(
    evaluations
      .filter((evaluation) => evaluation.decision === 'needs_human' && !evaluation.human_decision)
      .map((evaluation) => evaluation.plan_task_id),
  );

  const forDecision = tasks.find((task) => awaitingPerson.has(task.id));
  if (forDecision) return forDecision.id;

  const stalled = tasks.find((task) => task.state === 'needs_human');
  if (stalled) return stalled.id;

  return tasks[0]?.id ?? null;
}

/**
 * A group-scoped restatement of the same distinction, for the approval queue.
 *
 * `POST /incident-groups/{ref}/assurance/decision` writes one decision per covered evaluation and
 * dispatches nothing, exactly like the per-action endpoint. The queue previously reported only
 * "Recorded N decisions; M still need their own", which is true and stops one sentence short of the
 * thing an operator needs to know next.
 */
export interface GroupAuthorizationSummary {
  /** Decisions this approval wrote. */
  covered: number;
  /** Evaluations it could not cover, which still need their own decision. */
  excluded: number;
  /** Whether anything is now authorised and waiting to be run. */
  awaitingExecution: boolean;
  headline: string;
  detail: string;
}

export function summariseGroupApproval(result: {
  covered_count: number;
  excluded_count: number;
  replayed: boolean;
}): GroupAuthorizationSummary {
  const { covered_count: covered, excluded_count: excluded, replayed } = result;
  const plural = covered === 1 ? '' : 's';

  if (covered === 0) {
    return {
      covered,
      excluded,
      awaitingExecution: false,
      headline: 'No decision was recorded',
      detail:
        excluded > 0
          ? `${excluded} evaluation${excluded === 1 ? '' : 's'} could not be covered by this approval and still need their own decision.`
          : 'There was nothing awaiting a person to approve.',
    };
  }

  return {
    covered,
    excluded,
    awaitingExecution: true,
    headline: replayed
      ? `${covered} decision${plural} already recorded`
      : `${covered} action${plural} authorized, not executed`,
    detail:
      `Recording a decision does not run anything. ${covered} action${plural} ` +
      `${covered === 1 ? 'is' : 'are'} now authorized and waiting for the workflow to be advanced` +
      (excluded > 0
        ? `, and ${excluded} still need their own decision.`
        : '. Nothing else is outstanding.'),
  };
}

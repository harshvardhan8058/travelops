import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import {
  deriveAuthorizationState,
  preferredTaskId,
  summariseGroupApproval,
  type AuthorizationStage,
} from './authorizationState';
import type {
  ActionRecord,
  AssuranceEvaluation,
  HumanDecision,
  PlanTaskRow,
  TaskState,
} from '@/api/types';

function evaluation(overrides: Partial<AssuranceEvaluation> = {}): AssuranceEvaluation {
  return {
    id: 41,
    plan_task_id: 7,
    action_type: 'reserve_hotel_block',
    // The gate's own verdict. It is never mutated by an approval, which is why the module must not
    // read it to decide whether a person has answered.
    decision: 'needs_human',
    risk_tier: 'high',
    evaluated_at: '2026-08-20T15:40:00Z',
    checks: [],
    blocking: ['action_risk'],
    evidence_refs: [],
    config_version: 'assurance-v1',
    config_hash: 'f3964eb196257d1d',
    ...overrides,
  };
}

function task(state: TaskState): PlanTaskRow {
  return {
    id: 7,
    task_order: 1,
    action_type: 'reserve_hotel_block',
    state,
    depends_on: [],
    assurance_id: 41,
  };
}

function decision(verdict: 'approved' | 'rejected', persisted = true): HumanDecision {
  return {
    id: 3,
    assurance_id: 41,
    decision: verdict,
    actor_id: 'operator-1',
    reason: 'authorised under the charter',
    decided_at: '2026-08-20T15:45:00Z',
    persisted,
  };
}

function action(overrides: Partial<ActionRecord> = {}): ActionRecord {
  return {
    id: 12,
    plan_task_id: 7,
    action_type: 'reserve_hotel_block',
    assurance_id: 41,
    human_decision_id: 3,
    actor: 'orchestrator',
    status: 'success',
    reason: 'rooms held',
    cost_inr: 5000,
    idempotency_key: 'act:12',
    executed_at: '2026-08-20T15:50:00Z',
    ...overrides,
  };
}

const ALL_STAGES: AuthorizationStage[] = [
  'awaiting_decision',
  'authorized_not_executed',
  'executing',
  'executed',
  'execution_failed',
  'refused',
  'not_gated',
];

describe('the invariants that make this safe to render', () => {
  // Enumerate every combination the contracts allow and assert the two dangerous flags are never set
  // outside the single stage each belongs to. This is the guard against the defect the module exists
  // to fix: an approval reading as an execution.
  const taskStates: TaskState[] = [
    'pending',
    'proposed',
    'assured',
    'needs_human',
    'rejected',
    'executing',
    'succeeded',
    'failed',
    'skipped',
  ];
  const verdicts: (HumanDecision | undefined)[] = [
    undefined,
    decision('approved'),
    decision('rejected'),
  ];
  const gates: AssuranceEvaluation['decision'][] = ['needs_human', 'execute', 'execute_flagged'];

  const every = taskStates.flatMap((state) =>
    verdicts.flatMap((verdict) =>
      gates.map((gate) => ({
        state,
        verdict,
        gate,
        view: deriveAuthorizationState(
          evaluation({ decision: gate }),
          task(state),
          undefined,
          verdict,
        ),
      })),
    ),
  );

  it('covers a meaningful number of combinations', () => {
    expect(every.length).toBe(9 * 3 * 3);
  });

  it('claims execution only when the workflow recorded a succeeded task', () => {
    for (const { state, verdict, gate, view } of every) {
      const context = `task=${state} verdict=${verdict?.decision ?? 'none'} gate=${gate}`;
      expect(view.claimsExecuted, context).toBe(state === 'succeeded');
    }
  });

  it('never claims execution merely because a person approved', () => {
    const approvedButPending = every.filter(
      (row) => row.verdict?.decision === 'approved' && row.state === 'needs_human',
    );

    expect(approvedButPending.length).toBeGreaterThan(0);
    for (const row of approvedButPending) expect(row.view.claimsExecuted).toBe(false);
  });

  it('offers the run control only for authorized_not_executed', () => {
    for (const { state, verdict, gate, view } of every) {
      const context = `task=${state} verdict=${verdict?.decision ?? 'none'} gate=${gate}`;
      expect(view.offersRun, context).toBe(view.stage === 'authorized_not_executed');
    }
  });

  it('never offers a run control after a refusal', () => {
    const refused = every.filter((row) => row.verdict?.decision === 'rejected');

    expect(refused.length).toBeGreaterThan(0);
    for (const row of refused) expect(row.view.offersRun).toBe(false);
  });

  it('never presents a refusal or a failure with an ok tone', () => {
    for (const { view } of every) {
      if (view.stage === 'refused' || view.stage === 'execution_failed') {
        expect(view.tone).toBe('crit');
      }
    }
  });

  it('always produces a non-empty label and detail', () => {
    for (const { view } of every) {
      expect(view.label.trim()).not.toBe('');
      expect(view.detail.trim()).not.toBe('');
    }
  });

  it('produces only declared stages', () => {
    for (const { view } of every) expect(ALL_STAGES).toContain(view.stage);
  });
});

describe('awaiting a decision', () => {
  it('reports the gate is holding the action when nobody has answered', () => {
    const view = deriveAuthorizationState(evaluation(), task('needs_human'));

    expect(view.stage).toBe('awaiting_decision');
    expect(view.offersRun).toBe(false);
    expect(view.detail).toMatch(/nothing will run/i);
  });

  it('does not treat a gate-authorised action as needing a person', () => {
    const view = deriveAuthorizationState(evaluation({ decision: 'execute' }), task('pending'));

    expect(view.stage).toBe('not_gated');
    expect(view.detail).toContain('execute');
  });
});

describe('approved but not executed — the state the product was missing', () => {
  it('is reached from a persisted human_decision on the evaluation alone', () => {
    // Reload case: no session record, the verdict lives on the evaluation.
    const view = deriveAuthorizationState(
      evaluation({
        human_decision: {
          id: 3,
          decision: 'approved',
          actor_id: 'operator-1',
          reason: 'ok',
          decided_at: '2026-08-20T15:45:00Z',
        },
      }),
      task('needs_human'),
    );

    expect(view.stage).toBe('authorized_not_executed');
  });

  it('is reached from a session-only decision in fixture mode', () => {
    const view = deriveAuthorizationState(
      evaluation(),
      task('needs_human'),
      undefined,
      decision('approved', false),
    );

    expect(view.stage).toBe('authorized_not_executed');
  });

  it('states plainly that it has not executed yet', () => {
    const view = deriveAuthorizationState(
      evaluation(),
      task('needs_human'),
      undefined,
      decision('approved'),
    );

    expect(view.detail).toBe('This action is authorized but has not executed yet.');
    expect(view.claimsExecuted).toBe(false);
    expect(view.offersRun).toBe(true);
  });

  it('stops offering a run once the workflow has picked the approval up', () => {
    // `assured` is set in the same run that moves the incident to executing.
    for (const state of ['assured', 'executing'] as const) {
      const view = deriveAuthorizationState(
        evaluation(),
        task(state),
        undefined,
        decision('approved'),
      );
      expect(view.offersRun, state).toBe(false);
      expect(view.stage, state).toBe('executing');
    }
  });

  it('prefers the session decision over a conflicting persisted one', () => {
    // The screen's own record wins, because it is what the operator just did.
    const view = deriveAuthorizationState(
      evaluation({
        human_decision: {
          id: 3,
          decision: 'rejected',
          actor_id: 'operator-1',
          reason: 'no',
          decided_at: '2026-08-20T15:45:00Z',
        },
      }),
      task('needs_human'),
      undefined,
      decision('approved'),
    );

    expect(view.stage).toBe('authorized_not_executed');
  });
});

describe('refusal stays blocked', () => {
  it('is blocked immediately, before the run applies it', () => {
    const view = deriveAuthorizationState(
      evaluation(),
      task('needs_human'),
      undefined,
      decision('rejected'),
    );

    expect(view.stage).toBe('refused');
    expect(view.tone).toBe('crit');
    expect(view.detail).toMatch(/blocked/i);
    // And it does not pretend the work has already stopped.
    expect(view.detail).toMatch(/applied on the next run/i);
  });

  it('reports a settled refusal once the task is rejected', () => {
    const view = deriveAuthorizationState(
      evaluation(),
      task('rejected'),
      undefined,
      decision('rejected'),
    );

    expect(view.stage).toBe('refused');
    expect(view.detail).toMatch(/will not be executed/i);
  });

  it('offers no way to run a refused action', () => {
    for (const state of ['needs_human', 'rejected'] as const) {
      expect(
        deriveAuthorizationState(evaluation(), task(state), undefined, decision('rejected'))
          .offersRun,
      ).toBe(false);
    }
  });
});

describe('execution outcomes come from the workflow, not the decision', () => {
  it('reports executed only on a succeeded task', () => {
    const view = deriveAuthorizationState(
      evaluation(),
      task('succeeded'),
      action(),
      decision('approved'),
    );

    expect(view.stage).toBe('executed');
    expect(view.claimsExecuted).toBe(true);
    expect(view.tone).toBe('ok');
  });

  it("surfaces the service's own sentence when execution failed", () => {
    const view = deriveAuthorizationState(
      evaluation(),
      task('failed'),
      action({ status: 'failed', reason: 'SERVICE_NOT_IMPLEMENTED: no hotel provider is wired' }),
      decision('approved'),
    );

    expect(view.stage).toBe('execution_failed');
    expect(view.detail).toContain('SERVICE_NOT_IMPLEMENTED: no hotel provider is wired');
    expect(view.claimsExecuted).toBe(false);
  });

  it('does not soften a failure into a partial success', () => {
    const view = deriveAuthorizationState(
      evaluation(),
      task('failed'),
      action({ status: 'failed' }),
    );

    expect(view.detail).not.toMatch(/partial|complete/i);
  });

  it('a failure outranks an approval, so an approved-then-failed action is not shown as authorised', () => {
    const view = deriveAuthorizationState(
      evaluation(),
      task('failed'),
      undefined,
      decision('approved'),
    );

    expect(view.stage).toBe('execution_failed');
  });
});

describe('a missing task', () => {
  it('still answers, and never claims execution', () => {
    const view = deriveAuthorizationState(evaluation(), undefined);

    expect(view.stage).toBe('awaiting_decision');
    expect(view.claimsExecuted).toBe(false);
  });

  it('reports an approved evaluation with no task as authorised and un-run', () => {
    const view = deriveAuthorizationState(evaluation(), undefined, undefined, decision('approved'));

    expect(view.stage).toBe('authorized_not_executed');
    expect(view.offersRun).toBe(true);
  });
});

describe('summariseGroupApproval', () => {
  it('says an approval authorised work rather than performed it', () => {
    const summary = summariseGroupApproval({
      covered_count: 4,
      excluded_count: 2,
      replayed: false,
    });

    expect(summary.headline).toBe('4 actions authorized, not executed');
    expect(summary.detail).toMatch(/does not run anything/i);
    expect(summary.awaitingExecution).toBe(true);
  });

  it('names how many still need their own decision', () => {
    expect(
      summariseGroupApproval({ covered_count: 4, excluded_count: 2, replayed: false }).detail,
    ).toContain('2 still need their own decision');
  });

  it('says nothing is outstanding when nothing was excluded', () => {
    expect(
      summariseGroupApproval({ covered_count: 1, excluded_count: 0, replayed: false }).detail,
    ).toMatch(/nothing else is outstanding/i);
  });

  it('uses the singular for one action', () => {
    const summary = summariseGroupApproval({
      covered_count: 1,
      excluded_count: 0,
      replayed: false,
    });

    expect(summary.headline).toBe('1 action authorized, not executed');
    expect(summary.detail).toContain('1 action is now authorized');
  });

  it('does not claim a replay recorded anything new', () => {
    const summary = summariseGroupApproval({ covered_count: 3, excluded_count: 0, replayed: true });

    expect(summary.headline).toMatch(/already recorded/i);
  });

  it('does not claim work is awaiting execution when nothing was covered', () => {
    const summary = summariseGroupApproval({
      covered_count: 0,
      excluded_count: 5,
      replayed: false,
    });

    expect(summary.awaitingExecution).toBe(false);
    expect(summary.headline).toMatch(/no decision was recorded/i);
    expect(summary.detail).toContain('5 evaluations');
  });

  it('is honest when there was simply nothing to approve', () => {
    const summary = summariseGroupApproval({
      covered_count: 0,
      excluded_count: 0,
      replayed: false,
    });

    expect(summary.awaitingExecution).toBe(false);
    expect(summary.detail).toMatch(/nothing awaiting a person/i);
  });
});

describe('recording a decision cannot execute anything', () => {
  /*
   * The invariant these guards protect is the product's central safety claim: a human decision is an
   * authorisation, and execution is a separate act somebody has to ask for. Adding a run affordance
   * next to the approve button makes the two adjacent, and adjacency is exactly the condition under
   * which someone later "helpfully" chains them — at which point Approve silently becomes Approve
   * And Execute and every audit trail in the product starts lying about who did what.
   *
   * So these read the source and assert the chaining does not exist.
   */
  const read = (path: string) =>
    readFileSync(new URL(path, import.meta.url), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/(^|[^:])\/\/.*$/gm, '$1');

  /** The body of a named `useMutation({ ... })`, sliced by brace balance. */
  function mutationBody(source: string, declaration: string): string {
    const at = source.indexOf(declaration);
    expect(at, `${declaration} not found`).toBeGreaterThan(-1);
    const open = source.indexOf('{', at);
    let depth = 0;
    for (let i = open; i < source.length; i++) {
      if (source[i] === '{') depth++;
      if (source[i] === '}') {
        depth--;
        if (depth === 0) return source.slice(open, i + 1);
      }
    }
    throw new Error(`could not slice ${declaration}`);
  }

  it('the assurance panel holds no run endpoint of its own', () => {
    const panel = read('./AssurancePanel.tsx');

    // It receives `onRun` as a prop. If it could call the API directly it would be a second
    // execution path, with its own idempotency key and no relationship to the header's.
    expect(panel).not.toMatch(/api\.runIncident/);
    expect(panel).not.toMatch(/api\.runGroup/);
    expect(panel).toMatch(/onRun\?:\s*\(\)\s*=>\s*void/);
  });

  it('the incident decision mutation does not advance the workflow on success', () => {
    const workspace = read('../incident/RecoveryWorkspace.tsx');
    const body = mutationBody(workspace, 'const decisionMutation = useMutation');

    expect(body).not.toMatch(/runMutation/);
    expect(body).not.toMatch(/api\.runIncident/);
  });

  it('the incident run mutation does not record a decision', () => {
    // The converse: running must not manufacture an approval to get past the gate.
    const workspace = read('../incident/RecoveryWorkspace.tsx');
    const body = mutationBody(workspace, 'const runMutation = useMutation');

    expect(body).not.toMatch(/submitDecision/);
    expect(body).not.toMatch(/decisionMutation/);
  });

  it('the group approval mutation does not advance the group on success', () => {
    const queue = read('./GroupApprovalQueue.tsx');
    const body = mutationBody(queue, 'const approve = useMutation');

    expect(body).not.toMatch(/api\.runGroup/);
    expect(body).not.toMatch(/api\.openGroup/);
    expect(body).not.toMatch(/api\.runIncident/);
  });

  it('the group queue reaches execution only through the shared run control', () => {
    const queue = read('./GroupApprovalQueue.tsx');

    // Reused, not reimplemented: one component owns advancing a cascade.
    expect(queue).toMatch(/<GroupRunControl/);
    expect(queue).toMatch(/import \{ GroupRunControl \}/);
    // And the queue itself never calls a run endpoint anywhere.
    expect(queue).not.toMatch(/api\.(runGroup|openGroup|runIncident)/);
  });

  it('the run control is offered only where the derivation says so', () => {
    const panel = read('./AssurancePanel.tsx');

    // The button is gated on `offersRun`, which the invariant tests above pin to a single stage.
    expect(panel).toMatch(/authorization\.offersRun && onRun/);
  });

  it('the slicer is not vacuous', () => {
    const sliced = mutationBody(
      'const decisionMutation = useMutation({ a: { b: 1 }, c: 2 });',
      'const decisionMutation = useMutation',
    );

    expect(sliced).toBe('{ a: { b: 1 }, c: 2 }');
  });
});

describe('preferredTaskId', () => {
  const tasks: PlanTaskRow[] = [
    {
      id: 1,
      task_order: 1,
      action_type: 'check_connections',
      state: 'succeeded',
      depends_on: [],
      assurance_id: 1,
    },
    // Stalled on a service refusal, NOT waiting for a person: its gate said execute.
    {
      id: 3,
      task_order: 3,
      action_type: 'reserve_hotel_block',
      state: 'needs_human',
      depends_on: [],
      assurance_id: 3,
    },
    {
      id: 5,
      task_order: 5,
      action_type: 'notify_passengers',
      state: 'needs_human',
      depends_on: [],
      assurance_id: 5,
    },
  ];
  const stalled = evaluation({ id: 3, plan_task_id: 3, decision: 'execute' });
  const heldForPerson = evaluation({ id: 5, plan_task_id: 5, decision: 'needs_human' });

  it('prefers the task whose evaluation actually asked for a person', () => {
    // This is the real defect: task 3 comes first and is `needs_human`, but nothing about it needs a
    // decision — the operator can only act on task 5.
    expect(preferredTaskId(tasks, [stalled, heldForPerson])).toBe(5);
  });

  it('does not pick a task stalled on a service failure over one awaiting a decision', () => {
    expect(preferredTaskId(tasks, [stalled, heldForPerson])).not.toBe(3);
  });

  it('ignores an evaluation that has already been answered', () => {
    const answered = evaluation({
      id: 5,
      plan_task_id: 5,
      decision: 'needs_human',
      human_decision: {
        id: 9,
        decision: 'approved',
        actor_id: 'operator-1',
        reason: 'ok',
        decided_at: '2026-08-20T15:45:00Z',
      },
    });

    // Nothing awaits a person any more, so it falls back to the stalled task rather than reopening a
    // decision that has been made.
    expect(preferredTaskId(tasks, [stalled, answered])).toBe(3);
  });

  it('falls back to a stalled task when no evaluation needs a person', () => {
    expect(preferredTaskId(tasks, [stalled])).toBe(3);
  });

  it('falls back to the first task when nothing is blocked at all', () => {
    const done: PlanTaskRow[] = [
      {
        id: 1,
        task_order: 1,
        action_type: 'check_connections',
        state: 'succeeded',
        depends_on: [],
        assurance_id: 1,
      },
      {
        id: 2,
        task_order: 2,
        action_type: 'notify_passengers',
        state: 'succeeded',
        depends_on: [],
        assurance_id: 2,
      },
    ];

    expect(preferredTaskId(done, [])).toBe(1);
  });

  it('answers null for an empty plan rather than throwing', () => {
    expect(preferredTaskId([], [])).toBeNull();
  });

  it('still works before any evaluation has been fetched', () => {
    expect(preferredTaskId(tasks, [])).toBe(3);
  });

  it('does not select a task the evaluations do not mention', () => {
    const orphan = evaluation({ id: 99, plan_task_id: 99, decision: 'needs_human' });

    expect(preferredTaskId(tasks, [orphan])).toBe(3);
  });
});

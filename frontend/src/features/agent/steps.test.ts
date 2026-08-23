import { describe, expect, it } from 'vitest';

import type {
  ActionRecord,
  AssuranceEvaluation,
  CandidatePlan,
  IncidentDetail,
  PlanTaskRow,
  Provenance,
  ReplayFrame,
} from '@/api/types';
import {
  activityByActor,
  autonomySplit,
  buildStepLedger,
  describePayload,
  outcomeFor,
  resolveLedger,
  toolUse,
  troubledSteps,
} from './steps';

const provenance: Provenance = {
  kind: 'real',
  provider: 'orchestrator',
  source_ref: 'incident-1',
  observed_at: null,
  retrieved_at: null,
  is_stale: false,
};

function task(overrides: Partial<PlanTaskRow> = {}): PlanTaskRow {
  return {
    id: 1,
    task_order: 1,
    action_type: 'check_connections',
    state: 'pending',
    depends_on: [],
    assurance_id: null,
    ...overrides,
  };
}

function action(overrides: Partial<ActionRecord> = {}): ActionRecord {
  return {
    id: 10,
    plan_task_id: 1,
    action_type: 'check_connections',
    assurance_id: 5,
    human_decision_id: null,
    actor: 'connection_service',
    status: 'success',
    reason: 'examined 42 itineraries',
    cost_inr: null,
    idempotency_key: 'key-1',
    executed_at: '2026-08-20T09:00:00Z',
    provenance_kind: 'real',
    reason_code: null,
    decision_scope: null,
    plan_approval_id: null,
    ...overrides,
  };
}

function evaluation(overrides: Partial<AssuranceEvaluation> = {}): AssuranceEvaluation {
  return {
    id: 5,
    plan_task_id: 1,
    action_type: 'check_connections',
    decision: 'execute',
    risk_tier: 'low',
    evaluated_at: '2026-08-20T08:59:00Z',
    checks: [],
    blocking: [],
    evidence_refs: [],
    config_version: 'assurance-v1',
    config_hash: 'f3964eb196257d1d',
    ...overrides,
  };
}

function incident(overrides: Partial<IncidentDetail> = {}): IncidentDetail {
  return {
    id: 1,
    reference: 'INC-2026-0820-VOBL-01',
    group_reference: 'GRP-2026-0820-VOBL',
    flight: { flight_number: '6E 2134' },
    trigger_type: 'weather',
    severity: 'high',
    state: 'executing',
    opened_at: '2026-08-20T08:57:00Z',
    state_rail: [],
    evidence: { risk: null, weather: null },
    plan: {
      id: 3,
      generator: 'fallback-playbook',
      prompt_version: null,
      model_self_report: null,
      generated_at: '2026-08-20T08:58:00Z',
      rationale: 'ordered by dependency',
      tasks: [task()],
    },
    actions: [],
    provenance,
    ...overrides,
  };
}

function candidatePlan(overrides: Partial<CandidatePlan> = {}): CandidatePlan {
  return {
    id: 3,
    incident_reference: 'INC-2026-0820-VOBL-01',
    variant_key: 'baseline',
    generator: 'fallback-playbook',
    generated_at: '2026-08-20T08:58:00Z',
    rationale: null,
    selection_state: 'selected',
    selected_at: null,
    selected_by: null,
    plan_hash: 'abc123',
    tasks: [
      {
        id: 1,
        action_type: 'check_connections',
        task_order: 1,
        state: 'pending',
        target_refs: ['BOOKING-7'],
        depends_on: [],
      },
    ],
    ...overrides,
  };
}

function frame(overrides: Partial<ReplayFrame> = {}): ReplayFrame {
  return {
    sequence: 1,
    occurred_at: '2026-08-20T08:57:00Z',
    stage: 'run',
    actor: 'orchestrator',
    actor_kind: 'orchestrator',
    event_type: 'INCIDENT_OPENED',
    summary: 'Opened INC-2026-0820-VOBL-01',
    state_before: null,
    state_after: 'detected',
    incident_reference: 'INC-2026-0820-VOBL-01',
    evidence_refs: [],
    assurance_id: null,
    human_decision_id: null,
    decision_scope: null,
    plan_approval_id: null,
    detail: {},
    ...overrides,
  };
}

describe('outcomeFor', () => {
  it('reports nothing attempted as not_started rather than as a failure', () => {
    expect(outcomeFor(null)).toEqual({ outcome: 'not_started', refusal: null });
  });

  it('maps the three plain statuses straight through', () => {
    expect(outcomeFor(action({ status: 'success' })).outcome).toBe('succeeded');
    expect(outcomeFor(action({ status: 'failure' })).outcome).toBe('failed');
    expect(outcomeFor(action({ status: 'skipped' })).outcome).toBe('skipped');
  });

  it('separates a refusal from a decision that awaits a person', () => {
    const refused = outcomeFor(
      action({
        status: 'needs_human',
        reason: 'SERVICE_NOT_IMPLEMENTED: no deterministic service is registered',
        provenance_kind: 'unavailable',
      }),
    );
    expect(refused.outcome).toBe('refused');
    expect(refused.refusal?.code).toBe('SERVICE_NOT_IMPLEMENTED');
    expect(refused.refusal?.operatorCannotResolve).toBe(true);

    const awaiting = outcomeFor(
      action({
        status: 'needs_human',
        reason: 'payout above the auto limit',
        provenance_kind: 'real',
      }),
    );
    expect(awaiting.outcome).toBe('awaiting_human');
    expect(awaiting.refusal).toBeNull();
  });

  it('recognises a refusal from reason_code alone, without parsing prose', () => {
    const result = outcomeFor(
      action({
        status: 'needs_human',
        reason: 'nothing quotable here',
        reason_code: 'SERVICE_NOT_IMPLEMENTED',
      }),
    );
    expect(result.outcome).toBe('refused');
  });

  it('recognises a refusal from an unavailable provenance alone', () => {
    const result = outcomeFor(
      action({ status: 'needs_human', reason: 'no message', provenance_kind: 'unavailable' }),
    );
    expect(result.outcome).toBe('refused');
  });
});

/** Builds the ledger the way the console does, so the tests exercise the real path. */
function ledgerFor(
  detail: IncidentDetail,
  evaluations: AssuranceEvaluation[],
  candidates: CandidatePlan[] = [],
) {
  const resolved = resolveLedger(detail, evaluations, candidates);
  return { resolved, steps: buildStepLedger(resolved.tasks, evaluations, detail.actions) };
}

describe('resolveLedger', () => {
  it('uses the declared plan when nothing has been evaluated or executed', () => {
    const { resolved, steps } = ledgerFor(incident(), []);
    expect(resolved.basis).toBe('declared_plan');
    expect(resolved.supersededProposal).toBeNull();
    expect(steps).toHaveLength(1);
    expect(steps[0]?.outcome).toBe('not_started');
  });

  /**
   * The defect this function exists for, reproduced from the live API.
   *
   * Requesting candidates proposes a new variant, so `GET /incidents/{ref}` advertises plan 9
   * (tasks 41..45) while the evaluations and actions still reference plan 1 (tasks 1..5). Joining on
   * the advertised plan produced a ledger reading "not evaluated" on every step of an incident that
   * had in fact run to resolution.
   */
  it('describes the plan carrying the evidence, not the newest proposal the incident advertises', () => {
    const ranPlan = candidatePlan({
      id: 1,
      variant_key: 'baseline',
      plan_hash: 'ran-hash',
      tasks: [
        {
          id: 1,
          action_type: 'check_connections',
          task_order: 1,
          state: 'succeeded',
          target_refs: ['BOOKING-7'],
          depends_on: [],
        },
        {
          id: 2,
          action_type: 'notify_passengers',
          task_order: 2,
          state: 'succeeded',
          target_refs: [],
          depends_on: [1],
        },
      ],
    });
    const proposal = candidatePlan({
      id: 9,
      variant_key: 'notify-first',
      plan_hash: 'proposal-hash',
      tasks: [
        {
          id: 41,
          action_type: 'notify_passengers',
          task_order: 1,
          state: 'proposed',
          target_refs: [],
          depends_on: [],
        },
        {
          id: 42,
          action_type: 'check_connections',
          task_order: 2,
          state: 'proposed',
          target_refs: [],
          depends_on: [],
        },
      ],
    });
    const detail = incident({
      // The incident contract advertises the newest proposal, exactly as the live API does.
      plan: {
        id: 9,
        generator: 'fallback-playbook',
        prompt_version: null,
        model_self_report: null,
        tasks: [
          task({ id: 41, task_order: 1, action_type: 'notify_passengers', state: 'proposed' }),
          task({ id: 42, task_order: 2, action_type: 'check_connections', state: 'proposed' }),
        ],
      },
      actions: [action({ id: 10, plan_task_id: 1, status: 'success' })],
    });

    const { resolved, steps } = ledgerFor(
      detail,
      [evaluation({ id: 1, plan_task_id: 1, decision: 'execute' })],
      [ranPlan, proposal],
    );

    expect(resolved.basis).toBe('plan_of_record');
    expect(resolved.planId).toBe(1);
    expect(resolved.variantKey).toBe('baseline');
    expect(resolved.planHash).toBe('ran-hash');
    expect(resolved.supersededProposal).toEqual({ planId: 9, variantKey: 'notify-first' });

    expect(steps.map((step) => step.taskId)).toEqual([1, 2]);
    expect(steps[0]?.decision).toBe('execute');
    expect(steps[0]?.outcome).toBe('succeeded');
    expect(steps[0]?.targetRefs).toEqual(['BOOKING-7']);
  });

  it('prefers the candidate carrying the most evidence and breaks ties on the higher plan id', () => {
    const few = candidatePlan({
      id: 1,
      variant_key: 'few',
      tasks: [
        {
          id: 1,
          action_type: 'a',
          task_order: 1,
          state: 'succeeded',
          target_refs: [],
          depends_on: [],
        },
      ],
    });
    const many = candidatePlan({
      id: 2,
      variant_key: 'many',
      tasks: [
        {
          id: 1,
          action_type: 'a',
          task_order: 1,
          state: 'succeeded',
          target_refs: [],
          depends_on: [],
        },
        {
          id: 2,
          action_type: 'b',
          task_order: 2,
          state: 'succeeded',
          target_refs: [],
          depends_on: [],
        },
      ],
    });
    const detail = incident({ plan: null, actions: [] });
    const evaluations = [
      evaluation({ id: 1, plan_task_id: 1 }),
      evaluation({ id: 2, plan_task_id: 2 }),
    ];
    expect(resolveLedger(detail, evaluations, [few, many]).planId).toBe(2);
    expect(resolveLedger(detail, evaluations, [many, few]).planId).toBe(2);
  });

  it('falls back to the declared plan when it is the one carrying the evidence', () => {
    const detail = incident({ actions: [action()] });
    const { resolved } = ledgerFor(detail, [evaluation()], []);
    expect(resolved.basis).toBe('plan_of_record');
    expect(resolved.planId).toBe(3);
  });

  it('rebuilds rows from the evidence when no published plan contains those tasks', () => {
    const detail = incident({
      plan: {
        id: 9,
        generator: 'fallback-playbook',
        prompt_version: null,
        model_self_report: null,
        tasks: [task({ id: 41, task_order: 1, action_type: 'notify_passengers' })],
      },
      actions: [
        action({ id: 10, plan_task_id: 2, action_type: 'find_hotel_options', status: 'success' }),
      ],
    });
    const { resolved, steps } = ledgerFor(
      detail,
      [evaluation({ id: 1, plan_task_id: 1, action_type: 'check_connections' })],
      [],
    );
    expect(resolved.basis).toBe('recorded_evidence_only');
    expect(resolved.planHash).toBeNull();
    expect(steps.map((step) => [step.taskId, step.actionType])).toEqual([
      [1, 'check_connections'],
      [2, 'find_hotel_options'],
    ]);
    expect(steps[0]?.decision).toBe('execute');
    expect(steps[1]?.outcome).toBe('succeeded');
  });

  it('returns nothing when no plan has been proposed and nothing has run', () => {
    const { steps } = ledgerFor(incident({ plan: null }), []);
    expect(steps).toEqual([]);
  });
});

describe('buildStepLedger', () => {
  it('orders by task_order, not by the order the endpoint returned', () => {
    const detail = incident({
      plan: {
        id: 3,
        generator: 'fallback-playbook',
        prompt_version: null,
        model_self_report: null,
        tasks: [
          task({ id: 2, task_order: 2, action_type: 'notify_passengers' }),
          task({ id: 1, task_order: 1, action_type: 'check_connections' }),
        ],
      },
    });
    const { steps } = ledgerFor(detail, []);
    expect(steps.map((step) => step.actionType)).toEqual([
      'check_connections',
      'notify_passengers',
    ]);
  });

  it('joins the gate decision and the execution record on plan_task_id', () => {
    const { steps } = ledgerFor(incident({ actions: [action()] }), [evaluation()]);
    expect(steps[0]?.decision).toBe('execute');
    expect(steps[0]?.riskTier).toBe('low');
    expect(steps[0]?.action?.id).toBe(10);
    expect(steps[0]?.outcome).toBe('succeeded');
  });

  it('ignores records belonging to another task', () => {
    const resolved = resolveLedger(incident(), [], []);
    const steps = buildStepLedger(
      resolved.tasks,
      [evaluation({ id: 6, plan_task_id: 99 })],
      [action({ id: 11, plan_task_id: 99 })],
    );
    expect(steps[0]?.action).toBeNull();
    expect(steps[0]?.evaluation).toBeNull();
  });

  it('takes the latest evaluation by recorded time, with the id as the tie-break', () => {
    const tasks = resolveLedger(incident(), [], []).tasks;
    const first = evaluation({
      id: 5,
      evaluated_at: '2026-08-20T08:00:00Z',
      decision: 'needs_human',
    });
    const later = evaluation({ id: 6, evaluated_at: '2026-08-20T09:00:00Z', decision: 'execute' });
    expect(buildStepLedger(tasks, [first, later], [])[0]?.evaluation?.id).toBe(6);
    expect(buildStepLedger(tasks, [later, first], [])[0]?.evaluation?.id).toBe(6);

    const tieLow = evaluation({ id: 7, evaluated_at: '2026-08-20T09:00:00Z' });
    const tieHigh = evaluation({ id: 8, evaluated_at: '2026-08-20T09:00:00Z' });
    expect(buildStepLedger(tasks, [tieHigh, tieLow], [])[0]?.evaluation?.id).toBe(8);
  });

  it('treats a null executed_at as older than a recorded one', () => {
    const pending = action({
      id: 20,
      executed_at: null,
      status: 'needs_human',
      provenance_kind: 'real',
    });
    const done = action({ id: 21, executed_at: '2026-08-20T09:05:00Z', status: 'success' });
    const { steps } = ledgerFor(incident({ actions: [pending, done] }), []);
    expect(steps[0]?.action?.id).toBe(21);
    expect(steps[0]?.outcome).toBe('succeeded');
  });

  it('carries target_refs from the plan of record and leaves them empty without candidates', () => {
    const detail = incident({ actions: [action()] });
    expect(ledgerFor(detail, [evaluation()], [candidatePlan()]).steps[0]?.targetRefs).toEqual([
      'BOOKING-7',
    ]);
    expect(ledgerFor(detail, [evaluation()], []).steps[0]?.targetRefs).toEqual([]);
  });
});

describe('autonomySplit', () => {
  it('partitions by the decision the server recorded and never assumes a missing one is safe', () => {
    const detail = incident({
      plan: {
        id: 3,
        generator: 'fallback-playbook',
        prompt_version: null,
        model_self_report: null,
        tasks: [
          task({ id: 1, task_order: 1 }),
          task({ id: 2, task_order: 2 }),
          task({ id: 3, task_order: 3 }),
          task({ id: 4, task_order: 4 }),
        ],
      },
    });
    const { steps } = ledgerFor(detail, [
      evaluation({ id: 1, plan_task_id: 1, decision: 'execute' }),
      evaluation({ id: 2, plan_task_id: 2, decision: 'execute_flagged' }),
      evaluation({ id: 3, plan_task_id: 3, decision: 'needs_human' }),
    ]);
    const split = autonomySplit(steps);
    expect(split.execute).toHaveLength(1);
    expect(split.executeFlagged).toHaveLength(1);
    expect(split.needsHuman).toHaveLength(1);
    expect(split.unevaluated).toHaveLength(1);
    expect(split.unevaluated[0]?.taskId).toBe(4);
  });
});

describe('troubledSteps', () => {
  it('keeps refusals apart from failures', () => {
    const detail = incident({
      plan: {
        id: 3,
        generator: 'fallback-playbook',
        prompt_version: null,
        model_self_report: null,
        tasks: [task({ id: 1, task_order: 1 }), task({ id: 2, task_order: 2 })],
      },
      actions: [
        action({ id: 10, plan_task_id: 1, status: 'needs_human', provenance_kind: 'unavailable' }),
        action({ id: 11, plan_task_id: 2, status: 'failure' }),
      ],
    });
    const trouble = troubledSteps(ledgerFor(detail, []).steps);
    expect(trouble.refused.map((step) => step.taskId)).toEqual([1]);
    expect(trouble.failed.map((step) => step.taskId)).toEqual([2]);
  });
});

describe('toolUse', () => {
  it('reports only tools that were actually invoked, grouped by action type', () => {
    const detail = incident({
      plan: {
        id: 3,
        generator: 'fallback-playbook',
        prompt_version: null,
        model_self_report: null,
        tasks: [
          task({ id: 1, task_order: 1, action_type: 'check_connections' }),
          task({ id: 2, task_order: 2, action_type: 'check_connections' }),
          task({ id: 3, task_order: 3, action_type: 'reserve_hotel_block' }),
        ],
      },
      actions: [
        action({ id: 10, plan_task_id: 1, status: 'success' }),
        action({ id: 11, plan_task_id: 2, status: 'failure' }),
      ],
    });
    const used = toolUse(ledgerFor(detail, []).steps);
    expect(used).toHaveLength(1);
    expect(used[0]).toMatchObject({
      actionType: 'check_connections',
      invocations: 2,
      succeeded: 1,
      failed: 1,
      refused: 0,
      awaitingHuman: 0,
    });
  });
});

describe('activityByActor', () => {
  it('counts frames per actor kind and lists the distinct actors behind them', () => {
    const activity = activityByActor([
      frame({ sequence: 1, actor: 'orchestrator', actor_kind: 'orchestrator' }),
      frame({ sequence: 2, actor: 'connection_service', actor_kind: 'service' }),
      frame({ sequence: 3, actor: 'hotel_service', actor_kind: 'service' }),
      frame({ sequence: 4, actor: 'operator-1', actor_kind: 'human' }),
    ]);
    expect(activity.map((row) => row.actorKind)).toEqual(['orchestrator', 'service', 'human']);
    expect(activity[1]?.frames).toBe(2);
    expect(activity[1]?.actors).toEqual(['connection_service', 'hotel_service']);
    expect(activity[2]?.frames).toBe(1);
  });

  it('is empty for no frames rather than inventing a zeroed actor list', () => {
    expect(activityByActor([])).toEqual([]);
  });
});

describe('describePayload', () => {
  it('reflects the shape without interpreting any field', () => {
    const entries = describePayload({
      at_risk: [1, 2, 3],
      minimum_connection_minutes: 45,
      recovered: true,
      shortfall_minutes: null,
      rule_version: 'connections-v1',
      nested: { a: 1, b: 2 },
    });
    expect(entries.map((entry) => entry.key)).toEqual([
      'at_risk',
      'minimum_connection_minutes',
      'nested',
      'recovered',
      'rule_version',
      'shortfall_minutes',
    ]);
    expect(entries.find((entry) => entry.key === 'at_risk')).toEqual({
      key: 'at_risk',
      kind: 'array',
      display: '3 entries',
    });
    expect(entries.find((entry) => entry.key === 'nested')).toEqual({
      key: 'nested',
      kind: 'object',
      display: '2 fields',
    });
    expect(entries.find((entry) => entry.key === 'shortfall_minutes')).toEqual({
      key: 'shortfall_minutes',
      kind: 'null',
      display: null,
    });
    expect(entries.find((entry) => entry.key === 'recovered')?.display).toBe('true');
    expect(entries.find((entry) => entry.key === 'rule_version')?.display).toBe('connections-v1');
  });

  it('uses the singular for a one-entry collection', () => {
    expect(describePayload({ rows: [1] })[0]?.display).toBe('1 entry');
    expect(describePayload({ obj: { only: 1 } })[0]?.display).toBe('1 field');
  });
});

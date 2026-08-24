/**
 * The agent step ledger: one row per planned task, joined to what the gate decided and what
 * actually ran.
 *
 * This module exists because the three facts an operator needs about a step arrive from three
 * different endpoints and are keyed on `plan_task_id`:
 *
 *   - **intent** — `GET /incidents/{ref}` → `plan.tasks[]` (what the agent means to do, in order)
 *   - **authorisation** — `GET /incidents/{ref}/assurance` → `evaluations[]` (what the gate allows)
 *   - **outcome** — `GET /incidents/{ref}` → `actions[]` (what the tool actually did)
 *
 * Joining them on the client is not a business calculation: no figure is derived, no state is
 * inferred, and every field on an `AgentStep` is a value one of those endpoints returned. The
 * alternative — three tables an operator reads side by side and correlates by integer id — is
 * how a refused action gets mistaken for a pending one.
 *
 * Two rules the joins obey:
 *
 *   1. **The plan is the spine.** A task with no evaluation and no action is `not_started`, not
 *      absent. Dropping it would hide work the agent has declared it intends to do.
 *   2. **Latest record wins, and only by recorded time.** Where several evaluations or actions
 *      exist for one task, the most recent by the server's own timestamp is authoritative, with
 *      the row id as the tie-break. The UI never picks by "looks more complete".
 *
 * Owner: Stream D.
 */

import type {
  ActionRecord,
  AssuranceDecision,
  AssuranceEvaluation,
  CandidatePlan,
  CheckName,
  IncidentDetail,
  ReplayFrame,
  RiskTier,
} from '@/api/types';
import { refusalFor, SERVICE_NOT_IMPLEMENTED, type RefusalInfo } from '@/features/incident/refusal';

/**
 * A task, normalised across the two contracts that publish one.
 *
 * `plan.tasks[]` on the incident and `plans[].tasks[]` on the candidate contract carry the same rows
 * with two differences: `depends_on` is a string array on one and a number array on the other, and
 * only the candidate contract carries `target_refs`. Normalising here keeps that seam in one place.
 */
export interface LedgerTask {
  id: number;
  taskOrder: number;
  actionType: string;
  state: string;
  dependsOn: string[];
  targetRefs: string[];
}

/**
 * Which plan the ledger was built from, and how that was decided.
 *
 * - `plan_of_record` — the plan whose tasks carry the recorded evaluations and actions. This is the
 *   one that actually ran and the only honest basis for an outcome ledger.
 * - `declared_plan` — nothing has been evaluated or executed yet, so the plan the incident declares
 *   is all there is. Every step is legitimately `not_started`.
 * - `recorded_evidence_only` — evidence exists but no published plan contains those task ids, so
 *   the rows are reconstructed from the evaluations and actions themselves. Ordering and target
 *   references are unavailable in that case and the console says so.
 */
export type LedgerBasis = 'plan_of_record' | 'declared_plan' | 'recorded_evidence_only';

export interface ResolvedLedger {
  basis: LedgerBasis;
  tasks: LedgerTask[];
  planId: number | null;
  variantKey: string | null;
  planHash: string | null;
  generator: string | null;
  rationale: string | null;
  /**
   * A candidate the incident contract is currently advertising as `plan` which is NOT the plan that
   * ran. Its presence is a fact an operator has to see: proposing candidates creates newer plan
   * rows, and `GET /incidents/{ref}` surfaces the newest one, so the headline plan on that contract
   * can be a proposal nothing has authorised.
   */
  supersededProposal: { planId: number; variantKey: string | null } | null;
  candidateCount: number;
}

/**
 * What happened to a step, as a single token.
 *
 * `refused` is deliberately separate from `failed`. A refusal means no service was registered to
 * carry the task out, so nothing was attempted and nothing may be claimed — an operator approving
 * it would change nothing. A failure means a service ran and did not succeed. `dispatch.py`
 * distinguishes them with `reason_code` and `provenance_kind`, and flattening the two would tell
 * the operator to go and approve something that cannot execute.
 *
 * `awaiting_human` is the third: the service DID run, recorded evidence, and surfaced a decision
 * that belongs to a person.
 */
export type StepOutcome =
  'not_started' | 'succeeded' | 'failed' | 'skipped' | 'refused' | 'awaiting_human';

export interface AgentStep {
  /** `plan_task.id` — the join key across all three endpoints. */
  taskId: number;
  taskOrder: number;
  /** The tool the agent intends to invoke. A closed vocabulary of 11 on the backend. */
  actionType: string;
  taskState: string;
  /** Ids as the plan published them. */
  dependsOn: string[];

  /** The gate record, or null when this task has not been evaluated yet. */
  evaluation: AssuranceEvaluation | null;
  decision: AssuranceDecision | null;
  riskTier: RiskTier | null;
  blocking: CheckName[];

  /** The execution record, or null when nothing has been attempted. */
  action: ActionRecord | null;
  outcome: StepOutcome;
  /** Set only when the outcome is `refused`, carrying the designed copy for the code. */
  refusal: RefusalInfo | null;

  /**
   * The entities this task names, from the candidate-plan contract (`target_refs`).
   *
   * This is the closest thing to a tool INPUT that any endpoint publishes. `plan_task.inputs` is
   * persisted by the backend but is on no response model, so the refs are what can be shown, and
   * the panel says so rather than implying these are the full arguments.
   */
  targetRefs: string[];
}

function latestBy<T>(rows: T[], at: (row: T) => string | null, id: (row: T) => number): T | null {
  if (rows.length === 0) return null;
  return rows.reduce((best, row) => {
    const bestAt = at(best) ?? '';
    const rowAt = at(row) ?? '';
    if (rowAt > bestAt) return row;
    if (rowAt < bestAt) return best;
    return id(row) > id(best) ? row : best;
  });
}

/**
 * Classifies one step's outcome from the recorded action.
 *
 * Every branch reads a server field. Nothing is inferred from prose except the documented
 * `SERVICE_NOT_IMPLEMENTED` prefix, which `dispatch.py` states is stable so that the UI can map
 * it to copy — and even then only as one of three independent signals.
 */
export function outcomeFor(action: ActionRecord | null): {
  outcome: StepOutcome;
  refusal: RefusalInfo | null;
} {
  if (!action) return { outcome: 'not_started', refusal: null };

  if (action.status === 'success') return { outcome: 'succeeded', refusal: null };
  if (action.status === 'failure') return { outcome: 'failed', refusal: null };
  if (action.status === 'skipped') return { outcome: 'skipped', refusal: null };

  if (action.status === 'needs_human') {
    const refusal = refusalFor(action.reason);
    const byCode = action.reason_code === SERVICE_NOT_IMPLEMENTED;
    const byProvenance = action.provenance_kind === 'unavailable';
    if (refusal || byCode || byProvenance) {
      return { outcome: 'refused', refusal };
    }
    return { outcome: 'awaiting_human', refusal: null };
  }

  // An unrecognised status is reported as-is by the caller rather than guessed at here.
  return { outcome: 'not_started', refusal: null };
}

/**
 * Decides which plan the ledger describes.
 *
 * This is the load-bearing function of the whole console, and it exists because of a real trap in
 * the contracts. Asking `GET /incidents/{ref}/plans` PROPOSES candidate variants, which inserts new
 * plan rows with new plan tasks. `GET /incidents/{ref}` then reports the newest of those as
 * `plan` — so the headline plan on the incident contract can be a variant that nothing evaluated
 * and nothing ran, while the evaluations and actions still reference the original plan's tasks.
 *
 * Joining the ledger on the incident's advertised plan therefore produced a screen where every step
 * read "not evaluated" and the autonomy split was all zeroes, on an incident that had in fact run to
 * resolution. That is precisely the quiet, plausible-looking wrong answer this product treats as the
 * worst class of bug, so the plan is chosen by evidence instead:
 *
 *   **the plan of record is the published plan whose tasks carry the recorded evaluations and
 *   actions**, and where nothing has been recorded yet, the declared plan is used as declared.
 *
 * Nothing is inferred beyond that intersection, and the basis is reported so the screen can state
 * which plan it is describing.
 */
export function resolveLedger(
  incident: IncidentDetail,
  evaluations: AssuranceEvaluation[],
  candidates: CandidatePlan[],
): ResolvedLedger {
  const evidenced = new Set<number>([
    ...evaluations.map((row) => row.plan_task_id),
    ...incident.actions.map((row) => row.plan_task_id),
  ]);

  const declared = incident.plan;
  const declaredTasks: LedgerTask[] = (declared?.tasks ?? []).map((task) => ({
    id: task.id,
    taskOrder: task.task_order,
    actionType: task.action_type,
    state: task.state,
    dependsOn: task.depends_on,
    targetRefs: [],
  }));

  const fromCandidate = (plan: CandidatePlan): LedgerTask[] =>
    plan.tasks.map((task) => ({
      id: task.id,
      taskOrder: task.task_order,
      actionType: task.action_type,
      state: task.state,
      dependsOn: task.depends_on.map(String),
      targetRefs: task.target_refs,
    }));

  const sortTasks = (tasks: LedgerTask[]): LedgerTask[] =>
    tasks.slice().sort((a, b) => a.taskOrder - b.taskOrder || a.id - b.id);

  // Nothing has been evaluated or executed: the declared plan is the whole truth.
  if (evidenced.size === 0) {
    return {
      basis: 'declared_plan',
      tasks: sortTasks(declaredTasks),
      planId: declared?.id ?? null,
      variantKey: candidates.find((plan) => plan.id === declared?.id)?.variant_key ?? null,
      planHash: candidates.find((plan) => plan.id === declared?.id)?.plan_hash ?? null,
      generator: declared?.generator ?? null,
      rationale: declared?.rationale ?? null,
      supersededProposal: null,
      candidateCount: candidates.length,
    };
  }

  // The candidate whose tasks carry the most recorded evidence. Ties break on the higher plan id.
  let best: { plan: CandidatePlan; matches: number } | null = null;
  for (const plan of candidates) {
    const matches = plan.tasks.filter((task) => evidenced.has(task.id)).length;
    if (matches === 0) continue;
    if (!best || matches > best.matches || (matches === best.matches && plan.id > best.plan.id)) {
      best = { plan, matches };
    }
  }

  if (best) {
    const superseded =
      declared && declared.id !== best.plan.id
        ? {
            planId: declared.id,
            variantKey: candidates.find((plan) => plan.id === declared.id)?.variant_key ?? null,
          }
        : null;
    return {
      basis: 'plan_of_record',
      tasks: sortTasks(fromCandidate(best.plan)),
      planId: best.plan.id,
      variantKey: best.plan.variant_key,
      planHash: best.plan.plan_hash,
      generator: best.plan.generator,
      rationale: best.plan.rationale,
      supersededProposal: superseded,
      candidateCount: candidates.length,
    };
  }

  // No candidate matched. The declared plan still might.
  if (declaredTasks.some((task) => evidenced.has(task.id))) {
    return {
      basis: 'plan_of_record',
      tasks: sortTasks(declaredTasks),
      planId: declared?.id ?? null,
      variantKey: null,
      planHash: null,
      generator: declared?.generator ?? null,
      rationale: declared?.rationale ?? null,
      supersededProposal: null,
      candidateCount: candidates.length,
    };
  }

  /*
   * Evidence exists but no published plan contains those task ids — which happens when the candidate
   * contract is unavailable and the incident advertises a different plan. The rows are rebuilt from
   * the evaluations and actions themselves, which do carry the action type. Ordering is by task id
   * because no `task_order` is available, and the screen reports the weaker basis rather than
   * implying the plan's own sequence.
   */
  const byId = new Map<number, LedgerTask>();
  for (const evaluation of evaluations) {
    if (byId.has(evaluation.plan_task_id)) continue;
    byId.set(evaluation.plan_task_id, {
      id: evaluation.plan_task_id,
      taskOrder: 0,
      actionType: evaluation.action_type,
      state: 'not recorded',
      dependsOn: [],
      targetRefs: [],
    });
  }
  for (const action of incident.actions) {
    if (byId.has(action.plan_task_id)) continue;
    byId.set(action.plan_task_id, {
      id: action.plan_task_id,
      taskOrder: 0,
      actionType: action.action_type ?? 'not recorded',
      state: 'not recorded',
      dependsOn: [],
      targetRefs: [],
    });
  }
  return {
    basis: 'recorded_evidence_only',
    tasks: [...byId.values()].sort((a, b) => a.id - b.id),
    planId: null,
    variantKey: null,
    planHash: null,
    generator: declared?.generator ?? null,
    rationale: declared?.rationale ?? null,
    supersededProposal:
      declared && !declaredTasks.some((task) => evidenced.has(task.id))
        ? { planId: declared.id, variantKey: null }
        : null,
    candidateCount: candidates.length,
  };
}

/** Builds the ledger over the tasks of the plan the resolver settled on. */
export function buildStepLedger(
  tasks: LedgerTask[],
  evaluations: AssuranceEvaluation[],
  actions: ActionRecord[],
): AgentStep[] {
  return tasks.map((task) => {
    const evaluation = latestBy(
      evaluations.filter((row) => row.plan_task_id === task.id),
      (row) => row.evaluated_at,
      (row) => row.id,
    );
    const action = latestBy(
      actions.filter((row) => row.plan_task_id === task.id),
      (row) => row.executed_at,
      (row) => row.id,
    );
    const { outcome, refusal } = outcomeFor(action);

    return {
      taskId: task.id,
      taskOrder: task.taskOrder,
      actionType: task.actionType,
      taskState: task.state,
      dependsOn: task.dependsOn,
      evaluation,
      decision: evaluation?.decision ?? null,
      riskTier: evaluation?.risk_tier ?? null,
      blocking: evaluation?.blocking ?? [],
      action,
      outcome,
      refusal,
      targetRefs: task.targetRefs,
    };
  });
}

/**
 * How the gate partitioned the work: run without asking, run and flag, or stop for a person.
 *
 * These are array lengths over a partition the server already decided, which is the one aggregate
 * this console is allowed to compute. It never re-derives a decision: a step with no evaluation
 * lands in `unevaluated` rather than being assumed safe.
 */
export function autonomySplit(steps: AgentStep[]): {
  execute: AgentStep[];
  executeFlagged: AgentStep[];
  needsHuman: AgentStep[];
  unevaluated: AgentStep[];
} {
  return {
    execute: steps.filter((step) => step.decision === 'execute'),
    executeFlagged: steps.filter((step) => step.decision === 'execute_flagged'),
    needsHuman: steps.filter((step) => step.decision === 'needs_human'),
    unevaluated: steps.filter((step) => step.decision === null),
  };
}

/** Steps where a tool call did not produce a result: refusals and failures, kept apart. */
export function troubledSteps(steps: AgentStep[]): {
  refused: AgentStep[];
  failed: AgentStep[];
} {
  return {
    refused: steps.filter((step) => step.outcome === 'refused'),
    failed: steps.filter((step) => step.outcome === 'failed' || step.outcome === 'skipped'),
  };
}

/**
 * Which tools were actually invoked, and with what result.
 *
 * Grouped by `action_type` because the same tool can be dispatched by more than one task. This is
 * an observed ledger, not a capability catalogue: the backend's `SERVICE_REGISTRY` is on no
 * endpoint, so the console can only report what it has seen run.
 */
export interface ToolUse {
  actionType: string;
  invocations: number;
  succeeded: number;
  refused: number;
  failed: number;
  awaitingHuman: number;
}

export function toolUse(steps: AgentStep[]): ToolUse[] {
  const order: string[] = [];
  const byType = new Map<string, AgentStep[]>();
  for (const step of steps) {
    if (step.action === null) continue;
    if (!byType.has(step.actionType)) {
      byType.set(step.actionType, []);
      order.push(step.actionType);
    }
    byType.get(step.actionType)?.push(step);
  }
  return order.map((actionType) => {
    const rows = byType.get(actionType) ?? [];
    return {
      actionType,
      invocations: rows.length,
      succeeded: rows.filter((row) => row.outcome === 'succeeded').length,
      refused: rows.filter((row) => row.outcome === 'refused').length,
      failed: rows.filter((row) => row.outcome === 'failed' || row.outcome === 'skipped').length,
      awaitingHuman: rows.filter((row) => row.outcome === 'awaiting_human').length,
    };
  });
}

/**
 * Activity by actor, from replay frames.
 *
 * `actor_kind` is the backend's own five-value mapping (`app/api/actors.py`). Counting frames per
 * actor is what makes "the agent did eleven things and a person did one" legible, and it is the
 * figure that stops an autonomous console from reading as though nobody was involved.
 */
export interface ActorActivity {
  actorKind: string;
  frames: number;
  actors: string[];
}

export function activityByActor(frames: ReplayFrame[]): ActorActivity[] {
  const order: string[] = [];
  const byKind = new Map<string, ReplayFrame[]>();
  for (const frame of frames) {
    if (!byKind.has(frame.actor_kind)) {
      byKind.set(frame.actor_kind, []);
      order.push(frame.actor_kind);
    }
    byKind.get(frame.actor_kind)?.push(frame);
  }
  return order.map((actorKind) => {
    const rows = byKind.get(actorKind) ?? [];
    return {
      actorKind,
      frames: rows.length,
      actors: [...new Set(rows.map((row) => row.actor))].sort(),
    };
  });
}

/**
 * Structural reflection of a recorded tool output.
 *
 * `ActionDetailResponse.payload` is explicitly service-shaped and version-gated: the backend
 * documents it as opaque. So this describes the SHAPE — key, kind, and a primitive's value or a
 * collection's length — and never interprets a field or computes across fields. An array's length
 * is reported because that is a property of the payload, not a finding derived from it.
 */
export interface PayloadEntry {
  key: string;
  kind: 'string' | 'number' | 'boolean' | 'null' | 'array' | 'object';
  /** Primitives render their value; collections render their size. Null renders as absent. */
  display: string | null;
}

export function describePayload(payload: Record<string, unknown>): PayloadEntry[] {
  return Object.keys(payload)
    .sort()
    .map((key) => {
      const value = payload[key];
      if (value === null || value === undefined)
        return { key, kind: 'null' as const, display: null };
      if (Array.isArray(value)) {
        return {
          key,
          kind: 'array' as const,
          display: `${value.length} ${value.length === 1 ? 'entry' : 'entries'}`,
        };
      }
      if (typeof value === 'object') {
        const keys = Object.keys(value as Record<string, unknown>).length;
        return {
          key,
          kind: 'object' as const,
          display: `${keys} ${keys === 1 ? 'field' : 'fields'}`,
        };
      }
      if (typeof value === 'number')
        return { key, kind: 'number' as const, display: String(value) };
      if (typeof value === 'boolean') {
        return { key, kind: 'boolean' as const, display: value ? 'true' : 'false' };
      }
      return { key, kind: 'string' as const, display: String(value) };
    });
}

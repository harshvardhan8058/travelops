/**
 * Recovery workspace, centre column — Plan and execution.
 *
 * The generator chip at the top is the most important 40px on the screen. A judge must never
 * have to ask whether a model produced this plan. It states one of two things and never
 * anything ambiguous:
 *
 *   Planner · groq · llama-3.3-70b · prompt v1
 *   Fallback playbook · deterministic
 *
 * Classification comes from the `generator` string as returned, and the raw value is printed
 * alongside it, so the chip cannot quietly disagree with the record.
 *
 * Owner: Stream D.
 */

import { ChevronDown, ChevronRight, Cpu, Workflow } from 'lucide-react';
import { useState } from 'react';
import { clsx } from 'clsx';

import type { ActionRecord, IncidentDetail, PlanSummary, PlanTaskRow } from '@/api/types';
import { EmptyState, MonoValue, Panel, StateBadge, WhyPopover } from '@/components/ui/primitives';
import { actionDerivation } from '@/components/ui/derivation';
import { CountBar } from '@/components/ui/Metric';
import {
  Absent,
  DefinitionList,
  DefinitionRow,
  rowSelectionClass,
} from '@/components/ui/composition';
import { utcStamp } from '@/components/ui/format';
import { refusalFor } from './refusal';

/**
 * A plan is either model-proposed or deterministic. There is no third state, and no "maybe":
 * `LLM_MODE=off` must still complete a recovery, so the fallback is a first-class path rather
 * than an error condition.
 */
function GeneratorChip({ plan }: { plan: PlanSummary }) {
  // Classifies on the token, not the prose: the real API returns 'fallback-playbook' while the
  // committed fixture returns 'fallback-playbook · deterministic'.
  const isDeterministic = /fallback|playbook|deterministic/i.test(plan.generator);
  const Icon = isDeterministic ? Workflow : Cpu;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span
        className={clsx(
          'inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5 text-label uppercase',
          isDeterministic
            ? 'border-border-strong bg-inset text-fg-secondary'
            : 'border-accent-border bg-accent-subtle text-accent',
        )}
      >
        <Icon size={12} strokeWidth={1.5} aria-hidden />
        {isDeterministic ? (
          <span>Fallback playbook · deterministic</span>
        ) : (
          <span>
            Planner · {plan.generator}
            {plan.prompt_version ? ` · prompt ${plan.prompt_version}` : ' · prompt unversioned'}
          </span>
        )}
      </span>

      {/* The raw record, so the classification above is auditable rather than trusted. */}
      <span className="text-caption text-fg-muted">
        generator <MonoValue muted>{plan.generator}</MonoValue>
      </span>

      {isDeterministic && (
        <span className="text-caption text-fg-muted">no model produced this plan</span>
      )}

      {plan.model_self_report !== null && (
        <span className="text-caption text-fg-muted">
          model self-report <MonoValue muted>{plan.model_self_report}</MonoValue> · diagnostic only,
          never gates execution
        </span>
      )}
    </div>
  );
}

function TaskRow({
  task,
  action,
  incident,
  isSelected,
  isExpanded,
  onSelect,
  onToggleExpand,
}: {
  task: PlanTaskRow;
  action?: ActionRecord;
  incident: IncidentDetail;
  isSelected: boolean;
  isExpanded: boolean;
  onSelect: () => void;
  onToggleExpand: () => void;
}) {
  return (
    <li className={rowSelectionClass(isSelected)}>
      <div className="flex items-center gap-2 px-2">
        {/*
         * Selection and expansion are separate buttons rather than one clickable row: the row
         * also holds popovers, and interactive content inside a button is invalid HTML and
         * breaks tab order.
         */}
        <button
          type="button"
          onClick={onSelect}
          aria-current={isSelected}
          className="flex min-w-0 flex-1 items-center gap-2 py-1.5 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <MonoValue muted className="w-4 shrink-0 text-right">
            {task.task_order}
          </MonoValue>
          <MonoValue className={clsx('shrink-0', isSelected && 'text-accent')}>
            {task.action_type}
          </MonoValue>
          {task.depends_on.length > 0 && (
            <span className="shrink-0 rounded-sm border border-border-subtle bg-inset px-1 py-0.5 text-caption text-fg-muted">
              after {task.depends_on.join(', ')}
            </span>
          )}
          <span className="ml-auto shrink-0">
            <StateBadge status={task.state} />
          </span>
        </button>

        <button
          type="button"
          onClick={onToggleExpand}
          aria-expanded={isExpanded}
          aria-label={`${isExpanded ? 'Collapse' : 'Expand'} task ${task.task_order} detail`}
          className="shrink-0 rounded-sm p-1 text-fg-muted transition-colors duration-hover ease-out hover:text-fg-secondary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {isExpanded ? (
            <ChevronDown size={14} strokeWidth={1.5} aria-hidden />
          ) : (
            <ChevronRight size={14} strokeWidth={1.5} aria-hidden />
          )}
        </button>
      </div>

      {isExpanded && (
        <DefinitionList className="border-t border-border-subtle bg-inset px-3 py-2">
          <DefinitionRow label="task id">
            <MonoValue>{task.id}</MonoValue>
          </DefinitionRow>
          <DefinitionRow label="assurance">
            {task.assurance_id === null ? (
              <Absent
                label="not evaluated yet"
                title="No assurance evaluation references this task. Nothing is assumed to have passed."
              />
            ) : (
              <MonoValue>{task.assurance_id}</MonoValue>
            )}
          </DefinitionRow>
          <DefinitionRow label="action">
            <>
              {action ? (
                <>
                  <WhyPopover derivation={actionDerivation(action, incident)}>
                    <span className="inline-flex items-center gap-1.5">
                      <StateBadge
                        status={action.status === 'success' ? 'succeeded' : action.status}
                      />
                      <MonoValue muted>{action.actor}</MonoValue>
                    </span>
                  </WhyPopover>
                  {/* A refused action gets designed copy keyed off its stable reason code. */}
                  <p
                    className={
                      refusalFor(action.reason)
                        ? 'mt-1 text-caption text-state-warn'
                        : 'mt-1 text-caption text-fg-muted'
                    }
                  >
                    {refusalFor(action.reason)?.headline ?? action.reason}
                  </p>
                </>
              ) : (
                <Absent label="nothing executed" title="No action record references this task." />
              )}
            </>
          </DefinitionRow>
          {action && (
            <DefinitionRow label="idempotency">
              <MonoValue muted className="break-all text-caption">
                {action.idempotency_key}
              </MonoValue>
            </DefinitionRow>
          )}
          {/* Named, not omitted: the endpoint returns no per-task inputs. */}
          <DefinitionRow label="inputs">
            <span className="text-caption text-fg-muted">
              not recorded on this endpoint — task-level inputs are not returned by GET /incidents/
              {'{id}'}
            </span>
          </DefinitionRow>
        </DefinitionList>
      )}
    </li>
  );
}

export function PlanColumn({
  incident,
  selectedTaskId,
  onSelectTask,
}: {
  incident: IncidentDetail;
  selectedTaskId: number | null;
  onSelectTask: (taskId: number) => void;
}) {
  const { plan, actions } = incident;
  const actionByTask = new Map(actions.map((action) => [action.plan_task_id, action]));

  /**
   * The plan's task states, partitioned. Tones follow `StateBadge`'s own mapping so the bar and the
   * badges beside it cannot disagree about what amber means.
   */
  const taskStateSegments = (() => {
    if (!plan) return [];
    const tally = new Map<string, number>();
    for (const task of plan.tasks) tally.set(task.state, (tally.get(task.state) ?? 0) + 1);
    const tone = (state: string) =>
      state === 'succeeded' || state === 'executed'
        ? ('ok' as const)
        : state === 'awaiting_approval' || state === 'needs_human'
          ? ('warn' as const)
          : state === 'failed' || state === 'blocked'
            ? ('crit' as const)
            : state === 'executing'
              ? ('info' as const)
              : ('neutral' as const);
    return [...tally.entries()].map(([state, count]) => ({
      label: state.replace(/_/g, ' '),
      count,
      tone: tone(state),
    }));
  })();

  /*
   * `plan` is null until the orchestrator proposes one — the normal state of a freshly opened
   * incident, confirmed against the live API. Designed copy rather than an empty panel, which
   * would read as a failed fetch during a demo.
   */
  if (!plan) {
    return (
      <Panel title="Plan" className="flex min-w-0 flex-col">
        <EmptyState
          title="No plan proposed yet"
          description={
            incident.state === 'detected' || incident.state === 'assessing'
              ? 'The orchestrator proposes a plan during the planning stage. Run the workflow to advance this incident.'
              : `The endpoint returned no plan for this incident while it is in ${incident.state}. Nothing is inferred from the task list, because there is no task list.`
          }
        />
      </Panel>
    );
  }

  return (
    <Panel title="Plan" className="flex min-w-0 flex-col">
      {/*
       * How the plan presents itself: who authored it, why, what it is made of.
       *
       * The rationale was `text-caption text-fg-muted` — the quietest type in the system — which
       * meant the model's or the playbook's own reasoning was the least readable thing in a panel
       * about that reasoning. It is now body copy. Everything else about the plan's identity moves
       * into a definition list so the three facts line up instead of running together as one wrapped
       * sentence of `plan 12 generated ... tasks 6`.
       */}
      <div className="flex flex-col gap-2.5 border-b border-border-subtle px-3 py-2.5">
        <GeneratorChip plan={plan} />

        {plan.rationale ? (
          <p className="text-body text-fg-secondary">{plan.rationale}</p>
        ) : (
          <Absent
            label="no rationale recorded"
            title="This plan carries no rationale. Absent, not empty."
          />
        )}

        <DefinitionList width="sm">
          <DefinitionRow label="plan" width="sm">
            <MonoValue muted>{plan.id}</MonoValue>
          </DefinitionRow>
          <DefinitionRow label="generated" width="sm">
            <MonoValue muted>{utcStamp(plan.generated_at) ?? 'not recorded'}</MonoValue>
          </DefinitionRow>
          <DefinitionRow label="tasks" width="sm">
            <MonoValue muted>{plan.tasks.length}</MonoValue>
          </DefinitionRow>
        </DefinitionList>

        {/*
         * What state the plan's tasks are actually in, as a partition of the tasks returned. A
         * count, never a trend — the same rule `CountBar` is built around. It answers "how far
         * along is this plan" without the operator counting badges down the list.
         */}
        {taskStateSegments.length > 0 && (
          <CountBar segments={taskStateSegments} total={plan.tasks.length} />
        )}
      </div>

      <ol className="min-w-0">
        {plan.tasks.map((task) => (
          <TaskRowContainer
            key={task.id}
            task={task}
            action={actionByTask.get(task.id)}
            incident={incident}
            isSelected={task.id === selectedTaskId}
            onSelect={() => onSelectTask(task.id)}
          />
        ))}
      </ol>
    </Panel>
  );
}

/** Expansion is per-row local state: it is a view preference, not workflow state. */
function TaskRowContainer(props: {
  task: PlanTaskRow;
  action?: ActionRecord;
  incident: IncidentDetail;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const [isExpanded, setIsExpanded] = useState(false);
  return (
    <TaskRow
      {...props}
      isExpanded={isExpanded}
      onToggleExpand={() => setIsExpanded((value) => !value)}
    />
  );
}

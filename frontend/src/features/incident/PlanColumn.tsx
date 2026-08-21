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
    <li
      className={clsx(
        'border-b border-l-2 border-border-subtle',
        isSelected ? 'border-l-accent bg-raised' : 'border-l-transparent',
      )}
    >
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
        <dl className="border-t border-border-subtle bg-inset px-3 py-2">
          <div className="flex gap-2 py-0.5">
            <dt className="w-[104px] shrink-0 text-caption uppercase text-fg-muted">task id</dt>
            <dd>
              <MonoValue>{task.id}</MonoValue>
            </dd>
          </div>
          <div className="flex gap-2 py-0.5">
            <dt className="w-[104px] shrink-0 text-caption uppercase text-fg-muted">assurance</dt>
            <dd>
              {task.assurance_id === null ? (
                <span className="text-caption text-fg-muted">not evaluated yet</span>
              ) : (
                <MonoValue>{task.assurance_id}</MonoValue>
              )}
            </dd>
          </div>
          <div className="flex gap-2 py-0.5">
            <dt className="w-[104px] shrink-0 text-caption uppercase text-fg-muted">action</dt>
            <dd className="min-w-0 flex-1">
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
                <span className="text-caption text-fg-muted">
                  nothing executed — no action record references this task
                </span>
              )}
            </dd>
          </div>
          {action && (
            <div className="flex gap-2 py-0.5">
              <dt className="w-[104px] shrink-0 text-caption uppercase text-fg-muted">
                idempotency
              </dt>
              <dd className="min-w-0 flex-1">
                <MonoValue muted className="break-all text-caption">
                  {action.idempotency_key}
                </MonoValue>
              </dd>
            </div>
          )}
          {/* Named, not omitted: the endpoint returns no per-task inputs. */}
          <div className="flex gap-2 py-0.5">
            <dt className="w-[104px] shrink-0 text-caption uppercase text-fg-muted">inputs</dt>
            <dd className="text-caption text-fg-muted">
              not recorded on this endpoint — task-level inputs are not returned by GET /incidents/
              {'{id}'}
            </dd>
          </div>
        </dl>
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

  /*
   * `plan` is null until the orchestrator proposes one — the normal state of a freshly opened
   * incident, confirmed against the live API. Designed copy rather than an empty panel, which
   * would read as a failed fetch during a demo.
   */
  if (!plan) {
    return (
      <Panel title="Plan" className="flex min-h-0 flex-col overflow-hidden">
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
    <Panel title="Plan" className="flex min-h-0 flex-col overflow-hidden">
      <div className="border-b border-border-subtle px-3 py-2">
        <GeneratorChip plan={plan} />
        {plan.rationale && <p className="mt-1.5 text-caption text-fg-muted">{plan.rationale}</p>}
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 text-caption text-fg-muted">
          <span>
            plan <MonoValue muted>{plan.id}</MonoValue>
          </span>
          {plan.generated_at && (
            <span>
              generated{' '}
              <MonoValue muted>
                {plan.generated_at.slice(0, 10)} {plan.generated_at.slice(11, 19)}Z
              </MonoValue>
            </span>
          )}
          <span>
            tasks <MonoValue muted>{plan.tasks.length}</MonoValue>
          </span>
        </div>
      </div>

      <ol className="min-h-0 flex-1 overflow-y-auto">
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

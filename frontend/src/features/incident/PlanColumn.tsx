/**
 * Recovery workspace, centre column — Plan and execution.
 *
 * The generator chip at the top is the most important 40px on the screen. It classifies only
 * recorded tokens the console recognises: MODEL-AUTHORED for the recorded planner/model tokens,
 * DETERMINISTIC FALLBACK for playbook tokens, and UNCLASSIFIED GENERATOR for everything else.
 * The raw value remains visible so the classification is auditable.
 *
 * Owner: Stream D.
 */

import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, CircleHelp, Cpu, Workflow } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { clsx } from 'clsx';

import { api } from '@/api/client';
import type {
  ActionRecord,
  IncidentDetail,
  PlanSummary,
  PlanTaskRow,
  TimelineResponse,
} from '@/api/types';
import { EmptyState, MonoValue, Panel, StateBadge, WhyPopover } from '@/components/ui/primitives';
import { actionDerivation } from '@/components/ui/derivation';
import { CountBar } from '@/components/ui/Metric';
import {
  Absent,
  DefinitionList,
  DefinitionRow,
  Notice,
  rowSelectionClass,
} from '@/components/ui/composition';
import { utcStamp } from '@/components/ui/format';
import { refusalFor } from './refusal';
import {
  candidateMismatchLabel,
  planAuthorship,
  plannerCandidateAttribution,
  plannerUnavailableAttribution,
} from './planAttribution';

/**
 * Who wrote the plan of record, and — the part that matters for a demo — whether a model wrote a
 * plan that is NOT the one being run.
 *
 * The server persists the deterministic playbook first and never auto-selects the planner agent's
 * output, so "a model was called and succeeded" and "a model planned this recovery" are routinely
 * different facts. Stating only the first is how a reasonable viewer concludes the second.
 */
function GeneratorChip({ plan }: { plan: PlanSummary }) {
  const generatorKind = planAuthorship(plan);
  const isDeterministic = generatorKind === 'deterministic_fallback';
  const isModelAuthored = generatorKind === 'model_authored';
  const modelProvider = isModelAuthored ? 'recorded planner' : null;
  const Icon = isDeterministic ? Workflow : isModelAuthored ? Cpu : CircleHelp;
  // `selected` means a person chose this plan. `candidate` means it is the plan of record only
  // because it is the earliest one — a default, not a decision, and the two should not read alike.
  const chosenByPerson = plan.selection_state === 'selected';

  return (
    <div className="flex flex-col items-start gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={clsx(
            'inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5 text-label uppercase',
            isModelAuthored
              ? 'border-accent-border bg-accent-subtle text-accent'
              : 'border-border-strong bg-inset text-fg-secondary',
          )}
        >
          <Icon size={12} strokeWidth={1.5} aria-hidden />
          {isDeterministic
            ? 'Deterministic fallback'
            : isModelAuthored
              ? `Model-authored${modelProvider ? ` · ${modelProvider}` : ''}`
              : 'Unclassified generator'}
        </span>
        <StateBadge status="recorded" label="recorded plan of record" />
        <span className="text-caption text-fg-muted">
          recorded generator <MonoValue muted>{plan.generator}</MonoValue>
        </span>
        {isDeterministic && (
          <span className="text-caption text-fg-muted">
            Fallback playbook · deterministic · no model produced this plan
          </span>
        )}
        {generatorKind === 'unclassified' && (
          <span className="text-caption text-fg-muted">
            authorship is not inferred from an unknown token
          </span>
        )}
      </div>

      {plan.model_candidate_available === true && !isModelAuthored && (
        <Notice tone="info">
          A model-authored plan exists for this incident and is not the plan of record. The
          deterministic playbook is what will execute. Reasoning being live does not make a model
          plan authoritative: a person selects a candidate, or the playbook stands.
        </Notice>
      )}
      {plan.selection_state !== undefined && (
        <span className="text-caption text-fg-muted">
          {chosenByPerson
            ? 'Selected by a person, and recorded with an attribution.'
            : 'Plan of record by default — the earliest plan on this incident. Nobody has selected between candidates.'}
        </span>
      )}

      <details className="w-full rounded-sm border border-border-subtle bg-inset px-2 py-1.5">
        <summary className="cursor-pointer text-caption uppercase text-fg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
          Technical attribution
        </summary>
        <DefinitionList width="sm" className="mt-2 border-t border-border-subtle pt-2">
          <DefinitionRow label="generator" width="sm">
            <MonoValue muted>{plan.generator}</MonoValue>
          </DefinitionRow>
          <DefinitionRow label="prompt" width="sm">
            {plan.prompt_version ? (
              <MonoValue muted>{plan.prompt_version}</MonoValue>
            ) : (
              <Absent label="not recorded" title="This plan records no prompt version." />
            )}
          </DefinitionRow>
          <DefinitionRow label="model self-report" width="sm">
            {plan.model_self_report === null ? (
              <Absent label="not recorded" title="This plan records no model self-report." />
            ) : (
              <span className="text-caption text-fg-muted">
                <MonoValue muted>{plan.model_self_report}</MonoValue> · diagnostic only, never gates
                execution
              </span>
            )}
          </DefinitionRow>
        </DefinitionList>
      </details>
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
  timeline,
  timelineUnavailable,
  selectedTaskId,
  onSelectTask,
}: {
  incident: IncidentDetail;
  timeline?: TimelineResponse;
  timelineUnavailable?: boolean;
  selectedTaskId: number | null;
  onSelectTask: (taskId: number) => void;
}) {
  const { plan, actions } = incident;
  const systemMode = useQuery({ queryKey: ['system-mode'], queryFn: api.systemMode });
  const plannerCandidate = plannerCandidateAttribution(timeline, plan, systemMode.data?.llm_mode);
  const plannerUnavailable = plannerUnavailableAttribution(timeline);
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
      {plannerCandidate && (
        <Notice
          tone={
            !plannerCandidate.sourceVerified
              ? 'warn'
              : plannerCandidate.isPlanOfRecord
                ? 'default'
                : 'muted'
          }
        >
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <StateBadge
              status={plannerCandidate.isPlanOfRecord ? 'executing' : 'proposed'}
              label={
                plannerCandidate.isPlanOfRecord ? 'planner plan of record' : 'planner candidate'
              }
            />
            <span>
              <MonoValue>{plannerCandidate.generator}</MonoValue> · {plannerCandidate.sourceLabel} ·{' '}
              {plannerCandidate.isPlanOfRecord
                ? `candidate id ${plannerCandidate.planId} matches the recorded plan-of-record id`
                : `candidate id ${plannerCandidate.planId} does not match plan-of-record id ${plan.id}; ${candidateMismatchLabel(plan)}`}
            </span>
            <Link
              to={`/plans/${incident.reference}`}
              className="text-accent underline decoration-dotted underline-offset-2"
            >
              compare candidates
            </Link>
          </span>
        </Notice>
      )}
      {plannerUnavailable && (
        <Notice tone="warn">
          <span className="font-medium">Recorded planner unavailable:</span>{' '}
          <span>{plannerUnavailable.reason}</span>
          <span className="ml-2 text-caption">
            timeline event{' '}
            <MonoValue className="text-state-warn">{plannerUnavailable.eventId}</MonoValue> at{' '}
            <MonoValue className="text-state-warn">
              {utcStamp(plannerUnavailable.occurredAt) ?? plannerUnavailable.occurredAt}
            </MonoValue>
          </span>
        </Notice>
      )}
      {timelineUnavailable && (
        <Notice tone="warn">
          Planner attribution could not be read from the incident timeline. The plan of record below
          is still shown exactly as the incident endpoint returned it.
        </Notice>
      )}
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

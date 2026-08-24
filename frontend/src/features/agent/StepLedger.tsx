/**
 * The step ledger — the spine of the console.
 *
 * One row per planned task, reading left to right as the three questions an operator actually has:
 * **what does it intend, who authorised it, what happened.** The plan is the spine, so a task that
 * has not been evaluated or executed still occupies a row; that is how "nothing has run yet" is
 * told apart from "nothing was planned".
 *
 * The authorisation column is the reason this screen exists. `execute` means the gate permits it
 * unattended, `execute_flagged` means it runs but is recorded as notable, `needs_human` means it
 * stops. Those are the server's words, rendered with the shared state palette so they match every
 * other surface in the product.
 *
 * Owner: Stream D.
 */

import { clsx } from 'clsx';

import { Metric } from '@/components/ui/Metric';
import { agentStepDerivation, countDerivation } from '@/components/ui/derivation';
import { EmptyState, MonoValue, Panel, StateBadge } from '@/components/ui/primitives';
import { useKeyboardList } from '@/hooks/useKeyboardList';
import { ActorChip } from './ActorChip';
import type { AgentStep, StepOutcome } from './steps';

/**
 * Outcome to the shared status vocabulary.
 *
 * `refused` maps to `blocked` because nothing ran and nothing may be claimed — the same reading
 * `provenance_kind: unavailable` already gets elsewhere. It is deliberately not `needs_human`:
 * an operator cannot resolve a refusal by approving it.
 */
const OUTCOME_STATUS: Record<StepOutcome, { status: string; label: string }> = {
  not_started: { status: 'pending', label: 'not started' },
  succeeded: { status: 'succeeded', label: 'succeeded' },
  failed: { status: 'failed', label: 'failed' },
  skipped: { status: 'skipped', label: 'skipped' },
  refused: { status: 'blocked', label: 'refused' },
  awaiting_human: { status: 'needs_human', label: 'awaiting a person' },
};

export function StepLedger({
  steps,
  incidentReference,
  configVersion,
  configHash,
  selectedTaskId,
  onSelect,
}: {
  steps: AgentStep[];
  incidentReference: string;
  configVersion: string;
  configHash: string;
  selectedTaskId: number | null;
  onSelect: (taskId: number) => void;
}) {
  const keyboard = useKeyboardList({
    count: steps.length,
    onOpen: (index) => {
      const step = steps[index];
      if (step) onSelect(step.taskId);
    },
  });

  return (
    <Panel
      title="Step ledger"
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
      actions={
        <span className="flex items-center gap-3 text-caption text-fg-muted">
          <span>intent · authorisation · outcome</span>
          <Metric
            value={steps.length}
            derivation={countDerivation('Planned steps', steps.length, {
              endpoint: 'GET /incidents/{ref}',
              field: 'plan.tasks[]',
              note: 'The plan of record. Every task keeps a row whether or not it has been evaluated or executed.',
            })}
          />
        </span>
      }
    >
      {steps.length === 0 ? (
        <EmptyState
          title="No plan has been proposed"
          description="The orchestrator has not produced a plan for this incident, so there are no steps to authorise or execute. Run the incident to planning to populate this ledger."
        />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <table className="w-full border-collapse text-body">
            <caption className="sr-only">
              Planned agent steps, one row per task, with the gate decision and the recorded outcome
            </caption>
            <thead>
              <tr className="border-b border-border-subtle bg-inset text-label uppercase text-fg-muted">
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Step
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Authorisation
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Blocking
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Outcome
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Ran as
                </th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">
                  Cost INR
                </th>
              </tr>
            </thead>
            <tbody
              ref={keyboard.containerRef as React.RefObject<HTMLTableSectionElement>}
              onKeyDown={keyboard.onKeyDown}
            >
              {steps.map((step, index) => {
                const selected = step.taskId === selectedTaskId;
                const outcome = OUTCOME_STATUS[step.outcome];
                return (
                  <tr
                    key={step.taskId}
                    aria-current={selected || undefined}
                    className={clsx(
                      'h-row border-b border-l-2 border-border-subtle transition-colors duration-hover ease-out',
                      selected
                        ? 'border-l-accent bg-raised'
                        : 'border-l-transparent hover:bg-raised',
                    )}
                  >
                    <th scope="row" className="px-3 text-left font-normal">
                      <button
                        type="button"
                        {...keyboard.itemProps(index)}
                        onClick={() => onSelect(step.taskId)}
                        className="flex items-center gap-2 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                      >
                        <MonoValue muted className="w-4 shrink-0">
                          {step.taskOrder}
                        </MonoValue>
                        <span className="text-body text-fg">{step.actionType}</span>
                      </button>
                    </th>
                    <td className="px-3">
                      <span className="flex items-center gap-1.5">
                        {step.decision ? (
                          <StateBadge status={step.decision} />
                        ) : (
                          <span
                            className="text-caption text-fg-muted"
                            title="No evaluation names this task. Not evaluated is not the same as permitted."
                          >
                            not evaluated
                          </span>
                        )}
                        {step.riskTier && (
                          <StateBadge status={`tier_${step.riskTier}`} label={step.riskTier} />
                        )}
                      </span>
                    </td>
                    <td className="px-3">
                      {step.blocking.length === 0 ? (
                        <span className="text-caption text-fg-muted">none</span>
                      ) : (
                        <span className="flex flex-wrap gap-1">
                          {step.blocking.map((name) => (
                            <span key={name} className="text-caption text-state-warn">
                              {name.replace(/_/g, ' ')}
                            </span>
                          ))}
                        </span>
                      )}
                    </td>
                    <td className="px-3">
                      <StateBadge status={outcome.status} label={outcome.label} />
                    </td>
                    <td className="px-3">
                      {step.action ? (
                        <span className="flex flex-wrap items-center gap-1.5">
                          {/*
                           * The raw actor, as recorded. `ActionSummary` carries the actor name but
                           * not its kind — only the timeline and replay contracts publish
                           * `actor_kind` — so no kind is asserted here.
                           */}
                          <MonoValue muted>{step.action.actor}</MonoValue>
                          {/*
                           * Human involvement, from a field rather than a guess: a decision record
                           * is attached only when a person actually signed for this step.
                           */}
                          {step.evaluation?.human_decision && (
                            <ActorChip
                              actorKind="human"
                              actor={step.evaluation.human_decision.actor_id}
                            />
                          )}
                        </span>
                      ) : (
                        <span className="text-caption text-fg-muted">not invoked</span>
                      )}
                    </td>
                    <td className="px-3 text-right">
                      <Metric
                        value={step.action?.cost_inr ?? null}
                        derivation={agentStepDerivation({
                          actionType: step.actionType,
                          taskId: step.taskId,
                          taskOrder: step.taskOrder,
                          incidentReference,
                          taskState: step.taskState,
                          decision: step.decision,
                          riskTier: step.riskTier,
                          evaluationId: step.evaluation?.id ?? null,
                          actionId: step.action?.id ?? null,
                          actionStatus: step.action?.status ?? null,
                          configVersion,
                          configHash,
                          evaluatedAt: step.evaluation?.evaluated_at ?? null,
                          executedAt: step.action?.executed_at ?? null,
                        })}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

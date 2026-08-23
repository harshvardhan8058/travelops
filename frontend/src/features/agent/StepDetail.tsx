/**
 * One step, in full: what authorised it, what it was pointed at, what it returned.
 *
 * This is where the console earns the word "auditable". The gate's six checks are always all
 * rendered in the contract's fixed order, each with the machine reason code beside the prose,
 * because a check that silently disappears is indistinguishable from one that passed.
 *
 * The recorded output is reflected structurally rather than interpreted. `ActionDetailResponse.payload`
 * is documented as service-shaped and version-gated, so this shows the keys, their kinds, and a
 * primitive's value or a collection's size — and states the schema version it read them under. The
 * per-entity meaning of those payloads belongs to the Impact Explorer, which owns that reading.
 *
 * What is deliberately NOT here: any model-authored narration. No endpoint returns one, and the
 * summaries that do exist — the action's own `reason`, the check reasons, the plan rationale — are
 * recorded decisions rather than a private train of thought.
 *
 * Owner: Stream D.
 */

import { useQuery } from '@tanstack/react-query';
import { AlertTriangle } from 'lucide-react';

import { api, ApiError } from '@/api/client';
import { CHECK_ORDER } from '@/api/types';
import { Metric } from '@/components/ui/Metric';
import { toolOutputDerivation } from '@/components/ui/derivation';
import {
  CheckStateBadge,
  EmptyState,
  ErrorState,
  LoadingState,
  MonoValue,
  Panel,
  StateBadge,
} from '@/components/ui/primitives';
import { ActorChip } from './ActorChip';
import { NotPublished } from './NotPublished';
import { describePayload, type AgentStep } from './steps';

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-t border-border-subtle px-3 py-2">
      <h3 className="mb-1.5 text-label uppercase text-fg-muted">{title}</h3>
      {children}
    </div>
  );
}

export function StepDetail({
  step,
  incidentReference,
}: {
  step: AgentStep | null;
  incidentReference: string;
}) {
  const actionId = step?.action?.id ?? null;

  /**
   * Enabled only when an action exists. Requesting the detail of an action that has not happened
   * would be a guaranteed 404, and a console that logs an error for a normal early state is a
   * console an operator learns to distrust.
   */
  const detail = useQuery({
    queryKey: ['action-detail', incidentReference, actionId],
    queryFn: () => api.actionDetail(incidentReference, actionId as number),
    enabled: actionId !== null,
  });

  if (!step) {
    return (
      <Panel title="Step detail" className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <EmptyState
          title="No step selected"
          description="Choose a step in the ledger to see the checks that authorised it, the entities it names and the output it recorded."
        />
      </Panel>
    );
  }

  const checks = step.evaluation?.checks ?? [];
  const ordered = CHECK_ORDER.map((name) => checks.find((check) => check.name === name)).filter(
    (check): check is NonNullable<typeof check> => Boolean(check),
  );
  const humanDecision = step.evaluation?.human_decision ?? null;

  return (
    <Panel
      title="Step detail"
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
      actions={
        <span className="flex items-center gap-2 text-caption text-fg-muted">
          <MonoValue muted>step {step.taskOrder}</MonoValue>
          <MonoValue muted>{step.actionType}</MonoValue>
        </span>
      }
    >
      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* The recorded decision, in the words the service or gate wrote. Never paraphrased. */}
        <div className="px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            {step.decision ? (
              <StateBadge status={step.decision} />
            ) : (
              <span className="text-caption text-fg-muted">not evaluated</span>
            )}
            {step.riskTier && <StateBadge status={`tier_${step.riskTier}`} label={step.riskTier} />}
            {step.evaluation?.warn_permitted_by_config === true && (
              <span className="text-caption text-fg-secondary">warn permitted by config</span>
            )}
          </div>
          {step.action?.reason && (
            <p className="mt-1.5 text-body text-fg-secondary">{step.action.reason}</p>
          )}
        </div>

        {step.refusal && (
          <div className="border-t border-state-warn/30 bg-state-warn-bg px-3 py-2">
            <div className="flex items-center gap-2">
              <AlertTriangle
                size={12}
                strokeWidth={1.5}
                className="shrink-0 text-state-warn"
                aria-hidden
              />
              <span className="text-caption uppercase text-state-warn">{step.refusal.code}</span>
            </div>
            <p className="mt-1 text-body text-fg-secondary">{step.refusal.headline}</p>
            <p className="mt-1 text-caption text-fg-muted">{step.refusal.detail}</p>
          </div>
        )}

        <Section title={`Gate checks (${ordered.length})`}>
          {ordered.length === 0 ? (
            <p className="text-caption text-fg-muted">
              No evaluation names this task, so no check has been recorded against it.
            </p>
          ) : (
            <table className="w-full border-collapse text-body">
              <caption className="sr-only">
                The gate checks recorded for this step, in the contract&apos;s fixed order
              </caption>
              <tbody>
                {ordered.map((check) => (
                  <tr key={check.name} className="border-b border-border-subtle last:border-b-0">
                    <th scope="row" className="py-1 pr-2 text-left align-top font-normal">
                      <span className="text-caption text-fg-secondary">
                        {check.name.replace(/_/g, ' ')}
                      </span>
                    </th>
                    <td className="py-1 pr-2 align-top">
                      <CheckStateBadge state={check.state} />
                    </td>
                    <td className="py-1 align-top">
                      {/* The machine token first: it is what a replay compares. */}
                      <MonoValue muted className="text-caption break-all">
                        {check.reason_code}
                      </MonoValue>
                      {check.reason && (
                        <span className="block text-caption text-fg-muted">{check.reason}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {step.blocking.length > 0 && (
            <p className="mt-1.5 text-caption text-state-warn">
              Blocking: {step.blocking.map((name) => name.replace(/_/g, ' ')).join(', ')}
            </p>
          )}
        </Section>

        {humanDecision && (
          <Section title="Authorised by a person">
            <div className="flex flex-col gap-1">
              <div className="flex flex-wrap items-center gap-2">
                <ActorChip actorKind="human" actor={humanDecision.actor_id} />
                <StateBadge status={humanDecision.decision} />
              </div>
              <p className="text-body text-fg-secondary">{humanDecision.reason}</p>
              <MonoValue muted className="text-caption">
                {humanDecision.decided_at}
              </MonoValue>
            </div>
          </Section>
        )}

        <Section title="Entities named">
          {step.targetRefs.length === 0 ? (
            <p className="text-caption text-fg-muted">
              The candidate-plan contract records no target reference for this task.
            </p>
          ) : (
            <ul className="flex flex-wrap gap-1">
              {step.targetRefs.map((ref) => (
                <li key={ref}>
                  <MonoValue muted className="text-caption break-all">
                    {ref}
                  </MonoValue>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-1.5">
            <NotPublished
              items={[
                {
                  capability: 'call arguments',
                  wouldCarry: 'plan_task.inputs',
                  reason:
                    'The backend persists the arguments a task was built with, but no response model exposes them, so the references above are the whole of what is published.',
                },
              ]}
            />
          </div>
        </Section>

        <Section title="Recorded output">
          {actionId === null ? (
            <p className="text-caption text-fg-muted">
              No tool has been invoked for this step, so there is no output to report.
            </p>
          ) : detail.isLoading ? (
            <LoadingState label="Loading the recorded output" />
          ) : detail.isError ? (
            <ErrorState
              code={detail.error instanceof ApiError ? detail.error.code : 'UNAVAILABLE'}
              message={
                detail.error instanceof ApiError
                  ? detail.error.message
                  : 'The action detail endpoint did not respond.'
              }
              correlationId={detail.error instanceof ApiError ? detail.error.correlationId : null}
              onRetry={() => void detail.refetch()}
            />
          ) : detail.data ? (
            <PayloadTable
              payload={detail.data.payload}
              schemaVersion={detail.data.payload_schema_version}
              actionId={detail.data.id}
              incidentReference={incidentReference}
              provenanceKind={detail.data.provenance_kind}
              decisionScope={detail.data.decision_scope}
            />
          ) : null}
        </Section>
      </div>
    </Panel>
  );
}

function PayloadTable({
  payload,
  schemaVersion,
  actionId,
  incidentReference,
  provenanceKind,
  decisionScope,
}: {
  payload: Record<string, unknown>;
  schemaVersion: number;
  actionId: number;
  incidentReference: string;
  provenanceKind: string;
  decisionScope: string | null;
}) {
  const entries = describePayload(payload);

  return (
    <>
      <div className="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-fg-muted">
        <span>
          <span className="uppercase">schema</span>{' '}
          <MonoValue muted className="text-caption">
            v{schemaVersion}
          </MonoValue>
        </span>
        <span>
          <span className="uppercase">provenance</span>{' '}
          <MonoValue muted className="text-caption">
            {provenanceKind}
          </MonoValue>
        </span>
        {decisionScope && (
          <span>
            <span className="uppercase">scope</span>{' '}
            <MonoValue muted className="text-caption">
              {decisionScope}
            </MonoValue>
          </span>
        )}
      </div>
      {entries.length === 0 ? (
        <p className="text-caption text-fg-muted">The service recorded an empty payload.</p>
      ) : (
        <table className="w-full border-collapse text-body">
          <caption className="sr-only">
            Keys recorded in this action&apos;s payload, with the kind and size of each
          </caption>
          <tbody>
            {entries.map((entry) => (
              <tr key={entry.key} className="border-b border-border-subtle last:border-b-0">
                <th scope="row" className="py-1 pr-2 text-left align-top font-normal">
                  <MonoValue muted className="text-caption break-all">
                    {entry.key}
                  </MonoValue>
                </th>
                <td className="py-1 text-right align-top">
                  <Metric
                    value={entry.display}
                    derivation={toolOutputDerivation({
                      key: entry.key,
                      display: entry.display,
                      kind: entry.kind,
                      actionId,
                      incidentReference,
                      schemaVersion,
                    })}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="mt-1.5 text-caption text-fg-muted">
        Shape as the service recorded it. Per-entity findings are read on the Impact Explorer, which
        owns that interpretation.
      </p>
    </>
  );
}

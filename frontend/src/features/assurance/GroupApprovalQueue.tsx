/**
 * Group-scoped approval queue — the P2-D3 surface.
 *
 * The argument this screen has to make, out loud: **approval covers risk, never failed evidence, and
 * never high risk in bulk.** So the plan-level control lists exactly what it would cover, itemised
 * by task and tier, and lists what it excludes with the reason each item was excluded.
 *
 * The excluded set is the important half. A reviewer must be able to see that the control was
 * *unable* to cover something rather than assume it chose not to — "approve the routine seven, and
 * the cash payout still needs me by name" only lands if the eighth item is visible.
 *
 * Two things this screen never does:
 *
 *   - **No aggregate assurance score, at any level.** `docs/18` defines a fail-closed, ordered gate;
 *     a mean of six checks would be a fiction. And a group is not "assured" because most of its
 *     incidents are, so there is no group-level pass either.
 *   - **No client-side permission logic.** The server partitions. The UI renders the partition it
 *     was given. A UI that decided coverage would be the only thing preventing a bulk high-risk
 *     approval, which is not a control.
 *
 * Owner: Stream D.
 */

import { useState } from 'react';
import { clsx } from 'clsx';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api, ApiError } from '@/api/client';
import type { ExcludedEvaluation, GroupExposure, PlanCheck } from '@/api/types';
import { CountBar, Metric } from '@/components/ui/Metric';
import { planTotalDerivation } from '@/components/ui/derivation';
import {
  CheckStateBadge,
  EmptyState,
  ErrorState,
  LoadingState,
  MonoValue,
  Panel,
  StateBadge,
} from '@/components/ui/primitives';

/** Copy for the reasons the server can return. Never composed from prose. */
const REFUSAL_COPY: Record<string, string> = {
  HIGH_RISK_NEEDS_OWN_DECISION: 'High risk — needs its own approval',
  NOT_APPROVABLE_EVIDENCE: 'Blocked on evidence — cannot be approved',
  NOT_APPROVABLE_CONFLICT: 'Conflicts with another action — cannot be approved',
  TIER_NOT_COVERED: 'Risk tier outside this approval',
  PLAN_HASH_MISMATCH: 'The plan changed — this approval no longer covers it',
  TASK_NOT_IN_SCOPE: 'Not in the approval scope',
  NOTHING_TO_APPROVE: 'Nothing is waiting on a person',
};

function refusalLabel(code: string): string {
  return REFUSAL_COPY[code] ?? code.replace(/_/g, ' ').toLowerCase();
}

function ExcludedList({ items }: { items: ExcludedEvaluation[] }) {
  if (items.length === 0) {
    return <p className="px-3 py-2 text-body text-fg-muted">Nothing excluded.</p>;
  }
  const byReason = new Map<string, ExcludedEvaluation[]>();
  for (const item of items) {
    byReason.set(item.reason_code, [...(byReason.get(item.reason_code) ?? []), item]);
  }
  return (
    <div className="flex flex-col gap-3 px-3 py-2">
      {[...byReason.entries()].map(([code, group]) => (
        <div key={code} className="flex flex-col gap-1">
          <h4 className="text-label uppercase text-state-warn">
            {refusalLabel(code)} · {group.length}
          </h4>
          <ul className="flex flex-col gap-0.5">
            {group.map((item) => (
              <li key={item.evaluation_id} className="flex flex-wrap items-baseline gap-2">
                <MonoValue>{item.incident_reference}</MonoValue>
                <span className="text-body text-fg-primary">{item.action_type}</span>
                <span className="text-label uppercase text-fg-muted">{item.risk_tier}</span>
              </li>
            ))}
          </ul>
          <p className="text-body text-fg-secondary">{group[0]?.reason ?? ''}</p>
        </div>
      ))}
    </div>
  );
}

/**
 * The figures the exposure check measured, shown because it is the check most likely to block.
 *
 * An operator told "exposure within limits: FAIL" and not told what the exposure *was* cannot judge
 * whether to accept it, and this endpoint has always carried the numbers.
 *
 * A `null` renders as "not established", never as zero, and the title says what that means: the gate
 * treats an unknown figure as a breach. That distinction is the whole reason this row exists.
 * `rooms_committed` and `total_exposure_inr` were `null` on every single run, because a status filter
 * compared `Action.status` against a `TaskState` value and so matched nothing — the check reported a
 * breach on a group whose rooms and money were fully recorded in the ledger, and nothing on screen
 * could have revealed it.
 */
function ExposureRow({ exposure }: { exposure: GroupExposure }) {
  const figures: { label: string; value: number | null | undefined }[] = [
    { label: 'rooms committed', value: exposure.rooms_committed },
    { label: 'exposure INR', value: exposure.total_exposure_inr },
    { label: 'passengers', value: exposure.passengers_affected },
    { label: 'external effects', value: exposure.external_effects },
  ];
  const unresolved = exposure.unresolved_cohorts ?? [];

  return (
    <div className="border-t border-border-subtle px-3 py-1.5">
      <dl className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
        {figures.map((figure) => (
          <div key={figure.label} className="flex items-baseline gap-2">
            <dt className="text-label uppercase text-fg-muted">{figure.label}</dt>
            <dd>
              {figure.value === null || figure.value === undefined ? (
                <span
                  className="text-caption text-fg-muted"
                  title="Not established. The gate treats an unknown figure as a breach, not as zero."
                >
                  not established
                </span>
              ) : (
                <MonoValue>{figure.value.toLocaleString('en-IN')}</MonoValue>
              )}
            </dd>
          </div>
        ))}
      </dl>
      {unresolved.length > 0 && (
        <p className="mt-1 text-caption text-fg-muted">
          {unresolved.length} cohort{unresolved.length === 1 ? '' : 's'} unresolved, so exposure is
          not established: <MonoValue muted>{unresolved.join(', ')}</MonoValue>
        </p>
      )}
    </div>
  );
}

function ChecksRow({ checks }: { checks: PlanCheck[] }) {
  return (
    <table className="w-full border-collapse text-body">
      <caption className="sr-only">
        The six plan-level checks, in fixed order. Every check always renders.
      </caption>
      <thead>
        <tr className="border-b border-border-subtle">
          <th scope="col" className="px-3 py-1.5 text-left text-label uppercase text-fg-muted">
            Check
          </th>
          <th scope="col" className="px-3 py-1.5 text-left text-label uppercase text-fg-muted">
            State
          </th>
          <th scope="col" className="px-3 py-1.5 text-left text-label uppercase text-fg-muted">
            Reason
          </th>
        </tr>
      </thead>
      <tbody>
        {checks.map((check) => (
          <tr key={check.name} className="border-b border-border-subtle">
            <th scope="row" className="px-3 py-1.5 text-left font-normal text-fg-primary">
              {check.name.replace(/_/g, ' ')}
            </th>
            <td className="px-3 py-1.5">
              <CheckStateBadge state={check.state} />
            </td>
            <td className="px-3 py-1.5 text-fg-secondary">
              {check.reason ?? <span className="text-fg-muted">—</span>}
              {check.offending_refs.length > 0 && (
                <span className="ml-2 text-label text-fg-muted">
                  {check.offending_refs.slice(0, 4).join(', ')}
                </span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function GroupApprovalQueue() {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState('');
  const [failure, setFailure] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);

  const current = useQuery({ queryKey: ['current-group'], queryFn: () => api.currentGroup() });
  const groupRef = current.data?.reference ?? '';

  const assurance = useQuery({
    queryKey: ['group-assurance', groupRef],
    queryFn: () => api.groupAssurance(groupRef),
    enabled: Boolean(groupRef),
  });

  const approve = useMutation({
    mutationFn: (why: string) => api.approveGroupPlan(groupRef, why),
    onSuccess: (result) => {
      setFailure(null);
      setReason('');
      setOutcome(
        result.refusal
          ? `${refusalLabel(result.refusal)}. ${result.refusal_reason ?? ''}`.trim()
          : `Recorded ${result.covered_count} decision${result.covered_count === 1 ? '' : 's'}; ` +
              `${result.excluded_count} still need their own.`,
      );
      void queryClient.invalidateQueries({ queryKey: ['group-assurance', groupRef] });
    },
    onError: (error) => {
      setOutcome(null);
      setFailure(
        error instanceof ApiError
          ? `${error.code}: ${error.message}`
          : 'The approval was not recorded.',
      );
    },
  });

  if (current.isLoading || assurance.isLoading) {
    return <LoadingState label="Loading the approval queue" />;
  }
  if (current.isError) {
    return (
      <EmptyState
        title="No disruption group is open"
        description="Seed the demo dataset and inject the scenario, then this queue fills from the recorded evaluations."
      />
    );
  }
  if (assurance.isError) {
    const error = assurance.error;
    const resolution =
      error instanceof ApiError &&
      error.status === 404 &&
      error.code === 'ENTITY_NOT_FOUND' &&
      typeof error.details.resolution === 'string'
        ? error.details.resolution
        : null;

    if (resolution) {
      return <EmptyState title="Plan assurance is not available yet" description={resolution} />;
    }

    return (
      <ErrorState
        code={error instanceof ApiError ? error.code : 'UNAVAILABLE'}
        message={
          error instanceof ApiError
            ? error.message
            : 'The group assurance endpoint did not respond.'
        }
        correlationId={error instanceof ApiError ? error.correlationId : null}
      />
    );
  }

  const data = assurance.data;
  if (!data) return <LoadingState label="Loading the approval queue" />;

  const preview = data.approval_preview;
  /*
   * The only addition this console performs. It is confined to per-plan counts the same response
   * returned, every summand is listed in the derivation and in the table below, and the plans are
   * disjoint because each is scoped to one incident. Cascade figures are never treated this way:
   * those are derived server-side from recorded rows so a client cannot produce a second answer.
   */
  const taskContributions = data.incidents.map((incident) => ({
    incidentReference: incident.incident_reference,
    value: incident.task_count,
  }));
  const awaitingContributions = data.incidents.map((incident) => ({
    incidentReference: incident.incident_reference,
    value: incident.awaiting_approval_count,
  }));
  const totalTasks = taskContributions.reduce((sum, item) => sum + item.value, 0);
  const awaiting = awaitingContributions.reduce((sum, item) => sum + item.value, 0);
  const canApprove = api.canWrite && reason.trim().length > 0 && (preview?.covered_count ?? 0) > 0;

  return (
    <div className="flex flex-col gap-3">
      <Panel title={`Plan assurance · ${data.group_reference}`}>
        <div className="flex flex-wrap items-center gap-4 px-3 py-2">
          <span className="flex items-baseline gap-2">
            <span className="text-label uppercase text-fg-muted">Gate</span>
            <StateBadge status={data.requires_human ? 'awaiting_approval' : 'executing'} />
            <MonoValue>{data.decision.replace(/_/g, ' ')}</MonoValue>
          </span>
          <span className="flex items-baseline gap-2">
            <span className="text-label uppercase text-fg-muted">Plan risk tier</span>
            <MonoValue>{data.plan_risk_tier}</MonoValue>
          </span>
          <span className="flex items-baseline gap-2">
            <span className="text-label uppercase text-fg-muted">Tasks</span>
            <Metric
              value={totalTasks}
              derivation={planTotalDerivation({
                label: 'Tasks',
                field: 'task_count',
                contributions: taskContributions,
                configVersion: data.config_version,
                configHash: data.config_hash,
              })}
            />
          </span>
          <span className="flex items-baseline gap-2">
            {/* "Tasks", not "incidents": the shell's blocked badge counts incidents awaiting a
                person, and two differently-scoped figures under one word is how a reviewer
                concludes the console contradicts itself. */}
            <span className="text-label uppercase text-fg-muted">Tasks awaiting a person</span>
            <Metric
              value={awaiting}
              derivation={planTotalDerivation({
                label: 'Tasks awaiting a person',
                field: 'awaiting_approval_count',
                contributions: awaitingContributions,
                configVersion: data.config_version,
                configHash: data.config_hash,
              })}
            />
          </span>
        </div>

        {/* Always visible: a replay must be able to prove which semantics applied. */}
        <div className="flex flex-wrap items-center gap-3 border-t border-border-subtle px-3 py-1.5 text-label text-fg-muted">
          <span>
            <span className="uppercase">config</span> <MonoValue>{data.config_version}</MonoValue>
          </span>
          <span>
            <span className="uppercase">hash</span>{' '}
            <MonoValue>{data.config_hash.slice(0, 12)}</MonoValue>
          </span>
          <span>
            <span className="uppercase">plan</span>{' '}
            <MonoValue>{data.plan_hash.slice(0, 12)}</MonoValue>
          </span>
          {!data.config_hash_uniform && (
            <span className="text-state-warn">members judged under more than one config hash</span>
          )}
        </div>

        <p className="border-t border-border-subtle px-3 py-1.5 text-body text-fg-secondary">
          This summary authorises nothing. Every action still passes its own gate at execution time.
        </p>
      </Panel>

      <Panel title="The six plan checks">
        <ChecksRow checks={data.checks} />
        {data.blocking.length > 0 && (
          <p className="border-t border-border-subtle px-3 py-1.5 text-body text-state-warn">
            Blocking: {data.blocking.map((name) => name.replace(/_/g, ' ')).join(', ')}
          </p>
        )}
        <ExposureRow exposure={data.exposure} />
      </Panel>

      <Panel title="Per incident">
        <table className="w-full border-collapse text-body">
          <caption className="sr-only">
            Each member incident in the group, with its plan and how many tasks await a person.
          </caption>
          <thead>
            <tr className="border-b border-border-subtle">
              <th scope="col" className="px-3 py-1.5 text-left text-label uppercase text-fg-muted">
                Incident
              </th>
              <th scope="col" className="px-3 py-1.5 text-left text-label uppercase text-fg-muted">
                Variant
              </th>
              <th scope="col" className="px-3 py-1.5 text-left text-label uppercase text-fg-muted">
                Tasks
              </th>
              <th scope="col" className="px-3 py-1.5 text-left text-label uppercase text-fg-muted">
                Awaiting
              </th>
              <th scope="col" className="px-3 py-1.5 text-left text-label uppercase text-fg-muted">
                Decisions
              </th>
            </tr>
          </thead>
          <tbody>
            {data.incidents.map((incident) => {
              const segments = [
                { label: 'execute', tone: 'ok' as const, count: 0 },
                { label: 'execute flagged', tone: 'warn' as const, count: 0 },
                { label: 'needs human', tone: 'crit' as const, count: 0 },
              ];
              for (const task of incident.tasks) {
                const index =
                  task.decision === 'execute' ? 0 : task.decision === 'execute_flagged' ? 1 : 2;
                const segment = segments[index];
                if (segment) segment.count += 1;
              }
              return (
                <tr key={incident.incident_reference} className="border-b border-border-subtle">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">
                    <MonoValue>{incident.incident_reference}</MonoValue>
                  </th>
                  <td className="px-3 py-1.5 text-fg-secondary">{incident.variant_key ?? '—'}</td>
                  <td className="px-3 py-1.5">
                    <MonoValue>{incident.task_count}</MonoValue>
                  </td>
                  <td className="px-3 py-1.5">
                    <MonoValue
                      className={clsx(incident.awaiting_approval_count > 0 && 'text-state-warn')}
                    >
                      {incident.awaiting_approval_count}
                    </MonoValue>
                  </td>
                  <td className="px-3 py-1.5">
                    <CountBar segments={segments} total={incident.task_count} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Panel>

      <Panel title="Plan approval">
        <div className="flex flex-col gap-3 px-3 py-3">
          <p className="text-body text-fg-secondary">
            A plan approval covers <strong>low and medium risk</strong> actions only. High risk
            always requires its own action-level approval, and no approval ever covers a failed
            check — an operator may accept exposure, but may not assert a fact the evidence does not
            support.
          </p>

          <div className="grid gap-3 lg:grid-cols-2">
            <section aria-label="Would be covered">
              <h3 className="px-1 pb-1 text-label uppercase text-state-ok">
                Would be covered · {preview?.covered_count ?? 0}
              </h3>
              {preview && preview.covered.length > 0 ? (
                <ul className="flex flex-col gap-0.5 px-1">
                  {preview.covered.map((item) => (
                    <li key={item.evaluation_id} className="flex flex-wrap items-baseline gap-2">
                      <MonoValue>{item.incident_reference}</MonoValue>
                      <span className="text-fg-primary">{item.action_type}</span>
                      <span className="text-label uppercase text-fg-muted">{item.risk_tier}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="px-1 text-body text-fg-muted">
                  Nothing in this group can be covered by a single plan approval.
                </p>
              )}
            </section>

            <section aria-label="Cannot be covered">
              <h3 className="px-1 pb-1 text-label uppercase text-state-warn">
                Cannot be covered · {preview?.excluded_count ?? 0}
              </h3>
              <ExcludedList items={preview?.excluded ?? []} />
            </section>
          </div>

          {preview?.refusal && (
            <p className="text-body text-state-warn">
              {refusalLabel(preview.refusal)}
              {preview.refusal_reason ? ` — ${preview.refusal_reason}` : ''}
            </p>
          )}

          <label className="flex flex-col gap-1">
            <span className="text-label uppercase text-fg-muted">Reason (required)</span>
            <input
              type="text"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              maxLength={2000}
              aria-invalid={reason.trim().length === 0}
              className="rounded border border-border-subtle bg-inset px-2 py-1.5 text-body text-fg-primary"
              placeholder="Written to every decision this approval records"
            />
          </label>

          {failure && (
            <p role="alert" className="text-body text-state-crit">
              {failure}
            </p>
          )}
          {outcome && (
            <p role="status" className="text-body text-fg-primary">
              {outcome}
            </p>
          )}

          {/*
           * Absent rather than disabled when there is nothing to cover: a disabled control invites
           * the operator to work out why, and the excluded list above already says why.
           */}
          {(preview?.covered_count ?? 0) > 0 && (
            <button
              type="button"
              disabled={!canApprove || approve.isPending}
              onClick={() => approve.mutate(reason.trim())}
              className="self-start rounded border border-accent px-3 py-1.5 text-body text-accent disabled:border-border-subtle disabled:text-fg-muted"
            >
              {approve.isPending
                ? 'Recording…'
                : `Approve ${preview?.covered_count} low/medium action${
                    preview?.covered_count === 1 ? '' : 's'
                  }`}
            </button>
          )}

          {!api.canWrite && (
            <p className="text-body text-fg-muted">
              Fixture mode: write affordances are disabled, because a synthesised response would put
              a state change on screen that never happened.
            </p>
          )}
        </div>
      </Panel>
    </div>
  );
}

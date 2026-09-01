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
import { Link } from 'react-router-dom';

import { api, ApiError } from '@/api/client';
import { dataUnavailable, retryUnlessUnavailable } from '@/api/unavailable';
import type { CoveredEvaluation, ExcludedEvaluation, GroupExposure, PlanCheck } from '@/api/types';
import { CountBar, Metric } from '@/components/ui/Metric';
import { planTotalDerivation } from '@/components/ui/derivation';
import { GroupRunControl } from '@/features/cascade/GroupRunControl';
import { summariseGroupApproval, type GroupAuthorizationSummary } from './authorizationState';
import {
  CheckStateBadge,
  ErrorState,
  LoadingState,
  MonoValue,
  Panel,
  StateBadge,
} from '@/components/ui/primitives';
import {
  Absent,
  Button,
  DefinitionList,
  DefinitionRow,
  Labelled,
  Notice,
  NoDisruptionOpen,
  NotYetAvailable,
  PageHeader,
  PanelBody,
  ReasonField,
  SectionHeading,
  TableFrame,
  TableHead,
} from '@/components/ui/composition';

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

function IncidentLink({ reference, prefix }: { reference: string; prefix?: string }) {
  return (
    <Link
      to={`/incidents/${reference}`}
      className="rounded-sm underline decoration-dotted underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      {prefix}
      <MonoValue className="text-accent">{reference}</MonoValue>
    </Link>
  );
}

function CoveredList({ items }: { items: CoveredEvaluation[] }) {
  if (items.length === 0) {
    return (
      <p className="px-2.5 py-2 text-body text-fg-muted">
        Nothing in this group can be covered by one plan approval.
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-1.5 px-2.5 py-2">
      {items.map((item) => (
        <li key={item.evaluation_id} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <IncidentLink reference={item.incident_reference} />
          <span className="text-body text-fg">{item.action_type}</span>
          <span className="text-label uppercase text-fg-muted">{item.risk_tier} risk</span>
        </li>
      ))}
    </ul>
  );
}

function ExcludedList({ items }: { items: ExcludedEvaluation[] }) {
  if (items.length === 0) {
    return <p className="px-2.5 py-2 text-body text-fg-muted">Nothing remains separate.</p>;
  }
  const byReason = new Map<string, ExcludedEvaluation[]>();
  for (const item of items) {
    byReason.set(item.reason_code, [...(byReason.get(item.reason_code) ?? []), item]);
  }
  return (
    <div className="flex flex-col gap-2.5 px-2.5 py-2">
      {[...byReason.entries()].map(([code, group]) => (
        <section key={code} aria-label={refusalLabel(code)} className="flex flex-col gap-1">
          <h4 className="text-label uppercase text-state-warn">
            {refusalLabel(code)} · {group.length}
          </h4>
          <ul className="flex flex-col gap-1.5">
            {group.map((item) => (
              <li key={item.evaluation_id} className="min-w-0">
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                  <IncidentLink reference={item.incident_reference} />
                  <span className="text-body text-fg-primary">{item.action_type}</span>
                  <span className="text-label uppercase text-fg-muted">{item.risk_tier} risk</span>
                </div>
                <p className="text-caption text-fg-secondary">{item.reason}</p>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function ExcludedNextActions({ items }: { items: ExcludedEvaluation[] }) {
  const references = [...new Set(items.map((item) => item.incident_reference))];
  if (references.length === 0) return null;

  return (
    <Notice tone="warn" divider="none" className="rounded border">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span>Nothing is coverable here. Review each excluded incident and decide its action:</span>
        {references.map((reference) => (
          <IncidentLink key={reference} reference={reference} prefix="Review " />
        ))}
      </div>
    </Notice>
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
    <TableFrame caption="The six plan-level checks, in fixed order. Every check always renders.">
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
    </TableFrame>
  );
}

export function GroupApprovalQueue() {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState('');
  const [failure, setFailure] = useState<string | null>(null);
  /** A server refusal of the whole approval. Distinct from an approval that succeeded. */
  const [refusal, setRefusal] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<GroupAuthorizationSummary | null>(null);

  const current = useQuery({ queryKey: ['current-group'], queryFn: () => api.currentGroup() });
  const groupRef = current.data?.reference ?? '';

  const assurance = useQuery({
    queryKey: ['group-assurance', groupRef],
    queryFn: () => api.groupAssurance(groupRef),
    enabled: Boolean(groupRef),
    /*
     * A group with no member plan answers 404 with a resolution, and that is not transient: retrying
     * it is three more requests whose answer is already known, and it holds this screen on a spinner
     * before showing the empty state an operator could act on.
     */
    retry: retryUnlessUnavailable,
  });

  const approve = useMutation({
    mutationFn: (why: string) => api.approveGroupPlan(groupRef, why),
    onSuccess: (result) => {
      setFailure(null);
      setReason('');
      if (result.refusal) {
        // The server declined to record the approval at all. Not an outcome to celebrate.
        setRefusal(`${refusalLabel(result.refusal)}. ${result.refusal_reason ?? ''}`.trim());
        setOutcome(null);
      } else {
        setRefusal(null);
        setOutcome(summariseGroupApproval(result));
      }
      /*
       * The group record itself changes, not only its assurance: `awaiting_approval_count` and the
       * workflow badge in this page's own header are read from `['current-group']`, and the cascade
       * screen reads `['incident-group']`. Invalidating only the assurance key left this screen
       * showing the pre-approval counts it had just changed.
       */
      void queryClient.invalidateQueries({ queryKey: ['group-assurance', groupRef] });
      void queryClient.invalidateQueries({ queryKey: ['current-group'] });
      void queryClient.invalidateQueries({ queryKey: ['incident-group'] });
      void queryClient.invalidateQueries({ queryKey: ['incident-groups'] });
    },
    onError: (error) => {
      setOutcome(null);
      setRefusal(null);
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
  /*
   * A resolved query with no data is `/current` answering "none is started". The copy here used
   * to be hardcoded and used to instruct the operator to run a CLI seed — for a state the
   * Scenario Center resolves in the browser, on the screen this now links to.
   */
  if (!current.isError && current.isSuccess && !current.data) {
    return (
      <NoDisruptionOpen description="Nothing has been started against this dataset, so no plan has been evaluated and there is nothing to approve. Start a disruption from the Scenario Center." />
    );
  }
  if (current.isError) {
    const error = current.error;
    return (
      <ErrorState
        code={error instanceof ApiError ? error.code : 'UNAVAILABLE'}
        message={
          error instanceof ApiError
            ? error.message
            : 'The current-group endpoint did not respond, so the queue cannot say what is open.'
        }
        correlationId={error instanceof ApiError ? error.correlationId : null}
        onRetry={() => void current.refetch()}
      />
    );
  }
  if (assurance.isError) {
    const error = assurance.error;
    /*
     * Classified in one shared place rather than inline, so this screen and Plan Comparison cannot
     * drift apart about what a 404 means. The 404 is not hidden: the empty state carries the server's
     * own code, message and next step.
     */
    const unavailable = dataUnavailable(error);

    if (unavailable) {
      return (
        <NotYetAvailable title="Plan assurance is not available yet" unavailable={unavailable} />
      );
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
        onRetry={() => void assurance.refetch()}
      />
    );
  }

  const data = assurance.data;
  const group = current.data;
  if (!data || !group) return <LoadingState label="Loading the approval queue" />;

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
  const coveredCount = preview?.covered_count ?? 0;
  const excludedCount = preview?.excluded_count ?? 0;
  const canApprove = api.canWrite && reason.trim().length > 0 && coveredCount > 0;
  const unknownExposure = [
    data.exposure.rooms_committed,
    data.exposure.total_exposure_inr,
    data.exposure.passengers_affected,
    data.exposure.external_effects,
  ].some((value) => value === null || value === undefined);
  const unresolvedCohorts = data.exposure.unresolved_cohorts ?? [];
  const hashMismatch =
    (Boolean(preview?.plan_hash) && preview?.plan_hash !== data.plan_hash) ||
    Boolean(preview?.excluded.some((item) => item.reason_code === 'PLAN_HASH_MISMATCH'));

  return (
    <div className="flex flex-col gap-3">
      {/*
       * The disruption group is the subject, so the reference is the title — it was previously
       * interpolated into a 12px uppercase panel label, which is also what the browser gate reads to
       * prove this route rendered. It stays verbatim: `GRP-2026-0820-VOBL` is a contract value.
       *
       * The two figures move into tiles rather than sitting inline as `label value` pairs, because
       * "how many tasks" and "how many are waiting on me" are the numbers this screen exists to put
       * in front of a person. Both still carry their `planTotalDerivation`, so every summand remains
       * auditable through the popover.
       */}
      <PageHeader
        eyebrow="Group approval queue"
        title={<span className="font-mono tabular-nums">{data.group_reference}</span>}
        status={
          <StateBadge status={group.state} label={`workflow ${group.state.replace(/_/g, ' ')}`} />
        }
        meta={
          <>
            <Labelled label="gate">
              <MonoValue>{data.decision.replace(/_/g, ' ')}</MonoValue>
            </Labelled>
            <Labelled label="gate requirement">
              <MonoValue>
                {data.requires_human ? 'human decisions required' : 'no human gate'}
              </MonoValue>
            </Labelled>
            <Labelled label="incidents awaiting approval">
              <MonoValue>{group.awaiting_approval_count}</MonoValue>
            </Labelled>
            <Labelled label="plan risk tier">
              <MonoValue>{data.plan_risk_tier}</MonoValue>
            </Labelled>
          </>
        }
        footer={
          <p className="text-body text-fg-secondary">
            This summary authorises nothing. Every action still passes its own gate at execution
            time. “Human decisions required” describes the recorded plan rule; “incidents awaiting
            approval” and the workflow badge come from the current group record.
          </p>
        }
      />

      {/* Always visible: a replay must be able to prove which semantics applied. */}
      {!data.config_hash_uniform && (
        <Notice tone="warn" alert divider="none" className="rounded border">
          Members were judged under more than one config hash, so this record spans two sets of gate
          semantics.
        </Notice>
      )}

      {data.blocking.length > 0 && (
        <Notice tone="warn" alert divider="none" className="rounded border">
          Plan blockers: {data.blocking.map((name) => name.replace(/_/g, ' ')).join(', ')}. A
          signature can accept measured exposure, but it cannot override failed evidence or a
          conflict.
        </Notice>
      )}

      {hashMismatch && (
        <Notice tone="crit" alert divider="none" className="rounded border">
          Plan hash mismatch: this preview does not match the current plan record. Review the
          excluded incidents; this approval will not cover stale evaluations.
        </Notice>
      )}

      {(unknownExposure || unresolvedCohorts.length > 0) && (
        <Notice tone="warn" alert divider="none" className="rounded border">
          Exposure is not fully established. Unknown values are treated as a breach, never as zero
          {unresolvedCohorts.length > 0
            ? `; ${unresolvedCohorts.length} cohort${
                unresolvedCohorts.length === 1 ? ' is' : 's are'
              } unresolved.`
            : '.'}
        </Notice>
      )}

      <Panel title="Human decision">
        <PanelBody gap="loose">
          <div className="flex flex-wrap items-start justify-between gap-3 rounded border border-accent-border bg-accent-subtle px-3 py-2.5">
            <div className="min-w-0">
              <p className="text-label uppercase text-accent">Decision to make</p>
              <p className="mt-0.5 text-subtitle text-fg">
                {coveredCount > 0
                  ? `Approve ${coveredCount} low/medium risk action${coveredCount === 1 ? '' : 's'}`
                  : 'No actions can be approved as a group'}
              </p>
              <p className="mt-1 max-w-3xl text-body text-fg-secondary">
                The server partition below is the scope. One reason is written to every covered
                evaluation; excluded actions remain separate and must be reviewed on their own
                incident.
              </p>
            </div>
            <StateBadge
              status={coveredCount > 0 ? 'needs_human' : 'blocked'}
              label={`${coveredCount} covered · ${excludedCount} separate`}
            />
          </div>

          <section
            aria-labelledby="group-impact-heading"
            className="rounded border border-border-subtle"
          >
            <div className="border-b border-border-subtle px-3 py-1.5">
              <h3 id="group-impact-heading" className="text-label uppercase text-fg-secondary">
                Risk and measured impact
              </h3>
              <p className="mt-0.5 text-caption text-fg-muted">
                Plan tier <MonoValue>{data.plan_risk_tier}</MonoValue>. These are the recorded
                figures the exposure check judged; unknown values are not rendered as zero.
              </p>
            </div>
            <ExposureRow exposure={data.exposure} />
          </section>

          <p className="text-body text-fg-secondary">
            A plan approval covers <strong className="text-fg">low and medium risk</strong> actions
            only. High risk always requires its own action-level approval, and no approval ever
            covers failed evidence or a conflict. There is deliberately no group reject action
            because the server contract does not define one.
          </p>

          {/*
           * The two halves of the partition, side by side and visually separated.
           *
           * The excluded half is the important one, and it previously read as an afterthought: both
           * columns were a bare `h3` over a list with no boundary between them, so "seven covered"
           * and "one that still needs you by name" blended into a single column of text. Each half is
           * now a bordered well tinted to its own verdict, which is what makes the eighth item
           * visible — the entire argument this screen exists to make.
           */}
          <div className="grid gap-3 lg:grid-cols-2">
            <section
              aria-label="Would be covered"
              className="rounded border border-state-ok/30 bg-state-ok-bg"
            >
              <div className="border-b border-state-ok/30 px-2.5 py-1.5">
                <SectionHeading tone="ok" count={preview?.covered_count ?? 0}>
                  Would be covered
                </SectionHeading>
              </div>
              <CoveredList items={preview?.covered ?? []} />
            </section>

            <section
              aria-label="Cannot be covered"
              className="rounded border border-state-warn/30 bg-state-warn-bg"
            >
              <div className="border-b border-state-warn/30 px-2.5 py-1.5">
                <SectionHeading tone="warn" count={preview?.excluded_count ?? 0}>
                  Cannot be covered
                </SectionHeading>
              </div>
              <ExcludedList items={preview?.excluded ?? []} />
            </section>
          </div>

          {coveredCount === 0 && <ExcludedNextActions items={preview?.excluded ?? []} />}

          {preview?.refusal && (
            <Notice tone="warn" divider="none" className="rounded border">
              {refusalLabel(preview.refusal)}
              {preview.refusal_reason ? ` — ${preview.refusal_reason}` : ''}
            </Notice>
          )}

          {coveredCount > 0 && (
            <ReasonField
              id="group-approval-reason"
              value={reason}
              onChange={setReason}
              disabled={!api.canWrite}
              placeholder="Written to every decision this approval records"
              hint="Required. The same reason is recorded against every covered decision and cannot be edited afterwards."
            />
          )}

          {failure && (
            <Notice tone="crit" alert divider="none" className="rounded border">
              {failure}
            </Notice>
          )}
          {refusal && (
            <Notice tone="warn" alert divider="none" className="rounded border">
              {refusal}
            </Notice>
          )}
          {outcome && (
            /*
             * The handoff, stated at group scope.
             *
             * This used to read "Recorded 4 decisions; 2 still need their own" in an ok-toned band —
             * true, and it stopped one sentence short of the thing that matters: nothing had run.
             * `POST /incident-groups/{ref}/assurance/decision` writes one decision per covered
             * evaluation and dispatches nothing, so the honest state after a successful approval is
             * "authorized, not executed", and the next step is a control the operator must press.
             */
            <div
              className={clsx(
                'rounded-sm border px-2 py-1.5',
                outcome.awaitingExecution
                  ? 'border-state-warn/30 bg-state-warn-bg'
                  : 'border-border-subtle bg-inset',
              )}
              role="alert"
            >
              <span
                className={clsx(
                  'text-label uppercase',
                  outcome.awaitingExecution ? 'text-state-warn' : 'text-fg-secondary',
                )}
              >
                {outcome.headline}
              </span>
              <p
                className={clsx(
                  'mt-1 text-body',
                  outcome.awaitingExecution ? 'text-state-warn' : 'text-fg-secondary',
                )}
              >
                {outcome.detail}
              </p>
              {outcome.awaitingExecution && (
                <div className="mt-2">
                  {/*
                    The group's own execution control, reused rather than reimplemented. It is the
                    surface that advances a cascade, and it already disclaims that it approves
                    nothing — putting it here closes the gap between authorising the work and running
                    it, without creating a second way to execute.
                  */}
                  <GroupRunControl groupRef={groupRef} rollupStatus={null} />
                </div>
              )}
            </div>
          )}

          {/*
           * Absent rather than disabled when there is nothing to cover: a disabled control invites
           * the operator to work out why, and the excluded list above already says why.
           *
           * When there IS something to cover, the button states the count in its label so the
           * operator commits to a specific number of actions rather than to the word "approve".
           */}
          {coveredCount > 0 && (
            <Button
              variant="primary"
              size="md"
              className="self-start"
              disabled={!canApprove || approve.isPending}
              disabledReason={
                !api.canWrite
                  ? 'Fixtures are being served. Point the console at the live API to record an approval.'
                  : reason.trim().length === 0
                    ? 'Give a reason first: it is written to every decision this approval records.'
                    : undefined
              }
              onClick={() => approve.mutate(reason.trim())}
            >
              {approve.isPending
                ? 'Recording…'
                : `Approve ${coveredCount} low/medium action${coveredCount === 1 ? '' : 's'}`}
            </Button>
          )}
        </PanelBody>

        {!api.canWrite && (
          <Notice tone="muted">
            Fixture mode: write affordances are disabled, because a synthesised response would put a
            state change on screen that never happened.
          </Notice>
        )}
      </Panel>

      <details className="min-w-0 rounded border border-border-subtle bg-surface">
        <summary className="cursor-pointer px-3 py-2 text-label uppercase text-fg-secondary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
          Audit details · checks, incident counts and record identifiers
        </summary>
        <div className="min-w-0 border-t border-border-subtle">
          <section
            className="grid gap-3 px-3 py-2.5 md:grid-cols-2"
            aria-label="Record identifiers"
          >
            <DefinitionList width="lg">
              <DefinitionRow label="group" width="lg">
                <MonoValue>{data.group_reference}</MonoValue>
              </DefinitionRow>
              <DefinitionRow label="config version" width="lg">
                <MonoValue muted className="break-all">
                  {data.config_version}
                </MonoValue>
              </DefinitionRow>
              <DefinitionRow label="config hash" width="lg">
                <MonoValue muted className="break-all">
                  {data.config_hash}
                </MonoValue>
              </DefinitionRow>
              <DefinitionRow label="plan hash" width="lg">
                <MonoValue muted className="break-all">
                  {data.plan_hash}
                </MonoValue>
              </DefinitionRow>
            </DefinitionList>
            <DefinitionList width="lg">
              <DefinitionRow label="plan id" width="lg">
                {preview?.plan_id === null || preview?.plan_id === undefined ? (
                  <Absent label="not returned" />
                ) : (
                  <MonoValue>{preview.plan_id}</MonoValue>
                )}
              </DefinitionRow>
              <DefinitionRow label="preview hash" width="lg">
                {preview?.plan_hash ? (
                  <MonoValue muted className="break-all">
                    {preview.plan_hash}
                  </MonoValue>
                ) : (
                  <Absent label="not returned" />
                )}
              </DefinitionRow>
              <DefinitionRow label="evaluated" width="lg">
                <MonoValue muted>{data.evaluated_at}</MonoValue>
              </DefinitionRow>
              <DefinitionRow label="hash semantics" width="lg">
                <span className="text-caption text-fg-secondary">
                  {data.config_hash_uniform ? 'uniform across members' : 'mixed across members'}
                </span>
              </DefinitionRow>
            </DefinitionList>
          </section>

          <section className="border-t border-border-subtle" aria-labelledby="audit-checks-heading">
            <div className="px-3 py-2">
              <h3 id="audit-checks-heading" className="text-label uppercase text-fg-secondary">
                Six plan checks
              </h3>
            </div>
            <ChecksRow checks={data.checks} />
          </section>

          <section
            className="border-t border-border-subtle"
            aria-labelledby="audit-incidents-heading"
          >
            <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
              <h3 id="audit-incidents-heading" className="text-label uppercase text-fg-secondary">
                Per incident
              </h3>
              <div className="flex flex-wrap items-center gap-3 text-caption text-fg-muted">
                <span>
                  tasks{' '}
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
                <span>
                  human-gated{' '}
                  <Metric
                    value={awaiting}
                    derivation={planTotalDerivation({
                      label: 'Tasks whose gate requires a person',
                      field: 'awaiting_approval_count',
                      contributions: awaitingContributions,
                      configVersion: data.config_version,
                      configHash: data.config_hash,
                    })}
                  />
                </span>
              </div>
            </div>
            <TableFrame caption="Each member incident in the group, with its plan and how many task gates require a person. This is a plan property; the page header separately reports incidents whose workflows still await approval.">
              <TableHead
                columns={[
                  { key: 'incident', label: 'Incident' },
                  { key: 'variant', label: 'Variant' },
                  { key: 'tasks', label: 'Tasks', align: 'right' },
                  { key: 'awaiting', label: 'Human-gated', hint: 'tasks', align: 'right' },
                  { key: 'decisions', label: 'Decisions', hint: 'gate outcome per task' },
                ]}
              />
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
                        <IncidentLink reference={incident.incident_reference} />
                      </th>
                      <td className="px-3 py-1.5 text-fg-secondary">
                        {incident.variant_key ?? <Absent label="no variant" />}
                      </td>
                      <td className="px-3 py-1.5 text-right">
                        <MonoValue>{incident.task_count}</MonoValue>
                      </td>
                      <td className="px-3 py-1.5 text-right">
                        <MonoValue
                          className={clsx(
                            incident.awaiting_approval_count > 0 && 'text-state-warn',
                          )}
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
            </TableFrame>
          </section>

          <section
            className="border-t border-border-subtle px-3 py-2.5"
            aria-labelledby="audit-scope-heading"
          >
            <h3 id="audit-scope-heading" className="text-label uppercase text-fg-secondary">
              Approval scope record IDs
            </h3>
            <ul className="mt-1.5 grid gap-1 md:grid-cols-2">
              {[...(preview?.covered ?? []), ...(preview?.excluded ?? [])].map((item) => (
                <li key={item.evaluation_id} className="min-w-0 text-caption text-fg-secondary">
                  <IncidentLink reference={item.incident_reference} /> · {item.action_type} · task{' '}
                  <MonoValue muted>{item.plan_task_id}</MonoValue> · evaluation{' '}
                  <MonoValue muted>{item.evaluation_id}</MonoValue>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </details>
    </div>
  );
}

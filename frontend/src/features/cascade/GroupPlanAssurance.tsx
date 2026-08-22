/**
 * Plan-level assurance and plan approval, at group scope. P2-D1 and P2-D3.
 *
 * The hardest thing to get right on this screen is what the approve button means. It does **not**
 * authorise the plan's actions. It records that an operator accepted the plan's aggregate risk, and
 * that acceptance then covers the plan's low and medium risk tasks — nothing else. So the panel
 * states three things before the button is reachable:
 *
 * * `authorises no action`, taken from the payload rather than written here as reassurance;
 * * which tasks the approval would cover, by id and count;
 * * which tasks would **still** need their own decision, listed before the click rather than
 *   discovered after it.
 *
 * A plan the gate refuses is not approvable and the button is absent, not merely disabled: a
 * disabled control invites a hunt for the thing that would enable it, when the honest answer is that
 * a decision cannot cure failed evidence and the inputs have to change.
 *
 * Owner: Stream D.
 */

import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Lock, ShieldCheck, ShieldX } from 'lucide-react';
import { clsx } from 'clsx';

import { api, ApiError } from '@/api/client';
import type { PlanAssuranceRow } from '@/api/types';
import {
  EmptyState,
  ErrorState,
  LoadingState,
  MonoValue,
  Panel,
  StateBadge,
} from '@/components/ui/primitives';

const CHECK_TONE: Record<string, 'ok' | 'warn' | 'crit'> = {
  PASS: 'ok',
  WARN: 'warn',
  FAIL: 'crit',
};

export function GroupPlanAssurance({ groupId }: { groupId: string }) {
  const query = useQuery({
    queryKey: ['plan-assurance', groupId],
    queryFn: () => api.planAssurance(groupId),
  });
  const [expanded, setExpanded] = useState<number | null>(null);

  if (query.isLoading) {
    return (
      <Panel title="Plan assurance">
        <div className="h-40">
          <LoadingState label="Loading plan assurance" />
        </div>
      </Panel>
    );
  }

  if (query.error) {
    const error = query.error instanceof ApiError ? query.error : null;
    return (
      <ErrorState
        code={error?.code ?? 'INTERNAL_ERROR'}
        message={error?.message ?? 'Could not load plan assurance for this disruption.'}
        correlationId={error?.correlationId ?? null}
        onRetry={() => void query.refetch()}
      />
    );
  }

  const body = query.data;
  if (!body || body.plans.length === 0) {
    return (
      <Panel title="Plan assurance">
        <EmptyState
          title="No plans to judge"
          description={
            body?.note ??
            'Plan assurance evaluates a persisted plan, so this fills in once the disruption has been advanced.'
          }
        />
      </Panel>
    );
  }

  return (
    <Panel
      title="Plan assurance"
      actions={
        <span className="flex items-center gap-2 text-caption uppercase text-fg-muted">
          <span>
            config <MonoValue muted>{body.config_version}</MonoValue>
          </span>
          <MonoValue muted>{body.config_hash}</MonoValue>
        </span>
      }
    >
      <p className="border-b border-border-subtle px-3 py-2 text-caption text-fg-muted">
        {body.note}
      </p>
      <ul>
        {body.plans.map((plan) => (
          <PlanRow
            key={plan.plan_id ?? plan.plan_hash}
            groupId={groupId}
            plan={plan}
            expanded={expanded === plan.plan_id}
            onToggle={() => setExpanded(expanded === plan.plan_id ? null : plan.plan_id)}
          />
        ))}
      </ul>
    </Panel>
  );
}

function PlanRow({
  groupId,
  plan,
  expanded,
  onToggle,
}: {
  groupId: string;
  plan: PlanAssuranceRow;
  expanded: boolean;
  onToggle: () => void;
}) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState('');

  const approve = useMutation({
    mutationFn: () =>
      api.approvePlan(groupId, plan.plan_id as number, { reason, actor_id: 'operator-1' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['plan-assurance'] });
      void queryClient.invalidateQueries({ queryKey: ['incident-group'] });
      setReason('');
    },
  });
  const error = approve.error instanceof ApiError ? approve.error : null;

  const alreadyApproved = plan.approval !== null;
  // Absent, not disabled: a plan blocked on evidence cannot be cured by a decision, and a greyed
  // button invites hunting for the thing that would enable it.
  const approvable = !alreadyApproved && plan.plan_id !== null && plan.blocking.length <= 1;

  return (
    <li className="border-b border-border-subtle">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <MonoValue muted className="w-[132px] shrink-0 text-caption">
          {plan.incident_reference ?? `plan ${plan.plan_id}`}
        </MonoValue>
        <StateBadge status={plan.decision} />
        <span className="text-caption uppercase text-fg-muted">
          plan risk <MonoValue muted>{plan.plan_risk_tier}</MonoValue>
        </span>
        <span className="text-caption uppercase text-fg-muted">
          tasks <MonoValue muted>{plan.task_count}</MonoValue>
        </span>
        <span className="ml-auto flex items-center gap-2">
          {alreadyApproved ? (
            <span className="flex items-center gap-1 text-caption uppercase text-state-ok">
              <ShieldCheck size={12} strokeWidth={1.5} aria-hidden />
              approved
            </span>
          ) : plan.blocking.length > 0 ? (
            <span className="flex items-center gap-1 text-caption uppercase text-state-warn">
              <ShieldX size={12} strokeWidth={1.5} aria-hidden />
              {plan.blocking.length} blocking
            </span>
          ) : null}
          <MonoValue muted className="text-caption">
            {plan.plan_hash}
          </MonoValue>
        </span>
      </button>

      {expanded && (
        <div className="border-t border-border-subtle px-3 py-2">
          {/* Read from the payload, not asserted by this component. The boundary is the server's. */}
          <p className="mb-2 flex items-center gap-1.5 text-caption text-fg-muted">
            <Lock size={11} strokeWidth={1.5} aria-hidden />
            {plan.authorises_no_action
              ? 'This evaluation authorises no action. Every task still passes its own gate.'
              : 'Unexpected: this payload claims to authorise action.'}
          </p>

          <table className="mb-2 w-full border-collapse text-body">
            <thead>
              <tr className="border-y border-border-subtle bg-inset text-label uppercase text-fg-muted">
                <th scope="col" className="px-2 py-1 text-left font-medium">
                  Check
                </th>
                <th scope="col" className="px-2 py-1 text-left font-medium">
                  State
                </th>
                <th scope="col" className="px-2 py-1 text-left font-medium">
                  Reason
                </th>
              </tr>
            </thead>
            <tbody>
              {plan.checks.map((check) => (
                <tr key={check.name} className="border-b border-border-subtle">
                  <td className="px-2 py-1">
                    <MonoValue muted className="text-caption">
                      {check.name}
                    </MonoValue>
                  </td>
                  <td className="px-2 py-1">
                    <StateBadge
                      status={
                        CHECK_TONE[check.state] === 'ok'
                          ? 'resolved'
                          : CHECK_TONE[check.state] === 'warn'
                            ? 'awaiting_approval'
                            : 'blocked'
                      }
                      label={check.state}
                    />
                  </td>
                  <td className="px-2 py-1 text-caption text-fg-muted">
                    {check.reason ?? '—'}
                    {check.offending_refs.length > 0 && (
                      <span className="block">
                        <MonoValue muted className="text-caption">
                          {check.offending_refs.join(', ')}
                        </MonoValue>
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <dl className="mb-2 flex flex-wrap gap-x-4 gap-y-1">
            {Object.entries(plan.exposure).map(([key, value]) => (
              <div key={key} className="flex items-baseline gap-1.5">
                <dt className="text-caption uppercase text-fg-muted">{key.replace(/_/g, ' ')}</dt>
                <dd>
                  {/* `null` is shown as "unknown", never as zero. The gate treats an unknown
                   * exposure as a breach, and softening it here would hide why. */}
                  <MonoValue muted>
                    {value === null || value === undefined
                      ? 'unknown'
                      : Array.isArray(value)
                        ? value.length === 0
                          ? 'none'
                          : value.join(', ')
                        : String(value)}
                  </MonoValue>
                </dd>
              </div>
            ))}
          </dl>

          {plan.approval ? (
            <div className="rounded-sm border border-state-ok/30 bg-state-ok-bg px-2 py-1.5">
              <p className="text-caption text-fg-secondary">
                Approved by <MonoValue muted>{plan.approval.actor_id}</MonoValue> covering{' '}
                <MonoValue muted>{plan.approval.covered_task_ids.length}</MonoValue> task(s) at{' '}
                <MonoValue muted>{plan.approval.covers_tiers.join(', ')}</MonoValue> risk.
              </p>
              <p className="mt-0.5 text-caption text-fg-muted">{plan.approval.note}</p>
              {plan.approval.tasks_needing_own_decision.length > 0 && (
                <p className="mt-0.5 text-caption text-state-warn">
                  Still needing their own decision:{' '}
                  <MonoValue muted>{plan.approval.tasks_needing_own_decision.join(', ')}</MonoValue>
                </p>
              )}
            </div>
          ) : approvable ? (
            <div className="flex flex-col gap-1.5">
              {plan.tasks_needing_own_decision.length > 0 && (
                /* Before the click, not after. An approval that turns out to cover less than the
                 * operator assumed is the failure this line exists to prevent. */
                <p className="text-caption text-state-warn">
                  This approval would not cover{' '}
                  <MonoValue muted>{plan.tasks_needing_own_decision.join(', ')}</MonoValue> — high
                  risk always needs its own decision.
                </p>
              )}
              <label className="flex flex-col gap-1">
                <span className="text-caption uppercase text-fg-muted">Reason (required)</span>
                <input
                  type="text"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="Why the aggregate risk is accepted"
                  className="rounded-sm border border-border-subtle bg-inset px-2 py-1 text-body text-fg placeholder:text-fg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                />
              </label>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => approve.mutate()}
                  disabled={reason.trim().length === 0 || approve.isPending}
                  className={clsx(
                    'rounded-sm border border-accent-border bg-accent-subtle px-2 py-1 text-caption uppercase text-accent',
                    'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                    'disabled:cursor-not-allowed disabled:border-border-subtle disabled:bg-transparent disabled:text-fg-muted',
                  )}
                >
                  {approve.isPending ? 'Recording' : 'Approve plan risk'}
                </button>
                <span className="text-caption text-fg-muted">
                  Covers low and medium risk tasks only.
                </span>
              </div>
            </div>
          ) : (
            <p className="text-caption text-state-warn">
              This plan is not approvable: {plan.blocking.join(', ') || 'nothing awaits a human'}. A
              decision cannot cure failed evidence — the inputs have to change and the plan has to
              be evaluated again.
            </p>
          )}

          {error && (
            <div className="mt-1.5 flex items-center gap-2">
              <StateBadge status="failed" label={error.code} />
              <span className="text-caption text-state-crit">{error.message}</span>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

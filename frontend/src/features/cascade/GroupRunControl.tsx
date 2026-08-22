/**
 * Open and advance a disruption, and say whether its figures are finished.
 *
 * `POST /incident-groups/{ref}/open` and `/run` existed with nothing calling them, so the journey
 * could not be driven from the console at all — a reviewer had to reach for curl to see the cascade
 * populate.
 *
 * It approves nothing. Opening creates one incident per **declared** member flight and advancing
 * puts each through its own per-flight gate, so a single click never authorises eight flights' worth
 * of external effect. That is the architecture, and the button label says "advance", not "resolve".
 *
 * The second job matters as much as the first: `rollup_status.is_complete` decides whether the
 * numbers above it are totals or floors. Mid-recovery, partial is the normal state, and a console
 * that renders a partial rollup identically to a finished one is misreporting the disruption — so
 * the banner is always present and always says which it is.
 *
 * Owner: Stream D.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, CircleDashed, FolderOpen, Play } from 'lucide-react';
import { clsx } from 'clsx';

import { api, ApiError } from '@/api/client';
import type { GroupRollupStatus, GroupRunResponse } from '@/api/types';
import { MonoValue, Panel, StateBadge } from '@/components/ui/primitives';

export function GroupRunControl({
  groupRef,
  rollupStatus,
}: {
  groupRef: string;
  rollupStatus?: GroupRollupStatus | null;
}) {
  const queryClient = useQueryClient();

  // Every derived figure on the page comes from the group endpoints, so all of them are stale once
  // the run advances. Invalidated by key prefix rather than individually, to avoid a screen where
  // the graph has moved on and the rollups have not.
  const refresh = () => {
    for (const key of [
      ['incident-group'],
      ['incident-groups'],
      ['group-assurance'],
      ['group-replay'],
      ['flights'],
    ]) {
      void queryClient.invalidateQueries({ queryKey: key });
    }
  };

  const open = useMutation<GroupRunResponse, unknown, void>({
    mutationFn: () => api.openGroup(groupRef),
    onSuccess: refresh,
  });
  const advance = useMutation<GroupRunResponse, unknown, void>({
    mutationFn: () => api.runGroup(groupRef),
    onSuccess: refresh,
  });

  const pending = open.isPending || advance.isPending;
  const error =
    open.error instanceof ApiError
      ? open.error
      : advance.error instanceof ApiError
        ? advance.error
        : null;
  const result = advance.data ?? open.data;
  const complete = rollupStatus?.is_complete ?? false;

  return (
    <Panel>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2">
        <button
          type="button"
          onClick={() => open.mutate()}
          disabled={pending || !api.canWrite}
          className={clsx(
            'flex items-center gap-1.5 rounded-sm border px-2 py-1 text-caption uppercase',
            'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
            'border-border-strong text-fg-secondary hover:border-accent-border hover:text-accent',
            'disabled:cursor-not-allowed disabled:border-border-subtle disabled:text-fg-muted',
          )}
        >
          <FolderOpen size={12} strokeWidth={1.5} aria-hidden />
          {open.isPending ? 'Opening' : 'Open declared flights'}
        </button>

        <button
          type="button"
          onClick={() => advance.mutate()}
          disabled={pending || !api.canWrite}
          className={clsx(
            'flex items-center gap-1.5 rounded-sm border px-2 py-1 text-caption uppercase',
            'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
            'border-accent-border bg-accent-subtle text-accent',
            'disabled:cursor-not-allowed disabled:border-border-subtle disabled:bg-transparent disabled:text-fg-muted',
          )}
        >
          <Play size={12} strokeWidth={1.5} aria-hidden />
          {advance.isPending ? 'Advancing' : 'Advance disruption'}
        </button>

        <span className="flex items-center gap-1.5 text-caption">
          {complete ? (
            <CheckCircle2 size={12} strokeWidth={1.5} className="text-state-ok" aria-hidden />
          ) : (
            <CircleDashed size={12} strokeWidth={1.5} className="text-state-warn" aria-hidden />
          )}
          <span className={complete ? 'text-fg-secondary' : 'text-state-warn'}>
            {complete ? 'Figures are complete' : 'Figures are partial'}
          </span>
        </span>

        {rollupStatus?.flights_without_incident?.length ? (
          <span className="flex items-center gap-1.5 text-caption uppercase text-state-warn">
            {/* Named, not counted: which flights are unworked is the actionable part. */}
            no incident yet
            <MonoValue muted>{rollupStatus.flights_without_incident.join(', ')}</MonoValue>
          </span>
        ) : null}

        <span className="ml-auto text-caption uppercase text-fg-muted">
          approving nothing — each flight keeps its own gate
        </span>
      </div>

      {rollupStatus?.note && (
        /* The server's sentence, so the console cannot describe completeness differently from the
         * thing that computed it. */
        <p
          className={clsx(
            'border-t px-3 py-1.5 text-caption',
            complete
              ? 'border-border-subtle text-fg-muted'
              : 'border-state-warn/30 bg-state-warn-bg text-state-warn',
          )}
        >
          {rollupStatus.note}
        </p>
      )}

      {error && (
        <div className="border-t border-state-crit/30 bg-state-crit-bg px-3 py-2">
          <div className="flex items-center gap-2">
            <StateBadge status="failed" label={error.code} />
            <span className="text-caption text-state-crit">{error.message}</span>
          </div>
        </div>
      )}

      {result && (
        <div className="border-t border-border-subtle" aria-live="polite">
          <div className="flex flex-wrap items-center gap-2 px-3 py-1.5">
            <StateBadge status={result.state} />
            <span className="text-caption uppercase text-fg-muted">
              members <MonoValue muted>{result.members.length}</MonoValue>
            </span>
            {result.opened_incident_ids.length > 0 && (
              <span className="text-caption uppercase text-fg-muted">
                opened <MonoValue muted>{result.opened_incident_ids.length}</MonoValue>
              </span>
            )}
            <span className="text-caption uppercase text-fg-muted">
              awaiting a person <MonoValue muted>{result.awaiting_approval_count}</MonoValue>
            </span>
            {result.replayed && (
              <span className="text-caption uppercase text-fg-muted">
                replayed, nothing advanced
              </span>
            )}
          </div>
          {result.blocked_reason && (
            /* A group is `blocked` when a member did not resolve. The reason names which, because
             * "blocked" without a subject is not something an operator can act on. */
            <p className="border-t border-state-warn/30 bg-state-warn-bg px-3 py-1.5 text-caption text-state-warn">
              {result.blocked_reason}
            </p>
          )}
        </div>
      )}
    </Panel>
  );
}

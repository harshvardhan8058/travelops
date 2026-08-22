/**
 * Group run control and completeness banner.
 *
 * Two jobs, and the second matters more than the first.
 *
 * **Advance the disruption.** One button, driving `POST /incident-groups/{id}/run`, which opens the
 * declared incidents and advances each through its own per-flight gate. It approves nothing: the
 * group is the scope of a disruption, never a unit of authorisation, and a single click that
 * authorised eight flights' worth of external effects is precisely what this architecture refuses.
 * The result names how many member incidents are waiting for a person.
 *
 * **Say whether the figures are finished.** `rollup_status.is_complete` decides whether the numbers
 * above are a total or a floor. Mid-recovery, partial is the normal state, and a console that
 * renders a partial rollup identically to a final one is misreporting the disruption — so the banner
 * is always present and always states which it is.
 *
 * Owner: Stream D.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, CircleDashed, Play } from 'lucide-react';
import { clsx } from 'clsx';

import { api, ApiError } from '@/api/client';
import type { GroupRunResponse, RollupStatus } from '@/api/types';
import { MonoValue, Panel, StateBadge } from '@/components/ui/primitives';

export function GroupRunBar({
  groupId,
  rollupStatus,
}: {
  groupId: string;
  rollupStatus: RollupStatus;
}) {
  const queryClient = useQueryClient();
  const run = useMutation<GroupRunResponse, unknown, void>({
    mutationFn: () => api.runIncidentGroup(groupId),
    onSuccess: () => {
      // Every derived figure on the page comes from the group endpoints, so all of them are stale
      // once the run advances. Invalidating by prefix rather than by key avoids a screen where the
      // graph has moved on and the rollups have not.
      void queryClient.invalidateQueries({ queryKey: ['incident-group'] });
      void queryClient.invalidateQueries({ queryKey: ['incident-groups'] });
      void queryClient.invalidateQueries({ queryKey: ['group-replay'] });
      void queryClient.invalidateQueries({ queryKey: ['plan-assurance'] });
    },
  });

  const error = run.error instanceof ApiError ? run.error : null;
  const result = run.data;
  const complete = rollupStatus.is_complete;

  return (
    <Panel>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2">
        <button
          type="button"
          onClick={() => run.mutate()}
          disabled={run.isPending}
          className={clsx(
            'flex items-center gap-1.5 rounded-sm border px-2 py-1 text-caption uppercase',
            'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
            'border-accent-border bg-accent-subtle text-accent',
            'disabled:cursor-not-allowed disabled:border-border-subtle disabled:bg-transparent disabled:text-fg-muted',
          )}
        >
          <Play size={12} strokeWidth={1.5} aria-hidden />
          {run.isPending ? 'Advancing' : 'Advance disruption'}
        </button>

        <span className="flex items-center gap-1.5 text-caption text-fg-muted">
          {complete ? (
            <CheckCircle2 size={12} strokeWidth={1.5} className="text-state-ok" aria-hidden />
          ) : (
            <CircleDashed size={12} strokeWidth={1.5} className="text-state-warn" aria-hidden />
          )}
          <span className={complete ? 'text-fg-secondary' : 'text-state-warn'}>
            {complete ? 'Figures are complete' : 'Figures are partial'}
          </span>
        </span>

        <span className="flex items-center gap-1.5 text-caption uppercase text-fg-muted">
          incidents <MonoValue muted>{rollupStatus.incidents_in_group}</MonoValue>
        </span>
        <span className="flex items-center gap-1.5 text-caption uppercase text-fg-muted">
          connections assessed{' '}
          <MonoValue muted>
            {rollupStatus.incidents_assessed_connections}/{rollupStatus.incidents_in_group}
          </MonoValue>
        </span>
        <span className="flex items-center gap-1.5 text-caption uppercase text-fg-muted">
          crew assessed{' '}
          <MonoValue muted>
            {rollupStatus.incidents_assessed_crew}/{rollupStatus.incidents_in_group}
          </MonoValue>
        </span>
        {rollupStatus.membership_is_declared && (
          <span className="flex items-center gap-1.5 text-caption uppercase text-fg-muted">
            declared flights <MonoValue muted>{rollupStatus.member_flight_ids.length}</MonoValue>
          </span>
        )}
      </div>

      {/* Always rendered, complete or not. The sentence is the server's, so the console cannot
       * describe completeness differently from the thing that computed it. */}
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
          <p className="px-3 py-1.5 text-body text-fg">{result.note}</p>
          <div className="flex flex-wrap gap-2 px-3 pb-2">
            {Object.entries(result.states).map(([state, count]) => (
              <StateBadge
                key={state}
                status={state}
                label={`${count} ${state.replace(/_/g, ' ')}`}
              />
            ))}
            {result.snapshot_hash && (
              <span className="flex items-center gap-1.5 text-caption uppercase text-fg-muted">
                snapshot <MonoValue muted>{result.snapshot_hash}</MonoValue>
              </span>
            )}
            {result.replayed && (
              <span className="text-caption uppercase text-fg-muted">
                replayed, nothing advanced
              </span>
            )}
          </div>
        </div>
      )}
    </Panel>
  );
}

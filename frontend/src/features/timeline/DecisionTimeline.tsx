/**
 * Decision Timeline — the persistent right rail.
 *
 * A read over immutable records. Not a model narrating history: every entry names its
 * actor, and expanding one shows the evidence and config version behind it.
 *
 * Owner: Stream E.
 */

import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';

import { api, ApiError } from '@/api/client';
import type { TimelineEntry } from '@/api/types';
import { EmptyState, ErrorState, LoadingState, MonoValue } from '@/components/ui/primitives';

const ACTOR_CLASS: Record<TimelineEntry['actor_kind'], string> = {
  orchestrator: 'text-accent border-accent-border',
  agent: 'text-state-info border-state-info/30',
  service: 'text-fg-secondary border-border',
  human: 'text-state-warn border-state-warn/30',
  provider: 'text-fg-muted border-border-subtle',
};

function Entry({ entry }: { entry: TimelineEntry }) {
  const detail = entry.detail ? JSON.stringify(entry.detail, null, 2) : null;
  return (
    <li className="border-b border-border-subtle px-3 py-2">
      <div className="flex items-center gap-2">
        <MonoValue muted className="shrink-0">
          {new Date(entry.occurred_at).toISOString().slice(11, 19)}
        </MonoValue>
        <span
          className={clsx(
            'shrink-0 rounded-sm border px-1 py-0.5 text-caption uppercase',
            ACTOR_CLASS[entry.actor_kind],
          )}
        >
          {entry.actor_kind}
        </span>
      </div>

      <p className="mt-1 text-body text-fg">{entry.summary}</p>
      <MonoValue muted className="text-caption">
        {entry.event_type}
      </MonoValue>

      {detail && (
        <details className="mt-1">
          <summary className="cursor-pointer text-caption text-fg-muted hover:text-fg-secondary">
            evidence
          </summary>
          <pre className="mt-1 overflow-x-auto rounded-sm bg-inset p-2 font-mono text-caption text-fg-secondary">
            {detail}
          </pre>
        </details>
      )}
    </li>
  );
}

export function DecisionTimeline({ incidentId }: { incidentId: string }) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['timeline', incidentId],
    queryFn: () => api.timeline(incidentId),
    refetchInterval: 5_000,
  });

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border-subtle px-3 py-2">
        <h2 className="text-label uppercase text-fg-secondary">Decision timeline</h2>
        {data && <MonoValue muted>{data.entries.length}</MonoValue>}
      </header>

      {isLoading && <LoadingState label="Loading timeline" />}

      {error && (
        <ErrorState
          code={error instanceof ApiError ? error.code : 'INTERNAL_ERROR'}
          message="Timeline unavailable"
          correlationId={error instanceof ApiError ? error.correlationId : null}
          onRetry={() => void refetch()}
        />
      )}

      {data && data.entries.length === 0 && (
        <EmptyState
          title="No decisions yet"
          description="Inject the bengaluru_storm scenario to see the system work."
        />
      )}

      {data && data.entries.length > 0 && (
        <ol className="flex-1 overflow-auto">
          {[...data.entries].reverse().map((entry) => (
            <Entry key={entry.id} entry={entry} />
          ))}
        </ol>
      )}
    </div>
  );
}

/**
 * Decision Timeline — the persistent right rail.
 *
 * A read over immutable records. Not a model narrating history: every entry names its
 * actor, and expanding one shows the evidence and config version behind it.
 *
 * Owner: Stream E.
 */

import { useQuery } from '@tanstack/react-query';
import { User } from 'lucide-react';
import { clsx } from 'clsx';

import { api, ApiError } from '@/api/client';
import type { TimelineEntry } from '@/api/types';
import { EmptyState, ErrorState, LoadingState, MonoValue } from '@/components/ui/primitives';

/**
 * Actor chips. Identity, not status.
 *
 * `human` was `--state-warn`, which is the colour this product uses for SEVERITY HIGH, NEEDS
 * HUMAN, TIER HIGH and AWAITING APPROVAL. On the workspace all five appear at once, so an
 * operator's completed approval read as one more amber warning rather than as the thing a
 * person did. docs/21 reserves green, amber and red exclusively for operational state, so
 * identity has no business borrowing one.
 *
 * It is now the only chip drawn in primary text on a raised fill with a strong border: the
 * highest-contrast, most solid chip in the rail, which is appropriate for the one actor who
 * carries accountability, and impossible to mistake for a status.
 */
const ACTOR_CLASS: Record<TimelineEntry['actor_kind'], string> = {
  orchestrator: 'text-accent border-accent-border',
  agent: 'text-state-info border-state-info/30',
  service: 'text-fg-secondary border-border',
  human: 'text-fg bg-raised border-border-strong font-medium',
  provider: 'text-fg-muted border-border-subtle',
};

/**
 * Only the human chip carries an icon, deliberately.
 *
 * Every status badge on screen pairs a word with an icon, and before this the one entry
 * representing a person had the least furniture of anything in the rail. A single glyph among
 * twenty text-only chips is what makes an operator's action findable while scrolling a dense
 * log — which is the whole point of attributing it.
 */
const ACTOR_ICON: Partial<Record<TimelineEntry['actor_kind'], typeof User>> = {
  human: User,
};

function Entry({ entry }: { entry: TimelineEntry }) {
  const detail = entry.detail ? JSON.stringify(entry.detail, null, 2) : null;
  const ActorIcon = ACTOR_ICON[entry.actor_kind];
  return (
    <li className="border-b border-border-subtle px-3 py-2">
      <div className="flex items-center gap-2">
        <MonoValue muted className="shrink-0">
          {new Date(entry.occurred_at).toISOString().slice(11, 19)}
        </MonoValue>
        <span
          className={clsx(
            'inline-flex shrink-0 items-center gap-1 rounded-sm border px-1 py-0.5 text-caption uppercase',
            ACTOR_CLASS[entry.actor_kind],
          )}
        >
          {ActorIcon && <ActorIcon size={11} strokeWidth={1.5} aria-hidden />}
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

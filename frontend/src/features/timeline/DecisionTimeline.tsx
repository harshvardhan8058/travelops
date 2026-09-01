/**
 * Decision Timeline — the persistent right rail.
 *
 * A read over immutable records. Not a model narrating history: every entry names its
 * actor, and expanding one shows the evidence and config version behind it.
 *
 * Owner: Stream E.
 */

import { useQuery } from '@tanstack/react-query';

import { api, ApiError } from '@/api/client';
import type { TimelineEntry } from '@/api/types';
import { pollUnlessMissing, retryUnlessUnavailable } from '@/api/unavailable';
import { EmptyState, ErrorState, LoadingState, MonoValue } from '@/components/ui/primitives';
import { TimelineItem, TimelineList } from '@/components/ui/composition';
import { utcClock } from '@/components/ui/format';
import { ActorChip } from '@/features/agent/ActorChip';

/*
 * The local `ACTOR_CLASS` and `ACTOR_ICON` maps are gone: attribution renders through `ActorChip`.
 *
 * The reasoning above is preserved by that component, not discarded — a human act is still the
 * highest-contrast, most solid chip in the rail, and it is still the only one carrying a glyph,
 * which is what makes an operator's action findable while scrolling a dense log.
 *
 * What changes is that there is now one implementation instead of three. This map, an identical one
 * in `ReplayScreen.tsx` and `ActorChip.tsx` itself all carried the same `human` class string, and
 * two of the three painted `agent` with `text-state-info` — identity borrowing a colour from the
 * operational state ramp, which is the exact conflation this rail's own header warns about. Three
 * copies of the one rule the design docs care most about is how that rule quietly stops holding.
 */
function Entry({ entry, isLast }: { entry: TimelineEntry; isLast: boolean }) {
  const detail = entry.detail ? JSON.stringify(entry.detail, null, 2) : null;
  /*
   * A person's act is marked on the spine as well as in the chip. `accent` is an active-state cue
   * rather than a status colour, so it can carry "this one was a human" without being mistaken for
   * a warning — which is the whole reason identity is kept off the green/amber/red ramp.
   */
  const isHuman = entry.actor_kind === 'human';

  return (
    <TimelineItem
      tone={isHuman ? 'accent' : 'muted'}
      time={utcClock(entry.occurred_at)}
      isLast={isLast}
      className="px-3"
    >
      <ActorChip actorKind={entry.actor_kind} actor={entry.actor} />
      <p className="mt-1.5 text-body text-fg">{entry.summary}</p>
      <MonoValue muted className="text-caption">
        {entry.event_type}
      </MonoValue>

      {detail && (
        <details className="mt-1">
          <summary className="cursor-pointer rounded-sm text-caption text-fg-muted transition-colors duration-hover ease-out hover:text-fg-secondary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
            evidence
          </summary>
          <pre className="mt-1 overflow-x-auto rounded-sm bg-inset p-2 font-mono text-caption text-fg-secondary">
            {detail}
          </pre>
        </details>
      )}
    </TimelineItem>
  );
}

export function DecisionTimeline({ incidentId }: { incidentId: string | null }) {
  /*
   * `null` means the surface beside this rail is not about an incident — the Scenario Center, the
   * Scenario Builder. The query is disabled rather than pointed at a stand-in: substituting the
   * demo incident showed another incident's decisions next to a screen that never mentions it, and
   * 404ed for it after a demo reset removed it.
   */
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['timeline', incidentId],
    queryFn: () => api.timeline(incidentId as string),
    refetchInterval: pollUnlessMissing(5_000),
    retry: retryUnlessUnavailable,
    enabled: incidentId !== null,
  });

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between border-b border-border-subtle px-3 py-2">
        <h2 className="text-label uppercase text-fg-secondary">Decision timeline</h2>
        {data && <MonoValue muted>{data.entries.length}</MonoValue>}
      </header>

      {incidentId === null && (
        <EmptyState
          title="No incident in scope"
          description="This screen is not about one incident. Open a cascade or an incident to follow its decisions."
        />
      )}

      {incidentId !== null && isLoading && <LoadingState label="Loading timeline" />}

      {/*
        `Boolean(...)`, not `error &&`: react-query widens this query's error to `unknown` once
        `refetchInterval` is a callback, and `unknown && <JSX/>` is not a renderable node. The
        `instanceof` checks below are what actually read the error, and they are unaffected.
      */}
      {Boolean(error) && (
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
        <div className="min-h-0 flex-1 overflow-y-auto py-1.5">
          {/*
           * Newest first, and now on an actual spine. This was a `divide-y` list of rows: correct,
           * and indistinguishable from a table. The claim the rail exists to make is that these
           * records are ONE ordered sequence, and a marker per entry states that at a glance.
           */}
          <TimelineList label="Decisions, newest first">
            {[...data.entries].reverse().map((entry, index, entries) => (
              <Entry key={entry.id} entry={entry} isLast={index === entries.length - 1} />
            ))}
          </TimelineList>
        </div>
      )}
    </div>
  );
}

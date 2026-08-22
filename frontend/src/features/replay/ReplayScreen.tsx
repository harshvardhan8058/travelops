/**
 * Replay — `/replay/:incidentId`. docs/27 screen 6.
 *
 * A read over immutable records. Scrubbing is by entry index, filters are by actor kind and stage,
 * and the reconstructed state is folded from the records' own `STATE_CHANGED` details. Nothing is
 * interpolated between entries, because nothing was recorded between them.
 *
 * Owner: Stream D.
 */

import { useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';

import { api, ApiError } from '@/api/client';
import type { TimelineEntry } from '@/api/types';
import {
  EmptyState,
  ErrorState,
  LoadingState,
  MonoValue,
  Panel,
  StateBadge,
  StateRail,
} from '@/components/ui/primitives';
import { FilterChips } from '@/components/ui/Metric';
import { useKeyboardList } from '@/hooks/useKeyboardList';
import {
  applyFilters,
  BOOKKEEPING_EVENTS,
  NO_FILTERS,
  reconstruct,
  resolveCursor,
  type ReplayFilters,
} from './replayState';

const ACTOR_TONE: Record<string, string> = {
  orchestrator: 'text-accent border-accent-border',
  agent: 'text-state-info border-state-info/30',
  service: 'text-fg-secondary border-border',
  human: 'text-fg bg-raised border-border-strong font-medium',
  provider: 'text-fg-muted border-border-subtle',
};

export function ReplayScreen() {
  const { incidentId = '' } = useParams();
  /**
   * `null` means "the latest record", which is where replay opens: the screen's job is to show
   * everything that was recorded and let a reviewer scrub *back* through it. Starting at index 0
   * would open on a single entry and report an empty fold, which reads as missing data rather
   * than as an un-scrubbed timeline.
   */
  const [cursor, setCursor] = useState<number | null>(null);
  const [filters, setFilters] = useState<ReplayFilters>(NO_FILTERS);

  const timelineQuery = useQuery({
    queryKey: ['timeline', incidentId],
    queryFn: () => api.timeline(incidentId),
    enabled: incidentId.length > 0,
  });
  const incidentQuery = useQuery({
    queryKey: ['incident', incidentId],
    queryFn: () => api.incident(incidentId),
    enabled: incidentId.length > 0,
  });

  // Stable identity for the filter and fold memos below.
  const all = useMemo(() => timelineQuery.data?.entries ?? [], [timelineQuery.data]);
  const visible = useMemo(() => applyFilters(all, filters), [all, filters]);
  const hiddenBookkeeping = all.filter((entry) => BOOKKEEPING_EVENTS.has(entry.event_type)).length;

  // Resolved once, then used for both the fold and the controls so they can never disagree.
  const lastIndex = Math.max(0, visible.length - 1);
  const clampedCursor = resolveCursor(cursor, visible.length);
  const atLatest = clampedCursor === lastIndex;
  const state = useMemo(() => reconstruct(visible, clampedCursor), [visible, clampedCursor]);

  const keyboard = useKeyboardList({ count: visible.length, onOpen: setCursor });

  if (timelineQuery.isLoading) {
    return (
      <Panel title="Replay">
        <div className="h-[540px]">
          <LoadingState label="Loading records" />
        </div>
      </Panel>
    );
  }

  if (timelineQuery.error) {
    const error = timelineQuery.error instanceof ApiError ? timelineQuery.error : null;
    return (
      <ErrorState
        code={error?.code ?? 'INTERNAL_ERROR'}
        message={error?.message ?? `Could not load records for ${incidentId}.`}
        correlationId={error?.correlationId ?? null}
        onRetry={() => void timelineQuery.refetch()}
      />
    );
  }

  const actorKinds = [...new Set(all.map((entry) => entry.actor_kind))];
  const stages = [...new Set(all.map((entry) => entry.stage))];

  return (
    <div className="grid min-h-0 grid-cols-[minmax(0,1fr)_380px] gap-3">
      <Panel
        title="Replay"
        className="flex min-h-0 flex-col overflow-hidden"
        actions={
          <MonoValue muted className="text-caption">
            {visible.length} of {all.length} records
          </MonoValue>
        }
      >
        <div className="flex flex-col gap-2 border-b border-border-subtle px-3 py-2">
          <div className="flex items-center gap-2">
            <span className="shrink-0 text-caption uppercase text-fg-muted" aria-hidden>
              position
            </span>
            <input
              type="range"
              min={0}
              max={lastIndex}
              value={clampedCursor}
              disabled={visible.length === 0}
              onChange={(event) => setCursor(Number(event.target.value))}
              aria-label="Replay position by record index"
              aria-valuetext={
                visible[clampedCursor]
                  ? `record ${clampedCursor + 1} of ${visible.length}: ${visible[clampedCursor]?.summary}`
                  : 'no records'
              }
              className="h-1 w-full accent-accent"
            />
            <MonoValue muted className="shrink-0">
              {visible.length === 0 ? '0/0' : `${clampedCursor + 1}/${visible.length}`}
            </MonoValue>
            <button
              type="button"
              onClick={() => setCursor(null)}
              disabled={atLatest}
              className={clsx(
                'shrink-0 rounded-sm border px-2 py-0.5 text-label uppercase',
                'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                /*
                 * The unavailable state is carried by the border, not by dimming the label.
                 * `opacity-50` over --fg-muted measured 2.22:1, and a projector is the worst
                 * place to discover that a control's text has become unreadable.
                 */
                atLatest
                  ? 'border-border-subtle text-fg-muted'
                  : 'border-border-strong text-fg-secondary hover:text-fg',
              )}
            >
              Latest
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <FilterChips
              label="Actor kind"
              value={[...filters.actorKinds][0] ?? 'all'}
              onChange={(next) =>
                setFilters((current) => ({
                  ...current,
                  actorKinds: next === 'all' ? new Set() : new Set([next]),
                }))
              }
              options={[
                { value: 'all', label: 'All actors' },
                ...actorKinds.map((kind) => ({
                  value: kind,
                  label: kind,
                  count: all.filter((entry) => entry.actor_kind === kind).length,
                })),
              ]}
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <FilterChips
              label="Stage"
              value={[...filters.stages][0] ?? 'all'}
              onChange={(next) =>
                setFilters((current) => ({
                  ...current,
                  stages: next === 'all' ? new Set() : new Set([next]),
                }))
              }
              options={[
                { value: 'all', label: 'All stages' },
                ...stages.map((stage) => ({ value: stage, label: stage })),
              ]}
            />
            <button
              type="button"
              role="switch"
              aria-checked={filters.onlyDecisions}
              onClick={() =>
                setFilters((current) => ({ ...current, onlyDecisions: !current.onlyDecisions }))
              }
              className={clsx(
                'rounded-sm border px-2 py-0.5 text-label uppercase',
                'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                filters.onlyDecisions
                  ? 'border-accent-border bg-accent-subtle text-accent'
                  : 'border-border-subtle text-fg-muted',
              )}
            >
              Only decisions
            </button>
            {hiddenBookkeeping > 0 && (
              <button
                type="button"
                role="switch"
                aria-checked={filters.includeBookkeeping}
                onClick={() =>
                  setFilters((current) => ({
                    ...current,
                    includeBookkeeping: !current.includeBookkeeping,
                  }))
                }
                className="rounded-sm border border-border-subtle px-2 py-0.5 text-label uppercase text-fg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                {filters.includeBookkeeping ? 'Hide' : 'Show'} {hiddenBookkeeping} bookkeeping
              </button>
            )}
          </div>
        </div>

        {visible.length === 0 ? (
          <EmptyState
            title="No records match"
            description="Every record was filtered out. Clear a filter to see the incident's history."
          />
        ) : (
          <ol
            ref={keyboard.containerRef as React.RefObject<HTMLOListElement>}
            className="min-h-0 flex-1 overflow-y-auto"
            onKeyDown={keyboard.onKeyDown}
            aria-label="Recorded events"
          >
            {visible.map((entry, index) => (
              <ReplayEntry
                key={entry.id}
                entry={entry}
                past={index <= clampedCursor}
                current={index === clampedCursor}
                onSelect={() => setCursor(index)}
                itemProps={keyboard.itemProps(index)}
              />
            ))}
          </ol>
        )}
      </Panel>

      <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
        <Panel
          title="State at cursor"
          actions={
            <MonoValue muted className="text-caption">
              {visible.length === 0 ? 'no records' : `records 1–${clampedCursor + 1} folded`}
            </MonoValue>
          }
        >
          <div className="flex flex-col gap-2 px-3 py-2">
            <div className="flex items-center gap-2">
              <span className="text-caption uppercase text-fg-muted">state</span>
              {state.state ? (
                <StateBadge status={state.state} />
              ) : (
                <span className="text-caption text-fg-muted">no transition recorded yet</span>
              )}
            </div>
            <div className="flex items-center gap-2 text-caption uppercase text-fg-muted">
              at{' '}
              <MonoValue muted>
                {state.cursorAt ? state.cursorAt.slice(11, 19) + 'Z' : '—'}
              </MonoValue>
            </div>
            <dl className="mt-1 flex flex-col gap-1">
              <Row label="evaluations" value={state.evaluationsSeen} />
              <Row label="actions" value={state.actionsCompleted} />
              <Row label="decisions" value={state.decisionsRecorded.length} />
            </dl>
            {state.decisionsRecorded.length > 0 && (
              <ul className="mt-1 flex flex-col gap-0.5">
                {state.decisionsRecorded.map((decision, i) => (
                  <li key={i} className="text-caption text-fg-secondary">
                    <MonoValue muted>{decision.actorId ?? 'operator'}</MonoValue>{' '}
                    {decision.decision}
                    {decision.assuranceId !== null && (
                      <>
                        {' '}
                        evaluation <MonoValue muted>{decision.assuranceId}</MonoValue>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-1 text-caption text-fg-muted">
              Folded from the records' own transitions. Nothing is interpolated between entries.
            </p>
          </div>
        </Panel>

        {incidentQuery.data && (
          <Panel title="Recorded rail">
            <div className="px-3 py-2">
              <StateRail rail={incidentQuery.data.state_rail} current={incidentQuery.data.state} />
            </div>
          </Panel>
        )}
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <dt className="text-caption uppercase text-fg-muted">{label}</dt>
      <dd>
        <MonoValue>{value}</MonoValue>
      </dd>
    </div>
  );
}

function ReplayEntry({
  entry,
  past,
  current,
  onSelect,
  itemProps,
}: {
  entry: TimelineEntry;
  past: boolean;
  current: boolean;
  onSelect: () => void;
  /** Spread whole: `data-active` is how the roving-tabindex hook locates the item to focus. */
  itemProps: { tabIndex: number; 'data-active'?: true };
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <li
      className={clsx(
        'border-b border-l-2 border-border-subtle',
        current ? 'border-l-accent bg-raised' : 'border-l-transparent',
        !past && 'opacity-45',
      )}
    >
      <div className="flex items-center gap-2 px-2 py-1.5">
        <button
          type="button"
          onClick={onSelect}
          {...itemProps}
          aria-current={current || undefined}
          className="flex min-w-0 flex-1 items-center gap-2 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <MonoValue muted className="shrink-0">
            {entry.occurred_at.slice(11, 19)}
          </MonoValue>
          <span
            className={clsx(
              'shrink-0 rounded-sm border px-1 py-0.5 text-caption uppercase',
              ACTOR_TONE[entry.actor_kind] ?? 'text-fg-muted border-border-subtle',
            )}
          >
            {entry.actor_kind}
          </span>
          <span className="min-w-0 flex-1 truncate text-body text-fg">{entry.summary}</span>
          <MonoValue muted className="shrink-0 text-caption">
            {entry.event_type}
          </MonoValue>
        </button>
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          aria-label={`${expanded ? 'Collapse' : 'Expand'} record ${entry.id}`}
          className="shrink-0 rounded-sm p-1 text-caption text-fg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {expanded ? '−' : '+'}
        </button>
      </div>
      {expanded && (
        <div className="border-t border-border-subtle bg-inset px-3 py-2">
          <dl className="flex flex-col gap-1">
            <div className="flex gap-2">
              <dt className="w-[92px] shrink-0 text-caption uppercase text-fg-muted">stage</dt>
              <dd>
                <MonoValue muted>{entry.stage}</MonoValue>
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-[92px] shrink-0 text-caption uppercase text-fg-muted">actor</dt>
              <dd>
                <MonoValue muted>{entry.actor}</MonoValue>
              </dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-[92px] shrink-0 text-caption uppercase text-fg-muted">
                correlation
              </dt>
              <dd>
                {entry.correlation_id ? (
                  <MonoValue muted className="break-all">
                    {entry.correlation_id}
                  </MonoValue>
                ) : (
                  <span className="text-caption text-fg-muted">not recorded</span>
                )}
              </dd>
            </div>
          </dl>
          {entry.detail && (
            <pre className="mt-1.5 overflow-x-auto rounded-sm bg-base p-2 font-mono text-caption text-fg-secondary">
              {JSON.stringify(entry.detail, null, 2)}
            </pre>
          )}
        </div>
      )}
    </li>
  );
}

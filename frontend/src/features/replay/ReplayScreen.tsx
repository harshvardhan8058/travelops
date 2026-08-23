/**
 * Replay — `/replay/:incidentId` and `/replay/group/:groupRef`. docs/27 screen 6.
 *
 * A read over immutable records, from the endpoints built for it: `GET /incidents/{id}/replay` and
 * `GET /incident-groups/{ref}/replay`. It previously read the incident *timeline*, which worked but
 * could not do the one thing Phase 2 needs — a group replay interleaves eight incidents plus the
 * group's own entries in true chronological order, and the timeline endpoint is scoped to a single
 * incident by construction. The replay contract also carries `decision_scope`, `plan_approval_id`
 * and `evidence_refs` as typed fields, so a plan-covered action is distinguishable from a
 * per-action approval without parsing a JSON blob.
 *
 * Scrubbing is by record index, filters are by actor kind and stage, and the reconstructed state is
 * folded from the records' own transitions. Nothing is interpolated between records, because
 * nothing was recorded between them.
 *
 * Owner: Stream D.
 */

import { useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';

import { api, ApiError } from '@/api/client';
import type { ReplayFrame } from '@/api/types';
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
  const { incidentId = '', groupRef = '' } = useParams();
  /**
   * One screen, two scopes. A group replay is the Phase 2 view: the group's own cascade entries
   * interleaved with every member incident's, in the order they were recorded. The scope comes from
   * the route rather than from sniffing the reference format, so the client never has to guess what
   * kind of thing an identifier names.
   */
  const scope = groupRef ? ('group' as const) : ('incident' as const);
  const reference = groupRef || incidentId;
  /**
   * `null` means "the latest record", which is where replay opens: the screen's job is to show
   * everything that was recorded and let a reviewer scrub *back* through it. Starting at index 0
   * would open on a single entry and report an empty fold, which reads as missing data rather
   * than as an un-scrubbed timeline.
   */
  const [cursor, setCursor] = useState<number | null>(null);
  const [filters, setFilters] = useState<ReplayFilters>(NO_FILTERS);

  const replayQuery = useQuery({
    queryKey: ['replay', scope, reference],
    queryFn: () => (scope === 'group' ? api.groupReplay(reference) : api.incidentReplay(reference)),
    enabled: reference.length > 0,
  });
  /** The recorded rail is an incident property. A group has no single rail, so it is not shown. */
  const incidentQuery = useQuery({
    queryKey: ['incident', incidentId],
    queryFn: () => api.incident(incidentId),
    enabled: scope === 'incident' && incidentId.length > 0,
  });

  // Stable identity for the filter and fold memos below.
  const all = useMemo(() => replayQuery.data?.frames ?? [], [replayQuery.data]);
  const visible = useMemo(() => applyFilters(all, filters), [all, filters]);
  const hiddenBookkeeping = all.filter((entry) => BOOKKEEPING_EVENTS.has(entry.event_type)).length;

  // Resolved once, then used for both the fold and the controls so they can never disagree.
  const lastIndex = Math.max(0, visible.length - 1);
  const clampedCursor = resolveCursor(cursor, visible.length);
  const atLatest = clampedCursor === lastIndex;
  const state = useMemo(() => reconstruct(visible, clampedCursor), [visible, clampedCursor]);

  const keyboard = useKeyboardList({ count: visible.length, onOpen: setCursor });

  if (replayQuery.isLoading) {
    return (
      <Panel title="Replay">
        <div className="h-[540px]">
          <LoadingState label="Loading records" />
        </div>
      </Panel>
    );
  }

  if (replayQuery.error) {
    const error = replayQuery.error instanceof ApiError ? replayQuery.error : null;
    return (
      <ErrorState
        code={error?.code ?? 'INTERNAL_ERROR'}
        message={error?.message ?? `Could not load records for ${reference}.`}
        correlationId={error?.correlationId ?? null}
        onRetry={() => void replayQuery.refetch()}
      />
    );
  }

  const actorKinds = [...new Set(all.map((entry) => entry.actor_kind))];
  const stages = [...new Set(all.map((entry) => entry.stage))];

  return (
    <div className="grid min-h-0 grid-cols-[minmax(0,1fr)_380px] gap-3">
      <Panel
        title={scope === 'group' ? 'Replay: whole cascade' : 'Replay'}
        className="flex min-h-0 flex-col overflow-hidden"
        actions={
          <span className="flex items-center gap-2">
            <MonoValue muted className="text-caption">
              {reference}
            </MonoValue>
            <MonoValue muted className="text-caption">
              {visible.length} of {all.length} records
            </MonoValue>
          </span>
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
                key={entry.sequence}
                entry={entry}
                past={index <= clampedCursor}
                current={index === clampedCursor}
                onSelect={() => setCursor(index)}
                itemProps={keyboard.itemProps(index)}
                showIncident={scope === 'group'}
              />
            ))}
          </ol>
        )}
        {replayQuery.data && (
          /* The endpoint states that it wrote nothing. Repeated here rather than asserted by
           * this component, which has no way to know. */
          <p className="border-t border-border-subtle px-3 py-2 text-caption text-fg-muted">
            {replayQuery.data.is_read_only ? 'Read-only. ' : ''}
            {replayQuery.data.note}
          </p>
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

        {scope === 'incident' && incidentQuery.data && (
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
  showIncident,
}: {
  entry: ReplayFrame;
  past: boolean;
  current: boolean;
  onSelect: () => void;
  /** Spread whole: `data-active` is how the roving-tabindex hook locates the item to focus. */
  itemProps: { tabIndex: number; 'data-active'?: true };
  /** Which member flight a record belongs to. Meaningless for a single incident, essential for
   *  a group replay interleaving eight of them. */
  showIncident: boolean;
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
          {showIncident && (
            <MonoValue muted className="w-[168px] shrink-0 truncate text-caption">
              {/* A group entry belongs to no single flight. Said, not blanked. */}
              {entry.incident_reference ?? 'group'}
            </MonoValue>
          )}
          <span className="min-w-0 flex-1 truncate text-body text-fg">{entry.summary}</span>
          {entry.decision_scope && (
            /* `plan` or `action`. Both are a person's act; an auditor has to tell them apart, so
             * the scope is on the row rather than hidden behind an expander. */
            <span className="shrink-0 rounded-sm border border-border-strong bg-raised px-1 py-0.5 text-caption uppercase text-fg">
              {entry.decision_scope}
            </span>
          )}
          <MonoValue muted className="shrink-0 text-caption">
            {entry.event_type}
          </MonoValue>
        </button>
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          aria-label={`${expanded ? 'Collapse' : 'Expand'} record ${entry.sequence}`}
          className="shrink-0 rounded-sm p-1 text-caption text-fg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {expanded ? '\u2212' : '+'}
        </button>
      </div>
      {expanded && (
        <div className="border-t border-border-subtle bg-inset px-3 py-2">
          <dl className="flex flex-col gap-1">
            <DetailRow label="stage">
              <MonoValue muted>{entry.stage}</MonoValue>
            </DetailRow>
            <DetailRow label="actor">
              <MonoValue muted>{entry.actor}</MonoValue>
            </DetailRow>
            {(entry.state_before || entry.state_after) && (
              <DetailRow label="transition">
                <MonoValue muted>
                  {entry.state_before ?? 'none'} to {entry.state_after ?? 'none'}
                </MonoValue>
              </DetailRow>
            )}
            <DetailRow label="assurance">
              {entry.assurance_id === null ? (
                <span className="text-caption text-fg-muted">not an assured step</span>
              ) : (
                <MonoValue muted>{entry.assurance_id}</MonoValue>
              )}
            </DetailRow>
            {entry.human_decision_id !== null && (
              <DetailRow label="decision">
                <MonoValue muted>
                  #{entry.human_decision_id}
                  {entry.decision_scope ? ` (${entry.decision_scope})` : ''}
                  {entry.plan_approval_id !== null
                    ? ` via plan approval ${entry.plan_approval_id}`
                    : ''}
                </MonoValue>
              </DetailRow>
            )}
            <DetailRow label="evidence">
              {entry.evidence_refs.length === 0 ? (
                <span className="text-caption text-fg-muted">none recorded</span>
              ) : (
                <span className="flex flex-wrap gap-1">
                  {/* Capped for reading, with the true total stated. 604 chips is not evidence. */}
                  {entry.evidence_refs.slice(0, 12).map((ref) => (
                    <MonoValue key={ref} muted className="text-caption">
                      {ref}
                    </MonoValue>
                  ))}
                  {entry.evidence_refs.length > 12 && (
                    <span className="text-caption text-fg-muted">
                      and {entry.evidence_refs.length - 12} more of {entry.evidence_refs.length}{' '}
                      recorded
                    </span>
                  )}
                </span>
              )}
            </DetailRow>
          </dl>
          {Object.keys(entry.detail).length > 0 && (
            <pre className="mt-1.5 overflow-x-auto rounded-sm bg-base p-2 font-mono text-caption text-fg-secondary">
              {JSON.stringify(entry.detail, null, 2)}
            </pre>
          )}
        </div>
      )}
    </li>
  );
}

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      <dt className="w-[92px] shrink-0 text-caption uppercase text-fg-muted">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

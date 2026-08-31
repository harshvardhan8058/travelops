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
import { ActorChip } from '@/features/agent/ActorChip';
import {
  DefinitionList,
  DefinitionRow,
  Labelled,
  PageHeader,
  rowSelectionClass,
  Toolbar,
} from '@/components/ui/composition';
import { utcClock } from '@/components/ui/format';
import { useKeyboardList } from '@/hooks/useKeyboardList';
import {
  applyFrameFilters,
  BOOKKEEPING_EVENTS,
  foldFrames,
  NO_FRAME_FILTERS,
  resolveCursor,
  type FrameFilters,
} from './replayState';

/*
 * The local `ACTOR_TONE` map is gone: attribution renders through `ActorChip`.
 *
 * This was the third copy of the same palette — `ActorChip.tsx`, `DecisionTimeline.tsx` and here —
 * and all three carried the identical `human` class string. Two of the three also painted `agent`
 * with `text-state-info`, which is the defect `ActorChip`'s own header warns about: identity
 * borrowing a colour from the operational state ramp. On this screen a reviewer is scrubbing a log
 * to find what a *person* did, so "who acted" must never be readable as "what state it is in".
 *
 * `ActorChip` keeps the one thing that mattered here: a human act is the highest-contrast, most
 * solid chip in the list, and it is the only one carrying a glyph.
 */

export function ReplayScreen() {
  const { incidentId = '' } = useParams();
  /**
   * `null` means "the latest record", which is where replay opens: the screen's job is to show
   * everything that was recorded and let a reviewer scrub *back* through it. Starting at index 0
   * would open on a single entry and report an empty fold, which reads as missing data rather
   * than as an un-scrubbed timeline.
   */
  const [cursor, setCursor] = useState<number | null>(null);
  const [filters, setFilters] = useState<FrameFilters>(NO_FRAME_FILTERS);
  /**
   * Incident or group. The group replay interleaves every member's frames by time, which is the
   * only way to see that eight recoveries ran concurrently rather than in sequence — and the only
   * place a plan-scoped approval reads as one act across several incidents.
   */
  const [scope, setScope] = useState<'incident' | 'group'>('incident');

  const incidentQuery = useQuery({
    queryKey: ['incident', incidentId],
    queryFn: () => api.incident(incidentId),
    enabled: incidentId.length > 0,
  });

  const groupRef = incidentQuery.data?.group_reference ?? null;

  const replayQuery = useQuery({
    queryKey: ['replay', scope, scope === 'group' ? groupRef : incidentId],
    queryFn: () =>
      scope === 'group' && groupRef ? api.groupReplay(groupRef) : api.incidentReplay(incidentId),
    enabled: incidentId.length > 0 && (scope === 'incident' || Boolean(groupRef)),
  });

  // Stable identity for the filter and fold memos below.
  const all = useMemo(() => replayQuery.data?.frames ?? [], [replayQuery.data]);
  const visible = useMemo(() => applyFrameFilters(all, filters), [all, filters]);
  const hiddenBookkeeping = all.filter((frame) => BOOKKEEPING_EVENTS.has(frame.event_type)).length;

  // Resolved once, then used for both the fold and the controls so they can never disagree.
  const lastIndex = Math.max(0, visible.length - 1);
  const clampedCursor = resolveCursor(cursor, visible.length);
  const atLatest = clampedCursor === lastIndex;
  // Folds `state_after` as the server recorded it. Nothing here infers a state from a detail blob.
  const state = useMemo(() => foldFrames(visible, clampedCursor), [visible, clampedCursor]);

  const keyboard = useKeyboardList({ count: visible.length, onOpen: setCursor });

  if (replayQuery.isLoading || incidentQuery.isLoading) {
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
        message={error?.message ?? `Could not load replay frames for ${incidentId}.`}
        correlationId={error?.correlationId ?? null}
        onRetry={() => void replayQuery.refetch()}
      />
    );
  }

  const actorKinds = [...new Set(all.map((frame) => frame.actor_kind))];
  const stages = [...new Set(all.map((frame) => frame.stage))];
  const memberRefs = [
    ...new Set(
      all.map((frame) => frame.incident_reference).filter((ref): ref is string => Boolean(ref)),
    ),
  ].sort();

  return (
    <div className="flex min-h-0 flex-col gap-3">
      {/*
       * The record being replayed is the subject, so its reference is the title — and it changes
       * with the scope, because a group replay is a different record from one incident's.
       *
       * `note` is the server's own sentence about what this endpoint does and does not do. It was
       * fetched and never rendered anywhere on the screen; a read-only guarantee that the console
       * declines to repeat is a guarantee the reviewer has to take on trust.
       */}
      <PageHeader
        eyebrow="Replay"
        title={
          <span className="font-mono tabular-nums">
            {scope === 'group' && groupRef ? groupRef : incidentId}
          </span>
        }
        status={
          replayQuery.data?.is_read_only ? (
            <StateBadge status="skipped" label="read-only" />
          ) : undefined
        }
        meta={
          <>
            <Labelled label="scope">
              <MonoValue muted>{scope === 'group' ? 'whole group' : 'this incident'}</MonoValue>
            </Labelled>
            <Labelled label="frames">
              <MonoValue>{all.length}</MonoValue>
            </Labelled>
            <Labelled label="at cursor">
              <MonoValue muted>
                {visible.length === 0 ? '0/0' : `${clampedCursor + 1}/${visible.length}`}
              </MonoValue>
            </Labelled>
          </>
        }
        actions={
          <Toolbar>
            <FilterChips
              label="Replay scope"
              value={scope}
              onChange={(next) => {
                setScope(next as 'incident' | 'group');
                // A cursor from a 26-frame incident means nothing in a 226-frame group replay.
                setCursor(null);
                setFilters(NO_FRAME_FILTERS);
              }}
              options={[
                { value: 'incident', label: 'This incident' },
                ...(groupRef ? [{ value: 'group', label: 'Whole group' }] : []),
              ]}
            />
          </Toolbar>
        }
        footer={
          replayQuery.data?.note ? (
            <p className="text-caption text-fg-muted">{replayQuery.data.note}</p>
          ) : undefined
        }
      />

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_380px] gap-3 2xl:grid-cols-[minmax(0,1fr)_440px]">
        <Panel
          title="Records"
          className="flex min-h-0 flex-col overflow-hidden"
          actions={
            <div className="flex items-center gap-2">
              <MonoValue muted className="text-caption">
                {visible.length} of {all.length} frames
              </MonoValue>
            </div>
          }
        >
          <div className="flex flex-col gap-2.5 border-b border-border-subtle px-3 py-2.5">
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
              {scope === 'group' && memberRefs.length > 1 && (
                <FilterChips
                  label="Member incident"
                  value={[...filters.incidentReferences][0] ?? 'all'}
                  onChange={(next) =>
                    setFilters((current) => ({
                      ...current,
                      incidentReferences: next === 'all' ? new Set() : new Set([next]),
                    }))
                  }
                  options={[
                    { value: 'all', label: 'All members', count: all.length },
                    ...memberRefs.map((ref) => ({
                      value: ref,
                      label: ref.replace(/^INC-\d{4}-\d{4}-/, ''),
                      count: all.filter((frame) => frame.incident_reference === ref).length,
                    })),
                  ]}
                />
              )}
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
              {visible.map((frame, index) => (
                <ReplayEntry
                  key={`${frame.incident_reference ?? 'group'}-${frame.sequence}`}
                  frame={frame}
                  showIncident={scope === 'group'}
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
                  {state.cursorAt ? `${utcClock(state.cursorAt) ?? '—'}Z` : '—'}
                </MonoValue>
              </div>
              <dl className="mt-1.5 flex flex-col gap-1">
                <Row label="evaluations" value={state.evaluationsSeen} />
                <Row label="actions" value={state.actionsCompleted} />
                <Row label="decisions" value={state.decisions.length} />
                <Row label="transitions" value={state.statesReached.length} />
              </dl>

              {state.decisions.length > 0 && (
                <ul className="mt-1 flex flex-col gap-1">
                  {state.decisions.map((decision, i) => (
                    <li key={i} className="flex flex-wrap items-center gap-1.5 text-caption">
                      {/* A person's act reads as a person's, and its SCOPE is stated: a plan-wide
                        signature and a per-action one are different commitments. */}
                      <ActorChip actorKind="human" actor={decision.actor} />
                      {decision.scope && (
                        <span className="text-fg-secondary">
                          {decision.scope === 'plan' ? 'plan-scoped' : 'action-scoped'}
                        </span>
                      )}
                      {decision.assuranceId !== null && (
                        <MonoValue muted>evaluation {decision.assuranceId}</MonoValue>
                      )}
                      {decision.planApprovalId !== null && (
                        <MonoValue muted>approval {decision.planApprovalId}</MonoValue>
                      )}
                      {decision.incidentReference && scope === 'group' && (
                        <MonoValue muted>{decision.incidentReference}</MonoValue>
                      )}
                    </li>
                  ))}
                </ul>
              )}

              {scope === 'group' && state.incidentsTouched.length > 0 && (
                <p className="mt-1 text-caption text-fg-muted">
                  covers <MonoValue muted>{state.incidentsTouched.length}</MonoValue> member
                  incident
                  {state.incidentsTouched.length === 1 ? '' : 's'} — the group's state is the last
                  transition anywhere in it, not one member's
                </p>
              )}

              {state.evidenceRefs.length > 0 && (
                <details className="mt-1">
                  <summary className="cursor-pointer text-caption text-fg-muted">
                    {state.evidenceRefs.length} evidence reference
                    {state.evidenceRefs.length === 1 ? '' : 's'} up to here
                  </summary>
                  <ul className="mt-1 flex flex-wrap gap-1">
                    {state.evidenceRefs.map((ref) => (
                      <li key={ref}>
                        <MonoValue muted className="text-caption">
                          {ref}
                        </MonoValue>
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              <p className="mt-1 text-caption text-fg-muted">
                Folded from each frame's own <MonoValue muted>state_after</MonoValue>. The state is
                read, not inferred, and nothing is interpolated between frames.
              </p>
            </div>
          </Panel>

          {incidentQuery.data && (
            <Panel title="Recorded rail">
              <div className="px-3 py-2.5">
                <StateRail
                  rail={incidentQuery.data.state_rail}
                  current={incidentQuery.data.state}
                />
              </div>
            </Panel>
          )}
        </div>
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
  frame,
  past,
  current,
  onSelect,
  itemProps,
  showIncident,
}: {
  frame: ReplayFrame;
  past: boolean;
  current: boolean;
  onSelect: () => void;
  /** Spread whole: `data-active` is how the roving-tabindex hook locates the item to focus. */
  itemProps: { tabIndex: number; 'data-active'?: true };
  /** Group replay interleaves members, so each frame has to say which incident it belongs to. */
  showIncident: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const isHuman = frame.actor_kind === 'human' || frame.human_decision_id !== null;
  return (
    <li className={clsx(rowSelectionClass(current), !past && 'opacity-45')}>
      <div className="flex items-center gap-2 px-2 py-1.5">
        <button
          type="button"
          onClick={onSelect}
          {...itemProps}
          aria-current={current || undefined}
          className="flex min-w-0 flex-1 items-center gap-2 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <MonoValue muted className="shrink-0 text-caption">
            {frame.sequence}
          </MonoValue>
          <MonoValue muted className="shrink-0">
            {frame.occurred_at.slice(11, 19)}
          </MonoValue>
          {/*
           * `human_decision_id` is as much a mark of a person as `actor_kind` is: a frame recorded
           * against a service can still be the record of somebody's signature. So the chip is told
           * `human` when either says so, rather than trusting one field.
           */}
          <ActorChip actorKind={isHuman ? 'human' : frame.actor_kind} className="shrink-0" />
          {showIncident && frame.incident_reference && (
            <MonoValue muted className="shrink-0 text-caption">
              {frame.incident_reference.replace(/^INC-\d{4}-\d{4}-/, '')}
            </MonoValue>
          )}
          <span className="min-w-0 flex-1 truncate text-body text-fg">{frame.summary}</span>
          {/* The transition this frame caused, from the server's own fields. */}
          {frame.state_after && frame.state_after !== frame.state_before && (
            <span className="shrink-0 text-caption text-fg-muted">
              {frame.state_before ?? '—'} →{' '}
              <span className="text-fg-secondary">{frame.state_after}</span>
            </span>
          )}
          <MonoValue muted className="shrink-0 text-caption">
            {frame.event_type}
          </MonoValue>
        </button>
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          aria-label={`${expanded ? 'Collapse' : 'Expand'} frame ${frame.sequence}`}
          className="shrink-0 rounded-sm p-1 text-caption text-fg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {expanded ? '\u2212' : '+'}
        </button>
      </div>
      {expanded && (
        <div className="border-t border-border-subtle bg-inset px-3 py-2">
          <DefinitionList width="sm">
            <Detail label="stage" value={frame.stage} />
            <Detail label="actor" value={frame.actor} />
            {frame.incident_reference && (
              <Detail label="incident" value={frame.incident_reference} />
            )}
            {frame.decision_scope && (
              <Detail
                label="scope"
                value={frame.decision_scope}
                note={
                  frame.decision_scope === 'plan'
                    ? 'One signature covering several evaluations at once.'
                    : 'A decision on this action alone.'
                }
              />
            )}
            {frame.assurance_id !== null && (
              <Detail label="evaluation" value={String(frame.assurance_id)} />
            )}
            {frame.plan_approval_id !== null && (
              <Detail label="approval" value={String(frame.plan_approval_id)} />
            )}
          </DefinitionList>
          {frame.evidence_refs.length > 0 && (
            <div className="mt-1.5">
              <span className="text-caption uppercase text-fg-muted">evidence</span>
              <ul className="mt-0.5 flex flex-wrap gap-1">
                {frame.evidence_refs.map((ref) => (
                  <li key={ref}>
                    <MonoValue muted className="text-caption">
                      {ref}
                    </MonoValue>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {frame.detail && Object.keys(frame.detail).length > 0 && (
            <pre className="mt-1.5 overflow-x-auto rounded-sm bg-base p-2 font-mono text-caption text-fg-secondary">
              {JSON.stringify(frame.detail, null, 2)}
            </pre>
          )}
        </div>
      )}
    </li>
  );
}

/*
 * One of five definition-row implementations that existed at three different label widths, which is
 * why two panels in the same column had their values starting at different x positions.
 */
function Detail({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <DefinitionRow label={label} width="sm">
      <MonoValue muted className="break-all">
        {value}
      </MonoValue>
      {note && <span className="ml-1.5 text-caption text-fg-muted">{note}</span>}
    </DefinitionRow>
  );
}

/**
 * Replay state reconstruction. Pure, and a read over immutable records — not a model narrating
 * history and not a re-run of any logic.
 *
 * Scrubbing is by ENTRY INDEX rather than wall-clock time. An injected scenario carries the
 * disruption's own `opened_at` while the transitions carry the time the workflow ran, so a
 * time-based scrubber would stall across a 27-hour gap that never happened. Index-based scrubbing
 * has no such failure mode.
 *
 * Owner: Stream D.
 */

import type { IncidentState } from '@/api/types';

/**
 * The structural subset of a record this fold needs.
 *
 * Deliberately not `TimelineEntry` or `ReplayFrame`. Replay reads `/replay`, which returns frames
 * carrying `state_after` as a typed field rather than buried in `detail`; the incident timeline
 * predates that. Both satisfy this shape, so the fold works over either without either contract
 * having to pretend to be the other.
 */
export interface ReplayRecord {
  occurred_at: string;
  stage: string;
  actor: string;
  actor_kind: string;
  event_type: string;
  summary: string;
  detail?: Record<string, unknown> | null;
  /** Set by the server's replay frames. Preferred over reading `detail.to` when present. */
  state_after?: string | null;
}

/** Bookkeeping entries the engine writes for idempotency. Hidden by default, counted openly. */
export const BOOKKEEPING_EVENTS = new Set(['WORKFLOW_RUN_REQUESTED']);

/** The subset a reviewer means by "only decisions". */
export const DECISION_EVENTS = new Set([
  'ASSURANCE_EVALUATED',
  'HUMAN_DECISION_RECORDED',
  'STATE_CHANGED',
]);

export interface ReplayFilters {
  actorKinds: Set<string>;
  stages: Set<string>;
  onlyDecisions: boolean;
  includeBookkeeping: boolean;
}

export const NO_FILTERS: ReplayFilters = {
  actorKinds: new Set(),
  stages: new Set(),
  onlyDecisions: false,
  includeBookkeeping: false,
};

export function applyFilters<T extends ReplayRecord>(entries: T[], filters: ReplayFilters): T[] {
  return entries.filter((entry) => {
    if (!filters.includeBookkeeping && BOOKKEEPING_EVENTS.has(entry.event_type)) return false;
    if (filters.onlyDecisions && !DECISION_EVENTS.has(entry.event_type)) return false;
    if (filters.actorKinds.size > 0 && !filters.actorKinds.has(entry.actor_kind)) return false;
    if (filters.stages.size > 0 && !filters.stages.has(entry.stage)) return false;
    return true;
  });
}

/**
 * Resolves the scrub position against the currently visible records.
 *
 * `null` means "the latest record". Replay opens there deliberately: a reviewer arrives to see
 * what was recorded and scrubs *backwards* to find the moment they care about. Opening at index 0
 * folds a single entry, which reports an empty state and reads as missing data rather than as an
 * un-scrubbed timeline. Filters change `count` underneath a held cursor, so this also clamps.
 */
export function resolveCursor(cursor: number | null, count: number): number {
  const lastIndex = Math.max(0, count - 1);
  if (cursor === null) return lastIndex;
  return Math.min(Math.max(0, cursor), lastIndex);
}

export interface ReconstructedState {
  /** Derived by folding STATE_CHANGED details up to the cursor. Never predicted. */
  state: IncidentState | null;
  statesReached: { state: string; at: string }[];
  decisionsRecorded: { assuranceId: number | null; decision: string; actorId: string | null }[];
  actionsCompleted: number;
  evaluationsSeen: number;
  cursorAt: string | null;
}

function detailString(
  detail: Record<string, unknown> | null | undefined,
  key: string,
): string | null {
  const value = detail?.[key];
  return typeof value === 'string' ? value : null;
}

function detailNumber(
  detail: Record<string, unknown> | null | undefined,
  key: string,
): number | null {
  const value = detail?.[key];
  return typeof value === 'number' ? value : null;
}

/**
 * Folds entries [0, cursor] into the state the records say held at that point.
 *
 * Monotonic by construction: adding an entry can only add to what is known. There is no
 * interpolation between entries, because nothing is recorded between them.
 */
export function reconstruct(entries: ReplayRecord[], cursor: number): ReconstructedState {
  const upTo = entries.slice(0, Math.max(0, Math.min(cursor + 1, entries.length)));
  const statesReached: { state: string; at: string }[] = [];
  const decisionsRecorded: ReconstructedState['decisionsRecorded'] = [];
  let actionsCompleted = 0;
  let evaluationsSeen = 0;

  for (const entry of upTo) {
    if (entry.event_type === 'STATE_CHANGED') {
      // The replay frame's own field first; `detail.to` only for the timeline contract, which
      // has no such field. Reading the typed value where it exists means the fold does not
      // depend on the shape of a free-form JSON blob.
      const to = entry.state_after ?? detailString(entry.detail, 'to');
      if (to) statesReached.push({ state: to, at: entry.occurred_at });
    }
    if (entry.event_type === 'INCIDENT_OPENED') {
      statesReached.push({ state: 'detected', at: entry.occurred_at });
    }
    if (entry.event_type === 'ASSURANCE_EVALUATED') evaluationsSeen += 1;
    if (entry.event_type === 'ACTION_COMPLETED') actionsCompleted += 1;
    if (entry.event_type === 'HUMAN_DECISION_RECORDED') {
      decisionsRecorded.push({
        assuranceId: detailNumber(entry.detail, 'assurance_id'),
        decision: detailString(entry.detail, 'decision') ?? 'recorded',
        actorId: detailString(entry.detail, 'actor_id'),
      });
    }
  }

  const last = statesReached[statesReached.length - 1];
  return {
    state: (last?.state as IncidentState) ?? null,
    statesReached,
    decisionsRecorded,
    actionsCompleted,
    evaluationsSeen,
    cursorAt: upTo[upTo.length - 1]?.occurred_at ?? null,
  };
}

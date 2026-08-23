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

import type { IncidentState, ReplayFrame, TimelineEntry } from '@/api/types';

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

export function applyFilters(entries: TimelineEntry[], filters: ReplayFilters): TimelineEntry[] {
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
export function reconstruct(entries: TimelineEntry[], cursor: number): ReconstructedState {
  const upTo = entries.slice(0, Math.max(0, Math.min(cursor + 1, entries.length)));
  const statesReached: { state: string; at: string }[] = [];
  const decisionsRecorded: ReconstructedState['decisionsRecorded'] = [];
  let actionsCompleted = 0;
  let evaluationsSeen = 0;

  for (const entry of upTo) {
    if (entry.event_type === 'STATE_CHANGED') {
      const to = detailString(entry.detail, 'to');
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

// ---------------------------------------------------------------------------------------
// Folding the SERVER's replay frames.
//
// `reconstruct` above folds `TimelineEntry` rows and has to read the state out of
// `detail.to`, because the timeline endpoint does not carry it as a field. That worked, but it
// is a client-side derivation of the one thing this screen exists to state.
//
// `GET /incidents/{ref}/replay` and `GET /incident-groups/{ref}/replay` return frames that carry
// `state_before` and `state_after` directly, plus `decision_scope` distinguishing a plan-wide
// signature from a per-action one, and `evidence_refs` per frame. So the fold below reads the
// state instead of inferring it, and there is nothing left to get wrong.
// ---------------------------------------------------------------------------------------

export interface FrameFilters {
  actorKinds: Set<string>;
  stages: Set<string>;
  onlyDecisions: boolean;
  includeBookkeeping: boolean;
  /** Group replay only: narrow to one member incident. Empty means every member. */
  incidentReferences: Set<string>;
}

export const NO_FRAME_FILTERS: FrameFilters = {
  actorKinds: new Set(),
  stages: new Set(),
  onlyDecisions: false,
  includeBookkeeping: false,
  incidentReferences: new Set(),
};

export function applyFrameFilters(frames: ReplayFrame[], filters: FrameFilters): ReplayFrame[] {
  return frames.filter((frame) => {
    if (!filters.includeBookkeeping && BOOKKEEPING_EVENTS.has(frame.event_type)) return false;
    if (filters.onlyDecisions && !DECISION_EVENTS.has(frame.event_type)) return false;
    if (filters.actorKinds.size > 0 && !filters.actorKinds.has(frame.actor_kind)) return false;
    if (filters.stages.size > 0 && !filters.stages.has(frame.stage)) return false;
    if (
      filters.incidentReferences.size > 0 &&
      (frame.incident_reference === null ||
        !filters.incidentReferences.has(frame.incident_reference))
    ) {
      return false;
    }
    return true;
  });
}

export interface FrameFoldResult {
  /** The last `state_after` the frames recorded, read rather than inferred. */
  state: string | null;
  statesReached: { state: string; at: string; incidentReference: string | null }[];
  decisions: {
    actor: string;
    /** `action` or `plan`. Both are a person's act; an auditor needs to tell them apart. */
    scope: string | null;
    assuranceId: number | null;
    planApprovalId: number | null;
    incidentReference: string | null;
    at: string;
  }[];
  actionsCompleted: number;
  evaluationsSeen: number;
  evidenceRefs: string[];
  /** Group replay interleaves members, so "which incidents does this fold cover" is a real question. */
  incidentsTouched: string[];
  cursorAt: string | null;
}

/**
 * Fold frames [0, cursor].
 *
 * Monotonic by construction: adding a frame can only add to what is known, and nothing is
 * interpolated between frames because nothing was recorded between them.
 *
 * For a group replay the frames of eight incidents are interleaved by time, so `state` is the last
 * transition *anywhere in the group* — which is why `statesReached` carries the incident reference
 * alongside each transition rather than presenting one member's state as the group's.
 */
export function foldFrames(frames: ReplayFrame[], cursor: number): FrameFoldResult {
  const upTo = frames.slice(0, Math.max(0, Math.min(cursor + 1, frames.length)));
  const statesReached: FrameFoldResult['statesReached'] = [];
  const decisions: FrameFoldResult['decisions'] = [];
  const evidence = new Set<string>();
  const incidents = new Set<string>();
  let actionsCompleted = 0;
  let evaluationsSeen = 0;

  for (const frame of upTo) {
    if (frame.incident_reference) incidents.add(frame.incident_reference);
    for (const ref of frame.evidence_refs ?? []) evidence.add(ref);

    if (frame.state_after && frame.state_after !== frame.state_before) {
      statesReached.push({
        state: frame.state_after,
        at: frame.occurred_at,
        incidentReference: frame.incident_reference,
      });
    }
    if (frame.event_type === 'ASSURANCE_EVALUATED') evaluationsSeen += 1;
    if (frame.event_type === 'ACTION_COMPLETED') actionsCompleted += 1;
    if (frame.human_decision_id !== null || frame.actor_kind === 'human') {
      decisions.push({
        actor: frame.actor,
        scope: frame.decision_scope,
        assuranceId: frame.assurance_id,
        planApprovalId: frame.plan_approval_id,
        incidentReference: frame.incident_reference,
        at: frame.occurred_at,
      });
    }
  }

  return {
    state: statesReached[statesReached.length - 1]?.state ?? null,
    statesReached,
    decisions,
    actionsCompleted,
    evaluationsSeen,
    evidenceRefs: [...evidence].sort(),
    incidentsTouched: [...incidents].sort(),
    cursorAt: upTo[upTo.length - 1]?.occurred_at ?? null,
  };
}

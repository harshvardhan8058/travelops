/**
 * Folding the server's replay frames into the state they recorded. Pure, and a read over immutable
 * records — not a model narrating history and not a re-run of any logic.
 *
 * Scrubbing is by FRAME INDEX rather than wall-clock time. An injected scenario carries the
 * disruption's own `opened_at` while the transitions carry the time the workflow ran, so a
 * time-based scrubber would stall across a 27-hour gap that never happened. Index-based scrubbing
 * has no such failure mode.
 *
 * There was a second fold here that operated on `TimelineEntry` rows and had to infer the state
 * from `detail.to`. It is gone. Two folds answering "what state held at the cursor" by two
 * mechanisms could disagree — and they did: the inferring one recorded a transition for every
 * `STATE_CHANGED` whether or not the state changed, while the one below reads `state_after` and
 * suppresses a no-op. Keeping both meant a passing test vouching for the weaker semantics.
 *
 * Owner: Stream D.
 */

import type { ReplayFrame } from '@/api/types';

/** Bookkeeping frames the engine writes for idempotency. Hidden by default, counted openly. */
export const BOOKKEEPING_EVENTS = new Set(['WORKFLOW_RUN_REQUESTED']);

/** The subset a reviewer means by "only decisions". */
export const DECISION_EVENTS = new Set([
  'ASSURANCE_EVALUATED',
  'HUMAN_DECISION_RECORDED',
  'STATE_CHANGED',
]);

/**
 * Resolves the scrub position against the currently visible frames.
 *
 * `null` means "the latest frame". Replay opens there deliberately: a reviewer arrives to see what
 * was recorded and scrubs *backwards* to find the moment they care about. Opening at index 0 folds a
 * single frame, which reports an empty state and reads as missing data rather than as an un-scrubbed
 * timeline. Filters change `count` underneath a held cursor, so this also clamps.
 */
export function resolveCursor(cursor: number | null, count: number): number {
  const lastIndex = Math.max(0, count - 1);
  if (cursor === null) return lastIndex;
  return Math.min(Math.max(0, cursor), lastIndex);
}

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

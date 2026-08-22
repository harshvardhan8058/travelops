import { describe, expect, it } from 'vitest';

import type { TimelineEntry } from '@/api/types';
import { applyFilters, NO_FILTERS, reconstruct, resolveCursor } from './replayState';

function entry(overrides: Partial<TimelineEntry> = {}): TimelineEntry {
  return {
    id: 1,
    occurred_at: '2026-08-21T10:00:00Z',
    stage: 'assure',
    actor: 'orchestrator',
    actor_kind: 'orchestrator',
    event_type: 'STATE_CHANGED',
    summary: 'moved on',
    detail: null,
    ...overrides,
  };
}

const entries: TimelineEntry[] = [
  entry({
    id: 1,
    event_type: 'INCIDENT_OPENED',
    stage: 'detect',
    occurred_at: '2026-08-21T10:00:00Z',
  }),
  entry({
    id: 2,
    event_type: 'STATE_CHANGED',
    detail: { to: 'assessing' },
    occurred_at: '2026-08-21T10:00:05Z',
  }),
  entry({ id: 3, event_type: 'ASSURANCE_EVALUATED', occurred_at: '2026-08-21T10:00:07Z' }),
  entry({
    id: 4,
    event_type: 'HUMAN_DECISION_RECORDED',
    actor: 'human',
    actor_kind: 'human',
    detail: { assurance_id: 3, decision: 'approved', actor_id: 'operator-1' },
    occurred_at: '2026-08-21T10:00:09Z',
  }),
  entry({
    id: 5,
    event_type: 'ACTION_COMPLETED',
    stage: 'execute',
    occurred_at: '2026-08-21T10:00:11Z',
  }),
  entry({
    id: 6,
    event_type: 'STATE_CHANGED',
    detail: { to: 'resolved' },
    occurred_at: '2026-08-21T10:00:13Z',
  }),
  entry({
    id: 7,
    event_type: 'WORKFLOW_RUN_REQUESTED',
    stage: 'run',
    occurred_at: '2026-08-21T10:00:15Z',
  }),
];

describe('applyFilters', () => {
  it('hides bookkeeping entries by default and can show them', () => {
    expect(applyFilters(entries, NO_FILTERS)).toHaveLength(6);
    expect(applyFilters(entries, { ...NO_FILTERS, includeBookkeeping: true })).toHaveLength(7);
  });

  it('only-decisions keeps gate, human and state records', () => {
    const kept = applyFilters(entries, { ...NO_FILTERS, onlyDecisions: true });
    expect(kept.map((e) => e.event_type)).toEqual([
      'STATE_CHANGED',
      'ASSURANCE_EVALUATED',
      'HUMAN_DECISION_RECORDED',
      'STATE_CHANGED',
    ]);
  });

  it('filters by actor kind and stage without dropping anything silently', () => {
    const humans = applyFilters(entries, { ...NO_FILTERS, actorKinds: new Set(['human']) });
    expect(humans).toHaveLength(1);
    const execute = applyFilters(entries, { ...NO_FILTERS, stages: new Set(['execute']) });
    expect(execute).toHaveLength(1);
  });
});

describe('resolveCursor', () => {
  it('opens at the latest record so the fold covers the whole recorded history', () => {
    const visible = applyFilters(entries, NO_FILTERS);
    const at = resolveCursor(null, visible.length);
    expect(at).toBe(visible.length - 1);
    expect(reconstruct(visible, at).state).toBe('resolved');
    expect(reconstruct(visible, at).decisionsRecorded).toHaveLength(1);
  });

  it('clamps a held cursor when a filter shrinks the record set', () => {
    expect(resolveCursor(5, 3)).toBe(2);
    expect(resolveCursor(-1, 3)).toBe(0);
  });

  it('stays at 0 when there is nothing to scrub', () => {
    expect(resolveCursor(null, 0)).toBe(0);
    expect(resolveCursor(4, 0)).toBe(0);
  });
});

describe('reconstruct', () => {
  const visible = applyFilters(entries, NO_FILTERS);

  it('is monotonic: knowledge only grows as the cursor advances', () => {
    let previousStates = -1;
    for (let cursor = 0; cursor < visible.length; cursor += 1) {
      const state = reconstruct(visible, cursor);
      expect(state.statesReached.length).toBeGreaterThanOrEqual(previousStates);
      previousStates = state.statesReached.length;
    }
  });

  it('folds STATE_CHANGED details into the state that held at the cursor', () => {
    expect(reconstruct(visible, 0).state).toBe('detected');
    expect(reconstruct(visible, 1).state).toBe('assessing');
    expect(reconstruct(visible, visible.length - 1).state).toBe('resolved');
  });

  it('counts evaluations, actions and decisions from records only', () => {
    const final = reconstruct(visible, visible.length - 1);
    expect(final.evaluationsSeen).toBe(1);
    expect(final.actionsCompleted).toBe(1);
    expect(final.decisionsRecorded).toEqual([
      { assuranceId: 3, decision: 'approved', actorId: 'operator-1' },
    ]);
  });

  it('does not interpolate: a cursor before a transition does not know about it', () => {
    expect(reconstruct(visible, 0).state).not.toBe('resolved');
    expect(reconstruct(visible, 2).decisionsRecorded).toHaveLength(0);
  });

  it('handles an empty record set without inventing a state', () => {
    const empty = reconstruct([], 0);
    expect(empty.state).toBeNull();
    expect(empty.cursorAt).toBeNull();
  });
});

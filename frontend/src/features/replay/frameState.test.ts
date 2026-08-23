import { describe, expect, it } from 'vitest';

import type { ReplayFrame } from '@/api/types';
import { applyFrameFilters, foldFrames, NO_FRAME_FILTERS } from './replayState';

function frame(overrides: Partial<ReplayFrame> = {}): ReplayFrame {
  return {
    sequence: 1,
    occurred_at: '2026-08-21T10:00:00Z',
    stage: 'assure',
    actor: 'orchestrator',
    actor_kind: 'orchestrator',
    event_type: 'STATE_CHANGED',
    summary: 'moved on',
    state_before: null,
    state_after: null,
    incident_reference: 'INC-2026-0820-VOBL-01',
    evidence_refs: [],
    assurance_id: null,
    human_decision_id: null,
    decision_scope: null,
    plan_approval_id: null,
    detail: {},
    ...overrides,
  };
}

const frames: ReplayFrame[] = [
  frame({
    sequence: 1,
    event_type: 'INCIDENT_OPENED',
    state_before: null,
    state_after: 'detected',
  }),
  frame({
    sequence: 2,
    state_before: 'detected',
    state_after: 'assessing',
    evidence_refs: ['metar:VOBL:1'],
  }),
  frame({ sequence: 3, event_type: 'ASSURANCE_EVALUATED', assurance_id: 7 }),
  frame({
    sequence: 4,
    event_type: 'HUMAN_DECISION_RECORDED',
    actor: 'duty-manager-2',
    actor_kind: 'human',
    assurance_id: 7,
    human_decision_id: 3,
    decision_scope: 'action',
  }),
  frame({
    sequence: 5,
    event_type: 'ACTION_COMPLETED',
    evidence_refs: ['hotel:4', 'metar:VOBL:1'],
  }),
  frame({ sequence: 6, state_before: 'executing', state_after: 'resolved' }),
  frame({ sequence: 7, event_type: 'WORKFLOW_RUN_REQUESTED', stage: 'run' }),
];

describe('applyFrameFilters', () => {
  it('hides bookkeeping frames by default and can show them', () => {
    expect(applyFrameFilters(frames, NO_FRAME_FILTERS)).toHaveLength(6);
    expect(
      applyFrameFilters(frames, { ...NO_FRAME_FILTERS, includeBookkeeping: true }),
    ).toHaveLength(7);
  });

  it('narrows a group replay to one member incident', () => {
    const mixed = [
      ...frames,
      frame({ sequence: 8, incident_reference: 'INC-2026-0820-VAAH-01', state_after: 'resolved' }),
    ];
    const only = applyFrameFilters(mixed, {
      ...NO_FRAME_FILTERS,
      incidentReferences: new Set(['INC-2026-0820-VAAH-01']),
    });
    expect(only).toHaveLength(1);
    expect(only[0]?.incident_reference).toBe('INC-2026-0820-VAAH-01');
  });

  it('drops a frame with no incident reference when a member filter is set', () => {
    const orphan = [frame({ sequence: 9, incident_reference: null })];
    expect(
      applyFrameFilters(orphan, {
        ...NO_FRAME_FILTERS,
        incidentReferences: new Set(['INC-2026-0820-VOBL-01']),
      }),
    ).toHaveLength(0);
  });
});

describe('foldFrames', () => {
  const visible = applyFrameFilters(frames, NO_FRAME_FILTERS);

  it('reads state_after rather than inferring a state', () => {
    expect(foldFrames(visible, 0).state).toBe('detected');
    expect(foldFrames(visible, 1).state).toBe('assessing');
    expect(foldFrames(visible, visible.length - 1).state).toBe('resolved');
  });

  it('counts a transition only when the state actually changed', () => {
    // Frame 3 is an evaluation with no transition, so the count must not grow across it.
    expect(foldFrames(visible, 1).statesReached).toHaveLength(2);
    expect(foldFrames(visible, 2).statesReached).toHaveLength(2);
  });

  it('is monotonic: knowledge only grows as the cursor advances', () => {
    let previous = -1;
    for (let cursor = 0; cursor < visible.length; cursor += 1) {
      const fold = foldFrames(visible, cursor);
      expect(fold.statesReached.length).toBeGreaterThanOrEqual(previous);
      previous = fold.statesReached.length;
    }
  });

  it('records a human decision with its scope, so plan and action reads differently', () => {
    const fold = foldFrames(visible, visible.length - 1);
    expect(fold.decisions).toEqual([
      {
        actor: 'duty-manager-2',
        scope: 'action',
        assuranceId: 7,
        planApprovalId: null,
        incidentReference: 'INC-2026-0820-VOBL-01',
        at: '2026-08-21T10:00:00Z',
      },
    ]);
  });

  it('treats a plan-scoped decision as a human act too', () => {
    const planScoped = [
      frame({
        sequence: 1,
        actor: 'ops-lead-7',
        actor_kind: 'human',
        event_type: 'HUMAN_DECISION_RECORDED',
        decision_scope: 'plan',
        plan_approval_id: 11,
      }),
    ];
    const fold = foldFrames(planScoped, 0);
    expect(fold.decisions[0]?.scope).toBe('plan');
    expect(fold.decisions[0]?.planApprovalId).toBe(11);
  });

  it('unions evidence refs without repeating one seen twice', () => {
    const fold = foldFrames(visible, visible.length - 1);
    expect(fold.evidenceRefs).toEqual(['hotel:4', 'metar:VOBL:1']);
  });

  it('reports every member incident the fold covers', () => {
    const mixed = [
      frame({ sequence: 1, state_after: 'detected' }),
      frame({ sequence: 2, incident_reference: 'INC-2026-0820-VAAH-01', state_after: 'assessing' }),
    ];
    expect(foldFrames(mixed, 1).incidentsTouched).toEqual([
      'INC-2026-0820-VAAH-01',
      'INC-2026-0820-VOBL-01',
    ]);
  });

  it('does not interpolate: a cursor before a decision does not know about it', () => {
    expect(foldFrames(visible, 2).decisions).toHaveLength(0);
  });

  it('handles an empty frame set without inventing a state', () => {
    const empty = foldFrames([], 0);
    expect(empty.state).toBeNull();
    expect(empty.cursorAt).toBeNull();
    expect(empty.incidentsTouched).toEqual([]);
  });
});

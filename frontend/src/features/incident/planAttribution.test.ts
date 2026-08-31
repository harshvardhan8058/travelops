import { describe, expect, it } from 'vitest';

import type { PlanSummary, TimelineEntry, TimelineResponse } from '@/api/types';
import { isDeterministicGenerator, plannerCandidateAttribution } from './planAttribution';

function entry(overrides: Partial<TimelineEntry> = {}): TimelineEntry {
  return {
    id: 1,
    occurred_at: '2026-08-20T15:36:00Z',
    stage: 'plan',
    actor: 'orchestrator',
    actor_kind: 'orchestrator',
    event_type: 'PLAN_PROPOSED',
    summary: '5 tasks proposed by the planner agent',
    detail: {
      plan_id: 2,
      generator: 'planner-agent',
      prompt_version: 'planner-v1',
      llm_mode: 'fixture',
    },
    ...overrides,
  };
}

function timeline(entries: TimelineEntry[]): TimelineResponse {
  return { incident_reference: 'INC-1', entries };
}

function plan(overrides: Partial<PlanSummary> = {}): PlanSummary {
  return {
    id: 1,
    generator: 'fallback-playbook',
    prompt_version: null,
    model_self_report: null,
    tasks: [],
    ...overrides,
  };
}

describe('plan attribution', () => {
  it('keeps the deterministic fallback first-class', () => {
    expect(isDeterministicGenerator('fallback-playbook')).toBe(true);
    expect(isDeterministicGenerator('fallback-playbook · deterministic')).toBe(true);
    expect(isDeterministicGenerator('planner-agent')).toBe(false);
  });

  it('reads fixture-replayed planner output without calling the candidate endpoint', () => {
    const attribution = plannerCandidateAttribution(timeline([entry()]), plan());
    expect(attribution).toMatchObject({
      planId: 2,
      generator: 'planner-agent',
      isPlanOfRecord: false,
      sourceLabel: 'fixture-replayed model output',
    });
  });

  it('uses the incident plan id as the plan-of-record authority', () => {
    expect(plannerCandidateAttribution(timeline([entry()]), plan({ id: 2 }))?.isPlanOfRecord).toBe(
      true,
    );
    expect(plannerCandidateAttribution(timeline([entry()]), plan({ id: 1 }))?.isPlanOfRecord).toBe(
      false,
    );
  });

  it('ignores deterministic PLAN_PROPOSED records and malformed detail', () => {
    const fallback = entry({ detail: { plan_id: 1, generator: 'fallback-playbook' } });
    expect(plannerCandidateAttribution(timeline([fallback]), plan())).toBeNull();
    expect(
      plannerCandidateAttribution(
        timeline([entry({ detail: { generator: 'planner-agent' } })]),
        plan(),
      ),
    ).toBeNull();
  });
});

import { describe, expect, it } from 'vitest';

import type { PlanSummary, TimelineEntry, TimelineResponse } from '@/api/types';
import {
  candidateMismatchLabel,
  isDeterministicGenerator,
  planGeneratorKind,
  plannerCandidateAttribution,
  plannerUnavailableAttribution,
} from './planAttribution';

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
    expect(isDeterministicGenerator('fallback-playbook · deterministic')).toBe(false);
    expect(isDeterministicGenerator('FALLBACK-PLAYBOOK')).toBe(false);
    expect(isDeterministicGenerator(' fallback-playbook')).toBe(false);
    expect(planGeneratorKind('fallback-playbook')).toBe('deterministic_fallback');
    expect(planGeneratorKind('fallback-playbook · deterministic')).toBe('unclassified');
  });

  it('calls only recognised recorded generators model-authored', () => {
    expect(planGeneratorKind('planner-agent')).toBe('model_authored');
    expect(planGeneratorKind('PLANNER-AGENT')).toBe('unclassified');
    expect(planGeneratorKind('planner-agent ')).toBe('unclassified');
    expect(planGeneratorKind('groq:llama-3.3-70b')).toBe('unclassified');
    expect(planGeneratorKind('openrouter:openai/gpt-oss-120b')).toBe('unclassified');
    expect(planGeneratorKind('future-agent')).toBe('unclassified');
    expect(planGeneratorKind('')).toBe('unclassified');
  });

  it('keeps fallback-like unknown tokens unclassified', () => {
    for (const generator of [
      'not-a-fallback',
      'future-playbook-reviewer',
      'nondeterministic-agent',
    ]) {
      expect(planGeneratorKind(generator)).toBe('unclassified');
    }
  });

  it('describes candidate mismatch from the recorded plan-of-record generator', () => {
    expect(candidateMismatchLabel(plan())).toBe(
      'not selected; the deterministic playbook remains the plan of record',
    );
    expect(candidateMismatchLabel(plan({ generator: 'planner-agent' }))).toBe(
      'not selected; a different model-authored plan is the plan of record',
    );
    expect(candidateMismatchLabel(plan({ generator: 'future-agent' }))).toContain(
      'generator is unclassified',
    );
  });

  it('reads fixture-replayed planner output without calling the candidate endpoint', () => {
    const attribution = plannerCandidateAttribution(timeline([entry()]), plan(), 'fixture');
    expect(attribution).toMatchObject({
      planId: 2,
      generator: 'planner-agent',
      isPlanOfRecord: false,
      sourceLabel: 'fixture-replayed model output',
      sourceVerified: true,
    });
    expect(
      plannerCandidateAttribution(
        timeline([entry({ detail: { plan_id: 2, generator: 'planner-agent', llm_mode: 'live' } })]),
        plan(),
        'fixture',
      ),
    ).toMatchObject({
      sourceLabel: 'source mode mismatch; recorded live, effective fixture',
      sourceVerified: false,
    });
  });

  it('uses only the incident plan id as the plan-of-record authority', () => {
    expect(
      plannerCandidateAttribution(timeline([entry()]), plan({ id: 2 }), 'fixture')?.isPlanOfRecord,
    ).toBe(true);
    expect(
      plannerCandidateAttribution(
        timeline([entry()]),
        plan({ id: 1, generator: 'planner-agent' }),
        'fixture',
      )?.isPlanOfRecord,
    ).toBe(false);
  });

  it('ignores deterministic, unknown, and malformed PLAN_PROPOSED records', () => {
    const fallback = entry({ detail: { plan_id: 1, generator: 'fallback-playbook' } });
    const unknown = entry({ detail: { plan_id: 3, generator: 'future-agent' } });
    expect(plannerCandidateAttribution(timeline([fallback]), plan(), 'fixture')).toBeNull();
    expect(plannerCandidateAttribution(timeline([unknown]), plan(), 'fixture')).toBeNull();
    for (const planId of [0, -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(
        plannerCandidateAttribution(
          timeline([
            entry({
              detail: { plan_id: planId, generator: 'planner-agent', llm_mode: 'fixture' },
            }),
          ]),
          plan(),
          'fixture',
        ),
      ).toBeNull();
    }
    expect(
      plannerCandidateAttribution(
        timeline([entry({ detail: { generator: 'planner-agent' } })]),
        plan(),
        'fixture',
      ),
    ).toBeNull();
  });

  it('returns the latest recorded planner-unavailable reason verbatim', () => {
    const first = entry({
      id: 4,
      event_type: 'PLANNER_AGENT_UNAVAILABLE',
      detail: { reason: 'provider timed out' },
    });
    const latest = entry({
      id: 5,
      occurred_at: '2026-08-20T15:37:00Z',
      event_type: 'PLANNER_AGENT_UNAVAILABLE',
      detail: { reason: 'budget exhausted' },
    });
    expect(plannerUnavailableAttribution(timeline([first, latest]))).toEqual({
      eventId: 5,
      occurredAt: '2026-08-20T15:37:00Z',
      reason: 'budget exhausted',
    });
  });

  it('does not invent an unavailable reason for absent or malformed detail', () => {
    expect(
      plannerUnavailableAttribution(
        timeline([
          entry({ event_type: 'PLANNER_AGENT_UNAVAILABLE', detail: { reason: '   ' } }),
          entry({ id: 2, event_type: 'PLANNER_AGENT_UNAVAILABLE', detail: { reason: 503 } }),
        ]),
      ),
    ).toBeNull();
    expect(plannerUnavailableAttribution(timeline([]))).toBeNull();
  });
});

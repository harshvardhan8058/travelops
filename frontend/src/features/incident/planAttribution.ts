import type { PlanSummary, TimelineResponse } from '@/api/types';

/** Generator classification is based on the recorded token, never on display prose. */
export function isDeterministicGenerator(generator: string): boolean {
  return /fallback|playbook|deterministic/i.test(generator);
}

export interface PlannerCandidateAttribution {
  planId: number;
  generator: string;
  promptVersion: string | null;
  isPlanOfRecord: boolean;
  sourceLabel: string;
}

/**
 * Read planner attribution from the append-only timeline rather than the candidate-list endpoint.
 *
 * `GET /incidents/{ref}/plans` currently materialises deterministic comparison variants, so it is
 * not a passive read. `PLAN_PROPOSED` is already the recorded fact this panel needs: generator,
 * plan id, prompt version, and the LLM mode that produced it. The incident's own `plan` field is the
 * backend's plan of record; matching ids is the only way this helper calls the planner plan current.
 */
export function plannerCandidateAttribution(
  timeline: TimelineResponse | undefined,
  planOfRecord: PlanSummary | null,
): PlannerCandidateAttribution | null {
  const entry = [...(timeline?.entries ?? [])].reverse().find((candidate) => {
    if (candidate.event_type !== 'PLAN_PROPOSED') return false;
    const generator = candidate.detail?.generator;
    return typeof generator === 'string' && !isDeterministicGenerator(generator);
  });
  if (!entry) return null;

  const detail = entry.detail ?? {};
  const planId = detail.plan_id;
  const generator = detail.generator;
  if (typeof planId !== 'number' || typeof generator !== 'string') return null;

  const promptVersion = typeof detail.prompt_version === 'string' ? detail.prompt_version : null;
  const llmMode = detail.llm_mode;
  const sourceLabel =
    llmMode === 'live'
      ? 'live model output'
      : llmMode === 'fixture'
        ? 'fixture-replayed model output'
        : llmMode === 'off'
          ? 'model mode off; recorded candidate retained'
          : 'recorded model output; source mode unavailable';

  return {
    planId,
    generator,
    promptVersion,
    isPlanOfRecord: planOfRecord?.id === planId,
    sourceLabel,
  };
}

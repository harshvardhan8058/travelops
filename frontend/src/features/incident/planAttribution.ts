import type { PlanSummary, TimelineResponse } from '@/api/types';

export type PlanGeneratorKind = 'deterministic_fallback' | 'model_authored' | 'unclassified';

/** Generator classification is based on the recorded token, never on display prose. */
export function isDeterministicGenerator(generator: string): boolean {
  return generator === 'fallback-playbook';
}

/**
 * Recognise only generator tokens this repository records with model authorship semantics.
 * Everything else stays unclassified rather than becoming model-authored merely because it is not
 * a known fallback.
 *
 * This is now the FALLBACK path. Prefer `planAuthorship`, which reads the server's own decision:
 * classifying a generator string in the browser is what made the console report "unclassified
 * generator" for the committed fixture's `fallback-playbook · deterministic` — a plan whose
 * authorship was never in any doubt on the server.
 */
export function planGeneratorKind(generator: string): PlanGeneratorKind {
  if (isDeterministicGenerator(generator)) return 'deterministic_fallback';
  if (generator === 'planner-agent') return 'model_authored';
  return 'unclassified';
}

/**
 * Authorship of a plan: the server's answer when it gave one, this file's guess when it did not.
 *
 * `authored_by` is derived on the server by the same rule the assurance gate applies, so when it
 * is present there is nothing left to infer. The token fallback exists only so an older server —
 * or a committed fixture written before the field existed — still renders something truthful
 * rather than an empty chip.
 */
export function planAuthorship(plan: {
  generator: string;
  authored_by?: 'deterministic' | 'model';
}): PlanGeneratorKind {
  if (plan.authored_by === 'deterministic') return 'deterministic_fallback';
  if (plan.authored_by === 'model') return 'model_authored';
  return planGeneratorKind(plan.generator);
}

/** Explain an ID mismatch without inferring the current plan's authorship. */
export function candidateMismatchLabel(planOfRecord: PlanSummary): string {
  const kind = planAuthorship(planOfRecord);
  if (kind === 'deterministic_fallback') {
    return 'not selected; the deterministic playbook remains the plan of record';
  }
  if (kind === 'model_authored') {
    return 'not selected; a different model-authored plan is the plan of record';
  }
  return 'not selected; the incident endpoint plan remains the plan of record and its generator is unclassified';
}

export interface PlannerCandidateAttribution {
  planId: number;
  generator: string;
  promptVersion: string | null;
  /**
   * Which endpoint actually answered — `openrouter:openai/gpt-oss-120b`, or `fixture:planner` for
   * a replay.
   *
   * `null` when the recorded event predates this field. Rendered as "unavailable", never guessed:
   * the plan's own `generator` names the agent, not the vendor, and inferring a provider from an
   * agent name would be exactly the kind of invention this screen exists to avoid.
   */
  transportGenerator: string | null;
  isPlanOfRecord: boolean;
  sourceLabel: string;
  sourceVerified: boolean;
}

export interface PlannerUnavailableAttribution {
  eventId: number;
  occurredAt: string;
  reason: string;
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
  effectiveLlmMode: 'live' | 'fixture' | 'off' | undefined,
): PlannerCandidateAttribution | null {
  const entry = [...(timeline?.entries ?? [])].reverse().find((candidate) => {
    if (candidate.event_type !== 'PLAN_PROPOSED') return false;
    const generator = candidate.detail?.generator;
    return typeof generator === 'string' && planGeneratorKind(generator) === 'model_authored';
  });
  if (!entry) return null;

  const detail = entry.detail ?? {};
  const planId = detail.plan_id;
  const generator = detail.generator;
  if (
    typeof planId !== 'number' ||
    !Number.isSafeInteger(planId) ||
    planId <= 0 ||
    typeof generator !== 'string'
  ) {
    return null;
  }

  const promptVersion = typeof detail.prompt_version === 'string' ? detail.prompt_version : null;
  const llmMode = detail.llm_mode;
  const recordedMode =
    llmMode === 'live' || llmMode === 'fixture' || llmMode === 'off' ? llmMode : null;
  const sourceVerified = recordedMode !== null && recordedMode === effectiveLlmMode;
  const sourceLabel = !recordedMode
    ? 'recorded model output; source mode unavailable'
    : effectiveLlmMode === undefined
      ? `recorded ${recordedMode} model output; effective mode unavailable`
      : recordedMode !== effectiveLlmMode
        ? `source mode mismatch; recorded ${recordedMode}, effective ${effectiveLlmMode}`
        : recordedMode === 'live'
          ? 'live model output'
          : recordedMode === 'fixture'
            ? 'fixture-replayed model output'
            : 'model mode off; recorded candidate retained';

  const transportGenerator =
    typeof detail.transport_generator === 'string' && detail.transport_generator.length > 0
      ? detail.transport_generator
      : null;

  return {
    planId,
    generator,
    promptVersion,
    transportGenerator,
    isPlanOfRecord: planOfRecord?.id === planId,
    sourceLabel,
    sourceVerified,
  };
}

/** Latest timeline-recorded planner-unavailable reason, returned verbatim and never inferred. */
export function plannerUnavailableAttribution(
  timeline: TimelineResponse | undefined,
): PlannerUnavailableAttribution | null {
  const entry = [...(timeline?.entries ?? [])].reverse().find((candidate) => {
    if (candidate.event_type !== 'PLANNER_AGENT_UNAVAILABLE') return false;
    const reason = candidate.detail?.reason;
    return typeof reason === 'string' && reason.trim().length > 0;
  });
  if (!entry) return null;

  return {
    eventId: entry.id,
    occurredAt: entry.occurred_at,
    reason: entry.detail?.reason as string,
  };
}

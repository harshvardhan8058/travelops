/**
 * Stable reason codes the UI maps to copy.
 *
 * `backend/app/orchestrator/dispatch.py` refuses an unimplemented action with status
 * `needs_human` and a reason that BEGINS with a stable code:
 *
 *   SERVICE_NOT_IMPLEMENTED: no deterministic service is registered for 'check_connections'…
 *
 * That module's own comment says the code is stable "so the UI maps it to copy rather than
 * parsing the message", which is what this does. Matching a documented prefix is not the same
 * as scraping prose — but `ActionSummary` would be better carrying `reason_code` as a field,
 * and that is raised with Stream A rather than worked around here.
 *
 * Why this matters on screen: a refused action leaves the TASK in `needs_human` while its
 * gate evaluation says `execute_flagged`. Without an explanation the panel looks contradictory
 * — the gate approved it, so why is it waiting? The answer is that nothing executed it, and
 * that is a missing service rather than a decision an operator can make.
 *
 * Owner: Stream D.
 */

export const SERVICE_NOT_IMPLEMENTED = 'SERVICE_NOT_IMPLEMENTED';

export interface RefusalInfo {
  code: string;
  /** Designed copy, not the raw message. */
  headline: string;
  detail: string;
  /** True when no operator decision can resolve this. */
  operatorCannotResolve: boolean;
}

/**
 * Recognises a refusal from an action's reason. Returns null when the reason carries no known
 * code, in which case callers render the reason verbatim rather than guessing at it.
 */
export function refusalFor(reason: string | undefined | null): RefusalInfo | null {
  if (!reason) return null;

  if (reason.startsWith(SERVICE_NOT_IMPLEMENTED)) {
    return {
      code: SERVICE_NOT_IMPLEMENTED,
      headline: 'No service registered for this action yet',
      detail:
        'The gate authorised this task, but no deterministic service exists to carry it out, so execution was refused rather than reported as successful. Approving it would not help: there is nothing to run. The refusal disappears when the owning service is registered.',
      operatorCannotResolve: true,
    };
  }

  return null;
}

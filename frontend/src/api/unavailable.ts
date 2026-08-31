/**
 * Telling "this does not exist yet" apart from "this failed".
 *
 * Several endpoints answer 404 for a state that is entirely normal early in a disruption. Measured
 * against the live API on a cascade that has been injected but not advanced:
 *
 *   GET /incident-groups/{ref}/assurance
 *     404 ENTITY_NOT_FOUND  "no member incident in this group has a plan yet"
 *     details.resolution    "run the cascade to planning first"
 *
 *   GET /incidents/{ref}/plans            (and /plans/comparison, which proposes candidates too)
 *     404 ENTITY_NOT_FOUND  "this incident has no plan to vary"
 *     details.resolution    "run the incident to planning first"
 *
 * Both are correct answers. A group with no plan has no plan assurance, and there is nothing
 * truthful for those endpoints to return instead — an empty body would invite the console to render
 * "0 checks, decision none", which reads as an assessment that found nothing rather than an
 * assessment that has not happened.
 *
 * **`details.resolution` is the discriminator, not the status code.** The same endpoints also answer
 * 404 ENTITY_NOT_FOUND for a reference that genuinely does not exist — `_load_incident` raises
 * `"incident not found"` with `details.incident`, and the group resolver raises
 * `"disruption group not found"` with `details.group`. Neither carries a `resolution`, because there
 * is no next step that would make a mistyped reference resolve. So a missing entity stays an error
 * with a code and a correlation id, and only a state the backend told us how to advance becomes an
 * empty state. Branching on the status alone would swallow a real miss.
 *
 * Nothing here hides a 404. The code, the message and the correlation id stay available to the
 * caller, and the screens render the server's own sentence rather than one written here.
 *
 * Owner: Stream D.
 */

import { ApiError } from './client';

export interface DataUnavailable {
  /** The server's stable error code, kept so the screen can show it rather than paraphrase. */
  code: string;
  /** The server's own message. */
  message: string;
  /** The server's next step. Non-empty by construction: it is what makes this an empty state. */
  resolution: string;
  correlationId: string | null;
}

/**
 * Classifies an error as data that does not exist yet, or returns `null`.
 *
 * `null` means "treat this as a failure" — including for a 404 whose details carry no resolution.
 */
export function dataUnavailable(error: unknown): DataUnavailable | null {
  if (!(error instanceof ApiError)) return null;
  if (error.status !== 404 || error.code !== 'ENTITY_NOT_FOUND') return null;

  const resolution = error.details.resolution;
  if (typeof resolution !== 'string' || resolution.trim() === '') return null;

  return {
    code: error.code,
    message: error.message,
    resolution: resolution.trim(),
    correlationId: error.correlationId,
  };
}

/**
 * For a screen reading several endpoints at once.
 *
 * A genuine failure anywhere wins: if one query is merely waiting for a plan and another actually
 * broke, the screen must report the breakage rather than the wait. Returns the first not-yet state
 * only when every error is one.
 */
export function resolveUnavailable(
  errors: readonly unknown[],
): { unavailable: DataUnavailable } | { failure: unknown } | null {
  const present = errors.filter((error) => Boolean(error));
  if (present.length === 0) return null;

  const failure = present.find((error) => dataUnavailable(error) === null);
  if (failure) return { failure };

  const first = dataUnavailable(present[0]);
  // Unreachable while every error classified, but typed rather than asserted.
  return first ? { unavailable: first } : { failure: present[0] };
}

/**
 * Whether react-query should retry.
 *
 * A documented not-yet-planned 404 is not transient: retrying it three times is three more requests
 * the console already knows the answer to, and it holds the screen on a spinner before the empty
 * state an operator could have acted on immediately. `AgentConsole` already sets `retry: false` on
 * its plans query for this reason; this puts the rule in one place.
 */
export function retryUnlessUnavailable(failureCount: number, error: unknown): boolean {
  if (dataUnavailable(error) !== null) return false;
  return failureCount < 2;
}

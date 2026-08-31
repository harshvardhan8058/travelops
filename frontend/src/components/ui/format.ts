/**
 * Time and value formatting, in one place.
 *
 * Every screen that shows a timestamp had grown its own formatter: `timeOf` in the command
 * centre's zones, `clockOf` in the passenger view, `formatDuration` in the recovery workspace,
 * a private `formatUtc` inside `primitives.tsx`, and raw `.slice(11, 19)` calls in the timeline,
 * replay, plan column, actions strip, evidence column and provenance ledger. Seven
 * implementations of "show me the time" is how one screen ends up showing seconds while the
 * screen beside it shows minutes, and how a value that is absent renders as `Invalid Date`
 * on one surface and blank on another.
 *
 * Two rules hold throughout:
 *
 *   1. **Everything is UTC.** The whole product records and reasons in UTC — the top bar clock,
 *      `reached_at`, `observed_at`, every replay frame. Rendering one surface in the viewer's
 *      local zone would silently shift an audit trail by hours depending on who opened it.
 *      So these functions never call a local-time getter.
 *   2. **An unparseable or absent input returns `null`, never a guess.** Callers render an
 *      explicit absence (`Absent`) instead of `—` in one place and `Invalid Date` in another.
 *      An absent timestamp is a fact about the record and is shown as one.
 *
 * Owner: Stream D.
 */

/**
 * The ISO-8601 instant these helpers agree to work from.
 *
 * `Date` is used only to validate and normalise; the substring arithmetic below runs on the
 * canonical `YYYY-MM-DDTHH:mm:ss.sssZ` form that `toISOString()` guarantees, so there is no
 * timezone in play at all.
 */
function isoOf(value: string | null | undefined): string | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = new Date(value);
  const time = parsed.getTime();
  if (Number.isNaN(time)) return null;
  return parsed.toISOString();
}

/** `HH:MM:SS`, the operational default: seconds matter when steps land within a minute. */
export function utcClock(value: string | null | undefined): string | null {
  const iso = isoOf(value);
  return iso === null ? null : iso.slice(11, 19);
}

/**
 * `HH:MM`, for scheduled times.
 *
 * A departure time is published to the minute, so rendering `:00` seconds on it implies a
 * precision the schedule does not have.
 */
export function utcMinute(value: string | null | undefined): string | null {
  const iso = isoOf(value);
  return iso === null ? null : iso.slice(11, 16);
}

/** `YYYY-MM-DD`. */
export function utcDate(value: string | null | undefined): string | null {
  const iso = isoOf(value);
  return iso === null ? null : iso.slice(0, 10);
}

/** `YYYY-MM-DD HH:MM:SSZ` — the full instant, for a record's own identity. */
export function utcStamp(value: string | null | undefined): string | null {
  const iso = isoOf(value);
  return iso === null ? null : `${iso.slice(0, 10)} ${iso.slice(11, 19)}Z`;
}

/**
 * A whole-minute count rendered as a duration.
 *
 * Deliberately not "about an hour": an operations record is read to reconstruct what happened,
 * and a rounded phrase cannot be reconciled against a timestamp. `95` becomes `1h 35m`.
 *
 * Returns `null` for a negative or non-finite input rather than rendering `-1h 0m`, which would
 * present a clock-skew defect as an ordinary duration.
 */
export function durationFromMinutes(minutes: number | null | undefined): string | null {
  if (minutes === null || minutes === undefined) return null;
  if (!Number.isFinite(minutes) || minutes < 0) return null;
  const whole = Math.floor(minutes);
  if (whole < 60) return `${whole}m`;
  const hours = Math.floor(whole / 60);
  return `${hours}h ${whole % 60}m`;
}

/**
 * Elapsed time between two instants, in whole minutes.
 *
 * Returns `null` if either end is missing or unparseable, or if the pair is inverted — an end
 * before its start is a data defect, and presenting it as `0m` would hide it.
 */
export function durationBetween(
  from: string | null | undefined,
  to: string | null | undefined,
): string | null {
  const start = isoOf(from);
  const end = isoOf(to);
  if (start === null || end === null) return null;
  const minutes = (new Date(end).getTime() - new Date(start).getTime()) / 60000;
  if (minutes < 0) return null;
  return durationFromMinutes(minutes);
}

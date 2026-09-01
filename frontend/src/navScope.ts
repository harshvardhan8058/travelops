/**
 * Which incident the shell's incident-scoped nav entries should point at.
 *
 * Seven of the primary nav entries are keyed on an incident — Recovery workspace, Agent
 * operations, Impact, Plan comparison, Policy, Replay, Report — and the rail has nowhere to read a
 * live reference from, so every one of them was a hardcoded `INC-2026-0820-VOBL-01`. The Scenario
 * Center's Restore control deletes that incident, and the rail went on offering all seven links to
 * it: each one led to a screen that immediately issued three or four requests for an entity the
 * operator had just removed, and reported them as load failures.
 *
 * `/flights` is the resolution. It is the only endpoint that publishes the flight -> incident link,
 * it answers 200 unconditionally, and the Command Center already holds it under the same query key,
 * so following it costs nothing on the screen an operator arrives at first.
 *
 * When nothing is open the honest answer is not a different link — it is no link. An entry with no
 * incident to point at is disabled and says why, which is one fewer dead end than a link that
 * 404s on arrival.
 *
 * Owner: Stream D.
 */

import type { FlightRow } from '@/api/types';

/**
 * The incident behind the most delayed flight, or `null` when none is open.
 *
 * Board order alone picked whichever row the server happened to return first, which on the seeded
 * cascade is a 70-minute inbound carrying 12 passengers — while the disruption the demo is about,
 * a 420-minute departure carrying 174, sat six rows below it. The rail is a way in, and a way in
 * that opens the smallest incident in the cascade is a worse default than one that opens the
 * largest.
 *
 * `delay_minutes` is a recorded figure rather than a judgement, and ordering by it is not a claim
 * about severity: severity is the server's word and the screens render it. Board order breaks ties,
 * so the result is deterministic for a dataset where every delay is equal.
 */
export function incidentNavTarget(flights: readonly FlightRow[] | undefined): string | null {
  if (!flights) return null;

  let best: { reference: string; delay: number } | null = null;
  for (const flight of flights) {
    const reference = flight.incident_reference;
    if (typeof reference !== 'string' || reference.length === 0) continue;

    const delay = typeof flight.delay_minutes === 'number' ? flight.delay_minutes : 0;
    // Strictly greater, so the earliest row wins a tie and the order stays stable.
    if (best === null || delay > best.delay) best = { reference, delay };
  }
  return best?.reference ?? null;
}

/** Why an incident-scoped entry is unavailable. Rendered as the entry's tooltip. */
export const NO_INCIDENT_REASON =
  'No incident is open. Start a disruption from the Scenario Center, then this opens the incident being worked.';

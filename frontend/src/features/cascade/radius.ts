/**
 * Blast radius: how far the disruption reaches, and by what mechanism at each hop.
 *
 * Pure and unit-testable. Every figure is either a count the API returned or the length of an
 * array it returned — see §0.4 of the Phase 2 plan for the exhaustive whitelist.
 *
 * The important design decision is what is NOT a hop. Connections (22) and candidate hotels (11)
 * arrive as counts inside `rollups` with no arrays behind them, so they cannot be traversed and
 * are returned as terminals carrying the reason. Drawing them as nodes would be inventing 33
 * relationships that no endpoint asserts.
 *
 * Owner: Stream D.
 */

import type { CrewPairingImpact, IncidentGroupDetail, PairingMechanism } from '@/api/types';

export interface RadiusHop {
  index: number;
  from: string;
  to: string;
  /** Always an array length or an API-returned count. Never computed any other way. */
  count: number;
  countSource: string;
  /** Partition over a returned enum field. Empty for hop 1, which has no mechanism. */
  mechanismCounts: { mechanism: PairingMechanism; count: number }[];
  records: { id: string; label: string; detail?: string }[];
}

export interface RadiusTerminal {
  label: string;
  count: number;
  countSource: string;
  /** Why this cannot be traversed. Rendered on screen, not hidden. */
  reason: string;
}

export interface BlastRadius {
  trigger: { cause: string; airport: string };
  hops: RadiusHop[];
  terminals: RadiusTerminal[];
  unmatched: CrewPairingImpact[];
  /** The backend's own explanation, rendered verbatim. */
  summary?: string;
}

function partitionMechanisms(
  pairings: CrewPairingImpact[],
): { mechanism: PairingMechanism; count: number }[] {
  const counts = new Map<PairingMechanism, number>();
  for (const pairing of pairings) {
    counts.set(pairing.mechanism, (counts.get(pairing.mechanism) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([mechanism, count]) => ({ mechanism, count }))
    .sort((a, b) => b.count - a.count || a.mechanism.localeCompare(b.mechanism));
}

export function buildRadius(group: IncidentGroupDetail): BlastRadius {
  const flights = group.flights as { id?: number; flight_number?: string; route?: string }[];
  const pairings = group.crew_pairings;
  const flightNumbers = new Set(
    flights.map((flight) => flight.flight_number).filter((n): n is string => Boolean(n)),
  );

  const unmatched = pairings.filter((pairing) => !flightNumbers.has(pairing.source_flight));

  const hops: RadiusHop[] = [
    {
      index: 1,
      from: `${group.root_cause} at ${group.airport_icao}`,
      to: 'flights',
      count: flights.length,
      countSource: 'length of flights[]',
      mechanismCounts: [],
      records: flights.map((flight) => ({
        id: String(flight.id ?? flight.flight_number ?? ''),
        label: flight.flight_number ?? 'unknown flight',
        detail: flight.route,
      })),
    },
    {
      index: 2,
      from: 'flights',
      to: 'crew pairings',
      count: pairings.length,
      countSource: 'length of crew_pairings[]',
      mechanismCounts: partitionMechanisms(pairings),
      records: pairings.map((pairing) => ({
        id: pairing.pairing_reference,
        label: `${pairing.pairing_reference} · ${pairing.mechanism}`,
        detail: pairing.detail,
      })),
    },
  ];

  const rollups = group.rollups ?? {};
  const terminals: RadiusTerminal[] = [];
  const asCount = (value: unknown): number | null => (typeof value === 'number' ? value : null);

  const connections = asCount(rollups['connections_at_risk']);
  if (connections !== null) {
    terminals.push({
      label: 'connections at risk',
      count: connections,
      countSource: 'rollups.connections_at_risk',
      reason:
        'A count only. No per-connection records are returned, so this cannot be traversed or listed.',
    });
  }
  const hotels = asCount(rollups['candidate_hotels']);
  if (hotels !== null) {
    terminals.push({
      label: 'candidate hotels',
      count: hotels,
      countSource: 'rollups.candidate_hotels',
      reason:
        'A count only. No per-hotel records are returned, so this cannot be traversed or listed.',
    });
  }

  return {
    trigger: { cause: group.root_cause, airport: group.airport_icao },
    hops,
    terminals,
    unmatched,
    summary: group.why_nine_not_eight,
  };
}

/** Matched pairing count, used by the graph and the radius so they cannot disagree. */
export function matchedPairingCount(group: IncidentGroupDetail): number {
  return buildRadius(group).hops[1]?.count ?? 0;
}

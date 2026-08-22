/**
 * Blast radius: how far the disruption reaches, and by what mechanism at each hop.
 *
 * Pure and unit-testable. **Every figure here is repeated from the server payload; none is
 * computed.** The dimensions come from `blast_radius.dimensions`, each carrying the service that
 * measured it, and the hops are a partition of `graph.edges` by `edge_kind` — which is a grouping
 * of records the API returned, not a derivation of new ones.
 *
 * The earlier version had to describe connections and hotels as untraversable "terminals", because
 * they arrived as bare counts with no records behind them. They are now real nodes with real edges,
 * each naming the action it was read from, so they are ordinary hops. The distinction that remains
 * is completeness: a dimension whose `is_complete` is false is a floor, not a total, and it says so.
 *
 * There is deliberately no confidence figure. Completeness is countable; a confidence percentage
 * would be a probability nothing in this system is calibrated to produce.
 *
 * Owner: Stream D.
 */

import type {
  BlastRadiusDimension,
  CascadeEdgeKind,
  CascadeGraphPayload,
  CrewPairingImpact,
  IncidentGroupDetail,
} from '@/api/types';

export interface RadiusHop {
  kind: CascadeEdgeKind;
  index: number;
  from: string;
  to: string;
  /** Number of edges of this kind. A count of returned records, never anything else. */
  count: number;
  countSource: string;
  /** Partition over the returned `mechanism` field. Empty when the kind carries none. */
  mechanismCounts: { mechanism: string; count: number }[];
  records: { id: string; label: string; detail?: string; derivedFrom: string }[];
}

export interface Radius {
  trigger: { cause: string; airport: string };
  headline: string;
  hops: RadiusHop[];
  dimensions: BlastRadiusDimension[];
  completeness: IncidentGroupDetail['blast_radius']['completeness'];
  gaps: string[];
  /** Pairings the crew table lists that no graph edge reaches. Surfaced, never dropped. */
  unmatched: CrewPairingImpact[];
  /** The backend's own explanation, rendered verbatim. */
  summary?: string;
  snapshotHash: string;
}

const HOP_LABELS: Record<CascadeEdgeKind, { from: string; to: string }> = {
  root_cause: { from: 'trigger', to: 'flights' },
  crew: { from: 'flights', to: 'crew rotations' },
  connection: { from: 'flights', to: 'connections' },
  accommodation: { from: 'flights', to: 'rooms held' },
};

const HOP_ORDER: CascadeEdgeKind[] = ['root_cause', 'crew', 'connection', 'accommodation'];

function partitionMechanisms(
  edges: CascadeGraphPayload['edges'],
): { mechanism: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const edge of edges) {
    if (!edge.mechanism) continue;
    counts.set(edge.mechanism, (counts.get(edge.mechanism) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([mechanism, count]) => ({ mechanism, count }))
    .sort((a, b) => b.count - a.count || a.mechanism.localeCompare(b.mechanism));
}

export function buildRadius(group: IncidentGroupDetail): Radius {
  const graph = group.graph;
  const labelByRef = new Map(graph.nodes.map((node) => [node.ref, node]));

  const hops: RadiusHop[] = HOP_ORDER.filter((kind) =>
    graph.edges.some((edge) => edge.edge_kind === kind),
  ).map((kind, index) => {
    const edges = graph.edges.filter((edge) => edge.edge_kind === kind);
    return {
      kind,
      index: index + 1,
      from:
        kind === 'root_cause'
          ? `${group.root_cause} at ${group.airport_icao}`
          : HOP_LABELS[kind].from,
      to: HOP_LABELS[kind].to,
      count: edges.length,
      countSource: `graph.edges where edge_kind = ${kind}`,
      mechanismCounts: partitionMechanisms(edges),
      records: edges.map((edge) => ({
        id: `${edge.source_ref}->${edge.target_ref}`,
        label: `${labelByRef.get(edge.source_ref)?.label ?? edge.source_ref} -> ${
          labelByRef.get(edge.target_ref)?.label ?? edge.target_ref
        }`,
        detail: edge.detail ?? undefined,
        derivedFrom: edge.derived_from,
      })),
    };
  });

  // A rotation the crew table lists that the graph does not reach. Should be empty; shown when not,
  // because a silently missing edge understates the cascade.
  const reached = new Set(
    graph.edges
      .filter((edge) => edge.edge_kind === 'crew')
      .map((edge) => labelByRef.get(edge.target_ref)?.label)
      .filter((label): label is string => Boolean(label)),
  );
  const unmatched = group.crew_pairings.filter(
    (pairing) => !reached.has(pairing.pairing_reference),
  );

  return {
    trigger: { cause: group.root_cause, airport: group.airport_icao },
    headline: group.blast_radius.headline,
    hops,
    dimensions: group.blast_radius.dimensions,
    completeness: group.blast_radius.completeness,
    gaps: group.blast_radius.gaps,
    unmatched,
    summary: group.why_nine_not_eight,
    snapshotHash: graph.snapshot_hash,
  };
}

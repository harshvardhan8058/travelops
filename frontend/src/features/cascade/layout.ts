/**
 * Deterministic layered layout for the cascade graph. Pure, dependency-free, unit-testable.
 *
 * **This module positions a graph the server projected. It does not build one.** Which rows are
 * related is a statement about the data — it belongs behind the API, where it is computed from
 * recorded actions and predictions and tested against the database. The earlier version assembled
 * nodes and edges here from the `flights` and `crew_pairings` arrays, which meant the topology on
 * screen was a second implementation that nothing verified. Now `nodes` and `edges` arrive from
 * `GET /incident-groups/{id}`, every edge carrying the `action:` or `prediction:` row it was read
 * from, and this file decides only where each one sits.
 *
 * Deterministic matters twice: the same payload must draw the same picture every time a presenter
 * reloads, and a layout that re-flows while being read is unreadable. Ordering is by `ref`, there
 * is no force simulation, no randomness, and no animation of position.
 *
 * Layers come from `depth`, which the server assigns. Within the deepest layer the three
 * consequence kinds — crew, connections, accommodation — sit in their own clusters, because a
 * single row of 37 mixed nodes is a picture nobody can read from the back of a room.
 *
 * Owner: Stream D.
 */

import type { CascadeGraphPayload, GraphEdgeWire, GraphNodeWire } from '@/api/types';

export type CascadeNodeKind = 'event' | 'flight' | 'pairing' | 'booking' | 'hotel';

export interface CascadeNode {
  id: string;
  kind: CascadeNodeKind;
  label: string;
  sublabel?: string;
  /** Drives node size. Passengers for flights; undefined elsewhere. */
  weight?: number;
  /** Operational state, for the border. Never invented: taken from the record. */
  state?: string;
  /** `primary` | `affected_departure` | `affected_arrival`, for flights only. */
  role?: string;
  /** False when the flight is declared but nothing has assessed it. Drawn as a gap. */
  hasEvidence: boolean;
  x: number;
  y: number;
  radius: number;
}

export interface CascadeEdge {
  id: string;
  from: string;
  to: string;
  kind: GraphEdgeWire['edge_kind'];
  /** The propagation mechanism. This is the whole point of the graph. */
  mechanism: string | null;
  detail: string | null;
  /** `action:57` or `prediction:12`. Never empty — an edge with no evidence cannot be stored. */
  derivedFrom: string;
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
}

export interface CascadeLayout {
  nodes: CascadeNode[];
  edges: CascadeEdge[];
  /** Edges whose endpoints are not both present. Surfaced, never silently dropped. */
  danglingEdges: GraphEdgeWire[];
  width: number;
  height: number;
}

const RADIUS = { event: 15, flight: 12, pairing: 10, booking: 5, hotel: 8 } as const;
const FLIGHT_MAX_RADIUS = 22;
const LAYER_GAP = 150;
const TOP = 56;
const H_PADDING = 44;
/** Cluster gutter inside the consequence layer, so the three kinds read as three groups. */
const CLUSTER_GAP = 56;

/** Node size reflects passengers affected, clamped so one big flight cannot dwarf the rest. */
function flightRadius(passengers: number, maxPassengers: number): number {
  if (!passengers || maxPassengers <= 0) return RADIUS.flight;
  const ratio = Math.min(1, passengers / maxPassengers);
  return Math.round(RADIUS.flight + ratio * (FLIGHT_MAX_RADIUS - RADIUS.flight));
}

function spread(count: number, from: number, to: number): number[] {
  if (count === 0) return [];
  if (count === 1) return [(from + to) / 2];
  const step = (to - from) / (count - 1);
  return Array.from({ length: count }, (_, i) => from + step * i);
}

/** Passenger counts per flight ref, so node size stays a recorded figure rather than a guess. */
export type PassengerWeights = Record<string, number>;

const CLUSTER_ORDER: CascadeNodeKind[] = ['pairing', 'booking', 'hotel'];

export function layoutFromGraph(
  graph: CascadeGraphPayload,
  options: { width?: number; weights?: PassengerWeights } = {},
): CascadeLayout {
  const width = options.width ?? 1180;
  const weights = options.weights ?? {};

  // Sorted by ref within each layer, so the picture is stable across reloads and across machines.
  const byDepth = new Map<number, GraphNodeWire[]>();
  for (const node of [...graph.nodes].sort((a, b) => a.ref.localeCompare(b.ref))) {
    const bucket = byDepth.get(node.depth) ?? [];
    bucket.push(node);
    byDepth.set(node.depth, bucket);
  }

  const maxPassengers = Object.values(weights).reduce((max, n) => Math.max(max, n), 0);
  const positioned = new Map<string, CascadeNode>();
  const depths = [...byDepth.keys()].sort((a, b) => a - b);

  for (const depth of depths) {
    const row = byDepth.get(depth) ?? [];
    const y = TOP + depths.indexOf(depth) * LAYER_GAP;

    // Deepest layer only: split into kind clusters. Shallower layers are homogeneous already.
    const clusters = CLUSTER_ORDER.filter((kind) => row.some((node) => node.kind === kind));
    const useClusters = clusters.length > 1;

    if (!useClusters) {
      const xs = spread(row.length, H_PADDING, width - H_PADDING);
      row.forEach((node, index) => {
        positioned.set(node.ref, toNode(node, xs[index] ?? width / 2, y, weights, maxPassengers));
      });
      continue;
    }

    // Width is shared in proportion to how many nodes each cluster holds, so 22 bookings get more
    // room than 9 pairings instead of every cluster getting a third of the canvas.
    const total = row.length;
    const usable = width - H_PADDING * 2 - CLUSTER_GAP * (clusters.length - 1);
    let cursor = H_PADDING;
    for (const kind of clusters) {
      const members = row.filter((node) => node.kind === kind);
      const span = Math.max(60, (usable * members.length) / total);
      const xs = spread(members.length, cursor, cursor + span);
      members.forEach((node, index) => {
        positioned.set(
          node.ref,
          toNode(node, xs[index] ?? cursor + span / 2, y, weights, maxPassengers),
        );
      });
      cursor += span + CLUSTER_GAP;
    }
  }

  const edges: CascadeEdge[] = [];
  const danglingEdges: GraphEdgeWire[] = [];
  for (const edge of graph.edges) {
    const from = positioned.get(edge.source_ref);
    const to = positioned.get(edge.target_ref);
    if (!from || !to) {
      // Should be impossible — the projection only emits edges between nodes it also emits — so if
      // it happens the answer is to show it, not to hide a broken payload behind a tidy picture.
      danglingEdges.push(edge);
      continue;
    }
    edges.push({
      id: `${edge.edge_kind}:${edge.source_ref}->${edge.target_ref}`,
      from: edge.source_ref,
      to: edge.target_ref,
      kind: edge.edge_kind,
      mechanism: edge.mechanism,
      detail: edge.detail,
      derivedFrom: edge.derived_from,
      fromX: from.x,
      fromY: from.y,
      toX: to.x,
      toY: to.y,
    });
  }

  return {
    nodes: [...positioned.values()],
    edges,
    danglingEdges,
    width,
    height: TOP + Math.max(0, depths.length - 1) * LAYER_GAP + 70,
  };
}

function toNode(
  node: GraphNodeWire,
  x: number,
  y: number,
  weights: PassengerWeights,
  maxPassengers: number,
): CascadeNode {
  const passengers = weights[node.ref];
  return {
    id: node.ref,
    kind: node.kind,
    label: node.label,
    sublabel: node.sublabel ?? undefined,
    weight: passengers,
    state: node.at_risk ? 'at_risk' : undefined,
    role: node.role ?? undefined,
    hasEvidence: node.has_evidence,
    x,
    y,
    radius:
      node.kind === 'flight' ? flightRadius(passengers ?? 0, maxPassengers) : RADIUS[node.kind],
  };
}

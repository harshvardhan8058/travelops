/**
 * Deterministic layered layout for the cascade graph. Pure, dependency-free, unit-testable.
 *
 * Deterministic matters twice: the same payload must draw the same picture every time a presenter
 * reloads, and a layout that re-flows while being read is unreadable. There is no force
 * simulation, no randomness, no animation of position.
 *
 * Three layers, because the data supports exactly three: the trigger, the flights it delayed, and
 * the crew pairings those flights touch. Connections and hotels are counts with no arrays behind
 * them, so they are not nodes here — see radius.ts.
 *
 * Owner: Stream D.
 */

import type { CrewPairingImpact, PairingMechanism } from '@/api/types';

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
  x: number;
  y: number;
  radius: number;
}

export interface CascadeEdge {
  id: string;
  from: string;
  to: string;
  /**
   * The propagation mechanism. This is the whole point of the graph.
   *
   * Widened to `string` for the server projection, which carries connection and accommodation
   * mechanisms (`missed_connection`, `overnight_required`) alongside the four crew ones.
   */
  mechanism: PairingMechanism | string;
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
  /**
   * `action:57` or `prediction:12` — the row this edge was read off, composed from the
   * server's `derived_from_*` ids. Present only for the server projection, which is the only
   * source that can name it.
   */
  evidenceRef?: string;
  edgeKind?: string;
  detail?: string | null;
}

export interface CascadeLayout {
  nodes: CascadeNode[];
  edges: CascadeEdge[];
  /** Pairings whose `source_flight` matches no flight in the group. Never silently dropped. */
  unmatchedPairings: CrewPairingImpact[];
  width: number;
  height: number;
}

export interface LayoutFlight {
  id: number;
  flight_number: string;
  route?: string;
  passengers?: number | null;
  state?: string;
  delay_minutes?: number;
}

const NODE_MIN_RADIUS = 10;
const NODE_MAX_RADIUS = 22;
const LAYER_Y = { event: 60, flight: 210, pairing: 380 } as const;
const H_PADDING = 60;

/** Node size reflects passengers affected, clamped so one big flight cannot dwarf the rest. */
function radiusFor(passengers: number | null | undefined, maxPassengers: number): number {
  if (!passengers || maxPassengers <= 0) return NODE_MIN_RADIUS;
  const ratio = Math.min(1, passengers / maxPassengers);
  return Math.round(NODE_MIN_RADIUS + ratio * (NODE_MAX_RADIUS - NODE_MIN_RADIUS));
}

function spread(count: number, width: number): number[] {
  if (count === 0) return [];
  if (count === 1) return [width / 2];
  const usable = width - H_PADDING * 2;
  return Array.from({ length: count }, (_, i) => H_PADDING + (usable / (count - 1)) * i);
}

export function buildCascadeLayout(
  input: {
    rootCause: string;
    airportIcao: string;
    flights: LayoutFlight[];
    pairings: CrewPairingImpact[];
  },
  width = 900,
): CascadeLayout {
  const { rootCause, airportIcao, flights, pairings } = input;
  const nodes: CascadeNode[] = [];
  const edges: CascadeEdge[] = [];

  const eventId = `event:${airportIcao}`;
  nodes.push({
    id: eventId,
    kind: 'event',
    label: rootCause,
    sublabel: airportIcao,
    x: width / 2,
    y: LAYER_Y.event,
    radius: 14,
  });

  const maxPassengers = flights.reduce((max, f) => Math.max(max, f.passengers ?? 0), 0);
  const flightXs = spread(flights.length, width);
  const flightPositions = new Map<string, { x: number; y: number }>();

  flights.forEach((flight, i) => {
    const x = flightXs[i] ?? width / 2;
    const y = LAYER_Y.flight;
    const id = `flight:${flight.id}`;
    flightPositions.set(flight.flight_number, { x, y });
    nodes.push({
      id,
      kind: 'flight',
      label: flight.flight_number,
      sublabel: flight.route,
      weight: flight.passengers ?? undefined,
      state: flight.state,
      x,
      y,
      radius: radiusFor(flight.passengers, maxPassengers),
    });
    // Every flight in the group descends from the group's own trigger. That edge is the group
    // membership itself, not an inference.
    edges.push({
      id: `${eventId}->${id}`,
      from: eventId,
      to: id,
      mechanism: 'operating',
      fromX: width / 2,
      fromY: LAYER_Y.event,
      toX: x,
      toY: y,
    });
  });

  const pairingXs = spread(pairings.length, width);
  const unmatchedPairings: CrewPairingImpact[] = [];

  pairings.forEach((pairing, i) => {
    const x = pairingXs[i] ?? width / 2;
    const y = LAYER_Y.pairing;
    const id = `pairing:${pairing.pairing_reference}`;
    nodes.push({
      id,
      kind: 'pairing',
      label: pairing.pairing_reference,
      sublabel: pairing.base_icao,
      state: pairing.at_risk ? 'at_risk' : undefined,
      x,
      y,
      radius: NODE_MIN_RADIUS + 2,
    });

    const source = flightPositions.get(pairing.source_flight);
    if (!source) {
      // The join failed. Surfaced, never dropped: a missing edge would understate the cascade.
      unmatchedPairings.push(pairing);
      return;
    }
    edges.push({
      id: `flight:${pairing.source_flight}->${id}`,
      from: `flight:${pairing.source_flight}`,
      to: id,
      mechanism: pairing.mechanism,
      fromX: source.x,
      fromY: source.y,
      toX: x,
      toY: y,
    });
  });

  return { nodes, edges, unmatchedPairings, width, height: LAYER_Y.pairing + 70 };
}

// ---------------------------------------------------------------------------------------
// Positioning the SERVER's cascade projection.
//
// `buildCascadeLayout` above derives a graph client-side from `flights` and `crew_pairings`.
// That was correct while the committed fixture was the only payload, and it is wrong now that
// the backend projects a real one, for two reasons:
//
//   1. The server graph contains booking and hotel nodes. Those cannot be reconstructed from
//      `flights` and `crew_pairings` at all, so the derived picture was structurally incapable
//      of showing the parts of the cascade that reach a passenger.
//   2. Every server edge names the `action` or `prediction` row it was read off. A derived edge
//      names nothing, because the client joined it into existence.
//
// So this function derives nothing. It assigns coordinates to nodes the backend already decided
// exist and draws edges the backend already decided are real. If it ever has to invent an edge
// to make the picture look complete, that is a bug.
// ---------------------------------------------------------------------------------------

/** Depth from the root event decides the row. The backend sets `depth`; this only spaces it. */
const SERVER_LAYER_HEIGHT = 148;
const SERVER_TOP_PADDING = 58;

/** Fixed radii by kind. Bookings are numerous and individually unremarkable, so they stay small. */
const SERVER_RADIUS: Record<CascadeNodeKind, number> = {
  event: 15,
  flight: 16,
  pairing: 12,
  booking: 6,
  hotel: 9,
};

const SERVER_KINDS = ['event', 'flight', 'pairing', 'booking', 'hotel'] as const;

/** Structural subset of the server contract, so this module needs no import from `@/api`. */
export interface ServerGraphInput {
  nodes: {
    ref: string;
    kind: string;
    label: string;
    sublabel?: string | null;
    depth: number;
    at_risk: boolean;
    has_evidence: boolean;
    role?: string | null;
  }[];
  edges: {
    source_ref: string;
    target_ref: string;
    edge_kind: string;
    mechanism?: string | null;
    detail?: string | null;
    depth: number;
    derived_from_action_id?: number | null;
    derived_from_prediction_id?: number | null;
  }[];
}

function asKind(kind: string): CascadeNodeKind {
  return (SERVER_KINDS as readonly string[]).includes(kind)
    ? (kind as CascadeNodeKind)
    : // An unrecognised kind is drawn as a flight rather than dropped: a node the server sent
      // and the client silently discarded would make the cascade look smaller than it is.
      'flight';
}

/**
 * `action:57` or `prediction:12`, from whichever id the server set.
 *
 * The database enforces that exactly one is present, so an edge reaching the `null` branch means
 * that constraint has been violated — which is worth showing as such rather than hiding.
 */
function evidenceRefOf(edge: ServerGraphInput['edges'][number]): string {
  if (edge.derived_from_action_id != null) return `action:${edge.derived_from_action_id}`;
  if (edge.derived_from_prediction_id != null)
    return `prediction:${edge.derived_from_prediction_id}`;
  return 'not recorded';
}

/**
 * Lay out the backend's projection.
 *
 * Nodes are grouped into rows by `depth`, then spread evenly within their row, so the picture
 * reads downwards from the root cause — the direction the cascade actually propagates.
 *
 * An edge whose endpoints are not both present is DROPPED rather than drawn to a guessed
 * position. A line to nowhere is worse than a missing line, because it looks like data.
 */
export function layoutServerGraph(graph: ServerGraphInput, width = 920): CascadeLayout {
  const byDepth = new Map<number, ServerGraphInput['nodes']>();
  for (const node of graph.nodes) {
    const bucket = byDepth.get(node.depth) ?? [];
    bucket.push(node);
    byDepth.set(node.depth, bucket);
  }

  const depths = [...byDepth.keys()].sort((a, b) => a - b);
  const positions = new Map<string, { x: number; y: number }>();
  const nodes: CascadeNode[] = [];

  depths.forEach((depth, row) => {
    const bucket = byDepth.get(depth) ?? [];
    const xs = spread(bucket.length, width);
    const y = SERVER_TOP_PADDING + row * SERVER_LAYER_HEIGHT;
    bucket.forEach((node, index) => {
      const x = xs[index] ?? width / 2;
      positions.set(node.ref, { x, y });
      nodes.push({
        id: node.ref,
        kind: asKind(node.kind),
        label: node.label,
        sublabel: node.sublabel ?? undefined,
        // `at_risk` and `has_evidence` are the server's words, not a UI judgement. A declared
        // node nothing has assessed is MARKED, not hidden: a cascade that quietly omits its
        // unworked flights looks finished when it is not.
        state: node.has_evidence ? (node.at_risk ? 'at_risk' : undefined) : 'unassessed',
        x,
        y,
        radius: SERVER_RADIUS[asKind(node.kind)],
      });
    });
  });

  const edges: CascadeEdge[] = [];
  for (const edge of graph.edges) {
    const from = positions.get(edge.source_ref);
    const to = positions.get(edge.target_ref);
    if (!from || !to) continue;
    edges.push({
      id: `${edge.source_ref}->${edge.target_ref}:${edge.edge_kind}`,
      from: edge.source_ref,
      to: edge.target_ref,
      mechanism: edge.mechanism ?? edge.edge_kind,
      fromX: from.x,
      fromY: from.y,
      toX: to.x,
      toY: to.y,
      evidenceRef: evidenceRefOf(edge),
      edgeKind: edge.edge_kind,
      detail: edge.detail ?? null,
    });
  }

  return {
    nodes,
    edges,
    // Only the client-side derivation can fail to match a pairing to a flight. The server
    // projection joins on ids it owns, so there is nothing to report here.
    unmatchedPairings: [],
    width,
    height: SERVER_TOP_PADDING + Math.max(1, depths.length) * SERVER_LAYER_HEIGHT,
  };
}

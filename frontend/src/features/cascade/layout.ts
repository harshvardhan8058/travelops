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

export type CascadeNodeKind = 'event' | 'flight' | 'pairing';

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
  /** The propagation mechanism. This is the whole point of the graph. */
  mechanism: PairingMechanism;
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
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

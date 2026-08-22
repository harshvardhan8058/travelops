/**
 * The blast radius and the graph layout, over a server payload.
 *
 * These tests changed shape in Phase 2 for a reason worth recording. They used to assert that the
 * browser built the right graph from the flight and pairing arrays — which meant they were testing a
 * second implementation of "which rows are related" that the database never saw. The topology now
 * arrives from `GET /incident-groups/{id}`, so what is worth testing here is the opposite: that this
 * code repeats the server's records faithfully and adds nothing of its own.
 */

import { describe, expect, it } from 'vitest';

import type {
  BlastRadiusPayload,
  CascadeGraphPayload,
  CrewPairingImpact,
  GraphEdgeWire,
  GraphNodeWire,
  IncidentGroupDetail,
} from '@/api/types';
import { buildRadius } from './radius';
import { layoutFromGraph } from './layout';

const provenance = { kind: 'fixture' as const, provider: 'fixture', source_ref: 'fixture:test' };

function pairing(overrides: Partial<CrewPairingImpact> = {}): CrewPairingImpact {
  return {
    pairing_reference: 'PAIR-A1',
    base_icao: 'VOBL',
    source_flight: '6E 2134',
    affected_leg: 'leg 1 of 4',
    mechanism: 'operating',
    detail: 'Crew are operating the delayed flight.',
    at_risk: true,
    ...overrides,
  };
}

function node(
  overrides: Partial<GraphNodeWire> & Pick<GraphNodeWire, 'ref' | 'kind'>,
): GraphNodeWire {
  return {
    label: overrides.ref,
    sublabel: null,
    depth: 1,
    at_risk: true,
    has_evidence: true,
    role: null,
    ...overrides,
  };
}

function edge(
  overrides: Partial<GraphEdgeWire> &
    Pick<GraphEdgeWire, 'source_ref' | 'target_ref' | 'edge_kind'>,
): GraphEdgeWire {
  return {
    mechanism: null,
    detail: null,
    depth: 1,
    derived_from: 'action:1',
    ...overrides,
  };
}

const GRAPH: CascadeGraphPayload = {
  group_reference: 'GRP-TEST',
  rule_version: 'cascade-graph-v1',
  nodes: [
    node({ ref: 'event:GRP-TEST', kind: 'event', label: 'GRP-TEST', depth: 0 }),
    node({ ref: 'flight:1', kind: 'flight', label: '6E 2134', depth: 1, role: 'primary' }),
    node({
      ref: 'flight:2',
      kind: 'flight',
      label: '6E 811',
      depth: 1,
      role: 'affected_departure',
    }),
    node({ ref: 'pairing:11', kind: 'pairing', label: 'PAIR-A1', depth: 2 }),
    node({ ref: 'pairing:12', kind: 'pairing', label: 'PAIR-A2', depth: 2 }),
    node({ ref: 'pairing:13', kind: 'pairing', label: 'PAIR-B1', depth: 2 }),
    node({ ref: 'booking:100', kind: 'booking', label: 'PNR0100', depth: 2 }),
    node({ ref: 'booking:101', kind: 'booking', label: 'PNR0101', depth: 2 }),
    node({ ref: 'hotel:1', kind: 'hotel', label: 'Airport Transit Inn', depth: 2, at_risk: false }),
  ],
  edges: [
    edge({
      source_ref: 'event:GRP-TEST',
      target_ref: 'flight:1',
      edge_kind: 'root_cause',
      derived_from: 'prediction:5',
    }),
    edge({
      source_ref: 'event:GRP-TEST',
      target_ref: 'flight:2',
      edge_kind: 'root_cause',
      derived_from: 'prediction:6',
    }),
    edge({
      source_ref: 'flight:1',
      target_ref: 'pairing:11',
      edge_kind: 'crew',
      mechanism: 'operating',
      depth: 2,
    }),
    edge({
      source_ref: 'flight:1',
      target_ref: 'pairing:12',
      edge_kind: 'crew',
      mechanism: 'second_pairing',
      depth: 2,
    }),
    edge({
      source_ref: 'flight:2',
      target_ref: 'pairing:13',
      edge_kind: 'crew',
      mechanism: 'positioning',
      depth: 2,
    }),
    edge({
      source_ref: 'flight:1',
      target_ref: 'booking:100',
      edge_kind: 'connection',
      mechanism: 'missed_connection',
      depth: 2,
    }),
    edge({
      source_ref: 'flight:1',
      target_ref: 'booking:101',
      edge_kind: 'connection',
      mechanism: 'missed_connection',
      depth: 2,
    }),
    edge({
      source_ref: 'flight:1',
      target_ref: 'hotel:1',
      edge_kind: 'accommodation',
      mechanism: 'overnight_required',
      depth: 2,
    }),
  ],
  edge_counts_by_kind: { root_cause: 2, crew: 3, connection: 2, accommodation: 1 },
  completeness: {
    member_flight_count: 2,
    flights_with_evidence: 2,
    is_complete: true,
    note: 'All 2 declared flights carry recorded evidence.',
  },
  source_action_ids: [1, 2],
  source_prediction_ids: [5, 6],
  snapshot_hash: 'abc123def456',
};

const BLAST: BlastRadiusPayload = {
  group_reference: 'GRP-TEST',
  headline:
    '2 flights, 332 passengers, 22 connections, 3 rotations. All 2 declared flights assessed.',
  basis: 'composed_from_recorded_findings',
  dimensions: [
    {
      key: 'flights',
      label: 'Flights in the cascade',
      value: 2,
      unit: 'flights',
      measured_by: 'incident_group_flight',
      is_complete: true,
      note: 'Declared membership.',
    },
    {
      key: 'connections',
      label: 'Connections that break',
      value: 22,
      unit: 'connections',
      measured_by: 'connection',
      is_complete: false,
      note: 'Union of distinct bookings.',
    },
  ],
  completeness: { flights_declared: 2, flights_assessed: 2, ratio: '2/2', is_complete: true },
  gaps: [],
};

function group(overrides: Partial<IncidentGroupDetail> = {}): IncidentGroupDetail {
  return {
    id: 1,
    reference: 'GRP-TEST',
    root_cause: 'weather',
    airport_icao: 'VOBL',
    severity: 'high',
    state: 'executing',
    opened_at: '2026-08-20T15:36:00Z',
    rollups: { flights_affected: 2, connections_at_risk: 22, candidate_hotels: 11 },
    rollup_status: {
      is_complete: true,
      computed_at: '2026-08-20T16:04:00Z',
      incidents_in_group: 2,
      incidents_assessed_connections: 2,
      incidents_assessed_crew: 2,
      member_flight_ids: [1, 2],
      flights_without_incident: [],
      membership_is_declared: true,
      note: 'All incidents assessed.',
    },
    flights: [
      {
        flight_id: 1,
        flight_number: '6E 2134',
        route: 'VOBL -> VIDP',
        origin_icao: 'VOBL',
        destination_icao: 'VIDP',
        role: 'primary',
        delay_minutes: 420,
        scheduled_departure_local: '21:10 IST',
        incident_id: 1,
        incident_reference: 'INC-1',
        incident_state: 'executing',
        passengers: 174,
      },
      {
        flight_id: 2,
        flight_number: '6E 811',
        route: 'VOBL -> VABB',
        origin_icao: 'VOBL',
        destination_icao: 'VABB',
        role: 'affected_departure',
        delay_minutes: 110,
        scheduled_departure_local: '21:25 IST',
        incident_id: 2,
        incident_reference: 'INC-2',
        incident_state: 'assuring',
        passengers: 158,
      },
    ],
    crew_pairings: [
      pairing(),
      pairing({ pairing_reference: 'PAIR-A2', mechanism: 'second_pairing' }),
      pairing({ pairing_reference: 'PAIR-B1', source_flight: '6E 811', mechanism: 'positioning' }),
    ],
    mechanism_legend: {
      operating: 'Crew are working the affected flight',
      onward_duty: 'A later leg of the same pairing is now infeasible',
      second_pairing: 'Cockpit and cabin crew sit on different pairings',
      positioning: 'Crew were travelling as passengers to operate another flight',
    },
    why_nine_not_eight: 'Crew are assigned to pairings, not flights.',
    graph: GRAPH,
    blast_radius: BLAST,
    provenance,
    ...overrides,
  } as IncidentGroupDetail;
}

describe('buildRadius', () => {
  it('counts each hop as the number of edges of that kind the server returned', () => {
    const radius = buildRadius(group());
    expect(radius.hops.map((hop) => hop.kind)).toEqual([
      'root_cause',
      'crew',
      'connection',
      'accommodation',
    ]);
    expect(radius.hops.map((hop) => hop.count)).toEqual([2, 3, 2, 1]);
    for (const hop of radius.hops) {
      expect(hop.count).toBe(hop.records.length);
    }
  });

  it('names the recorded row behind every record, so nothing is unattributable', () => {
    const radius = buildRadius(group());
    for (const hop of radius.hops) {
      for (const record of hop.records) {
        expect(record.derivedFrom).toMatch(/^(action|prediction):\d+$/);
      }
    }
  });

  it('attributes root-cause hops to a prediction and the rest to an action', () => {
    const radius = buildRadius(group());
    const rootCause = radius.hops.find((hop) => hop.kind === 'root_cause');
    expect(rootCause?.records.every((r) => r.derivedFrom.startsWith('prediction:'))).toBe(true);
    const crew = radius.hops.find((hop) => hop.kind === 'crew');
    expect(crew?.records.every((r) => r.derivedFrom.startsWith('action:'))).toBe(true);
  });

  it('partitions mechanisms over the returned field', () => {
    const radius = buildRadius(group());
    const crew = radius.hops.find((hop) => hop.kind === 'crew');
    expect(crew?.mechanismCounts).toEqual([
      { mechanism: 'operating', count: 1 },
      { mechanism: 'positioning', count: 1 },
      { mechanism: 'second_pairing', count: 1 },
    ]);
  });

  it('repeats the server dimensions without recomputing any of them', () => {
    const radius = buildRadius(group());
    expect(radius.dimensions).toBe(BLAST.dimensions);
    expect(radius.headline).toBe(BLAST.headline);
    expect(radius.completeness.ratio).toBe('2/2');
  });

  it('carries the incomplete flag through, so a floor is never shown as a total', () => {
    const radius = buildRadius(group());
    const connections = radius.dimensions.find((d) => d.key === 'connections');
    expect(connections?.is_complete).toBe(false);
  });

  it('has no confidence or probability figure anywhere', () => {
    const radius = buildRadius(group());
    const serialised = JSON.stringify(radius).toLowerCase();
    expect(serialised).not.toContain('confidence');
    expect(serialised).not.toContain('probability');
  });

  it('surfaces a rotation the graph does not reach rather than dropping it', () => {
    const withOrphan = group({
      crew_pairings: [
        pairing(),
        pairing({ pairing_reference: 'PAIR-A2', mechanism: 'second_pairing' }),
        pairing({
          pairing_reference: 'PAIR-B1',
          source_flight: '6E 811',
          mechanism: 'positioning',
        }),
        pairing({ pairing_reference: 'PAIR-ORPHAN', source_flight: 'XX 999' }),
      ],
    });
    expect(buildRadius(withOrphan).unmatched.map((p) => p.pairing_reference)).toEqual([
      'PAIR-ORPHAN',
    ]);
  });
});

describe('layoutFromGraph', () => {
  it('positions every node the server returned and no others', () => {
    const layout = layoutFromGraph(GRAPH);
    expect(layout.nodes).toHaveLength(GRAPH.nodes.length);
    expect(new Set(layout.nodes.map((n) => n.id))).toEqual(new Set(GRAPH.nodes.map((n) => n.ref)));
  });

  it('draws every edge the server returned, with none dangling', () => {
    const layout = layoutFromGraph(GRAPH);
    expect(layout.edges).toHaveLength(GRAPH.edges.length);
    expect(layout.danglingEdges).toEqual([]);
  });

  it('lays out by depth, so the trigger sits above the flights it explains', () => {
    const layout = layoutFromGraph(GRAPH);
    const eventY = layout.nodes.find((n) => n.kind === 'event')?.y ?? 0;
    const flightY = layout.nodes.find((n) => n.kind === 'flight')?.y ?? 0;
    const pairingY = layout.nodes.find((n) => n.kind === 'pairing')?.y ?? 0;
    expect(eventY).toBeLessThan(flightY);
    expect(flightY).toBeLessThan(pairingY);
  });

  it('groups the consequence kinds into separate clusters', () => {
    const layout = layoutFromGraph(GRAPH);
    const xs = (kind: string) => layout.nodes.filter((n) => n.kind === kind).map((n) => n.x);
    const pairings = xs('pairing');
    const bookings = xs('booking');
    // Every pairing sits left of every booking: three readable groups rather than one mixed row.
    expect(Math.max(...pairings)).toBeLessThan(Math.min(...bookings));
  });

  it('is deterministic: the same payload draws the same picture', () => {
    const first = layoutFromGraph(GRAPH);
    const second = layoutFromGraph({ ...GRAPH, nodes: [...GRAPH.nodes].reverse() });
    expect(second.nodes).toEqual(first.nodes);
  });

  it('sizes flight nodes by recorded passengers, never by anything invented', () => {
    const layout = layoutFromGraph(GRAPH, { weights: { 'flight:1': 174, 'flight:2': 41 } });
    const big = layout.nodes.find((n) => n.id === 'flight:1');
    const small = layout.nodes.find((n) => n.id === 'flight:2');
    expect(big?.radius).toBeGreaterThan(small?.radius ?? 0);
    expect(big?.weight).toBe(174);
  });

  it('reports a dangling edge rather than hiding a broken payload', () => {
    const broken: CascadeGraphPayload = {
      ...GRAPH,
      edges: [
        ...GRAPH.edges,
        edge({ source_ref: 'flight:1', target_ref: 'pairing:999', edge_kind: 'crew' }),
      ],
    };
    const layout = layoutFromGraph(broken);
    expect(layout.danglingEdges).toHaveLength(1);
    expect(layout.edges).toHaveLength(GRAPH.edges.length);
  });

  it('keeps an unassessed flight visible, because a hidden gap looks like no gap', () => {
    const partial: CascadeGraphPayload = {
      ...GRAPH,
      nodes: [
        ...GRAPH.nodes,
        node({ ref: 'flight:9', kind: 'flight', label: 'UK 705', depth: 1, has_evidence: false }),
      ],
    };
    const layout = layoutFromGraph(partial);
    const unassessed = layout.nodes.find((n) => n.id === 'flight:9');
    expect(unassessed).toBeDefined();
    expect(unassessed?.hasEvidence).toBe(false);
  });
});

import { describe, expect, it } from 'vitest';

import type { CrewPairingImpact, IncidentGroupDetail } from '@/api/types';
import { buildRadius } from './radius';
import { edgeConnectsNode, layoutServerGraph, type ServerGraphInput } from './layout';

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

function group(overrides: Partial<IncidentGroupDetail> = {}): IncidentGroupDetail {
  return {
    id: 1,
    reference: 'GRP-TEST',
    root_cause: 'weather',
    airport_icao: 'VOBL',
    severity: 'high',
    state: 'executing',
    rollups: { flights_affected: 2, connections_at_risk: 22, candidate_hotels: 11 },
    flights: [
      { id: 1, flight_number: '6E 2134', route: 'BLR -> DEL', passengers: 174, state: 'executing' },
      { id: 2, flight_number: '6E 811', route: 'BLR -> BOM', passengers: 158, state: 'assuring' },
    ] as unknown as Record<string, unknown>[],
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
    provenance,
    ...overrides,
  } as IncidentGroupDetail;
}

describe('buildRadius', () => {
  it('counts hops from returned array lengths only', () => {
    const radius = buildRadius(group());
    expect(radius.hops.map((hop) => hop.count)).toEqual([2, 3]);
    expect(radius.hops[0]?.countSource).toBe('length of flights[]');
    expect(radius.hops[1]?.countSource).toBe('length of crew_pairings[]');
  });

  it('partitions mechanisms over the returned enum field', () => {
    const radius = buildRadius(group());
    expect(radius.hops[1]?.mechanismCounts).toEqual([
      { mechanism: 'operating', count: 1 },
      { mechanism: 'positioning', count: 1 },
      { mechanism: 'second_pairing', count: 1 },
    ]);
  });

  it('keeps connections and hotels as terminals with a stated reason, never as hops', () => {
    const radius = buildRadius(group());
    expect(radius.hops).toHaveLength(2);
    const labels = radius.terminals.map((terminal) => terminal.label);
    expect(labels).toEqual(['connections at risk', 'candidate hotels']);
    for (const terminal of radius.terminals) {
      expect(terminal.reason).toMatch(/count only/i);
    }
  });

  it('omits a terminal the API did not return rather than inventing a zero', () => {
    const radius = buildRadius(group({ rollups: { flights_affected: 2 } }));
    expect(radius.terminals).toHaveLength(0);
  });

  it('surfaces a pairing whose source flight is not in the group instead of dropping it', () => {
    const radius = buildRadius(group({ crew_pairings: [pairing({ source_flight: 'XX 999' })] }));
    expect(radius.unmatched).toHaveLength(1);
    expect(radius.unmatched[0]?.source_flight).toBe('XX 999');
  });

  it('renders the backend summary verbatim', () => {
    expect(buildRadius(group()).summary).toBe('Crew are assigned to pairings, not flights.');
  });
});

describe('layoutServerGraph', () => {
  /**
   * The server projection is the only graph source. These tests replaced a suite that exercised a
   * client-side derivation from `flights` and `crew_pairings`; that function is deleted, because
   * it could name no evidence for any edge and could not represent bookings or hotels at all.
   */
  const graph: ServerGraphInput = {
    nodes: [
      {
        ref: 'event:VOBL',
        kind: 'event',
        label: 'weather',
        depth: 0,
        at_risk: true,
        has_evidence: true,
      },
      {
        ref: 'flight:1',
        kind: 'flight',
        label: '6E 2134',
        depth: 1,
        at_risk: true,
        has_evidence: true,
      },
      {
        ref: 'flight:2',
        kind: 'flight',
        label: '6E 811',
        depth: 1,
        at_risk: true,
        has_evidence: false,
      },
      {
        ref: 'booking:9',
        kind: 'booking',
        label: 'PNR9',
        depth: 2,
        at_risk: true,
        has_evidence: true,
      },
      {
        ref: 'hotel:3',
        kind: 'hotel',
        label: 'Hotel 3',
        depth: 2,
        at_risk: false,
        has_evidence: true,
      },
      {
        ref: 'pairing:PAIR-A1',
        kind: 'pairing',
        label: 'PAIR-A1',
        depth: 2,
        at_risk: true,
        has_evidence: true,
      },
    ],
    edges: [
      {
        source_ref: 'event:VOBL',
        target_ref: 'flight:1',
        edge_kind: 'root_cause',
        mechanism: 'weather',
        depth: 1,
        derived_from_prediction_id: 12,
      },
      {
        source_ref: 'flight:1',
        target_ref: 'booking:9',
        edge_kind: 'connection',
        mechanism: 'missed_connection',
        depth: 2,
        derived_from_action_id: 57,
      },
      {
        source_ref: 'flight:1',
        target_ref: 'pairing:PAIR-A1',
        edge_kind: 'crew',
        mechanism: 'operating',
        depth: 2,
        derived_from_action_id: 58,
      },
    ],
  };

  it('is deterministic: the same payload draws identical positions', () => {
    expect(layoutServerGraph(graph)).toEqual(layoutServerGraph(graph));
  });

  it('positions every node the server sent and drops none', () => {
    const layout = layoutServerGraph(graph);
    expect(layout.nodes.map((node) => node.id).sort()).toEqual(
      graph.nodes.map((node) => node.ref).sort(),
    );
  });

  it('keeps booking and hotel nodes the client could never have derived', () => {
    const kinds = new Set(layoutServerGraph(graph).nodes.map((node) => node.kind));
    expect([...kinds].sort()).toEqual(['booking', 'event', 'flight', 'hotel', 'pairing']);
  });

  it('rows nodes by depth, so the picture reads the way the cascade propagates', () => {
    const layout = layoutServerGraph(graph);
    const yOf = (ref: string) => layout.nodes.find((node) => node.id === ref)?.y ?? -1;
    expect(yOf('event:VOBL')).toBeLessThan(yOf('flight:1'));
    expect(yOf('flight:1')).toBeLessThan(yOf('booking:9'));
    expect(yOf('flight:1')).toBe(yOf('flight:2'));
  });

  it('names the recorded row behind every edge', () => {
    const layout = layoutServerGraph(graph);
    expect(layout.edges.map((edge) => edge.evidenceRef)).toEqual([
      'prediction:12',
      'action:57',
      'action:58',
    ]);
  });

  it('gives parallel edges distinct keys without dropping their evidence', () => {
    const repeatedFinding = {
      ...graph.edges[1]!,
      derived_from_action_id: 59,
    };
    const layout = layoutServerGraph({
      nodes: graph.nodes,
      edges: [...graph.edges, repeatedFinding],
    });
    const connectionEdges = layout.edges.filter(
      (edge) => edge.from === 'flight:1' && edge.to === 'booking:9',
    );

    expect(connectionEdges).toHaveLength(2);
    expect(connectionEdges.map((edge) => edge.evidenceRef)).toEqual(['action:57', 'action:59']);
    expect(new Set(connectionEdges.map((edge) => edge.id)).size).toBe(connectionEdges.length);
  });

  it('matches node selection against endpoints, never provenance ids', () => {
    const connectionEdge = layoutServerGraph(graph).edges.find(
      (edge) => edge.evidenceRef === 'action:57',
    );

    expect(connectionEdge).toBeDefined();
    expect(edgeConnectsNode(connectionEdge!, 'flight:1')).toBe(true);
    expect(edgeConnectsNode(connectionEdge!, 'booking:9')).toBe(true);
    expect(edgeConnectsNode(connectionEdge!, 'booking:57')).toBe(false);
  });

  it('marks a declared node nothing has assessed rather than hiding it', () => {
    const layout = layoutServerGraph(graph);
    expect(layout.nodes.find((node) => node.id === 'flight:2')?.state).toBe('unassessed');
    expect(layout.nodes.find((node) => node.id === 'flight:1')?.state).toBe('at_risk');
  });

  it('drops an edge whose endpoint is missing rather than drawing a line to nowhere', () => {
    const layout = layoutServerGraph({
      nodes: graph.nodes,
      edges: [
        ...graph.edges,
        { source_ref: 'flight:1', target_ref: 'booking:404', edge_kind: 'connection', depth: 2 },
      ],
    });
    expect(layout.edges).toHaveLength(graph.edges.length);
  });
});

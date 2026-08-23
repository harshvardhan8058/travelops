import { describe, expect, it } from 'vitest';

import type { CrewPairingImpact, IncidentGroupDetail } from '@/api/types';
import { buildRadius } from './radius';
import { buildCascadeLayout } from './layout';

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

describe('buildCascadeLayout', () => {
  const input = {
    rootCause: 'weather',
    airportIcao: 'VOBL',
    flights: [
      { id: 1, flight_number: '6E 2134', route: 'BLR -> DEL', passengers: 174, state: 'executing' },
      { id: 2, flight_number: '6E 811', route: 'BLR -> BOM', passengers: 158, state: 'assuring' },
    ],
    pairings: [pairing(), pairing({ pairing_reference: 'PAIR-B1', source_flight: '6E 811' })],
  };

  it('is deterministic: the same payload draws identical positions', () => {
    expect(buildCascadeLayout(input)).toEqual(buildCascadeLayout(input));
  });

  it('creates one node per record plus the trigger, and no others', () => {
    const layout = buildCascadeLayout(input);
    expect(layout.nodes.filter((node) => node.kind === 'event')).toHaveLength(1);
    expect(layout.nodes.filter((node) => node.kind === 'flight')).toHaveLength(2);
    expect(layout.nodes.filter((node) => node.kind === 'pairing')).toHaveLength(2);
  });

  it('labels every pairing edge with the mechanism from the record', () => {
    const layout = buildCascadeLayout(input);
    const pairingEdges = layout.edges.filter((edge) => edge.to.startsWith('pairing:'));
    expect(pairingEdges).toHaveLength(2);
    for (const edge of pairingEdges) {
      expect(['operating', 'onward_duty', 'second_pairing', 'positioning']).toContain(
        edge.mechanism,
      );
    }
  });

  it('reports an unmatched pairing rather than drawing an edge to nowhere', () => {
    const layout = buildCascadeLayout({
      ...input,
      pairings: [pairing({ source_flight: 'XX 999' })],
    });
    expect(layout.unmatchedPairings).toHaveLength(1);
    expect(layout.edges.filter((edge) => edge.to.startsWith('pairing:'))).toHaveLength(0);
  });

  it('never emits a connection or hotel node', () => {
    const kinds = new Set(buildCascadeLayout(input).nodes.map((node) => node.kind));
    expect([...kinds].sort()).toEqual(['event', 'flight', 'pairing']);
  });
});

/**
 * Cascade Explorer — `/cascade/:groupId`. docs/27 screen 3.
 *
 * The screen that makes "eight flights, nine rotations" countable instead of asserted.
 *
 * The topology is the server's. `GET /incident-groups/{id}` returns `graph.nodes` and `graph.edges`
 * projected from recorded actions and predictions, every edge naming the row it came from, and this
 * screen positions and filters them. It used to assemble the graph itself from the flight and
 * pairing arrays, which put a second, untested implementation of "which rows are related" in the
 * browser. Connections and rooms are now real nodes with real edges rather than untraversable
 * counts, because the projection can point at the action that found each one.
 *
 * The graph is an enhancement throughout: the blast-radius list and the crew table carry the same
 * records and read completely with the SVG switched off.
 *
 * Owner: Stream D.
 */

import { useMemo, useState } from 'react';
import { clsx } from 'clsx';
import { useParams, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { api, ApiError } from '@/api/client';
import type { IncidentGroupDetail, PairingMechanism } from '@/api/types';
import {
  ErrorState,
  LoadingState,
  MonoValue,
  Panel,
  ProvenanceDot,
  StateBadge,
} from '@/components/ui/primitives';
import { Metric, MetricTile } from '@/components/ui/Metric';
import { countDerivation } from '@/components/ui/derivation';
import { GraphEdge, GraphLegend, GraphNode, GraphSurface } from '@/components/ui/Graph';
import { useKeyboardList } from '@/hooks/useKeyboardList';
import { layoutFromGraph, type PassengerWeights } from './layout';
import { BlastRadius, PairingTable } from './BlastRadius';
import { WhatIfPanel } from './WhatIf';
import { GroupRunBar } from './GroupRunBar';
import { GroupPlanAssurance } from './GroupPlanAssurance';

/** Edge kinds an operator can isolate. Filtering hides records; it never invents any. */
const EDGE_FILTERS = [
  { kind: 'root_cause' as const, label: 'Root cause' },
  { kind: 'crew' as const, label: 'Crew' },
  { kind: 'connection' as const, label: 'Connections' },
  { kind: 'accommodation' as const, label: 'Rooms' },
];

const ROLLUP_TILES = [
  { field: 'flights_affected', label: 'Flights' },
  { field: 'passengers_affected', label: 'Passengers' },
  { field: 'connections_at_risk', label: 'Connections' },
  { field: 'crew_pairings_affected', label: 'Crew pairings' },
  { field: 'candidate_hotels', label: 'Hotels' },
] as const;

function CascadeSkeleton() {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_380px] gap-3">
      <Panel title="Cascade">
        <div className="h-[440px]">
          <LoadingState label="Loading cascade" />
        </div>
      </Panel>
      <Panel title="Blast radius">
        <div className="h-[440px]">
          <LoadingState label="Loading blast radius" />
        </div>
      </Panel>
    </div>
  );
}

export function CascadeExplorer() {
  const { groupId = 'current' } = useParams();
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [selectedHop, setSelectedHop] = useState<number | null>(null);
  const [selectedMechanism, setSelectedMechanism] = useState<PairingMechanism | null>(null);
  const [hiddenKinds, setHiddenKinds] = useState<Set<string>>(new Set());

  const groupQuery = useQuery({
    queryKey: ['incident-group', groupId],
    queryFn: () => api.incidentGroup(groupId),
  });
  // Only source of a flight -> incident link today: the flight board's own rows.
  const flightsQuery = useQuery({ queryKey: ['flights'], queryFn: api.flights });

  const group = groupQuery.data;

  const layout = useMemo(() => {
    if (!group?.graph) return null;
    // Passenger counts come from the declared member flights, so node size is a recorded figure.
    const weights: PassengerWeights = {};
    for (const flight of group.flights ?? []) {
      weights[`flight:${flight.flight_id}`] = flight.passengers;
    }
    return layoutFromGraph(group.graph, { width: 1180, weights });
  }, [group]);

  const visibleEdges = (layout?.edges ?? []).filter((edge) => !hiddenKinds.has(edge.kind));
  const visibleRefs = new Set<string>();
  for (const edge of visibleEdges) {
    visibleRefs.add(edge.from);
    visibleRefs.add(edge.to);
  }
  // A flight with no evidence has no edges, and must stay visible: it is a declared member of the
  // cascade that nothing has assessed, which is a gap the operator needs to see.
  const nodes = (layout?.nodes ?? []).filter(
    (node) =>
      hiddenKinds.size === 0 ||
      visibleRefs.has(node.id) ||
      node.kind === 'event' ||
      (node.kind === 'flight' && !node.hasEvidence),
  );
  const keyboard = useKeyboardList({
    count: nodes.length,
    onOpen: (index) => setSelectedNode(nodes[index]?.id ?? null),
  });

  if (groupQuery.isLoading) return <CascadeSkeleton />;

  if (groupQuery.error || !group || !layout) {
    const error = groupQuery.error instanceof ApiError ? groupQuery.error : null;
    return (
      <ErrorState
        code={error?.code ?? 'INTERNAL_ERROR'}
        message={
          error?.message ??
          `Could not load cascade ${groupId}. The Ops Board and Decision Timeline still work.`
        }
        correlationId={error?.correlationId ?? null}
        onRetry={() => void groupQuery.refetch()}
      />
    );
  }

  const selected = nodes.find((node) => node.id === selectedNode) ?? null;
  const selectedFlightNumber = selected?.kind === 'flight' ? selected.label : null;

  // Emphasis only: opacity and stroke. Nothing is added or removed by selection, so the count on
  // screen never depends on what happens to be highlighted.
  const emphasisFor = (edge: {
    from: string;
    to: string;
    kind: string;
    mechanism: string | null;
  }): 'on' | 'off' | 'neutral' => {
    const hopKind = selectedHop ? EDGE_FILTERS[selectedHop - 1]?.kind : null;
    const hopMatch = hopKind ? edge.kind === hopKind : null;
    const mechanismMatch = selectedMechanism ? edge.mechanism === selectedMechanism : null;
    const nodeMatch = selectedNode ? edge.from === selectedNode || edge.to === selectedNode : null;

    const signals = [hopMatch, mechanismMatch, nodeMatch].filter((s) => s !== null);
    if (signals.length === 0) return 'neutral';
    return signals.every(Boolean) ? 'on' : 'off';
  };

  const toggleKind = (kind: string) => {
    setHiddenKinds((current) => {
      const next = new Set(current);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  };

  const incidentByFlightId = new Map(
    (flightsQuery.data?.flights ?? []).map((flight) => [flight.id, flight.incident_reference]),
  );

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <GroupRunBar groupId={groupId} rollupStatus={group.rollup_status} />
      <Panel>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2">
          <div className="flex items-baseline gap-2">
            <MonoValue className="text-subtitle">{group.reference}</MonoValue>
            <ProvenanceDot
              kind={group.provenance.kind}
              provider={group.provenance.provider}
              sourceRef={group.provenance.source_ref}
            />
          </div>
          <span className="flex items-center gap-1.5 text-caption uppercase text-fg-muted">
            cause <MonoValue muted>{group.root_cause}</MonoValue>
          </span>
          <span className="flex items-center gap-1.5 text-caption uppercase text-fg-muted">
            airport <MonoValue muted>{group.airport_icao}</MonoValue>
          </span>
          <StateBadge status={group.severity} label={`severity ${group.severity}`} />
          <StateBadge status={group.state} />
        </div>
        <div className="flex flex-wrap gap-2 border-t border-border-subtle px-3 py-2">
          {ROLLUP_TILES.map((tile) => {
            const value = group.rollups?.[tile.field];
            return (
              <MetricTile
                key={tile.field}
                label={tile.label}
                value={typeof value === 'number' ? value : null}
                provenance={{ kind: group.provenance.kind, provider: group.provenance.provider }}
                derivation={countDerivation(tile.label, typeof value === 'number' ? value : null, {
                  endpoint: 'GET /incident-groups/{id}',
                  field: `rollups.${tile.field}`,
                  provenance: group.provenance,
                })}
              />
            );
          })}
        </div>
      </Panel>

      <div className="grid min-h-0 grid-cols-[minmax(0,1fr)_380px] gap-3">
        <div className="flex min-h-0 flex-col gap-3">
          <Panel
            title="Cascade"
            actions={
              <div className="flex items-center gap-2">
                {/* Filters hide records. They never create one, and the totals beside them are of
                 * the whole projection rather than of what is currently shown. */}
                <div className="flex items-center gap-1" role="group" aria-label="Edge kinds">
                  {EDGE_FILTERS.filter((filter) =>
                    Boolean(group.graph.edge_counts_by_kind[filter.kind]),
                  ).map((filter) => {
                    const hidden = hiddenKinds.has(filter.kind);
                    return (
                      <button
                        key={filter.kind}
                        type="button"
                        onClick={() => toggleKind(filter.kind)}
                        aria-pressed={!hidden}
                        className={clsx(
                          'rounded-sm border px-1.5 py-0.5 text-caption uppercase',
                          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                          hidden
                            ? 'border-border-subtle text-fg-muted line-through'
                            : 'border-accent-border bg-accent-subtle text-accent',
                        )}
                      >
                        {filter.label} {group.graph.edge_counts_by_kind[filter.kind]}
                      </button>
                    );
                  })}
                </div>
                <span className="text-caption text-fg-muted">
                  {layout.nodes.length} nodes · {layout.edges.length} edges
                </span>
              </div>
            }
          >
            {/* The graph is an enhancement; the tables below carry the same records. */}
            <div
              ref={keyboard.containerRef as React.RefObject<HTMLDivElement>}
              onKeyDown={keyboard.onKeyDown}
              className="px-2 py-2"
            >
              <GraphSurface
                width={layout.width}
                height={layout.height}
                ariaLabel={`Cascade for ${group.reference}: ${layout.nodes.length} nodes across trigger, flights and crew pairings`}
              >
                {visibleEdges.map((edge) => (
                  <GraphEdge key={edge.id} edge={edge} emphasis={emphasisFor(edge)} />
                ))}
                {nodes.map((node, index) => (
                  <GraphNode
                    key={node.id}
                    node={node}
                    /* Labels for the trigger and the flights, which are the spine an operator
                     * reads. The consequence clusters are labelled on selection only: 37 overlapping
                     * captions is noise that looks like data. */
                    showLabel={node.kind === 'event' || node.kind === 'flight'}
                    selected={node.id === selectedNode}
                    dimmed={Boolean(selectedNode) && node.id !== selectedNode}
                    onSelect={() => {
                      setSelectedNode(node.id === selectedNode ? null : node.id);
                      keyboard.setIndex(index);
                    }}
                    itemProps={keyboard.itemProps(index)}
                  />
                ))}
              </GraphSurface>
            </div>
            <div className="border-t border-border-subtle">
              <GraphLegend
                items={Object.entries(group.mechanism_legend ?? {}).map(([label, description]) => ({
                  label: label.replace(/_/g, ' '),
                  description,
                }))}
              />
            </div>
            {!group.graph.completeness.is_complete && (
              /* An incomplete projection says so on the picture itself, not only in a panel
               * somewhere else. A cascade that hides its unassessed flights looks finished. */
              <div className="border-t border-state-warn/30 bg-state-warn-bg px-3 py-1.5 text-caption text-state-warn">
                {group.graph.completeness.note}
              </div>
            )}
            {layout.danglingEdges.length > 0 && (
              <div className="border-t border-state-crit/30 bg-state-crit-bg px-3 py-1.5 text-caption text-state-crit">
                {layout.danglingEdges.length} edge(s) reference a node the projection did not
                return, so the picture is incomplete in a way the payload should not allow.
              </div>
            )}
          </Panel>

          {selected && (
            <Panel title="Selected node">
              <dl className="flex flex-col gap-1 px-3 py-2">
                <div className="flex gap-2">
                  <dt className="w-[96px] shrink-0 text-caption uppercase text-fg-muted">kind</dt>
                  <dd className="text-body text-fg">{selected.kind}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="w-[96px] shrink-0 text-caption uppercase text-fg-muted">label</dt>
                  <dd>
                    <MonoValue>{selected.label}</MonoValue>
                  </dd>
                </div>
                {selected.sublabel && (
                  <div className="flex gap-2">
                    <dt className="w-[96px] shrink-0 text-caption uppercase text-fg-muted">
                      detail
                    </dt>
                    <dd>
                      <MonoValue muted>{selected.sublabel}</MonoValue>
                    </dd>
                  </div>
                )}
                {selected.weight !== undefined && (
                  <div className="flex gap-2">
                    <dt className="w-[96px] shrink-0 text-caption uppercase text-fg-muted">
                      passengers
                    </dt>
                    <dd>
                      <Metric
                        value={selected.weight}
                        derivation={countDerivation('Passengers', selected.weight, {
                          endpoint: 'GET /incident-groups/{id}',
                          field: 'flights[].passengers',
                          provenance: group.provenance,
                        })}
                      />
                    </dd>
                  </div>
                )}
                {selected.kind === 'flight' && (
                  <div className="flex gap-2">
                    <dt className="w-[96px] shrink-0 text-caption uppercase text-fg-muted">
                      incident
                    </dt>
                    <dd>
                      {(() => {
                        const flightId = Number(selected.id.split(':')[1]);
                        const reference = incidentByFlightId.get(flightId);
                        return reference ? (
                          <Link
                            to={`/incidents/${reference}`}
                            className="rounded-sm underline decoration-dotted underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                          >
                            <MonoValue className="text-accent">{reference}</MonoValue>
                          </Link>
                        ) : (
                          <span
                            className="text-caption text-fg-muted"
                            title="The group payload carries no incident_reference, and this flight is not in the flight board response."
                          >
                            no incident link available
                          </span>
                        );
                      })()}
                    </dd>
                  </div>
                )}
              </dl>
            </Panel>
          )}

          <PairingTable
            group={group}
            selectedMechanism={selectedMechanism}
            selectedFlight={selectedFlightNumber}
          />
        </div>

        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          <BlastRadius
            group={group as IncidentGroupDetail}
            selectedHop={selectedHop}
            onSelectHop={setSelectedHop}
            selectedMechanism={selectedMechanism}
            onSelectMechanism={setSelectedMechanism}
          />
          <GroupPlanAssurance groupId={groupId} />
          <WhatIfPanel groupId={groupId} />
        </div>
      </div>
    </div>
  );
}

/**
 * Cascade Explorer — `/cascade/:groupId`. docs/27 screen 3.
 *
 * The screen that makes "eight flights, nine rotations" countable instead of asserted. Three
 * layers, because the data supports exactly three: the trigger, the flights, and the crew
 * pairings those flights touch. Connections and hotels arrive as counts with no arrays behind
 * them, so they are terminal tiles in the blast radius rather than nodes here.
 *
 * Owner: Stream D.
 */

import { useMemo, useState } from 'react';
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
import { buildCascadeLayout, type LayoutFlight } from './layout';
import { BlastRadius, PairingTable } from './BlastRadius';

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

  const groupQuery = useQuery({
    queryKey: ['incident-group', groupId],
    queryFn: () => api.incidentGroup(groupId),
  });
  // Only source of a flight -> incident link today: the flight board's own rows.
  const flightsQuery = useQuery({ queryKey: ['flights'], queryFn: api.flights });

  const group = groupQuery.data;

  const layout = useMemo(() => {
    if (!group) return null;
    const flights = (group.flights as unknown as LayoutFlight[]) ?? [];
    return buildCascadeLayout(
      {
        rootCause: group.root_cause,
        airportIcao: group.airport_icao,
        flights,
        pairings: group.crew_pairings,
      },
      920,
    );
  }, [group]);

  const nodes = layout?.nodes ?? [];
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

  // Edges belonging to the selected hop or node, for emphasis. Opacity and stroke only.
  const emphasisFor = (edgeId: string, mechanism: PairingMechanism): 'on' | 'off' | 'neutral' => {
    const hopMatch =
      selectedHop === 1
        ? edgeId.startsWith('event:')
        : selectedHop === 2
          ? !edgeId.startsWith('event:')
          : null;
    const mechanismMatch = selectedMechanism ? mechanism === selectedMechanism : null;
    const nodeMatch = selectedNode ? edgeId.includes(selectedNode.split(':')[1] ?? '') : null;

    const signals = [hopMatch, mechanismMatch, nodeMatch].filter((s) => s !== null);
    if (signals.length === 0) return 'neutral';
    return signals.every(Boolean) ? 'on' : 'off';
  };

  const incidentByFlightId = new Map(
    (flightsQuery.data?.flights ?? []).map((flight) => [flight.id, flight.incident_reference]),
  );

  return (
    <div className="flex min-h-0 flex-col gap-3">
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
              <span className="text-caption text-fg-muted">
                {layout.nodes.length} nodes · {layout.edges.length} edges
              </span>
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
                {layout.edges.map((edge) => (
                  <GraphEdge
                    key={edge.id}
                    edge={edge}
                    emphasis={emphasisFor(edge.id, edge.mechanism)}
                  />
                ))}
                {layout.nodes.map((node, index) => (
                  <GraphNode
                    key={node.id}
                    node={node}
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
        </div>
      </div>
    </div>
  );
}

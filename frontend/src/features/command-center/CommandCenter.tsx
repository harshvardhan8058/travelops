/**
 * Network Command Center — `/`. The opening shot.
 *
 * Answers the controller's first three questions without scrolling: what is broken, what has the
 * system already done, and what is waiting for me. The Phase 1 Ops Board becomes the flight-board
 * zone inside it.
 *
 * Information hierarchy per §0.3 of the Phase 2 plan: T1 situational across the top (network,
 * groups, health), T2 diagnostic in the flight board, T3 forensic behind popovers and `/sources`.
 *
 * Owner: Stream D.
 */

import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { api, ApiError } from '@/api/client';
import type { FlightRow, IncidentGroupSummary } from '@/api/types';
import {
  ErrorState,
  LoadingState,
  MonoValue,
  Panel,
  ProvenanceDot,
  StateBadge,
} from '@/components/ui/primitives';
import { FilterChips, MetricTile } from '@/components/ui/Metric';
import { countDerivation } from '@/components/ui/derivation';
import { FlightBoard, NetworkStrip } from './zones';

type FlightFilter = 'all' | 'at_risk' | 'disrupted' | 'in_recovery' | 'resolved';

/** Non-normal first: a controller should never scroll to find the problem. */
const NORMAL_STATUSES = new Set(['on_time', 'scheduled']);

function matchesFilter(flight: FlightRow, filter: FlightFilter): boolean {
  switch (filter) {
    case 'at_risk':
      return flight.risk_level === 'high' || flight.risk_level === 'severe';
    case 'disrupted':
      return flight.status === 'delayed' || flight.status === 'cancelled';
    case 'in_recovery':
      return flight.incident_reference !== null;
    case 'resolved':
      return flight.status === 'resolved';
    default:
      return true;
  }
}

export function CommandCenter() {
  const [filter, setFilter] = useState<FlightFilter>('all');

  const flightsQuery = useQuery({
    queryKey: ['flights'],
    queryFn: api.flights,
    refetchInterval: 15_000,
  });
  const groupsQuery = useQuery({
    queryKey: ['incident-groups'],
    queryFn: api.incidentGroups,
    refetchInterval: 30_000,
  });
  const readyQuery = useQuery({
    queryKey: ['ready'],
    queryFn: api.ready,
    refetchInterval: 30_000,
  });

  // Memoised so the identity is stable between renders: a fresh [] every render would make the
  // sort below recompute forever.
  const flights = useMemo(() => flightsQuery.data?.flights ?? [], [flightsQuery.data]);
  const network = flightsQuery.data?.network ?? [];
  const groups = groupsQuery.data?.groups ?? [];

  const sorted = useMemo(() => {
    const rows = flights.filter((flight) => matchesFilter(flight, filter));
    return [...rows].sort((a, b) => {
      const aNormal = NORMAL_STATUSES.has(a.status) ? 1 : 0;
      const bNormal = NORMAL_STATUSES.has(b.status) ? 1 : 0;
      if (aNormal !== bNormal) return aNormal - bNormal;
      return b.risk_index - a.risk_index;
    });
  }, [flights, filter]);

  if (flightsQuery.isLoading) return <LoadingState label="Loading network" />;

  if (flightsQuery.error) {
    const error = flightsQuery.error instanceof ApiError ? flightsQuery.error : null;
    return (
      <ErrorState
        code={error?.code ?? 'INTERNAL_ERROR'}
        message={error?.message ?? 'Could not load the network. The Decision Timeline still works.'}
        correlationId={error?.correlationId ?? null}
        onRetry={() => void flightsQuery.refetch()}
      />
    );
  }

  const dependencies = Object.entries(readyQuery.data?.dependencies ?? {});

  return (
    <div className="flex flex-col gap-3">
      <Panel
        title="Network"
        actions={
          <span className="flex items-center gap-2 text-caption text-fg-muted">
            {dependencies.map(([name, dependency]) => (
              <span key={name} className="flex items-center gap-1">
                <ProvenanceDot
                  kind={dependency.status === 'up' ? 'real' : 'unavailable'}
                  provider={name}
                />
                {name}
              </span>
            ))}
            <Link
              to="/sources"
              className="rounded-sm underline decoration-dotted underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              provenance ledger
            </Link>
          </span>
        }
      >
        <NetworkStrip network={network} />
      </Panel>

      {groups.length > 0 && (
        <Panel title="Active cascades">
          <ul className="flex flex-col divide-y divide-border-subtle">
            {groups.map((group) => (
              <GroupCard key={group.reference} group={group} />
            ))}
          </ul>
        </Panel>
      )}

      <Panel
        title="Flight board"
        actions={
          <div className="flex items-center gap-3">
            <FilterChips
              label="Flight filter"
              value={filter}
              onChange={setFilter}
              options={[
                { value: 'all', label: 'All', count: flights.length },
                {
                  value: 'at_risk',
                  label: 'At risk',
                  count: flights.filter((f) => matchesFilter(f, 'at_risk')).length,
                },
                {
                  value: 'disrupted',
                  label: 'Disrupted',
                  count: flights.filter((f) => matchesFilter(f, 'disrupted')).length,
                },
                {
                  value: 'in_recovery',
                  label: 'In recovery',
                  count: flights.filter((f) => matchesFilter(f, 'in_recovery')).length,
                },
                {
                  value: 'resolved',
                  label: 'Resolved',
                  count: flights.filter((f) => matchesFilter(f, 'resolved')).length,
                },
              ]}
            />
            <span className="text-caption text-fg-muted">
              {api.usingFixtures ? 'fixture data' : 'live API'}
            </span>
          </div>
        }
      >
        <FlightBoard flights={sorted} network={network} />
      </Panel>
    </div>
  );
}

function GroupCard({ group }: { group: IncidentGroupSummary }) {
  const tiles = [
    { field: 'flights_affected', label: 'Flights' },
    { field: 'passengers_affected', label: 'Passengers' },
    { field: 'connections_at_risk', label: 'Connections' },
    { field: 'crew_pairings_affected', label: 'Crew pairings' },
  ] as const;

  return (
    <li className="flex flex-col gap-2 px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Link
          to={`/cascade/${group.reference}`}
          className="rounded-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <MonoValue className="text-accent">{group.reference}</MonoValue>
        </Link>
        <StateBadge status={group.state} />
        <StateBadge status={group.severity} label={`severity ${group.severity}`} />
        <span className="text-caption uppercase text-fg-muted">
          cause <MonoValue muted>{group.root_cause}</MonoValue>
        </span>
        <span className="text-caption uppercase text-fg-muted">
          airport <MonoValue muted>{group.airport_icao}</MonoValue>
        </span>
        {group.awaiting_approval_count > 0 && (
          <StateBadge
            status="needs_human"
            label={`${group.awaiting_approval_count} awaiting approval`}
          />
        )}
        <ProvenanceDot
          kind={group.provenance.kind}
          provider={group.provenance.provider}
          sourceRef={group.provenance.source_ref}
        />
      </div>
      <div className="flex flex-wrap gap-2">
        {tiles.map((tile) => {
          const value = group.rollups?.[tile.field];
          return (
            <MetricTile
              key={tile.field}
              label={tile.label}
              value={typeof value === 'number' ? value : null}
              derivation={countDerivation(tile.label, typeof value === 'number' ? value : null, {
                endpoint: 'GET /incident-groups',
                field: `rollups.${tile.field}`,
                provenance: group.provenance,
              })}
            />
          );
        })}
      </div>
    </li>
  );
}

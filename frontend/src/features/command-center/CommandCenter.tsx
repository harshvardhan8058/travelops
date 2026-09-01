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
 * The screen now opens with a `PageHeader` rather than a panel of 12px labels. That is not
 * decoration: the route previously had no `h1` and no element larger than its own panel titles, so
 * the network the controller is looking at was rendered at exactly the same weight as the word
 * "provenance ledger" beside it. Dependency health and the live/fixture flag moved into that
 * header, because they qualify the whole screen rather than the airport strip they used to sit on.
 *
 * Two states that were previously silent are now rendered. `groupsQuery` and `readyQuery` had no
 * error branch at all — both degraded to `?? []`, so a failed groups call was indistinguishable
 * from a calm network with nothing open. On a disruption-recovery console those two things are
 * opposites, so a failure now says so and an absence of cascades says that instead.
 *
 * Owner: Stream D.
 */

import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { api, ApiError } from '@/api/client';
import type { FlightRow, IncidentGroupSummary } from '@/api/types';
import { dimensionAssessment, groupAssessment } from '@/assessment';
import {
  EmptyState,
  ErrorState,
  LoadingState,
  MonoValue,
  Panel,
  ProvenanceDot,
  StateBadge,
} from '@/components/ui/primitives';
import { CountBar, FilterChips, MetricTile } from '@/components/ui/Metric';
import { countDerivation } from '@/components/ui/derivation';
import { Labelled, Notice, PageHeader, StatStrip, Toolbar } from '@/components/ui/composition';
import { FlightBoard, NetworkStrip } from './zones';

type FlightFilter = 'all' | 'at_risk' | 'disrupted' | 'incident_linked';

/** Non-normal first: a controller should never scroll to find the problem. */
const NORMAL_STATUSES = new Set(['on_time', 'scheduled']);

function matchesFilter(flight: FlightRow, filter: FlightFilter): boolean {
  switch (filter) {
    case 'at_risk':
      return flight.risk_level === 'high' || flight.risk_level === 'severe';
    case 'disrupted':
      return flight.status === 'delayed' || flight.status === 'cancelled';
    case 'incident_linked':
      return flight.incident_reference !== null;
    default:
      return true;
  }
}

/**
 * The board's own composition, as a partition of the rows actually returned.
 *
 * A count, never a trend: there is no time-series endpoint, so a sparkline here would be drawn
 * from nothing. `CountBar` is the same primitive the provenance ledger uses to partition source
 * kinds, and for the same reason — it states how many of each, and claims nothing else.
 */
function statusSegments(flights: FlightRow[]) {
  const tally = new Map<string, number>();
  for (const flight of flights) tally.set(flight.status, (tally.get(flight.status) ?? 0) + 1);

  const tone = (status: string) =>
    status === 'cancelled'
      ? ('crit' as const)
      : status === 'delayed'
        ? ('warn' as const)
        : status === 'resolved' || status === 'on_time'
          ? ('ok' as const)
          : ('info' as const);

  return [...tally.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([status, count]) => ({
      label: status.replace(/_/g, ' '),
      count,
      tone: tone(status),
    }));
}

/** Mirrors the real layout, so the screen does not jump when the data lands. */
function BoardSkeleton() {
  return (
    <div className="flex flex-col gap-3">
      <Panel title="Network">
        <div className="h-[120px]">
          <LoadingState label="Loading network" />
        </div>
      </Panel>
      <Panel title="Physical flight status board">
        <div className="h-[420px]">
          <LoadingState label="Loading flights" />
        </div>
      </Panel>
    </div>
  );
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
      if (a.risk_index === null && b.risk_index === null) return 0;
      if (a.risk_index === null) return 1;
      if (b.risk_index === null) return -1;
      return b.risk_index - a.risk_index;
    });
  }, [flights, filter]);

  const segments = useMemo(() => statusSegments(flights), [flights]);

  if (flightsQuery.isLoading) return <BoardSkeleton />;

  if (flightsQuery.error) {
    const error = flightsQuery.error instanceof ApiError ? flightsQuery.error : null;
    return (
      <Panel title="Network">
        <ErrorState
          code={error?.code ?? 'INTERNAL_ERROR'}
          message={
            error?.message ?? 'Could not load the network. The Decision Timeline still works.'
          }
          correlationId={error?.correlationId ?? null}
          onRetry={() => void flightsQuery.refetch()}
        />
      </Panel>
    );
  }

  const dependencies = Object.entries(readyQuery.data?.dependencies ?? {});

  return (
    <div className="flex flex-col gap-3">
      <PageHeader
        eyebrow="Command centre"
        title="Network"
        meta={
          <>
            <Labelled label="flights">
              <MonoValue>{flights.length}</MonoValue>
            </Labelled>
            <Labelled label="airports">
              <MonoValue>{network.length}</MonoValue>
            </Labelled>
            <Labelled label="cascades">
              <MonoValue>{groups.length}</MonoValue>
            </Labelled>
            <Labelled label="status scope">
              <MonoValue muted>physical flight operation</MonoValue>
            </Labelled>
            <Labelled label="transport">
              <MonoValue muted>{api.usingFixtures ? 'fixture files' : 'real API'}</MonoValue>
            </Labelled>
          </>
        }
        actions={
          <Toolbar>
            {/*
             * Dependency health belongs to the whole console, not to the airport strip it used to
             * decorate. A dependency that is down is shown as down: `ProvenanceDot` carries an
             * sr-only description, so this is not a bare colour.
             */}
            {dependencies.map(([name, dependency]) => (
              <span
                key={name}
                className="inline-flex items-center gap-1 text-caption text-fg-muted"
              >
                <ProvenanceDot
                  kind={dependency.status === 'up' ? 'real' : 'unavailable'}
                  provider={name}
                />
                {name}
              </span>
            ))}
            <Link
              to="/sources"
              className="rounded-sm text-caption text-fg-secondary underline decoration-dotted underline-offset-2 transition-colors duration-hover ease-out hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              provenance ledger
            </Link>
          </Toolbar>
        }
      />

      {readyQuery.error && (
        <Notice tone="warn" divider="none" alert className="rounded border">
          Dependency health could not be read, so the indicators above are omitted rather than
          guessed. The flight board below is unaffected.
        </Notice>
      )}

      {/*
       * "Airport conditions", not "Network": the screen itself is now titled Network, and a panel
       * repeating its parent's name tells the reader nothing. This panel holds observations per
       * airport, so it says that.
       */}
      <Panel
        title="Airport conditions"
        actions={
          <MonoValue muted className="text-caption">
            {network.length}
          </MonoValue>
        }
      >
        <NetworkStrip network={network} />
      </Panel>

      <Panel
        title="Disruption cascades"
        actions={
          <MonoValue muted className="text-caption">
            {groups.length}
          </MonoValue>
        }
      >
        {/*
         * A failed groups call and a network with nothing open used to render identically. They are
         * opposite facts, so each now says which one it is.
         */}
        {groupsQuery.error ? (
          <ErrorState
            code={groupsQuery.error instanceof ApiError ? groupsQuery.error.code : 'INTERNAL_ERROR'}
            message={
              groupsQuery.error instanceof ApiError
                ? groupsQuery.error.message
                : 'Could not read the disruption groups. This is not the same as no cascade being open.'
            }
            correlationId={
              groupsQuery.error instanceof ApiError ? groupsQuery.error.correlationId : null
            }
            onRetry={() => void groupsQuery.refetch()}
          />
        ) : groups.length === 0 ? (
          <div className="px-3 py-6 text-center">
            <p className="text-body text-fg">No cascade is open</p>
            <p className="mt-1 text-caption text-fg-muted">
              Every declared flight is operating inside its own plan. A cascade appears here the
              moment one disruption reaches a second flight.
            </p>
          </div>
        ) : (
          <ul className="flex flex-col divide-y divide-border-subtle">
            {groups.map((group) => (
              <GroupCard key={group.reference} group={group} />
            ))}
          </ul>
        )}
      </Panel>

      <Panel
        title="Physical flight status board"
        actions={
          <Toolbar>
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
                  value: 'incident_linked',
                  label: 'Incident linked',
                  count: flights.filter((f) => matchesFilter(f, 'incident_linked')).length,
                },
              ]}
            />
            <MonoValue muted className="text-caption">
              {sorted.length} of {flights.length}
            </MonoValue>
          </Toolbar>
        }
      >
        {/*
         * "Nothing loaded" and "nothing matches the filter" were one empty state, whose copy told
         * the operator to run `make seed` even when the real answer was that they had filtered the
         * board down to nothing. They are separated here: the board owns the filter case, this owns
         * the genuinely empty dataset.
         */}
        {flights.length === 0 ? (
          <EmptyState
            title="No flights loaded"
            description="Seed the fixed-seed dataset, then inject the bengaluru_storm scenario. Until then the network has nothing to show."
          />
        ) : (
          <>
            {segments.length > 0 && (
              <div className="border-b border-border-subtle px-3 py-2">
                <CountBar segments={segments} total={flights.length} />
              </div>
            )}
            <Notice tone="muted">
              Flight status describes the operation itself. Recovery workflow state is recorded on
              the disruption cascade and in each incident workspace; an incident reference remains
              linked after that workflow resolves.
            </Notice>
            <FlightBoard flights={sorted} network={network} />
          </>
        )}
      </Panel>
    </div>
  );
}

function GroupCard({ group }: { group: IncidentGroupSummary }) {
  /*
   * `measure` names which dimensions are findings and which are declared data.
   *
   * Flights and passengers come from declared membership and the booking table: they are populated
   * the moment a group exists and are never the product of an assessment, so they are always
   * legible as themselves. Connections and crew impact are counted from recorded service actions,
   * so their zero is only a finding once something has looked — and until this card asked, it
   * rendered "0 connections at risk" for a cascade nothing had examined.
   */
  const connections = dimensionAssessment(group.rollup_status, 'connections', 'connections');
  const crew = dimensionAssessment(group.rollup_status, 'crew', 'crew impact');
  const assessment = groupAssessment(group.rollup_status);

  const tiles = [
    { field: 'flights_affected', label: 'Flights', measure: null },
    { field: 'passengers_affected', label: 'Passengers', measure: null },
    { field: 'connections_at_risk', label: 'Connections', measure: connections },
    { field: 'crew_pairings_affected', label: 'Crew pairings', measure: crew },
  ] as const;

  return (
    <li className="flex flex-col gap-2.5 px-3 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        {/*
         * The reference is the subject of the row, so it leads at subtitle size. It was previously
         * the same size as the words "cause" and "airport" that qualify it.
         */}
        <Link
          to={`/cascade/${group.reference}`}
          className="rounded-sm transition-colors duration-hover ease-out focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <MonoValue className="text-subtitle text-accent">{group.reference}</MonoValue>
        </Link>
        <StateBadge status={group.state} label={`workflow ${group.state.replace(/_/g, ' ')}`} />
        <StateBadge status={group.severity} label={`severity ${group.severity}`} />
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

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <Labelled label="cause">
          <MonoValue muted>{group.root_cause}</MonoValue>
        </Labelled>
        <Labelled label="airport">
          <MonoValue muted>{group.airport_icao}</MonoValue>
        </Labelled>
      </div>

      {assessment.isPartial && (
        <Notice tone="warn" divider="none" className="rounded border">
          <span className="text-fg">Partial assessment. </span>
          {assessment.incidents === 0
            ? 'This cascade is declared but no incident is open against it yet, so none of the figures below are findings.'
            : `${assessment.fullyAssessed} of ${assessment.incidents} incidents fully assessed, ${assessment.awaiting} awaiting.`}
          {assessment.flightsWithoutIncident > 0 &&
            ` ${assessment.flightsWithoutIncident} declared flight${assessment.flightsWithoutIncident === 1 ? ' has' : 's have'} no incident open.`}{' '}
          <Link to={`/cascade/${group.reference}`} className="underline hover:no-underline">
            Advance the disruption
          </Link>{' '}
          to assess the declared incidents.
        </Notice>
      )}

      <StatStrip>
        {tiles.map((tile) => {
          const raw = group.rollups?.[tile.field];
          const value = typeof raw === 'number' ? raw : null;
          /*
           * An unassessed dimension renders as the design system's absent value — an em dash —
           * rather than as its own number. The number is real, but it is a count of findings that
           * do not exist yet, and showing it as a measurement is the claim this card must not make.
           */
          const measured = tile.measure === null || tile.measure.isMeasured;
          return (
            <MetricTile
              key={tile.field}
              label={tile.label}
              value={measured ? value : null}
              derivation={countDerivation(tile.label, measured ? value : null, {
                endpoint: 'GET /incident-groups',
                field: `rollups.${tile.field}`,
                provenance: group.provenance,
              })}
              footnote={
                tile.measure && tile.measure.note ? (
                  <span className={measured ? undefined : 'text-state-warn'}>
                    {measured ? tile.measure.note : 'not assessed'}
                  </span>
                ) : undefined
              }
            />
          );
        })}
      </StatStrip>
    </li>
  );
}

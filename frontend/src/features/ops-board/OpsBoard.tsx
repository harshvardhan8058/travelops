/**
 * Ops Board — the default route and the opening shot of the demo.
 *
 * Answers the controller's first question: what is broken, and how bad is it?
 *
 * Owner: Stream E. Wave 0 ships the network strip and flight board against fixtures so the
 * layout, density and token usage are settled before features land on top.
 */

import { useQuery } from '@tanstack/react-query';
import { useNavigate, Link } from 'react-router-dom';
import { clsx } from 'clsx';

import { api } from '@/api/client';
import type { AirportConditions, FlightRow } from '@/api/types';
import {
  AgeIndicator,
  EmptyState,
  ErrorState,
  LoadingState,
  MonoValue,
  Panel,
  ProvenanceDot,
  RiskChip,
  StateBadge,
  WhyPopover,
} from '@/components/ui/primitives';
import { ApiError } from '@/api/client';
import { flightRiskDerivation } from '@/components/ui/derivation';

function timeOf(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toISOString().slice(11, 16);
}

function AirportTile({ airport }: { airport: AirportConditions }) {
  return (
    <div className="flex min-w-[168px] flex-col gap-1.5 rounded border border-border-subtle bg-surface p-2">
      <div className="flex items-center justify-between">
        <div className="flex items-baseline gap-1.5">
          <MonoValue className="text-subtitle">{airport.airport_icao}</MonoValue>
          <span className="text-caption text-fg-muted">{airport.city}</span>
        </div>
        <ProvenanceDot
          kind={airport.provenance.kind}
          provider={airport.provenance.provider}
          sourceRef={airport.provenance.source_ref}
          isStale={airport.provenance.is_stale}
        />
      </div>

      <div className="flex items-center gap-2">
        <RiskChip index={airport.risk_index} level={airport.risk_level} />
        <AgeIndicator minutes={airport.observation_age_minutes} />
      </div>

      <dl className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-caption text-fg-muted">
        <dt>wind</dt>
        <dd>
          <MonoValue muted>
            {airport.wind_speed_kt ?? '—'}kt {airport.wind_direction_deg ?? ''}
          </MonoValue>
        </dd>
        <dt>vis</dt>
        <dd>
          <MonoValue muted>{airport.visibility_m ?? '—'}m</MonoValue>
        </dd>
        <dt>ceil</dt>
        <dd>
          <MonoValue muted>{airport.ceiling_ft ?? '—'}ft</MonoValue>
        </dd>
      </dl>
    </div>
  );
}

function FlightBoard({
  flights,
  network,
}: {
  flights: FlightRow[];
  /**
   * From the same /flights response. The risk popover shows the origin observation and its
   * freshness, so a stale source is visible before it causes a gate failure.
   */
  network: AirportConditions[];
}) {
  const originByIcao = new Map(network.map((airport) => [airport.airport_icao, airport]));
  const navigate = useNavigate();

  if (flights.length === 0) {
    return (
      <EmptyState
        title="No flights loaded"
        description="Run `make seed` to load the fixed-seed dataset, then inject the bengaluru_storm scenario."
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-body">
        <thead>
          <tr className="border-b border-border-subtle bg-inset text-label uppercase text-fg-muted">
            <th className="px-3 py-1.5 text-left font-medium">Flight</th>
            <th className="px-3 py-1.5 text-left font-medium">Route</th>
            <th className="px-3 py-1.5 text-right font-medium">Sched</th>
            <th className="px-3 py-1.5 text-right font-medium">Est</th>
            <th className="px-3 py-1.5 text-right font-medium">Delay</th>
            <th className="px-3 py-1.5 text-left font-medium">Status</th>
            <th className="px-3 py-1.5 text-left font-medium">Risk</th>
            <th className="px-3 py-1.5 text-right font-medium">Pax</th>
            <th className="px-3 py-1.5 text-right font-medium">Conn</th>
            <th className="px-3 py-1.5 text-left font-medium">Incident</th>
            <th className="px-3 py-1.5 text-left font-medium">Src</th>
          </tr>
        </thead>
        <tbody>
          {flights.map((flight) => (
            <tr
              key={flight.id}
              /*
               * First leg of the demo path: a row with an open incident opens its recovery
               * workspace. Mouse convenience only — the flight number and incident cells are
               * real links, which is what makes the hop keyboard reachable. The WhyPopover
               * trigger stops its own click, so opening a derivation never navigates.
               */
              onClick={
                flight.incident_reference
                  ? () => navigate(`/incidents/${flight.incident_reference}`)
                  : undefined
              }
              className={clsx(
                'h-row border-b border-border-subtle transition-colors duration-hover ease-out hover:bg-raised',
                flight.incident_reference && 'cursor-pointer',
              )}
            >
              <td className="px-3">
                {flight.incident_reference ? (
                  <Link
                    to={`/incidents/${flight.incident_reference}`}
                    className="rounded-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    <MonoValue className="text-accent">{flight.flight_number}</MonoValue>
                  </Link>
                ) : (
                  <MonoValue>{flight.flight_number}</MonoValue>
                )}
              </td>
              <td className="px-3">
                <MonoValue muted>
                  {flight.origin_icao} → {flight.destination_icao}
                </MonoValue>
              </td>
              <td className="px-3 text-right">
                <MonoValue muted>{timeOf(flight.scheduled_departure)}</MonoValue>
              </td>
              <td className="px-3 text-right">
                <MonoValue muted>{timeOf(flight.estimated_departure)}</MonoValue>
              </td>
              <td className="px-3 text-right">
                {flight.delay_minutes > 0 ? (
                  <MonoValue className="text-state-warn">+{flight.delay_minutes}m</MonoValue>
                ) : (
                  <MonoValue muted>—</MonoValue>
                )}
              </td>
              <td className="px-3">
                <StateBadge status={flight.status} />
              </td>
              <td className="px-3">
                {/*
                 * WhyPopover owns the interaction; RiskChip is presentational here and takes
                 * no onClick, because a button inside a button is invalid HTML and would
                 * break the tab order. The derivation comes from the adapter, not from a
                 * sentence written in this file.
                 */}
                <WhyPopover
                  derivation={flightRiskDerivation(flight, originByIcao.get(flight.origin_icao))}
                >
                  <RiskChip index={flight.risk_index} level={flight.risk_level} />
                </WhyPopover>
              </td>
              <td className="px-3 text-right">
                <MonoValue>{flight.passengers}</MonoValue>
              </td>
              <td className="px-3 text-right">
                {flight.connections_at_risk > 0 ? (
                  <MonoValue className="text-state-warn">{flight.connections_at_risk}</MonoValue>
                ) : (
                  <MonoValue muted>0</MonoValue>
                )}
              </td>
              <td className="px-3">
                {flight.incident_reference ? (
                  <Link
                    to={`/incidents/${flight.incident_reference}`}
                    className="rounded-sm underline decoration-dotted underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    <MonoValue className="text-accent">{flight.incident_reference}</MonoValue>
                  </Link>
                ) : (
                  <MonoValue muted>—</MonoValue>
                )}
              </td>
              <td className="px-3">
                <ProvenanceDot
                  kind={flight.provenance.kind}
                  provider={flight.provenance.provider}
                  sourceRef={flight.provenance.source_ref}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function OpsBoard() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['flights'],
    queryFn: api.flights,
    refetchInterval: 15_000,
  });

  if (isLoading) return <LoadingState label="Loading operations" />;

  if (error) {
    const apiError = error instanceof ApiError ? error : null;
    return (
      <ErrorState
        code={apiError?.code ?? 'INTERNAL_ERROR'}
        message={apiError?.message ?? 'Could not load the flight board'}
        correlationId={apiError?.correlationId ?? null}
        onRetry={() => void refetch()}
      />
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <Panel title="Network">
        <div className="flex gap-2 overflow-x-auto p-2">
          {data?.network.map((airport) => (
            <AirportTile key={airport.airport_icao} airport={airport} />
          ))}
        </div>
      </Panel>

      <Panel
        title="Flight board"
        actions={
          <span className="text-caption text-fg-muted">
            {api.usingFixtures ? 'fixture data' : 'live API'}
          </span>
        }
      >
        <FlightBoard flights={data?.flights ?? []} network={data?.network ?? []} />
      </Panel>
    </div>
  );
}

/**
 * Ops Board — the default route and the opening shot of the demo.
 *
 * Answers the controller's first question: what is broken, and how bad is it?
 *
 * Owner: Stream E. Wave 0 ships the network strip and flight board against fixtures so the
 * layout, density and token usage are settled before features land on top.
 */

import { useQuery } from '@tanstack/react-query';

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

function FlightBoard({ flights }: { flights: FlightRow[] }) {
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
              className="h-row border-b border-border-subtle transition-colors duration-hover ease-out hover:bg-raised"
            >
              <td className="px-3">
                <MonoValue>{flight.flight_number}</MonoValue>
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
                <WhyPopover
                  derivation={`Deterministic index from rule set; band ${flight.risk_level}. Not a calibrated probability.`}
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
                  <MonoValue className="text-accent">{flight.incident_reference}</MonoValue>
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
        <FlightBoard flights={data?.flights ?? []} />
      </Panel>
    </div>
  );
}

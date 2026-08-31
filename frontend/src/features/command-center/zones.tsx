/**
 * Command Center zones: the network strip and the flight board.
 *
 * Lifted from the Phase 1 Ops Board, which becomes a zone rather than a route. The Phase 1
 * behaviour is preserved exactly — sortable rows arrive from the parent already ordered, the
 * risk popover is the same `flightRiskDerivation`, and every count still comes from the payload.
 *
 * Two presentation changes in this pass, neither of which touches behaviour:
 *
 *   - the table head is the shared `TableHead` recipe rather than the fifth hand-written copy of
 *     it, and the numeric columns carry the unit in the header instead of repeating it in every
 *     cell, which is what lets a column of figures actually line up;
 *   - a missing observation reads as "not observed" rather than as an em dash. A dash is what an
 *     absent wind reading and a wind reading of zero looked like before, and on a weather-driven
 *     disruption those are different facts.
 *
 * Owner: Stream D.
 */

import { useNavigate, Link } from 'react-router-dom';
import { clsx } from 'clsx';

import type { AirportConditions, FlightRow } from '@/api/types';
import {
  AgeIndicator,
  EmptyState,
  MonoValue,
  ProvenanceDot,
  RiskChip,
  StateBadge,
  WhyPopover,
} from '@/components/ui/primitives';
import { flightRiskDerivation } from '@/components/ui/derivation';
import { Absent, TableFrame, TableHead } from '@/components/ui/composition';
import { utcMinute } from '@/components/ui/format';

/** One observation value with its unit, or a named absence. */
function Observed({
  value,
  unit,
  extra,
}: {
  value: number | null;
  unit: string;
  /** Wind direction, which only means something alongside a speed. */
  extra?: string;
}) {
  if (value === null) return <Absent label="not observed" title="No observation for this field." />;
  return (
    <MonoValue muted>
      {value}
      {unit}
      {extra ? ` ${extra}` : ''}
    </MonoValue>
  );
}

export function AirportTile({ airport }: { airport: AirportConditions }) {
  return (
    <div className="flex min-w-[188px] flex-col gap-2 rounded border border-border-subtle bg-surface px-2.5 py-2">
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-col">
          {/* The ICAO is the subject of the tile, so it leads. The city qualifies it. */}
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

      <div className="flex flex-wrap items-center gap-2">
        <RiskChip index={airport.risk_index} level={airport.risk_level} />
        <AgeIndicator minutes={airport.observation_age_minutes} />
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 border-t border-border-subtle pt-1.5 text-caption text-fg-muted">
        <dt className="uppercase">wind</dt>
        <dd>
          <Observed
            value={airport.wind_speed_kt}
            unit="kt"
            extra={
              airport.wind_direction_deg === null ? undefined : `${airport.wind_direction_deg}°`
            }
          />
        </dd>
        <dt className="uppercase">vis</dt>
        <dd>
          <Observed value={airport.visibility_m} unit="m" />
        </dd>
        <dt className="uppercase">ceil</dt>
        <dd>
          <Observed value={airport.ceiling_ft} unit="ft" />
        </dd>
      </dl>
    </div>
  );
}

export function FlightBoard({
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
        title="No flights match this filter"
        description="Every flight is filtered out by the current selection. Choose All to see the whole board."
      />
    );
  }

  return (
    <TableFrame caption="Flights on the board, ordered with non-normal operations first and then by descending risk index. The order is a sort, not a ranking of importance.">
      <TableHead
        columns={[
          { key: 'flight', label: 'Flight' },
          { key: 'route', label: 'Route' },
          { key: 'sched', label: 'Sched', hint: 'UTC', align: 'right' },
          { key: 'est', label: 'Est', hint: 'UTC', align: 'right' },
          { key: 'delay', label: 'Delay', hint: 'min', align: 'right' },
          { key: 'status', label: 'Status' },
          { key: 'risk', label: 'Risk', hint: 'index · band' },
          { key: 'pax', label: 'Pax', align: 'right' },
          { key: 'conn', label: 'Conn', hint: 'at risk', align: 'right' },
          { key: 'incident', label: 'Incident' },
          { key: 'src', label: 'Src' },
        ]}
      />
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
                {flight.origin_icao} {'->'} {flight.destination_icao}
              </MonoValue>
            </td>
            <td className="px-3 text-right">
              <MonoValue muted>{utcMinute(flight.scheduled_departure) ?? '—'}</MonoValue>
            </td>
            <td className="px-3 text-right">
              <MonoValue muted>{utcMinute(flight.estimated_departure) ?? '—'}</MonoValue>
            </td>
            <td className="px-3 text-right">
              {flight.delay_minutes > 0 ? (
                <MonoValue className="text-state-warn">+{flight.delay_minutes}</MonoValue>
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
    </TableFrame>
  );
}

/** The airport strip. Data arrives from the Command Center; this zone only renders. */
export function NetworkStrip({ network }: { network: AirportConditions[] }) {
  if (network.length === 0) {
    return (
      <EmptyState
        title="No airport observations"
        description="The flights response carried no airport conditions, so no risk index can be shown for the network."
      />
    );
  }

  return (
    <div className="flex gap-2 overflow-x-auto p-2.5">
      {network.map((airport) => (
        <AirportTile key={airport.airport_icao} airport={airport} />
      ))}
    </div>
  );
}

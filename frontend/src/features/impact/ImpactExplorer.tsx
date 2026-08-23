/**
 * Impact Explorer — `/impact/:incidentId`. Per-entity exploration of a disruption.
 *
 * The rollups elsewhere say "604 passengers, 22 connections, 9 rotations, 11 hotels". This screen
 * answers the question those numbers provoke: **which ones, and what happened to each.**
 *
 * The source is recorded action payloads via `GET /incidents/{ref}/actions/{id}`. There is no
 * per-entity endpoint and asking for one would ask the backend to re-publish what it has already
 * written down — the services record their findings per entity, and FE-1 exists to reach them.
 *
 * Design constraints that shaped this:
 *
 * - **No client business calculation.** Every figure is a payload field or the length of a payload
 *   array. Where the service recorded its own total, both it and the row count are shown, and a
 *   disagreement is surfaced rather than reconciled.
 * - **Absent is not zero.** `shortfall_minutes: null` renders as "not recorded"; a zero would
 *   claim the service measured no shortfall.
 * - **Operational colour for operational state only.** A connection's `tier` and a passenger's
 *   special-needs flag are attributes of a booking, not operational states, so they are rendered
 *   as neutral chips. The only state colour here is on the reservation outcome, which genuinely is
 *   one.
 * - **A tab whose action never ran is absent, not empty.** An empty table would suggest nothing
 *   was found; the absence plus a named reason says nobody looked yet.
 *
 * Owner: Stream D.
 */

import { useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQueries, useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { Accessibility, Hotel, PlaneTakeoff, Users } from 'lucide-react';

import { api, ApiError } from '@/api/client';
import type { ActionDetail } from '@/api/types';
import {
  EmptyState,
  ErrorState,
  LoadingState,
  MonoValue,
  Panel,
  StateBadge,
} from '@/components/ui/primitives';
import { Metric, MetricTile } from '@/components/ui/Metric';
import { FilterChips } from '@/components/ui/Metric';
import { impactCountDerivation, impactFieldDerivation } from '@/components/ui/derivation';
import { useKeyboardList } from '@/hooks/useKeyboardList';
import { PassengerPriorityPanel } from './PassengerPriorityPanel';
import {
  connectionImpact,
  crewImpact,
  hotelImpact,
  IMPACT_ACTION_TYPES,
  latestOfType,
  type ConnectionRow,
} from './payloads';

type Tab = 'connections' | 'passengers' | 'priorities' | 'crew' | 'hotels';

const TAB_LABEL: Record<Tab, string> = {
  connections: 'Connections',
  passengers: 'Passengers',
  priorities: 'Recorded priorities',
  crew: 'Crew rotations',
  hotels: 'Hotels',
};

export function ImpactExplorer() {
  const { incidentId = '' } = useParams();
  const [tab, setTab] = useState<Tab>('connections');

  const incidentQuery = useQuery({
    queryKey: ['incident', incidentId],
    queryFn: () => api.incident(incidentId),
    enabled: incidentId.length > 0,
  });

  const actionIds = useMemo(
    () => (incidentQuery.data?.actions ?? []).map((action) => action.id),
    [incidentQuery.data],
  );

  // One request per action. The alternative — a batch endpoint — is a backend change for a screen
  // that opens a handful of actions, and `actions[]` is already bounded by the plan's task count.
  const detailQueries = useQueries({
    queries: actionIds.map((id) => ({
      queryKey: ['action-detail', incidentId, id],
      queryFn: () => api.actionDetail(incidentId, id),
      enabled: incidentId.length > 0,
    })),
  });

  const details = useMemo(
    () =>
      detailQueries
        .map((query) => query.data)
        .filter((value): value is ActionDetail => Boolean(value)),
    [detailQueries],
  );

  const loadingDetails = detailQueries.some((query) => query.isLoading);

  const connections = useMemo(() => {
    const action = latestOfType(details, IMPACT_ACTION_TYPES.connections);
    return action ? connectionImpact(action) : null;
  }, [details]);

  const crew = useMemo(() => {
    const action = latestOfType(details, IMPACT_ACTION_TYPES.crew);
    return action ? crewImpact(action) : null;
  }, [details]);

  const hotels = useMemo(() => {
    const search = latestOfType(details, IMPACT_ACTION_TYPES.hotelSearch);
    const reserve = latestOfType(details, IMPACT_ACTION_TYPES.hotelReserve);
    if (!search && !reserve) return null;
    return hotelImpact(search, reserve, incidentId);
  }, [details, incidentId]);

  if (incidentQuery.isLoading) {
    return (
      <Panel title="Impact">
        <div className="h-[560px]">
          <LoadingState label="Loading recorded findings" />
        </div>
      </Panel>
    );
  }

  if (incidentQuery.error || !incidentQuery.data) {
    const error = incidentQuery.error instanceof ApiError ? incidentQuery.error : null;
    return (
      <ErrorState
        code={error?.code ?? 'INTERNAL_ERROR'}
        message={error?.message ?? `Could not load ${incidentId}.`}
        correlationId={error?.correlationId ?? null}
        onRetry={() => void incidentQuery.refetch()}
      />
    );
  }

  /**
   * `priorities` is offered whenever the incident belongs to a group, because the ranking is written
   * at group scope by the orchestrator rather than by any one action — so unlike the tabs above, its
   * availability does not depend on which actions have run. The panel states for itself when nothing
   * has been recorded yet.
   */
  const groupReference = incidentQuery.data.group_reference ?? null;
  const available: Tab[] = [
    ...(connections ? (['connections', 'passengers'] as Tab[]) : []),
    ...(groupReference ? (['priorities'] as Tab[]) : []),
    ...(crew ? (['crew'] as Tab[]) : []),
    ...(hotels ? (['hotels'] as Tab[]) : []),
  ];
  const active = available.includes(tab) ? tab : (available[0] ?? 'connections');

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <Panel
        title="Impact"
        actions={
          <span className="flex items-center gap-3 text-caption text-fg-muted">
            <MonoValue muted>{incidentId}</MonoValue>
            <span>
              from <MonoValue muted>{details.length}</MonoValue> recorded action
              {details.length === 1 ? '' : 's'}
            </span>
          </span>
        }
      >
        <div className="flex flex-wrap items-start gap-3 px-3 py-2">
          {connections && (
            <>
              <MetricTile
                label="Connections at risk"
                value={connections.atRisk.length}
                derivation={impactCountDerivation({
                  label: 'Connections at risk',
                  arrayLength: connections.atRisk.length,
                  recordedTotal: connections.atRiskCount,
                  actionId: connections.actionId,
                  incidentReference: connections.incidentReference,
                  field: 'at_risk',
                  ruleVersion: connections.ruleVersion,
                  note: connections.marginNote,
                })}
              />
              <MetricTile
                label="Itineraries examined"
                value={connections.examined}
                derivation={impactFieldDerivation({
                  label: 'Itineraries examined',
                  value: connections.examined,
                  actionId: connections.actionId,
                  incidentReference: connections.incidentReference,
                  field: 'connecting_itineraries_examined',
                  ruleVersion: connections.ruleVersion,
                })}
                footnote={
                  connections.minimumConnectionMinutes !== null
                    ? `min connection ${connections.minimumConnectionMinutes} min`
                    : undefined
                }
              />
              <MetricTile
                label="Near misses"
                value={connections.nearMisses.length}
                derivation={impactCountDerivation({
                  label: 'Near misses',
                  arrayLength: connections.nearMisses.length,
                  recordedTotal: connections.nearMissCount,
                  actionId: connections.actionId,
                  incidentReference: connections.incidentReference,
                  field: 'near_misses',
                  ruleVersion: connections.ruleVersion,
                })}
                footnote={
                  connections.nearMissMinutes !== null
                    ? `within ${connections.nearMissMinutes} min`
                    : undefined
                }
              />
            </>
          )}
          {crew && (
            <MetricTile
              label="Rotations at risk"
              value={crew.impacts.length}
              derivation={impactCountDerivation({
                label: 'Rotations at risk',
                arrayLength: crew.impacts.length,
                recordedTotal: crew.pairingsAtRisk,
                actionId: crew.actionId,
                incidentReference: crew.incidentReference,
                field: 'impacts',
                ruleVersion: crew.ruleVersion,
                note: crew.scopeNote,
              })}
            />
          )}
          {hotels && (
            <>
              <MetricTile
                label="Rooms required"
                value={hotels.roomsRequired}
                derivation={impactFieldDerivation({
                  label: 'Rooms required',
                  value: hotels.roomsRequired,
                  actionId: hotels.searchActionId ?? hotels.reserveActionId ?? 0,
                  incidentReference: hotels.incidentReference,
                  field: 'rooms_required',
                  ruleVersion: hotels.ruleVersion,
                  note: hotels.constraintsNote,
                })}
                footnote={
                  hotels.passengersPerRoom !== null
                    ? `${hotels.passengersPerRoom} per room`
                    : undefined
                }
              />
              <MetricTile
                label="Rooms secured"
                value={hotels.roomsAllocated}
                derivation={impactFieldDerivation({
                  label: 'Rooms secured',
                  value: hotels.roomsAllocated,
                  actionId: hotels.reserveActionId ?? 0,
                  incidentReference: hotels.incidentReference,
                  field: 'rooms_allocated',
                  ruleVersion: hotels.ruleVersion,
                  absentDetail:
                    'No reservation has been recorded for this incident, so nothing has been secured yet.',
                })}
              />
            </>
          )}
        </div>

        {loadingDetails && (
          <p className="border-t border-border-subtle px-3 py-1.5 text-caption text-fg-muted">
            Loading {actionIds.length - details.length} more action payload
            {actionIds.length - details.length === 1 ? '' : 's'}…
          </p>
        )}

        {available.length === 0 ? (
          <EmptyState
            title="Nothing has been assessed yet"
            description={
              'No connection, crew or accommodation action has run for this incident, so there is ' +
              'no per-entity finding to explore. Run the incident to produce one.'
            }
          />
        ) : (
          <div className="border-t border-border-subtle px-3 py-2">
            <FilterChips
              label="Entity type"
              value={active}
              onChange={(next) => setTab(next as Tab)}
              options={available.map((key) => ({
                value: key,
                label: TAB_LABEL[key],
                count:
                  key === 'connections'
                    ? connections?.atRisk.length
                    : key === 'passengers'
                      ? connections?.atRisk.length
                      : key === 'priorities'
                        ? // The count comes from the panel's own query, not from here. Left
                          // undefined rather than guessed: a chip count this screen invented
                          // could disagree with the table beneath it.
                          undefined
                        : key === 'crew'
                          ? crew?.impacts.length
                          : hotels?.properties.length,
              }))}
            />
          </div>
        )}
      </Panel>

      {active === 'connections' && connections && <ConnectionTable impact={connections} />}
      {active === 'passengers' && connections && <PassengerTable impact={connections} />}
      {active === 'priorities' && groupReference && (
        <PassengerPriorityPanel groupRef={groupReference} />
      )}
      {active === 'crew' && crew && <CrewTable impact={crew} />}
      {active === 'hotels' && hotels && <HotelTable impact={hotels} />}
    </div>
  );
}

/** Neutral chip. Deliberately NOT a state colour: a tier is an attribute, not an operational state. */
function AttributeChip({ children, title }: { children: React.ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className="inline-flex items-center gap-1 rounded-sm border border-border-subtle bg-inset px-1.5 py-0.5 text-caption text-fg-secondary"
    >
      {children}
    </span>
  );
}

function Minutes({
  value,
  row,
  field,
  impactActionId,
  incidentReference,
  ruleVersion,
  label,
}: {
  value: number | null;
  row: ConnectionRow;
  field: string;
  impactActionId: number;
  incidentReference: string;
  ruleVersion: string | null;
  label: string;
}) {
  return (
    <Metric
      value={value}
      suffix={value === null ? undefined : <span className="text-fg-muted"> min</span>}
      derivation={impactFieldDerivation({
        label: `${label} · ${row.pnr ?? 'booking'}`,
        value,
        actionId: impactActionId,
        incidentReference,
        field: `at_risk[].${field}`,
        ruleVersion,
      })}
    />
  );
}

function ConnectionTable({ impact }: { impact: ReturnType<typeof connectionImpact> }) {
  const [showNearMisses, setShowNearMisses] = useState(false);
  const rows = showNearMisses ? impact.nearMisses : impact.atRisk;
  const keyboard = useKeyboardList({ count: rows.length });

  return (
    <Panel
      title="Connections"
      className="flex min-h-0 flex-col overflow-hidden"
      actions={
        <div className="flex items-center gap-2">
          {impact.nearMisses.length > 0 && (
            <button
              type="button"
              role="switch"
              aria-checked={showNearMisses}
              onClick={() => setShowNearMisses((value) => !value)}
              className={clsx(
                'rounded-sm border px-2 py-0.5 text-label uppercase',
                'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                showNearMisses
                  ? 'border-accent-border bg-accent-subtle text-accent'
                  : 'border-border-subtle text-fg-muted',
              )}
            >
              Near misses ({impact.nearMisses.length})
            </button>
          )}
          <MonoValue muted className="text-caption">
            action #{impact.actionId}
          </MonoValue>
        </div>
      }
    >
      {impact.marginNote && (
        <p className="border-b border-border-subtle px-3 py-1.5 text-caption text-fg-muted">
          {impact.marginNote}
        </p>
      )}
      {rows.length === 0 ? (
        <EmptyState
          title={showNearMisses ? 'No near misses recorded' : 'No connection is at risk'}
          description="The service examined this incident's itineraries and recorded none in this category."
        />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <table className="w-full border-collapse text-body">
            <caption className="sr-only">
              Connections {showNearMisses ? 'within the near-miss margin' : 'at risk'}, one row per
              booking
            </caption>
            <thead>
              <tr className="border-b border-border-subtle bg-inset text-label uppercase text-fg-muted">
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  PNR
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Inbound
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Onward
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  At
                </th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">
                  Slack
                </th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">
                  Shortfall
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Alternatives
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Attributes
                </th>
              </tr>
            </thead>
            <tbody
              ref={keyboard.containerRef as React.RefObject<HTMLTableSectionElement>}
              onKeyDown={keyboard.onKeyDown}
            >
              {rows.map((row, index) => (
                <tr
                  key={`${row.bookingId ?? index}-${row.onwardFlight ?? index}`}
                  className="h-row border-b border-border-subtle"
                >
                  <th scope="row" className="px-3 text-left font-normal">
                    <button
                      type="button"
                      {...keyboard.itemProps(index)}
                      className="text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      <MonoValue>{row.pnr ?? '—'}</MonoValue>
                    </button>
                  </th>
                  <td className="px-3">
                    <MonoValue muted>{row.inboundFlight ?? '—'}</MonoValue>
                    {row.inboundDelayMinutes !== null && (
                      <span className="ml-1.5 text-caption text-fg-muted">
                        +{row.inboundDelayMinutes}
                      </span>
                    )}
                  </td>
                  <td className="px-3">
                    <MonoValue muted>{row.onwardFlight ?? '—'}</MonoValue>
                  </td>
                  <td className="px-3">
                    <MonoValue muted>{row.connectionAirport ?? '—'}</MonoValue>
                  </td>
                  <td className="px-3 text-right">
                    <Minutes
                      value={row.slackMinutes}
                      row={row}
                      field="slack_minutes"
                      label="Slack"
                      impactActionId={impact.actionId}
                      incidentReference={impact.incidentReference}
                      ruleVersion={impact.ruleVersion}
                    />
                  </td>
                  <td className="px-3 text-right">
                    <Minutes
                      value={row.shortfallMinutes}
                      row={row}
                      field="shortfall_minutes"
                      label="Shortfall"
                      impactActionId={impact.actionId}
                      incidentReference={impact.incidentReference}
                      ruleVersion={impact.ruleVersion}
                    />
                  </td>
                  <td className="px-3">
                    {row.alternativeFlightIds.length > 0 ? (
                      <span className="flex items-center gap-1.5">
                        <Metric
                          value={row.alternativeFlightIds.length}
                          derivation={impactCountDerivation({
                            label: `Alternatives for ${row.pnr ?? 'booking'}`,
                            arrayLength: row.alternativeFlightIds.length,
                            actionId: impact.actionId,
                            incidentReference: impact.incidentReference,
                            field: 'at_risk[].alternative_flight_ids',
                            ruleVersion: impact.ruleVersion,
                            note: row.alternativesBasis
                              ? `Basis: ${row.alternativesBasis}`
                              : impact.alternativesNote,
                          })}
                        />
                        {row.alternativesBasis && (
                          <span className="text-caption text-fg-muted">
                            {row.alternativesBasis}
                          </span>
                        )}
                      </span>
                    ) : (
                      <span
                        className="text-caption text-fg-muted"
                        title="The service recorded no alternative for this booking."
                      >
                        none recorded
                      </span>
                    )}
                  </td>
                  <td className="px-3">
                    <span className="flex flex-wrap items-center gap-1">
                      {row.tier && (
                        <AttributeChip title="Fare tier, from the booking">
                          {row.tier}
                        </AttributeChip>
                      )}
                      {row.hasSpecialNeeds && (
                        <AttributeChip title="Special assistance recorded on this booking">
                          <Accessibility size={11} strokeWidth={1.5} aria-hidden />
                          assistance
                        </AttributeChip>
                      )}
                      {row.recoveredByOnwardDelay && (
                        <AttributeChip title="The onward flight is itself delayed enough for this connection to hold">
                          held by onward delay
                        </AttributeChip>
                      )}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function PassengerTable({ impact }: { impact: ReturnType<typeof connectionImpact> }) {
  const keyboard = useKeyboardList({ count: impact.atRisk.length });
  return (
    <Panel
      title="Passengers"
      className="flex min-h-0 flex-col overflow-hidden"
      actions={
        <MonoValue muted className="text-caption">
          action #{impact.actionId}
        </MonoValue>
      }
    >
      <p className="border-b border-border-subtle px-3 py-1.5 text-caption text-fg-muted">
        Passengers reachable from this incident's recorded findings — those holding a connection the
        connection service flagged. This is not every passenger on the flight: the group rollup
        counts those from booking records, and only the ones with an at-risk itinerary appear here.
      </p>
      {impact.atRisk.length === 0 ? (
        <EmptyState
          title="No passenger has an at-risk itinerary"
          description="Nothing was recorded against an individual passenger for this incident."
        />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <table className="w-full border-collapse text-body">
            <caption className="sr-only">Passengers holding an at-risk connection</caption>
            <thead>
              <tr className="border-b border-border-subtle bg-inset text-label uppercase text-fg-muted">
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Passenger
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  PNR
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Itinerary
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Attributes
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Recorded outcome
                </th>
              </tr>
            </thead>
            <tbody
              ref={keyboard.containerRef as React.RefObject<HTMLTableSectionElement>}
              onKeyDown={keyboard.onKeyDown}
            >
              {impact.atRisk.map((row, index) => (
                <tr
                  key={`${row.passengerId ?? index}`}
                  className="h-row border-b border-border-subtle"
                >
                  <th scope="row" className="px-3 text-left font-normal">
                    <button
                      type="button"
                      {...keyboard.itemProps(index)}
                      className="text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      {/* Pseudonymous reference. No name is published anywhere in this product. */}
                      <MonoValue>{row.passengerReference ?? '—'}</MonoValue>
                    </button>
                  </th>
                  <td className="px-3">
                    <MonoValue muted>{row.pnr ?? '—'}</MonoValue>
                  </td>
                  <td className="px-3">
                    <MonoValue muted>
                      {row.inboundFlight ?? '—'} → {row.onwardFlight ?? '—'}
                    </MonoValue>
                    {row.connectionAirport && (
                      <span className="ml-1.5 text-caption text-fg-muted">
                        via {row.connectionAirport}
                      </span>
                    )}
                  </td>
                  <td className="px-3">
                    <span className="flex flex-wrap items-center gap-1">
                      {row.tier && <AttributeChip>{row.tier}</AttributeChip>}
                      {row.hasSpecialNeeds && (
                        <AttributeChip title="Special assistance recorded on this booking">
                          <Accessibility size={11} strokeWidth={1.5} aria-hidden />
                          assistance
                        </AttributeChip>
                      )}
                    </span>
                  </td>
                  <td className="px-3 text-caption text-fg-muted">
                    {row.recoveredByOnwardDelay
                      ? 'connection holds — onward flight also delayed'
                      : row.shortfallMinutes !== null
                        ? `misses by ${row.shortfallMinutes} min`
                        : 'shortfall not recorded'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function CrewTable({ impact }: { impact: ReturnType<typeof crewImpact> }) {
  const [mechanism, setMechanism] = useState<string>('all');
  const shown =
    mechanism === 'all'
      ? impact.impacts
      : impact.impacts.filter((row) => row.mechanism === mechanism);
  const keyboard = useKeyboardList({ count: shown.length });
  const mechanisms = Object.keys(impact.mechanismCounts);

  return (
    <Panel
      title="Crew rotations"
      className="flex min-h-0 flex-col overflow-hidden"
      actions={
        <MonoValue muted className="text-caption">
          action #{impact.actionId}
        </MonoValue>
      }
    >
      {mechanisms.length > 0 && (
        <div className="border-b border-border-subtle px-3 py-2">
          <FilterChips
            label="Mechanism"
            value={mechanism}
            onChange={setMechanism}
            options={[
              { value: 'all', label: 'All mechanisms', count: impact.impacts.length },
              ...mechanisms.map((key) => ({
                value: key,
                label: key.replace(/_/g, ' '),
                count: impact.mechanismCounts[key],
              })),
            ]}
          />
        </div>
      )}
      {shown.length === 0 ? (
        <EmptyState
          title="No rotation matches"
          description="Clear the mechanism filter to see every recorded rotation."
        />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <table className="w-full border-collapse text-body">
            <caption className="sr-only">Affected crew rotations, one row per pairing</caption>
            <thead>
              <tr className="border-b border-border-subtle bg-inset text-label uppercase text-fg-muted">
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Pairing
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Base
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Mechanism
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Reached from
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Affected leg
                </th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">
                  Depth
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Why
                </th>
              </tr>
            </thead>
            <tbody
              ref={keyboard.containerRef as React.RefObject<HTMLTableSectionElement>}
              onKeyDown={keyboard.onKeyDown}
            >
              {shown.map((row, index) => (
                <tr
                  key={row.pairingReference ?? index}
                  className="border-b border-border-subtle align-top"
                >
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">
                    <button
                      type="button"
                      {...keyboard.itemProps(index)}
                      className="text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      <MonoValue>{row.pairingReference ?? '—'}</MonoValue>
                    </button>
                  </th>
                  <td className="px-3 py-1.5">
                    <MonoValue muted>{row.baseIcao ?? '—'}</MonoValue>
                  </td>
                  <td className="px-3 py-1.5">
                    <AttributeChip>
                      {(row.mechanism ?? 'not recorded').replace(/_/g, ' ')}
                    </AttributeChip>
                  </td>
                  <td className="px-3 py-1.5">
                    <MonoValue muted>{row.sourceFlight ?? '—'}</MonoValue>
                  </td>
                  <td className="px-3 py-1.5">
                    <MonoValue muted>{row.affectedLegFlight ?? '—'}</MonoValue>
                    {row.affectedLegOrder !== null && row.pairingLegCount !== null && (
                      <span className="ml-1.5 text-caption text-fg-muted">
                        leg {row.affectedLegOrder} of {row.pairingLegCount}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    <Metric
                      value={row.depth}
                      derivation={impactFieldDerivation({
                        label: `Depth · ${row.pairingReference ?? 'pairing'}`,
                        value: row.depth,
                        actionId: impact.actionId,
                        incidentReference: impact.incidentReference,
                        field: 'impacts[].depth',
                        ruleVersion: impact.ruleVersion,
                        note: 'Hops from the disrupted flight. 1 means the rotation was reached directly.',
                      })}
                    />
                  </td>
                  {/* The service's own sentence, verbatim. The UI does not paraphrase a finding. */}
                  <td className="max-w-[420px] px-3 py-1.5 text-caption text-fg-muted">
                    {row.detail ?? 'no detail recorded'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

function HotelTable({ impact }: { impact: ReturnType<typeof hotelImpact> }) {
  const keyboard = useKeyboardList({ count: impact.properties.length });
  return (
    <Panel
      title="Hotels"
      className="flex min-h-0 flex-col overflow-hidden"
      actions={
        <div className="flex items-center gap-2">
          {/* The one genuine operational state on this screen, so the one state colour. */}
          {impact.isComplete === false && <StateBadge status="degraded" label="shortfall" />}
          {impact.searchActionId !== null && (
            <MonoValue muted className="text-caption">
              search #{impact.searchActionId}
            </MonoValue>
          )}
          {impact.reserveActionId !== null && (
            <MonoValue muted className="text-caption">
              reserve #{impact.reserveActionId}
            </MonoValue>
          )}
        </div>
      }
    >
      {impact.shortfallNote && (
        <p className="border-b border-state-warn/30 bg-state-warn-bg px-3 py-1.5 text-caption text-state-warn">
          {impact.shortfallNote}
        </p>
      )}
      {impact.scopeNote && !impact.shortfallNote && (
        <p className="border-b border-border-subtle px-3 py-1.5 text-caption text-fg-muted">
          {impact.scopeNote}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-b border-border-subtle px-3 py-2 text-caption text-fg-muted">
        {impact.maxRateInr !== null && (
          <span>
            rate cap <MonoValue muted>INR {impact.maxRateInr}</MonoValue>
          </span>
        )}
        {impact.excludedByRateCap.length > 0 && (
          <span>
            <Metric
              value={impact.excludedByRateCap.length}
              derivation={impactCountDerivation({
                label: 'Excluded by rate cap',
                arrayLength: impact.excludedByRateCap.length,
                actionId: impact.searchActionId ?? 0,
                incidentReference: impact.incidentReference,
                field: 'excluded_by_rate_cap',
                ruleVersion: impact.ruleVersion,
                note: 'Properties whose rate exceeds the configured cap. Excluded by the service, not hidden by the UI.',
              })}
            />{' '}
            excluded by rate cap
          </span>
        )}
        {impact.passengersUnaccommodated !== null && impact.passengersUnaccommodated > 0 && (
          <span className="text-state-warn">
            {impact.passengersUnaccommodated} passengers unaccommodated
          </span>
        )}
        {impact.totalCostInr !== null && (
          <span>
            total <MonoValue muted>INR {impact.totalCostInr}</MonoValue>
          </span>
        )}
      </div>
      {impact.properties.length === 0 ? (
        <EmptyState
          title="No property recorded"
          description="The hotel service recorded no candidate property for this incident's airport."
        />
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <table className="w-full border-collapse text-body">
            <caption className="sr-only">
              Candidate properties and the rooms secured against each
            </caption>
            <thead>
              <tr className="border-b border-border-subtle bg-inset text-label uppercase text-fg-muted">
                <th scope="col" className="px-3 py-1.5 text-right font-medium">
                  Rank
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Property
                </th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">
                  Rate
                </th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">
                  Distance
                </th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">
                  Available
                </th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">
                  Secured
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Attributes
                </th>
              </tr>
            </thead>
            <tbody
              ref={keyboard.containerRef as React.RefObject<HTMLTableSectionElement>}
              onKeyDown={keyboard.onKeyDown}
            >
              {impact.properties.map((row, index) => (
                <tr key={row.hotelId ?? index} className="h-row border-b border-border-subtle">
                  <td className="px-3 text-right">
                    <MonoValue muted>{row.rank ?? '—'}</MonoValue>
                  </td>
                  <th scope="row" className="px-3 text-left font-normal">
                    <button
                      type="button"
                      {...keyboard.itemProps(index)}
                      className="text-left text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      {row.name ?? `hotel ${row.hotelId ?? '—'}`}
                    </button>
                  </th>
                  <td className="px-3 text-right">
                    <Metric
                      value={row.rateInr}
                      derivation={impactFieldDerivation({
                        label: `Rate · ${row.name ?? 'property'}`,
                        value: row.rateInr,
                        actionId: impact.searchActionId ?? 0,
                        incidentReference: impact.incidentReference,
                        field: 'options[].rate_inr',
                        ruleVersion: impact.ruleVersion,
                      })}
                    />
                  </td>
                  <td className="px-3 text-right">
                    <MonoValue muted>
                      {row.distanceKm === null ? '—' : `${row.distanceKm} km`}
                    </MonoValue>
                  </td>
                  <td className="px-3 text-right">
                    <Metric
                      value={row.availableRooms}
                      derivation={impactFieldDerivation({
                        label: `Available rooms · ${row.name ?? 'property'}`,
                        value: row.availableRooms,
                        actionId: impact.searchActionId ?? 0,
                        incidentReference: impact.incidentReference,
                        field: 'options[].available_rooms',
                        ruleVersion: impact.ruleVersion,
                        note: 'Computed server-side from the hold ledger, not read from a stored counter.',
                      })}
                    />
                    {row.totalRooms !== null && (
                      <span className="ml-1 text-caption text-fg-muted">/ {row.totalRooms}</span>
                    )}
                  </td>
                  <td className="px-3 text-right">
                    {row.roomsAllocated === null ? (
                      <span
                        className="text-caption text-fg-muted"
                        title="Not used by the reservation"
                      >
                        —
                      </span>
                    ) : (
                      <Metric
                        value={row.roomsAllocated}
                        derivation={impactFieldDerivation({
                          label: `Rooms secured · ${row.name ?? 'property'}`,
                          value: row.roomsAllocated,
                          actionId: impact.reserveActionId ?? 0,
                          incidentReference: impact.incidentReference,
                          field: 'allocations[].rooms',
                          ruleVersion: impact.ruleVersion,
                          note: row.allocationDetail,
                        })}
                      />
                    )}
                  </td>
                  <td className="px-3">
                    <span className="flex flex-wrap items-center gap-1">
                      {row.isPartner && (
                        <AttributeChip title="Contracted partner property">partner</AttributeChip>
                      )}
                      {row.roomsHeld !== null && row.roomsHeld > 0 && (
                        <AttributeChip title="Rooms already held against other actions">
                          {row.roomsHeld} held
                        </AttributeChip>
                      )}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

/** Icons are re-exported for the nav, which needs one per entity type without importing lucide twice. */
export const IMPACT_ICONS = { Users, PlaneTakeoff, Hotel } as const;

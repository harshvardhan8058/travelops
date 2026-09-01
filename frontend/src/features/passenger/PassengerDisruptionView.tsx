/**
 * Passenger operational view — `/passenger/:bookingRef`.
 *
 * This route projects only contracts the backend actually serves: the current disruption group and
 * its persisted passenger-priority rows. It deliberately does not project a trip, seat, rebooking,
 * refund, hotel assignment, entitlement, or notification outcome because no passenger outcome
 * endpoint publishes those facts. Workflow resolution and booking resolution are stated separately.
 */

import { useMemo } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Check, CircleDot, UserRound } from 'lucide-react';

import { api, ApiError } from '@/api/client';
import {
  EmptyState,
  ErrorState,
  LoadingState,
  MonoValue,
  Panel,
  ProvenanceDot,
  StateBadge,
} from '@/components/ui/primitives';
import {
  Labelled,
  Notice,
  PageHeader,
  PanelBody,
  TimelineItem,
  TimelineList,
  Toolbar,
} from '@/components/ui/composition';
import { utcStamp } from '@/components/ui/format';
import { passengerJourneyState, passengerLookup } from './passengerJourney';

export function PassengerDisruptionView() {
  const { bookingRef = '' } = useParams();

  const currentGroup = useQuery({
    queryKey: ['current-group'],
    queryFn: api.currentGroup,
    refetchInterval: 10_000,
  });
  const groupRef = currentGroup.data?.reference ?? '';

  const impacts = useQuery({
    queryKey: ['group-impacts', groupRef, 1000],
    queryFn: () => api.groupImpacts(groupRef, 1000),
    enabled: groupRef.length > 0,
    refetchInterval: 10_000,
  });

  const lookup = useMemo(
    () =>
      passengerLookup(
        impacts.data?.passengers ?? [],
        bookingRef,
        impacts.data?.returned ?? 0,
        impacts.data?.passengers_assessed ?? 0,
      ),
    [bookingRef, impacts.data],
  );
  const passenger = lookup.passenger;

  if (currentGroup.isLoading || (groupRef && impacts.isLoading)) {
    return (
      <Panel title="Passenger operational view">
        <div className="h-[420px]">
          <LoadingState label="Loading recorded passenger impact" />
        </div>
      </Panel>
    );
  }

  if (currentGroup.error) {
    const error = currentGroup.error instanceof ApiError ? currentGroup.error : null;
    return (
      <ErrorState
        code={error?.code ?? 'GROUP_UNAVAILABLE'}
        message={error?.message ?? 'The current disruption group could not be loaded.'}
        correlationId={error?.correlationId ?? null}
        onRetry={() => void currentGroup.refetch()}
      />
    );
  }

  if (!currentGroup.data) {
    return (
      <EmptyState
        title="No disruption group is available"
        description="This page will not substitute a sample booking for missing operational data."
      />
    );
  }

  if (impacts.error) {
    const error = impacts.error instanceof ApiError ? impacts.error : null;
    return (
      <ErrorState
        code={error?.code ?? 'PASSENGER_IMPACT_UNAVAILABLE'}
        message={error?.message ?? 'Recorded passenger priorities could not be loaded.'}
        correlationId={error?.correlationId ?? null}
        onRetry={() => void impacts.refetch()}
      />
    );
  }

  if (!impacts.data) {
    return <LoadingState label="Loading recorded passenger impact" />;
  }

  const group = currentGroup.data;
  const journey = passengerJourneyState(group);

  if (impacts.data.passengers_assessed === 0) {
    return (
      <div className="flex flex-col gap-3">
        <PageHeader
          eyebrow="Passenger operational view"
          title={<span className="font-mono tabular-nums">Booking {bookingRef}</span>}
          status={<StateBadge status={journey.token} label={journey.label} />}
          meta={
            <Labelled label="group">
              <MonoValue>{group.reference}</MonoValue>
            </Labelled>
          }
        />
        <EmptyState
          title="Passenger priorities are not recorded yet"
          description={impacts.data.note}
        />
        <Notice tone="muted">
          No sample booking is shown in its place. Booking options and outcomes are not published by
          any current endpoint.
        </Notice>
      </div>
    );
  }

  if (!passenger) {
    return (
      <ErrorState
        code="BOOKING_NOT_IN_IMPACT_RECORDS"
        message={
          lookup.responseIsComplete
            ? `Booking ${bookingRef} is not among the ${impacts.data.passengers_assessed} recorded passenger-priority rows for ${group.reference}. No sample was substituted.`
            : `Booking ${bookingRef} is not in the ${impacts.data.returned} highest-priority rows returned for ${group.reference}. The group has ${impacts.data.passengers_assessed} assessed passengers, and this capped contract cannot establish whether the booking appears outside the returned slice. No sample was substituted.`
        }
        correlationId={null}
      />
    );
  }

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <PageHeader
        eyebrow="Passenger operational view"
        title={<span className="font-mono tabular-nums">Booking {passenger.pnr}</span>}
        status={<StateBadge status={journey.token} label={journey.label} />}
        meta={
          <>
            <Labelled label="passenger reference">
              <MonoValue>{passenger.passenger_reference}</MonoValue>
            </Labelled>
            <Labelled label="group">
              <MonoValue>{group.reference}</MonoValue>
            </Labelled>
            <Labelled label="assessment basis">
              <MonoValue muted>{impacts.data.basis}</MonoValue>
            </Labelled>
            <Labelled label="computed">
              <MonoValue muted>{utcStamp(impacts.data.computed_at) ?? 'not recorded'}</MonoValue>
            </Labelled>
          </>
        }
        actions={
          <Toolbar>
            <span className="flex items-center gap-1.5">
              <ProvenanceDot
                kind={group.provenance.kind}
                provider={group.provenance.provider}
                sourceRef={group.provenance.source_ref}
              />
              <span className="text-caption uppercase text-fg-muted">group summary source</span>
            </span>
            <span className="text-caption uppercase text-fg-muted">
              passenger impact · {impacts.data.basis.replace(/_/g, ' ')}
            </span>
          </Toolbar>
        }
      />

      <div className="grid min-h-0 gap-3 lg:grid-cols-[minmax(0,1fr)_400px]">
        <div className="flex min-h-0 flex-col gap-3">
          <Panel title="What happened">
            <PanelBody gap="tight">
              <p className="text-subtitle text-fg">
                {group.root_cause.replace(/_/g, ' ')} disruption at {group.airport_icao}
              </p>
              <p className="text-body text-fg-secondary">
                This booking appears in the passenger-priority records for the current disruption
                group. The cause is an operational category, not a legal finding.
              </p>
              <div className="flex flex-wrap gap-x-5 gap-y-1">
                <Labelled label="severity">
                  <MonoValue>{group.severity}</MonoValue>
                </Labelled>
                <Labelled label="group opened">
                  <MonoValue muted>{utcStamp(group.opened_at) ?? 'not recorded'}</MonoValue>
                </Labelled>
              </div>
            </PanelBody>
          </Panel>

          <Panel title="Recorded impact priority">
            <PanelBody gap="tight">
              <div className="flex flex-wrap items-center gap-3">
                <UserRound size={16} strokeWidth={1.5} className="text-fg-muted" aria-hidden />
                <StateBadge status={passenger.priority_band} label={passenger.priority_band} />
                <Labelled label="priority index">
                  <MonoValue>{passenger.priority_index}</MonoValue>
                </Labelled>
                <Labelled label="rule">
                  <MonoValue muted>{passenger.rule_version}</MonoValue>
                </Labelled>
              </div>
              <Notice tone="muted" divider="none" className="rounded border">
                This is a constraint ranking from persisted rows: who has fewer remaining options,
                not who matters more. It is not a probability and authorises nothing.
              </Notice>
              {passenger.factors.length > 0 ? (
                <TimelineList label="Recorded priority factors">
                  {passenger.factors.map((factor, index) => (
                    <TimelineItem
                      key={`${factor.factor}-${factor.source}`}
                      tone="info"
                      isLast={index === passenger.factors.length - 1}
                    >
                      <span className="flex flex-wrap items-baseline gap-2">
                        <span className="text-body text-fg">
                          {factor.factor.replace(/_/g, ' ')}
                        </span>
                        <MonoValue>
                          {factor.weight >= 0 ? '+' : ''}
                          {factor.weight}
                        </MonoValue>
                      </span>
                      <p className="mt-0.5 text-caption text-fg-muted">
                        recorded source: {factor.source}
                      </p>
                    </TimelineItem>
                  ))}
                </TimelineList>
              ) : (
                <p className="text-body text-fg-muted">
                  No contributing factors were recorded for this passenger row.
                </p>
              )}
            </PanelBody>
          </Panel>

          <Panel title="Still unassessed">
            {impacts.data.unassessed_factors.length === 0 ? (
              <p className="px-3 py-3 text-body text-fg-muted">
                The impact contract names no unassessed factors.
              </p>
            ) : (
              <ul>
                {impacts.data.unassessed_factors.map((factor) => (
                  <li
                    key={factor.factor}
                    className="flex items-start gap-2 border-b border-border-subtle px-3 py-2"
                  >
                    <AlertTriangle
                      size={13}
                      strokeWidth={1.5}
                      className="mt-0.5 shrink-0 text-state-warn"
                      aria-hidden
                    />
                    <div>
                      <p className="text-body text-fg">{factor.factor.replace(/_/g, ' ')}</p>
                      <p className="text-caption text-fg-secondary">{factor.reason}</p>
                      <p className="text-caption text-fg-muted">
                        established only by {factor.established_by}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>

        <div className="flex min-h-0 flex-col gap-3">
          <Panel title="Recovery status">
            <PanelBody gap="tight">
              <div className="flex items-center gap-2">
                {journey.workflowComplete ? (
                  <Check size={14} strokeWidth={1.5} className="text-state-ok" aria-hidden />
                ) : (
                  <CircleDot size={14} strokeWidth={1.5} className="text-state-info" aria-hidden />
                )}
                <StateBadge status={journey.token} label={journey.label} />
              </div>
              <p className="text-subtitle text-fg">{journey.headline}</p>
              <p className="text-body text-fg-secondary">{journey.detail}</p>
              <Labelled label="incidents awaiting operator approval">
                <MonoValue>{group.awaiting_approval_count}</MonoValue>
              </Labelled>
            </PanelBody>
            {journey.pendingHuman && (
              <Notice tone="warn">
                A person still has to decide operational actions. This page does not turn that wait
                into a confirmed passenger change.
              </Notice>
            )}
          </Panel>

          <Panel title="Booking outcome">
            <PanelBody gap="tight">
              <StateBadge status="unavailable" label="not published" />
              <p className="text-subtitle text-fg">No passenger booking outcome is available</p>
              <p className="text-body text-fg-secondary">
                The current contracts publish disruption workflow state and passenger priority. They
                do not publish a rebooking, seat, refund, hotel assignment, entitlement, gate,
                revised itinerary, or notification delivery for this booking.
              </p>
            </PanelBody>
            <Notice tone="muted">
              Workflow resolved means the operational workflow finished. It does not mean this
              booking was changed. TravelOps will show an outcome here only when a backend contract
              records one.
            </Notice>
          </Panel>

          <Panel title="Source boundary">
            <PanelBody gap="tight">
              <Labelled label="passenger impact source">
                <MonoValue>{impacts.data.basis.replace(/_/g, ' ')}</MonoValue>
              </Labelled>
              <Labelled label="impact rows">
                <MonoValue>{impacts.data.passengers_assessed}</MonoValue>
              </Labelled>
              <Labelled label="returned">
                <MonoValue>{impacts.data.returned}</MonoValue>
              </Labelled>
              <Labelled label="ruleset hash">
                <MonoValue muted>{impacts.data.ruleset_hash.slice(0, 12)}</MonoValue>
              </Labelled>
              <p className="text-caption text-fg-muted">{impacts.data.note}</p>
            </PanelBody>
          </Panel>
        </div>
      </div>
    </div>
  );
}

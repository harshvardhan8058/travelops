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
import { AlertTriangle, UserRound } from 'lucide-react';

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
      <Panel title="Passenger view">
        <div className="h-[420px]">
          <LoadingState label="Loading booking information" />
        </div>
      </Panel>
    );
  }

  if (currentGroup.error) {
    const error = currentGroup.error instanceof ApiError ? currentGroup.error : null;
    return (
      <ErrorState
        code={error?.code ?? 'GROUP_UNAVAILABLE'}
        message={error?.message ?? 'The current disruption could not be loaded.'}
        correlationId={error?.correlationId ?? null}
        onRetry={() => void currentGroup.refetch()}
      />
    );
  }

  if (!currentGroup.data) {
    return (
      <EmptyState
        title="No current disruption is available"
        description="There is no disruption information to show for this booking right now."
      />
    );
  }

  if (impacts.error) {
    const error = impacts.error instanceof ApiError ? impacts.error : null;
    return (
      <ErrorState
        code={error?.code ?? 'PASSENGER_IMPACT_UNAVAILABLE'}
        message={error?.message ?? 'Booking priority information could not be loaded.'}
        correlationId={error?.correlationId ?? null}
        onRetry={() => void impacts.refetch()}
      />
    );
  }

  if (!impacts.data) {
    return <LoadingState label="Loading booking information" />;
  }

  const group = currentGroup.data;
  const journey = passengerJourneyState(group);

  const missingPriorityTitle =
    impacts.data.passengers_assessed === 0
      ? 'Priority information is not available yet'
      : lookup.responseIsComplete
        ? 'Booking not found in the available priority records'
        : 'Booking not shown in this priority list';
  const missingPriorityDetail =
    impacts.data.passengers_assessed === 0
      ? 'This booking cannot be checked against passenger priority records yet.'
      : lookup.responseIsComplete
        ? `We could not find booking ${bookingRef} among the ${impacts.data.passengers_assessed} passenger records available for this disruption.`
        : `We could not find booking ${bookingRef} in the ${impacts.data.returned} highest-priority records shown here. There are ${impacts.data.passengers_assessed} assessed passengers in total, so this page cannot tell whether the booking appears outside this list.`;

  const unassessedDetails = (
    <div className="border-t border-border-subtle pt-3">
      <p className="text-label uppercase text-fg-secondary">
        Information not included in the assessment
      </p>
      {impacts.data.unassessed_factors.length === 0 ? (
        <p className="mt-2 text-body text-fg-muted">
          The impact record names no unassessed factors.
        </p>
      ) : (
        <ul className="mt-2 divide-y divide-border-subtle">
          {impacts.data.unassessed_factors.map((factor) => (
            <li key={factor.factor} className="flex items-start gap-2 py-2">
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
    </div>
  );

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <PageHeader
        eyebrow="Passenger view"
        title={
          <span className="font-mono tabular-nums">Booking {passenger?.pnr ?? bookingRef}</span>
        }
      />

      <Panel title="What you need to know">
        <PanelBody gap="tight">
          <StateBadge status={journey.token} label={journey.label} className="self-start" />
          <div>
            <p className="text-subtitle text-fg">{journey.headline}</p>
            <p className="mt-1 text-body text-fg-secondary">{journey.detail}</p>
          </div>
          <div className="border-t border-border-subtle pt-3">
            <p className="text-subtitle text-fg">No confirmed booking update is available</p>
            <p className="mt-1 text-body text-fg-secondary">
              We do not have a confirmed change to show for this booking yet.
            </p>
          </div>
        </PanelBody>
      </Panel>

      <Panel title="What happened">
        <PanelBody gap="tight">
          <p className="text-subtitle text-fg">
            {group.root_cause.replace(/_/g, ' ')} disruption at {group.airport_icao}
          </p>
          <p className="text-body text-fg-secondary">
            {passenger
              ? 'This booking is included in the current disruption review. The cause shown is a broad category, not a legal finding.'
              : 'This disruption is shown at group level. Without a matching passenger priority record, this page does not claim that this booking is included. The cause shown is a broad category, not a legal finding.'}
          </p>
        </PanelBody>
      </Panel>

      <div className="grid min-h-0 gap-3 lg:grid-cols-2">
        <Panel title="How this booking was prioritised">
          <details open={!passenger}>
            <summary className="cursor-pointer px-3 py-3 text-body font-medium text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
              {passenger ? 'View the recorded priority and its factors' : missingPriorityTitle}
            </summary>
            <div className="border-t border-border-subtle">
              <PanelBody gap="tight">
                {passenger ? (
                  <>
                    <div className="flex flex-wrap items-center gap-3">
                      <UserRound
                        size={16}
                        strokeWidth={1.5}
                        className="text-fg-muted"
                        aria-hidden
                      />
                      <StateBadge
                        status={passenger.priority_band}
                        label={passenger.priority_band}
                      />
                      <Labelled label="priority index">
                        <MonoValue>{passenger.priority_index}</MonoValue>
                      </Labelled>
                      <Labelled label="priority rule">
                        <MonoValue muted>{passenger.rule_version}</MonoValue>
                      </Labelled>
                    </div>
                    <Notice tone="muted" divider="none" className="rounded border">
                      This ranking shows who may have fewer remaining options, not who matters more.
                      It is not a probability and does not confirm or authorise a booking change.
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
                  </>
                ) : (
                  <div>
                    <p className="text-subtitle text-fg">{missingPriorityTitle}</p>
                    <p className="mt-1 text-body text-fg-secondary">{missingPriorityDetail}</p>
                  </div>
                )}
                {unassessedDetails}
              </PanelBody>
            </div>
          </details>
        </Panel>

        <Panel title="Technical details">
          <details>
            <summary className="cursor-pointer px-3 py-3 text-body font-medium text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
              View references, sources, and timestamps
            </summary>
            <div className="border-t border-border-subtle">
              <PanelBody gap="tight">
                <div className="flex flex-wrap gap-x-5 gap-y-2">
                  {passenger ? (
                    <Labelled label="passenger reference">
                      <MonoValue>{passenger.passenger_reference}</MonoValue>
                    </Labelled>
                  ) : (
                    <Labelled label="requested booking">
                      <MonoValue>{bookingRef}</MonoValue>
                    </Labelled>
                  )}
                  <Labelled label="group reference">
                    <MonoValue>{group.reference}</MonoValue>
                  </Labelled>
                  <Labelled label="severity">
                    <MonoValue>{group.severity}</MonoValue>
                  </Labelled>
                  <Labelled label="group opened">
                    <MonoValue muted>{utcStamp(group.opened_at) ?? 'not recorded'}</MonoValue>
                  </Labelled>
                  <Labelled label="assessment basis">
                    <MonoValue muted>{impacts.data.basis}</MonoValue>
                  </Labelled>
                  <Labelled label="computed">
                    <MonoValue muted>
                      {utcStamp(impacts.data.computed_at) ?? 'not recorded'}
                    </MonoValue>
                  </Labelled>
                  <Labelled label="passengers assessed">
                    <MonoValue>{impacts.data.passengers_assessed}</MonoValue>
                  </Labelled>
                  <Labelled label="records returned">
                    <MonoValue>{impacts.data.returned}</MonoValue>
                  </Labelled>
                  <Labelled label="awaiting decision">
                    <MonoValue>{group.awaiting_approval_count}</MonoValue>
                  </Labelled>
                  <Labelled label="ruleset hash" className="min-w-0">
                    <MonoValue muted className="break-all">
                      {impacts.data.ruleset_hash}
                    </MonoValue>
                  </Labelled>
                </div>

                <div className="flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3">
                  <ProvenanceDot
                    kind={group.provenance.kind}
                    provider={group.provenance.provider}
                    sourceRef={group.provenance.source_ref}
                  />
                  <span className="text-caption uppercase text-fg-muted">group summary source</span>
                  <MonoValue muted>{group.provenance.provider}</MonoValue>
                  {group.provenance.source_ref && (
                    <MonoValue muted>{group.provenance.source_ref}</MonoValue>
                  )}
                </div>
                <p className="text-caption text-fg-muted">
                  passenger impact · {impacts.data.basis.replace(/_/g, ' ')}
                </p>
                <p className="text-caption text-fg-muted">{impacts.data.note}</p>
              </PanelBody>
            </div>
          </details>
        </Panel>
      </div>
    </div>
  );
}

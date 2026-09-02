/**
 * Passenger view — `/passenger/:bookingRef`.
 *
 * Renders inside `PassengerShell`, not `AppShell`: this is a customer-portal screen, not an
 * operator surface with one more route added to it. See `PassengerShell` for why that split
 * matters and `App.tsx` for where the route branches.
 *
 * The section order is fixed and is the point: trip, then the disruption, then what happened, then
 * where things stand, then what TravelOps is doing about it, then what to expect, then a plain
 * reassurance, then — last, and visibly secondary — the technical record. A passenger who reads
 * only the first screenful still gets the whole story; an operator's own vocabulary (orchestration,
 * assurance gates, plan tasks, action ids, decision logs) appears nowhere above the fold, and the
 * one section that does carry hashes and reference ids is collapsed by default.
 *
 * Every fact here is read, never computed beyond formatting. `GET /bookings/{pnr}` is the trip;
 * `GET /incidents/{ref}` — the same incident an operator's Recovery Workspace reads, found via the
 * booking's own disrupted segment — is the disruption, its state, and its actions. Passenger and
 * operator read the same rows because there is only one lifecycle; this screen just narrates it in
 * different words. Nothing here invents a rebooking, a seat, a refund, a hotel night, a transport
 * booking, a sent notification or a compensation figure: those only ever appear if a backend row
 * says they happened, and none does yet, so the page says so instead of guessing.
 */

import { useMemo } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, Luggage } from 'lucide-react';

import { api, ApiError } from '@/api/client';
import { dataUnavailable, retryUnlessUnavailable } from '@/api/unavailable';
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
  PanelBody,
  TimelineItem,
  TimelineList,
} from '@/components/ui/composition';
import { durationFromMinutes, utcMinute, utcStamp } from '@/components/ui/format';
import { PassengerShell } from './PassengerShell';
import { journeyStateFor, passengerLookup } from './passengerJourney';
import { operationalStatus, summariseTrip } from './tripSummary';

function SectionTitle({ children }: { children: string }) {
  return <h2 className="text-label uppercase tracking-wide text-fg-muted">{children}</h2>;
}

export function PassengerDisruptionView() {
  const { bookingRef = '' } = useParams();

  const booking = useQuery({
    queryKey: ['booking', bookingRef],
    queryFn: () => api.booking(bookingRef),
    enabled: bookingRef.length > 0,
    retry: retryUnlessUnavailable,
  });

  const trip = useMemo(() => summariseTrip(booking.data?.segments ?? []), [booking.data]);
  const incidentRef = trip.disruptedSegment?.incident_reference ?? null;

  const incident = useQuery({
    queryKey: ['incident', incidentRef],
    queryFn: () => api.incident(incidentRef as string),
    enabled: incidentRef !== null,
    retry: retryUnlessUnavailable,
  });

  // Secondary: the network-wide priority ranking, kept only for the collapsed technical section.
  const currentGroup = useQuery({ queryKey: ['current-group'], queryFn: api.currentGroup });
  const groupRef = currentGroup.data?.reference ?? '';
  const impacts = useQuery({
    queryKey: ['group-impacts', groupRef, 1000],
    queryFn: () => api.groupImpacts(groupRef, 1000),
    enabled: groupRef.length > 0,
  });
  const priorityLookup = useMemo(
    () =>
      passengerLookup(
        impacts.data?.passengers ?? [],
        bookingRef,
        impacts.data?.returned ?? 0,
        impacts.data?.passengers_assessed ?? 0,
      ),
    [bookingRef, impacts.data],
  );

  if (booking.isLoading) {
    return (
      <PassengerShell>
        <div className="h-[320px]">
          <LoadingState label="Loading your trip" />
        </div>
      </PassengerShell>
    );
  }

  if (booking.error) {
    const unavailable = dataUnavailable(booking.error);
    if (unavailable) {
      return (
        <PassengerShell>
          <EmptyState
            title={`We can't find a trip for ${bookingRef || 'this reference'}`}
            description={unavailable.resolution}
          />
        </PassengerShell>
      );
    }
    const error = booking.error instanceof ApiError ? booking.error : null;
    return (
      <PassengerShell>
        <ErrorState
          code={error?.code ?? 'BOOKING_UNAVAILABLE'}
          message={error?.message ?? 'Your trip could not be loaded.'}
          correlationId={error?.correlationId ?? null}
          onRetry={() => void booking.refetch()}
        />
      </PassengerShell>
    );
  }

  if (!booking.data) {
    return (
      <PassengerShell>
        <LoadingState label="Loading your trip" />
      </PassengerShell>
    );
  }

  const status = operationalStatus(incident.data?.state, incident.data?.actions);
  const journey = incident.data ? journeyStateFor(incident.data.state, 0) : null;
  const incidentUnavailable = incident.error ? dataUnavailable(incident.error) : null;
  const incidentFailed = incident.error !== null && incidentUnavailable === null;

  return (
    <PassengerShell>
      <div className="flex flex-col gap-6">
        {/* ---------------------------------------------------------------- 1. Trip / flight */}
        <section className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <Luggage size={18} strokeWidth={1.5} className="text-fg-muted" aria-hidden />
            <h1 className="text-title font-semibold text-fg">
              Booking <span className="font-mono">{booking.data.pnr}</span>
            </h1>
          </div>
          <p className="text-body text-fg-secondary">
            {trip.routeLabel || 'No flights are on record for this booking.'}
            {trip.isConnecting ? ' · connecting itinerary' : ''}
          </p>

          <ul className="mt-1 flex flex-col divide-y divide-border-subtle rounded border border-border-subtle">
            {trip.segments.map((segment) => {
              const delay = durationFromMinutes(segment.delay_minutes);
              const isDisrupted = segment.incident_reference !== null;
              return (
                <li
                  key={segment.flight_id}
                  className={
                    'flex flex-wrap items-center justify-between gap-2 px-3 py-2.5' +
                    (isDisrupted ? ' bg-state-warn-bg' : '')
                  }
                >
                  <div className="flex items-center gap-3">
                    <MonoValue>{segment.flight_number}</MonoValue>
                    <span className="text-body text-fg">
                      {segment.origin_icao} → {segment.destination_icao}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-caption text-fg-muted">
                      scheduled {utcMinute(segment.scheduled_departure) ?? 'not recorded'}Z
                    </span>
                    {segment.delay_minutes > 0 && delay && (
                      <span className="text-caption text-state-warn">delayed {delay}</span>
                    )}
                    <StateBadge status={segment.status} label={segment.status.replace(/_/g, ' ')} />
                  </div>
                </li>
              );
            })}
          </ul>
        </section>

        {/* ---------------------------------------------------------------- 2. Current disruption */}
        <section className="flex flex-col gap-2">
          <SectionTitle>Current disruption</SectionTitle>
          {trip.disruptedSegment ? (
            <p className="text-body text-fg">
              Flight {trip.disruptedSegment.flight_number} ({trip.disruptedSegment.origin_icao} →{' '}
              {trip.disruptedSegment.destination_icao}) is{' '}
              {trip.disruptedSegment.status.replace(/_/g, ' ')}
              {trip.disruptedSegment.delay_minutes > 0
                ? ` by ${durationFromMinutes(trip.disruptedSegment.delay_minutes)}`
                : ''}
              .
            </p>
          ) : (
            <p className="text-body text-fg-secondary">
              No disruption is recorded against this trip's flights right now.
            </p>
          )}
        </section>

        {/* ---------------------------------------------------------------- 3. What happened */}
        {trip.disruptedSegment && (
          <section className="flex flex-col gap-2">
            <SectionTitle>What happened</SectionTitle>
            {incident.isLoading ? (
              <p className="text-body text-fg-muted">Loading what we have on record…</p>
            ) : incidentFailed ? (
              <p className="text-body text-fg-muted">
                We can't load the detail behind this disruption right now.
              </p>
            ) : incident.data ? (
              <p className="text-body text-fg-secondary">
                A {incident.data.trigger_type.replace(/_/g, ' ')} disruption was first recorded at{' '}
                {utcStamp(incident.data.opened_at) ?? 'an unrecorded time'}. The cause shown is a
                broad category, not a legal finding.
              </p>
            ) : (
              <p className="text-body text-fg-muted">Nothing further is on record yet.</p>
            )}
          </section>
        )}

        {/* ---------------------------------------------------------------- 4. Current confirmed status */}
        <section className="flex flex-col gap-2">
          <SectionTitle>Current confirmed status</SectionTitle>
          {journey ? (
            <div className="flex flex-col gap-1.5">
              <StateBadge status={journey.token} label={journey.label} className="self-start" />
              <p className="text-body text-fg">{journey.headline}</p>
              <p className="text-body text-fg-secondary">{journey.detail}</p>
            </div>
          ) : (
            <p className="text-body text-fg-secondary">
              {trip.disruptedSegment
                ? 'We are still loading the current status.'
                : 'Nothing is currently affecting this trip.'}
            </p>
          )}
          <Notice tone="muted" divider="none" className="mt-1 rounded border">
            The following has not been confirmed for this booking: a rebooking, a new flight, a seat
            change, a refund, a hotel reservation, or ground transport. This page only shows a
            change of that kind once it is actually on record.
          </Notice>
        </section>

        {/* ---------------------------------------------------------------- 5. What TravelOps is doing */}
        <section className="flex flex-col gap-2">
          <SectionTitle>What TravelOps is doing</SectionTitle>
          {!trip.disruptedSegment ? (
            <p className="text-body text-fg-secondary">
              There is nothing in progress for this trip right now.
            </p>
          ) : incident.isLoading ? (
            <p className="text-body text-fg-muted">Checking on the latest progress…</p>
          ) : status.workflowResolved ? (
            <div className="flex flex-col gap-2">
              <p className="flex items-center gap-1.5 text-body text-fg">
                <CheckCircle2 size={15} strokeWidth={1.5} className="text-state-ok" aria-hidden />
                Operational workflow: resolved.
              </p>
              {status.passengerImpactOutstanding ? (
                <>
                  <p className="flex items-center gap-1.5 text-body text-state-warn">
                    <AlertTriangle size={15} strokeWidth={1.5} aria-hidden />
                    Passenger impact: still outstanding.
                  </p>
                  <p className="text-body text-fg-secondary">
                    Review finished, but the following still needs a person to complete
                    {trip.disruptedSegment ? ` for ${trip.disruptedSegment.flight_number}` : ''}:
                  </p>
                  {/*
                    The flight number belongs beside these sentences.

                    Each one is a service's own words, and they routinely contain a bare ratio —
                    "0 of 79 rooms secured". The operator console shows the same sentence for a
                    different flight ("71 of 87"), because accommodation is allocated per flight
                    against one shared inventory and whichever flight runs second sees what is
                    left. Both figures are correct. Presented without the flight they belong to,
                    they read as the same number disagreeing with itself, and a passenger reading
                    "0 of 79" has no way to tell it is about their flight rather than the whole
                    disruption.
                  */}
                  <ul className="flex flex-col gap-1">
                    {status.outstanding.map((item) => (
                      <li key={item.actionId} className="text-body text-fg-secondary">
                        · {item.reason}
                      </li>
                    ))}
                  </ul>
                  {trip.disruptedSegment && (
                    <p className="text-caption text-fg-muted">
                      These figures cover {trip.disruptedSegment.flight_number} (
                      {trip.disruptedSegment.origin_icao} to{' '}
                      {trip.disruptedSegment.destination_icao}) only, not every flight affected by
                      this weather.
                    </p>
                  )}
                </>
              ) : (
                <p className="text-body text-fg-secondary">
                  Nothing further is recorded as outstanding for this trip.
                </p>
              )}
            </div>
          ) : (
            <p className="text-body text-fg-secondary">
              TravelOps is still working through this disruption. Nothing here is confirmed until
              the review finishes.
            </p>
          )}
        </section>

        {/* ---------------------------------------------------------------- 6. What to expect next */}
        <section className="flex flex-col gap-2">
          <SectionTitle>What to expect next</SectionTitle>
          <p className="text-body text-fg-secondary">
            {!trip.disruptedSegment
              ? "We'll show a disruption here as soon as one is recorded against this trip."
              : !status.workflowResolved
                ? "We'll keep this page updated as the review continues. You do not need to do anything here unless your airline contacts you directly."
                : status.passengerImpactOutstanding
                  ? "A person is completing the last steps for this disruption. We'll update this page once that finishes."
                  : 'This disruption is closed on our side. If your travel plans need to change, your airline will contact you directly.'}
          </p>
        </section>

        {/* ---------------------------------------------------------------- 7. Reassurance */}
        <Notice tone="muted" divider="none" className="rounded border">
          This page reflects the same records TravelOps operations staff use — nothing here is
          estimated just for display. If something changes, it will appear here once it is recorded,
          not before.
        </Notice>

        {/* ---------------------------------------------------------------- 8. Last updated / source */}
        <section className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border-subtle pt-3 text-caption text-fg-muted">
          <span>
            trip last updated {utcStamp(trip.segments[0]?.scheduled_departure) ?? 'not recorded'}
          </span>
          {incident.data && (
            <span>disruption recorded {utcStamp(incident.data.opened_at) ?? 'not recorded'}</span>
          )}
        </section>

        {/* -------------------------------------------------- secondary: priority + technical detail */}
        <Panel title="More detail">
          <details>
            <summary className="cursor-pointer px-3 py-3 text-body font-medium text-fg focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
              View how this booking was prioritised, plus references and sources
            </summary>
            <div className="border-t border-border-subtle">
              <PanelBody gap="tight">
                {priorityLookup.passenger ? (
                  <>
                    <div className="flex flex-wrap items-center gap-3">
                      <StateBadge
                        status={priorityLookup.passenger.priority_band}
                        label={priorityLookup.passenger.priority_band}
                      />
                      <Labelled label="priority index">
                        <MonoValue>{priorityLookup.passenger.priority_index}</MonoValue>
                      </Labelled>
                      <Labelled label="priority rule">
                        <MonoValue muted>{priorityLookup.passenger.rule_version}</MonoValue>
                      </Labelled>
                    </div>
                    <Notice tone="muted" divider="none" className="rounded border">
                      This ranking shows who may have fewer remaining options, not who matters more.
                      It is not a probability and does not confirm or authorise a booking change.
                    </Notice>
                    {priorityLookup.passenger.factors.length > 0 && (
                      <TimelineList label="Recorded priority factors">
                        {priorityLookup.passenger.factors.map((factor, index) => (
                          <TimelineItem
                            key={`${factor.factor}-${factor.source}`}
                            tone="info"
                            isLast={index === priorityLookup.passenger!.factors.length - 1}
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
                    )}
                  </>
                ) : (
                  <p className="text-body text-fg-secondary">
                    Priority information is not available for this booking right now.
                  </p>
                )}

                <div className="flex flex-wrap gap-x-5 gap-y-2 border-t border-border-subtle pt-3">
                  <Labelled label="booking reference">
                    <MonoValue>{booking.data.pnr}</MonoValue>
                  </Labelled>
                  <Labelled label="cabin">
                    <MonoValue muted>{booking.data.cabin}</MonoValue>
                  </Labelled>
                  {incidentRef && (
                    <Labelled label="disruption reference">
                      <MonoValue muted>{incidentRef}</MonoValue>
                    </Labelled>
                  )}
                  {impacts.data && (
                    <>
                      <Labelled label="priority basis">
                        <MonoValue muted>{impacts.data.basis}</MonoValue>
                      </Labelled>
                      <Labelled label="priority computed">
                        <MonoValue muted>
                          {utcStamp(impacts.data.computed_at) ?? 'not recorded'}
                        </MonoValue>
                      </Labelled>
                      <Labelled label="ruleset hash" className="min-w-0">
                        <MonoValue muted className="break-all">
                          {impacts.data.ruleset_hash}
                        </MonoValue>
                      </Labelled>
                    </>
                  )}
                </div>

                <div className="flex flex-wrap items-center gap-2 border-t border-border-subtle pt-3">
                  {trip.segments.map((segment) => (
                    <span key={segment.flight_id} className="flex items-center gap-1.5">
                      <ProvenanceDot
                        kind={segment.provenance.kind}
                        provider={segment.provenance.provider}
                        sourceRef={segment.provenance.source_ref}
                      />
                      <MonoValue muted>{segment.flight_number}</MonoValue>
                    </span>
                  ))}
                </div>
              </PanelBody>
            </div>
          </details>
        </Panel>
      </div>
    </PassengerShell>
  );
}

/**
 * Passenger disruption view — the one screen in this product whose reader is not an operator.
 *
 * It renders `GET /passenger/{booking_ref}/disruption`, which is a projection of recorded rows. That
 * makes the design problem here different from every other surface: the reader cannot cross-check
 * anything, so the screen's job is to be *narrower* than the operator console, not friendlier.
 *
 * What that means concretely, and what changed when this stopped being a sample:
 *
 *   - **No name.** The contract has no field for one. The header shows the PNR the reader already
 *     holds and the pseudonymous `PAX-…` reference an agent can quote back.
 *   - **No compensation figure, and no note promising one.** The old screen rendered an
 *     `entitlement_note` explaining where a figure would come from. The endpoint carries no money
 *     field at all, so there is nothing to caption; the policy surface owns that answer.
 *   - **No invented consequences.** The old screen listed hand-written impacts — bags, an overnight
 *     — that no row supported. `deriveConsequences` now builds the list from the recorded connection
 *     assessment and the recorded priority factors, and shows an honest empty state when neither
 *     exists.
 *   - **Every option states its basis.** A reachable later departure is not a seat, and the row says
 *     so next to the option rather than in a footnote.
 *
 * Owner: Stream D.
 */

import { useMemo } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowRight,
  Check,
  CircleDot,
  Clock,
  Hotel,
  Plane,
  RefreshCcw,
  X,
} from 'lucide-react';
import { clsx } from 'clsx';

import { api, ApiError } from '@/api/client';
import type { PassengerOption, PassengerSegmentOut } from '@/api/types';
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
import { utcMinute } from '@/components/ui/format';
import {
  actionLabel,
  deriveActionProgress,
  deriveConsequences,
  deriveNextStep,
  deriveTripStatus,
  formatDelay,
  openConsequenceCount,
  optionBasisNote,
  summariseOptions,
} from './passengerView';

const OPTION_ICON: Record<PassengerOption['kind'], typeof Plane> = {
  alternative_flight: Plane,
  hotel_room: Hotel,
};

const ACTION_ICON = {
  succeeded: Check,
  executing: RefreshCcw,
  pending: CircleDot,
  awaiting_approval: AlertTriangle,
  failed: X,
  needs_human: AlertTriangle,
} as const;

/*
 * `Labelled` is imported from `@/components/ui/composition`. The rule it encodes is unchanged and
 * still load-bearing: `uppercase` goes on the LABEL only, because on the wrapper it case-transformed
 * the values inside — the recorded cause token `weather` became `WEATHER` and `not assigned` became
 * `NOT ASSIGNED`, strings the contract never published. A guard in `scenarioDraft.test.ts` scans
 * this file for that shape.
 */

/** `label N of M`, with `uppercase` kept off both figures for the same reason as `Labelled`. */
function Ratio({ label, value, total }: { label: string; value: number; total: number }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-caption uppercase text-fg-muted">{label}</span>
      <MonoValue muted>{value}</MonoValue>
      <span className="text-caption uppercase text-fg-muted">of</span>
      <MonoValue muted>{total}</MonoValue>
    </span>
  );
}

/**
 * A scheduled time and its revision, with the revision marked.
 *
 * The original stays on screen struck through rather than being replaced, because a passenger
 * checking a screen against a boarding pass needs to see both to trust either.
 */
function TimePair({ scheduled, revised }: { scheduled: string; revised: string | null }) {
  const scheduledClock = utcMinute(scheduled);
  const revisedClock = utcMinute(revised);

  return (
    <span className="flex items-baseline gap-1.5">
      <MonoValue muted className={revisedClock ? 'line-through' : undefined}>
        {scheduledClock ?? 'not published'}
      </MonoValue>
      {revisedClock && (
        <>
          <ArrowRight size={10} strokeWidth={1.5} className="text-fg-muted" aria-hidden />
          <MonoValue className="text-state-warn">{revisedClock}</MonoValue>
        </>
      )}
    </span>
  );
}

function SegmentRow({ segment }: { segment: PassengerSegmentOut }) {
  const delay = formatDelay(segment.delay_minutes);

  return (
    <li
      className={clsx(
        'flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border-subtle px-3 py-2',
        segment.is_disrupted && 'bg-inset',
      )}
    >
      <span className="flex items-center gap-2">
        <Plane size={12} strokeWidth={1.5} className="text-fg-muted" aria-hidden />
        <MonoValue>{segment.flight_number}</MonoValue>
      </span>

      {/* ICAO, because that is what the record carries. No IATA mapping is invented here. */}
      <span className="flex items-baseline gap-1.5">
        <MonoValue muted>{segment.origin_icao}</MonoValue>
        <ArrowRight size={10} strokeWidth={1.5} className="text-fg-muted" aria-hidden />
        <MonoValue muted>{segment.destination_icao}</MonoValue>
      </span>

      <span className="flex items-baseline gap-1.5">
        <span className="text-caption uppercase text-fg-muted">departs</span>
        <TimePair scheduled={segment.scheduled_departure} revised={segment.estimated_departure} />
      </span>

      <span className="flex items-baseline gap-1.5">
        <span className="text-caption uppercase text-fg-muted">arrives</span>
        <TimePair scheduled={segment.scheduled_arrival} revised={null} />
      </span>

      <StateBadge status={segment.status} label={segment.status.replace(/_/g, ' ')} />

      {/*
        An unpublished delay is named, not printed as zero. `formatDelay` returns null for absent and
        the string "on time" for a real zero, so the two cannot render the same.
      */}
      <span className="flex items-center gap-1.5">
        <Clock size={11} strokeWidth={1.5} className="text-fg-muted" aria-hidden />
        {delay === null ? (
          <span className="text-caption text-fg-muted" title="No revised time has been published.">
            no new time yet
          </span>
        ) : (
          <span
            className={clsx(
              'text-caption',
              segment.delay_minutes && segment.delay_minutes > 0
                ? 'text-state-warn'
                : 'text-fg-secondary',
            )}
          >
            {delay}
          </span>
        )}
      </span>

      <span className="ml-auto">
        <Labelled label="gate">
          <MonoValue muted>{segment.gate ?? 'not assigned yet'}</MonoValue>
        </Labelled>
      </span>
    </li>
  );
}

/**
 * One recorded option, with the basis it was recorded under.
 *
 * The basis line is not a footnote and not a tooltip. A reachable departure presented without it
 * reads as an available seat, which is the single most damaging thing this screen could imply.
 */
function OptionRow({ option }: { option: PassengerOption }) {
  const Icon = OPTION_ICON[option.kind];
  const firm = option.basis === 'recorded_reservation';

  return (
    <li className="flex items-start gap-2 border-b border-border-subtle px-3 py-2">
      <Icon
        size={14}
        strokeWidth={1.5}
        className={clsx('mt-0.5 shrink-0', firm ? 'text-accent' : 'text-fg-muted')}
        aria-hidden
      />
      <div className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-body text-fg">{option.label}</span>
          {option.requires_agent && <StateBadge status="needs_human" label="agent will arrange" />}
          {!firm && <StateBadge status="skipped" label="not a confirmed booking" />}
        </span>
        <p className="mt-0.5 text-caption text-fg-secondary">{optionBasisNote(option)}</p>
        {option.scheduled_departure && (
          <p className="mt-0.5 flex items-baseline gap-1.5 text-caption text-fg-muted">
            <span className="uppercase">departs</span>
            <MonoValue muted>{utcMinute(option.scheduled_departure) ?? 'not published'}</MonoValue>
          </p>
        )}
        {option.nights !== null && (
          <p className="mt-0.5 flex items-baseline gap-1.5 text-caption text-fg-muted">
            <span className="uppercase">nights</span>
            <MonoValue muted>{option.nights}</MonoValue>
          </p>
        )}
      </div>
    </li>
  );
}

export function PassengerDisruptionView() {
  const { bookingRef = '' } = useParams();

  const viewQuery = useQuery({
    queryKey: ['passenger-disruption', bookingRef],
    queryFn: () => api.passengerDisruption(bookingRef),
    enabled: bookingRef !== '',
  });

  const view = viewQuery.data;

  const tripStatus = useMemo(() => (view ? deriveTripStatus(view) : null), [view]);
  const nextStep = useMemo(() => (view ? deriveNextStep(view) : null), [view]);
  const options = useMemo(() => (view ? summariseOptions(view.options) : null), [view]);
  const progress = useMemo(() => (view ? deriveActionProgress(view.actions) : null), [view]);
  const consequences = useMemo(() => (view ? deriveConsequences(view) : []), [view]);

  /*
   * The wording is the one thing that changes for this audience. A reader with no ledger to check
   * against needs to be told their trip could not be loaded — not handed a correlation id and an
   * error code as the headline. A 404 is separated from a fault, because "we do not hold that
   * reference" is actionable and "something broke" is not.
   */
  if (viewQuery.error) {
    const notFound = viewQuery.error instanceof ApiError && viewQuery.error.status === 404;
    return (
      <Panel title="Your trip">
        <ErrorState
          code={viewQuery.error instanceof ApiError ? viewQuery.error.code : 'INTERNAL_ERROR'}
          message={
            notFound
              ? 'We could not find a booking with that reference. Check it against your ticket, ' +
                'or contact the airline if it looks right.'
              : 'We could not load your trip just now. Nothing about your booking has changed, ' +
                'and your options are still open. Please try again.'
          }
          correlationId={viewQuery.error instanceof ApiError ? viewQuery.error.correlationId : null}
          onRetry={notFound ? undefined : () => void viewQuery.refetch()}
        />
      </Panel>
    );
  }

  if (viewQuery.isLoading || !view || !tripStatus || !nextStep || !options || !progress) {
    return (
      <Panel title="Your trip">
        <LoadingState label="Loading your trip" />
      </Panel>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <PageHeader
        eyebrow="Your trip"
        title={<span className="font-mono tabular-nums">{view.booking_ref}</span>}
        status={<StateBadge status={tripStatus.token} label={tripStatus.label} />}
        meta={
          <>
            {/* Pseudonymous by contract. There is no name field to render. */}
            <Labelled label="reference">
              <MonoValue muted>{view.passenger_reference}</MonoValue>
            </Labelled>
            <Labelled label="cabin">
              <MonoValue muted>{view.cabin}</MonoValue>
            </Labelled>
            <Labelled label="route">
              <MonoValue muted>
                {view.trip.origin_icao} to {view.trip.destination_icao}
              </MonoValue>
            </Labelled>
            <Ratio
              label="legs disrupted"
              value={tripStatus.disruptedSegments}
              total={tripStatus.totalSegments}
            />
          </>
        }
        actions={
          <Toolbar>
            {/*
              `uppercase` sits on the LITERAL label only. It used to wrap the whole line, which
              case-transformed `provenance.kind` — the contract value `synthetic` reached the screen
              as `SYNTHETIC`, a string the API never published. The console verifier's passenger
              case-transform check caught exactly that.
            */}
            <span className="flex items-center gap-1.5">
              <ProvenanceDot
                kind={view.provenance.kind}
                provider={view.provenance.provider}
                sourceRef={view.provenance.source_ref ?? undefined}
              />
              <span className="text-caption uppercase text-fg-muted">source</span>
              <MonoValue muted>{view.provenance.kind}</MonoValue>
            </span>
          </Toolbar>
        }
      />

      <div className="grid items-start gap-3 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="flex min-w-0 flex-col gap-3">
          {/* What happened, from the recorded incident. No narrative is composed for it. */}
          <Panel title="What happened">
            <PanelBody gap="tight">
              {view.disruption === null ? (
                <EmptyState
                  title="Nothing is recorded against this trip"
                  description="No disruption has been opened on any flight in this booking."
                />
              ) : (
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
                  <Labelled label="cause">
                    <MonoValue>{view.disruption.cause_category}</MonoValue>
                  </Labelled>
                  <Labelled label="airport">
                    <MonoValue muted>{view.disruption.airport_icao}</MonoValue>
                  </Labelled>
                  <Labelled label="flight">
                    <MonoValue muted>{view.disruption.flight_number}</MonoValue>
                  </Labelled>
                  <Labelled label="opened">
                    <MonoValue muted>
                      {utcMinute(view.disruption.opened_at) ?? 'not published'}
                    </MonoValue>
                  </Labelled>
                  <StateBadge status={view.disruption.state} />
                </div>
              )}
            </PanelBody>
          </Panel>

          <Panel
            title="Your flights"
            actions={
              <Ratio
                label="legs"
                value={tripStatus.totalSegments}
                total={tripStatus.totalSegments}
              />
            }
          >
            {view.trip.segments.length === 0 ? (
              <EmptyState
                title="No flights are recorded"
                description="This booking has no segments against it."
              />
            ) : (
              <ul>
                {view.trip.segments.map((segment) => (
                  <SegmentRow key={segment.segment_id} segment={segment} />
                ))}
              </ul>
            )}
          </Panel>

          <Panel
            title="What this means for you"
            actions={
              <Ratio
                label="open"
                value={openConsequenceCount(consequences)}
                total={consequences.length}
              />
            }
          >
            {consequences.length === 0 ? (
              <EmptyState
                title="No consequences are recorded yet"
                description="Nothing has been assessed against your booking so far. This is not the same as nothing being wrong."
              />
            ) : (
              <ul>
                {consequences.map((entry) => (
                  <li
                    key={entry.key}
                    className="flex items-start gap-2 border-b border-border-subtle px-3 py-2"
                  >
                    <AlertTriangle
                      size={13}
                      strokeWidth={1.5}
                      className="mt-0.5 shrink-0 text-state-warn"
                      aria-hidden
                    />
                    <div className="min-w-0 flex-1">
                      <p className="text-body text-fg">{entry.label}</p>
                      <p className="mt-0.5 text-caption text-fg-secondary">{entry.detail}</p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel
            title="Your options"
            actions={<Ratio label="recorded" value={options.total} total={options.total} />}
          >
            {view.options.length === 0 ? (
              <EmptyState
                title="No options are recorded yet"
                description="Nothing has been held or found for this booking. Any alternative appears here once it is recorded."
              />
            ) : (
              <ul>
                {view.options.map((option, index) => (
                  <OptionRow
                    key={`${option.kind}-${option.flight_id ?? option.hotel_name ?? index}`}
                    option={option}
                  />
                ))}
              </ul>
            )}
          </Panel>
        </div>

        <div className="flex min-w-0 flex-col gap-3">
          <Panel title="What happens next">
            <PanelBody gap="tight">
              <div className="flex items-center gap-2">
                <StateBadge status={nextStep.token} />
                {view.next_step.driven_by_action_type && (
                  <MonoValue muted className="text-caption">
                    {view.next_step.driven_by_action_type}
                  </MonoValue>
                )}
              </div>
              <p className="text-subtitle text-fg">{nextStep.headline}</p>
              <p className="text-body text-fg-secondary">{nextStep.detail}</p>
              {nextStep.awaitingDecision && (
                <Notice tone="warn">
                  Nothing on your booking has changed while this is waiting for a person.
                </Notice>
              )}
            </PanelBody>
          </Panel>

          <Panel
            title="What we have done"
            actions={<Ratio label="done" value={progress.done} total={progress.total} />}
          >
            {view.actions.length === 0 ? (
              <EmptyState
                title="No steps are recorded"
                description="No recovery work has been recorded for this disruption yet."
              />
            ) : (
              <div className="py-1.5">
                <TimelineList label="Recovery steps for this booking">
                  {view.actions.map((action, index) => {
                    const Icon = ACTION_ICON[action.state as keyof typeof ACTION_ICON] ?? CircleDot;
                    return (
                      <TimelineItem
                        key={`${action.action_type}-${index}`}
                        tone={action.awaiting_human ? 'accent' : 'muted'}
                        time={utcMinute(action.at) ?? '—'}
                        isLast={index === view.actions.length - 1}
                        className="px-3"
                      >
                        <span className="flex flex-wrap items-center gap-2">
                          <Icon
                            size={12}
                            strokeWidth={1.5}
                            className="shrink-0 text-fg-muted"
                            aria-hidden
                          />
                          <span className="text-body text-fg">{actionLabel(action)}</span>
                          <StateBadge
                            status={action.state}
                            label={action.state.replace(/_/g, ' ')}
                          />
                        </span>
                        {/*
                          Scope, stated. "We checked your connection" is true at incident scope;
                          only a row naming this booking makes something theirs.
                        */}
                        <p className="mt-0.5 text-caption text-fg-muted">
                          {action.applies_to === 'this_booking'
                            ? 'Recorded against your booking.'
                            : 'Part of the wider recovery for this flight.'}
                        </p>
                        {action.reason_code && (
                          <MonoValue muted className="text-caption">
                            {action.reason_code}
                          </MonoValue>
                        )}
                        {action.approval_scope && (
                          <p className="mt-0.5 text-caption text-fg-muted">
                            Approved by a person
                            {action.approval_scope === 'plan' ? ' as part of the whole plan' : ''}.
                          </p>
                        )}
                      </TimelineItem>
                    );
                  })}
                </TimelineList>
              </div>
            )}
          </Panel>

          {/*
            Factors nothing has established. Named rather than rendered false, so this screen never
            tells a reader nobody needs rebooking when nobody has looked.
          */}
          {view.unassessed_factors.length > 0 && (
            <Panel title="Not yet assessed">
              <ul className="flex flex-col gap-2 px-3 py-2">
                {view.unassessed_factors.map((factor) => (
                  <li key={factor.factor} className="flex flex-col gap-0.5">
                    <MonoValue muted className="text-caption">
                      {factor.factor.replace(/_/g, ' ')}
                    </MonoValue>
                    <span className="text-caption text-fg-muted">{factor.reason}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          {/*
            The boundary of what any contract publishes, stated on the screen.
            
            Carried forward from the group-derived version of this view, and narrowed to match what
            the booking endpoint now does publish: recorded actions, recorded rooms and schedule-
            feasible alternatives are here. A CONFIRMED rebooking, a seat, a refund, an entitlement
            figure and notification delivery are not, because nothing records them per booking. The
            most damaging thing this page could do is let a resolved workflow read as a changed
            ticket, so it says otherwise in as many words.
          */}
          <Panel title="Booking outcome">
            <PanelBody gap="tight">
              <StateBadge status="unavailable" label="not published" />
              <p className="text-subtitle text-fg">No confirmed booking change is published</p>
              <p className="text-body text-fg-secondary">
                This page shows the recovery work recorded against your flight. No contract records
                a confirmed rebooking, seat, refund, entitlement amount, or notification delivery
                for this booking, so none is shown.
              </p>
            </PanelBody>
            <Notice tone="muted">
              A resolved disruption means the operational workflow finished. It does not mean your
              booking was changed.
            </Notice>
          </Panel>

          <Panel title="About this page">
            <PanelBody gap="tight">
              {/*
                The contract's own basis token, verbatim and un-cased. It is the machine-readable
                claim about where every figure above came from, so rendering it transformed would
                misreport the contract — the console verifier asserts its exact casing.
              */}
              <Labelled label="basis">
                <MonoValue>{view.basis}</MonoValue>
              </Labelled>
            </PanelBody>
            <Notice tone="muted" icon={false}>
              {view.note}
            </Notice>
          </Panel>
        </div>
      </div>
    </div>
  );
}

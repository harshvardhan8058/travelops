/**
 * Passenger disruption view — `/passenger/:bookingRef`. Phase 5.
 *
 * The same disruption the operations console is working, told to the person it is happening to. It
 * answers four questions in the order a passenger actually asks them: what is happening to my trip,
 * why, what does it mean for me, and what happens next.
 *
 * This screen is where the product's honesty rules matter most, because the reader has no ledger to
 * cross-check against. Four things it will not do:
 *
 *   1. **It states no amount of money.** An entitlement is computed by the policy engine from a
 *      reviewed pack, and the console is forbidden from calculating one. The screen renders the
 *      contract's own `entitlement_note` verbatim and says where the figure comes from instead.
 *   2. **It confirms nothing a human has not approved.** A rebooking waiting on a decision reads as
 *      waiting. Telling a passenger they are rebooked because a plan exists would be the worst
 *      version of the state-transition-that-never-happened defect.
 *   3. **It never prints a zero for an unknown.** `delay_minutes: null` means no revision has been
 *      published, which is a different sentence from "on time", and the two are rendered differently.
 *   4. **It shows no probability.** Risk on the real contract is an uncalibrated band; a percentage
 *      here would be a number the reader cannot challenge.
 *
 * No endpoint serves this yet. The payload is the worked sample in `passengerContracts.ts`, carried
 * through react-query so the seam is a one-line change when the endpoint lands, and its synthetic
 * provenance is rendered rather than hidden. All folding lives in `passengerView.ts`, which is
 * unit-tested; this file is rendering only.
 *
 * Owner: Stream D.
 */

import { useMemo } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle,
  ArrowRight,
  Ban,
  Check,
  CircleDot,
  Clock,
  Hotel,
  Plane,
  RefreshCcw,
  Ticket,
  Utensils,
} from 'lucide-react';
import { clsx } from 'clsx';

import {
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
  PASSENGER_SAMPLE,
  passengerApi,
  type PassengerDisruptionResponse,
  type PassengerOption,
  type PassengerOptionKind,
  type PassengerSegment,
} from './passengerContracts';
import {
  deriveActionProgress,
  deriveNextStep,
  deriveTripStatus,
  formatDelay,
  openImpactCount,
  orderImpacts,
  summariseOptions,
} from './passengerView';

const OPTION_ICON: Record<PassengerOptionKind, typeof Plane> = {
  rebook: Plane,
  refund: Ticket,
  hotel: Hotel,
  meal: Utensils,
  transport: ArrowRight,
};

/*
 * `Labelled` is imported from `@/components/ui/composition`, where the third copy of it now lives
 * once. The rule it encodes is unchanged and still load-bearing: `uppercase` goes on the LABEL only,
 * because on the wrapper it case-transformed the values inside — the recorded cause token `weather`
 * became `WEATHER` and `not assigned` became `NOT ASSIGNED`, strings the contract never published.
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

/*
 * Times come from `utcMinute` in `@/components/ui/format`. One zone across the whole product, so two
 * rows can never be compared wrongly, and an unparseable instant returns null rather than
 * `Invalid Date`. A passenger-facing screen is the last place that should render a JavaScript error
 * string as if it were a departure time.
 */

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

function SegmentRow({ segment }: { segment: PassengerSegment }) {
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

      <span className="flex items-baseline gap-1.5">
        <MonoValue muted>{segment.origin_iata}</MonoValue>
        <ArrowRight size={10} strokeWidth={1.5} className="text-fg-muted" aria-hidden />
        <MonoValue muted>{segment.destination_iata}</MonoValue>
      </span>

      <span className="flex items-baseline gap-1.5">
        <span className="text-caption uppercase text-fg-muted">departs</span>
        <TimePair scheduled={segment.scheduled_departure} revised={segment.revised_departure} />
      </span>

      <span className="flex items-baseline gap-1.5">
        <span className="text-caption uppercase text-fg-muted">arrives</span>
        <TimePair scheduled={segment.scheduled_arrival} revised={segment.revised_arrival} />
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

function OptionRow({ option }: { option: PassengerOption }) {
  const Icon = OPTION_ICON[option.kind];

  return (
    <li className="flex items-start gap-2 border-b border-border-subtle px-3 py-2">
      <Icon
        size={14}
        strokeWidth={1.5}
        className={clsx('mt-0.5 shrink-0', option.available ? 'text-accent' : 'text-fg-muted')}
        aria-hidden
      />
      <div className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-2">
          <span className={clsx('text-body', option.available ? 'text-fg' : 'text-fg-muted')}>
            {option.label}
          </span>
          {option.available ? (
            option.requires_agent && <StateBadge status="needs_human" label="agent will arrange" />
          ) : (
            <StateBadge status="skipped" label="not offered" />
          )}
        </span>
        <p className="mt-0.5 text-caption text-fg-secondary">{option.detail}</p>
        {/*
          An unavailable option always states why. A greyed row with no reason reads as a fault in the
          page rather than a fact about the trip.
        */}
        {!option.available && (
          <p className="mt-0.5 flex items-start gap-1.5 text-caption text-fg-muted">
            <Ban size={11} strokeWidth={1.5} className="mt-0.5 shrink-0" aria-hidden />
            {option.unavailable_reason ?? 'No reason was published for this option.'}
          </p>
        )}
      </div>
    </li>
  );
}

const ACTION_ICON = {
  succeeded: Check,
  executing: RefreshCcw,
  pending: CircleDot,
  awaiting_approval: AlertTriangle,
} as const;

export function PassengerDisruptionView() {
  const { bookingRef = '' } = useParams();

  /*
   * One async boundary, so the day `GET /passenger/{ref}/disruption` exists this becomes
   * `queryFn: () => api.passengerDisruption(bookingRef)` and nothing else on the screen changes.
   */
  const viewQuery = useQuery<PassengerDisruptionResponse>({
    queryKey: ['passenger-disruption', bookingRef],
    queryFn: async () => ({
      ...PASSENGER_SAMPLE,
      booking_ref: bookingRef || PASSENGER_SAMPLE.booking_ref,
    }),
  });

  const view = viewQuery.data;

  const tripStatus = useMemo(() => (view ? deriveTripStatus(view) : null), [view]);
  const nextStep = useMemo(() => (view ? deriveNextStep(view) : null), [view]);
  const options = useMemo(() => (view ? summariseOptions(view.options) : null), [view]);
  const progress = useMemo(
    () => (view ? deriveActionProgress(view.travelops_actions) : null),
    [view],
  );
  const impacts = useMemo(() => (view ? orderImpacts(view.impacts) : []), [view]);

  /*
   * An error branch, which this screen did not have.
   *
   * The query cannot fail today — its `queryFn` resolves a local sample — but the seam above exists
   * precisely so that it becomes a network call, and the day it does, the previous code would have
   * held a passenger on a spinner forever. The wording is the one thing that changes for this
   * audience: a reader with no ledger to check against needs to be told the page could not load
   * their trip, not handed a correlation id and an error code as the headline.
   */
  if (viewQuery.error) {
    return (
      <Panel title="Your trip">
        <ErrorState
          code="TRIP_UNAVAILABLE"
          message="We could not load your trip just now. Nothing about your booking has changed, and your options are still open. Please try again."
          correlationId={null}
          onRetry={() => void viewQuery.refetch()}
        />
      </Panel>
    );
  }

  if (viewQuery.isLoading || !view || !tripStatus || !nextStep || !options || !progress) {
    return (
      <Panel title="Your trip">
        <div className="h-[420px]">
          <LoadingState label="Loading your trip" />
        </div>
      </Panel>
    );
  }

  return (
    <div className="flex min-h-0 flex-col gap-3">
      {/*
       * The trip is the subject, so the route is the title — at `text-title` rather than the
       * `text-subtitle` it shared with every panel heading on the page. The passenger's own name
       * reads as a name here rather than as a 12px uppercase caption, which is what it was.
       */}
      <PageHeader
        eyebrow="Your trip"
        title={`${view.trip.origin_iata} to ${view.trip.destination_iata}`}
        status={<StateBadge status={tripStatus.token} label={tripStatus.label} />}
        meta={
          <>
            <Labelled label="booking">
              <MonoValue>{view.booking_ref}</MonoValue>
            </Labelled>
            <Labelled label="passenger">
              <span className="text-body text-fg-secondary">{view.passenger_name}</span>
            </Labelled>
            <Labelled label="flights">
              <MonoValue muted>{tripStatus.totalSegments}</MonoValue>
            </Labelled>
          </>
        }
        actions={
          <Toolbar>
            <ProvenanceDot
              kind={view.provenance.kind}
              provider={view.provenance.provider}
              sourceRef={view.provenance.source_ref}
            />
            <span className="text-caption uppercase text-fg-muted">
              {passengerApi.isLive ? 'live booking' : 'sample booking, no service behind it'}
            </span>
          </Toolbar>
        }
      />

      <div className="grid min-h-0 gap-3 lg:grid-cols-[minmax(0,1fr)_380px] 2xl:grid-cols-[minmax(0,1fr)_440px]">
        <div className="flex min-h-0 flex-col gap-3">
          {/* What happened. */}
          <Panel title="What happened">
            <div className="px-3 py-3">
              {/*
               * The headline is the sentence the reader came for, so it is the largest thing in the
               * panel. It was `text-body`, identical to the supporting detail beneath it.
               */}
              <p className="text-subtitle text-fg">{view.what_happened.headline}</p>
              <p className="mt-1.5 text-body text-fg-secondary">{view.what_happened.detail}</p>
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border-subtle px-3 py-2">
              {/*
                `uppercase` sits on each LABEL, never on the wrapper. On the wrapper it case-transformed
                the values inside: the recorded cause token `weather` became `WEATHER`, which is not the
                string the contract published.
              */}
              <Labelled label="cause">
                <MonoValue muted>{view.what_happened.cause_category}</MonoValue>
              </Labelled>
              <Labelled label="recorded">
                <MonoValue muted>
                  {utcMinute(view.what_happened.recorded_at) ?? 'not published'}
                </MonoValue>
              </Labelled>
              <Labelled label="incident">
                <MonoValue muted>{view.incident_reference}</MonoValue>
              </Labelled>
            </div>
          </Panel>

          {/* Flight status, per segment. */}
          <Panel
            title="Your flights"
            actions={
              tripStatus.drivenBy && (
                <Labelled label="status set by">
                  <MonoValue muted>{tripStatus.drivenBy.flight_number}</MonoValue>
                </Labelled>
              )
            }
          >
            <ul>
              {view.trip.segments.map((segment) => (
                <SegmentRow key={segment.segment_ref} segment={segment} />
              ))}
            </ul>
          </Panel>

          {/* Passenger impact. */}
          <Panel
            title="What this means for you"
            actions={
              <Ratio
                label="open"
                value={openImpactCount(view.impacts)}
                total={view.impacts.length}
              />
            }
          >
            <ul>
              {impacts.map((impact) => (
                <li
                  key={impact.impact_ref}
                  className="flex items-start gap-2 border-b border-border-subtle px-3 py-2"
                >
                  {impact.resolved ? (
                    <Check
                      size={12}
                      strokeWidth={1.5}
                      className="mt-1 shrink-0 text-state-ok"
                      aria-hidden
                    />
                  ) : (
                    <AlertTriangle
                      size={12}
                      strokeWidth={1.5}
                      className="mt-1 shrink-0 text-state-warn"
                      aria-hidden
                    />
                  )}
                  <div className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="text-body text-fg">{impact.label}</span>
                      <StateBadge
                        status={impact.resolved ? 'succeeded' : 'at_risk'}
                        label={impact.resolved ? 'handled' : 'open'}
                      />
                    </span>
                    <p className="mt-0.5 text-caption text-fg-secondary">{impact.detail}</p>
                  </div>
                </li>
              ))}
            </ul>
          </Panel>

          {/* Available options. */}
          <Panel
            title="Your options"
            actions={
              <Ratio label="available" value={options.available.length} total={options.total} />
            }
          >
            <ul>
              {view.options.map((option) => (
                <OptionRow key={option.option_ref} option={option} />
              ))}
            </ul>
            {/*
              Where a figure comes from, in the contract's own words. This screen states no amount and
              makes no claim about what the rules require.
            */}
            <Notice tone="muted">{view.entitlement_note}</Notice>
            {options.missingReason.length > 0 && (
              /* A contract defect, surfaced rather than tolerated. */
              <Notice tone="warn">
                {options.missingReason.length} option
                {options.missingReason.length === 1 ? '' : 's'} were marked unavailable without a
                reason, so this page cannot say why.
              </Notice>
            )}
          </Panel>
        </div>

        <div className="flex min-h-0 flex-col gap-3">
          {/* Approval / next-step state. The most important panel on the screen. */}
          <Panel title="What happens next">
            <PanelBody gap="tight">
              <div className="flex flex-wrap items-center gap-2">
                <StateBadge status={nextStep.token} label={nextStep.token.replace(/_/g, ' ')} />
                {nextStep.passengerMustAct ? (
                  <span className="text-caption uppercase text-state-warn">over to you</span>
                ) : (
                  <span className="text-caption uppercase text-fg-muted">
                    nothing for you to do
                  </span>
                )}
              </div>
              {/* The instruction outranks everything else on the page, so it is set largest. */}
              <p className="text-subtitle text-fg">{nextStep.headline}</p>
              <p className="text-body text-fg-secondary">{nextStep.detail}</p>
              {nextStep.respondBy && (
                <p className="flex items-baseline gap-1.5">
                  <span className="text-caption uppercase text-state-warn">respond by</span>
                  <MonoValue className="text-state-warn">{nextStep.respondBy}</MonoValue>
                </p>
              )}
            </PanelBody>
            {nextStep.awaitingDecision && (
              /*
                Said plainly. A passenger told their rebooking is "being reviewed" understands the
                state; one told it is confirmed, when a gate has not cleared, has been misinformed.
              */
              <Notice tone="muted" icon={false} className="bg-inset">
                Changes of this size are approved by a person, not automatically. Nothing on your
                booking has changed yet.
              </Notice>
            )}
          </Panel>

          {/* TravelOps current actions and status. */}
          <Panel
            title="What TravelOps is doing"
            actions={<Ratio label="done" value={progress.done} total={progress.total} />}
          >
            {/*
             * An actual timeline rather than a divided list.
             *
             * These actions are one ordered sequence — that is the whole reassurance the panel
             * offers — and a `divide-y` list of rows states it no more clearly than a table does.
             * The spine and per-entry marker make the sequence legible at a glance, and the marker
             * tone is the action's own state, so "this one is waiting on a person" is visible
             * without reading. The state word is still on every row: tone is never the only signal.
             */}
            <div className="px-3 py-2.5">
              <TimelineList label="What TravelOps is doing">
                {view.travelops_actions.map((action, index) => {
                  const Icon = ACTION_ICON[action.state];
                  const tone =
                    action.state === 'succeeded'
                      ? ('ok' as const)
                      : action.state === 'awaiting_approval'
                        ? ('warn' as const)
                        : action.state === 'executing'
                          ? ('info' as const)
                          : ('muted' as const);

                  return (
                    <TimelineItem
                      key={action.action_ref}
                      tone={tone}
                      // Absent until it starts. Never backfilled with "now".
                      time={utcMinute(action.at)}
                      isLast={index === view.travelops_actions.length - 1}
                    >
                      <span className="flex flex-wrap items-center gap-2">
                        <Icon
                          size={12}
                          strokeWidth={1.5}
                          className="shrink-0 text-fg-muted"
                          aria-hidden
                        />
                        <span className="text-body text-fg">{action.label}</span>
                        <StateBadge status={action.state} label={action.state.replace(/_/g, ' ')} />
                      </span>
                      <p className="mt-1 text-caption text-fg-secondary">{action.detail}</p>
                      {action.at === null && (
                        <p className="mt-0.5 text-caption text-fg-muted">not started yet</p>
                      )}
                    </TimelineItem>
                  );
                })}
              </TimelineList>
            </div>
            {progress.blockedOnPerson && progress.current && (
              <Notice tone="warn">
                Waiting on a person: {progress.current.label.toLowerCase()}.
              </Notice>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}

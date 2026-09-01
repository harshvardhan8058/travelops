/**
 * Proposed contracts for the passenger disruption view — Phase 5.
 *
 * **No endpoint serves these**, and like the scenario contracts they are declared in the consuming
 * feature rather than in `@/api/types` so a speculative shape never sits beside a real one.
 *
 * Two things this contract deliberately does **not** carry, because carrying them would break rules
 * the rest of the product holds:
 *
 *   1. **No money.** An entitlement figure is computed by the policy engine from a reviewed pack,
 *      and `docs/38` reserves the words "current law" to one function in `compensation.py`. A rupee
 *      amount rendered on a passenger screen from a mock would be a locally computed entitlement —
 *      the exact thing the design system forbids. Options describe what is available; the amount and
 *      its legal standing arrive from the policy surface, and until they do the screen says so.
 *   2. **No probabilities.** `RiskLevel` on the real contract is a band with the standing comment
 *      "nothing here is calibrated". A passenger-facing "85% likely to misconnect" would be an
 *      uncalibrated percentage presented to the person least able to challenge it.
 *
 * The sample payload is marked `synthetic` throughout and the screen renders that provenance, so a
 * reader is never left to assume a real booking is on screen.
 *
 * Owner: Stream D.
 */

import type { Provenance } from '@/api/types';

/** Where a segment stands. Mirrors the flight states the real board already publishes. */
export type PassengerSegmentStatus = 'on_time' | 'delayed' | 'cancelled' | 'at_risk' | 'scheduled';

/** What TravelOps is doing, in the passenger's terms rather than the orchestrator's. */
export type PassengerActionState = 'succeeded' | 'executing' | 'pending' | 'awaiting_approval';

/**
 * The passenger's own next move, or the system's.
 *
 * `awaiting_approval` is the honest state for a plan a human has not signed: the passenger is not
 * being asked to do anything, and telling them their rebooking is confirmed would be a state
 * transition that has not happened.
 */
export type PassengerNextStepState =
  'awaiting_approval' | 'action_required' | 'executing' | 'resolved' | 'monitoring';

export interface PassengerSegment {
  segment_ref: string;
  flight_number: string;
  origin_iata: string;
  destination_iata: string;
  scheduled_departure: string;
  /** Null when no revision has been published. Never backfilled with the scheduled time. */
  revised_departure: string | null;
  scheduled_arrival: string;
  revised_arrival: string | null;
  status: PassengerSegmentStatus;
  /** Null when no delay has been published. Distinct from zero, which means on time. */
  delay_minutes: number | null;
  gate: string | null;
  /** True when this segment is the one the disruption started on. */
  is_disrupted: boolean;
}

export interface PassengerWhatHappened {
  headline: string;
  detail: string;
  /** The operational cause as recorded, never a legal verdict about who is at fault. */
  cause_category: string;
  recorded_at: string;
  provenance: Provenance;
}

/**
 * One consequence, named. `resolved` says whether TravelOps has already dealt with it.
 *
 * Deliberately not a severity score: the impacts a passenger cares about are discrete facts, and
 * ranking them would invent an ordering nobody reviewed.
 */
export interface PassengerImpact {
  impact_ref: string;
  label: string;
  detail: string;
  resolved: boolean;
}

export type PassengerOptionKind = 'rebook' | 'refund' | 'hotel' | 'meal' | 'transport';

/**
 * Something the passenger can take. `available: false` must always carry an `unavailable_reason`,
 * because an option greyed out without a reason reads as a broken screen.
 */
export interface PassengerOption {
  option_ref: string;
  kind: PassengerOptionKind;
  label: string;
  detail: string;
  available: boolean;
  unavailable_reason: string | null;
  /** True when choosing this needs an agent rather than being self-service. */
  requires_agent: boolean;
}

export interface PassengerActionRecord {
  action_ref: string;
  label: string;
  state: PassengerActionState;
  /** Null while the action has not started. */
  at: string | null;
  detail: string;
}

export interface PassengerNextStep {
  state: PassengerNextStepState;
  headline: string;
  detail: string;
  /** Null when nothing is time-boxed. Never a fabricated deadline. */
  respond_by: string | null;
}

export interface PassengerDisruptionResponse {
  booking_ref: string;
  passenger_name: string;
  incident_reference: string;
  trip: {
    origin_iata: string;
    destination_iata: string;
    segments: PassengerSegment[];
  };
  what_happened: PassengerWhatHappened;
  impacts: PassengerImpact[];
  options: PassengerOption[];
  travelops_actions: PassengerActionRecord[];
  next_step: PassengerNextStep;
  /**
   * Where an entitlement figure will come from. Rendered verbatim so this screen never states an
   * amount or its legal standing itself.
   */
  entitlement_note: string;
  provenance: Provenance;
}

/** The endpoint this feature is written against, named so the gap is legible. */
export const PASSENGER_VIEW_ENDPOINT = 'GET /api/v1/passenger/{booking_ref}/disruption';

export const passengerApi = {
  /** No endpoint serves the passenger view yet, so the screen labels its source. */
  isLive: false as boolean,
  endpoint: PASSENGER_VIEW_ENDPOINT,
} as const;

const SYNTHETIC: Provenance = {
  kind: 'synthetic',
  provider: 'console-sample',
  source_ref: 'phase5:passenger-sample',
  observed_at: '2026-08-20T15:36:00Z',
};

/**
 * One worked sample, matching the shipped `bengaluru_storm` incident so an operator can hold the
 * two screens side by side.
 *
 * The name is invented and the booking reference is not a real PNR. Figures that only the engine can
 * produce are absent rather than guessed, which is why there is no compensation amount here.
 */
export const PASSENGER_SAMPLE: PassengerDisruptionResponse = {
  booking_ref: 'X9Y2Z1',
  passenger_name: 'A. Nair',
  incident_reference: 'INC-2026-0820-VOBL-01',
  trip: {
    origin_iata: 'BLR',
    destination_iata: 'JFK',
    segments: [
      {
        segment_ref: 'seg-1',
        flight_number: '6E 2134',
        origin_iata: 'BLR',
        destination_iata: 'DEL',
        scheduled_departure: '2026-08-20T16:10:00Z',
        revised_departure: '2026-08-20T19:25:00Z',
        scheduled_arrival: '2026-08-20T18:55:00Z',
        revised_arrival: '2026-08-20T22:10:00Z',
        status: 'delayed',
        delay_minutes: 195,
        gate: null,
        is_disrupted: true,
      },
      {
        segment_ref: 'seg-2',
        flight_number: 'AI 101',
        origin_iata: 'DEL',
        destination_iata: 'JFK',
        scheduled_departure: '2026-08-20T20:40:00Z',
        revised_departure: null,
        scheduled_arrival: '2026-08-21T07:15:00Z',
        revised_arrival: null,
        status: 'at_risk',
        delay_minutes: null,
        gate: 'T3-12',
        is_disrupted: false,
      },
    ],
  },
  what_happened: {
    headline: 'A storm closed both runways at Bengaluru',
    detail:
      'Convective weather at Bengaluru held departures on stand from 15:36 UTC. Your first flight is now expected to leave about three hours late, which puts your Delhi connection at risk.',
    cause_category: 'weather',
    recorded_at: '2026-08-20T15:36:00Z',
    provenance: SYNTHETIC,
  },
  impacts: [
    {
      impact_ref: 'imp-connection',
      label: 'Delhi connection at risk',
      detail:
        'Your revised arrival in Delhi is after AI 101 is scheduled to depart, so the connection no longer holds.',
      resolved: false,
    },
    {
      impact_ref: 'imp-baggage',
      label: 'Bags stay checked through to New York',
      detail:
        'Your bags are tagged to the final destination and move with whichever flight you take.',
      resolved: true,
    },
    {
      impact_ref: 'imp-overnight',
      label: 'An overnight in Delhi may be needed',
      detail:
        'If you travel tomorrow morning instead, a hotel is held under the airline duty-of-care rules for this disruption.',
      resolved: false,
    },
  ],
  options: [
    {
      option_ref: 'opt-rebook-next',
      kind: 'rebook',
      label: 'Rebook to tomorrow morning',
      detail: 'AI 101 departs Delhi at 09:40 the next day, arriving New York the same evening.',
      available: true,
      unavailable_reason: null,
      requires_agent: false,
    },
    {
      option_ref: 'opt-hotel',
      kind: 'hotel',
      label: 'Accept a hotel in Delhi',
      detail: 'A room is held near Terminal 3 for the night, with transfers included.',
      available: true,
      unavailable_reason: null,
      requires_agent: false,
    },
    {
      option_ref: 'opt-refund',
      kind: 'refund',
      label: 'Cancel and request a refund',
      detail: 'Ends the trip and starts a refund of the unflown portion.',
      available: true,
      unavailable_reason: null,
      requires_agent: true,
    },
    {
      option_ref: 'opt-transport',
      kind: 'transport',
      label: 'Ground transport to Delhi',
      detail: 'Not offered on this route.',
      available: false,
      unavailable_reason: 'The distance is beyond the ground-transport limit for this disruption.',
      requires_agent: false,
    },
  ],
  travelops_actions: [
    {
      action_ref: 'act-assess',
      label: 'Checked your connection',
      state: 'succeeded',
      at: '2026-08-20T15:41:00Z',
      detail: 'Compared your revised arrival against the departure of AI 101.',
    },
    {
      action_ref: 'act-hold-hotel',
      label: 'Held a hotel room',
      state: 'succeeded',
      at: '2026-08-20T15:44:00Z',
      detail: 'A room near Terminal 3 is held pending your choice.',
    },
    {
      action_ref: 'act-rebook',
      label: 'Prepared a rebooking',
      state: 'awaiting_approval',
      at: '2026-08-20T15:46:00Z',
      detail: 'A seat on the next morning service is proposed and waiting for an airline decision.',
    },
    {
      action_ref: 'act-notify',
      label: 'Send you the confirmation',
      state: 'pending',
      at: null,
      detail: 'Sent once the rebooking is approved.',
    },
  ],
  next_step: {
    state: 'awaiting_approval',
    headline: 'An airline colleague is reviewing your rebooking',
    detail:
      'Nothing is confirmed yet and you do not need to do anything. The rebooking above is prepared and waiting for a person to approve it, because a change of this size is not made automatically.',
    respond_by: null,
  },
  entitlement_note:
    'Any compensation or care you are owed is decided by the airline against the published passenger rules for this disruption, and appears here once that assessment is recorded. This page does not calculate it.',
  provenance: SYNTHETIC,
};

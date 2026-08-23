/**
 * Reading per-entity impact out of recorded action payloads.
 *
 * There is no `/passengers`, `/connections`, `/crew` or `/hotels` endpoint, and asking for one
 * would be asking the backend to re-publish what it has already written down. The services
 * record their findings per entity inside `action.payload`, reachable via
 * `GET /incidents/{ref}/actions/{id}` — so that is the source, and FE-1 exists for exactly this.
 *
 * Two rules hold throughout:
 *
 * 1. **A field that is absent stays absent.** Every parser returns `null` for a missing value
 *    rather than `0` or `''`. `shortfall_minutes: null` renders as "not recorded"; a zero would
 *    claim the service measured no shortfall, which is a different and possibly false statement.
 * 2. **Nothing is computed here except the length of an array the server sent.** No mean, no
 *    rate, no severity score. Where a total is shown it is the server's own total from the same
 *    payload, not a sum this module performed.
 *
 * The shapes are asserted against the real payloads (see the key lists in each parser's doc), and
 * `payload_schema_version` is surfaced by the caller so a shape change is visible rather than
 * silently producing empty tables.
 *
 * Owner: Stream D.
 */

import type { ActionDetail } from '@/api/types';

// ---------------------------------------------------------------- safe readers

function str(row: Record<string, unknown>, key: string): string | null {
  const value = row[key];
  return typeof value === 'string' && value.length > 0 ? value : null;
}

function num(row: Record<string, unknown>, key: string): number | null {
  const value = row[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function bool(row: Record<string, unknown>, key: string): boolean | null {
  const value = row[key];
  return typeof value === 'boolean' ? value : null;
}

function rows(payload: Record<string, unknown>, key: string): Record<string, unknown>[] {
  const value = payload[key];
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is Record<string, unknown> => typeof item === 'object' && item !== null,
  );
}

function numbers(payload: Record<string, unknown>, key: string): number[] {
  const value = payload[key];
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is number => typeof item === 'number');
}

// ---------------------------------------------------------------- connections & passengers

/**
 * One at-risk connection, and the passenger holding it.
 *
 * From `check_connections.at_risk[]`, whose recorded keys are: alternative_flight_ids,
 * alternatives_basis, booking_id, connection_airport_icao, has_special_needs,
 * inbound_delay_minutes, inbound_flight_id, inbound_flight_number, inbound_revised_arrival,
 * inbound_scheduled_arrival, inbound_segment_id, minimum_connection_minutes, onward_flight_id,
 * onward_flight_number, onward_scheduled_departure, onward_segment_id, passenger_id,
 * passenger_reference, pnr, recovered_by_onward_delay, shortfall_minutes, slack_minutes, tier.
 */
export interface ConnectionRow {
  bookingId: number | null;
  pnr: string | null;
  passengerReference: string | null;
  passengerId: number | null;
  inboundFlight: string | null;
  onwardFlight: string | null;
  connectionAirport: string | null;
  /** Minutes the connection falls short. Negative slack, as the service recorded it. */
  shortfallMinutes: number | null;
  slackMinutes: number | null;
  minimumConnectionMinutes: number | null;
  inboundDelayMinutes: number | null;
  /** The service's own tier for this connection. Not a UI judgement. */
  tier: string | null;
  hasSpecialNeeds: boolean | null;
  recoveredByOnwardDelay: boolean | null;
  alternativeFlightIds: number[];
  alternativesBasis: string | null;
  onwardScheduledDeparture: string | null;
  inboundRevisedArrival: string | null;
}

function connectionRow(row: Record<string, unknown>): ConnectionRow {
  return {
    bookingId: num(row, 'booking_id'),
    pnr: str(row, 'pnr'),
    passengerReference: str(row, 'passenger_reference'),
    passengerId: num(row, 'passenger_id'),
    inboundFlight: str(row, 'inbound_flight_number'),
    onwardFlight: str(row, 'onward_flight_number'),
    connectionAirport: str(row, 'connection_airport_icao'),
    shortfallMinutes: num(row, 'shortfall_minutes'),
    slackMinutes: num(row, 'slack_minutes'),
    minimumConnectionMinutes: num(row, 'minimum_connection_minutes'),
    inboundDelayMinutes: num(row, 'inbound_delay_minutes'),
    tier: str(row, 'tier'),
    hasSpecialNeeds: bool(row, 'has_special_needs'),
    recoveredByOnwardDelay: bool(row, 'recovered_by_onward_delay'),
    alternativeFlightIds: numbers(row, 'alternative_flight_ids'),
    alternativesBasis: str(row, 'alternatives_basis'),
    onwardScheduledDeparture: str(row, 'onward_scheduled_departure'),
    inboundRevisedArrival: str(row, 'inbound_revised_arrival'),
  };
}

export interface ConnectionImpact {
  actionId: number;
  incidentReference: string;
  ruleVersion: string | null;
  atRisk: ConnectionRow[];
  nearMisses: ConnectionRow[];
  /** The service's own counts, read rather than recomputed from the arrays above. */
  atRiskCount: number | null;
  nearMissCount: number | null;
  examined: number | null;
  singleSegment: number | null;
  recoveredByOnwardDelay: number | null;
  minimumConnectionMinutes: number | null;
  nearMissMinutes: number | null;
  marginNote: string | null;
  alternativesNote: string | null;
}

export function connectionImpact(action: ActionDetail): ConnectionImpact {
  const payload = action.payload ?? {};
  return {
    actionId: action.id,
    incidentReference: action.incident_reference,
    ruleVersion: str(payload, 'rule_version'),
    atRisk: rows(payload, 'at_risk').map(connectionRow),
    nearMisses: rows(payload, 'near_misses').map(connectionRow),
    atRiskCount: num(payload, 'at_risk_count'),
    nearMissCount: num(payload, 'near_miss_count'),
    examined: num(payload, 'connecting_itineraries_examined'),
    singleSegment: num(payload, 'single_segment_itineraries'),
    recoveredByOnwardDelay: num(payload, 'recovered_by_onward_delay_count'),
    minimumConnectionMinutes: num(payload, 'minimum_connection_minutes'),
    nearMissMinutes: num(payload, 'near_miss_minutes'),
    marginNote: str(payload, 'margin_note'),
    alternativesNote: str(payload, 'alternatives_note'),
  };
}

// ---------------------------------------------------------------- crew

/**
 * One affected crew rotation.
 *
 * From `assess_crew_impact.impacts[]`: affected_leg_flight_number, affected_leg_id,
 * affected_leg_order, base_icao, covered_flight_ids, depth, detail, is_at_risk, mechanism,
 * pairing_id, pairing_leg_count, pairing_reference, source_flight_id, source_flight_number.
 */
export interface CrewRow {
  pairingReference: string | null;
  baseIcao: string | null;
  mechanism: string | null;
  sourceFlight: string | null;
  affectedLegFlight: string | null;
  affectedLegOrder: number | null;
  pairingLegCount: number | null;
  depth: number | null;
  isAtRisk: boolean | null;
  /** The service's own sentence. Rendered verbatim; never paraphrased. */
  detail: string | null;
  coveredFlightIds: number[];
}

export interface CrewImpact {
  actionId: number;
  incidentReference: string;
  ruleVersion: string | null;
  impacts: CrewRow[];
  pairingsAtRisk: number | null;
  /** mechanism -> count, as recorded. The legend lives on the group payload. */
  mechanismCounts: Record<string, number>;
  scopeNote: string | null;
}

export function crewImpact(action: ActionDetail): CrewImpact {
  const payload = action.payload ?? {};
  const counts: Record<string, number> = {};
  const recorded = payload['mechanism_counts'];
  if (typeof recorded === 'object' && recorded !== null) {
    for (const [key, value] of Object.entries(recorded as Record<string, unknown>)) {
      if (typeof value === 'number') counts[key] = value;
    }
  }
  return {
    actionId: action.id,
    incidentReference: action.incident_reference,
    ruleVersion: str(payload, 'rule_version'),
    impacts: rows(payload, 'impacts').map((row) => ({
      pairingReference: str(row, 'pairing_reference'),
      baseIcao: str(row, 'base_icao'),
      mechanism: str(row, 'mechanism'),
      sourceFlight: str(row, 'source_flight_number'),
      affectedLegFlight: str(row, 'affected_leg_flight_number'),
      affectedLegOrder: num(row, 'affected_leg_order'),
      pairingLegCount: num(row, 'pairing_leg_count'),
      depth: num(row, 'depth'),
      isAtRisk: bool(row, 'is_at_risk'),
      detail: str(row, 'detail'),
      coveredFlightIds: numbers(row, 'covered_flight_ids'),
    })),
    pairingsAtRisk: num(payload, 'pairings_at_risk'),
    mechanismCounts: counts,
    scopeNote: str(payload, 'scope_note'),
  };
}

// ---------------------------------------------------------------- hotels

/**
 * A candidate property from `find_hotel_options.options[]`, and — when a reservation was
 * attempted — what `reserve_hotel_block.allocations[]` actually secured against it.
 *
 * The two are joined on `hotel_id`. A property with no allocation is shown as a candidate that
 * was not used, which is different from one that was full, and the shortfall figures come from
 * the reservation payload rather than from subtracting anything here.
 */
export interface HotelRow {
  hotelId: number | null;
  name: string | null;
  rank: number | null;
  rateInr: number | null;
  isPartner: boolean | null;
  distanceKm: number | null;
  totalRooms: number | null;
  availableRooms: number | null;
  roomsHeld: number | null;
  /** From the allocation payload, when this property was actually used. */
  roomsAllocated: number | null;
  allocationCostInr: number | null;
  nights: number | null;
  allocationDetail: string | null;
}

export interface HotelImpact {
  searchActionId: number | null;
  reserveActionId: number | null;
  incidentReference: string;
  ruleVersion: string | null;
  properties: HotelRow[];
  roomsRequired: number | null;
  eligibleCapacityRooms: number | null;
  capacityIsSufficient: boolean | null;
  passengers: number | null;
  excludedByRateCap: number[];
  maxRateInr: number | null;
  passengersPerRoom: number | null;
  nights: number | null;
  /** Reservation outcome, all read from the payload. */
  roomsAllocated: number | null;
  shortfallRooms: number | null;
  passengersUnaccommodated: number | null;
  totalCostInr: number | null;
  isComplete: boolean | null;
  shortfallNote: string | null;
  constraintsNote: string | null;
  scopeNote: string | null;
  reserveStatus: string | null;
}

export function hotelImpact(
  search: ActionDetail | null,
  reserve: ActionDetail | null,
  incidentReference: string,
): HotelImpact {
  const searchPayload = search?.payload ?? {};
  const reservePayload = reserve?.payload ?? {};
  const constraints = (searchPayload['constraints'] ?? {}) as Record<string, unknown>;

  const allocationsByHotel = new Map<number, Record<string, unknown>>();
  for (const row of rows(reservePayload, 'allocations')) {
    const id = num(row, 'hotel_id');
    if (id !== null) allocationsByHotel.set(id, row);
  }

  const options = rows(searchPayload, 'options');
  // If the search did not run, the allocations still describe real properties, so they are shown
  // rather than dropped for want of a candidate row to hang them on.
  const source = options.length > 0 ? options : rows(reservePayload, 'allocations');

  const properties: HotelRow[] = source.map((row) => {
    const id = num(row, 'hotel_id');
    const allocation = id !== null ? allocationsByHotel.get(id) : undefined;
    return {
      hotelId: id,
      name: str(row, 'name') ?? str(row, 'hotel_name'),
      rank: num(row, 'rank'),
      rateInr: num(row, 'rate_inr'),
      isPartner: bool(row, 'is_partner'),
      distanceKm: num(row, 'distance_km'),
      totalRooms: num(row, 'total_rooms'),
      availableRooms: num(row, 'available_rooms'),
      roomsHeld: num(row, 'rooms_held'),
      roomsAllocated: allocation ? num(allocation, 'rooms') : null,
      allocationCostInr: allocation ? num(allocation, 'cost_inr') : null,
      nights: allocation ? num(allocation, 'nights') : null,
      allocationDetail: allocation ? str(allocation, 'detail') : null,
    };
  });

  return {
    searchActionId: search?.id ?? null,
    reserveActionId: reserve?.id ?? null,
    incidentReference,
    ruleVersion: str(searchPayload, 'rule_version') ?? str(reservePayload, 'rule_version'),
    properties,
    roomsRequired: num(searchPayload, 'rooms_required') ?? num(reservePayload, 'rooms_required'),
    eligibleCapacityRooms: num(searchPayload, 'eligible_capacity_rooms'),
    capacityIsSufficient: bool(searchPayload, 'capacity_is_sufficient'),
    passengers: num(searchPayload, 'passengers') ?? num(reservePayload, 'passengers'),
    excludedByRateCap: numbers(searchPayload, 'excluded_by_rate_cap'),
    maxRateInr: num(constraints, 'max_rate_inr'),
    passengersPerRoom: num(constraints, 'passengers_per_room'),
    nights: num(constraints, 'nights'),
    roomsAllocated: num(reservePayload, 'rooms_allocated'),
    shortfallRooms: num(reservePayload, 'shortfall_rooms'),
    passengersUnaccommodated: num(reservePayload, 'passengers_unaccommodated'),
    totalCostInr: num(reservePayload, 'total_cost_inr'),
    isComplete: bool(reservePayload, 'is_complete'),
    shortfallNote: str(reservePayload, 'shortfall_note'),
    constraintsNote: str(reservePayload, 'constraints_note'),
    scopeNote: str(searchPayload, 'scope_note'),
    reserveStatus: reserve?.status ?? null,
  };
}

// ---------------------------------------------------------------- action selection

export const IMPACT_ACTION_TYPES = {
  connections: 'check_connections',
  crew: 'assess_crew_impact',
  hotelSearch: 'find_hotel_options',
  hotelReserve: 'reserve_hotel_block',
} as const;

/**
 * Latest action of a type, by id.
 *
 * Latest rather than first: a re-run records a new action and the newer finding supersedes the
 * older one. Both remain in the record — this only decides which one the impact view describes.
 */
export function latestOfType(actions: ActionDetail[], actionType: string): ActionDetail | null {
  const matching = actions.filter((action) => action.action_type === actionType);
  if (matching.length === 0) return null;
  return matching.reduce((latest, action) => (action.id > latest.id ? action : latest));
}

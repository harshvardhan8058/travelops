/**
 * The Derivation contract — what a `<WhyPopover>` renders.
 *
 * docs/27-ui-specification.md requires every figure in the product to answer three
 * questions: where the input came from, which rule or formula produced it, and when. The
 * API already returns provenance and evidence references on every response, so this is
 * wiring, not invention.
 *
 * Two rules hold this honest:
 *
 *   1. A SCREEN NEVER WRITES EXPLANATORY PROSE. It hands an API object to an adapter in
 *      this file. Seven screens hand-writing their own sentences is how "why" answers drift
 *      apart and how a component ends up describing a derivation the backend never made.
 *
 *   2. AN ABSENCE IS RENDERED, NOT HIDDEN. If an endpoint does not return a rule version,
 *      the panel says so. A silently omitted section looks finished; a visible
 *      `not recorded` is a legible request to the stream that owns the endpoint. This is
 *      the same principle as the policy screen showing "Not owed" as a result rather than
 *      as an empty field.
 *
 * Adapters are added here as screens land. Nothing here is speculative: a builder exists
 * only once a real endpoint shape needs it.
 *
 * Owner: Stream D.
 */

import type { AirportConditions, FlightRow, Provenance } from '@/api/types';

// ---------------------------------------------------------------- contract

/** One input that fed the figure, with the provenance of that input. */
export interface DerivationInput {
  label: string;
  /** Formatted from API values only. Never a sentence about what the value means. */
  value: string;
  provenance?: Provenance;
}

/**
 * What produced the figure.
 *
 *   rule     — a deterministic rule or rule set, by ID and version
 *   formula  — the arithmetic actually applied, e.g.
 *              `least_of(cap 7500, basic_fare 4200 + fuel 800) = 5000`
 *   config   — a versioned configuration, e.g. the assurance gate semantics
 *   playbook — the deterministic fallback playbook, when no model was involved
 */
export interface DerivationRule {
  kind: 'rule' | 'formula' | 'config' | 'playbook';
  id?: string;
  version?: string;
  formula?: string;
  /** rules_fired[], clause refs, config hash — rendered verbatim. */
  refs?: string[];
  /** Only ever an API-supplied note. Adapters must not compose one. */
  note?: string;
}

/** When something was observed, retrieved or evaluated. ISO strings, formatted at render. */
export interface DerivationTime {
  label: string;
  at?: string | null;
  ageMinutes?: number | null;
  /** From the API's own freshness verdict — never computed in the UI. */
  isStale?: boolean;
}

/**
 * A fact the endpoint does not return.
 *
 * Rendered in its own section so a data-contract gap is visible on screen instead of
 * looking like a design choice. `detail` names the endpoint, so the fix is obvious.
 */
export interface DerivationAbsence {
  label: string;
  detail: string;
}

export interface Derivation {
  /** Includes the value itself, because it also labels the popover for screen readers. */
  title: string;
  /** Context line: flight number, route, action type. Rendered mono. */
  subtitle?: string;
  inputs?: DerivationInput[];
  rule?: DerivationRule;
  when?: DerivationTime[];
  evidenceRefs?: string[];
  absences?: DerivationAbsence[];
  /**
   * A standing honesty statement about the figure's class, not a description of this
   * particular value. Owned by an adapter so it reads identically everywhere.
   */
  caveat?: string;
}

// ---------------------------------------------------------------- helpers

function isPresent(value: string | null | undefined): value is string {
  return typeof value === 'string' && value.length > 0;
}

/** Formats airport observation fields. Formatting only — no interpretation. */
function describeConditions(airport: AirportConditions): string {
  const parts = [
    airport.visibility_m === null ? null : `vis ${airport.visibility_m}m`,
    airport.wind_speed_kt === null ? null : `wind ${airport.wind_speed_kt}kt`,
    airport.ceiling_ft === null ? null : `ceil ${airport.ceiling_ft}ft`,
    isPresent(airport.precipitation) ? airport.precipitation : null,
  ].filter(isPresent);
  return parts.length > 0 ? parts.join(' · ') : 'no observation values returned';
}

/**
 * Why every risk figure carries this: the index is a deterministic score over thresholds,
 * not a probability validated against observed outcomes. docs/18 is explicit that an
 * uncalibrated percentage is an unearned claim, so the UI shows an index and a band and
 * says why. It makes no claim about which inputs produced the number.
 */
const RISK_CAVEAT =
  'A deterministic index and a band, not a calibrated probability. No percentage is claimed because none is validated against observed outcomes.';

// ---------------------------------------------------------------- adapters

/**
 * Risk on an Ops Board flight row.
 *
 * `GET /flights` returns `risk_index`, `risk_level` and row provenance, but no contributing
 * factors and no rule version — those exist only on `GET /incidents/{id}`. Borrowing them
 * across endpoints would attribute one flight's factors to another row, so the panel
 * declares them absent instead.
 *
 * `origin` comes from the `network[]` array of the SAME response. It is presented as a
 * co-located observation with its own provenance, which is what makes the freshness of the
 * underlying weather visible before it can cause a gate failure. The panel does not claim
 * the observation produced the index, because the endpoint does not say that.
 */
export function flightRiskDerivation(flight: FlightRow, origin?: AirportConditions): Derivation {
  const inputs: DerivationInput[] = [
    { label: 'risk index', value: String(flight.risk_index), provenance: flight.provenance },
    { label: 'band', value: flight.risk_level },
  ];

  if (origin) {
    inputs.push({
      label: `origin ${origin.airport_icao}`,
      value: describeConditions(origin),
      provenance: origin.provenance,
    });
  }

  const when: DerivationTime[] = origin
    ? [
        {
          label: `observed ${origin.airport_icao}`,
          at: origin.provenance.observed_at,
          ageMinutes: origin.observation_age_minutes,
          isStale: origin.provenance.is_stale,
        },
        { label: 'retrieved', at: origin.provenance.retrieved_at },
      ]
    : [];

  const evidenceRefs = [flight.provenance.source_ref, origin?.provenance.source_ref].filter(
    isPresent,
  );

  const absences: DerivationAbsence[] = [
    {
      label: 'factors',
      detail: 'not recorded on this endpoint — GET /flights returns the index and band only',
    },
    { label: 'rule', detail: 'not recorded — the flight row carries no rule_version' },
    { label: 'evaluated at', detail: 'not recorded — the flight row carries no evaluation time' },
  ];

  if (!origin) {
    absences.push({
      label: 'origin observation',
      detail: `not recorded — no network entry for ${flight.origin_icao} in this response`,
    });
  }

  return {
    title: `Risk index ${flight.risk_index} · ${flight.risk_level}`,
    subtitle: `${flight.flight_number} · ${flight.origin_icao} → ${flight.destination_icao}`,
    inputs,
    // `rule` is deliberately omitted rather than filled in: the RULE section renders
    // "not recorded", which is the truthful answer for this endpoint.
    when,
    evidenceRefs,
    absences,
    caveat: RISK_CAVEAT,
  };
}

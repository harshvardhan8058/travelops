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

import type {
  AirportConditions,
  BlastRadiusDimension,
  CandidateComparisonRow,
  AssuranceEvaluation,
  CheckResult,
  CrewPairingImpact,
  FlightRow,
  IncidentDetail,
  Provenance,
  RiskEvidence,
  WeatherObservation,
  WhatIfDelta,
} from '@/api/types';

// ---------------------------------------------------------------- contract

/** One input that fed the figure, with the provenance of that input. */
export interface DerivationInput {
  label: string;
  /** Formatted from API values only. Never a sentence about what the value means. */
  value: string;
  /**
   * An API-supplied explanation of this input, rendered verbatim beneath it — e.g. a risk
   * factor's `detail`. Adapters pass it through; they never compose one.
   */
  detail?: string | null;
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

/**
 * Risk on the incident workspace. Unlike the flight row, this endpoint DOES return the
 * contributing factors and the rule version, so the panel can answer "which rule" properly.
 *
 * Note what is still declared absent: `evidence.weather` carries `observed_at` but no
 * observation age, and the age is NOT computed from `now()`. Fixture data is a committed
 * snapshot, so a browser-computed age would read as hours or days old and be worse than
 * useless — it would look like a bug in the freshness check.
 */
export function incidentRiskDerivation(
  risk: RiskEvidence,
  weather: WeatherObservation | null,
): Derivation {
  const inputs: DerivationInput[] = risk.factors.map((factor) => ({
    label: factor.name.replace(/_/g, ' '),
    value:
      [
        // `value` is the observed figure and can be an empty string when the rule recorded
        // none. Falling through to the points contribution keeps the row meaningful instead
        // of rendering a blank.
        isPresent(factor.value) ? factor.value : null,
        typeof factor.points === 'number' ? `${factor.points} pts` : null,
        factor.threshold ? `threshold ${factor.threshold}` : null,
        factor.runway ? `runway ${factor.runway}` : null,
      ]
        .filter(isPresent)
        .join(' · ') || 'no figure recorded',
    // The rule's own explanation, verbatim. This is the whole point of the popover.
    detail: factor.detail,
    provenance: weather?.provenance,
  }));

  const absences: DerivationAbsence[] = [];
  if (risk.factors.length === 0) {
    absences.push({
      label: 'factors',
      detail: 'the endpoint returned an index and a band with no contributing factors',
    });
  }
  if (!weather) {
    absences.push({
      label: 'weather observation',
      detail: 'null on this incident — no observation is recorded for the origin airport',
    });
  } else if (weather.observation_age_minutes === undefined) {
    absences.push({
      label: 'observation age',
      detail: 'not recorded on this endpoint — the UI does not compute one from the wall clock',
    });
  }

  return {
    title: `Risk index ${risk.risk_index} · ${risk.risk_level}`,
    subtitle: weather?.airport_icao,
    inputs,
    rule: { kind: 'rule', id: 'delay risk rule set', version: risk.rule_version, note: risk.note },
    when: weather ? [{ label: 'observed', at: weather.observed_at }] : [],
    evidenceRefs: [...(risk.evidence_refs ?? []), weather?.provenance.source_ref].filter(isPresent),
    absences,
    caveat: RISK_CAVEAT,
  };
}

/** A count is a derived figure too: it should name what it counted and where. */
export function entityCountDerivation(
  label: string,
  value: number,
  incident: IncidentDetail,
): Derivation {
  return {
    title: `${label.replace(/_/g, ' ')} · ${value}`,
    subtitle: incident.reference,
    inputs: [
      { label: 'count', value: String(value), provenance: incident.provenance },
      { label: 'scope', value: `incident ${incident.reference}` },
    ],
    rule: {
      kind: 'config',
      id: 'affected_entities rollup',
      note: 'Computed server-side from records. The UI renders the returned total and never sums its own.',
    },
    when: [{ label: 'incident opened', at: incident.opened_at }],
    evidenceRefs: [incident.provenance.source_ref].filter(isPresent),
    absences: [
      {
        label: 'per-entity ids',
        detail: 'not recorded on this endpoint — only the aggregate count is returned',
      },
    ],
  };
}

/**
 * Elapsed time, measured between two RECORDS rather than against the browser clock.
 *
 * A wall-clock timer was the obvious implementation and the wrong one. Incident timestamps
 * are a committed fixture snapshot, so `now() − opened_at` rendered "19h 26m" for a storm the
 * demo presents as unfolding now: arithmetically correct, and read by any audience as a bug.
 * Worse, the figure would keep changing for a dataset that cannot change.
 *
 * Subtracting the latest recorded timestamp from `opened_at` is both stable and more useful —
 * it is the workflow's own duration, the same quantity the executive report calls time to
 * first action. The popover names both endpoints of the subtraction.
 */
export function elapsedDerivation(
  incident: IncidentDetail,
  first: { at: string; label: string } | null,
  latest: { at: string; label: string } | null,
): Derivation {
  if (!first || !latest) {
    return {
      title: 'Workflow duration · not derivable',
      subtitle: incident.reference,
      inputs: first
        ? [{ label: 'only record', value: `${first.at} (${first.label})` }]
        : [{ label: 'opened at', value: incident.opened_at, provenance: incident.provenance }],
      when: [{ label: 'opened (disruption time)', at: incident.opened_at }],
      evidenceRefs: [incident.provenance.source_ref].filter(isPresent),
      absences: [
        {
          label: 'second record',
          detail:
            'only one recorded transition exists, so there is no interval to measure. A figure would have to come from the browser clock, and this screen does not do that.',
        },
      ],
    };
  }

  return {
    title: 'Workflow duration',
    subtitle: incident.reference,
    inputs: [
      { label: 'from', value: `${first.at} (${first.label})`, provenance: incident.provenance },
      { label: 'to', value: `${latest.at} (${latest.label})`, provenance: incident.provenance },
      {
        label: 'opened at',
        value: incident.opened_at,
        detail:
          "The disruption's own time, shown separately in the header. Deliberately NOT an end of this measurement: in an injected scenario it belongs to a different clock from the recorded transitions.",
      },
    ],
    rule: {
      kind: 'formula',
      formula: 'latest_recorded_timestamp − earliest_recorded_timestamp',
      note: 'Both ends are recorded transitions, so this measures the workflow. Measuring from opened_at instead mixed the scenario clock with the execution clock and reported 26h for a recovery that took seconds.',
    },
    when: [
      { label: first.label, at: first.at },
      { label: latest.label, at: latest.at },
    ],
    evidenceRefs: [incident.provenance.source_ref].filter(isPresent),
  };
}

/** One assurance check: its verdict, the reason code behind it, and the semantics used. */
export function checkDerivation(check: CheckResult, evaluation: AssuranceEvaluation): Derivation {
  const inputs: DerivationInput[] = [
    { label: 'verdict', value: check.state },
    { label: 'reason code', value: check.reason_code },
  ];
  if (check.reason) inputs.push({ label: 'reason', value: check.reason });
  if (check.tier) inputs.push({ label: 'risk tier', value: check.tier });

  return {
    title: `${check.name.replace(/_/g, ' ')} · ${check.state}`,
    subtitle: `${evaluation.action_type} · evaluation ${evaluation.id}`,
    inputs,
    rule: {
      kind: 'config',
      id: 'decision assurance gate',
      version: evaluation.config_version,
      refs: [`hash ${evaluation.config_hash}`],
      note: 'Freshness limits, risk tiers and warning exceptions come from versioned config. Missing config fails closed.',
    },
    when: [{ label: 'evaluated', at: evaluation.evaluated_at }],
    evidenceRefs: check.evidence_refs ?? [],
    absences:
      check.evidence_refs === undefined
        ? [
            {
              label: 'per-check evidence refs',
              detail: 'not recorded for this check — see the evaluation-level refs below the panel',
            },
          ]
        : undefined,
  };
}

/**
 * The gate decision itself — the most important derivation in the product, because it is
 * where "all six checks passed but this still blocks" has to be legible.
 */
export function decisionDerivation(evaluation: AssuranceEvaluation): Derivation {
  const failed = evaluation.checks.filter((check) => check.state === 'FAIL');
  const warned = evaluation.checks.filter((check) => check.state === 'WARN');
  const inputs: DerivationInput[] = [
    { label: 'decision', value: evaluation.decision },
    { label: 'risk tier', value: evaluation.risk_tier },
    {
      label: 'checks',
      value: `${evaluation.checks.filter((c) => c.state === 'PASS').length} pass · ${warned.length} warn · ${failed.length} fail`,
    },
    {
      label: 'blocking',
      value: evaluation.blocking.length > 0 ? evaluation.blocking.join(', ') : 'none',
    },
  ];
  if (evaluation.warn_permitted_by_config !== undefined) {
    inputs.push({
      label: 'warn allowed',
      value: String(evaluation.warn_permitted_by_config),
    });
  }

  return {
    title: `Gate decision · ${evaluation.decision}`,
    subtitle: `${evaluation.action_type} · evaluation ${evaluation.id}`,
    inputs,
    rule: {
      kind: 'config',
      id: 'fail-closed aggregation',
      version: evaluation.config_version,
      refs: [`hash ${evaluation.config_hash}`],
      // The aggregation order is the gate's contract, quoted from docs/18 rather than paraphrased per screen.
      note: evaluation.note,
    },
    when: [{ label: 'evaluated', at: evaluation.evaluated_at }],
    evidenceRefs: evaluation.evidence_refs,
    caveat:
      'A deterministic gate over verifiable facts. No model self-report is used for control flow; high risk blocks even when every check passes.',
  };
}

/** An executed side effect: what authorised it, what it cost, and how replay stays safe. */
export function actionDerivation(
  action: IncidentDetail['actions'][number],
  incident: IncidentDetail,
): Derivation {
  const inputs: DerivationInput[] = [
    { label: 'actor', value: action.actor },
    { label: 'result', value: action.status },
    { label: 'reason', value: action.reason },
    {
      label: 'cost',
      value: action.cost_inr === null ? 'not applicable' : `INR ${action.cost_inr}`,
    },
    { label: 'authorised by', value: `assurance ${action.assurance_id}` },
    {
      label: 'human decision',
      value:
        action.human_decision_id === null ? 'not required' : `decision ${action.human_decision_id}`,
    },
    { label: 'provenance', value: action.provenance_kind ?? 'not recorded' },
  ];

  return {
    title: `Action ${action.id} · ${action.status}`,
    subtitle: `${action.action_type ?? `task ${action.plan_task_id}`} · ${incident.reference}`,
    inputs,
    rule: {
      kind: 'config',
      id: 'idempotency key',
      refs: [action.idempotency_key],
      note: 'Re-running the same task cannot produce a second side effect.',
    },
    when: [{ label: 'executed', at: action.executed_at }],
    evidenceRefs: [incident.provenance.source_ref].filter(isPresent),
  };
}

// ---------------------------------------------------------------- Phase 2 adapters

/**
 * A count that came from the API, naming the endpoint and field it was read from.
 *
 * Used by every `<Metric>` on the Command Center, the cascade rollups and the blast radius, so a
 * reviewer pointing at any number gets the field name rather than a reassurance.
 */
export function countDerivation(
  label: string,
  value: number | null | undefined,
  source: { endpoint: string; field: string; provenance?: Provenance; note?: string },
): Derivation {
  return {
    title: `${label} · ${value ?? 'not returned'}`,
    subtitle: source.endpoint,
    inputs: [
      {
        label: 'value',
        value: value === null || value === undefined ? 'not returned' : String(value),
        provenance: source.provenance,
      },
      { label: 'field', value: source.field },
    ],
    rule: {
      kind: 'config',
      id: 'server-side rollup',
      note:
        source.note ??
        'Computed server-side from records. The UI renders the returned total and never sums its own.',
    },
    evidenceRefs: source.provenance?.source_ref ? [source.provenance.source_ref] : [],
    absences:
      value === null || value === undefined
        ? [{ label, detail: `not returned by ${source.endpoint}` }]
        : undefined,
  };
}

/** The length of a returned array — the only aggregate the UI is allowed to compute itself. */
export function arrayLengthDerivation(
  label: string,
  length: number,
  source: { endpoint: string; field: string; provenance?: Provenance },
): Derivation {
  return {
    title: `${label} · ${length}`,
    subtitle: source.endpoint,
    inputs: [
      { label: 'count', value: String(length), provenance: source.provenance },
      { label: 'counted', value: `length of ${source.field}` },
    ],
    rule: {
      kind: 'formula',
      formula: `${source.field}.length`,
      note: 'Counting a returned array is the only aggregate this UI computes. No mean, rate or score is derived anywhere.',
    },
    evidenceRefs: source.provenance?.source_ref ? [source.provenance.source_ref] : [],
  };
}

/** One crew pairing: which flight reached it, by which mechanism, and the rule's own words. */
export function pairingDerivation(
  pairing: CrewPairingImpact,
  legend: Record<string, string> | undefined,
  provenance?: Provenance,
): Derivation {
  return {
    title: `${pairing.pairing_reference} · ${pairing.mechanism.replace(/_/g, ' ')}`,
    subtitle: `reached from ${pairing.source_flight}`,
    inputs: [
      { label: 'source flight', value: pairing.source_flight, provenance },
      { label: 'affected leg', value: pairing.affected_leg },
      { label: 'base', value: pairing.base_icao },
      { label: 'at risk', value: String(pairing.at_risk) },
      {
        label: 'mechanism',
        value: pairing.mechanism,
        detail: legend?.[pairing.mechanism],
      },
    ],
    rule: {
      kind: 'rule',
      id: 'crew pairing impact',
      note: pairing.detail,
    },
    evidenceRefs: provenance?.source_ref ? [provenance.source_ref] : [],
    absences: [
      {
        label: 'duty-time legality',
        detail:
          'not modelled. Crew handling is coordination and display only; this product does not validate duty limits',
      },
    ],
  };
}

/** A terminal count in the blast radius: real number, no path behind it. */
export function terminalDerivation(
  label: string,
  count: number,
  field: string,
  reason: string,
): Derivation {
  return {
    title: `${label} · ${count}`,
    subtitle: 'GET /incident-groups/{id}',
    inputs: [
      { label: 'count', value: String(count) },
      { label: 'field', value: field },
    ],
    rule: {
      kind: 'config',
      id: 'server-side rollup',
      note: 'Computed server-side from records.',
    },
    absences: [{ label: 'per-entity records', detail: reason }],
  };
}

/** A report metric. Absent metrics stay absent — never estimated, never zero-filled. */
export function reportMetricDerivation(
  label: string,
  value: number | string | null | undefined,
  incidentReference: string,
): Derivation {
  return {
    title: `${label} · ${value ?? 'absent'}`,
    subtitle: `GET /reports/${incidentReference}`,
    inputs: [
      {
        label: label.toLowerCase(),
        value: value === null || value === undefined ? 'absent' : String(value),
      },
    ],
    rule: {
      kind: 'config',
      id: 'report metrics',
      note: 'Every value is derived from recorded rows. Absent metrics are absent, not estimated.',
    },
    absences:
      value === null || value === undefined
        ? [
            {
              label,
              detail: 'the records cannot support this metric yet, so it is shown as absent',
            },
          ]
        : undefined,
  };
}

/** The gate across a whole plan: counts by decision, never a score. */
export function planAssuranceDerivation(
  evaluations: AssuranceEvaluation[],
  configVersion: string,
  configHash: string,
): Derivation {
  const byDecision = new Map<string, number>();
  for (const evaluation of evaluations) {
    byDecision.set(evaluation.decision, (byDecision.get(evaluation.decision) ?? 0) + 1);
  }
  return {
    title: `Gate across ${evaluations.length} evaluation${evaluations.length === 1 ? '' : 's'}`,
    inputs: [...byDecision.entries()].map(([decision, count]) => ({
      label: decision,
      value: String(count),
    })),
    rule: {
      kind: 'config',
      id: 'fail-closed aggregation',
      version: configVersion,
      refs: [`hash ${configHash}`],
      note: 'Counts by decision only. No aggregate score: a fail-closed, ordered gate has no meaningful average, so one number would invite exactly the trust the gate replaces.',
    },
    evidenceRefs: evaluations.flatMap((evaluation) => evaluation.evidence_refs).slice(0, 8),
  };
}

// ---------------------------------------------------------------- Phase 2: comparison

/**
 * One candidate plan's figure, in a comparison.
 *
 * The rule is the plan gate's own config version and hash, because that is what judged the
 * candidate. `basis` is stated verbatim from the response: the server pins it to a literal so the
 * contract cannot express a projection, and the popover says so rather than the screen implying it.
 */
export function candidateDerivation(
  row: CandidateComparisonRow,
  basis: string,
  seed: number | null,
): Derivation {
  return {
    title: `Candidate ${row.variant_key}`,
    inputs: [
      { label: 'tasks', value: String(row.task_count) },
      { label: 'high-risk actions', value: String(row.high_risk_actions) },
      { label: 'approvals required', value: String(row.approvals_required) },
      { label: 'uncovered entities', value: String(row.uncovered_entities) },
      { label: 'plan hash', value: row.plan_hash },
    ],
    rule: {
      kind: 'config',
      id: 'plan gate re-evaluation',
      version: basis,
      refs: seed === null ? [] : [`seed ${seed}`],
      note: 'Re-evaluated against evidence already recorded for this incident. Nothing was simulated, projected or written, and no candidate is ranked.',
    },
    evidenceRefs: [],
  };
}

/**
 * A what-if delta.
 *
 * Both figures are shown because they answer different questions — "what did we find" and "what do
 * the rules say under these inputs". Presenting one as the other is how a what-if starts to look
 * like a correction to the live figures.
 */
export function whatIfDerivation(delta: WhatIfDelta, ruleVersion: string): Derivation {
  return {
    title: delta.label,
    inputs: [
      { label: 'recorded', value: String(delta.baseline) },
      { label: 're-evaluated', value: String(delta.scenario) },
    ],
    rule: {
      kind: 'rule',
      id: 'deterministic re-evaluation',
      version: ruleVersion,
      refs: [],
      note: 'The same deterministic rules the live services use, over substituted inputs. Not a forecast, and no rows were written.',
    },
    evidenceRefs: [],
  };
}

/** One dimension of the server-composed blast radius. */
export function blastDimensionDerivation(
  dimension: BlastRadiusDimension,
  ratio: string,
): Derivation {
  return {
    title: dimension.label,
    inputs: [
      { label: 'value', value: String(dimension.value), detail: dimension.note || null },
      { label: 'flights assessed', value: ratio },
    ],
    rule: {
      kind: 'rule',
      id: `measured by ${dimension.measured_by || 'declared data'}`,
      version: dimension.is_complete ? 'complete' : 'partial',
      refs: [],
      note: dimension.is_complete
        ? 'Every declared flight contributed to this figure.'
        : 'Not every declared flight has been assessed, so this is a floor rather than a total.',
    },
    evidenceRefs: [],
  };
}

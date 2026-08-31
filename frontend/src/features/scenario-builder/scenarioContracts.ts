/**
 * Proposed contracts for scenario authoring — Phase 5.
 *
 * **No endpoint serves these.** They are declared here, in the feature that would consume them,
 * rather than in `@/api/types`, because that module documents contracts the backend actually
 * publishes and putting a speculative shape beside a real one is how a reviewer ends up believing
 * an endpoint exists. When scenario authoring becomes real, these move to `@/api/types`, the
 * adapter below is deleted, and the screen changes by one import.
 *
 * The honesty rule this module exists to keep is the one `api/client.ts` states for writes:
 * synthesising a plausible success response would put a state transition on screen that never
 * happened, which is the one thing an audit-trail product must not do. So `prepareScenarioRequest`
 * returns a **request that has not been sent**, says which endpoint would receive it, and the screen
 * renders it as prepared rather than created. Nothing here fabricates a scenario id, a passenger
 * count, or an incident.
 *
 * Owner: Stream D.
 */

/** The disruption families the seed generator already understands. */
export type DisruptionType = 'weather' | 'crew' | 'technical' | 'airport_closure';

export const DISRUPTION_TYPES: readonly DisruptionType[] = [
  'weather',
  'crew',
  'technical',
  'airport_closure',
] as const;

/** Matches `IncidentSeverity` on the real contract, so this does not invent a fifth band. */
export type ScenarioSeverity = 'low' | 'medium' | 'high' | 'critical';

export const SCENARIO_SEVERITIES: readonly ScenarioSeverity[] = [
  'low',
  'medium',
  'high',
  'critical',
] as const;

/**
 * The operator-editable body of a scenario.
 *
 * Deliberately carries no passenger, connection or crew figures. Those are computed by the engine
 * from the seeded dataset when a scenario runs; a count typed into this form would be a number on
 * screen that no record supports.
 */
export interface ScenarioDraft {
  templateId: string | null;
  name: string;
  disruptionType: DisruptionType;
  airportIcao: string;
  startsAt: string;
  durationMinutes: number;
  severity: ScenarioSeverity;
  /** Flight designators, e.g. `6E 2134`. The first is the primary. */
  flightNumbers: string[];
  primaryFlight: string;
  notes: string;
}

/**
 * A starting point an operator picks, not a thing the console computes.
 *
 * `declaresFlights` is the template's own statement about its shape, rendered as the template's
 * claim rather than as a measured figure. `seedScenarioId` is present only where the repository
 * genuinely ships that scenario, which is what lets the review step offer a command that works.
 */
export interface ScenarioTemplate {
  id: string;
  name: string;
  summary: string;
  disruptionType: DisruptionType;
  airportIcao: string;
  severity: ScenarioSeverity;
  durationMinutes: number;
  flightNumbers: string[];
  declaresFlights: number;
  /** Set only for scenarios `python -m app.cli inject --scenario <id>` already accepts. */
  seedScenarioId: string | null;
}

/** The body that would be POSTed. Snake_case because that is the house wire convention. */
export interface ScenarioCreateRequest {
  name: string;
  disruption_type: DisruptionType;
  airport_icao: string;
  starts_at: string;
  duration_minutes: number;
  severity: ScenarioSeverity;
  flight_numbers: string[];
  primary_flight: string;
  notes: string;
  template_id: string | null;
  /** Whether the operator asked for the cascade to be advanced immediately after creation. */
  run_after_create: boolean;
}

/**
 * What the console produced. `submitted` is always false while no endpoint exists, and the screen
 * reads this field rather than assuming.
 */
export interface ScenarioRequestReceipt {
  requestId: string;
  preparedAt: string;
  targetEndpoint: string;
  payload: ScenarioCreateRequest;
  submitted: boolean;
  /** Why it was not submitted, in the console's own words. Never blank while `submitted` is false. */
  unsubmittedReason: string;
  /** A command that genuinely works today, or null when the draft has no seeded equivalent. */
  equivalentCommand: string | null;
}

/** The endpoint this feature is written against. Named so the gap is legible, not implied. */
export const SCENARIO_CREATE_ENDPOINT = 'POST /api/v1/scenarios';

/** The endpoint `Create & Run` would chain to once creation exists. */
export const SCENARIO_RUN_ENDPOINT = 'POST /api/v1/incident-groups/{ref}/run';

/**
 * Whether a scenario can actually be created against the running backend.
 *
 * A constant rather than a probe: there is no endpoint to probe, and a runtime check that always
 * fails would render as an error state rather than as an honest absence. Mirrors the `api.canWrite`
 * pattern, which gates every other write affordance in the product.
 */
export const scenarioApi = {
  canCreate: false as boolean,
  createEndpoint: SCENARIO_CREATE_ENDPOINT,
  runEndpoint: SCENARIO_RUN_ENDPOINT,
} as const;

/**
 * The four templates, matching disruption families the seed generator understands.
 *
 * Only `bengaluru_storm` carries a `seedScenarioId`, because it is the only one this repository
 * actually ships. The other three are shapes an operator can author; claiming a seed for them would
 * offer a command that fails.
 */
export const SCENARIO_TEMPLATES: readonly ScenarioTemplate[] = [
  {
    id: 'bengaluru-monsoon-storm',
    name: 'Bengaluru monsoon storm',
    summary:
      'Convective storm closes both runways at Bengaluru during the evening departure peak. The shipped demo dataset.',
    disruptionType: 'weather',
    airportIcao: 'VOBL',
    severity: 'high',
    durationMinutes: 180,
    flightNumbers: ['6E 2134', '6E 811', 'AI 503', 'UK 705'],
    declaresFlights: 8,
    seedScenarioId: 'bengaluru_storm',
  },
  {
    id: 'delhi-winter-fog',
    name: 'Delhi winter fog',
    summary:
      'Low visibility below CAT III minima at Delhi through the early morning bank, holding departures on stand.',
    disruptionType: 'weather',
    airportIcao: 'VIDP',
    severity: 'medium',
    durationMinutes: 240,
    flightNumbers: ['AI 811', '6E 2043'],
    declaresFlights: 2,
    seedScenarioId: null,
  },
  {
    id: 'crew-duty-breach',
    name: 'Crew duty limit breach',
    summary:
      'An inbound delay pushes an outbound crew past its duty ceiling, stranding a rotation rather than a single leg.',
    disruptionType: 'crew',
    airportIcao: 'VOBL',
    severity: 'medium',
    durationMinutes: 120,
    flightNumbers: ['6E 455'],
    declaresFlights: 1,
    seedScenarioId: null,
  },
  {
    id: 'aircraft-on-ground',
    name: 'Aircraft on ground',
    summary:
      'A technical defect grounds one airframe, so every downstream leg it was rostered to operate is exposed.',
    disruptionType: 'technical',
    airportIcao: 'VABB',
    severity: 'high',
    durationMinutes: 480,
    flightNumbers: ['UK 812'],
    declaresFlights: 1,
    seedScenarioId: null,
  },
] as const;

export function findTemplate(templateId: string | null): ScenarioTemplate | null {
  if (!templateId) return null;
  return SCENARIO_TEMPLATES.find((template) => template.id === templateId) ?? null;
}

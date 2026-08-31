/**
 * Operator-facing Scenario Builder contracts.
 *
 * Wire DTOs live in `@/api/types`; this module contains only editable draft/template vocabulary.
 * Owner: Stream D.
 */

/** The disruption families offered by the builder. */
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
 * Operator-editable draft. Name, duration and notes organise the local preview only; wire DTOs are
 * deliberately separate in `@/api/types` so the UI cannot imply those values are persisted.
 */
export interface ScenarioDraft {
  templateId: string | null;
  name: string;
  disruptionType: DisruptionType;
  airportIcao: string;
  startsAt: string;
  durationMinutes: number;
  severity: ScenarioSeverity;
  flightNumbers: string[];
  primaryFlight: string;
  notes: string;
}

/** A starting point an operator picks, not a thing the console computes. */
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
  seedScenarioId: string | null;
}

export const scenarioApi = {
  createEndpoint: 'POST /api/v1/scenarios',
  startEndpoint: 'POST /api/v1/scenarios/{scenario_reference}/start',
} as const;

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
    flightNumbers: ['6E 2134', '6E 811'],
    declaresFlights: 2,
    seedScenarioId: 'bengaluru_storm',
  },
] as const;

export function findTemplate(templateId: string | null): ScenarioTemplate | null {
  if (!templateId) return null;
  return SCENARIO_TEMPLATES.find((template) => template.id === templateId) ?? null;
}

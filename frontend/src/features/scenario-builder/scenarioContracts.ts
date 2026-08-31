/**
 * Operator-facing scenario draft contracts and templates.
 *
 * Published wire contracts live in `@/api/types`. This module contains only editable draft state
 * and repository-shipped starting points; `scenarioLifecycle.ts` resolves those declarations
 * against the live flight board before anything is submitted.
 */

export type DisruptionType = 'weather' | 'crew' | 'technical' | 'airport_closure';

export const DISRUPTION_TYPES: readonly DisruptionType[] = [
  'weather',
  'crew',
  'technical',
  'airport_closure',
] as const;

export type ScenarioSeverity = 'low' | 'medium' | 'high' | 'critical';

export const SCENARIO_SEVERITIES: readonly ScenarioSeverity[] = [
  'low',
  'medium',
  'high',
  'critical',
] as const;

export interface ScenarioDraft {
  templateId: string | null;
  name: string;
  disruptionType: DisruptionType;
  airportIcao: string;
  /** UTC wall time from the `datetime-local` control. */
  startsAt: string;
  durationMinutes: number;
  severity: ScenarioSeverity;
  flightNumbers: string[];
  primaryFlight: string;
  notes: string;
}

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

export const SCENARIO_CREATE_ENDPOINT = 'POST /api/v1/scenarios';
export const SCENARIO_START_ENDPOINT = 'POST /api/v1/scenarios/{scenario_reference}/start';

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
    flightNumbers: ['6E 2134'],
    declaresFlights: 1,
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

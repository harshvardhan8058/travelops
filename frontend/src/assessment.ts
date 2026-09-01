/**
 * Whether a rollup dimension was measured, and how much of it.
 *
 * The problem this exists to solve is that `connections_at_risk: 0` and `crew_pairings_affected: 0`
 * carry two opposite operational meanings — "assessed, none at risk" and "not looked at yet" — and
 * a number alone cannot tell them apart. Rendering the second as the first tells an operator a
 * cascade is clear when nothing has examined it, which is the most expensive sentence this console
 * can say.
 *
 * The distinction is only available for the two dimensions that are actually derived from recorded
 * findings. `flights_affected`, `passengers_affected` and `candidate_hotels` come from declared
 * membership and reference tables: they are populated the moment a group exists, are never the
 * product of an assessment, and so are never "not computed". `candidate_hotels` in particular
 * counts properties at the airport — a search space, not a search result — and the server says so
 * in its own note.
 *
 * Owner: Stream D. The counters are published by `RollupStatus`; nothing here infers them.
 */

import type { GroupRollupStatus } from '@/api/types';

/**
 * `assessed`   every member incident carries this finding, so a 0 is a real finding of none.
 * `partial`    some do, so the number is a floor rather than a total.
 * `none`       no member incident carries it, so the number says nothing at all.
 * `empty`      the group has no member incidents yet, so there is nothing that could carry it.
 * `unknown`    the server did not publish the counters. Not the same as none.
 */
export type AssessmentState = 'assessed' | 'partial' | 'none' | 'empty' | 'unknown';

export interface DimensionAssessment {
  state: AssessmentState;
  assessed: number;
  total: number;
  /** True when the figure may be rendered as a measurement. False when it must not. */
  isMeasured: boolean;
  /** Sentence for the operator. Empty when the figure stands on its own. */
  note: string;
}

function classify(assessed: number | undefined, total: number | undefined): AssessmentState {
  if (assessed === undefined || total === undefined) return 'unknown';
  if (total === 0) return 'empty';
  if (assessed === 0) return 'none';
  if (assessed < total) return 'partial';
  return 'assessed';
}

const LABEL: Record<AssessmentState, (a: number, t: number, dimension: string) => string> = {
  assessed: () => '',
  partial: (a, t, d) => `${a} of ${t} incidents assessed for ${d}. This is a floor, not a total.`,
  none: (_a, t, d) =>
    `No incident has been assessed for ${d} yet, across ${t}. Advance the disruption to find out.`,
  empty: (_a, _t, d) => `No incident is open, so ${d} has nothing to measure against.`,
  unknown: () => 'This server does not report which assessments have run.',
};

/**
 * The assessment behind one rollup dimension.
 *
 * `dimension` is the phrase used inside the sentence, so it reads as prose: "connections",
 * "crew impact".
 */
export function dimensionAssessment(
  status: GroupRollupStatus | undefined,
  which: 'connections' | 'crew',
  dimension: string,
): DimensionAssessment {
  const total = status?.incidents_in_group;
  const assessed =
    which === 'connections'
      ? status?.incidents_assessed_connections
      : status?.incidents_assessed_crew;
  const state = classify(assessed, total);

  return {
    state,
    assessed: assessed ?? 0,
    total: total ?? 0,
    /*
     * `unknown` is measured-by-default on purpose. An older server that does not publish the
     * counters is not evidence that nothing ran, and blanking every figure on that basis would
     * replace one wrong claim with another.
     */
    isMeasured: state === 'assessed' || state === 'partial' || state === 'unknown',
    note: LABEL[state](assessed ?? 0, total ?? 0, dimension),
  };
}

/** How much of the group has been worked at all. Drives the partial-assessment banner. */
export interface GroupAssessment {
  incidents: number;
  /** Incidents carrying both recorded assessments. */
  fullyAssessed: number;
  awaiting: number;
  /** Declared member flights with no incident open — named by the server, not counted here. */
  flightsWithoutIncident: number;
  isPartial: boolean;
  isKnown: boolean;
}

export function groupAssessment(status: GroupRollupStatus | undefined): GroupAssessment {
  const incidents = status?.incidents_in_group;
  const connections = status?.incidents_assessed_connections;
  const crew = status?.incidents_assessed_crew;
  const isKnown = incidents !== undefined && connections !== undefined && crew !== undefined;

  /*
   * The minimum of the two counters, not either one alone. An incident with connections recorded
   * and crew missing is not a fully assessed incident, and reporting the higher of the two would
   * overstate exactly the case this banner exists to expose.
   */
  const fullyAssessed = isKnown ? Math.min(connections, crew) : 0;
  const count = incidents ?? 0;

  return {
    incidents: count,
    fullyAssessed,
    awaiting: Math.max(0, count - fullyAssessed),
    flightsWithoutIncident: status?.flights_without_incident.length ?? 0,
    isPartial: isKnown ? status?.is_complete !== true : status?.is_complete === false,
    isKnown,
  };
}

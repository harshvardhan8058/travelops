/**
 * Which per-entity surfaces the Impact Explorer offers, and why.
 *
 * The screen reads recorded action payloads, so each surface depends on an action having run. The
 * original rule was "a tab whose action never ran is absent, not empty", and for crew and hotels
 * that is right: nobody claimed anything about them, and an empty table would suggest a search that
 * found nothing.
 *
 * **Connections is the exception, and getting it wrong was the defect.** On a cascade that had been
 * injected but not advanced, no `check_connections` action existed, so the connections tab, its
 * metric tiles and its panel were all dropped — and with them every mention of the word. The screen
 * then rendered only the group priority ranking, which is a different question, and gave no account
 * at all of the consequence the whole disruption is about. `GET /incident-groups/{ref}/impacts`
 * answered 200 the entire time, which is what made this look like a rendering fault rather than a
 * missing action: the request an operator could see in the network log had succeeded.
 *
 * So connections is always offered, and when nothing has been recorded the surface says so and names
 * the action that would produce it. That is the difference between "no connection is at risk" and
 * "nobody has looked yet", and only one of them is true before the cascade advances.
 *
 * Pure and dependency-free so it is unit-testable: `vitest.config.ts` runs in a node environment and
 * collects `*.test.ts` only, so a rule living inside the component is a rule no test can reach.
 *
 * Owner: Stream D.
 */

export type ImpactTab = 'connections' | 'passengers' | 'priorities' | 'crew' | 'hotels';

export const IMPACT_TAB_LABEL: Record<ImpactTab, string> = {
  connections: 'Connections',
  passengers: 'Passengers',
  priorities: 'Recorded priorities',
  crew: 'Crew rotations',
  hotels: 'Hotels',
};

/** What the screen actually has, after reading the recorded payloads. */
export interface ImpactSurfaces {
  /** A `check_connections` payload was recorded. */
  hasConnections: boolean;
  /** An `assess_crew_impact` payload was recorded. */
  hasCrew: boolean;
  /** A `find_hotel_options` or `reserve_hotel_block` payload was recorded. */
  hasHotels: boolean;
  /** The incident belongs to a group, so the group-scoped ranking is addressable. */
  hasGroup: boolean;
}

/**
 * The tabs to offer, in reading order.
 *
 * `connections` is unconditional. `passengers` stays gated on a connection payload because its rows
 * *are* the connection rows — offering it without them would promise a passenger list this screen
 * cannot build. `priorities` is gated on group membership rather than on an action, because the
 * ranking is written at group scope by the orchestrator, and its panel states its own absence.
 */
export function impactTabs(surfaces: ImpactSurfaces): ImpactTab[] {
  return [
    'connections',
    ...(surfaces.hasConnections ? (['passengers'] as ImpactTab[]) : []),
    ...(surfaces.hasGroup ? (['priorities'] as ImpactTab[]) : []),
    ...(surfaces.hasCrew ? (['crew'] as ImpactTab[]) : []),
    ...(surfaces.hasHotels ? (['hotels'] as ImpactTab[]) : []),
  ];
}

/**
 * The tab to show: the requested one when it is still offered, else the first available.
 *
 * Needed because the set changes as payloads arrive. A tab selected while a request was in flight
 * must not leave the screen rendering nothing.
 */
export function resolveActiveTab(tabs: readonly ImpactTab[], requested: ImpactTab): ImpactTab {
  if (tabs.includes(requested)) return requested;
  return tabs[0] ?? 'connections';
}

/** The action whose payload the connections surface is read from. */
export const CONNECTIONS_ACTION_TYPE = 'check_connections';

/**
 * Why the connections surface is empty. Rendered verbatim, and deliberately not "none at risk".
 *
 * The distinction this sentence carries is the entire point of the fix: an operator who reads "no
 * connection is at risk" on a cascade nobody has assessed has been told the disruption is contained.
 */
export const CONNECTIONS_NOT_ASSESSED =
  `No ${CONNECTIONS_ACTION_TYPE} finding has been recorded for this incident, so no connection has ` +
  'been examined yet. This is not a statement that every connection holds — nobody has looked. ' +
  'Advance the disruption to produce the assessment.';

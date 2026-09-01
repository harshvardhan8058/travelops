import type { IncidentGroupDetail, IncidentGroupSummary } from '@/api/types';

export interface CascadeReader {
  currentGroup(): Promise<IncidentGroupSummary | null>;
  incidentGroup(reference: string): Promise<IncidentGroupDetail>;
}

/**
 * Resolve the static `/current` selector before loading detail-only arrays.
 *
 * The backend intentionally returns a summary from `/incident-groups/current`; only the dynamic
 * reference endpoint returns `flights`, `crew_pairings`, graph, and blast radius. Keeping this
 * sequence in one tested adapter prevents a summary from being cast to detail and handed to the
 * cascade renderer.
 */
export async function loadCascade(reader: CascadeReader, routeGroupId: string) {
  const isAlias = routeGroupId === 'current';
  const selected = isAlias ? await reader.currentGroup() : null;

  /*
   * `current` resolving to nothing is an answer, not a failure. The nav links here with a static
   * alias because the shell cannot know a group reference, so a restored dataset lands on this
   * screen constantly — and rejecting made the Cascade Explorer report "could not load cascade",
   * a load failure, for a database that was simply empty. Returning a null detail lets the screen
   * say which of the two it is.
   */
  if (isAlias && selected === null) return { selected: null, detail: null };

  const reference = selected?.reference ?? routeGroupId;
  const detail = await reader.incidentGroup(reference);
  return { selected, detail };
}

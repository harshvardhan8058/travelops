import type { IncidentGroupDetail, IncidentGroupSummary } from '@/api/types';

export interface CascadeReader {
  currentGroup(): Promise<IncidentGroupSummary>;
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
  const selected = routeGroupId === 'current' ? await reader.currentGroup() : null;
  const reference = selected?.reference ?? routeGroupId;
  const detail = await reader.incidentGroup(reference);
  return { selected, detail };
}

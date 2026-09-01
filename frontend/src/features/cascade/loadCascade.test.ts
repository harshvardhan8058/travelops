import { describe, expect, it, vi } from 'vitest';

import type { IncidentGroupDetail, IncidentGroupSummary } from '@/api/types';
import { loadCascade } from './loadCascade';

const summary = {
  id: 12,
  reference: 'SCN-20260831-ABC123',
  root_cause: 'weather',
  airport_icao: 'VOBL',
  severity: 'high',
  state: 'detected',
  opened_at: '2026-08-31T16:54:55Z',
  rollups: { flights_affected: 1 },
  awaiting_approval_count: 0,
  provenance: { kind: 'simulated', provider: 'scenario-builder' },
} satisfies IncidentGroupSummary;

const detail = {
  ...summary,
  flights: [],
  crew_pairings: [],
  mechanism_legend: {},
  why_nine_not_eight: '',
} as unknown as IncidentGroupDetail;

describe('loadCascade', () => {
  it('resolves /cascade/current from summary to the full detail endpoint', async () => {
    const currentGroup = vi.fn(async () => summary);
    const incidentGroup = vi.fn(async () => detail);

    const result = await loadCascade({ currentGroup, incidentGroup }, 'current');

    expect(currentGroup).toHaveBeenCalledOnce();
    expect(incidentGroup).toHaveBeenCalledWith(summary.reference);
    expect(result).toEqual({ selected: summary, detail });
    expect(result.detail?.flights).toEqual([]);
    expect(result.detail?.crew_pairings).toEqual([]);
  });

  it('reports an empty dataset as no group rather than fetching a detail for nothing', async () => {
    /*
     * `/current` answers `null` on a restored dataset. Fetching detail for a reference of `current`
     * would 404 for a condition the Cascade Explorer renders calmly, and the screen reported
     * "could not load cascade" — a load failure — for a database that was simply empty.
     */
    const currentGroup = vi.fn(async () => null);
    const incidentGroup = vi.fn(async () => detail);

    const result = await loadCascade({ currentGroup, incidentGroup }, 'current');

    expect(currentGroup).toHaveBeenCalledOnce();
    expect(incidentGroup).not.toHaveBeenCalled();
    expect(result).toEqual({ selected: null, detail: null });
  });

  it('loads an explicit reference directly without consulting current selection', async () => {
    const currentGroup = vi.fn(async () => summary);
    const incidentGroup = vi.fn(async () => detail);

    await loadCascade({ currentGroup, incidentGroup }, summary.reference);

    expect(currentGroup).not.toHaveBeenCalled();
    expect(incidentGroup).toHaveBeenCalledWith(summary.reference);
  });
});

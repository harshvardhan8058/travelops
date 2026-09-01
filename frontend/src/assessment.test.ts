import { describe, expect, it } from 'vitest';

import { dimensionAssessment, groupAssessment } from './assessment';
import type { GroupRollupStatus } from '@/api/types';

function status(over: Partial<GroupRollupStatus> = {}): GroupRollupStatus {
  return {
    is_complete: false,
    computed_at: null,
    note: '',
    flights_without_incident: [],
    membership_is_declared: true,
    incidents_in_group: 4,
    incidents_assessed_connections: 0,
    incidents_assessed_crew: 0,
    ...over,
  };
}

describe('dimensionAssessment', () => {
  it('refuses to call an unassessed dimension measured', () => {
    const result = dimensionAssessment(status(), 'connections', 'connections');

    expect(result.state).toBe('none');
    expect(result.isMeasured).toBe(false);
    expect(result.note).toContain('No incident has been assessed');
  });

  it('treats a fully assessed dimension as measured, so a zero is a finding', () => {
    const result = dimensionAssessment(
      status({ incidents_assessed_connections: 4 }),
      'connections',
      'connections',
    );

    expect(result.state).toBe('assessed');
    expect(result.isMeasured).toBe(true);
    expect(result.note).toBe('');
  });

  it('marks a part-assessed dimension as a floor', () => {
    const result = dimensionAssessment(
      status({ incidents_assessed_crew: 1 }),
      'crew',
      'crew impact',
    );

    expect(result.state).toBe('partial');
    expect(result.isMeasured).toBe(true);
    expect(result.note).toContain('1 of 4');
    expect(result.note).toContain('floor');
  });

  it('separates the two dimensions rather than reading one composite flag', () => {
    // The exact case `is_complete` cannot express: connections done, crew not.
    const both = status({ incidents_assessed_connections: 4, incidents_assessed_crew: 0 });

    expect(dimensionAssessment(both, 'connections', 'connections').isMeasured).toBe(true);
    expect(dimensionAssessment(both, 'crew', 'crew impact').isMeasured).toBe(false);
  });

  it('says nothing is open rather than nothing was found, for a group with no incidents', () => {
    const result = dimensionAssessment(
      status({ incidents_in_group: 0 }),
      'connections',
      'connections',
    );

    expect(result.state).toBe('empty');
    expect(result.isMeasured).toBe(false);
  });

  it('does not read an older server as evidence that nothing ran', () => {
    const legacy: GroupRollupStatus = {
      is_complete: true,
      computed_at: null,
      note: '',
      flights_without_incident: [],
      membership_is_declared: true,
    };
    const result = dimensionAssessment(legacy, 'connections', 'connections');

    expect(result.state).toBe('unknown');
    expect(result.isMeasured).toBe(true);
  });

  it('classifies an absent status as unknown, not as zero', () => {
    expect(dimensionAssessment(undefined, 'crew', 'crew impact').state).toBe('unknown');
  });
});

describe('groupAssessment', () => {
  it('counts an incident as assessed only when both findings are recorded', () => {
    const result = groupAssessment(
      status({ incidents_assessed_connections: 4, incidents_assessed_crew: 1 }),
    );

    // Not 4: taking the higher counter would overstate exactly what this reports.
    expect(result.fullyAssessed).toBe(1);
    expect(result.awaiting).toBe(3);
    expect(result.isPartial).toBe(true);
  });

  it('reports a fully worked group as not partial', () => {
    const result = groupAssessment(
      status({
        is_complete: true,
        incidents_assessed_connections: 4,
        incidents_assessed_crew: 4,
      }),
    );

    expect(result.fullyAssessed).toBe(4);
    expect(result.awaiting).toBe(0);
    expect(result.isPartial).toBe(false);
  });

  it('names declared flights carrying no incident', () => {
    const result = groupAssessment(status({ flights_without_incident: [11, 12] }));

    expect(result.flightsWithoutIncident).toBe(2);
    expect(result.isPartial).toBe(true);
  });

  it('reports unknown when the server publishes no counters', () => {
    const result = groupAssessment({
      is_complete: false,
      computed_at: null,
      note: '',
      flights_without_incident: [],
      membership_is_declared: true,
    });

    expect(result.isKnown).toBe(false);
  });
});

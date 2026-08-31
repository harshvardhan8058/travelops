import { describe, expect, it, vi } from 'vitest';

import type {
  FlightRow,
  IncidentGroupDetail,
  IncidentGroupSummary,
  ScenarioCreateResponse,
  ScenarioStartResponse,
} from '@/api/types';
import type { ScenarioDraft } from './scenarioContracts';
import { buildPublishedScenarioRequest, submitScenario } from './scenarioLifecycle';

const provenance = { kind: 'simulated', provider: 'scenario-builder' } as const;

function flight(
  id: number,
  flight_number: string,
  origin_icao: string,
  destination_icao: string,
  delay_minutes: number,
): FlightRow {
  return {
    id,
    flight_number,
    airline_code: flight_number.split(' ')[0]!,
    origin_icao,
    destination_icao,
    scheduled_departure: '2026-08-31T16:00:00Z',
    estimated_departure: null,
    delay_minutes,
    block_time_minutes: 90,
    status: 'delayed',
    risk_index: 80,
    risk_level: 'high',
    passengers: 100,
    connections_at_risk: 2,
    incident_reference: null,
    provenance,
  };
}

const flights = [
  flight(1, '6E 2134', 'VOBL', 'VIDP', 420),
  flight(2, '6E 811', 'VOBL', 'VABB', 110),
  flight(4, 'UK 864', 'VIDP', 'VOBL', 0),
];

const draft: ScenarioDraft = {
  templateId: 'bengaluru-monsoon-storm',
  name: 'Live Bengaluru scenario',
  disruptionType: 'weather',
  airportIcao: 'VOBL',
  startsAt: '2026-08-31T16:54',
  durationMinutes: 180,
  severity: 'high',
  flightNumbers: ['6E 2134', '6E 811', 'UK 864'],
  primaryFlight: '6E 2134',
  notes: 'Created by regression test.',
};

const reference = 'SCN-20260831-ABC123';
const created = {
  scenario_reference: reference,
  state: 'detected',
  root_cause: 'weather',
  airport_icao: 'VOBL',
  severity: 'high',
  effective_at: '2026-08-31T16:54:00Z',
  members: [],
  created_by: 'operator-1',
  created_at: '2026-08-31T16:50:00Z',
  provenance,
  replayed: false,
} satisfies ScenarioCreateResponse;
const started = {
  scenario_reference: reference,
  state: 'detected',
  members: [],
  opened_incident_ids: [101, 102, 103],
  blocked_reason: null,
  awaiting_approval_count: 0,
  started_by: 'operator-1',
  started_at: '2026-08-31T16:50:01Z',
  provenance,
  replayed: false,
} satisfies ScenarioStartResponse;
const selected = {
  id: 12,
  reference,
  root_cause: 'weather',
  airport_icao: 'VOBL',
  severity: 'high',
  state: 'detected',
  opened_at: '2026-08-31T16:54:00Z',
  rollups: { flights_affected: 3 },
  awaiting_approval_count: 0,
  provenance,
} satisfies IncidentGroupSummary;
const detail = {
  ...selected,
  flights: [],
  crew_pairings: [],
  mechanism_legend: {},
  why_nine_not_eight: '',
} as unknown as IncidentGroupDetail;

describe('published scenario lifecycle', () => {
  it('maps recorded IDs, delays, and root-airport roles into the backend create contract', () => {
    expect(buildPublishedScenarioRequest(draft, flights)).toEqual({
      root_cause: 'weather',
      airport_icao: 'VOBL',
      severity: 'high',
      effective_at: '2026-08-31T16:54:00.000Z',
      actor_id: 'operator-1',
      members: [
        { flight_id: 1, role: 'primary', delay_minutes: 420 },
        { flight_id: 2, role: 'affected_departure', delay_minutes: 110 },
        { flight_id: 4, role: 'affected_arrival', delay_minutes: 0 },
      ],
    });
  });

  it('proves Create & Run resolves /cascade/current to the resulting active cascade', async () => {
    const client = {
      createScenario: vi.fn(async () => created),
      startScenario: vi.fn(async () => started),
      currentGroup: vi.fn(async () => selected),
      incidentGroup: vi.fn(async () => detail),
    };

    const result = await submitScenario(client, draft, flights, {
      runAfterCreate: true,
      operationKey: 'scenario-regression',
    });

    expect(client.createScenario).toHaveBeenCalledWith(
      buildPublishedScenarioRequest(draft, flights),
      'scenario-regression-create',
    );
    expect(client.startScenario).toHaveBeenCalledWith(
      reference,
      'operator-1',
      'scenario-regression-start',
    );
    expect(client.currentGroup).toHaveBeenCalledOnce();
    expect(client.incidentGroup).toHaveBeenCalledWith(reference);
    expect(result.route).toBe('/cascade/current');
    expect(result.detail).toBe(detail);
  });

  it('does not claim /cascade/current when the backend selected another group', async () => {
    const client = {
      createScenario: vi.fn(async () => created),
      startScenario: vi.fn(async () => started),
      currentGroup: vi.fn(async () => ({ ...selected, reference: 'GRP-OTHER' })),
      incidentGroup: vi.fn(async () => detail),
    };

    await expect(
      submitScenario(client, draft, flights, {
        runAfterCreate: true,
        operationKey: 'scenario-mismatch',
      }),
    ).rejects.toMatchObject({
      progress: { created, started, route: null },
      cause: { code: 'CURRENT_GROUP_MISMATCH' },
    });
    expect(client.incidentGroup).not.toHaveBeenCalled();
  });

  it('preserves committed progress and reuses the same keys when a retry resumes', async () => {
    const startScenario = vi
      .fn()
      .mockRejectedValueOnce(new Error('start transport failed'))
      .mockResolvedValueOnce(started);
    const client = {
      createScenario: vi.fn(async () => created),
      startScenario,
      currentGroup: vi.fn(async () => selected),
      incidentGroup: vi.fn(async () => detail),
    };
    const options = { runAfterCreate: true, operationKey: 'stable-attempt' };

    await expect(submitScenario(client, draft, flights, options)).rejects.toMatchObject({
      progress: { created, started: null, route: null },
      cause: { message: 'start transport failed' },
    });
    const resumed = await submitScenario(client, draft, flights, options);

    expect(client.createScenario).toHaveBeenNthCalledWith(
      1,
      buildPublishedScenarioRequest(draft, flights),
      'stable-attempt-create',
    );
    expect(client.createScenario).toHaveBeenNthCalledWith(
      2,
      buildPublishedScenarioRequest(draft, flights),
      'stable-attempt-create',
    );
    expect(startScenario).toHaveBeenNthCalledWith(
      1,
      reference,
      'operator-1',
      'stable-attempt-start',
    );
    expect(startScenario).toHaveBeenNthCalledWith(
      2,
      reference,
      'operator-1',
      'stable-attempt-start',
    );
    expect(resumed.route).toBe('/cascade/current');
  });

  it('names flight designators absent from the live flight board instead of posting guesses', () => {
    expect(() =>
      buildPublishedScenarioRequest(
        { ...draft, flightNumbers: ['6E 2134', 'AI 999'], primaryFlight: '6E 2134' },
        flights,
      ),
    ).toThrow(/AI 999/);
  });
});

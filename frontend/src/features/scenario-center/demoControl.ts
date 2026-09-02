/**
 * The decisions behind the Scenario Center, kept out of the component so they can be tested.
 *
 * Three of them are load-bearing.
 *
 * **A simulation is passed through, not rebuilt.** `simulationToScenarioRequest` copies the
 * published `flight_id`, `role` and `delay_minutes` verbatim. It does not look a flight up, does not
 * recompute a delay and does not decide who the primary is — the backend resolved the selection
 * against recorded rows and `POST /scenarios` refuses a declared delay that disagrees with the
 * recorded one. That refusal is the guarantee: if this module ever started composing operational
 * facts, the scenario contract would reject them rather than a fabricated disruption reaching an
 * operator. `effective_at` is the one value supplied here, because "when is this happening" is a
 * property of running the simulation now rather than of the selection.
 *
 * **The reset phrase is the server's, not a copy.** `RESET_CONFIRMATION` mirrors
 * `backend/app/schemas/demo.py`, and `demoControl.test.ts` reads that file to prove the two agree.
 * A drifted phrase would present an operator with a control that can never succeed.
 *
 * **A blocked simulation is still a simulation.** `runnable: false` comes from the server with a
 * reason; this module surfaces the reason rather than filtering the entry out, because a catalogue
 * that silently drops its third entry leaves an operator wondering where it went.
 *
 * Owner: Stream D.
 */

import type { DemoDatasetResponse, ScenarioCreateRequest, SimulationDefinition } from '@/api/types';

/**
 * The phrase `POST /demo/reset` requires, mirroring `RESET_CONFIRMATION` in
 * `backend/app/schemas/demo.py`. Rendered to the operator so the control states what it wants.
 */
export const RESET_CONFIRMATION = 'reset demo data';

/**
 * Whether a typed phrase satisfies the reset gate.
 *
 * Matches the server's own comparison — `payload.confirm.strip().lower()` — so the button enables
 * exactly when the request would be accepted. A stricter client check would disable a control the
 * server would have honoured; a looser one would enable a request destined for a 422.
 *
 * This is an affordance, never the guard: the server re-checks the phrase regardless.
 */
export function resetConfirmationMatches(typed: string): boolean {
  return typed.trim().toLowerCase() === RESET_CONFIRMATION;
}

/**
 * Turn a published simulation into the scenario request that starts it.
 *
 * Every operational value is copied, **including the instant**.
 *
 * `effective_at` deliberately cannot be supplied by the caller. It used to be a parameter, and the
 * Scenario Center passed `new Date().toISOString()` — which looked obviously right and was the one
 * defect that made a browser-started demo unfinishable. The dataset's evidence is a fixed-seed
 * snapshot recorded against the scenario's own date, so an incident opened "now" is evaluated
 * against a METAR that is however many days old this machine is. `sources_fresh` FAILs with
 * `SOURCE_STALE`, and an evidence failure is refused by `enforce_action_approval` with 409
 * `NOT_APPROVABLE_EVIDENCE` — approval covers risk, never failed evidence. The cascade then parks
 * on a hold no operator can clear.
 *
 * Removing the parameter is the fix, not a workaround: the recorded clock is a property of the
 * selection, the backend reads it from the seeded group row, and there is now no way for this module
 * to express a wall-clock time. `demoControl.test.ts` asserts the source reads no clock at all.
 */
export function simulationToScenarioRequest(
  simulation: SimulationDefinition,
  options: { actorId?: string } = {},
): ScenarioCreateRequest {
  return {
    root_cause: simulation.root_cause,
    airport_icao: simulation.airport_icao,
    severity: simulation.severity,
    effective_at: simulation.effective_at,
    actor_id: options.actorId ?? 'operator-1',
    members: simulation.members.map((member) => ({
      // Verbatim. Recomputing any of these would be inventing the disruption.
      flight_id: member.flight_id,
      role: member.role,
      delay_minutes: member.delay_minutes,
    })),
  };
}

export interface SimulationReadiness {
  /** True only when the server said so AND there is something to declare. */
  canStart: boolean;
  /** Why not, in the server's words where it gave them. Null when it can start. */
  reason: string | null;
}

/**
 * Whether this simulation can be started, and why not when it cannot.
 *
 * `runnable` is the server's answer and is trusted. The extra member check is not second-guessing
 * it — it closes the case where a definition is marked runnable but carries nothing to declare,
 * which would POST an empty `members` array and surface as a validation error from a button that
 * looked ready.
 */
export function simulationReadiness(simulation: SimulationDefinition): SimulationReadiness {
  if (!simulation.runnable) {
    return {
      canStart: false,
      reason:
        simulation.blocked_reason ??
        'The dataset cannot support this simulation, and the server gave no reason.',
    };
  }
  if (simulation.members.length === 0) {
    return {
      canStart: false,
      reason: 'The server reported this simulation as runnable but declared no flights.',
    };
  }
  return { canStart: true, reason: null };
}

/**
 * Why a start control is unavailable, in precedence order, or `null` when it is available.
 *
 * Ordered most-fundamental first so the message names the thing to fix rather than the last
 * condition tested. Writes being impossible outranks a dataset problem, which outranks a
 * definition problem.
 */
export function startBlockedReason(
  simulation: SimulationDefinition,
  context: { canWrite: boolean; isSeeded: boolean; isBusy: boolean },
): string | null {
  if (!context.canWrite) {
    return 'Fixtures are being served. Point the console at the live API to start a simulation.';
  }
  if (!context.isSeeded) {
    return 'The dataset is not seeded, so there are no recorded flights to select.';
  }
  const readiness = simulationReadiness(simulation);
  if (!readiness.canStart) return readiness.reason;
  if (context.isBusy) return 'Another demo operation is in progress.';
  return null;
}

/**
 * Why the reset control is unavailable, in precedence order, or `null` when it is available.
 *
 * The typed phrase is checked LAST on purpose: telling an operator to type a phrase into a control
 * that the environment forbids anyway is a dead end, and the phrase is the step they can act on
 * only once everything else is satisfied.
 */
export function resetBlockedReason(context: {
  canWrite: boolean;
  resetAllowed: boolean;
  isBusy: boolean;
  typed: string;
}): string | null {
  if (!context.canWrite) {
    return 'Fixtures are being served. Point the console at the live API to reset the dataset.';
  }
  if (!context.resetAllowed) {
    return 'This environment does not permit destructive demo controls.';
  }
  if (context.isBusy) return 'Another demo operation is in progress.';
  if (!resetConfirmationMatches(context.typed)) {
    return `Type "${RESET_CONFIRMATION}" to enable the reset.`;
  }
  return null;
}

/**
 * How much of the dataset is in play, in the four words an operator actually needs.
 *
 * `CLEAN` — nothing is open. Every simulation can run.
 * `ACTIVE` — at least one group is being worked, which is why simulations are refused.
 * `PARTIALLY_PROCESSED` — every group has stopped, but not all of them at `resolved`.
 * `RESOLVED` — every group finished, and finished successfully.
 */
export type DatasetStatus = 'CLEAN' | 'ACTIVE' | 'PARTIALLY_PROCESSED' | 'RESOLVED' | 'UNKNOWN';

export interface DatasetStatusView {
  status: DatasetStatus;
  /** One sentence naming the consequence, not just restating the word. */
  detail: string;
  /** True when a reset is the thing that would unblock the operator. */
  resetWouldClear: boolean;
}

/** Group states that mean work is still in progress. Mirrors `IncidentState.active()`. */
const ACTIVE_GROUP_STATES = new Set([
  'detected',
  'assessing',
  'planning',
  'assuring',
  'awaiting_approval',
  'executing',
]);

/**
 * Derive the dataset's status from what the server already publishes.
 *
 * The backend has no dataset-status field, and deliberately so: a group's state is derived from its
 * members rather than stored, and the same argument applies one level up. `GET /incident-groups`
 * already returns every group's state, so the fold is the only missing step and it belongs where it
 * can be tested — here, not inside a component.
 *
 * This exists because the Scenario Center could say "cannot run against the current dataset" with
 * complete accuracy and still leave an operator with no idea what to do about it. The refusal names
 * the incidents that own the flights; it does not say that the dataset as a whole is busy, or that
 * one control on the same screen clears it.
 *
 * `UNKNOWN` rather than a guess when the group list has not arrived: an unstarted dataset and an
 * unanswered query are different things, and only one of them means "go ahead".
 */
export interface DatasetGroup {
  state: string;
  /** How many incidents the group holds. The direct answer, when the server sends it. */
  incidentsInGroup?: number;
  /** Declared member flights. */
  flightsAffected: number;
  /** Declared member flights that have no incident open. */
  flightsWithoutIncident: number;
}

/**
 * Whether a group has actually been started, by the server's own definition of started.
 *
 * A seeded group exists before anything runs, and it sits in `detected` with no incidents at all —
 * so folding raw states reported a freshly reset dataset as ACTIVE, which is the opposite of the
 * truth and pointed the operator at a reset they had just performed. `/incident-groups/current`
 * already draws this line ("a group with no incident has not been started"); this draws the same
 * one from the fields the list publishes, rather than inventing a second rule.
 *
 * `incidents_in_group` answers it outright and is preferred. The flight comparison is the fallback
 * for a response predating that counter: when every declared flight still has no incident, nothing
 * has been opened. Both express the same rule; neither infers it from the group's state, because
 * `detected` is exactly the state a started group and an untouched one share.
 */
export function groupIsStarted(group: DatasetGroup): boolean {
  if (typeof group.incidentsInGroup === 'number') return group.incidentsInGroup > 0;
  if (group.flightsAffected <= 0) return false;
  return group.flightsWithoutIncident < group.flightsAffected;
}

export function datasetStatus(input: {
  isSeeded: boolean;
  groups: DatasetGroup[] | undefined;
}): DatasetStatusView {
  if (input.groups === undefined) {
    return {
      status: 'UNKNOWN',
      detail: 'Waiting for the group list before reporting what is open.',
      resetWouldClear: false,
    };
  }
  if (!input.isSeeded) {
    return {
      status: 'UNKNOWN',
      detail: 'The reference dataset is not seeded, so there are no recorded flights to work from.',
      resetWouldClear: true,
    };
  }
  // Declared-but-unstarted groups are not part of this answer. They own no flights, block no
  // simulation, and counting them was the defect above.
  const started = input.groups.filter(groupIsStarted);
  if (started.length === 0) {
    return {
      status: 'CLEAN',
      detail: 'No disruption is open. Every simulation can run against this dataset.',
      resetWouldClear: false,
    };
  }
  const groupStates = started.map((group) => group.state);
  const active = groupStates.filter((state) => ACTIVE_GROUP_STATES.has(state));
  if (active.length > 0) {
    return {
      status: 'ACTIVE',
      detail:
        `${active.length} disruption${active.length === 1 ? '' : 's'} still being worked. Their ` +
        'flights belong to that workflow, so a new simulation cannot declare them.',
      resetWouldClear: true,
    };
  }
  // Every group has stopped. `resolved` requires every member resolved — seven of eight is not
  // success — so a mixed terminal set is partially processed, not finished.
  if (groupStates.every((state) => state === 'resolved')) {
    return {
      status: 'RESOLVED',
      detail:
        'Every disruption finished and resolved. Flights stay owned by those workflows, so reset ' +
        'the dataset to run a simulation again.',
      resetWouldClear: true,
    };
  }
  return {
    status: 'PARTIALLY_PROCESSED',
    detail:
      'Every disruption has stopped, but not all of them resolved. Reset the dataset for a clean ' +
      'run.',
    resetWouldClear: true,
  };
}

export interface DatasetHeadline {
  label: string;
  value: number;
  /** The response field this was read from, so the figure's popover can name it. */
  field: string;
  /** Whether this figure is reference seed data or the workflow's own output. */
  origin: 'reference' | 'workflow';
}

/**
 * The figures an operator recognises, split by origin.
 *
 * The split is the point. Reference rows come from the fixed-seed dataset and a reset restores them;
 * groups and incidents are the workflow's output and a reset REMOVES them. Presenting all six as one
 * undifferentiated row of tiles would make "reset" look like it does the same thing to each.
 */
export function datasetHeadlines(dataset: DemoDatasetResponse): DatasetHeadline[] {
  return [
    { label: 'Airports', value: dataset.airports, field: 'airports', origin: 'reference' },
    { label: 'Flights', value: dataset.flights, field: 'flights', origin: 'reference' },
    { label: 'Bookings', value: dataset.bookings, field: 'bookings', origin: 'reference' },
    {
      label: 'Booking segments',
      value: dataset.booking_segments,
      field: 'booking_segments',
      origin: 'reference',
    },
    {
      label: 'Disruption groups',
      value: dataset.incident_groups,
      field: 'incident_groups',
      origin: 'workflow',
    },
    { label: 'Incidents', value: dataset.incidents, field: 'incidents', origin: 'workflow' },
  ];
}

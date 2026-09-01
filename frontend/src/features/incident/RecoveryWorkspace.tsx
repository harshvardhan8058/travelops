/**
 * Recovery Workspace — `/incidents/:incidentId`. docs/27-ui-specification.md screen 2.
 *
 * Where the actual work happens, and the middle of the demo path:
 *
 *   Ops Board -> Recovery Workspace -> assurance decision -> approval -> execution -> Timeline
 *
 * Three columns, one job each. LEFT is what the system knew (inputs, nothing editable).
 * CENTRE is what it decided to do and who produced that plan. RIGHT is what the gate made of
 * the selected task, and the approve/reject control when a human must decide.
 *
 * The default selection is deliberate: the first task the gate blocked, not the first task in
 * the list. Opening this screen should land an operator on the thing that needs them.
 *
 * Owner: Stream D.
 */

import { useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { PlayCircle } from 'lucide-react';

import { api, ApiError } from '@/api/client';
import type { AssuranceEvaluation, HumanDecision, IncidentDetail, RunResponse } from '@/api/types';
import {
  ErrorState,
  LoadingState,
  MonoValue,
  Panel,
  ProvenanceDot,
  StateBadge,
  StateRail,
  WhyPopover,
} from '@/components/ui/primitives';
import { elapsedDerivation } from '@/components/ui/derivation';
import { Button, Labelled, Notice, PageHeader, Toolbar } from '@/components/ui/composition';
import { utcClock } from '@/components/ui/format';
import { AssurancePanel } from '@/features/assurance/AssurancePanel';
import { preferredTaskId } from '@/features/assurance/authorizationState';
import { PlanAssuranceMatrix } from '@/features/assurance/PlanAssuranceMatrix';
import { EvidenceColumn } from './EvidenceColumn';
import { PlanColumn } from './PlanColumn';
import { ActionsStrip } from './ActionsStrip';

/** Skeletons match the final three-column geometry so nothing reflows when data lands. */
function WorkspaceSkeleton() {
  return (
    <div className="flex flex-col gap-3">
      <Panel>
        <div className="px-3 py-2">
          <LoadingState label="Loading incident" />
        </div>
      </Panel>
      <div className="grid items-start gap-3 lg:grid-cols-[300px_minmax(0,1fr)_360px] 2xl:grid-cols-[340px_minmax(0,1fr)_400px]">
        {['Evidence', 'Plan', 'Assurance'].map((title) => (
          <Panel key={title} title={title}>
            <div className="h-[420px] px-3 py-2">
              <LoadingState label={`Loading ${title.toLowerCase()}`} />
            </div>
          </Panel>
        ))}
      </div>
    </div>
  );
}

/**
 * Deliberately NOT `durationBetween` from `@/components/ui/format`.
 *
 * That helper reports whole minutes, which is right for a delay or a freshness window. This one
 * needs seconds: the deterministic slice resolves an incident in around twenty seconds, and a
 * minute-resolution formatter renders that as `0m` — a recovery that reads as having taken no time
 * at all. The one place in the product that needs second resolution keeps its own formatter.
 */
function formatDuration(fromIso: string, toIso: string): string {
  const seconds = Math.max(0, Math.round((Date.parse(toIso) - Date.parse(fromIso)) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, '0')}m`;
  return `${minutes}m ${String(seconds % 60).padStart(2, '0')}s`;
}

/**
 * Every timestamp the records carry for this incident, oldest first.
 *
 * `incident.opened_at` is deliberately NOT in here. In an injected scenario it is the
 * disruption's own time — 2026-08-20T15:36 for bengaluru_storm — while the state transitions
 * carry the real time the workflow ran. Measuring from one to the other produced
 * "ELAPSED 26h 46m" for a recovery that took 21 seconds. Stream A removed exactly this
 * clock-mixing from the state rail; this is the same bug on the same screen.
 *
 * Both ends now come from the same clock: the recorded transitions and actions.
 */
function recordedStamps(incident: IncidentDetail): { at: string; label: string }[] {
  return [
    ...incident.state_rail
      .filter((step) => step.reached_at !== null)
      .map((step) => ({ at: step.reached_at as string, label: `state ${step.state}` })),
    ...incident.actions
      .filter((action) => action.executed_at !== null)
      .map((action) => ({ at: action.executed_at as string, label: `action ${action.id}` })),
  ].sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
}

/**
 * Terminal states cannot advance. The backend returns a note saying so rather than erroring, but
 * there is no reason to offer the control.
 */
function isTerminalIncident(incident: IncidentDetail): boolean {
  return (
    incident.state === 'resolved' || incident.state === 'blocked' || incident.state === 'failed'
  );
}

/**
 * Why the workflow cannot be advanced, or `undefined` when it can.
 *
 * Lifted out of `Header` because there are now two places that offer the run: the header, and the
 * assurance panel once an approval is recorded and un-executed. Two copies of this condition would
 * eventually disagree, and the visible symptom would be one enabled button and one disabled button
 * for the same action — so the derivation is shared and the wording is identical in both.
 */
function runBlockedReason(incident: IncidentDetail): string | undefined {
  if (!api.canWrite) {
    return 'Fixtures are being served. Point the console at the live API to advance the workflow.';
  }
  if (isTerminalIncident(incident)) {
    return `This incident is terminal in ${incident.state} and cannot advance.`;
  }
  return undefined;
}

function Header({
  incident,
  onRun,
  isRunning,
  runError,
}: {
  incident: IncidentDetail;
  onRun: () => void;
  isRunning: boolean;
  runError?: string | null;
}) {
  const { flight } = incident;
  const stamps = recordedStamps(incident);
  const first = stamps[0] ?? null;
  // Needs two distinct records to be an interval; one record is a point in time, not a duration.
  const latest = stamps.length > 1 ? (stamps[stamps.length - 1] ?? null) : null;
  const isTerminal = isTerminalIncident(incident);
  const blockedReason = runBlockedReason(incident);

  return (
    <>
      {/*
       * The FLIGHT is the subject of this screen, not the incident reference.
       *
       * An operator arrives here from the board thinking "6E 2134", and the aircraft is what the
       * recovery is about; the incident reference is how the record is filed. So the flight number
       * is the title and the reference moves into the supporting row. Both are contract values and
       * neither is CSS-transformed.
       */}
      <PageHeader
        eyebrow="Recovery workspace"
        title={
          <span className="flex flex-wrap items-baseline gap-2.5">
            <span className="font-mono tabular-nums">{flight.flight_number}</span>
            {flight.route && (
              <span className="font-mono text-subtitle text-fg-secondary">{flight.route}</span>
            )}
            {flight.delay_minutes !== undefined && flight.delay_minutes > 0 && (
              <span className="font-mono text-subtitle text-state-warn">
                +{flight.delay_minutes}m
              </span>
            )}
          </span>
        }
        status={<StateBadge status={incident.severity} label={`severity ${incident.severity}`} />}
        meta={
          <>
            <Labelled label="incident">
              <MonoValue muted>{incident.reference}</MonoValue>
            </Labelled>
            <Labelled label="trigger">
              <MonoValue muted>{incident.trigger_type}</MonoValue>
            </Labelled>
            <Labelled label="opened">
              <MonoValue muted>{utcClock(incident.opened_at) ?? 'not recorded'}Z</MonoValue>
            </Labelled>
            <Labelled label="workflow">
              {/* Both ends are recorded transitions, so this cannot mix two clocks. */}
              <WhyPopover derivation={elapsedDerivation(incident, first, latest)}>
                <MonoValue>
                  {first && latest ? formatDuration(first.at, latest.at) : 'not derivable'}
                </MonoValue>
              </WhyPopover>
            </Labelled>
            <ProvenanceDot
              kind={incident.provenance.kind}
              provider={incident.provenance.provider}
              sourceRef={incident.provenance.source_ref}
            />
          </>
        }
        actions={
          <Toolbar className="items-start">
            {/*
             * The real POST /incidents/{id}/run. Disabled for two honest reasons rather than one
             * vague one: a terminal incident cannot advance, and fixture mode has no endpoint to
             * call. Both say which applies — now in the button's own title as well as beside it,
             * so the reason travels with the control that refused.
             */}
            <div className="flex flex-col items-end gap-1">
              <Button
                variant="primary"
                size="md"
                icon={PlayCircle}
                onClick={onRun}
                disabled={isRunning || isTerminal || !api.canWrite}
                aria-disabled={isRunning || isTerminal || !api.canWrite}
                disabledReason={blockedReason}
              >
                {isRunning ? 'Running…' : 'Run workflow'}
              </Button>
              <span className="max-w-[240px] text-right text-caption text-fg-muted">
                {blockedReason ?? 'advances the workflow one run'}
              </span>
            </div>
          </Toolbar>
        }
        footer={<StateRail rail={incident.state_rail} current={incident.state} />}
      />

      {runError && (
        <Notice tone="crit" alert divider="none" className="rounded border">
          {runError}
        </Notice>
      )}
    </>
  );
}

/** Pseudonymous, and the backend's own default. Never a name or an email address. */
const ACTOR_ID = 'operator-1';

/**
 * The persisted decision embedded on an evaluation, normalised to the UI's shape.
 *
 * This is what makes a reload truthful: once the API has the record, the panel reads it from the
 * API rather than from whatever this browser session happens to remember.
 */
function persistedDecision(evaluation: AssuranceEvaluation): HumanDecision | undefined {
  const record = evaluation.human_decision;
  if (!record) return undefined;
  return {
    id: record.id,
    assurance_id: evaluation.id,
    decision: record.decision,
    actor_id: record.actor_id,
    reason: record.reason,
    decided_at: record.decided_at,
    persisted: true,
  };
}

export function RecoveryWorkspace() {
  const { incidentId = '' } = useParams();
  const queryClient = useQueryClient();
  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null);
  const [decisions, setDecisions] = useState<Record<number, HumanDecision>>({});
  const [lastRun, setLastRun] = useState<RunResponse | null>(null);
  const [runToken, setRunToken] = useState(() => crypto.randomUUID());

  const incidentQuery = useQuery({
    queryKey: ['incident', incidentId],
    queryFn: () => api.incident(incidentId),
    enabled: incidentId.length > 0,
  });

  const assuranceQuery = useQuery({
    queryKey: ['assurance', incidentId],
    queryFn: () => api.assurance(incidentId),
    enabled: incidentId.length > 0,
    refetchInterval: 10_000,
  });

  const incident = incidentQuery.data;

  const timelineQuery = useQuery({
    queryKey: ['timeline', incidentId],
    queryFn: () => api.timeline(incidentId),
    enabled: incidentId.length > 0,
    refetchInterval: 10_000,
  });

  /*
   * Land on the blocked task. An operator opening this screen mid-disruption wants the thing
   * waiting for them, not task 1 which already succeeded. `plan` is null until the orchestrator
   * proposes one, so there is nothing to select on a freshly detected incident.
   */
  useEffect(() => {
    if (!incident?.plan || selectedTaskId !== null) return;
    /*
     * Wait for the evaluations before choosing.
     *
     * The choice needs them: a task in `needs_human` is not necessarily waiting for a person — it is
     * also the state of a task whose gate said `execute` and whose service then failed. Selecting on
     * task state alone landed the operator on `reserve_hotel_block`, stalled on a
     * SERVICE_NOT_IMPLEMENTED refusal, while `notify_passengers` was the task actually holding for
     * their decision. Choosing before the evaluations arrive would make that the permanent choice,
     * because this effect only ever selects once.
     */
    if (assuranceQuery.isLoading) return;
    setSelectedTaskId(preferredTaskId(incident.plan.tasks, assuranceQuery.data?.evaluations ?? []));
  }, [incident, assuranceQuery.data, assuranceQuery.isLoading, selectedTaskId]);

  const decisionMutation = useMutation({
    mutationFn: async (input: {
      assuranceId: number;
      decision: 'approved' | 'rejected';
      reason: string;
    }): Promise<HumanDecision> => {
      /*
       * The real endpoint exists now: POST /assurance/{id}/decision, verified against the live
       * API including its replay behaviour and its 409 on a conflicting decision. The
       * session-only branch remains for fixture mode, because the demo path must run with no
       * backend — and it is labelled as unpersisted on screen rather than passing for an audit
       * record.
       */
      if (api.canWrite) {
        const written = await api.submitDecision(
          input.assuranceId,
          input.decision,
          input.reason,
          ACTOR_ID,
        );
        return {
          assurance_id: written.assurance_id,
          decision: written.decision,
          actor_id: written.actor_id,
          reason: written.reason,
          decided_at: written.decided_at,
          persisted: true,
        };
      }

      return {
        assurance_id: input.assuranceId,
        decision: input.decision,
        actor_id: ACTOR_ID,
        reason: input.reason,
        decided_at: new Date().toISOString(),
        persisted: false,
      };
    },
    onSuccess: (record) => {
      setDecisions((previous) => ({ ...previous, [record.assurance_id]: record }));
      // The gate record, the plan and the timeline all move once a decision is persisted.
      if (record.persisted) {
        void queryClient.invalidateQueries({ queryKey: ['assurance', incidentId] });
        void queryClient.invalidateQueries({ queryKey: ['incident', incidentId] });
        void queryClient.invalidateQueries({ queryKey: ['timeline', incidentId] });
      }
    },
  });

  /**
   * Advance the workflow. This is the real `POST /incidents/{id}/run`.
   *
   * Two behaviours worth knowing at the call site: stopping at `awaiting_approval` is a SUCCESS
   * response with `is_terminal: false` and a `note`, not an error; and an `Idempotency-Key`
   * makes a double click replay the recorded result instead of taking another step.
   */
  const runMutation = useMutation({
    mutationFn: () => api.runIncident(incidentId, `run:${incidentId}:${runToken}`),
    onSuccess: (result) => {
      setLastRun(result);
      // A fresh key so the next deliberate click is a new run rather than a replay.
      setRunToken(crypto.randomUUID());
      void queryClient.invalidateQueries({ queryKey: ['incident', incidentId] });
      void queryClient.invalidateQueries({ queryKey: ['assurance', incidentId] });
      void queryClient.invalidateQueries({ queryKey: ['timeline', incidentId] });
    },
  });

  const selectedTask = useMemo(
    () => incident?.plan?.tasks.find((task) => task.id === selectedTaskId),
    [incident, selectedTaskId],
  );

  /** The action for the selected task, which is what explains a refusal the gate did not cause. */
  const selectedAction = useMemo(
    () => incident?.actions.find((action) => action.plan_task_id === selectedTaskId),
    [incident, selectedTaskId],
  );

  const selectedEvaluation = useMemo(() => {
    if (!selectedTask || selectedTask.assurance_id === null) return undefined;
    return assuranceQuery.data?.evaluations.find(
      (evaluation) => evaluation.id === selectedTask.assurance_id,
    );
  }, [assuranceQuery.data, selectedTask]);

  if (incidentQuery.isLoading) return <WorkspaceSkeleton />;

  if (incidentQuery.error) {
    const error = incidentQuery.error instanceof ApiError ? incidentQuery.error : null;
    return (
      <ErrorState
        code={error?.code ?? 'INTERNAL_ERROR'}
        message={
          error?.message ??
          `Could not load incident ${incidentId}. The Ops Board and Decision Timeline still work.`
        }
        correlationId={error?.correlationId ?? null}
        onRetry={() => void incidentQuery.refetch()}
      />
    );
  }

  if (!incident) {
    return (
      <ErrorState
        code="ENTITY_NOT_FOUND"
        message={`No incident ${incidentId}.`}
        correlationId={null}
        onRetry={() => void incidentQuery.refetch()}
      />
    );
  }

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <Header
        incident={incident}
        onRun={() => runMutation.mutate()}
        isRunning={runMutation.isPending}
        runError={
          runMutation.error instanceof ApiError
            ? `${runMutation.error.code}: ${runMutation.error.message}`
            : runMutation.error
              ? 'Could not advance the workflow.'
              : null
        }
      />

      {/*
       * The run result, verbatim. `note` carries the backend's own explanation of why a run
       * stopped — including a refusal such as SERVICE_NOT_IMPLEMENTED — and paraphrasing it
       * would be the moment this UI started editorialising over the audit trail.
       */}
      {lastRun && (
        <Panel>
          {/* Compact: this strip competes for vertical budget with the approval control. */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-3 py-2">
            <span className="text-label uppercase text-fg-secondary">Last run</span>
            <span className="flex items-center gap-1.5">
              <StateBadge status={lastRun.previous_state} />
              <span className="text-fg-muted" aria-hidden>
                {'->'}
              </span>
              <StateBadge status={lastRun.state} />
            </span>
            <Labelled label="steps">
              <MonoValue>{lastRun.steps_taken}</MonoValue>
            </Labelled>
            <Labelled label="terminal">
              <MonoValue>{String(lastRun.is_terminal)}</MonoValue>
            </Labelled>
            {lastRun.replayed && <StateBadge status="skipped" label="replayed" />}
          </div>
          {lastRun.note && <Notice tone="default">{lastRun.note}</Notice>}
        </Panel>
      )}

      {/*
       * Fixed outer tracks with a flexible plan column. The persistent 380px timeline now appears
       * at 2xl, so this grid can safely enter its three-column layout at lg without both responsive
       * systems subtracting width at the same breakpoint. `items-start` prevents shorter cards from
       * stretching to the height of the assurance column and leaving empty tails.
       */}
      <div className="grid items-start gap-3 lg:grid-cols-[300px_minmax(0,1fr)_360px] 2xl:grid-cols-[340px_minmax(0,1fr)_400px]">
        <EvidenceColumn incident={incident} />

        <PlanColumn
          incident={incident}
          timeline={timelineQuery.data}
          timelineUnavailable={timelineQuery.isError}
          selectedTaskId={selectedTaskId}
          onSelectTask={setSelectedTaskId}
        />

        <div className="flex min-w-0 flex-col gap-3">
          {assuranceQuery.error ? (
            <Panel title="Assurance">
              <ErrorState
                code={
                  assuranceQuery.error instanceof ApiError
                    ? assuranceQuery.error.code
                    : 'INTERNAL_ERROR'
                }
                message="Gate records unavailable. No action may execute without one, so nothing is assumed to have passed."
                correlationId={
                  assuranceQuery.error instanceof ApiError
                    ? assuranceQuery.error.correlationId
                    : null
                }
                onRetry={() => void assuranceQuery.refetch()}
              />
            </Panel>
          ) : (
            <AssurancePanel
              task={selectedTask}
              evaluation={selectedEvaluation}
              configVersion={assuranceQuery.data?.config_version}
              configHash={assuranceQuery.data?.config_hash}
              scopeReference={assuranceQuery.data?.incident_reference}
              incidentReference={incident.reference}
              action={selectedAction}
              canWrite={api.canWrite}
              /*
               * The API's own record wins. A session-only copy is a fixture-mode fallback, never
               * an override of what the audit trail says.
               */
              decision={
                selectedEvaluation
                  ? (persistedDecision(selectedEvaluation) ?? decisions[selectedEvaluation.id])
                  : undefined
              }
              isSubmitting={decisionMutation.isPending}
              submitError={
                decisionMutation.error instanceof ApiError
                  ? `${decisionMutation.error.code}: ${decisionMutation.error.message}`
                  : decisionMutation.error
                    ? 'Could not record the decision.'
                    : null
              }
              onSubmitDecision={(assuranceId, decision, reason) =>
                decisionMutation.mutate({ assuranceId, decision, reason })
              }
              /*
               * The SAME run mutation the header uses, including its idempotency key — not a second
               * path to execution. What is new is that the operator can ask for it from where they
               * just authorised the action, instead of having to know that the control four hundred
               * pixels up the page is the thing that makes an approval take effect.
               */
              onRun={() => runMutation.mutate()}
              isRunning={runMutation.isPending}
              runBlockedReason={runBlockedReason(incident)}
            />
          )}
        </div>
      </div>

      {/*
       * Plan-level assurance: the gate across the whole plan, not one task at a time. D1 scopes
       * this to the incident group; the group view needs FE-8, so this is the per-incident matrix
       * the group view will compose.
       */}
      {incident.plan && (
        <PlanAssuranceMatrix
          tasks={incident.plan.tasks}
          evaluations={assuranceQuery.data?.evaluations ?? []}
          configVersion={assuranceQuery.data?.config_version}
          configHash={assuranceQuery.data?.config_hash}
          selectedTaskId={selectedTaskId}
          onSelectTask={setSelectedTaskId}
        />
      )}

      <ActionsStrip incident={incident} />
    </div>
  );
}

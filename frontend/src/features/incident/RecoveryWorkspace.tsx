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
import { AssurancePanel } from '@/features/assurance/AssurancePanel';
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
      <div className="grid grid-cols-[320px_minmax(0,1fr)_380px] gap-3">
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
  // Terminal states cannot advance: the backend returns a note saying so rather than erroring,
  // but there is no reason to offer the control.
  const isTerminal =
    incident.state === 'resolved' || incident.state === 'blocked' || incident.state === 'failed';

  return (
    <Panel>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2">
        <div className="flex items-baseline gap-2">
          <MonoValue className="text-subtitle">{incident.reference}</MonoValue>
          <ProvenanceDot
            kind={incident.provenance.kind}
            provider={incident.provenance.provider}
            sourceRef={incident.provenance.source_ref}
          />
        </div>

        <span className="flex items-baseline gap-1.5">
          <MonoValue>{flight.flight_number}</MonoValue>
          {flight.route && <MonoValue muted>{flight.route}</MonoValue>}
          {flight.delay_minutes !== undefined && flight.delay_minutes > 0 && (
            <MonoValue className="text-state-warn">+{flight.delay_minutes}m</MonoValue>
          )}
        </span>

        <span className="flex items-center gap-1.5 text-caption uppercase text-fg-muted">
          trigger <MonoValue muted>{incident.trigger_type}</MonoValue>
        </span>
        <StateBadge status={incident.severity} label={`severity ${incident.severity}`} />

        <span className="flex items-center gap-1.5 text-caption uppercase text-fg-muted">
          opened <MonoValue muted>{incident.opened_at.slice(11, 19)}Z</MonoValue>
        </span>

        <span className="flex items-center gap-1.5 text-caption uppercase text-fg-muted">
          workflow {/* Both ends are recorded transitions, so this cannot mix two clocks. */}
          <WhyPopover derivation={elapsedDerivation(incident, first, latest)}>
            <MonoValue>
              {first && latest ? formatDuration(first.at, latest.at) : 'not derivable'}
            </MonoValue>
          </WhyPopover>
        </span>

        <div className="ml-auto flex items-center gap-2">
          {/*
           * The real POST /incidents/{id}/run. Disabled for two honest reasons rather than one
           * vague one: a terminal incident cannot advance, and fixture mode has no endpoint to
           * call. Both say which applies.
           */}
          <button
            type="button"
            onClick={onRun}
            disabled={isRunning || isTerminal || !api.canWrite}
            aria-disabled={isRunning || isTerminal || !api.canWrite}
            className="inline-flex items-center gap-1.5 rounded-sm border border-accent-border bg-accent-subtle px-2 py-1 text-label uppercase text-accent transition-colors duration-hover ease-out hover:bg-accent/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:border-border-subtle disabled:bg-inset disabled:text-fg-muted"
          >
            <PlayCircle size={14} strokeWidth={1.5} aria-hidden />
            {isRunning ? 'Running…' : 'Run workflow'}
          </button>
          <span className="max-w-[280px] text-caption text-fg-muted">
            {!api.canWrite
              ? 'fixtures are being served — point the UI at the live API to run'
              : isTerminal
                ? `incident is terminal in ${incident.state}`
                : 'advances the workflow one run'}
          </span>
        </div>
      </div>

      {runError && (
        <p
          role="alert"
          className="border-t border-state-crit/30 bg-state-crit-bg px-3 py-1.5 text-caption text-state-crit"
        >
          {runError}
        </p>
      )}

      <div className="border-t border-border-subtle px-3 py-2">
        <StateRail rail={incident.state_rail} current={incident.state} />
      </div>
    </Panel>
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

  /*
   * Land on the blocked task. An operator opening this screen mid-disruption wants the thing
   * waiting for them, not task 1 which already succeeded. `plan` is null until the orchestrator
   * proposes one, so there is nothing to select on a freshly detected incident.
   */
  useEffect(() => {
    if (!incident?.plan || selectedTaskId !== null) return;
    const tasks = incident.plan.tasks;
    const blocked = tasks.find((task) => task.state === 'needs_human');
    setSelectedTaskId(blocked?.id ?? tasks[0]?.id ?? null);
  }, [incident, selectedTaskId]);

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
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-1.5">
            <span className="text-label uppercase text-fg-muted">Last run</span>
            <span className="flex items-center gap-1.5">
              <StateBadge status={lastRun.previous_state} />
              <span className="text-fg-muted">{'->'}</span>
              <StateBadge status={lastRun.state} />
            </span>
            <span className="text-caption uppercase text-fg-muted">
              steps <MonoValue>{lastRun.steps_taken}</MonoValue>
            </span>
            <span className="text-caption uppercase text-fg-muted">
              terminal <MonoValue>{String(lastRun.is_terminal)}</MonoValue>
            </span>
            {lastRun.replayed && <StateBadge status="skipped" label="replayed" />}
          </div>
          {lastRun.note && (
            <p className="border-t border-border-subtle px-3 py-1.5 text-caption text-fg-secondary">
              {lastRun.note}
            </p>
          )}
        </Panel>
      )}

      {/*
       * Fixed left and right columns with a flexible centre: the evidence list and the
       * assurance panel have known content widths, and the plan is what benefits from space.
       */}
      <div className="grid min-h-0 flex-1 grid-cols-[320px_minmax(0,1fr)_380px] gap-3">
        <EvidenceColumn incident={incident} />

        <PlanColumn
          incident={incident}
          selectedTaskId={selectedTaskId}
          onSelectTask={setSelectedTaskId}
        />

        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
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

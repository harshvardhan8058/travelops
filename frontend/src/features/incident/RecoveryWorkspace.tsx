/**
 * Recovery Workspace — `/incidents/:incidentId`. docs/27-ui-specification.md screen 2.
 *
 * Where the actual work happens, and the middle of the demo path:
 *
 *   Ops Board → Recovery Workspace → assurance decision → approval → execution → Timeline
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
import type { HumanDecision, IncidentDetail } from '@/api/types';
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
 * The most recent thing the records say happened — a state transition or an executed action.
 * Used as the far end of the elapsed measurement so the figure comes from records rather than
 * from the browser clock.
 */
function latestRecord(incident: IncidentDetail): { at: string; label: string } | null {
  const stamps: { at: string; label: string }[] = [
    ...incident.state_rail
      .filter((step) => step.reached_at !== null)
      .map((step) => ({ at: step.reached_at as string, label: `state ${step.state}` })),
    ...incident.actions
      .filter((action) => action.executed_at !== null)
      .map((action) => ({ at: action.executed_at as string, label: `action ${action.id}` })),
  ].sort((a, b) => Date.parse(a.at) - Date.parse(b.at));

  return stamps[stamps.length - 1] ?? null;
}

function Header({ incident }: { incident: IncidentDetail }) {
  const { flight } = incident;
  const latest = latestRecord(incident);
  const isResumable = incident.state !== 'resolved' && incident.state !== 'failed';

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
          elapsed {/* Records minus records. No wall clock: see elapsedDerivation for why. */}
          <WhyPopover derivation={elapsedDerivation(incident, latest)}>
            <MonoValue>
              {latest ? formatDuration(incident.opened_at, latest.at) : 'not derivable'}
            </MonoValue>
          </WhyPopover>
        </span>

        <div className="ml-auto flex items-center gap-2">
          {/*
           * Rendered, disabled, and honest about why. A button that silently does nothing is
           * worse in a demo than one that names the endpoint it is waiting for.
           */}
          <button
            type="button"
            disabled
            aria-disabled
            className="inline-flex items-center gap-1.5 rounded-sm border border-border-subtle px-2 py-1 text-label uppercase text-fg-muted opacity-60"
          >
            <PlayCircle size={14} strokeWidth={1.5} aria-hidden />
            Continue workflow
          </button>
          <span className="text-caption text-fg-muted">
            {isResumable
              ? 'awaiting POST /incidents/{id}/continue from Stream A'
              : 'incident is not resumable'}
          </span>
        </div>
      </div>

      <div className="border-t border-border-subtle px-3 py-2">
        <StateRail rail={incident.state_rail} current={incident.state} />
      </div>
    </Panel>
  );
}

export function RecoveryWorkspace() {
  const { incidentId = '' } = useParams();
  const queryClient = useQueryClient();
  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null);
  const [decisions, setDecisions] = useState<Record<number, HumanDecision>>({});

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
   * waiting for them, not task 1 which already succeeded.
   */
  useEffect(() => {
    if (!incident || selectedTaskId !== null) return;
    const blocked = incident.plan.tasks.find((task) => task.state === 'needs_human');
    setSelectedTaskId(blocked?.id ?? incident.plan.tasks[0]?.id ?? null);
  }, [incident, selectedTaskId]);

  const decisionMutation = useMutation({
    mutationFn: async (input: {
      assuranceId: number;
      decision: 'approved' | 'rejected';
      reason: string;
    }): Promise<HumanDecision> => {
      const record: Omit<HumanDecision, 'persisted'> = {
        assurance_id: input.assuranceId,
        decision: input.decision,
        // The backend sets the real pseudonymous operator ID from the session. This is the
        // client's placeholder and is labelled as local wherever it appears.
        actor_id: 'demo-operator',
        reason: input.reason,
        decided_at: new Date().toISOString(),
      };

      /*
       * POST /assurance/{id}/decision is listed in docs/26 but is not in docs/openapi.json
       * and no fixture serves it. Rather than invent a response shape, the decision is kept
       * in session state and rendered as explicitly unpersisted. When the endpoint exists,
       * `usingFixtures` is false and this posts for real with no other change.
       */
      if (api.usingFixtures) return { ...record, persisted: false };

      await api.submitDecision(input.assuranceId, input.decision, input.reason);
      return { ...record, persisted: true };
    },
    onSuccess: (record) => {
      setDecisions((previous) => ({ ...previous, [record.assurance_id]: record }));
      // The gate record and the timeline both change once a decision is persisted.
      if (record.persisted) {
        void queryClient.invalidateQueries({ queryKey: ['assurance', incidentId] });
        void queryClient.invalidateQueries({ queryKey: ['incident', incidentId] });
        void queryClient.invalidateQueries({ queryKey: ['timeline', incidentId] });
      }
    },
  });

  const selectedTask = useMemo(
    () => incident?.plan.tasks.find((task) => task.id === selectedTaskId),
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
      <Header incident={incident} />

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
              decision={selectedEvaluation ? decisions[selectedEvaluation.id] : undefined}
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

      <ActionsStrip incident={incident} />
    </div>
  );
}

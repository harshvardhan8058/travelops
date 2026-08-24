/**
 * Agent Operations Console — `/agent/:incidentId`.
 *
 * The Recovery Workspace answers "what is happening to this flight". This answers a different
 * question, and it is the one that matters once the system acts on its own: **what is the agent
 * doing, what permitted it, and what came back.**
 *
 * It reads five existing contracts and adds no endpoint:
 *
 *   - `GET /incidents/{ref}` — the declared plan, the actions that ran, the evidence
 *   - `GET /incidents/{ref}/assurance` — the gate decision and six checks per task
 *   - `GET /incidents/{ref}/plans` — the selected candidate, for `target_refs` and `plan_hash`
 *   - `GET /incidents/{ref}/replay` — every recorded act, attributed to an actor kind
 *   - `GET /system/mode` — which reasoning mode is in force (shared cache with the shell)
 *
 * Three rules it holds to, because an agentic console is exactly where they get broken:
 *
 *   1. **No narration.** Every sentence on screen was written by a service, a gate or a person and
 *      is rendered verbatim. Nothing is generated here, and no private reasoning trace exists to
 *      leak — `plan.raw_response` is null and is on no response model.
 *   2. **Absence is stated, not skipped.** A goal field, tool arguments, call durations, retry
 *      counts and any confidence number do not exist. Each is named where an operator would look
 *      for it, because a missing column reads as a healthy one.
 *   3. **Identity is not status.** `actor_kind` gets neutral chips, operational state keeps the
 *      state palette. A console where amber might mean "a person is needed" or "a person did this"
 *      is unreadable at a glance.
 *
 * Owner: Stream D.
 */

import { useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { api, ApiError } from '@/api/client';
import { ErrorState, LoadingState, Panel } from '@/components/ui/primitives';
import { ActivityPanel } from './ActivityPanel';
import { AutonomyPanel, ToolsPanel } from './AutonomyStrip';
import { ObjectivePanel } from './ObjectivePanel';
import { StepDetail } from './StepDetail';
import { StepLedger } from './StepLedger';
import { UncertaintyPanel } from './UncertaintyPanel';
import { buildStepLedger, resolveLedger } from './steps';

export function AgentConsole() {
  const { incidentId = '' } = useParams<{ incidentId: string }>();
  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null);

  const incidentQuery = useQuery({
    queryKey: ['incident', incidentId],
    queryFn: () => api.incident(incidentId),
    enabled: incidentId.length > 0,
  });

  const assuranceQuery = useQuery({
    queryKey: ['assurance', incidentId],
    queryFn: () => api.assurance(incidentId),
    enabled: incidentId.length > 0,
  });

  /**
   * Candidates are wanted only for `target_refs` and `plan_hash`, neither of which is on the
   * incident contract. A 404 here is the documented pre-planning state, so the console carries on
   * without the refs rather than failing the screen over an enrichment.
   */
  const plansQuery = useQuery({
    queryKey: ['plans', incidentId],
    queryFn: () => api.plans(incidentId),
    enabled: incidentId.length > 0,
    retry: false,
  });

  const replayQuery = useQuery({
    queryKey: ['replay', 'incident', incidentId],
    queryFn: () => api.incidentReplay(incidentId),
    enabled: incidentId.length > 0,
  });

  /** Shares the shell's cache entry, so this costs no extra request. */
  const { data: mode } = useQuery({ queryKey: ['system-mode'], queryFn: api.systemMode });

  /**
   * Which plan actually ran. Asking for candidates proposes new plan rows, and the incident contract
   * then advertises the newest of them, so the plan on screen is chosen by which one carries the
   * recorded evaluations and actions rather than by which one the contract happens to name.
   */
  const ledger = useMemo(() => {
    if (!incidentQuery.data) return null;
    return resolveLedger(
      incidentQuery.data,
      assuranceQuery.data?.evaluations ?? [],
      plansQuery.data?.plans ?? [],
    );
  }, [incidentQuery.data, assuranceQuery.data, plansQuery.data]);

  const steps = useMemo(() => {
    if (!incidentQuery.data || !ledger) return [];
    return buildStepLedger(
      ledger.tasks,
      assuranceQuery.data?.evaluations ?? [],
      incidentQuery.data.actions,
    );
  }, [incidentQuery.data, assuranceQuery.data, ledger]);

  const selectedStep = useMemo(
    () => steps.find((step) => step.taskId === selectedTaskId) ?? steps[0] ?? null,
    [steps, selectedTaskId],
  );

  if (incidentQuery.isLoading || assuranceQuery.isLoading) {
    return (
      <Panel title="Agent operations">
        <div className="h-[560px]">
          <LoadingState label="Loading the agent ledger" />
        </div>
      </Panel>
    );
  }

  if (incidentQuery.error || !incidentQuery.data) {
    const error = incidentQuery.error instanceof ApiError ? incidentQuery.error : null;
    return (
      <ErrorState
        code={error?.code ?? 'INTERNAL_ERROR'}
        message={error?.message ?? `Could not load ${incidentId}.`}
        correlationId={error?.correlationId ?? null}
        onRetry={() => void incidentQuery.refetch()}
      />
    );
  }

  const incident = incidentQuery.data;
  /*
   * `config_version` and `config_hash` are literally 'unavailable' until something has been
   * evaluated. That string is rendered as returned rather than blanked: which semantics applied is
   * exactly what a replay has to be able to prove.
   */
  const configVersion = assuranceQuery.data?.config_version ?? 'unavailable';
  const configHash = assuranceQuery.data?.config_hash ?? 'unavailable';

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <ObjectivePanel incident={incident} mode={mode} ledger={ledger} />

      <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_420px]">
        <AutonomyPanel steps={steps} configVersion={configVersion} configHash={configHash} />
        <ToolsPanel steps={steps} />
      </div>

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[minmax(0,1fr)_420px]">
        <div className="flex min-h-0 flex-col gap-3">
          <StepLedger
            steps={steps}
            incidentReference={incident.reference}
            configVersion={configVersion}
            configHash={configHash}
            selectedTaskId={selectedStep?.taskId ?? null}
            onSelect={setSelectedTaskId}
          />
          <div className="max-h-[340px] shrink-0 overflow-hidden">
            <UncertaintyPanel
              incident={incident}
              plan={incident.plan}
              configVersion={configVersion}
              configHash={configHash}
              planHash={ledger?.planHash ?? null}
            />
          </div>
        </div>

        <div className="flex min-h-0 flex-col gap-3">
          <StepDetail step={selectedStep} incidentReference={incident.reference} />
          <div className="max-h-[340px] shrink-0 overflow-hidden">
            {replayQuery.data ? (
              <ActivityPanel replay={replayQuery.data} scope={incident.reference} />
            ) : (
              <Panel title="Agent activity">
                <div className="px-3 py-6">
                  <LoadingState label="Loading recorded activity" />
                </div>
              </Panel>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * The Scenario Center — a demo you can run from a browser.
 *
 * Everything this screen does already existed behind `python -m app.cli`. What was missing was a way
 * to see the dataset and start a simulation without a terminal, which is the difference between a
 * product and a set of developer tools.
 *
 * Four things it deliberately does NOT do.
 *
 * **It does not invent a disruption.** A simulation here is a reproducible *selection* over recorded
 * rows, resolved by the backend, and its members carry each flight's RECORDED delay. This screen
 * POSTs those values unmodified. `POST /scenarios` refuses a declared delay that disagrees with the
 * recorded one, so if this screen ever started composing operational facts the scenario contract
 * would reject them rather than a fabricated disruption reaching an operator.
 *
 * **It does not run a second lifecycle.** Starting a simulation calls `runScenarioLifecycle` — the
 * same create-then-start-then-confirm path the Scenario Builder uses, extracted rather than copied.
 * Everything downstream (evidence, planner, assurance, approval, execution, replay) is the one code
 * path it has always been.
 *
 * **It does not offer a mode switch.** The runtime posture panel is READ-ONLY. `LLM_MODE`,
 * `FLIGHT_STATUS_MODE`, `WEATHER_MODE` and `NOTIFICATION_MODE` are server process configuration; a
 * browser cannot change them. A toggle here would be a control that appears to do something and
 * does not, which is worse than no control. It reports what `/system/mode` publishes and says where
 * the values come from.
 *
 * **It does not claim a reset did more than it did.** Reset restores the dataset and stops — it does
 * not also open a cascade, and the panel says so before you press it. The result report is read back
 * from the counts the server returned, not predicted.
 *
 * Owner: Stream D.
 */

import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Play, RotateCcw } from 'lucide-react';

import { api, ApiError } from '@/api/client';
import { resolveUnavailable, retryUnlessUnavailable } from '@/api/unavailable';
import { deriveEvidencePosture, deriveModeChips } from '@/api/runtimeModes';
import type { DemoResetResponse, ProvenanceKind, SimulationDefinition } from '@/api/types';
import { countDerivation } from '@/components/ui/derivation';
import {
  Button,
  DefinitionList,
  DefinitionRow,
  FIELD_SHELL,
  Labelled,
  Notice,
  NotYetAvailable,
  PageHeader,
  PanelBody,
  PanelSection,
  SectionHeading,
  StatStrip,
  TableFrame,
  TableHead,
  type TableColumn,
} from '@/components/ui/composition';
import {
  EmptyState,
  ErrorState,
  LoadingState,
  MonoValue,
  Panel,
  ProvenanceDot,
  StateBadge,
} from '@/components/ui/primitives';
import { MetricTile } from '@/components/ui/Metric';
import {
  datasetHeadlines,
  RESET_CONFIRMATION,
  resetBlockedReason,
  resetConfirmationMatches,
  simulationReadiness,
  simulationToScenarioRequest,
  startBlockedReason,
} from './demoControl';
import { runScenarioLifecycle } from '../scenario-builder/scenarioLifecycle';

const MEMBER_COLUMNS: TableColumn[] = [
  { key: 'flight', label: 'Flight' },
  { key: 'role', label: 'Role' },
  { key: 'route', label: 'Route' },
  { key: 'delay', label: 'Recorded delay', align: 'right', hint: 'Minutes, as stored' },
];

export function ScenarioCenter() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const dataset = useQuery({
    queryKey: ['demo-dataset'],
    queryFn: api.demoDataset,
    retry: retryUnlessUnavailable,
  });
  const simulations = useQuery({
    queryKey: ['demo-simulations'],
    queryFn: api.demoSimulations,
    retry: retryUnlessUnavailable,
  });
  const mode = useQuery({
    queryKey: ['system-mode'],
    queryFn: api.systemMode,
    retry: retryUnlessUnavailable,
  });

  /**
   * One key per start attempt. Regenerated only after a start finishes, so a double-clicked button
   * replays the recorded result instead of opening a second cascade.
   */
  const operationKey = useRef(crypto.randomUUID());
  const [startingId, setStartingId] = useState<string | null>(null);
  const [startFailure, setStartFailure] = useState<Error | null>(null);
  const [typed, setTyped] = useState('');
  const [resetReport, setResetReport] = useState<DemoResetResponse | null>(null);
  const [resetFailure, setResetFailure] = useState<Error | null>(null);

  async function refreshDemoState() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['demo-dataset'] }),
      queryClient.invalidateQueries({ queryKey: ['demo-simulations'] }),
      queryClient.invalidateQueries({ queryKey: ['incident-groups'] }),
      queryClient.invalidateQueries({ queryKey: ['incident-group', 'current'] }),
      queryClient.invalidateQueries({ queryKey: ['current-group'] }),
    ]);
  }

  const start = useMutation({
    mutationFn: async (simulation: SimulationDefinition) => {
      // The instant is supplied here because a clock is not the catalogue's to read. Everything
      // else in the payload is copied from what the server published.
      const payload = simulationToScenarioRequest(simulation, {
        effectiveAt: new Date().toISOString(),
      });
      return runScenarioLifecycle(api, payload, {
        runAfterCreate: true,
        operationKey: operationKey.current,
      });
    },
    onMutate: (simulation) => {
      setStartFailure(null);
      setStartingId(simulation.id);
    },
    onSuccess: async (result) => {
      await refreshDemoState();
      // Navigate only once the lifecycle confirmed `/incident-groups/current` names what it created.
      if (result.route) navigate(result.route);
    },
    onError: (error) => {
      setStartFailure(error instanceof Error ? error : new Error('The simulation did not start.'));
    },
    onSettled: () => {
      setStartingId(null);
      operationKey.current = crypto.randomUUID();
    },
  });

  const reset = useMutation({
    mutationFn: () => api.resetDemoData(typed),
    onMutate: () => {
      setResetFailure(null);
      setResetReport(null);
    },
    onSuccess: async (report) => {
      setResetReport(report);
      setTyped('');
      await refreshDemoState();
    },
    onError: (error) => {
      setResetFailure(error instanceof Error ? error : new Error('The reset did not run.'));
    },
  });

  if (dataset.isLoading || simulations.isLoading) {
    return <LoadingState label="Reading the demo dataset" />;
  }

  const outcome = resolveUnavailable([dataset.error, simulations.error]);
  if (outcome && 'unavailable' in outcome) {
    return <NotYetAvailable title="Nothing to control yet" unavailable={outcome.unavailable} />;
  }
  if (outcome) {
    const failure = outcome.failure;
    return (
      <ErrorState
        code={failure instanceof ApiError ? failure.code : 'UNAVAILABLE'}
        message={
          api.canWrite
            ? failure instanceof Error
              ? failure.message
              : 'The demo control surface could not be read.'
            : 'The demo control surface reports the state of a real database, so it has no fixture. Point the console at the live API to use it.'
        }
        correlationId={failure instanceof ApiError ? failure.correlationId : null}
        onRetry={() => {
          void dataset.refetch();
          void simulations.refetch();
        }}
      />
    );
  }

  if (!dataset.data || !simulations.data) {
    return (
      <EmptyState
        title="No demo state was returned"
        description="The endpoints answered without a body. Retry, or check the API logs."
      />
    );
  }

  const demo = dataset.data;
  const catalogue = simulations.data;
  const chips = deriveModeChips(mode.data);
  const posture = deriveEvidencePosture(mode.data);
  const isBusy = start.isPending || reset.isPending;

  const resetReason = resetBlockedReason({
    canWrite: api.canWrite,
    resetAllowed: demo.reset_allowed,
    isBusy,
    typed,
  });

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <PageHeader
        eyebrow="Demo control"
        title="Scenario Center"
        status={
          <StateBadge
            status={demo.is_seeded ? 'up' : 'down'}
            label={demo.is_seeded ? 'Dataset seeded' : 'Dataset not seeded'}
          />
        }
        meta={
          <>
            <Labelled label="Catalogue">
              <MonoValue muted>{catalogue.catalogue_version}</MonoValue>
            </Labelled>
            <Labelled label="Basis">
              <MonoValue muted>{catalogue.basis}</MonoValue>
            </Labelled>
            <Labelled label="Environment">
              <MonoValue muted>{demo.app_env}</MonoValue>
            </Labelled>
            <Labelled label="Runnable">
              <MonoValue muted>
                {catalogue.runnable_count} of {catalogue.simulations.length}
              </MonoValue>
            </Labelled>
          </>
        }
        footer={
          <span className="text-body text-fg-secondary">
            Start a recorded disruption, inspect what is in the database, and restore it — without a
            terminal. {catalogue.note}
          </span>
        }
      />

      <div className="grid min-w-0 grid-cols-1 gap-3 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-3">
          <Panel title="Simulations">
            <PanelBody>
              <span className="text-body text-fg-secondary">
                Each entry is a reproducible selection over recorded flights. The delays below are
                the ones the dataset stores; they are sent to the scenario contract unchanged, which
                refuses any other value.
              </span>
            </PanelBody>
            {catalogue.simulations.length === 0 ? (
              <PanelBody>
                <EmptyState
                  title="The catalogue is empty"
                  description="The server published no simulation definitions."
                />
              </PanelBody>
            ) : (
              catalogue.simulations.map((simulation) => (
                <SimulationCard
                  key={simulation.id}
                  simulation={simulation}
                  blockedReason={startBlockedReason(simulation, {
                    canWrite: api.canWrite,
                    isSeeded: demo.is_seeded,
                    isBusy,
                  })}
                  isStarting={startingId === simulation.id}
                  onStart={() => start.mutate(simulation)}
                />
              ))
            )}
          </Panel>

          {startFailure && (
            <Panel title="The simulation did not start">
              <PanelBody>
                <Notice tone="crit" alert>
                  {startFailure.message}
                </Notice>
                <span className="font-mono text-mono-sm text-fg-muted">
                  {startFailure instanceof ApiError ? startFailure.code : 'LIFECYCLE_FAILED'}
                  {startFailure instanceof ApiError && startFailure.correlationId
                    ? ` · ${startFailure.correlationId}`
                    : ''}
                </span>
              </PanelBody>
            </Panel>
          )}
        </div>

        <div className="flex min-w-0 flex-col gap-3">
          <Panel title="Recorded dataset">
            <PanelBody>
              <StatStrip>
                {datasetHeadlines(demo).map((headline) => {
                  // Reference rows are the committed fixed-seed dataset; groups and incidents are
                  // what the orchestrator itself recorded. Different origins, different provenance.
                  const provenance = {
                    kind: (headline.origin === 'reference' ? 'fixture' : 'real') as ProvenanceKind,
                    provider: 'demo.dataset',
                    source_ref: 'GET /api/v1/demo/dataset',
                  };
                  return (
                    <MetricTile
                      key={headline.label}
                      label={headline.label}
                      value={headline.value}
                      provenance={provenance}
                      // The explanation comes from the shared adapter, never from prose written here.
                      derivation={countDerivation(headline.label, headline.value, {
                        endpoint: 'GET /api/v1/demo/dataset',
                        field: headline.field,
                        provenance,
                        note:
                          headline.origin === 'reference'
                            ? 'A row count read back from the database. Reference rows come from the fixed-seed dataset, and a reset restores them.'
                            : "A row count read back from the database. This is the workflow's own output, and a reset removes it.",
                      })}
                    />
                  );
                })}
              </StatStrip>
            </PanelBody>
            <PanelSection title="Current cascade">
              <DefinitionList width="lg">
                <DefinitionRow label="Group" width="lg">
                  {demo.current_group_reference ? (
                    <MonoValue>{demo.current_group_reference}</MonoValue>
                  ) : (
                    <span className="text-body text-fg-muted">
                      Nothing in progress. Start a simulation to open one.
                    </span>
                  )}
                </DefinitionRow>
              </DefinitionList>
            </PanelSection>
            <PanelSection title="Tables">
              <TableFrame caption="Row counts for the seeded reference tables">
                <TableHead
                  columns={[
                    { key: 'table', label: 'Table' },
                    { key: 'rows', label: 'Rows', align: 'right' },
                  ]}
                />
                <tbody>
                  {demo.tables.map((row) => (
                    <tr key={row.table} className="border-b border-border-subtle last:border-b-0">
                      <td className="px-3 py-1">
                        <MonoValue muted>{row.table}</MonoValue>
                      </td>
                      <td className="px-3 py-1 text-right">
                        <MonoValue>{row.rows}</MonoValue>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </TableFrame>
            </PanelSection>
            <PanelBody>
              <span className="text-body text-fg-secondary">{demo.note}</span>
            </PanelBody>
          </Panel>

          <RuntimePosturePanel chips={chips} posture={posture} />

          <Panel title="Reset demo data">
            <PanelBody>
              <span className="text-body text-fg-secondary">
                Removes the workflow rows the orchestrator recorded and re-seeds the reference
                dataset. It does <strong className="font-semibold text-fg">not</strong> open a
                cascade afterwards, so nothing will be in progress until you start a simulation.
              </span>
              <Notice tone="warn" divider="top">
                This is destructive and cannot be undone. Incidents, plans, assurance evaluations
                and decisions in the current dataset are deleted.
              </Notice>
              <label className="flex flex-col gap-1" htmlFor="reset-confirm">
                <span className="text-label uppercase text-fg-secondary">
                  Type the confirmation phrase
                </span>
                {/*
                  The phrase is rendered so the control states what it wants. `uppercase` is on the
                  label above, never on this value: it is the string the server compares.
                */}
                <MonoValue muted>{RESET_CONFIRMATION}</MonoValue>
                <input
                  id="reset-confirm"
                  className={FIELD_SHELL}
                  value={typed}
                  onChange={(event) => setTyped(event.target.value)}
                  placeholder={RESET_CONFIRMATION}
                  autoComplete="off"
                  spellCheck={false}
                  disabled={!api.canWrite || !demo.reset_allowed || isBusy}
                  aria-describedby="reset-confirm-hint"
                />
                <span id="reset-confirm-hint" className="text-caption text-fg-muted">
                  {resetConfirmationMatches(typed)
                    ? 'The phrase matches. The reset is enabled.'
                    : 'The server re-checks this phrase, so a mis-click cannot satisfy it.'}
                </span>
              </label>
              <Button
                variant="danger"
                icon={RotateCcw}
                disabled={resetReason !== null}
                disabledReason={resetReason ?? undefined}
                onClick={() => reset.mutate()}
              >
                {reset.isPending ? 'Resetting' : 'Reset demo data'}
              </Button>
            </PanelBody>
            {resetFailure && (
              <PanelBody>
                <Notice tone="crit" alert>
                  {resetFailure.message}
                </Notice>
                <span className="font-mono text-mono-sm text-fg-muted">
                  {resetFailure instanceof ApiError ? resetFailure.code : 'RESET_FAILED'}
                  {resetFailure instanceof ApiError && resetFailure.correlationId
                    ? ` · ${resetFailure.correlationId}`
                    : ''}
                </span>
              </PanelBody>
            )}
            {resetReport && <ResetReport report={resetReport} />}
          </Panel>
        </div>
      </div>
    </div>
  );
}

function SimulationCard({
  simulation,
  blockedReason,
  isStarting,
  onStart,
}: {
  simulation: SimulationDefinition;
  blockedReason: string | null;
  isStarting: boolean;
  onStart: () => void;
}) {
  const readiness = simulationReadiness(simulation);

  return (
    <PanelSection
      title={simulation.name}
      tone={readiness.canStart ? 'default' : 'muted'}
      actions={
        <Button
          size="sm"
          variant="primary"
          icon={Play}
          /*
           * Named after the simulation it starts. The visible label stays short, but three buttons
           * reading only "Run simulation" give a screen-reader user no way to tell which disruption
           * they are about to open — and this control writes real incidents to the database.
           */
          aria-label={`Run simulation: ${simulation.name}`}
          disabled={blockedReason !== null || isStarting}
          disabledReason={blockedReason ?? undefined}
          onClick={onStart}
        >
          {isStarting ? 'Starting' : 'Run simulation'}
        </Button>
      }
    >
      <span className="text-body text-fg-secondary">{simulation.summary}</span>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {/* Every one of these is a contract value, so each sits beside an uppercased LABEL and
            never inside one. */}
        <Labelled label="Id">
          <MonoValue muted>{simulation.id}</MonoValue>
        </Labelled>
        <Labelled label="Root cause">
          <MonoValue muted>{simulation.root_cause}</MonoValue>
        </Labelled>
        <Labelled label="Airport">
          <MonoValue muted>{simulation.airport_icao}</MonoValue>
        </Labelled>
        <Labelled label="Severity">
          <MonoValue muted>{simulation.severity}</MonoValue>
        </Labelled>
        <Labelled label="Passengers">
          {simulation.passengers_affected === null ? (
            <span className="text-body text-fg-muted">not recorded</span>
          ) : (
            <MonoValue>{simulation.passengers_affected}</MonoValue>
          )}
        </Labelled>
        <span className="flex items-center gap-1.5">
          <ProvenanceDot
            kind={simulation.provenance.kind}
            provider={simulation.provenance.provider}
            sourceRef={simulation.provenance.source_ref}
          />
          <span className="text-caption text-fg-muted">
            {simulation.provenance.kind} · {simulation.provenance.provider}
          </span>
        </span>
      </div>

      {!readiness.canStart && readiness.reason && (
        <Notice tone="warn">Cannot run against the current dataset: {readiness.reason}</Notice>
      )}

      {simulation.members.length > 0 && (
        <TableFrame caption={`Flights ${simulation.name} would declare`}>
          <TableHead columns={MEMBER_COLUMNS} />
          <tbody>
            {simulation.members.map((member) => (
              <tr key={member.flight_id} className="border-b border-border-subtle last:border-b-0">
                <td className="px-3 py-1">
                  <MonoValue>{member.flight_number}</MonoValue>
                </td>
                <td className="px-3 py-1">
                  <MonoValue muted>{member.role}</MonoValue>
                </td>
                <td className="px-3 py-1">
                  <MonoValue muted>
                    {member.origin_icao} → {member.destination_icao}
                  </MonoValue>
                </td>
                <td className="px-3 py-1 text-right">
                  <MonoValue>{member.delay_minutes}</MonoValue>
                </td>
              </tr>
            ))}
          </tbody>
        </TableFrame>
      )}
    </PanelSection>
  );
}

function RuntimePosturePanel({
  chips,
  posture,
}: {
  chips: ReturnType<typeof deriveModeChips>;
  posture: ReturnType<typeof deriveEvidencePosture>;
}) {
  return (
    <Panel title="Runtime posture">
      <PanelBody>
        <SectionHeading
          tone={
            posture.headline === 'LIVE' ? 'ok' : posture.headline === 'UNKNOWN' ? 'muted' : 'info'
          }
          hint={posture.summary}
        >
          Evidence sources
        </SectionHeading>
        <DefinitionList width="sm">
          {chips.map((chip) => (
            <DefinitionRow key={chip.label} label={chip.label} width="sm">
              <span className="flex min-w-0 flex-col gap-0.5">
                <MonoValue>{chip.value ?? 'unknown'}</MonoValue>
                <span className="text-caption text-fg-muted">{chip.detail}</span>
                {chip.degradation && (
                  <span className="text-caption text-state-warn">{chip.degradation}</span>
                )}
              </span>
            </DefinitionRow>
          ))}
        </DefinitionList>
        <Notice tone="muted" divider="top">
          Read-only. These are server process settings published by GET /system/mode; a browser
          cannot change them. Restart the API with different environment variables to switch a
          source.
        </Notice>
      </PanelBody>
    </Panel>
  );
}

function ResetReport({ report }: { report: DemoResetResponse }) {
  const removed = Object.entries(report.workflow_removed).filter(([, rows]) => rows > 0);

  return (
    <PanelSection title="What the reset did" tone="ok">
      <DefinitionList width="lg">
        <DefinitionRow label="Digest" width="lg">
          <MonoValue muted>{report.dataset_digest}</MonoValue>
        </DefinitionRow>
        <DefinitionRow label="Seeded group" width="lg">
          {report.seeded_group_reference ? (
            <MonoValue>{report.seeded_group_reference}</MonoValue>
          ) : (
            <span className="text-body text-fg-muted">none declared</span>
          )}
        </DefinitionRow>
        <DefinitionRow label="Performed by" width="lg">
          <MonoValue muted>{report.performed_by}</MonoValue>
        </DefinitionRow>
      </DefinitionList>

      <SectionHeading count={removed.length} tone={removed.length > 0 ? 'warn' : 'muted'}>
        Workflow rows removed
      </SectionHeading>
      {removed.length === 0 ? (
        <span className="text-body text-fg-muted">
          Nothing to remove: no workflow rows existed.
        </span>
      ) : (
        <TableFrame caption="Workflow rows removed by the reset">
          <TableHead
            columns={[
              { key: 'table', label: 'Table' },
              { key: 'rows', label: 'Removed', align: 'right' },
            ]}
          />
          <tbody>
            {removed.map(([table, rows]) => (
              <tr key={table} className="border-b border-border-subtle last:border-b-0">
                <td className="px-3 py-1">
                  <MonoValue muted>{table}</MonoValue>
                </td>
                <td className="px-3 py-1 text-right">
                  <MonoValue>{rows}</MonoValue>
                </td>
              </tr>
            ))}
          </tbody>
        </TableFrame>
      )}

      <span className="text-body text-fg-secondary">{report.note}</span>
    </PanelSection>
  );
}

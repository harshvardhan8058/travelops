/**
 * Scenario Builder — `/scenarios/new`. Phase 5.
 *
 * The question this screen answers: **what would this disruption do?** Until now a scenario could
 * only be introduced by running `python -m app.cli inject`, which means the one thing a reviewer most
 * wants to try — a disruption of their own — was the one thing the console could not do.
 *
 * Three steps, because there are three genuinely different decisions: which shape of disruption,
 * what its details are, and whether the result is what was meant. The third is not decoration. A
 * scenario opens one incident per declared flight and each of those carries its own approval gate, so
 * a mis-typed flight list is an operator authorising work on the wrong aircraft.
 *
 * The screen resolves entered designators against the published flight-board rows, creates a real Scenario
 * through the typed API client, and optionally starts it. Starting opens one canonical incident per
 * declared flight; creation alone persists membership without pretending work has begun.
 *
 * Impact figures remain engine-owned. The client sends recorded flight ids and delays, preserves
 * idempotency keys across retries, keeps a successful `SCN-*` when start fails, and navigates only
 * after the API confirms start.
 *
 * Owner: Stream D.
 */

import { useMemo, useState } from 'react';
import { AlertTriangle, Check, ChevronLeft, ChevronRight, Play, Plus } from 'lucide-react';
import { clsx } from 'clsx';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';

import { api, ApiError } from '@/api/client';
import type {
  ScenarioCreateRequest,
  ScenarioCreateResponse,
  ScenarioStartResponse,
} from '@/api/types';

import { MonoValue, Panel, ProvenanceDot, StateBadge } from '@/components/ui/primitives';
import { StepRail } from '@/components/ui/primitives';
import { FilterChips } from '@/components/ui/Metric';
import {
  DISRUPTION_TYPES,
  SCENARIO_SEVERITIES,
  SCENARIO_TEMPLATES,
  findTemplate,
  scenarioApi,
  type DisruptionType,
  type ScenarioSeverity,
  type ScenarioTemplate,
} from './scenarioContracts';
import {
  MAX_NOTES_LENGTH,
  DEMO_SCENARIO_ACTOR_ID,
  applyTemplate,
  buildCreateRequest,
  buildPreview,
  canOpenStep,
  createScenarioIdempotencyKeys,
  emptyDraft,
  issuesForField,
  parseFlightList,
  setFlightNumbers,
  startedMemberIncidentCount,
  stepStates,
  submitScenario,
  validateDraft,
  type ScenarioStepId,
  type ValidationIssue,
} from './scenarioDraft';

/** Shared input shell. One recipe so two fields cannot disagree about what a field looks like. */
const INPUT_CLASS = clsx(
  'w-full rounded-sm border border-border-strong bg-inset px-2 py-1 text-body text-fg',
  'placeholder:text-fg-muted',
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
);

const PRIMARY_BUTTON = clsx(
  'flex items-center gap-1.5 rounded-sm border px-2 py-1 text-caption uppercase',
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
  'border-accent-border bg-accent-subtle text-accent',
  'disabled:cursor-not-allowed disabled:border-border-subtle disabled:bg-transparent disabled:text-fg-muted',
);

const SECONDARY_BUTTON = clsx(
  'flex items-center gap-1.5 rounded-sm border px-2 py-1 text-caption uppercase',
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
  'border-border-strong text-fg-secondary hover:border-accent-border hover:text-accent',
  'disabled:cursor-not-allowed disabled:border-border-subtle disabled:text-fg-muted',
);

/** A labelled field with its own validation messages beside it, never in a distant summary. */
function Field({
  label,
  hint,
  issues,
  children,
}: {
  label: string;
  hint?: string;
  issues: ValidationIssue[];
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-caption uppercase text-fg-muted">{label}</span>
      {children}
      {hint && <span className="text-caption text-fg-muted">{hint}</span>}
      {issues.map((issue) => (
        <span
          key={issue.code}
          className={clsx(
            'flex items-start gap-1.5 text-caption',
            issue.severity === 'error' ? 'text-state-crit' : 'text-state-warn',
          )}
        >
          <AlertTriangle size={11} strokeWidth={1.5} className="mt-0.5 shrink-0" aria-hidden />
          {issue.message}
        </span>
      ))}
    </label>
  );
}

function TemplateCard({
  template,
  selected,
  onChoose,
}: {
  template: ScenarioTemplate;
  selected: boolean;
  onChoose: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onChoose}
      className={clsx(
        'flex flex-col gap-1.5 rounded border p-3 text-left',
        'transition-colors duration-hover ease-out',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
        selected
          ? 'border-accent-border bg-accent-subtle'
          : 'border-border-subtle bg-inset hover:border-border-strong',
      )}
    >
      <span className="flex items-center gap-2">
        {selected ? (
          <Check size={12} strokeWidth={1.5} className="text-accent" aria-hidden />
        ) : (
          <Plus size={12} strokeWidth={1.5} className="text-fg-muted" aria-hidden />
        )}
        <span className={clsx('text-body', selected ? 'text-accent' : 'text-fg')}>
          {template.name}
        </span>
      </span>
      <span className="text-caption text-fg-secondary">{template.summary}</span>
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <StateBadge status={template.severity} label={`severity ${template.severity}`} />
        <Labelled label="airport">
          <MonoValue muted>{template.airportIcao}</MonoValue>
        </Labelled>
        {/* The template's own declaration, not a measurement of anything. */}
        <Labelled label="declares">
          <MonoValue muted>{template.declaresFlights}</MonoValue>
          <span className="text-caption uppercase text-fg-muted">flights</span>
        </Labelled>
        {template.seedScenarioId && (
          <span className="text-caption uppercase text-state-ok">shipped dataset</span>
        )}
      </span>
    </button>
  );
}

/**
 * A label beside a value, with `uppercase` on the LABEL only.
 *
 * Written first as one uppercased span wrapping both, which case-transformed the values inside it: the
 * request id `scn-1a2b3c4d` rendered as `SCN-1A2B3C4D` and the endpoint path as `/API/V1/SCENARIOS`.
 * An operator copying either one would copy something the console never produced. Same defect class
 * as rendering "MoCA" as "MOCA", and the reason the policy screen has a component for this too.
 */
function Labelled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-caption uppercase text-fg-muted">{label}</span>
      {children}
    </span>
  );
}

/** Useful, status-specific feedback without replacing the server's stable code or correlation id. */
function submissionMessage(stage: 'create' | 'start', error: unknown): string {
  if (!(error instanceof ApiError)) {
    return `The scenario could not be ${stage === 'create' ? 'created' : 'started'}. Retry when the API is available.`;
  }
  if (error.status === 404) {
    return stage === 'create'
      ? 'A selected flight no longer exists. Refresh the flight dataset and review the draft.'
      : 'The created scenario could not be found. Keep its reference when contacting support.';
  }
  if (error.status === 409) {
    return 'The scenario conflicts with active work or this request was changed after its idempotency key was used.';
  }
  if (error.status === 422) {
    return 'The API rejected the scenario data. Review the selected flights, airport, delay records, and start time.';
  }
  if (error.status >= 500) {
    return 'The Scenario API failed. No success is assumed; retry with the same idempotency key.';
  }
  return error.message;
}

function ResultPanel({
  created,
  started,
  failure,
  pending,
  onRetryStart,
}: {
  created: ScenarioCreateResponse | null;
  started: ScenarioStartResponse | null;
  failure: { stage: 'create' | 'start'; error: unknown } | null;
  pending: boolean;
  onRetryStart: () => void;
}) {
  const apiError = failure?.error instanceof ApiError ? failure.error : null;
  return (
    <Panel title="Scenario lifecycle">
      {pending && (
        <p className="px-3 py-2 text-caption text-fg-secondary">Submitting to the Scenario API…</p>
      )}

      {created && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2">
          <StateBadge status={started ? started.state : created.state} />
          <Labelled label="scenario">
            <MonoValue>{created.scenario_reference}</MonoValue>
          </Labelled>
          <Labelled label="demo actor">
            <MonoValue muted>{created.created_by}</MonoValue>
          </Labelled>
          <ProvenanceDot
            kind={created.provenance.kind}
            provider={created.provenance.provider}
            sourceRef={created.provenance.source_ref}
          />
          {created.replayed && (
            <span className="text-caption uppercase text-fg-muted">replayed</span>
          )}
        </div>
      )}

      {created && !started && !failure && !pending && (
        <p className="border-t border-border-subtle px-3 py-1.5 text-caption text-fg-secondary">
          Created and persisted. It has not been started, so no incidents were opened.
        </p>
      )}

      {started && (
        <div className="flex flex-wrap items-center gap-3 border-t border-state-ok/30 bg-state-ok-bg px-3 py-2">
          <span className="text-caption text-state-ok">
            Started {startedMemberIncidentCount(started)} member incidents; this request opened{' '}
            {started.opened_incident_ids.length}.
          </span>
          <Link
            to={`/cascade/${encodeURIComponent(started.scenario_reference)}`}
            className="rounded-sm text-caption uppercase text-accent underline decoration-dotted underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            Open active cascade
          </Link>
        </div>
      )}

      {failure && (
        <div className="border-t border-state-crit/30 bg-state-crit-bg px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <StateBadge status="failed" label={`${failure.stage} failed`} />
            <MonoValue muted>{apiError?.code ?? 'INTERNAL_ERROR'}</MonoValue>
            {apiError?.correlationId && (
              <Labelled label="correlation">
                <MonoValue muted>{apiError.correlationId}</MonoValue>
              </Labelled>
            )}
          </div>
          <p className="mt-1 text-caption text-state-crit">
            {submissionMessage(failure.stage, failure.error)}
          </p>
          {failure.stage === 'start' && created && (
            <button
              type="button"
              className={clsx(SECONDARY_BUTTON, 'mt-2')}
              onClick={onRetryStart}
              disabled={pending}
            >
              <Play size={12} strokeWidth={1.5} aria-hidden />
              Retry start for {created.scenario_reference}
            </button>
          )}
        </div>
      )}
    </Panel>
  );
}

export function ScenarioBuilder() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(emptyDraft);
  const [step, setStep] = useState<ScenarioStepId>('template');
  const [flightText, setFlightText] = useState('');
  const [keys, setKeys] = useState<ReturnType<typeof createScenarioIdempotencyKeys> | null>(null);
  const [created, setCreated] = useState<ScenarioCreateResponse | null>(null);
  const [started, setStarted] = useState<ScenarioStartResponse | null>(null);
  const [failure, setFailure] = useState<{
    stage: 'create' | 'start';
    error: unknown;
  } | null>(null);

  const flightsQuery = useQuery({ queryKey: ['flights'], queryFn: api.flights });
  const draftReport = useMemo(() => validateDraft(draft), [draft]);
  const requestOutcome = useMemo(
    () =>
      flightsQuery.data
        ? buildCreateRequest(draft, flightsQuery.data.flights)
        : { refused: draftReport },
    [draft, draftReport, flightsQuery.data],
  );
  const report = 'refused' in requestOutcome ? requestOutcome.refused : draftReport;
  const preview = useMemo(() => buildPreview(draft), [draft]);
  const steps = useMemo(() => stepStates(draft, step, report), [draft, step, report]);
  const parsedFlights = useMemo(() => parseFlightList(flightText), [flightText]);
  const template = findTemplate(draft.templateId);

  const mutation = useMutation({
    mutationFn: ({
      request,
      runAfterCreate,
      requestKeys,
      existingCreate,
    }: {
      request: ScenarioCreateRequest;
      runAfterCreate: boolean;
      requestKeys: ReturnType<typeof createScenarioIdempotencyKeys>;
      existingCreate: ScenarioCreateResponse | null;
    }) => submitScenario(request, runAfterCreate, requestKeys, api, existingCreate),
    onSuccess: (result) => {
      if (result.created) setCreated(result.created);
      if (!result.ok) {
        setFailure({ stage: result.stage, error: result.error });
        return;
      }
      setFailure(null);
      setStarted(result.started);
      if (result.started && result.navigateTo) {
        void queryClient.invalidateQueries({ queryKey: ['current-group'] });
        void queryClient.invalidateQueries({ queryKey: ['incident-group'] });
        void queryClient.invalidateQueries({ queryKey: ['flights'] });
        navigate(result.navigateTo);
      }
    },
  });

  const resetSubmission = () => {
    setKeys(null);
    setCreated(null);
    setStarted(null);
    setFailure(null);
    mutation.reset();
  };

  const chooseTemplate = (chosen: ScenarioTemplate) => {
    resetSubmission();
    setDraft((current) => applyTemplate(current, chosen));
    setFlightText(chosen.flightNumbers.join(', '));
    setStep('details');
  };

  const commitFlightText = (raw: string) => {
    setFlightText(raw);
    resetSubmission();
    const { flights } = parseFlightList(raw);
    setDraft((current) => setFlightNumbers(current, flights));
  };

  const submit = (runAfterCreate: boolean) => {
    if (!('request' in requestOutcome)) {
      setStep(flightsQuery.data ? 'details' : 'review');
      return;
    }
    const requestKeys = keys ?? createScenarioIdempotencyKeys(() => crypto.randomUUID());
    if (!keys) setKeys(requestKeys);
    setFailure(null);
    mutation.mutate({
      request: requestOutcome.request,
      runAfterCreate,
      requestKeys,
      existingCreate: created,
    });
  };

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <Panel>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2">
          <span className="text-subtitle text-fg">Scenario builder</span>
          <StepRail
            steps={steps}
            label="Scenario builder steps"
            onSelect={(id) => {
              const next = id as ScenarioStepId;
              if (canOpenStep(draft, next)) setStep(next);
            }}
          />
          <span className="ml-auto flex items-center gap-1.5">
            <ProvenanceDot
              kind={api.canWrite ? 'real' : 'fixture'}
              provider="scenario-api"
              sourceRef={scenarioApi.createEndpoint}
            />
            <span className="text-caption uppercase text-fg-muted">
              {api.canWrite ? 'live Scenario API' : 'fixture mode — writes disabled'}
            </span>
          </span>
          <Labelled label="demo actor">
            <MonoValue muted>{DEMO_SCENARIO_ACTOR_ID}</MonoValue>
          </Labelled>
        </div>
      </Panel>

      <div className="grid min-h-0 gap-3 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="flex min-h-0 flex-col gap-3">
          {step === 'template' && (
            <Panel title="Choose a template">
              <div
                role="radiogroup"
                aria-label="Scenario template"
                className="grid gap-2 px-3 py-2 md:grid-cols-2"
              >
                {SCENARIO_TEMPLATES.map((candidate) => (
                  <TemplateCard
                    key={candidate.id}
                    template={candidate}
                    selected={draft.templateId === candidate.id}
                    onChoose={() => chooseTemplate(candidate)}
                  />
                ))}
              </div>
              <p className="border-t border-border-subtle px-3 py-1.5 text-caption text-fg-muted">
                A template sets the starting values. Every field stays editable in the next step,
                and nothing is created by choosing one.
              </p>
            </Panel>
          )}

          {step === 'details' && (
            <Panel
              title="Disruption details"
              actions={
                template && (
                  <Labelled label="from">
                    <span className="text-caption text-fg-secondary">{template.name}</span>
                  </Labelled>
                )
              }
            >
              <div className="grid gap-3 px-3 py-2 md:grid-cols-2">
                <Field label="Draft name (not sent)" issues={issuesForField(report, 'name')}>
                  <input
                    className={INPUT_CLASS}
                    value={draft.name}
                    onChange={(event) => {
                      resetSubmission();
                      setDraft((current) => ({ ...current, name: event.target.value }));
                    }}
                  />
                </Field>

                <Field
                  label="Airport"
                  hint="Four-letter ICAO indicator, e.g. VOBL."
                  issues={issuesForField(report, 'airportIcao')}
                >
                  <input
                    className={INPUT_CLASS}
                    value={draft.airportIcao}
                    maxLength={4}
                    onChange={(event) => {
                      resetSubmission();
                      setDraft((current) => ({
                        ...current,
                        airportIcao: event.target.value.toUpperCase(),
                      }));
                    }}
                  />
                </Field>

                <Field label="Starts at (UTC)" issues={issuesForField(report, 'startsAt')}>
                  <input
                    type="datetime-local"
                    className={INPUT_CLASS}
                    value={draft.startsAt}
                    onChange={(event) => {
                      resetSubmission();
                      setDraft((current) => ({ ...current, startsAt: event.target.value }));
                    }}
                  />
                </Field>

                <Field
                  label="Preview duration (not sent)"
                  issues={issuesForField(report, 'durationMinutes')}
                >
                  <input
                    type="number"
                    className={INPUT_CLASS}
                    value={String(draft.durationMinutes)}
                    min={15}
                    max={1440}
                    onChange={(event) => {
                      resetSubmission();
                      setDraft((current) => ({
                        ...current,
                        durationMinutes: Number.parseInt(event.target.value, 10),
                      }));
                    }}
                  />
                </Field>

                <Field label="Disruption type" issues={issuesForField(report, 'disruptionType')}>
                  <FilterChips<DisruptionType>
                    label="Disruption type"
                    value={draft.disruptionType}
                    onChange={(next) => {
                      resetSubmission();
                      setDraft((current) => ({ ...current, disruptionType: next }));
                    }}
                    options={DISRUPTION_TYPES.map((value) => ({
                      value,
                      label: value.replace(/_/g, ' '),
                    }))}
                  />
                </Field>

                <Field label="Severity" issues={issuesForField(report, 'severity')}>
                  <FilterChips<ScenarioSeverity>
                    label="Severity"
                    value={draft.severity}
                    onChange={(next) => {
                      resetSubmission();
                      setDraft((current) => ({ ...current, severity: next }));
                    }}
                    options={SCENARIO_SEVERITIES.map((value) => ({ value, label: value }))}
                  />
                </Field>

                <div className="md:col-span-2">
                  <Field
                    label="Affected flights"
                    hint="Comma-separated designators. Each one opens its own incident and its own approval gate."
                    issues={issuesForField(report, 'flightNumbers')}
                  >
                    <input
                      className={INPUT_CLASS}
                      value={flightText}
                      onChange={(event) => commitFlightText(event.target.value)}
                    />
                  </Field>
                  {parsedFlights.rejected.length > 0 && (
                    <p className="mt-1 flex items-start gap-1.5 text-caption text-state-warn">
                      <AlertTriangle
                        size={11}
                        strokeWidth={1.5}
                        className="mt-0.5 shrink-0"
                        aria-hidden
                      />
                      {/* Named, not silently dropped: a typo must not shrink the scenario quietly. */}
                      Not read as a flight: {parsedFlights.rejected.join(', ')}
                    </p>
                  )}
                  {draft.flightNumbers.length > 0 && (
                    <p className="mt-1 flex flex-wrap items-baseline gap-1.5">
                      <span className="text-caption uppercase text-fg-muted">reading</span>
                      <MonoValue muted>{draft.flightNumbers.length}</MonoValue>
                      <span className="text-caption uppercase text-fg-muted">flights</span>
                      <MonoValue muted>{draft.flightNumbers.join(' · ')}</MonoValue>
                    </p>
                  )}
                </div>

                {draft.flightNumbers.length > 0 && (
                  <div className="md:col-span-2">
                    <Field
                      label="Primary flight"
                      hint="The flight the disruption starts on. The others are treated as downstream."
                      issues={issuesForField(report, 'primaryFlight')}
                    >
                      <FilterChips<string>
                        label="Primary flight"
                        value={draft.primaryFlight}
                        onChange={(next) => {
                          resetSubmission();
                          setDraft((current) => ({ ...current, primaryFlight: next }));
                        }}
                        options={draft.flightNumbers.map((flight) => ({
                          value: flight,
                          label: flight,
                        }))}
                      />
                    </Field>
                  </div>
                )}

                <div className="md:col-span-2">
                  <Field
                    label="Draft notes (not sent)"
                    hint={`Local review context only; the published Scenario contract does not persist notes. ${draft.notes.length}/${MAX_NOTES_LENGTH}.`}
                    issues={issuesForField(report, 'notes')}
                  >
                    <textarea
                      className={clsx(INPUT_CLASS, 'min-h-[64px] resize-y')}
                      value={draft.notes}
                      onChange={(event) => {
                        resetSubmission();
                        setDraft((current) => ({ ...current, notes: event.target.value }));
                      }}
                    />
                  </Field>
                </div>
              </div>

              <div className="flex items-center gap-2 border-t border-border-subtle px-3 py-2">
                <button
                  type="button"
                  className={SECONDARY_BUTTON}
                  onClick={() => setStep('template')}
                >
                  <ChevronLeft size={12} strokeWidth={1.5} aria-hidden />
                  Template
                </button>
                <button
                  type="button"
                  className={PRIMARY_BUTTON}
                  onClick={() => setStep('review')}
                  disabled={!canOpenStep(draft, 'review')}
                >
                  Validate & preview
                  <ChevronRight size={12} strokeWidth={1.5} aria-hidden />
                </button>
              </div>
            </Panel>
          )}

          {step === 'review' && (
            <>
              <Panel
                title="Validation"
                actions={
                  <StateBadge
                    status={report.ok ? 'approved' : 'blocked'}
                    label={report.ok ? 'ready' : `${report.errors.length} to fix`}
                  />
                }
              >
                {flightsQuery.isLoading && (
                  <p className="border-b border-border-subtle px-3 py-1.5 text-caption text-fg-muted">
                    Loading flight-board records before validating membership…
                  </p>
                )}
                {flightsQuery.error && (
                  <div className="flex items-center gap-2 border-b border-state-crit/30 bg-state-crit-bg px-3 py-2">
                    <span className="text-caption text-state-crit">
                      Flight records could not be loaded, so no scenario can be submitted.
                    </span>
                    <button
                      type="button"
                      className={SECONDARY_BUTTON}
                      onClick={() => void flightsQuery.refetch()}
                    >
                      Retry
                    </button>
                  </div>
                )}
                {!api.canWrite && (
                  <p className="border-b border-state-warn/30 bg-state-warn-bg px-3 py-1.5 text-caption text-state-warn">
                    Fixture mode is read-only. Use the live API to create a scenario.
                  </p>
                )}
                {report.issues.length === 0 ? (
                  <p className="flex items-center gap-1.5 px-3 py-2 text-caption text-state-ok">
                    <Check size={12} strokeWidth={1.5} aria-hidden />
                    The draft is valid and every selected flight resolves to an authorable API
                    member.
                  </p>
                ) : (
                  <ul className="flex flex-col gap-1 px-3 py-2">
                    {report.issues.map((issue) => (
                      <li
                        key={`${issue.field}-${issue.code}-${issue.message}`}
                        className="flex items-start gap-2"
                      >
                        <StateBadge
                          status={issue.severity === 'error' ? 'failed' : 'at_risk'}
                          label={issue.severity}
                        />
                        <span className="min-w-0 flex-1 text-caption text-fg-secondary">
                          <MonoValue muted>{issue.field}</MonoValue> {issue.message}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                {report.warnings.length > 0 && report.ok && (
                  <p className="border-t border-border-subtle px-3 py-1.5 text-caption text-fg-muted">
                    Warnings do not block creation. They are things worth having intended.
                  </p>
                )}
              </Panel>

              <Panel title="Preview">
                <p className="border-b border-border-subtle px-3 py-1.5 text-caption text-fg-muted">
                  Draft name, duration and notes organise this local preview; the published API does
                  not persist them.
                </p>
                <dl className="grid gap-x-4 gap-y-1 px-3 py-2 md:grid-cols-2">
                  {(
                    [
                      ['name', preview.name || 'not set'],
                      ['template', preview.templateName ?? 'none'],
                      ['type', preview.disruptionType.replace(/_/g, ' ')],
                      ['airport', preview.airportIcao || 'not set'],
                      ['starts', preview.startsAt || 'not set'],
                      ['ends', preview.endsAt ?? 'not derivable yet'],
                      ['duration', `${preview.durationMinutes} minutes`],
                      ['severity', preview.severity],
                      ['primary flight', preview.primaryFlight || 'not set'],
                    ] as const
                  ).map(([label, value]) => (
                    <div key={label} className="flex items-baseline gap-2">
                      <dt className="w-[104px] shrink-0 text-caption uppercase text-fg-muted">
                        {label}
                      </dt>
                      <dd className="min-w-0 flex-1">
                        <MonoValue muted>{value}</MonoValue>
                      </dd>
                    </div>
                  ))}
                </dl>

                <div className="border-t border-border-subtle px-3 py-2">
                  <Labelled label="incidents this would open">
                    <MonoValue muted>{preview.affectedFlightCount}</MonoValue>
                  </Labelled>
                  <ul className="mt-1 flex flex-wrap gap-1">
                    {preview.affectedFlights.map((flight) => (
                      <li key={flight}>
                        <span className="inline-flex items-center gap-1.5 rounded-sm border border-border-subtle bg-inset px-1.5 py-0.5">
                          <MonoValue muted>{flight}</MonoValue>
                          {flight === preview.primaryFlight && (
                            <span className="text-caption uppercase text-accent">primary</span>
                          )}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>

                {/*
                  The figures this console will not produce, named. A review step that showed an
                  invented passenger count would be the most convincing wrong number in the product.
                */}
                <div className="border-t border-border-subtle px-3 py-2">
                  <span className="text-caption uppercase text-fg-muted">
                    computed by the engine when the scenario runs
                  </span>
                  <ul className="mt-1 flex flex-wrap gap-1">
                    {preview.computedByEngine.map((figure) => (
                      <li
                        key={figure}
                        className="rounded-sm border border-border-subtle px-1.5 py-0.5 text-caption text-fg-muted"
                      >
                        {figure}
                      </li>
                    ))}
                  </ul>
                </div>

                <div className="flex flex-wrap items-center gap-2 border-t border-border-subtle px-3 py-2">
                  <button
                    type="button"
                    className={SECONDARY_BUTTON}
                    onClick={() => setStep('details')}
                  >
                    <ChevronLeft size={12} strokeWidth={1.5} aria-hidden />
                    Edit details
                  </button>
                  <button
                    type="button"
                    className={SECONDARY_BUTTON}
                    onClick={() => submit(false)}
                    disabled={
                      !report.ok || !flightsQuery.data || !api.canWrite || mutation.isPending
                    }
                  >
                    <Plus size={12} strokeWidth={1.5} aria-hidden />
                    {mutation.isPending && !created ? 'Creating…' : 'Create scenario'}
                  </button>
                  <button
                    type="button"
                    className={PRIMARY_BUTTON}
                    onClick={() => submit(true)}
                    disabled={
                      !report.ok || !flightsQuery.data || !api.canWrite || mutation.isPending
                    }
                  >
                    <Play size={12} strokeWidth={1.5} aria-hidden />
                    {mutation.isPending ? (created ? 'Starting…' : 'Creating…') : 'Create & run'}
                  </button>
                  <span className="ml-auto text-caption uppercase text-fg-muted">
                    creation persists membership; start opens incidents — later actions remain
                    assurance-gated
                  </span>
                </div>
              </Panel>

              {(created || failure || mutation.isPending) && (
                <ResultPanel
                  created={created}
                  started={started}
                  failure={failure}
                  pending={mutation.isPending}
                  onRetryStart={() => submit(true)}
                />
              )}
            </>
          )}
        </div>

        <div className="flex min-h-0 flex-col gap-3">
          <Panel title="Draft">
            <dl className="flex flex-col gap-1 px-3 py-2">
              {(
                [
                  ['template', template?.name ?? 'not chosen'],
                  ['airport', draft.airportIcao || 'not set'],
                  ['flights', String(draft.flightNumbers.length)],
                  ['severity', draft.severity],
                ] as const
              ).map(([label, value]) => (
                <div key={label} className="flex items-baseline gap-2">
                  <dt className="w-[72px] shrink-0 text-caption uppercase text-fg-muted">
                    {label}
                  </dt>
                  <dd className="min-w-0 flex-1">
                    <MonoValue muted>{value}</MonoValue>
                  </dd>
                </div>
              ))}
            </dl>
            <div className="flex items-center gap-2 border-t border-border-subtle px-3 py-2">
              <StateBadge
                status={report.ok ? 'approved' : 'pending'}
                label={report.ok ? 'valid' : 'incomplete'}
              />
              {report.warnings.length > 0 && (
                <span className="text-caption uppercase text-state-warn">
                  {report.warnings.length} warning{report.warnings.length === 1 ? '' : 's'}
                </span>
              )}
            </div>
          </Panel>

          <Panel title="What happens next">
            <ol className="flex flex-col gap-2 px-3 py-2">
              {[
                'The scenario records the selected persisted flights, airport and effective time.',
                'Starting opens one incident per affected flight; later actions pass their own assurance gates.',
                'Every high-risk action still waits for a named person, exactly as it does today.',
                'Figures on the operations screens come from the engine, never from this form.',
              ].map((line, index) => (
                <li key={line} className="flex items-start gap-2">
                  <MonoValue muted>{index + 1}</MonoValue>
                  <span className="min-w-0 flex-1 text-caption text-fg-secondary">{line}</span>
                </li>
              ))}
            </ol>
          </Panel>
        </div>
      </div>
    </div>
  );
}

/**
 * Scenario Builder — `/scenarios/new`. Phase 5.
 *
 * Operators choose a template, edit and validate its declarations, then use the published scenario
 * lifecycle. The live adapter resolves designators to recorded flight IDs and delays before POSTing;
 * Create persists only, while Create & Run starts the scenario and opens its canonical incidents.
 * Neither path advances actions or approvals. Impact figures remain engine-owned and are never
 * guessed in this screen.
 *
 * Draft validation and request mapping live in pure modules so malformed or unrecorded flights
 * cannot reach the backend, and the Create & Run regression can exercise the exact API sequence.
 *
 * Owner: Stream D.
 */

import { useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Check, ChevronLeft, ChevronRight, Play, Plus } from 'lucide-react';
import { clsx } from 'clsx';

import { api, ApiError } from '@/api/client';
import { MonoValue, Panel, ProvenanceDot, StateBadge } from '@/components/ui/primitives';
import { StepRail } from '@/components/ui/primitives';
import { FilterChips } from '@/components/ui/Metric';
import {
  Button,
  DefinitionList,
  DefinitionRow,
  FIELD_SHELL,
  Labelled,
  Notice,
  PageHeader,
  PanelBody,
  PanelSection,
  Toolbar,
} from '@/components/ui/composition';
import {
  DISRUPTION_TYPES,
  SCENARIO_SEVERITIES,
  SCENARIO_TEMPLATES,
  findTemplate,
  type DisruptionType,
  type ScenarioSeverity,
  type ScenarioTemplate,
} from './scenarioContracts';
import {
  MAX_NOTES_LENGTH,
  applyTemplate,
  buildPreview,
  canOpenStep,
  emptyDraft,
  issuesForField,
  parseFlightList,
  setFlightNumbers,
  stepStates,
  validateDraft,
  type ScenarioStepId,
  type ValidationIssue,
} from './scenarioDraft';
import {
  ScenarioLifecycleFailure,
  ScenarioSubmissionError,
  submitScenario,
  type ScenarioLifecycleResult,
} from './scenarioLifecycle';

/*
 * The local `INPUT_CLASS`, `PRIMARY_BUTTON` and `SECONDARY_BUTTON` constants that used to live here
 * are now `FIELD_SHELL` and `Button` in `@/components/ui/composition`.
 *
 * They were the clearest primitives-in-waiting in the codebase: this screen held the only real
 * button vocabulary in the product while five other surfaces hand-rolled their own, which is how the
 * approval control and the plan-selection control ended up disagreeing about padding and type size.
 * Moving them changes no pixel here and gives every other screen the same three affordances.
 */

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

/*
 * The local `Labelled` is now the shared one in `@/components/ui/composition`.
 *
 * Three screens each carried a copy, and each copy carried its own version of the same comment: the
 * component exists because an uppercased *wrapper* case-transforms the values inside it, so the
 * request id `scn-1a2b3c4d` rendered as `SCN-1A2B3C4D` and the endpoint path as `/API/V1/SCENARIOS`.
 * An operator copying either would copy something the console never produced — the same defect class
 * as rendering "MoCA" as "MOCA". One component, one rule: `uppercase` goes on the label span only.
 */

/** Persisted lifecycle result; every value below came from the API response. */
function SubmissionPanel({ result }: { result: ScenarioLifecycleResult }) {
  return (
    <Panel
      title={result.started ? 'Scenario started' : 'Scenario created'}
      actions={
        <StateBadge
          status={result.started ? 'approved' : 'pending'}
          label={result.started ? 'incidents opened' : 'persisted'}
        />
      }
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5">
        <Labelled label="scenario">
          <MonoValue muted>{result.created.scenario_reference}</MonoValue>
        </Labelled>
        <Labelled label="members">
          <MonoValue muted>{result.created.members.length}</MonoValue>
        </Labelled>
        <ProvenanceDot
          kind={result.created.provenance.kind}
          provider={result.created.provenance.provider}
          sourceRef={result.created.provenance.source_ref}
        />
      </div>
      <Notice tone="muted" icon={false}>
        {result.started
          ? `${result.started.opened_incident_ids.length} canonical incidents were opened. No action was executed or approved.`
          : 'The scenario is persisted. Its incidents have not been opened or advanced.'}
      </Notice>
    </Panel>
  );
}

function SubmissionFailure({ error }: { error: Error }) {
  const partial = error instanceof ScenarioLifecycleFailure ? error : null;
  const cause = partial?.cause ?? error;
  const apiError = cause instanceof ApiError ? cause : null;
  const lifecycleError = cause instanceof ScenarioSubmissionError ? cause : null;
  return (
    <Panel title={partial ? 'Scenario lifecycle incomplete' : 'Scenario not submitted'}>
      <Notice tone="crit" alert>
        <div>
          <p>{error.message}</p>
          {partial && (
            <p className="mt-1 text-fg-secondary">
              The recorded scenario is{' '}
              <MonoValue muted>{partial.progress.created.scenario_reference}</MonoValue>. Retry uses
              the same idempotency keys and resumes the recorded lifecycle.
            </p>
          )}
          <p className="mt-1 font-mono text-mono-sm text-fg-muted">
            {apiError?.code ?? lifecycleError?.code ?? 'INTERNAL_ERROR'}
            {apiError?.correlationId ? ` · ${apiError.correlationId}` : ''}
          </p>
        </div>
      </Notice>
    </Panel>
  );
}

export function ScenarioBuilder() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const operationKey = useRef(crypto.randomUUID());
  const [draft, setDraft] = useState(emptyDraft);
  const [step, setStep] = useState<ScenarioStepId>('template');
  const [flightText, setFlightText] = useState('');
  const [submission, setSubmission] = useState<ScenarioLifecycleResult | null>(null);
  const [submissionError, setSubmissionError] = useState<Error | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const flightsQuery = useQuery({ queryKey: ['flights'], queryFn: api.flights });

  const report = useMemo(() => validateDraft(draft), [draft]);
  const preview = useMemo(() => buildPreview(draft), [draft]);
  const steps = useMemo(() => stepStates(draft, step, report), [draft, step, report]);
  const parsedFlights = useMemo(() => parseFlightList(flightText), [flightText]);
  const template = findTemplate(draft.templateId);

  const clearSubmission = () => {
    operationKey.current = crypto.randomUUID();
    setSubmission(null);
    setSubmissionError(null);
  };

  const chooseTemplate = (chosen: ScenarioTemplate) => {
    setDraft((current) => applyTemplate(current, chosen));
    setFlightText(chosen.flightNumbers.join(', '));
    clearSubmission();
    setStep('details');
  };

  const commitFlightText = (raw: string) => {
    setFlightText(raw);
    clearSubmission();
    const { flights } = parseFlightList(raw);
    setDraft((current) => setFlightNumbers(current, flights));
  };

  const submit = async (runAfterCreate: boolean) => {
    if (!report.ok) {
      clearSubmission();
      setStep('details');
      return;
    }
    if (!api.canWrite) {
      setSubmissionError(
        new ScenarioSubmissionError(
          'LIVE_API_REQUIRED',
          'Scenario creation requires the live API; fixture mode cannot record writes.',
        ),
      );
      return;
    }
    if (!flightsQuery.data) {
      setSubmissionError(
        flightsQuery.error instanceof Error
          ? flightsQuery.error
          : new ScenarioSubmissionError(
              'FLIGHTS_UNAVAILABLE',
              'The flight board is not available.',
            ),
      );
      return;
    }

    setSubmission(null);
    setSubmissionError(null);
    setIsSubmitting(true);
    try {
      const result = await submitScenario(api, draft, flightsQuery.data.flights, {
        runAfterCreate,
        operationKey: operationKey.current,
      });
      setSubmission(result);
      if (result.route) {
        queryClient.setQueryData(['incident-group', 'current'], {
          selected: result.selected,
          detail: result.detail,
        });
        await queryClient.invalidateQueries({ queryKey: ['incident-groups'] });
        navigate(result.route);
      }
    } catch (error) {
      setSubmissionError(error instanceof Error ? error : new Error('Scenario submission failed.'));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-col gap-3">
      {/*
       * The draft's own name is the subject of this screen, so it is the title. Rendered verbatim:
       * it is operator-typed text and CSS-transforming it would show them something they did not
       * write. The rail moves to the header footer, where it reads as progress through the screen
       * rather than as one more chip in a row of chips.
       */}
      <PageHeader
        eyebrow="Scenario builder"
        title={draft.name.trim() === '' ? 'Untitled scenario' : draft.name}
        status={
          <StateBadge
            status={report.ok ? 'approved' : 'pending'}
            label={report.ok ? 'valid draft' : 'incomplete draft'}
          />
        }
        meta={
          <>
            <Labelled label="airport">
              <MonoValue muted>{draft.airportIcao || 'not set'}</MonoValue>
            </Labelled>
            <Labelled label="flights">
              <MonoValue muted>{draft.flightNumbers.length}</MonoValue>
            </Labelled>
            <Labelled label="severity">
              <MonoValue muted>{draft.severity}</MonoValue>
            </Labelled>
          </>
        }
        actions={
          <Toolbar>
            <ProvenanceDot
              kind={api.canWrite ? 'simulated' : 'unavailable'}
              provider="scenario-builder"
              sourceRef="POST /api/v1/scenarios"
            />
            <span className="text-caption uppercase text-fg-muted">
              {api.canWrite ? 'published scenario lifecycle' : 'live API required'}
            </span>
          </Toolbar>
        }
        footer={
          <StepRail
            steps={steps}
            label="Scenario builder steps"
            onSelect={(id) => {
              const next = id as ScenarioStepId;
              if (canOpenStep(draft, next)) setStep(next);
            }}
          />
        }
      />

      <div className="grid min-h-0 gap-3 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="flex min-h-0 flex-col gap-3">
          {step === 'template' && (
            <Panel title="Choose a template">
              <div
                role="radiogroup"
                aria-label="Scenario template"
                className="grid gap-2 px-3 py-2.5 md:grid-cols-2 2xl:grid-cols-3"
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
              <Notice tone="muted" icon={false}>
                A template sets the starting values. Every field stays editable in the next step,
                and nothing is created by choosing one.
              </Notice>
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
              <div className="grid gap-x-6 gap-y-3.5 px-3 py-3 md:grid-cols-2">
                <Field
                  label="Scenario name"
                  hint="Draft-only label for review; the published scenario contract does not persist it."
                  issues={issuesForField(report, 'name')}
                >
                  <input
                    className={FIELD_SHELL}
                    value={draft.name}
                    onChange={(event) => {
                      clearSubmission();
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
                    className={FIELD_SHELL}
                    value={draft.airportIcao}
                    maxLength={4}
                    onChange={(event) => {
                      clearSubmission();
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
                    className={FIELD_SHELL}
                    value={draft.startsAt}
                    onChange={(event) => {
                      clearSubmission();
                      setDraft((current) => ({ ...current, startsAt: event.target.value }));
                    }}
                  />
                </Field>

                <Field
                  label="Duration (minutes)"
                  hint="Draft-only preview window; the published scenario contract records the effective start."
                  issues={issuesForField(report, 'durationMinutes')}
                >
                  <input
                    type="number"
                    className={FIELD_SHELL}
                    value={String(draft.durationMinutes)}
                    min={15}
                    max={1440}
                    onChange={(event) => {
                      clearSubmission();
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
                      clearSubmission();
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
                      clearSubmission();
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
                      className={FIELD_SHELL}
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
                          clearSubmission();
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
                    label="Notes"
                    hint={`Draft-only operator context; the published scenario contract does not persist this field. ${draft.notes.length}/${MAX_NOTES_LENGTH}.`}
                    issues={issuesForField(report, 'notes')}
                  >
                    <textarea
                      className={clsx(FIELD_SHELL, 'min-h-[72px] resize-y')}
                      value={draft.notes}
                      onChange={(event) => {
                        clearSubmission();
                        setDraft((current) => ({ ...current, notes: event.target.value }));
                      }}
                    />
                  </Field>
                </div>
              </div>

              <div className="flex items-center gap-2 border-t border-border-subtle px-3 py-2.5">
                <Button icon={ChevronLeft} onClick={() => setStep('template')}>
                  Template
                </Button>
                <Button
                  variant="primary"
                  onClick={() => setStep('review')}
                  disabled={!canOpenStep(draft, 'review')}
                  disabledReason="Fill in the required fields first. Each one names its own problem beside the input."
                >
                  Validate &amp; preview
                  <ChevronRight size={12} strokeWidth={1.5} aria-hidden />
                </Button>
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
                {report.issues.length === 0 ? (
                  <p className="flex items-center gap-1.5 px-3 py-2.5 text-caption text-state-ok">
                    <Check size={12} strokeWidth={1.5} aria-hidden />
                    Every draft field is present and well-formed. Flight identity and delay are
                    resolved from the live board before submission.
                  </p>
                ) : (
                  <ul className="flex flex-col gap-1.5 px-3 py-2.5">
                    {report.issues.map((issue) => (
                      <li key={`${issue.field}-${issue.code}`} className="flex items-start gap-2">
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
                  <Notice tone="muted" icon={false}>
                    Warnings do not block creation. They are things worth having intended.
                  </Notice>
                )}
              </Panel>

              <Panel title="Preview">
                <div className="px-3 py-2.5">
                  <DefinitionList className="gap-x-6 gap-y-1 md:grid md:grid-cols-2">
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
                      <DefinitionRow key={label} label={label}>
                        <MonoValue muted>{value}</MonoValue>
                      </DefinitionRow>
                    ))}
                  </DefinitionList>
                </div>

                <PanelSection title="Incidents this would open" count={preview.affectedFlightCount}>
                  <ul className="flex flex-wrap gap-1">
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
                </PanelSection>

                {/*
                  The figures this console will not produce, named. A review step that showed an
                  invented passenger count would be the most convincing wrong number in the product.
                */}
                <PanelSection title="Computed by the engine when the scenario runs" tone="muted">
                  <ul className="flex flex-wrap gap-1">
                    {preview.computedByEngine.map((figure) => (
                      <li
                        key={figure}
                        className="rounded-sm border border-border-subtle px-1.5 py-0.5 text-caption text-fg-muted"
                      >
                        {figure}
                      </li>
                    ))}
                  </ul>
                </PanelSection>

                <div className="flex flex-wrap items-center gap-2 border-t border-border-subtle px-3 py-2.5">
                  <Button icon={ChevronLeft} onClick={() => setStep('details')}>
                    Edit details
                  </Button>
                  <Button
                    icon={Plus}
                    onClick={() => void submit(false)}
                    disabled={!report.ok || !api.canWrite || isSubmitting || flightsQuery.isLoading}
                    disabledReason="A valid draft and the live flight board are required before creation."
                  >
                    Create scenario
                  </Button>
                  <Button
                    variant="primary"
                    icon={Play}
                    onClick={() => void submit(true)}
                    disabled={!report.ok || !api.canWrite || isSubmitting || flightsQuery.isLoading}
                    disabledReason="A valid draft and the live flight board are required before creation."
                  >
                    Create &amp; run
                  </Button>
                  <span className="ml-auto text-caption uppercase text-fg-muted">
                    Create & run opens one incident per flight — it approves nothing
                  </span>
                </div>
              </Panel>

              {submission && <SubmissionPanel result={submission} />}
              {submissionError && <SubmissionFailure error={submissionError} />}
            </>
          )}
        </div>

        <div className="flex min-h-0 flex-col gap-3">
          <Panel title="Draft">
            <PanelBody gap="tight">
              <DefinitionList width="sm">
                {(
                  [
                    ['template', template?.name ?? 'not chosen'],
                    ['airport', draft.airportIcao || 'not set'],
                    ['flights', String(draft.flightNumbers.length)],
                    ['severity', draft.severity],
                  ] as const
                ).map(([label, value]) => (
                  <DefinitionRow key={label} label={label} width="sm">
                    <MonoValue muted>{value}</MonoValue>
                  </DefinitionRow>
                ))}
              </DefinitionList>
            </PanelBody>
            <div className="flex items-center gap-2 border-t border-border-subtle px-3 py-2.5">
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
            <ol className="flex flex-col gap-2 px-3 py-2.5">
              {[
                'The scenario declares membership over flights already recorded on the live board.',
                'One incident opens per affected flight, each with its own assurance gate.',
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

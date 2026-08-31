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
 * What this screen refuses to do:
 *
 *   1. **It does not report a scenario as created.** No endpoint accepts one. `Create` and
 *      `Create & Run` build the exact request body and show it, labelled as prepared and not sent,
 *      with the endpoint that would receive it. Rendering a fabricated scenario id would put a state
 *      transition on screen that never happened — the rule `api/client.ts` sets for every write.
 *   2. **It computes no impact figures.** Passengers, connections, crew pairings and hotels are
 *      derived by the engine from the seeded dataset. The preview names them as pending rather than
 *      guessing, because a passenger count typed into a form is a number no record supports.
 *   3. **It offers a command only when the command works.** The equivalent CLI line appears only for
 *      a draft still matching a scenario this repository actually seeds.
 *
 * All validation, preview and step logic lives in `scenarioDraft.ts` so it is unit-tested; this file
 * is rendering and local state only.
 *
 * Owner: Stream D.
 */

import { useMemo, useState } from 'react';
import {
  AlertTriangle,
  Check,
  ChevronLeft,
  ChevronRight,
  Play,
  Plus,
  Terminal,
} from 'lucide-react';
import { clsx } from 'clsx';

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
  type ScenarioRequestReceipt,
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
  prepareScenarioRequest,
  setFlightNumbers,
  stepStates,
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

/** The prepared request. Everything here is a fact about the console, not about the backend. */
function ReceiptPanel({ receipt }: { receipt: ScenarioRequestReceipt }) {
  return (
    <Panel title="Prepared request">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2">
        <StateBadge status="pending" label="prepared, not sent" />
        <Labelled label="request">
          <MonoValue muted>{receipt.requestId}</MonoValue>
        </Labelled>
        <Labelled label="would POST to">
          <MonoValue muted>{receipt.targetEndpoint}</MonoValue>
        </Labelled>
        {receipt.payload.run_after_create && (
          <Labelled label="then">
            <MonoValue muted>{scenarioApi.runEndpoint}</MonoValue>
          </Labelled>
        )}
      </div>

      {/* The console's own sentence about why nothing was sent. Never blank while unsubmitted. */}
      <p className="border-t border-state-warn/30 bg-state-warn-bg px-3 py-1.5 text-caption text-state-warn">
        {receipt.unsubmittedReason}
      </p>

      {receipt.equivalentCommand && (
        <div className="border-t border-border-subtle px-3 py-2">
          <span className="flex items-center gap-1.5 text-caption uppercase text-fg-muted">
            <Terminal size={11} strokeWidth={1.5} aria-hidden />
            this draft is reproducible today
          </span>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all font-mono text-mono-sm text-fg-secondary">
            {receipt.equivalentCommand}
          </pre>
        </div>
      )}

      <div className="border-t border-border-subtle px-3 py-2">
        <span className="text-caption uppercase text-fg-muted">request body</span>
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all font-mono text-mono-sm text-fg-secondary">
          {JSON.stringify(receipt.payload, null, 2)}
        </pre>
      </div>
    </Panel>
  );
}

export function ScenarioBuilder() {
  const [draft, setDraft] = useState(emptyDraft);
  const [step, setStep] = useState<ScenarioStepId>('template');
  const [flightText, setFlightText] = useState('');
  const [receipt, setReceipt] = useState<ScenarioRequestReceipt | null>(null);

  const report = useMemo(() => validateDraft(draft), [draft]);
  const preview = useMemo(() => buildPreview(draft), [draft]);
  const steps = useMemo(() => stepStates(draft, step, report), [draft, step, report]);
  const parsedFlights = useMemo(() => parseFlightList(flightText), [flightText]);
  const template = findTemplate(draft.templateId);

  const chooseTemplate = (chosen: ScenarioTemplate) => {
    setDraft((current) => applyTemplate(current, chosen));
    setFlightText(chosen.flightNumbers.join(', '));
    setReceipt(null);
    setStep('details');
  };

  const commitFlightText = (raw: string) => {
    setFlightText(raw);
    setReceipt(null);
    const { flights } = parseFlightList(raw);
    setDraft((current) => setFlightNumbers(current, flights));
  };

  const prepare = (runAfterCreate: boolean) => {
    const outcome = prepareScenarioRequest(draft, { runAfterCreate, now: new Date() });
    if ('receipt' in outcome) {
      setReceipt(outcome.receipt);
      setStep('review');
      return;
    }
    // Refused. The report already renders beside every field; move to the step that shows it.
    setReceipt(null);
    setStep('details');
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
              kind="unavailable"
              provider="scenario-authoring"
              sourceRef={scenarioApi.createEndpoint}
            />
            <span className="text-caption uppercase text-fg-muted">
              authoring endpoint not published
            </span>
          </span>
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
                <Field label="Scenario name" issues={issuesForField(report, 'name')}>
                  <input
                    className={INPUT_CLASS}
                    value={draft.name}
                    onChange={(event) => {
                      setReceipt(null);
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
                      setReceipt(null);
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
                      setReceipt(null);
                      setDraft((current) => ({ ...current, startsAt: event.target.value }));
                    }}
                  />
                </Field>

                <Field
                  label="Duration (minutes)"
                  issues={issuesForField(report, 'durationMinutes')}
                >
                  <input
                    type="number"
                    className={INPUT_CLASS}
                    value={String(draft.durationMinutes)}
                    min={15}
                    max={1440}
                    onChange={(event) => {
                      setReceipt(null);
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
                      setReceipt(null);
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
                      setReceipt(null);
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
                          setReceipt(null);
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
                    hint={`Recorded with the scenario so a replay can say why it was run. ${draft.notes.length}/${MAX_NOTES_LENGTH}.`}
                    issues={issuesForField(report, 'notes')}
                  >
                    <textarea
                      className={clsx(INPUT_CLASS, 'min-h-[64px] resize-y')}
                      value={draft.notes}
                      onChange={(event) => {
                        setReceipt(null);
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
                {report.issues.length === 0 ? (
                  <p className="flex items-center gap-1.5 px-3 py-2 text-caption text-state-ok">
                    <Check size={12} strokeWidth={1.5} aria-hidden />
                    Every field the request needs is present and well-formed.
                  </p>
                ) : (
                  <ul className="flex flex-col gap-1 px-3 py-2">
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
                  <p className="border-t border-border-subtle px-3 py-1.5 text-caption text-fg-muted">
                    Warnings do not block creation. They are things worth having intended.
                  </p>
                )}
              </Panel>

              <Panel title="Preview">
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
                    onClick={() => prepare(false)}
                    disabled={!report.ok}
                  >
                    <Plus size={12} strokeWidth={1.5} aria-hidden />
                    Create scenario
                  </button>
                  <button
                    type="button"
                    className={PRIMARY_BUTTON}
                    onClick={() => prepare(true)}
                    disabled={!report.ok}
                  >
                    <Play size={12} strokeWidth={1.5} aria-hidden />
                    Create &amp; run
                  </button>
                  <span className="ml-auto text-caption uppercase text-fg-muted">
                    creating opens one gate per flight — it approves nothing
                  </span>
                </div>
              </Panel>

              {receipt && <ReceiptPanel receipt={receipt} />}
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
                'The scenario seeds flights, passengers and crew for the airport and window you set.',
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

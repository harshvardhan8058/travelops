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

/** The prepared request. Everything here is a fact about the console, not about the backend. */
function ReceiptPanel({ receipt }: { receipt: ScenarioRequestReceipt }) {
  return (
    <Panel
      title="Prepared request"
      actions={<StateBadge status="pending" label="prepared, not sent" />}
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5">
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
      <Notice tone="warn">{receipt.unsubmittedReason}</Notice>

      {receipt.equivalentCommand && (
        <PanelSection title="This draft is reproducible today" tone="muted">
          <div className="flex items-start gap-1.5">
            <Terminal
              size={12}
              strokeWidth={1.5}
              className="mt-1 shrink-0 text-fg-muted"
              aria-hidden
            />
            <pre className="min-w-0 overflow-x-auto whitespace-pre-wrap break-all rounded-sm bg-inset px-2 py-1.5 font-mono text-mono-sm text-fg-secondary">
              {receipt.equivalentCommand}
            </pre>
          </div>
        </PanelSection>
      )}

      <PanelSection title="Request body" tone="muted">
        <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-sm bg-inset px-2 py-1.5 font-mono text-mono-sm text-fg-secondary">
          {JSON.stringify(receipt.payload, null, 2)}
        </pre>
      </PanelSection>
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
              kind="unavailable"
              provider="scenario-authoring"
              sourceRef={scenarioApi.createEndpoint}
            />
            <span className="text-caption uppercase text-fg-muted">
              authoring endpoint not published
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
                <Field label="Scenario name" issues={issuesForField(report, 'name')}>
                  <input
                    className={FIELD_SHELL}
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
                    className={FIELD_SHELL}
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
                    className={FIELD_SHELL}
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
                    className={FIELD_SHELL}
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
                      className={clsx(FIELD_SHELL, 'min-h-[72px] resize-y')}
                      value={draft.notes}
                      onChange={(event) => {
                        setReceipt(null);
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
                    Every field the request needs is present and well-formed.
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
                    onClick={() => prepare(false)}
                    disabled={!report.ok}
                    disabledReason="The draft still has errors. Fix them in Disruption details first."
                  >
                    Create scenario
                  </Button>
                  <Button
                    variant="primary"
                    icon={Play}
                    onClick={() => prepare(true)}
                    disabled={!report.ok}
                    disabledReason="The draft still has errors. Fix them in Disruption details first."
                  >
                    Create &amp; run
                  </Button>
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

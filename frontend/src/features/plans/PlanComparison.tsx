/**
 * Candidate recovery plans, side by side.
 *
 * This is what-if in the only form the architecture permits: a **re-evaluation of the same recorded
 * facts** under a different plan shape. Not a projection, not a simulation. The server pins `basis`
 * to a literal so the contract cannot express a forecast, and the banner states that verbatim
 * rather than the screen implying it.
 *
 * Three rules this screen holds:
 *
 *   1. **No ranking.** The API returns no score, rank or `recommended` flag, and this component
 *      invents none. Choosing between recovery plans is a judgement with an owner, and the owner is
 *      the operator — so the comparison presents differences and the selection control, nothing
 *      more persuasive than that.
 *
 *   2. **Differences are marked, not ordered.** A cell that differs across candidates is flagged so
 *      the eye lands on it, which is the actual job: eight identical figures and one different one
 *      should take a second to read, not a minute.
 *
 *   3. **Inadmissible is shown, not hidden.** A candidate the plan gate refuses stays on screen
 *      with its blocking checks named. Hiding it would present a shorter list as though those were
 *      the only options considered.
 *
 * Owner: Stream D.
 */

import { useMemo, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { clsx } from 'clsx';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';

import { api, ApiError } from '@/api/client';
import { resolveUnavailable, retryUnlessUnavailable } from '@/api/unavailable';
import type { CandidateComparisonRow } from '@/api/types';
import { Metric } from '@/components/ui/Metric';
import { candidateDerivation } from '@/components/ui/derivation';
import {
  EmptyState,
  ErrorState,
  LoadingState,
  MonoValue,
  Panel,
  StateBadge,
} from '@/components/ui/primitives';
import {
  Absent,
  Button,
  Labelled,
  Notice,
  NotYetAvailable,
  PageHeader,
  PanelBody,
  ReasonField,
  TableFrame,
  Toolbar,
} from '@/components/ui/composition';

/** Rows of the comparison table. `format` keeps the arithmetic in the API, not here. */
const FIELDS: {
  key: keyof CandidateComparisonRow;
  label: string;
  hint?: string;
}[] = [
  { key: 'task_count', label: 'Tasks' },
  { key: 'plan_risk_tier', label: 'Plan risk tier' },
  { key: 'high_risk_actions', label: 'High-risk actions', hint: 'Each needs its own approval' },
  { key: 'approvals_required', label: 'Approvals required' },
  { key: 'external_effects', label: 'External effects', hint: 'Reaches outside our systems' },
  { key: 'exposure_inr', label: 'Exposure (INR)' },
  { key: 'passengers_affected', label: 'Passengers' },
  { key: 'rooms_committed', label: 'Rooms committed' },
  { key: 'uncovered_entities', label: 'Uncovered entities' },
];

/**
 * A key that is unique per COLUMN, not per candidate id.
 *
 * `candidate_id` is the backend's `plan.variant_key or f"plan-{plan.id}"`, and two candidates in one
 * comparison can legitimately carry the same variant key — the live cascade returns two candidates
 * both keyed `notify-first`. Using it as a React key made every `<th>` in the head and every `<td>`
 * in each row a duplicate, which React reports as a console error:
 *
 *     Encountered two children with the same key, `notify-first`.
 *
 * That is not cosmetic. React may reuse or drop a node with a colliding key, so two candidate
 * columns could render each other's figures — on the one screen whose entire job is to let an
 * operator tell two plans apart. The browser gate treats any console error as a route failure, and
 * this was the single failing check across all eleven routes.
 *
 * The fix belongs here rather than in the contract: the position of a column is a fact about this
 * table, and the id stays exactly as the API returned it everywhere it is displayed.
 */
function columnKey(row: CandidateComparisonRow, index: number): string {
  return `${index}:${row.candidate_id}`;
}

function display(value: unknown): string | number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (Array.isArray(value)) return value.length === 0 ? '—' : value.join(', ');
  if (typeof value === 'number' || typeof value === 'string') return value;
  return null;
}

/*
 * The local copy of this classification lived here and a second copy lived in the approval queue.
 * Two derivations of "does this data exist yet" is how two screens end up disagreeing about whether
 * a 404 is a failure, so both now call `@/api/unavailable`.
 */

export function PlanComparison() {
  const { incidentId = '' } = useParams<{ incidentId: string }>();
  const queryClient = useQueryClient();
  const [reason, setReason] = useState('');
  const [pending, setPending] = useState<number | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  /*
   * Both endpoints propose candidates, so both answer 404 with a resolution until the incident has a
   * plan. Not retried: the answer is already known and retrying only delays the empty state.
   */
  const comparison = useQuery({
    queryKey: ['plan-comparison', incidentId],
    queryFn: () => api.planComparison(incidentId),
    enabled: Boolean(incidentId),
    retry: retryUnlessUnavailable,
  });

  const plans = useQuery({
    queryKey: ['plans', incidentId],
    queryFn: () => api.plans(incidentId),
    enabled: Boolean(incidentId),
    retry: retryUnlessUnavailable,
  });

  const select = useMutation({
    mutationFn: ({ planId, why }: { planId: number; why: string }) =>
      api.selectPlan(incidentId, planId, why),
    onSuccess: () => {
      setFailure(null);
      setReason('');
      setPending(null);
      void queryClient.invalidateQueries({ queryKey: ['plans', incidentId] });
      void queryClient.invalidateQueries({ queryKey: ['plan-comparison', incidentId] });
      void queryClient.invalidateQueries({ queryKey: ['incident', incidentId] });
    },
    onError: (error) => {
      setPending(null);
      setFailure(
        error instanceof ApiError
          ? `${error.code}: ${error.message}`
          : 'The selection was not recorded.',
      );
    },
  });

  /** Which fields actually differ. Marked so the eye lands on the difference. */
  const differing = useMemo(() => {
    const rows = comparison.data?.candidates ?? [];
    if (rows.length < 2) return new Set<string>();
    const keys = new Set<string>();
    for (const field of FIELDS) {
      const values = rows.map((row) => JSON.stringify(row[field.key] ?? null));
      if (new Set(values).size > 1) keys.add(String(field.key));
    }
    return keys;
  }, [comparison.data]);

  if (comparison.isLoading || plans.isLoading) return <LoadingState label="Comparing plans" />;

  /*
   * A genuine failure on either query wins over a not-yet on the other: a screen that reports "no
   * candidates yet" while one of its endpoints is actually broken has hidden an outage behind an
   * empty state.
   */
  const outcome = resolveUnavailable([comparison.error, plans.error]);

  if (outcome && 'unavailable' in outcome) {
    return (
      <NotYetAvailable title="No candidates to compare yet" unavailable={outcome.unavailable} />
    );
  }

  if (outcome) {
    const queryError = outcome.failure;
    return (
      <ErrorState
        code={queryError instanceof ApiError ? queryError.code : 'UNAVAILABLE'}
        message={
          queryError instanceof ApiError
            ? queryError.message
            : 'The plan endpoints did not respond.'
        }
        correlationId={queryError instanceof ApiError ? queryError.correlationId : null}
        onRetry={() => {
          void comparison.refetch();
          void plans.refetch();
        }}
      />
    );
  }

  const data = comparison.data;
  if (!data || data.candidates.length === 0) {
    return (
      <EmptyState
        title="No candidates to compare"
        description="Candidates are derived from the deterministic playbook once a plan exists. Run the incident to planning first."
      />
    );
  }

  const selectedId = plans.data?.selected_plan_id ?? null;
  const canSelect = api.canWrite && reason.trim().length > 0;

  return (
    <div className="flex flex-col gap-3">
      {/*
       * The comparison is the screen, so the count of candidates is the title and the basis becomes
       * the header's supporting row rather than a panel of its own. `not_a_forecast` is the most
       * important sentence here — it is the boundary the whole surface depends on — and it was
       * `text-body text-fg-secondary` at the bottom of the visual stack.
       *
       * The LABEL is uppercased, never the value. `basis` is a contract literal and `decision` is a
       * gate enum: CSS-transforming either misrepresents what the API returned, which is the same
       * defect that once rendered the policy pack label as "MOCA". `Labelled` enforces that.
       */}
      <PageHeader
        eyebrow="Plan comparison"
        title={`${data.candidates.length} candidate${data.candidates.length === 1 ? '' : 's'}`}
        status={<StateBadge status={data.decision} label={data.decision.replace(/_/g, ' ')} />}
        meta={
          <>
            <Labelled label="incident">
              <MonoValue muted>{incidentId}</MonoValue>
            </Labelled>
            <Labelled label="basis">
              <MonoValue>{data.basis}</MonoValue>
            </Labelled>
            {data.seed !== null && (
              <Labelled label="seed">
                <MonoValue>{data.seed}</MonoValue>
              </Labelled>
            )}
          </>
        }
        footer={
          <div className="flex flex-col gap-2">
            <p className="text-body text-fg-secondary">{data.not_a_forecast}</p>
            {data.blocking_reasons.length > 0 && (
              <ul className="flex flex-col gap-1">
                {data.blocking_reasons.map((entry) => (
                  <li key={entry} className="flex items-start gap-1.5 text-body text-state-warn">
                    <AlertTriangle
                      size={12}
                      strokeWidth={1.5}
                      className="mt-1 shrink-0"
                      aria-hidden
                    />
                    {entry}
                  </li>
                ))}
              </ul>
            )}
          </div>
        }
      />

      <Panel
        title="Candidates"
        actions={
          <Toolbar>
            {differing.size > 0 && (
              <span className="text-caption uppercase text-accent">
                {differing.size} figure{differing.size === 1 ? '' : 's'} differ
              </span>
            )}
            <MonoValue muted className="text-caption">
              {data.candidates.length}
            </MonoValue>
          </Toolbar>
        }
      >
        <TableFrame caption="Candidate recovery plans compared against the same recorded evidence. No candidate is ranked; the column order is the order the endpoint returned them in.">
          <thead>
            <tr className="border-b border-border-subtle">
              <th scope="col" className="px-3 py-2 text-left text-label uppercase text-fg-muted">
                Figure
              </th>
              {data.candidates.map((row, index) => (
                <th
                  key={columnKey(row, index)}
                  scope="col"
                  className="px-3 py-2 text-left text-label uppercase text-fg-secondary"
                >
                  <span className="block">{row.variant_key}</span>
                  <span className="block text-caption font-normal normal-case text-fg-muted">
                    {row.generator ?? 'unknown'}
                    {row.prompt_version ? ` · ${row.prompt_version}` : ''}
                  </span>
                  <span
                    className={clsx(
                      'block font-normal normal-case',
                      row.admissible ? 'text-state-ok' : 'text-state-warn',
                    )}
                  >
                    {row.admissible ? 'admissible' : 'not admissible'}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {FIELDS.map((field) => {
              const differs = differing.has(String(field.key));
              return (
                <tr
                  key={String(field.key)}
                  className={clsx('border-b border-border-subtle', differs && 'bg-accent-subtle')}
                >
                  <th scope="row" className="px-3 py-1.5 text-left font-normal text-fg-secondary">
                    {field.label}
                    {differs && (
                      <span className="ml-2 text-label uppercase text-accent">differs</span>
                    )}
                    {field.hint && (
                      <span className="block text-label text-fg-muted">{field.hint}</span>
                    )}
                  </th>
                  {data.candidates.map((row, index) => (
                    <td key={columnKey(row, index)} className="px-3 py-1.5">
                      <Metric
                        value={display(row[field.key])}
                        derivation={candidateDerivation(row, data.basis, data.seed)}
                      />
                    </td>
                  ))}
                </tr>
              );
            })}
            <tr className="border-b border-border-subtle">
              <th scope="row" className="px-3 py-1.5 text-left font-normal text-fg-secondary">
                Blocking checks
                <span className="block text-label text-fg-muted">
                  Shown, not hidden: a refused candidate is still an option considered
                </span>
              </th>
              {data.candidates.map((row, index) => (
                <td key={columnKey(row, index)} className="px-3 py-1.5">
                  {row.blocking_checks.length === 0 ? (
                    <span className="text-fg-muted">none</span>
                  ) : (
                    <ul className="flex flex-col gap-0.5">
                      {row.blocking_checks.map((check) => (
                        <li key={check} className="text-state-warn">
                          {check.replace(/_/g, ' ')}
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row" className="px-3 py-1.5 text-left font-normal text-fg-secondary">
                Rationale
              </th>
              {data.candidates.map((row, index) => (
                <td key={columnKey(row, index)} className="px-3 py-1.5 text-fg-secondary">
                  {row.rationale ?? <Absent label="no rationale recorded" />}
                </td>
              ))}
            </tr>
          </tbody>
        </TableFrame>
      </Panel>

      <Panel title="Selection">
        <PanelBody gap="loose">
          <p className="text-body text-fg-secondary">
            A selection is attributed and immutable. A different choice later needs a new plan, not
            a re-selection — the same rule the operator decision record already follows.
          </p>

          {/*
           * The identical block lived here and in the approval queue, down to `maxLength={2000}` and
           * the `aria-invalid` expression. A recorded decision is only as good as its reason, so the
           * field that captures it is one component.
           */}
          <ReasonField
            id="plan-selection-reason"
            value={reason}
            onChange={setReason}
            disabled={!api.canWrite}
            placeholder="Why this plan, in the operator's words"
            hint="Recorded against the selection and shown in the audit trail. It cannot be edited afterwards."
          />

          {failure && (
            <Notice tone="crit" alert divider="none" className="rounded border px-2">
              {failure}
            </Notice>
          )}

          <div className="flex flex-wrap gap-2">
            {(plans.data?.plans ?? []).map((plan) => {
              const isSelected = plan.id === selectedId;
              const chosenElsewhere = selectedId !== null && !isSelected;
              const label = plan.variant_key ?? `plan ${plan.id}`;
              return (
                <Button
                  key={plan.id}
                  size="md"
                  variant={isSelected ? 'primary' : 'secondary'}
                  disabled={!canSelect || isSelected || chosenElsewhere || select.isPending}
                  disabledReason={
                    !api.canWrite
                      ? 'Fixtures are being served. Point the console at the live API to record a selection.'
                      : isSelected
                        ? 'This plan is already the selection of record.'
                        : chosenElsewhere
                          ? 'A plan has already been selected for this incident. A different choice needs a new plan.'
                          : reason.trim().length === 0
                            ? 'Give a reason first: a selection is recorded with its justification.'
                            : undefined
                  }
                  onClick={() => {
                    setPending(plan.id);
                    select.mutate({ planId: plan.id, why: reason.trim() });
                  }}
                >
                  {isSelected ? 'Selected' : 'Select'}
                  {/* The variant key is a contract value, so it is never case-transformed. */}
                  <span className="font-mono normal-case">{label}</span>
                  {pending === plan.id && select.isPending && ' …'}
                </Button>
              );
            })}
          </div>

          {selectedId !== null && (
            <p className="text-body text-fg-secondary">
              Recorded by{' '}
              <MonoValue>
                {plans.data?.plans.find((plan) => plan.id === selectedId)?.selected_by ?? 'unknown'}
              </MonoValue>
              . Further selection is refused with a conflict.
            </p>
          )}
        </PanelBody>
        {!api.canWrite && (
          <Notice tone="muted">
            Fixture mode: write affordances are disabled, because a synthesised response would put a
            state change on screen that never happened.
          </Notice>
        )}
      </Panel>
    </div>
  );
}

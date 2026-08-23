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
import { clsx } from 'clsx';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';

import { api, ApiError } from '@/api/client';
import type { CandidateComparisonRow } from '@/api/types';
import { Metric } from '@/components/ui/Metric';
import { candidateDerivation } from '@/components/ui/derivation';
import { EmptyState, ErrorState, LoadingState, MonoValue, Panel } from '@/components/ui/primitives';

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

function display(value: unknown): string | number | null {
  if (value === null || value === undefined) return null;
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (Array.isArray(value)) return value.length === 0 ? '—' : value.join(', ');
  if (typeof value === 'number' || typeof value === 'string') return value;
  return null;
}

function preplanningResolution(error: unknown): string | null {
  if (
    !(error instanceof ApiError) ||
    error.status !== 404 ||
    error.code !== 'ENTITY_NOT_FOUND' ||
    typeof error.details.resolution !== 'string'
  ) {
    return null;
  }
  return error.details.resolution;
}

export function PlanComparison() {
  const { incidentId = '' } = useParams<{ incidentId: string }>();
  const queryClient = useQueryClient();
  const [reason, setReason] = useState('');
  const [pending, setPending] = useState<number | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const comparison = useQuery({
    queryKey: ['plan-comparison', incidentId],
    queryFn: () => api.planComparison(incidentId),
    enabled: Boolean(incidentId),
  });

  const plans = useQuery({
    queryKey: ['plans', incidentId],
    queryFn: () => api.plans(incidentId),
    enabled: Boolean(incidentId),
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
  const comparisonResolution = preplanningResolution(comparison.error);
  const plansResolution = preplanningResolution(plans.error);
  const genericError = [comparison.error, plans.error].find(
    (error) => error && !preplanningResolution(error),
  );
  const queryError = genericError ?? comparison.error ?? plans.error;
  if (queryError) {
    const resolution = genericError ? null : (comparisonResolution ?? plansResolution);

    if (resolution) {
      return <EmptyState title="No candidates to compare yet" description={resolution} />;
    }

    return (
      <ErrorState
        code={queryError instanceof ApiError ? queryError.code : 'UNAVAILABLE'}
        message={
          queryError instanceof ApiError
            ? queryError.message
            : 'The plan endpoints did not respond.'
        }
        correlationId={queryError instanceof ApiError ? queryError.correlationId : null}
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
      {/* The boundary, rendered verbatim from the response rather than written here. */}
      <Panel title="Basis">
        <div className="flex flex-col gap-2 px-3 py-2">
          <p className="text-body text-fg-secondary">{data.not_a_forecast}</p>
          {/*
            The LABEL is uppercased, never the value. `basis` is a contract literal and `decision`
            is a gate enum: CSS-transforming either misrepresents what the API returned, which is
            the same defect that once rendered the policy pack label as "MOCA".
          */}
          <div className="flex flex-wrap items-center gap-3 text-label text-fg-muted">
            <span>
              <span className="uppercase">basis</span> <MonoValue>{data.basis}</MonoValue>
            </span>
            {data.seed !== null && (
              <span>
                <span className="uppercase">seed</span> <MonoValue>{data.seed}</MonoValue>
              </span>
            )}
            <span>
              <span className="uppercase">gate</span>{' '}
              <MonoValue>{data.decision.replace(/_/g, ' ')}</MonoValue>
            </span>
          </div>
          {data.blocking_reasons.length > 0 && (
            <ul className="flex flex-col gap-1 text-body text-state-warn">
              {data.blocking_reasons.map((entry) => (
                <li key={entry}>{entry}</li>
              ))}
            </ul>
          )}
        </div>
      </Panel>

      <Panel title={`Candidates (${data.candidates.length})`}>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-body">
            <caption className="sr-only">
              Candidate recovery plans compared against the same recorded evidence. No candidate is
              ranked.
            </caption>
            <thead>
              <tr className="border-b border-border-subtle">
                <th scope="col" className="px-3 py-2 text-left text-label uppercase text-fg-muted">
                  Figure
                </th>
                {data.candidates.map((row) => (
                  <th
                    key={row.candidate_id}
                    scope="col"
                    className="px-3 py-2 text-left text-label uppercase text-fg-secondary"
                  >
                    <span className="block">{row.variant_key}</span>
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
                    {data.candidates.map((row) => (
                      <td key={row.candidate_id} className="px-3 py-1.5">
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
                {data.candidates.map((row) => (
                  <td key={row.candidate_id} className="px-3 py-1.5">
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
                {data.candidates.map((row) => (
                  <td key={row.candidate_id} className="px-3 py-1.5 text-fg-secondary">
                    {row.rationale ?? '—'}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Selection">
        <div className="flex flex-col gap-3 px-3 py-3">
          <p className="text-body text-fg-secondary">
            A selection is attributed and immutable. A different choice later needs a new plan, not
            a re-selection — the same rule the operator decision record already follows.
          </p>

          <label className="flex flex-col gap-1">
            <span className="text-label uppercase text-fg-muted">Reason (required)</span>
            <input
              type="text"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              maxLength={2000}
              aria-invalid={reason.trim().length === 0}
              className="rounded border border-border-subtle bg-inset px-2 py-1.5 text-body text-fg-primary"
              placeholder="Why this plan, in the operator's words"
            />
          </label>

          {failure && (
            <p role="alert" className="text-body text-state-crit">
              {failure}
            </p>
          )}

          <div className="flex flex-wrap gap-2">
            {(plans.data?.plans ?? []).map((plan) => {
              const isSelected = plan.id === selectedId;
              const chosenElsewhere = selectedId !== null && !isSelected;
              return (
                <button
                  key={plan.id}
                  type="button"
                  disabled={!canSelect || isSelected || chosenElsewhere || select.isPending}
                  onClick={() => {
                    setPending(plan.id);
                    select.mutate({ planId: plan.id, why: reason.trim() });
                  }}
                  className={clsx(
                    'rounded border px-3 py-1.5 text-body',
                    isSelected
                      ? 'border-state-ok text-state-ok'
                      : 'border-border-subtle text-fg-primary hover:border-accent disabled:text-fg-muted',
                  )}
                >
                  {isSelected ? 'Selected: ' : 'Select '}
                  {plan.variant_key ?? `plan ${plan.id}`}
                  {pending === plan.id && select.isPending && ' …'}
                </button>
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
          {!api.canWrite && (
            <p className="text-body text-fg-muted">
              Fixture mode: write affordances are disabled, because a synthesised response would put
              a state change on screen that never happened.
            </p>
          )}
        </div>
      </Panel>
    </div>
  );
}

/**
 * Policy and citation — `/policy/:incidentId`. docs/27 screen 5.
 *
 * Also hosts what-if variant 1 under P2-D2: the cause re-evaluation the policy engine already
 * returns. Zero-write by construction — it is a GET — deterministic, and it renders the rules
 * engine's own `formula_used` verbatim. Nothing here is computed client-side.
 *
 * Owner: Stream D.
 */

import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { AlertTriangle } from 'lucide-react';
import { clsx } from 'clsx';

import { api, ApiError } from '@/api/client';
import type { Entitlement } from '@/api/types';
import {
  EmptyState,
  ErrorState,
  LoadingState,
  MonoValue,
  Panel,
  StateBadge,
} from '@/components/ui/primitives';
import { PackStandingBanner } from './PackStandingView';
import { packStanding, SOURCE_INTEGRITY_COPY, summariseApplicability } from './packStanding';

type CauseView = 'recorded' | 'alternative';

function EntitlementRow({ entitlement }: { entitlement: Entitlement }) {
  const notOwed = entitlement.outcome === 'not_owed';
  return (
    <li className="border-b border-border-subtle px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-body text-fg">{entitlement.type.replace(/_/g, ' ')}</span>
        {/* "Not owed" is a RESULT, displayed as one — never an absence or an empty cell. */}
        {notOwed ? (
          <StateBadge status="skipped" label="not owed" />
        ) : (
          <StateBadge status="approved" label={entitlement.outcome} />
        )}
        {entitlement.amount_inr !== undefined && (
          <MonoValue className={notOwed ? 'text-fg-muted' : 'text-fg'}>
            {entitlement.currency ?? 'INR'} {entitlement.amount_inr}
          </MonoValue>
        )}
        {entitlement.cash === false && (
          <span className="text-caption uppercase text-fg-muted">non-cash</span>
        )}
      </div>

      <p className="mt-1 text-caption text-fg-secondary">{entitlement.explanation}</p>

      <dl className="mt-1.5 flex flex-col gap-1">
        <Field label="rules fired">
          {entitlement.rules_fired.map((rule) => (
            <MonoValue key={rule} muted className="mr-2 text-caption">
              {rule}
            </MonoValue>
          ))}
        </Field>
        <Field label="clauses">
          {entitlement.source_clause_refs.map((ref) => (
            <MonoValue key={ref} muted className="mr-2 text-caption">
              {ref}
            </MonoValue>
          ))}
        </Field>
        {entitlement.reason_codes && entitlement.reason_codes.length > 0 && (
          <Field label="reason codes">
            {entitlement.reason_codes.map((code) => (
              <MonoValue key={code} muted className="mr-2 text-caption">
                {code}
              </MonoValue>
            ))}
          </Field>
        )}
        {entitlement.input_facts && (
          <Field label="input facts">
            <MonoValue muted className="break-all text-caption">
              {JSON.stringify(entitlement.input_facts)}
            </MonoValue>
          </Field>
        )}
        {entitlement.options && entitlement.options.length > 0 && (
          <Field label="options">
            {entitlement.options.map((option) => (
              <MonoValue key={option} muted className="mr-2 text-caption">
                {option}
              </MonoValue>
            ))}
          </Field>
        )}
      </dl>
    </li>
  );
}

/**
 * A cited value beside its label.
 *
 * The label is uppercased; the value never is. A regulation's name, a document title and a hex digest
 * are quotations, and a CSS transform on any of them misreports what the contract returned.
 */
function Cited({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-caption uppercase text-fg-muted">{label}</span>
      {children}
    </span>
  );
}

/**
 * A contract value, or a named absence when it published none.
 *
 * Several fields on the pack contract are nullable, and rendering `null` leaves a label sitting over
 * nothing — which reads as a broken screen rather than as "the contract did not supply this". Naming
 * the absence is the same rule `Metric` enforces for every figure.
 */
function Recorded({
  value,
  mono,
  absentTitle,
}: {
  value: string | null | undefined;
  mono?: boolean;
  absentTitle?: string;
}) {
  if (value === null || value === undefined || value.trim() === '') {
    return (
      <span
        className="text-caption text-fg-muted"
        title={absentTitle ?? 'Not published by this endpoint. An absent value, not an empty one.'}
      >
        not recorded
      </span>
    );
  }
  return mono ? (
    <MonoValue muted>{value}</MonoValue>
  ) : (
    <span className="text-caption text-fg-secondary">{value}</span>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      <dt className="w-[92px] shrink-0 text-caption uppercase text-fg-muted">{label}</dt>
      <dd className="min-w-0 flex-1">{children}</dd>
    </div>
  );
}

export function PolicyScreen() {
  const { incidentId = '' } = useParams();
  const [view, setView] = useState<CauseView>('recorded');

  const policyQuery = useQuery({
    queryKey: ['policy', incidentId],
    queryFn: () => api.policy(incidentId),
    enabled: incidentId.length > 0,
  });

  if (policyQuery.isLoading) {
    return (
      <Panel title="Policy">
        <div className="h-[420px]">
          <LoadingState label="Loading policy evaluation" />
        </div>
      </Panel>
    );
  }

  if (policyQuery.error || !policyQuery.data) {
    const error = policyQuery.error instanceof ApiError ? policyQuery.error : null;
    return (
      <ErrorState
        code={error?.code ?? 'INTERNAL_ERROR'}
        message={error?.message ?? `Could not load the policy evaluation for ${incidentId}.`}
        correlationId={error?.correlationId ?? null}
        onRetry={() => void policyQuery.refetch()}
      />
    );
  }

  const policy = policyQuery.data;
  const comparison = policy.cause_comparison;
  const alternative = (comparison?.['alternative'] ?? null) as Record<string, unknown> | null;
  const comparisonEnabled = Boolean(comparison?.['enabled']) && alternative !== null;
  /*
   * The pack's standing, derived once from the fields the contract published. Nothing on this screen
   * recomputes it, and the shell chip renders the same module.
   */
  const standing = packStanding(policy.pack);
  const applicability = summariseApplicability(policy.applicability);
  const missingFacts = applicability.missingFacts;

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <Panel>
        {/* One standing, one derivation, shared with the shell chip. */}
        <PackStandingBanner
          uiLabel={policy.pack.ui_label}
          standing={standing}
          packId={policy.pack.id}
          packVersion={policy.pack.version}
        />
        {/*
          Provenance, rendered through `Cited` so `uppercase` lands on the LABEL and never on the
          value. It previously sat on the wrapper, which case-transformed everything inside it: the
          authority's name, the document's title and — worst — the hex `pack_hash`, so the citation on
          screen did not match the digest that was recorded. Same defect class as rendering "MoCA" as
          "MOCA", and the reason this row is a component rather than five hand-built spans.
        */}
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 px-3 py-2">
          <Cited label="mode">
            <MonoValue muted>{policy.policy_mode}</MonoValue>
          </Cited>
          <Cited label="authority">
            <Recorded value={policy.pack.authority} />
          </Cited>
          <Cited label="document">
            <Recorded value={policy.pack.document} />
          </Cited>
          <Cited label="pack hash">
            <Recorded value={policy.pack.pack_hash} mono />
          </Cited>
          {/*
            `source_hash` is the digest the pack records, passed through verbatim: for the charter
            pack that is the `PENDING_ARCHIVAL` sentinel, and `null` is reserved for a pack that
            records no digest at all. An earlier comment here claimed the endpoint always published
            `null`; `api/policy.py` passes `LoadedPack.source_content_sha256` through and a backend
            e2e test locks that, so both states are real and they are reported differently.
            Rendering the raw value left the label standing over an empty space when it *is* null, so
            an absent digest looked like a rendering fault. It is named instead — the same rule
            `Metric` applies to every absent figure.
          */}
          <Cited label="source hash">
            <Recorded
              value={policy.pack.source_hash}
              mono
              absentTitle="No archived source digest is published for this pack. An absent value, not an empty one."
            />
          </Cited>
        </div>
        {/*
          Source-document integrity, reported from the digest the contract published. The gate that
          refuses a verified load on a sentinel, a missing file or a mismatched hash is Stream B's
          (docs/38 G3); this only states which of the four cases the recorded value falls in, so a
          reader is not left to recognise `PENDING_ARCHIVAL` on sight. Anything short of `archived`
          is warn-toned, including a recorded value that cannot be the documented SHA-256 — the state
          that matters in verified mode, where a real digest is what is expected.
        */}
        <p
          className={clsx(
            'flex items-start gap-2 border-t border-border-subtle px-3 py-1.5 text-caption',
            standing.sourceIntegrity === 'archived' ? 'text-fg-muted' : 'text-state-warn',
          )}
        >
          {standing.sourceIntegrity !== 'archived' && (
            <AlertTriangle size={12} strokeWidth={1.5} className="mt-0.5 shrink-0" aria-hidden />
          )}
          <span>{SOURCE_INTEGRITY_COPY[standing.sourceIntegrity]}</span>
        </p>
      </Panel>

      {/*
        The applicability tri-state. `undetermined` is the contract's own third answer and this screen
        used to drop it, reading only `missing_facts` — so an undetermined row that listed no missing
        fact rendered as nothing at all. Each state is now counted and named.
      */}
      {(applicability.hasOpenQuestion || missingFacts.length > 0) && (
        <Panel>
          <div className="flex flex-col gap-1 px-3 py-2">
            <p className="flex items-start gap-2 text-caption text-state-warn">
              <AlertTriangle size={12} strokeWidth={1.5} className="mt-0.5 shrink-0" aria-hidden />
              <span>
                {applicability.hasOpenQuestion ? (
                  <>
                    Applicability is not settled for{' '}
                    <MonoValue className="text-state-warn">
                      {applicability.undetermined + applicability.unknown}
                    </MonoValue>{' '}
                    of <MonoValue muted>{applicability.total}</MonoValue> assessed pack
                    {applicability.total === 1 ? '' : 's'}, so no authoritative figure follows from
                    those rows.
                  </>
                ) : (
                  <>
                    Required facts are missing, so the result is{' '}
                    <MonoValue className="text-state-warn">needs_human</MonoValue> rather than a
                    guessed number.
                  </>
                )}
              </span>
            </p>
            <dl className="flex flex-wrap gap-x-4 gap-y-1 text-caption">
              {(
                [
                  ['applicable', applicability.applicable],
                  ['not applicable', applicability.notApplicable],
                  ['undetermined', applicability.undetermined],
                  ['status not published', applicability.unknown],
                ] as const
              ).map(([label, count]) => (
                <div key={label} className="flex items-baseline gap-1.5">
                  <dt className="uppercase text-fg-muted">{label}</dt>
                  <dd>
                    <MonoValue muted>{count}</MonoValue>
                  </dd>
                </div>
              ))}
            </dl>
            {missingFacts.length > 0 && (
              <p className="text-caption text-fg-muted">Missing facts: {missingFacts.join(', ')}</p>
            )}
          </div>
        </Panel>
      )}

      <div className="grid min-h-0 grid-cols-[minmax(0,1fr)_380px] gap-3">
        <Panel
          title="Entitlements"
          actions={
            comparisonEnabled && (
              <div role="radiogroup" aria-label="Cause" className="flex items-center gap-1">
                {(
                  [
                    ['recorded', 'Recorded cause'],
                    ['alternative', 'Re-evaluated cause'],
                  ] as const
                ).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    role="radio"
                    aria-checked={view === value}
                    onClick={() => setView(value)}
                    className={clsx(
                      'rounded-sm border px-2 py-0.5 text-label uppercase',
                      'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                      view === value
                        ? 'border-accent-border bg-accent-subtle text-accent'
                        : 'border-border-subtle text-fg-muted',
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )
          }
        >
          {view === 'recorded' ? (
            policy.entitlements.length === 0 ? (
              <EmptyState
                title="No entitlements evaluated"
                description="The rules engine returned no entitlement rows for this event."
              />
            ) : (
              <ul aria-live="polite">
                {policy.entitlements.map((entitlement) => (
                  <EntitlementRow key={entitlement.type} entitlement={entitlement} />
                ))}
              </ul>
            )
          ) : (
            <div className="px-3 py-2" aria-live="polite">
              {/* Zero-write re-evaluation: the engine's own alternative, rendered verbatim. */}
              <div className="mb-2 flex items-center gap-2">
                <StateBadge status="scheduled" label="re-evaluated" />
                <span className="text-caption text-fg-muted">
                  {String(comparison?.['description'] ?? 'recomputed under a different cause')}
                </span>
              </div>
              <dl className="flex flex-col gap-1">
                {Object.entries(alternative ?? {}).map(([key, value]) => (
                  <Field key={key} label={key.replace(/_/g, ' ')}>
                    {Array.isArray(value) ? (
                      value.map((item) => (
                        <MonoValue key={String(item)} muted className="mr-2 text-caption">
                          {String(item)}
                        </MonoValue>
                      ))
                    ) : (
                      <MonoValue className="break-all">{String(value)}</MonoValue>
                    )}
                  </Field>
                ))}
              </dl>
              <p className="mt-2 text-caption text-fg-muted">
                A bounded re-evaluation of recorded facts under a different cause. Nothing was
                written, no state changed, and no outcome is predicted.
              </p>
            </div>
          )}
        </Panel>

        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          <Panel title="Cause assessment">
            <dl className="flex flex-col gap-1 px-3 py-2">
              {Object.entries(policy.cause_assessment).map(([key, value]) => (
                <Field key={key} label={key.replace(/_/g, ' ')}>
                  {Array.isArray(value) ? (
                    value.map((item) => (
                      <MonoValue key={String(item)} muted className="mr-2 break-all text-caption">
                        {String(item)}
                      </MonoValue>
                    ))
                  ) : (
                    <span className="text-caption text-fg-secondary">{String(value)}</span>
                  )}
                </Field>
              ))}
            </dl>
          </Panel>

          {policy.excluded_rules.length > 0 && (
            <Panel title="Excluded rules">
              <ul className="flex flex-col gap-2 px-3 py-2">
                {policy.excluded_rules.map((rule) => (
                  <li key={rule.rule_key} className="flex flex-col gap-0.5">
                    <span className="flex items-center gap-1.5">
                      <MonoValue>{rule.rule_key}</MonoValue>
                      <StateBadge status="skipped" label={rule.status.replace(/_/g, ' ')} />
                    </span>
                    <span className="text-caption text-fg-muted">{rule.reason}</span>
                    <span className="text-caption text-fg-muted">
                      evaluated: <MonoValue muted>{String(rule.evaluated)}</MonoValue>
                    </span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          <Panel title="Disclaimer">
            <p className="px-3 py-2 text-caption text-fg-muted">{policy.disclaimer}</p>
          </Panel>
        </div>
      </div>
    </div>
  );
}

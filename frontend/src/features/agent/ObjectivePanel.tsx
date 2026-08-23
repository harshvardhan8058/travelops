/**
 * What the agent is working on — assembled from declared facts, not from a model's own account.
 *
 * There is no goal, objective or intent field anywhere in the backend. That absence is the whole
 * design of this panel. What the system genuinely declares is: the trigger that opened the
 * incident, the severity it was opened at, the state spine it is driving along, and the plan it
 * proposed with the rationale recorded beside it. Composed, those answer "what is it trying to
 * accomplish" without any of it being narration.
 *
 * The alternative — a sentence beginning "I am attempting to…" — would be invented text attributed
 * to a system that produced none, on a console whose entire claim is that every word on it came
 * from a record. So the panel states its own construction instead, and names the field that would
 * carry a real objective if one existed.
 *
 * The generator token is rendered verbatim and never uppercased: `fallback-playbook` and
 * `groq:llama-3.3-70b` are the difference between a deterministic plan and a model-authored one,
 * and CSS-transforming a contract value misrepresents what the API returned.
 *
 * Owner: Stream D.
 */

import { AlertTriangle } from 'lucide-react';

import { MonoValue, Panel, StateBadge, StateRail } from '@/components/ui/primitives';
import type { IncidentDetail, SystemMode } from '@/api/types';
import { NotPublished } from './NotPublished';
import type { ResolvedLedger } from './steps';

/** What the ledger is describing, said plainly. */
const BASIS_COPY: Record<ResolvedLedger['basis'], string> = {
  plan_of_record:
    'The plan that carries the recorded evaluations and actions — the one that actually ran.',
  declared_plan:
    'The plan this incident declares. Nothing has been evaluated or executed against it yet.',
  recorded_evidence_only:
    'Rebuilt from the recorded evaluations and actions, because no published plan contains those tasks. Step order and target references are unavailable on this basis.',
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span className="flex items-baseline gap-2">
      <span className="text-label uppercase text-fg-muted">{label}</span>
      {children}
    </span>
  );
}

export function ObjectivePanel({
  incident,
  mode,
  ledger,
}: {
  incident: IncidentDetail;
  mode: SystemMode | undefined;
  ledger: ResolvedLedger | null;
}) {
  const plan = incident.plan;

  return (
    <Panel
      title="Objective"
      actions={
        <span className="text-caption text-fg-muted">
          composed from declared facts; no endpoint returns an agent-authored goal
        </span>
      }
    >
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-2 px-3 py-2">
        <Field label="incident">
          <MonoValue>{incident.reference}</MonoValue>
        </Field>
        <Field label="flight">
          <MonoValue muted>{incident.flight.flight_number}</MonoValue>
          {incident.flight.route && (
            <MonoValue muted className="text-caption">
              {incident.flight.route}
            </MonoValue>
          )}
        </Field>
        <Field label="trigger">
          <MonoValue muted>{incident.trigger_type}</MonoValue>
        </Field>
        <Field label="severity">
          <StateBadge status={incident.severity} />
        </Field>
        <Field label="state">
          <StateBadge status={incident.state} />
        </Field>
        {incident.group_reference && (
          <Field label="network scope">
            <MonoValue muted>{incident.group_reference}</MonoValue>
          </Field>
        )}
        <Field label="reasoning mode">
          {/* The producer of record, verbatim. `fixture` and `off` are not the same claim. */}
          <MonoValue muted>{mode?.llm_mode ?? 'not recorded'}</MonoValue>
        </Field>
      </div>

      {incident.state_rail.length > 0 && (
        <div className="border-t border-border-subtle px-3 py-2">
          <StateRail rail={incident.state_rail} current={incident.state} />
        </div>
      )}

      <div className="grid gap-x-5 gap-y-2 border-t border-border-subtle px-3 py-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="flex flex-col gap-1">
          <h3 className="text-label uppercase text-fg-muted">Plan of record</h3>
          {plan && ledger ? (
            <>
              <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <Field label="produced by">
                  {/* Verbatim: the token is the difference between a deterministic and a model plan. */}
                  <MonoValue>{ledger.generator ?? plan.generator}</MonoValue>
                </Field>
                {ledger.variantKey && (
                  <Field label="variant">
                    <MonoValue muted>{ledger.variantKey}</MonoValue>
                  </Field>
                )}
                <Field label="prompt">
                  <MonoValue muted className="text-caption">
                    {plan.prompt_version ?? 'not recorded'}
                  </MonoValue>
                </Field>
                <Field label="steps">
                  <MonoValue muted>{ledger.tasks.length}</MonoValue>
                </Field>
              </span>
              {/* The rationale as recorded. Never summarised, never rewritten. */}
              {(ledger.rationale ?? plan.rationale) && (
                <p className="text-body text-fg-secondary">{ledger.rationale ?? plan.rationale}</p>
              )}
              <p className="text-caption text-fg-muted">{BASIS_COPY[ledger.basis]}</p>
              {/*
               * The trap this console had to be built around: proposing candidates inserts newer
               * plan rows, and the incident contract then advertises the newest as `plan`. A
               * reviewer must see that the headline plan there is a proposal nothing authorised,
               * rather than wonder why the ledger and that field disagree.
               */}
              {ledger.supersededProposal && (
                <p className="flex items-start gap-1.5 text-caption text-state-warn">
                  <AlertTriangle
                    size={12}
                    strokeWidth={1.5}
                    className="mt-0.5 shrink-0"
                    aria-hidden
                  />
                  <span>
                    The incident contract currently advertises plan{' '}
                    <MonoValue className="text-caption">
                      {ledger.supersededProposal.planId}
                    </MonoValue>
                    {ledger.supersededProposal.variantKey ? (
                      <>
                        {' '}
                        (
                        <MonoValue className="text-caption">
                          {ledger.supersededProposal.variantKey}
                        </MonoValue>
                        )
                      </>
                    ) : null}{' '}
                    as its plan. It is a proposed candidate that nothing has evaluated or executed,
                    so this ledger describes the plan above instead.
                  </span>
                </p>
              )}
            </>
          ) : (
            <p className="text-caption text-fg-muted">
              No plan has been proposed for this incident, so nothing has been declared beyond the
              trigger above.
            </p>
          )}
        </div>

        <div className="flex flex-col gap-1">
          <h3 className="text-label uppercase text-fg-muted">Not published by any contract</h3>
          <NotPublished
            items={[
              {
                capability: 'agent-authored objective',
                wouldCarry: 'no goal, objective or intent field exists',
                reason:
                  'Nothing in the schema stores a stated aim, so the objective above is composed from the trigger, severity, state spine and declared plan rather than quoted.',
              },
              {
                capability: 'private reasoning trace',
                wouldCarry: 'plan.raw_response',
                reason:
                  'The column exists, is written as null, and is on no response model — so no chain of thought can reach this console even in principle. What is shown instead is the recorded rationale, the gate reasons and the timeline.',
              },
            ]}
          />
        </div>
      </div>
    </Panel>
  );
}

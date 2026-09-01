/**
 * Assurance panel — the six deterministic checks for one task, and the approve/reject
 * control when the gate says a human must decide.
 *
 * This replaces the "92% confidence" badge that mentor review rejected. What makes it
 * convincing is not the styling, it is that every row shows a verifiable fact and the
 * semantics that judged it: `docs/18-decision-assurance-gate.md`.
 *
 * Three rules this component exists to enforce:
 *
 *   1. ALL SIX CHECKS, ALWAYS, IN CHECK_ORDER. A check missing from the payload renders as
 *      "not returned" rather than vanishing, because a panel showing five rows looks
 *      complete and is not.
 *   2. CONFIG VERSION AND HASH REMAIN AVAILABLE IN AUDIT DETAILS. A replay must be able to prove
 *      which semantics applied when the decision was made without pushing the decision form down.
 *   3. PASS/WARN/FAIL AS ICON AND WORD AND COLOUR. Never colour alone.
 *
 * The case worth staring at is `notify_passengers`: all six checks PASS and it still blocks,
 * because the action is high risk. That is the entire point of the gate, so the panel states
 * it in words rather than leaving a reviewer to infer it from a badge.
 *
 * Shared deliberately: the approval queue at /assurance renders the same panel, so an
 * operator sees identical information wherever they meet a blocked action.
 *
 * Owner: Stream D.
 */

import { useState } from 'react';
import clsx from 'clsx';
import { AlertTriangle, PlayCircle, ShieldCheck, ShieldAlert } from 'lucide-react';

import { CHECK_ORDER } from '@/api/types';
import type {
  ActionRecord,
  AssuranceEvaluation,
  CheckName,
  CheckResult,
  HumanDecision,
  PlanTaskRow,
} from '@/api/types';
import { refusalFor } from '@/features/incident/refusal';
import { deriveAuthorizationState, type AuthorizationView } from './authorizationState';
import {
  CheckStateBadge,
  EmptyState,
  MonoValue,
  Panel,
  StateBadge,
  WhyPopover,
} from '@/components/ui/primitives';
import { Button } from '@/components/ui/composition';
import { checkDerivation, decisionDerivation } from '@/components/ui/derivation';
import {
  Button,
  DefinitionList,
  DefinitionRow,
  Notice,
  ReasonField,
} from '@/components/ui/composition';

const CHECK_LABEL: Record<CheckName, string> = {
  evidence_complete: 'Evidence completeness',
  sources_fresh: 'Source freshness',
  entities_valid: 'Entity validation',
  policy_compliant: 'Policy compliance',
  no_conflicts: 'Conflict detection',
  action_risk: 'Action risk tier',
};

function CheckRow({
  name,
  check,
  evaluation,
}: {
  name: CheckName;
  check?: CheckResult;
  evaluation: AssuranceEvaluation;
}) {
  const isBlocking = evaluation.blocking.includes(name);

  return (
    <li
      className={clsxRow(isBlocking)}
      // Blocking rows are the answer to "why did it stop?", so they are marked in the DOM
      // as well as visually.
      data-blocking={isBlocking || undefined}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="min-w-0 text-body text-fg">
          {check ? (
            <WhyPopover derivation={checkDerivation(check, evaluation)}>
              <span>{CHECK_LABEL[name]}</span>
            </WhyPopover>
          ) : (
            CHECK_LABEL[name]
          )}
        </span>
        {check ? (
          <CheckStateBadge state={check.state} />
        ) : (
          <StateBadge status="pending" label="not returned" />
        )}
      </div>

      {/*
       * This line renders only when it adds something the badge does not. `OK` beside a PASS
       * badge is the same fact twice, and six duplicated lines pushed the approve/reject
       * control below the fold at 1920x1080 — caught on the projector screenshot, not in
       * review. Nothing is lost: the WhyPopover on the check name shows the reason code
       * verbatim for every check, passing or not.
       */}
      {check && (informative(check) || isBlocking) && (
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
          {check.reason_code !== 'OK' && (
            <MonoValue muted className="text-caption">
              {check.reason_code}
            </MonoValue>
          )}
          {check.tier && <StateBadge status={`tier_${check.tier}`} label={`tier ${check.tier}`} />}
          {isBlocking && <StateBadge status="blocked" label="blocking" />}
        </div>
      )}

      {!check && (
        <p className="mt-1 text-caption text-fg-muted">
          This check was not present in the evaluation payload. Six checks are contractual, so an
          absent one is a gap, not a pass.
        </p>
      )}

      {check?.reason && <p className="mt-1 text-caption text-fg-secondary">{check.reason}</p>}

      {check?.evidence_refs && check.evidence_refs.length > 0 && (
        <ul className="mt-1 flex flex-col gap-0.5">
          {check.evidence_refs.map((ref) => (
            <li key={ref}>
              <MonoValue muted className="break-all text-caption">
                {ref}
              </MonoValue>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

/** True when the check carries something beyond "it passed": a code, a tier, or a reason. */
function informative(check: CheckResult): boolean {
  return check.reason_code !== 'OK' || Boolean(check.tier) || Boolean(check.reason);
}

function clsxRow(isBlocking: boolean): string {
  return [
    'border-b border-border-subtle px-3 py-2',
    // A 2px left edge, not a fill: the row stays readable and the marker survives a projector.
    isBlocking
      ? 'border-l-2 border-l-state-crit bg-state-crit-bg'
      : 'border-l-2 border-l-transparent',
  ].join(' ');
}

/**
 * The approve/reject control. The reason is mandatory because an approval without a stated
 * reason is not an audit record, and `human_decision.reason` is NOT NULL in the schema.
 *
 * Submitting writes a new immutable decision. The original evaluation is never mutated, and
 * the panel says so — that sentence is the difference between an audit trail and a form.
 */
/**
 * Where the action stands between authorised and done, and the one control that closes that gap.
 *
 * The tone comes from the derivation rather than from this component, so a refusal cannot be
 * rendered as a success by a styling choice made here. The run control appears only when
 * `offersRun` is set, which the derivation restricts to the single authorised-and-un-run stage.
 */
function AuthorizationBand({
  authorization,
  onRun,
  isRunning,
  runBlockedReason,
}: {
  authorization: AuthorizationView;
  onRun?: () => void;
  isRunning: boolean;
  runBlockedReason?: string;
}) {
  const tone = AUTHORIZATION_TONE[authorization.tone];

  return (
    <div className={clsx('rounded-sm border px-2 py-1.5', tone.border, tone.bg)}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className={clsx('text-label uppercase', tone.text)}>{authorization.label}</span>
        {authorization.offersRun && onRun && (
          <Button
            size="sm"
            variant="primary"
            icon={PlayCircle}
            onClick={onRun}
            disabled={isRunning || Boolean(runBlockedReason)}
            disabledReason={runBlockedReason}
          >
            {isRunning ? 'Running…' : 'Run workflow'}
          </Button>
        )}
      </div>
      <p className={clsx('mt-1 text-body', tone.text)}>{authorization.detail}</p>
      {authorization.offersRun && (
        <p className="mt-1 text-caption text-fg-muted">
          Approving recorded a decision. It did not run anything: the workflow advances only when
          somebody asks it to.
        </p>
      )}
    </div>
  );
}

/**
 * Tone to token. Only pairings already proven against the WCAG probe are used, and the map is total
 * so a new tone fails the build rather than rendering unstyled.
 */
const AUTHORIZATION_TONE: Record<
  AuthorizationView['tone'],
  { border: string; bg: string; text: string }
> = {
  ok: { border: 'border-state-ok/30', bg: 'bg-state-ok-bg', text: 'text-state-ok' },
  warn: { border: 'border-state-warn/30', bg: 'bg-state-warn-bg', text: 'text-state-warn' },
  crit: { border: 'border-state-crit/30', bg: 'bg-state-crit-bg', text: 'text-state-crit' },
  info: { border: 'border-state-info/30', bg: 'bg-state-info-bg', text: 'text-state-info' },
  muted: { border: 'border-border-subtle', bg: 'bg-inset', text: 'text-fg-secondary' },
};

function ApprovalPanel({
  evaluation,
  task,
  action,
  decision,
  onSubmit,
  isSubmitting,
  submitError,
  canWrite,
  onRun,
  isRunning,
  runBlockedReason,
}: {
  evaluation: AssuranceEvaluation;
  /** The plan task, whose state is what actually moves when a run picks the decision up. */
  task?: PlanTaskRow;
  action?: ActionRecord;
  decision?: HumanDecision;
  onSubmit: (decision: 'approved' | 'rejected', reason: string) => void;
  isSubmitting: boolean;
  submitError?: string | null;
  canWrite: boolean;
  /** Advances the workflow. Recording a decision never does this; a person has to ask for it. */
  onRun?: () => void;
  isRunning?: boolean;
  runBlockedReason?: string;
}) {
  const [reason, setReason] = useState('');
  const [reasonMissing, setReasonMissing] = useState(false);

  if (decision) {
    const authorization = deriveAuthorizationState(evaluation, task, action, decision);

    return (
      <div className="border-t border-border-subtle px-3 py-2">
        {/*
          The handoff, stated where the operator just decided.
          Approving wrote an audit record and ran nothing. The control that runs it is at the top of
          this page and looks identical before and after a decision, so without this band the
          reasonable reading of the screen is that approving it did it.
        */}
        <AuthorizationBand
          authorization={authorization}
          onRun={onRun}
          isRunning={isRunning ?? false}
          runBlockedReason={runBlockedReason}
        />

        <div className="mt-2 flex items-center justify-between gap-2">
          <h3 className="text-label uppercase text-fg-muted">Human decision</h3>
          <StateBadge status={decision.decision} label={decision.decision} />
        </div>
        <dl className="mt-1.5 flex flex-col gap-1">
          <div className="flex gap-2">
            <dt className="w-[86px] shrink-0 text-caption uppercase text-fg-muted">actor</dt>
            <dd>
              <MonoValue>{decision.actor_id}</MonoValue>
            </dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-[86px] shrink-0 text-caption uppercase text-fg-muted">decided</dt>
            <dd>
              <MonoValue muted>{decision.decided_at.slice(0, 19).replace('T', ' ')}Z</MonoValue>
            </dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-[86px] shrink-0 text-caption uppercase text-fg-muted">reason</dt>
            <dd className="min-w-0 flex-1 text-body text-fg-secondary">{decision.reason}</dd>
          </div>
        </dl>
        <p className="mt-2 text-caption text-fg-muted">
          Written as a new immutable record against evaluation{' '}
          <MonoValue muted>{evaluation.id}</MonoValue>. The original evaluation is not modified.
        </p>
        {!decision.persisted && (
          <p className="mt-1.5 flex items-start gap-1.5 rounded-sm border border-state-warn/30 bg-state-warn-bg px-2 py-1.5 text-caption text-state-warn">
            <AlertTriangle size={12} strokeWidth={1.5} className="mt-0.5 shrink-0" aria-hidden />
            <span>
              Recorded in this browser session only. Fixtures are being served and no endpoint
              accepted the write, so no audit row exists. It will not appear in the Decision
              Timeline, which reads persisted records.
            </span>
          </p>
        )}
      </div>
    );
  }

  const submit = (verdict: 'approved' | 'rejected') => {
    if (reason.trim().length === 0) {
      setReasonMissing(true);
      return;
    }
    onSubmit(verdict, reason.trim());
  };

  return (
    <form
      className="border-b border-accent-border bg-accent-subtle px-3 py-3"
      onSubmit={(event) => event.preventDefault()}
    >
      <h3 className="text-label uppercase text-accent">Operator decision required</h3>
      <p className="mt-1 text-body text-fg-secondary">
        Approve authorises this action; reject records that it must not proceed. Either choice
        writes a new immutable decision against this evaluation.
      </p>

      <div className="mt-2.5">
        <ReasonField
          id={`reason-${evaluation.id}`}
          value={reason}
          onChange={(next) => {
            setReason(next);
            if (reasonMissing) setReasonMissing(false);
          }}
          placeholder="Why this action is authorised, or why it is refused"
          hint="Required. This justification is written to the immutable decision record."
          invalid={reasonMissing}
          error={reasonMissing ? 'A reason is required before you approve or reject.' : undefined}
        />
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          size="md"
          disabled={isSubmitting}
          disabledReason="A decision is already being submitted."
          onClick={() => submit('approved')}
        >
          {isSubmitting ? 'Submitting…' : 'Approve action'}
        </Button>
        <Button
          variant="danger"
          size="md"
          disabled={isSubmitting}
          disabledReason="A decision is already being submitted."
          onClick={() => submit('rejected')}
        >
          Reject action
        </Button>
      </div>

      {/*
       * Fixture mode still lets an operator go through the motions, because the demo path must
       * work with no backend — but it says up front that the record will not be persisted,
       * rather than letting someone discover that after the fact.
       */}
      {!canWrite && (
        <p className="mt-2 text-caption text-fg-muted">
          Fixtures are being served, so this decision will be held in this browser session only.
          Point the UI at the live API to write an audit record.
        </p>
      )}

      {submitError && (
        <Notice tone="crit" alert divider="none" className="mt-2 rounded border">
          {submitError}
        </Notice>
      )}
    </form>
  );
}

export function AssurancePanel({
  task,
  evaluation,
  configVersion,
  configHash,
  scopeReference,
  incidentReference,
  action,
  decision,
  onSubmitDecision,
  isSubmitting,
  submitError,
  canWrite,
  onRun,
  isRunning,
  runBlockedReason,
}: {
  task?: PlanTaskRow;
  evaluation?: AssuranceEvaluation;
  configVersion?: string;
  configHash?: string;
  /** `AssuranceResponse.incident_reference` — which incident these gate records belong to. */
  scopeReference?: string;
  /** The incident actually on screen, for the mismatch check. */
  incidentReference?: string;
  /** This task's action, when one exists. Explains a refusal the gate did not cause. */
  action?: ActionRecord;
  decision?: HumanDecision;
  onSubmitDecision: (
    assuranceId: number,
    decision: 'approved' | 'rejected',
    reason: string,
  ) => void;
  isSubmitting: boolean;
  submitError?: string | null;
  /** False while fixtures are served: no endpoint can take a decision. */
  canWrite: boolean;
  /**
   * Advances the workflow one run. Passed in rather than called here because the incident screen
   * owns the run mutation and its idempotency key — this panel must not become a second way to
   * execute, only a second place to *ask*.
   */
  onRun?: () => void;
  isRunning?: boolean;
  /** Why running is unavailable, if it is. Rendered on the control rather than hiding it. */
  runBlockedReason?: string;
}) {
  /*
   * `incident_reference` is consumed, not decorated. If the assurance payload ever describes a
   * different incident from the one on screen, an operator could approve an action belonging to
   * another flight — so it is stated loudly rather than rendered quietly.
   */
  const scopeMismatch =
    Boolean(scopeReference) && Boolean(incidentReference) && scopeReference !== incidentReference;

  const scopeBanner = scopeMismatch ? (
    <p
      role="alert"
      className="flex items-start gap-1.5 border-b border-state-crit/30 bg-state-crit-bg px-3 py-2 text-caption text-state-crit"
    >
      <ShieldAlert size={12} strokeWidth={1.5} className="mt-0.5 shrink-0" aria-hidden />
      <span>
        These gate records are scoped to{' '}
        <MonoValue className="text-state-crit">{scopeReference}</MonoValue>, but this screen is
        showing <MonoValue className="text-state-crit">{incidentReference}</MonoValue>. Do not act
        on them.
      </span>
    </p>
  ) : null;

  if (!task) {
    return (
      <Panel title="Assurance">
        <EmptyState
          title="No task selected"
          description="Select a task in the plan to see the six checks that judged it."
        />
      </Panel>
    );
  }

  /*
   * Honest when there is no gate record. Three distinct situations, and collapsing them into one
   * "nothing here" message would hide the only one that is a defect:
   *
   *   assurance_id null                  -> not evaluated yet. Normal.
   *   assurance_id set, evaluation absent -> the endpoint owes us a record. A gap, not a pass.
   *   task needs_human with no record     -> an approval UI would be a trap: there is nothing
   *                                          to approve, so it is deliberately not offered.
   */
  if (!evaluation) {
    const refusal = refusalFor(action?.reason);
    return (
      <Panel title="Assurance">
        {scopeBanner}
        <EmptyState
          title={task.assurance_id === null ? 'Not evaluated yet' : 'No evaluation returned'}
          description={
            task.assurance_id === null
              ? `Task ${task.task_order} (${task.action_type}) has not reached the gate, so no record exists. Nothing may execute without one.`
              : `Task ${task.task_order} references evaluation ${task.assurance_id}, but the assurance endpoint did not return it. Nothing may execute without its gate record, so this is a gap rather than a pass.`
          }
        />
        {task.state === 'needs_human' && (
          <p className="border-t border-border-subtle px-3 py-2 text-caption text-fg-muted">
            This task reads <MonoValue muted>needs_human</MonoValue>, but no gate record is
            available, so no approve or reject control is offered — approving something the gate has
            not evaluated is exactly what the gate exists to prevent.
            {refusal ? ` ${refusal.detail}` : ''}
          </p>
        )}
      </Panel>
    );
  }

  const byName = new Map(evaluation.checks.map((check) => [check.name, check]));
  const allPassed = CHECK_ORDER.every((name) => byName.get(name)?.state === 'PASS');
  const blockedOnRiskAlone =
    allPassed && evaluation.blocking.length > 0 && evaluation.decision === 'needs_human';

  return (
    <Panel
      title="Assurance"
      actions={
        <span className="flex items-center gap-1.5">
          <ShieldCheck size={12} strokeWidth={1.5} className="text-fg-muted" aria-hidden />
          <MonoValue muted className="text-caption">
            task {task.task_order}
          </MonoValue>
        </span>
      }
    >
      {scopeBanner}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-subtle bg-inset px-3 py-2">
        <div className="min-w-0">
          <p className="text-label uppercase text-fg-muted">Selected action</p>
          <MonoValue className="break-all">{evaluation.action_type}</MonoValue>
        </div>
        <StateBadge
          status={`tier_${evaluation.risk_tier}`}
          label={`${evaluation.risk_tier} risk`}
        />
      </div>

      <div className="flex items-center justify-between gap-2 border-b border-border-subtle px-3 py-2">
        <span className="text-label uppercase text-fg-muted">Gate decision</span>
        <WhyPopover derivation={decisionDerivation(evaluation)}>
          <StateBadge status={evaluation.decision} />
        </WhyPopover>
      </div>

      {/*
       * The whole argument for the gate, in one sentence, on screen. A judge should not need
       * the presenter to explain why a plan with six passing checks did not execute.
       */}
      {blockedOnRiskAlone && (
        <Notice tone="warn" divider="top">
          All six checks passed. This still requires a human because the action is{' '}
          <MonoValue className="text-state-warn">{evaluation.risk_tier}</MonoValue> risk:{' '}
          {evaluation.blocking.join(', ')}.
        </Notice>
      )}

      {!blockedOnRiskAlone && evaluation.blocking.length > 0 && (
        <Notice tone="crit" divider="top">
          <span className="font-medium">Blocking checks:</span>{' '}
          {evaluation.blocking.map((name, index) => {
            const check = byName.get(name);
            return (
              <span key={name}>
                {index > 0 ? '; ' : ''}
                <MonoValue className="text-state-crit">{name}</MonoValue>{' '}
                {check?.reason ?? check?.reason_code ?? 'not returned'}
              </span>
            );
          })}
        </Notice>
      )}

      {/*
       * The gate authorised this, but the task is still waiting — because EXECUTION refused, not
       * because a human is needed. Without this the panel looks self-contradictory: an
       * `execute_flagged` decision beside a `needs_human` task. No approval control is offered,
       * because approving it would change nothing.
       */}
      {evaluation.decision !== 'needs_human' &&
        task.state === 'needs_human' &&
        (() => {
          const refusal = refusalFor(action?.reason);
          return (
            <div className="border-b border-border-subtle bg-state-warn-bg px-3 py-2">
              <p className="flex items-start gap-1.5 text-caption text-state-warn">
                <AlertTriangle
                  size={12}
                  strokeWidth={1.5}
                  className="mt-0.5 shrink-0"
                  aria-hidden
                />
                <span>
                  The gate returned{' '}
                  <MonoValue className="text-state-warn">{evaluation.decision}</MonoValue>, so this
                  task was authorised. It is waiting because execution did not complete.
                </span>
              </p>
              {refusal ? (
                <p className="mt-1.5 text-caption text-fg-secondary">
                  <MonoValue muted>{refusal.code}</MonoValue> — {refusal.detail}
                </p>
              ) : (
                action?.reason && (
                  <p className="mt-1.5 text-caption text-fg-secondary">{action.reason}</p>
                )
              )}
            </div>
          );
        })()}

      {evaluation.decision === 'needs_human' &&
        (scopeMismatch ? (
          <Notice tone="crit" divider="top">
            Approve and reject controls are hidden because this evaluation belongs to a different
            incident scope.
          </Notice>
        ) : (
          <ApprovalPanel
            evaluation={evaluation}
            task={task}
            action={action}
            decision={decision}
            isSubmitting={isSubmitting}
            submitError={submitError}
            canWrite={canWrite}
            onSubmit={(verdict, reason) => onSubmitDecision(evaluation.id, verdict, reason)}
            onRun={onRun}
            isRunning={isRunning}
            runBlockedReason={runBlockedReason}
          />
        ))}

      <details className="min-w-0 border-t border-border-subtle">
        <summary className="cursor-pointer px-3 py-2 text-label uppercase text-fg-secondary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
          Audit details · six checks, evidence and record identifiers
        </summary>
        <div className="min-w-0 border-t border-border-subtle bg-inset">
          <div className="px-3 py-2.5">
            <DefinitionList width="lg">
              <DefinitionRow label="evaluation" width="lg">
                <MonoValue>{evaluation.id}</MonoValue>
              </DefinitionRow>
              <DefinitionRow label="config version" width="lg">
                <MonoValue muted className="break-all">
                  {evaluation.config_version ?? configVersion ?? 'not returned'}
                </MonoValue>
              </DefinitionRow>
              <DefinitionRow label="config hash" width="lg">
                <MonoValue muted className="break-all">
                  {evaluation.config_hash ?? configHash ?? 'not returned'}
                </MonoValue>
              </DefinitionRow>
              <DefinitionRow label="scope" width="lg">
                <MonoValue muted className="break-all">
                  {scopeReference ?? 'not returned'}
                </MonoValue>
              </DefinitionRow>
            </DefinitionList>
          </div>

          {evaluation.note && (
            <p className="border-t border-border-subtle px-3 py-2 text-caption text-fg-secondary">
              {evaluation.note}
            </p>
          )}

          {evaluation.warn_permitted_by_config && (
            <p className="border-t border-border-subtle px-3 py-2 text-caption text-fg-secondary">
              A warning was permitted for this action type by versioned config, producing{' '}
              <MonoValue muted>execute_flagged</MonoValue> rather than a block. There is no global
              soft-failure bypass.
            </p>
          )}

          <ol className="border-t border-border-subtle">
            {CHECK_ORDER.map((name) => (
              <CheckRow key={name} name={name} check={byName.get(name)} evaluation={evaluation} />
            ))}
          </ol>

          <div className="border-t border-border-subtle px-3 py-2">
            <h3 className="text-label uppercase text-fg-muted">Evidence</h3>
            {evaluation.evidence_refs.length > 0 ? (
              <ul className="mt-1 flex min-w-0 flex-col gap-0.5">
                {evaluation.evidence_refs.map((ref) => (
                  <li key={ref} className="min-w-0">
                    <MonoValue muted className="break-all text-caption">
                      {ref}
                    </MonoValue>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-caption text-fg-muted">
                No evaluation-level references returned.
              </p>
            )}
          </div>
        </div>
      </details>
    </Panel>
  );
}

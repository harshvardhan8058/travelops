/**
 * Plan-level assurance: the gate across a whole plan at once, as tasks × the six checks.
 *
 * Deliberately NOT a score. `docs/18` defines a fail-closed, ordered gate — a mean of six checks
 * would be a fiction, and a single number invites exactly the trust the gate exists to replace.
 * So this shows counts, and where the blocking is.
 *
 * D1 scopes plan-level assurance to the incident GROUP. This component is the per-incident matrix
 * the group view composes; the group view itself needs FE-8, since no endpoint returns a group's
 * incidents.
 *
 * Owner: Stream D.
 */

import { clsx } from 'clsx';

import { CHECK_ORDER } from '@/api/types';
import type { AssuranceEvaluation, CheckName, CheckState, PlanTaskRow } from '@/api/types';
import { EmptyState, MonoValue, Panel, StateBadge } from '@/components/ui/primitives';
import { Metric } from '@/components/ui/Metric';
import { planAssuranceDerivation, checkDerivation } from '@/components/ui/derivation';
import { CountBar } from '@/components/ui/Metric';

const CHECK_SHORT: Record<CheckName, string> = {
  evidence_complete: 'Evidence',
  sources_fresh: 'Freshness',
  entities_valid: 'Entities',
  policy_compliant: 'Policy',
  no_conflicts: 'Conflicts',
  action_risk: 'Risk tier',
};

const CELL_TONE: Record<CheckState, string> = {
  PASS: 'text-state-ok',
  WARN: 'text-state-warn',
  FAIL: 'text-state-crit',
};

const CELL_GLYPH: Record<CheckState, string> = { PASS: '✓', WARN: '!', FAIL: '✕' };

export function PlanAssuranceMatrix({
  tasks,
  evaluations,
  configVersion,
  configHash,
  onSelectTask,
  selectedTaskId,
}: {
  tasks: PlanTaskRow[];
  evaluations: AssuranceEvaluation[];
  configVersion?: string;
  configHash?: string;
  onSelectTask?: (taskId: number) => void;
  selectedTaskId?: number | null;
}) {
  if (tasks.length === 0) {
    return (
      <Panel title="Plan assurance">
        <EmptyState
          title="No tasks to judge"
          description="The gate evaluates tasks, so this fills in once the orchestrator has proposed a plan."
        />
      </Panel>
    );
  }

  const byTask = new Map(evaluations.map((evaluation) => [evaluation.plan_task_id, evaluation]));

  // Partitions over returned enum fields only — the sole aggregate this UI is allowed to compute.
  const decisionCounts = [
    { label: 'execute', tone: 'ok' as const },
    { label: 'execute_flagged', tone: 'warn' as const },
    { label: 'needs_human', tone: 'crit' as const },
  ].map((entry) => ({
    label: entry.label.replace(/_/g, ' '),
    tone: entry.tone,
    count: evaluations.filter((evaluation) => evaluation.decision === entry.label).length,
  }));

  const blockingCounts = CHECK_ORDER.map((name) => ({
    name,
    count: evaluations.filter((evaluation) => evaluation.blocking.includes(name)).length,
  })).filter((entry) => entry.count > 0);

  // A group judged under two config hashes is a fact a reviewer must see, not a detail to smooth.
  const hashes = [...new Set(evaluations.map((evaluation) => evaluation.config_hash))];

  return (
    <Panel
      title="Plan assurance"
      actions={
        <span className="flex items-center gap-2 text-caption text-fg-muted">
          <span>
            config <MonoValue muted>{configVersion ?? hashes[0] ?? 'unavailable'}</MonoValue>
          </span>
          <Metric
            value={`${evaluations.length} evaluated`}
            derivation={planAssuranceDerivation(
              evaluations,
              configVersion ?? 'unavailable',
              configHash ?? 'unavailable',
            )}
          />
        </span>
      }
    >
      <div className="border-b border-border-subtle px-3 py-2">
        <CountBar segments={decisionCounts} total={evaluations.length} />
      </div>

      {hashes.length > 1 && (
        <p
          role="alert"
          className="border-b border-state-warn/30 bg-state-warn-bg px-3 py-1.5 text-caption text-state-warn"
        >
          These evaluations were judged under {hashes.length} different config hashes:{' '}
          {hashes.join(', ')}. A replay must use each evaluation's own semantics.
        </p>
      )}

      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-body">
          <caption className="sr-only">
            Tasks by assurance check. Each cell is PASS, WARN or FAIL.
          </caption>
          <thead>
            <tr className="border-b border-border-subtle bg-inset text-label uppercase text-fg-muted">
              <th scope="col" className="px-3 py-1.5 text-left font-medium">
                Task
              </th>
              {CHECK_ORDER.map((name) => (
                <th key={name} scope="col" className="px-2 py-1.5 text-center font-medium">
                  {CHECK_SHORT[name]}
                </th>
              ))}
              <th scope="col" className="px-3 py-1.5 text-left font-medium">
                Decision
              </th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => {
              const evaluation = byTask.get(task.id);
              const selected = task.id === selectedTaskId;
              return (
                <tr
                  key={task.id}
                  className={clsx(
                    'h-row border-b border-l-2 border-border-subtle',
                    selected ? 'border-l-accent bg-raised' : 'border-l-transparent',
                  )}
                >
                  <th scope="row" className="px-3 text-left font-normal">
                    <button
                      type="button"
                      onClick={() => onSelectTask?.(task.id)}
                      aria-current={selected || undefined}
                      className="flex items-center gap-2 py-1 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      <MonoValue muted>{task.task_order}</MonoValue>
                      <MonoValue className={clsx(selected && 'text-accent')}>
                        {task.action_type}
                      </MonoValue>
                    </button>
                  </th>

                  {CHECK_ORDER.map((name) => {
                    const check = evaluation?.checks.find((c) => c.name === name);
                    const blocking = evaluation?.blocking.includes(name);
                    return (
                      <td
                        key={name}
                        className={clsx('px-2 text-center', blocking && 'bg-state-crit-bg')}
                        aria-label={`${task.action_type}, ${CHECK_SHORT[name]}, ${check?.state ?? 'not returned'}`}
                      >
                        {check ? (
                          /* Icon and word and colour — never colour alone. */
                          <span
                            className={clsx('font-mono text-mono-sm', CELL_TONE[check.state])}
                            title={`${check.state} · ${check.reason_code}`}
                          >
                            {CELL_GLYPH[check.state]} {check.state}
                          </span>
                        ) : (
                          <span className="text-caption text-fg-muted" title="not returned">
                            —
                          </span>
                        )}
                      </td>
                    );
                  })}

                  <td className="px-3">
                    {evaluation ? (
                      <StateBadge status={evaluation.decision} />
                    ) : (
                      <StateBadge status="pending" label="not evaluated" />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {blockingCounts.length > 0 && (
        <div className="border-t border-border-subtle px-3 py-2">
          <h3 className="mb-1 text-label uppercase text-fg-muted">What is blocking</h3>
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {blockingCounts.map(({ name, count }) => {
              const sample = evaluations.find((evaluation) => evaluation.blocking.includes(name));
              const check = sample?.checks.find((c) => c.name === name);
              return (
                <li key={name} className="flex items-center gap-1.5 text-caption">
                  <span className="text-fg-secondary">{CHECK_SHORT[name]}</span>
                  {check && sample ? (
                    <Metric value={count} derivation={checkDerivation(check, sample)} />
                  ) : (
                    <MonoValue muted>{count}</MonoValue>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <p className="border-t border-border-subtle px-3 py-1.5 text-caption text-fg-muted">
        Counts only. No aggregate score: a fail-closed, ordered gate has no meaningful average.
      </p>
    </Panel>
  );
}

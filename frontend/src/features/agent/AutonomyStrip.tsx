/**
 * What runs unattended, what stops for a person, and which tools actually fired.
 *
 * This is the first thing an operator should be able to read from across a room, because it is the
 * question autonomy raises: *how much of this is the system deciding by itself?* The partition is
 * the gate's, recorded per task; the console only counts each arm.
 *
 * The tool list is an **observed** ledger, not a capability catalogue. The backend keeps a service
 * registry in `dispatch.py` and publishes it on no endpoint, so this reports what has been invoked
 * and says plainly that it cannot list what could be.
 *
 * Owner: Stream D.
 */

import { CountBar, Metric, MetricTile } from '@/components/ui/Metric';
import { autonomyDerivation, countDerivation } from '@/components/ui/derivation';
import { EmptyState, MonoValue, Panel } from '@/components/ui/primitives';
import { NotPublished } from './NotPublished';
import { autonomySplit, toolUse, troubledSteps, type AgentStep } from './steps';

export function AutonomyPanel({
  steps,
  configVersion,
  configHash,
}: {
  steps: AgentStep[];
  configVersion: string;
  configHash: string;
}) {
  const split = autonomySplit(steps);
  const trouble = troubledSteps(steps);

  const segments = [
    { label: 'runs unattended', tone: 'ok' as const, count: split.execute.length },
    { label: 'runs and is flagged', tone: 'warn' as const, count: split.executeFlagged.length },
    { label: 'stops for a person', tone: 'crit' as const, count: split.needsHuman.length },
    { label: 'not evaluated', tone: 'neutral' as const, count: split.unevaluated.length },
  ];

  return (
    <Panel title="Autonomy">
      <div className="px-3 py-2">
        <CountBar segments={segments} total={steps.length} />
      </div>
      <div className="flex flex-wrap items-start gap-2 border-t border-border-subtle px-3 py-2">
        <MetricTile
          label="Runs unattended"
          value={split.execute.length}
          derivation={autonomyDerivation({
            label: 'Runs unattended',
            decision: 'execute',
            count: split.execute.length,
            totalSteps: steps.length,
            configVersion,
            configHash,
          })}
        />
        <MetricTile
          label="Flagged"
          value={split.executeFlagged.length}
          derivation={autonomyDerivation({
            label: 'Flagged',
            decision: 'execute_flagged',
            count: split.executeFlagged.length,
            totalSteps: steps.length,
            configVersion,
            configHash,
          })}
          footnote="executes, recorded as notable"
        />
        <MetricTile
          label="Needs a person"
          value={split.needsHuman.length}
          derivation={autonomyDerivation({
            label: 'Needs a person',
            decision: 'needs_human',
            count: split.needsHuman.length,
            totalSteps: steps.length,
            configVersion,
            configHash,
          })}
        />
        <MetricTile
          label="Refused"
          value={trouble.refused.length}
          derivation={countDerivation('Refused calls', trouble.refused.length, {
            endpoint: 'GET /incidents/{ref}',
            field: 'actions[] where the record carries no provenance',
            note: 'A refusal means no service was registered to carry the task out, so nothing ran and nothing may be claimed. It is not a failure, and approving it would change nothing.',
          })}
        />
        <MetricTile
          label="Failed"
          value={trouble.failed.length}
          derivation={countDerivation('Failed or skipped calls', trouble.failed.length, {
            endpoint: 'GET /incidents/{ref}',
            field: 'actions[] with status failure or skipped',
            note: 'A service ran and did not succeed. Kept apart from a refusal, where nothing was attempted at all.',
          })}
        />
      </div>
    </Panel>
  );
}

export function ToolsPanel({ steps }: { steps: AgentStep[] }) {
  const used = toolUse(steps);

  return (
    <Panel
      title="Tools invoked"
      actions={<span className="text-caption text-fg-muted">observed, not a catalogue</span>}
    >
      {used.length === 0 ? (
        <EmptyState
          title="No tool has been invoked"
          description="No action record names a plan task for this incident, so nothing has been dispatched to a service yet."
        />
      ) : (
        <table className="w-full border-collapse text-body">
          <caption className="sr-only">
            Tools invoked for this incident, with the outcome of each invocation
          </caption>
          <thead>
            <tr className="border-b border-border-subtle bg-inset text-label uppercase text-fg-muted">
              <th scope="col" className="px-3 py-1.5 text-left font-medium">
                Tool
              </th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">
                Calls
              </th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">
                Succeeded
              </th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">
                Refused
              </th>
              <th scope="col" className="px-3 py-1.5 text-right font-medium">
                Failed
              </th>
            </tr>
          </thead>
          <tbody>
            {used.map((tool) => (
              <tr
                key={tool.actionType}
                className="h-row border-b border-border-subtle last:border-b-0"
              >
                <th scope="row" className="px-3 text-left font-normal">
                  <MonoValue>{tool.actionType}</MonoValue>
                </th>
                <td className="px-3 text-right">
                  <Metric
                    value={tool.invocations}
                    derivation={countDerivation(`${tool.actionType} calls`, tool.invocations, {
                      endpoint: 'GET /incidents/{ref}',
                      field: `actions[] with action_type ${tool.actionType}`,
                      note: 'One row per recorded dispatch. The same tool can be invoked by more than one planned task.',
                    })}
                  />
                </td>
                <td className="px-3 text-right">
                  <Metric
                    value={tool.succeeded}
                    derivation={countDerivation(`${tool.actionType} succeeded`, tool.succeeded, {
                      endpoint: 'GET /incidents/{ref}',
                      field: 'actions[] with status success',
                    })}
                  />
                </td>
                <td className="px-3 text-right">
                  <Metric
                    value={tool.refused}
                    derivation={countDerivation(`${tool.actionType} refused`, tool.refused, {
                      endpoint: 'GET /incidents/{ref}',
                      field: 'actions[] refused by the dispatcher',
                      note: 'No service was registered for this action, so execution was refused rather than reported as successful.',
                    })}
                  />
                </td>
                <td className="px-3 text-right">
                  <Metric
                    value={tool.failed}
                    derivation={countDerivation(`${tool.actionType} failed`, tool.failed, {
                      endpoint: 'GET /incidents/{ref}',
                      field: 'actions[] with status failure or skipped',
                    })}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="border-t border-border-subtle px-3 py-2">
        <NotPublished
          items={[
            {
              capability: 'available tools',
              wouldCarry: 'no route exposes SERVICE_REGISTRY',
              reason:
                'The dispatcher knows which services are registered, but nothing publishes that list, so this table can only report tools already seen to run.',
            },
            {
              capability: 'call duration and retries',
              wouldCarry: 'no field on ActionSummary or ActionDetailResponse',
              reason:
                'No latency, attempt count or timeout is recorded against an action, so none is shown and no timing may be inferred from the console.',
            },
          ]}
        />
      </div>
    </Panel>
  );
}

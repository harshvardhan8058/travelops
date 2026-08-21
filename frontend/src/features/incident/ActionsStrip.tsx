/**
 * Recovery workspace, bottom strip — Actions.
 *
 * The "what did it actually do in the world" answer. Every row names the actor, the result,
 * the cost, the assurance record that authorised it and the idempotency key that makes a
 * replay safe. An action with no assurance reference would be a side effect nobody approved,
 * so the reference is a column rather than a detail.
 *
 * Owner: Stream D.
 */

import { asProvenanceKind } from '@/api/types';
import type { ActionRecord, IncidentDetail } from '@/api/types';
import {
  EmptyState,
  MonoValue,
  Panel,
  ProvenanceDot,
  StateBadge,
  WhyPopover,
} from '@/components/ui/primitives';
import { actionDerivation } from '@/components/ui/derivation';
import { refusalFor } from './refusal';

export function ActionsStrip({ incident }: { incident: IncidentDetail }) {
  const actions: ActionRecord[] = incident.actions;

  return (
    <Panel
      title="Actions executed"
      actions={
        <MonoValue muted className="text-caption">
          {actions.length}
        </MonoValue>
      }
    >
      {actions.length === 0 ? (
        <EmptyState
          title="Nothing executed yet"
          description="Actions appear here the moment the gate authorises one. An action can never exist without an assurance record."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-body">
            <thead>
              <tr className="border-b border-border-subtle bg-inset text-label uppercase text-fg-muted">
                <th className="px-3 py-1.5 text-left font-medium">Task</th>
                <th className="px-3 py-1.5 text-left font-medium">Action</th>
                <th className="px-3 py-1.5 text-left font-medium">Actor</th>
                <th className="px-3 py-1.5 text-left font-medium">Result</th>
                <th className="px-3 py-1.5 text-left font-medium">Reason</th>
                <th className="px-3 py-1.5 text-right font-medium">Cost</th>
                <th className="px-3 py-1.5 text-left font-medium">Assurance</th>
                <th className="px-3 py-1.5 text-left font-medium">Idempotency key</th>
                <th className="px-3 py-1.5 text-right font-medium">Executed</th>
                <th className="px-3 py-1.5 text-left font-medium">Src</th>
              </tr>
            </thead>
            <tbody>
              {actions.map((action) => (
                <tr key={action.id} className="h-row border-b border-border-subtle">
                  <td className="px-3">
                    <MonoValue muted>{action.plan_task_id}</MonoValue>
                  </td>
                  <td className="px-3">
                    {/* Present on the real API, absent from the committed fixture. */}
                    {action.action_type ? (
                      <MonoValue>{action.action_type}</MonoValue>
                    ) : (
                      <span
                        className="text-caption text-fg-muted"
                        title="not returned by this endpoint"
                      >
                        —
                      </span>
                    )}
                  </td>
                  <td className="px-3">
                    <MonoValue>{action.actor}</MonoValue>
                  </td>
                  <td className="px-3">
                    <WhyPopover derivation={actionDerivation(action, incident)}>
                      <StateBadge
                        status={action.status === 'success' ? 'succeeded' : action.status}
                      />
                    </WhyPopover>
                  </td>
                  {/*
                   * A refusal shows designed copy with its stable code beside it; the raw message
                   * stays available on hover so nothing is hidden.
                   */}
                  <td className="max-w-[380px] px-3 text-fg-secondary" title={action.reason}>
                    {refusalFor(action.reason) ? (
                      <span className="flex items-center gap-1.5">
                        <MonoValue muted className="text-caption">
                          {refusalFor(action.reason)?.code}
                        </MonoValue>
                        <span className="truncate text-state-warn">
                          {refusalFor(action.reason)?.headline}
                        </span>
                      </span>
                    ) : (
                      <span className="block truncate">{action.reason}</span>
                    )}
                  </td>
                  <td className="px-3 text-right">
                    {action.cost_inr === null ? (
                      <MonoValue muted>n/a</MonoValue>
                    ) : (
                      <MonoValue>INR {action.cost_inr}</MonoValue>
                    )}
                  </td>
                  <td className="px-3">
                    <MonoValue muted>{action.assurance_id}</MonoValue>
                  </td>
                  <td className="px-3">
                    <MonoValue muted className="text-caption">
                      {action.idempotency_key}
                    </MonoValue>
                  </td>
                  <td className="px-3 text-right">
                    <MonoValue muted>
                      {action.executed_at ? action.executed_at.slice(11, 19) : '—'}
                    </MonoValue>
                  </td>
                  <td className="px-3">
                    {(() => {
                      const kind = asProvenanceKind(action.provenance_kind);
                      if (kind) {
                        return <ProvenanceDot kind={kind} provider={action.actor} />;
                      }
                      return (
                        <span
                          className="text-caption text-fg-muted"
                          title={
                            action.provenance_kind
                              ? `unrecognised provenance kind: ${action.provenance_kind}`
                              : 'no provenance recorded for this action'
                          }
                        >
                          —
                        </span>
                      );
                    })()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

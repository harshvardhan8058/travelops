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
import { Absent, TableFrame, TableHead } from '@/components/ui/composition';
import { utcClock } from '@/components/ui/format';
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
        <TableFrame
          className="[&_table]:table-fixed [&_td]:break-words [&_th]:break-words"
          caption="Every action executed for this incident, each referencing the assurance evaluation that authorised it. An action cannot exist without one."
        >
          <TableHead
            columns={[
              { key: 'task', label: 'Task' },
              { key: 'action', label: 'Action' },
              { key: 'actor', label: 'Actor' },
              { key: 'result', label: 'Result' },
              { key: 'reason', label: 'Reason' },
              { key: 'cost', label: 'Cost', hint: 'INR', align: 'right' },
              { key: 'assurance', label: 'Assurance' },
              { key: 'idem', label: 'Idempotency key' },
              { key: 'executed', label: 'Executed', hint: 'UTC', align: 'right' },
              { key: 'src', label: 'Src' },
            ]}
          />
          <tbody>
            {actions.map((action) => (
              <tr key={action.id} className="h-row border-b border-border-subtle">
                <td className="px-3">
                  <MonoValue muted>{action.plan_task_id}</MonoValue>
                </td>
                <td className="px-3">
                  {/* Present on the real API, absent from the committed fixture. */}
                  {action.action_type ? (
                    <MonoValue className="break-all">{action.action_type}</MonoValue>
                  ) : (
                    <Absent
                      label="not returned"
                      title="This endpoint does not return an action type for this record."
                    />
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
                    <Absent
                      label="none"
                      title="No cost was recorded against this action. Absent, not zero."
                      className="justify-end"
                    />
                  ) : (
                    <MonoValue>{action.cost_inr}</MonoValue>
                  )}
                </td>
                <td className="px-3">
                  <MonoValue muted>{action.assurance_id}</MonoValue>
                </td>
                <td className="px-3">
                  <MonoValue muted className="break-all text-caption">
                    {action.idempotency_key}
                  </MonoValue>
                </td>
                <td className="px-3 text-right">
                  <MonoValue muted>{utcClock(action.executed_at) ?? '—'}</MonoValue>
                </td>
                <td className="px-3">
                  {(() => {
                    const kind = asProvenanceKind(action.provenance_kind);
                    if (kind) {
                      return <ProvenanceDot kind={kind} provider={action.actor} />;
                    }
                    return (
                      <Absent
                        label={action.provenance_kind ? 'unrecognised' : 'none'}
                        title={
                          action.provenance_kind
                            ? `unrecognised provenance kind: ${action.provenance_kind}`
                            : 'no provenance recorded for this action'
                        }
                      />
                    );
                  })()}
                </td>
              </tr>
            ))}
          </tbody>
        </TableFrame>
      )}
    </Panel>
  );
}

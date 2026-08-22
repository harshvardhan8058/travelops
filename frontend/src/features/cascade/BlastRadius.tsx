/**
 * Blast-radius explanation: how far the disruption reaches, and by what mechanism at each hop.
 *
 * This panel is the primary representation and the graph beside it is the enhancement. It reads
 * completely with the SVG switched off, which is both the accessibility position and the honest
 * one: the argument is the chain of joins, not the picture.
 *
 * Owner: Stream D.
 */

import { ChevronDown, ChevronRight, CircleSlash } from 'lucide-react';
import { clsx } from 'clsx';

import type { IncidentGroupDetail, PairingMechanism } from '@/api/types';
import { MonoValue, Panel, StateBadge } from '@/components/ui/primitives';
import { Metric } from '@/components/ui/Metric';
import {
  arrayLengthDerivation,
  pairingDerivation,
  terminalDerivation,
} from '@/components/ui/derivation';
import { buildRadius } from './radius';

export function BlastRadius({
  group,
  selectedHop,
  onSelectHop,
  selectedMechanism,
  onSelectMechanism,
}: {
  group: IncidentGroupDetail;
  selectedHop: number | null;
  onSelectHop: (hop: number | null) => void;
  selectedMechanism: PairingMechanism | null;
  onSelectMechanism: (mechanism: PairingMechanism | null) => void;
}) {
  const radius = buildRadius(group);

  return (
    <Panel title="Blast radius" className="flex min-h-0 flex-col overflow-hidden">
      <div className="border-b border-border-subtle px-3 py-2">
        <p className="text-body text-fg">
          <MonoValue>{radius.trigger.cause}</MonoValue> at{' '}
          <MonoValue>{radius.trigger.airport}</MonoValue> reaches{' '}
          {radius.hops.map((hop, index) => (
            <span key={hop.index}>
              {index > 0 && ' → '}
              <Metric
                value={hop.count}
                derivation={arrayLengthDerivation(hop.to, hop.count, {
                  endpoint: 'GET /incident-groups/{id}',
                  field: hop.countSource.replace('length of ', ''),
                  provenance: group.provenance,
                })}
              />{' '}
              {hop.to}
            </span>
          ))}
          .
        </p>
        {radius.summary && (
          /* The backend's own explanation, verbatim. */
          <p className="mt-1.5 text-caption text-fg-muted">{radius.summary}</p>
        )}
      </div>

      <ol className="min-h-0 flex-1 overflow-y-auto">
        {radius.hops.map((hop) => {
          const expanded = selectedHop === hop.index;
          return (
            <li
              key={hop.index}
              className={clsx(
                'border-b border-l-2 border-border-subtle',
                expanded ? 'border-l-accent bg-raised' : 'border-l-transparent',
              )}
            >
              <button
                type="button"
                onClick={() => onSelectHop(expanded ? null : hop.index)}
                aria-expanded={expanded}
                aria-current={expanded || undefined}
                className="flex w-full items-center gap-2 px-2 py-1.5 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                {expanded ? (
                  <ChevronDown size={14} strokeWidth={1.5} aria-hidden />
                ) : (
                  <ChevronRight size={14} strokeWidth={1.5} aria-hidden />
                )}
                <MonoValue muted className="w-8 shrink-0">
                  hop {hop.index}
                </MonoValue>
                <span className="min-w-0 flex-1 text-body text-fg">
                  {hop.from} → {hop.to}
                </span>
                <Metric
                  value={hop.count}
                  derivation={arrayLengthDerivation(hop.to, hop.count, {
                    endpoint: 'GET /incident-groups/{id}',
                    field: hop.countSource.replace('length of ', ''),
                    provenance: group.provenance,
                  })}
                />
              </button>

              {expanded && (
                <div className="border-t border-border-subtle px-3 py-2">
                  {hop.mechanismCounts.length > 0 && (
                    <dl className="mb-2 flex flex-col gap-1">
                      {hop.mechanismCounts.map(({ mechanism, count }) => {
                        const active = selectedMechanism === mechanism;
                        return (
                          <div key={mechanism} className="flex items-start gap-2">
                            <dt className="w-[112px] shrink-0">
                              <button
                                type="button"
                                onClick={() => onSelectMechanism(active ? null : mechanism)}
                                aria-pressed={active}
                                className={clsx(
                                  'rounded-sm border px-1.5 py-0.5 text-caption uppercase',
                                  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                                  active
                                    ? 'border-accent-border bg-accent-subtle text-accent'
                                    : 'border-border-subtle text-fg-secondary',
                                )}
                              >
                                {mechanism.replace(/_/g, ' ')}
                              </button>
                            </dt>
                            <dd className="min-w-0 flex-1 text-caption text-fg-muted">
                              <MonoValue muted>{count}</MonoValue>{' '}
                              {group.mechanism_legend?.[mechanism] ?? 'no legend text returned'}
                            </dd>
                          </div>
                        );
                      })}
                    </dl>
                  )}

                  <ul className="flex flex-col gap-1">
                    {hop.records
                      .filter((record) =>
                        selectedMechanism ? record.label.includes(selectedMechanism) : true,
                      )
                      .map((record) => (
                        <li key={record.id} className="flex flex-col gap-0.5">
                          <MonoValue>{record.label}</MonoValue>
                          {record.detail && (
                            <span className="text-caption text-fg-muted">{record.detail}</span>
                          )}
                        </li>
                      ))}
                  </ul>
                </div>
              )}
            </li>
          );
        })}
      </ol>

      {/*
       * Terminal counts. These are real numbers with no records behind them, and saying so is the
       * difference between this and a demo that draws 22 fictional connection nodes.
       */}
      <div className="border-t border-border-subtle bg-inset px-3 py-2">
        <h3 className="mb-1 text-label uppercase text-fg-muted">Counted, not traversable</h3>
        <ul className="flex flex-col gap-1.5">
          {radius.terminals.map((terminal) => (
            <li key={terminal.label} className="flex items-start gap-2">
              <CircleSlash
                size={12}
                strokeWidth={1.5}
                className="mt-0.5 shrink-0 text-fg-muted"
                aria-hidden
              />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5">
                  <span className="text-caption uppercase text-fg-secondary">{terminal.label}</span>
                  <Metric
                    value={terminal.count}
                    derivation={terminalDerivation(
                      terminal.label,
                      terminal.count,
                      terminal.countSource,
                      terminal.reason,
                    )}
                  />
                </span>
                <span className="block text-caption text-fg-muted">{terminal.reason}</span>
              </span>
            </li>
          ))}
        </ul>
      </div>

      {radius.unmatched.length > 0 && (
        <div className="border-t border-state-warn/30 bg-state-warn-bg px-3 py-2">
          <div className="flex items-center gap-2">
            <StateBadge status="degraded" label="unmatched" />
            <span className="text-caption text-state-warn">
              {radius.unmatched.length} pairing
              {radius.unmatched.length === 1 ? '' : 's'} name a source flight that is not in this
              group, so no edge could be drawn for them
            </span>
          </div>
          <ul className="mt-1 flex flex-col gap-0.5">
            {radius.unmatched.map((pairing) => (
              <li key={pairing.pairing_reference}>
                <MonoValue muted className="text-caption">
                  {pairing.pairing_reference} · source {pairing.source_flight}
                </MonoValue>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

/** The pairing table: the accessible primary view of hop 2, sortable and filterable. */
export function PairingTable({
  group,
  selectedMechanism,
  selectedFlight,
}: {
  group: IncidentGroupDetail;
  selectedMechanism: PairingMechanism | null;
  selectedFlight: string | null;
}) {
  const rows = group.crew_pairings.filter(
    (pairing) =>
      (selectedMechanism ? pairing.mechanism === selectedMechanism : true) &&
      (selectedFlight ? pairing.source_flight === selectedFlight : true),
  );

  return (
    <Panel
      title="Crew pairings"
      actions={
        <MonoValue muted className="text-caption">
          {rows.length} of {group.crew_pairings.length}
        </MonoValue>
      }
    >
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-body">
          <thead>
            <tr className="border-b border-border-subtle bg-inset text-label uppercase text-fg-muted">
              <th scope="col" className="px-3 py-1.5 text-left font-medium">
                Pairing
              </th>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">
                Base
              </th>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">
                Source flight
              </th>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">
                Leg
              </th>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">
                Mechanism
              </th>
              <th scope="col" className="px-3 py-1.5 text-left font-medium">
                At risk
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((pairing) => (
              <tr key={pairing.pairing_reference} className="border-b border-border-subtle">
                <td className="px-3 py-1.5">
                  <WhyPairing pairing={pairing} group={group} />
                </td>
                <td className="px-3 py-1.5">
                  <MonoValue muted>{pairing.base_icao}</MonoValue>
                </td>
                <td className="px-3 py-1.5">
                  <MonoValue>{pairing.source_flight}</MonoValue>
                </td>
                <td className="px-3 py-1.5">
                  <MonoValue muted>{pairing.affected_leg}</MonoValue>
                </td>
                <td className="px-3 py-1.5">
                  <span className="rounded-sm border border-border-subtle bg-inset px-1.5 py-0.5 text-caption uppercase text-fg-secondary">
                    {pairing.mechanism.replace(/_/g, ' ')}
                  </span>
                </td>
                <td className="px-3 py-1.5">
                  <StateBadge
                    status={pairing.at_risk ? 'at_risk' : 'pending'}
                    label={pairing.at_risk ? 'at risk' : 'not flagged'}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="border-t border-border-subtle px-3 py-2 text-caption text-fg-muted">
        Crew handling is coordination and display only. Duty-time legality is deliberately not
        modelled.
      </p>
    </Panel>
  );
}

function WhyPairing({
  pairing,
  group,
}: {
  pairing: IncidentGroupDetail['crew_pairings'][number];
  group: IncidentGroupDetail;
}) {
  return (
    <Metric
      value={pairing.pairing_reference}
      derivation={pairingDerivation(pairing, group.mechanism_legend, group.provenance)}
    />
  );
}

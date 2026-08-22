/**
 * Blast-radius explanation: how far the disruption reaches, and by what mechanism at each hop.
 *
 * This panel is the primary representation and the graph beside it is the enhancement. It reads
 * completely with the SVG switched off, which is both the accessibility position and the honest
 * one: the argument is the chain of joins, not the picture.
 *
 * Owner: Stream D.
 */

import { AlertTriangle, ChevronDown, ChevronRight } from 'lucide-react';
import { clsx } from 'clsx';

import type { IncidentGroupDetail, PairingMechanism } from '@/api/types';
import { MonoValue, Panel, StateBadge } from '@/components/ui/primitives';
import { Metric } from '@/components/ui/Metric';
import { arrayLengthDerivation, pairingDerivation } from '@/components/ui/derivation';
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
        {/* The server's own headline, verbatim, including its completeness caveat. Rendered as one
         * string on purpose: splitting the totals from the qualification invites a UI that shows
         * the first and drops the second. */}
        <p className="text-body text-fg">{radius.headline}</p>
        <p className="mt-1 flex items-center gap-1.5 text-caption text-fg-muted">
          <MonoValue muted>{radius.trigger.cause}</MonoValue> at{' '}
          <MonoValue muted>{radius.trigger.airport}</MonoValue>
          <span aria-hidden>·</span>
          snapshot <MonoValue muted>{radius.snapshotHash}</MonoValue>
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
                  {hop.from} {'->'} {hop.to}
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
                        const legend =
                          group.mechanism_legend?.[mechanism as PairingMechanism] ??
                          'no legend text returned';
                        return (
                          <div key={mechanism} className="flex items-start gap-2">
                            <dt className="w-[112px] shrink-0">
                              <button
                                type="button"
                                onClick={() =>
                                  onSelectMechanism(active ? null : (mechanism as PairingMechanism))
                                }
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
                              <MonoValue muted>{count}</MonoValue> {legend}
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
                          <span className="flex items-baseline gap-1.5">
                            <MonoValue>{record.label}</MonoValue>
                            {/* The recorded row this edge was read from. On screen rather than only
                             * in the payload: "every figure names its source" is the product's
                             * central claim, and an edge is a figure. */}
                            <MonoValue muted className="text-caption">
                              {record.derivedFrom}
                            </MonoValue>
                          </span>
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
       * The measured dimensions, each naming the service that measured it. A dimension whose
       * `is_complete` is false is a floor rather than a total, and it is labelled as one — that
       * label is the whole difference between an honest partial answer and an overstated one.
       *
       * There is deliberately no confidence figure here. Completeness is countable; confidence
       * would be a probability nothing in this system is calibrated to produce.
       */}
      <div className="border-t border-border-subtle bg-inset px-3 py-2">
        <div className="mb-1.5 flex items-center justify-between gap-2">
          <h3 className="text-label uppercase text-fg-muted">Measured dimensions</h3>
          <span className="flex items-center gap-1.5 text-caption text-fg-muted">
            assessed
            <MonoValue muted>{radius.completeness.ratio}</MonoValue>
            flights
          </span>
        </div>
        <ul className="flex flex-col gap-1.5">
          {radius.dimensions.map((dimension) => (
            <li key={dimension.key} className="flex items-start gap-2">
              <span className="min-w-0 flex-1">
                <span className="flex items-baseline gap-1.5">
                  <span className="text-caption uppercase text-fg-secondary">
                    {dimension.label}
                  </span>
                  <Metric
                    value={dimension.value}
                    derivation={arrayLengthDerivation(dimension.label, dimension.value, {
                      endpoint: 'GET /incident-groups/{id}',
                      field: `blast_radius.dimensions[${dimension.key}].value`,
                      provenance: group.provenance,
                    })}
                  />
                  <span className="text-caption text-fg-muted">{dimension.unit}</span>
                  {!dimension.is_complete && (
                    /* Not decoration: this value is a lower bound and must never read as a total. */
                    <span className="rounded-sm border border-state-warn/40 px-1 text-caption uppercase text-state-warn">
                      at least
                    </span>
                  )}
                </span>
                <span className="block text-caption text-fg-muted">
                  measured by <MonoValue muted>{dimension.measured_by}</MonoValue>
                  {dimension.note ? ` · ${dimension.note}` : ''}
                </span>
              </span>
            </li>
          ))}
        </ul>
      </div>

      {radius.gaps.length > 0 && (
        /* Named, countable gaps. A partial cascade must say what is missing, not just that
         * something is. */
        <div className="border-t border-state-warn/30 bg-state-warn-bg px-3 py-2">
          <div className="flex items-center gap-2">
            <AlertTriangle size={12} strokeWidth={1.5} className="text-state-warn" aria-hidden />
            <span className="text-label uppercase text-state-warn">Not yet known</span>
          </div>
          <ul className="mt-1 flex flex-col gap-0.5">
            {radius.gaps.map((gap) => (
              <li key={gap} className="text-caption text-state-warn">
                {gap}
              </li>
            ))}
          </ul>
        </div>
      )}

      {radius.unmatched.length > 0 && (
        <div className="border-t border-state-warn/30 bg-state-warn-bg px-3 py-2">
          <div className="flex items-center gap-2">
            <StateBadge status="degraded" label="unmatched" />
            <span className="text-caption text-state-warn">
              {radius.unmatched.length} rotation
              {radius.unmatched.length === 1 ? '' : 's'} appear in the crew table with no graph edge
              reaching them, so the picture understates the cascade by that much
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

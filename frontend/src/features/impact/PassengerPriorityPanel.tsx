/**
 * Recorded passenger priorities — the surface that makes "604 passengers" into an order.
 *
 * Lives beside the Impact Explorer's other tabs and closes the gap that screen's own Passengers
 * table names: that table can only show passengers reachable from one incident's connection
 * findings, because that is what `action.payload` carries. This one covers every passenger the
 * group assessed, because the server wrote a row per passenger.
 *
 * Reads `GET /incident-groups/{ref}/impacts`. It derives nothing: the bands, the counts and the
 * factor tallies are built server-side in `services/passenger_impact`, and this component formats
 * them. A ranking that would decide who is offered one of a short supply of rooms must be traceable
 * to a row and a ruleset hash, which a client-side sort could not be.
 *
 * Two deliberate refusals in the visual language:
 *
 *   1. **Priority bands do not use state colour.** Green, amber and red are reserved exclusively
 *      for operational state in this system. A `critical` band is a statement about how constrained
 *      a passenger is, not about whether a step failed, and painting it red would put two unrelated
 *      meanings on one hue on a projector. Bands use a single-hue accent ramp plus the band name.
 *   2. **An unestablished factor is never drawn as "no".** `unassessed_factors` names the factors
 *      whose inputs no service has produced. Rendering them as false would tell an operator that
 *      nobody needs rebooking, when the truth is that nobody has looked. They get their own block,
 *      above the cohorts, because that gap changes how the ranking should be read.
 *
 * Owner: Stream D.
 */

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { clsx } from 'clsx';

import { api, ApiError } from '@/api/client';
import type { GroupImpactResponse } from '@/api/types';
import { EmptyState, ErrorState, LoadingState, MonoValue, Panel } from '@/components/ui/primitives';
import { FilterChips, Metric } from '@/components/ui/Metric';
import { impactCohortDerivation, passengerImpactDerivation } from '@/components/ui/derivation';
import { useKeyboardList } from '@/hooks/useKeyboardList';

/**
 * Bands in the order the ruleset checks them, most constrained first.
 *
 * `ring` is an accent-ramp weight, not a status colour — see the module note. The ramp is
 * monotonic so the ordering is legible without reading the labels, and the labels are there anyway
 * because colour is never the only channel.
 */
const BAND_ORDER = ['critical', 'high', 'elevated', 'routine'] as const;

const BAND_STYLE: Record<string, string> = {
  critical: 'border-accent bg-accent-subtle text-accent',
  high: 'border-accent-border text-accent',
  elevated: 'border-border-strong text-fg-secondary',
  routine: 'border-border-subtle text-fg-muted',
};

/** How many passenger rows the table shows before the operator asks for more. */
const PAGE = 100;

function bandRank(band: string): number {
  const index = (BAND_ORDER as readonly string[]).indexOf(band);
  return index === -1 ? BAND_ORDER.length : index;
}

export function PassengerPriorityPanel({ groupRef }: { groupRef: string }) {
  const [band, setBand] = useState<string>('all');
  const [expanded, setExpanded] = useState<number | null>(null);

  const impactsQuery = useQuery({
    queryKey: ['group-impacts', groupRef],
    queryFn: () => api.groupImpacts(groupRef, PAGE),
  });

  const impacts = impactsQuery.data;

  const cohorts = useMemo(
    () => [...(impacts?.cohorts ?? [])].sort((a, b) => bandRank(a.band) - bandRank(b.band)),
    [impacts],
  );
  const passengers = useMemo(
    () =>
      (impacts?.passengers ?? []).filter(
        (passenger) => band === 'all' || passenger.priority_band === band,
      ),
    [impacts, band],
  );

  /** The cohort the band filter names, so the table can say when it is showing only part of it. */
  const selectedCohort = cohorts.find((cohort) => cohort.band === band) ?? null;

  const keyboard = useKeyboardList({
    count: passengers.length,
    onOpen: (index) => {
      const id = passengers[index]?.passenger_id ?? null;
      setExpanded((current) => (current === id ? null : id));
    },
  });

  if (impactsQuery.isLoading) {
    return (
      <Panel title="Recorded priorities">
        <div className="h-[220px]">
          <LoadingState label="Loading recorded priorities" />
        </div>
      </Panel>
    );
  }

  if (impactsQuery.error || !impacts) {
    const error = impactsQuery.error instanceof ApiError ? impactsQuery.error : null;
    return (
      <Panel title="Recorded priorities">
        <ErrorState
          code={error?.code ?? 'INTERNAL_ERROR'}
          message={error?.message ?? 'Could not load recorded passenger priorities.'}
          correlationId={error?.correlationId ?? null}
          onRetry={() => void impactsQuery.refetch()}
        />
      </Panel>
    );
  }

  if (impacts.passengers_assessed === 0) {
    return (
      <Panel title="Recorded priorities">
        {/* Not zeros. Zero passengers in zero bands reads, on a wall display, as "everyone is
         *  fine" — which is a different claim from "nothing has been assessed". */}
        <EmptyState title="No priorities recorded yet" description={impacts.note} />
        <UnassessedFactors impacts={impacts} />
      </Panel>
    );
  }

  return (
    <Panel
      title="Recorded priorities"
      actions={
        <span className="flex items-center gap-3 text-caption text-fg-muted">
          <span>
            ruleset <MonoValue muted>{impacts.ruleset_hash}</MonoValue>
          </span>
          {impacts.computed_at && (
            <span>
              recorded <MonoValue muted>{impacts.computed_at.slice(11, 19)}Z</MonoValue>
            </span>
          )}
          <MonoValue muted>{impacts.passengers_assessed} assessed</MonoValue>
        </span>
      }
    >
      <UnassessedFactors impacts={impacts} />

      {/*
       * Band tiles, not a stacked bar. `CountBar` paints every segment one hue, so four bands
       * would be four indistinguishable blocks — and the only way to tell them apart would be to
       * borrow the status ramp, which is reserved for operational state in this system. The tiles
       * carry the count, the index range and the factor tally, which is what an operator reads
       * off this panel anyway.
       */}
      <div className="border-b border-border-subtle px-3 py-2">
        <ul className="flex flex-wrap gap-2">
          {cohorts.map((cohort) => (
            <li key={cohort.band}>
              <span
                className={clsx(
                  'flex min-w-[150px] flex-col gap-0.5 rounded border px-2 py-1.5',
                  BAND_STYLE[cohort.band] ?? 'border-border-subtle text-fg-muted',
                )}
              >
                <span className="text-caption uppercase">{cohort.band}</span>
                <span className="text-subtitle text-fg">
                  <Metric
                    value={cohort.passenger_count}
                    derivation={impactCohortDerivation(
                      cohort,
                      impacts.passengers_assessed,
                      impacts.ruleset_hash,
                    )}
                  />
                </span>
                <span className="text-caption text-fg-muted">
                  index {cohort.lowest_index}
                  {cohort.lowest_index === cohort.highest_index
                    ? ''
                    : ` to ${cohort.highest_index}`}
                </span>
                {Object.keys(cohort.factor_counts).length > 0 && (
                  <span className="text-caption text-fg-muted">
                    {Object.entries(cohort.factor_counts)
                      .map(([factor, count]) => `${factor.replace(/_/g, ' ')} ${count}`)
                      .join(' · ')}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b border-border-subtle px-3 py-2">
        <FilterChips
          label="Band"
          value={band}
          onChange={setBand}
          options={[
            { value: 'all', label: 'All bands', count: impacts.passengers_assessed },
            ...cohorts.map((cohort) => ({
              value: cohort.band,
              label: cohort.band,
              count: cohort.passenger_count,
            })),
          ]}
        />
      </div>

      {selectedCohort && passengers.length < selectedCohort.passenger_count && (
        /*
         * The chip says 567 and the table can only show the ones inside the returned page. Said
         * plainly, because a filter that silently shows a subset of what its own count promises is
         * how an operator concludes a band is smaller than it is.
         */
        <p className="border-b border-border-subtle bg-inset px-3 py-1.5 text-caption text-fg-muted">
          <MonoValue muted>{passengers.length}</MonoValue> of this band's{' '}
          <MonoValue muted>{selectedCohort.passenger_count}</MonoValue> passengers fall inside the{' '}
          <MonoValue muted>{impacts.returned}</MonoValue> highest-priority records this page
          requested. The rest are recorded; they are further down the ranking.
        </p>
      )}

      {passengers.length === 0 ? (
        <EmptyState
          title="No passenger in this band is in the returned page"
          description={`The list is capped at ${impacts.returned} of ${impacts.passengers_assessed} assessed, highest priority first. A band further down the ranking falls outside it.`}
        />
      ) : (
        <div className="max-h-[420px] overflow-y-auto">
          <table className="w-full border-collapse text-body">
            <caption className="sr-only">
              Recorded passenger priorities for {impacts.group_reference}, highest priority first
            </caption>
            <thead className="sticky top-0 bg-surface">
              <tr className="border-b border-border-subtle text-caption uppercase text-fg-muted">
                <th scope="col" className="px-3 py-1.5 text-right font-normal">
                  index
                </th>
                <th scope="col" className="px-2 py-1.5 text-left font-normal">
                  band
                </th>
                <th scope="col" className="px-2 py-1.5 text-left font-normal">
                  passenger
                </th>
                <th scope="col" className="px-2 py-1.5 text-left font-normal">
                  pnr
                </th>
                <th scope="col" className="px-2 py-1.5 text-left font-normal">
                  why
                </th>
              </tr>
            </thead>
            <tbody
              ref={keyboard.containerRef as React.RefObject<HTMLTableSectionElement>}
              onKeyDown={keyboard.onKeyDown}
            >
              {passengers.map((passenger, index) => {
                const open = expanded === passenger.passenger_id;
                return (
                  <tr
                    key={passenger.passenger_id}
                    className={clsx('border-b border-border-subtle align-top', open && 'bg-raised')}
                  >
                    <td className="px-3 py-1 text-right">
                      <Metric
                        value={passenger.priority_index}
                        derivation={passengerImpactDerivation(passenger)}
                      />
                    </td>
                    <td className="px-2 py-1">
                      <span
                        className={clsx(
                          'rounded-sm border px-1 py-0.5 text-caption uppercase',
                          BAND_STYLE[passenger.priority_band] ??
                            'border-border-subtle text-fg-muted',
                        )}
                      >
                        {passenger.priority_band}
                      </span>
                    </td>
                    <td className="px-2 py-1">
                      <MonoValue muted>{passenger.passenger_reference}</MonoValue>
                    </td>
                    <td className="px-2 py-1">
                      <MonoValue>{passenger.pnr}</MonoValue>
                    </td>
                    <td className="px-2 py-1">
                      <button
                        type="button"
                        {...keyboard.itemProps(index)}
                        aria-expanded={open}
                        onClick={() => setExpanded(open ? null : passenger.passenger_id)}
                        className="rounded-sm text-left text-caption text-fg-secondary underline decoration-dotted underline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                      >
                        {passenger.factors.length === 0
                          ? 'base priority: nothing recorded raises it'
                          : passenger.factors
                              .map(
                                (factor) => `${factor.factor.replace(/_/g, ' ')} +${factor.weight}`,
                              )
                              .join(', ')}
                      </button>
                      {open && (
                        <dl className="mt-1 flex flex-col gap-0.5">
                          {passenger.factors.map((factor) => (
                            <div key={factor.factor} className="flex gap-2">
                              <dt className="w-[168px] shrink-0 text-caption text-fg-muted">
                                {factor.factor.replace(/_/g, ' ')}
                              </dt>
                              <dd className="text-caption text-fg-secondary">
                                <MonoValue muted>+{factor.weight}</MonoValue> read from{' '}
                                <MonoValue muted>{factor.source}</MonoValue>
                              </dd>
                            </div>
                          ))}
                          <div className="flex gap-2">
                            <dt className="w-[168px] shrink-0 text-caption text-fg-muted">
                              ruleset
                            </dt>
                            <dd className="text-caption text-fg-secondary">
                              <MonoValue muted>{passenger.rule_version}</MonoValue>{' '}
                              <MonoValue muted>{passenger.ruleset_hash}</MonoValue>
                            </dd>
                          </div>
                        </dl>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <p className="border-t border-border-subtle px-3 py-2 text-caption text-fg-muted">
        Showing <MonoValue muted>{impacts.returned}</MonoValue> of{' '}
        <MonoValue muted>{impacts.passengers_assessed}</MonoValue> assessed, highest priority first.{' '}
        {impacts.note}
      </p>
    </Panel>
  );
}

/**
 * The factors nothing has established, stated as such.
 *
 * Rendered above the cohorts rather than in a footnote, because it changes how every band below it
 * should be read: `overnight_exposure` is the factor that decides who needs a room, and while it is
 * unestablished no band means "needs accommodation". The server names these on the contract, so
 * this component does not have to know which they are.
 */
function UnassessedFactors({ impacts }: { impacts: GroupImpactResponse }) {
  if (impacts.unassessed_factors.length === 0) return null;
  return (
    <div className="border-b border-border-subtle bg-inset px-3 py-2">
      <p className="text-caption uppercase text-fg-muted">
        not established — {impacts.unassessed_factors.length} of the ruleset's factors
      </p>
      <ul className="mt-1 flex flex-col gap-0.5">
        {impacts.unassessed_factors.map((factor) => (
          <li key={factor.factor} className="text-caption text-fg-secondary">
            <MonoValue>{factor.factor.replace(/_/g, ' ')}</MonoValue> — {factor.reason}. Would be
            established by <MonoValue muted>{factor.established_by}</MonoValue>.
          </li>
        ))}
      </ul>
      <p className="mt-1 text-caption text-fg-muted">
        These are absent, not false. No passenger is scored on them, and no band below should be
        read as "does not need accommodation".
      </p>
    </div>
  );
}

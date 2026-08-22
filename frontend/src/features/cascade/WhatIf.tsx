/**
 * What-If — a bounded, zero-write, deterministic re-evaluation. P2-D2.
 *
 * The design problem this panel has is credibility in the wrong direction: it is the one surface a
 * viewer is most likely to over-read as a prediction. So the boundary is not a footnote. The
 * server's own boundary note is rendered verbatim, `wrote_rows: false` is shown as a fact rather
 * than assumed, and every delta is phrased as "the same rules would have found", never "will".
 *
 * The levers are a closed set, published by the API. The controls below are built from
 * `levers_available` rather than hardcoded, so a lever the server does not accept cannot appear as
 * a control, and an undeclared one sent anyway comes back refused **by name** — shown, not
 * swallowed.
 *
 * Nothing here is a slider. A continuous control invites dragging until the answer looks good; a
 * small set of declared, typed values keeps this a question an operator asks deliberately.
 *
 * Owner: Stream D.
 */

import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { AlertTriangle, FlaskConical, RotateCcw } from 'lucide-react';
import { clsx } from 'clsx';

import { api, ApiError } from '@/api/client';
import type { WhatIfResponse } from '@/api/types';
import { MonoValue, Panel, StateBadge } from '@/components/ui/primitives';

/**
 * Discrete choices per lever. Values only — the labels and the set of levers come from the server.
 *
 * Chosen to bracket the seeded scenario: the connection minimum either side of 45, occupancy either
 * side of 2, and a rate cap above the INR 6,000 that makes the shortfall real.
 */
const LEVER_CHOICES: Record<string, { label: string; value: number }[]> = {
  minimum_connection_minutes: [
    { label: '30 min', value: 30 },
    { label: '45 min', value: 45 },
    { label: '60 min', value: 60 },
  ],
  passengers_per_room: [
    { label: '1 per room', value: 1 },
    { label: '2 per room', value: 2 },
    { label: '3 per room', value: 3 },
  ],
  max_rate_inr: [
    { label: 'INR 6,000 cap', value: 6000 },
    { label: 'INR 9,000 cap', value: 9000 },
    { label: 'INR 12,000 cap', value: 12000 },
  ],
  max_expansion_depth: [
    { label: 'direct only', value: 1 },
    { label: 'one hop on', value: 2 },
    { label: 'two hops on', value: 3 },
  ],
};

export function WhatIfPanel({ groupId }: { groupId: string }) {
  const [levers, setLevers] = useState<Record<string, number>>({});
  const [result, setResult] = useState<WhatIfResponse | null>(null);

  const run = useMutation({
    mutationFn: () => api.whatIf(groupId, levers),
    onSuccess: setResult,
  });

  // Available levers come from the last response; before the first call the panel offers the ones
  // it has choices for. Either way the server is the authority on what it will accept.
  const available = result ? Object.keys(result.levers_available) : Object.keys(LEVER_CHOICES);
  const offered = available.filter((lever) => LEVER_CHOICES[lever]);
  const error = run.error instanceof ApiError ? run.error : null;

  return (
    <Panel
      title="What-if"
      actions={
        <span className="flex items-center gap-1.5 text-caption uppercase text-fg-muted">
          <FlaskConical size={12} strokeWidth={1.5} aria-hidden />
          zero write
        </span>
      }
    >
      <div className="border-b border-border-subtle px-3 py-2">
        <p className="text-caption text-fg-muted">
          A re-evaluation of the recorded facts under substituted inputs, through the same
          deterministic rules the live services use. Not a simulation, not a forecast, and it writes
          nothing.
        </p>
      </div>

      <div className="flex flex-col gap-2 px-3 py-2">
        {offered.map((lever) => (
          <div key={lever}>
            <div className="mb-1 flex items-baseline gap-1.5">
              <span className="text-caption uppercase text-fg-secondary">
                {lever.replace(/_/g, ' ')}
              </span>
              {result?.levers_available[lever] && (
                <span className="text-caption text-fg-muted">{result.levers_available[lever]}</span>
              )}
            </div>
            <div className="flex flex-wrap gap-1" role="group" aria-label={lever}>
              {(LEVER_CHOICES[lever] ?? []).map((choice) => {
                const active = levers[lever] === choice.value;
                return (
                  <button
                    key={choice.value}
                    type="button"
                    aria-pressed={active}
                    onClick={() =>
                      setLevers((current) => {
                        const next = { ...current };
                        if (active) delete next[lever];
                        else next[lever] = choice.value;
                        return next;
                      })
                    }
                    className={clsx(
                      'rounded-sm border px-1.5 py-0.5 text-caption',
                      'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                      active
                        ? 'border-accent-border bg-accent-subtle text-accent'
                        : 'border-border-subtle text-fg-secondary hover:border-border-strong',
                    )}
                  >
                    {choice.label}
                  </button>
                );
              })}
            </div>
          </div>
        ))}

        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={() => run.mutate()}
            disabled={run.isPending || Object.keys(levers).length === 0}
            className={clsx(
              'rounded-sm border border-accent-border bg-accent-subtle px-2 py-1 text-caption uppercase text-accent',
              'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
              'disabled:cursor-not-allowed disabled:border-border-subtle disabled:bg-transparent disabled:text-fg-muted',
            )}
          >
            {run.isPending ? 'Re-evaluating' : 'Re-evaluate'}
          </button>
          {(Object.keys(levers).length > 0 || result) && (
            <button
              type="button"
              onClick={() => {
                setLevers({});
                setResult(null);
                run.reset();
              }}
              className="flex items-center gap-1 rounded-sm border border-border-subtle px-2 py-1 text-caption uppercase text-fg-secondary focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <RotateCcw size={11} strokeWidth={1.5} aria-hidden />
              Reset
            </button>
          )}
          {Object.keys(levers).length === 0 && !result && (
            <span className="text-caption text-fg-muted">Choose at least one input to vary.</span>
          )}
        </div>
      </div>

      {error && (
        <div className="border-t border-state-crit/30 bg-state-crit-bg px-3 py-2">
          <div className="flex items-center gap-2">
            <StateBadge status="failed" label={error.code} />
            <span className="text-caption text-state-crit">{error.message}</span>
          </div>
        </div>
      )}

      {result && (
        <div className="border-t border-border-subtle" aria-live="polite">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 bg-inset px-3 py-1.5">
            <span className="flex items-center gap-1.5 text-caption uppercase text-fg-muted">
              basis <MonoValue muted>{result.basis}</MonoValue>
            </span>
            <span className="flex items-center gap-1.5 text-caption uppercase text-fg-muted">
              wrote rows <MonoValue muted>{result.wrote_rows ? 'yes' : 'no'}</MonoValue>
            </span>
            <span className="flex items-center gap-1.5 text-caption uppercase text-fg-muted">
              rules <MonoValue muted>{result.rule_version}</MonoValue>
            </span>
          </div>

          <p className="px-3 py-2 text-body text-fg">{result.headline}</p>

          <table className="w-full border-collapse text-body">
            <thead>
              <tr className="border-y border-border-subtle bg-inset text-label uppercase text-fg-muted">
                <th scope="col" className="px-3 py-1 text-left font-medium">
                  Figure
                </th>
                <th scope="col" className="px-3 py-1 text-right font-medium">
                  Same rules, now
                </th>
                <th scope="col" className="px-3 py-1 text-right font-medium">
                  Substituted
                </th>
                <th scope="col" className="px-3 py-1 text-right font-medium">
                  Change
                </th>
              </tr>
            </thead>
            <tbody>
              {result.deltas.map((delta) => (
                <tr key={delta.key} className="border-b border-border-subtle">
                  <td className="px-3 py-1 text-fg-secondary">{delta.label}</td>
                  <td className="px-3 py-1 text-right">
                    <MonoValue muted>{delta.baseline}</MonoValue>
                  </td>
                  <td className="px-3 py-1 text-right">
                    <MonoValue>{delta.scenario}</MonoValue>
                  </td>
                  <td className="px-3 py-1 text-right">
                    {/* Sign only. No colour: green for "fewer connections broken" would read as an
                     * operational state, and state colours are reserved for operational state. */}
                    <MonoValue muted>
                      {delta.delta === 0 ? '—' : delta.delta > 0 ? `+${delta.delta}` : delta.delta}
                    </MonoValue>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {result.levers_rejected.length > 0 && (
            <div className="border-t border-state-warn/30 bg-state-warn-bg px-3 py-2">
              <div className="flex items-center gap-2">
                <AlertTriangle
                  size={12}
                  strokeWidth={1.5}
                  className="text-state-warn"
                  aria-hidden
                />
                <span className="text-label uppercase text-state-warn">Refused</span>
              </div>
              <ul className="mt-1 flex flex-col gap-0.5">
                {result.levers_rejected.map((rejection) => (
                  <li key={rejection.lever} className="text-caption text-state-warn">
                    <MonoValue muted>{rejection.lever}</MonoValue> — {rejection.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* The server's own boundary statement, verbatim. Paraphrasing it here would put a second
           * version of the most over-readable claim on the screen. */}
          <p className="border-t border-border-subtle px-3 py-2 text-caption text-fg-muted">
            {result.boundary_note}
          </p>
        </div>
      )}
    </Panel>
  );
}

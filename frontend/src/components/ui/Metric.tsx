/**
 * `<Metric>` — the traceability guarantee, enforced by the type system.
 *
 * `derivation` is REQUIRED. A number rendered without a provenance path does not compile, which
 * is what turns "every major number is traceable" from a promise in a review into a compiler
 * error. Every metric therefore arrives with the endpoint and field it came from, the rule or
 * formula behind it, and when it was recorded.
 *
 * It also centralises the two rules that make numbers scannable rather than readable: tabular
 * monospace, and an absent value rendered as an em dash rather than a fabricated zero.
 *
 * Owner: Stream D.
 */

import type { ReactNode } from 'react';
import { clsx } from 'clsx';

import type { Derivation } from './derivation';
import { MonoValue, ProvenanceDot, WhyPopover } from './primitives';

export function Metric({
  value,
  derivation,
  className,
  muted,
  suffix,
}: {
  /**
   * `null` and `undefined` mean "not returned" and render as an em dash. Zero renders as zero:
   * a real 0 from the API is data, an absent value is not.
   */
  value: number | string | null | undefined;
  derivation: Derivation;
  className?: string;
  muted?: boolean;
  suffix?: ReactNode;
}) {
  const absent = value === null || value === undefined || value === '';

  return (
    <WhyPopover derivation={derivation}>
      {absent ? (
        <span
          className="font-mono text-mono-sm tabular-nums text-fg-muted"
          title="Not returned by this endpoint. An absent value, not zero."
        >
          —
        </span>
      ) : (
        <MonoValue muted={muted} className={className}>
          {value}
          {suffix}
        </MonoValue>
      )}
    </WhyPopover>
  );
}

/** A metric in a bordered tile, for the situational tier. Label above, figure below. */
export function MetricTile({
  label,
  value,
  derivation,
  provenance,
  footnote,
}: {
  label: string;
  value: number | string | null | undefined;
  derivation: Derivation;
  provenance?: { kind: Parameters<typeof ProvenanceDot>[0]['kind']; provider: string };
  footnote?: ReactNode;
}) {
  return (
    <div className="flex min-w-[132px] flex-col gap-1 rounded border border-border-subtle bg-surface px-2.5 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-caption uppercase text-fg-muted">{label}</span>
        {provenance && <ProvenanceDot kind={provenance.kind} provider={provenance.provider} />}
      </div>
      <span className="text-subtitle">
        <Metric value={value} derivation={derivation} />
      </span>
      {footnote && <span className="text-caption text-fg-muted">{footnote}</span>}
    </div>
  );
}

/**
 * A bar over COUNTS that already exist in the payload — never a trend.
 *
 * No endpoint returns a time series, so a line chart would interpolate between two points and
 * invent the slope. A partition that the API already carries (evaluations by decision, pairings
 * by mechanism) is real, and a single-hue ramp of the accent reads it faster than a table.
 */
export function CountBar({
  segments,
  total,
}: {
  segments: { label: string; count: number; tone?: 'ok' | 'warn' | 'crit' | 'info' | 'neutral' }[];
  total: number;
}) {
  const TONE: Record<string, string> = {
    ok: 'bg-state-ok',
    warn: 'bg-state-warn',
    crit: 'bg-state-crit',
    info: 'bg-state-info',
    neutral: 'bg-state-neutral',
    accent: 'bg-accent',
  };

  return (
    <div className="flex flex-col gap-1">
      <div
        className="flex h-1.5 w-full overflow-hidden rounded-sm bg-inset"
        role="img"
        aria-label={segments.map((s) => `${s.label} ${s.count}`).join(', ')}
      >
        {segments
          .filter((segment) => segment.count > 0)
          .map((segment) => (
            <span
              key={segment.label}
              className={clsx('h-full', TONE[segment.tone ?? 'accent'])}
              style={{ width: `${total > 0 ? (segment.count / total) * 100 : 0}%` }}
            />
          ))}
      </div>
      <ul className="flex flex-wrap gap-x-3 gap-y-0.5">
        {segments.map((segment) => (
          <li key={segment.label} className="flex items-center gap-1 text-caption text-fg-muted">
            <span
              className={clsx('h-1.5 w-1.5 rounded-full', TONE[segment.tone ?? 'accent'])}
              aria-hidden
            />
            {segment.label}
            <MonoValue muted>{segment.count}</MonoValue>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Filter chips as a radiogroup: one active choice, keyboard-navigable, never colour-only. */
export function FilterChips<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: T; label: string; count?: number }[];
  value: T;
  onChange: (next: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap items-center gap-1">
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(option.value)}
            className={clsx(
              'inline-flex items-center gap-1.5 rounded-sm border px-2 py-0.5 text-label uppercase',
              'transition-colors duration-hover ease-out focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
              active
                ? 'border-accent-border bg-accent-subtle text-accent'
                : 'border-border-subtle text-fg-muted hover:text-fg-secondary',
            )}
          >
            {option.label}
            {option.count !== undefined && <MonoValue muted>{option.count}</MonoValue>}
          </button>
        );
      })}
    </div>
  );
}

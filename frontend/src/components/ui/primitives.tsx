/**
 * Shared primitives.
 *
 * Rules enforced here so they cannot drift:
 *   - every status renders through StateBadge (icon + label + colour, never colour alone)
 *   - every operational number renders through MonoValue with tabular figures
 *   - no component contains a colour literal; all colour comes from tokens
 *
 * Owner: Stream E. Stream F imports these and must not duplicate them.
 */

import {
  AlertTriangle,
  Ban,
  Check,
  CircleDot,
  Clock,
  HelpCircle,
  Loader2,
  Pause,
  X,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { clsx } from 'clsx';

import type { CheckState, ProvenanceKind, RiskLevel } from '@/api/types';

// ---------------------------------------------------------------- MonoValue
/**
 * Tabular monospace figures. The signature detail of the whole UI: digits align down a
 * column so the eye scans instead of reads.
 */
export function MonoValue({
  children,
  className,
  muted,
}: {
  children: ReactNode;
  className?: string;
  muted?: boolean;
}) {
  return (
    <span
      className={clsx(
        'font-mono text-mono-sm tabular-nums',
        muted ? 'text-fg-muted' : 'text-fg',
        className,
      )}
    >
      {children}
    </span>
  );
}

// ---------------------------------------------------------------- StateBadge
type Tone = 'ok' | 'warn' | 'crit' | 'info' | 'neutral';

const TONE_CLASS: Record<Tone, string> = {
  ok: 'text-state-ok bg-state-ok-bg border-state-ok/30',
  warn: 'text-state-warn bg-state-warn-bg border-state-warn/30',
  crit: 'text-state-crit bg-state-crit-bg border-state-crit/30',
  info: 'text-state-info bg-state-info-bg border-state-info/30',
  neutral: 'text-state-neutral bg-state-neutral-bg border-state-neutral/30',
};

const TONE_ICON: Record<Tone, typeof Check> = {
  ok: Check,
  warn: AlertTriangle,
  crit: X,
  info: CircleDot,
  neutral: Pause,
};

/** Operational status -> tone. Extend here, never inline in a feature. */
const STATUS_TONE: Record<string, Tone> = {
  on_time: 'ok',
  succeeded: 'ok',
  resolved: 'ok',
  execute: 'ok',
  PASS: 'ok',
  up: 'ok',
  approved: 'ok',

  delayed: 'warn',
  at_risk: 'warn',
  awaiting_approval: 'warn',
  needs_human: 'warn',
  execute_flagged: 'warn',
  WARN: 'warn',
  degraded: 'warn',

  cancelled: 'crit',
  failed: 'crit',
  blocked: 'crit',
  FAIL: 'crit',
  down: 'crit',
  rejected: 'crit',

  scheduled: 'info',
  detected: 'info',
  assessing: 'info',
  planning: 'info',
  assuring: 'info',
  executing: 'info',
  proposed: 'info',

  pending: 'neutral',
  skipped: 'neutral',
};

function humanise(value: string): string {
  if (value === value.toUpperCase() && value.length <= 5) return value;
  return value.replace(/_/g, ' ');
}

export function StateBadge({
  status,
  label,
  className,
}: {
  status: string;
  label?: string;
  className?: string;
}) {
  const tone = STATUS_TONE[status] ?? 'neutral';
  const Icon = TONE_ICON[tone];
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5 text-label uppercase',
        TONE_CLASS[tone],
        className,
      )}
    >
      <Icon size={12} strokeWidth={1.5} aria-hidden />
      {/* Label is always present: colour alone is never the signal. */}
      <span>{label ?? humanise(status)}</span>
    </span>
  );
}

export function CheckStateBadge({ state }: { state: CheckState }) {
  return <StateBadge status={state} label={state} />;
}

// ---------------------------------------------------------------- RiskChip
const RISK_TONE: Record<RiskLevel, Tone> = {
  low: 'ok',
  elevated: 'info',
  high: 'warn',
  severe: 'crit',
};

/**
 * Shows an index and a band, deliberately never a percentage: the score is not calibrated
 * against observed outcomes, so "87% chance of delay" would be an unearned claim.
 */
export function RiskChip({
  index,
  level,
  onClick,
}: {
  index: number;
  level: RiskLevel;
  onClick?: () => void;
}) {
  const tone = RISK_TONE[level];
  const Tag = onClick ? 'button' : 'span';
  return (
    <Tag
      {...(onClick ? { onClick, type: 'button' as const } : {})}
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5',
        TONE_CLASS[tone],
        onClick &&
          'transition-colors duration-hover ease-out hover:brightness-110 ' +
            'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 ' +
            'focus-visible:outline-accent',
      )}
      title={onClick ? 'Show contributing factors' : undefined}
    >
      <span className="font-mono text-mono-sm tabular-nums">{index}</span>
      <span className="text-label uppercase">{level}</span>
    </Tag>
  );
}

// ---------------------------------------------------------------- ProvenanceDot
const PROVENANCE_TONE: Record<ProvenanceKind, Tone> = {
  real: 'ok',
  simulated: 'info',
  synthetic: 'info',
  fixture: 'neutral',
  unavailable: 'crit',
};

const PROVENANCE_LABEL: Record<ProvenanceKind, string> = {
  real: 'Live/real source',
  simulated: 'Simulated by local state machine',
  synthetic: 'Synthetic generated data',
  fixture: 'Committed fixture',
  unavailable: 'Source unavailable',
};

const DOT_COLOUR: Record<Tone, string> = {
  ok: 'bg-state-ok',
  warn: 'bg-state-warn',
  crit: 'bg-state-crit',
  info: 'bg-state-info',
  neutral: 'bg-state-neutral',
};

/** Answers "is this real?" at a glance, on every panel that shows data. */
export function ProvenanceDot({
  kind,
  provider,
  sourceRef,
  isStale,
}: {
  kind: ProvenanceKind;
  provider?: string;
  sourceRef?: string | null;
  isStale?: boolean;
}) {
  const tone = isStale ? 'warn' : PROVENANCE_TONE[kind];
  const title = [
    PROVENANCE_LABEL[kind],
    provider ? `provider: ${provider}` : null,
    sourceRef ? `ref: ${sourceRef}` : null,
    isStale ? 'STALE: past its freshness limit' : null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <span className="inline-flex items-center gap-1" title={title}>
      <span className={clsx('h-1.5 w-1.5 rounded-full', DOT_COLOUR[tone])} aria-hidden />
      <span className="sr-only">{title}</span>
    </span>
  );
}

// ---------------------------------------------------------------- WhyPopover
/**
 * The highest-trust, cheapest feature in the design: every derived number can explain
 * where it came from. Wave 0 ships the accessible title-based version; Stream E upgrades
 * it to a positioned popover.
 */
export function WhyPopover({ children, derivation }: { children: ReactNode; derivation: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 border-b border-dashed border-border-strong"
      title={derivation}
      tabIndex={0}
      role="note"
      aria-label={`Derivation: ${derivation}`}
    >
      {children}
      <HelpCircle size={11} strokeWidth={1.5} className="text-fg-muted" aria-hidden />
    </span>
  );
}

// ---------------------------------------------------------------- states
export function Panel({
  title,
  actions,
  children,
  className,
}: {
  title?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={clsx('rounded border border-border-subtle bg-surface', className)}
      aria-label={title}
    >
      {title && (
        <header className="flex items-center justify-between border-b border-border-subtle px-3 py-2">
          <h2 className="text-label uppercase text-fg-secondary">{title}</h2>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

/** Designed, not blank. A blank panel during a demo reads as broken. */
export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
      <CircleDot size={20} strokeWidth={1.5} className="text-fg-muted" aria-hidden />
      <p className="text-body text-fg">{title}</p>
      <p className="max-w-sm text-caption text-fg-muted">{description}</p>
      {action}
    </div>
  );
}

export function LoadingState({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 px-4 py-10 text-fg-muted" role="status">
      <Loader2 size={16} strokeWidth={1.5} className="animate-spin" aria-hidden />
      <span className="text-body">{label}</span>
    </div>
  );
}

/** Always surfaces the correlation ID: it is what makes a failure diagnosable. */
export function ErrorState({
  code,
  message,
  correlationId,
  onRetry,
}: {
  code: string;
  message: string;
  correlationId?: string | null;
  onRetry?: () => void;
}) {
  return (
    <div className="flex flex-col gap-2 px-4 py-8" role="alert">
      <div className="flex items-center gap-2">
        <Ban size={16} strokeWidth={1.5} className="text-state-crit" aria-hidden />
        <MonoValue className="text-state-crit">{code}</MonoValue>
      </div>
      <p className="text-body text-fg-secondary">{message}</p>
      {correlationId && (
        <p className="text-caption text-fg-muted">
          correlation <MonoValue muted>{correlationId}</MonoValue>
        </p>
      )}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="self-start rounded-sm border border-border px-2 py-1 text-label uppercase text-fg-secondary transition-colors duration-hover ease-out hover:border-accent-border hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function AgeIndicator({ minutes, limit = 60 }: { minutes: number; limit?: number }) {
  const stale = minutes > limit;
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 font-mono text-caption tabular-nums',
        stale ? 'text-state-warn' : 'text-fg-muted',
      )}
      title={
        stale
          ? `Observed ${minutes}m ago, past the ${limit}m freshness limit`
          : `Observed ${minutes}m ago`
      }
    >
      <Clock size={10} strokeWidth={1.5} aria-hidden />
      {minutes}m
    </span>
  );
}

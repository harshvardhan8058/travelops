/**
 * Shared primitives.
 *
 * Rules enforced here so they cannot drift:
 *   - every status renders through StateBadge (icon + label + colour, never colour alone)
 *   - every operational number renders through MonoValue with tabular figures
 *   - every derived number explains itself through WhyPopover
 *   - no component contains a colour literal; all colour comes from tokens
 *
 * Owner: Stream D, which owns all of frontend/. Features import these and must not
 * duplicate them: a local variant is how two screens end up disagreeing about what amber
 * means.
 */

import {
  AlertTriangle,
  Ban,
  Check,
  Circle,
  CircleDot,
  Clock,
  HelpCircle,
  Info,
  Loader2,
  MinusCircle,
  Pause,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { clsx } from 'clsx';

import type { CheckState, ProvenanceKind, RiskLevel } from '@/api/types';
import type { Derivation, DerivationRule, DerivationTime } from './derivation';
import { useAnchoredPosition } from './useAnchoredPosition';

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
  stale: 'warn',

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

  // Action risk tier. `high` is amber rather than red: the action is not broken, it requires
  // a human. Red is reserved for something that failed or breached.
  tier_low: 'neutral',
  tier_medium: 'info',
  tier_high: 'warn',

  // Incident severity. Same reasoning: severe conditions are not a failure of the system.
  low: 'neutral',
  medium: 'info',
  high: 'warn',
  critical: 'crit',
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
/** Never silently drops an API value: an unparseable timestamp is shown as returned. */
function formatUtc(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  const text = parsed.toISOString();
  return `${text.slice(0, 10)} ${text.slice(11, 19)}Z`;
}

/**
 * An absence, stated. Rendering this instead of hiding the section is what turns a
 * data-contract gap into a visible request to the stream that owns the endpoint.
 */
function NotRecorded({ children = 'not recorded' }: { children?: ReactNode }) {
  return (
    <span className="inline-flex items-start gap-1 text-caption text-fg-muted">
      <MinusCircle size={11} strokeWidth={1.5} className="mt-0.5 shrink-0" aria-hidden />
      <span>{children}</span>
    </span>
  );
}

function DerivationSection({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="border-t border-border-subtle px-3 py-2 first:border-t-0">
      <h3 className="mb-1 text-label uppercase text-fg-muted">{label}</h3>
      {children}
    </div>
  );
}

function DerivationRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-2 py-0.5">
      <span className="w-[86px] shrink-0 text-caption uppercase text-fg-muted">{label}</span>
      <span className="min-w-0 flex-1 text-body text-fg">{children}</span>
    </div>
  );
}

function RuleSection({ rule }: { rule?: DerivationRule }) {
  const hasBody = rule && (rule.id || rule.formula || rule.version || rule.refs?.length);

  return (
    <DerivationSection label="Rule or formula">
      {!rule || !hasBody ? (
        <NotRecorded />
      ) : (
        <>
          <DerivationRow label="kind">{rule.kind}</DerivationRow>
          {rule.id && (
            <DerivationRow label="id">
              <MonoValue className="break-all">{rule.id}</MonoValue>
            </DerivationRow>
          )}
          {rule.version && (
            <DerivationRow label="version">
              <MonoValue className="break-all">{rule.version}</MonoValue>
            </DerivationRow>
          )}
          {/* The arithmetic actually applied, verbatim. A judge can check it by hand. */}
          {rule.formula && (
            <DerivationRow label="formula">
              <MonoValue className="break-all">{rule.formula}</MonoValue>
            </DerivationRow>
          )}
          {rule.refs && rule.refs.length > 0 && (
            <DerivationRow label="refs">
              <span className="flex flex-col gap-0.5">
                {rule.refs.map((ref) => (
                  <MonoValue key={ref} muted className="break-all">
                    {ref}
                  </MonoValue>
                ))}
              </span>
            </DerivationRow>
          )}
          {rule.note && <p className="mt-1 text-caption text-fg-muted">{rule.note}</p>}
        </>
      )}
    </DerivationSection>
  );
}

function TimeSection({ when }: { when?: DerivationTime[] }) {
  return (
    <DerivationSection label="When">
      {!when || when.length === 0 ? (
        <NotRecorded />
      ) : (
        when.map((entry) => (
          <DerivationRow key={entry.label} label={entry.label}>
            <span className="flex flex-wrap items-center gap-1.5">
              {formatUtc(entry.at) ? (
                <MonoValue muted>{formatUtc(entry.at)}</MonoValue>
              ) : (
                <NotRecorded />
              )}
              {typeof entry.ageMinutes === 'number' && <AgeIndicator minutes={entry.ageMinutes} />}
              {/* Icon + word + colour: the API's own freshness verdict, never recomputed. */}
              {entry.isStale && <StateBadge status="stale" label="stale" />}
            </span>
          </DerivationRow>
        ))
      )}
    </DerivationSection>
  );
}

function DerivationBody({ derivation }: { derivation: Derivation }) {
  const { inputs, rule, when, evidenceRefs, absences, caveat } = derivation;

  return (
    <>
      <DerivationSection label="Inputs">
        {!inputs || inputs.length === 0 ? (
          <NotRecorded />
        ) : (
          inputs.map((input) => (
            <DerivationRow key={input.label} label={input.label}>
              <span className="flex flex-wrap items-center gap-1.5">
                <MonoValue className="break-all">{input.value}</MonoValue>
                {input.provenance && (
                  <>
                    <ProvenanceDot
                      kind={input.provenance.kind}
                      provider={input.provenance.provider}
                      sourceRef={input.provenance.source_ref}
                      isStale={input.provenance.is_stale}
                    />
                    <span className="text-caption text-fg-muted">
                      {input.provenance.kind} · {input.provenance.provider}
                    </span>
                  </>
                )}
              </span>
              {/* An API-supplied explanation of this input, verbatim. */}
              {input.detail && (
                <span className="mt-0.5 block text-caption text-fg-muted">{input.detail}</span>
              )}
            </DerivationRow>
          ))
        )}
      </DerivationSection>

      <RuleSection rule={rule} />
      <TimeSection when={when} />

      <DerivationSection label="Evidence">
        {!evidenceRefs || evidenceRefs.length === 0 ? (
          <NotRecorded>no evidence refs returned</NotRecorded>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {evidenceRefs.map((ref) => (
              <li key={ref}>
                <MonoValue muted className="break-all">
                  {ref}
                </MonoValue>
              </li>
            ))}
          </ul>
        )}
      </DerivationSection>

      {/* Named gaps in the endpoint's contract. Visible, so nobody fills one in by guessing. */}
      {absences && absences.length > 0 && (
        <DerivationSection label="Not recorded">
          <ul className="flex flex-col gap-1">
            {absences.map((absence) => (
              <li key={absence.label}>
                <NotRecorded>
                  <MonoValue muted>{absence.label}</MonoValue> — {absence.detail}
                </NotRecorded>
              </li>
            ))}
          </ul>
        </DerivationSection>
      )}

      {caveat && (
        <div className="flex items-start gap-1.5 border-t border-border-subtle bg-inset px-3 py-2">
          <Info size={12} strokeWidth={1.5} className="mt-0.5 shrink-0 text-fg-muted" aria-hidden />
          <p className="text-caption text-fg-muted">{caveat}</p>
        </div>
      )}
    </>
  );
}

const TABBABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

function tabbablesIn(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(TABBABLE)).filter(
    (element) => !element.hasAttribute('disabled') && element.getClientRects().length > 0,
  );
}

/**
 * "Why?" on every number — the highest-trust, cheapest feature in the design.
 *
 * Answers the three questions docs/27 requires of every figure: where the input came from,
 * which rule or formula produced it, and when. Content comes from an adapter in
 * derivation.ts, never from prose written at the call site.
 *
 * Interaction model:
 *   - the trigger is a real <button>, so it is keyboard reachable and announces its state.
 *     Children must therefore be PRESENTATIONAL — passing an interactive element would nest
 *     buttons, which is invalid HTML and breaks tab order. RiskChip goes in without onClick.
 *   - no `title` attribute: a native tooltip alongside a popover renders the same content
 *     twice, in two places, with different styling.
 *   - Esc closes and returns focus to the trigger; outside click and tabbing out also close.
 *   - the panel is portalled and fixed-positioned so table and rail scroll containers cannot
 *     clip it. Elevation is a 1px border, not a shadow, and there is no backdrop blur — the
 *     command palette is the only blurred layer in the product.
 */
export function WhyPopover({
  children,
  derivation,
  className,
}: {
  children: ReactNode;
  derivation: Derivation;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [entered, setEntered] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const position = useAnchoredPosition({ anchorRef: triggerRef, floatingRef: panelRef, open });

  const hasFocusedRef = useRef(false);

  const close = useCallback((returnFocus = true) => {
    setOpen(false);
    setEntered(false);
    if (returnFocus) triggerRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) hasFocusedRef.current = false;
  }, [open]);

  /*
   * Move focus into the panel so Tab reaches its content and Esc has a home.
   *
   * This waits for `position`, and that is not incidental. Before measurement the panel is
   * `visibility: hidden`, and focusing a hidden element is a silent no-op — the browser
   * leaves focus on the trigger. React can flush this passive effect before the layout
   * effect's measurement has committed, so focusing on `open` alone loses the focus move
   * and, with it, Escape. Verified headlessly: without the `position` gate,
   * `document.activeElement` stayed on the trigger and Escape did nothing.
   *
   * The ref keeps it to once per open: `position` also changes on scroll and resize, and
   * refocusing then would yank focus off whatever the user had tabbed to.
   */
  useEffect(() => {
    if (!open || !position || hasFocusedRef.current) return;
    hasFocusedRef.current = true;
    panelRef.current?.focus({ preventScroll: true });
  }, [open, position]);

  // One frame late, so the transition has a start state to move from. prefers-reduced-motion
  // is honoured globally in tokens.css by collapsing transition-duration, so this becomes an
  // instant appearance rather than a special case here.
  useEffect(() => {
    if (!open) return;
    const frame = window.requestAnimationFrame(() => setEntered(true));
    return () => window.cancelAnimationFrame(frame);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: Event) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      // Do not pull focus back to the trigger: the user is clicking somewhere else.
      close(false);
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    return () => document.removeEventListener('pointerdown', onPointerDown, true);
  }, [open, close]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls={open ? panelId : undefined}
        aria-label={`Why: ${derivation.title}`}
        onClick={(event) => {
          // A popover trigger must not also activate whatever row or card contains it.
          event.stopPropagation();
          if (open) close();
          else setOpen(true);
        }}
        onKeyDown={(event) => {
          // Defensive: Escape works wherever focus happens to be while the panel is open.
          if (event.key === 'Escape' && open) {
            event.stopPropagation();
            close();
          }
        }}
        className={clsx(
          'inline-flex items-center gap-1 border-b border-dashed border-border-strong text-left',
          'transition-colors duration-hover ease-out hover:border-accent-border',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
          open && 'border-accent-border',
          className,
        )}
      >
        {children}
        <HelpCircle
          size={11}
          strokeWidth={1.5}
          className={clsx('shrink-0', open ? 'text-accent' : 'text-fg-muted')}
          aria-hidden
        />
      </button>

      {open &&
        createPortal(
          <div
            ref={panelRef}
            id={panelId}
            role="dialog"
            aria-label={`Derivation: ${derivation.title}`}
            tabIndex={-1}
            style={
              position
                ? { top: position.top, left: position.left, maxHeight: position.maxHeight }
                : // Measured in a layout effect before paint, so this is never visible.
                  { top: 0, left: 0, visibility: 'hidden' }
            }
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.stopPropagation();
                close();
                return;
              }
              if (event.key !== 'Tab') return;

              const panel = panelRef.current;
              if (!panel) return;
              const stops = tabbablesIn(panel);
              const active = document.activeElement;
              const leavingForward = !event.shiftKey && active === stops[stops.length - 1];
              const leavingBackward = event.shiftKey && (active === panel || active === stops[0]);

              if (stops.length === 0 || leavingForward || leavingBackward) {
                /*
                 * The panel is portalled to the end of <body>, so the browser's next tab
                 * stop is outside the document. Hand focus back to the trigger instead: the
                 * next Tab continues from the row the user was on, and focus never leaves
                 * the page.
                 */
                event.preventDefault();
                close();
              }
            }}
            onBlur={(event) => {
              const next = event.relatedTarget as Node | null;
              // A null relatedTarget means the window lost focus, not that the user left the
              // popover — closing then would fight with alt-tab.
              if (!next) return;
              if (panelRef.current?.contains(next) || triggerRef.current?.contains(next)) return;
              close(false);
            }}
            className={clsx(
              'fixed z-50 w-[340px] overflow-y-auto rounded border border-border bg-raised outline-none',
              'transition-[opacity,transform] duration-panel ease-out',
              'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
              entered ? 'translate-y-0 opacity-100' : 'translate-y-1 opacity-0',
            )}
          >
            <header className="flex items-start gap-2 border-b border-border-subtle bg-inset px-3 py-2">
              <div className="min-w-0 flex-1">
                <p className="text-body text-fg">{derivation.title}</p>
                {derivation.subtitle && (
                  <MonoValue muted className="break-all">
                    {derivation.subtitle}
                  </MonoValue>
                )}
              </div>
              <button
                type="button"
                onClick={() => close()}
                aria-label="Close derivation"
                className="shrink-0 rounded-sm border border-border-subtle p-0.5 text-fg-muted transition-colors duration-hover ease-out hover:border-accent-border hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                <X size={12} strokeWidth={1.5} aria-hidden />
              </button>
            </header>

            <DerivationBody derivation={derivation} />
          </div>,
          document.body,
        )}
    </>
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

// ---------------------------------------------------------------- StateRail
/**
 * The incident state machine, rendered as a rail:
 * detected -> assessing -> planning -> assuring -> executing -> resolved.
 *
 * Reached states carry a check, the current state carries a filled dot, and unreached states
 * stay muted — so the position in the workflow survives a projector washing out hue. The
 * accent marks the current step because that is an active-state cue, not an operational
 * status; green/amber/red stay reserved for what the operation is actually doing.
 *
 * States and their timestamps come from `state_rail` as returned. The rail never assumes the
 * canonical six: if the API returns a different sequence, that sequence is what renders.
 *
 * The connector between steps is `bg-border`, not `bg-border-default`. `tailwind.config.ts` maps
 * the mid-weight border to the `DEFAULT` key, and Tailwind emits a `DEFAULT` key as the bare class
 * name — so `bg-border-default` was never generated and the connectors rendered with no background
 * at all. A rail of disconnected chips is not a rail, and the failure was invisible to every gate:
 * a missing background breaks no type, no lint rule, no colour check, and no contrast probe.
 * `StepRail` below had the same typo.
 */
export function StateRail({
  rail,
  current,
}: {
  rail: { state: string; reached_at: string | null }[];
  current: string;
}) {
  return (
    <ol className="flex flex-wrap items-center gap-1" aria-label="Incident state">
      {rail.map((step, index) => {
        const isCurrent = step.state === current;
        const isReached = step.reached_at !== null;
        const Icon = isCurrent ? CircleDot : isReached ? Check : Circle;

        return (
          <li key={step.state} className="flex items-center gap-1">
            {index > 0 && <span className="h-px w-3 bg-border" aria-hidden />}
            <span
              {...(isCurrent ? { 'aria-current': 'step' as const } : {})}
              className={clsx(
                'inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5',
                isCurrent
                  ? 'border-accent-border bg-accent-subtle text-accent'
                  : isReached
                    ? 'border-border-subtle bg-inset text-fg-secondary'
                    : 'border-border-subtle text-fg-muted',
              )}
            >
              <Icon size={11} strokeWidth={1.5} aria-hidden />
              <span className="text-label uppercase">{step.state.replace(/_/g, ' ')}</span>
              {step.reached_at && (
                <MonoValue muted className="text-caption">
                  {new Date(step.reached_at).toISOString().slice(11, 19)}
                </MonoValue>
              )}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

// ---------------------------------------------------------------- StepRail
/**
 * A wizard's steps, rendered as a rail.
 *
 * Sibling to `StateRail` rather than a copy of it, because the two answer different questions and
 * conflating them would break both. `StateRail` reports a state machine the **backend** advanced,
 * keyed on a `reached_at` timestamp; this reports where an **operator** is inside a form they can
 * move around freely, and a form has a state the incident rail has no concept of: reachable but not
 * yet valid.
 *
 * That fourth state is why this exists. A wizard that renders "not started" and "started and wrong"
 * identically is a wizard whose Next button appears to do nothing, and the operator has no way to
 * learn which field is at fault. `blocked` carries a warning glyph and warn tone; `todo` stays muted.
 *
 * Steps are buttons when `onSelect` is given and plain items otherwise, so a rail on a read-only
 * review surface does not offer controls that go nowhere. Colour is never the only signal: every
 * state has its own glyph and every step is labelled in words.
 *
 * Owner: Stream D.
 */
export type StepState = 'done' | 'current' | 'todo' | 'blocked';

const STEP_ICON: Record<StepState, typeof Check> = {
  done: Check,
  current: CircleDot,
  todo: Circle,
  blocked: AlertTriangle,
};

const STEP_CLASS: Record<StepState, string> = {
  done: 'border-border-subtle bg-inset text-fg-secondary',
  current: 'border-accent-border bg-accent-subtle text-accent',
  todo: 'border-border-subtle text-fg-muted',
  blocked: 'border-state-warn/30 bg-state-warn-bg text-state-warn',
};

export function StepRail({
  steps,
  onSelect,
  label = 'Steps',
}: {
  steps: { id: string; label: string; state: StepState }[];
  onSelect?: (id: string) => void;
  label?: string;
}) {
  return (
    <ol className="flex flex-wrap items-center gap-1" aria-label={label}>
      {steps.map((step, index) => {
        const Icon = STEP_ICON[step.state];
        const isCurrent = step.state === 'current';
        const content = (
          <>
            <Icon size={11} strokeWidth={1.5} aria-hidden />
            <span className="text-label uppercase">{step.label}</span>
            {/* The number is a position, so it reads as an ordinal rather than a measurement. */}
            <MonoValue muted className="text-caption">
              {index + 1}
            </MonoValue>
          </>
        );

        const shell = clsx(
          'inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5',
          STEP_CLASS[step.state],
        );

        return (
          <li key={step.id} className="flex items-center gap-1">
            {index > 0 && <span className="h-px w-3 bg-border" aria-hidden />}
            {onSelect ? (
              <button
                type="button"
                onClick={() => onSelect(step.id)}
                {...(isCurrent ? { 'aria-current': 'step' as const } : {})}
                className={clsx(
                  shell,
                  'transition-colors duration-hover ease-out',
                  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                  !isCurrent && 'hover:border-accent-border hover:text-accent',
                )}
              >
                {content}
              </button>
            ) : (
              <span {...(isCurrent ? { 'aria-current': 'step' as const } : {})} className={shell}>
                {content}
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

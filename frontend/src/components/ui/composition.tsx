/**
 * Composition primitives: the shapes that sit between a `Panel` and a value.
 *
 * `primitives.tsx` answers "how does one value render" — a status, a number, a provenance dot,
 * an explanation. It deliberately says nothing about how a *screen* is put together, and the
 * consequence showed up in an audit of the eleven feature screens: eleven private
 * implementations of a section heading, five of a definition row at three different label
 * widths, three byte-identical `Labelled` components each carrying the same comment about the
 * "MoCA" defect, five copies of one table head, twelve hand-written notice strips, and two
 * copies of the approval reason field down to its `maxLength`.
 *
 * None of that is styling for its own sake. Each duplicate is a place where two screens can
 * start disagreeing about what a subordinate heading looks like, how wide a label column is, or
 * whether a warning has an icon — and an operations console that is internally inconsistent
 * reads as unreliable long before anyone can say why.
 *
 * So this file holds the composition layer, under the same rules `primitives.tsx` states:
 *
 *   - no colour literal; every colour is a token utility
 *   - a tone is never the only signal — anything that carries meaning carries a word or a glyph
 *   - the LABEL is uppercased, never the value: a contract literal reaches the screen verbatim
 *     (`MoCA Passenger Charter` must not render as `MOCA PASSENGER CHARTER`, and the browser
 *     gate asserts exactly this on the policy and plan routes)
 *   - elevation is a 1px border, never a shadow, gradient or blur
 *   - nothing is disabled with `opacity`: `opacity-50` over `--fg-muted` measures 2.22:1 and
 *     fails the contrast gate, so a disabled control changes token instead of fading
 *
 * Every colour pairing used below is one already proven against the WCAG probe in
 * `scripts/verify-console.mjs` by an existing component. New pairings are not invented here.
 *
 * Owner: Stream D.
 */

import { AlertTriangle, Ban, CheckCircle2, Info, MinusCircle } from 'lucide-react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { clsx } from 'clsx';

import type { DataUnavailable } from '@/api/unavailable';
import { EmptyState, MonoValue, StateBadge } from './primitives';

// ---------------------------------------------------------------- tone
/**
 * The tones available to composition.
 *
 * `muted`/`default` are structural — they carry no operational meaning. `ok`/`warn`/`crit`/`info`
 * are the operational state ramp and mean exactly what they mean in `StateBadge`. `accent` is
 * brand/selection/active and is deliberately outside the ramp, so a highlighted row can never be
 * misread as a warning.
 */
export type SurfaceTone = 'default' | 'muted' | 'ok' | 'warn' | 'crit' | 'info' | 'accent';

const TEXT_TONE: Record<SurfaceTone, string> = {
  default: 'text-fg-secondary',
  muted: 'text-fg-muted',
  ok: 'text-state-ok',
  warn: 'text-state-warn',
  crit: 'text-state-crit',
  info: 'text-state-info',
  accent: 'text-accent',
};

/** Tinted bands. The border carries 30% of the state colour, matching `StateBadge`. */
const BAND_TONE: Record<SurfaceTone, string> = {
  default: 'border-border-subtle text-fg-secondary',
  muted: 'border-border-subtle text-fg-muted',
  ok: 'border-state-ok/30 bg-state-ok-bg text-state-ok',
  warn: 'border-state-warn/30 bg-state-warn-bg text-state-warn',
  crit: 'border-state-crit/30 bg-state-crit-bg text-state-crit',
  info: 'border-state-info/30 bg-state-info-bg text-state-info',
  accent: 'border-accent-border bg-accent-subtle text-accent',
};

const BAND_ICON: Partial<Record<SurfaceTone, typeof Info>> = {
  ok: CheckCircle2,
  warn: AlertTriangle,
  crit: Ban,
  info: Info,
  accent: Info,
};

// ---------------------------------------------------------------- PageHeader
/**
 * The screen's own identity, above its panels.
 *
 * Before this, no screen had one. Every surface opened with an untitled `Panel` used as a bar of
 * 12px uppercase labels, which made a route's subject — the flight, the booking reference, the
 * disruption group — exactly as loud as the word "trigger" beside it. There was no `h1` anywhere
 * in the console, so a screen reader had no route-level landmark either.
 *
 * The hierarchy is: `eyebrow` says which surface you are on (uppercased, it is a label), `title`
 * is the subject and renders at `text-title` (never uppercased — it is usually a contract value),
 * `meta` is the supporting detail, and `footer` takes a rail or a strip that belongs to the
 * header rather than to the first panel.
 */
export function PageHeader({
  eyebrow,
  title,
  status,
  meta,
  actions,
  footer,
  className,
}: {
  eyebrow: string;
  /** The subject of the screen. Rendered verbatim: never CSS-transformed. */
  title: ReactNode;
  /** Operational state of the subject, e.g. a `StateBadge`. */
  status?: ReactNode;
  /** Supporting detail. `Labelled` items read best here. */
  meta?: ReactNode;
  actions?: ReactNode;
  /** A rail, a provenance strip, or anything that qualifies the whole screen. */
  footer?: ReactNode;
  className?: string;
}) {
  return (
    <header className={clsx('rounded border border-border-subtle bg-surface', className)}>
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3 px-3 py-2.5">
        <div className="flex min-w-0 flex-col gap-1.5">
          <p className="text-label uppercase text-fg-muted">{eyebrow}</p>
          <div className="flex flex-wrap items-center gap-2.5">
            <h1 className="text-title font-semibold text-fg">{title}</h1>
            {status}
          </div>
          {meta && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 pt-0.5">{meta}</div>
          )}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {footer && <div className="border-t border-border-subtle px-3 py-2">{footer}</div>}
    </header>
  );
}

// ---------------------------------------------------------------- Labelled
/**
 * An uppercase label beside a value rendered exactly as it arrived.
 *
 * Three screens each carried a private copy of this, each with its own comment explaining the
 * same defect: uppercasing the *wrapper* rather than the label once rendered the policy pack
 * `MoCA Passenger Charter` as `MOCA PASSENGER CHARTER`, `weather` as `WEATHER`, and a lowercase
 * hex `pack_hash` in caps. The browser gate now asserts several contract values reach the DOM
 * verbatim, so the rule is mechanical: `uppercase` goes on the label span and nowhere else.
 */
export function Labelled({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={clsx('inline-flex items-baseline gap-1.5', className)}>
      <span className="shrink-0 text-caption uppercase text-fg-muted">{label}</span>
      {children}
    </span>
  );
}

// ---------------------------------------------------------------- SectionHeading
/**
 * A subordinate heading inside a panel.
 *
 * `Panel` renders the `h2`; this renders the `h3` beneath it, at one weight and one size so a
 * subsection cannot accidentally out-shout the panel that contains it. `tone` exists because a
 * few headings *are* the verdict — "Would be covered" in green beside "Cannot be covered" in
 * amber — and in those cases the count belongs in the heading rather than in prose below it.
 */
export function SectionHeading({
  children,
  count,
  tone = 'default',
  hint,
  actions,
  className,
}: {
  children: ReactNode;
  /** Rendered after a separator, in tabular figures. */
  count?: number | string | null;
  tone?: SurfaceTone;
  /** One clarifying line, below the heading. */
  hint?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx('flex items-start justify-between gap-3', className)}>
      <div className="min-w-0">
        <h3 className={clsx('flex items-center gap-1.5 text-label uppercase', TEXT_TONE[tone])}>
          <span>{children}</span>
          {count !== null && count !== undefined && (
            <>
              <span aria-hidden className="text-fg-muted">
                ·
              </span>
              <MonoValue className={clsx('text-caption', TEXT_TONE[tone])}>{count}</MonoValue>
            </>
          )}
        </h3>
        {hint && <p className="mt-0.5 text-caption text-fg-muted">{hint}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

// ---------------------------------------------------------------- PanelBody / PanelSection
/**
 * The inside of a panel, at one padding.
 *
 * Panels were padded with `px-3 py-2`, `px-3 py-3`, `px-3 py-1.5` and `p-2` depending on which
 * screen wrote them, which is visible as soon as two panels sit side by side in a grid.
 */
export function PanelBody({
  children,
  className,
  gap = 'normal',
}: {
  children: ReactNode;
  className?: string;
  /** `tight` for a list of rows, `normal` for prose and controls, `loose` for a form. */
  gap?: 'tight' | 'normal' | 'loose';
}) {
  return (
    <div
      className={clsx(
        'flex flex-col px-3 py-2.5',
        gap === 'tight' ? 'gap-1.5' : gap === 'loose' ? 'gap-3.5' : 'gap-2.5',
        className,
      )}
    >
      {children}
    </div>
  );
}

/**
 * A titled division inside a panel, separated by a rule rather than by whitespace.
 *
 * `first:border-t-0` means sections stack without a doubled rule under the panel header, so a
 * caller can render a list of these without special-casing the first.
 */
export function PanelSection({
  title,
  count,
  tone,
  hint,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  count?: number | string | null;
  tone?: SurfaceTone;
  hint?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx('border-t border-border-subtle px-3 py-2.5 first:border-t-0', className)}>
      {title && (
        <SectionHeading tone={tone} count={count} hint={hint} actions={actions} className="mb-1.5">
          {title}
        </SectionHeading>
      )}
      {children}
    </div>
  );
}

// ---------------------------------------------------------------- DefinitionList
const LABEL_WIDTH = {
  sm: 'w-[80px]',
  md: 'w-[104px]',
  lg: 'w-[136px]',
} as const;

/**
 * A `dl` of label/value rows.
 *
 * Five private versions of this existed at three label widths (`w-[72px]`, `w-[92px]`,
 * `w-[104px]`), so two panels in the same column had their values starting at different x
 * positions. `width` is a named choice rather than a free pixel value, which is what stops a
 * sixth width appearing.
 */
export function DefinitionList({
  children,
  width = 'md',
  className,
}: {
  children: ReactNode;
  width?: keyof typeof LABEL_WIDTH;
  className?: string;
}) {
  return (
    <dl className={clsx('flex flex-col gap-1', className)} data-label-width={width}>
      {children}
    </dl>
  );
}

/**
 * One row of a `DefinitionList`.
 *
 * `width` is repeated on the row rather than inherited through context on purpose: these rows are
 * frequently rendered by a `.map` in a different component from the list, and a context provider
 * for a label width is more machinery than a prop.
 */
export function DefinitionRow({
  label,
  children,
  width = 'md',
  className,
}: {
  label: string;
  children: ReactNode;
  width?: keyof typeof LABEL_WIDTH;
  className?: string;
}) {
  return (
    <div className={clsx('flex items-start gap-2', className)}>
      <dt className={clsx('shrink-0 text-caption uppercase text-fg-muted', LABEL_WIDTH[width])}>
        {label}
      </dt>
      <dd className="min-w-0 flex-1 text-body text-fg">{children}</dd>
    </div>
  );
}

// ---------------------------------------------------------------- Notice
/**
 * A qualifying band: a footnote, a caveat, a warning, a refusal.
 *
 * Twelve of these were written by hand across the screens, in two shapes that had drifted apart —
 * a neutral `border-t … text-caption text-fg-muted` footnote and a tinted
 * `border-t border-state-warn/30 bg-state-warn-bg … text-state-warn` banner — with the icon
 * present on some and absent on others.
 *
 * `role="alert"` is set only when asked for. A permanent caveat that announces itself on every
 * render trains people to ignore the assistive layer, so `alert` is reserved for something that
 * has just become true.
 */
export function Notice({
  tone = 'muted',
  children,
  icon = true,
  alert = false,
  divider = 'top',
  className,
}: {
  tone?: SurfaceTone;
  children: ReactNode;
  /** Suppress for a dense footnote where a glyph would be noise. */
  icon?: boolean;
  alert?: boolean;
  /** Which edge carries the rule. `none` for a band that is already inside a padded body. */
  divider?: 'top' | 'bottom' | 'none';
  className?: string;
}) {
  const Icon = BAND_ICON[tone];
  return (
    <div
      {...(alert ? { role: 'alert' as const } : {})}
      className={clsx(
        'flex items-start gap-1.5 px-3 py-1.5 text-caption',
        divider === 'top' ? 'border-t' : divider === 'bottom' ? 'border-b' : 'border-0',
        BAND_TONE[tone],
        className,
      )}
    >
      {icon && Icon && <Icon size={12} strokeWidth={1.5} className="mt-px shrink-0" aria-hidden />}
      <span className="min-w-0">{children}</span>
    </div>
  );
}

// ---------------------------------------------------------------- Absent
/**
 * A value the record does not carry.
 *
 * Four private renderings of this existed — `NotComputed()`, `Recorded`, a "not established"
 * span, and a bare `—` with a `title` — which meant the same absence looked like four different
 * things, and in one case like a zero.
 *
 * Naming the absence in words is the point: `0` and "not computed" are different facts, and an
 * audit surface that cannot tell them apart is not an audit surface. The em dash alone is never
 * enough, so the label is always rendered and the `title` carries the reason.
 */
export function Absent({
  label = 'not recorded',
  title = 'Not returned by this endpoint. An absent value, not zero.',
  className,
}: {
  label?: string;
  title?: string;
  className?: string;
}) {
  return (
    <span
      className={clsx('inline-flex items-center gap-1 text-caption text-fg-muted', className)}
      title={title}
    >
      <MinusCircle size={11} strokeWidth={1.5} className="shrink-0" aria-hidden />
      {label}
    </span>
  );
}

// ---------------------------------------------------------------- Button
type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

const BUTTON_VARIANT: Record<ButtonVariant, string> = {
  // Accent, not green: this is a brand affordance, not an operational verdict.
  primary:
    'border-accent-border bg-accent-subtle text-accent hover:border-accent hover:text-accent-hover',
  secondary: 'border-border-strong text-fg-secondary hover:border-accent-border hover:text-accent',
  ghost: 'border-transparent text-fg-muted hover:border-border-subtle hover:text-fg-secondary',
  danger: 'border-state-crit/30 bg-state-crit-bg text-state-crit hover:border-state-crit/50',
};

/**
 * The button the design system never had.
 *
 * Buttons were hand-rolled everywhere: `ErrorState`'s Retry recipe, the scenario builder's local
 * `PRIMARY_BUTTON`/`SECONDARY_BUTTON` constants, the approval queue's approve control, the plan
 * comparison's per-candidate select, the shell's "Run workflow". They agreed on roughly nothing —
 * `text-label` against `text-caption`, `border-border` against `border-border-strong`.
 *
 * The disabled state is a token change, never `opacity`. That is not a preference: `opacity-50`
 * over `--fg-muted` measures 2.22:1, and the browser gate only exempts elements below 0.3 opacity,
 * so a faded disabled button is a hard contrast failure. `text-fg-muted` on the panel surface is
 * 5.92:1 and reads as unavailable without becoming unreadable.
 *
 * `disabledReason` is surfaced as the `title` because most disabled controls in this product are
 * disabled for a *policy* reason — fixture mode, a missing justification, an ungranted approval —
 * and a control that refuses without saying why is the interaction people file bugs about.
 */
export function Button({
  variant = 'secondary',
  size = 'sm',
  icon: Icon,
  children,
  className,
  disabledReason,
  ...rest
}: {
  variant?: ButtonVariant;
  size?: 'sm' | 'md';
  icon?: typeof Info;
  children: ReactNode;
  disabledReason?: string;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className' | 'children'> & {
    className?: string;
  }) {
  return (
    <button
      type="button"
      {...rest}
      title={rest.disabled && disabledReason ? disabledReason : rest.title}
      className={clsx(
        'inline-flex items-center justify-center gap-1.5 rounded-sm border text-label uppercase',
        'transition-colors duration-hover ease-out',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
        size === 'md' ? 'px-3 py-1.5' : 'px-2 py-1',
        BUTTON_VARIANT[variant],
        // No opacity: see the note above. Tokens only.
        'disabled:cursor-not-allowed disabled:border-border-subtle disabled:bg-transparent disabled:text-fg-muted',
        className,
      )}
    >
      {Icon && <Icon size={12} strokeWidth={1.5} className="shrink-0" aria-hidden />}
      {children}
    </button>
  );
}

// ---------------------------------------------------------------- fields
/** One input recipe, so two fields cannot disagree about what a field looks like. */
export const FIELD_SHELL = clsx(
  'w-full rounded-sm border border-border-strong bg-inset px-2 py-1.5 text-body text-fg',
  'placeholder:text-fg-muted',
  'transition-colors duration-hover ease-out',
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
  'disabled:cursor-not-allowed disabled:border-border-subtle disabled:text-fg-muted',
);

/**
 * The justification a write is attributed to.
 *
 * Plan comparison and the approval queue each carried an identical copy of this block, down to
 * `maxLength={2000}` and the `aria-invalid` expression — and both were followed by the same
 * sentence about fixture mode. A recorded decision is only as good as its reason, so the field
 * that captures it is a component rather than a paragraph two screens happen to agree on.
 */
export function ReasonField({
  id,
  label = 'Reason',
  value,
  onChange,
  disabled,
  hint,
  placeholder,
  rows = 2,
  maxLength = 2000,
}: {
  id: string;
  label?: string;
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
  hint?: ReactNode;
  placeholder?: string;
  rows?: number;
  maxLength?: number;
}) {
  const empty = value.trim().length === 0;
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-caption uppercase text-fg-muted">
        {label} <span className="normal-case">(required)</span>
      </label>
      <textarea
        id={id}
        value={value}
        rows={rows}
        maxLength={maxLength}
        disabled={disabled}
        aria-invalid={empty}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className={FIELD_SHELL}
      />
      {hint && <p className="text-caption text-fg-muted">{hint}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- tables
export interface TableColumn {
  key: string;
  label: ReactNode;
  /** One clarifying line under the column name, in sentence case. */
  hint?: ReactNode;
  align?: 'left' | 'right';
  className?: string;
}

/**
 * The table shell: a horizontal scroller, the table, and a caption for assistive tech.
 *
 * The scroller is what keeps the browser gate's "no horizontal overflow at 1920" check passing
 * when a dense table is wider than its column, and the `sr-only` caption is what stops a
 * screen-reader user meeting an unexplained grid of figures.
 */
export function TableFrame({
  caption,
  children,
  className,
}: {
  /** Read to assistive tech only. Say what the rows are and what is not implied by their order. */
  caption: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx('overflow-x-auto', className)}>
      <table className="w-full border-collapse text-body">
        <caption className="sr-only">{caption}</caption>
        {children}
      </table>
    </div>
  );
}

/** The one table head recipe. Five copies of this existed, three of them subtly different. */
export function TableHead({ columns }: { columns: TableColumn[] }) {
  return (
    <thead>
      <tr className="border-b border-border-subtle bg-inset">
        {columns.map((column) => (
          <th
            key={column.key}
            scope="col"
            className={clsx(
              'px-3 py-2 align-bottom text-label font-medium uppercase text-fg-muted',
              column.align === 'right' ? 'text-right' : 'text-left',
              column.className,
            )}
          >
            {column.label}
            {column.hint && (
              <span className="mt-0.5 block text-caption font-normal normal-case text-fg-muted">
                {column.hint}
              </span>
            )}
          </th>
        ))}
      </tr>
    </thead>
  );
}

/**
 * The selected-row idiom, as a class string rather than a component.
 *
 * Three screens wrote this by hand. It stays a function because a table row needs its own
 * handlers, keys and `aria-*`, and wrapping `<tr>` in a component to pass all of them through
 * costs more than it saves.
 *
 * The 2px left edge is always present and merely transparent when unselected, so selecting a row
 * cannot shift the text inside it by two pixels.
 */
export function rowSelectionClass(selected: boolean, interactive = true): string {
  return clsx(
    'border-b border-l-2 border-border-subtle',
    interactive && 'transition-colors duration-hover ease-out',
    selected ? 'border-l-accent bg-raised' : 'border-l-transparent',
    interactive && !selected && 'hover:bg-raised',
  );
}

// ---------------------------------------------------------------- NotYetAvailable
/**
 * A documented not-yet state, rendered as an empty state rather than an error.
 *
 * Plan comparison and the approval queue had identical copies. The server's own `resolution` and
 * `message` are rendered verbatim rather than paraphrased here, because the backend is the thing
 * that knows what would make the data exist, and a sentence written in the console drifts from
 * the endpoint the moment either changes.
 */
export function NotYetAvailable({
  title,
  unavailable,
}: {
  title: string;
  unavailable: DataUnavailable;
}) {
  return (
    <EmptyState
      title={title}
      description={unavailable.resolution}
      action={
        <span className="flex flex-wrap items-center justify-center gap-2">
          <StateBadge status="pending" label={unavailable.code} />
          <span className="text-caption text-fg-muted">{unavailable.message}</span>
        </span>
      }
    />
  );
}

// ---------------------------------------------------------------- Timeline
/**
 * An ordered record, rendered as an actual timeline.
 *
 * The decision timeline and the replay ledger were both a `divide-y` list of rows: correct, and
 * indistinguishable from a table. A spine with a marker per entry is what makes "these events are
 * one sequence" legible at a glance, which is the entire claim the timeline exists to make.
 *
 * The spine is `aria-hidden` and drawn with borders, not a pseudo-element, so it costs nothing at
 * the contrast gate and disappears cleanly for the last entry.
 */
export function TimelineList({
  children,
  label,
  className,
}: {
  children: ReactNode;
  label: string;
  className?: string;
}) {
  return (
    <ol className={clsx('flex flex-col', className)} aria-label={label}>
      {children}
    </ol>
  );
}

const MARKER_TONE: Record<SurfaceTone, string> = {
  default: 'border-border-strong bg-surface',
  muted: 'border-border bg-surface',
  ok: 'border-state-ok bg-state-ok-bg',
  warn: 'border-state-warn bg-state-warn-bg',
  crit: 'border-state-crit bg-state-crit-bg',
  info: 'border-state-info bg-state-info-bg',
  accent: 'border-accent bg-accent-subtle',
};

/**
 * One entry on a `TimelineList`.
 *
 * `interactive` and `itemProps` exist for the replay ledger, which drives a roving tabindex
 * through `useKeyboardList` and needs to spread `tabIndex`/`data-active` onto the focusable
 * element itself. Passing them through keeps that behaviour exactly as it was rather than
 * reimplementing keyboard navigation here.
 */
export function TimelineItem({
  tone = 'muted',
  time,
  isLast = false,
  active = false,
  children,
  className,
}: {
  tone?: SurfaceTone;
  /** Already formatted. Use `utcClock` from `./format`. */
  time?: string | null;
  isLast?: boolean;
  active?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <li className={clsx('flex gap-2.5', className)}>
      {/* The gutter: a fixed-width column holding the time, the marker and the spine. */}
      <div className="flex shrink-0 flex-col items-end" aria-hidden>
        <div className="flex items-center gap-1.5 pt-2">
          {time ? (
            <MonoValue muted className="text-caption">
              {time}
            </MonoValue>
          ) : (
            <span className="text-caption text-fg-muted">—</span>
          )}
          <span
            className={clsx(
              'h-2 w-2 shrink-0 rounded-full border',
              MARKER_TONE[active ? 'accent' : tone],
            )}
          />
        </div>
        {!isLast && <span className="mr-[3px] w-px flex-1 bg-border-subtle" />}
      </div>
      <div className={clsx('min-w-0 flex-1 pb-2.5 pt-1.5', !isLast && 'pb-3')}>{children}</div>
    </li>
  );
}

// ---------------------------------------------------------------- misc layout
/**
 * A row of metric tiles that wraps predictably.
 *
 * Tiles were laid out with `flex gap-2`, `flex flex-wrap gap-2` and a `grid` depending on the
 * screen, so the same four figures wrapped differently in two places at the same width.
 */
export function StatStrip({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={clsx('flex flex-wrap items-stretch gap-2', className)}>{children}</div>;
}

/** The right-hand slot of a `Panel` header. Keeps action clusters from setting their own spacing. */
export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={clsx('flex flex-wrap items-center gap-2.5', className)}>{children}</div>;
}

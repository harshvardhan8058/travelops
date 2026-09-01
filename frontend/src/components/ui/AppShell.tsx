/**
 * App shell: 56px icon rail, 52px top bar, and the persistent Decision Timeline rail.
 *
 * The timeline appears on EVERY route by design. It is not a page you navigate to. That
 * single decision is what makes the product feel like an operating layer rather than a set
 * of forms.
 *
 * Owner: Stream E.
 */

import {
  Bot,
  Database,
  FileText,
  FlaskConical,
  GitFork,
  History,
  LayoutDashboard,
  ListChecks,
  MonitorPlay,
  Scale,
  GitCompare,
  Luggage,
  ShieldCheck,
  Users,
  Wand2,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import { clsx } from 'clsx';

import { MonoValue, ProvenanceDot, StateBadge } from './primitives';
import { PackStandingChip } from '@/features/policy-citation/PackStandingView';
import type { SystemMode } from '@/api/types';
import { deriveModeChips, type ModeChipView, type ModePosture } from '@/api/runtimeModes';

const NAV = [
  { to: '/', icon: LayoutDashboard, label: 'Ops Board' },
  { to: '/cascade/current', icon: GitFork, label: 'Cascade' },
  { to: '/incidents/INC-2026-0820-VOBL-01', icon: ListChecks, label: 'Recovery workspace' },
  { to: '/agent/INC-2026-0820-VOBL-01', icon: Bot, label: 'Agent operations' },
  { to: '/impact/INC-2026-0820-VOBL-01', icon: Users, label: 'Impact' },
  { to: '/assurance', icon: ShieldCheck, label: 'Approval queue' },
  { to: '/what-if/current', icon: FlaskConical, label: 'What-if' },
  { to: '/plans/INC-2026-0820-VOBL-01', icon: GitCompare, label: 'Plan comparison' },
  { to: '/policy/INC-2026-0820-VOBL-01', icon: Scale, label: 'Policy & citations' },
  { to: '/replay/INC-2026-0820-VOBL-01', icon: History, label: 'Replay' },
  { to: '/reports/INC-2026-0820-VOBL-01', icon: FileText, label: 'Report' },
  { to: '/sources', icon: Database, label: 'Provenance ledger' },
  // Phase 5. None of these is keyed on an incident, so they sit at the end rather than beside the
  // incident-scoped entries.
  //
  // The Scenario Center comes before the builder deliberately: it is where a demo starts, and
  // starting a catalogued simulation is the common case. Authoring one by hand is the specialist
  // path, so it follows.
  { to: '/scenarios', icon: MonitorPlay, label: 'Scenario center' },
  { to: '/scenarios/new', icon: Wand2, label: 'Scenario builder' },
  { to: '/passenger/K4X8YR', icon: Luggage, label: 'Passenger view' },
] as const;

function Rail() {
  return (
    <nav
      aria-label="Primary"
      className="flex w-rail shrink-0 flex-col items-center gap-1 border-r border-border-subtle bg-inset py-2"
    >
      {NAV.map(({ to, icon: Icon, label }) => (
        <NavLink
          key={to}
          to={to}
          title={label}
          aria-label={label}
          // `/scenarios` needs an exact match as much as `/` does: without it, `/scenarios/new`
          // would light up the Scenario Center too and the rail would show two active surfaces.
          end={to === '/' || to === '/scenarios'}
          className={({ isActive }) =>
            clsx(
              'flex h-9 w-9 items-center justify-center rounded-sm transition-colors duration-hover ease-out',
              'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
              isActive
                ? 'bg-accent-subtle text-accent'
                : 'text-fg-muted hover:bg-raised hover:text-fg-secondary',
            )
          }
        >
          <Icon size={20} strokeWidth={1.5} aria-hidden />
        </NavLink>
      ))}
    </nav>
  );
}

/**
 * The one place a posture becomes a colour.
 *
 * Typed as a total `Record`, so adding a posture to the contract fails the build here rather than
 * falling through to an unstyled chip. `off` and `simulated` share amber because they share a
 * meaning an operator acts on — nothing real is happening on that adapter — while the chip's value
 * keeps them distinguishable.
 */
const POSTURE_TONE: Record<ModePosture, string> = {
  live: 'text-state-ok',
  fixture: 'text-state-info',
  simulated: 'text-state-warn',
  off: 'text-state-warn',
  unknown: 'text-fg-muted',
};

function ModeChip({ chip }: { chip: ModeChipView }) {
  // The server's own degradation sentence when there is one, so the tooltip never paraphrases it.
  const title = chip.degradation ? `${chip.detail}\n\n${chip.degradation}` : chip.detail;

  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-sm border border-border-subtle bg-inset px-1.5 py-0.5"
      title={title}
    >
      <span className="text-caption uppercase text-fg-muted">{chip.label}</span>
      <MonoValue className={clsx('uppercase', POSTURE_TONE[chip.posture])}>
        {chip.value ?? '…'}
      </MonoValue>
      {chip.degradation && (
        <span
          aria-label={`${chip.label} degraded`}
          className="text-caption leading-none text-state-warn"
        >
          !
        </span>
      )}
    </span>
  );
}

function TopBar({ mode, clock }: { mode?: SystemMode; clock: string }) {
  const chips = deriveModeChips(mode);

  return (
    <header className="flex h-topbar min-w-0 shrink-0 items-center gap-3 border-b border-border-subtle bg-surface px-3">
      <div className="flex shrink-0 items-baseline gap-2">
        <span className="text-subtitle font-semibold text-fg">TravelOps</span>
        <span className="text-caption uppercase text-fg-muted">Operations</span>
      </div>

      {/*
        Policy standing must never be derived here from `mode.policy_mode === 'verified'`.
        Runtime mode is not review status; the shared component owns the contract-backed standing
        interpretation so the shell and policy screen cannot diverge.

        The pack label is secondary shell context and may truncate visually, but the complete label
        and standing explanation remain in the chip's accessible title and screen-reader copy.
      */}
      <div className="min-w-0 flex-1 overflow-hidden whitespace-nowrap">
        <PackStandingChip uiLabel={mode?.policy_pack?.ui_label} />
      </div>

      <div className="ml-2 flex flex-wrap items-center gap-2">
        {chips.map((chip) => (
          <ModeChip key={chip.label} chip={chip} />
        ))}
      </div>

      <div className="flex shrink-0 items-center gap-3">
        {mode && (
          <span className="flex items-center gap-1.5" title="Assurance configuration">
            <ProvenanceDot
              kind={mode.assurance.workflow_executable ? 'real' : 'unavailable'}
              provider="assurance"
              sourceRef={mode.assurance.config_version ?? undefined}
            />
            <span className="text-caption uppercase text-fg-muted">
              gate {mode.assurance.config_version ?? 'missing'}
            </span>
          </span>
        )}
        <MonoValue muted>{clock}</MonoValue>
      </div>
    </header>
  );
}

function SourceStrip({ mode }: { mode?: SystemMode }) {
  const chips = deriveModeChips(mode);

  return (
    <footer
      aria-label="Effective source and notification modes"
      className="flex min-w-0 shrink-0 flex-wrap items-center gap-x-2 gap-y-1 border-t border-border-subtle bg-surface px-3 py-1.5"
    >
      <NavLink
        to="/sources"
        className="mr-1 shrink-0 rounded-sm text-caption uppercase text-fg-muted underline decoration-dotted underline-offset-2 transition-colors duration-hover ease-out hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        aria-label="Open source ledger for effective mode provenance"
      >
        effective modes
      </NavLink>
      {chips.map((chip) => (
        <ModeChip key={chip.label} chip={chip} />
      ))}
      <span className="min-w-0 text-caption text-fg-muted">
        Recorded runtime capability; source records and deliveries remain auditable in their own
        views.
      </span>
    </footer>
  );
}

export function AppShell({
  mode,
  clock,
  timeline,
  blockedCount,
  blockedSingular,
  blockedPlural,
  children,
}: {
  mode?: SystemMode;
  clock: string;
  timeline: ReactNode;
  blockedCount: number;
  blockedSingular: string;
  blockedPlural: string;
  children: ReactNode;
}) {
  const degradations = mode?.degradations ?? [];

  return (
    <div className="flex h-full min-h-0 w-full min-w-0 overflow-hidden bg-base text-fg">
      <Rail />

      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar mode={mode} clock={clock} />

        {/* The system never hides degradation to look healthier. */}
        {degradations.length > 0 && (
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-state-warn/30 bg-state-warn-bg px-3 py-1.5">
            <StateBadge status="degraded" label="degraded" />
            {degradations.map((detail) => (
              <span key={detail} className="text-caption text-state-warn">
                {detail}
              </span>
            ))}
          </div>
        )}

        <div className="flex min-h-0 min-w-0 flex-1">
          <main className="min-w-0 flex-1 overflow-auto p-3">{children}</main>
          <aside
            aria-label="Decision timeline"
            className="hidden min-h-0 w-timeline shrink-0 overflow-hidden border-l border-border-subtle bg-surface 2xl:block"
          >
            {timeline}
          </aside>
        </div>

        {/* A blocked action must never be discoverable only by navigating to a page. */}
        {blockedCount > 0 && (
          <div className="flex min-w-0 shrink-0 flex-wrap items-center gap-2 border-t border-state-warn/30 bg-state-warn-bg px-3 py-1.5">
            <StateBadge status="needs_human" label="awaiting approval" />
            <span className="text-body text-state-warn">
              <MonoValue className="text-state-warn">{blockedCount}</MonoValue>{' '}
              {blockedCount === 1 ? blockedSingular : blockedPlural}
            </span>
            <NavLink
              to="/assurance"
              className="ml-auto rounded-sm border border-state-warn/40 px-2 py-0.5 text-label uppercase text-state-warn transition-colors duration-hover ease-out hover:bg-state-warn/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              Review
            </NavLink>
          </div>
        )}

        <SourceStrip mode={mode} />
      </div>
    </div>
  );
}

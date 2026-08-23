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
  Database,
  FileText,
  FlaskConical,
  GitFork,
  History,
  LayoutDashboard,
  ListChecks,
  Scale,
  GitCompare,
  ShieldCheck,
  Users,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import { clsx } from 'clsx';

import { MonoValue, ProvenanceDot, StateBadge } from './primitives';
import type { SystemMode } from '@/api/types';

const NAV = [
  { to: '/', icon: LayoutDashboard, label: 'Ops Board' },
  { to: '/cascade/current', icon: GitFork, label: 'Cascade' },
  { to: '/incidents/INC-2026-0820-VOBL-01', icon: ListChecks, label: 'Recovery workspace' },
  { to: '/impact/INC-2026-0820-VOBL-01', icon: Users, label: 'Impact' },
  { to: '/assurance', icon: ShieldCheck, label: 'Approval queue' },
  { to: '/what-if/current', icon: FlaskConical, label: 'What-if' },
  { to: '/plans/INC-2026-0820-VOBL-01', icon: GitCompare, label: 'Plan comparison' },
  { to: '/policy/INC-2026-0820-VOBL-01', icon: Scale, label: 'Policy & citations' },
  { to: '/replay/INC-2026-0820-VOBL-01', icon: History, label: 'Replay' },
  { to: '/reports/INC-2026-0820-VOBL-01', icon: FileText, label: 'Report' },
  { to: '/sources', icon: Database, label: 'Provenance ledger' },
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
          end={to === '/'}
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

function ModeChip({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-sm border border-border-subtle bg-inset px-1.5 py-0.5">
      <span className="text-caption uppercase text-fg-muted">{label}</span>
      <MonoValue className={clsx('uppercase', tone)}>{value}</MonoValue>
    </span>
  );
}

function TopBar({ mode, clock }: { mode?: SystemMode; clock: string }) {
  const llm = mode?.llm_mode ?? '…';
  // Off is not an error state — it is a supported operating mode and a demo asset.
  const llmTone =
    llm === 'live' ? 'text-state-ok' : llm === 'fixture' ? 'text-state-info' : 'text-state-warn';

  return (
    <header className="flex h-topbar shrink-0 items-center gap-3 border-b border-border-subtle bg-surface px-3">
      <div className="flex items-baseline gap-2">
        <span className="text-subtitle font-semibold text-fg">TravelOps</span>
        <span className="text-caption uppercase text-fg-muted">Operations</span>
      </div>

      <div className="ml-2 flex items-center gap-2">
        <ModeChip label="LLM" value={llm} tone={llmTone} />
        <ModeChip label="WX" value={mode?.weather_mode ?? '…'} />
        <ModeChip label="NOTIFY" value={mode?.notification_mode ?? '…'} />
      </div>

      {/*
        Renders the pack's real status verbatim. Never upgraded by hand, and never case-transformed:
        the label is a regulation's name ("MoCA", "CAR"), so `uppercase` would misquote it.
      */}
      {mode?.policy_pack?.ui_label && (
        <span
          className={clsx(
            'truncate rounded-sm border px-1.5 py-0.5 text-caption',
            mode.policy_mode === 'verified'
              ? 'border-state-ok/30 bg-state-ok-bg text-state-ok'
              : 'border-state-warn/30 bg-state-warn-bg text-state-warn',
          )}
          title={mode.policy_pack.ui_label}
        >
          {mode.policy_pack.ui_label}
        </span>
      )}

      <div className="ml-auto flex items-center gap-3">
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

export function AppShell({
  mode,
  clock,
  timeline,
  blockedCount,
  children,
}: {
  mode?: SystemMode;
  clock: string;
  timeline: ReactNode;
  blockedCount: number;
  children: ReactNode;
}) {
  const degradations = mode?.degradations ?? [];

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-base text-fg">
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

        <div className="flex min-h-0 flex-1">
          <main className="min-w-0 flex-1 overflow-auto p-3">{children}</main>
          <aside
            aria-label="Decision timeline"
            className="hidden w-timeline shrink-0 overflow-auto border-l border-border-subtle bg-surface xl:block"
          >
            {timeline}
          </aside>
        </div>

        {/* A blocked action must never be discoverable only by navigating to a page. */}
        {blockedCount > 0 && (
          <div className="flex shrink-0 items-center gap-2 border-t border-state-warn/30 bg-state-warn-bg px-3 py-1.5">
            <StateBadge status="needs_human" label="awaiting approval" />
            <span className="text-body text-state-warn">
              <MonoValue className="text-state-warn">{blockedCount}</MonoValue>{' '}
              {blockedCount === 1 ? 'action requires' : 'actions require'} an operator decision
            </span>
            <NavLink
              to="/assurance"
              className="ml-auto rounded-sm border border-state-warn/40 px-2 py-0.5 text-label uppercase text-state-warn transition-colors duration-hover ease-out hover:bg-state-warn/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              Review
            </NavLink>
          </div>
        )}
      </div>
    </div>
  );
}

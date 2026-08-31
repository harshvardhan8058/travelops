/**
 * Route table and shell wiring.
 *
 * The Decision Timeline and the blocked-actions bar live above the router outlet and follow the
 * incident named in the path, so a rail showing one incident beside a workspace showing another
 * is impossible.
 *
 * Owner: Stream D.
 */

import { useEffect, useState } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';
import { AppShell } from '@/components/ui/AppShell';
import { CommandCenter } from '@/features/command-center/CommandCenter';
import { CascadeExplorer } from '@/features/cascade/CascadeExplorer';
import { RecoveryWorkspace } from '@/features/incident/RecoveryWorkspace';
import { PolicyScreen } from '@/features/policy-citation/PolicyScreen';
import { ReplayScreen } from '@/features/replay/ReplayScreen';
import { SourcesLedger } from '@/features/sources/SourcesLedger';
import { DecisionTimeline } from '@/features/timeline/DecisionTimeline';
import { GroupApprovalQueue } from '@/features/assurance/GroupApprovalQueue';
import { PlanComparison } from '@/features/plans/PlanComparison';
import { ReportScreen } from '@/features/reports/ReportScreen';
import { ImpactExplorer } from '@/features/impact/ImpactExplorer';
import { WhatIfScreen } from '@/features/cascade/WhatIfScreen';
import { AgentConsole } from '@/features/agent/AgentConsole';
import { ScenarioBuilder } from '@/features/scenario-builder/ScenarioBuilder';
import { PassengerDisruptionView } from '@/features/passenger/PassengerDisruptionView';

/** Used only when the current route names no incident, e.g. the Command Center. */
const DEMO_INCIDENT = 'INC-2026-0820-VOBL-01';

function useRouteIncidentId(fallback: string): string {
  const { pathname } = useLocation();
  // `impact` joins the list because that screen is incident-scoped too. There is no `/replay/group`
  // route: Replay carries its own incident/group toggle, and a second entry path to the same screen
  // would be the duplicate seam this integration exists to avoid.
  const match = /^\/(?:incidents|policy|replay|reports|plans|impact|agent)\/([^/]+)/.exec(pathname);
  const captured = match?.[1];
  return captured ? decodeURIComponent(captured) : fallback;
}

function useUtcClock(): string {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1_000);
    return () => window.clearInterval(timer);
  }, []);
  return `${now.toISOString().slice(11, 19)}Z`;
}

export function App() {
  const clock = useUtcClock();
  const { pathname } = useLocation();
  const incidentId = useRouteIncidentId(DEMO_INCIDENT);
  const usesGroupApprovalScope =
    pathname === '/' ||
    pathname === '/assurance' ||
    /^\/(?:cascade|what-if|passenger)\//.test(pathname);

  const { data: mode } = useQuery({
    queryKey: ['system-mode'],
    queryFn: api.systemMode,
    refetchInterval: 30_000,
  });

  const { data: assurance } = useQuery({
    queryKey: ['assurance', incidentId],
    queryFn: () => api.assurance(incidentId),
    refetchInterval: 10_000,
    enabled: !usesGroupApprovalScope,
  });

  const { data: currentGroup } = useQuery({
    queryKey: ['current-group'],
    queryFn: api.currentGroup,
    refetchInterval: 10_000,
    enabled: usesGroupApprovalScope,
  });

  return (
    <AppShell
      mode={mode}
      clock={clock}
      blockedCount={
        usesGroupApprovalScope
          ? (currentGroup?.awaiting_approval_count ?? 0)
          : (assurance?.awaiting_approval_count ?? 0)
      }
      blockedSingular={
        usesGroupApprovalScope
          ? 'incident awaits operator approval in the current group'
          : `action requires an operator decision for ${incidentId}`
      }
      blockedPlural={
        usesGroupApprovalScope
          ? 'incidents await operator approval in the current group'
          : `actions require an operator decision for ${incidentId}`
      }
      timeline={<DecisionTimeline incidentId={incidentId} />}
    >
      <Routes>
        <Route path="/" element={<CommandCenter />} />
        <Route path="/cascade/:groupId" element={<CascadeExplorer />} />
        <Route path="/incidents/:incidentId" element={<RecoveryWorkspace />} />
        {/*
         * The agent console is incident-scoped and joins the regex above, so the Decision Timeline
         * beside it follows the same incident. It reads only contracts that already exist.
         */}
        <Route path="/agent/:incidentId" element={<AgentConsole />} />
        <Route path="/policy/:incidentId" element={<PolicyScreen />} />
        <Route path="/replay/:incidentId" element={<ReplayScreen />} />
        <Route path="/impact/:incidentId" element={<ImpactExplorer />} />
        {/*
         * What-If has a route of its own as well as its panel inside the Cascade Explorer. It is a
         * question an operator asks deliberately, and a surface it can be sent to is the
         * difference between that and something found by scrolling.
         */}
        <Route path="/what-if/:groupId" element={<WhatIfScreen />} />
        <Route path="/sources" element={<SourcesLedger />} />
        {/*
         * `/assurance` and `/plans/:id` are real as of Phase 2: the group-scoped assurance endpoint
         * and the plan decision contract landed, so neither is a placeholder any more.
         *
         * Still blocked on effort rather than contracts:
         *   /reports/:id    executive report — buildable, sequenced last
         */}
        <Route path="/assurance" element={<GroupApprovalQueue />} />
        <Route path="/plans/:incidentId" element={<PlanComparison />} />
        <Route path="/reports/:incidentId" element={<ReportScreen />} />
        {/*
         * Phase 5. Neither route is incident-scoped, so neither joins the regex above:
         *
         *   /scenarios/new        authors a disruption that does not exist yet, so there is no
         *                         incident for the timeline to follow.
         *   /passenger/:ref       is keyed on a BOOKING reference, not an incident reference. Adding
         *                         it to that alternation would feed a PNR to the assurance query and
         *                         put a 404 in the rail beside a screen that rendered fine.
         *
         * The passenger route is keyed on a booking reference, not an incident reference. It reads
         * the current group's persisted passenger-priority records and keeps booking outcome fields
         * explicitly unavailable because no passenger outcome endpoint serves them.
         */}
        <Route path="/scenarios/new" element={<ScenarioBuilder />} />
        <Route path="/passenger/:bookingRef" element={<PassengerDisruptionView />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}

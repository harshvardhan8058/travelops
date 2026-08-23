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

/** Used only when the current route names no incident, e.g. the Command Center. */
const DEMO_INCIDENT = 'INC-2026-0820-VOBL-01';

function useRouteIncidentId(fallback: string): string {
  const { pathname } = useLocation();
  // `impact` joins the list because that screen is incident-scoped too. There is no `/replay/group`
  // route: Replay carries its own incident/group toggle, and a second entry path to the same screen
  // would be the duplicate seam this integration exists to avoid.
  const match = /^\/(?:incidents|policy|replay|reports|plans|impact)\/([^/]+)/.exec(pathname);
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
  const incidentId = useRouteIncidentId(DEMO_INCIDENT);

  const { data: mode } = useQuery({
    queryKey: ['system-mode'],
    queryFn: api.systemMode,
    refetchInterval: 30_000,
  });

  const { data: assurance } = useQuery({
    queryKey: ['assurance', incidentId],
    queryFn: () => api.assurance(incidentId),
    refetchInterval: 10_000,
  });

  return (
    <AppShell
      mode={mode}
      clock={clock}
      blockedCount={assurance?.awaiting_approval_count ?? 0}
      timeline={<DecisionTimeline incidentId={incidentId} />}
    >
      <Routes>
        <Route path="/" element={<CommandCenter />} />
        <Route path="/cascade/:groupId" element={<CascadeExplorer />} />
        <Route path="/incidents/:incidentId" element={<RecoveryWorkspace />} />
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
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}

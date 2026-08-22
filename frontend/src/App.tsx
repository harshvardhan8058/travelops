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
import { StreamPlaceholder } from '@/features/placeholder/StreamPlaceholder';

/** Used only when the current route names no incident, e.g. the Command Center. */
const DEMO_INCIDENT = 'INC-2026-0820-VOBL-01';

function useRouteIncidentId(fallback: string): string {
  const { pathname } = useLocation();
  const match = /^\/(?:incidents|policy|replay|reports)\/([^/]+)/.exec(pathname);
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
        <Route path="/sources" element={<SourcesLedger />} />
        {/*
         * Not yet built, and blocked on backend contracts rather than on effort:
         *   /assurance      group-scoped approval queue — needs FE-8 and FE-10
         *   /reports/:id    executive report — buildable, sequenced after C2-8
         */}
        <Route
          path="/assurance"
          element={
            <StreamPlaceholder
              screen="Group approval queue"
              owner="Stream D"
              spec="Phase 2 C2-5: blocked on FE-8 (group-scoped assurance) and FE-10 (plan decision contract)"
            />
          }
        />
        <Route
          path="/reports/:incidentId"
          element={
            <StreamPlaceholder
              screen="Executive report"
              owner="Stream D"
              spec="docs/27-ui-specification.md screen 7, sequenced after C2-8"
            />
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}

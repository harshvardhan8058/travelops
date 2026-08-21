/**
 * Route table and shell wiring.
 *
 * Wave 0 implements the Ops Board and the Decision Timeline. Every other route renders a
 * placeholder naming its owning stream and its specification section, so nobody has to
 * guess what belongs where.
 *
 * Owner: Stream E.
 */

import { useEffect, useState } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';
import { AppShell } from '@/components/ui/AppShell';
import { OpsBoard } from '@/features/ops-board/OpsBoard';
import { RecoveryWorkspace } from '@/features/incident/RecoveryWorkspace';
import { DecisionTimeline } from '@/features/timeline/DecisionTimeline';
import { StreamPlaceholder } from '@/features/placeholder/StreamPlaceholder';

/** Used only when the current route names no incident, e.g. the Ops Board. */
const DEMO_INCIDENT = 'INC-2026-0820-VOBL-01';

/**
 * The Decision Timeline and the blocked-actions bar live in the shell, above the router
 * outlet, so they cannot read route params. Matching the path keeps them following whatever
 * incident the operator is actually looking at — a timeline showing a different incident from
 * the workspace beside it would be actively misleading.
 */
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

  // Drives the persistent blocked-actions bar. Shares its cache key with the workspace, so a
  // recorded decision refreshes both.
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
        <Route path="/" element={<OpsBoard />} />
        <Route
          path="/cascade/:groupId"
          element={
            <StreamPlaceholder
              screen="Cascade view"
              owner="Stream F"
              spec="docs/27-ui-specification.md screen 3"
            />
          }
        />
        <Route path="/incidents/:incidentId" element={<RecoveryWorkspace />} />
        <Route
          path="/assurance"
          element={
            <StreamPlaceholder
              screen="Approval queue"
              owner="Stream F"
              spec="docs/27-ui-specification.md screen 4"
            />
          }
        />
        <Route
          path="/policy/:incidentId"
          element={
            <StreamPlaceholder
              screen="Policy and citation"
              owner="Stream F"
              spec="docs/27-ui-specification.md screen 5"
            />
          }
        />
        <Route
          path="/replay/:incidentId"
          element={
            <StreamPlaceholder
              screen="Timeline replay"
              owner="Stream E"
              spec="docs/27-ui-specification.md screen 6"
            />
          }
        />
        <Route
          path="/reports/:incidentId"
          element={
            <StreamPlaceholder
              screen="Executive report"
              owner="Stream F"
              spec="docs/27-ui-specification.md screen 7"
            />
          }
        />
        <Route
          path="/sources"
          element={
            <StreamPlaceholder
              screen="Provenance ledger"
              owner="Stream E"
              spec="docs/27-ui-specification.md screen 8"
            />
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}

/**
 * Which approval count the shell may report for a route, and therefore which contract it polls.
 *
 * Three scopes, because three different things can be true of a screen:
 *
 *   `incident`  the route names one incident, so the shell reports that incident's awaiting-approval
 *               count and the Decision Timeline follows it.
 *   `group`     the route is about the network rather than one incident, so the count is read from
 *               `/incident-groups/current` instead of falling through to a hardcoded demo incident.
 *   `none`      the route is about no disruption at all: the Scenario Center, the scenario author's
 *               blank page, and the source ledger. There is nothing to count, so nothing is polled.
 *
 * The `none` scope is not a cosmetic choice. On a freshly migrated and seeded stack no incident
 * group exists yet, so `/incident-groups/current` answers 404 by design — it "excludes
 * authored-but-unstarted empty groups". A screen whose entire premise is that nothing has happened
 * yet would therefore open with a failed request in the console. The Scenario Center is the demo's
 * documented starting point and the scenario author's page is the primary-journey verification's
 * starting state, so both must be quiet on a dataset where no cascade has been started.
 *
 * These screens still are not incident-scoped, which is the property the routing rule cares about:
 * `none` means they inherit no incident, exactly like `group` and unlike the old fall-through to a
 * hardcoded demo incident. The approval count returns as soon as the operator follows a started
 * cascade into a screen that is about it.
 */
export type ApprovalScope = 'incident' | 'group' | 'none';

/** Screens that are about no disruption at all, so no approval count applies. */
const NO_APPROVAL_ROUTES = new Set(['/scenarios', '/scenarios/new', '/sources']);

export function approvalScopeFor(pathname: string, routeIncidentId: string | null): ApprovalScope {
  if (NO_APPROVAL_ROUTES.has(pathname)) return 'none';
  // `/passenger/:bookingRef` never reaches this function: `App()` branches to it, in its own
  // shell, before the operator console (the only caller of `approvalScopeFor`) even renders.
  const network =
    routeIncidentId === null ||
    pathname === '/' ||
    pathname === '/assurance' ||
    /^\/scenarios(?:\/|$)/.test(pathname) ||
    /^\/(?:cascade|what-if)\//.test(pathname);
  return network ? 'group' : 'incident';
}

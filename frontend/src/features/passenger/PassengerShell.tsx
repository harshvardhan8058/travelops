/**
 * The passenger route's own shell — deliberately not `AppShell`.
 *
 * `/passenger/:bookingRef` used to render inside the full operator console: the 56px icon rail,
 * the mode chips, the Decision Timeline, the blocked-actions bar. None of those belong in front of
 * a passenger — a rail of links to Cascade, Assurance and Plan Comparison is not a "customer
 * portal", it is the operations console with one extra route bolted on, and every operational
 * control it exposes is a control a passenger cannot use and should not see.
 *
 * This shell is deliberately small: a single narrow column, a header naming the product, and a
 * footer stating where to get real-time help. Nothing here reads a query — every fact on the page
 * comes from `PassengerDisruptionView` itself, so this file has nothing to get wrong.
 *
 * Owner: Stream D.
 */

import type { ReactNode } from 'react';

export function PassengerShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-full w-full justify-center bg-base text-fg">
      <div className="flex w-full max-w-2xl flex-col">
        <header className="flex items-center justify-between border-b border-border-subtle px-4 py-3 sm:px-6">
          <span className="text-subtitle font-semibold text-fg">TravelOps</span>
          <span className="text-caption uppercase tracking-wide text-fg-muted">Trip status</span>
        </header>

        <main className="min-w-0 flex-1 px-4 py-5 sm:px-6">{children}</main>

        <footer className="border-t border-border-subtle px-4 py-3 text-caption text-fg-muted sm:px-6">
          This page shows what TravelOps has on record for your trip and checks for changes on its
          own. For anything urgent, contact your airline directly rather than relying on this page.
        </footer>
      </div>
    </div>
  );
}

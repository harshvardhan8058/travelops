/**
 * Route placeholders for screens owned by Stream F.
 *
 * Deliberately states WHO builds it and WHERE the spec is, rather than showing an empty
 * page. A blank route during a demo reads as broken.
 */

import { EmptyState } from '@/components/ui/primitives';

export function StreamPlaceholder({
  screen,
  owner,
  spec,
}: {
  screen: string;
  owner: string;
  spec: string;
}) {
  return (
    <EmptyState
      title={`${screen} — not built yet`}
      description={`Owned by ${owner}. Specification: ${spec}. Wave 0 ships the shell, tokens, typed client and fixtures so this screen can be built without waiting for the backend.`}
    />
  );
}

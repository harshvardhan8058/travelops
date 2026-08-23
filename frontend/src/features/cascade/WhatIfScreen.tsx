/**
 * What-If — `/what-if/:groupId`. A surface for the panel, not a second implementation of it.
 *
 * `WhatIfPanel` already renders the bounded zero-write re-evaluation and lives inside the Cascade
 * Explorer. It is reused verbatim here; the only thing this screen adds is a route, the group
 * resolution that `current` needs, and the standing boundary statement above it.
 *
 * Why a route at all when the panel is already reachable: a question an operator asks deliberately
 * should be somewhere they can be *sent*, and the Explorer is a long screen where the panel is
 * found by scrolling. Duplicating the panel to achieve that would be the wrong fix — one
 * implementation, two entry points.
 *
 * Owner: Stream D.
 */

import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { FlaskConical } from 'lucide-react';

import { api, ApiError } from '@/api/client';
import { ErrorState, LoadingState, MonoValue, Panel } from '@/components/ui/primitives';
import { WhatIfPanel } from './WhatIfPanel';

export function WhatIfScreen() {
  const { groupId = '' } = useParams();

  // `current` is the nav's stable link target: the shell cannot know a group reference, and
  // hardcoding one in the nav would rot the moment the dataset changes.
  const isAlias = groupId === 'current' || groupId.length === 0;
  const currentQuery = useQuery({
    queryKey: ['current-group'],
    queryFn: api.currentGroup,
    enabled: isAlias,
  });

  const groupRef = isAlias ? currentQuery.data?.reference : groupId;

  if (isAlias && currentQuery.isLoading) {
    return (
      <Panel title="What-if">
        <div className="h-[420px]">
          <LoadingState label="Resolving the current disruption" />
        </div>
      </Panel>
    );
  }

  if (!groupRef) {
    const error = currentQuery.error instanceof ApiError ? currentQuery.error : null;
    return (
      <ErrorState
        code={error?.code ?? 'NOT_FOUND'}
        message={
          error?.message ??
          'No disruption group is open, so there is nothing to re-evaluate. What-if reads recorded ' +
            'evidence; without a group there is none.'
        }
        correlationId={error?.correlationId ?? null}
        onRetry={() => void currentQuery.refetch()}
      />
    );
  }

  return (
    <div className="flex min-h-0 flex-col gap-3">
      {/* Untitled deliberately: `WhatIfPanel` below carries the heading, and two panels both
       * titled "What-if" reads as a rendering fault rather than as a lead-in. */}
      <Panel>
        <div className="flex items-start gap-2.5 px-3 py-2.5">
          <FlaskConical
            size={15}
            strokeWidth={1.5}
            className="mt-0.5 shrink-0 text-fg-muted"
            aria-hidden
          />
          <div className="flex min-w-0 flex-col gap-1">
            <p className="max-w-[86ch] text-body text-fg-secondary">
              Substitute a recorded input and ask what the same deterministic rules would have
              found. This is a re-evaluation of evidence that already exists — not a forecast, not a
              simulation, and not a model of the network. It writes nothing, and the server states
              both facts in its own response rather than leaving them to this page to claim.
            </p>
            <span className="text-caption uppercase text-fg-muted">
              over the recorded evidence of <MonoValue muted>{groupRef}</MonoValue>
            </span>
          </div>
        </div>
      </Panel>

      <WhatIfPanel groupRef={groupRef} />
    </div>
  );
}

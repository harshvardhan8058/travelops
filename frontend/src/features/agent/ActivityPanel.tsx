/**
 * Complete agent activity, under an actor lens.
 *
 * The Replay screen already owns the scrubber and the folded state at a cursor. What this adds is
 * the question that only matters once a system acts on its own: **who did each of these things.**
 * `actor_kind` is published per frame, so the split between the orchestrator, the services and a
 * named person is a fact rather than an impression.
 *
 * Read-only by contract, and it says so: `is_read_only` is a `Literal[true]` on the replay
 * response, and the note the server sends is rendered verbatim rather than paraphrased.
 *
 * Owner: Stream D.
 */

import { Metric } from '@/components/ui/Metric';
import { actorActivityDerivation, countDerivation } from '@/components/ui/derivation';
import { EmptyState, MonoValue, Panel } from '@/components/ui/primitives';
import { useKeyboardList } from '@/hooks/useKeyboardList';
import type { ServerReplayResponse } from '@/api/types';
import { ActorChip } from './ActorChip';
import { activityByActor } from './steps';

export function ActivityPanel({ replay, scope }: { replay: ServerReplayResponse; scope: string }) {
  const activity = activityByActor(replay.frames);
  const keyboard = useKeyboardList({ count: replay.frames.length });

  return (
    <Panel
      title="Agent activity"
      className="flex h-full min-h-0 flex-col overflow-hidden"
      actions={
        <span className="flex items-center gap-2 text-caption text-fg-muted">
          <Metric
            value={replay.frame_count}
            derivation={countDerivation('Recorded frames', replay.frame_count, {
              endpoint: 'GET /incidents/{ref}/replay',
              field: 'frame_count',
              note: 'Every frame is a persisted decision-log record. The console adds none and reorders none.',
            })}
          />
          <span>frames</span>
        </span>
      }
    >
      {replay.frames.length === 0 ? (
        <EmptyState
          title="Nothing has been recorded yet"
          description="No decision-log record names this incident, so there is no activity to replay."
        />
      ) : (
        <>
          {/* Who acted, at a glance. The figure that keeps an autonomous console honest. */}
          <div className="flex flex-wrap gap-x-4 gap-y-1 border-b border-border-subtle px-3 py-2">
            {activity.map((row) => (
              <span key={row.actorKind} className="flex items-center gap-1.5">
                <ActorChip actorKind={row.actorKind} />
                <Metric
                  value={row.frames}
                  derivation={actorActivityDerivation({
                    actorKind: row.actorKind,
                    frames: row.frames,
                    frameCount: replay.frame_count,
                    actors: row.actors,
                    scope,
                  })}
                />
              </span>
            ))}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            <ol
              ref={keyboard.containerRef as React.RefObject<HTMLOListElement>}
              onKeyDown={keyboard.onKeyDown}
              aria-label="Recorded agent activity, oldest first"
            >
              {replay.frames.map((frame, index) => (
                <li
                  key={`${frame.sequence}-${frame.occurred_at}`}
                  className="border-b border-border-subtle last:border-b-0"
                >
                  <button
                    type="button"
                    {...keyboard.itemProps(index)}
                    className="flex w-full flex-col items-start gap-0.5 px-3 py-1.5 text-left transition-colors duration-hover ease-out hover:bg-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    <span className="flex w-full flex-wrap items-center gap-1.5">
                      <MonoValue muted className="text-caption">
                        {frame.sequence}
                      </MonoValue>
                      <MonoValue muted className="text-caption">
                        {frame.occurred_at.slice(11, 19)}Z
                      </MonoValue>
                      <ActorChip actorKind={frame.actor_kind} />
                      {frame.decision_scope && (
                        <span className="text-caption uppercase text-fg-secondary">
                          {frame.decision_scope}-scoped
                        </span>
                      )}
                    </span>
                    <span className="flex w-full flex-wrap items-baseline gap-1.5">
                      <MonoValue muted className="text-caption break-all">
                        {frame.event_type}
                      </MonoValue>
                      {frame.state_after && (
                        <MonoValue muted className="text-caption">
                          {frame.state_before ?? 'none'} {'->'} {frame.state_after}
                        </MonoValue>
                      )}
                    </span>
                    <span className="text-caption text-fg-secondary">{frame.summary}</span>
                  </button>
                </li>
              ))}
            </ol>
          </div>

          <p className="border-t border-border-subtle px-3 py-1.5 text-caption text-fg-muted">
            {replay.note}
          </p>
        </>
      )}
    </Panel>
  );
}

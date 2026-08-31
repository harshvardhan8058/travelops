/**
 * Who acted — deliberately never a state colour.
 *
 * The console has two orthogonal axes and conflating them is the classic agentic-UI failure: an
 * operator cannot tell whether amber means "a person is needed" or "a person did this". So
 * operational state keeps `StateBadge` and its state palette, and identity gets this: neutral
 * tokens, one icon per `actor_kind`, and a weight difference.
 *
 * A human act is rendered heavier and brighter than a machine act. That is the whole point of the
 * component. On a projector, "the orchestrator did eleven of these and a named person did one" has
 * to be readable across the room, and it must not be readable as a status.
 *
 * `actor_kind` is the backend's own five-value mapping from `app/api/actors.py`. An unrecognised
 * kind renders with a neutral fallback icon rather than being dropped, because a missing actor is
 * worse than an unstyled one.
 *
 * **This is now the only actor chip in the console.** It previously existed alongside two private
 * copies of the same palette, in `features/timeline/DecisionTimeline.tsx` and
 * `features/replay/ReplayScreen.tsx`, and all three carried the byte-identical `human` class string.
 * That is the single rule the design documents care most about — identity must never look like
 * status — reimplemented three times, which is how such a rule quietly stops holding. Both of those
 * copies also painted `agent` with `text-state-info`, borrowing from the operational state ramp for
 * an identity signal, which is exactly what this component exists to prevent.
 *
 * The reasoning inherited from the decision timeline is worth keeping explicit, because it is why
 * `human` looks the way it does: `human` was once `--state-warn`, the colour this product uses for
 * SEVERITY HIGH, NEEDS HUMAN, TIER HIGH and AWAITING APPROVAL. On the recovery workspace all five
 * appear at once, so an operator's *completed* approval read as one more amber warning rather than
 * as the thing a person did. `docs/21` reserves green, amber and red exclusively for operational
 * state, so identity has no business borrowing one. A human act is therefore the only chip drawn in
 * primary text on a raised fill with a strong border — the highest-contrast, most solid chip on the
 * screen, appropriate for the one actor who carries accountability, and impossible to mistake for a
 * status.
 *
 * Owner: Stream D.
 */

import { Bot, CircleDot, Radio, User, Workflow, Wrench } from 'lucide-react';
import { clsx } from 'clsx';

const ICON = {
  orchestrator: Workflow,
  agent: Bot,
  service: Wrench,
  provider: Radio,
  human: User,
} as const;

/** The one kind that is a person. Everything else is machinery. */
function isHuman(actorKind: string): boolean {
  return actorKind === 'human';
}

export function ActorChip({
  actorKind,
  actor,
  className,
}: {
  actorKind: string;
  /** The specific actor, when the record names one. Shown after the kind. */
  actor?: string | null;
  className?: string;
}) {
  const Icon = ICON[actorKind as keyof typeof ICON] ?? CircleDot;
  const human = isHuman(actorKind);

  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5 text-caption',
        human
          ? 'border-border-strong bg-raised font-medium text-fg'
          : 'border-border-subtle bg-inset text-fg-secondary',
        className,
      )}
    >
      <Icon size={11} strokeWidth={1.5} aria-hidden />
      <span className="uppercase">{actorKind.replace(/_/g, ' ')}</span>
      {actor && actor !== actorKind && (
        <span className="font-mono text-mono-sm normal-case text-fg-muted">{actor}</span>
      )}
    </span>
  );
}

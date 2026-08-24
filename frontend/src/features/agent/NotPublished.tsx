/**
 * A capability no endpoint publishes, stated rather than quietly omitted.
 *
 * This console is asked to show things the backend does not record: a model-authored goal, the
 * arguments a tool was called with, how long a call took, how many times it was retried. Every one
 * of those is a field that does not exist on any response model.
 *
 * There were two dishonest options and one honest one. Inventing the figure is out. Silently
 * dropping the row is worse than it looks: a reviewer sees a console with no latency column and
 * concludes latency is fine, when the truth is that nothing measures it. So the row stays, names
 * the field that would carry it, and says what would have to change.
 *
 * This is the same doctrine the rest of the product already applies to data — an absent value is an
 * em dash with a reason, `unassessed_factors` is a first-class contract, and a counted-but-not-
 * traversable entity keeps its stated reason. This applies it to capabilities.
 *
 * Owner: Stream D.
 */

import { CircleSlash } from 'lucide-react';

import { MonoValue } from '@/components/ui/primitives';

export interface UnpublishedCapability {
  /** What an operator would reasonably look for. */
  capability: string;
  /** The field or route that would carry it, named exactly. */
  wouldCarry: string;
  /** Why it is absent, in concrete terms. */
  reason: string;
}

export function NotPublished({ items }: { items: UnpublishedCapability[] }) {
  if (items.length === 0) return null;

  return (
    <ul className="flex flex-col gap-1.5">
      {items.map((item) => (
        <li key={item.capability} className="flex items-start gap-2">
          <CircleSlash
            size={12}
            strokeWidth={1.5}
            className="mt-0.5 shrink-0 text-fg-muted"
            aria-hidden
          />
          <span className="min-w-0 flex-1">
            <span className="text-caption uppercase text-fg-secondary">{item.capability}</span>
            <span className="block text-caption text-fg-muted">
              {item.reason} Would be carried by{' '}
              <MonoValue muted className="text-caption break-all">
                {item.wouldCarry}
              </MonoValue>
              .
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

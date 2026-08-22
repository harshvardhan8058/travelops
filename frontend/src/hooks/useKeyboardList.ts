/**
 * Roving-tabindex navigation for any collection: lists, tables, matrices, graph nodes.
 *
 * One tab stop per collection rather than one per row, because a 34px-row flight board with a
 * tab stop on every row makes the keyboard unusable at operational density. `j`/`k` move,
 * arrows move, Enter opens, Home/End jump.
 *
 * Owner: Stream D.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

export interface KeyboardListOptions {
  count: number;
  onOpen?: (index: number) => void;
  /** Horizontal collections use ArrowLeft/ArrowRight instead of Up/Down. */
  orientation?: 'vertical' | 'horizontal';
}

export function useKeyboardList({ count, onOpen, orientation = 'vertical' }: KeyboardListOptions) {
  const [index, setIndex] = useState(0);
  const containerRef = useRef<HTMLElement | null>(null);
  /**
   * Set only by a key press. Roving tabindex has to move DOM focus, not just the tabbable
   * index — otherwise ArrowDown makes the next item tabbable while leaving the caret and the
   * focus ring on an item that is now `tabIndex={-1}`, and the keyboard user is stranded. It
   * must NOT fire on mount or on the filter clamp below, or opening a screen would yank focus
   * into the middle of a list nobody asked to enter.
   */
  const focusOnNextIndexChange = useRef(false);

  // Clamp when the collection shrinks under a filter, so focus never points past the end.
  // This effect is declared first so it runs first, which also cancels any pending focus move
  // left over from an arrow press that hit the end of the list and moved nothing.
  useEffect(() => {
    focusOnNextIndexChange.current = false;
    setIndex((current) => (count === 0 ? 0 : Math.min(current, count - 1)));
  }, [count]);

  useEffect(() => {
    if (!focusOnNextIndexChange.current) return;
    focusOnNextIndexChange.current = false;
    const active = containerRef.current?.querySelector<HTMLElement>('[data-active]');
    // `focus` on an SVG <g> works because the element carries a tabindex.
    active?.focus?.();
  }, [index]);

  const next = orientation === 'vertical' ? 'ArrowDown' : 'ArrowRight';
  const previous = orientation === 'vertical' ? 'ArrowUp' : 'ArrowLeft';

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (count === 0) return;
      const to = (resolve: (current: number) => number) => {
        event.preventDefault();
        focusOnNextIndexChange.current = true;
        setIndex((current) => Math.max(0, Math.min(count - 1, resolve(current))));
      };

      switch (event.key) {
        case 'j':
        case next:
          to((current) => current + 1);
          break;
        case 'k':
        case previous:
          to((current) => current - 1);
          break;
        case 'Home':
          to(() => 0);
          break;
        case 'End':
          to(() => count - 1);
          break;
        case 'Enter':
          if (onOpen) {
            event.preventDefault();
            onOpen(index);
          }
          break;
        default:
      }
    },
    [count, index, next, previous, onOpen],
  );

  /** Props for each item: exactly one is tabbable, and the active one is announced. */
  const itemProps = useCallback(
    (itemIndex: number) => ({
      tabIndex: itemIndex === index ? 0 : -1,
      'data-active': itemIndex === index || undefined,
    }),
    [index],
  );

  return { index, setIndex, onKeyDown, itemProps, containerRef };
}

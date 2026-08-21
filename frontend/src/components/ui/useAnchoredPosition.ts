/**
 * Anchored placement for popovers — deliberately dependency-free.
 *
 * Why not a positioning library: this needs four behaviours (place below, flip above when
 * short of room, clamp to the viewport, follow scroll and resize) and they are ~80 lines of
 * arithmetic. A floating-element library would add a dependency and a bundle to the demo
 * build for logic we can read in one screen.
 *
 * Why `position: fixed` in a portal rather than an absolutely-positioned child: the flight
 * board lives inside `overflow-x-auto` and the Decision Timeline inside `overflow-auto`. An
 * in-flow popover is clipped by exactly the panels that most need one. Fixed coordinates are
 * viewport coordinates, which is also what `getBoundingClientRect()` returns, so no scroll
 * offset maths is required.
 *
 * Owner: Stream D.
 */

import { useCallback, useEffect, useLayoutEffect, useState } from 'react';
import type { RefObject } from 'react';

export type AnchorPlacement = 'bottom-start' | 'top-start';

export interface AnchoredPosition {
  top: number;
  left: number;
  /** Space actually available in the chosen direction, so long content scrolls internally. */
  maxHeight: number;
  placement: AnchorPlacement;
}

interface AnchoredPositionOptions {
  anchorRef: RefObject<HTMLElement | null>;
  floatingRef: RefObject<HTMLElement | null>;
  open: boolean;
  /** Distance between anchor edge and panel. */
  gap?: number;
  /** Minimum distance kept from every viewport edge. */
  margin?: number;
  /** Floor for maxHeight: below this, flipping is preferred over squashing. */
  minHeight?: number;
}

function samePosition(a: AnchoredPosition | null, b: AnchoredPosition): boolean {
  return (
    a !== null &&
    a.top === b.top &&
    a.left === b.left &&
    a.maxHeight === b.maxHeight &&
    a.placement === b.placement
  );
}

/**
 * Returns null until the panel has been measured. Measurement happens in a layout effect,
 * so a position exists before the browser paints and the panel never appears misplaced.
 */
export function useAnchoredPosition({
  anchorRef,
  floatingRef,
  open,
  gap = 6,
  margin = 8,
  minHeight = 140,
}: AnchoredPositionOptions): AnchoredPosition | null {
  const [position, setPosition] = useState<AnchoredPosition | null>(null);

  const compute = useCallback(() => {
    const anchor = anchorRef.current;
    const floating = floatingRef.current;
    if (!anchor || !floating) return;

    const a = anchor.getBoundingClientRect();
    const f = floating.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    const spaceBelow = viewportHeight - a.bottom - gap - margin;
    const spaceAbove = a.top - gap - margin;

    // Prefer below. Flip only when below cannot hold the panel AND above has more room —
    // flipping into a smaller space would be a worse result, not a better one.
    const fitsBelow = f.height <= spaceBelow;
    const placement: AnchorPlacement =
      fitsBelow || spaceBelow >= spaceAbove ? 'bottom-start' : 'top-start';

    const available = placement === 'bottom-start' ? spaceBelow : spaceAbove;
    const maxHeight = Math.max(minHeight, Math.round(available));
    const height = Math.min(f.height, maxHeight);

    let top = placement === 'bottom-start' ? a.bottom + gap : a.top - gap - height;
    // Clamp vertically too: with maxHeight floored at minHeight, a very short viewport can
    // still overflow, and a panel half off-screen is worse than one slightly overlapping.
    top = Math.min(Math.max(margin, top), Math.max(margin, viewportHeight - margin - height));

    // Align to the anchor's leading edge, then pull back inside the viewport. Right-hand
    // table columns are the common case: `Src` and `Incident` sit near the window edge.
    let left = a.left;
    left = Math.min(left, viewportWidth - margin - f.width);
    left = Math.max(margin, left);

    const next: AnchoredPosition = {
      top: Math.round(top),
      left: Math.round(left),
      maxHeight,
      placement,
    };

    // Rounded values plus this guard keep the ResizeObserver from oscillating: setting
    // maxHeight can change the panel's height, which would otherwise recompute forever.
    setPosition((prev) => (samePosition(prev, next) ? prev : next));
  }, [anchorRef, floatingRef, gap, margin, minHeight]);

  // Layout effect: measure and place before paint, so there is no visible jump.
  useLayoutEffect(() => {
    if (!open) {
      setPosition(null);
      return;
    }
    compute();
  }, [open, compute]);

  useEffect(() => {
    if (!open) return;

    // Capture phase, because scroll does not bubble: this catches the flight board's
    // horizontal scroller and the timeline rail's vertical one, not just the window.
    window.addEventListener('scroll', compute, true);
    window.addEventListener('resize', compute);

    const observer = new ResizeObserver(compute);
    if (floatingRef.current) observer.observe(floatingRef.current);
    if (anchorRef.current) observer.observe(anchorRef.current);

    return () => {
      window.removeEventListener('scroll', compute, true);
      window.removeEventListener('resize', compute);
      observer.disconnect();
    };
  }, [open, compute, anchorRef, floatingRef]);

  return position;
}

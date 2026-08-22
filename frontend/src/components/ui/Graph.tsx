/**
 * Presentational SVG graph primitives. No layout, no data fetching, no colour literals.
 *
 * The graph is an enhancement, never the only representation — every screen that uses these also
 * ships a table carrying the same records, because a node-link diagram cannot be the sole way to
 * read nine crew rotations.
 *
 * Owner: Stream D.
 */

import type { ReactNode } from 'react';
import { clsx } from 'clsx';

import type { CascadeEdge, CascadeNode } from '@/features/cascade/layout';

const KIND_CLASS: Record<CascadeNode['kind'], string> = {
  event: 'fill-inset stroke-accent',
  flight: 'fill-surface stroke-border-strong',
  pairing: 'fill-surface stroke-border',
  booking: 'fill-inset stroke-border',
  hotel: 'fill-surface stroke-border',
};

const STATE_STROKE: Record<string, string> = {
  executing: 'stroke-state-info',
  assuring: 'stroke-state-info',
  planning: 'stroke-state-info',
  detected: 'stroke-state-info',
  awaiting_approval: 'stroke-state-warn',
  at_risk: 'stroke-state-warn',
  resolved: 'stroke-state-ok',
  blocked: 'stroke-state-crit',
  failed: 'stroke-state-crit',
};

export function GraphEdge({
  edge,
  emphasis,
}: {
  edge: CascadeEdge;
  /** 'on' when this edge belongs to the selected hop or node; 'off' when something else is. */
  emphasis: 'on' | 'off' | 'neutral';
}) {
  // Mechanism is carried by the label, never by the line style alone.
  return (
    <g
      className={clsx(
        'transition-opacity duration-hover ease-out',
        emphasis === 'off' && 'opacity-25',
      )}
    >
      <line
        x1={edge.fromX}
        y1={edge.fromY}
        x2={edge.toX}
        y2={edge.toY}
        className={clsx(
          emphasis === 'on' ? 'stroke-accent' : 'stroke-border',
          emphasis === 'on' ? 'stroke-2' : 'stroke-1',
        )}
      />
      {/* Only labelled when the edge carries a mechanism and is being looked at. A root-cause edge
       * has no mechanism, and 45 permanent labels is noise rather than information. */}
      {edge.mechanism && emphasis === 'on' && (
        <text
          x={(edge.fromX + edge.toX) / 2}
          y={(edge.fromY + edge.toY) / 2 - 4}
          textAnchor="middle"
          className="fill-accent font-mono text-[9px] uppercase"
        >
          {edge.mechanism.replace(/_/g, ' ')}
        </text>
      )}
    </g>
  );
}

export function GraphNode({
  node,
  selected,
  dimmed,
  onSelect,
  itemProps,
  showLabel,
}: {
  node: CascadeNode;
  selected: boolean;
  dimmed: boolean;
  onSelect: () => void;
  /**
   * Spread whole, not destructured down to `tabIndex`: `data-active` is how the roving-tabindex
   * hook finds the node to focus after an arrow key, so dropping it silently breaks keyboard
   * navigation while leaving the tab stops looking correct.
   */
  itemProps?: { tabIndex: number; 'data-active'?: true };
  /**
   * Whether to draw the text label.
   *
   * Off for the dense consequence clusters. With 22 bookings and 9 rotations on one row the labels
   * overlapped into an unreadable smear, which is worse than no label: it looks like data and reads
   * as noise, and at projector distance it made the whole layer look broken. The name is still on
   * the node's `aria-label`, appears on selection, and every record is in the table below — so
   * nothing is lost except the illegible part.
   */
  showLabel?: boolean;
}) {
  const label = node.sublabel ? `${node.label}, ${node.sublabel}` : node.label;
  const labelled = showLabel ?? true;
  return (
    <g
      {...itemProps}
      role="button"
      aria-label={`${node.kind}: ${label}`}
      aria-pressed={selected}
      tabIndex={itemProps?.tabIndex ?? -1}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onSelect();
        }
      }}
      className={clsx(
        'cursor-pointer transition-opacity duration-hover ease-out',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
        dimmed && 'opacity-30',
      )}
    >
      <circle
        cx={node.x}
        cy={node.y}
        r={node.radius}
        className={clsx(
          KIND_CLASS[node.kind],
          node.state && STATE_STROKE[node.state],
          selected ? 'stroke-2' : 'stroke-1',
        )}
      />
      {(labelled || selected) && (
        <text
          x={node.x}
          y={node.y + node.radius + 12}
          textAnchor="middle"
          className={clsx('font-mono text-[10px]', selected ? 'fill-accent' : 'fill-fg')}
        >
          {node.label}
        </text>
      )}
      {node.sublabel && (labelled || selected) && (
        <text
          x={node.x}
          y={node.y + node.radius + 23}
          textAnchor="middle"
          className="font-mono text-[9px] fill-fg-muted"
        >
          {node.sublabel}
        </text>
      )}
    </g>
  );
}

export function GraphLegend({ items }: { items: { label: string; description: string }[] }) {
  return (
    <dl className="flex flex-wrap gap-x-4 gap-y-1 px-3 py-2">
      {items.map((item) => (
        <div key={item.label} className="flex items-baseline gap-1.5">
          <dt className="font-mono text-caption uppercase text-fg-secondary">{item.label}</dt>
          <dd className="text-caption text-fg-muted">{item.description}</dd>
        </div>
      ))}
    </dl>
  );
}

export function GraphSurface({
  width,
  height,
  children,
  ariaLabel,
  onKeyDown,
}: {
  width: number;
  height: number;
  children: ReactNode;
  ariaLabel: string;
  onKeyDown?: (event: React.KeyboardEvent) => void;
}) {
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      role="group"
      aria-label={ariaLabel}
      onKeyDown={onKeyDown}
      className="h-auto w-full"
      /* Fixed viewBox scaled to fit: no free pan or zoom, so a presenter cannot lose the graph. */
    >
      {children}
    </svg>
  );
}

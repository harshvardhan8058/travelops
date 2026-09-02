/**
 * Provenance ledger — `/sources`. docs/27 screen 8.
 *
 * The definitive answer to "is any of this real?", and the same data the badges elsewhere derive
 * from. Every row is rendered as returned: `kind` is never inferred from a provider name.
 *
 * This screen used to render one status column, and that was the problem. `kind: real` renders as
 * a green badge whose accessible name is "Live/real source" — which was sitting on a row whose own
 * `current_mode` read `fixture`, and on a source that had never been called at all. Two facts were
 * being asked of one column.
 *
 * So the table now carries both, side by side and equally weighted:
 *
 *   Kind   — what the data IS   (real / synthetic / simulated / fixture / unavailable)
 *   Usage  — what this run DID  (used / unused / unavailable)
 *
 * `real` + `unused` is a genuine external source nobody called. `fixture` + `used` is a committed
 * snapshot standing in for a live read. Neither reads as the other any more, and the server states
 * both, so nothing here is inferred.
 *
 * Owner: Stream D.
 */

import { useQuery } from '@tanstack/react-query';

import { api, ApiError } from '@/api/client';
import type { SourceRow } from '@/api/types';
import {
  EmptyState,
  ErrorState,
  LoadingState,
  MonoValue,
  Panel,
  ProvenanceDot,
  StateBadge,
} from '@/components/ui/primitives';
import { CountBar } from '@/components/ui/Metric';
import {
  Absent,
  Labelled,
  Notice,
  PageHeader,
  TableFrame,
  TableHead,
} from '@/components/ui/composition';
import { utcStamp } from '@/components/ui/format';

const KIND_ORDER: SourceRow['kind'][] = [
  'real',
  'simulated',
  'synthetic',
  'fixture',
  'derived',
  'unavailable',
];

const KIND_TONE: Record<string, 'ok' | 'info' | 'neutral' | 'crit'> = {
  real: 'ok',
  simulated: 'info',
  synthetic: 'info',
  fixture: 'neutral',
  unavailable: 'crit',
  // `derived` is a legal ProvenanceKind. It was missing here, so such a row still counted toward
  // the bar's total while contributing no segment — the bar under-filled with no explanation.
  derived: 'info',
};

/**
 * Usage is rendered as its own badge, never folded into the kind badge.
 *
 * `unused` is deliberately neutral rather than a warning: a configured provider nobody called is
 * an ordinary state, and colouring it as a fault would train a reader to ignore the colour.
 */
const USAGE_STATUS: Record<string, 'up' | 'scheduled' | 'down'> = {
  used: 'up',
  unused: 'scheduled',
  unavailable: 'down',
};

export function SourcesLedger() {
  const query = useQuery({ queryKey: ['sources'], queryFn: api.sources });

  if (query.isLoading) {
    return (
      <Panel title="Provenance ledger">
        <div className="h-[420px]">
          <LoadingState label="Loading sources" />
        </div>
      </Panel>
    );
  }

  if (query.error || !query.data) {
    const error = query.error instanceof ApiError ? query.error : null;
    return (
      <ErrorState
        code={error?.code ?? 'INTERNAL_ERROR'}
        message={error?.message ?? 'Could not load the provenance ledger.'}
        correlationId={error?.correlationId ?? null}
        onRetry={() => void query.refetch()}
      />
    );
  }

  const sources = query.data.sources ?? [];
  if (sources.length === 0) {
    return (
      <Panel title="Provenance ledger">
        <EmptyState
          title="No sources registered"
          description="Every data surface derives its badge from this ledger, so an empty ledger means nothing can claim provenance."
        />
      </Panel>
    );
  }

  // A partition over a returned enum field — permitted. Not a score, not a percentage.
  const segments = KIND_ORDER.map((kind) => ({
    label: kind,
    tone: KIND_TONE[kind],
    count: sources.filter((source) => source.kind === kind).length,
  })).filter((segment) => segment.count > 0);

  // The server already counts these, using the same rule this screen would: a source is live only
  // when it is a real source AND this run actually read from it. Reading the server's counts rather
  // than recomputing them is what stops the header and the table drifting apart.
  const liveCount = query.data.live_count ?? 0;
  const unusedCount = query.data.unused_count ?? 0;
  const unavailableCount =
    query.data.unavailable_count ??
    sources.filter((source) => source.usage === 'unavailable').length;

  return (
    <div className="flex flex-col gap-3">
      {/*
       * This screen is the root of every provenance claim in the product: each badge elsewhere is
       * read from here. The summary keeps identity (`kind`) separate from effective operation:
       * a real-source row counts as live only when its recorded `current_mode` is exactly `live`.
       * These remain counts of returned rows, never a score.
       */}
      <PageHeader
        eyebrow="Provenance"
        title="Source ledger"
        meta={
          <>
            <Labelled label="sources">
              <MonoValue>{sources.length}</MonoValue>
            </Labelled>
            <Labelled label="live">
              <MonoValue className={liveCount > 0 ? 'text-state-ok' : undefined}>
                {liveCount}
              </MonoValue>
            </Labelled>
            <Labelled label="unused">
              <MonoValue muted>{unusedCount}</MonoValue>
            </Labelled>
            <Labelled label="unavailable">
              <MonoValue className={unavailableCount > 0 ? 'text-state-crit' : undefined}>
                {unavailableCount}
              </MonoValue>
            </Labelled>
          </>
        }
        footer={
          <div className="flex flex-col gap-1">
            <p className="text-body text-fg-secondary">
              Every provenance badge in the console is read from this ledger. A source that is not
              registered here cannot claim provenance anywhere else.
            </p>
            <p className="text-body text-fg-secondary">
              <strong className="text-fg">Kind</strong> is what the data is.{' '}
              <strong className="text-fg">Usage</strong> is whether this run read from it. A
              configured provider nobody called reads <MonoValue muted>unused</MonoValue> — not a
              fault, and not the same claim as <MonoValue muted>unavailable</MonoValue>.
            </p>
            {query.data.note && <p className="text-caption text-fg-muted">{query.data.note}</p>}
          </div>
        }
      />

      <Panel title="Registered sources" actions={<MonoValue muted>{sources.length}</MonoValue>}>
        <div className="border-b border-border-subtle px-3 py-2.5">
          <CountBar segments={segments} total={sources.length} />
        </div>
        <TableFrame caption="Every data source: what its data is, whether this run read from it, and under whose licence. The order is the order the endpoint returned them in and implies no priority.">
          <TableHead
            columns={[
              { key: 'source', label: 'Source' },
              { key: 'kind', label: 'Kind', hint: 'what the data is' },
              { key: 'usage', label: 'Usage', hint: 'what this run did' },
              { key: 'provider', label: 'Provider' },
              { key: 'mode', label: 'Mode' },
              { key: 'checked', label: 'Last checked', hint: 'UTC' },
              { key: 'licence', label: 'Licence' },
            ]}
          />
          <tbody>
            {sources.map((source) => (
              <tr key={source.name} className="border-b border-border-subtle">
                <th scope="row" className="max-w-[300px] px-3 py-1.5 text-left font-normal">
                  <span className="flex items-center gap-1.5">
                    <ProvenanceDot kind={source.kind} provider={source.provider} />
                    <span className="text-body text-fg">{source.name}</span>
                  </span>
                  {source.role && (
                    <span className="block text-caption text-fg-secondary">{source.role}</span>
                  )}
                  {/* The server's own sentence, rendered verbatim. This is where an `unavailable`
                      row says why, and where an `unused` row says what it would have been for. */}
                  <span className="mt-0.5 block text-caption text-fg-muted">
                    {source.usage_detail}
                  </span>
                  {source.evidence && (
                    <span className="block text-caption text-fg-muted">
                      evidence: <MonoValue muted>{source.evidence}</MonoValue>
                    </span>
                  )}
                  {source.note && (
                    <span className="block text-caption text-fg-muted">{source.note}</span>
                  )}
                </th>
                <td className="px-3 py-1.5">
                  <StateBadge
                    status={
                      source.kind === 'real'
                        ? 'up'
                        : source.kind === 'unavailable'
                          ? 'down'
                          : 'scheduled'
                    }
                    label={source.kind}
                  />
                </td>
                <td className="px-3 py-1.5">
                  <StateBadge
                    status={USAGE_STATUS[source.usage] ?? 'scheduled'}
                    label={source.usage}
                  />
                </td>
                <td className="px-3 py-1.5">
                  <MonoValue>{source.provider}</MonoValue>
                  {source.model && (
                    <span className="block text-caption text-fg-muted">{source.model}</span>
                  )}
                  <span className="block text-caption text-fg-muted">
                    {source.configured ? 'configured' : 'not configured'}
                  </span>
                </td>
                <td className="px-3 py-1.5">
                  <MonoValue muted>{source.current_mode}</MonoValue>
                </td>
                <td className="px-3 py-1.5">
                  {source.last_checked ? (
                    <MonoValue muted>{utcStamp(source.last_checked)}</MonoValue>
                  ) : (
                    <Absent
                      label="never checked"
                      title="This source has no recorded health check."
                    />
                  )}
                </td>
                <td className="max-w-[280px] px-3 py-1.5 text-caption text-fg-secondary">
                  {source.licence}
                  {source.attribution_required && (
                    <span className="ml-1 text-fg-muted">· attribution required</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </TableFrame>
        {unavailableCount > 0 && (
          <Notice tone="warn">
            {unavailableCount} source{unavailableCount === 1 ? '' : 's'} unavailable. Anything
            derived from one is badged unavailable rather than falling back to a value. Each row
            above states its own reason.
          </Notice>
        )}
      </Panel>
    </div>
  );
}

/**
 * Provenance ledger — `/sources`. docs/27 screen 8.
 *
 * The definitive answer to "is any of this real?", and the same data the badges elsewhere derive
 * from. Every row is rendered as returned: `kind` is never inferred from a provider name.
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
  'unavailable',
];

const KIND_TONE: Record<string, 'ok' | 'info' | 'neutral' | 'crit'> = {
  real: 'ok',
  simulated: 'info',
  synthetic: 'info',
  fixture: 'neutral',
  unavailable: 'crit',
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

  // `kind: real` records source identity, not whether the integration is operating live now.
  // Count a live source only when both facts are recorded; no provider or health inference.
  const liveCount = sources.filter(
    (source) => source.kind === 'real' && source.current_mode === 'live',
  ).length;
  const unavailableCount = sources.filter((source) => source.kind === 'unavailable').length;

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
            <Labelled label="unavailable">
              <MonoValue className={unavailableCount > 0 ? 'text-state-crit' : undefined}>
                {unavailableCount}
              </MonoValue>
            </Labelled>
          </>
        }
        footer={
          <p className="text-body text-fg-secondary">
            Every provenance badge in the console is read from this ledger. A source that is not
            registered here cannot claim provenance anywhere else.
          </p>
        }
      />

      <Panel title="Registered sources" actions={<MonoValue muted>{sources.length}</MonoValue>}>
        <div className="border-b border-border-subtle px-3 py-2.5">
          <CountBar segments={segments} total={sources.length} />
        </div>
        <TableFrame caption="Every data source, its kind, licence and health. The order is the order the endpoint returned them in and implies no priority.">
          <TableHead
            columns={[
              { key: 'source', label: 'Source' },
              { key: 'kind', label: 'Kind' },
              { key: 'provider', label: 'Provider' },
              { key: 'mode', label: 'Mode' },
              { key: 'checked', label: 'Last checked', hint: 'UTC' },
              { key: 'licence', label: 'Licence' },
              { key: 'health', label: 'Health' },
            ]}
          />
          <tbody>
            {sources.map((source) => (
              <tr key={source.name} className="border-b border-border-subtle">
                <th scope="row" className="px-3 py-1.5 text-left font-normal">
                  <span className="flex items-center gap-1.5">
                    <ProvenanceDot kind={source.kind} provider={source.provider} />
                    <span className="text-body text-fg">{source.name}</span>
                  </span>
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
                  <MonoValue>{source.provider}</MonoValue>
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
                <td className="px-3 py-1.5">
                  <span className="text-caption text-fg-secondary">{source.health}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </TableFrame>
        {unavailableCount > 0 && (
          <Notice tone="warn">
            {unavailableCount} source{unavailableCount === 1 ? '' : 's'} unavailable. Anything
            derived from one is badged unavailable rather than falling back to a value.
          </Notice>
        )}
      </Panel>
    </div>
  );
}

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

  return (
    <div className="flex flex-col gap-3">
      <Panel title="Provenance ledger" actions={<MonoValue muted>{sources.length}</MonoValue>}>
        <div className="border-b border-border-subtle px-3 py-2">
          <CountBar segments={segments} total={sources.length} />
        </div>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-body">
            <caption className="sr-only">Every data source, its kind, licence and health.</caption>
            <thead>
              <tr className="border-b border-border-subtle bg-inset text-label uppercase text-fg-muted">
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Source
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Kind
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Provider
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Mode
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Last checked
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Licence
                </th>
                <th scope="col" className="px-3 py-1.5 text-left font-medium">
                  Health
                </th>
              </tr>
            </thead>
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
                      <MonoValue muted>
                        {source.last_checked.slice(0, 10)} {source.last_checked.slice(11, 19)}Z
                      </MonoValue>
                    ) : (
                      <span className="text-caption text-fg-muted" title="never checked">
                        —
                      </span>
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
          </table>
        </div>
      </Panel>
    </div>
  );
}

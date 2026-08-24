/**
 * Confidence, stated the only way this system can honestly state it.
 *
 * There is no confidence number anywhere in the backend, on purpose. What exists is a **deterministic
 * index with a rule version and named contributing factors**, and that is a materially different
 * claim: 82 is not "82% likely", it is "these five rules fired and added up to 82 under
 * delay-risk-v1". A percentage would imply calibration against observed outcomes that nobody has
 * measured, so this panel shows the index, the band, the rule that produced it and every factor
 * behind it — and says outright that it is not a probability.
 *
 * `model_self_report` is included precisely because it is always absent. It is the one field on the
 * contract where a model could assert its own confidence, it is documented as diagnostic-only and
 * never a control input, and today no model runs at all. Rendering it as an em dash rather than
 * hiding the row is what stops a reviewer assuming a self-assessment exists.
 *
 * Owner: Stream D.
 */

import { Metric, MetricTile } from '@/components/ui/Metric';
import {
  countDerivation,
  incidentRiskDerivation,
  modelSelfReportDerivation,
  riskFactorDerivation,
} from '@/components/ui/derivation';
import { MonoValue, Panel, RiskChip } from '@/components/ui/primitives';
import type { IncidentDetail, PlanSummary } from '@/api/types';
import { NotPublished } from './NotPublished';

export function UncertaintyPanel({
  incident,
  plan,
  configVersion,
  configHash,
  planHash,
}: {
  incident: IncidentDetail;
  plan: PlanSummary | null;
  configVersion: string;
  configHash: string;
  planHash: string | null;
}) {
  const risk = incident.evidence.risk;
  const weather = incident.evidence.weather;

  return (
    <Panel
      title="Confidence and uncertainty"
      className="flex h-full min-h-0 flex-col overflow-hidden"
    >
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="px-3 py-2">
          <p className="text-caption text-fg-muted">
            An index under a named rule, not a probability. Nothing here is calibrated against
            observed outcomes, so no percentage is offered.
          </p>
        </div>

        <div className="flex flex-wrap items-start gap-2 border-t border-border-subtle px-3 py-2">
          {risk ? (
            <>
              <MetricTile
                label="Delay risk index"
                value={risk.risk_index}
                derivation={incidentRiskDerivation(risk, weather)}
                footnote={
                  <span className="flex items-center gap-1.5">
                    <RiskChip index={risk.risk_index} level={risk.risk_level} />
                  </span>
                }
              />
              <MetricTile
                label="Contributing factors"
                value={risk.factors.length}
                derivation={countDerivation('Contributing factors', risk.factors.length, {
                  endpoint: 'GET /incidents/{ref}',
                  field: 'evidence.risk.factors[]',
                  note: 'Each factor names the rule that fired, the observed value and the points it contributed, which is what makes the index explainable rather than asserted.',
                })}
              />
            </>
          ) : (
            <p className="text-caption text-fg-muted">
              No prediction has been recorded for this incident, so there is no index. That is the
              normal state of a freshly opened incident and is not a score of zero.
            </p>
          )}
          <MetricTile
            label="Model self-report"
            value={plan?.model_self_report ?? null}
            derivation={modelSelfReportDerivation(plan)}
          />
        </div>

        {risk && risk.factors.length > 0 && (
          <div className="border-t border-border-subtle px-3 py-2">
            <h3 className="mb-1.5 text-label uppercase text-fg-muted">Factors behind the index</h3>
            <table className="w-full border-collapse text-body">
              <caption className="sr-only">
                Rules that contributed to the delay risk index, with observed value and points
              </caption>
              <thead>
                <tr className="border-b border-border-subtle text-label uppercase text-fg-muted">
                  <th scope="col" className="py-1 pr-2 text-left font-medium">
                    Factor
                  </th>
                  <th scope="col" className="py-1 pr-2 text-left font-medium">
                    Observed
                  </th>
                  <th scope="col" className="py-1 text-right font-medium">
                    Points
                  </th>
                </tr>
              </thead>
              <tbody>
                {risk.factors.map((factor) => (
                  <tr key={factor.name} className="border-b border-border-subtle last:border-b-0">
                    <th scope="row" className="py-1 pr-2 text-left align-top font-normal">
                      <span className="text-caption text-fg-secondary">
                        {factor.name.replace(/_/g, ' ')}
                      </span>
                      {factor.detail && (
                        <span className="block text-caption text-fg-muted">{factor.detail}</span>
                      )}
                    </th>
                    <td className="py-1 pr-2 align-top">
                      <MonoValue muted className="text-caption break-all">
                        {factor.value === '' ? 'not recorded' : factor.value}
                      </MonoValue>
                      {factor.threshold && (
                        <span className="block text-caption text-fg-muted">
                          threshold {factor.threshold}
                        </span>
                      )}
                    </td>
                    <td className="py-1 text-right align-top">
                      <Metric
                        value={factor.points ?? null}
                        derivation={riskFactorDerivation(factor, risk.rule_version)}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="border-t border-border-subtle px-3 py-2">
          <h3 className="mb-1.5 text-label uppercase text-fg-muted">Semantics of record</h3>
          <dl className="flex flex-col gap-1 text-caption">
            <div className="flex items-baseline justify-between gap-2">
              <dt className="uppercase text-fg-muted">gate config</dt>
              <dd>
                <MonoValue muted className="text-caption">
                  {configVersion}
                </MonoValue>
              </dd>
            </div>
            <div className="flex items-baseline justify-between gap-2">
              <dt className="uppercase text-fg-muted">config hash</dt>
              <dd>
                <MonoValue muted className="text-caption break-all">
                  {configHash}
                </MonoValue>
              </dd>
            </div>
            <div className="flex items-baseline justify-between gap-2">
              <dt className="uppercase text-fg-muted">rule version</dt>
              <dd>
                <MonoValue muted className="text-caption">
                  {risk?.rule_version ?? 'not recorded'}
                </MonoValue>
              </dd>
            </div>
            <div className="flex items-baseline justify-between gap-2">
              <dt className="uppercase text-fg-muted">plan hash</dt>
              <dd>
                <MonoValue muted className="text-caption break-all">
                  {planHash ?? 'not recorded'}
                </MonoValue>
              </dd>
            </div>
          </dl>
          <p className="mt-1.5 text-caption text-fg-muted">
            Recorded so a replay can prove which semantics applied when each decision was taken.
          </p>
        </div>

        <div className="border-t border-border-subtle px-3 py-2">
          <h3 className="mb-1.5 text-label uppercase text-fg-muted">
            Not published by any contract
          </h3>
          <NotPublished
            items={[
              {
                capability: 'confidence score',
                wouldCarry: 'no field on any response model',
                reason:
                  'No endpoint returns a confidence or probability, and none is computed here. The index above is the honest form of the same question.',
              },
              {
                capability: 'retrieved precedent',
                wouldCarry: 'evidence.retrieved_precedent',
                reason:
                  'The field is on the contract and always null: precedent retrieval is unimplemented, so nothing similar is being cited.',
              },
            ]}
          />
        </div>
      </div>
    </Panel>
  );
}

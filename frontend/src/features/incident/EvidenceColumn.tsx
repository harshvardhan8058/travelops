/**
 * Recovery workspace, left column — Evidence.
 *
 * Everything here is an INPUT. Nothing is editable, because an operator editing the evidence
 * the gate reasoned over would make the audit record a work of fiction.
 *
 * Every section here can be legitimately absent in real data, and all three absences were
 * observed against the live API rather than imagined:
 *
 *   evidence.risk    null until the Delay Risk service records a Prediction
 *   evidence.weather null until an observation exists for the origin airport
 *   affected_entities  at most `passengers` and `bookings`, and `{}` when there are no bookings
 *
 * So each one has designed copy that says "not recorded" rather than rendering a zero, a dash
 * with no explanation, or an empty panel that reads as a broken fetch.
 *
 * Owner: Stream D.
 */

import type { ReactNode } from 'react';

import type { IncidentDetail } from '@/api/types';
import {
  AgeIndicator,
  MonoValue,
  Panel,
  ProvenanceDot,
  RiskChip,
  WhyPopover,
} from '@/components/ui/primitives';
import { entityCountDerivation, incidentRiskDerivation } from '@/components/ui/derivation';

/**
 * Entity kinds the product reports on, from docs/27 ("pax affected, connections at risk, crew
 * pairings affected") and the committed fixture contract.
 *
 * These rows are ALWAYS listed, and a kind the endpoint did not compute renders as an em dash.
 * Iterating only what the response contains would silently drop three rows on real data and
 * leave an operator unable to distinguish "no connections at risk" from "connections were never
 * computed". Zero is never substituted: a fabricated 0 reads as "nothing affected", which
 * docs/25 lists as a non-negotiable failure condition.
 */
const ENTITY_KINDS = [
  'passengers',
  'bookings',
  'connections_at_risk',
  'crew_pairings',
  'candidate_hotels',
] as const;

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-2 py-0.5">
      <span className="shrink-0 text-caption uppercase text-fg-muted">{label}</span>
      <span className="min-w-0 text-right text-body text-fg">{children}</span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="border-b border-border-subtle px-3 py-2 last:border-b-0">
      <h3 className="mb-1 text-label uppercase text-fg-secondary">{title}</h3>
      {children}
    </div>
  );
}

/** An absent count. Never 0, and it says why on hover. */
function NotComputed() {
  return (
    <span
      className="font-mono text-mono-sm text-fg-muted"
      title="Not computed by this endpoint. An absent value, not zero."
    >
      —
    </span>
  );
}

export function EvidenceColumn({ incident }: { incident: IncidentDetail }) {
  const {
    risk,
    weather,
    affected_entities: entities,
    retrieved_precedent: precedent,
  } = incident.evidence;

  // Canonical kinds first, then anything else the endpoint returned, so a new key is never
  // hidden by this component's idea of the list.
  const counts = entities ?? {};
  const extraKinds = Object.keys(counts).filter(
    (key) => !(ENTITY_KINDS as readonly string[]).includes(key),
  );
  const entityRows: string[] = [...ENTITY_KINDS, ...extraKinds];

  return (
    <Panel
      title="Evidence"
      className="flex min-h-0 flex-col overflow-hidden"
      actions={
        <ProvenanceDot
          kind={incident.provenance.kind}
          provider={incident.provenance.provider}
          sourceRef={incident.provenance.source_ref}
        />
      }
    >
      <div className="min-h-0 flex-1 overflow-y-auto">
        <Section title="Weather observation used">
          {!weather ? (
            <p className="text-caption text-fg-muted">
              No observation is recorded for the origin airport, so the plan was assured without
              one. This is <MonoValue muted>null</MonoValue> from the endpoint, not a fetch that
              failed.
            </p>
          ) : (
            <>
              <Field label="airport">
                <MonoValue>{weather.airport_icao ?? '—'}</MonoValue>
              </Field>
              <Field label="observed">
                <MonoValue muted>
                  {weather.observed_at
                    ? `${weather.observed_at.slice(0, 10)} ${weather.observed_at.slice(11, 19)}Z`
                    : 'not recorded'}
                </MonoValue>
              </Field>
              <Field label="wind">
                {weather.wind_speed_kt === null || weather.wind_speed_kt === undefined ? (
                  <NotComputed />
                ) : (
                  <MonoValue>
                    {weather.wind_speed_kt}kt {weather.wind_direction_deg ?? ''}
                  </MonoValue>
                )}
              </Field>
              <Field label="visibility">
                {weather.visibility_m === null || weather.visibility_m === undefined ? (
                  <NotComputed />
                ) : (
                  <MonoValue>{weather.visibility_m}m</MonoValue>
                )}
              </Field>
              <Field label="ceiling">
                {weather.ceiling_ft === null || weather.ceiling_ft === undefined ? (
                  <NotComputed />
                ) : (
                  <MonoValue>{weather.ceiling_ft}ft</MonoValue>
                )}
              </Field>
              {weather.precipitation && (
                <Field label="precip">
                  <MonoValue>{weather.precipitation}</MonoValue>
                </Field>
              )}
              <Field label="source">
                <span className="inline-flex items-center gap-1.5">
                  <ProvenanceDot
                    kind={weather.provenance.kind}
                    provider={weather.provenance.provider}
                    sourceRef={weather.provenance.source_ref}
                    isStale={weather.provenance.is_stale}
                  />
                  <span className="text-caption text-fg-muted">
                    {weather.provenance.kind} · {weather.provenance.provider}
                  </span>
                </span>
              </Field>
              {/*
               * Age is shown only when the endpoint records it. Deriving it from the browser
               * clock against a committed snapshot would read as hours stale and look like a bug
               * in the freshness check.
               */}
              <Field label="age">
                {weather.observation_age_minutes !== undefined ? (
                  <AgeIndicator minutes={weather.observation_age_minutes} />
                ) : (
                  <span className="text-caption text-fg-muted">not recorded on this endpoint</span>
                )}
              </Field>
            </>
          )}
        </Section>

        <Section title="Delay risk">
          {!risk ? (
            <p className="text-caption text-fg-muted">
              No risk index has been recorded for this incident. The Delay Risk service writes a
              prediction before assessment; until it does, this is <MonoValue muted>null</MonoValue>{' '}
              and the UI does not invent a band.
            </p>
          ) : (
            <>
              <div className="flex items-center justify-between gap-2">
                <WhyPopover derivation={incidentRiskDerivation(risk, weather)}>
                  <RiskChip index={risk.risk_index} level={risk.risk_level} />
                </WhyPopover>
                <MonoValue muted className="text-caption">
                  {risk.rule_version}
                </MonoValue>
              </div>
              {risk.factors.length === 0 ? (
                <p className="mt-1.5 text-caption text-fg-muted">
                  The endpoint returned an index and a band with no contributing factors.
                </p>
              ) : (
                <ul className="mt-1.5 flex flex-col gap-1">
                  {risk.factors.map((factor) => (
                    <li key={factor.name} className="flex items-start justify-between gap-2">
                      <span className="min-w-0 text-caption text-fg-secondary">
                        {factor.name.replace(/_/g, ' ')}
                      </span>
                      <span className="shrink-0 text-right">
                        <MonoValue>{factor.value}</MonoValue>
                        {factor.threshold && (
                          <MonoValue muted className="ml-1 text-caption">
                            / {factor.threshold}
                          </MonoValue>
                        )}
                        {factor.runway && (
                          <MonoValue muted className="ml-1 text-caption">
                            ({factor.runway})
                          </MonoValue>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              {risk.note && <p className="mt-1.5 text-caption text-fg-muted">{risk.note}</p>}
            </>
          )}
        </Section>

        <Section title="Affected entities">
          {entityRows.map((label) => {
            const value = counts[label];
            return (
              <Field key={label} label={label.replace(/_/g, ' ')}>
                {value === undefined ? (
                  <NotComputed />
                ) : (
                  /* Counts come from records, computed server-side. The UI never sums its own. */
                  <WhyPopover derivation={entityCountDerivation(label, value, incident)}>
                    <MonoValue>{value}</MonoValue>
                  </WhyPopover>
                )}
              </Field>
            );
          })}
          {Object.keys(counts).length === 0 && (
            <p className="mt-1 text-caption text-fg-muted">
              No entity counts have been derived for this incident yet. Every row above is absent
              rather than zero.
            </p>
          )}
        </Section>

        <Section title="Retrieved precedent">
          {precedent ? (
            <>
              <Field label="incident">
                <MonoValue className="text-accent">
                  {precedent.incident_reference ?? 'not recorded'}
                </MonoValue>
              </Field>
              <Field label="outcome">
                <MonoValue>{precedent.outcome ?? 'not recorded'}</MonoValue>
              </Field>
              {precedent.matched_on && precedent.matched_on.length > 0 && (
                <div className="mt-1">
                  <span className="text-caption uppercase text-fg-muted">matched on</span>
                  <ul className="mt-1 flex flex-wrap gap-1">
                    {precedent.matched_on.map((clause) => (
                      <li
                        key={clause}
                        className="rounded-sm border border-border-subtle bg-inset px-1.5 py-0.5"
                      >
                        <MonoValue muted className="text-caption">
                          {clause}
                        </MonoValue>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {precedent.note && (
                <p className="mt-1.5 text-caption text-fg-muted">{precedent.note}</p>
              )}
            </>
          ) : (
            <p className="text-caption text-fg-muted">
              No precedent was retrieved for this incident, so the plan was built without one.
            </p>
          )}
        </Section>
      </div>
    </Panel>
  );
}

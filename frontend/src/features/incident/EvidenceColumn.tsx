/**
 * Recovery workspace, left column — Evidence.
 *
 * Everything here is an INPUT. Nothing is editable, because an operator editing the evidence
 * the gate reasoned over would make the audit record a work of fiction.
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

export function EvidenceColumn({ incident }: { incident: IncidentDetail }) {
  const {
    risk,
    weather,
    affected_entities: entities,
    retrieved_precedent: precedent,
  } = incident.evidence;

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
            <MonoValue>
              {weather.wind_speed_kt ?? '—'}kt {weather.wind_direction_deg ?? ''}
            </MonoValue>
          </Field>
          <Field label="visibility">
            <MonoValue>{weather.visibility_m ?? '—'}m</MonoValue>
          </Field>
          <Field label="ceiling">
            <MonoValue>{weather.ceiling_ft ?? '—'}ft</MonoValue>
          </Field>
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
           * Age is shown only when the endpoint records it. Deriving it from the browser clock
           * against a committed snapshot would read as hours stale and look like a bug in the
           * freshness check.
           */}
          {weather.observation_age_minutes !== undefined ? (
            <Field label="age">
              <AgeIndicator minutes={weather.observation_age_minutes} />
            </Field>
          ) : (
            <Field label="age">
              <span className="text-caption text-fg-muted">not recorded on this endpoint</span>
            </Field>
          )}
        </Section>

        <Section title="Delay risk">
          <div className="flex items-center justify-between gap-2">
            <WhyPopover derivation={incidentRiskDerivation(risk, weather)}>
              <RiskChip index={risk.risk_index} level={risk.risk_level} />
            </WhyPopover>
            <MonoValue muted className="text-caption">
              {risk.rule_version}
            </MonoValue>
          </div>
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
          {risk.note && <p className="mt-1.5 text-caption text-fg-muted">{risk.note}</p>}
        </Section>

        <Section title="Affected entities">
          {Object.entries(entities).map(([label, value]) => (
            <Field key={label} label={label.replace(/_/g, ' ')}>
              {/* Counts come from records, computed server-side. The UI never sums its own. */}
              <WhyPopover derivation={entityCountDerivation(label, value, incident)}>
                <MonoValue>{value}</MonoValue>
              </WhyPopover>
            </Field>
          ))}
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

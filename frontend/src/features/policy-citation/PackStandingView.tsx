/**
 * The single rendering of policy-pack standing, used by every surface that shows one.
 *
 * There used to be two: a shell chip that coloured itself from `policy_mode` and a screen banner that
 * computed its own boolean. Two derivations of one legal fact is how a console ends up contradicting
 * itself in front of the person who has to sign for the payout, so both now call
 * `packStanding()` and render through here.
 *
 * Two variants, one derivation:
 *
 *   - `chip` — the shell. `/system/mode` publishes `policy_pack` as `{id, version, ui_label}` and
 *     **no status**, so this variant makes no standing claim at all and says where the standing is
 *     published. It is deliberately neutral: painting it green from a mode setting was the defect.
 *   - `banner` — the policy screen, where the pack contract does publish the ladder, so the standing
 *     is stated with its rung, its raw token and the reason it reads that way.
 *
 * Owner: Stream D.
 */

import { AlertTriangle, Ban, CircleDot, Scale } from 'lucide-react';
import { clsx } from 'clsx';

import { MonoValue } from '@/components/ui/primitives';
import type { PackStanding } from './packStanding';

const TONE_SHELL: Record<PackStanding['tone'], string> = {
  ok: 'border-state-ok/30 bg-state-ok-bg',
  warn: 'border-state-warn/30 bg-state-warn-bg',
  crit: 'border-state-crit/30 bg-state-crit-bg',
  neutral: 'border-border-subtle bg-inset',
};

const TONE_TEXT: Record<PackStanding['tone'], string> = {
  ok: 'text-state-ok',
  warn: 'text-state-warn',
  crit: 'text-state-crit',
  neutral: 'text-fg-secondary',
};

const TONE_ICON: Record<PackStanding['tone'], typeof Scale> = {
  ok: Scale,
  warn: AlertTriangle,
  crit: Ban,
  neutral: CircleDot,
};

/**
 * Where the standing is published, for the surface that cannot see it.
 *
 * Stated rather than left blank so nobody reads a neutral chip as an endorsement.
 */
const STANDING_NOT_PUBLISHED_HERE =
  'GET /system/mode publishes this pack\u2019s id, version and label but not its review status, so no standing is claimed here. The policy screen shows it.';

/** The label when even the pack label is absent. Visible, never a blank space. */
const LABEL_UNKNOWN = 'policy pack unknown';

export function PackStandingChip({ uiLabel }: { uiLabel: string | null | undefined }) {
  const label = uiLabel && uiLabel.trim() !== '' ? uiLabel : LABEL_UNKNOWN;
  const known = label !== LABEL_UNKNOWN;

  return (
    <span
      className={clsx(
        'truncate rounded-sm border px-1.5 py-0.5 text-caption',
        'border-border-subtle bg-inset',
        known ? 'text-fg-secondary' : 'text-fg-muted',
      )}
      /*
       * The label is a regulation's name ("MoCA", "CAR"), so it is never case-transformed: a CSS
       * `uppercase` here would misquote the instrument this figure is cited from.
       */
      title={known ? `${label} \u00b7 ${STANDING_NOT_PUBLISHED_HERE}` : STANDING_NOT_PUBLISHED_HERE}
    >
      {label}
      <span className="sr-only">
        {known
          ? ` \u2014 ${STANDING_NOT_PUBLISHED_HERE}`
          : ' \u2014 this response published no policy pack label.'}
      </span>
    </span>
  );
}

export function PackStandingBanner({
  uiLabel,
  standing,
  packId,
  packVersion,
}: {
  uiLabel: string | null | undefined;
  standing: PackStanding;
  packId: string | null | undefined;
  packVersion: string | null | undefined;
}) {
  const Icon = TONE_ICON[standing.tone];
  const label = uiLabel && uiLabel.trim() !== '' ? uiLabel : LABEL_UNKNOWN;

  return (
    <div className={clsx('flex flex-col gap-1 border-b px-3 py-2', TONE_SHELL[standing.tone])}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Icon
          size={14}
          strokeWidth={1.5}
          className={clsx('shrink-0', TONE_TEXT[standing.tone])}
          aria-hidden
        />
        {/*
          No `uppercase`: the pack label carries meaningful case ("MoCA", "CAR") and this badge is a
          citation, not a heading. A CSS transform here would misquote the regulation's own name.
        */}
        <span className={clsx('text-label font-medium', TONE_TEXT[standing.tone])}>{label}</span>
        {/* The standing, as a word. Colour is never the only signal. */}
        <span className={clsx('text-caption uppercase', TONE_TEXT[standing.tone])}>
          {standing.label}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-caption text-fg-muted">
        <span>
          pack <MonoValue muted>{packId ?? 'not recorded'}</MonoValue>{' '}
          <MonoValue muted>{packVersion ?? 'not recorded'}</MonoValue>
        </span>
        <span>
          {/* The ladder token verbatim: it is what a replay compares, so it is never prettified. */}
          status <MonoValue muted>{standing.status ?? 'not recorded'}</MonoValue>
        </span>
        <span>
          verified mode eligible{' '}
          <MonoValue muted>
            {standing.verifiedModeEligible === null
              ? 'not recorded'
              : String(standing.verifiedModeEligible)}
          </MonoValue>
        </span>
      </div>

      <p className="text-caption text-fg-secondary">{standing.detail}</p>
    </div>
  );
}

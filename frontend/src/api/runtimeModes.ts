/**
 * Telling LIVE, FIXTURE and SIMULATED apart for the four adapters the shell reports.
 *
 * These are not interchangeable, and the difference is the whole point of the chips. An operator
 * reading a delay figure needs to know whether it came off a vendor API or out of a committed
 * snapshot; a reader of a "passenger notified" line needs to know whether anything left the
 * building. So every chip here is derived from `GET /system/mode` and nothing is asserted by the
 * frontend on its own — there is no constant in this file that says a mode is live.
 *
 * Three properties of the backend contract carry the weight:
 *
 * 1. **The published modes are the *effective* ones.** `RuntimeModes.to_dict` in
 *    `backend/app/config.py` documents this for `flight_status_mode`: a `live` request that
 *    degraded to the snapshot reports `fixture`, not `live`. So reading the mode is sufficient to
 *    know what is actually serving, and a fallback can never hide behind a live badge.
 * 2. **`degradations` names the reason.** When a mode was downgraded the server ships its own
 *    sentence. The chip surfaces that sentence verbatim rather than paraphrasing it, the same way
 *    `unavailable.ts` renders the server's `resolution`.
 * 3. **`notification_mode` alone does not settle NOTIFY.** `mailtrap`/`gmail` with credentials but
 *    an empty `DEMO_RECIPIENT_ALLOWLIST` still delivers nothing — `resolve_modes` records
 *    "all deliveries recorded as simulated" and leaves `real_email_enabled` false. `real_email_enabled`
 *    is therefore the discriminator for that chip, not the mode string.
 *
 * `off` is deliberately not folded into an error posture. `LLM_MODE=off` is a supported operating
 * mode running the deterministic fallback playbook, and it is a demo asset rather than a fault —
 * but it must stay visible, because a plan built by the fallback is not a plan built by a model.
 *
 * Owner: Stream D.
 */

import type { SystemMode } from './types';

/** The four adapters the shell reports on. */
export type ModeLabel = 'LLM' | 'FLT' | 'WX' | 'NOTIFY';

/**
 * What an adapter is actually doing.
 *
 * - `live`      — reaching a real external provider, or really delivering.
 * - `fixture`   — replaying a committed recording. Real recorded values, not invented ones.
 * - `simulated` — the effect is recorded in the system but nothing leaves it.
 * - `off`       — not engaged at all; a deterministic substitute is doing the work.
 * - `unknown`   — `/system/mode` has not answered yet. Never rendered as any of the above.
 */
export type ModePosture = 'live' | 'fixture' | 'simulated' | 'off' | 'unknown';

export interface ModeChipView {
  label: ModeLabel;
  /**
   * The backend's own word for the effective adapter, or `null` before `/system/mode` answers.
   * Rendered as-is; this module never substitutes a friendlier synonym.
   */
  value: string | null;
  posture: ModePosture;
  /** One sentence naming what this adapter is doing, for the chip's tooltip. */
  detail: string;
  /**
   * The server's own sentence for this adapter when a stronger mode was requested and refused.
   * Non-null means a downgrade happened — not merely that the server described the mode.
   */
  degradation: string | null;
}

/**
 * Which adapter a `degradations` entry is talking about.
 *
 * Matching on the environment-variable name rather than on free text keeps this additive: an
 * unrecognised entry simply does not attach to a chip, so a wording change upstream loses a tooltip
 * rather than producing a false claim.
 */
const DEGRADATION_KEYS: Record<ModeLabel, readonly string[]> = {
  LLM: ['LLM_MODE='],
  FLT: ['FLIGHT_STATUS_MODE='],
  WX: ['WEATHER_MODE='],
  NOTIFY: ['NOTIFICATION_MODE=', 'DEMO_RECIPIENT_ALLOWLIST'],
};

/**
 * The phrases `resolve_modes` uses when a *requested* mode could not be honoured.
 *
 * Naming an adapter is not enough on its own. The committed demo fixture carries descriptive
 * entries like `WEATHER_MODE=fixture: serving the committed snapshot, not live METAR` — true, but
 * not a downgrade, because fixture is what was asked for. Flagging those put a warning marker on
 * two chips that were operating exactly as configured, which spends the operator's attention on
 * nothing and devalues the marker for the case that matters. Every real downgrade in
 * `backend/app/config.py` says either "degraded to …" or "recorded as simulated".
 */
const DOWNGRADE_PHRASES = ['degraded to', 'recorded as simulated'] as const;

function degradationFor(label: ModeLabel, degradations: readonly string[]): string | null {
  const keys = DEGRADATION_KEYS[label];
  const hit = degradations.find(
    (entry) =>
      keys.some((key) => entry.includes(key)) &&
      DOWNGRADE_PHRASES.some((phrase) => entry.toLowerCase().includes(phrase)),
  );
  return hit ?? null;
}

const PENDING: Omit<ModeChipView, 'label'> = {
  value: null,
  posture: 'unknown',
  detail: 'Waiting for GET /system/mode.',
  degradation: null,
};

function llmChip(mode: SystemMode): Omit<ModeChipView, 'label' | 'degradation'> {
  switch (mode.llm_mode) {
    case 'live':
      return {
        value: 'live',
        posture: 'live',
        detail: 'Reasoning calls a real provider. Output is generated per request.',
      };
    case 'fixture':
      return {
        value: 'fixture',
        posture: 'fixture',
        detail: 'Reasoning replays a committed artefact. No provider call is made.',
      };
    case 'off':
      return {
        value: 'off',
        posture: 'off',
        // The backend's own words for this mode, kept so the chip and `degradations` agree.
        detail: 'Recovery runs on the deterministic fallback playbook, not a model.',
      };
  }
}

function flightChip(mode: SystemMode): Omit<ModeChipView, 'label' | 'degradation'> {
  return mode.flight_status_mode === 'live'
    ? {
        value: 'live',
        posture: 'live',
        detail: 'Observed flight state comes from the live vendor API.',
      }
    : {
        value: 'fixture',
        posture: 'fixture',
        detail: 'Observed flight state comes from the committed snapshot.',
      };
}

function weatherChip(mode: SystemMode): Omit<ModeChipView, 'label' | 'degradation'> {
  return mode.weather_mode === 'live'
    ? {
        value: 'live',
        posture: 'live',
        detail: 'Conditions come from live Aviation Weather Center METAR.',
      }
    : {
        value: 'fixture',
        posture: 'fixture',
        detail: 'Conditions come from the committed METAR snapshot, not live weather.',
      };
}

function notifyChip(mode: SystemMode): Omit<ModeChipView, 'label' | 'degradation'> {
  // `real_email_enabled` is the discriminator, not the mode: credentials without an allowlist
  // still deliver nothing, and the backend records those deliveries as simulated.
  if (mode.real_email_enabled) {
    return {
      value: mode.notification_mode,
      posture: 'live',
      detail: 'Messages are delivered to real allowlisted recipients.',
    };
  }
  if (mode.notification_mode === 'console') {
    return {
      value: 'console',
      posture: 'simulated',
      detail: 'Messages are recorded as simulated. Nothing is delivered to a recipient.',
    };
  }
  return {
    value: mode.notification_mode,
    posture: 'simulated',
    detail:
      `${mode.notification_mode} is configured but real delivery is not enabled. ` +
      'Messages are recorded as simulated.',
  };
}

/**
 * The four chips, always all four, in a fixed order.
 *
 * Returning a constant-length list keeps the shell from conditionally dropping a chip — an absent
 * FLT chip would read as "flight data is not part of this system" rather than "still loading".
 */
export function deriveModeChips(mode: SystemMode | undefined): ModeChipView[] {
  if (!mode) {
    return (['LLM', 'FLT', 'WX', 'NOTIFY'] as const).map((label) => ({ label, ...PENDING }));
  }

  const degradations = mode.degradations ?? [];
  const built: Record<ModeLabel, Omit<ModeChipView, 'label' | 'degradation'>> = {
    LLM: llmChip(mode),
    FLT: flightChip(mode),
    WX: weatherChip(mode),
    NOTIFY: notifyChip(mode),
  };

  return (['LLM', 'FLT', 'WX', 'NOTIFY'] as const).map((label) => ({
    label,
    ...built[label],
    degradation: degradationFor(label, degradations),
  }));
}

export interface EvidencePosture {
  /** `unknown` until the modes arrive; then `live` only if every adapter is live. */
  headline: 'LIVE' | 'MIXED' | 'RECORDED' | 'UNKNOWN';
  /** Chips that are not serving live external data. Empty only when the headline is `LIVE`. */
  notLive: ModeChipView[];
  /** A sentence for a read-only posture panel. */
  summary: string;
}

/**
 * A whole-session summary of where evidence is coming from.
 *
 * Deliberately conservative: anything short of every adapter being live is reported as `MIXED` or
 * `RECORDED`, so a screen can never round a partly-recorded session up to a live one. There is no
 * session-wide "simulation" flag in any backend contract, so this makes no claim about whether a
 * given incident was simulated — that lives on each record's own provenance.
 */
export function deriveEvidencePosture(mode: SystemMode | undefined): EvidencePosture {
  const chips = deriveModeChips(mode);
  if (!mode) {
    return {
      headline: 'UNKNOWN',
      notLive: chips,
      summary: 'Runtime modes have not been read yet.',
    };
  }

  const notLive = chips.filter((chip) => chip.posture !== 'live');
  if (notLive.length === 0) {
    return {
      headline: 'LIVE',
      notLive,
      summary: 'Every adapter is live: reasoning, flight state, weather and delivery are real.',
    };
  }

  const names = notLive.map((chip) => chip.label).join(', ');
  if (notLive.length === chips.length) {
    return {
      headline: 'RECORDED',
      notLive,
      summary: `No adapter is live. ${names} run on recorded or simulated sources.`,
    };
  }
  return {
    headline: 'MIXED',
    notLive,
    summary: `Some adapters are live and some are not. Not live: ${names}.`,
  };
}

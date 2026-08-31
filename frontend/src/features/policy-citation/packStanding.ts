/**
 * The one place the console decides what standing a policy pack has.
 *
 * Before this module there were two, and they could disagree. The shell painted its badge green when
 * `policy_mode === 'verified'` — the *requested runtime mode*, which says nothing about the pack that
 * actually loaded — while the policy screen computed `status === 'approved' && verified_mode_eligible`.
 * Set `POLICY_MODE=verified` against a draft pack and the shell claimed verified standing while the
 * screen said otherwise, on the same product, in the same session.
 *
 * Both were also binary, and that lost the ladder. `draft`, `official_guidance_dated` and `retired`
 * all rendered identically, so a **retired** pack was indistinguishable from a currently-dated
 * official one. On a screen whose whole job is to say where a rupee figure came from, that is the
 * expensive kind of wrong.
 *
 * Three rules this module holds:
 *
 *   1. **The ladder is preserved.** Four rungs from `PolicyPackInfo.status`, each rendered
 *      distinctly, plus the eligibility flag that sits beside `approved`.
 *   2. **Unknown stays unknown.** An absent, empty or unrecognised value is `unknown` — never
 *      silently `false`, never a fabricated verified state. `approved` with an unpublished
 *      eligibility flag is also `unknown`, because claiming either answer would assert a fact the
 *      contract did not supply.
 *   3. **No legal prose.** `docs/38-phase4-verified-policy.md` §4 makes `_reason()` in
 *      `compensation.py` the single place standing is rendered as "current law", and says it is not
 *      to be duplicated. So nothing here produces that claim; the screen renders the contract's own
 *      `disclaimer` verbatim instead.
 *
 * Owner: Stream D.
 */

/**
 * The status ladder, exactly as `PolicyPackInfo.status` publishes it.
 *
 * Mirrors `_ALLOWED_STATUSES` in `backend/app/policy/loader.py`. Anything outside this set is treated
 * as unknown rather than mapped to the nearest rung.
 */
export const PACK_STATUS_LADDER = [
  'draft',
  'official_guidance_dated',
  'approved',
  'retired',
] as const;

export type PackStatus = (typeof PACK_STATUS_LADDER)[number];

/**
 * The sentinel `source-metadata.yaml` carries until the primary document is archived.
 *
 * Recorded in `docs/38-phase4-verified-policy.md` §2: a verified-mode load must fail while the hash is
 * this placeholder. Recognising it here reports a fact the contract already publishes — it does not
 * re-implement that gate, which is Stream B's (G3).
 */
export const SOURCE_PENDING_ARCHIVAL = 'PENDING_ARCHIVAL';

/**
 * The shape `pack.source_hash` has to have before this console will call it a checkable digest.
 *
 * `verify_source_document` in `backend/app/policy/loader.py` accepts a recorded digest only as
 * `_SHA256_HEX_LENGTH` hex characters after `.strip().lower()`, and docs/38 §2 makes that the
 * condition a verified load turns on. This mirrors that test rather than inventing a second hash
 * format. The contract in `schemas/policy.py` deliberately promises no format — it passes the pack's
 * recorded value through verbatim — so the rule is cited from the loader, which owns it.
 *
 * It exists because the previous check was "not the sentinel, therefore archived", which would call
 * any non-empty string a verifiable digest and print copy inviting a reader to check the text
 * against its source. Verified mode is the one mode where that field is expected to carry a real
 * digest, so it is also the one mode where guessing is least affordable.
 */
const SOURCE_SHA256_HEX = /^[0-9a-f]{64}$/;

export type PackStandingKind =
  'verified' | 'approved_not_eligible' | 'official_dated' | 'draft' | 'retired' | 'unknown';

/**
 * Whether the primary source document behind the pack has a real digest recorded.
 *
 * Four states, because "a digest is recorded" and "the recorded digest could be a SHA-256" are
 * different facts, and only the second one lets a reader check the text against its source.
 * `malformed` says a value is present and is not that — it does **not** claim a hash mismatch,
 * which only the backend can determine because only the backend holds the file.
 *
 * `archived` is therefore a statement about form, not about archival: the pack contract publishes
 * neither `archived` nor `source_document_verified`, both of which exist on `LoadedPack`, so the
 * console cannot report the load-time verdict. Its copy says so rather than implying the document was
 * checked here. Publishing `source_document_verified` on `PolicyPackInfo` would let this report the
 * fact instead of the shape; that is Stream A's contract to widen, not this module's to guess.
 */
export type SourceIntegrity = 'archived' | 'not_archived' | 'malformed' | 'unknown';

export interface PackStanding {
  kind: PackStandingKind;
  /** The ladder token exactly as published, or `null` when the contract published none. */
  status: string | null;
  /** As published. `null` when absent — never defaulted to `false`. */
  verifiedModeEligible: boolean | null;
  /** Always rendered: colour is never the only signal. */
  label: string;
  /** Why this standing, in terms of the fields that produced it. */
  detail: string;
  tone: 'ok' | 'warn' | 'crit' | 'neutral';
  sourceIntegrity: SourceIntegrity;
  /**
   * The recorded digest or sentinel, verbatim. `null` when the contract published none.
   *
   * The real G4 endpoint passes `LoadedPack.source_content_sha256` straight through, so for the
   * charter pack this is the `PENDING_ARCHIVAL` sentinel rather than `null`; `null` is reserved for
   * a pack that records no digest at all. Two different states, reported differently.
   */
  sourceHash: string | null;
  /** True only when the standing could not be established from the contract. */
  isUnknown: boolean;
}

/** The subset of any pack-bearing payload this derivation reads. */
export interface PackStandingInput {
  status?: string | null;
  verified_mode_eligible?: boolean | null;
  source_hash?: string | null;
}

function sourceIntegrityFor(sourceHash: string | null): SourceIntegrity {
  if (sourceHash === null) return 'unknown';
  const recorded = sourceHash.trim();
  if (recorded === '') return 'unknown';
  if (recorded === SOURCE_PENDING_ARCHIVAL) return 'not_archived';
  return SOURCE_SHA256_HEX.test(recorded.toLowerCase()) ? 'archived' : 'malformed';
}

function isLadderStatus(value: string): value is PackStatus {
  return (PACK_STATUS_LADDER as readonly string[]).includes(value);
}

/**
 * Classifies a pack's standing from the fields the contract published.
 *
 * Accepts a loose shape on purpose. The declared TypeScript union describes what the contract
 * *promises*; this function has to cope with what a running server actually sends, including nothing
 * at all — which is exactly the case that must land on `unknown` instead of on `false`.
 */
export function packStanding(pack: PackStandingInput | null | undefined): PackStanding {
  const rawStatus = typeof pack?.status === 'string' ? pack.status.trim() : '';
  const status = rawStatus === '' ? null : rawStatus;
  const eligible =
    typeof pack?.verified_mode_eligible === 'boolean' ? pack.verified_mode_eligible : null;
  const sourceHash =
    typeof pack?.source_hash === 'string' && pack.source_hash.trim() !== ''
      ? pack.source_hash
      : null;
  const sourceIntegrity = sourceIntegrityFor(sourceHash);

  const base = { status, verifiedModeEligible: eligible, sourceIntegrity, sourceHash };

  if (status === null || !isLadderStatus(status)) {
    return {
      ...base,
      kind: 'unknown',
      tone: 'warn',
      label: 'standing unknown',
      detail:
        status === null
          ? 'This response published no pack status, so the pack\u2019s standing cannot be established. It is reported as unknown rather than assumed.'
          : `This response published the status \u201c${status}\u201d, which is not a rung of the ladder the pack format defines. It is reported as unknown rather than mapped to the nearest one.`,
      isUnknown: true,
    };
  }

  if (status === 'retired') {
    return {
      ...base,
      kind: 'retired',
      tone: 'crit',
      label: 'retired pack',
      detail:
        'The pack is retired. Nothing it computes may be presented as a current entitlement, and it is shown here only so a recorded decision remains explicable.',
      isUnknown: false,
    };
  }

  if (status === 'draft') {
    return {
      ...base,
      kind: 'draft',
      tone: 'warn',
      label: 'draft pack',
      detail:
        'The pack is a draft. It has had no review, so it claims nothing and computes nothing in verified mode.',
      isUnknown: false,
    };
  }

  if (status === 'official_guidance_dated') {
    return {
      ...base,
      kind: 'official_dated',
      tone: 'warn',
      label: 'official but dated',
      detail:
        'An official publication that has not been confirmed against the current regulation. Its figures are cited as published on that date, not as the position today.',
      isUnknown: false,
    };
  }

  // `approved` — the only rung that can carry verified standing, and only with the flag beside it.
  if (eligible === true) {
    return {
      ...base,
      kind: 'verified',
      tone: 'ok',
      label: 'reviewed and approved',
      detail:
        'The pack is approved and declares itself eligible for verified mode, so a reviewer signed it. Its legal standing is stated by the response\u2019s own disclaimer, which is rendered verbatim rather than restated here.',
      isUnknown: false,
    };
  }

  if (eligible === false) {
    return {
      ...base,
      kind: 'approved_not_eligible',
      tone: 'warn',
      label: 'approved, not eligible',
      detail:
        'The pack is approved but declares itself ineligible for verified mode, so approval alone does not give it verified standing.',
      isUnknown: false,
    };
  }

  return {
    ...base,
    kind: 'unknown',
    tone: 'warn',
    label: 'standing unknown',
    detail:
      'The pack is approved, but this response published no verified_mode_eligible flag. Verified standing needs both, and asserting either answer here would claim a fact the contract did not supply.',
    isUnknown: true,
  };
}

/** Human-readable copy for the source-document integrity states. */
export const SOURCE_INTEGRITY_COPY: Record<SourceIntegrity, string> = {
  archived:
    'A SHA-256 digest is recorded for the primary document, so the text behind these figures can be checked against the archived original. Whether that document is present and still hashes to this value is checked when the pack loads, not here.',
  not_archived: `The source digest is still the ${SOURCE_PENDING_ARCHIVAL} sentinel, so the primary document behind these figures has not been archived or hashed.`,
  malformed:
    'A source digest is recorded but it is not in the SHA-256 form the pack format requires, so the text behind these figures cannot be checked against the archived original from here.',
  unknown: 'This response published no source digest, so source integrity is unknown.',
};

/**
 * The applicability tri-state, counted.
 *
 * `undetermined` is the contract's own third state and the screen previously dropped it, reading only
 * `missing_facts`. An undetermined applicability with no missing facts listed would therefore have
 * rendered as nothing at all. Counting is array length over rows the server already classified.
 */
export interface ApplicabilitySummary {
  applicable: number;
  notApplicable: number;
  undetermined: number;
  /** Rows whose status the contract did not publish, or published outside the tri-state. */
  unknown: number;
  total: number;
  missingFacts: string[];
  /** True when any row is undetermined or unknown — i.e. the question is genuinely open. */
  hasOpenQuestion: boolean;
}

export function summariseApplicability(
  entries:
    readonly { status?: string | null; missing_facts?: string[] | null }[] | null | undefined,
): ApplicabilitySummary {
  const rows = entries ?? [];
  const of = (want: string) => rows.filter((row) => row.status === want).length;
  const applicable = of('applicable');
  const notApplicable = of('not_applicable');
  const undetermined = of('undetermined');
  const unknown = rows.length - applicable - notApplicable - undetermined;

  const missingFacts = [
    ...new Set(rows.flatMap((row) => row.missing_facts ?? []).filter((fact) => fact !== '')),
  ];

  return {
    applicable,
    notApplicable,
    undetermined,
    unknown,
    total: rows.length,
    missingFacts,
    hasOpenQuestion: undetermined + unknown > 0,
  };
}

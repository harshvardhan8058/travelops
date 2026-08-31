import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import {
  PACK_STATUS_LADDER,
  SOURCE_INTEGRITY_COPY,
  SOURCE_PENDING_ARCHIVAL,
  packStanding,
  summariseApplicability,
  type PackStandingInput,
} from './packStanding';

/** The charter pack as `fixtures/api/policy.json` publishes it — the C-owned input contract. */
const CHARTER: PackStandingInput = {
  status: 'official_guidance_dated',
  verified_mode_eligible: false,
  source_hash: SOURCE_PENDING_ARCHIVAL,
};

/**
 * A syntactically valid SHA-256: 64 hex characters, which is what the contract documents.
 *
 * Obviously synthetic on purpose. Earlier tests used 16-character strings as stand-ins for a digest,
 * which is what let a value that could not be a SHA-256 read as an archived, checkable document.
 */
const ARCHIVED_DIGEST = '9f2c'.repeat(16);

describe('packStanding — the status ladder', () => {
  it('keeps all four rungs distinct, so a retired pack cannot read like a current one', () => {
    expect(PACK_STATUS_LADDER).toEqual(['draft', 'official_guidance_dated', 'approved', 'retired']);
    const kinds = PACK_STATUS_LADDER.map(
      (status) => packStanding({ status, verified_mode_eligible: true }).kind,
    );
    expect(kinds).toEqual(['draft', 'official_dated', 'verified', 'retired']);
    expect(new Set(kinds).size).toBe(4);
    // And each rung is visually distinguishable, not just internally distinct.
    const tones = PACK_STATUS_LADDER.map(
      (status) => packStanding({ status, verified_mode_eligible: true }).tone,
    );
    expect(tones).toEqual(['warn', 'warn', 'ok', 'crit']);
  });

  it('reads the charter pack as official but dated, never as verified', () => {
    const standing = packStanding(CHARTER);
    expect(standing.kind).toBe('official_dated');
    expect(standing.tone).toBe('warn');
    expect(standing.isUnknown).toBe(false);
    expect(standing.status).toBe('official_guidance_dated');
    expect(standing.verifiedModeEligible).toBe(false);
    expect(standing.label).toBe('official but dated');
  });

  it('reads an approved and eligible pack as verified', () => {
    const standing = packStanding({
      status: 'approved',
      verified_mode_eligible: true,
      source_hash: ARCHIVED_DIGEST,
    });
    expect(standing.kind).toBe('verified');
    expect(standing.tone).toBe('ok');
    expect(standing.isUnknown).toBe(false);
    expect(standing.sourceIntegrity).toBe('archived');
  });

  it('refuses verified standing when the pack is approved but declares itself ineligible', () => {
    const standing = packStanding({ status: 'approved', verified_mode_eligible: false });
    expect(standing.kind).toBe('approved_not_eligible');
    expect(standing.tone).toBe('warn');
    expect(standing.isUnknown).toBe(false);
  });

  it('treats a retired pack as the most serious rung', () => {
    const standing = packStanding({ status: 'retired', verified_mode_eligible: true });
    expect(standing.kind).toBe('retired');
    expect(standing.tone).toBe('crit');
  });

  it('reads a draft pack as claiming nothing', () => {
    const standing = packStanding({ status: 'draft', verified_mode_eligible: false });
    expect(standing.kind).toBe('draft');
    expect(standing.tone).toBe('warn');
  });

  it('never reports verified for any rung other than approved, whatever the flag says', () => {
    for (const status of PACK_STATUS_LADDER) {
      if (status === 'approved') continue;
      expect(packStanding({ status, verified_mode_eligible: true }).kind).not.toBe('verified');
    }
  });
});

describe('packStanding — unknown stays unknown', () => {
  it('reports an absent pack as unknown rather than as not verified', () => {
    for (const input of [null, undefined, {}]) {
      const standing = packStanding(input as PackStandingInput | null | undefined);
      expect(standing.kind).toBe('unknown');
      expect(standing.isUnknown).toBe(true);
      expect(standing.label).toBe('standing unknown');
      // The absent value is carried as null, never coerced to false or to a rung.
      expect(standing.status).toBeNull();
      expect(standing.verifiedModeEligible).toBeNull();
    }
  });

  it('reports an unrecognised status as unknown and keeps the raw token for display', () => {
    const standing = packStanding({ status: 'provisionally_blessed' });
    expect(standing.kind).toBe('unknown');
    expect(standing.isUnknown).toBe(true);
    expect(standing.status).toBe('provisionally_blessed');
    expect(standing.detail).toContain('provisionally_blessed');
  });

  it('treats an empty or whitespace status as absent, not as a rung', () => {
    expect(packStanding({ status: '' }).status).toBeNull();
    expect(packStanding({ status: '   ' }).kind).toBe('unknown');
  });

  it('refuses to guess when the pack is approved but publishes no eligibility flag', () => {
    const standing = packStanding({ status: 'approved' });
    expect(standing.kind).toBe('unknown');
    expect(standing.isUnknown).toBe(true);
    expect(standing.verifiedModeEligible).toBeNull();
    expect(standing.detail).toContain('verified_mode_eligible');
  });

  it('does not accept a truthy non-boolean eligibility value as approval', () => {
    const standing = packStanding({
      status: 'approved',
      verified_mode_eligible: 'yes' as unknown as boolean,
    });
    expect(standing.kind).toBe('unknown');
    expect(standing.verifiedModeEligible).toBeNull();
  });

  it('never returns a verified kind from data that did not say so', () => {
    const fabrications: (PackStandingInput | null | undefined)[] = [
      null,
      undefined,
      {},
      { status: 'approved' },
      { status: 'draft', verified_mode_eligible: true },
      { status: 'retired', verified_mode_eligible: true },
      { status: 'nonsense', verified_mode_eligible: true },
      { verified_mode_eligible: true },
    ];
    for (const input of fabrications) {
      expect(packStanding(input).kind).not.toBe('verified');
    }
  });
});

describe('packStanding — source document integrity', () => {
  it('reports the PENDING_ARCHIVAL sentinel as not archived', () => {
    const standing = packStanding(CHARTER);
    expect(standing.sourceIntegrity).toBe('not_archived');
    expect(standing.sourceHash).toBe(SOURCE_PENDING_ARCHIVAL);
    expect(SOURCE_INTEGRITY_COPY.not_archived).toContain(SOURCE_PENDING_ARCHIVAL);
  });

  it('reports a real digest as archived', () => {
    expect(packStanding({ status: 'approved', source_hash: ARCHIVED_DIGEST }).sourceIntegrity).toBe(
      'archived',
    );
    // Case is normalised the way `verify_source_document` normalises it, so an upper-case digest is
    // not reported as unusable.
    expect(
      packStanding({ status: 'approved', source_hash: ARCHIVED_DIGEST.toUpperCase() })
        .sourceIntegrity,
    ).toBe('archived');
    // The digest is carried verbatim even so: it is a citation, not a normalised value.
    expect(
      packStanding({ status: 'approved', source_hash: ARCHIVED_DIGEST.toUpperCase() }).sourceHash,
    ).toBe(ARCHIVED_DIGEST.toUpperCase());
  });

  it('refuses to call a recorded value archived when it cannot be the documented SHA-256', () => {
    /*
     * The old rule was "not the sentinel, therefore archived", so any non-empty string produced copy
     * inviting the reader to check the text against its source. Verified mode is precisely where a
     * real digest is expected, so a value that cannot be one is reported as its own state.
     */
    for (const sourceHash of [
      'a1b2c3d4e5f60718', // too short to be a SHA-256
      `${ARCHIVED_DIGEST}00`, // too long
      `${ARCHIVED_DIGEST.slice(0, 63)}z`, // right length, not hex
      'ARCHIVE_PENDING', // a different sentinel nobody has taught this console
    ]) {
      const standing = packStanding({
        status: 'approved',
        verified_mode_eligible: true,
        source_hash: sourceHash,
      });
      expect(standing.sourceIntegrity).toBe('malformed');
      // Reported, never silently upgraded — and the recorded value is still shown verbatim.
      expect(standing.sourceHash).toBe(sourceHash);
    }
  });

  it('does not claim a hash mismatch, which only the backend can determine', () => {
    // The console holds no file, so it can say a value is not a SHA-256 but never that it is the
    // wrong SHA-256. docs/38 G3 makes the comparison Stream B's.
    const copy = SOURCE_INTEGRITY_COPY.malformed.toLowerCase();
    expect(copy).not.toContain('mismatch');
    expect(copy).not.toContain('does not match');
    expect(copy).not.toContain('tampered');
  });

  it('reports an absent digest as unknown, not as archived', () => {
    expect(packStanding({ status: 'approved' }).sourceIntegrity).toBe('unknown');
    expect(packStanding({ status: 'approved', source_hash: '' }).sourceIntegrity).toBe('unknown');
    expect(packStanding({ status: 'approved', source_hash: null }).sourceHash).toBeNull();
  });

  it('handles the real endpoint shape: the sentinel passed through, not null', () => {
    /*
     * The live contract for the charter pack, against the running API:
     *   status official_guidance_dated · verified_mode_eligible false · source_hash PENDING_ARCHIVAL
     *
     * This test previously asserted `source_hash: null` and said the endpoint published null "rather
     * than echoing the pack's PENDING_ARCHIVAL sentinel". That is inverted: `api/policy.py` passes
     * `LoadedPack.source_content_sha256` through verbatim, and the backend e2e test named
     * `test_the_source_hash_is_the_digest_the_pack_records` locks it. So the state the charter pack
     * actually produces is `not_archived`, and it was documented here as impossible.
     */
    const standing = packStanding(CHARTER);
    expect(standing.kind).toBe('official_dated');
    expect(standing.sourceIntegrity).toBe('not_archived');
    expect(standing.sourceHash).toBe(SOURCE_PENDING_ARCHIVAL);
    // Source integrity must not drag the ladder rung down, and must not read as archived.
    expect(standing.isUnknown).toBe(false);
  });

  it('still reports a pack that records no digest at all as unknown', () => {
    // `null` remains reachable and means something different from the sentinel: no digest recorded,
    // versus a digest explicitly pending archival.
    const standing = packStanding({
      status: 'official_guidance_dated',
      verified_mode_eligible: false,
      source_hash: null,
    });
    expect(standing.kind).toBe('official_dated');
    expect(standing.sourceIntegrity).toBe('unknown');
    expect(standing.sourceHash).toBeNull();
    expect(standing.isUnknown).toBe(false);
    expect(SOURCE_INTEGRITY_COPY[standing.sourceIntegrity]).toMatch(/unknown/i);
  });

  it('keeps source integrity independent of the review ladder', () => {
    // An approved, eligible pack whose source was never archived is still verified on the ladder;
    // the two facts are reported separately, exactly as docs/38 §2 keeps them separate.
    const standing = packStanding({
      status: 'approved',
      verified_mode_eligible: true,
      source_hash: SOURCE_PENDING_ARCHIVAL,
    });
    expect(standing.kind).toBe('verified');
    expect(standing.sourceIntegrity).toBe('not_archived');
  });
});

/**
 * The four states an operator can actually be looking at, as each pack on disk publishes them.
 *
 * Phase 4 G1/G2 readiness is the point of this block: the verified row is the pack that does not
 * exist yet (`policy_packs/in-dgca-car-3m4/`), and it is here so the console's handling of it is
 * settled before the pack lands rather than discovered on the day it does.
 *
 * The demo and charter rows are read off `pack.yaml` and `source-metadata.yaml` on disk, and both were
 * confirmed against the running API. The verified row is necessarily anticipated: it encodes what
 * docs/38 §1 and §2 require of that pack — `status: approved`, `verified_mode_eligible: true`, and an
 * archived document with a real digest — and its digest is synthetic because no such document exists.
 */
describe('packStanding — demo, charter, verified and unknown', () => {
  const PACKS = {
    // policy_packs/demo-fixture/1.0 — fictional, cites nothing, content_sha256: null.
    demo: {
      input: { status: 'draft', verified_mode_eligible: false, source_hash: null },
      kind: 'draft',
      tone: 'warn',
      sourceIntegrity: 'unknown',
    },
    // policy_packs/in-moca-charter-2019/2019.02 — official, dated, content_sha256: PENDING_ARCHIVAL.
    charter: {
      input: CHARTER,
      kind: 'official_dated',
      tone: 'warn',
      sourceIntegrity: 'not_archived',
    },
    // The G1 pack: approved, eligible, and an archived document with a real digest.
    verified: {
      input: {
        status: 'approved',
        verified_mode_eligible: true,
        source_hash: ARCHIVED_DIGEST,
      },
      kind: 'verified',
      tone: 'ok',
      sourceIntegrity: 'archived',
    },
    // Any response that did not publish the fields standing is derived from.
    unknown: {
      input: {},
      kind: 'unknown',
      tone: 'warn',
      sourceIntegrity: 'unknown',
    },
  } as const;

  it('reports each pack on its own terms, and only verified reads as settled', () => {
    for (const [name, expected] of Object.entries(PACKS)) {
      const standing = packStanding(expected.input);
      expect(standing.kind, name).toBe(expected.kind);
      expect(standing.tone, name).toBe(expected.tone);
      expect(standing.sourceIntegrity, name).toBe(expected.sourceIntegrity);
      // Every state says something, in words, whichever pack is loaded.
      expect(standing.label.trim().length, name).toBeGreaterThan(0);
      expect(standing.detail.trim().length, name).toBeGreaterThan(0);
    }
    // Exactly one of the four is green, and only because its input declares both approval and
    // eligibility — the pair a reviewer has to put there. No SME has signed anything yet.
    const ok = Object.values(PACKS).filter((pack) => packStanding(pack.input).tone === 'ok');
    expect(ok).toHaveLength(1);
    expect(packStanding(PACKS.verified.input).kind).toBe('verified');
  });

  it('gives the four states four distinct labels, so none can be mistaken for another', () => {
    const labels = Object.values(PACKS).map((pack) => packStanding(pack.input).label);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it('cannot be told a standing by the runtime mode, only by the pack', () => {
    /*
     * The defect this module was built to remove: the shell painted itself green from
     * `policy_mode === 'verified'`. `/system/mode`'s label is still composed from the requested mode
     * in `backend/app/api/health.py` (docs/38 G7, Stream A), so this asserts the derivation ignores a
     * mode entirely — a payload carrying nothing but `policy_mode: 'verified'` is unknown, not
     * verified.
     */
    for (const mode of ['verified', 'charter', 'demo']) {
      const standing = packStanding({ policy_mode: mode } as unknown as PackStandingInput);
      expect(standing.kind).toBe('unknown');
      expect(standing.isUnknown).toBe(true);
      expect(standing.tone).not.toBe('ok');
    }
  });

  it('does not let an unarchived document produce checkable-provenance copy', () => {
    // The three non-archived states must never render the sentence that invites a reader to check
    // the text against its source, because in none of them can they.
    const checkable = SOURCE_INTEGRITY_COPY.archived;
    for (const state of ['not_archived', 'malformed', 'unknown'] as const) {
      expect(SOURCE_INTEGRITY_COPY[state]).not.toBe(checkable);
      expect(SOURCE_INTEGRITY_COPY[state].trim().length).toBeGreaterThan(0);
    }
  });
});

describe('packStanding — every state carries a word, not only a colour', () => {
  it('gives each kind a non-empty label and detail', () => {
    const inputs: PackStandingInput[] = [
      { status: 'approved', verified_mode_eligible: true },
      { status: 'approved', verified_mode_eligible: false },
      { status: 'official_guidance_dated', verified_mode_eligible: false },
      { status: 'draft' },
      { status: 'retired' },
      {},
    ];
    for (const input of inputs) {
      const standing = packStanding(input);
      expect(standing.label.trim().length).toBeGreaterThan(0);
      expect(standing.detail.trim().length).toBeGreaterThan(0);
    }
  });

  it('uses none of the words the browser gate treats as an unbuilt screen', () => {
    /*
     * `verify-console.mjs` fails a route whose DOM matches /not yet built|placeholder/i. The first
     * version of the source-integrity copy described PENDING_ARCHIVAL as a "placeholder" and failed
     * the Policy route for that reason alone. Rendered copy is checked here so the gate catches real
     * unbuilt screens rather than this module's vocabulary.
     */
    const reserved = /not yet built|placeholder/i;
    const rendered = [
      ...Object.values(SOURCE_INTEGRITY_COPY),
      ...(
        [
          { status: 'approved', verified_mode_eligible: true },
          { status: 'approved', verified_mode_eligible: false },
          { status: 'official_guidance_dated', verified_mode_eligible: false },
          { status: 'draft' },
          { status: 'retired' },
          {},
        ] as PackStandingInput[]
      ).flatMap((input) => {
        const standing = packStanding(input);
        return [standing.label, standing.detail];
      }),
    ];
    for (const copy of rendered) {
      expect(copy).not.toMatch(reserved);
    }
  });

  it('never renders a current-law claim, which docs/38 reserves for the backend', () => {
    const inputs: PackStandingInput[] = [
      { status: 'approved', verified_mode_eligible: true },
      { status: 'official_guidance_dated', verified_mode_eligible: false },
      { status: 'retired' },
      {},
    ];
    for (const input of inputs) {
      const standing = packStanding(input);
      const prose = `${standing.label} ${standing.detail}`.toLowerCase();
      expect(prose).not.toContain('current law');
    }
    for (const copy of Object.values(SOURCE_INTEGRITY_COPY)) {
      expect(copy.toLowerCase()).not.toContain('current law');
    }
  });
});

describe('summariseApplicability — the tri-state is preserved', () => {
  it('counts each state the contract publishes', () => {
    const summary = summariseApplicability([
      { status: 'applicable', missing_facts: [] },
      { status: 'not_applicable', missing_facts: [] },
      { status: 'undetermined', missing_facts: ['boarding_denied_reason'] },
    ]);
    expect(summary).toMatchObject({
      applicable: 1,
      notApplicable: 1,
      undetermined: 1,
      unknown: 0,
      total: 3,
      hasOpenQuestion: true,
    });
    expect(summary.missingFacts).toEqual(['boarding_denied_reason']);
  });

  it('flags an undetermined row as an open question even when it lists no missing fact', () => {
    // This is the case the screen previously rendered as nothing at all.
    const summary = summariseApplicability([{ status: 'undetermined', missing_facts: [] }]);
    expect(summary.undetermined).toBe(1);
    expect(summary.missingFacts).toEqual([]);
    expect(summary.hasOpenQuestion).toBe(true);
  });

  it('counts a row with no published status as unknown, not as applicable', () => {
    const summary = summariseApplicability([{ missing_facts: [] }, { status: null }]);
    expect(summary.applicable).toBe(0);
    expect(summary.unknown).toBe(2);
    expect(summary.hasOpenQuestion).toBe(true);
  });

  it('settles only when every row is applicable or not applicable', () => {
    const summary = summariseApplicability([
      { status: 'applicable', missing_facts: [] },
      { status: 'not_applicable', missing_facts: [] },
    ]);
    expect(summary.hasOpenQuestion).toBe(false);
  });

  it('is empty and settled for no rows rather than inventing a state', () => {
    for (const input of [null, undefined, []]) {
      const summary = summariseApplicability(input);
      expect(summary.total).toBe(0);
      expect(summary.hasOpenQuestion).toBe(false);
      expect(summary.missingFacts).toEqual([]);
    }
  });

  it('de-duplicates missing facts across packs without dropping any', () => {
    const summary = summariseApplicability([
      { status: 'undetermined', missing_facts: ['fare_basic', ''] },
      { status: 'undetermined', missing_facts: ['fare_basic', 'fuel_surcharge'] },
    ]);
    expect(summary.missingFacts).toEqual(['fare_basic', 'fuel_surcharge']);
  });
});

/**
 * Guards, not unit tests.
 *
 * The defect this change fixes was two surfaces each deriving legal standing for themselves. Unit
 * tests on the shared module cannot stop a future edit from adding a third derivation, so these read
 * the consuming files and assert the removed patterns have not come back.
 */
describe('no surface derives policy standing for itself', () => {
  /**
   * Comments are blanked before matching, the same way `scripts/check-tokens.mjs` does it.
   *
   * These files deliberately *document* the pattern that was removed, and a guard that fires on its
   * own explanation would teach the next author to delete the explanation rather than keep the fix.
   */
  const stripComments = (source: string) =>
    source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');

  const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');
  const shellRaw = read('../../components/ui/AppShell.tsx');
  const screenRaw = read('./PolicyScreen.tsx');
  const shell = stripComments(shellRaw);
  const screen = stripComments(screenRaw);

  it('the shell no longer infers standing from the requested runtime mode', () => {
    expect(shell).not.toMatch(/policy_mode\s*===/);
    expect(shell).toContain('PackStandingChip');
  });

  it('the policy screen no longer computes its own verified boolean', () => {
    expect(screen).not.toMatch(/status\s*===\s*['"]approved['"]/);
    expect(screen).not.toMatch(/verified_mode_eligible\s*&&/);
    expect(screen).not.toContain('PackStatusBanner');
  });

  it('both surfaces render the shared standing component', () => {
    expect(shell).toMatch(/from '@\/features\/policy-citation\/PackStandingView'/);
    expect(screen).toMatch(/from '\.\/PackStandingView'/);
    expect(screen).toMatch(/packStanding\(policy\.pack\)/);
  });

  it('no ladder or mode string is hardcoded as a standing decision in either surface', () => {
    for (const source of [shell, screen]) {
      // A bare quoted mode or rung used in a comparison is the pattern being kept out.
      expect(source).not.toMatch(/===\s*['"](verified|charter|demo|approved|retired|draft)['"]/);
    }
  });

  it('keeps the comment that records why the inference was removed', () => {
    // The fix is only durable if the reason survives with it.
    expect(shellRaw).toMatch(/policy_mode === 'verified'/);
  });

  /**
   * The browser gate must not pin a value whose purpose is to change.
   *
   * It asserted `PENDING_ARCHIVAL`, then the charter pack's rung, label and runtime mode. Each is a
   * property of one pack, so the gate would have failed on the day Phase 4's verified pack loaded and
   * the fix under that pressure is always to delete assertions. The tokens are now read from
   * `GET /incidents/{id}/policy`, so the gate verifies whichever pack is loaded — demo, charter or
   * verified — and this guard keeps any pack-specific literal from creeping back in.
   */
  it('the browser gate reads the policy tokens from the contract, not from this repository', () => {
    const verifier = readFileSync(
      new URL('../../../scripts/verify-console.mjs', import.meta.url),
      'utf8',
    );
    const policyRoute = stripComments(
      verifier.slice(
        verifier.indexOf("name: 'Policy'"),
        verifier.indexOf("name: 'Provenance ledger'"),
      ),
    );
    // Derived, not pinned.
    expect(policyRoute).toMatch(/derive:\s*policyExpectations/);
    // No transient digest, no ladder rung, no pack name and no runtime mode as a literal.
    expect(policyRoute).not.toMatch(/PENDING_ARCHIVAL/);
    expect(policyRoute).not.toMatch(/official_guidance_dated|approved|retired|draft/);
    expect(policyRoute).not.toMatch(/MoCA|charter|verified|demo/i);

    // And the deriving function asserts the contract's own values, uncased, plus the row structure.
    const derive = stripComments(
      verifier.slice(
        verifier.indexOf('async function policyExpectations'),
        verifier.indexOf("name: 'Command Center'"),
      ),
    );
    expect(derive).toMatch(/pack\.status/);
    expect(derive).toMatch(/pack\.ui_label/);
    expect(derive).toMatch(/expectExactCase:\s*exact/);
    expect(derive).toMatch(/source hash/);
    // The derived standing must be resolved, not merely echoed: the banner printing the unknown label
    // beside a perfectly good status/eligibility pair has to fail this route.
    expect(derive).toMatch(/absent:\s*\['standing unknown'\]/);

    /*
     * And the gate must not depend on an environment variable the documented invocation does not set.
     * `make verify-console` runs on the host and exports neither VITE_API_BASE_URL nor
     * VERIFY_API_BASE_URL, so without a fallback this route failed on configuration while the browser
     * bundle — which has its own fallback in `api/client.ts` — talked to the API perfectly well.
     */
    expect(stripComments(verifier)).toMatch(
      /CONTRACT_API_BASE\s*=[^;]*\|\|\s*'http:\/\/127\.0\.0\.1:8000\/api\/v1'/,
    );
  });

  it('the policy screen names an absent contract value instead of rendering a blank', () => {
    // `source_hash` is null on the real endpoint, so the raw interpolation left the label over an
    // empty space. Every nullable pack field now goes through `Recorded`.
    expect(screen).toMatch(/function Recorded\(/);
    expect(screen).toMatch(/not recorded/);
    expect(screen).not.toMatch(/<MonoValue muted>\{policy\.pack\.source_hash\}<\/MonoValue>/);
    expect(screen).toMatch(/<Recorded\s+value=\{policy\.pack\.source_hash\}/);
  });

  it('introduces no fixture path in the policy feature or the shell', () => {
    const sources = [
      shellRaw,
      screenRaw,
      read('./PackStandingView.tsx'),
      read('./packStanding.ts'),
    ].map(stripComments);
    for (const source of sources) {
      expect(source).not.toContain('/fixtures/');
      expect(source).not.toContain('VITE_USE_FIXTURES');
    }
  });
});

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
      source_hash: 'a1b2c3d4e5f60718',
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
    expect(
      packStanding({ status: 'approved', source_hash: 'c41a7e9b28d5f603' }).sourceIntegrity,
    ).toBe('archived');
  });

  it('reports an absent digest as unknown, not as archived', () => {
    expect(packStanding({ status: 'approved' }).sourceIntegrity).toBe('unknown');
    expect(packStanding({ status: 'approved', source_hash: '' }).sourceIntegrity).toBe('unknown');
    expect(packStanding({ status: 'approved', source_hash: null }).sourceHash).toBeNull();
  });

  it('handles the real endpoint shape: a dated pack with no digest published at all', () => {
    /*
     * The live contract for the charter pack, verified against the running API:
     *   status official_guidance_dated · verified_mode_eligible false · source_hash null
     * The endpoint publishes null rather than echoing the pack's PENDING_ARCHIVAL sentinel, because
     * that field is documented as a SHA-256 and a sentinel is not one.
     */
    const standing = packStanding({
      status: 'official_guidance_dated',
      verified_mode_eligible: false,
      source_hash: null,
    });
    expect(standing.kind).toBe('official_dated');
    expect(standing.sourceIntegrity).toBe('unknown');
    expect(standing.sourceHash).toBeNull();
    // Unknown source integrity must not drag the ladder rung down, and must not read as archived.
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
   * `verify-console.mjs` asserted `PENDING_ARCHIVAL` on the Policy route. The real G4 endpoint
   * publishes `source_hash: null` deliberately — a sentinel is not a SHA-256, and a backend e2e test
   * locks the null — so the token could never appear. It was doubly wrong: even once published it
   * would vanish the day the document is archived. Structure is asserted there; the state machine is
   * asserted here.
   */
  it('the browser gate does not pin the transient source-hash sentinel', () => {
    const verifier = readFileSync(
      new URL('../../../scripts/verify-console.mjs', import.meta.url),
      'utf8',
    );
    const policyRoute = verifier.slice(
      verifier.indexOf("name: 'Policy'"),
      verifier.indexOf("name: 'Provenance ledger'"),
    );
    expect(policyRoute).not.toMatch(/expect(ExactCase)?:[^\]]*PENDING_ARCHIVAL/);
    // The stable assertions are still in place.
    expect(policyRoute).toMatch(/official_guidance_dated/);
    expect(policyRoute).toMatch(/source hash/);
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

/**
 * The load-bearing property here is that nothing claims live without the backend saying so, and
 * that a mode which delivers nothing is never described as one that does.
 */

import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { deriveEvidencePosture, deriveModeChips, type ModeLabel } from './runtimeModes';
import type { SystemMode } from './types';

function systemMode(overrides: Partial<SystemMode> = {}): SystemMode {
  return {
    llm_mode: 'off',
    weather_mode: 'fixture',
    flight_status_mode: 'fixture',
    notification_mode: 'console',
    policy_mode: 'charter',
    real_email_enabled: false,
    app_env: 'demo',
    assurance: {
      config_present: true,
      config_version: 'assurance-v1',
      config_hash: 'f3964eb196257d1d',
      workflow_executable: true,
    },
    degradations: [],
    policy_pack: { id: 'in-moca-charter-2019', version: '2019.02', ui_label: 'CHARTER' },
    limits: { max_workflow_steps: 20, action_timeout_seconds: 30 },
    data_seed: 20260807,
    ...overrides,
  };
}

function chip(mode: SystemMode | undefined, label: ModeLabel) {
  const found = deriveModeChips(mode).find((entry) => entry.label === label);
  if (!found) throw new Error(`no ${label} chip`);
  return found;
}

describe('deriveModeChips', () => {
  it('always publishes exactly the four adapters, in a fixed order', () => {
    expect(deriveModeChips(systemMode()).map((c) => c.label)).toEqual([
      'LLM',
      'FLT',
      'WX',
      'NOTIFY',
    ]);
  });

  it('publishes all four while the modes are still loading, so none reads as absent', () => {
    const chips = deriveModeChips(undefined);

    expect(chips.map((c) => c.label)).toEqual(['LLM', 'FLT', 'WX', 'NOTIFY']);
    expect(chips.every((c) => c.posture === 'unknown')).toBe(true);
    expect(chips.every((c) => c.value === null)).toBe(true);
  });

  it('never reports a live posture for any adapter before the modes arrive', () => {
    expect(deriveModeChips(undefined).some((c) => c.posture === 'live')).toBe(false);
  });
});

describe('LLM chip', () => {
  it.each([
    ['live', 'live'],
    ['fixture', 'fixture'],
    ['off', 'off'],
  ] as const)('reports %s as posture %s', (llm_mode, posture) => {
    const view = chip(systemMode({ llm_mode }), 'LLM');
    expect(view.value).toBe(llm_mode);
    expect(view.posture).toBe(posture);
  });

  it('keeps the deterministic fallback visible rather than describing off as a model', () => {
    const view = chip(systemMode({ llm_mode: 'off' }), 'LLM');

    expect(view.posture).toBe('off');
    expect(view.detail).toMatch(/deterministic fallback/i);
    expect(view.detail).not.toMatch(/\blive\b/i);
  });
});

describe('FLT chip', () => {
  it('exists at all — the shell reported three adapters and observed flight state was not one', () => {
    expect(chip(systemMode(), 'FLT')).toBeDefined();
  });

  it.each([
    ['live', 'live'],
    ['fixture', 'fixture'],
  ] as const)('reports %s as posture %s', (flight_status_mode, posture) => {
    const view = chip(systemMode({ flight_status_mode }), 'FLT');
    expect(view.value).toBe(flight_status_mode);
    expect(view.posture).toBe(posture);
  });

  it('reads the published effective mode, so a degraded live request never shows live', () => {
    // `resolve_modes` publishes `fixture` and names the reason when a live request had no key.
    const degradation =
      'FLIGHT_STATUS_MODE=live requested without AVIATIONSTACK_API_KEY; ' +
      'degraded to the committed fixture snapshot';
    const view = chip(
      systemMode({ flight_status_mode: 'fixture', degradations: [degradation] }),
      'FLT',
    );

    expect(view.posture).toBe('fixture');
    expect(view.value).toBe('fixture');
    expect(view.degradation).toBe(degradation);
  });
});

describe('WX chip', () => {
  it.each([
    ['live', 'live'],
    ['fixture', 'fixture'],
  ] as const)('reports %s as posture %s', (weather_mode, posture) => {
    expect(chip(systemMode({ weather_mode }), 'WX').posture).toBe(posture);
  });

  it('does not call a committed snapshot live METAR', () => {
    expect(chip(systemMode({ weather_mode: 'fixture' }), 'WX').detail).toMatch(/not live weather/i);
  });
});

describe('NOTIFY chip', () => {
  it('treats console as simulated, because nothing is delivered', () => {
    const view = chip(systemMode({ notification_mode: 'console' }), 'NOTIFY');

    expect(view.posture).toBe('simulated');
    expect(view.detail).toMatch(/nothing is delivered/i);
  });

  it.each(['mailtrap', 'gmail'] as const)(
    'reports %s as simulated when real delivery is not enabled',
    (notification_mode) => {
      // Credentials can be present and the allowlist still empty; the backend records those
      // deliveries as simulated and leaves `real_email_enabled` false.
      const view = chip(systemMode({ notification_mode, real_email_enabled: false }), 'NOTIFY');

      expect(view.posture).toBe('simulated');
      expect(view.value).toBe(notification_mode);
    },
  );

  it.each(['mailtrap', 'gmail'] as const)(
    'reports %s as live only when real_email_enabled is true',
    (notification_mode) => {
      const view = chip(systemMode({ notification_mode, real_email_enabled: true }), 'NOTIFY');

      expect(view.posture).toBe('live');
      expect(view.detail).toMatch(/real allowlisted recipients/i);
    },
  );

  it('uses real_email_enabled rather than the mode string as the discriminator', () => {
    const configured = chip(
      systemMode({ notification_mode: 'gmail', real_email_enabled: false }),
      'NOTIFY',
    );
    const delivering = chip(
      systemMode({ notification_mode: 'gmail', real_email_enabled: true }),
      'NOTIFY',
    );

    // Same mode string, opposite postures.
    expect(configured.value).toBe(delivering.value);
    expect(configured.posture).not.toBe(delivering.posture);
  });

  it('attaches the empty-allowlist degradation to NOTIFY', () => {
    const degradation = 'DEMO_RECIPIENT_ALLOWLIST is empty; all deliveries recorded as simulated';
    const view = chip(systemMode({ degradations: [degradation] }), 'NOTIFY');

    expect(view.degradation).toBe(degradation);
  });
});

describe('degradation attribution', () => {
  it('attaches each degradation to its own adapter and no other', () => {
    const chips = deriveModeChips(
      systemMode({
        llm_mode: 'fixture',
        degradations: ['LLM_MODE=live requested without OPENROUTER_API_KEY; degraded to fixture'],
      }),
    );

    const degraded = chips.filter((c) => c.degradation !== null).map((c) => c.label);
    expect(degraded).toEqual(['LLM']);
  });

  it('does not flag a descriptive entry that names a mode without downgrading it', () => {
    // The committed demo fixture carries exactly these two. Both are true and neither is a
    // downgrade: off and fixture are what was requested. Marking them warned about nothing.
    const chips = deriveModeChips(
      systemMode({
        degradations: [
          'LLM_MODE=off: recovery runs on the deterministic fallback playbook',
          'WEATHER_MODE=fixture: serving the committed snapshot, not live METAR',
        ],
      }),
    );

    expect(chips.filter((c) => c.degradation !== null)).toEqual([]);
  });

  it.each([
    'LLM_MODE=live requested without OPENROUTER_API_KEY; degraded to fixture',
    'FLIGHT_STATUS_MODE=live requested without AVIATIONSTACK_API_KEY; degraded to the committed fixture snapshot',
    'NOTIFICATION_MODE=gmail missing SMTP_HOST; degraded to console',
    'DEMO_RECIPIENT_ALLOWLIST is empty; all deliveries recorded as simulated',
  ])('flags the real downgrade %#, which the backend does emit', (entry) => {
    const chips = deriveModeChips(systemMode({ degradations: [entry] }));

    expect(chips.filter((c) => c.degradation === entry)).toHaveLength(1);
  });

  it('ignores a degradation it does not recognise rather than misattributing it', () => {
    const chips = deriveModeChips(
      systemMode({ degradations: ['assurance config not found; workflow execution is blocked'] }),
    );

    expect(chips.every((c) => c.degradation === null)).toBe(true);
  });

  it('tolerates a payload with no degradations array at all', () => {
    const mode = systemMode();
    delete (mode as { degradations?: string[] }).degradations;

    expect(() => deriveModeChips(mode)).not.toThrow();
    expect(deriveModeChips(mode).every((c) => c.degradation === null)).toBe(true);
  });
});

describe('deriveEvidencePosture', () => {
  it('is UNKNOWN before the modes arrive', () => {
    expect(deriveEvidencePosture(undefined).headline).toBe('UNKNOWN');
  });

  it('is RECORDED when no adapter is live', () => {
    const posture = deriveEvidencePosture(systemMode());

    expect(posture.headline).toBe('RECORDED');
    expect(posture.notLive).toHaveLength(4);
  });

  it('is MIXED when only some adapters are live, and names the ones that are not', () => {
    const posture = deriveEvidencePosture(systemMode({ weather_mode: 'live' }));

    expect(posture.headline).toBe('MIXED');
    expect(posture.notLive.map((c) => c.label)).toEqual(['LLM', 'FLT', 'NOTIFY']);
    expect(posture.summary).toContain('LLM');
    expect(posture.summary).not.toContain('WX');
  });

  it('is LIVE only when every adapter is live', () => {
    const posture = deriveEvidencePosture(
      systemMode({
        llm_mode: 'live',
        weather_mode: 'live',
        flight_status_mode: 'live',
        notification_mode: 'gmail',
        real_email_enabled: true,
      }),
    );

    expect(posture.headline).toBe('LIVE');
    expect(posture.notLive).toEqual([]);
  });

  it('does not round a nearly-live session up to LIVE', () => {
    const posture = deriveEvidencePosture(
      systemMode({
        llm_mode: 'live',
        weather_mode: 'live',
        flight_status_mode: 'live',
        notification_mode: 'console',
      }),
    );

    expect(posture.headline).toBe('MIXED');
  });
});

describe('the committed fixture satisfies the contract the chips read', () => {
  // The FLT chip shipped reading a field the offline fixture did not carry, which renders as the
  // loading placeholder forever — indistinguishable from a backend that never answered. The fixture
  // stands in for the real response, so it has to carry every field a chip derives from.
  const fixture = JSON.parse(
    readFileSync(new URL('../../../fixtures/api/system_mode.json', import.meta.url), 'utf8'),
  ) as Record<string, unknown>;

  it.each(['llm_mode', 'flight_status_mode', 'weather_mode', 'notification_mode'])(
    'publishes %s',
    (field) => {
      expect(fixture[field]).toBeTypeOf('string');
    },
  );

  it('publishes real_email_enabled, the discriminator the NOTIFY chip depends on', () => {
    expect(fixture.real_email_enabled).toBeTypeOf('boolean');
  });

  it('yields four resolved chips offline, with none left in the unknown posture', () => {
    const chips = deriveModeChips(fixture as unknown as SystemMode);

    expect(chips).toHaveLength(4);
    expect(chips.filter((c) => c.posture === 'unknown')).toEqual([]);
  });

  it('does not advertise a live adapter in the offline demo fixture', () => {
    const chips = deriveModeChips(fixture as unknown as SystemMode);

    expect(chips.filter((c) => c.posture === 'live')).toEqual([]);
  });
});

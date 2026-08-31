import { describe, expect, it } from 'vitest';

import {
  CONNECTIONS_ACTION_TYPE,
  CONNECTIONS_NOT_ASSESSED,
  IMPACT_TAB_LABEL,
  impactTabs,
  resolveActiveTab,
  type ImpactSurfaces,
  type ImpactTab,
} from './availability';

function surfaces(overrides: Partial<ImpactSurfaces> = {}): ImpactSurfaces {
  return {
    hasConnections: false,
    hasCrew: false,
    hasHotels: false,
    hasGroup: false,
    ...overrides,
  };
}

describe('impactTabs — the state the regression was observed in', () => {
  it('offers Connections on a cascade where nothing has been assessed', () => {
    /*
     * The defect. Injected but not advanced: no `check_connections` action exists, so the old rule
     * dropped the connections tab, its tiles and its panel, and with them every mention of the word.
     * `GET /incident-groups/{ref}/impacts` answered 200 throughout, which is what made a missing
     * action look like a rendering fault.
     */
    expect(impactTabs(surfaces())).toContain('connections');
  });

  it('offers Connections in every combination of recorded surfaces', () => {
    for (const hasConnections of [false, true]) {
      for (const hasCrew of [false, true]) {
        for (const hasHotels of [false, true]) {
          for (const hasGroup of [false, true]) {
            const tabs = impactTabs(surfaces({ hasConnections, hasCrew, hasHotels, hasGroup }));
            expect(
              tabs,
              JSON.stringify({ hasConnections, hasCrew, hasHotels, hasGroup }),
            ).toContain('connections');
          }
        }
      }
    }
  });

  it('never returns an empty tab set, so the screen always has a surface to render', () => {
    expect(impactTabs(surfaces()).length).toBeGreaterThan(0);
  });

  it('puts Connections first, because it is what the disruption is about', () => {
    expect(impactTabs(surfaces({ hasCrew: true, hasHotels: true, hasGroup: true }))[0]).toBe(
      'connections',
    );
  });
});

describe('impactTabs — the other surfaces stay gated on a recorded finding', () => {
  it('withholds Passengers until a connection payload exists, because its rows are those rows', () => {
    expect(impactTabs(surfaces())).not.toContain('passengers');
    expect(impactTabs(surfaces({ hasConnections: true }))).toContain('passengers');
  });

  it('withholds crew and hotels until their action has run', () => {
    const none = impactTabs(surfaces());
    expect(none).not.toContain('crew');
    expect(none).not.toContain('hotels');
    expect(impactTabs(surfaces({ hasCrew: true }))).toContain('crew');
    expect(impactTabs(surfaces({ hasHotels: true }))).toContain('hotels');
  });

  it('offers priorities on group membership rather than on an action', () => {
    // The ranking is written at group scope by the orchestrator, and its panel states its own absence.
    expect(impactTabs(surfaces({ hasGroup: true }))).toContain('priorities');
    expect(impactTabs(surfaces({ hasGroup: false }))).not.toContain('priorities');
  });

  it('produces the full set once everything has been recorded', () => {
    expect(
      impactTabs(
        surfaces({ hasConnections: true, hasCrew: true, hasHotels: true, hasGroup: true }),
      ),
    ).toEqual(['connections', 'passengers', 'priorities', 'crew', 'hotels']);
  });

  it('emits no duplicates in any combination', () => {
    for (const hasConnections of [false, true]) {
      for (const hasGroup of [false, true]) {
        const tabs = impactTabs(surfaces({ hasConnections, hasGroup, hasCrew: true }));
        expect(new Set(tabs).size).toBe(tabs.length);
      }
    }
  });
});

describe('resolveActiveTab', () => {
  it('keeps the requested tab while it is still offered', () => {
    const tabs = impactTabs(surfaces({ hasConnections: true, hasCrew: true }));
    expect(resolveActiveTab(tabs, 'crew')).toBe('crew');
  });

  it('falls back to the first tab when the requested one disappears', () => {
    /*
     * The set changes as payloads arrive, so a tab chosen while a request was in flight must not
     * leave the screen rendering nothing.
     */
    expect(resolveActiveTab(impactTabs(surfaces()), 'hotels')).toBe('connections');
    expect(resolveActiveTab(impactTabs(surfaces({ hasGroup: true })), 'crew')).toBe('connections');
  });

  it('falls back to connections even for an empty list, so there is always a rendered surface', () => {
    expect(resolveActiveTab([], 'crew')).toBe('connections');
  });

  it('resolves to something the tab list actually contains', () => {
    for (const requested of Object.keys(IMPACT_TAB_LABEL) as ImpactTab[]) {
      const tabs = impactTabs(surfaces({ hasGroup: true }));
      expect(tabs).toContain(resolveActiveTab(tabs, requested));
    }
  });
});

describe('labels and copy', () => {
  it('labels every tab, so none renders as a blank chip', () => {
    for (const tab of Object.keys(IMPACT_TAB_LABEL) as ImpactTab[]) {
      expect(IMPACT_TAB_LABEL[tab].trim().length).toBeGreaterThan(0);
    }
  });

  it('keeps the literal the browser gate looks for on this route', () => {
    // `verify-console.mjs` asserts 'Connections' reaches the DOM on /impact/:id.
    expect(IMPACT_TAB_LABEL.connections).toBe('Connections');
  });

  it('says nobody looked, never that nothing is at risk', () => {
    /*
     * The distinction the whole fix exists for. An operator who reads "no connection is at risk" on a
     * cascade nobody has assessed has been told the disruption is contained.
     */
    const copy = CONNECTIONS_NOT_ASSESSED.toLowerCase();
    expect(copy).toContain('nobody has looked');
    expect(copy).toContain(CONNECTIONS_ACTION_TYPE);
    // The reassuring reading must not be available: it may appear only under an explicit negation.
    expect(copy).not.toContain('no connection is at risk');
    if (copy.includes('every connection holds')) {
      expect(copy).toContain('not a statement that every connection holds');
    }
  });

  it('uses none of the words the browser gate treats as an unbuilt screen', () => {
    expect(CONNECTIONS_NOT_ASSESSED).not.toMatch(/not yet built|placeholder/i);
  });
});

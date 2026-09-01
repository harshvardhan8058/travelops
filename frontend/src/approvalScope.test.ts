import { describe, expect, it } from 'vitest';

import { approvalScopeFor } from './approvalScope';

describe('approvalScopeFor', () => {
  it('follows the incident a route names', () => {
    expect(approvalScopeFor('/incidents/INC-1', 'INC-1')).toBe('incident');
    expect(approvalScopeFor('/agent/INC-1', 'INC-1')).toBe('incident');
    expect(approvalScopeFor('/replay/INC-1', 'INC-1')).toBe('incident');
  });

  it('reads the current group on network-wide screens', () => {
    expect(approvalScopeFor('/', null)).toBe('group');
    expect(approvalScopeFor('/assurance', null)).toBe('group');
    expect(approvalScopeFor('/cascade/GRP-1', null)).toBe('group');
    expect(approvalScopeFor('/what-if/GRP-1', null)).toBe('group');
    expect(approvalScopeFor('/passenger/K4X8YR', null)).toBe('group');
  });

  it('never falls through to a hardcoded demo incident on a screen that names none', () => {
    // The #121 routing rule: a non-incident route must not inherit another incident's scope.
    expect(approvalScopeFor('/scenarios', null)).not.toBe('incident');
    expect(approvalScopeFor('/scenarios/new', null)).not.toBe('incident');
    expect(approvalScopeFor('/sources', null)).not.toBe('incident');
  });

  it('polls nothing on the screens that are about no disruption at all', () => {
    // Regression: these polled `/incident-groups/current`, which 404s on a freshly seeded stack,
    // putting a console error on the demo's own starting screens and failing the primary-journey
    // verification at its documented starting state.
    expect(approvalScopeFor('/scenarios', null)).toBe('none');
    expect(approvalScopeFor('/scenarios/new', null)).toBe('none');
    expect(approvalScopeFor('/sources', null)).toBe('none');
  });

  it('keeps the demo starting points quiet on a dataset with no started cascade', () => {
    // The Scenario Center is the demo's front door and the Scenario Builder is the documented
    // starting state of the primary-journey verification. On a freshly seeded stack
    // `/incident-groups/current` answers 404 by design, so polling it from either screen opened
    // the demo with a failed request in the console.
    expect(approvalScopeFor('/scenarios', null)).toBe('none');
    expect(approvalScopeFor('/scenarios/new', null)).toBe('none');
  });
});

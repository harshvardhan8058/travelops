import { describe, expect, it } from 'vitest';

import { ApiError } from './client';
import {
  dataUnavailable,
  pollUnlessMissing,
  resolveUnavailable,
  retryUnlessUnavailable,
} from './unavailable';

/**
 * The three 404s measured against the live API on a cascade that was injected but not advanced.
 * Copied verbatim from the responses, so a backend wording change breaks these rather than the UI.
 */
const GROUP_ASSURANCE_404 = new ApiError(
  'ENTITY_NOT_FOUND',
  'no member incident in this group has a plan yet',
  'corr-assurance',
  404,
  {
    group_reference: 'GRP-2026-0820-VOBL',
    resolution: 'run the cascade to planning first',
  },
);

const PLANS_404 = new ApiError(
  'ENTITY_NOT_FOUND',
  'this incident has no plan to vary',
  'corr-plans',
  404,
  {
    incident_reference: 'INC-2026-0820-VOBL-01',
    resolution: 'run the incident to planning first',
  },
);

/** The same code and status for a reference that does not exist. No resolution, by design. */
const INCIDENT_MISSING_404 = new ApiError(
  'ENTITY_NOT_FOUND',
  'incident not found',
  'corr-miss',
  404,
  {
    incident: 'INC-NOPE',
  },
);

const GROUP_MISSING_404 = new ApiError(
  'ENTITY_NOT_FOUND',
  'disruption group not found',
  'corr-miss-group',
  404,
  { group: 'GRP-NOPE' },
);

describe('dataUnavailable — unavailable assurance data', () => {
  it('recognises a group with no member plan as a state, not a failure', () => {
    const unavailable = dataUnavailable(GROUP_ASSURANCE_404);
    expect(unavailable).not.toBeNull();
    expect(unavailable?.resolution).toBe('run the cascade to planning first');
  });

  it('keeps the server’s own code and message so the 404 is reported, not hidden', () => {
    /*
     * The point of the fix is that the console stops calling this a failure — not that it stops
     * mentioning it. Both are rendered beside the empty state.
     */
    const unavailable = dataUnavailable(GROUP_ASSURANCE_404);
    expect(unavailable?.code).toBe('ENTITY_NOT_FOUND');
    expect(unavailable?.message).toBe('no member incident in this group has a plan yet');
    expect(unavailable?.correlationId).toBe('corr-assurance');
  });
});

describe('dataUnavailable — incident with no plan', () => {
  it('recognises the plans and comparison 404, which share one cause', () => {
    // Both endpoints call `propose_candidates`, so both raise the same error.
    const unavailable = dataUnavailable(PLANS_404);
    expect(unavailable?.resolution).toBe('run the incident to planning first');
    expect(unavailable?.message).toBe('this incident has no plan to vary');
  });
});

describe('dataUnavailable — a missing entity stays an error', () => {
  it('does not treat a reference that does not exist as an empty state', () => {
    /*
     * The discriminator is `details.resolution`, not the status code. A mistyped reference has no
     * next step that would make it resolve, so the backend publishes none — and branching on 404
     * alone would swallow a real miss behind "nothing here yet".
     */
    expect(dataUnavailable(INCIDENT_MISSING_404)).toBeNull();
    expect(dataUnavailable(GROUP_MISSING_404)).toBeNull();
  });

  it('rejects a blank or non-string resolution rather than rendering an empty description', () => {
    for (const resolution of ['', '   ', 42, null, undefined, { step: 'run it' }]) {
      const error = new ApiError('ENTITY_NOT_FOUND', 'gone', null, 404, { resolution });
      expect(dataUnavailable(error), JSON.stringify(resolution)).toBeNull();
    }
  });

  it('rejects any other status or code, however similar', () => {
    expect(
      dataUnavailable(
        new ApiError('ENTITY_NOT_FOUND', 'gone', null, 409, { resolution: 'run it' }),
      ),
    ).toBeNull();
    expect(
      dataUnavailable(new ApiError('INTERNAL_ERROR', 'boom', null, 404, { resolution: 'run it' })),
    ).toBeNull();
    expect(
      dataUnavailable(new ApiError('INTERNAL_ERROR', 'boom', null, 500, { resolution: 'run it' })),
    ).toBeNull();
  });

  it('rejects anything that is not an ApiError, including a bare 404-shaped object', () => {
    for (const value of [
      null,
      undefined,
      new Error('network down'),
      { status: 404, code: 'ENTITY_NOT_FOUND', details: { resolution: 'run it' } },
      'ENTITY_NOT_FOUND',
    ]) {
      expect(dataUnavailable(value)).toBeNull();
    }
  });

  it('trims the resolution so a padded string cannot render as leading whitespace', () => {
    const error = new ApiError('ENTITY_NOT_FOUND', 'gone', null, 404, {
      resolution: '  run the cascade to planning first  ',
    });
    expect(dataUnavailable(error)?.resolution).toBe('run the cascade to planning first');
  });
});

describe('resolveUnavailable — a screen reading several endpoints', () => {
  it('reports nothing when no query failed', () => {
    expect(resolveUnavailable([])).toBeNull();
    expect(resolveUnavailable([null, undefined])).toBeNull();
  });

  it('reports the not-yet state when every failure is one', () => {
    const outcome = resolveUnavailable([PLANS_404, PLANS_404]);
    expect(outcome).not.toBeNull();
    expect(outcome && 'unavailable' in outcome).toBe(true);
  });

  it('lets a genuine failure win over a not-yet state', () => {
    /*
     * Plan Comparison reads two endpoints. Reporting "no candidates yet" while one of them is
     * actually broken would hide an outage behind an empty state.
     */
    const broken = new ApiError('INTERNAL_ERROR', 'boom', null, 500);
    for (const errors of [
      [PLANS_404, broken],
      [broken, PLANS_404],
    ]) {
      const outcome = resolveUnavailable(errors);
      expect(outcome && 'failure' in outcome).toBe(true);
      if (outcome && 'failure' in outcome) expect(outcome.failure).toBe(broken);
    }
  });

  it('treats a missing entity as the failure it is, even beside a not-yet state', () => {
    const outcome = resolveUnavailable([PLANS_404, INCIDENT_MISSING_404]);
    expect(outcome && 'failure' in outcome).toBe(true);
  });

  it('ignores falsy entries so an unfired query cannot look like a failure', () => {
    const outcome = resolveUnavailable([null, PLANS_404, undefined]);
    expect(outcome && 'unavailable' in outcome).toBe(true);
  });
});

describe('retryUnlessUnavailable', () => {
  it('never retries a documented not-yet state', () => {
    // Retrying is three more requests whose answer is already known, and it delays the empty state.
    expect(retryUnlessUnavailable(0, GROUP_ASSURANCE_404)).toBe(false);
    expect(retryUnlessUnavailable(0, PLANS_404)).toBe(false);
  });

  it('still retries a transient failure, up to the app default bound', () => {
    // One retry, matching `main.tsx`'s `retry: 1`. react-query counts from zero, so this is two
    // requests. It used to allow three, which made this helper noisier than no helper at all.
    const broken = new ApiError('INTERNAL_ERROR', 'boom', null, 500);
    expect(retryUnlessUnavailable(0, broken)).toBe(true);
    expect(retryUnlessUnavailable(1, broken)).toBe(false);
  });

  it('does not retry an unclassified 404 either', () => {
    /*
     * The docstring always claimed no 404 was retried; the rule underneath it only skipped the
     * retry for a 404 carrying a `resolution`. So `"incident not found"` — raised for the fixed
     * reference the nav links to, which a dataset restore deletes — fell through to the transient
     * branch and cost three requests instead of one. A missing resource is still missing on the
     * second ask; the retry only doubles what the browser console reports.
     */
    expect(retryUnlessUnavailable(0, INCIDENT_MISSING_404)).toBe(false);
  });

  it('retries a network failure that is not an ApiError at all', () => {
    // No status to read: this is the transient class the bound exists for.
    expect(retryUnlessUnavailable(0, new TypeError('Failed to fetch'))).toBe(true);
  });
});

describe('pollUnlessMissing', () => {
  const poll = pollUnlessMissing(10_000);
  const q = (error: unknown) => ({ state: { error } });

  it('keeps polling while nothing has failed', () => {
    expect(poll(q(null))).toBe(10_000);
  });

  it('stops once the resource is missing', () => {
    /*
     * react-query re-arms a polling interval on error as readily as on success, so the shell's
     * assurance read reissued the same 404 every ten seconds — and the timeline every five — for
     * as long as the tab stayed open, against the fixed incident reference the nav links to. None
     * of those requests could change the answer: the resource appears when an operator starts
     * something, and that navigates.
     */
    expect(poll(q(INCIDENT_MISSING_404))).toBe(false);
    expect(poll(q(PLANS_404))).toBe(false);
  });

  it('keeps polling through a transient failure, which is what polling is for', () => {
    expect(poll(q(new ApiError('INTERNAL_ERROR', 'boom', null, 500)))).toBe(10_000);
    expect(poll(q(new TypeError('Failed to fetch')))).toBe(10_000);
  });
});

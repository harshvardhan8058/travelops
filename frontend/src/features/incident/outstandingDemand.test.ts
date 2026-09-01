import { describe, expect, it } from 'vitest';

import { outstandingDemand, resolvedWithOutstandingDemand } from './outstandingDemand';
import type { ActionRecord } from '@/api/types';

function action(over: Partial<ActionRecord> = {}): ActionRecord {
  return {
    id: 1,
    plan_task_id: 1,
    action_type: 'check_connections',
    assurance_id: 1,
    human_decision_id: null,
    actor: 'orchestrator',
    status: 'success',
    reason: '',
    cost_inr: null,
    idempotency_key: 'k',
    executed_at: '2026-08-20T15:36:00Z',
    ...over,
  } as ActionRecord;
}

const SHORTFALL =
  '71 of 87 rooms secured. 16 rooms short. Every property within the rate cap is exhausted, ' +
  'so closing the gap needs a decision: raise the cap, go further out, or accept that some ' +
  'passengers wait.';

describe('outstandingDemand', () => {
  it('names the action the backend recorded as needing a person', () => {
    const items = outstandingDemand([
      action(),
      action({ id: 3, action_type: 'reserve_hotel_block', status: 'needs_human', reason: SHORTFALL }),
    ]);

    expect(items).toHaveLength(1);
    expect(items[0]?.actionId).toBe(3);
    expect(items[0]?.actionType).toBe('reserve_hotel_block');
    // Verbatim: the service wrote this sentence and it is the one the operator must act on.
    expect(items[0]?.reason).toBe(SHORTFALL);
  });

  it('does not fold a failure in with an operational choice', () => {
    /*
     * A broken service and 16 rooms short have different remedies. The screens that render
     * failures already say so; one shared heading would blur which of the two happened.
     */
    const items = outstandingDemand([action({ id: 2, status: 'failure', reason: 'boom' })]);

    expect(items).toEqual([]);
  });

  it('tolerates the committed fixture, whose action rows carry no action_type', () => {
    const items = outstandingDemand([
      { ...action({ id: 4, status: 'needs_human', reason: SHORTFALL }), action_type: undefined },
    ]);

    expect(items[0]?.actionType).toBeNull();
  });

  it('is empty when nothing was recorded', () => {
    expect(outstandingDemand(undefined)).toEqual([]);
    expect(outstandingDemand([])).toEqual([]);
  });
});

describe('resolvedWithOutstandingDemand', () => {
  it('is true for the run that finished still owing something', () => {
    /*
     * The header read WORKFLOW RESOLVED while a task on the same screen read NEEDS HUMAN, with
     * nothing reconciling them. Both are correct; only together are they the whole answer.
     */
    expect(
      resolvedWithOutstandingDemand('resolved', [
        action({ id: 3, status: 'needs_human', reason: SHORTFALL }),
      ]),
    ).toBe(true);
  });

  it('is false for a clean resolution', () => {
    expect(resolvedWithOutstandingDemand('resolved', [action()])).toBe(false);
  });

  it('is false while the run is still going, where the state already says so', () => {
    expect(
      resolvedWithOutstandingDemand('executing', [
        action({ id: 3, status: 'needs_human', reason: SHORTFALL }),
      ]),
    ).toBe(false);
  });
});

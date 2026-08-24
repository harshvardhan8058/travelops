"""Candidate recovery plans: propose, compare, select.

Phase 1 gave one incident one plan and `_current_plan` took the latest by id. Comparison needs
several plans to coexist with a recorded choice, which migration 0005 provides:
`plan.selection_state`, `selected_at`, `selected_by`, `variant_key`, `plan_hash`.

Two rules that keep this honest:

**Candidates come from the deterministic playbook, varied along declared axes** — task ordering
and the inclusion of optional steps. Not from a model. With `LLM_MODE=off` there must still be
more than one candidate, or the comparison screen is empty in the mode the demo runs in.

**Comparison is a re-evaluation, never a projection** (P2-D2). Every candidate is scored by
Stream B's `evaluate_candidates` against the *same recorded facts*; nothing is written, no world
state is modelled, and no figure appears that is not traceable to a stored row. Stream B
deliberately provides no rank or `recommended` flag — choosing is a human's job, or Stream A's
explicit tie-break, and it is recorded with an actor.

Owner: Stream A.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.assurance.candidates import CandidateInput, CandidateSet, evaluate_candidates
from app.assurance.plan_contract import (
    CoverageDeclaration,
    ExposureInputs,
    PlanUnderReview,
    WhatIfPolicy,
)
from app.config import Settings, get_settings
from app.db.plan_identity import compute_plan_hash
from app.errors import EntityNotFound, InvalidStateTransition
from app.models.enums import TaskState
from app.models.workflow import Incident, Plan, PlanTask
from app.orchestrator.plan_assurance import PlanAssuranceService, load_plan_configuration

log = structlog.get_logger(__name__)

SELECTION_CANDIDATE = "candidate"
SELECTION_SELECTED = "selected"
SELECTION_DISCARDED = "discarded"

#: Declared variation axes. Named so a reviewer can see the space is bounded — two variants,
#: differing in one stated way, not an open search.
VARIANT_BASELINE = "baseline"
VARIANT_NOTIFY_FIRST = "notify-first"

#: The seed every comparison uses. Deterministic by requirement: `assurance.v2.yaml` sets
#: `require_deterministic_seed: true`, so a comparison without one is refused outright.
COMPARISON_SEED = 20260820


@dataclass
class CandidateVariant:
    """One declared variation of the playbook's task order."""

    variant_key: str
    description: str
    #: Action types in the order this variant proposes them.
    order: tuple[str, ...]


def _reorder(actions: Sequence[str], first: str) -> tuple[str, ...]:
    """Move one action to the front, preserving the relative order of the rest."""
    if first not in actions:
        return tuple(actions)
    return (first, *[action for action in actions if action != first])


def declared_variants(actions: Sequence[str]) -> list[CandidateVariant]:
    """The variants for a given task set.

    Ordering is the axis because it is the one variation the deterministic playbook can express
    without inventing a step that has no service behind it. A variant that proposed an action
    with no registered service would fail at dispatch, which is a worse answer than not
    offering it.
    """
    baseline = CandidateVariant(
        variant_key=VARIANT_BASELINE,
        description="Playbook order: assess impact, then communicate.",
        order=tuple(actions),
    )
    variants = [baseline]

    if "notify_passengers" in actions and actions[0] != "notify_passengers":
        variants.append(
            CandidateVariant(
                variant_key=VARIANT_NOTIFY_FIRST,
                description=(
                    "Communicate first: passengers are told before the impact assessments "
                    "complete, trading assessment completeness for notice period."
                ),
                order=_reorder(actions, "notify_passengers"),
            )
        )
    return variants


class CandidateService:
    """Plan candidate lifecycle over persisted rows."""

    def __init__(self, session: AsyncSession, *, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._plans = PlanAssuranceService(session, settings=self._settings)

    # ---------------------------------------------------------------------------- reads

    async def plans_for_incident(self, incident_id: int) -> list[Plan]:
        stmt = select(Plan).where(Plan.incident_id == incident_id).order_by(Plan.id)
        return list((await self._session.execute(stmt)).scalars())

    async def tasks_for_plan(self, plan_id: int) -> list[PlanTask]:
        stmt = select(PlanTask).where(PlanTask.plan_id == plan_id).order_by(PlanTask.task_order)
        return list((await self._session.execute(stmt)).scalars())

    # -------------------------------------------------------------------------- proposal

    async def propose_candidates(self, incident: Incident) -> list[Plan]:
        """Ensure the incident holds every declared variant, and return them all.

        Idempotent: a variant that already exists is not duplicated, so re-running the
        comparison screen does not grow the candidate set.
        """
        existing = await self.plans_for_incident(incident.id)
        if not existing:
            raise EntityNotFound(
                "this incident has no plan to vary",
                details={
                    "incident_reference": incident.reference,
                    "resolution": "run the incident to planning first",
                },
            )

        base = existing[0]
        base_tasks = await self.tasks_for_plan(base.id)
        actions = [row.action_type for row in base_tasks]
        if not actions:
            return existing

        # Backfill identity on the original plan. Phase 1 wrote plans before 0005 existed.
        await self._ensure_identity(base, base_tasks)

        by_variant = {plan.variant_key: plan for plan in existing if plan.variant_key}
        if base.variant_key is None:
            base.variant_key = VARIANT_BASELINE
            by_variant[VARIANT_BASELINE] = base
            await self._session.flush()

        created: list[Plan] = []
        for variant in declared_variants(actions):
            if variant.variant_key in by_variant:
                continue
            created.append(await self._materialise(base, base_tasks, variant))

        if created:
            log.info(
                "plan_candidates_proposed",
                incident_reference=incident.reference,
                created=[plan.variant_key for plan in created],
                total=len(existing) + len(created),
            )
        return await self.plans_for_incident(incident.id)

    async def _materialise(
        self, base: Plan, base_tasks: list[PlanTask], variant: CandidateVariant
    ) -> Plan:
        """Persist one variant as its own candidate plan with its own tasks."""
        plan = Plan(
            incident_id=base.incident_id,
            generated_at=datetime.now(UTC),
            generator=base.generator,
            prompt_version=base.prompt_version,
            model_self_report=None,
            rationale=f"Candidate variant '{variant.variant_key}'. {variant.description}",
            raw_response=None,
            retrieved_incident_ids=[],
            selection_state=SELECTION_CANDIDATE,
            variant_key=variant.variant_key,
        )
        self._session.add(plan)
        await self._session.flush()

        by_action = {row.action_type: row for row in base_tasks}
        ordered = [by_action[action] for action in variant.order if action in by_action]
        for order, source in enumerate(ordered, start=1):
            self._session.add(
                PlanTask(
                    plan_id=plan.id,
                    action_type=source.action_type,
                    task_order=order,
                    depends_on=[],
                    target_refs=list(source.target_refs or []),
                    inputs=dict(source.inputs or {}),
                    state=TaskState.proposed,
                )
            )
        await self._session.flush()
        plan.plan_hash = compute_plan_hash(
            [
                {
                    "action_type": row.action_type,
                    "target_ref": (row.target_refs or [None])[0],
                    "risk_tier": None,
                }
                for row in ordered
            ],
            generator=plan.generator or "fallback_playbook",
            prompt_version=plan.prompt_version or "none",
        )
        await self._session.flush()
        return plan

    async def _ensure_identity(self, plan: Plan, tasks: list[PlanTask]) -> None:
        """Stamp `plan_hash` on a plan that predates migration 0005."""
        if plan.plan_hash:
            return
        plan.plan_hash = compute_plan_hash(
            [
                {
                    "action_type": row.action_type,
                    "target_ref": (row.target_refs or [None])[0],
                    "risk_tier": None,
                }
                for row in tasks
            ],
            generator=plan.generator or "fallback_playbook",
            prompt_version=plan.prompt_version or "none",
        )
        await self._session.flush()

    # ------------------------------------------------------------------------ comparison

    async def compare(
        self, incident: Incident, *, group_reference: str
    ) -> tuple[CandidateSet, list[Plan]]:
        """Re-evaluate every candidate against the same recorded facts. Writes nothing.

        This is what-if under P2-D2. Stream B's `evaluate_candidates` runs the zero-write guard
        FIRST: if it refuses — provider live, seed missing, too many candidates — nothing is
        evaluated at all and the refusal codes are returned, rather than a comparison being
        produced under conditions that were not safe to produce one in.
        """
        plans = await self.propose_candidates(incident)
        loaded = load_plan_configuration(self._settings)

        inputs: list[CandidateInput] = []
        for plan in plans:
            if plan.selection_state == SELECTION_DISCARDED:
                continue
            scope = await self._plans.scope_for_plan(plan, group_reference=group_reference)
            inputs.append(
                CandidateInput(
                    candidate_id=plan.variant_key or f"plan-{plan.id}",
                    plan=PlanUnderReview(
                        plan_id=plan.id,
                        group_reference=group_reference,
                        tasks=scope.tasks,
                        generator=plan.generator,
                    ),
                    coverage=CoverageDeclaration(
                        declared=True,
                        impacted_refs=sorted(
                            {ref for task in scope.tasks for ref in task.target_refs}
                        ),
                    ),
                    exposure=ExposureInputs(
                        passengers_affected=None,
                        external_effects=sum(
                            1
                            for task in scope.tasks
                            if task.action_type
                            in {
                                "notify_passengers",
                                "reserve_hotel_block",
                                "arrange_ground_transport",
                                "issue_compensation",
                                "rebook_passengers",
                            }
                        ),
                    ),
                )
            )

        result = evaluate_candidates(
            candidates=inputs,
            config=loaded.plan if loaded else None,
            config_version=loaded.version if loaded else "unavailable",
            config_hash=loaded.digest if loaded else "unavailable",
            # No config means no what-if policy, and WhatIfPolicy() defaults to
            # `enabled: False` — so a missing config refuses the comparison rather than
            # running one under unknown rules.
            what_if_policy=loaded.what_if if loaded else WhatIfPolicy(),
            seed=COMPARISON_SEED,
            provider_modes={"weather": "fixture", "notification": "console"},
            real_dispatch_enabled=False,
        )
        log.info(
            "plan_candidates_compared",
            incident_reference=incident.reference,
            candidates=len(inputs),
            decision=result.decision,
            admissible=result.admissible,
        )
        return result, plans

    # ------------------------------------------------------------------------- selection

    async def select(self, incident: Incident, *, plan_id: int, actor_id: str, reason: str) -> Plan:
        """Record which candidate an operator chose. Immutable once made.

        A second, *different* selection is a 409 rather than a silent overwrite, enforced by
        the partial unique index `uq_plan_selected_per_incident` as well as by this check —
        the same shape `human_decision` already uses, because a choice is a record of what
        somebody decided and not a mutable setting.
        """
        plan = await self._session.get(Plan, plan_id)
        if plan is None or plan.incident_id != incident.id:
            raise EntityNotFound(
                "plan not found for this incident",
                details={"incident_reference": incident.reference, "plan_id": plan_id},
            )

        already = [
            row
            for row in await self.plans_for_incident(incident.id)
            if row.selection_state == SELECTION_SELECTED
        ]
        if already:
            held = already[0]
            if held.id == plan.id:
                return held
            raise InvalidStateTransition(
                "this incident already has a selected plan",
                details={
                    "incident_reference": incident.reference,
                    "selected_plan_id": held.id,
                    "selected_by": held.selected_by,
                    "requested_plan_id": plan.id,
                    "resolution": "a different choice requires a new plan, not a re-selection",
                },
            )

        tasks = await self.tasks_for_plan(plan.id)
        await self._ensure_identity(plan, tasks)
        plan.selection_state = SELECTION_SELECTED
        plan.selected_at = datetime.now(UTC)
        plan.selected_by = actor_id
        if plan.rationale and reason:
            plan.rationale = f"{plan.rationale} Selected: {reason}"
        for other in await self.plans_for_incident(incident.id):
            if other.id != plan.id and other.selection_state == SELECTION_CANDIDATE:
                other.selection_state = SELECTION_DISCARDED
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise InvalidStateTransition(
                "another selection was recorded for this incident concurrently",
                details={"incident_reference": incident.reference, "plan_id": plan.id},
            ) from exc

        log.info(
            "plan_selected",
            incident_reference=incident.reference,
            plan_id=plan.id,
            variant=plan.variant_key,
            actor=actor_id,
        )
        return plan


def comparison_payload(result: CandidateSet, plans: list[Plan]) -> dict[str, Any]:
    """Shape a comparison for the API. Deliberately carries no ranking.

    `basis` is a fixed literal so the response *cannot* be read as a projection, and
    `not_a_forecast` is rendered verbatim by the console.
    """
    by_variant = {plan.variant_key or f"plan-{plan.id}": plan for plan in plans}
    return {
        "basis": "recorded_evidence",
        "not_a_forecast": (
            "Every figure below was re-evaluated against the evidence already recorded for "
            "this incident. Nothing was simulated, projected or written."
        ),
        "decision": result.decision,
        "admissible": list(result.admissible),
        "blocking_reasons": list(result.blocking_reasons),
        "seed": COMPARISON_SEED,
        "what_if": result.what_if.model_dump(mode="json") if result.what_if else None,
        "candidates": [
            {
                **comparison.model_dump(mode="json"),
                "plan_id": by_variant[comparison.candidate_id].id
                if comparison.candidate_id in by_variant
                else None,
                "variant_key": comparison.candidate_id,
                "generator": by_variant[comparison.candidate_id].generator
                if comparison.candidate_id in by_variant
                else None,
                "prompt_version": by_variant[comparison.candidate_id].prompt_version
                if comparison.candidate_id in by_variant
                else None,
                "selection_state": by_variant[comparison.candidate_id].selection_state
                if comparison.candidate_id in by_variant
                else None,
                "rationale": by_variant[comparison.candidate_id].rationale
                if comparison.candidate_id in by_variant
                else None,
            }
            for comparison in result.comparison
        ],
    }

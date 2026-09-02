"""Phase 3 reasoning-agent endpoints — Explanation and Report.

Both are read-only artifacts generated on demand from recorded evidence. Neither enters assurance,
triggers an action, or modifies any row. They are the model's contribution to the audit trail and
the executive display, not to the recovery itself.

When `LLM_MODE=off` these return 404 with a message naming the mode, not an error. A missing
artifact is a configuration fact, not a system failure.

Owner: Stream C.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import LLMMode, get_settings, provider_transport
from app.db.session import get_session
from app.errors import EntityNotFound, ProviderUnavailable
from app.models.enums import ActionStatus, IncidentState
from app.models.workflow import Action, Incident, IncidentGroup, Plan, PlanTask
from app.observability.logging import get_logger
from app.orchestrator.group_state import derive_group_state

router = APIRouter(tags=["reasoning"])
log = get_logger(__name__)


async def _resolve_incident(session: AsyncSession, reference: str) -> Incident:
    incident: Incident | None = None
    if reference.isdigit():
        incident = await session.get(Incident, int(reference))
    if incident is None:
        stmt = select(Incident).where(Incident.reference == reference)
        incident = (await session.execute(stmt)).scalars().first()
    if incident is None:
        raise EntityNotFound("incident not found", details={"incident": reference})
    return incident


async def _resolve_group(session: AsyncSession, reference: str) -> IncidentGroup:
    group: IncidentGroup | None = None
    if reference.isdigit():
        group = await session.get(IncidentGroup, int(reference))
    if group is None:
        stmt = select(IncidentGroup).where(IncidentGroup.reference == reference)
        group = (await session.execute(stmt)).scalars().first()
    if group is None:
        raise EntityNotFound("disruption group not found", details={"group": reference})
    return group


async def _actions_summary(session: AsyncSession, incident_id: int) -> list[dict[str, Any]]:
    """Summary of completed actions for the explainer's context."""
    stmt = (
        select(Action, PlanTask.action_type)
        .join(PlanTask, PlanTask.id == Action.plan_task_id)
        .join(Plan, Plan.id == PlanTask.plan_id)
        .where(
            Plan.incident_id == incident_id,
            Action.status.in_([ActionStatus.success.value, ActionStatus.needs_human.value]),
        )
        .order_by(Action.id)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "action_type": action_type,
            "status": action.status,
            "reason": action.reason or "",
        }
        for action, action_type in rows
    ]


def _group_figures(rollup: Any) -> dict[str, Any]:
    """The group-scoped figures the reporter prompt consumes."""
    return {
        "flights_affected": rollup.flights_affected,
        "passengers_affected": rollup.passengers_affected,
        "connections_at_risk": rollup.connections_at_risk,
        "crew_pairings_affected": rollup.crew_pairings_affected,
        "candidate_hotels": rollup.candidate_hotels,
    }


async def _incident_figures(session: AsyncSession, incident: Incident) -> dict[str, Any]:
    """The same figures at INCIDENT scope — one flight, counted the way the incident detail counts.

    Reuses `app.api.incidents._passenger_count` rather than writing a second passenger query, so an
    incident-scoped report and the incident screen can never disagree about how many people are on
    the flight. Connections and crew are read from this incident's own recorded actions; where no
    such action has run the figure is `None`, not zero — "no assessment" and "nothing found" are
    different, and a report is exactly the artefact that must not blur them.
    """
    from app.api.incidents import _passenger_count
    from app.db.scenario_queries import recorded_actions
    from app.models.enums import ActionType

    passengers = await _passenger_count(session, incident.flight_id)

    async def _finding(action_type: str, key: str) -> int | None:
        """A recorded count from this incident's own action payload, or None if it never ran."""
        rows = await recorded_actions(
            session, [incident.id], action_type, statuses=("success", "needs_human")
        )
        if not rows:
            return None
        return sum(int((payload or {}).get(key) or 0) for _i, _a, payload in rows)

    async def _hotel_options() -> int | None:
        rows = await recorded_actions(
            session,
            [incident.id],
            ActionType.find_hotel_options.value,
            statuses=("success", "needs_human"),
        )
        if not rows:
            return None
        return sum(len((payload or {}).get("options") or []) for _i, _a, payload in rows)

    return {
        # One incident is one flight, by construction.
        "flights_affected": 1,
        "passengers_affected": passengers,
        "connections_at_risk": await _finding(ActionType.check_connections.value, "at_risk_count"),
        "crew_pairings_affected": await _finding(
            ActionType.assess_crew_impact.value, "pairings_at_risk"
        ),
        "candidate_hotels": await _hotel_options(),
    }


async def _incident_hotel_totals(
    session: AsyncSession, incident: Incident
) -> dict[str, Any] | None:
    """This incident's own accommodation figures, in the shape `group_hotel_totals` returns.

    Same shape so the reporter prompt takes one summary type whatever the scope, and `None` — never
    a dictionary of zeros — when no allocation has run, so an unknown requirement cannot read as no
    requirement.
    """
    from app.db.scenario_queries import recorded_actions
    from app.models.enums import ActionType

    rows = await recorded_actions(
        session,
        [incident.id],
        ActionType.reserve_hotel_block.value,
        statuses=("success", "needs_human"),
    )
    if not rows:
        return None
    required = sum(int((payload or {}).get("rooms_required") or 0) for _i, _a, payload in rows)
    allocated = sum(int((payload or {}).get("rooms_allocated") or 0) for _i, _a, payload in rows)
    cost = sum(int((payload or {}).get("total_cost_inr") or 0) for _i, _a, payload in rows)
    short = max(0, required - allocated)
    return {
        "rooms_required": required,
        "rooms_allocated": allocated,
        "shortfall_rooms": short,
        "total_cost_inr": cost,
        "is_complete": short == 0,
        "coverage_is_complete": True,
        "incidents_allocated": 1,
        "incidents_declared": 1,
        "shortfall_note": (
            f"All {required} rooms secured for {incident.reference}."
            if short == 0
            else (
                f"{allocated} of {required} rooms secured for {incident.reference}. {short} rooms "
                "short. This figure covers this incident's flight alone, not the whole disruption."
            )
        ),
    }


#: Plain-language gloss for each non-resolved incident state, used only in the report's
#: server-composed correction below. Never shown as an LLM claim.
_STATE_GLOSS: dict[IncidentState, str] = {
    IncidentState.detected: "just detected; recovery has not started",
    IncidentState.assessing: "being assessed",
    IncidentState.planning: "being planned",
    IncidentState.assuring: "at the assurance gate",
    IncidentState.awaiting_approval: "awaiting an operator's decision",
    IncidentState.executing: "still executing",
    IncidentState.blocked: "blocked — no further automated progress is possible without a "
    "person's decision",
    IncidentState.failed: "failed",
}


async def _operational_state(
    session: AsyncSession, *, group: IncidentGroup | None, incident: Incident | None
) -> tuple[IncidentState, list[int]]:
    """The state actually on screen right now, plus the incident id(s) it covers.

    Never read a persisted `incident_group.state` column here. That value is written only when
    `GroupOrchestrator` runs a step (`_sync_state`), so it can lag behind a member incident that
    reached `blocked` on its own between group-level runs — exactly the gap that let a report
    narrate a resolved outcome for a group whose member had stalled. Re-deriving from each
    member's own current row, the same way `_sync_state` does, means this can never be stale.
    """
    if incident is not None:
        return IncidentState(incident.state), [incident.id]
    assert group is not None
    stmt = select(Incident.id, Incident.state).where(Incident.group_id == group.id)
    rows = (await session.execute(stmt)).all()
    incident_ids = [row[0] for row in rows]
    return derive_group_state([row[1] for row in rows]), incident_ids


async def _unresolved_evidence(session: AsyncSession, incident_ids: list[int]) -> str | None:
    """What a person still needs to decide, in the service's own recorded words.

    Reuses the `reason` already written on a `needs_human` action — `reserve_hotel_block` writes
    its `shortfall_note` there — rather than composing new prose, so the correction below can
    never assert something the evidence does not already say.
    """
    if not incident_ids:
        return None
    stmt = (
        select(PlanTask.action_type, Action.reason)
        .join(Plan, Plan.id == PlanTask.plan_id)
        .join(Action, Action.plan_task_id == PlanTask.id)
        .where(
            Plan.incident_id.in_(incident_ids),
            Action.status == ActionStatus.needs_human.value,
        )
        .order_by(Action.id)
    )
    rows = (await session.execute(stmt)).all()
    lines: list[str] = []
    for action_type, reason in rows:
        line = f"{action_type}: {reason}" if reason else str(action_type)
        if line not in lines:
            lines.append(line)
    return " / ".join(lines) if lines else None


def _source_of(generator: str) -> str:
    """`fixture` or `live`, read off the generator the client already recorded.

    Formatting, not derivation: `LLMClient` writes `fixture:<agent>` for a replay and
    `groq:<model>` for a network call, so this only saves every client from parsing a prefix. A
    consumer needs it because a fixture artefact and a live one carry different weight in a review,
    and `llm_mode` alone does not distinguish them — live mode falls back to no output rather than
    to a fixture, but a reader cannot know that from the mode.
    """
    return "fixture" if generator.startswith("fixture:") else "live"


def _unavailable(exc: Exception, *, artifact: str, mode: str) -> ProviderUnavailable:
    """Turn a model-provider failure into 503 rather than letting it become a 500.

    `LLMUnavailable` is a plain `Exception`, not a `TravelOpsError`, and `app.main` only installs
    handlers for `TravelOpsError` and `RequestValidationError`. So before this, every live
    failure — no API key, a rate limit, a schema mismatch — escaped uncaught and Starlette
    turned it into a bare 500 with no error code and no mode information.

    Deliberately NOT a fixture fallback. In `live` mode the honest answer to "the model could not
    be reached" is to say so; replaying a committed fixture would put recorded prose behind a
    `source: live` label and quietly make the artifact untraceable.
    """
    return ProviderUnavailable(
        f"{artifact} unavailable: the reasoning model could not be reached",
        details={
            "llm_mode": mode,
            "provider_error": str(exc)[:300],
            "resolution": (
                "Check the provider API key and provider status. "
                "Set LLM_MODE=fixture to serve the committed artefact instead."
            ),
        },
    )


@router.get(
    "/incidents/{incident_id}/explanation",
    summary="Natural-language explanation of the recovery (Phase 3 reasoning agent)",
)
async def get_explanation(
    incident_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """On-demand explanation from the Explainer agent.

    Generated from recorded evidence each time — not cached, because the value is in the
    explanation reflecting the current state of the evidence, and caching introduces a
    staleness question the system has no mechanism to answer.

    Returns 404 with mode information when `LLM_MODE=off`.
    """
    settings = get_settings()
    if settings.llm_mode == LLMMode.off:
        raise EntityNotFound(
            "explanation not available: LLM_MODE=off",
            details={
                "llm_mode": "off",
                "resolution": "Set LLM_MODE=fixture or LLM_MODE=live to enable reasoning agents.",
            },
        )

    incident = await _resolve_incident(session, incident_id)
    actions = await _actions_summary(session, incident.id)

    if not actions:
        raise EntityNotFound(
            "no completed actions to explain",
            details={
                "incident_reference": incident.reference,
                "resolution": "Run the incident to completion first.",
            },
        )

    from app.agents.explainer import ExplainerAgent
    from app.llm.client import LLMUnavailable

    agent = ExplainerAgent()
    try:
        response, audit = await agent.explain(
            incident_reference=incident.reference,
            actions_summary=actions,
        )
    except LLMUnavailable as exc:
        raise _unavailable(exc, artifact="explanation", mode=settings.llm_mode.value) from exc

    return {
        "incident_reference": incident.reference,
        "generator": audit.generator,
        "prompt_version": audit.prompt_version,
        "source": _source_of(audit.generator),
        "llm_mode": settings.llm_mode.value,
        **response.model_dump(mode="json"),
        "audit": audit.model_dump(mode="json"),
        # On the contract, not in a comment. An explanation of a recovery that already happened
        # cannot authorise, reverse or modify any part of it.
        "authorises_no_action": True,
    }


@router.get(
    "/reports/{report_id}",
    summary="Executive report for an incident or group, at its current state (Phase 3 reasoning agent)",
)
async def get_report(
    report_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """On-demand executive report from the Report Generator agent.

    `report_id` is an incident reference or a group reference. Callable at any state, not only a
    resolved one — an operator can ask for this while the recovery is still open. The narrative is
    never allowed to claim more than `operational_state` supports: see the override below `agent.
    generate` for what happens when the reference has not resolved.

    Returns 404 with mode information when `LLM_MODE=off`.
    """
    settings = get_settings()
    if settings.llm_mode == LLMMode.off:
        raise EntityNotFound(
            "report not available: LLM_MODE=off",
            details={
                "llm_mode": "off",
                "resolution": "Set LLM_MODE=fixture or LLM_MODE=live to enable reasoning agents.",
            },
        )

    # Try as a group first (the primary Phase 3 use case), then as an incident
    group: IncidentGroup | None = None
    incident: Incident | None = None

    stmt = select(IncidentGroup).where(IncidentGroup.reference == report_id)
    group = (await session.execute(stmt)).scalars().first()

    if group is None:
        incident = await _resolve_incident(session, report_id)

    # Build context from the cascade rollup
    from app.db.scenario_queries import cascade_rollup
    from app.services.hotel import group_hotel_totals

    if group:
        rollup_figures = _group_figures(await cascade_rollup(session, group_id=group.id))
        hotel = await group_hotel_totals(session, group_id=group.id)
        reference = group.reference
        scope = "group"
        scope_note = "Figures cover every flight this disruption group declares."
    elif incident and incident.group_id:
        # An incident reference produces an INCIDENT-scoped report.
        #
        # It used to produce a group-scoped one under the incident's own name: `cascade_rollup` and
        # `group_hotel_totals` both take a group id, so a request for INC-...-01 came back
        # narrating 445 passengers and 166 rooms — the whole cascade — beneath the heading of a
        # single flight's incident. Relabelling it to the group's reference would have made the
        # heading honest while still answering a question nobody asked. The report now answers the
        # question it was asked, at the scope it was asked at, and `scope` says which that is.
        rollup_figures = await _incident_figures(session, incident)
        hotel = await _incident_hotel_totals(session, incident)
        reference = incident.reference
        scope = "incident"
        scope_note = (
            f"Figures cover {incident.reference} alone — one flight — not the wider disruption "
            "group it belongs to."
        )
    else:
        raise EntityNotFound(
            "no group context for report generation",
            details={"report_id": report_id},
        )

    # The state actually on screen right now, re-derived from recorded rows rather than trusted
    # off a stored column — see `_operational_state`. This is the one fact the narrative below is
    # not permitted to contradict, whatever the reporter agent said.
    actual_state, member_incident_ids = await _operational_state(
        session, group=group, incident=incident
    )
    is_resolved = actual_state is IncidentState.resolved
    # Told to the model too, live or fixture: the prompt no longer gets to assume "resolved" just
    # because that is the only case it used to be asked about.
    rollup_figures = {**rollup_figures, "current_state": actual_state.value}

    from app.agents.reporter import ReportGeneratorAgent
    from app.llm.client import LLMUnavailable

    agent = ReportGeneratorAgent()
    try:
        response, audit = await agent.generate(
            group_reference=reference,
            rollup=rollup_figures,
            hotel_summary=hotel,
        )
    except LLMUnavailable as exc:
        transport = provider_transport(settings)
        log.warning(
            "report_generation_failed",
            reference=reference,
            provider=transport.provider.value,
            model=transport.model,
            phase=getattr(exc, "phase", "unknown"),
            status_code=getattr(exc, "status_code", None),
            finish_reason=getattr(exc, "finish_reason", None),
            content_length=getattr(exc, "content_length", None),
            error=type(exc).__name__,
        )
        raise _unavailable(exc, artifact="report", mode=settings.llm_mode.value) from exc

    # A fixture replay's prose is a committed artefact. It was written about the whole disruption
    # and it does not change with the figures this request passed in, so an incident-scoped request
    # served from a fixture gets incident-scoped FIGURES and a group-scoped NARRATIVE. Saying so is
    # the whole point of this endpoint's scope work: the alternative is a group story under an
    # incident heading, which is exactly the defect being fixed. A live call builds its prompt from
    # the scoped figures, so there the two agree.
    source = _source_of(audit.generator)
    narrative_scope = "group" if source == "fixture" else scope
    if narrative_scope != scope:
        scope_note = (
            f"{scope_note} The narrative below is a committed artefact written at group scope and "
            "replayed unchanged, so it describes the whole disruption; the figures are this "
            "incident's."
        )

    # The reporter's own claim of resolution is never trusted on its own. A fixture is a fixed
    # artefact recorded for one scenario's happy path and replayed unchanged for every request —
    # it has no way to know this reference is still blocked. A live call can in principle get it
    # right now that `current_state` is in its prompt, but a model can still say the wrong thing,
    # and a C-suite reader has no way to catch it. So whenever the recorded state disagrees with
    # "resolved", `status` moves off `success` — the one signal every caller of this endpoint, UI
    # included, already renders — and the summary and EVERY section are replaced with a
    # server-composed statement built only from recorded rows, never from the agent's prose.
    #
    # Earlier this only dropped sections whose heading matched "resolution" / "outcome" /
    # "conclusion" and kept the rest verbatim. That missed a whole class of stale claim: this
    # fixture's own "Recovery actions" section says the high-risk notification "received explicit
    # operator approval" — true in the happy path the fixture was recorded for, false the moment a
    # real run rejects that approval instead. A heading keyword cannot tell those runs apart, and
    # neither can any other heading-based filter, because the fixture is one fixed artefact replayed
    # for every request regardless of what actually happened. So nothing short of dropping every
    # agent-authored section is a real guarantee: this is enforced here, in the endpoint, not left
    # to a screen to notice and relabel, and not left to a keyword list that the next canned section
    # can slip past.
    narrative_overridden = False
    if not is_resolved:
        evidence = await _unresolved_evidence(session, member_incident_ids)
        state_gloss = _STATE_GLOSS.get(actual_state, actual_state.value)
        correction = (
            f"{reference} has not resolved. Current state: {state_gloss}."
            + (f" Recorded: {evidence}." if evidence else "")
            + " Nothing in this report should be read as a final, resolved outcome — it describes"
            " the disruption and the recovery attempted so far, not its conclusion."
        )
        from app.agents.contract import ReportSection

        response = response.model_copy(
            update={
                "status": (
                    ActionStatus.failure
                    if actual_state is IncidentState.failed
                    else ActionStatus.needs_human
                ),
                "reason": (
                    f"Executive summary generated while {reference} remains "
                    f"{actual_state.value}, not resolved."
                ),
                "summary": correction,
                # Every section, not a filtered subset: see the note above for why a keyword
                # filter over headings already proved insufficient.
                "sections": [ReportSection(heading="Current status", body=correction)],
            }
        )
        narrative_overridden = True

    return {
        "reference": reference,
        #: Which scope the FIGURES describe. Published rather than implied, because the same
        #: endpoint legitimately answers at two scopes and the reference alone cannot say which.
        "scope": scope,
        #: Which scope the PROSE describes. Equal to `scope` for a live call; `group` for a fixture
        #: replay, whose text is fixed at the scope it was recorded at.
        "narrative_scope": narrative_scope,
        "scope_note": scope_note,
        #: The state this report was generated against, re-derived from recorded rows every call
        #: (see `_operational_state`) — never the persisted `incident_group.state` column, which
        #: can lag a member that reached `blocked` on its own. This is the fact the narrative is
        #: not permitted to contradict.
        "operational_state": actual_state.value,
        "is_resolved": is_resolved,
        #: True when the agent's own narrative (fixture or live) disagreed with `operational_state`
        #: and was replaced. Published so a reader — and a test — can tell a corrected report from
        #: one the agent got right unprompted.
        "narrative_overridden": narrative_overridden,
        "generator": audit.generator,
        "prompt_version": audit.prompt_version,
        "source": source,
        "llm_mode": settings.llm_mode.value,
        **response.model_dump(mode="json"),
        "audit": audit.model_dump(mode="json"),
        "authorises_no_action": True,
    }

"""Bind the deterministic services to the orchestrator's dispatch table.

`app/orchestrator/dispatch.py` (Stream A) exposes `register(action, call)` and keeps
`SERVICE_REGISTRY` empty until Stream C's services land. This module is that landing: it
adapts each service to dispatch's calling convention and registers the four that exist.

    from app.services.registry import register_all
    register_all()

**One line is still needed in a Stream A file** — a call to `register_all()` in
`app/main.py`'s lifespan — and I have not made it, because `main.py` is not mine. Until then
`SERVICE_REGISTRY` stays empty in the running API and dispatch correctly refuses with
`SERVICE_NOT_IMPLEMENTED`, which is the honest state rather than a half-wired one.

The registration call is deliberately *not* performed at import time in
`app/services/__init__.py`, which would appear to solve that. `dispatch` imports
`app.services.base`, so importing `dispatch` from the `app.services` package initialiser
creates a cycle whose symptom is a partially-initialised module and a confusing
`AttributeError: register` at startup. `dispatch` is imported inside the function body here
for the same reason: the dependency direction that belongs in this codebase is
orchestrator → services, and this module inverts it in exactly one place, on purpose, with
nothing imported at module scope.

## What the adapters do

Dispatch calls a service with `target_refs`, `inputs` and `evidence_refs` — and **no
session**. The services themselves are pure value-in/value-out, so each adapter:

1. resolves a session (one supplied by the caller, or its own read-only one),
2. resolves the incident scope from `inputs` or `target_refs`,
3. loads value objects through `app.db.scenario_queries`,
4. calls the service and returns its `ServiceResult` unchanged.

`ACTION_OWNERS` in dispatch stays authoritative: `register_all` asserts that every action it
binds is owned by the service it binds, and refuses to start if the two disagree.

Owner: Stream C.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ActionStatus, ActionType, ProvenanceKind
from app.services.base import ServiceResult
from app.services.communication import CommunicationService, Recipient
from app.services.connection import ConnectionService
from app.services.crew_impact import CrewImpactService

#: Action -> the service `name` that implements it. Must agree with `dispatch.ACTION_OWNERS`,
#: which remains the single authority; this is checked, not assumed.
IMPLEMENTED_ACTIONS: dict[ActionType, str] = {
    ActionType.check_connections: ConnectionService.name,
    ActionType.assess_crew_impact: CrewImpactService.name,
    ActionType.prepare_notifications: CommunicationService.name,
    ActionType.notify_passengers: CommunicationService.name,
}

#: Template used for the passenger-facing delay message. Approved, versioned content in
#: `fixtures/notifications/templates.json`.
DELAY_TEMPLATE_ID = "delay_notice"

_MISSING_SCOPE = (
    "No affected flight could be resolved from the task. Reporting an empty result would "
    "read as 'nothing is affected', so this is refused instead."
)


# --------------------------------------------------------------------------- scope


def _flight_ids_from_refs(refs: Sequence[str]) -> set[int]:
    found: set[int] = set()
    for ref in refs:
        if ref.startswith("flight:"):
            candidate = ref.split(":", 1)[1]
            if candidate.isdigit():
                found.add(int(candidate))
    return found


def _incident_reference_from_refs(refs: Sequence[str]) -> str | None:
    for ref in refs:
        if ref.startswith("incident:"):
            return ref.split(":", 1)[1]
    return None


async def resolve_scope(
    session: AsyncSession,
    *,
    target_refs: Sequence[str],
    inputs: dict[str, Any],
) -> set[int]:
    """Which flights this task covers.

    Resolution order, most explicit first:

    1. `inputs["affected_flight_ids"]` — a planner or an operator said so outright.
    2. Every flight with an incident in the **same incident group**. This is what makes the
       cascade numbers right: one weather event owns eight flight incidents, and a
       connection or crew assessment scoped to a single flight would report 2 rotations
       rather than 9. The group is the unit of the cascade.
    3. The incident's own flight, for a single-flight incident with no group.

    An empty result is never silently returned; the caller refuses instead.
    """
    explicit = inputs.get("affected_flight_ids")
    if explicit:
        return {int(value) for value in explicit}

    from app.models.workflow import Incident

    reference = _incident_reference_from_refs(target_refs)
    if reference is not None:
        incident = (
            (await session.execute(select(Incident).where(Incident.reference == reference)))
            .scalars()
            .first()
        )
        if incident is not None:
            if incident.group_id is not None:
                siblings = (
                    (
                        await session.execute(
                            select(Incident.flight_id).where(Incident.group_id == incident.group_id)
                        )
                    )
                    .scalars()
                    .all()
                )
                if siblings:
                    return {int(value) for value in siblings}
            return {int(incident.flight_id)}

    return _flight_ids_from_refs(target_refs)


def _refusal(reason: str, *, evidence_refs: Sequence[str] | None = None) -> ServiceResult:
    return ServiceResult(
        status=ActionStatus.needs_human,
        reason=reason,
        payload={"reason_code": "SCOPE_UNRESOLVED"},
        evidence_refs=list(evidence_refs or []),
        provenance_kind=ProvenanceKind.unavailable.value,
    )


#: A session the caller has bound for the duration of a run.
#:
#: Dispatch passes no session, so without this an adapter must build one from
#: `settings.database_url` — which is right in production and wrong anywhere the caller is
#: already holding a session to a different database, such as a test on SQLite. A contextvar
#: lets the caller lend its session without dispatch's signature changing.
_bound_session: ContextVar[AsyncSession | None] = ContextVar(
    "travelops_registry_session", default=None
)


@contextmanager
def bind_session(session: AsyncSession) -> Iterator[None]:
    """Lend a session to every adapter invoked inside this block.

    Stream A can wrap a run in this to have the services read through the engine's own
    session and connection:

        with bind_session(session):
            await orchestrator.run(ctx)
    """
    token = _bound_session.set(session)
    try:
        yield
    finally:
        _bound_session.reset(token)


class _SessionScope:
    """Resolve a session: explicit argument, then bound session, then one of our own.

    A session of our own is safe because every adapter only *reads* committed reference and
    synthetic data; the engine's uncommitted incident and action rows are never needed. It is
    also the least good option — it costs a second connection and cannot see anything the
    caller has not committed — so it comes last.
    """

    def __init__(self, session: AsyncSession | None) -> None:
        self._provided = session or _bound_session.get()
        self._owned: AsyncSession | None = None

    async def __aenter__(self) -> AsyncSession:
        if self._provided is not None:
            return self._provided
        from app.db.session import get_sessionmaker

        self._owned = get_sessionmaker()()
        return self._owned

    async def __aexit__(self, *_exc: object) -> None:
        if self._owned is not None:
            # Read-only: rolled back rather than committed, so an adapter can never write.
            await self._owned.rollback()
            await self._owned.close()


# --------------------------------------------------------------------------- adapters


async def connection_adapter(
    *,
    target_refs: Sequence[str] | None = None,
    inputs: dict[str, Any] | None = None,
    evidence_refs: Sequence[str] | None = None,
    session: AsyncSession | None = None,
    **_kwargs: Any,
) -> ServiceResult:
    """`check_connections` -> ConnectionService."""
    from app.db.scenario_queries import load_business_constraints, load_connection_inputs

    refs = list(target_refs or [])
    payload_inputs = dict(inputs or {})

    async with _SessionScope(session) as active:
        scope = await resolve_scope(active, target_refs=refs, inputs=payload_inputs)
        if not scope:
            return _refusal(_MISSING_SCOPE, evidence_refs=evidence_refs)

        itineraries, flights = await load_connection_inputs(active, scope)
        constraints = await load_business_constraints(active)

        return await ConnectionService().execute(
            itineraries=itineraries,
            flights=flights,
            affected_flight_ids=scope,
            business_constraints=constraints,
        )


async def crew_impact_adapter(
    *,
    target_refs: Sequence[str] | None = None,
    inputs: dict[str, Any] | None = None,
    evidence_refs: Sequence[str] | None = None,
    session: AsyncSession | None = None,
    **_kwargs: Any,
) -> ServiceResult:
    """`assess_crew_impact` -> CrewImpactService."""
    from app.db.scenario_queries import load_crew_impact_inputs

    refs = list(target_refs or [])
    payload_inputs = dict(inputs or {})

    async with _SessionScope(session) as active:
        scope = await resolve_scope(active, target_refs=refs, inputs=payload_inputs)
        if not scope:
            return _refusal(_MISSING_SCOPE, evidence_refs=evidence_refs)

        affected, pairings, flights = await load_crew_impact_inputs(active, scope)
        if not affected:
            return _refusal(
                f"No flights found for {sorted(scope)}; crew impact cannot be assessed.",
                evidence_refs=evidence_refs,
            )

        return await CrewImpactService().execute(
            affected_flights=affected, pairings=pairings, flights=flights
        )


async def _communication(
    *,
    target_refs: Sequence[str],
    inputs: dict[str, Any],
    evidence_refs: Sequence[str] | None,
    session: AsyncSession | None,
    dispatch_real: bool,
) -> ServiceResult:
    """Shared body for `prepare_notifications` and `notify_passengers`.

    The two differ in one respect and it is the one that matters: `prepare_notifications`
    renders and records without a provider that can deliver anything, while
    `notify_passengers` uses the configured provider and may deliver to an allowlisted
    address. Both record every other recipient as `simulated`.
    """
    from app.db.scenario_queries import load_notification_recipients
    from app.providers.notifications import get_notification_provider
    from app.providers.notifications.console import ConsoleNotificationProvider

    async with _SessionScope(session) as active:
        scope = await resolve_scope(active, target_refs=list(target_refs), inputs=inputs)
        if not scope:
            return _refusal(_MISSING_SCOPE, evidence_refs=evidence_refs)

        rows = await load_notification_recipients(active, scope)
        if not rows:
            return _refusal(
                f"No passengers found on flights {sorted(scope)}; nothing to notify.",
                evidence_refs=evidence_refs,
            )

        recipients = [Recipient(**row) for row in rows]

        if dispatch_real:
            provider = get_notification_provider()
        else:
            # A preparation step must not be able to send. An empty allowlist guarantees it
            # regardless of how the process is configured.
            provider = ConsoleNotificationProvider(allowlist=[])

        return await CommunicationService().execute(
            template_id=inputs.get("template_id") or DELAY_TEMPLATE_ID,
            recipients=recipients,
            provider=provider,
        )


async def prepare_notifications_adapter(
    *,
    target_refs: Sequence[str] | None = None,
    inputs: dict[str, Any] | None = None,
    evidence_refs: Sequence[str] | None = None,
    session: AsyncSession | None = None,
    **_kwargs: Any,
) -> ServiceResult:
    """`prepare_notifications` -> CommunicationService, rendering only."""
    return await _communication(
        target_refs=list(target_refs or []),
        inputs=dict(inputs or {}),
        evidence_refs=evidence_refs,
        session=session,
        dispatch_real=False,
    )


async def notify_passengers_adapter(
    *,
    target_refs: Sequence[str] | None = None,
    inputs: dict[str, Any] | None = None,
    evidence_refs: Sequence[str] | None = None,
    session: AsyncSession | None = None,
    **_kwargs: Any,
) -> ServiceResult:
    """`notify_passengers` -> CommunicationService, with the configured provider.

    This action is `high` risk in `config/assurance.v1.yaml` and therefore always requires
    an approved human decision before the engine will dispatch it. That is the correct
    behaviour for a bulk external effect that cannot be retracted.
    """
    return await _communication(
        target_refs=list(target_refs or []),
        inputs=dict(inputs or {}),
        evidence_refs=evidence_refs,
        session=session,
        dispatch_real=True,
    )


ADAPTERS: dict[ActionType, Any] = {
    ActionType.check_connections: connection_adapter,
    ActionType.assess_crew_impact: crew_impact_adapter,
    ActionType.prepare_notifications: prepare_notifications_adapter,
    ActionType.notify_passengers: notify_passengers_adapter,
}


# --------------------------------------------------------------------------- wiring


def register_all() -> list[ActionType]:
    """Bind every implemented action into `dispatch.SERVICE_REGISTRY`.

    Returns the actions registered. Actions with no service are deliberately left
    unregistered so dispatch keeps refusing them with `SERVICE_NOT_IMPLEMENTED` — a visible
    gap is better than a green run that did nothing.
    """
    # Imported here, not at module scope: see the module docstring on the import cycle.
    from app.orchestrator import dispatch

    _assert_owners_agree(dispatch.ACTION_OWNERS)

    registered: list[ActionType] = []
    for action, adapter in ADAPTERS.items():
        dispatch.register(action, adapter)
        registered.append(action)
    return sorted(registered, key=lambda item: item.value)


def unregister_all() -> None:
    """Remove only what this module registered. Used by tests to avoid cross-talk."""
    from app.orchestrator import dispatch

    for action in ADAPTERS:
        dispatch.SERVICE_REGISTRY.pop(action, None)


def _assert_owners_agree(action_owners: dict[ActionType, str]) -> None:
    """`ACTION_OWNERS` is authoritative; disagreement is a wiring bug, not a preference.

    If this module bound `assess_crew_impact` to the connection service, the audit trail
    would name the wrong owner while running the wrong logic. Failing at registration makes
    that impossible to ship.
    """
    mismatched = {
        action.value: {"dispatch": action_owners.get(action), "registry": expected}
        for action, expected in IMPLEMENTED_ACTIONS.items()
        if action_owners.get(action) != expected
    }
    if mismatched:
        raise RuntimeError(
            "dispatch.ACTION_OWNERS and app.services.registry disagree about which service "
            f"owns an action: {mismatched}. ACTION_OWNERS is authoritative — fix the "
            "registry, not the owners table."
        )

    unbound = sorted(action.value for action in ADAPTERS if action not in IMPLEMENTED_ACTIONS)
    if unbound:
        raise RuntimeError(f"adapters bound for undeclared actions: {unbound}")

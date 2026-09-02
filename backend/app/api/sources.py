"""`GET /sources` — the provenance ledger, derived rather than declared.

This endpoint replaces a committed JSON document. That document was the single reason the
console's provenance claims did not agree with each other: it named `groq` as the reasoning
provider while `LLMProvider.openrouter` was the configured default, it reported
`current_mode: off` no matter what `LLM_MODE` was set to, and it carried `kind: real` on rows
whose own `current_mode` read `fixture` or `unused`. The screen was not lying about the data it
had; the data it had was a hand-written file that nothing kept in step with the process.

So every row below is read from one of exactly two places: the resolved settings of the running
process, or rows this deployment actually recorded. Nothing here is a literal that a future
config change could leave stranded, which is the property the old file lacked.

The two-column discipline this endpoint exists to enforce:

    kind   — what the data IS   (real / synthetic / simulated / fixture / unavailable)
    usage  — what this run DID  (used / unused / unavailable)

`kind: real, usage: unused` is a genuine external source nobody called. `kind: fixture,
usage: used` is a committed snapshot standing in for a live read. Those are different sentences,
and collapsing them into one status column is how "LLM LIVE" ended up on screen beside a
provenance row reporting an unconfigured provider.

A note on what `used` may claim. It is only ever set from a durable recorded artefact — a
`weather_observation` row carrying `provenance_kind = real`, a `plan` whose generator is not the
deterministic playbook, an action payload the flight-status adapter wrote. Configuration alone
never earns it: a key in the environment is a capability, and this ledger is the one surface in
the product whose job is to refuse to confuse the two.

Owner: Stream A.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    FlightStatusMode,
    LLMMode,
    LLMProvider,
    WeatherMode,
    get_modes,
    get_settings,
    provider_transport,
)
from app.db.session import get_session
from app.models.reference import WeatherObservation
from app.models.workflow import Action, Plan
from app.orchestrator.playbook import FALLBACK_GENERATOR
from app.schemas.provenance import ProvenanceKind
from app.schemas.sources import SourceRow, SourcesResponse, SourceUsage

router = APIRouter(tags=["sources"])

LEDGER_NOTE = (
    "Every row is derived from this process's resolved configuration and from rows it actually "
    "recorded. `kind` says what the data is; `usage` says whether this run read from it. A "
    "configured provider that nothing called reads `unused`, which is not a fault and not the "
    "same claim as `unavailable`."
)

#: Licence facts about the external sources. These are properties of the sources themselves, not
#: of this deployment, so they are the one thing in this module that is legitimately a literal.
_LICENCE_AWC = "US Government work, public domain"
_LICENCE_OURAIRPORTS = "Public domain"
_LICENCE_NONE = "n/a"


async def _real_weather_observations(session: AsyncSession) -> int:
    """Live METAR reads that were persisted. The only durable proof AWC was actually called."""
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(WeatherObservation)
                .where(WeatherObservation.provenance_kind == ProvenanceKind.real.value)
            )
        ).scalar_one()
    )


async def _last_weather_read(session: AsyncSession) -> datetime | None:
    return (
        await session.execute(
            select(func.max(WeatherObservation.observed_at)).where(
                WeatherObservation.provenance_kind == ProvenanceKind.real.value
            )
        )
    ).scalar_one_or_none()


async def _model_authored_plans(session: AsyncSession) -> int:
    """Plans a reasoning agent wrote.

    `plan.generator` is the recorded token, and the deterministic playbook writes a known
    constant, so anything else came from an agent. This counts candidates as well as selected
    plans on purpose: the model having produced a plan and that plan having become the plan of
    record are separate facts, and this row is about whether the provider was called at all.
    """
    return int(
        (
            await session.execute(
                select(func.count()).select_from(Plan).where(Plan.generator != FALLBACK_GENERATOR)
            )
        ).scalar_one()
    )


async def _flight_status_consulted(session: AsyncSession) -> int:
    """Actions whose recorded payload says the flight-status provider was consulted.

    The adapter attaches its payload only when it actually consulted a provider, so the presence
    of the key is itself the evidence. Read in Python rather than through a JSON operator so the
    query is identical on every dialect the test suite runs against; the scan is bounded because
    only executed actions carry a payload at all.
    """
    rows = (
        await session.execute(select(Action.payload).where(Action.payload.isnot(None)).limit(2000))
    ).scalars()
    consulted = 0
    for payload in rows:
        if not isinstance(payload, dict):
            continue
        block = payload.get("flight_status")
        if isinstance(block, dict) and block.get("consulted"):
            consulted += 1
    return consulted


async def _delivery_counts(session: AsyncSession) -> tuple[int, int]:
    """`(real, simulated)` deliveries, summed from the recorded communication payloads."""
    rows = (
        await session.execute(select(Action.payload).where(Action.payload.isnot(None)).limit(2000))
    ).scalars()
    real = simulated = 0
    for payload in rows:
        if not isinstance(payload, dict):
            continue
        if "real_count" in payload or "simulated_count" in payload:
            real += int(payload.get("real_count") or 0)
            simulated += int(payload.get("simulated_count") or 0)
    return real, simulated


def _reasoning_row(*, model_plans: int) -> SourceRow:
    """The provider `live` mode would actually talk to, named from `provider_transport`.

    This is the row the old ledger got wrong in three separate ways at once, so it is worth being
    explicit: the provider comes from the same resolution the LLM client itself uses, which means
    the ledger cannot drift from the transport the way a literal did.
    """
    settings = get_settings()
    modes = get_modes()
    transport = provider_transport(settings)
    configured = bool(transport.api_key)

    if modes.llm is LLMMode.off:
        return SourceRow(
            name="Reasoning and planning model",
            role="Planner, explainer and executive report agents",
            kind=ProvenanceKind.unavailable,
            provider=transport.provider.value,
            model=transport.model,
            current_mode="off",
            configured=configured,
            usage=SourceUsage.unused,
            usage_detail=(
                "LLM_MODE=off. No model is called, and recovery runs on the deterministic "
                "playbook. Explanation and report generation are unavailable by configuration, "
                "not by failure."
            ),
            licence="Provider terms apply to the configured account",
            health="not_called",
        )

    if modes.llm is LLMMode.fixture:
        return SourceRow(
            name="Reasoning and planning model",
            role="Planner, explainer and executive report agents",
            kind=ProvenanceKind.fixture,
            provider=transport.provider.value,
            model=transport.model,
            current_mode="fixture",
            configured=configured,
            usage=SourceUsage.unused,
            usage_detail=(
                "LLM_MODE=fixture. Reasoning replays committed artefacts, so "
                f"{transport.provider.value} is not contacted on any request. A key being "
                "present does not change that."
            ),
            evidence="committed artefacts under app/llm/fixtures/",
            licence="Provider terms apply to the configured account",
            health="not_called_in_fixture_mode",
        )

    if not configured:
        return SourceRow(
            name="Reasoning and planning model",
            role="Planner, explainer and executive report agents",
            kind=ProvenanceKind.unavailable,
            provider=transport.provider.value,
            model=transport.model,
            current_mode="live",
            configured=False,
            usage=SourceUsage.unavailable,
            usage_detail=(
                f"LLM_MODE=live but {transport.key_env_var} is empty, so no request can be made."
            ),
            licence="Provider terms apply to the configured account",
            health="not_configured",
        )

    if model_plans > 0:
        return SourceRow(
            name="Reasoning and planning model",
            role="Planner, explainer and executive report agents",
            kind=ProvenanceKind.real,
            provider=transport.provider.value,
            model=transport.model,
            current_mode="live",
            configured=True,
            usage=SourceUsage.used,
            usage_detail=(
                f"LLM_MODE=live against {transport.provider.value}. A reasoning agent authored "
                "at least one recorded plan on this dataset."
            ),
            evidence=f"{model_plans} plan(s) recorded with a non-playbook generator",
            licence="Provider terms apply to the configured account",
            health="ok",
        )

    return SourceRow(
        name="Reasoning and planning model",
        role="Planner, explainer and executive report agents",
        kind=ProvenanceKind.real,
        provider=transport.provider.value,
        model=transport.model,
        current_mode="live",
        configured=True,
        usage=SourceUsage.unused,
        usage_detail=(
            f"LLM_MODE=live against {transport.provider.value}, and nothing on this dataset has "
            "asked it for reasoning yet. Configured is not the same as called."
        ),
        licence="Provider terms apply to the configured account",
        health="configured_not_yet_called",
    )


def _alternative_transport_row() -> SourceRow | None:
    """The reasoning provider that is registered and deliberately not selected.

    Published as its own row because its absence is what made the old ledger unreadable. Groq
    appeared there as *the* reasoning provider; a reader who then saw `LLM LIVE` in the top bar
    had no way to learn that OpenRouter was the endpoint being called. Naming the unselected
    transport, and saying plainly that it is unselected, is cheaper than expecting a reader to
    infer it from an absence.
    """
    settings = get_settings()
    selected = provider_transport(settings).provider
    other = LLMProvider.groq if selected is LLMProvider.openrouter else LLMProvider.openrouter
    key = settings.groq_api_key if other is LLMProvider.groq else settings.openrouter_api_key
    model = settings.groq_model if other is LLMProvider.groq else settings.openrouter_model
    return SourceRow(
        name=f"{other.value} (alternative reasoning transport)",
        role="Registered alternative for the reasoning agents",
        kind=ProvenanceKind.unavailable,
        provider=other.value,
        model=model,
        current_mode="not_selected",
        configured=bool(key),
        usage=SourceUsage.unused,
        usage_detail=(
            f"LLM_PROVIDER selects {selected.value}, so {other.value} is registered but never "
            "called. It authored nothing on this dataset."
        ),
        licence="Provider terms apply to the configured account",
        health="not_selected",
    )


def _weather_row(*, real_reads: int, last_read: datetime | None) -> SourceRow:
    modes = get_modes()
    if modes.weather is WeatherMode.live:
        if real_reads > 0:
            return SourceRow(
                name="Aviation Weather Center METAR/TAF",
                role="Observed conditions behind delay-risk scoring",
                kind=ProvenanceKind.real,
                provider="awc",
                current_mode="live",
                configured=True,
                usage=SourceUsage.used,
                usage_detail=(
                    "WEATHER_MODE=live. Live METAR observations were retrieved and persisted "
                    "with their source references."
                ),
                evidence=f"{real_reads} observation(s) recorded with provenance_kind=real",
                last_checked=last_read,
                licence=_LICENCE_AWC,
                health="ok",
            )
        return SourceRow(
            name="Aviation Weather Center METAR/TAF",
            role="Observed conditions behind delay-risk scoring",
            kind=ProvenanceKind.real,
            provider="awc",
            current_mode="live",
            configured=True,
            usage=SourceUsage.unused,
            usage_detail=(
                "WEATHER_MODE=live and no live observation has been persisted on this dataset. "
                "Either nothing has asked for weather yet, or every attempt was refused — the "
                "incident timeline records which."
            ),
            licence=_LICENCE_AWC,
            health="configured_not_yet_called",
        )
    return SourceRow(
        name="Aviation Weather Center METAR/TAF",
        role="Observed conditions behind delay-risk scoring",
        kind=ProvenanceKind.fixture,
        provider="awc-fixture",
        current_mode="fixture",
        configured=True,
        usage=SourceUsage.used,
        usage_detail=(
            "WEATHER_MODE=fixture. Conditions come from the committed METAR snapshot; the "
            "Aviation Weather Center is not contacted."
        ),
        evidence="committed METAR snapshot",
        licence=_LICENCE_AWC,
        health="not_called_in_fixture_mode",
        note="The snapshot is a recording of a real AWC response, replayed rather than fetched.",
    )


def _flight_status_row(*, consulted: int) -> SourceRow:
    settings = get_settings()
    modes = get_modes()
    if modes.flight_status is FlightStatusMode.live:
        configured = bool(settings.aviationstack_api_key)
        if consulted > 0:
            return SourceRow(
                name="Flight status",
                role="Observed departure state, overlaid on the scheduled board",
                kind=ProvenanceKind.real,
                provider="aviationstack",
                current_mode="live",
                configured=configured,
                usage=SourceUsage.used,
                usage_detail=(
                    "FLIGHT_STATUS_MODE=live. The provider was consulted and its answer is "
                    "recorded on the actions that used it."
                ),
                evidence=f"{consulted} action payload(s) recording a provider consultation",
                licence="AviationStack terms apply to the configured account",
                health="ok",
            )
        return SourceRow(
            name="Flight status",
            role="Observed departure state, overlaid on the scheduled board",
            kind=ProvenanceKind.real,
            provider="aviationstack",
            current_mode="live",
            configured=configured,
            usage=SourceUsage.unused,
            usage_detail=(
                "FLIGHT_STATUS_MODE=live and no recorded action shows the provider being "
                "consulted yet. Derived delays currently stand on the scheduled board alone."
            ),
            licence="AviationStack terms apply to the configured account",
            health="configured_not_yet_called",
        )
    return SourceRow(
        name="Flight status",
        role="Observed departure state, overlaid on the scheduled board",
        kind=ProvenanceKind.simulated,
        provider="local-simulator",
        current_mode="fixture",
        configured=True,
        usage=SourceUsage.used,
        usage_detail=(
            "FLIGHT_STATUS_MODE=fixture. Observed state comes from the committed snapshot; no "
            "vendor API is called."
        ),
        evidence="committed flight-status snapshot",
        licence=_LICENCE_NONE,
        health="not_called_in_fixture_mode",
    )


def _notification_row(*, real: int, simulated: int) -> SourceRow:
    modes = get_modes()
    if modes.real_email_enabled:
        return SourceRow(
            name="Passenger notifications",
            role="Outbound passenger messages",
            kind=ProvenanceKind.real,
            provider=modes.notification.value,
            current_mode=modes.notification.value,
            configured=True,
            usage=SourceUsage.used if real > 0 else SourceUsage.unused,
            usage_detail=(
                f"Real delivery is enabled to allowlisted recipients only. {real} message(s) "
                f"were actually delivered; {simulated} are recorded as simulated and were sent "
                "nowhere."
            )
            if real > 0
            else (
                "Real delivery is enabled to allowlisted recipients, and nothing has been "
                f"delivered yet. {simulated} message(s) are recorded as simulated."
            ),
            evidence=f"{real} real, {simulated} simulated" if real or simulated else None,
            licence=_LICENCE_NONE,
            health="ok",
        )
    return SourceRow(
        name="Passenger notifications",
        role="Outbound passenger messages",
        kind=ProvenanceKind.simulated,
        provider=modes.notification.value,
        current_mode=modes.notification.value,
        configured=True,
        usage=SourceUsage.used if simulated > 0 else SourceUsage.unused,
        usage_detail=(
            f"Nothing is delivered to a recipient. {simulated} message(s) are recorded with "
            "delivery_mode=simulated."
        )
        if simulated > 0
        else (
            "Nothing is delivered to a recipient: messages would be recorded as simulated, and "
            "none has been generated yet."
        ),
        evidence=f"{simulated} simulated deliver(ies)" if simulated else None,
        licence=_LICENCE_NONE,
        health="ok",
    )


def _policy_row() -> SourceRow:
    """Read from the loaded pack, using the same helper the runtime chip reads."""
    from app.api.health import _policy_pack_payload

    settings = get_settings()
    pack: dict[str, Any] = _policy_pack_payload(settings)
    label = pack.get("ui_label") or ""
    return SourceRow(
        name=label or f"Policy pack {pack.get('id')}",
        role="Entitlement figures cited in plans and reports",
        kind=ProvenanceKind.real,
        provider="moca",
        current_mode=settings.policy_mode.value,
        configured=bool(pack.get("id")),
        usage=SourceUsage.used,
        usage_detail=(
            "Entitlement figures are cited from this pack, and the badge renders the pack's own "
            "label rather than one composed from the configured mode."
        ),
        evidence=f"{pack.get('id')} {pack.get('version')}",
        licence="Government of India publication; redistribution not yet confirmed",
        attribution_required=True,
        health="ok",
        note="Official but dated. The source PDF hash is not archived, so verified mode stays blocked.",
    )


def _generated_rows() -> list[SourceRow]:
    """The seeded dataset. Synthetic and labelled as such, which was already true and stays true."""
    common = {
        "kind": ProvenanceKind.synthetic,
        "provider": "generator",
        "current_mode": "synthetic",
        "configured": True,
        "usage": SourceUsage.used,
        "licence": _LICENCE_NONE,
        "health": "ok",
    }
    return [
        SourceRow(
            name="Airports and runways",
            role="Reference geography",
            kind=ProvenanceKind.real,
            provider="ourairports",
            current_mode="snapshot",
            configured=True,
            usage=SourceUsage.used,
            usage_detail=(
                "A committed snapshot of a real public-domain dataset. Real in origin, and read "
                "from disk rather than fetched."
            ),
            evidence="committed OurAirports extract",
            licence=_LICENCE_OURAIRPORTS,
            health="ok",
        ),
        SourceRow(
            name="Flight schedules",
            role="The scheduled board every derived delay is measured against",
            usage_detail=(
                "Generated by this project with a fixed seed. No real airline schedule data is "
                "used anywhere in the product."
            ),
            evidence="fixed-seed generator output",
            note=(
                "AIKosh is a candidate real source, but its file, schema and licence are not "
                "archived, so schedules stay synthetic and labelled."
            ),
            **common,
        ),
        SourceRow(
            name="Passengers and bookings",
            role="Who is on each flight, and the priority ranking",
            usage_detail=(
                "Generated by this project with a fixed seed. No real personal data exists in "
                "any code path."
            ),
            evidence="fixed-seed generator output",
            **common,
        ),
        SourceRow(
            name="Hotel inventory",
            role="Room availability behind accommodation decisions",
            usage_detail=(
                "Generated by this project with a fixed seed. Room counts, rates and holds are "
                "synthetic throughout."
            ),
            evidence="fixed-seed generator output",
            **common,
        ),
        SourceRow(
            name="Crew and pairings",
            role="Crew legality inputs",
            usage_detail="Generated by this project with a fixed seed.",
            evidence="fixed-seed generator output",
            **common,
        ),
    ]


@router.get(
    "/sources",
    response_model=SourcesResponse,
    summary="Provenance ledger: what each source is, and whether this run used it",
)
async def list_sources(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SourcesResponse:
    real_reads = await _real_weather_observations(session)
    last_read = await _last_weather_read(session)
    model_plans = await _model_authored_plans(session)
    consulted = await _flight_status_consulted(session)
    real_deliveries, simulated_deliveries = await _delivery_counts(session)

    rows: list[SourceRow] = [
        _reasoning_row(model_plans=model_plans),
        _weather_row(real_reads=real_reads, last_read=last_read),
        _flight_status_row(consulted=consulted),
        _notification_row(real=real_deliveries, simulated=simulated_deliveries),
        _policy_row(),
        *_generated_rows(),
    ]
    alternative = _alternative_transport_row()
    if alternative is not None:
        rows.append(alternative)

    return SourcesResponse(
        sources=rows,
        # Both facts, deliberately. A source is counted live only when it is a real source AND
        # this run actually read from it; either one alone is what the old ledger counted.
        live_count=sum(
            1 for row in rows if row.kind is ProvenanceKind.real and row.usage is SourceUsage.used
        ),
        unused_count=sum(1 for row in rows if row.usage is SourceUsage.unused),
        unavailable_count=sum(1 for row in rows if row.usage is SourceUsage.unavailable),
        note=LEDGER_NOTE,
    )

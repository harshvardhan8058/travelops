"""Event bus transport and delivery-reliability tests.

The bus is exercised as-is over an in-memory stream client, so the retry,
acknowledgement, dead-letter and duplicate-suppression code under test is the same code
that runs against Redis.

What is asserted here is delivery behaviour only. The bus is not permitted to make a
safety decision, and `test_bus_makes_no_safety_decisions` asserts that mechanically.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from app.events.bus import (
    DEFAULT_MAX_DELIVERY_ATTEMPTS,
    Outcome,
    ProcessedRegistry,
    RedisEventBus,
    decode,
    encode,
)
from app.events.inmemory import InMemoryStreamClient, in_memory_bus
from app.events.types import (
    ActionCompleted,
    AssuranceEvaluated,
    EventType,
    HighRiskDelay,
    HumanDecisionRecorded,
    IncidentOpened,
    IncidentResolved,
    PlanProposed,
    RecoveryBlocked,
    WeatherObserved,
)
from app.models.enums import (
    ActionStatus,
    AssuranceDecision,
    HumanDecisionType,
    RiskLevel,
    TriggerType,
)
from app.observability.logging import correlation_id_var

GROUP = "orchestrator"
CONSUMER = "worker-1"


class Clock:
    """Manual clock in seconds, so idle time can be aged without sleeping."""

    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value

    def advance_ms(self, milliseconds: float) -> None:
        self.value += milliseconds / 1000


def a_high_risk_delay(**overrides) -> HighRiskDelay:
    payload = {
        "producer": "delay-risk",
        "flight_id": 42,
        "risk_index": 87,
        "risk_level": RiskLevel.high,
        "rule_version": "delay-risk-v1",
        "factors": ["visibility_below_threshold"],
        "evidence_refs": ["fixture:bengaluru_storm:weather"],
    }
    payload.update(overrides)
    return HighRiskDelay(**payload)


ONE_OF_EVERY_EVENT = [
    WeatherObserved(
        producer="awc", airport_icao="VOBL", wind_speed_kt=24, visibility_m=800, provenance={}
    ),
    a_high_risk_delay(),
    IncidentOpened(
        producer="orchestrator",
        incident_reference="INC-1",
        flight_id=42,
        trigger_type=TriggerType.weather,
    ),
    PlanProposed(producer="orchestrator", plan_id=1, generator="fallback-playbook"),
    AssuranceEvaluated(
        producer="assurance",
        evaluation_id=1,
        plan_task_id=1,
        decision=AssuranceDecision.needs_human,
        risk_tier="high",
        check_results={},
        config_version="assurance-v1",
        config_hash="abc123",
    ),
    ActionCompleted(
        producer="orchestrator", action_id=1, plan_task_id=1, status=ActionStatus.success, actor="x"
    ),
    HumanDecisionRecorded(
        producer="api",
        evaluation_id=1,
        decision=HumanDecisionType.approved,
        actor_id="operator-1",
        reason="checked",
    ),
    IncidentResolved(producer="orchestrator", incident_reference="INC-1"),
    RecoveryBlocked(producer="orchestrator", incident_reference="INC-1"),
]


class TestWireFormat:
    @pytest.mark.parametrize("event", ONE_OF_EVERY_EVENT, ids=lambda e: str(e.event_type))
    def test_round_trip_preserves_the_typed_event(self, event):
        restored = decode(encode(event))
        assert type(restored) is type(event)
        assert restored.event_type is event.event_type
        assert restored.model_dump() == event.model_dump()

    def test_every_event_type_is_covered(self):
        """A new event type must arrive with a round-trip case, not silently."""
        assert {e.event_type for e in ONE_OF_EVERY_EVENT} == set(EventType)

    def test_flat_fields_are_queryable_without_parsing_the_payload(self):
        event = a_high_risk_delay(correlation_id="corr-1", incident_id=7)
        fields = encode(event)
        assert fields["event_id"] == event.event_id
        assert fields["event_type"] == EventType.high_risk_delay.value
        assert fields["correlation_id"] == "corr-1"
        assert fields["incident_id"] == "7"

    def test_bytes_from_an_undecoded_client_are_tolerated(self):
        event = a_high_risk_delay()
        raw = {k.encode(): v.encode() for k, v in encode(event).items()}
        assert decode(raw).event_id == event.event_id

    def test_discrimination_uses_the_payload_not_the_flat_field(self):
        """A tampered flat field cannot coerce an event into the wrong class."""
        fields = encode(a_high_risk_delay())
        fields["event_type"] = EventType.incident_resolved.value
        assert isinstance(decode(fields), HighRiskDelay)

    def test_unparseable_payload_is_rejected_without_echoing_it(self):
        from app.errors import ValidationFailed

        with pytest.raises(ValidationFailed) as caught:
            decode({"payload": '{"event_type": "NOT_A_REAL_EVENT"}', "event_id": "e1"})
        assert caught.value.details["event_id"] == "e1"
        assert "payload" not in caught.value.details

    def test_missing_payload_field_is_rejected(self):
        from app.errors import ValidationFailed

        with pytest.raises(ValidationFailed):
            decode({"event_type": "HIGH_RISK_DELAY"})


class TestPublish:
    async def test_publish_appends_and_returns_the_entry_id(self):
        bus, client = in_memory_bus()
        entry_id = await bus.publish(a_high_risk_delay())
        assert entry_id
        assert len(client.entries()) == 1

    async def test_publish_without_a_dedupe_key_is_at_least_once(self):
        """Two publishes of the same event are two entries. Consumers deduplicate."""
        bus, client = in_memory_bus()
        event = a_high_risk_delay()
        await bus.publish(event)
        await bus.publish(event)
        assert len(client.entries()) == 2

    async def test_dedupe_key_suppresses_the_second_publish(self):
        bus, client = in_memory_bus()
        first = await bus.publish(a_high_risk_delay(), dedupe_key="flight:42:delay-risk-v1")
        second = await bus.publish(a_high_risk_delay(), dedupe_key="flight:42:delay-risk-v1")
        assert first is not None
        assert second is None
        assert len(client.entries()) == 1

    async def test_a_different_dedupe_key_still_publishes(self):
        bus, client = in_memory_bus()
        await bus.publish(a_high_risk_delay(), dedupe_key="flight:42:delay-risk-v1")
        await bus.publish(a_high_risk_delay(flight_id=43), dedupe_key="flight:43:delay-risk-v1")
        assert len(client.entries()) == 2

    async def test_transport_failure_surfaces_as_provider_unavailable(self):
        from redis.exceptions import ConnectionError as RedisConnectionError

        from app.errors import ProviderUnavailable

        class Broken(InMemoryStreamClient):
            async def xadd(self, name, fields):
                raise RedisConnectionError("connection refused")

        bus = RedisEventBus(Broken())
        with pytest.raises(ProviderUnavailable) as caught:
            await bus.publish(a_high_risk_delay())
        assert caught.value.details["operation"] == "publish"


class TestConsume:
    async def test_handler_receives_the_typed_event(self):
        bus, _client = in_memory_bus()
        seen: list[HighRiskDelay] = []
        bus.subscribe(EventType.high_risk_delay, _collector(seen))
        await bus.publish(a_high_risk_delay())
        report = await bus.consume_once(group=GROUP, consumer=CONSUMER, block_ms=0)

        assert report.get(Outcome.handled) == 1
        assert len(seen) == 1
        assert isinstance(seen[0], HighRiskDelay)
        assert seen[0].risk_index == 87

    async def test_handled_event_is_acknowledged(self):
        bus, client = in_memory_bus()
        bus.subscribe(EventType.high_risk_delay, _collector([]))
        await bus.publish(a_high_risk_delay())
        await bus.consume_once(group=GROUP, consumer=CONSUMER, block_ms=0)
        assert client.pending_ids("travelops.events", GROUP) == []

    async def test_a_wildcard_handler_receives_every_event_type(self):
        bus, _client = in_memory_bus()
        seen: list[object] = []
        bus.subscribe(None, _collector(seen))
        for event in ONE_OF_EVERY_EVENT:
            await bus.publish(event)
        await bus.consume_once(group=GROUP, consumer=CONSUMER, count=50, block_ms=0)
        assert len(seen) == len(ONE_OF_EVERY_EVENT)

    async def test_unsubscribed_event_is_acknowledged_not_failed(self):
        """No handler is not an error: another group may be the intended subscriber."""
        bus, client = in_memory_bus()
        bus.subscribe(EventType.incident_resolved, _collector([]))
        await bus.publish(a_high_risk_delay())
        report = await bus.consume_once(group=GROUP, consumer=CONSUMER, block_ms=0)
        assert report.get(Outcome.ignored) == 1
        assert client.pending_ids("travelops.events", GROUP) == []

    async def test_a_group_created_after_publication_still_receives_the_event(self):
        """No silent loss in the window before a consumer first starts.

        Redis's default group start position is `$`, which would skip everything already
        in the stream. An event dropped without a trace is unrecoverable, so the bus
        starts new groups at the beginning instead.
        """
        bus, _client = in_memory_bus()
        await bus.publish(a_high_risk_delay())  # published before any group exists

        seen: list[object] = []
        bus.subscribe(EventType.high_risk_delay, _collector(seen))
        report = await bus.consume_once(group="late-subscriber", consumer=CONSUMER, block_ms=0)

        assert report.get(Outcome.handled) == 1
        assert len(seen) == 1

    async def test_backlog_replay_is_safe_because_of_duplicate_suppression(self):
        """The replay the previous test permits must not double-handle anything."""
        bus, _client = in_memory_bus()
        seen: list[object] = []
        bus.subscribe(EventType.high_risk_delay, _collector(seen))
        await bus.publish(a_high_risk_delay())

        await bus.consume_once(group=GROUP, consumer=CONSUMER, block_ms=0)
        bus._groups_ready.clear()  # simulate a restarted consumer re-running ensure_group
        await bus.consume_once(group=GROUP, consumer=CONSUMER, block_ms=0)

        assert len(seen) == 1

    async def test_empty_read_is_not_an_error(self):
        bus, _client = in_memory_bus()
        report = await bus.consume_once(group=GROUP, consumer=CONSUMER, block_ms=0)
        assert report.read == 0


class TestIdempotency:
    async def test_the_same_event_id_is_handled_once(self):
        bus, _client = in_memory_bus()
        seen: list[object] = []
        bus.subscribe(EventType.high_risk_delay, _collector(seen))

        event = a_high_risk_delay()
        await bus.publish(event)
        await bus.publish(event)  # same event_id, republished

        report = await bus.consume_once(group=GROUP, consumer=CONSUMER, count=10, block_ms=0)

        assert len(seen) == 1
        assert report.get(Outcome.handled) == 1
        assert report.get(Outcome.duplicate) == 1

    async def test_distinct_event_ids_are_both_handled(self):
        bus, _client = in_memory_bus()
        seen: list[object] = []
        bus.subscribe(EventType.high_risk_delay, _collector(seen))
        await bus.publish(a_high_risk_delay())
        await bus.publish(a_high_risk_delay())
        await bus.consume_once(group=GROUP, consumer=CONSUMER, count=10, block_ms=0)
        assert len(seen) == 2

    async def test_two_consumer_groups_each_handle_the_event_once(self):
        """Suppression is per group. A global marker would starve the second group."""
        bus, _client = in_memory_bus()
        seen: list[object] = []
        bus.subscribe(EventType.high_risk_delay, _collector(seen))
        await bus.publish(a_high_risk_delay())

        await bus.consume_once(group="orchestrator", consumer=CONSUMER, block_ms=0)
        await bus.consume_once(group="analytics", consumer=CONSUMER, block_ms=0)

        assert len(seen) == 2

    async def test_marker_is_not_written_when_the_handler_fails(self):
        """Otherwise a failure would be permanently mistaken for a completed delivery."""
        marks: list[tuple[str, str]] = []

        class RecordingRegistry:
            def __init__(self) -> None:
                self._seen: set[tuple[str, str]] = set()

            async def seen(self, group: str, event_id: str) -> bool:
                return (group, event_id) in self._seen

            async def mark(self, group: str, event_id: str) -> None:
                marks.append((group, event_id))
                self._seen.add((group, event_id))

        registry = RecordingRegistry()
        assert isinstance(registry, ProcessedRegistry)

        client = InMemoryStreamClient()
        bus = RedisEventBus(client, registry=registry)
        bus.subscribe(EventType.high_risk_delay, _raiser())

        await bus.publish(a_high_risk_delay())
        report = await bus.consume_once(group=GROUP, consumer=CONSUMER, block_ms=0)

        assert report.get(Outcome.failed) == 1
        assert marks == []


class TestFailureAndRetry:
    async def test_failed_handler_leaves_the_entry_pending(self):
        bus, client = in_memory_bus()
        bus.subscribe(EventType.high_risk_delay, _raiser())
        await bus.publish(a_high_risk_delay())

        report = await bus.consume_once(group=GROUP, consumer=CONSUMER, block_ms=0)

        assert report.get(Outcome.failed) == 1
        assert len(client.pending_ids("travelops.events", GROUP)) == 1

    async def test_reclaim_retries_a_pending_entry_after_it_goes_idle(self):
        clock = Clock()
        bus, client = in_memory_bus(clock=clock)
        attempts: list[object] = []
        fail_first = _fail_n_times(1, attempts)
        bus.subscribe(EventType.high_risk_delay, fail_first)

        await bus.publish(a_high_risk_delay())
        assert (await bus.consume_once(group=GROUP, consumer=CONSUMER, block_ms=0)).get(
            Outcome.failed
        ) == 1

        clock.advance_ms(60_000)
        report = await bus.reclaim_stale(group=GROUP, consumer=CONSUMER, min_idle_ms=30_000)

        assert report.get(Outcome.handled) == 1
        assert len(attempts) == 2
        assert client.pending_ids("travelops.events", GROUP) == []

    async def test_reclaim_ignores_an_entry_that_is_not_yet_idle(self):
        clock = Clock()
        bus, _client = in_memory_bus(clock=clock)
        bus.subscribe(EventType.high_risk_delay, _raiser())
        await bus.publish(a_high_risk_delay())
        await bus.consume_once(group=GROUP, consumer=CONSUMER, block_ms=0)

        clock.advance_ms(1_000)
        report = await bus.reclaim_stale(group=GROUP, consumer=CONSUMER, min_idle_ms=30_000)
        assert report.read == 0

    async def test_retries_are_bounded_and_end_in_the_dead_letter_stream(self):
        clock = Clock()
        bus, client = in_memory_bus(clock=clock)
        attempts: list[object] = []
        bus.subscribe(EventType.high_risk_delay, _raiser(attempts))

        event = a_high_risk_delay()
        await bus.publish(event)
        await bus.consume_once(group=GROUP, consumer=CONSUMER, block_ms=0)

        outcomes = []
        for _ in range(DEFAULT_MAX_DELIVERY_ATTEMPTS + 2):
            clock.advance_ms(60_000)
            outcomes.append(
                await bus.reclaim_stale(group=GROUP, consumer=CONSUMER, min_idle_ms=30_000)
            )

        dead = client.dead_letters()
        assert len(dead) == 1
        assert dead[0]["event_id"] == event.event_id
        assert dead[0]["dead_letter_reason"] == "max_delivery_attempts_exceeded"
        assert dead[0]["dead_letter_group"] == GROUP
        assert int(dead[0]["delivery_attempts"]) >= DEFAULT_MAX_DELIVERY_ATTEMPTS
        # Bounded: the handler is not retried forever.
        assert len(attempts) <= DEFAULT_MAX_DELIVERY_ATTEMPTS
        # And it is acknowledged, so it cannot be redelivered again.
        assert client.pending_ids("travelops.events", GROUP) == []
        assert sum(r.get(Outcome.dead_lettered) for r in outcomes) == 1

    async def test_undecodable_entry_is_dead_lettered_on_first_sight(self):
        """Re-reading identical bytes cannot succeed, so retrying would be pointless."""
        bus, client = in_memory_bus()
        bus.subscribe(EventType.high_risk_delay, _collector([]))
        await client.xadd("travelops.events", {"payload": "{not json", "event_id": "e9"})

        report = await bus.consume_once(group=GROUP, consumer=CONSUMER, block_ms=0)

        assert report.get(Outcome.dead_lettered) == 1
        dead = client.dead_letters()
        assert len(dead) == 1
        assert dead[0]["dead_letter_reason"] == "payload_validation_failed"
        assert client.pending_ids("travelops.events", GROUP) == []

    async def test_one_failing_handler_does_not_lose_the_rest_of_the_batch(self):
        bus, _client = in_memory_bus()
        seen: list[object] = []
        bus.subscribe(EventType.high_risk_delay, _raiser())
        bus.subscribe(EventType.incident_resolved, _collector(seen))

        await bus.publish(a_high_risk_delay())
        await bus.publish(IncidentResolved(producer="orchestrator", incident_reference="INC-1"))
        report = await bus.consume_once(group=GROUP, consumer=CONSUMER, count=10, block_ms=0)

        assert report.get(Outcome.failed) == 1
        assert report.get(Outcome.handled) == 1
        assert len(seen) == 1


class TestCorrelation:
    async def test_handler_runs_under_the_events_correlation_id(self):
        bus, _client = in_memory_bus()
        observed: list[str | None] = []

        async def handler(_event) -> None:
            observed.append(correlation_id_var.get())

        bus.subscribe(EventType.high_risk_delay, handler)
        await bus.publish(a_high_risk_delay(correlation_id="corr-abc"))
        await bus.consume_once(group=GROUP, consumer=CONSUMER, block_ms=0)

        assert observed == ["corr-abc"]

    async def test_ambient_correlation_id_is_restored_afterwards(self):
        bus, _client = in_memory_bus()
        bus.subscribe(EventType.high_risk_delay, _collector([]))
        token = correlation_id_var.set("outer")
        try:
            await bus.publish(a_high_risk_delay(correlation_id="inner"))
            await bus.consume_once(group=GROUP, consumer=CONSUMER, block_ms=0)
            assert correlation_id_var.get() == "outer"
        finally:
            correlation_id_var.reset(token)


class TestBoundaries:
    """The bus is transport. These assert it has not grown a second brain."""

    def test_bus_makes_no_safety_decisions(self):
        """No import of assurance, policy, services or the LLM layer from the bus."""
        source = pathlib.Path(__file__).resolve().parents[3] / "app" / "events" / "bus.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        forbidden = ("app.assurance", "app.policy", "app.services", "app.llm", "app.agents")
        offenders = {m for m in imported if m.startswith(forbidden)}
        assert not offenders, f"the event bus must stay transport-only; it imports {offenders}"

    def test_bus_does_not_add_fields_to_an_event(self):
        """Round-tripping must not introduce a field the contract does not define."""
        event = a_high_risk_delay()
        assert set(decode(encode(event)).model_dump()) == set(event.model_dump())

    def test_no_confidence_field_travels_on_the_wire(self):
        """Rule 7: no LLM self-report ever reaches a control-flow path."""
        for event in ONE_OF_EVERY_EVENT:
            payload = encode(event)["payload"]
            assert "confidence" not in payload, f"{event.event_type} carries a confidence value"


# ------------------------------------------------------------------------------ helpers


def _collector(sink: list) -> object:
    async def handler(event) -> None:
        sink.append(event)

    return handler


def _raiser(sink: list | None = None) -> object:
    async def handler(event) -> None:
        if sink is not None:
            sink.append(event)
        raise RuntimeError("handler blew up")

    return handler


def _fail_n_times(failures: int, sink: list) -> object:
    async def handler(event) -> None:
        sink.append(event)
        if len(sink) <= failures:
            raise RuntimeError("transient failure")

    return handler

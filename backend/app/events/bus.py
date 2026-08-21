"""Redis Streams event bus — STREAM A.

TRANSPORT AND DELIVERY RELIABILITY ONLY.

This module moves the typed events defined in `app/events/types.py` from producers to
consumers and keeps that delivery honest across a crash. Its remit is publish, consume,
acknowledge, retry, dead-letter, duplicate suppression and correlation propagation.

It deliberately does **not**:

* decide whether an action is permitted — that is the Decision Assurance Gate
  (`app/assurance/`), and there is no other authorisation path;
* decide what happens next in an incident — that is `app/orchestrator/`;
* interpret, enrich or repair an event payload — that belongs to the handler;
* add a field to an event — `app/events/types.py` is a cross-stream contract.

A transport that makes safety decisions becomes a second, invisible policy engine. This
one routes bytes and counts attempts.

## Delivery semantics: at least once

Exactly-once delivery is **not** provided and is not claimed. Handlers are invoked at
least once per event and must tolerate being invoked again.

The bus suppresses duplicates per consumer group by `event_id`, in this order:

    check seen -> run handlers -> mark seen -> acknowledge

That closes redelivery after a handler failure and after a consumer crash mid-batch. One
window stays open by construction: a crash *between* a handler succeeding and the seen
marker being written replays that handler on the next pass. Nothing a message broker can
do closes that gap, because the handler's side effect and the broker's bookkeeping live in
different systems.

So the marker is a cheap first line of defence, not the guarantee. The durable guarantee
is the handler's own: the idempotency key on every mutation and the unique constraints in
the schema. Both remain load-bearing.

## Failure path

A handler that raises is not acknowledged, so the entry stays in the group's pending
entries list and is reclaimed by `reclaim_stale()` once it has been idle long enough.
After `max_delivery_attempts` deliveries it is moved to the dead-letter stream and
acknowledged, so a poison event cannot become an infinite redelivery loop. An event whose
payload does not validate is dead-lettered on first sight: re-reading identical bytes
cannot produce a different result.

Owner: Stream A.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable

from pydantic import TypeAdapter, ValidationError
from redis.exceptions import RedisError, ResponseError

from app.config import Settings, get_settings
from app.errors import ProviderUnavailable, ValidationFailed
from app.events.types import STREAM_NAME, DomainEvent, EventType
from app.observability.logging import correlation_id_var, get_logger

log = get_logger(__name__)

#: Poison and exhausted-retry entries land here. Never consumed automatically; an
#: operator inspects it with `XRANGE`.
DEAD_LETTER_STREAM = f"{STREAM_NAME}.dead"

#: Deliveries attempted before an entry is dead-lettered. Bounded on purpose.
DEFAULT_MAX_DELIVERY_ATTEMPTS = 5

#: How long an entry must sit unacknowledged before another consumer may reclaim it.
DEFAULT_RECLAIM_IDLE_MS = 30_000

#: Duplicate-suppression markers outlive any plausible redelivery, then expire so the
#: keyspace does not grow without bound.
DEFAULT_SEEN_TTL_SECONDS = 7 * 24 * 3600

DEFAULT_DEDUPE_TTL_SECONDS = 24 * 3600

#: Where a newly created consumer group starts reading.
#:
#: "0" = the beginning of the stream, so a group created after its events were published
#: still receives them. The Redis default of "$" skips the backlog, which means an event
#: published in the window before a consumer first starts is dropped without a trace — the
#: exact failure this system must not have.
#:
#: The replay this permits is safe precisely because duplicate suppression exists: a group
#: re-reading history handles each event once. Losing an event is unrecoverable; handling
#: an old one again is not.
DEFAULT_GROUP_START_ID = "0"

# Stream entries are flat string maps. The whole event travels as JSON in one field; the
# rest are flat copies so `XRANGE` and log correlation work without parsing the payload.
FIELD_PAYLOAD = "payload"
FIELD_EVENT_TYPE = "event_type"
FIELD_EVENT_ID = "event_id"
FIELD_SCHEMA_VERSION = "schema_version"
FIELD_CORRELATION_ID = "correlation_id"
FIELD_INCIDENT_ID = "incident_id"

FIELD_DEAD_LETTER_REASON = "dead_letter_reason"
FIELD_DEAD_LETTER_GROUP = "dead_letter_group"
FIELD_DEAD_LETTER_AT = "dead_letter_at"
FIELD_DELIVERY_ATTEMPTS = "delivery_attempts"

_EVENT_ADAPTER: TypeAdapter[Any] = TypeAdapter(DomainEvent)

Handler = Callable[[Any], Awaitable[None]]


# --------------------------------------------------------------------------- wire format


def _text(value: Any) -> str:
    """Redis returns bytes unless the client decodes responses. Tolerate both."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def encode(event: Any) -> dict[str, str]:
    """Render one typed event as a flat Redis stream entry."""
    fields = {
        FIELD_PAYLOAD: event.model_dump_json(),
        FIELD_EVENT_TYPE: str(event.event_type),
        FIELD_EVENT_ID: event.event_id,
        FIELD_SCHEMA_VERSION: event.schema_version,
    }
    if event.correlation_id:
        fields[FIELD_CORRELATION_ID] = event.correlation_id
    if event.incident_id is not None:
        fields[FIELD_INCIDENT_ID] = str(event.incident_id)
    return fields


def decode(fields: Mapping[Any, Any]) -> Any:
    """Rebuild the typed event, or raise ValidationFailed.

    Discrimination is by the `event_type` inside the payload, so an entry can never be
    coerced into the wrong event class by a mismatched flat field.
    """
    flat = {_text(key): _text(value) for key, value in fields.items()}
    raw = flat.get(FIELD_PAYLOAD)
    if raw is None:
        raise ValidationFailed(
            "stream entry has no payload field",
            details={"fields": sorted(flat), "event_id": flat.get(FIELD_EVENT_ID)},
        )
    try:
        return _EVENT_ADAPTER.validate_json(raw)
    except ValidationError as exc:
        # The payload itself is never logged or echoed: it may carry operational detail.
        raise ValidationFailed(
            "event payload does not match any typed event contract",
            details={
                "event_id": flat.get(FIELD_EVENT_ID),
                "event_type": flat.get(FIELD_EVENT_TYPE),
                "schema_version": flat.get(FIELD_SCHEMA_VERSION),
                "error_count": exc.error_count(),
            },
        ) from exc


# ------------------------------------------------------------------- duplicate suppression


@runtime_checkable
class ProcessedRegistry(Protocol):
    """Records which events a consumer group has already handled."""

    async def seen(self, group: str, event_id: str) -> bool: ...

    async def mark(self, group: str, event_id: str) -> None: ...


class RedisProcessedRegistry:
    """Redis-backed markers, scoped per consumer group.

    Scoping by group is deliberate: two groups are two independent subscribers, and each
    must see every event once. A global marker would silently starve the second one.
    """

    def __init__(self, client: StreamClient, *, ttl_seconds: int = DEFAULT_SEEN_TTL_SECONDS):
        self._client = client
        self._ttl = ttl_seconds

    def _key(self, group: str, event_id: str) -> str:
        return f"{STREAM_NAME}:seen:{group}:{event_id}"

    async def seen(self, group: str, event_id: str) -> bool:
        return bool(await self._client.exists(self._key(group, event_id)))

    async def mark(self, group: str, event_id: str) -> None:
        await self._client.set(self._key(group, event_id), "1", ex=self._ttl)


class InMemoryProcessedRegistry:
    """Single-process registry. Tests and single-process workers only."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()

    async def seen(self, group: str, event_id: str) -> bool:
        return (group, event_id) in self._seen

    async def mark(self, group: str, event_id: str) -> None:
        self._seen.add((group, event_id))


# ----------------------------------------------------------------------------- dispatch


class Outcome(StrEnum):
    """What the bus did with one delivery. Transport outcomes, not business outcomes."""

    handled = "handled"
    duplicate = "duplicate"
    ignored = "ignored"
    failed = "failed"
    dead_lettered = "dead_lettered"


@dataclass
class ConsumeReport:
    """Counts for one consume pass. Feeds metrics; carries no decision."""

    counts: MutableMapping[Outcome, int] = field(default_factory=dict)

    def record(self, outcome: Outcome) -> None:
        self.counts[outcome] = self.counts.get(outcome, 0) + 1

    def merge(self, other: ConsumeReport) -> None:
        for outcome, value in other.counts.items():
            self.counts[outcome] = self.counts.get(outcome, 0) + value

    def get(self, outcome: Outcome) -> int:
        return self.counts.get(outcome, 0)

    @property
    def read(self) -> int:
        return sum(self.counts.values())

    def to_dict(self) -> dict[str, int]:
        return {outcome.value: count for outcome, count in sorted(self.counts.items())}


class _Dispatcher:
    """Event type -> handlers. A wildcard handler receives every event."""

    def __init__(self) -> None:
        self._by_type: dict[EventType, list[Handler]] = {}
        self._wildcard: list[Handler] = []

    def subscribe(self, event_type: EventType | None, handler: Handler) -> None:
        if event_type is None:
            self._wildcard.append(handler)
        else:
            self._by_type.setdefault(event_type, []).append(handler)

    def handlers_for(self, event_type: EventType) -> list[Handler]:
        return [*self._by_type.get(event_type, []), *self._wildcard]


# ------------------------------------------------------------------------- client surface


@runtime_checkable
class StreamClient(Protocol):
    """The Redis commands this bus uses, and nothing more.

    Narrowing the surface keeps the bus testable without a server and makes the
    dependency on Redis explicit rather than ambient.
    """

    async def xadd(self, name: str, fields: Mapping[str, str]) -> Any: ...

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",  # noqa: A002 - mirrors the redis-py command signature
        mkstream: bool = False,
    ) -> Any: ...

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: Mapping[str, str],
        count: int | None = None,
        block: int | None = None,
    ) -> Any: ...

    async def xack(self, name: str, groupname: str, *ids: str) -> Any: ...

    async def xpending_range(
        self,
        name: str,
        groupname: str,
        start: str,
        end: str,
        count: int,
        consumername: str | None = None,
        idle: int | None = None,
    ) -> Any: ...

    async def xclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        message_ids: Sequence[str],
    ) -> Any: ...

    async def xrange(self, name: str, start: str = "-", end: str = "+") -> Any: ...

    async def exists(self, *names: str) -> Any: ...

    async def set(self, name: str, value: str, ex: int | None = None, nx: bool = False) -> Any: ...


# ---------------------------------------------------------------------------------- bus


class EventBus(Protocol):
    """What producers and consumers depend on."""

    async def publish(self, event: Any, *, dedupe_key: str | None = None) -> str | None: ...

    def subscribe(self, event_type: EventType | None, handler: Handler) -> None: ...

    async def consume_once(
        self, *, group: str, consumer: str, count: int = 10, block_ms: int = 1000
    ) -> ConsumeReport: ...


class RedisEventBus:
    """Redis Streams implementation.

    Constructed with an explicit client so a test can drive it without a server. Never
    substituted automatically: if Redis is unavailable the caller gets
    ProviderUnavailable, because a silently in-process bus would drop every consumer in
    another process while looking perfectly healthy.
    """

    def __init__(
        self,
        client: StreamClient,
        *,
        stream: str = STREAM_NAME,
        dead_letter_stream: str = DEAD_LETTER_STREAM,
        registry: ProcessedRegistry | None = None,
        max_delivery_attempts: int = DEFAULT_MAX_DELIVERY_ATTEMPTS,
        dedupe_ttl_seconds: int = DEFAULT_DEDUPE_TTL_SECONDS,
        group_start_id: str = DEFAULT_GROUP_START_ID,
        producer: str = "travelops-api",
    ) -> None:
        self._client = client
        self._stream = stream
        self._dead_letter_stream = dead_letter_stream
        self._registry = registry or RedisProcessedRegistry(client)
        self._max_delivery_attempts = max_delivery_attempts
        self._dedupe_ttl = dedupe_ttl_seconds
        self._group_start_id = group_start_id
        self._producer = producer
        self._dispatcher = _Dispatcher()
        self._groups_ready: set[str] = set()

    # ------------------------------------------------------------------ registration

    def subscribe(self, event_type: EventType | None, handler: Handler) -> None:
        """Register a handler. `event_type=None` receives every event."""
        self._dispatcher.subscribe(event_type, handler)

    # ------------------------------------------------------------------------ publish

    async def publish(self, event: Any, *, dedupe_key: str | None = None) -> str | None:
        """Append one event to the stream. Returns the entry ID.

        `dedupe_key` lets a producer declare its own emit-once rule — for example one
        HIGH_RISK_DELAY per flight and rule version. When the key has already been used
        the publish is skipped and None is returned. The bus stores the key; it does not
        decide what a key should be, because that is a domain rule belonging to the
        producer.
        """
        if dedupe_key is not None:
            key = f"{self._stream}:dedupe:{dedupe_key}"
            first = await self._guard(
                "publish_dedupe", self._client.set(key, "1", ex=self._dedupe_ttl, nx=True)
            )
            if not first:
                log.info(
                    "event_publish_suppressed",
                    event_type=str(event.event_type),
                    event_id=event.event_id,
                    dedupe_key=dedupe_key,
                    outcome="duplicate",
                )
                return None

        entry_id = _text(
            await self._guard("publish", self._client.xadd(self._stream, encode(event)))
        )
        log.info(
            "event_published",
            event_type=str(event.event_type),
            event_id=event.event_id,
            stream_entry_id=entry_id,
            outcome="success",
        )
        return entry_id

    # ------------------------------------------------------------------------ consume

    async def ensure_group(self, group: str) -> None:
        """Create the consumer group if absent. Idempotent.

        Starts at `group_start_id`, which defaults to the beginning of the stream rather
        than Redis's `$`. See DEFAULT_GROUP_START_ID for why skipping the backlog is the
        wrong default here.
        """
        if group in self._groups_ready:
            return
        try:
            await self._client.xgroup_create(
                self._stream, group, id=self._group_start_id, mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise ProviderUnavailable(
                    "could not create consumer group",
                    details={"stream": self._stream, "group": group, "detail": str(exc)},
                ) from exc
        except RedisError as exc:
            raise ProviderUnavailable(
                "event bus unavailable while creating consumer group",
                details={"stream": self._stream, "group": group, "detail": type(exc).__name__},
            ) from exc
        self._groups_ready.add(group)

    async def consume_once(
        self,
        *,
        group: str,
        consumer: str,
        count: int = 10,
        block_ms: int = 1000,
        reclaim_idle_ms: int | None = None,
    ) -> ConsumeReport:
        """Read and handle up to `count` new entries.

        When `reclaim_idle_ms` is set, entries abandoned by a crashed consumer are
        reclaimed first so recovery does not wait for new traffic.
        """
        await self.ensure_group(group)
        report = ConsumeReport()

        if reclaim_idle_ms is not None:
            report.merge(
                await self.reclaim_stale(
                    group=group, consumer=consumer, min_idle_ms=reclaim_idle_ms, count=count
                )
            )

        response = await self._guard(
            "consume",
            self._client.xreadgroup(
                group, consumer, {self._stream: ">"}, count=count, block=block_ms
            ),
        )
        for entries in _iter_stream_entries(response):
            for entry_id, fields in entries:
                report.record(
                    await self._process(group=group, entry_id=_text(entry_id), fields=fields)
                )
        return report

    async def reclaim_stale(
        self,
        *,
        group: str,
        consumer: str,
        min_idle_ms: int = DEFAULT_RECLAIM_IDLE_MS,
        count: int = 10,
    ) -> ConsumeReport:
        """Retry entries left pending, and dead-letter those past the attempt cap."""
        report = ConsumeReport()
        pending = (
            await self._guard(
                "reclaim_pending",
                self._client.xpending_range(self._stream, group, "-", "+", count, idle=min_idle_ms),
            )
            or []
        )
        if not pending:
            return report

        exhausted: list[tuple[str, int]] = []
        retryable: list[str] = []
        for item in pending:
            entry_id = _text(item["message_id"])
            attempts = int(item.get("times_delivered", 1))
            if attempts >= self._max_delivery_attempts:
                exhausted.append((entry_id, attempts))
            else:
                retryable.append(entry_id)

        for entry_id, attempts in exhausted:
            rows = (
                await self._guard(
                    "reclaim_lookup", self._client.xrange(self._stream, entry_id, entry_id)
                )
                or []
            )
            fields = rows[0][1] if rows else {}
            await self._dead_letter(
                group=group,
                entry_id=entry_id,
                fields=fields,
                reason="max_delivery_attempts_exceeded",
                attempts=attempts,
            )
            report.record(Outcome.dead_lettered)

        if retryable:
            claimed = (
                await self._guard(
                    "reclaim_claim",
                    self._client.xclaim(self._stream, group, consumer, min_idle_ms, retryable),
                )
                or []
            )
            for entry_id, fields in claimed:
                report.record(
                    await self._process(group=group, entry_id=_text(entry_id), fields=fields)
                )
        return report

    async def run(
        self,
        *,
        group: str,
        consumer: str,
        stop_event: asyncio.Event | None = None,
        count: int = 10,
        block_ms: int = 1000,
        reclaim_idle_ms: int = DEFAULT_RECLAIM_IDLE_MS,
        backoff_seconds: float = 1.0,
    ) -> None:
        """Consume until `stop_event` is set.

        A transport outage is logged at error level and retried after a backoff — a
        worker should survive a Redis restart. It is never downgraded to a success and
        never silently swallowed.
        """
        while stop_event is None or not stop_event.is_set():
            try:
                report = await self.consume_once(
                    group=group,
                    consumer=consumer,
                    count=count,
                    block_ms=block_ms,
                    reclaim_idle_ms=reclaim_idle_ms,
                )
            except ProviderUnavailable as exc:
                log.error(
                    "event_bus_unavailable",
                    outcome="error",
                    error_code=exc.code,
                    group=group,
                    consumer=consumer,
                    detail=exc.message,
                )
                await asyncio.sleep(backoff_seconds)
                continue
            if report.read:
                log.info("event_batch_consumed", group=group, consumer=consumer, **report.to_dict())

    # -------------------------------------------------------------------- one delivery

    async def _process(self, *, group: str, entry_id: str, fields: Mapping[Any, Any]) -> Outcome:
        try:
            event = decode(fields)
        except ValidationFailed as exc:
            # Unparseable bytes cannot become parseable on retry.
            log.error(
                "event_undecodable",
                outcome="error",
                error_code=exc.code,
                group=group,
                stream_entry_id=entry_id,
                **{k: v for k, v in exc.details.items() if k != "fields"},
            )
            await self._dead_letter(
                group=group,
                entry_id=entry_id,
                fields=fields,
                reason="payload_validation_failed",
                attempts=1,
            )
            return Outcome.dead_lettered

        handlers = self._dispatcher.handlers_for(event.event_type)
        if not handlers:
            # Not an error: another consumer group may be the intended subscriber.
            await self._ack(group, entry_id)
            return Outcome.ignored

        if await self._registry.seen(group, event.event_id):
            log.info(
                "event_duplicate_skipped",
                group=group,
                event_type=str(event.event_type),
                event_id=event.event_id,
                stream_entry_id=entry_id,
                outcome="duplicate",
            )
            await self._ack(group, entry_id)
            return Outcome.duplicate

        # Every log line and every downstream decision inherits the originating ID.
        token = correlation_id_var.set(event.correlation_id or correlation_id_var.get())
        started = time.monotonic()
        try:
            for handler in handlers:
                await handler(event)
        except Exception as exc:
            # Left unacknowledged on purpose: it stays pending and is reclaimed later.
            log.error(
                "event_handler_failed",
                outcome="error",
                group=group,
                event_type=str(event.event_type),
                event_id=event.event_id,
                stream_entry_id=entry_id,
                error_code=getattr(exc, "code", type(exc).__name__),
                detail=str(exc),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return Outcome.failed
        finally:
            correlation_id_var.reset(token)

        # Ordering: mark first, then acknowledge. A crash between the two costs one
        # redelivery that the marker then suppresses. The reverse order would lose the
        # suppression entirely.
        await self._registry.mark(group, event.event_id)
        await self._ack(group, entry_id)
        log.info(
            "event_handled",
            group=group,
            event_type=str(event.event_type),
            event_id=event.event_id,
            stream_entry_id=entry_id,
            handlers=len(handlers),
            duration_ms=int((time.monotonic() - started) * 1000),
            outcome="success",
        )
        return Outcome.handled

    async def _ack(self, group: str, entry_id: str) -> None:
        await self._guard("ack", self._client.xack(self._stream, group, entry_id))

    async def _dead_letter(
        self,
        *,
        group: str,
        entry_id: str,
        fields: Mapping[Any, Any],
        reason: str,
        attempts: int,
    ) -> None:
        payload = {_text(key): _text(value) for key, value in fields.items()}
        payload[FIELD_DEAD_LETTER_REASON] = reason
        payload[FIELD_DEAD_LETTER_GROUP] = group
        payload[FIELD_DEAD_LETTER_AT] = datetime.now(UTC).isoformat()
        payload[FIELD_DELIVERY_ATTEMPTS] = str(attempts)
        await self._guard("dead_letter", self._client.xadd(self._dead_letter_stream, payload))
        await self._ack(group, entry_id)
        log.error(
            "event_dead_lettered",
            outcome="error",
            group=group,
            stream_entry_id=entry_id,
            dead_letter_stream=self._dead_letter_stream,
            reason=reason,
            delivery_attempts=attempts,
            event_id=payload.get(FIELD_EVENT_ID),
            event_type=payload.get(FIELD_EVENT_TYPE),
        )

    async def _guard(self, operation: str, awaitable: Any) -> Any:
        """Surface a transport failure as ProviderUnavailable, never as a quiet no-op."""
        try:
            return await awaitable
        except RedisError as exc:
            raise ProviderUnavailable(
                "event bus unavailable",
                details={
                    "operation": operation,
                    "stream": self._stream,
                    "detail": type(exc).__name__,
                },
            ) from exc


def _iter_stream_entries(response: Any) -> list[list[tuple[Any, Mapping[Any, Any]]]]:
    """Normalise XREADGROUP's reply, which is empty as None on timeout."""
    if not response:
        return []
    if isinstance(response, Mapping):
        return list(response.values())
    return [entries for _stream, entries in response]


# ------------------------------------------------------------------------------ factory

_bus: RedisEventBus | None = None


@lru_cache(maxsize=1)
def _redis_client(redis_url: str) -> Any:
    from redis.asyncio import from_url

    return from_url(redis_url, decode_responses=True)


def get_event_bus(settings: Settings | None = None) -> RedisEventBus:
    """The process-wide bus. Always Redis-backed.

    There is no automatic in-memory fallback: a bus that quietly stops crossing process
    boundaries is worse than one that reports it cannot reach Redis.
    """
    global _bus
    if _bus is None:
        cfg = settings or get_settings()
        _bus = RedisEventBus(_redis_client(cfg.redis_url))
    return _bus


async def dispose_event_bus() -> None:
    global _bus
    if _bus is not None:
        client = _bus._client
        close = getattr(client, "aclose", None)
        if close is not None:
            await close()
    _bus = None
    _redis_client.cache_clear()

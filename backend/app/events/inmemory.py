"""In-process implementation of the stream commands the bus uses — STREAM A.

Why this exists: `RedisEventBus` should be exercised by tests as-is, rather than tested
through a parallel implementation that could drift from it. Substituting the client
instead of the bus means the retry, acknowledgement, dead-letter and duplicate-suppression
logic under test is exactly the logic that runs in production.

It implements consumer-group semantics faithfully enough for that: a per-group delivery
cursor, a pending entries list, delivery counts, and idle-time-based reclaim.

**Not a transport.** It holds everything in one process's memory, so it is for tests and
for a genuinely single-process run only. `get_event_bus()` never selects it — see the note
there on why an automatic in-memory fallback would be dishonest.

`clock` is injectable so a test can age an entry past the reclaim threshold without
sleeping.

Owner: Stream A.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from redis.exceptions import ResponseError

from app.events.bus import DEAD_LETTER_STREAM, InMemoryProcessedRegistry, RedisEventBus
from app.events.types import STREAM_NAME


@dataclass
class _Pending:
    consumer: str
    delivered_at_ms: float
    times_delivered: int = 1


@dataclass
class _Group:
    cursor: int = 0
    pending: dict[str, _Pending] = field(default_factory=dict)


class InMemoryStreamClient:
    """Minimal, faithful stand-in for the Redis commands in `bus.StreamClient`."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self._groups: dict[tuple[str, str], _Group] = {}
        self._kv: dict[str, str] = {}
        self._sequence = 0

    # ---------------------------------------------------------------- test helpers

    def now_ms(self) -> float:
        return self._clock() * 1000

    def entries(self, name: str = STREAM_NAME) -> list[tuple[str, dict[str, str]]]:
        return list(self._streams.get(name, []))

    def dead_letters(self) -> list[dict[str, str]]:
        return [fields for _entry_id, fields in self.entries(DEAD_LETTER_STREAM)]

    def pending_ids(self, name: str, groupname: str) -> list[str]:
        group = self._groups.get((name, groupname))
        return sorted(group.pending) if group else []

    # -------------------------------------------------------------------- commands

    async def xadd(self, name: str, fields: Mapping[str, str]) -> str:
        self._sequence += 1
        entry_id = f"{self._sequence}-0"
        self._streams.setdefault(name, []).append((entry_id, dict(fields)))
        return entry_id

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "$",  # noqa: A002 - mirrors the redis-py command signature
        mkstream: bool = False,
    ) -> bool:
        if (name, groupname) in self._groups:
            raise ResponseError("BUSYGROUP Consumer Group name already exists")
        if mkstream:
            self._streams.setdefault(name, [])
        elif name not in self._streams:
            raise ResponseError("NOGROUP No such key")
        # Matches Redis: "$" starts after the current tail, "0" replays the backlog.
        cursor = len(self._streams.get(name, [])) if id == "$" else 0
        self._groups[(name, groupname)] = _Group(cursor=cursor)
        return True

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: Mapping[str, str],
        count: int | None = None,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        result: list[tuple[str, list[tuple[str, dict[str, str]]]]] = []
        for name in streams:
            group = self._groups.get((name, groupname))
            if group is None:
                raise ResponseError("NOGROUP No such consumer group")
            entries = self._streams.get(name, [])
            batch = entries[group.cursor : group.cursor + (count or len(entries))]
            group.cursor += len(batch)
            for entry_id, _fields in batch:
                group.pending[entry_id] = _Pending(
                    consumer=consumername, delivered_at_ms=self.now_ms()
                )
            if batch:
                result.append((name, [(eid, dict(f)) for eid, f in batch]))
        return result

    async def xack(self, name: str, groupname: str, *ids: str) -> int:
        group = self._groups.get((name, groupname))
        if group is None:
            return 0
        return sum(group.pending.pop(entry_id, None) is not None for entry_id in ids)

    async def xpending_range(
        self,
        name: str,
        groupname: str,
        start: str,
        end: str,
        count: int,
        consumername: str | None = None,
        idle: int | None = None,
    ) -> list[dict[str, Any]]:
        group = self._groups.get((name, groupname))
        if group is None:
            return []
        now = self.now_ms()
        rows: list[dict[str, Any]] = []
        for entry_id, pending in sorted(group.pending.items()):
            elapsed = now - pending.delivered_at_ms
            if idle is not None and elapsed < idle:
                continue
            if consumername is not None and pending.consumer != consumername:
                continue
            rows.append(
                {
                    "message_id": entry_id,
                    "consumer": pending.consumer,
                    "time_since_delivered": int(elapsed),
                    "times_delivered": pending.times_delivered,
                }
            )
        return rows[:count]

    async def xclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        message_ids: Sequence[str],
    ) -> list[tuple[str, dict[str, str]]]:
        group = self._groups.get((name, groupname))
        if group is None:
            return []
        now = self.now_ms()
        by_id = dict(self._streams.get(name, []))
        claimed: list[tuple[str, dict[str, str]]] = []
        for entry_id in message_ids:
            pending = group.pending.get(entry_id)
            if pending is None or (now - pending.delivered_at_ms) < min_idle_time:
                continue
            pending.consumer = consumername
            pending.delivered_at_ms = now
            pending.times_delivered += 1
            if entry_id in by_id:
                claimed.append((entry_id, dict(by_id[entry_id])))
        return claimed

    async def xrange(
        self, name: str, start: str = "-", end: str = "+"
    ) -> list[tuple[str, dict[str, str]]]:
        entries = self._streams.get(name, [])
        if start == "-" and end == "+":
            return [(eid, dict(f)) for eid, f in entries]
        return [(eid, dict(f)) for eid, f in entries if start <= eid <= end]

    async def exists(self, *names: str) -> int:
        return sum(name in self._kv for name in names)

    async def set(
        self, name: str, value: str, ex: int | None = None, nx: bool = False
    ) -> bool | None:
        if nx and name in self._kv:
            return None
        self._kv[name] = value
        return True

    async def aclose(self) -> None:
        return None


def in_memory_bus(
    *, clock: Callable[[], float] | None = None, **kwargs: Any
) -> tuple[RedisEventBus, InMemoryStreamClient]:
    """Build a bus over an in-memory client. Tests and single-process runs only."""
    client = InMemoryStreamClient(clock=clock)
    bus = RedisEventBus(client, registry=InMemoryProcessedRegistry(), **kwargs)
    return bus, client

"""Deterministic identity for a jurisdiction resolution — STREAM C.

`policy_applicability.resolver_hash` has existed as a column since the initial migration with
nothing producing it. This is that producer, and it is the only one — Phase 4 G5 in
`docs/38-phase4-verified-policy.md`.

## What the hash is for

`resolver_version` records *which resolver* decided; `resolver_hash` records *which decision*.
Without it a persisted applicability row cannot be told apart from a later row produced by
different facts against a re-authored pack, so an entitlement pinned to that row is not
replayable. `plan.plan_hash` does the same job for a plan's task set, and this module
deliberately mirrors `plan_identity.py` — same canonical-JSON approach, same digest, same
length — so the data layer has one hashing convention rather than two.

## What participates, and why

* **`resolver_version`** — Stream B's `RESOLVER_VERSION`. A change to the resolver's own logic
  must change every hash it produces, because the same facts may now resolve differently.
* **Each candidate's `pack_hash`** — computed by B's loader over `pack.yaml`,
  `applicability.yaml`, `rules.yaml` and `review.yaml`. This is what makes "any change to the
  resolver's *rules* changes the hash" true without re-hashing pack files here: editing an
  applicability condition changes `pack_hash`, which changes this hash.
* **The facts the resolution was decided on** — the declared required facts read from the trip
  context, plus the `basis` the resolver recorded for an applicable pack. Changing an input the
  resolution depended on changes the hash.
* **The outcome** — decision, selected packs, conflicts, blocking reasons, missing facts, and
  each candidate's tri-state status.

## What deliberately does not participate

Row ids, timestamps, and the order the caller happened to pass packs in. Candidates are sorted
by `(pack_id, pack_version)` because pack iteration order is a caller detail, not a resolver
input: two callers resolving the same trip against the same packs must agree. This is the same
reasoning behind `plan_identity`'s exclusion of ids and timestamps — a hash that moves when
nothing meaningful moved is a hash nobody can rely on.

Owner: Stream C. Consumes Stream B's `Resolution` unchanged; adds no second resolver and no
second representation of one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

#: Bumped only when the hashed document shape changes. A different version means older hashes
#: are not comparable, which is the honest outcome rather than something to paper over.
RESOLVER_IDENTITY_VERSION: Final = "resolver-identity-v1"

#: 32 hex characters, matching `plan_identity.HASH_LENGTH`. `policy_applicability.resolver_hash`
#: is `String(64)`, so this fits with room to spare.
HASH_LENGTH: Final = 32

_MISSING: Final = object()


def _read(facts: Any, path: str) -> Any:
    """Read a dotted fact path, or `_MISSING`.

    Mirrors `app.policy.engine._lookup` on purpose. It reads and nothing else: it evaluates no
    condition and decides no applicability, so it cannot drift into being a second resolver.
    Kept here rather than importing a private helper across a stream boundary.
    """
    current: Any = facts
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return _MISSING
        current = current[segment]
    return current


def _status_of(candidate: Any) -> str:
    status = getattr(candidate, "status", None)
    return str(getattr(status, "value", status))


def _strings(values: Any) -> list[str]:
    return [str(item) for item in values or []]


def consulted_facts(*, trip_context: Mapping[str, Any], paths: Sequence[str]) -> dict[str, Any]:
    """The declared required facts that are actually present, as `{path: value}`.

    An absent fact is omitted rather than recorded as null, for the same reason
    `app/db/trip_context.py` prunes nulls: "absent" and "explicitly null" must not become the
    same input. Which paths were absent is recorded separately by the resolver in
    `missing_facts`, so nothing is lost.
    """
    present: dict[str, Any] = {}
    for path in paths:
        value = _read(trip_context, path)
        if value is _MISSING or value is None:
            continue
        present[str(path)] = value
    return present


def canonical_resolution(
    *,
    resolution: Any,
    trip_context: Mapping[str, Any] | None = None,
    packs: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """The exact document that gets hashed. Exposed so a test can diff it rather than guess.

    Sorted where order is a caller detail; left as authored where the resolver's own ordering
    carries meaning — `blocking_reasons` is emitted most-specific-first by `resolver.select`,
    and flattening that to a set would discard information.
    """
    facts = trip_context or {}
    pack_hashes = {
        str(getattr(pack, "pack_id", "")): str(getattr(pack, "pack_hash", ""))
        for pack in packs or []
    }
    candidates = sorted(
        getattr(resolution, "candidates", None) or [],
        key=lambda candidate: (
            str(getattr(candidate, "pack_id", "")),
            str(getattr(candidate, "pack_version", "")),
        ),
    )

    return {
        "version": RESOLVER_IDENTITY_VERSION,
        "resolver_version": str(getattr(resolution, "resolver_version", "")),
        "decision": str(getattr(resolution, "decision", "")),
        "selected": sorted(_strings(getattr(resolution, "selected", None))),
        "conflicts": sorted(_strings(getattr(resolution, "conflicts", None))),
        "blocking_reasons": _strings(getattr(resolution, "blocking_reasons", None)),
        "missing_facts": sorted(_strings(getattr(resolution, "missing_facts", None))),
        "candidates": [
            {
                "pack_id": str(getattr(candidate, "pack_id", "")),
                "pack_version": str(getattr(candidate, "pack_version", "")),
                # Pinned to the pack's own hash, so an edited applicability rule and an edited
                # entitlement rule both reach this hash without reading pack files here.
                "pack_hash": pack_hashes.get(str(getattr(candidate, "pack_id", "")), ""),
                "status": _status_of(candidate),
                "basis": dict(getattr(candidate, "basis", None) or {}),
                "required_facts": _strings(getattr(candidate, "required_facts", None)),
                "missing_facts": _strings(getattr(candidate, "missing_facts", None)),
                "consulted_facts": consulted_facts(
                    trip_context=facts,
                    paths=_strings(getattr(candidate, "required_facts", None)),
                ),
            }
            for candidate in candidates
        ],
    }


def compute_resolver_hash(
    *,
    resolution: Any,
    trip_context: Mapping[str, Any] | None = None,
    packs: Sequence[Any] | None = None,
) -> str:
    """Hash one resolution. Returns 32 lowercase hex characters.

    `packs` supplies each candidate's `pack_hash`. It is optional only so a caller holding no
    pack objects still gets a stable hash; omitting it produces a weaker identity, which is why
    `policy_ingest.record_resolution` always passes the packs it resolved against.
    """
    document = canonical_resolution(resolution=resolution, trip_context=trip_context, packs=packs)
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()[:HASH_LENGTH]

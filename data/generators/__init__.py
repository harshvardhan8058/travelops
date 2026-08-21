"""Deterministic synthetic generators, fixed seed 20260807.

Two different kinds of determinism live here, and the distinction matters:

* The **cascade** (`cascade_spec`) is a *deliberate construction*. It is not random at all.
  It is built backwards from the targets in `data/fixtures/bengaluru_storm.yaml` so that
  the nine crew pairings are structurally derivable rather than a lucky draw.
* The **surrounding network** — passengers, bookings, hotels, historical incidents — is
  seeded-random from `SEED`, because its job is to look like a real operating day.

Owner: Stream C.
"""

from __future__ import annotations

SEED = 20260807

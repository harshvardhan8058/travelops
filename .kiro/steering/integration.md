---
inclusion: always
---

# Integration protocol — mandatory from Phase 3

Phase 2 was built correctly and merged badly: four streams worked in parallel, each edited files
another stream owned, and the conflicts were discovered at the final merge. The register and the
seams are in [`docs/36-integration-protocol.md`](../../docs/36-integration-protocol.md); this is the
part that must be in every session's context.

## The rule

> **One shared file, one owner. Everyone else consumes the owner's interface instead of editing the
> file.** If two streams need to change the same file, stop and coordinate before editing it.

`docs/28-parallel-workstreams.md` already partitions write access by directory. That is necessary and
not sufficient: the failures happen at **seams** — a registry that must list another stream's
services, a config another stream's scenario must fit, a frontend type mirroring a backend schema.
Every seam has a named owner and a consumption mechanism in `docs/36`. Use it; do not invent a second
way in.

## Before implementation starts

1. **Enumerate the shared files and contracts** the work touches, and check each against the register.
2. **Lock the API and schema contracts first** — response models, DB columns, config keys. A contract
   agreed after implementation is a rewrite disguised as a merge.
3. **Rebase onto latest `main`** before starting anything significant.
4. **No duplicate implementations and no parallel seams.** If a capability exists, call it. Two
   registries, two hashes or two mappings of the same thing is a defect even when both work.

## During the phase

- **Integrate continuously, not at the end.** Land small, working slices against `main`.
- Keep the integration branch **continuously synchronised** with `main`.
- When another stream changes a shared contract, **rebase and resolve immediately**. Divergence is
  cheapest on the day it appears and most expensive the week it is discovered.
- Needing to edit a file you do not own is a **request to its owner**, not an edit. If you must edit
  it to unblock the demo, say so explicitly in the PR body and name the owner.

## Before the final phase PR

- Rebase onto latest `main`.
- Run the full suite: backend tests, ruff check and format, frontend typecheck/lint/tokens/format/
  test/build, and the verification scripts.
- **Verify the PR reports zero merge conflicts** before asking for review.
- A final integration PR contains **integration work only** — not a phase of accumulated
  cross-stream change.

## What this does not relax

Nothing about correctness. A shared file still may not be weakened to make a merge easier: the six
frozen guard tests stay frozen, fail-closed stays fail-closed, and "resolve the conflict" never means
"delete the other stream's assertion".

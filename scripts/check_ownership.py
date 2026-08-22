#!/usr/bin/env python3
"""Enforce shared-file ownership before a merge conflict can happen.

    make check-ownership STREAM=D            # what has my branch touched?
    python3 scripts/check_ownership.py --stream D --base origin/main
    python3 scripts/check_ownership.py --report-collisions   # the Phase 2 evidence
    python3 scripts/check_ownership.py --audit               # unowned / shadowed rules

Phase 2 accumulated its conflicts silently: Stream A edited six of Stream D's frontend
files, A and C both edited `models/workflow.py`, A and B both edited
`config/assurance.v2.yaml`, and none of it surfaced until the final merge — by which point
two parallel backend integrations existed and one had to be thrown away.

This makes that visible on the first commit instead of the last. It reads `OWNERS`, diffs the
branch against `main`, and reports any file the given stream does not own.

Exit codes:
    0  clean, or advisory-only
    2  the branch edits a file another stream owns
    3  usage or environment error

Owner: SHARED — changing the enforcement is itself a coordinated change.
"""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OWNERS_FILE = REPO_ROOT / "OWNERS"

#: An owner that anyone may edit without coordination.
FREE = "ANY"
#: An owner meaning "more than one stream needs this; agree before editing".
SHARED = "SHARED"

STREAMS = ("A", "B", "C", "D")


@dataclass(frozen=True)
class Rule:
    pattern: str
    owner: str
    line_no: int


def load_rules(path: Path = OWNERS_FILE) -> list[Rule]:
    if not path.is_file():
        raise SystemExit(f"{path} not found; ownership cannot be checked")
    rules: list[Rule] = []
    for line_no, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            raise SystemExit(f"{path}:{line_no}: expected '<glob> <owner>', got {raw!r}")
        pattern, owner = parts
        if owner not in (*STREAMS, SHARED, FREE):
            raise SystemExit(f"{path}:{line_no}: unknown owner {owner!r}")
        rules.append(Rule(pattern=pattern, owner=owner, line_no=line_no))
    return rules


def owner_of(path: str, rules: list[Rule]) -> Rule | None:
    """Last matching rule wins, so a specific path can carve out of a broad one."""
    found: Rule | None = None
    for rule in rules:
        if _matches(path, rule.pattern):
            found = rule
    return found


def _matches(path: str, pattern: str) -> bool:
    """gitignore-ish matching: `**` spans directories, `*` does not span a separator."""
    if pattern == "*":
        return True
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(prefix + "/")
    if "**" in pattern:
        return fnmatch.fnmatch(path, pattern.replace("**", "*"))
    # A trailing-directory pattern with no glob should still match its contents.
    if "*" not in pattern and "?" not in pattern:
        return path == pattern
    return fnmatch.fnmatch(path, pattern)


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def changed_files(base: str) -> list[str]:
    """Files this branch changes relative to the merge base with `base`."""
    merge_base = _git("merge-base", "HEAD", base).strip()
    if not merge_base:
        raise SystemExit(f"no merge base with {base}")
    tracked = _git("diff", "--name-only", f"{merge_base}..HEAD").split()
    # Uncommitted work counts: the point is to catch this BEFORE it is pushed.
    working = _git("status", "--porcelain").splitlines()
    untracked = [line[3:].strip() for line in working if line.strip()]
    return sorted({*tracked, *untracked})


def check(stream: str, base: str, *, strict: bool) -> int:
    rules = load_rules()
    files = [f for f in changed_files(base) if (REPO_ROOT / f).exists() or True]

    violations: list[tuple[str, str]] = []
    shared: list[str] = []
    owned = 0

    for path in files:
        rule = owner_of(path, rules)
        owner = rule.owner if rule else SHARED
        if owner in (stream, FREE):
            owned += 1
        elif owner == SHARED:
            shared.append(path)
        else:
            violations.append((path, owner))

    print(f"stream {stream}: {len(files)} changed file(s) against {base}")
    print(f"  owned by {stream}      {owned}")
    print(f"  SHARED (coordinate)  {len(shared)}")
    print(f"  owned by others      {len(violations)}")

    if shared:
        print("\nSHARED files — these need agreement recorded before merge, not after:")
        for path in shared:
            print(f"  ~ {path}")

    if violations:
        print("\nOWNERSHIP VIOLATIONS — consume the owner's interface instead of editing:")
        for path, owner in violations:
            print(f"  ! {path}  (owner: {owner})")
        print(
            "\nIf the interface genuinely needs to change, ask the owner to change it.\n"
            "A second edit to someone else's file is a second seam, and the last phase\n"
            "showed where that ends: two group orchestrators, one of them discarded."
        )
        return 2 if strict else 0

    if not shared:
        print("\nclean: every changed file is owned by this stream")
    return 0


def _stream_of_branch(name: str) -> str | None:
    for letter in STREAMS:
        if f"stream/{letter.lower()}/" in name:
            return letter
    return None


def report_collisions(base: str, depth: int) -> int:
    """Which files more than one stream has actually touched. The evidence, not an opinion.

    Attribution is by the MERGE COMMIT's source branch, not by a subject prefix. Prefixes are
    unreliable — Phase 2's largest Stream A commit was titled "Phase 2: full disruption
    intelligence, integrated end to end" with no `[A]` — and a detector that silently skips the
    biggest contributor reports "no collisions" for the phase that had the most.
    """
    by_file: dict[str, set[str]] = {}

    merges = _git("log", "--merges", "--format=%H\t%s", f"-{depth}", base).splitlines()
    for entry in merges:
        if "\t" not in entry:
            continue
        sha, subject = entry.split("\t", 1)
        stream = _stream_of_branch(subject)
        if stream is None:
            continue
        parents = _git("rev-list", "--parents", "-n", "1", sha).split()[1:]
        if len(parents) < 2:
            continue
        # From the MERGE BASE, not from the first parent. A branch that is behind main differs
        # from the trunk by everything main gained meanwhile, so `parents[0]..parents[1]` credits
        # a stream with every file another stream changed while it was away — which reports
        # "ABCD touched everything" and is useless. The merge base isolates what the branch
        # itself introduced.
        merge_base = _git("merge-base", parents[0], parents[1]).strip()
        if not merge_base:
            continue
        for path in _git("diff", "--name-only", f"{merge_base}..{parents[1]}").split():
            by_file.setdefault(path, set()).add(stream)

    # Direct commits that never went through a stream merge still count.
    for entry in _git("log", "--no-merges", "--format=%H\t%s", f"-{depth}", base).splitlines():
        if "\t" not in entry:
            continue
        sha, subject = entry.split("\t", 1)
        stream = next((letter for letter in STREAMS if subject.startswith(f"[{letter}]")), None)
        if stream is None:
            continue
        for path in _git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).split():
            by_file.setdefault(path, set()).add(stream)

    collisions = {path: streams for path, streams in by_file.items() if len(streams) > 1}
    if not collisions:
        print(f"no file in the last {depth} commits was touched by more than one stream")
        return 0

    print(f"files touched by more than one stream in the last {depth} commits:\n")
    for path, streams in sorted(collisions.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        rules = load_rules()
        rule = owner_of(path, rules)
        owner = rule.owner if rule else SHARED
        print(f"  {''.join(sorted(streams))}  {path}  (now owned by: {owner})")
    print(f"\n{len(collisions)} collision(s). Each one is a late merge conflict waiting to happen.")
    return 0


def audit() -> int:
    """Health of the OWNERS file itself: shadowed rules and unowned tracked files."""
    rules = load_rules()
    problems = 0

    # A rule that no later rule can ever expose is dead weight and misleads a reader.
    for index, rule in enumerate(rules):
        later = rules[index + 1 :]
        if any(r.pattern == rule.pattern for r in later):
            print(
                f"  ! OWNERS:{rule.line_no}: {rule.pattern} is overridden by an identical"
                " later rule"
            )
            problems += 1

    tracked = _git("ls-files").split()
    unowned = [p for p in tracked if (owner_of(p, rules) or Rule("*", SHARED, 0)).owner == SHARED]
    print(f"{len(tracked)} tracked files; {len(unowned)} resolve to SHARED")
    if unowned:
        # Not an error: SHARED is the deliberate default. Printed so the list stays small
        # and intentional rather than quietly absorbing new directories.
        for path in unowned[:15]:
            print(f"  ~ {path}")
        if len(unowned) > 15:
            print(f"  ... and {len(unowned) - 15} more")
    return 2 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stream", choices=STREAMS, help="the stream this branch belongs to")
    parser.add_argument("--base", default="origin/main", help="branch to diff against")
    parser.add_argument(
        "--advisory",
        action="store_true",
        help="report violations but exit 0 (for a first pass, not for CI)",
    )
    parser.add_argument("--report-collisions", action="store_true")
    parser.add_argument("--depth", type=int, default=40, help="commits to scan for collisions")
    parser.add_argument("--audit", action="store_true", help="check the OWNERS file itself")
    args = parser.parse_args(argv)

    if args.report_collisions:
        return report_collisions(args.base, args.depth)
    if args.audit:
        return audit()
    if not args.stream:
        parser.error("--stream is required unless --report-collisions or --audit is given")
    return check(args.stream, args.base, strict=not args.advisory)


if __name__ == "__main__":
    sys.exit(main())

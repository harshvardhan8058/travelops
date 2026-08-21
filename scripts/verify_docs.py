#!/usr/bin/env python3
"""Verify every relative Markdown link in the repository resolves.

Run via `make verify-docs`. Kept dependency-free so it works on any Python 3.
"""

from __future__ import annotations

import pathlib
import re
import sys

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
EXTERNAL = ("http://", "https://", "mailto:")


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    targets = [
        *root.glob("*.md"),
        *(root / "docs").rglob("*.md"),
        *(root / ".kiro").rglob("*.md"),
    ]

    broken: list[str] = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            target = match.group(1).split("#", 1)[0].strip()
            if not target or target.startswith(EXTERNAL):
                continue
            if not (path.parent / target).resolve().exists():
                line = text.count("\n", 0, match.start()) + 1
                broken.append(f"{path.relative_to(root)}:{line} -> {target}")

    if broken:
        print("Broken relative links:")
        for item in broken:
            print(f"  {item}")
        return 1

    print(f"OK: {len(targets)} markdown files checked, all relative links resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())

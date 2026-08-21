"""Operational CLI.

Targets in the Makefile call these subcommands. Wave 0 implements `openapi`; the data and
demo commands are stubbed for their owning streams.

Owner: Stream A.
"""

from __future__ import annotations

import argparse
import json
import sys


def cmd_openapi() -> int:
    """Print the OpenAPI document. `make openapi` writes it to docs/openapi.json."""
    from app.main import app

    json.dump(app.openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def cmd_seed() -> int:
    raise NotImplementedError(
        "Stream C: seed the fixed-seed dataset from data/fixtures/bengaluru_storm.yaml. "
        "Must be byte-identical across runs for seed 20260807."
    )


def cmd_reset() -> int:
    raise NotImplementedError(
        "Stream C: drop and recreate schema. Must refuse outside development/demo, and "
        "must only remove rows tagged with the demo dataset ID."
    )


def cmd_inject(scenario: str) -> int:
    raise NotImplementedError(
        f"Stream A: inject scenario '{scenario}' idempotently. Injecting twice must not "
        "open a second incident for the same flight."
    )


def cmd_demo_reset() -> int:
    raise NotImplementedError("Stream A: reset demo-owned records, then re-inject.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="travelops")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("openapi", help="print the OpenAPI document")
    sub.add_parser("seed", help="seed the fixed-seed demo dataset")
    sub.add_parser("reset", help="drop and recreate schema (development only)")
    sub.add_parser("demo-reset", help="reset demo-owned records and re-inject")

    inject = sub.add_parser("inject", help="inject a demo scenario")
    inject.add_argument("--scenario", default="bengaluru_storm")

    args = parser.parse_args(argv)

    match args.command:
        case "openapi":
            return cmd_openapi()
        case "seed":
            return cmd_seed()
        case "reset":
            return cmd_reset()
        case "demo-reset":
            return cmd_demo_reset()
        case "inject":
            return cmd_inject(args.scenario)
        case _:
            parser.error(f"unknown command {args.command}")
            return 2


if __name__ == "__main__":
    sys.exit(main())

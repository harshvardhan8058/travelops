"""OurAirports airports and runways — real, public-domain reference data.

This is one of the two genuinely real datasets in the system (weather is the other), so it
carries the strictest evidence discipline:

* The upstream files are filtered to the ten-airport set and the **subset is archived** in
  the repository, with the SHA-256 of both the upstream file and the archived subset
  recorded in a manifest. The full 12 MB airports.csv is not committed; its hash is, so the
  exact upstream revision behind the subset stays checkable.
* The contract test runs against the archive, never the network. CI and a demo laptop on a
  dead venue network behave identically.
* Runway true headings are the reason this loader exists. Crosswind is a function of wind
  direction relative to runway orientation, and a rule using raw wind speed alone would
  flag a 45 kt straight-down-the-runway headwind as dangerous when it is operationally fine.

**The heading gap is recorded, not papered over.** OurAirports leaves `le_heading_degT`
blank for some runways, including VOBL 09L/27R — the demo airport. Those headings are
derived from the designator and marked `designator_derived`, so a risk score never
implicitly claims a surveyed heading it does not have.

Licence: OurAirports data is released into the public domain. See the archived LICENCE.md.

Refresh (needs network):
    cd backend && uv run python -m data.loaders.ourairports --refresh

Owner: Stream C.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.config import REPO_ROOT

#: The ten-airport set from docs/12-synthetic-data-plan.md. ICAO, never IATA — VOBL, not BLR.
AIRPORT_ICAOS: tuple[str, ...] = (
    "VOBL",  # Bengaluru      BLR
    "VIDP",  # Delhi          DEL
    "VABB",  # Mumbai         BOM
    "VOHS",  # Hyderabad      HYD
    "VOMM",  # Chennai        MAA
    "VECC",  # Kolkata        CCU
    "VOCI",  # Kochi          COK
    "VOGO",  # Goa (Dabolim)  GOI
    "VAAH",  # Ahmedabad      AMD
    "VAPO",  # Pune           PNQ
)

AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
RUNWAYS_URL = "https://davidmegginson.github.io/ourairports-data/runways.csv"

SNAPSHOT_ROOT = REPO_ROOT / "data" / "snapshots" / "ourairports"
#: The archived snapshot the loader and its contract test both read. Bump on refresh.
SNAPSHOT_DATE = "2026-08-21"

PROVENANCE_KIND = "real"
PROVIDER = "ourairports"

HEADING_TRUE = "ourairports_true"
HEADING_DERIVED = "designator_derived"


def snapshot_dir(date: str = SNAPSHOT_DATE) -> Path:
    return SNAPSHOT_ROOT / date


def manifest_path(date: str = SNAPSHOT_DATE) -> Path:
    return snapshot_dir(date) / "MANIFEST.json"


# --------------------------------------------------------------------------- records


@dataclass(frozen=True, slots=True)
class AirportRecord:
    icao_code: str
    iata_code: str | None
    name: str
    city: str | None
    country: str
    latitude: float
    longitude: float
    elevation_ft: int | None
    timezone: str
    source_ref: str
    provenance_kind: str = PROVENANCE_KIND


@dataclass(frozen=True, slots=True)
class RunwayRecord:
    """One runway END. Two ends per physical runway, because crosswind depends on the
    direction in use, not on the strip."""

    airport_icao: str
    designator: str
    heading_degrees_true: int
    heading_source: str
    length_ft: int | None
    is_active: bool
    source_ref: str


# --------------------------------------------------------------------------- helpers


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _heading_from_designator(designator: str) -> int | None:
    """`09L` -> 90. Accurate to the ten degrees a designator encodes.

    Designators are magnetic and rounded, so this ignores magnetic variation — about 1.5
    degrees at Bengaluru. That is why the result is labelled `designator_derived`.
    """
    digits = "".join(ch for ch in designator if ch.isdigit())
    if not digits:
        return None
    value = int(digits) * 10
    return value % 360


def _int_or_none(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    try:
        return round(float(value))
    except ValueError:
        return None


# --------------------------------------------------------------------------- filtering


def filter_airports(raw: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(raw))
    wanted = set(AIRPORT_ICAOS)
    rows = [row for row in reader if row["ident"] in wanted]
    return sorted(rows, key=lambda row: row["ident"])


def filter_runways(raw: str) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(raw))
    wanted = set(AIRPORT_ICAOS)
    rows = [row for row in reader if row["airport_ident"] in wanted]
    return sorted(rows, key=lambda row: (row["airport_ident"], row["le_ident"]))


def _write_subset(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    payload = buffer.getvalue().encode("utf-8")
    path.write_bytes(payload)
    return payload


# --------------------------------------------------------------------------- refresh


def refresh(date: str = SNAPSHOT_DATE) -> dict:
    """Download, filter, archive and hash. The only function here that touches the network."""
    import httpx

    target = snapshot_dir(date)
    target.mkdir(parents=True, exist_ok=True)
    retrieved_at = datetime.now(tz=UTC).replace(microsecond=0).isoformat()

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        airports_raw = client.get(AIRPORTS_URL).raise_for_status().content
        runways_raw = client.get(RUNWAYS_URL).raise_for_status().content

    airport_rows = filter_airports(airports_raw.decode("utf-8"))
    runway_rows = filter_runways(runways_raw.decode("utf-8"))

    missing = set(AIRPORT_ICAOS) - {row["ident"] for row in airport_rows}
    if missing:
        raise RuntimeError(f"snapshot is missing airports: {sorted(missing)}")

    airport_subset = _write_subset(
        target / "airports.subset.csv",
        airport_rows,
        list(airport_rows[0]),
    )
    runway_subset = _write_subset(
        target / "runways.subset.csv",
        runway_rows,
        list(runway_rows[0]),
    )

    manifest = {
        "source": PROVIDER,
        "licence": "public domain",
        "attribution": "Airport and runway data from OurAirports (ourairports.com/data)",
        "retrieved_at": retrieved_at,
        "airport_icaos": list(AIRPORT_ICAOS),
        # The hash is only meaningful for a known byte form, so the byte form is stated.
        "integrity": {
            "subset_line_ending": "LF",
            "subset_encoding": "utf-8",
            "note": (
                "subset_sha256 is the SHA-256 of the committed bytes, which use LF line "
                "endings. data/.gitattributes marks these files -text so no checkout "
                "rewrites them; a CRLF working tree hashes differently and "
                "verify_snapshot() will refuse it."
            ),
        },
        "files": {
            "airports": {
                "url": AIRPORTS_URL,
                "upstream_sha256": sha256_bytes(airports_raw),
                "upstream_bytes": len(airports_raw),
                "subset_file": "airports.subset.csv",
                "subset_sha256": sha256_bytes(airport_subset),
                "subset_bytes": len(airport_subset),
                "subset_rows": len(airport_rows),
            },
            "runways": {
                "url": RUNWAYS_URL,
                "upstream_sha256": sha256_bytes(runways_raw),
                "upstream_bytes": len(runways_raw),
                "subset_file": "runways.subset.csv",
                "subset_sha256": sha256_bytes(runway_subset),
                "subset_bytes": len(runway_subset),
                "subset_rows": len(runway_rows),
            },
        },
        "note": (
            "The upstream files are not committed; their SHA-256 is, so the exact revision "
            "behind the archived subset stays checkable. Only the filtered subset is loaded."
        ),
    }
    manifest_path(date).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


# --------------------------------------------------------------------------- loading


def read_manifest(date: str = SNAPSHOT_DATE) -> dict:
    return json.loads(manifest_path(date).read_text(encoding="utf-8"))


def verify_snapshot(date: str = SNAPSHOT_DATE) -> None:
    """Recompute the archived subsets' hashes and compare against the manifest.

    An archive whose hash is not checked is a claim, not evidence, so a mismatch always
    raises. What the message adds is the *cause*, because the overwhelmingly likely one is not
    a corrupted archive.

    The subsets are committed with LF line endings and hashed as such. Git for Windows
    defaults to `core.autocrlf=true` and rewrites them to CRLF on checkout, which adds a byte
    per line and changes the hash — so the archive is intact, the repository is intact, and the
    working tree is simply no longer the bytes the manifest describes. `data/.gitattributes`
    marks these files `-text` to prevent it; a checkout taken before that existed still needs
    refreshing. Diagnosing that from a bare hash mismatch costs an hour, so it is named here.
    """
    manifest = read_manifest(date)
    for key, entry in manifest["files"].items():
        payload = (snapshot_dir(date) / entry["subset_file"]).read_bytes()
        actual = sha256_bytes(payload)
        if actual == entry["subset_sha256"]:
            continue

        expected = entry["subset_sha256"]
        detail = ""
        if b"\r\n" in payload:
            normalised = sha256_bytes(payload.replace(b"\r\n", b"\n"))
            if normalised == expected:
                detail = (
                    " The file holds CRLF line endings; with LF it hashes correctly, so the "
                    "archive is intact and the checkout rewrote it. Ensure "
                    "data/.gitattributes is present, then restore the file: "
                    "`git rm --cached -r data/snapshots && git checkout -- data/snapshots` "
                    "(or re-clone). Do not update the manifest to the CRLF hash — that would "
                    "record a platform-specific value and break every other platform."
                )
        raise RuntimeError(
            f"{key} subset hash mismatch: manifest {expected}, actual {actual}.{detail}"
        )


def load_airports(date: str = SNAPSHOT_DATE) -> list[AirportRecord]:
    manifest = read_manifest(date)
    source_ref = f"ourairports:airports:{manifest['retrieved_at']}"
    path = snapshot_dir(date) / manifest["files"]["airports"]["subset_file"]

    records: list[AirportRecord] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            records.append(
                AirportRecord(
                    icao_code=row["ident"],
                    iata_code=row["iata_code"] or None,
                    name=row["name"],
                    city=row["municipality"] or None,
                    country=row["iso_country"],
                    latitude=float(row["latitude_deg"]),
                    longitude=float(row["longitude_deg"]),
                    elevation_ft=_int_or_none(row["elevation_ft"]),
                    # Every airport in the set is Indian. Recorded rather than looked up so
                    # the loader stays offline and deterministic.
                    timezone="Asia/Kolkata",
                    source_ref=source_ref,
                )
            )
    return sorted(records, key=lambda record: record.icao_code)


def load_runways(date: str = SNAPSHOT_DATE) -> list[RunwayRecord]:
    """Both ends of every runway in the set, with the heading source recorded."""
    manifest = read_manifest(date)
    source_ref = f"ourairports:runways:{manifest['retrieved_at']}"
    path = snapshot_dir(date) / manifest["files"]["runways"]["subset_file"]

    records: list[RunwayRecord] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            closed = row["closed"] == "1"
            length_ft = _int_or_none(row["length_ft"])

            for prefix in ("le", "he"):
                designator = row[f"{prefix}_ident"]
                if not designator:
                    continue

                heading = _int_or_none(row[f"{prefix}_heading_degT"])
                if heading is None:
                    heading = _heading_from_designator(designator)
                    heading_source = HEADING_DERIVED
                else:
                    heading_source = HEADING_TRUE
                if heading is None:
                    # No true heading and no numeric designator: unusable for crosswind, so
                    # it is dropped rather than defaulted to zero.
                    continue

                records.append(
                    RunwayRecord(
                        airport_icao=row["airport_ident"],
                        designator=designator,
                        heading_degrees_true=heading % 360,
                        heading_source=heading_source,
                        length_ft=length_ft,
                        is_active=not closed,
                        source_ref=source_ref,
                    )
                )

    return sorted(records, key=lambda record: (record.airport_icao, record.designator))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="download and re-archive")
    parser.add_argument("--date", default=SNAPSHOT_DATE)
    args = parser.parse_args()

    if args.refresh:
        manifest = refresh(args.date)
        print(json.dumps(manifest, indent=2))
        return

    verify_snapshot(args.date)
    airports = load_airports(args.date)
    runways = load_runways(args.date)
    derived = [r for r in runways if r.heading_source == HEADING_DERIVED]
    print(
        f"airports: {len(airports)}  runway ends: {len(runways)}  derived headings: {len(derived)}"
    )
    for record in derived:
        print(
            f"  derived: {record.airport_icao} {record.designator} -> {record.heading_degrees_true}"
        )
    print(json.dumps([asdict(r) for r in airports[:1]], indent=2))


if __name__ == "__main__":
    main()

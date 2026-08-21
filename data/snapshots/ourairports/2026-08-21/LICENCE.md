# OurAirports snapshot — licence and attribution

**Source:** [OurAirports open data](https://ourairports.com/data/)
**Upstream mirror:** <https://github.com/davidmegginson/ourairports-data>
**Retrieved:** 2026-08-21 (see `MANIFEST.json` for the exact timestamp and hashes)

## Licence

OurAirports releases its data into the **public domain**. There is no restriction on use,
redistribution or modification.

Attribution is not required by the licence, and is given anyway because a claim that data is
real should say where it came from:

> Airport and runway data from OurAirports (ourairports.com/data)

## What is archived here

Only the ten airports this project uses, filtered from the upstream CSVs:

| File | Rows | Contents |
| --- | --- | --- |
| `airports.subset.csv` | 10 | VOBL VIDP VABB VOHS VOMM VECC VOCI VOGO VAAH VAPO |
| `runways.subset.csv` | 19 | physical runways at those airports, both ends each |

The full upstream files are **not** committed — `airports.csv` alone is 12 MB. Their SHA-256
is recorded in `MANIFEST.json`, so the exact upstream revision behind this subset stays
checkable without carrying the bytes.

## Known gap in the upstream data

`VOBL 09L/27R` has **no `le_heading_degT` or `he_heading_degT` upstream**, and VOBL is the
demo airport. The loader derives those two headings from the designator and records them as
`heading_source = designator_derived`; the other 36 runway ends are `ourairports_true`.

This matters because crosswind is computed against runway orientation. A derived heading is
accurate to the ten degrees a designator encodes and ignores magnetic variation — about 1.5°
at Bengaluru, and the parallel `09R` is surveyed at 92° against the derived 90°, so the
error is small. It is labelled rather than hidden so a risk index never implies a surveyed
heading it does not have.

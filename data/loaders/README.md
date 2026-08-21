# data/loaders — public reference data

Owner: **Stream C**. Loaders for real, licence-checked public datasets. One module per source.

Expected modules:

| Module | Source | Notes |
| --- | --- | --- |
| `airports.py` | OurAirports airports.csv | The ten-airport set only |
| `runways.py` | OurAirports runways.csv | **True headings are mandatory** — crosswind scoring is meaningless without them |

Every loader must:

1. Archive the downloaded snapshot under `data/snapshots/<source>/<date>/` and record its SHA-256.
2. Write a `source` row with `provenance_kind=real`, the licence, and the fetch timestamp.
3. Have a contract test in `backend/tests/contract/` that passes against the archived snapshot,
   so CI never depends on the network.

Never load a dataset the repo cannot prove it is licensed to use. `docs/10-data-sources.md` lists
which sources are validated and which are not — AIKosh schedules are **not** yet, so schedule data
stays labelled `synthetic` until a loader contract test passes here.

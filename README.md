# Planetarium midterms 2026

Planetarium's forecasts of the 3 November 2026 US midterm elections, locked before election day so that they can be
scored against the results.

## What is here

This repository holds fingerprints, not the forecasts themselves. Each folder has a `SHA256SUMS` file: the sha256 of
every file of one forecast version, including the version's own seal. A sha256 changes completely if a single byte of
the file changes, so the fingerprints posted here before election day prove which files existed then. The files will
be published in the same folders later, and anyone can check that they match.

| Folder | Version | Made |
|---|---|---|
| `v2.1/` | The final forecast: every 2026 Senate, governor, House and statewide ballot-measure contest, with Planetarium alone beside it | 28 October 2026 (sealed 18:01 UTC) |
| `earlier/v2.0-after-the-alaska-poll-fix/` | Version 2.0, every state, with its one correction (the Alaska Senate poll average) | 23 October 2026 |
| `earlier/v2.0-as-published/` | Version 2.0 as first sent, before that correction | 23 October 2026 |
| `earlier/v1.0.0/` | Version 1.0.0: 56 contests in 8 states, as vote shares | 23 October 2026 |
| `earlier/2026-10-15/` | The first forecast: 56 contests in 8 states, with six other methods run beside it | 15 October 2026 |
| `dry-run/` | A test of the lock, not a forecast | 28 October 2026 |

The earlier versions were made on the dates shown and kept in our private repository. Their fingerprints are posted
here for the first time with the final version, so this repository proves that they existed before election day, not
the day each was made.

## How to check

Once the files are published, in a folder:

```
shasum -a 256 -c SHA256SUMS
```

`SHA256SUMS.ots` is an OpenTimestamps proof that `SHA256SUMS` existed at the time it records, anchored in the Bitcoin
blockchain. To check it, install the client (`pip install opentimestamps-client`) and run `ots verify SHA256SUMS.ots`,
or upload both files at https://opentimestamps.org.

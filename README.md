# Planetarium midterms 2026

Planetarium's forecast of the 3 November 2026 US midterm elections, locked before election day so that it can be
scored against the results.

## What is here

- `dry-run/`: a test of the lock, not a forecast. It checks that this repository is public, that the files'
  sha256 fingerprints are posted, that the fingerprints are timestamped with OpenTimestamps and that a snapshot is
  saved on archive.org.

The forecast itself will be added here once it is locked.

## How to check a file

Each folder has a `SHA256SUMS` file. To check that the files are unchanged:

```
shasum -a 256 -c SHA256SUMS
```

`SHA256SUMS.ots` is an OpenTimestamps proof that `SHA256SUMS` existed at the time it records. To check it, install the
client (`pip install opentimestamps-client`) and run `ots verify SHA256SUMS.ots`, or upload both files at
https://opentimestamps.org.

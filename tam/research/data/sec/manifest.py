"""Manifest: `{cik: {last_accession_seen, last_checked_at, schema_version}}`,
persisted via SecStore's own read_manifest_bytes()/write_manifest_bytes()
-- the exact same shape/tolerance as tam.marketdata.ingest's own _Manifest
for minute bars (see that module's docstring): a corrupt/partial manifest
just means a company gets redundantly re-checked next run, never a
correctness bug, so this isn't written with any atomic tempfile-rename
dance.

This is the answer to "what do I already have" -- deliberately a small
exact JSON blob, not a Bloom filter (see the approved plan's own
assessment of why a Bloom filter isn't worth it at curated-universe
scale).
"""
from __future__ import annotations

import json
from typing import Optional

from .store import SecStore

SCHEMA_VERSION = 1


class Manifest:
    """`record()` only updates the in-memory copy -- call `flush()` to
    persist. Kept separate so a caller checking/updating many CIKs in a
    row can record each one immediately (cheap, in-memory) while only
    writing the manifest file itself once at the end, not after every
    single company."""

    def __init__(self, store: SecStore):
        self._store = store
        self._data = self._read()

    def _read(self) -> dict:
        raw = self._store.read_manifest_bytes()
        if raw is None:
            return {}
        return json.loads(raw)

    def last_accession_seen(self, cik: int) -> Optional[str]:
        entry = self._data.get(str(cik))
        return entry.get("last_accession_seen") if entry else None

    def schema_version_for(self, cik: int) -> int:
        """The schema_version recorded for `cik`, or 1 (not 0) for a CIK
        never recorded at all -- same "unversioned means the very first
        schema" reasoning as tam.marketdata.completeness.sidecar_schema_version."""
        entry = self._data.get(str(cik))
        return int(entry.get("schema_version", 1)) if entry else 1

    def record(self, cik: int, *, last_accession_seen: str, checked_at: str) -> None:
        self._data[str(cik)] = {
            "last_accession_seen": last_accession_seen,
            "last_checked_at": checked_at,
            "schema_version": SCHEMA_VERSION,
        }

    def flush(self) -> None:
        self._store.write_manifest_bytes(json.dumps(self._data, indent=2).encode("utf-8"))

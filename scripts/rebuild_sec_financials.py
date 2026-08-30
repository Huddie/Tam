"""Pure local rebuild of the derived financials layer from already-fetched
raw facts -- NO network to SEC at all, only R2 reads/writes. Safe to rerun
any time normalize.py's concept-mapping logic improves (a new edgartools
version, a fix to the period-dedup logic, ...) without re-fetching a
single byte from SEC.

Usage:
    uv run python scripts/rebuild_sec_financials.py

Loads EVERY facts partition currently on R2 (via
SecStore.list_facts_partitions()) into memory before normalizing anything,
then runs normalize_facts() once per CIK over that company's FULL raw-facts
history across every taxonomy/fiscal_year -- not partition-by-partition.
This matters: normalize_facts()'s period dedup collapses the SAME true
period re-reported under DIFFERENT (unreliable) fiscal_year labels across
MULTIPLE accession numbers (see that module's own docstring for the real
AAPL example verified this session -- FY2022 revenue appearing under
fy=2022/2023/2024 alike). Normalizing one fiscal_year partition at a time
would only ever see one of those copies per call and silently let the
duplicates back in. At this system's own estimated scale (a few GB for the
curated universe's full history, per the approved plan's cost estimate)
holding it all in memory for this maintenance-only job is the simple,
correct choice -- not something to run on every ingest, just when the
normalization logic itself changes.
"""

from __future__ import annotations

import time

import pandas as pd

from tam.research.data.sec import SecStore, normalize_facts, schema


def main() -> None:
    store = SecStore()
    partitions = sorted(store.list_facts_partitions())
    print(f"{len(partitions)} facts partition(s) found.")

    started = time.monotonic()
    all_facts = []
    for i, (taxonomy, fiscal_year) in enumerate(partitions, start=1):
        facts = store.read_facts(taxonomy, fiscal_year)
        if not facts.empty:
            all_facts.append(facts)
        print(f"  read [{i}/{len(partitions)}] {taxonomy}/{fiscal_year}: {len(facts)} fact row(s)")

    if not all_facts:
        print("No facts found -- nothing to rebuild.")
        return

    combined = pd.concat(all_facts, ignore_index=True)
    companies = combined[schema.CIK].nunique()
    print(f"{len(combined)} total raw fact row(s) across {companies} compan(y/ies) -- normalizing ...")

    for i, (cik, group) in enumerate(combined.groupby(schema.CIK), start=1):
        financials = normalize_facts(group)
        for fiscal_year, fy_group in financials.groupby(schema.FISCAL_YEAR):
            store.write_financials(int(cik), int(fiscal_year), fy_group)
        if i % 50 == 0:
            print(f"  normalized [{i}/{companies}] ... ({time.monotonic() - started:.0f}s elapsed)")

    print(f"Done in {time.monotonic() - started:.0f}s.")


if __name__ == "__main__":
    main()

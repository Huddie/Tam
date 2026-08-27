"""One-time migration: reshuffle sec/ objects written under the OLD
Hive-style layout (facts/taxonomy={t}/fiscal_year={y}/facts.parquet,
submissions/fiscal_year={y}/filings.parquet, financials/fiscal_year={y}/
financials.parquet) into the NEW flat layout (facts/{t}/{y}.parquet,
submissions/{y}.parquet, financials/{y}.parquet) -- see store.py's own
docstring for why the new layout replaced the old one.

Only relevant if you ran scripts/backfill_sec_facts.py (or
rebuild_sec_financials.py) against an OLDER checkout that still wrote the
Hive-style paths -- a fresh checkout never produces old-style keys in the
first place, so this script finds nothing to do and exits immediately.

Usage:
    uv run python scripts/migrate_sec_layout.py           # do it
    uv run python scripts/migrate_sec_layout.py --dry-run # list what WOULD move, touch nothing

Uses R2's server-side COPY (boto3 copy_object) -- bytes never pass through
this machine, so this is fast and cheap regardless of how much data has
already been backfilled. Deletes the old-style object only after its
copy at the new key is confirmed to exist, and skips (does not
overwrite) a new-style key that's already there -- safe to interrupt and
re-run.

IMPORTANT: run this only after any in-flight scripts/backfill_sec_facts.py
run (started before this migration existed) has fully finished -- running
this WHILE that script is still actively writing old-style keys would race
it, potentially migrating a file the old run is about to rewrite again.
"""
from __future__ import annotations

import argparse
import re
from typing import List, Tuple

from tam.research.data.sec.store import SecStore

_OLD_FACTS_RE = re.compile(r"^facts/taxonomy=([^/]+)/fiscal_year=(\d+)/facts\.parquet$")
_OLD_SUBMISSIONS_RE = re.compile(r"^submissions/fiscal_year=(\d+)/filings\.parquet$")
_OLD_FINANCIALS_RE = re.compile(r"^financials/fiscal_year=(\d+)/financials\.parquet$")


def _list_old_keys(store: SecStore) -> List[Tuple[str, str]]:
    """Every old-style key under sec/ paired with its new-style
    equivalent -- (old_key, new_key), both including the `sec/` prefix."""
    prefix = f"{store._prefix}/"
    pairs = []
    paginator = store._client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=store._credentials.bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            relative = obj["Key"][len(prefix) :]
            if m := _OLD_FACTS_RE.match(relative):
                taxonomy, fiscal_year = m.groups()
                pairs.append((obj["Key"], f"{prefix}facts/{taxonomy}/{fiscal_year}.parquet"))
            elif m := _OLD_SUBMISSIONS_RE.match(relative):
                (fiscal_year,) = m.groups()
                pairs.append((obj["Key"], f"{prefix}submissions/{fiscal_year}.parquet"))
            elif m := _OLD_FINANCIALS_RE.match(relative):
                (fiscal_year,) = m.groups()
                pairs.append((obj["Key"], f"{prefix}financials/{fiscal_year}.parquet"))
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="List what would move, without touching anything")
    args = parser.parse_args()

    store = SecStore()
    pairs = _list_old_keys(store)
    print(f"{len(pairs)} old-style object(s) found.")

    if not pairs:
        print("Nothing to migrate.")
        return

    if args.dry_run:
        for old_key, new_key in pairs:
            print(f"  {old_key}  ->  {new_key}")
        return

    bucket = store._credentials.bucket
    client = store._client
    from botocore.exceptions import ClientError

    def _exists(key: str) -> bool:
        try:
            client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return False
            raise

    migrated = 0
    for old_key, new_key in pairs:
        if _exists(new_key):
            print(f"  SKIP (new key already exists): {new_key}")
            continue

        client.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": old_key}, Key=new_key)
        if not _exists(new_key):
            raise RuntimeError(f"Copy reported success but {new_key} still doesn't exist -- aborting before deleting {old_key}.")
        client.delete_object(Bucket=bucket, Key=old_key)
        migrated += 1
        print(f"  moved: {old_key}  ->  {new_key}")

    print(f"Migrated {migrated}/{len(pairs)} object(s).")


if __name__ == "__main__":
    main()

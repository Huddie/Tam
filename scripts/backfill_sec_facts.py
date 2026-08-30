"""Backfills the SEC XBRL/filings data lake (tam.research.data.sec) for the
curated universe -- the same "every ticker that was EVER an S&P 500
constituent in the window" universe as scripts/backfill_sp500_eod.py (same
survivorship-bias reasoning), not all ~10,000+ SEC filers. Populates all
three R2 layers: submissions (filing metadata), facts (raw XBRL, full
fidelity), and financials (derived, normalized, long format).

Usage:
    uv run python scripts/backfill_sec_facts.py
    uv run python scripts/backfill_sec_facts.py --years 15
    uv run python scripts/backfill_sec_facts.py --refresh-reference
    uv run python scripts/backfill_sec_facts.py --workers 4

Incremental/resumable via tam.research.data.sec.manifest.Manifest: every
company's cheap submissions.json is always re-fetched (no XBRL parsing,
cheap enough to check daily), but the heavier companyfacts.json refetch --
and the raw-facts/financials writes it drives -- only happens when that
check finds a genuinely NEW most-recent accession number since the last
recorded run. "Most recent" is the filing with the latest `filed_date`, not
positional order or SEC's own `fy`/`fp` fields -- see normalize.py's
docstring for why those aren't a reliable period identity across filings.
A company with no new filings since last run costs one cheap request, not
a full companyfacts refetch.

Needs a `SEC_IDENTITY` secret (tam.Secrets) -- SEC's own documented User-
Agent policy, e.g. "Your Name your.email@example.com" -- see provider.py.
Also needs the `sec/reference/company_tickers.parquet` reference table
populated at least once (--refresh-reference, or it auto-populates itself
on first run if that file doesn't exist yet); that specific fetch hits
www.sec.gov, a different host than every other endpoint this script uses
(data.sec.gov).

One company's failure (a transient SEC error, an unresolvable ticker, ...)
is caught and reported, not fatal to the whole run -- same reasoning as
backfill_sp500_eod.py's per-ticker resilience.
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone

import pandas as pd

from tam.research.data.sec import Manifest, SecProvider, SecStore, normalize_facts, schema
from tam.secrets import Secrets


def _historical_sp500_tickers(years: int) -> list[str]:
    """Every ticker that was ever an S&P 500 constituent in the last
    `years` years -- duplicated from scripts/backfill_sp500_eod.py's own
    helper of the same shape rather than shared, small independent pieces
    per script, matching this codebase's existing convention."""
    import pitindex

    end = date.today()
    start = end.replace(year=end.year - years)
    history = pitindex.get_constituents_history(start, end, index="sp500")
    return sorted(history["ticker"].unique().tolist())


def _resolve_ciks(
    store: SecStore, provider: SecProvider, tickers: list[str], refresh_reference: bool
) -> dict[str, int]:
    reference = store.read_reference()
    if refresh_reference or reference.empty:
        reference = provider.fetch_company_tickers()
        store.write_reference(reference)
        print(f"Refreshed reference table: {len(reference)} ticker(s).")

    lookup = {row[schema.TICKER].upper(): int(row[schema.CIK]) for row in reference.to_dict("records")}
    resolved: dict[str, int] = {}
    missing: list[str] = []
    for ticker in tickers:
        cik = lookup.get(ticker.upper())
        if cik is None:
            missing.append(ticker)
        else:
            resolved[ticker] = cik
    if missing:
        shown = ", ".join(missing[:20]) + (" ..." if len(missing) > 20 else "")
        print(f"{len(missing)} ticker(s) not found in the reference table, skipped: {shown}")
    return resolved


def _fill_missing_fiscal_year(facts: pd.DataFrame) -> pd.DataFrame:
    """Some raw facts arrive with a null `fy` (not every SEC entry carries
    one) -- fall back to the period's own end_date year so every row still
    lands in SOME fiscal_year partition, instead of vanishing from a
    groupby over a null key."""
    if facts.empty:
        return facts
    filled = facts.copy()
    missing = filled[schema.FISCAL_YEAR].isna()
    if missing.any():
        filled.loc[missing, schema.FISCAL_YEAR] = pd.to_datetime(filled.loc[missing, schema.END_DATE]).dt.year
    filled[schema.FISCAL_YEAR] = filled[schema.FISCAL_YEAR].astype("int64")
    return filled


def _latest_accession(submissions: pd.DataFrame) -> str | None:
    if submissions.empty:
        return None
    return submissions.loc[submissions[schema.FILED_DATE].idxmax(), schema.ACCESSION_NUMBER]


def _ingest_one(cik: int, ticker: str, provider: SecProvider, store: SecStore, last_seen: str | None) -> dict:
    submissions = provider.fetch_submissions(cik)
    latest_accession = _latest_accession(submissions)
    if latest_accession is None:
        return {"cik": cik, "latest_accession": None, "message": f"{ticker} (CIK {cik}): no filings found"}

    filed_years = pd.to_datetime(submissions[schema.FILED_DATE]).dt.year
    for fiscal_year, group in submissions.groupby(filed_years):
        store.write_submissions(cik, int(fiscal_year), group)

    if last_seen == latest_accession:
        return {
            "cik": cik,
            "latest_accession": latest_accession,
            "message": f"{ticker} (CIK {cik}): up to date ({latest_accession})",
        }

    facts = _fill_missing_fiscal_year(provider.fetch_company_facts(cik))
    for (taxonomy, fiscal_year), group in facts.groupby([schema.TAXONOMY, schema.FISCAL_YEAR]):
        store.write_facts(cik, taxonomy, int(fiscal_year), group)

    financials = normalize_facts(facts)
    for fiscal_year, group in financials.groupby(schema.FISCAL_YEAR):
        store.write_financials(cik, int(fiscal_year), group)

    return {
        "cik": cik,
        "latest_accession": latest_accession,
        "message": f"{ticker} (CIK {cik}): refreshed ({len(facts)} facts, {len(financials)} financials rows)",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--years", type=int, default=20, help="Curated universe lookback window (default: 20)")
    parser.add_argument(
        "--refresh-reference", action="store_true", help="Force-refetch sec/reference/company_tickers.parquet"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Thread pool size (default: 4 -- SEC's own ~10 req/sec limit is enforced process-wide "
        "regardless via provider.py's shared throttle, so this mainly hides per-request latency)",
    )
    args = parser.parse_args()

    identity = Secrets.get("SEC_IDENTITY")
    if not identity:
        raise SystemExit(
            "Missing SEC_IDENTITY secret -- SEC requires a real User-Agent identifying string "
            "(see tam/research/data/sec/provider.py's docstring)."
        )

    tickers = _historical_sp500_tickers(args.years)
    print(f"{len(tickers)} ticker(s) in the curated universe.")

    store = SecStore()
    provider = SecProvider(identity)
    manifest = Manifest(store)
    resolved = _resolve_ciks(store, provider, tickers, args.refresh_reference)
    print(f"Resolved {len(resolved)}/{len(tickers)} ticker(s) to a CIK.")

    started = time.monotonic()
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_ingest_one, cik, ticker, provider, store, manifest.last_accession_seen(cik)): ticker
            for ticker, cik in resolved.items()
        }
        for future in as_completed(futures):
            ticker = futures[future]
            done += 1
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 -- one company's failure shouldn't abort the whole backfill
                print(f"{ticker}: FAILED -- {exc}")
                continue
            print(result["message"])
            if result["latest_accession"] is not None:
                manifest.record(
                    result["cik"],
                    last_accession_seen=result["latest_accession"],
                    checked_at=datetime.now(timezone.utc).isoformat(),
                )
            if done % 25 == 0:
                manifest.flush()
                print(f"  [{done}/{len(resolved)}] ... ({time.monotonic() - started:.0f}s elapsed)")

    manifest.flush()
    print(f"Done in {time.monotonic() - started:.0f}s.")


if __name__ == "__main__":
    main()

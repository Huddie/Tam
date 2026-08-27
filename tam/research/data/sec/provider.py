"""SecProvider: fetches raw structured SEC data directly from SEC's own
JSON APIs (data.sec.gov) -- NOT through EdgarTools' higher-level
statement/facts abstractions.

Why not EdgarTools for this specific path: full XBRL fidelity (taxonomy,
unit, instant-vs-duration, accession number, filed date, ...) is the
explicit requirement here, and this session already hit two library-
version-specific surprises trying to guess EdgarTools' exact DataFrame
shapes (`facts.income_statement()` returned a `MultiPeriodStatement`, not
a DataFrame; its `to_dataframe()` output's row/column shape differs
between a single-filing view and an `XBRLS.from_filings()` multi-period
view). SEC's own `companyfacts`/`submissions`/`company_tickers` JSON
endpoints are stable, documented, and already used successfully earlier
in this same work -- reusing that verified shape directly here is more
reliable than a third guess at a library's internal API. EdgarTools stays
genuinely useful for the progressive filing-DOCUMENT cache (`sec/
raw_filings/`) instead, where its `.html()`/`.markdown()`/`.text()`
helpers add real value beyond raw bytes -- see the store's
`write_filing_document` call sites.

IMPORTANT, DISCLOSED LIMITATION: `companyfacts` only carries WHOLE-COMPANY
aggregate facts -- no per-segment/geographic dimensional breakdown, no
XBRL context id. That level of detail only exists in each filing's own
full XBRL instance document (a much heavier per-filing parse). This
covers every line item the approved plan lists as PRIMARY (revenue, gross
profit, operating income, net income, assets/liabilities/equity, cash
flow, EPS) -- segment/geographic detail ("where available" in the
original spec, a softer requirement) is intentionally NOT covered by this
provider and would need a separate, heavier per-filing XBRL-instance
parser as a future addition, not silently faked here. `dimensions`/
`context_id` are always None from this path.

SEC's documented rate limit is ~10 requests/second -- _RATE_LIMIT enforces
a floor between requests process-wide via a shared lock, the same
"serialize + throttle third-party API access" shape
tam.data.providers.YFinanceProvider's own _YFINANCE_LOCK already uses
(for a different reason there -- avoiding a data race -- but the same
mechanism).
"""
from __future__ import annotations

import threading
import time
from typing import List, Optional

import pandas as pd
import requests

from . import schema

_BASE_URL = "https://data.sec.gov"
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_MIN_INTERVAL_SECONDS = 0.11  # a hair over 1/10s -- SEC's documented ~10 req/sec limit, floored not raced

_rate_limit_lock = threading.Lock()
_last_request_at = 0.0


def _throttled_get(session: requests.Session, url: str, headers: dict, timeout: float = 30.0) -> requests.Response:
    global _last_request_at
    with _rate_limit_lock:
        wait = _MIN_INTERVAL_SECONDS - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        response = session.get(url, headers=headers, timeout=timeout)
        _last_request_at = time.monotonic()
    response.raise_for_status()
    return response


def _cik_padded(cik: int) -> str:
    return f"{cik:010d}"


def _coerce_value(val: object) -> Optional[float]:
    """SEC's raw `val` is JSON, so pandas builds this column as a Python
    object (mixed int/float/None) -- pyarrow then infers int64 for it on
    write, which overflows on the rare mis-tagged/mis-scaled XBRL fact
    (observed live: one real EBAY fact broke `PyLong is too large to fit
    int64`). Casting to a plain Python float here, at the JSON boundary,
    keeps the column a clean float64 the way FACTS_COLUMNS already
    documents it -- no int64 range to overflow."""
    return None if val is None else float(val)


class SecProvider:
    """`identity` is the required SEC User-Agent string (e.g. "Your Name
    your.email@example.com") -- SEC's own documented policy, same
    requirement already used directly in this session via
    tam.Secrets["SEC_IDENTITY"]."""

    def __init__(self, identity: str, session: Optional[requests.Session] = None):
        self._headers = {"User-Agent": identity}
        self._session = session or requests.Session()

    def fetch_company_tickers(self) -> pd.DataFrame:
        """SEC's own small bulk CIK<->ticker<->name file -- the same
        source EdgarTools' own ticker resolution is backed by."""
        response = _throttled_get(self._session, _TICKERS_URL, self._headers)
        payload = response.json()
        rows = [
            {schema.CIK: entry["cik_str"], schema.TICKER: entry["ticker"], schema.ENTITY_NAME: entry["title"]}
            for entry in payload.values()
        ]
        return pd.DataFrame(rows, columns=schema.REFERENCE_COLUMNS)

    def _rows_from_filings_page(self, cik: int, page: dict) -> List[dict]:
        """`page` is one of SEC's own flat per-field-array filing pages --
        either `payload["filings"]["recent"]` (the newest ~1000 filings,
        inline in the main submissions response) or one of the ADDITIONAL
        paginated files listed under `payload["filings"]["files"]` for any
        company with more history than that (verified live: AAPL alone
        has a second file covering 1994-2015, 1240 more filings not in
        `recent` at all -- silently only reading `recent` would drop a
        long-history company's entire older filing record)."""
        n = len(page.get("accessionNumber", []))
        return [
            {
                schema.CIK: cik,
                schema.FILING_ACCESSION_NUMBER: page["accessionNumber"][i],
                schema.FILING_FORM: page["form"][i],
                schema.FILING_FILED_DATE: page["filingDate"][i],
                schema.FILING_PERIOD_OF_REPORT: page.get("reportDate", [None] * n)[i] or None,
                schema.FILING_PRIMARY_DOCUMENT: page.get("primaryDocument", [None] * n)[i] or None,
                schema.FILING_IS_XBRL: bool(int(page.get("isXBRL", [0] * n)[i] or 0)),
            }
            for i in range(n)
        ]

    def fetch_submissions(self, cik: int) -> pd.DataFrame:
        """Every filing this CIK has ever made (accession number, form,
        filed date, period of report, ...) -- cheap JSON, no XBRL
        parsing, cheap enough to call daily per company just to detect
        what's new (see the manifest for how that comparison works).
        Follows `filings.files` to fetch every additional paginated page
        beyond the inline `recent` one, not just the newest ~1000."""
        url = f"{_BASE_URL}/submissions/CIK{_cik_padded(cik)}.json"
        response = _throttled_get(self._session, url, self._headers)
        payload = response.json()
        filings = payload.get("filings", {})

        rows = self._rows_from_filings_page(cik, filings.get("recent", {}))
        for page_ref in filings.get("files", []):
            page_url = f"{_BASE_URL}/submissions/{page_ref['name']}"
            page_response = _throttled_get(self._session, page_url, self._headers)
            rows.extend(self._rows_from_filings_page(cik, page_response.json()))

        return pd.DataFrame(rows, columns=schema.SUBMISSIONS_COLUMNS)


    def fetch_company_facts(self, cik: int) -> pd.DataFrame:
        """Every whole-company XBRL fact SEC has for this CIK, across
        every taxonomy (us-gaap, dei, and any company-specific custom
        extension) -- see this module's own docstring for the dimensional-
        facts limitation. Returns FACTS_COLUMNS-shaped rows, one per
        (concept, unit, period, accession) -- restated values from
        different accession numbers are separate rows, never collapsed."""
        url = f"{_BASE_URL}/api/xbrl/companyfacts/CIK{_cik_padded(cik)}.json"
        response = _throttled_get(self._session, url, self._headers)
        payload = response.json()
        entity_name = payload.get("entityName")

        rows: List[dict] = []
        for taxonomy, concepts in payload.get("facts", {}).items():
            for concept, concept_data in concepts.items():
                for unit, entries in concept_data.get("units", {}).items():
                    for entry in entries:
                        start = entry.get("start")
                        rows.append(
                            {
                                schema.CIK: cik,
                                schema.ENTITY_NAME: entity_name,
                                schema.TAXONOMY: taxonomy,
                                schema.CONCEPT: concept,
                                schema.UNIT: unit,
                                schema.FACT_TYPE: "duration" if start else "instant",
                                schema.START_DATE: start,
                                schema.END_DATE: entry.get("end"),
                                schema.FISCAL_YEAR: entry.get("fy"),
                                schema.FISCAL_PERIOD: entry.get("fp"),
                                schema.FORM: entry.get("form"),
                                schema.FILED_DATE: entry.get("filed"),
                                schema.ACCESSION_NUMBER: entry.get("accn"),
                                schema.FRAME: entry.get("frame"),
                                schema.DIMENSIONS: None,  # see module docstring -- not available from this endpoint
                                schema.CONTEXT_ID: None,
                                schema.VALUE: _coerce_value(entry.get("val")),
                            }
                        )
        return pd.DataFrame(rows, columns=schema.FACTS_COLUMNS)

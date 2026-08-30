"""MassiveReferenceProvider: fetch six Massive/Polygon reference datasets
about securities -- splits, dividends, IPOs, short volume, short interest,
free float. Unlike tam.marketdata.providers' MinuteBarProvider (one flat
file per day, one vendor bulk download), these are paginated REST list
endpoints -- one API call sequence per dataset, covering every ticker at
once (there is no per-symbol driving loop anywhere in this module).

Four of the six go through the official `massive` PyPI package's typed
RESTClient methods -- confirmed live via direct source introspection to
hit the CURRENT, correct endpoints with the full documented schema, and
each one already paginates internally (returns a fully-paginated
List/Iterator, no manual loop needed):
  - fetch_ipos()           -> client.vx.list_ipos(...)             /vX/reference/ipos
  - fetch_short_volume()   -> client.list_short_volume(...)        /stocks/v1/short-volume
  - fetch_short_interest() -> client.list_short_interest(...)      /stocks/v1/short-interest
  - fetch_float()          -> client.list_stocks_floats(...)       /stocks/vX/float

Splits and dividends do NOT go through the SDK -- its typed
list_splits()/list_dividends() wrap an OLDER `/v3/reference/...` endpoint
with a smaller schema (their Split/Dividend dataclasses only declare
id/execution_date/split_from/split_to/ticker and id/cash_amount/currency/
declaration_date/dividend_type/ex_dividend_date/frequency/pay_date/
record_date/ticker respectively -- confirmed by reading the actual
dataclass source, which reconstructs via `Model(**d)`, so this really is
the full response shape for that endpoint). `historical_adjustment_factor`
-- the whole reason these are being ingested at all -- only exists on the
NEWER `/stocks/v1/splits`/`/stocks/v1/dividends` endpoints, which no typed
SDK method wraps (not even under `vx`). Called directly via `requests`
instead, with a hand-rolled `next_url` pagination loop.
"""
from __future__ import annotations

import dataclasses
import os
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

from . import reference_schema as schema
from .store import _with_retries

_BASE_URL = "https://api.massive.com"


def _resolve_api_key(explicit: Optional[str]) -> str:
    """Same "env var, with a constructor override" convention
    tam.marketdata.providers._resolve_vendor_key() already uses for the
    flat-file S3 credentials -- kept as its own small independent copy
    here (per this package's established "small independent pieces per
    subpackage" convention) rather than imported, since this resolves a
    completely different Massive product surface (a REST bearer token,
    not S3 access keys)."""
    value = explicit or os.environ.get("MASSIVE_API_KEY")
    if not value:
        raise ValueError("Massive REST API key required: set MASSIVE_API_KEY env var or pass api_key=")
    return value


def _model_to_dict(row: Any) -> Dict[str, Any]:
    """Every massive SDK model (IPOListing, ShortVolume, ShortInterest,
    FinancialFloat, ...) is a real dataclass (confirmed live:
    dataclasses.is_dataclass() is True) -- dataclasses.asdict() converts
    one row back into a plain dict ready for pd.DataFrame(rows)."""
    return dataclasses.asdict(row)


def _paginate_raw(
    session: "requests.Session", url: str, params: Dict[str, Any], api_key: str, log: Optional[Callable[[str], None]] = None
) -> List[dict]:
    """Follows `next_url` until exhausted -- for the two endpoints
    (splits/dividends) called directly via `requests` rather than through
    the `massive` SDK. Bearer auth matches the SDK's own convention
    (confirmed live via its debug-trace example: 'Authorization': 'Bearer
    ...'). The SDK's own typed methods get 429/5xx retry handling for
    free (its BaseClient wraps every call in a urllib3 Retry with
    backoff, confirmed live via source: retries=3, backoff_factor=0.1,
    status_forcelist includes 429) -- these two endpoints bypass the SDK
    entirely (see module docstring for why), so each page fetch is
    wrapped in tam.marketdata.store's own `_with_retries` instead, same 5-
    attempt/2-4-8-16s-backoff policy R2 calls already get, imported
    rather than re-implemented since it's genuinely the same transient-
    failure-tolerance need. `log`, if given, is called once per page with
    a running row count -- dividends in particular can be hundreds of
    pages, and this is the only one of the six fetches with no SDK-
    internal pagination to hide that latency."""
    headers = {"Authorization": f"Bearer {api_key}"}
    results: List[dict] = []
    next_url: Optional[str] = url
    next_params: Optional[Dict[str, Any]] = params
    page = 0
    while next_url:
        def _get() -> dict:
            response = session.get(next_url, headers=headers, params=next_params, timeout=30)
            response.raise_for_status()
            return response.json()

        payload = _with_retries(_get)
        page += 1
        results.extend(payload.get("results", []))
        if log:
            log(f"  page {page}: {len(results)} row(s) so far")
        next_url = payload.get("next_url")
        next_params = None  # next_url already carries its own full query string
    return results


class MassiveReferenceProvider:
    """Fetches all six reference datasets. `api_key`, if omitted, resolves
    from the MASSIVE_API_KEY env var (see _resolve_api_key()) -- same
    "only ever runs from a local machine or CI" reasoning as
    tam.marketdata.providers.MassiveFlatFileProvider's own S3 credentials,
    just a REST bearer token instead of S3 access keys (a different
    Massive product surface entirely, deliberately not reused)."""

    def __init__(self, api_key: Optional[str] = None, *, client=None, session=None):
        self._api_key = _resolve_api_key(api_key)
        # `client=`/`session=` are test-only seams (inject a fake
        # massive.RESTClient / requests.Session instead of a real one,
        # matching this project's fakes-over-mocking-libraries convention
        # elsewhere, e.g. R2MinuteBarStore's own `client=`) -- production
        # callers never pass either.
        self._client = client
        self._session = session

    def _rest_client(self):
        if self._client is None:
            from massive import RESTClient

            self._client = RESTClient(self._api_key)
        return self._client

    def _http_session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    @staticmethod
    def _to_frame(rows: List[dict], columns: List[str]) -> pd.DataFrame:
        if not rows:
            return schema.empty_frame(columns)
        return pd.DataFrame(rows).reindex(columns=columns)

    # ---- append-only, date-cursor-based ------------------------------------

    def fetch_splits(self, since: Optional[str] = None, *, log: Optional[Callable[[str], None]] = None) -> pd.DataFrame:
        """Every split with execution_date > `since` (all of them, if
        omitted) -- a global feed across every ticker, not per-symbol.
        `log`, if given, gets one line per page fetched (see
        _paginate_raw()'s own docstring for why this is the one fetch
        worth that). Uses `>=`, not `>`, against `since` -- confirmed
        live: execution_date can be a FUTURE-scheduled date (a split
        announced ahead of when it actually executes), so the stored
        cursor can legitimately be a future date too; if that same
        future-dated split gets revised before it executes (its ratio
        changed, say) without its execution_date changing, a strict `>`
        would never re-fetch it, since it's equal to the cursor, not
        greater. `>=` costs re-fetching (and re-writing) the cursor
        date's own rows every run, which write()'s own dedup-on-id
        already makes harmless."""
        params: Dict[str, Any] = {"sort": "execution_date", "order": "asc", "limit": 1000}
        if since:
            params["execution_date.gte"] = since
        raw = _paginate_raw(self._http_session(), f"{_BASE_URL}/stocks/v1/splits", params, self._api_key, log=log)
        return self._to_frame(raw, schema.SPLIT_COLUMNS)

    def fetch_dividends(self, since: Optional[str] = None, *, log: Optional[Callable[[str], None]] = None) -> pd.DataFrame:
        """Every dividend with ex_dividend_date >= `since` (all of them, if
        omitted) -- global feed, typically the largest of the six (see
        _paginate_raw()'s own docstring). `>=` not `>` -- same
        future-scheduled-then-revised reasoning as fetch_splits()."""
        params: Dict[str, Any] = {"sort": "ex_dividend_date", "order": "asc", "limit": 1000}
        if since:
            params["ex_dividend_date.gte"] = since
        raw = _paginate_raw(self._http_session(), f"{_BASE_URL}/stocks/v1/dividends", params, self._api_key, log=log)
        return self._to_frame(raw, schema.DIVIDEND_COLUMNS)

    def fetch_short_volume(self, since: Optional[str] = None) -> pd.DataFrame:
        """Every short-volume row with date >= `since` (all of them, if
        omitted) -- global feed. `>=` not `>` -- FINRA-reported short
        volume/interest figures are occasionally restated for a date
        already published; re-checking the cursor date every run (safe,
        since write() dedups on (ticker, date) keeping the latest) catches
        that, same reasoning as fetch_splits()."""
        kwargs: Dict[str, Any] = {"sort": "date", "order": "asc", "limit": 50000}
        if since:
            kwargs["date_gte"] = since
        rows = [_model_to_dict(row) for row in self._rest_client().list_short_volume(**kwargs)]
        return self._to_frame(rows, schema.SHORT_VOLUME_COLUMNS)

    def fetch_short_interest(self, since: Optional[str] = None) -> pd.DataFrame:
        """Every short-interest row with settlement_date >= `since` (all of
        them, if omitted) -- global feed, biweekly cadence. `>=` not `>`
        -- same restatement reasoning as fetch_short_volume()."""
        kwargs: Dict[str, Any] = {"sort": "settlement_date", "order": "asc", "limit": 50000}
        if since:
            kwargs["settlement_date_gte"] = since
        rows = [_model_to_dict(row) for row in self._rest_client().list_short_interest(**kwargs)]
        return self._to_frame(rows, schema.SHORT_INTEREST_COLUMNS)

    # ---- full-refresh every run ---------------------------------------------

    def fetch_ipos(self) -> pd.DataFrame:
        """The FULL IPO history/current-state table, every run -- IPO
        records are mutable (status transitions rumor/pending->new->
        history, price ranges get revised after announcement), so there's
        no cursor to resume from; see reference_ingest.py's own docstring
        for why this gets overwritten wholesale each time instead."""
        rows = [
            _model_to_dict(row) for row in self._rest_client().vx.list_ipos(order="desc", sort="listing_date", limit=1000)
        ]
        return self._to_frame(rows, schema.IPO_COLUMNS)

    def fetch_float(self) -> pd.DataFrame:
        """The latest free-float snapshot for every ticker -- no
        date-range parameter exists on this endpoint at all (its own docs:
        "retrieve the LATEST free float"), so this is always a full
        re-fetch, same reasoning as fetch_ipos()."""
        rows = [_model_to_dict(row) for row in self._rest_client().list_stocks_floats(limit=5000)]
        return self._to_frame(rows, schema.FLOAT_COLUMNS)

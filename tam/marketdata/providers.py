"""MinuteBarProvider: fetch ALL symbols' 1-minute OHLCV bars for one trading
day from a vendor's bulk flat-file feed -- NOT one REST call per symbol.
Massive (and vendors like it) publishes a single flat file per day (covering
the entire US equities market) over its own S3-compatible bucket;
downloading that one file and filtering it down to the point-in-time
universe (SPY + that day's S&P 500 constituents) is dramatically cheaper
than one API request per symbol per day -- exactly the "bulk historical
files are preferable to enormous numbers of individual API requests"
approach this package is built around.

Filtering to a universe is deliberately NOT this module's job -- fetch()
always returns the full day, unfiltered; tam.marketdata.ingest does the
filtering (against tam.basket.universe.UniverseProvider, reused as-is, not
duplicated). Keeping the provider dumb (one concern: "get me this vendor's
bytes for this day, normalized to our schema") means adding a second vendor
never touches the filtering/validation/store code at all.
"""

from __future__ import annotations

import gzip
import io
import os
from abc import ABC, abstractmethod
from datetime import date

import pandas as pd

from ..registry import Registry
from .schema import (
    ADJ_CLOSE,
    CLOSE,
    HIGH,
    LOW,
    MINUTE_BAR_COLUMNS,
    OPEN,
    SYMBOL,
    TRANSACTIONS,
    TS,
    VOLUME,
    empty_minute_bar_frame,
)


def _resolve_vendor_key(explicit: str | None, env_var: str, vendor: str, kind: str) -> str:
    """Same simple "env var, with a constructor override" convention
    tam.data.providers.FMPProvider already uses for its own API key -- no
    Colab/saved-file resolution here (unlike tam.marketdata.credentials'
    R2 resolution), since a flat-file backfill only ever runs from a local
    machine or CI, never from a research notebook."""
    value = explicit or os.environ.get(env_var)
    if not value:
        raise ValueError(f"{vendor} flat-file {kind} required: set {env_var} env var or pass {kind}=")
    return value


class MinuteBarProvider(ABC):
    """Fetches ALL symbols' 1-minute OHLCV bars for one trading day, indexed
    by `ts` (tz-aware UTC) ascending, columns
    tam.marketdata.schema.MINUTE_BAR_COLUMNS. Returns an empty frame
    (tam.marketdata.schema.empty_minute_bar_frame()) for a day with no data
    (market holiday, not yet published, ...) -- never raises for that case;
    a genuinely missing/corrupt file for a day that SHOULD have data is an
    error tam.marketdata.ingest surfaces via validation, not something this
    layer decides on its own."""

    @abstractmethod
    def fetch(self, day: date) -> pd.DataFrame: ...


@Registry.register(MinuteBarProvider, "flatfile_s3")
class _FlatFileS3Provider(MinuteBarProvider):
    """Shared mechanics for "one flat file per day, over an S3-compatible
    bucket" vendors: download that day's object, gunzip if needed, parse as
    CSV, rename columns per `column_map`, convert the timestamp column to a
    tz-aware UTC index. Every vendor-specific detail (endpoint, bucket, the
    object key's date-based template, column names, timestamp units,
    compression) is a constructor argument, not hardcoded -- a brand new
    vendor with this same "daily flat file" shape is usable immediately via:

        Registry.create(MinuteBarProvider, "flatfile_s3",
            endpoint=..., bucket=..., key_template=..., column_map={...},
            timestamp_column=..., timestamp_unit=...,
            access_key_id=..., secret_access_key=...)

    with no new subclass required -- only worth a named subclass (see
    MassiveFlatFileProvider below) for a vendor used often enough that a
    zero/few-arg constructor is worth having.

    `key_template` is a strftime() template for the per-day object key
    (e.g. "us_stocks_sip/minute_aggs_v1/%Y/%m/%Y-%m-%d.csv.gz"). `column_map`
    renames the vendor's raw CSV columns to this package's canonical names
    (tam.marketdata.schema.SYMBOL/OPEN/HIGH/LOW/CLOSE/VOLUME) -- anything not
    a key in `column_map` is dropped. `timestamp_column` names the RAW
    (pre-rename) column holding each bar's start time; `timestamp_unit` is
    whatever `pandas.to_datetime(..., unit=...)` expects ("ns", "ms", "s",
    or None for an already-parseable string/date column).
    """

    def __init__(
        self,
        *,
        endpoint: str,
        bucket: str,
        key_template: str,
        column_map: dict[str, str],
        timestamp_column: str,
        timestamp_unit: str | None,
        access_key_id: str,
        secret_access_key: str,
        gzip_compressed: bool = True,
    ):
        self._endpoint = endpoint
        self._bucket = bucket
        self._key_template = key_template
        self._column_map = dict(column_map)
        self._timestamp_column = timestamp_column
        self._timestamp_unit = timestamp_unit
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._gzip_compressed = gzip_compressed

    def _filesystem(self):
        from pyarrow import fs

        return fs.S3FileSystem(
            endpoint_override=self._endpoint,
            access_key=self._access_key_id,
            secret_key=self._secret_access_key,
            scheme="https",
            region="auto",
        )

    def fetch(self, day: date) -> pd.DataFrame:
        key = day.strftime(self._key_template)
        path = f"{self._bucket}/{key}"
        try:
            with self._filesystem().open_input_file(path) as handle:
                raw = handle.readall()
        except FileNotFoundError:
            return empty_minute_bar_frame()

        if self._gzip_compressed:
            raw = gzip.decompress(raw)
        raw_df = pd.read_csv(io.BytesIO(raw))
        if raw_df.empty:
            return empty_minute_bar_frame()

        timestamps = pd.to_datetime(raw_df[self._timestamp_column], unit=self._timestamp_unit, utc=True)
        df = raw_df.rename(columns=self._column_map)

        required = [SYMBOL, OPEN, HIGH, LOW, CLOSE, VOLUME]
        missing = [column for column in required if column not in df.columns]
        if missing:
            raise ValueError(
                f"{type(self).__name__}.fetch({day}): column_map doesn't produce required "
                f"column(s) {missing} -- got {list(df.columns)} after mapping (check column_map "
                "against this vendor's current flat-file schema)"
            )
        if ADJ_CLOSE not in df.columns:
            # No adjustment info in the raw feed -- same "adj_close defaults
            # to close" convention tam.data.providers' FMPProvider/
            # YFinanceProvider already use when a source doesn't supply one.
            df[ADJ_CLOSE] = df[CLOSE]
        if TRANSACTIONS not in df.columns:
            # Not every vendor's flat-file schema reports a per-bar trade
            # count -- null, not a fabricated 0 (0 would falsely claim "we
            # know this bar had no trades," when the truth is "this vendor
            # doesn't tell us").
            df[TRANSACTIONS] = pd.NA

        df[TS] = timestamps
        df = df.set_index(TS).sort_index()
        return df[MINUTE_BAR_COLUMNS]


@Registry.register(MinuteBarProvider, "massive_flatfiles")
class MassiveFlatFileProvider(_FlatFileS3Provider):
    """Massive's daily US-stocks 1-minute-aggregates flat file
    (files.massive.com, bucket "flatfiles") -- one file per trading day
    covering the entire market; tam.marketdata.ingest filters it down to
    SPY + that day's point-in-time S&P 500 constituents.

    Endpoint, bucket, object-key template, and CSV column names below are
    verified against Massive's own "Flat Files Quickstart" documentation
    (not just inferred) -- confirmed live too: listed
    flatfiles/us_stocks_sip/minute_aggs_v1/YYYY/MM/YYYY-MM-DD.csv.gz and
    downloaded a real day; its header is exactly
    "ticker,volume,open,close,high,low,window_start,transactions", matching
    every default here. Still constructor-overridable (endpoint/bucket/
    key_template/column_map/timestamp_column/timestamp_unit) in case
    Massive's schema changes later, or you're pointing this at a different
    dataset within the same feed (e.g. day_aggs_v1 instead of
    minute_aggs_v1).

    Needs MASSIVE_S3_ACCESS_KEY_ID / MASSIVE_S3_SECRET_ACCESS_KEY -- from
    your Massive dashboard (Flat Files S3 access, not a REST API key).
    """

    _DEFAULT_ENDPOINT = "https://files.massive.com"
    _DEFAULT_BUCKET = "flatfiles"
    _DEFAULT_KEY_TEMPLATE = "us_stocks_sip/minute_aggs_v1/%Y/%m/%Y-%m-%d.csv.gz"
    _DEFAULT_COLUMN_MAP = {
        "ticker": SYMBOL,
        "open": OPEN,
        "high": HIGH,
        "low": LOW,
        "close": CLOSE,
        "volume": VOLUME,
        "transactions": TRANSACTIONS,
    }
    _DEFAULT_TIMESTAMP_COLUMN = "window_start"
    _DEFAULT_TIMESTAMP_UNIT = "ns"

    def __init__(
        self,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        endpoint: str | None = None,
        bucket: str | None = None,
        key_template: str | None = None,
        column_map: dict[str, str] | None = None,
        timestamp_column: str | None = None,
        timestamp_unit: str | None = None,
    ):
        super().__init__(
            endpoint=endpoint or self._DEFAULT_ENDPOINT,
            bucket=bucket or self._DEFAULT_BUCKET,
            key_template=key_template or self._DEFAULT_KEY_TEMPLATE,
            column_map=column_map or dict(self._DEFAULT_COLUMN_MAP),
            timestamp_column=timestamp_column or self._DEFAULT_TIMESTAMP_COLUMN,
            timestamp_unit=self._DEFAULT_TIMESTAMP_UNIT if timestamp_unit is None else timestamp_unit,
            access_key_id=_resolve_vendor_key(access_key_id, "MASSIVE_S3_ACCESS_KEY_ID", "Massive", "access_key_id"),
            secret_access_key=_resolve_vendor_key(
                secret_access_key, "MASSIVE_S3_SECRET_ACCESS_KEY", "Massive", "secret_access_key"
            ),
        )

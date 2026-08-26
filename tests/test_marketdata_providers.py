import gzip
import os
from datetime import date

import pandas as pd
import pyarrow.fs as fs
import pytest

from tam.marketdata.providers import MassiveFlatFileProvider, MinuteBarProvider, _FlatFileS3Provider
from tam.registry import Registry


class _LocalFlatFileProvider(_FlatFileS3Provider):
    """Same "one flat file per day, over an S3-compatible bucket" mechanics
    _FlatFileS3Provider implements for real vendors, pointed at a plain
    local directory instead of a network endpoint -- keeps these tests
    network-free while still exercising the real gunzip/CSV-parse/
    column-rename/timestamp logic, not a separate reimplementation of it."""

    def __init__(self, root, **overrides):
        defaults = dict(
            endpoint="unused",
            bucket=str(root),
            key_template="%Y-%m-%d.csv.gz",
            column_map={
                "ticker": "symbol",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
                "transactions": "transactions",
            },
            timestamp_column="window_start",
            timestamp_unit="ns",
            access_key_id="unused",
            secret_access_key="unused",
        )
        defaults.update(overrides)
        super().__init__(**defaults)

    def _filesystem(self):
        return fs.LocalFileSystem()


def _write_flat_file(root, day: str, csv_body: str) -> None:
    path = os.path.join(str(root), f"{day}.csv.gz")
    with open(path, "wb") as handle:
        handle.write(gzip.compress(csv_body.encode()))


def test_fetch_parses_and_normalizes_a_days_flat_file(tmp_path):
    _write_flat_file(
        tmp_path,
        "2024-01-02",
        "ticker,volume,open,close,high,low,window_start,transactions\n"
        "AAPL,100,1.0,1.5,1.6,0.9,1577958600000000000,5\n"
        "SPY,200,2.0,2.5,2.6,1.9,1577958660000000000,7\n",
    )
    provider = _LocalFlatFileProvider(tmp_path)

    df = provider.fetch(date(2024, 1, 2))

    assert list(df["symbol"]) == ["AAPL", "SPY"]
    assert str(df.index.tz) == "UTC"
    assert df.index.is_monotonic_increasing
    # adj_close defaults to close when the raw feed has no adjustment column
    assert list(df["adj_close"]) == list(df["close"])
    # transactions (trade count per bar) is preserved, not dropped
    assert list(df["transactions"]) == [5, 7]


def test_fetch_defaults_transactions_to_na_when_the_vendor_doesnt_provide_it(tmp_path):
    _write_flat_file(
        tmp_path,
        "2024-01-02",
        "ticker,volume,open,close,high,low,window_start\nAAPL,100,1.0,1.5,1.6,0.9,1577958600000000000\n",
    )
    provider = _LocalFlatFileProvider(
        tmp_path,
        column_map={"ticker": "symbol", "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"},
    )

    df = provider.fetch(date(2024, 1, 2))

    assert df["transactions"].isna().all()


def test_fetch_returns_empty_frame_for_a_missing_day(tmp_path):
    provider = _LocalFlatFileProvider(tmp_path)

    df = provider.fetch(date(2024, 1, 3))

    assert df.empty


def test_fetch_raises_clearly_when_column_map_misses_a_required_column(tmp_path):
    _write_flat_file(tmp_path, "2024-01-02", "ticker,volume,window_start\nAAPL,100,1577958600000000000\n")
    provider = _LocalFlatFileProvider(
        tmp_path,
        column_map={"ticker": "symbol", "volume": "volume"},  # no open/high/low/close mapping
    )

    with pytest.raises(ValueError, match="required column"):
        provider.fetch(date(2024, 1, 2))


def test_flatfile_s3_and_massive_providers_are_registered():
    assert "flatfile_s3" in Registry.names(MinuteBarProvider)
    assert "massive_flatfiles" in Registry.names(MinuteBarProvider)


def test_massive_provider_requires_s3_credentials(monkeypatch):
    monkeypatch.delenv("MASSIVE_S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("MASSIVE_S3_SECRET_ACCESS_KEY", raising=False)

    with pytest.raises(ValueError, match="MASSIVE_S3_ACCESS_KEY_ID"):
        MassiveFlatFileProvider()


def test_massive_provider_uses_documented_defaults(monkeypatch):
    monkeypatch.setenv("MASSIVE_S3_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("MASSIVE_S3_SECRET_ACCESS_KEY", "secret")

    provider = MassiveFlatFileProvider()

    assert provider._endpoint == "https://files.massive.com"
    assert provider._bucket == "flatfiles"
    assert provider._key_template == "us_stocks_sip/minute_aggs_v1/%Y/%m/%Y-%m-%d.csv.gz"

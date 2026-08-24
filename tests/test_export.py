"""Standalone data export (tam/data/export.py) -- fetch/cache reuse, the
transform (UDF) hook, and the Registry(FileFormat, ...) output-format lookup.
Follows tests/test_data.py's FakeProvider/_bars convention so this never
touches the network.
"""
from datetime import date

import pandas as pd
import pytest

from tam.data.export import FileFormat, export_history, run_export
from tam.data.providers import DataProvider
from tam.data.schema import OHLCV_COLUMNS
from tam.registry import Registry


class FakeProvider(DataProvider):
    """Deterministic in-memory provider so tests never touch the network."""

    def __init__(self, frame: pd.DataFrame):
        self._frame = frame
        self.calls = []

    def fetch_eod(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        df = self._frame
        return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]


def _bars(dates, closes):
    index = pd.to_datetime(dates)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "adj_close": closes,
            "volume": [1_000] * len(closes),
        },
        index=index,
    ).rename_axis("date")[OHLCV_COLUMNS]


_DATES = ["2024-01-02", "2024-01-03", "2024-01-04"]
_CLOSES = [100.0, 110.0, 90.0]


@Registry.register(DataProvider, "fake_export_test_provider")
class _RegisteredFakeProvider(FakeProvider):
    """Registered once at import time so run_export()'s config-driven path
    (which resolves providers by name, not by direct construction) can use it."""

    def __init__(self):
        super().__init__(_bars(_DATES, _CLOSES))


def test_export_history_writes_a_flat_csv_with_transform_applied(tmp_path):
    provider = FakeProvider(_bars(_DATES, _CLOSES))

    # export_history() resolves its provider via Registry.get(DataProvider, name)
    # (a cached singleton, no-arg construct) -- registering a zero-arg lambda
    # that returns THIS instance is a cheap way to inject a fake with a fresh,
    # per-test `.calls` list under a name unique to this test, without needing
    # a real class declaration or module-level sharing across tests.
    Registry.register(DataProvider, "fake_export_history_direct")(lambda: provider)
    out_path = tmp_path / "out.csv"

    result = export_history(
        "AAPL",
        date(2024, 1, 2),
        date(2024, 1, 4),
        str(out_path),
        provider="fake_export_history_direct",
        cache_root=str(tmp_path / "cache"),
        transform=lambda df: df.assign(ret=df["close"].pct_change()),
    )

    assert result == out_path
    written = pd.read_csv(out_path, index_col="date", parse_dates=["date"])
    assert list(written["close"]) == _CLOSES
    assert written["ret"].isna().iloc[0]
    assert written["ret"].iloc[1] == pytest.approx(0.10)


def test_export_history_reuses_the_cache_on_a_second_call(tmp_path):
    provider = FakeProvider(_bars(_DATES, _CLOSES))
    Registry.register(DataProvider, "fake_export_history_cache_reuse")(lambda: provider)
    cache_root = str(tmp_path / "cache")

    export_history(
        "AAPL", date(2024, 1, 2), date(2024, 1, 4), str(tmp_path / "one.csv"),
        provider="fake_export_history_cache_reuse", cache_root=cache_root,
    )
    export_history(
        "AAPL", date(2024, 1, 2), date(2024, 1, 4), str(tmp_path / "two.csv"),
        provider="fake_export_history_cache_reuse", cache_root=cache_root,
    )

    assert len(provider.calls) == 1  # second export_history() call didn't re-fetch


def test_export_history_infers_format_from_path_suffix(tmp_path):
    provider = FakeProvider(_bars(_DATES, _CLOSES))
    Registry.register(DataProvider, "fake_export_history_format_infer")(lambda: provider)

    parquet_path = export_history(
        "AAPL", date(2024, 1, 2), date(2024, 1, 4), str(tmp_path / "out.parquet"),
        provider="fake_export_history_format_infer", cache_root=str(tmp_path / "cache"),
    )

    assert pd.read_parquet(parquet_path)["close"].tolist() == _CLOSES


def test_export_history_explicit_format_overrides_suffix(tmp_path):
    provider = FakeProvider(_bars(_DATES, _CLOSES))
    Registry.register(DataProvider, "fake_export_history_format_override")(lambda: provider)

    out_path = export_history(
        "AAPL", date(2024, 1, 2), date(2024, 1, 4), str(tmp_path / "out.dat"),
        provider="fake_export_history_format_override", cache_root=str(tmp_path / "cache"),
        format="csv",
    )

    assert pd.read_csv(out_path)["close"].tolist() == _CLOSES


def test_export_history_rejects_an_unregistered_format(tmp_path):
    provider = FakeProvider(_bars(_DATES, _CLOSES))
    Registry.register(DataProvider, "fake_export_history_bad_format")(lambda: provider)

    with pytest.raises(ValueError, match="csv.*parquet|parquet.*csv"):
        export_history(
            "AAPL", date(2024, 1, 2), date(2024, 1, 4), str(tmp_path / "out.xyz"),
            provider="fake_export_history_bad_format", cache_root=str(tmp_path / "cache"),
        )


def test_file_format_registry_has_csv_and_parquet_built_in():
    assert set(Registry.names(FileFormat)) >= {"csv", "parquet"}


def test_run_export_reads_data_and_export_sections_from_config(tmp_path):
    out_path = tmp_path / "out.csv"
    config_path = tmp_path / "cfg.yaml"
    config_path.write_text(
        f"""
data:
  provider: fake_export_test_provider
  store: csv
  root: {tmp_path / "cache"}
export:
  symbol: AAPL
  start: "2024-01-02"
  end: "2024-01-04"
  path: {out_path}
"""
    )

    result = run_export(config_path, transform=lambda df: df.assign(doubled=df["close"] * 2))

    assert result == out_path
    written = pd.read_csv(out_path)
    assert written["doubled"].tolist() == [c * 2 for c in _CLOSES]

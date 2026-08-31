"""Tests for MarketDataMinuteBarSource -- the self-service minute-bar read
side, same thread-safety fix (thread_local_connection()) as
tam.marketdata.eod_provider and for the identical reason."""

from __future__ import annotations

import threading
from datetime import date

import pandas as pd
import pytest

from tam.marketdata.minute_source import MarketDataMinuteBarSource, MinuteBarSource
from tam.registry import Registry


class _FakeSymbol:
    """Stands in for tam.Symbol -- records the connection/engine it was
    given and returns a canned minute_bars() frame."""

    calls: list = []

    def __init__(self, symbol, con=None):
        self.symbol = symbol
        self.con = con

    def minute_bars(self, start=None, end=None, engine="pandas"):
        type(self).calls.append((self.symbol, self.con, engine, threading.current_thread().name))
        idx = pd.date_range("2024-01-02 14:30", periods=3, freq="1min", tz="UTC")
        return pd.DataFrame({"close": [100.0, 100.5, 100.2]}, index=idx)


@pytest.fixture(autouse=True)
def _patch_symbol(monkeypatch):
    _FakeSymbol.calls = []
    monkeypatch.setattr("tam.symbol.Symbol", _FakeSymbol)
    from tam.marketdata import connection

    monkeypatch.setattr(connection, "_thread_local", threading.local())
    monkeypatch.setattr(connection, "resolve_connection", lambda **kwargs: object())
    yield


def test_registered_under_marketdata():
    assert Registry.get(MinuteBarSource, "marketdata") is not None


def test_fetch_minute_bars_returns_the_underlying_frame():
    source = MarketDataMinuteBarSource()

    result = source.fetch_minute_bars("AAPL", date(2024, 1, 2), date(2024, 1, 2))

    assert len(result) == 3
    assert list(result.columns) == ["close"]


def test_fetch_minute_bars_passes_engine_through():
    source = MarketDataMinuteBarSource()

    source.fetch_minute_bars("AAPL", date(2024, 1, 2), date(2024, 1, 2), engine="polars")

    assert _FakeSymbol.calls[-1][2] == "polars"


def test_fetch_minute_bars_uses_a_thread_local_connection():
    source = MarketDataMinuteBarSource()

    def worker(symbol):
        source.fetch_minute_bars(symbol, date(2024, 1, 2), date(2024, 1, 2))

    threads = [threading.Thread(target=worker, args=(f"T{i}",), name=f"thread-{i}") for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    connections = {con for _, con, _, _ in _FakeSymbol.calls}
    assert len(connections) == 6  # every thread got a DIFFERENT connection object

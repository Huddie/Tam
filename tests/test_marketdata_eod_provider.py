"""Tests for MarketDataEodProvider -- fetch_eod() shape/empty-frame handling,
and the actual thread-safety fix (thread_local_connection(), not
default_connection()) since that's the whole reason this module exists."""

from __future__ import annotations

import threading
from datetime import date

import pandas as pd
import pytest

from tam.data.providers import DataProvider
from tam.data.schema import DATE, OHLCV_COLUMNS
from tam.marketdata.eod_provider import MarketDataEodProvider
from tam.registry import Registry


class _FakeSymbol:
    """Stands in for tam.Symbol -- records the connection it was given and
    returns a canned eod_bars() frame (or empty, per test)."""

    calls: list = []

    def __init__(self, symbol, con=None):
        self.symbol = symbol
        self.con = con

    def eod_bars(self, start=None, end=None):
        type(self).calls.append((self.symbol, self.con, threading.current_thread().name))
        if self.symbol == "EMPTY":
            return pd.DataFrame(columns=[DATE, *OHLCV_COLUMNS])
        idx = pd.to_datetime([date(2024, 1, 1), date(2024, 1, 2)])
        return pd.DataFrame(
            {
                DATE: idx,
                "open": [1.0, 2.0],
                "high": [1.5, 2.5],
                "low": [0.5, 1.5],
                "close": [1.2, 2.2],
                "adj_close": [1.2, 2.2],
                "volume": [100, 200],
            }
        )


@pytest.fixture(autouse=True)
def _patch_symbol(monkeypatch):
    _FakeSymbol.calls = []
    monkeypatch.setattr("tam.symbol.Symbol", _FakeSymbol)
    # Real thread_local_connection() left in place (that's what's under test
    # below) -- only its own resolve_connection() call is stubbed so no
    # real TAM_PAT/network is needed.
    from tam.marketdata import connection

    monkeypatch.setattr(connection, "_thread_local", threading.local())
    monkeypatch.setattr(connection, "resolve_connection", lambda **kwargs: object())
    yield


def test_registered_under_marketdata_eod():
    assert Registry.get(DataProvider, "marketdata_eod") is not None


def test_fetch_eod_returns_ohlcv_frame_indexed_by_date():
    provider = MarketDataEodProvider()

    result = provider.fetch_eod("AAPL", date(2024, 1, 1), date(2024, 1, 2))

    assert list(result.columns) == OHLCV_COLUMNS
    assert result.index.name == DATE
    assert len(result) == 2


def test_fetch_eod_returns_empty_ohlcv_frame_when_symbol_has_no_data():
    provider = MarketDataEodProvider()

    result = provider.fetch_eod("EMPTY", date(2024, 1, 1), date(2024, 1, 2))

    assert result.empty
    assert list(result.columns) == OHLCV_COLUMNS


def test_fetch_eod_uses_a_thread_local_connection_not_the_shared_default(monkeypatch):
    # The actual bug this module exists to avoid: every call must get a
    # connection scoped to ITS OWN thread, never the same object another
    # thread is using concurrently.
    from tam.marketdata import connection

    monkeypatch.setattr(connection, "_thread_local", threading.local())
    monkeypatch.setattr(connection, "resolve_connection", lambda **kwargs: object())

    provider = MarketDataEodProvider()

    def worker(symbol):
        provider.fetch_eod(symbol, date(2024, 1, 1), date(2024, 1, 2))

    threads = [threading.Thread(target=worker, args=(f"T{i}",), name=f"thread-{i}") for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(_FakeSymbol.calls) == 6
    connections_by_thread = {thread_name: con for _, con, thread_name in _FakeSymbol.calls}
    # every thread got a DIFFERENT connection object -- never shared
    assert len({id(con) for con in connections_by_thread.values()}) == 6

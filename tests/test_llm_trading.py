from datetime import date, timedelta

import pandas as pd
import pytest

from tam.backtest.harness import BacktestHarness
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.schema import OHLCV_COLUMNS
from tam.data.storage import CsvStore
from tam.portfolio.orders import Side
from tam.portfolio.portfolio import Portfolio
from tam.strategy.llm_trading import LLMTradingStrategy


class MultiTickerProvider(DataProvider):
    def __init__(self, series: dict):
        self._series = series

    def fetch_eod(self, symbol, start, end):
        dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        closes = self._series.get(symbol, [10.0] * len(dates))
        return _bars(dates, closes[: len(dates)])


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


def _setup(tmp_path, closes, dates):
    store = CsvStore(tmp_path)
    provider = MultiTickerProvider({"QQQ": closes})
    repo = DataRepository(provider, store)
    repo.ingest(["QQQ", "TQQQ", "SQQQ"], dates[0], dates[-1])
    return repo


def _dates(n, start=date(2024, 1, 2)):
    return [start + timedelta(days=i) for i in range(n)]


def test_always_long_client_buys_and_never_flips(tmp_path):
    closes = [100.0 + i for i in range(30)]
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", buy_qty=10, sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "LONG", lookback=5,
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert strategy._held == "long"
    assert all(t.ticker == "TQQQ" and t.side == Side.BUY for t in portfolio.trades)
    assert len(portfolio.trades) == 1  # only the initial entry -- never flips


def test_client_flips_between_long_and_short_each_call(tmp_path):
    closes = [100.0 + i for i in range(30)]
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    calls = {"n": 0}

    def alternating_client(prompt):
        calls["n"] += 1
        return "LONG" if calls["n"] % 2 == 1 else "SHORT"

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", buy_qty=10, sell_qty=10, portfolio_id="main",
        llm_client=alternating_client, lookback=5,
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    sides_and_tickers = [(t.side, t.ticker) for t in portfolio.trades]
    # First entry: BUY TQQQ. Then every subsequent day flips: SELL TQQQ, BUY SQQQ,
    # SELL SQQQ, BUY TQQQ, ...
    assert sides_and_tickers[0] == (Side.BUY, "TQQQ")
    assert (Side.SELL, "TQQQ") in sides_and_tickers
    assert (Side.BUY, "SQQQ") in sides_and_tickers


def test_failing_client_falls_back_to_current_holding_without_crashing(tmp_path):
    closes = [100.0 + i for i in range(30)]
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    def flaky_client(prompt):
        raise ConnectionError("model server not running")

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", buy_qty=10, sell_qty=10, portfolio_id="main",
        llm_client=flaky_client, lookback=5,
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()  # must not raise

    # Falls back to "long" (the hardcoded default) on the very first day since
    # there's no prior holding yet, then never flips again since every call fails.
    assert strategy._held == "long"
    assert len(portfolio.trades) == 1


def test_unparseable_response_falls_back_to_current_holding(tmp_path):
    closes = [100.0 + i for i in range(30)]
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", buy_qty=10, sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "I'm not sure, maybe go long? or short? unclear.",
        lookback=5,
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    # Ambiguous text (mentions both words) never resolves to a side, so it always
    # falls back to the hardcoded "long" default and never trades again after entry.
    assert strategy._held == "long"
    assert len(portfolio.trades) == 1


def test_prompt_includes_growing_track_record(tmp_path):
    closes = [100.0 + i for i in range(30)]
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    prompts = []

    def recording_client(prompt):
        prompts.append(prompt)
        return "LONG"

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", buy_qty=10, sell_qty=10, portfolio_id="main",
        llm_client=recording_client, lookback=5,
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert "(no track record yet)" in prompts[0]
    assert "Recent hit rate" in prompts[-1]
    assert "predicted LONG, actual was LONG (correct)" in prompts[-1]


def test_no_trade_before_lookback_is_satisfied(tmp_path):
    closes = [100.0, 101.0, 102.0]
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", buy_qty=10, sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "LONG", lookback=20,
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert portfolio.trades == []


def test_calls_record_outcome_on_clients_that_support_it(tmp_path):
    closes = [100.0 + i for i in range(30)]
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    recorded = []

    class LearningClient:
        def __call__(self, prompt):
            return "LONG"

        def record_outcome(self, prompt, realized_side):
            recorded.append((prompt, realized_side))

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", buy_qty=10, sell_qty=10, portfolio_id="main",
        llm_client=LearningClient(), lookback=5,
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    # A steadily-increasing series always realizes "long" the day after any call.
    assert recorded
    assert all(side == "long" for _, side in recorded)


def test_plain_callable_client_without_record_outcome_still_works(tmp_path):
    closes = [100.0 + i for i in range(30)]
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", buy_qty=10, sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "LONG", lookback=5,
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()  # must not raise despite the plain callable having no record_outcome

    assert strategy._held == "long"


def test_get_state_and_load_state_round_trip_preserves_memory_and_pending(tmp_path):
    closes = [100.0 + i for i in range(30)]
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", buy_qty=10, sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "LONG", lookback=5, memory_window=3,
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert len(strategy._memory) > 0

    restored = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", buy_qty=10, sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "LONG", lookback=5, memory_window=3,
    )
    restored.load_state(strategy.get_state())

    assert restored._held == strategy._held
    assert restored._pending == strategy._pending
    assert list(restored._memory) == list(strategy._memory)
    assert restored._memory.maxlen == 3


def test_get_state_delegates_to_a_client_that_supports_it(tmp_path):
    closes = [100.0 + i for i in range(30)]
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    class StatefulClient:
        def __init__(self):
            self.loaded_with = None

        def __call__(self, prompt):
            return "LONG"

        def get_state(self):
            return {"trained_on": 7}

        def load_state(self, state):
            self.loaded_with = state

    client = StatefulClient()
    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", buy_qty=10, sell_qty=10, portfolio_id="main",
        llm_client=client, lookback=5,
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    state = strategy.get_state()
    assert state["llm_client"] == {"trained_on": 7}

    restored_client = StatefulClient()
    restored = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", buy_qty=10, sell_qty=10, portfolio_id="main",
        llm_client=restored_client, lookback=5,
    )
    restored.load_state(state)

    assert restored_client.loaded_with == {"trained_on": 7}

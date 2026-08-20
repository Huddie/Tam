from datetime import date, timedelta

import numpy as np
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
from tam.strategy.signals import build_signals


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


def _trending_closes(n, seed=0):
    rng = np.random.default_rng(seed)
    steps = rng.normal(loc=0.15, scale=1.0, size=n)
    return list(100.0 + np.cumsum(steps))


# Small windows so a short synthetic series clears the required-history bar
# (max(signal.required_history() for signal in signals) + history_window).
_SMALL_SIGNAL_SPECS = [
    {"id": "sma", "config": {"window": 5}},
    {"id": "sma", "config": {"window": 10}},
    {"id": "zscore", "config": {"window": 5}},
    {"id": "rsi", "config": {}},
    {"id": "return", "config": {"horizon": 1}},
    {"id": "return", "config": {"horizon": 5}},
    {"id": "volatility", "config": {"window": 20}},
]


def _small_windows():
    return dict(signals=build_signals(_SMALL_SIGNAL_SPECS), history_window=3)


def test_no_trade_before_the_fastest_signal_has_any_history(tmp_path):
    closes = [100.0]  # 1 day -- even the fastest signal (return_1d, needs 2) isn't ready
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "80", **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert portfolio.trades == []


def test_starts_trading_as_soon_as_the_fastest_signal_is_ready(tmp_path):
    # 6 days -- well short of sma_10's 10-day requirement, but past return_1d's 2.
    closes = _trending_closes(6)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "80", **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert portfolio.trades  # trades despite sma_10/rsi_14/volatility_20d never warming up


def test_prompt_shows_a_placeholder_for_signals_not_yet_warmed_up(tmp_path):
    closes = _trending_closes(6)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    prompts = []

    def recording_client(prompt):
        prompts.append(prompt)
        return "10"

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=recording_client, **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert prompts
    assert "n/a (needs" in prompts[0]  # sma_10/rsi_14/volatility_20d not ready yet
    assert "return_1d" in prompts[0]   # the fast one has real values


def test_positive_output_buys_tqqq_at_the_suggested_percentage(tmp_path):
    closes = _trending_closes(40)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "80", **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert strategy._current_pct == 80.0
    assert portfolio.trades
    first_trade = portfolio.trades[0]
    assert first_trade.ticker == "TQQQ" and first_trade.side == Side.BUY
    assert first_trade.qty * first_trade.price == pytest.approx(10_000.0 * 0.80, rel=1e-6)


def test_negative_output_buys_sqqq_at_the_suggested_percentage(tmp_path):
    closes = _trending_closes(40)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "-65", **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert strategy._current_pct == -65.0
    first_trade = portfolio.trades[0]
    assert first_trade.ticker == "SQQQ" and first_trade.side == Side.BUY
    assert first_trade.qty * first_trade.price == pytest.approx(10_000.0 * 0.65, rel=1e-6)


def test_zero_output_stays_in_cash(tmp_path):
    closes = _trending_closes(40)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "0", **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert strategy._current_pct == 0.0
    assert portfolio.trades == []


def test_small_change_below_threshold_does_not_rebalance(tmp_path):
    closes = _trending_closes(40)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    calls = {"n": 0}

    def drifting_client(prompt):
        calls["n"] += 1
        return "50" if calls["n"] == 1 else "52"  # +2pp drift, below the 5pp threshold

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=drifting_client, rebalance_threshold_pct=5.0, **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert strategy._current_pct == 50.0  # never moved to 52
    assert len(portfolio.trades) == 1  # only the initial entry


def test_large_change_crossing_zero_flips_from_long_to_short(tmp_path):
    closes = _trending_closes(40)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    calls = {"n": 0}

    def flipping_client(prompt):
        calls["n"] += 1
        return "70" if calls["n"] == 1 else "-40"

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=flipping_client, **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    tickers_and_sides = [(t.ticker, t.side) for t in portfolio.trades]
    assert ("TQQQ", Side.BUY) in tickers_and_sides
    assert ("TQQQ", Side.SELL) in tickers_and_sides
    assert ("SQQQ", Side.BUY) in tickers_and_sides
    assert strategy._current_pct == -40.0


def test_failing_client_falls_back_to_current_exposure_without_crashing(tmp_path):
    closes = _trending_closes(40)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    def flaky_client(prompt):
        raise ConnectionError("model server not running")

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=flaky_client, **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()  # must not raise

    # Every call fails -> always falls back to whatever's currently held, which
    # starts at cash (0) and never changes.
    assert strategy._current_pct == 0.0
    assert portfolio.trades == []


def test_unparseable_response_falls_back_to_current_exposure(tmp_path):
    closes = _trending_closes(40)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "I'm not sure what to suggest here.",
        **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert strategy._current_pct == 0.0
    assert portfolio.trades == []


def test_out_of_range_output_is_clipped_to_100(tmp_path):
    closes = _trending_closes(40)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "250", **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert strategy._current_pct == 100.0


def test_prompt_includes_signal_history_and_calibration_track_record(tmp_path):
    closes = _trending_closes(40)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    prompts = []

    def recording_client(prompt):
        prompts.append(prompt)
        return "10"

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=recording_client, **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert "price_vs_sma_5" in prompts[0]
    assert "zscore_vs_sma_5" in prompts[0]
    assert "rsi_14" in prompts[0]
    assert "return_1d" in prompts[0]
    assert "volatility_20d" in prompts[0]
    assert "(none yet)" in prompts[0]
    assert "vs ideal" in prompts[-1]
    assert "MAE:" in prompts[-1]


def test_calls_record_outcome_with_the_hindsight_optimal_percentage(tmp_path):
    closes = [100.0 * (1.01 ** i) for i in range(40)]  # steady +1%/day
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    recorded = []

    class LearningClient:
        def __call__(self, prompt):
            return "10"

        def record_outcome(self, prompt, completion):
            recorded.append(completion)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=LearningClient(), target_daily_vol=0.01, **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    # A steady +1%/day series with target_daily_vol=0.01 -> hindsight-optimal
    # exposure clips to +100 every time.
    assert recorded
    assert all(value == "+100" for value in recorded)


def test_plain_callable_client_without_record_outcome_still_works(tmp_path):
    closes = _trending_closes(40)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "30", **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()  # must not raise despite the plain callable having no record_outcome

    assert strategy._current_pct == 30.0


def test_get_state_and_load_state_round_trip(tmp_path):
    closes = _trending_closes(40)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "42", memory_window=3, **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert len(strategy._memory) > 0

    restored = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "42", memory_window=3, **_small_windows(),
    )
    restored.load_state(strategy.get_state())

    assert restored._current_pct == strategy._current_pct
    assert restored._pending == strategy._pending
    assert list(restored._memory) == list(strategy._memory)
    assert restored._memory.maxlen == 3


def test_get_state_delegates_to_a_client_that_supports_it(tmp_path):
    closes = _trending_closes(40)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    class StatefulClient:
        def __init__(self):
            self.loaded_with = None

        def __call__(self, prompt):
            return "20"

        def get_state(self):
            return {"trained_on": 7}

        def load_state(self, state):
            self.loaded_with = state

    client = StatefulClient()
    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=client, **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    state = strategy.get_state()
    assert state["llm_client"] == {"trained_on": 7}

    restored_client = StatefulClient()
    restored = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=restored_client, **_small_windows(),
    )
    restored.load_state(state)

    assert restored_client.loaded_with == {"trained_on": 7}

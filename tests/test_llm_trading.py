import csv
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
from tam.strategy.llm_trading import LLMTradingStrategy, build_llm_trading
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
    return dict(signals=build_signals(_SMALL_SIGNAL_SPECS), history_window=3, vol_window=5)


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


def test_zero_threshold_disables_it_trading_on_any_nonzero_change(tmp_path):
    closes = _trending_closes(40)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    calls = {"n": 0}

    def drifting_client(prompt):
        calls["n"] += 1
        return {1: "50", 2: "50", 3: "50.5"}.get(calls["n"], "50.5")

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=drifting_client, rebalance_threshold_pct=0, **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert strategy._current_pct == 50.5
    # Entry (call 1), no-op on the unchanged repeat (call 2), then a real
    # rebalance on the +0.5pp change (call 3) -- even that tiny a move trades
    # once the threshold is off.
    assert len(portfolio.trades) == 3  # BUY entry, SELL+BUY to resize


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
    assert "ideal" in prompts[-1]
    assert "MAE" in prompts[-1]


def test_calls_record_outcome_with_the_hindsight_optimal_percentage(tmp_path):
    # A strong uptrend with a little day-to-day noise -- realistic enough that
    # trailing vol is computable (a perfectly constant series has zero
    # variance, which can't scale a label at all -- see
    # test_resolve_pending_skips_a_degenerate_zero_variance_window below) --
    # and the noise is tiny next to the drift, so every day's move clips to
    # the max label either way.
    rng = np.random.default_rng(1)
    closes = [100.0]
    for _ in range(39):
        closes.append(closes[-1] * (1.01 + rng.normal(0, 0.0005)))
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
        llm_client=LearningClient(), **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert recorded
    assert all(value == "+100" for value in recorded)


def test_hindsight_label_adapts_to_the_current_volatility_regime(tmp_path):
    # Same-sized 1% move, shown twice: once after a calm trailing window,
    # once after a turbulent one. An adaptive label should treat the calm-
    # window move as much higher conviction (closer to the +/-100 clip) than
    # the identical move seen right after a turbulent window.
    strategy = LLMTradingStrategy.__new__(LLMTradingStrategy)

    calm = pd.Series([100.0, 100.01, 99.99, 100.02, 99.98, 100.0, 101.0])
    turbulent = pd.Series([100.0, 108.0, 93.0, 109.0, 91.0, 106.0, 107.0])
    strategy._vol_window = 5

    calm_vol = strategy._trailing_daily_vol(calm)
    turbulent_vol = strategy._trailing_daily_vol(turbulent)

    assert calm_vol < turbulent_vol
    # Same realized_return (+1%) divided by a smaller vol -> a bigger |ideal_pct|.
    assert (0.01 / calm_vol) > (0.01 / turbulent_vol)


def test_trailing_daily_vol_is_none_for_a_degenerate_zero_variance_window(tmp_path):
    strategy = LLMTradingStrategy.__new__(LLMTradingStrategy)
    strategy._vol_window = 5

    flat = pd.Series([100.0 * (1.01 ** i) for i in range(7)])  # identical return every day -> zero variance

    assert strategy._trailing_daily_vol(flat) is None


def test_trailing_daily_vol_is_none_before_enough_history(tmp_path):
    strategy = LLMTradingStrategy.__new__(LLMTradingStrategy)
    strategy._vol_window = 5

    assert strategy._trailing_daily_vol(pd.Series([100.0, 101.0, 99.0])) is None


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


def test_log_path_writes_a_header_and_one_row_per_call(tmp_path):
    closes = _trending_closes(10)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)
    log_path = tmp_path / "llm_log.csv"

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "42", log_path=str(log_path), **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert log_path.exists()
    with log_path.open(newline="") as handle:
        rows = list(csv.reader(handle))

    assert rows[0] == ["iteration", "datetime", "prompt", "response", "warmup", "retried"]
    assert len(rows) > 1
    assert [r[0] for r in rows[1:]] == [str(i) for i in range(1, len(rows))]  # 1, 2, 3, ...
    assert all(r[3] == "42" for r in rows[1:])
    assert all(r[4] == "False" for r in rows[1:])  # no warmup_days set -> never warming up
    assert all(r[5] == "False" for r in rows[1:])  # clean single-number responses -> never retried


def test_multiple_numbers_in_response_triggers_one_retry_and_uses_its_answer():
    prompts_seen = []

    def rambling_then_clean_client(prompt):
        prompts_seen.append(prompt)
        return "63.60 66.10 68.8" if len(prompts_seen) == 1 else "42"

    strategy = LLMTradingStrategy(
        None, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=rambling_then_clean_client, **_small_windows(),
    )

    target_pct, raw, retried = strategy._ask_llm("original prompt")

    assert target_pct == 42.0  # used the retry's clean answer, not the first number it rambled
    assert raw == "42"
    assert retried is True
    assert len(prompts_seen) == 2
    assert prompts_seen[1].startswith("original prompt")
    assert "63.60 66.10 68.8" in prompts_seen[1]  # retry shows the model its own bad response
    assert "exactly one number" in prompts_seen[1]


def test_unparseable_response_also_triggers_a_retry():
    calls = {"n": 0}

    def confused_then_clean_client(prompt):
        calls["n"] += 1
        return "I'm not sure" if calls["n"] == 1 else "-55"

    strategy = LLMTradingStrategy(
        None, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=confused_then_clean_client, **_small_windows(),
    )

    target_pct, raw, retried = strategy._ask_llm("prompt")

    assert calls["n"] == 2  # exactly one retry, not a loop
    assert target_pct == -55.0
    assert retried is True


def test_retry_that_also_fails_gives_up_without_looping():
    calls = {"n": 0}

    def always_rambling_client(prompt):
        calls["n"] += 1
        return "1 2 3"

    strategy = LLMTradingStrategy(
        None, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=always_rambling_client, **_small_windows(),
    )

    target_pct, raw, retried = strategy._ask_llm("prompt")

    assert calls["n"] == 2  # 1 call + 1 retry, never more -- confirms no retry loop
    assert target_pct is None  # caller falls back to holding current exposure
    assert raw == "1 2 3"      # the retry's (still bad) response, for the log
    assert retried is True


def test_hard_failure_does_not_trigger_a_retry():
    calls = {"n": 0}

    def flaky_client(prompt):
        calls["n"] += 1
        raise ConnectionError("model server not running")

    strategy = LLMTradingStrategy(
        None, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=flaky_client, **_small_windows(),
    )

    target_pct, raw, retried = strategy._ask_llm("prompt")

    assert calls["n"] == 1  # no retry on a hard failure
    assert target_pct is None
    assert raw.startswith("ERROR:")
    assert retried is False


def test_log_path_records_error_for_a_failing_call(tmp_path):
    closes = _trending_closes(10)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)
    log_path = tmp_path / "llm_log.csv"

    def flaky_client(prompt):
        raise ConnectionError("model server not running")

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=flaky_client, log_path=str(log_path), **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    with log_path.open(newline="") as handle:
        rows = list(csv.reader(handle))

    assert len(rows) > 1
    assert all(r[3].startswith("ERROR:") for r in rows[1:])


def test_no_log_path_means_no_log_file(tmp_path):
    closes = _trending_closes(10)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "42", **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()  # must not raise despite log_path being unset

    assert not (tmp_path / "llm_log.csv").exists()


def test_warmup_days_predicts_and_logs_but_never_trades_during_warmup(tmp_path):
    closes = _trending_closes(10)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)
    log_path = tmp_path / "llm_log.csv"

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "80", log_path=str(log_path), warmup_days=3, **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    # Every call during warmup still predicts +80 and gets logged, but the
    # portfolio never actually moves off cash until warmup_days have passed.
    with log_path.open(newline="") as handle:
        rows = list(csv.reader(handle))
    assert [r[4] for r in rows[1:4]] == ["True", "True", "True"]
    assert [r[4] for r in rows[4:]] == ["False"] * len(rows[4:])

    assert portfolio.trades  # trades once warmup is over
    first_post_warmup_datetime = rows[4][1]  # row 4 is the first with warmup == "False"
    assert str(portfolio.trades[0].date) == first_post_warmup_datetime
    assert strategy._current_pct == 80.0


def test_zero_warmup_days_is_the_default_and_trades_immediately(tmp_path):
    closes = _trending_closes(10)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "80", **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert portfolio.trades  # no warmup configured -> trades on the very first call


def test_prompt_states_the_task_and_output_scale_explicitly(tmp_path):
    closes = _trending_closes(10)
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
    assert prompts[0].startswith("Task:")
    assert "TQQQ" in prompts[0] and "SQQQ" in prompts[0]
    assert "NOT the same scale" in prompts[0]


def test_iteration_counter_round_trips_through_get_state_and_load_state(tmp_path):
    closes = _trending_closes(10)
    dates = _dates(len(closes))
    repo = _setup(tmp_path, closes, dates)

    strategy = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "42", **_small_windows(),
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()

    assert strategy._iteration > 0

    restored = LLMTradingStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", sell_qty=10, portfolio_id="main",
        llm_client=lambda prompt: "42", **_small_windows(),
    )
    restored.load_state(strategy.get_state())

    assert restored._iteration == strategy._iteration


def test_build_llm_trading_passes_lora_knobs_through_to_the_client(tmp_path, monkeypatch):
    captured = {}

    class _FakeMLXLoRAClient:
        DEFAULT_MODEL = "fake-default"

        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("tam.strategy.mlx_lora_client.MLXLoRAClient", _FakeMLXLoRAClient)

    repo = _setup(tmp_path, _trending_closes(5), _dates(5))
    build_llm_trading(
        repo,
        "main",
        {
            "signal_ticker": "QQQ",
            "long_ticker": "TQQQ",
            "short_ticker": "SQQQ",
            "fine_tune_every_n_days": 10,
            "lora": {
                "lora_rank": 16,
                "lora_dropout": 0.1,
                "optimizer": "adamw",
                "weight_decay": 0.02,
                "val_split": 0.3,
                "extra": {"seed": 7},
            },
        },
        cash=10_000.0,
    )

    assert captured["fine_tune_every_n_days"] == 10
    assert captured["lora_rank"] == 16
    assert captured["lora_dropout"] == 0.1
    assert captured["optimizer"] == "adamw"
    assert captured["weight_decay"] == 0.02
    assert captured["val_split"] == 0.3
    assert captured["extra_mlx_config"] == {"seed": 7}

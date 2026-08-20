from datetime import date, timedelta

import numpy as np
import pandas as pd

from tam.backtest.harness import BacktestHarness
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.schema import OHLCV_COLUMNS
from tam.data.storage import CsvStore
from tam.portfolio.portfolio import Portfolio
from tam.strategy.ml_walk_forward import MLWalkForwardStrategy


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


def _trending_closes(n, seed=0):
    rng = np.random.default_rng(seed)
    steps = rng.normal(loc=0.15, scale=1.0, size=n)  # slight upward drift + noise
    return list(100.0 + np.cumsum(steps))


def _setup(tmp_path, signal_closes, dates):
    store = CsvStore(tmp_path)
    provider = MultiTickerProvider({"QQQ": signal_closes})
    repo = DataRepository(provider, store)
    repo.ingest(["QQQ", "TQQQ", "SQQQ"], dates[0], dates[-1])
    return repo


def _run(repo, dates, seed=0):
    strategy = MLWalkForwardStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", buy_qty=10, sell_qty=10, portfolio_id="main", seed=seed
    )
    portfolio = Portfolio("main", cash=10_000.0)
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, dates)
    harness.run()
    return strategy, portfolio


def test_no_trade_before_lookback_is_satisfied(tmp_path):
    closes = _trending_closes(10)
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(len(closes))]
    repo = _setup(tmp_path, closes, dates)

    strategy, portfolio = _run(repo, dates)

    assert portfolio.trades == []
    assert strategy._fitted is False


def test_no_trade_on_the_first_day_with_enough_history(tmp_path):
    # Exactly _LOOKBACK (25) closes -> exactly one qualifying day, with no prior
    # pending feature vector to have realized a label from yet, so the model
    # hasn't been fit at all.
    closes = _trending_closes(25)
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(len(closes))]
    repo = _setup(tmp_path, closes, dates)

    strategy, portfolio = _run(repo, dates)

    assert strategy._fitted is False
    assert portfolio.trades == []


def test_model_fits_and_trades_once_enough_days_have_passed(tmp_path):
    closes = _trending_closes(60)
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(len(closes))]
    repo = _setup(tmp_path, closes, dates)

    strategy, portfolio = _run(repo, dates)

    assert strategy._fitted is True
    assert strategy._held in ("long", "short")
    assert len(portfolio.trades) >= 1
    # Every trade is either the leveraged long or short instrument, never the
    # unleveraged signal ticker itself.
    assert {t.ticker for t in portfolio.trades} <= {"TQQQ", "SQQQ"}


def test_deterministic_given_the_same_seed_and_data(tmp_path):
    closes = _trending_closes(60)
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(len(closes))]

    repo_a = _setup(tmp_path / "a", closes, dates)
    _, portfolio_a = _run(repo_a, dates, seed=42)

    repo_b = _setup(tmp_path / "b", closes, dates)
    _, portfolio_b = _run(repo_b, dates, seed=42)

    trades_a = [(t.date, t.ticker, t.side, t.qty) for t in portfolio_a.trades]
    trades_b = [(t.date, t.ticker, t.side, t.qty) for t in portfolio_b.trades]
    assert trades_a == trades_b


def test_features_have_expected_shape_and_no_nans(tmp_path):
    closes = _trending_closes(30)
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(len(closes))]
    repo = _setup(tmp_path, closes, dates)

    strategy = MLWalkForwardStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", buy_qty=10, sell_qty=10, portfolio_id="main"
    )
    history = repo.query("QQQ").tail(25)
    features = strategy._compute_features(history["close"])

    assert features.shape == (1, 6)
    assert not np.isnan(features).any()


def test_get_state_and_load_state_round_trip_preserves_the_fitted_model(tmp_path):
    closes = _trending_closes(60)
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(len(closes))]
    repo = _setup(tmp_path, closes, dates)

    strategy, _ = _run(repo, dates, seed=42)
    assert strategy._fitted is True

    restored = MLWalkForwardStrategy(
        repo, "QQQ", "TQQQ", "SQQQ", buy_qty=10, sell_qty=10, portfolio_id="main", seed=42
    )
    restored.load_state(strategy.get_state())

    assert restored._held == strategy._held
    assert restored._fitted == strategy._fitted
    assert restored._pending[1] == strategy._pending[1]  # price half of the pending pair

    history = repo.query("QQQ").tail(25)
    features = strategy._compute_features(history["close"])
    # The restored classifier/scaler must predict identically to the original --
    # not just "some" state, but the actual learned weights.
    assert restored._predict(features) == strategy._predict(features)

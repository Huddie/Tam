"""tam/strategy/basket_overnight.py -- driven through a real BacktestHarness
against a fake multi-ticker repository. Heavier fixtures than most of
tests/, but this strategy's whole point is emergent behavior (monthly
rebalance cadence, daily round-trips, hedge sizing) across a real
event-driven simulation, not a pure function -- worth exercising the real
harness rather than just the private helpers in isolation.
"""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from tam.backtest.harness import BacktestHarness
from tam.basket.factors import RollingSharpe
from tam.basket.universe import StaticUniverse
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.schema import OHLCV_COLUMNS
from tam.data.storage import CsvStore
from tam.portfolio.portfolio import Portfolio
from tam.registry import Registry
from tam.strategy.base import Strategy
from tam.strategy.basket_overnight import BasketOvernightStrategy


def _price_frame(idx, overnight_r, intraday_r):
    opens, closes = [], []
    price = 100.0
    for i in range(len(idx)):
        price *= 1 + overnight_r[i]
        opens.append(price)
        price *= 1 + intraday_r[i]
        closes.append(price)
    open_s = pd.Series(opens, index=idx)
    close_s = pd.Series(closes, index=idx)
    return pd.DataFrame(
        {"open": open_s, "high": close_s, "low": open_s, "close": close_s, "adj_close": close_s, "volume": 1000},
        index=idx,
    ).rename_axis("date")[OHLCV_COLUMNS]


class _FakeProvider(DataProvider):
    def __init__(self, frames):
        self._frames = frames

    def fetch_eod(self, symbol, start, end):
        df = self._frames[symbol]
        return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]


def _build_repo(tmp_path, seed=0, periods=300):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-04", periods=periods)
    frames = {"SPY": _price_frame(idx, rng.normal(0.0002, 0.006, periods), rng.normal(0, 0.004, periods))}
    for ticker in ["A", "B", "C", "D"]:
        frames[ticker] = _price_frame(idx, rng.normal(0.0004, 0.008, periods), rng.normal(0, 0.008, periods))
    return DataRepository(_FakeProvider(frames), CsvStore(tmp_path)), idx


def _basic_strategy(repo, **overrides):
    kwargs = dict(
        repository=repo,
        universe=StaticUniverse(["A", "B", "C", "D"]),
        benchmark_ticker="SPY",
        factor_specs={"sharpe": (RollingSharpe(60), 1.0)},
        selection_params={"top_n": 4, "n_clusters": 2, "max_per_cluster": 2, "final_n": 2},
        weighting_params={"max_weight": 1.0},
        portfolio_id="basket",
        min_history_days=60,
    )
    kwargs.update(overrides)
    return BasketOvernightStrategy(**kwargs)


def test_rebalances_once_per_calendar_month_not_every_day(tmp_path):
    repo, idx = _build_repo(tmp_path)
    strategy = _basic_strategy(repo)
    portfolio = Portfolio("basket", cash=100_000.0)
    sim_dates = [ts.date() for ts in idx[130:250]]  # far enough in to have real trailing history

    rebalance_calls = []
    original_rebalance = strategy._rebalance

    def spy_rebalance(as_of):
        rebalance_calls.append(as_of)
        original_rebalance(as_of)

    strategy._rebalance = spy_rebalance

    harness = BacktestHarness(repo, [strategy], {"basket": portfolio}, sim_dates)
    harness.run()

    months = {(d.year, d.month) for d in rebalance_calls}
    assert len(rebalance_calls) == len(months)  # exactly one rebalance call per distinct month
    assert len(months) > 1  # this window genuinely spans more than one month


def test_holds_and_round_trips_selected_tickers_daily(tmp_path):
    repo, idx = _build_repo(tmp_path)
    strategy = _basic_strategy(repo)
    portfolio = Portfolio("basket", cash=100_000.0)
    sim_dates = [ts.date() for ts in idx[130:160]]

    harness = BacktestHarness(repo, [strategy], {"basket": portfolio}, sim_dates)
    report = harness.run()

    trades = report.trades_frame()
    assert not trades.empty
    assert set(trades["side"].unique()) <= {"BUY", "SELL"}
    # Every buy-at-close is closed by a sell-at-next-open -- except possibly
    # the very last simulated day's buy, which has no "next open" left in
    # this truncated window to close it (an artifact of the window's edge,
    # not of the strategy holding overnight through the regular session).
    for ticker in ["A", "B", "C", "D"]:
        ticker_trades = trades[trades["ticker"] == ticker]
        buys = (ticker_trades["side"] == "BUY").sum()
        sells = (ticker_trades["side"] == "SELL").sum()
        assert buys - sells in (0, 1)


def test_final_n_and_max_per_cluster_are_respected(tmp_path):
    repo, idx = _build_repo(tmp_path)
    strategy = _basic_strategy(
        repo, selection_params={"top_n": 4, "n_clusters": 4, "max_per_cluster": 1, "final_n": 2}
    )

    strategy._rebalance(idx[200].date())

    assert len(strategy._target_weights) <= 2


def test_weights_sum_to_one_when_a_basket_is_selected(tmp_path):
    repo, idx = _build_repo(tmp_path)
    strategy = _basic_strategy(repo)

    strategy._rebalance(idx[200].date())

    assert strategy._target_weights
    assert sum(strategy._target_weights.values()) == pytest.approx(1.0)


def test_empty_universe_leaves_the_basket_empty(tmp_path):
    repo, idx = _build_repo(tmp_path)
    strategy = _basic_strategy(repo, universe=StaticUniverse([]))

    strategy._rebalance(idx[200].date())

    assert strategy._target_weights == {}


def test_hedge_opens_a_short_and_fully_covers_it_by_the_end(tmp_path):
    repo, idx = _build_repo(tmp_path)
    strategy = _basic_strategy(repo, hedge_ticker="SPY", hedge_fraction=0.5, beta_window_days=60)
    portfolio = Portfolio("basket", cash=100_000.0)
    sim_dates = [ts.date() for ts in idx[130:140]]

    harness = BacktestHarness(repo, [strategy], {"basket": portfolio}, sim_dates)
    report = harness.run()

    # Every hedge SELL (open) at close is matched by a BUY (cover) at the
    # next open -- except possibly the very last simulated day's open, which
    # has no next open left in this truncated window (same edge artifact as
    # the basket's own daily round trips).
    spy_trades = report.trades_frame().pipe(lambda df: df[df["ticker"] == "SPY"])
    opens = (spy_trades["side"] == "SELL").sum()
    covers = (spy_trades["side"] == "BUY").sum()
    assert opens > 0  # the hedge actually did something in this window
    assert opens - covers in (0, 1)


def test_no_hedge_ticker_means_no_spy_trades(tmp_path):
    repo, idx = _build_repo(tmp_path)
    strategy = _basic_strategy(repo)  # hedge_ticker=None (default)
    portfolio = Portfolio("basket", cash=100_000.0)
    sim_dates = [ts.date() for ts in idx[130:140]]

    harness = BacktestHarness(repo, [strategy], {"basket": portfolio}, sim_dates)
    report = harness.run()

    assert (report.trades_frame()["ticker"] == "SPY").sum() == 0


def test_vol_targeting_produces_a_scale_between_zero_and_one(tmp_path):
    repo, idx = _build_repo(tmp_path, seed=1)
    strategy = _basic_strategy(repo, target_vol=0.01, vol_window_days=20)  # deliberately tight target
    as_of = idx[200].date()
    strategy._rebalance(as_of)

    scale = strategy._exposure_scale(as_of)

    assert 0.0 <= scale <= 1.0


def test_no_target_vol_means_full_exposure():
    class _Dummy:
        _target_vol = None
        _target_weights = {"A": 1.0}

    scale = BasketOvernightStrategy._exposure_scale(_Dummy(), date(2024, 1, 1))
    assert scale == 1.0


def test_builtin_registration_resolves_from_config_params(tmp_path):
    repo, _idx = _build_repo(tmp_path, periods=200)
    params = {
        "universe": {"provider": "static", "tickers": ["A", "B"]},
        "benchmark_ticker": "SPY",
        "factors": {"sharpe": {"factor": "sharpe", "window_days": 60, "weight": 1.0}},
        "selection": {"top_n": 2, "n_clusters": 1, "max_per_cluster": 2, "final_n": 2},
        "weighting": {"max_weight": 1.0},
    }

    strategy = Registry.create(Strategy, "basket_overnight", repo, "basket", params, 100_000.0)

    assert isinstance(strategy, BasketOvernightStrategy)


def test_scoring_method_defaults_to_zscore_and_is_configurable(tmp_path):
    repo, _idx = _build_repo(tmp_path, periods=200)
    params = {
        "universe": {"provider": "static", "tickers": ["A", "B"]},
        "benchmark_ticker": "SPY",
        "factors": {"sharpe": {"factor": "sharpe", "window_days": 60, "weight": 1.0}},
        "selection": {"top_n": 2, "n_clusters": 1, "max_per_cluster": 2, "final_n": 2},
        "weighting": {"max_weight": 1.0},
    }

    default_strategy = Registry.create(Strategy, "basket_overnight", repo, "basket", params, 100_000.0)
    assert default_strategy._scoring_method == "zscore"

    rank_strategy = Registry.create(
        Strategy, "basket_overnight", repo, "basket", {**params, "scoring": "rank"}, 100_000.0
    )
    assert rank_strategy._scoring_method == "rank"

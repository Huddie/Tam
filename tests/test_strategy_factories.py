from datetime import date

import pandas as pd
import pytest

from tam.backtest.config import build_strategies
from tam.config import Config
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.schema import OHLCV_COLUMNS
from tam.data.storage import CsvStore
from tam.portfolio.orders import Qty
from tam.registry import Registry
from tam.strategy.base import Strategy
from tam.strategy.buy_and_hold import BuyAndHoldStrategy
from tam.strategy.ma_crossover import MACrossoverStrategy
from tam.strategy.moving_average import MovingAverageStrategy


class FakeProvider(DataProvider):
    def __init__(self, frame: pd.DataFrame):
        self._frame = frame

    def fetch_eod(self, symbol, start, end):
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


def _repo_with_prices(tmp_path, dates, closes):
    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(_bars(dates, closes)), store)
    repo.ingest(["AAPL"], dates[0], dates[-1])
    return repo


def test_buy_and_hold_factory_defaults_to_full_cash_investment(tmp_path):
    dates = [date(2024, 1, 2), date(2024, 1, 3)]
    repo = _repo_with_prices(tmp_path, dates, [50.0, 51.0])

    strategy = Registry.create(
        Strategy, "buy_and_hold", repo, "baseline", {"ticker": "AAPL"}, 1_000.0
    )

    assert isinstance(strategy, BuyAndHoldStrategy)
    assert strategy._qty == Qty(pct=100)
    assert strategy._portfolio_id == "baseline"


def test_buy_and_hold_factory_respects_explicit_qty(tmp_path):
    dates = [date(2024, 1, 2)]
    repo = _repo_with_prices(tmp_path, dates, [50.0])

    strategy = Registry.create(
        Strategy, "buy_and_hold", repo, "baseline", {"ticker": "AAPL", "qty": 7}, 1_000.0
    )

    assert strategy._qty == Qty(static=7)


def test_moving_average_factory_builds_expected_strategy(tmp_path):
    dates = [date(2024, 1, 2)]
    repo = _repo_with_prices(tmp_path, dates, [50.0])

    strategy = Registry.create(
        Strategy,
        "moving_average",
        repo,
        "ma",
        {"ticker": "AAPL", "window": 20, "qty": 5},
        1_000.0,
    )

    assert isinstance(strategy, MovingAverageStrategy)
    assert strategy._window == 20
    assert strategy._buy_qty == Qty(static=5)
    assert strategy._sell_qty == Qty(static=5)


def test_ma_crossover_factory_builds_expected_strategy(tmp_path):
    dates = [date(2024, 1, 2)]
    repo = _repo_with_prices(tmp_path, dates, [50.0])

    strategy = Registry.create(
        Strategy,
        "ma_crossover",
        repo,
        "crossover",
        {"ticker": "AAPL", "first_window": 30, "second_window": 10, "qty": 3},
        1_000.0,
    )

    assert isinstance(strategy, MACrossoverStrategy)
    assert strategy._first_window == 30
    assert strategy._second_window == 10
    assert strategy._buy_qty == Qty(static=3)
    assert strategy._sell_qty == Qty(static=3)


def test_ma_crossover_factory_supports_separate_buy_and_sell_qty(tmp_path):
    dates = [date(2024, 1, 2)]
    repo = _repo_with_prices(tmp_path, dates, [50.0])

    strategy = Registry.create(
        Strategy,
        "ma_crossover",
        repo,
        "crossover",
        {
            "ticker": "AAPL",
            "first_window": 30,
            "second_window": 10,
            "buy": {"qty": {"pct": 20}},
            "sell": {"qty": {"pct": 100}},
        },
        1_000.0,
    )

    assert strategy._buy_qty == Qty(pct=20)
    assert strategy._sell_qty == Qty(pct=100)


def test_build_strategies_uses_default_cash_and_per_entry_override(tmp_path):
    dates = [date(2024, 1, 2)]
    repo = _repo_with_prices(tmp_path, dates, [100.0])

    config_path = tmp_path / "strategies.yaml"
    config_path.write_text(
        """
strategies:
  - strategy: buy_and_hold
    portfolio_id: baseline_default_cash
    params:
      ticker: AAPL
  - strategy: buy_and_hold
    portfolio_id: baseline_custom_cash
    cash: 500
    params:
      ticker: AAPL
      qty: 2
"""
    )
    cfg = Config(config_path)

    strategies, portfolios, traders = build_strategies(repo, cfg.strategies, default_cash=1_000.0)

    assert len(strategies) == 2
    assert set(portfolios) == {"baseline_default_cash", "baseline_custom_cash"}
    assert portfolios["baseline_default_cash"].cash == 1_000.0
    assert portfolios["baseline_custom_cash"].cash == 500.0

    default_strategy = next(s for s in strategies if s._portfolio_id == "baseline_default_cash")
    custom_strategy = next(s for s in strategies if s._portfolio_id == "baseline_custom_cash")
    assert default_strategy._qty == Qty(pct=100)
    assert custom_strategy._qty == Qty(static=2)


def test_unregistered_strategy_name_raises_key_error(tmp_path):
    repo = _repo_with_prices(tmp_path, [date(2024, 1, 2)], [100.0])
    with pytest.raises(KeyError):
        Registry.create(Strategy, "does_not_exist", repo, "x", {"ticker": "AAPL"}, 1_000.0)

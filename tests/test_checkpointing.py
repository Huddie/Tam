from datetime import date, timedelta

import pandas as pd
import pytest

from tam.backtest.harness import BacktestHarness
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.schema import OHLCV_COLUMNS
from tam.data.storage import CsvStore
from tam.events.clock import EOD_TOPIC
from tam.events.types import Event, State
from tam.portfolio.orders import Order, Qty, Side
from tam.portfolio.portfolio import Portfolio
from tam.strategy.base import Strategy


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


class _CrashableBuyOnce(Strategy):
    """Buys once on the first day, then holds -- with an optional trip wire that
    raises on a specific date, to simulate a mid-run crash without actually
    needing anything to fail for real."""

    def __init__(self, ticker: str, qty, portfolio_id: str, crash_on=None):
        super().__init__()
        self._ticker = ticker
        self._qty = Qty.of(qty)
        self._portfolio_id = portfolio_id
        self._crash_on = crash_on
        self._bought = False

    def state_change(self, state: State) -> None:
        if state is State.RUNNING:
            self.subscribe_to(EOD_TOPIC)

    def on_event(self, event: Event) -> None:
        if self._crash_on is not None and event.payload == self._crash_on:
            raise RuntimeError("simulated crash")
        if not self._bought:
            self.trade.stocks([Order(ticker=self._ticker, side=Side.BUY, qty=self._qty, portfolio=self._portfolio_id)])
            self._bought = True

    def get_state(self) -> dict:
        return {"bought": self._bought}

    def load_state(self, state: dict) -> None:
        self._bought = state["bought"]


_DATES = [date(2024, 1, 2) + timedelta(days=i) for i in range(5)]
_CLOSES = [100.0, 102.0, 98.0, 105.0, 110.0]


def _fresh_repo(store_path):
    store = CsvStore(store_path)
    repo = DataRepository(FakeProvider(_bars(_DATES, _CLOSES)), store)
    repo.ingest(["AAPL"], _DATES[0], _DATES[-1])
    return repo


def test_resume_after_a_mid_run_crash_matches_an_uninterrupted_run(tmp_path):
    baseline_repo = _fresh_repo(tmp_path / "baseline_store")
    baseline_portfolio = Portfolio("main", cash=10_000.0)
    baseline_strategy = _CrashableBuyOnce("AAPL", 5, "main")
    baseline_harness = BacktestHarness(baseline_repo, [baseline_strategy], {"main": baseline_portfolio}, _DATES)
    baseline_report = baseline_harness.run()

    checkpoint_path = tmp_path / "checkpoint.pkl"

    repo_1 = _fresh_repo(tmp_path / "run_store")
    portfolio_1 = Portfolio("main", cash=10_000.0)
    strategy_1 = _CrashableBuyOnce("AAPL", 5, "main", crash_on=_DATES[2])
    harness_1 = BacktestHarness(repo_1, [strategy_1], {"main": portfolio_1}, _DATES)
    with pytest.raises(RuntimeError, match="simulated crash"):
        harness_1.run(checkpoint_path=str(checkpoint_path), checkpoint_every=1)

    assert checkpoint_path.exists()
    # Day 1 (bought) and day 2 completed and got checkpointed; day 3 crashed before
    # completing, so nothing from it was ever committed.
    assert portfolio_1.trades and len(portfolio_1.trades) == 1

    # A brand-new repository/strategy/portfolio, as a fresh process resuming would
    # build from the same config -- only the on-disk store and checkpoint carry over.
    repo_2 = _fresh_repo(tmp_path / "run_store")
    portfolio_2 = Portfolio("main", cash=10_000.0)
    strategy_2 = _CrashableBuyOnce("AAPL", 5, "main")  # no crash this time
    harness_2 = BacktestHarness(repo_2, [strategy_2], {"main": portfolio_2}, _DATES)
    resumed_report = harness_2.run(checkpoint_path=str(checkpoint_path), checkpoint_every=1)

    assert not checkpoint_path.exists()  # cleaned up after a clean finish
    assert resumed_report.trades == baseline_report.trades
    assert resumed_report.to_frame().to_dict("records") == baseline_report.to_frame().to_dict("records")


def test_no_checkpoint_path_means_no_checkpoint_file_and_no_resume_state(tmp_path):
    repo = _fresh_repo(tmp_path / "store")
    portfolio = Portfolio("main", cash=10_000.0)
    strategy = _CrashableBuyOnce("AAPL", 5, "main")
    harness = BacktestHarness(repo, [strategy], {"main": portfolio}, _DATES)

    harness.run()  # checkpoint_path=None -- default, existing behavior unchanged

    assert not (tmp_path / "checkpoint.pkl").exists()


def test_portfolio_get_state_and_load_state_round_trip(tmp_path):
    portfolio = Portfolio("main", cash=10_000.0)
    portfolio.execute(
        Order(ticker="AAPL", side=Side.BUY, qty=10, portfolio="main"), qty=10, price=100.0, as_of=_DATES[0]
    )
    portfolio.execute(
        Order(ticker="AAPL", side=Side.SELL, qty=4, portfolio="main"), qty=4, price=110.0, as_of=_DATES[1]
    )

    state = portfolio.get_state()

    restored = Portfolio("main", cash=0.0)
    restored.load_state(state)

    assert restored.cash == portfolio.cash
    assert restored.position("AAPL") == portfolio.position("AAPL")
    assert restored.trades == portfolio.trades

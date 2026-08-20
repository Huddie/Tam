from datetime import date

import pandas as pd

from tam.backtest.harness import BacktestHarness
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.schema import OHLCV_COLUMNS
from tam.data.storage import CsvStore
from tam.events.clock import EOD_TOPIC
from tam.events.types import Event, State
from tam.portfolio.orders import Order, Side
from tam.portfolio.portfolio import Portfolio
from tam.strategy.base import Strategy
from tam.strategy.buy_and_hold import BuyAndHoldStrategy


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


class BuyOnceStrategy(Strategy):
    """Buys 10 shares of AAPL on the first EOD event, then holds."""

    def __init__(self):
        super().__init__()
        self.states = []
        self.bought = False

    def state_change(self, state: State) -> None:
        self.states.append(state)
        if state is State.RUNNING:
            self.subscribe_to(EOD_TOPIC)

    def on_event(self, event: Event) -> None:
        if not self.bought:
            self.trade.stocks([Order(ticker="AAPL", side=Side.BUY, qty=10, portfolio="main")])
            self.bought = True


def test_backtest_runs_full_lifecycle_and_produces_report(tmp_path):
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    closes = [100.0, 110.0, 90.0]
    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(_bars(dates, closes)), store)
    repo.ingest(["AAPL"], date(2024, 1, 2), date(2024, 1, 4))

    strategy = BuyOnceStrategy()
    portfolios = {"main": Portfolio("main", cash=10_000.0)}
    sim_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]

    harness = BacktestHarness(repo, [strategy], portfolios, sim_dates)
    report = harness.run()

    assert strategy.states == [State.START, State.RUNNING, State.END]

    frame = report.to_frame()
    assert list(frame["date"]) == sim_dates
    # Bought 10 @ 100 on day 1 -> cash 9000, then marked to market each day.
    assert frame.iloc[0]["value"] == 9_000.0 + 10 * 100.0
    assert frame.iloc[1]["value"] == 9_000.0 + 10 * 110.0
    assert frame.iloc[2]["value"] == 9_000.0 + 10 * 90.0

    summary = report.summary("main")
    assert summary["start_value"] == 10_000.0
    assert summary["end_value"] == 9_900.0

    assert report.trades == [
        {"date": date(2024, 1, 2), "portfolio": "main", "ticker": "AAPL", "side": Side.BUY, "qty": 10, "price": 100.0}
    ]


def test_buy_and_hold_strategy_buys_once_and_holds(tmp_path):
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    closes = [100.0, 110.0, 90.0]
    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(_bars(dates, closes)), store)
    repo.ingest(["AAPL"], date(2024, 1, 2), date(2024, 1, 4))

    portfolio = Portfolio("main", cash=10_000.0)
    portfolios = {"main": portfolio}
    sim_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]

    strategy = BuyAndHoldStrategy(ticker="AAPL", qty=5, portfolio_id="main")
    harness = BacktestHarness(repo, [strategy], portfolios, sim_dates)
    harness.run()

    # Bought 5 @ 100 on day 1 -> cash and position change once...
    assert portfolio.cash == 10_000.0 - 5 * 100.0
    position = portfolio.position("AAPL")
    assert position.qty == 5
    assert position.avg_price == 100.0
    # ...and never again on subsequent EOD events.
    assert len(portfolio.trades) == 1


def test_publish_and_subscribe_between_strategies(tmp_path):
    dates = ["2024-01-02"]
    store = CsvStore(tmp_path)
    repo = DataRepository(FakeProvider(_bars(dates, [50.0])), store)
    repo.ingest(["AAPL"], date(2024, 1, 2), date(2024, 1, 2))

    received = []

    class Publisher(Strategy):
        def state_change(self, state):
            if state is State.RUNNING:
                self.subscribe_to(EOD_TOPIC)

        def on_event(self, event):
            self.publish("signal.custom", Event(type="signal.custom", payload="go"))

    class Subscriber(Strategy):
        def state_change(self, state):
            if state is State.RUNNING:
                self.subscribe_to("signal.custom")

        def on_event(self, event):
            received.append(event.payload)

    portfolios = {"main": Portfolio("main", cash=1_000.0)}
    harness = BacktestHarness(repo, [Publisher(), Subscriber()], portfolios, [date(2024, 1, 2)])
    harness.run()

    assert received == ["go"]

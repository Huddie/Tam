"""Walk-forward online-learning rotation: predicts the next period's direction
from engineered technical features using an incrementally-updated linear
classifier, retrained one sample at a time as each day's outcome becomes
known. Holds long_ticker or short_ticker exclusively based on the prediction
-- same rotation mechanic as trend_rotation.py, so it's directly comparable.

Deliberately a *lightweight* classical model (logistic-regression-style
online learning via SGDClassifier), not a deep net or LLM: it trains in
microseconds per update, needs no GPU, and is far easier to validate for
lookahead-bias correctness than either alternative -- the actual "predict,
learn from the outcome, improve over time" loop the user wants is a textbook
online/walk-forward learning problem, which classical incremental ML is the
standard tool for.

Feature vector (all backward-looking as of the current bar -- no leakage):
- 1/5/10-day trailing returns
- 10-day realized volatility (std of daily returns)
- RSI(14)
- price / SMA(20) - 1

Walk-forward loop each simulated day (as_of = T):
1. Yesterday's features (computed as of T-1) are sitting in self._pending.
   Today's close is now known, so the label for that feature vector -- did
   price go up from T-1 to T? -- is now realized. partial_fit the scaler and
   classifier with it.
2. Compute today's features (as of T) and stash them as the new pending pair,
   to be labeled tomorrow.
3. Once the classifier has seen at least one training example, predict
   direction from today's features and flip to the corresponding side if it
   differs from what's currently held (same-day signal-and-execute, matching
   every other strategy in this package).
"""
from __future__ import annotations

import numpy as np
from sklearn.linear_model import SGDClassifier
from sklearn.preprocessing import StandardScaler

from ..data.repository import DataRepository
from ..events.clock import EOD_TOPIC
from ..events.types import Event, State
from ..portfolio.orders import Order, Qty, Side
from ..registry import Registry
from .base import Strategy
from .indicators import rsi, sma

_LOOKBACK = 25


class MLWalkForwardStrategy(Strategy):
    def __init__(
        self,
        repository: DataRepository,
        signal_ticker: str,
        long_ticker: str,
        short_ticker: str,
        buy_qty,
        sell_qty,
        portfolio_id: str,
        seed: int = 0,
    ):
        super().__init__()
        self._repository = repository
        self._signal_ticker = signal_ticker
        self._long_ticker = long_ticker
        self._short_ticker = short_ticker
        self._buy_qty = Qty.of(buy_qty)
        self._sell_qty = Qty.of(sell_qty)
        self._portfolio_id = portfolio_id
        self._held = None  # None | "long" | "short"
        self._scaler = StandardScaler()
        self._model = SGDClassifier(loss="log_loss", random_state=seed)
        self._fitted = False
        self._pending = None  # (features, price_as_of_pending) awaiting tomorrow's label

    def state_change(self, state: State) -> None:
        if state is State.RUNNING:
            self.subscribe_to(EOD_TOPIC)

    def on_event(self, event: Event) -> None:
        as_of = event.payload
        history = self._repository.history(self._signal_ticker).window_ending(as_of, _LOOKBACK)
        if len(history) < _LOOKBACK:
            return

        close = history["close"]
        current_price = close.iloc[-1]

        if self._pending is not None:
            pending_features, prior_price = self._pending
            label = 1 if current_price > prior_price else 0
            self._partial_fit(pending_features, label)

        features = self._compute_features(close)
        self._pending = (features, current_price)

        if not self._fitted:
            return

        target = "long" if self._predict(features) == 1 else "short"
        if target != self._held:
            self._flip_to(target)

    def _compute_features(self, close) -> np.ndarray:
        returns = close.pct_change()
        ret_1 = returns.iloc[-1]
        ret_5 = close.iloc[-1] / close.iloc[-6] - 1
        ret_10 = close.iloc[-1] / close.iloc[-11] - 1
        vol_10 = returns.tail(10).std()
        rsi_14 = rsi(close, 14).iloc[-1]
        sma_ratio = close.iloc[-1] / sma(close, 20).iloc[-1] - 1
        return np.array([[ret_1, ret_5, ret_10, vol_10, rsi_14, sma_ratio]])

    def _partial_fit(self, features: np.ndarray, label: int) -> None:
        self._scaler.partial_fit(features)
        scaled = self._scaler.transform(features)
        self._model.partial_fit(scaled, [label], classes=[0, 1])
        self._fitted = True

    def _predict(self, features: np.ndarray) -> int:
        scaled = self._scaler.transform(features)
        return int(self._model.predict(scaled)[0])

    def _flip_to(self, target: str) -> None:
        exit_ticker = {"long": self._long_ticker, "short": self._short_ticker}.get(self._held)
        if exit_ticker is not None:
            self.trade.stocks(
                [Order(ticker=exit_ticker, side=Side.SELL, qty=self._sell_qty, portfolio=self._portfolio_id)]
            )
        entry_ticker = self._long_ticker if target == "long" else self._short_ticker
        self.trade.stocks(
            [Order(ticker=entry_ticker, side=Side.BUY, qty=self._buy_qty, portfolio=self._portfolio_id)]
        )
        self._held = target

    def get_state(self) -> dict:
        return {
            "held": self._held,
            "scaler": self._scaler,
            "model": self._model,
            "fitted": self._fitted,
            "pending": self._pending,
        }

    def load_state(self, state: dict) -> None:
        self._held = state["held"]
        self._scaler = state["scaler"]
        self._model = state["model"]
        self._fitted = state["fitted"]
        self._pending = state["pending"]


@Registry.register(Strategy, "ml_walk_forward")
def build_ml_walk_forward(
    repository: DataRepository, portfolio_id: str, params, cash: float
) -> MLWalkForwardStrategy:
    buy_qty = params["buy"]["qty"] if "buy" in params else params["qty"]
    sell_qty = params["sell"]["qty"] if "sell" in params else params["qty"]
    return MLWalkForwardStrategy(
        repository,
        params["signal_ticker"],
        params["long_ticker"],
        params["short_ticker"],
        buy_qty,
        sell_qty,
        portfolio_id,
        seed=params.get("seed", 0),
    )

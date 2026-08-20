"""Trend + momentum regime rotation: each day, hold either a leveraged long
instrument or a leveraged inverse instrument on the same underlying index --
never both -- based on two signals computed from the *unleveraged* underlying
(e.g. QQQ), not from the leveraged instruments' own price action.

Why signal off the underlying rather than off TQQQ/SQQQ directly: daily-reset
3x ETFs suffer volatility decay -- their value erodes with realized variance
regardless of direction (see Cheng & Madhavan, "The Dynamics of Leveraged and
Inverse ETFs", 2009). That makes their own price series a noisier, decay-
distorted proxy for "is the underlying trending up or down" than the
underlying itself. So: compute the regime on the clean index, then express
that view with leverage via long_ticker/short_ticker.

Two independent, well-established signals on the underlying, required to
agree before flipping (disagreement -> hold whatever side is already held,
to avoid whipsawing on ambiguous days):

- Trend filter: price vs. its own SMA(trend_window) -- the classic Faber
  ("A Quantitative Approach to Tactical Asset Allocation") regime filter,
  typically a ~200-day window. Bullish above, bearish below.
- Momentum confirmation: trailing momentum_window-day return, sign only --
  time-series/absolute momentum (Moskowitz, Ooi & Pedersen, "Time Series
  Momentum", 2012), typically a shorter window (e.g. 20 days) so it reacts
  faster than the trend filter alone and reduces whipsaw right at the SMA line.

Two opt-in risk overlays soften the leverage, which is otherwise applied at a
constant 100% regardless of how turbulent or calm the underlying currently is:

- Volatility targeting (target_vol/vol_window): size the entry as a percentage
  of cash inversely proportional to the underlying's trailing realized
  volatility, so exposure shrinks automatically in turbulent regimes -- exactly
  when 3x daily-reset decay and whipsaw risk are worst -- and grows back in calm
  trending regimes. Re-sized in place (no side change) whenever the target
  drifts by more than rebalance_threshold_pct.
- Drawdown circuit breaker (max_position_drawdown): if the currently held
  leveraged ticker falls more than this fraction from its peak price since
  entry, exit to cash immediately rather than waiting for the slower trend/
  momentum signals to catch up. Re-entry on that same side is blocked until
  either the raw signal moves off of it, or breaker_cooldown_days elapse,
  whichever comes first -- a single bad print can't cause an instant re-buy
  into the same falling knife, but the block also can't strand the strategy
  in cash indefinitely if the regime never flips (e.g. a sharp air-pocket
  inside an otherwise-intact bull trend, where the trend/momentum signal
  itself never turns bearish).
"""
from __future__ import annotations

from typing import Optional

from ..data.repository import DataRepository
from ..events.clock import EOD_TOPIC
from ..events.types import Event, State
from ..portfolio.orders import Order, Qty, QtyBasis, Side
from ..registry import Registry
from .base import Strategy
from .indicators import sma

ANNUALIZATION_FACTOR = 252 ** 0.5


class TrendRotationStrategy(Strategy):
    def __init__(
        self,
        repository: DataRepository,
        signal_ticker: str,
        long_ticker: str,
        short_ticker: str,
        trend_window: int,
        momentum_window: int,
        buy_qty,
        sell_qty,
        portfolio_id: str,
        target_vol: Optional[float] = None,
        vol_window: int = 20,
        min_exposure_pct: float = 20.0,
        max_exposure_pct: float = 100.0,
        rebalance_threshold_pct: float = 10.0,
        max_position_drawdown: Optional[float] = None,
        breaker_cooldown_days: int = 10,
    ):
        super().__init__()
        self._repository = repository
        self._signal_ticker = signal_ticker
        self._long_ticker = long_ticker
        self._short_ticker = short_ticker
        self._trend_window = trend_window
        self._momentum_window = momentum_window
        self._buy_qty = Qty.of(buy_qty)
        self._sell_qty = Qty.of(sell_qty)
        self._portfolio_id = portfolio_id
        self._target_vol = target_vol
        self._vol_window = vol_window
        self._min_exposure_pct = min_exposure_pct
        self._max_exposure_pct = max_exposure_pct
        self._rebalance_threshold_pct = rebalance_threshold_pct
        self._max_position_drawdown = max_position_drawdown
        self._breaker_cooldown_days = breaker_cooldown_days
        self._held = None  # None | "long" | "short"
        self._blocked_side = None  # side under cooldown after a circuit-breaker stop-out
        self._cooldown_remaining = 0  # trading days left before the block auto-clears
        self._entry_peak = None  # peak price of the held ticker since entry, for the breaker
        self._last_exposure_pct = None  # last vol-targeted exposure applied, for rebalancing

    def state_change(self, state: State) -> None:
        if state is State.RUNNING:
            self.subscribe_to(EOD_TOPIC)

    def on_event(self, event: Event) -> None:
        as_of = event.payload
        self._maybe_stop_out(as_of)

        if self._blocked_side is not None:
            self._cooldown_remaining -= 1

        target = self._target_side(as_of)
        if target is not None and (target != self._blocked_side or self._cooldown_remaining <= 0):
            self._blocked_side = None

        if target is not None and target != self._blocked_side and target != self._held:
            self._flip_to(target, as_of)
            return

        if self._held is not None:
            self._maybe_rebalance(as_of)

    def _target_side(self, as_of):
        required = max(self._trend_window, self._momentum_window + 1)
        history = self._repository.query(self._signal_ticker, end=as_of).tail(required)
        if len(history) < required:
            return None

        close = history["close"]
        price = close.iloc[-1]
        trend_level = sma(close, self._trend_window).iloc[-1]
        momentum = price / close.iloc[-(self._momentum_window + 1)] - 1

        bull_votes = (price > trend_level) + (momentum > 0)
        bear_votes = (price < trend_level) + (momentum < 0)

        if bull_votes > bear_votes:
            return "long"
        if bear_votes > bull_votes:
            return "short"
        return self._held  # signals disagree -- hold whatever side we're already on

    def _current_price(self, ticker: str, as_of):
        history = self._repository.query(ticker, end=as_of).tail(1)
        return history["close"].iloc[-1] if len(history) else None

    def _realized_vol(self, as_of) -> Optional[float]:
        history = self._repository.query(self._signal_ticker, end=as_of).tail(self._vol_window + 1)
        if len(history) < self._vol_window + 1:
            return None
        return history["close"].pct_change().dropna().std() * ANNUALIZATION_FACTOR

    def _target_exposure_pct(self, as_of) -> Optional[float]:
        if self._target_vol is None:
            return None
        vol = self._realized_vol(as_of)
        if vol is None or vol <= 0:
            return None
        pct = self._target_vol / vol * 100
        return max(self._min_exposure_pct, min(self._max_exposure_pct, pct))

    def _entry_qty(self, as_of):
        exposure_pct = self._target_exposure_pct(as_of)
        if exposure_pct is None:
            return self._buy_qty, None
        return Qty(pct=exposure_pct, basis=QtyBasis.CASH), exposure_pct

    def _maybe_stop_out(self, as_of) -> None:
        if self._held is None or self._max_position_drawdown is None:
            return
        ticker = self._long_ticker if self._held == "long" else self._short_ticker
        price = self._current_price(ticker, as_of)
        if price is None:
            return
        self._entry_peak = max(self._entry_peak, price)
        if price / self._entry_peak - 1 <= -self._max_position_drawdown:
            self.trade.stocks(
                [Order(ticker=ticker, side=Side.SELL, qty=self._sell_qty, portfolio=self._portfolio_id)]
            )
            self._blocked_side = self._held
            self._cooldown_remaining = self._breaker_cooldown_days
            self._held = None
            self._entry_peak = None
            self._last_exposure_pct = None

    def _maybe_rebalance(self, as_of) -> None:
        if self._target_vol is None:
            return
        exposure_pct = self._target_exposure_pct(as_of)
        if exposure_pct is None or self._last_exposure_pct is None:
            return
        if abs(exposure_pct - self._last_exposure_pct) < self._rebalance_threshold_pct:
            return
        ticker = self._long_ticker if self._held == "long" else self._short_ticker
        self.trade.stocks([Order(ticker=ticker, side=Side.SELL, qty=self._sell_qty, portfolio=self._portfolio_id)])
        self.trade.stocks(
            [Order(ticker=ticker, side=Side.BUY, qty=Qty(pct=exposure_pct, basis=QtyBasis.CASH), portfolio=self._portfolio_id)]
        )
        self._last_exposure_pct = exposure_pct

    def _flip_to(self, target: str, as_of) -> None:
        exit_ticker = {"long": self._long_ticker, "short": self._short_ticker}.get(self._held)
        if exit_ticker is not None:
            self.trade.stocks(
                [Order(ticker=exit_ticker, side=Side.SELL, qty=self._sell_qty, portfolio=self._portfolio_id)]
            )

        entry_ticker = self._long_ticker if target == "long" else self._short_ticker
        buy_qty, exposure_pct = self._entry_qty(as_of)
        self.trade.stocks(
            [Order(ticker=entry_ticker, side=Side.BUY, qty=buy_qty, portfolio=self._portfolio_id)]
        )
        self._held = target
        self._entry_peak = self._current_price(entry_ticker, as_of)
        self._last_exposure_pct = exposure_pct

    def get_state(self) -> dict:
        return {
            "held": self._held,
            "blocked_side": self._blocked_side,
            "cooldown_remaining": self._cooldown_remaining,
            "entry_peak": self._entry_peak,
            "last_exposure_pct": self._last_exposure_pct,
        }

    def load_state(self, state: dict) -> None:
        self._held = state["held"]
        self._blocked_side = state["blocked_side"]
        self._cooldown_remaining = state["cooldown_remaining"]
        self._entry_peak = state["entry_peak"]
        self._last_exposure_pct = state["last_exposure_pct"]


@Registry.register(Strategy, "trend_rotation")
def build_trend_rotation(repository: DataRepository, portfolio_id: str, params, cash: float) -> TrendRotationStrategy:
    buy_qty = params["buy"]["qty"] if "buy" in params else params["qty"]
    sell_qty = params["sell"]["qty"] if "sell" in params else params["qty"]
    return TrendRotationStrategy(
        repository,
        params["signal_ticker"],
        params["long_ticker"],
        params["short_ticker"],
        params["trend_window"],
        params["momentum_window"],
        buy_qty,
        sell_qty,
        portfolio_id,
        target_vol=params.get("target_vol"),
        vol_window=params.get("vol_window", 20),
        min_exposure_pct=params.get("min_exposure_pct", 20.0),
        max_exposure_pct=params.get("max_exposure_pct", 100.0),
        rebalance_threshold_pct=params.get("rebalance_threshold_pct", 10.0),
        max_position_drawdown=params.get("max_position_drawdown"),
        breaker_cooldown_days=params.get("breaker_cooldown_days", 10),
    )

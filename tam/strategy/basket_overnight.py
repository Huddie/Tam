"""Basket overnight (BCSO) strategy: monthly, re-select a diversified basket
from a point-in-time universe (tam.basket.universe/factors/selection/
weighting -- the exact same building blocks a notebook would use to research
this, see LIB.md's "tam.basket" section); daily, execute the overnight
round-trip (buy every basket member at close, sell at next open) on whatever
the current basket/weights are -- the same buy-close/sell-open mechanic as
OvernightHoldStrategy (overnight_hold.py), generalized from one ticker to N.

Optional overlays, both off by default:
- Vol targeting (target_vol): scale the WHOLE basket's exposure down when its
  own trailing realized volatility is running hot -- same shape as
  TrendRotationStrategy's _target_exposure_pct (trend_rotation.py), applied
  to a basket's simulated return (tam.basket.simulate.simulate_basket) instead
  of one ticker's price series.
- SPY-beta hedge (hedge_ticker/hedge_fraction): short hedge_ticker sized to
  the basket's own weighted-average overnight beta (tam.basket.factors.
  OvernightBeta) times hedge_fraction, opened at close alongside the basket
  and closed at the next open -- removing (that fraction of) whatever part of
  the basket's overnight return is just "the market moved overnight and this
  basket has beta to that," per tam.basket.factors.OvernightAlpha's own
  rationale.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, Optional, Tuple

import pandas as pd

from ..basket.factors import Factor, OvernightBeta, compute_factors, score
from ..basket.matrix import price_matrix
from ..basket.selection import cluster, select_diversified
from ..basket.simulate import simulate_basket
from ..basket.universe import UniverseProvider
from ..basket.weighting import inverse_vol_weights
from ..data.repository import DataRepository
from ..data.schema import CLOSE, OPEN
from ..events.clock import EOD_TOPIC, OPEN_TOPIC
from ..events.types import Event, State
from ..portfolio.orders import Order, PriceBasis, Qty, QtyBasis, Side
from ..registry import Registry
from .base import Strategy

_TRADING_DAYS_PER_YEAR = 252


def _overnight_returns(repository: DataRepository, tickers, start: date, end: date) -> pd.DataFrame:
    """Open[t+1]/Close[t] - 1 -- this strategy's own return definition (buy
    close, sell next open), built from the generic price_matrix() primitive
    (tam/basket/matrix.py). Specific to basket_overnight, not part of the
    generic toolkit -- see LIB.md's "tam.basket" section for why."""
    opens = price_matrix(repository, tickers, start, end, column=OPEN)
    closes = price_matrix(repository, tickers, start, end, column=CLOSE)
    return opens.shift(-1) / closes - 1


def _calendar_days_for(trading_days: int) -> int:
    """A generous calendar-day buffer to ingest/query for `trading_days`
    worth of history -- accounts for weekends/holidays without needing an
    exact trading calendar (a couple of extra ingested days that end up
    unused is harmless; not having enough is a silent, wrong factor)."""
    return int(trading_days * 7 / 5) + 15


class BasketOvernightStrategy(Strategy):
    def __init__(
        self,
        repository: DataRepository,
        universe: UniverseProvider,
        benchmark_ticker: str,
        factor_specs: Dict[str, Tuple[Factor, float]],
        selection_params: dict,
        weighting_params: dict,
        portfolio_id: str,
        rebalance_frequency: str = "monthly",
        target_vol: Optional[float] = None,
        vol_window_days: int = 60,
        hedge_ticker: Optional[str] = None,
        hedge_fraction: Optional[float] = None,
        beta_window_days: int = 252,
        sectors: Optional[Dict[str, str]] = None,
        min_history_days: int = 252,
        scoring_method: str = "zscore",
    ):
        super().__init__()
        self._repository = repository
        self._universe = universe
        self._benchmark_ticker = benchmark_ticker
        self._factor_specs = factor_specs
        self._selection_params = selection_params
        self._weighting_params = weighting_params
        self._portfolio_id = portfolio_id
        self._rebalance_frequency = rebalance_frequency
        self._target_vol = target_vol
        self._vol_window_days = vol_window_days
        self._hedge_ticker = hedge_ticker
        self._hedge_fraction = hedge_fraction
        self._beta_window_days = beta_window_days
        self._sectors = pd.Series(sectors) if sectors else None
        self._min_history_days = min_history_days
        self._scoring_method = scoring_method

        lookback = max([min_history_days, beta_window_days, vol_window_days] + [0])
        self._lookback_calendar_days = _calendar_days_for(lookback)

        self._target_weights: Dict[str, float] = {}
        self._portfolio_beta: float = 0.0
        self._hedge_shares: int = 0
        self._last_rebalance_period = None
        self._ingested: set = set()

    def state_change(self, state: State) -> None:
        if state is State.RUNNING:
            self.subscribe_to(OPEN_TOPIC)
            self.subscribe_to(EOD_TOPIC)

    def on_event(self, event: Event) -> None:
        if event.type == OPEN_TOPIC:
            self._on_open(event.payload)
        elif event.type == EOD_TOPIC:
            self._on_close(event.payload)

    def _on_open(self, as_of: date) -> None:
        for ticker in self._target_weights:
            self.trade.stocks(
                [Order(ticker=ticker, side=Side.SELL, qty=Qty(pct=100), portfolio=self._portfolio_id, price_basis=PriceBasis.OPEN)]
            )
        if self._hedge_shares:
            self.trade.stocks(
                [
                    Order(
                        ticker=self._hedge_ticker,
                        side=Side.BUY,
                        qty=Qty(static=self._hedge_shares),
                        portfolio=self._portfolio_id,
                        price_basis=PriceBasis.OPEN,
                    )
                ]
            )
            self._hedge_shares = 0

    def _on_close(self, as_of: date) -> None:
        period = self._period_key(as_of)
        if period != self._last_rebalance_period:
            self._rebalance(as_of)
            self._last_rebalance_period = period

        if not self._target_weights:
            return

        exposure_scale = self._exposure_scale(as_of)
        cash_before = self.portfolios[self._portfolio_id].cash

        for ticker, weight in self._target_weights.items():
            pct = weight * exposure_scale * 100
            if pct <= 0:
                continue
            self.trade.stocks(
                [
                    Order(
                        ticker=ticker,
                        side=Side.BUY,
                        qty=Qty(pct=pct, basis=QtyBasis.CASH),
                        portfolio=self._portfolio_id,
                        price_basis=PriceBasis.CLOSE,
                    )
                ]
            )

        self._maybe_hedge(as_of, cash_before, exposure_scale)

    def _maybe_hedge(self, as_of: date, cash_before: float, exposure_scale: float) -> None:
        if not (self._hedge_ticker and self._hedge_fraction and self._portfolio_beta):
            return
        notional = cash_before * exposure_scale * self._portfolio_beta * self._hedge_fraction
        if notional <= 0:
            return
        price = self._repository.history(self._hedge_ticker).price_at(as_of, CLOSE)
        shares = int(notional / price)
        if shares <= 0:
            return
        self.trade.stocks(
            [
                Order(
                    ticker=self._hedge_ticker,
                    side=Side.SELL,
                    qty=Qty(static=shares),
                    portfolio=self._portfolio_id,
                    price_basis=PriceBasis.CLOSE,
                )
            ]
        )
        self._hedge_shares = shares

    def _period_key(self, as_of: date):
        if self._rebalance_frequency == "monthly":
            return (as_of.year, as_of.month)
        raise ValueError(f"unsupported rebalance frequency {self._rebalance_frequency!r} -- only 'monthly' today")

    def _exposure_scale(self, as_of: date) -> float:
        """min(1, target_vol / forecast_vol) of the CURRENT basket's own
        simulated return -- same shape as TrendRotationStrategy's
        _target_exposure_pct, generalized from one ticker's price series to
        a basket's simulated return (tam.basket.simulate.simulate_basket)."""
        if self._target_vol is None or not self._target_weights:
            return 1.0
        lookback_start = as_of - timedelta(days=_calendar_days_for(self._vol_window_days))
        returns = _overnight_returns(self._repository, list(self._target_weights), lookback_start, as_of)
        basket_returns = simulate_basket(returns, self._target_weights).tail(self._vol_window_days)
        if len(basket_returns) < 2:
            return 1.0
        forecast_vol = float(basket_returns.std() * (_TRADING_DAYS_PER_YEAR**0.5))
        if forecast_vol <= 0:
            return 1.0
        return min(1.0, self._target_vol / forecast_vol)

    def _rebalance(self, as_of: date) -> None:
        universe_tickers = [t for t in self._universe.constituents(as_of) if t != self._benchmark_ticker]
        all_tickers = universe_tickers + [self._benchmark_ticker]
        if not universe_tickers:
            self._target_weights = {}
            self._portfolio_beta = 0.0
            return

        lookback_start = as_of - timedelta(days=self._lookback_calendar_days)
        self._repository.ingest(all_tickers, lookback_start, as_of)
        self._ingested.update(all_tickers)

        returns = _overnight_returns(self._repository, all_tickers, lookback_start, as_of)
        present_universe = [t for t in universe_tickers if t in returns.columns]
        if not present_universe:
            self._target_weights = {}
            self._portfolio_beta = 0.0
            return

        factors = {name: factor for name, (factor, _weight) in self._factor_specs.items()}
        weights_by_factor = {name: weight for name, (_factor, weight) in self._factor_specs.items()}
        # Computed against the FULL frame (including the benchmark column) --
        # OvernightAlpha/OvernightBeta need the benchmark present to regress
        # against; the benchmark's own row is dropped right after, since it's
        # not a candidate to score/select/weight.
        factor_table = compute_factors(returns, as_of, factors).loc[present_universe]
        universe_returns = returns[present_universe]
        scores = score(factor_table, weights_by_factor, method=self._scoring_method)

        top_n = self._selection_params.get("top_n", len(scores))
        candidates = scores.sort_values(ascending=False).head(top_n)
        candidates = candidates[candidates > 0].index.tolist()
        if not candidates:
            self._target_weights = {}
            self._portfolio_beta = 0.0
            return

        n_clusters = self._selection_params.get("n_clusters", min(8, len(candidates)))
        clusters = cluster(universe_returns[candidates].loc[: pd.Timestamp(as_of)], n_clusters=n_clusters)
        final_n = self._selection_params.get("final_n", len(candidates))
        max_per_cluster = self._selection_params.get("max_per_cluster", final_n)
        picks = select_diversified(scores[candidates], clusters, n=final_n, max_per_cluster=max_per_cluster)
        if not picks:
            self._target_weights = {}
            self._portfolio_beta = 0.0
            return

        volatility = universe_returns[picks].loc[: pd.Timestamp(as_of)].tail(self._min_history_days).std()
        weights = inverse_vol_weights(
            scores[picks],
            volatility,
            max_weight=self._weighting_params.get("max_weight", 1.0),
            sector_caps=self._weighting_params.get("sector_caps"),
            sectors=self._sectors,
        )
        self._target_weights = {ticker: float(w) for ticker, w in weights.items() if w > 0}

        if self._hedge_ticker and self._target_weights:
            betas = OvernightBeta(self._beta_window_days, self._benchmark_ticker).compute(returns[all_tickers], as_of)
            self._portfolio_beta = float(sum(betas[t] * w for t, w in self._target_weights.items()))
        else:
            self._portfolio_beta = 0.0

    def get_state(self) -> dict:
        return {
            "target_weights": self._target_weights,
            "portfolio_beta": self._portfolio_beta,
            "hedge_shares": self._hedge_shares,
            "last_rebalance_period": self._last_rebalance_period,
            "ingested": self._ingested,
        }

    def load_state(self, state: dict) -> None:
        self._target_weights = state["target_weights"]
        self._portfolio_beta = state["portfolio_beta"]
        self._hedge_shares = state["hedge_shares"]
        self._last_rebalance_period = state["last_rebalance_period"]
        self._ingested = state["ingested"]


@Registry.register(Strategy, "basket_overnight")
def build_basket_overnight(repository: DataRepository, portfolio_id: str, params, cash: float) -> BasketOvernightStrategy:
    universe_cfg = dict(params["universe"])
    universe_provider_name = universe_cfg.pop("provider")
    universe = Registry.create(UniverseProvider, universe_provider_name, **universe_cfg)

    factor_specs: Dict[str, Tuple[Factor, float]] = {}
    for name, spec in params["factors"].items():
        spec = dict(spec)
        factor_name = spec.pop("factor")
        weight = spec.pop("weight")
        factor_specs[name] = (Registry.create(Factor, factor_name, **spec), weight)

    return BasketOvernightStrategy(
        repository,
        universe,
        params["benchmark_ticker"],
        factor_specs,
        dict(params.get("selection", {})),
        dict(params.get("weighting", {})),
        portfolio_id,
        rebalance_frequency=params.get("rebalance", "monthly"),
        target_vol=params.get("target_vol"),
        vol_window_days=params.get("vol_window_days", 60),
        hedge_ticker=params.get("hedge_ticker"),
        hedge_fraction=params.get("hedge_fraction"),
        beta_window_days=params.get("beta_window_days", 252),
        sectors=params.get("sectors"),
        min_history_days=params.get("min_history_days", 252),
        scoring_method=params.get("scoring", "zscore"),
    )

"""Daily portfolio snapshots produced by a backtest run, plus summary analytics.

Kept free of any plotting dependency; see backtest/visualization.py for rendering.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List

import pandas as pd

_EMPTY_SUMMARY_KEYS = (
    "start_value",
    "end_value",
    "total_return",
    "cagr",
    "volatility",
    "sharpe",
    "max_drawdown",
    "calmar",
    "num_trades",
)


@dataclass
class Report:
    snapshots: List[dict] = field(default_factory=list)
    trades: List[dict] = field(default_factory=list)
    annotations: List[dict] = field(default_factory=list)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.snapshots)

    def trades_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.trades)

    def trades_for(self, portfolio_id: str) -> pd.DataFrame:
        df = self.trades_frame()
        if df.empty:
            return df
        return df[df["portfolio"] == portfolio_id].sort_values("date")

    def portfolio_ids(self) -> List[str]:
        if not self.snapshots:
            return []
        return sorted(self.to_frame()["portfolio"].unique())

    def equity_curve(self, portfolio_id: str) -> pd.Series:
        df = self.to_frame()
        if df.empty:
            return pd.Series([], dtype=float, name="value")
        df = df[df["portfolio"] == portfolio_id].sort_values("date")
        return df.set_index("date")["value"]

    def drawdown_curve(self, portfolio_id: str) -> pd.Series:
        curve = self.equity_curve(portfolio_id)
        return curve / curve.cummax() - 1

    def summary(self, portfolio_id: str, trading_days_per_year: int = 252) -> dict:
        curve = self.equity_curve(portfolio_id)
        if len(curve) < 2:
            return {"portfolio": portfolio_id, **{k: 0.0 for k in _EMPTY_SUMMARY_KEYS}}

        returns = curve.pct_change().dropna()
        start_value, end_value = float(curve.iloc[0]), float(curve.iloc[-1])
        total_return = end_value / start_value - 1

        years = max((curve.index[-1] - curve.index[0]).days, 1) / 365.25
        cagr = (end_value / start_value) ** (1 / years) - 1 if start_value > 0 else 0.0

        mean, std = returns.mean(), returns.std()
        volatility = float(std * math.sqrt(trading_days_per_year)) if std else 0.0
        sharpe = float(mean / std * math.sqrt(trading_days_per_year)) if std else 0.0

        max_drawdown = float((curve / curve.cummax() - 1).min())
        calmar = cagr / abs(max_drawdown) if max_drawdown else 0.0

        return {
            "portfolio": portfolio_id,
            "start_value": start_value,
            "end_value": end_value,
            "total_return": float(total_return),
            "cagr": float(cagr),
            "volatility": volatility,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "calmar": float(calmar),
            "num_trades": len(self.trades_for(portfolio_id)),
        }

    def summary_all(self, trading_days_per_year: int = 252) -> pd.DataFrame:
        rows = [self.summary(pid, trading_days_per_year) for pid in self.portfolio_ids()]
        return pd.DataFrame(rows).set_index("portfolio")

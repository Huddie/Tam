"""Walk-forward validation: run the SAME config over several rolling
(train_start, train_end, test_start, test_end) windows, and stitch only each
window's TEST-period returns into one continuous out-of-sample Report --
never judged on a period a window's own run had "seen" being selected/tuned
against the whole history. `train_end` isn't used for anything beyond
documenting the window's own boundaries (this engine's strategies are
already point-in-time-safe by construction -- see tam/basket/factors.py --
so there's no separate "fit" step to bound); `train_start` IS used, as the
warm-up range a strategy needs real trailing history from before test_start
arrives, rather than starting stone cold exactly at test_start.

No engine change -- pure orchestration over runner._load()/BacktestHarness,
same shape as runner.py's own _drive().
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import List, Tuple

import pandas as pd

from .report import Report
from .runner import _load

Window = Tuple[date, date, date, date]  # (train_start, train_end, test_start, test_end)


def run_walk_forward(config_path: str | Path, windows: List[Window], starting_value: float = 100.0) -> Report:
    """Runs `config_path` once per window (over [train_start, test_end], so
    the strategy has real history by test_start), keeps only each window's
    [test_start, test_end] slice of the resulting equity curve, and chains
    those slices' own RETURNS (not absolute dollar levels -- each window is
    a fresh harness with fresh starting cash, so dollar levels aren't
    continuous across window boundaries, but returns are) into one
    continuous out-of-sample curve per portfolio, starting from
    `starting_value`. No checkpointing -- each window is expected to run to
    completion in one call, not resume mid-window."""
    config_path = Path(config_path)
    per_portfolio_test_curves: dict = {}

    for train_start, _train_end, test_start, test_end in windows:
        harness, _total_days, _backtest_settings, _report_settings, _price_series, _hash, _dir = _load(
            config_path, start_override=train_start, end_override=test_end
        )
        report = harness.run(checkpoint_path=None)

        for portfolio_id in report.portfolio_ids():
            curve = report.equity_curve(portfolio_id)
            # curve.index holds plain datetime.date (a harness snapshot's own
            # date type) -- compare against test_start/test_end as-is, not
            # wrapped in pd.Timestamp (which can't be compared to a bare date).
            test_slice = curve[(curve.index >= test_start) & (curve.index <= test_end)]
            if test_slice.empty:
                continue
            per_portfolio_test_curves.setdefault(portfolio_id, []).append(test_slice)

    stitched = {
        portfolio_id: _chain_returns(curves, starting_value)
        for portfolio_id, curves in per_portfolio_test_curves.items()
    }
    return Report.from_curves(stitched)


def _chain_returns(curves: List[pd.Series], starting_value: float) -> pd.Series:
    level = starting_value
    pieces = []
    for curve in curves:
        returns = curve.pct_change().fillna(0.0)
        wealth = level * (1 + returns).cumprod()
        pieces.append(wealth)
        level = float(wealth.iloc[-1])
    return pd.concat(pieces)

"""FeatureStore: a named Factor registry + point-in-time-safe materialization
+ a Parquet cache for the resulting panels.

`tam.basket.factors.Factor` + `tam.registry.Registry` already supply a
feature store's two properties that actually matter here: a named,
swappable registry, and point-in-time-safe computation (`Factor`'s own
`_window()` helper structurally prevents lookahead the same way a real
feature store's point-in-time join does). What was missing was an object
wrapping that with registration + materialization + caching, instead of a
bare `{name: Factor}` dict passed around by hand -- this class is that
object, sized and shaped for this codebase rather than a separate service
(Feast/Tecton/Hopsworks -- see docs/ml.md for why those were rejected).
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from ..basket.factors import Factor, compute_factors
from ..basket.matrix import price_matrix
from ..data.repository import DataRepository
from ..data.schema import CLOSE
from ..engine import Engine


class FeatureStore:
    """Register `Factor`s under a name, then `materialize()`/`with_labels()`
    a `(date, ticker) x feature` panel for a ticker list + date range.
    `cache_dir`, if given, memoizes the computed panel to a Parquet file
    keyed by a hash of everything that affects its content -- a cache hit
    skips recomputation entirely, the same "only do the work that's
    actually missing" shape `DataRepository.ingest()` already uses, not a
    new concept."""

    def __init__(self, repository: DataRepository, cache_dir: Optional[Union[str, Path]] = None):
        self._repository = repository
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._factors: dict[str, Factor] = {}

    def register(self, name: str, factor: Factor) -> "FeatureStore":
        self._factors[name] = factor
        return self

    def register_many(self, factors: dict[str, Factor]) -> "FeatureStore":
        for name, factor in factors.items():
            self.register(name, factor)
        return self

    @property
    def feature_names(self) -> list[str]:
        return list(self._factors)

    def _cache_key(self, tickers, start: date, end: date, horizon: Optional[int], warmup: int) -> str:
        payload = {
            "tickers": sorted(tickers),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "horizon": horizon,
            "warmup": warmup,
            # repr() of each Factor -- close enough to "its own config" for a
            # cache key without requiring every Factor to implement its own
            # hashing: two Factor instances built with the same args produce
            # the same class name + same __dict__ repr.
            "factors": sorted((name, repr(vars(factor))) for name, factor in self._factors.items()),
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        return digest

    def _cache_path(self, key: str) -> Optional[Path]:
        return self._cache_dir / f"{key}.parquet" if self._cache_dir else None

    def _materialize_pandas(self, tickers, start: date, end: date, warmup: int) -> pd.DataFrame:
        closes = price_matrix(self._repository, tickers, start, end, column=CLOSE)
        trading_days = closes.index[warmup:]

        rows = []
        for as_of in trading_days:
            table = compute_factors(closes, as_of.date(), self._factors)
            table.index.name = "ticker"
            table["date"] = as_of
            rows.append(table.reset_index())

        if not rows:
            return pd.DataFrame(columns=["date", "ticker", *self.feature_names]).set_index(["date", "ticker"])
        return pd.concat(rows, ignore_index=True).set_index(["date", "ticker"])

    def materialize(
        self, tickers, start: date, end: date, warmup: int = 60, *, engine: str = Engine.PANDAS
    ) -> pd.DataFrame:
        """`(date, ticker) x feature` panel, point-in-time safe by
        construction (inherits every registered `Factor`'s own `_window()`
        guarantee). Computed internally in pandas regardless of `engine`
        (`Factor.compute()`/`compute_factors()`/`price_matrix()` are pandas
        end-to-end -- their own contract, not rewritten here); `engine="polars"`
        converts ONLY at this return boundary, the identical pattern
        `Symbol.financials()` already uses for the same reason."""
        cache_key = self._cache_key(tickers, start, end, horizon=None, warmup=warmup)
        cache_path = self._cache_path(cache_key)
        if cache_path is not None and cache_path.exists():
            panel = pd.read_parquet(cache_path)
        else:
            panel = self._materialize_pandas(tickers, start, end, warmup)
            if cache_path is not None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                panel.to_parquet(cache_path)

        if engine == Engine.POLARS:
            import polars as pl

            return pl.from_pandas(panel.reset_index())
        return panel

    def with_labels(
        self, tickers, start: date, end: date, horizon: int, warmup: int = 60, *, engine: str = Engine.PANDAS
    ) -> pd.DataFrame:
        """`materialize()` + the forward-`horizon`-day return label, joined
        and ready for `tam.ml.dataset.time_split()`. The returned frame's
        `.attrs["horizon"]` carries `horizon` through so `time_split()`'s
        default leakage `gap` can't be forgotten by a caller."""
        cache_key = self._cache_key(tickers, start, end, horizon=horizon, warmup=warmup)
        cache_path = self._cache_path(f"{cache_key}_labeled")
        if cache_path is not None and cache_path.exists():
            dataset = pd.read_parquet(cache_path)
        else:
            closes = price_matrix(self._repository, tickers, start, end, column=CLOSE)
            panel = self.materialize(tickers, start, end, warmup)

            forward_return = closes.shift(-horizon) / closes - 1
            labels = forward_return.stack()
            labels.index.names = ["date", "ticker"]
            labels.name = f"fwd_return_{horizon}d"

            dataset = panel.join(labels, how="inner").dropna()
            if cache_path is not None:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                dataset.to_parquet(cache_path)

        dataset.attrs["horizon"] = horizon
        dataset.attrs["label_col"] = f"fwd_return_{horizon}d"

        if engine == Engine.POLARS:
            import polars as pl

            return pl.from_pandas(dataset.reset_index())
        return dataset

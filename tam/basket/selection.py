"""Correlation-cluster diversification: don't just buy the top-N by score --
several top-scored tickers can all be expressing the same underlying edge
(e.g. "the semiconductor overnight factor"), which breaks together the
moment that one factor weakens. Cluster by return correlation, then cap how
many picks come from any one cluster.
"""

from __future__ import annotations

import pandas as pd


def cluster(
    returns: pd.DataFrame,
    n_clusters: int,
    downside_quantile: float | None = None,
    market_returns: pd.Series | None = None,
) -> pd.Series:
    """{ticker: cluster_id}, from agglomerative clustering on correlation
    distance ((1 - corr) / 2, bounded [0, 1]).

    `downside_quantile`, if given, restricts the correlation calculation to
    the worst `downside_quantile` fraction of days (by `market_returns`,
    defaulting to `returns`' own cross-sectional mean per day) -- tickers
    that look uncorrelated normally can still crash together on bad nights;
    this is how you'd actually catch that (research doc's "downside
    correlation," not plain correlation)."""
    from sklearn.cluster import AgglomerativeClustering

    frame = returns
    if downside_quantile is not None:
        market = market_returns if market_returns is not None else returns.mean(axis=1)
        threshold = market.quantile(downside_quantile)
        frame = returns.loc[market <= threshold]

    corr = frame.corr().fillna(0.0)
    if len(corr) <= 1:
        return pd.Series(0, index=corr.index)

    distance = ((1 - corr) / 2).clip(lower=0.0)
    k = min(n_clusters, len(corr))
    if k <= 1:
        return pd.Series(0, index=corr.index)

    model = AgglomerativeClustering(n_clusters=k, metric="precomputed", linkage="average")
    labels = model.fit_predict(distance.values)
    return pd.Series(labels, index=corr.index)


def select_diversified(scores: pd.Series, clusters: pd.Series, n: int, max_per_cluster: int) -> list[str]:
    """Highest-scored tickers first, skipping any ticker once its cluster has
    already contributed `max_per_cluster` picks -- "select across clusters,"
    not just top-N by score regardless of how concentrated they are."""
    cluster_counts: dict = {}
    selected: list[str] = []
    for ticker in scores.sort_values(ascending=False).index:
        if len(selected) >= n:
            break
        cluster_id = clusters.get(ticker)
        if cluster_counts.get(cluster_id, 0) >= max_per_cluster:
            continue
        selected.append(ticker)
        cluster_counts[cluster_id] = cluster_counts.get(cluster_id, 0) + 1
    return selected

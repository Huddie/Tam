from datetime import date, timedelta

import pandas as pd

from tam.data.providers import YFinanceProvider
from tam.data.schema import OHLCV_COLUMNS


def test_yfinance_flattens_multiindex_columns(monkeypatch):
    """Regression test: newer yfinance returns (field, ticker) MultiIndex columns even
    for a single symbol, which used to make history["close"] a 1-column DataFrame
    instead of a Series and break float(history["close"].iloc[0]) downstream."""
    dates = pd.to_datetime(["2023-01-03", "2023-01-04"])
    columns = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Adj Close", "Volume"], ["AAPL"]])
    raw = pd.DataFrame(
        [
            [100.0, 101.0, 99.0, 100.5, 100.5, 1000],
            [101.0, 102.0, 100.0, 101.5, 101.5, 1100],
        ],
        index=dates,
        columns=columns,
    )

    monkeypatch.setattr("yfinance.download", lambda *args, **kwargs: raw)

    df = YFinanceProvider().fetch_eod("AAPL", date(2023, 1, 3), date(2023, 1, 4))

    assert list(df.columns) == OHLCV_COLUMNS
    assert isinstance(df["close"].iloc[0], float)
    assert df["close"].iloc[0] == 100.5
    assert df["close"].iloc[1] == 101.5


def test_yfinance_empty_download_returns_empty_frame(monkeypatch):
    monkeypatch.setattr("yfinance.download", lambda *args, **kwargs: pd.DataFrame())

    df = YFinanceProvider().fetch_eod("AAPL", date(2023, 1, 3), date(2023, 1, 4))

    assert df.empty
    assert list(df.columns) == OHLCV_COLUMNS


def test_yfinance_end_date_is_treated_as_inclusive(monkeypatch):
    """Regression test: yf.download's `end` is exclusive of that calendar day, so
    without padding, the last day of any requested range would never be fetched --
    and since DataRepository's gap-fill logic re-detects that exact 1-day gap on
    every future ingest, it would retry (and fail) forever instead of just once."""
    captured = {}

    def fake_download(symbol, start, end, **kwargs):
        captured["end"] = end
        all_dates = pd.to_datetime(["2023-01-03", "2023-01-04", "2023-01-05"])
        exclusive = all_dates[all_dates < pd.Timestamp(end)]
        return pd.DataFrame(
            {
                "Open": [1.0] * len(exclusive),
                "High": [1.0] * len(exclusive),
                "Low": [1.0] * len(exclusive),
                "Close": [1.0] * len(exclusive),
                "Adj Close": [1.0] * len(exclusive),
                "Volume": [10] * len(exclusive),
            },
            index=exclusive,
        )

    monkeypatch.setattr("yfinance.download", fake_download)

    requested_end = date(2023, 1, 4)
    df = YFinanceProvider().fetch_eod("AAPL", date(2023, 1, 3), requested_end)

    assert captured["end"] == requested_end + timedelta(days=1)
    assert list(df.index.date) == [date(2023, 1, 3), date(2023, 1, 4)]


def test_yfinance_translates_dot_share_class_tickers_to_hyphenated_form(monkeypatch):
    """Regression test: index/vendor data (Wikipedia's S&P 500 table, pitindex,
    ...) spells share-class tickers with a dot ("BRK.B", "BF.B"), but yfinance's
    own API only recognizes the hyphenated form ("BRK-B") -- querying it with the
    dot form returns nothing, which used to make price_matrix() silently drop the
    ticker's whole column and then blow up downstream with a confusing KeyError."""
    captured = {}

    def fake_download(symbol, **kwargs):
        captured["symbol"] = symbol
        return pd.DataFrame()

    monkeypatch.setattr("yfinance.download", fake_download)

    YFinanceProvider().fetch_eod("BRK.B", date(2023, 1, 3), date(2023, 1, 4))

    assert captured["symbol"] == "BRK-B"


def test_yfinance_defaults_to_unadjusted_ohlc(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "yfinance.download", lambda *a, **kw: captured.update(auto_adjust=kw["auto_adjust"]) or pd.DataFrame()
    )

    YFinanceProvider().fetch_eod("AAPL", date(2023, 1, 3), date(2023, 1, 4))

    assert captured["auto_adjust"] is False


def test_yfinance_adjust_true_requests_auto_adjusted_ohlc_from_yfinance(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "yfinance.download", lambda *a, **kw: captured.update(auto_adjust=kw["auto_adjust"]) or pd.DataFrame()
    )

    YFinanceProvider(adjust=True).fetch_eod("AAPL", date(2023, 1, 3), date(2023, 1, 4))

    assert captured["auto_adjust"] is True


def test_yfinance_adjust_true_fills_adj_close_from_close_when_yfinance_omits_it(monkeypatch):
    # yfinance's own auto_adjust=True response has no separate "Adj Close"
    # column at all -- "Close" IS already the adjusted close.
    dates = pd.to_datetime(["2023-01-03", "2023-01-04"])
    raw = pd.DataFrame(
        {"Open": [10.0, 11.0], "High": [10.5, 11.5], "Low": [9.5, 10.5], "Close": [10.2, 11.2], "Volume": [100, 200]},
        index=dates,
    )
    monkeypatch.setattr("yfinance.download", lambda *a, **kw: raw)

    df = YFinanceProvider(adjust=True).fetch_eod("AAPL", date(2023, 1, 3), date(2023, 1, 4))

    assert list(df["adj_close"]) == list(df["close"])


def test_yfinance_handles_a_duplicate_close_column_without_crashing(monkeypatch):
    # Regression test: a real Colab traceback hit "ValueError: Cannot set a
    # DataFrame with multiple columns to the single column adj_close" --
    # some yfinance version/auto_adjust combination returned two columns
    # both named "Close", making df[CLOSE] a DataFrame instead of a Series
    # by the time `df[ADJ_CLOSE] = df[CLOSE]` ran.
    dates = pd.to_datetime(["2023-01-03", "2023-01-04"])
    raw = pd.DataFrame(
        [[10.0, 10.5, 9.5, 10.2, 10.2, 100], [11.0, 11.5, 10.5, 11.2, 11.2, 200]],
        index=dates,
        columns=["Open", "High", "Low", "Close", "Close", "Volume"],
    )
    monkeypatch.setattr("yfinance.download", lambda *a, **kw: raw)

    df = YFinanceProvider(adjust=True).fetch_eod("AAPL", date(2023, 1, 3), date(2023, 1, 4))

    assert isinstance(df["close"], pd.Series)
    assert list(df["close"]) == [10.2, 11.2]
    assert list(df["adj_close"]) == [10.2, 11.2]


def test_yfinance_adjusted_provider_is_registered_and_zero_arg_constructible():
    from tam.data.providers import DataProvider, YFinanceAdjustedProvider
    from tam.registry import Registry

    provider = Registry.get(DataProvider, "yfinance_adjusted")

    assert isinstance(provider, YFinanceAdjustedProvider)
    assert provider._adjust is True


def test_importing_providers_quiets_yfinances_own_noisy_error_logging():
    # yfinance logs its own ERROR-level "possibly delisted; no price data
    # found" for the exact case DataRepository.ingest() already surfaces as
    # one clear UserWarning -- quieted at import time so that duplicate,
    # noisier signal doesn't drown out our own.
    import logging

    import tam.data.providers  # noqa: F401 -- already imported; explicit for clarity

    assert logging.getLogger("yfinance").level == logging.CRITICAL


def test_yfinance_fetch_eod_calls_never_overlap_across_threads(monkeypatch):
    # Regression test: a real corrupted cache was traced back to
    # DataRepository.ingest()'s thread pool calling fetch_eod() for
    # DIFFERENT symbols concurrently -- yfinance keeps shared, ticker-keyed
    # global state internally that isn't safe under that. The provider-level
    # lock must serialize every yf.download() call, regardless of how many
    # threads call fetch_eod() at once.
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    in_flight = 0
    max_observed = 0
    lock = threading.Lock()

    def fake_download(symbol, **kwargs):
        nonlocal in_flight, max_observed
        with lock:
            in_flight += 1
            max_observed = max(max_observed, in_flight)
        time.sleep(0.05)
        with lock:
            in_flight -= 1
        return pd.DataFrame()

    monkeypatch.setattr("yfinance.download", fake_download)

    provider = YFinanceProvider()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(lambda s: provider.fetch_eod(s, date(2023, 1, 3), date(2023, 1, 4)), [f"SYM{i}" for i in range(8)])
        )

    assert max_observed == 1

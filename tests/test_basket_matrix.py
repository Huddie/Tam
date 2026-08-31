import warnings
from datetime import date

import pandas as pd
import pytest

from tam.basket.matrix import price_matrix
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.schema import CLOSE, OHLCV_COLUMNS, OPEN
from tam.data.storage import CsvStore


class _TwoTickerProvider(DataProvider):
    def __init__(self, frames):
        self._frames = frames

    def fetch_eod(self, symbol, start, end):
        df = self._frames[symbol]
        return df[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))]


def _bars(dates, opens, closes):
    idx = pd.to_datetime(dates)
    return pd.DataFrame(
        {
            "open": opens,
            "high": closes,
            "low": opens,
            "close": closes,
            "adj_close": closes,
            "volume": [100] * len(dates),
        },
        index=idx,
    ).rename_axis("date")[OHLCV_COLUMNS]


def _repo(tmp_path, frames):
    return DataRepository(_TwoTickerProvider(frames), CsvStore(tmp_path))


_DATES = ["2024-01-02", "2024-01-03", "2024-01-04"]


def test_price_matrix_defaults_to_close(tmp_path):
    frames = {
        "AAPL": _bars(_DATES, opens=[100, 102, 103], closes=[101, 103, 104]),
        "MSFT": _bars(_DATES, opens=[200, 199, 205], closes=[199, 205, 206]),
    }
    repo = _repo(tmp_path, frames)

    matrix = price_matrix(repo, ["AAPL", "MSFT"], date(2024, 1, 2), date(2024, 1, 4))

    assert list(matrix.columns) == ["AAPL", "MSFT"]
    assert list(matrix["AAPL"]) == [101, 103, 104]
    assert list(matrix["MSFT"]) == [199, 205, 206]


def test_price_matrix_accepts_any_ohlcv_column(tmp_path):
    frames = {"AAPL": _bars(_DATES, opens=[100, 102, 103], closes=[101, 103, 104])}
    repo = _repo(tmp_path, frames)

    matrix = price_matrix(repo, ["AAPL"], date(2024, 1, 2), date(2024, 1, 4), column=OPEN)

    assert list(matrix["AAPL"]) == [100, 102, 103]


def test_overnight_and_intraday_returns_compose_from_two_price_matrices(tmp_path):
    # The actual point: no dedicated "overnight_return_matrix" function --
    # every return definition is one line of pandas on top of price_matrix().
    frames = {
        "AAPL": _bars(_DATES, opens=[100, 102, 103], closes=[101, 103, 104]),
        "MSFT": _bars(_DATES, opens=[200, 199, 205], closes=[199, 205, 206]),
    }
    repo = _repo(tmp_path, frames)

    opens = price_matrix(repo, ["AAPL", "MSFT"], date(2024, 1, 2), date(2024, 1, 4), column=OPEN)
    closes = price_matrix(repo, ["AAPL", "MSFT"], date(2024, 1, 2), date(2024, 1, 4), column=CLOSE)

    overnight = opens.shift(-1) / closes - 1
    intraday = closes / opens - 1

    assert overnight["AAPL"].iloc[0] == pytest.approx(102 / 101 - 1)
    assert overnight["AAPL"].isna().iloc[-1]  # no next day's open in range
    assert intraday["AAPL"].iloc[0] == pytest.approx(101 / 100 - 1)
    assert not intraday.isna().any().any()


def test_price_matrix_ingests_missing_tickers_itself(tmp_path):
    frames = {"AAPL": _bars(_DATES, opens=[100, 102, 103], closes=[101, 103, 104])}
    repo = _repo(tmp_path, frames)

    # No prior ingest() call -- price_matrix() must do it itself.
    matrix = price_matrix(repo, ["AAPL"], date(2024, 1, 2), date(2024, 1, 4))

    assert len(matrix) == 3


def test_price_matrix_omits_a_ticker_with_no_data(tmp_path):
    frames = {"AAPL": _bars(_DATES, opens=[100, 102, 103], closes=[101, 103, 104]), "MSFT": _bars([], [], [])}
    repo = _repo(tmp_path, frames)

    matrix = price_matrix(repo, ["AAPL", "MSFT"], date(2024, 1, 2), date(2024, 1, 4))

    assert list(matrix.columns) == ["AAPL"]


def test_price_matrix_warn_false_silences_the_no_data_warning(tmp_path):
    frames = {"AAPL": _bars(_DATES, opens=[100, 102, 103], closes=[101, 103, 104]), "MSFT": _bars([], [], [])}
    repo = _repo(tmp_path, frames)

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning fails the test
        matrix = price_matrix(repo, ["AAPL", "MSFT"], date(2024, 1, 2), date(2024, 1, 4), warn=False)

    assert list(matrix.columns) == ["AAPL"]

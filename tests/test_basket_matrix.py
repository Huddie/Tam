from datetime import date

import pandas as pd
import pytest

from tam.basket.matrix import intraday_return_matrix, overnight_return_matrix
from tam.data.providers import DataProvider
from tam.data.repository import DataRepository
from tam.data.schema import OHLCV_COLUMNS
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
        {"open": opens, "high": closes, "low": opens, "close": closes, "adj_close": closes, "volume": [100] * len(dates)},
        index=idx,
    ).rename_axis("date")[OHLCV_COLUMNS]


def _repo(tmp_path, frames):
    return DataRepository(_TwoTickerProvider(frames), CsvStore(tmp_path))


_DATES = ["2024-01-02", "2024-01-03", "2024-01-04"]


def test_overnight_return_matrix_matches_hand_computed_values(tmp_path):
    frames = {
        "AAPL": _bars(_DATES, opens=[100, 102, 103], closes=[101, 103, 104]),
        "MSFT": _bars(_DATES, opens=[200, 199, 205], closes=[199, 205, 206]),
    }
    repo = _repo(tmp_path, frames)

    matrix = overnight_return_matrix(repo, ["AAPL", "MSFT"], date(2024, 1, 2), date(2024, 1, 4))

    assert list(matrix.columns) == ["AAPL", "MSFT"]
    assert matrix["AAPL"].iloc[0] == pytest.approx(102 / 101 - 1)
    assert matrix["MSFT"].iloc[0] == pytest.approx(199 / 199 - 1)
    # Last row has no next day's open yet -> NaN, not dropped.
    assert matrix["AAPL"].isna().iloc[-1]


def test_intraday_return_matrix_matches_hand_computed_values(tmp_path):
    frames = {
        "AAPL": _bars(_DATES, opens=[100, 102, 103], closes=[101, 103, 104]),
        "MSFT": _bars(_DATES, opens=[200, 199, 205], closes=[199, 205, 206]),
    }
    repo = _repo(tmp_path, frames)

    matrix = intraday_return_matrix(repo, ["AAPL", "MSFT"], date(2024, 1, 2), date(2024, 1, 4))

    assert matrix["AAPL"].iloc[0] == pytest.approx(101 / 100 - 1)
    assert matrix["MSFT"].iloc[0] == pytest.approx(199 / 200 - 1)
    # Every row is fillable for intraday (same-day open/close both exist).
    assert not matrix.isna().any().any()


def test_overnight_return_matrix_ingests_missing_tickers_itself(tmp_path):
    frames = {"AAPL": _bars(_DATES, opens=[100, 102, 103], closes=[101, 103, 104])}
    repo = _repo(tmp_path, frames)

    # No prior ingest() call -- overnight_return_matrix() must do it itself.
    matrix = overnight_return_matrix(repo, ["AAPL"], date(2024, 1, 2), date(2024, 1, 4))

    assert len(matrix) == 3


def test_overnight_return_matrix_skips_a_ticker_with_no_data(tmp_path):
    frames = {"AAPL": _bars(_DATES, opens=[100, 102, 103], closes=[101, 103, 104]), "MSFT": _bars([], [], [])}
    repo = _repo(tmp_path, frames)

    matrix = overnight_return_matrix(repo, ["AAPL", "MSFT"], date(2024, 1, 2), date(2024, 1, 4))

    assert list(matrix.columns) == ["AAPL"]

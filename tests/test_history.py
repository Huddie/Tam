from datetime import date

import pandas as pd
import pytest

from tam.data.history import SymbolHistory


def _frame(dates, opens, closes):
    index = pd.to_datetime(dates)
    return pd.DataFrame({"open": opens, "close": closes}, index=index).rename_axis("date")


def test_price_at_returns_most_recent_bar_on_or_before_as_of():
    history = SymbolHistory(_frame(["2024-01-02", "2024-01-03", "2024-01-05"], [1, 2, 3], [10, 20, 30]))

    assert history.price_at(date(2024, 1, 3), "close") == 20
    # No bar on Jan 4 -- falls back to the most recent prior bar (Jan 3).
    assert history.price_at(date(2024, 1, 4), "close") == 20
    assert history.price_at(date(2024, 1, 2), "open") == 1


def test_price_at_raises_lookup_error_before_any_data():
    history = SymbolHistory(_frame(["2024-01-05"], [1], [10]))

    with pytest.raises(LookupError):
        history.price_at(date(2024, 1, 4), "close")


def test_window_ending_returns_last_n_bars_up_to_as_of():
    history = SymbolHistory(_frame(["2024-01-02", "2024-01-03", "2024-01-04"], [1, 2, 3], [10, 20, 30]))

    window = history.window_ending(date(2024, 1, 4), 2)

    assert list(window["close"]) == [20, 30]


def test_window_ending_returns_fewer_rows_when_not_enough_history():
    history = SymbolHistory(_frame(["2024-01-02"], [1], [10]))

    window = history.window_ending(date(2024, 1, 3), 5)

    assert list(window["close"]) == [10]


def test_window_ending_excludes_bars_after_as_of():
    history = SymbolHistory(_frame(["2024-01-02", "2024-01-03", "2024-01-04"], [1, 2, 3], [10, 20, 30]))

    window = history.window_ending(date(2024, 1, 3), 5)

    assert list(window["close"]) == [10, 20]

from datetime import date

import pandas as pd
import pytest

from tam.basket.universe import CsvUniverse, StaticUniverse, UniverseProvider
from tam.registry import Registry


def test_static_universe_ignores_as_of():
    universe = StaticUniverse(["AAPL", "MSFT"])

    assert universe.constituents(date(2000, 1, 1)) == ["AAPL", "MSFT"]
    assert universe.constituents(date(2030, 1, 1)) == ["AAPL", "MSFT"]


def test_csv_universe_replays_additions_and_removals_up_to_as_of(tmp_path):
    path = tmp_path / "membership.csv"
    pd.DataFrame(
        [
            {"date": "2010-01-01", "ticker": "AAPL", "action": "add"},
            {"date": "2010-01-01", "ticker": "MSFT", "action": "add"},
            {"date": "2015-06-01", "ticker": "MSFT", "action": "remove"},
            {"date": "2015-06-01", "ticker": "NVDA", "action": "add"},
        ]
    ).to_csv(path, index=False)

    universe = CsvUniverse(path)

    assert universe.constituents(date(2000, 1, 1)) == []
    assert universe.constituents(date(2012, 1, 1)) == ["AAPL", "MSFT"]
    assert universe.constituents(date(2020, 1, 1)) == ["AAPL", "NVDA"]


def test_csv_universe_is_point_in_time_safe_a_later_removal_does_not_leak_backward(tmp_path):
    # The whole point of a UniverseProvider: constituents(T) must not be
    # affected by an event dated after T.
    path = tmp_path / "membership.csv"
    pd.DataFrame(
        [
            {"date": "2010-01-01", "ticker": "AAPL", "action": "add"},
            {"date": "2020-01-01", "ticker": "AAPL", "action": "remove"},
        ]
    ).to_csv(path, index=False)

    universe = CsvUniverse(path)

    assert universe.constituents(date(2015, 1, 1)) == ["AAPL"]


def test_csv_universe_rejects_an_unknown_action(tmp_path):
    path = tmp_path / "membership.csv"
    pd.DataFrame([{"date": "2010-01-01", "ticker": "AAPL", "action": "delist"}]).to_csv(path, index=False)

    with pytest.raises(ValueError, match="delist"):
        CsvUniverse(path)


def test_builtin_universe_providers_are_registered():
    assert set(Registry.names(UniverseProvider)) >= {"static", "csv"}

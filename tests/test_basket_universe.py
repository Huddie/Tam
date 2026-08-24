from datetime import date

import pandas as pd
import pytest

from tam.basket.universe import (
    CsvUniverse,
    PitIndexUniverse,
    StaticUniverse,
    UniverseProvider,
    WikipediaUniverse,
    _find_column,
    _find_column_any,
    _label,
    build_membership_events,
)
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
    assert set(Registry.names(UniverseProvider)) >= {"static", "csv", "wikipedia", "pitindex"}


def test_csv_universe_shares_replay_logic_via_events_universe_base():
    from tam.basket.universe import _EventsUniverse

    assert issubclass(CsvUniverse, _EventsUniverse)
    assert issubclass(WikipediaUniverse, _EventsUniverse)


def test_wikipedia_universe_replays_events_from_fetch_sp500_membership(monkeypatch):
    # WikipediaUniverse is just fetch_sp500_membership() wrapped as an
    # _EventsUniverse -- no network needed to test the replay wiring itself.
    events = pd.DataFrame(
        [
            {"date": "2010-01-01", "ticker": "AAPL", "action": "add"},
            {"date": "2015-06-01", "ticker": "AAPL", "action": "remove"},
        ]
    )
    events["date"] = pd.to_datetime(events["date"])
    monkeypatch.setattr("tam.basket.universe.fetch_sp500_membership", lambda: events)

    universe = WikipediaUniverse()

    assert universe.constituents(date(2012, 1, 1)) == ["AAPL"]
    assert universe.constituents(date(2020, 1, 1)) == []


def test_pitindex_universe_returns_real_sp500_constituents():
    pytest.importorskip("pitindex")

    universe = PitIndexUniverse()
    tickers = universe.constituents(date(2024, 1, 2))

    assert len(tickers) > 100
    assert tickers == sorted(tickers)


def test_pitindex_universe_supports_other_indices():
    pytest.importorskip("pitindex")

    sp600 = PitIndexUniverse(index="sp600").constituents(date(2024, 1, 2))
    sp500 = PitIndexUniverse(index="sp500").constituents(date(2024, 1, 2))

    assert len(sp600) > 100
    assert sp600 != sp500


def test_pitindex_universe_is_registered_and_creatable_via_registry():
    pytest.importorskip("pitindex")

    universe = Registry.create(UniverseProvider, "pitindex", index="sp400")

    assert isinstance(universe, PitIndexUniverse)
    assert len(universe.constituents(date(2024, 1, 2))) > 100


def test_pitindex_universe_raises_a_clear_error_without_the_extra_installed(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pitindex":
            raise ImportError("no module named pitindex")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError, match="pitindex.*extra"):
        PitIndexUniverse().constituents(date(2024, 1, 2))


def test_label_joins_a_multiindex_tuple_column_lowercased():
    assert _label(("Added", "Ticker")) == "added ticker"
    assert _label("Symbol") == "symbol"


def test_find_column_matches_by_keyword_not_exact_position():
    columns = [("Date", "Date"), ("Added", "Ticker"), ("Added", "Security"), ("Removed", "Ticker")]

    assert _find_column(columns, ("added", "ticker")) == ("Added", "Ticker")
    assert _find_column(columns, ("removed", "ticker")) == ("Removed", "Ticker")


def test_find_column_raises_a_clear_error_listing_actual_columns():
    with pytest.raises(ValueError, match=r"no column matching.*among \['a', 'b'\]"):
        _find_column(["a", "b"], ("nope",))


def test_find_column_any_tries_each_option_in_order():
    assert _find_column_any(["Symbol", "Security"], [("ticker",), ("symbol",)]) == "Symbol"


def test_find_column_any_raises_when_nothing_matches():
    with pytest.raises(ValueError, match="no column matched any of"):
        _find_column_any(["a", "b"], [("nope",), ("also_nope",)])


def test_build_membership_events_produces_csv_universe_compatible_output(tmp_path):
    current = ["AAPL", "MSFT", "NVDA", "TSLA"]
    changes = pd.DataFrame(
        [
            {"date": "2020-06-01", "added_ticker": "TSLA", "removed_ticker": "XYZ"},
            {"date": "2015-03-01", "added_ticker": "NVDA", "removed_ticker": None},
        ]
    )

    events = build_membership_events(current, changes)

    assert list(events.columns) == ["date", "ticker", "action"]
    # AAPL/MSFT never appear as an added_ticker -> fallback-dated add, before
    # the earliest explicit change (2015-03-01).
    assert set(events[events["action"] == "add"]["ticker"]) == {"AAPL", "MSFT", "NVDA", "TSLA"}
    assert events[events["ticker"] == "XYZ"]["action"].item() == "remove"

    path = tmp_path / "membership.csv"
    events.to_csv(path, index=False)
    universe = CsvUniverse(path)

    assert universe.constituents(date(2010, 1, 1)) == []  # before the fallback date
    assert universe.constituents(date(2016, 1, 1)) == ["AAPL", "MSFT", "NVDA"]
    assert universe.constituents(date(2021, 1, 1)) == ["AAPL", "MSFT", "NVDA", "TSLA"]


def test_build_membership_events_accepts_an_explicit_fallback_date():
    events = build_membership_events(["AAPL"], pd.DataFrame(columns=["date", "added_ticker", "removed_ticker"]), fallback_date=date(1999, 1, 1))

    assert events.iloc[0]["date"] == pd.Timestamp(1999, 1, 1)


def test_build_membership_events_handles_a_pure_addition_or_pure_removal_row():
    changes = pd.DataFrame(
        [
            {"date": "2020-01-01", "added_ticker": "NEW", "removed_ticker": None},
            {"date": "2021-01-01", "added_ticker": None, "removed_ticker": "OLD"},
        ]
    )

    events = build_membership_events([], changes)

    assert set(zip(events["ticker"], events["action"])) == {("NEW", "add"), ("OLD", "remove")}

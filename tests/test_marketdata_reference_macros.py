import pandas as pd
import pytest

from tam.marketdata.duckdb_query import open_duckdb
from tam.marketdata.reference_store import LocalReferenceStore


@pytest.fixture
def local_root(tmp_path):
    store = LocalReferenceStore(tmp_path)
    store.write(
        "splits",
        pd.DataFrame(
            [
                {"id": "s1", "ticker": "AAPL", "execution_date": "2020-08-31", "split_from": 1, "split_to": 4, "adjustment_type": "forward_split", "historical_adjustment_factor": 0.25},
                {"id": "s2", "ticker": "TSLA", "execution_date": "2021-08-25", "split_from": 1, "split_to": 5, "adjustment_type": "forward_split", "historical_adjustment_factor": 0.2},
            ]
        ),
    )
    store.write(
        "float",
        pd.DataFrame([{"ticker": "AAPL", "effective_date": "2025-11-01", "free_float": 15000000000, "free_float_percent": 98.5}]),
    )
    return tmp_path


def test_splits_macro_with_no_ticker_returns_every_row(local_root):
    con = open_duckdb(local_root=str(local_root))
    df = con.sql("SELECT * FROM splits()").df()
    assert len(df) == 2


def test_splits_macro_filters_by_ticker_case_insensitively(local_root):
    con = open_duckdb(local_root=str(local_root))
    df = con.sql("SELECT * FROM splits('aapl')").df()
    assert len(df) == 1
    assert df["ticker"].iloc[0] == "AAPL"


def test_splits_macro_with_unmatched_ticker_returns_empty_with_right_columns(local_root):
    con = open_duckdb(local_root=str(local_root))
    df = con.sql("SELECT * FROM splits('MSFT')").df()
    assert df.empty
    assert "historical_adjustment_factor" in df.columns


def test_float_data_macro_reads_the_positioning_prefix(local_root):
    con = open_duckdb(local_root=str(local_root))
    df = con.sql("SELECT * FROM float_data('AAPL')").df()
    assert len(df) == 1
    assert df["free_float_percent"].iloc[0] == 98.5


def test_reference_macros_register_without_the_data_present(tmp_path):
    """Regression: a zero-arg-looking macro (ipos()/float_data()) must not
    eagerly resolve its read_parquet() glob at CREATE MACRO time -- same
    bug class as sec_companies()'s own earlier fix this session. Confirmed
    by opening a connection against a tree with NEITHER prefix at all --
    open_duckdb() itself (macro REGISTRATION) must not raise, even though
    actually SELECTing from a macro with no underlying files still does
    (same established convention as sec_companies() -- the raw macro
    layer raises on missing data; a Python wrapper catching that and
    returning empty is a separate, higher layer this marketdata-style
    exposure deliberately doesn't have)."""
    open_duckdb(local_root=str(tmp_path))  # must not raise

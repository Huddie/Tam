import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

from tam.cache import ManualCache  # noqa: E402
from tam.engine import Engine  # noqa: E402
from tam.marketdata.reference_store import LocalReferenceStore  # noqa: E402
from tam.query import query  # noqa: E402


@pytest.fixture
def local_root(tmp_path):
    store = LocalReferenceStore(tmp_path)
    store.write(
        "splits",
        pd.DataFrame(
            [
                {
                    "id": "s1",
                    "ticker": "AAPL",
                    "execution_date": "2020-08-31",
                    "split_from": 1.0,
                    "split_to": 4.0,
                    "adjustment_type": "forward_split",
                    "historical_adjustment_factor": 0.25,
                }
            ]
        ),
    )
    return tmp_path


def test_query_runs_raw_sql_against_the_given_local_root(local_root):
    df = query("SELECT * FROM splits()", local_root=str(local_root))

    assert len(df) == 1
    assert df["ticker"].iloc[0] == "AAPL"


def test_query_accepts_an_existing_connection_via_con(local_root):
    from tam.marketdata.duckdb_query import open_duckdb

    con = open_duckdb(local_root=str(local_root))

    df = query("SELECT * FROM splits()", con=con)

    assert len(df) == 1


def test_query_cache_hit_skips_the_connection(local_root):
    cache = ManualCache()
    from tam.marketdata.duckdb_query import open_duckdb

    con = open_duckdb(local_root=str(local_root))
    calls = []
    original_sql = con.sql

    class _Proxy:
        def sql(self, sql):
            calls.append(sql)
            return original_sql(sql)

    proxy = _Proxy()

    query("SELECT * FROM splits()", con=proxy, cache=cache)
    query("SELECT * FROM splits()", con=proxy, cache=cache)

    assert len(calls) == 1


def test_engine_enum_is_equivalent_to_the_plain_string(local_root):
    pytest.importorskip("polars")
    import polars as pl

    by_enum = query("SELECT * FROM splits()", local_root=str(local_root), engine=Engine.POLARS)
    by_string = query("SELECT * FROM splits()", local_root=str(local_root), engine="polars")

    assert isinstance(by_enum, pl.DataFrame)
    assert by_enum.equals(by_string)

import pandas as pd
import pytest

duckdb = pytest.importorskip("duckdb")

from tam.marketdata.reference_store import LocalReferenceStore  # noqa: E402
from tam.marketdata.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, SYMBOL, TRANSACTIONS, VOLUME  # noqa: E402
from tam.marketdata.store import LocalMinuteBarStore  # noqa: E402
from tam.symbol import CIK, Symbol  # noqa: E402


def _short_row(ticker: str, date: str, short_volume: float = 100.0) -> dict:
    return {
        "ticker": ticker,
        "date": date,
        "short_volume": short_volume,
        "total_volume": 1000.0,
        "short_volume_ratio": 0.1,
        "exempt_volume": 0.0,
        "non_exempt_volume": short_volume,
        "adf_short_volume": 0.0,
        "adf_short_volume_exempt": 0.0,
        "nasdaq_carteret_short_volume": 0.0,
        "nasdaq_carteret_short_volume_exempt": 0.0,
        "nasdaq_chicago_short_volume": 0.0,
        "nasdaq_chicago_short_volume_exempt": 0.0,
        "nyse_short_volume": 0.0,
        "nyse_short_volume_exempt": 0.0,
    }


def _split_row(ticker: str, execution_date: str) -> dict:
    return {
        "id": f"{ticker}-{execution_date}",
        "ticker": ticker,
        "execution_date": execution_date,
        "split_from": 1.0,
        "split_to": 4.0,
        "adjustment_type": "forward_split",
        "historical_adjustment_factor": 0.25,
    }


def _write_minute_bars(
    root, symbol: str, day: str = "2024-01-02", periods: int = 5, start_close: float = 100.0
) -> None:
    store = LocalMinuteBarStore(f"{root}/minute")
    index = pd.date_range(f"{day} 14:30", periods=periods, freq="min", tz="UTC", name="ts")
    closes = [start_close + i for i in range(periods)]
    df = pd.DataFrame(
        {
            SYMBOL: symbol,
            OPEN: closes,
            HIGH: closes,
            LOW: closes,
            CLOSE: closes,
            VOLUME: 100,
            ADJ_CLOSE: closes,
            TRANSACTIONS: 1,
        },
        index=index,
    )
    store.write(symbol, df)


@pytest.fixture
def local_root(tmp_path):
    _write_minute_bars(tmp_path, "AAPL")
    _write_minute_bars(tmp_path, "MSFT", start_close=200.0)

    ref_store = LocalReferenceStore(tmp_path)
    ref_store.write("splits", pd.DataFrame([_split_row("AAPL", "2020-08-31")]))
    ref_store.write(
        "short_volume",
        pd.DataFrame([_short_row("AAPL", "2024-01-02", 100.0), _short_row("MSFT", "2024-01-02", 200.0)]),
    )
    _write_sec_reference(tmp_path, [(320193, "AAPL", "Apple Inc.")])
    return tmp_path


def _write_sec_reference(root, rows) -> None:
    """SecStore is R2-only (no local variant), so a local sec/reference/
    company_tickers.parquet for tests is written directly here, matching
    the real schema -- just enough for sec_companies()/sec_cik() to work
    against a local_root=, no boto3/R2 involved."""
    import os

    import pyarrow as pa
    import pyarrow.parquet as pq

    os.makedirs(f"{root}/sec/reference", exist_ok=True)
    df = pd.DataFrame(rows, columns=["cik", "ticker", "entity_name"])
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), f"{root}/sec/reference/company_tickers.parquet")


class _CountingConnection:
    """Wraps a REAL duckdb connection to count .execute() calls -- duckdb's
    own connection object is a compiled extension type and rejects
    monkeypatching its methods directly ('attribute is read-only',
    confirmed live), so this wraps it instead and gets installed as the
    Symbol's OWN `_con` (a plain, freely-settable Python attribute)."""

    def __init__(self, real_con):
        self._real_con = real_con
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append(sql)
        return self._real_con.execute(sql, params) if params is not None else self._real_con.execute(sql)

    def __getattr__(self, name):
        return getattr(self._real_con, name)


def _count_queries(sym: Symbol) -> "_CountingConnection":
    proxy = _CountingConnection(sym._connect())
    sym._con = proxy
    return proxy


def test_symbol_requires_at_least_one_ticker():
    with pytest.raises(ValueError, match="at least one ticker"):
        Symbol()


def test_ticker_is_uppercased():
    sym = Symbol("aapl", local_root="/nonexistent")
    assert sym.tickers == ["AAPL"]


def test_single_ticker_minute_bars_returns_that_symbols_rows(local_root):
    sym = Symbol("AAPL", local_root=str(local_root))

    df = sym.minute_bars()

    assert len(df) == 5
    assert set(df["symbol"]) == {"AAPL"}


def test_single_ticker_splits_returns_only_that_tickers_rows(local_root):
    sym = Symbol("AAPL", local_root=str(local_root))

    df = sym.splits()

    assert len(df) == 1
    assert df["ticker"].iloc[0] == "AAPL"


def test_single_ticker_with_no_data_returns_empty_frame_not_a_raise(local_root):
    sym = Symbol("ZZZZ", local_root=str(local_root))

    df = sym.splits()

    assert df.empty
    assert "execution_date" in df.columns


def test_ipo_is_singular_and_tolerates_no_data(local_root):
    sym = Symbol("AAPL", local_root=str(local_root))

    df = sym.ipo()

    assert df.empty  # never ingested any IPO rows in this fixture
    assert "ticker" in df.columns


def test_multi_ticker_scan_all_dataset_runs_exactly_one_query(local_root):
    sym = Symbol("AAPL", "MSFT", local_root=str(local_root))
    proxy = _count_queries(sym)

    df = sym.short_volume()

    assert len(proxy.calls) == 1
    assert set(df["ticker"]) == {"AAPL", "MSFT"}


def test_multi_ticker_non_scan_all_dataset_runs_one_query_per_ticker(local_root):
    sym = Symbol("AAPL", "MSFT", local_root=str(local_root))
    proxy = _count_queries(sym)

    df = sym.minute_bars()

    assert len(proxy.calls) == 2  # minute_bars(sym) has no "every symbol" scan mode -- one call per ticker
    assert set(df["symbol"]) == {"AAPL", "MSFT"}


def test_cache_hit_skips_the_second_query(local_root):
    from tam.cache import ManualCache

    cache = ManualCache()
    sym = Symbol("AAPL", local_root=str(local_root), cache=cache)
    proxy = _count_queries(sym)

    sym.splits()
    sym.splits()

    assert len(proxy.calls) == 1


def test_per_call_cache_override_wins_over_the_instance_default(local_root, monkeypatch):
    from tam.cache import ManualCache

    instance_cache = ManualCache()
    call_cache = ManualCache()
    sym = Symbol("AAPL", local_root=str(local_root), cache=instance_cache)

    sym.splits(cache=call_cache)

    assert len(instance_cache._store) == 0
    assert len(call_cache._store) == 1


def test_engine_polars_returns_a_polars_dataframe(local_root):
    pytest.importorskip("polars")
    import polars as pl

    sym = Symbol("AAPL", local_root=str(local_root))

    df = sym.splits(engine="polars")

    assert isinstance(df, pl.DataFrame)
    assert len(df) == 1


def test_query_runs_raw_sql_over_the_same_connection(local_root):
    sym = Symbol("AAPL", local_root=str(local_root))

    df = sym.query("SELECT * FROM daily_bars('AAPL') ORDER BY day")

    assert len(df) == 1  # 5 one-minute bars on the same day -> one daily bar


class _FakeSec:
    """Stands in for tam.research.data.sec.Sec -- verifies Symbol.financials()/
    .filings() delegate with the right arguments, without needing a real
    SEC-shaped Parquet fixture (Sec itself has no existing test coverage
    to build on/reuse yet)."""

    instances = []

    def __init__(self, con=None):
        self.con = con
        self.financials_calls = []
        self.filings_calls = []
        type(self).instances.append(self)

    def financials(self, tickers=None, statement=None, line_items=None, start=None, end=None, dedupe_periods=True):
        self.financials_calls.append((tickers, statement, line_items, start, end, dedupe_periods))
        return pd.DataFrame([{"cik": 320193, "fiscal_year": 2023, "line_item": "revenue", "value": 1.0}])

    def filings(self, ticker=None, forms=None, start=None, end=None):
        self.filings_calls.append((ticker, forms, start, end))
        return pd.DataFrame([{"cik": 320193, "form": "10-K"}])


@pytest.fixture
def fake_sec(monkeypatch):
    pytest.importorskip(
        "edgar"
    )  # tam.research.data.sec imports this eagerly (see its own docstring) -- needs the `sec` extra
    _FakeSec.instances = []
    monkeypatch.setattr("tam.research.data.sec.Sec", _FakeSec)
    return _FakeSec


def test_financials_delegates_to_sec_with_this_symbols_tickers(local_root, fake_sec):
    sym = Symbol("AAPL", "MSFT", local_root=str(local_root))

    df = sym.financials(statement="income_statement")

    assert len(df) == 1
    assert fake_sec.instances[0].financials_calls == [(["AAPL", "MSFT"], "income_statement", None, None, None, True)]


def test_filings_delegates_per_ticker_and_concatenates(local_root, fake_sec):
    sym = Symbol("AAPL", "MSFT", local_root=str(local_root))

    df = sym.filings(forms=["10-K"])

    assert len(df) == 2  # one fake row per ticker
    assert [call[0] for call in fake_sec.instances[0].filings_calls] == ["AAPL", "MSFT"]


def test_bare_int_is_rejected_with_a_clear_type_error(local_root):
    with pytest.raises(TypeError, match="ticker string or CIK"):
        Symbol(320193, local_root=str(local_root))


def test_cik_resolves_to_the_real_ticker_for_bar_and_reference_macros(local_root):
    sym = Symbol(CIK(320193), local_root=str(local_root))

    df = sym.splits()

    assert len(df) == 1
    assert df["ticker"].iloc[0] == "AAPL"


def test_cik_and_ticker_mix_freely_in_one_symbol(local_root):
    sym = Symbol("MSFT", CIK(320193), local_root=str(local_root))

    df = sym.short_volume()

    assert set(df["ticker"]) == {"AAPL", "MSFT"}


def test_cik_with_no_matching_company_raises_a_clear_error(local_root):
    sym = Symbol(CIK(999999999), local_root=str(local_root))

    with pytest.raises(RuntimeError, match="No ticker on record for CIK 999999999"):
        sym.splits()


def test_cik_repr_and_construction_from_a_string():
    assert repr(CIK(320193)) == "CIK(320193)"
    assert CIK("320193").value == 320193


def test_cik_passed_through_directly_to_sec_for_financials(local_root, fake_sec):
    sym = Symbol(CIK(320193), local_root=str(local_root))

    sym.financials()

    assert fake_sec.instances[0].financials_calls[0][0] == [320193]  # the raw CIK int, no resolution needed


def test_columns_selects_a_subset(local_root):
    sym = Symbol("AAPL", local_root=str(local_root))

    df = sym.splits(columns=["ticker", "execution_date"])

    assert list(df.columns) == ["ticker", "execution_date"]


def test_columns_rejects_an_unknown_name_with_a_clear_error(local_root):
    sym = Symbol("AAPL", local_root=str(local_root))

    with pytest.raises(ValueError, match="Unknown column"):
        sym.splits(columns=["not_a_real_column"])


def test_columns_also_works_for_multi_ticker_scan_all_queries(local_root):
    sym = Symbol("AAPL", "MSFT", local_root=str(local_root))

    df = sym.short_volume(columns=["ticker", "short_volume"])

    assert list(df.columns) == ["ticker", "short_volume"]
    assert set(df["ticker"]) == {"AAPL", "MSFT"}


def test_daily_bars_now_supports_start_end_like_the_other_bar_methods(local_root):
    sym = Symbol("AAPL", local_root=str(local_root))

    in_range = sym.daily_bars(start="2024-01-02", end="2024-01-02")
    out_of_range = sym.daily_bars(start="2024-06-01", end="2024-06-30")

    assert len(in_range) == 1
    assert out_of_range.empty


def test_engine_enum_is_equivalent_to_the_plain_string(local_root):
    pytest.importorskip("polars")
    import polars as pl

    from tam.engine import Engine

    sym = Symbol("AAPL", local_root=str(local_root))

    by_enum = sym.splits(engine=Engine.POLARS)
    by_string = sym.splits(engine="polars")

    assert isinstance(by_enum, pl.DataFrame)
    assert by_enum.equals(by_string)

import pandas as pd
import pytest

from tam.marketdata import reference_schema as schema
from tam.marketdata.reference_store import LocalReferenceStore


@pytest.fixture
def store(tmp_path):
    return LocalReferenceStore(tmp_path)


def test_read_missing_dataset_returns_empty_frame_with_right_columns(store):
    df = store.read("splits")
    assert df.empty
    assert list(df.columns) == schema.SPLIT_COLUMNS


def test_write_then_read_round_trips_append_only_dataset(store):
    df = pd.DataFrame(
        [
            {
                "id": "s1",
                "ticker": "AAPL",
                "execution_date": "2020-08-31",
                "split_from": 1,
                "split_to": 4,
                "adjustment_type": "forward_split",
                "historical_adjustment_factor": 0.25,
            }
        ]
    )
    store.write("splits", df)

    result = store.read("splits")

    assert len(result) == 1
    assert result["ticker"].iloc[0] == "AAPL"


def test_write_partitions_append_only_data_by_year(store, tmp_path):
    df = pd.DataFrame(
        [
            {
                "id": "s1",
                "ticker": "AAPL",
                "execution_date": "2020-08-31",
                "split_from": 1,
                "split_to": 4,
                "adjustment_type": "forward_split",
                "historical_adjustment_factor": 0.25,
            },
            {
                "id": "s2",
                "ticker": "TSLA",
                "execution_date": "2021-08-25",
                "split_from": 1,
                "split_to": 5,
                "adjustment_type": "forward_split",
                "historical_adjustment_factor": 0.2,
            },
        ]
    )
    store.write("splits", df)

    assert (tmp_path / "corporate_actions" / "splits" / "2020.parquet").exists()
    assert (tmp_path / "corporate_actions" / "splits" / "2021.parquet").exists()


def test_write_dedupes_on_the_natural_key_keeping_the_latest(store):
    """A re-fetched row for an already-seen id (e.g. the vendor corrected
    something) must replace the stored one, not duplicate it."""
    original = pd.DataFrame(
        [
            {
                "id": "s1",
                "ticker": "AAPL",
                "execution_date": "2020-08-31",
                "split_from": 1,
                "split_to": 4,
                "adjustment_type": "forward_split",
                "historical_adjustment_factor": 0.25,
            }
        ]
    )
    store.write("splits", original)

    corrected = pd.DataFrame(
        [
            {
                "id": "s1",
                "ticker": "AAPL",
                "execution_date": "2020-08-31",
                "split_from": 1,
                "split_to": 7,
                "adjustment_type": "forward_split",
                "historical_adjustment_factor": 0.142857,
            }
        ]
    )
    store.write("splits", corrected)

    result = store.read("splits")
    assert len(result) == 1
    assert result["split_to"].iloc[0] == 7


def test_write_appends_new_rows_alongside_existing_ones_in_the_same_year(store):
    first = pd.DataFrame(
        [
            {
                "id": "s1",
                "ticker": "AAPL",
                "execution_date": "2020-08-31",
                "split_from": 1,
                "split_to": 4,
                "adjustment_type": "forward_split",
                "historical_adjustment_factor": 0.25,
            }
        ]
    )
    store.write("splits", first)
    second = pd.DataFrame(
        [
            {
                "id": "s2",
                "ticker": "NVDA",
                "execution_date": "2020-06-10",
                "split_from": 1,
                "split_to": 4,
                "adjustment_type": "forward_split",
                "historical_adjustment_factor": 0.25,
            }
        ]
    )
    store.write("splits", second)

    result = store.read("splits")
    assert len(result) == 2
    assert set(result["id"]) == {"s1", "s2"}


def test_snapshot_dataset_write_overwrites_wholesale_not_upserts(store):
    store.write("ipos", pd.DataFrame([{"ticker": "ABC", "issuer_name": "ABC Corp", "ipo_status": "pending"}]))
    store.write("ipos", pd.DataFrame([{"ticker": "ABC", "issuer_name": "ABC Corp", "ipo_status": "new"}]))

    result = store.read("ipos")

    assert len(result) == 1
    assert result["ipo_status"].iloc[0] == "new"


def test_snapshot_dataset_lives_at_a_single_all_parquet_file(store, tmp_path):
    store.write(
        "float",
        pd.DataFrame(
            [{"ticker": "AAPL", "effective_date": "2025-01-01", "free_float": 100, "free_float_percent": 99.0}]
        ),
    )

    assert (tmp_path / "positioning" / "float" / "all.parquet").exists()


def test_write_empty_append_only_frame_is_a_no_op(store):
    store.write("splits", schema.empty_frame(schema.SPLIT_COLUMNS))
    assert store.read("splits").empty


def _short_volume_row(ticker: str, date: str, short_volume: float = 100.0) -> dict:
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


def test_write_partitions_per_ticker_dataset_by_ticker_and_year(store, tmp_path):
    df = pd.DataFrame(
        [
            _short_volume_row("AAPL", "2025-01-02"),
            _short_volume_row("TSLA", "2025-01-02"),
            _short_volume_row("AAPL", "2026-01-02"),
        ]
    )
    store.write("short_volume", df)

    assert (tmp_path / "positioning" / "short_volume" / "AAPL" / "2025.parquet").exists()
    assert (tmp_path / "positioning" / "short_volume" / "AAPL" / "2026.parquet").exists()
    assert (tmp_path / "positioning" / "short_volume" / "TSLA" / "2025.parquet").exists()
    # No global year file for a per-ticker dataset -- unlike splits/dividends.
    assert not (tmp_path / "positioning" / "short_volume" / "2025.parquet").exists()


def test_read_per_ticker_dataset_with_no_ticker_returns_every_ticker(store):
    df = pd.DataFrame([_short_volume_row("AAPL", "2025-01-02"), _short_volume_row("TSLA", "2025-01-02")])
    store.write("short_volume", df)

    result = store.read("short_volume")

    assert len(result) == 2
    assert set(result["ticker"]) == {"AAPL", "TSLA"}


def test_read_per_ticker_dataset_scoped_to_one_ticker_ignores_others(store):
    df = pd.DataFrame([_short_volume_row("AAPL", "2025-01-02"), _short_volume_row("TSLA", "2025-01-02")])
    store.write("short_volume", df)

    result = store.read("short_volume", ticker="aapl")

    assert len(result) == 1
    assert result["ticker"].iloc[0] == "AAPL"


def test_write_dedupes_per_ticker_dataset_on_ticker_and_date(store):
    store.write("short_volume", pd.DataFrame([_short_volume_row("AAPL", "2025-01-02", short_volume=100.0)]))
    store.write("short_volume", pd.DataFrame([_short_volume_row("AAPL", "2025-01-02", short_volume=999.0)]))

    result = store.read("short_volume", ticker="AAPL")

    assert len(result) == 1
    assert result["short_volume"].iloc[0] == 999.0


def test_manifest_round_trips_per_group(store):
    assert store.read_manifest_bytes("corporate_actions") is None

    store.write_manifest_bytes("corporate_actions", b'{"splits_cursor": "2021-08-25"}')

    assert store.read_manifest_bytes("corporate_actions") == b'{"splits_cursor": "2021-08-25"}'
    assert store.read_manifest_bytes("positioning") is None  # a different group's manifest is untouched

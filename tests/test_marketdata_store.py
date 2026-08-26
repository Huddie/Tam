import io
from typing import Dict

import pandas as pd
import pytest

from tam.marketdata.credentials import R2Credentials
from tam.marketdata.schema import MINUTE_BAR_COLUMNS, TS
from tam.marketdata.store import LocalMinuteBarStore, R2MinuteBarStore
from tam.registry import Registry
from tam.marketdata.store import MinuteBarStore


def _bars(timestamps, closes, symbol="AAPL"):
    index = pd.DatetimeIndex(timestamps, tz="UTC", name=TS)
    return pd.DataFrame(
        {
            "symbol": [symbol] * len(closes),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [100] * len(closes),
            "adj_close": closes,
            "transactions": [10] * len(closes),
        },
        index=index,
    )[MINUTE_BAR_COLUMNS]


def test_exists_is_false_before_any_write(tmp_path):
    store = LocalMinuteBarStore(tmp_path)
    assert store.exists("AAPL") is False
    assert store.read("AAPL").empty


def test_write_then_read_roundtrip(tmp_path):
    store = LocalMinuteBarStore(tmp_path)
    df = _bars(["2024-01-02 14:30", "2024-01-02 14:31"], [100.0, 101.0])

    store.write("AAPL", df)

    assert store.exists("aapl") is True  # symbol lookup is case-insensitive
    result = store.read("AAPL")
    assert list(result["close"]) == [100.0, 101.0]
    assert str(result.index.tz) == "UTC"


def test_write_splits_across_year_partitions_transparently(tmp_path):
    store = LocalMinuteBarStore(tmp_path)
    df = _bars(["2020-06-01 14:30", "2021-06-01 14:30", "2022-06-01 14:30"], [1.0, 2.0, 3.0])

    store.write("AAPL", df)

    years = store._partition_years("AAPL")
    assert years == [2020, 2021, 2022]
    result = store.read("AAPL")
    assert list(result["close"]) == [1.0, 2.0, 3.0]


def test_write_is_upsert_overwriting_overlapping_timestamps_and_adding_new_ones(tmp_path):
    store = LocalMinuteBarStore(tmp_path)
    store.write("AAPL", _bars(["2024-01-02 14:30", "2024-01-02 14:31"], [100.0, 101.0]))

    store.write("AAPL", _bars(["2024-01-02 14:31", "2024-01-02 14:32"], [999.0, 102.0]))

    result = store.read("AAPL")
    assert list(result["close"]) == [100.0, 999.0, 102.0]  # overlap keeps the NEW value, new row appended
    assert result.index.is_monotonic_increasing
    assert not result.index.has_duplicates


def test_write_of_empty_frame_is_a_noop(tmp_path):
    store = LocalMinuteBarStore(tmp_path)
    from tam.marketdata.schema import empty_minute_bar_frame

    store.write("AAPL", empty_minute_bar_frame())

    assert store.exists("AAPL") is False


def test_different_symbols_are_stored_independently(tmp_path):
    store = LocalMinuteBarStore(tmp_path)
    store.write("AAPL", _bars(["2024-01-02 14:30"], [100.0], symbol="AAPL"))
    store.write("MSFT", _bars(["2024-01-02 14:30"], [200.0], symbol="MSFT"))

    assert list(store.read("AAPL")["close"]) == [100.0]
    assert list(store.read("MSFT")["close"]) == [200.0]


def test_symbol_column_is_preserved_through_a_write_read_roundtrip(tmp_path):
    store = LocalMinuteBarStore(tmp_path)
    store.write("AAPL", _bars(["2024-01-02 14:30"], [100.0], symbol="AAPL"))

    result = store.read("AAPL")
    assert list(result["symbol"]) == ["AAPL"]


def test_local_and_r2_stores_are_registered_under_expected_names():
    assert "local_parquet" in Registry.names(MinuteBarStore)
    assert "r2_parquet" in Registry.names(MinuteBarStore)


def test_r2_store_construction_raises_actionable_error_without_credentials(monkeypatch):
    for var in ["R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"]:
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(RuntimeError, match="Missing R2 credential"):
        Registry.create(MinuteBarStore, "r2_parquet")


# -- R2MinuteBarStore (boto3-backed) -----------------------------------------
#
# Uses a hand-rolled fake S3 client (this project's usual fakes-over-mocking-
# library convention -- see e.g. tests/test_data.py's FakeProvider) rather
# than moto/real R2 credentials. The fake only implements put_object/
# get_object/head_object/get_paginator -- exactly the calls R2MinuteBarStore
# makes; it deliberately has no create_multipart_upload/upload_part/
# complete_multipart_upload, so if a future change accidentally reintroduces
# a multipart code path, these tests fail with AttributeError instead of
# silently passing.

_FAKE_CREDS = R2Credentials(account_id="acct", access_key_id="key", secret_access_key="secret", bucket="test-bucket")


class _FakePaginator:
    def __init__(self, objects: Dict[str, bytes]):
        self._objects = objects

    def paginate(self, Bucket, Prefix):
        contents = [{"Key": key} for key in self._objects if key.startswith(Prefix)]
        yield {"Contents": contents}


class _FakeS3Client:
    def __init__(self, fail_times: int = 0):
        self.objects: Dict[str, bytes] = {}
        self.put_calls = 0
        self._fail_times = fail_times  # simulate this many transient failures before succeeding

    def _maybe_fail(self) -> None:
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("simulated transient R2 error")

    def put_object(self, Bucket, Key, Body):
        self._maybe_fail()
        self.put_calls += 1
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        from botocore.exceptions import ClientError

        self._maybe_fail()
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):
        from botocore.exceptions import ClientError

        self._maybe_fail()
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "not found"}}, "HeadObject")
        return {}

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return _FakePaginator(self.objects)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Retry backoff (_with_retries) really sleeps between attempts --
    harmless in production, but would make every retry test take seconds
    for no reason. Only applies within this section's tests."""
    import tam.marketdata.store as store_module

    monkeypatch.setattr(store_module.time, "sleep", lambda _seconds: None)


def test_r2_store_write_then_read_roundtrip():
    client = _FakeS3Client()
    store = R2MinuteBarStore(credentials=_FAKE_CREDS, client=client)
    df = _bars(["2024-01-02 14:30", "2024-01-02 14:31"], [100.0, 101.0])

    store.write("AAPL", df)

    assert store.exists("aapl")  # case-insensitive, matching LocalMinuteBarStore
    result = store.read("AAPL")
    assert list(result["close"]) == [100.0, 101.0]
    assert client.put_calls == 1  # one plain put_object for the whole year-partition, not one per row


def test_r2_store_write_is_upsert_overwriting_overlapping_timestamps():
    client = _FakeS3Client()
    store = R2MinuteBarStore(credentials=_FAKE_CREDS, client=client)
    store.write("AAPL", _bars(["2024-01-02 14:30", "2024-01-02 14:31"], [100.0, 101.0]))

    store.write("AAPL", _bars(["2024-01-02 14:31", "2024-01-02 14:32"], [999.0, 102.0]))

    result = store.read("AAPL")
    assert list(result["close"]) == [100.0, 999.0, 102.0]
    assert result.index.is_monotonic_increasing
    assert not result.index.has_duplicates


def test_r2_store_write_splits_across_year_partitions():
    client = _FakeS3Client()
    store = R2MinuteBarStore(credentials=_FAKE_CREDS, client=client)
    df = _bars(["2020-06-01 14:30", "2021-06-01 14:30", "2022-06-01 14:30"], [1.0, 2.0, 3.0])

    store.write("AAPL", df)

    assert sorted(client.objects.keys()) == ["minute/AAPL/2020.parquet", "minute/AAPL/2021.parquet", "minute/AAPL/2022.parquet"]
    assert list(store.read("AAPL")["close"]) == [1.0, 2.0, 3.0]


def test_r2_store_manifest_bytes_roundtrip():
    client = _FakeS3Client()
    store = R2MinuteBarStore(credentials=_FAKE_CREDS, client=client)

    assert store.read_manifest_bytes() is None
    store.write_manifest_bytes(b'{"2024-01-02": "abc"}')
    assert store.read_manifest_bytes() == b'{"2024-01-02": "abc"}'


def test_r2_store_retries_transient_errors_and_succeeds():
    client = _FakeS3Client(fail_times=2)  # fails twice, succeeds on the 3rd attempt
    store = R2MinuteBarStore(credentials=_FAKE_CREDS, client=client)

    store.write("AAPL", _bars(["2024-01-02 14:30"], [100.0]))  # must not raise

    assert store.exists("AAPL")


def test_r2_store_gives_up_and_raises_after_exhausting_retries():
    client = _FakeS3Client(fail_times=10)  # never succeeds within the retry budget
    store = R2MinuteBarStore(credentials=_FAKE_CREDS, client=client)

    with pytest.raises(RuntimeError, match="simulated transient R2 error"):
        store.write("AAPL", _bars(["2024-01-02 14:30"], [100.0]))


def test_r2_store_builds_a_real_client_with_region_auto_not_a_local_default(monkeypatch):
    # R2 rejects a real AWS region name outright (InvalidRegionName) --
    # confirmed live in production. Without an explicit region_name="auto",
    # boto3 falls back to whatever region is configured locally (e.g. a
    # real AWS region like "us-west-2" from ~/.aws/config), which is
    # exactly what caused a real failure before this test existed.
    # session.client(...) builds the client object with no network I/O, so
    # this runs safely with fake credentials, no real R2 access needed.
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    store = R2MinuteBarStore(credentials=_FAKE_CREDS)
    assert store._client.meta.region_name == "auto"

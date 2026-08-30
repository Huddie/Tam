import pytest

from tam.marketdata import connection


@pytest.fixture(autouse=True)
def _reset_shared_connection(monkeypatch):
    # _shared_connection is real module-level mutable state -- reset it
    # around every test in this file so default_connection() tests don't
    # leak a cached object into (or out of) each other.
    monkeypatch.setattr(connection, "_shared_connection", None)


def test_resolve_connection_with_local_root_calls_open_duckdb(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "tam.marketdata.duckdb_query.open_duckdb", lambda **kwargs: calls.append(kwargs) or "a-connection"
    )

    result = connection.resolve_connection(local_root=str(tmp_path))

    assert result == "a-connection"
    assert calls == [{"local_root": str(tmp_path)}]


def test_resolve_connection_with_a_raw_kwarg_also_wins_outright(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "tam.marketdata.duckdb_query.open_duckdb", lambda **kwargs: calls.append(kwargs) or "a-connection"
    )

    connection.resolve_connection(bucket="tam-data")

    assert calls == [{"local_root": None, "bucket": "tam-data"}]


def test_resolve_connection_falls_back_to_the_token_path(monkeypatch):
    monkeypatch.setattr("tam.marketdata.explorer_client.resolve_token", lambda token, required: "resolved-token")
    calls = []
    monkeypatch.setattr(
        "tam.marketdata.explorer_client.connect",
        lambda **kwargs: calls.append(kwargs) or "a-sql-connection",
    )

    result = connection.resolve_connection()

    assert result == "a-sql-connection"
    assert calls == [{"token": "resolved-token", "api_url": None, "ttl_seconds": None}]


def test_resolve_connection_raises_a_clear_error_with_no_token_and_no_override(monkeypatch):
    monkeypatch.setattr("tam.marketdata.explorer_client.resolve_token", lambda token, required: None)

    with pytest.raises(RuntimeError, match="TAM_PAT"):
        connection.resolve_connection()


def test_default_connection_builds_once_and_reuses_the_same_object(monkeypatch):
    built = []

    def fake_resolve(**kwargs):
        built.append(object())
        return built[-1]

    monkeypatch.setattr(connection, "resolve_connection", fake_resolve)

    first = connection.default_connection()
    second = connection.default_connection()

    assert first is second
    assert len(built) == 1


def test_is_missing_glob_error_matches_duckdbs_own_wording():
    exc = Exception('IO Error: No files found that match the pattern "s3://bucket/sec/reference/*.parquet"')

    assert connection.is_missing_glob_error(exc)
    assert connection.is_missing_glob_error(exc, "reference")
    assert not connection.is_missing_glob_error(exc, "financials")


def test_is_missing_glob_error_is_false_for_an_unrelated_exception():
    assert not connection.is_missing_glob_error(Exception("connection refused"))

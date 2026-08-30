"""tam/marketdata/reference_provider.py -- fakes over mocking libraries
(same convention as tests/test_marketdata_ingest.py's own local fakes):
a tiny _FakeSession stands in for requests.Session (splits/dividends,
called directly via requests), and a tiny _FakeRestClient stands in for
massive.RESTClient (ipos/short_volume/short_interest/float, called
through the SDK) -- neither test ever needs a real MASSIVE_API_KEY or
network access.
"""

import pytest

from tam.marketdata.reference_provider import MassiveReferenceProvider, _resolve_api_key


def test_resolve_api_key_prefers_explicit_over_env(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "from-env")
    assert _resolve_api_key("from-arg") == "from-arg"


def test_resolve_api_key_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "from-env")
    assert _resolve_api_key(None) == "from-env"


def test_resolve_api_key_raises_a_clear_error_when_missing(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="MASSIVE_API_KEY"):
        _resolve_api_key(None)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    """Two pages, second has no next_url -- exercises the pagination loop
    exactly once."""

    def __init__(self, pages):
        self._pages = pages
        self.requests = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.requests.append((url, headers, params))
        return _FakeResponse(self._pages[len(self.requests) - 1])


def _fake_row(**fields):
    """Stands in for a massive SDK model row -- MassiveReferenceProvider
    converts these via dataclasses.asdict(), so a real dataclass (not just
    an object with matching attributes) is needed here too."""
    import dataclasses

    cls = dataclasses.make_dataclass("_FakeRow", [(name, type(value)) for name, value in fields.items()])
    return cls(**fields)


class _FakeVx:
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def list_ipos(self, **kwargs):
        self.calls.append(kwargs)
        return self._rows


class _FakeRestClient:
    def __init__(self, short_volume_rows=(), short_interest_rows=(), float_rows=(), ipo_rows=()):
        self.vx = _FakeVx(ipo_rows)
        self._short_volume_rows = short_volume_rows
        self._short_interest_rows = short_interest_rows
        self._float_rows = float_rows
        self.calls = []

    def list_short_volume(self, **kwargs):
        self.calls.append(("list_short_volume", kwargs))
        return self._short_volume_rows

    def list_short_interest(self, **kwargs):
        self.calls.append(("list_short_interest", kwargs))
        return self._short_interest_rows

    def list_stocks_floats(self, **kwargs):
        self.calls.append(("list_stocks_floats", kwargs))
        return self._float_rows


def test_fetch_splits_paginates_via_next_url_and_sets_bearer_auth():
    pages = [
        {"results": [{"ticker": "AAPL", "execution_date": "2020-01-01"}], "next_url": "http://x/page2"},
        {"results": [{"ticker": "MSFT", "execution_date": "2020-02-01"}]},
    ]
    session = _FakeSession(pages)
    provider = MassiveReferenceProvider(api_key="fake-key", session=session)

    df = provider.fetch_splits()

    assert len(df) == 2
    assert list(df["ticker"]) == ["AAPL", "MSFT"]
    assert len(session.requests) == 2
    assert session.requests[0][1] == {"Authorization": "Bearer fake-key"}
    assert session.requests[1][0] == "http://x/page2"
    assert session.requests[1][2] is None  # next_url already carries its own query string


def test_fetch_splits_passes_since_as_execution_date_gte():
    """>=, not >: a future-scheduled split can get revised before it
    executes without its execution_date changing -- see fetch_splits()'s
    own docstring for why the cursor boundary is inclusive."""
    session = _FakeSession([{"results": []}])
    provider = MassiveReferenceProvider(api_key="fake-key", session=session)

    provider.fetch_splits(since="2023-05-01")

    assert session.requests[0][2]["execution_date.gte"] == "2023-05-01"


def test_fetch_dividends_passes_since_as_ex_dividend_date_gte():
    session = _FakeSession([{"results": []}])
    provider = MassiveReferenceProvider(api_key="fake-key", session=session)

    provider.fetch_dividends(since="2023-05-01")

    assert session.requests[0][2]["ex_dividend_date.gte"] == "2023-05-01"


def test_fetch_splits_returns_empty_frame_with_right_columns_when_no_results():
    from tam.marketdata import reference_schema as schema

    session = _FakeSession([{"results": []}])
    provider = MassiveReferenceProvider(api_key="fake-key", session=session)

    df = provider.fetch_splits()

    assert df.empty
    assert list(df.columns) == schema.SPLIT_COLUMNS


def test_fetch_ipos_uses_vx_client_and_converts_dataclass_rows():
    rows = [_fake_row(ticker="ABC", issuer_name="ABC Corp")]
    client = _FakeRestClient(ipo_rows=rows)
    provider = MassiveReferenceProvider(api_key="fake-key", client=client)

    df = provider.fetch_ipos()

    assert list(df["ticker"]) == ["ABC"]
    assert client.vx.calls[0] == {"order": "desc", "sort": "listing_date", "limit": 1000}


def test_fetch_short_volume_passes_since_as_date_gte():
    client = _FakeRestClient(short_volume_rows=[_fake_row(ticker="AAPL", date="2024-01-01")])
    provider = MassiveReferenceProvider(api_key="fake-key", client=client)

    provider.fetch_short_volume(since="2024-01-01")

    call_name, kwargs = client.calls[0]
    assert call_name == "list_short_volume"
    assert kwargs["date_gte"] == "2024-01-01"


def test_fetch_short_interest_passes_since_as_settlement_date_gte():
    client = _FakeRestClient(short_interest_rows=[_fake_row(ticker="AAPL", settlement_date="2024-01-01")])
    provider = MassiveReferenceProvider(api_key="fake-key", client=client)

    provider.fetch_short_interest(since="2024-01-01")

    call_name, kwargs = client.calls[0]
    assert call_name == "list_short_interest"
    assert kwargs["settlement_date_gte"] == "2024-01-01"


def test_fetch_float_has_no_since_parameter_at_all():
    """Confirms fetch_float()'s signature genuinely has no since= -- the
    vendor's own float endpoint has no date-range filter at all (see
    reference_provider.py's own docstring)."""
    import inspect

    assert "since" not in inspect.signature(MassiveReferenceProvider.fetch_float).parameters


def test_fetch_float_uses_rest_client():
    client = _FakeRestClient(float_rows=[_fake_row(ticker="AAPL", free_float=100)])
    provider = MassiveReferenceProvider(api_key="fake-key", client=client)

    df = provider.fetch_float()

    assert list(df["ticker"]) == ["AAPL"]
    assert client.calls[0][0] == "list_stocks_floats"


class _FlakyThenOkSession:
    """Fails with a transient error on its first N calls, then succeeds --
    exercises _paginate_raw()'s _with_retries() wrapping without needing a
    real network failure."""

    def __init__(self, fail_times: int):
        self._fail_times = fail_times
        self.attempts = 0

    def get(self, url, headers=None, params=None, timeout=None):
        self.attempts += 1
        if self.attempts <= self._fail_times:
            raise ConnectionError("simulated transient network failure")
        return _FakeResponse({"results": [{"ticker": "AAPL", "execution_date": "2024-01-01"}]})


def test_fetch_splits_retries_transient_failures_and_eventually_succeeds(monkeypatch):
    # Avoid real sleeping through _with_retries()'s exponential backoff.
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    session = _FlakyThenOkSession(fail_times=2)
    provider = MassiveReferenceProvider(api_key="fake-key", session=session)

    df = provider.fetch_splits()

    assert len(df) == 1
    assert session.attempts == 3


def test_fetch_splits_gives_up_after_five_attempts(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    session = _FlakyThenOkSession(fail_times=5)
    provider = MassiveReferenceProvider(api_key="fake-key", session=session)

    with pytest.raises(ConnectionError):
        provider.fetch_splits()


def test_fetch_splits_reports_progress_per_page_when_log_given():
    pages = [
        {"results": [{"ticker": "AAPL"}], "next_url": "http://x/page2"},
        {"results": [{"ticker": "MSFT"}]},
    ]
    session = _FakeSession(pages)
    provider = MassiveReferenceProvider(api_key="fake-key", session=session)
    messages = []

    provider.fetch_splits(log=messages.append)

    assert messages == ["  page 1: 1 row(s) so far", "  page 2: 2 row(s) so far"]

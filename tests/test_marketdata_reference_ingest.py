"""tam/marketdata/reference_ingest.py -- a tiny fake provider stands in
for MassiveReferenceProvider (matching this project's fakes-over-mocking
convention), so no real API key or network access is ever needed here.
"""

import pandas as pd
import pytest

from tam.marketdata import reference_schema as schema
from tam.marketdata.reference_ingest import Manifest, ingest_reference_data
from tam.marketdata.reference_store import LocalReferenceStore


class _FakeProvider:
    def __init__(self):
        self.calls = []
        self.splits_rows = [
            {
                "id": "s1",
                "ticker": "AAPL",
                "execution_date": "2020-01-15",
                "split_from": 1,
                "split_to": 4,
                "adjustment_type": "forward_split",
                "historical_adjustment_factor": 0.25,
            }
        ]
        self.ipo_status = "new"

    def fetch_splits(self, since=None, *, log=None):
        self.calls.append(("splits", since))
        rows = [] if (since and since >= "2020-01-15") else self.splits_rows
        return pd.DataFrame(rows, columns=schema.SPLIT_COLUMNS) if rows else schema.empty_frame(schema.SPLIT_COLUMNS)

    def fetch_dividends(self, since=None, *, log=None):
        self.calls.append(("dividends", since))
        return schema.empty_frame(schema.DIVIDEND_COLUMNS)

    def fetch_short_volume(self, since=None):
        self.calls.append(("short_volume", since))
        return schema.empty_frame(schema.SHORT_VOLUME_COLUMNS)

    def fetch_short_interest(self, since=None):
        self.calls.append(("short_interest", since))
        return schema.empty_frame(schema.SHORT_INTEREST_COLUMNS)

    def fetch_ipos(self):
        self.calls.append(("ipos", None))
        return pd.DataFrame([{"ticker": "ABC", "issuer_name": "ABC Corp", "ipo_status": self.ipo_status}])

    def fetch_float(self):
        self.calls.append(("float", None))
        return pd.DataFrame(
            [{"ticker": "AAPL", "effective_date": "2025-01-01", "free_float": 100, "free_float_percent": 99.0}]
        )


@pytest.fixture
def store(tmp_path):
    return LocalReferenceStore(tmp_path)


def test_first_run_fetches_everything_with_no_cursor(store):
    provider = _FakeProvider()

    results = ingest_reference_data(provider, store)

    assert ("splits", None) in provider.calls
    splits_result = next(r for r in results if r.dataset == "splits")
    assert splits_result.rows_fetched == 1


def test_first_run_writes_fetched_rows_to_the_store(store):
    ingest_reference_data(_FakeProvider(), store)

    assert len(store.read("splits")) == 1
    assert len(store.read("ipos")) == 1
    assert len(store.read("float")) == 1


def test_first_run_advances_the_cursor_to_the_newest_date_seen(store):
    ingest_reference_data(_FakeProvider(), store)

    manifest = Manifest(store, "corporate_actions")
    assert manifest.cursor_for("splits") == "2020-01-15"


def test_second_run_passes_the_stored_cursor_to_the_next_fetch(store):
    ingest_reference_data(_FakeProvider(), store)

    provider2 = _FakeProvider()
    ingest_reference_data(provider2, store)

    assert ("splits", "2020-01-15") in provider2.calls


def test_second_run_with_nothing_new_does_not_advance_or_rewrite_the_cursor(store):
    ingest_reference_data(_FakeProvider(), store)
    manifest_before = Manifest(store, "corporate_actions").cursor_for("splits")

    ingest_reference_data(_FakeProvider(), store)

    manifest_after = Manifest(store, "corporate_actions").cursor_for("splits")
    assert manifest_after == manifest_before == "2020-01-15"


def test_snapshot_datasets_are_always_refetched_regardless_of_prior_runs(store):
    provider1 = _FakeProvider()
    provider1.ipo_status = "pending"
    ingest_reference_data(provider1, store)
    assert store.read("ipos")["ipo_status"].iloc[0] == "pending"

    provider2 = _FakeProvider()
    provider2.ipo_status = "new"
    ingest_reference_data(provider2, store)

    assert ("ipos", None) in provider2.calls
    assert store.read("ipos")["ipo_status"].iloc[0] == "new"


def test_manifest_cursor_for_unknown_dataset_is_none(store):
    manifest = Manifest(store, "corporate_actions")
    assert manifest.cursor_for("splits") is None


def test_manifest_record_does_not_persist_until_flush(store):
    manifest = Manifest(store, "corporate_actions")
    manifest.record("splits", "2024-01-01")

    reloaded = Manifest(store, "corporate_actions")
    assert reloaded.cursor_for("splits") is None

    manifest.flush()
    reloaded_again = Manifest(store, "corporate_actions")
    assert reloaded_again.cursor_for("splits") == "2024-01-01"


def test_positioning_and_corporate_actions_manifests_are_independent(store):
    ca_manifest = Manifest(store, "corporate_actions")
    ca_manifest.record("splits", "2024-01-01")
    ca_manifest.flush()

    positioning_manifest = Manifest(store, "positioning")
    assert positioning_manifest.cursor_for("splits") is None

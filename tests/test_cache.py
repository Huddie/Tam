import pytest

from tam.cache import LRUCache, ManualCache, TTLCache


def test_manual_cache_round_trips_and_never_expires():
    cache = ManualCache()
    assert cache.get("k") is None

    cache.set("k", "v")

    assert cache.get("k") == "v"


def test_manual_cache_clear_empties_everything():
    cache = ManualCache()
    cache.set("a", 1)
    cache.set("b", 2)

    cache.clear()

    assert cache.get("a") is None
    assert cache.get("b") is None


def test_ttl_cache_returns_the_value_before_expiry(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    cache = TTLCache(ttl_seconds=10)
    cache.set("k", "v")

    now[0] += 5

    assert cache.get("k") == "v"


def test_ttl_cache_expires_after_ttl_elapses(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    cache = TTLCache(ttl_seconds=10)
    cache.set("k", "v")

    now[0] += 11

    assert cache.get("k") is None


def test_lru_cache_evicts_the_least_recently_used_entry_when_over_capacity():
    cache = LRUCache(max_entries=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)  # over capacity -- "a" (never re-read) should be evicted

    assert cache.get("a") is None
    assert cache.get("b") == 2
    assert cache.get("c") == 3


def test_lru_cache_get_refreshes_recency():
    cache = LRUCache(max_entries=2)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")  # "a" is now more recently used than "b"
    cache.set("c", 3)  # "b", not "a", should be evicted

    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.get("c") == 3


@pytest.mark.parametrize("cache_cls,args", [(ManualCache, ()), (TTLCache, (60,)), (LRUCache, (10,))])
def test_every_cache_treats_distinct_keys_independently(cache_cls, args):
    cache = cache_cls(*args)
    cache.set(("sql a", (), "pandas"), "result a")
    cache.set(("sql b", (), "pandas"), "result b")

    assert cache.get(("sql a", (), "pandas")) == "result a"
    assert cache.get(("sql b", (), "pandas")) == "result b"

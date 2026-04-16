import time

from anti_hacker.council.cache import DebateCache


def test_same_request_hits_cache() -> None:
    cache = DebateCache(ttl_seconds=60)
    key = cache.make_key(task="t", files={"a.py": "x"}, mode="review")
    cache.put(key, {"debate_id": "d1"})
    assert cache.get(key) == {"debate_id": "d1"}


def test_different_content_misses() -> None:
    cache = DebateCache(ttl_seconds=60)
    k1 = cache.make_key(task="t", files={"a.py": "x"}, mode="review")
    k2 = cache.make_key(task="t", files={"a.py": "y"}, mode="review")
    assert k1 != k2


def test_expired_entries_dropped() -> None:
    cache = DebateCache(ttl_seconds=0)
    key = cache.make_key(task="t", files={"a.py": "x"}, mode="review")
    cache.put(key, {"debate_id": "d1"})
    time.sleep(0.01)
    assert cache.get(key) is None

"""The fact-check evidence cache is safe to use from several threads.

`POST /v1/fact-check` is a sync route, so FastAPI runs it in worker threads,
and `/v1/process-audio` runs its enrichment in a worker thread too; all of
them share one engine and one cache.
"""

from __future__ import annotations

import sys
import threading

from app.insights.core.factcheck.factcheck_engine import _TTLCache

_WAIT = 0.5


class _PausingStore(dict):
    """Pauses the first lookup made while the cache scans for its oldest entry."""

    def __init__(self) -> None:
        super().__init__()
        self.in_scan = threading.Event()
        self.other_done = threading.Event()
        self.armed = False

    def __getitem__(self, key):
        if self.armed:
            self.armed = False
            self.in_scan.set()
            self.other_done.wait(_WAIT)
        return super().__getitem__(key)


def test_a_write_from_another_thread_waits_for_an_eviction_in_progress():
    cache = _TTLCache(ttl_sec=3600.0, max_entries=4)
    store = _PausingStore()
    cache._store = store
    for i in range(4):
        cache.set(("t", f"k{i}"), None)
    errors: list[BaseException] = []

    def evicting_write() -> None:
        try:
            cache.set(("t", "k4"), None)  # full: scans for the oldest entry
        except BaseException as exc:  # noqa: BLE001 - the test reports any failure
            errors.append(exc)

    def other_write() -> None:
        store.in_scan.wait(_WAIT)
        try:
            cache.set(("t", "k5"), None)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            store.other_done.set()

    store.armed = True
    a = threading.Thread(target=evicting_write)
    b = threading.Thread(target=other_write)
    a.start()
    b.start()
    a.join(5)
    b.join(5)

    assert errors == []
    assert len(cache._store) <= 4
    assert cache.get(("t", "k5")) is None and ("t", "k5") in cache._store


def test_many_threads_keep_the_cache_consistent_and_bounded():
    cache = _TTLCache(ttl_sec=0.001, max_entries=16)
    errors: list[BaseException] = []
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)

    def worker(n: int) -> None:
        try:
            for i in range(3000):
                key = ("t", f"{n}-{i % 50}")
                cache.get(key)
                cache.set(key, None)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    try:
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    finally:
        sys.setswitchinterval(old_interval)

    assert errors == []
    assert len(cache._store) <= 16


def test_expired_entries_are_swept_before_the_oldest_live_one_is_evicted():
    cache = _TTLCache(ttl_sec=3600.0, max_entries=3)
    for name in ("live", "old1", "old2"):
        cache.set(("t", name), None)
    for name in ("old1", "old2"):
        cache._store[("t", name)].expires_at = 0.0  # already expired

    cache.set(("t", "new"), None)

    assert set(cache._store) == {("t", "live"), ("t", "new")}

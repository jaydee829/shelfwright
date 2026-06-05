"""Tests for the opt-in embedding throttle (_throttle_embedding) in scouts/utils.py."""

import threading
import time

from agentic_librarian.scouts import utils


def test_throttle_does_not_hold_lock_while_sleeping(monkeypatch):
    """The lock only guards slot scheduling; the pacing sleep must happen outside it,
    so concurrent callers can reserve their own slots instead of serializing on the
    full sleep of whoever got there first."""
    monkeypatch.setattr(utils, "_EMBED_MIN_INTERVAL", 0.5)
    monkeypatch.setattr(utils, "_last_embed", time.monotonic())  # next call must wait the full interval

    sleeper = threading.Thread(target=utils._throttle_embedding)
    sleeper.start()
    time.sleep(0.1)  # let the sleeper enter its pacing wait

    acquired = utils._embed_lock.acquire(timeout=0.1)
    if acquired:
        utils._embed_lock.release()
    sleeper.join()

    assert acquired, "_embed_lock must be free while a caller sleeps out its pacing wait"


def test_throttle_paces_consecutive_calls(monkeypatch):
    """Two back-to-back cache-miss calls are spaced by at least the configured interval."""
    monkeypatch.setattr(utils, "_EMBED_MIN_INTERVAL", 0.2)
    monkeypatch.setattr(utils, "_last_embed", 0.0)

    start = time.monotonic()
    utils._throttle_embedding()  # first call: no prior embed recent enough -> no wait
    utils._throttle_embedding()  # second call: must wait out the interval
    elapsed = time.monotonic() - start

    assert elapsed >= 0.2


def test_throttle_noop_when_disabled(monkeypatch):
    """Interval 0/unset means no pacing at all (the interactive path)."""
    monkeypatch.setattr(utils, "_EMBED_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(utils, "_last_embed", time.monotonic())

    start = time.monotonic()
    utils._throttle_embedding()
    elapsed = time.monotonic() - start

    assert elapsed < 0.05

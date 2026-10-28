# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.log_filters (EventSub keepalive-missed log collapsing)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import log_filters
from modules.log_filters import KeepaliveMissedFilter, install_keepalive_missed_filter


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(log_filters.time, "monotonic", c)
    return c


def rec(msg="Keepalive missed, connection lost", level=logging.WARNING, args=None):
    return logging.LogRecord("twitchAPI.eventsub.websocket", level, __file__, 1, msg, args, None)


def test_unrelated_message_passes_untouched(clock):
    f = KeepaliveMissedFilter()
    r = rec("something else", logging.ERROR)
    assert f.filter(r) is True
    assert r.levelno == logging.ERROR
    assert f._seen_first is False


def test_getmessage_error_passes():
    f = KeepaliveMissedFilter()
    r = rec("bad %s %s", args=("one",))
    assert f.filter(r) is True


def test_below_warning_not_counted(clock):
    f = KeepaliveMissedFilter()
    r = rec(level=logging.INFO)
    assert f.filter(r) is True
    assert f._seen_first is False and f._last_miss_at == 0.0


def test_first_miss_passes_then_demoted_to_debug(clock):
    f = KeepaliveMissedFilter()
    first = rec()
    assert f.filter(first) is True
    assert first.levelno == logging.WARNING
    clock.t += 10
    second = rec("KEEPALIVE MISSED again")
    assert f.filter(second) is True
    assert second.levelno == logging.DEBUG and second.levelname == "DEBUG"
    assert f._suppressed == 1


def test_summary_after_interval_of_continuous_misses(clock):
    f = KeepaliveMissedFilter()
    f.filter(rec())
    for _ in range(5):
        clock.t += 60  # gaps below interval keep the window alive
        r = rec()
        f.filter(r)
    # elapsed is now 300s from window start: summary record
    assert r.levelno == logging.WARNING
    assert r.getMessage() == "keepalive missed x5 in the last 5m; further misses at DEBUG"
    assert f._suppressed == 0 and f._window_start == clock.t
    clock.t += 1
    nxt = rec()
    f.filter(nxt)
    assert nxt.levelno == logging.DEBUG


def test_quiet_gap_resets_to_first_miss(clock):
    f = KeepaliveMissedFilter()
    f.filter(rec())
    clock.t += 1
    f.filter(rec())
    assert f._suppressed == 1
    clock.t += KeepaliveMissedFilter.SUMMARY_INTERVAL_SEC
    r = rec()
    assert f.filter(r) is True
    assert r.levelno == logging.WARNING
    assert f._suppressed == 0 and f._window_start == clock.t


def test_install_is_idempotent_and_attaches_to_logger():
    logger = logging.getLogger("twitchAPI.eventsub.websocket")
    before = list(logger.filters)
    try:
        for f in [x for x in before if isinstance(x, KeepaliveMissedFilter)]:
            logger.removeFilter(f)
        a = install_keepalive_missed_filter()
        b = install_keepalive_missed_filter()
        assert a is b
        assert isinstance(a, KeepaliveMissedFilter)
        assert sum(isinstance(x, KeepaliveMissedFilter) for x in logger.filters) == 1
    finally:
        logger.filters[:] = before

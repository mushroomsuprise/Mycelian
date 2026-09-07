# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Logging filters that collapse known high-volume library warnings."""

from __future__ import annotations

import logging
import threading
import time


class KeepaliveMissedFilter(logging.Filter):
    """Allow the first EventSub keepalive miss, then DEBUG, with a 5-minute summary."""

    SUMMARY_INTERVAL_SEC = 300.0

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._seen_first = False
        self._suppressed = 0
        self._window_start = 0.0
        self._last_miss_at = 0.0

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        if "keepalive missed" not in message.lower():
            return True
        if record.levelno < logging.WARNING:
            return True

        now = time.monotonic()
        with self._lock:
            if self._last_miss_at and (now - self._last_miss_at) >= self.SUMMARY_INTERVAL_SEC:
                self._seen_first = False
                self._suppressed = 0
            self._last_miss_at = now

            if not self._seen_first:
                self._seen_first = True
                self._window_start = now
                self._suppressed = 0
                return True

            self._suppressed += 1
            elapsed = now - self._window_start
            if elapsed >= self.SUMMARY_INTERVAL_SEC:
                count = self._suppressed
                self._suppressed = 0
                self._window_start = now
                record.msg = "keepalive missed x%s in the last 5m; further misses at DEBUG"
                record.args = (count,)
                return True

            record.levelno = logging.DEBUG
            record.levelname = "DEBUG"
            return True


def install_keepalive_missed_filter() -> KeepaliveMissedFilter:
    """Attach the keepalive filter to twitchAPI's EventSub websocket logger."""
    logger = logging.getLogger("twitchAPI.eventsub.websocket")
    for existing in logger.filters:
        if isinstance(existing, KeepaliveMissedFilter):
            return existing
    filt = KeepaliveMissedFilter()
    logger.addFilter(filt)
    return filt

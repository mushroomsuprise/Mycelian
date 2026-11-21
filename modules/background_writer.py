# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""One background thread that runs saves in order, merging repeats.

UI handlers (tab switches, window moves, feed paging) and gevent hub handlers
must not wait on a database write: SQLite can wait up to ``busy_timeout`` for
another writer, and the Firebase/Mongo backends wait on the network. A
``BackgroundWriter`` takes the write off the caller's thread.

* Jobs run one at a time on a single thread, so writes keep their order.
* Submitting a key that is still waiting replaces the waiting job and moves it
  to the back of the queue, so ten quick tab switches cost one save.
* ``flush`` waits for everything queued so far; it is registered as a shutdown
  flush so the last change is written before the app exits.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class BackgroundWriter:
    def __init__(self, name: str) -> None:
        self.name = name
        self._cv = threading.Condition()
        self._pending: "OrderedDict[str, Callable[[], object]]" = OrderedDict()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._flush_registered = False

    def submit(self, key: str, job: Callable[[], object]) -> None:
        """Run ``job`` on the writer thread, replacing a waiting job with the same key."""
        with self._cv:
            self._pending.pop(key, None)
            self._pending[key] = job
            self._ensure_thread()
            self._cv.notify_all()
        self._register_flush()

    def pending(self) -> int:
        with self._cv:
            return len(self._pending) + (1 if self._running else 0)

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until every job queued so far has run. Returns False on timeout."""
        deadline = time.monotonic() + timeout
        with self._cv:
            while self._pending or self._running:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning("%s: %d save(s) still pending at flush", self.name, len(self._pending))
                    return False
                self._cv.wait(remaining)
        return True

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name=self.name, daemon=True)
        self._thread.start()

    def _register_flush(self) -> None:
        if self._flush_registered:
            return
        self._flush_registered = True
        try:
            from .shutdown import register_shutdown_flush

            register_shutdown_flush(self.name, self.flush)
        except Exception as exc:
            logger.debug("%s: shutdown flush not registered: %s", self.name, exc)

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._pending:
                    self._cv.wait()
                key, job = self._pending.popitem(last=False)
                self._running = True
            try:
                job()
            except Exception:
                logger.error("%s: save %r failed", self.name, key, exc_info=True)
            finally:
                with self._cv:
                    self._running = False
                    self._cv.notify_all()

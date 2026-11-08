# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Keep database writes in memory while Firebase / MongoDB can't be reached, and write them
once the connection is back.

``DatabaseManager`` hands a write here when it failed because of an outage (or while an
outage is already known). Writes are kept per path in the order they were made: a newer
``set`` or ``delete`` replaces an older one, an ``update`` merges into what's waiting. A
background thread checks every ``RETRY_SEC`` and, once the internet and the database answer,
writes everything in order. The user is told once when the connection drops (with how many
changes are waiting) and once when it's back. Reads of a path with a waiting write see that
write. Nothing is written to disk: changes made during an outage are lost if Mycelian is
closed before the connection returns (shutdown tries one last flush).
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

RETRY_SEC = 10.0
SHUTDOWN_FLUSH_SEC = 4.0

SET, UPDATE, DELETE = "set", "update", "delete"


class OfflineWrites:
    def __init__(
        self,
        write: Callable[[str, str, Any], bool],
        backend_reachable: Callable[[], bool],
        label: Callable[[], str],
    ) -> None:
        """``write(op, path, data)`` performs one write directly (True on success);
        ``backend_reachable()`` is a quick check that the database answers."""
        self._write = write
        self._reachable = backend_reachable
        self._label = label
        self._lock = threading.RLock()
        self._pending: "OrderedDict[str, Tuple[str, Any]]" = OrderedDict()
        self._offline = False
        self._since = 0.0
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._flushing = threading.Lock()

    # ----- state -----

    @property
    def offline(self) -> bool:
        return self._offline

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    # ----- queueing -----

    def queue(self, op: str, path: str, data: Any = None) -> None:
        """Keep a write for later (``op``: set / update / delete)."""
        data = copy.deepcopy(data)
        with self._lock:
            waiting = self._pending.pop(path, None)
            if op == UPDATE and waiting is not None:
                kind, value = waiting
                if kind == SET:
                    merged = dict(value or {})
                    merged.update(data or {})
                    op, data = SET, merged
                elif kind == UPDATE:
                    merged = dict(value or {})
                    merged.update(data or {})
                    data = merged
                # after a delete, an update writes just these fields: keep it an update
            self._pending[path] = (op, data)

    def overlay(self, path: str, read: Any) -> Any:
        """``read`` with any waiting write for ``path`` applied."""
        with self._lock:
            waiting = self._pending.get(path)
        if waiting is None:
            return read
        kind, value = waiting
        if kind == SET:
            return copy.deepcopy(value)
        if kind == DELETE:
            return {}
        base = dict(read) if isinstance(read, dict) else {}
        base.update(copy.deepcopy(value) or {})
        return base

    # ----- outage -----

    def go_offline(self, reason: str = "") -> None:
        """Start queueing every write and checking for the connection to come back."""
        with self._lock:
            started = not self._offline
            self._offline = True
            if started:
                self._since = time.time()
        if started:
            logger.warning("Database unreachable%s; keeping changes in memory until it's back",
                           f" ({reason})" if reason else "")
            self._notify_offline()
        self._ensure_thread()

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="MycelianDBOffline", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(RETRY_SEC):
            if not self._offline:
                return
            if self.try_flush():
                return

    def stop(self) -> None:
        self._stop.set()

    def try_flush(self) -> bool:
        """Write everything waiting if the database answers. True when nothing is left (back online)."""
        if not self._flushing.acquire(blocking=False):
            return False
        try:
            if not _internet_up() or not self._safe_reachable():
                return False
            written = 0
            while True:
                with self._lock:
                    if not self._pending:
                        break
                    path, (op, data) = next(iter(self._pending.items()))
                if not self._safe_write(op, path, data):
                    logger.info("Database still not accepting writes; %d change(s) waiting", self.pending_count())
                    return False
                with self._lock:
                    # Only drop it if nothing newer replaced it meanwhile.
                    if self._pending.get(path) == (op, data):
                        self._pending.pop(path, None)
                written += 1
            with self._lock:
                was_offline, self._offline = self._offline, False
            if was_offline:
                logger.info("Database connection is back; wrote %d change(s) kept during the outage", written)
                self._notify_back(written)
            return True
        finally:
            self._flushing.release()

    def flush_on_shutdown(self) -> None:
        """One last try at writing what's waiting (shutdown)."""
        if not self.pending_count():
            return
        deadline = time.monotonic() + SHUTDOWN_FLUSH_SEC
        while time.monotonic() < deadline:
            if self.try_flush():
                return
            time.sleep(0.5)
        logger.warning("Mycelian closed with %d database change(s) not written (the database was unreachable)",
                       self.pending_count())

    def _safe_write(self, op: str, path: str, data: Any) -> bool:
        try:
            return bool(self._write(op, path, data))
        except Exception:
            logger.debug("Offline flush write failed for %s", path, exc_info=True)
            return False

    def _safe_reachable(self) -> bool:
        try:
            return bool(self._reachable())
        except Exception:
            return False

    # ----- notices -----

    def _notify_offline(self) -> None:
        try:
            from .notification_engine import nav_actions_settings, notify

            label = self._label()
            notify(
                "Changes are kept in memory and saved as soon as the connection is back. Keep Mycelian open "
                "until then: changes still waiting when it closes are lost.",
                type="warning",
                source="database",
                title=f"Lost the connection to the {label} database",
                hint="This usually means the internet dropped. Mycelian reconnects on its own.",
                dedupe_key="db:offline",
                dedupe_cooldown_sec=1800.0,
                sticky=True,
                actions=nav_actions_settings("Database"),
            )
        except Exception:
            logger.debug("Database offline notice failed", exc_info=True)

    def _notify_back(self, written: int) -> None:
        try:
            from .notification_engine import notify

            label = self._label()
            minutes = max(1, round((time.time() - self._since) / 60)) if self._since else 0
            message = (f"Saved {written} change{'s' if written != 1 else ''} made while it was unreachable."
                       if written else "Nothing was waiting to be saved.")
            notify(
                message,
                type="success",
                source="database",
                title=f"Reconnected to the {label} database",
                hint=f"It was unreachable for about {minutes} minute{'s' if minutes != 1 else ''}." if minutes else None,
                dedupe_key="db:offline",
            )
        except Exception:
            logger.debug("Database reconnected notice failed", exc_info=True)


def _internet_up() -> bool:
    try:
        from .connection_monitor import is_internet_available

        return bool(is_internet_available(blocking=True))
    except TypeError:
        try:
            from .connection_monitor import is_internet_available

            return bool(is_internet_available())
        except Exception:
            return True
    except Exception:
        return True


# Words in a backend error that mean "couldn't reach it", not "it said no".
_NETWORK_WORDS = ("timed out", "timeout", "connection", "unreachable", "name resolution", "getaddrinfo",
                  "nodename", "temporary failure", "max retries", "serverselection", "autoreconnect",
                  "network", "503", "service unavailable", "ssl")


def looks_like_outage(status: str, reason: str) -> bool:
    if status == "timeout":
        return True
    text = (reason or "").lower()
    return any(w in text for w in _NETWORK_WORDS)


def describe(queue: Optional[OfflineWrites]) -> Dict[str, Any]:
    """For status displays: ``{"offline": bool, "pending": n}``."""
    if queue is None:
        return {"offline": False, "pending": 0}
    return {"offline": queue.offline, "pending": queue.pending_count()}

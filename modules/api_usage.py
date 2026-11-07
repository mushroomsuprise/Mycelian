# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""How many PSN / Steam API requests the app makes, plus a small 429 backoff helper.

``record`` is called once per HTTP request (the Steam client's request method and a
response hook on the PSN session). Counts live in memory only: the last hour and today
(since local midnight) per platform, with a per-endpoint breakdown, and an hourly INFO
line in the log. ``snapshot`` / ``summary_text`` feed the Settings tabs.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter, deque
from datetime import datetime
from typing import Any, Deque, Dict, Optional, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

HOUR = 3600.0
DAY = 86400.0
LOG_EVERY_SEC = HOUR

_lock = threading.Lock()
# platform -> deque of (ts, endpoint, status); entries older than a day are dropped
_entries: Dict[str, Deque[Tuple[float, str, Optional[int]]]] = {}
_last_log = time.time()

# IDs inside URL paths: account IDs, NPWR IDs, NP Title IDs, Steam app IDs / SteamID64s
_ID_SEGMENT = re.compile(r"^(?:\d{3,}|NPWR\d+_\d+|[A-Z]{4}\d{5}_\d{2}|me)$")


def endpoint_name(url: str) -> str:
    """A short, ID-free name for a request URL ("/trophy/v1/users/{id}/trophyTitles")."""
    text = urlsplit(str(url or "")).path
    parts = [("{id}" if _ID_SEGMENT.match(p) else p) for p in text.split("/") if p]
    # Drop the generic "/api" prefix PSN puts in front of everything.
    if parts and parts[0] == "api":
        parts = parts[1:]
    return "/" + "/".join(parts)


def _midnight(now: float) -> float:
    d = datetime.fromtimestamp(now)
    return datetime(d.year, d.month, d.day).timestamp()


def _prune(q: Deque[Tuple[float, str, Optional[int]]], now: float) -> None:
    cutoff = now - DAY
    while q and q[0][0] < cutoff:
        q.popleft()


def record(platform: str, endpoint: str, status: Optional[int] = None, *, now: Optional[float] = None) -> None:
    """Count one request. ``status`` is the HTTP status (None for a network error)."""
    global _last_log
    t = time.time() if now is None else now
    with _lock:
        q = _entries.setdefault(platform, deque())
        q.append((t, endpoint, status))
        _prune(q, t)
        due = t - _last_log >= LOG_EVERY_SEC
        if due:
            _last_log = t
    if due:
        _log_summary(t)


def snapshot(platform: str, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Counts for one platform: last hour, today, errors and 429s in the last hour, top endpoints."""
    t = time.time() if now is None else now
    hour_cut, day_cut = t - HOUR, _midnight(t)
    hour = today = errors = limited = 0
    endpoints: Counter = Counter()
    with _lock:
        q = _entries.get(platform)
        items = list(q) if q else []
    for ts, ep, status in items:
        if ts >= day_cut:
            today += 1
        if ts >= hour_cut:
            hour += 1
            endpoints[ep] += 1
            if status is None or status >= 400:
                errors += 1
            if status == 429:
                limited += 1
    return {"hour": hour, "today": today, "errors_hour": errors, "rate_limited_hour": limited,
            "endpoints": dict(endpoints.most_common())}


def timeline(platforms, *, minutes: int = 60, now: Optional[float] = None) -> Dict[str, Any]:
    """Requests per minute for the last ``minutes`` minutes (oldest first, the current minute last):
    ``{"labels": ["14:05", ...], "starts": [epoch, ...], "counts": {platform: [n, ...]},
    "limited": {platform: [429s, ...]}}``."""
    t = time.time() if now is None else now
    end = (int(t // 60) + 1) * 60.0
    start = end - minutes * 60.0
    starts = [start + i * 60.0 for i in range(minutes)]
    out: Dict[str, Any] = {
        "labels": [datetime.fromtimestamp(s).strftime("%H:%M") for s in starts],
        "starts": starts,
        "counts": {},
        "limited": {},
    }
    for platform in platforms:
        counts, limited = [0] * minutes, [0] * minutes
        with _lock:
            q = _entries.get(platform)
            items = list(q) if q else []
        for ts, _ep, status in items:
            i = int((ts - start) // 60)
            if 0 <= i < minutes:
                counts[i] += 1
                if status == 429:
                    limited[i] += 1
        out["counts"][platform] = counts
        out["limited"][platform] = limited
    return out


def summary_text(platform: str, *, now: Optional[float] = None) -> str:
    s = snapshot(platform, now=now)
    text = f"API calls: {s['hour']:,} last hour · {s['today']:,} today"
    if s["rate_limited_hour"]:
        text += f" · {s['rate_limited_hour']:,} rate-limited"
    return text


def _log_summary(now: float) -> None:
    with _lock:
        platforms = sorted(_entries)
    for platform in platforms:
        s = snapshot(platform, now=now)
        if not s["hour"]:
            continue
        top = ", ".join(f"{ep} {n}" for ep, n in list(s["endpoints"].items())[:5])
        logger.info("%s API: %d requests in the last hour (%d today, %d errors, %d rate-limited). Top: %s",
                    platform, s["hour"], s["today"], s["errors_hour"], s["rate_limited_hour"], top)


def reset() -> None:
    """Forget every count (tests)."""
    global _last_log
    with _lock:
        _entries.clear()
        _last_log = time.time()


class ChangeGate:
    """Lets a broadcast through when its payload changed, or at least every ``heartbeat`` seconds
    (so a page that missed one still catches up)."""

    def __init__(self, heartbeat: float = 60.0) -> None:
        self.heartbeat = heartbeat
        self._sig: Optional[str] = None
        self._at = 0.0
        self._lock = threading.Lock()

    def should_send(self, payload: Any, now: Optional[float] = None) -> bool:
        import json

        t = time.time() if now is None else now
        try:
            sig = json.dumps(payload, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return True
        with self._lock:
            if sig == self._sig and t - self._at < self.heartbeat:
                return False
            self._sig, self._at = sig, t
            return True

    def reset(self) -> None:
        with self._lock:
            self._sig, self._at = None, 0.0


class Backoff:
    """A rate-limit pause: ``hit()`` after a 429 pauses until Retry-After (or a delay that
    doubles from ``base`` up to ``cap``); ``ok()`` after a success resets the delay."""

    def __init__(self, base: float = 60.0, cap: float = 900.0) -> None:
        self.base = base
        self.cap = cap
        self.until = 0.0
        self._delay = 0.0
        self._lock = threading.Lock()

    def active(self, now: Optional[float] = None) -> bool:
        return (time.time() if now is None else now) < self.until

    def remaining(self, now: Optional[float] = None) -> float:
        return max(0.0, self.until - (time.time() if now is None else now))

    def hit(self, retry_after: Any = None, now: Optional[float] = None) -> float:
        """Start (or extend) the pause; returns its length in seconds."""
        t = time.time() if now is None else now
        with self._lock:
            self._delay = min(self.cap, self._delay * 2 if self._delay else self.base)
            wait = self._delay
            try:
                if retry_after is not None and float(retry_after) > 0:
                    wait = min(self.cap, max(float(retry_after), 1.0))
            except (TypeError, ValueError):
                pass
            self.until = max(self.until, t + wait)
            return wait

    def ok(self) -> None:
        with self._lock:
            self._delay = 0.0

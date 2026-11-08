# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""How many requests Mycelian makes to each third-party API, measured against each platform's
limit where one is known, plus a small 429 backoff helper and a change gate for broadcasts.

``record`` is called once per HTTP request:

* PSN and Steam: from their own clients (a response hook on the PSN session, the Steam
  client's request method).
* Twitch (Helix), YouTube, Spotify, Streamlabs, StreamElements: by the HTTP hooks installed
  here (``install_http_hooks``), keyed by host, so every call site is counted.

Counts are kept per minute and stored in ``data/api_usage.db`` (local SQLite, never the app
database): written every minute and at shutdown, loaded at start, kept ``KEEP_DAYS`` days. So
the Settings counters and graphs have history across restarts. The last hour's per-endpoint
breakdown is in memory only (for the hourly log line).

``LIMITS`` says what each platform allows:

* Steam: 100,000 calls a day (Steam Web API terms).
* PSN: Sony doesn't publish a limit; the community estimate psnawp is built around is about
  300 requests per 15 minutes.
* Twitch: Helix allows 800 points a minute per token; nearly every call costs 1 point.
* YouTube: the Data API's default quota is 10,000 units a day, reset at midnight Pacific.
  Calls cost different amounts (``YOUTUBE_UNIT_COSTS``: a search is 100 units).
* Spotify, Streamlabs, StreamElements: no published number, so only request counts are shown.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

HOUR = 3600.0
DAY = 86400.0
LOG_EVERY_SEC = HOUR
KEEP_DAYS = 31  # in the database
MEMORY_DAYS = 9  # in memory: 7 days of graph plus a day's window before its first point
FLUSH_EVERY_SEC = 60.0
DB_FILE = "api_usage.db"

ROLLING, DAILY_PACIFIC = "rolling", "daily_pacific"


@dataclass(frozen=True)
class Limit:
    requests: int
    window_sec: float  # rolling windows; for DAILY_PACIFIC it's a day
    window_text: str  # "15 min", "24 h", "today"
    source: str  # where the number comes from
    estimated: bool
    reset: str = ROLLING
    unit: str = "requests"  # what ``requests`` counts ("units" for YouTube's quota)


LIMITS: Dict[str, Limit] = {
    "PSN": Limit(300, 15 * 60.0, "15 min", "Sony doesn't publish a limit: this is the community estimate", True),
    "Steam": Limit(100_000, DAY, "24 h", "the Steam Web API terms", False),
    "Twitch": Limit(800, 60.0, "1 min", "Twitch's Helix rate limit (800 points a minute per token)", False),
    "YouTube": Limit(10_000, DAY, "today", "the YouTube Data API's default daily quota (resets at midnight Pacific)",
                     False, reset=DAILY_PACIFIC, unit="units"),
}

# Platforms with no published limit: shown as plain request counts.
NO_LIMIT_NOTES: Dict[str, str] = {
    "Spotify": "Spotify doesn't publish a number; it limits requests over a rolling 30 seconds.",
    "Streamlabs": "Streamlabs doesn't publish a rate limit for its API.",
    "StreamElements": "StreamElements doesn't publish a rate limit for its API.",
}

PLATFORMS = ("PSN", "Steam", "Twitch", "YouTube", "Spotify", "Streamlabs", "StreamElements")

# YouTube Data API quota cost per call, by path (https://developers.google.com/youtube/v3/determine_quota_cost).
# Anything not listed costs 1 unit.
YOUTUBE_UNIT_COSTS = {
    "/youtube/v3/search": 100,
    "/youtube/v3/liveChat/messages": 5,
}

_lock = threading.Lock()
# platform -> minute (epoch // 60) -> [requests, cost, errors, rate-limited]
_minutes: Dict[str, Dict[int, List[int]]] = {}
# platform -> last hour of (ts, endpoint) for the per-endpoint breakdown (memory only)
_recent: Dict[str, Deque[Tuple[float, str]]] = {}
_dirty: set = set()  # (platform, minute) not yet in the database
_loaded = False
_persist = True
_db_path_override: Optional[str] = None
_flusher: Optional[threading.Thread] = None
_flusher_stop = threading.Event()
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


# ----- storage -----


def _db_path() -> str:
    if _db_path_override:
        return _db_path_override
    from .path_utils import get_data_path

    return get_data_path(DB_FILE)


def _connect() -> sqlite3.Connection:
    path = _db_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS usage (platform TEXT NOT NULL, minute INTEGER NOT NULL, "
        "requests INTEGER NOT NULL, cost INTEGER NOT NULL, errors INTEGER NOT NULL, "
        "limited INTEGER NOT NULL, PRIMARY KEY (platform, minute))"
    )
    return conn


def _ensure_loaded() -> None:
    """Read the stored history once (the last MEMORY_DAYS days)."""
    global _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        _loaded = True
        if not _persist:
            return
        try:
            conn = _connect()
            try:
                since = int(time.time() // 60) - MEMORY_DAYS * 1440
                rows = conn.execute(
                    "SELECT platform, minute, requests, cost, errors, limited FROM usage WHERE minute >= ?", (since,)
                ).fetchall()
            finally:
                conn.close()
        except Exception:
            logger.debug("API usage history unavailable", exc_info=True)
            return
        for platform, minute, req, cost, err, lim in rows:
            bucket = _minutes.setdefault(platform, {}).setdefault(int(minute), [0, 0, 0, 0])
            # Counts recorded before the load (in this run) add to the stored ones.
            for i, v in enumerate((req, cost, err, lim)):
                bucket[i] += int(v)


def flush() -> None:
    """Write the minutes changed since the last flush (also at shutdown)."""
    if not _persist:
        return
    with _lock:
        keys = list(_dirty)
        _dirty.clear()
        rows = []
        for platform, minute in keys:
            b = _minutes.get(platform, {}).get(minute)
            if b is not None:
                rows.append((platform, minute, b[0], b[1], b[2], b[3]))
    if not rows:
        return
    try:
        conn = _connect()
        try:
            conn.executemany(
                "INSERT INTO usage (platform, minute, requests, cost, errors, limited) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(platform, minute) DO UPDATE SET requests = excluded.requests, cost = excluded.cost, "
                "errors = excluded.errors, limited = excluded.limited",
                rows,
            )
            conn.execute("DELETE FROM usage WHERE minute < ?", (int(time.time() // 60) - KEEP_DAYS * 1440,))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.debug("API usage history not saved", exc_info=True)
        with _lock:
            _dirty.update(keys)  # try again next time


def _flush_loop() -> None:
    while not _flusher_stop.wait(FLUSH_EVERY_SEC):
        flush()


def _ensure_flusher() -> None:
    global _flusher
    if not _persist:
        return
    if _flusher is not None and _flusher.is_alive():
        return
    _flusher_stop.clear()
    _flusher = threading.Thread(target=_flush_loop, name="ApiUsageFlush", daemon=True)
    _flusher.start()
    try:
        from .shutdown import register_shutdown_flush

        register_shutdown_flush("api_usage", flush)
    except Exception:
        logger.debug("API usage shutdown flush not registered", exc_info=True)


def configure_storage(*, enabled: bool = True, path: Optional[str] = None) -> None:
    """Turn the history file on or off, or point it elsewhere (tests)."""
    global _persist, _db_path_override, _loaded
    _persist = enabled
    _db_path_override = path
    _loaded = False


# ----- counting -----


def record(platform: str, endpoint: str, status: Optional[int] = None, *, cost: int = 1,
           now: Optional[float] = None) -> None:
    """Count one request. ``status`` is the HTTP status (None for a network error); ``cost`` is
    what it takes from the quota (YouTube units; 1 elsewhere)."""
    global _last_log
    _ensure_loaded()
    t = time.time() if now is None else now
    minute = int(t // 60)
    with _lock:
        bucket = _minutes.setdefault(platform, {}).setdefault(minute, [0, 0, 0, 0])
        bucket[0] += 1
        bucket[1] += max(0, int(cost))
        if status is None or status >= 400:
            bucket[2] += 1
        if status == 429:
            bucket[3] += 1
        _dirty.add((platform, minute))
        q = _recent.setdefault(platform, deque())
        q.append((t, endpoint))
        while q and q[0][0] < t - HOUR:
            q.popleft()
        oldest = minute - MEMORY_DAYS * 1440
        mins = _minutes[platform]
        if len(mins) > MEMORY_DAYS * 1440 + 60:
            for m in [m for m in mins if m < oldest]:
                del mins[m]
        due = t - _last_log >= LOG_EVERY_SEC
        if due:
            _last_log = t
    _ensure_flusher()
    if due:
        _log_summary(t)


def _sum(platform: str, start_minute: int, end_minute: int, index: int) -> int:
    """Total of one counter over minutes [start, end] (caller holds no lock)."""
    with _lock:
        mins = _minutes.get(platform)
        if not mins:
            return 0
        if end_minute - start_minute + 1 <= len(mins):
            return sum(mins[m][index] for m in range(start_minute, end_minute + 1) if m in mins)
        return sum(b[index] for m, b in mins.items() if start_minute <= m <= end_minute)


# ----- Pacific time (YouTube's quota day) -----


def _pacific_offset(utc: datetime) -> timedelta:
    try:
        from zoneinfo import ZoneInfo

        offset = utc.astimezone(ZoneInfo("America/Los_Angeles")).utcoffset()
        if offset is not None:
            return offset
    except Exception:
        pass
    # No time zone data (Windows without tzdata): US daylight saving rules.
    year = utc.year
    march = datetime(year, 3, 8, 10, tzinfo=timezone.utc)  # 2 am PST on the second Sunday of March
    start = march + timedelta(days=(6 - march.weekday()) % 7)
    nov = datetime(year, 11, 1, 9, tzinfo=timezone.utc)  # 2 am PDT on the first Sunday of November
    end = nov + timedelta(days=(6 - nov.weekday()) % 7)
    return timedelta(hours=-7) if start <= utc < end else timedelta(hours=-8)


def pacific_day_start(ts: float) -> float:
    """Epoch of the last midnight Pacific at or before ``ts``."""
    utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    local = utc + _pacific_offset(utc)
    midnight_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    # The UTC time of that midnight is about 8 hours later; DST changes at 2 am, so the offset
    # an hour or so after midnight is midnight's own.
    return (midnight_local - _pacific_offset(midnight_local + timedelta(hours=8))).timestamp()


def _window_start_minute(limit: "Limit", end_ts: float) -> int:
    """First minute counted in the limit's window ending at ``end_ts``."""
    if limit.reset == DAILY_PACIFIC:
        return int(pacific_day_start(end_ts) // 60)
    return int((end_ts - limit.window_sec) // 60) + 1


def _local_midnight(now: float) -> float:
    d = datetime.fromtimestamp(now)
    return datetime(d.year, d.month, d.day).timestamp()


# ----- reading -----


def snapshot(platform: str, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Requests in the last hour and today (local), errors and 429s in the last hour, top endpoints."""
    _ensure_loaded()
    t = time.time() if now is None else now
    m = int(t // 60)
    hour_start, day_start = m - 59, int(_local_midnight(t) // 60)
    with _lock:
        q = _recent.get(platform)
        recent = list(q) if q else []
    endpoints = Counter(ep for ts, ep in recent if ts >= t - HOUR)
    return {
        "hour": _sum(platform, hour_start, m, 0),
        "today": _sum(platform, day_start, m, 0),
        "errors_hour": _sum(platform, hour_start, m, 2),
        "rate_limited_hour": _sum(platform, hour_start, m, 3),
        "endpoints": dict(endpoints.most_common()),
    }


def usage(platform: str, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Quota used in the platform's limit window and the share of the limit it is (0 when unknown)."""
    _ensure_loaded()
    t = time.time() if now is None else now
    limit = LIMITS.get(platform)
    m = int(t // 60)
    if limit is None:
        count = _sum(platform, m - 59, m, 0)
        return {"count": count, "percent": 0.0, "limit": None}
    count = _sum(platform, _window_start_minute(limit, t), m, 1)
    return {"count": count, "percent": count * 100.0 / limit.requests, "limit": limit}


RANGES = {
    # range key -> (points, minutes per point, label format)
    "1h": (60, 1, "%H:%M"),
    "24h": (96, 15, "%H:%M"),
    "7d": (84, 120, "%a %H:%M"),
}


def series(platforms: Iterable[str], *, span: str = "1h", now: Optional[float] = None) -> Dict[str, Any]:
    """History for the graphs, oldest first, the last point now.

    For platforms with a limit, ``percent`` is the share of the limit in use at each point
    (the requests in the limit's window ending there); ``count`` is that window's total.
    ``requests`` is the number of requests in each point's own step (for platforms without a
    limit, that's what's drawn). ``limited`` counts 429s in each step."""
    _ensure_loaded()
    points, step, fmt = RANGES.get(span, RANGES["1h"])
    t = time.time() if now is None else now
    end_minute = int(t // 60)
    ends = [end_minute - (points - 1 - i) * step for i in range(points)]
    out: Dict[str, Any] = {
        "span": span,
        "step_minutes": step,
        "labels": [datetime.fromtimestamp(e * 60).strftime(fmt) for e in ends],
        "percent": {}, "count": {}, "requests": {}, "limited": {},
    }
    for platform in platforms:
        limit = LIMITS.get(platform)
        first = ends[0] - step + 1
        lookback = first
        if limit is not None:
            lookback = min(first, _window_start_minute(limit, ends[0] * 60.0 + 59))
        with _lock:
            mins = dict(_minutes.get(platform, {}))
        span_len = end_minute - lookback + 1
        req = [0] * span_len
        cost = [0] * span_len
        lim = [0] * span_len
        for minute, b in mins.items():
            i = minute - lookback
            if 0 <= i < span_len:
                req[i], cost[i], lim[i] = b[0], b[1], b[3]

        def prefix(values: List[int]) -> List[int]:
            acc, total = [0], 0
            for v in values:
                total += v
                acc.append(total)
            return acc

        p_req, p_cost, p_lim = prefix(req), prefix(cost), prefix(lim)

        def total(acc: List[int], a: int, b: int) -> int:  # minutes a..b inclusive
            a, b = max(a, lookback), min(b, end_minute)
            if b < a:
                return 0
            return acc[b - lookback + 1] - acc[a - lookback]

        out["requests"][platform] = [total(p_req, e - step + 1, e) for e in ends]
        out["limited"][platform] = [total(p_lim, e - step + 1, e) for e in ends]
        if limit is None:
            out["percent"][platform] = None
            out["count"][platform] = out["requests"][platform]
            continue
        counts = [total(p_cost, _window_start_minute(limit, e * 60.0 + 59), e) for e in ends]
        out["count"][platform] = counts
        out["percent"][platform] = [round(c * 100.0 / limit.requests, 2) for c in counts]
    return out


def percent_text(value: float) -> str:
    """12% / 0.4% / <0.1%: whole numbers from 10%, one decimal below."""
    if value <= 0:
        return "0%"
    if value < 0.1:
        return "<0.1%"
    return f"{value:.0f}%" if value >= 10 else f"{value:.1f}%"


def summary_text(platform: str, *, now: Optional[float] = None) -> str:
    """"41% of the estimated limit (123 of ~300 requests in the last 15 min)", or for a platform
    without a published limit "38 requests in the last hour · 512 today"."""
    u = usage(platform, now=now)
    limit = u["limit"]
    snap = snapshot(platform, now=now)
    if limit is None:
        text = f"{snap['hour']:,} requests in the last hour · {snap['today']:,} today"
    else:
        approx = "~" if limit.estimated else ""
        which = "the estimated limit" if limit.estimated else "the limit"
        if limit.reset == DAILY_PACIFIC:
            where = "today"
        else:
            where = "in the last minute" if limit.window_text == "1 min" else f"in the last {limit.window_text}"
        text = (f"{percent_text(u['percent'])} of {which} "
                f"({u['count']:,} of {approx}{limit.requests:,} {limit.unit} {where})")
    if snap["rate_limited_hour"]:
        text += f" · {snap['rate_limited_hour']:,} rate-limited in the last hour"
    return text


def limit_note(platform: str) -> str:
    """Where the limit comes from (hover text)."""
    limit = LIMITS.get(platform)
    if limit is None:
        return NO_LIMIT_NOTES.get(platform, "")
    approx = "about " if limit.estimated else ""
    per = "a day" if limit.reset == DAILY_PACIFIC else f"per {limit.window_text}"
    return f"{platform}: {approx}{limit.requests:,} {limit.unit} {per}, from {limit.source}."


def _log_summary(now: float) -> None:
    with _lock:
        platforms = sorted(set(_minutes) | set(_recent))
    for platform in platforms:
        s = snapshot(platform, now=now)
        if not s["hour"]:
            continue
        top = ", ".join(f"{ep} {n}" for ep, n in list(s["endpoints"].items())[:5])
        logger.info("%s API: %d requests in the last hour (%d today, %d errors, %d rate-limited). Top: %s",
                    platform, s["hour"], s["today"], s["errors_hour"], s["rate_limited_hour"], top)


def reset() -> None:
    """Forget every count in memory (tests)."""
    global _last_log
    with _lock:
        _minutes.clear()
        _recent.clear()
        _dirty.clear()
        _last_log = time.time()


# ----- HTTP hooks for services whose clients don't count themselves -----

# (host suffix, path prefix) -> platform. Checked in order; PSN and Steam count in their own clients.
_HOST_RULES: Tuple[Tuple[str, str, str], ...] = (
    ("api.twitch.tv", "/helix", "Twitch"),
    ("www.googleapis.com", "/youtube/", "YouTube"),
    ("youtube.googleapis.com", "/youtube/", "YouTube"),
    ("api.spotify.com", "", "Spotify"),
    ("accounts.spotify.com", "/api/token", "Spotify"),
    ("streamlabs.com", "/api/", "Streamlabs"),
    ("api.streamelements.com", "", "StreamElements"),
)


def platform_for_url(url: Any) -> Optional[str]:
    """The platform a request URL counts toward, or None."""
    try:
        parts = urlsplit(str(url))
    except Exception:
        return None
    host = (parts.hostname or "").lower()
    path = parts.path or ""
    for suffix, prefix, platform in _HOST_RULES:
        if (host == suffix or host.endswith("." + suffix)) and path.startswith(prefix):
            return platform
    return None


def _cost_for(platform: str, url: Any) -> int:
    if platform != "YouTube":
        return 1
    path = urlsplit(str(url)).path.rstrip("/")
    return YOUTUBE_UNIT_COSTS.get(path, 1)


def count_url(url: Any, status: Optional[int]) -> None:
    """Count a finished request if its host belongs to a tracked platform."""
    platform = platform_for_url(url)
    if platform is None:
        return
    try:
        record(platform, endpoint_name(str(url)), status, cost=_cost_for(platform, url))
    except Exception:
        logger.debug("API usage count failed", exc_info=True)


_hooks_installed = False


def install_http_hooks() -> None:
    """Count requests made with ``requests`` and ``aiohttp`` to the hosts in ``_HOST_RULES``.

    Wraps ``requests.adapters.HTTPAdapter.send`` and ``aiohttp.ClientSession._request`` once;
    every other request passes through untouched."""
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True
    try:
        from requests.adapters import HTTPAdapter

        original_send = HTTPAdapter.send

        def send(self, request, *args, **kwargs):
            url = getattr(request, "url", "")
            if platform_for_url(url) is None:
                return original_send(self, request, *args, **kwargs)
            try:
                response = original_send(self, request, *args, **kwargs)
            except Exception:
                count_url(url, None)
                raise
            count_url(url, getattr(response, "status_code", None))
            return response

        send.__wrapped__ = original_send  # type: ignore[attr-defined]
        HTTPAdapter.send = send
    except Exception:
        logger.debug("requests usage hook not installed", exc_info=True)
    try:
        import aiohttp

        original_request = aiohttp.ClientSession._request

        async def _request(self, method, str_or_url, *args, **kwargs):
            if platform_for_url(str_or_url) is None:
                return await original_request(self, method, str_or_url, *args, **kwargs)
            try:
                response = await original_request(self, method, str_or_url, *args, **kwargs)
            except Exception:
                count_url(str_or_url, None)
                raise
            count_url(str_or_url, getattr(response, "status", None))
            return response

        _request.__wrapped__ = original_request  # type: ignore[attr-defined]
        aiohttp.ClientSession._request = _request
    except Exception:
        logger.debug("aiohttp usage hook not installed", exc_info=True)


# ----- helpers used by the API clients -----


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


install_http_hooks()

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Read-only analytics over the statistics event log for the Statistics tab.

Everything here only SELECTs from ``user_events`` through the StatisticsManager's
connection pool. Nothing writes, and the schema is untouched: charts, deltas and
leaderboards are derived at read time from the rows the manager already records.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SYSTEM_USERNAME = "__system__"


@dataclass(frozen=True)
class EventType:
    key: str
    label: str
    icon: str  # Material icon name
    amount_label: str = ""  # what ``amount`` means, when it is meaningful


# Every event type the StatisticsManager records (see ``_record_event``).
EVENT_TYPES: Dict[str, EventType] = {
    e.key: e
    for e in (
        EventType("chat_message", "Chat messages", "chat"),
        EventType("bit", "Bits", "diamond", "bits"),
        EventType("sub", "New subs", "star"),
        EventType("resub", "Resubs", "autorenew"),
        EventType("giftsub", "Gift subs", "redeem", "subs gifted"),
        EventType("donation", "Donations", "volunteer_activism"),
        EventType("point_redeem", "Point redeems", "toll", "points"),
        EventType("follow", "Follows", "favorite"),
        EventType("raid", "Raids", "groups"),
        EventType("watch_streak", "Watch streaks", "local_fire_department", "streak"),
        EventType("connector", "Connector runs", "cable"),
        EventType("chatbot_command", "Bot commands", "terminal"),
        EventType("chatbot_event", "Bot events", "smart_toy"),
        EventType("quote_redeem", "Quote redeems", "format_quote"),
        EventType("giveaway_entry", "Giveaway entries", "confirmation_number"),
        EventType("giveaway_win", "Giveaway wins", "emoji_events"),
        EventType("giveaway_draw_complete", "Giveaway draws", "casino"),
        EventType("poll_complete", "Polls finished", "ballot"),
        EventType("poll_win", "Poll wins", "how_to_vote"),
    )
}


@dataclass(frozen=True)
class Metric:
    """A headline number: a combination of event types and how to aggregate them."""

    key: str
    label: str
    icon: str
    parts: Tuple[Tuple[str, str], ...]  # (event_type, "count" | "sum")
    distinct_users: bool = False


METRICS: Tuple[Metric, ...] = (
    Metric("chat", "Chat messages", "chat", (("chat_message", "count"),)),
    Metric("viewers", "Active viewers", "groups", (), distinct_users=True),
    Metric("bits", "Bits cheered", "diamond", (("bit", "sum"),)),
    Metric("subs", "Subs", "star", (("sub", "count"), ("resub", "count"))),
    Metric("gifts", "Gifted subs", "redeem", (("giftsub", "sum"),)),
    Metric("points", "Channel points", "toll", (("point_redeem", "sum"),)),
    Metric("follows", "Follows", "favorite", (("follow", "count"),)),
    Metric("raids", "Raids", "groups_2", (("raid", "count"),)),
    Metric("donations", "Donations", "volunteer_activism", (("donation", "count"),)),
    Metric("commands", "Bot commands", "terminal", (("chatbot_command", "count"),)),
)
METRICS_BY_KEY: Dict[str, Metric] = {m.key: m for m in METRICS}


# ----- date ranges -----

@dataclass(frozen=True)
class DateRange:
    start: datetime
    end: datetime  # inclusive, end of day
    label: str

    @property
    def start_ts(self) -> float:
        return self.start.timestamp()

    @property
    def end_ts(self) -> float:
        return self.end.timestamp()

    @property
    def days(self) -> int:
        return max(1, (self.end.date() - self.start.date()).days + 1)

    def previous(self) -> "DateRange":
        """The equally long period right before this one (for deltas)."""
        span = timedelta(days=self.days)
        return DateRange(self.start - span, self.end - span, "previous period")


def _day_start(d: datetime) -> datetime:
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def _day_end(d: datetime) -> datetime:
    return d.replace(hour=23, minute=59, second=59, microsecond=999999)


PRESETS: Tuple[Tuple[str, str], ...] = (
    ("today", "Today"),
    ("7d", "7 days"),
    ("30d", "30 days"),
    ("90d", "90 days"),
    ("all", "All time"),
)


def preset_range(key: str, *, now: Optional[datetime] = None, first_event_ts: Optional[float] = None) -> DateRange:
    now = now or datetime.now()
    end = _day_end(now)
    if key == "today":
        return DateRange(_day_start(now), end, "Today")
    if key == "all":
        start = datetime.fromtimestamp(first_event_ts) if first_event_ts else now - timedelta(days=29)
        return DateRange(_day_start(start), end, "All time")
    days = {"7d": 7, "30d": 30, "90d": 90}.get(key, 30)
    return DateRange(_day_start(now - timedelta(days=days - 1)), end, f"Last {days} days")


def custom_range(start: datetime, end: datetime) -> DateRange:
    if end < start:
        start, end = end, start
    label = f"{start:%b %d, %Y} – {end:%b %d, %Y}"
    return DateRange(_day_start(start), _day_end(end), label)


# ----- buckets -----

def bucket_for(rng: DateRange) -> str:
    """Hourly buckets for a single day, daily otherwise (weekly past ~6 months)."""
    if rng.days <= 1:
        return "hour"
    if rng.days > 190:
        return "week"
    return "day"


_BUCKET_SQL = {
    "hour": "strftime('%Y-%m-%d %H', timestamp, 'unixepoch', 'localtime')",
    "day": "strftime('%Y-%m-%d', timestamp, 'unixepoch', 'localtime')",
    # Monday-based week key: the date of that week's Monday.
    "week": "date(timestamp, 'unixepoch', 'localtime', '-' || ((CAST(strftime('%w', timestamp, 'unixepoch', 'localtime') AS INTEGER) + 6) % 7) || ' days')",
}


def bucket_keys(rng: DateRange, bucket: str) -> List[str]:
    keys: List[str] = []
    if bucket == "hour":
        cur = rng.start
        while cur <= rng.end:
            keys.append(cur.strftime("%Y-%m-%d %H"))
            cur += timedelta(hours=1)
        return keys
    cur = rng.start
    if bucket == "week":
        cur = cur - timedelta(days=cur.weekday())
        step = timedelta(days=7)
    else:
        step = timedelta(days=1)
    while cur <= rng.end:
        keys.append(cur.strftime("%Y-%m-%d"))
        cur += step
    return keys


def bucket_label(key: str, bucket: str) -> str:
    if bucket == "hour":
        return datetime.strptime(key, "%Y-%m-%d %H").strftime("%H:00")
    d = datetime.strptime(key, "%Y-%m-%d")
    if bucket == "week":
        return "Week of " + d.strftime("%b %d")
    return d.strftime("%b %d")


# ----- queries -----

class Insights:
    """Read-only queries. Each call borrows one pooled connection and returns it."""

    def __init__(self, manager: Any):
        self.mgr = manager

    def _query(self, sql: str, params: Sequence[Any] = ()) -> List[Tuple[Any, ...]]:
        if not getattr(self.mgr, "_stats_db_initialized", False):
            return []
        conn = None
        try:
            conn = self.mgr._get_stats_db_connection()
            if conn is None:
                return []
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            return [tuple(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("statistics insights query failed: %s", e, exc_info=True)
            return []
        finally:
            self.mgr._return_stats_db_connection(conn)

    @staticmethod
    def _user_clause(username: Optional[str]) -> Tuple[str, List[Any]]:
        if not username:
            return "", []
        return " AND IFNULL(username_lower, lower(username)) = ?", [username.strip().lower()]

    # -- spans --

    def first_event_ts(self) -> Optional[float]:
        rows = self._query("SELECT MIN(timestamp) FROM user_events")
        return float(rows[0][0]) if rows and rows[0][0] is not None else None

    # -- per-type summary --

    def summary(self, rng: DateRange, username: Optional[str] = None) -> Dict[str, Any]:
        """``{"types": {type: {"count", "total", "peak"}}, "unique_users", "total_events"}``."""
        uc, up = self._user_clause(username)
        rows = self._query(
            "SELECT event_type, COUNT(*), COALESCE(SUM(amount), 0), COALESCE(MAX(amount), 0) "
            "FROM user_events WHERE timestamp >= ? AND timestamp <= ?" + uc + " GROUP BY event_type",
            [rng.start_ts, rng.end_ts, *up],
        )
        types = {
            r[0]: {"count": int(r[1] or 0), "total": float(r[2] or 0), "peak": float(r[3] or 0)}
            for r in rows
        }
        users = self._query(
            "SELECT COUNT(DISTINCT IFNULL(username_lower, lower(username))) FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? AND IFNULL(username_lower, lower(username)) != ?" + uc,
            [rng.start_ts, rng.end_ts, SYSTEM_USERNAME, *up],
        )
        return {
            "types": types,
            "unique_users": int(users[0][0] or 0) if users else 0,
            "total_events": sum(t["count"] for t in types.values()),
        }

    @staticmethod
    def metric_value(summary: Dict[str, Any], metric: Metric) -> float:
        if metric.distinct_users:
            return float(summary.get("unique_users", 0))
        types = summary.get("types", {})
        total = 0.0
        for event_type, agg in metric.parts:
            row = types.get(event_type) or {}
            total += float(row.get("total" if agg == "sum" else "count", 0) or 0)
        return total

    # -- time series --

    def timeseries(
        self, rng: DateRange, bucket: Optional[str] = None, username: Optional[str] = None
    ) -> Dict[str, Any]:
        """Per-bucket counts and sums for every event type, plus distinct users.

        Returns ``{"bucket", "keys", "labels", "types": {type: {"count": [...], "total": [...]}},
        "viewers": [...]}`` with zero-filled buckets.
        """
        bucket = bucket or bucket_for(rng)
        keys = bucket_keys(rng, bucket)
        index = {k: i for i, k in enumerate(keys)}
        expr = _BUCKET_SQL[bucket]
        uc, up = self._user_clause(username)
        rows = self._query(
            f"SELECT {expr} AS b, event_type, COUNT(*), COALESCE(SUM(amount), 0) FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ?" + uc + " GROUP BY b, event_type",
            [rng.start_ts, rng.end_ts, *up],
        )
        types: Dict[str, Dict[str, List[float]]] = {}
        for b, event_type, count, total in rows:
            i = index.get(b)
            if i is None:
                continue
            row = types.setdefault(event_type, {"count": [0] * len(keys), "total": [0.0] * len(keys)})
            row["count"][i] += int(count or 0)
            row["total"][i] += float(total or 0)
        viewers = [0] * len(keys)
        for b, n in self._query(
            f"SELECT {expr} AS b, COUNT(DISTINCT IFNULL(username_lower, lower(username))) FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ? AND IFNULL(username_lower, lower(username)) != ?"
            + uc + " GROUP BY b",
            [rng.start_ts, rng.end_ts, SYSTEM_USERNAME, *up],
        ):
            i = index.get(b)
            if i is not None:
                viewers[i] = int(n or 0)
        return {
            "bucket": bucket,
            "keys": keys,
            "labels": [bucket_label(k, bucket) for k in keys],
            "types": types,
            "viewers": viewers,
        }

    @staticmethod
    def metric_series(series: Dict[str, Any], metric: Metric) -> List[float]:
        n = len(series.get("keys", []))
        if metric.distinct_users:
            return list(series.get("viewers", [0] * n))
        out = [0.0] * n
        for event_type, agg in metric.parts:
            row = series.get("types", {}).get(event_type)
            if not row:
                continue
            vals = row["total"] if agg == "sum" else row["count"]
            for i, v in enumerate(vals):
                out[i] += v
        return out

    # -- heatmap --

    def heatmap(self, rng: DateRange, event_types: Optional[Sequence[str]] = None) -> List[List[int]]:
        """7×24 grid of event counts, rows Monday..Sunday, columns hour 0..23 (local time)."""
        grid = [[0] * 24 for _ in range(7)]
        type_clause, params = "", [rng.start_ts, rng.end_ts]
        if event_types:
            type_clause = " AND event_type IN (" + ",".join("?" * len(event_types)) + ")"
            params += list(event_types)
        for dow, hour, n in self._query(
            "SELECT CAST(strftime('%w', timestamp, 'unixepoch', 'localtime') AS INTEGER), "
            "CAST(strftime('%H', timestamp, 'unixepoch', 'localtime') AS INTEGER), COUNT(*) "
            "FROM user_events WHERE timestamp >= ? AND timestamp <= ?" + type_clause + " GROUP BY 1, 2",
            params,
        ):
            grid[(int(dow) + 6) % 7][int(hour)] = int(n or 0)  # SQLite %w: 0 = Sunday
        return grid

    # -- leaderboards --

    def leaders(
        self, rng: DateRange, event_types: Sequence[str], agg: str = "count", limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Top viewers for the given event types. ``agg`` is count, sum or max."""
        if not event_types:
            return []
        value = {"count": "COUNT(*)", "sum": "COALESCE(SUM(amount), 0)", "max": "COALESCE(MAX(amount), 0)"}[agg]
        placeholders = ",".join("?" * len(event_types))
        rows = self._query(
            f"SELECT MIN(username), {value} AS v, COUNT(*) FROM user_events "
            f"WHERE timestamp >= ? AND timestamp <= ? AND event_type IN ({placeholders}) "
            "AND IFNULL(username_lower, lower(username)) != ? "
            "GROUP BY IFNULL(username_lower, lower(username)) ORDER BY v DESC, 3 DESC LIMIT ?",
            [rng.start_ts, rng.end_ts, *event_types, SYSTEM_USERNAME, limit],
        )
        return [{"username": r[0] or "?", "value": float(r[1] or 0), "events": int(r[2] or 0)} for r in rows]

    def recent_events(
        self, rng: DateRange, username: str, event_types: Optional[Sequence[str]] = None, limit: int = 200
    ) -> List[Dict[str, Any]]:
        uc, up = self._user_clause(username)
        type_clause = ""
        params: List[Any] = [rng.start_ts, rng.end_ts, *up]
        if event_types:
            type_clause = " AND event_type IN (" + ",".join("?" * len(event_types)) + ")"
            params += list(event_types)
        rows = self._query(
            "SELECT event_type, amount, alert_name, timestamp FROM user_events "
            "WHERE timestamp >= ? AND timestamp <= ?" + uc + type_clause + " ORDER BY timestamp DESC LIMIT ?",
            [*params, limit],
        )
        return [
            {"event_type": r[0], "amount": float(r[1] or 0), "alert_name": r[2] or "", "timestamp": float(r[3] or 0)}
            for r in rows
        ]

    def user_span(self, username: str) -> Tuple[Optional[float], Optional[float], int]:
        uc, up = self._user_clause(username)
        rows = self._query("SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM user_events WHERE 1=1" + uc, up)
        if not rows or rows[0][2] == 0:
            return None, None, 0
        return float(rows[0][0]), float(rows[0][1]), int(rows[0][2])


def pct_change(current: float, previous: float) -> Optional[float]:
    """Percent change, or None when there is no previous value to compare against."""
    if previous <= 0:
        return None
    return (current - previous) / previous * 100.0


def compact(n: float) -> str:
    """1,284 / 12.9K / 4.2M."""
    n = float(n or 0)
    a = abs(n)
    if a >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if a >= 10_000:
        return f"{n / 1_000:.1f}K".replace(".0K", "K")
    if n == int(n):
        return f"{int(n):,}"
    return f"{n:,.1f}"

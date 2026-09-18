#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024-2026 Mycelian

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

# Server-side alert queue playback handshake (holders vs spectators).

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence
from urllib.parse import unquote

logger = logging.getLogger(__name__)

REGISTRATION_WINDOW_SEC = 1.5
HEARTBEAT_SILENCE_SEC = 3.0
ABSOLUTE_CAP_SEC = 600.0
NO_ACK_GRACE_SEC = 1.0
DEFAULT_DURATION_SEC = 5.0
DEFAULT_DELAY_BETWEEN_SEC = 0.5
ALERTS_ROUTE = "alerts"


def normalize_holder_route(value: Any) -> str:
    """Lowercase alnum-only key for comparing overlay routes to queue_holders."""
    return "".join(c for c in str(value or "").strip().lower() if c.isalnum())


def holder_route_allowed(route: Any, holders: Iterable[Any]) -> bool:
    """True if *route* is in the allow-list (exact or alnum-normalized)."""
    raw = unquote(str(route or "").strip()).lstrip("/")
    if not raw:
        return False
    holder_list = [str(h).strip().lstrip("/") for h in holders if str(h).strip()]
    if raw in holder_list:
        return True
    norm = normalize_holder_route(raw)
    if not norm:
        return False
    return any(normalize_holder_route(h) == norm for h in holder_list)


def coerce_duration_seconds(value: Any, default: float = DEFAULT_DURATION_SEC) -> float:
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return default
    if duration <= 0:
        return default
    return duration


def queue_holder_routes_for_alert(alert: Any) -> list[str]:
    """Routes allowed to join the handshake for this queued alert."""
    holders = [ALERTS_ROUTE]
    hold_only = False
    alert_name = ""
    if isinstance(alert, dict):
        hold_only = bool(alert.get("hold_queue_only"))
        alert_name = str(alert.get("alert_name") or "")
    else:
        hold_only = bool(getattr(alert, "hold_queue_only", False))
        alert_name = str(getattr(alert, "alert_name", "") or "")
    if hold_only and alert_name.strip():
        try:
            from .template_config_parser import (
                find_matching_template_routes_for_reward_title,
            )

            for route in find_matching_template_routes_for_reward_title(alert_name):
                if route and route not in holders:
                    holders.append(route)
        except Exception as exc:
            logger.debug("Could not resolve dedicated queue holders: %s", exc)
    return holders


def delay_between_alerts_seconds() -> float:
    try:
        from .template_config_parser import delay_between_alerts_seconds as _delay

        return _delay()
    except Exception:
        return DEFAULT_DELAY_BETWEEN_SEC


@dataclass
class AlertPlaybackSession:
    """One queued next_alert handshake."""

    seq: int
    holders: tuple[str, ...]
    duration_sec: float
    delay_between_sec: float
    started_at: float
    registration_deadline: float
    required_sids: set[str] = field(default_factory=set)
    completed_sids: set[str] = field(default_factory=set)
    last_progress_at: dict[str, float] = field(default_factory=dict)
    finished: bool = False
    finish_reason: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _mark_finished_unlocked(self, reason: str) -> bool:
        if self.finished:
            return False
        self.finished = True
        self.finish_reason = reason
        logger.debug(
            "Alert playback session seq=%s finished (%s) required=%s completed=%s",
            self.seq,
            reason,
            sorted(self.required_sids),
            sorted(self.completed_sids),
        )
        return True

    def _drop_silent_unlocked(self, now: float) -> None:
        cutoff = now - HEARTBEAT_SILENCE_SEC
        for sid in list(self.required_sids):
            if sid in self.completed_sids:
                continue
            last = self.last_progress_at.get(sid)
            if last is None:
                last = self.started_at
            if last <= cutoff:
                logger.debug(
                    "Alert playback seq=%s dropping silent holder sid=%s",
                    self.seq,
                    sid,
                )
                self.completed_sids.add(sid)

    def _maybe_finish_unlocked(self, now: float) -> bool:
        if self.finished:
            return False
        self._drop_silent_unlocked(now)
        pending = self.required_sids - self.completed_sids
        if self.required_sids and not pending:
            return self._mark_finished_unlocked("all_holders_done")
        if (now - self.started_at) >= ABSOLUTE_CAP_SEC:
            return self._mark_finished_unlocked("absolute_timeout")
        if now >= self.registration_deadline and not self.required_sids:
            fallback_at = (
                self.started_at
                + max(0.0, self.duration_sec)
                + max(0.0, self.delay_between_sec)
                + NO_ACK_GRACE_SEC
            )
            if now >= fallback_at:
                return self._mark_finished_unlocked("no_ack_fallback")
        return False

    def tick(self, now: Optional[float] = None) -> bool:
        """Drop silent holders and apply fallbacks. True if newly finished."""
        if now is None:
            now = time.monotonic()
        with self._lock:
            return self._maybe_finish_unlocked(now)

    def force_finish(self, reason: str) -> bool:
        with self._lock:
            return self._mark_finished_unlocked(reason)

    def _route_allowed(self, route: Any) -> bool:
        return holder_route_allowed(route, self.holders)

    def note_playing(
        self,
        sid: str,
        route: Any,
        *,
        is_preview: bool = False,
        now: Optional[float] = None,
    ) -> bool:
        """Register a live holder. Returns True if this sid is now required."""
        if now is None:
            now = time.monotonic()
        if is_preview or self.finished or not sid:
            return False
        if not self._route_allowed(route):
            logger.debug(
                "alert_playing ignored (spectator route=%s seq=%s sid=%s)",
                route,
                self.seq,
                sid,
            )
            return False
        with self._lock:
            if self.finished:
                return False
            self.required_sids.add(sid)
            self.last_progress_at[sid] = now
            logger.debug(
                "alert_playing accepted seq=%s sid=%s route=%s",
                self.seq,
                sid,
                route,
            )
            return True

    def note_progress(
        self,
        sid: str,
        *,
        is_preview: bool = False,
        now: Optional[float] = None,
    ) -> bool:
        if now is None:
            now = time.monotonic()
        if is_preview or self.finished or not sid:
            return False
        with self._lock:
            if self.finished or sid not in self.required_sids:
                return False
            self.last_progress_at[sid] = now
            return True

    def note_complete(
        self,
        sid: str,
        route: Any,
        *,
        is_preview: bool = False,
        now: Optional[float] = None,
    ) -> tuple[bool, bool]:
        """Return (accepted, newly_finished).

        Allowed holders may complete without a prior playing ack (skip / legacy
        templates). Spectators are rejected.
        """
        if now is None:
            now = time.monotonic()
        if is_preview or not sid:
            return False, False
        if not self._route_allowed(route):
            logger.debug(
                "alert_complete ignored (spectator route=%s seq=%s sid=%s)",
                route,
                self.seq,
                sid,
            )
            return False, False
        with self._lock:
            if self.finished:
                return False, False
            self.required_sids.add(sid)
            self.completed_sids.add(sid)
            self.last_progress_at[sid] = now
            newly = self._maybe_finish_unlocked(now)
            logger.debug(
                "alert_complete accepted seq=%s sid=%s route=%s newly_finished=%s",
                self.seq,
                sid,
                route,
                newly,
            )
            return True, newly

    def note_disconnect(self, sid: str, now: Optional[float] = None) -> bool:
        if now is None:
            now = time.monotonic()
        if not sid:
            return False
        with self._lock:
            if self.finished:
                return False
            if sid not in self.required_sids:
                return False
            self.completed_sids.add(sid)
            return self._maybe_finish_unlocked(now)


_session_lock = threading.Lock()
_current_session: Optional[AlertPlaybackSession] = None


def begin_session(
    seq: int,
    holders: Sequence[str],
    duration_sec: float,
    delay_between_sec: float = DEFAULT_DELAY_BETWEEN_SEC,
    *,
    now: Optional[float] = None,
) -> AlertPlaybackSession:
    """Start a new handshake; any unfinished session is superseded."""
    global _current_session
    if now is None:
        now = time.monotonic()
    cleaned = tuple(
        dict.fromkeys(str(h).strip() for h in holders if str(h).strip())
    )
    if not cleaned:
        cleaned = (ALERTS_ROUTE,)
    session = AlertPlaybackSession(
        seq=int(seq),
        holders=cleaned,
        duration_sec=coerce_duration_seconds(duration_sec),
        delay_between_sec=max(0.0, float(delay_between_sec or 0.0)),
        started_at=now,
        registration_deadline=now + REGISTRATION_WINDOW_SEC,
    )
    with _session_lock:
        previous = _current_session
        _current_session = session
    if previous is not None and not previous.finished:
        previous.force_finish("superseded")
    logger.debug(
        "Alert playback session started seq=%s holders=%s duration=%s",
        session.seq,
        session.holders,
        session.duration_sec,
    )
    return session


def get_session() -> Optional[AlertPlaybackSession]:
    with _session_lock:
        return _current_session


def abort_session(reason: str = "abort") -> bool:
    """Force-finish the current session. True if a session was open."""
    session = get_session()
    if session is None:
        return False
    return session.force_finish(reason)


def reset_session_for_tests() -> None:
    global _current_session
    with _session_lock:
        _current_session = None

#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Per-template chat polls: categories, votes, timers, and overlay snapshots."""

from __future__ import annotations

import copy
import logging
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DATA_PATH = "BotData/Polls"
# Vote bursts coalesce into one background write of the latest state (DBX-05).
_VOTE_WRITE_COALESCE_SECONDS = 0.25
DEFAULT_BANNER_MESSAGE = "Poll winner: {option} ({votes})"
_KEYWORD_BODY = re.compile(r"[^\w\-]+", re.UNICODE)

DEFAULT_CONFIG: Dict[str, Any] = {
    "categories": [],
    "duration_seconds": 0,
    "one_entry_per_user": True,
    "allow_vote_switch": False,
    "mods_can_start": False,
    "start_command": "",
    "announce_winners_as_banner": False,
    "announce_winners_in_chat": False,
    "banner_message": DEFAULT_BANNER_MESSAGE,
    "banner_duration": 10,
    "last_winners": [],
    # Also run it as a Twitch poll (votes then come from Twitch, not chat keywords).
    "twitch_poll": False,
    "twitch_title": "",
    "twitch_points_enabled": False,
    "twitch_points_per_vote": 100,
}

# A Twitch poll ends on Twitch's clock; the local timer is only a fallback if its end event never arrives.
_TWITCH_END_GRACE_SECONDS = 15.0
# Twitch runs one poll per channel, so only one overlay's poll can run on Twitch at a time.
ONE_TWITCH_POLL_MESSAGE = ("Only one Twitch poll can run at a time, and “{template}” is running one now. "
                           "End it first, or turn off “Also run it on Twitch” for this poll.")


def normalize_keyword(raw: Any, fallback_name: str = "") -> str:
    """Force a ``#`` prefix and drop spaces so the keyword is one chat token."""
    text = str(raw or "").strip()
    if not text:
        text = str(fallback_name or "").strip()
    if text.startswith("#"):
        text = text[1:]
    text = _KEYWORD_BODY.sub("", text)
    if not text:
        return ""
    return "#" + text


def normalize_command(raw: Any) -> str:
    """Force a leading ``!`` and drop spaces so the command is one chat token."""
    text = str(raw or "").strip()
    if text.startswith("!"):
        text = text[1:]
    text = _KEYWORD_BODY.sub("", text)
    if not text:
        return ""
    return "!" + text


def message_starts_with_command(message: str, command: str) -> bool:
    """True when ``message`` begins with ``command``, including its ``!``.

    ``!startpoll`` and ``!startpoll now`` match. ``startpoll``, ``please !startpoll``,
    and ``!startpolls`` do not. Comparison is case-insensitive.
    """
    text = str(message or "").strip().casefold()
    cmd = normalize_command(command).casefold()
    if not text or not cmd.startswith("!"):
        return False
    if not text.startswith(cmd):
        return False
    rest = text[len(cmd):]
    return rest == "" or rest[:1].isspace()


def sender_can_start_poll(msg_dict: Dict[str, Any]) -> bool:
    """Twitch moderator/broadcaster badges, or YouTube chat mod/owner flags."""
    if not isinstance(msg_dict, dict):
        return False
    if (
        msg_dict.get("is_moderator")
        or msg_dict.get("is_broadcaster")
        or msg_dict.get("is_chat_owner")
    ):
        return True
    badges = msg_dict.get("badges")
    if isinstance(badges, str):
        return "moderator/" in badges or "broadcaster/" in badges
    return False


def keyword_match_span(message: str, keyword: str) -> Optional[Tuple[int, int]]:
    """Return the match span when ``keyword`` is its own word, else None.

    ``#yes`` matches inside ``I vote #yes`` and ``#yes!``. It does not match
    ``#yesterday``. Comparison is case-insensitive.
    """
    kw = str(keyword or "").strip()
    if not kw or not message:
        return None
    pattern = re.compile(
        r"(?<![\w])" + re.escape(kw) + r"(?![\w])",
        re.IGNORECASE,
    )
    found = pattern.search(message)
    if not found:
        return None
    return found.span()


def earliest_category(message: str, categories: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pick the category whose keyword appears first in ``message``."""
    best: Optional[Dict[str, Any]] = None
    best_at: Optional[int] = None
    for cat in categories:
        if not isinstance(cat, dict):
            continue
        span = keyword_match_span(message, str(cat.get("keyword") or ""))
        if span is None:
            continue
        if best_at is None or span[0] < best_at:
            best = cat
            best_at = span[0]
    return best


def voter_identity(msg_dict: Dict[str, Any]) -> str:
    """Stable ``platform:id`` key. Twitch is the default when platform is omitted."""
    platform = str(msg_dict.get("platform") or msg_dict.get("source") or "").strip().lower()
    if platform not in ("twitch", "youtube"):
        platform = "twitch"
    user_id = str(msg_dict.get("userid") or msg_dict.get("user_id") or "").strip()
    username = str(msg_dict.get("username") or "").strip().lower()
    ident = user_id or username
    if not ident:
        return ""
    return f"{platform}:{ident}"


def _normalize_last_winners(raw: Any) -> List[Dict[str, Any]]:
    """Keep the stored winning options from the last poll that had a winner."""
    winners: List[Dict[str, Any]] = []
    if not isinstance(raw, list):
        return winners
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        try:
            votes = max(0, int(item.get("votes") or 0))
        except (TypeError, ValueError):
            votes = 0
        winners.append(
            {
                "id": str(item.get("id") or "").strip(),
                "name": name,
                "keyword": str(item.get("keyword") or "").strip(),
                "votes": votes,
            }
        )
    return winners


def _idle_runtime() -> Dict[str, Any]:
    return {
        "status": "idle",
        "started_at": None,
        "ends_at": None,
        "ended_at": None,
        "votes": {},
        "voters": {},
        "winners": [],
        "twitch_poll_id": None,
        "twitch_choices": {},
    }


def _new_category(name: str, keyword: str = "") -> Dict[str, Any]:
    clean_name = str(name or "").strip() or "Option"
    return {
        "id": uuid.uuid4().hex[:12],
        "name": clean_name,
        "keyword": normalize_keyword(keyword, clean_name),
        "votes": 0,
    }


def _walk_elements(elements: Any) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    if not isinstance(elements, list):
        return found
    for el in elements:
        if not isinstance(el, dict):
            continue
        found.append(el)
        found.extend(_walk_elements(el.get("children")))
    return found


def _poll_elements(model: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(model, dict):
        return []
    return [
        el
        for el in _walk_elements(model.get("elements"))
        if str(el.get("type") or "").lower() == "poll"
    ]


def list_poll_template_names() -> List[str]:
    """Template names whose Spore sidecar contains a Poll widget."""
    try:
        from .path_utils import get_template_path
        from .spore_studio.template_parser_back import load_sidecar
    except Exception as exc:
        logger.debug("poll template scan import failed: %s", exc)
        return []
    folder = get_template_path("_spore")
    names: List[str] = []
    try:
        entries = os.listdir(folder) if os.path.isdir(folder) else []
    except OSError as exc:
        logger.debug("poll template folder unreadable: %s", exc)
        return []
    for filename in entries:
        if not filename.endswith(".spore.json"):
            continue
        stem = filename[: -len(".spore.json")]
        if not stem or stem.startswith("_"):
            continue
        model = load_sidecar(stem)
        if _poll_elements(model):
            names.append(stem)
    names.sort()
    return names


def format_banner_message(template_text: str, winners: List[Dict[str, Any]]) -> str:
    """The winner message: {option}/{options} names, {keyword} keywords, {votes} the winning count."""
    if not winners:
        return str(template_text or DEFAULT_BANNER_MESSAGE).strip()
    names = ", ".join(str(item.get("name") or "") for item in winners)
    keywords = ", ".join(str(item.get("keyword") or "") for item in winners)
    votes = str(int(winners[0].get("votes") or 0))
    text = str(template_text or DEFAULT_BANNER_MESSAGE)
    text = text.replace("{options}", names)
    text = text.replace("{option}", names if len(winners) != 1 else str(winners[0].get("name") or ""))
    text = text.replace("{keyword}", keywords if len(winners) != 1 else str(winners[0].get("keyword") or ""))
    text = text.replace("{votes}", votes)
    return text.strip()


_WIDGET_STYLE_DEFAULTS = {
    "background_color": "rgba(15,23,42,0.85)",
    "bar_color": "#a855f7",
    "track_color": "rgba(255,255,255,0.15)",
    "winner_color": "#facc15",
    "color": "#ffffff",
    "show_counts": True,
    "show_percent": True,
    "winner_mode": "highlight",
    "style": "bars",
    "title": "",
    "bar_color_2": "",
    "show_keywords": True,
}


def poll_widget_info(template: str) -> Dict[str, Any]:
    """The template's first Poll widget: its id, how many there are, and its colours/options."""
    try:
        from .spore_studio.template_parser_back import load_sidecar

        widgets = _poll_elements(load_sidecar(str(template or "")))
    except Exception as exc:
        logger.debug("poll widget info for %s failed: %s", template, exc)
        widgets = []
    props = dict(_WIDGET_STYLE_DEFAULTS)
    first = widgets[0] if widgets else {}
    for key in props:
        value = (first.get("props") or {}).get(key)
        if value not in (None, ""):
            props[key] = value
    return {"id": str(first.get("id") or ""), "count": len(widgets), "style": props}


_poll_manager: Optional["PollManager"] = None
_poll_lock = threading.Lock()


def _notify_poll_problem(title: str, message: str, hint: str, details: str, key: str,
                         *, critical: bool = False) -> None:
    """Deduplicated poll problem (background paths; never raises)."""
    try:
        from .notification_engine import nav_chatbot, notify

        notify(
            message,
            type="error" if critical else "warning",
            source="chatbot",
            title=title,
            hint=hint,
            details=details,
            actions=nav_chatbot("Polls"),
            dedupe_key=key,
            dedupe_cooldown_sec=600,
        )
    except Exception:
        logger.debug("Could not show a poll notification", exc_info=True)


def _register_shutdown_flush(name: str, func) -> None:
    try:
        from .shutdown import register_shutdown_flush
    except ImportError:
        return
    register_shutdown_flush(name, func)


def get_poll_manager() -> "PollManager":
    global _poll_manager
    with _poll_lock:
        if _poll_manager is None:
            _poll_manager = PollManager()
        return _poll_manager


class PollManager:
    """Thread-safe poll state for every template that hosts a Poll widget."""

    def __init__(self, *, persist: bool = True):
        self._lock = threading.RLock()
        self._persist_enabled = persist
        self._templates: Dict[str, Dict[str, Any]] = {}
        self._timers: Dict[str, threading.Timer] = {}
        self._generations: Dict[str, int] = {}
        self._last_error: Dict[str, str] = {}
        # Votes persist through one writer thread so chat never waits on the DB.
        # Writes are ordered by sequence, so an older payload never lands last.
        self._write_lock = threading.Lock()
        self._payload_seq = 0
        self._written_seq = 0
        self._vote_dirty = False
        self._vote_wake = threading.Event()
        self._vote_writer: Optional[threading.Thread] = None
        # Set when the stored polls could not be read (a failed read looks like
        # no polls). The path is re-read before any write; see _recover_locked.
        self._load_failed = False
        self._failed_load_baselines: Dict[str, Dict[str, Any]] = {}
        if persist:
            self._load()
            _register_shutdown_flush("poll_votes", self.flush)

    def _now(self) -> float:
        return time.time()

    def _load(self) -> None:
        try:
            from .database_manager import get_data_checked

            raw, ok = get_data_checked(DATA_PATH)
        except Exception as exc:
            logger.warning("Poll config load failed: %s", exc)
            raw, ok = None, False
        if not ok:
            self._load_failed = True
            logger.warning(
                "%s could not be read; it will be re-read before it is saved", DATA_PATH
            )
        with self._lock:
            self._apply_stored(raw if isinstance(raw, dict) else {}, self._now())

    def _apply_stored(self, raw: Dict[str, Any], now: float, *, schedule: bool = True) -> None:
        """Restore configs and still-open polls. Drops polls that already expired."""
        restored, pending_timers = self._parse_stored(raw, now, schedule=schedule)
        self._templates = restored
        if schedule:
            for template, delay in pending_timers:
                self._schedule_locked(template, delay)

    def _parse_stored(
        self, raw: Dict[str, Any], now: float, *, schedule: bool = True
    ) -> Tuple[Dict[str, Dict[str, Any]], List[Tuple[str, float]]]:
        templates = raw.get("templates") if isinstance(raw, dict) else None
        if not isinstance(templates, dict):
            templates = {}
        restored: Dict[str, Dict[str, Any]] = {}
        pending_timers: List[Tuple[str, float]] = []
        for name, entry in templates.items():
            template = str(name or "").strip()
            if not template or not isinstance(entry, dict):
                continue
            config = self._normalize_config(entry.get("config"))
            runtime = _idle_runtime()
            stored_runtime = entry.get("runtime")
            if isinstance(stored_runtime, dict):
                runtime = self._normalize_runtime(stored_runtime, config)
                if runtime.get("status") == "open":
                    if self._is_expired(config, runtime, now):
                        runtime = _idle_runtime()
                    elif schedule and runtime.get("ends_at"):
                        delay = float(runtime["ends_at"]) - now
                        if delay > 0:
                            pending_timers.append((template, delay))
            restored[template] = {"config": config, "runtime": runtime}
        return restored, pending_timers

    def _recover_locked(self) -> bool:
        """Re-read the stored polls after a failed load. Caller holds ``_lock``.

        On success the stored templates are restored, except ones changed in
        memory since (those keep the in-memory state). Returns False while the
        path still cannot be read; callers must not write then.
        """
        try:
            from .database_manager import get_data_checked

            raw, ok = get_data_checked(DATA_PATH)
        except Exception as exc:
            logger.debug("Poll re-read failed: %s", exc)
            ok = False
        if not ok:
            logger.warning("Not saving %s: it could not be read from the database", DATA_PATH)
            _notify_poll_problem(
                "Poll changes not saved yet",
                "Your poll settings and votes are kept and saved once the database can be read.",
                "Check Settings → Database. Don't close Mycelian until this clears.",
                f"Not saving {DATA_PATH}: it could not be read",
                "poll:save_blocked",
            )
            return False
        restored, pending_timers = self._parse_stored(
            raw if isinstance(raw, dict) else {}, self._now()
        )
        delays = dict(pending_timers)
        for name, entry in restored.items():
            current = self._templates.get(name)
            baseline = self._failed_load_baselines.get(name)
            if current is None or (baseline is not None and current == baseline):
                self._templates[name] = entry
                if name in delays:
                    self._schedule_locked(name, delays[name])
        self._load_failed = False
        self._failed_load_baselines.clear()
        logger.info("Re-read %s after a failed load; local changes kept", DATA_PATH)
        return True

    def _is_expired(self, config: Dict[str, Any], runtime: Dict[str, Any], now: float) -> bool:
        if int(config.get("duration_seconds") or 0) <= 0:
            return False
        ends_at = runtime.get("ends_at")
        if ends_at in (None, ""):
            return True
        try:
            return float(ends_at) <= now
        except (TypeError, ValueError):
            return True

    def _normalize_config(self, raw: Any) -> Dict[str, Any]:
        cfg = dict(DEFAULT_CONFIG)
        if isinstance(raw, dict):
            cfg.update(raw)
        try:
            duration = int(float(cfg.get("duration_seconds") or 0))
        except (TypeError, ValueError):
            duration = 0
        cfg["duration_seconds"] = max(0, min(86400 * 7, duration))
        cfg["one_entry_per_user"] = bool(cfg.get("one_entry_per_user", True))
        cfg["allow_vote_switch"] = bool(cfg.get("allow_vote_switch", False))
        cfg.pop("store_data", None)
        cfg["mods_can_start"] = bool(cfg.get("mods_can_start", False))
        cfg["start_command"] = normalize_command(cfg.get("start_command") or "")
        cfg["announce_winners_as_banner"] = bool(cfg.get("announce_winners_as_banner", False))
        cfg["announce_winners_in_chat"] = bool(cfg.get("announce_winners_in_chat", False))
        message = str(cfg.get("banner_message") or "").strip()
        cfg["banner_message"] = message or DEFAULT_BANNER_MESSAGE
        try:
            banner_duration = float(cfg.get("banner_duration") if cfg.get("banner_duration") is not None else 10)
        except (TypeError, ValueError):
            banner_duration = 10
        cfg["banner_duration"] = max(0.0, min(86400.0, banner_duration))
        cfg["last_winners"] = _normalize_last_winners(cfg.get("last_winners"))
        from . import twitch_polls

        cfg["twitch_poll"] = bool(cfg.get("twitch_poll", False))
        cfg["twitch_title"] = str(cfg.get("twitch_title") or "").strip()[: twitch_polls.TITLE_MAX]
        cfg["twitch_points_enabled"] = bool(cfg.get("twitch_points_enabled", False))
        cfg["twitch_points_per_vote"] = twitch_polls.clamp_points(cfg.get("twitch_points_per_vote", 100))
        if cfg["twitch_poll"]:
            # Twitch polls always run on a timer of 15 seconds to 30 minutes.
            cfg["duration_seconds"] = twitch_polls.clamp_duration(cfg["duration_seconds"])
        categories: List[Dict[str, Any]] = []
        seen_ids = set()
        for item in cfg.get("categories") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            cat_id = str(item.get("id") or "").strip() or uuid.uuid4().hex[:12]
            if cat_id in seen_ids:
                cat_id = uuid.uuid4().hex[:12]
            seen_ids.add(cat_id)
            try:
                stored_votes = max(0, int(item.get("votes") or 0))
            except (TypeError, ValueError):
                stored_votes = 0
            categories.append(
                {
                    "id": cat_id,
                    "name": name,
                    "keyword": normalize_keyword(item.get("keyword"), name),
                    "votes": stored_votes,
                }
            )
        cfg["categories"] = categories
        return cfg

    def _normalize_runtime(self, raw: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        runtime = _idle_runtime()
        status = str(raw.get("status") or "idle")
        if status not in ("idle", "open", "ended"):
            status = "idle"
        runtime["status"] = status
        for key in ("started_at", "ends_at", "ended_at"):
            value = raw.get(key)
            if value in (None, ""):
                runtime[key] = None
            else:
                try:
                    runtime[key] = float(value)
                except (TypeError, ValueError):
                    runtime[key] = None
        if int(config.get("duration_seconds") or 0) <= 0:
            runtime["ends_at"] = None
        ids = [c["id"] for c in config.get("categories") or []]
        votes_raw = raw.get("votes") if isinstance(raw.get("votes"), dict) else {}
        runtime["votes"] = {}
        for cat_id in ids:
            try:
                runtime["votes"][cat_id] = max(0, int(votes_raw.get(cat_id) or 0))
            except (TypeError, ValueError):
                runtime["votes"][cat_id] = 0
        voters_raw = raw.get("voters") if isinstance(raw.get("voters"), dict) else {}
        voters: Dict[str, str] = {}
        valid = set(ids)
        for key, cat_id in voters_raw.items():
            ident = str(key or "").strip()
            chosen = str(cat_id or "").strip()
            if ident and chosen in valid:
                voters[ident] = chosen
        runtime["voters"] = voters
        winners_raw = raw.get("winners") if isinstance(raw.get("winners"), list) else []
        runtime["winners"] = [str(item) for item in winners_raw if str(item) in valid]
        poll_id = str(raw.get("twitch_poll_id") or "").strip()
        runtime["twitch_poll_id"] = poll_id or None
        choices_raw = raw.get("twitch_choices") if isinstance(raw.get("twitch_choices"), dict) else {}
        runtime["twitch_choices"] = {str(k): str(v) for k, v in choices_raw.items() if str(v) in valid} if poll_id else {}
        return runtime

    def _persist(self) -> None:
        """Write the current state now. Caller holds ``self._lock``."""
        if not self._persist_enabled:
            return
        if self._load_failed and not self._recover_locked():
            # Keep the change pending; the next write or the shutdown flush retries.
            self._vote_dirty = True
            return
        self._vote_dirty = False
        seq, payload = self._build_payload_locked()
        self._write_payload(seq, payload)

    def _build_payload_locked(self) -> Tuple[int, Dict[str, Any]]:
        payload = {"templates": {}}
        for name, entry in self._templates.items():
            config = self._normalize_config(entry.get("config"))
            stored: Dict[str, Any] = {"config": config}
            runtime = entry.get("runtime") or _idle_runtime()
            if runtime.get("status") == "open":
                stored["runtime"] = {
                    "status": "open",
                    "started_at": runtime.get("started_at"),
                    "ends_at": runtime.get("ends_at"),
                    "votes": dict(runtime.get("votes") or {}),
                    "voters": dict(runtime.get("voters") or {}),
                    "winners": [],
                    "twitch_poll_id": runtime.get("twitch_poll_id"),
                    "twitch_choices": dict(runtime.get("twitch_choices") or {}),
                }
            payload["templates"][name] = stored
        self._payload_seq += 1
        return self._payload_seq, payload

    def _write_payload(self, seq: int, payload: Dict[str, Any]) -> None:
        with self._write_lock:
            if seq <= self._written_seq:
                return
            self._written_seq = seq
            error = ""
            try:
                from .database_manager import last_write_problem, quiet_notifications, set_data

                # Reported below with poll context, not by the database layer.
                with quiet_notifications():
                    ok = set_data(DATA_PATH, payload)
                    problem = last_write_problem()
                if ok is False:
                    reason = ": ".join(str(p) for p in problem if p) if problem else ""
                    error = f"Database write returned no success for {DATA_PATH}" + (f" ({reason})" if reason else "")
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            if error:
                logger.error("Poll config save failed: %s", error)
                _notify_poll_problem(
                    "Couldn't save polls",
                    "Your latest poll settings or votes may be lost when Mycelian closes.",
                    "Check Settings → Database, then change the poll again.",
                    error,
                    "poll:save_failed",
                    critical=True,
                )

    def _schedule_vote_persist_locked(self) -> None:
        if not self._persist_enabled:
            return
        self._vote_dirty = True
        writer = self._vote_writer
        if writer is None or not writer.is_alive():
            writer = threading.Thread(
                target=self._vote_writer_loop, name="PollVoteWriter", daemon=True
            )
            self._vote_writer = writer
            writer.start()
        self._vote_wake.set()

    def _vote_writer_loop(self) -> None:
        while True:
            self._vote_wake.wait()
            time.sleep(_VOTE_WRITE_COALESCE_SECONDS)
            self._vote_wake.clear()
            try:
                self._write_pending_votes()
            except Exception as exc:
                logger.error("Poll vote save failed: %s", exc)

    def _write_pending_votes(self) -> None:
        with self._lock:
            if not self._vote_dirty:
                return
            if self._load_failed and not self._recover_locked():
                return
            self._vote_dirty = False
            seq, payload = self._build_payload_locked()
        self._write_payload(seq, payload)

    def flush(self) -> None:
        """Write any pending vote state and wait for an in-flight write."""
        if not self._persist_enabled:
            return
        self._write_pending_votes()
        with self._write_lock:
            pass

    def _cancel_timer(self, template: str) -> None:
        timer = self._timers.pop(template, None)
        if timer is not None:
            timer.cancel()

    def _schedule_locked(self, template: str, delay: float) -> None:
        self._cancel_timer(template)
        if delay <= 0:
            return
        generation = int(self._generations.get(template) or 0)
        timer = threading.Timer(delay, self._timer_fired, args=(template, generation))
        timer.daemon = True
        self._timers[template] = timer
        timer.start()

    def _timer_fired(self, template: str, generation: int) -> None:
        # A Twitch poll already ended on Twitch's own timer; this only closes it here.
        self.stop_poll(template, generation=generation, end_on_twitch=False)

    def shutdown(self) -> None:
        with self._lock:
            for template in list(self._timers):
                self._cancel_timer(template)

    def ensure_template(self, template: str) -> Dict[str, Any]:
        name = str(template or "").strip()
        if not name:
            return copy.deepcopy(DEFAULT_CONFIG)
        with self._lock:
            entry = self._templates.get(name)
            if entry is None:
                config = self._normalize_config({})
                self._templates[name] = {"config": config, "runtime": _idle_runtime()}
                if self._load_failed:
                    self._failed_load_baselines[name] = copy.deepcopy(self._templates[name])
                self._persist()
            return copy.deepcopy(self._templates[name]["config"])

    def get_config(self, template: str) -> Dict[str, Any]:
        name = str(template or "").strip()
        with self._lock:
            entry = self._templates.get(name)
            if not entry:
                return copy.deepcopy(DEFAULT_CONFIG)
            return copy.deepcopy(entry["config"])

    def get_last_error(self, template: str) -> str:
        with self._lock:
            return self._last_error.get(str(template or "").strip(), "")

    def update_config(self, template: str, updates: Dict[str, Any]) -> None:
        name = str(template or "").strip()
        if not name:
            return
        with self._lock:
            self.ensure_template(name)
            config = self._templates[name]["config"]
            for key, value in (updates or {}).items():
                if key == "categories":
                    continue
                if key in DEFAULT_CONFIG:
                    config[key] = value
            self._templates[name]["config"] = self._normalize_config(config)
            self._persist()

    def _categories_locked(self, template: str) -> Tuple[bool, str]:
        runtime = (self._templates.get(template) or {}).get("runtime") or {}
        if runtime.get("status") == "open":
            return False, "End the poll before changing its options."
        return True, ""

    def add_category(self, template: str, name: str = "") -> Tuple[bool, str]:
        template = str(template or "").strip()
        with self._lock:
            self.ensure_template(template)
            ok, msg = self._categories_locked(template)
            if not ok:
                return False, msg
            config = self._templates[template]["config"]
            index = len(config["categories"]) + 1
            label = str(name or "").strip() or f"Option {index}"
            category = _new_category(label)
            existing = {cat["keyword"].casefold() for cat in config["categories"]}
            if category["keyword"].casefold() in existing:
                suffix = 2
                while f"{category['keyword']}{suffix}".casefold() in existing:
                    suffix += 1
                category["keyword"] = f"{category['keyword']}{suffix}"
            config["categories"].append(category)
            self._templates[template]["config"] = self._normalize_config(config)
            self._persist()
        return True, ""

    def update_category(
        self,
        template: str,
        category_id: str,
        *,
        name: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> Tuple[bool, str]:
        template = str(template or "").strip()
        category_id = str(category_id or "").strip()
        with self._lock:
            self.ensure_template(template)
            ok, msg = self._categories_locked(template)
            if not ok:
                return False, msg
            config = self._templates[template]["config"]
            target = None
            for cat in config["categories"]:
                if cat["id"] == category_id:
                    target = cat
                    break
            if target is None:
                return False, "Option not found."
            proposed_name = target["name"] if name is None else str(name or "").strip()
            if not proposed_name:
                return False, "Option name cannot be empty."
            keyword_source = target["keyword"] if keyword is None else keyword
            proposed_keyword = normalize_keyword(keyword_source, proposed_name)
            if not proposed_keyword:
                return False, "Keyword cannot be empty."
            folded = proposed_keyword.casefold()
            for cat in config["categories"]:
                if cat["id"] != category_id and cat["keyword"].casefold() == folded:
                    return False, f"{proposed_keyword} is already used by another option."
            target["name"] = proposed_name
            target["keyword"] = proposed_keyword
            self._persist()
        return True, ""

    def remove_category(self, template: str, category_id: str) -> Tuple[bool, str]:
        template = str(template or "").strip()
        category_id = str(category_id or "").strip()
        with self._lock:
            self.ensure_template(template)
            ok, msg = self._categories_locked(template)
            if not ok:
                return False, msg
            config = self._templates[template]["config"]
            before = len(config["categories"])
            config["categories"] = [c for c in config["categories"] if c["id"] != category_id]
            if len(config["categories"]) == before:
                return False, "Option not found."
            self._persist()
        return True, ""

    def move_category(self, template: str, category_id: str, delta: int) -> Tuple[bool, str]:
        template = str(template or "").strip()
        category_id = str(category_id or "").strip()
        with self._lock:
            self.ensure_template(template)
            ok, msg = self._categories_locked(template)
            if not ok:
                return False, msg
            cats = self._templates[template]["config"]["categories"]
            index = next((i for i, cat in enumerate(cats) if cat["id"] == category_id), -1)
            if index < 0:
                return False, "Option not found."
            target = index + int(delta)
            if target < 0 or target >= len(cats):
                return True, ""
            cats[index], cats[target] = cats[target], cats[index]
            self._persist()
        return True, ""

    def set_category_votes(self, template: str, category_id: str, count: int) -> Tuple[bool, str]:
        """Set a category's vote total while the poll is not running."""
        template = str(template or "").strip()
        category_id = str(category_id or "").strip()
        try:
            count = max(0, int(count))
        except (TypeError, ValueError):
            count = 0
        snapshot = None
        with self._lock:
            self.ensure_template(template)
            entry = self._templates[template]
            runtime = entry.get("runtime") or _idle_runtime()
            if runtime.get("status") == "open":
                return False, "End the poll before editing votes."
            config = entry["config"]
            target = next((cat for cat in config.get("categories") or [] if cat["id"] == category_id), None)
            if target is None:
                return False, "Option not found."
            target["votes"] = count
            votes = runtime.setdefault("votes", {})
            votes[category_id] = count
            if runtime.get("status") == "ended":
                winners = self._pick_winners(config, runtime)
                runtime["winners"] = [item["id"] for item in winners]
                self._remember_winners(config, winners)
                snapshot = self._snapshot_locked(template, self._now())
            self._persist()
        if snapshot:
            self._emit("poll_state", snapshot)
        return True, ""

    def _start_problem_locked(self, template: str, config: Dict[str, Any]) -> str:
        categories = config.get("categories") or []
        if len(categories) < 2:
            return "Add at least two options before starting."
        seen = set()
        for cat in categories:
            if not cat.get("keyword"):
                return f"{cat.get('name') or 'An option'} needs a keyword."
            folded = cat["keyword"].casefold()
            if folded in seen:
                return f"{cat['keyword']} is used more than once."
            seen.add(folded)
        if config.get("twitch_poll"):
            from . import twitch_polls

            other = self._twitch_poll_template_locked(exclude=template)
            if other:
                return ONE_TWITCH_POLL_MESSAGE.format(template=other)
            problems = twitch_polls.config_problems(config)
            if problems:
                return problems[0]
        return ""

    def _twitch_poll_template_locked(self, exclude: str = "") -> str:
        """The overlay whose poll is running on Twitch right now ("" when none)."""
        for name, entry in self._templates.items():
            runtime = entry.get("runtime") or {}
            if name != exclude and runtime.get("status") == "open" and runtime.get("twitch_poll_id"):
                return name
        return ""

    def twitch_poll_running(self, exclude: str = "") -> str:
        with self._lock:
            return self._twitch_poll_template_locked(exclude)

    def start_poll(self, template: str) -> Tuple[bool, str]:
        """Start the poll. With "Also run it on Twitch", the Twitch poll is created first (this waits for Twitch)."""
        template = str(template or "").strip()
        with self._lock:
            self.ensure_template(template)
            problem = self._start_problem_locked(template, self._templates[template]["config"])
            if problem:
                return False, problem
            wanted = copy.deepcopy(self._templates[template]["config"])
        twitch_poll: Optional[Dict[str, Any]] = None
        if wanted.get("twitch_poll"):
            from . import twitch_polls

            status = twitch_polls.scope_status()
            if status != "ok":
                return False, twitch_polls.readiness_message(status)
            ok, result = twitch_polls.create_poll(
                wanted["twitch_title"],
                [cat["name"] for cat in wanted["categories"]],
                int(wanted["duration_seconds"]),
                points_enabled=bool(wanted.get("twitch_points_enabled")),
                points_per_vote=int(wanted.get("twitch_points_per_vote") or 0),
            )
            if not ok:
                return False, str(result)
            twitch_poll = result
        with self._lock:
            config = self._templates[template]["config"]
            categories = config.get("categories") or []
            self._cancel_timer(template)
            self._generations[template] = int(self._generations.get(template) or 0) + 1
            now = self._now()
            duration = int(config.get("duration_seconds") or 0)
            runtime = _idle_runtime()
            runtime["status"] = "open"
            runtime["started_at"] = now
            runtime["ends_at"] = (now + duration) if duration > 0 else None
            runtime["votes"] = {cat["id"]: 0 for cat in categories}
            for cat in categories:
                cat["votes"] = 0
            if twitch_poll:
                # Twitch returns the choices in the order they were sent.
                runtime["twitch_poll_id"] = str(twitch_poll.get("id"))
                runtime["twitch_choices"] = {
                    str(choice.get("id")): cat["id"]
                    for choice, cat in zip(twitch_poll.get("choices") or [], categories)
                    if isinstance(choice, dict) and choice.get("id")
                }
            self._templates[template]["runtime"] = runtime
            self._last_error[template] = ""
            if duration > 0:
                self._schedule_locked(template, float(duration) + (_TWITCH_END_GRACE_SECONDS if twitch_poll else 0.0))
            snapshot = self._snapshot_locked(template, now)
            self._persist()
        self._emit("poll_state", snapshot)
        return True, "Poll started on Twitch too." if twitch_poll else "Poll started."

    def stop_poll(self, template: str, *, generation: Optional[int] = None, end_on_twitch: bool = True) -> Tuple[bool, str]:
        """End the poll. ``end_on_twitch`` also ends its Twitch poll (when you end it early)."""
        template = str(template or "").strip()
        twitch_id = None
        with self._lock:
            if generation is not None and int(self._generations.get(template) or 0) != int(generation):
                return False, "Poll is not running."
            entry = self._templates.get(template)
            if not entry or (entry.get("runtime") or {}).get("status") != "open":
                return False, "Poll is not running."
            self._cancel_timer(template)
            self._generations[template] = int(self._generations.get(template) or 0) + 1
            now = self._now()
            config = entry["config"]
            runtime = entry["runtime"]
            winners = self._pick_winners(config, runtime)
            runtime["status"] = "ended"
            runtime["ended_at"] = now
            runtime["winners"] = [item["id"] for item in winners]
            self._remember_winners(config, winners)
            for cat in config.get("categories") or []:
                cat["votes"] = int((runtime.get("votes") or {}).get(cat["id"]) or 0)
            snapshot = self._snapshot_locked(template, now)
            self._persist()
            twitch_id = runtime.get("twitch_poll_id")
        if twitch_id and end_on_twitch:
            from . import twitch_polls

            twitch_polls.end_poll(twitch_id)
        self._emit("poll_state", snapshot)
        self._emit("poll_ended", snapshot)
        self._announce(template, snapshot)
        self._record_stats(snapshot)
        if winners:
            names = ", ".join(item["name"] for item in winners)
            return True, f"Winner: {names}"
        return True, "Poll ended with no votes."

    def _pick_winners(self, config: Dict[str, Any], runtime: Dict[str, Any]) -> List[Dict[str, Any]]:
        votes = runtime.get("votes") or {}
        best = 0
        for cat in config.get("categories") or []:
            count = int(votes.get(cat["id"]) or 0)
            if count > best:
                best = count
        if best <= 0:
            return []
        winners = []
        for cat in config.get("categories") or []:
            count = int(votes.get(cat["id"]) or 0)
            if count == best:
                winners.append(
                    {
                        "id": cat["id"],
                        "name": cat["name"],
                        "keyword": cat["keyword"],
                        "votes": count,
                    }
                )
        return winners

    def _remember_winners(self, config: Dict[str, Any], winners: List[Dict[str, Any]]) -> None:
        """Keep the latest real result. A poll with no votes leaves the previous one."""
        stored = _normalize_last_winners(winners)
        if stored:
            config["last_winners"] = stored

    def try_register_vote(self, msg_dict: Dict[str, Any]) -> bool:
        message = str((msg_dict or {}).get("message") or "")
        if "#" not in message:
            return False
        ident = voter_identity(msg_dict)
        accepted = False
        snapshots: List[Dict[str, Any]] = []
        with self._lock:
            for template, entry in self._templates.items():
                runtime = entry.get("runtime") or {}
                if runtime.get("status") != "open" or runtime.get("twitch_poll_id"):
                    # Twitch polls count Twitch votes only, so both show the same numbers.
                    continue
                config = entry.get("config") or {}
                chosen = earliest_category(message, config.get("categories") or [])
                if not chosen:
                    continue
                if self._apply_vote_locked(config, runtime, ident, chosen["id"]):
                    accepted = True
                    snapshots.append(self._snapshot_locked(template, self._now()))
            if accepted:
                self._schedule_vote_persist_locked()
        for snapshot in snapshots:
            self._emit("poll_state", snapshot)
        return accepted

    def try_moderator_start(self, msg_dict: Dict[str, Any]) -> bool:
        """Start polls whose command is at the beginning of a moderator message."""
        if not sender_can_start_poll(msg_dict):
            return False
        message = str((msg_dict or {}).get("message") or "")
        if not message.strip().startswith("!"):
            return False
        targets: List[str] = []
        with self._lock:
            for template, entry in self._templates.items():
                config = entry.get("config") or {}
                if not config.get("mods_can_start"):
                    continue
                command = str(config.get("start_command") or "").strip()
                if not command or not message_starts_with_command(message, command):
                    continue
                runtime = entry.get("runtime") or {}
                if runtime.get("status") == "open":
                    continue
                targets.append(template)
        started = False
        for template in targets:
            if self.get_config(template).get("twitch_poll"):
                # Creating the Twitch poll waits on Twitch; don't hold up chat for it.
                threading.Thread(target=self._start_from_chat, args=(template,), name="PollChatStart", daemon=True).start()
                started = True
                continue
            started = self._start_from_chat(template) or started
        return started

    def _start_from_chat(self, template: str) -> bool:
        ok, message = self.start_poll(template)
        if not ok:
            _notify_poll_problem("Couldn't start the poll from chat", message, "Fix it on Chatbot → Polls, then try again.",
                                 f"{template}: {message}", f"poll:chat_start:{template}")
        return ok

    # -- Twitch poll updates (EventSub) -------------------------------------------

    def _template_for_twitch_poll_locked(self, poll_id: str) -> str:
        for name, entry in self._templates.items():
            if poll_id and (entry.get("runtime") or {}).get("twitch_poll_id") == poll_id:
                return name
        return ""

    def _set_twitch_votes_locked(self, runtime: Dict[str, Any], votes: Dict[str, int]) -> bool:
        mapping = runtime.get("twitch_choices") or {}
        changed = False
        counts = runtime.setdefault("votes", {})
        for choice_id, count in (votes or {}).items():
            cat_id = mapping.get(str(choice_id))
            if cat_id and int(counts.get(cat_id) or 0) != int(count):
                counts[cat_id] = int(count)
                changed = True
        return changed

    def twitch_poll_began(self, poll_id: str, ends_at: Optional[float]) -> bool:
        """Twitch started the poll (channel.poll.begin): use its end time for the countdown."""
        snapshot = None
        with self._lock:
            template = self._template_for_twitch_poll_locked(poll_id)
            runtime = (self._templates.get(template) or {}).get("runtime") or {}
            if not template or runtime.get("status") != "open" or not ends_at:
                return False
            runtime["ends_at"] = float(ends_at)
            self._schedule_locked(template, float(ends_at) - self._now() + _TWITCH_END_GRACE_SECONDS)
            snapshot = self._snapshot_locked(template, self._now())
            self._persist()
        self._emit("poll_state", snapshot)
        return True

    def apply_twitch_votes(self, poll_id: str, votes: Dict[str, int]) -> bool:
        """Live counts from Twitch (channel.poll.progress) for the poll it belongs to."""
        snapshot = None
        with self._lock:
            template = self._template_for_twitch_poll_locked(poll_id)
            runtime = (self._templates.get(template) or {}).get("runtime") or {}
            if not template or runtime.get("status") != "open":
                return False
            if self._set_twitch_votes_locked(runtime, votes):
                snapshot = self._snapshot_locked(template, self._now())
                self._schedule_vote_persist_locked()
        if snapshot:
            self._emit("poll_state", snapshot)
        return snapshot is not None

    def twitch_poll_ended(self, poll_id: str, votes: Dict[str, int], status: str = "") -> bool:
        """Twitch ended the poll (channel.poll.end): close it here with Twitch's final counts."""
        snapshot = None
        with self._lock:
            template = self._template_for_twitch_poll_locked(poll_id)
            if not template:
                return False
            entry = self._templates[template]
            runtime = entry.get("runtime") or {}
            self._set_twitch_votes_locked(runtime, votes)
            if runtime.get("status") == "ended":
                # Ended here first (End poll now); keep Twitch's final numbers.
                config = entry["config"]
                winners = self._pick_winners(config, runtime)
                runtime["winners"] = [item["id"] for item in winners]
                self._remember_winners(config, winners)
                for cat in config.get("categories") or []:
                    cat["votes"] = int((runtime.get("votes") or {}).get(cat["id"]) or 0)
                snapshot = self._snapshot_locked(template, self._now())
                self._persist()
        if snapshot is not None:
            self._emit("poll_state", snapshot)
            return True
        logger.info("Twitch poll %s ended (%s)", poll_id, status or "completed")
        ok, _message = self.stop_poll(template, end_on_twitch=False)
        return ok

    def _apply_vote_locked(
        self,
        config: Dict[str, Any],
        runtime: Dict[str, Any],
        ident: str,
        category_id: str,
    ) -> bool:
        votes = runtime.setdefault("votes", {})
        if config.get("one_entry_per_user"):
            if not ident:
                return False
            voters = runtime.setdefault("voters", {})
            previous = voters.get(ident)
            if previous:
                if not config.get("allow_vote_switch"):
                    return False
                if previous == category_id:
                    return False
                if previous in votes and votes[previous] > 0:
                    votes[previous] = int(votes[previous]) - 1
                votes[category_id] = int(votes.get(category_id) or 0) + 1
                voters[ident] = category_id
                return True
            votes[category_id] = int(votes.get(category_id) or 0) + 1
            voters[ident] = category_id
            return True
        votes[category_id] = int(votes.get(category_id) or 0) + 1
        return True

    def get_overlay_state(self, template: str) -> Dict[str, Any]:
        name = str(template or "").strip()
        with self._lock:
            if name not in self._templates:
                return self._empty_snapshot(name)
            return self._snapshot_locked(name, self._now())

    def _empty_snapshot(self, template: str) -> Dict[str, Any]:
        now = self._now()
        return {
            "template": template,
            "status": "idle",
            "permanent": False,
            "duration_seconds": 0,
            "ends_at": None,
            "ended_at": None,
            "server_now": now,
            "remaining_seconds": None,
            "options": [],
            "total_votes": 0,
            "winners": [],
            "twitch_poll": False,
        }

    def _snapshot_locked(self, template: str, now: Optional[float] = None) -> Dict[str, Any]:
        now = self._now() if now is None else now
        entry = self._templates.get(template) or {}
        config = entry.get("config") or self._normalize_config({})
        runtime = entry.get("runtime") or _idle_runtime()
        votes = runtime.get("votes") or {}
        winner_ids = set(runtime.get("winners") or [])
        ranked: List[Tuple[int, int, Dict[str, Any]]] = []
        total = 0
        status = runtime.get("status") or "idle"
        for index, cat in enumerate(config.get("categories") or []):
            if status == "open":
                count = int(votes.get(cat["id"]) or 0)
            elif status == "ended":
                count = int(votes.get(cat["id"]) or cat.get("votes") or 0)
            else:
                count = int(cat.get("votes") or 0)
            total += count
            ranked.append((count, index, cat))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        options = []
        winners = []
        for count, _index, cat in ranked:
            percent = round((count / total) * 100, 1) if total else 0
            is_winner = cat["id"] in winner_ids
            row = {
                "id": cat["id"],
                "name": cat["name"],
                "keyword": cat["keyword"],
                "votes": count,
                "percent": percent,
                "winner": is_winner,
            }
            options.append(row)
            if is_winner:
                winners.append(
                    {
                        "id": cat["id"],
                        "name": cat["name"],
                        "keyword": cat["keyword"],
                        "votes": count,
                    }
                )
        duration = int(config.get("duration_seconds") or 0)
        permanent = duration <= 0
        ends_at = runtime.get("ends_at")
        remaining = None
        if runtime.get("status") == "open" and not permanent and ends_at:
            remaining = max(0, int(round(float(ends_at) - now)))
        return {
            "template": template,
            "status": runtime.get("status") or "idle",
            "permanent": permanent and runtime.get("status") == "open",
            "duration_seconds": duration,
            "ends_at": ends_at if runtime.get("status") == "open" else None,
            "ended_at": runtime.get("ended_at"),
            "server_now": now,
            "remaining_seconds": remaining,
            "options": options,
            "total_votes": total,
            "winners": winners,
            "twitch_poll": bool(runtime.get("twitch_poll_id")) and runtime.get("status") in ("open", "ended"),
        }

    def _announce(self, template: str, snapshot: Dict[str, Any]) -> None:
        winners = snapshot.get("winners") or []
        if not winners:
            return
        config = self.get_config(template)
        message = self._banner_text(config.get("banner_message") or "", winners)
        if not message:
            return
        if config.get("announce_winners_as_banner"):
            try:
                duration = float(config.get("banner_duration") or 0)
            except (TypeError, ValueError):
                duration = 10
            try:
                from .chat_banner import emit_chat_banner

                emit_chat_banner(message, duration=duration, source="poll")
            except Exception as exc:
                logger.error("Poll winner banner failed: %s", exc, exc_info=True)
                _notify_poll_problem(
                    "Couldn't show the poll result banner",
                    f"The winner of “{template}” wasn't shown on the overlay.",
                    "Check that the overlay server is running (see the status footer at the bottom of the window).",
                    f"{type(exc).__name__}: {exc}",
                    "poll:banner_failed",
                )
        if config.get("announce_winners_in_chat"):
            try:
                from .chatbot import dispatch_chatbot_response

                dispatch_chatbot_response(message, ["twitch", "youtube"])
            except Exception as exc:
                logger.error("Poll winner chat failed: %s", exc, exc_info=True)

    def _banner_text(self, template_text: str, winners: List[Dict[str, Any]]) -> str:
        return format_banner_message(template_text, winners)

    def _record_stats(self, snapshot: Dict[str, Any]) -> None:
        if not self._persist_enabled:
            return
        try:
            from .statistics_manager import get_statistics_manager

            get_statistics_manager().record_poll_finished(
                list(snapshot.get("options") or []),
                list(snapshot.get("winners") or []),
            )
        except Exception as exc:
            logger.debug("poll stats failed: %s", exc)

    def _emit(self, event: str, data: Dict[str, Any]) -> None:
        try:
            from . import web_engine

            if web_engine.web_engine_instance:
                web_engine.web_engine_instance.safe_emit(event, data)
        except Exception as exc:
            logger.debug("poll emit %s: %s", event, exc)

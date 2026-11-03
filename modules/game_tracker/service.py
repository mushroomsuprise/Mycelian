# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Games Tracker background service.

Each platform has its own daemon worker thread (``game-tracker-psn``, ``game-tracker-steam``)
and queue. The first import and every library sweep run only there. Everything else
(the PSN presence loop, the Steam poller, the settings tab, the HTTP API) just queues work
with :func:`observe_psn_title`, :func:`observe_steam_game` or :func:`sync_now` and returns
at once. Cover art downloads have their own thread (``covers``).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import covers, psn_sync, steam_sync
from .dispatch import dispatch
from .models import PLATFORMS, PSN, STEAM, GameEvent, TrackedGame, TrackerSettings
from .store import GameTrackerStore, get_store

logger = logging.getLogger(__name__)

START_DELAY_SEC = 8.0  # let the app and the platform clients settle first
RETRY_SEC = 120.0  # after a sync that couldn't reach the platform
SUMMARY_TTL_SEC = 5.0


class GameTrackerService:
    def __init__(
        self,
        store: Optional[GameTrackerStore] = None,
        *,
        psn_client_fn: Optional[Callable[[], Any]] = None,
        steam_client_fn: Optional[Callable[[], Any]] = None,
        start_delay: float = START_DELAY_SEC,
    ):
        self._store = store
        self._psn_client_fn = psn_client_fn or _default_psn_client
        self._steam_client_fn = steam_client_fn or _SteamClientHolder().get
        self._start_delay = start_delay
        self._stop = threading.Event()
        self._queues: Dict[str, "queue.Queue[Tuple[str, Any]]"] = {p: queue.Queue() for p in PLATFORMS}
        self._threads: Dict[str, threading.Thread] = {}
        self._status: Dict[str, Dict[str, Any]] = {p: _blank_status() for p in PLATFORMS}
        self._status_lock = threading.Lock()
        self._listeners: List[Callable[[], None]] = []
        self._summary: Optional[Tuple[float, Dict[str, Any]]] = None
        self._settings: Optional[TrackerSettings] = None

    # ----- lifecycle -----

    @property
    def store(self) -> GameTrackerStore:
        if self._store is None:
            self._store = get_store()
        return self._store

    @property
    def settings(self) -> TrackerSettings:
        if self._settings is None:
            self._settings = self.store.load_settings()
            covers.set_ttl(self._settings.cover_ttl_days, self._settings.cover_missing_hours)
        return self._settings

    def start(self) -> None:
        self._stop.clear()
        for platform in PLATFORMS:
            t = self._threads.get(platform)
            if t is not None and t.is_alive():
                continue
            t = threading.Thread(target=self._run, args=(platform,), name=f"game-tracker-{platform}", daemon=True)
            self._threads[platform] = t
            t.start()

    def stop(self, join_timeout: float = 5.0) -> None:
        self._stop.set()
        for q in self._queues.values():
            q.put(("stop", None))
        for platform, t in list(self._threads.items()):
            t.join(timeout=join_timeout)
            if t.is_alive():
                logger.warning("Games Tracker: %s worker didn't stop within %.1fs", platform, join_timeout)
        self._threads.clear()

    def is_running(self) -> bool:
        return any(t.is_alive() for t in self._threads.values())

    # ----- requests (never block) -----

    def sync_now(self, platform: Optional[str] = None) -> None:
        for p in [platform] if platform else PLATFORMS:
            if p in self._queues:
                self._queues[p].put(("sync", None))

    def observe_psn_title(self, title_result: Dict[str, Any]) -> None:
        if title_result and title_result.get("np_communication_id"):
            self._queues[PSN].put(("observe", dict(title_result)))

    def observe_steam_game(self, appid: Any, name: str, achievements: List[Dict[str, Any]]) -> None:
        if appid:
            self._queues[STEAM].put(("observe", (appid, name, [dict(a) for a in achievements or []])))

    def add_listener(self, fn: Callable[[], None]) -> None:
        """Called (from a worker thread) after the library changes."""
        if fn not in self._listeners:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[], None]) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    # ----- worker -----

    def _run(self, platform: str) -> None:
        q = self._queues[platform]
        if self._stop.wait(self._start_delay):
            return
        next_sync = 0.0
        while not self._stop.is_set():
            try:
                kind, payload = q.get(timeout=max(0.2, min(30.0, next_sync - time.time())))
            except queue.Empty:
                kind, payload = "tick", None
            if self._stop.is_set() or kind == "stop":
                break
            settings = self.settings
            if not settings.platform_enabled(platform):
                self._set_status(platform, state="off", message="Turned off")
                next_sync = time.time() + 60
                continue
            try:
                if kind == "observe":
                    self._observe(platform, payload, settings)
                elif kind == "sync" or time.time() >= next_sync:
                    ok = self._sync(platform, settings)
                    next_sync = time.time() + (settings.sync_minutes * 60 if ok else RETRY_SEC)
            except Exception as e:
                logger.warning("Games Tracker: %s worker error: %s", platform, e, exc_info=True)
                self._set_status(platform, state="error", message=str(e)[:200])
                next_sync = time.time() + RETRY_SEC

    def _sync(self, platform: str, settings: TrackerSettings) -> bool:
        first = self.store.baseline(platform) is None
        self._set_status(platform, state="syncing", done=0, total=0, first_import=first, message="")
        progress = lambda done, total: self._set_status(platform, done=done, total=total)  # noqa: E731
        if platform == PSN:
            client = self._psn_client_fn()
            events = psn_sync.sync_library(client, self.store, settings, progress=progress) if client else None
        else:
            client = self._steam_client_fn()
            events = (
                steam_sync.sync_library(client, self.store, settings, stop_event=self._stop, progress=progress)
                if client
                else None
            )
        if events is None:
            self._set_status(platform, state="waiting", message="Not connected" if client is None else "Couldn't reach the service")
            return False
        dispatch(events, settings, self.store)
        self._set_status(platform, state="idle", last_sync=time.time(), first_import=False, message="")
        self._after_change(platform)
        return True

    def _observe(self, platform: str, payload: Any, settings: TrackerSettings) -> None:
        if self.store.baseline(platform) is None:
            return
        if platform == PSN:
            events = psn_sync.observe_title(self._psn_client_fn(), self.store, settings, payload)
        else:
            appid, name, achievements = payload
            client = self._steam_client_fn()
            account = client.resolve_steam_id() if client else ""
            events = steam_sync.observe_game(self.store, settings, account or "", appid, name, achievements)
        if events:
            dispatch(events, settings, self.store)
        self._after_change(platform)

    def _after_change(self, platform: str) -> None:
        self._summary = None
        games = self.store.list_games(platform)
        items = psn_sync.cover_items(games) if platform == PSN else steam_sync.cover_items(games)
        covers.request_covers(items)
        try:
            from .. import web_engine

            instance = getattr(web_engine, "web_engine_instance", None)
            if instance:
                instance.safe_emit("game_tracker_update", self.summary())
        except Exception:
            logger.debug("Games Tracker: overlay update failed", exc_info=True)
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                logger.debug("Games Tracker: listener failed", exc_info=True)

    # ----- status and summary -----

    def _set_status(self, platform: str, **changes: Any) -> None:
        with self._status_lock:
            self._status[platform].update(changes)

    def status(self) -> Dict[str, Dict[str, Any]]:
        with self._status_lock:
            out = {p: dict(s) for p, s in self._status.items()}
        for p in PLATFORMS:
            out[p]["enabled"] = self.settings.platform_enabled(p)
            out[p]["baselined"] = self.store.baseline(p) is not None
        return out

    def summary(self) -> Dict[str, Any]:
        """Totals over visible games (cached a few seconds; chat placeholders read it)."""
        cached = self._summary
        if cached and time.time() - cached[0] < SUMMARY_TTL_SEC:
            return cached[1]
        result = summarize(self.store.list_games())
        self._summary = (time.time(), result)
        return result

    # ----- edits -----

    def save_settings(self, settings: TrackerSettings) -> TrackerSettings:
        clean = TrackerSettings.from_dict(settings.to_dict())
        self.store.save_settings(clean)
        self._settings = clean
        covers.set_ttl(clean.cover_ttl_days, clean.cover_missing_hours)
        for q in self._queues.values():
            q.put(("tick", None))
        return clean

    def update_game(self, platform: str, game_id: str, **changes: Any) -> Tuple[Optional[TrackedGame], str]:
        if platform == PSN:
            game, error = psn_sync.apply_edit(self.store, game_id, **changes)
        else:
            game = self.store.get_game(STEAM, game_id)
            error = "" if game else "That game isn't in the tracker."
            if game is not None:
                if changes.get("title") is not None:
                    game.title_override = str(changes["title"]).strip()
                if changes.get("hidden") is not None:
                    game.hidden = bool(changes["hidden"])
                self.store.upsert_games([game])
        self._summary = None
        return game, error

    def remove_game(self, platform: str, game_id: str) -> bool:
        removed = psn_sync.remove_game(self.store, game_id) if platform == PSN else self.store.delete_game(STEAM, game_id)
        self._summary = None
        return removed

    def resolve_npwr(self, np_title_id: str) -> Optional[str]:
        """Blocking PSN lookup: call from a background thread (the tab uses ``run.io_bound``)."""
        return psn_sync.resolve_npwr(self._psn_client_fn(), np_title_id)

    def group_progress(self, game_id: str) -> Optional[List[Dict[str, Any]]]:
        """Blocking PSN lookup of a game's trophy groups: call from a background thread."""
        game = self.store.get_game(PSN, game_id)
        if game is None:
            return None
        return psn_sync.group_progress(self._psn_client_fn(), game.game_id, game.platform_label)

    def reset_baseline(self, platform: Optional[str] = None) -> None:
        for p in [platform] if platform else PLATFORMS:
            self.store.reset_baseline(p)
        self._summary = None


def _blank_status() -> Dict[str, Any]:
    return {"state": "idle", "done": 0, "total": 0, "last_sync": 0.0, "first_import": False, "message": ""}


def summarize(games: List[TrackedGame]) -> Dict[str, Any]:
    visible = [g for g in games if not g.hidden]
    psn = [g for g in visible if g.platform == PSN]
    steam = [g for g in visible if g.platform == STEAM]
    grades = {"bronze": 0, "silver": 0, "gold": 0, "platinum": 0}
    for g in psn:
        for k in grades:
            grades[k] += int(g.earned.get(k, 0) or 0)
    started = [g for g in visible if g.earned_total > 0]
    current = max(visible, key=lambda g: g.last_played, default=None)
    return {
        "games": len(visible),
        "psn_games": len(psn),
        "steam_games": len(steam),
        "platinums": sum(1 for g in psn if g.has_platinum),
        "perfect": sum(1 for g in steam if g.is_complete),
        "completed": sum(1 for g in visible if g.is_complete),
        "trophies": sum(grades.values()),
        "trophies_by_grade": grades,
        "achievements": sum(g.earned_total for g in steam),
        "avg_completion": round(sum(g.progress for g in started) / len(started)) if started else 0,
        "current": current.public() if current else None,
    }


_TRADEMARKS = ("™", "®", "©")
FUZZY_MIN_RATIO = 0.8


def _norm(text: str) -> str:
    """Casefolded words without ™/®/punctuation: "ELDEN RING™" and "elden ring" compare equal."""
    s = str(text or "")
    for ch in _TRADEMARKS:
        s = s.replace(ch, "")
    s = s.replace("’", "'").casefold()
    return " ".join("".join(c if c.isalnum() else " " for c in s.replace("'", "")).split())


def find_game(
    query: str,
    platform: str = "",
    games: Optional[List[TrackedGame]] = None,
    *,
    include_hidden: bool = False,
) -> Optional[TrackedGame]:
    """A tracked game by name, ``psn:<NPWR ID>`` / ``steam:<app ID>``, or (blank) the most recently played.

    Names match forgivingly, best first: the same words, the same letters ignoring spaces
    ("eldenring"), the start of a title, a word run inside it, then close spellings. Ties go to the
    most recently played. ``platform`` ("psn" / "steam") narrows the search.
    """
    from difflib import SequenceMatcher

    if games is None:
        games = get_service().store.list_games()
    platform = str(platform or "").strip().lower()
    if platform in (PSN, STEAM):
        games = [g for g in games if g.platform == platform]
    query = str(query or "").strip()
    if ":" in query:
        prefix, _, game_id = query.partition(":")
        if prefix.strip().lower() in (PSN, STEAM):
            wanted = (prefix.strip().lower(), game_id.strip().lower())
            return next((g for g in games if (g.platform, str(g.game_id).lower()) == wanted), None)
    pool = sorted((g for g in games if include_hidden or not g.hidden), key=lambda g: g.last_played or 0, reverse=True)
    q = _norm(query)
    if not q:
        return pool[0] if pool else None
    names = [(g, {_norm(g.display_title), _norm(g.title)} - {""}) for g in pool]
    q_tight = q.replace(" ", "")
    tests = (
        lambda n: n == q,
        lambda n: n.replace(" ", "") == q_tight,
        lambda n: n.startswith(q),
        lambda n: f" {q} " in f" {n} ",
        lambda n: q_tight in n.replace(" ", "") and len(q_tight) >= 4,
    )
    for test in tests:
        for g, ns in names:
            if any(test(n) for n in ns):
                return g
    best, best_ratio = None, FUZZY_MIN_RATIO
    for g, ns in names:
        for n in ns:
            ratio = SequenceMatcher(None, q, n).ratio()
            if ratio > best_ratio:
                best, best_ratio = g, ratio
    return best


def _default_psn_client():
    try:
        from ..psn_service import get_psn_client

        return get_psn_client()
    except Exception:
        return None


class _SteamClientHolder:
    """The tracker's own SteamClient (own HTTP session), rebuilt when the saved key or profile changes."""

    def __init__(self):
        self._client = None
        self._for: Tuple[str, str] = ("", "")

    def get(self):
        try:
            from ..dataobjects import state_manager
            from ..steamapi import SteamClient

            s = state_manager.get_steam_settings_data()
            key, profile = (s.api_key or "").strip(), (s.steam_profile or "").strip()
        except Exception:
            return None
        if not key or not profile:
            self._client = None
            return None
        if self._client is None or self._for != (key, profile):
            self._client = SteamClient(key, profile)
            self._for = (key, profile)
        return self._client


_service: Optional[GameTrackerService] = None
_service_lock = threading.Lock()


def get_service() -> GameTrackerService:
    global _service
    with _service_lock:
        if _service is None:
            _service = GameTrackerService()
        return _service


def set_service(service: Optional[GameTrackerService]) -> None:
    """Swap the shared service (tests)."""
    global _service
    with _service_lock:
        _service = service


def start_game_tracker() -> None:
    get_service().start()


def stop_game_tracker(join_timeout: float = 5.0) -> None:
    if _service is not None:
        _service.stop(join_timeout=join_timeout)


def observe_psn_title(title_result: Dict[str, Any]) -> None:
    """From the PSN presence loop: queue the title it just refreshed. Never blocks or raises."""
    try:
        if _service is not None:
            _service.observe_psn_title(title_result)
    except Exception:
        logger.debug("Games Tracker: PSN observe skipped", exc_info=True)


def observe_steam_game(appid: Any, name: str, achievements: List[Dict[str, Any]]) -> None:
    """From the Steam poller: queue the game it just refreshed. Never blocks or raises."""
    try:
        if _service is not None:
            _service.observe_steam_game(appid, name, achievements)
    except Exception:
        logger.debug("Games Tracker: Steam observe skipped", exc_info=True)


def sync_now(platform: Optional[str] = None) -> None:
    get_service().sync_now(platform)


def recent_unlocks(limit: int = 5, platform: Optional[str] = None) -> List[GameEvent]:
    return get_service().store.recent_events(limit, platform=platform, kinds=("unlock",))

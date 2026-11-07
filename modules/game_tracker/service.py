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
import os
import queue
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from . import covers, psn_sync, steam_sync
from . import goals as goal_kinds
from .dispatch import dispatch
from .goals import GameGoal, evaluate, game_payload
from .speedrun import LSS_EXTENSIONS, TIMINGS, SpeedrunLink, SpeedrunWatcher, refresh_link, speedruns_public
from .models import PLATFORMS, PSN, STEAM, GameEvent, TrackedGame, TrackerSettings
from .store import GameTrackerStore, get_store

logger = logging.getLogger(__name__)

START_DELAY_SEC = 8.0  # let the app and the platform clients settle first
RETRY_SEC = 120.0  # after a sync that couldn't reach the platform
SUMMARY_TTL_SEC = 5.0
CHECKED_EARNED = "checked_earned"  # TrackedGame.extra: earned total when its trophy goals were last read


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
        self._speedruns = SpeedrunWatcher(lambda: self.store.list_speedruns(), lambda link: self.store.upsert_speedrun(link),
                                          self._notify)

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
        self._speedruns.start()

    def stop(self, join_timeout: float = 5.0) -> None:
        self._stop.set()
        self._speedruns.stop(join_timeout)
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
                    # "sync" is Sync now / the API / a reset: read everything, not just what changed.
                    ok = self._sync(platform, settings, full=kind == "sync")
                    next_sync = time.time() + (settings.sync_minutes * 60 if ok else RETRY_SEC)
            except Exception as e:
                logger.warning("Games Tracker: %s worker error: %s", platform, e, exc_info=True)
                self._set_status(platform, state="error", message=str(e)[:200])
                next_sync = time.time() + RETRY_SEC

    def _sync(self, platform: str, settings: TrackerSettings, *, full: bool = False) -> bool:
        first = self.store.baseline(platform) is None
        version = self.store.version
        self._set_status(platform, state="syncing", done=0, total=0, first_import=first, message="")
        progress = lambda done, total: self._set_status(platform, done=done, total=total)  # noqa: E731
        if platform == PSN:
            client = self._psn_client_fn()
            events = psn_sync.sync_library(client, self.store, settings, progress=progress, full=full) if client else None
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
        self._set_status(platform, state="idle", last_sync=time.time(), first_import=False, message="")
        if self.store.version == version and not events:
            return True  # nothing new: no goal pass, no overlay or tab update
        goal_events, goal_keys = self._evaluate_goals(platform, emit=not first, client=client)
        dispatch(list(events) + goal_events, settings, self.store)
        self._after_change(platform, _event_keys(events) | goal_keys)
        return True

    def _observe(self, platform: str, payload: Any, settings: TrackerSettings) -> None:
        if self.store.baseline(platform) is None:
            return
        version = self.store.version
        if platform == PSN:
            client = self._psn_client_fn()
            events = psn_sync.observe_title(client, self.store, settings, payload)
            keys = {f"{PSN}:{payload.get('np_communication_id')}"}
        else:
            appid, name, achievements = payload
            client = self._steam_client_fn()
            account = client.resolve_steam_id() if client else ""
            events = steam_sync.observe_game(self.store, settings, account or "", appid, name, achievements)
            keys = {f"{STEAM}:{appid}"}
        if self.store.version == version and not events:
            return  # the same counts as last time: nothing to tell anyone
        goal_events, goal_keys = self._evaluate_goals(platform, emit=True, client=client)
        if events or goal_events:
            dispatch(list(events) + goal_events, settings, self.store)
        self._after_change(platform, keys | _event_keys(events) | goal_keys)

    def refresh_covers(self) -> None:
        """Queue every game's cover again (after Clear covers). No PSN / Steam library read needed:
        the cover addresses are in the store."""
        for platform in PLATFORMS:
            games = self.store.list_games(platform)
            covers.request_covers(psn_sync.cover_items(games) if platform == PSN else steam_sync.cover_items(games))

    def _after_change(self, platform: str, keys: Iterable[str] = ()) -> None:
        games = self.store.list_games(platform)
        items = psn_sync.cover_items(games) if platform == PSN else steam_sync.cover_items(games)
        covers.request_covers(items)
        self._notify(keys)

    def _notify(self, keys: Iterable[str] = ()) -> None:
        """Push fresh totals (and each touched game, with its goals) to overlays, then tell listeners."""
        self._summary = None
        try:
            from .. import web_engine

            instance = getattr(web_engine, "web_engine_instance", None)
            if instance:
                instance.safe_emit("game_tracker_update", self.summary())
                for key in sorted(set(keys)):
                    payload = self.game_payload_for(key)
                    if payload is not None:
                        instance.safe_emit("game_tracker_game", payload)
        except Exception:
            logger.debug("Games Tracker: overlay update failed", exc_info=True)
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                logger.debug("Games Tracker: listener failed", exc_info=True)

    # ----- goals -----

    def _evaluate_goals(self, platform: str, *, emit: bool, client: Any = None) -> Tuple[List[GameEvent], Set[str]]:
        """Re-read every goal of one platform's games. Returns goal events and the keys of games whose goals moved."""
        try:
            by_game = self.store.goals_by_game(platform)
            if not by_game:
                return [], set()
            games = self.store.games_by_id(platform)
            changed: List[GameGoal] = []
            events: List[GameEvent] = []
            keys: Set[str] = set()
            for key, goals in by_game.items():
                game = games.get(goals[0].game_id)
                if game is None:
                    continue
                if platform == PSN:
                    changed.extend(self._refresh_psn_unlocks(client, game, goals))
                moved, evs = evaluate(game, goals, emit=emit)
                if moved:
                    keys.add(key)
                changed.extend(moved)
                events.extend(evs)
            self.store.upsert_goals({g.id: g for g in changed}.values())
            return events, keys
        except Exception:
            logger.warning("Games Tracker: couldn't update goals", exc_info=True)
            return [], set()

    def _refresh_psn_unlocks(self, client: Any, game: TrackedGame, goals: List[GameGoal]) -> List[GameGoal]:
        """PSN only keeps trophy counts: read the earned list when a game with open trophy goals earns something."""
        pending = [g for g in goals if g.kind == goal_kinds.UNLOCK and g.unlock_id and not g.unlocked]
        # The earned total last read is kept on the game, so a restart doesn't read every list again.
        if not pending or client is None or game.extra.get(CHECKED_EARNED) == game.earned_total:
            return []
        trophies = psn_sync.trophy_list(client, game.game_id, game.platform_label, earned_only=True,
                                        defined=game.defined, store=self.store)
        if trophies is None:
            return []
        game.extra[CHECKED_EARNED] = game.earned_total
        self.store.upsert_games([game])
        earned = {str(t["id"]) for t in trophies}
        found = [g for g in pending if g.unlock_id in earned]
        for g in found:
            g.unlocked = True
        return found

    def goals_for(self, platform: str, game_id: str) -> List[GameGoal]:
        return self.store.list_goals(platform, game_id)

    def goals_by_game(self) -> Dict[str, List[GameGoal]]:
        return self.store.goals_by_game()

    def game_payload_for(self, key: str) -> Optional[Dict[str, Any]]:
        """``goals.game_payload`` for ``"psn:NPWR..."`` / ``"steam:<app ID>"`` (None when it isn't tracked)."""
        platform, _, game_id = str(key or "").partition(":")
        game = self.store.get_game(platform, game_id) if platform in PLATFORMS and game_id else None
        if game is None:
            return None
        return game_payload(game, self.goals_for(platform, game_id), speedruns_public(self.speedruns_for(platform, game_id)))

    def games_payload(self, mode: str = "selected", keys: Iterable[str] = ()) -> List[Dict[str, Any]]:
        """Games for an overlay: the chosen ``keys`` in order, every game with goals (``backlog``) or the last played (``current``)."""
        if mode == "backlog":
            return self.backlog()
        if mode == "current":
            game = find_game("", "", self.store.list_games())
            return [self.game_payload_for(game.key)] if game is not None else []
        out = []
        for key in keys or ():
            payload = self.game_payload_for(str(key).strip())
            if payload is not None:
                out.append(payload)
        return out

    def backlog(self, include_hidden: bool = False) -> List[Dict[str, Any]]:
        """Every game with goals (open goals first, then the furthest along), as payloads."""
        by_game = self.store.goals_by_game()
        runs = self.store.speedruns_by_game()
        out = []
        for key, goals in by_game.items():
            platform, _, game_id = key.partition(":")
            game = self.store.get_game(platform, game_id)
            if game is None or (game.hidden and not include_hidden):
                continue
            out.append(game_payload(game, goals, speedruns_public(runs.get(key, []))))
        out.sort(key=lambda p: (p["goals_complete"], -p["goals_progress"], -(p.get("last_played") or 0)))
        return out

    def add_goal(self, platform: str, game_id: str, **values: Any) -> Tuple[Optional[GameGoal], str]:
        game = self.store.get_game(platform, game_id)
        if game is None:
            return None, "That game isn't in the tracker."
        existing = self.goals_for(platform, game_id)
        raw = {k: v for k, v in values.items() if k not in ("id", "platform", "game_id", "completed_at", "last_current")}
        goal = GameGoal.from_dict({**raw, "platform": platform, "game_id": str(game_id),
                                   "order": max((g.order for g in existing), default=-1) + 1})
        if goal.kind == goal_kinds.UNLOCK and not goal.unlock_id:
            return None, "Pick the trophy or achievement for this goal."
        evaluate(game, [goal], emit=False)  # a goal that's already met is done, without an event
        self.store.upsert_goals([goal])
        self._notify([game.key])
        return goal, ""

    def update_goal(self, goal_id: str, **changes: Any) -> Tuple[Optional[GameGoal], str]:
        """Rename a goal or change its target, count or grade (its kind and game stay)."""
        goal = self.store.get_goal(goal_id)
        if goal is None:
            return None, "That goal no longer exists."
        allowed = ("name", "target", "current", "grade")
        goal = GameGoal.from_dict({**goal.to_dict(), **{k: v for k, v in changes.items() if k in allowed}})
        return self._save_goal(goal)

    def adjust_goal_by_id(self, goal_id: str, op: str, amount: Any = 1) -> Tuple[Optional[GameGoal], str]:
        goal = self.store.get_goal(goal_id)
        if goal is None:
            return None, "That goal no longer exists."
        if not goal_kinds.adjust(goal, op, amount):
            return None, "Only manual goals can be counted by hand."
        return self._save_goal(goal)

    def adjust_goal(
        self, game_query: str = "", platform: str = "", goal_ref: str = "", op: str = "add", amount: Any = 1
    ) -> Tuple[Optional[GameGoal], Optional[TrackedGame], str]:
        """For connectors and chat: find a game (like :func:`find_game`) and change one of its manual goals.

        ``goal_ref`` is a goal ID or name (forgiving match); blank picks the game's first open manual goal.
        """
        game = find_game(game_query, platform, self.store.list_games(), include_hidden=bool(str(game_query or "").strip()))
        if game is None:
            return None, None, f"No game matching “{game_query}” in the Games Tracker." if game_query else "No recently played game."
        goals = self.goals_for(game.platform, game.game_id)
        goal = _match_goal(goals, goal_ref)
        if goal is None:
            what = f"“{goal_ref}”" if goal_ref else "an open manual goal"
            return None, game, f"{game.display_title} has no goal {what}." if goal_ref else f"{game.display_title} has no open manual goal."
        if not goal_kinds.adjust(goal, op, amount):
            return None, game, f"“{goal_kinds.display_name(goal)}” follows the game's progress and can't be changed by hand."
        saved, error = self._save_goal(goal)
        return saved, game, error

    def _save_goal(self, goal: GameGoal) -> Tuple[Optional[GameGoal], str]:
        game = self.store.get_game(goal.platform, goal.game_id)
        goals = [goal if g.id == goal.id else g for g in self.goals_for(goal.platform, goal.game_id)]
        _changed, events = evaluate(game, goals, emit=True)
        self.store.upsert_goals([goal] + [g for g in _changed if g.id != goal.id])
        if events:
            dispatch(events, self.settings, self.store)
        self._notify([goal.key])
        return goal, ""

    def remove_goal(self, goal_id: str) -> bool:
        goal = self.store.get_goal(goal_id)
        if goal is None or not self.store.delete_goal(goal_id):
            return False
        self._notify([goal.key])
        return True

    def move_goal(self, goal_id: str, delta: int) -> bool:
        """Move a goal up (-1) or down (+1) in its game's list."""
        goal = self.store.get_goal(goal_id)
        if goal is None:
            return False
        goals = self.goals_for(goal.platform, goal.game_id)
        i = next((n for n, g in enumerate(goals) if g.id == goal_id), -1)
        j = i + (1 if delta > 0 else -1)
        if i < 0 or not 0 <= j < len(goals):
            return False
        goals[i], goals[j] = goals[j], goals[i]
        for n, g in enumerate(goals):
            g.order = n
        self.store.upsert_goals(goals)
        self._notify([goal.key])
        return True

    # ----- speedrun info (LiveSplit split files, any number per game) -----

    def speedruns_for(self, platform: str, game_id: str) -> List[SpeedrunLink]:
        return self.store.list_speedruns(platform, game_id)

    def speedruns_by_game(self) -> Dict[str, List[SpeedrunLink]]:
        return self.store.speedruns_by_game()

    def add_speedrun_file(self, platform: str, game_id: str, path: str, category: str = "") -> Tuple[Optional[SpeedrunLink], str]:
        """Link a LiveSplit split file to a game and read it once now (blocking: call off the UI thread).

        ``category`` labels it ("Any%"); left blank, the category saved in the file is used. The file is
        only ever read, in a way that never gets in LiveSplit's way (see ``speedrun.read_shared``).
        """
        if self.store.get_game(platform, game_id) is None:
            return None, "That game isn't in the tracker."
        path, error = _split_file_path(path)
        if error:
            return None, error
        existing = self.speedruns_for(platform, game_id)
        if any(os.path.normcase(x.path) == os.path.normcase(path) for x in existing):
            return None, "That file is already linked to this game."
        link = SpeedrunLink(platform=platform, game_id=str(game_id), path=path, category=str(category or "").strip(),
                            order=max((x.order for x in existing), default=-1) + 1)
        refresh_link(link)
        if not link.category:
            link.category = str(link.stats.get("category") or "").strip()
        self.store.upsert_speedrun(link)
        self._speedruns.poke()
        self._notify([link.key])
        return link, ""

    def update_speedrun(self, link_id: str, **changes: Any) -> Tuple[Optional[SpeedrunLink], str]:
        """Change a split file's ``category``, ``timing`` or ``path`` (a new path is read again)."""
        link = self.store.get_speedrun(link_id)
        if link is None:
            return None, "That split file is no longer linked."
        if "category" in changes:
            link.category = str(changes["category"] or "").strip()
        if changes.get("timing") in TIMINGS:
            link.timing = changes["timing"]
        if changes.get("path"):
            path, error = _split_file_path(changes["path"])
            if error:
                return None, error
            if os.path.normcase(path) != os.path.normcase(link.path):
                link.path, link.stats, link.signature, link.error, link.read_at = path, {}, [], "", 0.0
                refresh_link(link)
        self.store.upsert_speedrun(link)
        self._speedruns.poke()
        self._notify([link.key])
        return link, ""

    def remove_speedrun(self, link_id: str) -> bool:
        link = self.store.get_speedrun(link_id)
        if link is None or not self.store.delete_speedrun(link_id):
            return False
        self._notify([link.key])
        return True

    def move_speedrun(self, link_id: str, delta: int) -> bool:
        """Move a split file up (-1) or down (+1) in its game's list (the order categories show in)."""
        link = self.store.get_speedrun(link_id)
        if link is None:
            return False
        links = self.speedruns_for(link.platform, link.game_id)
        i = next((n for n, x in enumerate(links) if x.id == link_id), -1)
        j = i + (1 if delta > 0 else -1)
        if i < 0 or not 0 <= j < len(links):
            return False
        links[i], links[j] = links[j], links[i]
        for n, x in enumerate(links):
            x.order = n
            self.store.upsert_speedrun(x)
        self._notify([link.key])
        return True

    def achievement_list(self, platform: str, game_id: str) -> Optional[List[Dict[str, Any]]]:
        """Blocking: every trophy / achievement of a game (``id``, ``name``, ``description``, ``icon``, ``grade``,
        ``earned``) for picking a goal. None when the platform can't be reached."""
        game = self.store.get_game(platform, game_id)
        if game is None:
            return None
        if platform == PSN:
            return psn_sync.trophy_list(self._psn_client_fn(), game.game_id, game.platform_label,
                                        defined=game.defined, store=self.store)
        client = self._steam_client_fn()
        if client is None:
            return None
        try:
            # The stored unlocks are what the picker needs: no player request.
            earned = game.unlocked_ids if game.baselined else None
            return client.achievement_list(int(game.game_id), earned=earned)
        except Exception as e:
            logger.debug("Games Tracker: Steam achievement list for %s failed: %s", game_id, e)
            return None

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
        result = summarize(self.store.list_games(), self.store.goals_by_game())
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
        if game is not None and not error:
            self._notify([game.key])
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
        return psn_sync.group_progress(self._psn_client_fn(), game.game_id, game.platform_label,
                                       defined=game.defined, store=self.store)

    def reset_baseline(self, platform: Optional[str] = None) -> None:
        for p in [platform] if platform else PLATFORMS:
            self.store.reset_baseline(p)
        self._summary = None


def _blank_status() -> Dict[str, Any]:
    return {"state": "idle", "done": 0, "total": 0, "last_sync": 0.0, "first_import": False, "message": ""}


def _split_file_path(raw: Any) -> Tuple[str, str]:
    """(absolute path, error) for a LiveSplit split file the user picked."""
    path = os.path.abspath(str(raw or "").strip())
    if not path.lower().endswith(LSS_EXTENSIONS):
        return path, "Pick a LiveSplit split file (.lss)."
    if not os.path.isfile(path):
        return path, "That file doesn't exist."
    return path, ""


def _event_keys(events: Optional[Iterable[GameEvent]]) -> Set[str]:
    return {f"{e.platform}:{e.game_id}" for e in events or ()}


def _match_goal(goals: List[GameGoal], ref: str) -> Optional[GameGoal]:
    """A goal by ID, then by name (same words, start of the name, words inside it); blank: the first open manual goal."""
    ref = str(ref or "").strip()
    if not ref:
        return next((g for g in goals if g.kind == goal_kinds.MANUAL and not g.completed_at), None) or next(
            (g for g in goals if g.kind == goal_kinds.MANUAL), None)
    by_id = next((g for g in goals if g.id == ref), None)
    if by_id is not None:
        return by_id
    q = _norm(ref)
    names = [(g, _norm(goal_kinds.display_name(g))) for g in goals]
    for test in (lambda n: n == q, lambda n: n.startswith(q), lambda n: f" {q} " in f" {n} ", lambda n: q in n):
        found = next((g for g, n in names if q and test(n)), None)
        if found is not None:
            return found
    return None


def summarize(games: List[TrackedGame], goals_by_key: Optional[Dict[str, List[GameGoal]]] = None) -> Dict[str, Any]:
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
        **_goal_totals(visible, goals_by_key or {}),
    }


def _goal_totals(visible: List[TrackedGame], goals_by_key: Dict[str, List[GameGoal]]) -> Dict[str, Any]:
    """Backlog counts: games with goals, those with goals still open, and the goals themselves."""
    by_key = {g.key: g for g in visible}
    games = done_games = goals_total = goals_done = 0
    for key, goals in goals_by_key.items():
        game = by_key.get(key)
        if game is None or not goals:
            continue
        done = sum(1 for g in goals if goal_kinds.goal_state(g, game).done)
        games += 1
        done_games += done == len(goals)
        goals_total += len(goals)
        goals_done += done
    return {
        "backlog": games,
        "backlog_active": games - done_games,
        "backlog_complete": done_games,
        "goals_total": goals_total,
        "goals_done": goals_done,
        "goals_active": goals_total - goals_done,
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
    """The Steam client for library syncs: the live poller's client when it uses the saved key and
    profile (one HTTP session, one schema / percentage cache), else a client of our own, rebuilt
    when the saved key or profile changes."""

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
        try:
            from ..steam_service import get_steam_client

            live = get_steam_client()
        except Exception:
            live = None
        if live is not None and live.api_key == key and live.profile == profile:
            return live
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


def goals_of(game: Optional[TrackedGame]) -> List[GameGoal]:
    """A game's goals for lookups (chat, connectors); [] when there's no game or the store can't be read."""
    if game is None:
        return []
    try:
        return get_service().goals_for(game.platform, game.game_id)
    except Exception:
        logger.debug("Games Tracker: goals unavailable for %s", game.key, exc_info=True)
        return []


def recent_unlocks(limit: int = 5, platform: Optional[str] = None) -> List[GameEvent]:
    return get_service().store.recent_events(limit, platform=platform, kinds=("unlock",))

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

# Activity Feed: the app tab, the shared view (app <-> OBS dock) and the feed payloads.
#
# Rendering is done by assets/default_assets/activity_feed.js in both the tab and the
# dock. The tab is one NiceGUI host element: Python writes state into hidden "slot"
# elements (data-json + data-rev attributes) and the renderer paints from them, so a
# NiceGUI remount or reconnect re-sends the state and the feed repaints by itself. A
# watchdog asks the renderer for its health and falls back to a plain server-rendered
# list if it can't run (fail open, never a blank tab).

import html
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from nicegui import background_tasks, run, ui

from ..notification_engine import nav_actions_main_tab, nav_actions_settings, notify
from .. import alertutils
from ..ui_timer import app_schedule, run_on_ui_loop
from .activity_feed_model import (
    FEED_SETTINGS,
    alert_duration_months,
    feed_row_model,
    feed_setting_value,
    giftsub_message,
    mode_patch,
    normalize_feed_settings,
    normalize_queue_state,
    normalize_search,
    person_totals,
    resub_message,
    sub_message,
    view_mode,
)

logger = logging.getLogger(__name__)

_FEED_EVENT = "mycelian-afx"
_STYLE_ID = "mycelian-activity-feed"
_SLOT_NAMES = ("settings", "view", "live", "history", "person", "queue", "meta")
_QUEUE_POLL_SEC = 0.5
_FEED_WATCHDOG_INTERVAL_SEC = 20.0
_LIVE_PUSH_DEBOUNCE_SEC = 0.15

# The live feed is a session view, not history: older alerts stay available on the
# History view, which is paged from the database.
MAX_LIVE_ALERTS = 500
MAX_CONDENSED_GROUPS = 200


def _is_stale_client_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "client" in msg and "deleted" in msg


def _element_alive(el: Any) -> bool:
    """Return True when a NiceGUI element still belongs to a live client."""
    if el is None:
        return False
    try:
        if getattr(el, "is_deleted", False):
            return False
        _ = el.client
        return True
    except RuntimeError as exc:
        if _is_stale_client_error(exc):
            return False
        raise
    except Exception:
        return False


# --------------------------------------------------------------------------- live list

_live_lock = threading.RLock()
_seq_lock = threading.Lock()
_live_seq = 0


def _next_seq() -> int:
    """Arrival number of a live alert (#1, #2 ... this session)."""
    global _live_seq
    with _seq_lock:
        _live_seq += 1
        return _live_seq


def _trim_live_alerts() -> None:
    """Drop the oldest live alerts once the session feed exceeds its cap.

    With minimize-to-tray a session can run for weeks, so the list has to be bounded.
    Elements left over from older builds are deleted too.
    """
    with _live_lock:
        overflow = len(activity_feed_state.live_alerts) - MAX_LIVE_ALERTS
        if overflow <= 0:
            return
        for alert_data in activity_feed_state.live_alerts[-overflow:]:
            element = alert_data.get("element") if isinstance(alert_data, dict) else None
            if _element_alive(element):
                try:
                    element.delete()
                except Exception as exc:
                    logger.debug("activity_feed: could not delete trimmed element: %s", exc)
            if isinstance(alert_data, dict):
                alert_data["element"] = None
        del activity_feed_state.live_alerts[-overflow:]
    logger.debug("activity_feed: trimmed %d live alert(s) over the cap", overflow)


def live_alerts_snapshot() -> List[Dict[str, Any]]:
    """Copy of the session feed (newest first), safe to iterate off the UI loop."""
    with _live_lock:
        return list(activity_feed_state.live_alerts)


def _row_for(alert: Dict[str, Any]) -> Dict[str, Any]:
    row = alert.get("row") if isinstance(alert, dict) else None
    if isinstance(row, dict) and row.get("kind"):
        return row
    return feed_row_model(alert or {})


class AlertEventHandler:
    """Puts a new alert into the shared live list and repaints the feeds.

    The insert happens synchronously (before the overlay is told about the alert), so a
    dock that resyncs in between can never miss it, and arrival order is call order.
    """

    def __init__(self) -> None:
        self._should_stop = False

    def process_alert_immediately(self, alert_data: Dict[str, Any]) -> None:
        if self._should_stop or not isinstance(alert_data, dict):
            return
        try:
            entry = dict(alert_data)
            entry.setdefault("is_restored", False)
            if not isinstance(entry.get("row"), dict):
                entry["row"] = feed_row_model(entry)
            with _live_lock:
                activity_feed_state.live_alerts.insert(0, entry)
            _trim_live_alerts()
            _schedule_live_push()
            if view_mode(view_manager.snapshot()) == "person":
                schedule_condensed_view_update("new_alert")
        except Exception as exc:
            logger.error("activity_feed: could not add alert: %s", exc, exc_info=True)

    def force_timestamp_update(self) -> None:
        """Kept for callers: times are kept current by the renderer itself."""

    def shutdown(self) -> None:
        self._should_stop = True


alert_event_handler = AlertEventHandler()


def format_timestamp(timestamp):
    """Format a timestamp like "just now", "2m ago", "1h ago"."""
    if timestamp == "now":
        return "just now"
    try:
        timestamp_float = float(timestamp)
    except (ValueError, TypeError):
        timestamp_float = time.time()
    diff_seconds = time.time() - timestamp_float
    if diff_seconds < 60:
        return "just now"
    if diff_seconds < 3600:
        return f"{int(diff_seconds / 60)}m ago"
    if diff_seconds < 86400:
        return f"{int(diff_seconds / 3600)}h ago"
    return f"{int(diff_seconds / 86400)}d ago"


# --------------------------------------------------------------------------- shared view

ACTIVITY_FEED_VIEW_PATH = "ActivityFeedView"
FILTER_KEYS = (
    "all",
    "follows",
    "subs",
    "resubs",
    "giftsubs",
    "bits",
    "points",
    "donations",
    "raids",
    "streaks",
    "modiversaries",
    "hype_train",
)
_LIVE_ALERT_UI_KEYS = frozenset(
    {
        "element",
        "new_badge",
        "timestamp_label",
        "newBadge",
        "timestampLabel",
    }
)
_JSON_DROP = object()


def default_filter_state() -> Dict[str, bool]:
    """All event filters on. This is the view used when nothing is saved."""
    return {key: True for key in FILTER_KEYS}


def _coerce_bool(value: Any, default: bool) -> bool:
    """Coerce stored checkbox values without treating the string "false" as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return default


def normalize_filter_state(raw: Any) -> Dict[str, bool]:
    """Keep known filter keys only. Missing keys default to on."""
    filters = default_filter_state()
    if isinstance(raw, dict):
        for key in FILTER_KEYS:
            if key in raw:
                filters[key] = _coerce_bool(raw[key], filters[key])
    return filters


def apply_filter_rules(
    filters: Dict[str, bool], changed_key: Optional[str] = None
) -> Dict[str, bool]:
    """Apply the All Events checkbox rules used by both feeds."""
    result = dict(filters)
    if changed_key == "all" and result.get("all"):
        for key in result:
            result[key] = True
    elif changed_key and changed_key != "all":
        others_checked = all(
            value for key, value in result.items() if key != "all" and key != changed_key
        )
        result["all"] = bool(result.get(changed_key) and others_checked)
    return result


def _max_history_pages() -> int:
    try:
        from modules import dataobjects

        sm = dataobjects.state_manager
        if not getattr(sm, "_initialized", False):
            return 50  # never trigger a settings load just to clamp a page
        return max(1, int(sm.get_app_settings().activity_feed_max_pages or 1))
    except Exception:
        return 50


def normalize_view(raw: Any) -> Dict[str, Any]:
    """Fill a complete activity-feed view from a partial or invalid document."""
    source = raw if isinstance(raw, dict) else {}
    tab = source.get("tab", "current")
    if tab not in ("current", "previous"):
        tab = "current"
    try:
        page = int(source.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1
    return {
        "filters": normalize_filter_state(source.get("filters")),
        "condensed": _coerce_bool(source.get("condensed", False), False),
        "tab": tab,
        "page": page,
        "search": normalize_search(source.get("search", "")),
    }


def _json_ready(value: Any) -> Any:
    """Return a JSON-safe copy, dropping NiceGUI elements and other live objects."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        ready: Dict[str, Any] = {}
        for key, item in value.items():
            if key in _LIVE_ALERT_UI_KEYS:
                continue
            converted = _json_ready(item)
            if converted is _JSON_DROP:
                continue
            ready[str(key)] = converted
        return ready
    if isinstance(value, (list, tuple)):
        items = []
        for item in value:
            converted = _json_ready(item)
            if converted is _JSON_DROP:
                continue
            items.append(converted)
        return items
    return _JSON_DROP


def serialize_live_alert(alert: Dict[str, Any]) -> Dict[str, Any]:
    """Copy one live alert without UI element references."""
    ready = _json_ready(alert)
    if isinstance(ready, dict):
        return ready
    return {}


def serialize_live_alerts(alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Copy the session feed for the overlay."""
    serialized: List[Dict[str, Any]] = []
    for alert in alerts:
        if isinstance(alert, dict):
            serialized.append(serialize_live_alert(alert))
    return serialized


class ActivityFeedState:
    """Module state the rest of Mycelian reads (filters, view, live list, tab refs)."""

    def __init__(self):
        self.is_initialized: bool = False
        self.should_stop: bool = False
        self.live_alerts: List[Dict[str, Any]] = []  # newest first
        self.filter_state: Dict[str, bool] = default_filter_state()
        # Display type -> filter key (YouTube-native labels map onto the Twitch chips)
        self.alert_type_to_filter: Dict[str, str] = {
            "Follow": "follows",
            "Sub": "subs",
            "Resub": "resubs",
            "Giftsub": "giftsubs",
            "Bits": "bits",
            "Points": "points",
            "Donation": "donations",
            "Raid": "raids",
            "Streak": "streaks",
            "Modiversary": "modiversaries",
            "Hype Train": "hype_train",
            "Membership": "subs",
            "Member Milestone": "resubs",
            "Gift Membership": "giftsubs",
            "Super Chat": "donations",
            "Super Sticker": "donations",
        }
        self.current_page: int = 1
        self.total_pages: int = 1
        self.pagination_limit: int = 25
        self.current_tab: str = "current"  # "current" or "previous"
        self.condense_list: bool = False
        self.search_text: str = ""
        self.alerts_muted: bool = False


activity_feed_state = ActivityFeedState()


class ActivityFeedViewManager:
    """Canonical display state for every activity-feed surface.

    The app tab and the OBS dock both read and write this view (filters, Live / History /
    By person, history page and the search text). A revision stops an older echo from
    rolling the display backward. Search is shared but not saved across restarts.
    """

    _VIEW_FIELDS = ("filters", "condensed", "tab", "page", "search")
    _SAVED_FIELDS = ("filters", "condensed", "tab", "page")

    def __init__(self) -> None:
        self.revision = 0
        self._loaded = False
        self._view = normalize_view({})
        self._lock = threading.RLock()
        # The stored view could not be read (not merely empty): saves must merge
        # onto the stored copy instead of writing defaults over it.
        self._read_failed = False
        self._unsaved_fields: set = set()
        self._restored_fields: set = set()

    def load(self) -> Dict[str, Any]:
        """Read ActivityFeedView once per process. Failures keep the defaults."""
        with self._lock:
            if self._loaded:
                self._sync_state()
                return self.snapshot()
            self._loaded = True
            try:
                from ..database_manager import get_data, get_data_checked

                raw = get_data(ACTIVITY_FEED_VIEW_PATH)
                if not raw:
                    # get_data returns {} for a failed read too; ask once more in a
                    # way that tells the two apart.
                    raw, ok = get_data_checked(ACTIVITY_FEED_VIEW_PATH)
                    if not ok:
                        self._read_failed = True
                        raise RuntimeError("the read failed")
                raw = raw or {}
                if not isinstance(raw, dict):
                    raise TypeError("ActivityFeedView document is not a dict")
                view = normalize_view(raw)
                view["search"] = ""
                self._view = view
            except Exception as exc:
                logger.warning(
                    "activity_feed: could not load saved view, using defaults: %s", exc
                )
                self._view = normalize_view({})
            self._sync_state()
            return self.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        """Return the live view plus its revision."""
        with self._lock:
            view = self._view
            snap = {
                "revision": self.revision,
                "filters": dict(view["filters"]),
                "condensed": bool(view["condensed"]),
                "tab": view["tab"],
                "page": int(view["page"]),
                "search": str(view.get("search") or ""),
            }
        snap["mode"] = view_mode(snap)
        return snap

    def _merge_onto_stored_view(self) -> bool:
        """After a failed load: re-read and keep this session's changes on top.

        Returns False while the stored view is still unreadable (nothing is saved).
        """
        from ..database_manager import get_data_checked

        raw, ok = get_data_checked(ACTIVITY_FEED_VIEW_PATH)
        if not ok:
            logger.warning(
                "activity_feed: saved view still unreadable; not saving over it"
            )
            return False
        base = normalize_view(raw if isinstance(raw, dict) and raw else {})
        base["search"] = self._view.get("search", "")
        for field in self._unsaved_fields:
            base[field] = self._view[field]
        self._restored_fields = {
            f for f in self._SAVED_FIELDS if base[f] != self._view[f]
        }
        self._view = base
        self._read_failed = False
        self._unsaved_fields.clear()
        return True

    def save(self) -> bool:
        """Persist filters, view and page. Revision and search stay in memory."""
        with self._lock:
            if self._read_failed:
                try:
                    if not self._merge_onto_stored_view():
                        return False
                except Exception as exc:
                    logger.warning("activity_feed: could not save view: %s", exc)
                    return False
            payload = {
                "filters": dict(self._view["filters"]),
                "condensed": bool(self._view["condensed"]),
                "tab": self._view["tab"],
                "page": int(self._view["page"]),
            }
        try:
            from ..database_manager import set_data

            return bool(set_data(ACTIVITY_FEED_VIEW_PATH, payload))
        except Exception as exc:
            logger.warning("activity_feed: could not save view: %s", exc)
            return False

    def update(
        self,
        patch: Optional[Dict[str, Any]] = None,
        *,
        apply_desktop: bool = True,
        broadcast: bool = True,
    ) -> Dict[str, Any]:
        """Merge a partial change, save it, and push it to both surfaces."""
        self.load()
        with self._lock:
            changed = self._merge_patch(patch or {})
            if not changed:
                return self.snapshot()
            self.revision += 1
            saved_change = bool(changed & set(self._SAVED_FIELDS))
            if self._read_failed:
                self._unsaved_fields |= changed & set(self._SAVED_FIELDS)
        if saved_change and not self.save():
            _notify_view_not_saved()
        with self._lock:
            if self._restored_fields:
                # Stored fields brought back by a late successful read repaint too.
                changed = changed | self._restored_fields
                self._restored_fields = set()
            self._sync_state()
            snapshot = self.snapshot()
        if apply_desktop:
            _run_on_ui_loop(lambda: _apply_view_to_desktop(changed))
        if broadcast:
            broadcast_activity_feed_view(snapshot)
        return snapshot

    def apply_snapshot(self, snapshot: Dict[str, Any]) -> bool:
        """Adopt a newer snapshot. The same or an older revision does not save."""
        self.load()
        if not isinstance(snapshot, dict):
            return False
        try:
            revision = int(snapshot.get("revision"))
        except (TypeError, ValueError):
            return False
        with self._lock:
            if revision <= self.revision:
                return False
            self._view = normalize_view(snapshot)
            self.revision = revision
            if self._read_failed:
                self._unsaved_fields |= set(self._SAVED_FIELDS)
        if not self.save():
            _notify_view_not_saved()
        with self._lock:
            self._sync_state()
        return True

    def set_page_quietly(self, page: int) -> bool:
        """Correct a stale page (history shrank) without a user-visible change event."""
        with self._lock:
            page = max(1, int(page))
            if page == int(self._view["page"]):
                return False
            self._view = dict(self._view, page=page)
            self.revision += 1
            self._sync_state()
        self.save()
        return True

    def _merge_patch(self, patch: Dict[str, Any]) -> set:
        """Apply one partial update. Returns the field names that changed."""
        if not isinstance(patch, dict):
            return set()
        patch = dict(patch)
        if patch.get("mode") in ("live", "history", "person"):
            for key, value in mode_patch(patch["mode"]).items():
                patch.setdefault(key, value)
        changed: set = set()
        view = dict(self._view)

        if "condensed" in patch:
            condensed = _coerce_bool(patch.get("condensed"), bool(view["condensed"]))
            if condensed != bool(view["condensed"]):
                view["condensed"] = condensed
                changed.add("condensed")

        if "tab" in patch:
            tab = patch.get("tab")
            if tab in ("current", "previous") and tab != view["tab"]:
                view["tab"] = tab
                changed.add("tab")

        if "page" in patch:
            try:
                page = int(patch.get("page"))
            except (TypeError, ValueError):
                page = int(view["page"])
            page = min(max(page, 1), _max_history_pages())
            if page != int(view["page"]):
                view["page"] = page
                changed.add("page")

        if "search" in patch:
            search = normalize_search(patch.get("search"))
            if search != view.get("search", ""):
                view["search"] = search
                changed.add("search")

        incoming_filters = patch.get("filters")
        if isinstance(incoming_filters, dict):
            filters = dict(view["filters"])
            known = [key for key in incoming_filters if key in FILTER_KEYS]
            for key in known:
                filters[key] = _coerce_bool(incoming_filters[key], filters[key])
            if len(known) == 1:
                filters = apply_filter_rules(filters, known[0])
            else:
                filters = normalize_filter_state(filters)
            if filters != view["filters"]:
                view["filters"] = filters
                changed.add("filters")

        if changed:
            self._view = view
        return changed

    def _sync_state(self) -> None:
        """Copy the canonical view onto the fields the rest of the feed reads.

        Fresh objects are swapped in (never mutated in place) so a reader on another
        thread never sees a half-updated filter dict.
        """
        view = self._view
        activity_feed_state.filter_state = dict(view["filters"])
        activity_feed_state.condense_list = bool(view["condensed"])
        activity_feed_state.current_tab = view["tab"]
        activity_feed_state.current_page = int(view["page"])
        activity_feed_state.search_text = str(view.get("search") or "")


view_manager = ActivityFeedViewManager()


def broadcast_activity_feed_view(
    snapshot: Dict[str, Any],
    *,
    to: Optional[str] = None,
    include_live_alerts: bool = False,
) -> bool:
    """Push the shared view to overlay clients."""
    payload = {
        "revision": int(snapshot.get("revision", 0)),
        "filters": dict(snapshot.get("filters") or {}),
        "condensed": bool(snapshot.get("condensed", False)),
        "tab": snapshot.get("tab", "current"),
        "page": int(snapshot.get("page", 1) or 1),
        "search": str(snapshot.get("search") or ""),
    }
    payload["mode"] = view_mode(payload)
    if include_live_alerts:
        payload["live_alerts"] = serialize_live_alerts(live_alerts_snapshot())
    return _emit_overlay_event("activity_feed_view", payload, to=to)


def _emit_overlay_event(event: str, payload: Dict[str, Any], *, to: Optional[str] = None) -> bool:
    """Emit one Socket.IO event if the overlay server is running."""
    try:
        from modules import web_engine

        engine = getattr(web_engine, "web_engine_instance", None)
        if engine is None:
            return False
        return bool(engine.safe_emit(event, payload, to=to))
    except Exception as exc:
        logger.debug("activity_feed: overlay emit %s failed: %s", event, exc)
        return False


def _notify_view_not_saved() -> None:
    """Filter / page changes still apply now but won't survive a restart."""
    notify(
        "Your filter and page choices work for now, but will reset after a restart.",
        type="warning",
        source="activity_feed",
        title="Couldn't save your Activity Feed filters",
        hint="Check your database connection in Settings → Database.",
        actions=nav_actions_settings("Database"),
        dedupe_key="activity_feed:view_save_failed",
        dedupe_cooldown_sec=300.0,
    )


# --------------------------------------------------------------------------- feed settings

_legacy_hours_cache: Optional[int] = None


def _legacy_person_hours() -> Optional[int]:
    """The old dock setting (activity_feed.json condense_historical_hours), read once."""
    global _legacy_hours_cache
    if _legacy_hours_cache is None:
        try:
            _legacy_hours_cache = _load_condensed_historical_hours()
        except Exception:
            _legacy_hours_cache = 12
    return _legacy_hours_cache


def current_feed_settings(app_settings: Any = None) -> Dict[str, Any]:
    """The feed look/behaviour settings (App Settings → Activity Feed), validated."""
    if app_settings is None:
        try:
            from modules import dataobjects

            sm = dataobjects.state_manager
            # Never start a settings load (and a database connection) just to paint the feed.
            app_settings = sm.get_app_settings() if getattr(sm, "_initialized", False) else None
        except Exception as exc:
            logger.debug("activity_feed: settings unavailable, using defaults: %s", exc)
            app_settings = None
    raw_hours = getattr(app_settings, "activity_feed_person_hours", 0) if app_settings is not None else 0
    legacy = _legacy_person_hours() if not raw_hours else None
    settings = normalize_feed_settings(app_settings, legacy_person_hours=legacy)
    try:
        settings["page_limit"] = max(1, int(getattr(app_settings, "activity_feed_limit", 20) or 20))
        settings["max_pages"] = max(1, int(getattr(app_settings, "activity_feed_max_pages", 5) or 5))
    except (TypeError, ValueError):
        settings["page_limit"], settings["max_pages"] = 20, 5
    return settings


def show_sub_duration(app_settings: Any = None) -> bool:
    """App Settings → Activity Feed → Show multi-month subs (default on)."""
    if app_settings is None:
        try:
            from modules import dataobjects

            sm = dataobjects.state_manager
            app_settings = sm.get_app_settings() if getattr(sm, "_initialized", False) else None
        except Exception:
            app_settings = None
    return bool(normalize_feed_settings(app_settings).get("sub_duration", True))


def broadcast_feed_settings(*, to: Optional[str] = None) -> Dict[str, Any]:
    """Send the feed settings to the docks (all, or one sid)."""
    settings = current_feed_settings()
    _emit_overlay_event("activity_feed_settings", {"settings": settings}, to=to)
    return settings


def apply_feed_settings_changed(changed_keys: Optional[set] = None) -> None:
    """A feed setting changed: repaint the tab and the docks straight away."""
    settings = broadcast_feed_settings()
    _push_slot("settings", settings)
    if changed_keys and "person_hours" in changed_keys:
        schedule_condensed_view_update("person_hours")
    elif changed_keys and "sub_duration" in changed_keys:
        # By person lines group and word multi-month subs on the server side.
        schedule_condensed_view_update("sub_duration")


def set_feed_setting(key: str, value: Any) -> bool:
    """Store one feed setting in App Settings (saved now) and apply it live."""
    if key not in FEED_SETTINGS:
        raise ValueError(f"unknown feed setting {key!r}")
    field = FEED_SETTINGS[key][0]
    stored = feed_setting_value(key, value)
    try:
        from modules import dataobjects

        sm = dataobjects.state_manager
        if not sm.update_app_setting(field, stored):
            return False
        saved = bool(sm.save_changes())
    except Exception as exc:
        logger.error("activity_feed: could not save %s: %s", field, exc, exc_info=True)
        return False
    apply_feed_settings_changed({key})
    return saved


# --------------------------------------------------------------------------- UI loop glue


def _run_on_ui_loop(fn: Callable[[], Any]) -> None:
    """Marshal UI mutations onto NiceGUI's loop and into a live client slot."""

    def _invoke() -> None:
        client = None
        host = _host
        if host is not None and _element_alive(host.host):
            try:
                client = host.host.client
            except Exception:
                client = None
        if client is None:
            try:
                from nicegui import Client

                for inst in list(Client.instances.values()):
                    if getattr(inst, "is_deleted", False):
                        continue
                    if getattr(inst, "has_socket_connection", False):
                        client = inst
                        break
            except Exception:
                client = None
        try:
            if client is not None:
                with client:
                    fn()
            else:
                fn()
        except Exception as exc:
            if _is_stale_client_error(exc):
                logger.debug("activity_feed UI callback on a deleted client: %s", exc)
                return
            logger.error("activity_feed UI callback failed: %s", exc, exc_info=True)

    run_on_ui_loop(_invoke)


def _alert_kind(alert_data) -> str:
    kind = str((alert_data or {}).get("type") or "").replace("_", " ").strip()
    return kind or "alert"


_REPLAY_REFUSALS = {
    "missing_id": (
        "It was saved without the details needed to play it again.",
        "Replay works for alerts that were stored with their settings. Newer alerts can be replayed.",
    ),
    "not_found": (
        "Its saved details were cleaned up or couldn't be read from the database.",
        "Older alerts are trimmed by the Activity Feed history settings. Check Settings → Database if this happens for recent alerts.",
    ),
    "no_config": (
        "There's no alert set up for this kind of event any more.",
        "Set one up in the Alerts tab, then replay it again.",
    ),
}


def replay_alert(alert_data):
    """Replay a played alert: original content, today's alert media (alertutils.replay_alert)."""
    try:
        from modules import alertutils

        alert_id = (alert_data or {}).get("alert_id")
        result = alertutils.replay_alert(alert_id)
        if not result.get("ok"):
            reason = result.get("reason") or "error"
            body, hint = _REPLAY_REFUSALS.get(
                reason,
                ("", "Try again. If it keeps failing, copy the details and check the log."),
            )
            notify(
                body,
                type="warning" if reason in _REPLAY_REFUSALS else "error",
                source="activity_feed",
                title="This alert can't be replayed" if reason in _REPLAY_REFUSALS else "Couldn't replay this alert",
                hint=hint,
                details=f"alert_id={alert_id} reason={reason}",
                actions=nav_actions_settings("Database") if reason == "not_found" else None,
            )
            return result

        from modules import web_engine

        if result.get("reason") == "queued" and getattr(web_engine, "ALERTS_PAUSED", False):
            notify(
                f"The {_alert_kind(alert_data)} replay will play when you resume alerts.",
                type="warning",
                source="activity_feed",
                title="Alerts are paused",
                hint="Resume alerts with the pause button at the top of the Activity Feed.",
                actions=nav_actions_main_tab("Activity Feed"),
            )
        elif getattr(web_engine, "web_engine_instance", None) is not None:
            # (If the overlay server is down, the alert queue reports the skipped alert.)
            notify("", type="info", source="activity_feed", title=f"Replaying the {_alert_kind(alert_data)} alert")
        return result

    except Exception as e:
        logger.error(f"Error replaying alert: {str(e)}", exc_info=True)
        notify(
            "",
            type="error",
            source="activity_feed",
            title="Couldn't replay this alert",
            hint="Try again. If it keeps failing, copy the details and check the log.",
            details=f"{type(e).__name__}: {e}",
        )
        return {"ok": False, "reason": "error", "new_alert_id": None}


def extract_username_from_message(message):
    """Extract username from alert message"""
    try:
        # Common patterns: "username just followed!", "username cheered 100 bits!", etc.
        import re

        # Look for text before common action words
        patterns = [
            r"^([^\s]+)\s+(?:just\s+)?(?:followed|subscribed|resubscribed|cheered|redeemed|donated|raided)",
            r"^([^\s]+)\s+",  # Fallback: first word
        ]

        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return match.group(1)

        return "Unknown"
    except Exception:
        return "Unknown"


def skip_alert(alert_data):
    """Skip the playing alert ({"type": "current"} / no id) or the alert with alert_id
    (playing: stopped now; queued: removed). Uses web_engine.skip_current_alert."""
    try:
        from modules import web_engine

        alert_data = alert_data or {}
        alert_id = alert_data.get("alert_id")
        if alert_data.get("type") == "current":
            alert_id = None
        result = web_engine.skip_current_alert(alert_id, source="app")
        if result.get("ok"):
            kind = _alert_kind(alert_data)
            # Rows send only an id: no type to name, so say it plainly ("the alert alert" otherwise).
            named = kind not in ("", "alert", "current")
            if result.get("reason") == "removed_from_queue":
                title = f"Removed the {kind} alert from the queue" if named else "Removed the alert from the queue"
            elif named:
                title = f"Skipped the {kind} alert"
            else:
                title = "Skipped the current alert"
            notify("", type="info", source="activity_feed", title=title)
        elif result.get("reason") == "not_playing":
            notify(
                "Nothing was skipped.",
                type="info",
                source="activity_feed",
                title="That alert isn't playing",
            )
        return result

    except Exception as e:
        logger.error(f"Error skipping alert: {str(e)}", exc_info=True)
        notify(
            "",
            type="error",
            source="activity_feed",
            title="Couldn't skip the alert",
            hint="Try again. If the alert is stuck, pause and resume alerts.",
            details=f"{type(e).__name__}: {e}",
        )
        return {"ok": False, "skipped_alert_id": None, "reason": "error"}


def format_watch_streak_message(
    username: str, streak_count: int, *, include_username: bool = True
) -> str:
    """Format watch streak text for activity feed, chat events, and condensed view."""
    name = username or "Someone"
    try:
        count = int(streak_count)
    except (TypeError, ValueError):
        count = 0
    if include_username:
        return f"{name} has watched for {count} consecutive streams!"
    return f"Watched for {count} consecutive streams!"


def format_modiversary_message(username: str, months: int) -> str:
    """Format modiversary text for the chat template and both activity feeds."""
    name = username or "Someone"
    try:
        count = int(months)
    except (TypeError, ValueError):
        count = 0
    unit = "month" if count == 1 else "months"
    return f"{name} has been a moderator for {count} {unit}!"


def format_raid_activity_message(
    username: str, raider_count: int, game_name: Optional[str] = None
) -> str:
    """Format raid text for activity feed, chat events, and condensed view."""
    name = username or "Someone"
    try:
        count = int(raider_count)
    except (TypeError, ValueError):
        count = 0
    try:
        count_str = f"{count:,}"
    except (ValueError, TypeError):
        count_str = str(count)
    base = f"{name} raided with {count_str} viewers"
    if game_name:
        return f"{base}, they were last playing {game_name}."
    return f"{base}!"


def _raid_category_suffix(game_name: Optional[str]) -> str:
    """Suffix for condensed raid lines when category is known."""
    if game_name:
        return f", they were last playing {game_name}."
    return "!"


# Extra fields add_alert_to_feed callers may pass (YouTube, donations, raids ...).
_PAYLOAD_EXTRA_FIELDS = frozenset(
    {
        "raider_count",
        "game_name",
        "source",
        "donation_amount",
        "currency",
        "display_amount",
        "donation_text",
        "donation_source",
        "member_level",
        "is_supersticker",
        "resub_month",
        "months",
        "duration_months",
        "amt_cheered",
        "alert_name",
    }
)


def build_activity_feed_alert_payload(
    alert_type,
    message,
    badge_type="follow",
    timestamp="now",
    tier=None,
    user_message=None,
    alert_id=None,
    point_cost=None,
    level=None,
    hype_train_type=None,
    gift_qty=None,
    recipient=None,
    streak_count=None,
    raider_count=None,
    game_name=None,
    username=None,
    **extra,
):
    """
    Build the same dict that is sent over ``activity_feed_alert`` WebSocket events.

    Shared by ``add_alert_to_feed`` and browser-source preview so payloads stay identical.
    The dict carries a precomputed ``row`` (see activity_feed_model.feed_row_model).
    """
    now = time.time()
    ts = (
        now
        if timestamp == "now"
        else alertutils.safe_alert_timestamp(timestamp, default=now)
    )
    alert_data = {
        "type": alert_type,
        "message": message,
        "badge_type": badge_type,
        "timestamp": ts,
        "created_at": now,
        "tier": tier,
        "user_message": user_message,
    }
    if username:
        alert_data["username"] = str(username)
    for key, value in extra.items():
        if key in _PAYLOAD_EXTRA_FIELDS and value is not None and isinstance(value, (str, int, float, bool)):
            alert_data[key] = value

    for key, value in (
        ("point_cost", point_cost),
        ("level", level),
        ("gift_qty", gift_qty),
        ("streak_count", streak_count),
        ("raider_count", raider_count),
        ("duration_months", extra.get("duration_months")),
    ):
        if value is not None:
            try:
                alert_data[key] = int(str(value).replace(",", ""))
            except (TypeError, ValueError):
                alert_data.pop(key, None)

    if hype_train_type:
        alert_data["hype_train_type"] = str(hype_train_type).strip().lower()
    if game_name:
        alert_data["game_name"] = str(game_name)
    if recipient is not None:
        recip = str(recipient).strip()
        if recip:
            alert_data["recipient"] = recip

    if alert_id:
        alert_data["alert_id"] = alert_id
        try:
            alertutils.alert_state_manager.initialize()
            stored_alert_data = alertutils.alert_state_manager.get_stored_alert_by_id(
                alert_id
            )
            if stored_alert_data:
                alert_data["stored_alert_data"] = stored_alert_data
                if isinstance(stored_alert_data, dict):
                    if stored_alert_data.get("username") and not alert_data.get("username"):
                        alert_data["username"] = stored_alert_data["username"]
                    for field in ("streak_count", "point_cost", "raider_count"):
                        value = stored_alert_data.get(field)
                        if value is not None and field not in alert_data:
                            try:
                                alert_data[field] = int(str(value).replace(",", ""))
                            except (TypeError, ValueError):
                                pass
                    for field in (
                        "game_name",
                        "resub_month",
                        "duration_months",
                        "months_prepaid",
                        "gift_qty",
                        "amt_cheered",
                        "tier",
                        "recipient",
                        "donation_amount",
                        "currency",
                        "original_amount",
                        "original_currency",
                        "donation_text",
                        "donation_source",
                        "alert_name",
                        "source",
                        "member_level",
                        "display_amount",
                        "is_supersticker",
                        "emotes",
                        "fragments",
                    ):
                        value = stored_alert_data.get(field)
                        if value is not None and alert_data.get(field) is None:
                            alert_data[field] = value
            else:
                logger.debug("No stored alert data found for alert_id %s", alert_id)
        except Exception as e:
            logger.warning("Error retrieving stored alert data for %s: %s", alert_id, e)

    if not alert_data.get("username"):
        parsed = extract_username_from_message(message)
        if parsed and parsed != "Unknown":
            alert_data["username"] = parsed

    chat_media = alertutils.resolve_chat_alert_media(
        alert_data, preview_placeholder=False
    )
    if chat_media:
        alert_data["chat_media"] = chat_media

    alert_data["row"] = feed_row_model(alert_data)
    return alert_data


def build_typed_activity_feed_preview_payload(alert_type: str) -> dict:
    """
    Build a fixed ``activity_feed_alert`` preview payload for one alert type.

    Media paths come from configured alerts via :func:`resolve_chat_alert_media`.
    Unconfigured alerts omit ``chat_media`` so the chat template falls back to simple text.
    """
    import random

    at = (alert_type or "follow").strip().lower()
    ts = str(int(time.time()))
    username = random.choice(
        (
            "PixelPanda",
            "NeonNova",
            "TacoTuesday",
            "ShinyHaxor",
            "5olid5nake",
        )
    )
    stored_alert_data: Dict[str, Any] = {
        "username": username,
        "alert_type": at,
    }

    if at == "follow":
        payload = build_activity_feed_alert_payload(
            "Follow", f"{username} just followed!", "follow", timestamp=ts
        )
    elif at == "sub":
        tier = random.choice((1, 2, 3))
        stored_alert_data["tier"] = tier
        payload = build_activity_feed_alert_payload(
            "Sub",
            f"{username} subscribed (Tier {tier})!",
            "sub",
            timestamp=ts,
            tier=tier,
            user_message="Thanks for an awesome stream!",
        )
    elif at == "resub":
        tier = random.choice((1, 2, 3))
        months = random.randint(2, 36)
        stored_alert_data["tier"] = tier
        stored_alert_data["resub_month"] = months
        payload = build_activity_feed_alert_payload(
            "Resub",
            f"{username} resubscribed for {months} months (Tier {tier})!",
            "resub",
            timestamp=ts,
            tier=tier,
            user_message=f"{months} months strong!",
            resub_month=months,
        )
    elif at in ("bit", "bits"):
        amount = random.choice((100, 500, 1000, 5000))
        stored_alert_data["amt_cheered"] = amount
        payload = build_activity_feed_alert_payload(
            "Bits",
            f"{username} cheered {amount} bits!",
            "bits",
            timestamp=ts,
            user_message="Loving the stream!",
            amt_cheered=amount,
        )
    elif at == "giftsub":
        tier = random.choice((1, 2, 3))
        qty = random.randint(1, 10)
        stored_alert_data["tier"] = tier
        stored_alert_data["gift_qty"] = qty
        recipient = None
        if qty == 1:
            recipient = random.choice(
                ("ViewerOne", "LuckyFan", "SubReceiver", "ChatBuddy")
            )
            stored_alert_data["recipient"] = recipient
            gift_message = (
                f"{username} gifted a Tier {tier} sub to {recipient}!"
            )
        else:
            gift_message = f"{username} gifted {qty} Tier {tier} subs!"
        payload = build_activity_feed_alert_payload(
            "Giftsub",
            gift_message,
            "giftsub",
            timestamp=ts,
            tier=tier,
            gift_qty=qty,
            recipient=recipient,
        )
    elif at == "raid":
        count = random.randint(25, 250)
        game = random.choice(
            ("Just Chatting", "Hades II", "Final Fantasy VII", "Hollow Knight")
        )
        stored_alert_data["raider_count"] = count
        stored_alert_data["game_name"] = game
        payload = build_activity_feed_alert_payload(
            "Raid",
            format_raid_activity_message(username, count, game),
            "raid",
            timestamp=ts,
            raider_count=count,
            game_name=game,
        )
    elif at == "donation":
        amount = round(random.uniform(5.0, 50.0), 2)
        stored_alert_data["donation_amount"] = amount
        payload = build_activity_feed_alert_payload(
            "Donation",
            f"{username} donated ${amount:.2f}!",
            "donation",
            timestamp=ts,
            user_message="Keep up the great work!",
            donation_amount=amount,
            currency="USD",
        )
    elif at in ("point", "points"):
        reward = random.choice(
            ("Hydrate Reminder", "Highlight My Message", "Pick the next song")
        )
        cost = random.choice((100, 500, 1100, 2500))
        stored_alert_data["point_cost"] = cost
        payload = build_activity_feed_alert_payload(
            "Points",
            f"{username} redeemed '{reward}'!",
            "points",
            timestamp=ts,
            point_cost=cost,
        )
    elif at == "streak":
        count = random.randint(2, 24)
        stored_alert_data["streak_count"] = count
        payload = build_activity_feed_alert_payload(
            "Streak",
            format_watch_streak_message(username, count),
            "streak",
            timestamp=ts,
            streak_count=count,
        )
    elif at in ("hype_train", "hypetrain"):
        hype_level = random.randint(1, 5)
        train_type = random.choice(("regular", "treasure", "golden_kappa"))
        stored_alert_data["hype_train_type"] = train_type
        stored_alert_data["level"] = hype_level
        payload = build_activity_feed_alert_payload(
            "Hype Train",
            f"Hype Train started by {username}! Level {hype_level}.",
            "hype_train",
            timestamp=ts,
            level=hype_level,
            hype_train_type=train_type,
        )
    else:
        payload = build_activity_feed_alert_payload(
            "Follow", f"{username} just followed!", "follow", timestamp=ts
        )

    payload["username"] = username
    payload["stored_alert_data"] = stored_alert_data
    payload.pop("chat_media", None)

    chat_media = alertutils.resolve_chat_alert_media(
        payload, preview_placeholder=False
    )
    if chat_media:
        payload["chat_media"] = chat_media
        stored_alert_data.update(chat_media)
        payload["stored_alert_data"] = stored_alert_data
    payload["row"] = feed_row_model(payload)
    return payload


def iter_activity_feed_preview_payloads():
    """Sample alerts matching production-style messages (Custom Sources iframe only).

    Produces a varied stream of alerts whose ``type`` and ``badge_type`` values match what
    the live ``add_alert_to_feed`` callers emit, so the preview matches the real feed.
    """
    import random

    ts = str(int(time.time()))
    usernames = (
        "PixelPanda", "NeonNova", "TacoTuesday", "ShinyHaxor",
        "MidnightMango", "EmberFox", "GlitchWizard", "VelvetVortex",
        "RetroRogue", "CelestialCat", "BlueberryBoss", "QuantumQuokka",
        "FrostyFlame", "LunarLynx", "RubyRanger", "TwilightTitan",
    )

    def pick():
        return random.choice(usernames)

    follow_user = pick()
    yield build_activity_feed_alert_payload(
        "Follow", f"{follow_user} just followed!", "follow", timestamp=ts
    )

    sub_user = pick()
    sub_tier = random.choice((1, 2, 3))
    yield build_activity_feed_alert_payload(
        "Sub",
        f"{sub_user} subscribed (Tier {sub_tier})!",
        "sub",
        timestamp=ts,
        tier=sub_tier,
        user_message=None,
    )

    resub_user = pick()
    resub_tier = random.choice((1, 2, 3))
    resub_months = random.randint(2, 36)
    yield build_activity_feed_alert_payload(
        "Resub",
        f"{resub_user} resubscribed for {resub_months} months (Tier {resub_tier})!",
        "resub",
        timestamp=ts,
        tier=resub_tier,
        user_message=f"{resub_months} months strong!",
        resub_month=resub_months,
    )

    bits_user = pick()
    bits_amount = random.choice((100, 200, 500, 1000, 5000))
    yield build_activity_feed_alert_payload(
        "Bits",
        f"{bits_user} cheered {bits_amount} bits!",
        "bits",
        timestamp=ts,
        user_message="Loving the stream!",
        amt_cheered=bits_amount,
    )

    raid_user = pick()
    raid_count = random.randint(5, 2500)
    raid_game = random.choice(
        ("Just Chatting", "Final Fantasy VII", "Hollow Knight: Silksong", "League of Legends")
    )
    yield build_activity_feed_alert_payload(
        "Raid",
        format_raid_activity_message(raid_user, raid_count, raid_game),
        "raid",
        timestamp=ts,
        raider_count=raid_count,
        game_name=raid_game,
    )

    gift_user = pick()
    gift_qty = random.randint(1, 25)
    gift_tier = random.choice((1, 2, 3))
    gift_recipient = None
    if gift_qty == 1:
        gift_recipient = pick()
        gift_msg = f"{gift_user} gifted a Tier {gift_tier} sub to {gift_recipient}!"
    else:
        gift_msg = f"{gift_user} gifted {gift_qty} Tier {gift_tier} subs!"
    yield build_activity_feed_alert_payload(
        "Giftsub",
        gift_msg,
        "giftsub",
        timestamp=ts,
        tier=gift_tier,
        gift_qty=gift_qty,
        recipient=gift_recipient,
    )

    points_user = pick()
    point_reward = random.choice((
        "Hydrate Reminder",
        "Highlight My Message",
        "Pick the next song",
        "Take a 5min break",
    ))
    yield build_activity_feed_alert_payload(
        "Points",
        f"{points_user} redeemed '{point_reward}'!",
        "points",
        timestamp=ts,
        user_message=None,
        point_cost=random.choice((100, 250, 500, 1100, 2500, 5000)),
    )

    streak_user = pick()
    streak_count = random.randint(2, 50)
    yield build_activity_feed_alert_payload(
        "Streak",
        format_watch_streak_message(streak_user, streak_count),
        "streak",
        timestamp=ts,
        streak_count=streak_count,
    )

    hype_user = pick()
    hype_level = random.randint(1, 5)
    hype_train_type = random.choice(("regular", "treasure", "golden_kappa"))
    yield build_activity_feed_alert_payload(
        "Hype Train",
        f"Hype Train started by {hype_user}! Level {hype_level}.",
        "hype_train",
        timestamp=ts,
        level=hype_level,
        hype_train_type=hype_train_type,
    )

    hype_end_level = random.randint(1, 5)
    hype_end_type = random.choice(("regular", "treasure", "golden_kappa"))
    yield build_activity_feed_alert_payload(
        "Hype Train",
        f"Hype Train ended at Level {hype_end_level}!",
        "hype_train",
        timestamp=ts,
        level=hype_end_level,
        hype_train_type=hype_end_type,
    )


def add_alert_to_feed(
    alert_type,
    message,
    badge_type="follow",
    timestamp="now",
    tier=None,
    user_message=None,
    alert_id=None,
    point_cost=None,
    level=None,
    hype_train_type=None,
    gift_qty=None,
    recipient=None,
    always_broadcast_html=False,
    streak_count=None,
    username=None,
    **kwargs,
):
    """Add a new event to the Activity Feed (tab and OBS dock).

    Args:
        alert_type (str): Display type ("Follow", "Bits", "Super Chat" ...)
        message (str): The alert message text
        badge_type (str): Event key used for the icon / colour ("follow", "superchat" ...)
        timestamp (str or float): "now" or a Unix timestamp
        tier (int, optional): Subscription tier (1-3)
        user_message (str, optional): The viewer's message
        alert_id (str, optional): Stored alert id (replay, queue matching)
        username (str, optional): Who triggered it (YouTube passes this; otherwise parsed)
        always_broadcast_html (bool): Kept for callers; the dock always gets the alert.
        **kwargs: Extra known fields (raider_count, game_name, donation_amount, currency,
            source ...). Unknown keys are ignored so callers can't break the feed.
    """
    alert_data = build_activity_feed_alert_payload(
        alert_type,
        message,
        badge_type=badge_type,
        timestamp=timestamp,
        tier=tier,
        user_message=user_message,
        alert_id=alert_id,
        point_cost=point_cost,
        level=level,
        hype_train_type=hype_train_type,
        gift_qty=gift_qty,
        recipient=recipient,
        streak_count=streak_count,
        username=username,
        **kwargs,
    )
    seq = _next_seq()
    alert_data["seq"] = seq
    if isinstance(alert_data.get("row"), dict):
        alert_data["row"]["seq"] = seq

    # Into the shared list first, then tell the dock: a resync in between can't drop it.
    alert_event_handler.process_alert_immediately(alert_data)

    try:
        from modules import web_engine

        engine = getattr(web_engine, "web_engine_instance", None)
        if engine:
            engine.activity_feed_alert(serialize_live_alert(alert_data))
    except Exception as e:
        logger.error("Error sending alert to the activity feed dock: %s", e, exc_info=True)


def update_alert_in_feed(alert_id: Optional[str], **fields: Any) -> bool:
    """Change fields of an alert already in the live feed (tab and docks repaint in place).

    Used when a detail arrives after the alert, such as the months of a gifted sub.
    Returns False when the alert isn't in the live feed.
    """
    if not alert_id:
        return False
    changes = {k: v for k, v in fields.items() if v is not None}
    if not changes:
        return False
    with _live_lock:
        entry = next(
            (a for a in activity_feed_state.live_alerts if isinstance(a, dict) and a.get("alert_id") == alert_id),
            None,
        )
        if entry is None:
            return False
        entry.update(changes)
        stored = entry.get("stored_alert_data")
        if isinstance(stored, dict):
            stored = dict(stored)
            stored.update({k: v for k, v in changes.items() if k != "message"})
            entry["stored_alert_data"] = stored
        entry["row"] = feed_row_model(entry)
    _schedule_live_push()
    try:
        # The dock replaces its live list in place (no new row, order kept).
        broadcast_activity_feed_view(view_manager.snapshot(), include_live_alerts=True)
        if view_mode(view_manager.snapshot()) == "person":
            schedule_condensed_view_update("alert_updated")
    except Exception as exc:
        logger.debug("activity_feed: could not send the updated alert: %s", exc)
    return True


def start_alert_processor():
    """Kept for callers: new alerts are handled as they arrive."""
    logger.debug("Activity feed is event driven - nothing to start")


def stop_alert_processor():
    """Stop accepting feed updates (shutdown)."""
    alert_event_handler.shutdown()
    _stop_timers()
    logger.debug("Stopped the activity feed")


def force_ui_refresh():
    """Repaint the feed from state (kept for callers)."""
    push_all_slots()


# --------------------------------------------------------------------------- stored alerts

_STORED_TYPE_DISPLAY = {
    "bit": "Bits",
    "sub": "Sub",
    "resub": "Resub",
    "giftsub": "Giftsub",
    "follow": "Follow",
    "point": "Points",
    "raid": "Raid",
    "donation": "Donation",
    "modiversary": "Modiversary",
    "streak": "Streak",
    "hype_train_start": "Hype Train",
    "hype_train_progress": "Hype Train",
    "hype_train_end": "Hype Train",
}
# Stored alert_type -> the badge key live alerts use.
_STORED_TYPE_BADGE = {
    "bit": "bits",
    "point": "points",
    "hype_train_start": "hype_train",
    "hype_train_progress": "hype_train",
    "hype_train_end": "hype_train",
}


def convert_stored_alert_to_feed_format(stored_alert_data):
    """Convert one stored alert (AlertStorage) to the activity feed format.

    Uses the same type keys as live alerts, so history rows get the same icons, filters
    and wording as the live feed.
    """
    try:
        if not isinstance(stored_alert_data, dict):
            return None
        original_type = str(stored_alert_data.get("alert_type") or "unknown")
        display_type = _STORED_TYPE_DISPLAY.get(original_type, original_type.replace("_", " ").title())
        badge = _STORED_TYPE_BADGE.get(original_type, original_type)
        source = str(stored_alert_data.get("source") or "").lower()

        if source == "youtube":
            youtube = {
                "sub": ("Membership", "membership"),
                "resub": ("Member Milestone", "member_milestone"),
                "giftsub": ("Gift Membership", "gift_membership"),
            }
            if original_type in youtube:
                display_type, badge = youtube[original_type]
            elif original_type == "donation":
                if stored_alert_data.get("is_supersticker") or stored_alert_data.get("supersticker"):
                    display_type, badge = "Super Sticker", "supersticker"
                else:
                    display_type, badge = "Super Chat", "superchat"

        username = stored_alert_data.get("username") or ""
        if not username:
            log_fn = logger.debug if original_type.startswith("hype_train") else logger.warning
            log_fn("Alert %s missing username", stored_alert_data.get("alert_id", "unknown"))
            username = "Unknown User"

        def _int(value, default=0):
            try:
                return int(str(value).replace(",", ""))
            except (TypeError, ValueError):
                return default

        show_duration = show_sub_duration()
        duration_months = alert_duration_months(stored_alert_data)
        message = ""
        if original_type == "bit":
            message = f"{username} cheered {_int(stored_alert_data.get('amt_cheered')):,} bits!"
        elif original_type == "sub":
            tier = stored_alert_data.get("tier", 1)
            resub_month = _int(stored_alert_data.get("resub_month"))
            if source == "youtube":
                level = stored_alert_data.get("member_level") or ""
                message = f"{username} became a channel member" + (f" ({level})!" if level else "!")
            elif resub_month > 1:
                display_type, badge = "Resub", "resub"
                message = resub_message(username, resub_month, None, duration_months, show_duration=show_duration)
            else:
                message = sub_message(username, tier, duration_months, show_duration=show_duration)
        elif original_type == "resub":
            resub_month = (
                _int(stored_alert_data.get("resub_month"))
                or _int(stored_alert_data.get("months"))
                or _int(stored_alert_data.get("total_months"))
                or 1
            )
            if source == "youtube":
                level = stored_alert_data.get("member_level") or ""
                message = (
                    f"{username} reached a member milestone ({resub_month} months"
                    + (f", {level}" if level else "")
                    + ")!"
                )
            else:
                message = resub_message(username, resub_month, None, duration_months, show_duration=show_duration)
        elif original_type == "giftsub":
            gift_qty = _int(stored_alert_data.get("gift_qty"), 1) or 1
            tier = stored_alert_data.get("tier", 1)
            recipient = str(stored_alert_data.get("recipient") or "").strip()
            if source == "youtube":
                message = f"{username} gifted {gift_qty} memberships!"
            else:
                message = giftsub_message(
                    username, tier, gift_qty, recipient, duration_months, show_duration=show_duration
                )
        elif original_type == "follow":
            message = f"{username} just followed!"
        elif original_type == "point":
            alert_name = stored_alert_data.get("alert_name", "Unknown Reward")
            message = f"{username} redeemed '{alert_name}'!"
        elif original_type == "raid":
            message = format_raid_activity_message(
                username,
                _int(stored_alert_data.get("raider_count")),
                stored_alert_data.get("game_name"),
            )
        elif original_type == "donation":
            from .activity_feed_model import format_money

            amount = stored_alert_data.get("donation_amount", 0)
            if source == "youtube":
                display_amount = stored_alert_data.get("display_amount")
                amount_str = str(display_amount) if display_amount else format_money(
                    amount, stored_alert_data.get("currency")
                )
                label = "Super Sticker" if badge == "supersticker" else "Super Chat"
                message = f"{username} sent a {label} ({amount_str})!"
            else:
                text = stored_alert_data.get("donation_text") or format_money(
                    amount, stored_alert_data.get("currency")
                )
                message = f"{username} donated {text}!"
        elif original_type == "streak":
            message = format_watch_streak_message(username, stored_alert_data.get("streak_count", 0))
        elif original_type == "modiversary":
            message = format_modiversary_message(username, stored_alert_data.get("months", 0))
        elif original_type.startswith("hype_train"):
            level = stored_alert_data.get("hype_train_level") or stored_alert_data.get("level") or 1
            if original_type == "hype_train_end":
                message = f"Hype Train ended at Level {level}!"
            else:
                message = f"Hype Train Level {level}!"
        else:
            message = f"{username} triggered {display_type}!"

        ts = alertutils.safe_alert_timestamp(stored_alert_data.get("timestamp"), default=time.time())
        result = {
            "type": display_type,
            "message": message,
            "badge_type": badge,
            "timestamp": ts,
            "created_at": ts,
            "tier": stored_alert_data.get("tier"),
            "user_message": stored_alert_data.get("message"),
            "is_restored": True,
            "alert_id": stored_alert_data.get("alert_id"),
            "username": username,
            "source": source or stored_alert_data.get("source"),
            # The full stored alert, used by replay and the condensed grouping.
            "stored_alert_data": stored_alert_data,
        }
        if duration_months and source != "youtube" and original_type in ("sub", "resub", "giftsub"):
            result["duration_months"] = duration_months
        if original_type == "giftsub":
            gift_qty = stored_alert_data.get("gift_qty")
            if gift_qty is not None:
                result["gift_qty"] = gift_qty
            recipient = str(stored_alert_data.get("recipient") or "").strip()
            if recipient:
                result["recipient"] = recipient
        if original_type == "raid":
            result["raider_count"] = _int(stored_alert_data.get("raider_count"))
            if stored_alert_data.get("game_name"):
                result["game_name"] = stored_alert_data.get("game_name")
        result["row"] = feed_row_model(result)
        return result

    except Exception as e:
        logger.error("Error converting stored alert to feed format: %s", e, exc_info=True)
        return None


def load_restored_alerts(page=1):
    """Load one page of stored alerts (newest first), clamped to pages that exist.

    Returns (feed alerts, pagination info). Each alert carries a ``row`` whose ``seq`` is
    its position in the stored history (oldest = #1).
    """
    try:
        from modules import dataobjects

        app_settings = dataobjects.state_manager.get_app_settings()
        limit = max(1, int(app_settings.activity_feed_limit or 20))
        max_pages = max(1, int(app_settings.activity_feed_max_pages or 1))
        max_total_alerts = max_pages * limit
        activity_feed_state.pagination_limit = limit
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = 1
        page = min(max(page, 1), max_pages)

        alertutils.alert_state_manager.initialize()
        result = alertutils.alert_state_manager.get_limited_stored_alerts_paginated(
            page=page, limit=limit, max_total_alerts=max_total_alerts
        )
        total_pages = max(1, min(int(result.get("total_pages") or 1), max_pages))
        if page > total_pages:
            # History shrank (trim, fewer pages allowed): show the last page that exists.
            page = total_pages
            result = alertutils.alert_state_manager.get_limited_stored_alerts_paginated(
                page=page, limit=limit, max_total_alerts=max_total_alerts
            )
        total_count = int(result.get("total_count") or 0)
        activity_feed_state.current_page = page
        activity_feed_state.total_pages = total_pages

        restored_alerts = []
        for index, stored_alert in enumerate(result.get("alerts") or []):
            feed_alert = convert_stored_alert_to_feed_format(stored_alert)
            if feed_alert:
                seq = total_count - (page - 1) * limit - index
                feed_alert["row"] = feed_row_model(feed_alert, seq=seq if seq > 0 else None)
                restored_alerts.append(feed_alert)

        info = {
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "limit": limit,
        }
        return restored_alerts, info

    except Exception as e:
        logger.error("Error loading restored alerts: %s", e, exc_info=True)
        notify(
            "The History view may look empty even though your history is still saved.",
            type="warning",
            source="activity_feed",
            title="Couldn't load earlier alerts",
            hint="Check your database connection in Settings → Database, then open History again.",
            details=f"page={page}: {type(e).__name__}: {e}",
            actions=nav_actions_settings("Database"),
            dedupe_key="activity_feed:history_failed",
            dedupe_cooldown_sec=60.0,
        )
        return [], {
            "page": 1,
            "total_count": 0,
            "total_pages": 1,
            "has_next": False,
            "has_prev": False,
            "error": str(e),
        }


# Stored alerts considered for "By person" before the time window is applied. Far more
# than any feed page; the window (hours) is what actually limits the list.
_PERSON_SCAN_LIMIT = 20000


def load_restored_alerts_for_time_window(cutoff_time: float):
    """Load stored alerts newer than cutoff_time (across all of storage, not N pages)."""
    try:
        manager = alertutils.alert_state_manager
        manager.initialize()
        since = getattr(manager, "get_stored_alerts_since", None)
        if callable(since):
            rows = since(cutoff_time, limit=_PERSON_SCAN_LIMIT) or []
        else:
            stored = manager.get_limited_stored_alerts_from_firebase(_PERSON_SCAN_LIMIT) or {}
            rows = [dict(data, alert_id=alert_id) for alert_id, data in stored.items() if isinstance(data, dict)]
        restored_alerts = []
        for row in rows:
            if not isinstance(row, dict) or _alert_timestamp_for_cutoff(row) < cutoff_time:
                continue
            feed_alert = convert_stored_alert_to_feed_format(dict(row))
            if feed_alert:
                restored_alerts.append(feed_alert)
        restored_alerts.sort(key=_alert_timestamp_for_cutoff, reverse=True)
        return restored_alerts, len(restored_alerts)
    except Exception as e:
        logger.error("Error loading restored alerts for time window: %s", e, exc_info=True)
        return [], 0


def _alert_timestamp_for_cutoff(alert_data: Dict[str, Any]) -> float:
    for key in ("created_at", "timestamp"):
        val = alert_data.get(key)
        if val is None or val == "":
            continue
        if val == "now":
            return time.time()
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return 0.0


def parse_condensed_historical_hours(value: Any, default: int = 12) -> int:
    """Coerce condensed-view hours from config/socket payloads to a positive int."""
    try:
        hours = int(float(value))
    except (TypeError, ValueError):
        hours = default
    if hours < 1:
        return default
    return hours


def _normalize_condensed_alert_username(alert_data: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow copy with username resolved for condensed grouping."""
    result = dict(alert_data)
    if not result.get("username"):
        username = extract_username_from_message(result.get("message", ""))
        if username and username != "Unknown":
            result["username"] = username
    return result


def collect_alerts_for_condensed_view(cutoff_time: float):
    """Merge stored and live alerts for condensed view, deduped by alert_id."""
    restored_alerts, historical_count = load_restored_alerts_for_time_window(
        cutoff_time
    )

    by_id: Dict[str, Dict[str, Any]] = {}
    no_id_alerts: List[Dict[str, Any]] = []

    for alert in restored_alerts:
        normalized = _normalize_condensed_alert_username(alert)
        alert_id = normalized.get("alert_id")
        if alert_id:
            by_id[alert_id] = normalized
        else:
            no_id_alerts.append(normalized)

    live_in_window = 0
    for live_alert in live_alerts_snapshot():
        if _alert_timestamp_for_cutoff(live_alert) < cutoff_time:
            continue
        live_in_window += 1
        normalized = _normalize_condensed_alert_username(live_alert)
        alert_id = normalized.get("alert_id")
        if alert_id:
            by_id[alert_id] = normalized
        else:
            no_id_alerts.append(normalized)

    alerts_to_process = list(by_id.values()) + no_id_alerts
    alerts_to_process.sort(key=_alert_timestamp_for_cutoff, reverse=True)
    return alerts_to_process, historical_count, live_in_window


def _load_condensed_historical_hours() -> int:
    """The old dock setting condense_historical_hours (activity_feed.json), for migration."""
    condense_historical_hours = 12
    try:
        from modules.template_config_parser import TemplateConfigParser

        config_data = TemplateConfigParser().load_config("activity_feed")
        if config_data and "elements" in config_data:
            for element in config_data["elements"]:
                if element.get("type") == "section":
                    continue
                if element.get("id") == "condense_historical_hours":
                    condense_historical_hours = parse_condensed_historical_hours(
                        element.get("value")
                    )
                    break
    except Exception as exc:
        logger.debug("Could not load template config for condensed view: %s", exc)
    return condense_historical_hours


def _person_hours() -> int:
    return parse_condensed_historical_hours(current_feed_settings().get("person_hours", 12))


def _accumulate_condensed_alert(
    alert_data: Dict[str, Any],
    user_alerts: Dict[str, Dict[str, Any]],
    filter_state: Dict[str, Any],
    type_to_filter: Dict[str, str],
    search: Optional[str] = None,
    *,
    show_duration: bool = True,
) -> str:
    """Fold one alert into condensed grouping. Returns ok, excluded, or unknown.

    With ``show_duration`` multi-month subs and gifts (``duration_months`` > 1) get their
    own group per length, so "Gifted 5 Tier 1 subs · 3 months" never mixes lengths.
    """
    import re

    alert_type = alert_data.get("type", "").lower()
    badge_type = alert_data.get("badge_type", "").lower()

    # Exclude hype train only (channel points are included and grouped by reward)
    if alert_type == "hype train" or badge_type == "hype_train":
        return "excluded"

    display_type = alert_data.get("type", "")
    filter_key = type_to_filter.get(display_type)
    if not (
        filter_state.get("all", True)
        or (filter_key and filter_state.get(filter_key, True))
    ):
        return "excluded"
    if search is None:
        search_match = _alert_matches_search(alert_data)
    else:
        search_match = _alert_matches_search(alert_data, str(search).strip().lower())
    if not search_match:
        return "excluded"

    username = alert_data.get("username")
    if not username:
        username = extract_username_from_message(alert_data.get("message", ""))

    if not username or username == "Unknown":
        return "unknown"

    if username not in user_alerts:
        user_alerts[username] = {}

    def _num(text: str) -> int:
        return int(str(text).replace(",", ""))

    grouping_key = alert_type
    reward_name = None
    stored_data = alert_data.get("stored_alert_data")
    if not isinstance(stored_data, dict):
        stored_data = {}
    sub_duration = 0

    if alert_type in ["sub", "resub", "giftsub"]:
        tier = alert_data.get("tier", 1)
        grouping_key = f"{alert_type}_tier{tier}"

        if alert_type in ["resub", "giftsub"]:
            duration_months = 1
            if stored_data:
                duration_months = (
                    stored_data.get("resub_month", 0)
                    or stored_data.get("months", 0)
                    or stored_data.get("total_months", 0)
                    or 1
                )
            else:
                message = alert_data.get("message", "")
                month_match = re.search(r"(\d+)\s*months?", message, re.IGNORECASE)
                if month_match:
                    duration_months = int(month_match.group(1))

            if duration_months > 1:
                grouping_key = f"{alert_type}_tier{tier}_multi_month"
            else:
                grouping_key = f"{alert_type}_tier{tier}_1month"
        if show_duration:
            sub_duration = alert_duration_months(alert_data)
            if sub_duration:
                grouping_key = f"{grouping_key}_dur{sub_duration}"
    elif alert_type in (
        "membership",
        "member milestone",
        "gift membership",
        "super chat",
        "super sticker",
    ) or badge_type in (
        "membership",
        "member_milestone",
        "gift_membership",
        "superchat",
        "supersticker",
    ):
        if badge_type in (
            "membership",
            "member_milestone",
            "gift_membership",
            "superchat",
            "supersticker",
        ):
            grouping_key = badge_type
        else:
            grouping_key = {
                "membership": "membership",
                "member milestone": "member_milestone",
                "gift membership": "gift_membership",
                "super chat": "superchat",
                "super sticker": "supersticker",
            }[alert_type]
    elif alert_type in ("point", "points") or badge_type in ("point", "points"):
        reward_name = stored_data.get("alert_name")
        if not reward_name:
            message = alert_data.get("message", "")
            reward_match = re.search(r"redeemed\s+'(.+?)'!", message, re.IGNORECASE)
            if reward_match:
                reward_name = reward_match.group(1)
        if not reward_name:
            reward_name = "Unknown Reward"
        grouping_key = f"points:{reward_name}"

    if grouping_key not in user_alerts[username]:
        user_alerts[username][grouping_key] = {
            "count": 0,
            "total_amount": 0,
            "tier": alert_data.get("tier", 1),
            "months": 0,
            "original_type": alert_type,
            "last_ts": 0.0,
        }
        if reward_name is not None:
            user_alerts[username][grouping_key]["reward_name"] = reward_name
        if sub_duration:
            user_alerts[username][grouping_key]["duration_months"] = sub_duration

    group = user_alerts[username][grouping_key]
    group["count"] += 1
    group["last_ts"] = max(float(group.get("last_ts") or 0), _alert_timestamp_for_cutoff(alert_data))

    message = alert_data.get("message", "")

    if alert_type in ("bit", "bits"):
        amt_cheered = stored_data.get("amt_cheered") if stored_data else None
        if amt_cheered:
            group["total_amount"] += _num(amt_cheered)
        else:
            bit_match = re.search(r"([\d,]+)\s*bits?", message, re.IGNORECASE)
            if bit_match:
                group["total_amount"] += _num(bit_match.group(1))
    elif alert_type in ("donation", "super chat", "super sticker"):
        donation_amount = stored_data.get("donation_amount") if stored_data else None
        if donation_amount is None:
            donation_amount = alert_data.get("donation_amount")
        if donation_amount:
            group["total_amount"] += float(donation_amount)
        else:
            donation_match = re.search(r"donated\s+\$?([\d,]+(?:\.\d{2})?)", message, re.IGNORECASE)
            if donation_match:
                group["total_amount"] += float(donation_match.group(1).replace(",", ""))
    elif alert_type in ("giftsub", "gift membership"):
        gift_qty = stored_data.get("gift_qty") if stored_data else None
        if gift_qty is None:
            gift_qty = alert_data.get("gift_qty")
        if gift_qty:
            group["total_amount"] += _num(gift_qty)
        else:
            gift_match = re.search(r"gifted\s+([\d,]+)", message, re.IGNORECASE)
            if gift_match:
                group["total_amount"] += _num(gift_match.group(1))
    elif alert_type == "resub":
        resub_month = None
        if stored_data:
            resub_month = (
                stored_data.get("resub_month")
                or stored_data.get("months")
                or stored_data.get("total_months")
            )
        if resub_month:
            months = int(resub_month)
        else:
            month_match = re.search(r"(\d+)\s*months?", message, re.IGNORECASE)
            months = int(month_match.group(1)) if month_match else 1
        if months > group["months"]:
            group["months"] = months
    elif alert_type == "raid":
        raider_count = alert_data.get("raider_count")
        if raider_count is None and stored_data:
            raider_count = stored_data.get("raider_count")
        if raider_count is not None:
            viewers = _num(raider_count)
        else:
            raid_match = re.search(r"([\d,]+)\s*viewers?", message, re.IGNORECASE)
            viewers = _num(raid_match.group(1)) if raid_match else 0
        raid_game = alert_data.get("game_name")
        if not raid_game and stored_data:
            raid_game = stored_data.get("game_name")
        if viewers > group["total_amount"]:
            group["total_amount"] = viewers
            if raid_game:
                group["game_name"] = raid_game
    elif alert_type == "streak":
        streak_count_val = alert_data.get("streak_count")
        if streak_count_val is None and stored_data:
            streak_count_val = stored_data.get("streak_count")
        if streak_count_val is not None:
            sc = int(streak_count_val)
        else:
            streak_match = re.search(r"(\d+)\s*consecutive streams", message, re.IGNORECASE)
            sc = int(streak_match.group(1)) if streak_match else 0
        if sc > group["total_amount"]:
            group["total_amount"] = sc

    return "ok"


def _alert_search_query() -> str:
    """Lowercased shared search text. Empty shows all."""
    return (getattr(activity_feed_state, "search_text", "") or "").strip().lower()


def _alert_matches_search(alert_data: Dict[str, Any], query: Optional[str] = None) -> bool:
    """True when username, message, or the user's note contains the query."""
    if query is None:
        query = _alert_search_query()
    if not query:
        return True
    haystack = " ".join(
        str(alert_data.get(key) or "")
        for key in ("username", "message", "user_message")
    ).lower()
    return query in haystack


def _group_alerts_for_condensed(
    alerts_to_process: List[Dict[str, Any]],
    filter_state: Optional[Dict[str, Any]] = None,
    search: Optional[str] = None,
    show_duration: Optional[bool] = None,
):
    """Group alerts by user and type for the condensed view (CPU-only)."""
    if show_duration is None:
        show_duration = show_sub_duration()
    user_alerts: Dict[str, Dict[str, Any]] = {}
    excluded_count = 0
    unknown_username_count = 0
    if filter_state is None:
        filter_state = dict(activity_feed_state.filter_state)
    type_to_filter = activity_feed_state.alert_type_to_filter

    for alert_data in alerts_to_process:
        if not isinstance(alert_data, dict):
            excluded_count += 1
            continue
        try:
            result = _accumulate_condensed_alert(
                alert_data, user_alerts, filter_state, type_to_filter, search,
                show_duration=show_duration,
            )
        except Exception as exc:
            excluded_count += 1
            logger.debug(
                "Skipping condensed grouping for alert_id=%s: %s",
                alert_data.get("alert_id"),
                exc,
            )
            continue
        if result == "excluded":
            excluded_count += 1
        elif result == "unknown":
            unknown_username_count += 1

    return user_alerts, excluded_count, unknown_username_count


def _person_kind(grouping_key: str, data: Dict[str, Any]) -> str:
    original = str(data.get("original_type") or "")
    if grouping_key.startswith("points:") or original in ("point", "points"):
        return "points"
    for prefix, kind in (
        ("sub_tier", "sub"),
        ("resub_tier", "resub"),
        ("giftsub_tier", "giftsub"),
        ("member_milestone", "milestone"),
        ("gift_membership", "giftmember"),
        ("membership", "member"),
        ("superchat", "superchat"),
        ("supersticker", "supersticker"),
    ):
        if grouping_key.startswith(prefix):
            return kind
    return {
        "follow": "follow",
        "bits": "bits",
        "bit": "bits",
        "donation": "donation",
        "raid": "raid",
        "streak": "streak",
        "modiversary": "modiversary",
    }.get(grouping_key, "alert")


def person_line(grouping_key: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """One "By person" line: kind, readable text without the count, and the count."""
    from .activity_feed_model import KIND_META, format_money, sub_duration_caption, sub_duration_kind

    kind = _person_kind(grouping_key, data)
    count = int(data.get("count") or 1)
    total = data.get("total_amount") or 0
    tier = data.get("tier") or 1
    months = int(data.get("months") or 0)
    amount = 0.0
    biggest = 0
    if kind == "sub":
        text = f"Subscribed (Tier {tier})"
    elif kind == "resub":
        text = f"Resubscribed for {months} months (Tier {tier})" if months > 1 else f"Resubscribed (Tier {tier})"
        biggest = months
    elif kind == "giftsub":
        gifts = int(total or count)
        text = f"Gifted {gifts:,} Tier {tier} sub" + ("s" if gifts != 1 else "")
        amount = float(total or count)
        count = 1 if total else count
    elif kind == "follow":
        text = "Followed"
    elif kind == "bits":
        text = f"Cheered {int(total):,} bits"
        amount = float(total)
    elif kind == "donation":
        text = f"Tipped {format_money(total)}"
        amount = float(total)
    elif kind in ("superchat", "supersticker"):
        label = "Super Sticker" if kind == "supersticker" else "Super Chat"
        text = f"Sent a {label} ({format_money(total)})" if total else f"Sent a {label}"
        amount = float(total)
    elif kind == "member":
        text = "Became a member"
    elif kind == "milestone":
        text = "Reached a member milestone"
    elif kind == "giftmember":
        text = f"Gifted {int(total or count):,} memberships"
        amount = float(total or count)
        count = 1 if total else count
    elif kind == "raid":
        game = data.get("game_name")
        text = f"Raided with {int(total):,} viewers" + (f" · {game}" if game else "")
        biggest = int(total)
    elif kind == "streak":
        text = f"Watched {int(total)} streams in a row" if total else "Watch streak"
        biggest = int(total)
    elif kind == "points":
        reward = data.get("reward_name") or grouping_key.split(":", 1)[-1] or "a reward"
        text = f"Redeemed “{reward}”"
    else:
        text = grouping_key.replace("_", " ").title()
    duration = int(data.get("duration_months") or 0)
    if duration > 1 and kind in ("sub", "resub", "giftsub"):
        gifts = int(total or data.get("count") or 1) if kind == "giftsub" else 1
        text += " · " + sub_duration_caption(duration, sub_duration_kind(kind, gifts))
    icon, color = KIND_META.get(kind, KIND_META["alert"])[:2]
    return {
        "kind": kind,
        "icon": icon,
        "color": color,
        "text": text,
        "count": count,
        "amount": amount,
        "max": biggest,
        "tier": tier if kind == "sub" else None,
    }


def serialize_condensed_groups(
    user_alerts: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Grouped alert dicts -> [{username, lines, items, totals, events, last_ts}].

    ``lines`` keeps the original wording for older docks; ``items`` / ``totals`` drive the
    per-person cards.
    """
    groups: List[Dict[str, Any]] = []
    for username, alert_types in user_alerts.items():
        if not alert_types:
            continue
        lines: List[str] = []
        items: List[Dict[str, Any]] = []
        events = 0
        last_ts = 0.0
        for grouping_key, data in alert_types.items():
            try:
                text = create_aggregated_alert_text(grouping_key, data)
                item = person_line(grouping_key, data)
            except Exception:
                continue
            if text:
                lines.append(text)
            items.append(item)
            events += int(data.get("count") or 0)
            last_ts = max(last_ts, float(data.get("last_ts") or 0))
        if lines:
            groups.append(
                {
                    "username": str(username),
                    "lines": lines,
                    "items": items,
                    "totals": person_totals(items),
                    "events": events,
                    "last_ts": last_ts or None,
                }
            )
            if len(groups) >= MAX_CONDENSED_GROUPS:
                break
    return groups


# Event kinds that only come from Twitch; a person with one of these is a Twitch viewer.
_TWITCH_PERSON_KINDS = frozenset(
    ("follow", "sub", "resub", "giftsub", "bits", "points", "raid", "streak", "modiversary")
)


def attach_person_avatars(groups: List[Dict[str, Any]]) -> None:
    """Add ``avatar`` (Twitch profile picture URL) to By person groups of Twitch viewers.

    Uses pictures already looked up and fetches the rest in the background; the view is
    rebuilt when they arrive. Tip-only and YouTube people keep their initial.
    """
    from .. import twitch_avatars

    names = [
        str(g.get("username") or "")
        for g in groups
        if any(item.get("kind") in _TWITCH_PERSON_KINDS for item in g.get("items") or [])
    ]
    if not names:
        return
    known = twitch_avatars.cached_avatars(names)
    for group in groups:
        url = known.get(str(group.get("username") or ""))
        if url:
            group["avatar"] = url
    if len(known) < len(names):
        twitch_avatars.request_avatars(names, on_update=_person_avatars_arrived)


def _person_avatars_arrived() -> None:
    # Called from the lookup thread; timers must be created on the UI loop.
    run_on_ui_loop(lambda: schedule_condensed_view_update("avatars"))


def _condensed_truncation(user_total: int) -> Optional[str]:
    if user_total > MAX_CONDENSED_GROUPS:
        return f"Showing first {MAX_CONDENSED_GROUPS} of {user_total} people"
    return None


def _condensed_view_model(build_data: Dict[str, Any]) -> Dict[str, Any]:
    """Pure description of the condensed view: header, empty state, truncation, groups."""
    hours = build_data.get("condense_historical_hours", 12)
    groups = build_data.get("groups")
    if groups is None:
        groups = serialize_condensed_groups(build_data.get("user_alerts") or {})
    alert_count = build_data.get("alert_count")
    if alert_count is None:
        alert_count = len(build_data.get("alerts_to_process") or [])
    historical_count = int(build_data.get("historical_count") or 0)
    live_in_window = int(build_data.get("live_in_window") or 0)
    groups = list(groups or [])

    if not groups:
        return {
            "empty": f"No alerts to condense in the last {hours} hours",
            "header": None,
            "truncation": None,
            "groups": [],
        }

    header = None
    if historical_count > 0 or live_in_window > 0:
        header = f"Showing {alert_count} alerts from past {hours} hours"
    user_total = int(build_data.get("user_total") or len(groups))
    return {
        "empty": None,
        "header": header,
        "truncation": _condensed_truncation(user_total),
        "groups": groups[:MAX_CONDENSED_GROUPS],
    }


def build_condensed_overlay_payload(
    hours: Any = None,
    filter_state: Optional[Dict[str, Any]] = None,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    """Collect, group, and serialize condensed view data for the overlay.

    Filters and search default to the shared view (the dock and the tab always match).
    """
    parsed_hours = parse_condensed_historical_hours(hours if hours is not None else _person_hours())
    if filter_state is None:
        filter_state = dict(activity_feed_state.filter_state)
    if search is None:
        search = activity_feed_state.search_text or ""
    cutoff_time = time.time() - (parsed_hours * 3600)
    try:
        alerts_to_process, historical_count, live_in_window = collect_alerts_for_condensed_view(cutoff_time)
    except Exception as exc:
        logger.error("Error loading alerts for condensed overlay payload: %s", exc, exc_info=True)
        alerts_to_process, historical_count, live_in_window = [], 0, 0

    user_alerts, excluded_count, unknown_username_count = _group_alerts_for_condensed(
        alerts_to_process, filter_state=filter_state, search=search
    )
    groups = serialize_condensed_groups(user_alerts)
    return {
        "success": True,
        "groups": groups,
        "historical_count": historical_count,
        "live_in_window": live_in_window,
        "alert_count": len(alerts_to_process),
        "hours": parsed_hours,
        "user_count": len(user_alerts),
        "truncation": _condensed_truncation(len(user_alerts)),
        "excluded_count": excluded_count,
        "unknown_username_count": unknown_username_count,
        "search": str(search),
    }


def condensed_payload_from_build(build_data: Dict[str, Any]) -> Dict[str, Any]:
    """Match the overlay's condensed_view_alerts shape from a desktop rebuild."""
    groups = list(build_data.get("groups") or [])
    user_total = int(build_data.get("user_total") or len(groups))
    return {
        "success": True,
        "groups": groups,
        "historical_count": int(build_data.get("historical_count") or 0),
        "live_in_window": int(build_data.get("live_in_window") or 0),
        "alert_count": int(build_data.get("alert_count") or 0),
        "hours": build_data.get("condense_historical_hours", 12),
        "user_count": user_total,
        "truncation": _condensed_truncation(user_total),
        "excluded_count": int(build_data.get("excluded_count") or 0),
        "unknown_username_count": int(build_data.get("unknown_username_count") or 0),
        "search": str(build_data.get("search") or ""),
    }


def _broadcast_condensed_groups(build_data: Dict[str, Any], *, to: Optional[str] = None) -> bool:
    """Send the groups just built so the dock paints the same people as the tab."""
    return _emit_overlay_event(
        "condensed_view_alerts", condensed_payload_from_build(build_data), to=to
    )


def _collect_condensed_build_data() -> Dict[str, Any]:
    """Collect all data needed to render the condensed view (blocking I/O)."""
    started = time.monotonic()
    hours = _person_hours()
    search = activity_feed_state.search_text or ""
    filters = dict(activity_feed_state.filter_state)
    cutoff_time = time.time() - (hours * 3600)
    historical_count = 0
    live_in_window = 0
    alerts_to_process: List[Dict[str, Any]] = []
    try:
        alerts_to_process, historical_count, live_in_window = collect_alerts_for_condensed_view(cutoff_time)
    except Exception as exc:
        logger.error("Error loading alerts for condensed view: %s", exc, exc_info=True)

    elapsed = time.monotonic() - started
    if elapsed > 1.0:
        logger.warning(
            "activity_feed: condensed alert collection took %.2fs (stored=%d live=%d)",
            elapsed,
            historical_count,
            live_in_window,
        )

    user_alerts, excluded_count, unknown_username_count = _group_alerts_for_condensed(
        alerts_to_process, filter_state=filters, search=search
    )
    groups = serialize_condensed_groups(user_alerts)
    attach_person_avatars(groups)
    return {
        "condense_historical_hours": hours,
        "alert_count": len(alerts_to_process),
        "historical_count": historical_count,
        "live_in_window": live_in_window,
        "user_alerts": user_alerts,
        "user_total": len(user_alerts),
        "groups": groups,
        "excluded_count": excluded_count,
        "unknown_username_count": unknown_username_count,
        "search": search,
    }


def create_aggregated_alert_text(grouping_key, data):
    """Create aggregated text for multiple alerts of the same type"""
    try:
        count = data["count"]
        total_amount = data["total_amount"]
        tier = data["tier"]
        months = data["months"]
        original_type = data.get("original_type", grouping_key.split("_")[0])
        duration = int(data.get("duration_months") or 0)

        if grouping_key.startswith("sub_tier"):
            if count > 1:
                return f"Subscribed {count} times (Tier {tier})!"
            return f"Subscribed (Tier {tier})!"
        elif grouping_key.startswith("resub_tier") and "_1month" in grouping_key:
            if count > 1:
                return f"Resubscribed {count} times for 1 month (Tier {tier})!"
            return f"Resubscribed for 1 month (Tier {tier})!"
        elif grouping_key.startswith("resub_tier") and "_multi_month" in grouping_key:
            if count > 1:
                return f"Resubscribed {count} times for {months} months (Tier {tier})!"
            return f"Resubscribed for {months} months (Tier {tier})!"
        elif grouping_key.startswith("giftsub_tier") and duration > 1:
            try:
                formatted_amount = f"{int(total_amount):,}"
            except (ValueError, TypeError):
                formatted_amount = str(total_amount)
            return f"Gifted {formatted_amount} Tier {tier} subs ({duration} months)!"
        elif grouping_key.startswith("giftsub_tier") and "_1month" in grouping_key:
            try:
                formatted_amount = f"{int(total_amount):,}"
            except (ValueError, TypeError):
                formatted_amount = str(total_amount)
            return f"Gifted {formatted_amount} Tier {tier} subs (1 month)!"
        elif grouping_key.startswith("giftsub_tier") and "_multi_month" in grouping_key:
            try:
                formatted_amount = f"{int(total_amount):,}"
            except (ValueError, TypeError):
                formatted_amount = str(total_amount)
            return f"Gifted {formatted_amount} Tier {tier} subs ({months} months)!"
        elif grouping_key == "follow":
            if count > 1:
                return f"Followed {count} times!"
            return "Followed!"
        elif grouping_key == "bits":
            try:
                formatted_amount = f"{int(total_amount):,}"
            except (ValueError, TypeError):
                formatted_amount = str(total_amount)
            return f"Gave {formatted_amount} bits!"
        elif grouping_key == "donation":
            if total_amount == int(total_amount):
                formatted_amount = f"${int(total_amount)}"
            else:
                formatted_amount = f"${total_amount:.2f}"
            return f"Donated {formatted_amount}!"
        elif grouping_key in ("membership", "member"):
            if count > 1:
                return f"Joined as member {count} times!"
            return "Became a channel member!"
        elif grouping_key in ("member_milestone", "member milestone"):
            if count > 1:
                return f"Reached member milestones {count} times!"
            return "Reached a member milestone!"
        elif grouping_key in ("gift_membership", "gift membership"):
            try:
                formatted_amount = f"{int(total_amount):,}"
            except (ValueError, TypeError):
                formatted_amount = str(total_amount or count)
            return f"Gifted {formatted_amount} memberships!"
        elif grouping_key in ("superchat", "super chat"):
            if total_amount == int(total_amount):
                formatted_amount = f"{int(total_amount)}"
            else:
                formatted_amount = f"{total_amount:.2f}"
            return f"Sent Super Chat ({formatted_amount})!"
        elif grouping_key in ("supersticker", "super sticker"):
            if total_amount == int(total_amount):
                formatted_amount = f"{int(total_amount)}"
            else:
                formatted_amount = f"{total_amount:.2f}"
            return f"Sent Super Sticker ({formatted_amount})!"
        elif grouping_key == "raid":
            try:
                formatted_viewers = f"{int(total_amount):,}"
            except (ValueError, TypeError):
                formatted_viewers = str(total_amount)
            suffix = _raid_category_suffix(data.get("game_name"))
            if count > 1:
                return f"Raided {count} times (largest: {formatted_viewers} viewers){suffix}"
            return f"Raided with {formatted_viewers} viewers{suffix}"
        elif grouping_key == "streak":
            sc = int(total_amount) if total_amount else 0
            if sc < 1:
                return "Watch streak!"
            return format_watch_streak_message("", sc, include_username=False)
        elif grouping_key.startswith("points:") or original_type in ("point", "points"):
            reward_name = data.get("reward_name")
            if not reward_name and grouping_key.startswith("points:"):
                reward_name = grouping_key.split(":", 1)[1]
            if not reward_name:
                reward_name = "Unknown Reward"
            if count > 1:
                return f"Redeemed '{reward_name}' {count} times!"
            return f"Redeemed '{reward_name}'!"
        else:
            display_name = grouping_key.replace("_", " ").title()
            if count > 1:
                return f"{display_name} {count} times!"
            return f"{display_name}!"

    except Exception as e:
        logger.error("Error creating aggregated alert text: %s", e, exc_info=True)
        return f"{grouping_key.title()}!"


# --------------------------------------------------------------------------- history / person flows

_history_payload: Dict[str, Any] = {"rows": [], "page": 1, "total_pages": 1, "total_count": 0, "loading": True}
_person_payload: Dict[str, Any] = {"groups": [], "loading": True}
_history_task_running = False
_history_rerun = False
_condensed_update_scheduled = False
_condensed_rebuild_running = False
_condensed_rebuild_rerun = False


def _emit_stored_alerts_payload(
    alerts: List[Dict[str, Any]],
    pagination_info: Dict[str, Any],
    *,
    to: Optional[str] = None,
) -> bool:
    """Send one history page to the dock."""
    response = {
        "success": not pagination_info.get("error"),
        "alerts": serialize_live_alerts(alerts),
        "page": pagination_info.get("page", activity_feed_state.current_page),
        "total_pages": pagination_info.get("total_pages", activity_feed_state.total_pages),
        "total_count": pagination_info.get("total_count", 0),
        "has_prev": bool(pagination_info.get("has_prev", False)),
        "has_next": bool(pagination_info.get("has_next", False)),
    }
    if pagination_info.get("error"):
        response["error"] = str(pagination_info["error"])
    return _emit_overlay_event("stored_alerts_paginated", response, to=to)


def emit_stored_alerts_page(page: int, *, to: Optional[str] = None) -> None:
    """Load one stored page and send that payload to one dock (or all)."""
    try:
        alerts, info = load_restored_alerts(page)
        _emit_stored_alerts_payload(alerts, info, to=to)
    except Exception as exc:
        logger.error("activity_feed: could not emit stored page %s: %s", page, exc)


def _apply_history_page(alerts: List[Dict[str, Any]], info: Dict[str, Any]) -> None:
    global _history_payload
    _history_payload = {
        "rows": [_row_for(a) for a in alerts],
        "page": info.get("page", 1),
        "total_pages": info.get("total_pages", 1),
        "total_count": info.get("total_count", 0),
        "has_prev": bool(info.get("has_prev")),
        "has_next": bool(info.get("has_next")),
        "loading": False,
        "error": info.get("error") or "",
    }
    _push_slot("history", _history_payload)
    _emit_stored_alerts_payload(alerts, info)
    loaded_page = info.get("page")
    if isinstance(loaded_page, int) and loaded_page >= 1 and loaded_page != view_manager.snapshot()["page"]:
        # The saved page no longer exists (history shrank): show and keep the real one.
        if view_manager.set_page_quietly(loaded_page):
            snapshot = view_manager.snapshot()
            broadcast_activity_feed_view(snapshot)
            _push_slot("view", snapshot)


async def _refresh_history_async() -> None:
    global _history_task_running, _history_rerun
    _history_task_running = True
    try:
        while True:
            _history_rerun = False
            page = view_manager.snapshot()["page"]
            alerts, info = await run.io_bound(load_restored_alerts, page)
            if view_mode(view_manager.snapshot()) == "history":
                _apply_history_page(alerts, info)
            if not _history_rerun:
                break
    except Exception as exc:
        logger.error("activity_feed: history load failed: %s", exc, exc_info=True)
    finally:
        _history_task_running = False


def refresh_restored_alerts() -> None:
    """Load the shared history page off the UI loop, then repaint the tab and the docks."""
    global _history_rerun
    if _history_task_running:
        _history_rerun = True
        return
    try:
        background_tasks.create(_refresh_history_async(), name="activity_feed_history")
    except Exception:
        # No NiceGUI loop (early startup / tests): load inline.
        alerts, info = load_restored_alerts(view_manager.snapshot()["page"])
        _apply_history_page(alerts, info)


def schedule_condensed_view_update(reason: str) -> None:
    """Debounced By person rebuild (bursts of alerts / typing coalesce)."""
    global _condensed_update_scheduled

    if view_mode(view_manager.snapshot()) != "person":
        return
    if _condensed_update_scheduled:
        return
    _condensed_update_scheduled = True

    def _deferred() -> None:
        global _condensed_update_scheduled
        _condensed_update_scheduled = False
        _schedule_condensed_rebuild(reason)

    try:
        app_schedule(0.3, _deferred, once=True)
    except Exception:
        _condensed_update_scheduled = False


def _schedule_condensed_rebuild(reason: str) -> None:
    """Start (or coalesce) an async By person rebuild."""
    global _condensed_rebuild_rerun
    if _condensed_rebuild_running:
        _condensed_rebuild_rerun = True
        return
    logger.debug("activity_feed: by-person rebuild (%s)", reason)
    try:
        background_tasks.create(
            _update_condensed_view_async(reason), name="activity_feed_condensed_rebuild"
        )
    except Exception as exc:
        logger.debug("activity_feed: by-person rebuild not scheduled: %s", exc)


def _apply_person_build(build_data: Dict[str, Any]) -> None:
    global _person_payload
    payload = condensed_payload_from_build(build_data)
    _person_payload = payload
    _push_slot("person", payload)
    _broadcast_condensed_groups(build_data)


async def _update_condensed_view_async(reason: str) -> None:
    """Group off the UI loop, then paint the tab and the docks."""
    global _condensed_rebuild_running, _condensed_rebuild_rerun

    _condensed_rebuild_running = True
    try:
        while True:
            _condensed_rebuild_rerun = False
            if view_mode(view_manager.snapshot()) != "person":
                return
            build_data = await run.io_bound(_collect_condensed_build_data)
            if view_mode(view_manager.snapshot()) == "person":
                _apply_person_build(build_data)
            if not _condensed_rebuild_rerun:
                break
    except Exception as exc:
        logger.error("activity_feed: by-person rebuild failed (%s): %s", reason, exc, exc_info=True)
        _push_slot("person", {"success": False, "error": str(exc), "groups": []})
    finally:
        _condensed_rebuild_running = False


def update_condensed_view() -> bool:
    """Rebuild By person when it is the open view (kept for callers)."""
    if view_mode(view_manager.snapshot()) == "person":
        _schedule_condensed_rebuild("update_condensed_view")
    return True


def refresh_condensed_view() -> None:
    update_condensed_view()


def publish_activity_feed_view(*, to: Optional[str] = None) -> Dict[str, Any]:
    """Send one dock everything it needs: settings, view + live list, page or groups."""
    snapshot = view_manager.load()
    broadcast_feed_settings(to=to)
    broadcast_activity_feed_view(snapshot, to=to, include_live_alerts=True)
    mode = view_mode(snapshot)
    if mode == "history":
        emit_stored_alerts_page(int(snapshot["page"]), to=to)
    elif mode == "person":
        payload = build_condensed_overlay_payload(
            filter_state=snapshot["filters"], search=snapshot.get("search") or ""
        )
        _emit_overlay_event("condensed_view_alerts", payload, to=to)
    return snapshot


def _apply_view_to_desktop(changed: set) -> None:
    """The shared view changed: repaint the tab and load what the new view needs."""
    snapshot = view_manager.snapshot()
    _push_slot("view", snapshot)
    mode = view_mode(snapshot)
    if mode == "history" and ("tab" in changed or "page" in changed or "condensed" in changed):
        _push_slot("history", dict(_history_payload, loading=True))
        refresh_restored_alerts()
    if mode == "person" and changed & {"tab", "condensed", "filters", "search"}:
        if changed & {"tab", "condensed"}:
            _push_slot("person", dict(_person_payload, loading=True))
        schedule_condensed_view_update("view_change")


def on_checkbox_change(key, value):
    """A filter checkbox changed (kept for callers)."""
    view_manager.update({"filters": {key: bool(value)}})


def switch_to_tab(tab_name):
    """Switch to the current (Live / By person) or previous (History) tab."""
    if tab_name not in ("current", "previous"):
        return
    patch: Dict[str, Any] = {"tab": tab_name}
    if tab_name == "previous":
        patch["page"] = 1
    view_manager.update(patch)


def go_to_page(page):
    """Open one history page (clamped to the pages that exist)."""
    try:
        page = int(page)
    except (TypeError, ValueError):
        return
    max_allowed = max(1, min(activity_feed_state.total_pages or 1, _max_history_pages()))
    page = min(max(page, 1), max_allowed)
    view_manager.update({"page": page})


# --------------------------------------------------------------------------- the tab host


class _FeedHost:
    def __init__(self, host, slots: Dict[str, Any], fallback) -> None:
        self.host = host
        self.slots = slots
        self.fallback = fallback
        self.fallback_on = False


_host: Optional[_FeedHost] = None
_slot_cache: Dict[str, str] = {}
_slot_rev = 0
_live_push_scheduled = False
_queue_poll_timer = None
_watchdog_timer = None
_last_queue_sig: Optional[str] = None
_last_meta: Dict[str, Any] = {}
_meta_tick = 0
_health_failures = 0
_registered_event_clients: set = set()
_assets_shared = False


def _tab_alive() -> bool:
    return _host is not None and _element_alive(_host.host)


def _slot_value(name: str) -> Any:
    if name == "settings":
        return current_feed_settings()
    if name == "view":
        return view_manager.snapshot()
    if name == "live":
        return [_row_for(a) for a in live_alerts_snapshot()]
    if name == "history":
        return _history_payload
    if name == "person":
        return _person_payload
    if name == "queue":
        return current_queue_state()
    if name == "meta":
        return _meta_state()
    return None


def _apply_slot(name: str, payload: str) -> None:
    global _slot_rev
    host = _host
    if host is None or not _element_alive(host.host):
        return
    if _slot_cache.get(name) == payload:
        return
    el = host.slots.get(name)
    if el is None or not _element_alive(el):
        return
    _slot_cache[name] = payload
    _slot_rev += 1
    el._props.update({"data-json": payload, "data-rev": str(_slot_rev)})
    el.update()


def _push_slot(name: str, value: Any) -> None:
    """Write one state slot of the tab (no-op while the tab isn't built)."""
    if not _tab_alive():
        return
    try:
        payload = json.dumps(_json_ready(value), separators=(",", ":"), default=str)
    except Exception as exc:
        logger.debug("activity_feed: slot %s not serialisable: %s", name, exc)
        return
    _run_on_ui_loop(lambda: _apply_slot(name, payload))


def push_all_slots() -> None:
    for name in _SLOT_NAMES:
        _push_slot(name, _slot_value(name))


def _schedule_live_push() -> None:
    """Coalesce bursts of alerts into one repaint."""
    global _live_push_scheduled
    if not _tab_alive() or _live_push_scheduled:
        return
    _live_push_scheduled = True

    def _go() -> None:
        global _live_push_scheduled
        _live_push_scheduled = False
        _push_slot("live", _slot_value("live"))

    def _arm() -> None:
        try:
            app_schedule(_LIVE_PUSH_DEBOUNCE_SEC, _go, once=True)
        except Exception:
            _go()

    run_on_ui_loop(_arm)


# ---- queue state


def _fallback_queue_state(web_engine: Any) -> Dict[str, Any]:
    """Before the queue-state API existed: flags plus the raw queue."""
    queue: List[Dict[str, Any]] = []
    try:
        from modules import alert_processor

        for alert in alert_processor.snapshot_alert_queue():
            queue.append(
                {
                    "alert_id": getattr(alert, "alert_id", None),
                    "type": getattr(alert, "alert_type", ""),
                    "username": getattr(alert, "username", ""),
                }
            )
    except Exception:
        pass
    playing = {"alert_id": None, "type": "", "username": ""} if getattr(web_engine, "ALERT_PLAYING", False) else None
    return {"paused": bool(getattr(web_engine, "ALERTS_PAUSED", False)),
            "muted": bool(getattr(web_engine, "ALERTS_MUTED", False)), "playing": playing, "queue": queue}


def current_queue_state() -> Dict[str, Any]:
    """Normalised alert queue state (web_engine.get_alert_queue_state, else flags)."""
    try:
        from modules import web_engine
    except Exception:
        return normalize_queue_state(None)
    getter = getattr(web_engine, "get_alert_queue_state", None)
    raw = None
    if callable(getter):
        try:
            raw = getter()
        except Exception as exc:
            logger.debug("activity_feed: queue state unavailable: %s", exc)
    contract = isinstance(raw, dict)
    if not contract:
        raw = _fallback_queue_state(web_engine)
    state = normalize_queue_state(
        raw,
        paused=bool(getattr(web_engine, "ALERTS_PAUSED", False)),
        muted=bool(getattr(web_engine, "ALERTS_MUTED", False)),
    )
    state["contract"] = contract
    return state


def _queue_sig(state: Dict[str, Any]) -> str:
    body = {k: v for k, v in state.items() if k != "server_now_ms"}
    return json.dumps(body, sort_keys=True, default=str)


def _meta_state() -> Dict[str, Any]:
    dock_open = None
    try:
        from modules import web_engine

        engine = getattr(web_engine, "web_engine_instance", None)
        if engine is not None and hasattr(engine, "_overlay_connected_routes"):
            dock_open = "activity_feed" in engine._overlay_connected_routes()
    except Exception:
        dock_open = None
    return {"app": True, "dock_open": dock_open}


def _poll_queue(force: bool = False) -> None:
    """Push the queue state to the tab when it changed (cheap; runs twice a second)."""
    global _last_queue_sig, _meta_tick, _last_meta
    if not _tab_alive():
        return
    state = current_queue_state()
    sig = _queue_sig(state)
    if force or sig != _last_queue_sig:
        _last_queue_sig = sig
        _push_slot("queue", state)
    _meta_tick += 1
    if force or _meta_tick % 10 == 0:
        meta = _meta_state()
        if force or meta != _last_meta:
            _last_meta = meta
            _push_slot("meta", meta)


def sync_pause_breath(playing: bool) -> None:
    """The playing flag changed (web_engine.set_alert_playing): repaint the queue state."""
    run_on_ui_loop(lambda: _poll_queue(force=True))


def sync_pause_button_state():
    """Pause changed. Safe from any thread."""
    run_on_ui_loop(lambda: _poll_queue(force=True))


def sync_mute_button_state():
    """Mute changed. Safe from any thread."""
    run_on_ui_loop(lambda: _poll_queue(force=True))


def _start_timers() -> None:
    global _queue_poll_timer, _watchdog_timer
    if _queue_poll_timer is None:
        _queue_poll_timer = app_schedule(_QUEUE_POLL_SEC, _poll_queue, active=True)
    if _watchdog_timer is None:
        _watchdog_timer = app_schedule(_FEED_WATCHDOG_INTERVAL_SEC, _run_feed_watchdog, active=True)


def _stop_timers() -> None:
    global _queue_poll_timer, _watchdog_timer
    for timer in (_queue_poll_timer, _watchdog_timer):
        if timer is not None:
            try:
                timer.active = False
            except Exception:
                pass
    _queue_poll_timer = None
    _watchdog_timer = None


# ---- assets


def _read_asset(name: str) -> str:
    from ..path_utils import get_assets_path

    try:
        with open(get_assets_path(f"default_assets/{name}"), "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception as exc:
        logger.error("Activity Feed asset %s missing: %s", name, exc)
        return ""


_APP_EXTRA_CSS = """
.afx-host { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; width: 100%; }
.afx-mount { display: flex; flex-direction: column; flex: 1 1 auto; min-height: 0; }
.afx-slot { display: none !important; }
.afx { font-family: inherit; }
"""


def feed_app_stylesheet() -> str:
    """activity_feed.css scoped to the tab (and colours promoted over the light-mode rule)."""
    from ..help_system.help_render import promote_color_importance, scope_selectors

    css = _read_asset("activity_feed.css") + "\n" + _APP_EXTRA_CSS
    return promote_color_importance(scope_selectors(css, ".afx-host"))


def _inject_assets() -> None:
    """Stylesheet + renderer, on this page and every page loaded later."""
    global _assets_shared
    from ..help_system.help_render import inject_page_stylesheet

    inject_page_stylesheet(_STYLE_ID, feed_app_stylesheet())
    js = _read_asset("activity_feed.js")
    if not js:
        return
    if not _assets_shared:
        ui.add_head_html(f"<script>{js}</script>", shared=True)
        _assets_shared = True
    _reinject_renderer(js)


def _reinject_renderer(js: Optional[str] = None) -> None:
    js = js if js is not None else _read_asset("activity_feed.js")
    client = _feed_client()
    if client is None or not js or not getattr(client, "has_socket_connection", False):
        return  # pages loaded later get it from the shared head
    try:
        client.run_javascript(js)
    except Exception as exc:
        logger.debug("activity_feed: renderer inject failed: %s", exc)


def _feed_client() -> Any:
    host = _host
    if host is not None and _element_alive(host.host):
        try:
            return host.host.client
        except Exception:
            return None
    try:
        from nicegui import context

        return context.client
    except Exception:
        return None


# ---- actions from the renderer


def _open_settings_section(anchor: str = "aps-activity-feed") -> None:
    try:
        from modules.help_system.contextual_help import navigate_to_settings_subtab

        navigate_to_settings_subtab("App Settings", main_tab="Settings")
    except Exception as exc:
        logger.debug("activity_feed: settings navigation failed: %s", exc)
        return
    client = _feed_client()
    if client is None:
        return
    js = (
        "(function(){var n=0;(function go(){var el=document.getElementById(%s);"
        "if(el){el.scrollIntoView({behavior:'smooth',block:'start'});return;}"
        "if(n++<40){setTimeout(go,100);}})();})();" % json.dumps(anchor)
    )
    try:
        client.run_javascript(js)
    except Exception:
        pass


def open_feed_settings() -> None:
    """Show Settings → App Settings → Activity Feed (tab tune button, dock "Open in Mycelian")."""
    _run_on_ui_loop(lambda: _open_settings_section("aps-activity-feed"))


def _open_help() -> None:
    try:
        from modules.help_system.contextual_help import _open_help_target, get_help_target_for_tab

        _open_help_target(get_help_target_for_tab("Activity Feed") or "alert_testing")
    except Exception as exc:
        logger.debug("activity_feed: help failed: %s", exc)


def dock_url() -> str:
    host, port = "127.0.0.1", 5000
    try:
        from modules import web_engine

        engine = web_engine.web_engine_instance
        if engine is not None:
            port = int(getattr(engine, "port", port) or port)
            host = engine._local_connect_host()
    except Exception:
        pass
    return f"http://{host}:{port}/activity_feed"


def _copy_dock_link() -> None:
    url = dock_url()
    copied = False
    try:
        from ..notification_engine import copy_text_to_clipboard

        copied = bool(copy_text_to_clipboard(url))
    except Exception:
        copied = False
    if not copied:
        try:
            ui.clipboard.write(url)
            copied = True
        except Exception:
            copied = False
    notify(
        url,
        type="success" if copied else "warning",
        source="activity_feed",
        title="Activity Feed dock link copied" if copied else "Couldn't copy the dock link",
        hint="In OBS open Docks → Custom Browser Docks, paste it as the URL and click Apply.",
        actions=None if copied else [{"kind": "copy", "text": url, "label": "Copy link"}],
    )


def handle_feed_action(action: str, data: Optional[Dict[str, Any]] = None) -> Any:
    """One user action from the renderer in the tab (same actions as the dock)."""
    data = data if isinstance(data, dict) else {}
    try:
        if action == "pause":
            from modules import web_engine
            from modules.mainuiwindow import toggle_alerts

            want = data.get("paused")
            if want is None or bool(want) != bool(getattr(web_engine, "ALERTS_PAUSED", False)):
                toggle_alerts()
            _poll_queue(force=True)
        elif action == "mute":
            from modules import web_engine
            from modules.mainuiwindow import toggle_mute

            want = data.get("muted")
            if want is None or bool(want) != bool(getattr(web_engine, "ALERTS_MUTED", False)):
                toggle_mute()
            _poll_queue(force=True)
        elif action == "skip":
            alert_id = data.get("alert_id") or None
            result = skip_alert({"alert_id": alert_id} if alert_id else {"type": "current"})
            _poll_queue(force=True)
            return result
        elif action == "replay":
            alert_id = data.get("alert_id")
            label = str(data.get("label") or "")
            return replay_alert({"alert_id": alert_id, "type": label, "username": data.get("user")})
        elif action == "mode":
            mode = data.get("mode")
            if mode in ("live", "history", "person"):
                view_manager.update(mode_patch(mode))
        elif action == "filter":
            key = data.get("key")
            if key in FILTER_KEYS:
                view_manager.update({"filters": {key: bool(data.get("value"))}})
        elif action == "search":
            view_manager.update({"search": data.get("text", "")})
        elif action == "page":
            go_to_page(data.get("page"))
        elif action == "settings":
            _open_settings_section("aps-activity-feed")
        elif action == "storage":
            _open_settings_section("aps-storage")
        elif action == "help":
            _open_help()
        elif action == "copy-dock":
            _copy_dock_link()
        else:
            logger.debug("activity_feed: unknown action %r", action)
    except Exception as exc:
        logger.error("activity_feed: action %s failed: %s", action, exc, exc_info=True)
    return None


def _on_feed_event(event: Any) -> None:
    args = getattr(event, "args", None)
    if not isinstance(args, dict):
        return
    handle_feed_action(str(args.get("action") or ""), args)


# ---- fail-open watchdog

_HEALTH_JS = (
    "(window.MycelianFeed && window.MycelianFeed.health) ? window.MycelianFeed.health()"
    " : {ok: false, reason: 'no_renderer', hosts: document.querySelectorAll('.afx-host[data-afx-app]').length,"
    " visible: document.querySelectorAll('.afx-host[data-afx-app]').length}"
)


def _feed_health_action(result: Any, failures: int) -> tuple:
    """Decide what to do with a renderer health report -> (action, failures).

    ok: healthy (leave fallback). skip: nothing to judge (tab not in the DOM / hidden).
    reinject: load the renderer again. fallback: show the server-rendered list.
    """
    if not isinstance(result, dict):
        return "skip", failures
    if result.get("ok"):
        return "ok", 0
    hosts = int(result.get("hosts") or 0)
    visible = int(result.get("visible") or 0)
    if hosts == 0 or visible == 0:
        return "skip", failures
    failures += 1
    if failures >= 2:
        return "fallback", failures
    return "reinject", failures


def render_fallback_html(rows: List[Dict[str, Any]], *, limit: int = 150) -> str:
    """Plain list used only if the renderer can't run (every value escaped)."""
    parts = [
        '<div class="afx-fb-note">The live feed view couldn\'t start, so this is a simple list. '
        "It updates as events arrive; reopening the tab usually brings the full view back.</div>"
    ]
    show_duration = show_sub_duration()
    for row in rows[:limit]:
        user = html.escape(str(row.get("user") or ""))
        # The plain list has no tag, so a multi-month row uses its full sentence.
        what = html.escape(str((show_duration and row.get("what_full")) or row.get("what") or ""))
        amount = html.escape(str(row.get("amount") or ""))
        when = html.escape(format_timestamp(row.get("ts") or time.time()))
        msg = html.escape(str(row.get("msg") or ""))
        parts.append(
            f'<div class="afx-fb-row"><b>{user}</b> {what}'
            + (f" · {amount}" if amount else "")
            + (f" — <i>{msg}</i>" if msg else "")
            + f"<small>{when}</small></div>"
        )
    if len(parts) == 1:
        parts.append('<div class="afx-fb-row">No events yet this session.</div>')
    return "".join(parts)


def _set_fallback(on: bool) -> None:
    host = _host
    if host is None or not _element_alive(host.host):
        return
    if on:
        rows = [_row_for(a) for a in live_alerts_snapshot()]
        try:
            host.fallback.set_content(render_fallback_html(rows))
        except Exception:
            pass
        if not host.fallback_on:
            logger.warning("activity_feed: renderer unhealthy — showing the plain fallback list")
        host.host.classes(add="is-fallback")
    elif host.fallback_on:
        logger.info("activity_feed: renderer healthy again — leaving the fallback list")
        host.host.classes(remove="is-fallback")
    host.fallback_on = on


def _feed_ui_is_on_screen() -> bool:
    """False when the feed cannot be visible (tray, other main tab, no socket)."""
    try:
        from ..tray_controller import is_minimized

        if is_minimized():
            return False
    except Exception:
        pass
    try:
        from ..help_system.contextual_help import get_current_tab_context

        main_tab, _ = get_current_tab_context()
        if (
            main_tab
            and main_tab not in ("Activity Feed", "unknown")
            and "activity feed" not in str(main_tab).lower()
        ):
            return False
    except Exception:
        pass
    return _get_feed_client() is not None


def _get_feed_client() -> Any:
    client = _feed_client() if _tab_alive() else None
    if client is not None and getattr(client, "has_socket_connection", False):
        return client
    try:
        from nicegui import Client

        for inst in Client.instances.values():
            if getattr(inst, "has_socket_connection", False):
                return inst
    except Exception:
        pass
    return None


async def _probe_feed_health() -> None:
    global _health_failures
    client = _feed_client()
    if client is None:
        return
    try:
        result = await client.run_javascript(_HEALTH_JS, timeout=5.0)
    except Exception as exc:
        logger.debug("activity_feed: health probe failed: %s", exc)
        return
    action, _health_failures = _feed_health_action(result, _health_failures)
    if action == "ok":
        _set_fallback(False)
    elif action == "reinject":
        logger.info("activity_feed: renderer not painting (%s) — reloading it", result)
        _reinject_renderer()
        push_all_slots()
    elif action == "fallback":
        _set_fallback(True)
        _reinject_renderer()


def _run_feed_watchdog() -> None:
    try:
        from modules.shutdown import is_shutdown_in_progress

        if is_shutdown_in_progress():
            return
    except Exception:
        pass
    if not _tab_alive() or not _feed_ui_is_on_screen():
        return
    if _host is not None and _host.fallback_on:
        _set_fallback(True)  # keep the plain list current while it is shown
    try:
        background_tasks.create(_probe_feed_health(), name="activity_feed_health")
    except Exception:
        pass


# ---- client lifecycle

_hooks_registered_client_ids: set = set()


def recover_after_client_reconnect() -> None:
    """Re-send every slot (the renderer repaints from them)."""
    _slot_cache.clear()
    push_all_slots()


def register_client_lifecycle_hooks() -> None:
    """Repaint after a socket reconnect (NiceGUI re-sends elements; slots refresh)."""
    try:
        from nicegui import context

        client = context.client
    except Exception as exc:
        logger.debug("activity_feed: could not register lifecycle hooks: %s", exc)
        return
    if client.id in _hooks_registered_client_ids:
        return
    _hooks_registered_client_ids.add(client.id)
    client_id = client.id

    def _on_connect(_client=None) -> None:
        app_schedule(0.5, recover_after_client_reconnect, once=True)

    def _on_delete(_client=None) -> None:
        _hooks_registered_client_ids.discard(client_id)
        _registered_event_clients.discard(client_id)

    client.on_connect(_on_connect)
    client.on_delete(_on_delete)


def create_activity_feed_tab():
    """Build the Activity Feed tab: one host the shared renderer paints."""
    global _host, _health_failures, _last_queue_sig, _last_meta
    view_manager.load()
    _inject_assets()
    try:
        from nicegui import context

        client = context.client
        if client.id not in _registered_event_clients:
            _registered_event_clients.add(client.id)
            client.layout.on(_FEED_EVENT, _on_feed_event)
    except Exception as exc:
        logger.error("activity_feed: could not listen for feed actions: %s", exc)

    with ui.element("div").classes("tab-surface w-full h-full relative flex flex-col p-4 afx-tab"):
        host = ui.element("div").classes("afx-host afx-app-host").props('data-afx-app="1"')
        slots: Dict[str, Any] = {}
        with host:
            for name in _SLOT_NAMES:
                slots[name] = ui.element("div").classes("afx-slot").props(f'data-slot="{name}"')
            ui.element("div").classes("afx-mount")
            fallback = ui.html("", sanitize=False).classes("afx-fallback")
    _host = _FeedHost(host, slots, fallback)
    _slot_cache.clear()
    _health_failures = 0
    _last_queue_sig = None
    _last_meta = {}
    for name in _SLOT_NAMES:
        try:
            payload = json.dumps(_json_ready(_slot_value(name)), separators=(",", ":"), default=str)
        except Exception as exc:
            logger.error("activity_feed: initial %s slot failed: %s", name, exc)
            continue
        _apply_slot(name, payload)
    activity_feed_state.is_initialized = True
    mode = view_mode(view_manager.snapshot())
    if mode == "history":
        refresh_restored_alerts()
    elif mode == "person":
        _schedule_condensed_rebuild("tab_open")
    _start_timers()
    logger.debug("Activity feed tab built")

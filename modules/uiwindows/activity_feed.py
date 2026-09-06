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

import asyncio
import html
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import current_process
from typing import Any, Callable, Dict, List, Optional

from nicegui import app, background_tasks, run, ui

from ..notification_engine import notify
from .. import alertutils
from ..ui_timer import app_schedule, run_on_ui_loop

logger = logging.getLogger(__name__)

_pause_breath_timer_started = False
_DOCK_BTN_PROPS = "flat no-caps dense"
_recovery_scheduled = False
_integrity_check_reason: Optional[str] = None
_integrity_check_scheduled = False
_condensed_update_scheduled = False
_condensed_rebuild_running = False
_condensed_rebuild_rerun = False
_condensed_integrity_failures = 0
_ignore_condense_toggle_event = False
_dom_desync_failures = 0
_feed_watchdog_started = False
_FEED_WATCHDOG_INTERVAL_SEC = 30.0
_DOM_DESYNC_RELOAD_THRESHOLD = 3
_stale_ui_skip_logged = False

_FEED_DOM_PROBE_JS = """
(function () {
  function visible(el) {
    if (!el) return false;
    if (el.classList && el.classList.contains('hidden')) return false;
    var s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden';
  }
  var cur = document.querySelector('.activity-feed-current');
  var con = document.querySelector('.activity-feed-condensed');
  var prev = document.querySelector('.activity-feed-previous');
  var targets = [cur, con, prev].filter(visible);
  if (!targets.length) {
    return {ok: false, reason: 'no_visible_surface', children: 0};
  }
  var children = 0;
  for (var i = 0; i < targets.length; i++) {
    children += targets[i].children.length;
  }
  return {ok: children > 0, reason: 'ok', children: children};
})()
"""


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


def _slot_child_count(el: Any) -> int:
    """Return child count for a NiceGUI element's default slot (3.x safe)."""
    if not _element_alive(el):
        return 0
    try:
        slot = getattr(el, "default_slot", None)
        if slot is None:
            return 0
        return len(slot.children)
    except Exception:
        return 0


def _null_live_alert_element_refs() -> None:
    """Drop stale UI refs before a clear()/rebuild so callers never touch deleted elements."""
    for alert_data in activity_feed_state.live_alerts:
        alert_data["element"] = None
        alert_data["new_badge"] = None
        alert_data["timestamp_label"] = None


# The live feed is a session view, not history: older alerts stay available on the
# Previous tab, which is paged from the database.
MAX_LIVE_ALERTS = 500


def _trim_live_alerts() -> None:
    """Drop the oldest live alerts once the session feed exceeds its cap.

    ``live_alerts`` used to be insert-only, which was survivable when a session lasted
    an evening. With minimize-to-tray a session can run for weeks, and every entry
    pins a NiceGUI element, so the list and its DOM nodes have to be bounded.
    """
    overflow = len(activity_feed_state.live_alerts) - MAX_LIVE_ALERTS
    if overflow <= 0:
        return

    for alert_data in activity_feed_state.live_alerts[-overflow:]:
        for key in ("element", "new_badge", "timestamp_label"):
            element = alert_data.get(key)
            if _element_alive(element):
                try:
                    element.delete()
                except Exception as exc:
                    logger.debug("activity_feed: could not delete trimmed element: %s", exc)
            alert_data[key] = None

    del activity_feed_state.live_alerts[-overflow:]
    logger.debug("activity_feed: trimmed %d live alert(s) over the cap", overflow)


def _containers_alive() -> bool:
    if (
        activity_feed_state.condense_list
        and activity_feed_state.current_tab == "current"
    ):
        return _element_alive(activity_feed_state.condensed_container)
    return _element_alive(activity_feed_state.current_alerts_container)


def _condensed_view_has_content() -> bool:
    container = activity_feed_state.condensed_container
    if not _element_alive(container):
        return False
    return _slot_child_count(container) > 0


def _element_has_hidden_class(el: Any) -> bool:
    """Return True when NiceGUI's class list includes ``hidden``."""
    if not _element_alive(el):
        return True
    try:
        classes = getattr(el, "_classes", None)
        if classes is None:
            return False
        return "hidden" in classes
    except Exception:
        return False


def _count_rendered_live_alerts() -> int:
    """Return how many live alerts still have a bound, alive UI element."""
    count = 0
    for alert_data in activity_feed_state.live_alerts:
        element = alert_data.get("element")
        if element is not None and _element_alive(element):
            count += 1
    return count


def _feed_expects_visible_content() -> bool:
    """True when the Current Alerts tab should show at least one card/group."""
    if activity_feed_state.current_tab != "current":
        return False
    return bool(activity_feed_state.live_alerts)


def _fix_visibility_desync(reason: str) -> bool:
    """Ensure a Current-tab surface is visible; return True if a fix was applied.

    During condensed rebuilds the regular feed intentionally stays visible until
    condensed content is ready — that is not treated as a desync.
    """
    if activity_feed_state.current_tab != "current":
        return False

    current = activity_feed_state.current_alerts_container
    condensed = activity_feed_state.condensed_container
    if not _element_alive(current) and not _element_alive(condensed):
        return False

    current_hidden = _element_has_hidden_class(current)
    condensed_hidden = _element_has_hidden_class(condensed)
    want_condensed = bool(activity_feed_state.condense_list)
    rebuild_pending = _condensed_rebuild_running or _condensed_update_scheduled

    both_hidden = current_hidden and condensed_hidden
    regular_hidden_when_needed = (not want_condensed) and current_hidden
    condensed_ready_but_hidden = (
        want_condensed
        and _condensed_view_has_content()
        and condensed_hidden
        and not rebuild_pending
    )

    if not (both_hidden or regular_hidden_when_needed or condensed_ready_but_hidden):
        return False

    logger.warning(
        "activity_feed: visibility desync (%s) — "
        "want_condensed=%s current_hidden=%s condensed_hidden=%s "
        "both_hidden=%s ready_but_hidden=%s",
        reason,
        want_condensed,
        current_hidden,
        condensed_hidden,
        both_hidden,
        condensed_ready_but_hidden,
    )

    if both_hidden:
        if want_condensed and _condensed_view_has_content():
            _apply_condensed_visibility(True)
        elif want_condensed and not rebuild_pending:
            _fallback_to_regular_feed(f"visibility_desync:{reason}")
        else:
            _apply_condensed_visibility(False)
            _ensure_regular_feed_populated(f"visibility_desync:{reason}")
        return True

    if condensed_ready_but_hidden:
        _apply_condensed_visibility(True)
        return True

    # Regular mode but current surface hidden (condensed may still be showing).
    _apply_condensed_visibility(False)
    _ensure_regular_feed_populated(f"visibility_desync:{reason}")
    return True


def _ensure_regular_feed_populated(reason: str) -> None:
    """Rebuild the regular feed when rendered cards are missing or out of date.

    While condensed mode is on, new alerts are state-only (no cards). Toggle-off
    and fallback paths must rebuild whenever rendered count lags live state.
    """
    if not activity_feed_state.live_alerts:
        return
    if not _element_alive(activity_feed_state.current_alerts_container):
        return
    rendered = _count_rendered_live_alerts()
    live_count = len(activity_feed_state.live_alerts)
    if rendered >= live_count:
        return
    logger.warning(
        "activity_feed: regular feed stale (%s) — rebuilding "
        "(rendered=%d live=%d)",
        reason,
        rendered,
        live_count,
    )
    rebuild_current_alerts_feed()


def _fallback_to_regular_feed(reason: str, *, disable_condense: bool = False) -> None:
    """Show the regular feed and rebuild it if the surface is empty."""
    global _condensed_integrity_failures, _ignore_condense_toggle_event

    logger.warning("activity_feed: falling back to regular feed (%s)", reason)
    _condensed_integrity_failures = 0

    if disable_condense and activity_feed_state.condense_list:
        activity_feed_state.condense_list = False
        toggle = activity_feed_state.condense_toggle
        if _element_alive(toggle) and getattr(toggle, "value", False):
            _ignore_condense_toggle_event = True
            try:
                toggle.value = False
            except Exception:
                pass
            finally:
                _ignore_condense_toggle_event = False

    _apply_condensed_visibility(False)
    _ensure_regular_feed_populated(f"fallback:{reason}")


def _ensure_feed_integrity(reason: str) -> None:
    """Rebuild the feed when state has alerts but nothing is rendered."""
    global _condensed_integrity_failures

    if activity_feed_state.current_tab != "current":
        return
    if not _containers_alive():
        return

    # Fix both-hidden / wrong-surface class races before content checks.
    if _fix_visibility_desync(reason):
        schedule_feed_dom_probe(f"after_visibility_fix:{reason}")
        return

    if activity_feed_state.condense_list:
        if _condensed_view_has_content():
            _condensed_integrity_failures = 0
            # Python thinks condensed is healthy — still verify the browser DOM.
            schedule_feed_dom_probe(f"condensed_ok:{reason}")
            return
        # A rebuild in flight is expected to leave the surface empty briefly;
        # re-check after it finishes instead of counting a failure.
        if _condensed_rebuild_running or _condensed_update_scheduled:
            app_schedule(
                0.5,
                lambda: _ensure_feed_integrity(f"{reason}_await_rebuild"),
                once=True,
            )
            return
        _condensed_integrity_failures += 1
        logger.warning(
            "activity_feed: condensed integrity mismatch (%s) — "
            "empty condensed surface (failure %d)",
            reason,
            _condensed_integrity_failures,
        )
        if _condensed_integrity_failures >= 2:
            _fallback_to_regular_feed(
                f"integrity_exhausted:{reason}", disable_condense=True
            )
            return
        # Schedule a rebuild; do not treat "scheduled" as a healthy surface.
        _schedule_condensed_rebuild(f"integrity:{reason}")
        app_schedule(
            0.5,
            lambda: _ensure_feed_integrity(f"{reason}_recheck"),
            once=True,
        )
        return

    if not activity_feed_state.live_alerts:
        return
    if _count_rendered_live_alerts() > 0:
        schedule_feed_dom_probe(f"regular_ok:{reason}")
        return

    logger.warning(
        "activity_feed: integrity mismatch (%s) — %d live alerts, 0 rendered",
        reason,
        len(activity_feed_state.live_alerts),
    )
    recover_activity_feed_panel()


def schedule_condensed_view_update(reason: str) -> None:
    """Debounced condensed view refresh (avoids clear/rebuild storms)."""
    global _condensed_update_scheduled

    if not activity_feed_state.condense_list:
        return
    if activity_feed_state.current_tab != "current":
        return
    if _condensed_update_scheduled:
        return
    _condensed_update_scheduled = True

    def _deferred() -> None:
        global _condensed_update_scheduled
        _condensed_update_scheduled = False
        try:
            update_condensed_view()
        except Exception as exc:
            logger.error(
                "activity_feed: debounced condensed update failed (%s): %s",
                reason,
                exc,
                exc_info=True,
            )

    app_schedule(0.3, _deferred, once=True)


def schedule_feed_integrity_check(reason: str) -> None:
    """Debounced integrity check after destructive UI operations."""
    global _integrity_check_reason, _integrity_check_scheduled

    _integrity_check_reason = reason
    if _integrity_check_scheduled:
        return
    _integrity_check_scheduled = True

    def _deferred() -> None:
        global _integrity_check_scheduled
        _integrity_check_scheduled = False
        _ensure_feed_integrity(_integrity_check_reason or "unspecified")

    app_schedule(0.2, _deferred, once=True)


_dom_probe_scheduled = False
_dom_probe_reason: Optional[str] = None


def schedule_feed_dom_probe(reason: str) -> None:
    """Debounced client-side DOM probe (catches Vue remount / outbox desync)."""
    global _dom_probe_scheduled, _dom_probe_reason

    if not _feed_expects_visible_content():
        return
    _dom_probe_reason = reason
    if _dom_probe_scheduled:
        return
    _dom_probe_scheduled = True

    def _deferred() -> None:
        global _dom_probe_scheduled
        _dom_probe_scheduled = False
        background_tasks.create(
            _probe_feed_dom_async(_dom_probe_reason or "unspecified"),
            name="activity_feed_dom_probe",
        )

    app_schedule(0.3, _deferred, once=True)


def _get_connected_client() -> Any:
    """Return a NiceGUI client with an active socket, if any."""
    try:
        from nicegui import Client

        for inst in Client.instances.values():
            if getattr(inst, "has_socket_connection", False):
                return inst
    except Exception:
        pass
    return None


async def _probe_feed_dom_async(reason: str) -> None:
    """Ask the browser whether a visible feed surface has children."""
    global _dom_desync_failures

    if not _feed_expects_visible_content():
        _dom_desync_failures = 0
        return
    if _condensed_rebuild_running or _condensed_update_scheduled:
        # Rebuild in flight — re-check shortly instead of false-positive desync.
        app_schedule(
            0.5,
            lambda: schedule_feed_dom_probe(f"{reason}_await_rebuild"),
            once=True,
        )
        return

    client = _get_connected_client()
    if client is None:
        return

    try:
        result = await client.run_javascript(_FEED_DOM_PROBE_JS, timeout=5.0)
    except Exception as exc:
        msg = str(exc).lower()
        if "client" in msg and "deleted" in msg:
            _handle_stale_client(f"dom_probe:{reason}")
            return
        logger.debug("activity_feed: dom probe failed (%s): %s", reason, exc)
        return

    if not isinstance(result, dict):
        return

    ok = bool(result.get("ok"))
    probe_reason = str(result.get("reason") or "unknown")
    children = int(result.get("children") or 0)
    expected = len(activity_feed_state.live_alerts)

    if ok and children > 0:
        _dom_desync_failures = 0
        return

    _dom_desync_failures += 1
    logger.warning(
        "activity_feed: dom_desync (%s) — probe=%s children=%d expected=%d "
        "(failure %d)",
        reason,
        probe_reason,
        children,
        expected,
        _dom_desync_failures,
    )

    if _dom_desync_failures >= _DOM_DESYNC_RELOAD_THRESHOLD:
        _dom_desync_failures = 0
        _escalate_page_reload("feed_dom_desync")
        return

    # Prefer visibility fix, then panel recover / condensed fallback.
    if probe_reason == "no_visible_surface":
        if _fix_visibility_desync(f"dom:{reason}"):
            schedule_feed_dom_probe(f"after_visibility:{reason}")
            return

    if activity_feed_state.condense_list:
        # Show regular immediately so the panel is not blank, then rebuild
        # condensed (it will hide regular again when ready).
        _ensure_regular_feed_populated(f"dom_desync:{reason}")
        _apply_condensed_visibility(False)
        _schedule_condensed_rebuild(f"dom_desync:{reason}")
        schedule_feed_integrity_check(f"dom_desync:{reason}")
    else:
        if not recover_activity_feed_panel():
            _escalate_page_reload("feed_dom_desync_recover_failed")


def _run_feed_watchdog() -> None:
    """Periodic integrity + DOM probe — catches silent blanks with no mutations."""
    try:
        from modules.shutdown import is_shutdown_in_progress

        if is_shutdown_in_progress():
            return
    except Exception:
        pass

    if not activity_feed_state.is_initialized:
        return
    if activity_feed_state.current_tab != "current":
        return
    if not _feed_expects_visible_content():
        return

    schedule_feed_integrity_check("periodic_watchdog")


def _start_feed_watchdog() -> None:
    """Start the repeating feed-blank watchdog (idempotent)."""
    global _feed_watchdog_started
    if _feed_watchdog_started:
        return
    _feed_watchdog_started = True
    app_schedule(_FEED_WATCHDOG_INTERVAL_SEC, _run_feed_watchdog, active=True)
    logger.warning(
        "activity_feed: feed watchdog started (interval=%ss)",
        int(_FEED_WATCHDOG_INTERVAL_SEC),
    )


def _run_on_ui_loop(fn: Callable[[], Any]) -> None:
    """Marshal UI mutations onto NiceGUI's loop and into a live client slot.

    ``app.timer`` / raw ``call_soon_threadsafe`` have no client context, so creating
    feed elements after tray restore fails with a slot-stack error. Enter the
    connected client the same way custom-sources OBS apply does.
    """

    def _invoke() -> None:
        client = None
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
            logger.error("activity_feed UI callback failed: %s", exc, exc_info=True)

    run_on_ui_loop(_invoke)


def _handle_stale_client(reason: str) -> None:
    """Mark feed stale and schedule recovery (idempotent)."""
    global _recovery_scheduled

    logger.warning("activity_feed: client_stale (%s)", reason)
    activity_feed_state.is_initialized = False
    if _recovery_scheduled:
        return
    _recovery_scheduled = True

    def _recover() -> None:
        global _recovery_scheduled
        try:
            recover_after_client_reconnect()
        finally:
            _recovery_scheduled = False

    _run_on_ui_loop(_recover)


def _escalate_page_reload(reason: str) -> None:
    try:
        from modules.shutdown import is_shutdown_in_progress

        if is_shutdown_in_progress():
            logger.debug(
                "activity_feed: reload_escalation skipped during shutdown (%s)", reason
            )
            return
    except Exception:
        pass

    logger.warning("activity_feed: reload_escalation (%s)", reason)
    try:
        from nicegui import Client, background_tasks

        client = None
        for inst in Client.instances.values():
            if getattr(inst, "has_socket_connection", False):
                client = inst
                break
        if client is None:
            logger.debug("activity_feed: reload skipped (no connected client)")
            return

        async def _reload() -> None:
            try:
                await client.run_javascript(
                    "window.location.reload()", timeout=5.0
                )
            except Exception as exc:
                logger.error("activity_feed: page reload failed: %s", exc, exc_info=True)

        background_tasks.create(_reload(), name="activity_feed_reload")
    except Exception as exc:
        logger.error("activity_feed: page reload failed: %s", exc, exc_info=True)


def recover_activity_feed_panel() -> bool:
    """Rebuild visible feed content when containers are still bound to a live client."""
    global _dom_desync_failures, _stale_ui_skip_logged

    if not _containers_alive():
        return False

    try:
        _null_live_alert_element_refs()
        rebuild_current_alerts_feed()
        if activity_feed_state.condense_list and activity_feed_state.current_tab == "current":
            # Schedule async condensed rebuild; regular feed stays populated as fallback.
            _schedule_condensed_rebuild("recover_activity_feed_panel")
            schedule_feed_integrity_check("recover_activity_feed_panel")
        else:
            _apply_condensed_visibility(False)
        update_alert_visibility()
        activity_feed_state.is_initialized = True
        _stale_ui_skip_logged = False
        _dom_desync_failures = 0
        logger.warning(
            "activity_feed: recovered (%d live alerts)",
            len(activity_feed_state.live_alerts),
        )
        schedule_feed_dom_probe("after_recover")
        return True
    except Exception as exc:
        logger.error("activity_feed: recover_activity_feed_panel failed: %s", exc, exc_info=True)
        return False


def recover_after_client_reconnect() -> None:
    """Called after NiceGUI socket reconnect or stale-client detection."""
    from ..shutdown import is_shutdown_in_progress

    if is_shutdown_in_progress():
        return

    if recover_activity_feed_panel():
        return

    _escalate_page_reload("containers_dead_after_reconnect")


_connect_recovery_enabled = False


_hooks_registered_client_ids: set[str] = set()


def register_client_lifecycle_hooks() -> None:
    """Register connect/disconnect/delete handlers for Activity Feed recovery."""
    global _connect_recovery_enabled
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

    def _on_disconnect(_client=None) -> None:
        # Transient socket blips are common under load. Do not flip
        # is_initialized — that used to drop alerts until reconnect recovery.
        logger.warning("activity_feed: client_disconnected")

    def _on_connect(_client=None) -> None:
        if not _connect_recovery_enabled:
            return
        logger.warning("activity_feed: client_connected — scheduling recovery")
        app_schedule(0.5, recover_after_client_reconnect, once=True)

    def _on_delete(_client=None) -> None:
        # Every minimize/restore cycle retires a client id, so the guard set has to
        # give the id back or it grows for the life of the process.
        _hooks_registered_client_ids.discard(client_id)
        logger.warning("activity_feed: client_deleted — scheduling recovery")
        _handle_stale_client("client_deleted")

    client.on_disconnect(_on_disconnect)
    client.on_connect(_on_connect)
    client.on_delete(_on_delete)
    app_schedule(3.0, _enable_connect_recovery, once=True)
    logger.warning("activity_feed: client lifecycle hooks registered")


def _enable_connect_recovery() -> None:
    global _connect_recovery_enabled
    _connect_recovery_enabled = True


# Event-based system instead of continuous polling
class AlertEventHandler:
    """Thread-safe event handler for processing alerts without continuous polling"""

    def __init__(self):
        self._executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="AlertEvent"
        )
        self._ui_update_lock = threading.Lock()
        self._timestamp_update_event = threading.Event()
        self._timestamp_update_timer = None
        self._should_stop = False

    def process_alert_immediately(self, alert_data: Dict[str, Any]) -> None:
        """Process an alert immediately in a thread-safe manner"""
        try:
            # Submit to thread pool for thread-safe processing
            future = self._executor.submit(self._process_alert_threadsafe, alert_data)

            # Don't wait for completion to avoid blocking the caller
            logger.debug(
                f"Submitted alert for processing: {alert_data.get('type', 'Unknown')}"
            )

        except Exception as e:
            logger.error(f"Error submitting alert for processing: {e}", exc_info=True)

    def _process_alert_threadsafe(self, alert_data: Dict[str, Any]) -> None:
        """Process alert in a thread-safe manner (runs in thread pool)"""
        try:
            current_time = time.time()
            created_time = alertutils.safe_alert_timestamp(
                alert_data.get("created_at"), default=current_time
            )
            is_recent = (current_time - created_time) < 300  # 5 minutes

            new_alert_data = {
                "type": alert_data["type"],
                "message": alert_data["message"],
                "badge_type": alert_data["badge_type"],
                "timestamp": alertutils.safe_alert_timestamp(
                    alert_data.get("timestamp"), default=current_time
                ),
                "created_at": created_time,
                "element": None,
                "tier": alert_data.get("tier"),
                "user_message": alert_data.get("user_message"),
                "new_badge": None,
                "timestamp_label": None,
                "is_recent": is_recent,
                "is_restored": False,
                "stored_alert_data": alert_data.get("stored_alert_data"),
                "alert_id": alert_data.get("alert_id"),
                "username": alert_data.get("username"),
            }

            _run_on_ui_loop(
                lambda: self._apply_alert_on_ui(new_alert_data, alert_data["type"])
            )
            self._trigger_timestamp_update()

        except Exception as e:
            logger.error(f"Error in thread-safe alert processing: {e}", exc_info=True)

    def _apply_alert_on_ui(
        self, new_alert_data: Dict[str, Any], alert_type: str
    ) -> None:
        """Apply a new alert to UI state (must run on NiceGUI loop)."""
        global _stale_ui_skip_logged

        with self._ui_update_lock:
            # Always keep alert state so recovery can repaint after desync.
            activity_feed_state.live_alerts.insert(0, new_alert_data)
            _trim_live_alerts()

            ui_ready = (
                activity_feed_state.is_initialized and _containers_alive()
            )
            if not ui_ready:
                if not _stale_ui_skip_logged:
                    logger.warning(
                        "activity_feed: UI stale — alert kept in state, "
                        "skipping paint (%s); scheduling recovery",
                        alert_type,
                    )
                    _stale_ui_skip_logged = True
                # Only recover when containers existed and then went stale —
                # never escalate during pre-init startup.
                if (
                    activity_feed_state.current_alerts_container is not None
                    or activity_feed_state.condensed_container is not None
                ):
                    _handle_stale_client("apply_alert_on_ui")
                return

            _stale_ui_skip_logged = False

            should_display_new_alert = activity_feed_state.current_tab == "current"
            condensed_active = (
                activity_feed_state.condense_list
                and activity_feed_state.current_tab == "current"
            )

            if condensed_active:
                # Avoid dual-feed churn: keep state only; condensed rebuild owns the surface.
                schedule_condensed_view_update("apply_alert_on_ui")
                logger.debug(
                    "Queued condensed update for new alert: %s (skipped regular card)",
                    alert_type,
                )
            elif should_display_new_alert:
                if not create_alert_element(new_alert_data):
                    return
                self._apply_filter_visibility(new_alert_data, alert_type)
                element = new_alert_data.get("element")
                if _element_alive(element):
                    element.update()
                logger.debug(
                    f"Displayed new alert: {alert_type} (current tab: {activity_feed_state.current_tab})"
                )
                schedule_feed_integrity_check("apply_alert_on_ui")
            else:
                logger.debug(
                    f"New alert {alert_type} added to state but not displayed - on {activity_feed_state.current_tab} tab"
                )

    def _apply_filter_visibility(
        self, new_alert_data: Dict[str, Any], alert_type: str
    ) -> None:
        """Apply filter visibility to new alert"""
        filter_key = None
        if alert_type == "Points":
            filter_key = "points"
        elif alert_type == "Follow":
            filter_key = "follows"
        elif alert_type == "Bits":
            filter_key = "bits"
        elif alert_type == "Sub":
            filter_key = "subs"
        elif alert_type == "Resub":
            filter_key = "resubs"
        elif alert_type == "Giftsub":
            filter_key = "giftsubs"
        elif alert_type == "Donation":
            filter_key = "donations"
        elif alert_type == "Membership":
            filter_key = "subs"
        elif alert_type == "Member Milestone":
            filter_key = "resubs"
        elif alert_type == "Gift Membership":
            filter_key = "giftsubs"
        elif alert_type in ("Super Chat", "Super Sticker"):
            filter_key = "donations"
        elif alert_type == "Raid":
            filter_key = "raids"
        elif alert_type == "Streak":
            filter_key = "streaks"
        elif alert_type == "Hype Train":
            filter_key = "hype_train"

        logger.debug(
            f"New alert of type {alert_type} mapped to filter key: {filter_key}"
        )

        if filter_key and not (
            activity_feed_state.filter_state.get("all", True)
            or activity_feed_state.filter_state.get(filter_key, True)
        ):
            element = new_alert_data.get("element")
            if _element_alive(element):
                element.classes(add="hidden")

    def _trigger_timestamp_update(self) -> None:
        """Trigger a timestamp update cycle instead of continuous polling"""
        self._timestamp_update_event.set()

        # Cancel existing timer if any
        if self._timestamp_update_timer:
            self._timestamp_update_timer.cancel()

        # Schedule next update in 30 seconds (instead of continuous 5-second polling)
        self._timestamp_update_timer = threading.Timer(
            30.0, self._scheduled_timestamp_update
        )
        self._timestamp_update_timer.daemon = True
        self._timestamp_update_timer.start()

    def _scheduled_timestamp_update(self) -> None:
        """Perform scheduled timestamp update (threading.Timer callback)."""
        if self._should_stop:
            return
        _run_on_ui_loop(self._run_scheduled_timestamp_update_ui)

    def _run_scheduled_timestamp_update_ui(self) -> None:
        try:
            if (
                activity_feed_state.current_tab == "current"
                and len(activity_feed_state.live_alerts) > 0
                and _containers_alive()
            ):
                with self._ui_update_lock:
                    self._update_timestamps_and_recent_status()
                self._trigger_timestamp_update()
            else:
                logger.debug("No live alerts to update, skipping timestamp update")
        except Exception as e:
            logger.error(f"Error in scheduled timestamp update: {e}", exc_info=True)

    def _update_timestamps_and_recent_status(self) -> None:
        """Update timestamps and recent status for live alerts"""
        current_time = time.time()
        updates_made = False

        # Update each LIVE alert's recent status (not restored alerts)
        for alert_data in activity_feed_state.live_alerts:
            # Skip restored alerts entirely
            if alert_data.get("is_restored", False):
                continue

            element = alert_data.get("element")
            new_badge = alert_data.get("new_badge")
            if not _element_alive(element) or not _element_alive(new_badge):
                continue

            try:
                created_time = alertutils.safe_alert_timestamp(
                    alert_data.get("created_at"), default=current_time
                )
                time_diff = current_time - created_time
                is_recent = time_diff < 300  # 5 minutes

                # Check if badge visibility needs to change
                badge_hidden = "hidden" in getattr(new_badge, "_classes", [])
                should_hide = not is_recent

                if badge_hidden != should_hide:
                    if should_hide:
                        new_badge.classes(add="hidden")
                    else:
                        new_badge.classes(remove="hidden")
                    updates_made = True

                # Update timestamp if it exists
                timestamp_label = alert_data.get("timestamp_label")
                if _element_alive(timestamp_label):
                    old_timestamp = timestamp_label.text
                    new_timestamp = format_timestamp(alert_data.get("timestamp", ""))
                    if old_timestamp != new_timestamp:
                        timestamp_label.set_text(new_timestamp)
                        updates_made = True

            except Exception as e:
                logger.error(f"Error updating individual live alert status: {str(e)}")

        # Only force UI update if changes were made
        if updates_made:
            container = activity_feed_state.current_alerts_container
            if _element_alive(container):
                container.update()
            logger.debug("Completed timestamp/recent status update cycle with changes")

    def force_timestamp_update(self) -> None:
        """Force an immediate timestamp update (called when switching tabs, etc.)"""
        _run_on_ui_loop(self._force_timestamp_update_ui)

    def _force_timestamp_update_ui(self) -> None:
        try:
            if activity_feed_state.current_tab == "current" and _containers_alive():
                with self._ui_update_lock:
                    self._update_timestamps_and_recent_status()
        except Exception as e:
            logger.error(f"Error in forced timestamp update: {e}", exc_info=True)

    def shutdown(self) -> None:
        """Shutdown the event handler"""
        self._should_stop = True
        if self._timestamp_update_timer:
            self._timestamp_update_timer.cancel()
        self._executor.shutdown(wait=False)
        logger.debug("Alert event handler shutdown complete")


# Global event handler instance
alert_event_handler = AlertEventHandler()

# Dropdown visibility is now tracked in activity_feed_state.dropdown_visible


def format_timestamp(timestamp):
    """Format a timestamp in a user-friendly way.

    Args:
        timestamp (float or str): Unix timestamp or "now"

    Returns:
        str: Formatted timestamp like "just now", "2m ago", "1h ago", etc.
    """
    if timestamp == "now":
        return "just now"

    # Convert timestamp to float if it's a string
    try:
        timestamp_float = float(timestamp)
    except (ValueError, TypeError):
        logger.warning(f"Invalid timestamp format: {timestamp}, using current time")
        timestamp_float = time.time()

    current_time = time.time()
    diff_seconds = current_time - timestamp_float

    if diff_seconds < 60:
        return "just now"
    elif diff_seconds < 3600:
        minutes = int(diff_seconds / 60)
        return f"{minutes}m ago"
    elif diff_seconds < 86400:
        hours = int(diff_seconds / 3600)
        return f"{hours}h ago"
    else:
        days = int(diff_seconds / 86400)
        return f"{days}d ago"


# Create a class to store the reference to the activity feed container
class ActivityFeedState:
    def __init__(self):
        self.activity_feed_container: Optional[ui.element] = None
        self.current_alerts_container: Optional[ui.element] = None
        self.previous_alerts_container: Optional[ui.element] = None
        self.is_initialized: bool = False
        self.alert_processor_thread: Optional[threading.Thread] = None
        self.should_stop: bool = False
        self.update_status_thread: Optional[threading.Thread] = None
        self.alert_elements: List[
            Dict[str, Any]
        ] = []  # Store references to alert elements for updating
        self.live_alerts: List[Dict[str, Any]] = []  # Store live alerts separately
        self.timestamp_labels: Dict[
            str, Any
        ] = {}  # Store references to timestamp labels for updating
        # Add filter state to track which event types are visible
        self.filter_state: Dict[str, bool] = {
            "all": True,
            "follows": True,
            "subs": True,
            "resubs": True,
            "giftsubs": True,
            "bits": True,
            "points": True,
            "donations": True,
            "raids": True,
            "streaks": True,
            "hype_train": True,
        }
        # Map alert types to filter keys
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
            "Hype Train": "hype_train",
            # YouTube-native labels (map onto existing filter chips)
            "Membership": "subs",
            "Member Milestone": "resubs",
            "Gift Membership": "giftsubs",
            "Super Chat": "donations",
            "Super Sticker": "donations",
        }
        # Track dropdown visibility state
        self.dropdown_visible: bool = False
        # Pagination state for restored alerts
        self.current_page: int = 1
        self.total_pages: int = 1
        self.pagination_limit: int = 25
        self.restored_alerts_loaded: bool = False
        self.pagination_container: Optional[ui.element] = None
        # Tab state
        self.current_tab: str = "current"  # "current" or "previous"
        self.tab_container: Optional[ui.element] = None
        # Condense list state
        self.condense_list: bool = False
        self.condense_toggle: Optional[ui.element] = None
        self.condensed_container: Optional[ui.element] = None
        self.alerts_muted: bool = False
        self.pause_btn: Optional[Any] = None
        self.mute_btn: Optional[Any] = None
        self.pause_breath_phase: float = 0.0
        self.pause_breath_timer: Optional[ui.timer] = None
        # Filter dropdown reference
        self.filter_dropdown: Optional[ui.element] = None
        # Timer for click-outside detection
        self.dropdown_timer: Optional[ui.timer] = None
        # Backdrop reference
        self.backdrop: Optional[ui.element] = None


# Global instance
activity_feed_state = ActivityFeedState()


def _animate_pause_button_border() -> None:
    """Drive the playing-state border breathe via inline inset ring (CSS animation blocked by Quasar)."""
    btn = activity_feed_state.pause_btn
    if not _element_alive(btn):
        return
    try:
        from modules import web_engine

        if getattr(web_engine, "ALERTS_PAUSED", False):
            btn.style("box-shadow: none")
            return
        activity_feed_state.pause_breath_phase = (
            activity_feed_state.pause_breath_phase + 0.05
        ) % 3.0
        phase = activity_feed_state.pause_breath_phase / 3.0
        alpha = 0.2 + 0.8 * (0.5 - 0.5 * math.cos(phase * math.pi * 2))
        btn.style(f"box-shadow: inset 0 0 0 1px rgba(34, 197, 94, {alpha:.3f})")
    except Exception as exc:
        logger.debug("activity_feed: pause button breath update failed: %s", exc)


def replay_alert(alert_data):
    """Replay a specific alert by fetching stored data directly from database"""
    try:
        logger.debug(f"Replaying alert: {alert_data}")

        from modules import alert_processor, alertutils

        # Get the alert ID from the alert data
        alert_id = alert_data.get("alert_id")
        if not alert_id:
            logger.error("No alert_id found in alert data, cannot replay")
            from nicegui import ui

            notify("Cannot replay alert: no alert ID available", type="negative")
            return

        # First try to get stored alert data from the alert data itself (for current alerts)
        stored_alert_data = alert_data.get("stored_alert_data")

        # If not available in the alert data, fetch from database (for previous alerts)
        if not stored_alert_data:
            alertutils.alert_state_manager.initialize()
            stored_alert_data = alertutils.alert_state_manager.get_stored_alert_by_id(
                alert_id
            )

        if not stored_alert_data:
            logger.error(f"No stored alert data found for alert_id: {alert_id}")
            from nicegui import ui

            notify("Cannot replay alert: stored data not found", type="negative")
            return

        logger.debug(
            f"Fetched stored alert data for replay: {list(stored_alert_data.keys())}"
        )

        # Create AlertObj and populate all fields from stored data
        replay_alert_obj = alertutils.AlertObj()

        # Copy all available fields from stored alert data to ensure complete AlertObj
        alert_fields = [
            "duration",
            "alert_name",
            "display_name",
            "alert_type",
            "deleted",
            "alert_id",
            "played",
            "stackable",
            "timestamp",
            "skip_alert",
            "is_replay",
            "is_test",
            "username",
            "anonymous",
            "message",
            "emotes",
            "title",
            "tier",
            "gift_qty",
            "recipient",
            "resub_month",
            "months_prepaid",
            "amt_cheered",
            "twitch_reward_id",
            "point_cost",
            "enable_alert",
            "raider_count",
            "game_name",
            "donation_amount",
            "currency",
            "hype_train_level",
            "hype_train_in_progress",
            "fade_in",
            "fade_out",
            "volume",
            "audio_only",
            "single_audio_dir",
            "single_audio_name",
            "gif_dir",
            "gif_name",
            "randomized",
            "randomized_dir",
            "randomized_chance",
            "randomized_extra",
            "randomized_extra_chance",
            "randomized_extra_dir",
        ]

        for field in alert_fields:
            if field in stored_alert_data and stored_alert_data[field] is not None:
                setattr(replay_alert_obj, field, stored_alert_data[field])
                logger.debug(
                    f"Set replay alert field {field}: {stored_alert_data[field]}"
                )

        # Override replay-specific fields
        replay_alert_obj.alert_id = f"Replay{round(time.time())}"
        replay_alert_obj.timestamp = time.time()
        replay_alert_obj.played = False
        replay_alert_obj.stackable = (
            True  # Make replayed alerts stackable for immediate processing
        )
        replay_alert_obj.is_replay = True  # Mark as replay alert

        logger.debug(
            f"Created replay AlertObj - type: {replay_alert_obj.alert_type}, "
            f"gif_dir: {replay_alert_obj.gif_dir}, gif_name: {replay_alert_obj.gif_name}, "
            f"audio_dir: {replay_alert_obj.single_audio_dir}, audio_name: {replay_alert_obj.single_audio_name}"
        )

        # Append the created AlertObj to the ALERT_QUEUE for processing
        alert_processor.enqueue_alert(replay_alert_obj)
        logger.debug(
            f"Added replay alert to ALERT_QUEUE: {replay_alert_obj.alert_type} (ID: {replay_alert_obj.alert_id})"
        )

        # Show user feedback
        from nicegui import ui

        notify(f"Replaying {alert_data.get('type', 'alert')} alert", type="info")

    except Exception as e:
        logger.error(f"Error replaying alert: {str(e)}", exc_info=True)
        from nicegui import ui

        notify(f"Error replaying alert: {str(e)}", type="negative")


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
    """Skip a specific alert"""
    try:
        logger.debug(f"Skipping alert: {alert_data}")

        # Method 1: Use web engine to skip current alert if it's playing
        from modules import web_engine

        if (
            hasattr(web_engine, "web_engine_instance")
            and web_engine.web_engine_instance
        ):
            # Clean the alert data by removing non-serializable UI elements
            clean_alert_data = {
                key: value
                for key, value in alert_data.items()
                if key not in ["element", "new_badge", "timestamp_label"]
                and not hasattr(value, "_classes")
            }

            # Overlay listens for alerts_skip_alert (connector skip uses the same event)
            web_engine.web_engine_instance.safe_emit(
                "alerts_skip_alert", clean_alert_data
            )
            web_engine.set_alert_playing(False)
            web_engine.EXPECTED_ALERT_COMPLETE_SEQ = None
            logger.debug(
                f"Sent skip request via websocket for alert: {clean_alert_data.get('type', 'Unknown')}"
            )

        # Method 2: If this is a queued alert, we could try to find and remove it from the queue
        # However, since this is called from the activity feed (already processed alerts),
        # we mainly just provide user feedback

        # Show user feedback
        from nicegui import ui

        notify(f"Skipped {alert_data.get('type', 'alert')} alert", type="warning")

    except Exception as e:
        logger.error(f"Error skipping alert: {str(e)}", exc_info=True)


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
):
    """
    Build the same dict that is sent over ``activity_feed_alert`` WebSocket events.

    Shared by ``add_alert_to_feed`` and browser-source preview so payloads stay identical.
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

    if point_cost is not None:
        try:
            alert_data["point_cost"] = int(point_cost)
        except (TypeError, ValueError):
            pass

    if level is not None:
        try:
            alert_data["level"] = int(level)
        except (TypeError, ValueError):
            pass

    if hype_train_type:
        alert_data["hype_train_type"] = str(hype_train_type).strip().lower()

    if gift_qty is not None:
        try:
            alert_data["gift_qty"] = int(gift_qty)
        except (TypeError, ValueError):
            pass

    if recipient is not None:
        recip = str(recipient).strip()
        if recip:
            alert_data["recipient"] = recip

    if alert_id:
        try:
            alertutils.alert_state_manager.initialize()
            stored_alert_data = alertutils.alert_state_manager.get_stored_alert_by_id(
                alert_id
            )

            if stored_alert_data:
                logger.debug(
                    f"Found stored alert data for live alert {alert_id}: {list(stored_alert_data.keys())}"
                )
                alert_data["stored_alert_data"] = stored_alert_data
                alert_data["alert_id"] = alert_id

                if isinstance(stored_alert_data, dict):
                    if (
                        "username" in stored_alert_data
                        and stored_alert_data["username"]
                    ):
                        alert_data["username"] = stored_alert_data["username"]
                    streak_count = stored_alert_data.get("streak_count")
                    if streak_count is not None:
                        try:
                            alert_data["streak_count"] = int(streak_count)
                        except (TypeError, ValueError):
                            pass
                    game_name = stored_alert_data.get("game_name")
                    if game_name:
                        alert_data["game_name"] = game_name
                    point_cost = stored_alert_data.get("point_cost")
                    if point_cost is not None and "point_cost" not in alert_data:
                        try:
                            alert_data["point_cost"] = int(point_cost)
                        except (TypeError, ValueError):
                            pass
                    for field in (
                        "resub_month",
                        "gift_qty",
                        "amt_cheered",
                        "tier",
                        "recipient",
                    ):
                        value = stored_alert_data.get(field)
                        if value is not None and field not in alert_data:
                            alert_data[field] = value
                    for field in ("emotes", "fragments"):
                        value = stored_alert_data.get(field)
                        if value is not None and field not in alert_data:
                            alert_data[field] = value
            else:
                logger.debug(f"No stored alert data found for alert_id {alert_id}")

        except Exception as e:
            logger.warning(
                f"Error retrieving stored alert data for {alert_id}: {str(e)}"
            )

    if not alert_data.get("username"):
        parsed = extract_username_from_message(message)
        if parsed and parsed != "Unknown":
            alert_data["username"] = parsed

    chat_media = alertutils.resolve_chat_alert_media(
        alert_data, preview_placeholder=False
    )
    if chat_media:
        alert_data["chat_media"] = chat_media

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
            "Follow",
            f"{username} just followed!",
            "follow",
            timestamp=ts,
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
            "Follow",
            f"{username} just followed!",
            "follow",
            timestamp=ts,
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

    return payload


def iter_activity_feed_preview_payloads():
    """Sample alerts matching production-style messages (Custom Sources iframe only).

    Produces a varied stream of alerts whose ``type`` and ``badge_type`` values
    match what the live ``add_alert_to_feed`` callers in ``modules/twitch.py``
    emit, so the previewer card styling (badge color, tier ring, etc.) matches
    the real Activity Feed exactly.
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
        "Follow",
        f"{follow_user} just followed!",
        "follow",
        timestamp=ts,
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
    )

    bits_user = pick()
    bits_amount = random.choice((100, 200, 500, 1000, 5000))
    yield build_activity_feed_alert_payload(
        "Bits",
        f"{bits_user} cheered {bits_amount} bits!",
        "bits",
        timestamp=ts,
        user_message="Loving the stream!",
    )

    raid_user = pick()
    raid_count = random.randint(5, 250)
    raid_games = (
        "Just Chatting",
        "Final Fantasy VII",
        "Hollow Knight: Silksong",
        "League of Legends",
    )
    yield build_activity_feed_alert_payload(
        "Raid",
        format_raid_activity_message(
            raid_user, raid_count, random.choice(raid_games)
        ),
        "raid",
        timestamp=ts,
    )

    gift_user = pick()
    gift_qty = random.randint(1, 25)
    gift_tier = random.choice((1, 2, 3))
    gift_recipient = None
    if gift_qty == 1:
        gift_recipient = pick()
        gift_msg = (
            f"{gift_user} gifted a Tier {gift_tier} sub to {gift_recipient}!"
        )
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
    points_payload = build_activity_feed_alert_payload(
        "Points",
        f"{points_user} redeemed '{point_reward}'!",
        "points",
        timestamp=ts,
        user_message=None,
        point_cost=random.choice((100, 250, 500, 1100, 2500, 5000)),
    )
    yield points_payload

    streak_user = pick()
    streak_count = random.randint(2, 50)
    yield build_activity_feed_alert_payload(
        "Streak",
        format_watch_streak_message(streak_user, streak_count),
        "streak",
        timestamp=ts,
        user_message=f"{streak_count} streams in a row!",
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
):
    """Add a new alert card to the activity feed container.

    Args:
        alert_type (str): The type of alert (e.g., "Follow", "Channel Points", "Bits")
        message (str): The alert message text
        badge_type (str): The type of badge to show (affects styling)
        timestamp (str or float): When the alert occurred, can be "now" or a Unix timestamp
        tier (int, optional): The tier level for subscriptions (1, 2, or 3)
        user_message (str, optional): User's message for resubs, bits, points, or donations
        alert_id (str, optional): The alert ID to look up stored alert data
        gift_qty (int, optional): Number of gift subs (giftsub alerts)
        recipient (str, optional): Gift recipient username (single gifts)
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
    )

    # Process the alert immediately using the event-based system
    alert_event_handler.process_alert_immediately(alert_data)

    # Only send the alert via websocket to the HTML template if we're on the current alerts tab
    # If not on current alerts tab, the alert will still be queued and processed by the Python thread
    try:
        from modules import web_engine

        if (
            hasattr(web_engine, "web_engine_instance")
            and web_engine.web_engine_instance
            and activity_feed_state.current_tab == "current"
        ):
            web_engine.web_engine_instance.activity_feed_alert(alert_data)
            logger.debug(
                f"Sent alert to activity feed HTML template via websocket: {alert_type}"
            )
        else:
            logger.debug(
                f"Alert not sent to HTML template - current tab: {activity_feed_state.current_tab}"
            )
    except Exception as e:
        logger.error(
            f"Error sending alert to activity feed HTML template: {str(e)}",
            exc_info=True,
        )


# Legacy functions removed - replaced by event-based system
# The following functions are no longer needed with the event-based approach:
# - update_recent_status() -> replaced by AlertEventHandler._update_timestamps_and_recent_status()
# - start_status_updater() -> replaced by AlertEventHandler._trigger_timestamp_update()
# - stop_status_updater() -> replaced by AlertEventHandler.shutdown()
# - process_alert_queue() -> replaced by AlertEventHandler.process_alert_immediately()
# - start_alert_processor() -> no longer needed (events are processed immediately)
# - stop_alert_processor() -> replaced by AlertEventHandler.shutdown()


def start_alert_processor():
    """Start the event-based alert processor (replaces old queue-based system)"""
    logger.debug("Event-based alert processor is always ready - no startup needed")


def stop_alert_processor():
    """Stop the event-based alert processor"""
    alert_event_handler.shutdown()
    logger.debug("Stopped event-based alert processor")


def rebuild_alert_feed():
    """Rebuild the entire alert feed to ensure proper ordering and styling"""
    try:
        container = activity_feed_state.activity_feed_container
        if not _element_alive(container):
            _handle_stale_client("rebuild_alert_feed: container dead")
            return

        for alert_data in activity_feed_state.alert_elements:
            alert_data["element"] = None
            alert_data["new_badge"] = None
            alert_data["timestamp_label"] = None

        container.clear()

        for alert_data in activity_feed_state.alert_elements:
            if not create_alert_element(alert_data):
                return

        if _element_alive(container):
            container.update()

    except Exception as e:
        if _is_stale_client_error(e):
            _handle_stale_client(f"rebuild_alert_feed: {e}")
        else:
            logger.error(f"Error rebuilding alert feed: {str(e)}", exc_info=True)


def rebuild_current_alerts_feed():
    """Rebuild the current alerts feed, showing only live alerts"""
    try:
        container = activity_feed_state.current_alerts_container
        if not _element_alive(container):
            _handle_stale_client("rebuild_current_alerts_feed: container dead")
            return

        _null_live_alert_element_refs()
        container.clear()

        for alert_data in activity_feed_state.live_alerts:
            if not create_alert_element(alert_data):
                return

        if _element_alive(container):
            container.update()

        logger.debug(
            f"Rebuilt current alerts feed with {len(activity_feed_state.live_alerts)} live alerts"
        )
        schedule_feed_integrity_check("rebuild_current_alerts_feed")

    except Exception as e:
        if _is_stale_client_error(e):
            _handle_stale_client(f"rebuild_current_alerts_feed: {e}")
        else:
            logger.error(f"Error rebuilding current alerts feed: {str(e)}", exc_info=True)


def create_alert_element(alert_data) -> bool:
    """Create a single alert element in the feed.

    Returns:
        True on success, False if the client is stale (recovery scheduled).
    """
    try:
        is_restored = alert_data.get("is_restored", False)

        if is_restored:
            target_container = activity_feed_state.previous_alerts_container
        else:
            target_container = activity_feed_state.current_alerts_container

        if not _element_alive(target_container):
            _handle_stale_client("create_alert_element: container dead")
            return False

        # Recalculate if the alert is recent (within last 5 minutes) based on current time
        current_time = time.time()
        created_time = alertutils.safe_alert_timestamp(
            alert_data.get("created_at"), default=current_time
        )
        is_recent = (
            current_time - created_time
        ) < 300 and not is_restored  # 5 minutes, but never for restored alerts

        # Update the alert data with the current recent status
        alert_data["is_recent"] = is_recent

        # Build class list
        classes = ["alert-card", "w-full", "hover-parent"]
        if is_restored:
            classes.append("restored")
        if is_recent:
            classes.append("recent")

        # Create the alert element
        with target_container:
            alert_element = ui.element("div").classes(" ".join(classes))

        # Store element reference
        alert_data["element"] = alert_element

        # Build the alert content
        with alert_element:
            # Add "new" badge for recent alerts (but not for restored alerts)
            new_badge = ui.label("NEW").classes("new-badge")
            if not is_recent or is_restored:
                new_badge.classes(add="hidden")
            alert_data["new_badge"] = new_badge

            # Container for both main alert and user message
            with ui.element("div").classes("w-full relative"):
                # Main alert row
                with ui.row().classes("w-full h-[40px] items-center px-2"):
                    # Left side with badge and message
                    with ui.row().classes("h-full items-center gap-2"):
                        # Apply tier-specific classes for subscriptions and resubscriptions
                        badge_classes = f"badge {alert_data.get('badge_type', 'follow')} h-[24px] flex items-center"
                        if alert_data.get("tier") in [2, 3] and alert_data.get(
                            "badge_type"
                        ) in ["sub", "resub"]:
                            badge_classes += f" tier{alert_data['tier']}"

                        ui.label(alert_data["type"]).classes(badge_classes)

                        # Combine main message and user message if present
                        display_message = alert_data["message"]
                        if alert_data.get("user_message"):
                            # Wrap user message in span with gray color and keep italics
                            display_message += f" - <span class='secondary-text'>*{alert_data['user_message']}*</span>"

                        ui.markdown(display_message).classes(
                            "text-sm"
                        )  # Use markdown for italics

                # Timestamp (positioned at far right edge)
                with ui.row().classes("timestamp-container"):
                    timestamp_label = ui.label(
                        format_timestamp(alert_data["timestamp"])
                    ).classes("timestamp")
                    alert_data["timestamp_label"] = timestamp_label

                # Action buttons (absolutely positioned)
                with ui.row().classes("action-buttons hover-child opacity-0"):
                    # Replay button
                    ui.button(
                        icon="replay", on_click=lambda a=alert_data: replay_alert(a)
                    ).classes(
                        "replay-button h-[24px] w-[24px] min-w-[24px] flex items-center justify-center"
                    ).tooltip("Replay Alert")

                    # Skip button
                    ui.button(
                        icon="skip_next",
                        on_click=lambda e, a=alert_data: skip_alert(
                            {"type": a["type"], "message": a["message"]}
                        ),
                    ).classes(
                        "replay-button h-[24px] w-[24px] min-w-[24px] flex items-center justify-center"
                    ).tooltip("Skip Alert")

        return True

    except Exception as e:
        if _is_stale_client_error(e):
            _handle_stale_client(f"create_alert_element: {e}")
            return False
        logger.error(f"Error creating alert element: {str(e)}", exc_info=True)
        return False


def update_alert_visibility():
    """Update the visibility of all alerts based on the current filter state"""
    # Store the current dropdown visibility state
    dropdown_was_visible = activity_feed_state.dropdown_visible

    # Update visibility for both live alerts and historical alerts
    alerts_to_process = []

    # Add live alerts if we're on current tab or need to update them
    if activity_feed_state.current_tab == "current":
        alerts_to_process.extend(activity_feed_state.live_alerts)

    # Add historical alerts if we're on previous tab or need to update them
    if activity_feed_state.current_tab == "previous":
        alerts_to_process.extend(activity_feed_state.alert_elements)

    # Batch process alerts for better performance
    updates_needed = 0

    for alert_data in alerts_to_process:
        element = alert_data.get("element")
        if not _element_alive(element):
            continue

        alert_type = alert_data.get("type")

        # Map alert type to filter key (optimized mapping)
        filter_key_map = {
            "Points": "points",
            "Follow": "follows",
            "Bits": "bits",
            "Sub": "subs",
            "Resub": "resubs",
            "Giftsub": "giftsubs",
            "Donation": "donations",
            "Raid": "raids",
            "Streak": "streaks",
            "Hype Train": "hype_train",
        }
        filter_key = filter_key_map.get(alert_type)

        # Show if either:
        # 1. The "All Events" filter is enabled, or
        # 2. The specific filter for this alert type is enabled
        should_show = activity_feed_state.filter_state.get("all", True) or (
            filter_key and activity_feed_state.filter_state.get(filter_key, True)
        )

        # Only update elements that need to change (performance optimization)
        current_hidden = "hidden" in getattr(element, "_classes", [])
        needs_update = (should_show and current_hidden) or (
            not should_show and not current_hidden
        )

        if needs_update:
            # Remove classes one at a time
            element.classes(remove="hidden")
            element.classes(remove="visible")

            # Then add the appropriate class and style
            if should_show:
                element.classes(add="visible")
                element.style("display: block")
            else:
                element.classes(add="hidden")
                element.style("display: none")

            updates_needed += 1

    # Only update UI if we actually made changes
    if updates_needed > 0:
        container = (
            activity_feed_state.current_alerts_container
            if activity_feed_state.current_tab == "current"
            else activity_feed_state.previous_alerts_container
        )
        if _element_alive(container):
            container.update()
        logger.debug(
            f"Updated visibility for {updates_needed} alerts on {activity_feed_state.current_tab} tab"
        )

    # Restore the dropdown visibility state if it was visible before
    if dropdown_was_visible:
        activity_feed_state.dropdown_visible = True
        # Find the filter dropdown element and restore its visibility
        if activity_feed_state.filter_dropdown:
            activity_feed_state.filter_dropdown.visible = True


def close_filter_dropdown():
    """Close the filter dropdown (called from backdrop click handler)"""
    try:
        activity_feed_state.dropdown_visible = False
        if activity_feed_state.filter_dropdown:
            activity_feed_state.filter_dropdown.visible = False
        if activity_feed_state.backdrop:
            activity_feed_state.backdrop.style("display: none;")

        logger.debug("Closed filter dropdown via backdrop click")
    except Exception as e:
        logger.error(f"Error closing filter dropdown: {str(e)}", exc_info=True)


def on_checkbox_change(key, value):
    """Handle checkbox state changes"""
    logger.debug(f"Checkbox changed: {key} = {value}")
    activity_feed_state.filter_state[key] = value

    # If "All Events" is checked, enable all filters
    if key == "all" and value:
        for k in activity_feed_state.filter_state:
            activity_feed_state.filter_state[k] = True
    # If "All Events" is unchecked, keep individual filter states
    elif key == "all" and not value:
        pass
    # If an individual filter is changed and all others are checked, update "All Events"
    elif key != "all":
        all_others_checked = all(
            v
            for k, v in activity_feed_state.filter_state.items()
            if k != "all" and k != key
        )
        if value and all_others_checked:
            activity_feed_state.filter_state["all"] = True
        else:
            activity_feed_state.filter_state["all"] = False

    logger.debug(f"Updated filter state: {activity_feed_state.filter_state}")

    # Update alert visibility without affecting dropdown visibility
    update_alert_visibility()

    # Condensed rows are rebuilt labels (not per-alert cards), so rebuild when filters change
    if (
        activity_feed_state.condense_list
        and activity_feed_state.current_tab == "current"
    ):
        schedule_condensed_view_update("filter_change")


def create_activity_feed_tab():
    """Create the activity feed tab UI"""
    logger.debug("Creating activity feed tab...")

    # Sync tab UI state with the module singleton (survives client reloads).
    activity_feed_state.current_tab = "current"
    activity_feed_state.dropdown_visible = False

    # Add the hover CSS once at the top level
    ui.add_head_html(
        """
        <style>
        .hover-child {
            transition: opacity 0.2s ease-in-out;
            opacity: 0;
            z-index: 10;
            pointer-events: auto;
        }
        .hover-parent:hover .hover-child {
            opacity: 1 !important;
        }
        .hover-parent {
            position: relative;
        }
        .replay-button {
            padding: 4px !important;
            height: 24px !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            background-color: var(--color-hover-overlay) !important;
            border: 1px solid var(--color-border-default) !important;
            color: var(--color-text-primary) !important;
            border-radius: 4px !important;
            cursor: pointer !important;
            transition: all 0.2s ease !important;
            line-height: 1 !important;
        }
        .replay-button:hover {
            background-color: var(--color-active-overlay) !important;
            border-color: var(--color-border-default) !important;
            transform: scale(1.05) !important;
        }
        .alert-card {
            position: relative;
            border-radius: 8px;
            padding: 8px;
            margin-bottom: 8px;
            background-color: var(--color-bg-surface);
            border: 1px solid var(--color-border-subtle);
            transition: background-color 0.2s ease;
        }
        .alert-card:hover {
            background-color: var(--color-hover-overlay);
        }
        /* Badge styling for different alert types */
        .badge {
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            border: 1px solid;
            transition: all 0.3s ease;
        }
        
        /* Follow alerts */
        .badge.follow {
            background: var(--color-primary);
            color: white;
            border-color: var(--color-primary);
        }
        
        /* Subscription alerts */
        .badge.sub, .badge.resub {
            background: var(--color-info);
            color: white;
            border-color: var(--color-info);
        }
        .badge.sub.tier2, .badge.resub.tier2 {
            background: var(--color-error);
            border-color: var(--color-error);
        }
        .badge.sub.tier3, .badge.resub.tier3 {
            background: var(--color-warning);
            border-color: var(--color-warning);
        }
        
        /* Badge animations - only on hover and when visible */
        .alert-card:hover .badge:not(.not-visible) {
            animation: badgePulse 3s infinite;
        }
        
        .alert-card:hover .badge.sub.tier2:not(.not-visible), 
        .alert-card:hover .badge.resub.tier2:not(.not-visible) {
            animation: badgePulse 3s infinite, tier2Glow 2s infinite;
        }
        
        .alert-card:hover .badge.sub.tier3:not(.not-visible), 
        .alert-card:hover .badge.resub.tier3:not(.not-visible) {
            animation: badgePulse 3s infinite, tier3Glow 2s infinite;
        }
        
        @keyframes badgePulse {
            0% {
                box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 3px rgba(255, 255, 255, 0.1);
            }
            50% {
                box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 6px rgba(255, 255, 255, 0.2);
            }
            100% {
                box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 3px rgba(255, 255, 255, 0.1);
            }
        }
        
        @keyframes tier2Glow {
            0% {
                border-color: rgba(255, 255, 255, 0.3);
                box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 5px rgba(255, 255, 255, 0.2);
            }
            50% {
                border-color: rgba(0, 188, 212, 0.5);
                box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 8px rgba(0, 188, 212, 0.4);
            }
            100% {
                border-color: rgba(255, 255, 255, 0.3);
                box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 5px rgba(255, 255, 255, 0.2);
            }
        }
        
        @keyframes tier3Glow {
            0% {
                border-color: rgba(255, 255, 255, 0.3);
                box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 5px rgba(255, 255, 255, 0.2);
            }
            50% {
                border-color: rgba(255, 215, 0, 0.6);
                box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 12px rgba(255, 215, 0, 0.6);
            }
            100% {
                border-color: rgba(255, 255, 255, 0.3);
                box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.4), 0 0 5px rgba(255, 255, 255, 0.2);
            }
        }
        
        /* Gift subscription alerts */
        .badge.giftsub {
            background: var(--color-success);
            color: white;
            border-color: var(--color-success);
        }
        
        /* Watch streak alerts */
        .badge.streak {
            background: var(--color-primary);
            color: white;
            border-color: var(--color-primary);
        }
        
        /* Bits alerts */
        .badge.bits {
            background: var(--color-warning);
            color: white;
            border-color: var(--color-warning);
        }
        
        /* Channel points alerts */
        .badge.points {
            background: var(--color-error);
            color: white;
            border-color: var(--color-error);
        }
        
        /* Donation alerts */
        .badge.donation {
            background: var(--color-success);
            color: white;
            border-color: var(--color-success);
        }
        
        /* Raid alerts */
        .badge.raid {
            background: var(--color-error);
            color: white;
            border-color: var(--color-error);
        }
        
        /* Hype train alerts */
        .badge.hype_train {
            background: var(--color-primary);
            color: white;
            border-color: var(--color-primary);
        }

        /* YouTube-native badges */
        .badge.membership {
            background: var(--color-info);
            color: white;
            border-color: var(--color-info);
        }
        .badge.member_milestone {
            background: var(--color-info);
            color: white;
            border-color: var(--color-info);
        }
        .badge.gift_membership {
            background: #e91e63;
            color: white;
            border-color: #e91e63;
        }
        .badge.superchat {
            background: var(--color-success);
            color: white;
            border-color: var(--color-success);
        }
        .badge.supersticker {
            background: #ff9800;
            color: white;
            border-color: #ff9800;
        }
        
        /* NEW badge styling */
        .new-badge {
            position: absolute;
            top: 4px;
            right: 4px;
            background: var(--color-primary);
            color: white;
            font-size: 0.625rem;
            font-weight: 600;
            padding: 2px 6px;
            border-radius: 3px;
            border: 1px solid rgba(115, 0, 255, 0.6);
            z-index: 5;
            text-transform: uppercase;
            letter-spacing: 0.025em;
            display: inline-block;
            width: auto;
            height: auto;
            max-width: fit-content;
            backdrop-filter: blur(4px);
        }
        
        /* Restored alert styling - muted appearance with animations disabled */
        .alert-card.restored {
            opacity: 0.7;
            background-color: rgba(255, 255, 255, 0.02);
            /* Disable all animations and transitions for performance */
            transition: none !important;
            animation: none !important;
            transform: none !important;
        }
        .alert-card.restored .badge {
            background: linear-gradient(to bottom, rgba(128, 128, 128, 0.1), rgba(96, 96, 96, 0.2)) !important;
            color: rgba(255, 255, 255, 0.6) !important;
            border: 1px solid rgba(128, 128, 128, 0.3) !important;
            /* Disable badge animations */
            transition: none !important;
            animation: none !important;
            transform: none !important;
        }
        .alert-card.restored:hover {
            background-color: rgba(255, 255, 255, 0.05);
            /* Disable hover animations */
            transition: none !important;
            transform: none !important;
        }
        
        /* Disable all animations and transitions for restored alert children */
        .alert-card.restored *,
        .alert-card.restored *:before,
        .alert-card.restored *:after {
            transition: none !important;
            animation: none !important;
            transform: none !important;
        }
        
        /* Specifically disable hover effects on restored alert buttons */
        .alert-card.restored .replay-button {
            transition: none !important;
            animation: none !important;
        }
        .alert-card.restored .replay-button:hover {
            transition: none !important;
            transform: none !important;
            background-color: rgba(255, 255, 255, 0.25) !important;
        }
        
        /* Disable hover child animations for restored alerts */
        .alert-card.restored .hover-child {
            transition: none !important;
            animation: none !important;
        }

        /* Ensure action buttons are properly centered in restored alerts */
        .alert-card.restored .action-buttons {
            position: absolute !important;
            right: 120px !important;
            top: 50% !important;
            transform: translateY(-50%) !important;
            display: flex !important;
            align-items: center !important;
            gap: 4px !important;
            z-index: 10 !important;
        }

        /* Ensure replay buttons are properly sized and centered in restored alerts */
        .alert-card.restored .replay-button {
            height: 24px !important;
            width: 24px !important;
            min-width: 24px !important;
            padding: 4px !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            line-height: 1 !important;
        }
        .alert-card.restored:hover .hover-child {
            transition: none !important;
            animation: none !important;
        }
        
        /* Condense toggle styling */
        .condense-toggle {
            margin-left: 16px;
        }
        .condense-toggle .q-field__label {
            color: rgba(255, 255, 255, 0.9) !important;
            font-size: 13px !important;
            font-weight: 500 !important;
        }
        .condense-toggle .q-toggle__inner {
            color: var(--color-primary) !important;
        }
        .condense-toggle .q-toggle__inner--truthy {
            color: var(--color-primary) !important;
        }
        
        /* Condensed view styling */
        .condensed-view {
            padding: 8px;
            background: rgba(255, 255, 255, 0.02);
            border-radius: 6px;
            border: 1px solid rgba(255, 255, 255, 0.05);
        }
        .condensed-view .user-group {
            margin-bottom: 16px;
            padding: 8px;
            background: rgba(255, 255, 255, 0.03);
            border-radius: 4px;
            border-left: 3px solid var(--color-primary);
        }
        .condensed-view .username {
            font-weight: 600;
            color: white;
            margin-bottom: 4px;
        }
        .condensed-view .alert-item {
            color: rgba(255, 255, 255, 0.8);
            font-size: 14px;
            margin-left: 16px;
            margin-bottom: 2px;
        }
        .condensed-view .alert-item::before {
            content: "•";
            color: rgba(115, 0, 255, 0.8);
            margin-right: 8px;
        }
        /* Ensure action buttons container is positioned correctly */
        .action-buttons {
            position: absolute;
            right: 120px;
            top: 50%;
            transform: translateY(-50%);
            display: flex;
            align-items: center;
            gap: 4px;
            z-index: 10;
        }
        /* Timestamp positioning */
        .timestamp-container {
            position: absolute;
            right: 8px;
            top: 50%;
            transform: translateY(-50%);
            text-align: right;
        }
        .timestamp {
            font-size: 0.875rem;
            color: rgba(255, 255, 255, 0.6);
        }
        .hidden {
            display: none !important;
        }
        .visible {
            display: block !important;
        }
        </style>
    """,
        shared=True,
    )

    with ui.element("div").classes("tab-surface w-full h-full relative flex flex-col p-4"):
        # Control row: keep overflow scroll only on left buttons so the filter
        # dropdown is not clipped into a scrollable top bar (CSS overflow-x:auto
        # forces overflow-y:auto and swallows absolute menus).
        with ui.row().classes("w-full items-center gap-2 mb-4 flex-nowrap"):
            from modules.mainuiwindow import toggle_alerts

            with ui.row().classes(
                "items-center gap-2 flex-nowrap overflow-x-auto min-w-0 shrink"
            ):
                def on_pause_button_click():
                    toggle_alerts()
                    _apply_pause_button_state()

                pause_btn = ui.button(
                    icon="pause", text="PAUSE ALERTS", on_click=on_pause_button_click
                ).classes("control-button pause-alerts-btn alerts-playing").props(
                    f"{_DOCK_BTN_PROPS} id=pause-alerts-btn"
                )
                activity_feed_state.pause_btn = pause_btn
                global _pause_breath_timer_started
                if not _pause_breath_timer_started:
                    _pause_breath_timer_started = True
                    app_schedule(0.1, _animate_pause_button_border, active=True)

                try:
                    _apply_pause_button_state()
                except Exception as e:
                    logger.error(
                        f"Error initializing pause button state: {str(e)}",
                        exc_info=True,
                    )

                def on_mute_button_click():
                    from modules.mainuiwindow import toggle_mute

                    toggle_mute()
                    _apply_mute_button_state()

                mute_btn = ui.button(
                    icon="notifications_off",
                    text="MUTE ALERTS",
                    on_click=on_mute_button_click,
                ).classes("control-button mute-alerts-btn").props(
                    f"{_DOCK_BTN_PROPS} id=mute-alerts-btn"
                )
                activity_feed_state.mute_btn = mute_btn

                try:
                    _apply_mute_button_state()
                except Exception as e:
                    logger.error(
                        f"Error initializing mute button state: {str(e)}",
                        exc_info=True,
                    )
                skip_btn = ui.button(
                    icon="skip_next",
                    text="SKIP ALERT",
                    on_click=lambda: skip_alert({"type": "current"}),
                ).classes("control-button")
                skip_btn.props(_DOCK_BTN_PROPS)

            ui.element("div").classes("grow")

            # Condense List toggle (only visible on Current Alerts tab)
            def toggle_condense_list(e):
                global _ignore_condense_toggle_event
                if _ignore_condense_toggle_event:
                    return
                logger.debug(f"Condense list toggle changed: {e.value}")
                activity_feed_state.condense_list = e.value
                logger.debug(
                    f"Updated activity_feed_state.condense_list to: {activity_feed_state.condense_list}"
                )
                update_condensed_view()

            activity_feed_state.condense_toggle = ui.switch(
                text="Condense List",
                value=activity_feed_state.condense_list,
                on_change=toggle_condense_list,
            ).classes("condense-toggle shrink-0")
            logger.debug(
                "Condense List toggle created (id=%s)",
                getattr(activity_feed_state.condense_toggle, "id", None),
            )

            # Filter dropdown lives outside the overflow-x cluster so it can float
            with ui.element("div").classes("relative shrink-0 z-50"):
                def toggle_filter_dropdown(e):
                    logger.debug(
                        f"Filter button clicked. Current state: {activity_feed_state.dropdown_visible}"
                    )

                    # Toggle the dropdown visibility using the activity_feed_state
                    activity_feed_state.dropdown_visible = (
                        not activity_feed_state.dropdown_visible
                    )

                    # Use the stored reference for reliable access
                    if activity_feed_state.dropdown_visible:
                        # Show the dropdown and backdrop
                        activity_feed_state.filter_dropdown.visible = True
                        activity_feed_state.backdrop.style("display: block;")
                        logger.debug("Showing dropdown with backdrop")
                    else:
                        # Hide the dropdown and backdrop
                        activity_feed_state.filter_dropdown.visible = False
                        activity_feed_state.backdrop.style("display: none;")
                        logger.debug("Hiding dropdown and backdrop")

                filter_button = ui.button(
                    icon="filter_list",
                    text="FILTERS",
                    on_click=toggle_filter_dropdown,
                ).classes("control-button")
                filter_button.props(_DOCK_BTN_PROPS)

                # Register click before style so NiceGUI 3.x does not treat it as a late listener.
                backdrop = ui.element("div").on(
                    "click", lambda e: close_filter_dropdown()
                )
                backdrop.classes("fixed inset-0 z-40")
                backdrop.style("background: transparent; display: none;")

                # Create a custom dropdown container (start visible: false, initially hidden)
                filter_dropdown = ui.element("div").classes(
                    "absolute top-full right-0 z-[60] mt-1 bg-theme-base rounded-md shadow-lg w-56"
                )
                filter_dropdown.visible = (
                    False  # Use NiceGUI's built-in visibility instead of CSS classes
                )

                # Add simple CSS for pagination controls only (remove complex JavaScript)
                ui.add_head_html(
                    """
                    <style>
                    .filter-dropdown-content {
                        background-color: var(--color-bg-elevated);
                        border-radius: 0.375rem;
                        box-shadow: 0 4px 6px -1px var(--color-bg-overlay);
                        width: 14rem;
                        z-index: 50;
                    }
                    /* Ensure checkboxes and labels don't trigger dropdown closing */
                    .filter-checkbox, .filter-checkbox label {
                        pointer-events: auto;
                    }
                    
                    /* Pagination controls styling */
                    .pagination-btn {
                        background-color: var(--color-bg-surface) !important;
                        color: var(--color-text-primary) !important;
                        border: 1px solid var(--color-border-default) !important;
                        padding: 6px 12px !important;
                        border-radius: 6px !important;
                        min-width: 40px !important;
                        height: 32px !important;
                        transition: all 0.2s ease !important;
                    }
                    
                    .pagination-btn:hover:not(:disabled) {
                        background-color: var(--color-hover-overlay) !important;
                        transform: translateY(-1px) !important;
                    }
                    
                    .pagination-btn:disabled {
                        background-color: var(--color-bg-surface) !important;
                        color: var(--color-text-muted) !important;
                        cursor: not-allowed !important;
                        opacity: 0.5 !important;
                    }
                    </style>
                """,
                    shared=True,
                )

                # Store the dropdown and backdrop references in the activity_feed_state
                activity_feed_state.filter_dropdown = filter_dropdown
                activity_feed_state.backdrop = backdrop

                # Create a container for the checkboxes with the filter-dropdown-content class
                with filter_dropdown:
                    with ui.element("div").classes("filter-dropdown-content"):
                        # Create a scrollable container for checkboxes
                        with ui.element("div").classes("max-h-60 overflow-y-auto p-2"):
                            # Create checkboxes for each event type
                            def create_checkbox(key: str, label: str):
                                def on_change(e):
                                    logger.debug(f"Checkbox {key} changed to {e.value}")
                                    on_checkbox_change(key, e.value)

                                    # Only manage dropdown visibility if it's currently visible
                                    if activity_feed_state.dropdown_visible:
                                        # Ensure the dropdown stays visible during checkbox interactions
                                        if activity_feed_state.filter_dropdown:
                                            activity_feed_state.filter_dropdown.visible = True
                                        logger.debug(
                                            "Maintained dropdown visibility after checkbox change"
                                        )

                                ui.checkbox(
                                    text=label,
                                    value=activity_feed_state.filter_state.get(
                                        key, True
                                    ),
                                    on_change=on_change,
                                ).classes(
                                    "text-sm mb-1 w-full whitespace-nowrap filter-checkbox"
                                )

                            # Create each checkbox with its own handler
                            create_checkbox("all", "All Events")
                            create_checkbox("follows", "Follows")
                            create_checkbox("subs", "Subscriptions")
                            create_checkbox("resubs", "Resubscriptions")
                            create_checkbox("giftsubs", "Gift Subs")
                            create_checkbox("bits", "Bits")
                            create_checkbox("points", "Channel Points")
                            create_checkbox("donations", "Donations")
                            create_checkbox("raids", "Raids")
                            create_checkbox("streaks", "Watch streaks")
                            create_checkbox("hype_train", "Hype Train")

        with ui.row().classes("tab-row"):

            def on_current_tab():
                switch_to_tab("current")
                current_tab_btn.classes(add="active")
                previous_tab_btn.classes(remove="active")

            def on_previous_tab():
                switch_to_tab("previous")
                previous_tab_btn.classes(add="active")
                current_tab_btn.classes(remove="active")

            current_tab_btn = ui.button(
                text="CURRENT ALERTS", on_click=on_current_tab
            ).classes("tab-button active")
            current_tab_btn.props(_DOCK_BTN_PROPS)

            previous_tab_btn = ui.button(
                text="PREVIOUS ALERTS", on_click=on_previous_tab
            ).classes("tab-button")
            previous_tab_btn.props(_DOCK_BTN_PROPS)

        # Create a scrollable container for alert cards
        with ui.element("div").classes("scroll-content grow"):
            # Add pagination controls container above the feed (only visible for previous alerts tab)
            activity_feed_state.pagination_container = ui.element("div").classes(
                "w-full mb-2"
            )

            # Add tab container to store content based on active tab
            activity_feed_state.tab_container = ui.element("div").classes("w-full")

            # Create separate containers for each tab
            activity_feed_state.current_alerts_container = ui.element("div").classes(
                "w-full activity-feed-current"
            )
            activity_feed_state.previous_alerts_container = ui.element("div").classes(
                "w-full hidden activity-feed-previous"
            )

            # Create condensed view container (hidden by default)
            activity_feed_state.condensed_container = ui.element("div").classes(
                "w-full hidden condensed-view activity-feed-condensed"
            )

            # Set the main container reference (for backward compatibility)
            activity_feed_state.activity_feed_container = (
                activity_feed_state.current_alerts_container
            )
            activity_feed_state.is_initialized = True
            logger.debug("Activity feed container initialized")

            # Start the alert processor thread
            start_alert_processor()
            logger.debug("Alert processor started")

            # Event-based system doesn't need separate status updater thread
            logger.debug("Event-based system ready - status updates on-demand")

            # Hide pagination initially (only show for previous alerts tab)
            activity_feed_state.pagination_container.classes(add="hidden")

            # Defer condensed-view setup so first paint is never blocked by storage I/O.
            app_schedule(0, update_condensed_view, once=True)

            # Periodic DOM/integrity watchdog catches silent NiceGUI remount blanks.
            _start_feed_watchdog()

            # Start on current alerts tab - no need to load restored alerts initially

            # Add sample alerts to demonstrate the functionality
            current_time = time.time()
            logger.debug(f"Creating sample alerts at time: {current_time}")

            # Add alerts with different timestamps, using created_at for proper timing
            # alerts = [
            #     ("Points", "Test just redeemed Highlight Message!", "points", current_time, None, "This is a test message for channel points redemption!"),
            #     ("Follow", "User1 just followed!", "follow", current_time - 120, None, "Thanks for the follow!"),
            #     ("Bits", "User2 cheered 100 bits!", "bits", current_time - 300, None, "Thanks for the bits!"),
            #     ("Sub", "User3 subscribed!", "sub", current_time - 600, None, "Welcome to the channel!"),
            #     ("Sub", "User4 subscribed!", "sub", current_time - 900, 2, "Thanks for the tier 2 sub!"),
            #     ("Sub", "User5 subscribed!", "sub", current_time - 1200, 3, "Thanks for the tier 3 sub!"),
            #     ("Resub", "User6 resubscribed for 6 months!", "resub", current_time - 1800, None, "Keep up the amazing work!"),
            #     ("Resub", "User7 resubscribed for 12 months!", "resub", current_time - 2400, 2, "Your streams are the highlight of my week!"),
            #     ("Resub", "User8 resubscribed for 24 months!", "resub", current_time - 3000, 3, "Been here since day one, and you're only getting better!"),
            #     ("Donation", "User9 donated $10!", "donation", current_time - 3600, None, "Supporting your channel because you deserve it!")
            # ]

            # for alert_type, message, badge_type, timestamp, tier, user_message in alerts:
            #     # Add the alert to the queue
            #     alert = {
            #         "type": alert_type,
            #         "message": message,
            #         "badge_type": badge_type,
            #         "timestamp": timestamp,  # When the alert occurred
            #         "created_at": timestamp,  # Use the same timestamp for created_at
            #         "tier": tier,
            #         "user_message": user_message
            #     }
            #     alert_queue.put(alert)
            #     logger.debug(f"Added alert to queue: {alert_type} at {timestamp} (created_at: {timestamp})")

            # logger.debug("Sample alerts created")


def _apply_pause_button_state() -> None:
    """Apply ALERTS_PAUSED to the live NiceGUI pause button (must have a live client)."""
    btn = activity_feed_state.pause_btn
    if not _element_alive(btn):
        return
    try:
        from modules import web_engine

        paused = bool(getattr(web_engine, "ALERTS_PAUSED", False))
        if paused:
            btn.props(f"{_DOCK_BTN_PROPS} id=pause-alerts-btn icon=play_arrow")
            btn.text = "RESUME ALERTS"
            btn.classes(remove="alerts-playing")
            btn.classes(add="paused")
            btn.style("box-shadow: none")
        else:
            btn.props(f"{_DOCK_BTN_PROPS} id=pause-alerts-btn icon=pause")
            btn.text = "PAUSE ALERTS"
            btn.classes(remove="paused")
            btn.classes(add="alerts-playing")
    except Exception as e:
        logger.error("Error updating pause button state: %s", e, exc_info=True)


def sync_pause_button_state():
    """Sync the pause button with ALERTS_PAUSED. Safe from any thread."""
    _run_on_ui_loop(_apply_pause_button_state)


def _apply_mute_button_state() -> None:
    """Apply ALERTS_MUTED to the live NiceGUI mute button (must have a live client)."""
    btn = activity_feed_state.mute_btn
    if not _element_alive(btn):
        logger.debug("Mute button not initialized or stale, skipping sync")
        return
    try:
        from modules import web_engine

        muted = bool(getattr(web_engine, "ALERTS_MUTED", False))
        activity_feed_state.alerts_muted = muted
        if muted:
            btn.classes(add="muted")
        else:
            btn.classes(remove="muted")
    except Exception as e:
        logger.debug("Error syncing mute button state: %s", e)


def sync_mute_button_state():
    """Sync the mute button with ALERTS_MUTED. Safe from any thread."""
    _run_on_ui_loop(_apply_mute_button_state)


def add_alert_direct(
    alert_type: str,
    message: str,
    badge_type: str = "follow",
    timestamp: str = "now",
    tier: Optional[int] = None,
    user_message: Optional[str] = None,
    alert_id: Optional[str] = None,
    **kwargs,
) -> None:
    """
    Public function for external modules to add alerts directly to the UI using the event system.

    This is the new recommended way for modules (e.g. twitch) to add alerts.
    Replaces the old queue-based system with immediate event processing.

    Args:
        alert_type (str): The type of alert (e.g., "Follow", "Channel Points", "Bits")
        message (str): The alert message text
        badge_type (str): The type of badge to show (affects styling)
        timestamp (str or float): When the alert occurred, can be "now" or a Unix timestamp
        tier (int, optional): The tier level for subscriptions (1, 2, or 3)
        user_message (str, optional): User's message for resubs, bits, points, or donations
        alert_id (str, optional): The alert ID to look up stored alert data
        **kwargs: Additional alert data (username, stored_alert_data, etc.)
    """
    try:
        # Create alert data with all provided information
        alert_data = {
            "type": alert_type,
            "message": message,
            "badge_type": badge_type,
            "timestamp": timestamp if timestamp != "now" else time.time(),
            "created_at": time.time(),
            "tier": tier,
            "user_message": user_message,
            "alert_id": alert_id,
        }

        # Add any additional data from kwargs
        alert_data.update(kwargs)

        # Process immediately using the event system
        alert_event_handler.process_alert_immediately(alert_data)

        logger.debug(f"Alert added via event system: {alert_type} from external module")

    except Exception as e:
        logger.error(f"Error adding alert via event system: {e}", exc_info=True)


def force_ui_refresh():
    """Force a UI refresh and timestamp update - useful after bulk operations"""
    try:
        alert_event_handler.force_timestamp_update()
        ui.update()
        logger.debug("Forced UI refresh completed")
    except Exception as e:
        logger.error(f"Error during forced UI refresh: {e}", exc_info=True)


def refresh_condensed_view():
    """Force a refresh of the condensed view - useful when configuration changes"""
    try:
        if (
            activity_feed_state.current_tab == "current"
            and activity_feed_state.condense_list
        ):
            update_condensed_view()
            logger.debug("Forced condensed view refresh completed")
    except Exception as e:
        logger.error(f"Error during forced condensed view refresh: {e}", exc_info=True)


def convert_stored_alert_to_feed_format(stored_alert_data):
    """Convert stored alert data to the format expected by the activity feed

    Args:
        stored_alert_data (dict): Stored alert data from AlertStateManager

    Returns:
        dict: Alert data in activity feed format
    """
    try:
        # Debug logging to see what data we actually have
        logger.debug(
            f"Converting stored alert to feed format. Available keys: {list(stored_alert_data.keys())}"
        )

        # Map alert_type to display type
        alert_type_mapping = {
            "bit": "Bits",
            "sub": "Sub",
            "resub": "Resub",
            "giftsub": "Giftsub",
            "follow": "Follow",
            "point": "Points",
            "raid": "Raid",
            "donation": "Donation",
            "hype_train_start": "Hype Train",
            "hype_train_progress": "Hype Train",
            "hype_train_end": "Hype Train",
        }

        original_type = stored_alert_data.get("alert_type", "unknown")
        display_type = alert_type_mapping.get(original_type, original_type.title())
        source = (stored_alert_data.get("source") or "").lower()
        youtube_badge = None

        # Restore YouTube-native labels when stored with source=youtube
        if source == "youtube":
            if original_type == "sub":
                display_type = "Membership"
                youtube_badge = "membership"
            elif original_type == "resub":
                display_type = "Member Milestone"
                youtube_badge = "member_milestone"
            elif original_type == "giftsub":
                display_type = "Gift Membership"
                youtube_badge = "gift_membership"
            elif original_type == "donation":
                # Prefer supersticker if flagged; otherwise Super Chat
                if stored_alert_data.get("is_supersticker") or stored_alert_data.get(
                    "supersticker"
                ):
                    display_type = "Super Sticker"
                    youtube_badge = "supersticker"
                else:
                    display_type = "Super Chat"
                    youtube_badge = "superchat"

        # Get username with fallback and debugging
        username = stored_alert_data.get("username", None)
        if username is None or username == "":
            log_fn = (
                logger.debug
                if str(original_type).startswith("hype_train")
                else logger.warning
            )
            log_fn(
                f"Alert {stored_alert_data.get('alert_id', 'unknown')} missing username. Available data: {stored_alert_data}"
            )
            username = "Unknown User"

        # Generate appropriate message based on alert type
        message = ""

        if original_type == "bit":
            amt_cheered = stored_alert_data.get("amt_cheered", 0)
            message = f"{username} cheered {amt_cheered} bits!"
        elif original_type == "sub":
            tier = stored_alert_data.get("tier", 1)
            resub_month = stored_alert_data.get("resub_month", 0)
            if source == "youtube":
                level = stored_alert_data.get("member_level") or ""
                message = (
                    f"{username} became a channel member"
                    + (f" ({level})!" if level else "!")
                )
            elif resub_month > 1:
                display_type = "Resub"
                message = f"{username} has resubscribed for {resub_month} months!"
            else:
                message = f"{username} subscribed (Tier {tier})!"
        elif original_type == "resub":
            tier = stored_alert_data.get("tier", 1)
            # Check multiple possible field names for the month count
            resub_month = (
                stored_alert_data.get("resub_month", 0)
                or stored_alert_data.get("months", 0)
                or stored_alert_data.get("total_months", 0)
                or 1
            )
            if source == "youtube":
                level = stored_alert_data.get("member_level") or ""
                message = (
                    f"{username} reached a member milestone"
                    f" ({resub_month} months"
                    + (f", {level}" if level else "")
                    + ")!"
                )
            else:
                message = f"{username} has resubscribed for {resub_month} months!"
        elif original_type == "giftsub":
            gift_qty = stored_alert_data.get("gift_qty", 1)
            try:
                gift_qty = int(gift_qty) if gift_qty is not None else 1
            except (TypeError, ValueError):
                gift_qty = 1
            tier = stored_alert_data.get("tier", 1)
            recipient = str(stored_alert_data.get("recipient") or "").strip()
            if source == "youtube":
                message = f"{username} gifted {gift_qty} memberships!"
            elif gift_qty == 1 and recipient:
                message = f"{username} gifted a Tier {tier} sub to {recipient}!"
            elif gift_qty == 1:
                message = f"{username} gifted a Tier {tier} sub!"
            else:
                message = f"{username} gifted {gift_qty} Tier {tier} subs!"
        elif original_type == "follow":
            message = f"{username} just followed!"
        elif original_type == "point":
            alert_name = stored_alert_data.get("alert_name", "Unknown Reward")
            message = f"{username} redeemed '{alert_name}'!"
        elif original_type == "raid":
            raider_count = stored_alert_data.get("raider_count", 0)
            message = format_raid_activity_message(
                username,
                raider_count,
                stored_alert_data.get("game_name"),
            )
        elif original_type == "donation":
            donation_amount = stored_alert_data.get("donation_amount", 0)
            if source == "youtube":
                display_amount = stored_alert_data.get("display_amount")
                currency = stored_alert_data.get("currency") or ""
                if display_amount:
                    amount_str = str(display_amount)
                else:
                    amount_str = f"{donation_amount} {currency}".strip()
                label = (
                    "Super Sticker"
                    if youtube_badge == "supersticker"
                    else "Super Chat"
                )
                message = f"{username} sent a {label} ({amount_str})!"
            else:
                message = f"{username} donated ${donation_amount}!"
        elif original_type.startswith("hype_train"):
            hype_train_level = stored_alert_data.get("hype_train_level", 1)
            # For hype train, username might not be relevant
            if username == "Unknown User":
                message = f"Hype Train Level {hype_train_level}!"
            else:
                message = f"Hype Train Level {hype_train_level}!"
        else:
            message = f"{username} triggered {display_type}!"

        # Create result with all available data, preserving the original stored data
        result = {
            "type": display_type,
            "message": message,
            "badge_type": youtube_badge or original_type,
            "timestamp": alertutils.safe_alert_timestamp(
                stored_alert_data.get("timestamp"), default=time.time()
            ),
            "created_at": alertutils.safe_alert_timestamp(
                stored_alert_data.get("timestamp"), default=time.time()
            ),
            "tier": stored_alert_data.get("tier"),
            "user_message": stored_alert_data.get("message"),
            "is_restored": True,  # Mark as restored alert
            "alert_id": stored_alert_data.get(
                "alert_id"
            ),  # Include original alert ID for debugging
            "username": username,  # Include the resolved username
            "source": source or stored_alert_data.get("source"),
            # Preserve the full original stored alert data for replay functionality
            "stored_alert_data": stored_alert_data,
        }
        if original_type == "giftsub":
            gift_qty = stored_alert_data.get("gift_qty")
            if gift_qty is not None:
                result["gift_qty"] = gift_qty
            recipient = str(stored_alert_data.get("recipient") or "").strip()
            if recipient:
                result["recipient"] = recipient

        logger.debug(
            f"Converted alert {stored_alert_data.get('alert_id', 'unknown')} - type: {display_type}, username: '{username}', message: '{message}'"
        )

        return result

    except Exception as e:
        logger.error(
            f"Error converting stored alert to feed format: {str(e)}", exc_info=True
        )
        logger.error(f"Stored alert data that failed conversion: {stored_alert_data}")
        return None


def load_restored_alerts(page=1):
    """Load restored alerts from storage with pagination

    Args:
        page (int): Page number to load (1-based)
    """
    try:
        # Get the app settings to determine pagination limit and max pages
        from modules import dataobjects

        app_settings = dataobjects.state_manager.get_app_settings()
        limit = app_settings.activity_feed_limit
        max_pages = app_settings.activity_feed_max_pages

        # Calculate maximum total alerts to fetch (max_pages * items_per_page)
        max_total_alerts = max_pages * limit

        # Update pagination limit if it changed
        activity_feed_state.pagination_limit = limit

        # Get paginated stored alerts using the limited method to reduce bandwidth
        from modules import alertutils

        alertutils.alert_state_manager.initialize()
        result = alertutils.alert_state_manager.get_limited_stored_alerts_paginated(
            page=page, limit=limit, max_total_alerts=max_total_alerts
        )

        # Update pagination state
        activity_feed_state.current_page = result["page"]
        activity_feed_state.total_pages = min(
            result["total_pages"], max_pages
        )  # Enforce max pages limit

        # Convert stored alerts to feed format and add them
        restored_alerts = []
        for stored_alert in result["alerts"]:
            feed_alert = convert_stored_alert_to_feed_format(stored_alert)
            if feed_alert:
                restored_alerts.append(feed_alert)

        logger.debug(
            f"Loaded {len(restored_alerts)} restored alerts for page {page} (limited to {max_total_alerts} total alerts)"
        )
        return restored_alerts, result

    except Exception as e:
        logger.error(f"Error loading restored alerts: {str(e)}", exc_info=True)
        return [], {
            "total_count": 0,
            "total_pages": 1,
            "has_next": False,
            "has_prev": False,
        }


def load_restored_alerts_for_time_window(cutoff_time: float):
    """Load stored alerts newer than cutoff_time using one AlertStorage fetch."""
    try:
        from modules import alertutils, dataobjects

        app_settings = dataobjects.state_manager.get_app_settings()
        max_total_alerts = (
            app_settings.activity_feed_max_pages * app_settings.activity_feed_limit
        )

        alertutils.alert_state_manager.initialize()
        limited = alertutils.alert_state_manager.get_limited_stored_alerts_from_firebase(
            max_total_alerts
        )

        restored_alerts = []
        for alert_id, alert_data in limited.items():
            if _alert_timestamp_for_cutoff(alert_data) < cutoff_time:
                continue
            row = dict(alert_data)
            row["alert_id"] = alert_id
            feed_alert = convert_stored_alert_to_feed_format(row)
            if feed_alert:
                restored_alerts.append(feed_alert)

        restored_alerts.sort(key=_alert_timestamp_for_cutoff, reverse=True)
        logger.debug(
            "Loaded %d restored alerts for condensed view (cutoff=%s)",
            len(restored_alerts),
            cutoff_time,
        )
        return restored_alerts, len(restored_alerts)
    except Exception as e:
        logger.error(
            f"Error loading restored alerts for time window: {str(e)}", exc_info=True
        )
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
    for live_alert in activity_feed_state.live_alerts:
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
    alerts_to_process.sort(
        key=_alert_timestamp_for_cutoff,
        reverse=True,
    )

    logger.debug(
        "Collected %d alerts for condensed view (stored=%d, live_in_window=%d)",
        len(alerts_to_process),
        historical_count,
        live_in_window,
    )
    return alerts_to_process, historical_count, live_in_window


def clear_restored_alerts():
    """Clear all restored alerts from the previous alerts container"""
    try:
        container = activity_feed_state.previous_alerts_container
        if not _element_alive(container):
            return

        container.clear()

        # Clear all historical alerts (alert_elements is only for historical alerts now)
        activity_feed_state.alert_elements = []

        logger.debug("Cleared restored alerts from activity feed")

    except Exception as e:
        logger.error(f"Error clearing restored alerts: {str(e)}", exc_info=True)


def add_restored_alert_to_feed(alert_data):
    """Add a restored alert to the activity feed (Previous Alerts tab only)

    Args:
        alert_data (dict): Alert data in feed format with is_restored=True
    """
    try:
        if not activity_feed_state.activity_feed_container:
            return

        # Only add if we're on the previous alerts tab
        if activity_feed_state.current_tab != "previous":
            return

        # Ensure the alert data has the restored flag
        alert_data["is_restored"] = True
        alert_data["is_recent"] = False  # Restored alerts are never recent

        # Add to the main alert elements list (for historical alerts only)
        activity_feed_state.alert_elements.append(alert_data)

        # Create the alert element using the unified approach
        create_alert_element(alert_data)

        # Apply visibility based on filter state
        alert_type = alert_data["type"]
        filter_key = None
        if alert_type == "Points":
            filter_key = "points"
        elif alert_type == "Follow":
            filter_key = "follows"
        elif alert_type == "Bits":
            filter_key = "bits"
        elif alert_type == "Sub":
            filter_key = "subs"
        elif alert_type == "Resub":
            filter_key = "resubs"
        elif alert_type == "Giftsub":
            filter_key = "giftsubs"
        elif alert_type == "Donation":
            filter_key = "donations"
        elif alert_type == "Membership":
            filter_key = "subs"
        elif alert_type == "Member Milestone":
            filter_key = "resubs"
        elif alert_type == "Gift Membership":
            filter_key = "giftsubs"
        elif alert_type in ("Super Chat", "Super Sticker"):
            filter_key = "donations"
        elif alert_type == "Raid":
            filter_key = "raids"
        elif alert_type == "Streak":
            filter_key = "streaks"
        elif alert_type == "Hype Train":
            filter_key = "hype_train"

        if filter_key and not (
            activity_feed_state.filter_state.get("all", True)
            or activity_feed_state.filter_state.get(filter_key, True)
        ):
            if alert_data.get("element"):
                alert_data["element"].classes(add="hidden")

    except Exception as e:
        logger.error(f"Error adding restored alert to feed: {str(e)}", exc_info=True)


def refresh_restored_alerts():
    """Refresh the restored alerts display"""
    try:
        if activity_feed_state.current_tab != "previous":
            return

        # Clear existing restored alerts
        clear_restored_alerts()

        # Load and display restored alerts for current page
        restored_alerts, pagination_info = load_restored_alerts(
            activity_feed_state.current_page
        )

        # Add restored alerts to feed
        for alert_data in restored_alerts:
            add_restored_alert_to_feed(alert_data)

        # Apply filters to the newly loaded alerts
        update_alert_visibility()

        # Update pagination UI if it exists
        update_pagination_ui(pagination_info)

        activity_feed_state.restored_alerts_loaded = True
        logger.debug(
            f"Refreshed restored alerts - page {activity_feed_state.current_page} of {activity_feed_state.total_pages}"
        )

    except Exception as e:
        logger.error(f"Error refreshing restored alerts: {str(e)}", exc_info=True)


def update_pagination_ui(pagination_info):
    """Update the pagination controls

    Args:
        pagination_info (dict): Pagination information from get_stored_alerts_paginated
    """
    try:
        container = activity_feed_state.pagination_container
        if not _element_alive(container):
            return

        container.clear()

        with container:
            if pagination_info["total_count"] > 0:
                with ui.row().classes("items-center gap-2 justify-center w-full"):
                    # Previous button
                    prev_btn = ui.button(
                        icon="chevron_left",
                        on_click=lambda: go_to_page(
                            activity_feed_state.current_page - 1
                        ),
                    ).classes("pagination-btn")
                    prev_btn.set_enabled(pagination_info["has_prev"])

                    # Page info
                    ui.label(
                        f"Page {pagination_info['page']} of {pagination_info['total_pages']}"
                    ).classes("text-sm secondary-text px-2")
                    ui.label(
                        f"({pagination_info['total_count']} total alerts)"
                    ).classes("text-xs secondary-text px-2")

                    # Next button
                    next_btn = ui.button(
                        icon="chevron_right",
                        on_click=lambda: go_to_page(
                            activity_feed_state.current_page + 1
                        ),
                    ).classes("pagination-btn")
                    next_btn.set_enabled(pagination_info["has_next"])
            else:
                ui.label("No stored alerts found").classes(
                    "text-sm secondary-text text-center w-full"
                )

    except Exception as e:
        logger.error(f"Error updating pagination UI: {str(e)}", exc_info=True)


def go_to_page(page):
    """Navigate to a specific page of restored alerts

    Args:
        page (int): Page number to navigate to
    """
    try:
        # Get max pages setting to enforce limit
        from modules import dataobjects

        app_settings = dataobjects.state_manager.get_app_settings()
        max_pages = app_settings.activity_feed_max_pages

        # Enforce both total pages and max pages limits
        max_allowed_page = min(activity_feed_state.total_pages, max_pages)

        if page < 1 or page > max_allowed_page:
            return

        activity_feed_state.current_page = page
        refresh_restored_alerts()

    except Exception as e:
        logger.error(f"Error navigating to page {page}: {str(e)}", exc_info=True)


def _load_condensed_historical_hours() -> int:
    """Load condense_historical_hours from template config (may perform I/O)."""
    condense_historical_hours = 12
    try:
        from modules.template_config_parser import TemplateConfigParser

        config_parser = TemplateConfigParser()
        config_data = config_parser.load_config("activity_feed")

        if config_data and "elements" in config_data:
            for element in config_data["elements"]:
                if element.get("type") == "section":
                    continue
                if element.get("id") == "condense_historical_hours":
                    element_value = element.get("value")
                    condense_historical_hours = parse_condensed_historical_hours(
                        element_value
                    )
                    break
            logger.debug(
                "Loaded condensed view config: hours=%s", condense_historical_hours
            )
        else:
            logger.debug("No activity_feed config elements found, using defaults")
    except Exception as exc:
        logger.debug("Could not load template config for condensed view: %s", exc)
    return condense_historical_hours


def _group_alerts_for_condensed(
    alerts_to_process: List[Dict[str, Any]],
    filter_state: Optional[Dict[str, Any]] = None,
):
    """Group alerts by user and type for the condensed view (CPU-only)."""
    import re

    user_alerts: Dict[str, Dict[str, Any]] = {}
    excluded_count = 0
    unknown_username_count = 0
    if filter_state is None:
        filter_state = activity_feed_state.filter_state
    type_to_filter = activity_feed_state.alert_type_to_filter

    for alert_data in alerts_to_process:
        alert_type = alert_data.get("type", "").lower()
        badge_type = alert_data.get("badge_type", "").lower()

        # Exclude hype train only (channel points are included and grouped by reward)
        if alert_type == "hype train" or badge_type == "hype_train":
            excluded_count += 1
            continue

        # Apply the same filter rules as the regular card view
        display_type = alert_data.get("type", "")
        filter_key = type_to_filter.get(display_type)
        if not (
            filter_state.get("all", True)
            or (filter_key and filter_state.get(filter_key, True))
        ):
            excluded_count += 1
            continue

        username = alert_data.get("username")
        if not username:
            username = extract_username_from_message(alert_data.get("message", ""))

        if not username or username == "Unknown":
            unknown_username_count += 1
            continue

        if username not in user_alerts:
            user_alerts[username] = {}

        grouping_key = alert_type
        reward_name = None

        if alert_type in ["sub", "resub", "giftsub"]:
            tier = alert_data.get("tier", 1)
            grouping_key = f"{alert_type}_tier{tier}"

            if alert_type in ["resub", "giftsub"]:
                duration_months = 1
                stored_data = alert_data.get("stored_alert_data", {})

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
            elif alert_type == "membership":
                grouping_key = "membership"
            elif alert_type == "member milestone":
                grouping_key = "member_milestone"
            elif alert_type == "gift membership":
                grouping_key = "gift_membership"
            elif alert_type == "super chat":
                grouping_key = "superchat"
            elif alert_type == "super sticker":
                grouping_key = "supersticker"
        elif alert_type in ("point", "points") or badge_type in ("point", "points"):
            stored_data = alert_data.get("stored_alert_data", {}) or {}
            reward_name = stored_data.get("alert_name")
            if not reward_name:
                message = alert_data.get("message", "")
                reward_match = re.search(
                    r"redeemed\s+'(.+?)'!", message, re.IGNORECASE
                )
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
            }
            if reward_name is not None:
                user_alerts[username][grouping_key]["reward_name"] = reward_name

        user_alerts[username][grouping_key]["count"] += 1

        message = alert_data.get("message", "")
        stored_data = alert_data.get("stored_alert_data", {})

        if alert_type in ("bit", "bits"):
            amt_cheered = stored_data.get("amt_cheered") if stored_data else None
            if amt_cheered:
                user_alerts[username][grouping_key]["total_amount"] += int(amt_cheered)
            else:
                bit_match = re.search(r"(\d+)\s*bits?", message, re.IGNORECASE)
                if bit_match:
                    user_alerts[username][grouping_key]["total_amount"] += int(
                        bit_match.group(1)
                    )
        elif alert_type == "donation":
            donation_amount = (
                stored_data.get("donation_amount") if stored_data else None
            )
            if donation_amount:
                user_alerts[username][grouping_key]["total_amount"] += float(
                    donation_amount
                )
            else:
                donation_match = re.search(
                    r"donated\s+\$?(\d+(?:\.\d{2})?)", message, re.IGNORECASE
                )
                if donation_match:
                    user_alerts[username][grouping_key]["total_amount"] += float(
                        donation_match.group(1)
                    )
        elif alert_type == "giftsub":
            gift_qty = stored_data.get("gift_qty") if stored_data else None
            if gift_qty:
                user_alerts[username][grouping_key]["total_amount"] += int(gift_qty)
            else:
                gift_match = re.search(r"gifted\s+(\d+)", message, re.IGNORECASE)
                if gift_match:
                    user_alerts[username][grouping_key]["total_amount"] += int(
                        gift_match.group(1)
                    )
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
            if months > user_alerts[username][grouping_key]["months"]:
                user_alerts[username][grouping_key]["months"] = months
        elif alert_type == "raid":
            raider_count = stored_data.get("raider_count") if stored_data else None
            if raider_count:
                viewers = int(raider_count)
            else:
                raid_match = re.search(r"(\d+)\s*viewers?", message, re.IGNORECASE)
                viewers = int(raid_match.group(1)) if raid_match else 0
            raid_game = alert_data.get("game_name")
            if not raid_game and stored_data:
                raid_game = stored_data.get("game_name")
            if viewers > user_alerts[username][grouping_key]["total_amount"]:
                user_alerts[username][grouping_key]["total_amount"] = viewers
                if raid_game:
                    user_alerts[username][grouping_key]["game_name"] = raid_game
        elif alert_type == "streak":
            streak_count_val = alert_data.get("streak_count")
            if streak_count_val is None and stored_data:
                streak_count_val = stored_data.get("streak_count")
            if streak_count_val is not None:
                sc = int(streak_count_val)
            else:
                streak_match = re.search(
                    r"(\d+)\s*consecutive streams", message, re.IGNORECASE
                )
                sc = int(streak_match.group(1)) if streak_match else 0
            if sc > user_alerts[username][grouping_key]["total_amount"]:
                user_alerts[username][grouping_key]["total_amount"] = sc

    logger.debug(
        "Condensed grouping: processed=%d excluded=%d unknown_user=%d users=%d",
        len(alerts_to_process),
        excluded_count,
        unknown_username_count,
        len(user_alerts),
    )
    return user_alerts, excluded_count, unknown_username_count


def serialize_condensed_groups(
    user_alerts: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Turn grouped alert dicts into a slim [{username, lines}] payload."""
    groups: List[Dict[str, Any]] = []
    for username, alert_types in user_alerts.items():
        if not alert_types:
            continue
        lines: List[str] = []
        for grouping_key, data in alert_types.items():
            text = create_aggregated_alert_text(grouping_key, data)
            if text:
                lines.append(text)
        if lines:
            groups.append({"username": str(username), "lines": lines})
    return groups


def _build_condensed_html(build_data: Dict[str, Any]) -> str:
    """Build one escaped HTML block for the condensed surface."""
    hours = build_data.get("condense_historical_hours", 12)
    groups = build_data.get("groups")
    if groups is None:
        groups = serialize_condensed_groups(build_data.get("user_alerts") or {})
    alert_count = build_data.get("alert_count")
    if alert_count is None:
        alert_count = len(build_data.get("alerts_to_process") or [])
    historical_count = int(build_data.get("historical_count") or 0)
    live_in_window = int(build_data.get("live_in_window") or 0)

    if not groups:
        msg = html.escape(f"No alerts to condense in the last {hours} hours")
        return f'<div class="no-alerts text-sm muted-text italic">{msg}</div>'

    parts: List[str] = []
    if historical_count > 0 or live_in_window > 0:
        indicator = html.escape(
            f"Showing {alert_count} alerts from past {hours} hours"
        )
        parts.append(
            f'<div class="text-xs muted-text mb-2 italic">{indicator}</div>'
        )
    for group in groups:
        username = html.escape(str(group.get("username") or ""))
        lines_html = []
        for line in group.get("lines") or []:
            lines_html.append(
                f'<div class="alert-item secondary-text text-sm mb-1">'
                f"{html.escape(str(line))}</div>"
            )
        parts.append(
            f'<div class="user-group mb-4">'
            f'<div class="username font-semibold mb-1">{username}:</div>'
            f'<div class="ml-4">{"".join(lines_html)}</div>'
            f"</div>"
        )
    return "".join(parts)


def build_condensed_overlay_payload(
    hours: Any = 12,
    filter_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Collect, group, and serialize condensed view data for the overlay."""
    parsed_hours = parse_condensed_historical_hours(hours)
    cutoff_time = time.time() - (parsed_hours * 3600)
    try:
        (
            alerts_to_process,
            historical_count,
            live_in_window,
        ) = collect_alerts_for_condensed_view(cutoff_time)
    except Exception as exc:
        logger.error(
            "Error loading alerts for condensed overlay payload: %s",
            exc,
            exc_info=True,
        )
        alerts_to_process = []
        historical_count = 0
        live_in_window = 0

    user_alerts, excluded_count, unknown_username_count = _group_alerts_for_condensed(
        alerts_to_process, filter_state=filter_state
    )
    groups = serialize_condensed_groups(user_alerts)
    return {
        "success": True,
        "groups": groups,
        "historical_count": historical_count,
        "live_in_window": live_in_window,
        "alert_count": len(alerts_to_process),
        "hours": parsed_hours,
        "user_count": len(groups),
        "excluded_count": excluded_count,
        "unknown_username_count": unknown_username_count,
    }


def _collect_condensed_build_data() -> Dict[str, Any]:
    """Collect all data needed to render the condensed view (blocking I/O)."""
    started = time.monotonic()
    condense_historical_hours = _load_condensed_historical_hours()
    cutoff_time = time.time() - (condense_historical_hours * 3600)
    historical_count = 0
    live_in_window = 0
    alerts_to_process: List[Dict[str, Any]] = []

    logger.debug(
        "Loading alerts for condensed view from past %s hours...",
        condense_historical_hours,
    )

    try:
        (
            alerts_to_process,
            historical_count,
            live_in_window,
        ) = collect_alerts_for_condensed_view(cutoff_time)
        logger.debug(
            "Condensed view loaded %d stored and %d live alerts (past %d hours)",
            historical_count,
            live_in_window,
            condense_historical_hours,
        )
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
        alerts_to_process
    )
    groups = serialize_condensed_groups(user_alerts)

    return {
        "condense_historical_hours": condense_historical_hours,
        "alert_count": len(alerts_to_process),
        "historical_count": historical_count,
        "live_in_window": live_in_window,
        "user_alerts": user_alerts,
        "groups": groups,
        "excluded_count": excluded_count,
        "unknown_username_count": unknown_username_count,
    }


def _render_condensed_view_ui(build_data: Dict[str, Any]) -> bool:
    """Render the condensed view from pre-fetched data (UI loop only).

    Builds into a staging child first, then removes previous siblings so the
    visible condensed surface never flashes empty mid-rebuild.
    """
    container = activity_feed_state.condensed_container
    if not _element_alive(container):
        return False

    groups = build_data.get("groups")
    if groups is None:
        groups = serialize_condensed_groups(build_data.get("user_alerts") or {})
    alert_count = int(build_data.get("alert_count") or 0)

    previous_children = _slot_child_count(container)
    logger.warning(
        "activity_feed: condensed render start (prev_children=%d users=%d alerts=%d)",
        previous_children,
        len(groups),
        alert_count,
    )

    staging = None
    try:
        with container:
            staging = ui.element("div").classes("condensed-staging w-full")

        with staging:
            ui.html(_build_condensed_html(build_data), sanitize=False).classes(
                "w-full"
            )

        # Drop previous siblings only after staging content exists.
        slot = getattr(container, "default_slot", None)
        if slot is not None:
            for child in list(slot.children):
                if child is staging:
                    continue
                try:
                    child.delete()
                except Exception:
                    pass

        child_count = _slot_child_count(container)
        logger.warning(
            "activity_feed: condensed render end (children=%d)",
            child_count,
        )
        return child_count > 0
    except Exception as exc:
        logger.error(
            "activity_feed: condensed staging render failed: %s", exc, exc_info=True
        )
        if staging is not None:
            try:
                staging.delete()
            except Exception:
                pass
        return False


def _apply_condensed_visibility(show_condensed: bool) -> None:
    """Show condensed view and hide regular feed, or the reverse."""
    if show_condensed:
        if _element_alive(activity_feed_state.current_alerts_container):
            activity_feed_state.current_alerts_container.classes(add="hidden")
        if _element_alive(activity_feed_state.condensed_container):
            activity_feed_state.condensed_container.classes(remove="hidden")
    else:
        if _element_alive(activity_feed_state.condensed_container):
            activity_feed_state.condensed_container.classes(add="hidden")
        if _element_alive(activity_feed_state.current_alerts_container):
            activity_feed_state.current_alerts_container.classes(remove="hidden")


def _schedule_condensed_rebuild(reason: str) -> None:
    """Start (or coalesce) an async condensed-view rebuild."""
    global _condensed_rebuild_running, _condensed_rebuild_rerun

    if _condensed_rebuild_running:
        _condensed_rebuild_rerun = True
        logger.debug(
            "activity_feed: condensed rebuild coalesced (%s)", reason
        )
        return

    logger.warning("activity_feed: condensed rebuild scheduled (%s)", reason)
    background_tasks.create(
        _update_condensed_view_async(reason),
        name="activity_feed_condensed_rebuild",
    )


async def _update_condensed_view_async(reason: str) -> None:
    """Fetch condensed data off the UI loop, then render on the loop."""
    global _condensed_rebuild_running, _condensed_rebuild_rerun
    global _condensed_integrity_failures, _dom_desync_failures

    _condensed_rebuild_running = True
    try:
        while True:
            _condensed_rebuild_rerun = False

            if not (
                activity_feed_state.current_tab == "current"
                and activity_feed_state.condense_list
            ):
                _ensure_regular_feed_populated(f"condensed_aborted:{reason}")
                _apply_condensed_visibility(False)
                return

            build_data = await run.io_bound(_collect_condensed_build_data)

            if not (
                activity_feed_state.current_tab == "current"
                and activity_feed_state.condense_list
            ):
                _ensure_regular_feed_populated(f"condensed_aborted_after_io:{reason}")
                _apply_condensed_visibility(False)
                return

            try:
                built = _render_condensed_view_ui(build_data)
            except Exception as exc:
                logger.error(
                    "activity_feed: condensed UI render failed (%s): %s",
                    reason,
                    exc,
                    exc_info=True,
                )
                built = False

            if built:
                _condensed_integrity_failures = 0
                _dom_desync_failures = 0
                _apply_condensed_visibility(True)
                schedule_feed_integrity_check(f"condensed_show:{reason}")
            else:
                _fallback_to_regular_feed(f"condensed_build_failed:{reason}")

            if not _condensed_rebuild_rerun:
                break
    except Exception as exc:
        logger.error(
            "activity_feed: async condensed rebuild failed (%s): %s",
            reason,
            exc,
            exc_info=True,
        )
        _fallback_to_regular_feed(f"condensed_async_failed:{reason}")
    finally:
        _condensed_rebuild_running = False
        if _condensed_rebuild_rerun:
            _schedule_condensed_rebuild("coalesced_rerun")


def update_condensed_view() -> bool:
    """Update the condensed view based on the toggle state.

    Returns:
        True when the toggle/tab UI was updated successfully. Scheduling a
        condensed rebuild is not treated as a confirmed healthy surface —
        callers must use integrity checks for that.
    """
    try:
        logger.debug(
            f"update_condensed_view called - current tab: {activity_feed_state.current_tab}, condense_list: {activity_feed_state.condense_list}"
        )

        if not activity_feed_state.condense_toggle:
            logger.debug("No condense toggle found, returning early")
            return True

        # Show/hide condense toggle based on current tab
        if activity_feed_state.current_tab == "current":
            if _element_alive(activity_feed_state.condense_toggle):
                activity_feed_state.condense_toggle.classes(remove="hidden")
            logger.debug("Condense List toggle shown (Current Alerts tab)")
        else:
            if _element_alive(activity_feed_state.condense_toggle):
                activity_feed_state.condense_toggle.classes(add="hidden")
            logger.debug(
                "Condense List toggle hidden (tab=%s; only visible on Current Alerts)",
                activity_feed_state.current_tab,
            )

        # Only create condensed view if we're on current tab and toggle is on
        if (
            activity_feed_state.current_tab == "current"
            and activity_feed_state.condense_list
        ):
            # Data collection can hit the database/network, so it must not run on
            # the UI event loop (a long fetch stalls the websocket and the client
            # eventually gets deleted). Schedule an async rebuild instead; the
            # regular feed stays visible until the condensed view is ready.
            logger.debug("Scheduling condensed view rebuild...")
            _schedule_condensed_rebuild("update_condensed_view")
            # Scheduling is not confirmation that the surface is healthy.
            return True

        logger.debug("Showing regular alerts view...")
        _ensure_regular_feed_populated("update_condensed_view_regular")
        _apply_condensed_visibility(False)
        schedule_feed_integrity_check("update_condensed_view_regular")
        return True

    except Exception as e:
        logger.error(f"Error updating condensed view: {str(e)}", exc_info=True)
        _fallback_to_regular_feed("update_condensed_view_exception")
        return False


def create_condensed_view() -> bool:
    """Create the condensed view of alerts grouped by user (sync fallback).

    Returns:
        True if the condensed container was rebuilt successfully, False otherwise.
    """
    try:
        build_data = _collect_condensed_build_data()
        if not _render_condensed_view_ui(build_data):
            return False
        _apply_condensed_visibility(True)
        return True
    except Exception as e:
        logger.error(f"Error creating condensed view: {str(e)}", exc_info=True)
        return False


def create_aggregated_alert_text(grouping_key, data):
    """Create aggregated text for multiple alerts of the same type"""
    try:
        count = data["count"]
        total_amount = data["total_amount"]
        tier = data["tier"]
        months = data["months"]
        original_type = data.get("original_type", grouping_key.split("_")[0])

        # Handle tier and duration-specific keys for subs, resubs, and giftsubs
        if grouping_key.startswith("sub_tier"):
            tier_num = tier
            if count > 1:
                return f"Subscribed {count} times (Tier {tier_num})!"
            else:
                return f"Subscribed (Tier {tier_num})!"
        elif grouping_key.startswith("resub_tier") and "_1month" in grouping_key:
            tier_num = tier
            if count > 1:
                return f"Resubscribed {count} times for 1 month (Tier {tier_num})!"
            else:
                return f"Resubscribed for 1 month (Tier {tier_num})!"
        elif grouping_key.startswith("resub_tier") and "_multi_month" in grouping_key:
            tier_num = tier
            if count > 1:
                return (
                    f"Resubscribed {count} times for {months} months (Tier {tier_num})!"
                )
            else:
                return f"Resubscribed for {months} months (Tier {tier_num})!"
        elif grouping_key.startswith("giftsub_tier") and "_1month" in grouping_key:
            tier_num = tier
            try:
                formatted_amount = f"{int(total_amount):,}"
            except (ValueError, TypeError):
                formatted_amount = str(total_amount)
            return f"Gifted {formatted_amount} Tier {tier_num} subs (1 month)!"
        elif grouping_key.startswith("giftsub_tier") and "_multi_month" in grouping_key:
            tier_num = tier
            try:
                formatted_amount = f"{int(total_amount):,}"
            except (ValueError, TypeError):
                formatted_amount = str(total_amount)
            return f"Gifted {formatted_amount} Tier {tier_num} subs ({months} months)!"
        elif grouping_key == "follow":
            if count > 1:
                return f"Followed {count} times!"
            else:
                return "Followed!"
        elif grouping_key == "bits":
            # Format with commas for readability
            try:
                formatted_amount = f"{int(total_amount):,}"
            except (ValueError, TypeError):
                formatted_amount = str(total_amount)
            return f"Gave {formatted_amount} bits!"
        elif grouping_key == "donation":
            # Format donation amount
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
            # For raids, show the largest raid
            try:
                formatted_viewers = f"{int(total_amount):,}"
            except (ValueError, TypeError):
                formatted_viewers = str(total_amount)
            game_name = data.get("game_name")
            suffix = _raid_category_suffix(game_name)
            if count > 1:
                return (
                    f"Raided {count} times (largest: {formatted_viewers} viewers)"
                    f"{suffix}"
                )
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
            # Fallback for any other alert types
            display_name = grouping_key.replace("_", " ").title()
            if count > 1:
                return f"{display_name} {count} times!"
            else:
                return f"{display_name}!"

    except Exception as e:
        logger.error(f"Error creating aggregated alert text: {str(e)}", exc_info=True)
        return f"{grouping_key.title()}!"


def create_condensed_alert_text(alert_data):
    """Create condensed text for an alert (legacy function for compatibility)"""
    try:
        alert_type = alert_data.get("type", "").lower()
        user_message = alert_data.get("user_message")

        if alert_type == "follow":
            return "Followed!"
        elif alert_type == "sub":
            tier = alert_data.get("tier", 1)
            return f"Subscribed (Tier {tier})!"
        elif alert_type == "resub":
            # Extract months from message
            message = alert_data.get("message", "")
            import re

            month_match = re.search(r"(\d+)\s*months?", message, re.IGNORECASE)
            months = month_match.group(1) if month_match else "?"
            return f"Resubscribed for {months} months!"
        elif alert_type == "giftsub":
            # Extract gift quantity from message
            message = alert_data.get("message", "")
            import re

            gift_match = re.search(r"gifted\s+(\d+)", message, re.IGNORECASE)
            qty = gift_match.group(1) if gift_match else "1"
            return f"Gifted {qty} subs!"
        elif alert_type == "bits":
            # Extract bit amount from message
            message = alert_data.get("message", "")
            import re

            bit_match = re.search(r"(\d+)\s*bits?", message, re.IGNORECASE)
            amount = bit_match.group(1) if bit_match else "?"
            # Format with commas for readability
            try:
                amount_int = int(amount)
                amount = f"{amount_int:,}"
            except (ValueError, TypeError):
                pass
            return f"Gave {amount} bits!"
        elif alert_type == "donation":
            # Extract donation amount from message
            message = alert_data.get("message", "")
            import re

            donation_match = re.search(
                r"donated\s+\$?(\d+(?:\.\d{2})?)", message, re.IGNORECASE
            )
            amount = donation_match.group(1) if donation_match else "?"
            return f"Donated ${amount}!"
        elif alert_type in ("membership", "member"):
            return "Became a channel member!"
        elif alert_type in ("member_milestone", "member milestone"):
            return "Reached a member milestone!"
        elif alert_type in ("gift_membership", "gift membership"):
            return "Gifted memberships!"
        elif alert_type in ("superchat", "super chat"):
            return "Sent a Super Chat!"
        elif alert_type in ("supersticker", "super sticker"):
            return "Sent a Super Sticker!"
        elif alert_type == "raid":
            stored_data = alert_data.get("stored_alert_data") or {}
            raider_count = alert_data.get("raider_count") or stored_data.get(
                "raider_count"
            )
            game_name = alert_data.get("game_name") or stored_data.get("game_name")
            if raider_count is not None:
                try:
                    viewers_int = int(raider_count)
                    viewers = f"{viewers_int:,}"
                except (ValueError, TypeError):
                    viewers = str(raider_count)
            else:
                message = alert_data.get("message", "")
                import re

                raid_match = re.search(r"(\d+)\s*viewers?", message, re.IGNORECASE)
                viewers = raid_match.group(1) if raid_match else "?"
                try:
                    viewers_int = int(viewers)
                    viewers = f"{viewers_int:,}"
                except (ValueError, TypeError):
                    pass
            return f"Raided with {viewers} viewers{_raid_category_suffix(game_name)}"
        else:
            return f"{alert_type.title()}!"

    except Exception as e:
        logger.error(f"Error creating condensed alert text: {str(e)}", exc_info=True)
        return f"{alert_data.get('type', 'Alert')}!"


def switch_to_tab(tab_name):
    """Switch to a specific tab (current or previous)"""
    try:
        # Don't switch if we're already on this tab
        if activity_feed_state.current_tab == tab_name:
            logger.debug(f"Already on {tab_name} tab, skipping switch")
            return

        previous_tab = activity_feed_state.current_tab
        activity_feed_state.current_tab = tab_name

        if tab_name == "previous":
            # Switching to previous alerts tab
            logger.debug(f"Switching from {previous_tab} to previous alerts tab")

            # Clear any existing restored alerts to prevent accumulation
            clear_restored_alerts()
            activity_feed_state.current_page = 1  # Always start at page 1
            activity_feed_state.restored_alerts_loaded = False

            # Show pagination container
            if activity_feed_state.pagination_container:
                activity_feed_state.pagination_container.classes(remove="hidden")

            # Hide current alerts container and show previous alerts container
            if activity_feed_state.current_alerts_container:
                activity_feed_state.current_alerts_container.classes(add="hidden")
            if activity_feed_state.previous_alerts_container:
                activity_feed_state.previous_alerts_container.classes(remove="hidden")

            # Hide condensed view and toggle when switching to previous tab
            if activity_feed_state.condensed_container:
                activity_feed_state.condensed_container.classes(add="hidden")
            if activity_feed_state.condense_toggle:
                activity_feed_state.condense_toggle.classes(add="hidden")

            # Load the first page of restored alerts
            refresh_restored_alerts()

            logger.debug("Successfully switched to previous alerts tab")
        else:
            # Switching to current alerts tab
            logger.debug(f"Switching from {previous_tab} to current alerts tab")

            # Hide pagination container
            if activity_feed_state.pagination_container:
                activity_feed_state.pagination_container.classes(add="hidden")

            # Show current alerts container and hide previous alerts container
            if activity_feed_state.current_alerts_container:
                activity_feed_state.current_alerts_container.classes(remove="hidden")
            if activity_feed_state.previous_alerts_container:
                activity_feed_state.previous_alerts_container.classes(add="hidden")

            # Clear historical alerts from previous container to free memory
            clear_restored_alerts()
            activity_feed_state.restored_alerts_loaded = False

            # Rebuild feed with only live alerts (including any that came in while on previous tab)
            rebuild_current_alerts_feed()

            # Apply filters to the current alerts
            update_alert_visibility()

            # Update condensed view visibility
            update_condensed_view()

            schedule_feed_integrity_check("switch_to_tab_current")

            logger.debug("Successfully switched to current alerts tab")

    except Exception as e:
        logger.error(f"Error switching to {tab_name} tab: {str(e)}", exc_info=True)

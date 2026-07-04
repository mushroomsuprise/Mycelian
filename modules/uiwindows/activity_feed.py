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
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import current_process
from typing import Any, Callable, Dict, List, Optional

from nicegui import app, ui

from ..notification_engine import notify
from .. import alertutils
from ..ui_timer import app_schedule

logger = logging.getLogger(__name__)

_pause_breath_timer_started = False
_recovery_scheduled = False
_integrity_check_reason: Optional[str] = None
_integrity_check_scheduled = False


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


def _containers_alive() -> bool:
    return _element_alive(activity_feed_state.current_alerts_container)


def _count_rendered_live_alerts() -> int:
    """Return how many live alerts still have a bound, alive UI element."""
    count = 0
    for alert_data in activity_feed_state.live_alerts:
        element = alert_data.get("element")
        if element is not None and _element_alive(element):
            count += 1
    return count


def _ensure_feed_integrity(reason: str) -> None:
    """Rebuild the feed when state has alerts but nothing is rendered."""
    if activity_feed_state.current_tab != "current":
        return
    if activity_feed_state.condense_list:
        return
    if not _containers_alive():
        return
    if not activity_feed_state.live_alerts:
        return
    if _count_rendered_live_alerts() > 0:
        return

    logger.warning(
        "activity_feed: integrity mismatch (%s) — %d live alerts, 0 rendered",
        reason,
        len(activity_feed_state.live_alerts),
    )
    recover_activity_feed_panel()


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


def _run_on_ui_loop(fn: Callable[[], Any]) -> None:
    """Marshal UI mutations onto NiceGUI's asyncio loop (safe from worker threads)."""
    try:
        from nicegui import core

        loop = getattr(core, "loop", None)
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(fn)
            return
    except Exception:
        pass
    try:
        fn()
    except Exception as exc:
        logger.error("activity_feed UI callback failed: %s", exc, exc_info=True)


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
    if not _containers_alive():
        return False

    try:
        for alert_data in activity_feed_state.live_alerts:
            alert_data["element"] = None
            alert_data["new_badge"] = None
            alert_data["timestamp_label"] = None

        rebuild_current_alerts_feed()
        if activity_feed_state.condense_list and activity_feed_state.current_tab == "current":
            update_condensed_view()
        update_alert_visibility()
        activity_feed_state.is_initialized = True
        logger.info("activity_feed: recovered (%d live alerts)", len(activity_feed_state.live_alerts))
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

    def _on_disconnect(_client=None) -> None:
        logger.info("activity_feed: client_disconnected")
        activity_feed_state.is_initialized = False

    def _on_connect(_client=None) -> None:
        if not _connect_recovery_enabled:
            return
        logger.info("activity_feed: client_connected — scheduling recovery")
        app_schedule(0.5, recover_after_client_reconnect, once=True)

    def _on_delete(_client=None) -> None:
        logger.info("activity_feed: client_deleted — scheduling recovery")
        _handle_stale_client("client_deleted")

    client.on_disconnect(_on_disconnect)
    client.on_connect(_on_connect)
    client.on_delete(_on_delete)
    app_schedule(3.0, _enable_connect_recovery, once=True)
    logger.debug("activity_feed: client lifecycle hooks registered")


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
            created_time = alert_data.get("created_at", current_time)
            is_recent = (current_time - float(created_time)) < 300  # 5 minutes

            new_alert_data = {
                "type": alert_data["type"],
                "message": alert_data["message"],
                "badge_type": alert_data["badge_type"],
                "timestamp": alert_data["timestamp"],
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
        if not (
            activity_feed_state.is_initialized
            and _containers_alive()
        ):
            logger.debug("UI not initialized or containers stale, skipping alert")
            return

        with self._ui_update_lock:
            activity_feed_state.live_alerts.insert(0, new_alert_data)

            should_display_new_alert = activity_feed_state.current_tab == "current"

            if should_display_new_alert:
                if not create_alert_element(new_alert_data):
                    return
                self._apply_filter_visibility(new_alert_data, alert_type)
                ui.update()
                logger.debug(
                    f"Displayed new alert: {alert_type} (current tab: {activity_feed_state.current_tab})"
                )
            else:
                logger.debug(
                    f"New alert {alert_type} added to state but not displayed - on {activity_feed_state.current_tab} tab"
                )

            if (
                activity_feed_state.condense_list
                and activity_feed_state.current_tab == "current"
            ):
                update_condensed_view()
            elif should_display_new_alert:
                schedule_feed_integrity_check("apply_alert_on_ui")

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
            if new_alert_data.get("element"):
                new_alert_data["element"].classes(add="hidden")

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

            if not alert_data.get("element") or not alert_data.get("new_badge"):
                continue

            try:
                created_time = float(alert_data.get("created_at", current_time))
                time_diff = current_time - created_time
                is_recent = time_diff < 300  # 5 minutes

                # Check if badge visibility needs to change
                badge_hidden = "hidden" in alert_data["new_badge"]._classes
                should_hide = not is_recent

                if badge_hidden != should_hide:
                    if should_hide:
                        alert_data["new_badge"].classes(add="hidden")
                    else:
                        alert_data["new_badge"].classes(remove="hidden")
                    updates_made = True

                # Update timestamp if it exists
                if alert_data.get("timestamp_label"):
                    old_timestamp = alert_data["timestamp_label"].text
                    new_timestamp = format_timestamp(alert_data.get("timestamp", ""))
                    if old_timestamp != new_timestamp:
                        alert_data["timestamp_label"].set_text(new_timestamp)
                        updates_made = True

            except Exception as e:
                logger.error(f"Error updating individual live alert status: {str(e)}")

        # Only force UI update if changes were made
        if updates_made:
            ui.update()
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
    if btn is None:
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
    except Exception:
        pass


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
        alert_processor.ALERT_QUEUE.append(replay_alert_obj)
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

            # Skip the current alert by setting a flag or emitting a skip event
            web_engine.web_engine_instance.socketio.emit("skip_alert", clean_alert_data)
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
):
    """
    Build the same dict that is sent over ``activity_feed_alert`` WebSocket events.

    Shared by ``add_alert_to_feed`` and browser-source preview so payloads stay identical.
    """
    alert_data = {
        "type": alert_type,
        "message": message,
        "badge_type": badge_type,
        "timestamp": timestamp if timestamp != "now" else time.time(),
        "created_at": time.time(),
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
                    for field in ("resub_month", "gift_qty", "amt_cheered", "tier"):
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
        payload = build_activity_feed_alert_payload(
            "Giftsub",
            f"{username} gifted {qty} Tier {tier} subs!",
            "giftsub",
            timestamp=ts,
            tier=tier,
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
    yield build_activity_feed_alert_payload(
        "Giftsub",
        f"{gift_user} gifted {gift_qty} Tier {gift_tier} subs!",
        "giftsub",
        timestamp=ts,
        tier=gift_tier,
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

        container.clear()

        for alert_data in activity_feed_state.alert_elements:
            if not create_alert_element(alert_data):
                return

        ui.update()

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

        container.clear()

        for alert_data in activity_feed_state.live_alerts:
            if not create_alert_element(alert_data):
                return

        ui.update()

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
        created_time = alert_data.get("created_at", current_time)
        is_recent = (
            current_time - float(created_time)
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
        if not alert_data.get("element"):
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
        current_hidden = "hidden" in alert_data["element"]._classes
        needs_update = (should_show and current_hidden) or (
            not should_show and not current_hidden
        )

        if needs_update:
            # Remove classes one at a time
            alert_data["element"].classes(remove="hidden")
            alert_data["element"].classes(remove="visible")

            # Then add the appropriate class and style
            if should_show:
                alert_data["element"].classes(add="visible")
                alert_data["element"].style("display: block")
            else:
                alert_data["element"].classes(add="hidden")
                alert_data["element"].style("display: none")

            updates_needed += 1

    # Add CSS for visibility classes if not already added
    ui.add_head_html(
        """
        <style>
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

    # Only update UI if we actually made changes
    if updates_needed > 0:
        ui.update()
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


def create_activity_feed_tab():
    """Create the activity feed tab UI"""
    logger.debug("Creating activity feed tab...")

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
        </style>
    """,
        shared=True,
    )

    _DOCK_BTN_PROPS = "flat no-caps dense"

    with ui.element("div").classes("tab-surface w-full h-full relative flex flex-col p-4"):
        # Control buttons row (nowrap + horizontal scroll avoids clipping toggle on narrow/scaled windows)
        with ui.row().classes(
            "w-full items-center gap-2 mb-4 flex-nowrap overflow-x-auto"
        ):
            from modules import web_engine
            from modules.mainuiwindow import toggle_alerts

            def on_pause_button_click():
                toggle_alerts()
                update_pause_button_state()

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

            def update_pause_button_state():
                """Update the pause button state based on current ALERTS_PAUSED status"""
                try:
                    # Get the current pause state with better error handling
                    paused = False
                    try:
                        if hasattr(web_engine, "ALERTS_PAUSED"):
                            paused = bool(web_engine.ALERTS_PAUSED)
                            logger.debug(
                                f"Got pause state from ALERTS_PAUSED: {paused}"
                            )
                        else:
                            logger.debug(
                                "web_engine not available, defaulting to False"
                            )
                    except Exception as state_err:
                        logger.debug(
                            f"Error getting pause state: {str(state_err)}, defaulting to False"
                        )
                        paused = False

                    logger.debug(f"Final pause state: paused={paused}")

                    if paused:
                        pause_btn.props(f"{_DOCK_BTN_PROPS} id=pause-alerts-btn icon=play_arrow")
                        pause_btn.text = "RESUME ALERTS"
                        pause_btn.classes(remove="alerts-playing")
                        pause_btn.classes(add="paused")
                        pause_btn.style("box-shadow: none")
                        logger.debug("Updated button to RESUME ALERTS state")

                    else:
                        pause_btn.props(f"{_DOCK_BTN_PROPS} id=pause-alerts-btn icon=pause")
                        pause_btn.text = "PAUSE ALERTS"
                        pause_btn.classes(remove="paused")
                        pause_btn.classes(add="alerts-playing")
                        logger.debug("Updated button to PAUSE ALERTS state")

                except Exception as e:
                    logger.error(
                        f"Error updating pause button state: {str(e)}", exc_info=True
                    )

            # Event-based system doesn't need to store button update function reference

            # Initialize the button state with error handling
            try:
                update_pause_button_state()
            except Exception as e:
                logger.error(
                    f"Error initializing pause button state: {str(e)}", exc_info=True
                )

            mute_btn = ui.button(icon="notifications_off", text="MUTE ALERTS").classes(
                "control-button mute-alerts-btn"
            ).props(f"{_DOCK_BTN_PROPS} id=mute-alerts-btn")
            activity_feed_state.mute_btn = mute_btn

            def update_mute_button_state():
                """Update mute button based on current ALERTS_MUTED status."""
                try:
                    muted = False
                    try:
                        if hasattr(web_engine, "ALERTS_MUTED"):
                            muted = bool(web_engine.ALERTS_MUTED)
                    except Exception as state_err:
                        logger.debug(
                            f"Error getting mute state: {state_err}, defaulting to False"
                        )
                    activity_feed_state.alerts_muted = muted
                    if muted:
                        mute_btn.classes(add="muted")
                    else:
                        mute_btn.classes(remove="muted")
                except Exception as e:
                    logger.error(
                        f"Error updating mute button state: {str(e)}", exc_info=True
                    )

            def on_mute_button_click():
                from modules.mainuiwindow import toggle_mute

                toggle_mute()
                update_mute_button_state()

            mute_btn.on_click(on_mute_button_click)

            try:
                update_mute_button_state()
            except Exception as e:
                logger.error(
                    f"Error initializing mute button state: {str(e)}", exc_info=True
                )
            skip_btn = ui.button(icon="skip_next", text="SKIP ALERT").classes(
                "control-button"
            )
            skip_btn.props(_DOCK_BTN_PROPS)

            ui.element("div").classes("grow")

            # Condense List toggle (only visible on Current Alerts tab)
            def toggle_condense_list(e):
                logger.debug(f"Condense list toggle changed: {e.value}")
                activity_feed_state.condense_list = e.value
                logger.debug(
                    f"Updated activity_feed_state.condense_list to: {activity_feed_state.condense_list}"
                )
                update_condensed_view()

            activity_feed_state.condense_toggle = ui.switch(
                text="Condense List", value=False, on_change=toggle_condense_list
            ).classes("condense-toggle shrink-0")
            logger.debug(
                "Condense List toggle created (id=%s)",
                getattr(activity_feed_state.condense_toggle, "id", None),
            )

            # Create a container for the filter dropdown
            with ui.element("div").classes("relative shrink-0"):
                # Create a button for filters
                filter_button = ui.button(icon="filter_list", text="FILTERS").classes(
                    "control-button"
                )
                filter_button.props(_DOCK_BTN_PROPS)

                # Create an invisible backdrop that covers the entire screen when dropdown is open
                backdrop = ui.element("div").classes("fixed inset-0 z-40")
                backdrop.style("background: transparent; display: none;")
                backdrop.on("click", lambda e: close_filter_dropdown())

                # Create a custom dropdown container (start visible: false, initially hidden)
                filter_dropdown = ui.element("div").classes(
                    "absolute top-full right-0 z-50 mt-1 bg-theme-base rounded-md shadow-lg w-56"
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

                # Toggle the dropdown when the filter button is clicked
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

                # Add a direct click handler to the filter button
                filter_button.on("click", toggle_filter_dropdown)

                # Create a container for the checkboxes with the filter-dropdown-content class
                with filter_dropdown:
                    with ui.element("div").classes("filter-dropdown-content"):
                        # Create a scrollable container for checkboxes
                        with ui.element("div").classes("max-h-60 overflow-y-auto p-2"):
                            # Create checkboxes for each event type
                            def create_checkbox(key: str, label: str):
                                def on_change(e):
                                    logger.debug(f"Checkbox {key} changed to {e.value}")
                                    # Add a data attribute to identify this as a filter checkbox
                                    e.sender.classes("filter-checkbox")

                                    # Update the filter state (this is the important part)
                                    on_checkbox_change(key, e.value)

                                    # Only manage dropdown visibility if it's currently visible
                                    if activity_feed_state.dropdown_visible:
                                        # Ensure the dropdown stays visible during checkbox interactions
                                        if activity_feed_state.filter_dropdown:
                                            activity_feed_state.filter_dropdown.visible = True
                                        logger.debug(
                                            "Maintained dropdown visibility after checkbox change"
                                        )

                                checkbox = ui.checkbox(
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
                "w-full"
            )
            activity_feed_state.previous_alerts_container = ui.element("div").classes(
                "w-full hidden"
            )

            # Create condensed view container (hidden by default)
            activity_feed_state.condensed_container = ui.element("div").classes(
                "w-full hidden condensed-view"
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

            # Ensure condense toggle visibility matches initial tab (Current Alerts)
            update_condensed_view()

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


def sync_pause_button_state():
    """Public function to sync the pause button state - can be called from other modules"""
    # With event-based system, button state is managed internally
    # This function remains for compatibility but no longer needs to do anything
    logger.debug("Pause button state sync requested - handled by event system")


def sync_mute_button_state():
    """Sync the mute button state from web_engine.ALERTS_MUTED."""
    btn = activity_feed_state.mute_btn
    if btn is None:
        logger.debug("Mute button not initialized, skipping sync")
        return
    try:
        from modules import web_engine

        muted = bool(getattr(web_engine, "ALERTS_MUTED", False))
        activity_feed_state.alerts_muted = muted
        if muted:
            btn.classes(add="muted")
        else:
            btn.classes(remove="muted")
        ui.update()
    except Exception as e:
        logger.debug(f"Error syncing mute button state: {e}")


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
            if resub_month > 1:
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
            message = f"{username} has resubscribed for {resub_month} months!"
        elif original_type == "giftsub":
            gift_qty = stored_alert_data.get("gift_qty", 1)
            tier = stored_alert_data.get("tier", 1)
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
            "badge_type": original_type,
            "timestamp": stored_alert_data.get("timestamp", time.time()),
            "created_at": stored_alert_data.get("timestamp", time.time()),
            "tier": stored_alert_data.get("tier"),
            "user_message": stored_alert_data.get("message"),
            "is_restored": True,  # Mark as restored alert
            "alert_id": stored_alert_data.get(
                "alert_id"
            ),  # Include original alert ID for debugging
            "username": username,  # Include the resolved username
            # Preserve the full original stored alert data for replay functionality
            "stored_alert_data": stored_alert_data,
        }

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
            if alert_data.get("timestamp", 0) < cutoff_time:
                continue
            row = dict(alert_data)
            row["alert_id"] = alert_id
            feed_alert = convert_stored_alert_to_feed_format(row)
            if feed_alert:
                restored_alerts.append(feed_alert)

        restored_alerts.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
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
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return 0.0


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


def update_condensed_view():
    """Update the condensed view based on the toggle state"""
    try:
        logger.debug(
            f"update_condensed_view called - current tab: {activity_feed_state.current_tab}, condense_list: {activity_feed_state.condense_list}"
        )

        if not activity_feed_state.condense_toggle:
            logger.debug("No condense toggle found, returning early")
            return

        # Show/hide condense toggle based on current tab
        if activity_feed_state.current_tab == "current":
            activity_feed_state.condense_toggle.classes(remove="hidden")
            logger.debug("Condense List toggle shown (Current Alerts tab)")
        else:
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
            logger.debug("Creating condensed view...")
            built = create_condensed_view()
            if built:
                if activity_feed_state.current_alerts_container:
                    activity_feed_state.current_alerts_container.classes(add="hidden")
                if activity_feed_state.condensed_container:
                    activity_feed_state.condensed_container.classes(remove="hidden")
            else:
                logger.warning(
                    "Condensed view build failed; keeping regular alerts visible"
                )
                if activity_feed_state.condensed_container:
                    activity_feed_state.condensed_container.classes(add="hidden")
                if activity_feed_state.current_alerts_container:
                    activity_feed_state.current_alerts_container.classes(
                        remove="hidden"
                    )
        else:
            logger.debug("Showing regular alerts view...")
            # Show regular alerts
            if activity_feed_state.current_alerts_container:
                activity_feed_state.current_alerts_container.classes(remove="hidden")
            # Hide condensed view
            if activity_feed_state.condensed_container:
                activity_feed_state.condensed_container.classes(add="hidden")

            schedule_feed_integrity_check("update_condensed_view_regular")

    except Exception as e:
        logger.error(f"Error updating condensed view: {str(e)}", exc_info=True)


def create_condensed_view() -> bool:
    """Create the condensed view of alerts grouped by user.

    Returns:
        True if the condensed container was rebuilt successfully, False otherwise.
    """
    try:
        container = activity_feed_state.condensed_container
        if not _element_alive(container):
            return False

        container.clear()

        # Get template configuration for condensed view settings
        condense_historical_hours = 12

        try:
            # Load template configuration using the existing parser
            from modules.template_config_parser import TemplateConfigParser

            config_parser = TemplateConfigParser()
            config_data = config_parser.load_config("activity_feed")

            if config_data and "elements" in config_data:
                # Parse configuration from elements array (same as HTML templates do)
                for element in config_data["elements"]:
                    if element.get("type") != "section":  # Skip section headers
                        element_id = element.get("id")
                        element_value = element.get("value")

                        if element_id == "condense_historical_hours":
                            condense_historical_hours = (
                                int(element_value) if element_value is not None else 12
                            )
                print(
                    f"Loaded condensed view config: hours={condense_historical_hours}"
                )
                logger.debug(
                    f"Loaded condensed view config: hours={condense_historical_hours}"
                )
            else:
                print("No activity_feed config elements found, using defaults")
                logger.debug("No activity_feed config elements found, using defaults")

        except Exception as e:
            # Use default values if configuration is not available
            logger.debug(f"Could not load template config for condensed view: {e}")
            pass

        current_time = time.time()
        cutoff_time = current_time - (
            condense_historical_hours * 3600
        )  # Convert hours to seconds
        historical_count = 0
        live_in_window = 0

        print(
            f"Loading alerts for condensed view from past {condense_historical_hours} hours..."
        )
        logger.debug(
            f"Loading alerts for condensed view from past {condense_historical_hours} hours..."
        )

        try:
            (
                alerts_to_process,
                historical_count,
                live_in_window,
            ) = collect_alerts_for_condensed_view(cutoff_time)

            print(
                f"Condensed view loaded {historical_count} stored and {live_in_window} live alerts (past {condense_historical_hours} hours)"
            )
            logger.debug(
                f"Condensed view loaded {historical_count} stored and {live_in_window} live alerts (past {condense_historical_hours} hours)"
            )

        except Exception as e:
            logger.error(f"Error loading alerts for condensed view: {e}")
            print(f"Error loading alerts for condensed view: {e}")
            alerts_to_process = []
            historical_count = 0
            live_in_window = 0

        logger.debug(
            f"Total alerts to process for condensed view: {len(alerts_to_process)}"
        )
        print(f"Total alerts to process for condensed view: {len(alerts_to_process)}")

        # Group alerts by user and type, excluding hype train and point alerts
        user_alerts = {}
        excluded_count = 0
        unknown_username_count = 0

        for alert_data in alerts_to_process:
            alert_type = alert_data.get("type", "").lower()
            badge_type = alert_data.get("badge_type", "").lower()

            # Exclude hype train and point alerts (channel point redemptions)
            if alert_type in ["hype train", "point"] or badge_type in [
                "hype_train",
                "point",
            ]:
                excluded_count += 1
                logger.debug(
                    f"Excluding {alert_type}/{badge_type} alert from condensed view"
                )
                continue

            # Get username from alert data, with fallback to message extraction
            username = alert_data.get("username")
            if not username:
                username = extract_username_from_message(alert_data.get("message", ""))

            if not username or username == "Unknown":
                unknown_username_count += 1
                logger.debug(
                    f"Skipping alert with unknown username: {alert_data.get('message', '')} (alert_id: {alert_data.get('alert_id', 'N/A')})"
                )
                continue

            if username not in user_alerts:
                user_alerts[username] = {}
                logger.debug(f"Added new user to condensed view: {username}")

            # Create specific grouping key based on alert type, tier, and duration
            grouping_key = alert_type

            # For subs, resubs, and gift subs, include tier and duration information
            if alert_type in ["sub", "resub", "giftsub"]:
                tier = alert_data.get("tier", 1)
                grouping_key = f"{alert_type}_tier{tier}"

                # For resubs and gift subs, also separate by duration
                if alert_type in ["resub", "giftsub"]:
                    # Get duration information
                    duration_months = 1
                    stored_data = alert_data.get("stored_alert_data", {})

                    if stored_data:
                        # Try stored data first
                        duration_months = (
                            stored_data.get("resub_month", 0)
                            or stored_data.get("months", 0)
                            or stored_data.get("total_months", 0)
                            or 1
                        )
                    else:
                        # Fall back to message parsing
                        message = alert_data.get("message", "")
                        import re

                        month_match = re.search(
                            r"(\d+)\s*months?", message, re.IGNORECASE
                        )
                        if month_match:
                            duration_months = int(month_match.group(1))

                    # Create duration-specific key
                    if duration_months > 1:
                        grouping_key = f"{alert_type}_tier{tier}_multi_month"
                    else:
                        grouping_key = f"{alert_type}_tier{tier}_1month"

            # Group by the specific key and accumulate data
            if grouping_key not in user_alerts[username]:
                user_alerts[username][grouping_key] = {
                    "count": 0,
                    "total_amount": 0,
                    "tier": alert_data.get("tier", 1),
                    "months": 0,  # For resubs, track months
                    "original_type": alert_type,  # Keep track of original alert type
                }

            user_alerts[username][grouping_key]["count"] += 1

            # Extract amounts based on alert type - prioritize stored_alert_data values over message parsing
            message = alert_data.get("message", "")
            stored_data = alert_data.get("stored_alert_data", {})

            import re

            if alert_type == "bit" or alert_type == "bits":
                # Try to get amount from stored data first
                amt_cheered = stored_data.get("amt_cheered") if stored_data else None
                if amt_cheered:
                    user_alerts[username][grouping_key]["total_amount"] += int(
                        amt_cheered
                    )
                else:
                    # Fall back to message parsing
                    bit_match = re.search(r"(\d+)\s*bits?", message, re.IGNORECASE)
                    if bit_match:
                        user_alerts[username][grouping_key]["total_amount"] += int(
                            bit_match.group(1)
                        )
            elif alert_type == "donation":
                # Try to get amount from stored data first
                donation_amount = (
                    stored_data.get("donation_amount") if stored_data else None
                )
                if donation_amount:
                    user_alerts[username][grouping_key]["total_amount"] += float(
                        donation_amount
                    )
                else:
                    # Fall back to message parsing
                    donation_match = re.search(
                        r"donated\s+\$?(\d+(?:\.\d{2})?)", message, re.IGNORECASE
                    )
                    if donation_match:
                        user_alerts[username][grouping_key]["total_amount"] += float(
                            donation_match.group(1)
                        )
            elif alert_type == "giftsub":
                # Try to get amount from stored data first
                gift_qty = stored_data.get("gift_qty") if stored_data else None
                if gift_qty:
                    user_alerts[username][grouping_key]["total_amount"] += int(gift_qty)
                else:
                    # Fall back to message parsing
                    gift_match = re.search(r"gifted\s+(\d+)", message, re.IGNORECASE)
                    if gift_match:
                        user_alerts[username][grouping_key]["total_amount"] += int(
                            gift_match.group(1)
                        )
            elif alert_type == "resub":
                # For resubs, get the month count
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
                    # Fall back to message parsing
                    month_match = re.search(r"(\d+)\s*months?", message, re.IGNORECASE)
                    if month_match:
                        months = int(month_match.group(1))
                    else:
                        months = 1
                # Keep the highest month count for this user
                if months > user_alerts[username][grouping_key]["months"]:
                    user_alerts[username][grouping_key]["months"] = months
            elif alert_type == "raid":
                # Try to get viewers from stored data first
                raider_count = stored_data.get("raider_count") if stored_data else None
                if raider_count:
                    viewers = int(raider_count)
                else:
                    # Fall back to message parsing
                    raid_match = re.search(r"(\d+)\s*viewers?", message, re.IGNORECASE)
                    if raid_match:
                        viewers = int(raid_match.group(1))
                    else:
                        viewers = 0
                # Keep the largest raid for this user
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

        # Debug summary
        logger.debug(f"Condensed view processing complete:")
        logger.debug(f"  - Processed {len(alerts_to_process)} total alerts")
        logger.debug(f"  - Excluded {excluded_count} hype train/points alerts")
        logger.debug(
            f"  - Skipped {unknown_username_count} alerts with unknown usernames"
        )
        logger.debug(f"  - Final result: {len(user_alerts)} users with alerts")

        # Create the condensed UI
        with activity_feed_state.condensed_container:
            if not user_alerts:
                ui.label(
                    f"No alerts to condense in the last {condense_historical_hours} hours"
                ).classes("text-sm muted-text italic")
            else:
                if historical_count > 0 or live_in_window > 0:
                    ui.label(
                        f"Showing {len(alerts_to_process)} alerts from past {condense_historical_hours} hours"
                    ).classes("text-xs muted-text mb-2 italic")

                for username, alert_types in user_alerts.items():
                    if alert_types:  # Only show users with alerts
                        with ui.element("div").classes("mb-4"):
                            ui.label(f"{username}:").classes(
                                "font-semibold mb-1"
                            )
                            with ui.element("div").classes("ml-4"):
                                for grouping_key, data in alert_types.items():
                                    condensed_text = create_aggregated_alert_text(
                                        grouping_key, data
                                    )
                                    if condensed_text:
                                        ui.label(f"• {condensed_text}").classes(
                                            "secondary-text text-sm mb-1"
                                        )

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

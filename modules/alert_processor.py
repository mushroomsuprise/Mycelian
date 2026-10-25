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

import logging
import threading
import time
from collections import deque

from .alertutils import AlertObj, initialize_alert_state
from .notification_engine import nav_actions_main_tab, nav_actions_settings, notify, notify_critical
from .shutdown import is_shutdown_in_progress

ALERT_QUEUE: deque[AlertObj] = deque()
_ALERT_QUEUE_LOCK = threading.Lock()
_ALERT_WAKE = threading.Event()
_ALERT_COMPLETE = threading.Event()
web_engine_instance = None  # Initialize to None; prefer web_engine.web_engine_instance
logger = logging.getLogger(__name__)


class _LazyWebEngine:
    """Load Flask/SocketIO overlay module on first attribute access."""

    def __init__(self):
        object.__setattr__(self, "_mod", None)

    def _load(self):
        mod = object.__getattribute__(self, "_mod")
        if mod is None:
            from . import web_engine as web_engine_module

            object.__setattr__(self, "_mod", web_engine_module)
            mod = web_engine_module
        return mod

    def __getattr__(self, name):
        return getattr(self._load(), name)

    def __setattr__(self, name, value):
        setattr(self._load(), name, value)


web_engine = _LazyWebEngine()

# Global thread references
web_thread = None
alert_thread = None

# Global flag to track Alert Queue status
alert_queue_active = False

# Global flag to track initialization status
_initialized = False


def wake_alert_queue() -> None:
    """Wake the alert-queue thread (enqueue, pause/resume, shutdown)."""
    _ALERT_WAKE.set()


def wait_for_alert_queue(*, playing: bool, paused: bool) -> None:
    """Block until the next alert event. Idle waits with no timeout."""
    if paused:
        _ALERT_WAKE.wait(timeout=0.5)
    elif playing:
        _ALERT_WAKE.wait(timeout=0.2)
    else:
        _ALERT_WAKE.wait()
    _ALERT_WAKE.clear()


def notify_alert_playing(playing: bool) -> None:
    """Signal alert_complete / skip so process_alert can stop waiting."""
    if playing:
        _ALERT_COMPLETE.clear()
    else:
        _ALERT_COMPLETE.set()
    _ALERT_WAKE.set()


def enqueue_alert(obj: AlertObj) -> None:
    """Append an alert under the queue lock (safe from any thread)."""
    with _ALERT_QUEUE_LOCK:
        ALERT_QUEUE.append(obj)
    wake_alert_queue()


def snapshot_alert_queue() -> list[AlertObj]:
    """Return a copy of the current queue."""
    with _ALERT_QUEUE_LOCK:
        return list(ALERT_QUEUE)


def remove_queued_alert(obj: AlertObj) -> bool:
    """Remove one queued alert. Returns False if it was already gone."""
    with _ALERT_QUEUE_LOCK:
        try:
            ALERT_QUEUE.remove(obj)
            return True
        except ValueError:
            return False


def _overlay_engine():
    """Prefer the canonical web_engine singleton."""
    return web_engine.web_engine_instance or web_engine_instance


def _alert_label(alert) -> str:
    """Short plain name for an alert in notifications ("follow alert from TestUser")."""
    kind = str(getattr(alert, "alert_type", "") or "").replace("_", " ").strip() or "an"
    user = str(getattr(alert, "username", "") or "").strip()
    return f"{kind} alert from {user}" if user else f"{kind} alert"


def _notify_alert_dropped(alert, reason: str) -> None:
    """The overlay never got this alert, so it was skipped."""
    notify(
        f"The {_alert_label(alert)} was skipped because the overlay server isn't running.",
        type="warning",
        source="alerts",
        title="An alert couldn't be shown",
        hint="Check the overlay server in the footer. New alerts will play once it is running again.",
        details=f"reason={reason} alert_id={getattr(alert, 'alert_id', '')}",
        actions=nav_actions_main_tab("Activity Feed"),
        dedupe_key="alert:dropped",
        dedupe_cooldown_sec=300.0,
    )


def process_alert(alert: AlertObj):
    """
    Process an alert by emitting it over the next_alert websocket

    Args:
        alert (AlertObj): The alert object to process
    """
    try:
        if getattr(alert, "alert_type", "") == "point" and not alert.hold_queue_only:
            gif_ok = (
                alert.gif_dir
                and str(alert.gif_dir).strip()
                and alert.gif_name
                and str(alert.gif_name).strip()
            )
            audio_ok = alert.single_audio_dir and alert.single_audio_name
            if not gif_ok and not audio_ok and not alert.randomized:
                try:
                    from .template_config_parser import (
                        find_template_config_for_reward_title,
                        point_alert_silent_no_media_enabled,
                        point_reward_template_duration_seconds,
                    )

                    if point_alert_silent_no_media_enabled():
                        cfg = find_template_config_for_reward_title(
                            alert.alert_name or ""
                        )
                        if cfg:
                            alert.duration = point_reward_template_duration_seconds(
                                cfg
                            )
                except Exception as dur_exc:
                    logger.debug(
                        "Could not resolve point alert duration from template: %s",
                        dur_exc,
                    )

        # Convert AlertObj to dictionary for JSON serialization
        alert_data = vars(alert)
        queue_seq = web_engine.assign_next_alert_queue_seq()
        alert_data["queue_seq"] = queue_seq

        from .alert_playback_session import (
            coerce_duration_seconds,
            delay_between_alerts_seconds,
            get_session,
            queue_holder_routes_for_alert,
        )

        holders = queue_holder_routes_for_alert(alert)
        alert_data["queue_holders"] = holders
        alert_duration = coerce_duration_seconds(alert_data.get("duration"))
        delay_between = delay_between_alerts_seconds()

        web_engine.begin_alert_playback_session(
            queue_seq, holders, alert_duration, delay_between
        )

        # Emit the alert data over the websocket
        engine = _overlay_engine()
        if engine is None:
            logger.error("No web engine instance; cannot process alert")
            web_engine.abort_alert_playback("no_engine")
            _notify_alert_dropped(alert, "no_engine")
            return
        web_engine.set_alert_playing(True)
        if not engine.next_alert(alert_data):
            logger.error("Failed to emit next_alert; not marking ALERT_PLAYING")
            web_engine.abort_alert_playback("emit_failed")
            _notify_alert_dropped(alert, "emit_failed")
            return
        session = get_session()
        if session is None or session.seq != queue_seq or session.finished:
            web_engine.set_alert_playing(False)
            return

        # Wait until holders complete (or watchdog/fallback/skip)
        while web_engine.ALERT_PLAYING:
            if is_shutdown_in_progress():
                web_engine.abort_alert_playback("shutdown")
                break
            session = get_session()
            if session is None or session.seq != queue_seq:
                web_engine.set_alert_playing(False)
                break
            if session.tick():
                reason = session.finish_reason
                if reason in ("absolute_timeout", "no_ack_fallback"):
                    logger.warning(
                        "Alert handshake seq=%s finished via %s (type=%s holders=%s)",
                        queue_seq,
                        reason,
                        alert.alert_type,
                        holders,
                    )
                web_engine._apply_playback_session_finished(True)
                break
            _ALERT_COMPLETE.wait(timeout=0.2)

        logger.debug(f"Processed alert: {alert_data}")
    except Exception as e:
        logger.error(f"Error processing alert: {str(e)}", exc_info=True)
        web_engine.abort_alert_playback("process_failed")
        notify(
            f"The {_alert_label(alert)} couldn't play and was skipped.",
            type="error",
            source="alerts",
            title="An alert couldn't play",
            hint="Check the alert's media files in the Alerts tab. If this keeps happening, copy the details for support.",
            details=f"{type(e).__name__}: {e}",
            actions=nav_actions_main_tab("Alerts"),
            dedupe_key="alert:process_failed",
            dedupe_cooldown_sec=300.0,
        )


# The queue loop retries every second, so a lasting fault must not toast each pass.
_QUEUE_ERROR_NOTIFY_COOLDOWN_SEC = 900.0
_QUEUE_ERROR_ESCALATE_AFTER = 30  # consecutive failed passes (about 30 s)
_queue_error_state = {"streak": 0, "last_notified": None, "escalated": False}


def _note_queue_error(exc: Exception) -> None:
    """Tell the user once per fault streak, and once more if alerts stay stuck."""
    state = _queue_error_state
    state["streak"] += 1
    now = time.monotonic()
    details = f"{type(exc).__name__}: {exc}"
    last = state["last_notified"]
    if state["streak"] == 1 and (last is None or now - last >= _QUEUE_ERROR_NOTIFY_COOLDOWN_SEC):
        state["last_notified"] = now
        notify(
            "Alerts may be delayed while Mycelian retries.",
            type="warning",
            source="alerts",
            title="The alert queue hit an error",
            hint="Nothing to do yet. If alerts stop playing, restart Mycelian.",
            details=details,
            actions=nav_actions_main_tab("Activity Feed"),
            dedupe_key="alert:queue_error",
            dedupe_cooldown_sec=_QUEUE_ERROR_NOTIFY_COOLDOWN_SEC,
        )
    elif state["streak"] >= _QUEUE_ERROR_ESCALATE_AFTER and not state["escalated"]:
        state["escalated"] = True
        notify_critical(
            "New alerts won't play until this is fixed.",
            source="alerts",
            title="Alerts are stuck",
            hint="Restart Mycelian. If it happens again, copy the details for support.",
            details=details,
            actions=nav_actions_main_tab("Activity Feed"),
            dedupe_key="alert:queue_stuck",
            dedupe_cooldown_sec=_QUEUE_ERROR_NOTIFY_COOLDOWN_SEC,
        )


def _note_queue_ok() -> None:
    state = _queue_error_state
    if state["streak"]:
        state["streak"] = 0
        state["escalated"] = False


def alert_queue():
    global alert_queue_active
    logger.debug("Starting alert queue processor")
    alert_queue_active = True

    paused_state_logged = False  # Track if we've logged the paused state to avoid spam

    while True:
        try:
            if is_shutdown_in_progress():
                logger.debug("Alert queue stopping due to shutdown")
                alert_queue_active = False
                break
            if web_engine.ALERTS_PAUSED:
                if not paused_state_logged:
                    logger.debug("Alert queue is paused - waiting for resume")
                    paused_state_logged = True
                alert_queue_active = False
                wait_for_alert_queue(playing=False, paused=True)
                continue
            else:
                # Reset paused state logging when we're no longer paused
                if paused_state_logged:
                    logger.debug("Alert queue resumed from pause")
                    paused_state_logged = False
                # Ensure queue is marked as active when not paused and we have alerts
                with _ALERT_QUEUE_LOCK:
                    has_queued = len(ALERT_QUEUE) > 0
                if has_queued:
                    alert_queue_active = True

            # Check for stackable alerts
            processed_stackable = False
            queued = snapshot_alert_queue()
            for alert in queued:
                if alert.skip_alert:
                    logger.debug(f"Skipping alert: {alert.alert_type}")
                    remove_queued_alert(alert)
                    processed_stackable = True
                    continue

            if web_engine.ALERT_PLAYING:
                wait_for_alert_queue(playing=True, paused=False)
                continue

            queued = snapshot_alert_queue()
            for alert in queued:
                if alert.stackable:
                    logger.debug(f"Processing stackable alert: {alert.alert_type}")
                    if not remove_queued_alert(alert):
                        continue
                    process_alert(alert)
                    processed_stackable = True

            with _ALERT_QUEUE_LOCK:
                alert = ALERT_QUEUE.popleft() if ALERT_QUEUE else None
            if alert is not None:
                logger.debug(f"Processing alert from queue: {alert.alert_type}")
                process_alert(alert)
                alert_queue_active = True
            else:
                # No alerts to process; wait until enqueue/pause/complete.
                with _ALERT_QUEUE_LOCK:
                    alert_queue_active = len(ALERT_QUEUE) > 0
                if processed_stackable or web_engine.ALERT_PLAYING:
                    wait_for_alert_queue(
                        playing=bool(web_engine.ALERT_PLAYING),
                        paused=False,
                    )
                else:
                    wait_for_alert_queue(playing=False, paused=False)
            _note_queue_ok()

        except Exception as e:
            logger.error(f"Error in alert queue processor: {str(e)}", exc_info=True)
            _note_queue_error(e)
            time.sleep(1.0)  # Sleep longer on error


def initialize():
    """Initialize the alert processor and web engine"""
    global \
        alert_thread, \
        web_thread, \
        web_engine_instance, \
        alert_queue_active, \
        _initialized

    # Check if already initialized
    if _initialized:
        logger.info(
            "Alert processor already initialized, skipping duplicate initialization"
        )
        return

    logger.info("Initializing alert processor and web engine")

    from .startup_profiler import StartupTimer
    from .alertutils import alert_state_manager

    if alert_state_manager is None:
        logger.info("Alert state manager not initialized, initializing now...")
        initialize_alert_state()
    elif not getattr(alert_state_manager, "_initialized", False):
        logger.info("Alert state manager not initialized, initializing now...")
        initialize_alert_state()
    else:
        # The startup preload already read most collections; read only the
        # rest here. Stored alerts load in the background once the overlay
        # server is up (SU-03 / DBX-01).
        logger.info(
            "Alert state manager already initialized, loading alert paths the startup preload skipped"
        )
        with StartupTimer("alert_processor.load_missing_alert_paths"):
            alert_state_manager.load_missing_alert_paths()

    # Initialize web engine with correct path for templates
    from .path_utils import get_template_path

    template_dir = get_template_path()
    with StartupTimer("alert_processor.web_engine_start"):
        web_engine_instance = web_engine.WebEngine(template_dir=template_dir)

        # Set the global instance in the web_engine module
        web_engine.web_engine_instance = web_engine_instance

        try:
            web_engine_instance.prepare_port_for_startup()
        except Exception as e:
            logger.warning("WebEngine startup port preparation failed: %s", e)

        # Start web engine in a separate thread
        web_thread = threading.Thread(
            target=web_engine_instance.run, daemon=True, name="WebEngine"
        )
        web_thread.start()
        # Track the thread on the instance so the supervisor can detect a crash
        # and restart it.
        web_engine_instance.server_thread = web_thread
        logger.info("Web engine thread started")

    # Readers that need stored alerts before this finishes wait for it.
    try:
        from . import alertutils as _alertutils

        _alertutils.alert_state_manager.prefetch_alert_storage()
    except Exception as e:
        logger.debug("Stored alert prefetch could not start: %s", e)

    # Verify web thread is running
    if web_thread.is_alive():
        logger.info("Web thread status - alive: True")
    else:
        logger.warning("Web thread failed to start")

    # Start the supervisor that auto-restarts the overlay server if its thread
    # exits or the gevent hub freezes (OBS sources / Stream Deck / alerts dead
    # while the main UI keeps working).
    try:
        web_engine_instance.start_supervisor()
    except Exception as e:
        logger.warning("Could not start WebEngine supervisor: %s", e)

    # Start alert queue processor
    logger.info("Starting alert queue processor")
    alert_queue_active = True
    alert_thread = threading.Thread(
        target=alert_queue, daemon=True, name="AlertProcessor"
    )
    alert_thread.start()

    # Verify alert thread is running
    if alert_thread.is_alive():
        logger.info("Alert thread status - alive: True")
    else:
        logger.warning("Alert thread failed to start")

    try:
        from .game_hooks_service import game_hooks_service

        game_hooks_service.start()
        logger.info("Game hooks service started")
    except Exception as e:
        logger.error("Failed to start game hooks service: %s", e, exc_info=True)
        notify_critical(
            "In-game overlays may not work until it starts.",
            source="game_hooks",
            title="Game hooks couldn't start",
            hint="Open Settings → Game Hooks and turn the hook off and on again, or restart Mycelian.",
            details=f"{type(e).__name__}: {e}",
            dedupe_key="game_hooks:start_failed",
            actions=nav_actions_settings("Game Hooks"),
        )

    def _spore_sidecar_migrate() -> None:
        try:
            from .spore_studio import ensure_sidecar_migration

            ensure_sidecar_migration()
        except Exception as e:
            logger.debug("Spore Studio sidecar migration skipped: %s", e)

    threading.Thread(
        target=_spore_sidecar_migrate,
        daemon=True,
        name="SporeSidecarMigrate",
    ).start()

    # Mark as initialized
    _initialized = True
    logger.info("Alert processor initialization completed")


def cleanup():
    """Clean up resources when shutting down"""
    global web_thread, alert_thread, alert_queue_active, _initialized

    logger.debug("Cleaning up alert processor resources")

    try:
        # Pause alerts via web_engine module (where ALERTS_PAUSED is actually defined)
        web_engine.set_alerts_paused(True)
        alert_queue_active = False
        wake_alert_queue()
        logger.debug("Alert queue paused")

        try:
            from .game_hooks_service import game_hooks_service

            game_hooks_service.stop()
            logger.debug("Game hooks service stopped")
        except Exception as e:
            logger.debug("Game hooks service stop: %s", e)

        # Stop web engine (aggressive teardown so port 5000 is released on exit)
        engine = _overlay_engine()
        if engine:
            engine.force_stop()
            logger.debug("Web engine force-stopped")

        # Brief join; threads are daemon and exit once the queue/web server stop.
        if web_thread and web_thread.is_alive():
            web_thread.join(timeout=15.0)
            logger.debug("Web thread joined")

        if engine and engine._port_is_open():
            logger.error(
                "WebEngine port %s still open after shutdown cleanup",
                engine.port,
            )

        if alert_thread and alert_thread.is_alive():
            alert_thread.join(timeout=1)
            logger.debug("Alert thread joined")

        # Reset initialization state
        _initialized = False
        logger.debug("Reset initialization state")

        logger.debug("Alert processor cleanup completed")
    except Exception as e:
        logger.error(f"Error during alert processor cleanup: {str(e)}", exc_info=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    initialize()

    # Keep the main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.debug("Shutting down alert processor...")
        cleanup()

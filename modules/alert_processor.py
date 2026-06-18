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

from . import web_engine
from .alertutils import AlertObj, initialize_alert_state
from .notification_engine import nav_actions_settings, notify_critical

ALERT_QUEUE: list[AlertObj] = []
web_engine_instance = None  # Initialize to None
logger = logging.getLogger(__name__)

# Global thread references
web_thread = None
alert_thread = None

# Global flag to track Alert Queue status
alert_queue_active = False

# Global flag to track initialization status
_initialized = False


def process_alert(alert: AlertObj):
    """
    Process an alert by emitting it over the next_alert websocket

    Args:
        alert (AlertObj): The alert object to process
    """
    try:
        # Convert AlertObj to dictionary for JSON serialization
        alert_data = vars(alert)
        queue_seq = web_engine.assign_next_alert_queue_seq()
        alert_data["queue_seq"] = queue_seq

        # Calculate dynamic timeout based on alert properties
        alert_duration = alert_data.get(
            "duration", 5
        )  # Default to 5 seconds if not specified

        # Check if this alert has audio that might extend completion time
        has_audio = alert_data.get("randomized", False) or (
            alert_data.get("single_audio_dir") and alert_data.get("single_audio_name")
        )

        if alert_data.get("hold_queue_only"):
            timeout_duration = (
                float(alert_duration) + 5.0
            )  # client delay + margin; no A/V in main overlay
            logger.debug(
                "hold_queue_only alert, using extended client-aligned timeout: %ss",
                timeout_duration,
            )
        elif alert_data.get("randomized", False):
            # Randomized alerts can take much longer due to audio completion + buffer
            # Give them a generous timeout: max(duration * 3, duration + 30 seconds for audio)
            timeout_duration = max(alert_duration * 3, alert_duration + 30)
            logger.debug(
                f"Randomized alert detected, using extended timeout: {timeout_duration}s"
            )
        elif has_audio:
            # Non-randomized alerts with audio: wait for duration + audio completion
            timeout_duration = (
                alert_duration + 3
            )  # 15 seconds should be enough for most audio
            logger.debug(
                f"Audio alert detected, using audio-aware timeout: {timeout_duration}s"
            )
        else:
            # Standard timeout for alerts without audio
            timeout_duration = alert_duration + 2
            logger.debug(f"Standard alert timeout: {timeout_duration}s")

        start_time = time.time()

        # Emit the alert data over the websocket
        web_engine_instance.next_alert(alert_data)
        web_engine.ALERT_PLAYING = True

        # Wait for alert completion with timeout protection
        while web_engine.ALERT_PLAYING:
            time.sleep(0.1)
            elapsed_time = time.time() - start_time

            # Check if we've exceeded the timeout
            if elapsed_time >= timeout_duration:
                logger.warning(
                    f"Alert timeout reached ({timeout_duration}s) for alert: {alert.alert_type}. "
                    f"Template may have failed to send alert_complete callback. "
                    f"Randomized: {alert_data.get('randomized', False)}, Has Audio: {has_audio}. "
                    f"Forcing completion to prevent alert queue deadlock."
                )
                web_engine.ALERT_PLAYING = False
                break

        logger.debug(f"Processed alert: {alert_data}")
    except Exception as e:
        logger.error(f"Error processing alert: {str(e)}", exc_info=True)
        notify_critical(
            "An alert failed to process. Check logs if this keeps happening.",
            dedupe_key="alert:process_failed",
            dedupe_cooldown_sec=60.0,
        )


def alert_queue():
    global alert_queue_active
    logger.debug("Starting alert queue processor")
    alert_queue_active = True

    paused_state_logged = False  # Track if we've logged the paused state to avoid spam

    while True:
        try:
            if web_engine.ALERTS_PAUSED:
                if not paused_state_logged:
                    logger.debug("Alert queue is paused - waiting for resume")
                    paused_state_logged = True
                alert_queue_active = False
                time.sleep(0.5)  # Longer sleep when paused
                continue
            else:
                # Reset paused state logging when we're no longer paused
                if paused_state_logged:
                    logger.debug("Alert queue resumed from pause")
                    paused_state_logged = False
                # Ensure queue is marked as active when not paused and we have alerts
                if len(ALERT_QUEUE) > 0:
                    alert_queue_active = True

            # Check for stackable alerts
            processed_stackable = False
            for alert in ALERT_QUEUE[:]:  # Create a copy of the list to iterate through
                if alert.skip_alert:
                    logger.debug(f"Skipping alert: {alert.alert_type}")
                    ALERT_QUEUE.remove(alert)
                    processed_stackable = True
                    continue
                if alert.stackable:
                    logger.debug(f"Processing stackable alert: {alert.alert_type}")
                    ALERT_QUEUE.remove(alert)
                    process_alert(alert)
                    processed_stackable = True

            if web_engine.ALERT_PLAYING:
                time.sleep(0.2)  # Longer sleep when alert is playing
                continue

            alert = next(iter(ALERT_QUEUE), None)
            if alert is not None:
                logger.debug(f"Processing alert from queue: {alert.alert_type}")
                ALERT_QUEUE.remove(alert)
                process_alert(alert)
                alert_queue_active = True
            else:
                # No alerts to process, sleep longer to avoid CPU spinning
                alert_queue_active = len(ALERT_QUEUE) > 0
                time.sleep(0.1 if processed_stackable else 0.5)

        except Exception as e:
            logger.error(f"Error in alert queue processor: {str(e)}", exc_info=True)
            notify_critical(
                "Alert queue hit an error and may be stalled. Check logs.",
                dedupe_key="alert:queue_error",
                dedupe_cooldown_sec=90.0,
            )
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

    # Check if alert state manager is already initialized to avoid duplicate loading
    from .alertutils import alert_state_manager

    if alert_state_manager is None:
        logger.info("Alert state manager not initialized, initializing now...")
        initialize_alert_state()
    elif not getattr(alert_state_manager, "_initialized", False):
        logger.info("Alert state manager not initialized, initializing now...")
        initialize_alert_state()
    else:
        logger.info(
            "Alert state manager already initialized, reloading alerts from Firebase to ensure latest data"
        )
        from .startup_profiler import StartupTimer

        with StartupTimer("alert_processor.reload_from_firebase"):
            alert_state_manager.reload_from_firebase()

    # Initialize web engine with correct path for templates
    from .path_utils import get_template_path

    template_dir = get_template_path()
    web_engine_instance = web_engine.WebEngine(template_dir=template_dir)

    # Set the global instance in the web_engine module
    web_engine.web_engine_instance = web_engine_instance

    # Start web engine in a separate thread
    web_thread = threading.Thread(
        target=web_engine_instance.run, daemon=True, name="WebEngine"
    )
    web_thread.start()
    # Track the thread on the instance so the supervisor can detect a crash
    # and restart it.
    web_engine_instance.server_thread = web_thread
    logger.info("Web engine thread started")

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
            "Game hooks service failed to start. In-game overlays may not work.",
            dedupe_key="game_hooks:start_failed",
            actions=nav_actions_settings("Game Hooks"),
        )

    # Mark as initialized
    _initialized = True
    logger.info("Alert processor initialization completed")


def cleanup():
    """Clean up resources when shutting down"""
    global web_thread, alert_thread, alert_queue_active, _initialized

    logger.debug("Cleaning up alert processor resources")

    try:
        # Pause alerts via web_engine module (where ALERTS_PAUSED is actually defined)
        web_engine.ALERTS_PAUSED = True
        alert_queue_active = False
        logger.debug("Alert queue paused")

        try:
            from .game_hooks_service import game_hooks_service

            game_hooks_service.stop()
            logger.debug("Game hooks service stopped")
        except Exception as e:
            logger.debug("Game hooks service stop: %s", e)

        # Stop web engine if it's running
        if web_engine_instance:
            web_engine_instance.stop()
            logger.debug("Web engine stopped")

        # Brief join; threads are daemon and exit once the queue/web server stop.
        if web_thread and web_thread.is_alive():
            web_thread.join(timeout=2)
            logger.debug("Web thread joined")

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

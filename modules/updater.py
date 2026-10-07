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
import copy
import logging
import os
import threading
import time
from typing import Any, Dict, Optional, Tuple

import aiohttp

from . import update_sync
from .notification_engine import notify
from .ui_timer import app_schedule
from packaging.version import \
    parse as parse_version  # For robust version comparison

logger = logging.getLogger(__name__)

# GitHub Configuration - Update these variables when the repo is created
GITHUB_OWNER = "mushroomsuprise"  # Replace with your GitHub username
GITHUB_REPO = "mycelian"        # Replace with your repository name
GITHUB_API_BASE = "https://api.github.com"

# Shared GitHub releases/latest cache (avoids duplicate startup fetches)
_GITHUB_RELEASE_CACHE_TTL_SEC = 60.0
_github_release_cache_lock = threading.Lock()
_github_release_cache: Optional[Tuple[float, Optional[Dict[str, Any]]]] = None

# Ensure 'packaging' and 'aiohttp' libraries are installed: pip install packaging aiohttp

def _compare_versions(current_v_str: str, new_v_str: str) -> bool:
    """
    Compares two version strings using packaging.version.
    Returns True if new_v_str is greater than current_v_str, False otherwise.
    Handles potential errors during parsing.
    """
    try:
        # packaging.version.parse can handle various version formats like 1.0.0, 1.0.1-beta, etc.
        return parse_version(new_v_str) > parse_version(current_v_str)
    except Exception as e: # More specific: packaging.version.InvalidVersion
        logger.error(f"Error comparing versions '{current_v_str}' and '{new_v_str}': {e}", exc_info=True)
        return False

async def fetch_latest_update_info_from_github(*, force_refresh: bool = False):
    """
    Fetches the latest update information from GitHub releases API asynchronously.
    
    Returns:
        dict: A dictionary with 'latest_version', 'tag_name', 'release_notes',
              and 'release_url', or None if data is not found, invalid, or an error occurs.
    """
    global _github_release_cache

    if not force_refresh:
        now = time.monotonic()
        with _github_release_cache_lock:
            if _github_release_cache is not None:
                cached_at, cached_value = _github_release_cache
                if now - cached_at < _GITHUB_RELEASE_CACHE_TTL_SEC:
                    if cached_value is None:
                        return None
                    return copy.deepcopy(cached_value)

    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

    cached_fallback: Optional[Dict[str, Any]] = None
    with _github_release_cache_lock:
        if _github_release_cache is not None:
            _, cached_value = _github_release_cache
            if cached_value is not None:
                cached_fallback = copy.deepcopy(cached_value)

    result: Optional[Dict[str, Any]] = None
    try:
        # Set a reasonable timeout to prevent hanging
        timeout = aiohttp.ClientTimeout(total=15.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Extract version from tag_name (remove 'v' prefix if present)
                    tag_name = data.get("tag_name", "")
                    latest_version = tag_name.lstrip("v")
                    release_notes = data.get("body", "")
                    release_page_url = data.get("html_url", "")

                    if latest_version:
                        logger.info(f"Successfully fetched update info from GitHub: version {latest_version}")
                        result = {
                            "latest_version": latest_version,
                            "tag_name": tag_name,
                            "release_notes": release_notes,
                            "release_url": release_page_url,
                        }
                    else:
                        logger.warning(f"No valid version found in GitHub release data. Tag name: {tag_name}")
                        
                elif response.status == 404:
                    logger.warning(f"GitHub repository '{GITHUB_OWNER}/{GITHUB_REPO}' not found or has no releases")
                else:
                    logger.error(f"GitHub API request failed with status {response.status}: {await response.text()}")
                    
    except (asyncio.TimeoutError, aiohttp.ServerTimeoutError) as e:
        logger.warning(
            "Timed out fetching update info from GitHub after 15s: %s", e
        )
        if cached_fallback is not None:
            return cached_fallback
    except aiohttp.ClientError as e:
        logger.error(f"Network error while fetching update info from GitHub: {e}", exc_info=True)
    except Exception as e:
        if isinstance(e, asyncio.CancelledError):
            logger.warning("GitHub update check cancelled or timed out")
            if cached_fallback is not None:
                return cached_fallback
        else:
            logger.error(f"Unexpected error fetching update info from GitHub: {e}", exc_info=True)
    finally:
        with _github_release_cache_lock:
            if result is not None:
                _github_release_cache = (time.monotonic(), result)
            elif cached_fallback is None:
                _github_release_cache = (time.monotonic(), None)

    if result is None:
        return None
    return copy.deepcopy(result)

async def check_for_updates(current_app_version: str, *, force_refresh: bool = False):
    """
    Checks if a newer version of the application is available on GitHub.

    Args:
        current_app_version (str): The current version of the running application.

    Returns:
        dict: Release info when a newer version is available, otherwise None.
              File sync uses ``tag_name`` and does not download an installer.
    """
    logger.info(f"Checking for updates on GitHub. Current application version: {current_app_version}")
    update_info = await fetch_latest_update_info_from_github(force_refresh=force_refresh)

    if update_info:
        latest_version = update_info.get("latest_version")
        # Ensure latest_version is not None or empty before comparison
        if latest_version and _compare_versions(current_app_version, latest_version):
            logger.info(f"A new version '{latest_version}' is available (current: '{current_app_version}').")
            return update_info
        else:
            if latest_version:
                logger.info(f"Current version '{current_app_version}' is up to date. Latest available: '{latest_version}'.")

    return None

def finish_update_and_exit(helper_pid: int) -> None:
    """Keep the apply helper alive, then exit so it can replace files and relaunch."""
    if not isinstance(helper_pid, int) or helper_pid <= 0:
        raise update_sync.UpdateSyncError("The update helper did not start.")
    from .shutdown import protect_child_process_trees

    protect_child_process_trees([helper_pid])
    _force_application_exit()


def _force_application_exit():
    """
    Force immediate application exit with proper cleanup for NiceGUI/pywebview apps.
    """
    try:
        try:
            from .shutdown import reap_child_process_trees

            reap_child_process_trees()
        except Exception as e:
            logger.debug(f"Could not reap child processes: {e}")

        # Special handling for NiceGUI with pywebview
        try:
            # Try to get NiceGUI app instance and close it properly
            from nicegui import app
            if hasattr(app, 'native') and hasattr(app.native, 'main_window'):
                # Close the native window if it exists
                if app.native.main_window:
                    app.native.main_window.destroy()
                    logger.info("Closed NiceGUI native window")
        except Exception as e:
            logger.debug(f"Could not close NiceGUI window: {e}")
        
        # Try to stop pywebview windows
        try:
            import webview

            # Get all active windows and close them
            for window in webview.windows:
                if hasattr(window, 'destroy'):
                    window.destroy()
                    logger.info("Closed pywebview window")
        except Exception as e:
            logger.debug(f"Could not close pywebview windows: {e}")
        
        # Try to cleanup asyncio event loop if it exists
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule the exit in the event loop
                loop.call_soon_threadsafe(lambda: loop.stop())
                # Give it a moment to stop
                import time
                time.sleep(0.5)
        except RuntimeError:
            pass  # No event loop running
        
        # Clean up any remaining NiceGUI resources
        try:
            from nicegui import app
            if hasattr(app, 'shutdown'):
                app.shutdown()
                logger.info("Executed NiceGUI app shutdown")
        except Exception as e:
            logger.debug(f"Could not shutdown NiceGUI app: {e}")

        try:
            from .shutdown import reap_child_process_trees

            reap_child_process_trees()
        except Exception as e:
            logger.debug(f"Could not reap child processes: {e}")

        # Force immediate exit
        logger.info("Forcing application exit")
        os._exit(0)
        
    except Exception as e:
        logger.error(f"Error during application exit: {e}")
        try:
            from .shutdown import reap_child_process_trees

            reap_child_process_trees()
        except Exception:
            pass
        # Fallback to hard exit
        os._exit(1) 


# -----------------------------
# Enhanced utilities for UpdateManager
# -----------------------------

def _format_speed(bytes_per_second: float) -> str:
    try:
        if bytes_per_second <= 0:
            return "0 B/s"
        units = ["B/s", "KB/s", "MB/s", "GB/s"]
        index = 0
        value = float(bytes_per_second)
        while value >= 1024.0 and index < len(units) - 1:
            value /= 1024.0
            index += 1
        if index <= 1:
            return f"{value:.0f} {units[index]}"
        return f"{value:.2f} {units[index]}"
    except Exception:
        return "N/A"


# -----------------------------
# Update Manager (central coordinator)
# -----------------------------

# First automatic check: settle after UI is up, then a short pause before hitting GitHub.
STARTUP_SETTLE_SECONDS = 10.0
PRE_CHECK_DELAY_SECONDS = 3.0
# Start periodic timer scheduling shortly after on_ui_ready (reads settings from state).
PERIODIC_SCHEDULE_DELAY_SECONDS = 3.0


def _connected_client():
    """Return a NiceGUI client that can actually show a dialog, or None.

    While Mycelian is minimised to the tray the webview sits on a blank page, so there
    is no client and anything that builds UI elements has to be deferred.
    """
    try:
        from nicegui import Client

        for client in list(Client.instances.values()):
            if getattr(client, "is_deleted", False):
                continue
            if getattr(client, "has_socket_connection", False):
                return client
    except Exception as e:
        logger.debug("UpdateManager: could not resolve a connected client: %s", e)
    return None


class UpdateManager:
    """
    Centralized manager for update checks, UI prompts, and installation flow.
    """

    def __init__(self) -> None:
        self._initial_timer = None
        self._periodic_timer = None
        self._periodic_schedule_timer = None
        self._check_running = False
        self._dialog_open = False
        self._session_suppressed = False  # set when user declines an automatic update (session only)
        self._ui_ready_scheduled = False
        self._automatic_startup_check_done = False
        self._pending_prompt: Optional[Dict[str, Any]] = None

    def _dialog_is_open(self) -> bool:
        """Treat a missing/tray-unloaded client as the dialog no longer being open."""
        if not self._dialog_open:
            return False
        if _connected_client() is None:
            self._dialog_open = False
            return False
        return True

    # ---------------- Scheduling ----------------
    def on_ui_ready(self) -> None:
        """Schedule initial and periodic checks once the server is up.

        Every timer here uses ``app_schedule``. Client-bound timers die with the
        NiceGUI websocket, which is exactly what happens when the app is minimised to
        the tray, and update checks have to keep running there.
        """
        if self._ui_ready_scheduled:
            logger.debug("UpdateManager.on_ui_ready already scheduled; skipping")
            return
        self._ui_ready_scheduled = True

        try:
            def _after_settle_schedule_pre_check() -> None:
                app_schedule(PRE_CHECK_DELAY_SECONDS, self._run_initial_check, once=True)

            self._initial_timer = app_schedule(
                STARTUP_SETTLE_SECONDS,
                _after_settle_schedule_pre_check,
                once=True,
            )
            if self._periodic_schedule_timer is None:
                self._periodic_schedule_timer = app_schedule(
                    PERIODIC_SCHEDULE_DELAY_SECONDS,
                    self.reschedule_periodic_timer,
                    once=True,
                )
        except Exception as e:
            logger.error(f"UpdateManager.on_ui_ready error: {e}", exc_info=True)
            self._ui_ready_scheduled = False

    def _run_initial_check(self) -> None:
        if self._session_suppressed:
            logger.info("UpdateManager: session suppressed; skipping initial check")
            return
        self.trigger_check_and_prompt()

    def reschedule_periodic_timer(self) -> None:
        try:
            from nicegui import app

            from . import dataobjects

            settings = dataobjects.state_manager.get_app_settings()
            if not getattr(settings, "auto_update", False) or self._session_suppressed:
                logger.info("UpdateManager: auto-update disabled; stopping periodic checks")
                if self._periodic_timer:
                    try:
                        self._periodic_timer.deactivate()
                    except Exception:
                        pass
                    self._periodic_timer = None
                return

            interval_minutes = max(5, min(120, int(getattr(settings, "update_check_interval_minutes", 30))))
            interval_seconds = float(interval_minutes) * 60.0

            if self._periodic_timer:
                try:
                    self._periodic_timer.deactivate()
                except Exception:
                    pass
                self._periodic_timer = None

            self._periodic_timer = app_schedule(interval_seconds, self._periodic_check)
            setattr(app, "update_check_timer", self._periodic_timer)
            logger.info(f"UpdateManager: scheduled periodic checks every {interval_minutes} minutes")
        except Exception as e:
            logger.error(f"UpdateManager.reschedule_periodic_timer failed: {e}", exc_info=True)

    def _periodic_check(self) -> None:
        if self._session_suppressed:
            logger.info("UpdateManager: session suppressed; skipping periodic check")
            return
        if not self._automatic_startup_check_done:
            logger.debug(
                "UpdateManager: skipping periodic check until startup check completes"
            )
            return
        self.trigger_check_and_prompt()

    # ---------------- Public triggers ----------------
    def trigger_manual_check(self) -> None:
        """Manual check initiated by user via settings button."""
        self.trigger_check_and_prompt(manual=True)

    def trigger_check_and_prompt(self, manual: bool = False) -> None:
        logger.info("UpdateManager: update check requested (manual=%s)", manual)
        if self._check_running:
            logger.info("UpdateManager: check already running; skipping")
            return
        if self._dialog_is_open():
            logger.info("UpdateManager: update dialog already open; skipping")
            return
        try:
            from nicegui import ui

            from . import database_manager, dataobjects

            if not database_manager.database_manager._initialized:
                logger.warning("UpdateManager: database not initialized; skipping check")
                return

            settings = dataobjects.state_manager.get_app_settings()
            if not manual and not getattr(settings, "auto_update", False):
                logger.info("UpdateManager: auto-update disabled; skipping automatic check")
                return

            current_app_version = settings.version
            if not current_app_version:
                logger.warning("UpdateManager: unknown current version; skipping check")
                return

            self._check_running = True

            if manual:
                notify("Checking for updates...", type="info", timeout=2000)

            result_holder: Dict[str, Any] = {"completed": False, "update_info": None, "error": None}

            def worker():
                try:
                    result_holder["update_info"] = asyncio.run(
                        check_for_updates(
                            current_app_version, force_refresh=manual
                        )
                    )
                except Exception as e:
                    logger.error(f"UpdateManager: error in async check: {e}", exc_info=True)
                    result_holder["error"] = str(e)
                finally:
                    result_holder["completed"] = True

            import threading

            t = threading.Thread(target=worker, daemon=True)
            t.start()

            poll_counter = {"count": 0}
            max_polls = 150  # ~30s at 0.2s interval

            def poll_result():
                try:
                    poll_counter["count"] += 1
                    if poll_counter["count"] >= max_polls:
                        logger.warning("UpdateManager: update check timed out")
                        if not manual:
                            self._automatic_startup_check_done = True
                        self._check_running = False
                        return False

                    if not result_holder["completed"]:
                        return True

                    if result_holder["error"]:
                        if manual:
                            notify(f"Error checking for updates: {result_holder['error']}", type="negative")
                        elif not manual:
                            self._automatic_startup_check_done = True
                        self._check_running = False
                        return False

                    update_info = result_holder["update_info"]
                    if update_info and (manual or not self._session_suppressed):
                        self._show_update_modal(update_info, manual=manual)
                    else:
                        if manual:
                            notify("You are running the latest version!", type="positive", timeout=3000)
                    if not manual:
                        self._automatic_startup_check_done = True
                    self._check_running = False
                    return False
                except Exception as e:
                    logger.error(f"UpdateManager: poll_result error: {e}", exc_info=True)
                    self._check_running = False
                    return False

            # Create a timer that will keep checking until poll_result returns False
            update_timer = app_schedule(0.2, poll_result)
            
            # Add a safety mechanism to ensure the timer stops after completion
            def ensure_timer_stops():
                if result_holder["completed"] and update_timer:
                    try:
                        update_timer.deactivate()
                        logger.debug("UpdateManager: Ensured update check timer is stopped")
                    except Exception as e:
                        logger.debug(f"UpdateManager: Error stopping timer: {e}")
                    return False
                return not result_holder["completed"]
            
            # Safety timer that runs for longer intervals to ensure cleanup
            app_schedule(1.0, ensure_timer_stops)
        except Exception as e:
            logger.error(f"UpdateManager.trigger_check_and_prompt error: {e}", exc_info=True)
            self._check_running = False

    # ---------------- Modal and download flow ----------------
    def _show_update_modal(self, update_info: Dict[str, Any], *, manual: bool = False) -> None:
        """Prompt for an update, or fall back to a desktop notification.

        Checks now run on client-independent timers, so there may be no UI on screen
        when one finds a release. In that case the user gets an OS notification and the
        prompt is held until the window comes back.
        """
        client = _connected_client()
        if client is None:
            self._defer_update_prompt(update_info)
            return

        try:
            # app_schedule timers carry no client context, so enter one explicitly
            # before building elements.
            with client:
                self._build_update_modal(update_info, manual=manual)
        except Exception as e:
            logger.error(f"UpdateManager._show_update_modal error: {e}", exc_info=True)

    def _defer_update_prompt(self, update_info: Dict[str, Any]) -> None:
        """Hold an update prompt until a window is available, and notify the desktop."""
        if self._pending_prompt is not None:
            return
        self._pending_prompt = update_info

        version = update_info.get("latest_version", "?")
        logger.info("UpdateManager: no UI on screen; deferring prompt for v%s", version)

        from .system_notify import notify_async

        notify_async(
            f"Mycelian {version} is available. Open Mycelian to update.",
            title="Update available",
        )
        try:
            from .tray_controller import set_tray_update_available

            set_tray_update_available(True)
        except Exception:
            pass

    def flush_pending_prompt(self) -> None:
        """Show a deferred update prompt now that the UI is back."""
        update_info = self._pending_prompt
        if update_info is None:
            return
        try:
            from .tray_controller import set_tray_update_available

            set_tray_update_available(False)
        except Exception:
            pass

        # The window has only just been told to reload, so poll for its socket rather
        # than assuming a client is already there.
        state: Dict[str, Any] = {"timer": None, "attempts": 0}
        max_attempts = 30

        def _stop() -> None:
            timer = state.get("timer")
            if timer is not None:
                try:
                    timer.deactivate()
                except Exception:
                    pass
                state["timer"] = None

        def _show() -> None:
            state["attempts"] += 1
            client = _connected_client()
            if client is None:
                if state["attempts"] >= max_attempts:
                    logger.info("UpdateManager: gave up waiting for a UI to prompt on")
                    _stop()
                return
            _stop()
            self._pending_prompt = None
            try:
                with client:
                    self._build_update_modal(update_info, manual=False)
            except Exception as e:
                logger.error(f"UpdateManager.flush_pending_prompt error: {e}", exc_info=True)

        state["timer"] = app_schedule(1.0, _show)

    def _build_update_modal(self, update_info: Dict[str, Any], *, manual: bool = False) -> None:
        try:
            from nicegui import ui

            if self._dialog_open:
                logger.info("UpdateManager: update dialog already open; skipping")
                return

            version = update_info.get("latest_version", "?")
            tag_name = str(update_info.get("tag_name") or "").strip()
            if not tag_name and version and version != "?":
                tag_name = version if str(version).startswith("v") else f"v{version}"
            release_notes = update_info.get("release_notes", "")
            release_url = update_info.get("release_url", "")

            self._dialog_open = True

            update_dialog = ui.dialog().props("persistent")
            with update_dialog:
                card = ui.card().classes("w-[500px] p-6").style(
                    "background: linear-gradient(135deg, #2a1d4a 0%, #1a1a2e 100%);"
                    "color: white; border-radius: 12px; border: 1px solid rgba(115, 0, 255, 0.3);"
                    "box-shadow: 0 8px 32px rgba(115, 0, 255, 0.2);"
                )
                with card:
                    title_label = ui.label(f"🚀 New Version Available: {version}").classes("text-h5 font-bold mb-4").style("color: #b980ff;")
                    content_area = ui.column().classes("w-full gap-3")
                    with content_area:
                        if release_url:
                            ui.link("View release on GitHub", release_url).classes("text-sm underline").style("color: #7c3aed;")
                        if release_notes:
                            ui.label("📋 What's new:").classes("text-sm font-semibold mt-2").style("color: #e2e8f0;")
                            # Create scrollable area for release notes
                            with ui.scroll_area().classes("w-full").style("max-height: 150px; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 8px; background: rgba(0,0,0,0.2);"):
                                ui.html(release_notes.replace("\n", "<br>")).classes("text-sm").style("color: #cbd5e1; line-height: 1.4;")

                    progress_area = ui.column().classes("w-full gap-3").style("display: none;")
                    with progress_area:
                        progress_label = ui.label("Preparing update...").classes("text-sm").style("color: #e2e8f0;")
                        progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full").style("height: 8px;")
                        speed_label = ui.label("").classes("text-xs").style("color: #94a3b8;")

                    button_row = ui.row().classes("w-full justify-end gap-2 mt-4")
                    cancel_event = threading.Event()

                    def on_decline():
                        try:
                            if not manual:
                                self._session_suppressed = True
                                self._cancel_periodic()
                        finally:
                            self._dialog_open = False
                            update_dialog.close()

                    def on_close():
                        self._dialog_open = False
                        update_dialog.close()

                    def on_accept():
                        try:
                            if not update_sync.can_apply_updates():
                                notify(
                                    "This checkout is running from source. "
                                    "Install a packaged build to apply updates.",
                                    type="warning",
                                )
                                return
                            content_area.style("display: none;")
                            progress_area.style("display: block;")
                            try:
                                decline_btn.style("display: none;")
                                update_btn.style("display: none;")
                                cancel_btn.style("display: inline-flex;")
                            except Exception:
                                pass

                            self._start_sync_flow(
                                tag_name,
                                title_label,
                                progress_label,
                                progress_bar,
                                speed_label,
                                cancel_btn,
                                close_btn,
                                cancel_event=cancel_event,
                            )
                        except Exception as e:
                            logger.error(f"Error starting update: {e}", exc_info=True)

                    with button_row:
                        decline_btn = ui.button("Decline", on_click=on_decline).props("flat").classes("secondary-text").style("border: 1px solid rgba(255,255,255,0.2);")
                        update_btn = ui.button("Update", on_click=on_accept).props("unelevated").classes("font-semibold").style("background: linear-gradient(135deg, #7c3aed 0%, #b980ff 100%); color: white; border: none;")
                        cancel_btn = ui.button(
                            "Cancel",
                            on_click=lambda: (
                                cancel_event.set(),
                                progress_label.set_text("Cancelling update..."),
                            ),
                        ).props("flat").classes("secondary-text").style(
                            "border: 1px solid rgba(255,255,255,0.2); display: none;"
                        )
                        close_btn = ui.button(
                            "Close",
                            on_click=on_close,
                        ).props("flat").classes("secondary-text").style(
                            "border: 1px solid rgba(255,255,255,0.2); display: none;"
                        )

            update_dialog.open()
        except Exception as e:
            self._dialog_open = False
            logger.error(f"UpdateManager._show_update_modal error: {e}", exc_info=True)

    def _start_sync_flow(
        self,
        tag_name: str,
        title_label,
        progress_label,
        progress_bar,
        speed_label,
        cancel_btn,
        close_btn,
        cancel_event: Optional[threading.Event] = None,
    ) -> None:
        try:
            download_state: Dict[str, Any] = {
                "status": "working",
                "phase": "listing",
                "progress": 0.0,
                "speed_bps": 0.0,
                "label": "Reading the release file list...",
                "error": None,
                "helper_pid": None,
                "done": False,
            }

            def worker():
                try:
                    helper_pid = asyncio.run(
                        update_sync.sync_release(
                            tag=tag_name,
                            owner=GITHUB_OWNER,
                            repo=GITHUB_REPO,
                            state=download_state,
                            cancel_event=cancel_event,
                        )
                    )
                    if helper_pid:
                        download_state["helper_pid"] = helper_pid
                        download_state["status"] = "ready"
                    elif cancel_event is not None and cancel_event.is_set():
                        download_state["status"] = "cancelled"
                        download_state["error"] = "Update cancelled."
                    else:
                        download_state["status"] = "current"
                except update_sync.UpdateCancelled as exc:
                    download_state["status"] = "cancelled"
                    download_state["error"] = str(exc)
                except Exception as e:
                    logger.error(f"UpdateManager sync worker error: {e}", exc_info=True)
                    download_state["status"] = "failed"
                    download_state["error"] = str(e)
                finally:
                    download_state["done"] = True

            poll_holder: Dict[str, Any] = {"timer": None}

            def _stop_poll() -> None:
                timer = poll_holder.get("timer")
                if timer is None:
                    return
                try:
                    timer.active = False
                except Exception:
                    pass
                try:
                    timer.deactivate()
                except Exception:
                    pass
                poll_holder["timer"] = None

            def _widget_gone(el) -> bool:
                return el is None or getattr(el, "is_deleted", False)

            def _show_close() -> None:
                try:
                    cancel_btn.style("display: none;")
                    close_btn.style("display: inline-flex;")
                except Exception:
                    pass

            def poll_update():
                try:
                    widgets_dead = (
                        _widget_gone(progress_bar)
                        or _widget_gone(progress_label)
                        or _widget_gone(title_label)
                    )
                    if not download_state["done"]:
                        if _connected_client() is None or widgets_dead:
                            return
                        progress_bar.set_value(float(download_state.get("progress") or 0.0))
                        progress_label.set_text(
                            download_state.get("label") or "Updating..."
                        )
                        speed_bps = float(download_state.get("speed_bps") or 0.0)
                        if download_state.get("phase") == "downloading" and speed_bps > 0:
                            speed_label.set_text(f"Speed: {_format_speed(speed_bps)}")
                        else:
                            speed_label.set_text("")
                        return

                    status = download_state["status"]
                    if status in ("failed", "cancelled", "current"):
                        if not widgets_dead:
                            try:
                                if status == "cancelled":
                                    title_label.set_text("Update cancelled")
                                elif status == "current":
                                    title_label.set_text("Already up to date")
                                else:
                                    title_label.set_text("Update failed")
                                progress_label.set_text(
                                    download_state.get("error")
                                    or download_state.get("label")
                                    or "Unknown error"
                                )
                                speed_label.set_text("")
                                progress_bar.set_value(0.0 if status != "current" else 1.0)
                            except Exception:
                                pass
                            _show_close()
                        else:
                            self._dialog_open = False
                        _stop_poll()
                        return

                    if status == "ready":
                        if not widgets_dead:
                            try:
                                title_label.set_text("Restarting Mycelian")
                                progress_label.set_text(
                                    "Closing Mycelian to finish the update..."
                                )
                                speed_label.set_text("")
                                progress_bar.set_value(1.0)
                                cancel_btn.style("display: none;")
                            except Exception:
                                pass

                        def launch():
                            try:
                                finish_update_and_exit(download_state.get("helper_pid"))
                                self._dialog_open = False
                            except Exception as e:
                                logger.error(f"Error restarting after update: {e}", exc_info=True)
                                try:
                                    title_label.set_text("Update failed")
                                    progress_label.set_text(str(e))
                                    _show_close()
                                except Exception:
                                    self._dialog_open = False

                        app_schedule(0.6, launch, once=True)
                        _stop_poll()
                        return

                    _stop_poll()
                except Exception as e:
                    logger.error(f"UpdateManager.poll_update error: {e}")
                    if not download_state["done"]:
                        return
                    _stop_poll()

            t = threading.Thread(target=worker, daemon=True)
            t.start()
            poll_holder["timer"] = app_schedule(0.2, poll_update)
        except Exception as e:
            logger.error(f"UpdateManager._start_sync_flow error: {e}", exc_info=True)

    def _cancel_periodic(self) -> None:
        if self._periodic_timer:
            try:
                self._periodic_timer.deactivate()
            except Exception:
                pass
            self._periodic_timer = None


# Shared singleton instance
update_manager = UpdateManager()

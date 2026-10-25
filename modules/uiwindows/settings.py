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
import time
import webbrowser
from datetime import datetime
from typing import Dict, List, Optional

import aiohttp
from nicegui import run, ui
from ..notification_engine import nav_actions_settings, notify, notify_progress
from ..ui_timer import layout_schedule

from .. import dataobjects
from ..dataobjects import state_manager
from ..build_info import resolve_build_number
from ..log_parser import get_actionable_errors, get_log_dir
from ..path_utils import reveal_in_file_manager
from ..startup_profiler import StartupTimer, log_startup_summary
from ..ui_form_controls import form_input, form_number, form_select
from ..ui_settings_layout import settings_header, settings_section, settings_surface
from .service_brand_icons import SERVICE_BRAND_SVG, service_tab_icon

# from ..psnapi import PSNClient # No longer needed here for status display

logger = logging.getLogger(__name__)

# CSS for settings-specific styling - shared card/section rules live in ui_styles.py
CSS = """
.header-section {
    padding: 1rem;
    margin-bottom: 1rem;
    border-radius: 8px;
    background: var(--color-primary-light);
    border-left: 4px solid var(--color-primary);
}

.settings-header {
    font-size: 1.25rem;
    font-weight: 600;
    color: var(--color-text-primary);
    margin-bottom: 0.5rem;
}

.settings-description {
    font-size: 0.9rem;
    color: var(--color-text-secondary);
    margin-bottom: 1rem;
}

.secondary-text {
    font-size: 0.85rem;
    color: var(--color-text-muted);
}

.settings-card {
    transition: all 0.3s ease;
    background: var(--color-bg-surface);
}

.settings-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 8px var(--color-bg-overlay);
}

/* Flat cards inside Settings subtabs (no hover lift/glow) */
.mycelian-sub-tab-shell .q-card,
.mycelian-sub-tab-shell .content-section,
.mycelian-sub-tab-shell .settings-card,
.mycelian-sub-tab-shell .statistics-section,
.mycelian-sub-tab-shell .statistics-metric-card {
    box-shadow: none !important;
    filter: none !important;
}

.mycelian-sub-tab-shell .settings-card:hover,
.mycelian-sub-tab-shell .q-card:hover,
.mycelian-sub-tab-shell .content-section:hover,
.mycelian-sub-tab-shell .statistics-section:hover,
.mycelian-sub-tab-shell .statistics-metric-card:hover {
    transform: none !important;
    box-shadow: none !important;
    filter: none !important;
}

.button-row {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-top: 1rem;
}

.settings-group {
    margin-bottom: 1.5rem;
}

.divider {
    margin: 1.5rem 0;
    border-top: 1px solid var(--color-border-accent);
}

.tab-content {
    padding: 1rem;
    min-height: unset;
    width: 100%;
    max-width: 100%;
    background: var(--color-bg-elevated);
    box-sizing: border-box;
}

.settings-tab-surface {
    flex: 0 0 auto !important;
    width: 100% !important;
    max-width: 100% !important;
    height: auto !important;
    max-height: none !important;
    box-sizing: border-box;
}

.settings-status-band {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1.25rem;
    align-items: center;
}

.settings-form-grid {
    width: 100%;
}

.settings-form-grid > * {
    min-width: 0;
}

.settings-section {
    margin-top: 0.25rem;
}

.settings-divider {
    margin: 0.75rem 0;
}

.settings-toolbar {
    gap: 0.5rem;
}
"""


class SettingsUI:
    """Settings UI component for managing application settings and Twitch data."""

    def __init__(self):
        self.ui_elements = {}
        self.last_saved_values = {}
        self.is_initialized = False
        self.twitch_data: Optional[dataobjects.TwitchData] = None
        self.app_settings: Optional[dataobjects.AppSettings] = None
        self.psn_settings_data: Optional[dataobjects.PSNSettingsData] = None
        self.spotify_data: Optional[dataobjects.SpotifyData] = None
        self.database_settings: Optional[dataobjects.DatabaseSettings] = None
        self.api_credentials: Optional[object] = None  # Will hold APICredentials

        # Active timers tracking to prevent duplicates and enable cleanup
        self.active_timers = {}
        self._active_timers = []  # List for storing timer references for cleanup

        self.oauth_in_progress = {
            "spotify": False,
            "twitch": False,
        }

        # Defer PSN initialization until the PSN tab is actually accessed
        self._psn_initialized = False

        # Modular per-tab components
        self._tabs_by_name = {}
        self._active_tab_name = "Twitch"
        self._unsaved_dialog_open = False
        self._settings_switch_guard = False
        self._accepted_settings_tab = "Twitch"
        self._settings_jump = None
        self._jump_sync = False

    # Removed the old initialize method - initialization is now done inline in build_ui()

    @staticmethod
    def _format_source_url_title(name: str) -> str:
        return name.replace("_", " ").title()

    def open_logs_folder(self) -> None:
        """Open the folder containing mycelian.log in the native file manager."""
        log_dir = ""
        try:
            log_dir = str(get_log_dir())
            reveal_in_file_manager(log_dir)
        except Exception as e:
            logger.error(f"Error opening logs folder: {e}", exc_info=True)
            notify(
                f"You can find it at {log_dir}." if log_dir else "",
                type="error",
                source="files",
                title="Couldn't open the logs folder",
                hint="Open the folder yourself in your file manager. Copy the path with the button below.",
                details=f"{log_dir}\n{e}" if log_dir else str(e),
                actions=[{"kind": "copy", "text": log_dir, "label": "Copy path"}] if log_dir else None,
            )

    def _copy_url_to_clipboard(self, url: str, notice: Optional[str] = None):
        """Copy text (usually a URL) to the clipboard and confirm it with a short notification."""
        from ..notification_engine import copy_text_to_clipboard

        try:
            copied = copy_text_to_clipboard(url)
        except Exception as e:  # pragma: no cover - copy_text_to_clipboard already catches
            logger.error(f"Error copying to clipboard: {e}", exc_info=True)
            copied = False
        if copied:
            notify("", type="success", source="app", title=notice or "Link copied")
            return
        notify(
            "",
            type="warning",
            source="app",
            title="Couldn't copy to the clipboard",
            hint="Select the text and press Ctrl+C (Cmd+C on Mac).",
        )

    def _prompt_add_source_to_obs(self, url_info: dict) -> None:
        """Open the add-to-OBS dialog, or notify when WebSocket is unavailable."""
        from ..obs_service import obs_service

        if not obs_service.is_enabled():
            notify(
                "Mycelian needs the OBS WebSocket connection to add sources.",
                type="warning",
                source="obs",
                title="OBS WebSocket is turned off",
                hint="Turn it on in Settings → OBS, then try again.",
                actions=nav_actions_settings("OBS"),
            )
            return
        if not obs_service.is_connected():
            self._notify_obs_not_connected()
            return

        async def _open() -> None:
            ok, err = await run.io_bound(obs_service.refresh_snapshot_blocking, 12.0)
            if not obs_service.is_connected():
                self._notify_obs_not_connected()
                return
            if not ok:
                notify(
                    "OBS may be busy.",
                    type="error",
                    source="obs",
                    title="Couldn't get the scene list from OBS",
                    hint="Try again in a moment. If it keeps failing, test the connection in Settings → OBS.",
                    details=str(err or "Timed out waiting for OBS"),
                    actions=nav_actions_settings("OBS"),
                )
                return
            snap = obs_service.get_connector_snapshot()
            scenes = [
                str(name)
                for name in (snap.get("scene_names") or [])
                if str(name).strip()
            ]
            if not scenes:
                notify(
                    "",
                    type="warning",
                    source="obs",
                    title="OBS has no scenes yet",
                    hint="Create a scene in OBS, then try again.",
                )
                return
            scan_ok, scan = await run.io_bound(
                obs_service.list_scene_browser_urls_blocking, 12.0
            )
            if not scan_ok:
                scan = {
                    "taken_names": list(snap.get("input_names") or []) + list(scenes),
                    "urls_by_scene": {},
                }
            self._show_add_to_obs_dialog(url_info, snap, scenes, scan)

        layout_schedule(0, _open, once=True)

    @staticmethod
    def _notify_obs_not_connected() -> None:
        notify(
            "",
            type="warning",
            source="obs",
            title="OBS isn't connected",
            hint="Open OBS, check WebSocket is on (Tools → WebSocket Server Settings), then try again.",
            actions=nav_actions_settings("OBS"),
        )

    def _show_add_to_obs_dialog(
        self, url_info: dict, snap: dict, scenes: List[str], scan: dict
    ) -> None:
        """Scene, source name, resolution, and the four OBS browser-source options."""
        from functools import partial

        from ..obs_browser_source_match import scene_has_template_url
        from ..obs_service import adjusted_obs_input_name, obs_service

        title = url_info.get("title") or self._format_source_url_title(
            str(url_info.get("name") or "")
        )
        url = str(url_info.get("url") or "")
        current = str(snap.get("current_program_scene_name") or "")
        selected = current if current in scenes else scenes[0]
        scene_options = {name: name for name in scenes}
        taken_names = [
            str(name)
            for name in (scan.get("taken_names") or [])
            if str(name).strip()
        ]
        if not taken_names:
            taken_names = [
                str(name)
                for name in (snap.get("input_names") or [])
                if str(name).strip()
            ]
            taken_names.extend(scenes)
        urls_by_scene = scan.get("urls_by_scene") or {}
        default_name, _default_reason = adjusted_obs_input_name(title, taken_names)
        name_adjustment = {
            "from": title if default_name != title else "",
            "to": default_name if default_name != title else "",
        }

        with ui.dialog() as dialog, ui.card().classes("w-[420px] max-w-[92vw] p-4"):
            ui.label("Add to OBS").classes("text-lg font-bold mb-1")
            ui.label(title).classes("secondary-text text-sm mb-3")

            scene_select = form_select(
                tooltip="Scene to add the browser source to",
                options=scene_options,
                label="Scene",
                value=selected,
            ).classes("w-full")
            scene_warn = ui.label("").classes(
                "text-theme-warning text-sm break-words mt-1 mb-2"
            )

            def _sync_scene_warning(*_args: object) -> None:
                scene_name = str(scene_select.value or "").strip()
                urls = urls_by_scene.get(scene_name) or []
                if scene_name and scene_has_template_url(urls, url):
                    scene_warn.set_text(
                        f'This template is already a browser source on "{scene_name}".'
                    )
                    scene_warn.set_visibility(True)
                else:
                    scene_warn.set_text("")
                    scene_warn.set_visibility(False)

            scene_select.on_value_change(_sync_scene_warning)
            _sync_scene_warning()

            name_input = form_input(
                tooltip="Name of the new OBS source",
                label="Source name",
                value=default_name,
            ).classes("w-full")
            name_note = ui.label("").classes(
                "text-theme-warning text-sm break-words mt-1 mb-2"
            )

            def _sync_name_note(*_args: object) -> None:
                current_name = str(name_input.value or "").strip()
                if not current_name:
                    name_note.set_text("")
                    name_note.set_visibility(False)
                    return
                chosen_name, _explanation = adjusted_obs_input_name(
                    current_name, taken_names
                )
                if chosen_name != current_name:
                    name_note.set_text(
                        f'A source named "{current_name}" already exists in OBS.'
                    )
                    name_note.set_visibility(True)
                    return
                origin = str(name_adjustment.get("from") or "")
                renamed = str(name_adjustment.get("to") or "")
                if origin and current_name == renamed:
                    name_note.set_text(
                        f'A source named "{origin}" already exists in OBS, '
                        f'so the name was changed to "{renamed}".'
                    )
                    name_note.set_visibility(True)
                    return
                name_note.set_text("")
                name_note.set_visibility(False)

            name_input.on_value_change(_sync_name_note)
            _sync_name_note()

            with ui.row().classes("w-full gap-2 mb-2"):
                width_input = form_number(
                    tooltip="Browser source width",
                    label="Width",
                    value=1920,
                    min=1,
                    step=1,
                    classes="flex-1",
                )
                height_input = form_number(
                    tooltip="Browser source height",
                    label="Height",
                    value=1080,
                    min=1,
                    step=1,
                    classes="flex-1",
                )

            with ui.column().classes("w-full gap-1 mb-2"):
                reroute_audio = ui.checkbox(
                    "Control audio via OBS", value=False
                ).classes("text-sm")
                fps_custom = ui.checkbox(
                    "Use custom frame rate", value=False
                ).classes("text-sm")
                fps_wrap = ui.column().classes("w-full pl-7")
                with fps_wrap:
                    fps_input = form_number(
                        tooltip="Custom frame rate",
                        label="FPS",
                        value=60,
                        min=1,
                        max=1000,
                        step=1,
                        classes="w-full",
                    )
                fps_wrap.set_visibility(False)
                fps_custom.on_value_change(
                    lambda e: fps_wrap.set_visibility(bool(e.value))
                )
                shutdown_source = ui.checkbox(
                    "Shutdown source when not visible", value=True
                ).classes("text-sm")
                restart_when_active = ui.checkbox(
                    "Refresh browser source when scene becomes active",
                    value=False,
                ).classes("text-sm")

            def _invalid(title: str) -> None:
                notify("", type="warning", source="obs", title=title)

            def _whole_number(
                raw: object, label: str, minimum: int, maximum: Optional[int]
            ):
                if raw is None or raw == "":
                    _invalid(f"Enter a {label.lower()}")
                    return None
                try:
                    if isinstance(raw, float) and not raw.is_integer():
                        raise ValueError
                    value = int(raw)
                except (TypeError, ValueError):
                    _invalid(f"{label} must be a whole number")
                    return None
                if value < minimum or (maximum is not None and value > maximum):
                    if maximum is None:
                        _invalid(f"{label} must be at least {minimum}")
                    else:
                        _invalid(f"{label} must be between {minimum} and {maximum}")
                    return None
                return value

            add_btn = None

            def _submit() -> None:
                scene = str(scene_select.value or "").strip()
                if not scene or scene not in scenes:
                    _invalid("Choose a scene")
                    return
                entered_name = str(name_input.value or "").strip()
                if not entered_name:
                    _invalid("Enter a source name")
                    return
                chosen_name, _explanation = adjusted_obs_input_name(
                    entered_name, taken_names
                )
                if chosen_name != entered_name:
                    name_adjustment["from"] = entered_name
                    name_adjustment["to"] = chosen_name
                    name_input.value = chosen_name
                    _sync_name_note()
                    return
                width = _whole_number(width_input.value, "Width", 1, None)
                if width is None:
                    return
                height = _whole_number(height_input.value, "Height", 1, None)
                if height is None:
                    return
                use_custom_fps = bool(fps_custom.value)
                fps = None
                if use_custom_fps:
                    fps = _whole_number(fps_input.value, "FPS", 1, 1000)
                    if fps is None:
                        return
                if add_btn is not None:
                    add_btn.disable()

                create = partial(
                    obs_service.create_browser_source_blocking,
                    scene_name=scene,
                    input_name=entered_name,
                    url=url,
                    width=width,
                    height=height,
                    reroute_audio=bool(reroute_audio.value),
                    fps_custom=use_custom_fps,
                    fps=fps,
                    shutdown=bool(shutdown_source.value),
                    restart_when_active=bool(restart_when_active.value),
                )

                async def _create() -> None:
                    try:
                        ok, message = await run.io_bound(create)
                    except Exception as e:
                        logger.error("Add to OBS failed: %s", e, exc_info=True)
                        ok, message = False, str(e) or "Could not add the browser source"
                    if ok:
                        notify(
                            "",
                            type="success",
                            source="obs",
                            title=message or "Browser source added",
                        )
                        dialog.close()
                        return
                    notify(
                        "",
                        type="error",
                        source="obs",
                        title="Couldn't add the browser source to OBS",
                        hint="Check OBS is connected and the source name isn't already taken, then try again.",
                        details=str(message or "Unknown error"),
                        actions=nav_actions_settings("OBS"),
                    )
                    if add_btn is not None:
                        add_btn.enable()

                layout_schedule(0, _create, once=True)

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Cancel", on_click=dialog.close).props("outline")
                add_btn = ui.button("Add", on_click=_submit).props("color=primary")

            dialog.open()

    def check_for_updates_manual(self):
        """Manual update check triggers the centralized UpdateManager."""
        try:
            logger.info(
                "User clicked Check for Updates button (delegated to UpdateManager)"
            )
            from .. import updater

            updater.update_manager.trigger_manual_check()
        except Exception as e:
            logger.error(f"Error delegating manual update check: {e}", exc_info=True)
            notify(
                "",
                type="error",
                source="updates",
                title="Couldn't check for updates",
                hint="Check your internet connection and try again.",
                details=str(e),
            )

    async def fetch_all_releases_from_github(self) -> List[Dict[str, str]]:
        """
        Fetch all releases from GitHub repository

        Returns:
            List of release dictionaries with version, date, and notes
        """
        # GitHub configuration (matching updater.py)
        GITHUB_OWNER = "mushroomsuprise"
        GITHUB_REPO = "mycelian"
        GITHUB_API_BASE = "https://api.github.com"

        api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    if response.status == 200:
                        releases_data = await response.json()

                        releases = []
                        for release in releases_data:
                            # Skip draft releases
                            if release.get("draft", False):
                                continue

                            # Extract version from tag_name (remove 'v' prefix if present)
                            tag_name = release.get("tag_name", "")
                            version = tag_name.lstrip("v")

                            # Get release date
                            published_at = release.get("published_at", "")
                            release_date = ""
                            if published_at:
                                try:
                                    from datetime import datetime

                                    dt = datetime.fromisoformat(
                                        published_at.replace("Z", "+00:00")
                                    )
                                    release_date = dt.strftime("%B %d, %Y")
                                except:
                                    release_date = published_at.split("T")[
                                        0
                                    ]  # Fallback to date part

                            # Get release notes
                            release_notes = release.get(
                                "body", "No release notes available."
                            )

                            # Get release URL
                            release_url = release.get("html_url", "")

                            if version:
                                releases.append(
                                    {
                                        "version": version,
                                        "date": release_date,
                                        "notes": release_notes,
                                        "url": release_url,
                                        "prerelease": release.get("prerelease", False),
                                    }
                                )

                        logger.info(
                            f"Successfully fetched {len(releases)} releases from GitHub"
                        )
                        return releases

                    elif response.status == 404:
                        logger.warning(
                            "GitHub repository not found or has no releases"
                        )
                        return []
                    else:
                        logger.error(
                            f"GitHub API request failed with status {response.status}"
                        )
                        # Raised so the caller can tell "no releases" apart from a failed request.
                        raise RuntimeError(
                            f"GitHub returned HTTP {response.status} for {api_url}"
                        )

        except aiohttp.ClientError as e:
            logger.error(f"Network error while fetching releases from GitHub: {e}")
            raise RuntimeError(f"Network error reaching {api_url}: {e}") from e

    def show_changelog_modal(self):
        """Show the changelog modal window"""
        work = None
        try:
            logger.info("User clicked View Changelog button")

            # Prevent multiple concurrent requests (the progress notice is already showing)
            if hasattr(self, "_changelog_loading") and self._changelog_loading:
                return

            self._changelog_loading = True
            work = notify_progress("Loading the changelog", source="updates")

            # Use a shared variable to communicate between threads
            changelog_result = {
                "status": "loading",
                "releases": [],
                "error": None,
                "processed": False,
            }

            # Start changelog fetch in a separate thread
            import threading

            def changelog_fetch_thread():
                try:
                    # Create new event loop for this thread
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)

                    # Fetch releases
                    releases = loop.run_until_complete(
                        self.fetch_all_releases_from_github()
                    )

                    changelog_result["status"] = "complete"
                    changelog_result["releases"] = releases

                except Exception as e:
                    logger.error(f"Error in changelog fetch thread: {e}", exc_info=True)
                    changelog_result["status"] = "error"
                    changelog_result["error"] = str(e)
                finally:
                    try:
                        loop.close()
                    except:
                        pass

            # Start the fetch thread
            changelog_worker = threading.Thread(target=changelog_fetch_thread)
            changelog_worker.daemon = True
            changelog_worker.start()

            # Use a timer to check the result and show modal when ready
            def check_changelog_result():
                try:
                    # Prevent processing multiple times
                    if changelog_result.get("processed", False):
                        return False  # Stop checking - already processed

                    if changelog_result["status"] == "loading":
                        return True  # Continue checking
                    elif changelog_result["status"] == "complete":
                        changelog_result["processed"] = True  # Mark as processed
                        self._changelog_loading = False  # Reset loading flag

                        releases = changelog_result["releases"]
                        if releases:
                            if self._create_changelog_modal(releases):
                                work.close()  # the dialog opening is the result
                            else:
                                work.fail(
                                    "Couldn't open the changelog",
                                    hint="Try again. If it keeps happening, the details are in the log.",
                                )
                        else:
                            work.done(
                                "No releases published yet",
                                type="info",
                            )
                        return False  # Stop checking
                    elif changelog_result["status"] == "error":
                        changelog_result["processed"] = True  # Mark as processed
                        self._changelog_loading = False  # Reset loading flag

                        work.fail(
                            "Couldn't load the changelog from GitHub",
                            hint="Check your internet connection and try again.",
                            details=str(changelog_result["error"] or ""),
                        )
                        return False  # Stop checking

                    return False  # Default stop
                except Exception as e:
                    logger.error(f"Error in check_changelog_result: {e}", exc_info=True)
                    self._changelog_loading = False  # Reset loading flag on error
                    work.fail(
                        "Couldn't open the changelog",
                        hint="Try again. If it keeps happening, the details are in the log.",
                        details=str(e),
                    )
                    return False  # Stop checking

            # Start timer to check results every 200ms (reduced frequency).
            # Cancel it once the result is handled so each click does not leave a
            # no-op timer running for the rest of the session.
            changelog_timer: Dict[str, object] = {}

            def _changelog_tick():
                if not check_changelog_result():
                    timer = changelog_timer.get("timer")
                    if timer is not None:
                        timer.cancel()

            changelog_timer["timer"] = layout_schedule(0.2, _changelog_tick)

        except Exception as e:
            logger.error(f"Error showing changelog modal: {e}", exc_info=True)
            self._changelog_loading = False  # Reset loading flag on error
            fail_kwargs = {
                "hint": "Try again. If it keeps happening, the details are in the log.",
                "details": str(e),
            }
            if work is not None:
                work.fail("Couldn't open the changelog", **fail_kwargs)
            else:
                notify("", type="error", source="updates", title="Couldn't open the changelog", **fail_kwargs)

    def _create_changelog_modal(self, releases: List[Dict[str, str]]) -> bool:
        """Create and display the changelog modal with release data (False if it failed)."""
        try:
            with ui.dialog().props("maximized") as changelog_dialog:
                with ui.card().classes("w-full max-w-4xl mx-auto my-8"):
                    # Header
                    with ui.row().classes(
                        "w-full items-center justify-between p-4 border-b"
                    ):
                        ui.label("Mycelian Changelog").classes("text-2xl font-bold")
                        ui.button(icon="close", on_click=changelog_dialog.close).props(
                            "flat round"
                        )

                    # Content area with scroll
                    with ui.scroll_area().classes("w-full").style("height: 70vh"):
                        with ui.column().classes("w-full gap-4 p-4"):
                            if not releases:
                                ui.label("No releases found.").classes(
                                    "text-center muted-text text-lg"
                                )
                            else:
                                for release in releases:
                                    # Release card
                                    with ui.card().classes("w-full"):
                                        # Release header
                                        with ui.row().classes(
                                            "w-full items-center justify-between p-4 border-b"
                                        ):
                                            with ui.column().classes("gap-1"):
                                                version_text = (
                                                    f"Version {release['version']}"
                                                )
                                                if release.get("prerelease", False):
                                                    version_text += " (Pre-release)"
                                                ui.label(version_text).classes(
                                                    "text-xl font-semibold"
                                                )
                                                if release.get("date"):
                                                    ui.label(
                                                        f"Released {release['date']}"
                                                    ).classes("text-sm muted-text")

                                            # Link to GitHub release
                                            if release.get("url"):

                                                def create_open_url_handler(url):
                                                    return lambda _: webbrowser.open(
                                                        url
                                                    )

                                                ui.button(
                                                    "View on GitHub",
                                                    on_click=create_open_url_handler(
                                                        release["url"]
                                                    ),
                                                ).props(
                                                    "outline size=sm icon=open_in_new"
                                                )

                                        # Release notes
                                        with ui.column().classes("w-full p-4"):
                                            notes = release.get(
                                                "notes", "No release notes available."
                                            )
                                            if notes.strip():
                                                # Format the markdown-like content for display
                                                formatted_notes = (
                                                    self._format_release_notes(notes)
                                                )
                                                ui.html(
                                                    formatted_notes, sanitize=False
                                                ).classes("text-sm")
                                            else:
                                                ui.label(
                                                    "No release notes available."
                                                ).classes("text-sm muted-text")

            # Open the dialog
            changelog_dialog.open()
            return True

        except Exception as e:
            logger.error(f"Error creating changelog modal: {e}", exc_info=True)
            return False

    def _format_release_notes(self, notes: str) -> str:
        """Format release notes for HTML display"""
        try:
            # Basic markdown-like formatting for GitHub release notes
            import html

            # Escape HTML first
            formatted = html.escape(notes)

            # Convert common markdown patterns to HTML
            # Headers (## becomes h3, ### becomes h4)
            import re

            formatted = re.sub(
                r"^### (.+)$", r"<h4>\1</h4>", formatted, flags=re.MULTILINE
            )
            formatted = re.sub(
                r"^## (.+)$", r"<h3>\1</h3>", formatted, flags=re.MULTILINE
            )
            formatted = re.sub(
                r"^# (.+)$", r"<h2>\1</h2>", formatted, flags=re.MULTILINE
            )

            # Bold text (**text** or __text__)
            formatted = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", formatted)
            formatted = re.sub(r"__(.+?)__", r"<strong>\1</strong>", formatted)

            # Italic text (*text* or _text_)
            formatted = re.sub(r"\*(.+?)\*", r"<em>\1</em>", formatted)
            formatted = re.sub(r"_(.+?)_", r"<em>\1</em>", formatted)

            # Code blocks (```code```)
            formatted = re.sub(
                r"```(.+?)```", r"<code>\1</code>", formatted, flags=re.DOTALL
            )

            # Inline code (`code`)
            formatted = re.sub(r"`(.+?)`", r"<code>\1</code>", formatted)

            # Links ([text](url))
            formatted = re.sub(
                r"\[(.+?)\]\((.+?)\)", r'<a href="\2" target="_blank">\1</a>', formatted
            )

            # Bullet points (- or *)
            lines = formatted.split("\n")
            in_list = False
            formatted_lines = []

            for line in lines:
                stripped = line.strip()
                if stripped.startswith("- ") or stripped.startswith("* "):
                    if not in_list:
                        formatted_lines.append("<ul>")
                        in_list = True
                    formatted_lines.append(f"<li>{stripped[2:]}</li>")
                else:
                    if in_list:
                        formatted_lines.append("</ul>")
                        in_list = False
                    formatted_lines.append(line)

            if in_list:
                formatted_lines.append("</ul>")

            formatted = "\n".join(formatted_lines)

            # Convert line breaks to <br> tags
            formatted = formatted.replace("\n", "<br>")

            return formatted.strip()

        except Exception as e:
            logger.error(f"Error formatting release notes: {e}")
            return notes  # Return original notes if formatting fails

    def build_ui_v2(self):
        """Build the modular settings UI with per-tab components and per-tab Save/Discard.

        This implementation uses independent tab classes with local buffers and a
        tab-switch guard for unsaved changes. Statistics and About remain unchanged.
        """
        logger.info("--- SettingsUI.build_ui_v2() CALLED ---")

        # Ensure state is initialized for About info
        with StartupTimer("settings_state_init"):
            if (
                not hasattr(state_manager, "_initialized")
                or not state_manager._initialized
            ):
                state_manager.initialize()
            self.app_settings = state_manager.get_app_settings()

        # Prepare tab components
        with StartupTimer("settings_tab_objects"):
            from .tabs.app_settings_tab import AppSettingsTab
            from .tabs.database_tab import DatabaseTab
            from .tabs.discord_tab import DiscordTab
            from .tabs.game_hooks_tab import GameHooksTab
            from .tabs.obs_tab import ObsTab
            from .tabs.psn_tab import PSNTab
            from .tabs.spotify_tab import SpotifyTab
            from .tabs.statistics_tab import StatisticsTab
            from .tabs.theme_tab import ThemeTab
            from .tabs.twitch_tab import TwitchTab
            from .tabs.youtube_tab import YouTubeTab
            from .tabs.streamlabs_tab import StreamlabsTab
            from .tabs.streamelements_tab import StreamElementsTab

            # Time each tab object creation individually
            with StartupTimer("settings_tab_objects_app_settings"):
                app_settings_tab = AppSettingsTab()
            with StartupTimer("settings_tab_objects_theme"):
                theme_tab = ThemeTab()
            with StartupTimer("settings_tab_objects_twitch"):
                twitch_tab = TwitchTab()
            with StartupTimer("settings_tab_objects_psn"):
                psn_tab = PSNTab()
            with StartupTimer("settings_tab_objects_spotify"):
                spotify_tab = SpotifyTab()
            with StartupTimer("settings_tab_objects_youtube"):
                youtube_tab = YouTubeTab()
            with StartupTimer("settings_tab_objects_streamlabs"):
                streamlabs_tab = StreamlabsTab()
            with StartupTimer("settings_tab_objects_streamelements"):
                streamelements_tab = StreamElementsTab()
            with StartupTimer("settings_tab_objects_discord"):
                discord_tab = DiscordTab()
            with StartupTimer("settings_tab_objects_obs"):
                obs_tab = ObsTab()
            with StartupTimer("settings_tab_objects_game_hooks"):
                game_hooks_tab = GameHooksTab()
            with StartupTimer("settings_tab_objects_database"):
                database_tab = DatabaseTab()
            with StartupTimer("settings_tab_objects_statistics"):
                statistics_tab = StatisticsTab()

            self._tabs_by_name = {
                "App Settings": app_settings_tab,
                "Theme": theme_tab,
                "Twitch": twitch_tab,
                "PSN": psn_tab,
                "Spotify": spotify_tab,
                "YouTube": youtube_tab,
                "Streamlabs": streamlabs_tab,
                "StreamElements": streamelements_tab,
                "Discord": discord_tab,
                "OBS": obs_tab,
                "Game Hooks": game_hooks_tab,
                "Database": database_tab,
                "Statistics": statistics_tab,
            }
            saved_settings = (
                getattr(self.app_settings, "last_settings_tab", "") or "Twitch"
            )
            if saved_settings in self._tabs_by_name or saved_settings == "About":
                self._active_tab_name = saved_settings
                self._accepted_settings_tab = saved_settings

        # Add the custom CSS
        with StartupTimer("settings_css"):
            ui.add_head_html(f"<style>{CSS}</style>")

        # Match Alerts/Chatbot layout: tab-surface padding outside the connected sub-tab shell
        with StartupTimer("settings_ui_structure"):
            with ui.element("div").classes(
                "tab-surface w-full flex-1 min-h-0 flex flex-col p-4"
            ):
                with ui.element("div").classes(
                    "mycelian-sub-tab-shell w-full flex-1 min-h-0 flex flex-col"
                ):
                    settings_tab_names = [
                        "Twitch",
                        "OBS",
                        "PSN",
                        "Spotify",
                        "YouTube",
                        "Streamlabs",
                        "StreamElements",
                        "Discord",
                        "Game Hooks",
                        "Database",
                        "Statistics",
                        "Theme",
                        "App Settings",
                        "About",
                    ]
                    with ui.tabs().classes("w-full settings-tabs mycelian-sub-tabs") as tabs:
                        ui.tab("Twitch", icon=service_tab_icon("twitch"))
                        ui.tab("OBS", icon=service_tab_icon("obs"))
                        ui.tab("PSN", icon=service_tab_icon("psn"))
                        ui.tab("Spotify", icon=service_tab_icon("spotify"))
                        ui.tab("YouTube", icon=service_tab_icon("youtube"))
                        ui.tab("Streamlabs", icon=service_tab_icon("streamlabs"))
                        ui.tab("StreamElements", icon=service_tab_icon("streamelements"))
                        ui.tab("Discord", icon=service_tab_icon("discord"))
                        ui.tab("Game Hooks", icon="memory")
                        ui.tab("Database", icon="storage")
                        ui.tab("Statistics", icon="analytics")
                        ui.tab("Theme", icon="palette")
                        ui.tab("App Settings", icon="tune")
                        ui.tab("About", icon="info")

                    with ui.row().classes(
                        "w-full items-center gap-2 px-1 pb-1"
                    ).style("order: -1"):
                        self._settings_jump = ui.select(
                            options=settings_tab_names,
                            value=self._active_tab_name,
                            label="Jump to",
                            with_input=True,
                            on_change=lambda e: self._on_settings_jump(tabs, e.value),
                        ).props("dense outlined options-dense").classes("w-56")

                    # Panels for each tab
                    with StartupTimer("settings_tab_panels"):
                        with StartupTimer("settings_create_tab_panels_container"):
                            tab_panels_container = ui.tab_panels(
                                tabs, value=self._active_tab_name
                            ).classes("w-full flex-1 min-h-0 grow").props(
                                "animated=false"
                            )

                            # Set references for help system context detection
                            from ..help_system.contextual_help import (
                                set_settings_ui_references,
                            )

                            set_settings_ui_references(tabs, tab_panels_container)

                        # Initialize lazy loading state
                        self._settings_loaded_tabs = set()
                        self._settings_preload_failed = set()

                        def load_tab_content(tab_name):
                            """Load content for a specific tab"""
                            from ..ui_tab_transitions import _tab_label

                            tab_name = _tab_label(tab_name)
                            if not tab_name or tab_name in self._settings_loaded_tabs:
                                return  # Already loaded or unknown

                            container = self._settings_tab_containers.get(tab_name)
                            if not container:
                                return

                            with StartupTimer(
                                f"settings_{tab_name.lower().replace(' ', '_')}_tab"
                            ):
                                try:
                                    # Safely remove loading elements before adding content
                                    try:
                                        if hasattr(container, "_loading_spinner"):
                                            container.remove(container._loading_spinner)
                                        if hasattr(container, "_loading_label"):
                                            container.remove(container._loading_label)
                                    except (ValueError, Exception):
                                        pass  # Already removed or not in children

                                    # Load actual content (tab .build() enters parent_container)
                                    if tab_name == "App Settings":
                                        self._tabs_by_name[
                                            "App Settings"
                                        ].build(container)
                                    elif tab_name == "Theme":
                                        self._tabs_by_name["Theme"].build(container)
                                    elif tab_name == "Twitch":
                                        self._tabs_by_name["Twitch"].build(container)
                                    elif tab_name == "PSN":
                                        self._tabs_by_name["PSN"].build(container)
                                    elif tab_name == "Spotify":
                                        self._tabs_by_name["Spotify"].build(container)
                                    elif tab_name == "YouTube":
                                        self._tabs_by_name["YouTube"].build(container)
                                    elif tab_name == "Streamlabs":
                                        self._tabs_by_name["Streamlabs"].build(container)
                                    elif tab_name == "StreamElements":
                                        self._tabs_by_name["StreamElements"].build(container)
                                    elif tab_name == "Discord":
                                        self._tabs_by_name["Discord"].build(container)
                                    elif tab_name == "OBS":
                                        self._tabs_by_name["OBS"].build(container)
                                    elif tab_name == "Game Hooks":
                                        self._tabs_by_name["Game Hooks"].build(
                                            container
                                        )
                                    elif tab_name == "Database":
                                        self._tabs_by_name["Database"].build(
                                            container
                                        )
                                    elif tab_name == "Statistics":
                                        self._tabs_by_name["Statistics"].build(
                                            container
                                        )
                                    elif tab_name == "About":
                                        self._build_about_tab(container)

                                    self._settings_loaded_tabs.add(tab_name)
                                    self._note_settings_tab_loaded(tab_name)
                                except Exception as e:
                                    logger.error(
                                        f"Error loading settings tab {tab_name}: {e}",
                                        exc_info=True,
                                    )
                                    with container:
                                        ui.label(
                                            "This page couldn't be loaded. Details are in the log."
                                        ).classes("text-red-400 text-center p-4")
                                    notify(
                                        "Restart Mycelian to try again.",
                                        type="error",
                                        source="app",
                                        title=f"Couldn't open {tab_name} settings",
                                        hint="If it keeps happening, copy the details and report it.",
                                        details=f"{type(e).__name__}: {e}",
                                        dedupe_key=f"settings:tab_load:{tab_name}",
                                    )

                        # Create tab panels with lazy loading
                        self._settings_tab_containers = {}

                        # Panel DOM order matches tab strip (for correct slide direction)
                        settings_panel_order = [
                            "Twitch",
                            "OBS",
                            "PSN",
                            "Spotify",
                            "YouTube",
                            "Streamlabs",
                            "StreamElements",
                            "Discord",
                            "Game Hooks",
                            "Database",
                            "Statistics",
                            "Theme",
                            "App Settings",
                            "About",
                        ]
                        self._load_settings_tab_content = load_tab_content
                        self._settings_preload_order = list(settings_panel_order)

                        _TAB_PANEL_CLASSES = (
                            "tab-content w-full h-full min-h-0 flex flex-col"
                        )

                        with tab_panels_container:
                            for tab_name in settings_panel_order:
                                with ui.tab_panel(tab_name).classes(_TAB_PANEL_CLASSES):
                                    with ui.scroll_area().classes("w-full h-full grow"):
                                        container = ui.column().classes(
                                            "w-full gap-4"
                                        )
                                        self._settings_tab_containers[tab_name] = (
                                            container
                                        )
                                        _eager_tabs = {
                                            "App Settings",
                                            "Statistics",
                                            "About",
                                        }
                                        if tab_name in _eager_tabs:
                                            with StartupTimer(
                                                f"settings_{tab_name.lower().replace(' ', '_')}_tab"
                                            ):
                                                if tab_name == "About":
                                                    self._build_about_tab(container)
                                                else:
                                                    self._tabs_by_name[
                                                        tab_name
                                                    ].build(container)
                                            self._settings_loaded_tabs.add(tab_name)
                                            self._note_settings_tab_loaded(tab_name)
                                        else:
                                            spinner = (
                                                ui.spinner("dots")
                                                .classes("mx-auto")
                                                .props("size=2rem")
                                            )
                                            label = ui.label("Loading...").classes(
                                                "text-center muted-text"
                                            )
                                            container._loading_spinner = spinner
                                            container._loading_label = label

                        from ..ui_tab_transitions import _tab_label

                        def _settings_tab_from_event(e) -> str:
                            val = getattr(e, "value", None)
                            if val is None:
                                args = getattr(e, "args", None)
                                if isinstance(args, dict) and "value" in args:
                                    val = args["value"]
                            return _tab_label(val)

                        def on_settings_tab_change(e):
                            new_tab = _settings_tab_from_event(e)
                            if (
                                new_tab in self._settings_tab_containers
                                and new_tab not in self._settings_loaded_tabs
                            ):
                                load_tab_content(new_tab)

                        tabs.on("change", on_settings_tab_change)

                        # Timer fallback when tabs.on("change") does not fire (native mode)
                        previous_settings_tab = _tab_label(tabs.value)

                        def check_settings_tab_changes():
                            nonlocal previous_settings_tab

                            current_tab = _tab_label(tabs.value)
                            if current_tab != previous_settings_tab:
                                if (
                                    current_tab in self._settings_tab_containers
                                    and current_tab not in self._settings_loaded_tabs
                                ):
                                    load_tab_content(current_tab)
                                previous_settings_tab = current_tab

                        # Check for tab changes every 200ms
                        self._settings_tab_poll = layout_schedule(
                            2.0, check_settings_tab_changes, active=True
                        )

                        # Lazy-loaded default tab never fires a change event on first open
                        initial_settings_tab = (
                            _tab_label(tabs.value) or self._active_tab_name
                        )
                        if (
                            initial_settings_tab
                            and initial_settings_tab not in self._settings_loaded_tabs
                        ):
                            layout_schedule(
                                0.05,
                                lambda t=initial_settings_tab: load_tab_content(t),
                                once=True,
                            )

                # Unsaved-changes guard. Quasar reports the new tab through
                # on_value_change; the timer covers a missed event.
                from ..ui_tab_transitions import _tab_label

                self._settings_tabs = tabs
                self._accepted_settings_tab = (
                    _tab_label(tabs.value) or self._active_tab_name
                )

                def on_tab_change(e):
                    if self._settings_switch_guard:
                        return
                    new_name = _tab_label(getattr(e, "value", None))
                    prev_name = _tab_label(getattr(e, "previous_value", None)) or _tab_label(
                        self._active_tab_name
                    )
                    if self._unsaved_dialog_open:
                        if new_name and new_name != self._active_tab_name:
                            self._settings_switch_guard = True
                            try:
                                tabs.value = self._active_tab_name
                            finally:
                                self._settings_switch_guard = False
                        return
                    if not new_name or new_name == prev_name:
                        return
                    dirty_name = self._first_dirty_tab_name()
                    # The tab being left is the one that matters. Fall back to any
                    # dirty tab so a label mismatch still prompts.
                    leaving_dirty = dirty_name if dirty_name and dirty_name != new_name else ""
                    if prev_name and self._tab_is_dirty(prev_name):
                        leaving_dirty = prev_name
                    if leaving_dirty:
                        self._unsaved_dialog_open = True
                        self._active_tab_name = leaving_dirty
                        tabs.value = leaving_dirty
                        self._sync_settings_jump(leaving_dirty)
                        self._show_unsaved_changes_dialog(tabs, leaving_dirty, new_name)
                        return
                    current_tab = self._tabs_by_name.get(prev_name)
                    if current_tab:
                        current_tab.on_exit()
                    next_tab = self._tabs_by_name.get(new_name)
                    if next_tab:
                        next_tab.on_enter()
                        self._snapshot_tab_fields(next_tab)
                    self._active_tab_name = new_name
                    self._accepted_settings_tab = new_name
                    self._sync_settings_jump(new_name)
                    self._remember_settings_tab(new_name)

                def check_subtab_changes():
                    current_subtab = _tab_label(tabs.value)
                    if (
                        not current_subtab
                        or current_subtab == self._accepted_settings_tab
                        or self._unsaved_dialog_open
                        or self._settings_switch_guard
                    ):
                        return
                    on_tab_change(
                        type(
                            "MockEvent",
                            (),
                            {
                                "value": current_subtab,
                                "previous_value": self._accepted_settings_tab,
                            },
                        )()
                    )

                tabs.on_value_change(on_tab_change)
                tab_panels_container.on_value_change(on_tab_change)
                self._settings_subtab_poll = layout_schedule(
                    2.0, check_subtab_changes, active=True
                )

        ui.run_javascript(
            "window.mycelianInitSubTabSeams && window.mycelianInitSubTabSeams()"
        )

        # Log timing summary
        # log_startup_summary()

    def _build_about_tab(self, container):
        """Build the About tab content (see tabs/about_tab.py)."""
        from .tabs.about_tab import AboutTab

        self._about_tab = AboutTab(self)
        self._about_tab.build(container)

    def _note_settings_tab_loaded(self, tab_name: str) -> None:
        """Remember the field values just built so later edits can be detected."""
        tab = self._tabs_by_name.get(tab_name)
        if tab is None:
            return
        self._snapshot_tab_fields(tab)
        tab.dirty = False

    def _snapshot_tab_fields(self, tab) -> None:
        snap = {}
        for key, element in getattr(tab, "ui_elements", {}).items():
            if not self._is_settings_field(element):
                continue
            try:
                snap[key] = element.value
            except Exception:
                continue
        tab._field_snapshot = snap

    @staticmethod
    def _is_settings_field(element) -> bool:
        return element.__class__.__name__ in {
            "Input",
            "Textarea",
            "Number",
            "Select",
            "Switch",
            "Checkbox",
            "Slider",
            "Toggle",
            "ColorInput",
        }

    @staticmethod
    def _settings_values_equal(left, right) -> bool:
        if left == right:
            return True
        if (left is None or left == "") and (right is None or right == ""):
            return True
        if (
            isinstance(left, (int, float))
            and isinstance(right, (int, float))
            and not isinstance(left, bool)
            and not isinstance(right, bool)
        ):
            try:
                return float(left) == float(right)
            except (TypeError, ValueError):
                return False
        return False

    def _tab_is_dirty(self, tab_name: str) -> bool:
        tab = self._tabs_by_name.get(tab_name)
        if tab is None:
            return False
        checker = getattr(tab, "has_unsaved_changes", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                logger.debug(
                    "Settings tab %s unsaved check failed", tab_name, exc_info=True
                )
        if getattr(tab, "dirty", False):
            return True
        snap = getattr(tab, "_field_snapshot", None)
        if not snap:
            return False
        elements = getattr(tab, "ui_elements", {})
        for key, old in snap.items():
            element = elements.get(key)
            if element is None:
                continue
            try:
                current = element.value
            except Exception:
                continue
            if not self._settings_values_equal(old, current):
                return True
        return False

    def _first_dirty_tab_name(self) -> str:
        for name in self._tabs_by_name:
            if self._tab_is_dirty(name):
                return name
        return ""

    def _remember_settings_tab(self, name: str) -> None:
        if not name:
            return
        try:
            from ..mainuiwindow import remember_ui_pref

            remember_ui_pref("last_settings_tab", name)
        except Exception as exc:
            logger.debug("Could not remember settings tab: %s", exc)

    def _sync_settings_jump(self, name: str) -> None:
        jump = self._settings_jump
        if jump is None or not name:
            return
        self._jump_sync = True
        try:
            if jump.value != name:
                jump.value = name
        finally:
            self._jump_sync = False

    def _on_settings_jump(self, tabs_component, name: str) -> None:
        if self._jump_sync or not name:
            return
        if name == self._active_tab_name:
            return
        tabs_component.value = name

    def _complete_settings_tab_switch(
        self, tabs_component, prev_name: str, next_name: str
    ) -> None:
        """Leave the previous settings tab and enter the next one."""
        self._settings_switch_guard = True
        try:
            current_tab = self._tabs_by_name.get(prev_name)
            if current_tab:
                current_tab.on_exit()
            next_tab = self._tabs_by_name.get(next_name)
            if next_tab:
                next_tab.on_enter()
                self._snapshot_tab_fields(next_tab)
                next_tab.dirty = False
            self._active_tab_name = next_name
            self._accepted_settings_tab = next_name
            tabs_component.value = next_name
            self._sync_settings_jump(next_name)
            self._remember_settings_tab(next_name)
        finally:
            self._settings_switch_guard = False

    def _show_unsaved_changes_dialog(
        self, tabs_component, prev_name: str, next_name: str
    ) -> None:
        """Prompt to save, discard, or stay when leaving a dirty tab."""
        from nicegui import context

        with context.client.layout:
            self._open_unsaved_settings_dialog(tabs_component, prev_name, next_name)

    def _open_unsaved_settings_dialog(
        self, tabs_component, prev_name: str, next_name: str
    ) -> None:
        with ui.dialog().props("persistent") as dialog, ui.card().classes(
            "w-[460px] p-4"
        ):
            ui.label("Unsaved changes").classes("text-lg font-bold mb-2")
            prompt = ui.label(
                "You have unsaved changes on this tab. Save them before switching?"
            ).classes("secondary-text mb-4")

            def _save_failed_inline() -> None:
                # The save path already notified once; say it here too, without a second toast.
                prompt.set_text(
                    f"Couldn't save {prev_name} settings, so you're still on that tab. "
                    "Your changes are still here: fix the problem and save again, or discard them."
                )

            def _release() -> None:
                self._unsaved_dialog_open = False

            def stay() -> None:
                _release()
                self._sync_settings_jump(prev_name)
                dialog.close()

            def confirm_switch():
                self._settings_switch_guard = True
                try:
                    current_tab = self._tabs_by_name.get(prev_name)
                    if current_tab is not None:
                        current_tab._suppress_dirty = True
                        try:
                            current_tab.discard()
                            if hasattr(current_tab, "mark_clean"):
                                current_tab.mark_clean()
                        finally:
                            current_tab._suppress_dirty = False
                        self._snapshot_tab_fields(current_tab)
                        current_tab.dirty = False
                    self._complete_settings_tab_switch(
                        tabs_component, prev_name, next_name
                    )
                finally:
                    _release()
                    dialog.close()

            def save_and_switch():
                current_tab = self._tabs_by_name.get(prev_name)
                try:
                    if current_tab:
                        current_tab.save()
                except Exception as exc:
                    logger.error(
                        "Error saving settings tab %s: %s", prev_name, exc, exc_info=True
                    )
                    notify(
                        "Your changes are still here.",
                        type="error",
                        source="app",
                        title=f"Couldn't save {prev_name} settings",
                        hint="Try Save again, or choose Discard and switch.",
                        details=f"{type(exc).__name__}: {exc}",
                    )
                    _save_failed_inline()
                    return
                if current_tab and self._tab_is_dirty(prev_name):
                    _save_failed_inline()
                    return
                _release()
                try:
                    if current_tab:
                        self._snapshot_tab_fields(current_tab)
                        current_tab.dirty = False
                    self._complete_settings_tab_switch(
                        tabs_component, prev_name, next_name
                    )
                finally:
                    dialog.close()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Stay", on_click=stay).props("outline")
                ui.button("Discard and switch", on_click=confirm_switch).props(
                    "outline"
                )
                ui.button("Save and switch", on_click=save_and_switch).props(
                    "color=primary"
                )

            dialog.open()  # Explicitly open the dialog

    def load_next_unloaded_settings_tab(self) -> bool:
        """Build one Settings subtab that is still a spinner.

        Returns True when a subtab was attempted, False when none remain or the
        Settings shell has not been built yet.
        """
        loader = getattr(self, "_load_settings_tab_content", None)
        order = getattr(self, "_settings_preload_order", None)
        loaded = getattr(self, "_settings_loaded_tabs", None)
        if not callable(loader) or not order or loaded is None:
            return False
        failed = getattr(self, "_settings_preload_failed", None)
        if failed is None:
            failed = set()
            self._settings_preload_failed = failed
        for name in order:
            if name in loaded or name in failed:
                continue
            loader(name)
            if name not in loaded:
                failed.add(name)
            else:
                self._pause_preloaded_tab(name)
            return True
        return False

    def _pause_preloaded_tab(self, name: str) -> None:
        """A subtab built in the background starts its status timers; stop them
        until the user opens it (``on_enter`` restarts them and refreshes at once)."""
        if name == getattr(self, "_active_tab_name", ""):
            return
        tab = (getattr(self, "_tabs_by_name", None) or {}).get(name)
        if tab is None or not hasattr(tab, "on_exit"):
            return
        try:
            tab.on_exit()
        except Exception:
            logger.debug("Settings preload pause failed for %s", name, exc_info=True)

    def pause_background_work(self) -> None:
        """Stop Settings polls and the visible service-tab timer."""
        self._settings_background_paused = True
        for timer in (
            getattr(self, "_settings_tab_poll", None),
            getattr(self, "_settings_subtab_poll", None),
        ):
            if timer is None:
                continue
            try:
                timer.active = False
            except Exception:
                pass
        tabs = getattr(self, "_tabs_by_name", None) or {}
        tab = tabs.get(getattr(self, "_active_tab_name", "") or "")
        if tab is not None and hasattr(tab, "on_exit"):
            try:
                tab.on_exit()
            except Exception:
                logger.debug("Settings tab on_exit failed", exc_info=True)

    def resume_background_work(self) -> None:
        """Restart Settings polls when the Settings tab is showing again."""
        if not getattr(self, "_settings_background_paused", False):
            return
        self._settings_background_paused = False
        for timer in (
            getattr(self, "_settings_tab_poll", None),
            getattr(self, "_settings_subtab_poll", None),
        ):
            if timer is None:
                continue
            try:
                timer.active = True
            except Exception:
                pass
        tabs = getattr(self, "_tabs_by_name", None) or {}
        tab = tabs.get(getattr(self, "_active_tab_name", "") or "")
        if tab is not None and hasattr(tab, "on_enter"):
            try:
                tab.on_enter()
            except Exception:
                logger.debug("Settings tab on_enter failed", exc_info=True)

    def has_unsaved_changes(self) -> bool:
        """Check if any settings tab has unsaved changes."""
        if not hasattr(self, "_tabs_by_name") or self._tabs_by_name is None:
            return False
        return bool(self._first_dirty_tab_name())


settings_ui = SettingsUI()


def create_settings_tab():
    """Create and return the settings tab content"""
    # Use the modular per-tab UI build
    return settings_ui.build_ui_v2()

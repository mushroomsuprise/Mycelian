# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
from __future__ import annotations

import logging
from typing import Dict, Any, List, Optional

from nicegui import ui

from ...ui_buttons import outline_button, primary_button
from ...ui_form_controls import form_sensitive_input
from ...ui_settings_layout import (
    THEME_CHIP_CLASSES,
    settings_form_grid,
    settings_section,
    settings_status_band,
    settings_surface,
    theme_chip_row,
)
from ...notification_engine import notify
from ...ui_timer import layout_schedule
from ...api_credentials_manager import api_credentials_manager

from ... import dataobjects
from ...dataobjects import state_manager, YouTubeData
from ...youtube import YOUTUBE_OAUTH_REDIRECT_URI

logger = logging.getLogger(__name__)


class YouTubeTab:
    name = "YouTube"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.YouTubeData] = None
        self.ui_elements: Dict[str, Any] = {}
        self._creds: Dict[str, str] = {}
        self._playlist_chip_container: Optional[ui.row] = None
        self._playlist_input: Optional[ui.input] = None
        self._status_timer: Optional[Any] = None
        self._oauth_in_progress: bool = False
        self._active_timers: List[Any] = []

    @staticmethod
    def _str_from_value_event(e: Any) -> str:
        v = getattr(e, "value", None)
        if v is not None and not isinstance(v, (list, tuple)):
            return str(v)
        args = getattr(e, "args", None)
        if isinstance(args, str):
            return args
        if isinstance(args, (list, tuple)) and len(args) > 0:
            return str(args[0])
        return ""

    def on_enter(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = True
        layout_schedule(0.05, self._refresh_status, once=True)

    def _refresh_status(self) -> None:
        """Refresh YouTube status display."""
        try:
            # Force reload the latest YouTube data from state manager
            from ...dataobjects import state_manager

            youtube_data = state_manager.get_youtube_data()

            # Get current status from the YouTube module
            from ... import youtube

            status_info = youtube.get_youtube_status()

            # Also get the connection status directly from our data
            current_connection_status = (
                youtube_data.connection_status if youtube_data else "Unknown"
            )

            # Update status label and color
            if "status_label" in self.ui_elements:
                # Use the most current status from either source
                status_text = current_connection_status or status_info.get(
                    "status", "Unknown"
                )
                is_connected = status_info.get("is_connected", False)

                self.ui_elements["status_label"].set_text(status_text)

                # Update color based on status
                if is_connected or status_text == "Connected":
                    self.ui_elements["status_label"].classes(
                        replace="font-semibold text-theme-success"
                    )
                else:
                    self.ui_elements["status_label"].classes(
                        replace="font-semibold text-theme-error"
                    )

            # Update channel label
            if "channel_label" in self.ui_elements:
                if youtube_data and youtube_data.channels:
                    channel_count = len(youtube_data.channels)
                    latest_channel = (
                        youtube_data.latest_video_channel or "Multiple channels"
                    )
                    channel_info = (
                        f"{channel_count} channels - Latest: {latest_channel}"
                    )
                else:
                    channel_info = "No channels configured"
                self.ui_elements["channel_label"].set_text(channel_info)

            # Update latest video label
            if "video_label" in self.ui_elements:
                latest_video = status_info.get("latest_video", "N/A")
                if latest_video and latest_video != "No video found":
                    # Truncate long titles for display
                    if len(latest_video) > 50:
                        latest_video = latest_video[:47] + "..."
                self.ui_elements["video_label"].set_text(latest_video)

            # Live chat / OAuth status
            if "live_status_label" in self.ui_elements:
                live_status = status_info.get("live_chat_status") or (
                    youtube_data.live_chat_status if youtube_data else "Not authorized"
                )
                oauth_title = status_info.get("oauth_channel_title") or (
                    (youtube_data.oauth_channel_title if youtube_data else "") or ""
                )
                if oauth_title and live_status in ("Live", "Offline"):
                    label_text = f"{live_status} — {oauth_title}"
                else:
                    label_text = str(live_status)
                self.ui_elements["live_status_label"].set_text(label_text)
                if live_status == "Live":
                    self.ui_elements["live_status_label"].classes(
                        replace="font-semibold text-theme-success"
                    )
                elif live_status == "Offline" or status_info.get("oauth_connected"):
                    self.ui_elements["live_status_label"].classes(
                        replace="font-semibold text-theme-primary"
                    )
                else:
                    self.ui_elements["live_status_label"].classes(
                        replace="font-semibold text-theme-error"
                    )

            if "connect_button" in self.ui_elements and not self._oauth_in_progress:
                if status_info.get("oauth_connected"):
                    self.ui_elements["connect_button"].set_text("Reconnect")
                else:
                    self.ui_elements["connect_button"].set_text("Connect")

        except Exception as e:
            logger.error(f"Error refreshing YouTube status: {str(e)}", exc_info=True)
            # Set error status
            if "status_label" in self.ui_elements:
                self.ui_elements["status_label"].set_text("Status Error")
                self.ui_elements["status_label"].classes(
                    replace="font-semibold text-theme-error"
                )

    def _test_connection(self) -> None:
        """Handle the Test Connection button click for YouTube"""
        try:
            import logging

            logger = logging.getLogger(__name__)
            logger.info("User clicked Test Connection button for YouTube")

            # Prevent duplicate test attempts
            if getattr(self, "_test_in_progress", False):
                logger.warning(
                    "YouTube test already in progress, ignoring duplicate request"
                )
                notify("YouTube test already in progress...", type="info")
                return

            # Mark test as in progress
            self._test_in_progress = True

            # Update button state to show it's working
            if "test_button" in self.ui_elements:
                self.ui_elements["test_button"].set_text("Testing...")
                self.ui_elements["test_button"].disable()

            # Show a notification that the process is starting
            notify("Testing YouTube connection...", type="info")

            # Test the connection in a separate thread
            import threading

            # Create shared result holder for thread communication
            test_result = {
                "status": "testing",
                "success": None,
                "error": None,
            }

            def test_thread():
                try:
                    from ... import youtube

                    # Test the connection
                    success = youtube.test_connection()
                    test_result["status"] = "complete"
                    test_result["success"] = success

                except Exception as e:
                    logger.error(
                        f"Error in YouTube test thread: {str(e)}", exc_info=True
                    )
                    test_result["status"] = "error"
                    test_result["error"] = str(e)

            # Start the thread
            test_worker = threading.Thread(target=test_thread)
            test_worker.daemon = True
            test_worker.start()

            # Use a single-shot approach with delayed execution
            def handle_test_completion():
                try:
                    # Wait for thread to complete (up to 5 seconds)
                    test_worker.join(timeout=5.0)

                    if test_result["status"] == "complete":
                        if test_result["success"]:
                            notify(
                                "YouTube connection successful!",
                                type="positive",
                                timeout=3000,
                            )
                            logger.info(
                                "YouTube connection test completed successfully"
                            )
                        else:
                            notify(
                                "YouTube connection failed. Please check your API key and channel URLs.",
                                type="negative",
                                timeout=5000,
                            )
                            logger.warning("YouTube connection test failed")

                    elif test_result["status"] == "error":
                        notify(
                            f"Error testing YouTube connection: {test_result['error']}",
                            type="negative",
                        )
                        logger.error(
                            f"YouTube connection test error: {test_result['error']}"
                        )

                    # Refresh the status display
                    self._refresh_status()

                except Exception as e:
                    logger.error(f"Error handling YouTube test completion: {str(e)}")
                finally:
                    self._cleanup_test()

            # Execute completion handler after a delay
            layout_schedule(1.0, handle_test_completion, once=True)

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                f"Error handling YouTube test connection: {str(e)}", exc_info=True
            )
            notify(f"Error starting YouTube test: {str(e)}", type="negative")
            self._cleanup_test()

    def _cleanup_test(self) -> None:
        """Clean up after YouTube connection test"""
        try:
            # Mark test as no longer in progress
            self._test_in_progress = False

            # Reset button state
            if "test_button" in self.ui_elements:
                self.ui_elements["test_button"].set_text("Test")
                self.ui_elements["test_button"].enable()
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error cleaning up YouTube test: {str(e)}")

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False

    def _persist_credentials_to_state(self) -> None:
        """Write UI OAuth credentials to YouTubeData and api_credentials.json."""
        client_id = self._creds.get("client_id", "")
        client_secret = self._creds.get("client_secret", "")
        if self.buffer:
            self.buffer.oauth_client_id = client_id
            self.buffer.oauth_client_secret = client_secret
        state_manager.update_youtube_field("oauth_client_id", client_id)
        state_manager.update_youtube_field("oauth_client_secret", client_secret)
        if self.buffer is not None:
            state_manager.update_youtube_field(
                "live_chat_enabled", bool(self.buffer.live_chat_enabled)
            )
        api_credentials_manager.update_youtube_credentials(
            client_id=client_id,
            client_secret=client_secret,
        )
        state_manager.save_changes()
        try:
            from ... import youtube

            youtube.restart_youtube_live_chat_if_needed()
        except Exception:
            pass

    def _handle_oauth_connection(self) -> None:
        """Connect / reconnect Google OAuth for live chat (Spotify-style)."""
        try:
            if self._oauth_in_progress:
                notify("YouTube OAuth already in progress...", type="info")
                return

            self._persist_credentials_to_state()
            client_id = (self._creds.get("client_id") or "").strip()
            client_secret = (self._creds.get("client_secret") or "").strip()
            if not client_id or not client_secret:
                notify(
                    "Enter OAuth Client ID and Client Secret first, then Save or Connect.",
                    type="warning",
                )
                return

            self._oauth_in_progress = True
            if "connect_button" in self.ui_elements:
                self.ui_elements["connect_button"].set_text("Connecting...")
                self.ui_elements["connect_button"].disable()

            notify("Opening browser for YouTube authorization...", type="info")

            oauth_result = {
                "status": "connecting",
                "success": None,
                "error": None,
            }

            def oauth_thread():
                try:
                    from ... import youtube

                    success = youtube.trigger_youtube_oauth_flow()
                    oauth_result["status"] = "complete"
                    oauth_result["success"] = success
                except Exception as e:
                    logger.error(
                        "Error in YouTube OAuth thread: %s", e, exc_info=True
                    )
                    oauth_result["status"] = "error"
                    oauth_result["error"] = str(e)

            import threading

            oauth_worker = threading.Thread(target=oauth_thread, daemon=True)
            oauth_worker.start()

            def check_oauth_result():
                if oauth_result["status"] == "connecting":
                    return
                if oauth_result["status"] == "complete":
                    if oauth_result["success"]:
                        notify(
                            "YouTube live chat authorized successfully!",
                            type="positive",
                            timeout=4000,
                        )
                        self._load_from_state()
                        if "oauth_client_id" in self.ui_elements:
                            self.ui_elements["oauth_client_id"].value = (
                                self._creds.get("client_id", "")
                            )
                        if "oauth_client_secret" in self.ui_elements:
                            self.ui_elements["oauth_client_secret"].value = (
                                self._creds.get("client_secret", "")
                            )
                    else:
                        notify(
                            "YouTube authorization failed or was cancelled.",
                            type="negative",
                            timeout=5000,
                        )
                    self._cleanup_oauth()
                    self._refresh_status()
                elif oauth_result["status"] == "error":
                    notify(
                        f"Error during YouTube OAuth: {oauth_result['error']}",
                        type="negative",
                    )
                    self._cleanup_oauth()
                    self._refresh_status()

            timer = layout_schedule(0.25, check_oauth_result)
            self._active_timers.append(timer)
        except Exception as e:
            logger.error("Error starting YouTube OAuth: %s", e, exc_info=True)
            notify(f"Error starting YouTube OAuth: {e}", type="negative")
            self._cleanup_oauth()

    def _cleanup_oauth(self) -> None:
        self._oauth_in_progress = False
        if "connect_button" in self.ui_elements:
            self.ui_elements["connect_button"].enable()
            self.ui_elements["connect_button"].set_text("Connect")
        for t in self._active_timers:
            try:
                t.active = False
            except Exception:
                pass
        self._active_timers.clear()

    def _handle_oauth_disconnect(self) -> None:
        try:
            from ... import youtube

            youtube.disconnect_youtube_oauth()
            self._load_from_state()
            notify("YouTube live chat disconnected", type="info")
            self._refresh_status()
        except Exception as e:
            logger.error("Error disconnecting YouTube OAuth: %s", e, exc_info=True)
            notify(f"Error disconnecting: {e}", type="negative")

    def _on_playlist_input_enter(self, _event=None) -> None:
        """Handle Enter key in the playlist filter input."""
        if not self._playlist_input:
            return
        name = (self._playlist_input.value or "").strip()
        if not name:
            return
        self._playlist_input.value = ""
        self._add_playlist_filter(name)

    def _add_playlist_filter(self, name: str) -> None:
        """Add a playlist name to the exclusion list and create its chip."""
        if not self.buffer:
            return
        if name in self.buffer.playlist_filter:
            notify(f"'{name}' is already in the filter list", type="warning")
            return
        self.buffer.playlist_filter.append(name)
        self.dirty = True
        self._create_chip(name)

    def _remove_playlist_filter(self, name: str) -> None:
        """Remove a playlist name from the exclusion list and rebuild chips."""
        if not self.buffer:
            return
        if name in self.buffer.playlist_filter:
            self.buffer.playlist_filter.remove(name)
            self.dirty = True
        self._rebuild_playlist_chips()

    def _create_chip(self, name: str) -> None:
        """Create a single removable chip element inside the chip container."""
        if not self._playlist_chip_container:
            return
        with self._playlist_chip_container:
            with (
                ui.element("div")
                .classes(THEME_CHIP_CLASSES)
                .style("white-space: nowrap;")
            ):
                ui.label(name).classes("text-sm").style("white-space: nowrap;")
                ui.button(
                    icon="close",
                    on_click=lambda _e, n=name: self._remove_playlist_filter(n),
                ).props("flat dense round size=xs")

    def _rebuild_playlist_chips(self) -> None:
        """Clear and recreate all playlist chips from the buffer."""
        if not self._playlist_chip_container:
            return
        self._playlist_chip_container.clear()
        current_list: List[str] = self.buffer.playlist_filter if self.buffer else []
        for name in current_list:
            self._create_chip(name)

    def build(self, parent_container) -> None:
        self._load_from_state()
        with settings_surface(parent_container):

            with settings_status_band():
                with ui.column().classes("gap-0"):
                    ui.label("Status").classes("text-xs secondary-text")
                    self.ui_elements["status_label"] = ui.label(
                        "Loading..."
                    ).classes("font-semibold text-sm")
                with ui.column().classes("gap-0 flex-1 min-w-[10rem]"):
                    ui.label("Channel").classes("text-xs secondary-text")
                    self.ui_elements["channel_label"] = ui.label("N/A").classes(
                        "font-semibold text-sm"
                    )
                with ui.column().classes("gap-0 flex-1 min-w-[10rem]"):
                    ui.label("Latest video").classes("text-xs secondary-text")
                    self.ui_elements["video_label"] = ui.label("N/A").classes(
                        "font-semibold text-sm"
                    )

            with settings_form_grid(columns=2):
                self.ui_elements["api_key"] = form_sensitive_input(
                    tooltip="YouTube Data API v3 key for channel and video lookups",
                    label="API key",
                    value=getattr(self.buffer, "api_key", ""),
                    placeholder="YouTube Data API v3 Key",
                    on_change=lambda e: self._set(
                        "api_key", "" if e.value is None else str(e.value)
                    ),
                )
            self.ui_elements["channel_urls"] = form_sensitive_input(
                tooltip="Pipe-separated YouTube channel URLs to monitor",
                label="Channel URLs",
                value=getattr(self.buffer, "channel_urls", ""),
                placeholder="https://youtube.com/@Channel|https://...",
                on_change=lambda e: self._set(
                    "channel_urls", "" if e.value is None else str(e.value)
                ),
            )

            with settings_section(
                "Playlist filter (exclude)",
                subtitle="Press Enter to add a playlist name",
            ):
                self._playlist_chip_container = theme_chip_row()
                self._rebuild_playlist_chips()
                self._playlist_input = form_sensitive_input(
                    tooltip="Playlist title to exclude from latest-video selection; press Enter to add",
                    placeholder="Playlist name",
                )
                self._playlist_input.on("keydown.enter", self._on_playlist_input_enter)

            with settings_section(
                "Live chat & alerts",
                subtitle=(
                    "Google OAuth for live chat, memberships, and Super Chats "
                    f"(redirect URI: {YOUTUBE_OAUTH_REDIRECT_URI})"
                ),
            ):
                with ui.row().classes("w-full items-center gap-4 mb-2"):
                    with ui.column().classes("gap-0 flex-1 min-w-[10rem]"):
                        ui.label("Live status").classes("text-xs secondary-text")
                        self.ui_elements["live_status_label"] = ui.label(
                            "Not authorized"
                        ).classes("font-semibold text-sm")

                with settings_form_grid(columns=2):
                    self.ui_elements["oauth_client_id"] = form_sensitive_input(
                        tooltip=(
                            "Google OAuth Client ID (Web application). "
                            "Stored in api_credentials.json like Spotify."
                        ),
                        label="OAuth Client ID",
                        value=self._creds.get("client_id", ""),
                        placeholder="xxxx.apps.googleusercontent.com",
                    )
                    self.ui_elements["oauth_client_id"].on_value_change(
                        lambda e: self._set_cred(
                            "client_id", self._str_from_value_event(e)
                        )
                    )
                    self.ui_elements["oauth_client_secret"] = form_sensitive_input(
                        tooltip="Google OAuth Client Secret",
                        label="OAuth Client Secret",
                        value=self._creds.get("client_secret", ""),
                        placeholder="GOCSPX-...",
                    )
                    self.ui_elements["oauth_client_secret"].on_value_change(
                        lambda e: self._set_cred(
                            "client_secret", self._str_from_value_event(e)
                        )
                    )

                with ui.row().classes("w-full items-center gap-3 mt-2"):
                    self.ui_elements["live_chat_enabled"] = ui.switch(
                        "Enable live chat poller",
                        value=bool(
                            getattr(self.buffer, "live_chat_enabled", True)
                        ),
                        on_change=lambda e: self._set(
                            "live_chat_enabled", bool(e.value)
                        ),
                    ).tooltip(
                        "When on and authorized, poll live chat for messages and alerts. "
                        "Chat overlay display is controlled separately in the chat template "
                        "(EnableYouTubeChat, default off)."
                    )
                    self.ui_elements["alerts_enabled"] = ui.switch(
                        "Process YouTube alerts",
                        value=bool(getattr(self.buffer, "alerts_enabled", True)),
                        on_change=lambda e: self._set(
                            "alerts_enabled", bool(e.value)
                        ),
                    ).tooltip(
                        "When off, memberships and Super Chats still reach chat, "
                        "Connectors, and Chatbot — but skip the alert queue, "
                        "instant alerts, and activity feed."
                    )

                with ui.row().classes(
                    "button-row w-full justify-end gap-2 mt-2 flex-wrap"
                ):
                    outline_button(
                        "Disconnect",
                        self._handle_oauth_disconnect,
                        icon="logout",
                    )
                    self.ui_elements["connect_button"] = primary_button(
                        "Connect",
                        self._handle_oauth_connection,
                        icon="login",
                    )

            with ui.row().classes(
                "button-row w-full justify-end gap-2 mt-1 flex-wrap"
            ):
                self.ui_elements["refresh_button"] = outline_button(
                    "Refresh",
                    self._refresh_status,
                    icon="refresh",
                )
                self.ui_elements["test_button"] = outline_button(
                    "Test",
                    self._test_connection,
                    icon="wifi_tethering",
                )
                outline_button("Discard", self.discard)
                primary_button("Save", self.save)
            self._status_timer = layout_schedule(5.0, self._refresh_status, active=True)

        self._refresh_status()

    def _load_from_state(self) -> None:
        yt = state_manager.get_youtube_data()
        self.buffer = dataobjects.YouTubeData(
            **{
                field.name: getattr(yt, field.name)
                for field in YouTubeData.__dataclass_fields__.values()
            }
        )
        self._creds = dict(api_credentials_manager.get_youtube_credentials())
        # Prefer api_credentials; fall back to YouTubeData if credentials file empty
        if not (self._creds.get("client_id") or "").strip() and (
            self.buffer.oauth_client_id or ""
        ).strip():
            self._creds["client_id"] = self.buffer.oauth_client_id
        if not (self._creds.get("client_secret") or "").strip() and (
            self.buffer.oauth_client_secret or ""
        ).strip():
            self._creds["client_secret"] = self.buffer.oauth_client_secret
        self.dirty = False

    def _set(self, field: str, value) -> None:
        if getattr(self.buffer, field) != value:
            setattr(self.buffer, field, value)
            self.dirty = True

    def _set_cred(self, field: str, value: str) -> None:
        if self._creds.get(field) != value:
            self._creds[field] = value
            self.dirty = True

    def save(self) -> None:
        if not self.buffer:
            return
        self._persist_credentials_to_state()
        yt_dict = {
            field.name: getattr(self.buffer, field.name)
            for field in YouTubeData.__dataclass_fields__.values()
        }
        # Ensure oauth fields match persisted credentials
        yt_dict["oauth_client_id"] = self._creds.get("client_id", "")
        yt_dict["oauth_client_secret"] = self._creds.get("client_secret", "")
        state_manager.set_youtube_data(yt_dict)
        if state_manager.save_changes():
            try:
                from ... import youtube

                youtube.restart_youtube_live_chat_if_needed()
            except Exception:
                pass
            notify("YouTube saved", type="positive")
            self.dirty = False
        else:
            notify("Error saving YouTube", type="negative")

    def discard(self) -> None:
        self._load_from_state()
        for key, element in self.ui_elements.items():
            if key == "oauth_client_id" and hasattr(element, "value"):
                element.value = self._creds.get("client_id", "")
            elif key == "oauth_client_secret" and hasattr(element, "value"):
                element.value = self._creds.get("client_secret", "")
            elif key == "live_chat_enabled" and hasattr(element, "value"):
                element.value = bool(
                    getattr(self.buffer, "live_chat_enabled", True)
                )
            elif hasattr(element, "value") and hasattr(self.buffer, key):
                val = getattr(self.buffer, key)
                element.value = "" if val is None else val
        self._rebuild_playlist_chips()
        self.dirty = False

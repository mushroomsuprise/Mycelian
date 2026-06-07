from __future__ import annotations

from typing import Dict, Any, List, Optional

from nicegui import ui

from ...ui_buttons import outline_button, primary_button
from ...ui_form_controls import form_input
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

from ... import dataobjects
from ...dataobjects import state_manager, YouTubeData


class YouTubeTab:
    name = "YouTube"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.YouTubeData] = None
        self.ui_elements: Dict[str, Any] = {}
        self._playlist_chip_container: Optional[ui.row] = None
        self._playlist_input: Optional[ui.input] = None
        self._status_timer: Optional[Any] = None

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

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
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
            ui.label("YouTube Integration").classes("text-lg font-bold")

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
                self.ui_elements["api_key"] = form_input(
                    tooltip="YouTube Data API v3 key for channel and video lookups",
                    label="API key",
                    value=getattr(self.buffer, "api_key", ""),
                    password=True,
                    placeholder="YouTube Data API v3 Key",
                )
                self.ui_elements["api_key"].props("password-toggle-button")
                self.ui_elements["api_key"].on(
                    "change",
                    lambda e: self._set(
                        "api_key",
                        getattr(e, "args", [getattr(e, "value", "")])[0] or "",
                    ),
                )
            self.ui_elements["channel_urls"] = form_input(
                tooltip="Pipe-separated YouTube channel URLs to monitor",
                label="Channel URLs",
                value=getattr(self.buffer, "channel_urls", ""),
                placeholder="https://youtube.com/@Channel|https://...",
            )
            self.ui_elements["channel_urls"].on(
                "change",
                lambda e: self._set(
                    "channel_urls",
                    getattr(e, "args", [getattr(e, "value", "")])[0] or "",
                ),
            )

            with settings_section(
                "Playlist filter (exclude)",
                subtitle="Press Enter to add a playlist name",
            ):
                self._playlist_chip_container = theme_chip_row()
                self._rebuild_playlist_chips()
                self._playlist_input = form_input(
                    tooltip="Playlist title to exclude from latest-video selection; press Enter to add",
                    placeholder="Playlist name",
                )
                self._playlist_input.on("keydown.enter", self._on_playlist_input_enter)

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
        self.dirty = False

    def _set(self, field: str, value) -> None:
        if getattr(self.buffer, field) != value:
            setattr(self.buffer, field, value)
            self.dirty = True

    def save(self) -> None:
        if not self.buffer:
            return
        # convert dataclass to dict expected by state_manager.set_youtube_data
        yt_dict = {
            field.name: getattr(self.buffer, field.name)
            for field in YouTubeData.__dataclass_fields__.values()
        }
        state_manager.set_youtube_data(yt_dict)
        if state_manager.save_changes():
            notify("YouTube saved", type="positive")
            self.dirty = False
        else:
            notify("Error saving YouTube", type="negative")

    def discard(self) -> None:
        self._load_from_state()
        for key, element in self.ui_elements.items():
            if hasattr(element, "value") and hasattr(self.buffer, key):
                element.value = getattr(self.buffer, key) or ""
        self._rebuild_playlist_chips()
        self.dirty = False

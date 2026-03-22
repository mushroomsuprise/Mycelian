from __future__ import annotations

from typing import Dict, Any, List, Optional

from nicegui import ui

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

    def on_enter(self) -> None:
        # Refresh status when tab becomes active (delayed to avoid spam)
        from nicegui import ui

        ui.timer(2.0, lambda: self._refresh_status(), once=True)

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
                ui.notify("YouTube test already in progress...", type="info")
                return

            # Mark test as in progress
            self._test_in_progress = True

            # Update button state to show it's working
            if "test_button" in self.ui_elements:
                self.ui_elements["test_button"].set_text("Testing...")
                self.ui_elements["test_button"].disable()

            # Show a notification that the process is starting
            ui.notify("Testing YouTube connection...", type="info")

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
                            ui.notify(
                                "YouTube connection successful!",
                                type="positive",
                                timeout=3000,
                            )
                            logger.info(
                                "YouTube connection test completed successfully"
                            )
                        else:
                            ui.notify(
                                "YouTube connection failed. Please check your API key and channel URLs.",
                                type="negative",
                                timeout=5000,
                            )
                            logger.warning("YouTube connection test failed")

                    elif test_result["status"] == "error":
                        ui.notify(
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
            ui.timer(1.0, handle_test_completion, once=True)

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                f"Error handling YouTube test connection: {str(e)}", exc_info=True
            )
            ui.notify(f"Error starting YouTube test: {str(e)}", type="negative")
            self._cleanup_test()

    def _cleanup_test(self) -> None:
        """Clean up after YouTube connection test"""
        try:
            # Mark test as no longer in progress
            self._test_in_progress = False

            # Reset button state
            if "test_button" in self.ui_elements:
                self.ui_elements["test_button"].set_text("Test Connection")
                self.ui_elements["test_button"].enable()
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error cleaning up YouTube test: {str(e)}")

    def on_exit(self) -> None:
        pass

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
            ui.notify(f"'{name}' is already in the filter list", type="warning")
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
            with ui.element("div").classes(
                "flex items-center gap-1 px-3 py-1 rounded-full"
                " bg-blue-500/20 border border-blue-500/40"
            ).style("flex-shrink: 0; white-space: nowrap;"):
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
        current_list: List[str] = (
            self.buffer.playlist_filter if self.buffer else []
        )
        for name in current_list:
            self._create_chip(name)

    def build(self, parent_container) -> None:
        self._load_from_state()
        with parent_container:
            with ui.card().classes("content-section w-full"):
                ui.label("YouTube Integration").classes("text-xl font-bold mb-4")

                with ui.column().classes("w-full gap-4"):
                    with ui.row().classes("w-full gap-4 items-start"):
                        # Column 1: Status information
                        with ui.column().classes("flex-1 gap-2"):
                            ui.label("Status:").classes("text-sm font-medium")
                            self.ui_elements["status_label"] = ui.label(
                                "Loading..."
                            ).classes("font-semibold")

                            ui.label("Channel:").classes("text-sm font-medium")
                            self.ui_elements["channel_label"] = ui.label("N/A").classes(
                                "font-semibold"
                            )

                            ui.label("Latest Video:").classes("text-sm font-medium")
                            self.ui_elements["video_label"] = ui.label("N/A").classes(
                                "font-semibold"
                            )

                        # Column 2: Configuration
                        with ui.column().classes("flex-1 gap-2"):
                            ui.label("API Key:").classes("text-sm font-medium")
                            self.ui_elements["api_key"] = (
                                ui.input(
                                    value=getattr(self.buffer, "api_key", ""),
                                    password=True,
                                    password_toggle_button=True,
                                    placeholder="YouTube Data API v3 Key",
                                )
                                .classes("w-full")
                                .on(
                                    "change",
                                    lambda e: self._set(
                                        "api_key",
                                        getattr(e, "args", [getattr(e, "value", "")])[0]
                                        or "",
                                    ),
                                )
                            )

                            ui.label("Channel URLs:").classes("text-sm font-medium")
                            self.ui_elements["channel_urls"] = (
                                ui.input(
                                    value=getattr(self.buffer, "channel_urls", ""),
                                    placeholder="https://youtube.com/@Channel|https://...",
                                )
                                .classes("w-full")
                                .on(
                                    "change",
                                    lambda e: self._set(
                                        "channel_urls",
                                        getattr(e, "args", [getattr(e, "value", "")])[0]
                                        or "",
                                    ),
                                )
                            )

                            ui.label("Playlist Filter (Exclude):").classes(
                                "text-sm font-medium"
                            )
                            with ui.column().classes("w-full gap-1"):
                                self._playlist_chip_container = ui.row().classes(
                                    "w-full flex-wrap gap-1 items-center min-h-[32px]"
                                )
                                self._rebuild_playlist_chips()
                                self._playlist_input = (
                                    ui.input(placeholder="Type playlist name, press Enter")
                                    .classes("w-full")
                                    .on(
                                        "keydown.enter",
                                        self._on_playlist_input_enter,
                                    )
                                )

                        # Column 3: Connection buttons (stacked vertically)
                        with ui.column().classes("gap-2"):
                            self.ui_elements["test_button"] = (
                                ui.button(
                                    "Test",
                                    on_click=self._test_connection,
                                )
                                .props("icon=wifi_tethering outline")
                                .classes("w-32")
                            )

                            self.ui_elements["refresh_button"] = (
                                ui.button(
                                    "Refresh",
                                    on_click=self._refresh_status,
                                )
                                .props("icon=refresh outline")
                                .classes("w-32")
                            )

                    with ui.row().classes("justify-end gap-2 mt-3"):
                        ui.button("Discard", on_click=self.discard).props("outline")
                        ui.button("Save", on_click=self.save).props("color=primary")

        # Refresh status information after UI is built
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
            ui.notify("YouTube saved", type="positive")
            self.dirty = False
        else:
            ui.notify("Error saving YouTube", type="negative")

    def discard(self) -> None:
        self._load_from_state()
        for key, element in self.ui_elements.items():
            if hasattr(element, "value") and hasattr(self.buffer, key):
                element.value = getattr(self.buffer, key) or ""
        self._rebuild_playlist_chips()
        self.dirty = False

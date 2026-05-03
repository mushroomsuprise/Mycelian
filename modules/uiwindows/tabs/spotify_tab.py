from __future__ import annotations

from typing import Dict, Any, Optional

from nicegui import ui
from ...notification_engine import notify

from ... import dataobjects
from ...dataobjects import state_manager
from ...api_credentials_manager import api_credentials_manager


class SpotifyTab:
    name = "Spotify"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.SpotifyData] = None
        self.ui_elements: Dict[str, Any] = {}
        self._creds: Dict[str, str] = {}

    @staticmethod
    def _str_from_value_event(e: Any) -> str:
        """
        Read full string from input/slider value events. Do not use e.args[0] when
        args is already a str — that would keep only the first character.
        """
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
        # Refresh status when tab becomes active (delayed to avoid spam)
        from nicegui import ui

        ui.timer(2.0, lambda: self._refresh_status(), once=True)

    def _refresh_status(self) -> None:
        """Refresh Spotify status display."""
        try:
            # Force reload the latest Spotify data from state manager
            from ...dataobjects import state_manager

            spotify_data = state_manager.get_spotify_data()

            # Get current status from the Spotify module
            from ... import spotify

            status_info = spotify.get_spotify_status()

            # Also get the connection status directly from our data
            current_connection_status = (
                spotify_data.connection_status if spotify_data else "Unknown"
            )

            # Update market country field if it exists in UI
            if "market_country" in self.ui_elements:
                current_market = (
                    getattr(spotify_data, "market_country", "") if spotify_data else ""
                )
                self.ui_elements["market_country"].value = current_market

            # Update status label and color
            if "status_label" in self.ui_elements:
                # Use the most current status from either source
                status_text = current_connection_status or status_info.get(
                    "status", "Unknown"
                )
                is_authenticated = status_info.get("is_authenticated", False)

                self.ui_elements["status_label"].set_text(status_text)

                # Update color based on status
                if is_authenticated or status_text == "Connected":
                    self.ui_elements["status_label"].classes(
                        replace="font-semibold text-theme-success"
                    )
                else:
                    self.ui_elements["status_label"].classes(
                        replace="font-semibold text-theme-error"
                    )

            # Update current track label - Handle regional restrictions gracefully
            if "track_label" in self.ui_elements:
                current_track = status_info.get("current_track", "N/A")

                # If we get "Nothing playing - Nothing playing" due to regional restrictions,
                # show a more user-friendly message
                if current_track == "Nothing playing - Nothing playing":
                    if status_info.get("is_authenticated", False):
                        current_track = (
                            "Spotify connected (playback may be restricted by region)"
                        )
                    else:
                        current_track = "Not connected"

                self.ui_elements["track_label"].set_text(current_track)

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error refreshing Spotify status: {str(e)}", exc_info=True)
            # Set error status
            if "status_label" in self.ui_elements:
                self.ui_elements["status_label"].set_text("Status Error")
                self.ui_elements["status_label"].classes(
                    replace="font-semibold text-theme-error"
                )

    def on_exit(self) -> None:
        pass

    def build(self, parent_container) -> None:
        self._load_from_state()
        with parent_container:
            with ui.card().classes("content-section w-full"):
                ui.label("Spotify Integration").classes("text-xl font-bold mb-4")

                with ui.column().classes("w-full gap-4"):
                    with ui.row().classes("w-full gap-4 items-start"):
                        # Column 1: Status information
                        with ui.column().classes("flex-1 gap-2"):
                            ui.label("Status:").classes("text-sm font-medium")
                            self.ui_elements["status_label"] = ui.label(
                                "Loading..."
                            ).classes("font-semibold")

                            ui.label("Current Track:").classes("text-sm font-medium")
                            self.ui_elements["track_label"] = ui.label("N/A").classes(
                                "font-semibold"
                            )

                        # Column 2: Credentials and settings
                        with ui.column().classes("flex-1 gap-2"):
                            ui.label("Client ID:").classes("text-sm font-medium")
                            self.ui_elements["client_id"] = (
                                ui.input(
                                    value=self._creds.get("client_id", ""),
                                    placeholder="Spotify API Client ID",
                                )
                                .classes("w-full")
                                .on_value_change(
                                    lambda e: self._set_cred(
                                        "client_id", self._str_from_value_event(e)
                                    )
                                )
                            )

                            ui.label("Client Secret:").classes("text-sm font-medium")
                            self.ui_elements["client_secret"] = (
                                ui.input(
                                    value=self._creds.get("client_secret", ""),
                                    password=True,
                                    password_toggle_button=True,
                                    placeholder="Spotify API Client Secret",
                                )
                                .classes("w-full")
                                .on_value_change(
                                    lambda e: self._set_cred(
                                        "client_secret", self._str_from_value_event(e)
                                    )
                                )
                            )

                            ui.label("Market Country:").classes("text-sm font-medium")
                            markets = {
                                "": "Auto (from account)",
                                "US": "US",
                                "GB": "GB",
                                "CA": "CA",
                            }
                            self.ui_elements["market_country"] = (
                                ui.select(
                                    options=markets,
                                    value=getattr(self.buffer, "market_country", ""),
                                )
                                .classes("w-full")
                                .on_value_change(
                                    lambda e: self._set(
                                        "market_country", self._str_from_value_event(e)
                                    )
                                )
                            )

                        # Column 3: Connection buttons (stacked vertically)
                        with ui.column().classes("gap-2"):
                            self.ui_elements["connect_button"] = (
                                ui.button(
                                    "Connect",
                                    on_click=self._handle_oauth_connection,
                                )
                                .props("icon=login color=primary")
                                .classes("w-32")
                            )

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

    def _load_from_state(self) -> None:
        sp = state_manager.get_spotify_data()
        self.buffer = dataobjects.SpotifyData(
            **{k: getattr(sp, k) for k in sp.__dataclass_fields__.keys()}
        )
        self._creds = dict(api_credentials_manager.get_spotify_credentials())
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
        for field in self.buffer.__dataclass_fields__.keys():
            state_manager.update_spotify_field(field, getattr(self.buffer, field))
        api_credentials_manager.update_spotify_credentials(
            client_id=self._creds.get("client_id", ""),
            client_secret=self._creds.get("client_secret", ""),
        )
        if state_manager.save_changes():
            notify("Spotify saved", type="positive")
            self.dirty = False
        else:
            notify("Error saving Spotify", type="negative")

    def discard(self) -> None:
        self._load_from_state()
        for key, element in self.ui_elements.items():
            if key in self._creds and hasattr(element, "value"):
                element.value = self._creds.get(key, "")
            elif hasattr(self.buffer, key) and hasattr(element, "value"):
                element.value = getattr(self.buffer, key)
        self.dirty = False

    def _handle_oauth_connection(self) -> None:
        """Handle the Connect to Spotify button click to trigger OAuth connection"""
        try:
            import logging

            logger = logging.getLogger(__name__)
            logger.info("User clicked Connect to Spotify button")

            # Prevent duplicate OAuth attempts
            if getattr(self, "_oauth_in_progress", False):
                logger.warning(
                    "Spotify OAuth already in progress, ignoring duplicate request"
                )
                notify("Spotify connection already in progress...", type="info")
                return

            # Check if client ID and secret are provided
            if not self._creds.get("client_id") or not self._creds.get("client_secret"):
                notify(
                    "Please enter your Spotify Client ID and Client Secret first!",
                    type="warning",
                )
                return

            # Mark OAuth as in progress
            self._oauth_in_progress = True

            # Update button state to show it's working
            if "connect_button" in self.ui_elements:
                self.ui_elements["connect_button"].set_text("Connecting...")
                self.ui_elements["connect_button"].disable()

            # Show a notification that the process is starting
            notify("Starting Spotify OAuth connection...", type="info")

            # Save only Spotify settings to avoid triggering database and other notifications
            self._save_settings_only()

            # Start the OAuth connection in a separate thread
            import threading

            # Create shared result holder for thread communication
            oauth_result = {
                "status": "connecting",
                "success": None,
                "error": None,
            }

            def oauth_thread():
                try:
                    from ... import spotify

                    # Update Spotify settings with current values
                    spotify.update_spotify_settings(
                        client_id=self._creds.get("client_id", ""),
                        client_secret=self._creds.get("client_secret", ""),
                    )

                    # Use automatic OAuth flow
                    success = spotify.trigger_automatic_oauth_flow()
                    oauth_result["status"] = "complete"
                    oauth_result["success"] = success

                except Exception as e:
                    logger.error(
                        f"Error in Spotify OAuth thread: {str(e)}", exc_info=True
                    )
                    oauth_result["status"] = "error"
                    oauth_result["error"] = str(e)

            # Start the thread
            oauth_worker = threading.Thread(target=oauth_thread)
            oauth_worker.daemon = True
            oauth_worker.start()

            # Create a timer to check the result from main thread
            check_count = {"count": 0}
            max_checks = 150  # 30 seconds at 0.2 second intervals

            def check_oauth_result():
                check_count["count"] += 1

                # Safety timeout for OAuth (30 seconds)
                if check_count["count"] >= max_checks:
                    logger.warning("Spotify OAuth check timed out")
                    self._cleanup_oauth()
                    notify("Spotify OAuth timed out", type="negative")
                    return False

                if oauth_result["status"] == "connecting":
                    return True  # Continue checking
                elif oauth_result["status"] == "complete":
                    try:
                        if oauth_result["success"]:
                            notify(
                                "Spotify authorization will open in your browser. Please authorize the application.",
                                type="info",
                                timeout=5000,
                            )
                            logger.info(
                                "Spotify automatic OAuth flow started successfully"
                            )
                        else:
                            notify(
                                "Failed to start Spotify authorization. Please check your credentials.",
                                type="negative",
                                timeout=5000,
                            )
                            logger.warning(
                                "Failed to start Spotify automatic OAuth flow"
                            )

                        # Refresh the status display
                        self._refresh_status()

                    except Exception as e:
                        logger.error(f"Error updating UI after Spotify OAuth: {str(e)}")

                    self._cleanup_oauth()
                    return False  # Stop checking
                elif oauth_result["status"] == "error":
                    try:
                        notify(
                            f"Error during Spotify OAuth: {oauth_result['error']}",
                            type="negative",
                        )
                    except Exception as ui_error:
                        logger.error(f"Error showing OAuth error: {str(ui_error)}")
                    self._cleanup_oauth()
                    return False  # Stop checking
                return False  # Default stop

            oauth_timer = ui.timer(0.2, check_oauth_result)
            # Store timer reference for potential cleanup
            self._active_timers = getattr(self, "_active_timers", [])
            self._active_timers.append(oauth_timer)

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                f"Error handling Spotify OAuth connection: {str(e)}", exc_info=True
            )
            notify(f"Error starting Spotify connection: {str(e)}", type="negative")
            self._cleanup_oauth()

    def _cleanup_oauth(self) -> None:
        """Clean up after Spotify OAuth"""
        try:
            # Mark OAuth as no longer in progress
            self._oauth_in_progress = False

            # Reset button state
            if "connect_button" in self.ui_elements:
                self.ui_elements["connect_button"].set_text("Connect to Spotify")
                self.ui_elements["connect_button"].enable()
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error cleaning up Spotify OAuth: {str(e)}")

    def _test_connection(self) -> None:
        """Handle the Test Connection button click for Spotify"""
        try:
            import logging

            logger = logging.getLogger(__name__)
            logger.info("User clicked Test Connection button for Spotify")

            # Prevent duplicate test attempts
            if getattr(self, "_test_in_progress", False):
                logger.warning(
                    "Spotify test already in progress, ignoring duplicate request"
                )
                notify("Spotify test already in progress...", type="info")
                return

            # Mark test as in progress
            self._test_in_progress = True

            # Update button state to show it's working
            if "test_button" in self.ui_elements:
                self.ui_elements["test_button"].set_text("Testing...")
                self.ui_elements["test_button"].disable()

            # Show a notification that the process is starting
            notify("Testing Spotify connection...", type="info")

            # Save only Spotify settings to avoid triggering database and other notifications
            self._save_settings_only()

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
                    from ... import spotify

                    # Test and reconnect if possible
                    success = spotify.test_and_reconnect()
                    test_result["status"] = "complete"
                    test_result["success"] = success

                except Exception as e:
                    logger.error(
                        f"Error in Spotify test thread: {str(e)}", exc_info=True
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
                                "Spotify connection successful!",
                                type="positive",
                                timeout=3000,
                            )
                            logger.info(
                                "Spotify connection test completed successfully"
                            )
                        else:
                            notify(
                                "Spotify connection failed. Please check your credentials and try again.",
                                type="negative",
                                timeout=5000,
                            )
                            logger.warning("Spotify connection test failed")

                    elif test_result["status"] == "error":
                        notify(
                            f"Error testing Spotify connection: {test_result['error']}",
                            type="negative",
                        )
                        logger.error(
                            f"Spotify connection test error: {test_result['error']}"
                        )

                    # Refresh the status display
                    self._refresh_status()

                except Exception as e:
                    logger.error(f"Error handling Spotify test completion: {str(e)}")
                finally:
                    self._cleanup_test()

            # Execute completion handler after a delay
            ui.timer(1.0, handle_test_completion, once=True)

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                f"Error handling Spotify test connection: {str(e)}", exc_info=True
            )
            notify(f"Error starting Spotify test: {str(e)}", type="negative")
            self._cleanup_test()

    def _cleanup_test(self) -> None:
        """Clean up after Spotify connection test"""
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
            logger.error(f"Error cleaning up Spotify test: {str(e)}")

    def _save_settings_only(self) -> None:
        """Save only Spotify credentials without triggering full save"""
        try:
            # Persist credentials only
            from ... import api_credentials_manager

            api_credentials_manager.update_spotify_credentials(
                client_id=self._creds.get("client_id", ""),
                client_secret=self._creds.get("client_secret", ""),
            )
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error saving Spotify settings only: {str(e)}", exc_info=True)

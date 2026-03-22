from __future__ import annotations

from typing import Dict, Any, Optional

from nicegui import ui

from ... import dataobjects
from ...dataobjects import state_manager
from ...api_credentials_manager import api_credentials_manager
from ...help_system.contextual_help import help_button


class StreamlabsTab:
    name = "Streamlabs"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.StreamlabsData] = None
        self.ui_elements: Dict[str, Any] = {}
        self._creds: Dict[str, str] = {}

    def on_enter(self) -> None:
        # Refresh status when tab becomes active
        from nicegui import ui

        ui.timer(0.1, lambda: self._refresh_status(), once=True)

    def _refresh_status(self) -> None:
        """Refresh Streamlabs status display."""
        try:
            # Get current status from the Streamlabs module
            from ... import streamlabs

            status_info = streamlabs.get_streamlabs_status()

            # Update status label and color
            if "status_label" in self.ui_elements:
                status_text = status_info.get("status", "Unknown")
                is_authenticated = status_info.get("is_authenticated", False)
                is_connected = status_info.get("is_connected", False)

                self.ui_elements["status_label"].set_text(status_text)

                # Update color based on status
                if is_connected and is_authenticated:
                    # Both authenticated and connected - best state
                    self.ui_elements["status_label"].classes(
                        replace="font-semibold text-theme-success"
                    )
                elif is_authenticated:
                    # Authenticated but may not be connected
                    if "Disconnected" in status_text or "Connection" in status_text:
                        self.ui_elements["status_label"].classes(
                            replace="font-semibold text-theme-warning"
                        )
                    else:
                        self.ui_elements["status_label"].classes(
                            replace="font-semibold text-theme-success"
                        )
                elif is_connected:
                    # Connected but not authenticated (using static token)
                    self.ui_elements["status_label"].classes(
                        replace="font-semibold text-theme-info"
                    )
                elif "Reconnection Required" in status_text:
                    # Need to reconnect
                    self.ui_elements["status_label"].classes(
                        replace="font-semibold text-theme-warning"
                    )
                else:
                    # Not connected or error
                    self.ui_elements["status_label"].classes(
                        replace="font-semibold text-theme-error"
                    )

            # Update last update label
            if "last_update_label" in self.ui_elements:
                last_update = status_info.get("last_update", "Never")
                self.ui_elements["last_update_label"].set_text(last_update)

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error refreshing Streamlabs status: {str(e)}", exc_info=True)
            # Set error status
            if "status_label" in self.ui_elements:
                self.ui_elements["status_label"].set_text("Status Error")
                self.ui_elements["status_label"].classes(
                    replace="font-semibold text-theme-error"
                )

    def _handle_oauth_connection(self) -> None:
        """Handle the Connect to Streamlabs button click to trigger OAuth connection"""
        try:
            import logging

            logger = logging.getLogger(__name__)
            logger.info("User clicked Connect to Streamlabs button")

            # Prevent duplicate OAuth attempts
            if getattr(self, "_oauth_in_progress", False):
                logger.warning(
                    "Streamlabs OAuth already in progress, ignoring duplicate request"
                )
                ui.notify("Streamlabs connection already in progress...", type="info")
                return

            # Mark OAuth as in progress
            self._oauth_in_progress = True

            # Update button state to show it's working
            if "connect_button" in self.ui_elements:
                self.ui_elements["connect_button"].set_text("Connecting...")
                self.ui_elements["connect_button"].disable()

            # Show a notification that the process is starting
            ui.notify("Starting Streamlabs OAuth connection...", type="info")

            # Save only Streamlabs settings to avoid triggering database and other notifications
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
                    from ... import streamlabs

                    # Update Streamlabs settings with current values
                    streamlabs.update_streamlabs_settings(
                        client_id=self._creds.get("client_id", ""),
                        client_secret=self._creds.get("client_secret", ""),
                    )

                    # Trigger OAuth flow
                    success = streamlabs.trigger_oauth_connection()
                    oauth_result["status"] = "complete"
                    oauth_result["success"] = success

                except Exception as e:
                    logger.error(
                        f"Error in Streamlabs OAuth thread: {str(e)}", exc_info=True
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
                    logger.warning("Streamlabs OAuth check timed out")
                    self._cleanup_oauth()
                    ui.notify("Streamlabs OAuth timed out", type="negative")
                    return False

                if oauth_result["status"] == "connecting":
                    return True  # Continue checking
                elif oauth_result["status"] == "complete":
                    try:
                        if oauth_result["success"]:
                            ui.notify(
                                "Successfully connected to Streamlabs!",
                                type="positive",
                                timeout=3000,
                            )
                            logger.info("Streamlabs OAuth connection successful")
                        else:
                            ui.notify(
                                "Failed to connect to Streamlabs. Please check your credentials and try again.",
                                type="negative",
                                timeout=5000,
                            )
                            logger.error("Streamlabs OAuth connection failed")

                        # Refresh the status display and reset button
                        self._refresh_status()

                    except Exception as e:
                        logger.error(
                            f"Error updating UI after Streamlabs OAuth: {str(e)}"
                        )
                    finally:
                        self._cleanup_oauth()
                    return False  # Stop checking
                elif oauth_result["status"] == "error":
                    try:
                        ui.notify(
                            f"Error during Streamlabs connection: {oauth_result['error']}",
                            type="negative",
                        )
                    except Exception as ui_error:
                        logger.error(f"Error showing OAuth error: {str(ui_error)}")
                    finally:
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
                f"Error handling Streamlabs OAuth connection: {str(e)}", exc_info=True
            )
            ui.notify(
                f"Error starting Streamlabs connection: {str(e)}", type="negative"
            )
            self._cleanup_oauth()

    def _cleanup_oauth(self) -> None:
        """Clean up after Streamlabs OAuth"""
        try:
            # Mark OAuth as no longer in progress
            self._oauth_in_progress = False

            # Reset button state
            if "connect_button" in self.ui_elements:
                self.ui_elements["connect_button"].set_text("Connect to Streamlabs")
                self.ui_elements["connect_button"].enable()
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error cleaning up Streamlabs OAuth: {str(e)}")

    def _save_settings_only(self) -> None:
        """Save only Streamlabs credentials without triggering full save"""
        try:
            # Persist credentials only
            from ... import api_credentials_manager

            api_credentials_manager.update_streamlabs_credentials(
                client_id=self._creds.get("client_id", ""),
                client_secret=self._creds.get("client_secret", ""),
                socket_token=self._creds.get("socket_token", ""),
            )
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                f"Error saving Streamlabs settings only: {str(e)}", exc_info=True
            )

    def on_exit(self) -> None:
        pass

    def build(self, parent_container) -> None:
        self._load_from_state()
        with parent_container:
            with ui.card().classes("content-section w-full"):
                with ui.row().classes("w-full justify-between items-center mb-4"):
                    ui.label("Streamlabs Integration").classes("text-xl font-bold")
                    help_button(tooltip="Streamlabs setup help")

                with ui.column().classes("w-full gap-4"):
                    with ui.row().classes("w-full gap-4 items-start"):
                        # Column 1: Status information
                        with ui.column().classes("flex-1 gap-2"):
                            ui.label("Status:").classes("text-sm font-medium")
                            self.ui_elements["status_label"] = ui.label(
                                "Loading..."
                            ).classes("font-semibold")

                            ui.label("Last Update:").classes("text-sm font-medium")
                            self.ui_elements["last_update_label"] = ui.label(
                                "Never"
                            ).classes("secondary-text text-sm")

                        # Column 2: Credentials
                        with ui.column().classes("flex-1 gap-2"):
                            ui.label("Socket Token:").classes("text-sm font-medium")
                            self.ui_elements["socket_token"] = (
                                ui.input(
                                    value=self._creds.get("socket_token", ""),
                                    password=True,
                                    password_toggle_button=True,
                                    placeholder="Streamlabs socket token (optional)",
                                )
                                .classes("w-full")
                                .on(
                                    "change",
                                    lambda e: self._set_cred(
                                        "socket_token",
                                        getattr(e, "args", [getattr(e, "value", "")])[0]
                                        or "",
                                    ),
                                )
                            )

                            ui.label("Client ID:").classes("text-sm font-medium")
                            self.ui_elements["client_id"] = (
                                ui.input(
                                    value=self._creds.get("client_id", ""),
                                    placeholder="Streamlabs API Client ID",
                                )
                                .classes("w-full")
                                .on(
                                    "change",
                                    lambda e: self._set_cred(
                                        "client_id",
                                        getattr(e, "args", [getattr(e, "value", "")])[0]
                                        or "",
                                    ),
                                )
                            )

                            ui.label("Client Secret:").classes("text-sm font-medium")
                            self.ui_elements["client_secret"] = (
                                ui.input(
                                    value=self._creds.get("client_secret", ""),
                                    password=True,
                                    password_toggle_button=True,
                                    placeholder="Streamlabs API Client Secret",
                                )
                                .classes("w-full")
                                .on(
                                    "change",
                                    lambda e: self._set_cred(
                                        "client_secret",
                                        getattr(e, "args", [getattr(e, "value", "")])[0]
                                        or "",
                                    ),
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
        sl = state_manager.get_streamlabs_data()
        self.buffer = dataobjects.StreamlabsData(
            **{k: getattr(sl, k) for k in sl.__dataclass_fields__.keys()}
        )
        self._creds = dict(api_credentials_manager.get_streamlabs_credentials())
        self.dirty = False

    def _set_cred(self, field: str, value: str) -> None:
        if self._creds.get(field) != value:
            self._creds[field] = value
            self.dirty = True

    def save(self) -> None:
        if not self.buffer:
            return
        # persist streamlabs data
        for field in self.buffer.__dataclass_fields__.keys():
            state_manager.update_streamlabs_field(field, getattr(self.buffer, field))

        # persist credentials
        api_credentials_manager.update_streamlabs_credentials(
            client_id=self._creds.get("client_id", ""),
            client_secret=self._creds.get("client_secret", ""),
            socket_token=self._creds.get("socket_token", ""),
        )

        if state_manager.save_changes():
            ui.notify("Streamlabs saved", type="positive")
            self.dirty = False
        else:
            ui.notify("Error saving Streamlabs", type="negative")

    def discard(self) -> None:
        self._load_from_state()
        for key, element in self.ui_elements.items():
            if hasattr(element, "value") and key in self._creds:
                element.value = self._creds.get(key, "")
        self.dirty = False

from __future__ import annotations

from typing import Any, Dict, Optional

from nicegui import ui
from ...notification_engine import notify
from ...ui_buttons import outline_button, primary_button
from ...ui_form_controls import form_input
from ...ui_settings_layout import (
    settings_footer,
    settings_form_grid,
    settings_inner_panel,
    settings_status_band,
    settings_surface,
    settings_toolbar,
)

from ... import dataobjects
from ...api_credentials_manager import api_credentials_manager
from ...dataobjects import state_manager


class TwitchTab:
    name = "Twitch"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.TwitchData] = None
        self.ui_elements: Dict[str, Any] = {}
        self._creds: Dict[str, str] = {}
        self._status_timer: Optional[Any] = None

    def on_enter(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = True
        ui.timer(0.05, self._refresh_status, once=True)

    def _refresh_main_status(self) -> None:
        """Refresh main Twitch account status display."""
        try:
            # Get current status from the Twitch module
            from ... import twitch

            status_info = twitch.get_twitch_connection_status()

            # Update status label and color
            if "main_status_label" in self.ui_elements:
                status_text = status_info.get("status", "Unknown")
                is_valid = status_info.get("is_valid", False)

                self.ui_elements["main_status_label"].set_text(status_text)

                # Update color based on status
                if is_valid:
                    self.ui_elements["main_status_label"].classes(
                        replace="font-semibold text-theme-success"
                    )
                else:
                    self.ui_elements["main_status_label"].classes(
                        replace="font-semibold text-theme-error"
                    )

            # Update user label
            if "main_user_name_label" in self.ui_elements:
                user_name = status_info.get("user_name", "N/A")
                self.ui_elements["main_user_name_label"].set_text(user_name)

            # Update last update label
            if "main_last_update_label" in self.ui_elements:
                last_update = status_info.get("last_update", "Never")
                self.ui_elements["main_last_update_label"].set_text(last_update)

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                f"Error refreshing main Twitch status: {str(e)}", exc_info=True
            )
            # Set error status
            if "main_status_label" in self.ui_elements:
                self.ui_elements["main_status_label"].set_text("Status Error")
                self.ui_elements["main_status_label"].classes(
                    replace="font-semibold text-theme-error"
                )

    def _refresh_chatbot_status(self) -> None:
        """Refresh chatbot Twitch account status display."""
        try:
            # Get current status from the chatbot module
            from ... import chatbot

            status_info = chatbot.get_chatbot_connection_status()

            # Update status label and color
            if "chatbot_status_label" in self.ui_elements:
                status_text = status_info.get("status", "Unknown")
                is_valid = status_info.get("is_valid", False)

                self.ui_elements["chatbot_status_label"].set_text(status_text)

                # Update color based on status
                if is_valid:
                    self.ui_elements["chatbot_status_label"].classes(
                        replace="font-semibold text-theme-success"
                    )
                else:
                    self.ui_elements["chatbot_status_label"].classes(
                        replace="font-semibold text-theme-error"
                    )

            # Update user label
            if "chatbot_user_name_label" in self.ui_elements:
                user_name = status_info.get("user_name", "N/A")
                self.ui_elements["chatbot_user_name_label"].set_text(user_name)

            # Update last update label
            if "chatbot_last_update_label" in self.ui_elements:
                last_update = status_info.get("last_update", "Never")
                self.ui_elements["chatbot_last_update_label"].set_text(last_update)

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error refreshing chatbot status: {str(e)}", exc_info=True)
            # Set error status
            if "chatbot_status_label" in self.ui_elements:
                self.ui_elements["chatbot_status_label"].set_text("Status Error")
                self.ui_elements["chatbot_status_label"].classes(
                    replace="font-semibold text-theme-error"
                )

    def _refresh_status(self) -> None:
        """Refresh both main account and chatbot status displays."""
        self._refresh_main_status()
        self._refresh_chatbot_status()

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False

    def _build_account_panel(
        self,
        title: str,
        prefix: str,
        *,
        connect_handler,
        refresh_handler,
        client_id_key: str,
        client_secret_key: str,
        client_id_placeholder: str,
        client_secret_placeholder: str,
    ) -> None:
        with settings_inner_panel():
            ui.label(title).classes("text-base font-semibold")
            with settings_toolbar():
                self.ui_elements[f"{prefix}_connect_button"] = primary_button(
                    "Connect",
                    connect_handler,
                    icon="login",
                )
                self.ui_elements[f"{prefix}_refresh_button"] = outline_button(
                    "Refresh",
                    refresh_handler,
                    icon="refresh",
                )
            with settings_status_band():
                with ui.column().classes("gap-0"):
                    ui.label("Status").classes("text-xs secondary-text")
                    self.ui_elements[f"{prefix}_status_label"] = ui.label(
                        "Loading..."
                    ).classes("font-semibold text-sm")
                with ui.column().classes("gap-0"):
                    ui.label("User").classes("text-xs secondary-text")
                    self.ui_elements[f"{prefix}_user_name_label"] = ui.label(
                        "N/A"
                    ).classes("font-semibold text-sm")
                with ui.column().classes("gap-0"):
                    ui.label("Updated").classes("text-xs secondary-text")
                    self.ui_elements[f"{prefix}_last_update_label"] = ui.label(
                        "Never"
                    ).classes("secondary-text text-sm")
            with settings_form_grid(columns=2):
                self.ui_elements[client_id_key] = form_input(
                    tooltip="Twitch application Client ID from the developer console",
                    label="Client ID",
                    value=self._creds.get(client_id_key, ""),
                    placeholder=client_id_placeholder,
                )
                self.ui_elements[client_id_key].on(
                    "change",
                    lambda e, k=client_id_key: self._set_cred(k, e.args or ""),
                )
                self.ui_elements[client_secret_key] = form_input(
                    tooltip="Twitch application Client Secret (keep private)",
                    label="Client Secret",
                    value=self._creds.get(client_secret_key, ""),
                    password=True,
                    placeholder=client_secret_placeholder,
                )
                self.ui_elements[client_secret_key].props("password-toggle-button")
                self.ui_elements[client_secret_key].on(
                    "change",
                    lambda e, k=client_secret_key: self._set_cred(k, e.args or ""),
                )

    def build(self, parent_container) -> None:
        self._load_from_state()
        with settings_surface(parent_container):
            ui.label("Twitch Integration").classes("text-lg font-bold")

            with ui.grid(columns=2).classes("w-full gap-3"):
                self._build_account_panel(
                    "Main Account",
                    "main",
                    connect_handler=self._handle_main_oauth_connection,
                    refresh_handler=self._refresh_main_status,
                    client_id_key="client_id",
                    client_secret_key="client_secret",
                    client_id_placeholder="Twitch API Client ID",
                    client_secret_placeholder="Twitch API Client Secret",
                )
                self._build_account_panel(
                    "Chatbot Account",
                    "chatbot",
                    connect_handler=self._handle_chatbot_oauth_connection,
                    refresh_handler=self._refresh_chatbot_status,
                    client_id_key="chatbot_client_id",
                    client_secret_key="chatbot_client_secret",
                    client_id_placeholder="Chatbot Twitch API Client ID",
                    client_secret_placeholder="Chatbot Twitch API Client Secret",
                )

            settings_footer(self.discard, self.save)
            self._status_timer = ui.timer(3.0, self._refresh_status, active=True)

    # ----- helpers -----
    def _load_from_state(self) -> None:
        twitch_data = state_manager.get_twitch_data()
        self.buffer = dataobjects.TwitchData(
            **{
                k: getattr(twitch_data, k)
                for k in twitch_data.__dataclass_fields__.keys()
            }
        )
        self._creds = dict(api_credentials_manager.get_twitch_credentials())
        chatbot_creds = api_credentials_manager.get_chatbot_credentials()
        self._creds.update(
            {
                "chatbot_client_id": chatbot_creds.get("client_id", ""),
                "chatbot_client_secret": chatbot_creds.get("client_secret", ""),
            }
        )

        self.dirty = False

    def _set(self, field: str, value) -> None:
        if getattr(self.buffer, field) != value:
            setattr(self.buffer, field, value)
            self.dirty = True

    def _set_cred(self, field: str, value: str) -> None:
        if self._creds.get(field) != value:
            self._creds[field] = value
            self.dirty = True

    # ----- actions -----
    def save(self) -> None:
        if not self.buffer:
            return
        # persist buffered twitch fields
        for field in self.buffer.__dataclass_fields__.keys():
            state_manager.update_twitch_field(field, getattr(self.buffer, field))

        # persist credentials only on Save
        api_credentials_manager.update_twitch_credentials(
            client_id=self._creds.get("client_id", ""),
            client_secret=self._creds.get("client_secret", ""),
        )

        # persist chatbot credentials
        api_credentials_manager.update_chatbot_credentials(
            client_id=self._creds.get("chatbot_client_id", ""),
            client_secret=self._creds.get("chatbot_client_secret", ""),
        )

        if state_manager.save_changes():
            notify("Twitch saved", type="positive")
            self.dirty = False
        else:
            notify("Error saving Twitch", type="negative")

    def discard(self) -> None:
        self._load_from_state()
        # reset UI controls
        for key, element in self.ui_elements.items():
            if key in (
                "client_id",
                "client_secret",
                "chatbot_client_id",
                "chatbot_client_secret",
            ) and hasattr(element, "value"):
                element.value = self._creds.get(key, "")
            elif hasattr(self.buffer, key) and hasattr(element, "value"):
                element.value = getattr(self.buffer, key)
        self.dirty = False

    def _handle_main_oauth_connection(self) -> None:
        """Handle the Connect Main Account button click to trigger OAuth reconnection"""
        try:
            import logging

            logger = logging.getLogger(__name__)
            logger.info("User clicked Connect Main Account button")

            # Check if client ID and secret are provided
            if not self._creds.get("client_id") or not self._creds.get("client_secret"):
                notify(
                    "Please enter your main Twitch Client ID and Client Secret first!",
                    type="warning",
                )
                return

            # Update button state to show it's working
            if "main_connect_button" in self.ui_elements:
                self.ui_elements["main_connect_button"].set_text("Connecting...")
                self.ui_elements["main_connect_button"].disable()

            # Show a notification that the process is starting
            notify("Starting main Twitch OAuth connection...", type="info")

            # Save only Twitch settings to avoid triggering database and other notifications
            self._save_settings_only()

            # Start the OAuth reconnection in a separate thread
            import threading

            from ... import twitch

            # Create shared result holder for thread communication
            oauth_result = {
                "status": "connecting",
                "success": None,
                "error": None,
                "notification_shown": False,
            }

            def oauth_thread():
                try:
                    success = twitch.trigger_oauth_reconnection()
                    oauth_result["status"] = "complete"
                    oauth_result["success"] = success

                except Exception as e:
                    logger.error(f"Error in main OAuth thread: {str(e)}", exc_info=True)
                    oauth_result["status"] = "error"
                    oauth_result["error"] = str(e)

            # Start the thread
            oauth_worker = threading.Thread(target=oauth_thread)
            oauth_worker.daemon = True
            oauth_worker.start()

            # Create a timer to check the result from main thread
            def check_oauth_result():
                if oauth_result["status"] == "connecting":
                    return True  # Continue checking
                elif (
                    oauth_result["status"] == "complete"
                    and not oauth_result["notification_shown"]
                ):
                    try:
                        oauth_result["notification_shown"] = True
                        if oauth_result["success"]:
                            notify(
                                "Successfully connected main Twitch account!",
                                type="positive",
                                timeout=3000,
                            )
                            logger.info("Main Twitch OAuth connection successful")
                        else:
                            notify(
                                "Failed to connect main Twitch account. Please check your credentials and try again.",
                                type="negative",
                                timeout=5000,
                            )
                            logger.error("Main Twitch OAuth connection failed")

                        # Refresh the status display and reset button
                        self._refresh_main_status()
                        if "main_connect_button" in self.ui_elements:
                            self.ui_elements["main_connect_button"].set_text(
                                "Connect Main Account"
                            )
                            self.ui_elements["main_connect_button"].enable()
                    except Exception as e:
                        logger.error(
                            f"Error updating UI after main Twitch OAuth: {str(e)}"
                        )
                    return False  # Stop checking
                elif (
                    oauth_result["status"] == "error"
                    and not oauth_result["notification_shown"]
                ):
                    try:
                        oauth_result["notification_shown"] = True
                        notify(
                            f"Error during main Twitch connection: {oauth_result['error']}",
                            type="negative",
                        )
                        # Reset button state
                        if "main_connect_button" in self.ui_elements:
                            self.ui_elements["main_connect_button"].set_text(
                                "Connect Main Account"
                            )
                            self.ui_elements["main_connect_button"].enable()
                    except Exception as ui_error:
                        logger.error(f"Error handling UI error update: {str(ui_error)}")
                    return False  # Stop checking
                elif oauth_result["notification_shown"]:
                    # Notification already shown, stop checking
                    return False
                return False  # Default stop

            # Start timer to check results with timeout protection
            check_count = {"count": 0}  # Use dict to maintain reference in closure
            max_checks = 150  # 30 seconds max (150 * 0.2s)

            def check_oauth_result_with_timeout():
                check_count["count"] += 1

                # Add timeout protection
                if (
                    check_count["count"] >= max_checks
                    and not oauth_result["notification_shown"]
                ):
                    oauth_result["notification_shown"] = True
                    logger.warning("Main OAuth check timed out after 30 seconds")
                    notify(
                        "Main OAuth connection timed out. Please try again.",
                        type="negative",
                    )
                    # Reset button state
                    if "main_connect_button" in self.ui_elements:
                        self.ui_elements["main_connect_button"].set_text(
                            "Connect Main Account"
                        )
                        self.ui_elements["main_connect_button"].enable()
                    return False  # Stop timer

                # Call the original check function
                return check_oauth_result()

            oauth_timer = ui.timer(0.2, check_oauth_result_with_timeout)
            # Store timer reference for potential cleanup
            self._active_timers = getattr(self, "_active_timers", [])
            self._active_timers.append(oauth_timer)

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                f"Error handling main Twitch OAuth connection: {str(e)}", exc_info=True
            )
            notify(
                f"Error starting main Twitch connection: {str(e)}", type="negative"
            )
            # Reset button state
            if "main_connect_button" in self.ui_elements:
                self.ui_elements["main_connect_button"].set_text("Connect Main Account")
                self.ui_elements["main_connect_button"].enable()

    def _handle_chatbot_oauth_connection(self) -> None:
        """Handle the Connect Chatbot button click to trigger OAuth reconnection"""
        try:
            import logging

            logger = logging.getLogger(__name__)
            logger.info("User clicked Connect Chatbot button")

            # Check if chatbot client ID and secret are provided
            if not self._creds.get("chatbot_client_id") or not self._creds.get(
                "chatbot_client_secret"
            ):
                notify(
                    "Please enter your chatbot Twitch Client ID and Client Secret first!",
                    type="warning",
                )
                return

            # Update button state to show it's working
            if "chatbot_connect_button" in self.ui_elements:
                self.ui_elements["chatbot_connect_button"].set_text("Connecting...")
                self.ui_elements["chatbot_connect_button"].disable()

            # Show a notification that the process is starting
            notify("Starting chatbot Twitch OAuth connection...", type="info")

            # Save settings to ensure chatbot credentials are persisted
            self.save()

            # Start the OAuth reconnection in a separate thread
            import threading

            from ... import chatbot

            # Create shared result holder for thread communication
            oauth_result = {
                "status": "connecting",
                "success": None,
                "error": None,
                "notification_shown": False,
            }

            def oauth_thread():
                try:
                    success = chatbot.trigger_chatbot_oauth_reconnection()
                    oauth_result["status"] = "complete"
                    oauth_result["success"] = success

                except Exception as e:
                    logger.error(
                        f"Error in chatbot OAuth thread: {str(e)}", exc_info=True
                    )
                    oauth_result["status"] = "error"
                    oauth_result["error"] = str(e)

            # Start the thread
            oauth_worker = threading.Thread(target=oauth_thread)
            oauth_worker.daemon = True
            oauth_worker.start()

            # Create a timer to check the result from main thread
            def check_oauth_result():
                if oauth_result["status"] == "connecting":
                    return True  # Continue checking
                elif (
                    oauth_result["status"] == "complete"
                    and not oauth_result["notification_shown"]
                ):
                    try:
                        oauth_result["notification_shown"] = True
                        if oauth_result["success"]:
                            notify(
                                "Successfully connected chatbot Twitch account!",
                                type="positive",
                                timeout=3000,
                            )
                            logger.info("Chatbot Twitch OAuth connection successful")
                        else:
                            notify(
                                "Failed to connect chatbot Twitch account. Please check your credentials and try again.",
                                type="negative",
                                timeout=5000,
                            )
                            logger.error("Chatbot Twitch OAuth connection failed")

                        # Refresh the status display and reset button
                        self._refresh_chatbot_status()
                        if "chatbot_connect_button" in self.ui_elements:
                            self.ui_elements["chatbot_connect_button"].set_text(
                                "Connect Chatbot"
                            )
                            self.ui_elements["chatbot_connect_button"].enable()
                    except Exception as e:
                        logger.error(
                            f"Error updating UI after chatbot Twitch OAuth: {str(e)}"
                        )
                    return False  # Stop checking
                elif (
                    oauth_result["status"] == "error"
                    and not oauth_result["notification_shown"]
                ):
                    try:
                        oauth_result["notification_shown"] = True
                        notify(
                            f"Error during chatbot Twitch connection: {oauth_result['error']}",
                            type="negative",
                        )
                        # Reset button state
                        if "chatbot_connect_button" in self.ui_elements:
                            self.ui_elements["chatbot_connect_button"].set_text(
                                "Connect Chatbot"
                            )
                            self.ui_elements["chatbot_connect_button"].enable()
                    except Exception as ui_error:
                        logger.error(f"Error handling UI error update: {str(ui_error)}")
                    return False  # Stop checking
                elif oauth_result["notification_shown"]:
                    # Notification already shown, stop checking
                    return False
                return False  # Default stop

            # Start timer to check results with timeout protection
            check_count = {"count": 0}  # Use dict to maintain reference in closure
            max_checks = 300  # 60 seconds max for chatbot OAuth (longer process)

            def check_oauth_result_with_timeout():
                check_count["count"] += 1

                # Add timeout protection
                if (
                    check_count["count"] >= max_checks
                    and not oauth_result["notification_shown"]
                ):
                    oauth_result["notification_shown"] = True
                    logger.warning("Chatbot OAuth check timed out after 60 seconds")
                    notify(
                        "Chatbot OAuth connection timed out. Please try again.",
                        type="negative",
                    )
                    # Reset button state
                    if "chatbot_connect_button" in self.ui_elements:
                        self.ui_elements["chatbot_connect_button"].set_text(
                            "Connect Chatbot"
                        )
                        self.ui_elements["chatbot_connect_button"].enable()
                    return False  # Stop timer

                # Call the original check function
                return check_oauth_result()

            oauth_timer = ui.timer(0.2, check_oauth_result_with_timeout)
            # Store timer reference for potential cleanup
            self._active_timers = getattr(self, "_active_timers", [])
            self._active_timers.append(oauth_timer)

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                f"Error handling chatbot Twitch OAuth connection: {str(e)}",
                exc_info=True,
            )
            notify(
                f"Error starting chatbot Twitch connection: {str(e)}", type="negative"
            )
            # Reset button state
            if "chatbot_connect_button" in self.ui_elements:
                self.ui_elements["chatbot_connect_button"].set_text("Connect Chatbot")
                self.ui_elements["chatbot_connect_button"].enable()

    def _save_settings_only(self) -> None:
        """Save only Twitch credentials without triggering full save"""
        try:
            # Persist credentials only
            from ... import api_credentials_manager

            api_credentials_manager.update_twitch_credentials(
                client_id=self._creds.get("client_id", ""),
                client_secret=self._creds.get("client_secret", ""),
            )
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error saving Twitch settings only: {str(e)}", exc_info=True)

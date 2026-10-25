# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
from __future__ import annotations

from typing import Dict, Any, Optional

from nicegui import run, ui
from ...notification_engine import nav_actions_settings, notify, notify_progress
from ...ui_buttons import outline_button, primary_button
from ...ui_form_controls import (
    copy_text_button,
    form_input,
    form_select,
    form_sensitive_input,
)
from ...ui_timer import layout_schedule
from ...ui_settings_layout import (
    settings_action_row,
    settings_form_grid,
    settings_status_band,
    settings_surface,
)

from ... import dataobjects
from ...dataobjects import state_manager
from ...api_credentials_manager import api_credentials_manager
from ...spotify import SPOTIFY_OAUTH_REDIRECT_URI


class SpotifyTab:
    name = "Spotify"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.SpotifyData] = None
        self.ui_elements: Dict[str, Any] = {}
        self._creds: Dict[str, str] = {}
        self._status_timer: Optional[Any] = None

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
        if self._status_timer is not None:
            self._status_timer.active = True
        layout_schedule(0.05, self._refresh_status, once=True)

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
        if self._status_timer is not None:
            self._status_timer.active = False

    def build(self, parent_container) -> None:
        self._load_from_state()
        with settings_surface(parent_container):

            with settings_status_band():
                with ui.column().classes("gap-0"):
                    ui.label("Status").classes("text-xs secondary-text")
                    self.ui_elements["status_label"] = ui.label(
                        "Loading..."
                    ).classes("font-semibold text-sm")
                with ui.column().classes("gap-0 flex-1 min-w-[12rem]"):
                    ui.label("Current track").classes("text-xs secondary-text")
                    self.ui_elements["track_label"] = ui.label("N/A").classes(
                        "font-semibold text-sm"
                    )

            markets = {
                "": "Auto (from account)",
                "US": "US",
                "GB": "GB",
                "CA": "CA",
            }
            with ui.expansion("Advanced", icon="vpn_key").classes("w-full").props(
                "dense"
            ):
                ui.label(
                    "Client ID and secret from the Spotify dashboard. "
                    "Connect still uses these values."
                ).classes("text-xs secondary-text mb-2")
                with settings_form_grid(columns=2):
                    self.ui_elements["client_id"] = form_sensitive_input(
                        tooltip="Spotify application Client ID from the developer dashboard",
                        label="Client ID",
                        value=self._creds.get("client_id", ""),
                        placeholder="Spotify API Client ID",
                    )
                    self.ui_elements["client_id"].on_value_change(
                        lambda e: self._set_cred(
                            "client_id", self._str_from_value_event(e)
                        )
                    )
                    self.ui_elements["client_secret"] = form_sensitive_input(
                        tooltip="Spotify application Client Secret",
                        label="Client Secret",
                        value=self._creds.get("client_secret", ""),
                        placeholder="Spotify API Client Secret",
                    )
                    self.ui_elements["client_secret"].on_value_change(
                        lambda e: self._set_cred(
                            "client_secret", self._str_from_value_event(e)
                        )
                    )
            with settings_form_grid(columns=3):
                self.ui_elements["market_country"] = form_select(
                    tooltip="Market used for Spotify API requests (Auto uses your account region)",
                    label="Market country",
                    options=markets,
                    value=getattr(self.buffer, "market_country", ""),
                )
                self.ui_elements["market_country"].on_value_change(
                    lambda e: self._set(
                        "market_country", self._str_from_value_event(e)
                    )
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
                copy_text_button(
                    SPOTIFY_OAUTH_REDIRECT_URI,
                    tooltip=f"Copy redirect URI {SPOTIFY_OAUTH_REDIRECT_URI}",
                )
                self.ui_elements["connect_button"] = primary_button(
                    "Connect",
                    self._handle_oauth_connection,
                    icon="login",
                )
            self._status_timer = layout_schedule(5.0, self._refresh_status, active=True)

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

    def _persist_credentials_to_state(self) -> None:
        """Write UI credentials to SpotifyData and api_credentials.json."""
        client_id = self._creds.get("client_id", "")
        client_secret = self._creds.get("client_secret", "")
        if self.buffer:
            self.buffer.client_id = client_id
            self.buffer.client_secret = client_secret
        state_manager.update_spotify_field("client_id", client_id)
        state_manager.update_spotify_field("client_secret", client_secret)
        api_credentials_manager.update_spotify_credentials(
            client_id=client_id,
            client_secret=client_secret,
        )

    def save(self) -> None:
        if not self.buffer:
            return
        self._persist_credentials_to_state()
        for field in self.buffer.__dataclass_fields__.keys():
            state_manager.update_spotify_field(field, getattr(self.buffer, field))
        if state_manager.save_changes():
            notify("", type="success", source="spotify", title="Spotify settings saved")
            self.dirty = False
        # A failed save_changes() was already reported once (changes stay pending).

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
                return  # the progress notice is already showing

            # Check if client ID and secret are provided
            if not self._creds.get("client_id") or not self._creds.get("client_secret"):
                notify(
                    "",
                    type="warning",
                    source="spotify",
                    title="Enter your Spotify Client ID and Client Secret first",
                    hint="Create an app at developer.spotify.com/dashboard to get them.",
                )
                return

            # Mark OAuth as in progress
            self._oauth_in_progress = True

            # Update button state to show it's working
            if "connect_button" in self.ui_elements:
                self.ui_elements["connect_button"].set_text("Connecting...")
                self.ui_elements["connect_button"].disable()

            # Save only Spotify settings (a failure is reported by save_changes)
            self._save_settings_only()

            work = notify_progress(
                "Connecting to Spotify",
                source="spotify",
                message="Approve Mycelian in the browser page that opens.",
            )
            self._oauth_work = work

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
            # A little longer than the callback server's own 5-minute wait, so the
            # real result (success or timeout) normally arrives first.
            max_checks = 1650
            notification_shown = {"done": False}

            def check_oauth_result():
                if notification_shown["done"]:
                    self._cleanup_oauth()
                    return

                check_count["count"] += 1

                if oauth_result["status"] == "connecting":
                    if check_count["count"] >= max_checks:
                        logger.warning("Spotify OAuth check timed out")
                        notification_shown["done"] = True
                        self._cleanup_oauth()
                        work.done(
                            "Still waiting for Spotify sign-in",
                            "Finish signing in in your browser, or press Connect to Spotify again.",
                            type="warning",
                        )
                    return

                notification_shown["done"] = True
                if oauth_result["status"] == "complete":
                    try:
                        if oauth_result["success"]:
                            # trigger_automatic_oauth_flow returns only after the
                            # browser approval and token exchange both succeeded.
                            work.done("Connected to Spotify")
                            logger.info("Spotify automatic OAuth flow completed")
                        else:
                            status = ""
                            try:
                                status = str(
                                    getattr(state_manager.get_spotify_data(), "connection_status", "") or ""
                                )
                            except Exception:
                                pass
                            if "timeout" in status.lower():
                                work.fail(
                                    "Spotify sign-in timed out",
                                    hint="Press Connect to Spotify and approve Mycelian within 5 minutes.",
                                    details=status,
                                )
                            else:
                                work.fail(
                                    "Spotify sign-in didn't complete",
                                    hint=(
                                        "Check the Client ID and Secret, and that the redirect URI in your "
                                        "Spotify app matches the one shown here, then try again."
                                    ),
                                    details=status or None,
                                )
                            logger.warning("Spotify automatic OAuth flow failed: %s", status)

                        # Refresh the status display
                        self._refresh_status()

                    except Exception as e:
                        logger.error(f"Error updating UI after Spotify OAuth: {str(e)}")

                    self._cleanup_oauth()
                    return
                elif oauth_result["status"] == "error":
                    try:
                        work.fail(
                            "Spotify sign-in didn't complete",
                            hint="Check the Client ID and Secret, then try again.",
                            details=str(oauth_result["error"] or ""),
                        )
                    except Exception as ui_error:
                        logger.error(f"Error showing OAuth error: {str(ui_error)}")
                    self._cleanup_oauth()
                    return
                self._cleanup_oauth()

            oauth_timer = layout_schedule(0.2, check_oauth_result)
            self._oauth_timer = oauth_timer
            # Store timer reference for potential cleanup
            self._active_timers = getattr(self, "_active_timers", [])
            self._active_timers.append(oauth_timer)

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                f"Error handling Spotify OAuth connection: {str(e)}", exc_info=True
            )
            fail_kwargs = {
                "hint": "Try again. If it keeps happening, copy the details and report it.",
                "details": f"{type(e).__name__}: {e}",
            }
            work = getattr(self, "_oauth_work", None)
            if work is not None and not work.finished:
                work.fail("Couldn't start Spotify sign-in", **fail_kwargs)
            else:
                notify("", type="error", source="spotify", title="Couldn't start Spotify sign-in", **fail_kwargs)
            self._cleanup_oauth()

    def _cleanup_oauth(self) -> None:
        """Clean up after Spotify OAuth"""
        try:
            oauth_timer = getattr(self, "_oauth_timer", None)
            if oauth_timer is not None:
                try:
                    oauth_timer.active = False
                except Exception:
                    pass
                try:
                    oauth_timer.cancel()
                except Exception:
                    pass
                self._oauth_timer = None
            for t in getattr(self, "_active_timers", []):
                try:
                    t.active = False
                except Exception:
                    pass
            self._active_timers = []

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

    async def _test_connection(self) -> None:
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
                return  # the progress notice is already showing

            # Mark test as in progress
            self._test_in_progress = True

            # Update button state to show it's working
            if "test_button" in self.ui_elements:
                self.ui_elements["test_button"].set_text("Testing...")
                self.ui_elements["test_button"].disable()

            # Save only Spotify settings (a failure is reported by save_changes)
            self._save_settings_only()

            def test_thread():
                from ... import spotify

                return spotify.test_and_reconnect()

            work = notify_progress("Testing the Spotify connection", source="spotify")
            try:
                success = await run.io_bound(test_thread)
                if success:
                    work.done("Connected to Spotify")
                    logger.info("Spotify connection test completed successfully")
                else:
                    work.fail(
                        "Couldn't reach Spotify with the saved sign-in",
                        hint="Press Connect to Spotify to sign in again.",
                    )
                    logger.warning("Spotify connection test failed")
                self._refresh_status()
            except Exception as e:
                logger.error(f"Error testing Spotify connection: {str(e)}", exc_info=True)
                work.fail(
                    "Couldn't test the Spotify connection",
                    hint="Press Connect to Spotify to sign in again.",
                    details=f"{type(e).__name__}: {e}",
                )
            finally:
                self._cleanup_test()

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                f"Error handling Spotify test connection: {str(e)}", exc_info=True
            )
            notify(
                "",
                type="error",
                source="spotify",
                title="Couldn't run the Spotify test",
                hint="Try again. If it keeps happening, copy the details and report it.",
                details=f"{type(e).__name__}: {e}",
            )
            self._cleanup_test()

    def _cleanup_test(self) -> None:
        """Clean up after Spotify connection test"""
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
            logger.error(f"Error cleaning up Spotify test: {str(e)}")

    def _save_settings_only(self) -> bool:
        """Save Spotify credentials to DB and api_credentials.json.

        A False result has already been reported (save_changes or here).
        """
        try:
            self._persist_credentials_to_state()
            return bool(state_manager.save_changes())
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error saving Spotify settings only: {str(e)}", exc_info=True)
            notify(
                "Sign-in may use the previously saved values.",
                type="error",
                source="spotify",
                title="Couldn't save your Spotify Client ID and Secret",
                hint="Check the connection in Settings → Database, then try again.",
                details=f"{type(e).__name__}: {e}",
                actions=nav_actions_settings("Database"),
            )
            return False

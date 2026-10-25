# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
from __future__ import annotations

from typing import Dict, Any, Optional, Tuple

from nicegui import run, ui
from ...notification_engine import nav_actions_settings, notify, notify_progress
from ...ui_buttons import outline_button, primary_button
from ...ui_timer import layout_schedule

from ... import dataobjects
from ...dataobjects import state_manager
from ...api_credentials_manager import api_credentials_manager
from ...spotify import SPOTIFY_OAUTH_REDIRECT_URI
from .integration_ui import (
    AccountCard,
    CopyRow,
    CredentialsFold,
    IntegrationPage,
    field as text_field,
    fields,
    hint,
    media_html,
    row,
    section,
    set_connect_style,
)

_MARKETS = {
    "": "Auto (from account)",
    "US": "United States",
    "GB": "United Kingdom",
    "CA": "Canada",
}
_BUSY_STATUSES = ("Opening Browser...", "Awaiting Authorization")
_WARN_STATUSES = (
    "Authorization Required",
    "Authorization Timeout",
    "No Internet",
    "Checking Internet",
    "Service Unreachable",
)
_NOTHING_PLAYING = "Nothing playing"


class SpotifyTab:
    name = "Spotify"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.SpotifyData] = None
        self.ui_elements: Dict[str, Any] = {}
        self._creds: Dict[str, str] = {}
        self._saved_creds: Dict[str, str] = {}
        self._status_timer: Optional[Any] = None
        self._page: Optional[IntegrationPage] = None
        self._card: Optional[AccountCard] = None
        self._fold: Optional[CredentialsFold] = None
        self._track: Optional[Any] = None
        self._track_box: Optional[Any] = None
        self._track_html: str = ""

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
        if self._page is not None:
            self._page.set_active(True)
        layout_schedule(0.05, self._refresh_status, once=True)

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False
        if self._page is not None:
            self._page.set_active(False)

    def _describe(self, status_text: str, connected: bool) -> Tuple[str, str, str, str, str]:
        """(card state kind, card name, state line, header pill kind, header pill text)."""
        if connected:
            return "ok", "Connected", "Connected · now playing is available", "ok", "Connected"
        if getattr(self, "_oauth_in_progress", False) or status_text in _BUSY_STATUSES:
            return "busy", "Signing in…", status_text, "busy", "Signing in"
        if status_text in _WARN_STATUSES:
            return "warn", "Not connected", status_text, "warn", status_text
        if "error" in status_text.lower() or "failed" in status_text.lower():
            return "error", "Not connected", status_text, "error", "Connection error"
        creds = getattr(self, "_saved_creds", {}) or {}
        if creds.get("client_id") and creds.get("client_secret"):
            line = "Click Connect and approve Mycelian in your browser"
        else:
            line = "Add your app's Client ID and secret, then connect"
        return "off", "Not connected", line, "off", "Not connected"

    def _set_track(self, status_info: Dict[str, Any], connected: bool) -> None:
        """Now playing strip: title and artist, or the old fallback texts."""
        if self._track is None:
            return
        if self._track_box is not None:
            # Nothing to show until signed in; the card already says it isn't connected.
            self._track_box.set_visibility(connected)
        title, sub = "", ""
        try:
            from ... import spotify

            client = getattr(spotify, "spotify_client", None)
            data = client.spotify_data if client is not None else None
            if data is not None:
                title = str(getattr(data, "track_name", "") or "").strip()
                sub = " · ".join(
                    part
                    for part in (
                        str(getattr(data, "artist_name", "") or "").strip(),
                        str(getattr(data, "album_name", "") or "").strip(),
                    )
                    if part and part != _NOTHING_PLAYING
                )
        except Exception:
            title, sub = "", ""
        current_track = str(status_info.get("current_track", "N/A") or "N/A")
        if not title or title == _NOTHING_PLAYING or current_track == "N/A":
            # If we get "Nothing playing - Nothing playing" due to regional restrictions,
            # show a more user-friendly message
            if connected:
                text = "Spotify connected (playback may be restricted by region)"
            else:
                text = "Not connected"
            content = media_html("music_off", text, muted=True)
        else:
            content = media_html("music_note", title, sub)
        if content != self._track_html:
            self._track_html = content
            self._track.set_content(content)

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

            # Use the most current status from either source
            status_text = str(
                current_connection_status or status_info.get("status", "Unknown")
            )
            is_authenticated = bool(status_info.get("is_authenticated", False))
            connected = is_authenticated or status_text == "Connected"

            kind, name, line, pill_kind, pill_text = self._describe(status_text, connected)
            if self._card is not None:
                self._card.set(name, kind, line)
            if self._page is not None:
                self._page.set_status([(pill_kind, pill_text)])
            self._set_track(status_info, is_authenticated)
            btn = self.ui_elements.get("connect_button")
            if btn is not None and not getattr(self, "_oauth_in_progress", False):
                set_connect_style(btn, connected)

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error refreshing Spotify status: {str(e)}", exc_info=True)
            # Set error status
            if self._card is not None:
                self._card.set("Not connected", "error", "Status Error")
            if self._page is not None:
                self._page.set_status([("error", "Status error")])

    def _refresh_saved_tag(self) -> None:
        if self._fold is not None:
            self._fold.set_saved(
                bool(self._saved_creds.get("client_id") and self._saved_creds.get("client_secret"))
            )

    def build(self, parent_container) -> None:
        self._load_from_state()
        self._page = page = IntegrationPage(
            parent_container,
            "spotify",
            "Spotify",
            "Now playing for overlays and chat commands",
            on_save=self.save,
            on_discard=self.discard,
            is_dirty=lambda: self.dirty,
            help_topic="guide_spotify",
            help_tooltip="Spotify setup guide",
        )
        with page.column():
            with section("account_circle", "Account"):
                self._card = card = AccountCard("spotify", "Spotify")
                with card.actions:
                    self.ui_elements["refresh_button"] = (
                        ui.button(icon="refresh", on_click=self._refresh_status)
                        .props("flat round dense")
                        .classes("itg-ibtn")
                        .tooltip("Check status now")
                    )
                    self.ui_elements["test_button"] = (
                        outline_button("Test", self._test_connection, icon="wifi_tethering")
                        .props("dense")
                        .classes("px-3")
                        .tooltip("Save the Client ID and Secret and check the saved sign-in")
                    )
                    self.ui_elements["connect_button"] = (
                        primary_button("Connect", self._handle_oauth_connection, icon="login")
                        .props("dense")
                        .classes("px-3")
                    )
                self._track_box = card.extra()
                with self._track_box:
                    self._track = ui.html("", sanitize=False).classes("w-full")
                saved = bool(self._creds.get("client_id") and self._creds.get("client_secret"))
                self._fold = fold = card.credentials("Client ID & secret", opened=not saved)
                with fold.body:
                    with fields():
                        self.ui_elements["client_id"] = text_field(
                            "Client ID",
                            value=self._creds.get("client_id", ""),
                            password=True,
                            placeholder="Spotify API Client ID",
                            tip="Spotify application Client ID from the developer dashboard",
                            on_change=lambda e: self._set_cred(
                                "client_id", self._str_from_value_event(e)
                            ),
                        )
                        self.ui_elements["client_secret"] = text_field(
                            "Client Secret",
                            value=self._creds.get("client_secret", ""),
                            password=True,
                            placeholder="Spotify API Client Secret",
                            tip="Spotify application Client Secret",
                            on_change=lambda e: self._set_cred(
                                "client_secret", self._str_from_value_event(e)
                            ),
                        )
                    CopyRow("Redirect URI", SPOTIFY_OAUTH_REDIRECT_URI, copied="Redirect URI copied")
                    hint(
                        "Create an app, add the redirect URI above, then paste its Client ID and Secret here.",
                        link_label="developer.spotify.com/dashboard",
                        url="https://developer.spotify.com/dashboard",
                    )
                self._refresh_saved_tag()
        with page.column():
            with section("tune", "Options"):
                market = str(getattr(self.buffer, "market_country", "") or "")
                markets = dict(_MARKETS)
                if market not in markets:
                    markets[market] = market
                _r, ctrl = row("Market country", "Region used for Spotify lookups")
                with ctrl:
                    self.ui_elements["market_country"] = (
                        ui.select(options=markets, value=market)
                        .props("outlined dense")
                        .style("width:210px")
                        .tooltip("Market used for Spotify API requests (Auto uses your account region)")
                    )
                self.ui_elements["market_country"].on_value_change(
                    lambda e: self._set(
                        "market_country", self._str_from_value_event(e)
                    )
                )
        self._status_timer = layout_schedule(5.0, self._refresh_status, active=True)
        self._refresh_status()

    def _load_from_state(self) -> None:
        sp = state_manager.get_spotify_data()
        self.buffer = dataobjects.SpotifyData(
            **{k: getattr(sp, k) for k in sp.__dataclass_fields__.keys()}
        )
        self._creds = dict(api_credentials_manager.get_spotify_credentials())
        self._saved_creds = dict(self._creds)
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
        self._saved_creds = {"client_id": client_id, "client_secret": client_secret}
        self._refresh_saved_tag()

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
        self._refresh_saved_tag()

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
            self._refresh_status()

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
                            "Finish signing in in your browser, or try connecting again.",
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
                                    hint="Connect again and approve Mycelian within 5 minutes.",
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

            # Reset button state (the label follows the connection state again)
            if "connect_button" in self.ui_elements:
                self.ui_elements["connect_button"].enable()
                self.ui_elements["connect_button"]._itg_connected = None
            self._refresh_status()
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
                        hint="Connect again to sign in.",
                    )
                    logger.warning("Spotify connection test failed")
                self._refresh_status()
            except Exception as e:
                logger.error(f"Error testing Spotify connection: {str(e)}", exc_info=True)
                work.fail(
                    "Couldn't test the Spotify connection",
                    hint="Connect again to sign in.",
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

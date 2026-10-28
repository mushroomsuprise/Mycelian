# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from nicegui import ui
from ...notification_engine import notify, notify_progress
from ...ui_buttons import primary_button
from ...ui_timer import layout_schedule
from ... import dataobjects
from ...api_credentials_manager import api_credentials_manager
from ...dataobjects import state_manager
from .integration_ui import (
    AccountCard,
    CopyRow,
    CredentialsFold,
    IntegrationPage,
    field as text_field,
    fields,
    hint,
    initial_of,
    section,
    set_connect_style,
    steps_html,
    switch_row,
)

TWITCH_OAUTH_REDIRECT_URI = "http://localhost:17563"

# Status strings from connection_status_tracker's connectivity overlay.
_OFFLINE_STATUSES = ("No Internet", "Checking Internet", "Service Unreachable")
# user_name values that mean "no account".
_NO_USER = ("", "None", "N/A", "Error")
_ACCOUNT_LABELS = {"main": "Main account", "chatbot": "Chatbot"}


def _stop_ui_timer(timer) -> None:
    """NiceGUI 3 ignores ``return False`` from timer callbacks; deactivate explicitly."""
    if timer is None:
        return
    try:
        timer.active = False
    except Exception:
        pass
    try:
        timer.cancel()
    except Exception:
        pass


def _fail_or_notify(work, source: str, title: str, details: str) -> None:
    """End a sign-in progress notice with an error, or show one if it never started."""
    kwargs = {
        "hint": "Try again. If it keeps happening, copy the details and report it.",
        "details": details,
    }
    if work is not None and not work.finished:
        work.fail(title, **kwargs)
    else:
        notify("", type="error", source=source, title=title, **kwargs)


def _has_token(status_info: Dict[str, Any]) -> bool:
    """True when the account holds a user token (the timing fields are filled)."""
    return str(status_info.get("token_expires_countdown", "—") or "—") != "—"


def _is_fallback(prefix: str, status_info: Dict[str, Any]) -> bool:
    """Chatbot replies through the main account (no dedicated bot connected)."""
    return prefix == "chatbot" and "fallback" in str(status_info.get("status", "")).lower()


class TwitchTab:
    name = "Twitch"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.TwitchData] = None
        self._app_settings_buffer: Optional[dataobjects.AppSettings] = None
        self.ui_elements: Dict[str, Any] = {}
        self._creds: Dict[str, str] = {}
        self._saved_creds: Dict[str, str] = {}
        self._status_timer: Optional[Any] = None
        self._token_timer: Optional[Any] = None
        self._page: Optional[IntegrationPage] = None
        self._cards: Dict[str, AccountCard] = {}
        self._folds: Dict[str, CredentialsFold] = {}
        self._steps: Optional[Any] = None
        self._steps_html: str = ""
        self._pills: Dict[str, Tuple[str, str]] = {}
        self._valid: Dict[str, bool] = {}
        self._connected: Dict[str, bool] = {}
        self._connecting: Dict[str, bool] = {}

    def on_enter(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = True
        if self._token_timer is not None:
            self._token_timer.active = True
        if self._page is not None:
            self._page.set_active(True)
        layout_schedule(0.05, self._refresh_status, once=True)
        layout_schedule(0.05, self._refresh_token_countdowns, once=True)

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False
        if self._token_timer is not None:
            self._token_timer.active = False
        if self._page is not None:
            self._page.set_active(False)

    # ----- status -----
    def _refresh_main_status(self) -> None:
        """Refresh main Twitch account status display."""
        try:
            from ... import twitch

            self._apply_status("main", twitch.get_twitch_connection_status())
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                f"Error refreshing main Twitch status: {str(e)}", exc_info=True
            )
            self._apply_status_error("main")

    def _refresh_chatbot_status(self) -> None:
        """Refresh chatbot Twitch account status display."""
        try:
            from ... import chatbot

            self._apply_status("chatbot", chatbot.get_chatbot_connection_status())
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error refreshing chatbot status: {str(e)}", exc_info=True)
            self._apply_status_error("chatbot")

    def _refresh_status(self) -> None:
        """Refresh both main account and chatbot status displays."""
        self._refresh_main_status()
        self._refresh_chatbot_status()

    def _refresh_token_countdowns(self) -> None:
        """Update token refresh/expiry countdowns (runs every second)."""
        try:
            from ... import chatbot, twitch

            self._update_token_timing_labels(
                "main", twitch.get_twitch_connection_status()
            )
            self._update_token_timing_labels(
                "chatbot", chatbot.get_chatbot_connection_status()
            )
        except Exception:
            pass

    @staticmethod
    def _countdown_class(status_info: Dict[str, Any], *, for_expiry: bool) -> str:
        if status_info.get("token_expired"):
            return "is-error"
        if not for_expiry and status_info.get("token_refresh_due"):
            return "is-warn"
        return ""

    def _update_token_timing_labels(
        self, prefix: str, status_info: Dict[str, Any]
    ) -> None:
        """Apply Updated / Next refresh / Token expires to the account card."""
        card = self._cards.get(prefix)
        if card is None:
            return
        if _is_fallback(prefix, status_info) or not (
            status_info.get("is_valid") or _has_token(status_info)
        ):
            card.set_meta([])  # no account of its own: nothing to time
            return
        card.set_meta(
            [
                ("Updated", str(status_info.get("last_update", "Never")), "", ""),
                (
                    "Next refresh",
                    str(status_info.get("token_refresh_countdown", "—")),
                    str(status_info.get("token_refresh_at", "—")),
                    self._countdown_class(status_info, for_expiry=False),
                ),
                (
                    "Token expires",
                    str(status_info.get("token_expires_countdown", "—")),
                    str(status_info.get("token_expires_at", "—")),
                    self._countdown_class(status_info, for_expiry=True),
                ),
            ]
        )

    def _describe(self, prefix: str, info: Dict[str, Any]) -> Tuple[str, str, str, str]:
        """(card state kind, state line, header pill kind, header pill text)."""
        label = _ACCOUNT_LABELS[prefix]
        status = str(info.get("status") or "Unknown")
        valid = bool(info.get("is_valid"))
        if _is_fallback(prefix, info):
            if valid or status.startswith("Not Configured"):
                return "off", "Replies use your main account until you connect a bot", "warn", f"{label} not connected"
            return "warn", status, "warn", f"{label} not connected"
        if info.get("token_expired") and (valid or _has_token(info)):
            return "error", "Token expired · reconnect to sign in again", "error", f"{label} token expired"
        if valid:
            if info.get("degraded"):
                return "warn", status, "warn", f"{label} degraded"
            line = "Connected · events flowing" if prefix == "main" else "Connected · replies come from this account"
            return "ok", line, "ok", f"{label} connected"
        if status in _OFFLINE_STATUSES or status == "Authenticated but Disconnected":
            return "warn", status, "warn", f"{label} disconnected"
        if status in ("Error", "Status Error"):
            return "error", status, "error", f"{label} status error"
        return "off", status, "warn", f"{label} not connected"

    def _account_name(self, prefix: str, info: Dict[str, Any]) -> Tuple[str, bool]:
        """(display name, whether it is a real account)."""
        if _is_fallback(prefix, info):
            return "Not connected", False
        user = str(info.get("user_name") or "").strip()
        if user not in _NO_USER and user != "Unknown":
            return user, True
        if info.get("is_valid") or _has_token(info):
            return "Twitch account", True
        return "Not connected", False

    def _apply_status(self, prefix: str, info: Dict[str, Any]) -> None:
        card = self._cards.get(prefix)
        if card is None:
            return
        kind, line, pill_kind, pill_text = self._describe(prefix, info)
        name, real = self._account_name(prefix, info)
        card.set(
            name, kind, line,
            initial=initial_of(name) if real else None,
            photo_url=info.get("avatar_url") if real else None,
        )
        self._update_token_timing_labels(prefix, info)
        self._valid[prefix] = bool(info.get("is_valid")) and not _is_fallback(prefix, info)
        self._connected[prefix] = real
        self._pills[prefix] = (pill_kind, pill_text)
        self._sync_connect_button(prefix)
        self._refresh_overview()

    def _apply_status_error(self, prefix: str) -> None:
        card = self._cards.get(prefix)
        if card is None:
            return
        card.set("Not connected", "error", "Status Error")
        self._pills[prefix] = ("error", f"{_ACCOUNT_LABELS[prefix]} status error")
        self._refresh_overview()

    def _refresh_overview(self) -> None:
        """Header pills, credential tags and the setup ticks."""
        if self._page is not None:
            self._page.set_status([self._pills[k] for k in ("main", "chatbot") if k in self._pills])
        main_saved = bool(self._saved_creds.get("client_id") and self._saved_creds.get("client_secret"))
        bot_saved = bool(
            self._saved_creds.get("chatbot_client_id") and self._saved_creds.get("chatbot_client_secret")
        )
        for prefix, saved in (("main", main_saved), ("chatbot", bot_saved)):
            fold = self._folds.get(prefix)
            if fold is not None:
                fold.set_saved(saved)
        if self._steps is not None:
            content = steps_html(
                [
                    (
                        "Create an app at <b>dev.twitch.tv/console</b> with redirect URI "
                        f"<b>{TWITCH_OAUTH_REDIRECT_URI}</b>.",
                        main_saved,
                    ),
                    ("Paste its Client ID and Secret under <b>Client ID &amp; secret</b>.", main_saved),
                    ("Click <b>Connect</b> and approve Mycelian in your browser.", self._valid.get("main", False)),
                    (
                        "Optional: repeat for a separate <b>chatbot</b> account so replies come from the bot.",
                        self._valid.get("chatbot", False),
                    ),
                ]
            )
            if content != self._steps_html:
                self._steps_html = content
                self._steps.set_content(content)

    # ----- connect button -----
    def _sync_connect_button(self, prefix: str) -> None:
        btn = self.ui_elements.get(f"{prefix}_connect_button")
        if btn is None or self._connecting.get(prefix):
            return
        set_connect_style(btn, self._connected.get(prefix, False))

    def _start_connect_button(self, prefix: str) -> None:
        btn = self.ui_elements.get(f"{prefix}_connect_button")
        if btn is None:
            return
        self._connecting[prefix] = True
        btn.set_text("Connecting...")
        btn.disable()

    def _reset_connect_button(self, prefix: str) -> None:
        btn = self.ui_elements.get(f"{prefix}_connect_button")
        if btn is None:
            return
        self._connecting[prefix] = False
        btn.enable()
        btn._itg_connected = None  # force the label back from "Connecting..."
        self._sync_connect_button(prefix)

    # ----- layout -----
    def _build_account_card(
        self,
        kicker: str,
        prefix: str,
        *,
        connect_handler,
        refresh_handler,
        client_id_key: str,
        client_secret_key: str,
        client_id_placeholder: str,
        client_secret_placeholder: str,
    ) -> None:
        card = AccountCard("twitch", kicker)
        self._cards[prefix] = card
        with card.actions:
            self.ui_elements[f"{prefix}_refresh_button"] = (
                ui.button(icon="refresh", on_click=refresh_handler)
                .props("flat round dense")
                .classes("itg-ibtn")
                .tooltip("Check status now")
            )
            self.ui_elements[f"{prefix}_connect_button"] = (
                primary_button("Connect", connect_handler, icon="login")
                .props("dense")
                .classes("px-3")
            )
        saved = bool(self._creds.get(client_id_key) and self._creds.get(client_secret_key))
        fold = card.credentials("Client ID & secret", opened=not saved)
        self._folds[prefix] = fold
        with fold.body:
            with fields():
                self.ui_elements[client_id_key] = text_field(
                    "Client ID",
                    value=self._creds.get(client_id_key, ""),
                    password=True,
                    placeholder=client_id_placeholder,
                    tip="Twitch application Client ID from the developer console",
                    on_change=lambda e, k=client_id_key: self._set_cred(
                        k, "" if e.value is None else str(e.value)
                    ),
                )
                self.ui_elements[client_secret_key] = text_field(
                    "Client Secret",
                    value=self._creds.get(client_secret_key, ""),
                    password=True,
                    placeholder=client_secret_placeholder,
                    tip="Twitch application Client Secret (keep private)",
                    on_change=lambda e, k=client_secret_key: self._set_cred(
                        k, "" if e.value is None else str(e.value)
                    ),
                )
            CopyRow("Redirect URI", TWITCH_OAUTH_REDIRECT_URI, copied="Redirect URI copied")
            hint(
                "Create an app with the redirect URI above, then paste its Client ID and Secret here and Save.",
                link_label="dev.twitch.tv/console",
                url="https://dev.twitch.tv/console",
            )

    def build(self, parent_container) -> None:
        self._load_from_state()
        self._page = page = IntegrationPage(
            parent_container,
            "twitch",
            "Twitch",
            "Alerts, chat, raids and channel points from your channel",
            on_save=self.save,
            on_discard=self.discard,
            is_dirty=lambda: self.dirty,
            help_topic="twitch_setup",
            help_tooltip="Twitch setup guide",
        )
        with page.column():
            with section("account_circle", "Accounts"):
                self._build_account_card(
                    "Main account",
                    "main",
                    connect_handler=self._handle_main_oauth_connection,
                    refresh_handler=self._refresh_main_status,
                    client_id_key="client_id",
                    client_secret_key="client_secret",
                    client_id_placeholder="Twitch API Client ID",
                    client_secret_placeholder="Twitch API Client Secret",
                )
                self._build_account_card(
                    "Chatbot account",
                    "chatbot",
                    connect_handler=self._handle_chatbot_oauth_connection,
                    refresh_handler=self._refresh_chatbot_status,
                    client_id_key="chatbot_client_id",
                    client_secret_key="chatbot_client_secret",
                    client_id_placeholder="Chatbot Twitch API Client ID",
                    client_secret_placeholder="Chatbot Twitch API Client Secret",
                )
        with page.column():
            with section("campaign", "Alerts"):
                self.ui_elements["alerts_enabled"] = switch_row(
                    "Process Twitch alerts",
                    "Follows, subs, bits, raids and redemptions go to the alert queue",
                    bool(getattr(self.buffer, "alerts_enabled", True)),
                    lambda v: self._set("alerts_enabled", v),
                )
            with section("diversity_3", "Raids"):
                self.ui_elements["auto_raid_helix_shoutout"] = switch_row(
                    "Send a native Twitch shoutout",
                    "Uses Twitch's /shoutout for incoming raids",
                    bool(self._app_settings_buffer.auto_raid_helix_shoutout),
                    lambda v: self._set_app_setting("auto_raid_helix_shoutout", v),
                )
                self.ui_elements["auto_raid_chat_shoutout"] = switch_row(
                    "Send a chat raid shoutout",
                    "Posts a link to the raider's channel in chat",
                    bool(self._app_settings_buffer.auto_raid_chat_shoutout),
                    lambda v: self._set_app_setting("auto_raid_chat_shoutout", v),
                )
            with section("menu_book", "Setup"):
                self._steps = ui.html("", sanitize=False).classes("w-full")
        self._refresh_overview()
        self._status_timer = layout_schedule(3.0, self._refresh_status, active=True)
        self._token_timer = layout_schedule(
            1.0, self._refresh_token_countdowns, active=True
        )
        self._refresh_status()

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
        self._saved_creds = dict(self._creds)

        app_settings = state_manager.get_app_settings()
        self._app_settings_buffer = dataobjects.AppSettings(
            **{
                k: getattr(app_settings, k)
                for k in app_settings.__dataclass_fields__.keys()
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

    def _set_app_setting(self, field: str, value) -> None:
        if not self._app_settings_buffer:
            return
        if getattr(self._app_settings_buffer, field) != value:
            setattr(self._app_settings_buffer, field, value)
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
        self._saved_creds = dict(self._creds)

        if self._app_settings_buffer:
            for field in ("auto_raid_helix_shoutout", "auto_raid_chat_shoutout"):
                state_manager.update_app_setting(
                    field, getattr(self._app_settings_buffer, field)
                )

        if state_manager.save_changes():
            notify("", type="success", source="twitch", title="Twitch settings saved")
            self.dirty = False
        # A failed save_changes() was already reported once (changes stay pending).
        self._refresh_overview()

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
            elif (
                self._app_settings_buffer
                and hasattr(self._app_settings_buffer, key)
                and hasattr(element, "value")
            ):
                element.value = getattr(self._app_settings_buffer, key)
            elif hasattr(self.buffer, key) and hasattr(element, "value"):
                element.value = getattr(self.buffer, key)
        self.dirty = False
        self._refresh_overview()

    def _handle_main_oauth_connection(self) -> None:
        """Handle the Connect Main Account button click to trigger OAuth reconnection"""
        try:
            import logging

            logger = logging.getLogger(__name__)
            logger.info("User clicked Connect Main Account button")

            # Check if client ID and secret are provided
            if not self._creds.get("client_id") or not self._creds.get("client_secret"):
                notify(
                    "",
                    type="warning",
                    source="twitch",
                    title="Enter your Twitch Client ID and Client Secret first",
                    hint="Get them from your app at dev.twitch.tv/console.",
                )
                return

            self._start_connect_button("main")

            work = notify_progress(
                "Connecting your Twitch account",
                source="twitch",
                message="Opening Twitch sign-in in your browser…",
            )

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
            oauth_timer_holder: Dict[str, Any] = {"timer": None}

            def check_oauth_result():
                if oauth_result["status"] == "connecting":
                    return
                elif (
                    oauth_result["status"] == "complete"
                    and not oauth_result["notification_shown"]
                ):
                    try:
                        oauth_result["notification_shown"] = True
                        if oauth_result["success"]:
                            # trigger_oauth_reconnection() returns True once the browser
                            # flow has *started*; the status watcher announces the
                            # connection when Twitch confirms it.
                            work.done(
                                "Finish signing in to Twitch in your browser",
                                "Mycelian connects as soon as you approve it.",
                                type="info",
                            )
                            logger.info("Main Twitch OAuth browser flow started")
                        else:
                            work.fail(
                                "Couldn't start Twitch sign-in",
                                hint=(
                                    "Check the Client ID and Secret, and that your Twitch app's "
                                    "OAuth redirect URL matches the one shown here, then try again."
                                ),
                            )
                            logger.error("Main Twitch OAuth connection failed")

                        # Refresh the status display and reset the button
                        self._refresh_main_status()
                        self._reset_connect_button("main")
                    except Exception as e:
                        logger.error(
                            f"Error updating UI after main Twitch OAuth: {str(e)}"
                        )
                    _stop_ui_timer(oauth_timer_holder.get("timer"))
                    return
                elif (
                    oauth_result["status"] == "error"
                    and not oauth_result["notification_shown"]
                ):
                    try:
                        oauth_result["notification_shown"] = True
                        work.fail(
                            "Couldn't start Twitch sign-in",
                            hint="Check the Client ID and Secret, then try again.",
                            details=str(oauth_result["error"] or ""),
                        )
                        self._reset_connect_button("main")
                    except Exception as ui_error:
                        logger.error(f"Error handling UI error update: {str(ui_error)}")
                    _stop_ui_timer(oauth_timer_holder.get("timer"))
                    return
                elif oauth_result["notification_shown"]:
                    _stop_ui_timer(oauth_timer_holder.get("timer"))
                    return
                _stop_ui_timer(oauth_timer_holder.get("timer"))

            # Start timer to check results with timeout protection
            check_count = {"count": 0}  # Use dict to maintain reference in closure
            # trigger_oauth_reconnection() waits up to 90 s for the browser flow to
            # start; wait a little longer so its answer arrives first.
            max_checks = 500  # 100 seconds (500 * 0.2s)

            def check_oauth_result_with_timeout():
                check_count["count"] += 1

                # Add timeout protection
                if (
                    check_count["count"] >= max_checks
                    and not oauth_result["notification_shown"]
                ):
                    oauth_result["notification_shown"] = True
                    logger.warning("Main OAuth check timed out after 100 seconds")
                    work.done(
                        "Still waiting for Twitch sign-in",
                        "Finish it in your browser, or try connecting again.",
                        type="warning",
                    )
                    self._reset_connect_button("main")
                    _stop_ui_timer(oauth_timer_holder.get("timer"))
                    return

                # Call the original check function
                check_oauth_result()

            oauth_timer = layout_schedule(0.2, check_oauth_result_with_timeout)
            oauth_timer_holder["timer"] = oauth_timer
            # Store timer reference for potential cleanup
            self._active_timers = getattr(self, "_active_timers", [])
            self._active_timers.append(oauth_timer)

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                f"Error handling main Twitch OAuth connection: {str(e)}", exc_info=True
            )
            _fail_or_notify(
                locals().get("work"),
                "twitch",
                "Couldn't start Twitch sign-in",
                f"{type(e).__name__}: {e}",
            )
            self._reset_connect_button("main")

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
                    "",
                    type="warning",
                    source="chatbot",
                    title="Enter the chatbot's Twitch Client ID and Client Secret first",
                    hint="Get them from your app at dev.twitch.tv/console.",
                )
                return

            self._start_connect_button("chatbot")

            work = notify_progress(
                "Connecting the chatbot account",
                source="chatbot",
                message="Sign in with the bot's Twitch account in the browser page that opens.",
            )

            # Persist chatbot credentials without flushing the rest of the Twitch tab.
            self._save_settings_only()

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
            oauth_timer_holder: Dict[str, Any] = {"timer": None}

            def check_oauth_result():
                if oauth_result["status"] == "connecting":
                    return
                elif (
                    oauth_result["status"] == "complete"
                    and not oauth_result["notification_shown"]
                ):
                    try:
                        oauth_result["notification_shown"] = True
                        if oauth_result["success"]:
                            work.done("Chatbot account connected")
                            logger.info("Chatbot Twitch OAuth connection successful")
                        else:
                            work.fail(
                                "Couldn't connect the chatbot account",
                                hint=(
                                    "Check the chatbot Client ID and Secret, and sign in with the bot "
                                    "account (not your main account), then try again."
                                ),
                            )
                            logger.error("Chatbot Twitch OAuth connection failed")

                        # Refresh the status display and reset the button
                        self._refresh_chatbot_status()
                        self._reset_connect_button("chatbot")
                    except Exception as e:
                        logger.error(
                            f"Error updating UI after chatbot Twitch OAuth: {str(e)}"
                        )
                    _stop_ui_timer(oauth_timer_holder.get("timer"))
                    return
                elif (
                    oauth_result["status"] == "error"
                    and not oauth_result["notification_shown"]
                ):
                    try:
                        oauth_result["notification_shown"] = True
                        work.fail(
                            "Couldn't connect the chatbot account",
                            hint="Check the chatbot Client ID and Secret, then try again.",
                            details=str(oauth_result["error"] or ""),
                        )
                        self._reset_connect_button("chatbot")
                    except Exception as ui_error:
                        logger.error(f"Error handling UI error update: {str(ui_error)}")
                    _stop_ui_timer(oauth_timer_holder.get("timer"))
                    return
                elif oauth_result["notification_shown"]:
                    _stop_ui_timer(oauth_timer_holder.get("timer"))
                    return
                _stop_ui_timer(oauth_timer_holder.get("timer"))

            # Start timer to check results with timeout protection
            check_count = {"count": 0}  # Use dict to maintain reference in closure
            # trigger_chatbot_oauth_reconnection() gives up after 120 s; wait a
            # little longer so its answer arrives first.
            max_checks = 650  # 130 seconds (650 * 0.2s)

            def check_oauth_result_with_timeout():
                check_count["count"] += 1

                # Add timeout protection
                if (
                    check_count["count"] >= max_checks
                    and not oauth_result["notification_shown"]
                ):
                    oauth_result["notification_shown"] = True
                    logger.warning("Chatbot OAuth check timed out after 130 seconds")
                    work.done(
                        "Still waiting for the chatbot sign-in",
                        "Finish it in your browser, or try connecting the chatbot again.",
                        type="warning",
                    )
                    self._reset_connect_button("chatbot")
                    _stop_ui_timer(oauth_timer_holder.get("timer"))
                    return

                # Call the original check function
                check_oauth_result()

            oauth_timer = layout_schedule(0.2, check_oauth_result_with_timeout)
            oauth_timer_holder["timer"] = oauth_timer
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
            _fail_or_notify(
                locals().get("work"),
                "chatbot",
                "Couldn't start the chatbot sign-in",
                f"{type(e).__name__}: {e}",
            )
            self._reset_connect_button("chatbot")

    def _save_settings_only(self) -> None:
        """Save only Twitch / chatbot credentials without triggering a full tab save."""
        try:
            api_credentials_manager.update_twitch_credentials(
                client_id=self._creds.get("client_id", ""),
                client_secret=self._creds.get("client_secret", ""),
            )
            for key in ("client_id", "client_secret"):
                self._saved_creds[key] = self._creds.get(key, "")
            chatbot_id = (self._creds.get("chatbot_client_id") or "").strip() or None
            chatbot_secret = (self._creds.get("chatbot_client_secret") or "").strip() or None
            if chatbot_id or chatbot_secret:
                api_credentials_manager.update_chatbot_credentials(
                    client_id=chatbot_id,
                    client_secret=chatbot_secret,
                )
                self._saved_creds["chatbot_client_id"] = chatbot_id or ""
                self._saved_creds["chatbot_client_secret"] = chatbot_secret or ""
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error saving Twitch settings only: {str(e)}", exc_info=True)
        finally:
            self._refresh_overview()

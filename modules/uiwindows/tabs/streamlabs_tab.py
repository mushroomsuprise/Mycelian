# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Settings tab for Streamlabs donation alerts."""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from nicegui import ui

from ...api_credentials_manager import (
    get_streamlabs_credentials,
    update_streamlabs_credentials,
)
from ...donation_currency import ALERT_CURRENCIES
from ...donation_settings import get_settings, update_settings
from ...notification_engine import nav_actions_settings, notify, notify_progress
from ...streamlabs import (
    STREAMLABS_OAUTH_REDIRECT_URI,
    disconnect_streamlabs,
    get_streamlabs_client,
    get_streamlabs_status,
)
from ...ui_buttons import browser_link, outline_button, primary_button
from ...ui_form_controls import copy_text_button
from ...ui_timer import layout_schedule
from .integration_ui import (
    AccountCard,
    CopyRow,
    CredentialsFold,
    IntegrationPage,
    field,
    fields,
    hint,
    icon_html,
    link,
    row,
    section,
)

logger = logging.getLogger(__name__)

_REGISTER_URL = "https://dev.streamlabs.com/docs/register-your-application"


def _streamlabs_error_text(error: str) -> tuple:
    """Plain (title, hint) for a Streamlabs sign-in error; the raw text goes in details."""
    text = str(error or "").strip()
    low = text.lower()
    if "already in progress" in low:
        return (
            "Streamlabs sign-in is already open",
            "Finish it in your browser, or wait a few minutes and try again.",
        )
    if "port" in low and "in use" in low:
        return (
            "Another app is using the port Streamlabs sign-in needs (9975)",
            "Close the other app (or another Mycelian window), then try again.",
        )
    if "not completed" in low:
        return (
            "Streamlabs sign-in timed out",
            "Press Connect and approve Mycelian within 3 minutes.",
        )
    if "access_denied" in low or "denied" in low:
        return (
            "Streamlabs sign-in was cancelled",
            "Press Connect and choose Approve on the Streamlabs page.",
        )
    return (
        "Couldn't connect to Streamlabs",
        "Check the Client ID, Client Secret and redirect URI in your Streamlabs app, then try again.",
    )


class StreamlabsTab:
    name = "Streamlabs"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.ui_elements: Dict[str, Any] = {}
        self._creds: Dict[str, str] = {"client_id": "", "client_secret": ""}
        self._creds_saved: bool = False
        self._currency: str = "USD"
        self._status_timer: Optional[Any] = None
        self._connect_in_progress: bool = False
        self._page: Optional[IntegrationPage] = None
        self._card: Optional[AccountCard] = None
        self._fold: Optional[CredentialsFold] = None
        self._connect_button: Optional[Any] = None
        self._disconnect_button: Optional[Any] = None

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

    def _load(self) -> None:
        creds = get_streamlabs_credentials()
        self._creds = {
            "client_id": (creds.get("client_id") or "").strip(),
            "client_secret": (creds.get("client_secret") or "").strip(),
        }
        self._creds_saved = bool(self._creds["client_id"] and self._creds["client_secret"])
        currency = (get_settings().alert_currency or "USD").upper()
        self._currency = currency if currency in ALERT_CURRENCIES else "USD"
        self.dirty = False

    def _str_from_value_event(self, event: Any) -> str:
        value = getattr(event, "value", None)
        if value is not None and not isinstance(value, (list, tuple)):
            return str(value)
        args = getattr(event, "args", None)
        if isinstance(args, str):
            return args
        if isinstance(args, (list, tuple)) and args:
            return str(args[0])
        return ""

    def _set_cred(self, field: str, value: str) -> None:
        text = (value or "").strip()
        if field == "client_secret" and not text and self._creds.get(field):
            return
        if self._creds.get(field) != text:
            self._creds[field] = text
            self.dirty = True

    def _set_currency(self, value: str) -> None:
        code = (value or "USD").upper()
        if code != self._currency:
            self._currency = code
            self.dirty = True

    def has_unsaved_changes(self) -> bool:
        return bool(self.dirty)

    def save(self, silent: bool = False) -> None:
        try:
            update_streamlabs_credentials(
                client_id=self._creds.get("client_id", ""),
                client_secret=self._creds.get("client_secret", ""),
            )
            update_settings(alert_currency=self._currency or "USD")
            self.dirty = False
            self._creds_saved = bool(
                self._creds.get("client_id") and self._creds.get("client_secret")
            )
            if not silent:
                notify("", type="success", source="streamlabs", title="Streamlabs settings saved")
        except Exception as exc:
            logger.error("Streamlabs save failed: %s", exc, exc_info=True)
            notify(
                "Your changes are still in the form.",
                type="error",
                source="streamlabs",
                title="Couldn't save Streamlabs settings",
                hint="Check the connection in Settings → Database, then press Save again.",
                details=f"{type(exc).__name__}: {exc}",
                actions=nav_actions_settings("Database"),
            )
        self._refresh_status()

    def discard(self) -> None:
        self._load()
        if "client_id" in self.ui_elements:
            self.ui_elements["client_id"].value = self._creds.get("client_id", "")
        if "client_secret" in self.ui_elements:
            self.ui_elements["client_secret"].value = self._creds.get("client_secret", "")
        if "currency" in self.ui_elements:
            self.ui_elements["currency"].value = self._currency
        self.dirty = False
        self._refresh_status()

    def _status(self) -> tuple:
        """(card state kind, header pill text, card name, card state line, connected)."""
        info = get_streamlabs_status()
        status = str(info.get("status") or "Disconnected")
        detail = str(info.get("error") or "").strip()
        low = status.lower()
        if status == "Connected":
            return "ok", "Connected", "Connected", detail or "Listening for donations", True
        if self._connect_in_progress or low == "connecting":
            line = detail or "Approve Mycelian in the Streamlabs page that opens"
            return "busy", "Connecting", "Connecting", line, False
        if low == "disconnected":
            if detail:
                line = detail
            elif self._creds_saved:
                line = "Press Connect to sign in to Streamlabs"
            else:
                line = "Add your app's client ID and secret, then connect"
            return "off", "Not connected", "Not connected", line, False
        return "error", status, status, detail or "Press Connect to try again", False

    def _refresh_status(self) -> None:
        try:
            kind, short, name, line, connected = self._status()
            if self._page is not None:
                self._page.set_status([("error" if kind == "off" else kind, short)])
            if self._card is not None:
                self._card.set(name, kind, line)
            if self._fold is not None:
                self._fold.set_saved(self._creds_saved)
            if self._disconnect_button is not None:
                self._disconnect_button.set_visibility(kind != "off")
            button = self._connect_button
            if button is not None:
                button.set_visibility(not connected)
                if self._connect_in_progress:
                    button.set_text("Connecting…")
                    button.disable()
                else:
                    button.set_text("Connect")
                    button.enable()
        except Exception:
            logger.debug("Streamlabs status refresh failed", exc_info=True)

    def _show_guide(self) -> None:
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl"):
            ui.label("Connect Streamlabs").classes("text-xl font-bold")
            ui.label(
                "Mycelian only reads donations. Follows, subscriptions, and bits stay on Twitch."
            ).classes("text-sm secondary-text")
            with ui.column().classes("gap-3"):
                ui.label("1. Sign in to Streamlabs").classes("font-semibold")
                browser_link("streamlabs.com/login", "https://streamlabs.com/login")
                ui.label("2. Register an application").classes("font-semibold")
                ui.label(
                    "Create an app named Mycelian from the apps page. "
                    "The written steps are in Streamlabs' register guide."
                ).classes("text-sm")
                browser_link(
                    "Register your application",
                    "https://dev.streamlabs.com/docs/register-your-application",
                )
                browser_link(
                    "Streamlabs apps",
                    "https://streamlabs.com/dashboard#/apps",
                )
                ui.label("3. Use this redirect URI exactly").classes("font-semibold")
                ui.label(STREAMLABS_OAUTH_REDIRECT_URI).classes("text-sm font-mono")
                copy_text_button(STREAMLABS_OAUTH_REDIRECT_URI, tooltip="Copy redirect URI")
                ui.label(
                    "A mismatch is why Connect returns to an error page."
                ).classes("text-sm secondary-text")
                ui.label("4. Whitelist your Streamlabs username").classes("font-semibold")
                ui.label(
                    "Until the app is approved, only whitelisted users can authorize it. "
                    "Add the account that receives donations. Public review is not required."
                ).classes("text-sm")
                ui.label("5. Copy the client id and secret into Mycelian").classes(
                    "font-semibold"
                )
                ui.label(
                    "Connect asks only for donations.read and socket.token."
                ).classes("text-sm")
                browser_link(
                    "Scope list",
                    "https://dev.streamlabs.com/docs/scopes",
                )
            outline_button("Close", dialog.close)
        dialog.open()

    def _connect(self) -> None:
        if self._connect_in_progress:
            return
        self.save(silent=True)
        creds = get_streamlabs_credentials()
        if not (creds.get("client_id") or "").strip() or not (
            creds.get("client_secret") or ""
        ).strip():
            notify(
                "",
                type="warning",
                source="streamlabs",
                title="Enter your Streamlabs Client ID and Client Secret first",
                hint="Press How to connect to see where to get them.",
            )
            return
        self._connect_in_progress = True
        self._refresh_status()
        progress = notify_progress(
            "Connecting to Streamlabs",
            source="streamlabs",
            message="Approve Mycelian in the Streamlabs page that opens (up to 3 minutes).",
        )

        def work() -> None:
            try:
                error = get_streamlabs_client().begin_oauth(
                    creds["client_id"], creds["client_secret"]
                )
                if error:
                    title, hint = _streamlabs_error_text(error)
                    progress.fail(title, hint=hint, details=error)
                else:
                    progress.done("Connected to Streamlabs")
            except Exception as exc:
                logger.error("Streamlabs connect failed: %s", exc, exc_info=True)
                progress.fail(
                    "Couldn't connect to Streamlabs",
                    hint="Try again. If it keeps happening, copy the details and report it.",
                    details=f"{type(exc).__name__}: {exc}",
                )
            finally:
                self._connect_in_progress = False

        threading.Thread(target=work, name="StreamlabsOAuth", daemon=True).start()

    def _disconnect(self) -> None:
        try:
            disconnect_streamlabs()
        except Exception as exc:
            logger.error("Streamlabs disconnect failed: %s", exc, exc_info=True)
            notify(
                "",
                type="warning",
                source="streamlabs",
                title="Couldn't disconnect Streamlabs",
                hint="Try again.",
                details=f"{type(exc).__name__}: {exc}",
            )
            self._refresh_status()
            return
        self._refresh_status()
        notify("", type="info", source="streamlabs", title="Streamlabs disconnected")

    def _register_hint(self) -> None:
        with ui.element("div").classes("itg-hint"):
            ui.html(icon_html("info"), sanitize=False)
            with ui.element("span"):
                ui.html("Register an app at ", sanitize=False).classes("inline")
                link("dev.streamlabs.com", _REGISTER_URL).classes("inline")
                ui.html(" with the redirect URI above. ", sanitize=False).classes("inline")
                guide = ui.label("How to connect").classes("itg-link inline")
                guide.on("click", lambda _e: self._show_guide())
                ui.html(" walks through it.", sanitize=False).classes("inline")

    def build(self, parent_container) -> None:
        self._load()
        self._page = page = IntegrationPage(
            parent_container,
            "streamlabs",
            "Streamlabs",
            "Donations from Streamlabs as alerts",
            on_save=self.save,
            on_discard=self.discard,
            is_dirty=self.has_unsaved_changes,
            help_topic="guide_streamlabs",
            help_tooltip="Streamlabs setup help",
        )
        with page.column():
            with section("account_circle", "Account"):
                self._card = AccountCard("streamlabs", "Streamlabs")
                with self._card.actions:
                    self._disconnect_button = (
                        ui.button(icon="logout", on_click=self._disconnect)
                        .props("flat round dense")
                        .classes("itg-ibtn")
                        .tooltip("Disconnect")
                    )
                    outline_button("How to connect", self._show_guide, icon="menu_book").props(
                        "dense"
                    ).classes("px-3")
                    self._connect_button = (
                        primary_button("Connect", self._connect, icon="login")
                        .props("dense")
                        .classes("px-3")
                    )
                self._fold = self._card.credentials(
                    "Client ID & secret", opened=not self._creds_saved
                )
                with self._fold.body:
                    with fields():
                        self.ui_elements["client_id"] = field(
                            "Client ID",
                            value=self._creds.get("client_id", ""),
                            placeholder="Streamlabs application client id",
                            tip="Streamlabs application client id",
                            on_change=lambda event: self._set_cred(
                                "client_id", self._str_from_value_event(event)
                            ),
                        )
                        self.ui_elements["client_secret"] = field(
                            "Client secret",
                            value=self._creds.get("client_secret", ""),
                            password=True,
                            placeholder="Stored encrypted",
                            tip="Streamlabs application client secret, stored encrypted",
                            on_change=lambda event: self._set_cred(
                                "client_secret", self._str_from_value_event(event)
                            ),
                        )
                    CopyRow(
                        "Redirect URI",
                        STREAMLABS_OAUTH_REDIRECT_URI,
                        copied="Redirect URI copied",
                    )
                    self._register_hint()
        with page.column():
            with section("payments", "Donations"):
                _row, ctrl = row(
                    "Alert currency",
                    "Donation alert amounts are matched in this currency "
                    "(shared with StreamElements)",
                )
                with ctrl:
                    self.ui_elements["currency"] = (
                        ui.select(options=list(ALERT_CURRENCIES), value=self._currency)
                        .props("outlined dense options-dense")
                        .style("width:120px")
                        .tooltip("Donation alert thresholds use this currency")
                    )
                    self.ui_elements["currency"].on_value_change(
                        lambda event: self._set_currency(self._str_from_value_event(event))
                    )
                hint("Only tips come from Streamlabs. Follows, subs and bits stay on Twitch.")
        with page.root:
            self._status_timer = ui.timer(2.0, self._refresh_status)
        self._refresh_status()

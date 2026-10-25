# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Settings tab for StreamElements donation alerts."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from nicegui import ui

from ...donation_currency import ALERT_CURRENCIES
from ...donation_settings import get_settings, update_settings
from ...notification_engine import nav_actions_settings, notify
from ...streamelements import (
    connect_streamelements,
    disconnect_streamelements,
    get_streamelements_status,
)
from ...ui_buttons import browser_link, outline_button, primary_button
from ...ui_timer import layout_schedule
from .integration_ui import (
    AccountCard,
    CredentialsFold,
    IntegrationPage,
    field,
    fields,
    hint,
    row,
    section,
)

logger = logging.getLogger(__name__)


class StreamElementsTab:
    name = "StreamElements"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.ui_elements: Dict[str, Any] = {}
        self._jwt: str = ""
        self._jwt_saved: bool = False
        self._currency: str = "USD"
        self._status_timer: Optional[Any] = None
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
        settings = get_settings()
        self._jwt = (settings.streamelements_jwt or "").strip()
        self._jwt_saved = bool(self._jwt)
        currency = (settings.alert_currency or "USD").upper()
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

    def _set_jwt(self, value: str) -> None:
        text = (value or "").strip()
        if not text and self._jwt:
            return
        if text != self._jwt:
            self._jwt = text
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
            if self._jwt:
                update_settings(
                    streamelements_jwt=self._jwt,
                    alert_currency=self._currency or "USD",
                )
                self._jwt_saved = True
            else:
                update_settings(alert_currency=self._currency or "USD")
            self.dirty = False
            if not silent:
                notify("", type="success", source="streamelements", title="StreamElements settings saved")
        except Exception as exc:
            logger.error("StreamElements save failed: %s", exc, exc_info=True)
            notify(
                "Your changes are still in the form.",
                type="error",
                source="streamelements",
                title="Couldn't save StreamElements settings",
                hint="Check the connection in Settings → Database, then press Save again.",
                details=f"{type(exc).__name__}: {exc}",
                actions=nav_actions_settings("Database"),
            )
        self._refresh_status()

    def discard(self) -> None:
        self._load()
        if "jwt" in self.ui_elements:
            self.ui_elements["jwt"].value = self._jwt
        if "currency" in self.ui_elements:
            self.ui_elements["currency"].value = self._currency
        self.dirty = False
        self._refresh_status()

    def _status(self) -> tuple:
        """(card state kind, header pill text, card name, card state line, connected)."""
        info = get_streamelements_status()
        status = str(info.get("status") or "Disconnected")
        detail = str(info.get("error") or "").strip()
        low = status.lower()
        if status == "Connected":
            return "ok", "Connected", "Connected", detail or "Listening for tips", True
        if low == "connecting":
            return "busy", "Connecting", "Connecting", detail or "Opening the StreamElements connection", False
        if low == "disconnected":
            if detail:
                line = detail
            elif self._jwt_saved:
                line = "Press Connect to start listening for tips"
            else:
                line = "Paste your channel's JWT token, then connect"
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
                self._fold.set_saved(self._jwt_saved)
            if self._disconnect_button is not None:
                self._disconnect_button.set_visibility(kind != "off")
            if self._connect_button is not None:
                self._connect_button.set_visibility(not connected)
        except Exception:
            logger.debug("StreamElements status refresh failed", exc_info=True)

    def _show_guide(self) -> None:
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl"):
            ui.label("Connect StreamElements").classes("text-xl font-bold")
            ui.label(
                "Paste the channel JWT. Mycelian listens for tips only. "
                "Emulate tip in the StreamElements overlay is included."
            ).classes("text-sm secondary-text")
            with ui.column().classes("gap-3"):
                ui.label("1. Sign in").classes("font-semibold")
                browser_link(
                    "streamelements.com/dashboard",
                    "https://streamelements.com/dashboard",
                )
                ui.label("2. Select the channel that receives tips").classes(
                    "font-semibold"
                )
                ui.label(
                    "If Twitch, YouTube, and Kick are all linked, click the avatar "
                    "in the top-right and choose the right channel. Each one has its own JWT."
                ).classes("text-sm")
                ui.label("3. Open Account, then Channels").classes("font-semibold")
                browser_link(
                    "Account → Channels",
                    "https://streamelements.com/dashboard/account/channels",
                )
                ui.label("4. Turn on Show secrets and copy the JWT token").classes(
                    "font-semibold"
                )
                ui.label(
                    "Copy the JWT, not the Account ID. The JWT is private."
                ).classes("text-sm")
                browser_link(
                    "How to locate your Account ID and JWT token",
                    "https://support.streamelements.com/hc/en-us/articles/10474949304466-How-to-Locate-Your-Account-ID-and-JWT-Token",
                )
            outline_button("Close", dialog.close)
        dialog.open()

    def _connect(self) -> None:
        self.save(silent=True)
        error = connect_streamelements(self._jwt)
        if error:
            notify(
                "",
                type="warning",
                source="streamelements",
                title=error,
                hint="Press How to connect to see where to find the JWT.",
            )
            return
        self._jwt_saved = True
        # The status line shows "Connecting"; the result is announced by the status watcher.
        self._refresh_status()

    def _disconnect(self) -> None:
        try:
            disconnect_streamelements()
        except Exception as exc:
            logger.error("StreamElements disconnect failed: %s", exc, exc_info=True)
            notify(
                "",
                type="warning",
                source="streamelements",
                title="Couldn't disconnect StreamElements",
                hint="Try again.",
                details=f"{type(exc).__name__}: {exc}",
            )
            self._refresh_status()
            return
        self._jwt = ""
        self._jwt_saved = False
        if "jwt" in self.ui_elements:
            self.ui_elements["jwt"].value = ""
        self.dirty = False
        self._refresh_status()
        notify("", type="info", source="streamelements", title="StreamElements disconnected")

    def build(self, parent_container) -> None:
        self._load()
        self._page = page = IntegrationPage(
            parent_container,
            "streamelements",
            "StreamElements",
            "Tips from StreamElements as alerts",
            on_save=self.save,
            on_discard=self.discard,
            is_dirty=self.has_unsaved_changes,
            help_topic="guide_streamelements",
            help_tooltip="StreamElements setup help",
        )
        with page.column():
            with section("account_circle", "Account"):
                self._card = AccountCard("streamelements", "StreamElements")
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
                self._fold = self._card.credentials("JWT token", opened=not self._jwt_saved)
                with self._fold.body:
                    with fields(one=True):
                        self.ui_elements["jwt"] = field(
                            "JWT token",
                            value=self._jwt,
                            password=True,
                            placeholder="Stored encrypted",
                            tip="StreamElements channel JWT (Account → Channels → Show secrets), stored encrypted",
                            on_change=lambda event: self._set_jwt(self._str_from_value_event(event)),
                        )
        with page.column():
            with section("payments", "Donations"):
                _row, ctrl = row(
                    "Alert currency",
                    "Donation alert amounts are matched in this currency "
                    "(shared with Streamlabs)",
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
                hint("Only tips come from StreamElements. Follows, subs and bits stay on Twitch.")
        with page.root:
            self._status_timer = ui.timer(2.0, self._refresh_status)
        self._refresh_status()

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Settings tab for StreamElements donation alerts."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from nicegui import ui

from ...donation_currency import ALERT_CURRENCIES
from ...donation_settings import get_settings, update_settings
from ...help_system.contextual_help import help_button
from ...notification_engine import notify
from ...streamelements import (
    connect_streamelements,
    disconnect_streamelements,
    get_streamelements_status,
)
from ...ui_buttons import outline_button, primary_button
from ...ui_form_controls import form_select, form_sensitive_input
from ...ui_settings_layout import (
    settings_action_row,
    settings_form_grid,
    settings_section,
    settings_status_band,
    settings_surface,
)
from ...ui_timer import layout_schedule

logger = logging.getLogger(__name__)


def _status_classes(status: str) -> str:
    text = (status or "").lower()
    if text == "connected":
        return "font-semibold text-sm text-theme-success"
    if "connect" in text:
        return "font-semibold text-sm text-theme-warning"
    return "font-semibold text-sm text-theme-error"


class StreamElementsTab:
    name = "StreamElements"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.ui_elements: Dict[str, Any] = {}
        self._jwt: str = ""
        self._currency: str = "USD"
        self._status_timer: Optional[Any] = None

    def on_enter(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = True
        layout_schedule(0.05, self._refresh_status, once=True)

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False

    def _load(self) -> None:
        settings = get_settings()
        self._jwt = (settings.streamelements_jwt or "").strip()
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
            else:
                update_settings(alert_currency=self._currency or "USD")
            self.dirty = False
            if not silent:
                notify("StreamElements settings saved.", type="positive")
        except Exception as exc:
            logger.error("StreamElements save failed: %s", exc, exc_info=True)
            notify("Could not save StreamElements settings.", type="negative")

    def discard(self) -> None:
        self._load()
        if "jwt" in self.ui_elements:
            self.ui_elements["jwt"].value = self._jwt
        if "currency" in self.ui_elements:
            self.ui_elements["currency"].value = self._currency
        self.dirty = False

    def _refresh_status(self) -> None:
        info = get_streamelements_status()
        status = str(info.get("status") or "Disconnected")
        label = self.ui_elements.get("status_label")
        if label is not None:
            detail = str(info.get("error") or "").strip()
            label.set_text(status if not detail else f"{status}: {detail}")
            label.classes(replace=_status_classes(status))

    def _show_guide(self) -> None:
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl"):
            ui.label("Connect StreamElements").classes("text-xl font-bold")
            ui.label(
                "Paste the channel JWT. Mycelian listens for tips only. "
                "Emulate tip in the StreamElements overlay is included."
            ).classes("text-sm secondary-text")
            with ui.column().classes("gap-3"):
                ui.label("1. Sign in").classes("font-semibold")
                ui.link(
                    "streamelements.com/dashboard",
                    "https://streamelements.com/dashboard",
                ).classes("text-theme-info")
                ui.label("2. Select the channel that receives tips").classes(
                    "font-semibold"
                )
                ui.label(
                    "If Twitch, YouTube, and Kick are all linked, click the avatar "
                    "in the top-right and choose the right channel. Each one has its own JWT."
                ).classes("text-sm")
                ui.label("3. Open Account, then Channels").classes("font-semibold")
                ui.link(
                    "Account → Channels",
                    "https://streamelements.com/dashboard/account/channels",
                ).classes("text-theme-info")
                ui.label("4. Turn on Show secrets and copy the JWT token").classes(
                    "font-semibold"
                )
                ui.label(
                    "Copy the JWT, not the Account ID. The JWT is private."
                ).classes("text-sm")
                ui.link(
                    "How to locate your Account ID and JWT token",
                    "https://support.streamelements.com/hc/en-us/articles/10474949304466-How-to-Locate-Your-Account-ID-and-JWT-Token",
                ).classes("text-theme-info")
            outline_button("Close", dialog.close)
        dialog.open()

    def _connect(self) -> None:
        self.save(silent=True)
        error = connect_streamelements(self._jwt)
        if error:
            notify(error, type="warning")
            return
        self._refresh_status()
        notify("Connecting to StreamElements.", type="info")

    def _disconnect(self) -> None:
        disconnect_streamelements()
        self._jwt = ""
        if "jwt" in self.ui_elements:
            self.ui_elements["jwt"].value = ""
        self.dirty = False
        self._refresh_status()
        notify("StreamElements disconnected.", type="info")

    def build(self, parent_container) -> None:
        self._load()
        with settings_surface(parent_container):
            with settings_status_band():
                with ui.column().classes("gap-0"):
                    ui.label("Status").classes("text-xs secondary-text")
                    self.ui_elements["status_label"] = ui.label("Disconnected").classes(
                        "font-semibold text-sm"
                    )
            self._refresh_status()
            self._status_timer = ui.timer(2.0, self._refresh_status)

            with settings_section(
                "Donations",
                subtitle="Tips from StreamElements become donation alerts.",
            ):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(
                        "Alert amounts are matched in the currency below."
                    ).classes("text-xs secondary-text")
                    with ui.row().classes("items-center gap-2"):
                        outline_button("How to connect", self._show_guide)
                        help_button(
                            topic_id="integrations_streamelements",
                            tooltip="StreamElements setup help",
                            size="sm",
                        )
                with settings_form_grid(columns=1):
                    self.ui_elements["currency"] = form_select(
                        tooltip="Donation alert thresholds use this currency",
                        label="Alert currency",
                        options=list(ALERT_CURRENCIES),
                        value=self._currency,
                    )
                    self.ui_elements["currency"].on_value_change(
                        lambda event: self._set_currency(self._str_from_value_event(event))
                    )
                    self.ui_elements["jwt"] = form_sensitive_input(
                        tooltip="StreamElements channel JWT, stored encrypted",
                        label="JWT token",
                        value=self._jwt,
                    )
                    self.ui_elements["jwt"].on_value_change(
                        lambda event: self._set_jwt(self._str_from_value_event(event))
                    )
                with ui.row().classes("gap-2"):
                    primary_button("Connect", self._connect)
                    outline_button("Disconnect", self._disconnect)

            settings_action_row(discard=self.discard, save=self.save)

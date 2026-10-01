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
from ...help_system.contextual_help import help_button
from ...notification_engine import notify
from ...streamlabs import (
    STREAMLABS_OAUTH_REDIRECT_URI,
    disconnect_streamlabs,
    get_streamlabs_client,
    get_streamlabs_status,
)
from ...ui_buttons import outline_button, primary_button
from ...ui_form_controls import copy_text_button, form_select, form_sensitive_input
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


class StreamlabsTab:
    name = "Streamlabs"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.ui_elements: Dict[str, Any] = {}
        self._creds: Dict[str, str] = {"client_id": "", "client_secret": ""}
        self._currency: str = "USD"
        self._status_timer: Optional[Any] = None
        self._connect_in_progress: bool = False

    def on_enter(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = True
        layout_schedule(0.05, self._refresh_status, once=True)

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False

    def _load(self) -> None:
        creds = get_streamlabs_credentials()
        self._creds = {
            "client_id": (creds.get("client_id") or "").strip(),
            "client_secret": (creds.get("client_secret") or "").strip(),
        }
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
            if not silent:
                notify("Streamlabs settings saved.", type="positive")
        except Exception as exc:
            logger.error("Streamlabs save failed: %s", exc, exc_info=True)
            notify("Could not save Streamlabs settings.", type="negative")

    def discard(self) -> None:
        self._load()
        if "client_id" in self.ui_elements:
            self.ui_elements["client_id"].value = self._creds.get("client_id", "")
        if "client_secret" in self.ui_elements:
            self.ui_elements["client_secret"].value = self._creds.get("client_secret", "")
        if "currency" in self.ui_elements:
            self.ui_elements["currency"].value = self._currency
        self.dirty = False

    def _refresh_status(self) -> None:
        info = get_streamlabs_status()
        status = str(info.get("status") or "Disconnected")
        label = self.ui_elements.get("status_label")
        if label is not None:
            detail = str(info.get("error") or "").strip()
            label.set_text(status if not detail else f"{status}: {detail}")
            label.classes(replace=_status_classes(status))

    def _show_guide(self) -> None:
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-2xl"):
            ui.label("Connect Streamlabs").classes("text-xl font-bold")
            ui.label(
                "Mycelian only reads donations. Follows, subscriptions, and bits stay on Twitch."
            ).classes("text-sm secondary-text")
            with ui.column().classes("gap-3"):
                ui.label("1. Sign in to Streamlabs").classes("font-semibold")
                ui.link("streamlabs.com/login", "https://streamlabs.com/login").classes(
                    "text-theme-info"
                )
                ui.label("2. Register an application").classes("font-semibold")
                ui.label(
                    "Create an app named Mycelian from the apps page. "
                    "The written steps are in Streamlabs' register guide."
                ).classes("text-sm")
                ui.link(
                    "Register your application",
                    "https://dev.streamlabs.com/docs/register-your-application",
                ).classes("text-theme-info")
                ui.link(
                    "Streamlabs apps",
                    "https://streamlabs.com/dashboard#/apps",
                ).classes("text-theme-info")
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
                ui.link(
                    "Scope list",
                    "https://dev.streamlabs.com/docs/scopes",
                ).classes("text-theme-info")
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
            notify("Enter a Streamlabs client id and secret first.", type="warning")
            return
        self._connect_in_progress = True

        def work() -> None:
            try:
                error = get_streamlabs_client().begin_oauth(
                    creds["client_id"], creds["client_secret"]
                )
                if error:
                    notify(error, type="negative")
                else:
                    notify("Streamlabs connected.", type="positive")
            except Exception as exc:
                logger.error("Streamlabs connect failed: %s", exc, exc_info=True)
                notify("Streamlabs connection failed.", type="negative")
            finally:
                self._connect_in_progress = False

        threading.Thread(target=work, name="StreamlabsOAuth", daemon=True).start()

    def _disconnect(self) -> None:
        disconnect_streamlabs()
        self._refresh_status()
        notify("Streamlabs disconnected.", type="info")

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
                subtitle="Cash tips from Streamlabs become donation alerts.",
            ):
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(
                        "Alert amounts are matched in the currency below."
                    ).classes("text-xs secondary-text")
                    with ui.row().classes("items-center gap-2"):
                        outline_button("How to connect", self._show_guide)
                        help_button(
                            topic_id="integrations_streamlabs",
                            tooltip="Streamlabs setup help",
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
                    self.ui_elements["client_id"] = form_sensitive_input(
                        tooltip="Streamlabs application client id",
                        label="Client ID",
                        value=self._creds.get("client_id", ""),
                        password=False,
                    )
                    self.ui_elements["client_id"].on_value_change(
                        lambda event: self._set_cred(
                            "client_id", self._str_from_value_event(event)
                        )
                    )
                    self.ui_elements["client_secret"] = form_sensitive_input(
                        tooltip="Streamlabs application client secret, stored encrypted",
                        label="Client secret",
                        value=self._creds.get("client_secret", ""),
                    )
                    self.ui_elements["client_secret"].on_value_change(
                        lambda event: self._set_cred(
                            "client_secret", self._str_from_value_event(event)
                        )
                    )
                with ui.row().classes("gap-2"):
                    primary_button("Connect", self._connect)
                    outline_button("Disconnect", self._disconnect)

            settings_action_row(discard=self.discard, save=self.save)

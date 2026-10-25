# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from nicegui import run, ui

from ... import dataobjects
from ...ui_settings_layout import (
    settings_action_row,
    settings_form_grid,
    settings_status_band,
    settings_surface,
)
from ...dataobjects import OBSData, state_manager
from ...notification_engine import notify, notify_progress
from ...obs_service import obs_service
from ...help_system.contextual_help import help_button
from ...ui_form_controls import form_sensitive_input, form_sensitive_number
from ...ui_timer import layout_schedule

logger = logging.getLogger(__name__)


class ObsTab:
    name = "OBS"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.OBSData] = None
        self.ui_elements: Dict[str, Any] = {}
        self._status_timer: Optional[Any] = None
        self._test_in_progress: bool = False

    def on_enter(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = True
        layout_schedule(0.05, self._refresh_status, once=True)

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False

    def _refresh_status(self) -> None:
        try:
            phase = obs_service.get_connection_phase()
            ok, plug, rpc = obs_service.connection_details()
            if phase == "connecting":
                txt = "Connecting…"
                success = False
                pending = True
            elif phase == "disconnecting":
                txt = "Disconnecting…"
                success = False
                pending = True
            elif ok:
                txt = "Connected"
                if plug:
                    extra = plug
                    if rpc:
                        extra = f"{plug} · ws {rpc}"
                    txt = f"Connected — {extra}"
                success = True
                pending = False
            else:
                txt = (
                    self.buffer.connection_status
                    if self.buffer
                    else "Disconnected"
                )
                success = False
                pending = False
            if "status_label" in self.ui_elements:
                self.ui_elements["status_label"].set_text(txt)
                if pending:
                    css = "font-semibold text-theme-warning"
                elif success:
                    css = "font-semibold text-theme-success"
                else:
                    css = "font-semibold text-theme-error"
                self.ui_elements["status_label"].classes(replace=css)
        except Exception:
            pass

    def _load_from_state(self) -> None:
        raw = state_manager.get_obs_data()
        self.buffer = OBSData(
            **{
                f.name: getattr(raw, f.name)
                for f in OBSData.__dataclass_fields__.values()
            }
        )
        self.dirty = False

    def _set(self, field: str, value) -> None:
        if self.buffer is None:
            return
        if getattr(self.buffer, field) != value:
            setattr(self.buffer, field, value)
            self.dirty = True

    async def _test_connection(self) -> None:
        if getattr(self, "_test_in_progress", False):
            return  # the progress notice is already showing
        if not self.buffer:
            return
        self._test_in_progress = True
        address = f"{self.buffer.host}:{self.buffer.port}"
        work = None
        try:
            state_manager.set_obs_data(
                {
                    f.name: getattr(self.buffer, f.name)
                    for f in OBSData.__dataclass_fields__.values()
                }
            )
            if not state_manager.save_changes():
                return  # save_changes already said why; the test needs the saved settings
            work = notify_progress("Testing the OBS connection", source="obs", message=f"Waiting for OBS at {address}…")
            obs_service.apply_settings()
            # Must not block NiceGUI thread (Future.result would freeze UI for the timeout window).
            ok, msg = await run.io_bound(
                obs_service.refresh_snapshot_blocking,
                25.0,
            )
            if ok:
                work.done("Connected to OBS")
            else:
                from ...connection_status_tracker import obs_disconnected_status
                from ...service_alerts import OBS_OPEN_HINT

                why = obs_disconnected_status()
                if obs_service.auth_failed():
                    work.close()  # obs_service already explained the password problem
                elif why == "Not running":
                    work.fail(
                        "OBS isn't open",
                        "Mycelian connects to OBS on its own once it's running.",
                        hint="Open OBS, then test again.",
                        details=str(msg or "No OBS process found on this computer."),
                    )
                elif why == "Can't connect":
                    work.fail(
                        "OBS is open, but its WebSocket server isn't answering",
                        hint=OBS_OPEN_HINT,
                        details=f"{address}: {msg or 'No answer within 25 seconds.'}",
                    )
                else:
                    work.fail(
                        f"Couldn't reach OBS at {address}",
                        hint="Check that OBS is open on that computer with its WebSocket server on "
                        "(Tools → WebSocket Server Settings), and that the host, port and password match.",
                        details=str(msg or "No answer within 25 seconds."),
                    )
            self._refresh_status()
        except Exception as e:
            logger.error("OBS test failed: %s", e, exc_info=True)
            fail_kwargs = {
                "hint": "Check the host, port and password, then test again.",
                "details": f"{type(e).__name__}: {e}",
            }
            if work is not None:
                work.fail("Couldn't test the OBS connection", **fail_kwargs)
            else:
                notify("", type="error", source="obs", title="Couldn't test the OBS connection", **fail_kwargs)
        finally:
            self._test_in_progress = False

    def build(self, parent_container) -> None:
        self._load_from_state()
        with settings_surface(parent_container):

            with settings_status_band():
                with ui.row().classes("items-center gap-2"):
                    ui.label("Live status").classes("text-xs secondary-text")
                    self.ui_elements["status_label"] = ui.label("…").classes(
                        "font-semibold"
                    )
                self.ui_elements["enabled"] = ui.switch(
                    "Enable OBS integration",
                    value=bool(getattr(self.buffer, "enabled", True)),
                    on_change=lambda e: self._set("enabled", bool(e.value)),
                )

            with ui.column().classes("w-full gap-1 mb-2"):
                with ui.row().classes("items-center gap-2"):
                    ui.label("WebSocket checklist").classes("text-sm font-semibold")
                    help_button(
                        topic_id="obs_websocket",
                        tooltip="OBS WebSocket setup",
                        size="sm",
                    )
                ui.label(
                    "1. In OBS, open Tools → WebSocket Server Settings."
                ).classes("text-xs secondary-text")
                ui.label(
                    "2. Enable the server and note the port (default 4455) and password."
                ).classes("text-xs secondary-text")
                ui.label(
                    "3. Enter those values here, Save, then Test."
                ).classes("text-xs secondary-text")

            with settings_form_grid(columns=2):
                self.ui_elements["host"] = form_sensitive_input(
                    tooltip="Hostname or IP where OBS WebSocket server listens",
                    label="Host",
                    value=getattr(self.buffer, "host", ""),
                    placeholder="localhost",
                )
                self.ui_elements["host"].on_value_change(
                    lambda e: self._set(
                        "host",
                        ("" if e.value is None else str(e.value)).strip(),
                    ),
                )
                self.ui_elements["port"] = form_sensitive_number(
                    tooltip="OBS WebSocket server port (default 4455)",
                    label="Port",
                    value=int(getattr(self.buffer, "port", 4455) or 4455),
                    min=1,
                    max=65535,
                    classes="w-full",
                )
                self.ui_elements["port"].on_value_change(
                    lambda e: self._set(
                        "port",
                        int(float(e.value if e.value is not None else 4455)),
                    ),
                )
            self.ui_elements["password"] = form_sensitive_input(
                tooltip="Password configured in OBS WebSocket server settings",
                label="WebSocket password",
                value=getattr(self.buffer, "password", ""),
            )
            self.ui_elements["password"].on_value_change(
                lambda e: self._set(
                    "password",
                    "" if e.value is None else str(e.value),
                ),
            )

            settings_action_row(
                discard=self.discard,
                save=self.save,
                before_discard=[
                    ("Test", self._test_connection, "wifi_tethering", False),
                ],
            )
            self._status_timer = layout_schedule(5.0, self._refresh_status, active=True)

        self._refresh_status()

    def save(self) -> None:
        if not self.buffer:
            return
        payload = {
            f.name: getattr(self.buffer, f.name)
            for f in OBSData.__dataclass_fields__.values()
        }
        state_manager.set_obs_data(payload)
        if state_manager.save_changes():
            notify("", type="success", source="obs", title="OBS settings saved")
            self.dirty = False
            obs_service.apply_settings()
            self._refresh_status()
        # A failed save_changes() was already reported once (changes stay pending).

    def discard(self) -> None:
        self._load_from_state()
        if self.buffer is None:
            return
        for key in ("host", "port", "password"):
            el = self.ui_elements.get(key)
            if el is not None and hasattr(el, "value"):
                v = getattr(self.buffer, key)
                if key == "port":
                    el.value = str(int(v or 4455))
                else:
                    el.value = v
        sw = self.ui_elements.get("enabled")
        if sw is not None and hasattr(sw, "value"):
            sw.value = bool(self.buffer.enabled)
        self.dirty = False

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from nicegui import run, ui

from ... import dataobjects
from ...dataobjects import OBSData, state_manager
from ...notification_engine import notify, notify_progress
from ...obs_service import obs_service
from ...ui_buttons import outline_button
from ...ui_timer import layout_schedule
from .integration_ui import AccountCard, IntegrationPage, field, fields, hint, section, steps, switch_row

logger = logging.getLogger(__name__)


class ObsTab:
    name = "OBS"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.OBSData] = None
        self.ui_elements: Dict[str, Any] = {}
        self._status_timer: Optional[Any] = None
        self._test_in_progress: bool = False
        self._page: Optional[IntegrationPage] = None
        self._card: Optional[AccountCard] = None

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

    def _status(self) -> tuple:
        """(pill kind, short status, longer line, meta rows)."""
        if self.buffer is not None and not bool(getattr(self.buffer, "enabled", True)):
            return "off", "Turned off", "OBS integration is off. Turn it on below to connect.", []
        phase = obs_service.get_connection_phase()
        ok, plug, rpc = obs_service.connection_details()
        if phase == "connecting":
            return "busy", "Connecting", "Connecting to OBS…", []
        if phase == "disconnecting":
            return "busy", "Disconnecting", "Disconnecting from OBS…", []
        if ok:
            meta = []
            if plug:
                meta.append(("OBS", str(plug), "", ""))
            if rpc:
                meta.append(("WebSocket", f"v{rpc}", "", ""))
            return "ok", "Connected", "Connected · scenes and sources are available", meta
        if obs_service.auth_failed():
            return "error", "Wrong password", "OBS rejected the WebSocket password. Copy it again from OBS.", []
        from ...connection_status_tracker import obs_disconnected_status

        why = obs_disconnected_status()
        if why == "Not running":
            return "off", "OBS not running", "OBS isn't open. Mycelian connects on its own once it is.", []
        if why == "Can't connect":
            return "warn", "Can't connect", "OBS is open, but its WebSocket server isn't answering.", []
        return "warn", "Not connected", "Can't reach OBS at this address. Check that it's open there.", []

    def _refresh_status(self) -> None:
        try:
            kind, short, line, meta = self._status()
            if self._page is not None:
                self._page.set_status([(kind, short)])
            if self._card is not None:
                host = (self.buffer.host if self.buffer else "") or "localhost"
                port = (self.buffer.port if self.buffer else 4455) or 4455
                state = {"busy": "busy", "ok": "ok", "error": "error", "warn": "warn"}.get(kind, "off")
                self._card.set(f"{host}:{port}", state, line)
                self._card.set_meta(meta)
        except Exception:
            logger.debug("OBS status refresh failed", exc_info=True)

    def _load_from_state(self) -> None:
        raw = state_manager.get_obs_data()
        self.buffer = OBSData(
            **{
                f.name: getattr(raw, f.name)
                for f in OBSData.__dataclass_fields__.values()
            }
        )
        self.dirty = False

    def has_unsaved_changes(self) -> bool:
        """Fields use the dirty flag. The page's Save calls save() directly, so the
        Settings field snapshot (taken when the tab opened) goes stale after a save.
        A port that isn't valid yet never reaches the buffer, so compare it here."""
        if self.dirty:
            return True
        port = self.ui_elements.get("port")
        if port is None or self.buffer is None:
            return False
        typed = "" if port.value is None else str(port.value).strip()
        return typed != str(self.buffer.port)

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
        self._page = page = IntegrationPage(
            parent_container,
            "obs",
            "OBS",
            "Scenes, sources and recording control over OBS WebSocket",
            on_save=self.save,
            on_discard=self.discard,
            is_dirty=lambda: self.dirty,
            help_topic="obs_websocket",
            help_tooltip="OBS WebSocket setup",
        )
        with page.column():
            with section("cable", "Connection"):
                self._card = AccountCard("obs", "OBS Studio")
                with self._card.actions:
                    outline_button("Test", self._test_connection, icon="wifi_tethering").props("dense").classes(
                        "px-3"
                    ).tooltip("Save these settings and try connecting now")
                with ui.element("div").style("margin-top:8px"):
                    self.ui_elements["enabled"] = switch_row(
                        "Enable OBS integration",
                        "Connect automatically whenever OBS is running",
                        bool(getattr(self.buffer, "enabled", True)),
                        lambda v: (self._set("enabled", v), self._refresh_status()),
                    )
                with fields():
                    self.ui_elements["host"] = field(
                        "Host",
                        value=getattr(self.buffer, "host", ""),
                        placeholder="localhost",
                        tip="Hostname or IP address where the OBS WebSocket server listens",
                        on_change=lambda e: self._set("host", ("" if e.value is None else str(e.value)).strip()),
                    )
                    self.ui_elements["port"] = field(
                        "Port",
                        value=int(getattr(self.buffer, "port", 4455) or 4455),
                        placeholder="4455",
                        tip="OBS WebSocket server port (default 4455)",
                        on_change=self._on_port,
                    )
                    self.ui_elements["port"].props("hide-bottom-space")
                    self.ui_elements["port"].validation = {"Enter a port from 1 to 65535": self._port_ok}
                with fields(one=True):
                    self.ui_elements["password"] = field(
                        "WebSocket password",
                        value=getattr(self.buffer, "password", ""),
                        password=True,
                        tip="From OBS → Tools → WebSocket Server Settings → Show Connect Info",
                        on_change=lambda e: self._set("password", "" if e.value is None else str(e.value)),
                    )
        with page.column():
            with section("checklist", "WebSocket checklist"):
                steps(
                    [
                        ("In OBS, open <b>Tools → WebSocket Server Settings</b>.", False),
                        ("Tick <b>Enable WebSocket server</b> and note the port (default 4455).", False),
                        ("Click <b>Show Connect Info</b> and copy the password.", False),
                        ("Enter the host, port and password here, <b>Save</b>, then <b>Test</b>.", False),
                    ]
                )
                hint(
                    "OBS on another computer? Use that computer's IP address as the host, and allow the port "
                    "through its firewall."
                )
        self._status_timer = layout_schedule(5.0, self._refresh_status, active=True)
        self._refresh_status()

    @staticmethod
    def _port_ok(value: Any) -> bool:
        try:
            return 1 <= int(str(value).strip()) <= 65535
        except (TypeError, ValueError):
            return False

    def _on_port(self, e: Any) -> None:
        if self._port_ok(e.value):
            self._set("port", int(str(e.value).strip()))
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
        self._refresh_status()

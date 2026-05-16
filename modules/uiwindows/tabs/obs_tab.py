from __future__ import annotations

from typing import Any, Dict, Optional

from nicegui import run, ui

from ... import dataobjects
from ...dataobjects import OBSData, state_manager
from ...notification_engine import notify
from ...obs_service import obs_service


class ObsTab:
    name = "OBS"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.OBSData] = None
        self.ui_elements: Dict[str, Any] = {}
        self._status_timer: Optional[Any] = None

    def on_enter(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = True
        ui.timer(0.05, self._refresh_status, once=True)

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False

    def _refresh_status(self) -> None:
        try:
            ok, plug, rpc = obs_service.connection_details()
            txt = (
                "Connected"
                if ok
                else (self.buffer.connection_status if self.buffer else "Disconnected")
            )
            if ok and plug:
                extra = plug
                if rpc:
                    extra = f"{plug} · ws {rpc}"
                txt = f"Connected — {extra}"
            if "status_label" in self.ui_elements:
                self.ui_elements["status_label"].set_text(txt)
                self.ui_elements["status_label"].classes(
                    replace=(
                        "font-semibold text-theme-success"
                        if ok
                        else "font-semibold text-theme-error"
                    )
                )
        except Exception:
            pass

    def _load_from_state(self) -> None:
        raw = state_manager.get_obs_data()
        self.buffer = OBSData(
            **{f.name: getattr(raw, f.name) for f in OBSData.__dataclass_fields__.values()}
        )
        self.dirty = False

    def _set(self, field: str, value) -> None:
        if self.buffer is None:
            return
        if getattr(self.buffer, field) != value:
            setattr(self.buffer, field, value)
            self.dirty = True

    async def _test_connection(self) -> None:
        if not self.buffer:
            return
        state_manager.set_obs_data(
            {
                f.name: getattr(self.buffer, f.name)
                for f in OBSData.__dataclass_fields__.values()
            }
        )
        if not state_manager.save_changes():
            notify("Could not save OBS settings before test", type="negative")
            return
        obs_service.apply_settings()
        # Must not block NiceGUI thread (Future.result would freeze UI for the timeout window).
        ok, msg = await run.io_bound(
            obs_service.refresh_snapshot_blocking,
            25.0,
        )
        if ok:
            notify("OBS responded successfully", type="positive")
        else:
            notify(f"OBS test failed: {msg}", type="negative")
        self._refresh_status()

    def build(self, parent_container) -> None:
        self._load_from_state()
        with parent_container:
            with ui.card().classes("content-section w-full"):
                ui.label("OBS Studio (WebSocket)").classes("text-xl font-bold mb-4")

                with ui.column().classes("w-full gap-4"):
                    with ui.row().classes("w-full gap-4 items-start"):
                        with ui.column().classes("flex-1 gap-2"):
                            ui.label("Live status").classes("text-sm font-medium")
                            self.ui_elements["status_label"] = ui.label(
                                "…"
                            ).classes("font-semibold")

                        with ui.column().classes("flex-1 gap-2"):
                            self.ui_elements["enabled"] = ui.switch(
                                "Enable OBS integration",
                                value=bool(getattr(self.buffer, "enabled", True)),
                                on_change=lambda e: self._set("enabled", bool(e.value)),
                            )
                            self.ui_elements["host"] = (
                                ui.input(
                                    label="Host",
                                    value=getattr(self.buffer, "host", ""),
                                    placeholder="localhost",
                                )
                                .classes("w-full")
                                .on_value_change(
                                    lambda e: self._set(
                                        "host",
                                        ("" if e.value is None else str(e.value)).strip(),
                                    ),
                                )
                            )
                            self.ui_elements["port"] = (
                                ui.number(
                                    label="Port",
                                    value=float(getattr(self.buffer, "port", 4455)),
                                    min=1,
                                    max=65535,
                                    precision=0,
                                )
                                .classes("w-full")
                                .on_value_change(
                                    lambda e: self._set(
                                        "port",
                                        int(
                                            float(e.value if e.value is not None else 4455),
                                        ),
                                    ),
                                )
                            )
                            self.ui_elements["password"] = (
                                ui.input(
                                    label="WebSocket password",
                                    value=getattr(self.buffer, "password", ""),
                                    password=True,
                                    password_toggle_button=True,
                                )
                                .classes("w-full")
                                .on_value_change(
                                    lambda e: self._set(
                                        "password",
                                        "" if e.value is None else str(e.value),
                                    ),
                                )
                            )

                        with ui.column().classes("gap-2"):
                            ui.button(
                                "Test connection",
                                on_click=self._test_connection,
                                icon="wifi_tethering",
                            ).classes("w-40")

                    with ui.row().classes("justify-end gap-2 mt-3"):
                        ui.button("Discard", on_click=self.discard).props("outline")
                        ui.button("Save", on_click=self.save).props("color=primary")

                self._status_timer = ui.timer(5.0, self._refresh_status, active=True)

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
            notify("OBS settings saved", type="positive")
            self.dirty = False
            obs_service.apply_settings()
            self._refresh_status()
        else:
            notify("Error saving OBS settings", type="negative")

    def discard(self) -> None:
        self._load_from_state()
        if self.buffer is None:
            return
        for key in ("host", "port", "password"):
            el = self.ui_elements.get(key)
            if el is not None and hasattr(el, "value"):
                v = getattr(self.buffer, key)
                el.value = v
        sw = self.ui_elements.get("enabled")
        if sw is not None and hasattr(sw, "value"):
            sw.value = bool(self.buffer.enabled)
        self.dirty = False

"""Settings tab: enable/disable game memory hooks (FF7, etc.)."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from nicegui import ui
from ...notification_engine import notify
from ...ui_buttons import outline_button, primary_button

from ...database_manager import database_manager
from ...game_hooks.base import runtime_os_key
from ...game_hooks.registry import enabled_db_path, is_hook_enabled, list_hooks_for_ui
from ..os_brand_icons import OS_BRAND_ROW

logger = logging.getLogger(__name__)

_STATUS_LABEL_LAYOUT = "text-right shrink-0 max-w-[50%] truncate"


class GameHooksTab:
    name = "Game Hooks"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.ui_elements: Dict[str, Any] = {}
        self._buffer_enabled: Dict[str, bool] = {}
        self._loaded_enabled: Dict[str, bool] = {}
        self._status_timer: Optional[Any] = None
        self._hook_supported: Dict[str, bool] = {}

    def _load_from_db(self) -> None:
        for meta in list_hooks_for_ui():
            hid = meta.hook_id
            raw = database_manager.get_data(enabled_db_path(hid))
            if isinstance(raw, bool):
                loaded = raw
            elif isinstance(raw, dict) and "enabled" in raw:
                loaded = bool(raw["enabled"])
            else:
                loaded = False
            self._loaded_enabled[hid] = loaded
            self._buffer_enabled[hid] = loaded
            self._hook_supported[hid] = runtime_os_key() in meta.supported_platforms
        self.dirty = False

    def _render_os_strip(self, supported: frozenset) -> None:
        with ui.row().classes("items-center gap-2 shrink-0"):
            for svg_color, svg_grey, os_key, os_label in OS_BRAND_ROW:
                supported_here = os_key in supported
                tip = (
                    f"{os_label} — supported"
                    if supported_here
                    else f"{os_label} — not supported"
                )
                svg = svg_color if supported_here else svg_grey
                cls = (
                    "inline-flex items-center leading-none opacity-100 drop-shadow-sm"
                    if supported_here
                    else "inline-flex items-center leading-none opacity-55"
                )
                with ui.element("div").classes(cls).tooltip(tip):
                    ui.html(svg, tag="span")

    @staticmethod
    def _truncate(msg: str, max_len: int = 72) -> str:
        msg = msg.strip()
        if len(msg) <= max_len:
            return msg
        return msg[: max_len - 1] + "…"

    @staticmethod
    def _status_from_snapshot(snap: Dict[str, Any]) -> Tuple[str, str]:
        """Return (label text, tailwind/theme classes for the status label)."""
        disabled = bool(snap.get("disabled"))
        attached = bool(snap.get("attached"))
        err_raw = snap.get("error")
        err = err_raw if isinstance(err_raw, str) else (str(err_raw) if err_raw else "")

        if disabled:
            return "Disabled", "text-sm font-medium secondary-text"

        err_l = err.lower()
        if "requires windows" in err_l or "only supported on windows" in err_l:
            return (
                "Unsupported on this platform",
                "text-sm font-medium text-theme-warning",
            )

        if attached and not err:
            return "Enabled — connected", "text-sm font-medium text-theme-success"

        if not err or not err.strip():
            return "Enabled — not connected", "text-sm font-medium text-theme-warning"

        if "not attached" in err_l or "not found" in err_l:
            return "Enabled — not connected", "text-sm font-medium text-theme-warning"

        detail = GameHooksTab._truncate(err)
        return f"Error: {detail}", "text-sm font-medium text-theme-error"

    def _refresh_runtime_status(self, hook_id: str) -> None:
        lbl = self.ui_elements.get(f"{hook_id}_runtime_status_label")
        if not lbl:
            return
        try:
            from ...game_hooks_service import game_hooks_service

            info = game_hooks_service.get_hook_ui_snapshot(hook_id)
        except Exception as e:
            logger.debug("%s runtime status: %s", hook_id, e)
            lbl.set_text("Status unavailable")
            lbl.classes(
                replace=f"text-sm font-medium text-theme-error {_STATUS_LABEL_LAYOUT}"
            )
            return

        if not info.get("service_running"):
            lbl.set_text("Hooks service not running")
            lbl.classes(
                replace=f"text-sm font-medium text-theme-warning {_STATUS_LABEL_LAYOUT}"
            )
            return

        snap = info.get(hook_id)
        if not isinstance(snap, dict):
            lbl.set_text("Starting…")
            lbl.classes(
                replace=f"text-sm font-medium secondary-text {_STATUS_LABEL_LAYOUT}"
            )
            return

        text, classes = self._status_from_snapshot(snap)
        lbl.set_text(text)
        lbl.classes(replace=f"{classes} {_STATUS_LABEL_LAYOUT}")

    def _refresh_all_runtime_status(self) -> None:
        for meta in list_hooks_for_ui():
            self._refresh_runtime_status(meta.hook_id)

    def build(self, parent_container) -> None:
        self._load_from_db()
        hook_metas = list_hooks_for_ui()
        with parent_container:
            with ui.card().classes("content-section w-full"):
                with ui.row().classes("w-full items-center gap-2 mb-2"):
                    ui.label("Game Hooks").classes("text-xl font-bold")
                ui.label(
                    "When enabled, Mycelian reads live data from supported PC games "
                    "and broadcasts it to browser templates. "
                ).classes("settings-description mb-4")

                with ui.grid(columns=2).classes("w-full gap-4"):
                    for meta in hook_metas:
                        hid = meta.hook_id
                        with ui.column().classes("content-card w-full min-w-0"):
                            with ui.row().classes(
                                "w-full items-center justify-between gap-3 min-w-0"
                            ):
                                with ui.row().classes(
                                    "items-center gap-3 shrink-0 min-w-0"
                                ):
                                    ui.label(meta.title).classes(
                                        "font-semibold text-sm shrink-0"
                                    )
                                    self._render_os_strip(meta.supported_platforms)
                                    hook_supported = self._hook_supported.get(
                                        hid, True
                                    )
                                    sw = (
                                        ui.switch(
                                            value=(
                                                self._buffer_enabled.get(hid, False)
                                                if hook_supported
                                                else False
                                            ),
                                        )
                                        .classes("shrink-0")
                                        .on(
                                            "update:model-value",
                                            lambda e, h=hid: self._on_toggle(
                                                h, bool(e.args)
                                            ),
                                        )
                                    )
                                    if not hook_supported:
                                        sw.disable()
                                    self.ui_elements[f"{hid}_toggle"] = sw
                                self.ui_elements[f"{hid}_runtime_status_label"] = (
                                    ui.label("").classes(
                                        f"text-sm font-medium secondary-text "
                                        f"{_STATUS_LABEL_LAYOUT}"
                                    )
                                )

                self._status_timer = ui.timer(
                    0.5, self._refresh_all_runtime_status, active=True
                )

            with ui.row().classes("justify-end gap-2 mt-4 w-full"):
                outline_button("Discard", self.discard)
                primary_button("Save", self.save)

    def _on_toggle(self, hook_id: str, value: bool) -> None:
        if not self._hook_supported.get(hook_id, True):
            return
        if value != self._buffer_enabled.get(hook_id, False):
            self._buffer_enabled[hook_id] = value
            self.dirty = True

    def on_enter(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = True
        ui.timer(0.05, self._refresh_all_runtime_status, once=True)

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False

    def save(self) -> None:
        try:
            ok_all = True
            for meta in list_hooks_for_ui():
                hid = meta.hook_id
                to_save = (
                    self._buffer_enabled.get(hid, False)
                    if self._hook_supported.get(hid, True)
                    else False
                )
                ok = database_manager.set_data(
                    enabled_db_path(hid), {"enabled": to_save}
                )
                if ok:
                    self._loaded_enabled[hid] = bool(to_save)
                    self._buffer_enabled[hid] = bool(to_save)
                    logger.info("GameHooks: %s_enabled=%s", hid, to_save)
                else:
                    ok_all = False
            if ok_all:
                self.dirty = False
                notify("Game Hooks saved", type="positive")
            else:
                notify("Failed to save Game Hooks", type="negative")
        except Exception as e:
            logger.error("GameHooks save failed: %s", e, exc_info=True)
            notify(f"Error saving Game Hooks: {e}", type="negative")
        self._refresh_all_runtime_status()

    def discard(self) -> None:
        self._load_from_db()
        for meta in list_hooks_for_ui():
            hid = meta.hook_id
            t = self.ui_elements.get(f"{hid}_toggle")
            if t is not None:
                t.value = (
                    self._buffer_enabled.get(hid, False)
                    if self._hook_supported.get(hid, True)
                    else False
                )
        self._refresh_all_runtime_status()


def is_ff7_hook_enabled() -> bool:
    """Read persisted FF7 hook flag (default False)."""
    return is_hook_enabled("ff7")

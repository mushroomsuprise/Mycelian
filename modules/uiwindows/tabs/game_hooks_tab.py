"""Settings tab: enable/disable game memory hooks (FF7, etc.)."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Optional, Tuple

from nicegui import ui

from ...database_manager import database_manager
from ...help_system.contextual_help import help_button
from ..os_brand_icons import OS_BRAND_ROW

logger = logging.getLogger(__name__)

_GAME_HOOKS_ROOT = "GameHooks"
_FF7_ENABLED_KEY = f"{_GAME_HOOKS_ROOT}/ff7_enabled"


@dataclass(frozen=True)
class _HookUiDef:
    hook_id: str
    title: str
    supported_os: FrozenSet[str]


_HOOK_ROWS: Tuple[_HookUiDef, ...] = (
    _HookUiDef(
        hook_id="ff7",
        title="Final Fantasy VII (PC)",
        supported_os=frozenset({"windows"}),
    ),
)


def _runtime_os_key() -> str:
    p = sys.platform
    if p == "win32":
        return "windows"
    if p == "darwin":
        return "darwin"
    if p.startswith("linux"):
        return "linux"
    return "other"


class GameHooksTab:
    name = "Game Hooks"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.ui_elements: Dict[str, Any] = {}
        self._buffer_ff7_enabled: bool = False
        self._loaded_ff7_enabled: bool = False
        self._status_timer: Optional[Any] = None
        self._ff7_hook_supported = next(
            (
                _runtime_os_key() in h.supported_os
                for h in _HOOK_ROWS
                if h.hook_id == "ff7"
            ),
            True,
        )

    def _load_from_db(self) -> None:
        raw = database_manager.get_data(_FF7_ENABLED_KEY)
        if isinstance(raw, bool):
            self._loaded_ff7_enabled = raw
        elif isinstance(raw, dict) and "enabled" in raw:
            self._loaded_ff7_enabled = bool(raw["enabled"])
        else:
            self._loaded_ff7_enabled = False
        self._buffer_ff7_enabled = self._loaded_ff7_enabled
        self.dirty = False

    def _render_os_strip(self, supported: FrozenSet[str]) -> None:
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
    def _ff7_status_from_snapshot(
        snap: Dict[str, Any],
    ) -> Tuple[str, str]:
        """Return (label text, tailwind/theme classes for the status label)."""
        disabled = bool(snap.get("disabled"))
        attached = bool(snap.get("attached"))
        err_raw = snap.get("error")
        err = err_raw if isinstance(err_raw, str) else (str(err_raw) if err_raw else "")

        if disabled:
            return "Disabled", "text-sm font-medium secondary-text"

        err_l = err.lower()
        if "requires windows" in err_l:
            return (
                "Unsupported on this platform",
                "text-sm font-medium text-theme-warning",
            )

        if attached and not err:
            return "Enabled — connected", "text-sm font-medium text-theme-success"

        if not err or not err.strip():
            return "Enabled — not connected", "text-sm font-medium text-theme-warning"

        if "not attached" in err_l:
            return "Enabled — not connected", "text-sm font-medium text-theme-warning"

        detail = GameHooksTab._truncate(err)
        return f"Error: {detail}", "text-sm font-medium text-theme-error"

    def _refresh_ff7_runtime_status(self) -> None:
        lbl = self.ui_elements.get("ff7_runtime_status_label")
        if not lbl:
            return
        try:
            from ...game_hooks_service import game_hooks_service

            info = game_hooks_service.get_ff7_ui_snapshot()
        except Exception as e:
            logger.debug("FF7 runtime status: %s", e)
            lbl.set_text("Status unavailable")
            lbl.classes(replace="text-sm font-medium text-theme-error")
            return

        if not info.get("service_running"):
            lbl.set_text("Hooks service not running")
            lbl.classes(replace="text-sm font-medium text-theme-warning")
            return

        ff7 = info.get("ff7")
        if not isinstance(ff7, dict):
            lbl.set_text("Starting…")
            lbl.classes(replace="text-sm font-medium secondary-text")
            return

        text, classes = self._ff7_status_from_snapshot(ff7)
        lbl.set_text(text)
        lbl.classes(replace=classes)

    def build(self, parent_container) -> None:
        self._load_from_db()
        with parent_container:
            with ui.card().classes("content-section w-full"):
                with ui.row().classes("w-full items-center gap-2 mb-2"):
                    ui.label("Game Hooks").classes("text-xl font-bold")
                    help_button(topic_id="game_hooks", tooltip="Game Hooks help")
                ui.label(
                    "When enabled, Mycelian reads live data from supported PC games "
                    "and broadcasts it to browser templates. "
                ).classes("settings-description mb-4")

                with ui.grid(columns=2).classes("w-full gap-4"):
                    for hdef in _HOOK_ROWS:
                        with ui.column().classes("content-card w-full min-w-0"):
                            with ui.row().classes(
                                "w-full items-center justify-between gap-4 flex-wrap"
                            ):
                                self._render_os_strip(hdef.supported_os)
                                if hdef.hook_id == "ff7":
                                    hook_supported = _runtime_os_key() in hdef.supported_os
                                    self._ff7_hook_supported = hook_supported
                                    ff7_switch = (
                                        ui.switch(
                                            hdef.title,
                                            value=(
                                                self._buffer_ff7_enabled
                                                if hook_supported
                                                else False
                                            ),
                                        )
                                        .props("left-label")
                                        .classes("min-w-0 grow")
                                        .on(
                                            "update:model-value",
                                            lambda e: self._on_ff7_toggle(
                                                bool(e.args)
                                            ),
                                        )
                                    )
                                    if not hook_supported:
                                        ff7_switch.disable()
                                    self.ui_elements["ff7_toggle"] = ff7_switch
                                    self.ui_elements["ff7_runtime_status_label"] = (
                                        ui.label("").classes(
                                            "text-sm font-medium secondary-text"
                                        )
                                    )

                # active=True so status updates run even if on_enter ran before lazy build()
                self._status_timer = ui.timer(
                    0.5, self._refresh_ff7_runtime_status, active=True
                )

            with ui.row().classes("justify-end gap-2 mt-4 w-full"):
                ui.button("Discard", on_click=self.discard).props("outline")
                ui.button("Save", on_click=self.save).props("color=primary")

    def _on_ff7_toggle(self, value: bool) -> None:
        if not self._ff7_hook_supported:
            return
        if value != self._buffer_ff7_enabled:
            self._buffer_ff7_enabled = value
            self.dirty = True

    def on_enter(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = True
        ui.timer(0.05, self._refresh_ff7_runtime_status, once=True)

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False

    def save(self) -> None:
        try:
            to_save = (
                self._buffer_ff7_enabled if self._ff7_hook_supported else False
            )
            ok = database_manager.set_data(
                _FF7_ENABLED_KEY, {"enabled": to_save}
            )
            if ok:
                self._loaded_ff7_enabled = bool(to_save)
                self._buffer_ff7_enabled = bool(to_save)
                self.dirty = False
                ui.notify("Game Hooks saved", type="positive")
                logger.info("GameHooks: ff7_enabled=%s", to_save)
            else:
                ui.notify("Failed to save Game Hooks", type="negative")
        except Exception as e:
            logger.error("GameHooks save failed: %s", e, exc_info=True)
            ui.notify(f"Error saving Game Hooks: {e}", type="negative")
        self._refresh_ff7_runtime_status()

    def discard(self) -> None:
        self._load_from_db()
        t = self.ui_elements.get("ff7_toggle")
        if t is not None:
            t.value = (
                self._buffer_ff7_enabled if self._ff7_hook_supported else False
            )
        self._refresh_ff7_runtime_status()


def is_ff7_hook_enabled() -> bool:
    """Read persisted FF7 hook flag (default False)."""
    try:
        raw = database_manager.get_data(_FF7_ENABLED_KEY)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, dict) and "enabled" in raw:
            return bool(raw["enabled"])
    except Exception as e:
        logger.debug("is_ff7_hook_enabled: %s", e)
    return False

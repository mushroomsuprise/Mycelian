"""Settings tab: enable/disable game memory hooks (FF7, etc.)."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from nicegui import ui

from ...database_manager import database_manager

logger = logging.getLogger(__name__)

_GAME_HOOKS_ROOT = "GameHooks"
_FF7_ENABLED_KEY = f"{_GAME_HOOKS_ROOT}/ff7_enabled"


class GameHooksTab:
    name = "Game Hooks"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.ui_elements: Dict[str, Any] = {}
        self._buffer_ff7_enabled: bool = False
        self._loaded_ff7_enabled: bool = False

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

    def build(self, parent_container) -> None:
        self._load_from_db()
        with parent_container:
            with ui.card().classes("content-section w-full"):
                ui.label("Game Hooks").classes("text-xl font-bold mb-2")
                ui.label(
                    "When enabled, Mycelian reads live data from supported PC games "
                    "and broadcasts it to browser templates (e.g. /ff7). "
                ).classes("settings-description mb-4")

                self.ui_elements["ff7_toggle"] = (
                    ui.switch(
                        "Final Fantasy VII (PC)",
                        value=self._buffer_ff7_enabled,
                    )
                    .props("left-label")
                    .classes("w-full")
                    .on(
                        "update:model-value",
                        lambda e: self._on_ff7_toggle(bool(e.args)),
                    )
                )

                self.ui_elements["status_label"] = ui.label("").classes(
                    "secondary-text text-sm mt-2"
                )
                self._refresh_status()

            with ui.row().classes("justify-end gap-2 mt-4 w-full"):
                ui.button("Discard", on_click=self.discard).props("outline")
                ui.button("Save", on_click=self.save).props("color=primary")

    def _on_ff7_toggle(self, value: bool) -> None:
        if value != self._buffer_ff7_enabled:
            self._buffer_ff7_enabled = value
            self.dirty = True

    def _refresh_status(self) -> None:
        lbl = self.ui_elements.get("status_label")
        if not lbl:
            return
        persisted = self._loaded_ff7_enabled
        lbl.set_text(
            "Saved: FF7 hook is "
            + ("ON" if persisted else "OFF")
            + " — save to apply when the web server is running."
        )

    def on_enter(self) -> None:
        self._refresh_status()

    def on_exit(self) -> None:
        pass

    def save(self) -> None:
        try:
            ok = database_manager.set_data(
                _FF7_ENABLED_KEY, {"enabled": bool(self._buffer_ff7_enabled)}
            )
            if ok:
                self._loaded_ff7_enabled = bool(self._buffer_ff7_enabled)
                self.dirty = False
                ui.notify("Game Hooks saved", type="positive")
                logger.info("GameHooks: ff7_enabled=%s", self._loaded_ff7_enabled)
            else:
                ui.notify("Failed to save Game Hooks", type="negative")
        except Exception as e:
            logger.error("GameHooks save failed: %s", e, exc_info=True)
            ui.notify(f"Error saving Game Hooks: {e}", type="negative")

    def discard(self) -> None:
        self._load_from_db()
        t = self.ui_elements.get("ff7_toggle")
        if t is not None:
            t.value = self._buffer_ff7_enabled
        self._refresh_status()


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

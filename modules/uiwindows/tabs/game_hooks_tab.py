"""Settings tab: enable/disable game memory hooks (FF7, etc.)."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from nicegui import ui
from ...notification_engine import notify
from ...ui_buttons import outline_button, primary_button

from ...database_manager import database_manager
from ...game_hooks.base import runtime_os_key
from ...game_hooks.registry import (
    enabled_db_path,
    is_hook_enabled,
    list_hooks_for_ui,
    set_hook_enabled_cached,
)
from ..os_brand_icons import OS_BRAND_ROW

logger = logging.getLogger(__name__)

_CARD_BASE = (
    "content-card w-full min-w-0 p-3 border-2 border-solid transition-colors"
)
_STATE_NEUTRAL = "border-transparent"
_STATE_SUCCESS = "border-theme-success"
_STATE_WARNING = "border-theme-warning"
_STATE_ERROR = "border-theme-error"
_STATE_UNSUPPORTED = "opacity-60 border-theme-error"

_CARD_STATE_CLASSES = {
    "neutral": _STATE_NEUTRAL,
    "success": _STATE_SUCCESS,
    "warning": _STATE_WARNING,
    "error": _STATE_ERROR,
    "unsupported": _STATE_UNSUPPORTED,
}


class GameHooksTab:
    name = "Game Hooks"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.ui_elements: Dict[str, Any] = {}
        self._buffer_enabled: Dict[str, bool] = {}
        self._loaded_enabled: Dict[str, bool] = {}
        self._status_timer: Optional[Any] = None
        self._hook_supported: Dict[str, bool] = {}
        self._last_error_notified: Dict[str, str] = {}

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
    def _truncate(msg: str, max_len: int = 120) -> str:
        msg = msg.strip()
        if len(msg) <= max_len:
            return msg
        return msg[: max_len - 1] + "…"

    def _is_unsupported(self, hook_id: str, snap: Optional[Dict[str, Any]] = None) -> bool:
        if not self._hook_supported.get(hook_id, True):
            return True
        if not isinstance(snap, dict):
            return False
        debug = snap.get("debug")
        if isinstance(debug, dict) and debug.get("stage") == "unsupported_os":
            return True
        err_raw = snap.get("error")
        err = err_raw if isinstance(err_raw, str) else (str(err_raw) if err_raw else "")
        err_l = err.lower()
        return "requires windows" in err_l or "only supported on windows" in err_l

    @staticmethod
    def _state_from_snapshot(snap: Dict[str, Any]) -> Tuple[str, Optional[str]]:
        """Return (card_state, optional_error_message_for_notify)."""
        disabled = bool(snap.get("disabled"))
        attached = bool(snap.get("attached"))
        err_raw = snap.get("error")
        err = err_raw if isinstance(err_raw, str) else (str(err_raw) if err_raw else "")

        if disabled:
            return "neutral", None

        err_l = err.lower()
        if "requires windows" in err_l or "only supported on windows" in err_l:
            return "unsupported", None

        debug = snap.get("debug")
        if isinstance(debug, dict) and debug.get("stage") == "unsupported_os":
            return "unsupported", None

        if attached and not err:
            return "success", None

        if not err or not err.strip():
            return "warning", None

        if "not attached" in err_l or "not found" in err_l:
            return "warning", None

        return "error", GameHooksTab._truncate(err)

    def _apply_card_state(self, hook_id: str, state: str) -> None:
        card = self.ui_elements.get(f"{hook_id}_card")
        if card is None:
            return
        state_classes = _CARD_STATE_CLASSES.get(state, _STATE_NEUTRAL)
        card.classes(replace=f"{_CARD_BASE} {state_classes}")

    def _maybe_notify_error(self, hook_id: str, err_msg: Optional[str], title: str) -> None:
        prev = self._last_error_notified.get(hook_id, "")
        if err_msg:
            if err_msg != prev:
                self._last_error_notified[hook_id] = err_msg
                notify(f"{title}: {err_msg}", type="negative", timeout=8000)
        elif prev:
            self._last_error_notified[hook_id] = ""

    def _refresh_runtime_status(self, hook_id: str) -> None:
        meta_by_id = {m.hook_id: m for m in list_hooks_for_ui()}
        title = meta_by_id.get(hook_id).title if hook_id in meta_by_id else hook_id

        try:
            from ...game_hooks_service import game_hooks_service

            info = game_hooks_service.get_hook_ui_snapshot(hook_id)
        except Exception as e:
            logger.debug("%s runtime status: %s", hook_id, e)
            self._apply_card_state(hook_id, "error")
            self._maybe_notify_error(hook_id, "Status unavailable", title)
            return

        snap = info.get(hook_id)
        if self._is_unsupported(hook_id, snap if isinstance(snap, dict) else None):
            self._apply_card_state(hook_id, "unsupported")
            self._last_error_notified.pop(hook_id, None)
            return

        if not info.get("service_running"):
            self._apply_card_state(hook_id, "warning")
            self._last_error_notified.pop(hook_id, None)
            return

        if not isinstance(snap, dict):
            self._apply_card_state(hook_id, "neutral")
            return

        state, err_msg = self._state_from_snapshot(snap)
        self._apply_card_state(hook_id, state)
        self._maybe_notify_error(hook_id, err_msg, title)

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
                    "and broadcasts it to browser templates."
                ).classes("settings-description mb-4")

                with ui.grid(columns=3).classes("w-full gap-4"):
                    for meta in hook_metas:
                        hid = meta.hook_id
                        hook_supported = self._hook_supported.get(hid, True)
                        initial_state = (
                            "unsupported" if not hook_supported else "neutral"
                        )
                        with ui.column().classes(
                            f"{_CARD_BASE} {_CARD_STATE_CLASSES[initial_state]}"
                        ) as card_col:
                            self.ui_elements[f"{hid}_card"] = card_col
                            with ui.row().classes(
                                "w-full items-center justify-between gap-2 min-w-0"
                            ):
                                with ui.row().classes(
                                    "items-center gap-2 min-w-0 flex-1"
                                ):
                                    ui.label(meta.title).classes(
                                        "font-semibold text-sm shrink-0"
                                    )
                                    self._render_os_strip(meta.supported_platforms)
                                sw = (
                                    ui.switch(
                                        value=(
                                            self._buffer_enabled.get(hid, False)
                                            if hook_supported
                                            else False
                                        ),
                                    )
                                    .classes("shrink-0 ml-auto")
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

                self._status_timer = ui.timer(
                    0.5, self._refresh_all_runtime_status, active=True
                )

            with ui.row().classes("justify-end gap-2 mt-4 w-full"):
                outline_button("Discard", self.discard)
                primary_button("Save", self.save)

        ui.timer(0.05, self._refresh_all_runtime_status, once=True)

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
                    set_hook_enabled_cached(hid, to_save)
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

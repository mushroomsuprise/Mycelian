# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Settings tab: enable/disable game hooks (FF7, Factorio, etc.)."""

from __future__ import annotations

import base64
import io
import logging
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple

from nicegui import run, ui
import time

from ...notification_engine import nav_actions_settings, notify
from ...ui_buttons import outline_button
from ...ui_timer import layout_schedule

from ...database_manager import (
    clear_last_write_problem,
    database_manager,
    last_write_problem,
    quiet_notifications,
)
from ...game_hooks.base import runtime_os_key
from ...game_hooks.registry import (
    enabled_db_path,
    get_hook_class,
    is_hook_enabled,
    list_hooks_for_ui,
    set_hook_enabled_cached,
)
from ..os_brand_icons import OS_BRAND_ROW
from .integration_ui import IntegrationPage, esc, section

logger = logging.getLogger(__name__)

# hook_id -> monotonic time of the last error pop-up. Module level so a rebuilt tab or a
# changing error text (addresses, PIDs) doesn't re-announce the same problem.
_HOOK_ERROR_TOASTED: Dict[str, float] = {}
_HOOK_ERROR_TOAST_GAP_SEC = 600.0

_OS_LABELS = {key: label for _c, _g, key, label in OS_BRAND_ROW}

GAME_HOOKS_CSS = """
.itg-hooks { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
@media (max-width: 1000px) { .itg-hooks { grid-template-columns: 1fr; } }
.itg-hook { display: flex; flex-direction: column; border: 1px solid var(--color-border-subtle); border-radius: 10px;
  background: var(--color-bg-base); overflow: hidden; transition: border-color .15s; }
.itg-hook.is-success { border-color: color-mix(in srgb, var(--color-success) 50%, var(--color-border-subtle)); }
.itg-hook.is-warning { border-color: color-mix(in srgb, var(--color-warning) 45%, var(--color-border-subtle)); }
.itg-hook.is-error { border-color: color-mix(in srgb, var(--color-error) 50%, var(--color-border-subtle)); }
.itg-hook.is-unsupported .itg-hook-top { opacity: .6; }
.itg-hook-top { display: flex; align-items: center; gap: 12px; padding: 11px 12px; flex: 1 1 auto; }
.itg-hook-cover { width: 52px; height: 52px; border-radius: 9px; flex: 0 0 auto; overflow: hidden; display: flex; align-items: center;
  justify-content: center; font-weight: 800; font-size: .9rem; color: #fff; background: var(--color-primary);
  box-shadow: 0 0 0 1px var(--color-border-subtle); }
.itg-hook-cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
.itg-hook-text { flex: 1 1 auto; min-width: 0; }
.itg-hook-name { font-size: .92rem; font-weight: 650; color: var(--color-text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.itg-os { display: flex; align-items: center; gap: 6px; margin-top: 4px; flex-wrap: wrap; }
.itg-os-chip { display: inline-flex; align-items: center; gap: 4px; height: 20px; padding: 0 7px 0 5px; border-radius: 999px; font-size: .66rem;
  font-weight: 600; color: var(--color-text-secondary); background: var(--color-bg-surface); border: 1px solid var(--color-border-subtle); }
.itg-os-chip svg { width: 12px; height: 12px; }
.itg-os-chip.is-no { opacity: .45; text-decoration: line-through; }
.itg-hook-state { display: flex; align-items: center; gap: 8px; min-height: 40px; padding: 4px 8px 4px 12px; border-top: 1px solid var(--color-border-subtle);
  font-size: .76rem; color: var(--color-text-secondary); }
.itg-hook-line { display: flex; align-items: center; gap: 7px; flex: 1 1 auto; min-width: 0; }
.itg-hook-line span:last-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.itg-hook-line .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--st, var(--color-text-muted)); flex: 0 0 auto; }
.itg-hook-line.is-success { --st: var(--color-success); }
.itg-hook-line.is-warning { --st: var(--color-warning); }
.itg-hook-line.is-error { --st: var(--color-error); }
.itg-hook-acts { display: flex; align-items: center; gap: 8px; flex: 0 0 auto; }
.itg-hook-acts .q-btn { min-height: 26px; font-size: .72rem; padding: 0 8px; }
.itg-hook-detail { font-size: .7rem; color: var(--color-text-muted); white-space: nowrap; }
.itg-hook-detail.is-ok { color: var(--color-success); }
.itg-hook-detail.is-warning { color: var(--color-warning); }
"""

_STYLE_ID = "mycelian-game-hooks"


@lru_cache(maxsize=None)
def _cover_data_uri(hook_id: str) -> Optional[str]:
    """Game cover from assets/default_assets/images/game_hooks/<hook_id>.jpg, as a small data URI."""
    try:
        from PIL import Image

        from ...path_utils import get_assets_path

        path = get_assets_path(f"default_assets/images/game_hooks/{hook_id}.jpg")
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((112, 112))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=88)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.debug("Game Hooks: no cover for %s: %s", hook_id, e)
        return None


def _initials(title: str) -> str:
    words = [w for w in str(title or "").replace("(", " ").split() if w[:1].isalnum()]
    return "".join(w[0] for w in words[:3]).upper() or "?"


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
        self._refresh_inflight: bool = False
        self._card_state: Dict[str, Tuple[str, str]] = {}
        self._page: Optional[IntegrationPage] = None

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

    @staticmethod
    def _os_chips_html(supported: frozenset) -> str:
        chips = []
        for svg_color, svg_grey, os_key, os_label in OS_BRAND_ROW:
            ok = os_key in supported
            # The lit Apple mark is near-white; draw it in the chip's text colour so it shows in light themes.
            svg_color = svg_color.replace('fill="#f5f5f7"', 'fill="currentColor"')
            tip = f"{os_label}: supported" if ok else f"{os_label}: not supported"
            chips.append(
                f'<span class="itg-os-chip{"" if ok else " is-no"}" title="{esc(tip)}">'
                f'{svg_color if ok else svg_grey}{esc(os_label)}</span>'
            )
        return '<div class="itg-os">' + "".join(chips) + "</div>"

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

    def _unsupported_text(self, hook_id: str) -> str:
        here = _OS_LABELS.get(runtime_os_key(), "this computer")
        meta = {m.hook_id: m for m in list_hooks_for_ui()}.get(hook_id)
        where = ", ".join(_OS_LABELS.get(k, k) for k in sorted(meta.supported_platforms)) if meta else ""
        return f"Not available on {here}" + (f" · {where} only" if where else "")

    def _waiting_text(self, hook_id: str) -> str:
        cls = get_hook_class(hook_id)
        names = tuple(getattr(cls, "process_names", ()) or ())
        return f"Waiting for the game · looking for {names[0]}" if names else "Waiting for the game to start"

    def _apply_card_state(self, hook_id: str, state: str, text: str = "") -> None:
        """Card border + status line. ``state``: neutral | success | warning | error | unsupported."""
        if state == "neutral" and not text:
            text = "Off" if not self._loaded_enabled.get(hook_id) else "Starting…"
        if self._card_state.get(hook_id) == (state, text):
            return
        self._card_state[hook_id] = (state, text)
        card = self.ui_elements.get(f"{hook_id}_card")
        if card is not None:
            card.classes(replace=f"itg-hook is-{state}")
        line = self.ui_elements.get(f"{hook_id}_line")
        if line is not None:
            line.set_content(f'<span class="dot"></span><span>{esc(text)}</span>')
            line.classes(replace=f"itg-hook-line is-{state}")
            tip = self.ui_elements.get(f"{hook_id}_line_tip")
            if tip is not None:
                tip.set_text(text)

    def _refresh_header(self) -> None:
        if self._page is None:
            return
        attached = sum(1 for st, _t in self._card_state.values() if st == "success")
        problems = sum(1 for st, _t in self._card_state.values() if st == "error")
        on = sum(1 for hid, v in self._loaded_enabled.items() if v and self._hook_supported.get(hid, True))
        pills = []
        if attached:
            pills.append(("ok", f"{attached} game{'s' if attached != 1 else ''} running"))
        if problems:
            pills.append(("error", f"{problems} with a problem"))
        if not pills:
            pills.append(("warn", "Waiting for a game") if on else ("off", "No hooks on"))
        self._page.set_status(pills)

    def _maybe_notify_error(self, hook_id: str, err_msg: Optional[str], title: str) -> None:
        # Driven by the status timer: the card shows the error; the pop-up is rate-limited.
        prev = self._last_error_notified.get(hook_id, "")
        if err_msg:
            if err_msg != prev:
                self._last_error_notified[hook_id] = err_msg
                now = time.monotonic()
                last = _HOOK_ERROR_TOASTED.get(hook_id)
                if last is None or now - last >= _HOOK_ERROR_TOAST_GAP_SEC:
                    _HOOK_ERROR_TOASTED[hook_id] = now
                    notify(
                        "Its card shows the latest status.",
                        type="warning",
                        source="game_hooks",
                        title=f"The {title} hook has a problem",
                        hint="Make sure the game is running and Mycelian is allowed to read it, then check the card.",
                        details=err_msg,
                        dedupe_key=f"gamehook:{hook_id}:err",
                        dedupe_cooldown_sec=_HOOK_ERROR_TOAST_GAP_SEC,
                        actions=nav_actions_settings("Game Hooks"),
                    )
        elif prev:
            self._last_error_notified[hook_id] = ""

    def _refresh_runtime_status(self, hook_id: str) -> None:
        meta_by_id = {m.hook_id: m for m in list_hooks_for_ui()}
        title = meta_by_id.get(hook_id).title if hook_id in meta_by_id else hook_id

        if not self._hook_supported.get(hook_id, True):
            self._apply_card_state(hook_id, "unsupported", self._unsupported_text(hook_id))
            self._last_error_notified.pop(hook_id, None)
            return

        try:
            from ...game_hooks_service import game_hooks_service

            info = game_hooks_service.get_hook_ui_snapshot(hook_id)
        except Exception as e:
            logger.debug("%s runtime status: %s", hook_id, e)
            self._apply_card_state(hook_id, "error", "Status unavailable")
            self._maybe_notify_error(hook_id, "Status unavailable", title)
            return

        snap = info.get(hook_id)
        if self._is_unsupported(hook_id, snap if isinstance(snap, dict) else None):
            self._apply_card_state(hook_id, "unsupported", self._unsupported_text(hook_id))
            self._last_error_notified.pop(hook_id, None)
            return

        if not self._loaded_enabled.get(hook_id):
            self._apply_card_state(hook_id, "neutral", "Off")
            self._last_error_notified.pop(hook_id, None)
            return

        if not info.get("service_running"):
            self._apply_card_state(hook_id, "warning", "Waiting for the game hooks service to start")
            self._last_error_notified.pop(hook_id, None)
            return

        if not isinstance(snap, dict):
            self._apply_card_state(hook_id, "neutral", "Starting…")
            return

        state, err_msg = self._state_from_snapshot(snap)
        self._apply_card_state(hook_id, state, self._state_text(hook_id, state, err_msg))
        self._maybe_notify_error(hook_id, err_msg, title)

    def _state_text(self, hook_id: str, state: str, err_msg: Optional[str]) -> str:
        if state == "success":
            return "Attached · reading live data"
        if state == "warning":
            return self._waiting_text(hook_id)
        if state == "error":
            return f"Problem: {err_msg}"
        if state == "unsupported":
            return self._unsupported_text(hook_id)
        return "Off"

    def _refresh_all_runtime_status(self) -> None:
        for meta in list_hooks_for_ui():
            self._refresh_runtime_status(meta.hook_id)
            self._refresh_hook_actions(meta.hook_id)
        self._refresh_header()

    def _collect_hook_ui_actions(self, hook_ids: list) -> Dict[str, list]:
        return {hid: self._hook_ui_actions(hid) for hid in hook_ids}

    async def _refresh_all_runtime_status_async(self) -> None:
        """Timer refresh: hook action state (process scans, mods-folder reads) is
        computed on a worker thread and applied here, so the UI loop never blocks."""
        if self._refresh_inflight:
            return
        self._refresh_inflight = True
        try:
            hook_ids = [meta.hook_id for meta in list_hooks_for_ui()]
            for hid in hook_ids:
                self._refresh_runtime_status(hid)
            self._refresh_header()
            actions = await run.io_bound(self._collect_hook_ui_actions, hook_ids) or {}
            for hid in hook_ids:
                self._refresh_hook_actions(hid, actions.get(hid, []))
        finally:
            self._refresh_inflight = False

    def build(self, parent_container) -> None:
        from ...help_system.help_render import inject_page_stylesheet, promote_color_importance, scope_selectors

        inject_page_stylesheet(_STYLE_ID, promote_color_importance(scope_selectors(GAME_HOOKS_CSS, ".itg")))
        self._load_from_db()
        hook_metas = list_hooks_for_ui()
        self._page = page = IntegrationPage(
            parent_container,
            "game_hooks",
            "Game Hooks",
            "Live data from PC games for your overlays",
            icon="sports_esports",
            on_save=self.save,
            on_discard=self.discard,
            is_dirty=lambda: self.dirty,
            help_topic="game_hooks",
            help_tooltip="How game hooks work",
        )
        count = len(hook_metas)
        with page.wide(top=True):
            with section("extension", "Hooks", f"{count} game{'s' if count != 1 else ''}"):
                with ui.element("div").classes("itg-hooks"):
                    for meta in hook_metas:
                        self._build_card(meta)

        self._status_timer = layout_schedule(0.5, self._refresh_all_runtime_status_async, active=True)
        self._refresh_header()
        layout_schedule(0.05, self._refresh_all_runtime_status_async, once=True)

    def _build_card(self, meta: Any) -> None:
        hid = meta.hook_id
        hook_supported = self._hook_supported.get(hid, True)
        initial_state = "unsupported" if not hook_supported else "neutral"
        with ui.element("div").classes(f"itg-hook is-{initial_state}") as card:
            self.ui_elements[f"{hid}_card"] = card
            with ui.element("div").classes("itg-hook-top"):
                cover = _cover_data_uri(hid)
                cover_html = f'<img src="{cover}" alt="">' if cover else esc(_initials(meta.title))
                ui.html(f'<div class="itg-hook-cover">{cover_html}</div>', sanitize=False)
                ui.html(
                    f'<div class="itg-hook-name">{esc(meta.title)}</div>{self._os_chips_html(meta.supported_platforms)}',
                    sanitize=False,
                ).classes("itg-hook-text")
                sw = (
                    ui.switch(value=self._buffer_enabled.get(hid, False) if hook_supported else False)
                    .props("dense")
                    .on("update:model-value", lambda e, h=hid: self._on_toggle(h, bool(e.args)))
                )
                if not hook_supported:
                    sw.disable()
                    sw.tooltip(self._unsupported_text(hid))
                self.ui_elements[f"{hid}_toggle"] = sw
            with ui.element("div").classes("itg-hook-state"):
                line = ui.html("", sanitize=False).classes("itg-hook-line")
                self.ui_elements[f"{hid}_line"] = line
                # Full status text on hover (the line is cut off when long).
                self.ui_elements[f"{hid}_line_tip"] = ui.tooltip("")
                self._render_hook_actions(hid)
        self._apply_card_state(hid, initial_state, self._unsupported_text(hid) if not hook_supported else "")

    def _hook_ui_actions(self, hook_id: str) -> list:
        cls = get_hook_class(hook_id)
        getter = getattr(cls, "ui_actions", None) if cls is not None else None
        if not callable(getter):
            return []
        try:
            actions = getter()
        except Exception as e:
            logger.debug("ui_actions(%s): %s", hook_id, e)
            return []
        return list(actions or [])

    @staticmethod
    def _detail_classes(tone: str) -> str:
        if tone == "warning":
            return "itg-hook-detail is-warning"
        if tone == "ok":
            return "itg-hook-detail is-ok"
        return "itg-hook-detail"

    def _render_hook_actions(self, hook_id: str) -> None:
        """Hook-specific buttons (e.g. Factorio's mod install), compact at the end of the status line."""
        actions = self._hook_ui_actions(hook_id)
        if not actions:
            return
        with ui.element("div").classes("itg-hook-acts"):
            for action in actions:
                if not isinstance(action, dict):
                    continue
                aid = str(action.get("id") or "")
                label = str(action.get("label") or "Action")
                tooltip = str(action.get("tooltip") or "")
                enabled = bool(action.get("enabled", True))
                run = action.get("run")
                detail = str(action.get("detail") or "").strip()
                tone = str(action.get("detail_tone") or "")
                lbl = ui.label(detail).classes(self._detail_classes(tone))
                self.ui_elements[f"{hook_id}_action_{aid}_detail"] = lbl
                btn = outline_button(
                    label,
                    lambda _e=None, h=hook_id, a=aid: self._run_hook_action(h, a),
                ).props("dense")
                if tooltip:
                    self._set_action_tooltip(hook_id, aid, btn, tooltip)
                if not enabled or not callable(run):
                    btn.disable()
                self.ui_elements[f"{hook_id}_action_{aid}"] = btn

    def _set_action_tooltip(self, hook_id: str, aid: str, btn: Any, text: str) -> None:
        """Create the button tooltip once, then only update its text.

        ``Element.tooltip()`` adds a new Tooltip element per call, so calling it from
        the refresh timer leaked one element per tick.
        """
        key = f"{hook_id}_action_{aid}_tooltip"
        tip = self.ui_elements.get(key)
        if tip is None:
            tip = ui.tooltip(text)
            tip.props["target"] = f"#{btn.html_id}"
            self.ui_elements[key] = tip
        elif tip.text != text:
            tip.set_text(text)

    def _refresh_hook_actions(self, hook_id: str, actions: Optional[list] = None) -> None:
        if actions is None:
            actions = self._hook_ui_actions(hook_id)
        for action in actions:
            if not isinstance(action, dict):
                continue
            aid = str(action.get("id") or "")
            btn = self.ui_elements.get(f"{hook_id}_action_{aid}")
            if btn is None:
                continue
            label = str(action.get("label") or "Action")
            try:
                btn.text = label
            except Exception:
                pass
            enabled = bool(action.get("enabled", True)) and callable(action.get("run"))
            try:
                if enabled:
                    btn.enable()
                else:
                    btn.disable()
            except Exception:
                pass
            tooltip = str(action.get("tooltip") or "")
            if tooltip:
                try:
                    self._set_action_tooltip(hook_id, aid, btn, tooltip)
                except Exception:
                    pass
            detail_el = self.ui_elements.get(f"{hook_id}_action_{aid}_detail")
            if detail_el is None:
                continue
            detail = str(action.get("detail") or "").strip()
            try:
                detail_el.text = detail
            except Exception:
                pass
            tone = str(action.get("detail_tone") or "")
            try:
                detail_el.classes(replace=self._detail_classes(tone))
            except Exception:
                pass

    def _run_hook_action(self, hook_id: str, action_id: str) -> None:
        for action in self._hook_ui_actions(hook_id):
            if not isinstance(action, dict):
                continue
            if str(action.get("id") or "") != action_id:
                continue
            run = action.get("run")
            if not callable(run):
                return
            label = str(action.get("label") or action_id)
            hook_title = self._hook_title(hook_id)
            try:
                result = run()
            except Exception as e:
                logger.warning(
                    "hook action %s/%s failed: %s", hook_id, action_id, e, exc_info=True
                )
                notify(
                    "",
                    type="error",
                    source="game_hooks",
                    title=f"{hook_title}: “{label}” didn't work",
                    hint="Make sure the game is running and the hook is attached, then try again.",
                    details=f"{type(e).__name__}: {e}",
                )
                return
            ok = True
            message = ""
            if isinstance(result, tuple) and result:
                ok = bool(result[0])
                if len(result) > 1:
                    message = str(result[1] or "")
            elif isinstance(result, str):
                message = result
            if message:
                if ok:
                    notify(message, type="success", source="game_hooks", title=f"{hook_title}: {label}")
                else:
                    notify(
                        message,
                        type="error",
                        source="game_hooks",
                        title=f"{hook_title}: “{label}” didn't work",
                        hint="Check the hook's card for its status, then try again.",
                    )
            self._refresh_hook_actions(hook_id)
            return

    @staticmethod
    def _hook_title(hook_id: str) -> str:
        for meta in list_hooks_for_ui():
            if meta.hook_id == hook_id:
                return meta.title
        return hook_id

    def _on_toggle(self, hook_id: str, value: bool) -> None:
        if not self._hook_supported.get(hook_id, True):
            return
        if value != self._buffer_enabled.get(hook_id, False):
            self._buffer_enabled[hook_id] = value
            self.dirty = True

    def on_enter(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = True
        if self._page is not None:
            self._page.set_active(True)
        layout_schedule(0.05, self._refresh_all_runtime_status_async, once=True)

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False
        if self._page is not None:
            self._page.set_active(False)

    def save(self) -> None:
        try:
            failed = []
            for meta in list_hooks_for_ui():
                hid = meta.hook_id
                to_save = (
                    self._buffer_enabled.get(hid, False)
                    if self._hook_supported.get(hid, True)
                    else False
                )
                # This tab reports a failed write itself (one message for the save).
                with quiet_notifications():
                    clear_last_write_problem()
                    ok = database_manager.set_data(
                        enabled_db_path(hid), {"enabled": to_save}
                    )
                    problem = last_write_problem()
                if ok or (problem and problem[0] == "superseded"):
                    set_hook_enabled_cached(hid, to_save)
                    self._loaded_enabled[hid] = bool(to_save)
                    self._buffer_enabled[hid] = bool(to_save)
                    logger.info("GameHooks: %s_enabled=%s", hid, to_save)
                else:
                    status, reason = problem or ("failed", "")
                    failed.append(f"{meta.title}: {status}{' - ' + reason if reason else ''}")
            if not failed:
                self.dirty = False
                notify("", type="success", source="game_hooks", title="Game Hooks saved")
            else:
                self._notify_save_failed("\n".join(failed))
        except Exception as e:
            logger.error("GameHooks save failed: %s", e, exc_info=True)
            self._notify_save_failed(f"{type(e).__name__}: {e}")
        self._refresh_all_runtime_status()

    @staticmethod
    def _notify_save_failed(details: str) -> None:
        notify(
            "Your changes are still selected here.",
            type="error",
            source="game_hooks",
            title="Couldn't save Game Hooks settings",
            hint="Check the connection in Settings → Database, then press Save again.",
            details=details,
            actions=nav_actions_settings("Database"),
        )

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

"""
Central notification API: toast (NiceGUI), persisted history, deduplication, actions.

Call :func:`notify` instead of ``ui.notify`` so all user messages flow through one path.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, cast

from nicegui import ui

from .path_utils import get_data_path

logger = logging.getLogger(__name__)

HISTORY_FILENAME = "notification_center_history.json"
MAX_HISTORY_ITEMS = 200
DEFAULT_DEDUPE_COOLDOWN_SEC = 45.0

_last_emit_monotonic: Dict[str, float] = {}
_history: List[Dict[str, Any]] = []
_history_refresh_callbacks: List[Callable[[], None]] = []


def _history_path() -> Path:
    return Path(get_data_path(HISTORY_FILENAME))


def _notifications_enabled() -> bool:
    try:
        from .dataobjects import state_manager

        s = state_manager.get_app_settings()
        if s is None:
            return True
        return bool(getattr(s, "notifications_enabled", True))
    except Exception:
        return True


def _normalize_notify_type(notify_type: Optional[str]) -> str:
    if not notify_type:
        return "info"
    t = str(notify_type).lower()
    if t in ("error", "err"):
        return "negative"
    if t in ("ok", "success"):
        return "positive"
    if t in ("warn",):
        return "warning"
    if t in ("positive", "negative", "warning", "info", "ongoing"):
        return t
    return "info"


def load_history() -> None:
    global _history
    p = _history_path()
    if not p.exists():
        _history = []
        return
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            _history = [x for x in data if isinstance(x, dict)]
        else:
            _history = []
    except Exception as e:
        logger.warning("Could not load notification history: %s", e)
        _history = []


def save_history() -> None:
    p = _history_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(_history[-MAX_HISTORY_ITEMS:], f, indent=2)
    except Exception as e:
        logger.warning("Could not save notification history: %s", e)


def register_history_refresh(callback: Callable[[], None]) -> None:
    if callback not in _history_refresh_callbacks:
        _history_refresh_callbacks.append(callback)


def _trigger_history_refresh() -> None:
    for cb in list(_history_refresh_callbacks):
        try:
            cb()
        except Exception as e:
            logger.debug("history refresh callback error: %s", e)


def get_history() -> List[Dict[str, Any]]:
    return list(_history)


def clear_history() -> None:
    global _history
    _history = []
    save_history()
    _trigger_history_refresh()


def remove_history_item(item_id: str) -> None:
    global _history
    _history = [h for h in _history if h.get("id") != item_id]
    save_history()
    _trigger_history_refresh()


def copy_text_to_clipboard(text: str) -> bool:
    text = text or ""
    try:
        import pyperclip  # type: ignore

        pyperclip.copy(text)
        return True
    except Exception:
        pass
    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
            return True
        if sys.platform == "win32":
            subprocess.run(
                ["clip"],
                input=text.encode("utf-8"),
                shell=True,
                check=False,
            )
            return True
        for cmd in (
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ):
            try:
                subprocess.run(cmd, input=text.encode("utf-8"), check=False)
                return True
            except FileNotFoundError:
                continue
    except Exception as e:
        logger.debug("clipboard copy failed: %s", e)
    return False


def _pick_navigate_action(
    actions: Optional[List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """First navigate action in the list, if any."""
    for a in actions or []:
        if isinstance(a, dict) and a.get("kind") == "navigate":
            return a
    return None


def run_action(
    action: Dict[str, Any],
    *,
    message: str,
) -> None:
    kind = (action or {}).get("kind")
    if kind == "copy":
        if not copy_text_to_clipboard(message):
            logger.debug("clipboard copy failed for notification action")
    elif kind == "navigate":
        from .help_system.contextual_help import navigate_to_settings_subtab

        main_tab = action.get("main_tab")
        settings_subtab = action.get("settings_subtab")
        if settings_subtab:
            navigate_to_settings_subtab(str(settings_subtab), main_tab=str(main_tab) if main_tab else None)
        elif main_tab:
            from .help_system.contextual_help import navigate_to_main_tab

            navigate_to_main_tab(str(main_tab))


def _should_skip_dedupe(dedupe_key: Optional[str], cooldown: float) -> bool:
    if not dedupe_key:
        return False
    now = time.monotonic()
    last = _last_emit_monotonic.get(dedupe_key)
    if last is not None and (now - last) < cooldown:
        return True
    return False


def _mark_dedupe(dedupe_key: str) -> None:
    _last_emit_monotonic[dedupe_key] = time.monotonic()


def notify(
    message: str,
    *,
    type: str = "info",
    position: str = "top-right",
    timeout: Optional[float] = None,
    dedupe_key: Optional[str] = None,
    dedupe_cooldown_sec: float = DEFAULT_DEDUPE_COOLDOWN_SEC,
    skip_toast: bool = False,
    actions: Optional[List[Dict[str, Any]]] = None,
    **kwargs: Any,
) -> Optional[str]:
    """
    Show a toast and record in notification center history.

    ``type`` accepts NiceGUI/Quasar names; ``error`` maps to ``negative``.

    Returns the history entry id, or None if deduped and skipped entirely.
    """
    if not _notifications_enabled():
        return None

    ntype = _normalize_notify_type(type)
    key = dedupe_key or f"msg:{ntype}:{message[:120]}"
    if _should_skip_dedupe(key, dedupe_cooldown_sec):
        return None
    _mark_dedupe(key)

    entry_id = str(uuid.uuid4())
    entry: Dict[str, Any] = {
        "id": entry_id,
        "ts": time.time(),
        "message": message,
        "type": ntype,
        "dedupe_key": dedupe_key,
        "actions": actions or [],
    }
    _history.append(entry)
    if len(_history) > MAX_HISTORY_ITEMS:
        del _history[: len(_history) - MAX_HISTORY_ITEMS]
    save_history()
    _trigger_history_refresh()

    if not skip_toast:
        opts = {"type": ntype, "position": position}
        if timeout is not None:
            opts["timeout"] = timeout
        opts.update(kwargs)
        try:
            ui.notify(message, **opts)
        except RuntimeError:
            # No NiceGUI slot context (called from a Flask route, a
            # background thread, or any other non-UI task). Fall back
            # to enqueuing the notify message directly into every live
            # client's outbox — same payload shape ``ui.notify`` would
            # have produced. This keeps the toast behaviour for
            # background callers (Spore Studio /notify proxy, service
            # watchers, etc.) without forcing every site to wrap calls
            # in a ``with client:`` block.
            try:
                _broadcast_notify(message, opts)
            except Exception as e:
                logger.warning(
                    "broadcast notify fallback failed: %s", e, exc_info=True,
                )
        except Exception as e:
            logger.error("ui.notify failed: %s", e, exc_info=True)

    return entry_id


def _broadcast_notify(message: str, opts: Dict[str, Any]) -> None:
    """Push a notify payload directly into every connected client's outbox.

    Mirrors what ``ui.notify`` does internally, minus the ``context.client``
    lookup that requires a slot stack. Designed to be called from any
    thread (Flask request handlers, background workers, …) — the actual
    enqueue runs on NiceGUI's asyncio loop via ``call_soon_threadsafe``
    because ``Outbox.enqueue_message`` triggers ``asyncio.Event.set()``,
    which is not safe to invoke from a foreign thread.
    """
    from nicegui import Client, core

    payload: Dict[str, Any] = {}
    for k, v in opts.items():
        if v is None:
            continue
        key = "closeBtn" if k == "close_button" else (
            "multiLine" if k == "multi_line" else k
        )
        payload[key] = v
    payload["message"] = str(message)

    loop = getattr(core, "loop", None)

    def _enqueue_on_loop() -> None:
        # Mycelian uses NiceGUI's auto-index pattern (no @ui.page), so
        # ``Client.auto_index_client`` IS the live UI window — we MUST
        # include it. The outbox loop already skips clients without a
        # socket connection, so no extra filter is needed here.
        for client in list(Client.instances.values()):
            try:
                client.outbox.enqueue_message(
                    "notify", dict(payload), client.id
                )
            except Exception as e:
                logger.debug(
                    "notify broadcast skipped client %s: %s", client, e
                )

    if loop is not None and loop.is_running():
        try:
            loop.call_soon_threadsafe(_enqueue_on_loop)
            return
        except RuntimeError as e:
            logger.debug(
                "notify broadcast: call_soon_threadsafe failed (%s); "
                "falling back to direct enqueue",
                e,
            )

    # NiceGUI loop unavailable (e.g. unit test, app shutting down).
    # Direct enqueue is best-effort: the deque write itself is GIL-safe,
    # and if the asyncio.Event.set() race fails it's harmless — the next
    # naturally-triggered enqueue will flush this message too.
    _enqueue_on_loop()


def nav_actions_settings(subtab: str) -> List[Dict[str, Any]]:
    """Toast/history: open Settings and select a sub-tab by label."""
    return [
        {
            "kind": "navigate",
            "main_tab": "Settings",
            "settings_subtab": subtab,
        }
    ]


def nav_actions_main_tab(tab_label: str) -> List[Dict[str, Any]]:
    """Toast/history: switch a top-level main tab only."""
    return [{"kind": "navigate", "main_tab": tab_label}]


def notify_critical(
    message: str,
    *,
    dedupe_key: Optional[str] = None,
    dedupe_cooldown_sec: float = 120.0,
    actions: Optional[List[Dict[str, Any]]] = None,
    timeout: Optional[float] = 8.0,
) -> Optional[str]:
    """
    User-visible error for serious backend failures (toast + notification history).

    Prefer a stable ``dedupe_key`` when the same condition may log repeatedly.
    """
    return notify(
        message,
        type="negative",
        dedupe_key=dedupe_key,
        dedupe_cooldown_sec=dedupe_cooldown_sec,
        actions=actions,
        timeout=timeout,
    )


def maybe_suggest_game_hook_for_category(category: Optional[str]) -> None:
    """If Twitch category matches a supported hook game, nudge user toward Game Hooks."""
    if not category or not str(category).strip():
        return
    c = str(category).strip().lower()
    hook_id: Optional[str] = None
    game_label = ""
    if "final fantasy vii" in c or c == "ff7" or " ff7" in f" {c}":
        hook_id = "ff7"
        game_label = category.strip()
    if not hook_id:
        return
    dk = f"game_hook_suggest:{hook_id}:{c}"
    msg = f"Playing {game_label} on PC? Try the game hook out!"
    notify(
        msg,
        type="info",
        dedupe_key=dk,
        dedupe_cooldown_sec=3600.0,
        actions=[
            {
                "kind": "navigate",
                "main_tab": "Settings",
                "settings_subtab": "Game Hooks",
            },
            {"kind": "copy"},
        ],
    )


def _twitch_connected() -> bool:
    try:
        from .dataobjects import state_manager

        t = state_manager.get_twitch_data()
        if not t:
            return False
        return bool((getattr(t, "auth_token", "") or "").strip()) and bool(
            (getattr(t, "user_id", "") or "").strip()
        )
    except Exception:
        return False


def _spotify_connected() -> bool:
    try:
        from .dataobjects import state_manager

        s = state_manager.get_spotify_data()
        if not s:
            return False
        st = (getattr(s, "connection_status", "") or "").strip().lower()
        return st == "connected"
    except Exception:
        return False


def _youtube_connected() -> bool:
    try:
        from .dataobjects import state_manager

        y = state_manager.get_youtube_data()
        if not y:
            return False
        st = (getattr(y, "connection_status", "") or "").strip().lower()
        return st.startswith("connected") or st.startswith("partial")
    except Exception:
        return False


def _psn_connected() -> bool:
    """Aligned with PSN tab: NPSSO present in settings or live snapshot."""
    try:
        from .dataobjects import state_manager

        live = state_manager.get_live_psn_data()
        settings = state_manager.get_psn_settings_data()
        token_in_settings = (
            (settings.npsso_code or "").strip() if settings else ""
        )
        token_in_live = ""
        if live and getattr(live, "npsso_code", None):
            token_in_live = str(live.npsso_code or "").strip()
        return bool(token_in_settings or token_in_live)
    except Exception:
        return False


_service_last: Dict[str, bool] = {}


def poll_service_connection_changes() -> None:
    """Call from a UI timer; emit notify on edge transitions only."""
    if not _notifications_enabled():
        return

    states = {
        "twitch": _twitch_connected(),
        "spotify": _spotify_connected(),
        "youtube": _youtube_connected(),
        "psn": _psn_connected(),
    }
    labels = {
        "twitch": "Twitch",
        "spotify": "Spotify",
        "youtube": "YouTube",
        "psn": "PSN",
    }
    subtabs = {
        "twitch": "Twitch",
        "spotify": "Spotify",
        "youtube": "YouTube",
        "psn": "PSN",
    }

    for key, now_connected in states.items():
        prev = _service_last.get(key)
        if prev is None:
            _service_last[key] = now_connected
            continue
        if prev == now_connected:
            continue
        _service_last[key] = now_connected
        name = labels[key]
        sub = subtabs[key]
        if now_connected:
            msg = f"{name} connected"
            ntype = "positive"
        else:
            msg = f"{name} disconnected"
            ntype = "warning"
        dk = f"svc:{key}:{'1' if now_connected else '0'}"
        notify(
            msg,
            type=ntype,
            dedupe_key=dk,
            dedupe_cooldown_sec=3.0,
            actions=[
                {
                    "kind": "navigate",
                    "main_tab": "Settings",
                    "settings_subtab": sub,
                }
            ],
        )


def start_service_watcher_timer() -> None:
    """Start periodic polling for integration connection edges (after UI exists)."""
    ui.timer(2.0, poll_service_connection_changes, active=True)


# Load persisted history at import (for non-UI code paths); UI registers refresh later
load_history()

_history_last_read_ts: float = time.time()

_history_column: Optional[Any] = None
_notification_dialog: Optional[Any] = None
_tray_badge_ref: Optional[Any] = None


def _notification_panel_is_open() -> bool:
    global _notification_dialog
    if _notification_dialog is None:
        return False
    try:
        if getattr(_notification_dialog, "is_deleted", False):
            return False
    except Exception:
        return False
    return bool(getattr(_notification_dialog, "value", False))


def _compute_tray_badge_count() -> int:
    if _notification_panel_is_open():
        return 0
    cut = _history_last_read_ts
    return sum(1 for e in _history if float(e.get("ts") or 0.0) > cut)


def _update_tray_badge() -> None:
    ref = _tray_badge_ref
    if ref is None:
        return
    try:
        if getattr(ref, "is_deleted", False):
            return
    except Exception:
        return
    n = _compute_tray_badge_count()
    if n <= 0:
        ref.text = ""
        ref.classes(add="hidden")
        return
    ref.classes(remove="hidden")
    ref.text = str(n) if n < 100 else "99+"


def _bump_history_read_watermark() -> None:
    global _history_last_read_ts
    _history_last_read_ts = time.time()
    _update_tray_badge()


def _render_history_cards() -> None:
    col = _history_column
    if col is None:
        return
    col.clear()
    with col:
        items = list(reversed(get_history()))
        if not items:
            ui.label("No notifications yet").classes("text-sm secondary-text p-2")
            return
        for entry in items:
            eid = entry.get("id", "")
            msg = entry.get("message", "")
            ntype = str(entry.get("type", "info") or "info")
            ts = entry.get("ts", 0)
            try:
                tss = time.strftime("%b %d, %Y %H:%M:%S", time.localtime(ts))
            except Exception:
                tss = ""
            type_class = {
                "positive": "nc-history-card--positive",
                "negative": "nc-history-card--negative",
                "warning": "nc-history-card--warning",
                "info": "nc-history-card--info",
                "ongoing": "nc-history-card--ongoing",
            }.get(ntype, "nc-history-card--default")

            nav_action = _pick_navigate_action(entry.get("actions") or [])

            def make_remove(i: str):
                return lambda: remove_history_item(i)

            def make_copy_handler(text: str):
                def _copy() -> None:
                    copy_text_to_clipboard(text)

                return _copy

            def make_nav_handler(action: Dict[str, Any], text: str):
                def _go() -> None:
                    run_action(action, message=text)

                return _go

            with ui.card().classes(
                f"nc-history-card w-full min-w-0 overflow-visible {type_class} p-0 gap-0"
            ):
                with ui.row().classes(
                    "w-full items-center justify-between gap-2 flex-nowrap "
                    "px-2 py-0.5 border-b border-[var(--color-border-subtle)]"
                ):
                    if tss:
                        ui.label(tss).classes(
                            "text-xs text-[var(--color-text-muted)] min-w-0 flex-1 "
                            "leading-tight"
                        )
                    else:
                        ui.element("div").classes("flex-1 min-h-0")
                    with ui.row().classes("items-center gap-0 shrink-0"):
                        ui.button(
                            icon="content_copy",
                            on_click=make_copy_handler(msg),
                        ).props("flat dense round size=sm").tooltip("Copy text")
                        ui.button(icon="close", on_click=make_remove(cast(str, eid))).props(
                            "flat dense round size=sm"
                        ).tooltip("Dismiss")

                body_classes = "nc-history-card__body w-full min-w-0 px-2 py-1 gap-0"
                if nav_action:
                    body_classes += " nc-history-card__body--clickable cursor-pointer"
                body = ui.column().classes(body_classes)
                if nav_action:
                    body.on("click", make_nav_handler(nav_action, msg))
                with body:
                    ui.label(msg).classes(
                        "text-sm break-words text-[var(--color-text-primary)] leading-snug"
                    )


def create_notification_tray_button() -> None:
    """Place inside the main header row (next to tabs). Opens notification center."""
    global _history_column, _notification_dialog, _tray_badge_ref

    def refresh() -> None:
        _render_history_cards()
        _update_tray_badge()

    register_history_refresh(refresh)

    def toggle_panel() -> None:
        refresh()
        if _notification_dialog is None:
            return
        if _notification_dialog.value:
            _notification_dialog.close()
            _bump_history_read_watermark()
        else:
            _notification_dialog.open()
            _bump_history_read_watermark()

    def close_notification_panel() -> None:
        if _notification_dialog is not None:
            _notification_dialog.close()
        _bump_history_read_watermark()

    # Seamless right panel: no modal backdrop, app stays clickable; persistent = no click-outside dismiss
    with ui.dialog().props(
        "seamless position=right persistent full-height no-esc-dismiss"
    ) as dlg, ui.card().classes(
        "w-[min(100vw,420px)] max-w-[100vw] h-full flex flex-col p-3 gap-2 "
        "rounded-none border-l border-[var(--color-border-default)]"
    ):
        _notification_dialog = dlg
        with ui.row().classes("w-full items-center justify-between gap-2"):
            ui.label("Notifications").classes("text-lg font-bold")
            with ui.row().classes("items-center gap-0"):
                ui.button("Clear all", on_click=lambda: clear_history()).props(
                    "flat dense no-caps"
                )
                ui.button(
                    icon="close",
                    on_click=close_notification_panel,
                ).props("flat dense round").tooltip("Close panel")
        with ui.scroll_area().classes("w-full flex-grow min-h-0"):
            _history_column = ui.column().classes("w-full gap-2")

    with ui.row().classes("relative inline-flex items-center shrink-0"):
        ui.button(icon="notifications", on_click=toggle_panel).props(
            "flat round dense"
        ).tooltip("Notifications")
        _tray_badge_ref = ui.badge("", color="negative").classes(
            "absolute -top-0.5 -right-0.5 min-w-[1.125rem] text-[0.65rem] "
            "leading-none px-1 py-0.5 pointer-events-none hidden"
        ).props("rounded")
    refresh()

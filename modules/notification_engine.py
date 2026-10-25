# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Central notification API: toast (NiceGUI), persisted history, deduplication, actions.

Call :func:`notify` instead of ``ui.notify`` so all user messages flow through one path.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .connection_status_tracker import service_configured as _service_configured
from .service_alerts import ServiceAlertTracker
from .notify_settings import LEVEL_TO_TYPE, current_prefs, level_of, normalize_source, source_label
from .path_utils import get_data_path

logger = logging.getLogger(__name__)

HISTORY_FILENAME = "notification_center_history.json"
MAX_HISTORY_ITEMS = 200
DEFAULT_DEDUPE_COOLDOWN_SEC = 45.0
DEFAULT_TOAST_TIMEOUT_MS = 5000
# Values below this threshold are treated as seconds (legacy call sites used ``timeout=3``).
_TIMEOUT_SECONDS_THRESHOLD = 1000

def _toast_head_html() -> str:
    """The shared pop-up renderer plus the glue that sends button clicks back to Python."""
    try:
        from .path_utils import get_assets_path

        with open(get_assets_path("default_assets/mycelian_toast.js"), "r", encoding="utf-8") as f:
            js = f.read()
    except Exception as e:
        logger.error("Could not load the notification renderer: %s", e)
        js = ""
    glue = (
        "if(window.MycelianToast){"
        "MycelianToast.onAction=function(id,i){if(typeof emitEvent==='function')emitEvent('mycelian-toast-action',{id:id,index:i});};"
        "MycelianToast.onDismiss=null;MycelianToast.ensureStyles();}"
    )
    return "<script>" + js + "\n" + glue + "</script>"


_notification_leave_script_injected = False
_toast_nav_handler_registered = False

_last_emit_monotonic: Dict[str, float] = {}
_history: List[Dict[str, Any]] = []
_history_loaded = False
_history_refresh_callbacks: List[Callable[[], None]] = []
_pending_toasts: List[tuple[str, Dict[str, Any]]] = []
_history_lock = threading.RLock()
_MAX_PENDING_TOASTS = 20
_service_watcher_started = False


def _history_path() -> Path:
    return Path(get_data_path(HISTORY_FILENAME))


def load_history() -> None:
    global _history, _history_loaded
    p = _history_path()
    if not p.exists():
        with _history_lock:
            _history = []
            _history_loaded = True
        return
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        with _history_lock:
            if isinstance(data, list):
                _history = [x for x in data if isinstance(x, dict)]
            else:
                _history = []
            _history_loaded = True
    except Exception as e:
        logger.warning("Could not load notification history: %s", e)
        with _history_lock:
            _history = []
            _history_loaded = True


def _ensure_history_loaded() -> None:
    """Read notification history on first use, not at import."""
    if _history_loaded:
        return
    with _history_lock:
        if _history_loaded:
            return
        load_history()


_history_flush_timer: Optional[threading.Timer] = None


def save_history() -> None:
    p = _history_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with _history_lock:
            payload = list(_history[-MAX_HISTORY_ITEMS:])
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        logger.warning("Could not save notification history: %s", e)


def _flush_history_soon() -> None:
    """Write history off the toast path. A later toast replaces a pending flush."""
    global _history_flush_timer

    def _run() -> None:
        global _history_flush_timer
        _history_flush_timer = None
        save_history()

    with _history_lock:
        timer = _history_flush_timer
        if timer is not None:
            return
        _history_flush_timer = threading.Timer(1.0, _run)
        _history_flush_timer.daemon = True
        _history_flush_timer.start()


def register_history_refresh(callback: Callable[[], None]) -> None:
    """Replace the tray refresh callback (rebuilds create a new closure)."""
    _history_refresh_callbacks.clear()
    _history_refresh_callbacks.append(callback)


def _run_on_ui_loop(fn: Callable[[], Any]) -> None:
    from .ui_timer import run_on_ui_loop

    run_on_ui_loop(fn)


def _app_schedule(*args: Any, **kwargs: Any) -> Any:
    from .ui_timer import app_schedule

    return app_schedule(*args, **kwargs)


def _trigger_history_refresh() -> None:
    def _run() -> None:
        for cb in list(_history_refresh_callbacks):
            try:
                cb()
            except Exception as e:
                logger.debug("history refresh callback error: %s", e)

    _run_on_ui_loop(_run)


def get_history() -> List[Dict[str, Any]]:
    _ensure_history_loaded()
    with _history_lock:
        return list(_history)


def clear_history() -> None:
    global _history
    _ensure_history_loaded()
    with _history_lock:
        _history = []
        save_history()
    _trigger_history_refresh()


def remove_history_item(item_id: str) -> None:
    global _history
    _ensure_history_loaded()
    with _history_lock:
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


_NAV_KEYS = ("main_tab", "settings_subtab", "spore_template", "chatbot_tab", "alerts_tab", "source_template")


def _action_label(action: Dict[str, Any]) -> str:
    """Button text for an action that didn't name one."""
    if action.get("label"):
        return str(action["label"])
    kind = action.get("kind")
    if kind == "copy":
        return "Copy details"
    if kind != "navigate":
        return "Open"
    if action.get("spore_template"):
        return "Open in Spore Studio"
    if action.get("source_template"):
        return "Open in Source Settings"
    if action.get("settings_subtab"):
        return f"Open {action['settings_subtab']} settings"
    if action.get("chatbot_tab"):
        return f"Open Chatbot → {action['chatbot_tab']}"
    if action.get("alerts_tab"):
        return f"Open Alerts → {action['alerts_tab']}"
    if action.get("main_tab"):
        return f"Open {action['main_tab']}"
    return "Open"


def _action_icon(action: Dict[str, Any]) -> str:
    if action.get("icon"):
        return str(action["icon"])
    if action.get("kind") == "copy":
        return "content_copy"
    if action.get("spore_template"):
        return "auto_awesome"
    return "open_in_new"


def _normalize_actions(actions: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for a in actions or []:
        if not isinstance(a, dict):
            continue
        kind = a.get("kind")
        if kind == "navigate":
            if not any(a.get(k) for k in _NAV_KEYS):
                continue
            out.append({k: v for k, v in a.items() if k in _NAV_KEYS + ("kind", "label", "icon") and v})
        elif kind == "copy":
            out.append({k: v for k, v in a.items() if k in ("kind", "label", "icon", "text") and v is not None})
        elif kind == "call" and callable(a.get("fn")):
            out.append(dict(a))
    return out


def run_action(
    action: Dict[str, Any],
    *,
    message: str,
    entry: Optional[Dict[str, Any]] = None,
) -> None:
    kind = (action or {}).get("kind")
    if kind == "copy":
        text = action.get("text") or _copy_text_for(entry) or message
        if copy_text_to_clipboard(text):
            notify("Copied to the clipboard", type="positive", source=(entry or {}).get("source"),
                   dedupe_key="notify:copied", dedupe_cooldown_sec=2)
        else:
            logger.debug("clipboard copy failed for notification action")
    elif kind == "call":
        fn = action.get("fn")
        if callable(fn):
            try:
                fn()
            except Exception as e:
                logger.error("Notification action failed: %s", e, exc_info=True)
    elif kind == "navigate":
        from .help_system import contextual_help as ch

        main_tab = action.get("main_tab")
        settings_subtab = action.get("settings_subtab")
        if settings_subtab:
            ch.navigate_to_settings_subtab(str(settings_subtab), main_tab=str(main_tab) if main_tab else None)
        elif action.get("chatbot_tab"):
            ch.navigate_to_chatbot_subtab(str(action["chatbot_tab"]))
        elif action.get("alerts_tab"):
            ch.navigate_to_alerts_subtab(str(action["alerts_tab"]))
        elif action.get("source_template"):
            ch.navigate_to_source_settings_template(str(action["source_template"]))
        elif main_tab:
            ch.navigate_to_main_tab(str(main_tab))
        spore_template = action.get("spore_template")
        if spore_template:
            from .uiwindows.spore_studio.editor_view import open_template

            ch.navigate_to_main_tab("Spore Studio")
            open_template(str(spore_template))


def _copy_text_for(entry: Optional[Dict[str, Any]]) -> str:
    """What "Copy" copies: the details when there are some, else title, message and fix."""
    if not entry:
        return ""
    if entry.get("details"):
        head = " - ".join(x for x in (entry.get("title"), entry.get("message")) if x)
        return f"{head}\n\n{entry['details']}" if head else str(entry["details"])
    parts = [entry.get("title"), entry.get("message"), entry.get("hint")]
    return "\n".join(str(p) for p in parts if p)


# Repeats of the same notification within the cooldown update one entry (×N) instead
# of adding a new one. Key -> (entry id, last time).
_repeat_index: Dict[str, tuple] = {}
_REPEAT_INDEX_LIMIT = 512
# Callbacks for "call" actions, which can't be stored in history. Entry id -> actions.
_call_actions: Dict[str, List[Dict[str, Any]]] = {}
_CALL_ACTIONS_LIMIT = 200


def _remember_repeat(key: str, entry_id: str) -> None:
    now = time.monotonic()
    _repeat_index[key] = (entry_id, now)
    if len(_repeat_index) > _REPEAT_INDEX_LIMIT:
        for k, (_eid, stamp) in list(_repeat_index.items()):
            if now - stamp > _DEDUPE_ENTRY_TTL_SEC:
                _repeat_index.pop(k, None)


def _find_repeat(key: str, cooldown: float) -> Optional[str]:
    hit = _repeat_index.get(key)
    if not hit:
        return None
    entry_id, stamp = hit
    if time.monotonic() - stamp >= cooldown:
        return None
    return entry_id


def _should_skip_dedupe(dedupe_key: Optional[str], cooldown: float) -> bool:
    """True when ``dedupe_key`` fired within ``cooldown`` (used by callers that gate work)."""
    if not dedupe_key:
        return False
    now = time.monotonic()
    last = _last_emit_monotonic.get(dedupe_key)
    if last is not None and (now - last) < cooldown:
        return True
    return False


# Dedupe cooldowns are seconds to minutes, so anything untouched for an hour can never
# suppress a future notification and is only holding memory.
_DEDUPE_ENTRY_TTL_SEC = 3600.0
_DEDUPE_PRUNE_THRESHOLD = 256


def _mark_dedupe(dedupe_key: str) -> None:
    now = time.monotonic()
    _last_emit_monotonic[dedupe_key] = now
    if len(_last_emit_monotonic) > _DEDUPE_PRUNE_THRESHOLD:
        for key, stamp in list(_last_emit_monotonic.items()):
            if now - stamp > _DEDUPE_ENTRY_TTL_SEC:
                _last_emit_monotonic.pop(key, None)


def _toast_payload(entry: Dict[str, Any], timeout_ms: int) -> Dict[str, Any]:
    """What the browser renderer gets (no callables, labels resolved)."""
    actions = []
    click = None
    for index, a in enumerate(entry.get("actions") or []):
        actions.append({"label": _action_label(a), "icon": _action_icon(a)})
        if click is None and a.get("kind") == "navigate":
            click = index
    return {
        "id": entry["id"],
        "level": entry.get("level", "info"),
        "source": source_label(entry.get("source")),
        "title": entry.get("title") or "",
        "message": entry.get("message") or "",
        "hint": entry.get("hint") or "",
        "actions": actions,
        "count": int(entry.get("count") or 1),
        "timeout": timeout_ms,
        "progress": entry.get("progress"),
        "click": click,
    }


def _deliver_js(code: str) -> bool:
    """Run ``code`` in every connected client. False when the UI isn't up yet (retry later).

    Never touches ``context.client``: before ``ui.run`` that creates a NiceGUI pseudo-client
    and flips ``core.script_mode`` (which can shut the app down). Safe from any thread: the
    enqueue runs on NiceGUI's loop.
    """
    try:
        from nicegui import Client, core

        app = getattr(core, "app", None)
        if app is None or not getattr(app, "is_started", False):
            return False
        clients = [c for c in list(Client.instances.values()) if not getattr(c, "_deleted", False)]
        if not clients:
            return False
    except Exception as e:
        logger.warning("notification delivery failed: %s", e)
        return False

    def _enqueue() -> None:
        for client in list(Client.instances.values()):
            if getattr(client, "_deleted", False):
                continue
            try:
                client.outbox.enqueue_message("run_javascript", {"code": code}, client.id)
            except Exception as e:
                logger.debug("notification skipped client %s: %s", client, e)

    loop = getattr(core, "loop", None)
    if loop is not None and loop.is_running():
        try:
            loop.call_soon_threadsafe(_enqueue)
            return True
        except RuntimeError:
            pass
    _enqueue()
    return True


def _deliver_toast(message: Any, opts: Optional[Dict[str, Any]] = None) -> bool:
    """Show (or update) one pop-up. ``message`` is a renderer payload (or plain text)."""
    inject_notification_ui_assets()
    payload = message if isinstance(message, dict) else {
        "id": str(uuid.uuid4()), "level": level_of((opts or {}).get("type")), "title": str(message), "timeout": DEFAULT_TOAST_TIMEOUT_MS,
    }
    cfg = (opts or {}).get("config") or current_prefs().for_client()
    code = (
        "window.MycelianToast&&(MycelianToast.configure(" + json.dumps(cfg) + "),"
        "MycelianToast.show(" + json.dumps(payload) + "))"
    )
    return _deliver_js(code)


def dismiss_notification(entry_id: str) -> None:
    """Close a pop-up (it stays in the notification centre)."""
    _deliver_js("window.MycelianToast&&MycelianToast.dismiss(" + json.dumps(str(entry_id)) + ")")


def _queue_toast(payload: Dict[str, Any], opts: Dict[str, Any]) -> None:
    with _history_lock:
        _pending_toasts[:] = [(p, o) for p, o in _pending_toasts if not (isinstance(p, dict) and p.get("id") == payload["id"])]
        _pending_toasts.append((payload, dict(opts)))
        if len(_pending_toasts) > _MAX_PENDING_TOASTS:
            # Keep errors when trimming: they are usually the root cause.
            errors = [t for t in _pending_toasts if isinstance(t[0], dict) and t[0].get("level") == "error"]
            rest = [t for t in _pending_toasts if t not in errors]
            _pending_toasts[:] = (errors + rest)[-_MAX_PENDING_TOASTS:]
    _ensure_toast_flush_timer()


_toast_flush_timer: Optional[Any] = None


def _toast_flush_tick() -> None:
    flush_pending_toasts()
    timer = _toast_flush_timer
    if timer is None:
        return
    with _history_lock:
        still_pending = bool(_pending_toasts)
    if not still_pending:
        try:
            timer.active = False
        except Exception:
            pass


def _ensure_toast_flush_timer() -> None:
    """Retry queued toasts until they deliver, then stop the timer."""
    global _toast_flush_timer
    timer = _toast_flush_timer
    if timer is not None:
        try:
            timer.active = True
            return
        except Exception:
            _toast_flush_timer = None
    try:
        _toast_flush_timer = _app_schedule(1.0, _toast_flush_tick, active=True)
    except Exception:
        _toast_flush_timer = None


def flush_pending_toasts() -> None:
    """Retry toasts queued before the NiceGUI client was available."""
    with _history_lock:
        if not _pending_toasts:
            return
        pending = list(_pending_toasts)
        _pending_toasts.clear()
    still_pending: List[tuple] = []
    for message, opts in pending:
        if not _deliver_toast(message, opts):
            still_pending.append((message, opts))
    if still_pending:
        with _history_lock:
            _pending_toasts[:] = still_pending + list(_pending_toasts)
        _ensure_toast_flush_timer()


def _find_entry(entry_id: str) -> Optional[Dict[str, Any]]:
    with _history_lock:
        for entry in reversed(_history):
            if entry.get("id") == entry_id:
                return entry
    return None


def _stored_actions(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Actions without callables (history is saved as JSON)."""
    return [{k: v for k, v in a.items() if k != "fn"} for a in actions]


def notify(
    message: str,
    *,
    type: str = "info",
    source: Optional[str] = None,
    title: Optional[str] = None,
    hint: Optional[str] = None,
    details: Optional[str] = None,
    position: Optional[str] = None,
    timeout: Optional[float] = None,
    dedupe_key: Optional[str] = None,
    dedupe_cooldown_sec: float = DEFAULT_DEDUPE_COOLDOWN_SEC,
    skip_toast: bool = False,
    actions: Optional[List[Dict[str, Any]]] = None,
    progress: Optional[float] = None,
    notice_id: Optional[str] = None,
    sticky: bool = False,
    **kwargs: Any,
) -> Optional[str]:
    """Show a pop-up and record it in the notification centre.

    ``type``: error / warning / info / success / ongoing (Quasar names also work).
    ``source``: the feature it belongs to (see ``notify_settings.SOURCES``); the user can
    silence each level per feature. ``title`` is the bold first line and ``message`` the
    detail under it; with no title, ``message`` is the title. ``hint`` says how to fix it.
    ``details`` is what "Copy details" copies (exception text, URLs...).
    ``actions``: ``{"kind": "navigate", main_tab | settings_subtab | chatbot_tab | alerts_tab
    | spore_template | source_template, "label"?}``, ``{"kind": "copy", "text"?}``,
    ``{"kind": "call", "label", "fn"}``.
    Repeats within ``dedupe_cooldown_sec`` update the same entry and show ×N.
    ``notice_id`` updates an existing entry in place (see :func:`notify_progress`).
    How long a pop-up stays comes only from the user's per-level settings (work in progress
    stays until it ends); ``timeout`` and ``sticky`` are accepted for old callers and ignored.

    Returns the entry id, or None when the user's settings drop it entirely.
    """
    del position, kwargs  # legacy Quasar options; the look and place come from settings
    level = level_of(type)
    src = normalize_source(source)
    prefs = current_prefs()
    muted = prefs.is_muted(level, src)
    if muted and not prefs.record_muted:
        return None
    text = str(message or "")
    head = str(title or "").strip()
    norm_actions = _normalize_actions(actions)
    if details and not any(a.get("kind") == "copy" for a in norm_actions):
        norm_actions.append({"kind": "copy"})

    _ensure_history_loaded()
    repeat = False
    with _history_lock:
        entry: Optional[Dict[str, Any]] = _find_entry(notice_id) if notice_id else None
        key = dedupe_key or f"{src}:{level}:{head or text}"[:240]
        if entry is None and not notice_id:
            hit = _find_repeat(key, dedupe_cooldown_sec)
            entry = _find_entry(hit) if hit else None
            repeat = entry is not None
        if dedupe_key:
            _mark_dedupe(dedupe_key)
        now = time.time()
        if entry is None:
            entry = {"id": notice_id or str(uuid.uuid4()), "first_ts": now, "count": 1}
            _history.append(entry)
            if len(_history) > MAX_HISTORY_ITEMS:
                del _history[: len(_history) - MAX_HISTORY_ITEMS]
        elif repeat:
            entry["count"] = int(entry.get("count") or 1) + 1
            entry["read"] = False
            # A repeat that says less keeps what the first one offered.
            if not norm_actions:
                norm_actions = _call_actions.get(entry["id"]) or list(entry.get("actions") or [])
            hint = hint or entry.get("hint")
            details = details or entry.get("details")
        entry.update({
            "ts": now,
            "level": level,
            "type": LEVEL_TO_TYPE.get(level, "info"),
            "source": src,
            "title": head,
            "message": text,
            "hint": str(hint or ""),
            "details": str(details or ""),
            "dedupe_key": dedupe_key,
            "actions": _stored_actions(norm_actions),
            "progress": progress if level == "ongoing" else None,
            "muted": muted,
        })
        if not notice_id:
            _remember_repeat(key, entry["id"])
        if any(a.get("kind") == "call" for a in norm_actions):
            _call_actions[entry["id"]] = norm_actions
            while len(_call_actions) > _CALL_ACTIONS_LIMIT:
                _call_actions.pop(next(iter(_call_actions)))
        snapshot = dict(entry)
    _flush_history_soon()
    _trigger_history_refresh()

    if level in ("error", "warning") and not muted and not repeat and prefs.desktop_when_minimized:
        try:
            from .tray_controller import is_minimized

            if is_minimized():
                from .system_notify import notify_async

                notify_async(text if head else "", title=head or text or "Mycelian")
        except Exception:
            pass

    popup = not skip_toast and prefs.show_popup(level, src)
    if popup or notice_id:
        # Updates of a progress notice always reach its pop-up (or close it when muted).
        if not popup:
            dismiss_notification(snapshot["id"])
        else:
            payload = _toast_payload(snapshot, prefs.duration_ms(level))
            opts = {"config": prefs.for_client()}
            if not _deliver_toast(payload, opts):
                _queue_toast(payload, opts)
    return snapshot["id"]


class ProgressNotice:
    """A "working…" pop-up that updates in place and then turns into the result.

    ``notice = notify_progress("Copying templates", source="files")``
    ``notice.update("18 of 42 files", progress=43)``
    ``notice.done("Templates copied")`` or ``notice.fail("Couldn't copy templates", hint=...)``
    Also a context manager: an exception inside the block calls ``fail``.
    """

    def __init__(self, title: str, *, source: str = "app", message: str = "", progress: Optional[float] = None) -> None:
        self.title = title
        self.source = source
        self.id = str(uuid.uuid4())
        self.finished = False
        notify(message, type="ongoing", source=source, title=title, progress=progress, notice_id=self.id)

    def update(self, message: Optional[str] = None, *, progress: Optional[float] = None, title: Optional[str] = None) -> None:
        if self.finished:
            return
        if title:
            self.title = title
        notify(message or "", type="ongoing", source=self.source, title=self.title, progress=progress, notice_id=self.id)

    def done(self, title: Optional[str] = None, message: str = "", *, type: str = "success", **kwargs: Any) -> None:
        if self.finished:
            return
        self.finished = True
        notify(message, type=type, source=self.source, title=title or self.title, notice_id=self.id, **kwargs)

    def fail(self, title: Optional[str] = None, message: str = "", **kwargs: Any) -> None:
        if self.finished:
            return
        self.finished = True
        notify(message, type="error", source=self.source, title=title or self.title, notice_id=self.id, **kwargs)

    def close(self) -> None:
        """Remove the pop-up and its history entry (the work produced nothing worth keeping)."""
        self.finished = True
        dismiss_notification(self.id)
        remove_history_item(self.id)

    def __enter__(self) -> "ProgressNotice":
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        if exc is not None and not self.finished:
            self.fail(message="Something went wrong.", details=f"{exc_type.__name__}: {exc}")
        elif not self.finished:
            self.close()
        return False


def notify_progress(title: str, *, source: str = "app", message: str = "", progress: Optional[float] = None) -> ProgressNotice:
    return ProgressNotice(title, source=source, message=message, progress=progress)


def nav_actions_settings(subtab: str) -> List[Dict[str, Any]]:
    """Toast/history: open Settings and select a sub-tab by label."""
    return [{"kind": "navigate", "main_tab": "Settings", "settings_subtab": subtab}]


def nav_actions_main_tab(tab_label: str) -> List[Dict[str, Any]]:
    """Toast/history: switch a top-level main tab only."""
    return [{"kind": "navigate", "main_tab": tab_label}]


def nav_chatbot(tab: str) -> List[Dict[str, Any]]:
    return [{"kind": "navigate", "main_tab": "Chatbot", "chatbot_tab": tab}]


def nav_alerts(tab: str) -> List[Dict[str, Any]]:
    return [{"kind": "navigate", "main_tab": "Alerts", "alerts_tab": tab}]


def nav_spore_template(name: str) -> List[Dict[str, Any]]:
    return [{"kind": "navigate", "main_tab": "Spore Studio", "spore_template": name}]


def nav_source_template(name: str) -> List[Dict[str, Any]]:
    return [{"kind": "navigate", "main_tab": "Source Settings", "source_template": name}]


def notify_critical(
    message: str,
    *,
    source: Optional[str] = None,
    title: Optional[str] = None,
    hint: Optional[str] = None,
    details: Optional[str] = None,
    dedupe_key: Optional[str] = None,
    dedupe_cooldown_sec: float = 120.0,
    actions: Optional[List[Dict[str, Any]]] = None,
    timeout: Optional[float] = None,
) -> Optional[str]:
    """A serious failure: an error that stays until dismissed (subject to user settings).

    Prefer a stable ``dedupe_key`` when the same condition may log repeatedly.
    """
    return notify(
        message,
        type="error",
        source=source,
        title=title,
        hint=hint,
        details=details,
        dedupe_key=dedupe_key,
        dedupe_cooldown_sec=dedupe_cooldown_sec,
        actions=actions,
        timeout=timeout,
    )


# Exact Twitch category names (lowercase) per game hook. Remake, Rebirth, Crisis Core etc.
# are different games the hooks can't read.
_GAME_HOOK_CATEGORIES: Dict[str, frozenset] = {
    "ff7": frozenset({"final fantasy vii", "final fantasy 7", "ff7", "ffvii"}),
    "factorio": frozenset({"factorio", "factorio: space age"}),
}
_GAME_HOOK_HINTS: Dict[str, str] = {
    "ff7": "Works with the Steam PC version (ff7_en.exe). Turn it on in Settings → Game Hooks.",
    "factorio": "Turn it on in Settings → Game Hooks. It uses a small Factorio mod that Mycelian can install for you.",
}
_game_hook_suggested: set = set()


def maybe_suggest_game_hook_for_category(category: Optional[str]) -> None:
    """Once per session, suggest a game hook when the Twitch category is a game it supports.

    Skipped when the hook is already on or doesn't run on this OS.
    """
    c = str(category or "").strip().lower()
    hook_id = next((hid for hid, names in _GAME_HOOK_CATEGORIES.items() if c in names), None)
    if not hook_id or hook_id in _game_hook_suggested:
        return
    try:
        from .game_hooks import get_hook_class, is_hook_enabled
        from .game_hooks.base import runtime_os_key

        cls = get_hook_class(hook_id)
        if cls is None or runtime_os_key() not in cls.ui.supported_platforms:
            return
        if is_hook_enabled(hook_id):
            return
        title = cls.ui.title
    except Exception:
        logger.debug("game hook suggestion check failed for %s", hook_id, exc_info=True)
        return
    _game_hook_suggested.add(hook_id)
    notify(
        f"The {title} game hook can show live game data in your overlays.",
        type="info",
        source="game_hooks",
        title=f"Playing {category.strip()}? Try the game hook",
        hint=_GAME_HOOK_HINTS.get(hook_id),
        dedupe_key=f"game_hook_suggest:{hook_id}",
        dedupe_cooldown_sec=86400.0,
        actions=[{"kind": "navigate", "main_tab": "Settings", "settings_subtab": "Game Hooks",
                  "label": "Open Game Hooks"}],
    )


def _service_status_label(key: str) -> str:
    from .connection_status_tracker import get_connection_status

    return get_connection_status(key)


_SERVICE_KEYS = (
    "internet",
    "webengine",
    "twitch",
    "obs",
    "psn",
    "spotify",
    "youtube",
    "discord",
    "streamlabs",
    "streamelements",
)

_SERVICE_LABELS: Dict[str, str] = {
    "internet": "Internet",
    "twitch": "Twitch",
    "spotify": "Spotify",
    "youtube": "YouTube",
    "psn": "PSN",
    "obs": "OBS",
    "discord": "Discord",
    "streamlabs": "Streamlabs",
    "streamelements": "StreamElements",
    "webengine": "Overlay Server",
}

_SERVICE_SUBTABS: Dict[str, str] = {
    "twitch": "Twitch",
    "spotify": "Spotify",
    "youtube": "YouTube",
    "psn": "PSN",
    "obs": "OBS",
    "discord": "Discord",
    "streamlabs": "Streamlabs",
    "streamelements": "StreamElements",
    "webengine": "App Settings",
}

_service_alerts = ServiceAlertTracker()

# A repeated connection notice within this window updates the same entry (×N).
_SERVICE_STATUS_DEDUPE_COOLDOWN_SEC = 600.0


def _status_footer_enabled() -> bool:
    try:
        from .dataobjects import state_manager

        s = state_manager.get_app_settings()
        if s is None:
            return True
        return bool(getattr(s, "status_footer_enabled", True))
    except Exception:
        return True


def _status_footer_minimal() -> bool:
    try:
        from .dataobjects import state_manager

        s = state_manager.get_app_settings()
        if s is None:
            return False
        return bool(getattr(s, "status_footer_minimal", False))
    except Exception:
        return False


_FOOTER_TIERS = ("success", "warning", "error", "info", "muted")


def _apply_footer_minimal_class() -> None:
    if _footer_container is None:
        return
    try:
        if _status_footer_minimal():
            _footer_container.classes(add="service-status-footer--minimal")
        else:
            _footer_container.classes(remove="service-status-footer--minimal")
    except Exception:
        pass


def _set_footer_item_tier(container, tier: str) -> None:
    safe = tier if tier in _FOOTER_TIERS else "info"
    try:
        for name in _FOOTER_TIERS:
            container.classes(remove=f"service-status-tier-{name}")
        container.classes(add=f"service-status-tier-{safe}")
    except Exception:
        pass


def _footer_item_tip(service_key: str, entry: "ServiceFooterEntry") -> str:
    if service_key == "webengine":
        try:
            from .web_engine import get_webengine_health

            detail = get_webengine_health().get("detail")
            if detail:
                return str(detail)
        except Exception:
            pass
        if entry.status_raw:
            return str(entry.status_raw)
    return entry.display


def _set_footer_item_tooltip(container, refs: Dict[str, Any], text: str) -> None:
    if refs.get("tooltip_text") == text and refs.get("tooltip") is not None:
        return
    tip = refs.get("tooltip")
    try:
        if tip is None:
            from nicegui.elements.tooltip import Tooltip

            tip = Tooltip(text)
            tip.props["target"] = f"#{container.html_id}"
            refs["tooltip"] = tip
        else:
            tip.set_text(text)
        refs["tooltip_text"] = text
    except Exception:
        logger.debug("footer tooltip update failed", exc_info=True)


def footer_status_display(service_key: str, status_raw: str) -> str:
    """Short label for the status footer badge.

    Failure phrases are matched before broad "authorization" / "disconnected"
    buckets so a token or login problem is not labeled Idle.
    """
    s = (status_raw or "").strip().lower()
    if not s:
        return "Unknown"
    if service_key == "webengine":
        webengine_labels = {
            "running": "Running",
            "stalled": "Stalled",
            "overloaded": "Overloaded",
            "crashed": "Crashed",
            "restarting": "Restarting",
            "starting": "Starting",
            "stopped": "Stopped",
        }
        if s in webengine_labels:
            return webengine_labels[s]
    if "frozen" in s:
        return "Frozen"
    if "authenticated but" in s and "disconnected" in s:
        return "Degraded"
    if "degraded" in s or "stale" in s or "no recent events" in s:
        return "Degraded"
    if s.startswith("partial"):
        return "Partial"
    if s == "connected" or s.startswith("connected"):
        return "Connected"
    if "reconnecting" in s:
        return "Reconnecting"
    if s in ("connecting", "disconnecting"):
        return status_raw.strip().title() or "Connecting"
    if s == "not running":
        return "Not running"
    if s == "can't connect":
        return "Can't connect"
    if "stopped" in s:
        return "Stopped"
    if "no internet" in s:
        return "No Internet"
    if "unreachable" in s:
        return "Unreachable"
    if "checking" in s:
        return "Checking"
    if s == "online":
        return "Online"
    if s == "offline":
        return "Offline"
    if "token expired" in s or (
        "token refresh" in s and ("fail" in s or "error" in s)
    ):
        return "Expired"
    if "all channels failed" in s:
        return "Error"
    if any(
        x in s
        for x in (
            "authentication failed",
            "auth failed",
            "authorization error",
            "authorization timeout",
        )
    ):
        return "Auth Failed"
    if "disconnected" in s:
        return "Disconnected"
    if "token refresh" in s:
        return "Connecting"
    if any(
        x in s
        for x in (
            "not connected",
            "not configured",
            "not initialized",
            "configured but",
            "authorization required",
            "authorization",
            "awaiting",
            "opening browser",
            "api key",
            "channel url",
        )
    ):
        return "Idle"
    if "error" in s or "fail" in s:
        return "Error"
    if len(status_raw) <= 12:
        return status_raw.strip()
    return status_raw.strip()[:12].rstrip() + "…"


def footer_status_tier(service_key: str, status_raw: str) -> str:
    """Map status to a footer color: success, info, muted, warning, or error.

    Offline is red for Internet and amber for other services (PSN presence).
    """
    display = footer_status_display(service_key, status_raw).lower()
    if display in ("connected", "running", "online"):
        return "success"
    if display in (
        "connecting",
        "disconnecting",
        "starting",
        "restarting",
        "reconnecting",
        "checking",
    ):
        return "info"
    if display in ("disconnected", "idle", "unknown", "not running"):
        return "muted"
    if display == "can't connect":
        return "warning"
    if display == "offline":
        return "error" if service_key == "internet" else "warning"
    if display in ("degraded", "stalled", "overloaded", "partial", "stopped"):
        return "warning"
    if display in (
        "error",
        "crashed",
        "frozen",
        "expired",
        "auth failed",
        "no internet",
        "unreachable",
    ):
        return "error"
    return "info"


@dataclass
class ServiceFooterEntry:
    key: str
    label: str
    status_raw: str
    display: str
    tier: str
    visible: bool


def iter_service_footer_entries() -> List[ServiceFooterEntry]:
    """Build footer row entries for all integration services."""
    entries: List[ServiceFooterEntry] = []
    for key in _SERVICE_KEYS:
        if key == "internet":
            visible = True
        elif key == "twitch":
            visible = True
        else:
            visible = _service_configured(key)
        if not visible:
            continue
        try:
            status_raw = _service_status_label(key)
        except Exception:
            logger.debug("footer status failed for %s", key, exc_info=True)
            status_raw = "Unknown"
        entries.append(
            ServiceFooterEntry(
                key=key,
                label=_SERVICE_LABELS[key],
                status_raw=status_raw,
                display=footer_status_display(key, status_raw),
                tier=footer_status_tier(key, status_raw),
                visible=True,
            )
        )
    return entries


_footer_container: Optional[Any] = None
_footer_item_refs: Dict[str, Dict[str, Any]] = {}
_trim_footer_refs: Dict[str, Any] = {}
_trim_footer_poll_started = False
_trim_footer_timer = None
_trim_footer_last_signature: Optional[tuple] = None
_copy_footer_refs: Dict[str, Any] = {}
_copy_footer_last_signature: Optional[tuple] = None
_footer_probe_scheduled = False
_footer_probe_refresh_pending = False
_footer_probe_running = False


def _run_integration_probe_background(*, force: bool) -> None:
    """Run service probes off the UI thread (never touch NiceGUI from here)."""
    global _footer_probe_running, _footer_probe_refresh_pending
    from .connection_status_tracker import probe_configured_services

    try:
        probe_configured_services(force=force)
    except Exception:
        logger.debug("background integration probe failed", exc_info=True)
    finally:
        _footer_probe_running = False
        _footer_probe_refresh_pending = True


def _refresh_footer_after_background_probe() -> None:
    """Poll on the UI thread until the background probe sets the refresh flag."""
    global _footer_probe_refresh_pending
    if _footer_probe_refresh_pending:
        _footer_probe_refresh_pending = False
        refresh_service_status_footer()
        return
    if _footer_probe_running:
        _app_schedule(0.15, _refresh_footer_after_background_probe, once=True)


def schedule_service_status_probe(*, force: bool = True, delay_seconds: float = 0.1) -> None:
    """Schedule a one-shot background probe (does not block UI construction)."""
    global _footer_probe_scheduled, _footer_probe_running
    if _footer_probe_scheduled and force:
        return
    if force:
        _footer_probe_scheduled = True

    def _start_probe() -> None:
        global _footer_probe_running
        _footer_probe_running = True
        threading.Thread(
            target=_run_integration_probe_background,
            kwargs={"force": force},
            daemon=True,
            name="ServiceStatusProbe",
        ).start()
        _app_schedule(0.15, _refresh_footer_after_background_probe, once=True)

    _app_schedule(delay_seconds, _start_probe, once=True)


def create_service_status_footer() -> None:
    """Mount the global connection status footer below main tab content."""
    global _footer_container, _footer_item_refs, _trim_footer_last_signature

    from nicegui import ui
    from .startup_profiler import StartupTimer
    from .uiwindows.service_brand_icons import SERVICE_BRAND_SVG

    with StartupTimer("create_service_status_footer.ui"):
        with ui.element("div").classes(
            "service-status-footer w-full shrink-0 flex-none"
        ) as footer:
            _footer_container = footer
            with ui.element("div").classes("service-status-footer-inner w-full"):
                for key in _SERVICE_KEYS:
                    svg = SERVICE_BRAND_SVG.get(key, "")
                    with ui.element("div").classes(
                        "service-status-item service-status-item--hidden "
                        "service-status-tier-info cursor-pointer select-none"
                    ) as item:
                        item.on(
                            "click",
                            lambda _e, k=key: _on_footer_item_click(k),
                        )
                        if svg:
                            ui.html(svg, tag="span", sanitize=False).classes(
                                "service-status-brand-icon"
                            )
                        ui.html(
                            _SERVICE_LABELS[key],
                            tag="span",
                            sanitize=False,
                        ).classes("service-status-name")
                        with ui.element("div").classes(
                            "service-status-status-cluster"
                        ):
                            dot = ui.element("span").classes(
                                "service-status-dot muted"
                            )
                            badge_wrap = ui.element("div").classes(
                                "service-status-badge info"
                            )
                            with badge_wrap:
                                badge_label = ui.html(
                                    "…", tag="span", sanitize=False
                                ).classes("service-status-badge-label")
                        _footer_item_refs[key] = {
                            "container": item,
                            "dot": dot,
                            "badge": badge_label,
                            "badge_wrap": badge_wrap,
                        }

                with ui.element("div").classes(
                    "service-status-item service-status-item--hidden "
                    "service-status-item--trim cursor-pointer select-none"
                ) as trim_item:
                    trim_item.on("click", _on_alert_trim_footer_click)
                    ui.html(
                        "Alert trim",
                        tag="span",
                        sanitize=False,
                    ).classes("service-status-name")
                    with ui.element("div").classes("service-status-status-cluster"):
                        trim_dot = ui.element("span").classes(
                            "service-status-dot muted"
                        )
                        trim_badge_wrap = ui.element("div").classes(
                            "service-status-badge info"
                        )
                        with trim_badge_wrap:
                            trim_badge_label = ui.html(
                                "…", tag="span", sanitize=False
                            ).classes("service-status-badge-label")
                    _trim_footer_refs.update(
                        {
                            "container": trim_item,
                            "dot": trim_dot,
                            "badge": trim_badge_label,
                            "badge_wrap": trim_badge_wrap,
                        }
                    )
                    _trim_footer_last_signature = None

                with ui.element("div").classes(
                    "service-status-item service-status-item--hidden "
                    "service-status-item--copy cursor-pointer select-none"
                ) as copy_item:
                    copy_item.on("click", _on_template_copy_footer_click)
                    copy_name = ui.html(
                        "Copying",
                        tag="span",
                        sanitize=False,
                    ).classes("service-status-name")
                    with ui.element("div").classes("service-status-status-cluster"):
                        copy_dot = ui.element("span").classes(
                            "service-status-dot muted"
                        )
                        copy_badge_wrap = ui.element("div").classes(
                            "service-status-badge info"
                        )
                        with copy_badge_wrap:
                            copy_badge_label = ui.html(
                                "…", tag="span", sanitize=False
                            ).classes("service-status-badge-label")
                    _copy_footer_refs.update(
                        {
                            "container": copy_item,
                            "name": copy_name,
                            "dot": copy_dot,
                            "badge": copy_badge_label,
                            "badge_wrap": copy_badge_wrap,
                        }
                    )
                    _copy_footer_last_signature = None

    _apply_footer_minimal_class()
    start_alert_trim_footer_poll()
    refresh_alert_trim_footer()
    refresh_template_copy_footer()
    schedule_service_status_probe(force=True)


def _on_footer_item_click(service_key: str) -> None:
    if service_key == "internet":
        return
    try:
        from .help_system.contextual_help import navigate_to_settings_subtab

        sub = _SERVICE_SUBTABS.get(service_key)
        if sub:
            navigate_to_settings_subtab(sub, main_tab="Settings")
    except Exception:
        logger.debug("footer navigate failed for %s", service_key, exc_info=True)


def _on_template_copy_footer_click(_e: Any = None) -> None:
    try:
        from .help_system.contextual_help import navigate_to_main_tab

        navigate_to_main_tab("Source Settings")
    except Exception:
        logger.debug("footer navigate failed for template copy", exc_info=True)


def _template_copy_footer_is_active() -> bool:
    try:
        from .template_copy_progress import template_copy_is_active

        return template_copy_is_active()
    except Exception:
        return False


def _footer_should_stay_open() -> bool:
    return (
        _status_footer_enabled()
        or _alert_trim_footer_is_active()
        or _template_copy_footer_is_active()
    )


def _on_alert_trim_footer_click(_e: Any = None) -> None:
    try:
        from .help_system.contextual_help import navigate_to_settings_subtab

        navigate_to_settings_subtab("App Settings", main_tab="Settings")
    except Exception:
        logger.debug("footer navigate failed for alert trim", exc_info=True)


def _alert_trim_footer_is_active() -> bool:
    try:
        from .alertutils import get_alert_storage_trim_progress

        return bool(get_alert_storage_trim_progress().get("active"))
    except Exception:
        return False


def _set_footer_container_hidden(hidden: bool) -> None:
    if _footer_container is None:
        return
    try:
        if hidden:
            _footer_container.classes(add="service-status-footer--hidden")
        else:
            _footer_container.classes(remove="service-status-footer--hidden")
    except Exception:
        pass


def refresh_alert_trim_footer() -> None:
    """Show or hide the Alert trim badge from shared progress (UI thread only)."""
    global _trim_footer_last_signature
    if not _trim_footer_refs:
        return

    formatted = None
    try:
        from .alertutils import format_trim_progress_badge, get_alert_storage_trim_progress

        progress = get_alert_storage_trim_progress()
        formatted = format_trim_progress_badge(progress)
    except Exception:
        progress = {"active": False, "phase": "idle", "to_delete": 0, "deleted": 0}

    try:
        signature = (
            bool(progress.get("active")),
            str(progress.get("phase") or ""),
            int(progress.get("to_delete") or 0),
            int(progress.get("deleted") or 0),
        )
    except (TypeError, ValueError):
        signature = (False, "idle", 0, 0)
        formatted = None

    if signature == _trim_footer_last_signature:
        return
    _trim_footer_last_signature = signature
    container = _trim_footer_refs.get("container")
    if container is None:
        return

    if formatted is None:
        try:
            container.classes(add="service-status-item--hidden")
        except Exception:
            pass
        if not _footer_should_stay_open():
            _set_footer_container_hidden(True)
        return

    badge_text, tier = formatted
    try:
        container.classes(remove="service-status-item--hidden")
    except Exception:
        pass
    _set_footer_container_hidden(False)
    try:
        dot = _trim_footer_refs.get("dot")
        if dot is not None:
            dot.classes(replace=f"service-status-dot {tier}")
    except Exception:
        pass
    try:
        badge = _trim_footer_refs.get("badge")
        if badge is not None:
            badge.set_content(badge_text)
        badge_wrap = _trim_footer_refs.get("badge_wrap")
        if badge_wrap is not None:
            badge_wrap.classes(replace=f"service-status-badge {tier}")
    except Exception:
        pass


def refresh_template_copy_footer() -> None:
    """Show or hide the template-copy badge from shared progress (UI thread only)."""
    global _copy_footer_last_signature
    if not _copy_footer_refs:
        return

    formatted = None
    progress: Dict[str, Any] = {
        "active": False,
        "phase": "idle",
        "copied": 0,
        "total": 0,
    }
    try:
        from .template_copy_progress import (
            format_template_copy_badge,
            get_template_copy_progress,
        )

        progress = get_template_copy_progress()
        formatted = format_template_copy_badge(progress)
    except Exception:
        formatted = None

    try:
        signature = (
            bool(progress.get("active")),
            str(progress.get("phase") or ""),
            int(progress.get("copied") or 0),
            int(progress.get("total") or 0),
            str(progress.get("label") or ""),
            str(progress.get("title") or ""),
        )
    except (TypeError, ValueError):
        signature = (False, "idle", 0, 0, "", "")
        formatted = None

    if signature == _copy_footer_last_signature:
        return
    _copy_footer_last_signature = signature
    container = _copy_footer_refs.get("container")
    if container is None:
        return

    if formatted is None:
        try:
            container.classes(add="service-status-item--hidden")
        except Exception:
            pass
        if not _footer_should_stay_open():
            _set_footer_container_hidden(True)
        return

    badge_text, tier = formatted
    try:
        container.classes(remove="service-status-item--hidden")
    except Exception:
        pass
    _set_footer_container_hidden(False)
    try:
        dot = _copy_footer_refs.get("dot")
        if dot is not None:
            dot.classes(replace=f"service-status-dot {tier}")
    except Exception:
        pass
    try:
        badge = _copy_footer_refs.get("badge")
        if badge is not None:
            badge.set_content(badge_text)
        badge_wrap = _copy_footer_refs.get("badge_wrap")
        if badge_wrap is not None:
            badge_wrap.classes(replace=f"service-status-badge {tier}")
        name_el = _copy_footer_refs.get("name")
        if name_el is not None:
            name_el.set_content(str(progress.get("title") or "Copying"))
        tip = str(progress.get("label") or "Copying template")
        _set_footer_item_tooltip(container, _copy_footer_refs, tip)
    except Exception:
        pass


def _footer_progress_active() -> bool:
    return _alert_trim_footer_is_active() or _template_copy_footer_is_active()


def poll_alert_trim_footer() -> None:
    if not _footer_progress_active():
        timer = globals().get("_trim_footer_timer")
        if timer is not None:
            try:
                timer.active = False
            except Exception:
                pass
        return
    refresh_alert_trim_footer()
    refresh_template_copy_footer()


def start_alert_trim_footer_poll() -> None:
    """Poll trim/copy progress on the UI loop only while that work is active."""
    global _trim_footer_poll_started, _trim_footer_timer
    if _trim_footer_timer is not None:
        try:
            _trim_footer_timer.active = True
        except Exception:
            pass
        return
    if _trim_footer_poll_started:
        return
    _trim_footer_poll_started = True
    _trim_footer_timer = _app_schedule(0.25, poll_alert_trim_footer, active=True)


def refresh_service_status_footer() -> None:
    """Update footer visibility and per-service badges (called from status poll)."""
    if _footer_container is None:
        return

    _apply_footer_minimal_class()
    _set_footer_container_hidden(not _footer_should_stay_open())

    if not _status_footer_enabled():
        refresh_alert_trim_footer()
        refresh_template_copy_footer()
        return

    entries = {e.key: e for e in iter_service_footer_entries()}
    for key in _SERVICE_KEYS:
        refs = _footer_item_refs.get(key)
        if not refs:
            continue
        entry = entries.get(key)
        container = refs["container"]
        if entry is None:
            try:
                container.classes(add="service-status-item--hidden")
            except Exception:
                pass
            continue
        try:
            container.classes(remove="service-status-item--hidden")
        except Exception:
            pass
        dot = refs["dot"]
        badge = refs["badge"]
        tier = entry.tier
        try:
            dot.classes(replace=f"service-status-dot {tier}")
        except Exception:
            pass
        try:
            badge.set_content(entry.display)
            badge_wrap = refs.get("badge_wrap")
            if badge_wrap is not None:
                badge_wrap.classes(replace=f"service-status-badge {tier}")
            _set_footer_item_tier(container, tier)
            _set_footer_item_tooltip(
                container, refs, _footer_item_tip(key, entry)
            )
        except Exception:
            pass

    refresh_alert_trim_footer()
    refresh_template_copy_footer()


def poll_service_status_changes() -> None:
    """Call from a UI timer; notify when a configured integration's status label changes."""
    global _footer_probe_refresh_pending
    if _footer_probe_refresh_pending:
        _footer_probe_refresh_pending = False
        refresh_service_status_footer()

    samples: Dict[str, Optional[str]] = {}
    for key in _SERVICE_KEYS:
        if key != "internet" and key != "twitch" and not _service_configured(key):
            _service_alerts.forget(key)
            continue
        try:
            samples[key] = _service_status_label(key)
        except Exception:
            logger.debug("service status poll failed for %s", key, exc_info=True)
    for notice in _service_alerts.observe(samples):
        actions = None
        if notice.settings_tab:
            actions = [{"kind": "navigate", "main_tab": "Settings", "settings_subtab": notice.settings_tab}]
        notify(
            notice.message,
            type=notice.type,
            source=notice.source,
            title=notice.title,
            hint=notice.hint,
            dedupe_key=notice.dedupe_key,
            dedupe_cooldown_sec=_SERVICE_STATUS_DEDUPE_COOLDOWN_SEC,
            actions=actions,
        )

    refresh_service_status_footer()


def poll_service_connection_changes() -> None:
    """Backward-compatible alias for :func:`poll_service_status_changes`."""
    poll_service_status_changes()


_connectivity_repoll_queued = False


def _poll_after_connectivity_refresh() -> None:
    global _connectivity_repoll_queued
    _connectivity_repoll_queued = False
    try:
        poll_service_status_changes()
    except Exception:
        logger.debug("status poll after connectivity refresh failed", exc_info=True)


def _on_connectivity_refreshed() -> None:
    """Connectivity probes finished on the background worker: re-poll on the UI loop.

    The status poll reads cached probe results and never probes inline; this runs the
    poll (notifications + footer) as soon as fresh results land, which is when the
    inline probe used to return.
    """
    global _connectivity_repoll_queued
    if not _service_watcher_started or _connectivity_repoll_queued:
        return
    _connectivity_repoll_queued = True
    _run_on_ui_loop(_poll_after_connectivity_refresh)


def start_service_watcher_timer() -> None:
    """Start periodic polling for integration status changes (after UI exists)."""
    global _service_watcher_started
    if _service_watcher_started:
        return
    _service_watcher_started = True
    try:
        from .connection_monitor import add_background_refresh_listener

        add_background_refresh_listener(_on_connectivity_refreshed)
    except Exception:
        logger.debug("connectivity refresh listener registration failed", exc_info=True)
    _app_schedule(2.0, poll_service_status_changes, active=True)
    start_alert_trim_footer_poll()
    flush_pending_toasts()
    if _pending_toasts:
        _ensure_toast_flush_timer()


def run_entry_action(entry_id: str, index: int) -> None:
    """Run action ``index`` of a history entry (pop-up and notification-centre buttons)."""
    entry = _find_entry(entry_id)
    if entry is None:
        return
    actions = _call_actions.get(entry_id) or entry.get("actions") or []
    if 0 <= index < len(actions):
        run_action(actions[index], message=str(entry.get("message") or ""), entry=entry)


def _on_toast_action_event(e: Any) -> None:
    args = getattr(e, "args", None) or {}
    if isinstance(args, dict) and args.get("id") is not None:
        try:
            run_entry_action(str(args["id"]), int(args.get("index", 0)))
        except (TypeError, ValueError):
            pass


def inject_notification_ui_assets() -> None:
    """Add the pop-up renderer to the page head and listen for its button clicks (once)."""
    global _notification_leave_script_injected, _toast_nav_handler_registered
    if _notification_leave_script_injected:
        return
    from nicegui import ui

    ui.add_head_html(_toast_head_html(), shared=True)
    _notification_leave_script_injected = True
    if not _toast_nav_handler_registered:
        ui.on("mycelian-toast-action", _on_toast_action_event)
        _toast_nav_handler_registered = True


def create_notification_tray_button() -> None:
    """The bell in the header (see :mod:`notify_center`)."""
    from .notify_center import create_notification_tray_button as build

    build()

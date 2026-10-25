# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""The New/Edit connector dialog: When (trigger) → If (conditions) → Then (actions).

Everything the dialog edits lives in one plain ``EditorState`` so the close guard can
compare snapshots, and saving builds a fresh ``Connector`` while keeping the fields the
dialog doesn't show (created_at, metadata, statistics, action ids). Action fields come
from ``connector_action_schemas``; variables, condition fields, triggers and test events
from ``connector_variables``.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import logging
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from nicegui import background_tasks, run, ui

from .. import connector_actions, connector_integration, connector_manager, connector_triggers
from .. import connector_variables as cv
from ..connector_core import ActionType, ComparisonOperator, Connector, TriggerCondition, TriggerType
from ..connector_placeholders import substitute_connector_placeholders
from ..notification_engine import nav_actions_settings, notify
from ..ui_timer import layout_schedule
from . import connector_action_schemas as schemas
from .connector_action_schemas import Field
from .connector_editor_styles import CONNECTOR_EDITOR_CSS, CONNECTOR_EDITOR_MENU_CSS
from .editor_variables import DEFAULT_DATE_FORMATS, VariableKit
from .editor_variables import esc as _esc
from .editor_variables import icon_html as _ic
from .editor_variables import raw_html as _html
from .service_brand_icons import SERVICE_BRAND_SVG

logger = logging.getLogger(__name__)

_TRIGGER_BRANDS = (("twitch_", "twitch"), ("youtube_", "youtube"), ("obs_", "obs"))
_TRIGGER_ICONS = {
    "donation": "volunteer_activism", "timer": "timer", "schedule": "event", "hotkey": "keyboard",
    "streamdeck": "grid_view", "webhook": "webhook", "any": "all_inclusive",
}
_GROUP_GLYPHS = {
    "Twitch": "twitch", "YouTube": "youtube", "Tips": "volunteer_activism", "OBS": "obs",
    "Controls": "keyboard", "Time": "timer", "Other": "webhook",
}
_DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_TIME_UNITS = {"sec": 1, "min": 60, "hr": 3600}
_HHMM = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def _glyph(key: str) -> str:
    svg = SERVICE_BRAND_SVG.get(key)
    return f'<span class="ce-brand is-{key}">{svg}</span>' if svg else _ic(key)


def _trigger_brand(trigger_type: Optional[str]) -> str:
    for prefix, brand in _TRIGGER_BRANDS:
        if (trigger_type or "").startswith(prefix):
            return brand
    return ""


def _trigger_glyph(trigger_type: Optional[str]) -> str:
    return _glyph(_trigger_brand(trigger_type) or _TRIGGER_ICONS.get(trigger_type or "", "bolt"))


def _trigger_info(trigger_type: Optional[str]) -> Dict[str, Any]:
    for entry in cv.TRIGGERS:
        if entry.get("value") == trigger_type:
            return entry
    return {"value": trigger_type, "label": (trigger_type or "").replace("_", " ").title(), "group": "Other", "hint": ""}


def _condition_fields(trigger_type: Optional[str]) -> List[Dict[str, Any]]:
    return list(cv.CONDITION_FIELDS.get(trigger_type or "", []) or [])


def _operators(kind: str) -> List[Tuple[str, str]]:
    return [tuple(op) for op in (cv.OPERATORS.get(kind) or cv.OPERATORS.get("text") or [])]


def _as_float(value: Any, default: Any = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        try:
            return float(default)
        except (TypeError, ValueError):
            return 0.0


def _hotkey_combo_from_event(event) -> str:
    """Turn a NiceGUI keyboard event into a ctrl+shift+f style combination."""
    action = getattr(event, "action", None)
    if getattr(action, "keyup", False) or getattr(action, "repeat", False):
        return ""
    key = str(getattr(getattr(event, "key", None), "name", "") or "").lower()
    if not key or key in ("control", "shift", "alt", "meta"):
        return ""
    modifiers = getattr(event, "modifiers", None)
    parts = [name for name, on in (
        ("ctrl", getattr(modifiers, "ctrl", False)), ("alt", getattr(modifiers, "alt", False)),
        ("shift", getattr(modifiers, "shift", False)), ("cmd", getattr(modifiers, "meta", False)),
    ) if on]
    parts.append(key)
    return normalize_hotkey("+".join(parts))


def normalize_hotkey(combo: str) -> str:
    try:
        from ..hotkey_listener import normalize_hotkey_combo

        return normalize_hotkey_combo(combo)
    except Exception:
        return "+".join(p.strip().lower() for p in str(combo or "").split("+") if p.strip())


def _new_secret() -> str:
    try:
        return cv.generate_webhook_secret()
    except Exception:
        return secrets.token_urlsafe(24)


def _webhook_url(connector_id: str, secret: str) -> str:
    try:
        return cv.webhook_url(connector_id, secret)
    except Exception:
        return f"http://127.0.0.1:5000/api/connectors/webhook/{connector_id}?key={secret}"


# ---------------------------------------------------------------------------
#  State
# ---------------------------------------------------------------------------


@dataclass
class EditorState:
    connector_id: str
    is_new: bool
    name: str = ""
    description: str = ""
    enabled: bool = True
    trigger_type: Optional[str] = None
    trigger: Dict[str, Any] = field(default_factory=dict)
    conditions: List[Dict[str, Any]] = field(default_factory=list)
    actions: List[Dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> str:
        actions = [{k: v for k, v in a.items() if k != "open"} for a in self.actions]
        data = {
            "name": self.name, "description": self.description, "enabled": self.enabled,
            "trigger_type": self.trigger_type, "trigger": self.trigger, "conditions": self.conditions, "actions": actions,
        }
        return json.dumps(data, sort_keys=True, default=str)


def _new_action(action_type: str) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    spec = schemas.spec_for(action_type)
    if spec is not None:
        schemas.apply_defaults(spec, cfg)
    return {"type": action_type, "config": cfg, "delay_seconds": 0.0, "enabled": True, "action_id": "",
            "description": "", "metadata": {}, "open": True}


def state_from_connector(connector: Optional[Connector], connector_id: Optional[str] = None) -> EditorState:
    """Editor state for an existing connector (or a blank one)."""
    if connector is None:
        return EditorState(connector_id=connector_id or str(uuid.uuid4()), is_new=True)
    trig = connector.trigger
    tt = getattr(getattr(trig, "trigger_type", None), "value", None)
    trigger_cfg: Dict[str, Any] = {"cooldown_seconds": float(getattr(trig, "cooldown_seconds", 0) or 0)}
    if tt == "hotkey":
        trigger_cfg["key_combination"] = normalize_hotkey(str(getattr(trig, "key_combination", "") or ""))
    elif tt == "timer":
        trigger_cfg["interval_seconds"] = int(getattr(trig, "interval_seconds", 60) or 60)
    elif tt == "schedule":
        trigger_cfg["specific_time"] = str(getattr(trig, "specific_time", "") or "")
        trigger_cfg["days_of_week"] = [int(d) for d in (getattr(trig, "days_of_week", None) or [])]
    elif tt == "webhook":
        trigger_cfg["secret_key"] = str(getattr(trig, "secret_key", "") or "")
    conditions = []
    for cond in getattr(trig, "conditions", None) or []:
        op = getattr(cond, "operator", None)
        value = getattr(cond, "value", "")
        conditions.append({
            "field": str(getattr(cond, "field", "") or ""),
            "operator": getattr(op, "value", op) or "equal",
            "value": value if isinstance(value, bool) else ("" if value is None else str(value)),
            "join": "or" if str(getattr(cond, "join", "and") or "and").lower() == "or" else "and",
            "ignore_case": not bool(getattr(cond, "case_sensitive", True)),
        })
    actions = []
    for action in connector.actions or []:
        at = getattr(getattr(action, "action_type", None), "value", None) or ""
        unreadable = False
        try:
            cfg = schemas.action_to_config(action)
        except Exception as exc:
            logger.warning("Could not read action %s for editing: %s", at, exc)
            cfg = {}
            unreadable = True
        spec = schemas.spec_for(at)
        if spec is not None and spec.fields and not spec.custom:
            schemas.apply_defaults(spec, cfg)
        entry = {
            "type": at, "config": cfg, "delay_seconds": float(getattr(action, "delay_seconds", 0) or 0),
            "enabled": bool(getattr(action, "enabled", True)), "action_id": getattr(action, "action_id", "") or "",
            "description": getattr(action, "description", "") or "", "metadata": dict(getattr(action, "metadata", None) or {}),
            "open": False,
        }
        if unreadable:
            # The form shows defaults; build_connector keeps the stored action
            # unless the user changes this config (no silent reset on save).
            entry["unreadable"] = True
            entry["loaded_config"] = copy.deepcopy(cfg)
        actions.append(entry)
    if actions and len(actions) == 1:
        actions[0]["open"] = True
    return EditorState(
        connector_id=connector.connector_id, is_new=False, name=connector.name or "", description=connector.description or "",
        enabled=bool(connector.enabled), trigger_type=tt, trigger=trigger_cfg, conditions=conditions, actions=actions,
    )


def _coerce_condition_value(kind: str, value: Any) -> Any:
    if kind == "bool":
        return value if isinstance(value, bool) else str(value).strip().lower() in ("true", "1", "yes", "on")
    if kind == "number":
        try:
            num = float(str(value).strip())
        except (TypeError, ValueError):
            return value
        return int(num) if num.is_integer() else num
    return "" if value is None else str(value)


def _field_kind(trigger_type: Optional[str], key: str) -> str:
    for f in _condition_fields(trigger_type):
        if f.get("key") == key:
            return f.get("type") or "text"
    return "text"


def state_problems(state: EditorState, context: Optional[dict] = None) -> List[str]:
    """Reasons the connector can't be saved yet, most important first."""
    problems: List[str] = []
    if not state.name.strip():
        problems.append("Name this connector.")
    tt = state.trigger_type
    if not tt:
        problems.append("Choose a trigger.")
    elif tt == "hotkey" and not str(state.trigger.get("key_combination") or "").strip():
        problems.append("Set the hotkey.")
    elif tt == "schedule" and not _HHMM.match(str(state.trigger.get("specific_time") or "")):
        problems.append("Set the time the schedule runs (HH:MM).")
    elif tt == "timer" and int(state.trigger.get("interval_seconds") or 0) < 1:
        problems.append("Set how often the timer runs.")
    for n, cond in enumerate(state.conditions, 1):
        if not cond.get("field"):
            problems.append(f"Condition {n}: choose a field.")
            continue
        kind = _field_kind(tt, cond["field"])
        value = cond.get("value")
        if kind == "number" and not isinstance(_coerce_condition_value("number", value), (int, float)):
            problems.append(f"Condition {n}: enter a number.")
        elif kind == "text" and str(value or "") == "" and cond.get("operator") not in ("equal", "not_equal"):
            problems.append(f"Condition {n}: enter a value.")
    if not state.actions:
        problems.append("Add at least one action.")
    for n, action in enumerate(state.actions, 1):
        for msg in schemas.config_problems(action.get("type"), action.get("config") or {}):
            problems.append(f"Action {n}: {msg}")
        spec = schemas.spec_for(action.get("type"))
        if spec is not None:
            for f in schemas.visible_fields(spec, action.get("config") or {}):
                if f.kind == "json":
                    err = schemas.json_error((action.get("config") or {}).get(f.key), context)
                    if err:
                        problems.append(f"Action {n}: {f.label}: {err}")
    return problems


def build_connector(state: EditorState, existing: Optional[Connector] = None) -> Connector:
    """A ``Connector`` from the editor state, keeping fields the editor doesn't show."""
    tt = TriggerType(state.trigger_type)
    old_trigger = getattr(existing, "trigger", None)
    same_type = getattr(getattr(old_trigger, "trigger_type", None), "value", None) == state.trigger_type
    cond_fields = {f.name for f in dataclasses.fields(TriggerCondition)}
    conditions = []
    for cond in state.conditions:
        kind = _field_kind(state.trigger_type, cond["field"])
        kwargs: Dict[str, Any] = {
            "field": cond["field"], "operator": ComparisonOperator(cond.get("operator") or "equal"),
            "value": _coerce_condition_value(kind, cond.get("value")),
        }
        if "case_sensitive" in cond_fields:
            kwargs["case_sensitive"] = not cond.get("ignore_case", False)
        if "join" in cond_fields:
            kwargs["join"] = cond.get("join") or "and"
        conditions.append(TriggerCondition(**kwargs))
    tcfg = state.trigger
    trigger_kwargs: Dict[str, Any] = {"conditions": conditions, "cooldown_seconds": max(0.0, float(tcfg.get("cooldown_seconds") or 0))}
    if same_type and isinstance(getattr(old_trigger, "metadata", None), dict):
        trigger_kwargs["metadata"] = dict(old_trigger.metadata)
    if tt == TriggerType.HOTKEY:
        trigger_kwargs["key_combination"] = normalize_hotkey(tcfg.get("key_combination") or "")
    elif tt == TriggerType.TIMER:
        trigger_kwargs["interval_seconds"] = max(1, int(tcfg.get("interval_seconds") or 60))
    elif tt == TriggerType.SCHEDULE:
        trigger_kwargs["specific_time"] = str(tcfg.get("specific_time") or "")
        trigger_kwargs["days_of_week"] = sorted({int(d) for d in tcfg.get("days_of_week") or []})
    elif tt == TriggerType.WEBHOOK:
        trigger_kwargs["secret_key"] = str(tcfg.get("secret_key") or "")
    elif tt == TriggerType.STREAMDECK:
        trigger_kwargs["connector_id"] = state.connector_id
    trigger_id = getattr(old_trigger, "trigger_id", None) or str(uuid.uuid4())
    trigger = connector_triggers.create_trigger(tt, trigger_id, f"{state.name} Trigger", **trigger_kwargs)

    stored_actions = {getattr(a, "action_id", ""): a for a in (getattr(existing, "actions", None) or [])}
    actions = []
    for n, action in enumerate(state.actions, 1):
        at = action["type"]
        original = stored_actions.get(action.get("action_id") or "")
        if (
            action.get("unreadable")
            and original is not None
            and getattr(getattr(original, "action_type", None), "value", None) == at
            and (action.get("config") or {}) == action.get("loaded_config")
        ):
            # Couldn't be shown in the form and wasn't changed: keep it exactly as stored.
            actions.append(dataclasses.replace(
                original, delay_seconds=max(0.0, float(action.get("delay_seconds") or 0)),
                enabled=bool(action.get("enabled", True)), description=action.get("description") or "",
            ))
            continue
        kwargs = schemas.config_to_kwargs(at, action.get("config") or {})
        actions.append(connector_actions.create_action(
            ActionType(at), action.get("action_id") or str(uuid.uuid4()), f"{state.name} Action {n}",
            delay_seconds=max(0.0, float(action.get("delay_seconds") or 0)), enabled=bool(action.get("enabled", True)),
            description=action.get("description") or "", metadata=dict(action.get("metadata") or {}), **kwargs,
        ))
    return Connector(
        connector_id=state.connector_id, name=state.name.strip(), description=state.description.strip(),
        enabled=state.enabled, trigger=trigger, actions=actions,
        created_at=getattr(existing, "created_at", None) or time.time(),
        last_triggered=getattr(existing, "last_triggered", 0) or 0, trigger_count=getattr(existing, "trigger_count", 0) or 0,
        metadata=copy.deepcopy(getattr(existing, "metadata", None) or {}),
    )


def run_connector_test(connector_id: str) -> None:
    """Fire a saved connector once with a realistic sample event for its trigger."""
    try:
        manager = connector_manager.get_manager()
        connector = manager.get_connector(connector_id)
        if not connector:
            notify("Save the connector first, then test it.", type="info", source="connectors")
            return
        if not connector.trigger:
            notify("Choose a trigger and save the connector before testing.", type="info", source="connectors")
            return
        if not connector.enabled:
            notify("Turn the connector on to test it.", type="info", source="connectors")
            return
        data = cv.sample_event(connector.trigger.trigger_type.value, trigger=connector.trigger)
    except Exception as exc:
        logger.error("Error preparing connector test: %s", exc, exc_info=True)
        notify("", type="error", source="connectors", title="Couldn't build a sample event for this trigger",
               hint="Check the trigger settings, save, and try again.", details=f"{type(exc).__name__}: {exc}")
        return

    async def go() -> None:
        result = await manager.test_connector(connector_id, data)
        failures = result.get("failures") or []
        if not result.get("success"):
            notify("", type="error", source="connectors", title="The connector test couldn't run",
                   hint="Try again. If it keeps happening, restart Mycelian.",
                   details=str(result.get("error") or "") or None)
        elif failures:
            lines = [f"Action {f['index']} ({f['label']})" + (f": {f['reason']}" if f.get("reason") else "") for f in failures]
            hints = [f["hint"] for f in failures if f.get("hint")]
            details = "\n\n".join(
                f"Action {f['index']} ({f['label']}):\n{f.get('details') or 'no details'}" for f in failures
            )
            count = len(failures)
            notify(
                "; ".join(lines) + ".",
                type="warning",
                source="connectors",
                title=f"Test ran, but {count} action{'s' if count != 1 else ''} didn't work",
                hint=hints[0] if hints else "Check the settings of the action(s) listed, then test again.",
                details=details,
            )
        elif result.get("triggered"):
            count = result.get("action_count", 0)
            notify(f"Test ran {count} action{'s' if count != 1 else ''} with sample data", type="success", source="connectors")
        else:
            notify("The sample event didn't pass the conditions, so nothing ran.", type="info", source="connectors",
                   title="Test didn't run")

    background_tasks.create(go(), name="connector_test")


# ---------------------------------------------------------------------------
#  Dialog
# ---------------------------------------------------------------------------


class _LogCapture(logging.Handler):
    """Collect warnings/errors one logger writes during a call (for "Copy details")."""

    def __init__(self, logger_name: str) -> None:
        super().__init__(level=logging.WARNING)
        self._logger = logging.getLogger(logger_name)
        self.lines: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if len(self.lines) < 8:
            self.lines.append(f"{record.levelname}: {record.getMessage()}")

    def __enter__(self) -> "_LogCapture":
        self._logger.addHandler(self)
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._logger.removeHandler(self)


class ConnectorEditor:
    def __init__(self, connector_id: Optional[str], on_saved: Callable[[], None]):
        self.on_saved = on_saved
        self.existing: Optional[Connector] = None
        if connector_id:
            self.existing = connector_manager.get_manager().get_connector(connector_id)
        self.state = state_from_connector(self.existing, connector_id)
        unreadable = [(n, a.get("type") or "action") for n, a in enumerate(self.state.actions, 1) if a.get("unreadable")]
        if unreadable:
            listed = ", ".join(f"action {n} ({t.replace('_', ' ')})" for n, t in unreadable)
            notify(
                f"The editor shows default settings for {listed}. Saving keeps the stored settings unless you change them.",
                type="warning",
                source="connectors",
                title="Some action settings couldn't be read",
                hint="Leave these actions as they are to keep them. If you change them, check every field before saving.",
            )
        self._baseline = ""
        self._live: List[Callable[[], None]] = []
        self._picking_trigger = self.state.trigger_type is None
        self._picking_action = False
        self._ctx_cache: Tuple[Any, dict] = (None, {})
        self._obs_bootstrapped = False
        self._template_actions: Optional[dict] = None
        self.dialog: Optional[ui.dialog] = None
        self.vars = VariableKit(self._variables, self.render_text, self._date_formats)

    # -- helpers ---------------------------------------------------------

    def _context(self) -> dict:
        key = (self.state.trigger_type, self.state.name)
        if self._ctx_cache[0] != key:
            count = int(getattr(self.existing, "trigger_count", 0) or 0)
            try:
                ctx = cv.example_context(self.state.trigger_type or "", self.state.name or "My connector", count)
            except Exception as exc:
                logger.debug("example_context failed: %s", exc)
                ctx = {}
            self._ctx_cache = (key, ctx)
        return self._ctx_cache[1]

    def render_text(self, text: Any) -> str:
        text = "" if text is None else str(text)
        try:
            return substitute_connector_placeholders(text, self._context())
        except Exception:
            return text

    def _variables(self, action_index: Optional[int] = None, extra: Optional[List[dict]] = None) -> List[dict]:
        out = [dict(v) for v in getattr(cv, "ALWAYS_VARIABLES", [])]
        if self.state.trigger_type:
            out = [dict(v, group=v.get("group") or "Event") for v in cv.EVENT_VARIABLES.get(self.state.trigger_type, [])] + out
        if action_index:
            for j, action in enumerate(self.state.actions[:action_index], 1):
                if action.get("type") == "game_hook":
                    for key, label in (("item_name", "item name"), ("quantity", "quantity"), ("resolved_name", "resolved name"), ("error", "error")):
                        out.append({"key": f"action{j}.{key}", "label": f"Action {j} {label}", "example": "", "group": "Earlier actions"})
        out.extend(extra or [])
        return out

    def _date_formats(self) -> List[dict]:
        return list(getattr(cv, "DATE_FORMATS", None) or DEFAULT_DATE_FORMATS)

    def _dirty(self) -> bool:
        return self.state.snapshot() != self._baseline

    # -- open / close ----------------------------------------------------

    def open(self) -> ui.dialog:
        from ..help_system.help_render import inject_page_stylesheet, promote_color_importance, scope_selectors

        inject_page_stylesheet("mycelian-connector-editor", promote_color_importance(scope_selectors(CONNECTOR_EDITOR_CSS, ".cxe")))
        inject_page_stylesheet("mycelian-connector-editor-menus", CONNECTOR_EDITOR_MENU_CSS)
        self.dialog = ui.dialog().props("persistent")
        with self.dialog, ui.card().classes("cxe"):
            self._build_header()
            with ui.element("div").classes("ce-body"):
                self.flow = ui.element("div").classes("ce-flow")
                self.vars_panel = ui.element("div").classes("ce-vars")
            self._build_footer()
        self.render_flow()
        self.render_vars()
        self._baseline = self.state.snapshot()
        self.dialog.open()
        return self.dialog

    def request_close(self) -> None:
        if not self._dirty():
            self.dialog.close()
            return
        with ui.dialog().props("persistent") as confirm, ui.card().classes("w-[440px] p-4"):
            ui.label("Unsaved changes").classes("text-lg font-bold mb-1")
            ui.label("Close this connector without saving? Your edits will be lost.").classes("secondary-text mb-3")

            def discard() -> None:
                confirm.close()
                self.dialog.close()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Keep editing", on_click=confirm.close).props("outline no-caps")
                ui.button("Discard", on_click=discard).props("color=negative unelevated no-caps")
        confirm.open()

    # -- header / footer -------------------------------------------------

    def _build_header(self) -> None:
        s = self.state
        with ui.element("div").classes("ce-head"):
            _html(f'<span class="ce-tile">{_ic("hub")}</span>')
            with ui.element("div").classes("ce-head-text"):
                with ui.element("div").classes("ce-name-row"):
                    ui.label("New" if s.is_new else "Edit").classes("ce-kicker")
                    ui.input(value=s.name, placeholder="Name this connector",
                             on_change=lambda e: self._set("name", e.value or "")).props("borderless dense").classes("ce-name-input")
                ui.input(value=s.description, placeholder="Add a short description (optional)",
                         on_change=lambda e: self._set("description", e.value or "")).props("borderless dense").classes("ce-desc-input")
            with ui.element("div").classes("ce-onoff"):
                onoff = ui.label("On" if s.enabled else "Off")

                def toggle(e) -> None:
                    s.enabled = bool(e.value)
                    onoff.set_text("On" if s.enabled else "Off")

                ui.switch(value=s.enabled, on_change=toggle).props("dense").tooltip("Turn the connector on or off")
            ui.button(icon="close", on_click=self.request_close).props("flat round dense").tooltip("Close")

    def _build_footer(self) -> None:
        with ui.element("div").classes("ce-foot"):
            with ui.element("div").classes("ce-status") as self.status_box:
                self.status_icon = _html(_ic("check_circle"))
                self.status_label = ui.label("")
            ui.element("div").classes("ce-spacer")
            ui.button("Save & test", icon="play_arrow", on_click=lambda: self.save(test_after=True)).props(
                "outline no-caps color=primary").tooltip("Save, then run it once with sample data")
            ui.button("Cancel", on_click=self.request_close).props("outline no-caps color=primary")
            ui.button("Create connector" if self.state.is_new else "Save", icon="check", on_click=lambda: self.save()).props(
                "unelevated no-caps color=primary")

    def _set(self, key: str, value: Any) -> None:
        setattr(self.state, key, value)
        self.refresh_live()

    # -- live updates ----------------------------------------------------

    def refresh_live(self) -> None:
        self._live = [fn for fn in self._live if fn() is not False]
        problems = state_problems(self.state, self._context())
        self.status_box.classes(remove="is-ok is-warn", add="is-warn" if problems else "is-ok")
        if problems:
            more = f"  (+{len(problems) - 1} more)" if len(problems) > 1 else ""
            self.status_icon.set_content(_ic("error_outline"))
            self.status_label.set_text(problems[0] + more)
        else:
            self.status_icon.set_content(_ic("check_circle"))
            self.status_label.set_text("Ready to save")

    # -- flow ------------------------------------------------------------

    def render_flow(self) -> None:
        self._live = []
        self.flow.clear()
        with self.flow:
            self._step(1, "When", "Trigger", "What starts this connector", self._build_trigger)
            self.cond_step = self._step(2, "If", "Conditions",
                                        "Optional. Leave empty to run every time." if self.state.trigger_type else "Choose a trigger first",
                                        self._build_conditions, tag=lambda: self._count(len(self.state.conditions), "condition"))
            self._step(3, "Then", "Actions", "Run top to bottom", self._build_actions,
                       tag=lambda: self._count(len(self.state.actions), "action"))
        self.refresh_live()

    @staticmethod
    def _count(n: int, word: str) -> str:
        return f"{n} {word}{'s' if n != 1 else ''}" if n else ""

    def _step(self, n: int, key: str, title: str, sub: str, body: Callable[[ui.element], None],
              tag: Optional[Callable[[], str]] = None) -> ui.element:
        with ui.element("section").classes("ce-step"):
            _html(f'<div class="ce-step-rail"><span class="ce-step-n">{n}</span></div>', "contents")
            with ui.element("div").classes("ce-step-main"):
                with ui.element("div").classes("ce-step-head"):
                    ui.label(key).classes("ce-step-key")
                    ui.label(title).classes("ce-step-title")
                    ui.label(sub).classes("ce-step-sub")
                    ui.element("div").classes("ce-spacer")
                    if tag is not None:
                        tag_label = ui.label("").classes("ce-tag")

                        def upd() -> bool:
                            if tag_label.is_deleted:
                                return False
                            text = tag()
                            tag_label.set_text(text)
                            tag_label.set_visibility(bool(text))
                            return True

                        upd()
                        self._live.append(upd)
                container = ui.element("div").classes("ce-stack")
                with container:
                    body(container)
        return container

    # -- trigger ---------------------------------------------------------

    def _build_trigger(self, container: ui.element) -> None:
        self.trigger_box = container
        if self._picking_trigger or not self.state.trigger_type:
            self._build_trigger_picker()
            return
        tt = self.state.trigger_type
        info = _trigger_info(tt)
        tcfg = self.state.trigger
        with ui.element("div").classes("ce-trig"):
            brand = _trigger_brand(tt)
            _html(f'<span class="ce-tile{" is-" + brand if brand else ""}">{_trigger_glyph(tt)}</span>')
            with ui.element("div").classes("ce-trig-text"):
                ui.label(info.get("label") or tt).classes("ce-trig-name")
                if info.get("hint"):
                    ui.label(info["hint"]).classes("ce-trig-hint")
            with ui.element("div").classes("ce-trig-opt").tooltip("Ignore new events for this long after the connector runs"):
                _html(_ic("timer"))
                ui.label("Cooldown")
                ui.number(value=tcfg.get("cooldown_seconds") or 0, min=0, step=1, format="%g",
                          on_change=lambda e: self._set_trigger("cooldown_seconds", float(e.value or 0))).props(
                    "outlined dense").style("width: 70px")
                ui.label("sec").classes("ce-unit")
            ui.button("Change", icon="swap_horiz", on_click=self._start_trigger_pick).props("outline no-caps dense color=primary")
        if info.get("warning"):
            _html(f'<div class="ce-warnline">{_ic("warning_amber")}<span>{_esc(info["warning"])}</span></div>', "")
        settings = getattr(self, f"_trigger_settings_{tt}", None)
        if settings is not None:
            settings(tcfg)

    def _set_trigger(self, key: str, value: Any) -> None:
        self.state.trigger[key] = value
        self.refresh_live()

    def _start_trigger_pick(self) -> None:
        self._picking_trigger = True
        self._rerender_trigger()

    def _rerender_trigger(self) -> None:
        self.trigger_box.clear()
        with self.trigger_box:
            self._build_trigger(self.trigger_box)
        self.refresh_live()

    def _build_trigger_picker(self) -> None:
        current = self.state.trigger_type
        with ui.element("div").classes("ce-picker"):
            with ui.element("div").classes("ce-picker-head"):
                ui.label("Choose a trigger").classes("ce-step-title")
                ui.element("div").classes("ce-spacer")
                search = ui.input(placeholder="Search triggers").props("outlined dense clearable autofocus")
                with search.add_slot("prepend"):
                    ui.icon("search").classes("text-sm")
                if current:
                    ui.button("Cancel", on_click=self._cancel_trigger_pick).props("flat no-caps dense color=primary")
            groups_box = ui.element("div").classes("ce-stack")

        def fill(query: str = "") -> None:
            q = query.strip().lower()
            groups: Dict[str, List[dict]] = {}
            for entry in cv.TRIGGERS:
                text = f"{entry.get('label', '')} {entry.get('hint', '')} {entry.get('group', '')}".lower()
                if not q or q in text:
                    groups.setdefault(entry.get("group") or "Other", []).append(entry)
            groups_box.clear()
            with groups_box:
                if not groups:
                    ui.label("No triggers match.").classes("ce-step-sub")
                for group, entries in groups.items():
                    with ui.element("div").classes("ce-pgroup"):
                        _html(f'<div class="ce-pgroup-head">{_glyph(_GROUP_GLYPHS.get(group, "bolt"))}<span>{_esc(group)}</span></div>', "")
                        with ui.element("div").classes("ce-pgrid"):
                            for entry in entries:
                                opt = ui.element("div").classes("ce-popt" + (" is-selected" if entry["value"] == current else ""))
                                with opt:
                                    ui.label(entry.get("label", entry["value"])).classes("ce-popt-title")
                                    if entry.get("hint"):
                                        ui.label(entry["hint"]).classes("ce-popt-hint")
                                    if entry.get("warning"):
                                        ui.label("May not fire").classes("ce-popt-warn")
                                opt.on("click", lambda _e, v=entry["value"]: self._choose_trigger(v))

        search.on_value_change(lambda e: fill(e.value or ""))
        fill()

    def _cancel_trigger_pick(self) -> None:
        self._picking_trigger = False
        self._rerender_trigger()

    def _choose_trigger(self, trigger_type: str) -> None:
        s = self.state
        old = s.trigger_type
        self._picking_trigger = False
        if trigger_type != old:
            allowed = {f.get("key") for f in _condition_fields(trigger_type)}
            kept = [c for c in s.conditions if c.get("field") in allowed]
            dropped = len(s.conditions) - len(kept)
            s.conditions = kept
            s.trigger_type = trigger_type
            s.trigger = {"cooldown_seconds": s.trigger.get("cooldown_seconds", 0)}
            if trigger_type == "timer":
                s.trigger["interval_seconds"] = 300
            elif trigger_type == "webhook":
                s.trigger["secret_key"] = _new_secret()
            elif trigger_type == "schedule":
                s.trigger.update({"specific_time": "", "days_of_week": []})
            elif trigger_type == "hotkey":
                s.trigger["key_combination"] = ""
            if dropped:
                notify(f"Removed {dropped} condition{'s' if dropped != 1 else ''} that don't apply to this trigger.",
                       type="warning", source="connectors",
                       hint="Close without saving to keep them, or add new conditions for this trigger.")
        self.render_flow()
        self.render_vars()

    # trigger-specific settings ------------------------------------------

    def _trigger_settings_hotkey(self, tcfg: dict) -> None:
        with ui.element("div").classes("ce-trig-settings"):
            ui.label("Keys").classes("ce-f-label")
            combo = ui.input(value=tcfg.get("key_combination", ""), placeholder="ctrl+shift+b").props("outlined dense").style("width: 220px")
            ui.button("Record", icon="radio_button_checked", on_click=lambda: self._record_hotkey(combo)).props(
                "outline no-caps dense color=primary")
            conflict = ui.label("").classes("ce-warnline")

            def changed(e) -> None:
                tcfg["key_combination"] = (e.value or "").strip()
                conflict.set_text(self._hotkey_conflict(tcfg["key_combination"]))
                self.refresh_live()

            def tidy() -> None:
                normalized = normalize_hotkey(combo.value or "")
                if normalized and normalized != combo.value:
                    combo.value = normalized

            combo.on_value_change(changed)
            combo.on("blur", tidy)
            conflict.set_text(self._hotkey_conflict(tcfg.get("key_combination", "")))
        _html(f'<div class="ce-note">{_ic("info")}<span>Works anywhere on this computer, even when Mycelian is in the background. '
              'Combine keys with +, e.g. ctrl+alt+f9.</span></div>', "")

    def _hotkey_conflict(self, combo: str) -> str:
        combo = normalize_hotkey(combo)
        if not combo:
            return ""
        names = []
        try:
            for cid, other in connector_manager.get_manager().get_all_connectors().items():
                if cid == self.state.connector_id:
                    continue
                if normalize_hotkey(getattr(getattr(other, "trigger", None), "key_combination", "") or "") == combo:
                    names.append(getattr(other, "name", cid) or cid)
        except Exception:
            return ""
        return ("Also used by: " + ", ".join(names) + " (both will run)") if names else ""

    def _record_hotkey(self, combo_input: ui.input) -> None:
        with ui.dialog() as rec, ui.card().classes("w-[380px] p-4 items-center"):
            ui.label("Press the key combination").classes("text-lg font-bold")
            ui.label("Hold the modifiers, then press the key. Esc cancels.").classes("secondary-text text-sm")

            def on_key(event) -> None:
                if str(getattr(getattr(event, "key", None), "name", "")).lower() == "escape":
                    rec.close()
                    return
                combo = _hotkey_combo_from_event(event)
                if combo:
                    combo_input.value = combo
                    rec.close()

            keyboard = ui.keyboard(on_key=on_key, ignore=[])
            ui.button("Cancel", on_click=rec.close).props("outline no-caps")
        # ui.keyboard listens page-wide; switch it off with the dialog so later typing isn't recorded.
        rec.on_value_change(lambda e: setattr(keyboard, "active", bool(e.value)))
        rec.open()

    def _trigger_settings_timer(self, tcfg: dict) -> None:
        seconds = int(tcfg.get("interval_seconds") or 300)
        unit = "hr" if seconds % 3600 == 0 else ("min" if seconds % 60 == 0 else "sec")
        holder = {"unit": unit}
        with ui.element("div").classes("ce-trig-settings"):
            ui.label("Run every").classes("ce-f-label")
            amount = ui.number(value=seconds // _TIME_UNITS[unit], min=1, step=1, format="%g").props("outlined dense").style("width: 90px")
            unit_sel = ui.select({"sec": "seconds", "min": "minutes", "hr": "hours"}, value=unit).props("outlined dense options-dense").style("width: 120px")

            def changed(_e=None) -> None:
                holder["unit"] = unit_sel.value or "sec"
                tcfg["interval_seconds"] = max(1, int(float(amount.value or 1) * _TIME_UNITS[holder["unit"]]))
                self.refresh_live()

            amount.on_value_change(changed)
            unit_sel.on_value_change(changed)
        _html(f'<div class="ce-note">{_ic("info")}<span>Starts counting when Mycelian starts. Turn the connector off to pause it.</span></div>', "")

    def _trigger_settings_schedule(self, tcfg: dict) -> None:
        days = tcfg.setdefault("days_of_week", [])
        with ui.element("div").classes("ce-trig-settings"):
            ui.label("At").classes("ce-f-label")
            ui.input(value=tcfg.get("specific_time", ""), on_change=lambda e: self._set_trigger("specific_time", (e.value or "")[:5])).props(
                "outlined dense type=time").style("width: 130px")
            ui.label("On").classes("ce-f-label")
            with ui.element("div").classes("ce-days"):
                for idx, name in enumerate(_DAY_NAMES):
                    chip = ui.label(name).classes("ce-day" + (" is-on" if idx in days else ""))

                    def flip(_e, i=idx, c=chip) -> None:
                        if i in days:
                            days.remove(i)
                            c.classes(remove="is-on")
                        else:
                            days.append(i)
                            days.sort()
                            c.classes(add="is-on")
                        self.refresh_live()

                    chip.on("click", flip)
        _html(f'<div class="ce-note">{_ic("info")}<span>Runs once at this local time. No days picked means every day.</span></div>', "")

    def _trigger_settings_webhook(self, tcfg: dict) -> None:
        if not tcfg.get("secret_key"):
            tcfg["secret_key"] = _new_secret()
        with ui.element("div").classes("ce-trig-settings"):
            ui.label("URL").classes("ce-f-label")
            url_label = ui.label(_webhook_url(self.state.connector_id, tcfg["secret_key"])).classes("ce-code").style("flex: 1 1 320px")

            def copy() -> None:
                ui.clipboard.write(url_label.text)
                notify("Webhook URL copied", type="success", source="connectors")

            def regenerate() -> None:
                tcfg["secret_key"] = _new_secret()
                url_label.set_text(_webhook_url(self.state.connector_id, tcfg["secret_key"]))
                self.refresh_live()

            ui.button("Copy", icon="content_copy", on_click=copy).props("outline no-caps dense color=primary")
            ui.button("New key", icon="autorenew", on_click=regenerate).props("flat no-caps dense color=primary").tooltip(
                "Make a new secret key. The old URL stops working after you save.")
        _html(f'<div class="ce-note">{_ic("info")}<span>Send a POST with a JSON body (or a GET with query parameters) to this URL. '
              'Each field becomes a variable, so <code>{"amount": 5}</code> gives you <code>{amount}</code>. Keep the key private; '
              'the URL only works on this computer\'s network.</span></div>', "")

    def _trigger_settings_streamdeck(self, _tcfg: dict) -> None:
        _html(f'<div class="ce-note">{_ic("info")}<span>In the Stream Deck app, add the Mycelian <b>Connector</b> action and pick '
              f'“{_esc(self.state.name or "this connector")}”. Connector ID: <code>{_esc(self.state.connector_id)}</code></span></div>', "")

    def _trigger_settings_any(self, _tcfg: dict) -> None:
        _html(f'<div class="ce-warnline">{_ic("warning_amber")}<span>Runs for every event from every service, including each chat message. '
              'Add a condition on <b>Event type</b> to narrow it down.</span></div>', "")

    # -- conditions ------------------------------------------------------

    def _build_conditions(self, container: ui.element) -> None:
        self.cond_box = container
        tt = self.state.trigger_type
        if not tt:
            ui.label("Conditions depend on the trigger.").classes("ce-empty")
            return
        fields = _condition_fields(tt)
        if not fields and not self.state.conditions:
            ui.label("This trigger has nothing to check, so it always runs.").classes("ce-empty")
            return
        if self.state.conditions:
            with ui.element("div").classes("ce-conds"):
                _html('<div class="ce-cond is-head"><span></span><span>Field</span><span>Is</span><span>Value</span><span></span></div>', "")
                for idx, cond in enumerate(self.state.conditions):
                    if idx and cond.get("join") == "or":
                        _html('<div class="ce-or-div"><span>or</span></div>', "")
                    self._condition_row(idx, cond, fields)
                summary = ui.element("div").classes("ce-summary")
                with summary:
                    _html(_ic("info"))
                    summary_html = ui.html("", sanitize=False)

                def upd() -> bool:
                    if summary_html.is_deleted:
                        return False
                    summary_html.set_content(self._condition_summary())
                    return True

                upd()
                self._live.append(upd)
        if fields:
            add = ui.element("div").classes("ce-add")
            with add:
                _html(_ic("add"))
                ui.label("Add condition")
            add.on("click", self._add_condition)

    def _rerender_conditions(self) -> None:
        self.cond_box.clear()
        with self.cond_box:
            self._build_conditions(self.cond_box)
        self.refresh_live()

    def _add_condition(self) -> None:
        fields = _condition_fields(self.state.trigger_type)
        if not fields:
            return
        first = fields[0]
        kind = first.get("type") or "text"
        self.state.conditions.append({
            "field": first["key"], "operator": _operators(kind)[0][0], "value": True if kind == "bool" else "",
            "join": "and", "ignore_case": True,
        })
        self._rerender_conditions()

    def _condition_row(self, idx: int, cond: dict, fields: List[dict]) -> None:
        field_opts = {f["key"]: f.get("label") or f["key"] for f in fields}
        if cond.get("field") and cond["field"] not in field_opts:
            field_opts[cond["field"]] = f"{cond['field']} (not available)"
        kind = _field_kind(self.state.trigger_type, cond.get("field", ""))
        ops = dict(_operators(kind))
        if cond.get("operator") not in ops:
            cond["operator"] = next(iter(ops))
        with ui.element("div").classes("ce-cond"):
            if idx == 0:
                ui.label("where").classes("ce-cond-word")
            else:
                join = ui.element("div").classes("ce-join").tooltip("Switch between AND (all must match) and OR (either group can match)")
                with join:
                    ui.label("AND").classes("is-on" if cond.get("join") != "or" else "")
                    ui.label("OR").classes("is-on" if cond.get("join") == "or" else "")

                def flip_join(_e, c=cond) -> None:
                    c["join"] = "and" if c.get("join") == "or" else "or"
                    self._rerender_conditions()

                join.on("click", flip_join)

            def set_field(e, c=cond) -> None:
                c["field"] = e.value
                new_kind = _field_kind(self.state.trigger_type, e.value)
                c["operator"] = _operators(new_kind)[0][0]
                c["value"] = True if new_kind == "bool" else ""
                self._rerender_conditions()

            ui.select(field_opts, value=cond.get("field") or None, on_change=set_field).props("outlined dense options-dense")
            ui.select(ops, value=cond.get("operator"), on_change=lambda e, c=cond: self._cond_set(c, "operator", e.value)).props(
                "outlined dense options-dense")
            if kind == "bool":
                ui.select({True: "true", False: "false"}, value=bool(cond.get("value")) if isinstance(cond.get("value"), bool)
                          else _coerce_condition_value("bool", cond.get("value")),
                          on_change=lambda e, c=cond: self._cond_set(c, "value", bool(e.value))).props("outlined dense options-dense")
            else:
                with ui.element("div").classes("ce-inline"):
                    value_input = ui.input(value=str(cond.get("value") if cond.get("value") is not None else ""),
                                           placeholder="number" if kind == "number" else "text",
                                           on_change=lambda e, c=cond: self._cond_set(c, "value", e.value or "")).props("outlined dense")
                    if kind == "number":
                        value_input.props("inputmode=decimal")
                    else:
                        case = ui.label("Aa").classes("ce-case" + (" is-on" if cond.get("ignore_case") else ""))
                        case.tooltip("Ignore upper/lower case")

                        def flip_case(_e, c=cond, el=case) -> None:
                            c["ignore_case"] = not c.get("ignore_case")
                            el.classes(add="is-on") if c["ignore_case"] else el.classes(remove="is-on")
                            self.refresh_live()

                        case.on("click", flip_case)
            ui.button(icon="close", on_click=lambda _e, i=idx: self._remove_condition(i)).props("flat round dense").classes(
                "is-danger").tooltip("Remove condition")

    def _cond_set(self, cond: dict, key: str, value: Any) -> None:
        cond[key] = value
        self.refresh_live()

    def _remove_condition(self, idx: int) -> None:
        if 0 <= idx < len(self.state.conditions):
            self.state.conditions.pop(idx)
            if self.state.conditions:
                self.state.conditions[0]["join"] = "and"
        self._rerender_conditions()

    def _condition_text(self, cond: dict) -> str:
        fields = {f["key"]: f.get("label") or f["key"] for f in _condition_fields(self.state.trigger_type)}
        kind = _field_kind(self.state.trigger_type, cond.get("field", ""))
        op_label = dict(_operators(kind)).get(cond.get("operator"), cond.get("operator", ""))
        value = cond.get("value")
        if kind == "text":
            value_text = f"“{value}”"
        elif kind == "bool":
            value_text = "true" if _coerce_condition_value("bool", value) else "false"
        else:
            value_text = str(value) if str(value).strip() else "…"
        return f"{fields.get(cond.get('field'), cond.get('field') or '…')} {op_label} {value_text}"

    def _condition_summary(self) -> str:
        groups: List[List[str]] = [[]]
        for idx, cond in enumerate(self.state.conditions):
            if idx and cond.get("join") == "or":
                groups.append([])
            groups[-1].append(_esc(self._condition_text(cond)))
        parts = [f"<b>{' and '.join(g)}</b>" for g in groups if g]
        return "<span>Runs when " + ", or when ".join(parts) + ".</span>"

    # -- actions ---------------------------------------------------------

    def _build_actions(self, container: ui.element) -> None:
        self.actions_box = container
        self._action_refs: Dict[int, Dict[str, Any]] = {}
        with ui.element("div").classes("ce-actions"):
            for idx in range(len(self.state.actions)):
                self._action_card(idx)
        if self._picking_action:
            self._build_action_picker()
        else:
            add = ui.element("div").classes("ce-add")
            with add:
                _html(_ic("add"))
                ui.label("Add action")
            add.on("click", self._start_action_pick)

    def _rerender_actions(self) -> None:
        self.actions_box.clear()
        with self.actions_box:
            self._build_actions(self.actions_box)
        self.refresh_live()

    def _start_action_pick(self) -> None:
        self._picking_action = True
        self._rerender_actions()

    def _build_action_picker(self) -> None:
        with ui.element("div").classes("ce-picker"):
            with ui.element("div").classes("ce-picker-head"):
                ui.label("Add an action").classes("ce-step-title")
                ui.element("div").classes("ce-spacer")
                search = ui.input(placeholder="Search actions").props("outlined dense clearable autofocus")
                with search.add_slot("prepend"):
                    ui.icon("search").classes("text-sm")

                def cancel() -> None:
                    self._picking_action = False
                    self._rerender_actions()

                ui.button("Cancel", on_click=cancel).props("flat no-caps dense color=primary")
            box = ui.element("div").classes("ce-stack")

        def fill(query: str = "") -> None:
            q = query.strip().lower()
            box.clear()
            with box:
                shown = False
                for group, icon, specs in schemas.grouped_specs():
                    matches = [s for s in specs if not q or q in f"{s.label} {s.hint} {group}".lower()]
                    if not matches:
                        continue
                    shown = True
                    with ui.element("div").classes("ce-pgroup"):
                        _html(f'<div class="ce-pgroup-head">{_glyph(icon)}<span>{_esc(group)}</span></div>', "")
                        with ui.element("div").classes("ce-pgrid"):
                            for spec in matches:
                                opt = ui.element("div").classes("ce-popt")
                                with opt:
                                    ui.label(spec.label).classes("ce-popt-title")
                                    if spec.hint:
                                        ui.label(spec.hint).classes("ce-popt-hint")
                                opt.on("click", lambda _e, v=spec.value: self._add_action(v))
                if not shown:
                    ui.label("No actions match.").classes("ce-step-sub")

        search.on_value_change(lambda e: fill(e.value or ""))
        fill()

    def _add_action(self, action_type: str) -> None:
        for action in self.state.actions:
            action["open"] = False
        self.state.actions.append(_new_action(action_type))
        self._picking_action = False
        self._rerender_actions()

    def _action_title(self, action: dict) -> str:
        spec = schemas.spec_for(action.get("type"))
        return spec.label if spec else (action.get("type") or "Unknown action").replace("_", " ").title()

    def _action_labels(self, action: dict) -> Dict[str, str]:
        at = action.get("type")
        labels: Dict[str, str] = {}
        if at == "trigger_alert":
            labels.update(schemas._alert_type_options({}))
        elif at == "obs_control":
            try:
                from ..obs_connector_catalog import OBS_CONNECTOR_CATALOG

                labels.update({c["id"]: c["label"] for c in OBS_CONNECTOR_CATALOG})
            except Exception:
                pass
        elif at == "game_hook":
            try:
                from ..game_hooks.ff7_hook import FF7_CONNECTOR_CATALOG

                labels.update({c["id"]: c["label"] for c in FF7_CONNECTOR_CATALOG})
            except Exception:
                pass
        elif at == "template_control":
            tpl = (action.get("config") or {}).get("template_name")
            for aid, acfg in (self._templates().get(tpl) or {}).items():
                labels[aid] = acfg.get("action_name", aid)
        return labels

    def _action_card(self, idx: int) -> None:
        action = self.state.actions[idx]
        at = action.get("type")
        spec = schemas.spec_for(at)
        brand = (spec.icon if spec else "play_arrow")
        card = ui.element("div").classes("ce-action")
        refs: Dict[str, Any] = {"card": card}
        self._action_refs[idx] = refs
        with card:
            with ui.element("div").classes("ce-action-head"):
                ui.label(str(idx + 1)).classes("ce-action-n")
                tile = _html(f'<span class="ce-tile is-action">{_glyph(brand)}</span>', "")
                text = ui.element("div").classes("ce-action-text")
                with text:
                    ui.label(self._action_title(action)).classes("ce-action-title")
                    summary = ui.label("").classes("ce-action-sum")
                for el in (tile, text):
                    el.on("click", lambda _e, i=idx: self._toggle_action(i))
                delay = _html("", "")

                def upd(a=action, s=summary, d=delay, c=card) -> bool:
                    if s.is_deleted:
                        return False
                    cfg = a.get("config") or {}
                    problems = schemas.config_problems(a.get("type"), cfg)
                    s.set_text(problems[0] if problems and not a.get("open") else
                               schemas.summarize(a.get("type"), cfg, self.render_text, self._action_labels(a)))
                    s.classes(add="is-warn") if problems and not a.get("open") else s.classes(remove="is-warn")
                    secs = float(a.get("delay_seconds") or 0)
                    d.set_content(f'<span class="ce-delay">{_ic("schedule")}wait {secs:g} s</span>' if secs else "")
                    c.classes(add="is-invalid") if problems else c.classes(remove="is-invalid")
                    return True

                self._live.append(upd)
                upd()
                ui.switch(value=bool(action.get("enabled", True)), on_change=lambda e, i=idx: self._set_action_enabled(i, e.value)).props(
                    "dense").tooltip("Run this action")
                ui.button(icon="arrow_upward", on_click=lambda _e, i=idx: self._move_action(i, -1)).props(
                    "flat round dense" + (" disable" if idx == 0 else "")).tooltip("Move up")
                ui.button(icon="arrow_downward", on_click=lambda _e, i=idx: self._move_action(i, 1)).props(
                    "flat round dense" + (" disable" if idx == len(self.state.actions) - 1 else "")).tooltip("Move down")
                ui.button(icon="content_copy", on_click=lambda _e, i=idx: self._duplicate_action(i)).props("flat round dense").tooltip("Duplicate")
                ui.button(icon="delete", on_click=lambda _e, i=idx: self._remove_action(i)).props("flat round dense").classes(
                    "is-danger").tooltip("Remove")
                ui.button(icon="expand_less" if action.get("open") else "expand_more",
                          on_click=lambda _e, i=idx: self._toggle_action(i)).props("flat round dense")
            body = ui.element("div").classes("ce-action-body")
            refs["body"] = body
        card.classes(add="is-open" if action.get("open") else "")
        if not action.get("enabled", True):
            card.classes(add="is-off")
        if action.get("open"):
            with body:
                self._action_body(idx)
        else:
            body.set_visibility(False)

    def _toggle_action(self, idx: int) -> None:
        action = self.state.actions[idx]
        action["open"] = not action.get("open")
        self._rerender_actions()

    def _set_action_enabled(self, idx: int, value: bool) -> None:
        self.state.actions[idx]["enabled"] = bool(value)
        card = self._action_refs.get(idx, {}).get("card")
        if card is not None:
            card.classes(remove="is-off") if value else card.classes(add="is-off")
        self.refresh_live()

    def _move_action(self, idx: int, step: int) -> None:
        dest = idx + step
        acts = self.state.actions
        if 0 <= dest < len(acts):
            acts[idx], acts[dest] = acts[dest], acts[idx]
            self._rerender_actions()

    def _duplicate_action(self, idx: int) -> None:
        clone = copy.deepcopy(self.state.actions[idx])
        clone["action_id"] = ""
        for action in self.state.actions:
            action["open"] = False
        clone["open"] = True
        self.state.actions.insert(idx + 1, clone)
        self._rerender_actions()

    def _remove_action(self, idx: int) -> None:
        if 0 <= idx < len(self.state.actions):
            self.state.actions.pop(idx)
        self._rerender_actions()

    def _rerender_action_body(self, idx: int) -> None:
        body = self._action_refs.get(idx, {}).get("body")
        if body is None:
            return
        body.clear()
        with body:
            self._action_body(idx)
        self.refresh_live()

    def _action_body(self, idx: int) -> None:
        action = self.state.actions[idx]
        at = action.get("type")
        cfg = action.setdefault("config", {})
        spec = schemas.spec_for(at)
        if spec is None:
            _html(f'<div class="ce-warnline">{_ic("warning_amber")}<span>Unknown action type “{_esc(at)}”. '
                  'It is kept as is; remove it if it is no longer needed.</span></div>', "")
            return
        if spec.warning:
            _html(f'<div class="ce-warnline">{_ic("warning_amber")}<span>{_esc(spec.warning)}</span></div>', "")
        if spec.fields:
            schemas.apply_defaults(spec, cfg)
            with ui.element("div").classes("ce-fields"):
                for f in schemas.visible_fields(spec, cfg):
                    self._field(idx, f, cfg)
        custom = getattr(self, f"_custom_{at}", None)
        if custom is not None:
            custom(idx, cfg)
        with ui.element("div").classes("ce-f-row").style("gap: 8px; margin-top: 2px"):
            ui.label("Wait").classes("ce-f-label")
            ui.number(value=action.get("delay_seconds") or 0, min=0, step=0.5, format="%g",
                      on_change=lambda e, a=action: self._action_set(a, "delay_seconds", float(e.value or 0))).props(
                "outlined dense").style("width: 80px; flex: 0 0 auto")
            ui.label("seconds before this action runs").classes("ce-unit")

    def _action_set(self, action: dict, key: str, value: Any) -> None:
        action[key] = value
        self.refresh_live()

    # fields --------------------------------------------------------------

    def _field(self, idx: int, f: Field, cfg: dict, on_change: Optional[Callable[[Field, Any], None]] = None,
               extra_vars: Optional[List[dict]] = None) -> None:
        width = {"half": " is-half", "third": " is-third"}.get(f.width, "")
        cfg.setdefault(f.key, list(f.default) if isinstance(f.default, list) else f.default)

        def changed(value: Any) -> None:
            cfg[f.key] = value
            for key in f.clears:
                cfg.pop(key, None)
            if on_change is not None:
                on_change(f, value)
            elif f.rerender:
                self._rerender_action_body(idx)
            else:
                self.refresh_live()

        with ui.element("div").classes("ce-f" + width):
            if f.kind != "toggle":
                ui.label(f.label).classes("ce-f-label")
            if f.kind in ("text", "textarea", "json"):
                self._text_field(idx, f, cfg, changed, extra_vars)
            elif f.kind in ("number", "slider"):
                with ui.element("div").classes("ce-f-row"):
                    if f.kind == "slider":
                        slider = ui.slider(min=f.min or 0, max=f.max or 100, step=f.step or 1, value=_as_float(cfg.get(f.key), f.default)).props(
                            "dense label").style("flex: 1 1 auto")
                        value_label = ui.label(f"{float(slider.value or 0):g}{f.unit}").classes("ce-unit").style(
                            "min-width: 40px; text-align: right")
                        slider.on_value_change(lambda e: (changed(e.value), value_label.set_text(f"{float(e.value or 0):g}{f.unit}")))
                    else:
                        ui.number(value=_as_float(cfg.get(f.key), f.default), min=f.min, max=f.max, step=f.step or 1, format="%g",
                                  placeholder=f.placeholder or None,
                                  on_change=lambda e: changed(e.value if e.value is not None else f.default)).props("outlined dense")
                        if f.unit:
                            ui.label(f.unit).classes("ce-unit")
            elif f.kind == "select":
                opts = f.option_map(cfg)
                current = cfg.get(f.key)
                if current not in (None, "") and str(current) not in opts:
                    opts = {**opts, current: f"{current} (not found)"}
                if not opts:
                    opts = {"": "(nothing available)"}
                ui.select(opts, value=current if current in opts else next(iter(opts)), with_input=len(opts) > 12,
                          on_change=lambda e: changed(e.value)).props("outlined dense options-dense")
            elif f.kind == "toggle":
                ui.switch(f.label, value=bool(cfg.get(f.key)), on_change=lambda e: changed(bool(e.value))).props("dense")
            elif f.kind == "targets":
                self._targets_field(cfg, f, changed)
            elif f.kind == "macro":
                self._macro_field(cfg, f, changed)
            if f.help:
                ui.label(f.help).classes("ce-f-help")

    def _text_field(self, idx: int, f: Field, cfg: dict, changed: Callable[[Any], None], extra_vars: Optional[List[dict]]) -> None:
        area = f.kind in ("textarea", "json")
        host = ui.element("div").classes("ce-varhost" + (" is-area" if area else "")) if f.variables else ui.element("div")
        with host:
            value = cfg.get(f.key)
            text = "" if value is None else (value if isinstance(value, str) else json.dumps(value))
            if area:
                if f.kind == "json":
                    # Fixed rows: Quasar's autogrow measures before the dialog lays out and stays one line tall.
                    rows = min(max(text.count("\n") + 1, 3), 12)
                    inp = ui.textarea(value=text, placeholder=f.placeholder or None).props(f"outlined dense rows={rows}")
                    inp.style("font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace")
                else:
                    inp = ui.textarea(value=text, placeholder=f.placeholder or None).props("outlined dense autogrow")
            else:
                inp = ui.input(value=text, placeholder=f.placeholder or None).props("outlined dense")
            if f.variables:
                self.vars.button(inp, idx, extra_vars)
                self.vars.track(inp)
        error = ui.label("").classes("ce-f-error") if f.kind == "json" else None
        preview = None
        if f.preview:
            with ui.element("div").classes("ce-preview") as preview:
                _html(_ic("visibility"))
                preview_label = ui.label("")

        def refresh() -> bool:
            if inp.is_deleted:
                return False
            if preview is not None:
                rendered = self.render_text(cfg.get(f.key) or "")
                preview.set_visibility(bool(rendered.strip()) and "{" in str(cfg.get(f.key) or ""))
                preview_label.set_text(rendered)
            if error is not None:
                err = schemas.json_error(cfg.get(f.key), self._context())
                error.set_text(err)
                error.set_visibility(bool(err))
            return True

        inp.on_value_change(lambda e: changed(e.value or ""))
        refresh()
        self._live.append(refresh)

    def _targets_field(self, cfg: dict, f: Field, changed: Callable[[Any], None]) -> None:
        targets = list(cfg.get(f.key) or [])
        with ui.element("div").classes("ce-pills"):
            for key, label in (("twitch", "Twitch"), ("youtube", "YouTube")):
                pill = ui.element("div").classes("ce-pill" + (" is-on" if key in targets else ""))
                with pill:
                    _html(_glyph(key))
                    ui.label(label)

                def flip(_e, k=key, p=pill) -> None:
                    if k in targets:
                        targets.remove(k)
                        p.classes(remove="is-on")
                    else:
                        targets.append(k)
                        p.classes(add="is-on")
                    changed([t for t in ("twitch", "youtube") if t in targets])

                pill.on("click", flip)

    def _macro_field(self, cfg: dict, f: Field, changed: Callable[[Any], None]) -> None:
        try:
            steps = json.loads(cfg.get(f.key) or "[]")
            if not isinstance(steps, list):
                steps = []
        except (TypeError, ValueError):
            steps = []
        if not steps:
            steps.append({"type": "key", "target": "", "delay": 0.1})
            cfg[f.key] = json.dumps(steps)
        box = ui.element("div").classes("flex flex-col gap-1")

        def save() -> None:
            changed(json.dumps(steps))

        def render() -> None:
            box.clear()
            with box:
                for n, step in enumerate(steps):
                    with ui.element("div").classes("ce-macro"):
                        ui.label(str(n + 1)).classes("ce-macro-n")

                        def set_type(e, s=step) -> None:
                            s["type"] = e.value
                            s["target"] = {"delay": "1.0", "mouse": "left_click"}.get(e.value, "")
                            save()
                            render()

                        ui.select({"key": "Keys", "mouse": "Click", "delay": "Wait"}, value=step.get("type", "key"),
                                  on_change=set_type).props("outlined dense options-dense")
                        if step.get("type") == "mouse":
                            ui.select({"left_click": "Left click", "right_click": "Right click", "middle_click": "Middle click"},
                                      value=step.get("target") or "left_click",
                                      on_change=lambda e, s=step: (s.update(target=e.value), save())).props("outlined dense options-dense")
                        else:
                            ui.input(value=str(step.get("target", "")),
                                     placeholder="seconds" if step.get("type") == "delay" else "ctrl+c, enter, space",
                                     on_change=lambda e, s=step: (s.update(target=e.value or ""), save())).props("outlined dense")
                        if step.get("type") != "delay":
                            with ui.element("div").classes("ce-f-row"):
                                ui.number(value=float(step.get("delay", 0.1) or 0), min=0, step=0.05, format="%g",
                                          on_change=lambda e, s=step: (s.update(delay=float(e.value or 0)), save())).props("outlined dense")
                                ui.label("s").classes("ce-unit")
                        else:
                            ui.element("div")
                        ui.button(icon="close", on_click=lambda _e, i=n: remove(i)).props(
                            "flat round dense" + (" disable" if len(steps) == 1 else "")).classes("is-danger")
                add = ui.element("div").classes("ce-add").style("height: 28px")
                with add:
                    _html(_ic("add"))
                    ui.label("Add step")
                add.on("click", add_step)

        def add_step() -> None:
            steps.append({"type": "key", "target": "", "delay": 0.1})
            save()
            render()

        def remove(i: int) -> None:
            if len(steps) > 1:
                steps.pop(i)
                save()
                render()

        render()

    # variables -----------------------------------------------------------

    def render_vars(self) -> None:
        tt = self.state.trigger_type
        self.vars.panel(
            self.vars_panel, tag=_trigger_info(tt).get("label", tt) if tt else "",
            empty_html="" if tt else "Choose a trigger to see the variables you can use in actions, like "
                                     "<code>{username}</code> or <code>{amount}</code>.",
        )

    # custom action builders ---------------------------------------------

    def _custom_send_discord_message(self, idx: int, cfg: dict) -> None:
        from .. import discord_service

        channels = cfg.setdefault("channels", [])
        ui.label("Channels").classes("ce-f-label")
        chips = ui.element("div").classes("ce-pills")

        def key_of(entry: dict) -> str:
            return f"{entry.get('guild_id')}:{entry.get('channel_id')}"

        def render_chips() -> None:
            chips.clear()
            with chips:
                if not channels:
                    ui.label("No channels yet").classes("ce-f-help")
                for entry in list(channels):
                    with ui.element("div").classes("ce-chip"):
                        ui.label(f"{entry.get('guild_name') or entry.get('guild_id')} / #{entry.get('channel_name') or entry.get('channel_id')}")
                        ui.button(icon="close", on_click=lambda _e, k=key_of(entry): remove(k)).props("flat round dense size=xs")

        def remove(key: str) -> None:
            channels[:] = [e for e in channels if key_of(e) != key]
            render_chips()
            self.refresh_live()

        guilds: Dict[str, str] = {}
        try:
            if discord_service.discord_service.is_connected():
                guilds = {g["id"]: g.get("name") or g["id"] for g in discord_service.list_guilds()}
        except Exception:
            guilds = {}
        channel_opts: Dict[str, str] = {}
        with ui.element("div").classes("ce-f-row"):
            guild_sel = ui.select(guilds, label=None, with_input=True).props('outlined dense options-dense placeholder="Server"').style("width: 200px")
            chan_sel = ui.select({}, with_input=True).props('outlined dense options-dense placeholder="Channel"').style("width: 200px")

            def on_guild(e) -> None:
                channel_opts.clear()
                if e.value:
                    try:
                        channel_opts.update({c["id"]: f"#{c.get('name') or c['id']}" for c in discord_service.list_text_channels(e.value)})
                    except Exception:
                        pass
                chan_sel.set_options(dict(channel_opts), value=None)

            def add() -> None:
                gid, chid = guild_sel.value, chan_sel.value
                if not gid or not chid:
                    notify("Choose a server and a channel first", type="warning", source="connectors", hint="Pick both, then click Add.")
                    return
                entry = {"guild_id": gid, "channel_id": chid, "guild_name": guilds.get(gid, gid),
                         "channel_name": channel_opts.get(chid, chid).lstrip("#")}
                if any(key_of(e) == key_of(entry) for e in channels):
                    notify("That channel is already added", type="info", source="connectors")
                    return
                channels.append(entry)
                render_chips()
                self.refresh_live()

            guild_sel.on_value_change(on_guild)
            ui.button("Add", icon="add", on_click=add).props("outline no-caps dense color=primary")
        if not guilds:
            ui.label("Connect Discord in Settings to list your servers and channels.").classes("ce-f-help")
        render_chips()

    def _templates(self) -> dict:
        if self._template_actions is None:
            try:
                self._template_actions = connector_integration.get_integration().get_available_template_actions() or {}
            except Exception as exc:
                logger.debug("Template actions unavailable: %s", exc)
                self._template_actions = {}
        return self._template_actions

    def _custom_template_control(self, idx: int, cfg: dict) -> None:
        templates = self._templates()
        tpl = cfg.get("template_name") or ""
        tpl_opts = {name: name.replace("_", " ").title() for name in templates}
        if tpl and tpl not in tpl_opts:
            tpl_opts[tpl] = f"{tpl} (not found)"
        actions = templates.get(tpl) or {}
        act_opts = {aid: acfg.get("action_name", aid) for aid, acfg in actions.items()}
        act = cfg.get("control_action") or ""
        if act and act not in act_opts:
            act_opts[act] = f"{act} (not found)"

        def set_template(e) -> None:
            for key in list(cfg):
                del cfg[key]
            cfg["template_name"] = e.value or ""
            first = next(iter(templates.get(e.value) or {}), "")
            cfg["control_action"] = first
            self._rerender_action_body(idx)

        def set_action(e) -> None:
            keep = cfg.get("template_name")
            cfg.clear()
            cfg.update({"template_name": keep, "control_action": e.value or ""})
            self._rerender_action_body(idx)

        with ui.element("div").classes("ce-fields"):
            with ui.element("div").classes("ce-f is-half"):
                ui.label("Overlay").classes("ce-f-label")
                ui.select(tpl_opts or {"": "(no overlays found)"}, value=tpl or None, on_change=set_template).props("outlined dense options-dense")
            with ui.element("div").classes("ce-f is-half"):
                ui.label("Do").classes("ce-f-label")
                ui.select(act_opts or {"": "(choose an overlay)"}, value=act or None, on_change=set_action).props("outlined dense options-dense")
            for element in (actions.get(act) or {}).get("elements", []) or []:
                f = self._template_element_field(element)
                if f is None:
                    if element.get("type") == "separator" and element.get("label"):
                        ui.label(element["label"]).classes("ce-vgroup").style("grid-column: span 6")
                    continue
                if f.key not in cfg and f.default is not None:
                    cfg[f.key] = f.default
                if f.kind == "select":
                    opts = f.option_map(cfg)
                    if opts and str(cfg.get(f.key)) not in opts and cfg.get(f.key) in ("", None):
                        cfg[f.key] = next(iter(opts))
                self._field(idx, f, cfg)

    @staticmethod
    def _template_element_field(element: dict) -> Optional[Field]:
        et, eid = element.get("type"), element.get("id")
        if not eid or et not in ("text", "number", "select", "switch"):
            return None
        label = element.get("label") or eid
        help_text = element.get("description") or ""
        if et == "text":
            return Field(eid, label, "text", default=element.get("value", "") or "", variables=True, help=help_text,
                         placeholder=element.get("placeholder", ""))
        if et == "number":
            return Field(eid, label, "number", default=element.get("value", 0) or 0, min=element.get("min"), max=element.get("max"),
                         width="third", help=help_text)
        if et == "select":
            raw = element.get("options") or []
            opts = {str(o): str(o).replace("_", " ").title() for o in raw} if isinstance(raw, list) else {str(k): str(v) for k, v in raw.items()}
            return Field(eid, label, "select", default=str(element.get("value", "")) if element.get("value") is not None else "",
                         options=opts, width="half", help=help_text)
        return Field(eid, label, "toggle", default=bool(element.get("value", False)), help=help_text)

    def _custom_obs_control(self, idx: int, cfg: dict) -> None:
        from ..obs_connector_catalog import OBS_CONNECTOR_CATALOG
        from ..obs_service import obs_service

        ops = {c["id"]: c["label"] for c in OBS_CONNECTOR_CATALOG}
        if not cfg.get("operation"):
            cfg["operation"] = next(iter(ops))
        op = cfg["operation"]
        if op not in ops:
            ops[op] = f"{op} (not found)"
        entry = next((c for c in OBS_CONNECTOR_CATALOG if c["id"] == op), None)
        try:
            snap = obs_service.get_connector_snapshot() or {}
            connected = bool(obs_service.connection_details()[0])
        except Exception:
            snap, connected = {}, False

        def set_op(e) -> None:
            for key in [k for k in cfg if k.startswith("arg_")]:
                del cfg[key]
            cfg["operation"] = e.value
            self._rerender_action_body(idx)

        def refresh_lists() -> None:
            async def job() -> None:
                ok, err = await run.io_bound(obs_service.refresh_snapshot_blocking, 25.0)
                if not ok and err:
                    notify("The scene and source lists may be out of date.", type="warning", source="obs",
                           title="Couldn't load scenes and sources from OBS",
                           hint="Check that OBS is running and connected in Settings → OBS, then refresh again.",
                           details=str(err), actions=nav_actions_settings("OBS"))
                self._rerender_action_body(idx)

            layout_schedule(0, job, once=True)

        with ui.element("div").classes("ce-fields"):
            with ui.element("div").classes("ce-f is-half"):
                ui.label("OBS action").classes("ce-f-label")
                with ui.element("div").classes("ce-f-row"):
                    ui.select(ops, value=op, with_input=True, on_change=set_op).props("outlined dense options-dense")
                    ui.button(icon="refresh", on_click=refresh_lists).props("flat round dense").tooltip("Reload scenes and sources from OBS")
            for arg in (entry or {}).get("args", []):
                key = f"arg_{arg['name']}"
                if arg.get("control") == "select":
                    dynamic = arg.get("dynamic")
                    if dynamic == "scene":
                        opts = {n: n for n in snap.get("scene_names") or []}
                    elif dynamic == "input":
                        opts = {n: n for n in snap.get("input_names") or []}
                    elif dynamic == "scene_item":
                        scene = str(cfg.get(f"arg_{arg.get('scene_arg') or 'scene_name'}") or "")
                        opts = dict((snap.get("sources_by_scene") or {}).get(scene) or {})
                    else:
                        opts = dict(arg.get("options") or {})
                    f = Field(key, arg.get("label", arg["name"]), "select", default="", options=opts, width="half",
                              strict=not dynamic, rerender=True,
                              help="" if opts or not dynamic else ("Nothing loaded yet. Press reload." if connected
                                                                   else "OBS isn't connected (Settings → OBS)."))
                    if dynamic and opts and cfg.get(key) in (None, ""):
                        cfg[key] = next(iter(opts))
                    elif not dynamic and opts and str(cfg.get(key)) not in opts:
                        cfg[key] = next(iter(opts))
                else:
                    f = Field(key, arg.get("label", arg["name"]), "text", default=str(arg.get("default", "") or ""), variables=True, width="half")
                self._field(idx, f, cfg)
        if entry and entry.get("description"):
            ui.label(entry["description"]).classes("ce-f-help")
        if not self._obs_bootstrapped:
            self._obs_bootstrapped = True

            async def bootstrap() -> None:
                try:
                    await run.io_bound(obs_service.refresh_snapshot_blocking, 25.0)
                except Exception as exc:
                    logger.debug("OBS list refresh: %s", exc)
                    return
                if idx < len(self.state.actions) and self.state.actions[idx].get("type") == "obs_control" and self.state.actions[idx].get("open"):
                    self._rerender_action_body(idx)

            layout_schedule(0, bootstrap, once=True)

    def _custom_game_hook(self, idx: int, cfg: dict) -> None:
        from ..game_hooks.ff7_hook import FF7_CONNECTOR_CATALOG, catalog_entry_is_public, ff7_game_speed_select_options

        cfg.setdefault("game_id", "ff7")
        ops = {c["id"]: c["label"] for c in FF7_CONNECTOR_CATALOG if catalog_entry_is_public(c)}
        if not cfg.get("operation"):
            cfg["operation"] = "add_gil" if "add_gil" in ops else next(iter(ops))
        op = cfg["operation"]
        if op not in ops:
            legacy = next((c for c in FF7_CONNECTOR_CATALOG if c["id"] == op), None)
            ops[op] = f"{(legacy or {}).get('label', op)} (legacy)"
        entry = next((c for c in FF7_CONNECTOR_CATALOG if c["id"] == op), None)

        def set_op(e) -> None:
            for key in [k for k in cfg if k.startswith("arg_")]:
                del cfg[key]
            cfg["operation"] = e.value
            self._rerender_action_body(idx)

        with ui.element("div").classes("ce-fields"):
            with ui.element("div").classes("ce-f is-third"):
                ui.label("Game").classes("ce-f-label")
                ui.select({"ff7": "Final Fantasy VII (2013)"}, value=cfg["game_id"],
                          on_change=lambda e: (cfg.update(game_id=e.value), self.refresh_live())).props("outlined dense options-dense")
            with ui.element("div").classes("ce-f is-half"):
                ui.label("Operation").classes("ce-f-label")
                ui.select(ops, value=op, with_input=True, on_change=set_op).props("outlined dense options-dense")
            if entry is None:
                for key in sorted(k for k in cfg if k.startswith("arg_")):
                    self._field(idx, Field(key, key[4:], "text", variables=True, width="half"), cfg)
            for arg in (entry or {}).get("args", []):
                key = f"arg_{arg['name']}"
                if arg.get("control") == "select":
                    opts = dict(arg.get("options") or {})
                    if arg["name"] == "speed" and not opts:
                        opts = ff7_game_speed_select_options()
                    if opts and str(cfg.get(key)) not in opts:
                        cfg[key] = str(arg.get("default")) if str(arg.get("default")) in opts else next(iter(opts))
                    f = Field(key, arg.get("label", arg["name"]), "select", options=opts, width="half")
                else:
                    f = Field(key, arg.get("label", arg["name"]), "text", default=str(arg.get("default", "") or ""), variables=True, width="half")
                self._field(idx, f, cfg, extra_vars=_ff7_variables(arg.get("hint_tags") or ()))
        if entry and entry.get("description"):
            ui.label(entry["description"]).classes("ce-f-help")
        ui.label("Needs Game Hooks turned on and the game running on Windows. Battle-only operations are skipped outside battle.").classes("ce-f-help")

    # -- save ------------------------------------------------------------

    def save(self, test_after: bool = False) -> None:
        s = self.state
        problems = state_problems(s, self._context())
        if problems:
            more = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
            notify(problems[0] + more, type="warning", source="connectors", title="Can't save the connector yet",
                   hint="Fix this, then save again.")
            bad = {int(m.group(1)) - 1 for p in problems if (m := re.match(r"Action (\d+):", p))}
            if bad:
                for i, action in enumerate(s.actions):
                    action["open"] = i in bad
                self._rerender_actions()
            return
        try:
            connector = build_connector(s, self.existing)
        except Exception as exc:
            logger.error("Could not build connector: %s", exc, exc_info=True)
            notify("Nothing was saved. Your changes are still in the editor.", type="error", source="connectors",
                   title="Couldn't save the connector", hint="Check the trigger and actions for unusual values and try again.",
                   details=f"{type(exc).__name__}: {exc}")
            return
        manager = connector_manager.get_manager()
        with _LogCapture("modules.connector_manager") as captured:
            ok = manager.add_connector(connector) if s.is_new else manager.update_connector(s.connector_id, connector, keep_stats=True)
        if not ok:
            notify("Nothing was saved. Your changes are still in the editor.", type="error", source="connectors",
                   title="Couldn't save the connector",
                   hint="Check the trigger and each action, then try again. Copy the details if it keeps happening.",
                   details="\n".join(captured.lines) or None)
            return
        if manager.last_save_ok:  # a failed database write already showed its own error
            notify(f"{'Created' if s.is_new else 'Saved'} “{connector.name}”", type="success", source="connectors")
        self._baseline = s.snapshot()
        self.dialog.close()
        try:
            self.on_saved()
        except Exception as exc:
            logger.debug("on_saved callback failed: %s", exc)
        if test_after:
            run_connector_test(s.connector_id)



def _ff7_variables(tags) -> List[dict]:
    """Extra insertable tokens for an FF7 argument, scoped by its catalog ``hint_tags``."""
    tags = frozenset(tags or ())
    out: List[Tuple[str, str]] = []
    if tags & {"character", "hooks_party"}:
        out += [(f"hooks.ff7.party.{i}.name", f"Party slot {i + 1} name") for i in range(3)]
    if "character" in tags:
        out.append(("random_character", "Random party member"))
    if tags & {"enemy", "hooks_enemy"}:
        out += [(f"hooks.ff7.enemies.{i}.name", f"Enemy slot {i + 1} name") for i in range(6)]
        out.append(("message_after_conditions", "Message after condition words"))
    if "enemy" in tags:
        out.append(("random_enemy", "Random enemy"))
    if "hooks_gil" in tags:
        out.append(("hooks.ff7.gil", "Current gil"))
    if "hooks_battle" in tags:
        out.append(("hooks.ff7.battle", "In battle"))
    if "hooks_field" in tags:
        out.append(("hooks.ff7.field_name", "Current area"))
    if tags & {"damage", "random_range"}:
        out.append(("random_damage.1.9999", "Random number 1–9999 (edit the range)"))
    if "gear" in tags:
        out += [("random_weapon", "Random weapon"), ("random_armor", "Random armor"), ("random_accessory", "Random accessory")]
    return [{"key": k, "label": label, "example": "", "group": "Game"} for k, label in out]


def open_connector_editor(connector_id: Optional[str], on_saved: Callable[[], None]) -> ConnectorEditor:
    editor = ConnectorEditor(connector_id, on_saved)
    editor.open()
    return editor

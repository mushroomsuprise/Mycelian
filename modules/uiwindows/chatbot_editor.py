# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""The New/Edit dialog for chatbot commands and events.

Commands: When (command word, aliases, who can use it, cooldown, limit, type) → With (inputs)
→ Look up (optional Twitch info) → Then (reply). Events: When (event + filters) → Look up →
Then. Everything the dialog edits lives in ``ChatbotState``; saving starts from the stored
item's ``to_dict()`` so runtime counters, conditions and unknown fields survive. Catalogs come
from ``chatbot_variables`` / ``chatbot_lookups`` / ``chatbot_custom_variables``; the visual
shell and variable menus are shared with the connector editor.
"""

from __future__ import annotations

import copy
import importlib
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from nicegui import background_tasks, ui

from ..chatbot_core import ChatCommand, ChatEvent, EventType
from ..chatbot_manager import get_manager
from ..notification_engine import notify
from .chatbot_cards import event_glyph_key, event_info
from .chatbot_editor_styles import CHATBOT_EDITOR_CSS
from .connector_editor_styles import CONNECTOR_EDITOR_CSS, CONNECTOR_EDITOR_MENU_CSS
from .editor_variables import DEFAULT_DATE_FORMATS, VariableKit
from .editor_variables import esc as _esc
from .editor_variables import icon_html as _ic
from .editor_variables import raw_html as _html
from .service_brand_icons import SERVICE_BRAND_SVG

logger = logging.getLogger(__name__)

_COMMAND_RE = re.compile(r"^[^\s!][^\s]*$")
_HHMM = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_TIME_UNITS = {"sec": 1, "min": 60, "hr": 3600}
_PERMISSIONS_FALLBACK = [("everyone", "Everyone"), ("subscriber", "Subscribers"), ("vip", "VIPs"), ("moderator", "Mods")]
_PERMISSION_ICONS = {"everyone": "groups", "subscriber": "star", "vip": "diamond", "moderator": "shield"}
_COMMAND_TYPES = (("basic", "chat", "Reply"), ("counter", "plus_one", "Counter"), ("reset", "restart_alt", "Reset a counter"))
_LOOKUP_GROUP_ICONS = {"Viewers": "person", "Your stream": "live_tv", "Other channels": "switch_account", "Do something": "bolt"}
_EVENT_GROUP_ICONS = {"Twitch": "twitch", "YouTube": "youtube", "Tips": "volunteer_activism", "Time": "timer"}
_HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _glyph(key: str) -> str:
    svg = SERVICE_BRAND_SVG.get(key)
    return f'<span class="ce-brand is-{key}">{svg}</span>' if svg else _ic(key)


def _module(name: str):
    """Catalog modules are optional at import time so the editor still opens if one fails."""
    try:
        return importlib.import_module(f"..{name}", __package__)
    except Exception as exc:
        logger.debug("Catalog %s unavailable: %s", name, exc)
        return None


def _cv():
    return _module("chatbot_variables")


def _lk():
    return _module("chatbot_lookups")


def _cvars():
    return _module("chatbot_custom_variables")


def _value(obj: Any, name: str, default: Any = None) -> Any:
    v = getattr(obj, name, default)
    return getattr(v, "value", v)


# ---------------------------------------------------------------------------
#  Catalog access
# ---------------------------------------------------------------------------


def events_catalog() -> List[Dict[str, Any]]:
    cv = _cv()
    entries = list(getattr(cv, "EVENTS", None) or []) if cv else []
    if entries:
        return entries
    return [event_info(e.value) for e in EventType]


def event_filters(event_type: Optional[str]) -> List[Dict[str, Any]]:
    for entry in events_catalog():
        if entry.get("value") == event_type:
            return list(entry.get("filters") or [])
    return []


def all_filter_keys() -> Dict[str, Any]:
    keys: Dict[str, Any] = {}
    for entry in events_catalog():
        for spec in entry.get("filters") or []:
            keys.setdefault(spec["key"], spec.get("default", 0 if spec.get("kind") in ("number", "duration", "select") else ""))
    return keys


def lookups_catalog() -> List[Dict[str, Any]]:
    lk = _lk()
    return list(getattr(lk, "LOOKUPS", None) or []) if lk else []


def lookup_info(lookup_id: Optional[str]) -> Dict[str, Any]:
    for entry in lookups_catalog():
        if entry.get("id") == lookup_id:
            return entry
    if lookup_id == "advanced":
        return {"id": "advanced", "label": "Twitch API request", "group": "Advanced", "hint": "Any Helix endpoint. Ids and tokens are added for you."}
    return {"id": lookup_id, "label": (lookup_id or "").replace("_", " ").capitalize(), "hint": ""}


def lookup_available(entry: Dict[str, Any]) -> Tuple[bool, str]:
    lk = _lk()
    fn = getattr(lk, "lookup_available", None) if lk else None
    if callable(fn):
        try:
            ok, reason = fn(entry)
            return bool(ok), str(reason or "")
        except Exception as exc:
            return False, str(exc)
    return True, ""


def permissions_catalog() -> List[Tuple[str, str]]:
    cv = _cv()
    return [tuple(p) for p in (getattr(cv, "PERMISSIONS", None) or _PERMISSIONS_FALLBACK)] if cv else list(_PERMISSIONS_FALLBACK)


def date_formats() -> List[dict]:
    cv = _cv()
    return list(getattr(cv, "DATE_FORMATS", None) or DEFAULT_DATE_FORMATS) if cv else list(DEFAULT_DATE_FORMATS)


# ---------------------------------------------------------------------------
#  State
# ---------------------------------------------------------------------------


@dataclass
class ChatbotState:
    kind: str  # "command" | "event"
    item_id: str
    is_new: bool
    name: str = ""
    description: str = ""
    enabled: bool = True
    command_name: str = ""
    aliases: List[str] = field(default_factory=list)
    permission: str = "everyone"
    cooldown: int = 0
    usage_limit: int = 0
    command_type: str = "basic"
    persistent_counter: bool = True
    counter_value: int = 0
    reset_command: str = ""
    inputs: List[Dict[str, Any]] = field(default_factory=list)
    event_type: Optional[str] = None
    filters: Dict[str, Any] = field(default_factory=dict)
    lookup: Optional[Dict[str, Any]] = None
    lookup_fallback: str = ""
    response_text: str = ""
    reply_targets: List[str] = field(default_factory=lambda: ["twitch"])
    output_mode: str = "chat"
    discord_channels: List[Dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, default=str)


def _clean_word(text: Any) -> str:
    return str(text or "").strip().lstrip("!").strip().lower()


def _inputs_from_mappings(mappings: Any) -> List[Dict[str, Any]]:
    out = []
    for name, spec in (mappings or {}).items():
        if isinstance(spec, dict):
            pos, default = spec.get("position", 1), spec.get("default", "")
        else:
            pos, default = spec, ""
        try:
            pos = int(pos)
        except (TypeError, ValueError):
            pos = 1
        out.append({"name": str(name), "position": max(1, pos), "default": str(default or "")})
    return sorted(out, key=lambda i: (i["position"], i["name"]))


def _mappings_from_inputs(inputs: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for inp in inputs:
        name = str(inp.get("name") or "").strip()
        if not name:
            continue
        pos = max(1, int(inp.get("position") or 1))
        default = str(inp.get("default") or "")
        out[name] = {"position": pos, "default": default} if default else pos
    return out


def state_from_item(kind: str, item: Any, item_id: Optional[str] = None) -> ChatbotState:
    if item is None:
        state = ChatbotState(kind=kind, item_id=item_id or str(uuid.uuid4()), is_new=True)
        return state
    common = dict(
        name=getattr(item, "name", "") or "", description=getattr(item, "description", "") or "", enabled=bool(getattr(item, "enabled", True)),
        lookup=copy.deepcopy(getattr(item, "lookup", None)) or None, lookup_fallback=getattr(item, "lookup_fallback", "") or "",
        response_text=str(getattr(item, "response_text", "") or ""), reply_targets=list(getattr(item, "reply_targets", None) or []),
        output_mode=_value(item, "output_mode", "chat") or "chat", discord_channels=copy.deepcopy(list(getattr(item, "discord_channels", None) or [])),
    )
    if kind == "command":
        permission = getattr(item, "permission", None) or ("moderator" if getattr(item, "mod_only", False) else "everyone")
        return ChatbotState(
            kind=kind, item_id=getattr(item, "command_id", "") or item_id or str(uuid.uuid4()), is_new=False,
            command_name=_clean_word(getattr(item, "command_name", "")),
            aliases=[a for a in (_clean_word(a) for a in (getattr(item, "aliases", None) or [])) if a and "valuechange" not in a],
            permission=permission, cooldown=int(getattr(item, "cooldown", 0) or 0), usage_limit=int(getattr(item, "usage_limit", 0) or 0),
            command_type=_value(item, "command_type", "basic") or "basic", persistent_counter=bool(getattr(item, "persistent_counter", True)),
            counter_value=int(getattr(item, "counter_value", 0) or 0), reset_command=_clean_word(getattr(item, "reset_command", "")),
            inputs=_inputs_from_mappings(getattr(item, "argument_mappings", None)), **common,
        )
    et = _value(item, "event_type", None)
    filters = {}
    for spec in event_filters(et):
        filters[spec["key"]] = getattr(item, spec["key"], spec.get("default"))
    if et == "interval":
        filters.setdefault("interval", int(getattr(item, "interval", 0) or 0))
    elif et == "specific_time":
        filters.setdefault("specific_time", getattr(item, "specific_time", "") or "")
    return ChatbotState(kind=kind, item_id=getattr(item, "event_id", "") or item_id or str(uuid.uuid4()), is_new=False,
                        event_type=et, filters=filters, **common)


def _other_commands(state: ChatbotState) -> Dict[str, Any]:
    try:
        return {cid: c for cid, c in get_manager().get_all_commands().items() if cid != state.item_id}
    except Exception:
        return {}


def state_problems(state: ChatbotState) -> List[str]:
    problems: List[str] = []
    if not state.name.strip():
        problems.append("Name this " + state.kind + ".")
    if state.kind == "command":
        word = state.command_name
        if not word:
            problems.append("Type the command viewers use.")
        elif not _COMMAND_RE.match(word):
            problems.append("Commands can't contain spaces.")
        else:
            taken = {}
            for other in _other_commands(state).values():
                for w in [getattr(other, "command_name", "")] + list(getattr(other, "aliases", None) or []):
                    taken[_clean_word(w)] = getattr(other, "name", "") or "another command"
            for w in [word] + state.aliases:
                if w in taken:
                    problems.append(f"!{w} is already used by “{taken[w]}”.")
                    break
        if state.command_type == "reset" and not state.reset_command:
            problems.append("Choose the counter to reset.")
        names = [i.get("name", "").strip() for i in state.inputs]
        if any(not n for n in names):
            problems.append("Give every input a name.")
        elif any(not _NAME_RE.match(n) for n in names):
            problems.append("Input names use lowercase letters, digits and _ only.")
        elif len(set(names)) != len(names):
            problems.append("Input names must be different.")
    else:
        if not state.event_type:
            problems.append("Choose an event.")
        elif state.event_type == "interval" and int(state.filters.get("interval") or 0) < 10:
            problems.append("Timers need at least 10 seconds between messages.")
        elif state.event_type == "specific_time" and not _HHMM.match(str(state.filters.get("specific_time") or "")):
            problems.append("Set the time of day (HH:MM).")
    lk = state.lookup or {}
    if lk.get("id") == "advanced" and not str(lk.get("path") or "").strip():
        problems.append("Enter the Twitch API path for the advanced request.")
    if state.command_type != "reset" or state.kind == "event":
        if not state.response_text.strip():
            problems.append("Write the reply.")
    if not state.reply_targets and not state.discord_channels and state.output_mode != "banner":
        problems.append("Choose where the reply goes.")
    return problems


def build_item(state: ChatbotState, existing: Any = None) -> Any:
    """A new ChatCommand/ChatEvent from the state, starting from the stored item's data."""
    data: Dict[str, Any] = {}
    if existing is not None and hasattr(existing, "to_dict"):
        try:
            data = existing.to_dict()
        except Exception:
            data = {}
    data.update({
        "name": state.name.strip(), "description": state.description.strip(), "enabled": state.enabled,
        "response_text": state.response_text, "reply_targets": list(state.reply_targets), "output_mode": state.output_mode,
        "discord_channels": copy.deepcopy(state.discord_channels), "lookup": copy.deepcopy(state.lookup) if state.lookup else None,
        "lookup_fallback": state.lookup_fallback,
    })
    if state.lookup or getattr(existing, "lookup", None):
        data["api_enabled"] = False  # lookups replace the old raw API fields
    if state.kind == "command":
        data.update({
            "command_id": state.item_id, "command_name": state.command_name, "aliases": list(dict.fromkeys(state.aliases)),
            "permission": state.permission, "mod_only": state.permission in ("moderator", "broadcaster"),
            "cooldown": max(0, int(state.cooldown or 0)), "usage_limit": max(0, int(state.usage_limit or 0)),
            "command_type": state.command_type, "persistent_counter": state.persistent_counter,
            "counter_value": max(0, int(state.counter_value or 0)), "reset_command": state.reset_command,
            "argument_mappings": _mappings_from_inputs(state.inputs), "repeating_enabled": False,
        })
        return ChatCommand.from_dict(data)
    blank = ChatEvent()  # fields the current event type doesn't use go back to "any"
    for key in set(all_filter_keys()) | set(state.filters):
        data[key] = state.filters[key] if key in state.filters else getattr(blank, key, data.get(key))
    data.update({"event_id": state.item_id, "event_type": state.event_type})
    if state.event_type != "interval":
        data["interval"] = 0
    return ChatEvent.from_dict(data)


def run_item_test(kind: str, item_id: str) -> None:
    manager = get_manager()
    fn = getattr(manager, "test_command" if kind == "command" else "test_event", None)

    def _test_failed(details: str) -> None:
        notify(
            "",
            type="error",
            source="chatbot",
            title=f"The {kind} test couldn't run",
            hint="Save the item and try again. If it keeps happening, copy the details and report it.",
            details=details,
        )

    async def go() -> None:
        if not callable(fn):
            # The chatbot manager always has test_command/test_event; guard only.
            logger.error("Chatbot manager has no test function for %s", kind)
            _test_failed(f"No test function for {kind}")
            return
        try:
            result = fn(item_id)
            if hasattr(result, "__await__"):
                result = await result
        except Exception as exc:
            logger.error("Chatbot test failed: %s", exc, exc_info=True)
            _test_failed(f"{type(exc).__name__}: {exc}")
            return
        result = result or {}
        if not result.get("ok", True) and result.get("reason") not in (None, "", "Command not found", "Event not found"):
            _test_failed(str(result.get("reason")))  # an exception inside the test
        elif result.get("triggered") and result.get("response"):
            notify(
                result["response"],
                type="success",
                source="chatbot",
                title="Test reply",
                actions=[{"kind": "copy", "text": str(result["response"]), "label": "Copy reply"}],
            )
        elif result.get("ok", True) and result.get("triggered"):
            notify("Test ran with sample data", type="success", source="chatbot")
        else:
            notify(
                result.get("reason") or "Its filters didn't match the sample.",
                type="info",
                source="chatbot",
                title="Test didn't run",
            )

    background_tasks.create(go(), name=f"chatbot_test_{kind}")


# ---------------------------------------------------------------------------
#  Dialog
# ---------------------------------------------------------------------------


class ChatbotEditor:
    def __init__(self, kind: str, item_id: Optional[str], on_saved: Callable[[], None]):
        self.kind = kind
        self.on_saved = on_saved
        manager = get_manager()
        self.existing = (manager.get_command(item_id) if kind == "command" else manager.get_event(item_id)) if item_id else None
        self.state = state_from_item(kind, self.existing, item_id)
        if self.state.is_new and kind == "event":
            self.state.reply_targets = ["twitch"]
        self._baseline = ""
        self._live: List[Callable[[], bool]] = []
        self._picking_event = kind == "event" and not self.state.event_type
        self._picking_lookup = False
        self.dialog: Optional[ui.dialog] = None
        self.vars = VariableKit(self._variables, self.render_text, date_formats, "Type any {name} the event sends.")

    # -- context / variables ------------------------------------------------

    def _preview_item(self) -> Any:
        try:
            return build_item(self.state, self.existing)
        except Exception:
            return self.existing

    def render_text(self, text: Any) -> str:
        text = "" if text is None else str(text)
        cv = _cv()
        fn = getattr(cv, "render_preview", None) if cv else None
        if not callable(fn):
            return text
        try:
            return fn(text, self.kind, event_type=self.state.event_type, item=self._preview_item())
        except Exception as exc:
            logger.debug("Preview failed: %s", exc)
            return text

    def _variables(self, _idx: Optional[int] = None, extra: Optional[List[dict]] = None) -> List[dict]:
        cv = _cv()
        out: List[dict] = []
        if cv is None:
            return [{"key": "username", "label": "Who triggered it", "example": "MossyMika", "group": "Viewer"}] + list(extra or [])
        try:
            if self.kind == "command":
                names = [i["name"] for i in self.state.inputs if i.get("name")]
                out += [dict(v) for v in cv.command_variables(self.state.command_type, names, self.state.lookup)]
            elif self.state.event_type:
                out += [dict(v, group=v.get("group") or "Event") for v in cv.EVENT_VARIABLES.get(self.state.event_type, [])]
            if self.state.lookup and hasattr(cv, "lookup_variables") and self.kind == "event":
                out += [dict(v) for v in cv.lookup_variables(self.state.lookup)]
            common = [dict(v) for v in getattr(cv, "COMMON_VARIABLES", [])]
            basics = [v for v in common if v.get("group") in ("Basics", "Viewer", "Time")]
            out += basics
            if hasattr(cv, "custom_variables"):
                out += [dict(v, group="Custom") for v in cv.custom_variables()]
            out += [v for v in common if v not in basics]
        except Exception as exc:
            logger.debug("Variable catalog failed: %s", exc)
        seen, uniq = set(), []
        for v in out + list(extra or []):
            if v.get("key") and v["key"] not in seen:
                seen.add(v["key"])
                uniq.append(v)
        return uniq

    def _dirty(self) -> bool:
        return self.state.snapshot() != self._baseline

    # -- open / close ---------------------------------------------------------

    def open(self) -> ui.dialog:
        from ..help_system.help_render import inject_page_stylesheet, promote_color_importance, scope_selectors

        inject_page_stylesheet("mycelian-connector-editor", promote_color_importance(scope_selectors(CONNECTOR_EDITOR_CSS, ".cxe")))
        inject_page_stylesheet("mycelian-connector-editor-menus", CONNECTOR_EDITOR_MENU_CSS)
        inject_page_stylesheet("mycelian-chatbot-editor", promote_color_importance(scope_selectors(CHATBOT_EDITOR_CSS, ".cxe")))
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
            ui.label(f"Close this {self.kind} without saving? Your edits will be lost.").classes("secondary-text mb-3")

            def discard() -> None:
                confirm.close()
                self.dialog.close()

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Keep editing", on_click=confirm.close).props("outline no-caps")
                ui.button("Discard", on_click=discard).props("color=negative unelevated no-caps")
        confirm.open()

    def _build_header(self) -> None:
        s = self.state
        icon = "add_comment" if self.kind == "command" else "celebration"
        with ui.element("div").classes("ce-head"):
            _html(f'<span class="ce-tile">{_ic(icon)}</span>')
            with ui.element("div").classes("ce-head-text"):
                with ui.element("div").classes("ce-name-row"):
                    ui.label("New" if s.is_new else "Edit").classes("ce-kicker")
                    ui.input(value=s.name, placeholder=f"Name this {self.kind}",
                             on_change=lambda e: self._set("name", e.value or "")).props("borderless dense").classes("ce-name-input")
                ui.input(value=s.description, placeholder="Add a short description (optional)",
                         on_change=lambda e: self._set("description", e.value or "")).props("borderless dense").classes("ce-desc-input")
            with ui.element("div").classes("ce-onoff"):
                onoff = ui.label("On" if s.enabled else "Off")

                def toggle(e) -> None:
                    s.enabled = bool(e.value)
                    onoff.set_text("On" if s.enabled else "Off")

                ui.switch(value=s.enabled, on_change=toggle).props("dense").tooltip(f"Turn the {self.kind} on or off")
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
            label = ("Create command" if self.kind == "command" else "Create event") if self.state.is_new else "Save"
            ui.button(label, icon="check", on_click=lambda: self.save()).props("unelevated no-caps color=primary")

    def _set(self, key: str, value: Any) -> None:
        setattr(self.state, key, value)
        self.refresh_live()

    def refresh_live(self) -> None:
        self._live = [fn for fn in self._live if fn() is not False]
        problems = state_problems(self.state)
        self.status_box.classes(remove="is-ok is-warn", add="is-warn" if problems else "is-ok")
        if problems:
            more = f"  (+{len(problems) - 1} more)" if len(problems) > 1 else ""
            self.status_icon.set_content(_ic("error_outline"))
            self.status_label.set_text(problems[0] + more)
        else:
            self.status_icon.set_content(_ic("check_circle"))
            self.status_label.set_text("Ready to save")

    # -- flow -----------------------------------------------------------------

    def render_flow(self) -> None:
        self._live = []
        self.flow.clear()
        with self.flow:
            if self.kind == "command":
                self._step(1, "When", "Command", "What viewers type", self._build_command)
                self.inputs_box = self._step(2, "With", "Inputs", "Optional. Words viewers add after the command.", self._build_inputs,
                                             tag=lambda: self._count(len(self.state.inputs), "input"))
                self.lookup_box = self._step(3, "Look up", "Twitch info", "Optional. Fetch live info to use in the reply.", self._build_lookup)
                self._step(4, "Then", "Reply", "What the bot says", self._build_reply)
            else:
                self.event_box = self._step(1, "When", "Event", "What starts this reply", self._build_event)
                self.lookup_box = self._step(2, "Look up", "Twitch info", "Optional. Fetch live info to use in the reply.", self._build_lookup)
                self._step(3, "Then", "Reply", "What the bot says", self._build_reply)
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

    def _rerender(self, box: ui.element, builder: Callable[[ui.element], None]) -> None:
        box.clear()
        with box:
            builder(box)
        self.refresh_live()

    def _seg(self, options: List[Tuple[str, str, str]], current: str, on_pick: Callable[[str], None]) -> None:
        with ui.element("div").classes("ce-seg"):
            for value, icon, label in options:
                opt = ui.element("div").classes("is-on" if value == current else "")
                with opt:
                    if icon:
                        _html(_ic(icon))
                    ui.label(label)
                opt.on("click", lambda _e, v=value: on_pick(v))

    def _num(self, value: Any, on_change: Callable[[int], None], width: str = "72px", minimum: int = 0) -> ui.number:
        return ui.number(value=value or 0, min=minimum, step=1, format="%g",
                         on_change=lambda e: on_change(int(e.value or 0))).props("outlined dense").style(f"width: {width}")

    # -- command step ---------------------------------------------------------

    def _build_command(self, container: ui.element) -> None:
        self.command_box = container
        s = self.state
        with ui.element("div").classes("ce-box"):
            with ui.element("div").classes("ce-cmdgrid"):
                with ui.element("div").classes("ce-f"):
                    ui.label("Command").classes("ce-f-label")
                    word = ui.input(value=s.command_name, placeholder="discord").props("outlined dense autofocus" if s.is_new else "outlined dense").classes("ce-bang")
                    with word.add_slot("prepend"):
                        ui.label("!")

                    def set_word(e) -> None:
                        cleaned = _clean_word(e.value)
                        s.command_name = cleaned
                        self.refresh_live()

                    word.on_value_change(set_word)
                    word.on("blur", lambda: word.set_value(s.command_name) if word.value != s.command_name else None)
                with ui.element("div").classes("ce-f"):
                    ui.label("Also answers to").classes("ce-f-label")
                    self._aliases_editor()
            with ui.element("div").classes("ce-row"):
                ui.label("Who can use it").classes("ce-mini")
                self._seg([(v, _PERMISSION_ICONS.get(v, "person"), label) for v, label in permissions_catalog()], s.permission,
                          lambda v: (setattr(s, "permission", v), self._rerender(self.command_box, self._build_command)))
                ui.element("div").classes("ce-sep")
                ui.label("Cooldown").classes("ce-mini")
                self._num(s.cooldown, lambda v: self._set("cooldown", v)).tooltip("Ignore the command for this long after it runs")
                ui.label("sec").classes("ce-unit")
                ui.element("div").classes("ce-sep")
                ui.label("Limit").classes("ce-mini")
                self._num(s.usage_limit, lambda v: self._set("usage_limit", v)).tooltip("How many times it can be used in total. 0 = no limit")
                ui.label("uses").classes("ce-unit")
                used = int(getattr(self.existing, "usage_count", 0) or 0)
                if used and s.usage_limit:
                    reset = ui.label(f"used {used} · reset").classes("ce-link")
                    reset.on("click", self._reset_usage)
            with ui.element("div").classes("ce-row"):
                ui.label("Type").classes("ce-mini")
                self._seg(list(_COMMAND_TYPES), s.command_type,
                          lambda v: (setattr(s, "command_type", v), self._rerender(self.command_box, self._build_command), self.render_vars()))
                if s.command_type == "basic":
                    _html(f'<span class="ce-help-inline">{_ic("info")}Counters add 1 each time and give you <code>{{count}}</code>.</span>', "")
                elif s.command_type == "counter":
                    ui.label("Count now").classes("ce-mini")
                    self._num(s.counter_value, lambda v: self._set("counter_value", v), width="84px")
                    ui.switch("Keep the count after a restart", value=s.persistent_counter,
                              on_change=lambda e: self._set("persistent_counter", bool(e.value))).props("dense")
                else:
                    counters = self._counter_options()
                    if s.reset_command and s.reset_command not in counters:
                        counters[s.reset_command] = f"!{s.reset_command} (not found)"
                    ui.label("Resets").classes("ce-mini")
                    ui.select(counters or {"": "(no counter commands yet)"}, value=s.reset_command or None,
                              on_change=lambda e: self._set("reset_command", e.value or "")).props("outlined dense options-dense").style("width: 220px")
                    _html(f'<span class="ce-help-inline">{_ic("info")}Use <code>{{target_count}}</code> for the count before the reset.</span>', "")

    def _counter_options(self) -> Dict[str, str]:
        out = {}
        for c in _other_commands(self.state).values():
            if _value(c, "command_type", "basic") == "counter":
                w = _clean_word(getattr(c, "command_name", ""))
                if w:
                    out[w] = f"!{w} · {getattr(c, 'name', '')}"
        return out

    def _reset_usage(self) -> None:
        fn = getattr(get_manager(), "reset_usage", None)
        if callable(fn) and not self.state.is_new:
            if fn(self.state.item_id):
                notify("Uses reset to 0", type="success", source="chatbot")
            else:
                notify("This command was deleted, so there's nothing to reset.", type="info", source="chatbot")
        else:
            notify("Save the command first, then reset its uses.", type="info", source="chatbot")

    def _aliases_editor(self) -> None:
        s = self.state
        box = ui.element("div").classes("ce-chips")

        def render() -> None:
            box.clear()
            with box:
                for alias in list(s.aliases):
                    with ui.element("div").classes("ce-alias"):
                        ui.label(f"!{alias}")
                        ui.button(icon="close", on_click=lambda _e, a=alias: remove(a)).props("flat round dense size=xs")
                entry = ui.input(placeholder="Add another…" if s.aliases else "Add another, e.g. dc").props("borderless dense")

                def commit(_e=None) -> None:
                    parts = [p for p in re.split(r"[\s,]+", entry.value or "") if p.strip()]
                    added = False
                    for part in parts:
                        w = _clean_word(part)
                        if w and w != s.command_name and w not in s.aliases:
                            s.aliases.append(w)
                            added = True
                    if added or parts:
                        render()
                        self.refresh_live()

                entry.on("keydown.enter", commit)
                entry.on("blur", commit)
                entry.on_value_change(lambda e: commit() if (e.value or "").endswith((",", " ")) else None)

        def remove(alias: str) -> None:
            s.aliases = [a for a in s.aliases if a != alias]
            render()
            self.refresh_live()

        render()

    # -- inputs step ----------------------------------------------------------

    def _usage_line(self) -> str:
        words = []
        by_pos = {}
        for inp in self.state.inputs:
            by_pos.setdefault(int(inp.get("position") or 1), inp.get("name") or "?")
        last = max(by_pos) if by_pos else 0
        for pos in range(1, last + 1):
            words.append(f"<i>&lt;{_esc(by_pos[pos])}&gt;</i>" if pos in by_pos else "<i>…</i>")
        return f'Viewers type: <b>!{_esc(self.state.command_name or "command")}</b> ' + " ".join(words)

    def _build_inputs(self, container: ui.element) -> None:
        s = self.state
        if s.inputs:
            with ui.element("div").classes("ce-box"):
                usage = ui.html("", sanitize=False).classes("ce-usage")

                def upd() -> bool:
                    if usage.is_deleted:
                        return False
                    usage.set_content(self._usage_line())
                    return True

                upd()
                self._live.append(upd)
                for idx, inp in enumerate(s.inputs):
                    self._input_row(idx, inp)
        add = ui.element("div").classes("ce-add")
        with add:
            _html(_ic("add"))
            ui.label("Add input")
        add.on("click", self._add_input)

    def _input_row(self, idx: int, inp: Dict[str, Any]) -> None:
        with ui.element("div").classes("ce-arg"):
            ui.label(str(idx + 1)).classes("ce-arg-n")
            # Words 1-10 (what Add input hands out), plus a stored position outside that.
            pos = int(inp.get("position") or 1)
            ui.select({p: f"Word {p}" for p in sorted({*range(1, 11), pos})}, value=pos,
                      on_change=lambda e, i=inp: (i.update(position=int(e.value or 1)), self.refresh_live())).props("outlined dense options-dense")
            _html(_ic("arrow_forward"), "ce-arrow")
            name = ui.input(value=inp.get("name", ""), placeholder="target").props("outlined dense").tooltip("Use it in the reply as {name}")

            def set_name(e, i=inp) -> None:
                i["name"] = re.sub(r"[^a-z0-9_]", "_", (e.value or "").strip().lower())
                self.refresh_live()

            name.on_value_change(set_name)
            name.on("blur", lambda _e, i=inp, el=name: (el.set_value(i["name"]) if el.value != i["name"] else None, self.render_vars()))
            with ui.element("div").classes("ce-varhost"):
                default = ui.input(value=inp.get("default", ""), placeholder="If empty: leave blank, or e.g. {username}",
                                   on_change=lambda e, i=inp: (i.update(default=e.value or ""), self.refresh_live())).props("outlined dense")
                self.vars.button(default)
                self.vars.track(default)
            ui.button(icon="close", on_click=lambda _e, i=idx: self._remove_input(i)).props("flat round dense").classes("is-danger").tooltip("Remove input")

    def _add_input(self) -> None:
        taken = {int(i.get("position") or 1) for i in self.state.inputs}
        pos = next(p for p in range(1, 11) if p not in taken) if len(taken) < 10 else 1
        default_name = "target" if not self.state.inputs else f"word{pos}"
        self.state.inputs.append({"name": default_name, "position": pos, "default": ""})
        self._rerender(self.inputs_box, self._build_inputs)
        self.render_vars()

    def _remove_input(self, idx: int) -> None:
        if 0 <= idx < len(self.state.inputs):
            self.state.inputs.pop(idx)
        self._rerender(self.inputs_box, self._build_inputs)
        self.render_vars()

    # -- event step -----------------------------------------------------------

    def _build_event(self, container: ui.element) -> None:
        s = self.state
        if self._picking_event or not s.event_type:
            self._event_picker()
            return
        info = event_info(s.event_type)
        key = event_glyph_key(s.event_type)
        tile_cls = {"twitch": "is-twitch", "youtube": "is-youtube"}.get(key, "")
        with ui.element("div").classes("ce-trig"):
            _html(f'<span class="ce-tile {tile_cls}">{_glyph(key)}</span>')
            with ui.element("div").classes("ce-trig-text"):
                ui.label(info.get("label") or s.event_type).classes("ce-trig-name")
                if info.get("hint"):
                    ui.label(info["hint"]).classes("ce-trig-hint")
            ui.button("Change", icon="swap_horiz", on_click=self._start_event_pick).props("outline no-caps dense color=primary")
        if info.get("warning"):
            _html(f'<div class="ce-warnline">{_ic("warning_amber")}<span>{_esc(info["warning"])}</span></div>', "")
        specs = event_filters(s.event_type)
        if specs:
            with ui.element("div").classes("ce-box"):
                with ui.element("div").classes("ce-filter"):
                    ui.label("Only when" if s.event_type not in ("interval", "specific_time") else "Runs").classes("ce-mini")
                    has_match = any(spec.get("kind") == "match" for spec in specs)
                    for spec in sorted(specs, key=lambda sp: 0 if sp.get("kind") == "match" else 1):
                        self._filter_widget(spec, paired=has_match)
                helps = [f"{spec.get('label') or ''}: {spec['help']}".strip(": ") if len(specs) > 1 else spec["help"]
                         for spec in specs if spec.get("help")]
                if helps:
                    _html(f'<div class="ce-note">{_ic("info")}<span>{_esc(" · ".join(helps))}</span></div>', "")

    def _filter_widget(self, spec: Dict[str, Any], paired: bool = False) -> None:
        s = self.state
        key, kind = spec["key"], spec.get("kind", "number")
        value = s.filters.get(key, spec.get("default"))
        label = spec.get("label", "")

        def setv(v: Any) -> None:
            s.filters[key] = v
            self.refresh_live()

        if kind == "number":
            if label:
                ui.label(label).classes("ce-unit")
            ui.number(value=value or 0, min=0, step=spec.get("step", 1), format="%g",
                      on_change=lambda e: setv((e.value or 0) if isinstance(spec.get("default"), float) else int(e.value or 0))).props(
                "outlined dense").style("width: 90px")
            if spec.get("unit"):
                ui.label(spec["unit"]).classes("ce-unit")
        elif kind == "select":
            opts = {(int(k) if str(k).lstrip("-").isdigit() else k): v for k, v in (spec.get("options") or {}).items()}
            if label:
                ui.label(label).classes("ce-unit")
            ui.select(opts, value=value if value in opts else next(iter(opts), None), on_change=lambda e: setv(e.value)).props(
                "outlined dense options-dense").style("width: 140px")
        elif kind == "time":
            if label:
                ui.label(label).classes("ce-unit")
            ui.input(value=str(value or ""), on_change=lambda e: setv((e.value or "")[:5])).props("outlined dense type=time").style("width: 130px")
        elif kind == "duration":
            seconds = int(value or 0) or 1200
            unit = "hr" if seconds % 3600 == 0 else ("min" if seconds % 60 == 0 else "sec")
            if label:
                ui.label(label).classes("ce-unit")
            amount = ui.number(value=seconds // _TIME_UNITS[unit], min=1, step=1, format="%g").props("outlined dense").style("width: 80px")
            unit_sel = ui.select({"sec": "seconds", "min": "minutes", "hr": "hours"}, value=unit).props("outlined dense options-dense").style("width: 120px")

            def changed(_e=None) -> None:
                setv(max(1, int(float(amount.value or 1) * _TIME_UNITS[unit_sel.value or "sec"])))

            amount.on_value_change(changed)
            unit_sel.on_value_change(changed)
            s.filters[key] = seconds
        elif kind == "match":
            opts = dict(spec.get("options") or {"exact": "Is exactly", "starts_with": "Starts with", "contains": "Contains"})
            ui.label("Message").classes("ce-unit")
            ui.select(opts, value=value if value in opts else next(iter(opts)), on_change=lambda e: setv(e.value)).props(
                "outlined dense options-dense").style("width: 150px")
        elif kind == "text" and paired:
            ui.input(value=str(value or ""), placeholder="!lurk  (empty = every message)", on_change=lambda e: setv(e.value or "")).props(
                "outlined dense").style("width: 240px")
        else:
            if label:
                ui.label(label).classes("ce-unit")
            ui.input(value=str(value or ""), placeholder=spec.get("placeholder", "(any)"), on_change=lambda e: setv(e.value or "")).props(
                "outlined dense").style("width: 200px")

    def _start_event_pick(self) -> None:
        self._picking_event = True
        self._rerender(self.event_box, self._build_event)

    def _event_picker(self) -> None:
        current = self.state.event_type
        with ui.element("div").classes("ce-picker"):
            with ui.element("div").classes("ce-picker-head"):
                ui.label("Choose an event").classes("ce-step-title")
                ui.element("div").classes("ce-spacer")
                search = ui.input(placeholder="Search events").props("outlined dense clearable autofocus")
                with search.add_slot("prepend"):
                    ui.icon("search").classes("text-sm")
                if current:
                    ui.button("Cancel", on_click=lambda: (setattr(self, "_picking_event", False), self._rerender(self.event_box, self._build_event))).props(
                        "flat no-caps dense color=primary")
            box = ui.element("div").classes("ce-stack")

        def fill(query: str = "") -> None:
            q = query.strip().lower()
            groups: Dict[str, List[dict]] = {}
            for entry in events_catalog():
                if not q or q in f"{entry.get('label', '')} {entry.get('hint', '')} {entry.get('group', '')}".lower():
                    groups.setdefault(entry.get("group") or "Other", []).append(entry)
            box.clear()
            with box:
                if not groups:
                    ui.label("No events match.").classes("ce-step-sub")
                for group, entries in groups.items():
                    with ui.element("div").classes("ce-pgroup"):
                        _html(f'<div class="ce-pgroup-head">{_glyph(_EVENT_GROUP_ICONS.get(group, "bolt"))}<span>{_esc(group)}</span></div>', "")
                        with ui.element("div").classes("ce-pgrid"):
                            for entry in entries:
                                opt = ui.element("div").classes("ce-popt" + (" is-selected" if entry["value"] == current else ""))
                                with opt:
                                    ui.label(entry.get("label", entry["value"])).classes("ce-popt-title")
                                    if entry.get("hint"):
                                        ui.label(entry["hint"]).classes("ce-popt-hint")
                                    if entry.get("warning"):
                                        ui.label("May not fire").classes("ce-popt-warn")
                                opt.on("click", lambda _e, v=entry["value"]: self._choose_event(v))

        search.on_value_change(lambda e: fill(e.value or ""))
        fill()

    def _choose_event(self, event_type: str) -> None:
        s = self.state
        self._picking_event = False
        if event_type != s.event_type:
            s.event_type = event_type
            s.filters = {spec["key"]: spec.get("default") for spec in event_filters(event_type)}
            if s.lookup and (s.lookup.get("target") or {}).get("from") in ("chatter", "input"):
                s.lookup["target"] = {"from": "event_user"}
        self.render_flow()
        self.render_vars()

    # -- lookup step ----------------------------------------------------------

    def _build_lookup(self, container: ui.element) -> None:
        s = self.state
        if self._picking_lookup:
            self._lookup_picker()
            return
        if not s.lookup:
            add = ui.element("div").classes("ce-add")
            with add:
                _html(_ic("travel_explore"))
                ui.label("Look up Twitch info")
            add.on("click", lambda: (setattr(self, "_picking_lookup", True), self._rerender(self.lookup_box, self._build_lookup)))
            return
        if s.lookup.get("id") == "advanced":
            self._advanced_card()
        else:
            self._lookup_card()

    def _lookup_picker(self) -> None:
        availability = {entry["id"]: lookup_available(entry) for entry in lookups_catalog()}
        reasons = {reason for ok, reason in availability.values() if not ok}
        shared = next(iter(reasons)) if availability and len(reasons) == 1 and not any(ok for ok, _ in availability.values()) else ""
        with ui.element("div").classes("ce-picker"):
            with ui.element("div").classes("ce-picker-head"):
                ui.label("Look up Twitch info").classes("ce-step-title")
                ui.element("div").classes("ce-spacer")
                search = ui.input(placeholder="Search lookups").props("outlined dense clearable autofocus")
                with search.add_slot("prepend"):
                    ui.icon("search").classes("text-sm")
                ui.button("Cancel", on_click=lambda: (setattr(self, "_picking_lookup", False), self._rerender(self.lookup_box, self._build_lookup))).props(
                    "flat no-caps dense color=primary")
            if shared:
                _html(f'<div class="ce-warnline">{_ic("warning_amber")}<span>{_esc(shared)} You can still set these up; '
                      'they start working once Twitch is connected.</span></div>', "")
            box = ui.element("div").classes("ce-stack")
            with ui.element("div").classes("ce-adv"):
                _html(_ic("verified"))
                ui.label("All of these use your connected Twitch account. Ids, tokens and headers are filled in for you.")
            with ui.element("div").classes("ce-adv"):
                _html(_ic("tune"))
                ui.label("Need something else?")
                adv = ui.label("Use any Twitch API request (advanced)").classes("ce-link")
                adv.on("click", lambda: self._choose_lookup("advanced"))

        current = (self.state.lookup or {}).get("id")
        def fill(query: str = "") -> None:
            q = query.strip().lower()
            groups: Dict[str, List[dict]] = {}
            for entry in lookups_catalog():
                if not q or q in f"{entry.get('label', '')} {entry.get('hint', '')} {entry.get('group', '')}".lower():
                    groups.setdefault(entry.get("group") or "Other", []).append(entry)
            box.clear()
            with box:
                if not lookups_catalog():
                    ui.label("Twitch lookups aren't available right now.").classes("ce-step-sub")
                elif not groups:
                    ui.label("No lookups match.").classes("ce-step-sub")
                for group, entries in groups.items():
                    with ui.element("div").classes("ce-pgroup"):
                        _html(f'<div class="ce-pgroup-head">{_ic(_LOOKUP_GROUP_ICONS.get(group, "bolt"))}<span>{_esc(group)}</span></div>', "")
                        with ui.element("div").classes("ce-pgrid"):
                            for entry in entries:
                                ok, reason = availability.get(entry["id"], (True, ""))
                                opt = ui.element("div").classes("ce-popt" + (" is-selected" if entry["id"] == current else ""))
                                with opt:
                                    ui.label(entry.get("label", entry["id"])).classes("ce-popt-title")
                                    if entry.get("hint"):
                                        ui.label(entry["hint"]).classes("ce-popt-hint")
                                    if not ok and not shared:
                                        ui.label(reason or "Not available").classes("ce-popt-warn")
                                opt.on("click", lambda _e, v=entry["id"]: self._choose_lookup(v))

        search.on_value_change(lambda e: fill(e.value or ""))
        fill()

    def _choose_lookup(self, lookup_id: str) -> None:
        s = self.state
        self._picking_lookup = False
        if lookup_id == "advanced":
            old = s.lookup if (s.lookup or {}).get("id") == "advanced" else None
            s.lookup = old or {"id": "advanced", "method": "GET", "path": "", "params": {}, "body": "", "processing": []}
        else:
            entry = lookup_info(lookup_id)
            config: Dict[str, Any] = {"id": lookup_id}
            if entry.get("target"):
                if s.kind == "event":
                    config["target"] = {"from": "event_user"}
                elif s.inputs:
                    config["target"] = {"from": "input", "input": s.inputs[0]["name"]}
                else:
                    config["target"] = {"from": "chatter"}
            text_arg = entry.get("text_arg")
            if text_arg:
                # The default is {command_message}, which only commands have.
                config["text"] = text_arg.get("default", "{command_message}") if s.kind == "command" else ""
            s.lookup = config
        self._rerender(self.lookup_box, self._build_lookup)
        self.render_vars()

    def _remove_lookup(self) -> None:
        self.state.lookup = None
        self.state.lookup_fallback = ""
        self._rerender(self.lookup_box, self._build_lookup)
        self.render_vars()

    def _lookup_vars(self) -> List[dict]:
        cv = _cv()
        if cv is not None and hasattr(cv, "lookup_variables") and self.state.lookup:
            try:
                return [dict(v) for v in cv.lookup_variables(self.state.lookup)]
            except Exception:
                pass
        return [dict(v) for v in lookup_info((self.state.lookup or {}).get("id")).get("variables") or []]

    def _target_options(self) -> Dict[str, str]:
        if self.kind == "event":
            opts = {"event_user": "The viewer from the event"}
        else:
            opts = {"chatter": "Whoever typed the command"}
            for inp in self.state.inputs:
                if inp.get("name"):
                    opts[f"input:{inp['name']}"] = f"{{{inp['name']}}} (word {inp.get('position', 1)}, or whoever typed it)"
        opts["text"] = "A name I type…"
        return opts

    def _lookup_card(self) -> None:
        s = self.state
        lk = s.lookup
        entry = lookup_info(lk.get("id"))
        ok, reason = lookup_available(entry)
        with ui.element("div").classes("ce-look"):
            _html(f'<span class="ce-tile is-twitch">{_glyph("twitch")}</span>')
            with ui.element("div").classes("ce-look-t"):
                with ui.element("div").classes("ce-look-n"):
                    ui.label(entry.get("label", lk.get("id")))
                    if ok:
                        _html(f'<span class="ce-auto">{_ic("verified")}Uses your Twitch login · nothing to set up</span>', "")
                    else:
                        _html(f'<span class="ce-auto is-warn">{_ic("warning_amber")}{_esc(reason or "Not available right now")}</span>', "")
                if entry.get("hint"):
                    ui.label(entry["hint"]).classes("ce-look-d")
                if entry.get("target"):
                    target = lk.get("target") or {}
                    current = target.get("from", "chatter")
                    if current == "input":
                        current = f"input:{target.get('input', '')}"
                    opts = self._target_options()
                    if current not in opts:
                        opts[current] = current.replace("input:", "{") + ("}" if current.startswith("input:") else "")
                    with ui.element("div").classes("ce-row"):
                        ui.label("Check" if entry.get("target") == "user" else "Channel").classes("ce-mini")

                        def set_target(e) -> None:
                            v = e.value or "chatter"
                            if v.startswith("input:"):
                                lk["target"] = {"from": "input", "input": v[6:]}
                            elif v == "text":
                                lk["target"] = {"from": "text", "text": (lk.get("target") or {}).get("text", "")}
                            else:
                                lk["target"] = {"from": v}
                            self._rerender(self.lookup_box, self._build_lookup)

                        ui.select(opts, value=current, on_change=set_target).props("outlined dense options-dense").style("width: 320px")
                        if current == "text":
                            with ui.element("div").classes("ce-varhost").style("flex: 1 1 200px"):
                                t = ui.input(value=target.get("text", ""), placeholder="friendlystreamer or {command_word_1}",
                                             on_change=lambda e: (lk.setdefault("target", {}).update(text=e.value or ""), self.refresh_live())).props("outlined dense")
                                self.vars.button(t)
                                self.vars.track(t)
                for opt in entry.get("options") or []:
                    choices = opt.get("choices") or opt.get("options") or {}
                    if isinstance(choices, list):
                        choices = {c: str(c).replace("_", " ").capitalize() for c in choices}
                    if not choices:
                        continue
                    okey = opt.get("key", "period")
                    if lk.get(okey) not in choices:
                        lk[okey] = opt.get("default", next(iter(choices)))
                    with ui.element("div").classes("ce-row"):
                        ui.label(opt.get("label", okey.capitalize())).classes("ce-mini")
                        ui.select(choices, value=lk[okey], on_change=lambda e, k=okey: (lk.update({k: e.value}), self.refresh_live())).props(
                            "outlined dense options-dense").style("width: 180px")
                text_arg = entry.get("text_arg")
                if text_arg:
                    with ui.element("div").classes("ce-row"):
                        ui.label(text_arg.get("label", "Text")).classes("ce-mini")
                        with ui.element("div").classes("ce-varhost").style("flex: 1 1 260px"):
                            t = ui.input(value=lk.get("text", ""), placeholder=text_arg.get("placeholder", ""),
                                         on_change=lambda e: (lk.update(text=e.value or ""), self.refresh_live())).props("outlined dense")
                            self.vars.button(t)
                            self.vars.track(t)
                variables = self._lookup_vars()
                if variables:
                    with ui.element("div").classes("ce-gives"):
                        ui.html("<em>Gives you</em>", sanitize=False).classes("contents")
                        for var in variables:
                            code = ui.html(f"<code>{{{_esc(var['key'])}}}</code>", sanitize=False).classes("contents")
                            code.tooltip(var.get("label", ""))
                with ui.element("div").classes("ce-row"):
                    ui.label("If it fails" if lk.get("id") != "follow_age" else "If they don't follow").classes("ce-mini")
                    with ui.element("div").classes("ce-varhost").style("flex: 1 1 260px"):
                        fb = ui.input(value=s.lookup_fallback, placeholder="Leave empty to skip the reply",
                                      on_change=lambda e: self._set("lookup_fallback", e.value or "")).props("outlined dense")
                        self.vars.button(fb)
                        self.vars.track(fb)
            ui.button("Change", icon="swap_horiz", on_click=lambda: (setattr(self, "_picking_lookup", True), self._rerender(self.lookup_box, self._build_lookup))).props(
                "outline no-caps dense color=primary")
            ui.button(icon="close", on_click=self._remove_lookup).props("flat round dense").classes("is-danger").tooltip("Remove lookup")

    def _advanced_card(self) -> None:
        s = self.state
        lk = s.lookup
        lk.setdefault("params", {})
        with ui.element("div").classes("ce-look"):
            _html(f'<span class="ce-tile is-twitch">{_glyph("twitch")}</span>')
            with ui.element("div").classes("ce-look-t"):
                with ui.element("div").classes("ce-look-n"):
                    ui.label("Twitch API request")
                    _html(f'<span class="ce-auto">{_ic("verified")}Ids, client id and token are added for you</span>', "")
                with ui.element("div").classes("ce-row"):
                    ui.select({m: m for m in _HTTP_METHODS}, value=str(lk.get("method") or "GET").upper(),
                              on_change=lambda e: (lk.update(method=e.value), self._rerender(self.lookup_box, self._build_lookup))).props(
                        "outlined dense options-dense").style("width: 100px")
                    ui.input(value=lk.get("path", ""), placeholder="/channels/followers",
                             on_change=lambda e: (lk.update(path=(e.value or "").strip()), self.refresh_live())).props(
                        'outlined dense prefix="helix"').style("flex: 1 1 260px")
                self._params_editor(lk)
                if str(lk.get("method") or "GET").upper() != "GET":
                    with ui.element("div").classes("ce-varhost is-area"):
                        body = ui.textarea(value=lk.get("body", "") if isinstance(lk.get("body"), str) else json.dumps(lk.get("body"), indent=2),
                                           placeholder='{"title": "{command_message}"}',
                                           on_change=lambda e: (lk.update(body=e.value or ""), self.refresh_live())).props("outlined dense rows=3")
                        body.style("font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace")
                        self.vars.button(body)
                        self.vars.track(body)
                _html(f'<div class="ce-note">{_ic("info")}<span>Values can use <code>{{broadcaster_id}}</code>, <code>{{chatter_id}}</code>, '
                      '<code>{chatter_login}</code> and any variable. broadcaster_id / moderator_id are filled automatically when left empty. '
                      'The response gives you <code>{api_&lt;field&gt;}</code> (first result), <code>{api_total}</code> and <code>{api_status}</code>.</span></div>', "")
                with ui.element("div").classes("ce-row"):
                    ui.label("If it fails").classes("ce-mini")
                    with ui.element("div").classes("ce-varhost").style("flex: 1 1 260px"):
                        fb = ui.input(value=s.lookup_fallback, placeholder="Leave empty to skip the reply",
                                      on_change=lambda e: self._set("lookup_fallback", e.value or "")).props("outlined dense")
                        self.vars.button(fb)
                        self.vars.track(fb)
            ui.button("Change", icon="swap_horiz", on_click=lambda: (setattr(self, "_picking_lookup", True), self._rerender(self.lookup_box, self._build_lookup))).props(
                "outline no-caps dense color=primary")
            ui.button(icon="close", on_click=self._remove_lookup).props("flat round dense").classes("is-danger").tooltip("Remove request")

    def _params_editor(self, lk: Dict[str, Any]) -> None:
        params: Dict[str, Any] = lk["params"]
        rows = list(params.items())
        box = ui.element("div").classes("ce-stack")

        def write(items: List[Tuple[str, str]]) -> None:
            params.clear()
            for k, v in items:
                if str(k).strip():
                    params[str(k).strip()] = v
            self.refresh_live()

        def render() -> None:
            box.clear()
            with box:
                for idx, (k, v) in enumerate(rows):
                    with ui.element("div").classes("ce-kv"):
                        ui.input(value=k, placeholder="parameter", on_change=lambda e, i=idx: (rows.__setitem__(i, (e.value or "", rows[i][1])), write(rows))).props(
                            "outlined dense")
                        with ui.element("div").classes("ce-varhost"):
                            val = ui.input(value=str(v), placeholder="value or {variable}",
                                           on_change=lambda e, i=idx: (rows.__setitem__(i, (rows[i][0], e.value or "")), write(rows))).props("outlined dense")
                            self.vars.button(val)
                            self.vars.track(val)
                        ui.button(icon="close", on_click=lambda _e, i=idx: (rows.pop(i), write(rows), render())).props("flat round dense").classes("is-danger")
                add = ui.label("+ Add parameter").classes("ce-link").style("font-size: .74rem")
                add.on("click", lambda: (rows.append(("", "")), render()))

        render()

    # -- reply step -----------------------------------------------------------

    def _build_reply(self, container: ui.element) -> None:
        s = self.state
        if self.kind == "command" and s.command_type == "reset":
            _html(f'<div class="ce-note">{_ic("info")}<span>Optional for reset commands: leave empty to reset silently.</span></div>', "")
        with ui.element("div").classes("ce-box"):
            with ui.element("div").classes("ce-varhost is-area"):
                reply = ui.textarea(value=s.response_text, placeholder="Thanks {username}!" if self.kind == "event" else "Join the Discord: https://…",
                                    on_change=lambda e: self._set("response_text", e.value or "")).props("outlined dense autogrow")
                self.vars.button(reply)
                self.vars.track(reply)
            with ui.element("div").classes("ce-preview") as preview:
                _html(_ic("visibility"))
                preview_label = ui.label("")
            limit = ui.label("").classes("ce-f-error")
            unknown = ui.html("", sanitize=False).classes("ce-unknown")

            def upd() -> bool:
                if preview_label.is_deleted:
                    return False
                rendered = self.render_text(s.response_text)
                preview.set_visibility(bool(s.response_text.strip()))
                preview_label.set_text(rendered)
                too_long = len(rendered) > 500 and "twitch" in s.reply_targets and s.output_mode != "banner"
                limit.set_text(f"{len(rendered)} characters: Twitch allows 500 per message, so this will be split into parts." if too_long else "")
                limit.set_visibility(too_long)
                missing = self._unknown_tokens(s.response_text)
                unknown.set_content(
                    f'{_ic("help_outline")}<span>Not a variable here, so it is posted as written: '
                    + ", ".join(f"<code>{{{_esc(k)}}}</code>" + (f" (did you mean <code>{{{_esc(hint)}}}</code>?)" if hint else "") for k, hint in missing)
                    + "</span>" if missing else "")
                unknown.set_visibility(bool(missing))
                return True

            upd()
            self._live.append(upd)
            self.outputs_box = ui.element("div").classes("ce-out")
            with self.outputs_box:
                self._outputs()

    _SUGGEST = {"user": "username", "name": "username", "viewer": "username", "displayname": "username", "streamer": "stats",
                "bits": "amount", "viewers": "viewer_count", "arg1": "command_word_1", "arg2": "command_word_2", "args": "command_message"}

    def _unknown_tokens(self, text: str) -> List[Tuple[str, str]]:
        tokens = re.findall(r"\{([A-Za-z_][\w.\-]*)(?::[^{}]*)?\}", text or "")
        if not tokens:
            return []
        known = {v["key"] for v in self._variables()}
        out = []
        for tok in dict.fromkeys(tokens):
            if tok in known or tok.split(".", 1)[0] in ("stats", "youtube", "quote", "time") or tok.startswith("custom_"):
                continue
            if re.fullmatch(r"command_word_\d+|message\.word\.\d+|api_[\w.]+", tok):
                continue
            if any(k.endswith("_N") and re.fullmatch(re.escape(k[:-1]) + r"\d+", tok) for k in known):
                continue
            hint = self._SUGGEST.get(tok.lower(), "")
            hint = "" if hint == "stats" else hint
            out.append((tok, hint if hint in known else ""))
        return out

    def _outputs(self) -> None:
        s = self.state
        banner = s.output_mode == "banner"
        if not banner:
            ui.label("Send to").classes("ce-mini")
            for key, label in (("twitch", "Twitch"), ("youtube", "YouTube")):
                pill = ui.element("div").classes("ce-pill" + (" is-on" if key in s.reply_targets else ""))
                with pill:
                    _html(_glyph(key))
                    ui.label(label)
                pill.on("click", lambda _e, k=key: self._flip_target(k))
            ui.element("div").classes("ce-sep")
        ui.label("Discord").classes("ce-mini")
        for entry in list(s.discord_channels):
            with ui.element("div").classes("ce-dc"):
                _html(_glyph("discord"))
                ui.label(f"{entry.get('guild_name') or entry.get('guild_id')} / #{entry.get('channel_name') or entry.get('channel_id')}")
                ui.button(icon="close", on_click=lambda _e, e2=entry: self._remove_discord(e2)).props("flat round dense size=xs")
        with ui.button("Channel", icon="add").props("outline no-caps dense color=primary"):
            with ui.menu() as menu:
                self._discord_menu(menu)
        ui.element("div").classes("ce-spacer")
        ui.label("Show as banner").classes("ce-mini").tooltip("Show the reply on the chat overlay instead of posting it in chat")
        ui.switch(value=banner, on_change=lambda e: self._set_banner(bool(e.value))).props("dense")

    def _rerender_outputs(self) -> None:
        self.outputs_box.clear()
        with self.outputs_box:
            self._outputs()
        self.refresh_live()

    def _flip_target(self, key: str) -> None:
        s = self.state
        s.reply_targets = [t for t in s.reply_targets if t != key] if key in s.reply_targets else [t for t in ("twitch", "youtube") if t in s.reply_targets + [key]]
        self._rerender_outputs()

    def _set_banner(self, on: bool) -> None:
        self.state.output_mode = "banner" if on else "chat"
        if not on and not self.state.reply_targets:
            self.state.reply_targets = ["twitch"]
        self._rerender_outputs()

    def _remove_discord(self, entry: dict) -> None:
        key = (entry.get("guild_id"), entry.get("channel_id"))
        self.state.discord_channels = [e for e in self.state.discord_channels if (e.get("guild_id"), e.get("channel_id")) != key]
        self._rerender_outputs()

    def _discord_menu(self, menu: ui.menu) -> None:
        from .. import discord_service

        guilds: Dict[str, str] = {}
        try:
            if discord_service.discord_service.is_connected():
                guilds = {g["id"]: g.get("name") or g["id"] for g in discord_service.list_guilds()}
        except Exception:
            guilds = {}
        with ui.element("div").style("padding: 8px; width: 280px").classes("ce-stack"):
            if not guilds:
                ui.label("Connect Discord in Settings to list your servers.").classes("ce-f-help")
                return
            channels: Dict[str, str] = {}
            gsel = ui.select(guilds, label="Server", with_input=True).props("outlined dense options-dense").classes("w-full")
            csel = ui.select({}, label="Channel", with_input=True).props("outlined dense options-dense").classes("w-full")

            def on_guild(e) -> None:
                channels.clear()
                try:
                    channels.update({c["id"]: f"#{c.get('name') or c['id']}" for c in discord_service.list_text_channels(e.value)})
                except Exception:
                    pass
                csel.set_options(dict(channels), value=None)

            def add() -> None:
                if not gsel.value or not csel.value:
                    notify("Choose a server and a channel first", type="warning", source="chatbot",
                           hint="Pick both, then click Add.")
                    return
                entry = {"guild_id": gsel.value, "channel_id": csel.value, "guild_name": guilds.get(gsel.value, gsel.value),
                         "channel_name": channels.get(csel.value, csel.value).lstrip("#")}
                if any((e.get("guild_id"), e.get("channel_id")) == (entry["guild_id"], entry["channel_id"]) for e in self.state.discord_channels):
                    notify("That channel is already added", type="info", source="chatbot")
                    return
                self.state.discord_channels.append(entry)
                menu.close()
                self._rerender_outputs()

            gsel.on_value_change(on_guild)
            ui.button("Add channel", icon="add", on_click=add).props("unelevated no-caps dense color=primary")

    # -- variables panel and custom variables ---------------------------------

    def render_vars(self) -> None:
        tag = f"!{self.state.command_name}" if self.kind == "command" and self.state.command_name else (
            event_info(self.state.event_type).get("label", "") if self.state.event_type else "")
        empty = "" if (self.kind == "command" or self.state.event_type) else (
            "Choose an event to see its variables, like <code>{username}</code> or <code>{amount}</code>.")

        def footer(group: str) -> None:
            if group != "Custom":
                return
            add = ui.element("div").classes("ce-vadd")
            with add:
                _html(_ic("add"))
                ui.label("New custom variable")
            add.on("click", lambda: self.open_custom_variable())

        def tools(var: dict) -> None:
            if var.get("group") == "Custom" and str(var.get("key", "")).startswith("custom_"):
                ui.button(icon="edit").props("flat round dense size=xs").tooltip("Edit or delete this variable").on(
                    "click.stop", lambda _e, k=var["key"]: self.open_custom_variable(k))

        self.vars.panel(self.vars_panel, tag=tag, empty_html=empty, group_footer=footer,
                        groups_always={"Custom": "Your own calculated variables."}, row_tools=tools)

    def open_custom_variable(self, name: Optional[str] = None) -> None:
        open_custom_variable_builder(name, on_saved=lambda: (self.render_vars(), self.refresh_live()), render=self.render_text,
                                     variables=lambda: self._variables())

    # -- save -----------------------------------------------------------------

    def save(self, test_after: bool = False) -> None:
        s = self.state
        problems = state_problems(s)
        if problems:
            more = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
            notify(problems[0] + more, type="warning", source="chatbot", title=f"Can't save the {self.kind} yet",
                   hint="Fix this, then save again.")
            return
        try:
            item = build_item(s, self.existing)
        except Exception as exc:
            logger.error("Could not build chatbot %s: %s", self.kind, exc, exc_info=True)
            notify("Nothing was saved. Your changes are still in the editor.", type="error", source="chatbot",
                   title=f"Couldn't save the {self.kind}", hint="Check the fields for unusual values and try again.",
                   details=f"{type(exc).__name__}: {exc}")
            return
        manager = get_manager()
        validate = getattr(manager, "validate_command" if self.kind == "command" else "validate_event", None)
        if callable(validate):
            try:
                message = validate(item)
            except Exception:
                message = None
            if message:
                notify(message, type="warning", source="chatbot", title=f"Can't save the {self.kind} yet",
                       hint="Change it, then save again.")
                return
        error_details = ""
        try:
            if self.kind == "command":
                if s.is_new:
                    ok = manager.add_command(item)
                else:
                    # Keep live counters/usage unless the user edited the count here.
                    counter_edited = s.counter_value != int(getattr(self.existing, "counter_value", 0) or 0)
                    ok = manager.update_command(s.item_id, item, keep_runtime=not counter_edited)
            else:
                ok = manager.add_event(item) if s.is_new else manager.update_event(s.item_id, item)
        except Exception as exc:
            logger.error("Saving chatbot %s failed: %s", self.kind, exc, exc_info=True)
            ok = False
            error_details = f"{type(exc).__name__}: {exc}"
        if not ok:
            gone = not s.is_new and not error_details
            notify(
                f"This {self.kind} was deleted while you were editing it. Close the editor and create it again."
                if gone else "Nothing was saved. Your changes are still in the editor.",
                type="error",
                source="chatbot",
                title=f"Couldn't save the {self.kind}",
                hint="Copy your text, close the editor and create a new one." if gone else "Try again.",
                details=error_details or None,
            )
            return
        notify(f"{'Created' if s.is_new else 'Saved'} “{item.name}”", type="success", source="chatbot")
        self._baseline = s.snapshot()
        self.dialog.close()
        try:
            self.on_saved()
        except Exception as exc:
            logger.debug("on_saved failed: %s", exc)
        if test_after:
            run_item_test(self.kind, s.item_id)


# ---------------------------------------------------------------------------
#  Custom variable builder
# ---------------------------------------------------------------------------

_CV_KINDS = (
    ("math", "calculate", "Math", "Add, subtract, multiply, divide"),
    ("if", "rule", "If / else", "Pick text by a rule"),
    ("age", "history", "How long ago", "Time since a date"),
    ("text", "text_fields", "Text", "Join variables and text"),
    ("twitch", "travel_explore", "Twitch data", "A value from Twitch"),
)
_CV_OPS = {"+": "+ add", "-": "− subtract", "*": "× multiply", "/": "÷ divide", "random": "🎲 random between"}
_CV_AGE_STYLES = {"long": "2 years, 3 months", "years": "Years (2)", "months": "Months (27)", "days": "Days (812)", "hours": "Hours (19488)"}
_CV_DATE_OTHER = "__typed__"
_CV_COMPARE = {"equal": "is", "not_equal": "is not", "greater_than": "more than", "greater_than_or_equal": "at least",
               "less_than": "less than", "less_than_or_equal": "at most", "contains": "contains"}


def _compile_fallback(kind: str, params: Dict[str, Any]) -> str:
    if kind == "math":
        expr = f"{params.get('left', '0')} {params.get('op', '+')} {params.get('right', '0')}"
        d = params.get("decimals")
        return f"math(round({expr}, {int(d)}))" if d not in (None, "") else f"math({expr})"
    if kind == "age":
        return f"date_to_age({params.get('date', '')})"
    if kind == "text":
        return str(params.get("template", ""))
    if kind == "if":
        return f"compare({params.get('left', '')}, {params.get('op', 'equal')}, {params.get('right', '')})"
    return str(params.get("expression", ""))


def open_custom_variable_builder(name: Optional[str], on_saved: Callable[[], None], render: Callable[[str], str],
                                 variables: Callable[[], List[dict]]) -> None:
    mod = _cvars()
    defs: Dict[str, Any] = {}
    if mod is not None and hasattr(mod, "load_custom_definitions"):
        try:
            defs = dict(mod.load_custom_definitions() or {})
        except Exception as exc:
            logger.debug("Could not load custom variables: %s", exc)
    key = f"custom_{name}" if name and not name.startswith("custom_") else name
    existing = copy.deepcopy(defs.get(key)) if key else None
    state = {
        "name": (key or "")[7:] if key else "",
        "kind": (existing or {}).get("kind", "math"),
        "params": dict((existing or {}).get("params") or {}),
        "description": (existing or {}).get("description", ""),
    }
    if state["kind"] == "expression":
        state["params"].setdefault("expression", (existing or {}).get("expression", ""))
    kit = VariableKit(lambda _i=None, _e=None: [v for v in variables() if v.get("group") != "Custom" or v.get("key") != key], render, date_formats)

    def compile_expr() -> str:
        fn = getattr(mod, "compile_custom_variable", None) if mod else None
        if callable(fn):
            try:
                return fn(state["kind"], state["params"])
            except Exception as exc:
                return f"⚠ {exc}"
        return _compile_fallback(state["kind"], state["params"])

    def preview() -> Tuple[bool, str]:
        fn = getattr(mod, "preview_custom_variable", None) if mod else None
        if not callable(fn):
            return True, render(compile_expr())
        try:
            text = str(fn({"kind": state["kind"], "params": state["params"], "expression": compile_expr()}, None))
        except Exception as exc:
            return False, str(exc)
        if text.startswith("Error:"):
            return False, text[6:].strip()
        return True, text

    with ui.dialog().props("persistent") as dialog, ui.card().classes("cxe cv"):
        with ui.element("div").classes("cv-head"):
            _html(f'<span class="ce-tile">{_ic("functions")}</span>')
            with ui.element("div").style("flex: 1 1 auto"):
                ui.label("Edit custom variable" if existing else "New custom variable").classes("ce-step-title")
                ui.label("Works in every command and event reply.").classes("ce-step-sub")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense")
        with ui.element("div").classes("cv-body"):
            with ui.element("div").classes("ce-row"):
                ui.label("Name").classes("ce-mini")
                ui.label("{custom_").classes("ce-unit").style("font-family: ui-monospace, SFMono-Regular, Menlo, monospace; margin-right: -6px")
                name_in = ui.input(value=state["name"], placeholder="hours_live").props("outlined dense").style("width: 220px")
                ui.label("}").classes("ce-unit").style("font-family: ui-monospace, SFMono-Regular, Menlo, monospace; margin-left: -6px")
                if existing:
                    name_in.props("readonly")
                name_in.on_value_change(lambda e: (state.update(name=re.sub(r"[^a-z0-9_]", "_", (e.value or "").strip().lower())), refresh()))
            kinds = ui.element("div").classes("cv-kinds")
            builder = ui.element("div").classes("cv-builder")
            expr_label = ui.html("", sanitize=False).classes("cv-expr")
            result = ui.element("div").classes("cv-result")
            with result:
                result_icon = _html(_ic("visibility"))
                result_text = ui.html("", sanitize=False)
        with ui.element("div").classes("cv-foot"):
            error = ui.label("").classes("ce-f-error")
            ui.element("div").classes("ce-spacer")
            if existing:
                def delete() -> None:
                    fn = getattr(mod, "delete_custom_definition", None) if mod else None
                    if callable(fn) and fn(key):
                        notify(f"Deleted {{{key}}}", type="success", source="chatbot")
                        dialog.close()
                        on_saved()
                    else:
                        error.set_text("Couldn't delete the variable. Check Settings → Database and try again.")

                ui.button("Delete", icon="delete", on_click=delete).props("flat no-caps color=negative")
            ui.button("Cancel", on_click=dialog.close).props("outline no-caps color=primary")
            ui.button("Save variable" if existing else "Create variable", icon="check", on_click=lambda: save()).props("unelevated no-caps color=primary")

    def field(label: str, pkey: str, placeholder: str = "", width: str = "", default: str = "") -> None:
        state["params"].setdefault(pkey, default)
        with ui.element("div").classes("ce-varhost").style(f"flex: 1 1 {width or '160px'}"):
            inp = ui.input(value=str(state["params"].get(pkey, "")), placeholder=placeholder or label,
                           on_change=lambda e: (state["params"].__setitem__(pkey, e.value or ""), refresh())).props("outlined dense")
            kit.button(inp)
            kit.track(inp)

    def select(pkey: str, options: Dict[Any, str], default: Any, width: str = "140px", rebuild: bool = False) -> None:
        if pkey not in state["params"] or state["params"][pkey] not in options:
            state["params"][pkey] = default

        def changed(e) -> None:
            state["params"][pkey] = e.value
            if rebuild:
                render_builder()
            refresh()

        ui.select(options, value=state["params"][pkey], on_change=changed).props("outlined dense options-dense").style(f"width: {width}")

    def date_options() -> Dict[str, str]:
        opts: Dict[str, str] = {}
        clock = {"time", "date", "datetime", "timestamp", "triggered_at"}  # "now": always 0
        for var in variables():
            if var.get("date") and var["key"] not in clock:
                opts["{" + var["key"] + "}"] = f"{var.get('label') or var['key']}  {{{var['key']}}}"
        lk = _lk()
        for entry in (getattr(lk, "LOOKUPS", None) or []) if lk else []:
            for var in entry.get("variables") or []:
                if var.get("date"):
                    opts.setdefault("{" + var["key"] + "}", f"{var.get('label') or var['key']}  {{{var['key']}}}  · {entry.get('label', '')}")
        preferred = "{followed_since}"
        if preferred in opts:
            opts = {preferred: opts.pop(preferred), **opts}
        opts[_CV_DATE_OTHER] = "A date I type…"
        return opts

    def twitch_builder() -> None:
        params = state["params"]
        params.setdefault("source", "lookup")
        with ui.element("div").classes("ce-row"):
            ui.label("Get it from").classes("ce-mini")
            with ui.element("div").classes("ce-seg"):
                for value, icon, label in (("lookup", "travel_explore", "A Twitch lookup"), ("request", "tune", "An API request (advanced)")):
                    opt = ui.element("div").classes("is-on" if params["source"] == value else "")
                    with opt:
                        _html(_ic(icon))
                        ui.label(label)
                    opt.on("click", lambda _e, v=value: (params.update(source=v, field=""), render_builder(), refresh()))
        if params["source"] == "lookup":
            entries = [e for e in lookups_catalog() if e.get("group") != "Do something"]
            opts = {e["id"]: f"{e.get('group', '')} · {e.get('label', e['id'])}" for e in entries}
            if not opts:
                ui.label("Twitch lookups aren't available right now.").classes("ce-f-help")
                return
            with ui.element("div").classes("ce-row"):
                ui.label("Look up").classes("ce-mini")
                select("lookup", opts, next(iter(opts)), "300px", rebuild=True)
            entry = lookup_info(params.get("lookup"))
            fields = {v["key"]: f"{v.get('label') or v['key']}  {{{v['key']}}}" for v in entry.get("variables") or [] if v.get("key") != "lookup_error"}
            if entry.get("target"):
                target = params.setdefault("target", {"from": "chatter"})
                with ui.element("div").classes("ce-row"):
                    ui.label("About").classes("ce-mini")

                    def set_from(e) -> None:
                        params["target"] = {"from": e.value, "text": params.get("target", {}).get("text", "")}
                        render_builder()
                        refresh()

                    ui.select({"chatter": "Whoever triggered the reply", "text": "A name I type…"}, value=target.get("from", "chatter"),
                              on_change=set_from).props("outlined dense options-dense").style("width: 230px")
                    if target.get("from") == "text":
                        with ui.element("div").classes("ce-varhost").style("flex: 1 1 200px"):
                            inp = ui.input(value=target.get("text", ""), placeholder="friendlystreamer or {command_word_1}",
                                           on_change=lambda e: (params["target"].update(text=e.value or ""), refresh())).props("outlined dense")
                            kit.button(inp)
                            kit.track(inp)
            for opt in entry.get("options") or []:
                choices = opt.get("choices") or {}
                if choices:
                    with ui.element("div").classes("ce-row"):
                        ui.label(opt.get("label", "Option")).classes("ce-mini")
                        select(opt.get("key", "period"), dict(choices), opt.get("default", next(iter(choices))), "170px")
            with ui.element("div").classes("ce-row"):
                ui.label("Use").classes("ce-mini")
                if fields:
                    select("field", fields, next(iter(fields)), "300px")
        else:
            with ui.element("div").classes("ce-row"):
                ui.label("GET").classes("ce-mini")
                ui.input(value=params.get("path", ""), placeholder="/users",
                         on_change=lambda e: (params.update(path=(e.value or "").strip()), refresh())).props('outlined dense prefix="helix"').style(
                    "flex: 1 1 240px")
            rows = list((params.get("params") or {}).items())
            box = ui.element("div").classes("ce-stack")

            def write() -> None:
                params["params"] = {k.strip(): v for k, v in rows if str(k).strip()}
                refresh()

            def render_rows() -> None:
                box.clear()
                with box:
                    for idx, (k, v) in enumerate(rows):
                        with ui.element("div").classes("ce-kv"):
                            ui.input(value=k, placeholder="parameter",
                                     on_change=lambda e, i=idx: (rows.__setitem__(i, (e.value or "", rows[i][1])), write())).props("outlined dense")
                            with ui.element("div").classes("ce-varhost"):
                                val = ui.input(value=str(v), placeholder="value or {variable}",
                                               on_change=lambda e, i=idx: (rows.__setitem__(i, (rows[i][0], e.value or "")), write())).props("outlined dense")
                                kit.button(val)
                                kit.track(val)
                            ui.button(icon="close", on_click=lambda _e, i=idx: (rows.pop(i), write(), render_rows())).props(
                                "flat round dense").classes("is-danger")
                    add = ui.label("+ Add parameter").classes("ce-link").style("font-size: .74rem")
                    add.on("click", lambda: (rows.append(("", "")), render_rows()))

            render_rows()
            with ui.element("div").classes("ce-row"):
                ui.label("Use").classes("ce-mini")
                ui.input(value=params.get("field", ""), placeholder="total, title, created_at…",
                         on_change=lambda e: (params.update(field=(e.value or "").strip()), refresh())).props("outlined dense").style("width: 240px")
                ui.label("from the first result").classes("ce-unit")
            _html(f'<div class="ce-note">{_ic("info")}<span>broadcaster_id and moderator_id are filled in for you; use '
                  '<code>{chatter_id}</code>, <code>{chatter_login}</code> or any variable in values.</span></div>', "")
        with ui.element("div").classes("ce-row"):
            ui.label("If it fails").classes("ce-mini")
            field("Text", "fallback", "Leave empty for nothing", "260px")
        _html(f'<div class="ce-note">{_ic("info")}<span>The preview uses example data. The real value comes from Twitch when a reply '
              'uses this variable (kept for 30 seconds). To change it, use this variable inside a Math or If / else variable.</span></div>', "")

    def render_kinds() -> None:
        kinds.clear()
        with kinds:
            options = list(_CV_KINDS) + ([("expression", "code", "Expression", "Written by hand")] if state["kind"] == "expression" else [])
            for value, icon, title, desc in options:
                k = ui.element("div").classes("cv-kind" + (" is-on" if value == state["kind"] else ""))
                with k:
                    _html(f'<div class="cv-kind-t">{_ic(icon)}{_esc(title)}</div><div class="cv-kind-d">{_esc(desc)}</div>', "contents")
                k.on("click", lambda _e, v=value: pick_kind(v))

    def pick_kind(kind: str) -> None:
        if kind != state["kind"]:
            state["kind"] = kind
            state["params"] = {}
            state.pop("typed_date", None)
            render_kinds()
            render_builder()
            refresh()

    def render_builder() -> None:
        builder.clear()
        with builder:
            kind = state["kind"]
            if kind == "math":
                rand = state["params"].get("op") == "random"
                with ui.element("div").classes("ce-row"):
                    if rand:
                        ui.label("Between").classes("ce-mini")
                    field("Lowest" if rand else "Number or variable", "left", "1" if rand else "{stats.session.session_duration}", "180px" if rand else "220px")
                    select("op", _CV_OPS, "/", "170px", rebuild=True)
                    field("Highest" if rand else "Number or variable", "right", "100" if rand else "3600", "160px" if rand else "120px")
                if rand:
                    _html(f'<div class="ce-note">{_ic("casino")}<span>Picks a new number every time. Whole numbers give whole results; '
                          'use 0.5 or 2.25 for decimals.</span></div>', "")
                with ui.element("div").classes("ce-row"):
                    ui.label("Round to").classes("ce-mini")
                    select("decimals", {None: "Don't round", 0: "Whole number", 1: "1 decimal", 2: "2 decimals"}, None, "150px")
            elif kind == "if":
                with ui.element("div").classes("ce-row"):
                    ui.label("If").classes("ce-mini")
                    field("Variable", "left", "{count}", "180px")
                    select("op", _CV_COMPARE, "greater_than_or_equal", "130px")
                    field("Value", "right", "100", "120px")
                with ui.element("div").classes("ce-row"):
                    ui.label("Then").classes("ce-mini")
                    field("Text", "then", "That's a lot of deaths!", "220px")
                    ui.label("Else").classes("ce-mini")
                    field("Text", "else", "Still going strong", "220px")
            elif kind == "age":
                opts = date_options()
                current = str(state["params"].get("date") or "")
                # "A date I type…" starts with no date; remember the pick so it stays chosen.
                typed = bool(current) or state.get("typed_date", False)
                choice = current if current in opts else (_CV_DATE_OTHER if typed else next(iter(opts)))
                if not current and choice != _CV_DATE_OTHER:
                    state["params"]["date"] = choice
                with ui.element("div").classes("ce-row"):
                    ui.label("Since").classes("ce-mini")

                    def pick_date(e) -> None:
                        state["typed_date"] = e.value == _CV_DATE_OTHER
                        state["params"]["date"] = "" if e.value == _CV_DATE_OTHER else e.value
                        render_builder()
                        refresh()

                    ui.select(opts, value=choice, on_change=pick_date).props("outlined dense options-dense").style("width: 380px")
                    if choice == _CV_DATE_OTHER:
                        ui.input(value=current, on_change=lambda e: (state["params"].__setitem__("date", e.value or ""), refresh())).props(
                            "outlined dense type=date").style("width: 170px")
                with ui.element("div").classes("ce-row"):
                    ui.label("Show as").classes("ce-mini")
                    select("style", _CV_AGE_STYLES, "long", "190px")
                _html(f'<div class="ce-note">{_ic("info")}<span>Shows how long ago the date was. Dates from a Twitch lookup (follow date, '
                      'account created, live since) only have a value in replies that use that lookup.</span></div>', "")
            elif kind == "text":
                field("Text", "template", "{username} has {count} points", "100%")
            elif kind == "twitch":
                twitch_builder()
            else:
                field("Expression", "expression", "math({count} * 2)", "100%")

    def refresh() -> None:
        expr = compile_expr()
        expr_label.set_content(f"Expression: <b>{_esc(expr)}</b>")
        ok, text = preview()
        result.classes(remove="is-error", add="" if ok else "is-error")
        result_icon.set_content(_ic("visibility" if ok else "error_outline"))
        result_text.set_content(f"Right now this gives <b>{_esc(text) or '(empty)'}</b>" if ok else f"Can't work this out yet: <b>{_esc(text)}</b>")
        error.set_text("")

    def save() -> None:
        nm = state["name"]
        if not nm or not _NAME_RE.match(nm):
            error.set_text("Use a name with lowercase letters, digits and _ (starting with a letter).")
            return
        full = f"custom_{nm}"
        if not existing and full in defs:
            error.set_text(f"{{{full}}} already exists.")
            return
        definition = {"kind": state["kind"], "params": state["params"], "expression": compile_expr(), "description": state["description"]}
        fn = getattr(mod, "save_custom_definition", None) if mod else None
        if not callable(fn) or not fn(full, definition):
            error.set_text("Couldn't save the variable. Check Settings → Database and try again.")
            return
        notify(f"Saved {{{full}}}", type="success", source="chatbot")
        dialog.close()
        on_saved()

    render_kinds()
    render_builder()
    refresh()
    dialog.open()


def open_chatbot_editor(kind: str, item_id: Optional[str], on_saved: Callable[[], None]) -> ChatbotEditor:
    editor = ChatbotEditor(kind, item_id, on_saved)
    editor.open()
    return editor

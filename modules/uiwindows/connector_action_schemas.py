# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Field schemas for connector actions, shared by the editor's render, save and edit-load.

Each simple action type is described by a list of ``Field``\\ s. The editor draws them,
writes every visible field's default into the config as soon as it is shown (so what you
see is what gets saved), drops keys of fields that are hidden (so switching a mode never
leaves stale values behind), and converts the config to and from the action's dataclass
fields. Types marked ``custom`` (template control, Discord, OBS, game hooks) have their
own builders in ``connector_editor`` but still use ``config_to_kwargs`` /
``action_to_config`` from here.

No NiceGUI import: this module is unit-tested on its own.
"""

from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

Options = Union[Dict[str, str], Callable[[dict], Dict[str, str]]]


@dataclass
class Field:
    key: str
    label: str
    kind: str = "text"  # text | textarea | number | select | toggle | json | targets | slider
    default: Any = ""
    options: Optional[Options] = None
    variables: bool = False  # offers the insert-variable button and is substituted at runtime
    preview: bool = False  # shows a live preview with example values
    show: Optional[Callable[[dict], bool]] = None
    width: str = "full"  # full | half | third
    unit: str = ""
    help: str = ""
    placeholder: str = ""
    required: bool = False
    rerender: bool = False  # other fields depend on this one
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    integer: bool = False
    strict: bool = True  # select: replace a stored value that is not one of the options
    clears: Tuple[str, ...] = ()  # keys to forget when this field changes

    def visible(self, cfg: dict) -> bool:
        return self.show is None or bool(self.show(cfg))

    def option_map(self, cfg: dict) -> Dict[str, str]:
        if self.options is None:
            return {}
        return dict(self.options(cfg) if callable(self.options) else self.options)


@dataclass
class ActionSpec:
    value: str
    label: str
    group: str
    icon: str  # material icon or service brand key
    hint: str = ""
    fields: List[Field] = field(default_factory=list)
    custom: bool = False
    warning: str = ""


def _is(key: str, *values: str) -> Callable[[dict], bool]:
    return lambda cfg: str(cfg.get(key, "")) in values


def _not(key: str, *values: str) -> Callable[[dict], bool]:
    return lambda cfg: str(cfg.get(key, "")) not in values


# ---------------------------------------------------------------------------
#  Option providers (runtime lists are looked up lazily)
# ---------------------------------------------------------------------------


def _alert_type_options(_cfg: dict) -> Dict[str, str]:
    try:
        from ..connector_variables import ALERT_TYPES

        return {v: label for v, label in ALERT_TYPES}
    except Exception:
        return {
            "bit": "Bits", "sub": "Subscription", "resub": "Resub", "giftsub": "Gift sub", "streak": "Watch streak",
            "donation": "Donation", "raid": "Raid", "follow": "Follow", "point": "Channel Points",
        }


_OPTION_CACHE: Dict[str, Tuple[float, Dict[str, str]]] = {}
_OPTION_TTL = 30.0


def _cached_options(name: str, load: Callable[[], Dict[str, str]]) -> Dict[str, str]:
    """Device and app lists shell out on some platforms; reuse them for a short while."""
    now = time.monotonic()
    hit = _OPTION_CACHE.get(name)
    if hit and now - hit[0] < _OPTION_TTL:
        return dict(hit[1])
    try:
        opts = dict(load() or {})
    except Exception:
        opts = {}
    _OPTION_CACHE[name] = (now, opts)
    return dict(opts)


def _audio_device_options(_cfg: dict) -> Dict[str, str]:
    def load() -> Dict[str, str]:
        from ..audio_targets import get_available_audio_devices

        return get_available_audio_devices()

    return _cached_options("audio_devices", load) or {"": "Default device"}


def _audio_app_options(_cfg: dict) -> Dict[str, str]:
    def load() -> Dict[str, str]:
        from ..audio_targets import get_available_applications

        return get_available_applications()

    return _cached_options("audio_apps", load)


def _audio_mode_options(cfg: dict) -> Dict[str, str]:
    if cfg.get("control_type") == "application":
        return {"set": "Set volume", "mute": "Mute", "unmute": "Unmute", "toggle_mute": "Toggle mute"}
    return {
        "set": "Set volume", "set_random": "Set a random volume", "increase": "Turn up", "decrease": "Turn down",
        "mute": "Mute", "unmute": "Unmute", "toggle_mute": "Toggle mute",
    }


_COLOR_OPTIONS = {"primary": "Channel colour", "blue": "Blue", "green": "Green", "orange": "Orange", "purple": "Purple"}
_HTTP_METHODS = {m: m for m in ("GET", "POST", "PUT", "PATCH", "DELETE")}
_MOUSE_BUTTONS = {"left_click": "Left click", "right_click": "Right click", "middle_click": "Middle click"}


# ---------------------------------------------------------------------------
#  Specs
# ---------------------------------------------------------------------------

ACTION_GROUPS: Tuple[Tuple[str, str], ...] = (
    ("Chat & social", "forum"),
    ("Alerts & overlays", "notifications_active"),
    ("OBS", "obs"),
    ("Input & audio", "keyboard"),
    ("Files & web", "language"),
    ("Games", "sports_esports"),
)

ACTION_SPECS: Dict[str, ActionSpec] = {}


def _add(spec: ActionSpec) -> None:
    ACTION_SPECS[spec.value] = spec


_add(ActionSpec("send_chat_message", "Send chat message", "Chat & social", "chat", "Post in Twitch or YouTube chat", [
    Field("message", "Message", "textarea", variables=True, preview=True, required=True,
          placeholder="Thanks {username}!"),
    Field("reply_targets", "Send to", "targets", default=["twitch"]),
]))
_add(ActionSpec("send_announcement", "Send announcement", "Chat & social", "campaign", "Highlighted Twitch announcement", [
    Field("message", "Announcement", "textarea", variables=True, preview=True, required=True),
    Field("color", "Colour", "select", default="primary", options=_COLOR_OPTIONS, width="half"),
]))
_add(ActionSpec("send_discord_message", "Send Discord message", "Chat & social", "discord", "Post to Discord channels", [
    Field("message", "Message", "textarea", variables=True, preview=True, required=True),
], custom=True))
_add(ActionSpec("add_greeting", "Add greeting", "Chat & social", "waving_hand", "Save a custom greeting for a viewer", [
    Field("username", "Viewer name", default="{username}", variables=True, width="half"),
    Field("user_id", "Viewer ID", default="{user_id}", variables=True, width="half"),
    Field("greeting_text", "Greeting", "textarea", variables=True, preview=True, required=True),
    Field("greeting_enabled", "Greeting turned on", "toggle", default=True),
]))
_add(ActionSpec("update_greeting", "Update greeting", "Chat & social", "edit_note", "Change a saved greeting", [
    Field("greeting_id", "Greeting ID", default="{greeting_id}", variables=True, required=True),
    Field("greeting_text", "New greeting (leave empty to keep)", "textarea", variables=True, preview=True),
    Field("greeting_enabled", "Greeting on/off", "select", default="",
          options={"": "Don't change", "on": "Turn on", "off": "Turn off"}, width="half"),
]))
_add(ActionSpec("send_greeting", "Send greeting", "Chat & social", "record_voice_over", "Post a viewer's saved greeting", [
    Field("username", "Viewer name", default="{username}", variables=True, width="half"),
    Field("user_id", "Viewer ID", default="{user_id}", variables=True, width="half"),
    Field("force_send", "Send even if greeted recently", "toggle", default=True),
]))

_add(ActionSpec("trigger_alert", "Play an alert", "Alerts & overlays", "notifications_active",
                "Play one of your Alerts tab alerts", [
    Field("alert_type", "Alert", "select", default="bit", options=_alert_type_options, width="half", required=True),
    Field("amount", "Amount", default="{amount}", variables=True, width="half",
          help="Picks which alert plays (bits, months, gifts, viewers or donation amount)."),
    Field("username", "Viewer name", default="{username}", variables=True, width="half"),
    Field("reward_name", "Reward name", default="{reward_name}", variables=True, width="half",
          show=_is("alert_type", "point")),
    Field("message", "Message", "textarea", default="{message}", variables=True, preview=True),
]))
_add(ActionSpec("display_banner", "Show a banner", "Alerts & overlays", "featured_video", "Scroll a message on the banner overlay", [
    Field("message", "Banner text", "textarea", variables=True, preview=True, required=True),
    Field("permanent", "Keep showing until replaced", "toggle", default=False, rerender=True),
    Field("duration", "Show for", "number", default=10, unit="sec", min=1, step=1, width="third",
          show=lambda cfg: not cfg.get("permanent")),
]))
_add(ActionSpec("template_control", "Control an overlay", "Alerts & overlays", "widgets", "Run an action on a template overlay", custom=True))
_add(ActionSpec("websocket_emit", "Send overlay event", "Alerts & overlays", "settings_ethernet", "Advanced: emit a custom WebSocket event", [
    Field("event_name", "Event name", required=True, placeholder="custom_event", width="half"),
    Field("event_data", "Event data (JSON)", "json", default="{}", variables=True,
          placeholder='{"user": "{username}", "amount": {amount}}'),
]))

_add(ActionSpec("obs_control", "OBS action", "OBS", "obs", "Switch scenes, toggle sources, start/stop and more", custom=True))

_add(ActionSpec("key_press", "Press keys / click", "Input & audio", "keyboard", "Simulate keyboard or mouse input", [
    Field("input_type", "Input", "select", default="key", width="third", rerender=True, clears=("key_sequence",),
          options={"key": "Keyboard", "mouse": "Mouse", "macro": "Macro (sequence)"}),
    Field("action_mode", "Mode", "select", default="press", width="third", rerender=True,
          options=lambda cfg: ({"press": "Single click", "hold": "Press and hold", "repeat": "Several clicks"}
                               if cfg.get("input_type") == "mouse" else
                               {"press": "Press and release", "hold": "Press and hold", "repeat": "Repeat"}),
          show=_not("input_type", "macro")),
    Field("key_sequence", "Keys", variables=True, required=True, width="third", placeholder="ctrl+shift+b",
          help="Use + to combine keys: ctrl+c, alt+tab, f12, up.", show=_is("input_type", "key")),
    Field("key_sequence", "Button", "select", default="left_click", options=_MOUSE_BUTTONS, width="third",
          show=_is("input_type", "mouse")),
    Field("hold_duration", "Hold for", "number", default=0.5, unit="sec", min=0.05, step=0.1, width="third",
          show=lambda cfg: cfg.get("input_type") != "macro" and cfg.get("action_mode") == "hold"),
    Field("repeat_count", "Times", "number", default=3, min=1, step=1, integer=True, width="third",
          show=lambda cfg: cfg.get("input_type") != "macro" and cfg.get("action_mode") == "repeat"),
    Field("repeat_interval", "Every", "number", default=0.1, unit="sec", min=0.01, step=0.05, width="third",
          show=lambda cfg: cfg.get("input_type") != "macro" and cfg.get("action_mode") == "repeat"),
    Field("macro_sequence", "Steps", "macro", default="[]", show=_is("input_type", "macro")),
]))
_add(ActionSpec("audio_control", "Change volume", "Input & audio", "volume_up", "Set, mute or change a device or app volume", [
    Field("control_type", "Control", "select", default="device", width="third", rerender=True,
          options={"device": "Audio device", "application": "An app"}),
    Field("target_device", "Device", "select", default="", options=_audio_device_options, width="third",
          show=_is("control_type", "device"), strict=False),
    Field("target_application", "App", "select", default="", options=_audio_app_options, width="third",
          show=_is("control_type", "application"), help="Pick a running app.", strict=False),
    Field("action_mode", "Do", "select", default="set", options=_audio_mode_options, width="third", rerender=True),
    Field("volume_level", "Volume", "slider", default=50, unit="%", min=0, max=100, step=1,
          show=_is("action_mode", "set")),
    Field("volume_step", "By", "number", default=10, unit="%", min=1, max=100, step=1, width="third",
          show=_is("action_mode", "increase", "decrease")),
    Field("duration", "Undo after", "number", default=0, unit="sec (0 = keep)", min=0, step=1, width="third",
          help="Restore the previous volume after this many seconds."),
]))

_add(ActionSpec("api_call", "Web request", "Files & web", "http", "Call a URL (webhooks, home automation, APIs)", [
    Field("method", "Method", "select", default="POST", options=_HTTP_METHODS, width="third", rerender=True),
    Field("url", "URL", variables=True, required=True, placeholder="https://example.com/hook", width="full"),
    Field("headers", "Headers (JSON)", "json", default="{}", variables=True, placeholder='{"Authorization": "Bearer …"}'),
    Field("body", "Body", "json", default="", variables=True, show=_not("method", "GET"),
          placeholder='{"user": "{username}", "amount": {amount}}',
          help="JSON is sent as JSON; anything else is sent as plain text."),
]))
_add(ActionSpec("write_file", "Write to a file", "Files & web", "description", "Write or append text to a file", [
    Field("file_path", "File", variables=True, required=True, placeholder="logs/events.txt",
          help="Relative paths are inside the Mycelian folder."),
    Field("content", "Text", "textarea", variables=True, preview=True, placeholder="{time} {username}: {message}"),
    Field("append", "Add to the end instead of replacing", "toggle", default=True),
]))
_add(ActionSpec("execute_command", "Run a command", "Files & web", "terminal", "Run a program or script on this computer", [
    Field("command", "Command", variables=True, required=True, placeholder='say "Thanks {username}"',
          help="Variables are inserted as safe, quoted text, so viewers can't run extra commands."),
    Field("working_directory", "Run in folder", variables=True, width="half", placeholder="(Mycelian folder)"),
    Field("timeout_seconds", "Stop after", "number", default=30, unit="sec", min=1, step=1, integer=True, width="third"),
], warning="Runs programs on your computer. Only use commands you trust."))

_add(ActionSpec("game_hook", "Game hook", "Games", "sports_esports", "Change values in a supported game (FF7)", custom=True))


# Keys stored in the editor config that are not action kwargs.
_BASE_ACTION_FIELDS = {"action_id", "action_type", "name", "description", "enabled", "delay_seconds", "parameters", "metadata"}
_JSON_FIELDS = {"websocket_emit": ("event_data",), "api_call": ("headers", "body")}


def spec_for(action_type: Optional[str]) -> Optional[ActionSpec]:
    return ACTION_SPECS.get(action_type or "")


def visible_fields(spec: ActionSpec, cfg: dict) -> List[Field]:
    seen, out = set(), []
    for f in spec.fields:
        if f.visible(cfg) and f.key not in seen:
            seen.add(f.key)
            out.append(f)
    return out


def apply_defaults(spec: ActionSpec, cfg: dict) -> dict:
    """Write defaults for visible fields that have no value yet; drop keys of hidden schema fields.

    Repeats until stable because a default can reveal more fields (e.g. input type → mode).
    Select fields get their first option when the stored value is not one of the options.
    """
    if spec.custom and not spec.fields:
        return cfg
    for _ in range(5):
        before = dict(cfg)
        visible = visible_fields(spec, cfg)
        visible_keys = {f.key for f in visible}
        for f in spec.fields:
            if f.key not in visible_keys and f.key in cfg and not spec.custom:
                cfg.pop(f.key, None)
        for f in visible:
            if f.key not in cfg or cfg[f.key] is None:
                cfg[f.key] = list(f.default) if isinstance(f.default, list) else f.default
            if f.kind == "select":
                opts = f.option_map(cfg)
                current = cfg.get(f.key)
                if opts and str(current) not in opts and (f.strict or current in ("", None)):
                    cfg[f.key] = f.default if str(f.default) in opts else next(iter(opts))
        if cfg == before:
            break
    return cfg


def _coerce_number(f: Field, value: Any) -> Any:
    try:
        num = float(value)
    except (TypeError, ValueError):
        num = float(f.default or 0)
    if f.min is not None:
        num = max(f.min, num)
    if f.max is not None:
        num = min(f.max, num)
    return int(round(num)) if f.integer else num


def json_error(text: Any, context: Optional[dict] = None) -> str:
    """'' when ``text`` is empty or valid JSON after substituting example values, else the parse error."""
    if not isinstance(text, str) or not text.strip():
        return ""
    candidate = text
    if context is not None:
        try:
            from ..connector_placeholders import substitute_connector_placeholders

            candidate = substitute_connector_placeholders(text, context)
        except Exception:
            candidate = text
    try:
        json.loads(candidate)
        return ""
    except json.JSONDecodeError as exc:
        return f"Not valid JSON ({exc.msg}, line {exc.lineno})."


def config_problems(action_type: Optional[str], cfg: dict) -> List[str]:
    """Human-readable reasons this action can't be saved (empty list when fine)."""
    spec = spec_for(action_type)
    if spec is None:
        return ["Choose what this action does."]
    problems = []
    for f in visible_fields(spec, cfg):
        value = cfg.get(f.key)
        if f.required and (value is None or (isinstance(value, str) and not value.strip()) or value == []):
            problems.append(f"{f.label} is empty.")
    if action_type == "send_chat_message" and not cfg.get("reply_targets"):
        problems.append("Choose Twitch, YouTube or both.")
    if action_type == "send_discord_message" and not cfg.get("channels"):
        problems.append("Add at least one Discord channel.")
    if action_type == "template_control" and not (cfg.get("template_name") and cfg.get("control_action")):
        problems.append("Choose an overlay and what it should do.")
    if action_type == "obs_control" and not cfg.get("operation"):
        problems.append("Choose an OBS action.")
    if action_type == "game_hook" and not cfg.get("operation"):
        problems.append("Choose a game hook operation.")
    if action_type == "key_press" and cfg.get("input_type") == "macro":
        try:
            steps = json.loads(cfg.get("macro_sequence") or "[]")
        except (TypeError, ValueError):
            steps = []
        if not any(s.get("type") != "delay" and str(s.get("target") or "").strip() for s in steps if isinstance(s, dict)):
            problems.append("Add at least one key or click step.")
    return problems


def config_to_kwargs(action_type: str, cfg: dict) -> Dict[str, Any]:
    """Editor config → keyword arguments for ``connector_actions.create_action``."""
    cfg = dict(cfg or {})
    spec = spec_for(action_type)
    if action_type == "template_control":
        template_name = cfg.pop("template_name", "") or ""
        control_action = cfg.pop("control_action", "") or ""
        return {"template_name": template_name, "control_action": control_action,
                "control_data": {k: v for k, v in cfg.items() if not k.startswith("_")}}
    if action_type == "game_hook":
        try:
            from ..game_hooks.ff7_hook import ff7_connector_config_to_hook_kwargs

            hook_args = ff7_connector_config_to_hook_kwargs(cfg)
        except Exception:
            hook_args = {k[4:]: v for k, v in cfg.items() if k.startswith("arg_")}
        return {"game_id": cfg.get("game_id", "ff7") or "ff7", "operation": str(cfg.get("operation") or ""),
                "hook_arguments": hook_args}
    if action_type == "obs_control":
        return {"operation": str(cfg.get("operation") or ""),
                "obs_arguments": {k[4:]: v for k, v in cfg.items() if k.startswith("arg_")}}
    if action_type == "send_discord_message":
        return {"message": cfg.get("message", "") or "", "channels": list(cfg.get("channels") or [])}
    if action_type == "trigger_alert":
        data = {k: str(cfg.get(k) or "") for k in ("amount", "username", "message") if str(cfg.get(k) or "").strip()}
        if cfg.get("alert_type") == "point":
            for key in ("reward_name", "reward_id"):
                if str(cfg.get(key) or "").strip():
                    data[key] = str(cfg[key])
        return {"alert_type": cfg.get("alert_type") or "bit", "alert_data": data}

    kwargs: Dict[str, Any] = {}
    fields_by_key = {f.key: f for f in visible_fields(spec, cfg)} if spec else {}
    for key, f in fields_by_key.items():
        value = cfg.get(key, f.default)
        if f.kind in ("number", "slider"):
            value = _coerce_number(f, value)
        elif f.kind == "toggle":
            value = bool(value)
        elif f.kind == "json":
            value = _parse_json_field(value)
        elif isinstance(value, str) and f.kind != "textarea":
            value = value.strip() if key not in ("message",) else value
        kwargs[key] = value
    if action_type == "update_greeting":
        choice = kwargs.pop("greeting_enabled", "")
        kwargs["greeting_enabled"] = {"on": True, "off": False}.get(choice)
    return kwargs


def _parse_json_field(value: Any) -> Any:
    """Dict when the text is a JSON object without variables; otherwise the raw text (substituted at runtime)."""
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _json_text(value: Any) -> str:
    if value is None or value == {}:
        return "{}" if value == {} else ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False)
    return str(value)


def action_to_config(action: Any) -> Dict[str, Any]:
    """Saved action → editor config (inverse of ``config_to_kwargs``)."""
    action_type = getattr(getattr(action, "action_type", None), "value", None) or ""
    raw: Dict[str, Any] = {}
    if dataclasses.is_dataclass(action):
        for f in dataclasses.fields(action):
            if f.name in _BASE_ACTION_FIELDS or f.name.startswith("_"):
                continue
            raw[f.name] = getattr(action, f.name, None)
    if action_type == "template_control":
        cfg = {"template_name": raw.get("template_name") or "", "control_action": raw.get("control_action") or ""}
        cfg.update({k: v for k, v in (raw.get("control_data") or {}).items() if k != "enabled"})
        return cfg
    if action_type in ("game_hook", "obs_control"):
        args = raw.get("hook_arguments") if action_type == "game_hook" else raw.get("obs_arguments")
        cfg = {"operation": raw.get("operation") or ""}
        if action_type == "game_hook":
            cfg["game_id"] = raw.get("game_id") or "ff7"
        for k, v in (args or {}).items():
            cfg[f"arg_{k}"] = v if isinstance(v, (bool, int, float)) else str(v)
        return cfg
    if action_type == "trigger_alert":
        data = dict(raw.get("alert_data") or {})
        try:
            from ..connector_actions import normalize_trigger_alert_type

            alert_type = normalize_trigger_alert_type(raw.get("alert_type")) or "bit"
        except Exception:
            alert_type = raw.get("alert_type") or "bit"
        cfg = {"alert_type": alert_type}
        for k in ("amount", "username", "message", "reward_name", "reward_id"):
            if k in data:
                cfg[k] = data[k]
        return cfg
    if action_type == "update_greeting":
        enabled = raw.pop("greeting_enabled", None)
        raw["greeting_enabled"] = "" if enabled is None else ("on" if enabled else "off")
    for key in _JSON_FIELDS.get(action_type, ()):
        raw[key] = _json_text(raw.get(key))
    if action_type == "execute_command" and "timeout" in raw and "timeout_seconds" not in raw:
        raw["timeout_seconds"] = raw.pop("timeout")
    return {k: v for k, v in raw.items() if v is not None}


# ---------------------------------------------------------------------------
#  Summaries for collapsed action cards
# ---------------------------------------------------------------------------


def _quote(text: str, limit: int = 70) -> str:
    text = " ".join(str(text or "").split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return f"“{text}”" if text else ""


def summarize(action_type: Optional[str], cfg: dict, render: Callable[[str], str] = lambda s: s,
              labels: Optional[Dict[str, str]] = None) -> str:
    """One-line summary; ``render`` fills variables with example values."""
    spec = spec_for(action_type)
    if spec is None:
        return "Choose what this action does"
    c = cfg or {}
    labels = labels or {}
    if action_type == "send_chat_message":
        targets = [t.title() if t != "youtube" else "YouTube" for t in (c.get("reply_targets") or [])]
        return " · ".join(p for p in (" + ".join(targets), _quote(render(c.get("message", "")))) if p)
    if action_type in ("send_announcement", "display_banner", "add_greeting", "update_greeting"):
        text = c.get("message") if action_type in ("send_announcement", "display_banner") else c.get("greeting_text")
        extra = ""
        if action_type == "display_banner":
            extra = "until replaced" if c.get("permanent") else f"{c.get('duration', 10)} s"
        return " · ".join(p for p in (_quote(render(text or "")), extra) if p)
    if action_type == "send_discord_message":
        chans = [f"#{ch.get('channel_name') or ch.get('channel_id')}" for ch in (c.get("channels") or []) if isinstance(ch, dict)]
        return " · ".join(p for p in (", ".join(chans), _quote(render(c.get("message", "")))) if p)
    if action_type == "send_greeting":
        return f"Greeting for {render(c.get('username') or '{username}')}"
    if action_type == "trigger_alert":
        alert = labels.get(c.get("alert_type", ""), c.get("alert_type", ""))
        amount = render(str(c.get("amount") or ""))
        return f"{alert} alert" + (f" · amount {amount}" if amount else "")
    if action_type == "template_control":
        tpl, act = c.get("template_name") or "", c.get("control_action") or ""
        return f"{tpl.title()} → {labels.get(act, act).replace('_', ' ')}" if tpl and act else "Choose an overlay"
    if action_type == "websocket_emit":
        return c.get("event_name") or "Choose an event name"
    if action_type == "obs_control":
        op = labels.get(c.get("operation", ""), c.get("operation", ""))
        detail = next((str(v) for k, v in c.items() if k.startswith("arg_") and str(v).strip()), "")
        return " · ".join(p for p in (op, _quote(render(detail), 40)) if p) or "Choose an OBS action"
    if action_type == "game_hook":
        return labels.get(c.get("operation", ""), c.get("operation", "")) or "Choose an operation"
    if action_type == "key_press":
        if c.get("input_type") == "macro":
            try:
                n = len(json.loads(c.get("macro_sequence") or "[]"))
            except (TypeError, ValueError):
                n = 0
            return f"Macro · {n} step{'s' if n != 1 else ''}"
        what = _MOUSE_BUTTONS.get(c.get("key_sequence", ""), "") if c.get("input_type") == "mouse" else render(c.get("key_sequence") or "")
        mode = {"hold": "hold", "repeat": f"×{c.get('repeat_count', 3)}"}.get(c.get("action_mode", ""), "")
        return " ".join(p for p in (what or "No keys yet", mode) if p)
    if action_type == "audio_control":
        target = (c.get("target_application") if c.get("control_type") == "application" else c.get("target_device")) or "Default device"
        mode = c.get("action_mode", "set")
        verb = {"set": f"set to {c.get('volume_level', 50)}%", "set_random": "random volume", "increase": f"up {c.get('volume_step', 10)}%",
                "decrease": f"down {c.get('volume_step', 10)}%", "mute": "mute", "unmute": "unmute", "toggle_mute": "toggle mute"}.get(mode, mode)
        return f"{target} · {verb}"
    if action_type == "api_call":
        return f"{c.get('method', 'POST')} {render(c.get('url') or '')}".strip()
    if action_type == "write_file":
        return f"{'Append to' if c.get('append') else 'Write'} {render(c.get('file_path') or '')}".strip()
    if action_type == "execute_command":
        return _quote(render(c.get("command") or ""), 60)
    return spec.label


def action_type_options() -> Dict[str, str]:
    return {k: s.label for k, s in ACTION_SPECS.items()}


def grouped_specs() -> List[Tuple[str, str, List[ActionSpec]]]:
    out = []
    for group, icon in ACTION_GROUPS:
        specs = [s for s in ACTION_SPECS.values() if s.group == group]
        if specs:
            out.append((group, icon, specs))
    return out


def variable_fields(action_type: Optional[str]) -> Sequence[str]:
    spec = spec_for(action_type)
    return [f.key for f in spec.fields if f.variables] if spec else []

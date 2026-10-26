# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Colour field (swatch + text box) and the bridge to the shared Mycelian colour picker.

The picker itself is ``assets/default_assets/mycelian_color_picker.js`` / ``.css`` (Spore Studio loads the
same files). This module injects them once per page, opens the picker next to an element and routes its
live / done / cancel events back to Python callbacks.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
import weakref
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from nicegui import context, ui

from .ui_form_controls import form_input

logger = logging.getLogger(__name__)

PICKER_JS_ASSET = "default_assets/mycelian_color_picker.js"
PICKER_CSS_ASSET = "default_assets/mycelian_color_picker.css"
_PICKER_EVENT = "mycelian-color-picker"

CHECKERBOARD_BG = (
    "linear-gradient(45deg, #ccc 25%, transparent 25%), "
    "linear-gradient(-45deg, #ccc 25%, transparent 25%), "
    "linear-gradient(45deg, transparent 75%, #ccc 75%), "
    "linear-gradient(-45deg, transparent 75%, #ccc 75%)"
)

COLOR_CONTROL_CSS = """
.mycelian-color-row { display: flex; align-items: center; gap: 8px; width: 100%; }
.mycelian-color-swatch {
    width: 32px; height: 32px; min-width: 32px; border-radius: 8px; cursor: pointer; position: relative; overflow: hidden;
    border: 1px solid var(--color-border-default); flex: 0 0 auto;
}
.mycelian-color-swatch:hover { border-color: var(--color-primary); }
.mycelian-color-swatch:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }
.mycelian-color-swatch.is-picking { box-shadow: 0 0 0 2px var(--color-primary); }
.mycelian-color-swatch__checker {
    position: absolute; inset: 0; background-color: #fff;
    background-image: linear-gradient(45deg, #cfcfcf 25%, transparent 25%, transparent 75%, #cfcfcf 75%),
        linear-gradient(45deg, #cfcfcf 25%, transparent 25%, transparent 75%, #cfcfcf 75%);
    background-size: 10px 10px; background-position: 0 0, 5px 5px;
}
.mycelian-color-swatch__fill { position: absolute; inset: 0; }
"""

# -------------------------------------------------------------------------------------------- parsing


@dataclass
class ColorState:
    r: int = 255
    g: int = 255
    b: int = 255
    a: float = 1.0
    raw: str = "#ffffff"
    prefer: str = "auto"  # auto | hex | rgb | rgba | transparent


def _clamp_channel(value: float) -> int:
    return max(0, min(255, int(round(value))))


def _clamp_alpha(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


_RGB_RE = re.compile(
    r"^rgba?\(\s*([\d.]+%?)\s*[,\s]\s*([\d.]+%?)\s*[,\s]\s*([\d.]+%?)(?:\s*[,/]\s*([\d.]+%?))?\s*\)$",
    re.I,
)
_GRADIENT_RE = re.compile(r"^\s*(repeating-)?(linear|radial|conic)-gradient\(", re.I)
_COLOUR_TOKEN_RE = re.compile(r"rgba?\([^()]*\)|hsla?\([^()]*\)|#[0-9a-f]{3,8}\b|\btransparent\b", re.I)
_UNSAFE_CSS_RE = re.compile(r"[;{}<>\"\\]|url\(|expression\(", re.I)


def _channel(token: str) -> float:
    if token.endswith("%"):
        return float(token[:-1]) * 2.55
    return float(token)


def _alpha(token: str) -> float:
    if token.endswith("%"):
        return float(token[:-1]) / 100
    return float(token)


def _detect_prefer(value: str, state: ColorState) -> str:
    raw = (value or "").strip()
    if not raw:
        return "hex"
    lower = raw.lower()
    if lower == "transparent":
        return "transparent"
    if raw.startswith("#"):
        return "hex"
    if lower.startswith("rgba"):
        return "rgba"
    if lower.startswith("rgb"):
        return "rgb"
    if state.a <= 0:
        return "transparent"
    return "auto"


def parse_color_string(value: Any) -> ColorState:
    """Parse #hex (3/4/6/8 digits), rgb(), rgba() or transparent into a ColorState.

    Anything else (gradients, names, ``inherit``) comes back opaque with ``raw`` holding the text.
    """
    raw = str(value or "").strip()
    if not raw or raw.lower() == "transparent":
        return ColorState(r=255, g=255, b=255, a=0.0, raw="transparent", prefer="transparent")

    if raw.startswith("#"):
        hex_clean = raw[1:]
        if len(hex_clean) in (3, 4):
            hex_clean = "".join(c * 2 for c in hex_clean)
        if len(hex_clean) in (6, 8):
            try:
                r = int(hex_clean[0:2], 16)
                g = int(hex_clean[2:4], 16)
                b = int(hex_clean[4:6], 16)
                a = round(int(hex_clean[6:8], 16) / 255, 3) if len(hex_clean) == 8 else 1.0
                return ColorState(r=r, g=g, b=b, a=a, raw=raw, prefer="hex" if a >= 1 else "rgba")
            except ValueError:
                pass

    rgb_match = _RGB_RE.match(raw)
    if rgb_match:
        try:
            r = _clamp_channel(_channel(rgb_match.group(1)))
            g = _clamp_channel(_channel(rgb_match.group(2)))
            b = _clamp_channel(_channel(rgb_match.group(3)))
            has_alpha = rgb_match.group(4) is not None
            a = _clamp_alpha(_alpha(rgb_match.group(4))) if has_alpha else 1.0
        except ValueError:
            return ColorState(raw=raw, prefer=_detect_prefer(raw, ColorState()))
        return ColorState(r=r, g=g, b=b, a=a, raw=raw, prefer="rgba" if has_alpha else "rgb")

    return ColorState(raw=raw, prefer=_detect_prefer(raw, ColorState()))


def format_color_string(state: ColorState, prefer: Optional[str] = None) -> str:
    """Format ColorState back to a CSS colour string."""
    pref = prefer or state.prefer or "auto"
    a = _clamp_alpha(state.a)

    if a <= 0:
        if pref == "rgba":
            return f"rgba({state.r}, {state.g}, {state.b}, 0)"
        return "transparent"

    if a >= 1:
        if pref == "hex":
            return f"#{state.r:02x}{state.g:02x}{state.b:02x}"
        return f"rgb({state.r}, {state.g}, {state.b})"

    return f"rgba({state.r}, {state.g}, {state.b}, {round(a, 3)})"


def is_gradient(value: Any) -> bool:
    """True for linear / radial / conic (repeating or not) gradient CSS."""
    return bool(_GRADIENT_RE.match(str(value or "")))


def _is_solid_syntax(raw: str) -> bool:
    return raw.lower() == "transparent" or bool(
        re.match(r"^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$", raw, re.I) or _RGB_RE.match(raw)
    )


def is_color_transparent(value: Any) -> bool:
    """True when the colour is transparent or has alpha below 1.

    Gradients count as transparent when any stop is ``transparent`` or has alpha below 1.
    """
    raw = str(value or "").strip()
    if raw.lower() == "transparent":
        return True
    if is_gradient(raw):
        for token in _COLOUR_TOKEN_RE.findall(raw):
            low = token.lower()
            if low == "transparent":
                return True
            if low.startswith("hsl"):
                parts = re.split(r"[,/\s]+", low[low.index("(") + 1 : -1].strip())
                try:
                    if len(parts) >= 4 and _alpha(parts[3]) < 1:
                        return True
                except ValueError:
                    continue
                continue
            if parse_color_string(token).a < 1:
                return True
        return False
    return parse_color_string(raw).a < 1.0


def swatch_background(value: Any) -> str:
    """CSS ``background`` value for a checkerboard preview swatch (solid colours, gradients, names)."""
    raw = str(value or "").strip()
    if not raw:
        return "transparent"
    if _is_solid_syntax(raw):
        state = parse_color_string(raw)
        if state.a <= 0:
            return "transparent"
        return f"rgba({state.r}, {state.g}, {state.b}, {round(state.a, 3)})"
    if _UNSAFE_CSS_RE.search(raw):
        return "transparent"
    if is_gradient(raw) or re.match(r"^[a-z]+$", raw, re.I) or re.match(r"^hsla?\([^()]*\)$", raw, re.I):
        return raw
    return "transparent"


def swatch_fill_style(state: ColorState) -> str:
    """Inline style for the colour overlay inside a checkerboard swatch."""
    if state.a <= 0:
        return "background-color: transparent;"
    return f"background-color: rgba({state.r}, {state.g}, {state.b}, {state.a});"


# -------------------------------------------------------------------------------------------- page bridge


class _PickerBridge:
    """Per-client routing of picker events (one ``ui.on`` listener per page)."""

    def __init__(self) -> None:
        self.sessions: Dict[str, Dict[str, Optional[Callable]]] = {}

    def dispatch(self, event: Any) -> None:
        args = getattr(event, "args", None)
        if isinstance(args, list):
            args = args[0] if args else None
        if not isinstance(args, dict):
            return
        token = str(args.get("token") or "")
        kind = str(args.get("kind") or "")
        session = self.sessions.get(token)
        if session is None:
            return
        value = args.get("value")
        value = "" if value is None else str(value)
        if kind in ("done", "cancel"):
            self.sessions.pop(token, None)
        callback = session.get(kind)
        if callback is None:
            return
        try:
            if kind == "cancel":
                callback()
            else:
                callback(value)
        except Exception:
            logger.exception("Colour picker %s handler failed", kind)


_bridges: "weakref.WeakKeyDictionary[Any, _PickerBridge]" = weakref.WeakKeyDictionary()


def _read_asset(relative: str) -> str:
    try:
        from .path_utils import get_assets_path

        with open(get_assets_path(relative), "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception as exc:
        logger.error("Could not load %s: %s", relative, exc)
        return ""


def _bridge_for_client(client: Any = None) -> Optional[_PickerBridge]:
    """The page's event bridge, injecting the picker on first use.

    ``client`` defaults to the current context; pass the anchor's client from handlers whose element
    may already have been rebuilt (its slot is gone, so ``context.client`` would raise).
    """
    if client is None:
        try:
            client = context.client
        except Exception:
            return None
    bridge = _bridges.get(client)
    if bridge is None:
        bridge = _PickerBridge()
        _bridges[client] = bridge
        client.layout.on(_PICKER_EVENT, bridge.dispatch)
        css = _read_asset(PICKER_CSS_ASSET) + "\n" + COLOR_CONTROL_CSS
        js = _read_asset(PICKER_JS_ASSET)
        client.run_javascript(
            "(function(){var id='mycelian-color-picker-css';var el=document.getElementById(id);"
            "if(!el){el=document.createElement('style');el.id=id;document.head.appendChild(el);}"
            "el.textContent=" + json.dumps(css) + ";})();\n" + js
        )
    return bridge


def ensure_color_control_assets() -> None:
    """Inject the picker JS / CSS and the event bridge once for the current page."""
    try:
        _bridge_for_client()
    except Exception as exc:
        logger.debug("Colour picker asset injection skipped: %s", exc)


_OPEN_JS = """(function(){
var P=window.MycelianColorPicker; if(!P){return;}
var cfg=%s, n=0;
(function go(){
  var a=document.getElementById(cfg.anchor);
  if(!a){ if(n++<80){setTimeout(go,25);} return; }
  var fill=cfg.fill?a.querySelector(cfg.fill):null;
  var paint=function(v){ if(fill){ fill.style.background=P.swatchBackground(v); } };
  var send=function(kind,v){ if(typeof emitEvent==='function'){ emitEvent(cfg.event,{token:cfg.token,kind:kind,value:v==null?null:String(v)}); } };
  var o={anchor:a,value:cfg.value,presets:cfg.presets,allowAlpha:cfg.allowAlpha,allowGradient:cfg.allowGradient,
    preferredFormat:cfg.preferredFormat,title:cfg.title,note:cfg.note,
    onDone:function(v){ paint(v); send('done',v); },
    onCancel:function(){ paint(cfg.value); send('cancel',null); }};
  if(cfg.live||cfg.fill){ o.onInput=function(v){ paint(v); if(cfg.live){ send('input',v); } }; }
  P.open(o);
})();
})();"""


def open_color_picker(
    anchor: Any,
    value: Any,
    *,
    on_done: Optional[Callable[[str], None]] = None,
    on_input: Optional[Callable[[str], None]] = None,
    on_cancel: Optional[Callable[[], None]] = None,
    presets: Optional[List[str]] = None,
    allow_alpha: bool = True,
    allow_gradient: bool = False,
    preferred_format: Optional[str] = None,
    title: Optional[str] = None,
    note: Optional[str] = None,
    fill_selector: Optional[str] = None,
) -> None:
    """Open the shared picker next to ``anchor`` (a NiceGUI element).

    ``on_input`` gets live values while dragging (throttled in the browser), ``on_done`` the final value
    (the untouched original when nothing changed), ``on_cancel`` fires on Cancel / Escape.
    ``fill_selector`` names a child of the anchor whose background is painted live in the browser.
    """
    client = getattr(anchor, "client", None)
    bridge = _bridge_for_client(client)
    if bridge is None or client is None:
        return
    token = uuid.uuid4().hex
    # Only the newest session per page matters; a picker replaced by another reports "done" first.
    if len(bridge.sessions) > 32:
        bridge.sessions.clear()
    bridge.sessions[token] = {"input": on_input, "done": on_done, "cancel": on_cancel}
    live = on_input is not None
    if note is None and not live:
        note = "Enter or Done applies the colour"
    cfg = {
        "anchor": f"c{anchor.id}",
        "token": token,
        "event": _PICKER_EVENT,
        "value": "" if value is None else str(value),
        "presets": [str(p) for p in (presets or []) if p is not None and str(p).strip()],
        "allowAlpha": bool(allow_alpha),
        "allowGradient": bool(allow_gradient),
        "preferredFormat": preferred_format,
        "title": title,
        "note": note,
        "live": live,
        "fill": fill_selector,
    }
    client.run_javascript(_OPEN_JS % json.dumps(cfg))


# -------------------------------------------------------------------------------------------- the field


def _set_fill(fill_el: Any, value: str) -> None:
    fill_el.style(replace=f"background: {swatch_background(value)}")


def render_color_field(
    *,
    element_id: str,
    value: Any,
    tooltip: str,
    preset_options: Optional[List[str]] = None,
    on_change: Callable[[str], None],
    on_input: Optional[Callable[[str], None]] = None,
    allow_gradient: bool = False,
) -> Any:
    """Render a swatch + text input; returns the text input element.

    ``on_change`` fires with the committed value (picker Done, or Enter / blur in the text box).
    ``on_input`` (optional) fires with live values while the picker is open, and with the original
    value again if the picker is cancelled. Gradients are offered when ``allow_gradient`` is set or the
    current value already is one. Values are never reformatted unless the user picks a new colour.
    """
    ensure_color_control_assets()
    initial = "" if value is None else str(value)
    committed = {"value": initial}
    refs: Dict[str, Any] = {}

    def show(new_value: str) -> None:
        text = refs.get("text")
        if text is not None and text.value != new_value:
            text.value = new_value
        if refs.get("fill") is not None:
            _set_fill(refs["fill"], new_value)

    def commit(new_value: str) -> None:
        show(new_value)
        if new_value != committed["value"]:
            committed["value"] = new_value
            on_change(new_value)

    def open_picker() -> None:
        text = refs.get("text")
        start = str(text.value if text is not None and text.value is not None else committed["value"])
        session = {"touched": False}

        def handle_input(new_value: str) -> None:
            session["touched"] = True
            show(new_value)
            if on_input is not None:
                on_input(new_value)

        def handle_done(new_value: str) -> None:
            if new_value != start:
                commit(new_value)
            else:
                show(start)
                if start != committed["value"]:
                    commit(start)
                elif session["touched"] and on_input is not None:
                    on_input(start)

        def handle_cancel() -> None:
            show(start)
            if session["touched"] and on_input is not None:
                on_input(start)

        open_color_picker(
            refs["swatch"],
            start,
            presets=preset_options,
            allow_gradient=allow_gradient or is_gradient(start),
            on_input=handle_input if on_input is not None else None,
            on_done=handle_done,
            on_cancel=handle_cancel,
            fill_selector=".mycelian-color-swatch__fill",
        )

    def on_text_input(e: Any) -> None:
        raw = getattr(e, "value", None)
        if raw is None and refs.get("text") is not None:
            raw = refs["text"].value
        if refs.get("fill") is not None:
            _set_fill(refs["fill"], str(raw or ""))

    def on_text_commit(_e: Any = None) -> None:
        text = refs.get("text")
        raw = str(text.value or "").strip() if text is not None else ""
        if text is not None and text.value != raw:
            text.value = raw
        commit(raw)

    with ui.row().classes("mycelian-color-row w-full"):
        swatch = ui.element("div").on("click", open_picker)
        swatch.classes("mycelian-color-swatch").props('tabindex=0 role=button aria-label="Pick a colour"')
        swatch._props["data-color-field"] = str(element_id)
        swatch.on("keydown.enter", open_picker)
        refs["swatch"] = swatch
        with swatch:
            ui.element("div").classes("mycelian-color-swatch__checker")
            fill_el = ui.element("div").classes("mycelian-color-swatch__fill")
            refs["fill"] = fill_el
            _set_fill(fill_el, initial)

        text_input = form_input(
            tooltip=tooltip,
            value=initial,
            placeholder="#hex, rgb(), rgba(), transparent" + (", gradient" if allow_gradient else ""),
            classes="grow font-mono text-sm",
            on_change=on_text_input,
        )
        refs["text"] = text_input
        text_input.on("blur", on_text_commit)
        text_input.on("keydown.enter", on_text_commit)

    return text_input

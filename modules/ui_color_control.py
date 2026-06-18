# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Unified color field with swatch preview, text input, and custom alpha-aware picker."""

from __future__ import annotations

import html
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from nicegui import ui

from .ui_form_controls import form_input
from .ui_timer import layout_schedule

logger = logging.getLogger(__name__)

CHECKERBOARD_BG = (
    "linear-gradient(45deg, #ccc 25%, transparent 25%), "
    "linear-gradient(-45deg, #ccc 25%, transparent 25%), "
    "linear-gradient(45deg, transparent 75%, #ccc 75%), "
    "linear-gradient(-45deg, transparent 75%, #ccc 75%)"
)

COLOR_CONTROL_CSS = """
.mycelian-color-row {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
}
.mycelian-color-swatch {
    width: 32px;
    height: 32px;
    min-width: 32px;
    border-radius: 6px;
    cursor: pointer;
    border: 1px solid var(--color-border-default, #555);
    position: relative;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
    transition: transform 0.15s ease, box-shadow 0.15s ease;
}
.mycelian-color-swatch:hover {
    transform: scale(1.05);
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.3);
}
.mycelian-color-swatch__checker {
    position: absolute;
    inset: 0;
    background-image: linear-gradient(45deg, #ccc 25%, transparent 25%),
        linear-gradient(-45deg, #ccc 25%, transparent 25%),
        linear-gradient(45deg, transparent 75%, #ccc 75%),
        linear-gradient(-45deg, transparent 75%, #ccc 75%);
    background-size: 8px 8px;
    background-position: 0 0, 0 4px, 4px -4px, -4px 0px;
}
.mycelian-color-swatch__fill {
    position: absolute;
    inset: 0;
}
.mycelian-color-picker-preview {
    width: 48px;
    height: 48px;
    min-width: 48px;
    border-radius: 8px;
    border: 1px solid var(--color-border-default, #555);
    position: relative;
    overflow: hidden;
}
.mycelian-color-picker-sv {
    position: relative;
    width: 100%;
    height: 160px;
    border-radius: 8px;
    cursor: crosshair;
    overflow: hidden;
    border: 1px solid var(--color-border-default, #555);
    touch-action: none;
    user-select: none;
}
.mycelian-color-picker-sv__hue {
    position: absolute;
    inset: 0;
}
.mycelian-color-picker-sv__white {
    position: absolute;
    inset: 0;
    background: linear-gradient(to right, #fff, transparent);
}
.mycelian-color-picker-sv__black {
    position: absolute;
    inset: 0;
    background: linear-gradient(to top, #000, transparent);
}
.mycelian-color-picker-sv__cursor {
    position: absolute;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    border: 2px solid #fff;
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.5);
    transform: translate(-50%, -50%);
    pointer-events: none;
}
.mycelian-color-picker-hue {
    width: 100%;
    height: 14px;
    border-radius: 7px;
    cursor: pointer;
    border: 1px solid var(--color-border-default, #555);
    background: linear-gradient(to right,
        #f00 0%, #ff0 17%, #0f0 33%, #0ff 50%, #00f 67%, #f0f 83%, #f00 100%);
    position: relative;
    touch-action: none;
    user-select: none;
}
.mycelian-color-picker-hue__cursor {
    position: absolute;
    top: 50%;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    border: 2px solid #fff;
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.5);
    transform: translate(-50%, -50%);
    pointer-events: none;
}
.mycelian-color-picker-alpha {
    width: 100%;
    height: 14px;
    border-radius: 7px;
    cursor: pointer;
    border: 1px solid var(--color-border-default, #555);
    position: relative;
    overflow: hidden;
    touch-action: none;
    user-select: none;
}
.mycelian-color-picker-alpha__checker {
    position: absolute;
    inset: 0;
    background-image: linear-gradient(45deg, #ccc 25%, transparent 25%),
        linear-gradient(-45deg, #ccc 25%, transparent 25%),
        linear-gradient(45deg, transparent 75%, #ccc 75%),
        linear-gradient(-45deg, transparent 75%, #ccc 75%);
    background-size: 8px 8px;
    background-position: 0 0, 0 4px, 4px -4px, -4px 0px;
}
.mycelian-color-picker-alpha__gradient {
    position: absolute;
    inset: 0;
}
.mycelian-color-picker-alpha__cursor {
    position: absolute;
    top: 50%;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    border: 2px solid #fff;
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.5);
    transform: translate(-50%, -50%);
    pointer-events: none;
}
.mycelian-color-presets {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    max-height: 120px;
    overflow-y: auto;
}
.mycelian-color-preset {
    width: 24px;
    height: 24px;
    border-radius: 4px;
    cursor: pointer;
    border: 1px solid var(--color-border-default, #555);
    position: relative;
    overflow: hidden;
    flex-shrink: 0;
}
.mycelian-color-preset:hover {
    transform: scale(1.1);
}
/* Override app-wide * { transition: background-color } so picker paints live during drag */
.mycelian-color-picker-root,
.mycelian-color-picker-root * {
    transition: none !important;
}
"""

_COLOR_PICKER_BIND_IIFE = r"""
(function () {
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  function hsvToRgb(h, s, v) {
    h = ((h % 360) + 360) % 360;
    s = clamp(s, 0, 1);
    v = clamp(v, 0, 1);
    var c = v * s;
    var x = c * (1 - Math.abs((h / 60) % 2 - 1));
    var m = v - c;
    var r = 0, g = 0, b = 0;
    if (h < 60) { r = c; g = x; }
    else if (h < 120) { r = x; g = c; }
    else if (h < 180) { g = c; b = x; }
    else if (h < 240) { g = x; b = c; }
    else if (h < 300) { r = x; b = c; }
    else { r = c; b = x; }
    return {
      r: Math.round((r + m) * 255),
      g: Math.round((g + m) * 255),
      b: Math.round((b + m) * 255)
    };
  }

  function rgbToHsv(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    var max = Math.max(r, g, b), min = Math.min(r, g, b);
    var d = max - min;
    var h = 0;
    var s = max === 0 ? 0 : d / max;
    var v = max;
    if (d !== 0) {
      if (max === r) { h = ((g - b) / d) % 6; }
      else if (max === g) { h = (b - r) / d + 2; }
      else { h = (r - g) / d + 4; }
      h *= 60;
      if (h < 0) { h += 360; }
    }
    return { h: h, s: s, v: v };
  }

  function parseColor(str) {
    str = String(str || "").trim();
    if (!str || str.toLowerCase() === "transparent") {
      return { r: 255, g: 255, b: 255, a: 0, raw: "transparent" };
    }
    var m;
    m = str.match(/^#([0-9a-f]{3})$/i);
    if (m) {
      var h3 = m[1];
      return { r: parseInt(h3[0]+h3[0],16), g: parseInt(h3[1]+h3[1],16),
        b: parseInt(h3[2]+h3[2],16), a: 1, raw: str };
    }
    m = str.match(/^#([0-9a-f]{6})$/i);
    if (m) {
      var h6 = m[1];
      return { r: parseInt(h6.slice(0,2),16), g: parseInt(h6.slice(2,4),16),
        b: parseInt(h6.slice(4,6),16), a: 1, raw: str };
    }
    m = str.match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)$/i);
    if (m) {
      return { r: +m[1], g: +m[2], b: +m[3], a: m[4] != null ? +m[4] : 1, raw: str };
    }
    return { r: 255, g: 255, b: 255, a: 1, raw: str };
  }

  function formatColor(rgb, a, prefer) {
    prefer = prefer || "auto";
    if (a <= 0) {
      if (prefer === "transparent") { return "transparent"; }
      if (prefer === "rgba") { return "rgba(" + rgb.r + ", " + rgb.g + ", " + rgb.b + ", 0)"; }
      return "transparent";
    }
    if (a >= 1) {
      if (prefer === "hex") {
        function hx(n) { var s = n.toString(16); return s.length < 2 ? "0" + s : s; }
        return "#" + hx(rgb.r) + hx(rgb.g) + hx(rgb.b);
      }
      return "rgb(" + rgb.r + ", " + rgb.g + ", " + rgb.b + ")";
    }
    return "rgba(" + rgb.r + ", " + rgb.g + ", " + rgb.b + ", " + Math.round(a * 1000) / 1000 + ")";
  }

  function findRoot(el) {
    return el && el.closest ? el.closest(".mycelian-color-picker-root") : null;
  }

  function queryParts(rootId) {
    var root = document.getElementById(rootId);
    if (!root) { return null; }
    return {
      root: root,
      sv: root.querySelector(".mycelian-color-picker-sv"),
      svHue: root.querySelector(".mycelian-color-picker-sv__hue"),
      svCursor: root.querySelector(".mycelian-color-picker-sv__cursor"),
      hueBar: root.querySelector(".mycelian-color-picker-hue"),
      hueCursor: root.querySelector(".mycelian-color-picker-hue__cursor"),
      alphaBar: root.querySelector(".mycelian-color-picker-alpha"),
      alphaGrad: root.querySelector(".mycelian-color-picker-alpha__gradient"),
      alphaCursor: root.querySelector(".mycelian-color-picker-alpha__cursor"),
      previewFill: root.querySelector(".mycelian-color-picker-preview .mycelian-color-swatch__fill"),
      textOut: document.getElementById(rootId + "-text")
    };
  }

  function syncPicker(rootId, opts) {
    opts = opts || {};
    var inst = window.mycelianPickerInstances[rootId];
    var p = queryParts(rootId);
    if (!inst || !p || !p.sv) { return; }
    var rgb = hsvToRgb(inst.hsv.h, inst.hsv.s, inst.hsv.v);
    inst.state.r = rgb.r;
    inst.state.g = rgb.g;
    inst.state.b = rgb.b;
    var hueRgb = hsvToRgb(inst.hsv.h, 1, 1);
    function hx(n) { var s = n.toString(16); return s.length < 2 ? "0" + s : s; }
    var hueHex = "#" + hx(hueRgb.r) + hx(hueRgb.g) + hx(hueRgb.b);
    if (p.svHue) {
      p.svHue.style.backgroundColor = hueHex;
    } else if (p.sv) {
      p.sv.style.backgroundColor = hueHex;
    }
    var a = clamp(inst.state.a, 0, 1);
    if (p.alphaGrad) {
      p.alphaGrad.style.background = "linear-gradient(to right, rgba(" + rgb.r + "," + rgb.g + "," + rgb.b + ",0), rgba(" + rgb.r + "," + rgb.g + "," + rgb.b + ",1))";
    }
    if (p.previewFill) {
      p.previewFill.style.backgroundColor = "rgba(" + rgb.r + "," + rgb.g + "," + rgb.b + "," + a + ")";
    }
    if (p.svCursor) {
      p.svCursor.style.left = (inst.hsv.s * 100) + "%";
      p.svCursor.style.top = ((1 - inst.hsv.v) * 100) + "%";
    }
    if (p.hueCursor) {
      p.hueCursor.style.left = (inst.hsv.h / 360 * 100) + "%";
    }
    if (p.alphaCursor) {
      p.alphaCursor.style.left = (a * 100) + "%";
    }
    var formatted = formatColor(rgb, a, inst.prefer);
    inst._current = formatted;
    p.root._mycelianCurrent = formatted;
    p.root._mycelianGetValue = function () { return inst._current; };
    if (!opts.skipText) {
      var textEl = p.textOut || document.getElementById(inst.textId);
      if (textEl) { textEl.value = formatted; }
    }
  }

  window.mycelianPickerInstances = window.mycelianPickerInstances || {};
  window.mycelianPickerApi = window.mycelianPickerApi || { activeDrag: null };
  window.mycelianPickerApi.syncPicker = syncPicker;

  window.mycelianPickerApi.onDragMove = function (ev) {
    var activeDrag = window.mycelianPickerApi.activeDrag;
    if (!activeDrag) { return; }
    var inst = window.mycelianPickerInstances[activeDrag.rootId];
    var p = queryParts(activeDrag.rootId);
    if (!inst || !p) { return; }
    if (activeDrag.type === "sv" && p.sv) {
      var srect = p.sv.getBoundingClientRect();
      inst.hsv.s = clamp((ev.clientX - srect.left) / srect.width, 0, 1);
      inst.hsv.v = clamp(1 - (ev.clientY - srect.top) / srect.height, 0, 1);
      if (inst.state.a <= 0) { inst.state.a = 1; }
    } else if (activeDrag.type === "hue" && p.hueBar) {
      var hrect = p.hueBar.getBoundingClientRect();
      inst.hsv.h = clamp((ev.clientX - hrect.left) / hrect.width, 0, 1) * 360;
    } else if (activeDrag.type === "alpha" && p.alphaBar) {
      var arect = p.alphaBar.getBoundingClientRect();
      inst.state.a = clamp((ev.clientX - arect.left) / arect.width, 0, 1);
    }
    syncPicker(activeDrag.rootId, { skipText: true });
  };

  window.mycelianPickerApi.endDrag = function (ev) {
    var activeDrag = window.mycelianPickerApi.activeDrag;
    if (activeDrag) {
      syncPicker(activeDrag.rootId);
      if (activeDrag.captureEl && activeDrag.captureEl.releasePointerCapture) {
        try { activeDrag.captureEl.releasePointerCapture(ev.pointerId); } catch (e) {}
      }
    }
    window.mycelianPickerApi.activeDrag = null;
  };

  if (!window._mycelianPickerDelegated) {
    window._mycelianPickerDelegated = true;

  window.addEventListener("pointerdown", function (ev) {
    if (ev.button !== 0) { return; }
    var preset = ev.target.closest ? ev.target.closest(".mycelian-color-preset") : null;
    if (preset) {
      var proot = findRoot(preset);
      if (!proot) { return; }
      var pinst = window.mycelianPickerInstances[proot.id];
      if (!pinst) { return; }
      var c = parseColor(preset.getAttribute("data-color") || "");
      pinst.state.r = c.r; pinst.state.g = c.g; pinst.state.b = c.b; pinst.state.a = c.a;
      pinst.hsv = rgbToHsv(c.r, c.g, c.b);
      if (c.raw && c.raw.toLowerCase() === "transparent") { pinst.prefer = "transparent"; }
      else if (c.raw && c.raw.charAt(0) === "#") { pinst.prefer = "hex"; }
      syncPicker(proot.id);
      ev.preventDefault();
      return;
    }
    var root = null;
    var type = null;
    var hue = ev.target.closest ? ev.target.closest(".mycelian-color-picker-hue") : null;
    var sv = ev.target.closest ? ev.target.closest(".mycelian-color-picker-sv") : null;
    var alpha = ev.target.closest ? ev.target.closest(".mycelian-color-picker-alpha") : null;
    if (hue) { root = findRoot(hue); type = "hue"; }
    else if (sv) { root = findRoot(sv); type = "sv"; }
    else if (alpha) { root = findRoot(alpha); type = "alpha"; }
    if (!root || !type || !window.mycelianPickerInstances[root.id]) { return; }
    var captureEl = hue || sv || alpha;
    window.mycelianPickerApi.activeDrag = { rootId: root.id, type: type, captureEl: captureEl };
    if (captureEl && captureEl.setPointerCapture) {
      try { captureEl.setPointerCapture(ev.pointerId); } catch (e) {}
    }
    window.mycelianPickerApi.onDragMove(ev);
    ev.preventDefault();
  }, true);

  window.addEventListener("pointermove", function (ev) {
    if (!window.mycelianPickerApi.activeDrag) { return; }
    window.mycelianPickerApi.onDragMove(ev);
    ev.preventDefault();
  }, true);

  window.addEventListener("pointerup", function (ev) {
    window.mycelianPickerApi.endDrag(ev);
  }, true);
  window.addEventListener("pointercancel", function (ev) {
    window.mycelianPickerApi.endDrag(ev);
  }, true);

  }

  window.mycelianColorPickerBind = function (cfg) {
    var state = parseColor(cfg.initial);
    var prefer = cfg.prefer || "auto";
    if (state.a <= 0 && prefer === "auto") { prefer = state.raw === "transparent" ? "transparent" : "rgba"; }
    else if (state.raw && state.raw.charAt(0) === "#" && state.a >= 1) { prefer = "hex"; }
    else if (state.raw && state.raw.indexOf("rgba") === 0) { prefer = "rgba"; }
    else if (state.raw && state.raw.indexOf("rgb") === 0) { prefer = "rgb"; }
    var hsv = rgbToHsv(state.r, state.g, state.b);
    if (state.a <= 0) { hsv = { h: 0, s: 0, v: 1 }; }
    window.mycelianPickerInstances[cfg.rootId] = {
      rootId: cfg.rootId,
      textId: cfg.textId,
      prefer: prefer,
      hsv: hsv,
      state: state,
      _current: null
    };
    syncPicker(cfg.rootId);
  };
})();
"""


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
    """Parse #hex, rgb(), rgba(), or transparent into a ColorState."""
    raw = str(value or "").strip()
    if not raw or raw.lower() == "transparent":
        return ColorState(r=255, g=255, b=255, a=0.0, raw="transparent", prefer="transparent")

    if raw.startswith("#"):
        hex_clean = raw.lstrip("#")
        if len(hex_clean) == 3:
            hex_clean = "".join(c * 2 for c in hex_clean)
        if len(hex_clean) >= 6:
            try:
                r = int(hex_clean[0:2], 16)
                g = int(hex_clean[2:4], 16)
                b = int(hex_clean[4:6], 16)
                return ColorState(r=r, g=g, b=b, a=1.0, raw=raw, prefer="hex")
            except ValueError:
                pass

    rgb_match = re.match(
        r"^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)$",
        raw,
        re.I,
    )
    if rgb_match:
        r = _clamp_channel(float(rgb_match.group(1)))
        g = _clamp_channel(float(rgb_match.group(2)))
        b = _clamp_channel(float(rgb_match.group(3)))
        a = _clamp_alpha(float(rgb_match.group(4))) if rgb_match.group(4) is not None else 1.0
        prefer = "rgba" if rgb_match.group(4) is not None else "rgb"
        if a <= 0 and raw.lower() == "transparent":
            prefer = "transparent"
        return ColorState(r=r, g=g, b=b, a=a, raw=raw, prefer=prefer)

    return ColorState(raw=raw, prefer=_detect_prefer(raw, ColorState()))


def format_color_string(state: ColorState, prefer: Optional[str] = None) -> str:
    """Format ColorState back to a CSS color string."""
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


def is_color_transparent(value: Any) -> bool:
    """Return True when the color is transparent or has alpha below 1."""
    raw = str(value or "").strip().lower()
    if raw == "transparent":
        return True
    state = parse_color_string(value)
    return state.a < 1.0


def swatch_fill_style(state: ColorState) -> str:
    """Inline style for the color overlay inside a checkerboard swatch."""
    if state.a <= 0:
        return "background-color: transparent;"
    return f"background-color: rgba({state.r}, {state.g}, {state.b}, {state.a});"


def ensure_color_control_assets() -> None:
    """Inject shared CSS and JS; refresh CSS on each call so updates apply without reload."""
    try:
        ui.run_javascript(
            "(function(){var id='mycelian-color-control-css';var el=document.getElementById(id);"
            "if(!el){el=document.createElement('style');el.id=id;document.head.appendChild(el);}"
            "el.textContent=" + json.dumps(COLOR_CONTROL_CSS) + ";})();"
        )
        ui.run_javascript(_COLOR_PICKER_BIND_IIFE)
    except Exception as exc:
        logger.debug("Color control asset injection skipped: %s", exc)


def _update_swatch_fill(fill_el: Any, value: str) -> None:
    state = parse_color_string(value)
    fill_el.style(swatch_fill_style(state))


def _open_color_picker_dialog(
    *,
    current_value: str,
    preset_options: Optional[List[str]],
    on_apply: Callable[[str], None],
) -> None:
    ensure_color_control_assets()
    state = parse_color_string(current_value)
    root_id = "mycelian-cp-" + uuid.uuid4().hex[:12]
    text_id = root_id + "-text"
    display = format_color_string(state)
    display_attr = html.escape(display, quote=True)

    with ui.dialog() as dlg, ui.card().classes("p-4 min-w-[320px]"):
        ui.label("Pick color").classes("text-lg font-medium mb-3")

        presets_html = ""
        if preset_options:
            chips = []
            for opt in preset_options:
                opt_str = str(opt)
                if opt_str.lower() == "transparent":
                    inner = '<div class="mycelian-color-swatch__checker"></div>'
                else:
                    ps = parse_color_string(opt_str)
                    inner = (
                        '<div class="mycelian-color-swatch__checker"></div>'
                        f'<div class="mycelian-color-swatch__fill" style="{swatch_fill_style(ps)}"></div>'
                    )
                chips.append(
                    f'<div class="mycelian-color-preset" data-color="{opt_str}" title="{opt_str}">{inner}</div>'
                )
            presets_html = (
                '<div class="mycelian-color-presets">' + "".join(chips) + "</div>"
            )

        picker_html = f"""
        <div id="{root_id}" class="mycelian-color-picker-root w-full flex flex-col gap-3">
          <div class="flex items-center gap-3 mb-1">
            <div class="mycelian-color-picker-preview">
              <div class="mycelian-color-swatch__checker"></div>
              <div class="mycelian-color-swatch__fill"></div>
            </div>
            <input id="{text_id}" class="grow font-mono text-sm"
              style="flex:1; padding:8px; border:1px solid var(--color-border-default,#555);
              border-radius:4px; background:var(--color-bg-surface,#222); color:inherit;"
              readonly value="{display_attr}" />
          </div>
          <div class="mycelian-color-picker-sv">
            <div class="mycelian-color-picker-sv__hue"></div>
            <div class="mycelian-color-picker-sv__white"></div>
            <div class="mycelian-color-picker-sv__black"></div>
            <div class="mycelian-color-picker-sv__cursor"></div>
          </div>
          <div class="mycelian-color-picker-hue">
            <div class="mycelian-color-picker-hue__cursor"></div>
          </div>
          <div class="mycelian-color-picker-alpha">
            <div class="mycelian-color-picker-alpha__checker"></div>
            <div class="mycelian-color-picker-alpha__gradient"></div>
            <div class="mycelian-color-picker-alpha__cursor"></div>
          </div>
          {presets_html}
        </div>
        """
        ui.html(picker_html, sanitize=False)

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=dlg.close).props("flat dense")

            async def apply_color() -> None:
                js = (
                    f"var r=document.getElementById({json.dumps(root_id)});"
                    "if (r && r._mycelianGetValue) { return r._mycelianGetValue(); }"
                    f"var t=document.getElementById({json.dumps(text_id)});"
                    "return t ? t.value : null;"
                )
                chosen = None
                try:
                    result = await ui.run_javascript(js, timeout=2.0)
                    if isinstance(result, str) and result.strip():
                        chosen = result
                except Exception:
                    pass
                if chosen:
                    on_apply(chosen)
                dlg.close()

            ui.button("Apply", on_click=apply_color).props("dense color=primary")

    prefer = state.prefer
    bind_js = (
        "window.mycelianColorPickerBind && window.mycelianColorPickerBind("
        f"{json.dumps({'rootId': root_id, 'textId': text_id, 'initial': current_value, 'prefer': prefer})}"
        ");"
    )

    def open_and_bind() -> None:
        dlg.open()

        def _try_bind(attempt: int = 0) -> None:
            ui.run_javascript(bind_js)
            if attempt < 4:
                layout_schedule(
                    0.08,
                    lambda a=attempt + 1: _try_bind(a),
                    once=True,
                )

        layout_schedule(0.05, lambda: _try_bind(0), once=True)

    open_and_bind()


def render_color_field(
    *,
    element_id: str,
    value: Any,
    tooltip: str,
    preset_options: Optional[List[str]] = None,
    on_change: Callable[[str], None],
) -> Any:
    """Render unified swatch + text input; returns the text input element."""
    ensure_color_control_assets()
    initial = str(value or "")
    state = parse_color_string(initial)
    display_value = format_color_string(state)

    swatch_fill: dict[str, Any] = {"el": None}
    text_input: dict[str, Any] = {"el": None}

    def apply_value(new_value: str, *, notify: bool = True) -> None:
        parsed = parse_color_string(new_value)
        formatted = format_color_string(parsed)
        if text_input["el"] is not None:
            text_input["el"].value = formatted
        if swatch_fill["el"] is not None:
            _update_swatch_fill(swatch_fill["el"], formatted)
        if notify:
            on_change(formatted)

    def open_picker() -> None:
        current = text_input["el"].value if text_input["el"] else display_value

        def on_apply(chosen: str) -> None:
            apply_value(chosen)

        _open_color_picker_dialog(
            current_value=current,
            preset_options=preset_options,
            on_apply=on_apply,
        )

    def _try_parse_and_preview(raw: str) -> Optional[str]:
        text = (raw or "").strip()
        if not text:
            return None
        if text.lower() == "transparent":
            return "transparent"
        if re.match(r"^#([0-9a-f]{3}|[0-9a-f]{6})$", text, re.I):
            return format_color_string(parse_color_string(text))
        if re.match(
            r"^rgba?\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+(?:\s*,\s*[\d.]+)?\s*\)$",
            text,
            re.I,
        ):
            return format_color_string(parse_color_string(text))
        return None

    def on_text_input(e: Any) -> None:
        raw = getattr(e, "value", None)
        if raw is None and text_input["el"] is not None:
            raw = text_input["el"].value
        formatted = _try_parse_and_preview(str(raw or ""))
        if formatted and swatch_fill["el"] is not None:
            _update_swatch_fill(swatch_fill["el"], formatted)

    def on_text_commit(_e: Any = None) -> None:
        raw = text_input["el"].value if text_input["el"] else ""
        formatted = _try_parse_and_preview(raw)
        if formatted is None:
            parsed = parse_color_string(raw)
            if (raw or "").strip():
                formatted = format_color_string(parsed)
            else:
                return
        if text_input["el"] is not None and text_input["el"].value != formatted:
            text_input["el"].value = formatted
        if swatch_fill["el"] is not None:
            _update_swatch_fill(swatch_fill["el"], formatted)
        on_change(formatted)

    with ui.row().classes("mycelian-color-row w-full"):
        with ui.element("div").classes("mycelian-color-swatch").on("click", open_picker):
            ui.element("div").classes("mycelian-color-swatch__checker")
            fill_el = ui.element("div").classes("mycelian-color-swatch__fill")
            swatch_fill["el"] = fill_el
            _update_swatch_fill(fill_el, display_value)

        text_input["el"] = form_input(
            tooltip=tooltip,
            value=display_value,
            placeholder="#hex, rgb(), rgba(), transparent",
            classes="grow font-mono text-sm",
            on_change=on_text_input,
        )
        text_input["el"].on("blur", on_text_commit)
        text_input["el"].on("keydown.enter", on_text_commit)

    return text_input["el"]

#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024-2026 Mycelian

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import copy
import html
import inspect
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from nicegui import ui
from nicegui.elements.mixins.text_element import TextElement

from .. import web_engine as web_engine_module

# Use proper relative import for template_config_parser
from ..custom_sources_preview_mocks import get_mock_actions
from ..notification_engine import nav_actions_settings, nav_source_template, nav_spore_template, notify
from ..template_copy_progress import (
    mark_template_copy_idle as _mark_template_copy_idle,
    set_template_copy_state as _set_template_copy_state,
    template_copy_is_active,
)
from ..path_utils import get_assets_path, get_template_path
from ..template_config_parser import TemplateConfigParser
from ..template_preview_settings import (
    PREVIEW_HEIGHT_MAX,
    PREVIEW_HEIGHT_MIN,
    PREVIEW_WIDTH_MAX,
    PREVIEW_WIDTH_MIN,
    clamp_split_editor_pct,
    clear_template_preview_resolution,
    get_template_preview_resolution,
    load_template_preview_settings,
    save_template_preview_settings,
    set_template_preview_resolution,
)
from ..ui_buttons import outline_button, primary_button
from ..ui_color_control import is_color_transparent, render_color_field
from ..ui_form_controls import form_input, form_number, form_select, form_textarea
from ..ui_timer import app_schedule, layout_schedule, run_on_ui_loop
from .source_settings_styles import SOURCE_SETTINGS_CSS

logger = logging.getLogger(__name__)

_FONT_AUTOCOMPLETE_EXTS = {
    ".ttf",
    ".otf",
    ".ttc",
    ".woff",
    ".woff2",
}


def _list_default_font_basenames() -> List[str]:
    """Filenames in assets/default_assets/fonts for template config autocomplete."""
    fonts_dir = get_assets_path(os.path.join("default_assets", "fonts"))
    if not os.path.isdir(fonts_dir):
        return []
    names: List[str] = []
    try:
        for entry in os.listdir(fonts_dir):
            _, ext = os.path.splitext(entry)
            if ext.lower() in _FONT_AUTOCOMPLETE_EXTS:
                names.append(entry)
    except OSError:
        return []
    names.sort(key=str.lower)
    return names


# Global dictionary to store form data for each config
form_data_store = {}

# Values last loaded from disk or last successfully saved, keyed by config name.
_disk_baselines: Dict[str, Dict[str, Any]] = {}
_active_config_name: Optional[str] = None
_suppress_config_change = False

# Custom Sources preview pane (iframe + labels); populated in create_custom_sources_tab
CUSTOM_SOURCES_PREVIEW_TOKEN = str(uuid.uuid4())
_custom_sources_preview_gen: list[int] = [0]
_custom_sources_ctx: Dict[str, Any] = {}
_popout_preview_is_open = False
# Active iframe design size (OBS / stored / estimate); used by scale helpers.
_active_preview_size: Dict[str, Any] = {
    "width": 1920,
    "height": 1080,
    "config": "",
    "source": "estimate",
    "obs_source_name": "",
}
_preview_size_resolve_gen: list[int] = [0]

# Maps config file stem -> overlay HTML route (JSON ``template_name``).
_preview_route_cache: Dict[str, str] = {}

# Pop-out preview dialog (fixed position, outside the scoped tab stylesheet)
CUSTOM_CSS = """
/* Pop-out preview — modeless, draggable, resizable (Spore Studio parity) */
.mycelian-cs-popout-dialog {
    position: fixed;
    top: 80px;
    left: 80px;
    width: min(96vw, 720px);
    height: min(92vh, 520px);
    min-width: 280px;
    min-height: 200px;
    background: var(--color-bg-elevated);
    border: 1px solid var(--color-border-default);
    border-radius: 8px;
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.45);
    display: none;
    flex-direction: column;
    z-index: 9000;
    overflow: hidden;
}
.mycelian-cs-popout-dialog--active {
    user-select: none;
}
.mycelian-cs-popout-dialog__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 6px 10px;
    background: var(--color-bg-surface);
    border-bottom: 1px solid var(--color-border-default);
    cursor: move;
    user-select: none;
    flex: 0 0 auto;
}
.mycelian-cs-popout-dialog__mocktools {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 4px 6px;
    background: var(--color-bg-surface);
    border-bottom: 1px solid var(--color-border-default);
    overflow-x: auto;
    overflow-y: hidden;
    flex: 0 0 auto;
    white-space: nowrap;
}
.mycelian-cs-popout-dialog__body {
    flex: 1 1 0;
    min-height: 220px;
    position: relative;
    overflow: hidden;
    background: var(--color-bg-elevated);
}
.mycelian-cs-popout-dialog__preview-outer {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    overflow: hidden;
}
.mycelian-cs-popout-dialog__preview-inner {
    position: absolute;
    left: 0;
    top: 0;
    overflow: hidden;
}
.mycelian-cs-popout-dialog__resize {
    position: absolute;
    right: 0;
    bottom: 0;
    width: 14px;
    height: 14px;
    cursor: nwse-resize;
    background: linear-gradient(
        135deg,
        transparent 50%,
        var(--color-border-default) 50%
    );
    z-index: 2;
}

[data-mycelian-cs-popout-preview],
[data-mycelian-cs-popout-preview] > div {
    background: var(--color-bg-elevated);
}
[data-mycelian-cs-preview-outer] iframe,
[data-mycelian-cs-popout-preview] iframe {
    background: transparent;
    border: 0;
}

"""


def _template_html_exists(config_name: str) -> bool:
    if not config_name:
        return False
    return _route_html_exists(_effective_preview_route(config_name))


def _route_html_exists(route: str) -> bool:
    if not route:
        return False
    stem = get_template_path()
    return os.path.isfile(os.path.join(stem, f"{route}.html"))


def _assign_preview_iframe_src(
    inner_id: str,
    src: str,
    design_w: int,
    design_h: int,
    *,
    scale_outer_id: Optional[str] = None,
    zoom_pct: int = 100,
) -> None:
    """Set iframe src/size via the browser DOM (reliable across NiceGUI updates)."""
    onload_scale = ""
    if scale_outer_id:
        onload_scale = (
            "iframe.onload=function(){"
            "if(window.mycelianCsPreviewPostChrome){window.mycelianCsPreviewPostChrome(iframe);}"
            "if(window.mycelianCsPreviewScale){"
            f"window.mycelianCsPreviewScale({json.dumps(scale_outer_id)},"
            f"{json.dumps(inner_id)},{int(design_w)},{int(design_h)},"
            f"{int(zoom_pct)},false);"
            "}};"
        )
    js = (
        "(function(){"
        f"var inner=document.getElementById({json.dumps(inner_id)});"
        "if(!inner){return;}"
        "var iframe=inner.querySelector('iframe');"
        "if(!iframe){return;}"
        f"var __rawSrc={json.dumps(src)};"
        "var __src=(window.mycelianCsPreviewSrcWithChrome"
        "?window.mycelianCsPreviewSrcWithChrome(__rawSrc):__rawSrc);"
        "iframe.src=__src;"
        "iframe.style.visibility='visible';"
        f"iframe.width={int(design_w)};"
        f"iframe.height={int(design_h)};"
        f"iframe.style.width='{int(design_w)}px';"
        f"iframe.style.height='{int(design_h)}px';"
        "iframe.style.background='transparent';"
        "inner.style.background='transparent';"
        f"{onload_scale}"
        "})();"
    )
    try:
        ui.run_javascript(js)
        layout_schedule(0.05, lambda j=js: ui.run_javascript(j), once=True)
    except Exception as e:
        logger.debug("Preview iframe src JS skipped: %s", e)


def _clear_preview_iframe(inner_id: Optional[str]) -> None:
    if not inner_id:
        return
    js = (
        "(function(){"
        f"var inner=document.getElementById({json.dumps(inner_id)});"
        "if(!inner){return;}"
        "var iframe=inner.querySelector('iframe');"
        "if(!iframe){return;}"
        "iframe.src='about:blank';"
        "iframe.style.visibility='hidden';"
        "})();"
    )
    try:
        ui.run_javascript(js)
    except Exception as e:
        logger.debug("Preview iframe clear JS skipped: %s", e)


def _estimate_design_size(form_data: Dict[str, Any]) -> Tuple[int, int]:
    """
    Logical overlay width/height for scaling the preview to fit the panel.

    Uses common JSON keys from template configs (``DesignWidth``/``DesignHeight`` are what
    Spore Studio templates write); defaults match a typical stream canvas.
    """
    w, h = 1920, 1080
    for key in (
        "DesignWidth",
        "StreamWidth",
        "CanvasWidth",
        "OverlayWidth",
        "AlertWidth",
        "FeedWidth",
        "PreviewWidth",
    ):
        v = form_data.get(key)
        if v is not None:
            try:
                w = int(float(v))
                break
            except (TypeError, ValueError):
                pass
    for key in (
        "DesignHeight",
        "StreamHeight",
        "CanvasHeight",
        "OverlayHeight",
        "AlertHeight",
        "PreviewHeight",
    ):
        v = form_data.get(key)
        if v is not None:
            try:
                h = int(float(v))
                break
            except (TypeError, ValueError):
                pass
    w = max(PREVIEW_WIDTH_MIN, min(w, PREVIEW_WIDTH_MAX))
    h = max(PREVIEW_HEIGHT_MIN, min(h, PREVIEW_HEIGHT_MAX))
    return w, h


def _stored_or_estimated_size(config_name: str, form_data: Dict[str, Any]) -> Tuple[int, int, str]:
    """Return ``(w, h, source)`` from stored resolution or form-data estimate."""
    stored = get_template_preview_resolution(config_name) if config_name else None
    if stored is not None:
        return stored[0], stored[1], "stored"
    w, h = _estimate_design_size(form_data or {})
    return w, h, "estimate"


def _get_active_preview_size(form_data: Optional[Dict[str, Any]] = None) -> Tuple[int, int]:
    """Iframe design size for the current config (falls back to estimate)."""
    ctx = _custom_sources_ctx
    sel = ctx.get("config_select")
    config_name = sel.value if sel and sel.value else str(_active_preview_size.get("config") or "")
    if (
        config_name
        and _active_preview_size.get("config") == config_name
        and _active_preview_size.get("width")
        and _active_preview_size.get("height")
    ):
        try:
            return int(_active_preview_size["width"]), int(_active_preview_size["height"])
        except (TypeError, ValueError):
            pass
    fd = form_data if form_data is not None else form_data_store.get(config_name, {})
    w, h, _src = _stored_or_estimated_size(config_name, fd)
    return w, h


def _set_active_preview_size(
    config_name: str,
    width: int,
    height: int,
    *,
    source: str,
    obs_source_name: str = "",
) -> None:
    _active_preview_size["config"] = config_name or ""
    _active_preview_size["width"] = int(width)
    _active_preview_size["height"] = int(height)
    _active_preview_size["source"] = source
    _active_preview_size["obs_source_name"] = obs_source_name or ""


def _sync_preview_size_inputs(width: int, height: int) -> None:
    ctx = _custom_sources_ctx
    w_in = ctx.get("preview_width_input")
    h_in = ctx.get("preview_height_input")
    suppressing = ctx.setdefault("_suppress_size_input", [False])
    suppressing[0] = True
    try:
        if w_in is not None:
            w_in.value = int(width)
        if h_in is not None:
            h_in.value = int(height)
    finally:
        suppressing[0] = False
    hint = ctx.get("preview_size_hint")
    if hint is not None:
        src = str(_active_preview_size.get("source") or "")
        name = str(_active_preview_size.get("obs_source_name") or "")
        content = preview_size_chip_html(src, name)
        hint.set_content(content)
        hint.set_visibility(bool(content))


def _resize_preview_iframes(width: int, height: int) -> None:
    """Update iframe CSS dimensions and re-fit without reloading src."""
    ctx = _custom_sources_ctx
    z = _current_preview_zoom_pct()
    pairs = [
        (ctx.get("preview_outer_id"), ctx.get("preview_inner_id")),
    ]
    if _popout_preview_is_open:
        pairs.append(
            (ctx.get("popout_preview_outer_id"), ctx.get("popout_preview_inner_id"))
        )
    for oid, iid in pairs:
        if not oid or not iid:
            continue
        js = (
            "(function(){"
            f"var inner=document.getElementById({json.dumps(iid)});"
            "if(!inner){return;}"
            "var iframe=inner.querySelector('iframe');"
            "if(!iframe){return;}"
            f"iframe.width={int(width)};"
            f"iframe.height={int(height)};"
            f"iframe.style.width='{int(width)}px';"
            f"iframe.style.height='{int(height)}px';"
            "if(window.mycelianCsPreviewScale){"
            f"window.mycelianCsPreviewScale({json.dumps(oid)},{json.dumps(iid)},"
            f"{int(width)},{int(height)},{int(z)},false);"
            "}"
            "})();"
        )
        try:
            ui.run_javascript(js)
        except Exception as e:
            logger.debug("Preview resize JS skipped: %s", e)


def _apply_preview_size(
    config_name: str,
    width: int,
    height: int,
    *,
    source: str,
    obs_source_name: str = "",
    resize: bool = True,
) -> None:
    _set_active_preview_size(
        config_name, width, height, source=source, obs_source_name=obs_source_name
    )
    _sync_preview_size_inputs(width, height)
    if resize:
        _resize_preview_iframes(width, height)


def _overlay_port() -> int:
    inst = getattr(web_engine_module, "web_engine_instance", None)
    return int(getattr(inst, "port", 5000) or 5000)


def _lookup_obs_size_blocking(route: str, port: int) -> Optional[Dict[str, Any]]:
    try:
        from ..obs_service import obs_service

        return obs_service.lookup_browser_source_size(route, port)
    except Exception as e:
        logger.debug("OBS preview size lookup skipped: %s", e)
        return None


def _schedule_obs_preview_size_resolve(config_name: str) -> None:
    """Background OBS lookup; applies size only if still on the same config."""
    if not config_name:
        return
    route = _effective_preview_route(config_name)
    if not route:
        return
    _preview_size_resolve_gen[0] += 1
    gen = _preview_size_resolve_gen[0]
    port = _overlay_port()

    # Capture NiceGUI client on the UI thread — layout_schedule from a
    # background worker has no client context and silently no-ops.
    client = None
    try:
        from nicegui import context

        client = context.client
    except Exception:
        client = None

    def _worker() -> None:
        payload = _lookup_obs_size_blocking(route, port)
        logger.info(
            "Custom Sources OBS size lookup route=%s port=%s result=%s",
            route,
            port,
            payload,
        )

        def _apply() -> None:
            if _preview_size_resolve_gen[0] != gen:
                return
            sel = _custom_sources_ctx.get("config_select")
            if not sel or sel.value != config_name:
                return
            if not isinstance(payload, dict):
                return
            try:
                w = int(payload.get("width"))
                h = int(payload.get("height"))
            except (TypeError, ValueError):
                return
            w = max(PREVIEW_WIDTH_MIN, min(w, PREVIEW_WIDTH_MAX))
            h = max(PREVIEW_HEIGHT_MIN, min(h, PREVIEW_HEIGHT_MAX))
            _apply_preview_size(
                config_name,
                w,
                h,
                source="obs",
                obs_source_name=str(payload.get("source_name") or ""),
                resize=True,
            )

        def _apply_safe() -> None:
            if client is not None:
                try:
                    with client:
                        _apply()
                    return
                except Exception as e:
                    logger.debug("OBS size apply with client failed: %s", e)
            try:
                _apply()
            except Exception as e:
                logger.debug("OBS size apply failed: %s", e)

        # app.timer does not need a layout slot (unlike layout_schedule), but
        # timer creation is not thread-safe — hop onto the NiceGUI loop first.
        try:
            run_on_ui_loop(lambda: app_schedule(0.05, _apply_safe, once=True))
        except Exception as e:
            logger.debug("OBS size app_schedule failed: %s", e)

    try:
        import threading

        threading.Thread(
            target=_worker, name="CsPreviewObsSize", daemon=True
        ).start()
    except Exception as e:
        logger.debug("Could not start OBS size resolve thread: %s", e)


def _resolve_preview_size_for_config(
    config_name: str,
    form_data: Optional[Dict[str, Any]] = None,
    *,
    try_obs: bool = True,
) -> Tuple[int, int]:
    """
    Apply stored/estimate immediately; optionally schedule OBS override.

    Returns the size applied synchronously (before OBS completes).
    """
    fd = form_data if form_data is not None else form_data_store.get(config_name, {})
    w, h, src = _stored_or_estimated_size(config_name, fd or {})
    _apply_preview_size(config_name, w, h, source=src, resize=False)
    if try_obs:
        _schedule_obs_preview_size_resolve(config_name)
    return w, h


_PREVIEW_SCALE_IIFE = r"""
(function () {
  if (window.mycelianCsPreviewScale) { return; }
  window.mycelianCsPreviewChromeBg = function () {
    var root = document.documentElement;
    var bg = getComputedStyle(root).getPropertyValue("--color-bg-elevated").trim();
    if (!bg) {
      bg = getComputedStyle(root).getPropertyValue("--color-bg-base").trim();
    }
    if (!bg) { bg = "#1a1d24"; }
    return bg;
  };
  window.mycelianCsPreviewSrcWithChrome = function (src) {
    var url = String(src || "");
    if (url.indexOf("__preview_chrome_bg=") >= 0) { return url; }
    var bg = window.mycelianCsPreviewChromeBg();
    return url + (url.indexOf("?") >= 0 ? "&" : "?") + "__preview_chrome_bg=" + encodeURIComponent(bg);
  };
  window.mycelianCsPreviewPostChrome = function (iframe) {
    if (!iframe || !iframe.contentWindow) { return; }
    try {
      iframe.contentWindow.postMessage(
        { type: "mycelian_preview_chrome", bg: window.mycelianCsPreviewChromeBg() },
        "*"
      );
    } catch (e) {}
  };
  function getState(outer) {
    if (!outer._mycelianCs) {
      outer._mycelianCs = {
        designW: 1920,
        designH: 1080,
        zoomPct: 100,
        panX: 0,
        panY: 0,
        innerId: null,
      };
    }
    return outer._mycelianCs;
  }
  function clampPan(state, cw, ch, scaledW, scaledH) {
    var cx = (cw - scaledW) / 2;
    var cy = (ch - scaledH) / 2;
    var fx = cx + state.panX;
    var fy = cy + state.panY;
    if (scaledW <= cw) {
      fx = cx;
      state.panX = 0;
    } else {
      var minFx = cw - scaledW;
      fx = Math.max(minFx, Math.min(0, fx));
      state.panX = fx - cx;
    }
    if (scaledH <= ch) {
      fy = cy;
      state.panY = 0;
    } else {
      var minFy = ch - scaledH;
      fy = Math.max(minFy, Math.min(0, fy));
      state.panY = fy - cy;
    }
    return { fx: fx, fy: fy };
  }
  function applyTransform(outer) {
    var state = getState(outer);
    var inner = document.getElementById(state.innerId);
    if (!inner) { return; }
    var cw = outer.clientWidth || 1;
    var ch = outer.clientHeight || 1;
    var sFit = Math.min(cw / state.designW, ch / state.designH);
    if (!isFinite(sFit) || sFit <= 0) { sFit = 1; }
    var eff = sFit * (state.zoomPct / 100);
    var scaledW = state.designW * eff;
    var scaledH = state.designH * eff;
    var pos = clampPan(state, cw, ch, scaledW, scaledH);
    inner.style.transformOrigin = "top left";
    inner.style.width = state.designW + "px";
    inner.style.height = state.designH + "px";
    inner.style.transform = "translate(" + pos.fx + "px," + pos.fy + "px) scale(" + eff + ")";
    inner.style.background = "transparent";
    var frame = outer.querySelector("[data-cs-frame]");
    if (frame) {
      frame.style.left = pos.fx + "px";
      frame.style.top = pos.fy + "px";
      frame.style.width = scaledW + "px";
      frame.style.height = scaledH + "px";
    }
    var zoomLabelId = outer.getAttribute("data-zoom-label");
    var zoomLabel = zoomLabelId ? document.getElementById(zoomLabelId) : null;
    if (zoomLabel) { zoomLabel.textContent = Math.round(eff * 100) + "%"; }
    var iframe = inner.querySelector("iframe");
    if (iframe) {
      iframe.setAttribute("width", String(state.designW));
      iframe.setAttribute("height", String(state.designH));
      iframe.style.width = state.designW + "px";
      iframe.style.height = state.designH + "px";
      iframe.style.pointerEvents = "none";
      iframe.style.background = "transparent";
    }
  }
  function ensureOverlay(outer) {
    var overlay = outer.querySelector(".mycelian-cs-pan-overlay");
    if (overlay) { return overlay; }
    overlay = document.createElement("div");
    overlay.className = "mycelian-cs-pan-overlay";
    overlay.style.position = "absolute";
    overlay.style.left = "0";
    overlay.style.top = "0";
    overlay.style.right = "0";
    overlay.style.bottom = "0";
    overlay.style.zIndex = "10";
    overlay.style.cursor = "grab";
    overlay.style.background = "transparent";
    overlay.style.userSelect = "none";
    outer.appendChild(overlay);
    var dragging = false;
    var lastX = 0, lastY = 0;
    overlay.addEventListener("mousedown", function (ev) {
      if (ev.button !== 0) { return; }
      dragging = true;
      overlay.style.cursor = "grabbing";
      lastX = ev.clientX;
      lastY = ev.clientY;
      ev.preventDefault();
      ev.stopPropagation();
    });
    document.addEventListener("mousemove", function (ev) {
      if (!dragging) { return; }
      var dx = ev.clientX - lastX;
      var dy = ev.clientY - lastY;
      lastX = ev.clientX;
      lastY = ev.clientY;
      var state = getState(outer);
      state.panX += dx;
      state.panY += dy;
      applyTransform(outer);
    });
    document.addEventListener("mouseup", function () {
      if (dragging) {
        dragging = false;
        overlay.style.cursor = "grab";
      }
    });
    return overlay;
  }
  window.mycelianCsPreviewScale = function (outerId, innerId, designW, designH, zoomPct, resetPan) {
    var outer = document.getElementById(outerId);
    var inner = document.getElementById(innerId);
    if (!outer || !inner) { return; }
    outer.style.overflow = "hidden";
    outer.style.position = "relative";
    var state = getState(outer);
    state.innerId = innerId;
    state.designW = Number(designW) || state.designW;
    state.designH = Number(designH) || state.designH;
    var z = Number(zoomPct);
    if (isFinite(z) && z > 0) { state.zoomPct = z; }
    if (resetPan) { state.panX = 0; state.panY = 0; }
    ensureOverlay(outer);
    applyTransform(outer);
    if (outer._mycelianCsRo) { outer._mycelianCsRo.disconnect(); }
    outer._mycelianCsRo = new ResizeObserver(function () { applyTransform(outer); });
    outer._mycelianCsRo.observe(outer);
  };
  window.mycelianCsPreviewRefreshOuter = function (outerId) {
    var outer = document.getElementById(outerId);
    if (outer) { applyTransform(outer); }
  };
  window.mycelianCsPreviewSetZoom = function (outerId, zoomPct, resetPan) {
    var outer = document.getElementById(outerId);
    if (!outer) { return; }
    var state = getState(outer);
    var z = Number(zoomPct);
    if (isFinite(z) && z > 0) { state.zoomPct = z; }
    if (resetPan) { state.panX = 0; state.panY = 0; }
    applyTransform(outer);
  };
  window.mycelianCsSplitDrag = function (rowId, leftId, rightId, minLeftPct, maxLeftPct, initialPct) {
    var row = document.getElementById(rowId);
    var divider = row && row.querySelector(".mycelian-cs-split-divider");
    if (!row || !divider) { return; }
    if (divider._mycelianBound) { return; }
    divider._mycelianBound = true;
    var dragging = false;
    var startX = 0, startLeftPct = 50, lastPct = null;
    minLeftPct = Number(minLeftPct) || 28;
    maxLeftPct = Number(maxLeftPct) || 78;
    function getCurrentLeftPct() {
      var left = document.getElementById(leftId);
      var rowW = row.clientWidth || 1;
      var leftW = left ? left.getBoundingClientRect().width : rowW / 2;
      return Math.max(minLeftPct, Math.min(maxLeftPct, (leftW / rowW) * 100));
    }
    function applyPct(pct) {
      var left = document.getElementById(leftId);
      var right = document.getElementById(rightId);
      if (left) {
        left.style.flex = pct + " " + pct + " 0%";
        left.style.minWidth = "0";
        left.style.maxWidth = "100%";
      }
      if (right) {
        var rp = 100 - pct;
        right.style.flex = rp + " " + rp + " 0%";
        right.style.minWidth = "160px";
        right.style.maxWidth = "100%";
      }
    }
    var init = Number(initialPct);
    applyPct(isFinite(init) && init > 0 ? Math.max(minLeftPct, Math.min(maxLeftPct, init)) : getCurrentLeftPct());
    divider.addEventListener("mousedown", function (ev) {
      if (ev.button !== 0) { return; }
      dragging = true;
      startX = ev.clientX;
      startLeftPct = getCurrentLeftPct();
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      ev.preventDefault();
      ev.stopPropagation();
    });
    document.addEventListener("mousemove", function (ev) {
      if (!dragging) { return; }
      var rowW = row.clientWidth || 1;
      var dx = ev.clientX - startX;
      var pct = startLeftPct + (dx / rowW) * 100;
      pct = Math.max(minLeftPct, Math.min(maxLeftPct, pct));
      lastPct = pct;
      applyPct(pct);
      var outer = document.querySelector("[data-mycelian-cs-preview-outer]");
      if (outer) { applyTransform(outer); }
    });
    document.addEventListener("mouseup", function () {
      if (dragging) {
        dragging = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        if (lastPct !== null && typeof emitEvent === "function") {
          emitEvent("mycelian-cs-split", Math.round(lastPct * 10) / 10);
        }
        lastPct = null;
      }
    });
  };
})();
"""

_POPOUT_DIALOG_IIFE = r"""
(function () {
  if (window.mycelianCsPopoutLoadPreview) { return; }
  function popoutVisible(dlg) {
    return dlg && dlg.style.display === "flex";
  }
  window.mycelianCsPopoutSetup = function (dialogId) {
    var dlg = document.getElementById(dialogId);
    if (!dlg || dlg._mycelianPopoutBound) { return; }
    dlg._mycelianPopoutBound = true;
    var head = dlg.querySelector("[data-popout-drag-handle]");
    var resize = dlg.querySelector("[data-popout-resize-handle]");
    if (!head || !resize) { return; }
    var dragData = null;
    var resizeData = null;
    function endInteraction() {
      dragData = null;
      resizeData = null;
      dlg.classList.remove("mycelian-cs-popout-dialog--active");
    }
    head.addEventListener("mousedown", function (ev) {
      if (ev.button !== 0 || ev.target.closest("button")) { return; }
      var rect = dlg.getBoundingClientRect();
      dragData = {
        startX: ev.clientX,
        startY: ev.clientY,
        origLeft: rect.left,
        origTop: rect.top
      };
      dlg.classList.add("mycelian-cs-popout-dialog--active");
      ev.preventDefault();
    });
    resize.addEventListener("mousedown", function (ev) {
      if (ev.button !== 0) { return; }
      var rect = dlg.getBoundingClientRect();
      resizeData = {
        startX: ev.clientX,
        startY: ev.clientY,
        origW: rect.width,
        origH: rect.height
      };
      dlg.classList.add("mycelian-cs-popout-dialog--active");
      ev.preventDefault();
      ev.stopPropagation();
    });
    document.addEventListener("mousemove", function (ev) {
      if (dragData) {
        var dlgW = dlg.offsetWidth || 280;
        var dlgH = dlg.offsetHeight || 200;
        var x = dragData.origLeft + (ev.clientX - dragData.startX);
        var y = dragData.origTop + (ev.clientY - dragData.startY);
        x = Math.max(0, Math.min(window.innerWidth - dlgW, x));
        y = Math.max(0, Math.min(window.innerHeight - dlgH, y));
        dlg.style.left = x + "px";
        dlg.style.top = y + "px";
        return;
      }
      if (resizeData) {
        var rect = dlg.getBoundingClientRect();
        var maxW = Math.max(280, window.innerWidth - rect.left);
        var maxH = Math.max(200, window.innerHeight - rect.top);
        var w = Math.max(280, resizeData.origW + (ev.clientX - resizeData.startX));
        var h = Math.max(200, resizeData.origH + (ev.clientY - resizeData.startY));
        dlg.style.width = Math.min(w, maxW) + "px";
        dlg.style.height = Math.min(h, maxH) + "px";
        var outer = dlg.querySelector("[data-mycelian-cs-popout-preview]");
        if (outer && window.mycelianCsPopoutFitPreview) {
          var dw = parseInt(outer.getAttribute("data-design-w") || "1920", 10);
          var dh = parseInt(outer.getAttribute("data-design-h") || "1080", 10);
          var z = parseInt(outer.getAttribute("data-zoom-pct") || "100", 10);
          var iid = outer.getAttribute("data-inner-id") || "";
          window.mycelianCsPopoutFitPreview(outer.id, iid, dw, dh, z);
        }
      }
    });
    document.addEventListener("mouseup", endInteraction);
  };
  window.mycelianCsPopoutFitPreview = function (outerId, innerId, designW, designH, zoomPct) {
    var outer = document.getElementById(outerId);
    var inner = innerId ? document.getElementById(innerId) : null;
    if (!outer || !inner) { return; }
    var iframe = inner.querySelector("iframe") || outer.querySelector("iframe");
    if (!iframe) { return; }
    var cw = outer.clientWidth;
    var ch = outer.clientHeight;
    if (cw < 4 || ch < 4) {
      setTimeout(function () {
        window.mycelianCsPopoutFitPreview(outerId, innerId, designW, designH, zoomPct);
      }, 50);
      return;
    }
    var zoom = Number(zoomPct) || 100;
    var sFit = Math.min(cw / designW, ch / designH);
    if (!isFinite(sFit) || sFit <= 0) { sFit = 1; }
    var eff = sFit * (zoom / 100);
    var sw = Math.max(1, Math.round(designW * eff));
    var sh = Math.max(1, Math.round(designH * eff));
    inner.style.position = "absolute";
    inner.style.left = Math.round((cw - sw) / 2) + "px";
    inner.style.top = Math.round((ch - sh) / 2) + "px";
    inner.style.width = sw + "px";
    inner.style.height = sh + "px";
    inner.style.overflow = "hidden";
    inner.style.transform = "none";
    inner.style.background = "transparent";
    iframe.style.display = "block";
    iframe.style.border = "none";
    iframe.style.width = designW + "px";
    iframe.style.height = designH + "px";
    iframe.style.background = "transparent";
    iframe.style.transform = "scale(" + eff + ")";
    iframe.style.transformOrigin = "top left";
    var panOverlay = outer.querySelector(".mycelian-cs-pan-overlay");
    if (panOverlay) { panOverlay.remove(); }
  };
  window.mycelianCsPopoutLoadPreview = function (
    dialogId, outerId, innerId, src, designW, designH, zoomPct
  ) {
    var dlg = document.getElementById(dialogId);
    if (dlg) {
      window.mycelianCsPopoutSetup(dialogId);
      dlg.style.display = "flex";
    }
    var outer = document.getElementById(outerId);
    if (outer) {
      outer.setAttribute("data-design-w", String(designW));
      outer.setAttribute("data-design-h", String(designH));
      outer.setAttribute("data-zoom-pct", String(zoomPct));
      outer.setAttribute("data-inner-id", innerId);
    }
    function locateIframe() {
      var dlg = document.getElementById(dialogId);
      if (dlg) {
        var scoped = dlg.querySelector("[data-mycelian-cs-popout-preview] iframe");
        if (scoped) { return scoped; }
        var any = dlg.querySelector("iframe");
        if (any) { return any; }
      }
      var inner = document.getElementById(innerId);
      if (inner) {
        var inFrame = inner.querySelector("iframe");
        if (inFrame) { return inFrame; }
      }
      return outer ? outer.querySelector("iframe") : null;
    }
    function fit() {
      window.mycelianCsPopoutFitPreview(outerId, innerId, designW, designH, zoomPct);
    }
    function applySrc() {
      var iframe = locateIframe();
      if (!iframe) {
        setTimeout(applySrc, 40);
        return;
      }
      iframe.onload = function () {
        if (window.mycelianCsPreviewPostChrome) {
          window.mycelianCsPreviewPostChrome(iframe);
        }
        fit();
      };
      iframe.src = window.mycelianCsPreviewSrcWithChrome
        ? window.mycelianCsPreviewSrcWithChrome(src)
        : src;
      iframe.style.visibility = "visible";
      iframe.setAttribute("width", String(designW));
      iframe.setAttribute("height", String(designH));
      requestAnimationFrame(function () { requestAnimationFrame(fit); });
      setTimeout(fit, 80);
      setTimeout(fit, 250);
    }
    requestAnimationFrame(function () { requestAnimationFrame(applySrc); });
  };
  window.mycelianCsPopoutOpen = function (dialogId) {
    var dlg = document.getElementById(dialogId);
    if (!dlg) { return; }
    window.mycelianCsPopoutSetup(dialogId);
    dlg.style.display = "flex";
  };
  window.mycelianCsPopoutClose = function (dialogId) {
    var dlg = document.getElementById(dialogId);
    if (!dlg) { return; }
    dlg.style.display = "none";
    var iframe = dlg.querySelector("[data-mycelian-cs-popout-preview] iframe")
      || dlg.querySelector("iframe");
    if (iframe) { iframe.src = "about:blank"; }
  };
  window.mycelianCsPopoutIsOpen = popoutVisible;
})();
"""


_SECTION_NAV_IIFE = r"""
(function () {
  if (window.mycelianSsxSyncNav) { return; }
  window.mycelianSsxSyncNav = function (formId, navId) {
    var form = document.getElementById(formId);
    var nav = document.getElementById(navId);
    if (!form || !nav) { return; }
    var secs = form.querySelectorAll("[data-sec]");
    var visible = [];
    for (var i = 0; i < secs.length; i++) {
      if (secs[i].offsetParent !== null) { visible.push(secs[i]); }
    }
    var cur = visible.length ? visible[0].getAttribute("data-sec") : null;
    var top = form.scrollTop + 16;
    for (var j = 0; j < visible.length; j++) {
      if (visible[j].offsetTop <= top) { cur = visible[j].getAttribute("data-sec"); } else { break; }
    }
    if (visible.length && form.scrollTop > 0 && form.scrollTop + form.clientHeight >= form.scrollHeight - 2) {
      cur = visible[visible.length - 1].getAttribute("data-sec");
    }
    var items = nav.querySelectorAll("[data-nav]");
    for (var k = 0; k < items.length; k++) {
      var on = items[k].getAttribute("data-nav") === cur;
      items[k].classList.toggle("is-on", on);
      if (on) {
        var list = items[k].closest(".ssx-navlist") || nav;
        var it = items[k].offsetTop, ib = it + items[k].offsetHeight;
        if (it < list.scrollTop) { list.scrollTop = it - 24; }
        else if (ib > list.scrollTop + list.clientHeight) { list.scrollTop = ib - list.clientHeight + 8; }
      }
    }
  };
  window.mycelianSsxBindNav = function (formId, navId) {
    var form = document.getElementById(formId);
    if (!form) { return; }
    if (!form._ssxNavBound) {
      form._ssxNavBound = true;
      var raf = 0;
      form.addEventListener("scroll", function () {
        if (raf) { return; }
        raf = requestAnimationFrame(function () { raf = 0; window.mycelianSsxSyncNav(formId, navId); });
      }, { passive: true });
    }
    window.mycelianSsxSyncNav(formId, navId);
  };
  window.mycelianSsxScrollTo = function (formId, secId) {
    var form = document.getElementById(formId);
    var sec = document.getElementById(secId);
    if (!form || !sec) { return; }
    form.scrollTop = Math.max(0, sec.offsetTop - 10);
  };
})();
"""


def _ensure_preview_scale_script() -> None:
    """Define the preview JS helpers on `window` via run_javascript (eval).

    NOTE: We intentionally do NOT use ui.add_head_html here. Per the HTML5 spec,
    <script> tags injected via document.head.insertAdjacentHTML(...) (which is
    what NiceGUI's add_head_html does at runtime with shared=False) have their
    "already started" flag set to true and DO NOT execute. ui.run_javascript
    sends the code to the client which evaluates it via eval(), which runs
    the IIFE, defines window.mycelianCs* helpers, and lets later
    ui.run_javascript("window.mycelianCsPreviewScale(...)") calls succeed.
    """
    try:
        ui.run_javascript(_PREVIEW_SCALE_IIFE)
        ui.run_javascript(_POPOUT_DIALOG_IIFE)
        ui.run_javascript(_SECTION_NAV_IIFE)
    except Exception as e:
        logger.debug("Preview scale JS injection skipped: %s", e)


def _popout_preview_scale_args() -> Tuple[Optional[str], Optional[str], int, int, int]:
    ctx = _custom_sources_ctx
    sel = ctx.get("config_select")
    popout_oid = ctx.get("popout_preview_outer_id")
    popout_iid = ctx.get("popout_preview_inner_id")
    dw, dh, z = 1920, 1080, 100
    if sel and sel.value:
        fd = form_data_store.get(sel.value, {})
        dw, dh = _get_active_preview_size(fd)
        z = _current_preview_zoom_pct()
    return popout_oid, popout_iid, dw, dh, z


def _load_popout_preview(config_name: str, fd: dict) -> None:
    """Show pop-out dialog and load the template iframe (Spore Studio-style fit)."""
    if not _popout_preview_is_open:
        return
    ctx = _custom_sources_ctx
    dialog_id = ctx.get("popout_preview_dialog_id")
    outer_id = ctx.get("popout_preview_outer_id")
    inner_id = ctx.get("popout_preview_inner_id")
    ph = ctx.get("popout_preview_placeholder")
    if not dialog_id or not outer_id or not inner_id:
        return

    if not config_name:
        if ph:
            ph.text = "Select a configuration to preview."
            ph.visible = True
        _clear_preview_iframe(inner_id)
        return

    route = _effective_preview_route(config_name)
    inst = getattr(web_engine_module, "web_engine_instance", None)
    if not inst or not getattr(inst, "is_running", False):
        if ph:
            ph.text = (
                "Overlay server is not ready yet. It starts with the alert system."
            )
            ph.visible = True
        return

    if not _route_html_exists(route):
        if ph:
            ph.text = (
                f"No browser template at templates/{route}.html — preview unavailable."
            )
            ph.visible = True
        return

    port = getattr(inst, "port", 5000) or 5000
    cache_bust = time.time()
    src = (
        f"http://127.0.0.1:{port}/{route}"
        f"?__preview_token={CUSTOM_SOURCES_PREVIEW_TOKEN}&_cb={cache_bust}"
    )
    dw, dh = _get_active_preview_size(fd)
    z = _current_preview_zoom_pct()

    if ph:
        ph.text = ""
        ph.visible = False

    _ensure_preview_scale_script()
    js = (
        "window.mycelianCsPopoutLoadPreview && window.mycelianCsPopoutLoadPreview("
        f"{json.dumps(dialog_id)}, {json.dumps(outer_id)}, {json.dumps(inner_id)}, "
        f"{json.dumps(src)}, {int(dw)}, {int(dh)}, {int(z)})"
    )
    try:
        ui.run_javascript(js)
        layout_schedule(0.15, lambda j=js: ui.run_javascript(j), once=True)
        layout_schedule(0.4, lambda j=js: ui.run_javascript(j), once=True)
    except Exception as e:
        logger.debug("Popout preview load JS skipped: %s", e)


def _open_popout_preview_dialog(dialog_id: str) -> None:
    _ensure_preview_scale_script()
    js = (
        "window.mycelianCsPopoutOpen && "
        f"window.mycelianCsPopoutOpen({json.dumps(dialog_id)})"
    )
    try:
        ui.run_javascript(js)
    except Exception as e:
        logger.debug("Popout open JS skipped: %s", e)


def _close_popout_preview_dialog(dialog_id: str) -> None:
    global _popout_preview_is_open
    _popout_preview_is_open = False
    _close_popout_preview_dialog_js(dialog_id)


def _close_popout_preview_dialog_js(dialog_id: str) -> None:
    js = (
        "window.mycelianCsPopoutClose && "
        f"window.mycelianCsPopoutClose({json.dumps(dialog_id)})"
    )
    try:
        ui.run_javascript(js)
    except Exception as e:
        logger.debug("Popout close JS skipped: %s", e)


PREVIEW_ZOOM_MIN = 25
PREVIEW_ZOOM_MAX = 200
# Zoom is a percentage of "fit to the stage"; − / + walk these steps.
PREVIEW_ZOOM_STEPS = (25, 33, 50, 67, 75, 90, 100, 110, 125, 150, 175, 200)


def next_preview_zoom(current: Any, direction: int) -> int:
    """The next zoom step above (``direction`` > 0) or below the current zoom."""
    try:
        z = int(current)
    except (TypeError, ValueError):
        z = 100
    if direction > 0:
        return next((step for step in PREVIEW_ZOOM_STEPS if step > z), PREVIEW_ZOOM_MAX)
    return next((step for step in reversed(PREVIEW_ZOOM_STEPS) if step < z), PREVIEW_ZOOM_MIN)


def _current_preview_zoom_pct() -> int:
    try:
        z = int(_custom_sources_ctx.get("preview_zoom_pct", 100))
    except (TypeError, ValueError):
        z = 100
    return max(PREVIEW_ZOOM_MIN, min(PREVIEW_ZOOM_MAX, z))


def _run_preview_scale_js(reset_pan: bool) -> None:
    """Apply fit/zoom/pan transform to the preview pane (uses form_data + zoom slider)."""
    ctx = _custom_sources_ctx
    sel = ctx.get("config_select")
    if not sel or not sel.value:
        return
    fd = form_data_store.get(sel.value, {})
    dw, dh = _get_active_preview_size(fd)
    z = _current_preview_zoom_pct()
    oid = ctx.get("preview_outer_id")
    iid = ctx.get("preview_inner_id")
    if not oid or not iid:
        return
    js = (
        "window.mycelianCsPreviewScale && window.mycelianCsPreviewScale("
        f"{json.dumps(oid)}, {json.dumps(iid)}, {int(dw)}, {int(dh)}, {int(z)}, "
        f"{str(reset_pan).lower()})"
    )
    try:
        ui.run_javascript(js)
        layout_schedule(0.15, lambda j=js: ui.run_javascript(j), once=True)
    except Exception as e:
        logger.debug("Preview scale JS skipped: %s", e)


def _run_preview_zoom_js(reset_pan: bool) -> None:
    """Apply only the zoom delta to the existing preview transform (no iframe reload)."""
    ctx = _custom_sources_ctx
    oid = ctx.get("preview_outer_id")
    if not oid:
        return
    z = _current_preview_zoom_pct()
    js = (
        "window.mycelianCsPreviewSetZoom && window.mycelianCsPreviewSetZoom("
        f"{json.dumps(oid)}, {int(z)}, {str(reset_pan).lower()})"
    )
    try:
        ui.run_javascript(js)
    except Exception as e:
        logger.debug("Preview zoom JS skipped: %s", e)


def _apply_inline_preview_visibility(show: bool, *, persist: bool = True) -> None:
    """Show or hide the inline preview pane and expand the editor when hidden."""
    ctx = _custom_sources_ctx
    panel = ctx.get("preview_panel")
    divider = ctx.get("split_divider")
    editor_id = ctx.get("preview_editor_panel_id")
    show_btn = ctx.get("show_preview_button")
    if panel is not None:
        panel.set_visibility(show)
    if divider is not None:
        divider.set_visibility(show)
    if show_btn is not None:
        show_btn.set_visibility(not show)
    if editor_id:
        flex = "100" if not show else f"{clamp_split_editor_pct(ctx.get('split_pct')):g}"
        js = (
            f"var el=document.getElementById({json.dumps(editor_id)});"
            f"if(el){{el.style.flex='{flex} {flex} 0%';}}"
        )
        try:
            ui.run_javascript(js)
        except Exception as e:
            logger.debug("Inline preview visibility JS skipped: %s", e)
    if persist:
        save_template_preview_settings({"show_inline_preview": bool(show)})
    if show:
        _run_preview_scale_js(False)


def _sync_preview_iframe(
    inner_id: Optional[str],
    ph_el: Any,
    config_name: str,
    fd: dict,
    *,
    scale_outer_id: Optional[str] = None,
    zoom_pct: Optional[int] = None,
) -> None:
    """Load preview URL into the iframe nested under ``inner_id``."""
    if not config_name:
        if ph_el:
            ph_el.text = "Select a configuration to preview."
            ph_el.visible = True
        _clear_preview_iframe(inner_id)
        return

    route = _effective_preview_route(config_name)
    inst = getattr(web_engine_module, "web_engine_instance", None)
    if not inst or not getattr(inst, "is_running", False):
        if ph_el:
            ph_el.text = (
                "Overlay server is not ready yet. It starts with the alert system."
            )
            ph_el.visible = True
        _clear_preview_iframe(inner_id)
        return

    if not _route_html_exists(route):
        if ph_el:
            ph_el.text = (
                f"No browser template at templates/{route}.html — preview unavailable."
            )
            ph_el.visible = True
        _clear_preview_iframe(inner_id)
        return

    port = getattr(inst, "port", 5000) or 5000
    cache_bust = time.time()
    src = (
        f"http://127.0.0.1:{port}/{route}"
        f"?__preview_token={CUSTOM_SOURCES_PREVIEW_TOKEN}&_cb={cache_bust}"
    )
    design_w, design_h = _get_active_preview_size(fd)
    z = zoom_pct if zoom_pct is not None else _current_preview_zoom_pct()

    if inner_id:
        _assign_preview_iframe_src(
            inner_id,
            src,
            design_w,
            design_h,
            scale_outer_id=scale_outer_id,
            zoom_pct=z,
        )

    if ph_el:
        ph_el.text = ""
        ph_el.visible = False


def _bind_split_divider_js() -> None:
    """Bind the draggable split divider (idempotent on the JS side)."""
    ctx = _custom_sources_ctx
    rid = ctx.get("preview_split_row_id")
    lid = ctx.get("preview_editor_panel_id")
    rrid = ctx.get("preview_panel_id")
    if not rid or not lid or not rrid:
        return
    pct = clamp_split_editor_pct(ctx.get("split_pct"))
    js = (
        "window.mycelianCsSplitDrag && window.mycelianCsSplitDrag("
        f"{json.dumps(rid)}, {json.dumps(lid)}, {json.dumps(rrid)}, 28, 78, {pct:g})"
    )
    try:
        ui.run_javascript(js)
        layout_schedule(0.2, lambda j=js: ui.run_javascript(j), once=True)
    except Exception as e:
        logger.debug("Preview split JS skipped: %s", e)


def _preview_values_differ(saved: Any, current: Any) -> bool:
    """Loose equality for dirty preview (align with update_form_data switch handling)."""
    if (saved is None or saved == "") and (current is None or current == ""):
        return False
    if isinstance(current, bool) and not isinstance(saved, bool):
        if isinstance(saved, str):
            saved = saved.lower() == "true"
        else:
            saved = bool(saved)
    elif isinstance(saved, bool) and not isinstance(current, bool):
        if isinstance(current, str):
            current = current.lower() == "true"
        else:
            current = bool(current)
    if (
        isinstance(saved, (int, float))
        and isinstance(current, (int, float))
        and not isinstance(saved, bool)
        and not isinstance(current, bool)
    ):
        try:
            return float(saved) != float(current)
        except (TypeError, ValueError):
            pass
    try:
        return saved != current
    except Exception:
        return True


def source_settings_is_dirty() -> bool:
    """True when the template open in Source Settings has unsaved edits."""
    return _config_is_dirty(_active_config_name)


def save_open_source_settings() -> bool:
    """Save the open template. Returns False when edits are still unsaved."""
    ctx = _custom_sources_ctx
    parser = ctx.get("config_parser")
    select = ctx.get("config_select")
    container = ctx.get("config_container")
    if not parser or not select or not container or not getattr(select, "value", None):
        return not source_settings_is_dirty()
    save_config(parser, select, container)
    return not _config_is_dirty(getattr(select, "value", None))


def discard_open_source_settings() -> None:
    """Reload the open template from disk."""
    ctx = _custom_sources_ctx
    name = _active_config_name
    parser = ctx.get("config_parser")
    container = ctx.get("config_container")
    if name and parser and container:
        _perform_config_reset(parser, name, container)


def _config_is_dirty(config_name: Optional[str]) -> bool:
    """True when in-memory edits differ from the last saved or loaded baseline."""
    if not config_name:
        return False
    baseline = _disk_baselines.get(config_name)
    if baseline is None:
        return False
    current = form_data_store.get(config_name, {})
    keys = set(baseline) | set(current)
    for key in keys:
        if _preview_values_differ(baseline.get(key), current.get(key)):
            return True
    return False


def _remember_disk_baseline(config_name: str, form_data: dict) -> None:
    _disk_baselines[config_name] = copy.deepcopy(form_data)


def preview_live_state(
    config_name: Optional[str], *, server_running: bool, has_template: bool, dirty: bool
) -> Tuple[str, str]:
    """(kind, text) for the chip in the preview bar; kind is "live" or "off"."""
    if not config_name:
        return "off", "No template"
    if not server_running:
        return "off", "Waiting for the overlay server"
    if not has_template:
        return "off", "No preview for this template"
    if dirty:
        return "live", "Live · shows unsaved changes"
    return "live", "Live"


def preview_live_chip_html(kind: str, text: str) -> str:
    if kind == "live":
        return f'<span class="ssx-chip is-live"><span class="dot"></span>{html.escape(text)}</span>'
    return f'<span class="ssx-chip">{_icon_html("hourglass_empty")}{html.escape(text)}</span>'


def preview_size_chip_html(source: str, obs_source_name: str = "") -> str:
    """Where the preview size came from (OBS / saved); empty for the template estimate."""
    if source == "obs":
        text = f"From OBS: {obs_source_name}" if obs_source_name else "From OBS"
        icon = "videocam"
    elif source == "stored":
        text, icon = "Saved size", "push_pin"
    else:
        return ""
    return f'<span class="ssx-chip" title="{html.escape(text, quote=True)}">{_icon_html(icon)}{html.escape(text)}</span>'


def _refresh_live_chip(config_name: Optional[str]) -> None:
    chip = _custom_sources_ctx.get("preview_live_chip")
    if chip is None:
        return
    inst = getattr(web_engine_module, "web_engine_instance", None)
    running = bool(inst and getattr(inst, "is_running", False))
    has_template = bool(config_name) and running and _route_html_exists(_effective_preview_route(config_name or ""))
    kind, text = preview_live_state(
        config_name,
        server_running=running,
        has_template=has_template,
        dirty=_config_is_dirty(config_name),
    )
    content = preview_live_chip_html(kind, text)
    if _custom_sources_ctx.get("_live_chip_html") != content:
        _custom_sources_ctx["_live_chip_html"] = content
        chip.set_content(content)


def _refresh_preview_dirty_label(config_name: str) -> None:
    """Refresh everything that shows unsaved state: the header, rows, outline and preview chip."""
    _refresh_live_chip(config_name)
    if config_name and config_name == _form_state.get("config"):
        _refresh_dirty_ui()


def _invalidate_preview_route_cache(config_name: Optional[str] = None) -> None:
    """Clear cached JSON ``template_name`` lookup (entire tab or one config)."""
    global _preview_route_cache
    if config_name is None:
        _preview_route_cache.clear()
    else:
        _preview_route_cache.pop(config_name, None)


def _effective_preview_route(config_name: str) -> str:
    """Route stem served at ``/{route}.html`` from config JSON ``template_name``."""
    if not config_name:
        return ""
    cached = _preview_route_cache.get(config_name)
    if cached is not None:
        return cached
    cp = _custom_sources_ctx.get("config_parser")
    route = config_name
    if cp:
        try:
            cfg = cp.load_config(config_name)
            tn = cfg.get("template_name")
            if isinstance(tn, str) and tn.strip():
                route = tn.strip()
        except Exception:
            pass
    _preview_route_cache[config_name] = route
    return route


def _push_hot_preview_overrides() -> None:
    """Push form values and refresh preview in-place (no iframe reload).

    Every template uses this path on setting edits. Templates may define their own
    ``loadTemplateConfig``; others rely on the preview helper's generic style sync.
    """
    ctx = _custom_sources_ctx
    sel = ctx.get("config_select")
    if not sel or not sel.value:
        return
    config_name = sel.value
    _refresh_preview_dirty_label(config_name)
    inst = getattr(web_engine_module, "web_engine_instance", None)
    if not inst or not getattr(inst, "is_running", False):
        return
    if not _route_html_exists(_effective_preview_route(config_name)):
        return
    fd = form_data_store.get(config_name, {})
    route = _effective_preview_route(config_name)
    try:
        inst.push_preview_overrides(CUSTOM_SOURCES_PREVIEW_TOKEN, route, fd)
        inst.emit_preview_config_refresh(CUSTOM_SOURCES_PREVIEW_TOKEN)
    except Exception as e:
        logger.warning("Hot preview config push failed: %s", e)
        _notify_preview_push_failed(config_name, e)


def _emit_preview_mock(event_name: str, mock_alert_type: Any, config_name: str) -> None:
    """Send one sample event to the preview (mock toolbar button)."""
    eng = getattr(web_engine_module, "web_engine_instance", None)
    if not eng or not getattr(eng, "is_running", False):
        notify(
            "Sample events need the overlay server.",
            type="warning",
            source="source_settings",
            title="The overlay server isn't running yet",
            hint="Wait for it to start (see the footer), then try again.",
            dedupe_key="source_settings:mock:no_server",
        )
        return
    ok, err, _ = eng.emit_preview_mock(
        CUSTOM_SOURCES_PREVIEW_TOKEN,
        event_name,
        None,
        alert_type=mock_alert_type,
    )
    if ok:
        return
    text = str(err or "")
    if text.startswith("No preview iframe"):
        notify(
            "",
            type="warning",
            source="source_settings",
            title="Open the preview first",
            hint="The sample event plays in the preview. Show the preview, then click the button again.",
            details=text,
        )
    elif text.startswith("No mock payload"):
        notify(
            "",
            type="warning",
            source="source_settings",
            title="No sample data for this event",
            hint="This template has no sample data for that button. Try another event.",
            details=text,
        )
    else:
        notify(
            "",
            type="error",
            source="source_settings",
            title="Couldn't send the sample event",
            hint="Reload the preview and try again.",
            details=text or None,
            actions=nav_source_template(config_name),
        )


def _notify_preview_push_failed(config_name: str, exc: Exception) -> None:
    """The preview kept the old values (background, deduped)."""
    notify(
        "The preview may show old values.",
        type="warning",
        source="source_settings",
        title="The preview didn't update",
        hint="Click the Reload preview button (↻) in the bar above the preview.",
        details=f"{config_name}: {type(exc).__name__}: {exc}",
        dedupe_key="source_settings:preview_push",
        dedupe_cooldown_sec=60.0,
    )


def _rebuild_preview_mock_toolbar(config_name: str) -> None:
    row = _custom_sources_ctx.get("mock_toolbar_row")
    if row is None:
        return
    row.clear()
    st = load_template_preview_settings()
    row.visible = bool(st.get("show_mock_toolbar", True))
    actions = get_mock_actions(config_name) if config_name else []
    with row:
        _Text("span", "Sample events").classes("ssx-mocks-label")
        if not actions:
            _Text("span", "No sample events for this template.").classes("ssx-mocks-empty")
            return
        for act in actions:
            ev = act["event"]
            label = act.get("label", ev)
            alert_type = act.get("alert_type")

            def _emit_mock(
                _e: Any = None,
                event_name: str = ev,
                mock_alert_type: Any = alert_type,
            ) -> None:
                _emit_preview_mock(event_name, mock_alert_type, config_name)

            btn = ui.element("button").classes("ssx-mock").props('type="button"')
            with btn:
                ui.icon("play_arrow")
                _Text("span", str(label))
            btn.on("click", _emit_mock)
            btn.tooltip(str(act.get("tooltip") or f"Play a sample «{ev}» event in the preview"))


def _rebuild_popout_mock_toolbar(config_name: str) -> None:
    row = _custom_sources_ctx.get("popout_mock_toolbar_row")
    if row is None:
        return
    row.clear()
    st = load_template_preview_settings()
    row.visible = bool(st.get("show_mock_toolbar", True))
    actions = get_mock_actions(config_name)
    with row:
        if not actions:
            ui.label("No mock events for this template.").classes(
                "text-xs opacity-60 shrink-0"
            )
            return
        for act in actions:
            ev = act["event"]
            label = act.get("label", ev)
            alert_type = act.get("alert_type")

            def _emit_mock(
                event_name: str = ev,
                mock_alert_type: Any = alert_type,
            ) -> None:
                _emit_preview_mock(event_name, mock_alert_type, config_name)

            ui.button(label, on_click=_emit_mock).props("dense flat size=sm").tooltip(
                str(act.get("tooltip") or f"Emit mock «{ev}»")
            )


def _flush_template_preview() -> None:
    """Push form data to WebEngine and reload the preview iframe."""
    global _popout_preview_is_open
    ctx = _custom_sources_ctx
    sel = ctx.get("config_select")
    inline_inner_id = ctx.get("preview_inner_id")
    ph = ctx.get("preview_placeholder")
    popout_inner_id = ctx.get("popout_preview_inner_id")
    popout_ph = ctx.get("popout_preview_placeholder")
    if not sel:
        return
    config_name = sel.value

    _refresh_preview_dirty_label(config_name)

    if config_name:
        route = _effective_preview_route(config_name)
        inst = getattr(web_engine_module, "web_engine_instance", None)
        fd = form_data_store.get(config_name, {})
        _resolve_preview_size_for_config(config_name, fd, try_obs=True)
        if inst and getattr(inst, "is_running", False) and _route_html_exists(route):
            try:
                inst.push_preview_overrides(
                    CUSTOM_SOURCES_PREVIEW_TOKEN, route, fd
                )
            except Exception as e:
                logger.warning("Template preview push failed: %s", e)
                _notify_preview_push_failed(config_name, e)
            _sync_preview_iframe(inline_inner_id, ph, config_name, fd)
            if _popout_preview_is_open and popout_inner_id:
                _load_popout_preview(config_name, fd)
        else:
            _sync_preview_iframe(inline_inner_id, ph, config_name, {})
            if _popout_preview_is_open and popout_inner_id:
                _sync_preview_iframe(popout_inner_id, popout_ph, config_name, {})
    else:
        _sync_preview_iframe(inline_inner_id, ph, "", {})
        if _popout_preview_is_open and popout_inner_id:
            _sync_preview_iframe(popout_inner_id, popout_ph, "", {})

    _run_preview_scale_js(True)
    _rebuild_preview_mock_toolbar(config_name or "")
    if _popout_preview_is_open:
        _rebuild_popout_mock_toolbar(config_name or "")


def _schedule_template_preview_refresh() -> None:
    _custom_sources_preview_gen[0] += 1
    gen = _custom_sources_preview_gen[0]

    def _tick() -> None:
        if _custom_sources_preview_gen[0] != gen:
            return
        sel = _custom_sources_ctx.get("config_select")
        if not sel or not sel.value:
            _flush_template_preview()
            return
        _push_hot_preview_overrides()

    layout_schedule(0.32, _tick, once=True)


# ---------------------------------------------------------------------------
#  Sections, search, units and asset fields (pure helpers; unit tested)
# ---------------------------------------------------------------------------

GENERAL_SECTION_TITLE = "General"
ROULETTE_CONFIG_NAME = "roulette"
# Templates with nothing to set here. Not marked "hidden" in their config, because
# that would also drop them from the browser source URL list.
_NO_SETTINGS_TEMPLATES = frozenset({"activity_feed"})
_SECTIONS_OPEN_BY_DEFAULT = 3
_OPTION_ID_RE = re.compile(r"^Option(\d+)")
_UNIT_RE = re.compile(r"(?:^|[\s,;:])\((px|pixels|ms|s|sec|secs|seconds|%|deg|degrees)\)", re.I)
_UNIT_ALIASES = {"pixels": "px", "sec": "s", "secs": "s", "seconds": "s", "degrees": "deg"}


@dataclass
class SettingsSection:
    """One card in the form and one entry in the outline."""

    key: str
    title: str
    elements: List[dict] = field(default_factory=list)
    option_num: Optional[int] = None


def roulette_option_number(element_id: Any) -> Optional[int]:
    """``Option12Name`` -> 12 (roulette per-option settings); None otherwise."""
    match = _OPTION_ID_RE.match(str(element_id or ""))
    return int(match.group(1)) if match else None


def roulette_option_title(option_num: int, option_name: Any) -> str:
    title = f"Option {option_num}"
    if option_name:
        title += f" ({option_name})"
    return title


def build_sections(
    config_name: str, elements: Iterable[Any], form_data: Optional[Dict[str, Any]] = None
) -> List[SettingsSection]:
    """Group config elements into sections, in file order.

    Settings before the first separator form "General". Separators with the same label
    share one section (as before). The roulette template's per-option settings become
    one "Option N (name)" section each, after the others.
    """
    form_data = form_data or {}
    ordered: List[SettingsSection] = []
    by_title: Dict[str, SettingsSection] = {}
    options: Dict[int, SettingsSection] = {}
    current: Optional[SettingsSection] = None
    is_roulette = config_name == ROULETTE_CONFIG_NAME

    def section_for(title: str, key: str) -> SettingsSection:
        sec = by_title.get(title)
        if sec is None:
            sec = SettingsSection(key=key, title=title)
            by_title[title] = sec
            ordered.append(sec)
        return sec

    for element in elements or []:
        if not isinstance(element, dict):
            continue
        if element.get("type") == "separator":
            title = str(element.get("label") or "").strip() or "Settings"
            current = section_for(title, f"s{len(ordered)}")
            continue
        if is_roulette:
            num = roulette_option_number(element.get("id"))
            if num is not None:
                sec = options.get(num)
                if sec is None:
                    sec = options[num] = SettingsSection(key=f"o{num}", title="", option_num=num)
                sec.elements.append(element)
                continue
        if current is None:
            current = section_for(GENERAL_SECTION_TITLE, "g")
        current.elements.append(element)

    sections = [sec for sec in ordered if sec.elements]
    for num in sorted(options):
        sec = options[num]
        name_id = f"Option{num}Name"
        name = form_data.get(name_id)
        if name is None:
            name = next((el.get("value") for el in sec.elements if el.get("id") == name_id), None)
        sec.title = roulette_option_title(num, name)
        sections.append(sec)
    return sections


def group_config_elements(elements):
    """Group elements by separator label (``Other`` before the first separator).

    Kept for callers that want the old dict shape; the tab itself uses :func:`build_sections`.
    """
    groups: Dict[str, list] = {}
    current_group = "Other"
    for element in elements:
        if element.get("type", "") == "separator":
            current_group = element.get("label", "")
            groups.setdefault(current_group, [])
            continue
        groups.setdefault(current_group, []).append(element)
    return groups


def normalize_search(term: Any) -> str:
    return str(term or "").strip().lower()


def setting_matches(element: dict, term: str) -> bool:
    """Search matches the label or the description (``term`` already lower-case)."""
    if not term:
        return True
    label = str(element.get("label") or element.get("id") or "").lower()
    description = str(element.get("description") or "").lower()
    return term in label or term in description


def row_matches_filters(element: dict, term: str, changed_only: bool, changed_ids: Set[str]) -> bool:
    if changed_only and str(element.get("id") or "") not in changed_ids:
        return False
    return setting_matches(element, term)


def filter_sections(
    sections: Sequence[SettingsSection], term: Any = "", changed_only: bool = False, changed_ids: Optional[Set[str]] = None
) -> List[Tuple[SettingsSection, List[dict]]]:
    """Sections that still have settings after search / "Changed only", with those settings."""
    norm = normalize_search(term)
    changed = changed_ids or set()
    out: List[Tuple[SettingsSection, List[dict]]] = []
    for sec in sections:
        kept = [el for el in sec.elements if row_matches_filters(el, norm, changed_only, changed)]
        if kept:
            out.append((sec, kept))
    return out


def changed_setting_ids(baseline: Optional[Dict[str, Any]], current: Optional[Dict[str, Any]]) -> Set[str]:
    """Setting ids whose current value differs from the saved one."""
    baseline = baseline or {}
    current = current or {}
    return {
        key
        for key in set(baseline) | set(current)
        if _preview_values_differ(baseline.get(key), current.get(key))
    }


def setting_unit(element: dict) -> str:
    """Unit shown after a number box: (px) (ms) (s) (%) (deg) in the label or description."""
    for text in (element.get("label"), element.get("description")):
        match = _UNIT_RE.search(str(text or ""))
        if match:
            unit = match.group(1).lower()
            return _UNIT_ALIASES.get(unit, unit)
    return ""


def number_step(element: dict) -> Optional[float]:
    """The JSON ``step`` for a number box (None when missing or unusable)."""
    raw = element.get("step")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        step = float(raw)
    except (TypeError, ValueError):
        return None
    if step <= 0 or step != step:
        return None
    return int(step) if step == int(step) else step


def is_gradient_value(value: Any) -> bool:
    return "gradient(" in str(value or "").lower()


FONT_FILE_EXTS = (".ttf", ".otf", ".ttc", ".woff", ".woff2")
DEFAULT_FONTS_WEB_DIR = "/assets/default_assets/fonts/"


@dataclass(frozen=True)
class AssetSpec:
    """How a setting that holds a file is browsed and written back.

    ``style``: ``web`` (``/assets/x/y.png``), ``assets`` (``assets/x/y.png``), ``relative``
    (relative to ``/assets/``) or ``filename`` (relative to ``base_dir``).
    """

    kind: str
    extensions: Tuple[str, ...]
    mode: str = "file"
    style: str = "web"
    base_dir: str = ""


def _asset_ext_lists() -> Dict[str, Tuple[str, ...]]:
    from .file_picker import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS

    return {
        "image": tuple(IMAGE_EXTS),
        "audio": tuple(AUDIO_EXTS),
        "video": tuple(VIDEO_EXTS),
        "font": FONT_FILE_EXTS,
        "media": tuple(IMAGE_EXTS) + tuple(AUDIO_EXTS) + tuple(VIDEO_EXTS),
    }


def _words(text: Any) -> List[str]:
    """``bit_image_range_0_src`` / ``BackgroundImageURL`` / labels -> lower-case words."""
    raw = str(text or "")
    raw = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", raw)
    raw = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", raw)
    return [w for w in re.split(r"[^A-Za-z0-9]+", raw.lower()) if w]


_KIND_WORDS = {
    "image": {"image", "images", "img", "picture", "icon", "avatar", "gif", "logo", "sprite", "art", "thumbnail", "png", "jpg", "jpeg", "webp", "svg"},
    "audio": {"sound", "sounds", "audio", "music", "sfx", "mp3", "wav", "ogg"},
    "video": {"video", "videos", "mp4", "webm", "mov"},
    "media": {"media"},
    "font": {"font"},
}
# Names that are a file by themselves ("Banner Background Image", "Video").
_MEDIA_NOUNS = {"image", "img", "gif", "avatar", "picture", "sprite", "thumbnail", "video"}
_FILE_WORDS = {"src", "source", "file", "filename", "path", "url", "uri", "folder", "directory", "upload"}
_FOLDER_WORDS = {"folder", "directory", "dir"}
_NOT_A_FILE_WORDS = {
    "color", "colour", "shadow", "filter", "extension", "size", "weight", "width", "height", "opacity", "radius",
    "blur", "position", "align", "alignment", "style", "duration", "delay", "volume", "speed", "family", "css",
    "spacing", "padding", "margin", "scale", "rotation", "angle", "count", "text",
}
_DESC_FILE_RE = re.compile(r"\b(url|path|file ?name|file|folder|directory|upload)\b|\.(png|gif|jpe?g|webp|svg|mp3|wav|ogg|mp4|webm|mov|ttf|otf|woff2?)\b", re.I)
_MULTI_VALUE_RE = re.compile(r"\b(several|separated|one per line|comma|list of)\b", re.I)
_DESC_ASSET_DIR_RE = re.compile(r"(?<![\w/])/?assets/([\w .\-/]*?/)(?=[\s)'\",]|$)")


def _ext_of(value: str) -> str:
    return os.path.splitext(value.split("?", 1)[0].split("#", 1)[0])[1].lower()


def _value_style(value: str) -> str:
    v = value.strip().replace("\\", "/")
    if v.startswith("/assets/"):
        return "web"
    if v.startswith("assets/"):
        return "assets"
    if "/" in v:
        return "relative"
    return "filename"


def _asset_browse_spec(element: dict, value: Any, config_name: str = "") -> Optional[AssetSpec]:
    """How to browse a setting that holds a file path/URL, or None for ordinary settings.

    A setting qualifies when its type is ``file``; when its current or saved value is an
    ``/assets/...`` path or a media/font file name; or when its id/label name a kind of
    media together with a file word ("Death Sound File", "Background Image URL") or its
    description says it is a URL/path/file. Numbers, colours, selects, CSS-ish settings
    (shadow, filter, size...) and multi-value lists never qualify.
    """
    element_type = element.get("type", "text")
    if element_type not in ("text", "file"):
        return None
    description = str(element.get("description") or "")
    if element_type != "file" and _MULTI_VALUE_RE.search(description):
        return None
    lists = _asset_ext_lists()
    current = str(value or "").strip()
    saved = str(element.get("value") or "").strip()
    name_words = set(_words(element.get("id"))) | set(_words(element.get("label")))

    kind = ""
    sample = ""
    for candidate in (current, saved):
        ext = _ext_of(candidate) if candidate else ""
        kind = next((k for k in ("image", "audio", "video", "font") if ext and ext in lists[k]), "")
        if kind:
            sample = candidate
            break
    path_value = next(
        (c for c in (current, saved) if c and c.replace("\\", "/").startswith(("/assets/", "assets/"))), ""
    )
    sample = sample or path_value or current or saved
    by_value = bool(kind or path_value)

    if not by_value and element_type != "file":
        if name_words & _NOT_A_FILE_WORDS and not name_words & {"file", "path", "src", "source", "url"}:
            return None
    if not kind:
        accept = str(element.get("accept") or "").lower()
        kind = next((k for k in ("image", "audio", "video") if accept.startswith(k)), "")
    if not kind:
        kind = next((k for k in ("image", "audio", "video", "media", "font") if name_words & _KIND_WORDS[k]), "")
        if kind and not (element_type == "file" or by_value):
            names_file = bool(name_words & _FILE_WORDS)
            describes_file = bool(_DESC_FILE_RE.search(description))
            if not (names_file or describes_file or name_words & _MEDIA_NOUNS):
                kind = ""
            elif kind == "font" and not (names_file or re.search(r"\bfile", description, re.I)):
                kind = ""
    if not kind and (element_type == "file" or by_value):
        kind = "media"
    if not kind:
        return None

    mode = "folder" if name_words & _FOLDER_WORDS else "file"
    if sample:
        style = _value_style(sample)
    elif kind == "font" or re.search(r"\bfile ?name\b", description, re.I):
        style = "filename"
    else:
        style = "web"
    base_dir = ""
    if style == "filename":
        match = _DESC_ASSET_DIR_RE.search(description)
        if kind == "font":
            base_dir = DEFAULT_FONTS_WEB_DIR
        elif match and match.group(1).strip("/"):
            base_dir = "/assets/" + match.group(1).lstrip("/")
        elif config_name:
            base_dir = f"/assets/{config_name}/"
        else:
            style = "web"
    return AssetSpec(kind=kind, extensions=lists[kind], mode=mode, style=style, base_dir=base_dir)


def asset_value_to_web_path(spec: AssetSpec, value: Any) -> str:
    """The ``/assets/...`` path a setting value points at ('' when it can't tell)."""
    v = str(value or "").strip().replace("\\", "/")
    if not v:
        return spec.base_dir if spec.style == "filename" else ""
    if v.startswith("/assets/"):
        return v
    if v.startswith("assets/"):
        return "/" + v
    if spec.style == "filename" and spec.base_dir:
        return spec.base_dir + v
    if spec.style == "relative" or "/" in v:
        return "/assets/" + v.lstrip("/")
    return ""


def asset_value_from_pick(spec: AssetSpec, web_path: str) -> str:
    """What to write into the setting for a picked ``/assets/...`` path."""
    path = str(web_path or "")
    if spec.style == "assets":
        return path.lstrip("/")
    if spec.style == "relative" and path.startswith("/assets/"):
        return path[len("/assets/"):]
    if spec.style == "filename" and spec.base_dir and path.startswith(spec.base_dir):
        return path[len(spec.base_dir):]
    return path


# ---------------------------------------------------------------------------
#  Small render helpers
# ---------------------------------------------------------------------------


class _Text(TextElement):
    """A native element with text (``<span>``, ``<option>`` ...)."""

    def __init__(self, tag: str, text: str = "") -> None:
        super().__init__(tag=tag, text=text)


def _esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def _icon_html(name: str) -> str:
    return f'<span class="ssx-mi">{_esc(name)}</span>'


def _icon_button(icon: str, tooltip: str, on_click: Callable[[], Any], *, danger: bool = False, small: bool = False):
    btn = ui.element("button").classes(
        "ssx-ibtn" + (" is-danger" if danger else "") + (" is-small" if small else "")
    ).props(f'type="button" aria-label="{_esc(tooltip)}"')
    with btn:
        ui.icon(icon)
    btn.on("click", lambda _e: on_click())
    if tooltip:
        btn.tooltip(tooltip)
    return btn


def _inject_source_settings_styles() -> None:
    from ..help_system.help_render import inject_page_stylesheet, promote_color_importance, scope_selectors

    inject_page_stylesheet(
        "mycelian-source-settings", promote_color_importance(scope_selectors(SOURCE_SETTINGS_CSS, ".ssx"))
    )


_color_field_params_cache: Optional[Set[str]] = None


def _color_field_params() -> Set[str]:
    """Keyword arguments render_color_field accepts (``on_input`` / ``allow_gradient`` are newer)."""
    global _color_field_params_cache
    if _color_field_params_cache is None:
        try:
            _color_field_params_cache = set(inspect.signature(render_color_field).parameters)
        except (TypeError, ValueError):
            _color_field_params_cache = set()
    return _color_field_params_cache


# ---------------------------------------------------------------------------
#  Form state (one rendered template at a time)
# ---------------------------------------------------------------------------


@dataclass
class _RowHandle:
    element_id: str
    element: dict
    row: Any
    set_value: Callable[[Any], None]
    visible: bool = True


@dataclass
class _SectionHandle:
    section: SettingsSection
    root: Any
    head: Any
    rows: List[_RowHandle]
    dom_id: str
    is_open: bool
    visible: bool = True
    shown: int = 0
    head_html: str = ""


# config, sections (list of _SectionHandle), rows ({id: _RowHandle}), changed (set),
# search, changed_only, nav_html, empty
_form_state: Dict[str, Any] = {"search": "", "changed_only": False}


def _sections() -> List[_SectionHandle]:
    return list(_form_state.get("sections") or [])


def _section_by_key(key: str) -> Optional[_SectionHandle]:
    for sec in _sections():
        if sec.section.key == key:
            return sec
    return None


def _filtering() -> bool:
    return bool(normalize_search(_form_state.get("search")) or _form_state.get("changed_only"))


def _section_head_html(title: str, count: int) -> str:
    return (
        f'<span class="ssx-sec-title" title="{_esc(title)}">{_esc(title)}</span>'
        f'<span class="n">{count}</span><span class="ssx-mi chev">expand_more</span>'
    )


def _update_section_head(sec: _SectionHandle) -> None:
    content = _section_head_html(sec.section.title, sec.shown if _filtering() else len(sec.rows))
    if content != sec.head_html:
        sec.head_html = content
        sec.head.set_content(content)


def _set_section_open(sec: _SectionHandle, open_: bool) -> None:
    if sec.is_open == open_:
        return
    sec.is_open = open_
    if open_:
        sec.root.classes(remove="is-closed")
    else:
        sec.root.classes(add="is-closed")


def _sync_nav_js() -> None:
    ctx = _custom_sources_ctx
    form_id, nav_id = ctx.get("form_id"), ctx.get("nav_id")
    if not form_id or not nav_id:
        return
    js = (
        "window.mycelianSsxBindNav && "
        f"window.mycelianSsxBindNav({json.dumps(form_id)}, {json.dumps(nav_id)})"
    )
    try:
        ui.run_javascript(js)
    except Exception as e:
        logger.debug("Section outline sync JS skipped: %s", e)


def _render_nav() -> None:
    nav = _custom_sources_ctx.get("nav_list")
    if nav is None:
        return
    filtering = _filtering()
    changed = _form_state.get("changed") or set()
    parts = ['<div class="ssx-navhead">Sections</div>']
    shown_any = False
    for sec in _sections():
        if filtering and not sec.shown:
            continue
        shown_any = True
        count = sec.shown if filtering else len(sec.rows)
        dot = '<span class="dot" title="Has unsaved changes"></span>' if any(r.element_id in changed for r in sec.rows) else ""
        parts.append(
            f'<div class="ssx-navitem" data-nav="{_esc(sec.section.key)}" title="{_esc(sec.section.title)}">'
            f'<span class="nm">{_esc(sec.section.title)}</span>{dot}<span class="n">{count}</span></div>'
        )
    if not shown_any:
        parts.append(
            '<div class="ssx-navnone">No settings match.</div>' if filtering and _form_state.get("config") else ""
        )
    content = "".join(parts)
    if content != _form_state.get("nav_html"):
        _form_state["nav_html"] = content
        nav.set_content(content)
        _sync_nav_js()


def _render_jump() -> None:
    """Options of the "Jump to section" list shown instead of the outline on narrow tabs."""
    jump = _custom_sources_ctx.get("jump_select")
    if jump is None:
        return
    jump.clear()
    with jump:
        _Text("option", "Jump to section…").props('value=""')
        for sec in _sections():
            _Text("option", sec.section.title).props(f'value="{_esc(sec.section.key)}"')


def _refresh_meta() -> None:
    meta = _custom_sources_ctx.get("meta_label")
    if meta is None:
        return
    sections = _sections()
    if not _form_state.get("config"):
        meta.set_text("")
        return
    settings = sum(len(sec.rows) for sec in sections)
    meta.set_text(
        f"{settings} setting{'s' if settings != 1 else ''} · {len(sections)} section{'s' if len(sections) != 1 else ''}"
    )


def _dirty_pill_html(config_name: Optional[str], changed_count: int) -> str:
    if not config_name:
        return ""
    if changed_count:
        noun = "change" if changed_count == 1 else "changes"
        return f'<span class="ssx-pill is-warn">{_icon_html("edit")}{changed_count} unsaved {noun}</span>'
    return f'<span class="ssx-pill is-ok">{_icon_html("check_circle")}Saved</span>'


def _refresh_dirty_ui() -> None:
    """Header pill, Discard, changed rows and outline dots for the open template."""
    st = _form_state
    config_name = st.get("config")
    baseline = _disk_baselines.get(config_name) if config_name else None
    current = form_data_store.get(config_name, {}) if config_name else {}
    changed = changed_setting_ids(baseline, current) if baseline is not None else set()
    previous = st.get("changed") or set()
    rows = st.get("rows") or {}
    for element_id in changed ^ previous:
        handle = rows.get(element_id)
        if handle is None:
            continue
        if element_id in changed:
            handle.row.classes(add="is-changed")
        else:
            handle.row.classes(remove="is-changed")
    st["changed"] = changed

    ctx = _custom_sources_ctx
    pill = ctx.get("dirty_pill")
    if pill is not None:
        content = _dirty_pill_html(config_name, len(changed))
        if ctx.get("_dirty_pill_html") != content:
            ctx["_dirty_pill_html"] = content
            pill.set_content(content)
    discard = ctx.get("discard_button")
    if discard is not None:
        discard.set_enabled(bool(changed))
    _render_nav()


def _apply_filters() -> None:
    """Show only settings that match the search and "Changed only", grouped by section."""
    st = _form_state
    term = normalize_search(st.get("search"))
    changed_only = bool(st.get("changed_only"))
    changed = st.get("changed") or set()
    filtering = bool(term) or changed_only
    total = 0
    for sec in _sections():
        shown = 0
        for handle in sec.rows:
            show = row_matches_filters(handle.element, term, changed_only, changed)
            if show != handle.visible:
                handle.row.set_visibility(show)
                handle.visible = show
            shown += int(show)
        sec.shown = shown
        sec_visible = shown > 0 or not filtering
        if sec_visible != sec.visible:
            sec.root.set_visibility(sec_visible)
            sec.visible = sec_visible
        if filtering and shown:
            _set_section_open(sec, True)
        _update_section_head(sec)
        total += shown
    empty = st.get("empty")
    if empty is not None:
        empty.set_visibility(filtering and total == 0 and bool(_sections()))
        title, sub = st.get("empty_labels") or (None, None)
        if title is not None:
            if changed_only and not term:
                title.set_text("No unsaved changes")
                sub.set_text("Settings you change show up here until you save or discard them.")
            else:
                title.set_text("No settings match")
                sub.set_text(
                    "Search looks at setting names and descriptions"
                    + (" among your unsaved changes." if changed_only else ".")
                )
    _render_nav()


_search_render_generation = 0


def _on_search(value: Any) -> None:
    """Debounced search (label + description); results stay under their section headings."""
    global _search_render_generation
    _form_state["search"] = str(value or "")
    _search_render_generation += 1
    generation = _search_render_generation

    def _run() -> None:
        if generation == _search_render_generation:
            _apply_filters()

    layout_schedule(0.2, _run, once=True)


def _on_changed_only(value: Any) -> None:
    _form_state["changed_only"] = bool(value)
    _apply_filters()


def _goto_section(key: Any) -> None:
    sec = _section_by_key(str(key or ""))
    if sec is None or not sec.visible:
        return
    _set_section_open(sec, True)
    form_id = _custom_sources_ctx.get("form_id")
    if not form_id:
        return
    js = (
        "window.mycelianSsxScrollTo && "
        f"window.mycelianSsxScrollTo({json.dumps(form_id)}, {json.dumps(sec.dom_id)})"
    )
    try:
        ui.run_javascript(js)
    except Exception as e:
        logger.debug("Scroll to section JS skipped: %s", e)


def _on_form_click(e: Any) -> None:
    """Delegated clicks in the form: section headers fold, ↶ reverts one setting."""
    data = getattr(e, "args", None) or {}
    if not isinstance(data, dict):
        return
    if data.get("revert"):
        _revert_setting(str(data["revert"]))
    elif data.get("sec"):
        sec = _section_by_key(str(data["sec"]))
        if sec is not None:
            _set_section_open(sec, not sec.is_open)


_FORM_CLICK_JS = (
    "(e) => {"
    " const r = e.target.closest('[data-revert]');"
    " if (r) { e.preventDefault(); emit({revert: r.dataset.revert}); return; }"
    " const h = e.target.closest('[data-sec-head]');"
    " if (h) { emit({sec: h.dataset.secHead}); }"
    "}"
)


def _revert_setting(element_id: str) -> None:
    """Put one setting back to its saved value."""
    config_name = _form_state.get("config")
    form_data = form_data_store.get(config_name) if config_name else None
    baseline = _disk_baselines.get(config_name) if config_name else None
    if form_data is None or baseline is None or element_id not in baseline:
        return
    value = copy.deepcopy(baseline[element_id])
    handle = (_form_state.get("rows") or {}).get(element_id)
    if handle is not None:
        try:
            handle.set_value(value)
        except Exception as e:
            logger.debug("Could not show the saved value for %s: %s", element_id, e)
    update_form_data(form_data, element_id, value)
    if _form_state.get("changed_only"):
        _apply_filters()


def _on_split_moved(e: Any) -> None:
    pct = clamp_split_editor_pct(getattr(e, "args", None))
    _custom_sources_ctx["split_pct"] = pct
    save_template_preview_settings({"split_editor_pct": pct})


# ---------------------------------------------------------------------------
#  Tab
# ---------------------------------------------------------------------------


def create_custom_sources_tab():
    """Build the Source Settings tab (header, outline, form, preview)."""
    ui.add_head_html(f"<style>{CUSTOM_CSS}</style>")
    _inject_source_settings_styles()
    _ensure_preview_scale_script()
    from ..help_system.contextual_help import help_button

    config_dir = "templates/template_configs"
    config_parser = TemplateConfigParser(config_dir)

    _pst_initial = load_template_preview_settings()
    split_pct = clamp_split_editor_pct(_pst_initial.get("split_editor_pct"))
    uid = uuid.uuid4().hex[:12]
    preview_outer_id = "mycelian-cs-pe-" + uid
    preview_inner_id = preview_outer_id + "-inner"
    preview_split_row_id = "mycelian-cs-row-" + uid
    preview_editor_panel_id = "mycelian-cs-ed-" + uid
    preview_panel_id = "mycelian-cs-pv-" + uid
    popout_outer_id = "mycelian-cs-pop-" + uid
    popout_inner_id = popout_outer_id + "-inner"
    popout_dialog_id = "mycelian-cs-popdlg-" + uid
    form_id = "mycelian-cs-form-" + uid
    nav_id = "mycelian-cs-nav-" + uid
    zoom_label_id = "mycelian-cs-zoom-" + uid

    def open_popout_preview() -> None:
        global _popout_preview_is_open
        _popout_preview_is_open = True
        sel = _custom_sources_ctx.get("config_select")
        if sel and sel.value:
            fd = form_data_store.get(sel.value, {})
            _load_popout_preview(sel.value, fd)
        else:
            _open_popout_preview_dialog(popout_dialog_id)

    def show_inline_preview(show: bool) -> None:
        _apply_inline_preview_visibility(show)
        if show:
            _bind_split_divider_js()

    with ui.element("div").classes("tab-surface w-full h-full flex flex-col relative p-4"):
        with ui.element("div").classes("ssx"):
            with ui.element("div").classes("ssx-shell"):
                # ---- Header ----
                with ui.element("div").classes("ssx-head"):
                    ui.html(_icon_html("tune"), sanitize=False).classes("ssx-tile")
                    with ui.element("div").classes("ssx-title"):
                        _Text("div", "Source Settings").classes("ssx-kicker")
                        with ui.element("div").classes("ssx-title-row"):
                            config_select = form_select(
                                tooltip="Choose which template to edit (type to filter)",
                                options=[],
                                label=None,
                                classes="ssx-picker",
                                with_input=True,
                                on_change=lambda e: on_config_selected(
                                    e, config_parser, config_container, config_select
                                ),
                            )
                            config_select.props("dropdown-icon=unfold_more options-dense")
                            meta_label = _Text("span", "").classes("ssx-meta")
                    dirty_pill = ui.html("", sanitize=False)
                    ui.element("span").classes("ssx-spacer")
                    show_preview_button = ui.element("button").classes("ssx-tbtn").props('type="button"')
                    with show_preview_button:
                        ui.icon("visibility")
                        _Text("span", "Show preview")
                    show_preview_button.on("click", lambda _e: show_inline_preview(True))
                    show_preview_button.tooltip("Show the live preview next to the settings")
                    show_preview_button.set_visibility(False)
                    _icon_button(
                        "refresh",
                        "Reload the template list",
                        lambda: load_config_files(config_parser, config_select, config_container),
                    )
                    _icon_button(
                        "content_copy",
                        "Duplicate this template",
                        lambda: duplicate_config(config_parser, config_select, config_container),
                    )
                    _icon_button(
                        "drive_file_rename_outline",
                        "Rename this template",
                        lambda: rename_config(config_parser, config_select, config_container),
                    )
                    _icon_button(
                        "delete_outline",
                        "Delete this template",
                        lambda: delete_config(config_parser, config_select, config_container),
                        danger=True,
                    )
                    ui.element("span").classes("ssx-sep")
                    help_button(topic_id="template_configuration", tooltip="Source Settings help", size="sm").props(
                        remove="color"
                    )
                    discard_button = outline_button(
                        "Discard", lambda: reset_config(config_parser, config_select, config_container)
                    ).props("dense").classes("px-3")
                    discard_button.disable()
                    discard_button.tooltip("Go back to the last saved version of this template")
                    primary_button(
                        "Save", lambda: save_config(config_parser, config_select, config_container), icon="check"
                    ).props("dense").classes("px-4")

                # ---- Body: outline | form | preview ----
                with ui.element("div").classes("ssx-body"):
                    with ui.element("div").classes("ssx-nav"):
                        with ui.element("label").classes("ssx-search"):
                            ui.icon("search")
                            ui.element("input").props(
                                'type="search" placeholder="Search settings" aria-label="Search settings" '
                                'autocomplete="off" spellcheck="false"'
                            ).on("input", lambda e: _on_search(e.args), js_handler="(e) => emit(e.target.value)")
                        nav_list = ui.html("", sanitize=False).classes("ssx-navlist").props(f"id={nav_id}")
                        nav_list.on(
                            "click",
                            lambda e: _goto_section(e.args),
                            js_handler="(e) => { const n = e.target.closest('[data-nav]'); if (n) emit(n.dataset.nav); }",
                        )
                        with ui.element("div").classes("ssx-navfoot"):
                            _Text("span", "Changed only")
                            ui.switch(value=False, on_change=lambda e: _on_changed_only(e.value)).props(
                                "dense"
                            ).tooltip("Show only settings with unsaved changes")

                    with ui.element("div").props(f"id={preview_split_row_id}").classes("ssx-split"):
                        editor_panel = (
                            ui.element("div")
                            .props(f"id={preview_editor_panel_id}")
                            .classes("ssx-editor")
                            .style(f"flex: {split_pct:g} {split_pct:g} 0%")
                        )
                        with editor_panel:
                            jump_select = ui.element("select").classes("ssx-jump").props('aria-label="Jump to section"')
                            jump_select.on(
                                "change",
                                lambda e: _goto_section(e.args),
                                js_handler="(e) => { const v = e.target.value; e.target.selectedIndex = 0; if (v) emit(v); }",
                            )
                            config_container = ui.element("div").classes("ssx-form").props(f"id={form_id}")
                            config_container.on("click", _on_form_click, js_handler=_FORM_CLICK_JS)
                        split_divider = ui.element("div").classes("mycelian-cs-split-divider ssx-divider")
                        split_divider.props('title="Drag to resize the settings and the preview"')
                        preview_panel = (
                            ui.element("div")
                            .props(f"id={preview_panel_id}")
                            .classes("ssx-prev")
                            .style(f"flex: {100 - split_pct:g} {100 - split_pct:g} 0%; min-width: 160px")
                        )

                        with ui.dialog() as preview_settings_dialog, ui.card():
                            ui.label("Preview settings").classes("text-lg font-medium mb-2")
                            preview_sound_switch = ui.switch(
                                "Enable preview sounds",
                                value=bool(_pst_initial.get("enable_preview_sounds", True)),
                            )
                            preview_toolbar_switch = ui.switch(
                                "Show sample events bar",
                                value=bool(_pst_initial.get("show_mock_toolbar", True)),
                            )
                            ui.label("Preview-only — does not change saved template JSON.").classes(
                                "text-xs opacity-60 mb-2"
                            )

                            def save_preview_settings_dialog() -> None:
                                if save_template_preview_settings(
                                    {
                                        "enable_preview_sounds": bool(preview_sound_switch.value),
                                        "show_mock_toolbar": bool(preview_toolbar_switch.value),
                                    }
                                ):
                                    preview_settings_dialog.close()
                                    inst_l = getattr(web_engine_module, "web_engine_instance", None)
                                    if inst_l:
                                        inst_l.emit_preview_settings_to_iframe(CUSTOM_SOURCES_PREVIEW_TOKEN)
                                    mr = _custom_sources_ctx.get("mock_toolbar_row")
                                    if mr is not None:
                                        mr.visible = bool(preview_toolbar_switch.value)
                                    pmr = _custom_sources_ctx.get("popout_mock_toolbar_row")
                                    if pmr is not None:
                                        pmr.visible = bool(preview_toolbar_switch.value)
                                    notify("", type="success", source="source_settings", title="Preview settings saved")
                                else:
                                    notify(
                                        "",
                                        type="error",
                                        source="source_settings",
                                        title="Couldn't save preview settings",
                                        hint="Check that Mycelian's data folder is writable, then try again.",
                                    )

                            def open_preview_settings_dialog() -> None:
                                st = load_template_preview_settings()
                                preview_sound_switch.value = bool(st.get("enable_preview_sounds", True))
                                preview_toolbar_switch.value = bool(st.get("show_mock_toolbar", True))
                                preview_settings_dialog.open()

                            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                                ui.button("Cancel", on_click=preview_settings_dialog.close).props("flat dense")
                                ui.button("Save", on_click=save_preview_settings_dialog).props("dense")

                        with preview_panel:
                            with ui.element("div").classes("ssx-prev-bar"):
                                _Text("span", "Preview").classes("ssx-prev-title")
                                preview_live_chip = ui.html("", sanitize=False)
                                ui.element("span").classes("ssx-spacer")
                                _icon_button("refresh", "Reload preview", lambda: _flush_template_preview())
                                _icon_button("open_in_new", "Pop out the preview", open_popout_preview)
                                _icon_button("settings", "Preview settings", open_preview_settings_dialog)
                                _icon_button("visibility_off", "Hide the preview", lambda: show_inline_preview(False))
                            with ui.element("div").classes("ssx-prev-bar is-sub"):
                                with ui.element("div").classes("ssx-size"):
                                    ui.icon("aspect_ratio")
                                    preview_width_input = ui.number(
                                        value=1920, min=PREVIEW_WIDTH_MIN, max=PREVIEW_WIDTH_MAX, step=1, format="%.0f"
                                    ).props("dense outlined")
                                    preview_width_input.tooltip(
                                        "Preview width (px). Matches the OBS browser source when OBS is connected."
                                    )
                                    _Text("span", "×")
                                    preview_height_input = ui.number(
                                        value=1080, min=PREVIEW_HEIGHT_MIN, max=PREVIEW_HEIGHT_MAX, step=1, format="%.0f"
                                    ).props("dense outlined")
                                    preview_height_input.tooltip(
                                        "Preview height (px). Matches the OBS browser source when OBS is connected."
                                    )
                                preview_size_hint = ui.html("", sanitize=False)
                                preview_size_hint.set_visibility(False)
                                ui.element("span").classes("ssx-spacer")
                                with ui.element("div").classes("ssx-zoom"):
                                    zoom_out_btn = _Text("button", "−").props('type="button" aria-label="Zoom out"')
                                    _Text("span", "100%").props(f"id={zoom_label_id}")
                                    zoom_in_btn = _Text("button", "+").props('type="button" aria-label="Zoom in"')
                                    zoom_fit_btn = _Text("button", "Fit").props('type="button"')
                                zoom_out_btn.tooltip("Zoom out")
                                zoom_in_btn.tooltip("Zoom in")
                                zoom_fit_btn.tooltip("Fit the whole overlay in the preview")
                                _icon_button(
                                    "restart_alt",
                                    "Clear the saved size and detect it again (OBS or template defaults)",
                                    lambda: reset_preview_size(),
                                    small=True,
                                )
                            preview_outer = (
                                ui.element("div")
                                .props(
                                    f"id={preview_outer_id} data-mycelian-cs-preview-outer=true "
                                    f"data-zoom-label={zoom_label_id}"
                                )
                                .classes("ssx-stage")
                            )
                            with preview_outer:
                                preview_inner = (
                                    ui.element("div")
                                    .props(f"id={preview_inner_id}")
                                    .classes("absolute left-0 top-0 origin-top-left")
                                )
                                with preview_inner:
                                    # Hidden until it has a page (an empty iframe paints white).
                                    preview_iframe = ui.element("iframe").classes(
                                        "block border-0 bg-transparent pointer-events-none"
                                    ).style("visibility: hidden")
                                ui.element("div").classes("ssx-frame").props("data-cs-frame")
                                preview_placeholder = ui.label("Waiting for overlay server…").classes("ssx-stage-msg")
                            mock_toolbar_row = ui.element("div").classes("ssx-mocks")

        def step_zoom(direction: int) -> None:
            _custom_sources_ctx["preview_zoom_pct"] = next_preview_zoom(_current_preview_zoom_pct(), direction)
            _run_preview_zoom_js(False)

        def reset_preview_zoom() -> None:
            _custom_sources_ctx["preview_zoom_pct"] = 100
            _run_preview_zoom_js(True)

        def on_preview_size_change(_: Any = None) -> None:
            if (_custom_sources_ctx.get("_suppress_size_input") or [False])[0]:
                return
            sel = _custom_sources_ctx.get("config_select")
            if not sel or not sel.value:
                return
            try:
                w = int(float(preview_width_input.value))
                h = int(float(preview_height_input.value))
            except (TypeError, ValueError):
                return
            w = max(PREVIEW_WIDTH_MIN, min(w, PREVIEW_WIDTH_MAX))
            h = max(PREVIEW_HEIGHT_MIN, min(h, PREVIEW_HEIGHT_MAX))
            set_template_preview_resolution(sel.value, w, h)
            _apply_preview_size(sel.value, w, h, source="stored", resize=True)

        def reset_preview_size() -> None:
            sel = _custom_sources_ctx.get("config_select")
            if not sel or not sel.value:
                return
            clear_template_preview_resolution(sel.value)
            fd = form_data_store.get(sel.value, {})
            w, h = _resolve_preview_size_for_config(sel.value, fd, try_obs=True)
            _resize_preview_iframes(w, h)

        zoom_out_btn.on("click", lambda _e: step_zoom(-1))
        zoom_in_btn.on("click", lambda _e: step_zoom(1))
        zoom_fit_btn.on("click", lambda _e: reset_preview_zoom())
        preview_width_input.on_value_change(on_preview_size_change)
        preview_height_input.on_value_change(on_preview_size_change)

        popout_preview_iframe = None
        popout_preview_placeholder = None
        with ui.element("div").props(f"id={popout_dialog_id}").classes("mycelian-cs-popout-dialog"):
            with ui.element("div").props("data-popout-drag-handle").classes("mycelian-cs-popout-dialog__head"):
                ui.label("Live preview").classes("text-sm font-medium")
                with ui.row().classes("items-center gap-1"):
                    ui.button(
                        icon="refresh",
                        on_click=lambda: _flush_template_preview(),
                    ).props("flat dense round").tooltip("Reload preview")
                    ui.button(
                        icon="close",
                        on_click=lambda: _close_popout_preview_dialog(popout_dialog_id),
                    ).props("flat dense round").tooltip("Close")
            popout_mock_toolbar_row = ui.row().classes(
                "mycelian-cs-popout-dialog__mocktools w-full shrink-0 flex-nowrap gap-1 items-center"
            )
            with ui.element("div").classes("mycelian-cs-popout-dialog__body"):
                popout_preview_placeholder = ui.label("Waiting for overlay server…").classes(
                    "text-xs opacity-60 absolute top-2 left-2 z-10"
                )
                popout_preview_outer = (
                    ui.element("div")
                    .props(f"id={popout_outer_id} data-mycelian-cs-popout-preview=true")
                    .classes("mycelian-cs-popout-dialog__preview-outer")
                )
                with popout_preview_outer:
                    popout_preview_inner = (
                        ui.element("div")
                        .props(f"id={popout_inner_id}")
                        .classes("mycelian-cs-popout-dialog__preview-inner")
                    )
                    with popout_preview_inner:
                        popout_preview_iframe = ui.element("iframe").classes(
                            "block border-0 bg-transparent pointer-events-none"
                        )
            ui.element("div").props("data-popout-resize-handle").classes("mycelian-cs-popout-dialog__resize")

        _custom_sources_ctx.clear()
        _custom_sources_ctx.update(
            {
                "config_select": config_select,
                "config_container": config_container,
                "config_parser": config_parser,
                "meta_label": meta_label,
                "dirty_pill": dirty_pill,
                "discard_button": discard_button,
                "show_preview_button": show_preview_button,
                "nav_list": nav_list,
                "nav_id": nav_id,
                "form_id": form_id,
                "jump_select": jump_select,
                "preview_iframe": preview_iframe,
                "preview_placeholder": preview_placeholder,
                "preview_live_chip": preview_live_chip,
                "preview_outer_id": preview_outer_id,
                "preview_inner_id": preview_inner_id,
                "preview_zoom_pct": 100,
                "preview_width_input": preview_width_input,
                "preview_height_input": preview_height_input,
                "preview_size_hint": preview_size_hint,
                "_suppress_size_input": [False],
                "preview_split_row_id": preview_split_row_id,
                "preview_editor_panel_id": preview_editor_panel_id,
                "preview_panel_id": preview_panel_id,
                "preview_panel": preview_panel,
                "split_divider": split_divider,
                "split_pct": split_pct,
                "mock_toolbar_row": mock_toolbar_row,
                "popout_preview_iframe": popout_preview_iframe,
                "popout_preview_placeholder": popout_preview_placeholder,
                "popout_preview_outer_id": popout_outer_id,
                "popout_preview_inner_id": popout_inner_id,
                "popout_preview_dialog_id": popout_dialog_id,
                "popout_mock_toolbar_row": popout_mock_toolbar_row,
            }
        )
        # A rebuilt tab starts with an empty search box and "Changed only" off.
        _form_state.update(
            {"config": None, "sections": [], "rows": {}, "changed": set(), "nav_html": None, "search": "", "changed_only": False}
        )

        ui.on("mycelian-cs-split", _on_split_moved)
        _bind_split_divider_js()
        _apply_inline_preview_visibility(bool(_pst_initial.get("show_inline_preview", True)), persist=False)

        # Load the config files initially
        load_config_files(config_parser, config_select, config_container)


def _listed_config_names(config_parser) -> List[str]:
    """Templates shown in the Source Settings list."""
    return sorted(
        name
        for name in config_parser.get_non_hidden_config_files()
        if name not in _NO_SETTINGS_TEMPLATES
    )


def load_config_files(config_parser, config_select, config_container):
    """Load the template list; keep the open template selected when it still exists."""
    global _active_config_name, _suppress_config_change
    _invalidate_preview_route_cache()
    try:
        config_parser.invalidate_cache()
    except Exception:
        logger.debug("Template list cache refresh failed", exc_info=True)
    configs = _listed_config_names(config_parser)

    if configs:
        config_select.options = configs
        previous = _active_config_name
        target = previous if previous in configs else configs[0]
        if target == previous and not _config_is_dirty(target):
            # Nothing unsaved: read the file again so outside edits show up.
            _discard_config_edits(target)
            _disk_baselines.pop(target, None)

        _suppress_config_change = True
        try:
            config_select.value = target
        finally:
            _suppress_config_change = False
        _apply_config_selection(target, config_parser, config_container)
    else:
        config_select.options = []
        _suppress_config_change = True
        try:
            config_select.value = None
        finally:
            _suppress_config_change = False
        _active_config_name = None
        _show_no_configs(config_container)
        _flush_template_preview()


def _show_no_configs(config_container) -> None:
    _form_state.update({"config": None, "sections": [], "rows": {}, "changed": set()})
    config_container.clear()
    with config_container:
        with ui.element("div").classes("ssx-empty"):
            ui.icon("folder_off")
            _Text("div", "No templates yet").classes("ssx-empty-title")
            _Text("div", "Create one in Spore Studio, or add a configuration to templates/template_configs.").classes(
                "ssx-empty-sub"
            )
    _refresh_meta()
    _render_nav()
    _render_jump()
    _refresh_dirty_ui()


def _apply_config_selection(config_name, config_parser, config_container):
    """Render a template config and remember it as the active selection."""
    global _active_config_name
    if not config_name:
        return
    _active_config_name = config_name
    _invalidate_preview_route_cache(config_name)
    render_config_ui(config_parser, config_name, config_container)
    _flush_template_preview()


def _discard_config_edits(config_name: str) -> None:
    """Drop in-memory edits so the next render loads the saved file."""
    form_data_store.pop(config_name, None)


def _prompt_unsaved_config(
    previous_name: str,
    next_name: str,
    config_parser,
    config_select,
    config_container,
) -> None:
    """Ask to save, discard, or stay before leaving a dirty template."""
    with ui.dialog().props("persistent") as dialog, ui.card().classes("w-[460px] p-4"):
        ui.label("Unsaved changes").classes("text-lg font-bold mb-2")
        ui.label(
            f"You have unsaved changes to {previous_name}. Save them before switching?"
        ).classes("secondary-text mb-4")

        def stay() -> None:
            dialog.close()

        def discard_and_switch() -> None:
            _discard_config_edits(previous_name)
            dialog.close()
            _suppress_and_select(config_select, next_name)
            _apply_config_selection(next_name, config_parser, config_container)

        def save_and_switch() -> None:
            _suppress_and_select(config_select, previous_name)
            save_config(config_parser, config_select, config_container)
            if _config_is_dirty(previous_name):
                # save_config already said why; the dialog stays open with the edits.
                return
            dialog.close()
            _suppress_and_select(config_select, next_name)
            _apply_config_selection(next_name, config_parser, config_container)

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Stay", on_click=stay).props("outline")
            ui.button("Discard and switch", on_click=discard_and_switch).props(
                "outline"
            )
            ui.button("Save and switch", on_click=save_and_switch).props(
                "color=primary"
            )
        dialog.open()


def _suppress_and_select(config_select, config_name) -> None:
    global _suppress_config_change
    _suppress_config_change = True
    try:
        config_select.value = config_name
    finally:
        _suppress_config_change = False


def select_config(config_name: str) -> bool:
    """Select ``config_name`` in the open Source Settings tab (normal change handling applies).

    Returns False while the tab isn't built yet or the config doesn't exist.
    """
    sel = _custom_sources_ctx.get("config_select")
    if sel is None or getattr(sel, "is_deleted", False) or config_name not in (sel.options or []):
        return False
    if sel.value != config_name:
        sel.value = config_name
    return True


def on_config_selected(e, config_parser, config_container, config_select=None):
    """Handle config selection"""
    global _suppress_config_change
    if _suppress_config_change:
        return
    config_name = e.value
    if not config_name:
        return

    previous = _active_config_name
    if (
        previous
        and previous != config_name
        and _config_is_dirty(previous)
        and config_select is not None
    ):
        _suppress_and_select(config_select, previous)
        _prompt_unsaved_config(
            previous,
            config_name,
            config_parser,
            config_select,
            config_container,
        )
        return

    _apply_config_selection(config_name, config_parser, config_container)


def _build_form_data(config_name: str, config: dict) -> dict:
    """Build complete form data, preserving in-session edits across re-renders."""
    form_data = dict(form_data_store.get(config_name, {}))
    for element in config.get("elements", []):
        if not isinstance(element, dict) or element.get("type") == "separator":
            continue
        element_id = element.get("id", "")
        if element_id and element_id not in form_data:
            form_data[element_id] = element.get("value", "")
    return form_data


def render_config_ui(config_parser, config_name, container, search_term=None):
    """Render the template's settings as section cards (``search_term`` is ignored; the
    outline's search box filters the rendered rows)."""
    container.clear()
    config = config_parser.load_config(config_name)

    fresh_from_disk = config_name not in form_data_store
    form_data = _build_form_data(config_name, config)
    if fresh_from_disk and config_name not in _disk_baselines:
        _remember_disk_baseline(config_name, form_data)
    form_data_store[config_name] = form_data

    elements = [el for el in config.get("elements", []) if isinstance(el, dict)]
    sections = build_sections(config_name, elements, form_data)
    form_id = _custom_sources_ctx.get("form_id") or "ssx-form"
    handles: List[_SectionHandle] = []
    rows: Dict[str, _RowHandle] = {}
    _form_state.update(
        {"config": config_name, "sections": handles, "rows": rows, "changed": set(), "nav_html": None}
    )

    with container:
        for index, section in enumerate(sections):
            open_ = index < _SECTIONS_OPEN_BY_DEFAULT
            dom_id = f"{form_id}-{section.key}"
            root = (
                ui.element("div")
                .classes("ssx-sec" + ("" if open_ else " is-closed"))
                .props(f'id={dom_id} data-sec="{_esc(section.key)}"')
            )
            with root:
                head_html = _section_head_html(section.title, len(section.elements))
                head = ui.html(head_html, sanitize=False).classes("ssx-sec-head").props(
                    f'data-sec-head="{_esc(section.key)}"'
                )
                body = ui.element("div").classes("ssx-sec-body")
                section_rows: List[_RowHandle] = []
                with body:
                    for element in section.elements:
                        handle = render_form_element(element, form_data, config_name)
                        if handle is not None:
                            section_rows.append(handle)
                            rows[handle.element_id] = handle
            sec = _SectionHandle(
                section=section, root=root, head=head, rows=section_rows, dom_id=dom_id, is_open=open_, head_html=head_html
            )
            handles.append(sec)
        if not sections:
            with ui.element("div").classes("ssx-empty"):
                ui.icon("tune")
                _Text("div", "This template has no settings").classes("ssx-empty-title")
                _Text("div", "Add some in Spore Studio, or in the template's configuration file.").classes("ssx-empty-sub")
        empty = ui.element("div").classes("ssx-empty")
        with empty:
            ui.icon("search_off")
            empty_title = _Text("div", "No settings match").classes("ssx-empty-title")
            empty_sub = _Text("div", "").classes("ssx-empty-sub")
        empty.set_visibility(False)
        _form_state["empty"] = empty
        _form_state["empty_labels"] = (empty_title, empty_sub)

    _refresh_meta()
    _render_jump()
    _refresh_dirty_ui()
    if _filtering():
        _apply_filters()
    try:
        ui.run_javascript(f"var f=document.getElementById({json.dumps(form_id)}); if (f) {{ f.scrollTop = 0; }}")
    except Exception as e:
        logger.debug("Form scroll reset skipped: %s", e)
    _sync_nav_js()
    layout_schedule(0.1, lambda: _after_render(config_name), once=True)


def _after_render(config_name: str) -> None:
    _refresh_preview_dirty_label(config_name)
    _schedule_template_preview_refresh()


def handle_number_change(e, element_id, form_data):
    """Handle number input changes"""
    update_form_data(form_data, element_id, e.value)


def _asset_media_url(web_path: str) -> str:
    """URL NiceGUI serves an /assets/ image at (thumbnails work without the overlay server)."""
    try:
        from .file_picker import _media_url, assets_root, from_web_path

        root = assets_root()
        path = from_web_path(web_path, root)
        if path is None or not path.is_file():
            return ""
        return _media_url(path, root)
    except Exception:
        logger.debug("Asset thumbnail URL failed for %s", web_path, exc_info=True)
        return ""


def _asset_thumb_html(spec: AssetSpec, value: Any) -> str:
    lists = _asset_ext_lists()
    web = asset_value_to_web_path(spec, value)
    ext = _ext_of(web or str(value or ""))
    if spec.mode == "folder":
        return _icon_html("folder")
    if ext in lists["image"]:
        url = _asset_media_url(web) if web else ""
        if url:
            return f'<img src="{_esc(url)}" alt="">'
        return _icon_html("image")
    if ext in lists["audio"] or (not ext and spec.kind == "audio"):
        return _icon_html("music_note")
    if ext in lists["video"] or (not ext and spec.kind == "video"):
        return _icon_html("movie")
    if ext in lists["font"] or spec.kind == "font":
        return _icon_html("font_download")
    if not ext and spec.kind == "image":
        return _icon_html("image")
    return _icon_html("insert_drive_file")


def _attach_asset_browse(input_element, label: str, spec: AssetSpec) -> None:
    """Folder button after the field: opens the shared picker and writes the chosen path."""
    from pathlib import Path

    from .file_picker import assets_root, open_file_picker

    nouns = {"image": "an image", "audio": "a sound", "video": "a video", "media": "a file", "font": "a font file"}
    icons = {"image": "image", "audio": "music_note", "video": "movie", "font": "font_download"}

    def pick() -> None:
        def apply(result) -> None:
            web = result.web_dir if spec.mode == "folder" else result.web_path
            if web:
                input_element.value = asset_value_from_pick(spec, web)

        within = None
        within_hint = ""
        if spec.style == "filename" and spec.base_dir:
            folder = Path(assets_root()) / spec.base_dir[len("/assets/"):]
            within = folder
            within_hint = f"This setting reads files from {spec.base_dir}"
        noun = "a folder" if spec.mode == "folder" else nouns.get(spec.kind, "a file")
        start = asset_value_to_web_path(spec, input_element.value) or None
        open_file_picker(
            title=f"Choose {noun} for {label}" if label else f"Choose {noun}",
            subtitle=", ".join(e[1:].upper() for e in spec.extensions[:8]) + " from your assets folder",
            extensions=spec.extensions,
            on_pick=apply,
            mode=spec.mode,
            kind=spec.kind if spec.kind != "font" else "file",
            icon="folder" if spec.mode == "folder" else icons.get(spec.kind, ""),
            start=start,
            within=within,
            within_hint=within_hint,
        )

    btn = _icon_button("folder_open", "Browse the assets folder", pick)
    return btn


def _row_text_html(element_id: str, label: str, description: str) -> str:
    title = (
        f'<div class="ssx-row-title"><span class="ssx-row-label">{_esc(label)}</span>'
        '<span class="ssx-changed">Changed</span>'
        f'<button type="button" class="ssx-revert" data-revert="{_esc(element_id)}" '
        'title="Back to the saved value" aria-label="Back to the saved value">'
        f'{_icon_html("undo")}</button></div>'
    )
    if description:
        title += f'<div class="ssx-row-sub" title="{_esc(description)}">{_esc(description)}</div>'
    return title


def render_form_element(element, form_data, config_name="", container=None, search_term=""):
    """Render one setting as a row (label + description left, control right).

    Returns the row handle (used for "Changed", revert and filtering); ``container`` and
    ``search_term`` are accepted for older callers and ignored.
    """
    element_type = element.get("type", "text")
    element_id = element.get("id", "")
    element_label = element.get("label", element_id)
    element_description = element.get("description", "")

    # Prefer the in-session value (edits survive re-renders) over the config file value
    element_value = form_data.get(element_id, element.get("value", ""))
    form_data[element_id] = element_value

    def commit(value, id=element_id) -> None:
        update_form_data(form_data, id, value)

    stack = element_type == "textarea"
    row = ui.element("div").classes("ssx-row" + (" is-stack" if stack else ""))
    with row:
        ui.html(_row_text_html(element_id, element_label, element_description), sanitize=False).classes("ssx-row-text")
        if stack:
            control = form_textarea(
                tooltip="",
                value=element_value,
                classes="ssx-area",
                rows=4,
                on_change=lambda e: commit(e.value),
            )
            setter = _value_setter(control)
        else:
            with ui.element("div").classes("ssx-ctrl"):
                setter = _render_control(element, element_type, element_id, element_label, element_value, form_data, config_name, commit)
    return _RowHandle(element_id=element_id, element=element, row=row, set_value=setter)


def _value_setter(control: Any) -> Callable[[Any], None]:
    def set_value(value: Any) -> None:
        control.value = value

    return set_value


def _render_control(element, element_type, element_id, element_label, element_value, form_data, config_name, commit):
    """Build the control for one setting; returns a function that shows a value in it."""
    if element_type in ("text", "file"):
        spec = _asset_browse_spec(element, element_value, config_name)
        if spec is None:
            control = form_input(tooltip="", value=element_value, classes="ssx-text", on_change=lambda e: commit(e.value))
            return _value_setter(control)
        thumb = ui.html(_asset_thumb_html(spec, element_value), sanitize=False).classes("ssx-thumb")

        def on_asset_change(e) -> None:
            commit(e.value)
            thumb.set_content(_asset_thumb_html(spec, e.value))

        control = form_input(tooltip="", value=element_value, classes="ssx-text is-asset", on_change=on_asset_change)
        _attach_asset_browse(control, element_label, spec)
        return _value_setter(control)

    if element_type == "select":
        options: List[Any] = list(element.get("options", []))
        if element.get("options_from") == "fonts":
            options = _list_default_font_basenames()
            ev_str = str(element_value).strip() if element_value is not None else ""
            if ev_str and ev_str not in options:
                options = [ev_str] + options
            if not options and ev_str:
                options = [ev_str]
        if element.get("display", "dropdown") == "color_grid":
            return _render_color(element_id, element_value, list(element.get("options", [])) or None, commit)
        if element_value not in (None, "") and element_value not in options:
            options = [element_value] + options

        def shown(value: Any) -> Any:
            # An empty value that is not an option shows as no selection (ui.select rejects it)
            return None if value in (None, "") and value not in options else value

        control = form_select(
            tooltip="", options=options, value=shown(element_value), classes="ssx-select", on_change=lambda e: commit(e.value)
        )

        def set_select_value(value: Any) -> None:
            control.value = shown(value)

        return set_select_value

    if element_type == "number":
        control = form_number(
            tooltip="",
            value=element_value,
            min=element.get("min", None),
            max=element.get("max", None),
            step=number_step(element),
            classes="ssx-num",
            on_change=lambda e, id=element_id: handle_number_change(e, id, form_data),
        )
        unit = setting_unit(element)
        if unit:
            _Text("span", unit).classes("ssx-unit")
        return _value_setter(control)

    if element_type == "slider":
        min_val = element.get("min", 0)
        max_val = element.get("max", 100)
        step_val = element.get("step", 1)
        try:
            if step_val == int(step_val):
                slider_value = int(float(element_value)) if element_value != "" else min_val
            else:
                slider_value = float(element_value) if element_value != "" else min_val
        except (ValueError, TypeError):
            slider_value = min_val

        def slider_readout(value, step=step_val):
            try:
                number = float(value)
            except (TypeError, ValueError):
                return str(value)
            if step == int(step):
                return str(int(round(number)))
            return str(number)

        control = ui.slider(min=min_val, max=max_val, step=step_val, value=slider_value).classes("ssx-slider")
        value_label = _Text("span", slider_readout(slider_value)).classes("ssx-readout")

        def handle_slider_change(e, label=value_label):
            commit(e.value)
            label.set_text(slider_readout(e.value))

        control.on_value_change(handle_slider_change)
        return _value_setter(control)

    if element_type == "checkbox":
        control = ui.switch(value=element_value, on_change=lambda e: commit(e.value)).props("dense")
        return _value_setter(control)

    if element_type == "color":
        preset_options = element.get("options")
        return _render_color(element_id, element_value, list(preset_options) if preset_options else None, commit)

    control = ui.input(value=element_value, on_change=lambda e: commit(e.value)).classes("ssx-text").props("outlined dense")
    return _value_setter(control)


def _render_color(element_id: str, value: Any, preset_options: Optional[List[str]], commit: Callable[[Any], None]):
    """Shared colour field; ``on_input`` drives the live preview while dragging (newer picker)."""
    params = _color_field_params()
    kwargs: Dict[str, Any] = {
        "element_id": element_id,
        "value": value,
        "tooltip": "",
        "preset_options": preset_options,
        "on_change": lambda v: commit(v),
    }
    if "on_input" in params:
        kwargs["on_input"] = lambda v: commit(v)
    if "allow_gradient" in params and is_gradient_value(value):
        kwargs["allow_gradient"] = True
    box = ui.element("div").classes("ssx-color")
    with box:
        render_color_field(**kwargs)

    def set_value(new_value: Any) -> None:
        # Rebuild the field so its swatch and "last committed" value match the saved value.
        box.clear()
        with box:
            render_color_field(**{**kwargs, "value": new_value})

    return set_value


def update_roulette_expansion_title(option_num, option_name):
    """Show a renamed roulette option in its section heading and the outline."""
    for sec in _sections():
        if sec.section.option_num == option_num:
            sec.section.title = roulette_option_title(option_num, option_name)
            _update_section_head(sec)
            _render_nav()
            return


def update_form_data(form_data, element_id, value):
    """Update the form data when an element changes"""
    old_value = form_data.get(element_id, "NOT_SET")
    form_data[element_id] = value

    config_name = None
    for name, data in form_data_store.items():
        if data is form_data:
            config_name = name
            break

    if config_name:
        _refresh_preview_dirty_label(config_name)

    if config_name == "combobar" and element_id in [
        "Tier2EXP",
        "Tier3EXP",
        "TotalLevels",
        "ExpIncreasePerLevel",
    ]:
        logger.info(
            f"ComboBar config change: {element_id} changed from {old_value} to {value}"
        )

    logger.debug(f"Form data updated: {element_id} = {value}")

    # Keep roulette option headings in step with their names
    if config_name == ROULETTE_CONFIG_NAME and element_id.startswith("Option") and element_id.endswith("Name"):
        option_num = roulette_option_number(element_id)
        if option_num is not None:
            update_roulette_expansion_title(option_num, value)

    _schedule_template_preview_refresh()


def _notify_no_template() -> None:
    notify(
        "",
        type="warning",
        source="source_settings",
        title="Choose a template first",
        hint="Pick a template in the list at the top of Source Settings.",
    )


def _is_technical_error(error: BaseException) -> bool:
    """SporeStudioError text is plain for name checks, but wraps OS errors otherwise."""
    return error.__cause__ is not None or "Errno" in str(error) or "\\" in str(error)


def save_config(config_parser, config_select, config_container):
    """Save the current config"""
    config_name = config_select.value
    if not config_name:
        _notify_no_template()
        return

    try:
        # Get the form data from the global store
        form_data = form_data_store.get(config_name, {})

        # Load the original config to get the structure (including dynamic_controls)
        original_config = config_parser.load_config(
            config_name, include_dynamic_controls=True
        )

        # Log the current form data for debugging
        logger.debug(
            f"Saving config for {config_name}, form_data keys: {list(form_data.keys())}"
        )

        # Collect all element IDs to verify we have form data for all of them
        all_element_ids = []
        missing_element_ids = []

        # Update the values in the original config
        for element in original_config.get("elements", []):
            element_id = element.get("id", "")
            element_type = element.get("type", "")

            # Skip separator elements as they don't have values
            if element_type == "separator":
                continue

            all_element_ids.append(element_id)

            if element_id in form_data:
                new_value = form_data[element_id]
                element_type = element.get("type", "")

                # Ensure numeric types are preserved correctly for critical combobar fields
                if (
                    config_name == "combobar"
                    and element_id
                    in [
                        "Tier2EXP",
                        "Tier3EXP",
                        "TotalLevels",
                        "ExpIncreasePerLevel",
                        "DefaultGoalXP",
                    ]
                    and element_type == "number"
                ):
                    try:
                        # Ensure the value is properly numeric
                        if isinstance(new_value, str):
                            # Try to convert string to appropriate numeric type
                            if "." in new_value:
                                new_value = float(new_value)
                            else:
                                new_value = int(new_value)
                        logger.debug(
                            f"Preserved numeric type for {element_id}: {new_value} (type: {type(new_value).__name__})"
                        )
                    except (ValueError, TypeError):
                        logger.warning(
                            f"Failed to convert {element_id} value '{new_value}' to number, keeping original: {element.get('value')}"
                        )
                        continue  # Skip updating this element to preserve original value

                element["value"] = new_value

                # Update transparency property for color elements
                if element.get("type") == "color":
                    color_value = form_data[element_id]
                    element["transparent"] = is_color_transparent(color_value)

                # Legacy: select elements with color_grid display
                if (
                    element.get("type") == "select"
                    and element.get("display") == "color_grid"
                ):
                    color_value = form_data[element_id]
                    element["transparent"] = is_color_transparent(color_value)
            else:
                # Element is missing from form data - this is a potential issue
                missing_element_ids.append(element_id)
                logger.warning(
                    f"Element {element_id} not found in form data for {config_name}, keeping original value: {element.get('value')}"
                )

        if missing_element_ids:
            logger.warning(
                f"Config save for {config_name}: {len(missing_element_ids)} elements missing from form data: {missing_element_ids}"
            )
        labels_by_id = {
            el.get("id"): str(el.get("label") or el.get("id"))
            for el in original_config.get("elements", [])
            if isinstance(el, dict)
        }
        missing_labels = [labels_by_id.get(i, i) for i in missing_element_ids]

        # Save the config
        if config_parser.save_config(config_name, original_config):
            if missing_labels:
                # One notice for this save: saved, but some fields kept their old value.
                shown = ", ".join(missing_labels[:3]) + ("…" if len(missing_labels) > 3 else "")
                notify(
                    f"These fields kept their previous value: {shown}.",
                    type="warning",
                    source="source_settings",
                    title=f"Saved {config_name}, but some fields weren't updated",
                    hint="Reopen the template, change those fields again, and click Save.",
                    details=", ".join(missing_element_ids),
                    actions=nav_source_template(config_name),
                )
            else:
                notify("", type="success", source="source_settings", title=f"Saved {config_name}")
            if config_name in ("ff7", "factorio"):
                try:
                    from ..game_hooks_service import game_hooks_service

                    game_hooks_service.reload_hook_config(config_name)
                except Exception as e:
                    logger.warning(
                        "Game hook config refresh after save failed (%s): %s",
                        config_name,
                        e,
                        exc_info=True,
                    )
                    notify(
                        "The running game hook is still using the old values.",
                        type="warning",
                        source="game_hooks",
                        title=f"Saved {config_name}, but the game hook didn't pick it up",
                        hint="Turn the hook off and on again in Settings → Game Hooks.",
                        details=f"{type(e).__name__}: {e}",
                        actions=nav_actions_settings("Game Hooks"),
                    )
            _remember_disk_baseline(
                config_name, form_data_store.get(config_name, {})
            )
            _refresh_preview_dirty_label(config_name)
            _invalidate_preview_route_cache(config_name)
            _flush_template_preview()
        else:
            notify(
                "Your changes are still in the form.",
                type="error",
                source="source_settings",
                title=f"Couldn't save {config_name}",
                hint="Check that the templates folder is writable and the file isn't open in another program, then try again.",
                details=getattr(config_parser, "last_error", "") or None,
                actions=nav_source_template(config_name),
            )
    except Exception as e:
        logger.error(
            f"Error saving configuration for {config_name}: {str(e)}", exc_info=True
        )
        notify(
            "Your changes are still in the form.",
            type="error",
            source="source_settings",
            title=f"Couldn't save {config_name}",
            hint="Try again. If it keeps failing, copy the details and check the log.",
            details=f"{type(e).__name__}: {e}",
            actions=nav_source_template(config_name),
        )


def reset_config(config_parser, config_select, config_container):
    """Reset the current config to the saved version"""
    config_name = config_select.value
    if not config_name:
        _notify_no_template()
        return

    if not _config_is_dirty(config_name):
        _perform_config_reset(config_parser, config_name, config_container)
        return

    with ui.dialog().props("persistent") as dialog, ui.card().classes("w-[460px] p-4"):
        ui.label("Discard unsaved changes?").classes("text-lg font-bold mb-2")
        ui.label(
            f"Go back to the last saved version of {config_name}? Your unsaved edits will be lost."
        ).classes("secondary-text mb-4")

        def confirm_reset() -> None:
            dialog.close()
            _perform_config_reset(config_parser, config_name, config_container)

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Stay", on_click=dialog.close).props("outline")
            ui.button("Discard changes", on_click=confirm_reset).props("color=primary")
        dialog.open()


def _perform_config_reset(config_parser, config_name, config_container) -> None:
    """Reload a template from disk, dropping in-memory edits."""
    _invalidate_preview_route_cache(config_name)

    # Discard in-memory edits (and the old baseline) so the re-render reads the file
    _discard_config_edits(config_name)
    _disk_baselines.pop(config_name, None)

    render_config_ui(config_parser, config_name, config_container)
    _flush_template_preview()
    notify("", type="success", source="source_settings", title=f"Reverted {config_name} to the last saved version")


def _spore_sidecar_file(template_name: str) -> str:
    return get_template_path(os.path.join("_spore", f"{template_name}.spore.json"))


def _template_assets_dir(template_name: str) -> str:
    """Writable asset folder for a template (same root Spore Studio uses)."""
    return os.path.join(get_assets_path(), template_name)


def _template_name_is_taken(template_name: str, config_parser) -> bool:
    if os.path.isfile(get_template_path(f"{template_name}.html")):
        return True
    try:
        json_path = config_parser.get_config_path(template_name)
    except ValueError:
        return True
    if os.path.isfile(json_path):
        return True
    if os.path.isfile(_spore_sidecar_file(template_name)):
        return True
    return os.path.isdir(_template_assets_dir(template_name))


def _refresh_template_copy_footer() -> None:
    try:
        from ..notification_engine import refresh_template_copy_footer

        refresh_template_copy_footer()
    except Exception:
        logger.debug("template copy footer refresh failed", exc_info=True)


def _invalidate_webengine_template_cache() -> None:
    try:
        inst = getattr(web_engine_module, "web_engine_instance", None)
        if inst is not None and hasattr(inst, "invalidate_all_template_configs_cache"):
            inst.invalidate_all_template_configs_cache()
    except Exception:
        logger.debug("template config cache refresh failed", exc_info=True)


def _list_asset_files(root: str) -> List[str]:
    if not os.path.isdir(root):
        return []
    found: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            found.append(os.path.join(dirpath, filename))
    found.sort()
    return found


def _mirror_asset_dirs(src: str, dst: str) -> None:
    for dirpath, _dirnames, _filenames in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        target = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target, exist_ok=True)


def _cleanup_partial_copy(
    written: List[str], dst_assets: str, made_assets: bool
) -> None:
    for path in written:
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
    if made_assets and os.path.isdir(dst_assets):
        shutil.rmtree(dst_assets, ignore_errors=True)


def _copy_template_files(
    source_name: str,
    new_name: str,
    config_parser,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
) -> None:
    """Copy HTML, JSON, the Spore sidecar, and the asset folder."""
    from ..spore_studio import template_parser_back
    from ..spore_studio.save_pipeline import SporeStudioError

    def report(phase: str, copied: int, total: int) -> None:
        if on_progress is not None:
            on_progress(phase, copied, total)

    src_json = config_parser.get_config_path(source_name)
    if not os.path.isfile(src_json):
        raise SporeStudioError(f"No configuration file for '{source_name}'.")
    try:
        with open(src_json, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise SporeStudioError(f"Could not read '{source_name}': {e}") from e
    if not isinstance(config, dict):
        raise SporeStudioError(f"Configuration for '{source_name}' is not an object.")

    html_text = None
    src_html = get_template_path(f"{source_name}.html")
    if os.path.isfile(src_html):
        try:
            with open(src_html, "r", encoding="utf-8") as fh:
                html_text = fh.read()
        except OSError as e:
            raise SporeStudioError(f"Could not read '{source_name}.html': {e}") from e

    sidecar = template_parser_back.load_sidecar(source_name)
    config = _rewrite_template_tree(config, source_name, new_name)
    config["template_name"] = new_name
    if isinstance(sidecar, dict):
        sidecar = _rewrite_template_tree(sidecar, source_name, new_name)
        sidecar["template_name"] = new_name
    if html_text is not None:
        html_text = _rewrite_template_html(html_text, source_name, new_name)

    src_assets = _template_assets_dir(source_name)
    dst_assets = _template_assets_dir(new_name)
    asset_files = _list_asset_files(src_assets)
    report("preparing", 0, 0)

    steps: List[Tuple[str, str]] = [
        (
            config_parser.get_config_path(new_name),
            json.dumps(config, indent=4, ensure_ascii=False),
        )
    ]
    dst_html = get_template_path(f"{new_name}.html")
    if html_text is not None:
        steps.append((dst_html, html_text))
    if isinstance(sidecar, dict):
        steps.append(
            (
                _spore_sidecar_file(new_name),
                json.dumps(sidecar, indent=2, ensure_ascii=False),
            )
        )

    total = len(steps) + len(asset_files)
    copied = 0
    written: List[str] = []
    made_assets = False
    report("copying", copied, total)
    try:
        for path, text in steps:
            _write_rename_text(path, text)
            written.append(path)
            copied += 1
            report("copying", copied, total)
        if os.path.isdir(src_assets):
            _mirror_asset_dirs(src_assets, dst_assets)
            made_assets = True
            for src_file in asset_files:
                rel = os.path.relpath(src_file, src_assets)
                dest = os.path.join(dst_assets, rel)
                parent = os.path.dirname(dest)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                shutil.copy2(src_file, dest)
                copied += 1
                report("copying", copied, total)
    except Exception as e:
        _cleanup_partial_copy(written, dst_assets, made_assets)
        raise SporeStudioError(f"Could not copy '{source_name}': {e}") from e


def _same_existing_file(src: str, dst: str) -> bool:
    try:
        return (
            os.path.exists(src)
            and os.path.exists(dst)
            and os.path.samefile(src, dst)
        )
    except OSError:
        return False


def _path_blocks_rename(src: str, dst: str) -> bool:
    """True when dst exists and is not the source path (case-only renames are ok)."""
    if not os.path.exists(dst):
        return False
    return not _same_existing_file(src, dst)


def _rewrite_template_tree(value: Any, old_name: str, new_name: str) -> Any:
    old_asset = f"/assets/{old_name}/"
    new_asset = f"/assets/{new_name}/"
    old_counters = f"{old_name}/counters"
    new_counters = f"{new_name}/counters"
    if isinstance(value, dict):
        return {
            key: _rewrite_template_tree(item, old_name, new_name)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_rewrite_template_tree(item, old_name, new_name) for item in value]
    if isinstance(value, str):
        return value.replace(old_asset, new_asset).replace(old_counters, new_counters)
    return value


def _rewrite_template_html(text: str, old_name: str, new_name: str) -> str:
    text = text.replace(f"/assets/{old_name}/", f"/assets/{new_name}/")
    text = text.replace(f"{old_name}/counters", f"{new_name}/counters")
    old_assign = f'window.__sporeTemplateName = "{old_name}"'
    new_assign = f'window.__sporeTemplateName = "{new_name}"'
    return text.replace(old_assign, new_assign)


def _write_rename_text(path: str, text: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = path + ".rename-tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def _publish_renamed_file(src: str, dst: str, text: str, created: List[str]) -> None:
    _write_rename_text(dst, text)
    if not _same_existing_file(src, dst):
        created.append(dst)


def _rename_template_files(source_name: str, raw_name: str, config_parser) -> str:
    """Rename HTML, JSON config, and the Spore sidecar when one exists."""
    from ..spore_studio import template_parser_back
    from ..spore_studio.save_pipeline import (
        SporeStudioError,
        _refresh_web_engine_routes,
        _validate_name,
    )

    new_name = _validate_name(raw_name or "")
    if new_name == source_name:
        raise SporeStudioError("Choose a different name.")

    src_json = config_parser.get_config_path(source_name)
    dst_json = config_parser.get_config_path(new_name)
    src_html = get_template_path(f"{source_name}.html")
    dst_html = get_template_path(f"{new_name}.html")
    src_sidecar = _spore_sidecar_file(source_name)
    dst_sidecar = _spore_sidecar_file(new_name)
    src_assets = _template_assets_dir(source_name)
    dst_assets = _template_assets_dir(new_name)

    if not os.path.isfile(src_json):
        raise SporeStudioError(f"No configuration file for '{source_name}'.")
    if any(
        _path_blocks_rename(src, dst)
        for src, dst in (
            (src_html, dst_html),
            (src_json, dst_json),
            (src_sidecar, dst_sidecar),
            (src_assets, dst_assets),
        )
    ):
        raise SporeStudioError(f"A template named '{new_name}' already exists.")

    try:
        with open(src_json, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, json.JSONDecodeError) as e:
        raise SporeStudioError(f"Could not read '{source_name}': {e}") from e
    if not isinstance(config, dict):
        raise SporeStudioError(f"Configuration for '{source_name}' is not an object.")

    html_text = None
    if os.path.isfile(src_html):
        try:
            with open(src_html, "r", encoding="utf-8") as fh:
                html_text = fh.read()
        except OSError as e:
            raise SporeStudioError(f"Could not read '{source_name}.html': {e}") from e

    sidecar = template_parser_back.load_sidecar(source_name)
    src_sidecar = _spore_sidecar_file(source_name)

    config = _rewrite_template_tree(config, source_name, new_name)
    config["template_name"] = new_name
    if isinstance(sidecar, dict):
        sidecar = _rewrite_template_tree(sidecar, source_name, new_name)
        sidecar["template_name"] = new_name
    if html_text is not None:
        html_text = _rewrite_template_html(html_text, source_name, new_name)

    created: List[str] = []
    moved_assets = False
    try:
        if os.path.isdir(src_assets) and not _same_existing_file(
            src_assets, dst_assets
        ):
            os.makedirs(os.path.dirname(dst_assets), exist_ok=True)
            shutil.move(src_assets, dst_assets)
            moved_assets = True
        _publish_renamed_file(
            src_json,
            dst_json,
            json.dumps(config, indent=4, ensure_ascii=False),
            created,
        )
        if html_text is not None:
            _publish_renamed_file(src_html, dst_html, html_text, created)
        if isinstance(sidecar, dict):
            _publish_renamed_file(
                src_sidecar,
                dst_sidecar,
                json.dumps(sidecar, indent=2, ensure_ascii=False),
                created,
            )
    except Exception as e:
        if (
            moved_assets
            and os.path.isdir(dst_assets)
            and not os.path.isdir(src_assets)
        ):
            try:
                shutil.move(dst_assets, src_assets)
            except OSError:
                logger.warning(
                    "Could not restore assets for %s after a failed rename",
                    source_name,
                    exc_info=True,
                )
        for path in created:
            if os.path.isfile(path) and not _same_existing_file(path, src_json):
                try:
                    os.remove(path)
                except OSError:
                    pass
        raise SporeStudioError(f"Could not rename '{source_name}': {e}") from e

    leftovers: List[str] = []
    for src, dst in (
        (src_json, dst_json),
        (src_html, dst_html),
        (src_sidecar, dst_sidecar),
    ):
        if os.path.isfile(src) and not _same_existing_file(src, dst):
            try:
                os.remove(src)
            except OSError as exc:
                logger.warning(
                    "Could not remove old template file %s", src, exc_info=True
                )
                leftovers.append(f"{src}: {exc}")
    if leftovers:
        notify(
            f"The old files for {source_name} couldn't be removed, so a copy may still show up.",
            type="warning",
            source="source_settings",
            title=f"Renamed to {new_name}, but old files remain",
            hint="Close any program using those files, then delete them from the templates folder.",
            details="\n".join(leftovers),
        )

    config_parser.invalidate_cache()
    _invalidate_preview_route_cache(source_name)
    _invalidate_preview_route_cache(new_name)
    _refresh_web_engine_routes()
    _invalidate_webengine_template_cache()
    return new_name


def _show_duplicated_config(
    name, config_parser, config_select, config_container, verb: str = "Created"
) -> None:
    configs = _listed_config_names(config_parser)
    config_select.options = configs
    form_data_store.pop(name, None)
    _disk_baselines.pop(name, None)
    if name not in configs:
        notify(
            "Its configuration is marked as hidden, so it doesn't appear in the template list.",
            type="warning",
            source="source_settings",
            title=f"{verb} {name}, but it's hidden",
            hint="To show it here, remove \"hidden\": true from its configuration file.",
            actions=nav_spore_template(name),
        )
        return
    _suppress_and_select(config_select, name)
    _apply_config_selection(name, config_parser, config_container)


def duplicate_config(config_parser, config_select, config_container):
    """Ask for a name, then copy the selected template's files."""
    config_name = config_select.value
    if not config_name:
        _notify_no_template()
        return
    if _config_is_dirty(config_name):
        _notify_save_first("duplicating")
        return

    with ui.dialog() as dialog, ui.card():
        ui.label("Duplicate Template").classes("text-lg font-medium mb-4")
        ui.label(
            "Copies the HTML file, configuration, Spore Studio file, and asset folder."
        ).classes("text-sm opacity-75 mb-2")
        ui.label(f"Copy of {config_name}").classes("text-sm opacity-75 mb-2")
        name_input = form_input(
            tooltip="Name for the duplicated template",
            label="Template Name",
            value=f"{config_name}-copy",
        )

        def confirm_duplicate() -> None:
            duplicate_config_action(
                config_name,
                name_input.value,
                config_parser,
                config_select,
                config_container,
                dialog,
            )

        with ui.row().classes("w-full justify-end gap-2 mt-4"):
            ui.button("Cancel", on_click=dialog.close).classes("control-button")
            ui.button("Duplicate", on_click=confirm_duplicate).classes(
                "control-button"
            )

    dialog.open()


def _notify_save_first(action: str) -> None:
    notify(
        "",
        type="warning",
        source="source_settings",
        title=f"Save this template before {action}",
        hint="Click Save (or Discard to drop your edits), then try again.",
    )


def _notify_copy_busy() -> None:
    notify(
        "",
        type="warning",
        source="source_settings",
        title="A template copy or delete is already running",
        hint="Wait for it to finish (see the progress in the footer), then try again.",
    )


def _finish_template_copy(
    error: Optional[BaseException],
    source_name: str,
    new_name: str,
    config_parser,
    config_select,
    config_container,
) -> None:
    """UI-thread completion for a background template copy."""
    from ..spore_studio.save_pipeline import (
        SporeStudioError,
        _refresh_web_engine_routes,
    )

    _mark_template_copy_idle()
    _refresh_template_copy_footer()
    if error is not None:
        if not isinstance(error, SporeStudioError):
            logger.error(
                "Failed to duplicate template %s", source_name, exc_info=error
            )
        if isinstance(error, SporeStudioError) and not _is_technical_error(error):
            notify(
                str(error),
                type="warning",
                source="source_settings",
                title=f"Couldn't copy {source_name}",
                hint="Fix the problem above, then try Duplicate again.",
            )
        else:
            notify(
                "",
                type="error",
                source="source_settings",
                title=f"Couldn't copy {source_name}",
                hint="Check free disk space and that no other program is using the template's files, then try again.",
                details=f"{type(error).__name__}: {error}",
                actions=nav_source_template(source_name),
            )
        return

    config_parser.invalidate_cache()
    _invalidate_preview_route_cache(source_name)
    _invalidate_preview_route_cache(new_name)
    _refresh_web_engine_routes()
    _invalidate_webengine_template_cache()
    notify("", type="success", source="source_settings", title=f"Duplicated {source_name} as {new_name}")
    try:
        _show_duplicated_config(
            new_name, config_parser, config_select, config_container
        )
    except Exception as e:
        logger.error(
            "Copied %s but could not select it", new_name, exc_info=True
        )
        notify(
            "The copy is saved, but the template list didn't refresh.",
            type="warning",
            source="source_settings",
            title=f"Couldn't open {new_name}",
            hint="Click the button below, or switch tabs and come back to Source Settings.",
            details=f"{type(e).__name__}: {e}",
            actions=nav_source_template(new_name),
        )


def duplicate_config_action(
    source_name, raw_name, config_parser, config_select, config_container, dialog
):
    """Validate the new name, then copy the template on a background thread."""
    from ..spore_studio.save_pipeline import SporeStudioError, _validate_name

    if _config_is_dirty(source_name):
        _notify_save_first("duplicating")
        return
    if template_copy_is_active():
        _notify_copy_busy()
        return

    try:
        new_name = _validate_name(raw_name or "")
        if new_name == source_name:
            raise SporeStudioError("Choose a different name for the copy.")
        if _template_name_is_taken(new_name, config_parser):
            raise SporeStudioError(f"A template named '{new_name}' already exists.")
        if not os.path.isfile(config_parser.get_config_path(source_name)):
            raise SporeStudioError(f"No configuration file for '{source_name}'.")
    except SporeStudioError as e:
        notify(
            str(e),
            type="warning",
            source="source_settings",
            title="Choose another name",
            hint="Type a different name for the copy, then click Duplicate.",
        )
        return

    dialog.close()
    _set_template_copy_state(
        active=True,
        phase="preparing",
        copied=0,
        total=0,
        label=f"Copying {source_name} to {new_name}",
        title="Copying",
    )
    _refresh_template_copy_footer()

    def report(phase: str, copied: int, total: int) -> None:
        _set_template_copy_state(
            active=True,
            phase=phase,
            copied=copied,
            total=total,
            label=f"Copying {source_name} to {new_name}",
            title="Copying",
        )

    def work() -> None:
        error: Optional[BaseException] = None
        try:
            _copy_template_files(source_name, new_name, config_parser, report)
        except Exception as exc:
            error = exc
        try:
            run_on_ui_loop(
                lambda err=error: _finish_template_copy(
                    err,
                    source_name,
                    new_name,
                    config_parser,
                    config_select,
                    config_container,
                )
            )
        except Exception:
            _mark_template_copy_idle()
            logger.exception("Could not return template copy result to the UI")
            _notify_result_lost("copy")

    threading.Thread(target=work, name="TemplateDuplicate", daemon=True).start()


def _notify_result_lost(action: str) -> None:
    notify(
        "",
        type="warning",
        source="source_settings",
        title=f"The template {action} finished, but the screen didn't update",
        hint="Switch to another tab and back to Source Settings to see the result.",
    )


def rename_config(config_parser, config_select, config_container):
    """Ask for a new name, then rename the selected template's files."""
    config_name = config_select.value
    if not config_name:
        _notify_no_template()
        return
    if _config_is_dirty(config_name):
        _notify_save_first("renaming")
        return

    with ui.dialog() as dialog, ui.card():
        ui.label("Rename Template").classes("text-lg font-medium mb-4")
        ui.label(
            "Renames the HTML file, configuration, and Spore Studio file."
        ).classes("text-sm opacity-75 mb-2")
        name_input = form_input(
            tooltip="New name for this template",
            label="Template Name",
            value=config_name,
        )

        def confirm_rename() -> None:
            rename_config_action(
                config_name,
                name_input.value,
                config_parser,
                config_select,
                config_container,
                dialog,
            )

        with ui.row().classes("w-full justify-end gap-2 mt-4"):
            ui.button("Cancel", on_click=dialog.close).classes("control-button")
            ui.button("Rename", on_click=confirm_rename).classes("control-button")

    dialog.open()


def rename_config_action(
    source_name, raw_name, config_parser, config_select, config_container, dialog
):
    """Rename the selected template and select the new name."""
    global _active_config_name
    from ..spore_studio.save_pipeline import SporeStudioError

    if _config_is_dirty(source_name):
        _notify_save_first("renaming")
        return

    try:
        new_name = _rename_template_files(source_name, raw_name, config_parser)
    except SporeStudioError as e:
        if not _is_technical_error(e):
            notify(
                str(e),
                type="warning",
                source="source_settings",
                title="Choose another name",
                hint="Type a different name, then click Rename.",
            )
            return
        _notify_rename_failed(source_name, e)
        return
    except Exception as e:
        logger.error("Failed to rename template %s", source_name, exc_info=True)
        _notify_rename_failed(source_name, e)
        return

    form_data_store.pop(source_name, None)
    _disk_baselines.pop(source_name, None)
    if _active_config_name == source_name:
        _active_config_name = None

    notify("", type="success", source="source_settings", title=f"Renamed {source_name} to {new_name}")
    dialog.close()
    _show_duplicated_config(
        new_name, config_parser, config_select, config_container, verb="Renamed"
    )


def _notify_rename_failed(source_name: str, error: BaseException) -> None:
    notify(
        "",
        type="error",
        source="source_settings",
        title=f"Couldn't rename {source_name}",
        hint="Close any program using the template's files, then try again.",
        details=f"{type(error).__name__}: {error}",
        actions=nav_source_template(source_name),
    )


def _legacy_sidecar_file(template_name: str) -> str:
    return get_template_path(
        os.path.join("template_configs", f"{template_name}.spore.json")
    )


def _delete_template_files(
    config_name: str,
    config_parser,
    on_progress: Optional[Callable[[str, int, int], None]] = None,
) -> None:
    """Delete HTML, JSON, the Spore sidecar, and the asset folder."""
    from ..spore_studio.save_pipeline import SporeStudioError

    def report(phase: str, done: int, total: int) -> None:
        if on_progress is not None:
            on_progress(phase, done, total)

    assets = _template_assets_dir(config_name)
    asset_files = _list_asset_files(assets)
    singles = [
        path
        for path in (
            config_parser.get_config_path(config_name),
            get_template_path(f"{config_name}.html"),
            _spore_sidecar_file(config_name),
            _legacy_sidecar_file(config_name),
        )
        if os.path.isfile(path)
    ]
    folder_step = 1 if os.path.isdir(assets) else 0
    total = len(asset_files) + len(singles) + folder_step
    done = 0
    report("preparing", 0, total)
    report("deleting", 0, total)
    errors: List[str] = []

    def remove_file(path: str) -> None:
        try:
            if os.path.isfile(path) or os.path.islink(path):
                os.remove(path)
        except OSError as exc:
            errors.append(f"{path}: {exc}")

    for path in asset_files:
        remove_file(path)
        done += 1
        report("deleting", done, total)
    if folder_step:
        try:
            if os.path.isdir(assets):
                shutil.rmtree(assets)
        except OSError as exc:
            errors.append(f"{assets}: {exc}")
        done += 1
        report("deleting", done, total)
    for path in singles:
        remove_file(path)
        done += 1
        report("deleting", done, total)
    if errors:
        raise SporeStudioError(
            f"Could not fully delete '{config_name}': {errors[0]}"
        )


def delete_config(config_parser, config_select, config_container):
    """Delete the current config"""
    config_name = config_select.value
    if not config_name:
        _notify_no_template()
        return

    # Create a confirmation dialog
    with ui.dialog() as dialog, ui.card():
        ui.label("Confirm Deletion").classes("text-lg font-medium mb-4")
        ui.label(
            f"Delete {config_name}? This removes its configuration, HTML file, "
            "Spore Studio file, and asset folder when those exist."
        )

        with ui.row().classes("w-full justify-end gap-2 mt-4"):
            ui.button("Cancel", on_click=dialog.close).classes("control-button")
            ui.button(
                "Delete",
                on_click=lambda: delete_config_action(
                    config_name, config_parser, config_select, config_container, dialog
                ),
            ).classes("control-button")

    dialog.open()


def _refresh_config_dropdown(
    config_parser, config_select, config_container, *, skip: Optional[str] = None
) -> None:
    """Reload the template list from disk without discarding a different selection."""
    global _active_config_name
    config_parser.invalidate_cache()
    configs = _listed_config_names(config_parser)
    if skip:
        configs = [name for name in configs if name != skip]
    config_select.options = configs
    current = config_select.value
    if current in configs:
        return
    if configs:
        _suppress_and_select(config_select, configs[0])
        _apply_config_selection(configs[0], config_parser, config_container)
        return
    _active_config_name = None
    _suppress_and_select(config_select, None)
    _show_no_configs(config_container)
    _flush_template_preview()


def _finish_template_delete(
    error: Optional[BaseException],
    config_name: str,
    config_parser,
    config_select,
    config_container,
) -> None:
    """UI-thread completion for a background template delete."""
    from ..spore_studio.save_pipeline import (
        SporeStudioError,
        _refresh_web_engine_routes,
    )

    _mark_template_copy_idle()
    _refresh_template_copy_footer()
    _invalidate_preview_route_cache(config_name)
    _refresh_web_engine_routes()
    _invalidate_webengine_template_cache()
    try:
        _refresh_config_dropdown(config_parser, config_select, config_container)
    except Exception:
        logger.error(
            "Deleted %s but could not refresh the list",
            config_name,
            exc_info=True,
        )
    if error is None:
        notify("", type="success", source="source_settings", title=f"Deleted {config_name}")
        return
    if not isinstance(error, SporeStudioError):
        logger.error("Failed to delete template %s", config_name, exc_info=error)
    notify(
        "Some of its files may remain.",
        type="error",
        source="source_settings",
        title=f"Couldn't fully delete {config_name}",
        hint="Close any program using the template's files, then delete it again.",
        details=f"{type(error).__name__}: {error}",
        actions=nav_source_template(config_name),
    )


def delete_config_action(
    config_name, config_parser, config_select, config_container, dialog
):
    """Delete the template files on a background thread, including its assets."""
    global _active_config_name
    if template_copy_is_active():
        _notify_copy_busy()
        return

    dialog.close()
    form_data_store.pop(config_name, None)
    _disk_baselines.pop(config_name, None)
    if _active_config_name == config_name:
        _active_config_name = None
    _refresh_config_dropdown(
        config_parser, config_select, config_container, skip=config_name
    )

    label = f"Deleting {config_name}"
    _set_template_copy_state(
        active=True,
        phase="preparing",
        copied=0,
        total=0,
        label=label,
        title="Deleting",
    )
    _refresh_template_copy_footer()

    def report(phase: str, done: int, total: int) -> None:
        _set_template_copy_state(
            active=True,
            phase=phase,
            copied=done,
            total=total,
            label=label,
            title="Deleting",
        )

    def work() -> None:
        error: Optional[BaseException] = None
        try:
            _delete_template_files(config_name, config_parser, report)
        except Exception as exc:
            error = exc
        try:
            run_on_ui_loop(
                lambda err=error: _finish_template_delete(
                    err,
                    config_name,
                    config_parser,
                    config_select,
                    config_container,
                )
            )
        except Exception:
            _mark_template_copy_idle()
            logger.exception("Could not return template delete result to the UI")
            _notify_result_lost("delete")

    threading.Thread(target=work, name="TemplateDelete", daemon=True).start()

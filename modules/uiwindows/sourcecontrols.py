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

# Source Controls tab: the buttons and inputs from every template's ``dynamic_controls``.
#
# Same look and behaviour as the OBS dock (templates/source_controls.html): both use
# assets/default_assets/source_controls.css (.scx ...) and the live values that
# ``/api/template-configs`` serves. Controls are native elements styled by that sheet;
# their values are written as ``defaultValue`` (never ``value``) so a re-render of the
# tab never resets what the user is typing. Live value changes arrive through
# :func:`mark_source_controls_dirty` (any thread) and are applied in place by a timer.

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from nicegui import run, ui
from nicegui.elements.mixins.text_element import TextElement

from .. import template_config_parser, web_engine
from ..notification_engine import nav_source_template, notify
from ..spore_studio.spore_data_codegen import timer_control_event
from ..ui_buttons import apply_flat_btn_props
from ..ui_timer import layout_schedule

logger = logging.getLogger(__name__)

# The grid that holds the template cards (kept under its historical name).
source_controls_container = None

_STYLE_ID = "mycelian-source-controls"
_APP_STYLE_ID = "mycelian-source-controls-app"
_DOCK_ROUTE = "source_controls"
_HELP_TOPIC = "source_controls"

# Live refresh: how often the tab checks for changes, and how long a field the
# user just changed is left alone.
_REFRESH_TICK_SEC = 0.5
_USER_EDIT_GRACE_SEC = 2.0
_TEXT_DEBOUNCE_SEC = 0.2
_SLIDER_THROTTLE_SEC = 0.05

# Element types the no-engine fallback picks out of a template's plain "elements".
_FALLBACK_CONTROL_TYPES = (
    "button",
    "slider",
    "toggle",
    "counter_control",
    "spin_control",
    "text_input",
    "number_input",
)

# App-only additions to the shared sheet: the tab fills its panel (header fixed,
# cards scroll), the Quasar help button matches .scx-hbtn.is-icon, empty states, and
# text spans inherit their control's colour (the app's "body span" rule would win).
_APP_CSS = """
.scx.is-app { height: 100%; min-height: 0; display: flex; flex-direction: column; }
.scx.is-app .scx-shell { display: flex; flex-direction: column; min-height: 0; max-height: 100%; }
.scx.is-app .scx-head { flex: 0 0 auto; }
.scx.is-app .scx-body { flex: 1 1 auto; min-height: 0; overflow-y: auto; }
.scx .scx-hbtn.is-icon:hover { color: var(--color-text-primary); background: var(--color-bg-base); }
.scx .scx-hbtn:focus-visible, .scx .scx-btn:focus-visible, .scx .scx-switch:focus-visible, .scx .scx-iconbtn:focus-visible,
.scx .scx-open:focus-visible, .scx .scx-seg button:focus-visible, .scx .scx-stepper button:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--color-primary) 60%, transparent); outline-offset: 1px; }
.scx .scx-head .help-inline-icon.q-btn { width: 32px; height: 32px; min-width: 32px; min-height: 32px; padding: 0; border-radius: 8px;
  font-size: 10px; color: var(--color-text-muted); }
.scx .scx-head .help-inline-icon.q-btn:hover { color: var(--color-text-primary); }
.scx button > span:not(.mi), .scx .scx-label > span, .scx .scx-banner-opts > span,
.scx .scx-empty > div:not(.scx-empty-title) { color: inherit; }
.scx .scx-stepper input::-webkit-outer-spin-button, .scx .scx-stepper input::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
.scx .scx-stepper input[type=number] { -moz-appearance: textfield; }
.scx .scx-unknown { font-size: .78rem; color: var(--color-error); }
.scx .scx-empty { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 36px 12px; text-align: center;
  font-size: .8rem; color: var(--color-text-muted); }
.scx .scx-empty .mi { font-size: 30px; color: var(--color-text-muted); }
.scx .scx-empty-title { font-size: .9rem; font-weight: 650; color: var(--color-text-secondary); }
"""


class _Tag(TextElement):
    """A native element with text (``<span>``, ``<button>`` ...)."""

    def __init__(self, tag: str, text: str = "") -> None:
        super().__init__(tag=tag, text=text)


def _icon(name: str):
    return ui.icon(name).classes("mi")


def _resolve_button_text(element: dict) -> str:
    if element.get("button_text"):
        return str(element["button_text"])
    if element.get("label"):
        return str(element["label"])
    action = str(element.get("action") or "").strip()
    if action:
        return action.replace("_", " ").title()
    return "Run"


def _is_spin(element: dict) -> bool:
    return element.get("type") == "spin_control" or element.get("action") == "spin"


def _button_label(element: dict) -> str:
    if _is_spin(element) and not element.get("button_text"):
        return "Spin"
    return _resolve_button_text(element)


def _template_sort_key(name: str) -> tuple:
    """Alphabetical, case-insensitive (same order as the OBS dock)."""
    return (str(name).lower(), str(name))


def _control_elements(controls_config: Any) -> List[dict]:
    if not isinstance(controls_config, dict):
        return []
    elements = controls_config.get("elements")
    if not isinstance(elements, list):
        return []
    return [e for e in elements if isinstance(e, dict)]


def _search_text(template_name: str, elements: List[dict]) -> str:
    parts = [str(template_name)]
    for el in elements:
        for key in ("label", "button_text"):
            if el.get(key):
                parts.append(str(el[key]))
        if el.get("type") in ("button", "spin_control"):
            parts.append(_button_label(el))
    return "\n".join(parts).lower()


def _structure_signature(template_name: str, elements: List[dict], hidden: bool) -> str:
    """Everything that shapes a card except live values (a change means a rebuild)."""
    shape = [{k: v for k, v in el.items() if k != "value"} for el in elements]
    return json.dumps([template_name, bool(hidden), shape], sort_keys=True, default=str)


def _to_number(raw: Any) -> Optional[float]:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        text = str(raw).strip()
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def _fmt_number(value: Any) -> str:
    num = _to_number(value)
    if num is None:
        return ""
    if num.is_integer():
        return str(int(num))
    return repr(num)


def _slider_value(raw: Any) -> Any:
    num = _to_number(raw)
    if num is None:
        return None
    return int(num) if num.is_integer() else num


def _percent(value: Any, lo: Any, hi: Any) -> float:
    v, a, b = _to_number(value), _to_number(lo), _to_number(hi)
    a = 0.0 if a is None else a
    b = 100.0 if b is None else b
    v = a if v is None else v
    span = (b - a) or 1.0
    return max(0.0, min(100.0, (v - a) / span * 100.0))


def _counter_step_value(element) -> int:
    raw = element.get("step", element.get("value", 1))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _spore_counter_payload(element, operation: str, amount: int) -> dict:
    target_counter_id = element.get("target_counter_id", "")
    if operation == "reset":
        return {"target_counter_id": target_counter_id, "operation": "reset"}
    return {
        "target_counter_id": target_counter_id,
        "operation": operation,
        "delta": {"kind": "fixed", "value": max(1, amount)},
    }


def dock_url() -> str:
    """The OBS custom browser dock address (same server and route OBS loads)."""
    host, port = "127.0.0.1", 5000
    engine = web_engine.web_engine_instance
    if engine is not None:
        try:
            port = int(getattr(engine, "port", port) or port)
            host = engine._local_connect_host()
        except Exception:
            logger.debug("dock url: using defaults", exc_info=True)
    return f"http://{host}:{port}/{_DOCK_ROUTE}"


# --------------------------------------------------------------------------- data


def _load_configs_from_files(parser) -> Tuple[Dict[str, Any], List[Tuple[str, Exception]]]:
    """Fallback when the overlay server isn't there: read the template JSON directly."""
    resolve = getattr(template_config_parser, "resolve_dynamic_control_values_from_elements", None)
    configs: Dict[str, Any] = {}
    errors: List[Tuple[str, Exception]] = []
    for config_name in sorted(parser.get_config_files() or []):
        try:
            config = parser.load_config(config_name, include_dynamic_controls=True)
            dynamic_controls = None
            if isinstance(config, dict):
                if "dynamic_controls" in config:
                    dynamic_controls = config["dynamic_controls"]
                    if resolve is not None:
                        try:
                            dynamic_controls = resolve(config) or dynamic_controls
                        except Exception:
                            logger.debug("live values unavailable for %s", config_name, exc_info=True)
                elif "elements" in config:
                    elements = config["elements"]
                    if isinstance(elements, list):
                        control_elements = [
                            elem
                            for elem in elements
                            if isinstance(elem, dict)
                            and elem.get("type") in _FALLBACK_CONTROL_TYPES
                            and (
                                elem.get("action")
                                or elem.get("action_increment")
                                or elem.get("action_decrement")
                                or elem.get("action_reset")
                            )
                        ]
                        if control_elements:
                            dynamic_controls = {"elements": control_elements}
            if dynamic_controls:
                configs[config_name] = dynamic_controls
        except Exception as e:
            logger.error(f"Error processing config {config_name}: {str(e)}", exc_info=True)
            errors.append((config_name, e))
    return configs, errors


def _fetch_source_controls() -> Tuple[Dict[str, Any], set, List[Tuple[str, Exception]]]:
    """Live configs, hidden template names and read errors. No UI calls (runs off the loop)."""
    configs: Optional[Dict[str, Any]] = None
    errors: List[Tuple[str, Exception]] = []
    engine = web_engine.web_engine_instance
    getter = getattr(engine, "get_source_controls_configs", None) if engine is not None else None
    if callable(getter):
        try:
            result = getter()
            if isinstance(result, dict):
                configs = result
        except Exception:
            logger.debug("source controls: engine configs unavailable", exc_info=True)
    parser = template_config_parser.TemplateConfigParser()
    if configs is None:
        configs, errors = _load_configs_from_files(parser)
    configs = {name: cfg for name, cfg in configs.items() if _control_elements(cfg)}
    hidden = set()
    for name, cfg in configs.items():
        flag = cfg.get("hidden") if isinstance(cfg, dict) else None
        if flag is None:
            try:
                flag = parser.is_config_hidden(name)
            except Exception:
                flag = False
        if flag:
            hidden.add(name)
    return configs, hidden, errors


def _notify_config_errors(errors: List[Tuple[str, Exception]]) -> None:
    for config_name, e in errors:
        notify(
            "Its controls aren't shown here until its configuration can be read.",
            type="warning",
            source="source_controls",
            title=f"Couldn't read the controls for {config_name}",
            hint="Open the template in Source Settings to check its configuration, then refresh this page.",
            details=f"{type(e).__name__}: {e}",
            actions=nav_source_template(config_name),
            dedupe_key=f"source_controls:config:{config_name}",
            dedupe_cooldown_sec=600.0,
        )


def _engine_generation() -> Any:
    engine = web_engine.web_engine_instance
    return getattr(engine, "_template_config_generation", None) if engine is not None else None


# ------------------------------------------------------------------ live updates

_dirty_lock = threading.Lock()
_dirty_names: set = set()
_dirty_state = {"all": False}


def mark_source_controls_dirty(payload: Optional[dict] = None) -> None:
    """Note that live values changed (called by web_engine from any thread; cheap).

    The open tab picks this up on its next tick and refreshes the values of the
    affected templates in place (all of them when ``template_names`` is empty).
    """
    names = payload.get("template_names") if isinstance(payload, dict) else None
    with _dirty_lock:
        if isinstance(names, (list, tuple, set)) and names:
            _dirty_names.update(str(n) for n in names if n)
        else:
            _dirty_state["all"] = True


def _take_dirty() -> Tuple[set, bool]:
    with _dirty_lock:
        names = set(_dirty_names)
        everything = _dirty_state["all"]
        _dirty_names.clear()
        _dirty_state["all"] = False
    return names, everything


class _LiveControl:
    """A control whose value follows the runtime value (toggle, number, text, slider)."""

    def __init__(self, kind: str, el, value: Any, chip=None, lo: Any = 0, hi: Any = 100) -> None:
        self.kind = kind
        self.el = el
        self.value = value
        self.chip = chip
        self.lo = lo
        self.hi = hi
        self.last_edit = 0.0
        self.needs_sync = False

    def recently_edited(self, window: float = _USER_EDIT_GRACE_SEC) -> bool:
        return time.monotonic() - self.last_edit < window

    def user_changed(self, value: Any) -> None:
        """Remember the user's value without echoing it (that would fight their typing)."""
        self.value = value
        self.last_edit = time.monotonic()
        if self.kind == "toggle":
            return
        self.el._props["defaultValue"] = _fmt_number(value) if self.kind != "text" else str(value or "")
        if self.kind == "slider":
            self.el._style["--p"] = f"{_percent(value, self.lo, self.hi):.2f}%"
            if self.chip is not None:
                self.chip.set_text(_fmt_number(value))
        self.needs_sync = True

    def sync_if_settled(self) -> None:
        """Send the stored value once the user stops, so a remount shows it."""
        if self.needs_sync and not self.recently_edited(1.0) and not self.el.is_deleted:
            self.needs_sync = False
            self.el.update()

    def apply_live(self, value: Any) -> None:
        if self.el.is_deleted or self.recently_edited():
            return
        if self.kind == "toggle":
            on = bool(value)
            if on == bool(self.value):
                return
            self.value = on
            _set_switch(self.el, on)
            return
        if self.kind == "text":
            text = "" if value is None else str(value)
            if text == str(self.value or ""):
                return
            self.value = text
            self.el._props["defaultValue"] = text
            self.el.update()
            self._set_dom_value(text)
            return
        num = _to_number(value)
        if num is None or num == _to_number(self.value):
            return
        self.value = _slider_value(num) if self.kind == "slider" else num
        shown = _fmt_number(num)
        self.el._props["defaultValue"] = shown
        pct = None
        if self.kind == "slider":
            pct = f"{_percent(num, self.lo, self.hi):.2f}%"
            self.el._style["--p"] = pct
            if self.chip is not None:
                self.chip.set_text(shown)
        self.el.update()
        self._set_dom_value(shown, pct)

    def _set_dom_value(self, text: str, pct: Optional[str] = None) -> None:
        js = (
            "(() => { const e = document.getElementById(%s);"
            " if (!e || document.activeElement === e) return; e.value = %s;%s })()"
            % (
                json.dumps(self.el.html_id),
                json.dumps(text),
                f" e.style.setProperty('--p', {json.dumps(pct)});" if pct else "",
            )
        )
        try:
            self.el.client.run_javascript(js)
        except Exception:
            logger.debug("source controls: live value not pushed", exc_info=True)


def _set_switch(el, on: bool) -> None:
    if on:
        el.classes(add="is-on")
    else:
        el.classes(remove="is-on")
    el.props(f'aria-checked="{"true" if on else "false"}"')


class _Card:
    def __init__(self, name: str, element, search: str, signature: str) -> None:
        self.name = name
        self.element = element
        self.search = search
        self.signature = signature
        self.controls: Dict[int, _LiveControl] = {}


class _SourceControlsView:
    """State of the open Source Controls tab."""

    def __init__(self) -> None:
        self.grid = None
        self.pill = None
        self.empty_search = None
        self.cards: Dict[str, _Card] = {}
        self.query = ""
        self.generation: Any = None
        self.inflight = False

    # -- render
    def render(self, configs: Dict[str, Any], hidden: set) -> None:
        grid = self.grid
        if grid is None or grid.is_deleted:
            return
        grid.clear()
        self.cards = {}
        names = sorted(configs, key=_template_sort_key)
        total = sum(len(_control_elements(configs[n])) for n in names)
        if self.pill is not None:
            self.pill.set_text(
                f"{len(names)} template{'' if len(names) == 1 else 's'} · "
                f"{total} control{'' if total == 1 else 's'}"
            )
        with grid:
            if not names:
                with ui.element("div").classes("scx-empty").style("column-span: all"):
                    _icon("gamepad")
                    _Tag("div", "No source controls yet").classes("scx-empty-title")
                    _Tag(
                        "div",
                        "Add controls from a template's Source Controls tab in Spore Studio, then refresh this page.",
                    )
                    _Tag("div", "Hidden templates are included when they have controls.")
            for name in names:
                card = create_template_control_section(name, configs[name], hidden=name in hidden)
                if card is not None:
                    self.cards[name] = card
        self.apply_search()

    def apply_search(self) -> None:
        query = self.query.strip().lower()
        shown = 0
        for card in self.cards.values():
            visible = not query or query in card.search
            shown += 1 if visible else 0
            try:
                card.element.set_visibility(visible)
            except Exception:
                logger.debug("source control search update failed", exc_info=True)
        if self.empty_search is not None and not self.empty_search.is_deleted:
            self.empty_search.set_visibility(bool(query) and shown == 0 and bool(self.cards))

    # -- live values
    def apply(self, configs: Dict[str, Any], hidden: set, names: Optional[set]) -> None:
        current = {name: card.signature for name, card in self.cards.items()}
        incoming = {
            name: _structure_signature(name, _control_elements(cfg), name in hidden)
            for name, cfg in configs.items()
        }
        if current != incoming:
            self.render(configs, hidden)
            return
        for name, card in self.cards.items():
            if names is not None and name not in names:
                continue
            elements = _control_elements(configs.get(name))
            for idx, ctl in card.controls.items():
                if idx < len(elements) and "value" in elements[idx]:
                    ctl.apply_live(elements[idx].get("value"))

    async def tick(self) -> None:
        for card in self.cards.values():
            for ctl in card.controls.values():
                ctl.sync_if_settled()
        if self.inflight or self.grid is None or self.grid.is_deleted:
            return
        generation = _engine_generation()
        generation_changed = generation is not None and generation != self.generation
        names, everything = _take_dirty()
        if not (names or everything or generation_changed):
            return
        self.generation = generation
        self.inflight = True
        try:
            configs, hidden, errors = await run.io_bound(_fetch_source_controls)
        except Exception:
            logger.debug("source controls: live refresh failed", exc_info=True)
            return
        finally:
            self.inflight = False
        if self.grid is None or self.grid.is_deleted:
            return
        _notify_config_errors(errors)
        self.apply(configs, hidden, None if (everything or generation_changed) else names)


_view: Optional[_SourceControlsView] = None


# --------------------------------------------------------------------- the tab


def _inject_styles() -> None:
    from ..help_system.help_render import inject_page_stylesheet, promote_color_importance, scope_selectors
    from ..path_utils import get_assets_path

    css = ""
    try:
        path = get_assets_path(os.path.join("default_assets", "source_controls.css"))
        with open(path, encoding="utf-8") as fh:
            css = fh.read()
    except Exception:
        logger.error("Source Controls stylesheet missing", exc_info=True)
    inject_page_stylesheet(_STYLE_ID, promote_color_importance(scope_selectors(css, ".scx")))
    inject_page_stylesheet(_APP_STYLE_ID, promote_color_importance(scope_selectors(_APP_CSS, ".scx")))


def _copy_dock_link() -> None:
    from ..notification_engine import copy_text_to_clipboard

    url = dock_url()
    copied = False
    try:
        copied = copy_text_to_clipboard(url)
    except Exception:
        logger.debug("dock link copy failed", exc_info=True)
    if not copied:
        try:
            ui.clipboard.write(url)
            copied = True
        except Exception:
            logger.debug("browser clipboard copy failed", exc_info=True)
    if copied:
        notify(
            url,
            type="success",
            source="source_controls",
            title="OBS dock link copied",
            hint="In OBS open Docks → Custom Browser Docks, paste it as the URL and click Apply.",
        )
        return
    notify(
        url,
        type="warning",
        source="source_controls",
        title="Couldn't copy the OBS dock link",
        hint="Select the address and press Ctrl+C (Cmd+C on Mac).",
        actions=[{"kind": "copy", "text": url, "label": "Copy link"}],
    )


def _header_button(text: str, icon: str, on_click, *, tooltip: str = "", icon_only: bool = False):
    btn = ui.element("button").classes("scx-hbtn" + (" is-icon" if icon_only else "")).props('type="button"')
    if icon_only:
        btn.props(f'aria-label="{tooltip or text}"')
    with btn:
        _icon(icon)
        if text and not icon_only:
            _Tag("span", text)
    btn.on("click", lambda _e: on_click())
    if tooltip:
        btn.tooltip(tooltip)
    return btn


def create_source_controls_tab():
    """Create the Source Controls tab UI"""
    global source_controls_container, _view

    from ..help_system.contextual_help import help_button

    _inject_styles()
    view = _SourceControlsView()
    _view = view

    def on_search(e) -> None:
        view.query = str(e.args or "")
        view.apply_search()

    with ui.element("div").classes("source-controls-tab w-full h-full min-h-0 flex flex-col p-4"):
        with ui.element("div").classes("scx is-app"):
            with ui.element("div").classes("scx-shell"):
                with ui.element("div").classes("scx-head"):
                    with ui.element("span").classes("scx-tile"):
                        _icon("gamepad")
                    with ui.element("span"):
                        _Tag("div", "Source Controls").classes("scx-title")
                        _Tag("div", "Buttons and inputs from your templates · also available as an OBS dock").classes(
                            "scx-sub"
                        )
                    view.pill = _Tag("span", "").classes("scx-pill")
                    ui.element("span").classes("scx-spacer")
                    with ui.element("label").classes("scx-search"):
                        _icon("search")
                        ui.element("input").props(
                            'type="search" placeholder="Search templates and controls" '
                            'aria-label="Search templates and controls" autocomplete="off" spellcheck="false"'
                        ).on("input", on_search, js_handler="(e) => emit(e.target.value)")
                    _header_button(
                        "OBS dock link",
                        "content_copy",
                        _copy_dock_link,
                        tooltip="Copy the address for an OBS custom browser dock",
                    )
                    _header_button("", "refresh", refresh_source_controls, tooltip="Refresh", icon_only=True)
                    help_button(topic_id=_HELP_TOPIC, tooltip="Source Controls help", size="sm").props(
                        remove="color"
                    )
                with ui.element("div").classes("scx-body"):
                    source_controls_container = ui.element("div").classes("scx-grid")
                    view.grid = source_controls_container
                    view.empty_search = ui.element("div").classes("scx-empty")
                    with view.empty_search:
                        _icon("search_off")
                        _Tag("div", "Nothing matches your search").classes("scx-empty-title")
                        _Tag("div", "Search looks at template names, control labels and button text.")
                    view.empty_search.set_visibility(False)
        ui.timer(_REFRESH_TICK_SEC, view.tick)

    load_source_controls()


def load_source_controls():
    """Load and display source controls from template configs"""
    view = _view
    if view is None or source_controls_container is None or source_controls_container.is_deleted:
        logger.error("Source controls container not initialized")
        return

    if web_engine.web_engine_instance:
        try:
            web_engine.web_engine_instance.register_additional_control_handlers()
            logger.debug("Registered additional control handlers")
        except Exception as e:
            logger.error(
                f"Error registering additional control handlers: {str(e)}",
                exc_info=True,
            )

    _take_dirty()
    view.generation = _engine_generation()
    configs, hidden, errors = _fetch_source_controls()
    _notify_config_errors(errors)
    view.render(configs, hidden)


def refresh_source_controls():
    """Refresh the source controls display"""
    load_source_controls()


# ---------------------------------------------------------------- one template


def create_template_control_section(template_name, controls_config, hidden: Optional[bool] = None):
    """Build one template card; returns its :class:`_Card` (element, search text, live controls)."""
    from ..help_system.contextual_help import navigate_to_source_settings_template

    if hidden is None:
        try:
            hidden = template_config_parser.TemplateConfigParser().is_config_hidden(template_name)
        except Exception:
            hidden = False
    elements = _control_elements(controls_config)
    count = len(elements)

    element = ui.element("div").classes("scx-card")
    card = _Card(
        template_name,
        element,
        _search_text(template_name, elements),
        _structure_signature(template_name, elements, bool(hidden)),
    )
    with element:
        with ui.element("div").classes("scx-card-head"):
            _Tag("span", str(template_name)).classes("scx-card-name").props(f'title="{_attr(template_name)}"')
            _Tag("span", str(count)).classes("scx-count").props(
                f'title="{count} control{"" if count == 1 else "s"}"'
            )
            if hidden:
                _Tag("span", "Hidden").classes("scx-tag").props('title="Hidden in Source Settings"')
            open_btn = ui.element("button").classes("scx-open").props(
                'type="button" aria-label="Open in Source Settings"'
            )
            with open_btn:
                _icon("tune")
            open_btn.tooltip("Open in Source Settings")
            open_btn.on("click", lambda _e, n=template_name: navigate_to_source_settings_template(n))
        with ui.element("div").classes("scx-card-body"):
            buttons: List[dict] = []

            def flush() -> None:
                if not buttons:
                    return
                with ui.element("div").classes("scx-btns"):
                    for i, btn_el in enumerate(buttons):
                        wide = len(buttons) % 2 == 1 and i == len(buttons) - 1
                        create_button_control(template_name, btn_el, wide=wide)
                buttons.clear()

            for idx, el in enumerate(elements):
                if el.get("type") in ("button", "spin_control"):
                    buttons.append(el)
                    continue
                flush()
                live = create_control_element(template_name, el)
                if live is not None:
                    card.controls[idx] = live
            flush()
    return card


def _attr(text: Any) -> str:
    return str(text).replace("&", "&amp;").replace('"', "&quot;")


def _label(element: dict, fallback: str = ""):
    """``.scx-label``: text plus an info icon and tooltip when there is a description."""
    text = element.get("label") or fallback or element.get("id") or ""
    description = str(element.get("description") or "")
    lab = ui.element("div").classes("scx-label")
    with lab:
        _Tag("span", str(text))
        if description:
            _icon("info_outline")
    if description:
        lab.tooltip(description)
    return lab


def _native_input(classes: str, *, value: Any = None, input_type: str = "text", attrs: str = ""):
    el = ui.element("input").classes(classes).props(f'type="{input_type}" {attrs}'.strip())
    if value is not None:
        el._props["defaultValue"] = value
    return el


def create_control_element(template_name, element):
    """Render one non-button control; returns its live-value handle when it has one."""
    etype = element.get("type", "unknown")
    if etype == "toggle":
        return create_toggle_control(template_name, element)
    if etype == "number_input":
        return create_number_input_control(template_name, element)
    if etype == "text_input":
        return create_text_input_control(template_name, element)
    if etype == "slider":
        return create_slider_control(template_name, element)
    if etype == "counter_control":
        create_counter_control(template_name, element)
    elif etype == "timer_control":
        create_timer_control(template_name, element)
    elif etype == "banner_control":
        create_banner_control(template_name, element)
    else:
        _Tag("div", f"Unknown control type: {etype}").classes("scx-unknown")
    return None


def create_button_control(template_name, element, wide: bool = False):
    """A button (or spin) in the card's two-column button grid."""
    spin = _is_spin(element)
    classes = "scx-btn" + (" is-spin" if spin else "") + (" is-wide" if wide else "")
    btn = _Tag("button", _button_label(element)).classes(classes).props('type="button"')
    if element.get("type") == "spin_control":
        action = element.get("action", "spin")

        def handle_click(_e=None):
            send_websocket_event(template_name, action, {})
    else:
        action = element.get("action", "")

        def handle_click(_e=None):
            send_websocket_event(template_name, action, element.get("data", {}))

    btn.on("click", handle_click)
    if element.get("description"):
        btn.tooltip(str(element["description"]))
    return btn


def create_toggle_control(template_name, element):
    """Label and switch; sends {"enabled": bool}."""
    action = element.get("action", "")
    on = bool(element.get("value", False))
    with ui.element("div").classes("scx-row"):
        _label(element)
        sw = ui.element("span").classes("scx-switch" + (" is-on" if on else "")).props(
            f'role="switch" tabindex="0" aria-checked="{"true" if on else "false"}" '
            f'aria-label="{_attr(element.get("label") or element.get("id") or "Toggle")}"'
        )
    live = _LiveControl("toggle", sw, on)

    def toggle(_e=None) -> None:
        new = not bool(live.value)
        live.user_changed(new)
        _set_switch(sw, new)
        send_websocket_event(template_name, action, {"enabled": new})

    sw.on("click", toggle)
    sw.on("keydown", toggle, js_handler="(e) => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); emit(); } }")
    return live


def create_number_input_control(template_name, element):
    """Label and number box; sends {"value": number} as you type."""
    action = element.get("action", "")
    value = element.get("value", 0)
    attrs = ""
    if element.get("min") is not None:
        attrs += f' min="{_attr(element.get("min"))}"'
    if element.get("max") is not None:
        attrs += f' max="{_attr(element.get("max"))}"'
    if element.get("step") is not None:
        attrs += f' step="{_attr(element.get("step"))}"'
    with ui.element("div").classes("scx-row"):
        _label(element)
        box = _native_input(
            "scx-input is-num",
            value=_fmt_number(value),
            input_type="number",
            attrs=f'aria-label="{_attr(element.get("label") or "Value")}"{attrs}',
        )
    live = _LiveControl("number", box, _to_number(value))

    def handle(e) -> None:
        num = _to_number(e.args)
        live.user_changed(num)
        send_websocket_event(template_name, action, {"value": num})

    box.on("input", handle, js_handler="(e) => emit(e.target.value)")
    return live


def create_text_input_control(template_name, element):
    """Label and text box showing the live value; sends {"text": str} ~200 ms after typing stops."""
    action = element.get("action", "")
    value = element.get("value")
    text = "" if value is None else str(value)
    with ui.element("div").classes("scx-field"):
        _label(element)
        box = _native_input(
            "scx-input is-text",
            value=text,
            attrs=(
                f'placeholder="{_attr(element.get("placeholder", ""))}" '
                f'aria-label="{_attr(element.get("label") or "Text")}" autocomplete="off"'
            ),
        )
    live = _LiveControl("text", box, text)
    emit_timer: dict = {"timer": None}

    def handle(e) -> None:
        live.user_changed("" if e.args is None else str(e.args))
        existing = emit_timer["timer"]
        if existing is not None:
            existing.active = False

        def emit_now():
            emit_timer["timer"] = None
            send_websocket_event(template_name, action, {"text": str(live.value or "")})

        emit_timer["timer"] = layout_schedule(_TEXT_DEBOUNCE_SEC, emit_now, once=True)

    box.on("input", handle, js_handler="(e) => emit(e.target.value)")
    return live


def create_slider_control(template_name, element):
    """Label, value chip and theme-coloured range; sends {"value": number}."""
    action = element.get("action", "")
    lo = element.get("min", 0)
    hi = element.get("max", 100)
    step = element.get("step", 1)
    value = element.get("value", lo)
    with ui.element("div").classes("scx-field"):
        with ui.element("div").classes("scx-row"):
            _label(element)
            chip = _Tag("span", _fmt_number(value)).classes("scx-count")
        rng = _native_input(
            "scx-range",
            value=_fmt_number(value),
            input_type="range",
            attrs=(
                f'min="{_attr(lo)}" max="{_attr(hi)}" step="{_attr(step)}" '
                f'aria-label="{_attr(element.get("label") or "Slider")}"'
            ),
        ).style(f"--p: {_percent(value, lo, hi):.2f}%")
    live = _LiveControl("slider", rng, _slider_value(value), chip=chip, lo=lo, hi=hi)

    def handle(e) -> None:
        num = _slider_value(e.args)
        if num is None:
            return
        live.user_changed(num)
        send_websocket_event(template_name, action, {"value": num})

    rng.on(
        "input",
        handle,
        throttle=_SLIDER_THROTTLE_SEC,
        js_handler=(
            "(e) => { const t = e.target, lo = Number(t.min) || 0, hi = Number(t.max) || 100;"
            " t.style.setProperty('--p', ((Number(t.value) - lo) / ((hi - lo) || 1) * 100) + '%');"
            " emit(t.value); }"
        ),
    )
    return live


def _counter_row(element, fallback: str):
    row = ui.element("div").classes("scx-row")
    with row:
        _label(element, fallback)
    return row


def _stepper_button(text: str, cls: str, tooltip: str, on_click):
    btn = _Tag("button", text).classes(cls).props(f'type="button" aria-label="{tooltip}" title="{tooltip}"')
    btn.on("click", lambda _e: on_click())
    return btn


def _reset_icon_button(on_click, tooltip: str = "Reset"):
    btn = ui.element("button").classes("scx-iconbtn").props(f'type="button" aria-label="{tooltip}"')
    with btn:
        _icon("restart_alt")
    btn.tooltip(tooltip)
    btn.on("click", lambda _e: on_click())
    return btn


def create_counter_control(template_name, element):
    """− / + (and the amount box for Spore counters) plus reset."""
    if element.get("target_counter_id"):
        _create_spore_counter_control(template_name, element)
        return

    action_increment = element.get("action_increment", "counter_increment")
    action_decrement = element.get("action_decrement", "counter_decrement")
    action_reset = element.get("action_reset", "counter_reset")

    with _counter_row(element, "Counter"):
        with ui.element("div").classes("scx-tools"):
            with ui.element("div").classes("scx-stepper"):
                _stepper_button(
                    "−", "minus", "Decrease", lambda: send_websocket_event(template_name, action_decrement, {})
                )
                _stepper_button(
                    "+", "plus", "Increase", lambda: send_websocket_event(template_name, action_increment, {})
                )
            _reset_icon_button(lambda: send_websocket_event(template_name, action_reset, {}))


def _create_spore_counter_control(template_name, element):
    """Spore Studio counter_control: amount box between − and +, and reset."""
    action = element.get("action", "counter_adjust")
    step = _counter_step_value(element)
    amount = {"value": step}

    def current_amount() -> int:
        try:
            return max(1, int(float(amount["value"])))
        except (TypeError, ValueError):
            return step

    def send(operation: str) -> None:
        send_websocket_event(
            template_name, action, _spore_counter_payload(element, operation, current_amount())
        )

    def on_amount(e) -> None:
        amount["value"] = e.args

    with _counter_row(element, "Counter"):
        with ui.element("div").classes("scx-tools"):
            with ui.element("div").classes("scx-stepper"):
                _stepper_button("−", "minus", "Decrease", lambda: send("decrement"))
                box = _native_input(
                    "",
                    value=str(step),
                    input_type="number",
                    attrs='min="1" max="999999999" title="Amount to increment or decrement" aria-label="Amount"',
                )
                box.on("input", on_amount, js_handler="(e) => emit(e.target.value)")
                _stepper_button("+", "plus", "Increase", lambda: send("increment"))
            _reset_icon_button(lambda: send("reset"))


def create_timer_control(template_name, element):
    """Start, stop and reset for one Spore Studio timer."""
    target = str(element.get("target_timer_id") or "")

    def send(operation: str) -> None:
        send_websocket_event(
            template_name,
            timer_control_event(target, operation),
            {"element_id": target},
        )

    def seg_button(text: str, icon: str, cls: str, operation: str, tooltip: str = ""):
        btn = ui.element("button").classes(cls).props('type="button"')
        if tooltip:
            btn.props(f'aria-label="{tooltip}"')
        with btn:
            _icon(icon)
            if text:
                _Tag("span", text)
        if tooltip:
            btn.tooltip(tooltip)
        btn.on("click", lambda _e: send(operation))

    with _counter_row(element, "Timer"):
        with ui.element("div").classes("scx-seg"):
            seg_button("Start", "play_arrow", "go", "start")
            seg_button("Stop", "pause", "pause", "pause")
            seg_button("", "restart_alt", "", "reset", "Reset")


def create_banner_control(template_name, element):
    """Message, duration and Permanent switch; Show / Clear the chat banner."""
    from ..chat_banner import emit_chat_banner

    placeholder = element.get("placeholder", "Banner message")
    try:
        default_duration = float(element.get("duration", 10))
    except (TypeError, ValueError):
        default_duration = 10.0
    min_val = _to_number(element.get("min", 0))
    max_val = _to_number(element.get("max", 3600))
    state = {"text": "", "duration": default_duration, "permanent": False}

    def current_duration() -> float:
        num = _to_number(state["duration"])
        if num is None:
            return default_duration
        if min_val is not None:
            num = max(min_val, num)
        if max_val is not None:
            num = min(max_val, num)
        return num

    def show_banner(_e=None):
        emit_chat_banner(
            str(state["text"] or ""),
            duration=current_duration(),
            permanent=bool(state["permanent"]),
            source="manual",
        )

    def clear_banner(_e=None):
        emit_chat_banner(action="clear", source="manual")

    with ui.element("div").classes("scx-banner"):
        _label(element, "Banner")
        msg = _native_input(
            "scx-input is-text",
            attrs=f'placeholder="{_attr(placeholder)}" aria-label="Banner message" autocomplete="off"',
        )
        msg.on("input", lambda e: state.__setitem__("text", e.args or ""), js_handler="(e) => emit(e.target.value)")
        with ui.element("div").classes("scx-banner-opts"):
            dur_attrs = 'aria-label="Seconds to show the banner"'
            if min_val is not None:
                dur_attrs += f' min="{_fmt_number(min_val)}"'
            if max_val is not None:
                dur_attrs += f' max="{_fmt_number(max_val)}"'
            dur = _native_input(
                "scx-input", value=_fmt_number(default_duration), input_type="number", attrs=dur_attrs
            )
            dur.props('title="Seconds to show the banner. Ignored when Permanent is on."')
            dur.on("input", lambda e: state.__setitem__("duration", e.args), js_handler="(e) => emit(e.target.value)")
            _Tag("span", "s")
            ui.element("span").style("flex: 1")
            perm = ui.element("span").classes("scx-switch").props(
                'role="switch" tabindex="0" aria-checked="false" aria-label="Permanent"'
            )

            def toggle_permanent(_e=None) -> None:
                state["permanent"] = not state["permanent"]
                _set_switch(perm, state["permanent"])

            perm.on("click", toggle_permanent)
            perm.on(
                "keydown",
                toggle_permanent,
                js_handler="(e) => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); emit(); } }",
            )
            _Tag("span", "Permanent")
        with ui.element("div").classes("scx-btns"):
            show = ui.element("button").classes("scx-btn").props('type="button"')
            with show:
                _icon("campaign")
                _Tag("span", "Show")
            show.on("click", show_banner)
            _Tag("button", "Clear").classes("scx-btn is-plain").props('type="button"').on("click", clear_banner)


# ------------------------------------------------------------------- send paths

# Sliders send many events; say "it didn't reach the overlay" at most this often.
_EMIT_FAIL_NOTIFY_INTERVAL_SEC = 30.0
_emit_fail_last = {"t": None}


def _notify_control_not_sent(template_name, action, reason: str, details: str = "") -> None:
    now = time.monotonic()
    last = _emit_fail_last["t"]
    if last is not None and now - last < _EMIT_FAIL_NOTIFY_INTERVAL_SEC:
        return
    _emit_fail_last["t"] = now
    what = str(action or "control").replace("_", " ")
    notify(
        reason,
        type="warning",
        source="source_controls",
        title=f"“{what}” wasn't sent to {template_name}",
        hint="Check the overlay server in the footer. Controls work again once it is running.",
        details=details or None,
        dedupe_key="source_controls:emit_failed",
        dedupe_cooldown_sec=_EMIT_FAIL_NOTIFY_INTERVAL_SEC,
    )


def send_websocket_event(template_name, action, data):
    """Send websocket event to overlay clients (same delivery path as OBS dock relay)."""
    event_name = f"{template_name}_{action}"
    try:
        engine = web_engine.web_engine_instance
        if not engine or not getattr(engine, "is_running", True):
            logger.error("Web engine not available for sending websocket events")
            _notify_control_not_sent(
                template_name, action, "The overlay server isn't running, so the overlay didn't change."
            )
            return
        engine.emit_template_control_event(template_name, action, data)
        logger.debug("Sent websocket event: %s with data: %s", event_name, data)
    except Exception as e:
        logger.error(
            f"Error sending websocket event {action} for {template_name}: {str(e)}",
            exc_info=True,
        )
        _notify_control_not_sent(
            template_name,
            action,
            "The overlay didn't change.",
            f"{event_name}: {type(e).__name__}: {e}",
        )


def show_debug_info():
    """Show debug information about registered handlers"""
    try:
        if web_engine.web_engine_instance:
            handlers = web_engine.web_engine_instance.get_registered_dynamic_handlers()

            with ui.dialog() as dialog, ui.card().classes("w-96"):
                ui.label("Registered Dynamic Handlers").classes("text-h6 mb-4")

                if handlers:
                    ui.label(f"Total handlers: {len(handlers)}").classes("mb-2")

                    with ui.scroll_area().classes("h-64 w-full"):
                        for handler in handlers:
                            ui.label(f"• {handler}").classes("text-sm secondary-text")
                else:
                    ui.label("No dynamic handlers registered").classes("secondary-text")

                with ui.row().classes("w-full justify-end mt-4"):
                    close_btn = ui.button("Close", on_click=dialog.close).classes(
                        "btn-cancel"
                    )
                    apply_flat_btn_props(close_btn)

            dialog.open()
        else:
            notify(
                "",
                type="warning",
                source="overlay_server",
                title="The overlay server isn't running yet",
                hint="Wait for it to start (see the footer), then try again.",
            )
    except Exception as e:
        logger.error(f"Error showing debug info: {str(e)}", exc_info=True)
        notify(
            "",
            type="error",
            source="source_controls",
            title="Couldn't show the handler list",
            hint="Try again in a moment.",
            details=f"{type(e).__name__}: {e}",
        )

#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024 Mycelian

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

import logging

from nicegui import ui

from .. import template_config_parser, web_engine
from ..notification_engine import notify
from ..ui_buttons import apply_flat_btn_props, outline_button, themed_control_button
from ..ui_form_controls import form_input, form_number
from ..ui_timer import layout_schedule

logger = logging.getLogger(__name__)

# Global reference to the source controls container
source_controls_container = None

# Inner control grid columns — keep in sync with OBS .controls-grid (repeat(2, ...))
_INNER_CONTROL_COLUMNS = 2

# Input types span full card width; buttons stay in the 2-column grid
_INPUT_CONTROL_TYPES = frozenset({"text_input", "number_input", "slider", "toggle"})


def _resolve_button_text(element: dict) -> str:
    if element.get("button_text"):
        return str(element["button_text"])
    if element.get("label"):
        return str(element["label"])
    action = str(element.get("action") or "").strip()
    if action:
        return action.replace("_", " ").title()
    return "Run"


def _template_sort_key(item: tuple) -> tuple:
    """Sort by control count ascending — matches OBS renderControls() sort."""
    name, config = item
    return (len(config.get("elements", [])), name)


def _masonry_column_count(card_count: int) -> int:
    """How many flex columns to split cards into (~3 cards per column)."""
    if card_count <= 1:
        return 1
    return max(1, min(card_count, (card_count + 2) // 3))


def _estimate_card_height(config: dict) -> float:
    """Relative vertical weight for column balancing (not pixels)."""
    elements = config.get("elements", [])
    height = 1.2  # card chrome + header
    button_count = 0
    for element in elements:
        etype = element.get("type", "")
        if etype in ("button", "spin_control"):
            button_count += 1
        elif etype == "counter_control":
            height += 1.15 if element.get("target_counter_id") else 0.95
        elif etype in ("text_input", "number_input"):
            height += 1.15
        elif etype in ("slider", "toggle"):
            height += 1.0
        else:
            height += 0.8
    if button_count:
        height += ((button_count + 1) // 2) * 0.55
    return height


def _distribute_to_columns(templates: list) -> list[list]:
    """Column-first fill by estimated height; largest card alone in the rightmost column."""
    n = len(templates)
    if n == 0:
        return []

    n_cols = _masonry_column_count(n)
    if n_cols <= 1 or n == 1:
        return [list(templates)]

    # Sorted ascending — last card is largest; keep it in its own rightmost column.
    body = templates[:-1]
    tail = [templates[-1]]
    body_cols = max(1, n_cols - 1)
    columns = _distribute_balanced(body, body_cols)
    columns.append(tail)
    return columns


def _distribute_balanced(templates: list, n_cols: int) -> list[list]:
    """Stack templates into n_cols left-to-right, advancing when height budget is met."""
    columns: list[list] = [[] for _ in range(n_cols)]
    if not templates:
        return columns

    weights = [_estimate_card_height(cfg) for _, cfg in templates]
    col_idx = 0
    col_height = 0.0
    n = len(templates)

    for i, item in enumerate(templates):
        columns[col_idx].append(item)
        col_height += weights[i]

        if col_idx >= n_cols - 1:
            continue

        remaining_cards = n - i - 1
        remaining_cols = n_cols - col_idx - 1
        if remaining_cards < remaining_cols:
            continue

        remaining_weight = sum(weights[i + 1 :])
        target = remaining_weight / remaining_cols

        if col_height >= target:
            col_idx += 1
            col_height = 0.0

    return columns


def _counter_tooltip(label: str, description: str) -> str:
    return description or label or "Increment or decrement the counter"


def create_source_controls_tab():
    """Create the Source Controls tab UI"""
    global source_controls_container

    with ui.element("div").classes(
        "source-controls-tab content-section w-full h-full flex flex-col relative self-stretch"
    ):
        with ui.column().classes("w-full gap-2 p-3 flex-none"):
            with ui.column().classes("w-full gap-1"):
                ui.label("Source Controls").classes("sc-header-title")

                with ui.row().classes("w-full items-center justify-between"):
                    ui.label("Interactive controls for your templates").classes(
                        "text-xs opacity-75"
                    )
                    outline_button(
                        "Refresh",
                        refresh_source_controls,
                        icon="refresh",
                        extra_classes="btn-primary text-xs px-2 py-1",
                    )

        with ui.element("div").classes("grow overflow-hidden min-h-0 w-full"):
            with ui.scroll_area().classes("source-controls-scroll w-full h-full"):
                source_controls_container = ui.element("div").classes(
                    "source-controls-masonry w-full"
                )

        load_source_controls()


def load_source_controls():
    """Load and display source controls from template configs"""
    global source_controls_container

    if source_controls_container is None:
        logger.error("Source controls container not initialized")
        return

    source_controls_container.clear()

    if web_engine.web_engine_instance:
        try:
            web_engine.web_engine_instance.register_additional_control_handlers()
            logger.debug("Registered additional control handlers")
        except Exception as e:
            logger.error(
                f"Error registering additional control handlers: {str(e)}",
                exc_info=True,
            )

    config_parser = template_config_parser.TemplateConfigParser()
    config_files = config_parser.get_config_files()

    if not config_files:
        with source_controls_container:
            with ui.column().classes("w-full h-full flex flex-col gap-4 p-4"):
                ui.label("No template configurations found").classes("secondary-text")
        return

    all_template_controls = {}

    for config_name in sorted(config_files):
        try:
            config = config_parser.load_config(
                config_name, include_dynamic_controls=True
            )

            dynamic_controls = None
            if isinstance(config, dict):
                if "dynamic_controls" in config:
                    dynamic_controls = config["dynamic_controls"]
                elif "elements" in config:
                    elements = config["elements"]
                    if isinstance(elements, list):
                        control_elements = [
                            elem
                            for elem in elements
                            if isinstance(elem, dict)
                            and elem.get("type")
                            in [
                                "button",
                                "slider",
                                "toggle",
                                "counter_control",
                                "spin_control",
                                "text_input",
                                "number_input",
                            ]
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
                all_template_controls[config_name] = dynamic_controls

        except Exception as e:
            logger.error(
                f"Error processing config {config_name}: {str(e)}", exc_info=True
            )

    with source_controls_container:
        if not all_template_controls:
            with ui.column().classes("w-full h-full flex flex-col gap-2 p-2"):
                ui.label(
                    "No dynamic controls found in template configurations"
                ).classes("secondary-text")
                ui.label(
                    "Add a 'dynamic_controls' section to your template config files to enable interactive controls"
                ).classes("text-caption muted-text mt-2")
                ui.label(
                    "Note: Both visible and hidden templates are checked for controls"
                ).classes("text-caption muted-text mt-1")
        else:
            sorted_templates = sorted(
                all_template_controls.items(), key=_template_sort_key
            )
            columns = _distribute_to_columns(sorted_templates)
            for col_items in columns:
                with ui.element("div").classes("sc-masonry-col"):
                    for template_name, controls_config in col_items:
                        create_template_control_section(
                            template_name, controls_config
                        )


def create_template_control_section(template_name, controls_config):
    """Create a control section for a specific template"""

    config_parser = template_config_parser.TemplateConfigParser()
    is_hidden = config_parser.is_config_hidden(template_name)

    elements = controls_config.get("elements", [])

    with ui.card().props("flat").classes(
        "content-card control-card source-controls-template-card w-full"
    ):
        with ui.column().classes("w-full gap-2"):
            with ui.row().classes("w-full items-center justify-between mb-1"):
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"{template_name.title()} Controls").classes(
                        "text-sm font-semibold"
                    )
                    if is_hidden:
                        ui.badge("Hidden", color="orange").classes("text-xs")

                ui.badge(
                    f"{len(elements)} controls",
                    color="primary",
                ).classes("sc-control-count-badge text-xs")

            grouped_controls = group_controls_by_type(elements)

            for group_name, group_elements in grouped_controls.items():
                if group_name != "Other":
                    ui.label(group_name).classes("sc-group-title")

                with ui.element("div").classes("sc-controls-grid w-full"):
                    for element in group_elements:
                        if not isinstance(element, dict):
                            continue
                        create_control_element(
                            template_name, element, _INNER_CONTROL_COLUMNS
                        )


def group_controls_by_type(elements):
    """Group control elements by their type"""
    groups = {}

    for element in elements:
        element_type = element.get("type", "unknown")

        if element_type == "button":
            group_name = "Actions"
        elif element_type in ["counter_control", "number_input"]:
            group_name = "Counters & Numbers"
        elif element_type in ["text_input"]:
            group_name = "Text Input"
        elif element_type in ["slider", "toggle"]:
            group_name = "Adjustments"
        elif element_type == "spin_control":
            group_name = "Special Controls"
        else:
            group_name = "Other"

        if group_name not in groups:
            groups[group_name] = []

        groups[group_name].append(element)

    return groups


def create_control_element(template_name, element, grid_columns: int):
    """Create a single control element inside a ui.grid cell."""

    element_type = element.get("type", "unknown")
    element_id = element.get("id", "unknown")
    label = element.get("label", element_id)
    description = element.get("description", "")

    compact_types = ("button", "counter_control", "spin_control")
    span_all = (
        element_type == "counter_control"
        or element_type in _INPUT_CONTROL_TYPES
        or grid_columns <= 1
    )
    wrap_classes = "sc-control-cell w-full"
    if span_all:
        wrap_classes += " sc-grid-span-all"

    if element_type in compact_types:
        with ui.element("div").classes(wrap_classes):
            if element_type == "button":
                create_button_control(template_name, element, description)
            elif element_type == "counter_control":
                create_counter_control(template_name, element, label, description)
            elif element_type == "spin_control":
                create_spin_control(template_name, element, description)
        return

    with ui.element("div").classes(f"sc-control-input-wrap {wrap_classes}"):
        with ui.column().classes("w-full gap-1"):
            ui.label(label).classes("text-xs font-medium")
            if element_type == "slider":
                create_slider_control(template_name, element, description or label)
            elif element_type == "toggle":
                create_toggle_control(template_name, element, description or label)
            elif element_type == "text_input":
                create_text_input_control(template_name, element, description or label)
            elif element_type == "number_input":
                create_number_input_control(template_name, element, description or label)
            else:
                ui.label(f"Unknown control type: {element_type}").classes(
                    "text-red-400 text-xs"
                )


def create_button_control(template_name, element, description: str = ""):
    """Create a button control"""
    button_text = _resolve_button_text(element)
    action = element.get("action", "")

    def handle_button_click():
        send_websocket_event(template_name, action, element.get("data", {}))

    btn_class = (
        "btn-warning w-full text-xs py-1"
        if action == "spin"
        else "btn-primary w-full text-xs py-1"
    )
    btn = themed_control_button(
        button_text, handle_button_click, extra_classes=btn_class, dense=True
    )
    if description:
        btn.tooltip(description).classes("bg-theme-surface")


def create_slider_control(template_name, element, tooltip: str = ""):
    """Create a slider control"""
    min_val = element.get("min", 0)
    max_val = element.get("max", 100)
    value = element.get("value", min_val)
    step = element.get("step", 1)
    action = element.get("action", "")

    def handle_slider_change(e):
        send_websocket_event(template_name, action, {"value": e.value})

    slider = ui.slider(
        min=min_val, max=max_val, value=value, step=step, on_change=handle_slider_change
    ).classes("w-full")
    if tooltip:
        slider.tooltip(tooltip).classes("bg-theme-surface")


def create_toggle_control(template_name, element, tooltip: str = ""):
    """Create a toggle control"""
    value = element.get("value", False)
    action = element.get("action", "")

    def handle_toggle_change(e):
        send_websocket_event(template_name, action, {"enabled": e.value})

    sw = ui.switch(value=value, on_change=handle_toggle_change)
    if tooltip:
        sw.tooltip(tooltip).classes("bg-theme-surface")


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


def create_counter_control(template_name, element, label: str = "", description: str = ""):
    """Create counter increment/decrement controls"""
    tooltip_text = _counter_tooltip(label, description)

    if element.get("target_counter_id"):
        _create_spore_counter_control(template_name, element, tooltip_text)
        return

    action_increment = element.get("action_increment", "counter_increment")
    action_decrement = element.get("action_decrement", "counter_decrement")
    action_reset = element.get("action_reset", "counter_reset")

    def increment():
        send_websocket_event(template_name, action_increment, {})

    def decrement():
        send_websocket_event(template_name, action_decrement, {})

    def reset():
        send_websocket_event(template_name, action_reset, {})

    counter_row = ui.element("div").classes("sc-counter-row")
    counter_row.tooltip(tooltip_text).classes("bg-theme-surface")
    with counter_row:
        themed_control_button(
            "-", decrement, extra_classes="btn-danger grow text-xs py-1", dense=True
        )
        themed_control_button(
            "+", increment, extra_classes="btn-success grow text-xs py-1", dense=True
        )
        themed_control_button(
            "Reset", reset, extra_classes="btn-cancel grow text-xs py-1", dense=True
        )


def _create_spore_counter_control(template_name, element, tooltip_text: str):
    """Spore Studio counter_control: amount field plus increment/decrement/reset."""
    action = element.get("action", "counter_adjust")
    step = _counter_step_value(element)
    amount_input = None

    def current_amount() -> int:
        if amount_input is None:
            return step
        try:
            return max(1, int(amount_input.value))
        except (TypeError, ValueError):
            return step

    def increment():
        send_websocket_event(
            template_name, action, _spore_counter_payload(element, "increment", current_amount())
        )

    def decrement():
        send_websocket_event(
            template_name, action, _spore_counter_payload(element, "decrement", current_amount())
        )

    def reset():
        send_websocket_event(
            template_name, action, _spore_counter_payload(element, "reset", current_amount())
        )

    counter_row = ui.element("div").classes("sc-counter-row")
    counter_row.tooltip(tooltip_text).classes("bg-theme-surface")
    with counter_row:
        amount_input = form_number(
            tooltip="Amount to increment or decrement",
            value=step,
            min=1,
            max=999999999,
            classes="sc-stretch-field grow text-xs",
        )
        themed_control_button(
            "-", decrement, extra_classes="btn-danger grow text-xs py-1", dense=True
        )
        themed_control_button(
            "+", increment, extra_classes="btn-success grow text-xs py-1", dense=True
        )
        themed_control_button(
            "Reset", reset, extra_classes="btn-cancel grow text-xs py-1", dense=True
        )


def create_spin_control(template_name, element, description: str = ""):
    """Create spin control for roulette-like templates"""
    action = element.get("action", "spin")

    def handle_spin():
        send_websocket_event(template_name, action, {})

    spin_btn = themed_control_button(
        "Spin",
        handle_spin,
        extra_classes="btn-warning w-full text-xs py-1",
        dense=True,
    )
    tip = description or element.get("label") or ""
    if tip:
        spin_btn.tooltip(tip).classes("bg-theme-surface")


def create_text_input_control(template_name, element, tooltip: str = ""):
    """Create text input control"""
    placeholder = element.get("placeholder", "")
    action = element.get("action", "")
    emit_timer: dict = {"timer": None}

    def schedule_emit_current_text(_e=None):
        """Emit on next UI tick so text_input.value matches the latest keystroke."""
        existing = emit_timer["timer"]
        if existing is not None:
            existing.active = False

        def emit_now():
            emit_timer["timer"] = None
            value = text_input.value
            text_payload = "" if value is None else str(value)
            send_websocket_event(
                template_name,
                action,
                {"text": text_payload},
            )

        emit_timer["timer"] = layout_schedule(0.0, emit_now, once=True)

    text_input = form_input(
        tooltip=tooltip or placeholder or "Send text to the template control",
        placeholder=placeholder,
        classes="sc-stretch-field w-full text-xs",
        on_change=schedule_emit_current_text,
    )
    text_input.props("debounce=0")


def create_number_input_control(template_name, element, tooltip: str = ""):
    """Create number input control"""
    min_val = element.get("min", 0)
    max_val = element.get("max", 999999)
    value = element.get("value", 0)
    action = element.get("action", "")

    def handle_number_change(e):
        send_websocket_event(template_name, action, {"value": e.value})

    form_number(
        tooltip=tooltip or "Numeric value sent to the template control",
        value=value,
        min=min_val,
        max=max_val,
        classes="sc-stretch-field w-full text-xs",
        on_change=handle_number_change,
    )


def send_websocket_event(template_name, action, data):
    """Send websocket event to overlay clients (same delivery path as OBS dock relay)."""
    event_name = f"{template_name}_{action}"
    try:
        engine = web_engine.web_engine_instance
        if not engine:
            logger.error("Web engine not available for sending websocket events")
            return
        engine.emit_template_control_event(template_name, action, data)
        logger.debug("Sent websocket event: %s with data: %s", event_name, data)
    except Exception as e:
        logger.error(
            f"Error sending websocket event {action} for {template_name}: {str(e)}",
            exc_info=True,
        )


def refresh_source_controls():
    """Refresh the source controls display"""
    load_source_controls()


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
            notify("Web engine not available", type="negative")
    except Exception as e:
        logger.error(f"Error showing debug info: {str(e)}", exc_info=True)
        notify(f"Error: {str(e)}", type="negative")

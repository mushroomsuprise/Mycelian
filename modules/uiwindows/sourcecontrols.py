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

import json
import logging
import os

from nicegui import ui

from .. import template_config_parser, web_engine
from ..notification_engine import notify
from ..ui_buttons import apply_flat_btn_props
from ..ui_form_controls import form_input, form_number
from ..ui_timer import layout_schedule

logger = logging.getLogger(__name__)

# Global reference to the source controls container
source_controls_container = None

# Add custom CSS for animations and styling
CUSTOM_CSS = """
.fade-in {
    animation: fadeIn 0.3s ease-in-out;
}

.scale-in {
    animation: scaleIn 0.2s ease-out;
}

.slide-in {
    animation: slideIn 0.3s ease-out;
}

@keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
}

@keyframes scaleIn {
    from { transform: scale(0.95); opacity: 0; }
    to { transform: scale(1); opacity: 1; }
}

@keyframes slideIn {
    from { transform: translateY(-10px); opacity: 0; }
    to { transform: translateY(0); opacity: 1; }
}

.control-card {
    transition: all 0.2s ease-in-out;
    background: var(--color-bg-surface);
}

.control-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 6px -1px var(--color-bg-overlay);
}

.control-group {
    transition: all 0.2s ease;
}

.control-group:hover {
    background-color: var(--color-hover-overlay);
}

.control-button {
    transition: all 0.2s ease;
}

.control-button:hover {
    transform: translateY(-2px);
    opacity: 0.9;
}
"""

def create_source_controls_tab():
    """Create the Source Controls tab UI"""
    global source_controls_container

    # Add custom CSS to the page
    ui.add_head_html(f"<style>{CUSTOM_CSS}</style>", shared=True)

    # Create a card for the entire tab content with flex layout
    with ui.element("div").classes("content-section w-full h-full flex flex-col relative"):
        # Compact header section - much smaller
        with ui.column().classes("w-full gap-2 p-3 flex-none"):
            # Header with title and description/refresh on separate rows
            with ui.column().classes("w-full gap-1"):
                # Title row
                ui.label("Source Controls").classes(
                    "text-lg font-medium fade-in text-theme-primary"
                )

                # Description and refresh button row
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(
                        "Interactive controls for your templates (includes hidden templates)"
                    ).classes("text-xs opacity-75 fade-in")
                    refresh_btn = ui.button(
                        icon="refresh", text="Refresh", on_click=refresh_source_controls
                    ).classes(
                        "control-button btn-primary text-xs px-2 py-1"
                    )
                    apply_flat_btn_props(refresh_btn, dense=True)

        # Create a container for the controls - flexible height
        with ui.element("div").classes("grow overflow-hidden"):
            with ui.scroll_area().classes("w-full h-full"):
                source_controls_container = ui.element("div").classes("w-full")

        # Load and display controls
        load_source_controls()


def load_source_controls():
    """Load and display source controls from template configs"""
    global source_controls_container

    if source_controls_container is None:
        logger.error("Source controls container not initialized")
        return

    # Clear existing controls
    source_controls_container.clear()

    # Register additional websocket handlers for dynamic controls
    if web_engine.web_engine_instance:
        try:
            web_engine.web_engine_instance.register_additional_control_handlers()
            logger.debug("Registered additional control handlers")
        except Exception as e:
            logger.error(
                f"Error registering additional control handlers: {str(e)}",
                exc_info=True,
            )

    # Initialize template config parser
    config_parser = template_config_parser.TemplateConfigParser()

    # Get all config files
    config_files = config_parser.get_config_files()

    if not config_files:
        with source_controls_container:
            with ui.column().classes("w-full h-full flex flex-col gap-4 p-4"):
                ui.label("No template configurations found").classes(
                    "secondary-text fade-in"
                )
        return

    # Process each config file and collect all controls
    all_template_controls = {}

    for config_name in sorted(config_files):
        try:
            config = config_parser.load_config(
                config_name, include_dynamic_controls=True
            )

            # Look for dynamic_controls category
            dynamic_controls = None
            if isinstance(config, dict):
                # Check if there's a dynamic_controls section
                if "dynamic_controls" in config:
                    dynamic_controls = config["dynamic_controls"]
                # Also check in elements for backwards compatibility
                elif "elements" in config:
                    # Filter elements that might be dynamic controls
                    elements = config["elements"]
                    if isinstance(elements, list):
                        # Look for elements that have control-related properties AND an action property
                        # This prevents template configuration elements (like styling sliders) from being treated as dynamic controls
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

    # Render all controls
    with source_controls_container:
        with ui.column().classes("w-full h-full flex flex-col gap-2 p-2"):
            if not all_template_controls:
                ui.label(
                    "No dynamic controls found in template configurations"
                ).classes("secondary-text fade-in")
                ui.label(
                    "Add a 'dynamic_controls' section to your template config files to enable interactive controls"
                ).classes("text-caption muted-text mt-2 fade-in")
                ui.label(
                    "Note: Both visible and hidden templates are checked for controls"
                ).classes("text-caption muted-text mt-1 fade-in")
            else:
                # Create template control sections
                for template_name, controls_config in all_template_controls.items():
                    create_template_control_section(template_name, controls_config)


def create_template_control_section(template_name, controls_config):
    """Create a control section for a specific template"""

    # Check if this template is hidden
    config_parser = template_config_parser.TemplateConfigParser()
    is_hidden = config_parser.is_config_hidden(template_name)

    with ui.element("div").classes("content-card mb-3 w-full control-card"):
        with ui.column().classes("w-full gap-2"):
            # Template header - more compact
            with ui.row().classes("w-full items-center justify-between mb-2"):
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"{template_name.title()} Controls").classes(
                        "text-base font-medium fade-in"
                    )
                    if is_hidden:
                        ui.badge("Hidden", color="orange").classes("scale-in text-xs")

                ui.badge(
                    f"{len(controls_config.get('elements', []))} controls",
                    color="primary",
                ).classes("scale-in text-xs")

            # Group controls by type
            elements = controls_config.get("elements", [])
            grouped_controls = group_controls_by_type(elements)

            # Create controls for each group
            for group_name, group_elements in grouped_controls.items():
                if group_name != "Other":
                    ui.label(group_name).classes("text-xs font-medium mb-1 opacity-75")

                # Dynamically set columns based on number of elements, max 3
                num_elements = len(group_elements)
                grid_cols = max(1, min(num_elements, 3))

                # Create UI elements for each control in the group - tighter spacing
                with ui.grid(columns=grid_cols).classes("w-full gap-x-2 gap-y-1 mb-2"):
                    for element in group_elements:
                        if not isinstance(element, dict):
                            continue
                        create_control_element(template_name, element)


def group_controls_by_type(elements):
    """Group control elements by their type"""
    groups = {}

    for element in elements:
        element_type = element.get("type", "unknown")

        # Determine group based on control type
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


def create_control_element(template_name, element):
    """Create a single control element"""

    element_type = element.get("type", "unknown")
    element_id = element.get("id", "unknown")
    label = element.get("label", element_id)
    description = element.get("description", "")

    # The main container for the element - more compact
    with ui.column().classes("w-full gap-1 control-group py-1 px-2 rounded"):
        with ui.column().classes("w-full gap-1"):
            # Label and description - smaller fonts
            ui.label(label).classes("text-xs font-medium")
            if description:
                ui.label(description).classes("text-xs opacity-50")

            # Control element based on type
            if element_type == "button":
                create_button_control(template_name, element)
            elif element_type == "slider":
                create_slider_control(template_name, element)
            elif element_type == "toggle":
                create_toggle_control(template_name, element)
            elif element_type == "counter_control":
                create_counter_control(template_name, element)
            elif element_type == "spin_control":
                create_spin_control(template_name, element)
            elif element_type == "text_input":
                create_text_input_control(template_name, element)
            elif element_type == "number_input":
                create_number_input_control(template_name, element)
            else:
                ui.label(f"Unknown control type: {element_type}").classes(
                    "text-red-400 text-xs"
                )


def create_button_control(template_name, element):
    """Create a button control"""
    button_text = element.get("button_text", "Action")
    action = element.get("action", "")

    def handle_button_click():
        send_websocket_event(template_name, action, element.get("data", {}))

    # Use different colors based on action type
    button_classes = "w-full text-xs py-1"
    if action == "spin":
        button_classes += " btn-warning"
    elif "reset" in action.lower() or "clear" in action.lower():
        button_classes += " btn-danger"
    else:
        button_classes += " btn-primary"

    btn = ui.button(button_text, on_click=handle_button_click).classes(button_classes)
    apply_flat_btn_props(btn)


def create_slider_control(template_name, element):
    """Create a slider control"""
    min_val = element.get("min", 0)
    max_val = element.get("max", 100)
    value = element.get("value", min_val)
    step = element.get("step", 1)
    action = element.get("action", "")

    def handle_slider_change(e):
        send_websocket_event(template_name, action, {"value": e.value})

    ui.slider(
        min=min_val, max=max_val, value=value, step=step, on_change=handle_slider_change
    ).classes("w-full")


def create_toggle_control(template_name, element):
    """Create a toggle control"""
    value = element.get("value", False)
    action = element.get("action", "")

    def handle_toggle_change(e):
        send_websocket_event(template_name, action, {"enabled": e.value})

    ui.switch(value=value, on_change=handle_toggle_change)


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


def create_counter_control(template_name, element):
    """Create counter increment/decrement controls"""
    if element.get("target_counter_id"):
        _create_spore_counter_control(template_name, element)
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

    with ui.row().classes("gap-1 w-full"):
        dec_btn = ui.button("-", on_click=decrement).classes(
            "w-6 h-6 btn-danger text-xs"
        )
        apply_flat_btn_props(dec_btn, dense=True)
        inc_btn = ui.button("+", on_click=increment).classes(
            "w-6 h-6 btn-success text-xs"
        )
        apply_flat_btn_props(inc_btn, dense=True)
        reset_btn = ui.button("Reset", on_click=reset).classes(
            "grow btn-cancel text-xs py-1"
        )
        apply_flat_btn_props(reset_btn)


def _create_spore_counter_control(template_name, element):
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

    with ui.row().classes("gap-1 w-full items-center"):
        amount_input = form_number(
            tooltip="Amount to increment or decrement",
            value=step,
            min=1,
            max=999999999,
            classes="grow text-xs",
        )
        dec_btn = ui.button("-", on_click=decrement).classes(
            "w-6 h-6 btn-danger text-xs"
        )
        apply_flat_btn_props(dec_btn, dense=True)
        inc_btn = ui.button("+", on_click=increment).classes(
            "w-6 h-6 btn-success text-xs"
        )
        apply_flat_btn_props(inc_btn, dense=True)
        reset_btn = ui.button("Reset", on_click=reset).classes(
            "btn-cancel text-xs py-1"
        )
        apply_flat_btn_props(reset_btn)


def create_spin_control(template_name, element):
    """Create spin control for roulette-like templates"""
    action = element.get("action", "spin")

    def handle_spin():
        send_websocket_event(template_name, action, {})

    spin_btn = ui.button("Spin", on_click=handle_spin).classes(
        "btn-warning w-full text-xs py-1"
    )
    apply_flat_btn_props(spin_btn)


def create_text_input_control(template_name, element):
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
        tooltip=placeholder or "Send text to the template control",
        placeholder=placeholder,
        classes="w-full text-xs",
        on_change=schedule_emit_current_text,
    )
    text_input.props("debounce=0")


def create_number_input_control(template_name, element):
    """Create number input control"""
    min_val = element.get("min", 0)
    max_val = element.get("max", 999999)
    value = element.get("value", 0)
    action = element.get("action", "")

    def handle_number_change(e):
        send_websocket_event(template_name, action, {"value": e.value})

    form_number(
        tooltip="Numeric value sent to the template control",
        value=value,
        min=min_val,
        max=max_val,
        classes="w-full text-xs",
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

            # Create a dialog with debug info
            with ui.dialog() as dialog, ui.card().classes("w-96"):
                ui.label("Registered Dynamic Handlers").classes(
                    "text-h6 mb-4"
                )

                if handlers:
                    ui.label(f"Total handlers: {len(handlers)}").classes(
                        "mb-2"
                    )

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

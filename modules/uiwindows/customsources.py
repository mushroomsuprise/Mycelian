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
import os
import sys
from typing import Any, Dict

from nicegui import app, ui

# Use proper relative import for template_config_parser
from ..template_config_parser import TemplateConfigParser

logger = logging.getLogger(__name__)

# Global dictionary to store form data for each config
form_data_store = {}

# Add a dictionary to store original values of fields
original_values = {}

# Add global dictionary to store UI elements
element_ui_map = {}

# Add global variable to store current search term
current_search_term = ""

# Add global variables for re-rendering
current_config_name = ""
current_container = None

# Add global dictionary to store expansion elements for dynamic title updates
roulette_expansions = {}

# Add custom CSS for animations and styling (removed pulsing animations)
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

.config-card {
    transition: all 0.2s ease-in-out;
    background: var(--color-bg-surface);
}

.config-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 6px -1px var(--color-bg-overlay);
}

.color-preview {
    width: 24px;
    height: 24px;
    border-radius: 4px;
    margin-right: 8px;
    transition: transform 0.2s ease;
}

.color-preview:hover {
    transform: scale(1.1);
}

.color-swatch {
    width: 32px;
    height: 32px;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.2s ease;
    border: 2px solid transparent;
    position: relative;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
}

.color-swatch:hover {
    transform: scale(1.1);
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.3);
}

.color-swatch.selected {
    border-color: var(--color-primary) !important;
    box-shadow: 0 0 0 2px var(--color-focus-ring) !important;
    transform: scale(1.05) !important;
}

.color-grid {
    display: grid;
    grid-template-columns: repeat(8, 1fr);
    gap: 8px;
    padding: 8px;
    background-color: var(--color-bg-surface);
    border-radius: 8px;
    border: 1px solid var(--color-border-default);
}

.form-group {
    transition: all 0.2s ease;
}

.form-group:hover {
    /* transform: translateX(4px); Removed for grid layout */
    background-color: var(--color-hover-overlay); /* Subtle hover for grid cells */
}

.control-button {
    transition: all 0.2s ease;
}

.control-button:hover {
    transform: translateY(-2px);
    opacity: 0.9;
}

.description-text {
    transition: opacity 0.2s ease;
}

.form-group:hover .description-text {
    opacity: 1 !important;
}
"""


def create_custom_sources_tab():
    """
    Create the custom sources tab content using NiceGUI

    Returns:
        None
    """
    # Add custom CSS to the page
    ui.add_head_html(f"<style>{CUSTOM_CSS}</style>")

    # Initialize the config parser
    config_dir = "templates/template_configs"
    config_parser = TemplateConfigParser(config_dir)

    # Create a card for the entire tab content with flex layout
    with ui.element("div").classes(
        "content-section w-full h-full flex flex-col relative"
    ):
        # Help icon in top right corner
        with ui.row().classes("absolute top-1 right-1 z-20"):
            from ..help_system.contextual_help import help_button

            help_button(topic_id="templates_intro", tooltip="Templates help", size="sm")

        # Header section with title and controls - fixed height
        with ui.column().classes("w-full gap-4 p-4 flex-none"):
            # Header section with title and controls
            with ui.row().classes("w-full items-center justify-between mb-6"):
                with ui.column().classes("gap-1"):
                    ui.label("Template Configurations").classes(
                        "text-xl font-medium fade-in"
                    )
                    ui.label(
                        "Manage your template settings and configurations"
                    ).classes("text-sm opacity-75 fade-in")

                # Configuration selector and refresh button
                with ui.row().classes("items-center gap-2 slide-in"):
                    config_select = ui.select(
                        options=[],
                        label="Select Configuration",
                        on_change=lambda e: on_config_selected(
                            e, config_parser, config_container
                        ),
                    ).classes("w-48 bg-theme-base")

                    ui.button(
                        icon="refresh",
                        text="",
                        on_click=lambda: load_config_files(
                            config_parser, config_select, config_container
                        ),
                    ).classes("control-button p-2 bg-theme-base")

            # Search input
            with ui.row().classes("w-full items-center gap-2"):
                search_input = (
                    ui.input(
                        label="Search properties",
                        placeholder="Type to search by property label...",
                        on_change=lambda e: on_search_changed(
                            e, config_parser, config_select, config_container
                        ),
                    )
                    .classes("flex-grow bg-theme-base")
                    .props("clearable")
                )
                search_input.props('prepend-icon="search"')

            # Action buttons
            with ui.row().classes("w-full items-center gap-2"):
                with ui.row().classes("gap-2"):
                    ui.button(
                        icon="add",
                        text="New",
                        on_click=lambda: create_new_config(
                            config_parser, config_select, config_container
                        ),
                    ).classes("control-button btn-primary")

                    ui.button(
                        icon="delete",
                        text="Delete",
                        on_click=lambda: delete_config(
                            config_parser, config_select, config_container
                        ),
                    ).classes("control-button btn-danger")

                ui.element("div").classes("flex-grow")

                with ui.row().classes("gap-2"):
                    ui.button(
                        icon="restart_alt",
                        text="Reset",
                        on_click=lambda: reset_config(
                            config_parser, config_select, config_container
                        ),
                    ).classes("control-button bg-theme-base")

                    ui.button(
                        icon="save",
                        text="Save",
                        on_click=lambda: save_config(
                            config_parser, config_select, config_container
                        ),
                    ).classes("control-button btn-success")

        # Create a container for the config editor - flexible height
        with ui.element("div").classes("flex-grow overflow-hidden"):
            config_container = ui.element("div").classes("w-full h-full")

            # Help text
            with ui.expansion("Help").classes("mt-4 opacity-75"):
                help_text = (
                    "This tab allows you to manage template configurations. Each configuration is stored as a "
                    "JSON file in the templates/template_configs directory. You can create, edit, and delete "
                    "configuration files. Note: Templates marked as 'hidden' will not appear here but can still "
                    "be controlled through the Source Controls tab."
                )
                ui.label(help_text).classes("text-sm p-2")

            # Load the config files initially
            load_config_files(config_parser, config_select, config_container)


def load_config_files(config_parser, config_select, config_container):
    """Load the config files into the select dropdown"""
    configs = config_parser.get_non_hidden_config_files()

    if configs:
        # Sort configs alphabetically before updating the select options
        configs = sorted(configs)

        # Update the select options
        config_select.options = configs
        config_select.value = configs[0]

        # Load the first config
        render_config_ui(config_parser, configs[0], config_container, "")
    else:
        # Clear the select options
        config_select.options = []
        config_select.value = None

        # Clear the container
        config_container.clear()
        with config_container:
            ui.label("No configuration files found.").classes("text-sm opacity-75")


def on_config_selected(e, config_parser, config_container):
    """Handle config selection"""
    config_name = e.value
    if not config_name:
        return

    # Clear element_ui_map before loading a new config
    element_ui_map.clear()

    # Clear roulette expansions when switching configs
    roulette_expansions.clear()

    # Render the config UI
    render_config_ui(config_parser, config_name, config_container, "")


def on_search_changed(e, config_parser, config_select, config_container):
    """Handle search input changes"""
    global current_search_term
    current_search_term = e.value.lower() if e.value else ""

    # Re-render the current config with the search filter
    config_name = config_select.value
    if config_name:
        render_config_ui(
            config_parser, config_name, config_container, current_search_term
        )


def render_config_ui(config_parser, config_name, container, search_term=""):
    """Render the config as interactive UI elements"""
    # Clear the container
    container.clear()

    # Clear the element_ui_map first
    element_ui_map.clear()

    # Load the config
    config = config_parser.load_config(config_name)

    # Create a form for the config
    with container:
        with ui.column().classes("w-full h-full flex flex-col gap-4 p-4"):
            # Title and description - fixed height section
            with ui.column().classes("flex-none"):
                ui.label(f"Configuration: {config_name}").classes(
                    "text-lg font-medium mb-2 fade-in"
                )

            # Scrollable content area - flexible height
            with ui.scroll_area().classes("w-full flex-grow"):
                # Create a form for the elements
                form_data = {}

                # Filter elements based on search term
                elements = config.get("elements", [])
                if search_term:
                    filtered_elements = []
                    for element in elements:
                        element_label = element.get("label", "").lower()
                        element_description = element.get("description", "").lower()
                        if (
                            search_term in element_label
                            or search_term in element_description
                        ):
                            filtered_elements.append(element)
                    elements = filtered_elements

                # Show search results info
                if search_term:
                    total_elements = len(config.get("elements", []))
                    filtered_count = len(elements)
                    ui.label(
                        f"Showing {filtered_count} of {total_elements} properties"
                    ).classes("text-sm opacity-75 mb-2")

                # Group similar elements together
                grouped_elements = group_config_elements(elements)

                # Special handling for roulette template - make each option collapsible
                if config_name == "roulette":
                    # Create collapsible cards for each option
                    option_groups = {}
                    general_elements = []

                    # Group elements by option number
                    for element in config.get("elements", []):
                        element_id = element.get("id", "")
                        if element_id.startswith("Option"):
                            # Extract option number
                            option_part = element_id[6:]  # Remove "Option" prefix
                            option_num_str = ""
                            for char in option_part:
                                if char.isdigit():
                                    option_num_str += char
                                else:
                                    break
                            if option_num_str:
                                option_num = int(option_num_str)
                                if option_num not in option_groups:
                                    option_groups[option_num] = []
                                option_groups[option_num].append(element)
                        else:
                            general_elements.append(element)

                    # Render general elements first, grouped by separators
                    if general_elements:
                        general_groups = group_config_elements(general_elements)
                        for group_name, group_elements in general_groups.items():
                            # Skip empty groups
                            if not group_elements:
                                continue

                            # Make roulette general categories collapsible too
                            if group_name != "Other":
                                with ui.expansion(group_name).classes(
                                    "content-card mb-1 w-full"
                                ):
                                    # Dynamically set columns based on number of elements, max 3
                                    num_elements = len(group_elements)
                                    grid_cols = max(1, min(num_elements, 3))

                                    # Create UI elements for each general element in the group
                                    with ui.grid(columns=grid_cols).classes(
                                        "w-full gap-x-2 gap-y-px"
                                    ):
                                        for element in group_elements:
                                            if (
                                                search_term
                                                and search_term.lower()
                                                not in element.get("label", "").lower()
                                                and search_term.lower()
                                                not in element.get(
                                                    "description", ""
                                                ).lower()
                                            ):
                                                continue
                                            render_form_element(
                                                element,
                                                form_data,
                                                config_name,
                                                container,
                                                current_search_term,
                                            )
                            else:
                                with ui.expansion("Other Settings").classes(
                                    "content-card mb-1 w-full"
                                ):
                                    # Dynamically set columns based on number of elements, max 3
                                    num_elements = len(group_elements)
                                    grid_cols = max(1, min(num_elements, 3))

                                    # Create UI elements for each general element in the group
                                    with ui.grid(columns=grid_cols).classes(
                                        "w-full gap-x-2 gap-y-px"
                                    ):
                                        for element in group_elements:
                                            if (
                                                search_term
                                                and search_term.lower()
                                                not in element.get("label", "").lower()
                                                and search_term.lower()
                                                not in element.get(
                                                    "description", ""
                                                ).lower()
                                            ):
                                                continue
                                            render_form_element(
                                                element,
                                                form_data,
                                                config_name,
                                                container,
                                                current_search_term,
                                            )

                    # Render each option as a collapsible card
                    for option_num in sorted(option_groups.keys()):
                        option_elements = option_groups[option_num]

                        # Check if this option should be shown based on search
                        if search_term:
                            show_option = False
                            for element in option_elements:
                                if (
                                    search_term.lower()
                                    in element.get("label", "").lower()
                                    or search_term.lower()
                                    in element.get("description", "").lower()
                                ):
                                    show_option = True
                                    break
                            if not show_option:
                                continue

                        # Extract option name from the original config (most reliable)
                        option_name_id = f"Option{option_num}Name"
                        option_name = None
                        for element in config.get("elements", []):
                            if element.get("id") == option_name_id:
                                option_name = element.get(
                                    "value", f"Option {option_num}"
                                )
                                break

                        # Create the expansion title with option name always in parentheses
                        expansion_title = f"Option {option_num}"
                        if option_name:
                            expansion_title += f" ({option_name})"

                        with ui.expansion(expansion_title).classes(
                            "content-card mb-1 w-full"
                        ) as expansion:
                            # Store reference to this expansion for dynamic title updates
                            roulette_expansions[option_num] = expansion
                            # Add data attribute for JavaScript identification
                            expansion.props(f'data-option="{option_num}"')
                            # Create UI elements for this option's elements
                            num_elements = len(option_elements)
                            grid_cols = max(1, min(num_elements, 3))

                            with ui.grid(columns=grid_cols).classes(
                                "w-full gap-x-2 gap-y-px"
                            ):
                                for element in option_elements:
                                    render_form_element(
                                        element,
                                        form_data,
                                        config_name,
                                        container,
                                        current_search_term,
                                    )
                else:
                    # Default grouping behavior for other templates
                    # Create UI elements for each group
                    for group_name, group_elements in grouped_elements.items():
                        # Skip empty groups
                        if not group_elements:
                            continue

                        # Create collapsible categories for all groups
                        if group_name != "Other":
                            with ui.expansion(group_name).classes(
                                "content-card mb-1 w-full"
                            ):
                                # Dynamically set columns based on number of elements, max 3
                                num_elements = len(group_elements)
                                grid_cols = max(1, min(num_elements, 3))

                                # Create UI elements for each config element in the group
                                with ui.grid(columns=grid_cols).classes(
                                    "w-full gap-x-2 gap-y-px"
                                ):
                                    for element in group_elements:
                                        render_form_element(
                                            element,
                                            form_data,
                                            config_name,
                                            container,
                                            current_search_term,
                                        )
                        else:
                            with ui.expansion("Other Settings").classes(
                                "content-card mb-1 w-full"
                            ):
                                # Dynamically set columns based on number of elements, max 3
                                num_elements = len(group_elements)
                                grid_cols = max(1, min(num_elements, 3))

                                # Create UI elements for each config element in the group
                                with ui.grid(columns=grid_cols).classes(
                                    "w-full gap-x-2 gap-y-px"
                                ):
                                    for element in group_elements:
                                        render_form_element(
                                            element,
                                            form_data,
                                            config_name,
                                            container,
                                            current_search_term,
                                        )

                # Show "no results" message if search yielded no results
                if search_term and not elements:
                    with ui.element("div").classes("text-center p-8"):
                        ui.icon("search_off").classes("text-4xl opacity-50 mb-2")
                        ui.label("No properties found").classes("text-lg opacity-75")
                        ui.label(f"No properties match '{search_term}'").classes(
                            "text-sm opacity-50"
                        )

                # Store the form data in the global store
                form_data_store[config_name] = form_data

                # Set a small delay to ensure everything is rendered before initializing
                ui.timer(0.1, lambda: initialize_values(config_name), once=True)


def initialize_values(config_name):
    """Initialize the original values for the current config"""
    # Check if we have form data for this config
    if config_name in form_data_store:
        form_data = form_data_store[config_name]

        # Update original values with current form data
        for element_id, value in form_data.items():
            if element_id in element_ui_map:
                original_values[element_id] = value

                # Ensure element has an ID
                element = element_ui_map[element_id]
                if not element.id:
                    element.id = f"source-{element_id}-{id(element)}"

                logger.debug(
                    f"Set original value for {element_id}: {value}, element ID: {element.id}"
                )

    # Clear any changed styling that might be present
    clear_changed_styling()


def group_config_elements(elements):
    """Group elements by their type or common prefixes"""
    groups: Dict[str, list] = {}
    current_group = "Other"

    for element in elements:
        element_type = element.get("type", "")
        element_label = element.get("label", "")

        # Check if this is a separator - if so, it defines the new group name
        if element_type == "separator":
            current_group = element_label
            if current_group not in groups:
                groups[current_group] = []
            continue

        # Add non-separator elements to the current group
        if current_group not in groups:
            groups[current_group] = []

        groups[current_group].append(element)

    return groups


def handle_number_change(e, element_id, form_data):
    """Handle number input changes"""
    # Update the form data
    update_form_data(form_data, element_id, e.value)


def render_form_element(
    element, form_data, config_name="", container=None, search_term=""
):
    """Render a single form element"""
    element_type = element.get("type", "text")
    element_id = element.get("id", "")
    element_label = element.get("label", element_id)
    element_value = element.get("value", "")
    element_description = element.get("description", "")

    # Store config_name and container for potential re-rendering
    global current_config_name, current_container, current_search_term
    current_config_name = config_name
    current_container = container
    current_search_term = search_term

    # Store original value for later comparison
    original_values[element_id] = element_value

    # CRITICAL: Initialize form_data with the current element value
    # This ensures ALL config values are tracked, not just changed ones
    form_data[element_id] = element_value

    # The main container for the element, designed to fit in a grid cell
    with ui.column().classes("w-full gap-px form-group py-px px-1 rounded"):
        with ui.column().classes("w-full gap-1"):
            # Label row
            ui.label(element_label).classes("text-sm font-medium")

            if element_description:
                ui.label(element_description).classes(
                    "text-xs opacity-50 mb-1 description-text"
                )

            if element_type == "text":
                input_element = ui.input(
                    value=element_value,
                    on_change=lambda _, id=element_id: update_form_data(
                        form_data, id, input_element.value
                    ),
                ).classes("w-full")
                element_ui_map[element_id] = input_element
            elif element_type == "textarea":
                input_element = ui.textarea(
                    value=element_value,
                    on_change=lambda _, id=element_id: update_form_data(
                        form_data, id, input_element.value
                    ),
                ).classes("w-full h-24")
                element_ui_map[element_id] = input_element
            elif element_type == "select":
                options = element.get("options", [])
                display_type = element.get("display", "dropdown")

                if display_type == "color_grid":
                    # Render as a color grid
                    selected_color = element_value

                    # Create a hidden input to store the selected value
                    hidden_input = ui.input(value=selected_color).classes("hidden")
                    element_ui_map[element_id] = hidden_input

                    # Store swatch references for this element
                    swatch_refs = {}

                    # Create the visual color grid
                    with ui.element("div").classes("color-grid"):
                        for color_option in options:
                            # Create a color swatch
                            swatch_classes = "color-swatch"
                            if color_option == selected_color:
                                swatch_classes += " selected"

                            # Handle transparent option differently
                            if color_option == "transparent":
                                # Create a checkerboard pattern for transparent
                                with ui.element("div").classes(
                                    swatch_classes
                                ) as swatch:
                                    swatch.style("""
                                        background: linear-gradient(45deg, #ccc 25%, transparent 25%), 
                                                   linear-gradient(-45deg, #ccc 25%, transparent 25%), 
                                                   linear-gradient(45deg, transparent 75%, #ccc 75%), 
                                                   linear-gradient(-45deg, transparent 75%, #ccc 75%);
                                        background-size: 8px 8px;
                                        background-position: 0 0, 0 4px, 4px -4px, -4px 0px;
                                    """)
                                    # Store reference to this swatch
                                    swatch_refs[color_option] = swatch

                                    swatch.on(
                                        "click",
                                        lambda color=color_option: select_color_from_grid(
                                            color,
                                            element_id,
                                            form_data,
                                            hidden_input,
                                            swatch_refs,
                                        ),
                                    )

                                    # Add tooltip
                                    swatch.tooltip("Transparent")
                            else:
                                with (
                                    ui.element("div")
                                    .classes(swatch_classes)
                                    .style(
                                        f"background-color: {color_option}"
                                    ) as swatch
                                ):
                                    # Store reference to this swatch
                                    swatch_refs[color_option] = swatch

                                    swatch.on(
                                        "click",
                                        lambda color=color_option: select_color_from_grid(
                                            color,
                                            element_id,
                                            form_data,
                                            hidden_input,
                                            swatch_refs,
                                        ),
                                    )

                                    # Add tooltip with color value
                                    swatch.tooltip(color_option)
                else:
                    # Render as normal dropdown
                    input_element = ui.select(
                        options=options,
                        value=element_value,
                        on_change=lambda _, id=element_id: update_form_data(
                            form_data, id, input_element.value
                        ),
                    ).classes("w-full")
                    element_ui_map[element_id] = input_element
            elif element_type == "number":
                min_val = element.get("min", None)
                max_val = element.get("max", None)
                input_element = ui.number(
                    value=element_value,
                    min=min_val,
                    max=max_val,
                    on_change=lambda e, id=element_id: handle_number_change(
                        e, id, form_data
                    ),
                ).classes("w-full")
                element_ui_map[element_id] = input_element
            elif element_type == "slider":
                min_val = element.get("min", 0)
                max_val = element.get("max", 100)
                step_val = element.get("step", 1)

                # Convert element_value to appropriate numeric type
                try:
                    if step_val == int(step_val):
                        # Integer step, convert value to int
                        slider_value = (
                            int(float(element_value))
                            if element_value != ""
                            else min_val
                        )
                    else:
                        # Float step, keep as float
                        slider_value = (
                            float(element_value) if element_value != "" else min_val
                        )
                except (ValueError, TypeError):
                    slider_value = min_val

                # Create a row to show the current value
                with ui.row().classes("w-full items-center gap-2"):
                    # Slider element
                    input_element = ui.slider(
                        min=min_val,
                        max=max_val,
                        step=step_val,
                        value=slider_value,
                        on_change=lambda e, id=element_id: update_form_data(
                            form_data, id, e.value
                        ),
                    ).classes("flex-grow")

                    # Value display
                    value_label = ui.label(str(slider_value)).classes(
                        "text-sm font-mono min-w-[3rem] text-right"
                    )

                    # Update value display when slider changes
                    def update_slider_display(e, label=value_label):
                        label.text = str(e.value)

                    input_element.on("change", update_slider_display)

                element_ui_map[element_id] = input_element
            elif element_type == "checkbox":
                input_element = ui.switch(
                    value=element_value,
                    on_change=lambda _, id=element_id: update_form_data(
                        form_data, id, input_element.value
                    ),
                )
                element_ui_map[element_id] = input_element
                # For checkboxes, add a custom wrapper to make styling work better
                input_element.classes("q-switch")
            elif element_type == "color":
                # Extract current color and transparency values
                is_transparent = element.get("transparent", False)
                base_color = element_value

                if element_value.startswith("rgba"):
                    try:
                        rgba_parts = (
                            element_value.replace("rgba(", "")
                            .replace(")", "")
                            .split(",")
                        )
                        r, g, b = map(int, rgba_parts[:3])
                        alpha = float(rgba_parts[3])
                        is_transparent = alpha < 1.0
                        base_color = f"#{r:02x}{g:02x}{b:02x}"
                    except:
                        base_color = "#ffffff"

                # Create a row for the color picker and transparency toggle
                with ui.row().classes("w-full items-center gap-2"):
                    # Create the transparency switch first
                    transparency_switch = ui.switch(value=is_transparent).props(
                        'label="Transparent"'
                    )
                    transparency_switch.classes("q-switch")

                    # Color input that shows the NiceGUI color picker
                    with ui.column().classes("w-full"):
                        ui.label("Color").classes("text-xs opacity-75 mb-1")
                        color_input = ui.color_input(value=base_color).classes("w-full")
                        element_ui_map[element_id] = color_input

                        # Store references for closure
                        color_ref = color_input
                        trans_ref = transparency_switch

                        # Set up event handlers after both elements are created
                        def handle_color_change():
                            update_color_with_transparency(
                                color_ref.value, trans_ref.value, element_id, form_data
                            )

                        def handle_transparency_change():
                            update_color_with_transparency(
                                color_ref.value, trans_ref.value, element_id, form_data
                            )

                        color_input.on("change", lambda _: handle_color_change())
                        transparency_switch.on(
                            "change", lambda _: handle_transparency_change()
                        )
            else:
                input_element = ui.input(
                    value=element_value,
                    on_change=lambda _, id=element_id: update_form_data(
                        form_data, id, input_element.value
                    ),
                ).classes("w-full")
                element_ui_map[element_id] = input_element

            # Store the initial value in the form data
            form_data[element_id] = element_value


def update_color_with_transparency(color, is_transparent, element_id, form_data):
    """Update the color with transparency in the form data"""
    try:
        if is_transparent:
            # Convert hex to RGBA with 0 alpha
            if color.startswith("#"):
                color = color.lstrip("#")
                if len(color) == 3:
                    color = "".join(c + c for c in color)
                r = int(color[0:2], 16)
                g = int(color[2:4], 16)
                b = int(color[4:6], 16)
                rgba_color = f"rgba({r}, {g}, {b}, 0)"
            elif color.startswith("rgb"):
                # Convert RGB to RGBA with 0 alpha
                rgba_color = f"rgba{color[3:-1]}, 0)"
            else:
                rgba_color = color
        else:
            # Convert to RGB if it's RGBA
            if color.startswith("rgba"):
                rgba_parts = color.replace("rgba(", "").replace(")", "").split(",")
                r, g, b = map(int, rgba_parts[:3])
                rgba_color = f"rgb({r}, {g}, {b})"
            else:
                rgba_color = color

        # Update the form data
        update_form_data(form_data, element_id, rgba_color)
    except Exception as e:
        logger.error(f"Error updating color with transparency: {str(e)}")


def update_roulette_expansion_title(option_num, option_name):
    """Update the title of a roulette option expansion"""
    if option_num in roulette_expansions:
        expansion = roulette_expansions[option_num]
        # Create new title with option name always in parentheses
        new_title = f"Option {option_num}"
        if option_name:
            new_title += f" ({option_name})"

        # Update the expansion title
        try:
            # Try to update the expansion label via JavaScript
            ui.run_javascript(f"""
                const expansion = document.querySelector('[data-option="{option_num}"]');
                if (expansion) {{
                    const label = expansion.querySelector('.q-expansion-item__label');
                    if (label) {{
                        label.textContent = '{new_title}';
                    }}
                }}
            """)
        except Exception as e:
            logger.error(f"Failed to update roulette expansion title: {str(e)}")


def update_form_data(form_data, element_id, value):
    """Update the form data when an element changes"""
    old_value = form_data.get(element_id, "NOT_SET")
    form_data[element_id] = value

    # Log important configuration changes for combobar (check form_data_store keys)
    config_name = None
    for name, data in form_data_store.items():
        if data is form_data:
            config_name = name
            break

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

    # Update roulette expansion titles when option names change
    if (
        config_name == "roulette"
        and element_id.startswith("Option")
        and element_id.endswith("Name")
    ):
        # Extract option number from element_id (e.g., "Option1Name" -> 1)
        option_num = ""
        for char in element_id[6:]:  # Remove "Option" prefix, skip "Name" suffix
            if char.isdigit():
                option_num += char
            else:
                break
        if option_num:
            update_roulette_expansion_title(int(option_num), value)

    # If we have an original value, compare and update styling
    if element_id in original_values and element_id in element_ui_map:
        original_value = original_values[element_id]
        element = element_ui_map[element_id]

        # Ensure element has an ID
        if not element.id:
            element.id = f"source-{element_id}-{id(element)}"

        # Convert to same type for comparison
        if isinstance(value, bool) and not isinstance(original_value, bool):
            # Convert string 'True'/'False' to actual boolean
            if isinstance(original_value, str):
                original_value = original_value.lower() == "true"
            else:
                original_value = bool(original_value)
        elif isinstance(original_value, bool) and not isinstance(value, bool):
            value = value.lower() == "true" if isinstance(value, str) else bool(value)

        # Apply or remove animation using JavaScript
        if original_value != value:
            # Detect if this is a switch
            is_switch = "switch" in str(element.__class__).lower() or (
                hasattr(element, "props") and 'type="checkbox"' in str(element.props)
            )
            ui.run_javascript(
                f"window.addSourcePulseAnimation('{element.id}', {str(is_switch).lower()});"
            )
            logger.debug(
                f"Change detected in {element_id}: {original_value} → {value}, applying animation to {element.id}"
            )
        else:
            ui.run_javascript(f"window.removeSourcePulseAnimation('{element.id}');")
            logger.debug(
                f"No change in {element_id}, removing animation from {element.id}"
            )


def clear_changed_styling():
    """Clear changed styling from all elements"""
    logger.debug("Clearing all changed styling")

    # Loop through all elements and remove styling
    for element_id, element in element_ui_map.items():
        if not element:
            continue

        # Reduced logging to prevent spam
        pass


def reset_original_values(config_name):
    """Reset original values to current values in the form"""
    if config_name in form_data_store:
        form_data = form_data_store[config_name]
        # Update original values with current values
        for element_id, value in form_data.items():
            original_values[element_id] = value

    # Clear changed styling
    clear_changed_styling()


def save_config(config_parser, config_select, config_container):
    """Save the current config"""
    config_name = config_select.value
    if not config_name:
        ui.notify("No configuration selected.", type="negative")
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
                    except (ValueError, TypeError) as e:
                        logger.warning(
                            f"Failed to convert {element_id} value '{new_value}' to number, keeping original: {element.get('value')}"
                        )
                        continue  # Skip updating this element to preserve original value

                element["value"] = new_value

                # Update transparency property for color elements
                if element.get("type") == "color":
                    # Check if the color is transparent (rgba with alpha < 1)
                    color_value = form_data[element_id]
                    if color_value == "transparent":
                        element["transparent"] = True
                    elif color_value.startswith("rgba"):
                        try:
                            rgba_parts = (
                                color_value.replace("rgba(", "")
                                .replace(")", "")
                                .split(",")
                            )
                            alpha = float(rgba_parts[3])
                            element["transparent"] = alpha < 1.0
                        except:
                            element["transparent"] = False
                    else:
                        element["transparent"] = False

                # Update transparency property for select elements with color grids
                if (
                    element.get("type") == "select"
                    and element.get("display") == "color_grid"
                ):
                    color_value = form_data[element_id]
                    element["transparent"] = color_value == "transparent"
            else:
                # Element is missing from form data - this is a potential issue
                missing_element_ids.append(element_id)
                logger.warning(
                    f"Element {element_id} not found in form data for {config_name}, keeping original value: {element.get('value')}"
                )

        # Report missing elements
        if missing_element_ids:
            logger.warning(
                f"Config save for {config_name}: {len(missing_element_ids)} elements missing from form data: {missing_element_ids}"
            )
            # Show a notification to the user about this issue
            ui.notify(
                f"Warning: Some configuration values may not have been updated due to UI tracking issues. Missing: {', '.join(missing_element_ids[:3])}{'...' if len(missing_element_ids) > 3 else ''}",
                type="warning",
            )

        # Save the config
        if config_parser.save_config(config_name, original_config):
            ui.notify(f"Configuration saved for {config_name}.", type="positive")
            # Reset original values to current values
            reset_original_values(config_name)
        else:
            ui.notify(
                f"Failed to save configuration for {config_name}.", type="negative"
            )
    except Exception as e:
        logger.error(
            f"Error saving configuration for {config_name}: {str(e)}", exc_info=True
        )
        ui.notify(f"Error saving configuration: {str(e)}", type="negative")


def reset_config(config_parser, config_select, config_container):
    """Reset the current config to the saved version"""
    config_name = config_select.value
    if not config_name:
        ui.notify("No configuration selected.", type="negative")
        return

    # Clear element_ui_map before re-rendering
    element_ui_map.clear()

    # Re-render the config UI
    render_config_ui(config_parser, config_name, config_container, "")
    ui.notify(f"Configuration reset for {config_name}.", type="positive")


def create_new_config(config_parser, config_select, config_container):
    """Create a new config"""
    # Create a dialog for the new config name
    with ui.dialog() as dialog, ui.card():
        ui.label("New Configuration").classes("text-lg font-medium mb-4")

        name_input = ui.input(label="Configuration Name")

        with ui.row().classes("w-full justify-end gap-2 mt-4"):
            ui.button("Cancel", on_click=dialog.close).classes("control-button")
            ui.button(
                "Create",
                on_click=lambda: create_config_action(
                    name_input.value,
                    config_parser,
                    config_select,
                    config_container,
                    dialog,
                ),
            ).classes("control-button")

    dialog.open()


def create_config_action(name, config_parser, config_select, config_container, dialog):
    """Handle the create config action"""
    name = name.strip()
    if not name:
        ui.notify("Please enter a name for the configuration.", type="negative")
        return

    # Create a default config structure
    default_config = {
        "template_name": name,
        "elements": [
            {
                "type": "text",
                "id": "title",
                "label": "Title",
                "value": "New Template",
                "description": "The title of the template",
            }
        ],
    }

    # Create the config
    if config_parser.create_config(name, default_config):
        ui.notify(f"New configuration created: {name}", type="positive")
        dialog.close()

        # Reload the config files
        load_config_files(config_parser, config_select, config_container)

        # Select the new config
        config_select.value = name
        render_config_ui(config_parser, name, config_container, "")
    else:
        ui.notify(f"Failed to create configuration: {name}", type="negative")


def delete_config(config_parser, config_select, config_container):
    """Delete the current config"""
    config_name = config_select.value
    if not config_name:
        ui.notify("No configuration selected.", type="negative")
        return

    # Create a confirmation dialog
    with ui.dialog() as dialog, ui.card():
        ui.label("Confirm Deletion").classes("text-lg font-medium mb-4")
        ui.label(
            f"Are you sure you want to delete the configuration for {config_name}?"
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


def delete_config_action(
    config_name, config_parser, config_select, config_container, dialog
):
    """Handle the delete config action"""
    # Delete the config
    if config_parser.delete_config(config_name):
        ui.notify(f"Configuration deleted for {config_name}.", type="positive")
        dialog.close()

        # Remove from form data store
        if config_name in form_data_store:
            del form_data_store[config_name]

        # Reload the config files
        load_config_files(config_parser, config_select, config_container)
    else:
        ui.notify(f"Failed to delete configuration for {config_name}.", type="negative")


def track_element_change(element_id, element, value):
    """Track changes to an element and update UI accordingly

    Args:
        element_id (str): Identifier for the element
        element (ui.element): UI element reference
        value: New value of the element
    """
    # Get the original value for comparison
    original_value = original_values.get(element_id, None)

    # Store the element in the global map
    element_ui_map[element_id] = element

    # Log the change for debugging
    logger.debug(f"Element {element_id} changed: {original_value} → {value}")


def update_field_styling():
    """Update styling for all tracked fields"""
    logger.debug("Updating styling for all fields")

    # Loop through all tracked elements
    for element_id, element in element_ui_map.items():
        if not element:
            continue

        # Skip if we don't have an original value
        if element_id not in original_values:
            continue

        # Get current value
        if hasattr(element, "value"):
            value = element.value

            # Compare with original value
            original_value = original_values.get(element_id)

            # Log the result for debugging
            if original_value != value:
                logger.debug(
                    f"Change detected in {element_id}: {original_value} → {value}"
                )
            else:
                logger.debug(f"No change in {element_id}")
                logger.debug(f"No change in {element_id}")


def select_color_from_grid(color, element_id, form_data, hidden_input, swatch_refs):
    """Handle selection from a color grid"""
    # Update the hidden input with the selected color
    hidden_input.value = color

    # Update the form data
    update_form_data(form_data, element_id, color)

    # Update visual selection in all swatches
    for swatch_color, swatch_element in swatch_refs.items():
        # Clear all classes first
        swatch_element._classes.clear()

        if swatch_color == color:
            # This is the selected swatch
            swatch_element._classes.extend(["color-swatch", "selected"])
        else:
            # This is not selected
            swatch_element._classes.append("color-swatch")

        # Force a UI update
        swatch_element.update()

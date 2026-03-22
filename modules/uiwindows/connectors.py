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

import asyncio
import logging
import time
import uuid
from typing import Any, Dict

from nicegui import ui

from ..help_system.contextual_help import help_button

from .. import (
    connector_actions,
    connector_examples,
    connector_integration,
    connector_manager,
    connector_triggers,
)
from ..connector_core import (
    ActionType,
    ComparisonOperator,
    Connector,
    TriggerCondition,
    TriggerType,
)

logger = logging.getLogger(__name__)

# Global references for UI state
connectors_container = None
selected_connector = None
create_dialog = None
edit_dialog = None
search_input = None
current_search = ""
connector_cards = {}  # Store connector cards by ID for search functionality

# Add custom CSS for the connectors UI
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

.connector-card {
    transition: all 0.2s ease-in-out;
    background: var(--color-bg-surface);
    border: 1px solid var(--color-border-accent);
}

.connector-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 25px var(--color-bg-overlay);
    border-color: var(--color-primary);
}

.connector-card.disabled {
    opacity: 0.6;
    background: var(--color-bg-surface);
    border-color: var(--color-border-default);
}

.trigger-badge {
    background: linear-gradient(135deg, rgba(59, 130, 246, 0.2), rgba(37, 99, 235, 0.3));
    border: 1px solid rgba(59, 130, 246, 0.4);
    color: var(--color-info);
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
}

.action-badge {
    background: linear-gradient(135deg, rgba(16, 185, 129, 0.2), rgba(5, 150, 105, 0.3));
    border: 1px solid rgba(16, 185, 129, 0.4);
    color: var(--color-success);
    padding: 4px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
    width: fit-content;
}

.action-badge.block {
    display: block;
    margin-bottom: 2px;
}

.status-badge {
    padding: 4px 8px;
    border-radius: 8px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
}

.status-enabled {
    background: rgba(16, 185, 129, 0.2);
    color: var(--color-success);
    border: 1px solid rgba(16, 185, 129, 0.3);
}

.status-disabled {
    background: rgba(239, 68, 68, 0.2);
    color: var(--color-error);
    border: 1px solid rgba(239, 68, 68, 0.3);
}

.condition-chip {
    background: rgba(147, 51, 234, 0.1);
    border: 1px solid rgba(147, 51, 234, 0.3);
    color: var(--color-primary);
    padding: 4px 8px;
    border-radius: 8px;
    font-size: 10px;
    margin: 1px 0;
    display: block;
    width: fit-content;
}

.condition-chip.block {
    display: block;
    margin-bottom: 2px;
}

.form-section {
    background: var(--color-bg-surface);
    border: 1px solid var(--color-border-subtle);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
}

.form-section-title {
    color: var(--color-primary);
    font-weight: 600;
    font-size: 18px;
    margin-bottom: 12px;
    border-bottom: 1px solid var(--color-border-accent);
    padding-bottom: 4px;
}

.control-button {
    transition: all 0.2s ease;
}

.control-button:hover {
    transform: translateY(-1px);
    opacity: 0.9;
}

.dialog-section {
    background: var(--color-bg-elevated);
    border: 1px solid var(--color-border-default);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 16px;
}



/* Trigger Select Styling - Blue Theme */
.trigger-select .q-field__control::before {
    border-color: rgba(59, 130, 246, 0.4) !important;
}

.trigger-select .q-field__control::after {
    border-color: var(--color-info) !important;
}

.trigger-select .q-field__control {
    background: rgba(59, 130, 246, 0.1) !important;
    border-radius: 4px !important;
}

.trigger-select .q-field__native {
    color: var(--color-text-primary) !important;
}

.trigger-select .q-field__label {
    color: rgba(255, 255, 255, 0.8) !important;
}

.trigger-select .q-icon {
    color: var(--color-text-primary) !important;
}

/* Action Select Styling - Green Theme */
.action-select .q-field__control::before {
    border-color: rgba(16, 185, 129, 0.4) !important;
}

.action-select .q-field__control::after {
    border-color: var(--color-success) !important;
}

.action-select .q-field__control {
    background: rgba(16, 185, 129, 0.1) !important;
    border-radius: 4px !important;
}

.action-select .q-field__native {
    color: var(--color-text-primary) !important;
}

.action-select .q-field__label {
    color: rgba(255, 255, 255, 0.8) !important;
}

.action-select .q-icon {
    color: var(--color-text-primary) !important;
}

/* Condition Select Styling - Purple Theme */
.condition-select .q-field__control::before {
    border-color: rgba(147, 51, 234, 0.4) !important;
}

.condition-select .q-field__control::after {
    border-color: var(--color-primary) !important;
}

.condition-select .q-field__control {
    background: rgba(147, 51, 234, 0.1) !important;
    border-radius: 4px !important;
}

.condition-select .q-field__native {
    color: var(--color-text-primary) !important;
}

.condition-select .q-field__label {
    color: rgba(255, 255, 255, 0.8) !important;
}

.condition-select .q-icon {
    color: var(--color-text-primary) !important;
}

/* Input field styling for color themes */
.trigger-input .q-field__control::before {
    border-color: rgba(59, 130, 246, 0.4) !important;
}

.trigger-input .q-field__control::after {
    border-color: var(--color-info) !important;
}

.trigger-input .q-field__control {
    background: rgba(59, 130, 246, 0.1) !important;
    border-radius: 4px !important;
}

.trigger-input .q-field__native {
    color: var(--color-text-primary) !important;
}

.trigger-input .q-field__label {
    color: rgba(255, 255, 255, 0.8) !important;
}

.action-input .q-field__control::before {
    border-color: rgba(16, 185, 129, 0.4) !important;
}

.action-input .q-field__control::after {
    border-color: var(--color-success) !important;
}

.action-input .q-field__control {
    background: rgba(16, 185, 129, 0.1) !important;
    border-radius: 4px !important;
}

.action-input .q-field__native {
    color: var(--color-text-primary) !important;
}

.action-input .q-field__label {
    color: rgba(255, 255, 255, 0.8) !important;
}

.condition-input .q-field__control::before {
    border-color: rgba(147, 51, 234, 0.4) !important;
}

.condition-input .q-field__control::after {
    border-color: var(--color-primary) !important;
}

.condition-input .q-field__control {
    background: rgba(147, 51, 234, 0.1) !important;
    border-radius: 4px !important;
}

.condition-input .q-field__native {
    color: var(--color-text-primary) !important;
}

.condition-input .q-field__label {
    color: rgba(255, 255, 255, 0.8) !important;
}

/* Container background styling */
.condition-container {
    background: linear-gradient(135deg, rgba(147, 51, 234, 0.1), rgba(147, 51, 234, 0.05)) !important;
    border: 1px solid rgba(147, 51, 234, 0.2) !important;
}

.action-container {
    background: linear-gradient(135deg, rgba(16, 185, 129, 0.1), rgba(16, 185, 129, 0.05)) !important;
    border: 1px solid rgba(16, 185, 129, 0.2) !important;
}

/* Search input styling - theme aware */
.search-input .q-field__control::before {
    border-color: var(--color-border-default) !important;
}

.search-input .q-field__control::after {
    border-color: var(--color-primary) !important;
}

.search-input .q-field__control {
    background: var(--color-bg-surface) !important;
    border-radius: 6px !important;
}

.search-input .q-field__native {
    color: var(--color-text-primary) !important;
}

.search-input .q-field__label {
    color: var(--color-text-muted) !important;
}

.search-input .q-icon {
    color: var(--color-text-secondary) !important;
}

.search-input .q-field__control:hover {
    background: var(--color-hover-overlay) !important;
}
"""


def create_connectors_tab():
    """Create the Connectors tab UI"""
    global connectors_container

    # Add custom CSS to the page
    ui.add_head_html(f"<style>{CUSTOM_CSS}</style>")

    # Create a card for the entire tab content with flex layout
    with ui.element("div").classes("content-section w-full h-full flex flex-col"):
        # Compact header section - single row layout
        with ui.column().classes("w-full gap-3 p-4 flex-none"):
            # Top row: Title/description on left, buttons on right
            with ui.row().classes("w-full items-center justify-between"):
                # Left side - title and description
                with ui.column().classes("gap-1"):
                    ui.label("Connectors").classes(
                        "text-xl font-medium fade-in text-theme-primary"
                    )
                    ui.label(
                        "Create trigger-action automations for your stream"
                    ).classes("text-sm opacity-75 fade-in")

                # Right side - search and action buttons
                with ui.row().classes("items-center gap-3 slide-in"):
                    # Search input
                    global search_input
                    search_input = (
                        ui.input(
                            placeholder="Search connectors...",
                            on_change=on_search_change,
                        )
                        .classes("w-64 search-input")
                        .props("clearable dense")
                    )

                    ui.button(
                        icon="add",
                        text="New Connector",
                        on_click=show_create_connector_dialog,
                    ).classes(
                        "control-button btn-primary px-4 py-2"
                    )

                    ui.button(
                        icon="auto_awesome", text="Examples", on_click=create_examples
                    ).classes(
                        "control-button btn-warning px-3 py-2"
                    )

                    ui.button(
                        icon="refresh", text="Refresh", on_click=refresh_connectors
                    ).classes(
                        "control-button btn-cancel px-3 py-2"
                    )

                    ui.button(
                        icon="help", text="Help", on_click=show_help_dialog
                    ).classes(
                        "control-button btn-secondary px-3 py-2"
                    )

        # Main content area - flexible height
        with ui.element("div").classes("flex-grow overflow-hidden relative"):
            # Help icon in top right corner
            with ui.row().classes("absolute top-1 right-1 z-20"):
                help_button(topic_id="connectors_intro", tooltip="Connectors help", size="sm")

            with ui.scroll_area().classes("w-full h-full"):
                connectors_container = ui.element("div").classes("w-full p-4")

        # Load and display connectors
        load_connectors()

        # Initialize search visibility after loading
        update_search_visibility()


def load_connectors():
    """Load and display connectors"""
    global connectors_container, connector_cards

    if connectors_container is None:
        logger.error("Connectors container not initialized")
        return

    # Clear existing content and card references
    connectors_container.clear()
    connector_cards.clear()

    try:
        manager = connector_manager.get_manager()
        connectors = manager.get_all_connectors()

        with connectors_container:
            if not connectors:
                with ui.column().classes(
                    "w-full h-full flex flex-col items-center justify-center gap-4 p-8 empty-state"
                ):
                    ui.icon("link_off", size="4rem").classes("muted-text")
                    ui.label("No connectors created yet").classes(
                        "text-lg secondary-text fade-in"
                    )
                    ui.label(
                        "Create your first connector to automate your stream"
                    ).classes("text-sm muted-text fade-in")
                    ui.button(
                        icon="add",
                        text="Create First Connector",
                        on_click=show_create_connector_dialog,
                    ).classes(
                        "control-button btn-primary px-6 py-3 mt-4"
                    )
            else:
                # Display connectors in a grid
                with ui.element("div").classes(
                    "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
                ):
                    for connector_id, connector in connectors.items():
                        create_connector_card(connector_id, connector)

    except Exception as e:
        logger.error(f"Error loading connectors: {e}", exc_info=True)
        with connectors_container:
            ui.label(f"Error loading connectors: {str(e)}").classes("text-red-400")


def create_connector_card(connector_id: str, connector: Connector):
    """Create a card display for a connector"""
    card_classes = "connector-card p-4 rounded-lg"
    if not connector.enabled:
        card_classes += " disabled"

    # Create the card element and store reference for search functionality
    card_element = (
        ui.element("div")
        .classes(card_classes)
        .props(f'data-connector-id="{connector_id}"')
    )
    connector_cards[connector_id] = card_element

    with card_element:
        # Header row with name and status
        with ui.row().classes("w-full items-center justify-between mb-3"):
            with ui.column().classes("gap-1 flex-grow"):
                ui.label(connector.name).classes("text-base font-semibold")
                if connector.description:
                    ui.label(connector.description).classes("text-xs secondary-text")

            # Status badge and toggle
            with ui.column().classes("items-end gap-1"):
                status_classes = (
                    "status-badge status-enabled"
                    if connector.enabled
                    else "status-badge status-disabled"
                )
                status_text = "Enabled" if connector.enabled else "Disabled"
                ui.label(status_text).classes(status_classes)

                # Toggle switch
                ui.switch(
                    value=connector.enabled,
                    on_change=lambda e, cid=connector_id: toggle_connector(
                        cid, e.value
                    ),
                ).classes("scale-75")

        # Trigger and Actions flow
        if connector.trigger:
            with ui.row().classes("w-full items-start gap-3 mb-3"):
                # Left side - Trigger
                with ui.column().classes("gap-2 flex-shrink-0"):
                    # Trigger badge with icon
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("flash_on", size="16px").classes("text-blue-400")
                        ui.label("Trigger:").classes("text-xs secondary-text")
                        ui.label(
                            format_trigger_name(connector.trigger.trigger_type)
                        ).classes("trigger-badge")

                    # Conditions (indented)
                    if connector.trigger.conditions:
                        with ui.column().classes("ml-6 gap-1"):
                            ui.icon("filter_list", size="12px").classes(
                                "text-theme-primary mb-1"
                            )
                            for condition in connector.trigger.conditions:
                                condition_text = f"{condition.field} {condition.operator.value.replace('_', ' ')} {condition.value}"
                                ui.label(condition_text).classes(
                                    "condition-chip block mb-1"
                                )

                # Arrow
                ui.icon("arrow_forward", size="20px").classes(
                    "muted-text mt-2 flex-shrink-0"
                )

                # Right side - Actions
                if connector.actions:
                    with ui.column().classes("gap-2 flex-grow"):
                        # Actions header
                        with ui.row().classes("items-center gap-2"):
                            ui.icon("play_arrow", size="16px").classes("text-green-400")
                            ui.label("Actions:").classes("text-xs secondary-text")

                        # Action badges (vertical list)
                        with ui.column().classes("gap-1 ml-6"):
                            for action in connector.actions:
                                action_display = get_action_display_name(action)
                                ui.label(action_display).classes("action-badge block")

        # Statistics
        with ui.row().classes(
            "w-full items-center justify-between text-xs secondary-text mt-3 pt-3 border-t border-gray-700"
        ):
            ui.label(f"Triggered: {connector.trigger_count}x")
            if connector.last_triggered > 0:
                import datetime

                last_triggered = datetime.datetime.fromtimestamp(
                    connector.last_triggered
                )
                ui.label(f"Last: {last_triggered.strftime('%m/%d %H:%M')}")
            else:
                ui.label("Never triggered")

        # Action buttons
        with ui.row().classes("w-full items-center gap-2 mt-3"):
            ui.button(
                icon="edit",
                text="Edit",
                on_click=lambda cid=connector_id: show_edit_connector_dialog(cid),
            ).classes(
                "control-button btn-secondary text-xs px-3 py-1 flex-grow"
            )

            ui.button(
                icon="play_arrow",
                text="Test",
                on_click=lambda cid=connector_id: test_connector(cid),
            ).classes(
                "control-button btn-warning text-xs px-3 py-1 flex-grow"
            )

            ui.button(
                icon="delete",
                text="Delete",
                on_click=lambda cid=connector_id: delete_connector(cid),
            ).classes(
                "control-button btn-danger text-xs px-3 py-1 flex-grow"
            )


def format_trigger_name(trigger_type: TriggerType) -> str:
    """Format trigger type for display"""
    name_mapping = {
        TriggerType.TWITCH_BITS: "Twitch Bits",
        TriggerType.TWITCH_SUB: "Twitch Sub",
        TriggerType.TWITCH_RESUB: "Twitch Resub",
        TriggerType.TWITCH_GIFTSUB: "Gift Sub",
        TriggerType.TWITCH_FOLLOW: "Twitch Follow",
        TriggerType.TWITCH_RAID: "Twitch Raid",
        TriggerType.TWITCH_POINTS: "Channel Points",
        TriggerType.TWITCH_CHAT_MESSAGE: "Chat Message",
        TriggerType.TWITCH_HYPE_TRAIN_START: "Hype Train Start",
        TriggerType.TWITCH_HYPE_TRAIN_END: "Hype Train End",
        TriggerType.DONATION: "Donation",
        TriggerType.TIMER: "Timer",
        TriggerType.SCHEDULE: "Schedule",
        TriggerType.HOTKEY: "Hotkey",
        TriggerType.WEBHOOK: "Webhook",
    }
    return name_mapping.get(trigger_type, trigger_type.value.replace("_", " ").title())


def get_action_display_name(action) -> str:
    """Get a descriptive display name for an action"""
    try:
        action_type = action.action_type.value

        # Return more descriptive names based on action type and configuration
        if action_type == "template_control":
            template_name = getattr(action, "template_name", "Unknown")
            control_action = getattr(action, "control_action", "Unknown")
            return (
                f"{template_name.title()} → {control_action.replace('_', ' ').title()}"
            )
        elif action_type == "websocket_emit":
            event_name = getattr(action, "event_name", "Custom Event")
            return f"WebSocket → {event_name}"
        elif action_type == "send_chat_message":
            return "Send Chat Message"
        elif action_type == "trigger_alert":
            alert_type = getattr(action, "alert_type", "Alert")
            amount = getattr(action, "amount", "")
            if amount:
                return f"Trigger {alert_type.title()} ({amount})"
            return f"Trigger {alert_type.title()}"
        elif action_type == "api_call":
            method = getattr(action, "method", "GET")
            url = getattr(action, "url", "API")
            # Extract domain from URL for display
            if url.startswith("http"):
                try:
                    from urllib.parse import urlparse

                    domain = urlparse(url).netloc
                    return f"{method} → {domain}"
                except Exception:
                    return f"{method} → API Call"
            return f"{method} → API Call"
        elif action_type == "write_file":
            file_path = getattr(action, "file_path", "file")
            # Extract just the filename if it's a path
            file_name = file_path.split("/")[-1] if "/" in file_path else file_path
            return f"Write to {file_name}"
        elif action_type == "execute_command":
            command = getattr(action, "command", "Command")
            # Show first part of command for display
            if len(command) > 20:
                return f"Execute: {command[:20]}..."
            return f"Execute: {command}"
        elif action_type == "key_press":
            input_type = getattr(action, "input_type", "key")
            key_sequence = getattr(action, "key_sequence", "")
            action_mode = getattr(action, "action_mode", "press")
            if input_type == "macro":
                return "Execute Macro"
            elif input_type == "mouse":
                return f"Mouse {action_mode}: {key_sequence}"
            else:
                return f"Key {action_mode}: {key_sequence}"
        elif action_type == "audio_control":
            control_type = getattr(action, "control_type", "system_volume")
            action_mode = getattr(action, "action_mode", "set")
            if control_type == "microphone":
                return f"Microphone: {action_mode}"
            elif control_type == "application_volume":
                app_name = getattr(action, "target_application", "App")
                return f"{app_name} Volume: {action_mode}"
            else:
                return f"System Volume: {action_mode}"
        else:
            return action_type.replace("_", " ").title()

    except Exception:
        return getattr(action, "name", "Unknown Action")


def show_create_connector_dialog():
    """Show the create connector dialog"""
    show_connector_dialog()


def show_connector_dialog(connector_id: str = None):
    """Show the create/edit connector dialog"""
    global create_dialog

    if create_dialog:
        create_dialog.close()  # Close existing dialog first
        create_dialog = None

    # Create the dialog with 75% window size
    create_dialog = ui.dialog().props("persistent maximized")

    with create_dialog:
        with ui.card().classes("w-[75vw] h-[75vh] overflow-hidden"):
            with ui.column().classes("w-full h-full"):
                # Dialog header
                is_edit = connector_id is not None
                title = "Edit Connector" if is_edit else "Create New Connector"

                with ui.row().classes(
                    "w-full items-center justify-between p-4 border-b border-gray-700"
                ):
                    ui.label(title).classes("text-xl font-semibold text-theme-primary")
                    ui.button(icon="close", on_click=create_dialog.close).props(
                        "flat round"
                    ).classes("secondary-text")

                # Dialog content
                with ui.scroll_area().classes("flex-grow p-4"):
                    create_connector_form(connector_id)

    create_dialog.open()


def create_connector_form(connector_id: str = None):
    """Create the connector creation/edit form"""
    # Load existing connector data if editing
    existing_connector = None
    if connector_id:
        try:
            manager = connector_manager.get_manager()
            existing_connector = manager.get_connector(connector_id)
        except Exception as e:
            logger.error(f"Error loading connector for edit: {e}")
            ui.notify("Error loading connector data", type="negative")
            return

    # Form state - populate with existing data if editing
    trigger_type_value = None
    if existing_connector and existing_connector.trigger:
        try:
            trigger_type_value = existing_connector.trigger.trigger_type.value
        except Exception as e:
            logger.error(f"Error accessing trigger type: {e}", exc_info=True)

    form_data = {
        "connector_id": connector_id,
        "name": existing_connector.name if existing_connector else "",
        "description": existing_connector.description if existing_connector else "",
        "trigger_type": trigger_type_value,
        "trigger_conditions": [],
        "trigger_config": {
            "key_combination": "",
            "is_global": True,
        },
        "actions": [],
    }

    # Populate existing trigger conditions
    if (
        existing_connector
        and existing_connector.trigger
        and existing_connector.trigger.conditions
    ):
        for condition in existing_connector.trigger.conditions:
            try:
                form_data["trigger_conditions"].append(
                    {
                        "field": condition.field,
                        "operator": condition.operator.value,
                        "value": condition.value,
                    }
                )
            except Exception as e:
                logger.error(
                    f"Error processing condition during edit: {e}", exc_info=True
                )
                # Continue with other conditions rather than failing completely

    # Populate existing trigger config for hotkey triggers
    if (
        existing_connector
        and existing_connector.trigger
        and existing_connector.trigger.trigger_type.value == "hotkey"
    ):
        form_data["trigger_config"] = {
            "key_combination": getattr(
                existing_connector.trigger, "key_combination", ""
            ),
            "is_global": getattr(existing_connector.trigger, "is_global", True),
        }

    # Populate existing actions
    if existing_connector and existing_connector.actions:
        for action in existing_connector.actions:
            try:
                action_data = {"type": action.action_type.value, "config": {}}

                # Extract action configuration based on type
                if hasattr(action, "template_name"):
                    action_data["config"]["template_name"] = action.template_name
                if hasattr(action, "control_action"):
                    action_data["config"]["control_action"] = action.control_action

                # For template control actions, extract control_data back to individual fields
                if hasattr(action, "control_data") and action.control_data:
                    action_data["config"].update(action.control_data)

                if hasattr(action, "event_name"):
                    action_data["config"]["event_name"] = action.event_name
                if hasattr(action, "event_data"):
                    action_data["config"]["event_data"] = action.event_data
                if hasattr(action, "message"):
                    action_data["config"]["message"] = action.message
                if hasattr(action, "file_path"):
                    action_data["config"]["file_path"] = action.file_path
                if hasattr(action, "content"):
                    action_data["config"]["content"] = action.content
                if hasattr(action, "append"):
                    action_data["config"]["append"] = action.append

                # Extract greeting action specific attributes
                if hasattr(action, "user_id"):
                    action_data["config"]["user_id"] = action.user_id
                if hasattr(action, "username"):
                    action_data["config"]["username"] = action.username
                if hasattr(action, "greeting_text"):
                    action_data["config"]["greeting_text"] = action.greeting_text
                if hasattr(action, "greeting_id"):
                    action_data["config"]["greeting_id"] = action.greeting_id
                if hasattr(action, "enabled"):
                    action_data["config"]["enabled"] = action.enabled
                if hasattr(action, "force_send"):
                    action_data["config"]["force_send"] = action.force_send

                form_data["actions"].append(action_data)
            except Exception as e:
                logger.error(f"Error processing action during edit: {e}", exc_info=True)
                # Continue with other actions rather than failing completely

    # Main layout - use grid for better horizontal space usage
    with ui.element("div").classes("w-full"):
        # Top row - Basic information
        with ui.element("div").classes("form-section mb-4"):
            ui.label("Basic Information").classes("form-section-title")

            with ui.grid(columns=2).classes("gap-4 w-full"):
                ui.input(
                    label="Connector Name",
                    placeholder="e.g., High Bits Counter",
                    value=form_data["name"],
                    on_change=lambda e: form_data.update({"name": e.value}),
                ).classes("w-full")

                ui.input(
                    label="Description (optional)",
                    placeholder="What does this connector do?",
                    value=form_data["description"],
                    on_change=lambda e: form_data.update({"description": e.value}),
                ).classes("w-full")

        # Main content - side by side layout
        with ui.grid(columns=2).classes("gap-6 w-full"):
            # Left column - Trigger configuration
            with ui.element("div").classes("w-full"):
                with ui.element("div").classes("form-section"):
                    ui.label("Trigger Configuration").classes("form-section-title")

                    trigger_options = {
                        "twitch_bits": "Twitch Bits",
                        "twitch_sub": "Twitch Subscription",
                        "twitch_resub": "Twitch Resubscription",
                        "twitch_giftsub": "Twitch Gift Sub",
                        "twitch_follow": "Twitch Follow",
                        "twitch_raid": "Twitch Raid",
                        "twitch_points": "Channel Points",
                        "twitch_chat_message": "Chat Message",
                        "donation": "Donation",
                        "hotkey": "Hotkey Trigger",
                    }

                    ui.select(
                        options=trigger_options,
                        label="Trigger Type",
                        value=form_data["trigger_type"],
                        on_change=lambda e: handle_trigger_type_change(
                            e.value,
                            form_data,
                            trigger_config_container,
                            conditions_container,
                        ),
                    ).classes("w-full mb-4 trigger-select")

                    # Trigger config container (for hotkey, timer, etc.)
                    trigger_config_container = ui.element("div").classes("w-full mb-4")

                    # Conditions container
                    conditions_container = ui.element("div").classes("w-full")

            # Right column - Actions configuration
            with ui.element("div").classes("w-full"):
                with ui.element("div").classes("form-section"):
                    ui.label("Actions Configuration").classes("form-section-title")

                    actions_container = ui.element("div").classes("w-full")

                    with ui.row().classes("w-full items-center gap-2 mt-4"):
                        ui.button(
                            icon="add",
                            text="Add Action",
                            on_click=lambda: add_action_to_form(
                                form_data, actions_container
                            ),
                        ).classes(
                            "control-button btn-success px-4 py-2"
                        )

        # Form buttons
        with ui.row().classes(
            "w-full items-center justify-end gap-2 mt-6 pt-4 border-t border-gray-700"
        ):
            ui.button(text="Cancel", on_click=create_dialog.close).props(
                "flat"
            ).classes("secondary-text")

            is_edit = connector_id is not None
            button_text = "Update Connector" if is_edit else "Create Connector"
            ui.button(
                icon="save",
                text=button_text,
                on_click=lambda: save_connector(form_data),
            ).classes(
                "control-button btn-primary px-6 py-2"
            )

        # Initialize existing data if editing
        if existing_connector:
            # Initialize trigger configuration if trigger type is already selected
            if form_data.get("trigger_type"):
                handle_trigger_type_change(
                    form_data["trigger_type"],
                    form_data,
                    trigger_config_container,
                    conditions_container,
                )

            # Populate trigger conditions if any
            if form_data["trigger_type"]:
                # Add existing conditions
                for i, condition_data in enumerate(form_data["trigger_conditions"]):
                    add_condition_to_trigger_with_data_and_index(
                        form_data["trigger_type"],
                        form_data,
                        conditions_container,
                        condition_data,
                        i,
                    )

            # Populate existing actions
            for i, action_data in enumerate(form_data["actions"]):
                add_action_to_form_with_data_and_index(
                    form_data, actions_container, action_data, i
                )


def handle_trigger_type_change(
    trigger_type: str, form_data: dict, trigger_config_container, conditions_container
):
    """Handle trigger type change and show relevant configuration options"""
    if not trigger_type:
        return

    form_data["trigger_type"] = trigger_type
    trigger_config_container.clear()
    conditions_container.clear()

    # Show trigger-specific configuration
    if trigger_type == "hotkey":
        with trigger_config_container:
            ui.label("Hotkey Configuration").classes(
                "text-sm font-medium secondary-text mb-2"
            )

            # Key combination input
            ui.input(
                label="Key Combination",
                placeholder="e.g., ctrl+shift+f, f12, alt+tab",
                value=form_data["trigger_config"]["key_combination"],
                on_change=lambda e: form_data["trigger_config"].update(
                    {"key_combination": e.value}
                ),
            ).classes("w-full mb-3").props(
                'hint="Use + to combine keys (ctrl+shift+f)"'
            )

            # Global hotkey checkbox
            ui.checkbox(
                text="Global hotkey (works when app is not focused)",
                value=form_data["trigger_config"]["is_global"],
                on_change=lambda e: form_data["trigger_config"].update(
                    {"is_global": e.value}
                ),
            ).classes("mb-3")

    with conditions_container:
        ui.label("Conditions (optional)").classes(
            "text-base font-medium secondary-text mb-2"
        )
        ui.label("Add conditions to make the trigger more specific").classes(
            "text-xs muted-text mb-3"
        )

        # Add condition button
        ui.button(
            icon="add",
            text="Add Condition",
            on_click=lambda: add_condition_to_trigger(
                trigger_type, form_data, conditions_list
            ),
        ).classes(
            "control-button btn-secondary text-sm px-3 py-1 mb-3"
        )

        # Conditions list
        conditions_list = ui.element("div").classes("w-full space-y-2")


def add_condition_to_trigger(trigger_type: str, form_data: dict, conditions_list):
    """Add a condition input to the trigger"""
    add_condition_to_trigger_with_data(trigger_type, form_data, conditions_list, None)


def add_condition_to_trigger_with_data(
    trigger_type: str, form_data: dict, conditions_list, condition_data: dict = None
):
    """Add a condition input to the trigger with optional existing data"""
    # For new conditions, calculate the next index
    condition_index = len(form_data.get("trigger_conditions", []))
    add_condition_to_trigger_with_data_and_index(
        trigger_type, form_data, conditions_list, condition_data, condition_index
    )


def add_condition_to_trigger_with_data_and_index(
    trigger_type: str,
    form_data: dict,
    conditions_list,
    condition_data: dict = None,
    condition_index: int = None,
):
    """Add a condition input to the trigger with optional existing data and explicit index"""
    # Get available fields for this trigger type
    available_fields = get_available_fields_for_trigger(trigger_type)

    # Use existing data or defaults
    initial_field = condition_data.get("field") if condition_data else None
    initial_operator = condition_data.get("operator") if condition_data else None
    initial_value = condition_data.get("value", "") if condition_data else ""

    # If this is a new condition (no condition_data), add it to form_data
    if condition_data is None:
        if "trigger_conditions" not in form_data:
            form_data["trigger_conditions"] = []
        if condition_index is None:
            condition_index = len(form_data["trigger_conditions"])
        form_data["trigger_conditions"].append(
            {
                "field": initial_field or "",
                "operator": initial_operator or "",
                "value": initial_value,
            }
        )

    # If condition_index is still None, use the current position in the conditions list
    if condition_index is None:
        condition_index = len(form_data.get("trigger_conditions", [])) - 1

    with conditions_list:
        with ui.row().classes(
            "w-full items-center gap-2 p-2 condition-container rounded"
        ):
            ui.select(
                options=available_fields,
                label="Field",
                value=initial_field,
                on_change=lambda e, idx=condition_index: update_condition_field(
                    idx, e.value, form_data
                ),
            ).classes("w-32 condition-select")

            ui.select(
                options={
                    "equal": "Equals",
                    "greater_than_or_equal": "Greater than or equal",
                    "less_than_or_equal": "Less than or equal",
                    "greater_than": "Greater than",
                    "less_than": "Less than",
                    "contains": "Contains",
                    "starts_with": "Starts with",
                    "ends_with": "Ends with",
                },
                label="Operator",
                value=initial_operator,
                on_change=lambda e, idx=condition_index: update_condition_operator(
                    idx, e.value, form_data
                ),
            ).classes("w-32 condition-select")

            ui.input(
                label="Value",
                value=initial_value,
                on_change=lambda e, idx=condition_index: update_condition_value(
                    idx, e.value, form_data
                ),
            ).classes("flex-grow condition-input")

            ui.button(
                icon="delete",
                on_click=lambda idx=condition_index: remove_condition(
                    idx, form_data, conditions_list
                ),
            ).props("flat round").classes("text-red-400")


def get_available_fields_for_trigger(trigger_type: str) -> Dict[str, str]:
    """Get available fields for a trigger type"""
    field_mappings = {
        "twitch_bits": {
            "amount": "Amount",
            "username": "Username",
            "message": "Message",
        },
        "twitch_sub": {
            "tier": "Tier",
            "username": "Username",
            "message": "Message",
        },
        "twitch_resub": {
            "tier": "Tier",
            "username": "Username",
            "message": "Message",
        },
        "twitch_giftsub": {
            "tier": "Tier",
            "gifter_username": "Gifter Username",
            "quantity": "Quantity",
            "username": "Recipient Username",
        },
        "twitch_raid": {
            "viewer_count": "Viewer Count",
            "username": "Username",
        },
        "twitch_chat_message": {
            "username": "Username",
            "message": "Message",
        },
        "donation": {
            "amount": "Amount",
            "username": "Username",
            "message": "Message",
        },
        "twitch_points": {
            "reward_name": "Reward Name",
            "reward_id": "Reward ID",
            "username": "Username",
            "user_input": "User Input",
        },
    }

    return field_mappings.get(trigger_type, {"username": "Username"})


def add_action_to_form(form_data: dict, actions_container):
    """Add an action to the form"""
    add_action_to_form_with_data(form_data, actions_container, None)


def add_action_to_form_with_data(
    form_data: dict, actions_container, action_data: dict = None
):
    """Add an action to the form with optional existing data"""
    # For new actions, calculate the next index
    action_index = len(form_data.get("actions", []))
    add_action_to_form_with_data_and_index(
        form_data, actions_container, action_data, action_index
    )


def add_action_to_form_with_data_and_index(
    form_data: dict,
    actions_container,
    action_data: dict = None,
    action_index: int = None,
):
    """Add an action to the form with optional existing data and explicit index"""
    # Get available actions
    available_actions = get_available_actions()

    # Use existing data or defaults
    initial_type = action_data.get("type") if action_data else None
    initial_config = action_data.get("config", {}) if action_data else {}

    # If this is a new action (no action_data), add it to form_data
    if action_data is None:
        if "actions" not in form_data:
            form_data["actions"] = []
        if action_index is None:
            action_index = len(form_data["actions"])
        form_data["actions"].append(
            {"type": initial_type or "", "config": initial_config}
        )

    # If action_index is still None, use the current position in the actions list
    if action_index is None:
        action_index = len(form_data.get("actions", [])) - 1

    with actions_container:
        with ui.element("div").classes("w-full p-3 action-container rounded mb-3"):
            with ui.row().classes("w-full items-center justify-between mb-2"):
                ui.label(f"Action #{action_index + 1}").classes(
                    "text-base font-medium"
                )
                ui.button(
                    icon="delete",
                    on_click=lambda idx=action_index: remove_action(
                        idx, form_data, actions_container
                    ),
                ).props("flat round").classes("text-red-400")

            ui.select(
                options=available_actions,
                label="Action Type",
                value=initial_type,
                on_change=lambda e, idx=action_index: handle_action_type_change(
                    idx, e.value, form_data, action_config_container
                ),
            ).classes("w-full mb-3 action-select")

            action_config_container = ui.element("div").classes("w-full")

    # If we have existing data, populate the configuration
    if action_data and initial_type:
        handle_action_type_change_with_data(
            action_index,
            initial_type,
            form_data,
            action_config_container,
            initial_config,
        )


def get_available_actions() -> Dict[str, str]:
    """Get available action types"""
    return {
        "template_control": "Template Control",
        "websocket_emit": "WebSocket Event",
        "trigger_alert": "Trigger Alert",
        "send_chat_message": "Send Chat Message",
        "add_greeting": "Add Greeting",
        "update_greeting": "Update Greeting",
        "send_greeting": "Send Greeting",
        "api_call": "API Call",
        "write_file": "Write to File",
        "execute_command": "Execute Command",
        "key_press": "Key Press / Mouse Click",
        "audio_control": "Audio Control",
    }


def handle_action_type_change(
    action_index: int, action_type: str, form_data: dict, config_container
):
    """Handle action type change and show relevant configuration options"""
    handle_action_type_change_with_data(
        action_index, action_type, form_data, config_container, {}
    )


def handle_action_type_change_with_data(
    action_index: int,
    action_type: str,
    form_data: dict,
    config_container,
    initial_config: dict = None,
):
    """Handle action type change and show relevant configuration options with initial data"""
    if not action_type:
        return

    if initial_config is None:
        initial_config = {}

    form_data["actions"][action_index]["type"] = action_type
    # Only clear config if we don't have initial data
    if not initial_config:
        form_data["actions"][action_index]["config"] = {}
    else:
        form_data["actions"][action_index]["config"] = initial_config.copy()

    config_container.clear()

    with config_container:
        if action_type == "template_control":
            create_template_control_config(action_index, form_data, initial_config)
        elif action_type == "websocket_emit":
            create_websocket_emit_config(action_index, form_data, initial_config)
        elif action_type == "send_chat_message":
            create_chat_message_config(action_index, form_data, initial_config)
        elif action_type == "add_greeting":
            create_add_greeting_config(action_index, form_data, initial_config)
        elif action_type == "update_greeting":
            create_update_greeting_config(action_index, form_data, initial_config)
        elif action_type == "send_greeting":
            create_send_greeting_config(action_index, form_data, initial_config)
        elif action_type == "trigger_alert":
            create_trigger_alert_config(action_index, form_data, initial_config)
        elif action_type == "api_call":
            create_api_call_config(action_index, form_data, initial_config)
        elif action_type == "execute_command":
            create_execute_command_config(action_index, form_data, initial_config)
        elif action_type == "write_file":
            create_write_file_config(action_index, form_data, initial_config)
        elif action_type == "key_press":
            create_key_press_config(action_index, form_data, initial_config)
        elif action_type == "audio_control":
            create_audio_control_config(action_index, form_data, initial_config)


def create_key_press_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for key press action"""
    if initial_config is None:
        initial_config = {}

    # Input Type Selection
    ui.select(
        options={"key": "Keyboard", "mouse": "Mouse", "macro": "Macro Sequence"},
        label="Input Type",
        value=initial_config.get("input_type", "key"),
        on_change=lambda e: [
            update_action_config(action_index, "input_type", e.value, form_data),
            refresh_key_press_ui(action_index, form_data, e.value),
        ],
    ).classes("w-full mb-2 action-select")

    # Dynamic container for input-specific options
    config_container = ui.element("div").classes("w-full")

    def refresh_key_press_ui(action_index: int, form_data: dict, input_type: str):
        """Refresh the UI based on input type selection"""
        config_container.clear()

        with config_container:
            if input_type == "key":
                create_keyboard_config(action_index, form_data, initial_config)
            elif input_type == "mouse":
                create_mouse_config(action_index, form_data, initial_config)
            elif input_type == "macro":
                create_macro_config(action_index, form_data, initial_config)

    # Initialize with current input type
    current_input_type = initial_config.get("input_type", "key")
    refresh_key_press_ui(action_index, form_data, current_input_type)


def create_keyboard_config(action_index: int, form_data: dict, initial_config: dict):
    """Create keyboard-specific configuration"""

    # Action Mode
    ui.select(
        options={
            "press": "Press & Release",
            "hold": "Press & Hold",
            "repeat": "Repeat",
        },
        label="Action Mode",
        value=initial_config.get("action_mode", "press"),
        on_change=lambda e: [
            update_action_config(action_index, "action_mode", e.value, form_data),
            show_mode_options(e.value),
        ],
    ).classes("w-full mb-2 action-select")

    # Key Sequence Input
    ui.input(
        label="Key Sequence",
        placeholder="e.g., ctrl+c, alt+tab, f1, space, enter",
        value=initial_config.get("key_sequence", ""),
        on_change=lambda e: update_action_config(
            action_index, "key_sequence", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.label(
        "Enter key combinations using '+' (e.g., ctrl+c). Use {{placeholder}} for dynamic values."
    ).classes("text-xs muted-text mb-2")

    # Mode-specific options container
    mode_options_container = ui.element("div").classes("w-full")

    def show_mode_options(mode: str):
        """Show options specific to the selected mode"""
        mode_options_container.clear()

        with mode_options_container:
            if mode == "hold":
                ui.input(
                    label="Hold Duration (seconds)",
                    placeholder="0.5",
                    value=str(initial_config.get("hold_duration", 0.5)),
                    on_change=lambda e: update_action_config(
                        action_index,
                        "hold_duration",
                        float(e.value) if e.value else 0.5,
                        form_data,
                    ),
                ).classes("w-full mb-2 action-input")
            elif mode == "repeat":
                with ui.row().classes("w-full gap-4"):
                    ui.input(
                        label="Repeat Count",
                        placeholder="3",
                        value=str(initial_config.get("repeat_count", 1)),
                        on_change=lambda e: update_action_config(
                            action_index,
                            "repeat_count",
                            int(e.value) if e.value.isdigit() else 1,
                            form_data,
                        ),
                    ).classes("flex-1 action-input")
                    ui.input(
                        label="Interval (seconds)",
                        placeholder="0.1",
                        value=str(initial_config.get("repeat_interval", 0.1)),
                        on_change=lambda e: update_action_config(
                            action_index,
                            "repeat_interval",
                            float(e.value) if e.value else 0.1,
                            form_data,
                        ),
                    ).classes("flex-1 action-input")

    # Initialize mode options
    current_mode = initial_config.get("action_mode", "press")
    show_mode_options(current_mode)


def create_mouse_config(action_index: int, form_data: dict, initial_config: dict):
    """Create mouse-specific configuration"""

    # Mouse Button Selection
    ui.select(
        options={
            "left_click": "Left Click",
            "right_click": "Right Click",
            "middle_click": "Middle Click",
        },
        label="Mouse Button",
        value=initial_config.get("key_sequence", "left_click"),
        on_change=lambda e: update_action_config(
            action_index, "key_sequence", e.value, form_data
        ),
    ).classes("w-full mb-2 action-select")

    # Action Mode
    ui.select(
        options={
            "press": "Single Click",
            "hold": "Press & Hold",
            "repeat": "Multiple Clicks",
        },
        label="Click Mode",
        value=initial_config.get("action_mode", "press"),
        on_change=lambda e: [
            update_action_config(action_index, "action_mode", e.value, form_data),
            show_mouse_mode_options(e.value),
        ],
    ).classes("w-full mb-2 action-select")

    # Mode-specific options container
    mouse_mode_options_container = ui.element("div").classes("w-full")

    def show_mouse_mode_options(mode: str):
        """Show options specific to the selected mouse mode"""
        mouse_mode_options_container.clear()

        with mouse_mode_options_container:
            if mode == "hold":
                ui.input(
                    label="Hold Duration (seconds)",
                    placeholder="0.5",
                    value=str(initial_config.get("hold_duration", 0.5)),
                    on_change=lambda e: update_action_config(
                        action_index,
                        "hold_duration",
                        float(e.value) if e.value else 0.5,
                        form_data,
                    ),
                ).classes("w-full mb-2 action-input")
            elif mode == "repeat":
                with ui.row().classes("w-full gap-4"):
                    ui.input(
                        label="Click Count",
                        placeholder="3",
                        value=str(initial_config.get("repeat_count", 1)),
                        on_change=lambda e: update_action_config(
                            action_index,
                            "repeat_count",
                            int(e.value) if e.value.isdigit() else 1,
                            form_data,
                        ),
                    ).classes("flex-1 action-input")
                    ui.input(
                        label="Interval (seconds)",
                        placeholder="0.1",
                        value=str(initial_config.get("repeat_interval", 0.1)),
                        on_change=lambda e: update_action_config(
                            action_index,
                            "repeat_interval",
                            float(e.value) if e.value else 0.1,
                            form_data,
                        ),
                    ).classes("flex-1 action-input")

    # Initialize mouse mode options
    current_mode = initial_config.get("action_mode", "press")
    show_mouse_mode_options(current_mode)


def create_macro_config(action_index: int, form_data: dict, initial_config: dict):
    """Create macro sequence configuration"""

    # Macro Description
    ui.label("Macro Sequence").classes("text-base font-medium secondary-text mb-2")
    ui.label("Define a sequence of keyboard and mouse actions with delays.").classes(
        "text-xs muted-text mb-2"
    )

    # Macro steps container
    macro_container = ui.element("div").classes("w-full")

    # Initialize macro steps from config
    try:
        import json

        macro_steps = json.loads(initial_config.get("macro_sequence", "[]"))
    except Exception:
        macro_steps = []

    if not macro_steps:
        macro_steps = [{"type": "key", "target": "", "delay": 0.1}]

    def update_macro_sequence():
        """Update the macro sequence in form data"""
        import json

        macro_json = json.dumps(macro_steps)
        update_action_config(action_index, "macro_sequence", macro_json, form_data)

    def render_macro_steps():
        """Render all macro steps"""
        macro_container.clear()

        with macro_container:
            for i, step in enumerate(macro_steps):
                with ui.card().classes("w-full mb-2 p-3"):
                    with ui.row().classes("w-full items-center gap-4"):
                        # Step number
                        ui.label(f"Step {i+1}").classes(
                            "text-sm font-medium text-theme-primary min-w-16"
                        )

                        # Step type
                        ui.select(
                            options={
                                "key": "Keyboard",
                                "mouse": "Mouse",
                                "delay": "Delay",
                            },
                            value=step.get("type", "key"),
                            on_change=lambda e, idx=i: update_step_type(idx, e.value),
                        ).classes("w-32")

                        # Step target/value
                        if step.get("type") == "delay":
                            ui.input(
                                placeholder="Seconds",
                                value=str(step.get("target", "1.0")),
                                on_change=lambda e, idx=i: update_step_target(
                                    idx, e.value
                                ),
                            ).classes("flex-1")
                        elif step.get("type") == "mouse":
                            ui.select(
                                options={
                                    "left_click": "Left",
                                    "right_click": "Right",
                                    "middle_click": "Middle",
                                },
                                value=step.get("target", "left_click"),
                                on_change=lambda e, idx=i: update_step_target(
                                    idx, e.value
                                ),
                            ).classes("flex-1")
                        else:  # keyboard
                            ui.input(
                                placeholder="e.g., ctrl+c, enter, space",
                                value=step.get("target", ""),
                                on_change=lambda e, idx=i: update_step_target(
                                    idx, e.value
                                ),
                            ).classes("flex-1")

                        # Delay after step
                        if step.get("type") != "delay":
                            ui.input(
                                placeholder="Delay",
                                value=str(step.get("delay", 0.1)),
                                on_change=lambda e, idx=i: update_step_delay(
                                    idx, e.value
                                ),
                            ).classes("w-20")

                        # Remove step button
                        if len(macro_steps) > 1:
                            ui.button(
                                icon="delete", on_click=lambda idx=i: remove_step(idx)
                            ).props("flat dense").classes("text-red-400")

            # Add step button
            ui.button(icon="add", text="Add Step", on_click=add_step).classes(
                "w-full mt-2 btn-primary"
            )

    def update_step_type(idx: int, step_type: str):
        """Update step type"""
        if idx < len(macro_steps):
            macro_steps[idx]["type"] = step_type
            if step_type == "delay":
                macro_steps[idx]["target"] = "1.0"
            elif step_type == "mouse":
                macro_steps[idx]["target"] = "left_click"
            else:
                macro_steps[idx]["target"] = ""
            update_macro_sequence()
            render_macro_steps()

    def update_step_target(idx: int, target: str):
        """Update step target"""
        if idx < len(macro_steps):
            macro_steps[idx]["target"] = target
            update_macro_sequence()

    def update_step_delay(idx: int, delay: str):
        """Update step delay"""
        if idx < len(macro_steps):
            try:
                macro_steps[idx]["delay"] = float(delay) if delay else 0.1
            except ValueError:
                macro_steps[idx]["delay"] = 0.1
            update_macro_sequence()

    def add_step():
        """Add a new step"""
        macro_steps.append({"type": "key", "target": "", "delay": 0.1})
        update_macro_sequence()
        render_macro_steps()

    def remove_step(idx: int):
        """Remove a step"""
        if len(macro_steps) > 1 and idx < len(macro_steps):
            macro_steps.pop(idx)
            update_macro_sequence()
            render_macro_steps()

    # Initial render
    render_macro_steps()

    # Help text
    ui.label(
        "Use {{placeholder}} in keyboard steps for dynamic values from trigger events."
    ).classes("text-xs muted-text mt-2")


def create_audio_control_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for audio control action"""
    if initial_config is None:
        initial_config = {}

    # Permission Status Section
    permission_container = ui.element("div").classes("w-full mb-4")
    update_permission_status(permission_container)

    # Control Type Selection
    ui.select(
        options={
            "device": "Device",
            "application": "Application",
        },
        label="Control Type",
        value=initial_config.get("control_type", "device"),
        on_change=lambda e: [
            update_action_config(action_index, "control_type", e.value, form_data),
            refresh_audio_control_ui(action_index, form_data, e.value),
        ],
    ).classes("w-full mb-2 action-select")

    # Duration input (shown for all control types)
    ui.input(
        label="Duration (seconds)",
        placeholder="0",
        value=str(initial_config.get("duration", 0.0)),
        on_change=lambda e: update_action_config(
            action_index, "duration", float(e.value) if e.value else 0.0, form_data
        ),
    ).classes("w-full mb-4 action-input")

    ui.label(
        "Duration in seconds (0 = permanent change, >0 = temporary change that auto-restores)"
    ).classes("text-xs muted-text mb-4")

    # Dynamic container for control-specific options
    config_container = ui.element("div").classes("w-full")

    def refresh_audio_control_ui(action_index: int, form_data: dict, control_type: str):
        """Refresh the UI based on control type selection"""
        config_container.clear()

        with config_container:
            if control_type == "device":
                create_device_config(action_index, form_data, initial_config)
            elif control_type == "application":
                create_application_config(action_index, form_data, initial_config)

    # Initialize with current control type
    current_control_type = initial_config.get("control_type", "device")
    refresh_audio_control_ui(action_index, form_data, current_control_type)


def update_permission_status(container):
    """Update the permission status display"""
    container.clear()

    with container:
        permission_info = check_audio_permissions()

        # Permission status indicator
        with ui.row().classes("w-full items-center gap-2 mb-2"):
            if permission_info["has_permissions"]:
                ui.icon("check_circle", color="green").classes("text-green-500")
                ui.label("Audio permissions: OK").classes("text-green-600 font-medium")
            else:
                ui.icon("warning", color="orange").classes("text-orange-500")
                ui.label("Audio permissions: Restricted").classes(
                    "text-orange-600 font-medium"
                )

        # Permission message
        ui.label(permission_info["message"]).classes("text-sm muted-text mb-2")

        # Request permissions button (if applicable)
        if not permission_info["has_permissions"] and permission_info.get(
            "can_request", False
        ):
            ui.button(
                "Request Permissions",
                icon="security",
                on_click=lambda: request_permissions_and_update(container),
            ).classes("btn-secondary text-sm px-3 py-1")

        # Additional instructions for Windows admin
        if permission_info.get("is_admin") is False:
            ui.label(
                "💡 Tip: Right-click the application and select 'Run as administrator' for full audio control"
            ).classes("text-xs text-blue-600 mt-2")


def request_permissions_and_update(container):
    """Request permissions and update the status display"""
    try:
        success = request_audio_permissions()
        if success:
            ui.notify("Permission request initiated", type="positive")
        else:
            ui.notify("Permission request failed or not supported", type="warning")

        # Refresh the permission status
        update_permission_status(container)
    except Exception as e:
        logger.error(f"Error requesting permissions: {e}")
        ui.notify(f"Error requesting permissions: {e}", type="negative")


def create_system_volume_config(
    action_index: int, form_data: dict, initial_config: dict
):
    """Create system volume configuration"""

    # Action Mode
    ui.select(
        options={
            "set": "Set Volume",
            "set_random": "Set Random Volume",
            "increase": "Increase Volume",
            "decrease": "Decrease Volume",
            "mute": "Mute",
            "unmute": "Unmute",
            "toggle_mute": "Toggle Mute",
        },
        label="Action",
        value=initial_config.get("action_mode", "set"),
        on_change=lambda e: [
            update_action_config(action_index, "action_mode", e.value, form_data),
            show_volume_options(e.value),
        ],
    ).classes("w-full mb-2 action-select")

    # Volume options container
    volume_options_container = ui.element("div").classes("w-full")

    def show_volume_options(mode: str):
        """Show options specific to the selected mode"""
        volume_options_container.clear()

        with volume_options_container:
            if mode == "set":
                ui.input(
                    label="Volume Level (%)",
                    placeholder="50",
                    value=str(initial_config.get("volume_level", 50.0)),
                    on_change=lambda e: update_action_config(
                        action_index,
                        "volume_level",
                        float(e.value) if e.value else 50.0,
                        form_data,
                    ),
                ).classes("w-full mb-2 action-input")
                ui.label("Volume level from 0-100%").classes("text-xs muted-text")
            elif mode in ["increase", "decrease"]:
                ui.input(
                    label="Volume Step (%)",
                    placeholder="10",
                    value=str(initial_config.get("volume_step", 10.0)),
                    on_change=lambda e: update_action_config(
                        action_index,
                        "volume_step",
                        float(e.value) if e.value else 10.0,
                        form_data,
                    ),
                ).classes("w-full mb-2 action-input")
                ui.label("Amount to increase/decrease volume by").classes(
                    "text-xs muted-text"
                )

    # Initialize volume options
    current_mode = initial_config.get("action_mode", "set")
    show_volume_options(current_mode)


def create_microphone_config(action_index: int, form_data: dict, initial_config: dict):
    """Create microphone control configuration"""

    # Action Mode
    ui.select(
        options={
            "mute": "Mute Microphone",
            "unmute": "Unmute Microphone",
            "toggle_mute": "Toggle Mute",
            "set": "Set Microphone Level",
        },
        label="Action",
        value=initial_config.get("action_mode", "toggle_mute"),
        on_change=lambda e: [
            update_action_config(action_index, "action_mode", e.value, form_data),
            show_microphone_options(e.value),
        ],
    ).classes("w-full mb-2 action-select")

    # Microphone options container
    mic_options_container = ui.element("div").classes("w-full")

    def show_microphone_options(mode: str):
        """Show options specific to the selected microphone mode"""
        mic_options_container.clear()

        with mic_options_container:
            if mode == "set":
                ui.input(
                    label="Microphone Level (%)",
                    placeholder="50",
                    value=str(initial_config.get("volume_level", 50.0)),
                    on_change=lambda e: update_action_config(
                        action_index,
                        "volume_level",
                        float(e.value) if e.value else 50.0,
                        form_data,
                    ),
                ).classes("w-full mb-2 action-input")
                ui.label("Microphone input level from 0-100%").classes(
                    "text-xs muted-text"
                )

    # Initialize microphone options
    current_mode = initial_config.get("action_mode", "toggle_mute")
    show_microphone_options(current_mode)

    # Help text
    ui.label(
        "Microphone control may require elevated permissions on some systems."
    ).classes("text-xs text-yellow-500 mt-2")


def create_application_volume_config(
    action_index: int, form_data: dict, initial_config: dict
):
    """Create application volume configuration"""

    # Application Name Input
    ui.input(
        label="Application Name",
        placeholder="e.g., discord.exe, spotify.exe, chrome.exe",
        value=initial_config.get("target_application", ""),
        on_change=lambda e: update_action_config(
            action_index, "target_application", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.label(
        "Enter the application process name (case-insensitive). Use {{placeholder}} for dynamic values."
    ).classes("text-xs muted-text mb-2")

    # Action Mode
    ui.select(
        options={
            "set": "Set Volume",
            "mute": "Mute Application",
            "unmute": "Unmute Application",
            "toggle_mute": "Toggle Mute",
        },
        label="Action",
        value=initial_config.get("action_mode", "set"),
        on_change=lambda e: [
            update_action_config(action_index, "action_mode", e.value, form_data),
            show_app_volume_options(e.value),
        ],
    ).classes("w-full mb-2 action-select")

    # App volume options container
    app_volume_options_container = ui.element("div").classes("w-full")

    def show_app_volume_options(mode: str):
        """Show options specific to the selected app volume mode"""
        app_volume_options_container.clear()

        with app_volume_options_container:
            if mode == "set":
                ui.input(
                    label="Volume Level (%)",
                    placeholder="50",
                    value=str(initial_config.get("volume_level", 50.0)),
                    on_change=lambda e: update_action_config(
                        action_index,
                        "volume_level",
                        float(e.value) if e.value else 50.0,
                        form_data,
                    ),
                ).classes("w-full mb-2 action-input")
                ui.label("Application volume level from 0-100%").classes(
                    "text-xs muted-text"
                )

    # Initialize app volume options
    current_mode = initial_config.get("action_mode", "set")
    show_app_volume_options(current_mode)

    # Platform notice
    # ui.label(
    #     "Application volume control is currently supported on Windows only."
    # ).classes("text-xs text-blue-500 mt-2")


def create_device_config(action_index: int, form_data: dict, initial_config: dict):
    """Create device-specific audio configuration"""
    if initial_config is None:
        initial_config = {}

    # Device Selection
    device_options = get_available_audio_devices()
    initial_device = initial_config.get("target_device", "")
    # Use default if initial value is empty or not in options
    if not initial_device or initial_device not in device_options:
        initial_device = (
            next(iter(device_options.keys())) if device_options else "default"
        )

    ui.select(
        options=device_options,
        label="Audio Device",
        value=initial_device,
        on_change=lambda e: update_action_config(
            action_index, "target_device", e.value, form_data
        ),
    ).classes("w-full mb-2 action-select")

    # Device type hint
    ui.label(
        "Select the audio device you want to control (speakers, microphone, etc.)"
    ).classes("text-xs muted-text mb-2")

    # Action Mode (reuse system volume config)
    create_system_volume_config(action_index, form_data, initial_config)


def create_application_config(action_index: int, form_data: dict, initial_config: dict):
    """Create application-specific audio configuration"""
    if initial_config is None:
        initial_config = {}

    # Application Selection
    app_options = get_available_applications()
    initial_app = initial_config.get("target_application", "")
    # Use default if initial value is empty or not in options
    if not initial_app or initial_app not in app_options:
        initial_app = next(iter(app_options.keys())) if app_options else "default"

    ui.select(
        options=app_options,
        label="Application",
        value=initial_app,
        on_change=lambda e: update_action_config(
            action_index, "target_application", e.value, form_data
        ),
    ).classes("w-full mb-2 action-select")

    # Application hint
    ui.label("Select the application whose volume you want to control").classes(
        "text-xs muted-text mb-2"
    )

    # Action Mode for Applications
    ui.select(
        options={
            "set": "Set Volume",
            "mute": "Mute Application",
            "unmute": "Unmute Application",
            "toggle_mute": "Toggle Mute",
        },
        label="Action",
        value=initial_config.get("action_mode", "set"),
        on_change=lambda e: [
            update_action_config(action_index, "action_mode", e.value, form_data),
            show_app_action_options(e.value, action_index, form_data, initial_config),
        ],
    ).classes("w-full mb-2 action-select")

    # App action options container
    app_action_options_container = ui.element("div").classes("w-full")

    def show_app_action_options(
        mode: str, action_index: int, form_data: dict, initial_config: dict
    ):
        """Show options specific to the selected app action mode"""
        app_action_options_container.clear()

        with app_action_options_container:
            if mode == "set":
                ui.input(
                    label="Volume Level (%)",
                    placeholder="50",
                    value=str(initial_config.get("volume_level", 50.0)),
                    on_change=lambda e: update_action_config(
                        action_index,
                        "volume_level",
                        float(e.value) if e.value else 50.0,
                        form_data,
                    ),
                ).classes("w-full mb-2 action-input")

                ui.label(
                    "Set the volume level for the selected application (0-100)"
                ).classes("text-xs muted-text")

    # Initialize with current action mode
    current_mode = initial_config.get("action_mode", "set")
    show_app_action_options(current_mode, action_index, form_data, initial_config)


def get_available_audio_devices() -> Dict[str, str]:
    """Get available audio devices on the current system"""
    try:
        import platform

        system = platform.system().lower()

        if system == "windows":
            return get_windows_audio_devices()
        elif system == "darwin":  # macOS
            return get_macos_audio_devices()
        elif system == "linux":
            return get_linux_audio_devices()
        else:
            return {"": "Default Device"}
    except Exception as e:
        logger.error(f"Error getting audio devices: {e}")
        return {"": "Default Device"}


def get_available_applications() -> Dict[str, str]:
    """Get available applications on the current system"""
    try:
        import platform

        system = platform.system().lower()

        if system == "windows":
            return get_windows_applications()
        elif system == "darwin":  # macOS
            return get_macos_applications()
        elif system == "linux":
            return get_linux_applications()
        else:
            return {"default": "Default Application"}
    except Exception as e:
        logger.error(f"Error getting applications: {e}")
        return {"default": "Default Application"}


def get_windows_audio_devices() -> Dict[str, str]:
    """Get Windows audio devices using pycaw with actual device names"""
    try:
        import warnings

        import comtypes
        from comtypes import CLSCTX_ALL
        from pycaw.constants import AudioDeviceState
        from pycaw.pycaw import AudioUtilities

        devices = {}
        seen_names = set()  # Track device names to avoid duplicates

        # Temporarily suppress pycaw warnings during device enumeration
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", module="pycaw.*", category=UserWarning)

            # Get all audio devices using the simpler AudioUtilities approach
            try:
                all_devices = AudioUtilities.GetAllDevices()

                for device in all_devices:
                    try:
                        # Only process enabled/active devices
                        if device.state != AudioDeviceState.Active:
                            continue

                        device_id = device.id

                        # Try to get friendly name, but don't fail if we can't
                        try:
                            friendly_name = device.FriendlyName
                            if not friendly_name:
                                continue
                        except Exception:
                            # Skip devices where we can't get the friendly name
                            continue

                        # Skip duplicates by name
                        if friendly_name in seen_names:
                            continue
                        seen_names.add(friendly_name)

                        # Determine if it's a playback or recording device based on name patterns
                        if any(
                            keyword in friendly_name.lower()
                            for keyword in [
                                "speaker",
                                "headphone",
                                "headset",
                                "hdmi",
                                "display",
                            ]
                        ):
                            devices[f"playback:{device_id}"] = f"[OUT] {friendly_name}"
                        elif any(
                            keyword in friendly_name.lower()
                            for keyword in ["microphone", "mic", "input", "line-in"]
                        ):
                            devices[f"recording:{device_id}"] = f"[MIC] {friendly_name}"

                    except Exception as e:
                        # Only log actual errors, not property access issues
                        if "COMError" not in str(e):
                            logger.warning(f"Error processing device: {e}")
                        continue

            except Exception as e:
                logger.warning(f"Error getting all devices: {e}")

        # If no devices found, provide fallback
        if not devices:
            devices = {"default": "Default Device"}

        return devices

    except ImportError:
        logger.warning("pycaw not available for Windows device enumeration")
        return {"default": "Default Device (pycaw not installed)"}
    except Exception as e:
        logger.error(f"Error getting Windows audio devices: {e}")
        return {"default": "Default Device (enumeration failed)"}


def get_macos_audio_devices() -> Dict[str, str]:
    """Get macOS audio devices with improved parsing"""
    try:
        import re
        import subprocess

        devices = {}

        # Use system_profiler to get audio devices
        result = subprocess.run(
            ["system_profiler", "SPAudioDataType"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            logger.warning("system_profiler failed, trying alternative method")
            return get_macos_audio_devices_fallback()

        lines = result.stdout.split("\n")
        current_section = None
        device_name = None
        device_type = None

        for line in lines:
            line = line.strip()

            # Check for section headers
            if "Audio Devices:" in line:
                current_section = "devices"
            elif "Output Devices:" in line:
                current_section = "output"
            elif "Input Devices:" in line:
                current_section = "input"
            elif line.startswith("Device Name:"):
                device_name = line.split(":", 1)[1].strip()
                device_type = "unknown"
            elif line.startswith("Default Output Device:"):
                default_device = line.split(":", 1)[1].strip()
                if default_device and default_device != "Yes":
                    devices[f"default_output:{default_device}"] = (
                        f"[OUT] {default_device} (Default)"
                    )
            elif line.startswith("Default Input Device:"):
                default_device = line.split(":", 1)[1].strip()
                if default_device and default_device != "Yes":
                    devices[f"default_input:{default_device}"] = (
                        f"[MIC] {default_device} (Default)"
                    )
            elif device_name and current_section:
                # Look for device type indicators
                if "Headphones" in device_name or "Headset" in device_name:
                    device_type = "headphones"
                elif "Speaker" in device_name or "Speakers" in device_name:
                    device_type = "speakers"
                elif "Microphone" in device_name or "Mic" in device_name:
                    device_type = "microphone"
                elif "Display" in device_name:
                    device_type = "display"

                # Add device with appropriate icon
                if device_type == "headphones":
                    devices[f"{current_section}:{device_name}"] = (
                        f"[HEAD] {device_name}"
                    )
                elif device_type == "microphone":
                    devices[f"{current_section}:{device_name}"] = f"[MIC] {device_name}"
                elif device_type == "speakers":
                    devices[f"{current_section}:{device_name}"] = f"[SPK] {device_name}"
                elif device_type == "display":
                    devices[f"{current_section}:{device_name}"] = f"[DSP] {device_name}"
                else:
                    # Generic device with section indicator
                    icon = "[OUT]" if current_section == "output" else "[MIC]"
                    devices[f"{current_section}:{device_name}"] = (
                        f"{icon} {device_name}"
                    )

                device_name = None  # Reset for next device

        # If we didn't find any devices, try fallback method
        if not devices:
            return get_macos_audio_devices_fallback()

        return devices

    except Exception as e:
        logger.error(f"Error getting macOS audio devices: {e}")
        return get_macos_audio_devices_fallback()


def get_macos_audio_devices_fallback() -> Dict[str, str]:
    """Fallback method for macOS audio device enumeration"""
    try:
        import subprocess

        devices = {"default": "Default Device"}

        # Try using audio device list command
        try:
            result = subprocess.run(
                ["audiodevice", "list"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                lines = result.stdout.split("\n")
                for line in lines:
                    if "Device:" in line:
                        device_info = line.split("Device:", 1)[1].strip()
                        devices[device_info] = device_info

        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        # Try using SwitchAudioSource if available
        try:
            result = subprocess.run(
                ["SwitchAudioSource", "-a"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                lines = result.stdout.split("\n")
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith("("):
                        # Extract device name (usually before the first parenthesis)
                        device_name = line.split("(")[0].strip()
                        if device_name:
                            devices[device_name] = device_name

        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        return devices

    except Exception as e:
        logger.error(f"Error in macOS audio device fallback: {e}")
        return {"default": "Default Device"}


def get_linux_audio_devices() -> Dict[str, str]:
    """Get Linux audio devices using pulsectl with improved formatting"""
    try:
        import pulsectl

        devices = {}

        with pulsectl.Pulse("device-enumeration") as pulse:
            # Get output devices (speakers)
            for sink in pulse.sink_list():
                device_name = sink.description or f"Sink {sink.index}"
                # Add appropriate icon based on device type
                if (
                    "headphone" in device_name.lower()
                    or "headset" in device_name.lower()
                ):
                    icon = "[HEAD]"
                elif "speaker" in device_name.lower():
                    icon = "[SPK]"
                elif "hdmi" in device_name.lower() or "display" in device_name.lower():
                    icon = "[DSP]"
                else:
                    icon = "[OUT]"
                devices[f"sink:{sink.index}"] = f"{icon} {device_name}"

            # Get input devices (microphones)
            for source in pulse.source_list():
                if not source.name.endswith(".monitor"):  # Skip monitor sources
                    device_name = source.description or f"Source {source.index}"
                    # Add microphone icon
                    if (
                        "microphone" in device_name.lower()
                        or "mic" in device_name.lower()
                    ):
                        icon = "[MIC]"
                    elif "webcam" in device_name.lower():
                        icon = "[CAM]"
                    else:
                        icon = "[MIC]"
                    devices[f"source:{source.index}"] = f"{icon} {device_name}"

        return devices if devices else {"default": "Default Device"}
    except ImportError:
        logger.warning("pulsectl not available for Linux device enumeration")
        return {"default": "Default Device (pulsectl not installed)"}
    except Exception as e:
        logger.error(f"Error getting Linux audio devices: {e}")
        return {"default": "Default Device (enumeration failed)"}


def get_windows_applications() -> Dict[str, str]:
    """Get Windows applications using psutil"""
    try:
        import psutil

        applications = {}

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"]:
                    app_name = proc.info["name"]
                    # Filter out system processes
                    if not any(
                        sys_proc in app_name.lower()
                        for sys_proc in [
                            "svchost",
                            "system",
                            "winlogon",
                            "csrss",
                            "smss",
                        ]
                    ):
                        applications[app_name] = app_name
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Sort alphabetically and limit to first 50 to avoid UI clutter
        sorted_apps = dict(sorted(applications.items())[:50])
        return sorted_apps
    except ImportError:
        logger.warning("psutil not available for Windows application enumeration")
        return {"default": "Default Application"}
    except Exception as e:
        logger.error(f"Error getting Windows applications: {e}")
        return {"default": "Default Application"}


def get_macos_applications() -> Dict[str, str]:
    """Get macOS applications using psutil"""
    try:
        import psutil

        applications = {}

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"]:
                    app_name = proc.info["name"]
                    # Filter out system processes
                    if not any(
                        sys_proc in app_name.lower()
                        for sys_proc in ["kernel", "launchd", "system", "windowserver"]
                    ):
                        applications[app_name] = app_name
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Sort alphabetically and limit to first 50 to avoid UI clutter
        sorted_apps = dict(sorted(applications.items())[:50])
        return sorted_apps
    except ImportError:
        logger.warning("psutil not available for macOS application enumeration")
        return {"default": "Default Application"}
    except Exception as e:
        logger.error(f"Error getting macOS applications: {e}")
        return {"default": "Default Application"}


def get_linux_applications() -> Dict[str, str]:
    """Get Linux applications using psutil"""
    try:
        import psutil

        applications = {}

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"]:
                    app_name = proc.info["name"]
                    # Filter out system processes
                    if not any(
                        sys_proc in app_name.lower()
                        for sys_proc in [
                            "systemd",
                            "init",
                            "kthreadd",
                            "ksoftirqd",
                            "migration",
                        ]
                    ):
                        applications[app_name] = app_name
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Sort alphabetically and limit to first 50 to avoid UI clutter
        sorted_apps = dict(sorted(applications.items())[:50])
        return sorted_apps
    except ImportError:
        logger.warning("psutil not available for Linux application enumeration")
        return {"default": "Default Application"}
    except Exception as e:
        logger.error(f"Error getting Linux applications: {e}")
        return {"default": "Default Application"}


def check_audio_permissions() -> Dict[str, Any]:
    """Check audio permissions for the current operating system"""
    try:
        import platform

        system = platform.system().lower()

        if system == "darwin":  # macOS
            return check_macos_audio_permissions()
        elif system == "windows":
            return check_windows_audio_permissions()
        elif system == "linux":
            return check_linux_audio_permissions()
        else:
            return {
                "has_permissions": True,
                "message": "Unknown operating system",
                "can_request": False,
            }
    except Exception as e:
        logger.error(f"Error checking audio permissions: {e}")
        return {
            "has_permissions": False,
            "message": f"Error checking permissions: {e}",
            "can_request": False,
        }


def check_macos_audio_permissions() -> Dict[str, Any]:
    """Check macOS microphone and audio permissions"""
    try:
        # Check if we can access microphone using system_profiler
        import subprocess

        result = subprocess.run(
            ["system_profiler", "SPMicrophoneDataType"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode == 0 and "No microphone" not in result.stdout:
            return {
                "has_permissions": True,
                "message": "Microphone access available",
                "can_request": False,  # Permissions are granted automatically when first requested
            }
        else:
            return {
                "has_permissions": False,
                "message": "Microphone access may be restricted. Check System Preferences > Security & Privacy > Microphone",
                "can_request": False,
            }
    except Exception as e:
        logger.error(f"Error checking macOS audio permissions: {e}")
        return {
            "has_permissions": False,
            "message": "Unable to check microphone permissions",
            "can_request": False,
        }


def check_windows_audio_permissions() -> Dict[str, Any]:
    """Check Windows audio permissions and administrator status"""
    try:
        import ctypes

        # Check if running as administrator
        is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0

        if is_admin:
            return {
                "has_permissions": True,
                "message": "Running as administrator - full audio control available",
                "can_request": False,
                "is_admin": True,
            }
        else:
            return {
                "has_permissions": False,
                "message": "Not running as administrator. Some audio operations may be restricted.",
                "can_request": True,
                "is_admin": False,
                "request_message": "Restart the application as administrator to enable full audio control",
            }
    except Exception as e:
        logger.error(f"Error checking Windows audio permissions: {e}")
        return {
            "has_permissions": False,
            "message": "Unable to check administrator status",
            "can_request": False,
        }


def check_linux_audio_permissions() -> Dict[str, Any]:
    """Check Linux audio permissions"""
    try:
        import grp
        import os
        import pwd

        # Get current user
        current_user = pwd.getpwuid(os.getuid()).pw_name

        # Check if user is in audio group
        try:
            audio_group = grp.getgrnam("audio")
            user_in_audio_group = current_user in audio_group.gr_mem
        except KeyError:
            # Audio group doesn't exist
            user_in_audio_group = False

        if user_in_audio_group:
            return {
                "has_permissions": True,
                "message": "User is in audio group - audio access available",
                "can_request": False,
            }
        else:
            return {
                "has_permissions": False,
                "message": "User is not in the 'audio' group. Audio device access may be restricted.",
                "can_request": True,
                "request_message": f"Add user to audio group: 'sudo usermod -a -G audio {current_user}' then restart the application",
            }
    except Exception as e:
        logger.error(f"Error checking Linux audio permissions: {e}")
        return {
            "has_permissions": False,
            "message": "Unable to check audio group membership",
            "can_request": False,
        }


def request_audio_permissions() -> bool:
    """Request audio permissions for the current operating system"""
    try:
        import platform

        system = platform.system().lower()

        if system == "darwin":  # macOS
            return request_macos_audio_permissions()
        elif system == "windows":
            return request_windows_audio_permissions()
        elif system == "linux":
            return request_linux_audio_permissions()
        else:
            logger.warning(f"Permission request not supported on {system}")
            return False
    except Exception as e:
        logger.error(f"Error requesting audio permissions: {e}")
        return False


def request_macos_audio_permissions() -> bool:
    """Request macOS audio permissions - opens System Preferences"""
    try:
        import subprocess

        # Try to open System Preferences to Microphone pane
        script = """
        tell application "System Preferences"
            activate
            reveal anchor "Privacy_Microphone" of pane id "com.apple.preference.security"
        end tell
        """

        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            logger.info("Opened macOS System Preferences for microphone permissions")
            return True
        else:
            logger.error(f"Failed to open System Preferences: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error requesting macOS permissions: {e}")
        return False


def request_windows_audio_permissions() -> bool:
    """Request Windows audio permissions by attempting to run as admin"""
    try:
        import os
        import subprocess
        import sys

        # Try to relaunch as administrator
        script = f"""
        If (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {{
            $arguments = "& '{sys.executable}'"
            foreach ($arg in $args) {{
                $arguments += " '$arg'"
            }}
            Start-Process powershell -Verb runAs -ArgumentList $arguments
            Exit
        }}
        """

        # For now, just show a message
        logger.info(
            "To run as administrator, right-click the application and select 'Run as administrator'"
        )
        return True
    except Exception as e:
        logger.error(f"Error requesting Windows permissions: {e}")
        return False


def request_linux_audio_permissions() -> bool:
    """Request Linux audio permissions by showing instructions"""
    try:
        import os
        import pwd

        current_user = pwd.getpwuid(os.getuid()).pw_name
        command = f"sudo usermod -a -G audio {current_user}"

        logger.info(f"To add audio permissions, run: {command}")
        logger.info("Then restart the application")

        # Try to run the command if we have sudo access
        import subprocess

        result = subprocess.run(
            ["sudo", "usermod", "-a", "-G", "audio", current_user],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            logger.info("Successfully added user to audio group")
            return True
        else:
            logger.warning(f"Failed to add user to audio group: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error requesting Linux permissions: {e}")
        return False


def create_template_control_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for template control action"""
    if initial_config is None:
        initial_config = {}

    # Get available templates and their actions
    try:
        integration = connector_integration.get_integration()
        template_actions = integration.get_available_template_actions()
        logger.debug(f"Template actions received in UI: {template_actions}")

        template_options = {name: name.title() for name in template_actions.keys()}

        initial_template = initial_config.get("template_name") or None
        initial_action = initial_config.get("control_action") or None

        ui.select(
            options=template_options,
            label="Template",
            value=initial_template,
            on_change=lambda e: handle_template_change(
                e.value, action_index, form_data, action_select, action_params_container
            ),
        ).classes("w-full mb-2 action-select")

        # Get initial action options if template is selected
        initial_action_options = {}
        if initial_template and initial_template in template_actions:
            actions = template_actions[initial_template]
            # actions is now a dict of {action_id: action_config}
            initial_action_options = {
                action_id: action_config.get("action_name", action_id)
                for action_id, action_config in actions.items()
            }
            logger.debug(
                f"Initial template '{initial_template}' has actions: {initial_action_options}"
            )
        else:
            logger.debug(
                f"No initial template or template '{initial_template}' not found in template_actions"
            )

        action_select = ui.select(
            options=initial_action_options,
            label="Action",
            value=initial_action,
            on_change=lambda e: handle_action_change(
                e.value, action_index, form_data, action_params_container
            ),
        ).classes("w-full mb-2 action-select")

        # Container for action-specific parameters
        action_params_container = ui.element("div").classes("w-full mt-3")

        def handle_template_change(
            template_name,
            action_index,
            form_data,
            action_select,
            action_params_container,
        ):
            logger.debug(
                f"handle_template_change called with template_name: {template_name}"
            )
            # Update the form data
            update_action_config(
                action_index, "template_name", template_name, form_data
            )

            # Clear action parameters container
            action_params_container.clear()

            # Update the action options
            if template_name and template_name in template_actions:
                actions = template_actions[template_name]
                logger.debug(f"Found actions for {template_name}: {actions}")
                # actions is now a dict of {action_id: action_config}
                action_options = {
                    action_id: action_config.get("action_name", action_id)
                    for action_id, action_config in actions.items()
                }
                logger.debug(f"Setting action_options: {action_options}")
                action_select.options = action_options
                action_select.update()
                # Clear the current action selection since template changed
                action_select.value = None
                update_action_config(action_index, "control_action", None, form_data)
                # Update the action select handler with the new template
                action_select.on_value_change = lambda e: handle_action_change(
                    e.value, action_index, form_data, action_params_container
                )
            else:
                logger.debug(
                    f"No actions found for template {template_name} or template not in template_actions"
                )
                logger.debug(f"Available templates: {list(template_actions.keys())}")
                action_select.options = {}
                action_select.update()
                action_select.value = None
                update_action_config(action_index, "control_action", None, form_data)

        def handle_action_change(
            action_name, action_index, form_data, action_params_container
        ):
            logger.debug(f"handle_action_change called with action_name: {action_name}")
            # Update the form data
            update_action_config(action_index, "control_action", action_name, form_data)

            # Clear previous parameters
            action_params_container.clear()

            # Get the current template name from form data
            current_template = (
                form_data.get("actions", [{}])[action_index]
                .get("config", {})
                .get("template_name")
            )

            # Create action-specific parameters
            if current_template and action_name:
                create_template_action_params(
                    current_template,
                    action_name,
                    action_index,
                    form_data,
                    action_params_container,
                    initial_config,
                )

        # Initialize with existing data if present
        if initial_template and initial_action:
            create_template_action_params(
                initial_template,
                initial_action,
                action_index,
                form_data,
                action_params_container,
                initial_config,
            )

    except Exception as e:
        logger.error(f"Error creating template control config: {e}")
        ui.label("Error loading template actions").classes("text-red-400")


def create_template_action_params(
    template_name: str,
    action_name: str,
    action_index: int,
    form_data: dict,
    container,
    initial_config: dict = None,
):
    """Create template-specific action parameters UI from JSON configuration"""
    if initial_config is None:
        initial_config = {}

    logger.debug(f"Creating template action params for {template_name}.{action_name}")

    with container:
        try:
            # Get the connector integration to access template configs
            integration = connector_integration.get_integration()
            template_actions = integration.get_available_template_actions()

            # Get the specific action configuration
            if (
                template_name in template_actions
                and action_name in template_actions[template_name]
            ):
                action_config = template_actions[template_name][action_name]
                elements = action_config.get("elements", [])

                if not elements:
                    ui.label("No additional configuration needed").classes(
                        "text-sm secondary-text"
                    )
                    return

                # Generate UI elements from JSON configuration
                for element in elements:
                    create_element_from_config(
                        element, action_index, form_data, initial_config
                    )
            else:
                ui.label("Action configuration not found").classes(
                    "text-sm text-red-400"
                )

        except Exception as e:
            logger.error(f"Error creating template action params: {e}", exc_info=True)
            ui.label("Error loading action configuration").classes(
                "text-sm text-red-400"
            )


def create_element_from_config(
    element_config: dict, action_index: int, form_data: dict, initial_config: dict
):
    """Create a UI element from JSON configuration"""
    element_type = element_config.get("type")
    element_id = element_config.get("id")
    label = element_config.get("label", "")
    description = element_config.get("description", "")
    value = initial_config.get(element_id, element_config.get("value"))

    if element_type == "text":
        ui.input(
            label=label,
            placeholder=element_config.get("placeholder", ""),
            value=str(value) if value is not None else "",
            on_change=lambda e, eid=element_id: update_action_config(
                action_index, eid, e.value, form_data
            ),
        ).classes("w-full mb-2 action-input")

    elif element_type == "number":
        min_val = element_config.get("min", 0)
        max_val = element_config.get("max", 1000)
        ui.input(
            label=label,
            placeholder=str(value) if value is not None else "",
            value=str(value) if value is not None else "",
            validation={"min": min_val, "max": max_val},
            on_change=lambda e, eid=element_id: update_action_config(
                action_index,
                eid,
                int(e.value)
                if e.value and e.value.lstrip("-").isdigit()
                else element_config.get("value", 0),
                form_data,
            ),
        ).classes("w-full mb-2 action-input")

    elif element_type == "select":
        options = element_config.get("options", [])
        if isinstance(options, list):
            # Convert list to dict for NiceGUI select
            option_dict = {str(opt): str(opt).title() for opt in options}
        else:
            option_dict = options

        ui.select(
            options=option_dict,
            label=label,
            value=str(value) if value is not None else "",
            on_change=lambda e, eid=element_id: update_action_config(
                action_index, eid, e.value, form_data
            ),
        ).classes("w-full mb-2 action-select")

    elif element_type == "switch":
        ui.switch(
            text=label,
            value=bool(value) if value is not None else False,
            on_change=lambda e, eid=element_id: update_action_config(
                action_index, eid, e.value, form_data
            ),
        ).classes("w-full mb-2")

    elif element_type == "separator":
        ui.label(label).classes("text-base font-medium secondary-text mb-2 mt-4")

    else:
        logger.warning(f"Unknown element type: {element_type}")
        ui.label(f"Unsupported element type: {element_type}").classes(
            "text-sm text-yellow-400"
        )

    # Add description if provided
    if description:
        ui.label(description).classes("text-xs muted-text mb-2")


def create_websocket_emit_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for WebSocket emit action"""
    if initial_config is None:
        initial_config = {}

    ui.input(
        label="Event Name",
        placeholder="custom_event",
        value=initial_config.get("event_name", ""),
        on_change=lambda e: update_action_config(
            action_index, "event_name", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.textarea(
        label="Event Data (JSON)",
        placeholder='{"key": "value", "message": "{{username}} triggered this!"}',
        value=initial_config.get("event_data", ""),
        on_change=lambda e: update_action_config(
            action_index, "event_data", e.value, form_data
        ),
    ).classes("w-full action-input")


def create_chat_message_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for chat message action"""
    if initial_config is None:
        initial_config = {}

    ui.textarea(
        label="Message",
        placeholder="Thanks {{username}} for the {{amount}} bits!",
        value=initial_config.get("message", ""),
        on_change=lambda e: update_action_config(
            action_index, "message", e.value, form_data
        ),
    ).classes("w-full action-input")

    ui.label("Use {{field}} placeholders to insert event data").classes(
        "text-xs muted-text"
    )


def create_write_file_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for write file action"""
    if initial_config is None:
        initial_config = {}

    ui.input(
        label="File Path",
        placeholder="logs/{{event_type}}.log",
        value=initial_config.get("file_path", ""),
        on_change=lambda e: update_action_config(
            action_index, "file_path", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.textarea(
        label="Content",
        placeholder="{{timestamp}}: {{username}} - {{message}}",
        value=initial_config.get("content", ""),
        on_change=lambda e: update_action_config(
            action_index, "content", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.switch(
        text="Append to file",
        value=initial_config.get("append", False),
        on_change=lambda e: update_action_config(
            action_index, "append", e.value, form_data
        ),
    ).classes("w-full")


def create_add_greeting_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for add greeting action"""
    if initial_config is None:
        initial_config = {}

    ui.input(
        label="User ID",
        placeholder="{{user_id}}",
        value=initial_config.get("user_id", ""),
        on_change=lambda e: update_action_config(
            action_index, "user_id", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.input(
        label="Username",
        placeholder="{{username}}",
        value=initial_config.get("username", ""),
        on_change=lambda e: update_action_config(
            action_index, "username", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.textarea(
        label="Greeting Text",
        placeholder="Welcome to the stream, {{username}}!",
        value=initial_config.get("greeting_text", ""),
        on_change=lambda e: update_action_config(
            action_index, "greeting_text", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.switch(
        text="Enabled",
        value=initial_config.get("enabled", True),
        on_change=lambda e: update_action_config(
            action_index, "enabled", e.value, form_data
        ),
    ).classes("w-full")

    ui.label("Use {{field}} placeholders to insert event data").classes(
        "text-xs muted-text"
    )


def create_update_greeting_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for update greeting action"""
    if initial_config is None:
        initial_config = {}

    ui.input(
        label="Greeting ID",
        placeholder="{{greeting_id}}",
        value=initial_config.get("greeting_id", ""),
        on_change=lambda e: update_action_config(
            action_index, "greeting_id", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.textarea(
        label="New Greeting Text (optional)",
        placeholder="Updated greeting for {{username}}",
        value=initial_config.get("greeting_text", ""),
        on_change=lambda e: update_action_config(
            action_index, "greeting_text", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.switch(
        text="Enabled",
        value=initial_config.get("enabled", None),
        on_change=lambda e: update_action_config(
            action_index, "enabled", e.value, form_data
        ),
    ).classes("w-full")

    ui.label("Leave greeting text empty to only update enabled status").classes(
        "text-xs muted-text"
    )


def create_send_greeting_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for send greeting action"""
    if initial_config is None:
        initial_config = {}

    ui.input(
        label="User ID",
        placeholder="{{user_id}}",
        value=initial_config.get("user_id", ""),
        on_change=lambda e: update_action_config(
            action_index, "user_id", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.input(
        label="Username",
        placeholder="{{username}}",
        value=initial_config.get("username", ""),
        on_change=lambda e: update_action_config(
            action_index, "username", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.switch(
        text="Force Send (ignore greeting cooldown)",
        value=initial_config.get("force_send", True),
        on_change=lambda e: update_action_config(
            action_index, "force_send", e.value, form_data
        ),
    ).classes("w-full")

    ui.label("Use {{field}} placeholders to insert event data").classes(
        "text-xs muted-text"
    )


def create_trigger_alert_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for trigger alert action"""
    if initial_config is None:
        initial_config = {}

    # Alert type selection
    alert_types = {
        "follow": "Follow Alert",
        "subscription": "Subscription Alert",
        "bits": "Bits Alert",
        "donation": "Donation Alert",
        "raid": "Raid Alert",
        "host": "Host Alert",
        "custom": "Custom Alert",
    }

    ui.select(
        options=alert_types,
        label="Alert Type",
        value=initial_config.get("alert_type")
        if initial_config.get("alert_type")
        else None,
        on_change=lambda e: update_action_config(
            action_index, "alert_type", e.value, form_data
        ),
    ).classes("w-full mb-2 action-select")

    # Quantity/amount
    ui.input(
        label="Amount/Quantity",
        placeholder="e.g., 100 (for bits), 1 (for follows)",
        value=str(
            initial_config.get("amount", "")
            if initial_config.get("amount") is not None
            else ""
        ),
        on_change=lambda e: update_action_config(
            action_index, "amount", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    # Optional custom message
    ui.input(
        label="Custom Message (optional)",
        placeholder="Custom alert message",
        value=initial_config.get("message", ""),
        on_change=lambda e: update_action_config(
            action_index, "message", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.label("Configure the type and amount for the alert to trigger").classes(
        "text-xs muted-text"
    )


def create_api_call_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for API call action"""
    if initial_config is None:
        initial_config = {}

    # HTTP Method
    http_methods = {
        "GET": "GET",
        "POST": "POST",
        "PUT": "PUT",
        "DELETE": "DELETE",
        "PATCH": "PATCH",
    }

    ui.select(
        options=http_methods,
        label="HTTP Method",
        value=initial_config.get("method") or "GET",
        on_change=lambda e: update_action_config(
            action_index, "method", e.value, form_data
        ),
    ).classes("w-full mb-2 action-select")

    # URL
    ui.input(
        label="URL",
        placeholder="https://api.example.com/endpoint",
        value=initial_config.get("url", ""),
        on_change=lambda e: update_action_config(
            action_index, "url", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    # Headers
    ui.textarea(
        label="Headers (JSON)",
        placeholder='{"Content-Type": "application/json", "Authorization": "Bearer {{token}}"}',
        value=initial_config.get("headers", ""),
        on_change=lambda e: update_action_config(
            action_index, "headers", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    # Request Body
    ui.textarea(
        label="Request Body (JSON)",
        placeholder='{"message": "{{username}} triggered this!", "amount": {{amount}}}',
        value=initial_config.get("body", ""),
        on_change=lambda e: update_action_config(
            action_index, "body", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.label("Use {{field}} placeholders to insert event data").classes(
        "text-xs muted-text"
    )


def create_execute_command_config(
    action_index: int, form_data: dict, initial_config: dict = None
):
    """Create configuration for execute command action"""
    if initial_config is None:
        initial_config = {}

    # Command to execute
    ui.input(
        label="Command",
        placeholder="echo 'Hello {{username}}!'",
        value=initial_config.get("command", ""),
        on_change=lambda e: update_action_config(
            action_index, "command", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    # Working directory (optional)
    ui.input(
        label="Working Directory (optional)",
        placeholder="/path/to/directory",
        value=initial_config.get("working_directory", ""),
        on_change=lambda e: update_action_config(
            action_index, "working_directory", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    # Timeout
    ui.input(
        label="Timeout (seconds)",
        placeholder="30",
        value=str(initial_config.get("timeout", 30)),
        on_change=lambda e: update_action_config(
            action_index, "timeout", e.value, form_data
        ),
    ).classes("w-full mb-2 action-input")

    ui.label(
        "Use {{field}} placeholders to insert event data. Be careful with command execution!"
    ).classes("text-xs muted-text")


def update_action_config(action_index: int, key: str, value: Any, form_data: dict):
    """Update action configuration"""
    if "actions" not in form_data:
        form_data["actions"] = []
    if action_index >= len(form_data["actions"]):
        return
    if "config" not in form_data["actions"][action_index]:
        form_data["actions"][action_index]["config"] = {}

    form_data["actions"][action_index]["config"][key] = value


def update_condition_field(index: int, field: str, form_data: dict):
    """Update condition field"""
    if "trigger_conditions" in form_data and index < len(
        form_data["trigger_conditions"]
    ):
        form_data["trigger_conditions"][index]["field"] = field


def update_condition_operator(index: int, operator: str, form_data: dict):
    """Update condition operator"""
    if "trigger_conditions" in form_data and index < len(
        form_data["trigger_conditions"]
    ):
        form_data["trigger_conditions"][index]["operator"] = operator


def update_condition_value(index: int, value: str, form_data: dict):
    """Update condition value"""
    if "trigger_conditions" in form_data and index < len(
        form_data["trigger_conditions"]
    ):
        form_data["trigger_conditions"][index]["value"] = value


def remove_condition(index: int, form_data: dict, conditions_list):
    """Remove a condition"""
    if "trigger_conditions" in form_data and index < len(
        form_data["trigger_conditions"]
    ):
        form_data["trigger_conditions"].pop(index)
        # Rebuild the conditions list
        conditions_list.clear()
        # This would need to be implemented to refresh the display


def remove_action(index: int, form_data: dict, actions_container):
    """Remove an action"""
    if "actions" in form_data and index < len(form_data["actions"]):
        form_data["actions"].pop(index)
        # Rebuild the actions container
        actions_container.clear()
        # This would need to be implemented to refresh the display


def close_dialog_and_refresh():
    """Close the create dialog and refresh the connectors list"""
    global create_dialog, current_search
    try:
        logger.info(
            f"Closing dialog and refreshing connectors - create_dialog exists: {create_dialog is not None}"
        )
        if create_dialog:
            logger.info("Attempting to close dialog...")
            create_dialog.close()
            logger.info("Dialog close() called successfully")
        else:
            logger.warning("create_dialog is None, cannot close")

        logger.info("Refreshing connectors...")
        refresh_connectors()
        logger.info("Successfully closed dialog and refreshed")
    except Exception as e:
        logger.error(f"Error closing dialog and refreshing: {e}", exc_info=True)


def save_connector(form_data: dict):
    """Save the connector (create new or update existing)"""
    is_edit = form_data.get("connector_id") is not None

    if is_edit:
        save_updated_connector(form_data)
    else:
        save_new_connector(form_data)


def save_new_connector(form_data: dict):
    """Save the new connector"""
    try:
        # Validate form data
        if not form_data.get("name"):
            ui.notify("Connector name is required", type="negative")
            return

        if not form_data.get("trigger_type"):
            ui.notify("Trigger type is required", type="negative")
            return

        if not form_data.get("actions"):
            ui.notify("At least one action is required", type="negative")
            return

        # Create trigger
        logger.info(f"Creating trigger of type: {form_data['trigger_type']}")
        trigger_type = TriggerType(form_data["trigger_type"])
        trigger_id = str(uuid.uuid4())
        logger.info(f"Generated trigger_id: {trigger_id}")

        # Create conditions
        conditions = []
        for cond_data in form_data.get("trigger_conditions", []):
            if cond_data.get("field") and cond_data.get("operator"):
                try:
                    operator = ComparisonOperator(cond_data["operator"])
                    condition = TriggerCondition(
                        field=cond_data["field"],
                        operator=operator,
                        value=cond_data["value"],
                    )
                    conditions.append(condition)
                except Exception as e:
                    logger.warning(f"Invalid condition: {e}")

        # Create trigger with trigger-specific configuration
        logger.info(f"About to create trigger with conditions: {conditions}")
        trigger_params = {
            "trigger_type": trigger_type,
            "trigger_id": trigger_id,
            "name": f"{form_data['name']} Trigger",
            "conditions": conditions,
        }

        # Add trigger-specific configuration
        if trigger_type == TriggerType.HOTKEY:
            trigger_config = form_data.get("trigger_config", {})
            trigger_params.update(
                {
                    "key_combination": trigger_config.get("key_combination", ""),
                    "is_global": trigger_config.get("is_global", True),
                }
            )

        trigger = connector_triggers.create_trigger(**trigger_params)
        logger.info(f"Successfully created trigger: {trigger}")

        # Create actions
        logger.info(f"Creating {len(form_data.get('actions', []))} actions")
        actions = []
        for i, action_data in enumerate(form_data.get("actions", [])):
            logger.info(f"Creating action {i+1}: {action_data}")
            action_type = ActionType(action_data["type"])
            action_id = str(uuid.uuid4())

            # Create action with configuration
            action_config = action_data.get("config", {})
            logger.info(f"Action config for action {i+1}: {action_config}")

            # For template control actions, map template-specific parameters to control_data
            if action_type == ActionType.TEMPLATE_CONTROL:
                control_data = {}
                template_name = action_config.get("template_name", "")
                control_action = action_config.get("control_action", "")

                # Extract template-specific parameters (everything except template_name and control_action)
                for key, value in action_config.items():
                    if key not in ["template_name", "control_action"]:
                        control_data[key] = value

                action = connector_actions.create_action(
                    action_type=action_type,
                    action_id=action_id,
                    name=f"{form_data['name']} Action {i+1}",
                    template_name=template_name,
                    control_action=control_action,
                    control_data=control_data,
                )
            else:
                action = connector_actions.create_action(
                    action_type=action_type,
                    action_id=action_id,
                    name=f"{form_data['name']} Action {i+1}",
                    **action_config,
                )
            logger.info(f"Successfully created action {i+1}: {action}")
            actions.append(action)

        # Create connector
        logger.info("Creating final Connector object")
        connector = Connector(
            connector_id=str(uuid.uuid4()),
            name=form_data["name"],
            description=form_data.get("description", ""),
            trigger=trigger,
            actions=actions,
        )
        logger.info(f"Successfully created Connector: {connector}")

        # Save connector
        logger.info("Getting connector manager and saving")
        manager = connector_manager.get_manager()
        success = manager.add_connector(connector)
        logger.info(f"Save result: {success}")

        if success:
            ui.notify(
                f"Connector '{connector.name}' created successfully", type="positive"
            )
            # Direct execution for testing
            logger.info("About to close dialog and refresh")
            close_dialog_and_refresh()
            logger.info("Finished close dialog and refresh")
        else:
            ui.notify("Failed to create connector", type="negative")

    except Exception as e:
        logger.error(f"Error saving connector: {e}", exc_info=True)
        ui.notify(f"Error creating connector: {str(e)}", type="negative")


def save_updated_connector(form_data: dict):
    """Save the updated connector"""
    try:
        connector_id = form_data.get("connector_id")
        if not connector_id:
            ui.notify("Invalid connector ID", type="negative")
            return

        # Validate form data
        if not form_data.get("name"):
            ui.notify("Connector name is required", type="negative")
            return

        if not form_data.get("trigger_type"):
            ui.notify("Trigger type is required", type="negative")
            return

        if not form_data.get("actions"):
            ui.notify("At least one action is required", type="negative")
            return

        # Get the manager and existing connector
        manager = connector_manager.get_manager()
        existing_connector = manager.get_connector(connector_id)

        if not existing_connector:
            ui.notify("Connector not found", type="negative")
            return

        # Create updated trigger
        logger.info(f"Updating trigger of type: {form_data['trigger_type']}")
        trigger_type = TriggerType(form_data["trigger_type"])
        trigger_id = (
            existing_connector.trigger.trigger_id
            if existing_connector.trigger
            else str(uuid.uuid4())
        )

        # Create conditions
        conditions = []
        for cond_data in form_data.get("trigger_conditions", []):
            if cond_data.get("field") and cond_data.get("operator"):
                try:
                    operator = ComparisonOperator(cond_data["operator"])
                    condition = TriggerCondition(
                        field=cond_data["field"],
                        operator=operator,
                        value=cond_data["value"],
                    )
                    conditions.append(condition)
                except Exception as e:
                    logger.warning(f"Invalid condition: {e}")

        # Create updated trigger with trigger-specific configuration
        trigger_params = {
            "trigger_type": trigger_type,
            "trigger_id": trigger_id,
            "name": f"{form_data['name']} Trigger",
            "conditions": conditions,
        }

        # Add trigger-specific configuration
        if trigger_type == TriggerType.HOTKEY:
            trigger_config = form_data.get("trigger_config", {})
            trigger_params.update(
                {
                    "key_combination": trigger_config.get("key_combination", ""),
                    "is_global": trigger_config.get("is_global", True),
                }
            )

        trigger = connector_triggers.create_trigger(**trigger_params)

        # Create updated actions
        actions = []
        for i, action_data in enumerate(form_data.get("actions", [])):
            action_type = ActionType(action_data["type"])
            action_id = str(uuid.uuid4())

            # Create action with configuration
            action_config = action_data.get("config", {})

            # For template control actions, map template-specific parameters to control_data
            if action_type == ActionType.TEMPLATE_CONTROL:
                control_data = {}
                template_name = action_config.get("template_name", "")
                control_action = action_config.get("control_action", "")

                # Extract template-specific parameters (everything except template_name and control_action)
                for key, value in action_config.items():
                    if key not in ["template_name", "control_action"]:
                        control_data[key] = value

                action = connector_actions.create_action(
                    action_type=action_type,
                    action_id=action_id,
                    name=f"{form_data['name']} Action {i+1}",
                    template_name=template_name,
                    control_action=control_action,
                    control_data=control_data,
                )
            else:
                action = connector_actions.create_action(
                    action_type=action_type,
                    action_id=action_id,
                    name=f"{form_data['name']} Action {i+1}",
                    **action_config,
                )
            actions.append(action)

        # Create updated connector
        updated_connector = Connector(
            connector_id=connector_id,
            name=form_data["name"],
            description=form_data.get("description", ""),
            trigger=trigger,
            actions=actions,
            enabled=existing_connector.enabled,  # Preserve enabled state
            trigger_count=existing_connector.trigger_count,  # Preserve statistics
            last_triggered=existing_connector.last_triggered,
        )

        # Update connector
        success = manager.update_connector(connector_id, updated_connector)

        if success:
            ui.notify(
                f"Connector '{updated_connector.name}' updated successfully",
                type="positive",
            )
            close_dialog_and_refresh()
        else:
            ui.notify("Failed to update connector", type="negative")

    except Exception as e:
        logger.error(f"Error updating connector: {e}", exc_info=True)
        ui.notify(f"Error updating connector: {str(e)}", type="negative")


def show_edit_connector_dialog(connector_id: str):
    """Show the edit connector dialog"""
    show_connector_dialog(connector_id)


def test_connector(connector_id: str):
    """Test a connector with sample data"""
    try:
        manager = connector_manager.get_manager()
        connector = manager.get_connector(connector_id)

        if not connector:
            ui.notify("Connector not found", type="negative")
            return

        # Create sample test data based on trigger type
        test_data = create_test_data_for_trigger(connector.trigger.trigger_type)

        # Test the connector
        async def run_test():
            result = await manager.test_connector(connector_id, test_data)
            if result.get("success"):
                if result.get("triggered"):
                    ui.notify(
                        f"Test successful: Connector triggered and executed {result.get('action_count', 0)} actions",
                        type="positive",
                    )
                else:
                    ui.notify(
                        "Test completed: Connector did not trigger (conditions not met)",
                        type="info",
                    )
            else:
                ui.notify(
                    f"Test failed: {result.get('error', 'Unknown error')}",
                    type="negative",
                )

        asyncio.create_task(run_test())

    except Exception as e:
        logger.error(f"Error testing connector: {e}", exc_info=True)
        ui.notify(f"Error testing connector: {str(e)}", type="negative")


def create_test_data_for_trigger(trigger_type: TriggerType) -> Dict[str, Any]:
    """Create sample test data for a trigger type"""
    base_data = {"timestamp": time.time(), "username": "TestUser", "source": "test"}

    if trigger_type == TriggerType.TWITCH_BITS:
        return {
            **base_data,
            "event_type": "twitch_bits",
            "amount": 100,
            "message": "Test bits!",
        }
    elif trigger_type == TriggerType.TWITCH_SUB:
        return {
            **base_data,
            "event_type": "twitch_sub",
            "tier": 1,
            "months": 1,
            "message": "Test sub!",
        }
    elif trigger_type == TriggerType.TWITCH_FOLLOW:
        return {**base_data, "event_type": "twitch_follow"}
    elif trigger_type == TriggerType.TWITCH_CHAT_MESSAGE:
        return {
            **base_data,
            "event_type": "twitch_chat_message",
            "message": "Hello test!",
        }

    elif trigger_type == TriggerType.DONATION:
        return {
            **base_data,
            "event_type": "donation",
            "amount": 5.0,
            "currency": "USD",
            "message": "Test donation!",
        }
    elif trigger_type == TriggerType.HOTKEY:
        return {
            **base_data,
            "event_type": "hotkey",
            "key_code": "f12",
            "modifiers": ["ctrl"],
            "is_global": True,
        }
    else:
        return {**base_data, "event_type": "unknown"}


def toggle_connector(connector_id: str, enabled: bool):
    """Toggle a connector's enabled state"""
    try:
        manager = connector_manager.get_manager()
        success = manager.toggle_connector(connector_id)

        if success:
            status = "enabled" if enabled else "disabled"
            ui.notify(f"Connector {status}", type="positive")
            refresh_connectors()
        else:
            ui.notify("Failed to toggle connector", type="negative")
    except Exception as e:
        logger.error(f"Error toggling connector: {e}", exc_info=True)
        ui.notify(f"Error toggling connector: {str(e)}", type="negative")


def delete_connector(connector_id: str):
    """Delete a connector with confirmation"""

    def confirm_delete():
        try:
            manager = connector_manager.get_manager()
            success = manager.remove_connector(connector_id)

            if success:
                ui.notify("Connector deleted", type="positive")
                # Remove from card references before refreshing
                if connector_id in connector_cards:
                    del connector_cards[connector_id]
                refresh_connectors()
            else:
                ui.notify("Failed to delete connector", type="negative")
        except Exception as e:
            logger.error(f"Error deleting connector: {e}", exc_info=True)
            ui.notify(f"Error deleting connector: {str(e)}", type="negative")

    # Show confirmation dialog
    with ui.dialog().props("persistent") as dialog:
        with ui.card():
            ui.label("Confirm Delete").classes("text-lg font-semibold mb-2")
            ui.label(
                "Are you sure you want to delete this connector? This action cannot be undone."
            ).classes("mb-4")

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button(
                    "Delete", on_click=lambda: [confirm_delete(), dialog.close()]
                ).classes("btn-danger")

    dialog.open()


def show_help_dialog():
    """Show help information about connectors"""
    with ui.dialog().props("maximized") as help_dialog:
        with ui.card().classes("w-full h-full"):
            with ui.column().classes("w-full h-full"):
                # Header
                with ui.row().classes(
                    "w-full items-center justify-between p-4 border-b border-gray-700"
                ):
                    ui.label("Connectors Help & Guide").classes(
                        "text-xl font-semibold text-theme-primary"
                    )
                    ui.button(icon="close", on_click=help_dialog.close).props(
                        "flat round"
                    ).classes("secondary-text")

                # Content
                with ui.scroll_area().classes("flex-grow p-6"):
                    with ui.column().classes("w-full max-w-4xl mx-auto gap-6"):
                        # What are Connectors?
                        with ui.element("div").classes("form-section"):
                            ui.label("What are Connectors?").classes(
                                "text-lg font-semibold text-theme-primary mb-3"
                            )
                            ui.label("""
Connectors are automated trigger-action systems that respond to events in your stream. 
Think of them as "if this happens, then do that" rules. They let you create custom automations 
without any programming knowledge.
                            """).classes("text-sm secondary-text mb-4")

                            ui.label("Examples:").classes(
                                "text-sm font-medium secondary-text"
                            )
                            ui.label(
                                "• When someone cheers 100+ bits → increment counter"
                            ).classes("text-xs secondary-text")
                            ui.label(
                                "• When someone follows → spin the roulette wheel"
                            ).classes("text-xs secondary-text")
                            ui.label(
                                "• When someone types !hello → respond in chat"
                            ).classes("text-xs secondary-text")
                            ui.label(
                                "• When someone donates $5+ → log to file"
                            ).classes("text-xs secondary-text")

                        # How to Create
                        with ui.element("div").classes("form-section"):
                            ui.label("How to Create a Connector").classes(
                                "text-lg font-semibold text-theme-primary mb-3"
                            )
                            ui.label(
                                "1. Click 'New Connector' to open the creation dialog"
                            ).classes("text-sm secondary-text")
                            ui.label(
                                "2. Give your connector a name and description"
                            ).classes("text-sm secondary-text")
                            ui.label(
                                "3. Choose a trigger type (what event to watch for)"
                            ).classes("text-sm secondary-text")
                            ui.label(
                                "4. Add conditions to make the trigger more specific (optional)"
                            ).classes("text-sm secondary-text")
                            ui.label(
                                "5. Add one or more actions to execute when triggered"
                            ).classes("text-sm secondary-text")
                            ui.label("6. Save and enable your connector").classes(
                                "text-sm secondary-text"
                            )

                        # Available Triggers
                        with ui.element("div").classes("form-section"):
                            ui.label("Available Triggers").classes(
                                "text-lg font-semibold text-theme-primary mb-3"
                            )

                            with ui.grid(columns=2).classes("gap-4"):
                                with ui.column().classes("gap-2"):
                                    ui.label("🎬 Twitch Events:").classes(
                                        "text-sm font-medium text-blue-400"
                                    )
                                    ui.label("• Bits/Cheers").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label(
                                        "• Subscriptions & Resubscriptions"
                                    ).classes("text-xs secondary-text")
                                    ui.label("• Gift Subscriptions").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label("• New Followers").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label("• Raids").classes("text-xs secondary-text")
                                    ui.label("• Channel Point Redemptions").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label("• Chat Messages & Commands").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label("• Hype Train Events").classes(
                                        "text-xs secondary-text"
                                    )

                                with ui.column().classes("gap-2"):
                                    ui.label("💰 Other Events:").classes(
                                        "text-sm font-medium text-green-400"
                                    )
                                    ui.label(
                                        "• Donations (StreamLabs/Elements)"
                                    ).classes("text-xs secondary-text")
                                    ui.label("• Timer-based triggers").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label("• Manual triggers").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label("• Webhook events").classes(
                                        "text-xs secondary-text"
                                    )

                        # Available Actions
                        with ui.element("div").classes("form-section"):
                            ui.label("Available Actions").classes(
                                "text-lg font-semibold text-theme-primary mb-3"
                            )

                            with ui.grid(columns=2).classes("gap-4"):
                                with ui.column().classes("gap-2"):
                                    ui.label("🎮 Template Controls:").classes(
                                        "text-sm font-medium text-theme-primary"
                                    )
                                    ui.label(
                                        "• Counter increment/decrement/reset"
                                    ).classes("text-xs secondary-text")
                                    ui.label("• Roulette wheel spin").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label("• Timer controls").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label("• Any template action").classes(
                                        "text-xs secondary-text"
                                    )

                                with ui.column().classes("gap-2"):
                                    ui.label("🌐 External Actions:").classes(
                                        "text-sm font-medium text-orange-400"
                                    )
                                    ui.label("• Send chat messages").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label("• Write to log files").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label("• Make API calls").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label("• Execute system commands").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label("• Custom WebSocket events").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label(
                                        "• Audio control (volume, mute, duration)"
                                    ).classes("text-xs secondary-text")

                        # Audio Control Permissions
                        with ui.element("div").classes("form-section"):
                            ui.label("Audio Control Permissions").classes(
                                "text-lg font-semibold text-theme-primary mb-3"
                            )

                            ui.label(
                                "Audio Control actions require specific permissions on each operating system:"
                            ).classes("text-sm secondary-text mb-3")

                            with ui.grid(columns=1).classes("gap-3"):
                                with ui.column().classes("gap-2"):
                                    ui.label("🍎 macOS:").classes(
                                        "text-sm font-medium text-blue-400"
                                    )
                                    ui.label(
                                        "• Microphone access for audio device control"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Check: System Preferences > Security & Privacy > Microphone"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• The app will automatically request permissions when needed"
                                    ).classes("text-xs secondary-text")

                                with ui.column().classes("gap-2"):
                                    ui.label("🪟 Windows:").classes(
                                        "text-sm font-medium text-green-400"
                                    )
                                    ui.label(
                                        "• Administrator privileges for full audio control"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Right-click the app and select 'Run as administrator'"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Some audio operations work without admin, but full control requires it"
                                    ).classes("text-xs secondary-text")

                                with ui.column().classes("gap-2"):
                                    ui.label("🐧 Linux:").classes(
                                        "text-sm font-medium text-orange-400"
                                    )
                                    ui.label(
                                        "• User must be in the 'audio' group"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Command: sudo usermod -a -G audio $USER"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Restart the application after adding to group"
                                    ).classes("text-xs secondary-text")

                            ui.label(
                                "💡 Tip: The Audio Control UI will show your current permission status and provide request buttons when applicable."
                            ).classes("text-xs text-yellow-400 mt-3")

                        # Audio Control Duration Feature
                        with ui.element("div").classes("form-section"):
                            ui.label("Audio Control Duration Feature").classes(
                                "text-lg font-semibold text-theme-primary mb-3"
                            )

                            ui.label(
                                "The Audio Control action now supports temporary changes with automatic restoration:"
                            ).classes("text-sm secondary-text mb-3")

                            with ui.grid(columns=1).classes("gap-3"):
                                with ui.column().classes("gap-2"):
                                    ui.label("⏱️ Duration Setting:").classes(
                                        "text-sm font-medium text-blue-400"
                                    )
                                    ui.label(
                                        "• Set duration in seconds (0 = permanent change)"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• After the duration expires, audio settings automatically restore to original values"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Perfect for temporary effects like muting during alerts"
                                    ).classes("text-xs secondary-text")

                                with ui.column().classes("gap-2"):
                                    ui.label("🔄 Auto-Restoration:").classes(
                                        "text-sm font-medium text-green-400"
                                    )
                                    ui.label(
                                        "• Stores original volume and mute state before applying changes"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Restores both volume level and mute/unmute state"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Handles multiple overlapping duration changes gracefully"
                                    ).classes("text-xs secondary-text")

                                with ui.column().classes("gap-2"):
                                    ui.label("🎯 Use Cases:").classes(
                                        "text-sm font-medium text-orange-400"
                                    )
                                    ui.label(
                                        "• Temporary volume reduction during important alerts"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Brief mute for notification sounds"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Random volume effects for fun interactions"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Application-specific temporary audio changes"
                                    ).classes("text-xs secondary-text")

                            ui.label(
                                "💡 Example: Set volume to 20% for 5 seconds, then automatically restore to original volume."
                            ).classes("text-xs text-yellow-400 mt-3")

                        # Audio Control Duration Stacking
                        with ui.element("div").classes("form-section"):
                            ui.label("Audio Control Duration Stacking").classes(
                                "text-lg font-semibold text-theme-primary mb-3"
                            )

                            ui.label(
                                "When multiple duration actions target the same audio source:"
                            ).classes("text-sm secondary-text mb-3")

                            with ui.grid(columns=1).classes("gap-3"):
                                with ui.column().classes("gap-2"):
                                    ui.label(
                                        "🔄 Non-Random Actions (Stacking):"
                                    ).classes("text-sm font-medium text-green-400")
                                    ui.label(
                                        "• Durations add up when multiple actions trigger"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Action 1: Set volume to 20% for 5 seconds"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Action 2: Set volume to 30% for 3 seconds (2 seconds later)"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Result: Volume stays at 30% for 6 more seconds total"
                                    ).classes("text-xs secondary-text")

                                with ui.column().classes("gap-2"):
                                    ui.label(
                                        "🎲 Random Actions (Immediate Reset):"
                                    ).classes("text-sm font-medium text-orange-400")
                                    ui.label(
                                        "• Random volume actions immediately change volume and reset duration"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Action 1: Set random volume for 5 seconds"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Action 2: Set random volume for 3 seconds"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Result: Immediately set new random volume, reset to 3 seconds"
                                    ).classes("text-xs secondary-text")

                                with ui.column().classes("gap-2"):
                                    ui.label("🎯 Smart Behavior:").classes(
                                        "text-sm font-medium text-blue-400"
                                    )
                                    ui.label(
                                        "• Only applies new values if they differ from current values"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Tracks original baseline values for accurate restoration"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Handles concurrent actions gracefully without conflicts"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Cleans up automatically when application shuts down"
                                    ).classes("text-xs secondary-text")

                            ui.label(
                                "💡 Tip: Use stacking for gradual audio changes, random reset for instant variety effects."
                            ).classes("text-xs text-yellow-400 mt-3")

                        # Conditions & Placeholders
                        with ui.element("div").classes("form-section"):
                            ui.label("Conditions & Placeholders").classes(
                                "text-lg font-semibold text-theme-primary mb-3"
                            )

                            with ui.grid(columns=2).classes("gap-4"):
                                with ui.column().classes("gap-2"):
                                    ui.label("📋 Conditions:").classes(
                                        "text-sm font-medium text-blue-400"
                                    )
                                    ui.label(
                                        "Make triggers more specific by adding conditions:"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• Amount >= 100 (for bits/donations)"
                                    ).classes("text-xs secondary-text")
                                    ui.label("• Username contains 'VIP'").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label("• Message starts with 'Hello'").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label("• Command equals 'test'").classes(
                                        "text-xs secondary-text"
                                    )

                                with ui.column().classes("gap-2"):
                                    ui.label("🔤 Placeholders:").classes(
                                        "text-sm font-medium text-green-400"
                                    )
                                    ui.label(
                                        "Use {{field}} to insert event data:"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• {{username}} - who triggered it"
                                    ).classes("text-xs secondary-text")
                                    ui.label(
                                        "• {{amount}} - bits/donation amount"
                                    ).classes("text-xs secondary-text")
                                    ui.label("• {{message}} - chat message").classes(
                                        "text-xs secondary-text"
                                    )
                                    ui.label(
                                        "• {{timestamp}} - when it happened"
                                    ).classes("text-xs secondary-text")

                        # Tips
                        with ui.element("div").classes("form-section"):
                            ui.label("Tips & Best Practices").classes(
                                "text-lg font-semibold text-theme-primary mb-3"
                            )
                            ui.label(
                                "💡 Start with examples - click 'Examples' to see pre-made connectors"
                            ).classes("text-sm text-yellow-400")
                            ui.label(
                                "🧪 Test your connectors - use the 'Test' button to verify they work"
                            ).classes("text-sm text-yellow-400")
                            ui.label(
                                "⏸️ Start disabled - new connectors start disabled so you can review them first"
                            ).classes("text-sm text-yellow-400")
                            ui.label(
                                " Monitor statistics - check the stats to see how often your connectors trigger"
                            ).classes("text-sm text-yellow-400")
                            ui.label(
                                "🔄 Use cooldowns - add cooldown seconds to prevent spam triggering"
                            ).classes("text-sm text-yellow-400")
                            ui.label(
                                "📝 Be descriptive - good names and descriptions help you manage many connectors"
                            ).classes("text-sm text-yellow-400")

    help_dialog.open()


def create_examples():
    """Create example connectors"""
    try:
        connector_examples.create_example_connectors()
        ui.notify(
            "Example connectors created! Check them out below (they start disabled).",
            type="positive",
        )
        refresh_connectors()
    except Exception as e:
        logger.error(f"Error creating example connectors: {e}", exc_info=True)
        ui.notify(f"Error creating examples: {str(e)}", type="negative")


def refresh_connectors():
    """Refresh the connectors display"""
    global current_search, connector_cards
    current_search = ""  # Clear search when refreshing
    connector_cards.clear()  # Clear card references
    if search_input:
        search_input.value = ""
    load_connectors()


def on_search_change(event):
    """Handle search input changes"""
    global current_search
    current_search = event.value.lower() if event.value else ""
    update_search_visibility()


def update_search_visibility():
    """Update connector visibility based on search term"""
    search_term = current_search.strip()

    visible_count = 0
    total_connectors = len(connector_cards)

    # Iterate through all stored connector cards
    for connector_id, card_element in connector_cards.items():
        try:
            manager = connector_manager.get_manager()
            connector = manager.get_connector(connector_id)

            if connector:
                # Search through name, description, trigger type, and action types
                searchable_text = (
                    (connector.name or "")
                    + " "
                    + (connector.description or "")
                    + " "
                    + format_trigger_name(connector.trigger.trigger_type)
                    + " "
                    + " ".join(
                        [
                            get_action_display_name(action)
                            for action in connector.actions
                        ]
                    )
                ).lower()

                # Show/hide based on search match
                if not search_term or search_term in searchable_text:
                    card_element.classes(remove="hidden")
                    visible_count += 1
                else:
                    card_element.classes(add="hidden")
            else:
                card_element.classes(add="hidden")
        except Exception as e:
            logger.error(f"Error updating visibility for connector {connector_id}: {e}")
            card_element.classes(add="hidden")

    # Update empty state visibility if we have a connectors container
    if connectors_container:
        # Find the empty state element among the container's children
        empty_state = None
        for child in connectors_container.default_slot.children:
            if hasattr(child, "classes") and "empty-state" in str(child.classes):
                empty_state = child
                break

        if empty_state:
            if total_connectors == 0:
                # No connectors at all
                empty_state.classes(remove="hidden")
                # Update the message label
                for child in empty_state.default_slot.children:
                    if hasattr(child, "text"):
                        child.text = "No connectors created yet"
                        break
            elif visible_count == 0 and search_term:
                # Search returned no results
                empty_state.classes(remove="hidden")
                # Update the message label
                for child in empty_state.default_slot.children:
                    if hasattr(child, "text"):
                        child.text = f"No connectors found matching '{search_term}'"
                        break
            else:
                # Hide empty state when there are visible connectors
                empty_state.classes(add="hidden")

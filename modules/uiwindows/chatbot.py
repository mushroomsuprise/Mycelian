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

import html
import logging
import re
import uuid
from typing import Any, Dict, Optional

from nicegui import ui
from ..notification_engine import notify
from ..ui_buttons import apply_flat_btn_props

from ..help_system.contextual_help import set_chatbot_ui_references

# Import the chatbot modules
from ..chatbot_core import (
    ChatCommand,
    ChatEvent,
    replace_time_variables,  # noqa: F401 - re-exported
)
from ..chatbot_manager import get_manager as get_chatbot_manager
from ..database_manager import get_data, get_data_checked, set_data  # noqa: F401
from .chatbot_cards import CHATBOT_CARDS_CSS, CardActions, ItemsPanel
from .chatbot_quotes_greetings import GreetingsPanel, QuotesPanel, open_greeting_editor, open_quote_editor
from .chatbot_giveaways import GiveawaysPanel
from .chatbot_polls import PollsPanel
from ..twitch import get_twitch_api  # noqa: F401 - re-exported

logger = logging.getLogger(__name__)

# Custom Variables Storage
CUSTOM_VARIABLES_PATH = "chatbot_custom_variables"

# Template-preview patterns (compiled once; were compiled per call).
_PREVIEW_STATS_PATTERN = re.compile(r"\{stats\.([^}]+)\}")
_PREVIEW_YOUTUBE_PATTERN = re.compile(r"\{youtube\.([^}]+)\}")
_PREVIEW_CUSTOM_PATTERN = re.compile(r"\{custom_([^}]+)\}")
_PREVIEW_WORD_PATTERN = re.compile(r"\{command_word_(\d+)\}")
_PREVIEW_DATE_TO_AGE_PATTERN = re.compile(r"date_to_age\(([^)]+)\)")
_PREVIEW_MATH_PATTERN = re.compile(r"math\(([^)]+)\)")
_PREVIEW_COMPARE_PATTERN = re.compile(r"compare\(([^)]+)\)")
_PREVIEW_VAR_PATTERN = re.compile(r"\{([^}]+)\}")
_PREVIEW_USERNAME_PATTERN = re.compile(re.escape("{username}"))
_PREVIEW_QUOTE_PATTERN = re.compile(r"\{quote\.([^.]+)\.([^}]+)\}")
_PREVIEW_API_PATTERN = re.compile(r"\{api_([^}]+)\}")
_PREVIEW_DOT_PATTERN = re.compile(r"\{([^}]+\.([^}]+))\}")


# Custom variable storage / evaluation lives in the non-UI runtime module;
# these names are re-exported for the editor code below.
from ..chatbot_custom_variables import (  # noqa: E402,F401
    _evaluate_compare,
    _evaluate_date_to_age,
    _evaluate_math,
    _load_custom_variables_for_write,
    add_custom_variable,
    evaluate_custom_variable_expression,
    get_live_basic_variables,
    load_custom_variables,
    remove_custom_variable,
    save_custom_variables,
    validate_expression_variables,
)


def _wrap_variable_value(value: str) -> str:
    """Wrap a variable value in styled span"""
    escaped_value = html.escape(str(value))
    return f'<span style="color: var(--color-primary); font-style: italic;">{escaped_value}</span>'


# Global references for UI state
commands_container = None
events_container = None
_items_panels: Dict[str, "ItemsPanel"] = {}
_qg_panels: Dict[str, Any] = {}
_gp_panels: Dict[str, Any] = {}
quotes_container = None
greetings_container = None
giveaways_container = None
polls_container = None

# Global search term (shared across tabs)
search_terms = {
    "commands": "",
    "events": "",
    "quotes": "",
    "greetings": "",
}
selected_chatbot_item = None
create_dialog = None
custom_variable_dialog = None  # Separate dialog for custom variable creation
edit_dialog = None
current_tab = (
    "commands"  # Track current tab: "commands", "events", "quotes", or "greetings"
)

# Tab button references for active state management
command_button = None
event_button = None
quotes_button = None
greetings_button = None




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

.chatbot-card {
    transition: all 0.2s ease-in-out;
    background: var(--color-bg-surface);
    border: 1px solid rgba(59, 130, 246, 0.2);
}

.chatbot-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 25px var(--color-bg-overlay);
    border-color: rgba(59, 130, 246, 0.4);
}

.chatbot-card.command {
    background: var(--color-bg-surface);
    border: 1px solid rgba(16, 185, 129, 0.2);
}

.chatbot-card.command:hover {
    border-color: rgba(16, 185, 129, 0.4);
    box-shadow: 0 8px 25px var(--color-bg-overlay);
}

.chatbot-card.event {
    background: var(--color-bg-surface);
    border: 1px solid rgba(245, 101, 101, 0.2);
}

.chatbot-card.event:hover {
    border-color: rgba(245, 101, 101, 0.4);
    box-shadow: 0 8px 25px var(--color-bg-overlay);
}

.chatbot-card.disabled {
    opacity: 0.6;
    background: var(--color-bg-surface);
    border-color: var(--color-border-default);
}

.mod-only-badge {
    background: linear-gradient(135deg, rgba(245, 101, 101, 0.2), rgba(239, 68, 68, 0.3));
    border: 1px solid rgba(245, 101, 101, 0.4);
    color: var(--color-error);
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
}

.counter-badge {
    background: linear-gradient(135deg, rgba(251, 191, 36, 0.2), rgba(245, 158, 11, 0.3));
    border: 1px solid rgba(251, 191, 36, 0.4);
    color: var(--color-warning);
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
}

.repeating-badge {
    background: linear-gradient(135deg, rgba(139, 92, 246, 0.2), rgba(124, 58, 237, 0.3));
    border: 1px solid rgba(139, 92, 246, 0.4);
    color: var(--color-primary);
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
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

.form-section {
    background: var(--color-bg-surface);
    border: 1px solid var(--color-border-subtle);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
}

.form-section-title {
    color: var(--color-info);
    font-weight: 600;
    font-size: 14px;
    margin-bottom: 12px;
    border-bottom: 1px solid rgba(59, 130, 246, 0.2);
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



.variable-chip {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.3);
    color: var(--color-info);
    padding: 3px 8px;
    border-radius: 12px;
    font-size: 11px;
    margin: 2px;
    cursor: pointer;
    transition: all 0.2s ease;
    font-family: 'Courier New', monospace;
    font-weight: 500;
}

.variable-chip:hover {
    background: rgba(59, 130, 246, 0.25);
    border-color: rgba(59, 130, 246, 0.6);
    transform: translateY(-1px);
    box-shadow: 0 2px 8px rgba(59, 130, 246, 0.2);
}

.variable-chip:active {
    transform: translateY(0px);
    transition: transform 0.1s ease;
}

.variable-help-text {
    background: rgba(245, 101, 101, 0.1);
    border: 1px solid rgba(245, 101, 101, 0.2);
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 11px;
    color: var(--color-error);
    margin-top: 8px;
}

/* Tab styling */
.chatbot-tab {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.3);
    color: var(--color-info);
}

.chatbot-tab.active {
    background: rgba(59, 130, 246, 0.2);
    border-color: rgba(59, 130, 246, 0.5);
    color: var(--color-info);
}

.response-preview {
    background: rgba(0, 0, 0, 0.3);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 4px;
    padding: 8px;
    font-family: 'Courier New', monospace;
    font-size: 12px;
    white-space: pre-wrap;
}

.counter-display {
    background: rgba(251, 191, 36, 0.1);
    border: 1px solid rgba(251, 191, 36, 0.3);
    border-radius: 4px;
    padding: 8px;
    text-align: center;
}

.usage-display {
    background: rgba(139, 92, 246, 0.1);
    border: 1px solid rgba(139, 92, 246, 0.3);
    border-radius: 4px;
    padding: 8px;
    text-align: center;
}

/* Variable Panel Styles */
.variable-panel {
    background: rgba(30, 30, 30, 0.8);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 8px;
    padding: 16px;
}

.variable-category {
    background: rgba(45, 45, 45, 0.5);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 6px;
    padding: 8px;
    margin-bottom: 8px;
}

.variable-category:hover {
    background: rgba(55, 55, 55, 0.5);
    border-color: rgba(255, 255, 255, 0.1);
}

.variable-chip {
    background: rgba(59, 130, 246, 0.1);
    border: 1px solid rgba(59, 130, 246, 0.3);
    color: var(--color-info);
    padding: 4px 8px;
    border-radius: 12px;
    font-size: 11px;
    margin: 2px;
    cursor: pointer;
    transition: all 0.2s ease;
    font-family: 'Courier New', monospace;
    font-weight: 500;
    display: inline-block;
}

.variable-chip:hover {
    background: rgba(59, 130, 246, 0.25);
    border-color: rgba(59, 130, 246, 0.6);
    transform: translateY(-1px);
    box-shadow: 0 2px 8px rgba(59, 130, 246, 0.2);
}

.variable-chip:active {
    transform: translateY(0px);
    transition: transform 0.1s ease;
}

/* Category specific colors */
.variable-chip[data-category="basic"] {
    background: rgba(59, 130, 246, 0.1);
    border-color: rgba(59, 130, 246, 0.3);
    color: var(--color-info);
}

.variable-chip[data-category="stats"] {
    background: rgba(245, 101, 101, 0.1);
    border-color: rgba(245, 101, 101, 0.3);
    color: var(--color-error);
}

.variable-chip[data-category="api"] {
    background: rgba(192, 132, 252, 0.1);
    border-color: rgba(192, 132, 252, 0.3);
    color: var(--color-primary);
}

.variable-chip[data-category="custom"] {
    background: rgba(139, 92, 246, 0.1);
    border-color: rgba(139, 92, 246, 0.3);
    color: var(--color-primary);
}

.variable-chip[data-category="command_event"] {
    background: rgba(34, 197, 94, 0.1);
    border-color: rgba(34, 197, 94, 0.3);
    color: var(--color-success);
}

.variable-chip[data-category="youtube"] {
    background: rgba(239, 68, 68, 0.1);
    border-color: rgba(239, 68, 68, 0.3);
    color: var(--color-error);
}

.variable-chip[data-category="quotes"] {
    background: rgba(245, 158, 11, 0.1);
    border-color: rgba(245, 158, 11, 0.3);
    color: var(--color-warning);
}

.variable-chip[data-category="youtube"] {
    background: rgba(239, 68, 68, 0.1);
    border-color: rgba(239, 68, 68, 0.3);
    color: var(--color-error);
}

.variable-chip[data-category="command-event"] {
    background: rgba(16, 185, 129, 0.1);
    border-color: rgba(16, 185, 129, 0.3);
    color: var(--color-success);
}

.variable-chip[data-category="quotes"] {
    background: rgba(245, 158, 11, 0.1);
    border-color: rgba(245, 158, 11, 0.3);
    color: var(--color-warning);
}

/* Expression chips */
.expression-chip {
    background: rgba(75, 85, 99, 0.1);
    border: 1px solid rgba(75, 85, 99, 0.3);
    color: var(--color-text-secondary);
    padding: 3px 6px;
    border-radius: 8px;
    font-size: 10px;
    cursor: pointer;
    transition: all 0.2s ease;
    font-family: 'Courier New', monospace;
    font-weight: 500;
    display: inline-block;
    margin: 1px;
    max-width: 100%;
    word-wrap: break-word;
}

.expression-chip:hover {
    background: rgba(75, 85, 99, 0.25);
    border-color: rgba(75, 85, 99, 0.6);
    transform: translateY(-1px);
    box-shadow: 0 2px 8px rgba(75, 85, 99, 0.2);
}

.expression-chip:active {
    transform: translateY(0px);
    transition: transform 0.1s ease;
}

/* Expression category colors */
.expression-chip[data-category="time_formatting"] {
    background: rgba(59, 130, 246, 0.1);
    border-color: rgba(59, 130, 246, 0.3);
    color: var(--color-info);
}

.expression-chip[data-category="math_operations"] {
    background: rgba(34, 197, 94, 0.1);
    border-color: rgba(34, 197, 94, 0.3);
    color: var(--color-success);
}

.expression-chip[data-category="comparisons"] {
    background: rgba(245, 158, 11, 0.1);
    border-color: rgba(245, 158, 11, 0.3);
    color: var(--color-warning);
}

.expression-chip[data-category="date_functions"] {
    background: rgba(192, 132, 252, 0.1);
    border-color: rgba(192, 132, 252, 0.3);
    color: var(--color-primary);
}
"""


def create_chatbot_tab():
    """Create the Chatbot tab UI"""
    global commands_container, events_container, quotes_container, greetings_container
    global giveaways_container, polls_container

    # Add custom CSS to the page
    ui.add_head_html(
        f"<style id='chatbot-custom-css'>{CUSTOM_CSS}</style>", shared=True
    )
    from ..help_system.help_render import inject_page_stylesheet, promote_color_importance, scope_selectors
    from .connectors_styles import CONNECTORS_CSS

    inject_page_stylesheet("mycelian-connectors", promote_color_importance(scope_selectors(CONNECTORS_CSS, ".cnx")))
    inject_page_stylesheet("mycelian-chatbot-cards", promote_color_importance(scope_selectors(CHATBOT_CARDS_CSS, ".cnx")))

    # Create a card for the entire tab content with flex layout
    with ui.element("div").classes(
        "tab-surface w-full h-full flex flex-col relative p-4"
    ):
        with ui.element("div").classes(
            "mycelian-sub-tab-shell w-full flex-1 min-h-0 flex flex-col"
        ):
            with ui.tabs().classes("w-full settings-tabs mycelian-sub-tabs") as chatbot_tabs:
                commands_tab = ui.tab("Commands")
                events_tab = ui.tab("Events")
                quotes_tab = ui.tab("Quotes")
                greetings_tab = ui.tab("Greetings")
                giveaways_tab = ui.tab("Giveaways")
                polls_tab = ui.tab("Polls")

            set_chatbot_ui_references(chatbot_tabs)

            with ui.tab_panels(chatbot_tabs, value=commands_tab).classes(
                "w-full grow flex-1 min-h-0"
            ):
                # Commands Tab
                with ui.tab_panel(commands_tab).classes("w-full h-full flex flex-col"):
                    global commands_container
                    commands_container = ui.element("div").classes("w-full h-full min-h-0 flex flex-col")
                    _items_panels["commands"] = ItemsPanel("commands", lambda: get_chatbot_manager().get_all_commands(), CardActions(
                        new=lambda: show_create_chatbot_dialog("command"),
                        edit=lambda iid: show_edit_chatbot_dialog(iid, "command"),
                        test=lambda iid: test_chatbot_item(iid, "command"),
                        toggle=toggle_chatbot_item,
                        duplicate=lambda iid: duplicate_chatbot_item(iid, "command"),
                        delete=lambda iid: delete_chatbot_item(iid, "command"),
                        reset_counter=reset_command_counter,
                        refresh=lambda: refresh_tab_content("commands"),
                    ))

                # Events Tab
                with ui.tab_panel(events_tab).classes("w-full h-full flex flex-col"):
                    global events_container
                    events_container = ui.element("div").classes("w-full h-full min-h-0 flex flex-col")
                    _items_panels["events"] = ItemsPanel("events", lambda: get_chatbot_manager().get_all_events(), CardActions(
                        new=lambda: show_create_chatbot_dialog("event"),
                        edit=lambda iid: show_edit_chatbot_dialog(iid, "event"),
                        test=lambda iid: test_chatbot_item(iid, "event"),
                        toggle=toggle_chatbot_item,
                        duplicate=lambda iid: duplicate_chatbot_item(iid, "event"),
                        delete=lambda iid: delete_chatbot_item(iid, "event"),
                        refresh=lambda: refresh_tab_content("events"),
                    ))

                # Quotes Tab
                with ui.tab_panel(quotes_tab).classes("w-full h-full flex flex-col"):
                    global quotes_container
                    quotes_container = ui.element("div").classes("w-full h-full min-h-0 flex flex-col")

                # Greetings Tab
                with ui.tab_panel(greetings_tab).classes("w-full h-full flex flex-col"):
                    global greetings_container
                    greetings_container = ui.element("div").classes("w-full h-full min-h-0 flex flex-col")

                # Giveaways Tab
                with ui.tab_panel(giveaways_tab).classes(
                    "transition-all duration-300 w-full h-full flex flex-col min-h-0"
                ):
                    giveaways_container = ui.element("div").classes(
                        "w-full h-full min-h-0 flex flex-col"
                    )

                # Polls Tab
                with ui.tab_panel(polls_tab).classes(
                    "transition-all duration-300 w-full h-full flex flex-col min-h-0"
                ):
                    polls_container = ui.element("div").classes(
                        "w-full h-full min-h-0 flex flex-col"
                    )

            # Load and display chatbot items for all tabs
            load_chatbot_items()

    ui.run_javascript(
        "window.mycelianInitSubTabSeams && window.mycelianInitSubTabSeams()"
    )

    # Load custom variables from persistent storage
    try:
        custom_vars = load_custom_variables()
        logger.info(
            f"Loaded {len(custom_vars)} custom variables on chatbot tab initialization"
        )
    except Exception as e:
        logger.error(f"Error loading custom variables on initialization: {e}")


def refresh_tab_content(tab_type: str):
    """Refresh the content of a specific tab"""

    # Get the appropriate container based on tab type
    container = get_container_for_tab(tab_type)
    if container is None:
        logger.error(f"Tab container not initialized for {tab_type}")
        return

    if tab_type in _items_panels:
        _items_panels[tab_type].refresh()
        return
    if tab_type in _qg_panels:
        _qg_panels[tab_type].refresh()
        return
    if tab_type in _gp_panels:
        _gp_panels[tab_type].refresh()
        return

    logger.debug("No panel for chatbot tab %s", tab_type)


def get_container_for_tab(tab_type: str):
    """Get the container element for a specific tab"""
    global commands_container, events_container, quotes_container, greetings_container
    global giveaways_container, polls_container

    if tab_type == "commands":
        return commands_container
    elif tab_type == "events":
        return events_container
    elif tab_type == "quotes":
        return quotes_container
    elif tab_type == "greetings":
        return greetings_container
    elif tab_type == "giveaways":
        return giveaways_container
    elif tab_type == "polls":
        return polls_container
    else:
        return None


def load_chatbot_items():
    """Load and display chatbot items for all tabs initially"""

    for kind in ("commands", "events"):
        panel = _items_panels.get(kind)
        container = get_container_for_tab(kind)
        if panel is not None and container is not None:
            container.clear()
            panel.build(container)

    for kind in ("quotes", "greetings"):
        container = get_container_for_tab(kind)
        if container is not None:
            panel = _qg_panels.setdefault(kind, QuotesPanel() if kind == "quotes" else GreetingsPanel())
            panel.build(container)

    for kind in ("giveaways", "polls"):
        container = get_container_for_tab(kind)
        if container is not None:
            panel = _gp_panels.setdefault(kind, GiveawaysPanel() if kind == "giveaways" else PollsPanel())
            try:
                panel.build(container)
            except Exception as e:
                logger.error("Error loading %s: %s", kind, e, exc_info=True)
                with container:
                    ui.label(f"Error loading {kind}: {e}").classes("text-red-400")


def show_create_chatbot_dialog(item_type: str):
    """Show the create chatbot item dialog"""
    if item_type == "quote":
        open_quote_editor(None, lambda: refresh_tab_content("quotes"))
    elif item_type == "greeting":
        open_greeting_editor(None, lambda: refresh_tab_content("greetings"))
    else:
        show_chatbot_dialog(item_type=item_type)


_active_editor = None


def show_chatbot_dialog(item_id: Optional[str] = None, item_type: Optional[str] = None):
    """Open the New/Edit editor for a command or event (see ``chatbot_editor``)."""
    global _active_editor
    from .chatbot_editor import open_chatbot_editor

    kind = "event" if item_type == "event" else "command"
    if _active_editor is not None and _active_editor.dialog is not None:
        try:
            _active_editor.dialog.delete()
        except Exception:
            pass
    try:
        _active_editor = open_chatbot_editor(kind, item_id, on_saved=lambda: refresh_tab_content(f"{kind}s"))
    except Exception as e:
        logger.error("Could not open the chatbot editor: %s", e, exc_info=True)
        notify("Could not open the editor", type="negative")


def close_dialog_and_refresh():
    """Close the create dialog and refresh the items list"""
    global create_dialog
    try:
        if create_dialog:
            create_dialog.close()
        refresh_chatbot_items()
    except Exception as e:
        logger.error(f"Error closing dialog and refreshing: {e}")


def show_edit_chatbot_dialog(item_id: str, item_type: str):
    """Show the edit chatbot item dialog"""
    show_chatbot_dialog(item_id, item_type)


def test_chatbot_item(item_id: str, item_type: str):
    """Run a command or event once with sample data (no counters, cooldowns or stats change)."""
    from .chatbot_editor import run_item_test

    run_item_test("event" if item_type == "event" else "command", item_id)


def toggle_chatbot_item(item_id: str, enabled: bool):
    """Toggle a chatbot item's enabled state"""
    try:
        manager = get_chatbot_manager()
        success = manager.toggle_item(item_id, enabled)

        if success:
            status = "enabled" if enabled else "disabled"
            notify(f"Item {status}", type="positive")
            refresh_chatbot_items()
        else:
            notify("Failed to toggle item", type="negative")
    except Exception as e:
        logger.error(f"Error toggling chatbot item: {e}", exc_info=True)
        notify(f"Error toggling item: {str(e)}", type="negative")


def reset_command_counter(command_id: str):
    """Ask before resetting a command's counter."""

    def do_reset() -> None:
        dialog.close()
        try:
            manager = get_chatbot_manager()
            success = manager.reset_command_counter(command_id)

            if success:
                notify("Command counter reset", type="positive")
                refresh_chatbot_items()
            else:
                notify("Failed to reset counter", type="negative")
        except Exception as e:
            logger.error(f"Error resetting command counter: {e}", exc_info=True)
            notify(f"Error resetting counter: {str(e)}", type="negative")

    with ui.dialog().props("persistent") as dialog, ui.card().classes("w-[420px] p-4"):
        ui.label("Reset command counter?").classes("text-lg font-bold mb-2")
        ui.label(
            "This sets the command's use count back to zero."
        ).classes("secondary-text mb-4")
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Cancel", on_click=dialog.close).props("outline")
            ui.button("Reset", on_click=do_reset).props("color=negative")
    dialog.open()


def _unique_copy_label(base: str, taken: set) -> str:
    """Return a display name that is not already used, case-insensitively."""
    root = (base or "Item").strip() or "Item"
    candidate = f"{root} copy"
    number = 2
    taken_lower = {item.lower() for item in taken if item}
    while candidate.lower() in taken_lower:
        candidate = f"{root} copy {number}"
        number += 1
    return candidate


def _unique_command_token(base: str, taken: set) -> str:
    """Return a command name that does not collide with names or aliases."""
    root = re.sub(r"\s+", "", (base or "command").strip()) or "command"
    candidate = f"{root}_copy"
    number = 2
    taken_lower = {item.lower() for item in taken if item}
    while candidate.lower() in taken_lower:
        candidate = f"{root}_copy{number}"
        number += 1
    return candidate


def duplicate_chatbot_item(item_id: str, item_type: str) -> None:
    """Save a new command, event, or quote from an existing one."""
    try:
        manager = get_chatbot_manager()
        if item_type == "command":
            source = manager.commands.get(item_id)
            if source is None:
                notify("Command not found", type="negative")
                return
            data = source.to_dict()
            taken = set()
            for command in manager.commands.values():
                if command.command_name:
                    taken.add(command.command_name)
                taken.update(command.aliases or [])
            data["command_id"] = str(uuid.uuid4())
            data["name"] = _unique_copy_label(
                source.name, {command.name for command in manager.commands.values()}
            )
            data["command_name"] = _unique_command_token(source.command_name, taken)
            data["aliases"] = []
            data["usage_count"] = 0
            data["last_used"] = 0
            data["counter_value"] = 0
            data["trigger_count"] = 0
            data["last_triggered"] = 0
            if not manager.add_command(ChatCommand.from_dict(data)):
                notify("Could not duplicate command", type="negative")
                return
            notify(
                f"Duplicated command as !{data['command_name']}. Aliases were cleared so both do not fire together.",
                type="positive",
            )
            refresh_tab_content("commands")
            return

        if item_type == "event":
            source = manager.events.get(item_id)
            if source is None:
                notify("Event not found", type="negative")
                return
            data = source.to_dict()
            data["event_id"] = str(uuid.uuid4())
            data["name"] = _unique_copy_label(
                source.name, {event.name for event in manager.events.values()}
            )
            data["trigger_count"] = 0
            data["last_triggered"] = 0
            if not manager.add_event(ChatEvent.from_dict(data)):
                notify("Could not duplicate event", type="negative")
                return
            notify(f"Duplicated event '{data['name']}'.", type="positive")
            refresh_tab_content("events")
            return

        if item_type == "quote":
            source = manager.quotes.get(item_id)
            if source is None:
                notify("Quote not found", type="negative")
                return
            success, error, number = manager.add_quote(
                source.text, source.author, getattr(source, "added_by", "") or ""
            )
            if not success:
                notify(error or "Could not duplicate quote", type="negative")
                return
            notify(f"Duplicated quote as #{number}.", type="positive")
            refresh_tab_content("quotes")
            return

        notify("This item cannot be duplicated.", type="warning")
    except Exception as exc:
        logger.error("Error duplicating chatbot item: %s", exc, exc_info=True)
        notify("Could not duplicate this item.", type="negative")


def delete_chatbot_item(item_id: str, item_type: str):
    """Delete a chatbot item with confirmation"""

    def confirm_delete():
        try:
            manager = get_chatbot_manager()
            if item_type == "command":
                success = manager.remove_command(item_id)
            elif item_type == "event":
                success = manager.remove_event(item_id)
            else:  # quote
                success = manager.delete_quote(item_id)

            if success:
                notify(f"{item_type.title()} deleted", type="positive")
                refresh_chatbot_items()
            else:
                notify(f"Failed to delete {item_type}", type="negative")
        except Exception as e:
            logger.error(f"Error deleting chatbot item: {e}", exc_info=True)
            notify(f"Error deleting item: {str(e)}", type="negative")

    # Show confirmation dialog
    with ui.dialog().props("persistent") as dialog:
        with ui.card():
            ui.label("Confirm Delete").classes("text-lg font-semibold mb-2")
            ui.label(
                f"Are you sure you want to delete this {item_type}? This action cannot be undone."
            ).classes("mb-4")

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                delete_btn = ui.button(
                    "Delete",
                    on_click=lambda: [
                        confirm_delete(),
                        dialog.close(),
                    ],
                )
                delete_btn.classes("btn-danger mycelian-btn")
                apply_flat_btn_props(delete_btn)

    dialog.open()


def refresh_chatbot_items():
    """Refresh all chatbot tabs"""
    refresh_tab_content("commands")
    refresh_tab_content("events")
    refresh_tab_content("quotes")
    refresh_tab_content("greetings")
    refresh_tab_content("giveaways")
    refresh_tab_content("polls")


# Greeting-specific functions
async def lookup_twitch_user(username: str) -> tuple[str, str]:
    """Lookup Twitch user by username and return (user_id, display_name) or raise exception"""
    try:
        from ..twitch import get_twitch_api

        twitch_api = get_twitch_api()
        if not twitch_api or not twitch_api.is_connected:
            raise Exception(
                "Twitch API not connected. Please authenticate with Twitch first."
            )

        # Use the generic API call method to lookup user by login name
        url = "https://api.twitch.tv/helix/users"
        params = {"login": username.lower()}

        response_data = await twitch_api.generic_api_call(url, "GET", params=params)

        if "data" in response_data and len(response_data["data"]) > 0:
            user_data = response_data["data"][0]
            return user_data["id"], user_data["display_name"]
        else:
            raise Exception(f"Twitch user '{username}' not found")

    except Exception as e:
        logger.error(f"Error looking up Twitch user '{username}': {e}")
        raise


def close_edit_dialog():
    """Close the edit dialog and reset the global variable"""
    global edit_dialog
    if edit_dialog:
        edit_dialog.close()
        edit_dialog = None

"""
Contextual Help Components

Provides help buttons and inline help components for UI integration.
"""

from nicegui import ui
import logging
from typing import Any, Dict, Optional, Tuple
from .help_manager import get_help_manager
from .help_browser import ensure_help_system_styles, show_help_browser

logger = logging.getLogger(__name__)

# Global references to main UI elements (set during UI initialization)
_main_tabs = None
_main_tab_panels = None
_settings_tabs = None
_settings_tab_panels = None
_alerts_tabs = None
_chatbot_tabs = None
_main_tab_refs: Dict[str, Any] = {}


def set_main_ui_references(tabs, tab_panels):
    """Set references to main UI tabs and panels for context detection"""
    global _main_tabs, _main_tab_panels
    _main_tabs = tabs
    _main_tab_panels = tab_panels


def register_main_tabs(tab_refs: Dict[str, Any]) -> None:
    """Map main tab labels (e.g. \"Settings\") to NiceGUI tab objects for navigation."""
    global _main_tab_refs
    _main_tab_refs = dict(tab_refs) if tab_refs else {}


def set_settings_ui_references(tabs, tab_panels):
    """Set references to settings UI tabs and panels for context detection"""
    global _settings_tabs, _settings_tab_panels
    _settings_tabs = tabs
    _settings_tab_panels = tab_panels


def set_alerts_ui_references(tabs) -> None:
    """Set reference to Alerts sub-tabs for context detection."""
    global _alerts_tabs
    _alerts_tabs = tabs


def set_chatbot_ui_references(tabs) -> None:
    """Set reference to Chatbot sub-tabs for context detection."""
    global _chatbot_tabs
    _chatbot_tabs = tabs


def _open_help_target(target: str) -> None:
    """Open help browser for a topic ID or category name."""
    help_manager = get_help_manager()
    if help_manager.get_topic(target):
        show_help_browser(topic_id=target)
    else:
        show_help_browser(category=target)


def navigate_to_main_tab(tab_label: str) -> None:
    """Switch the top-level app tab by label (e.g. \"Settings\", \"Activity Feed\")."""
    try:
        if not _main_tabs:
            return
        ref = _main_tab_refs.get(tab_label)
        if ref is not None:
            _main_tabs.value = ref
            return
        _main_tabs.value = tab_label
    except Exception as e:
        logger.debug("navigate_to_main_tab failed: %s", e)


def navigate_to_settings_subtab(
    subtab_label: str,
    *,
    main_tab: Optional[str] = None,
) -> None:
    """Open Settings and select a sub-tab by name (e.g. \"Game Hooks\", \"Twitch\")."""
    target_main = main_tab or "Settings"

    def go_sub() -> None:
        try:
            if _settings_tabs:
                _settings_tabs.value = subtab_label
        except Exception as e:
            logger.debug("navigate_to_settings_subtab inner failed: %s", e)

    try:
        navigate_to_main_tab(target_main)
        ui.timer(0.12, go_sub, once=True)
        ui.timer(0.45, go_sub, once=True)
    except Exception as e:
        logger.debug("navigate_to_settings_subtab failed: %s", e)


def get_current_tab_context() -> Tuple[str, Optional[str]]:
    """
    Get the current active tab context.

    Returns:
        Tuple of (main_tab_name, sub_tab_name) where sub_tab_name is None if no sub-tab
    """
    try:
        # Check main tabs first
        if _main_tabs:
            current_main_tab = _main_tabs.value
            main_tab_name = _get_tab_name(current_main_tab)

            if main_tab_name == "Settings" and _settings_tabs:
                current_sub_tab = _settings_tabs.value
                sub_tab_name = _get_tab_name(current_sub_tab)
                return main_tab_name, sub_tab_name

            if main_tab_name == "Alerts" and _alerts_tabs:
                current_sub_tab = _alerts_tabs.value
                sub_tab_name = _get_tab_name(current_sub_tab)
                return main_tab_name, sub_tab_name

            if main_tab_name == "Chatbot" and _chatbot_tabs:
                current_sub_tab = _chatbot_tabs.value
                sub_tab_name = _get_tab_name(current_sub_tab)
                return main_tab_name, sub_tab_name

            return main_tab_name, None

        return "unknown", None

    except Exception as e:
        return "unknown", None


def _get_tab_name(tab_object) -> str:
    """Extract tab name from tab object"""
    if isinstance(tab_object, str):
        return tab_object

    # Try different attributes that might contain the tab name
    for attr in ['text', 'label', 'name', '_text', '_props', 'props']:
        if hasattr(tab_object, attr):
            value = getattr(tab_object, attr)
            if value:
                # If it's a dict (like _props), look for common keys
                if isinstance(value, dict):
                    for key in ['label', 'text', 'name']:
                        if key in value and value[key]:
                            return str(value[key])
                else:
                    return str(value)

    # Try accessing as a NiceGUI element
    try:
        if hasattr(tab_object, 'props') and callable(getattr(tab_object, 'props', None)):
            props = tab_object.props()
            if props and 'label' in props:
                return str(props['label'])
    except Exception as e:
        pass

    # Fallback to string representation
    result = str(tab_object)
    return result


def get_help_target_for_tab(main_tab: str, sub_tab: Optional[str] = None) -> Optional[str]:
    """
    Get the appropriate help target (category or topic ID) for a given tab context.

    Args:
        main_tab: Main tab name
        sub_tab: Sub-tab name (if applicable)

    Returns:
        Help category name, topic ID, or None if no mapping found
    """
    main_tab_topic_mapping = {
        "Activity Feed": "getting_started_intro",
        "Alerts": "alerts_overview",
        "Source Settings": "templates_intro",
        "Source Controls": "source_controls",
        "Connectors": "connectors_intro",
        "Chatbot": "chatbot_commands",
        "Spore Studio": "spore_studio_overview",
        "Settings": "settings",
    }

    if main_tab == "Chatbot" and sub_tab:
        chatbot_sub_tab_mapping = {
            "Commands": "chatbot_commands",
            "Events": "chatbot_events",
            "Quotes": "chatbot_quotes",
            "Greetings": "chatbot_greetings",
            "Giveaways": "chatbot_giveaways",
        }
        return chatbot_sub_tab_mapping.get(sub_tab, "chatbot_commands")

    if main_tab == "Settings" and sub_tab:
        settings_sub_tab_mapping = {
            "Twitch": "integrations_twitch",
            "PSN": "integrations_psn",
            "Spotify": "integrations_spotify",
            "YouTube": "integrations_youtube",
            "OBS": "obs_setup",
            "Database": "settings",
            "Statistics": "settings",
            "Theme": "settings",
            "App Settings": "settings",
            "About": "settings",
            "Game Hooks": "game_hooks",
        }
        return settings_sub_tab_mapping.get(sub_tab)

    return main_tab_topic_mapping.get(main_tab)


def help_button(context: str = None, topic_id: str = None, tooltip: str = "Help", size: str = "sm", variant: str = "flat", auto_context: bool = True):
    """
    Create a help button that opens contextual help.

    Args:
        context: UI context string (e.g., "settings.twitch")
        topic_id: Direct topic ID to open
        tooltip: Tooltip text for the button
        size: Button size ("xs", "sm", "md", "lg")
        variant: Button variant ("flat", "outline", "solid")
        auto_context: Whether to automatically detect current tab context

    Returns:
        NiceGUI button element
    """
    ensure_help_system_styles()

    def on_click():
        try:
            if topic_id:
                show_help_browser(topic_id)
                logger.debug(f"Opened help for topic: {topic_id}")
            elif context:
                help_manager = get_help_manager()
                topic = help_manager.get_topic_for_context(context)
                if topic:
                    show_help_browser(topic.id)
                    logger.debug(f"Opened contextual help for context: {context}, topic: {topic.id}")
                else:
                    # Fallback to general help browser
                    show_help_browser()
                    logger.debug(f"No specific topic found for context: {context}, showing general help")
            elif auto_context:
                main_tab, sub_tab = get_current_tab_context()
                target = get_help_target_for_tab(main_tab, sub_tab)
                if target:
                    _open_help_target(target)
                else:
                    show_help_browser()
            else:
                show_help_browser()
                logger.debug("Opened general help browser")
        except Exception as e:
            logger.error(f"Error opening help: {e}")
            from ..notification_engine import notify as app_notify

            app_notify(f"Error opening help: {e}", type="negative")

    # Choose icon based on size
    icon_name = "help_outline" if size == "sm" else "help"

    button = ui.button(
        icon=icon_name,
        on_click=on_click
    ).props(f"{variant} round dense").tooltip(tooltip).classes("help-inline-icon")

    size_classes = {
        "xs": "text-xs p-1",
        "sm": "text-sm p-1",
        "md": "text-base p-2",
        "lg": "text-lg p-3"
    }

    if size in size_classes:
        button.classes(size_classes[size])

    return button


def inline_help(text: str, topic_id: str = None, context: str = None, show_icon: bool = True):
    """
    Create inline help text with optional link to full topic.

    Args:
        text: Help text to display
        topic_id: Direct topic ID to link to
        context: UI context to find appropriate topic
        show_icon: Whether to show info icon

    Returns:
        NiceGUI row element containing the help
    """
    try:
        ensure_help_system_styles()
        with ui.row().classes("items-center gap-2 flex-wrap") as container:
            if show_icon:
                ui.icon("info", size="sm").classes("help-inline-icon")

            ui.label(text).classes("text-sm secondary-text flex-grow")

            # Add "Learn more" link if topic available
            link_topic_id = topic_id
            if not link_topic_id and context:
                help_manager = get_help_manager()
                topic = help_manager.get_topic_for_context(context)
                if topic:
                    link_topic_id = topic.id

            if link_topic_id:
                ui.button(
                    "Learn more",
                    on_click=lambda: show_help_browser(link_topic_id),
                ).props("flat dense no-caps").classes("text-xs help-inline-link")

        return container

    except Exception as e:
        logger.error(f"Error creating inline help: {e}")
        # Fallback to simple text
        return ui.label(text).classes("text-sm secondary-text")


def help_tooltip(element, help_text: str, topic_id: str = None, context: str = None):
    """
    Add a help tooltip to any UI element.

    Args:
        element: NiceGUI element to add tooltip to
        help_text: Tooltip text
        topic_id: Optional topic to open on click
        context: Optional context to find topic
    """
    try:
        # Add tooltip
        element.tooltip(help_text)

        # If topic provided, make it clickable to open help
        if topic_id or context:
            def on_click():
                target_topic = topic_id
                if not target_topic and context:
                    help_manager = get_help_manager()
                    topic = help_manager.get_topic_for_context(context)
                    if topic:
                        target_topic = topic.id

                if target_topic:
                    show_help_browser(target_topic)

            # Add click handler if element supports it
            if hasattr(element, 'on_click'):
                element.on_click(on_click)

    except Exception as e:
        logger.error(f"Error adding help tooltip: {e}")


def help_accordion(title: str, content: str, topic_id: str = None, context: str = None):
    """
    Create a collapsible help section.

    Args:
        title: Accordion title
        content: Help content (can include markdown)
        topic_id: Optional topic to link to
        context: Optional context to find topic
    """
    try:
        ensure_help_system_styles()
        with ui.expansion(title).classes("w-full") as accordion:
            ui.markdown(content).classes("w-full help-accordion-markdown max-w-none")

            # Optional link to full topic
            link_topic_id = topic_id
            if not link_topic_id and context:
                help_manager = get_help_manager()
                topic = help_manager.get_topic_for_context(context)
                if topic:
                    link_topic_id = topic.id

            if link_topic_id:
                ui.separator().classes("my-2 help-separator-subtle")
                ui.button(
                    "Read full documentation",
                    icon="open_in_new",
                    on_click=lambda: show_help_browser(link_topic_id)
                ).props("outline no-caps").classes("text-sm help-related-btn")

        return accordion

    except Exception as e:
        logger.error(f"Error creating help accordion: {e}")
        # Fallback
        return ui.label(f"{title}: {content}").classes("text-sm secondary-text")


def help_card(title: str, summary: str, topic_id: str, show_category: bool = True):
    """
    Create a help topic card for quick access.

    Args:
        title: Topic title
        summary: Brief summary
        topic_id: Topic ID to open
        show_category: Whether to show category badge
    """
    try:
        ensure_help_system_styles()
        help_manager = get_help_manager()
        topic = help_manager.get_topic(topic_id)

        with ui.card().classes(
            "p-4 cursor-pointer help-contextual-card transition-colors"
        ).on("click", lambda: show_help_browser(topic_id)) as card:

            if show_category and topic:
                category_name = topic.category.value.replace("_", " ").title()
                ui.label(category_name).classes("help-category-badge mb-2")

            ui.label(title).classes("font-bold mb-1 help-text-primary")

            ui.label(summary).classes("text-sm help-text-secondary")

            if topic and topic.keywords:
                with ui.row().classes("gap-1 mt-2 flex-wrap"):
                    for kw in topic.keywords[:3]:
                        ui.label(kw).classes("help-keyword-pill")

        return card

    except Exception as e:
        logger.error(f"Error creating help card for topic {topic_id}: {e}")
        # Fallback
        with ui.card().classes("p-4") as card:
            ui.label(title).classes("font-bold")
            ui.label(summary).classes("text-sm secondary-text")
        return card


def create_help_section(title: str, topics: list, columns: int = 2):
    """
    Create a section with multiple help topic cards.

    Args:
        title: Section title
        topics: List of (topic_id, title, summary) tuples
        columns: Number of columns for grid layout
    """
    try:
        ensure_help_system_styles()
        ui.label(title).classes("text-lg font-semibold mb-4 help-text-primary")

        with ui.grid(columns=columns).classes("gap-4"):
            for topic_id, card_title, summary in topics:
                help_card(card_title, summary, topic_id)

    except Exception as e:
        logger.error(f"Error creating help section '{title}': {e}")


# Convenience functions for common help patterns

def twitch_help_button():
    """Help button specifically for Twitch settings"""
    return help_button(context="settings.twitch", tooltip="Twitch connection help")

def alerts_help_button():
    """Help button specifically for alerts"""
    return help_button(topic_id="alerts_overview", tooltip="Alerts help")

def connectors_help_button():
    """Help button specifically for connectors"""
    return help_button(topic_id="connectors_intro", tooltip="Connectors help")

def templates_help_button():
    """Help button specifically for templates"""
    return help_button(topic_id="templates_intro", tooltip="Templates help")

def spore_studio_help_button(**kwargs):
    """Help button specifically for Spore Studio"""
    return help_button(topic_id="spore_studio_overview", tooltip="Spore Studio help", **kwargs)

def troubleshooting_help_button():
    """Help button specifically for troubleshooting"""
    return help_button(topic_id="troubleshooting_alerts", tooltip="Troubleshooting help")
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
import time

from nicegui import ui

from ..help_system.contextual_help import help_button

from .. import alert_processor, alertutils

logger = logging.getLogger(__name__)

# Resizable alert file browser dialog (POSIX paths inside dialog for web/OBS consistency).
# self-start + mx-auto: without this, flex stretch gives the card 100% width and only height resizes.
_FILE_BROWSER_CARD_CLASSES = (
    "mx-auto self-start min-w-[480px] min-h-[400px] w-[min(88vw,1100px)] h-[500px] "
    "!max-w-[min(96vw,1920px)] max-h-[90vh] resize overflow-auto p-4 flex flex-col"
)
# Quasar: content-class is applied to .q-dialog__inner; width override is in mainuiwindow FILE_BROWSER_QDIALOG_CSS.
_FILE_BROWSER_DIALOG_PROPS = "content-class=mycelian-wide-file-dialog"


def _alert_browser_path_str(path) -> str:
    """Forward-slash path for in-dialog display and state (browser-style)."""
    from pathlib import Path

    return Path(path).expanduser().as_posix()


def _path_under_assets(path) -> bool:
    from pathlib import Path

    from .. import path_utils

    assets_dir = Path(path_utils.get_assets_path()).resolve()
    try:
        Path(path).resolve().relative_to(assets_dir)
        return True
    except (ValueError, OSError):
        return False


# Basic CSS for styling - uses theme CSS variables
CSS = """
/* Basic styling elements */
.q-switch {
    border-radius: 16px;
}

/* Custom button styling to override any conflicting styles */
/* Using maximum specificity to override NiceGUI and other styles */
.q-btn.alert-delete-btn,
button.alert-delete-btn,
.alert-delete-btn.q-btn,
.alert-delete-btn {
    background-color: var(--color-error) !important;
    background: var(--color-error) !important;
    color: white !important;
    font-weight: 500 !important;
    box-shadow: 0 4px 6px -1px var(--color-bg-overlay) !important;
    border: none !important;
}

.q-btn.alert-delete-btn:hover,
button.alert-delete-btn:hover,
.alert-delete-btn.q-btn:hover,
.alert-delete-btn:hover {
    background-color: rgb(185, 28, 28) !important;
    background: rgb(185, 28, 28) !important;
}

.q-btn.alert-save-btn,
button.alert-save-btn,
.alert-save-btn.q-btn,
.alert-save-btn {
    background-color: var(--color-success) !important;
    background: var(--color-success) !important;
    color: white !important;
    font-weight: 500 !important;
    box-shadow: 0 4px 6px -1px var(--color-bg-overlay) !important;
    border: none !important;
}

.q-btn.alert-save-btn:hover,
button.alert-save-btn:hover,
.alert-save-btn.q-btn:hover,
.alert-save-btn:hover {
    background-color: rgb(21, 128, 61) !important;
    background: rgb(21, 128, 61) !important;
}
"""


class AlertSettingsState:
    """Class to store alert settings UI state"""

    def __init__(self):
        # Store UI elements per tab type
        self.tab_elements = {}
        # Track original values per tab
        self.tab_original_values = {}
        # Track which tab is currently active
        self.current_tab = None

    def get_elements(self, tab_type):
        """Get UI elements for a specific tab type"""
        if tab_type not in self.tab_elements:
            self.tab_elements[tab_type] = {}
        return self.tab_elements[tab_type]

    def get_original_values(self, tab_type):
        """Get original values for a specific tab type"""
        if tab_type not in self.tab_original_values:
            self.tab_original_values[tab_type] = {}
        return self.tab_original_values[tab_type]

    def set_current_tab(self, tab_type):
        """Set the currently active tab"""
        self.current_tab = tab_type


# Create global state instance
alert_settings_state = AlertSettingsState()

# Reserved Channel Points combobox values (not real Twitch custom reward IDs)
POINTS_REWARD_SELECT_PLACEHOLDERS = frozenset(
    {
        "new",
        "loading",
        "error",
        "no_rewards",
        "not_connected",
        "no_channel_points",
    }
)

TWITCH_REWARD_UI_FIELD_NAMES = (
    "twitch_title_input",
    "twitch_cost_input",
    "twitch_enabled_switch",
    "twitch_user_input_switch",
    "twitch_user_input_prompt",
    "twitch_max_per_stream_switch",
    "twitch_max_per_stream_input",
    "twitch_max_per_user_switch",
    "twitch_max_per_user_input",
    "twitch_cooldown_switch",
    "twitch_cooldown_input",
    "twitch_skip_queue_switch",
)


def update_volume_value(alert_type: str, volume_value: int):
    """Helper function to update both volume slider and its display label

    Args:
        alert_type (str): The alert type (bits, subs, etc.)
        volume_value (int): The volume value to set (0-100)
    """
    try:
        elements = alert_settings_state.get_elements(alert_type)
        if "volume_input" in elements and elements["volume_input"]:
            elements["volume_input"].value = volume_value
        if "volume_value_label" in elements and elements["volume_value_label"]:
            elements["volume_value_label"].set_text(f"{volume_value}%")
    except Exception as e:
        logger.error(f"Error updating volume value: {str(e)}", exc_info=True)


TTS_SOURCE_OPTIONS = {
    "alert_message": "Alert Message",
    "custom_message": "Custom Message",
}


def update_tts_custom_message_visibility(alert_type: str):
    """Custom TTS field is always visible; kept for compatibility."""
    return


def create_tts_settings_section(alert_type: str):
    """Create the text-to-speech settings section for alerts."""
    with ui.expansion("TTS", icon="record_voice_over").classes(
        "w-full bg-theme-base rounded-lg overflow-hidden"
    ).style("border: 1px solid var(--color-border-default);"):
        with ui.grid(columns=1).classes("w-full gap-2 p-2"):
            with ui.card().classes("w-full p-3 rounded-lg").style(
                "background-color: var(--color-bg-surface);"
            ):
                ui.label("Speech Playback").classes("font-medium mb-2 text-sm")
                alert_settings_state.get_elements(alert_type)["tts_enabled_switch"] = (
                    ui.switch("Enable Text-to-Speech", value=False).classes(
                        "w-full q-switch"
                    )
                )
                alert_settings_state.get_elements(alert_type)["tts_enabled_switch"].on(
                    "change",
                    lambda e, at=alert_type: track_field_change(
                        "tts_enabled_switch",
                        alert_settings_state.get_elements(at)["tts_enabled_switch"],
                        e,
                        at,
                    ),
                )
                ui.tooltip(
                    "Speak this alert using the browser text-to-speech system"
                ).classes("bg-theme-surface")

            with ui.card().classes("w-full p-3 rounded-lg").style(
                "background-color: var(--color-bg-surface);"
            ):
                ui.label("Speech Source").classes("font-medium mb-2 text-sm")
                alert_settings_state.get_elements(alert_type)["tts_source_select"] = (
                    ui.select(
                        TTS_SOURCE_OPTIONS,
                        label="TTS Text Source",
                        value="alert_message",
                    ).classes("w-full bg-theme-base rounded-md")
                )

                def handle_tts_source_change(e, at=alert_type):
                    update_tts_custom_message_visibility(at)
                    track_field_change(
                        "tts_source_select",
                        alert_settings_state.get_elements(at)["tts_source_select"],
                        e,
                        at,
                    )

                alert_settings_state.get_elements(alert_type)["tts_source_select"].on(
                    "change", handle_tts_source_change
                )
                ui.tooltip(
                    "Choose whether TTS should speak the alert message or a custom static message"
                ).classes("bg-theme-surface")

                alert_settings_state.get_elements(alert_type)[
                    "tts_custom_message_input"
                ] = ui.input("Custom TTS Message").classes(
                    "w-full mt-3 bg-theme-base rounded-md text-sm"
                )
                alert_settings_state.get_elements(alert_type)[
                    "tts_custom_message_input"
                ].props('placeholder="Thanks for the support!"')
                alert_settings_state.get_elements(alert_type)[
                    "tts_custom_message_input"
                ].on(
                    "change",
                    lambda e, at=alert_type: track_field_change(
                        "tts_custom_message_input",
                        alert_settings_state.get_elements(at)[
                            "tts_custom_message_input"
                        ],
                        e,
                        at,
                    ),
                )
                ui.tooltip(
                    "Static message to speak when Custom Message is selected"
                ).classes("bg-theme-surface")


def create_alert_settings_tab():
    """Create the alert settings tab content"""

    # Add the CSS for basic styling - no animations
    ui.add_head_html(f"<style>{CSS}</style>")

    with ui.element("div").classes("content-section w-full h-full relative"):
        # Help icon in top right corner
        with ui.row().classes("absolute top-1 right-1 z-20"):
            help_button(topic_id="alerts_overview", tooltip="Alerts help", size="sm")

        # Create tabs for different alert types with a more modern style
        with ui.tabs().classes("w-full bg-theme-base rounded-lg p-1") as alert_tabs:
            bits_tab = ui.tab("Bits").classes(
                "transition-all duration-200 hover-theme-surface rounded-md"
            )
            subs_tab = ui.tab("Subscriptions").classes(
                "transition-all duration-200 hover-theme-surface rounded-md"
            )
            giftsubs_tab = ui.tab("Gift Subs").classes(
                "transition-all duration-200 hover-theme-surface rounded-md"
            )
            donations_tab = ui.tab("Donations").classes(
                "transition-all duration-200 hover-theme-surface rounded-md"
            )
            raids_tab = ui.tab("Raids").classes(
                "transition-all duration-200 hover-theme-surface rounded-md"
            )
            follows_tab = ui.tab("Follows").classes(
                "transition-all duration-200 hover-theme-surface rounded-md"
            )
            points_tab = ui.tab("Channel Points").classes(
                "transition-all duration-200 hover-theme-surface rounded-md"
            )

        # Add an on_change handler to the tabs to initialize values when tab changes
        alert_tabs.on(
            "change",
            lambda e: initialize_tab_values(e.args["value"])
            if e.args and "value" in e.args
            else None,
        )

        # Add a simple handler for Channel Points tab to load rewards immediately
        def simple_tab_handler(e):
            if e.value == "Channel Points":
                logger.debug("Channel Points tab selected, loading rewards immediately")

                # Simple direct load with short delay
                def direct_load():
                    try:
                        load_twitch_point_rewards()
                    except Exception as load_err:
                        logger.error(f"Error in direct load: {str(load_err)}")

                ui.timer(0.1, direct_load, once=True)

        alert_tabs.on("change", simple_tab_handler)

        # Main content area with tab panels
        with ui.tab_panels(alert_tabs, value=bits_tab).classes(
            "w-full h-[calc(100%-48px)] flex-grow"
        ):
            # Bits Alerts Tab
            with ui.tab_panel(bits_tab).classes(
                "transition-all duration-300 w-full h-full"
            ):
                create_alert_type_panel("bits")

            # Subscription Alerts Tab
            with ui.tab_panel(subs_tab).classes(
                "transition-all duration-300 w-full h-full"
            ):
                create_alert_type_panel("subs")

            # Gift Sub Alerts Tab
            with ui.tab_panel(giftsubs_tab).classes(
                "transition-all duration-300 w-full h-full"
            ):
                create_alert_type_panel("giftsubs")

            # Donation Alerts Tab
            with ui.tab_panel(donations_tab).classes(
                "transition-all duration-300 w-full h-full"
            ):
                create_alert_type_panel("donations")

            # Raid Alerts Tab
            with ui.tab_panel(raids_tab).classes(
                "transition-all duration-300 w-full h-full"
            ):
                create_alert_type_panel("raids")

            # Follow Alerts Tab
            with ui.tab_panel(follows_tab).classes(
                "transition-all duration-300 w-full h-full"
            ):
                create_alert_type_panel("follows")

            # Channel Points Tab
            with ui.tab_panel(points_tab).classes(
                "transition-all duration-300 w-full h-full"
            ):
                create_points_alert_panel()

                # Add a simple visibility-based loader for points tab
                def load_points_when_visible():
                    try:
                        # Simple check - if we're on points tab and select exists, load rewards
                        if alert_settings_state.current_tab == "points":
                            select_element = alert_settings_state.get_elements(
                                "points"
                            ).get("alert_select")
                            if select_element:
                                logger.debug(
                                    "Points tab panel visible, loading rewards directly"
                                )
                                load_twitch_point_rewards()
                            else:
                                logger.debug(
                                    "Points tab visible but select element not found yet"
                                )
                    except Exception as e:
                        logger.error(f"Error in load_points_when_visible: {str(e)}")

                # Schedule multiple attempts to load when this panel is created
                ui.timer(0.5, load_points_when_visible, once=True)
                ui.timer(1.5, load_points_when_visible, once=True)
                ui.timer(3.0, load_points_when_visible, once=True)

        # Initialize first tab immediately
        ui.timer(0.5, lambda: initialize_tab_values("Bits"), once=True)


def initialize_tab_values(tab_name):
    """Initialize values when a tab is selected"""
    try:
        # Map tab names to alert types
        tab_map = {
            "Bits": "bits",
            "Subscriptions": "subs",
            "Gift Subs": "giftsubs",
            "Donations": "donations",
            "Raids": "raids",
            "Follows": "follows",
            "Channel Points": "points",
        }

        alert_type = tab_map.get(tab_name, "bits")
        logger.debug(
            f"Tab changed to '{tab_name}', initializing values for alert type: '{alert_type}'"
        )

        # Set the current tab in the state manager
        alert_settings_state.set_current_tab(alert_type)
        logger.debug(f"Current tab set to: {alert_settings_state.current_tab}")

        # Reload alert data from Firebase to ensure we have the latest
        alertutils.alert_state_manager.reload_from_firebase()

        # Store current values for the tab
        store_original_values(alert_type)

        # Update all field styling
        update_all_fields_styling(alert_type)

        # Special handling for Channel Points tab - load Twitch rewards with simple approach
        if alert_type == "points":
            logger.debug("Channel Points tab detected, scheduling reward load")

            def simple_load_rewards():
                try:
                    logger.debug("Attempting to load Twitch point rewards")
                    load_twitch_point_rewards()
                except Exception as load_err:
                    logger.error(f"Error loading Twitch point rewards: {str(load_err)}")

            # Simple delayed load
            ui.timer(0.3, simple_load_rewards, once=True)

        # If alert_select has a valid value, try to reload that alert
        if (
            alert_settings_state.get_elements(alert_type).get("alert_select")
            and alert_settings_state.get_elements(alert_type).get("alert_select").value
            and alert_settings_state.get_elements(alert_type).get("alert_select").value
            not in ["new"]
        ):
            try:
                selected_alert_id = (
                    alert_settings_state.get_elements(alert_type)
                    .get("alert_select")
                    .value
                )
                logger.debug(f"Reloading currently selected alert: {selected_alert_id}")
                load_alert_settings(alert_type, selected_alert_id)
            except Exception as reload_err:
                logger.error(
                    f"Error reloading selected alert: {str(reload_err)}", exc_info=True
                )

        # Update delete button visibility based on current selection
        update_delete_button_visibility(alert_type)

    except Exception as e:
        logger.error(f"Error initializing tab values: {str(e)}", exc_info=True)


def create_alert_type_panel(alert_type: str):
    """Create a panel for a specific alert type with all settings

    Args:
        alert_type (str): The type of alert (bits, subs, etc.)
    """
    # Set the current tab in the state manager
    alert_settings_state.set_current_tab(alert_type)

    with ui.scroll_area().classes("w-full h-full"):
        with ui.column().classes("w-full gap-4 p-4"):
            # Alert selector section with improved styling
            with ui.row().classes("w-full items-center gap-2 mb-4"):
                with ui.row().classes("items-center"):
                    # Get existing alerts from AlertStateManager
                    alerts = get_alerts_for_type(alert_type)
                    alert_options = {
                        "new": "+ Create New Alert",
                    }
                    # Sort alerts and add to options
                    sorted_alerts = sort_alert_ids(alert_type, list(alerts.items()))
                    for alert_id, alert_data in sorted_alerts:
                        # Use the centralized display name method from AlertStateManager
                        display_name = alertutils.alert_state_manager.get_display_name(
                            alert_type, alert_id, alert_data
                        )
                        alert_options[alert_id] = display_name

                    # For subs tab, always ensure the fallback alert entry is present
                    if alert_type == "subs":
                        fallback_id = alertutils.AlertSettings.FALLBACK_ALERT_ID
                        if fallback_id not in alert_options:
                            alert_options[fallback_id] = "Resub Fallback"

                    alert_settings_state.get_elements(alert_type)["alert_select"] = (
                        ui.select(
                            alert_options,
                            label="Select Alert",
                            on_change=lambda e: handle_alert_selection(e, alert_type),
                        ).classes("w-64 bg-theme-base rounded-md")
                    )
                    ui.tooltip("Choose an existing alert or create a new one").classes(
                        "bg-theme-surface"
                    )

                # Alert type specific settings
                if alert_type in ["bits", "subs", "giftsubs", "donations", "raids"]:
                    with ui.row().classes("items-center gap-2"):
                        # Alert type toggle
                        with ui.row().classes("items-center"):
                            ui.label("Range Mode").classes("text-sm mr-2")
                            alert_settings_state.get_elements(alert_type)[
                                "range_toggle"
                            ] = ui.switch(
                                on_change=lambda e: toggle_range_inputs(
                                    e, exact_input_row, range_input_row
                                )
                            ).classes("mr-2 q-switch")
                            ui.tooltip(
                                "Toggle between exact amount and range mode"
                            ).classes("bg-theme-surface")

                        # Exact amount input
                        exact_input_row = ui.row().classes(
                            "items-center range-input exact-input"
                        )
                        with exact_input_row:
                            # Determine label and suffix based on alert type
                            if alert_type == "subs":
                                input_label = "Month"
                                input_suffix = None
                                tooltip_text = "Set the subscription month for this alert (1 for new sub, 2+ for resub months)"
                            elif alert_type == "bits":
                                input_label = "Amount"
                                input_suffix = " bits"
                                tooltip_text = (
                                    "Set the exact amount for this alert to trigger"
                                )
                            else:
                                input_label = "Amount"
                                input_suffix = None
                                tooltip_text = (
                                    "Set the exact amount for this alert to trigger"
                                )

                            alert_settings_state.get_elements(alert_type)[
                                "amount_input"
                            ] = ui.number(
                                label=input_label, suffix=input_suffix
                            ).classes("w-32 bg-theme-base rounded-md")
                            # Add change tracking - fixed to use element's current value
                            alert_settings_state.get_elements(alert_type)[
                                "amount_input"
                            ].on(
                                "change",
                                lambda e, at=alert_type: track_field_change(
                                    "amount_input",
                                    alert_settings_state.get_elements(at)[
                                        "amount_input"
                                    ],
                                    e,
                                    at,
                                ),
                            )
                            ui.tooltip(tooltip_text).classes("bg-theme-surface")

                        # Range inputs (hidden by default)
                        range_input_row = ui.row().classes(
                            "items-center gap-2 range-input range-inputs"
                        )
                        range_input_row.visible = False
                        with range_input_row:
                            with ui.row().classes("items-center"):
                                # Determine tooltip text for range based on alert type
                                if alert_type == "subs":
                                    min_tooltip = "Minimum subscription month for this alert range"
                                    max_tooltip = "Maximum subscription month for this alert range"
                                    range_suffix = None
                                elif alert_type == "bits":
                                    min_tooltip = "Minimum amount for this alert range"
                                    max_tooltip = "Maximum amount for this alert range"
                                    range_suffix = " bits"
                                else:
                                    min_tooltip = "Minimum amount for this alert range"
                                    max_tooltip = "Maximum amount for this alert range"
                                    range_suffix = None

                                alert_settings_state.get_elements(alert_type)[
                                    "min_input"
                                ] = ui.number(label="Min", suffix=range_suffix).classes(
                                    "w-24 bg-theme-base rounded-md"
                                )
                                # Add change tracking - fixed to use element's current value
                                alert_settings_state.get_elements(alert_type)[
                                    "min_input"
                                ].on(
                                    "change",
                                    lambda e, at=alert_type: track_field_change(
                                        "min_input",
                                        alert_settings_state.get_elements(at)[
                                            "min_input"
                                        ],
                                        e,
                                        at,
                                    ),
                                )
                                ui.tooltip(min_tooltip).classes(
                                    "bg-theme-surface"
                                )

                            with ui.row().classes("items-center"):
                                alert_settings_state.get_elements(alert_type)[
                                    "max_input"
                                ] = ui.number(label="Max", suffix=range_suffix).classes(
                                    "w-24 bg-theme-base rounded-md"
                                )
                                # Add change tracking - fixed to use element's current value
                                alert_settings_state.get_elements(alert_type)[
                                    "max_input"
                                ].on(
                                    "change",
                                    lambda e, at=alert_type: track_field_change(
                                        "max_input",
                                        alert_settings_state.get_elements(at)[
                                            "max_input"
                                        ],
                                        e,
                                        at,
                                    ),
                                )
                                ui.tooltip(max_tooltip).classes(
                                    "bg-theme-surface"
                                )

                # Resub fallback toggle (subs tab only, top-right corner)
                if alert_type == "subs":
                    ui.space()
                    with ui.row().classes("items-center"):
                        ui.label("Resub Fallback").classes("text-sm mr-2")
                        fallback_enabled = (
                            alertutils.alert_state_manager.get_resub_fallback_enabled()
                        )
                        alert_settings_state.get_elements(alert_type)[
                            "fallback_toggle"
                        ] = ui.switch(
                            value=fallback_enabled,
                            on_change=lambda e: alertutils.alert_state_manager.set_resub_fallback_enabled(
                                e.value
                            ),
                        ).classes("q-switch")
                        ui.tooltip(
                            "When enabled, the Resub Fallback alert plays for resubs "
                            "that don't match any configured month alert"
                        ).classes("bg-theme-surface")

            # Settings sections in a scrollable container with improved styling
            with ui.element("div").classes("content-card"):
                # Create a more compact layout with a grid of settings
                with ui.grid(columns=2).classes("w-full gap-4"):
                    # Left column - General and Audio settings
                    with ui.column().classes("w-full gap-2"):
                        # General Settings
                        with ui.expansion("General", icon="settings").classes(
                            "w-full bg-theme-base rounded-lg overflow-hidden"
                        ).style("border: 1px solid var(--color-border-default);"):
                            with ui.grid(columns=2).classes("w-full gap-2 p-2"):
                                # For points tab, add enable alert toggle
                                if alert_type == "points":
                                    with ui.card().classes("w-full p-3 rounded-lg col-span-2").style("background-color: var(--color-bg-surface);"):
                                        ui.label("Alert Configuration").classes("font-medium mb-2 text-sm")
                                        alert_settings_state.get_elements(alert_type)[
                                            "enable_alert_switch"
                                        ] = ui.switch("Enable as Alert").classes(
                                            "w-full q-switch"
                                        )
                                        alert_settings_state.get_elements(alert_type)[
                                            "enable_alert_switch"
                                        ].on(
                                            "change",
                                            lambda e, at=alert_type: track_field_change(
                                                "enable_alert_switch",
                                                alert_settings_state.get_elements(at)[
                                                    "enable_alert_switch"
                                                ],
                                                e,
                                                at,
                                            ),
                                        )
                                        ui.tooltip(
                                            "Enable this point reward as an alert in the app"
                                        ).classes("bg-theme-surface")

                                with ui.card().classes("w-full p-3 rounded-lg").style("background-color: var(--color-bg-surface);"):
                                    ui.label("Timing").classes("font-medium mb-2 text-sm")
                                    alert_settings_state.get_elements(alert_type)[
                                        "duration_input"
                                    ] = ui.number(
                                        "Duration", value=3.0, min=0.1, step=0.1
                                    ).classes("w-full bg-theme-base rounded-md")
                                    alert_settings_state.get_elements(alert_type)[
                                        "duration_input"
                                    ].on(
                                        "change",
                                        lambda e, at=alert_type: track_field_change(
                                            "duration_input",
                                            alert_settings_state.get_elements(at)[
                                                "duration_input"
                                            ],
                                            e,
                                            at,
                                        ),
                                    )
                                    ui.tooltip(
                                        "How long the alert will display (seconds)"
                                    ).classes("bg-theme-surface")

                                with ui.card().classes("w-full p-3 rounded-lg").style("background-color: var(--color-bg-surface);"):
                                    ui.label("Behavior").classes("font-medium mb-2 text-sm")
                                    alert_settings_state.get_elements(alert_type)[
                                        "stackable_switch"
                                    ] = ui.switch("Stackable").classes(
                                        "w-full q-switch"
                                    )
                                    # Add change tracking - fixed to use element's current value
                                    alert_settings_state.get_elements(alert_type)[
                                        "stackable_switch"
                                    ].on(
                                        "change",
                                        lambda e, at=alert_type: track_field_change(
                                            "stackable_switch",
                                            alert_settings_state.get_elements(at)[
                                                "stackable_switch"
                                            ],
                                            e,
                                            at,
                                        ),
                                    )
                                    ui.tooltip(
                                        "Allow multiple alerts of this type to stack"
                                    ).classes("bg-theme-surface")

                        # Audio Settings
                        create_audio_settings_section(alert_type)
                        create_tts_settings_section(alert_type)

                    # Right column - Visual settings and Preview
                    with ui.column().classes("w-full gap-2"):
                        # Visual Settings
                        with ui.expansion("Visual", icon="image").classes(
                            "w-full bg-theme-base rounded-lg overflow-hidden"
                        ).style("border: 1px solid var(--color-border-default);"):
                            with ui.grid(columns=1).classes("w-full gap-2 p-2"):
                                with ui.card().classes("w-full p-3 rounded-lg").style("background-color: var(--color-bg-surface);"):
                                    ui.label("GIF Directory").classes("font-medium mb-2 text-sm")
                                    alert_settings_state.get_elements(alert_type)[
                                        "gif_dir_input"
                                    ] = ui.input("GIF Directory").classes(
                                        "w-full bg-theme-base rounded-md text-sm"
                                    )
                                    alert_settings_state.get_elements(alert_type)[
                                        "gif_dir_input"
                                    ].on(
                                        "change",
                                        lambda e, at=alert_type: track_field_change(
                                            "gif_dir_input",
                                            alert_settings_state.get_elements(at)[
                                                "gif_dir_input"
                                            ],
                                            e,
                                            at,
                                        ),
                                    )
                                    ui.tooltip(
                                        "Directory containing the GIF file"
                                    ).classes("bg-theme-surface")

                                with ui.card().classes("w-full p-3 rounded-lg").style("background-color: var(--color-bg-surface);"):
                                    ui.label("GIF File").classes("font-medium mb-2 text-sm")
                                    alert_settings_state.get_elements(alert_type)[
                                        "gif_file_input"
                                    ] = ui.input("GIF File").classes(
                                        "w-full bg-theme-base rounded-md text-sm"
                                    )
                                    alert_settings_state.get_elements(alert_type)[
                                        "gif_file_input"
                                    ].on(
                                        "change",
                                        lambda e, at=alert_type: track_field_change(
                                            "gif_file_input",
                                            alert_settings_state.get_elements(at)[
                                                "gif_file_input"
                                            ],
                                            e,
                                            at,
                                        ),
                                    )
                                    ui.tooltip("Name of the GIF file").classes(
                                        "bg-theme-surface"
                                    )

                                with ui.card().classes("w-full p-3 rounded-lg").style("background-color: var(--color-bg-surface);"):
                                    ui.label("File Selection").classes("font-medium mb-2 text-sm")
                                    gif_browse_btn = ui.button(
                                        "Browse",
                                        icon="folder",
                                        on_click=lambda: handle_browse("gif"),
                                    ).classes(
                                        "control-button bg-theme-surface hover-theme-overlay transition-colors duration-200 text-sm"
                                    )
                                    ui.tooltip("Browse for the GIF file").classes(
                                        "bg-theme-surface"
                                    )

                        # Randomizer Settings
                        create_randomizer_settings_section(alert_type)

                        # Twitch Options (only for points tab)
                        if alert_type == "points":
                            with ui.expansion("Twitch Options", icon="api").classes(
                                "w-full bg-theme-base rounded-lg overflow-hidden"
                            ).style("border: 1px solid var(--color-border-default);"):
                                create_twitch_options_section(alert_type)

            # Save and Test buttons at the bottom
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                with ui.row().classes("items-center"):
                    test_btn = ui.button(
                        "Test Alert",
                        icon="play_arrow",
                        on_click=lambda: test_alert(alert_type),
                    ).classes(
                        "btn-secondary transition-colors duration-200 text-sm"
                    )
                    ui.tooltip("Test the current alert settings").classes(
                        "bg-theme-surface"
                    )

                with ui.row().classes("items-center"):
                    # Delete button - only show for existing alerts
                    alert_settings_state.get_elements(alert_type)["delete_btn"] = (
                        ui.button(
                            "Delete Alert",
                            icon="delete",
                            on_click=lambda: show_delete_confirmation(alert_type),
                        ).classes(
                            "alert-delete-btn transition-colors duration-200 text-sm"
                        )
                    )
                    alert_settings_state.get_elements(alert_type)[
                        "delete_btn"
                    ].visible = False  # Hidden by default
                    ui.tooltip("Delete this alert permanently").classes(
                        "bg-theme-surface"
                    )

                with ui.row().classes("items-center"):
                    save_btn = ui.button(
                        "Save Alert",
                        icon="save",
                        on_click=lambda: save_alert(alert_type),
                    ).classes("alert-save-btn transition-colors duration-200 text-sm")
                    ui.tooltip("Save your alert settings").classes(
                        "bg-theme-surface"
                    )

            # At the end of building the tab, trigger the default alert to load or store original values
            ui.timer(0.5, lambda: initialize_alert_values(alert_type), once=True)

            # Register all UI elements for tracking
            ui.timer(1.0, register_ui_elements, once=True)

            # Log to verify that the panel was created
            logger.debug(f"Created alert panel for {alert_type}")
            # Also log which UI elements were registered
            logger.debug(
                f"Registered UI elements: {list(alert_settings_state.get_elements(alert_type).keys())}"
            )


def sort_alert_ids(alert_type: str, alert_items: list) -> list:
    """Sort alert IDs based on alert type - numerically for most types, alphabetically for points

    Args:
        alert_type (str): The type of alert (bits, subs, etc.)
        alert_items (list): List of (alert_id, alert_data) tuples

    Returns:
        list: Sorted list of (alert_id, alert_data) tuples
    """
    try:
        if alert_type == "points":
            # For points, sort alphabetically by display name
            return sorted(
                alert_items,
                key=lambda x: alertutils.alert_state_manager.get_display_name(
                    alert_type, x[0], x[1]
                ).lower(),
            )
        else:
            # For other alert types, sort numerically by extracting numbers from alert ID
            def extract_sort_key(item):
                alert_id = item[0]

                # Always push the resub fallback alert to the very end
                if alert_id == alertutils.AlertSettings.FALLBACK_ALERT_ID:
                    return (float("inf"), float("inf"), 1)

                # Remove the alert type prefix to get the numeric part
                numeric_part = alert_id
                if alert_id.startswith(alert_type):
                    numeric_part = alert_id[len(alert_type) :]

                # Handle range alerts (e.g., "100-500")
                if "-" in numeric_part:
                    try:
                        min_val, max_val = numeric_part.split("-")
                        return (
                            int(min_val),
                            int(max_val),
                            0,
                        )  # Sort by min value, then max value
                    except ValueError:
                        return (
                            float("inf"),
                            float("inf"),
                            0,
                        )  # Put invalid formats at the end
                else:
                    # Handle exact alerts (e.g., "100")
                    try:
                        return (
                            int(numeric_part),
                            0,
                            0,
                        )  # Use 0 as secondary sort to put exact before ranges with same min
                    except ValueError:
                        return (
                            float("inf"),
                            float("inf"),
                            0,
                        )  # Put invalid formats at the end

            return sorted(alert_items, key=extract_sort_key)
    except Exception as e:
        logger.error(f"Error sorting alert IDs: {str(e)}", exc_info=True)
        return alert_items  # Return unsorted if there's an error


def get_alerts_for_type(alert_type: str) -> dict:
    """Get all alerts of a specific type from the AlertStateManager

    Args:
        alert_type (str): The type of alert (bits, subs, etc.)

    Returns:
        dict: Dictionary of alerts for the specified type
    """
    try:
        # Initialize the alert state manager
        alertutils.initialize_alert_state()

        # Get alerts from AlertStateManager
        alerts = alertutils.alert_state_manager.get_alerts_for_type(alert_type)
        if not alerts:
            logger.debug(f"No alerts found for type: {alert_type}")
            return {}

        return alerts
    except Exception as e:
        logger.error(
            f"Error getting alerts for type {alert_type}: {str(e)}", exc_info=True
        )
        return {}


def toggle_range_inputs(e, exact_row, range_row):
    """Toggle between exact amount and range inputs

    Args:
        e: The selection event
        exact_row: The row containing the exact amount input
        range_row: The row containing the range inputs
    """
    # Toggle visibility based on switch state
    if not e.value:  # Switch is off (exact mode)
        exact_row.visible = True
        range_row.visible = False
    else:  # Switch is on (range mode)
        exact_row.visible = False
        range_row.visible = True


def handle_alert_selection(e, alert_type: str):
    """Handle when an alert is selected from the dropdown

    Args:
        e: The selection event
        alert_type (str): The type of alert (bits, subs, etc.)
    """
    try:
        fallback_id = alertutils.AlertSettings.FALLBACK_ALERT_ID
        is_fallback = alert_type == "subs" and e.value == fallback_id

        if e.value == "new":
            # Show notification for creating new alert
            ui.notify("Creating new alert...", type="info")
            # Load default values for new alert
            set_default_values_for_new_alert(alert_type)
            # Store initial values as originals
            store_original_values(alert_type)
        elif is_fallback:
            # Check if saved fallback data exists
            fallback_data = alertutils.alert_state_manager.get_alert_by_id(
                alert_type, fallback_id
            )
            if fallback_data:
                ui.notify("Loading Resub Fallback alert...", type="info")
                load_alert_settings(alert_type, fallback_id)
            else:
                ui.notify("Configuring new Resub Fallback alert...", type="info")
                set_default_values_for_new_alert(alert_type)
                store_original_values(alert_type)
        else:
            # Show notification for loading selected alert
            ui.notify(f"Loading alert: {e.value}", type="info")
            # Load selected alert settings
            load_alert_settings(alert_type, e.value)

        # Update delete button visibility based on selection
        update_delete_button_visibility(alert_type)

        # Hide/show amount and range inputs when the fallback alert is selected
        _update_amount_inputs_visibility(alert_type, is_fallback)

    except Exception as e:
        logger.error(f"Error handling alert selection: {str(e)}", exc_info=True)
        ui.notify("Error loading alert settings", type="negative")


def _update_amount_inputs_visibility(alert_type: str, hide: bool):
    """Show or hide the amount/range input controls and the range toggle.

    Args:
        alert_type: The alert type tab.
        hide: If True, hide the controls; otherwise restore them.
    """
    elements = alert_settings_state.get_elements(alert_type)

    # Amount input (exact mode)
    amount_input = elements.get("amount_input")
    if amount_input and hasattr(amount_input, "parent_slot"):
        # Walk up to the containing exact_input_row
        try:
            amount_input.visible = not hide
        except Exception:
            pass

    # Min / Max inputs (range mode)
    for key in ("min_input", "max_input"):
        inp = elements.get(key)
        if inp:
            try:
                inp.visible = not hide
            except Exception:
                pass

    # Range toggle switch
    range_toggle = elements.get("range_toggle")
    if range_toggle:
        try:
            range_toggle.visible = not hide
        except Exception:
            pass


def set_default_values_for_new_alert(alert_type: str):
    """Set default values for a new alert based on alert type

    Args:
        alert_type (str): The type of alert (bits, subs, etc.)
    """
    # Set common default values
    alert_settings_state.get_elements(alert_type)["duration_input"].value = 3.0
    alert_settings_state.get_elements(alert_type)["stackable_switch"].value = False
    alert_settings_state.get_elements(alert_type)["fade_in_input"].value = 0
    alert_settings_state.get_elements(alert_type)["fade_out_input"].value = 0
    update_volume_value(alert_type, 100)

    # Empty sound directories - let user enter their own
    alert_settings_state.get_elements(alert_type)["primary_dir_input"].value = ""
    alert_settings_state.get_elements(alert_type)["primary_file_input"].value = ""
    alert_settings_state.get_elements(alert_type)["randomized_switch"].value = False
    alert_settings_state.get_elements(alert_type)["random_dir_input"].value = ""
    alert_settings_state.get_elements(alert_type)["random_chance_input"].value = 0
    alert_settings_state.get_elements(alert_type)[
        "randomized_extra_switch"
    ].value = False
    alert_settings_state.get_elements(alert_type)["extra_dir_input"].value = ""
    alert_settings_state.get_elements(alert_type)["extra_chance_input"].value = 0

    # Empty GIF settings - let user enter their own
    alert_settings_state.get_elements(alert_type)["gif_dir_input"].value = ""
    alert_settings_state.get_elements(alert_type)["gif_file_input"].value = ""
    alert_settings_state.get_elements(alert_type)["tts_enabled_switch"].value = False
    alert_settings_state.get_elements(alert_type)["tts_source_select"].value = (
        "alert_message"
    )
    alert_settings_state.get_elements(alert_type)["tts_custom_message_input"].value = ""
    update_tts_custom_message_visibility(alert_type)

    # Set appropriate default values for amount-based alerts
    if alert_type in ["bits", "subs", "giftsubs", "donations", "raids"]:
        # Default to exact mode for simplicity
        alert_settings_state.get_elements(alert_type)["range_toggle"].value = False

        # Set reasonable default values based on alert type
        if alert_type == "bits":
            alert_settings_state.get_elements(alert_type)["amount_input"].value = 100
        elif alert_type == "subs":
            alert_settings_state.get_elements(alert_type)["amount_input"].value = 1
        elif alert_type == "giftsubs":
            alert_settings_state.get_elements(alert_type)["amount_input"].value = 1
        elif alert_type == "donations":
            alert_settings_state.get_elements(alert_type)["amount_input"].value = 5
        elif alert_type == "raids":
            alert_settings_state.get_elements(alert_type)["amount_input"].value = 10

        # Default values for range mode too
        if alert_type == "subs":
            # For subs, range would be something like months 1-3, 4-6, etc.
            alert_settings_state.get_elements(alert_type)["min_input"].value = 1
            alert_settings_state.get_elements(alert_type)["max_input"].value = 3
        else:
            alert_settings_state.get_elements(alert_type)[
                "min_input"
            ].value = alert_settings_state.get_elements(alert_type)[
                "amount_input"
            ].value
            alert_settings_state.get_elements(alert_type)["max_input"].value = (
                alert_settings_state.get_elements(alert_type)["amount_input"].value * 5
            )


def clear_alert_inputs(tab_type: str):
    """Clear all alert input fields for a specific tab

    Args:
        tab_type (str): The type of alert (bits, subs, etc.)
    """
    # Clear duration
    alert_settings_state.get_elements(tab_type)["duration_input"].value = 3.0

    # Clear stackable switch
    alert_settings_state.get_elements(tab_type)["stackable_switch"].value = False

    # Clear audio settings
    alert_settings_state.get_elements(tab_type)["fade_in_input"].value = 0
    alert_settings_state.get_elements(tab_type)["fade_out_input"].value = 0
    update_volume_value(tab_type, 100)

    # Clear sound inputs
    alert_settings_state.get_elements(tab_type)["primary_dir_input"].value = ""
    alert_settings_state.get_elements(tab_type)["primary_file_input"].value = ""
    alert_settings_state.get_elements(tab_type)["randomized_switch"].value = False
    alert_settings_state.get_elements(tab_type)["random_dir_input"].value = ""
    alert_settings_state.get_elements(tab_type)["random_chance_input"].value = 0
    alert_settings_state.get_elements(tab_type)["randomized_extra_switch"].value = False
    alert_settings_state.get_elements(tab_type)["extra_dir_input"].value = ""
    alert_settings_state.get_elements(tab_type)["extra_chance_input"].value = 0

    # Clear GIF inputs
    alert_settings_state.get_elements(tab_type)["gif_dir_input"].value = ""
    alert_settings_state.get_elements(tab_type)["gif_file_input"].value = ""
    alert_settings_state.get_elements(tab_type)["tts_enabled_switch"].value = False
    alert_settings_state.get_elements(tab_type)["tts_source_select"].value = (
        "alert_message"
    )
    alert_settings_state.get_elements(tab_type)["tts_custom_message_input"].value = ""
    update_tts_custom_message_visibility(tab_type)


def load_alert_settings(alert_type: str, alert_id: str):
    """Load alert settings from AlertStateManager

    Args:
        alert_type (str): The type of alert (bits, subs, etc.)
        alert_id (str): The ID of the alert to load
    """
    try:
        # Ensure the current tab is set correctly
        alert_settings_state.set_current_tab(alert_type)

        # Get alert from AlertStateManager
        alert_data = alertutils.alert_state_manager.get_alert_by_id(
            alert_type, alert_id
        )
        if not alert_data:
            logger.error(f"Alert {alert_id} not found for type {alert_type}")
            ui.notify(
                f"Alert {alert_id} not found for type {alert_type}", type="negative"
            )
            return

        logger.debug(f"Loading alert data: {alert_data}")

        # Debug: Log specifically the GIF data from the database
        logger.debug(
            f"DEBUG: GIF data from database - gif_dir: '{alert_data.get('gif_dir')}' (type: {type(alert_data.get('gif_dir'))})"
        )
        logger.debug(
            f"DEBUG: GIF data from database - gif_name: '{alert_data.get('gif_name')}' (type: {type(alert_data.get('gif_name'))})"
        )

        # Verify UI elements exist before attempting to update them
        elements = alert_settings_state.get_elements(alert_type)
        required_elements = [
            "duration_input",
            "stackable_switch",
            "fade_in_input",
            "fade_out_input",
            "volume_input",
            "gif_dir_input",
            "gif_file_input",
        ]

        missing_elements = [
            elem
            for elem in required_elements
            if elem not in elements or elements[elem] is None
        ]
        if missing_elements:
            logger.error(f"Missing UI elements for {alert_type}: {missing_elements}")
            ui.notify(
                f"UI elements not ready for {alert_type}. Please try again.",
                type="warning",
            )
            return

        # Update UI with alert data - use get with defaults to handle missing fields
        elements["duration_input"].value = float(alert_data.get("duration", 3.0))
        elements["stackable_switch"].value = bool(alert_data.get("stackable", False))

        # Update audio settings
        elements["fade_in_input"].value = int(alert_data.get("fade_in", 0))
        elements["fade_out_input"].value = int(alert_data.get("fade_out", 0))
        update_volume_value(alert_type, int(alert_data.get("volume", 100)))
        elements["tts_enabled_switch"].value = bool(alert_data.get("tts_enabled", False))
        elements["tts_source_select"].value = str(
            alert_data.get("tts_source", "alert_message") or "alert_message"
        )
        elements["tts_custom_message_input"].value = str(
            alert_data.get("tts_custom_message", "") or ""
        )
        update_tts_custom_message_visibility(alert_type)
        # Only set audio_only_switch if it exists (only available in points alerts)
        if "audio_only_switch" in elements and elements["audio_only_switch"]:
            elements["audio_only_switch"].value = bool(
                alert_data.get("audio_only", False)
            )

        # Log the audio file paths for debugging
        logger.debug(
            f"Audio path data in alert: single_audio_dir={alert_data.get('single_audio_dir')}, single_audio_name={alert_data.get('single_audio_name')}"
        )

        # Update sound settings - handle None values and convert to strings
        single_dir = alert_data.get("single_audio_dir")
        single_file = alert_data.get("single_audio_name")
        random_dir = alert_data.get("randomized_dir")
        extra_dir = alert_data.get("randomized_extra_dir")

        # Ensure we're setting strings, not None values
        elements["primary_dir_input"].value = (
            "" if single_dir is None else str(single_dir)
        )
        elements["primary_file_input"].value = (
            "" if single_file is None else str(single_file)
        )
        elements["randomized_switch"].value = bool(alert_data.get("randomized", False))
        elements["random_dir_input"].value = (
            "" if random_dir is None else str(random_dir)
        )
        elements["random_chance_input"].value = int(
            alert_data.get("randomized_chance", 0)
        )
        elements["randomized_extra_switch"].value = bool(
            alert_data.get("randomized_extra", False)
        )
        elements["extra_dir_input"].value = "" if extra_dir is None else str(extra_dir)
        elements["extra_chance_input"].value = int(
            alert_data.get("randomized_extra_chance", 0)
        )

        # Log the values we're setting for sound paths
        logger.debug(
            f"Setting primary_dir_input to: {elements['primary_dir_input'].value}"
        )
        logger.debug(
            f"Setting primary_file_input to: {elements['primary_file_input'].value}"
        )
        logger.debug(
            f"Setting random_dir_input to: {elements['random_dir_input'].value}"
        )
        logger.debug(f"Setting extra_dir_input to: {elements['extra_dir_input'].value}")

        # Update GIF settings - handle None values and convert to strings
        gif_dir = alert_data.get("gif_dir")
        gif_file = alert_data.get("gif_name")

        # Log the GIF file paths for debugging
        logger.debug(f"GIF path data in alert: gif_dir={gif_dir}, gif_name={gif_file}")

        # Ensure we're setting strings, not None values and that the elements exist
        if "gif_dir_input" in elements and elements["gif_dir_input"] is not None:
            gif_dir_value = "" if gif_dir is None else str(gif_dir)
            logger.debug(
                f"DEBUG: About to set gif_dir_input from '{gif_dir}' to '{gif_dir_value}'"
            )
            elements["gif_dir_input"].value = gif_dir_value
            logger.debug(
                f"DEBUG: After setting gif_dir_input, element.value is: '{elements['gif_dir_input'].value}'"
            )
        else:
            logger.error(f"gif_dir_input element not found or is None for {alert_type}")

        if "gif_file_input" in elements and elements["gif_file_input"] is not None:
            gif_file_value = "" if gif_file is None else str(gif_file)
            logger.debug(
                f"DEBUG: About to set gif_file_input from '{gif_file}' to '{gif_file_value}'"
            )
            elements["gif_file_input"].value = gif_file_value
            logger.debug(
                f"DEBUG: After setting gif_file_input, element.value is: '{elements['gif_file_input'].value}'"
            )
        else:
            logger.error(
                f"gif_file_input element not found or is None for {alert_type}"
            )

        # Force a UI update by using the update method if available
        for field_name, element in elements.items():
            if element and hasattr(element, "update"):
                try:
                    element.update()
                except Exception as update_err:
                    logger.error(
                        f"Error updating element {field_name}: {str(update_err)}"
                    )

        # Multiple refresh attempts to ensure GIF inputs display properly
        def force_ui_refresh_multiple():
            try:
                for attempt in range(3):  # Try 3 times
                    logger.debug(f"Force refresh attempt {attempt + 1}")

                    # Specifically refresh GIF inputs which seem to have display issues
                    gif_dir_element = elements.get("gif_dir_input")
                    gif_file_element = elements.get("gif_file_input")

                    if gif_dir_element and hasattr(gif_dir_element, "value"):
                        current_gif_dir = gif_dir_element.value
                        logger.debug(
                            f"Force refreshing gif_dir_input with value: {current_gif_dir}"
                        )
                        # Force update by setting value again and calling update if available
                        gif_dir_element.value = current_gif_dir
                        if hasattr(gif_dir_element, "update"):
                            gif_dir_element.update()

                    if gif_file_element and hasattr(gif_file_element, "value"):
                        current_gif_file = gif_file_element.value
                        logger.debug(
                            f"Force refreshing gif_file_input with value: {current_gif_file}"
                        )
                        # Force update by setting value again and calling update if available
                        gif_file_element.value = current_gif_file
                        if hasattr(gif_file_element, "update"):
                            gif_file_element.update()

                    # Also force update on primary sound inputs as they may have similar issues
                    for input_name in [
                        "primary_dir_input",
                        "primary_file_input",
                        "random_dir_input",
                        "extra_dir_input",
                        "tts_custom_message_input",
                    ]:
                        element = elements.get(input_name)
                        if element and hasattr(element, "value"):
                            current_value = element.value
                            element.value = current_value
                            if hasattr(element, "update"):
                                element.update()

            except Exception as refresh_err:
                logger.error(f"Error in force UI refresh: {str(refresh_err)}")

        # Schedule multiple refresh attempts with delays
        ui.timer(0.1, force_ui_refresh_multiple, once=True)
        ui.timer(0.3, force_ui_refresh_multiple, once=True)
        ui.timer(0.5, force_ui_refresh_multiple, once=True)

        # Update range/exact inputs if applicable
        is_fallback = alert_id == alertutils.AlertSettings.FALLBACK_ALERT_ID
        if alert_type in ["bits", "subs", "giftsubs", "donations", "raids"]:
            if is_fallback:
                # Fallback alert has no amount/range — hide controls
                _update_amount_inputs_visibility(alert_type, hide=True)
            else:
                # Ensure controls are visible for normal alerts
                _update_amount_inputs_visibility(alert_type, hide=False)

                # Remove the alert_type prefix to get just the numeric part
                numeric_part = alert_id
                if alert_id.startswith(alert_type):
                    numeric_part = alert_id[len(alert_type) :]

                if "-" in numeric_part:  # Range alert
                    elements["range_toggle"].value = True
                    try:
                        min_val, max_val = numeric_part.split("-")
                        elements["min_input"].value = int(min_val)
                        elements["max_input"].value = int(max_val)
                    except ValueError:
                        logger.error(
                            f"Error parsing range values from alert ID: {alert_id}"
                        )
                        elements["min_input"].value = 0
                        elements["max_input"].value = 0
                else:  # Exact alert
                    elements["range_toggle"].value = False
                    try:
                        elements["amount_input"].value = int(numeric_part)
                    except ValueError:
                        logger.error(
                            f"Error parsing exact value from alert ID: {alert_id}"
                        )
                        elements["amount_input"].value = 0

        # Store the original values for change detection
        store_original_values(alert_type)

        # Clear any changed styling
        clear_changed_styling(alert_type)

        # Log successful loading
        logger.debug(f"Successfully loaded alert {alert_id} for type {alert_type}")

        # Update delete button visibility
        update_delete_button_visibility(alert_type)

    except Exception as e:
        logger.error(f"Error loading alert settings: {str(e)}", exc_info=True)
        ui.notify("Error loading alert settings", type="negative")


def handle_browse(folder_type: str):
    """Handle browsing for files/folders using a custom file browser dialog

    Args:
        folder_type (str): Type of folder being browsed (primary, random, extra, gif)
    """
    try:
        # Get the current alert type from the global state
        current_alert_type = alert_settings_state.current_tab
        if not current_alert_type:
            ui.notify("No alert type selected", type="warning")
            return

        # Get UI elements to check what fields exist
        elements = alert_settings_state.get_elements(current_alert_type)

        # Determine browse configuration based on folder type and available fields
        if folder_type == "gif":
            # GIF selection - both directory and file fields exist
            dir_field_name = "gif_dir_input"
            file_field_name = "gif_file_input"
            title = "Select GIF File"
            extensions = [".gif", ".png", ".jpg", ".jpeg", ".webp"]
            browse_mode = "file"  # Select files and split into dir/file
            target_fields = [dir_field_name, file_field_name]
        elif folder_type == "primary":
            # Primary audio - both directory and file fields exist
            dir_field_name = "primary_dir_input"
            file_field_name = "primary_file_input"
            title = "Select Primary Audio File"
            extensions = [".mp3", ".wav", ".ogg", ".m4a", ".flac"]
            browse_mode = "file"  # Select files and split into dir/file
            target_fields = [dir_field_name, file_field_name]
        elif folder_type == "random":
            # Random audio - only directory field exists
            dir_field_name = "random_dir_input"
            title = "Select Random Audio Directory"
            extensions = [
                ".mp3",
                ".wav",
                ".ogg",
                ".m4a",
                ".flac",
            ]  # For display purposes
            browse_mode = "directory"  # Select directories only
            target_fields = [dir_field_name]
        elif folder_type == "extra":
            # Extra audio - only directory field exists
            dir_field_name = "extra_dir_input"
            title = "Select Extra Audio Directory"
            extensions = [
                ".mp3",
                ".wav",
                ".ogg",
                ".m4a",
                ".flac",
            ]  # For display purposes
            browse_mode = "directory"  # Select directories only
            target_fields = [dir_field_name]
        else:
            ui.notify(f"Unknown folder type: {folder_type}", type="negative")
            return

        # Verify that the required fields exist
        missing_fields = [
            field
            for field in target_fields
            if field not in elements or elements[field] is None
        ]
        if missing_fields:
            ui.notify(
                f"Required UI fields not found: {missing_fields}", type="negative"
            )
            return

        # Get the initial path from the corresponding directory input field
        initial_path = get_initial_browse_path(current_alert_type, dir_field_name)

        # Show the file browser dialog
        show_file_browser_dialog(
            current_alert_type,
            folder_type,
            title,
            extensions,
            target_fields,
            initial_path,
            browse_mode,
        )

    except Exception as e:
        logger.error(
            f"Error handling browse for {folder_type}: {str(e)}", exc_info=True
        )
        ui.notify(f"Error opening file browser for {folder_type}", type="negative")


def get_initial_browse_path(alert_type: str, dir_field_name: str) -> str:
    """Get the initial path for the browse dialog based on the directory input field

    Args:
        alert_type (str): The alert type (bits, subs, etc.)
        dir_field_name (str): The name of the directory input field

    Returns:
        str: The initial path to use for the browse dialog (with forward slashes, within assets folder)
    """
    try:
        import os
        from pathlib import Path, PurePath

        from .. import path_utils

        # Get the assets directory as the base path (restrict browsing to assets only)
        assets_dir = Path(path_utils.get_assets_path()).resolve()

        # Get UI elements for this alert type
        elements = alert_settings_state.get_elements(alert_type)

        # Check if the directory field exists and has a value
        if dir_field_name in elements and elements[dir_field_name]:
            existing_dir = elements[dir_field_name].value
            if existing_dir and existing_dir.strip():
                try:
                    # Clean up the path - remove leading/trailing slashes and normalize separators
                    cleaned_dir = existing_dir.strip()

                    # Handle web paths that start with /assets/
                    if cleaned_dir.startswith("/assets/"):
                        # Remove the /assets/ prefix to get the relative path within assets
                        cleaned_dir = cleaned_dir[8:]  # Remove '/assets/'
                    elif cleaned_dir.startswith("assets/"):
                        # Remove the assets/ prefix to get the relative path within assets
                        cleaned_dir = cleaned_dir[7:]  # Remove 'assets/'

                    # Remove leading and trailing slashes
                    cleaned_dir = cleaned_dir.strip("/")

                    # If cleaned path is empty, start at assets root
                    if not cleaned_dir:
                        logger.debug(
                            "Directory path is empty after cleaning, using assets root"
                        )
                        return _alert_browser_path_str(assets_dir)

                    # Convert cleaned_dir to a Path object and handle OS differences properly
                    relative_path = PurePath(cleaned_dir)

                    # Construct path within assets directory
                    full_path = assets_dir / relative_path
                    resolved_path = full_path.resolve()

                    # Security check: ensure the resolved path is still within the assets directory
                    try:
                        # This will raise ValueError if resolved_path is not within assets_dir
                        resolved_path.relative_to(assets_dir)

                        if resolved_path.exists() and resolved_path.is_dir():
                            logger.debug(
                                f"Using existing directory path for browse: {resolved_path}"
                            )
                            return _alert_browser_path_str(resolved_path)
                        else:
                            logger.debug(
                                f"Constructed directory path doesn't exist: {resolved_path}"
                            )
                            # Try to find the closest existing parent directory within assets
                            current_path = resolved_path
                            while current_path != current_path.parent:
                                try:
                                    # Check if still within assets
                                    current_path.relative_to(assets_dir)
                                    if current_path.exists() and current_path.is_dir():
                                        closest_path = _alert_browser_path_str(current_path)
                                        logger.debug(
                                            f"Using closest existing parent directory: {closest_path}"
                                        )
                                        return closest_path
                                    current_path = current_path.parent
                                except ValueError:
                                    # Went outside assets directory
                                    break

                            # If no parent directories exist in the constructed path within assets,
                            # try to find existing parts starting from the assets directory
                            logger.debug(
                                "No constructed path parents exist, checking from assets directory"
                            )
                            path_parts = relative_path.parts
                            current_check = assets_dir

                            for part in path_parts:
                                next_path = current_check / part
                                if next_path.exists() and next_path.is_dir():
                                    current_check = next_path
                                else:
                                    break

                            if current_check != assets_dir:
                                deepest_path = _alert_browser_path_str(current_check)
                                logger.debug(
                                    f"Using deepest existing directory: {deepest_path}"
                                )
                                return deepest_path

                    except ValueError:
                        logger.warning(
                            f"Path '{cleaned_dir}' would escape assets directory, using assets root"
                        )

                except Exception as path_err:
                    logger.debug(
                        f"Error processing directory path '{existing_dir}': {str(path_err)}"
                    )

        # Fall back to assets directory
        fallback_path = _alert_browser_path_str(assets_dir)
        logger.debug(f"Using default assets directory for browse: {fallback_path}")
        return fallback_path

    except Exception as e:
        logger.error(f"Error getting initial browse path: {str(e)}", exc_info=True)
        # Final fallback to assets directory
        from .. import path_utils

        return _alert_browser_path_str(Path(path_utils.get_assets_path()))


def show_file_browser_dialog(
    alert_type: str,
    folder_type: str,
    title: str,
    extensions: list,
    target_fields: list,
    initial_path: str | None = None,
    browse_mode: str = "file",
):
    """Show a NiceGUI-based file browser dialog for alert files

    Args:
        alert_type (str): The alert type (bits, subs, etc.)
        folder_type (str): Type of folder being browsed (primary, random, extra, gif)
        title (str): Dialog title
        extensions (list): List of allowed file extensions (e.g., ['.mp3', '.wav'])
        target_fields (list): List of UI field names to update [dir_field, file_field]
        initial_path (str, optional): Initial path to start browsing from
        browse_mode (str, optional): Mode of browsing ('file' or 'directory')
    """
    import os
    from pathlib import Path

    from .. import path_utils

    # Use provided initial path or fall back to assets directory (POSIX in dialog)
    raw_start = (
        initial_path if initial_path else str(Path(path_utils.get_assets_path()))
    )
    start_path = _alert_browser_path_str(Path(raw_start).expanduser().resolve())

    # Create dialog state
    dialog_state = {
        "current_path": start_path,
        "selected_file": None,
        "path_input": None,
        "file_list": None,
        "alert_type": alert_type,
        "folder_type": folder_type,
        "extensions": extensions,
        "target_fields": target_fields,
        "browse_mode": browse_mode,
    }

    with ui.dialog().props(_FILE_BROWSER_DIALOG_PROPS) as dialog, ui.card().classes(
        _FILE_BROWSER_CARD_CLASSES
    ):
        ui.label(title).classes("text-lg font-bold mb-4 shrink-0")

        with ui.column().classes("w-full min-h-0 flex-1 gap-3"):
            # Current path display and manual entry
            with ui.row().classes("w-full items-center gap-2"):
                ui.label("Path:").classes("text-sm font-medium")
                dialog_state["path_input"] = ui.input(
                    value=dialog_state["current_path"],
                    placeholder="Enter file path or navigate below..."
                    if dialog_state["browse_mode"] == "file"
                    else "Enter directory path or navigate below...",
                ).classes("flex-1")
                ui.button(
                    "Go", icon="folder", on_click=lambda: navigate_to_path(dialog_state)
                ).props("dense")

            # Quick access buttons - assets folder specific
            with ui.row().classes("w-full gap-2 mb-2 shrink-0 flex-wrap"):
                ui.button(
                    "Assets Root",
                    icon="home",
                    on_click=lambda: navigate_to_path(
                        dialog_state, Path(path_utils.get_assets_path())
                    ),
                ).props("dense outline size=sm")

                ui.button(
                    "Default Assets",
                    icon="folder",
                    on_click=lambda: navigate_to_path(
                        dialog_state,
                        Path(path_utils.get_assets_path()) / "default_assets",
                    ),
                ).props("dense outline size=sm")

                ui.button(
                    "Images",
                    icon="image",
                    on_click=lambda: navigate_to_path(
                        dialog_state,
                        Path(path_utils.get_assets_path())
                        / "default_assets"
                        / "images",
                    ),
                ).props("dense outline size=sm")

                ui.button(
                    "Sounds",
                    icon="audiotrack",
                    on_click=lambda: navigate_to_path(
                        dialog_state,
                        Path(path_utils.get_assets_path())
                        / "default_assets"
                        / "sounds",
                    ),
                ).props("dense outline size=sm")

                # Add alert type specific folder if it exists
                alert_folder_name = dialog_state["alert_type"]
                ui.button(
                    f"{alert_folder_name.title()}",
                    icon="folder_special",
                    on_click=lambda: navigate_to_path(
                        dialog_state,
                        Path(path_utils.get_assets_path()) / alert_folder_name,
                    ),
                ).props("dense outline size=sm")

            # File listing area
            with ui.scroll_area().classes(
                "w-full min-h-0 flex-1 border rounded-lg p-2 bg-theme-base"
            ):
                dialog_state["file_list"] = ui.column().classes("w-full gap-1")

            # Selected file/directory display
            with ui.row().classes("w-full items-center"):
                ui.label("Selected:").classes("text-sm font-medium")
                dialog_state["selected_label"] = ui.label("None").classes(
                    "text-sm secondary-text"
                )

            # Directory mode: Show current directory selection option
            if dialog_state["browse_mode"] == "directory":
                with ui.row().classes(
                    "w-full items-center gap-2 p-2 bg-theme-surface rounded"
                ):
                    ui.icon("folder").classes("text-yellow-400")
                    ui.label("Select current directory").classes("text-sm")
                    select_current_btn = ui.button(
                        "Select This Directory",
                        icon="check_circle",
                        on_click=lambda: select_current_directory(dialog_state, dialog),
                    ).classes("btn-secondary text-sm")

            # Dialog buttons
            with ui.row().classes("w-full justify-end gap-2 mt-4 shrink-0"):
                ui.button("Cancel", on_click=dialog.close).classes(
                    "btn-cancel"
                )

                # Button text depends on browse mode
                button_text = (
                    "Select File"
                    if dialog_state["browse_mode"] == "file"
                    else "Select Directory"
                )
                button_icon = (
                    "description" if dialog_state["browse_mode"] == "file" else "folder"
                )

                select_button = ui.button(
                    button_text,
                    icon=button_icon,
                    on_click=lambda: select_file_from_dialog(dialog_state, dialog),
                ).classes("btn-success")

                # For directory mode, button is always enabled (can select current dir)
                # For file mode, button starts disabled until a file is selected
                select_button.enabled = dialog_state["browse_mode"] == "directory"
                dialog_state["select_button"] = select_button

    # Initial file listing
    update_file_listing(dialog_state)

    dialog.open()


def format_path_for_web_server(path_str: str) -> str:
    """Convert an absolute path to a web server compatible path starting with /assets/

    Args:
        path_str (str): Absolute path to convert

    Returns:
        str: Web server compatible path starting with /assets/ (directories end with /)
    """
    try:
        from pathlib import Path, PurePath

        from .. import path_utils

        # Get the assets directory
        assets_dir = Path(path_utils.get_assets_path()).resolve()

        # Convert input to Path and resolve
        input_path = Path(path_str).resolve()

        # Check if the path is within the assets directory using OS-agnostic comparison
        try:
            # Use relative_to to check if path is within assets_dir - this raises ValueError if not
            relative_path = input_path.relative_to(assets_dir)

            # Format as web path with forward slashes (PurePath handles OS differences)
            web_path = "/assets/" + str(PurePath(relative_path)).replace("\\", "/")

            # Add trailing slash for directories
            if input_path.is_dir() and not web_path.endswith("/"):
                web_path += "/"

            return web_path
        except ValueError:
            # Path is not within assets directory
            logger.warning(f"Path '{path_str}' is not within assets directory")
            return "/assets/"

    except Exception as e:
        logger.error(f"Error formatting path for web server: {str(e)}")
        return "/assets/"


def navigate_to_path(dialog_state, path=None):
    """Navigate to a specific path in the file browser (restricted to assets directory)

    Args:
        dialog_state (dict): Dialog state dictionary
        path (Path, optional): Path to navigate to, or None to use path_input value
    """
    try:
        from pathlib import Path

        from .. import path_utils

        # Get the assets directory for restriction
        assets_dir = Path(path_utils.get_assets_path()).resolve()

        if path is None:
            path_input = dialog_state["path_input"].value
            if path_input:
                path = Path(path_input).expanduser()
            else:
                path = assets_dir

        path = Path(path).expanduser().resolve()

        # Security check: ensure path is within assets directory using OS-agnostic method
        try:
            path.relative_to(assets_dir)
        except ValueError:
            logger.warning(f"Attempt to navigate outside assets directory: {path}")
            path = assets_dir
            ui.notify("Navigation is restricted to the assets folder", type="warning")

        if path.exists():
            path_str = _alert_browser_path_str(path)

            dialog_state["current_path"] = path_str
            dialog_state["path_input"].value = path_str
            dialog_state["selected_file"] = None
            dialog_state["selected_label"].set_text("None")

            # For directory mode, button stays enabled; for file mode, disable until file selected
            browse_mode = dialog_state.get("browse_mode", "file")
            dialog_state["select_button"].enabled = browse_mode == "directory"

            if hasattr(dialog_state["path_input"], "update"):
                dialog_state["path_input"].update()

            update_file_listing(dialog_state)
        else:
            ui.notify(f"Path does not exist: {path}", type="warning")

    except Exception as e:
        logger.error(f"Error navigating to path: {str(e)}")
        ui.notify(f"Error navigating to path: {str(e)}", type="negative")


def select_current_directory(dialog_state, dialog):
    """Select the current directory in directory browse mode

    Args:
        dialog_state (dict): Dialog state dictionary
        dialog: The dialog to close
    """
    try:
        from pathlib import Path

        current_path = Path(dialog_state["current_path"])

        if not current_path.exists() or not current_path.is_dir():
            ui.notify("Current path is not a valid directory", type="warning")
            return

        # Format path for web server (converts to /assets/... format)
        web_server_path = format_path_for_web_server(str(current_path))

        # Update the target UI field (directory only)
        alert_type = dialog_state["alert_type"]
        target_fields = dialog_state["target_fields"]

        if len(target_fields) == 1:  # Directory-only mode
            dir_field = target_fields[0]

            # Get UI elements
            elements = alert_settings_state.get_elements(alert_type)

            if dir_field in elements:
                # Update the directory field with web server compatible path
                elements[dir_field].value = web_server_path

                # Force UI update to ensure cross-platform compatibility
                if hasattr(elements[dir_field], "update"):
                    elements[dir_field].update()

                # Track the change for styling
                track_field_change(
                    dir_field, elements[dir_field], web_server_path, alert_type
                )

                logger.info(f"Selected directory: {web_server_path}")
                ui.notify(f"Selected directory: {current_path.name}", type="positive")
            else:
                logger.error(f"Target UI field not found: {dir_field}")
                ui.notify("Error updating directory field", type="negative")
        else:
            logger.error(f"Invalid target fields for directory mode: {target_fields}")
            ui.notify("Error updating directory field", type="negative")

        dialog.close()

    except Exception as e:
        logger.error(f"Error selecting current directory: {str(e)}", exc_info=True)
        ui.notify("Error selecting directory", type="negative")


def update_file_listing(dialog_state):
    """Update the file listing in the browser

    Args:
        dialog_state (dict): Dialog state dictionary
    """
    try:
        from pathlib import Path

        current_path = Path(dialog_state["current_path"])
        extensions = dialog_state["extensions"]
        browse_mode = dialog_state.get("browse_mode", "file")

        # Clear existing file list
        dialog_state["file_list"].clear()

        # Add parent directory option (if not at root)
        if current_path.parent != current_path:
            with dialog_state["file_list"]:
                with ui.row().classes(
                    "w-full items-center gap-2 p-2 hover-theme-surface rounded cursor-pointer"
                ):
                    ui.icon("arrow_upward").classes("text-blue-400")
                    parent_label = ui.label(".. (Parent Directory)").classes(
                        "text-blue-400"
                    )
                    parent_label.on(
                        "click",
                        lambda: navigate_to_path(dialog_state, current_path.parent),
                    )

        # List directories and files
        try:
            items = sorted(
                current_path.iterdir(), key=lambda x: (x.is_file(), x.name.lower())
            )

            for item in items:
                if item.name.startswith("."):
                    continue  # Skip hidden files

                with dialog_state["file_list"]:
                    with ui.row().classes(
                        "w-full items-center gap-2 p-2 hover-theme-surface rounded cursor-pointer"
                    ) as row:
                        if item.is_dir():
                            ui.icon("folder").classes("text-yellow-400")
                            dir_label = ui.label(item.name).classes("")
                            # Directories are always clickable for navigation
                            row.on(
                                "click",
                                lambda path=item: navigate_to_path(dialog_state, path),
                            )

                            # In directory mode, also allow selecting directories
                            if browse_mode == "directory":
                                row.on(
                                    "click",
                                    lambda path=item: select_directory_in_dialog(
                                        dialog_state, path
                                    ),
                                )
                                dir_label.classes(
                                    "text-green-400"
                                )  # Highlight selectable directories
                        else:
                            # Show different icons for different file types
                            file_extension = item.suffix.lower()
                            if file_extension in extensions:
                                # Determine icon based on file type
                                if file_extension in [
                                    ".mp3",
                                    ".wav",
                                    ".ogg",
                                    ".m4a",
                                    ".flac",
                                ]:
                                    ui.icon("audiotrack").classes("text-green-400")
                                elif file_extension in [
                                    ".gif",
                                    ".png",
                                    ".jpg",
                                    ".jpeg",
                                    ".webp",
                                ]:
                                    ui.icon("image").classes("text-theme-primary")
                                else:
                                    ui.icon("description").classes("text-green-400")
                                file_label = ui.label(item.name).classes(
                                    "text-green-400"
                                )
                            else:
                                ui.icon("description").classes("secondary-text")
                                file_label = ui.label(item.name).classes(
                                    "secondary-text"
                                )

                            # Make files selectable only in file mode and if they match our extensions
                            if browse_mode == "file" and file_extension in extensions:
                                row.on(
                                    "click",
                                    lambda path=item: select_file_in_dialog(
                                        dialog_state, path
                                    ),
                                )

        except PermissionError:
            with dialog_state["file_list"]:
                ui.label("Permission denied - cannot access this directory").classes(
                    "text-red-400 p-2"
                )

    except Exception as e:
        logger.error(f"Error updating file listing: {str(e)}")
        with dialog_state["file_list"]:
            ui.label(f"Error loading directory: {str(e)}").classes("text-red-400 p-2")


def select_directory_in_dialog(dialog_state, dir_path):
    """Select a directory in the dialog (for directory mode)

    Args:
        dialog_state (dict): Dialog state dictionary
        dir_path (Path): Path to the selected directory
    """
    try:
        from pathlib import Path

        dialog_state["selected_file"] = _alert_browser_path_str(
            Path(dir_path).expanduser().resolve()
        )
        dialog_state["selected_label"].set_text(dir_path.name)
        dialog_state["select_button"].enabled = True

        dialog_state["path_input"].value = _alert_browser_path_str(dir_path)
        if hasattr(dialog_state["path_input"], "update"):
            dialog_state["path_input"].update()

    except Exception as e:
        logger.error(f"Error selecting directory: {str(e)}")


def select_file_in_dialog(dialog_state, file_path):
    """Select a file in the dialog

    Args:
        dialog_state (dict): Dialog state dictionary
        file_path (Path): Path to the selected file
    """
    try:
        from pathlib import Path

        resolved = Path(file_path).expanduser().resolve()
        dialog_state["selected_file"] = _alert_browser_path_str(resolved)
        dialog_state["selected_label"].set_text(file_path.name)
        dialog_state["select_button"].enabled = True

        dialog_state["path_input"].value = _alert_browser_path_str(resolved)
        if hasattr(dialog_state["path_input"], "update"):
            dialog_state["path_input"].update()

    except Exception as e:
        logger.error(f"Error selecting file: {str(e)}")


def _resolve_alert_browser_selection(dialog_state):
    """Prefer list selection, then manual path input, then directory-mode current folder."""
    from pathlib import Path

    browse_mode = dialog_state.get("browse_mode", "file")
    path_in = dialog_state.get("path_input")

    def _coerce(p_src, mode):
        if not p_src:
            return None
        p = Path(str(p_src).strip()).expanduser()
        try:
            p = p.resolve()
        except OSError:
            return None
        if not p.exists():
            return None
        if mode == "file" and not p.is_file():
            return None
        if mode == "directory" and not p.is_dir():
            return None
        return p

    sel = dialog_state.get("selected_file")
    if sel:
        hit = _coerce(sel, browse_mode)
        if hit is not None:
            return hit

    manual = path_in.value if path_in else None
    if manual:
        hit = _coerce(manual, browse_mode)
        if hit is not None:
            return hit

    if browse_mode == "directory":
        return _coerce(dialog_state.get("current_path"), "directory")

    return None


def select_file_from_dialog(dialog_state, dialog):
    """Handle the final file/directory selection from the dialog

    Args:
        dialog_state (dict): Dialog state dictionary
        dialog: The dialog to close
    """
    try:
        from pathlib import Path

        browse_mode = dialog_state.get("browse_mode", "file")

        selected_path_obj = _resolve_alert_browser_selection(dialog_state)
        if not selected_path_obj:
            selection_type = "directory" if browse_mode == "directory" else "file"
            ui.notify(f"Please select a {selection_type}", type="warning")
            return

        if browse_mode == "file":
            # Validate file extension
            file_extension = selected_path_obj.suffix.lower()
            if file_extension not in dialog_state["extensions"]:
                extensions_str = ", ".join(dialog_state["extensions"])
                ui.notify(
                    f"Please select a file with one of these extensions: {extensions_str}",
                    type="warning",
                )
                return

        # Update the target UI fields
        alert_type = dialog_state["alert_type"]
        target_fields = dialog_state["target_fields"]

        # Get UI elements
        elements = alert_settings_state.get_elements(alert_type)

        if browse_mode == "file" and len(target_fields) == 2:
            # File mode with both directory and file fields
            dir_field, file_field = target_fields

            if dir_field in elements and file_field in elements:
                # Split path into directory and filename
                file_path = Path(selected_path_obj)
                filename = file_path.name

                # Format directory path for web server
                directory_web_path = format_path_for_web_server(str(file_path.parent))
                if not _path_under_assets(file_path):
                    ui.notify(
                        "This file is outside the assets folder. Move it under assets for correct browser-source paths.",
                        type="warning",
                    )

                # Update the UI elements
                elements[dir_field].value = directory_web_path
                elements[file_field].value = filename

                # Force UI updates to ensure cross-platform compatibility
                if hasattr(elements[dir_field], "update"):
                    elements[dir_field].update()
                if hasattr(elements[file_field], "update"):
                    elements[file_field].update()

                # Track the changes for styling
                track_field_change(
                    dir_field, elements[dir_field], directory_web_path, alert_type
                )
                track_field_change(
                    file_field, elements[file_field], filename, alert_type
                )

                logger.info(f"Selected file: {directory_web_path}/{filename}")
                ui.notify(f"Selected file: {filename}", type="positive")
            else:
                logger.error(f"Target UI fields not found: {target_fields}")
                ui.notify("Error updating file fields", type="negative")

        elif browse_mode == "directory" and len(target_fields) == 1:
            # Directory mode with only directory field
            dir_field = target_fields[0]

            if dir_field in elements:
                # Format directory path for web server
                directory_web_path = format_path_for_web_server(str(selected_path_obj))
                if not _path_under_assets(selected_path_obj):
                    ui.notify(
                        "This folder is outside the assets folder. Move or copy it under assets for browser-source paths.",
                        type="warning",
                    )

                # Update the directory field
                elements[dir_field].value = directory_web_path

                # Force UI update to ensure cross-platform compatibility
                if hasattr(elements[dir_field], "update"):
                    elements[dir_field].update()

                # Track the change for styling
                track_field_change(
                    dir_field, elements[dir_field], directory_web_path, alert_type
                )

                logger.info(f"Selected directory: {directory_web_path}")
                ui.notify(
                    f"Selected directory: {selected_path_obj.name}", type="positive"
                )
            else:
                logger.error(f"Target UI field not found: {dir_field}")
                ui.notify("Error updating directory field", type="negative")
        else:
            logger.error(
                f"Invalid field configuration for {browse_mode} mode: {target_fields}"
            )
            ui.notify("Error updating fields", type="negative")

        dialog.close()

    except Exception as e:
        logger.error(f"Error selecting from dialog: {str(e)}", exc_info=True)
        ui.notify("Error processing selection", type="negative")


def test_alert(alert_type: str):
    """Test the current alert settings

    Args:
        alert_type (str): The type of alert being tested
    """
    try:
        # Check if UI elements are properly initialized
        elements = alert_settings_state.get_elements(alert_type)
        if not elements:
            ui.notify(
                f"UI elements not initialized for {alert_type} tab", type="warning"
            )
            return

        # Verify required elements exist
        required_elements = [
            "duration_input",
            "stackable_switch",
            "fade_in_input",
            "fade_out_input",
            "volume_input",
            "gif_dir_input",
            "gif_file_input",
            "primary_dir_input",
            "primary_file_input",
            "randomized_switch",
            "random_dir_input",
            "random_chance_input",
            "randomized_extra_switch",
            "extra_dir_input",
            "extra_chance_input",
        ]

        missing_elements = [
            elem
            for elem in required_elements
            if elem not in elements or elements[elem] is None
        ]
        if missing_elements:
            ui.notify(f"Missing UI elements: {missing_elements}", type="warning")
            return

        # Create test alert data with safe value extraction
        alert_data = {
            "alert_type": alert_type,
            "duration": float(elements.get("duration_input", {}).value or 3.0),
            "stackable": bool(elements.get("stackable_switch", {}).value or False),
            "fade_in": int(elements.get("fade_in_input", {}).value or 0),
            "fade_out": int(elements.get("fade_out_input", {}).value or 0),
            "volume": int(elements.get("volume_input", {}).value or 100),
            # Only get audio_only value if the switch exists (only available in points alerts)
            "audio_only": bool(elements.get("audio_only_switch", {}).value)
            if "audio_only_switch" in elements and elements["audio_only_switch"]
            else False,
            "single_audio_dir": str(elements.get("primary_dir_input", {}).value or ""),
            "single_audio_name": str(
                elements.get("primary_file_input", {}).value or ""
            ),
            "randomized": bool(elements.get("randomized_switch", {}).value or False),
            "randomized_dir": str(elements.get("random_dir_input", {}).value or ""),
            "randomized_chance": int(
                elements.get("random_chance_input", {}).value or 0
            ),
            "randomized_extra": bool(
                elements.get("randomized_extra_switch", {}).value or False
            ),
            "randomized_extra_dir": str(
                elements.get("extra_dir_input", {}).value or ""
            ),
            "randomized_extra_chance": int(
                elements.get("extra_chance_input", {}).value or 0
            ),
            "gif_dir": str(elements.get("gif_dir_input", {}).value or ""),
            "gif_name": str(elements.get("gif_file_input", {}).value or ""),
            "tts_enabled": bool(elements.get("tts_enabled_switch", {}).value or False),
            "tts_source": str(
                elements.get("tts_source_select", {}).value or "alert_message"
            ),
            "tts_custom_message": str(
                elements.get("tts_custom_message_input", {}).value or ""
            ),
            "alert_id": f"TestAlert{round(time.time())}",
            "timestamp": time.time(),
            "alert_name": "Test Alert",
        }

        # Add specific data based on alert type with safe handling
        if alert_type == "bits":
            # Handle range toggle safely
            use_range = (
                bool(elements.get("range_toggle", {}).value)
                if "range_toggle" in elements and elements["range_toggle"]
                else False
            )
            if use_range and "min_input" in elements and elements["min_input"]:
                alert_data["amt_cheered"] = int(elements["min_input"].value or 100)
            elif "amount_input" in elements and elements["amount_input"]:
                alert_data["amt_cheered"] = int(elements["amount_input"].value or 100)
            else:
                alert_data["amt_cheered"] = 100  # Default value
            alert_data["username"] = "TestUser"
        elif alert_type == "subs":
            use_range = (
                bool(elements.get("range_toggle", {}).value)
                if "range_toggle" in elements and elements["range_toggle"]
                else False
            )
            if use_range and "min_input" in elements and elements["min_input"]:
                alert_data["resub_month"] = int(elements["min_input"].value or 1)
            elif "amount_input" in elements and elements["amount_input"]:
                alert_data["resub_month"] = int(elements["amount_input"].value or 1)
            else:
                alert_data["resub_month"] = 1  # Default value
            alert_data["username"] = "TestUser"
            alert_data["tier"] = 1
        elif alert_type == "giftsubs":
            use_range = (
                bool(elements.get("range_toggle", {}).value)
                if "range_toggle" in elements and elements["range_toggle"]
                else False
            )
            if use_range and "min_input" in elements and elements["min_input"]:
                alert_data["gift_qty"] = int(elements["min_input"].value or 1)
            elif "amount_input" in elements and elements["amount_input"]:
                alert_data["gift_qty"] = int(elements["amount_input"].value or 1)
            else:
                alert_data["gift_qty"] = 1  # Default value
            alert_data["username"] = "TestUser"
            alert_data["tier"] = 1
        elif alert_type == "donations":
            use_range = (
                bool(elements.get("range_toggle", {}).value)
                if "range_toggle" in elements and elements["range_toggle"]
                else False
            )
            if use_range and "min_input" in elements and elements["min_input"]:
                alert_data["donation_amount"] = float(
                    elements["min_input"].value or 5.0
                )
            elif "amount_input" in elements and elements["amount_input"]:
                alert_data["donation_amount"] = float(
                    elements["amount_input"].value or 5.0
                )
            else:
                alert_data["donation_amount"] = 5.0  # Default value
            alert_data["username"] = "TestUser"
        elif alert_type == "raids":
            use_range = (
                bool(elements.get("range_toggle", {}).value)
                if "range_toggle" in elements and elements["range_toggle"]
                else False
            )
            if use_range and "min_input" in elements and elements["min_input"]:
                alert_data["raider_count"] = int(elements["min_input"].value or 10)
            elif "amount_input" in elements and elements["amount_input"]:
                alert_data["raider_count"] = int(elements["amount_input"].value or 10)
            else:
                alert_data["raider_count"] = 10  # Default value
            alert_data["username"] = "TestUser"
        elif alert_type == "follows":
            alert_data["username"] = "TestUser"
        elif alert_type == "points":
            alert_data["username"] = "TestUser"
            alert_data["twitch_reward_id"] = "test_reward"
            alert_data["point_cost"] = 1000
            alert_data["title"] = "Test Point Redemption"

        # Create AlertObj and add to the correct queue
        alert = alertutils.AlertObj(**alert_data)
        alert.is_test = True  # Mark as test alert
        alert_processor.ALERT_QUEUE.append(alert)

        logger.debug(f"Testing {alert_type} alert with data: {alert_data}")
        ui.notify(f"Testing {alert_type} alert...", type="info")
    except Exception as e:
        logger.error(f"Error testing alert: {str(e)}", exc_info=True)
        ui.notify("Error testing alert", type="negative")


def save_alert(alert_type: str):
    """Save the current alert settings using the AlertStateManager

    Args:
        alert_type (str): The type of alert being saved
    """
    try:
        # Get the current input values with proper handling for empty strings
        duration = alert_settings_state.get_elements(alert_type)["duration_input"].value
        stackable = alert_settings_state.get_elements(alert_type)[
            "stackable_switch"
        ].value
        fade_in = alert_settings_state.get_elements(alert_type)["fade_in_input"].value
        fade_out = alert_settings_state.get_elements(alert_type)["fade_out_input"].value
        volume = alert_settings_state.get_elements(alert_type)["volume_input"].value
        tts_enabled = alert_settings_state.get_elements(alert_type)[
            "tts_enabled_switch"
        ].value
        tts_source = (
            alert_settings_state.get_elements(alert_type)["tts_source_select"].value
            or "alert_message"
        )
        tts_custom_message = (
            alert_settings_state.get_elements(alert_type)["tts_custom_message_input"].value
            or ""
        )
        # Only get audio_only value if the switch exists (only available in points alerts)
        audio_only = (
            alert_settings_state.get_elements(alert_type)["audio_only_switch"].value
            if "audio_only_switch" in alert_settings_state.get_elements(alert_type)
            else False
        )

        # Get path values, ensuring empty strings are properly handled
        primary_dir = (
            alert_settings_state.get_elements(alert_type)["primary_dir_input"].value
            or ""
        )
        primary_file = (
            alert_settings_state.get_elements(alert_type)["primary_file_input"].value
            or ""
        )
        randomized = alert_settings_state.get_elements(alert_type)[
            "randomized_switch"
        ].value
        random_dir = (
            alert_settings_state.get_elements(alert_type)["random_dir_input"].value
            or ""
        )
        random_chance = alert_settings_state.get_elements(alert_type)[
            "random_chance_input"
        ].value
        randomized_extra = alert_settings_state.get_elements(alert_type)[
            "randomized_extra_switch"
        ].value
        extra_dir = (
            alert_settings_state.get_elements(alert_type)["extra_dir_input"].value or ""
        )
        extra_chance = alert_settings_state.get_elements(alert_type)[
            "extra_chance_input"
        ].value
        gif_dir = (
            alert_settings_state.get_elements(alert_type)["gif_dir_input"].value or ""
        )
        gif_file = (
            alert_settings_state.get_elements(alert_type)["gif_file_input"].value or ""
        )

        # Log the values we're about to save
        logger.debug(
            f"Saving alert with paths - Primary: {primary_dir}/{primary_file}, Random: {random_dir}, Extra: {extra_dir}, GIF: {gif_dir}/{gif_file}"
        )

        # Create alert data
        alert_data = {
            "alert_type": alert_type,
            "duration": duration,
            "stackable": stackable,
            "fade_in": fade_in,
            "fade_out": fade_out,
            "volume": volume,
            "tts_enabled": tts_enabled,
            "tts_source": tts_source,
            "tts_custom_message": tts_custom_message,
            "audio_only": audio_only,
            "single_audio_dir": primary_dir,
            "single_audio_name": primary_file,
            "randomized": randomized,
            "randomized_dir": random_dir,
            "randomized_chance": random_chance,
            "randomized_extra": randomized_extra,
            "randomized_extra_dir": extra_dir,
            "randomized_extra_chance": extra_chance,
            "gif_dir": gif_dir,
            "gif_name": gif_file,
            "alert_name": alert_settings_state.get_elements(alert_type)[
                "alert_select"
            ].value
            if alert_settings_state.get_elements(alert_type)["alert_select"].value
            != "new"
            else f"New {alert_type.title()} Alert",
            "timestamp": time.time(),
        }

        # Generate alert ID based on type and amount/range
        selected_value = alert_settings_state.get_elements(alert_type)[
            "alert_select"
        ].value

        # Special handling for resub fallback alert
        if (
            alert_type == "subs"
            and selected_value == alertutils.AlertSettings.FALLBACK_ALERT_ID
        ):
            alert_id = alertutils.AlertSettings.FALLBACK_ALERT_ID
            alert_data["alert_name"] = "Resub Fallback"
            alert_data["resub_month"] = 0

        elif alert_type in ["bits", "subs", "giftsubs", "donations", "raids"]:
            if alert_settings_state.get_elements(alert_type)["range_toggle"].value:
                # Format as <alert_type><min-max>
                min_val = int(
                    alert_settings_state.get_elements(alert_type)["min_input"].value
                )
                max_val = int(
                    alert_settings_state.get_elements(alert_type)["max_input"].value
                )

                # Store numeric data for the specific alert type
                if alert_type == "bits":
                    alert_data["amt_cheered"] = min_val
                elif alert_type == "subs":
                    alert_data["resub_month"] = min_val
                elif alert_type == "giftsubs":
                    alert_data["gift_qty"] = min_val
                elif alert_type == "donations":
                    alert_data["donation_amount"] = min_val
                elif alert_type == "raids":
                    alert_data["raider_count"] = min_val

                alert_id = f"{alert_type}{min_val}-{max_val}"

                # Generate proper display name using the centralized method
                if (
                    alert_settings_state.get_elements(alert_type)["alert_select"].value
                    == "new"
                ):
                    alert_data["alert_name"] = (
                        alertutils.alert_state_manager.get_display_name(
                            alert_type, alert_id
                        )
                    )
            else:
                # Format as <alert_type><quantity>
                quantity = int(
                    alert_settings_state.get_elements(alert_type)["amount_input"].value
                )

                # Store numeric data for the specific alert type
                if alert_type == "bits":
                    alert_data["amt_cheered"] = quantity
                elif alert_type == "subs":
                    alert_data["resub_month"] = quantity
                elif alert_type == "giftsubs":
                    alert_data["gift_qty"] = quantity
                elif alert_type == "donations":
                    alert_data["donation_amount"] = quantity
                elif alert_type == "raids":
                    alert_data["raider_count"] = quantity

                alert_id = f"{alert_type}{quantity}"

                # Generate proper display name using the centralized method
                if (
                    alert_settings_state.get_elements(alert_type)["alert_select"].value
                    == "new"
                ):
                    alert_data["alert_name"] = (
                        alertutils.alert_state_manager.get_display_name(
                            alert_type, alert_id
                        )
                    )
        else:
            # For non-quantity alerts, use alert_type1 format
            alert_id = f"{alert_type}1"

            # Generate proper display name using the centralized method
            if (
                alert_settings_state.get_elements(alert_type)["alert_select"].value
                == "new"
            ):
                alert_data["alert_name"] = (
                    alertutils.alert_state_manager.get_display_name(
                        alert_type, alert_id
                    )
                )

            # Add specific data for follows and points
            if alert_type == "follows":
                # No specific data needed for follows
                pass
            elif alert_type == "points":
                # Point-specific data
                alert_data["twitch_reward_id"] = (
                    ""  # Will be set when a point redemption is bound
                )
                alert_data["point_cost"] = 0  # Set when point reward is configured

        # Note: randomized and randomized_extra are already set from the switches above

        # Log the alert ID and data being saved
        logger.debug(f"Saving alert with ID: {alert_id} for type: {alert_type}")
        logger.debug(f"Alert data: {alert_data}")

        # Save alert using AlertStateManager
        success = alertutils.alert_state_manager.save_alert(
            alert_type, alert_id, alert_data
        )

        if success:
            ui.notify(
                f'Saved {alert_type} alert: {alert_data["alert_name"]}', type="positive"
            )

            # Ensure we update the dropdown to show all alerts including the new one
            try:
                # If we just created a new alert, update the dropdown and select it
                if (
                    alert_settings_state.get_elements(alert_type)["alert_select"].value
                    == "new"
                    or selected_value == alertutils.AlertSettings.FALLBACK_ALERT_ID
                ):
                    # Force a refresh of the alert list
                    alertutils.alert_state_manager.reload_from_firebase()

                    # Get alerts for this type (fresh from Firebase)
                    alerts = get_alerts_for_type(alert_type)
                    if alerts or alert_type == "subs":
                        # Create updated options
                        alert_options = {
                            "new": "+ Create New Alert",
                        }
                        # Sort alerts and add to options
                        sorted_alerts = sort_alert_ids(
                            alert_type, list((alerts or {}).items())
                        )
                        for a_id, a_data in sorted_alerts:
                            # Use the centralized display name method from AlertStateManager
                            display_name = (
                                alertutils.alert_state_manager.get_display_name(
                                    alert_type, a_id, a_data
                                )
                            )
                            alert_options[a_id] = display_name

                        # Ensure fallback entry is always present for subs
                        if alert_type == "subs":
                            fallback_id = alertutils.AlertSettings.FALLBACK_ALERT_ID
                            if fallback_id not in alert_options:
                                alert_options[fallback_id] = "Resub Fallback"

                        # Update the select options
                        alert_settings_state.get_elements(alert_type)[
                            "alert_select"
                        ].options = alert_options
                        # Select the newly created alert
                        alert_settings_state.get_elements(alert_type)[
                            "alert_select"
                        ].value = alert_id

                        # Log that we updated the dropdown
                        logger.debug(
                            f"Updated alert selector with options: {alert_options}"
                        )
                        logger.debug(f"Selected alert: {alert_id}")
            except Exception as dropdown_err:
                logger.error(
                    f"Error updating alert dropdown: {str(dropdown_err)}", exc_info=True
                )
                # This shouldn't prevent the alert from being saved, so we continue

            # Store the new values as original values
            store_original_values(alert_type)
            # Clear changed styling
            clear_changed_styling(alert_type)
        else:
            ui.notify("Error saving alert settings", type="negative")

    except Exception as e:
        logger.error(f"Error saving alert: {str(e)}", exc_info=True)
        ui.notify("Error saving alert settings", type="negative")


# Add the following functions for change detection and styling


def track_field_change(field_name, element, new_value, tab_type=None):
    """Track changes to a field and update styling accordingly

    Args:
        field_name (str): The name of the field to track
        element (ui.element): The UI element to update styling for
        new_value (Any): The new value to compare against the original
        tab_type (str): The tab type to use (fallback if current_tab is not set)
    """
    try:
        # Get the current tab (use parameter as fallback)
        current_tab = alert_settings_state.current_tab or tab_type
        if not current_tab:
            logger.error(
                "No current tab set for field change tracking and no fallback provided"
            )
            return

        # Get original value for this tab
        original_values = alert_settings_state.get_original_values(current_tab)
        original_value = original_values.get(field_name, None)

        # Store the UI element reference
        alert_settings_state.get_elements(current_tab)[field_name] = element

        # Handle null values in text fields
        if new_value is None and element.__class__.__name__ in ["Input", "TextField"]:
            new_value = ""

        # Check if the value has changed from the original
        if original_value != new_value:
            logger.debug(
                f"Change detected in {field_name}: {original_value} → {new_value}"
            )
        else:
            logger.debug(f"No change in {field_name}")
    except Exception as e:
        logger.error(f"Error tracking field change: {str(e)}", exc_info=True)


def update_all_fields_styling(tab_type: str):
    """Update styling for all tracked fields"""
    logger.debug("Updating styling for all fields")

    # Loop through all tracked elements
    for field_name, element in alert_settings_state.get_elements(tab_type).items():
        if not element:
            continue

        # Get the current value
        current_value = None

        if hasattr(element, "value"):
            current_value = element.value

        # Get original value for this tab
        original_values = alert_settings_state.get_original_values(tab_type)
        original_value = original_values.get(field_name, None)

        # Check if this is a switch element
        is_switch = element.__class__.__name__ == "Switch"

        # Check if the value has changed from the original
        if original_value != current_value:
            logger.debug(
                f"Styling update: Change detected in {field_name}: {original_value} → {current_value}"
            )
        else:
            logger.debug(f"Styling update: No change in {field_name}")


def clear_changed_styling(tab_type: str):
    """Clear all changed styling"""
    logger.debug("Clearing all changed styling")

    # Loop through all tracked elements and remove styling
    for field_name, element in alert_settings_state.get_elements(tab_type).items():
        if not element:
            continue


def store_original_values(tab_type: str):
    """Store the original values of all fields for change detection"""
    try:
        logger.debug("Storing original values for all alert fields")

        # Get the original values dictionary for this tab
        original_values = alert_settings_state.get_original_values(tab_type)

        # Loop through all UI elements that have been created and store their values
        for field_name, element in alert_settings_state.get_elements(tab_type).items():
            if not element:
                continue

            if hasattr(element, "value"):
                # Convert None to empty string for text inputs
                if element.value is None and element.__class__.__name__ in [
                    "Input",
                    "TextField",
                ]:
                    original_values[field_name] = ""
                else:
                    original_values[field_name] = element.value

        # Log stored values for debugging
        logger.debug(f"Stored original values: {original_values}")
    except Exception as e:
        logger.error(f"Error storing original values: {str(e)}", exc_info=True)


# Add this new function to handle initial values
def initialize_alert_values(tab_type):
    """Initialize alert values either by loading default or storing current values"""
    try:
        # Store the current values as originals regardless of selection state
        store_original_values(tab_type)
        logger.debug(f"Stored original values for alert type: {tab_type}")
        logger.debug(
            f"Original values: {alert_settings_state.get_original_values(tab_type)}"
        )
    except Exception as e:
        logger.error(f"Error initializing alert values: {str(e)}", exc_info=True)


def refresh_ui_from_current_values(tab_type: str):
    """Force refresh UI components to display current values in alert_settings_state"""
    try:
        logger.debug("Refreshing UI components with current values")

        # For each UI element with a value, force an update
        for field_name, element in alert_settings_state.get_elements(tab_type).items():
            if not element:
                continue

            if hasattr(element, "value"):
                current_value = getattr(element, "value")
                logger.debug(f"Field {field_name} has value: {current_value}")

                # Force a UI refresh by setting the value again
                if hasattr(element, "update"):
                    try:
                        element.update()
                        logger.debug(f"Updated element {field_name}")
                    except Exception as update_err:
                        logger.error(
                            f"Error updating element {field_name}: {str(update_err)}"
                        )
    except Exception as e:
        logger.error(f"Error refreshing UI: {str(e)}", exc_info=True)


def create_points_alert_panel():
    """Create a specialized panel for points alerts with Twitch integration"""
    alert_type = "points"

    # Set the current tab in the state manager
    alert_settings_state.set_current_tab(alert_type)

    with ui.scroll_area().classes("w-full h-full"):
        with ui.column().classes("w-full gap-4 p-4"):
            # Special alert selector for Twitch point rewards
            with ui.row().classes("w-full items-center gap-2 mb-4"):
                with ui.row().classes("items-center"):
                    alert_settings_state.get_elements(alert_type)["alert_select"] = (
                        ui.select(
                            {"loading": "Loading Twitch rewards..."},
                            label="Select Point Reward",
                            on_change=lambda e: handle_point_reward_selection(
                                e, alert_type
                            ),
                        ).classes("w-64 bg-theme-base rounded-md")
                    )
                    ui.tooltip(
                        "Choose a Twitch point reward to configure as an alert"
                    ).classes("bg-theme-surface")

                with ui.row().classes("items-center gap-2"):
                    refresh_btn = ui.button(
                        "Refresh Rewards",
                        icon="refresh",
                        on_click=lambda: load_twitch_point_rewards(),
                    ).classes(
                        "btn-secondary transition-colors duration-200 text-sm"
                    )
                    ui.tooltip("Refresh the list of Twitch point rewards").classes(
                        "bg-theme-surface"
                    )

            channel_points_banner = ui.card().classes(
                "w-full p-3 rounded-lg"
            ).style(
                "background-color: var(--color-bg-warning-muted, rgba(234, 179, 8, 0.15)); "
                "border: 1px solid var(--color-border-default);"
            )
            with channel_points_banner:
                ui.label(
                    "Custom Channel Point rewards require Twitch Affiliate or Partner. "
                    "This account does not have Channel Points unlocked yet."
                ).classes("text-sm")
            channel_points_banner.visible = False
            alert_settings_state.get_elements(alert_type)[
                "channel_points_locked_banner"
            ] = channel_points_banner

            # Settings sections in a scrollable container
            with ui.element("div").classes("content-card"):
                # Create a layout similar to other alert types but with Twitch integration
                with ui.grid(columns=2).classes("w-full gap-4"):
                    # Left column - General and Audio settings
                    with ui.column().classes("w-full gap-2"):
                        # General Settings
                        with ui.expansion("General", icon="settings").classes(
                            "w-full bg-theme-base rounded-lg overflow-hidden"
                        ):
                            with ui.grid(columns=2).classes("w-full gap-2 p-2"):
                                # Enable as alert toggle
                                with ui.row().classes("w-full items-center col-span-2"):
                                    alert_settings_state.get_elements(alert_type)[
                                        "enable_alert_switch"
                                    ] = ui.switch("Enable as Alert").classes(
                                        "w-full q-switch"
                                    )
                                    alert_settings_state.get_elements(alert_type)[
                                        "enable_alert_switch"
                                    ].on(
                                        "change",
                                        lambda e, at=alert_type: track_field_change(
                                            "enable_alert_switch",
                                            alert_settings_state.get_elements(at)[
                                                "enable_alert_switch"
                                            ],
                                            e,
                                            at,
                                        ),
                                    )
                                    ui.tooltip(
                                        "Enable this point reward as an alert in the app"
                                    ).classes("bg-theme-surface")

                                with ui.row().classes("w-full items-center"):
                                    alert_settings_state.get_elements(alert_type)[
                                        "duration_input"
                                    ] = ui.number(
                                        "Duration", value=3.0, min=0.1, step=0.1
                                    ).classes("w-full bg-theme-base rounded-md")
                                    alert_settings_state.get_elements(alert_type)[
                                        "duration_input"
                                    ].on(
                                        "change",
                                        lambda e, at=alert_type: track_field_change(
                                            "duration_input",
                                            alert_settings_state.get_elements(at)[
                                                "duration_input"
                                            ],
                                            e,
                                            at,
                                        ),
                                    )
                                    ui.tooltip(
                                        "How long the alert will display (seconds)"
                                    ).classes("bg-theme-surface")

                                with ui.row().classes("w-full items-center"):
                                    alert_settings_state.get_elements(alert_type)[
                                        "stackable_switch"
                                    ] = ui.switch("Stackable").classes(
                                        "w-full q-switch"
                                    )
                                    alert_settings_state.get_elements(alert_type)[
                                        "stackable_switch"
                                    ].on(
                                        "change",
                                        lambda e, at=alert_type: track_field_change(
                                            "stackable_switch",
                                            alert_settings_state.get_elements(at)[
                                                "stackable_switch"
                                            ],
                                            e,
                                            at,
                                        ),
                                    )
                                    ui.tooltip(
                                        "Allow multiple alerts of this type to stack"
                                    ).classes("bg-theme-surface")

                        # Audio Settings (same as other alert types)
                        create_audio_settings_section(alert_type)
                        create_tts_settings_section(alert_type)

                    # Right column - Visual and Twitch settings
                    with ui.column().classes("w-full gap-2"):
                        # Visual Settings (same as other alert types)
                        create_visual_settings_section(alert_type)

                        # Randomizer Settings
                        create_randomizer_settings_section(alert_type)

                        # Twitch Options (unique to points)
                        create_twitch_options_section(alert_type)

            # Save and Test buttons at the bottom
            with ui.row().classes("w-full justify-end mt-2 gap-2"):
                with ui.row().classes("items-center"):
                    test_btn = ui.button(
                        "Test Alert",
                        icon="play_arrow",
                        on_click=lambda: test_alert(alert_type),
                    ).classes(
                        "btn-secondary transition-colors duration-200 text-sm"
                    )
                    ui.tooltip("Test the current alert settings").classes(
                        "bg-theme-surface"
                    )

                with ui.row().classes("items-center"):
                    # Delete button - only show for existing alerts
                    alert_settings_state.get_elements(alert_type)["delete_btn"] = (
                        ui.button(
                            "Delete Alert",
                            icon="delete",
                            on_click=lambda: show_delete_confirmation(alert_type),
                        ).classes(
                            "alert-delete-btn transition-colors duration-200 text-sm"
                        )
                    )
                    alert_settings_state.get_elements(alert_type)[
                        "delete_btn"
                    ].visible = False  # Hidden by default
                    ui.tooltip("Delete this alert permanently").classes(
                        "bg-theme-surface"
                    )

                with ui.row().classes("items-center"):
                    save_btn = ui.button(
                        "Save Alert", icon="save", on_click=lambda: save_point_alert()
                    ).classes("alert-save-btn transition-colors duration-200 text-sm")
                    alert_settings_state.get_elements(alert_type)["save_btn"] = save_btn
                    ui.tooltip("Save your alert settings").classes(
                        "bg-theme-surface"
                    )

            # Auto-load Twitch rewards when this panel is created
            def auto_load_rewards():
                try:
                    logger.debug("Auto-loading Twitch rewards for points panel")
                    # Check if we're currently on the points tab before loading
                    if alert_settings_state.current_tab == "points":
                        load_twitch_point_rewards()
                    else:
                        logger.debug("Not on points tab, skipping auto-load")
                except Exception as auto_load_err:
                    logger.error(f"Error in auto-load rewards: {str(auto_load_err)}")

            # Schedule auto-load with a delay to ensure UI is fully rendered
            ui.timer(1.0, auto_load_rewards, once=True)


def create_audio_settings_section(alert_type: str):
    """Create the audio settings section for alerts"""
    with ui.expansion("Audio", icon="volume_up").classes(
        "w-full bg-theme-base rounded-lg overflow-hidden"
    ).style("border: 1px solid var(--color-border-default);"):
        with ui.column().classes("w-full gap-2 p-2"):
            # Fade In, Fade Out, and Volume all on the same card
            with ui.card().classes("w-full p-3 rounded-lg").style("background-color: var(--color-bg-surface);"):
                ui.label("Audio Controls").classes("font-medium mb-3 text-sm")
                with ui.row().classes("w-full items-center gap-4"):
                    # Fade In
                    alert_settings_state.get_elements(alert_type)["fade_in_input"] = (
                        ui.number(
                            "Fade In", value=0, min=0
                        ).classes("w-24 bg-theme-base rounded-md")
                    )
                    alert_settings_state.get_elements(alert_type)["fade_in_input"].on(
                        "change",
                        lambda e, at=alert_type: track_field_change(
                            "fade_in_input",
                            alert_settings_state.get_elements(at)["fade_in_input"],
                            e,
                            at,
                        ),
                    )
                    ui.tooltip("Time in milliseconds for the audio to fade in").classes(
                        "bg-theme-surface"
                    )

                    # Fade Out
                    alert_settings_state.get_elements(alert_type)["fade_out_input"] = (
                        ui.number(
                            "Fade Out", value=0, min=0
                        ).classes("w-24 bg-theme-base rounded-md")
                    )
                    alert_settings_state.get_elements(alert_type)["fade_out_input"].on(
                        "change",
                        lambda e, at=alert_type: track_field_change(
                            "fade_out_input",
                            alert_settings_state.get_elements(at)["fade_out_input"],
                            e,
                            at,
                        ),
                    )
                    ui.tooltip("Time in milliseconds for the audio to fade out").classes(
                        "bg-theme-surface"
                    )

                    # Volume slider with label
                    ui.label("Volume").classes("text-sm font-medium")
                    alert_settings_state.get_elements(alert_type)["volume_input"] = (
                        ui.slider(min=0, max=100, value=100).classes("flex-1")
                    )
                    alert_settings_state.get_elements(alert_type)["volume_value_label"] = (
                        ui.label(
                            "100%"
                        ).classes("text-sm font-medium min-w-[40px] text-right")
                    )

                    # Create a combined change handler for both tracking and label update
                    def handle_volume_change(e, at=alert_type):
                        track_field_change(
                            "volume_input",
                            alert_settings_state.get_elements(at)["volume_input"],
                            e,
                            at,
                        )
                        # Update the value label - get value directly from the slider element
                        slider_element = alert_settings_state.get_elements(at)[
                            "volume_input"
                        ]
                        value = (
                            int(slider_element.value)
                            if slider_element.value is not None
                            else 100
                        )
                        alert_settings_state.get_elements(at)[
                            "volume_value_label"
                        ].set_text(f"{value}%")

                    alert_settings_state.get_elements(alert_type)["volume_input"].on(
                        "change", handle_volume_change
                    )
                    ui.tooltip("Volume level for the alert sound (0-100%)").classes(
                        "bg-theme-surface"
                    )

            # Only show audio_only toggle for points alerts
            if alert_type == "points":
                with ui.card().classes("w-full p-3 rounded-lg").style("background-color: var(--color-bg-surface);"):
                    ui.label("Playback Mode").classes("font-medium mb-2 text-sm")
                    alert_settings_state.get_elements(alert_type)[
                        "audio_only_switch"
                    ] = ui.switch("Audio Only").classes("w-full q-switch")
                    alert_settings_state.get_elements(alert_type)[
                        "audio_only_switch"
                    ].on(
                        "change",
                        lambda e, at=alert_type: track_field_change(
                            "audio_only_switch",
                            alert_settings_state.get_elements(at)["audio_only_switch"],
                            e,
                            at,
                        ),
                    )
                    ui.tooltip(
                        "Play only audio without any visual elements (useful for channel points)"
                    ).classes("bg-theme-surface")

            # Primary Sound
            with ui.card().classes("w-full p-2 bg-theme-surface rounded-lg col-span-2"):
                ui.label("Primary Sound").classes("font-medium mb-1 text-sm")
                with ui.row().classes("w-full items-center"):
                    alert_settings_state.get_elements(alert_type)[
                        "primary_dir_input"
                    ] = ui.input("Directory").classes(
                        "w-full mb-1 bg-theme-base rounded-md text-sm"
                    )
                    alert_settings_state.get_elements(alert_type)[
                        "primary_dir_input"
                    ].on(
                        "change",
                        lambda e, at=alert_type: track_field_change(
                            "primary_dir_input",
                            alert_settings_state.get_elements(at)["primary_dir_input"],
                            e,
                            at,
                        ),
                    )
                    ui.tooltip("Directory containing the primary sound file").classes(
                        "bg-theme-surface"
                    )

                with ui.row().classes("w-full items-center"):
                    alert_settings_state.get_elements(alert_type)[
                        "primary_file_input"
                    ] = ui.input("File").classes(
                        "w-full mb-1 bg-theme-base rounded-md text-sm"
                    )
                    alert_settings_state.get_elements(alert_type)[
                        "primary_file_input"
                    ].on(
                        "change",
                        lambda e, at=alert_type: track_field_change(
                            "primary_file_input",
                            alert_settings_state.get_elements(at)["primary_file_input"],
                            e,
                            at,
                        ),
                    )
                    ui.tooltip("Name of the primary sound file").classes(
                        "bg-theme-surface"
                    )

                with ui.row().classes("w-full items-center"):
                    primary_browse_btn = ui.button(
                        "Browse",
                        icon="folder",
                        on_click=lambda: handle_browse("primary"),
                    ).classes(
                        "control-button bg-theme-surface hover-theme-overlay transition-colors duration-200 text-sm"
                    )
                    ui.tooltip("Browse for the primary sound file").classes(
                        "bg-theme-surface"
                    )


def create_randomizer_settings_section(alert_type: str):
    """Create the randomizer settings section for random and extra random sounds"""
    with ui.expansion("Randomizer", icon="shuffle").classes(
        "w-full bg-theme-base rounded-lg overflow-hidden"
    ).style("border: 1px solid var(--color-border-default);"):
        with ui.grid(columns=1).classes("w-full gap-2 p-2"):
            # Random Sound
            with ui.card().classes("w-full p-2 bg-theme-surface rounded-lg"):
                ui.label("Random Sound").classes("font-medium mb-1 text-sm")
                with ui.row().classes("w-full items-center"):
                    alert_settings_state.get_elements(alert_type)[
                        "randomized_switch"
                    ] = ui.switch("Enable Random Sounds").classes("w-full q-switch")
                    alert_settings_state.get_elements(alert_type)[
                        "randomized_switch"
                    ].on(
                        "change",
                        lambda e, at=alert_type: track_field_change(
                            "randomized_switch",
                            alert_settings_state.get_elements(at)["randomized_switch"],
                            e,
                            at,
                        ),
                    )
                    ui.tooltip(
                        "Enable random sound playback instead of primary sound"
                    ).classes("bg-theme-surface")

                with ui.row().classes("w-full items-center"):
                    alert_settings_state.get_elements(alert_type)[
                        "random_dir_input"
                    ] = ui.input("Directory").classes(
                        "w-full mb-1 bg-theme-base rounded-md text-sm"
                    )
                    alert_settings_state.get_elements(alert_type)[
                        "random_dir_input"
                    ].on(
                        "change",
                        lambda e, at=alert_type: track_field_change(
                            "random_dir_input",
                            alert_settings_state.get_elements(at)["random_dir_input"],
                            e,
                            at,
                        ),
                    )
                    ui.tooltip("Directory containing random sound files").classes(
                        "bg-theme-surface"
                    )

                with ui.row().classes("w-full items-center"):
                    alert_settings_state.get_elements(alert_type)[
                        "random_chance_input"
                    ] = ui.number("Chance (%)", value=0, min=0, max=100).classes(
                        "w-full mb-1 bg-theme-base rounded-md text-sm"
                    )
                    alert_settings_state.get_elements(alert_type)[
                        "random_chance_input"
                    ].on(
                        "change",
                        lambda e, at=alert_type: track_field_change(
                            "random_chance_input",
                            alert_settings_state.get_elements(at)[
                                "random_chance_input"
                            ],
                            e,
                            at,
                        ),
                    )
                    ui.tooltip(
                        "Percentage chance to play a random sound instead of the primary sound"
                    ).classes("bg-theme-surface")

                with ui.row().classes("w-full items-center"):
                    random_browse_btn = ui.button(
                        "Browse",
                        icon="folder",
                        on_click=lambda: handle_browse("random"),
                    ).classes(
                        "control-button bg-theme-surface hover-theme-overlay transition-colors duration-200 text-sm"
                    )
                    ui.tooltip(
                        "Browse for directory containing random sound files"
                    ).classes("bg-theme-surface")

            # Extra Random Sound
            with ui.card().classes("w-full p-2 bg-theme-surface rounded-lg"):
                ui.label("Extra Random Sound").classes("font-medium mb-1 text-sm")
                with ui.row().classes("w-full items-center"):
                    alert_settings_state.get_elements(alert_type)[
                        "randomized_extra_switch"
                    ] = ui.switch("Enable Extra Random Sounds").classes(
                        "w-full q-switch"
                    )
                    alert_settings_state.get_elements(alert_type)[
                        "randomized_extra_switch"
                    ].on(
                        "change",
                        lambda e, at=alert_type: track_field_change(
                            "randomized_extra_switch",
                            alert_settings_state.get_elements(at)[
                                "randomized_extra_switch"
                            ],
                            e,
                            at,
                        ),
                    )
                    ui.tooltip(
                        "Enable extra random sounds to play after the main sound"
                    ).classes("bg-theme-surface")

                with ui.row().classes("w-full items-center"):
                    alert_settings_state.get_elements(alert_type)["extra_dir_input"] = (
                        ui.input(
                            "Directory"
                        ).classes("w-full mb-1 bg-theme-base rounded-md text-sm")
                    )
                    alert_settings_state.get_elements(alert_type)["extra_dir_input"].on(
                        "change",
                        lambda e, at=alert_type: track_field_change(
                            "extra_dir_input",
                            alert_settings_state.get_elements(at)["extra_dir_input"],
                            e,
                            at,
                        ),
                    )
                    ui.tooltip("Directory containing extra random sound files").classes(
                        "bg-theme-surface"
                    )

                with ui.row().classes("w-full items-center"):
                    alert_settings_state.get_elements(alert_type)[
                        "extra_chance_input"
                    ] = ui.number("Chance (%)", value=0, min=0, max=100).classes(
                        "w-full mb-1 bg-theme-base rounded-md text-sm"
                    )
                    alert_settings_state.get_elements(alert_type)[
                        "extra_chance_input"
                    ].on(
                        "change",
                        lambda e, at=alert_type: track_field_change(
                            "extra_chance_input",
                            alert_settings_state.get_elements(at)["extra_chance_input"],
                            e,
                            at,
                        ),
                    )
                    ui.tooltip(
                        "Percentage chance to play an additional random sound after the primary/random sound"
                    ).classes("bg-theme-surface")

                with ui.row().classes("w-full items-center"):
                    extra_browse_btn = ui.button(
                        "Browse", icon="folder", on_click=lambda: handle_browse("extra")
                    ).classes(
                        "control-button bg-theme-surface hover-theme-overlay transition-colors duration-200 text-sm"
                    )
                    ui.tooltip(
                        "Browse for directory containing extra sound files"
                    ).classes("bg-theme-surface")


def create_visual_settings_section(alert_type: str):
    """Create the visual settings section for alerts"""
    with ui.expansion("Visual", icon="image").classes(
        "w-full bg-theme-base rounded-lg overflow-hidden"
    ).style("border: 1px solid var(--color-border-default);"):
        with ui.grid(columns=1).classes("w-full gap-2 p-2"):
            with ui.card().classes("w-full p-3 rounded-lg").style("background-color: var(--color-bg-surface);"):
                ui.label("GIF Directory").classes("font-medium mb-2 text-sm")
                alert_settings_state.get_elements(alert_type)["gif_dir_input"] = (
                    ui.input(
                        "GIF Directory"
                    ).classes("w-full bg-theme-base rounded-md text-sm")
                )
                alert_settings_state.get_elements(alert_type)["gif_dir_input"].on(
                    "change",
                    lambda e, at=alert_type: track_field_change(
                        "gif_dir_input",
                        alert_settings_state.get_elements(at)["gif_dir_input"],
                        e,
                        at,
                    ),
                )
                ui.tooltip("Directory containing the GIF file").classes(
                    "bg-theme-surface"
                )

            with ui.card().classes("w-full p-3 rounded-lg").style("background-color: var(--color-bg-surface);"):
                ui.label("GIF File").classes("font-medium mb-2 text-sm")
                alert_settings_state.get_elements(alert_type)["gif_file_input"] = (
                    ui.input(
                        "GIF File"
                    ).classes("w-full bg-theme-base rounded-md text-sm")
                )
                alert_settings_state.get_elements(alert_type)["gif_file_input"].on(
                    "change",
                    lambda e, at=alert_type: track_field_change(
                        "gif_file_input",
                        alert_settings_state.get_elements(at)["gif_file_input"],
                        e,
                        at,
                    ),
                )
                ui.tooltip("Name of the GIF file").classes("bg-theme-surface")

            with ui.card().classes("w-full p-3 rounded-lg").style("background-color: var(--color-bg-surface);"):
                ui.label("File Selection").classes("font-medium mb-2 text-sm")
                gif_browse_btn = ui.button(
                    "Browse", icon="folder", on_click=lambda: handle_browse("gif")
                ).classes(
                    "control-button bg-theme-surface hover-theme-overlay transition-colors duration-200 text-sm"
                )
                ui.tooltip("Browse for GIF/image file").classes(
                    "bg-theme-surface"
                )


def create_twitch_options_section(alert_type: str):
    """Create the Twitch options section for point rewards"""
    with ui.expansion("Twitch Options", icon="api").classes(
        "w-full bg-theme-base rounded-lg overflow-hidden"
    ).style("border: 1px solid var(--color-border-default);"):
        with ui.grid(columns=2).classes("w-full gap-2 p-2"):
            # Basic reward info
            with ui.row().classes("w-full items-center"):
                alert_settings_state.get_elements(alert_type)["twitch_title_input"] = (
                    ui.input(
                        "Reward Title"
                    ).classes("w-full bg-theme-surface rounded-md text-sm")
                )
                alert_settings_state.get_elements(alert_type)["twitch_title_input"].on(
                    "change",
                    lambda e, at=alert_type: track_field_change(
                        "twitch_title_input",
                        alert_settings_state.get_elements(at)["twitch_title_input"],
                        e,
                        at,
                    ),
                )
                ui.tooltip("The title of the Twitch point reward").classes(
                    "bg-theme-surface"
                )

            with ui.row().classes("w-full items-center"):
                alert_settings_state.get_elements(alert_type)["twitch_cost_input"] = (
                    ui.number(
                        "Point Cost", value=100, min=1
                    ).classes("w-full bg-theme-surface rounded-md text-sm")
                )
                alert_settings_state.get_elements(alert_type)["twitch_cost_input"].on(
                    "change",
                    lambda e, at=alert_type: track_field_change(
                        "twitch_cost_input",
                        alert_settings_state.get_elements(at)["twitch_cost_input"],
                        e,
                        at,
                    ),
                )
                ui.tooltip("How many channel points this reward costs").classes(
                    "bg-theme-surface"
                )

            # Toggle switches
            with ui.row().classes("w-full items-center"):
                alert_settings_state.get_elements(alert_type)[
                    "twitch_enabled_switch"
                ] = ui.switch("Reward Enabled").classes("w-full q-switch")
                alert_settings_state.get_elements(alert_type)[
                    "twitch_enabled_switch"
                ].on(
                    "change",
                    lambda e, at=alert_type: track_field_change(
                        "twitch_enabled_switch",
                        alert_settings_state.get_elements(at)["twitch_enabled_switch"],
                        e,
                        at,
                    ),
                )
                ui.tooltip("Whether the reward is enabled on Twitch").classes(
                    "bg-theme-surface"
                )

            with ui.row().classes("w-full items-center"):
                alert_settings_state.get_elements(alert_type)[
                    "twitch_user_input_switch"
                ] = ui.switch("Require User Input").classes("w-full q-switch")
                alert_settings_state.get_elements(alert_type)[
                    "twitch_user_input_switch"
                ].on(
                    "change",
                    lambda e, at=alert_type: track_field_change(
                        "twitch_user_input_switch",
                        alert_settings_state.get_elements(at)[
                            "twitch_user_input_switch"
                        ],
                        e,
                        at,
                    ),
                )
                ui.tooltip(
                    "Whether users must provide text input when redeeming"
                ).classes("bg-theme-surface")

            # User input prompt (shown when user input is required)
            with ui.row().classes("w-full items-center col-span-2"):
                alert_settings_state.get_elements(alert_type)[
                    "twitch_user_input_prompt"
                ] = ui.input("User Input Prompt").classes(
                    "w-full bg-theme-surface rounded-md text-sm"
                )
                alert_settings_state.get_elements(alert_type)[
                    "twitch_user_input_prompt"
                ].on(
                    "change",
                    lambda e, at=alert_type: track_field_change(
                        "twitch_user_input_prompt",
                        alert_settings_state.get_elements(at)[
                            "twitch_user_input_prompt"
                        ],
                        e,
                        at,
                    ),
                )
                ui.tooltip(
                    "Prompt text shown to users when they redeem (if user input is required)"
                ).classes("bg-theme-surface")

            # Stream limits
            with ui.row().classes("w-full items-center"):
                alert_settings_state.get_elements(alert_type)[
                    "twitch_max_per_stream_switch"
                ] = ui.switch("Limit Per Stream").classes("w-full q-switch")
                alert_settings_state.get_elements(alert_type)[
                    "twitch_max_per_stream_switch"
                ].on(
                    "change",
                    lambda e, at=alert_type: track_field_change(
                        "twitch_max_per_stream_switch",
                        alert_settings_state.get_elements(at)[
                            "twitch_max_per_stream_switch"
                        ],
                        e,
                        at,
                    ),
                )
                ui.tooltip("Enable limit on total redemptions per stream").classes(
                    "bg-theme-surface"
                )

            with ui.row().classes("w-full items-center"):
                alert_settings_state.get_elements(alert_type)[
                    "twitch_max_per_stream_input"
                ] = ui.number("Max Per Stream", value=1, min=1).classes(
                    "w-full bg-theme-surface rounded-md text-sm"
                )
                alert_settings_state.get_elements(alert_type)[
                    "twitch_max_per_stream_input"
                ].on(
                    "change",
                    lambda e, at=alert_type: track_field_change(
                        "twitch_max_per_stream_input",
                        alert_settings_state.get_elements(at)[
                            "twitch_max_per_stream_input"
                        ],
                        e,
                        at,
                    ),
                )
                ui.tooltip("Maximum number of redemptions allowed per stream").classes(
                    "bg-theme-surface"
                )

            # User limits
            with ui.row().classes("w-full items-center"):
                alert_settings_state.get_elements(alert_type)[
                    "twitch_max_per_user_switch"
                ] = ui.switch("Limit Per User").classes("w-full q-switch")
                alert_settings_state.get_elements(alert_type)[
                    "twitch_max_per_user_switch"
                ].on(
                    "change",
                    lambda e, at=alert_type: track_field_change(
                        "twitch_max_per_user_switch",
                        alert_settings_state.get_elements(at)[
                            "twitch_max_per_user_switch"
                        ],
                        e,
                        at,
                    ),
                )
                ui.tooltip("Enable limit on redemptions per user per stream").classes(
                    "bg-theme-surface"
                )

            with ui.row().classes("w-full items-center"):
                alert_settings_state.get_elements(alert_type)[
                    "twitch_max_per_user_input"
                ] = ui.number("Max Per User", value=1, min=1).classes(
                    "w-full bg-theme-surface rounded-md text-sm"
                )
                alert_settings_state.get_elements(alert_type)[
                    "twitch_max_per_user_input"
                ].on(
                    "change",
                    lambda e, at=alert_type: track_field_change(
                        "twitch_max_per_user_input",
                        alert_settings_state.get_elements(at)[
                            "twitch_max_per_user_input"
                        ],
                        e,
                        at,
                    ),
                )
                ui.tooltip("Maximum number of redemptions per user per stream").classes(
                    "bg-theme-surface"
                )

            # Cooldown settings
            with ui.row().classes("w-full items-center"):
                alert_settings_state.get_elements(alert_type)[
                    "twitch_cooldown_switch"
                ] = ui.switch("Global Cooldown").classes("w-full q-switch")
                alert_settings_state.get_elements(alert_type)[
                    "twitch_cooldown_switch"
                ].on(
                    "change",
                    lambda e, at=alert_type: track_field_change(
                        "twitch_cooldown_switch",
                        alert_settings_state.get_elements(at)["twitch_cooldown_switch"],
                        e,
                        at,
                    ),
                )
                ui.tooltip("Enable global cooldown between redemptions").classes(
                    "bg-theme-surface"
                )

            with ui.row().classes("w-full items-center"):
                alert_settings_state.get_elements(alert_type)[
                    "twitch_cooldown_input"
                ] = ui.number("Cooldown (seconds)", value=60, min=1).classes(
                    "w-full bg-theme-surface rounded-md text-sm"
                )
                alert_settings_state.get_elements(alert_type)[
                    "twitch_cooldown_input"
                ].on(
                    "change",
                    lambda e, at=alert_type: track_field_change(
                        "twitch_cooldown_input",
                        alert_settings_state.get_elements(at)["twitch_cooldown_input"],
                        e,
                        at,
                    ),
                )
                ui.tooltip("Cooldown time in seconds between redemptions").classes(
                    "bg-theme-surface"
                )

            # Other options
            with ui.row().classes("w-full items-center col-span-2"):
                alert_settings_state.get_elements(alert_type)[
                    "twitch_skip_queue_switch"
                ] = ui.switch("Skip Request Queue").classes("w-full q-switch")
                alert_settings_state.get_elements(alert_type)[
                    "twitch_skip_queue_switch"
                ].on(
                    "change",
                    lambda e, at=alert_type: track_field_change(
                        "twitch_skip_queue_switch",
                        alert_settings_state.get_elements(at)[
                            "twitch_skip_queue_switch"
                        ],
                        e,
                        at,
                    ),
                )
                ui.tooltip(
                    "Skip the request queue and auto-fulfill redemptions"
                ).classes("bg-theme-surface")


def register_ui_elements():
    """Register all UI elements for tracking and updates"""
    try:
        logger.debug("UI elements are already registered during creation")
        # UI elements are now registered directly when they are created in create_alert_type_panel
        # This function is kept for compatibility but no longer needed

    except Exception as e:
        logger.error(f"Error registering UI elements: {str(e)}", exc_info=True)


# Points tab specific handler functions
def handle_point_reward_selection(e, alert_type: str):
    """Handle when a point reward is selected from the dropdown

    Args:
        e: The selection event
        alert_type (str): The type of alert (should be 'points')
    """
    try:
        # Validate the selection value - handle None and empty values gracefully
        if e.value is None:
            logger.debug("Selection value is None, ignoring")
            return
        if not isinstance(e.value, str) or not e.value.strip():
            logger.warning(f"Invalid selection value: {e.value}")
            return

        if e.value == "new":
            ui.notify(
                "New reward: set Twitch Options and alert settings, then Save Alert.",
                type="info",
            )
            set_default_values_for_new_point_reward(alert_type)
            store_original_values(alert_type)
        elif e.value in POINTS_REWARD_SELECT_PLACEHOLDERS:
            pass
        else:
            # Validate that it's a valid reward ID (should be a UUID-like string)
            if len(e.value) > 5:  # Basic validation - real reward IDs are much longer
                # Show notification for loading selected reward
                ui.notify(f"Loading point reward: {e.value}", type="info")
                # Load selected point reward settings
                load_point_reward_settings(alert_type, e.value)
            else:
                logger.warning(f"Invalid reward ID format: {e.value}")
                ui.notify("Invalid reward selection", type="warning")

        # Update delete button visibility based on selection
        update_delete_button_visibility(alert_type)

    except Exception as e:
        logger.error(f"Error handling point reward selection: {str(e)}", exc_info=True)
        ui.notify("Error loading point reward settings", type="negative")


def _schedule_point_reward_select_refresh(select_element):
    """Best-effort UI refresh for the point reward combobox after options change."""

    if not select_element:
        return

    def force_refresh():
        try:
            if select_element and hasattr(select_element, "update"):
                select_element.update()
                logger.debug("Forced refresh of point rewards select dropdown")
        except Exception as refresh_err:
            logger.error(f"Error in force refresh: {str(refresh_err)}")

    ui.timer(0.1, force_refresh, once=True)
    ui.timer(0.3, force_refresh, once=True)
    ui.timer(0.5, force_refresh, once=True)


def load_twitch_point_rewards():
    """Load Twitch point rewards from the API and update the dropdown"""
    alert_type = "points"
    try:
        from .. import twitch

        elements = alert_settings_state.get_elements(alert_type)
        select_element = elements.get("alert_select")
        if not select_element:
            logger.debug("Select element not found, cannot load rewards yet")
            return

        save_btn = elements.get("save_btn")
        banner = elements.get("channel_points_locked_banner")

        select_element.options = {"loading": "Loading Twitch rewards..."}
        if hasattr(select_element, "update"):
            select_element.update()

        result = twitch.fetch_channel_point_rewards()
        status = result["status"]
        rewards = result.get("rewards")

        def set_save(enabled: bool):
            if save_btn is not None:
                save_btn.enabled = enabled

        def set_banner(visible: bool):
            if banner is not None:
                banner.visible = visible

        if status == "not_unlocked":
            set_banner(True)
            set_save(False)
            select_element.options = {
                "no_channel_points": (
                    "Channel points not available — Affiliate or Partner required"
                )
            }
            if hasattr(select_element, "update"):
                select_element.update()
            ui.notify(
                "This Twitch account does not have Channel Points (custom rewards) unlocked.",
                type="warning",
            )
            _schedule_point_reward_select_refresh(select_element)
            return

        set_banner(False)

        if status == "not_connected":
            set_save(False)
            select_element.options = {
                "not_connected": "Twitch not connected - Please connect in Settings"
            }
            if hasattr(select_element, "update"):
                select_element.update()
            ui.notify(
                "Twitch is not connected. Please connect to Twitch in the Settings tab first.",
                type="warning",
            )
            _schedule_point_reward_select_refresh(select_element)
            return

        if status == "error":
            set_save(False)
            select_element.options = {
                "error": "Error loading rewards - Check connection or authentication"
            }
            if hasattr(select_element, "update"):
                select_element.update()
            ui.notify(
                "Error loading Twitch point rewards. Please check your Twitch connection and authentication in Settings.",
                type="negative",
            )
            _schedule_point_reward_select_refresh(select_element)
            return

        set_save(True)
        reward_options = {"new": "+ Create New Reward"}
        if rewards:
            sorted_rewards = sorted(
                rewards, key=lambda r: r.get("title", "Unnamed Reward").lower()
            )
            for reward in sorted_rewards:
                reward_id = reward.get("id")
                reward_title = reward.get("title", "Unnamed Reward")
                reward_cost = reward.get("cost", 0)
                display_name = f"{reward_title} ({reward_cost} points)"
                reward_options[reward_id] = display_name
            ui.notify(
                f"Loaded {len(rewards)} point rewards from Twitch", type="positive"
            )
        else:
            reward_options["no_rewards"] = (
                "No rewards yet — choose + Create New Reward and click Save Alert"
            )
            ui.notify(
                "No point rewards on Twitch yet. Use + Create New Reward, set Twitch Options, then Save Alert.",
                type="info",
            )

        select_element.options = reward_options
        if hasattr(select_element, "update"):
            select_element.update()
        _schedule_point_reward_select_refresh(select_element)

    except Exception as e:
        logger.error(f"Error loading Twitch point rewards: {str(e)}", exc_info=True)
        reward_options = {
            "error": "Error loading rewards - Check connection or authentication"
        }
        select_element = alert_settings_state.get_elements(alert_type).get(
            "alert_select"
        )
        save_btn = alert_settings_state.get_elements(alert_type).get("save_btn")
        banner = alert_settings_state.get_elements(alert_type).get(
            "channel_points_locked_banner"
        )
        if banner is not None:
            banner.visible = False
        if save_btn is not None:
            save_btn.enabled = False
        if select_element:
            select_element.options = reward_options
            if hasattr(select_element, "update"):
                select_element.update()
            _schedule_point_reward_select_refresh(select_element)
        ui.notify(
            "Error loading Twitch point rewards. Please check your Twitch connection and authentication in Settings.",
            type="negative",
        )


def set_default_values_for_new_point_reward(alert_type: str):
    """Set default values for a new point reward"""
    # Set common alert defaults
    alert_settings_state.get_elements(alert_type)["enable_alert_switch"].value = False
    alert_settings_state.get_elements(alert_type)["duration_input"].value = 3.0
    alert_settings_state.get_elements(alert_type)["stackable_switch"].value = False
    alert_settings_state.get_elements(alert_type)["fade_in_input"].value = 0
    alert_settings_state.get_elements(alert_type)["fade_out_input"].value = 0
    update_volume_value(alert_type, 100)

    # Set default sound settings
    alert_settings_state.get_elements(alert_type)["primary_dir_input"].value = ""
    alert_settings_state.get_elements(alert_type)["primary_file_input"].value = ""
    alert_settings_state.get_elements(alert_type)["randomized_switch"].value = False
    alert_settings_state.get_elements(alert_type)["random_dir_input"].value = ""
    alert_settings_state.get_elements(alert_type)["random_chance_input"].value = 0
    alert_settings_state.get_elements(alert_type)[
        "randomized_extra_switch"
    ].value = False
    alert_settings_state.get_elements(alert_type)["extra_dir_input"].value = ""
    alert_settings_state.get_elements(alert_type)["extra_chance_input"].value = 0

    # Set default visual settings
    alert_settings_state.get_elements(alert_type)["gif_dir_input"].value = ""
    alert_settings_state.get_elements(alert_type)["gif_file_input"].value = ""
    alert_settings_state.get_elements(alert_type)["tts_enabled_switch"].value = False
    alert_settings_state.get_elements(alert_type)["tts_source_select"].value = (
        "alert_message"
    )
    alert_settings_state.get_elements(alert_type)["tts_custom_message_input"].value = ""
    update_tts_custom_message_visibility(alert_type)

    # Set default Twitch settings
    alert_settings_state.get_elements(alert_type)[
        "twitch_title_input"
    ].value = "New Point Reward"
    alert_settings_state.get_elements(alert_type)["twitch_cost_input"].value = 100
    alert_settings_state.get_elements(alert_type)["twitch_enabled_switch"].value = True
    alert_settings_state.get_elements(alert_type)[
        "twitch_user_input_switch"
    ].value = False
    alert_settings_state.get_elements(alert_type)["twitch_user_input_prompt"].value = ""
    alert_settings_state.get_elements(alert_type)[
        "twitch_max_per_stream_switch"
    ].value = False
    alert_settings_state.get_elements(alert_type)[
        "twitch_max_per_stream_input"
    ].value = 1
    alert_settings_state.get_elements(alert_type)[
        "twitch_max_per_user_switch"
    ].value = False
    alert_settings_state.get_elements(alert_type)["twitch_max_per_user_input"].value = 1
    alert_settings_state.get_elements(alert_type)[
        "twitch_cooldown_switch"
    ].value = False
    alert_settings_state.get_elements(alert_type)["twitch_cooldown_input"].value = 60
    alert_settings_state.get_elements(alert_type)[
        "twitch_skip_queue_switch"
    ].value = True


def set_default_values_for_new_point_reward_non_twitch(alert_type: str):
    """Set default values for a new point reward, excluding Twitch settings (which should be loaded from API)"""
    # Set common alert defaults
    alert_settings_state.get_elements(alert_type)["enable_alert_switch"].value = False
    alert_settings_state.get_elements(alert_type)["duration_input"].value = 3.0
    alert_settings_state.get_elements(alert_type)["stackable_switch"].value = False
    alert_settings_state.get_elements(alert_type)["fade_in_input"].value = 0
    alert_settings_state.get_elements(alert_type)["fade_out_input"].value = 0
    update_volume_value(alert_type, 100)

    # Set default sound settings
    alert_settings_state.get_elements(alert_type)["primary_dir_input"].value = ""
    alert_settings_state.get_elements(alert_type)["primary_file_input"].value = ""
    alert_settings_state.get_elements(alert_type)["randomized_switch"].value = False
    alert_settings_state.get_elements(alert_type)["random_dir_input"].value = ""
    alert_settings_state.get_elements(alert_type)["random_chance_input"].value = 0
    alert_settings_state.get_elements(alert_type)[
        "randomized_extra_switch"
    ].value = False
    alert_settings_state.get_elements(alert_type)["extra_dir_input"].value = ""
    alert_settings_state.get_elements(alert_type)["extra_chance_input"].value = 0

    # Set default visual settings
    alert_settings_state.get_elements(alert_type)["gif_dir_input"].value = ""
    alert_settings_state.get_elements(alert_type)["gif_file_input"].value = ""
    alert_settings_state.get_elements(alert_type)["tts_enabled_switch"].value = False
    alert_settings_state.get_elements(alert_type)["tts_source_select"].value = (
        "alert_message"
    )
    alert_settings_state.get_elements(alert_type)["tts_custom_message_input"].value = ""
    update_tts_custom_message_visibility(alert_type)

    # NOTE: Twitch settings are intentionally NOT set here - they should be loaded from API data


def load_point_reward_settings(alert_type: str, reward_id: str):
    """Load point reward settings from both Twitch API and local alert data"""
    try:
        from .. import twitch

        # First, get the Twitch reward data
        reward_data = twitch.get_point_reward_by_id(reward_id)
        if not reward_data:
            ui.notify(f"Point reward {reward_id} not found on Twitch", type="negative")
            return

        # Load Twitch settings into UI
        alert_settings_state.get_elements(alert_type)[
            "twitch_title_input"
        ].value = reward_data.get("title", "")
        alert_settings_state.get_elements(alert_type)[
            "twitch_cost_input"
        ].value = reward_data.get("cost", 100)
        alert_settings_state.get_elements(alert_type)[
            "twitch_enabled_switch"
        ].value = reward_data.get("is_enabled", True)
        alert_settings_state.get_elements(alert_type)[
            "twitch_user_input_switch"
        ].value = reward_data.get("is_user_input_required", False)
        alert_settings_state.get_elements(alert_type)[
            "twitch_user_input_prompt"
        ].value = reward_data.get("prompt", "")

        # Handle limits
        max_per_stream = reward_data.get("max_per_stream")
        if max_per_stream and max_per_stream.get("is_enabled"):
            alert_settings_state.get_elements(alert_type)[
                "twitch_max_per_stream_switch"
            ].value = True
            alert_settings_state.get_elements(alert_type)[
                "twitch_max_per_stream_input"
            ].value = max_per_stream.get("max_per_stream", 1)
        else:
            alert_settings_state.get_elements(alert_type)[
                "twitch_max_per_stream_switch"
            ].value = False
            alert_settings_state.get_elements(alert_type)[
                "twitch_max_per_stream_input"
            ].value = 1

        max_per_user = reward_data.get("max_per_user_per_stream")
        if max_per_user and max_per_user.get("is_enabled"):
            alert_settings_state.get_elements(alert_type)[
                "twitch_max_per_user_switch"
            ].value = True
            alert_settings_state.get_elements(alert_type)[
                "twitch_max_per_user_input"
            ].value = max_per_user.get("max_per_user_per_stream", 1)
        else:
            alert_settings_state.get_elements(alert_type)[
                "twitch_max_per_user_switch"
            ].value = False
            alert_settings_state.get_elements(alert_type)[
                "twitch_max_per_user_input"
            ].value = 1

        # Handle cooldown
        cooldown = reward_data.get("global_cooldown")
        if cooldown and cooldown.get("is_enabled"):
            alert_settings_state.get_elements(alert_type)[
                "twitch_cooldown_switch"
            ].value = True
            alert_settings_state.get_elements(alert_type)[
                "twitch_cooldown_input"
            ].value = cooldown.get("global_cooldown_seconds", 60)
        else:
            alert_settings_state.get_elements(alert_type)[
                "twitch_cooldown_switch"
            ].value = False
            alert_settings_state.get_elements(alert_type)[
                "twitch_cooldown_input"
            ].value = 60

        alert_settings_state.get_elements(alert_type)[
            "twitch_skip_queue_switch"
        ].value = reward_data.get("should_redemptions_skip_request_queue", True)

        # Next, check if we have local alert data for this reward
        alert_data = alertutils.alert_state_manager.get_alert_by_id(
            alert_type, reward_id
        )
        if alert_data:
            # Get elements and verify they exist
            elements = alert_settings_state.get_elements(alert_type)

            # Load local alert settings with element validation
            if "enable_alert_switch" in elements and elements["enable_alert_switch"]:
                elements["enable_alert_switch"].value = alert_data.get(
                    "enable_alert", False
                )
            if "duration_input" in elements and elements["duration_input"]:
                elements["duration_input"].value = float(
                    alert_data.get("duration", 3.0)
                )
            if "stackable_switch" in elements and elements["stackable_switch"]:
                elements["stackable_switch"].value = bool(
                    alert_data.get("stackable", False)
                )
            if "fade_in_input" in elements and elements["fade_in_input"]:
                elements["fade_in_input"].value = int(alert_data.get("fade_in", 0))
            if "fade_out_input" in elements and elements["fade_out_input"]:
                elements["fade_out_input"].value = int(alert_data.get("fade_out", 0))
            if "volume_input" in elements and elements["volume_input"]:
                update_volume_value(alert_type, int(alert_data.get("volume", 100)))
            if "tts_enabled_switch" in elements and elements["tts_enabled_switch"]:
                elements["tts_enabled_switch"].value = bool(
                    alert_data.get("tts_enabled", False)
                )
            if "tts_source_select" in elements and elements["tts_source_select"]:
                elements["tts_source_select"].value = str(
                    alert_data.get("tts_source", "alert_message")
                    or "alert_message"
                )
            if (
                "tts_custom_message_input" in elements
                and elements["tts_custom_message_input"]
            ):
                elements["tts_custom_message_input"].value = (
                    alert_data.get("tts_custom_message", "") or ""
                )
            update_tts_custom_message_visibility(alert_type)
            # Only set audio_only_switch if it exists (only available in points alerts)
            if "audio_only_switch" in elements and elements["audio_only_switch"]:
                elements["audio_only_switch"].value = bool(
                    alert_data.get("audio_only", False)
                )

            # Load sound settings with element validation
            if "primary_dir_input" in elements and elements["primary_dir_input"]:
                elements["primary_dir_input"].value = (
                    alert_data.get("single_audio_dir", "") or ""
                )
            if "primary_file_input" in elements and elements["primary_file_input"]:
                elements["primary_file_input"].value = (
                    alert_data.get("single_audio_name", "") or ""
                )
            if "randomized_switch" in elements and elements["randomized_switch"]:
                elements["randomized_switch"].value = bool(
                    alert_data.get("randomized", False)
                )
            if "random_dir_input" in elements and elements["random_dir_input"]:
                elements["random_dir_input"].value = (
                    alert_data.get("randomized_dir", "") or ""
                )
            if "random_chance_input" in elements and elements["random_chance_input"]:
                elements["random_chance_input"].value = int(
                    alert_data.get("randomized_chance", 0)
                )
            if (
                "randomized_extra_switch" in elements
                and elements["randomized_extra_switch"]
            ):
                elements["randomized_extra_switch"].value = bool(
                    alert_data.get("randomized_extra", False)
                )
            if "extra_dir_input" in elements and elements["extra_dir_input"]:
                elements["extra_dir_input"].value = (
                    alert_data.get("randomized_extra_dir", "") or ""
                )
            if "extra_chance_input" in elements and elements["extra_chance_input"]:
                elements["extra_chance_input"].value = int(
                    alert_data.get("randomized_extra_chance", 0)
                )

            # Load visual settings with element validation and logging
            if "gif_dir_input" in elements and elements["gif_dir_input"]:
                gif_dir_value = alert_data.get("gif_dir", "") or ""
                elements["gif_dir_input"].value = gif_dir_value
                logger.debug(f"Setting gif_dir_input to: {gif_dir_value}")
            else:
                logger.warning(
                    f"gif_dir_input element not found or is None for {alert_type}"
                )

            if "gif_file_input" in elements and elements["gif_file_input"]:
                gif_file_value = alert_data.get("gif_name", "") or ""
                elements["gif_file_input"].value = gif_file_value
                logger.debug(f"Setting gif_file_input to: {gif_file_value}")
            else:
                logger.warning(
                    f"gif_file_input element not found or is None for {alert_type}"
                )
        else:
            # No local alert data, set defaults for non-Twitch fields only
            # Note: Twitch settings are already loaded from API above and should NOT be overwritten
            set_default_values_for_new_point_reward_non_twitch(alert_type)
            alert_settings_state.get_elements(alert_type)[
                "enable_alert_switch"
            ].value = False

        # Force refresh of specific UI elements that commonly have display issues
        def force_ui_refresh():
            try:
                # Get current elements (in case they've changed)
                current_elements = alert_settings_state.get_elements(alert_type)

                # Specifically refresh GIF inputs which seem to have display issues
                gif_dir_element = current_elements.get("gif_dir_input")
                gif_file_element = current_elements.get("gif_file_input")

                if gif_dir_element and hasattr(gif_dir_element, "value"):
                    current_gif_dir = gif_dir_element.value
                    logger.debug(
                        f"Force refreshing gif_dir_input with value: {current_gif_dir}"
                    )
                    # Force update by setting value again and calling update if available
                    gif_dir_element.value = current_gif_dir
                    if hasattr(gif_dir_element, "update"):
                        gif_dir_element.update()

                if gif_file_element and hasattr(gif_file_element, "value"):
                    current_gif_file = gif_file_element.value
                    logger.debug(
                        f"Force refreshing gif_file_input with value: {current_gif_file}"
                    )
                    # Force update by setting value again and calling update if available
                    gif_file_element.value = current_gif_file
                    if hasattr(gif_file_element, "update"):
                        gif_file_element.update()

                # Also refresh other input elements that might have similar issues
                for input_name in [
                    "primary_dir_input",
                    "primary_file_input",
                    "random_dir_input",
                    "extra_dir_input",
                ]:
                    element = current_elements.get(input_name)
                    if element and hasattr(element, "value"):
                        current_value = element.value
                        element.value = current_value
                        if hasattr(element, "update"):
                            element.update()

            except Exception as refresh_err:
                logger.error(f"Error in force UI refresh: {str(refresh_err)}")

        # Schedule multiple refresh attempts with delays
        ui.timer(0.1, force_ui_refresh, once=True)
        ui.timer(0.3, force_ui_refresh, once=True)
        ui.timer(0.5, force_ui_refresh, once=True)

        # Store original values
        store_original_values(alert_type)
        # Clear any changed styling
        clear_changed_styling(alert_type)

        logger.debug(f"Successfully loaded point reward {reward_id}")

        # Update delete button visibility
        update_delete_button_visibility(alert_type)

    except Exception as e:
        logger.error(f"Error loading point reward settings: {str(e)}", exc_info=True)
        ui.notify("Error loading point reward settings", type="negative")


def _twitch_ui_values_equal(orig, cur) -> bool:
    if isinstance(orig, (int, float)) or isinstance(cur, (int, float)):
        try:
            return float(orig) == float(cur)
        except (TypeError, ValueError):
            return orig == cur
    if isinstance(orig, str) or isinstance(cur, str):
        return (orig or "").strip() == (cur or "").strip()
    return orig == cur


def twitch_reward_fields_changed(alert_type: str) -> bool:
    """True if any Twitch Options field differs from values stored in store_original_values."""
    originals = alert_settings_state.get_original_values(alert_type)
    elements = alert_settings_state.get_elements(alert_type)
    for field in TWITCH_REWARD_UI_FIELD_NAMES:
        el = elements.get(field)
        if not el or not hasattr(el, "value"):
            continue
        if not _twitch_ui_values_equal(originals.get(field), el.value):
            return True
    return False


def build_twitch_reward_payload_from_ui(alert_type: str) -> dict:
    """Build dict for twitch.create_point_reward / update_point_reward (_convert_ui_to_api_format)."""
    els = alert_settings_state.get_elements(alert_type)
    title = (els["twitch_title_input"].value or "").strip()
    payload = {
        "title": title,
        "cost": int(els["twitch_cost_input"].value),
        "is_enabled": els["twitch_enabled_switch"].value,
        "is_user_input_required": els["twitch_user_input_switch"].value,
        "prompt": els["twitch_user_input_prompt"].value,
        "should_redemptions_skip_request_queue": els["twitch_skip_queue_switch"].value,
    }
    if els["twitch_max_per_stream_switch"].value:
        payload["max_per_stream"] = {
            "is_enabled": True,
            "max_per_stream": int(els["twitch_max_per_stream_input"].value),
        }
    else:
        payload["max_per_stream"] = {"is_enabled": False}

    if els["twitch_max_per_user_switch"].value:
        payload["max_per_user_per_stream"] = {
            "is_enabled": True,
            "max_per_user_per_stream": int(els["twitch_max_per_user_input"].value),
        }
    else:
        payload["max_per_user_per_stream"] = {"is_enabled": False}

    if els["twitch_cooldown_switch"].value:
        payload["global_cooldown"] = {
            "is_enabled": True,
            "global_cooldown_seconds": int(els["twitch_cooldown_input"].value),
        }
    else:
        payload["global_cooldown"] = {"is_enabled": False}

    return payload


def save_point_alert():
    """Save the current point alert settings (creates or updates Twitch reward when needed)."""
    try:
        from .. import twitch

        alert_type = "points"
        elements = alert_settings_state.get_elements(alert_type)
        selected = elements["alert_select"].value

        if not twitch.twitch_api or not twitch.twitch_api.is_connected:
            ui.notify(
                "Twitch is not connected. Please connect to Twitch in Settings first.",
                type="negative",
            )
            return

        fetch = twitch.fetch_channel_point_rewards()
        if fetch["status"] == "not_unlocked":
            ui.notify(
                "Channel Points are not unlocked for this Twitch account.",
                type="warning",
            )
            return

        if fetch["status"] not in ("ok",):
            ui.notify(
                "Cannot reach Twitch Channel Points right now. Try Refresh Rewards.",
                type="warning",
            )
            return

        if selected in POINTS_REWARD_SELECT_PLACEHOLDERS and selected != "new":
            ui.notify("Please select a valid point reward", type="warning")
            return

        selected_reward_id = selected

        if selected == "new":
            title = (elements["twitch_title_input"].value or "").strip()
            if not title:
                ui.notify(
                    "Please enter a reward title in Twitch Options.",
                    type="warning",
                )
                return
            cost_val = elements["twitch_cost_input"].value
            if cost_val is None or int(cost_val) < 1:
                ui.notify("Point cost must be at least 1.", type="warning")
                return
            ui.notify("Creating reward on Twitch...", type="info")
            new_reward = twitch.create_point_reward(
                build_twitch_reward_payload_from_ui(alert_type)
            )
            if not new_reward or not new_reward.get("id"):
                ui.notify("Failed to create reward on Twitch.", type="negative")
                return
            selected_reward_id = new_reward["id"]
        else:
            if twitch_reward_fields_changed(alert_type):
                if not twitch.update_point_reward(
                    selected_reward_id,
                    build_twitch_reward_payload_from_ui(alert_type),
                ):
                    ui.notify(
                        "Error updating Twitch point reward. Local alert was not saved.",
                        type="negative",
                    )
                    return

        duration = elements["duration_input"].value
        stackable = elements["stackable_switch"].value
        fade_in = elements["fade_in_input"].value
        fade_out = elements["fade_out_input"].value
        volume = elements["volume_input"].value
        tts_enabled = elements["tts_enabled_switch"].value
        tts_source = elements["tts_source_select"].value or "alert_message"
        tts_custom_message = elements["tts_custom_message_input"].value or ""
        audio_only = (
            elements["audio_only_switch"].value
            if "audio_only_switch" in elements
            else False
        )

        primary_dir = elements["primary_dir_input"].value or ""
        primary_file = elements["primary_file_input"].value or ""
        randomized = elements["randomized_switch"].value
        random_dir = elements["random_dir_input"].value or ""
        random_chance = elements["random_chance_input"].value
        randomized_extra = elements["randomized_extra_switch"].value
        extra_dir = elements["extra_dir_input"].value or ""
        extra_chance = elements["extra_chance_input"].value
        gif_dir = elements["gif_dir_input"].value or ""
        gif_file = elements["gif_file_input"].value or ""

        enable_alert = elements["enable_alert_switch"].value
        reward_title = (elements["twitch_title_input"].value or "").strip()

        alert_data = {
            "alert_type": alert_type,
            "enable_alert": enable_alert,
            "duration": duration,
            "stackable": stackable,
            "fade_in": fade_in,
            "fade_out": fade_out,
            "volume": volume,
            "tts_enabled": tts_enabled,
            "tts_source": tts_source,
            "tts_custom_message": tts_custom_message,
            "audio_only": audio_only,
            "single_audio_dir": primary_dir,
            "single_audio_name": primary_file,
            "randomized": randomized,
            "randomized_dir": random_dir,
            "randomized_chance": random_chance,
            "randomized_extra": randomized_extra,
            "randomized_extra_dir": extra_dir,
            "randomized_extra_chance": extra_chance,
            "gif_dir": gif_dir,
            "gif_name": gif_file,
            "twitch_reward_id": selected_reward_id,
            "point_cost": elements["twitch_cost_input"].value,
            "title": reward_title,
            "alert_name": alertutils.alert_state_manager.get_display_name(
                alert_type,
                selected_reward_id,
                {"title": reward_title},
            ),
            "timestamp": time.time(),
        }

        success = alertutils.alert_state_manager.save_alert(
            alert_type, selected_reward_id, alert_data
        )

        if success:
            ui.notify(f'Saved point alert: {alert_data["title"]}', type="positive")
            load_twitch_point_rewards()
            rid = selected_reward_id

            def after_list_refresh():
                try:
                    sel = alert_settings_state.get_elements(alert_type).get(
                        "alert_select"
                    )
                    if sel:
                        sel.value = rid
                        if hasattr(sel, "update"):
                            sel.update()
                    load_point_reward_settings(alert_type, rid)
                except Exception as refresh_err:
                    logger.error(
                        f"Error re-selecting point reward after save: {refresh_err}",
                        exc_info=True,
                    )
                    store_original_values(alert_type)
                    clear_changed_styling(alert_type)

            ui.timer(0.85, after_list_refresh, once=True)
        else:
            ui.notify("Error saving point alert settings", type="negative")

    except Exception as e:
        logger.error(f"Error saving point alert: {str(e)}", exc_info=True)
        ui.notify("Error saving point alert settings", type="negative")


def show_delete_confirmation(alert_type: str):
    """Show a confirmation dialog before deleting an alert

    Args:
        alert_type (str): The type of alert being deleted
    """
    try:
        # Get the currently selected alert
        selected_alert_id = alert_settings_state.get_elements(alert_type)[
            "alert_select"
        ].value

        if (
            not selected_alert_id
            or selected_alert_id in POINTS_REWARD_SELECT_PLACEHOLDERS
        ):
            ui.notify("No valid alert selected for deletion", type="warning")
            return

        # Get alert data to show in confirmation
        alert_data = alertutils.alert_state_manager.get_alert_by_id(
            alert_type, selected_alert_id
        )
        if not alert_data:
            ui.notify("Alert not found", type="negative")
            return

        # Get display name for the alert using the centralized method
        alert_name = alertutils.alert_state_manager.get_display_name(
            alert_type, selected_alert_id, alert_data
        )

        with ui.dialog() as dialog, ui.card().classes("w-96 p-4"):
            ui.label("Confirm Delete Alert").classes(
                "text-lg font-bold mb-4 text-red-400"
            )

            with ui.column().classes("w-full gap-3"):
                ui.label(f"Are you sure you want to delete this alert?").classes(
                    "text-base"
                )

                # Show alert details
                with ui.card().classes("w-full p-3 bg-theme-surface rounded-lg"):
                    ui.label("Alert Details:").classes("font-medium text-sm mb-2")
                    ui.label(f"Type: {alert_type.title()}").classes("text-sm")
                    ui.label(f"Name: {alert_name}").classes("text-sm")
                    ui.label(f"ID: {selected_alert_id}").classes(
                        "text-sm secondary-text"
                    )

                ui.label("This action cannot be undone!").classes(
                    "text-red-400 font-medium"
                )

                # Buttons
                with ui.row().classes("w-full justify-end gap-2 mt-4"):
                    ui.button("Cancel", on_click=dialog.close).classes(
                        "btn-cancel transition-colors duration-200"
                    )
                    ui.button(
                        "Delete Alert",
                        icon="delete",
                        on_click=lambda: confirm_delete_alert(
                            dialog, alert_type, selected_alert_id
                        ),
                    ).classes("alert-delete-btn transition-colors duration-200")

        dialog.open()

    except Exception as e:
        logger.error(f"Error showing delete confirmation: {str(e)}", exc_info=True)
        ui.notify("Error showing delete confirmation dialog", type="negative")


def confirm_delete_alert(dialog, alert_type: str, alert_id: str):
    """Confirm and execute the alert deletion

    Args:
        dialog: The confirmation dialog to close
        alert_type (str): The type of alert being deleted
        alert_id (str): The ID of the alert to delete
    """
    try:
        # Close the dialog first
        dialog.close()

        # Show deletion in progress
        ui.notify("Deleting alert...", type="info")

        # Delete the alert using AlertStateManager
        success = alertutils.alert_state_manager.delete_alert(alert_type, alert_id)

        if success:
            ui.notify(
                f"Successfully deleted {alert_type} alert: {alert_id}", type="positive"
            )

            # Update the dropdown to remove the deleted alert
            try:
                # Reload alerts from Firebase to get updated list
                alertutils.alert_state_manager.reload_from_firebase()

                if alert_type == "points":
                    load_twitch_point_rewards()
                    alert_settings_state.get_elements(alert_type)[
                        "alert_select"
                    ].value = "new"
                    set_default_values_for_new_point_reward(alert_type)
                    store_original_values(alert_type)
                    update_delete_button_visibility(alert_type)
                    logger.debug(
                        "Updated points alert selector via Twitch rewards after deletion"
                    )
                else:
                    alerts = get_alerts_for_type(alert_type)

                    alert_options = {
                        "new": "+ Create New Alert",
                    }

                    sorted_alerts = sort_alert_ids(alert_type, list(alerts.items()))
                    for a_id, a_data in sorted_alerts:
                        display_name = alertutils.alert_state_manager.get_display_name(
                            alert_type, a_id, a_data
                        )
                        alert_options[a_id] = display_name

                    if alert_type == "subs":
                        fallback_id = alertutils.AlertSettings.FALLBACK_ALERT_ID
                        if fallback_id not in alert_options:
                            alert_options[fallback_id] = "Resub Fallback"

                    alert_settings_state.get_elements(alert_type)[
                        "alert_select"
                    ].options = alert_options

                    alert_settings_state.get_elements(alert_type)[
                        "alert_select"
                    ].value = "new"

                    set_default_values_for_new_alert(alert_type)

                    store_original_values(alert_type)

                    update_delete_button_visibility(alert_type)

                    logger.debug(
                        f"Updated alert selector after deletion. New options: {alert_options}"
                    )

            except Exception as dropdown_err:
                logger.error(
                    f"Error updating alert dropdown after deletion: {str(dropdown_err)}",
                    exc_info=True,
                )
                # This shouldn't prevent the deletion from being successful
        else:
            ui.notify("Error deleting alert", type="negative")

    except Exception as e:
        logger.error(f"Error confirming alert deletion: {str(e)}", exc_info=True)
        ui.notify("Error deleting alert", type="negative")


def refresh_alert_dropdowns():
    """Refresh all alert dropdowns to show updated alert lists after data changes"""
    try:
        # Get all alert types that have dropdowns
        alert_types = [
            "bits",
            "subs",
            "giftsubs",
            "donations",
            "raids",
            "follows",
            "points",
        ]

        for alert_type in alert_types:
            # Check if the elements exist for this alert type
            elements = alert_settings_state.get_elements(alert_type)
            alert_select = elements.get("alert_select")
            if alert_select:
                if alert_type == "points":
                    load_twitch_point_rewards()
                    continue

                alerts = get_alerts_for_type(alert_type)
                alert_options = {
                    "new": "+ Create New Alert",
                }

                sorted_alerts = sort_alert_ids(alert_type, list(alerts.items()))
                for alert_id, alert_data in sorted_alerts:
                    # Use the centralized display name method from AlertStateManager
                    display_name = alertutils.alert_state_manager.get_display_name(
                        alert_type, alert_id, alert_data
                    )
                    alert_options[alert_id] = display_name

                # Ensure fallback entry is always present for subs
                if alert_type == "subs":
                    fallback_id = alertutils.AlertSettings.FALLBACK_ALERT_ID
                    if fallback_id not in alert_options:
                        alert_options[fallback_id] = "Resub Fallback"

                # Update the select options
                alert_select.options = alert_options

                # If the current selection is no longer valid, reset to "new"
                if alert_select.value not in alert_options:
                    alert_select.value = "new"

    except Exception as e:
        logger.error(f"Error refreshing alert dropdowns: {e}", exc_info=True)


def update_delete_button_visibility(alert_type: str):
    """Update the visibility of the delete button based on current selection

    Args:
        alert_type (str): The type of alert to update
    """
    try:
        # Get the delete button element
        delete_btn = alert_settings_state.get_elements(alert_type).get("delete_btn")
        if not delete_btn:
            return

        # Get the currently selected alert
        selected_alert_id = alert_settings_state.get_elements(alert_type)[
            "alert_select"
        ].value

        # Show delete button only for existing alerts (not 'new' or special states)
        if (
            selected_alert_id
            and selected_alert_id not in POINTS_REWARD_SELECT_PLACEHOLDERS
        ):
            delete_btn.visible = True
        else:
            delete_btn.visible = False

        logger.debug(
            f"Updated delete button visibility for {alert_type}: {delete_btn.visible} (selected: {selected_alert_id})"
        )

    except Exception as e:
        logger.error(
            f"Error updating delete button visibility: {str(e)}", exc_info=True
        )
        logger.error(
            f"Error updating delete button visibility: {str(e)}", exc_info=True
        )

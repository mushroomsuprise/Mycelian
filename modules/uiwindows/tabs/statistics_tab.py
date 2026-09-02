# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, Generator, List, Optional

from nicegui import ui
from ...notification_engine import notify
from ...ui_form_controls import form_input
from ...ui_timer import layout_schedule

from ... import dataobjects
from ...dataobjects import state_manager
from ...statistics_manager import get_statistics_manager

logger = logging.getLogger(__name__)


class StatisticsTab:
    name = "Statistics"

    def __init__(self) -> None:
        self.dirty: bool = (
            False  # Statistics tab doesn't have save/discard functionality
        )
        self.buffer: Optional[dataobjects.AppSettings] = None
        self.ui_elements: Dict[str, Any] = {}

        # Statistics-specific attributes
        self.statistics_container = None
        self.live_update_timer = None
        self.live_updates_enabled = False
        self.live_update_interval = 5  # seconds
        self._update_counter = 0
        # Full UI rebuild (for dynamic “top user” cards) clears inputs; keep this modest.
        self._live_full_refresh_every_n = 36  # e.g. 36 * 5s ≈ 3 minutes

        # UI element references for live updates
        self._session_duration_label = None
        self._last_save_label = None
        self._saving_status_label = None
        self._live_status_label = None
        self._live_toggle_button = None

        # Alert statistic labels
        self._bit_alerts_label = None
        self._total_bits_label = None
        self._resubs_label = None
        self._new_subs_label = None
        self._gift_subs_label = None
        self._total_gift_subs_label = None

        # Social statistic labels
        self._follow_alerts_label = None
        self._point_alerts_label = None
        self._total_channel_points_label = None
        self._twitch_messages_label = None

        # Hype train level labels (dictionary for dynamic levels)
        self._hype_train_labels: Dict[int, Any] = {}

        # Connector statistic labels
        self._connectors_created_label = None
        self._total_triggers_label = None
        self._connectors_triggered_label = None

        # Chatbot statistic labels
        self._commands_created_label = None
        self._events_created_label = None
        self._commands_triggered_label = None
        self._events_triggered_label = None
        self._giveaways_completed_label = None
        self._giveaways_entries_label = None
        self._giveaways_avg_label = None

        # Quote statistic labels
        self._quotes_created_label = None
        self._quotes_redeemed_label = None

        # Template statistic labels (dict for dynamic templates)
        self._template_stat_labels = {}

        # Per-user statistics section references
        self._user_search_input = None
        self._user_start_date = None
        self._user_end_date = None
        self._per_user_results_container = None
        self._selected_username: Optional[str] = None

        # Export highlights section references
        self._export_start_date = None
        self._export_end_date = None
        self._export_status_label = None

    # ----- lifecycle -----
    def on_enter(self) -> None:
        """Called when the tab becomes active."""
        # Start live updates when entering the tab
        self._start_live_updates()

    def on_exit(self) -> None:
        """Called when the tab is no longer active."""
        # Stop live updates when leaving the tab
        self._stop_live_updates()

    def save(self) -> None:
        """Statistics tab doesn't have save functionality."""
        pass

    def discard(self) -> None:
        """Statistics tab doesn't have discard functionality."""
        pass

    @contextmanager
    def _stat_card(self, title: str) -> Generator[None, None, None]:
        """Compact metric card with title top-left and horizontal metrics row."""
        with ui.card().classes("statistics-metric-card w-full"):
            ui.label(title).classes(
                "font-semibold text-sm mb-1 w-full text-left shrink-0"
            )
            with ui.row().classes(
                "w-full gap-3 justify-start items-start flex-wrap"
            ):
                yield

    def _stat_value(
        self, text: str, caption: str, value_class: str = "text-theme-primary"
    ) -> Any:
        with ui.column().classes("items-start gap-0 shrink-0 min-w-[4.5rem]"):
            lbl = ui.label(text).classes(f"text-lg font-bold leading-tight {value_class}")
            ui.label(caption).classes("text-xs secondary-text text-left leading-tight")
            return lbl

    def _stat_footer(self, *lines: str) -> None:
        if not lines:
            return
        with ui.column().classes(
            "w-full items-start gap-0 mt-2 pt-2 "
            "border-t border-[var(--color-border-default)]"
        ):
            for i, line in enumerate(lines):
                cls = (
                    "text-xs secondary-text text-left"
                    if i
                    else "text-xs text-theme-primary-light text-left"
                )
                ui.label(line).classes(cls)

    # ----- building -----
    def build(self, parent_container) -> None:
        """Build the statistics tab UI inside parent_container."""
        with parent_container:
            self._build_statistics_dashboard()

    def _build_statistics_dashboard(self):
        """Build the statistics dashboard with elegant card-based layout"""
        try:
            # Clean up any existing references before building
            self._cleanup_live_updates()

            # Store reference to the container for refresh functionality
            self.statistics_container = ui.column().classes(
                "w-full statistics-dashboard"
            )

            with self.statistics_container:
                # Build the actual content
                self._rebuild_statistics_content()

                # Try to ensure periodic saving is running
                try:
                    stats_manager = get_statistics_manager()
                    stats_manager.ensure_periodic_saving()
                except Exception as e:
                    print(f"Could not ensure periodic saving on load: {e}")

            # Start live updates by default AFTER UI elements are created
            print("Starting live updates from dashboard build")
            self._start_live_updates()

        except Exception as e:
            logger.error(f"Error building statistics dashboard: {str(e)}", exc_info=True)
            with ui.card().classes(
                "content-section statistics-section w-full"
            ):
                ui.label("❌ Error Loading Statistics").classes(
                    "text-xl font-bold mb-4 text-red-400"
                )
                ui.label(
                    "Unable to load statistics at this time. Please try again later."
                ).classes("secondary-text")

    def _cleanup_live_updates(self):
        """Clean up live update references when dashboard is rebuilt"""
        # Clear UI element references to avoid memory leaks
        # Session labels
        self._session_duration_label = None
        self._last_save_label = None
        self._saving_status_label = None
        self._live_status_label = None
        self._live_toggle_button = None

        # Alert statistic labels
        self._bit_alerts_label = None
        self._total_bits_label = None
        self._resubs_label = None
        self._new_subs_label = None
        self._gift_subs_label = None
        self._total_gift_subs_label = None

        # Social statistic labels
        self._follow_alerts_label = None
        self._point_alerts_label = None
        self._total_channel_points_label = None
        self._twitch_messages_label = None

        # Hype train level labels (dictionary for dynamic levels)
        self._hype_train_labels: Dict[int, Any] = {}

        # Connector statistic labels
        self._connectors_created_label = None
        self._total_triggers_label = None
        self._connectors_triggered_label = None

        # Chatbot statistic labels
        self._commands_created_label = None
        self._events_created_label = None
        self._commands_triggered_label = None
        self._events_triggered_label = None
        self._giveaways_completed_label = None
        self._giveaways_entries_label = None
        self._giveaways_avg_label = None

        # Quote statistic labels
        self._quotes_created_label = None
        self._quotes_redeemed_label = None

        # Template statistic labels
        self._template_stat_labels = {}

        # Per-user section references
        self._user_search_input = None
        self._user_start_date = None
        self._user_end_date = None
        self._per_user_results_container = None
        self._selected_username = None

        # Export section references
        self._export_start_date = None
        self._export_end_date = None
        self._export_status_label = None

        # Initialize UI element references for live updates
        self._initialize_ui_references()

        # Initialize update counter for periodic full refreshes
        self._update_counter = 0

    def _initialize_ui_references(self):
        """Initialize UI element references for live updates"""
        # Session labels
        self._session_duration_label = None
        self._last_save_label = None
        self._saving_status_label = None
        self._live_status_label = None
        self._live_toggle_button = None

        # Alert statistic labels
        self._bit_alerts_label = None
        self._total_bits_label = None
        self._resubs_label = None
        self._new_subs_label = None
        self._gift_subs_label = None
        self._total_gift_subs_label = None

        # Social statistic labels
        self._follow_alerts_label = None
        self._point_alerts_label = None
        self._total_channel_points_label = None
        self._twitch_messages_label = None

        # Hype train level labels (dictionary for dynamic levels)
        self._hype_train_labels: Dict[int, Any] = {}

        # Connector statistic labels
        self._connectors_created_label = None
        self._total_triggers_label = None
        self._connectors_triggered_label = None

        # Chatbot statistic labels
        self._commands_created_label = None
        self._events_created_label = None
        self._commands_triggered_label = None
        self._events_triggered_label = None
        self._giveaways_completed_label = None
        self._giveaways_entries_label = None
        self._giveaways_avg_label = None

        # Quote statistic labels
        self._quotes_created_label = None
        self._quotes_redeemed_label = None

        # Template statistic labels (dict for dynamic templates)
        self._template_stat_labels = {}

        # Per-user section references
        self._user_search_input = None
        self._user_start_date = None
        self._user_end_date = None
        self._per_user_results_container = None

        # Export section references
        self._export_start_date = None
        self._export_end_date = None
        self._export_status_label = None

    @staticmethod
    def _normalize_date_str(s: str) -> str:
        """Reduce date inputs to YYYY-MM-DD (NiceGUI may return longer ISO strings)."""
        s = (s or "").strip()
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return s[:10]
        return s

    def _input_value_str(self, element) -> str:
        """Best-effort read of a NiceGUI input value as string."""
        if element is None:
            return ""
        v = getattr(element, "value", None)
        if v is None:
            return ""
        raw = str(v).strip()
        if self._looks_like_date_value(raw):
            return self._normalize_date_str(raw)
        return raw

    @staticmethod
    def _looks_like_date_value(s: str) -> bool:
        s = (s or "").strip()
        return len(s) >= 10 and s[4] == "-" and s[7] == "-"

    def _snapshot_statistics_inputs(self) -> Dict[str, str]:
        """Capture per-user and export date fields before a full dashboard rebuild."""
        return {
            "user_search": self._input_value_str(self._user_search_input),
            "user_start": self._input_value_str(self._user_start_date),
            "user_end": self._input_value_str(self._user_end_date),
            "export_start": self._input_value_str(self._export_start_date),
            "export_end": self._input_value_str(self._export_end_date),
        }

    def _restore_statistics_inputs(self, snap: Dict[str, str]) -> None:
        """Re-apply inputs after rebuild; re-run per-user search if a username was set."""
        if not snap:
            return
        try:
            if self._user_search_input and snap.get("user_search"):
                self._user_search_input.value = snap["user_search"]
            if self._user_start_date and snap.get("user_start"):
                self._user_start_date.value = snap["user_start"]
            if self._user_end_date and snap.get("user_end"):
                self._user_end_date.value = snap["user_end"]
            if self._export_start_date and snap.get("export_start"):
                self._export_start_date.value = snap["export_start"]
            if self._export_end_date and snap.get("export_end"):
                self._export_end_date.value = snap["export_end"]
            if snap.get("user_search"):
                self._on_user_search(show_notification=False)
        except Exception as e:
            print(f"Could not restore statistics tab inputs: {e}")

    def _refresh_statistics(self):
        """Refresh the statistics dashboard"""
        try:
            notify("🔄 Refreshing statistics...", type="info")

            # Clear the current container and rebuild
            if self.statistics_container is not None:
                self.statistics_container.clear()
                with self.statistics_container:
                    self._rebuild_statistics_content()
                notify("✅ Statistics refreshed!", type="positive")
            else:
                # If no container exists, rebuild the entire dashboard
                self._build_statistics_dashboard()
                notify("✅ Statistics dashboard rebuilt!", type="positive")

        except Exception as e:
            logger.error(f"Error refreshing statistics: {str(e)}", exc_info=True)
            notify("❌ Error refreshing statistics", type="negative")

    def _force_save_statistics(self):
        """Force an immediate save of statistics"""
        try:
            notify("Force saving statistics...", type="info")

            stats_manager = get_statistics_manager()
            saved_stats = stats_manager.force_save()
            notify("Statistics saved successfully!", type="positive")

        except Exception as e:
            logger.error(f"Error force saving statistics: {str(e)}", exc_info=True)
            notify("Error saving statistics", type="negative")

    def _debug_counts(self):
        """Debug current dynamic counts"""
        try:
            notify("🔍 Checking dynamic counts...", type="info")

            stats_manager = get_statistics_manager()

            # Test each count method directly
            commands_count = stats_manager._get_commands_count()
            events_count = stats_manager._get_events_count()
            quotes_count = stats_manager._get_quotes_count()
            connectors_count = stats_manager._get_connector_count()

            notify(
                f"Counts - Commands: {commands_count}, Events: {events_count}, Quotes: {quotes_count}, Connectors: {connectors_count}",
                type="info",
            )

            # Also refresh the statistics display
            self._refresh_statistics()

        except Exception as e:
            logger.error(f"Error debugging counts: {str(e)}", exc_info=True)
            notify("Error checking counts", type="negative")

    def _start_live_updates(self):
        """Start automatic live updates for the statistics dashboard"""
        # Check if timer is already running
        if self.live_update_timer and self.live_update_timer.active:
            return  # Already running

        # Only start live updates if we have the necessary UI elements
        if self.statistics_container is None:
            print("Not starting live updates - statistics container not yet created")
            return

        # Check if essential labels exist (they should be created by _rebuild_statistics_content)
        essential_labels_exist = (
            hasattr(self, "_session_duration_label")
            and self._session_duration_label is not None
        )

        if not essential_labels_exist:
            print("Not starting live updates - essential UI labels not yet created")
            return

        self.live_updates_enabled = True
        print(
            f"Starting live statistics updates every {self.live_update_interval} seconds"
        )

        def update_task():
            try:
                if (
                    self.live_updates_enabled
                    and self.statistics_container is not None
                    and not getattr(self.statistics_container, "is_deleted", False)
                ):
                    # Only update if the statistics tab is currently visible
                    # We'll do a lightweight update without clearing the entire container
                    self._update_statistics_display()
            except Exception as e:
                print(f"Error in live statistics update: {str(e)}")

        # Start the timer
        self.live_update_timer = layout_schedule(
            self.live_update_interval, update_task, active=True
        )
        print(
            f"Timer created and started: {self.live_update_timer} active={self.live_update_timer.active if self.live_update_timer else 'None'}"
        )

    def _stop_live_updates(self):
        """Stop automatic live updates for the statistics dashboard"""
        self.live_updates_enabled = False
        if self.live_update_timer:
            self.live_update_timer.active = False
            self.live_update_timer = None
        print("Stopped live statistics updates")

    def _toggle_live_updates(self):
        """Toggle live updates on/off"""
        if self.live_updates_enabled:
            self._stop_live_updates()
            notify(" Live updates disabled", type="info")
            # Update button text if it exists
            if hasattr(self, "_live_toggle_button") and self._live_toggle_button:
                self._live_toggle_button.set_text(" Live Updates")
        else:
            self._start_live_updates()
            notify(" Live updates enabled", type="positive")
            # Update button text if it exists
            if hasattr(self, "_live_toggle_button") and self._live_toggle_button:
                self._live_toggle_button.set_text("⏸️ Stop Live")

    def _update_statistics_display(self):
        """Lightweight update of statistics display values without rebuilding UI structure"""
        try:
            # print("Live update triggered")
            # Get fresh statistics data
            stats_manager = get_statistics_manager()
            stats_data = stats_manager.get_all_statistics()
            # print(f"Got statistics data: {stats_data is not None}")

            if not stats_data:
                print("No statistics data available, skipping update")
                return

            # Occasionally do a full refresh to update dynamic content (top items/users)
            # This is done every 10th update to keep performance good while ensuring
            # the dynamic content stays current
            if not hasattr(self, "_update_counter"):
                self._update_counter = 0
            self._update_counter += 1

            interval = max(1, getattr(self, "_live_full_refresh_every_n", 36))
            if self._update_counter % interval == 0:
                print("Performing full statistics refresh for dynamic content")
                # Clear the container and rebuild within proper context
                if (
                    hasattr(self, "statistics_container")
                    and self.statistics_container
                    and hasattr(self.statistics_container, "clear")
                ):
                    try:
                        snap = self._snapshot_statistics_inputs()
                        # Clean up existing references before rebuilding
                        self._cleanup_live_updates()
                        # Ensure container is properly cleared
                        self.statistics_container.clear()
                        # Verify container is empty before rebuilding
                        print("Container cleared, preparing to rebuild")
                        # Rebuild content within the container context
                        with self.statistics_container:
                            self._rebuild_statistics_content()
                        self._restore_statistics_inputs(snap)
                        print("Full statistics refresh completed successfully")
                    except Exception as e:
                        print(f"Error during full statistics refresh: {e}")
                        # Reset counter to avoid repeated failures
                        self._update_counter = 0
                return

            # Update session duration and last save time
            try:
                if (
                    hasattr(self, "_session_duration_label")
                    and self._session_duration_label
                ):
                    session_duration = time.time() - stats_data["session"]["start_time"]
                    hours = int(session_duration // 3600)
                    minutes = int((session_duration % 3600) // 60)
                    seconds = int(session_duration % 60)
                    new_time = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
                    self._session_duration_label.set_text(new_time)
                    # print(f"Updated session duration to: {new_time}")
                else:
                    print("Session duration label not found")
            except Exception as e:
                print(f"Could not update session duration: {e}")

            try:
                if hasattr(self, "_last_save_label") and self._last_save_label:
                    if stats_data["session"]["last_save_time"]:
                        last_save = datetime.fromtimestamp(
                            stats_data["session"]["last_save_time"]
                        )
                        new_save_time = last_save.strftime("%H:%M:%S")
                        self._last_save_label.set_text(new_save_time)
                        # print(f"Updated last save time to: {new_save_time}")
                    else:
                        self._last_save_label.set_text("Never")
                else:
                    print("Last save label not found")
            except Exception as e:
                print(f"Could not update last save time: {e}")

            # Update individual statistic labels if they exist
            try:
                self._update_statistic_labels(stats_data)
            except Exception as e:
                print(f"Could not update statistic labels: {e}")

        except Exception as e:
            logger.error(f"Error updating statistics display: {str(e)}", exc_info=True)

    def _update_statistic_labels(self, stats_data):
        """Update individual statistic value labels"""
        try:
            # Update Alert Statistics
            try:
                if hasattr(self, "_bit_alerts_label") and self._bit_alerts_label:
                    self._bit_alerts_label.set_text(
                        f"{stats_data['alerts']['bit_alerts_played']:,}"
                    )
            except Exception as e:
                print(f"Could not update bit alerts: {e}")

            try:
                if hasattr(self, "_total_bits_label") and self._total_bits_label:
                    self._total_bits_label.set_text(
                        f"{stats_data['alerts']['total_bits']:,}"
                    )
            except Exception as e:
                print(f"Could not update total bits: {e}")

            try:
                if hasattr(self, "_resubs_label") and self._resubs_label:
                    self._resubs_label.set_text(
                        f"{stats_data['alerts']['resubs_played']:,}"
                    )
            except Exception as e:
                print(f"Could not update resubs: {e}")

            try:
                if hasattr(self, "_new_subs_label") and self._new_subs_label:
                    self._new_subs_label.set_text(
                        f"{stats_data['alerts']['new_subs_played']:,}"
                    )
            except Exception as e:
                print(f"Could not update new subs: {e}")

            try:
                if hasattr(self, "_gift_subs_label") and self._gift_subs_label:
                    self._gift_subs_label.set_text(
                        f"{stats_data['alerts']['gift_subs_played']:,}"
                    )
            except Exception as e:
                print(f"Could not update gift subs: {e}")

            try:
                if (
                    hasattr(self, "_total_gift_subs_label")
                    and self._total_gift_subs_label
                ):
                    self._total_gift_subs_label.set_text(
                        f"{stats_data['alerts']['total_gift_subs']:,}"
                    )
            except Exception as e:
                print(f"Could not update total gift subs: {e}")

            # Update Social Statistics
            try:
                if hasattr(self, "_follow_alerts_label") and self._follow_alerts_label:
                    self._follow_alerts_label.set_text(
                        f"{stats_data['alerts']['follow_alerts_played']:,}"
                    )
            except Exception as e:
                print(f"Could not update follow alerts: {e}")

            try:
                if hasattr(self, "_point_alerts_label") and self._point_alerts_label:
                    self._point_alerts_label.set_text(
                        f"{stats_data['alerts']['point_alerts_redeemed']:,}"
                    )
            except Exception as e:
                print(f"Could not update point alerts: {e}")

            try:
                if (
                    hasattr(self, "_total_channel_points_label")
                    and self._total_channel_points_label
                ):
                    self._total_channel_points_label.set_text(
                        f"{stats_data['alerts']['total_channel_points_redeemed']:,}"
                    )
            except Exception as e:
                print(f"Could not update total channel points: {e}")

            try:
                if (
                    hasattr(self, "_twitch_messages_label")
                    and self._twitch_messages_label
                ):
                    self._twitch_messages_label.set_text(
                        f"{stats_data['chat']['twitch_messages_received']:,}"
                    )
            except Exception as e:
                print(f"Could not update twitch messages: {e}")

            # Update Hype Train Statistics
            try:
                if hasattr(self, "_hype_train_labels") and self._hype_train_labels:
                    # Check for new format first, fallback to old format
                    raw_level_completions = stats_data["hype_trains"].get(
                        "level_completions", {}
                    )

                    # Convert string keys to integers (JSON serialization converts int keys to strings)
                    level_completions = {}
                    # Always process level_completions, even if it's an empty dict
                    for k, v in raw_level_completions.items():
                        try:
                            level_completions[int(k)] = v
                        except (ValueError, TypeError):
                            pass

                    # If level_completions is empty or missing, initialize with zeros for levels 1-5
                    if not level_completions:
                        level_completions = {level: 0 for level in range(1, 6)}

                    # Update only labels that exist in our dictionary
                    for level, label in self._hype_train_labels.items():
                        if label:
                            count = level_completions.get(level, 0)
                            label.set_text(f"{count:,}")
                else:
                    print("Hype train labels not initialized")
            except Exception as e:
                import traceback

                traceback.print_exc()

            # Update Connector Statistics
            try:
                if (
                    hasattr(self, "_connectors_created_label")
                    and self._connectors_created_label
                ):
                    connectors_created = stats_data["connectors"]["connectors_created"]
                    self._connectors_created_label.set_text(f"{connectors_created:,}")
                    # print(f" UI: Updated connectors created to: {connectors_created}")
            except Exception as e:
                print(f"Could not update connectors created: {e}")

            try:
                if (
                    hasattr(self, "_total_triggers_label")
                    and self._total_triggers_label
                ):
                    self._total_triggers_label.set_text(
                        f"{stats_data['connectors']['total_triggers']:,}"
                    )
            except Exception as e:
                print(f"Could not update total triggers: {e}")

            try:
                if (
                    hasattr(self, "_connectors_triggered_label")
                    and self._connectors_triggered_label
                ):
                    self._connectors_triggered_label.set_text(
                        f"{stats_data['connectors']['connectors_triggered']:,}"
                    )
            except Exception as e:
                print(f"Could not update connectors triggered: {e}")

            # Update Chatbot Statistics
            try:
                if (
                    hasattr(self, "_commands_created_label")
                    and self._commands_created_label
                ):
                    self._commands_created_label.set_text(
                        f"{stats_data['chatbot']['commands_created']:,}"
                    )
            except Exception as e:
                print(f"Could not update commands created: {e}")

            try:
                if (
                    hasattr(self, "_events_created_label")
                    and self._events_created_label
                ):
                    self._events_created_label.set_text(
                        f"{stats_data['chatbot']['events_created']:,}"
                    )
            except Exception as e:
                print(f"Could not update events created: {e}")

            try:
                if (
                    hasattr(self, "_commands_triggered_label")
                    and self._commands_triggered_label
                ):
                    self._commands_triggered_label.set_text(
                        f"{stats_data['chatbot']['commands_triggered']:,}"
                    )
            except Exception as e:
                print(f"Could not update commands triggered: {e}")

            try:
                if (
                    hasattr(self, "_events_triggered_label")
                    and self._events_triggered_label
                ):
                    self._events_triggered_label.set_text(
                        f"{stats_data['chatbot']['events_triggered']:,}"
                    )
            except Exception as e:
                print(f"Could not update events triggered: {e}")

            try:
                gw = stats_data.get("giveaways", {}) or {}
                if (
                    hasattr(self, "_giveaways_completed_label")
                    and self._giveaways_completed_label
                ):
                    self._giveaways_completed_label.set_text(
                        f"{int(gw.get('giveaways_completed', 0) or 0):,}"
                    )
                if (
                    hasattr(self, "_giveaways_entries_label")
                    and self._giveaways_entries_label
                ):
                    self._giveaways_entries_label.set_text(
                        f"{int(gw.get('total_entry_events', 0) or 0):,}"
                    )
                if hasattr(self, "_giveaways_avg_label") and self._giveaways_avg_label:
                    avg_gw = float(gw.get("average_entries_per_giveaway", 0) or 0)
                    self._giveaways_avg_label.set_text(f"{avg_gw:.2f}")
            except Exception as e:
                print(f"Could not update giveaway statistics: {e}")

            # Update Quote Statistics
            try:
                if (
                    hasattr(self, "_quotes_created_label")
                    and self._quotes_created_label
                ):
                    self._quotes_created_label.set_text(
                        f"{stats_data['quotes']['quotes_created']:,}"
                    )
            except Exception as e:
                print(f"Could not update quotes created: {e}")

            try:
                if (
                    hasattr(self, "_quotes_redeemed_label")
                    and self._quotes_redeemed_label
                ):
                    self._quotes_redeemed_label.set_text(
                        f"{stats_data['quotes']['total_quotes_redeemed']:,}"
                    )
            except Exception as e:
                print(f"Could not update quotes redeemed: {e}")

            # Update Template Statistics
            try:
                if (
                    hasattr(self, "_template_stat_labels")
                    and self._template_stat_labels
                ):
                    for (
                        template_name,
                        template_labels,
                    ) in self._template_stat_labels.items():
                        if template_name in stats_data["templates"]:
                            template_stats = stats_data["templates"][template_name]
                            if "custom_stats" in template_stats:
                                for stat_name, label in template_labels.items():
                                    if stat_name in template_stats["custom_stats"]:
                                        stat_value = template_stats["custom_stats"][
                                            stat_name
                                        ]
                                        label.set_text(f"{stat_value:,}")
            except Exception as e:
                print(f"Could not update template stats: {e}")

        except Exception as e:
            logger.error(f"Error updating statistic labels: {str(e)}", exc_info=True)

    def _rebuild_statistics_content(self):
        """Rebuild the statistics dashboard content without recreating the container"""
        try:
            # Get fresh statistics data
            stats_manager = get_statistics_manager()
            stats_data = stats_manager.get_all_statistics()

            # Alert Statistics Section
            with ui.card().classes("content-section statistics-section w-full"):
                ui.label("🎯 Alert Statistics").classes("text-base font-bold mb-2")

                with ui.grid(columns=4).classes("w-full gap-2"):
                    with self._stat_card("⚡ Bits"):
                        self._bit_alerts_label = self._stat_value(
                            f"{stats_data['alerts']['bit_alerts_played']:,}",
                            "Alerts played",
                            "text-theme-primary",
                        )
                        self._total_bits_label = self._stat_value(
                            f"{stats_data['alerts']['total_bits']:,}",
                            "Total bits",
                            "text-theme-primary-light",
                        )

                    with self._stat_card("🔄 Resubs"):
                        self._resubs_label = self._stat_value(
                            f"{stats_data['alerts']['resubs_played']:,}",
                            "Total played",
                            "text-blue-400",
                        )

                    with self._stat_card("🆕 New Subs"):
                        self._new_subs_label = self._stat_value(
                            f"{stats_data['alerts']['new_subs_played']:,}",
                            "Total played",
                            "text-green-400",
                        )

                    with self._stat_card("🎁 Gift Subs"):
                        self._gift_subs_label = self._stat_value(
                            f"{stats_data['alerts']['gift_subs_played']:,}",
                            "Alerts played",
                            "text-pink-400",
                        )
                        self._total_gift_subs_label = self._stat_value(
                            f"{stats_data['alerts']['total_gift_subs']:,}",
                            "Total subs",
                            "text-pink-300",
                        )

            # Follow & Point Statistics Section
            with ui.card().classes("content-section statistics-section w-full"):
                ui.label("👥 Social & Interaction Statistics").classes(
                    "text-base font-bold mb-2"
                )

                with ui.grid(columns=3).classes("w-full gap-2"):
                    with self._stat_card("👤 Follow Alerts"):
                        self._follow_alerts_label = self._stat_value(
                            f"{stats_data['alerts']['follow_alerts_played']:,}",
                            "Total played",
                            "text-cyan-400",
                        )

                    with self._stat_card("🎯 Point Alerts"):
                        self._point_alerts_label = self._stat_value(
                            f"{stats_data['alerts']['point_alerts_redeemed']:,}",
                            "Redeemed",
                            "text-orange-400",
                        )
                        self._total_channel_points_label = self._stat_value(
                            f"{stats_data['alerts']['total_channel_points_redeemed']:,}",
                            "Points spent",
                            "text-orange-300",
                        )

                    with self._stat_card("💬 Chat Messages"):
                        self._twitch_messages_label = self._stat_value(
                            f"{stats_data['chat']['twitch_messages_received']:,}",
                            "Total received",
                            "text-indigo-400",
                        )

            # Hype Train Statistics Section
            # Check for new format first, fallback to old format for backwards compatibility
            raw_level_completions = stats_data["hype_trains"].get(
                "level_completions", {}
            )

            # Convert string keys to integers (JSON serialization converts int keys to strings)
            level_completions = {}
            if raw_level_completions:
                for k, v in raw_level_completions.items():
                    try:
                        level_completions[int(k)] = v
                    except (ValueError, TypeError):
                        pass

            if not level_completions:
                # Fallback to old format if new format doesn't exist
                level_completions = {
                    level: stats_data["hype_trains"].get(
                        f"level_{level}_completions", 0
                    )
                    for level in range(1, 6)
                }

            # Always show at least levels 1-5, plus any higher levels that exist
            max_level = max(level_completions.keys()) if level_completions else 5
            max_level = max(max_level, 5)  # Ensure we always show at least 5 levels

            # Build complete level data (including zeros for display)
            all_levels = {
                level: level_completions.get(level, 0)
                for level in range(1, max_level + 1)
            }

            # Always show the hype train section
            with ui.card().classes(
                "content-section statistics-section statistics-section-full w-full"
            ):
                ui.label("🚂 Hype Train Statistics").classes("text-base font-bold mb-2")

                with ui.grid(columns=5).classes("w-full gap-2"):
                    for level in sorted(all_levels.keys()):
                        count = all_levels[level]
                        with self._stat_card(f"Level {level}"):
                            self._hype_train_labels[level] = self._stat_value(
                                f"{count:,}",
                                "Completed",
                                "text-yellow-400",
                            )

            # Connector Statistics Section
            with ui.card().classes("content-section statistics-section w-full"):
                ui.label("🔗 Connector Statistics").classes("text-base font-bold mb-2")

                # Get connector insights
                top_connector = stats_manager.get_top_connectors(limit=1)
                top_connector_user = stats_manager.get_top_users_by_statistic(
                    "connector_triggers", limit=1
                )

                with ui.grid(columns=3).classes("w-full gap-2"):
                    with self._stat_card("📦 Total Connectors"):
                        self._connectors_created_label = self._stat_value(
                            f"{stats_data['connectors']['connectors_created']:,}",
                            "Created",
                            "text-emerald-400",
                        )
                        if top_connector:
                            cn = top_connector[0]["connector_name"]
                            cc = top_connector[0]["trigger_count"]
                            self._stat_footer(
                                f"Most used: {cn}",
                                f"({cc:,} triggers)",
                            )

                    with self._stat_card("⚡ Total Triggers"):
                        self._total_triggers_label = self._stat_value(
                            f"{stats_data['connectors']['total_triggers']:,}",
                            "All executions",
                            "text-green-400",
                        )
                        if top_connector_user:
                            un = top_connector_user[0]["username"]
                            uc = top_connector_user[0]["value"]
                            self._stat_footer(
                                f"Top user: {un}",
                                f"({uc:,} triggers)",
                            )

                    with self._stat_card("🔗 Unique Triggered"):
                        self._connectors_triggered_label = self._stat_value(
                            f"{stats_data['connectors']['connectors_triggered']:,}",
                            "Unique runs",
                            "text-red-400",
                        )

            # Quote Statistics Section
            with ui.card().classes("content-section statistics-section w-full"):
                ui.label("💬 Quote Statistics").classes("text-base font-bold mb-2")

                top_quote_user = stats_manager.get_top_users_by_statistic(
                    "quotes_redeemed", limit=1
                )

                with ui.grid(columns=2).classes("w-full gap-2"):
                    with self._stat_card("📚 Total Quotes"):
                        self._quotes_created_label = self._stat_value(
                            f"{stats_data['quotes']['quotes_created']:,}",
                            "In database",
                            "text-indigo-400",
                        )
                        individual_quote_usage = stats_data["quotes"].get(
                            "individual_quote_usage", {}
                        )
                        if individual_quote_usage:
                            quote_id, quote_count = max(
                                individual_quote_usage.items(), key=lambda x: x[1]
                            )
                            self._stat_footer(
                                f"Most redeemed: {quote_id}",
                                f"({quote_count:,} redemptions)",
                            )

                    with self._stat_card("🎯 Quotes Redeemed"):
                        self._quotes_redeemed_label = self._stat_value(
                            f"{stats_data['quotes']['total_quotes_redeemed']:,}",
                            "Total uses",
                            "text-pink-400",
                        )
                        if top_quote_user:
                            user_name = top_quote_user[0]["username"]
                            user_count = top_quote_user[0]["value"]
                            self._stat_footer(
                                f"Top user: {user_name}",
                                f"({user_count:,} redemptions)",
                            )

            # Chatbot Statistics Section
            with ui.card().classes(
                "content-section statistics-section statistics-section-full w-full"
            ):
                ui.label("🤖 Chatbot Statistics").classes("text-base font-bold mb-2")

                # Get chatbot insights
                top_command = stats_manager.get_top_commands(limit=1)
                top_event = stats_manager.get_top_events(limit=1)
                top_command_user = stats_manager.get_top_users_by_statistic(
                    "chatbot_interactions", limit=1
                )

                with ui.grid(columns=4).classes("w-full gap-2"):
                    with self._stat_card("📝 Commands"):
                        self._commands_created_label = self._stat_value(
                            f"{stats_data['chatbot']['commands_created']:,}",
                            "Created",
                            "text-theme-primary",
                        )
                        if top_command:
                            command_name = top_command[0]["command_name"]
                            command_count = top_command[0]["usage_count"]
                            self._stat_footer(
                                f"Most used: {command_name}",
                                f"({command_count:,} uses)",
                            )

                    with self._stat_card("🎪 Events"):
                        self._events_created_label = self._stat_value(
                            f"{stats_data['chatbot']['events_created']:,}",
                            "Created",
                            "text-blue-400",
                        )
                        if top_event:
                            event_name = top_event[0]["event_name"]
                            event_count = top_event[0]["trigger_count"]
                            self._stat_footer(
                                f"Most used: {event_name}",
                                f"({event_count:,} triggers)",
                            )

                    with self._stat_card("⚡ Commands Used"):
                        self._commands_triggered_label = self._stat_value(
                            f"{stats_data['chatbot']['commands_triggered']:,}",
                            "Executions",
                            "text-green-400",
                        )
                        if top_command_user:
                            user_name = top_command_user[0]["username"]
                            user_count = top_command_user[0]["value"]
                            self._stat_footer(
                                f"Top user: {user_name}",
                                f"({user_count:,} interactions)",
                            )

                    with self._stat_card("🎯 Events Triggered"):
                        self._events_triggered_label = self._stat_value(
                            f"{stats_data['chatbot']['events_triggered']:,}",
                            "Executions",
                            "text-orange-400",
                        )

                gw = stats_data.get("giveaways", {}) or {}
                ui.label("🎁 Giveaways").classes("text-sm font-semibold mb-1 mt-2")
                with ui.grid(columns=3).classes("w-full gap-2"):
                    with self._stat_card("Draws completed"):
                        self._giveaways_completed_label = self._stat_value(
                            f"{int(gw.get('giveaways_completed', 0) or 0):,}",
                            "Draw clicks",
                            "text-pink-400",
                        )
                    with self._stat_card("Total entries"):
                        self._giveaways_entries_label = self._stat_value(
                            f"{int(gw.get('total_entry_events', 0) or 0):,}",
                            "Keyword matches",
                            "text-rose-400",
                        )
                    avg_gw = float(gw.get("average_entries_per_giveaway", 0) or 0)
                    with self._stat_card("Avg / giveaway"):
                        self._giveaways_avg_label = self._stat_value(
                            f"{avg_gw:.2f}",
                            "Per draw",
                            "text-fuchsia-400",
                        )

            # Template Statistics Section (if any templates have stats)
            if stats_data["templates"]:
                with ui.card().classes("content-section statistics-section w-full"):
                    ui.label("🎨 Template Statistics").classes(
                        "text-base font-bold mb-2"
                    )

                    for template_name, template_stats in stats_data[
                        "templates"
                    ].items():
                        with ui.card().classes("settings-card mb-4"):
                            ui.label(f"📄 {template_name}").classes(
                                "text-lg font-semibold mb-3"
                            )

                            if template_stats["custom_stats"]:
                                # Initialize template entry in the labels dict
                                if template_name not in self._template_stat_labels:
                                    self._template_stat_labels[template_name] = {}

                                with ui.grid(columns=3).classes("w-full gap-2"):
                                    for stat_name, stat_value in template_stats[
                                        "custom_stats"
                                    ].items():
                                        with ui.card().classes(
                                            "text-center p-2 statistics-stat-cell rounded"
                                        ):
                                            ui.label(
                                                stat_name.replace("_", " ").title()
                                            ).classes("font-semibold mb-1 text-sm")
                                            self._template_stat_labels[template_name][
                                                stat_name
                                            ] = ui.label(f"{stat_value:,}").classes(
                                                "text-xl font-bold text-theme-primary"
                                            )
                            else:
                                ui.label("No custom statistics recorded yet").classes(
                                    "secondary-text italic"
                                )

            # Per-User Statistics Section
            self._build_per_user_section(stats_manager)

            # Export Highlights Section
            self._build_export_section()

        except Exception as e:
            logger.error(f"Error rebuilding statistics content: {str(e)}", exc_info=True)
            with ui.card().classes(
                "content-section statistics-section w-full"
            ):
                ui.label("❌ Error Loading Statistics").classes(
                    "text-xl font-bold mb-4 text-red-400"
                )
                ui.label(
                    "Unable to load statistics at this time. Please try again later."
                ).classes("secondary-text")

    # ---- Per-User Statistics Section ----

    def _build_per_user_section(self, stats_manager):
        """Build the per-user statistics lookup section."""
        with ui.card().classes(
            "content-section statistics-section statistics-section-full w-full"
        ):
            ui.label("👤 Per-User Statistics").classes("text-xl font-bold mb-4")
            ui.label(
                "Date range shows the timestamped event log (alerts, chat messages, connectors, "
                "chatbot, quotes, giveaways, etc.). Lifetime totals below are all-time saved aggregates."
            ).classes("secondary-text mb-4")

            with ui.row().classes("w-full gap-4 items-end mb-4"):
                # Username search with autocomplete
                known_users = stats_manager.get_all_tracked_usernames()
                self._user_search_input = form_input(
                    tooltip="Twitch username to look up in saved statistics",
                    label="🔍 Search username",
                    placeholder="Type a username...",
                    classes="flex-1",
                    autocomplete=known_users,
                )

                today = datetime.now()
                thirty_days_ago = today - timedelta(days=30)

                self._user_start_date = form_input(
                    tooltip="Start of the date range for per-user event statistics",
                    label="Start date",
                    value=thirty_days_ago.strftime("%Y-%m-%d"),
                    classes="w-40",
                )
                self._user_start_date.props("type=date")

                self._user_end_date = form_input(
                    tooltip="End of the date range for per-user event statistics",
                    label="End date",
                    value=today.strftime("%Y-%m-%d"),
                    classes="w-40",
                )
                self._user_end_date.props("type=date")

                ui.button(
                    "Search",
                    on_click=lambda: self._on_user_search(),
                ).classes(
                    "btn-primary px-4 py-2 rounded-lg font-semibold"
                )

            # Results container
            self._per_user_results_container = ui.column().classes("w-full gap-4")
            with self._per_user_results_container:
                with ui.card().classes("settings-card w-full text-center p-6"):
                    ui.label("Enter a username and click Search to view their stats.").classes(
                        "secondary-text italic"
                    )

    def _on_user_search(self, show_notification: bool = True):
        """Handle user search: fetch and display per-user stats for the selected date range."""
        try:
            username = (
                self._user_search_input.value.strip()
                if self._user_search_input and self._user_search_input.value
                else ""
            )
            if not username:
                if show_notification:
                    notify("Please enter a username.", type="warning")
                return

            self._selected_username = username

            # Parse date range
            start_str = self._normalize_date_str(
                str(self._user_start_date.value or "") if self._user_start_date else ""
            )
            end_str = self._normalize_date_str(
                str(self._user_end_date.value or "") if self._user_end_date else ""
            )

            try:
                start_dt = datetime.strptime(start_str, "%Y-%m-%d") if start_str else datetime.now() - timedelta(days=30)
                end_dt = datetime.strptime(end_str, "%Y-%m-%d") if end_str else datetime.now()
                # End of day (inclusive of last moment, matches export / SQLite range)
                end_dt = end_dt.replace(
                    hour=23, minute=59, second=59, microsecond=999999
                )
            except ValueError:
                if show_notification:
                    notify("Invalid date format. Use YYYY-MM-DD.", type="negative")
                return

            start_ts = start_dt.timestamp()
            end_ts = end_dt.timestamp()

            stats_manager = get_statistics_manager()

            # Get events from the timestamped database
            events = stats_manager.get_user_events(
                username, start_time=start_ts, end_time=end_ts, limit=200
            )

            # Also get the lifetime aggregate stats for context
            user_stats = stats_manager.get_user_statistics(username)

            # Aggregate the timestamped events by type for the date range
            range_counts: Dict[str, int] = {}
            range_totals: Dict[str, float] = {}
            for ev in events:
                etype = ev.get("event_type", "unknown")
                range_counts[etype] = range_counts.get(etype, 0) + 1
                amt = float(ev.get("amount", 0) or 0)
                if etype == "watch_streak":
                    range_totals[etype] = max(range_totals.get(etype, 0), amt)
                else:
                    range_totals[etype] = range_totals.get(etype, 0) + amt

            # Build UI
            if self._per_user_results_container:
                self._per_user_results_container.clear()
                with self._per_user_results_container:
                    self._render_per_user_results(
                        username, start_dt, end_dt, events, range_counts, range_totals, user_stats
                    )

            if show_notification:
                notify(f"Found {len(events)} events for {username}.", type="positive")

        except Exception as e:
            print(f"Error in user search: {e}")
            if show_notification:
                notify("Error searching user statistics.", type="negative")

    def _render_per_user_results(
        self,
        username: str,
        start_dt: datetime,
        end_dt: datetime,
        events: List[Dict[str, Any]],
        range_counts: Dict[str, int],
        range_totals: Dict[str, float],
        user_stats: Dict[str, Any],
    ):
        """Render per-user results cards inside the results container."""
        display_name = user_stats.get("username") or username
        has_lifetime = any(
            bool(user_stats.get(k))
            for k in ("alerts", "connectors", "chatbot", "quotes", "giveaways", "chat")
        )

        # Header
        with ui.card().classes("settings-card w-full p-4"):
            with ui.row().classes("w-full justify-between items-center"):
                ui.label(f"📊 Stats for {display_name}").classes("text-lg font-bold text-theme-primary-light")
                date_range_str = f"{start_dt.strftime('%b %d, %Y')} - {end_dt.strftime('%b %d, %Y')}"
                ui.label(date_range_str).classes("text-sm secondary-text")

        # Date range event counts (SQLite event log)
        with ui.card().classes("settings-card w-full p-4"):
            ui.label("Date range (event log)").classes("font-semibold mb-1")
            ui.label(
                "Each row is a timestamped event: alerts, chat messages, connectors, chatbot, "
                "quotes, giveaways, and more."
            ).classes("text-xs secondary-text mb-3")

            if not events:
                ui.label("No events recorded in this date range.").classes(
                    "secondary-text italic"
                )
                if user_stats.get("chat") and has_lifetime:
                    ui.label(
                        "Lifetime chat totals below may include messages from before per-message "
                        "logging or from periods outside this range."
                    ).classes("text-xs secondary-text mt-2")
            else:
                event_config = [
                    ("bit", "⚡ Bits", "text-theme-primary", "total bits"),
                    ("sub", "🔄 Subs", "text-blue-400", "events"),
                    ("giftsub", "🎁 Gift Subs", "text-pink-400", "total gifted"),
                    ("donation", "💰 Donations", "text-green-400", "events"),
                    ("point_redeem", "🎯 Point Redeems", "text-orange-400", "total points"),
                    ("follow", "👤 Follows", "text-cyan-400", "events"),
                    ("watch_streak", "🔥 Watch streaks", "text-amber-400", "peak streak"),
                    ("raid", "🛡️ Raids", "text-yellow-400", "events"),
                    ("connector", "🔗 Connectors", "text-emerald-400", "trigger weight"),
                    ("chatbot_command", "🤖 Commands", "text-theme-primary", "uses"),
                    ("chatbot_event", "🎪 Chatbot events", "text-blue-300", "uses"),
                    ("quote_redeem", "💬 Quote redeems", "text-pink-300", "uses"),
                    ("chat_message", "📨 Chat messages", "text-indigo-400", "messages"),
                    ("giveaway_entry", "🎁 Giveaway entries", "text-rose-400", "entries"),
                    ("giveaway_win", "🏆 Giveaway wins", "text-fuchsia-400", "wins"),
                ]
                # Show zero-count placeholders only for core alert types (legacy UI behavior).
                show_if_zero = ("bit", "sub", "giftsub", "donation", "point_redeem")

                with ui.grid(columns=4).classes("w-full gap-4"):
                    for etype, label, color_cls, amount_label in event_config:
                        count = range_counts.get(etype, 0)
                        total = range_totals.get(etype, 0)
                        if count > 0 or etype in show_if_zero:
                            with ui.card().classes("settings-card text-center p-3"):
                                ui.label(label).classes("font-semibold mb-1 text-sm")
                                ui.label(f"{count:,}").classes(
                                    f"text-xl font-bold {color_cls}"
                                )
                                ui.label("Events").classes("text-xs secondary-text")
                                if total > 0:
                                    ui.label(f"{int(total):,}").classes(
                                        f"text-lg font-semibold {color_cls} mt-1"
                                    )
                                    ui.label(amount_label).classes("text-xs secondary-text")

        # Lifetime totals (JSON-backed categories)
        if has_lifetime:
            with ui.card().classes("settings-card w-full p-4"):
                ui.label("Lifetime totals (all time)").classes("font-semibold mb-3")

                alert_stats = user_stats.get("alerts") or {}
                if alert_stats:
                    ui.label("Alerts").classes("text-sm font-semibold mb-2 text-theme-primary-light")
                    with ui.grid(columns=5).classes("w-full gap-3"):
                        lifetime_items = [
                            ("Bit Alerts", alert_stats.get("bit_alerts_played", 0), "text-theme-primary"),
                            ("Resubs", alert_stats.get("resubs_played", 0), "text-blue-400"),
                            ("New Subs", alert_stats.get("new_subs_played", 0), "text-green-400"),
                            ("Gift Subs", alert_stats.get("gift_subs_played", 0), "text-pink-400"),
                            ("Donations", alert_stats.get("donations", 0), "text-emerald-400"),
                            ("Follow Alerts", alert_stats.get("follow_alerts_played", 0), "text-cyan-400"),
                            ("Watch streak alerts", alert_stats.get("watch_streak_alerts_played", 0), "text-amber-400"),
                            ("Highest streak (lifetime)", alert_stats.get("highest_watch_streak", 0), "text-orange-300"),
                            ("Raids", alert_stats.get("raids", 0), "text-yellow-400"),
                            ("Point Alerts", alert_stats.get("point_alerts_redeemed", 0), "text-orange-400"),
                            ("Total Alerts", alert_stats.get("total_alerts", 0), "text-theme-primary"),
                        ]
                        for label, value, color in lifetime_items:
                            with ui.card().classes("text-center p-2 statistics-stat-cell rounded"):
                                ui.label(label).classes("text-xs secondary-text mb-1")
                                ui.label(f"{value:,}").classes(f"text-lg font-bold {color}")

                    first_seen = alert_stats.get("first_seen")
                    last_seen = alert_stats.get("last_seen")
                    if first_seen:
                        with ui.row().classes("gap-4 mt-2"):
                            ui.label(
                                f"First seen: {datetime.fromtimestamp(first_seen).strftime('%b %d, %Y %H:%M')}"
                            ).classes("text-xs secondary-text")
                            if last_seen:
                                ui.label(
                                    f"Last seen: {datetime.fromtimestamp(last_seen).strftime('%b %d, %Y %H:%M')}"
                                ).classes("text-xs secondary-text")

                chat_stats = user_stats.get("chat") or {}
                if chat_stats:
                    ui.label("Chat").classes("text-sm font-semibold mb-2 mt-4 text-indigo-300")
                    with ui.grid(columns=4).classes("w-full gap-3"):
                        with ui.card().classes("text-center p-2 statistics-stat-cell rounded"):
                            ui.label("Twitch messages").classes("text-xs secondary-text mb-1")
                            ui.label(f"{int(chat_stats.get('twitch_messages_received', 0) or 0):,}").classes(
                                "text-lg font-bold text-indigo-400"
                            )
                        with ui.card().classes("text-center p-2 statistics-stat-cell rounded"):
                            ui.label("Total messages").classes("text-xs secondary-text mb-1")
                            ui.label(f"{int(chat_stats.get('total_messages', 0) or 0):,}").classes(
                                "text-lg font-bold text-indigo-300"
                            )
                        fs = chat_stats.get("first_seen")
                        ls = chat_stats.get("last_seen")
                        if fs:
                            with ui.card().classes("text-center p-2 statistics-stat-cell rounded"):
                                ui.label("Activity window").classes("text-xs secondary-text mb-1")
                                fs_s = datetime.fromtimestamp(fs).strftime("%b %d, %Y %H:%M")
                                ls_s = (
                                    datetime.fromtimestamp(ls).strftime("%b %d, %Y %H:%M")
                                    if ls
                                    else "—"
                                )
                                ui.label(f"{fs_s} → {ls_s}").classes("text-xs secondary-text")

                conn_stats = user_stats.get("connectors") or {}
                if conn_stats:
                    ui.label("Connectors").classes("text-sm font-semibold mb-2 mt-4 text-emerald-300")
                    with ui.grid(columns=3).classes("w-full gap-3"):
                        with ui.card().classes("text-center p-2 statistics-stat-cell rounded"):
                            ui.label("Unique triggered").classes("text-xs secondary-text mb-1")
                            ui.label(f"{int(conn_stats.get('connectors_triggered', 0) or 0):,}").classes(
                                "text-lg font-bold text-emerald-400"
                            )
                        with ui.card().classes("text-center p-2 statistics-stat-cell rounded"):
                            ui.label("Total triggers").classes("text-xs secondary-text mb-1")
                            ui.label(f"{int(conn_stats.get('total_triggers', 0) or 0):,}").classes(
                                "text-lg font-bold text-green-400"
                            )
                        fs, ls = conn_stats.get("first_seen"), conn_stats.get("last_seen")
                        if fs:
                            with ui.card().classes("text-center p-2 statistics-stat-cell rounded"):
                                ui.label("First / last").classes("text-xs secondary-text mb-1")
                                ui.label(
                                    datetime.fromtimestamp(fs).strftime("%b %d, %Y")
                                    + (" / " + datetime.fromtimestamp(ls).strftime("%b %d, %Y") if ls else "")
                                ).classes("text-xs secondary-text")

                bot_stats = user_stats.get("chatbot") or {}
                if bot_stats:
                    ui.label("Chatbot").classes("text-sm font-semibold mb-2 mt-4 text-blue-300")
                    with ui.grid(columns=4).classes("w-full gap-3"):
                        for lbl, key in (
                            ("Commands used", "commands_triggered"),
                            ("Events used", "events_triggered"),
                            ("Total interactions", "total_interactions"),
                        ):
                            with ui.card().classes("text-center p-2 statistics-stat-cell rounded"):
                                ui.label(lbl).classes("text-xs secondary-text mb-1")
                                ui.label(f"{int(bot_stats.get(key, 0) or 0):,}").classes(
                                    "text-lg font-bold text-theme-primary"
                                )

                quote_stats = user_stats.get("quotes") or {}
                if quote_stats:
                    ui.label("Quotes").classes("text-sm font-semibold mb-2 mt-4 text-pink-300")
                    with ui.grid(columns=2).classes("w-full gap-3"):
                        with ui.card().classes("text-center p-2 statistics-stat-cell rounded"):
                            ui.label("Total redeemed").classes("text-xs secondary-text mb-1")
                            ui.label(f"{int(quote_stats.get('total_quotes_redeemed', 0) or 0):,}").classes(
                                "text-lg font-bold text-pink-400"
                            )

                gw_stats = user_stats.get("giveaways") or {}
                if gw_stats:
                    ui.label("Giveaways").classes("text-sm font-semibold mb-2 mt-4 text-rose-300")
                    with ui.card().classes("text-center p-2 statistics-stat-cell rounded"):
                        ui.label("Giveaway wins").classes("text-xs secondary-text mb-1")
                        ui.label(f"{int(gw_stats.get('giveaway_wins', 0) or 0):,}").classes(
                            "text-lg font-bold text-rose-400"
                        )

        # Recent event timeline
        if events:
            with ui.card().classes("settings-card w-full p-4"):
                ui.label("Recent Event Timeline").classes("font-semibold mb-3")

                # Show at most 50 events in the timeline
                timeline_events = events[:50]
                with ui.scroll_area().classes("w-full").style("max-height: 300px;"):
                    for ev in timeline_events:
                        ev_type = ev.get("event_type", "?")
                        ev_amount = ev.get("amount", 0) or 0
                        ev_ts = ev.get("timestamp", 0)
                        ev_alert = ev.get("alert_name", "")

                        type_icons = {
                            "bit": "⚡",
                            "sub": "🔄",
                            "giftsub": "🎁",
                            "donation": "💰",
                            "point_redeem": "🎯",
                            "follow": "👤",
                            "raid": "🛡️",
                            "connector": "🔗",
                            "chatbot_command": "🤖",
                            "chatbot_event": "🎪",
                            "quote_redeem": "💬",
                            "chat_message": "📨",
                            "giveaway_entry": "🎁",
                            "giveaway_win": "🏆",
                            "giveaway_draw_complete": "✅",
                        }
                        icon = type_icons.get(ev_type, "📌")
                        ts_str = datetime.fromtimestamp(ev_ts).strftime(
                            "%b %d, %Y %H:%M:%S"
                        ) if ev_ts else "?"

                        amount_str = f" ({int(ev_amount):,})" if ev_amount > 0 else ""
                        alert_str = f" - {ev_alert}" if ev_alert else ""

                        with ui.row().classes("w-full items-center gap-2 py-1"):
                            ui.label(icon).classes("text-base")
                            ui.label(f"{ev_type}{amount_str}{alert_str}").classes(
                                "font-semibold text-sm"
                            )
                            ui.label(ts_str).classes("text-xs secondary-text ml-auto")

    # ---- Export Highlights Section ----

    def _build_export_section(self):
        """Build the export highlights section with date range pickers and export button."""
        with ui.card().classes(
            "content-section statistics-section statistics-section-full w-full"
        ):
            ui.label("📸 Export Highlights").classes("text-xl font-bold mb-4")
            ui.label(
                "Export a shareable PNG summary of alert, social, and chatbot stats "
                "for the selected date range. Totals and leaders (top bit donor, top "
                "chatter, etc.) are computed from the timestamped event log for that "
                "range only—not from stored highlight records."
            ).classes("secondary-text mb-4")

            with ui.row().classes("w-full gap-4 items-end"):
                # Date range pickers
                today = datetime.now()
                thirty_days_ago = today - timedelta(days=30)

                self._export_start_date = form_input(
                    tooltip="Start date for the highlights export image",
                    label="Start date",
                    value=thirty_days_ago.strftime("%Y-%m-%d"),
                    classes="w-40",
                )
                self._export_start_date.props("type=date")

                self._export_end_date = form_input(
                    tooltip="End date for the highlights export image",
                    label="End date",
                    value=today.strftime("%Y-%m-%d"),
                    classes="w-40",
                )
                self._export_end_date.props("type=date")

                ui.button(
                    "📸 Export Image",
                    on_click=lambda: self._export_highlights(),
                ).classes(
                    "btn-primary px-6 py-2 rounded-lg font-semibold"
                )

                self._export_status_label = ui.label("").classes("text-sm secondary-text ml-4")

    def _export_highlights(self):
        """Handle the export highlights button click."""
        try:
            from ...path_utils import get_data_path

            _p_data = get_data_path(os.path.join("data", "statistics.db"))
            _p_root = get_data_path("statistics.db")

            def _exists_size(p: str) -> tuple:
                try:
                    return (os.path.isfile(p), os.path.getsize(p) if os.path.isfile(p) else None)
                except OSError:
                    return (False, None)

            _ed, _es = _exists_size(_p_data)
            _er, _ers = _exists_size(_p_root)
            print(
                "[highlights export] db paths data=",
                _p_data,
                "exists=",
                _ed,
                "size=",
                _es,
                "| root statistics.db=",
                _p_root,
                "exists=",
                _er,
                "size=",
                _ers,
            )

            # Parse date range
            raw_start = (
                str(self._export_start_date.value or "")
                if self._export_start_date
                else ""
            )
            raw_end = (
                str(self._export_end_date.value or "")
                if self._export_end_date
                else ""
            )
            start_str = self._normalize_date_str(raw_start)
            end_str = self._normalize_date_str(raw_end)

            try:
                start_dt = datetime.strptime(start_str, "%Y-%m-%d") if start_str else datetime.now() - timedelta(days=30)
                end_dt = datetime.strptime(end_str, "%Y-%m-%d") if end_str else datetime.now()
                end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            except ValueError:
                notify("Invalid date format. Use YYYY-MM-DD.", type="negative")
                return

            start_ts = start_dt.timestamp()
            end_ts = end_dt.timestamp()
            print(
                "[highlights export] dates raw_start/end=",
                repr(raw_start),
                repr(raw_end),
                "parsed=",
                start_str,
                end_str,
                "start_dt=",
                start_dt.isoformat(),
                "end_dt=",
                end_dt.isoformat(),
                "start_ts/end_ts=",
                start_ts,
                end_ts,
            )

            if self._export_status_label:
                self._export_status_label.set_text("Generating image...")

            stats_manager = get_statistics_manager()
            highlights, _hl_source = stats_manager.get_highlights_for_export(
                start_ts, end_ts
            )
            print(
                "[highlights export] after get_highlights_for_export: source=",
                _hl_source,
                "total_events=",
                highlights.get("total_events"),
                "n_keys=",
                len(highlights),
                "fallback_partial=",
                highlights.get("_fallback_partial"),
            )

            if not highlights.get("total_events", 0):
                log = stats_manager.get_event_log_summary()
                print("[highlights export] no rows in range; event_log_summary=", log)
                total_rows = int(log.get("total_rows") or 0)
                if total_rows == 0:
                    extra = (
                        " The event log is empty. Run Mycelian while viewers chat, redeem alerts, "
                        "use commands, etc., to record timestamped events."
                    )
                else:
                    mn = log.get("min_timestamp")
                    mx = log.get("max_timestamp")
                    if mn is not None and mx is not None:
                        d0 = datetime.fromtimestamp(float(mn)).strftime("%Y-%m-%d")
                        d1 = datetime.fromtimestamp(float(mx)).strftime("%Y-%m-%d")
                        extra = (
                            f" Event log spans {d0} to {d1} ({total_rows:,} rows). "
                            "Adjust the export range to overlap that span."
                        )
                    else:
                        extra = f" ({total_rows:,} rows in log; could not read date span.)"
                notify(
                    "Nothing to export: no events in the event log for this date range." + extra,
                    type="warning",
                )
                if self._export_status_label:
                    self._export_status_label.set_text("No data — see notification.")
                return

            # Generate output path
            export_dir = get_data_path("exports")
            os.makedirs(export_dir, exist_ok=True)
            filename = f"highlights_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.png"
            output_path = os.path.join(export_dir, filename)

            # Generate the image
            from ...statistics_export import generate_highlights_image
            from ...theme_manager import get_theme_manager

            settings = state_manager.get_app_settings()
            streamer = (settings.streamer_name or "Mycelian").strip() or "Mycelian"
            theme_manager = get_theme_manager()
            theme_manager.load_themes_from_directory()
            theme = theme_manager.get_theme()

            print(
                "[highlights export] calling generate_highlights_image path=",
                output_path,
            )
            success = generate_highlights_image(
                highlights=highlights,
                start_date=start_dt,
                end_date=end_dt,
                output_path=output_path,
                streamer_name=streamer,
                theme=theme,
            )
            print("[highlights export] generate_highlights_image success=", success)

            if success:
                if self._export_status_label:
                    self._export_status_label.set_text(f"Saved to: {output_path}")
                lifetime_note = (
                    "\n\nNumbers are lifetime totals from stored statistics, not limited to the selected dates."
                    if _hl_source == "lifetime"
                    else ""
                )
                notify(
                    f"Highlights image exported successfully!{lifetime_note}\n{output_path}",
                    type="positive",
                    close_button=True,
                )
            else:
                if self._export_status_label:
                    self._export_status_label.set_text("Export failed.")
                notify("Failed to generate highlights image.", type="negative")

        except Exception as e:
            print(f"Error exporting highlights: {e}")
            if self._export_status_label:
                self._export_status_label.set_text(f"Error: {e}")
            notify("Error exporting highlights image.", type="negative")

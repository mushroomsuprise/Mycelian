"""
Migration Tab for Mycelian Settings UI.

Provides UI for migrating data from old Firebase-only database format
to the new multi-database system.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

from nicegui import ui

from ...dataobjects import state_manager
from ...legacy_migration import LegacyMigrator, MigrationConfig, MigrationResult
from ...path_utils import ensure_directory_exists, get_data_path
from ...uiwindows.alertsettings import refresh_alert_dropdowns

logger = logging.getLogger(__name__)

_FILE_BROWSER_CARD_CLASSES = (
    "mx-auto self-start min-w-[480px] min-h-[400px] w-[min(88vw,1100px)] h-[500px] "
    "!max-w-[min(96vw,1920px)] max-h-[90vh] resize overflow-auto p-4 flex flex-col"
)
_FILE_BROWSER_DIALOG_PROPS = "content-class=mycelian-wide-file-dialog"


class MigrationTab:
    """Migration tab for transferring data from old Firebase database."""

    name = "Migration"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.ui_elements: Dict[str, Any] = {}
        self.config = MigrationConfig()
        self.migrator: Optional[LegacyMigrator] = None
        self.migration_result: Optional[MigrationResult] = None
        self.is_migrating: bool = False
        self.log_messages: list = []
        self.total_items: int = 0

        # Ensure directories exist
        ensure_directory_exists(get_data_path("data/migration_keys"))
        ensure_directory_exists(get_data_path("data/migration_legacy"))

    def on_enter(self) -> None:
        """Called when the tab becomes active."""
        # Refresh UI state
        self._update_button_states()

    def on_exit(self) -> None:
        """Called when the tab is no longer active."""
        pass

    def save(self) -> None:
        """Persist any configuration (not applicable for migration tab)."""
        pass

    def discard(self) -> None:
        """Reset configuration."""
        self.config = MigrationConfig()
        self._load_config_to_ui()

    def build(self, parent_container) -> None:
        """Build the migration tab UI."""
        with parent_container:
            with ui.card().classes("content-section w-full"):
                ui.label("Legacy Database Migration").classes("text-xl font-bold mb-4")

                ui.markdown(
                    """
                    This tool migrates data from the old Firebase-only database to your current database configuration.
                    
                    **Before starting:**
                    - Ensure you have your old Firebase service account key file
                    - Know your old Firebase database URL and streamer name
                    - Optionally, provide the path to your old assets folder for file copying
                    """
                ).classes("mb-4 secondary-text")

                ui.separator().classes("divider")

                # Configuration Section
                self._build_config_section()

                ui.separator().classes("divider")

                # Migration Controls Section
                self._build_controls_section()

                ui.separator().classes("divider")

                # Progress Section
                self._build_progress_section()

                ui.separator().classes("divider")

                # Results Section
                self._build_results_section()

    def _build_config_section(self) -> None:
        """Build the configuration section."""
        ui.label("Configuration").classes("text-lg font-semibold mb-3")

        with ui.column().classes("w-full gap-3"):
            # Service Account Key
            with ui.row().classes("w-full items-center gap-2"):
                ui.label("Old Service Account Key:").classes("w-48")
                self.ui_elements["service_account_path"] = (
                    ui.input(
                        value=self.config.old_service_account_path,
                        placeholder="Path to old ServiceAccountKey.json",
                    )
                    .classes("flex-1")
                    .on(
                        "change",
                        lambda e: self._update_config(
                            "old_service_account_path", e.value
                        ),
                    )
                )
                ui.button(
                    icon="folder_open",
                    on_click=lambda: self._browse_file("service_account_path"),
                ).props("flat dense")

            # Database URL
            with ui.row().classes("w-full items-center gap-2"):
                ui.label("Old Database URL:").classes("w-48")
                self.ui_elements["database_url"] = (
                    ui.input(
                        value=self.config.old_database_url,
                        placeholder="https://your-project-rtdb.firebaseio.com/",
                    )
                    .classes("flex-1")
                    .on(
                        "change",
                        lambda e: self._update_config("old_database_url", e.value),
                    )
                )

            # Streamer Name
            with ui.row().classes("w-full items-center gap-2"):
                ui.label("Old Streamer Name:").classes("w-48")
                self.ui_elements["streamer_name"] = (
                    ui.input(
                        value=self.config.old_streamer_name,
                        placeholder="Your username in the old database",
                    )
                    .classes("w-64")
                    .on(
                        "change",
                        lambda e: self._update_config("old_streamer_name", e.value),
                    )
                )

            # Assets Folder
            with ui.row().classes("w-full items-center gap-2"):
                ui.label("Old Assets Folder:").classes("w-48")
                self.ui_elements["assets_folder"] = (
                    ui.input(
                        value=self.config.old_assets_folder,
                        placeholder="Path to old assets/static folder (optional)",
                    )
                    .classes("flex-1")
                    .on(
                        "change",
                        lambda e: self._update_config("old_assets_folder", e.value),
                    )
                )
                ui.button(
                    icon="folder_open",
                    on_click=lambda: self._browse_folder("assets_folder"),
                ).props("flat dense")

            # JSON File (alternative to Firebase)
            with ui.row().classes("w-full items-center gap-2"):
                ui.label("Or Load from JSON:").classes("w-48")
                self.ui_elements["json_file"] = ui.input(
                    placeholder="Path to old_tool_data.json (alternative to Firebase)",
                ).classes("flex-1")
                ui.button(
                    icon="folder_open",
                    on_click=lambda: self._browse_json_file(),
                ).props("flat dense")

            ui.separator().classes("my-2")

            # Options
            ui.label("Options").classes("font-semibold")
            with ui.row().classes("w-full gap-4"):
                self.ui_elements["skip_disabled"] = ui.checkbox(
                    "Skip disabled alerts",
                    value=self.config.skip_disabled_alerts,
                    on_change=lambda e: self._update_config(
                        "skip_disabled_alerts", e.value
                    ),
                )
                self.ui_elements["skip_existing"] = ui.checkbox(
                    "Skip existing alerts",
                    value=self.config.skip_existing_alerts,
                    on_change=lambda e: self._update_config(
                        "skip_existing_alerts", e.value
                    ),
                )

            with ui.row().classes("w-full gap-4"):
                self.ui_elements["migrate_logs"] = ui.checkbox(
                    "Migrate alert history",
                    value=self.config.migrate_alert_logs,
                    on_change=lambda e: self._update_config(
                        "migrate_alert_logs", e.value
                    ),
                )
                self.ui_elements["migrate_other"] = ui.checkbox(
                    "Save other data as JSON",
                    value=self.config.migrate_other_data,
                    on_change=lambda e: self._update_config(
                        "migrate_other_data", e.value
                    ),
                )
                self.ui_elements["copy_files"] = ui.checkbox(
                    "Copy asset files",
                    value=self.config.copy_asset_files,
                    on_change=lambda e: self._update_config(
                        "copy_asset_files", e.value
                    ),
                )

    def _build_controls_section(self) -> None:
        """Build the migration controls section."""
        ui.label("Migration Controls").classes("text-lg font-semibold mb-3")

        with ui.row().classes("w-full gap-3"):
            self.ui_elements["test_btn"] = ui.button(
                "Test Connection",
                on_click=self._test_connection,
            ).props("icon=wifi_tethering outline")

            self.ui_elements["start_btn"] = ui.button(
                "Start Migration",
                on_click=self._start_migration,
            ).props("icon=play_arrow color=primary")

            self.ui_elements["cancel_btn"] = ui.button(
                "Cancel",
                on_click=self._cancel_migration,
            ).props("icon=cancel outline color=negative")
            self.ui_elements["cancel_btn"].set_visibility(False)

    def _build_progress_section(self) -> None:
        """Build the progress section."""
        ui.label("Progress").classes("text-lg font-semibold mb-3")

        with ui.column().classes("w-full gap-2"):
            # Progress bar
            self.ui_elements["progress"] = ui.linear_progress(
                value=0, show_value=False
            ).classes("w-full")
            self.ui_elements["progress_label"] = ui.label("Ready to migrate").classes(
                "secondary-text"
            )

            # Log display
            ui.label("Log").classes("font-semibold mt-2")
            self.ui_elements["log_area"] = (
                ui.textarea(value="")
                .classes("w-full font-mono text-sm")
                .props("readonly outlined rows=8")
            )

            # Summary stats (moved from results section)
            ui.label("Summary").classes("font-semibold mt-3")
            with ui.row().classes("w-full gap-6"):
                with ui.column().classes("items-center"):
                    self.ui_elements["stat_total"] = ui.label("0").classes(
                        "text-2xl font-bold muted-text"
                    )
                    ui.label("Total").classes("secondary-text text-sm")

                with ui.column().classes("items-center"):
                    self.ui_elements["stat_success"] = ui.label("0").classes(
                        "text-2xl font-bold text-green-500"
                    )
                    ui.label("Migrated").classes("secondary-text text-sm")

                with ui.column().classes("items-center"):
                    self.ui_elements["stat_failed"] = ui.label("0").classes(
                        "text-2xl font-bold text-red-500"
                    )
                    ui.label("Failed").classes("secondary-text text-sm")

                with ui.column().classes("items-center"):
                    self.ui_elements["stat_skipped"] = ui.label("0").classes(
                        "text-2xl font-bold text-yellow-500"
                    )
                    ui.label("Skipped").classes("secondary-text text-sm")

                with ui.column().classes("items-center"):
                    self.ui_elements["stat_files"] = ui.label("0").classes(
                        "text-2xl font-bold text-blue-500"
                    )
                    ui.label("Files Copied").classes("secondary-text text-sm")

    def _build_results_section(self) -> None:
        """Build the results section (now empty)."""
        pass

    def _update_config(self, field: str, value: Any) -> None:
        """Update configuration field."""
        if hasattr(self.config, field):
            setattr(self.config, field, value)
            self.dirty = True

            # Special handling for assets folder - update copy files checkbox
            if field == "old_assets_folder":
                self._update_copy_files_checkbox_state()

    def _load_config_to_ui(self) -> None:
        """Load current config into UI elements."""
        if "service_account_path" in self.ui_elements:
            self.ui_elements[
                "service_account_path"
            ].value = self.config.old_service_account_path
        if "database_url" in self.ui_elements:
            self.ui_elements["database_url"].value = self.config.old_database_url
        if "streamer_name" in self.ui_elements:
            self.ui_elements["streamer_name"].value = self.config.old_streamer_name
        if "assets_folder" in self.ui_elements:
            self.ui_elements["assets_folder"].value = self.config.old_assets_folder
        if "skip_disabled" in self.ui_elements:
            self.ui_elements["skip_disabled"].value = self.config.skip_disabled_alerts
        if "skip_existing" in self.ui_elements:
            self.ui_elements["skip_existing"].value = self.config.skip_existing_alerts
        if "migrate_logs" in self.ui_elements:
            self.ui_elements["migrate_logs"].value = self.config.migrate_alert_logs
        if "migrate_other" in self.ui_elements:
            self.ui_elements["migrate_other"].value = self.config.migrate_other_data
        if "copy_files" in self.ui_elements:
            self.ui_elements["copy_files"].value = self.config.copy_asset_files
        # Update copy files checkbox state based on assets folder
        self._update_copy_files_checkbox_state()

    def _update_copy_files_checkbox_state(self) -> None:
        """Update the copy asset files checkbox based on whether assets folder is set."""
        copy_checkbox = self.ui_elements.get("copy_files")
        if copy_checkbox is not None:
            has_assets_folder = bool(
                self.config.old_assets_folder and self.config.old_assets_folder.strip()
            )
            copy_checkbox.set_enabled(has_assets_folder)
            if has_assets_folder and not self.config.copy_asset_files:
                # If folder is present and checkbox is not checked, check it
                self.config.copy_asset_files = True
                copy_checkbox.value = True
            elif not has_assets_folder:
                # If no folder, uncheck and disable
                self.config.copy_asset_files = False
                copy_checkbox.value = False

    def _update_button_states(self) -> None:
        """Update button enabled/disabled states."""
        test_btn = self.ui_elements.get("test_btn")
        start_btn = self.ui_elements.get("start_btn")
        cancel_btn = self.ui_elements.get("cancel_btn")

        if self.is_migrating:
            if test_btn is not None:
                test_btn.set_enabled(False)
            if start_btn is not None:
                start_btn.set_enabled(False)
            if cancel_btn is not None:
                cancel_btn.set_visibility(True)
        else:
            if test_btn is not None:
                test_btn.set_enabled(True)
            if start_btn is not None:
                start_btn.set_enabled(True)
            if cancel_btn is not None:
                cancel_btn.set_visibility(False)

    def _log(self, message: str) -> None:
        """Add a message to the log display."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        self.log_messages.append(log_entry)

        # Keep only last 100 messages
        if len(self.log_messages) > 100:
            self.log_messages = self.log_messages[-100:]

        # Update log area
        if "log_area" in self.ui_elements:
            self.ui_elements["log_area"].value = "\n".join(self.log_messages)
            # Scroll to bottom to show latest messages
            self.ui_elements["log_area"].run_javascript(
                "this.scrollTop = this.scrollHeight"
            )

    def _progress_callback(self, message: str, progress: float) -> None:
        """Callback for migration progress updates."""
        self._log(message)

        # Handle progress values that might be decimals (0.0-1.0) or percentages (0-100)
        if progress <= 1.0:
            # Assume it's a decimal fraction, convert to percentage
            progress_percent = progress * 100.0
            progress_decimal = progress
        else:
            # Assume it's already a percentage
            progress_percent = progress
            progress_decimal = progress / 100.0

        if "progress" in self.ui_elements:
            self.ui_elements["progress"].value = progress_decimal
        if "progress_label" in self.ui_elements:
            if self.total_items > 0:
                # Show item-based progress if we have total count
                current_item = int(progress_percent / 100.0 * self.total_items)
                self.ui_elements[
                    "progress_label"
                ].text = f"{current_item}/{self.total_items} - {message}"
            else:
                self.ui_elements[
                    "progress_label"
                ].text = f"{progress_percent:.1f}% - {message}"

    def _browse_file(self, target_element: str) -> None:
        """Browse for a service account key file."""
        self._show_file_picker_dialog(
            title="Select Service Account Key File",
            extensions=[".json"],
            target_field=target_element,
            browse_mode="file",
            initial_path=get_data_path("data/migration_keys/"),
        )

    def _browse_folder(self, target_element: str) -> None:
        """Browse for an assets folder."""
        self._show_file_picker_dialog(
            title="Select Old Assets Folder",
            extensions=[],  # No extensions for directory selection
            target_field=target_element,
            browse_mode="directory",
            initial_path="",
        )

    def _browse_json_file(self) -> None:
        """Browse for a JSON file."""
        self._show_file_picker_dialog(
            title="Select Old Tool Data JSON File",
            extensions=[".json"],
            target_field="json_file",
            browse_mode="file",
            initial_path="",
        )

    def _show_file_picker_dialog(
        self,
        title: str,
        extensions: list,
        target_field: str,
        browse_mode: str,
        initial_path: str = "",
    ):
        """Show a simplified file picker dialog."""
        from pathlib import Path

        start = initial_path or str(Path.home())
        try:
            p0 = Path(start).expanduser()
            start_native = str(p0.resolve()) if p0.exists() else str(Path.home())
        except OSError:
            start_native = str(Path.home())

        # Create dialog state (native OS path strings in UI)
        dialog_state = {
            "current_path": start_native,
            "selected_file": None,
            "path_input": None,
            "file_list": None,
            "target_field": target_field,
            "extensions": extensions,
            "browse_mode": browse_mode,
        }

        with ui.dialog().props(_FILE_BROWSER_DIALOG_PROPS) as dialog, ui.card().classes(
            _FILE_BROWSER_CARD_CLASSES
        ):
            ui.label(title).classes("text-lg font-bold mb-4 shrink-0")

            with ui.column().classes("w-full min-h-0 flex-1 gap-3"):
                # Current path input
                with ui.row().classes("w-full items-center gap-2"):
                    ui.label("Path:").classes("text-sm font-medium")
                    dialog_state["path_input"] = ui.input(
                        value=dialog_state["current_path"],
                        placeholder="Enter path or navigate below...",
                    ).classes("flex-1")
                    ui.button(
                        "Go",
                        icon="folder",
                        on_click=lambda: self._navigate_to_path(dialog_state),
                    ).props("dense")

                # File listing area
                with ui.scroll_area().classes(
                    "w-full min-h-0 flex-1 border rounded-lg p-2 bg-theme-base"
                ):
                    dialog_state["file_list"] = ui.column().classes("w-full gap-1")

                # Selected item display
                with ui.row().classes("w-full items-center shrink-0"):
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
                        ui.button(
                            "Select This Directory",
                            icon="check_circle",
                            on_click=lambda: self._select_current_directory(
                                dialog_state, dialog
                            ),
                        ).classes("btn-secondary text-sm")

                # Dialog buttons
                with ui.row().classes("w-full justify-end gap-2 mt-4 shrink-0"):
                    ui.button("Cancel", on_click=dialog.close).classes(
                        "btn-secondary"
                    )

                    button_text = (
                        "Select File"
                        if dialog_state["browse_mode"] == "file"
                        else "Select Directory"
                    )
                    button_icon = (
                        "description"
                        if dialog_state["browse_mode"] == "file"
                        else "folder"
                    )

                    select_button = ui.button(
                        button_text,
                        icon=button_icon,
                        on_click=lambda: self._select_file_from_dialog(
                            dialog_state, dialog
                        ),
                    ).classes("btn-primary")

                    # For directory mode, button is always enabled
                    if dialog_state["browse_mode"] == "file":
                        select_button.enabled = False
                    dialog_state["select_button"] = select_button

        # Initial file listing
        self._update_file_listing(dialog_state)
        dialog.open()

    def _navigate_to_path(self, dialog_state):
        """Navigate to the path entered in the input field."""
        from pathlib import Path

        raw = (dialog_state["path_input"].value or "").strip()
        if not raw:
            return
        path = Path(raw).expanduser()
        try:
            path = path.resolve()
        except OSError:
            pass
        if path.exists() and path.is_dir():
            native = str(path)
            dialog_state["current_path"] = native
            dialog_state["path_input"].value = native
            if hasattr(dialog_state["path_input"], "update"):
                dialog_state["path_input"].update()
            self._update_file_listing(dialog_state)
        else:
            ui.notify(f"Path does not exist: {raw}", type="warning")

    def _update_file_listing(self, dialog_state):
        """Update the file listing for the current path."""
        import os
        from pathlib import Path

        current_path = dialog_state["current_path"]
        if not os.path.exists(current_path):
            return

        # Clear existing list
        dialog_state["file_list"].clear()

        try:
            path_obj = Path(current_path)
            items = []

            # Add parent directory option
            if path_obj.parent != path_obj:
                items.append(("..", "parent", True))

            # Add directories and files
            for item in sorted(
                path_obj.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())
            ):
                if item.name.startswith("."):  # Skip hidden files
                    continue

                is_dir = item.is_dir()
                if dialog_state["browse_mode"] == "file":
                    # For file mode, show both dirs and files, but filter files by extension
                    if not is_dir and dialog_state["extensions"]:
                        if not any(
                            item.name.lower().endswith(ext.lower())
                            for ext in dialog_state["extensions"]
                        ):
                            continue
                items.append((item.name, "dir" if is_dir else "file", is_dir))

            # Create UI elements
            for name, item_type, is_dir in items:
                with ui.element("div").classes(
                    "w-full flex items-center gap-2 p-1 hover-theme-surface rounded cursor-pointer"
                ) as item_div:
                    if item_type == "parent":
                        ui.icon("arrow_upward").classes("text-blue-400")
                    elif item_type == "dir":
                        ui.icon("folder").classes("text-yellow-400")
                    else:
                        ui.icon("description").classes("muted-text")

                    ui.label(name).classes("flex-1 text-sm")

                    # Make div clickable
                    item_div.on(
                        "click",
                        lambda n=name,
                        t=item_type,
                        ds=dialog_state: self._handle_item_click(n, t, ds),
                    )

        except Exception as e:
            ui.notify(f"Error listing directory: {e}", type="negative")

    def _handle_item_click(self, name, item_type, dialog_state):
        """Handle clicking on a file/directory item."""
        from pathlib import Path

        current_path = Path(dialog_state["current_path"])

        if item_type == "parent":
            new_path = current_path.parent
        else:
            new_path = current_path / name

        if item_type in ["parent", "dir"]:
            # Navigate into directory
            dialog_state["current_path"] = str(new_path)
            dialog_state["path_input"].value = str(new_path)
            self._update_file_listing(dialog_state)
        else:
            # Select file
            dialog_state["selected_file"] = str(new_path)
            dialog_state["selected_label"].text = name
            if "select_button" in dialog_state:
                dialog_state["select_button"].enabled = True

    def _select_current_directory(self, dialog_state, dialog):
        """Select the current directory."""
        selected_path = dialog_state["current_path"]
        self._set_selected_path(dialog_state["target_field"], selected_path)
        dialog.close()

    def _select_file_from_dialog(self, dialog_state, dialog):
        """Select the chosen file/directory from the dialog."""
        if dialog_state["browse_mode"] == "directory":
            selected_path = dialog_state["current_path"]
        else:
            selected_path = dialog_state.get("selected_file")
            if not selected_path:
                ui.notify("No file selected", type="warning")
                return

        self._set_selected_path(dialog_state["target_field"], selected_path)
        dialog.close()

    def _set_selected_path(self, target_field, path):
        """Set the selected path in the appropriate UI element."""
        if target_field in self.ui_elements:
            self.ui_elements[target_field].value = path
            # Trigger change event to update config
            if target_field == "service_account_path":
                self._update_config("old_service_account_path", path)
            elif target_field == "assets_folder":
                self._update_config("old_assets_folder", path)
            elif target_field == "json_file":
                # Just update the json_file field, no config update needed
                pass

    async def _test_connection(self) -> None:
        """Test connection to old database."""
        json_file = self.ui_elements.get("json_file")
        json_path = json_file.value if json_file else ""

        if json_path and os.path.exists(json_path):
            # Test JSON file loading
            self._log("Testing JSON file loading...")
            try:
                migrator = LegacyMigrator(self.config)
                success, error = migrator.load_from_json_file(json_path)
                if success:
                    ui.notify("JSON file loaded successfully!", type="positive")
                    self._log("JSON file loaded successfully")
                else:
                    ui.notify(f"Failed to load JSON: {error}", type="negative")
                    self._log(f"Error: {error}")
            except Exception as e:
                ui.notify(f"Error: {str(e)}", type="negative")
                self._log(f"Error: {str(e)}")
        else:
            # Test Firebase connection
            if not self.config.old_service_account_path:
                ui.notify("Please provide a service account key path", type="warning")
                return
            if not self.config.old_database_url:
                ui.notify("Please provide the old database URL", type="warning")
                return

            self._log("Testing Firebase connection...")
            try:
                migrator = LegacyMigrator(self.config)
                success, error = migrator.connect_to_old_database()
                if success:
                    ui.notify("Connection successful!", type="positive")
                    self._log("Connection successful")
                    # Try to fetch data
                    success, error = migrator.fetch_all_data()
                    if success:
                        self._log("Data fetch successful")
                    else:
                        self._log(f"Data fetch failed: {error}")
                else:
                    ui.notify(f"Connection failed: {error}", type="negative")
                    self._log(f"Connection failed: {error}")
                migrator.cleanup()
            except Exception as e:
                ui.notify(f"Error: {str(e)}", type="negative")
                self._log(f"Error: {str(e)}")

    async def _start_migration(self) -> None:
        """Start the migration process."""
        json_file = self.ui_elements.get("json_file")
        json_path = json_file.value if json_file else ""

        # Validate configuration
        if not json_path or not os.path.exists(json_path):
            if not self.config.old_service_account_path:
                ui.notify(
                    "Please provide a service account key or JSON file", type="warning"
                )
                return
            if not self.config.old_database_url:
                ui.notify("Please provide the old database URL", type="warning")
                return

        # Confirm migration
        with ui.dialog() as dialog, ui.card():
            ui.label("Confirm Migration").classes("text-lg font-bold")
            ui.label(
                "This will import data from your old database. "
                "Existing alerts may be skipped or overwritten based on your options."
            ).classes("my-2")
            with ui.row().classes("justify-end gap-2"):
                ui.button("Cancel", on_click=dialog.close).props("outline")
                ui.button(
                    "Start Migration",
                    on_click=lambda: self._run_migration(dialog, json_path),
                ).props("color=primary")
        dialog.open()

    async def _run_migration(self, dialog, json_path: str) -> None:
        """Run the migration in a background thread."""
        dialog.close()

        self.is_migrating = True
        self._update_button_states()
        self.log_messages = []
        self._log("Starting migration...")

        # Reset stats
        self._update_stats(MigrationResult())

        try:
            self.migrator = LegacyMigrator(self.config)
            self.migrator.set_progress_callback(self._progress_callback)

            # Load data
            if json_path and os.path.exists(json_path):
                success, error = self.migrator.load_from_json_file(json_path)
            else:
                success, error = self.migrator.connect_to_old_database()
                if success:
                    success, error = self.migrator.fetch_all_data()

            if not success:
                self._log(f"Failed to load data: {error}")
                ui.notify(f"Migration failed: {error}", type="negative")
                self.is_migrating = False
                self._update_button_states()
                return

            # Count total items for progress tracking
            self.total_items = self.migrator.count_total_items()
            if self.total_items > 0:
                self._log(f"Found {self.total_items} items to migrate")
            else:
                self._log("No items found to migrate")
                self.is_migrating = False
                self._update_button_states()
                return

            # Run migration in background
            def run_migration():
                try:
                    result = self.migrator.run_full_migration()
                    self.migration_result = result

                    # Check if migration was cancelled
                    if self.migrator.is_cancelled():
                        self._log(
                            "<span style='color: orange;'>❌ Migration was cancelled by user</span>"
                        )
                        # Still trigger data reload for cancelled migrations
                        try:
                            state_manager.reload_from_firebase()
                            self._log("Database data reloaded after cancellation")

                            # Refresh alert dropdowns
                            refresh_alert_dropdowns()
                            self._log("Alert dropdowns refreshed")
                        except Exception as reload_error:
                            logger.error(
                                f"Failed to reload database data after cancellation: {reload_error}",
                                exc_info=True,
                            )
                            self._log(
                                "Warning: Failed to reload database data after cancellation"
                            )

                except Exception as e:
                    logger.error(f"Migration error: {e}", exc_info=True)
                    self.migration_result = MigrationResult()
                    self.migration_result.add_error("migration", "", str(e))

            # Run in thread pool
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, run_migration)

            # Update UI with results
            if self.migration_result:
                self._update_stats(self.migration_result)
                self._log(
                    f"Migration complete! {self.migration_result.successful} items migrated."
                )

                # Reload database data to reflect migrated changes
                try:
                    state_manager.reload_from_firebase()
                    self._log("Database data reloaded successfully")

                    # Refresh alert dropdowns in the UI to show migrated alerts
                    refresh_alert_dropdowns()
                    self._log("Alert dropdowns refreshed")
                except Exception as reload_error:
                    logger.error(
                        f"Failed to reload database data: {reload_error}", exc_info=True
                    )
                    self._log(
                        "Warning: Failed to reload database data - you may need to restart the app"
                    )

                if self.migration_result.failed > 0:
                    ui.notify(
                        f"Migration complete with {self.migration_result.failed} errors",
                        type="warning",
                    )
                else:
                    ui.notify("Migration completed successfully!", type="positive")

        except Exception as e:
            logger.error(f"Migration error: {e}", exc_info=True)
            self._log(f"Error: {str(e)}")
            ui.notify(f"Migration error: {str(e)}", type="negative")
        finally:
            self.is_migrating = False
            self._update_button_states()
            if self.migrator:
                self.migrator.cleanup()

    def _cancel_migration(self) -> None:
        """Cancel the ongoing migration."""
        if self.migrator and self.is_migrating:
            self.migrator.cancel_migration()
            self._log(
                "<span style='color: orange;'>⚠️ Migration cancellation requested...</span>"
            )
            ui.notify("Migration cancellation requested", type="warning")
        else:
            ui.notify("No migration in progress", type="info")

    def _update_stats(self, result: MigrationResult) -> None:
        """Update the statistics display."""
        if "stat_total" in self.ui_elements:
            self.ui_elements["stat_total"].text = str(result.total_items)
        if "stat_success" in self.ui_elements:
            self.ui_elements["stat_success"].text = str(result.successful)
        if "stat_failed" in self.ui_elements:
            self.ui_elements["stat_failed"].text = str(result.failed)
        if "stat_skipped" in self.ui_elements:
            self.ui_elements["stat_skipped"].text = str(result.skipped)
        if "stat_files" in self.ui_elements:
            self.ui_elements["stat_files"].text = str(len(result.copied_files))

        # Log missing files and errors in the log area with color coding
        if result.missing_files:
            self._log(
                f"<span style='color: orange;'>⚠️ {len(result.missing_files)} missing file(s) found:</span>"
            )
            for m in result.missing_files[:20]:  # Limit to 20 in log
                self._log(
                    f"<span style='color: orange;'>[{m['type']}] {m['alert_id']}: {m['path']}</span>"
                )
            if len(result.missing_files) > 20:
                self._log(
                    f"<span style='color: orange;'>... and {len(result.missing_files) - 20} more missing files</span>"
                )

        if result.errors:
            self._log(
                f"<span style='color: red;'>❌ {len(result.errors)} error(s) occurred:</span>"
            )
            for e in result.errors[:20]:  # Limit to 20 in log
                self._log(
                    f"<span style='color: red;'>[{e['type']}] {e['id']}: {e['error']}</span>"
                )
            if len(result.errors) > 20:
                self._log(
                    f"<span style='color: red;'>... and {len(result.errors) - 20} more errors</span>"
                )

    def _add_final_status_message(self, result: MigrationResult) -> None:
        """Add a final color-coded status message summarizing migration issues."""
        issues = []

        if result.failed > 0:
            issues.append(f"{result.failed} failed")
        if result.missing_files:
            issues.append(f"{len(result.missing_files)} missing files")
        if result.errors:
            issues.append(f"{len(result.errors)} errors")

        if issues:
            status_msg = f"⚠️ Migration completed with issues: {', '.join(issues)}"
            self._log(
                f"<span style='color: orange; font-weight: bold;'>{status_msg}</span>"
            )
        elif result.total_items > 0:
            status_msg = "✅ Migration completed successfully with no issues!"
            self._log(
                f"<span style='color: green; font-weight: bold;'>{status_msg}</span>"
            )
        else:
            status_msg = "ℹ️ No items were found to migrate"
            self._log(f"<span style='color: blue;'>{status_msg}</span>")

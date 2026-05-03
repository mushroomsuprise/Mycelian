from __future__ import annotations

import json
import logging
import threading
from dataclasses import replace
from typing import Dict, Any, Optional, List

from nicegui import ui
from ...notification_engine import notify

from ... import dataobjects
from ...config_manager import config_manager
from ...database_manager import DatabaseConfig, database_manager

logger = logging.getLogger(__name__)


class DatabaseViewer:
    """A file-browser-like database viewer and editor dialog"""

    def __init__(self) -> None:
        self.dialog: Optional[ui.dialog] = None
        self.snapshot: Dict[str, Any] = {}
        self.all_paths: List[str] = []
        self.current_path: str = ""
        self.pending_changes: Dict[str, Any] = {}

        # UI element references
        self._tree_container: Optional[ui.column] = None
        self._content_container: Optional[ui.column] = None
        self._breadcrumb_container: Optional[ui.row] = None
        self._path_label: Optional[ui.label] = None
        self._json_editor: Optional[ui.textarea] = None
        self._save_btn: Optional[ui.button] = None
        self._status_label: Optional[ui.label] = None

    def show(self) -> None:
        """Show the database viewer dialog"""
        # Take snapshot of database
        self._load_snapshot()

        with ui.dialog().props("maximized persistent") as self.dialog:
            with (
                ui.card()
                .classes("w-full h-full flex flex-col")
                .style("max-width: 100vw; max-height: 100vh; background: var(--color-bg-base);")
            ):
                # Header
                self._build_header()

                # Main content area
                with (
                    ui.row()
                    .classes("flex-1 w-full gap-0")
                    .style("min-height: 0; overflow: hidden;")
                ):
                    # Left sidebar - tree navigation
                    self._build_sidebar()

                    # Right content area
                    self._build_content_area()

                # Footer with status
                self._build_footer()

        self.dialog.open()

    def _load_snapshot(self) -> None:
        """Load a complete snapshot of the database"""
        try:
            self.snapshot = database_manager.get_snapshot()
            self.all_paths = database_manager.get_all_paths()
            logger.info(f"Database snapshot loaded: {len(self.all_paths)} paths found")
        except Exception as e:
            logger.error(f"Error loading database snapshot: {e}", exc_info=True)
            self.snapshot = {}
            self.all_paths = []

    def _build_header(self) -> None:
        """Build the dialog header"""
        with (
            ui.row()
            .classes("w-full items-center justify-between p-4")
            .style("background: var(--color-bg-elevated); border-bottom: 1px solid var(--color-border-default);")
        ):
            with ui.row().classes("items-center gap-3"):
                ui.icon("storage", size="md").classes("text-theme-primary")
                ui.label("Database Explorer").classes("text-xl font-bold")
                db_status = database_manager.get_connection_status()
                db_type = db_status.get("database_type", "Unknown")
                ui.badge(db_type, color="primary").classes("ml-2")

            with ui.row().classes("gap-2"):
                ui.button(
                    "Refresh",
                    icon="refresh",
                    on_click=self._refresh_snapshot,
                ).props("flat color=white").classes("")
                ui.button(
                    "New Path",
                    icon="add",
                    on_click=self._show_create_dialog,
                ).props("flat color=white").classes("")
                ui.button(
                    "",
                    icon="close",
                    on_click=self._close_dialog,
                ).props("flat round color=white").classes("")

    def _build_sidebar(self) -> None:
        """Build the left sidebar with tree navigation"""
        with (
            ui.column()
            .classes("h-full")
            .style(
                "width: 320px; background: var(--color-bg-base); border-right: 1px solid var(--color-border-default); overflow: hidden;"
            )
        ):
            # Search box
            with (
                ui.row()
                .classes("w-full p-3")
                .style("border-bottom: 1px solid var(--color-border-default);")
            ):
                self._search_input = (
                    ui.input(
                        placeholder="Search paths...",
                    )
                    .classes("w-full")
                    .props("dense outlined")
                    .style("background: var(--color-bg-elevated);")
                )
                self._search_input.on("update:model-value", self._filter_tree)

            # Tree container with scroll
            with ui.scroll_area().classes("flex-1 w-full"):
                self._tree_container = ui.column().classes("w-full p-2 gap-1")
                self._build_tree()

    def _build_tree(self, filter_text: str = "") -> None:
        """Build the tree structure from paths"""
        if self._tree_container is None:
            return

        self._tree_container.clear()

        # Build tree structure from flat paths
        tree_structure = self._build_tree_structure()

        with self._tree_container:
            if not tree_structure:
                ui.label("No data in database").classes("secondary-text italic p-4")
                return

            self._render_tree_level(tree_structure, "", filter_text.lower())

    def _build_tree_structure(self) -> Dict[str, Any]:
        """Convert flat paths to nested tree structure"""
        tree: Dict[str, Any] = {}

        for path in self.all_paths:
            parts = path.split("/")
            current = tree

            for i, part in enumerate(parts):
                if part not in current:
                    current[part] = {"__children__": {}, "__is_leaf__": False}
                if i == len(parts) - 1:
                    current[part]["__is_leaf__"] = True
                    current[part]["__path__"] = path
                current = current[part]["__children__"]

        return tree

    def _render_tree_level(
        self,
        level: Dict[str, Any],
        parent_path: str,
        filter_text: str,
        depth: int = 0,
    ) -> None:
        """Render a level of the tree"""
        for name, node in sorted(level.items()):
            full_path = f"{parent_path}/{name}" if parent_path else name

            # Filter check
            if filter_text and filter_text not in full_path.lower():
                # Check if any children match
                has_matching_children = self._has_matching_children(
                    node.get("__children__", {}), full_path, filter_text
                )
                if not has_matching_children:
                    continue

            is_leaf = node.get("__is_leaf__", False)
            has_children = bool(node.get("__children__", {}))
            is_selected = self.current_path == node.get("__path__", full_path)

            # Create tree item row
            indent = depth * 16
            bg_class = "btn-selected" if is_selected else "hover-theme-surface"

            with (
                ui.row()
                .classes(
                    f"w-full items-center py-1 px-2 rounded cursor-pointer {bg_class}"
                )
                .style(f"margin-left: {indent}px;") as row
            ):
                # Folder/file icon
                if is_leaf:
                    ui.icon("description", size="xs").classes("text-blue-400 mr-2")
                elif has_children:
                    ui.icon("folder", size="xs").classes("text-yellow-400 mr-2")
                else:
                    ui.icon("folder_open", size="xs").classes("text-yellow-600 mr-2")

                # Name label
                ui.label(name).classes("flex-1 text-sm truncate").style(
                    "max-width: 200px;"
                )

                # Click handler
                if is_leaf:
                    actual_path = node.get("__path__", full_path)
                    row.on("click", lambda p=actual_path: self._select_path(p))
                else:
                    row.on(
                        "click",
                        lambda fp=full_path: self._toggle_folder(fp),
                    )

            # Render children
            if has_children:
                self._render_tree_level(
                    node["__children__"],
                    full_path,
                    filter_text,
                    depth + 1,
                )

    def _has_matching_children(
        self, children: Dict[str, Any], parent_path: str, filter_text: str
    ) -> bool:
        """Check if any children match the filter"""
        for name, node in children.items():
            full_path = f"{parent_path}/{name}" if parent_path else name
            if filter_text in full_path.lower():
                return True
            if self._has_matching_children(
                node.get("__children__", {}), full_path, filter_text
            ):
                return True
        return False

    def _build_content_area(self) -> None:
        """Build the main content area"""
        with (
            ui.column()
            .classes("flex-1 h-full")
            .style("background: var(--color-bg-base); overflow: hidden;")
        ):
            # Breadcrumb
            with (
                ui.row()
                .classes("w-full p-3 items-center gap-2")
                .style("background: var(--color-bg-elevated); border-bottom: 1px solid var(--color-border-default);")
            ):
                ui.icon("folder_open", size="sm").classes("secondary-text")
                self._breadcrumb_container = ui.row().classes(
                    "flex-1 items-center gap-1 flex-wrap"
                )
                self._update_breadcrumb()

            # Content area
            with ui.scroll_area().classes("flex-1 w-full"):
                self._content_container = ui.column().classes("w-full p-4 gap-4")
                self._show_welcome_content()

    def _show_welcome_content(self) -> None:
        """Show welcome/empty state content"""
        if self._content_container is None:
            return

        self._content_container.clear()

        with self._content_container:
            with ui.column().classes("w-full items-center justify-center py-12"):
                ui.icon("storage", size="xl").classes("muted-text mb-4")
                ui.label("Select a path from the tree").classes("text-lg secondary-text")
                ui.label(f"{len(self.all_paths)} paths available in database").classes(
                    "text-sm muted-text mt-2"
                )

    def _update_breadcrumb(self) -> None:
        """Update the breadcrumb navigation"""
        if self._breadcrumb_container is None:
            return

        self._breadcrumb_container.clear()

        with self._breadcrumb_container:
            # Root button
            ui.button(
                "Root",
                on_click=lambda: self._select_path(""),
            ).props("flat dense").classes("text-theme-primary-light text-xs")

            if self.current_path:
                parts = self.current_path.split("/")
                accumulated_path = ""

                for i, part in enumerate(parts):
                    accumulated_path = (
                        f"{accumulated_path}/{part}" if accumulated_path else part
                    )

                    ui.label("/").classes("muted-text mx-1")

                    if i == len(parts) - 1:
                        ui.label(part).classes("text-sm font-medium")
                    else:
                        path_copy = accumulated_path
                        ui.button(
                            part,
                            on_click=lambda p=path_copy: self._select_path(p),
                        ).props("flat dense").classes("text-theme-primary-light text-xs")

    def _select_path(self, path: str) -> None:
        """Select and display a path"""
        self.current_path = path
        self._update_breadcrumb()
        self._build_tree()  # Rebuild to show selection
        self._load_path_content()

    def _load_path_content(self) -> None:
        """Load and display content for the current path"""
        if self._content_container is None:
            return

        self._content_container.clear()

        if not self.current_path:
            self._show_welcome_content()
            return

        # Get data for this path
        data = self._get_data_from_snapshot(self.current_path)

        with self._content_container:
            # Path info header
            with ui.row().classes("w-full items-center justify-between mb-4"):
                with ui.column().classes("gap-1"):
                    ui.label(self.current_path.split("/")[-1]).classes(
                        "text-xl font-bold"
                    )
                    self._path_label = ui.label(f"Path: {self.current_path}").classes(
                        "text-sm secondary-text font-mono"
                    )

                with ui.row().classes("gap-2"):
                    ui.button(
                        "Delete",
                        icon="delete",
                        on_click=lambda: self._confirm_delete(self.current_path),
                    ).props("flat color=negative").classes("text-red-400")

            # JSON Editor
            with (
                ui.card()
                .classes("w-full")
                .style("background: var(--color-bg-base); border: 1px solid var(--color-border-default);")
            ):
                with (
                    ui.row()
                    .classes("w-full items-center justify-between p-3")
                    .style("border-bottom: 1px solid var(--color-border-default);")
                ):
                    ui.label("JSON Data").classes("font-medium")
                    with ui.row().classes("gap-2"):
                        ui.button(
                            "Format",
                            icon="code",
                            on_click=self._format_json,
                        ).props("flat dense").classes("text-theme-primary-light")
                        ui.button(
                            "Reset",
                            icon="undo",
                            on_click=lambda: self._reset_editor(),
                        ).props("flat dense").classes("secondary-text")

                # Editor textarea
                json_str = json.dumps(data, indent=2, default=str) if data else "{}"

                self._json_editor = (
                    ui.textarea(value=json_str)
                    .classes("w-full font-mono")
                    .props("outlined rows=20")
                    .style(
                        "font-family: 'Fira Code', 'Consolas', monospace; "
                        "font-size: 13px; background: var(--color-bg-base);"
                    )
                )
                self._json_editor.on("update:model-value", self._on_editor_change)

                # Save button row
                with (
                    ui.row()
                    .classes("w-full justify-end p-3 gap-2")
                    .style("border-top: 1px solid var(--color-border-default);")
                ):
                    self._save_btn = ui.button(
                        "Save Changes",
                        icon="save",
                        on_click=self._save_current_path,
                    ).props("color=primary")
                    self._save_btn.disable()

    def _get_data_from_snapshot(self, path: str) -> Any:
        """Get data for a path from the snapshot"""
        # First try to get from pending changes
        if path in self.pending_changes:
            return self.pending_changes[path]

        # SQLite and MongoDB store one document per path; merged get_snapshot() can
        # disagree with get_data() when paths are prefix-related. Always read the
        # canonical document from the backend for those types.
        db_type = database_manager.get_config().database_type
        if db_type in ("sql", "mongodb"):
            return database_manager.get_data(path)

        # Firebase: navigate through snapshot (matches RTDB tree shape)
        parts = path.split("/")
        current = self.snapshot

        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return database_manager.get_data(path)

        return current

    def _on_editor_change(self, e) -> None:
        """Handle editor content change"""
        if self._save_btn:
            self._save_btn.enable()

    def _format_json(self) -> None:
        """Format the JSON in the editor"""
        if self._json_editor is None:
            return

        try:
            data = json.loads(self._json_editor.value)
            formatted = json.dumps(data, indent=2, default=str)
            self._json_editor.value = formatted
            notify("JSON formatted", type="positive")
        except json.JSONDecodeError as e:
            notify(f"Invalid JSON: {e}", type="negative")

    def _reset_editor(self) -> None:
        """Reset editor to original value"""
        if self._json_editor is None or not self.current_path:
            return

        data = self._get_data_from_snapshot(self.current_path)
        json_str = json.dumps(data, indent=2, default=str) if data else "{}"
        self._json_editor.value = json_str

        if self._save_btn:
            self._save_btn.disable()

        notify("Editor reset", type="info")

    def _save_current_path(self) -> None:
        """Save the current path data to database"""
        if self._json_editor is None or not self.current_path:
            return

        try:
            data = json.loads(self._json_editor.value)
        except json.JSONDecodeError as e:
            notify(f"Invalid JSON: {e}", type="negative")
            return

        # Save to database
        if database_manager.set_data(self.current_path, data):
            notify(f"Saved: {self.current_path}", type="positive")
            db_type = database_manager.get_config().database_type
            if db_type in ("sql", "mongodb"):
                self._load_snapshot()
                self._build_tree()
            else:
                self._set_data_in_snapshot(self.current_path, data)

            if self._save_btn:
                self._save_btn.disable()
        else:
            notify("Failed to save data", type="negative")

    def _set_data_in_snapshot(self, path: str, data: Any) -> None:
        """Update a path in the local snapshot"""
        parts = path.split("/")
        current = self.snapshot

        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                current[part] = data
            else:
                if part not in current:
                    current[part] = {}
                current = current[part]

    def _confirm_delete(self, path: str) -> None:
        """Show delete confirmation dialog"""
        with ui.dialog() as confirm_dialog:
            with ui.card().style("background: var(--color-bg-base); border: 1px solid var(--color-error);"):
                with ui.column().classes("p-4 gap-4"):
                    with ui.row().classes("items-center gap-2"):
                        ui.icon("warning", size="md").classes("text-red-400")
                        ui.label("Confirm Delete").classes(
                            "text-lg font-bold"
                        )

                    ui.label(f"Delete path: {path}?").classes("secondary-text")
                    ui.label("This action cannot be undone.").classes(
                        "text-red-400 text-sm"
                    )

                    with ui.row().classes("justify-end gap-2 mt-4"):
                        ui.button("Cancel", on_click=confirm_dialog.close).props(
                            "flat color=white"
                        )
                        ui.button(
                            "Delete",
                            on_click=lambda: self._execute_delete(path, confirm_dialog),
                        ).props("color=negative")

        confirm_dialog.open()

    def _execute_delete(self, path: str, dialog: ui.dialog) -> None:
        """Execute path deletion"""
        if database_manager.delete_data(path):
            notify(f"Deleted: {path}", type="positive")
            # Refresh snapshot
            self._load_snapshot()
            self._build_tree()
            self.current_path = ""
            self._update_breadcrumb()
            self._show_welcome_content()
        else:
            notify("Failed to delete", type="negative")

        dialog.close()

    def _show_create_dialog(self) -> None:
        """Show dialog to create a new path"""
        with ui.dialog() as create_dialog:
            with ui.card().style(
                "background: var(--color-bg-base); border: 1px solid var(--color-border-default); min-width: 400px;"
            ):
                with ui.column().classes("p-4 gap-4"):
                    ui.label("Create New Path").classes("text-lg font-bold")

                    path_input = (
                        ui.input(
                            label="Path",
                            placeholder="e.g., Settings/MyData",
                        )
                        .classes("w-full")
                        .props("outlined")
                    )

                    data_input = (
                        ui.textarea(
                            label="Initial JSON Data",
                            value="{}",
                        )
                        .classes("w-full font-mono")
                        .props("outlined rows=6")
                    )

                    with ui.row().classes("justify-end gap-2 mt-4"):
                        ui.button("Cancel", on_click=create_dialog.close).props(
                            "flat color=white"
                        )
                        ui.button(
                            "Create",
                            on_click=lambda: self._execute_create(
                                path_input.value,
                                data_input.value,
                                create_dialog,
                            ),
                        ).props("color=primary")

        create_dialog.open()

    def _execute_create(self, path: str, json_data: str, dialog: ui.dialog) -> None:
        """Execute path creation"""
        if not path or not path.strip():
            notify("Path cannot be empty", type="warning")
            return

        path = path.strip()
        if path.startswith("/"):
            path = path[1:]

        try:
            data = json.loads(json_data) if json_data.strip() else {}
        except json.JSONDecodeError as e:
            notify(f"Invalid JSON: {e}", type="negative")
            return

        if database_manager.set_data(path, data):
            notify(f"Created: {path}", type="positive")
            # Refresh snapshot
            self._load_snapshot()
            self._build_tree()
            self._select_path(path)
            dialog.close()
        else:
            notify("Failed to create path", type="negative")

    def _refresh_snapshot(self) -> None:
        """Refresh the database snapshot"""
        self._load_snapshot()
        self._build_tree()
        self._update_breadcrumb()

        if self.current_path:
            self._load_path_content()
        else:
            self._show_welcome_content()

        notify(f"Refreshed: {len(self.all_paths)} paths", type="positive")

    def _filter_tree(self, e) -> None:
        """Filter the tree based on search input"""
        filter_text = (
            e.args if isinstance(e.args, str) else (e.args[0] if e.args else "")
        )
        self._build_tree(filter_text)

    def _toggle_folder(self, path: str) -> None:
        """Toggle folder expansion (placeholder for future enhancement)"""
        # For now, folders auto-expand in the tree
        pass

    def _build_footer(self) -> None:
        """Build the footer status bar"""
        with (
            ui.row()
            .classes("w-full items-center justify-between p-2 px-4")
            .style("background: var(--color-bg-elevated); border-top: 1px solid var(--color-border-default);")
        ):
            self._status_label = ui.label(
                f"{len(self.all_paths)} paths loaded"
            ).classes("text-sm secondary-text")

            with ui.row().classes("gap-4 text-sm muted-text"):
                ui.label("Click path to view/edit")
                ui.label("•")
                ui.label("Ctrl+S to save")

    def _close_dialog(self) -> None:
        """Close the dialog"""
        if self.dialog:
            self.dialog.close()


class DatabaseTab:
    name = "Database"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.DatabaseSettings] = None
        self.ui_elements: Dict[str, Any] = {}

    def on_enter(self) -> None:
        # Refresh status when tab becomes active
        from nicegui import ui

        ui.timer(0.1, lambda: self._refresh_status(), once=True)

    def _refresh_status(self) -> None:
        """Refresh Database status display."""
        try:
            # Get current status from the database manager
            status_info = database_manager.get_connection_status()

            # Update status label and color
            if "status_label" in self.ui_elements:
                status_text = status_info.get("status", "Unknown")
                is_connected = status_info.get("is_connected", False)

                self.ui_elements["status_label"].set_text(status_text)

                # Update color based on status
                if is_connected:
                    self.ui_elements["status_label"].classes(
                        replace="font-semibold text-green-500"
                    )
                else:
                    self.ui_elements["status_label"].classes(
                        replace="font-semibold text-red-500"
                    )

            # Update database type label
            if "type_label" in self.ui_elements:
                db_type = status_info.get("database_type", "N/A")
                self.ui_elements["type_label"].set_text(db_type)

            # Update last check label
            if "last_check_label" in self.ui_elements:
                last_check = status_info.get("last_check", "Never")
                # Format the timestamp if it's an ISO string
                try:
                    if last_check != "Never":
                        from datetime import datetime

                        dt = datetime.fromisoformat(last_check.replace("Z", "+00:00"))
                        last_check = dt.strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, TypeError):
                    pass  # Keep original value if parsing fails

                self.ui_elements["last_check_label"].set_text(last_check)

            # Show specific Firebase configuration issues if present
            if status_info.get("database_type") == "Firebase" and not status_info.get(
                "is_connected", False
            ):
                firebase_issues = status_info.get("config_issues", [])
                if firebase_issues:
                    issue_text = "Firebase Configuration Issues:\n" + "\n".join(
                        f"• {issue}"
                        for issue in firebase_issues[:3]  # Limit to 3 issues
                    )
                    notify(issue_text, type="warning", timeout=8000)

        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(f"Error refreshing database status: {str(e)}", exc_info=True)
            # Set error status
            if "status_label" in self.ui_elements:
                self.ui_elements["status_label"].set_text("Status Error")
                self.ui_elements["status_label"].classes(
                    replace="font-semibold text-red-500"
                )

    def _test_connection(self) -> None:
        """Test database connection"""
        try:
            notify("Testing database connection...", type="info")

            if database_manager.test_connection():
                notify(
                    "Database connection successful!", type="positive", timeout=3000
                )
            else:
                notify(
                    "Database connection failed. Check configuration and logs.",
                    type="negative",
                    timeout=5000,
                )

            self._refresh_status()

        except Exception as e:
            logger.error(f"Error testing database connection: {str(e)}", exc_info=True)
            notify(f"Error testing connection: {str(e)}", type="negative")

    def _event_select_value(self, e: Any) -> Any:
        """Resolve NiceGUI select value from change events (value vs args)."""
        v = getattr(e, "value", None)
        if v is None and hasattr(e, "args") and e.args is not None:
            args = e.args
            if isinstance(args, (list, tuple)) and len(args) > 0:
                v = args[0]
            elif not isinstance(args, (list, tuple)):
                v = args
        return v

    def _show_migration_dialog(self) -> None:
        """Show database migration dialog (same flow as Settings → migrate)."""
        try:
            available_dbs = database_manager.get_available_databases()
            if not available_dbs:
                notify("No database backends available", type="negative")
                return

            with ui.dialog() as migration_dialog:
                with ui.card().classes("w-full max-w-lg").style(
                    "background: var(--color-bg-base); border: 1px solid var(--color-border-default);"
                ):
                    ui.label("Migrate database").classes("text-xl font-bold mb-2")
                    ui.label(
                        "Copies all paths from the source database to the target using "
                        "the connection details below. Configure those fields before starting."
                    ).classes("secondary-text mb-4")

                    with ui.row().classes("w-full items-center mb-2"):
                        ui.label("From:").classes("w-20")
                        source_select = (
                            ui.select(
                                options=available_dbs,
                                value=(
                                    database_manager.get_config().database_type
                                    if database_manager.get_config().database_type
                                    in available_dbs
                                    else available_dbs[0]
                                ),
                            ).classes("flex-1")
                        )

                    with ui.row().classes("w-full items-center mb-4"):
                        ui.label("To:").classes("w-20")
                        target_select = ui.select(
                            options=available_dbs,
                            value=(
                                available_dbs[1]
                                if len(available_dbs) > 1
                                else available_dbs[0]
                            ),
                        ).classes("flex-1")

                    ui.label(
                        "Existing data at the same paths on the target may be overwritten."
                    ).classes("text-orange-600 mb-4")

                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button("Cancel", on_click=migration_dialog.close).props(
                            "outline"
                        )
                        ui.button(
                            "Start migration",
                            on_click=lambda: self._start_migration(
                                source_select.value or "sql",
                                target_select.value or "sql",
                                migration_dialog,
                            ),
                        ).props("color=primary")

            migration_dialog.open()

        except Exception as e:
            logger.error(f"Error showing migration dialog: {str(e)}", exc_info=True)
            notify(f"Error opening migration dialog: {str(e)}", type="negative")

    def _start_migration(self, source_type: str, target_type: str, dialog: Any) -> None:
        """Run migration in a background thread; update config when complete."""
        try:
            if source_type == target_type:
                notify(
                    "Source and target must be different database types",
                    type="warning",
                )
                return

            dialog.close()
            notify("Starting database migration…", type="info")

            migration_status: Dict[str, Any] = {
                "completed": False,
                "success": False,
                "error": None,
                "notified": False,
            }

            buf = self.buffer

            def migration_thread() -> None:
                try:
                    current_config = database_manager.get_config()

                    if source_type == current_config.database_type:
                        source_config = replace(current_config)
                    elif source_type == "firebase":
                        source_config = DatabaseConfig(
                            database_type="firebase",
                            firebase_service_account_path=buf.firebase_service_account_path,
                            firebase_database_url=buf.firebase_database_url,
                            streamer_name="mycelian",
                        )
                    elif source_type == "sql":
                        source_config = DatabaseConfig(
                            database_type="sql",
                            sql_database_path=buf.sql_database_path,
                            streamer_name="mycelian",
                        )
                    else:
                        source_config = DatabaseConfig(
                            database_type="mongodb",
                            mongodb_connection_string=buf.mongodb_connection_string,
                            mongodb_database_name=buf.mongodb_database_name,
                            streamer_name="mycelian",
                        )

                    if target_type == "firebase":
                        target_config = DatabaseConfig(
                            database_type="firebase",
                            firebase_service_account_path=buf.firebase_service_account_path,
                            firebase_database_url=buf.firebase_database_url,
                            streamer_name="mycelian",
                        )
                    elif target_type == "sql":
                        target_config = DatabaseConfig(
                            database_type="sql",
                            sql_database_path=buf.sql_database_path,
                            streamer_name="mycelian",
                        )
                    else:
                        target_config = DatabaseConfig(
                            database_type="mongodb",
                            mongodb_connection_string=buf.mongodb_connection_string,
                            mongodb_database_name=buf.mongodb_database_name,
                            streamer_name="mycelian",
                        )

                    ok = database_manager.migrate_data(source_config, target_config)
                    if ok:
                        prior_type = buf.database_type
                        snapshot_before_switch = replace(database_manager.get_config())
                        switch_cfg = replace(
                            DatabaseConfig(**buf.__dict__, streamer_name="mycelian"),
                            database_type=target_type,
                        )
                        if database_manager.update_config(**switch_cfg.__dict__):
                            buf.database_type = target_type
                            if not config_manager.update_database_config(**buf.__dict__):
                                logger.error(
                                    "Migration OK but failed to write config.json"
                                )
                            migration_status["success"] = True
                        else:
                            buf.database_type = prior_type
                            database_manager.initialize(snapshot_before_switch)
                            migration_status["error"] = (
                                "Data was copied but the app could not switch to the "
                                "target database. Check credentials and logs."
                            )
                    else:
                        migration_status["error"] = (
                            f"Migration {source_type} → {target_type} failed "
                            "(see logs)"
                        )
                    migration_status["completed"] = True
                except Exception as ex:
                    logger.error(f"Migration thread error: {ex}", exc_info=True)
                    migration_status["completed"] = True
                    migration_status["error"] = str(ex)

            threading.Thread(target=migration_thread, daemon=True).start()

            check_count = {"n": 0}
            max_checks = 240

            def poll() -> None:
                check_count["n"] += 1
                if check_count["n"] >= max_checks:
                    if not migration_status["notified"]:
                        migration_status["notified"] = True
                        notify("Migration timed out; check logs.", type="warning")
                    return
                if migration_status["completed"] and not migration_status["notified"]:
                    migration_status["notified"] = True
                    if migration_status["success"]:
                        notify(
                            f"Migration to {target_type} finished successfully.",
                            type="positive",
                            timeout=5000,
                        )
                        self._load_from_config()
                        for key, element in self.ui_elements.items():
                            if hasattr(element, "value") and hasattr(self.buffer, key):
                                element.value = getattr(self.buffer, key)
                        self.dirty = False
                    else:
                        notify(
                            migration_status.get("error", "Migration failed"),
                            type="negative",
                            timeout=8000,
                        )
                    self._refresh_status()
                    return
                if not migration_status["completed"]:
                    ui.timer(0.5, poll, once=True)

            ui.timer(0.5, poll, once=True)

        except Exception as e:
            logger.error(f"Error starting migration: {e}", exc_info=True)
            notify(f"Error starting migration: {e}", type="negative")

    def _show_data_viewer_dialog(self) -> None:
        """Show database data viewer and editor dialog"""
        try:
            # Create the viewer instance
            viewer = DatabaseViewer()
            viewer.show()

        except Exception as e:
            logger.error(f"Error showing data viewer dialog: {str(e)}", exc_info=True)
            notify(f"Error opening data viewer dialog: {str(e)}", type="negative")

    def _validate_path_name(self, path: str) -> bool:
        """Validate data path name format"""
        if not path or not path.strip():
            return False

        # Basic validation: no leading/trailing slashes, no empty segments
        if path.startswith("/") or path.endswith("/"):
            return False

        segments = path.split("/")
        if any(not segment.strip() for segment in segments):
            return False

        # Should not start with "category_" (reserved for internal use)
        if path.startswith("category_"):
            return False

        return True

    def _validate_json(self, value: str) -> bool:
        """Validate JSON format"""
        if not value.strip():
            return True  # Empty is allowed
        try:
            import json

            json.loads(value)
            return True
        except json.JSONDecodeError:
            return False

    def on_exit(self) -> None:
        pass

    def build(self, parent_container) -> None:
        self._load_from_config()
        with parent_container:
            with ui.card().classes("content-section w-full"):
                ui.label("Database Configuration").classes("text-xl font-bold mb-4")

                with ui.column().classes("w-full gap-4"):
                    ui.label("Connection Status").classes("text-lg font-semibold")
                    with ui.row().classes("w-full items-center"):
                        ui.label("Status:").classes("w-40")
                        self.ui_elements["status_label"] = ui.label(
                            "Loading..."
                        ).classes("font-semibold")

                    with ui.row().classes("w-full items-center"):
                        ui.label("Database Type:").classes("w-40")
                        self.ui_elements["type_label"] = ui.label("N/A").classes(
                            "font-semibold"
                        )

                    with ui.row().classes("w-full items-center"):
                        ui.label("Last Check:").classes("w-40")
                        self.ui_elements["last_check_label"] = ui.label(
                            "Never"
                        ).classes("secondary-text")

                    ui.separator().classes("divider")

                    # Connection controls
                    ui.label("Connection Controls").classes("text-lg font-semibold")
                    with ui.row().classes("w-full gap-2"):
                        self.ui_elements["test_button"] = ui.button(
                            "Test Connection",
                            on_click=self._test_connection,
                        ).props("icon=wifi_tethering outline")

                        self.ui_elements["migrate_button"] = ui.button(
                            "Migrate Database",
                            on_click=self._show_migration_dialog,
                        ).props("icon=sync_alt outline")

                        self.ui_elements["refresh_button"] = ui.button(
                            "Refresh Status",
                            on_click=self._refresh_status,
                        ).props("icon=refresh outline")

                        self.ui_elements["view_data_button"] = ui.button(
                            "View Data",
                            on_click=self._show_data_viewer_dialog,
                        ).props("icon=visibility outline")

                    ui.separator().classes("divider")

                    # Configuration
                    ui.label("Configuration").classes("text-lg font-semibold")
                    with ui.row().classes("w-full items-center"):
                        ui.label("Database Type:").classes("w-40")
                        self.ui_elements["database_type"] = (
                            ui.select(
                                options=["sql", "firebase", "mongodb"],
                                value=self.buffer.database_type,
                            )
                            .classes("w-48")
                            .on(
                                "change",
                                lambda e: self._set(
                                    "database_type",
                                    self._event_select_value(e)
                                    or self.ui_elements["database_type"].value,
                                ),
                            )
                        )

                    # SQL path
                    with ui.row().classes("w-full items-center"):
                        ui.label("SQLite Path:").classes("w-40")
                        self.ui_elements["sql_database_path"] = (
                            ui.input(
                                value=self.buffer.sql_database_path,
                                placeholder="mycelian.db",
                            )
                            .classes("flex-1")
                            .on(
                                "change",
                                lambda e: self._set(
                                    "sql_database_path",
                                    getattr(e, "args", [getattr(e, "value", "")])[0]
                                    or "mycelian.db",
                                ),
                            )
                        )

                    # Firebase
                    with ui.row().classes("w-full items-center"):
                        ui.label("Firebase Key:").classes("w-40")
                        self.ui_elements["firebase_service_account_path"] = (
                            ui.input(
                                value=self.buffer.firebase_service_account_path,
                                placeholder="ServiceAccountKey.json",
                            )
                            .classes("flex-1")
                            .on(
                                "change",
                                lambda e: self._set(
                                    "firebase_service_account_path",
                                    getattr(e, "args", [getattr(e, "value", "")])[0]
                                    or "",
                                ),
                            )
                        )
                    with ui.row().classes("w-full items-center"):
                        ui.label("Firebase URL:").classes("w-40")
                        self.ui_elements["firebase_database_url"] = (
                            ui.input(
                                value=self.buffer.firebase_database_url,
                                placeholder="https://...firebaseio.com/",
                            )
                            .classes("flex-1")
                            .on(
                                "change",
                                lambda e: self._set(
                                    "firebase_database_url",
                                    getattr(e, "args", [getattr(e, "value", "")])[0]
                                    or "",
                                ),
                            )
                        )

                    # Mongo
                    with ui.row().classes("w-full items-center"):
                        ui.label("Mongo URI:").classes("w-40")
                        self.ui_elements["mongodb_connection_string"] = (
                            ui.input(
                                value=self.buffer.mongodb_connection_string,
                                placeholder="mongodb://localhost:27017/",
                            )
                            .classes("flex-1")
                            .on(
                                "change",
                                lambda e: self._set(
                                    "mongodb_connection_string",
                                    getattr(e, "args", [getattr(e, "value", "")])[0]
                                    or "",
                                ),
                            )
                        )
                    with ui.row().classes("w-full items-center"):
                        ui.label("Mongo DB Name:").classes("w-40")
                        self.ui_elements["mongodb_database_name"] = (
                            ui.input(
                                value=self.buffer.mongodb_database_name,
                                placeholder="mycelian",
                            )
                            .classes("w-48")
                            .on(
                                "change",
                                lambda e: self._set(
                                    "mongodb_database_name",
                                    getattr(e, "args", [getattr(e, "value", "")])[0]
                                    or "mycelian",
                                ),
                            )
                        )

                    # Common
                    with ui.row().classes("w-full items-center"):
                        ui.label("Timeout (s):").classes("w-40")
                        self.ui_elements["connection_timeout"] = (
                            ui.number(
                                value=self.buffer.connection_timeout,
                                min=5,
                                max=300,
                                step=5,
                            )
                            .classes("w-24")
                            .on(
                                "change",
                                lambda e: self._set(
                                    "connection_timeout",
                                    int(
                                        getattr(e, "args", [getattr(e, "value", 30)])[0]
                                        or 30
                                    ),
                                ),
                            )
                        )
                    with ui.row().classes("w-full items-center"):
                        ui.label("Retry Attempts:").classes("w-40")
                        self.ui_elements["retry_attempts"] = (
                            ui.number(
                                value=self.buffer.retry_attempts, min=1, max=10, step=1
                            )
                            .classes("w-24")
                            .on(
                                "change",
                                lambda e: self._set(
                                    "retry_attempts",
                                    int(
                                        getattr(e, "args", [getattr(e, "value", 3)])[0]
                                        or 3
                                    ),
                                ),
                            )
                        )

                    with ui.row().classes("justify-end gap-2 mt-3"):
                        ui.button("Discard", on_click=self.discard).props("outline")
                        ui.button("Save", on_click=self.save).props("color=primary")

    def _load_from_config(self) -> None:
        cfg = config_manager.get_database_config()
        # fallback defaults similar to settings
        from ... import dataobjects as _d

        self.buffer = _d.DatabaseSettings(
            database_type=cfg.get("database_type", "sql"),
            sql_database_path=cfg.get("sql_database_path", "mycelian.db"),
            firebase_service_account_path=cfg.get(
                "firebase_service_account_path", "ServiceAccountKey.json"
            ),
            firebase_database_url=cfg.get(
                "firebase_database_url",
                "https://twitch-api-bot-default-rtdb.firebaseio.com/",
            ),
            mongodb_connection_string=cfg.get(
                "mongodb_connection_string", "mongodb://localhost:27017/"
            ),
            mongodb_database_name=cfg.get("mongodb_database_name", "mycelian"),
            connection_timeout=cfg.get("connection_timeout", 30),
            retry_attempts=cfg.get("retry_attempts", 3),
        )
        self.dirty = False

    def _set(self, field: str, value) -> None:
        if getattr(self.buffer, field) != value:
            setattr(self.buffer, field, value)
            self.dirty = True

    def save(self) -> None:
        if not self.buffer:
            return
        # apply to config manager
        ok = config_manager.update_database_config(**self.buffer.__dict__)
        if not ok:
            notify("Failed to save database config", type="negative")
            return

        # update runtime database manager
        cfg = DatabaseConfig(**self.buffer.__dict__, streamer_name="mycelian")
        if database_manager.update_config(**cfg.__dict__):
            notify("Database settings saved", type="positive")
            self.dirty = False
        else:
            notify("Failed to apply database settings", type="negative")

    def discard(self) -> None:
        self._load_from_config()
        for key, element in self.ui_elements.items():
            if hasattr(element, "value") and hasattr(self.buffer, key):
                element.value = getattr(self.buffer, key)
        self.dirty = False

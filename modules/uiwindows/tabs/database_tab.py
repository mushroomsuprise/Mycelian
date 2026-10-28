# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from dataclasses import replace
from datetime import datetime
from typing import Dict, Any, Optional, List, Set, Tuple
from urllib.parse import urlparse

from nicegui import run, ui
from ...notification_engine import nav_actions_settings, notify, notify_progress
from ...ui_buttons import outline_button, primary_button
from ...ui_timer import layout_schedule
from .integration_ui import (
    AccountCard,
    IntegrationPage,
    esc,
    field,
    field_label,
    fields,
    hint,
    icon_html,
    inject_integration_styles,
    pill_html,
    row,
    section,
    tool_row,
    copy_to_clipboard,
)

from ... import dataobjects
from ...config_manager import config_manager
from ...database_manager import (
    DatabaseConfig,
    clear_last_write_problem,
    database_label,
    database_manager,
    last_write_problem,
    quiet_notifications,
)

logger = logging.getLogger(__name__)

_DB_NAV = nav_actions_settings("Database")

# Friendly names for the database types (config values: sql, firebase, mongodb).
_DB_SHORT = {"sql": "Local file", "firebase": "Firebase", "mongodb": "MongoDB"}
_DB_NAMES = {"sql": "Local file (SQLite)", "firebase": "Firebase", "mongodb": "MongoDB"}
_DB_ICONS = {"sql": "folder", "firebase": "cloud", "mongodb": "dns"}
_DB_CHOICES = (
    ("sql", "SQLite on this computer. Nothing to set up."),
    ("firebase", "Google's Realtime Database. Use the same data on several PCs."),
    ("mongodb", "Your own MongoDB server or Atlas cluster."),
)

# Explorer tree: rows are plain HTML in one element; a click reports the row's kind and path.
_TREE_CLICK_JS = (
    "(e) => { const t = e.target.closest('[data-k]');"
    " if (t) emit({k: t.dataset.k, p: t.dataset.p}); }"
)
_TREE_FOLDER_LIMIT = 300  # rows per folder before a "Show more" row
_TREE_ROW_LIMIT = 2000  # rows in the whole tree
_SAVE_KEY_JS = (
    "(e) => { if ((e.metaKey || e.ctrlKey) && !e.altKey && (e.key === 's' || e.key === 'S'))"
    " { e.preventDefault(); emit(); } }"
)
_ACTIVATE_KEY_JS = "(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); emit(); } }"


def _reported_write(fn, *args) -> tuple:
    """Run a database write whose result this tab reports itself: ``(ok, reason)``.

    The database layer's generic "couldn't save" notification is silenced so
    the user gets one message that names the path.
    """
    with quiet_notifications():
        clear_last_write_problem()
        ok = bool(fn(*args))
        problem = last_write_problem()
    status, reason = problem or (("ok", "") if ok else ("failed", ""))
    if ok or status == "superseded":
        return True, ""
    if status == "timeout":
        return False, f"The database didn't answer in time ({reason})."
    if status == "down":
        return False, "The database isn't connected."
    return False, reason or "The database rejected the change (see the log)."


def _delete_with_children(path: str) -> bool:
    """Delete ``path`` and every path under it, as the Explorer's delete dialog says.

    Firebase removes a node's children itself; SQLite and MongoDB keep each path as
    its own row, so the paths under it are deleted first (deepest first).
    """
    if database_manager.get_config().database_type != "firebase":
        prefix = path.strip("/") + "/"
        children = [p for p in database_manager.get_all_paths() if p.startswith(prefix)]
        for child in sorted(children, reverse=True):
            if not database_manager.delete_data(child):
                return False
    return database_manager.delete_data(path)


def _notify_write_failed(title: str, reason: str) -> None:
    notify(
        reason,
        type="error",
        source="database",
        title=title,
        hint="Check the connection in Settings → Database, then try again.",
        actions=_DB_NAV,
    )


def _notify_invalid_json(e: json.JSONDecodeError) -> None:
    notify(
        f"Line {e.lineno}, column {e.colno}: {e.msg}.",
        type="warning",
        source="database",
        title="That isn't valid JSON",
        hint="Fix it and try again.",
    )


def _format_bytes(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _ago(value: Any) -> str:
    """'just now', '12 s ago', '3 min ago'... from an ISO timestamp; '' if unreadable."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    secs = max(0, int((now - dt).total_seconds()))
    if secs < 5:
        return "just now"
    if secs < 60:
        return f"{secs} s ago"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    return dt.strftime("%Y-%m-%d %H:%M")


def _live_type() -> str:
    cfg = database_manager.get_config()
    return (getattr(cfg, "database_type", "") or "sql") if cfg else "sql"


def _resolved_sql_path(path: str) -> str:
    from ...path_utils import get_data_path

    return path if not path or os.path.isabs(path) else get_data_path(path)


def _live_where(cfg: Any) -> str:
    """Short location of the database in use (never the Mongo connection string)."""
    if cfg is None:
        return ""
    if cfg.database_type == "sql":
        return os.path.basename(cfg.sql_database_path or "")
    if cfg.database_type == "firebase":
        url = cfg.firebase_database_url or ""
        return urlparse(url).netloc or url
    return cfg.mongodb_database_name or ""


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _value_text(data: Any) -> str:
    return json.dumps(data, indent=2, default=str) if data is not None else "{}"


def _describe_value(data: Any) -> str:
    if isinstance(data, dict):
        return f"Object · {len(data)} key{'' if len(data) == 1 else 's'}"
    if isinstance(data, list):
        return f"Array · {len(data)} item{'' if len(data) == 1 else 's'}"
    if isinstance(data, str):
        return f"Text · {len(data)} character{'' if len(data) == 1 else 's'}"
    if isinstance(data, bool):
        return "True / false"
    if isinstance(data, (int, float)):
        return "Number"
    return "Empty"


def _chips_html(data: Any) -> str:
    try:
        size = len(json.dumps(data, default=str, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        size = 0
    chips = [_describe_value(data)] + ([_format_bytes(size)] if size else [])
    return "".join(f'<span class="dbx-chip">{esc(c)}</span>' for c in chips)


class DatabaseViewer:
    """A file-browser-like database viewer and editor dialog"""

    def __init__(self) -> None:
        self.dialog: Optional[ui.dialog] = None
        self.snapshot: Dict[str, Any] = {}
        self.all_paths: List[str] = []
        self.current_path: str = ""
        self.pending_changes: Dict[str, Any] = {}
        self._loaded_ok: bool = True

        # Tree state: built once per snapshot; only expanded folders are rendered.
        self._tree_cache: Optional[Dict[str, Any]] = None
        self._folder_count: int = 0
        self._expanded: Set[str] = set()
        self._show_all: Set[str] = set()
        self._search_collapsed: Set[str] = set()
        self._filter_text: str = ""
        self._filter_generation: int = 0

        # Editor state
        self._original_text: str = ""
        self._edit_generation: int = 0
        self._cm_theme: str = "vscodeDark"

        # UI element references
        self._tree_html: Optional[ui.html] = None
        self._tree_foot: Optional[ui.label] = None
        self._pill: Optional[ui.html] = None
        self._search_input: Optional[ui.input] = None
        self._content_container: Optional[ui.element] = None
        self._breadcrumb_container: Optional[ui.element] = None
        self._path_label: Optional[ui.label] = None
        self._meta_html: Optional[ui.html] = None
        self._json_editor: Optional[ui.codemirror] = None
        self._valid_html: Optional[ui.html] = None
        self._dirty_html: Optional[ui.html] = None
        self._save_btn: Optional[ui.button] = None

    async def show(self) -> None:
        """Show the database viewer dialog"""
        await self._load_snapshot()
        await self._detect_editor_theme()
        inject_integration_styles()

        with ui.dialog().props("maximized persistent") as self.dialog:
            # Focusable so Ctrl/Cmd+S works wherever the user clicked inside the explorer;
            # the listener lives on this element, so it goes away with the dialog.
            root = (
                ui.element("div")
                .classes("itg")
                .props("tabindex=-1")
                .style("height: 100%; outline: none; background: var(--color-bg-base);")
            )
            root.on("keydown", self._on_save_key, js_handler=_SAVE_KEY_JS)
            with root, ui.element("div").classes("dbx"):
                self._build_header()
                with ui.element("div").classes("dbx-main"):
                    self._build_sidebar()
                    self._build_content_area()

        self.dialog.open()

    async def _detect_editor_theme(self) -> None:
        """Pick the code editor theme from the page's dark/light mode."""
        try:
            dark = await ui.run_javascript("document.body.classList.contains('body--dark')", timeout=2.0)
        except Exception:
            try:
                from ...theme_manager import get_theme_manager

                dark = get_theme_manager().get_theme().theme_type != "light"
            except Exception:
                dark = True
        self._cm_theme = "vscodeDark" if dark else "githubLight"

    def _fetch_snapshot(self) -> tuple:
        snapshot, error = database_manager.get_snapshot_checked()
        paths = database_manager.get_paths_from_snapshot(snapshot) if not error else []
        return snapshot, paths, error

    async def _load_snapshot(self) -> bool:
        """Load a complete snapshot of the database off the NiceGUI thread.

        On failure the explorer is left empty and the user is told why.
        """
        try:
            snapshot, paths, error = await run.io_bound(self._fetch_snapshot)
        except Exception as e:
            logger.error(f"Error loading database snapshot: {e}", exc_info=True)
            snapshot, paths, error = {}, [], str(e)
        self.snapshot = snapshot
        self.all_paths = paths
        self._tree_cache = None
        self._loaded_ok = not error
        self._update_pill()
        if error:
            logger.error("Database snapshot failed: %s", error)
            notify(
                "The explorer can't show anything until it can read the database.",
                type="error",
                source="database",
                title="Couldn't read the database",
                hint="Check the connection in Settings → Database, then press Refresh.",
                details=error,
                dedupe_key="db:explorer_read",
                dedupe_cooldown_sec=30,
                actions=nav_actions_settings("Database"),
            )
            return False
        logger.info(f"Database snapshot loaded: {len(self.all_paths)} paths found")
        return True

    def _build_header(self) -> None:
        """Build the dialog header"""
        with ui.element("div").classes("dbx-head"):
            ui.html(
                f'<span class="itg-tile">{icon_html("travel_explore")}</span>'
                '<span><div class="itg-title-name">Database Explorer</div>'
                '<div class="itg-title-sub">Browse and edit what Mycelian has stored</div></span>',
                sanitize=False,
            ).classes("itg-title")
            self._pill = ui.html("", sanitize=False)
            self._update_pill()
            ui.element("div").classes("itg-spacer")
            outline_button("Refresh", self._refresh_snapshot, icon="refresh").props("dense").classes("px-3")
            outline_button("New item", self._show_create_dialog, icon="add").props("dense").classes("px-3")
            ui.button(icon="close", on_click=self._close_dialog).props("flat round dense").classes("itg-ibtn").tooltip(
                "Close"
            )

    def _update_pill(self) -> None:
        """Header pill naming the database in use; red when it couldn't be read."""
        if self._pill is None:
            return
        cfg = database_manager.get_config()
        db_type = _live_type()
        where = _live_where(cfg)
        text = _DB_SHORT.get(db_type, db_type) + (f" · {where}" if where else "")
        self._pill.set_content(pill_html("ok" if self._loaded_ok else "error", text))

    def _build_sidebar(self) -> None:
        """Build the left sidebar with tree navigation"""
        with ui.element("div").classes("dbx-side"):
            with ui.element("div").classes("dbx-search"):
                self._search_input = (
                    ui.input(placeholder="Search paths").props("outlined dense clearable").classes("w-full")
                )
                with self._search_input.add_slot("prepend"):
                    ui.icon("search", size="18px")
                self._search_input.on("update:model-value", self._filter_tree)

            self._tree_html = (
                ui.html("", sanitize=False).classes("dbx-tree").props('role=tree aria-label="Stored items"')
            )
            self._tree_html.on("click", self._on_tree_click, js_handler=_TREE_CLICK_JS)
            self._tree_foot = ui.label("").classes("dbx-side-foot")
            self._build_tree()

    def _tree_structure(self) -> Dict[str, Any]:
        """The nested tree for the current snapshot (cached until the next load)."""
        if self._tree_cache is None:
            tree = self._build_tree_structure()
            _leaves, self._folder_count = self._annotate_counts(tree)
            self._tree_cache = tree
        return self._tree_cache

    @classmethod
    def _annotate_counts(cls, level: Dict[str, Any]) -> Tuple[int, int]:
        """Store each node's item count (items below it); return (items, folders) in ``level``."""
        leaves = folders = 0
        for node in level.values():
            children = node["__children__"]
            child_leaves, child_folders = cls._annotate_counts(children) if children else (0, 0)
            node["__count__"] = child_leaves
            leaves += child_leaves + (1 if node.get("__is_leaf__") else 0)
            folders += child_folders + (1 if children else 0)
        return leaves, folders

    def _build_tree(self, filter_text: Optional[str] = None) -> None:
        """Render the tree (expanded folders only; while searching, every match is shown)."""
        if filter_text is not None:
            if filter_text != self._filter_text:
                self._search_collapsed.clear()
            self._filter_text = filter_text
        if self._tree_html is None:
            return

        tree = self._tree_structure()
        if self._tree_foot is not None:
            items = len(self.all_paths)
            self._tree_foot.set_text(
                f"{items} item{'' if items == 1 else 's'} · "
                f"{self._folder_count} folder{'' if self._folder_count == 1 else 's'}"
            )
        if not tree:
            note = "Nothing stored yet" if self._loaded_ok else "Couldn't read the database"
            self._tree_html.set_content(f'<div class="dbx-tree-note">{note}</div>')
            return

        query = self._filter_text.strip().lower()
        visible: Optional[Set[str]] = None
        if query:
            visible = set()
            for path in self.all_paths:
                if query in path.lower():
                    parts = path.split("/")
                    for i in range(1, len(parts) + 1):
                        visible.add("/".join(parts[:i]))
        rows: List[str] = []
        budget = [_TREE_ROW_LIMIT]
        self._render_tree_level(tree, "", rows, visible, 0, budget)
        if query and not rows:
            rows.append(f'<div class="dbx-tree-note">Nothing matches “{esc(self._filter_text.strip())}”.</div>')
        elif budget[0] <= 0:
            rows.append(
                f'<div class="dbx-tree-note">Showing the first {_TREE_ROW_LIMIT:,} rows. '
                "Search to narrow it down.</div>"
            )
        self._tree_html.set_content("".join(rows))

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
        rows: List[str],
        visible: Optional[Set[str]],
        depth: int,
        budget: List[int],
    ) -> None:
        """Append the HTML rows for one level (and its open folders) to ``rows``."""
        names = sorted(level)
        if visible is not None:
            names = [n for n in names if (f"{parent_path}/{n}" if parent_path else n) in visible]
        limit = None if (visible is not None or parent_path in self._show_all) else _TREE_FOLDER_LIMIT
        pad = 8 + depth * 18
        for index, name in enumerate(names):
            if budget[0] <= 0:
                return
            if limit is not None and index >= limit:
                rows.append(
                    f'<div class="dbx-node is-more" data-k="m" data-p="{esc(parent_path)}" '
                    f'style="padding-left:{pad + 22}px">Show {len(names) - index:,} more…</div>'
                )
                return
            budget[0] -= 1
            node = level[name]
            full_path = f"{parent_path}/{name}" if parent_path else name
            children = node["__children__"]
            leaf_path = node.get("__path__")
            selected = " is-on" if leaf_path is not None and leaf_path == self.current_path else ""
            if children:
                if visible is not None:
                    is_open = full_path not in self._search_collapsed
                else:
                    is_open = full_path in self._expanded
                # A folder that is also an item: the row opens the item, the chevron folds.
                row_kind, row_path = ("s", leaf_path) if leaf_path is not None else ("t", full_path)
                rows.append(
                    f'<div class="dbx-node{selected}" role="treeitem" aria-expanded="{"true" if is_open else "false"}" '
                    f'data-k="{row_kind}" data-p="{esc(row_path)}" title="{esc(full_path)}" style="padding-left:{pad}px">'
                    f'<span class="itg-icon chev" data-k="t" data-p="{esc(full_path)}">'
                    f'{"expand_more" if is_open else "chevron_right"}</span>'
                    f'<span class="itg-icon fold">{"folder_open" if is_open else "folder"}</span>'
                    f'<span class="nm">{esc(name)}</span><span class="n">{node.get("__count__", 0):,}</span></div>'
                )
                if is_open:
                    self._render_tree_level(children, full_path, rows, visible, depth + 1, budget)
            else:
                path = leaf_path if leaf_path is not None else full_path
                rows.append(
                    f'<div class="dbx-node{selected}" role="treeitem" data-k="s" data-p="{esc(path)}" '
                    f'title="{esc(path)}" style="padding-left:{pad + 22}px">'
                    f'<span class="itg-icon leaf">data_object</span><span class="nm">{esc(name)}</span></div>'
                )

    def _on_tree_click(self, e: Any) -> None:
        args = e.args if isinstance(e.args, dict) else {}
        kind, path = args.get("k"), args.get("p")
        if path is None:
            return
        if kind == "t":
            self._toggle_folder(path)
        elif kind == "s":
            self._select_path(path)
        elif kind == "m":
            self._show_all.add(path)
            self._build_tree()

    def _build_content_area(self) -> None:
        """Build the main content area"""
        with ui.element("div").classes("dbx-content"):
            self._breadcrumb_container = ui.element("div").classes("dbx-crumbs")
            self._update_breadcrumb()
            self._content_container = ui.element("div").classes("dbx-body")
            self._show_welcome_content()

    def _show_welcome_content(self) -> None:
        """Show welcome/empty state content"""
        if self._content_container is None:
            return

        self._content_container.clear()
        self._json_editor = None
        self._save_btn = None

        with self._content_container:
            with ui.element("div").classes("dbx-empty"):
                ui.html(icon_html("travel_explore" if self._loaded_ok else "cloud_off"), sanitize=False)
                if not self._loaded_ok:
                    ui.label("Couldn't read the database").classes("dbx-empty-title")
                    ui.label("Check the connection in Settings → Database, then press Refresh.").classes(
                        "dbx-empty-sub"
                    )
                elif not self.all_paths:
                    ui.label("Nothing stored yet").classes("dbx-empty-title")
                    ui.label("Use New item to add something.").classes("dbx-empty-sub")
                else:
                    self._tree_structure()
                    items = len(self.all_paths)
                    ui.label("Pick an item on the left").classes("dbx-empty-title")
                    ui.label(
                        f"{items} item{'' if items == 1 else 's'} in "
                        f"{self._folder_count} folder{'' if self._folder_count == 1 else 's'}"
                    ).classes("dbx-empty-sub")

    def _update_breadcrumb(self) -> None:
        """Update the breadcrumb navigation"""
        if self._breadcrumb_container is None:
            return

        self._breadcrumb_container.clear()

        with self._breadcrumb_container:
            ui.html(icon_html("home"), sanitize=False)
            root = ui.label("Root").classes("dbx-crumb" if self.current_path else "dbx-crumb-cur")
            if self.current_path:
                root.on("click", lambda: self._select_path(""))

                parts = self.current_path.split("/")
                accumulated_path = ""

                for i, part in enumerate(parts):
                    accumulated_path = (
                        f"{accumulated_path}/{part}" if accumulated_path else part
                    )

                    ui.label("/").classes("dbx-crumb-sep")

                    if i == len(parts) - 1:
                        ui.label(part).classes("dbx-crumb-cur")
                    else:
                        crumb = ui.label(part).classes("dbx-crumb")
                        crumb.on("click", lambda p=accumulated_path: self._select_path(p))

    def _select_path(self, path: str) -> None:
        """Select and display a path"""
        self.current_path = path
        if path:
            parts = path.split("/")
            self._expanded.update("/".join(parts[:i]) for i in range(1, len(parts)))
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
        json_str = _value_text(data)
        self._original_text = json_str
        path = self.current_path

        with self._content_container:
            with ui.element("div").classes("dbx-item"):
                with ui.element("div").classes("flex-1").style("min-width: 0"):
                    ui.label(path.split("/")[-1]).classes("dbx-item-name")
                    with ui.element("div").classes("dbx-item-path"):
                        self._path_label = ui.label(path).classes("dbx-path-text")
                        ui.button(
                            icon="content_copy",
                            on_click=lambda: copy_to_clipboard(path, "Path copied"),
                        ).props("flat round dense size=xs").classes("itg-ibtn").tooltip("Copy path")
                    self._meta_html = ui.html(_chips_html(data), sanitize=False).classes("dbx-item-meta")
                ui.button(
                    "Delete",
                    icon="delete_outline",
                    on_click=lambda: self._confirm_delete(self.current_path),
                ).props("flat dense no-caps").classes("dbx-del px-2")

            with ui.element("div").classes("dbx-editor"):
                with ui.element("div").classes("dbx-editor-bar"):
                    ui.label("JSON").classes("dbx-bar-label")
                    self._valid_html = ui.html("", sanitize=False).classes("dbx-valid")
                    ui.element("div").classes("itg-spacer")
                    ui.button("Format", icon="data_object", on_click=self._format_json).props(
                        "flat dense no-caps"
                    ).classes("itg-ibtn")
                    ui.button("Undo changes", icon="undo", on_click=lambda: self._reset_editor()).props(
                        "flat dense no-caps"
                    ).classes("itg-ibtn")

                self._json_editor = ui.codemirror(
                    value=json_str,
                    language="JSON",
                    theme=self._cm_theme,
                    on_change=self._on_editor_change,
                ).classes("dbx-cm w-full")

                with ui.element("div").classes("dbx-editor-foot"):
                    self._dirty_html = ui.html("", sanitize=False)
                    ui.element("div").classes("itg-spacer")
                    mod = "⌘" if sys.platform == "darwin" else "Ctrl"
                    ui.html(f'<span class="dbx-kbd"><kbd>{mod}</kbd> <kbd>S</kbd> to save</span>', sanitize=False)
                    self._save_btn = primary_button("Save item", self._save_current_path, icon="check").props(
                        "dense"
                    ).classes("px-3")
        self._update_editor_state()

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

    def _on_editor_change(self, e=None) -> None:
        """Handle editor content change (validity and the save state follow shortly after)."""
        self._edit_generation += 1
        generation = self._edit_generation

        def _apply() -> None:
            if generation == self._edit_generation:
                self._update_editor_state()

        layout_schedule(0.25, _apply, once=True)

    def _update_editor_state(self) -> None:
        """Validity line, "Unsaved changes" tag and the Save button from the editor text."""
        if self._json_editor is None:
            return
        text = self._json_editor.value or ""
        error: Optional[json.JSONDecodeError] = None
        try:
            json.loads(text)
        except json.JSONDecodeError as ex:
            error = ex
        changed = text != self._original_text
        if self._valid_html is not None:
            if error is None:
                self._valid_html.set_content(f'{icon_html("check_circle")}<span>Valid JSON</span>')
                self._valid_html.classes(remove="is-bad")
            else:
                self._valid_html.set_content(
                    f'{icon_html("error_outline")}<span>Invalid JSON: {esc(error.msg)} (line {error.lineno})</span>'
                )
                self._valid_html.classes(add="is-bad")
        if self._dirty_html is not None:
            self._dirty_html.set_content(pill_html("edit", "Unsaved changes", "edit") if changed else "")
        if self._save_btn is not None:
            self._save_btn.set_enabled(changed and error is None)

    async def _on_save_key(self, _e: Any = None) -> None:
        """Ctrl+S / Cmd+S while the explorer is open: save the item if it changed."""
        if self._json_editor is None or not self.current_path:
            return
        if (self._json_editor.value or "") != self._original_text:
            await self._save_current_path()

    def _format_json(self) -> None:
        """Format the JSON in the editor"""
        if self._json_editor is None:
            return

        try:
            data = json.loads(self._json_editor.value)
            formatted = json.dumps(data, indent=2, default=str)
            self._json_editor.value = formatted
        except json.JSONDecodeError as e:
            _notify_invalid_json(e)

    def _reset_editor(self) -> None:
        """Reset editor to original value"""
        if self._json_editor is None or not self.current_path:
            return

        data = self._get_data_from_snapshot(self.current_path)
        json_str = _value_text(data)
        self._original_text = json_str
        self._json_editor.value = json_str
        if self._meta_html is not None:
            self._meta_html.set_content(_chips_html(data))
        self._update_editor_state()

    async def _save_current_path(self) -> None:
        """Save the current path data to database"""
        if self._json_editor is None or not self.current_path:
            return

        text = self._json_editor.value
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            _notify_invalid_json(e)
            return

        # Save to database (worker thread; this handler already awaits)
        ok, reason = await run.io_bound(
            _reported_write, database_manager.set_data, self.current_path, data
        )
        if ok:
            notify("", type="success", source="database", title=f"Saved {self.current_path}")
            db_type = database_manager.get_config().database_type
            if db_type in ("sql", "mongodb"):
                await self._load_snapshot()
                self._build_tree()
            else:
                self._set_data_in_snapshot(self.current_path, data)

            self._original_text = text
            if self._meta_html is not None:
                self._meta_html.set_content(_chips_html(data))
            self._update_editor_state()
        else:
            _notify_write_failed(f"Couldn't save {self.current_path}", reason)

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
        name = path.split("/")[-1]
        with ui.dialog() as confirm_dialog, ui.element("div").classes("itg").style("width: auto"):
            with ui.element("div").classes("itg-shell itg-dialog is-danger"):
                ui.html(
                    f'<div class="itg-dialog-title">{icon_html("warning_amber")}<span>Delete “{esc(name)}”?</span></div>'
                    f'<div class="itg-dialog-sub">{esc(path)} and everything in it is removed from the database. '
                    "This can't be undone.</div>",
                    sanitize=False,
                )
                with ui.element("div").classes("itg-actions-end"):
                    outline_button("Cancel", confirm_dialog.close).props("dense").classes("px-3")
                    ui.button(
                        "Delete",
                        icon="delete",
                        on_click=lambda: self._execute_delete(path, confirm_dialog),
                    ).props("unelevated no-caps dense color=negative").classes("px-3")

        confirm_dialog.open()

    async def _execute_delete(self, path: str, dialog: ui.dialog) -> None:
        """Execute path deletion"""
        ok, reason = await run.io_bound(_reported_write, _delete_with_children, path)
        if ok:
            notify("", type="success", source="database", title=f"Deleted {path}")
            # Refresh snapshot
            await self._load_snapshot()
            self.current_path = ""
            self._build_tree()
            self._update_breadcrumb()
            self._show_welcome_content()
        else:
            _notify_write_failed(f"Couldn't delete {path}", reason)

        dialog.close()

    def _show_create_dialog(self) -> None:
        """Show dialog to create a new path"""
        with ui.dialog() as create_dialog, ui.element("div").classes("itg").style("width: auto"):
            with ui.element("div").classes("itg-shell itg-dialog"):
                ui.html(
                    '<div class="itg-dialog-title">New item</div>'
                    '<div class="itg-dialog-sub">Creates a new path. Folders are made automatically from the slashes.</div>',
                    sanitize=False,
                )
                with ui.element("div").style("margin-top: 12px"):
                    with fields(one=True):
                        path_input = field("Path", placeholder="e.g. Settings/MyData")
                        with ui.element("div").style("min-width: 0"):
                            field_label("Starting value (JSON)")
                            data_input = ui.codemirror(value="{}", language="JSON", theme=self._cm_theme).classes(
                                "dbx-cm is-small w-full"
                            )

                with ui.element("div").classes("itg-actions-end"):
                    outline_button("Cancel", create_dialog.close).props("dense").classes("px-3")
                    primary_button(
                        "Create",
                        lambda: self._execute_create(
                            path_input.value,
                            data_input.value,
                            create_dialog,
                        ),
                        icon="add",
                    ).props("dense").classes("px-3")

        create_dialog.open()

    async def _execute_create(self, path: str, json_data: str, dialog: ui.dialog) -> None:
        """Execute path creation"""
        if not path or not path.strip():
            notify(
                "",
                type="warning",
                source="database",
                title="Enter a path",
                hint="For example Settings/MyData.",
            )
            return

        path = path.strip()
        if path.startswith("/"):
            path = path[1:]

        try:
            data = json.loads(json_data) if json_data.strip() else {}
        except json.JSONDecodeError as e:
            _notify_invalid_json(e)
            return

        ok, reason = await run.io_bound(_reported_write, database_manager.set_data, path, data)
        if ok:
            notify("", type="success", source="database", title=f"Created {path}")
            # Refresh snapshot
            await self._load_snapshot()
            self._build_tree()
            self._select_path(path)
            dialog.close()
        else:
            _notify_write_failed(f"Couldn't create {path}", reason)

    async def _refresh_snapshot(self) -> None:
        """Refresh the database snapshot"""
        await self._load_snapshot()
        self._build_tree()
        self._update_breadcrumb()

        if self.current_path:
            self._load_path_content()
        else:
            self._show_welcome_content()
        # The tree visibly reloads; a failed read was already reported by _load_snapshot.

    def _filter_tree(self, e) -> None:
        """Filter the tree based on search input"""
        filter_text = (
            e.args if isinstance(e.args, str) else (e.args[0] if e.args else "")
        ) or ""
        self._filter_generation += 1
        generation = self._filter_generation

        def _apply() -> None:
            if generation != self._filter_generation:
                return
            self._build_tree(filter_text)

        layout_schedule(0.2, _apply, once=True)

    def _toggle_folder(self, path: str) -> None:
        """Open or close a folder in the tree."""
        # While searching every match starts open, so a click records a fold instead.
        state = self._search_collapsed if self._filter_text.strip() else self._expanded
        state.symmetric_difference_update({path})
        self._build_tree()

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
        self._status_timer: Optional[Any] = None
        # Tracks the last set of Firebase config issues we notified for, so the
        # periodic status timer does not spam the same toast every tick.
        self._last_firebase_issues_signature: Optional[str] = None
        self._status_refresh_inflight: bool = False
        self._last_status: Dict[str, Any] = {}
        self._page: Optional[IntegrationPage] = None
        self._card: Optional[AccountCard] = None
        self._choices: Dict[str, Tuple[Any, Any]] = {}
        self._choice_state: Optional[Tuple[str, str]] = None
        self._type_boxes: Dict[str, Any] = {}
        self._firebase_issues: Optional[ui.html] = None
        self._switch_hint: Optional[Any] = None

    def on_enter(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = True
        if self._page is not None:
            self._page.set_active(True)
        layout_schedule(0.05, self._refresh_status_async, once=True)

    def _refresh_status(self) -> None:
        """Refresh Database status display."""
        try:
            # Get current status from the database manager
            status_info = database_manager.get_connection_status()
            self._apply_status_info(status_info)
        except Exception as e:
            self._show_status_error(e)

    async def _refresh_status_async(self, *, force: bool = False) -> None:
        """Same as :meth:`_refresh_status`, with the status probe (a database round
        trip on Firebase / MongoDB) on a worker thread instead of the UI loop."""
        if self._status_refresh_inflight and not force:
            return
        self._status_refresh_inflight = True
        try:
            status_info = await run.io_bound(database_manager.get_connection_status)
            if status_info is None:  # app shutting down
                return
            self._apply_status_info(status_info)
        except Exception as e:
            self._show_status_error(e)
        finally:
            self._status_refresh_inflight = False

    def _apply_status_info(self, status_info: Dict[str, Any]) -> None:
        self._last_status = dict(status_info or {})
        self._render_status()

        # Show specific Firebase configuration issues if present. Notify only
        # when the issue set changes so the periodic status timer does not
        # repeat the toast every tick.
        if status_info.get("database_type") == "Firebase" and not status_info.get(
            "is_connected", False
        ):
            firebase_issues = status_info.get("config_issues", [])
            signature = "|".join(firebase_issues) if firebase_issues else ""
            if (
                firebase_issues
                and signature != self._last_firebase_issues_signature
            ):
                notify(
                    "; ".join(str(issue) for issue in firebase_issues[:3]) + ".",
                    type="warning",
                    source="database",
                    title="Firebase isn't set up correctly",
                    hint="Fix these fields below, press Save, then press Test.",
                    dedupe_key="db:firebase_config:" + signature,
                    dedupe_cooldown_sec=600,
                )
            self._last_firebase_issues_signature = signature
        else:
            self._last_firebase_issues_signature = None

    def _render_status(self) -> None:
        """Card, header pill and Firebase hint from the last status of the database in use."""
        cfg = database_manager.get_config()
        live = _live_type()
        short = _DB_SHORT.get(live, live)
        st = self._last_status
        connected = bool(st.get("is_connected"))
        checked = _ago(st.get("last_check"))
        when = f" · last checked {checked}" if checked else ""
        if not st:
            kind, text, pill = "busy", "Checking…", ("busy", f"Checking · {short}")
        elif connected:
            kind, text, pill = "ok", f"Connected{when}", ("ok", f"Connected · {short}")
        else:
            status = str(st.get("status") or "Not connected")
            if status.startswith("Configuration issues"):
                status = "Not set up correctly"
            if len(status) > 90:
                status = status[:89] + "…"
            kind, text, pill = "error", f"{status}{when}", ("error", f"Can't connect · {short}")

        if self._page is not None:
            self._page.set_status([pill])
        if self._card is not None:
            self._card.set(_DB_NAMES.get(live, live), kind, text, icon=_DB_ICONS.get(live, "storage"))
            self._card.set_meta(self._meta_rows(cfg, live))

        if self._firebase_issues is not None:
            issues = (st.get("config_issues") or []) if live == "firebase" and not connected else []
            if issues:
                self._firebase_issues.set_content(
                    f'{icon_html("warning_amber")}<span>{esc("; ".join(str(i) for i in issues))}. '
                    "Fix the Firebase fields, press <b>Save</b>, then <b>Test</b>.</span>"
                )
            self._firebase_issues.set_visibility(bool(issues))
        self._sync_choices()

    @staticmethod
    def _meta_rows(cfg: Any, live: str) -> List[Tuple[str, str, str, str]]:
        if cfg is None:
            return []
        if live == "sql":
            configured = cfg.sql_database_path or ""
            path = _resolved_sql_path(configured)
            where = "data folder" if configured and not os.path.isabs(configured) else os.path.dirname(path)
            rows = [("Type", "SQLite", "", ""), ("Location", os.path.basename(path) or "—", where, "")]
            try:
                rows.append(("Size", _format_bytes(os.path.getsize(path)), "", ""))
            except (OSError, TypeError, ValueError):
                pass
            return rows
        if live == "firebase":
            return [("Type", "Firebase", "Realtime Database", ""), ("Location", _live_where(cfg) or "—", "", "")]
        # The connection string can hold a password: show only the database name.
        return [("Type", "MongoDB", "", ""), ("Location", cfg.mongodb_database_name or "—", "database name", "")]

    def _show_status_error(self, e: Exception) -> None:
        logger.error(f"Error refreshing database status: {str(e)}", exc_info=True)
        self._last_status = {"status": "Couldn't check the connection", "is_connected": False}
        self._render_status()
        label = self.ui_elements.get("status_label")  # older layouts
        if label is not None:
            label.set_text("Status Error")

    async def _test_connection(self) -> None:
        """Test database connection"""
        label = database_label()
        work = notify_progress(f"Testing the {label} database connection", source="database")
        try:
            ok = await run.io_bound(database_manager.test_connection)
            if ok:
                work.done(f"Connected to the {label} database")
            else:
                work.fail(
                    f"Couldn't connect to the {label} database",
                    hint="Check the connection details below, press Save, then test again.",
                    details=database_manager.last_init_error or None,
                    actions=_DB_NAV,
                )

            await self._refresh_status_async(force=True)

        except Exception as e:
            logger.error(f"Error testing database connection: {str(e)}", exc_info=True)
            work.fail(
                f"Couldn't test the {label} database connection",
                hint="Check the connection details below, press Save, then test again.",
                details=str(e),
            )

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
            if not available_dbs:  # SQL is always listed; kept as a guard
                notify(
                    "",
                    type="warning",
                    source="database",
                    title="No other database is available to migrate to",
                    hint="Fill in the Firebase or MongoDB details first.",
                )
                return

            live = database_manager.get_config().database_type
            names = {key: _DB_SHORT.get(key, key) for key in available_dbs}
            source_names = {key: name + (" (in use)" if key == live else "") for key, name in names.items()}
            with ui.dialog() as migration_dialog, ui.element("div").classes("itg").style("width: auto"):
                with ui.element("div").classes("itg-shell itg-dialog is-wide"):
                    ui.html(
                        '<div class="itg-dialog-title">Move my data</div>'
                        '<div class="itg-dialog-sub">Copies every stored item to another database, '
                        "then switches to it.</div>",
                        sanitize=False,
                    )
                    with ui.element("div").style(
                        "display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); "
                        "gap: 10px; align-items: end; margin-top: 14px"
                    ):
                        with ui.element("div").style("min-width: 0"):
                            field_label("From")
                            source_select = (
                                ui.select(
                                    source_names,
                                    value=live if live in available_dbs else available_dbs[0],
                                )
                                .props("outlined dense options-dense")
                                .classes("w-full")
                                .tooltip("Source database to copy data from")
                            )
                        ui.html(icon_html("arrow_forward"), sanitize=False).classes("itg-dialog-arrow")
                        with ui.element("div").style("min-width: 0"):
                            field_label("To")
                            target_select = (
                                ui.select(
                                    names,
                                    value=(
                                        available_dbs[1]
                                        if len(available_dbs) > 1
                                        else available_dbs[0]
                                    ),
                                )
                                .props("outlined dense options-dense")
                                .classes("w-full")
                                .tooltip("Target database to receive migrated data")
                            )

                    hint(
                        "Items at the same paths in the target are overwritten. "
                        "Uses the connection details from the Database tab.",
                        "warning_amber",
                        warn=True,
                    )

                    with ui.element("div").classes("itg-actions-end"):
                        outline_button("Cancel", migration_dialog.close).props("dense").classes("px-3")
                        primary_button(
                            "Start moving",
                            lambda: self._start_migration(
                                source_select.value or "sql",
                                target_select.value or "sql",
                                migration_dialog,
                            ),
                            icon="sync_alt",
                        ).props("dense").classes("px-3")

            migration_dialog.open()

        except Exception as e:
            logger.error(f"Error showing migration dialog: {str(e)}", exc_info=True)
            notify(
                "",
                type="error",
                source="database",
                title="Couldn't open the migration window",
                hint="Try again. If it keeps happening, copy the details and report it.",
                details=str(e),
            )

    def _start_migration(self, source_type: str, target_type: str, dialog: Any) -> None:
        """Run migration in a background thread; update config when complete."""
        try:
            if source_type == target_type:
                notify(
                    "",
                    type="warning",
                    source="database",
                    title="Pick two different databases",
                    hint="Copying needs a different source and target.",
                )
                return

            dialog.close()
            src_label, dst_label = database_label(source_type), database_label(target_type)
            work = notify_progress(
                f"Copying your data to the {dst_label} database",
                source="database",
                message="Don't close Mycelian until this finishes.",
            )

            migration_status: Dict[str, Any] = {
                "completed": False,
                "success": False,
                "failure": None,  # dict of notify kwargs for work.fail
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
                        with quiet_notifications():
                            switched = database_manager.update_config(**switch_cfg.__dict__)
                        if switched:
                            buf.database_type = target_type
                            # A failed config.json write is reported by config_manager.
                            if not config_manager.update_database_config(
                                **buf.__dict__
                            ):
                                logger.error(
                                    "Migration OK but failed to write config.json"
                                )
                            migration_status["success"] = True
                        else:
                            reason = database_manager.last_init_error
                            buf.database_type = prior_type
                            with quiet_notifications():
                                database_manager.initialize(snapshot_before_switch)
                            migration_status["failure"] = {
                                "title": f"Copied your data, but couldn't switch to the {dst_label} database",
                                "message": f"Mycelian is still using the {src_label} database.",
                                "hint": f"Check the {dst_label} connection details and press Test.",
                                "details": reason or None,
                            }
                    else:
                        result = database_manager.last_migration_result or {}
                        stage = result.get("stage")
                        if stage in ("source", "target"):
                            which = src_label if stage == "source" else dst_label
                            migration_status["failure"] = {
                                "title": f"Couldn't open the {which} database",
                                "message": "Nothing was copied or switched.",
                                "hint": f"Check the {which} connection details below and try again.",
                                "details": result.get("error") or None,
                            }
                        elif result.get("failed"):
                            failed_paths = result.get("failed_paths") or []
                            migration_status["failure"] = {
                                "title": "Some data couldn't be copied",
                                "message": (
                                    f"Copied {result.get('migrated', 0)} of {result.get('total', 0)} items. "
                                    f"{result['failed']} couldn't be written to the {dst_label} database, "
                                    f"so Mycelian stayed on the {src_label} database."
                                ),
                                "hint": f"Check the {dst_label} connection and try again.",
                                "details": "Not copied:\n" + "\n".join(failed_paths[:200]),
                            }
                        else:
                            migration_status["failure"] = {
                                "title": "The migration stopped before it finished",
                                "message": f"Nothing was switched; Mycelian is still using the {src_label} database.",
                                "hint": "Check both databases' connection details and try again.",
                                "details": result.get("error") or None,
                            }
                    migration_status["completed"] = True
                except Exception as ex:
                    logger.error(f"Migration thread error: {ex}", exc_info=True)
                    migration_status["completed"] = True
                    migration_status["failure"] = {
                        "title": "The migration stopped before it finished",
                        "message": "Check which database Mycelian is using above before trying again.",
                        "hint": "Try again. If it keeps happening, copy the details and report it.",
                        "details": str(ex),
                    }

            threading.Thread(target=migration_thread, daemon=True).start()

            check_count = {"n": 0}
            slow_after = 240  # 2 minutes of 0.5 s polls

            def poll() -> None:
                # Polls until the thread finishes; the progress notice stays up meanwhile.
                check_count["n"] += 1
                if check_count["n"] == slow_after:
                    work.update(
                        "Still copying. Large databases can take several minutes; don't close Mycelian."
                    )
                if migration_status["completed"] and not migration_status["notified"]:
                    migration_status["notified"] = True
                    if migration_status["success"]:
                        work.done(
                            f"Moved your data to the {dst_label} database",
                            "Mycelian now uses it.",
                        )
                        self._load_from_config()
                        for key, element in self.ui_elements.items():
                            if hasattr(element, "value") and hasattr(self.buffer, key):
                                element.value = getattr(self.buffer, key)
                        self._update_config_visibility()
                        self.dirty = False
                    else:
                        failure = dict(migration_status.get("failure") or {})
                        work.fail(
                            failure.pop("title", "The migration didn't finish"),
                            failure.pop("message", ""),
                            actions=_DB_NAV,
                            **failure,
                        )
                    self._refresh_status()
                    return
                if not migration_status["completed"]:
                    layout_schedule(0.5, poll, once=True)

            layout_schedule(0.5, poll, once=True)

        except Exception as e:
            logger.error(f"Error starting migration: {e}", exc_info=True)
            fail_kwargs = {
                "hint": "Try again. If it keeps happening, copy the details and report it.",
                "details": str(e),
            }
            if "work" in locals():
                work.fail("Couldn't start the migration", **fail_kwargs)
            else:
                notify("", type="error", source="database", title="Couldn't start the migration", **fail_kwargs)


    async def _show_data_viewer_dialog(self) -> None:
        """Show database data viewer and editor dialog"""
        try:
            viewer = DatabaseViewer()
            await viewer.show()

        except Exception as e:
            logger.error(f"Error showing data viewer dialog: {str(e)}", exc_info=True)
            notify(
                "",
                type="error",
                source="database",
                title="Couldn't open the Database Explorer",
                hint="Try again. If it keeps happening, copy the details and report it.",
                details=str(e),
            )

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
            json.loads(value)
            return True
        except json.JSONDecodeError:
            return False

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False
        if self._page is not None:
            self._page.set_active(False)

    def _update_config_visibility(self) -> None:
        """Show only the selected database type's fields."""
        if not self.buffer:
            return
        db_type = self.buffer.database_type
        for key, box in self._type_boxes.items():
            box.set_visibility(key == db_type)
        self._sync_choices()

    def _choice_html(self, key: str, sub: str, live: str) -> str:
        tag = '<span class="itg-choice-tag">In use</span>' if key == live else ""
        return (
            f'{tag}<div class="itg-choice-top">{icon_html(_DB_ICONS[key])}'
            f'<span class="itg-choice-name">{esc(_DB_SHORT[key])}</span><span class="itg-choice-check"></span></div>'
            f'<div class="itg-choice-sub">{esc(sub)}</div>'
        )

    def _sync_choices(self) -> None:
        """Selected card, "In use" tag and the switching hint (only when something changed)."""
        if not self.buffer or not self._choices:
            return
        selected, live = self.buffer.database_type, _live_type()
        if self._choice_state == (selected, live):
            return
        self._choice_state = (selected, live)
        for key, sub in _DB_CHOICES:
            card, inner = self._choices[key]
            inner.set_content(self._choice_html(key, sub, live))
            on = key == selected
            card.classes(add="is-on") if on else card.classes(remove="is-on")
            card.props(f"aria-checked={'true' if on else 'false'}")
        if self._switch_hint is not None:
            self._switch_hint.set_visibility(selected != live)

    def _on_database_type_change(self, value: str) -> None:
        self._set("database_type", value)
        self._update_config_visibility()

    def _build_choices(self) -> None:
        with ui.element("div").classes("itg-choices").props('role=radiogroup aria-label="Database type"'):
            for key, _sub in _DB_CHOICES:
                card = ui.element("div").classes("itg-choice").props("role=radio tabindex=0")
                with card:
                    inner = ui.html("", sanitize=False).style("display: contents")
                card.on("click", lambda _e, k=key: self._on_database_type_change(k))
                card.on("keydown", lambda _e, k=key: self._on_database_type_change(k), js_handler=_ACTIVATE_KEY_JS)
                self._choices[key] = (card, inner)

    def _number_row(
        self, key: str, title: str, sub: str, tip: str, lo: int, hi: int, step: int, unit: str, default: int
    ) -> None:
        _row, ctrl = row(title, sub)
        with ctrl:
            el = (
                ui.number(value=getattr(self.buffer, key), min=lo, max=hi, step=step, format="%d")
                .props("outlined dense")
                .classes("itg-num")
                .tooltip(tip)
            )
            el.on_value_change(lambda e: self._set(key, int(e.value or default)))
            ui.label(unit).classes("itg-unit")
        self.ui_elements[key] = el

    def build(self, parent_container) -> None:
        self._load_from_config()
        self._page = page = IntegrationPage(
            parent_container,
            "database",
            "Database",
            "Where Mycelian keeps your settings, alerts and history",
            on_save=self.save,
            on_discard=self.discard,
            is_dirty=lambda: self.dirty,
            help_topic="database",
            help_tooltip="Database settings help",
            icon="storage",
        )
        with page.column():
            with section("cable", "Connection"):
                self._card = AccountCard("database", "In use")
                with self._card.actions:
                    self.ui_elements["refresh_button"] = (
                        ui.button(icon="refresh", on_click=lambda: self._refresh_status_async(force=True))
                        .props("flat round dense")
                        .classes("itg-ibtn")
                        .tooltip("Check now")
                    )
                    self.ui_elements["test_button"] = (
                        outline_button("Test", self._test_connection, icon="wifi_tethering")
                        .props("dense")
                        .classes("px-3")
                        .tooltip("Try connecting to the database in use")
                    )

            with section("storage", "Storage"):
                self._build_choices()
                with ui.element("div").style("margin-top: 12px"):
                    with ui.element("div") as box:
                        with fields(one=True):
                            self.ui_elements["sql_database_path"] = field(
                                "Database file",
                                value=self.buffer.sql_database_path,
                                placeholder="mycelian.db",
                                tip="Path to the SQLite database file",
                                on_change=lambda e: self._set("sql_database_path", _text(e.value) or "mycelian.db"),
                            )
                        hint("Relative paths are inside Mycelian's data folder.")
                    self._type_boxes["sql"] = box

                    with ui.element("div") as box:
                        with fields(one=True):
                            self.ui_elements["firebase_service_account_path"] = field(
                                "Service account key file",
                                value=self.buffer.firebase_service_account_path,
                                placeholder="ServiceAccountKey.json",
                                tip="The .json key from Firebase console → Project settings → Service accounts",
                                on_change=lambda e: self._set("firebase_service_account_path", _text(e.value)),
                            )
                            self.ui_elements["firebase_database_url"] = field(
                                "Realtime Database URL",
                                value=self.buffer.firebase_database_url,
                                placeholder="https://…firebaseio.com/",
                                tip="Firebase Realtime Database URL",
                                on_change=lambda e: self._set("firebase_database_url", _text(e.value)),
                            )
                        self._firebase_issues = ui.html("", sanitize=False).classes("itg-hint is-warn")
                        self._firebase_issues.set_visibility(False)
                    self._type_boxes["firebase"] = box

                    with ui.element("div") as box:
                        with fields(one=True):
                            self.ui_elements["mongodb_connection_string"] = field(
                                "Connection string",
                                value=self.buffer.mongodb_connection_string,
                                placeholder="mongodb://localhost:27017/",
                                tip="MongoDB connection URI. It can contain your password, so it's hidden.",
                                password=True,
                                on_change=lambda e: self._set("mongodb_connection_string", _text(e.value)),
                            )
                            self.ui_elements["mongodb_database_name"] = field(
                                "Database name",
                                value=self.buffer.mongodb_database_name,
                                placeholder="mycelian",
                                tip="MongoDB database name",
                                on_change=lambda e: self._set("mongodb_database_name", _text(e.value) or "mycelian"),
                            )
                    self._type_boxes["mongodb"] = box

                    self._attach_db_browse("sql_database_path", "Choose a SQLite database",
                                           "A .db file anywhere on this computer", (".db", ".sqlite", ".sqlite3"),
                                           "storage", "mycelian.db")
                    self._attach_db_browse("firebase_service_account_path", "Choose a Firebase key file",
                                           "The service account .json key from the Firebase console", (".json",),
                                           "key", "")
                    self._switch_hint = hint(
                        "Switching only changes which database Mycelian reads. To bring your data along, "
                        "use <b>Move my data</b> on the right."
                    )

            with section("tune", "Connection settings"):
                self._number_row(
                    "connection_timeout", "Timeout", "How long to wait before a connection attempt gives up",
                    "Seconds to wait before a connection attempt times out", 5, 300, 5, "s", 30,
                )
                self._number_row(
                    "retry_attempts", "Retry attempts", "How many times to retry a failed connection",
                    "Number of times to retry a failed connection", 1, 10, 1, "times", 3,
                )

        with page.column():
            with section("build", "Your data"):
                with tool_row("travel_explore", "Database Explorer", "Browse and edit the raw stored data. For advanced fixes."):
                    self.ui_elements["view_data_button"] = (
                        outline_button("Open", self._show_data_viewer_dialog, icon="open_in_new")
                        .props("dense")
                        .classes("px-3")
                    )
                with tool_row("sync_alt", "Move my data", "Copy everything to another database, then switch to it."):
                    self.ui_elements["migrate_button"] = (
                        outline_button("Migrate…", self._show_migration_dialog, icon="arrow_forward")
                        .props("dense")
                        .classes("px-3")
                    )
            with section("info", "Good to know"):
                hint("Saving checks the new connection first. If it can't connect, Mycelian keeps using the database that works.")
                hint("Firebase and MongoDB keep your data online, so a crash or a new PC doesn't lose it. A local file is faster.")

        self._update_config_visibility()
        self._render_status()
        self._status_timer = layout_schedule(
            3.0, self._refresh_status_async, active=True
        )

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

    def _attach_db_browse(self, field: str, title: str, subtitle: str, extensions, icon: str, default: str) -> None:
        """Folder button after a path field that opens the picker (anywhere on this computer)."""
        element = self.ui_elements[field]

        def pick() -> None:
            from ...path_utils import get_data_path
            from ..file_picker import open_file_picker

            current = str(element.value or default or "")
            if current and not os.path.isabs(current):
                current = get_data_path(current)

            def apply(result) -> None:
                element.value = str(result.path)
                self._set(field, str(result.path))

            open_file_picker(
                title=title, subtitle=subtitle, extensions=extensions, on_pick=apply,
                scope="system", icon=icon, start=current or None,
            )

        with element.add_slot("after"):
            ui.button(icon="folder_open", on_click=pick).props("flat round dense").tooltip("Browse")

    def _set(self, field: str, value) -> None:
        if getattr(self.buffer, field) != value:
            setattr(self.buffer, field, value)
            self.dirty = True

    def save(self) -> None:
        if not self.buffer:
            return
        previous_file_cfg = dict(config_manager.get_database_config() or {})
        live_before = replace(database_manager.get_config())
        # apply to config manager (config_manager reports a failed write itself)
        ok = config_manager.update_database_config(**self.buffer.__dict__)
        if not ok:
            return

        # update runtime database manager
        cfg = DatabaseConfig(**self.buffer.__dict__, streamer_name="mycelian")
        with quiet_notifications():
            applied = database_manager.update_config(**cfg.__dict__)
        if applied:
            connection_fields = (
                "sql_database_path",
                "firebase_service_account_path",
                "firebase_database_url",
                "mongodb_connection_string",
                "mongodb_database_name",
            )
            reconnect_needed = cfg.database_type == live_before.database_type and any(
                getattr(cfg, f) != getattr(live_before, f) for f in connection_fields
            )
            notify(
                "Restart Mycelian to use the new connection details." if reconnect_needed else "",
                type="success",
                source="database",
                title="Database settings saved",
            )
            self.dirty = False
            return

        # Couldn't connect to the new database: go back to the one that worked,
        # in memory and in config.json, and keep the edits in the form.
        reason = database_manager.last_init_error
        with quiet_notifications():
            database_manager.initialize(live_before)
        if previous_file_cfg:
            config_manager.update_database_config(**previous_file_cfg)
        new_label = database_label(cfg.database_type)
        notify(
            f"Mycelian kept using the {database_label(live_before.database_type)} database. "
            "Your edits are still in the form.",
            type="error",
            source="database",
            title=f"Couldn't connect to the {new_label} database",
            hint="Check the connection details, press Test, then Save again.",
            details=reason or None,
        )

    def discard(self) -> None:
        self._load_from_config()
        for key, element in self.ui_elements.items():
            if hasattr(element, "value") and hasattr(self.buffer, key):
                element.value = getattr(self.buffer, key)
        self._update_config_visibility()
        self.dirty = False

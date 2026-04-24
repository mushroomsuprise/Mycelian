from __future__ import annotations

import logging
from typing import Dict, Any, Optional, List

from nicegui import ui

from ... import dataobjects
from ...dataobjects import state_manager
from ... import psn_service
from ...npsso_authenticator import start_npsso_auth_flow, NpssoResult
from ...psnapi import (
    load_all_psn_game_cache_docs_from_db,
    update_psn_game_cache_in_db,
    delete_psn_game_cache_in_db,
)

logger = logging.getLogger(__name__)


class PSNTab:
    name = "PSN"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.PSNSettingsData] = None
        self.ui_elements: Dict[str, Any] = {}
        self._cached_games: List[dict] = []
        self._selected_game: Optional[dict] = None
        self._game_cache_dirty: bool = False
        self._cache_chip_container: Any = None
        self._game_select_container: Any = None

    def on_enter(self) -> None:
        ui.timer(0.2, lambda: self.refresh_game_cache(), once=True)
        ui.timer(0.2, lambda: self._refresh_status(), once=True)

    def on_exit(self) -> None:
        pass

    @staticmethod
    def _str_from_value_event(e: Any) -> str:
        """
        Read full string from input/slider value events. Do not use e.args[0] when
        args is already a str — that would keep only the first character.
        """
        v = getattr(e, "value", None)
        if v is not None and not isinstance(v, (list, tuple)):
            return str(v)
        args = getattr(e, "args", None)
        if isinstance(args, str):
            return args
        if isinstance(args, (list, tuple)) and len(args) > 0:
            return str(args[0])
        return ""

    def build(self, parent_container) -> None:
        self._load_from_state()
        with parent_container:
            with ui.card().classes("content-section w-full"):
                with ui.row().classes("w-full justify-between items-center mb-4"):
                    ui.label("PlayStation Network Integration").classes(
                        "text-xl font-bold"
                    )
                    ui.button(
                        "Help", icon="help_outline", on_click=self._show_help_dialog
                    ).props("flat")

                with ui.column().classes("w-full gap-4"):
                    ui.label("Connection Status").classes("text-lg font-semibold")
                    with ui.row().classes("w-full items-center"):
                        ui.label("Status:").classes("w-40")
                        self.ui_elements["status_label"] = ui.label(
                            "Not Connected"
                        ).classes("font-semibold")

                    with ui.row().classes("w-full items-center"):
                        ui.label("Tracking:").classes("w-40")
                        self.ui_elements["user_label"] = ui.label("N/A").classes(
                            "font-semibold"
                        )

                    # Mismatch notification banner (hidden by default)
                    self.ui_elements["mismatch_banner"] = ui.card().classes(
                        "w-full hint-warning p-3"
                    )
                    self.ui_elements["mismatch_banner"].set_visibility(False)
                    with self.ui_elements["mismatch_banner"]:
                        with ui.row().classes("w-full items-center gap-2"):
                            ui.icon("warning", color="orange").classes("text-2xl")
                            with ui.column().classes("flex-grow gap-0"):
                                ui.label("Game Mismatch Detected").classes(
                                    "font-semibold text-theme-warning"
                                )
                                self.ui_elements["mismatch_game_name"] = ui.label(
                                    ""
                                ).classes(
                                    "text-sm text-theme-warning"
                                )
                            ui.label("Edit in Game Cache section below").classes(
                                "text-sm text-theme-warning"
                            )

                    ui.separator().classes("divider")

                    ui.label("Configuration").classes("text-lg font-semibold")

                    # Connection section with Connect button
                    with ui.row().classes("w-full items-center gap-2 mb-4"):
                        self.connect_button = ui.button(
                            "Connect to PSN",
                            icon="login",
                            on_click=self._start_npsso_auth,
                        ).classes("btn-primary")

                        ui.label("Get your NPSSO token automatically").classes(
                            "text-muted text-sm"
                        )

                    # Manual NPSSO input
                    with ui.row().classes("w-full items-center"):
                        ui.label("NPSSO Code:").classes("w-40")
                        self.ui_elements["npsso_code"] = (
                            ui.input(
                                value=self.buffer.npsso_code or "",
                                password=True,
                                password_toggle_button=True,
                                placeholder="Required for PSN API access",
                            )
                            .classes("w-96")
                            .on_value_change(
                                lambda e: self._set(
                                    "npsso_code", self._str_from_value_event(e)
                                )
                            )
                        )

                    with ui.row().classes("w-full items-center"):
                        ui.label("PSN Username:").classes("w-40")
                        self.ui_elements["psn_username"] = (
                            ui.input(
                                value=self.buffer.psn_username or "",
                                placeholder="Optional: Username to track (leave empty for your own)",
                            )
                            .classes("w-96")
                            .on_value_change(
                                lambda e: self._set(
                                    "psn_username", self._str_from_value_event(e)
                                )
                            )
                        )

                    with ui.row().classes("justify-end gap-2 mt-3"):
                        ui.button("Discard", on_click=self.discard).props("outline")
                        ui.button("Save", on_click=self.save).props("color=primary")

                    ui.separator().classes("divider")

                    # Game Cache Editor Section
                    self._build_game_cache_section()

                    # Status: first tick can run before PSN service writes live data
                    ui.timer(0.1, lambda: self._refresh_status(), once=True)
                    ui.timer(0.8, lambda: self._refresh_status(), once=True)
                    ui.timer(0.5, lambda: self._check_mismatch(), once=True)
                    # Periodic mismatch check
                    ui.timer(10.0, lambda: self._check_mismatch())

    def _start_npsso_auth(self) -> None:
        """Start the NPSSO authentication flow"""
        if not hasattr(self, "_auth_in_progress"):
            self._auth_in_progress = False

        if self._auth_in_progress:
            ui.notify("Authentication already in progress", type="warning")
            return

        self._auth_in_progress = True
        self.connect_button.disable()
        self.connect_button.text = "Connecting..."

        def on_auth_complete(result: NpssoResult):
            """Handle authentication result"""
            self._auth_in_progress = False
            self.connect_button.enable()
            self.connect_button.text = "Connect to PSN"

            if result.success:
                # Update the input field
                self.ui_elements["npsso_code"].value = result.npsso_code
                self._set("npsso_code", result.npsso_code)

                # Save the settings
                self.save()

                ui.notify(
                    "NPSSO token acquired successfully! PSN service will reconnect automatically.",
                    type="positive",
                    position="bottom-right",
                    duration=5000,
                )
            else:
                ui.notify(
                    f"Authentication failed: {result.error_message}",
                    type="negative",
                    position="bottom-right",
                    duration=8000,
                )

        try:
            start_npsso_auth_flow(on_auth_complete)
        except Exception as e:
            logger.error(f"Error starting NPSSO auth: {e}")
            self._auth_in_progress = False
            self.connect_button.enable()
            self.connect_button.text = "Connect to PSN"
            ui.notify(f"Error: {str(e)}", type="negative")

    def _show_help_dialog(self) -> None:
        """Show help dialog with NPSSO token acquisition steps."""
        with ui.dialog() as dialog:
            with ui.card().classes("w-full max-w-2xl"):
                ui.label("Getting Your NPSSO Token").classes("text-xl font-bold mb-4")

                with ui.column().classes("gap-4"):
                    # Step 1
                    with ui.column().classes("gap-2"):
                        ui.label("1. Sign in to PlayStation Network").classes(
                            "font-semibold"
                        )
                        ui.label(
                            "Go to PlayStation.com and sign in to your account."
                        ).classes("text-sm")
                        ui.link(
                            "www.playstation.com", "https://www.playstation.com"
                        ).classes("text-theme-info")

                    ui.separator()

                    # Step 2
                    with ui.column().classes("gap-2"):
                        ui.label("2. Get Your NPSSO Token").classes("font-semibold")
                        ui.label(
                            "Navigate to the following URL to retrieve your token:"
                        ).classes("text-sm")
                        ui.link(
                            "ca.account.sony.com/api/v1/ssocookie",
                            "https://ca.account.sony.com/api/v1/ssocookie",
                        ).classes("text-theme-info")
                        ui.label(
                            'The response will be formatted as: { "npsso": "<64 character token>" }'
                        ).classes("text-sm secondary-text")
                        ui.label("Copy only the 64-character token string.").classes(
                            "text-sm font-medium"
                        )

                    ui.separator()

                    # Step 3
                    with ui.column().classes("gap-2"):
                        ui.label("3. Enter the Token").classes("font-semibold")
                        ui.label(
                            "Paste the 64-character token into the 'NPSSO Code' field below."
                        ).classes("text-sm")

                    ui.separator()

                    # Step 4
                    with ui.column().classes("gap-2"):
                        ui.label("4. Set Username to Track").classes("font-semibold")
                        ui.label(
                            "Enter the PSN username you want to track in the 'PSN Username' field."
                        ).classes("text-sm")
                        ui.label(
                            "Note: You can only track your own profile or friends who have their status set to online."
                        ).classes("text-sm text-theme-warning")

                    ui.separator()

                    # Step 5
                    with ui.column().classes("gap-2"):
                        ui.label("5. Save and Restart").classes("font-semibold")
                        ui.label(
                            "Click 'Save' to apply your changes, then restart Mycelian."
                        ).classes("text-sm")

                with ui.row().classes("justify-end mt-6"):
                    ui.button("Close", on_click=dialog.close).props("color=primary")

        dialog.open()

    def _load_from_state(self) -> None:
        psn = state_manager.get_psn_settings_data()
        self.buffer = dataobjects.PSNSettingsData(
            **{k: getattr(psn, k) for k in psn.__dataclass_fields__.keys()}
        )
        self.dirty = False

    def _set(self, field: str, value) -> None:
        if getattr(self.buffer, field) != value:
            setattr(self.buffer, field, value)
            self.dirty = True

    def save(self) -> None:
        if not self.buffer:
            return
        for field in ("npsso_code", "psn_username"):
            el = self.ui_elements.get(field)
            if el and hasattr(el, "value"):
                v = el.value
                if v is not None:
                    self._set(field, str(v))
        for field in self.buffer.__dataclass_fields__.keys():
            state_manager.update_psn_setting(field, getattr(self.buffer, field))
        if state_manager.save_changes():
            psn_service.handle_psn_settings_change()
            ui.notify("PSN settings saved", type="positive")
            self.dirty = False
            self._refresh_status()
        else:
            ui.notify("Error saving PSN settings", type="negative")

    def discard(self) -> None:
        self._load_from_state()
        for key, element in self.ui_elements.items():
            if hasattr(element, "value") and hasattr(self.buffer, key):
                element.value = getattr(self.buffer, key) or ""
        self.dirty = False

    def _refresh_status(self) -> None:
        """Update PSN status labels in the UI."""
        try:
            from ...dataobjects import state_manager

            live_psn_data = state_manager.get_live_psn_data()
            psn_settings = state_manager.get_psn_settings_data()
            target_username = psn_settings.psn_username if psn_settings else None
            # Live PSN data can lack npsso_code briefly before the service updates it,
            # and must not be the only source: trust saved settings too.
            token_in_settings = (
                (psn_settings.npsso_code or "").strip() if psn_settings else ""
            )
            token_in_live = ""
            if live_psn_data and getattr(live_psn_data, "npsso_code", None):
                token_in_live = str(live_psn_data.npsso_code or "").strip()
            has_credentials = bool(token_in_settings or token_in_live)

            if not has_credentials:
                status_text = "Not Connected"
                user_text = "N/A"
            elif live_psn_data and live_psn_data.is_online:
                if target_username:
                    status_text = f"Connected - Tracking {target_username}"
                    user_text = f"{target_username} (target)"
                else:
                    status_text = f"Connected as {live_psn_data.online_id}"
                    user_text = f"{live_psn_data.online_id} (own account)"
            else:
                if target_username:
                    status_text = f"Configured - Tracking {target_username}"
                    user_text = f"{target_username} (target)"
                else:
                    status_text = "Configured but Offline"
                    user_text = (
                        f"{live_psn_data.online_id} (own account)"
                        if live_psn_data and live_psn_data.online_id
                        else "Unknown"
                    )

            # Update UI elements if they exist
            if "status_label" in self.ui_elements:
                self.ui_elements["status_label"].set_text(status_text)
            if "user_label" in self.ui_elements:
                self.ui_elements["user_label"].set_text(user_text)

        except Exception as e:
            logger.error(f"Error refreshing PSN status: {str(e)}", exc_info=True)
            # Set error status if UI elements exist
            if "status_label" in self.ui_elements:
                self.ui_elements["status_label"].set_text("Error")
            if "user_label" in self.ui_elements:
                self.ui_elements["user_label"].set_text("N/A")

    def _check_mismatch(self) -> None:
        """Check for game mismatch and update banner visibility."""
        try:
            live_psn_data = state_manager.get_live_psn_data()
            if live_psn_data and live_psn_data.current_game_mismatch:
                mismatch = live_psn_data.current_game_mismatch
                if "mismatch_banner" in self.ui_elements:
                    self.ui_elements["mismatch_banner"].set_visibility(True)
                if "mismatch_game_name" in self.ui_elements:
                    self.ui_elements["mismatch_game_name"].set_text(
                        f'Game: "{mismatch.presence_name}" not found in trophy data'
                    )
            else:
                if "mismatch_banner" in self.ui_elements:
                    self.ui_elements["mismatch_banner"].set_visibility(False)
        except Exception as e:
            logger.debug(f"Error checking mismatch: {str(e)}")

    def _build_game_cache_section(self) -> None:
        """Build the Game Cache Editor section."""
        ui.label("Game Cache").classes("text-lg font-semibold")
        ui.label(
            "View and edit cached game data. Use this to fix game name mismatches between presence and trophy APIs."
        ).classes("text-sm secondary-text mb-1")

        ui.label(
            "Cached games — use remove on a cell to clear that entry; the game can be re-cached on next play."
        ).classes("text-sm secondary-text mb-2")

        self._cache_chip_container = ui.row().classes(
            "w-full flex-wrap gap-1 items-center mt-1 mb-2"
        )

        with ui.row().classes("w-full items-center gap-2"):
            ui.label("Search Game:").classes("w-40")
            self._game_select_container = ui.column().classes("w-96 flex-none")
            self._rebuild_game_select()

        self._rebuild_cache_chips()

        # Game details container (hidden until game selected)
        self.ui_elements["game_details_container"] = ui.column().classes(
            "w-full gap-3 mt-4"
        )

        with self.ui_elements["game_details_container"]:
            ui.label("Selected Game Details").classes("font-semibold text-base")

            # Read-only fields
            with ui.row().classes("w-full items-center"):
                ui.label("Trophy Name:").classes(
                    "w-48 secondary-text"
                )
                self.ui_elements["cache_trophy_name"] = ui.label("--").classes(
                    "font-medium"
                )

            with ui.row().classes("w-full items-center"):
                ui.label("NP Communication ID:").classes(
                    "w-48 secondary-text"
                )
                self.ui_elements["cache_np_comm_id"] = ui.label("--").classes(
                    "font-mono text-sm"
                )

            with ui.row().classes("w-full items-center"):
                ui.label("Platform:").classes("w-48 secondary-text")
                self.ui_elements["cache_platform"] = ui.label("--")

            with ui.row().classes("w-full items-center"):
                ui.label("Cover Art URL:").classes(
                    "w-48 secondary-text"
                )
                self.ui_elements["cache_cover_url"] = ui.label("--").classes(
                    "text-sm truncate max-w-md"
                )

            with ui.row().classes("w-full items-center"):
                ui.label("Last Updated:").classes(
                    "w-48 secondary-text"
                )
                self.ui_elements["cache_last_updated"] = ui.label("--").classes(
                    "text-sm"
                )

            ui.separator().classes("my-2")

            # Editable fields
            ui.label("Editable Fields").classes("font-semibold text-base")
            ui.label("Update these fields to fix game name mismatches.").classes(
                "text-sm muted-text mb-2"
            )

            with ui.row().classes("w-full items-center"):
                ui.label("Presence Name:").classes("w-48")
                self.ui_elements["cache_presence_name"] = (
                    ui.input(placeholder="Name from presence/social API")
                    .classes("w-96")
                    .on("change", lambda e: self._on_cache_field_changed())
                )

            with ui.row().classes("w-full items-center"):
                ui.label("NP Title ID:").classes("w-48")
                self.ui_elements["cache_np_title_id"] = (
                    ui.input(placeholder="ID from presence data (e.g., PPSA01234_00)")
                    .classes("w-96")
                    .on("change", lambda e: self._on_cache_field_changed())
                )

            # Save button
            with ui.row().classes("justify-end gap-2 mt-3"):
                self.ui_elements["cache_save_btn"] = ui.button(
                    "Save Changes", on_click=self._save_game_cache
                ).props("color=primary")
                self.ui_elements["cache_save_btn"].disable()

        # Hide details initially
        self.ui_elements["game_details_container"].set_visibility(False)

    def _rebuild_game_select(self) -> None:
        """Rebuild the game dropdown or empty-state label inside the selector column."""
        if not self._game_select_container:
            return
        self._game_select_container.clear()
        self._load_cached_games()
        game_options = self._get_game_options()
        with self._game_select_container:
            if game_options:
                self.ui_elements["game_select"] = (
                    ui.select(
                        options=game_options,
                        with_input=True,
                        on_change=lambda e: self._on_game_selected(e.value),
                    )
                    .classes("w-96")
                    .props('use-input input-debounce="300" clearable')
                )
            else:
                self.ui_elements["game_select"] = ui.label(
                    "No cached games yet. Play a game to populate the cache."
                ).classes("muted-text italic")

    def _rebuild_cache_chips(self) -> None:
        """Recreate cache chips (same style as YouTube playlist filter)."""
        if not self._cache_chip_container:
            return
        self._cache_chip_container.clear()
        for game in self._cached_games:
            self._create_cache_chip(game)

    def _create_cache_chip(self, game: dict) -> None:
        np_comm = game.get("np_communication_id", "") or ""
        title = (
            game.get("trophy_name")
            or game.get("presence_name")
            or np_comm
        )
        platform = (game.get("platform") or "").strip()
        if platform:
            title = f"{title} ({platform})"
        with self._cache_chip_container:
            with ui.element("div").classes(
                "flex items-center gap-1 px-3 py-1 rounded-full"
                " bg-blue-500/20 border border-blue-500/40"
            ).style("flex-shrink: 0; white-space: nowrap; max-width: 100%;"):
                ui.label(title).classes("text-sm truncate").style("max-width: 14rem;")
                ui.button(
                    icon="close",
                    on_click=lambda _e, cid=np_comm: self._on_remove_cache_chip(
                        cid
                    ),
                ).props("flat dense round size=xs")

    def _on_remove_cache_chip(self, np_comm: str) -> None:
        if not np_comm:
            return
        if not delete_psn_game_cache_in_db(np_comm):
            ui.notify("Failed to remove cache entry", type="negative")
            return
        ui.notify("Cache entry removed", type="positive", position="bottom-right")
        if self._selected_game and self._selected_game.get("np_communication_id") == (
            np_comm
        ):
            self._selected_game = None
            if "game_details_container" in self.ui_elements:
                self.ui_elements["game_details_container"].set_visibility(False)
        self.refresh_game_cache()

    def _load_cached_games(self) -> None:
        """Load all cached game documents from the app database (no live PSN client required)."""
        try:
            self._cached_games = load_all_psn_game_cache_docs_from_db()
            logger.debug(f"Loaded {len(self._cached_games)} cached game(s) from database")
        except Exception as e:
            logger.error(f"Error loading cached games: {str(e)}")
            self._cached_games = []

    def _get_game_options(self) -> dict:
        """Get game options for the dropdown as {np_communication_id: display_name}."""
        options = {}
        for game in self._cached_games:
            np_comm_id = game.get("np_communication_id", "")
            trophy_name = game.get("trophy_name", "Unknown")
            platform = game.get("platform", "")
            display_name = f"{trophy_name}"
            if platform:
                display_name += f" ({platform})"
            options[np_comm_id] = display_name
        return options

    def _on_game_selected(self, np_communication_id: str) -> None:
        """Handle game selection from dropdown."""
        if not np_communication_id:
            self._selected_game = None
            if "game_details_container" in self.ui_elements:
                self.ui_elements["game_details_container"].set_visibility(False)
            return

        # Find the selected game
        self._selected_game = None
        for game in self._cached_games:
            if game.get("np_communication_id") == np_communication_id:
                self._selected_game = game
                break

        if not self._selected_game:
            logger.warning(f"Could not find game with ID: {np_communication_id}")
            return

        # Populate the fields
        self._populate_game_details()

        # Show the details container
        if "game_details_container" in self.ui_elements:
            self.ui_elements["game_details_container"].set_visibility(True)

        # Reset dirty state
        self._game_cache_dirty = False
        if "cache_save_btn" in self.ui_elements:
            self.ui_elements["cache_save_btn"].disable()

    def _populate_game_details(self) -> None:
        """Populate game details fields with selected game data."""
        if not self._selected_game:
            return

        game = self._selected_game

        # Read-only fields
        if "cache_trophy_name" in self.ui_elements:
            self.ui_elements["cache_trophy_name"].set_text(
                game.get("trophy_name") or "--"
            )

        if "cache_np_comm_id" in self.ui_elements:
            self.ui_elements["cache_np_comm_id"].set_text(
                game.get("np_communication_id") or "--"
            )

        if "cache_platform" in self.ui_elements:
            self.ui_elements["cache_platform"].set_text(game.get("platform") or "--")

        if "cache_cover_url" in self.ui_elements:
            cover_url = game.get("cover_art_url") or "--"
            # Truncate long URLs for display
            display_url = cover_url if len(cover_url) <= 60 else cover_url[:57] + "..."
            self.ui_elements["cache_cover_url"].set_text(display_url)

        if "cache_last_updated" in self.ui_elements:
            self.ui_elements["cache_last_updated"].set_text(
                game.get("last_updated") or "--"
            )

        # Editable fields
        if "cache_presence_name" in self.ui_elements:
            self.ui_elements["cache_presence_name"].value = (
                game.get("presence_name") or ""
            )

        if "cache_np_title_id" in self.ui_elements:
            self.ui_elements["cache_np_title_id"].value = game.get("np_title_id") or ""

    def _on_cache_field_changed(self) -> None:
        """Handle changes to editable cache fields."""
        self._game_cache_dirty = True
        if "cache_save_btn" in self.ui_elements:
            self.ui_elements["cache_save_btn"].enable()

    def _save_game_cache(self) -> None:
        """Save changes to the game cache."""
        if not self._selected_game:
            ui.notify("No game selected", type="warning")
            return

        np_comm_id = self._selected_game.get("np_communication_id")
        if not np_comm_id:
            ui.notify("Invalid game data", type="negative")
            return

        # Gather updated values
        updates = {}

        if "cache_presence_name" in self.ui_elements:
            new_presence_name = self.ui_elements["cache_presence_name"].value or ""
            if new_presence_name != (self._selected_game.get("presence_name") or ""):
                updates["presence_name"] = new_presence_name

        if "cache_np_title_id" in self.ui_elements:
            new_np_title_id = self.ui_elements["cache_np_title_id"].value or ""
            if new_np_title_id != (self._selected_game.get("np_title_id") or ""):
                updates["np_title_id"] = new_np_title_id

        if not updates:
            ui.notify("No changes to save", type="info")
            return

        # Save to cache
        try:
            success = update_psn_game_cache_in_db(np_comm_id, updates)
            if success:
                ui.notify("Game cache updated successfully", type="positive")
                for key, value in updates.items():
                    self._selected_game[key] = value
                self._game_cache_dirty = False
                if "cache_save_btn" in self.ui_elements:
                    self.ui_elements["cache_save_btn"].disable()
                self.refresh_game_cache()
            else:
                ui.notify("Failed to update game cache", type="negative")
        except Exception as e:
            logger.error(f"Error saving game cache: {str(e)}")
            ui.notify(f"Error saving: {str(e)}", type="negative")

    def refresh_game_cache(self) -> None:
        """Reload list from the database and rebuild selector, chips, and details sync."""
        if not self._game_select_container or not self._cache_chip_container:
            self._load_cached_games()
            return
        prev_id = None
        if self._selected_game:
            prev_id = self._selected_game.get("np_communication_id")
        self._rebuild_game_select()
        self._rebuild_cache_chips()
        if prev_id and any(
            g.get("np_communication_id") == prev_id for g in self._cached_games
        ):
            if "game_select" in self.ui_elements:
                el = self.ui_elements["game_select"]
                if hasattr(el, "value"):
                    try:
                        el.value = prev_id
                    except (TypeError, ValueError, AttributeError):
                        pass
            self._on_game_selected(str(prev_id))
        else:
            self._selected_game = None
            if "game_details_container" in self.ui_elements:
                self.ui_elements["game_details_container"].set_visibility(False)
            if "game_select" in self.ui_elements:
                el = self.ui_elements["game_select"]
                if hasattr(el, "value"):
                    try:
                        el.value = None
                    except (TypeError, ValueError, AttributeError):
                        pass

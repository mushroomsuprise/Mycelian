from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Dict, Any, Optional, List

from nicegui import ui
from ...notification_engine import notify
from ...ui_buttons import outline_button, primary_button
from ...ui_timer import layout_schedule
from ...ui_form_controls import form_input, form_select, form_sensitive_input
from ...ui_settings_layout import (
    THEME_CHIP_CLASSES,
    settings_divider,
    settings_form_grid,
    settings_section,
    settings_status_band,
    settings_surface,
    theme_chip_row,
)

from ... import dataobjects
from ...dataobjects import state_manager
from ... import psn_service
from ...npsso_authenticator import (
    NpssoResult,
    _run_npsso_capture_subprocess,
    show_npsso_instruction_dialog,
)
from ...psnapi import (
    load_all_psn_game_cache_docs_from_db,
    update_psn_game_cache_in_db,
    delete_psn_game_cache_in_db,
)

logger = logging.getLogger(__name__)


def _npsso_trace(msg: str) -> None:
    """Stdout trace for NPSSO flow debugging (remove when stable)."""
    print(
        f"[NPSSO_TRACE] t={time.monotonic():.3f} "
        f"thread={threading.current_thread().name!r} {msg}",
        flush=True,
    )


class PSNTab:
    name = "PSN"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.buffer: Optional[dataobjects.PSNSettingsData] = None
        self.ui_elements: Dict[str, Any] = {}
        self._npsso_capture_timer: Any = None
        self._cached_games: List[dict] = []
        self._selected_game: Optional[dict] = None
        self._game_cache_dirty: bool = False
        self._cache_chip_container: Any = None
        self._game_select_container: Any = None
        self._status_timer: Optional[Any] = None
        self._mismatch_timer: Optional[Any] = None

    def on_enter(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = True
        if self._mismatch_timer is not None:
            self._mismatch_timer.active = True
        layout_schedule(0.05, self._refresh_status, once=True)
        layout_schedule(0.2, self.refresh_game_cache, once=True)

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False
        if self._mismatch_timer is not None:
            self._mismatch_timer.active = False

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
        with settings_surface(parent_container):

            with settings_status_band():
                with ui.column().classes("gap-0"):
                    ui.label("Status").classes("text-xs secondary-text")
                    self.ui_elements["status_label"] = ui.label(
                        "Not Connected"
                    ).classes("font-semibold text-sm")
                with ui.column().classes("gap-0"):
                    ui.label("Tracking").classes("text-xs secondary-text")
                    self.ui_elements["user_label"] = ui.label("N/A").classes(
                        "font-semibold text-sm"
                    )

            self.ui_elements["mismatch_banner"] = ui.card().classes(
                "w-full hint-warning p-2"
            )
            self.ui_elements["mismatch_banner"].set_visibility(False)
            with self.ui_elements["mismatch_banner"]:
                with ui.row().classes("w-full items-center gap-2"):
                    ui.icon("warning").classes("text-xl text-theme-warning")
                    with ui.column().classes("grow gap-0"):
                        ui.label("Game Mismatch Detected").classes(
                            "font-semibold text-theme-warning"
                        )
                        self.ui_elements["mismatch_game_name"] = ui.label("").classes(
                            "text-sm text-theme-warning"
                        )
                    ui.label("Edit in Game Cache below").classes(
                        "text-sm text-theme-warning shrink-0"
                    )

            with settings_section(
                "Configuration",
                subtitle="Use Connect for automatic NPSSO, or enter credentials below.",
            ):
                with settings_form_grid(columns=2):
                    self.ui_elements["npsso_code"] = form_sensitive_input(
                        tooltip="Sony NPSSO token for PlayStation Network API access",
                        label="NPSSO code",
                        value=self.buffer.npsso_code or "",
                        placeholder="Required for PSN API access",
                    )
                    self.ui_elements["npsso_code"].on_value_change(
                        lambda e: self._set(
                            "npsso_code", self._str_from_value_event(e)
                        )
                    )
                    self.ui_elements["psn_username"] = form_sensitive_input(
                        tooltip="PSN online ID to query; leave empty to use your own account",
                        label="PSN username",
                        value=self.buffer.psn_username or "",
                        placeholder="Optional — leave empty for your own account",
                    )
                    self.ui_elements["psn_username"].on_value_change(
                        lambda e: self._set(
                            "psn_username", self._str_from_value_event(e)
                        )
                    )

            with ui.row().classes(
                "button-row w-full justify-end gap-2 mt-1 flex-wrap"
            ):
                outline_button(
                    "How to Connect",
                    self._show_help_dialog,
                    icon="help_outline",
                )
                outline_button("Discard", self.discard)
                primary_button("Save", self.save)
                self.connect_button = primary_button(
                    "Connect to PSN",
                    self._start_npsso_auth,
                    icon="login",
                )

            settings_divider()
            self._build_game_cache_section()
            self.refresh_game_cache()

            layout_schedule(0.1, self._refresh_status, once=True)
            layout_schedule(0.8, self._refresh_status, once=True)
            layout_schedule(0.5, self._check_mismatch, once=True)
            self._status_timer = layout_schedule(3.0, self._refresh_status, active=True)
            self._mismatch_timer = layout_schedule(10.0, self._check_mismatch, active=True)

    def _start_npsso_auth(self) -> None:
        """Start the NPSSO authentication flow"""
        if not hasattr(self, "_auth_in_progress"):
            self._auth_in_progress = False

        if self._auth_in_progress:
            notify("Authentication already in progress", type="warning")
            return

        self._auth_in_progress = True
        self.connect_button.disable()
        self.connect_button.text = "Connecting..."
        _npsso_trace("_start_npsso_auth: connect UI armed")

        def reset_connect_ui() -> None:
            _npsso_trace("reset_connect_ui: re-enable Connect button")
            self._auth_in_progress = False
            self.connect_button.enable()
            self.connect_button.text = "Connect to PSN"
            self._refresh_status()

        def on_auth_complete(result: NpssoResult):
            """Handle authentication result (runs on NiceGUI event loop)."""
            _npsso_trace(
                f"on_auth_complete: enter success={result.success} "
                f"token_len={len(result.npsso_code) if result.npsso_code else 0}"
            )
            defer_reset = False
            try:
                if result.success:
                    self.ui_elements["npsso_code"].value = result.npsso_code
                    self._set("npsso_code", result.npsso_code)
                    _npsso_trace(
                        "on_auth_complete: set npsso_code input + buffer "
                        f"(len={len(result.npsso_code)})"
                    )
                    notify(
                        "NPSSO token acquired successfully! PSN service will reconnect automatically.",
                        type="positive",
                        position="bottom-right",
                        duration=5000,
                    )
                    defer_reset = True

                    async def deferred_save() -> None:
                        """
                        Yield the event loop so the browser receives the NPSSO field
                        update, then persist and run PSN service work off the main
                        thread (join + connect can block ~10s and starve WebSockets).
                        """
                        _npsso_trace(
                            "deferred_save: async enter (first await yields loop)"
                        )
                        try:
                            await asyncio.sleep(0.25)
                            _npsso_trace(
                                "deferred_save: after sleep, persist + service (executor)"
                            )
                            ok = self._persist_psn_settings()
                            if ok is None:
                                return
                            if not ok:
                                notify(
                                    "Error saving PSN settings",
                                    type="negative",
                                    position="bottom-right",
                                    duration=8000,
                                )
                                return
                            loop = asyncio.get_running_loop()
                            await loop.run_in_executor(
                                None, psn_service.handle_psn_settings_change
                            )
                            _npsso_trace(
                                "deferred_save: handle_psn_settings_change returned"
                            )
                            self.dirty = False
                            self._refresh_status()
                        except Exception as save_err:
                            logger.error(
                                "Error saving PSN settings after NPSSO capture: %s",
                                save_err,
                                exc_info=True,
                            )
                            _npsso_trace(f"deferred_save: raised {save_err!r}")
                            notify(
                                f"Error saving PSN settings: {save_err}",
                                type="negative",
                                position="bottom-right",
                                duration=8000,
                            )
                        finally:
                            _npsso_trace("deferred_save: finally -> reset_connect_ui")
                            reset_connect_ui()

                    layout_schedule(0.01, deferred_save, once=True)
                    _npsso_trace(
                        "on_auth_complete: scheduled async deferred_save via ui.timer"
                    )
                else:
                    notify(
                        f"Authentication failed: {result.error_message}",
                        type="negative",
                        position="bottom-right",
                        duration=8000,
                    )
            except Exception as e:
                logger.error(f"Error applying NPSSO auth result: {e}", exc_info=True)
                notify(
                    f"Error applying NPSSO token: {e}",
                    type="negative",
                    position="bottom-right",
                    duration=8000,
                )
            finally:
                if not defer_reset:
                    _npsso_trace(
                        "on_auth_complete: finally immediate reset_connect_ui "
                        f"(defer_reset={defer_reset})"
                    )
                    reset_connect_ui()

        def begin_capture_after_instructions() -> None:
            """Run webview subprocess in a thread; poll with a tab-anchored timer (see Spotify tab)."""
            _npsso_trace("begin_capture_after_instructions: enter")
            if getattr(self, "_npsso_capture_timer", None) is not None:
                try:
                    self._npsso_capture_timer.cancel()
                except Exception:
                    pass
                self._npsso_capture_timer = None

            state: dict = {"done": False, "result": None}

            def worker() -> None:
                _npsso_trace("worker: subprocess thread started")
                result: NpssoResult | None = None
                try:
                    ok, token, err = _run_npsso_capture_subprocess()
                    _npsso_trace(
                        f"worker: subprocess returned ok={ok} token_len={len(token) if token else 0} "
                        f"err_set={bool(err)}"
                    )
                    if ok:
                        result = NpssoResult(success=True, npsso_code=token)
                    else:
                        result = NpssoResult(
                            success=False,
                            error_message=err or "NPSSO capture failed.",
                        )
                except Exception as e:
                    logger.exception("NPSSO capture error")
                    result = NpssoResult(success=False, error_message=str(e))
                state["result"] = result
                state["done"] = True
                _npsso_trace("worker: state['done']=True")

            threading.Thread(target=worker, daemon=True).start()

            checks = {"n": 0}
            max_checks = 3000  # ~10 minutes at 0.2s (subprocess timeout is longer)

            def poll_capture() -> None:
                checks["n"] += 1
                if not state["done"] and checks["n"] % 25 == 1:
                    _npsso_trace(
                        f"poll_capture: tick n={checks['n']} still waiting worker"
                    )
                if checks["n"] >= max_checks:
                    _npsso_trace(
                        f"poll_capture: max_checks={max_checks} timeout, failing auth"
                    )
                    try:
                        if self._npsso_capture_timer is not None:
                            self._npsso_capture_timer.cancel()
                    except Exception:
                        pass
                    self._npsso_capture_timer = None
                    on_auth_complete(
                        NpssoResult(
                            success=False,
                            error_message="Timed out waiting for the NPSSO sign-in window.",
                        )
                    )
                    return
                if not state["done"]:
                    return
                _npsso_trace(
                    f"poll_capture: worker done, cancel timer and on_auth_complete "
                    f"success={bool(state.get('result') and state['result'].success)}"
                )
                try:
                    if self._npsso_capture_timer is not None:
                        self._npsso_capture_timer.cancel()
                except Exception:
                    pass
                self._npsso_capture_timer = None
                r = state["result"]
                if r is None:
                    on_auth_complete(
                        NpssoResult(
                            success=False,
                            error_message="Unknown error during NPSSO capture.",
                        )
                    )
                else:
                    on_auth_complete(r)

            self._npsso_capture_timer = layout_schedule(0.2, poll_capture)
            _npsso_trace("begin_capture_after_instructions: poll timer started")

        try:
            show_npsso_instruction_dialog(begin_capture_after_instructions)
        except Exception as e:
            logger.error(f"Error starting NPSSO auth: {e}")
            self._auth_in_progress = False
            self.connect_button.enable()
            self.connect_button.text = "Connect to PSN"
            notify(f"Error: {str(e)}", type="negative")

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

    def _persist_psn_settings(self) -> Optional[bool]:
        """
        Copy inputs + buffer into state_manager and flush to DB.

        Returns:
            None if there is no buffer (nothing to do).
            True if ``save_changes()`` succeeded.
            False if persistence failed.
        """
        if not self.buffer:
            return None
        for field in ("npsso_code", "psn_username"):
            el = self.ui_elements.get(field)
            if el and hasattr(el, "value"):
                v = el.value
                if v is not None:
                    self._set(field, str(v))
        for field in self.buffer.__dataclass_fields__.keys():
            state_manager.update_psn_setting(field, getattr(self.buffer, field))
        _npsso_trace("save(): calling state_manager.save_changes() …")
        return state_manager.save_changes()

    def save(self, *, suppress_saved_notification: bool = False) -> None:
        _npsso_trace(
            f"save(): enter suppress_saved_notification={suppress_saved_notification}"
        )
        ok = self._persist_psn_settings()
        if ok is None:
            _npsso_trace("save(): no buffer, return")
            return
        if not ok:
            _npsso_trace("save(): save_changes returned False")
            notify("Error saving PSN settings", type="negative")
            return
        _npsso_trace("save(): save_changes OK, calling handle_psn_settings_change() …")
        psn_service.handle_psn_settings_change()
        _npsso_trace("save(): handle_psn_settings_change() returned")
        if not suppress_saved_notification:
            notify("PSN settings saved", type="positive")
        self.dirty = False
        self._refresh_status()

    def discard(self) -> None:
        self._load_from_state()
        for key, element in self.ui_elements.items():
            if hasattr(element, "value") and hasattr(self.buffer, key):
                element.value = getattr(self.buffer, key) or ""
        self.dirty = False

    def _refresh_status(self) -> None:
        """Update PSN status labels in the UI."""
        try:
            from ...connection_status_tracker import get_connectivity_overlay

            overlay = get_connectivity_overlay("psn")
            if overlay:
                status_text = overlay
                user_text = "N/A"
                status_color = "text-theme-error"
                if "status_label" in self.ui_elements:
                    self.ui_elements["status_label"].set_text(status_text)
                    self.ui_elements["status_label"].classes(
                        replace=f"font-semibold {status_color}"
                    )
                if "user_label" in self.ui_elements:
                    self.ui_elements["user_label"].set_text(user_text)
                return

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

            # status_color tracks the connection state: error (no creds),
            # success (live online), or warning (configured but not online).
            if not has_credentials:
                status_text = "Not Connected"
                user_text = "N/A"
                status_color = "text-theme-error"
            elif live_psn_data and live_psn_data.is_online:
                if target_username:
                    status_text = f"Connected - Tracking {target_username}"
                    user_text = f"{target_username} (target)"
                else:
                    status_text = f"Connected as {live_psn_data.online_id}"
                    user_text = f"{live_psn_data.online_id} (own account)"
                status_color = "text-theme-success"
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
                status_color = "text-theme-warning"

            # Update UI elements if they exist
            if "status_label" in self.ui_elements:
                self.ui_elements["status_label"].set_text(status_text)
                self.ui_elements["status_label"].classes(
                    replace=f"font-semibold {status_color}"
                )
            if "user_label" in self.ui_elements:
                self.ui_elements["user_label"].set_text(user_text)

        except Exception as e:
            logger.error(f"Error refreshing PSN status: {str(e)}", exc_info=True)
            # Set error status if UI elements exist
            if "status_label" in self.ui_elements:
                self.ui_elements["status_label"].set_text("Error")
                self.ui_elements["status_label"].classes(
                    replace="font-semibold text-theme-error"
                )
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
        with settings_section(
            "Game Cache",
            subtitle=(
                "Edit cached game data to fix name mismatches. Remove chips to "
                "clear entries (re-cached on next play)."
            ),
        ):
            self._load_cached_games()
            self._cache_chip_container = theme_chip_row()
            self._rebuild_cache_chips()

            with ui.row().classes("w-full items-end gap-2"):
                ui.label("Search game").classes("text-sm secondary-text shrink-0 pb-2")
                self._game_select_container = ui.column().classes("flex-1 min-w-0")
            with self._game_select_container:
                self._rebuild_game_select()

            self.ui_elements["game_details_container"] = ui.column().classes(
                "w-full gap-2 mt-2"
            )

            with self.ui_elements["game_details_container"]:
                ui.label("Selected game").classes("font-semibold text-sm")
                with settings_form_grid(columns=2):
                    with ui.column().classes("gap-0"):
                        ui.label("Trophy name").classes("text-xs secondary-text")
                        self.ui_elements["cache_trophy_name"] = ui.label("--").classes(
                            "font-medium text-sm"
                        )
                    with ui.column().classes("gap-0"):
                        ui.label("Platform").classes("text-xs secondary-text")
                        self.ui_elements["cache_platform"] = ui.label("--").classes(
                            "text-sm"
                        )
                    with ui.column().classes("gap-0 col-span-2"):
                        ui.label("NP Communication ID").classes(
                            "text-xs secondary-text"
                        )
                        self.ui_elements["cache_np_comm_id"] = ui.label("--").classes(
                            "font-mono text-sm"
                        )
                    with ui.column().classes("gap-0 col-span-2"):
                        ui.label("Cover art URL").classes("text-xs secondary-text")
                        self.ui_elements["cache_cover_url"] = ui.label("--").classes(
                            "text-sm truncate"
                        )
                    with ui.column().classes("gap-0"):
                        ui.label("Last updated").classes("text-xs secondary-text")
                        self.ui_elements["cache_last_updated"] = ui.label(
                            "--"
                        ).classes("text-sm")

                with settings_form_grid(columns=2):
                    self.ui_elements["cache_presence_name"] = form_input(
                        tooltip="Display name shown for this game in presence/social APIs",
                        label="Presence name",
                        placeholder="Name from presence/social API",
                    )
                    self.ui_elements["cache_presence_name"].on(
                        "change", lambda e: self._on_cache_field_changed()
                    )
                    self.ui_elements["cache_np_title_id"] = form_input(
                        tooltip="PlayStation NP Title ID for this cached game",
                        label="NP Title ID",
                        placeholder="e.g. PPSA01234_00",
                    )
                    self.ui_elements["cache_np_title_id"].on(
                        "change", lambda e: self._on_cache_field_changed()
                    )

                with ui.row().classes("justify-end gap-2 mt-1"):
                    self.ui_elements["cache_save_btn"] = ui.button(
                        "Save Changes", on_click=self._save_game_cache
                    ).props("color=primary")
                    self.ui_elements["cache_save_btn"].disable()

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
                self.ui_elements["game_select"] = form_select(
                    tooltip="Select a cached PlayStation game to view or edit details",
                    options=game_options,
                    classes="w-full",
                    on_change=lambda e: self._on_game_selected(e.value),
                )
                self.ui_elements["game_select"].props(
                    'use-input input-debounce="300" clearable with-input'
                )
            else:
                self.ui_elements["game_select"] = ui.label(
                    "No cached games yet. Play a game to populate the cache."
                ).classes("muted-text italic")
        self._rebuild_cache_chips()

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
            with (
                ui.element("div")
                .classes(f"{THEME_CHIP_CLASSES} max-w-full")
                .style("white-space: nowrap;")
            ):
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
            notify("Failed to remove cache entry", type="negative")
            return
        notify("Cache entry removed", type="positive", position="bottom-right")
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
            notify("No game selected", type="warning")
            return

        np_comm_id = self._selected_game.get("np_communication_id")
        if not np_comm_id:
            notify("Invalid game data", type="negative")
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
            notify("No changes to save", type="info")
            return

        # Save to cache
        try:
            success = update_psn_game_cache_in_db(np_comm_id, updates)
            if success:
                notify("Game cache updated successfully", type="positive")
                for key, value in updates.items():
                    self._selected_game[key] = value
                self._game_cache_dirty = False
                if "cache_save_btn" in self.ui_elements:
                    self.ui_elements["cache_save_btn"].disable()
                self.refresh_game_cache()
            else:
                notify("Failed to update game cache", type="negative")
        except Exception as e:
            logger.error(f"Error saving game cache: {str(e)}")
            notify(f"Error saving: {str(e)}", type="negative")

    def refresh_game_cache(self) -> None:
        """Reload list from the database and rebuild selector, chips, and details sync."""
        self._load_cached_games()
        if self._cache_chip_container:
            self._rebuild_cache_chips()
        if not self._game_select_container:
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

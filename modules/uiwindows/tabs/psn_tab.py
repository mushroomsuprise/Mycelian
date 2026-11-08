# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Dict, Any, Optional

from nicegui import run, ui
from ...notification_engine import notify, notify_progress

from ...ui_buttons import outline_button, primary_button
from ...ui_timer import app_schedule, layout_schedule, run_on_ui_loop

from ... import dataobjects
from ...dataobjects import state_manager
from ... import psn_service
from ...npsso_authenticator import (
    NpssoResult,
    _run_npsso_capture_subprocess,
    show_npsso_instruction_dialog,
)
from .integration_ui import (
    clear_credentials_button,
    AccountCard,
    IntegrationPage,
    banner,
    esc,
    field,
    fields,
    hint,
    icon_html,
    initial_of,
    section,
    set_connect_style,
    tool_row,
    UsageRow,
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
        self._status_timer: Optional[Any] = None
        self._mismatch_timer: Optional[Any] = None
        self._auth_in_progress: bool = False
        self._connected: bool = False
        self._page: Optional[IntegrationPage] = None
        self._card: Optional[AccountCard] = None
        self._fold: Any = None
        self._usage: Any = None
        self._banner: Any = None

    def on_enter(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = True
        if self._mismatch_timer is not None:
            self._mismatch_timer.active = True
        if self._page is not None:
            self._page.set_active(True)
        layout_schedule(0.05, self._refresh_status, once=True)

    def on_exit(self) -> None:
        if self._status_timer is not None:
            self._status_timer.active = False
        if self._mismatch_timer is not None:
            self._mismatch_timer.active = False
        if self._page is not None:
            self._page.set_active(False)

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
        self._page = page = IntegrationPage(
            parent_container,
            "psn",
            "PlayStation",
            "Trophies, presence and the current game for overlays",
            on_save=self.save,
            on_discard=self.discard,
            is_dirty=self.has_unsaved_changes,
            help_topic="guide_psn",
            help_tooltip="PlayStation setup guide",
        )
        with page.wide(top=True):
            self._banner = banner()
            # The mismatch link opens Games Tracker; listening on the banner itself (set up now,
            # before the page renders it) avoids NiceGUI's re-render for late page-level listeners.
            self._banner.on("click", lambda _e: _open_games_tracker(),
                            js_handler="(e) => { if (e.target.closest('[data-open-games-tracker]')) emit(); }")

        with page.column():
            with section("account_circle", "Account"):
                self._card = AccountCard("psn", "PlayStation account")
                with self._card.actions:
                    clear_credentials_button("psn", self._page, self._after_clear)
                    outline_button("How to connect", self._show_help_dialog, icon="help_outline").props(
                        "dense"
                    ).classes("px-3")
                    self.connect_button = primary_button(
                        "Connect to PSN", self._start_npsso_auth, icon="login"
                    ).props("dense").classes("px-3")
                saved_npsso = bool((self.buffer.npsso_code or "").strip())
                self._fold = self._card.credentials("NPSSO code", opened=not saved_npsso)
                with self._fold.body:
                    with fields(one=True):
                        self.ui_elements["npsso_code"] = field(
                            "NPSSO code",
                            value=self.buffer.npsso_code or "",
                            password=True,
                            placeholder="Required for PSN API access",
                            tip="Sony NPSSO token for PlayStation Network API access. Sign in fills this for you.",
                            on_change=lambda e: self._set("npsso_code", self._str_from_value_event(e)),
                        )
                    with ui.element("div").classes("itg-hint"):
                        ui.html(icon_html("info"), sanitize=False)
                        with ui.element("span"):
                            ui.html(
                                "Signing in opens a PlayStation window and saves the code for you. "
                                "To paste one by hand, see&nbsp;",
                                sanitize=False,
                            ).classes("inline")
                            ui.label("How to get an NPSSO code").classes("itg-link inline").on(
                                "click", lambda _e: self._show_help_dialog()
                            )
                self._fold.set_saved(saved_npsso)

        with page.column():
            with section("person_search", "Tracking"):
                with fields(one=True):
                    self.ui_elements["psn_username"] = field(
                        "PSN username to track",
                        value=self.buffer.psn_username or "",
                        placeholder="Leave empty to track your own account",
                        tip="PSN online ID to query; leave empty to use your own account",
                        on_change=lambda e: self._set("psn_username", self._str_from_value_event(e)),
                    )
                hint("You can only track your own profile, or friends who are online.")
                self._usage = UsageRow(["PSN"])

        with page.wide():
            self._build_game_cache_link()

        self._refresh_status()
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
            return  # the Connect button is already disabled

        self._auth_in_progress = True
        self.connect_button.disable()
        self.connect_button.text = "Connecting..."
        _npsso_trace("_start_npsso_auth: connect UI armed")

        def reset_connect_ui() -> None:
            _npsso_trace("reset_connect_ui: re-enable Connect button")
            self._auth_in_progress = False
            self.connect_button.enable()
            self._sync_connect_button()
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
                    work = notify_progress(
                        "Connecting to PlayStation",
                        source="psn",
                        message="Signed in. Saving and reconnecting…",
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
                                work.close()
                                return
                            if not ok:
                                # save_changes() already said why; don't stack a second error.
                                work.close()
                                return
                            loop = asyncio.get_running_loop()
                            await loop.run_in_executor(
                                None, psn_service.handle_psn_settings_change
                            )
                            _npsso_trace(
                                "deferred_save: handle_psn_settings_change returned"
                            )
                            work.done("Signed in to PlayStation", "PSN is reconnecting with the new sign-in.")
                            self.dirty = False
                            self._refresh_status()
                        except Exception as save_err:
                            logger.error(
                                "Error saving PSN settings after NPSSO capture: %s",
                                save_err,
                                exc_info=True,
                            )
                            _npsso_trace(f"deferred_save: raised {save_err!r}")
                            work.fail(
                                "Signed in, but couldn't connect to PSN",
                                hint="Press Connect to PSN to try again.",
                                details=f"{type(save_err).__name__}: {save_err}",
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
                        "",
                        type="error",
                        source="psn",
                        title="PlayStation sign-in didn't finish",
                        hint="Press Connect to PSN to try again, and finish signing in in the window that opens.",
                        details=result.error_message or None,
                    )
            except Exception as e:
                logger.error(f"Error applying NPSSO auth result: {e}", exc_info=True)
                notify(
                    "",
                    type="error",
                    source="psn",
                    title="Signed in, but Mycelian couldn't use the PSN sign-in",
                    hint="Press Connect to PSN to try again.",
                    details=f"{type(e).__name__}: {e}",
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

            state: dict = {"done": False, "result": None, "applied": False}

            def apply_result_once(result: NpssoResult) -> None:
                if state.get("applied"):
                    return
                state["applied"] = True
                on_auth_complete(result)

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

                # Persist from the worker so a tray unload (layout poller death)
                # cannot drop a successful token.
                if result is not None and result.success and result.npsso_code:
                    try:
                        from ...dataobjects import state_manager as _sm

                        _sm.update_psn_setting("npsso_code", result.npsso_code)
                        _sm.save_changes()
                        _npsso_trace(
                            f"worker: persisted npsso_code (len={len(result.npsso_code)})"
                        )
                    except Exception as persist_err:
                        logger.error(
                            "NPSSO worker persist failed: %s", persist_err, exc_info=True
                        )

                state["result"] = result
                state["done"] = True
                _npsso_trace("worker: state['done']=True")

                def _apply_on_ui() -> None:
                    r = state.get("result")
                    if r is None:
                        apply_result_once(
                            NpssoResult(
                                success=False,
                                error_message="Unknown error during NPSSO capture.",
                            )
                        )
                    else:
                        apply_result_once(r)

                run_on_ui_loop(
                    lambda: app_schedule(0.05, _apply_on_ui, once=True)
                )

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
                    apply_result_once(
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
                    apply_result_once(
                        NpssoResult(
                            success=False,
                            error_message="Unknown error during NPSSO capture.",
                        )
                    )
                else:
                    apply_result_once(r)

            self._npsso_capture_timer = layout_schedule(0.2, poll_capture)
            _npsso_trace("begin_capture_after_instructions: poll timer started")

        try:
            show_npsso_instruction_dialog(
                begin_capture_after_instructions,
                on_cancel=reset_connect_ui,
            )
        except Exception as e:
            logger.error(f"Error starting NPSSO auth: {e}")
            self._auth_in_progress = False
            self.connect_button.enable()
            self._sync_connect_button()
            notify(
                "",
                type="error",
                source="psn",
                title="Couldn't open the PlayStation sign-in window",
                hint="Try again. If it keeps happening, copy the details and report it.",
                details=f"{type(e).__name__}: {e}",
            )

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
            if not getattr(self, "_suppress_dirty", False):
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
        for key in ("npsso_code", "psn_username"):
            el = self.ui_elements.get(key)
            if el and hasattr(el, "value"):
                v = el.value
                if v is not None:
                    self._set(key, str(v))
        for key in self.buffer.__dataclass_fields__.keys():
            state_manager.update_psn_setting(key, getattr(self.buffer, key))
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
            return  # save_changes() already reported it (changes stay pending)
        _npsso_trace("save(): save_changes OK, calling handle_psn_settings_change() …")

        def _after_service() -> None:
            _npsso_trace("save(): handle_psn_settings_change() returned")
            if not suppress_saved_notification:
                notify("", type="success", source="psn", title="PSN settings saved")
            self.dirty = False
            self._refresh_status()

        async def _run_service() -> None:
            try:
                await run.io_bound(psn_service.handle_psn_settings_change)
            except Exception as e:
                logger.error("PSN save service change failed: %s", e, exc_info=True)
                notify(
                    "",
                    type="warning",
                    source="psn",
                    title="PSN settings saved, but PSN couldn't reconnect",
                    hint="Check the NPSSO token, or press Connect to PSN to sign in again.",
                    details=f"{type(e).__name__}: {e}",
                )
            else:
                _after_service()

        from nicegui import background_tasks

        background_tasks.create(_run_service(), name="psn_save_settings_change")

    def _after_clear(self) -> None:
        """Show the cleared credentials: reload the fields from the (now blank) saved settings."""
        self.discard()
        refresh = getattr(self, "_refresh_status", None)
        if callable(refresh):
            refresh()

    def discard(self) -> None:
        self._suppress_dirty = True
        try:
            self._load_from_state()
            for key, element in self.ui_elements.items():
                if hasattr(element, "value") and hasattr(self.buffer, key):
                    element.value = getattr(self.buffer, key) or ""
        finally:
            self._suppress_dirty = False
        self.mark_clean()

    def mark_clean(self) -> None:
        """Drop unsaved account edits."""
        self.dirty = False

    def _sync_connect_button(self) -> None:
        """Connect when there's no working sign-in; Sign in again once PSN is connected."""
        btn = getattr(self, "connect_button", None)
        if btn is None or self._auth_in_progress:
            return
        has_code = bool((self.buffer.npsso_code if self.buffer else "") or "")
        set_connect_style(
            btn,
            self._connected,
            connect="Sign in again" if has_code else "Connect to PSN",
            reconnect="Sign in again",
        )

    def _status(self) -> tuple:
        """(pill kind, pill text, card name, card state kind, card state text, meta rows)."""
        status_text, user_text, status_color = psn_service.get_psn_status_display()
        kind = {"text-theme-success": "ok", "text-theme-warning": "warn"}.get(status_color, "error")
        saved = state_manager.get_psn_settings_data()
        has_code = bool(getattr(saved, "npsso_code", "") or "")
        if status_text == "Not Connected" and not has_code:
            return "off", "Not connected", "Not signed in", "off", "Connect to PSN to sign in", []

        tracked, whose = "", ""
        user_text = str(user_text or "")
        for suffix, label in ((" (own account)", "own account"), (" (target)", "tracking target")):
            if user_text.endswith(suffix):
                tracked, whose = user_text[: -len(suffix)], label
        meta = []
        if tracked:
            meta.append(("Tracking", tracked, whose, ""))

        state_text = str(status_text).replace(" - ", " · ")
        if kind == "error":
            return "error", str(status_text), "PlayStation account", "error", state_text, meta

        live = state_manager.get_live_psn_data()
        online_id = str(getattr(live, "online_id", "") or "") if live else ""
        game = str(getattr(live, "current_game_name", "") or "") if live else ""
        if game:
            meta.append(("Now playing", game, "", ""))
        name = online_id or tracked or "PlayStation account"
        pill = "Connected" if kind == "ok" else str(status_text).split(" - ")[0]
        return kind, pill, name, kind, state_text, meta

    @staticmethod
    def _with_avatars(name: str, meta: list) -> tuple:
        """(card picture, meta) with PSN profile pictures from the live data.

        The card shows whoever it names; a tracked friend's picture goes on the Tracking cell.
        """
        live = state_manager.get_live_psn_data()
        own = str(getattr(live, "avatar_url", "") or "") if live else ""
        target = str(getattr(live, "target_avatar_url", "") or "") if live else ""
        online_id = str(getattr(live, "online_id", "") or "").lower() if live else ""
        photo = own if online_id and name.lower() == online_id else target
        rows = [
            (*row, target) if row[0] == "Tracking" and target and row[1].lower() != name.lower() else row
            for row in meta
        ]
        return photo or None, rows

    def _refresh_status(self) -> None:
        """Update the header pill, account card and NPSSO fold from the PSN service."""
        try:
            pill_kind, pill_text, name, state_kind, state_text, meta = self._status()
            self._connected = pill_kind in ("ok", "warn")
            if self._page is not None:
                self._page.set_status([(pill_kind, pill_text)])
            if self._usage is not None:
                self._usage.refresh()
            if self._card is not None:
                photo = None
                if self._connected:
                    photo, meta = self._with_avatars(name, meta)
                initial = initial_of(name) if self._connected and name != "PlayStation account" else None
                self._card.set(name, state_kind, state_text, initial=initial, photo_url=photo)
                self._card.set_meta(meta)
            if self._fold is not None:
                saved = state_manager.get_psn_settings_data()
                self._fold.set_saved(bool(getattr(saved, "npsso_code", "") or ""))
        except Exception as e:
            logger.error(f"Error refreshing PSN status: {str(e)}", exc_info=True)
            self._connected = False
            if self._page is not None:
                self._page.set_status([("error", "Error")])
            if self._card is not None:
                self._card.set("PlayStation account", "error", "Error")
                self._card.set_meta([])
        self._sync_connect_button()

    def _check_mismatch(self) -> None:
        """Check for game mismatch and update banner visibility."""
        try:
            live_psn_data = state_manager.get_live_psn_data()
            if self._banner is None:
                return
            if live_psn_data and live_psn_data.current_game_mismatch:
                mismatch = live_psn_data.current_game_mismatch
                self._banner.set_content(
                    f'{icon_html("warning_amber")}<div><b>Game mismatch:</b> '
                    f"“{esc(mismatch.presence_name)}” isn’t in your trophy data, so trophy overlays show "
                    'nothing for it. <span class="itg-link" data-open-games-tracker="1">'
                    "Fix it in Games Tracker</span>.</div>"
                )
                self._banner.set_visibility(True)
            else:
                self._banner.set_visibility(False)
        except Exception as e:
            logger.debug(f"Error checking mismatch: {str(e)}")

    def _build_game_cache_link(self) -> None:
        """The game cache moved to the Games Tracker tab; this points there."""
        with section("storage", "Game cache"):
            with tool_row(
                "emoji_events",
                "The game cache now lives in Games Tracker",
                "Edit display titles, NPWR IDs and NP Title IDs, re-resolve trophy lists and manage cover art "
                "for every PlayStation game there.",
            ):
                outline_button("Open Games Tracker", _open_games_tracker, icon="arrow_forward")

    def has_unsaved_changes(self) -> bool:
        return bool(self.dirty)


def _open_games_tracker() -> None:
    from ...help_system.contextual_help import navigate_to_main_tab

    navigate_to_main_tab("Games Tracker")

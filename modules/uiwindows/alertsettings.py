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

import html as html_lib
import logging
import re
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

from nicegui import ui
from ..notification_engine import nav_actions_main_tab, nav_actions_settings, nav_alerts, notify, notify_progress
from ..ui_buttons import destructive_button, outline_button, primary_button
from ..ui_form_controls import form_input, form_number, form_select
from ..ui_timer import app_schedule, layout_schedule, run_on_ui_loop

from ..help_system.contextual_help import set_alerts_ui_references

from .. import alert_processor, alertutils
from .alerts_styles import ALERTS_CSS, ALERTS_TAB_CSS

logger = logging.getLogger(__name__)


class AlertSettingsState:
    """Class to store alert settings UI state"""

    def __init__(self):
        # Store UI elements per tab type
        self.tab_elements = {}
        # Track original values per tab
        self.tab_original_values = {}
        # Track which tab is currently active
        self.current_tab = None
        # Skip selection on_change handlers during programmatic dropdown updates
        self.suppress_selection_handler_depth = 0
        # Sub-tab chrome used by the unsaved-changes guard
        self.tabs_component = None
        self.active_tab_name = "Bits"
        self.tab_switch_guard = False
        self.unsaved_prompt_open = False
        # Discard reloads the form and must not mark the tab dirty
        self.ignore_field_dirty = False
        # Alert types whose forms differ from the last loaded or saved values
        self.dirty_tabs = set()
        # Alert id last loaded into each type's form
        self.loaded_alert_ids = {}

    @property
    def suppress_selection_handler(self) -> bool:
        return self.suppress_selection_handler_depth > 0

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

_LAZY_ALERT_PANEL_BUILDERS = {}


def _ensure_lazy_alert_panel(tab_name: str) -> None:
    """Build an alert-type form the first time that tab is shown."""
    builder = _LAZY_ALERT_PANEL_BUILDERS.pop(tab_name, None)
    if builder is not None:
        builder()


ALERT_TAB_TO_TYPE = {
    "Bits": "bits",
    "Subscriptions": "subs",
    "Streaks": "streaks",
    "Gift Subs": "giftsubs",
    "Donations": "donations",
    "Raids": "raids",
    "Follows": "follows",
    "Channel Points": "points",
}
_ALERT_TYPE_TO_TAB = {t: name for name, t in ALERT_TAB_TO_TYPE.items()}


def _alerts_nav(alert_type: str) -> list:
    """Notification action that opens the Alerts sub-tab for ``alert_type``."""
    return nav_alerts(_ALERT_TYPE_TO_TAB.get(alert_type, "Bits"))


def _alert_name(alert_type: str, alert_id: str, alert_data=None) -> str:
    """The name the alert list shows (never the internal id when avoidable)."""
    try:
        return alertutils.alert_state_manager.get_display_name(alert_type, alert_id, alert_data)
    except Exception:
        return str(alert_id)


def _alerts_paused() -> bool:
    try:
        from .. import web_engine

        return bool(web_engine.ALERTS_PAUSED)
    except Exception:
        return False


def _overlay_running() -> bool:
    try:
        from .. import web_engine

        return web_engine.web_engine_instance is not None
    except Exception:
        return False


def _notify_write_failed(title: str, message: str, alert_type: str, details: str = "") -> None:
    """An alert save or delete didn't reach the database."""
    reason = getattr(alertutils.alert_state_manager, "last_write_error", "") or ""
    if reason == "unreadable":
        message = (
            f"{message} Mycelian couldn't read your saved alerts from the database, "
            "so it didn't overwrite them."
        ).strip()
    notify(
        message,
        type="error",
        source="alerts",
        title=title,
        hint="Check your database connection in Settings → Database, then try again.",
        details="\n".join(x for x in (reason, details) if x) or None,
        actions=nav_actions_settings("Database") + _alerts_nav(alert_type),
    )


def _notify_tab_not_ready(alert_type: str) -> None:
    notify(
        "",
        type="warning",
        source="alerts",
        title="This alert tab is still loading",
        hint="Wait a moment, then try again. If it keeps happening, switch to another alert tab and back.",
        actions=_alerts_nav(alert_type),
        dedupe_key=f"alerts:not_ready:{alert_type}",
    )


def _set_resub_fallback(enabled: bool) -> None:
    if alertutils.alert_state_manager.set_resub_fallback_enabled(enabled):
        return
    notify(
        "The switch will go back to its saved position after a restart.",
        type="error",
        source="alerts",
        title="Couldn't save the Resub Fallback setting",
        hint="Check your database connection in Settings → Database, then flip the switch again.",
        actions=nav_actions_settings("Database"),
    )

_ALERT_DIRTY_SKIP = {
    "alert_select",
    "delete_btn",
    "save_btn",
    "unsaved_label",
    "channel_points_locked_banner",
    "exact_input_row",
    "range_input_row",
}


def _alert_values_differ(original, current) -> bool:
    """True when a form field no longer matches the value last loaded or saved."""
    if original == current:
        return False
    if (original is None or original == "") and (current is None or current == ""):
        return False
    if (
        isinstance(original, (int, float))
        and isinstance(current, (int, float))
        and not isinstance(original, bool)
        and not isinstance(current, bool)
    ):
        try:
            return float(original) != float(current)
        except (TypeError, ValueError):
            return True
    return True


def alerts_have_unsaved_changes() -> bool:
    """True when the open alert form, or any alert type, has unsaved edits."""
    active = ALERT_TAB_TO_TYPE.get(alert_settings_state.active_tab_name or "")
    if active and alert_form_is_dirty(active):
        return True
    return any(alert_form_is_dirty(tab_type) for tab_type in ALERT_TAB_TO_TYPE.values())


def save_active_alert(done) -> None:
    """Save the alert type currently on screen and report success to ``done``."""
    active = ALERT_TAB_TO_TYPE.get(alert_settings_state.active_tab_name or "")
    if not active:
        done(True)
        return
    _save_alert_type(active, done)


def discard_active_alert() -> None:
    """Reload the alert type currently on screen from disk."""
    active = ALERT_TAB_TO_TYPE.get(alert_settings_state.active_tab_name or "")
    if active:
        _discard_alert_form(active)


def _alert_form_values_differ(tab_type: str) -> bool:
    """True when a visible field no longer matches the last loaded or saved value."""
    originals = alert_settings_state.get_original_values(tab_type)
    if not originals:
        return False
    elements = alert_settings_state.get_elements(tab_type)
    for name, element in elements.items():
        if name in _ALERT_DIRTY_SKIP or not element or not hasattr(element, "value"):
            continue
        if name not in originals:
            continue
        if element.__class__.__name__ in {"Button", "Label", "Icon", "Card"}:
            continue
        if _alert_values_differ(originals.get(name), element.value):
            return True
    return False


def alert_form_is_dirty(tab_type: str) -> bool:
    """True when the alert form has edits that have not been saved."""
    if tab_type in alert_settings_state.dirty_tabs:
        return True
    return _alert_form_values_differ(tab_type)


def _refresh_alert_dirty_label(tab_type: str) -> None:
    label = alert_settings_state.get_elements(tab_type).get("unsaved_label")
    if not label:
        return
    try:
        label.visible = alert_form_is_dirty(tab_type)
    except Exception:
        logger.debug("Could not refresh unsaved label for %s", tab_type, exc_info=True)


def _mount_unsaved_label(alert_type: str) -> None:
    label = ui.html(
        '<span class="alr-pill"><span class="alr-icon">edit</span>Unsaved changes</span>', sanitize=False
    )
    label.visible = False
    label.tooltip("These alert settings are not saved yet")
    alert_settings_state.get_elements(alert_type)["unsaved_label"] = label


def _remember_loaded_alert(tab_type: str) -> None:
    select = alert_settings_state.get_elements(tab_type).get("alert_select")
    if select is not None and hasattr(select, "value"):
        alert_settings_state.loaded_alert_ids[tab_type] = select.value


def _resolve_alert_tab_name(event) -> str:
    tab_name = getattr(event, "value", None)
    if not tab_name:
        args = getattr(event, "args", None)
        if isinstance(args, str):
            tab_name = args
        elif isinstance(args, dict):
            tab_name = args.get("value")
    if tab_name and not isinstance(tab_name, str):
        from ..ui_tab_transitions import _tab_label

        tab_name = _tab_label(tab_name)
    return str(tab_name or "")


def _activate_alert_tab(tab_name: str) -> None:
    """Show an alert-type tab and reload its selected alert."""
    if not tab_name:
        return
    _ensure_lazy_alert_panel(tab_name)
    alert_settings_state.active_tab_name = tab_name
    tabs = alert_settings_state.tabs_component
    alert_settings_state.tab_switch_guard = True
    try:
        if tabs is not None:
            tabs.value = tab_name
    finally:
        alert_settings_state.tab_switch_guard = False
    initialize_tab_values(tab_name)


def _discard_alert_form(alert_type: str) -> None:
    """Reload the current alert from disk so unsaved edits are dropped."""
    elements = alert_settings_state.get_elements(alert_type)
    select = elements.get("alert_select")
    value = select.value if select is not None and hasattr(select, "value") else None
    placeholders = POINTS_REWARD_SELECT_PLACEHOLDERS
    if alert_type == "points":
        if value and value != "new" and value not in placeholders:
            load_point_reward_settings(alert_type, value)
        else:
            set_default_values_for_new_point_reward(alert_type)
            store_original_values(alert_type)
            clear_changed_styling(alert_type)
    elif value and value != "new":
        load_alert_settings(alert_type, value)
    else:
        set_default_values_for_new_alert(alert_type)
        store_original_values(alert_type)
        clear_changed_styling(alert_type)
    alert_settings_state.dirty_tabs.discard(alert_type)
    _refresh_alert_dirty_label(alert_type)


def _save_alert_type(alert_type: str, done) -> None:
    """Save the alert form and report success to ``done(ok)``."""
    if alert_type == "points":
        save_point_alert(on_complete=done)
        return
    try:
        save_alert(alert_type)
        done(not alert_form_is_dirty(alert_type))
    except Exception:
        logger.error("Error saving %s alert before switch", alert_type, exc_info=True)
        done(False)


def _prompt_unsaved_alert(alert_type: str, message: str, on_discard, on_saved) -> None:
    """Stay, discard, or save before leaving an edited alert form."""
    with ui.dialog().props("persistent") as dialog, ui.card().classes("w-[460px] p-4"):
        ui.label("Unsaved changes").classes("text-lg font-bold mb-2")
        ui.label(message).classes("secondary-text mb-4")
        busy = {"value": False}

        def stay() -> None:
            if not busy["value"]:
                alert_settings_state.unsaved_prompt_open = False
                dialog.close()

        def discard() -> None:
            if busy["value"]:
                return
            dialog.close()
            on_discard()

        def save_and_continue() -> None:
            if busy["value"]:
                return
            busy["value"] = True

            def finished(ok: bool) -> None:
                busy["value"] = False
                if not ok:
                    # The save already said why; the dialog stays open with the edits.
                    return
                dialog.close()
                on_saved()

            _save_alert_type(alert_type, finished)

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Stay", on_click=stay).props("outline")
            ui.button("Discard and switch", on_click=discard).props("outline")
            ui.button("Save and switch", on_click=save_and_continue).props(
                "color=primary"
            )
        dialog.open()


def request_alert_tab_change(tab_name: str) -> None:
    """Switch alert-type tabs, prompting when the current form is dirty."""
    if alert_settings_state.tab_switch_guard:
        return
    if alert_settings_state.unsaved_prompt_open:
        tabs = alert_settings_state.tabs_component
        if tabs is not None and tab_name != alert_settings_state.active_tab_name:
            alert_settings_state.tab_switch_guard = True
            try:
                tabs.value = alert_settings_state.active_tab_name
            finally:
                alert_settings_state.tab_switch_guard = False
        return
    if not tab_name or tab_name == alert_settings_state.active_tab_name:
        return
    prev_name = alert_settings_state.active_tab_name
    prev_type = ALERT_TAB_TO_TYPE.get(prev_name)
    if prev_type and alert_form_is_dirty(prev_type):
        alert_settings_state.unsaved_prompt_open = True
        tabs = alert_settings_state.tabs_component
        alert_settings_state.tab_switch_guard = True
        try:
            if tabs is not None:
                tabs.value = prev_name
        finally:
            alert_settings_state.tab_switch_guard = False
        def _finish_prompt() -> None:
            alert_settings_state.unsaved_prompt_open = False

        def _discard_and_switch() -> None:
            alert_settings_state.ignore_field_dirty = True
            alert_settings_state.tab_switch_guard = True
            try:
                _discard_alert_form(prev_type)
                alert_settings_state.dirty_tabs.discard(prev_type)
                _activate_alert_tab(tab_name)
            finally:
                alert_settings_state.ignore_field_dirty = False
                alert_settings_state.unsaved_prompt_open = False
                alert_settings_state.tab_switch_guard = False

        def _save_and_switch() -> None:
            _finish_prompt()
            _activate_alert_tab(tab_name)

        _prompt_unsaved_alert(
            prev_type,
            "You have unsaved changes on this alert tab. Save them before switching?",
            on_discard=_discard_and_switch,
            on_saved=_save_and_switch,
        )
        return
    _activate_alert_tab(tab_name)


def _guard_alert_selection(alert_type: str, new_value):
    """Return True when the selection change should wait for a save prompt."""
    if alert_type not in alert_settings_state.loaded_alert_ids:
        return False
    loaded = alert_settings_state.loaded_alert_ids.get(alert_type)
    if new_value == loaded or not alert_form_is_dirty(alert_type):
        return False
    select = alert_settings_state.get_elements(alert_type).get("alert_select")
    if select is not None:
        with suppress_alert_selection_handler():
            select.value = loaded
    return True


def _discard_then_select(alert_type: str, new_value, handler) -> None:
    """Drop the unsaved edits, then switch; switching alone would re-trigger the prompt."""
    alert_settings_state.ignore_field_dirty = True
    try:
        _discard_alert_form(alert_type)
    finally:
        alert_settings_state.ignore_field_dirty = False
    _select_alert_programmatically(alert_type, new_value, handler)


def _select_alert_programmatically(alert_type: str, new_value, handler) -> None:
    select = alert_settings_state.get_elements(alert_type).get("alert_select")
    if select is not None:
        with suppress_alert_selection_handler():
            select.value = new_value
    handler(SimpleNamespace(value=new_value), alert_type)


@contextmanager
def suppress_alert_selection_handler():
    """Prevent alert/point reward dropdown on_change handlers during init."""
    alert_settings_state.suppress_selection_handler_depth += 1
    try:
        yield
    finally:
        alert_settings_state.suppress_selection_handler_depth -= 1


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

_TEST_ALERT_REQUIRED_ELEMENTS = (
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
)


def _element_value(elements: dict, name: str, default=None):
    """Read a NiceGUI element value from the tab elements dict."""
    element = elements.get(name)
    if element is None:
        return default
    value = getattr(element, "value", default)
    if value is None:
        return default
    return value


def _create_test_alert_button(alert_type: str) -> ui.button:
    """Test Alert control — outline style with click handler bound to the button."""

    def _run_test(_event=None):
        test_alert(alert_type)

    btn = outline_button(
        "Test Alert",
        _run_test,
        icon="play_arrow",
        extra_classes="text-sm",
    )
    btn.tooltip("Test the current alert settings").classes("bg-theme-surface")
    return btn


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


# ---------------------------------------------------------------------------
#  Layout: alert list (left) + detail with compact sections (right)
# ---------------------------------------------------------------------------

# Sub-tab icons, in tab order.
_ALERT_TAB_ICONS = {
    "Bits": "diamond",
    "Subscriptions": "star",
    "Streaks": "local_fire_department",
    "Gift Subs": "redeem",
    "Donations": "volunteer_activism",
    "Raids": "groups",
    "Follows": "favorite",
    "Channel Points": "toll",
}

# Unit shown after the trigger amount.
_TRIGGER_UNITS = {
    "bits": "bits",
    "subs": "months",
    "streaks": "streams",
    "giftsubs": "gifts",
    "donations": "",
    "raids": "viewers",
}

# Per-type view state that is not a form field (kept out of the elements dict so
# dirty tracking never sees it).
_ALERT_VIEW: dict = {}
_TAB_COUNT_LABELS: dict = {}


def _view(alert_type: str) -> dict:
    return _ALERT_VIEW.setdefault(alert_type, {})


def _el(alert_type: str) -> dict:
    return alert_settings_state.get_elements(alert_type)


def _tracker(alert_type: str, name: str):
    """on_change / "change" handler that feeds track_field_change for one field."""
    return lambda e, at=alert_type, n=name: track_field_change(n, _el(at)[n], e, at)


def _icon_html(name: str, cls: str = "") -> str:
    return f'<span class="alr-icon {cls}">{name}</span>'


def _section(icon: str, title: str, right_html: str = ""):
    sec = ui.element("div").classes("alr-sec")
    with sec:
        ui.html(
            f'{_icon_html(icon)}<span>{html_lib.escape(title)}</span><span class="alr-spacer"></span>{right_html}',
            sanitize=False,
        ).classes("alr-sec-head")
    return sec


def _label_with_help(label: str, tip: str = "", tag: str = "") -> None:
    with ui.element("div").classes("alr-label"):
        ui.label(label)
        if tag:
            ui.label(tag).classes("alr-tag")
        if tip:
            ui.html(_icon_html("help_outline"), sanitize=False).classes("alr-help").tooltip(tip)


@contextmanager
def _row(label: str, tip: str = "", *, tag: str = "", grow: bool = False):
    """One line: label (+ hover help) on the left, the control on the right."""
    row = ui.element("div").classes("alr-row")
    with row:
        _label_with_help(label, tip, tag)
        with ui.element("div").classes("alr-ctrl is-grow" if grow else "alr-ctrl"):
            yield row


@contextmanager
def _stack(label: str, tip: str = ""):
    """Label above a full-width control."""
    stack = ui.element("div").classes("alr-stack")
    with stack:
        _label_with_help(label, tip)
        yield stack


def _switch_field(alert_type: str, name: str):
    """Dense switch registered under ``name``; change tracking as before."""
    sw = ui.switch().props("dense")
    _el(alert_type)[name] = sw
    sw.on("change", _tracker(alert_type, name))
    return sw


def _number_field(alert_type: str, name: str, tooltip: str, *, value=None, min=None, max=None, step=None, wide=False):
    kwargs = {"tooltip": tooltip, "classes": "alr-num-wide" if wide else "alr-num", "on_change": _tracker(alert_type, name)}
    for key, val in (("value", value), ("min", min), ("max", max), ("step", step)):
        if val is not None:
            kwargs[key] = val
    field = form_number(**kwargs)
    _el(alert_type)[name] = field
    return field


def _unit(text: str) -> None:
    if text:
        ui.label(text).classes("alr-unit")


# ----- file cards -----------------------------------------------------------

_IMAGE_EXTS = (".gif", ".png", ".jpg", ".jpeg", ".webp", ".apng", ".avif")


def _overlay_media_url(directory: str, filename: str) -> str:
    """URL the overlay server uses for an /assets/ file, or '' when unavailable."""
    if not directory or not filename or not filename.lower().endswith(_IMAGE_EXTS):
        return ""
    web_dir = directory.replace("\\", "/")
    if not web_dir.startswith("/assets/"):
        return ""
    try:
        from .. import web_engine as web_engine_module

        inst = getattr(web_engine_module, "web_engine_instance", None)
        if not inst or not getattr(inst, "is_running", False):
            return ""
        port = getattr(inst, "port", 5000) or 5000
    except Exception:
        return ""
    from urllib.parse import quote

    base = web_dir if web_dir.endswith("/") else web_dir + "/"
    return f"http://127.0.0.1:{port}{quote(base + filename)}"


def _media_card(alert_type: str, *, icon: str, browse: str, dir_key: str, file_key: str | None,
                empty: str, browse_tip: str, dir_tip: str, file_tip: str = "", dir_label: str = "Folder"):
    """File card: name + folder, Browse, and ✎ to type the folder/file by hand.

    The real inputs (``dir_key`` / ``file_key``) live in the collapsible edit area,
    so loading, saving, dirty tracking and Browse keep using them unchanged.
    """
    card = ui.element("div").classes("alr-media")
    with card:
        with ui.element("div").classes("alr-media-main"):
            thumb = ui.html(_icon_html(icon), sanitize=False).classes("alr-thumb")
            with ui.element("div").classes("alr-media-text"):
                name_label = ui.label(empty).classes("alr-media-name is-empty")
                path_label = ui.label("Not set").classes("alr-media-path")
            edit_btn = ui.button(icon="edit").props("flat round dense size=sm")
            edit_btn.tooltip("Type the path")
            outline_button("Browse", lambda at=alert_type, b=browse: handle_browse(b, at), icon="folder_open").props(
                "dense"
            ).classes("px-2").tooltip(browse_tip)
        edit = ui.element("div").classes("alr-edit")
        edit.visible = False
        with edit:
            dir_input = form_input(tooltip=dir_tip, label=dir_label, classes="w-full", on_change=_tracker(alert_type, dir_key))
            _el(alert_type)[dir_key] = dir_input
            file_input = None
            if file_key:
                file_input = form_input(tooltip=file_tip, label="File", classes="w-full", on_change=_tracker(alert_type, file_key))
                _el(alert_type)[file_key] = file_input

    def refresh(_=None) -> None:
        directory = str(dir_input.value or "")
        filename = str(file_input.value or "") if file_input is not None else ""
        if file_input is not None:
            title = filename
        else:
            parts = [p for p in directory.replace("\\", "/").split("/") if p]
            title = f"{parts[-1]} folder" if parts else ""
        if title:
            name_label.set_text(title)
            name_label.classes(remove="is-empty")
            path_label.set_text(directory or "No folder set")
        else:
            name_label.set_text(empty)
            name_label.classes(add="is-empty")
            path_label.set_text("Not set")
        url = _overlay_media_url(directory, filename)
        thumb.set_content(
            f'<img src="{html_lib.escape(url, quote=True)}" alt="">' if url else _icon_html(icon)
        )

    def toggle_edit() -> None:
        edit.visible = not edit.visible

    edit_btn.on_click(toggle_edit)
    dir_input.on_value_change(refresh)
    if file_input is not None:
        file_input.on_value_change(refresh)
    refresh()
    return card


# ----- dependent dimming ----------------------------------------------------


def _refresh_alert_dims(alert_type: str) -> None:
    """Dim settings that do nothing with the current switches."""
    view = _view(alert_type)
    elements = _el(alert_type)

    def on(name: str) -> bool:
        el = elements.get(name)
        return bool(getattr(el, "value", False)) if el is not None else False

    tts = on("tts_enabled_switch")
    custom = tts and getattr(elements.get("tts_source_select"), "value", "") == "custom_message"
    rules = {
        "tts_source": not tts,
        "tts_custom": not custom,
        "random": not on("randomized_switch"),
        "extra": not (on("randomized_switch") and on("randomized_extra_switch")),
        "extra_switch": not on("randomized_switch"),
        "per_stream": not on("twitch_max_per_stream_switch"),
        "per_user": not on("twitch_max_per_user_switch"),
        "cooldown": not on("twitch_cooldown_switch"),
        "prompt": not on("twitch_user_input_switch"),
    }
    for key, dim in rules.items():
        for el in view.get(f"dim_{key}", ()):
            if dim:
                el.classes(add="is-dim")
            else:
                el.classes(remove="is-dim")
    # Extra random sounds only play together with random sounds.
    extra = elements.get("randomized_extra_switch")
    if extra is not None and hasattr(extra, "set_enabled"):
        extra.set_enabled(on("randomized_switch"))


def _dim_on(alert_type: str, key: str, element) -> None:
    _view(alert_type).setdefault(f"dim_{key}", []).append(element)


def _watch_dims(alert_type: str, *names: str) -> None:
    for name in names:
        el = _el(alert_type).get(name)
        if el is not None:
            el.on_value_change(lambda _e, at=alert_type: _refresh_alert_dims(at))


# ----- sections -------------------------------------------------------------


def _build_playback_section(alert_type: str) -> None:
    with _section("tune", "Playback"):
        with _row("Duration", "How long the alert stays on screen"):
            _number_field(alert_type, "duration_input", "How long the alert will display (seconds)", value=3.0, min=0.1, step=0.1)
            _unit("sec")
        with _row("Stackable", "Allow multiple alerts of this type to stack"):
            _switch_field(alert_type, "stackable_switch")
        if alert_type == "points":
            with _row("Audio only", "Play only audio without any visual elements (useful for channel points)"):
                _switch_field(alert_type, "audio_only_switch")


def create_visual_settings_section(alert_type: str):
    """GIF / image file card."""
    with _section("image", "Visual"):
        _media_card(
            alert_type, icon="gif_box", browse="gif", dir_key="gif_dir_input", file_key="gif_file_input",
            empty="No GIF or image", browse_tip="Browse for GIF/image file",
            dir_tip="Directory containing the GIF file", file_tip="Name of the GIF file",
        )


def create_audio_settings_section(alert_type: str):
    """Primary sound card, volume and fades."""
    with _section("volume_up", "Sound"):
        _media_card(
            alert_type, icon="music_note", browse="primary", dir_key="primary_dir_input", file_key="primary_file_input",
            empty="No sound", browse_tip="Browse for the primary sound file",
            dir_tip="Directory containing the primary sound file", file_tip="Name of the primary sound file",
        )
        with _row("Volume", "Volume level for the alert sound (0-100%)", grow=True):
            slider = ui.slider(min=0, max=100, value=100).classes("alr-slider")
            _el(alert_type)["volume_input"] = slider
            _el(alert_type)["volume_value_label"] = ui.label("100%").classes("alr-pct")

            def handle_volume_change(e, at=alert_type):
                track_field_change("volume_input", _el(at)["volume_input"], e, at)
                slider_element = _el(at)["volume_input"]
                value = int(slider_element.value) if slider_element.value is not None else 100
                _el(at)["volume_value_label"].set_text(f"{value}%")

            slider.on("change", handle_volume_change)
        with ui.element("div").classes("alr-row is-pair"):
            _label_with_help("Fade in", "Time in milliseconds for the audio to fade in")
            with ui.element("div").classes("alr-ctrl"):
                _number_field(alert_type, "fade_in_input", "Time in milliseconds for the audio to fade in", value=0, min=0)
                _unit("ms")
            with ui.element("div").classes("alr-label alr-label-r"):
                ui.label("Fade out")
            with ui.element("div").classes("alr-ctrl"):
                _number_field(alert_type, "fade_out_input", "Time in milliseconds for the audio to fade out", value=0, min=0)
                _unit("ms")


def create_tts_settings_section(alert_type: str):
    """Text-to-speech settings."""
    with _section("record_voice_over", "Text-to-speech"):
        with _row("Speak this alert", "Speak this alert using the browser text-to-speech system"):
            _switch_field(alert_type, "tts_enabled_switch")
        with _row("Speak", "Choose whether TTS should speak the alert message or a custom static message") as source_row:

            def handle_tts_source_change(e, at=alert_type):
                update_tts_custom_message_visibility(at)
                track_field_change("tts_source_select", _el(at)["tts_source_select"], e, at)

            _el(alert_type)["tts_source_select"] = form_select(
                tooltip="Choose whether TTS should speak the alert message or a custom static message",
                options=TTS_SOURCE_OPTIONS,
                value="alert_message",
                classes="w-40",
                on_change=handle_tts_source_change,
            )
        _dim_on(alert_type, "tts_source", source_row)
        with _stack("Custom message", "Static message to speak when Custom Message is selected") as custom_stack:
            _el(alert_type)["tts_custom_message_input"] = form_input(
                tooltip="Static message to speak when Custom Message is selected",
                classes="w-full",
                placeholder="Thanks for the support!",
                on_change=_tracker(alert_type, "tts_custom_message_input"),
            )
        _dim_on(alert_type, "tts_custom", custom_stack)
    _watch_dims(alert_type, "tts_enabled_switch", "tts_source_select")


def create_randomizer_settings_section(alert_type: str):
    """Random and extra random sounds."""
    with _section("shuffle", "Randomizer"):
        with _row("Random sounds", "Enable random sound playback instead of primary sound"):
            _switch_field(alert_type, "randomized_switch")
        with _row("Chance", "Percentage chance to play a random sound instead of the primary sound") as row:
            _number_field(alert_type, "random_chance_input",
                          "Percentage chance to play a random sound instead of the primary sound", value=0, min=0, max=100)
            _unit("%")
        _dim_on(alert_type, "random", row)
        card = _media_card(
            alert_type, icon="folder", browse="random", dir_key="random_dir_input", file_key=None,
            empty="No folder", browse_tip="Browse for directory containing random sound files",
            dir_tip="Directory containing random sound files", dir_label="Directory",
        )
        _dim_on(alert_type, "random", card)
        ui.element("div").classes("alr-div")
        with _row("Extra random sound", "Play an extra random sound after the main sound (needs Random sounds on)") as extra_row:
            _switch_field(alert_type, "randomized_extra_switch")
        _dim_on(alert_type, "extra_switch", extra_row)
        with _row("Chance", "Percentage chance to play an additional random sound after the primary/random sound") as row:
            _number_field(alert_type, "extra_chance_input",
                          "Percentage chance to play an additional random sound after the primary/random sound",
                          value=0, min=0, max=100)
            _unit("%")
        _dim_on(alert_type, "extra", row)
        card = _media_card(
            alert_type, icon="folder", browse="extra", dir_key="extra_dir_input", file_key=None,
            empty="No folder", browse_tip="Browse for directory containing extra sound files",
            dir_tip="Directory containing extra random sound files", dir_label="Directory",
        )
        _dim_on(alert_type, "extra", card)
    _watch_dims(alert_type, "randomized_switch", "randomized_extra_switch")
    _el(alert_type)["randomized_switch"].on_value_change(lambda e, at=alert_type: _random_switch_changed(at, e.value))


def _random_switch_changed(alert_type: str, enabled: bool) -> None:
    """Turning Random sounds off also turns Extra random sound off (and counts as a change)."""
    extra = _el(alert_type).get("randomized_extra_switch")
    if enabled or extra is None or not extra.value:
        return
    extra.value = False
    track_field_change("randomized_extra_switch", extra, False, alert_type)


def create_twitch_options_section(alert_type: str):
    """Twitch reward fields (Channel Points only)."""
    with _section("api", "Twitch reward", '<span class="alr-tag">Synced to Twitch</span>'):
        with _stack("Reward title", "The title of the Twitch point reward"):
            _el(alert_type)["twitch_title_input"] = form_input(
                tooltip="The title of the Twitch point reward", classes="w-full",
                on_change=_tracker(alert_type, "twitch_title_input"),
            )
        with _row("Cost", "How many channel points this reward costs"):
            _number_field(alert_type, "twitch_cost_input", "How many channel points this reward costs", value=100, min=1, wide=True)
            _unit("points")
        with _row("Enabled on Twitch", "Whether the reward is enabled on Twitch"):
            _switch_field(alert_type, "twitch_enabled_switch")
        with _row("Viewer must type a message", "Whether users must provide text input when redeeming"):
            _switch_field(alert_type, "twitch_user_input_switch")
        with _stack("Prompt", "Prompt text shown to users when they redeem (if user input is required)") as prompt:
            _el(alert_type)["twitch_user_input_prompt"] = form_input(
                tooltip="Prompt text shown to users when they redeem (if user input is required)", classes="w-full",
                on_change=_tracker(alert_type, "twitch_user_input_prompt"),
            )
        _dim_on(alert_type, "prompt", prompt)
        for switch_name, input_name, label, switch_tip, input_tip, default, unit, dim_key in (
            ("twitch_max_per_stream_switch", "twitch_max_per_stream_input", "Limit per stream",
             "Enable limit on total redemptions per stream", "Maximum number of redemptions allowed per stream", 1, "", "per_stream"),
            ("twitch_max_per_user_switch", "twitch_max_per_user_input", "Limit per viewer",
             "Enable limit on redemptions per user per stream", "Maximum number of redemptions per user per stream", 1, "", "per_user"),
            ("twitch_cooldown_switch", "twitch_cooldown_input", "Global cooldown",
             "Enable global cooldown between redemptions", "Cooldown time in seconds between redemptions", 60, "sec", "cooldown"),
        ):
            with _row(label, switch_tip):
                _switch_field(alert_type, switch_name)
                with ui.element("div").classes("alr-dimc") as amount:
                    _number_field(alert_type, input_name, input_tip, value=default, min=1)
                    _unit(unit)
                _dim_on(alert_type, dim_key, amount)
        with _row("Skip request queue", "Skip the request queue and auto-fulfill redemptions"):
            _switch_field(alert_type, "twitch_skip_queue_switch")
    _watch_dims(alert_type, "twitch_user_input_switch", "twitch_max_per_stream_switch",
                "twitch_max_per_user_switch", "twitch_cooldown_switch")


# ----- alert list -----------------------------------------------------------

_POINTS_LABEL_RE = re.compile(r"^(?P<title>.*) \((?P<cost>\d+) points\)$")


def _item_flags(data: dict) -> list:
    if not isinstance(data, dict):
        return []
    flags = []
    if data.get("single_audio_name"):
        flags.append(("volume_up", "Sound"))
    if data.get("gif_name"):
        flags.append(("image", "GIF or image"))
    if data.get("randomized") or data.get("randomized_extra"):
        flags.append(("shuffle", "Randomizer"))
    if data.get("tts_enabled"):
        flags.append(("record_voice_over", "Text-to-speech"))
    return flags


def _alert_list_entries(alert_type: str):
    """(id, name, meta_html, flags_html, off) for each list entry, from the select's options."""
    select = _el(alert_type).get("alert_select")
    options = dict(getattr(select, "options", None) or {}) if select is not None else {}
    saved = get_alerts_for_type(alert_type)
    fallback_id = alertutils.AlertSettings.FALLBACK_ALERT_ID
    entries = []
    for alert_id, label in options.items():
        if alert_id == "new" or alert_id in POINTS_REWARD_SELECT_PLACEHOLDERS:
            continue
        data = saved.get(alert_id) or {}
        flags = "".join(
            f'<span class="alr-icon alr-flag" title="{tip}">{icon}</span>' for icon, tip in _item_flags(data)
        )
        off = False
        if alert_type == "points":
            match = _POINTS_LABEL_RE.match(str(label))
            name = match.group("title") if match else str(label)
            cost = f"{int(match.group('cost')):,}" if match else ""
            meta = f'<span class="alr-cost">{_icon_html("toll")}{cost}</span>' if cost else ""
            off = not data.get("enable_alert", False)
            if off:
                flags = '<span class="alr-off">Not an alert</span>'
        else:
            name = str(label)
            numeric = alert_id[len(alert_type):] if alert_id.startswith(alert_type) else alert_id
            if alert_id == fallback_id:
                meta = '<span class="alr-kind is-fb">Fallback</span>'
            elif "-" in numeric:
                meta = '<span class="alr-kind">Range</span>'
            else:
                meta = ""
        entries.append((alert_id, name, meta, flags, off))
    return entries, options


def _placeholder_message(options: dict) -> str:
    for key in ("loading", "not_connected", "no_channel_points", "error", "no_rewards"):
        if key in options:
            return str(options[key])
    return ""


def _refresh_tab_count(alert_type: str, count: int) -> None:
    tab_name = next((n for n, t in ALERT_TAB_TO_TYPE.items() if t == alert_type), None)
    label = _TAB_COUNT_LABELS.get(tab_name)
    if label is not None:
        label.set_text(str(count))
        label.visible = count > 0


def _render_alert_list(alert_type: str) -> None:
    """Redraw the alert list and the header title from the selector's options/value."""
    view = _view(alert_type)
    container = view.get("list")
    select = _el(alert_type).get("alert_select")
    if container is None or select is None:
        return
    entries, options = _alert_list_entries(alert_type)
    current = select.value
    query = str(view.get("query") or "").strip().lower()
    _refresh_tab_count(alert_type, len(entries))

    new_btn = view.get("new")
    if new_btn is not None:
        if current == "new":
            new_btn.classes(add="is-active")
        else:
            new_btn.classes(remove="is-active")

    container.clear()
    with container:
        shown = 0
        for alert_id, name, meta, flags, off in entries:
            if query and query not in name.lower():
                continue
            shown += 1
            classes = "alr-item"
            if alert_id == current:
                classes += " is-active"
            if off:
                classes += " is-off"
            item = ui.element("div").on("click", lambda _e, at=alert_type, aid=alert_id: _pick_alert(at, aid))
            item.classes(classes)
            with item:
                ui.html(
                    f'<div class="alr-item-top"><span class="alr-item-name">{html_lib.escape(name)}</span>{meta}</div>'
                    f'<div class="alr-item-flags">{flags}</div>',
                    sanitize=False,
                ).classes("contents")
        if not shown:
            message = _placeholder_message(options) if alert_type == "points" else ""
            if not message:
                message = "No alerts match your search." if query else "No alerts yet. Click New alert to make one."
            ui.label(message).classes("alr-empty")

    title = view.get("title")
    if title is not None:
        if current is None:
            text = "Choose a reward" if alert_type == "points" else "Choose an alert"
        elif current == "new":
            text = "New reward" if alert_type == "points" else "New alert"
        else:
            text = next((name for aid, name, *_rest in entries if aid == current), str(options.get(current, "")))
        title.set_text(text or ("Channel Points" if alert_type == "points" else "Alert"))


def _pick_alert(alert_type: str, alert_id: str) -> None:
    """List click: same path as choosing in the old dropdown (unsaved prompt included)."""
    select = _el(alert_type).get("alert_select")
    if select is None or select.value == alert_id:
        return
    select.value = alert_id


def _build_alert_list(alert_type: str, select) -> None:
    view = _view(alert_type)
    with ui.element("div").classes("alr-rail"):
        with ui.element("div").classes("alr-rail-head"):
            search = ui.input(placeholder="Search").props("dense outlined clearable")
            with search.add_slot("prepend"):
                ui.icon("search").classes("text-base")

            def on_search(e, at=alert_type):
                _view(at)["query"] = e.value or ""
                _render_alert_list(at)

            search.on_value_change(on_search)
            if alert_type == "points":
                refresh_btn = ui.button(
                    icon="refresh", on_click=lambda: load_twitch_point_rewards(user_initiated=True)
                ).props(
                    "flat round dense size=sm"
                )
                refresh_btn.tooltip("Refresh the list of Twitch point rewards")
        new_btn = ui.element("div").on("click", lambda _e, at=alert_type: _pick_alert(at, "new"))
        new_btn.classes("alr-new")
        with new_btn:
            ui.html(
                f'{_icon_html("add")}<span>{"New reward" if alert_type == "points" else "New alert"}</span>',
                sanitize=False,
            ).classes("contents")
        view["new"] = new_btn
        view["list"] = ui.element("div").classes("alr-list")
        if alert_type == "subs":
            with ui.element("div").classes("alr-rail-foot"):
                with _row("Resub fallback", "When enabled, the Resub Fallback alert plays for resubs that don't match any configured month alert"):
                    _el(alert_type)["fallback_toggle"] = ui.switch(
                        value=alertutils.alert_state_manager.get_resub_fallback_enabled(),
                        on_change=lambda e: _set_resub_fallback(bool(e.value)),
                    ).props("dense")
    select.on_value_change(lambda _e, at=alert_type: _render_alert_list(at))


# ----- header ---------------------------------------------------------------


def _build_trigger(alert_type: str) -> None:
    """Exact/Range switch with amount or min–max (amount-based types)."""
    elements = _el(alert_type)
    unit = _TRIGGER_UNITS.get(alert_type, "")
    with ui.element("div").classes("alr-trigger") as trigger:
        ui.label("Triggers on").classes("alr-trig-label")
        range_toggle = ui.toggle({False: "Exact", True: "Range"}, value=False).props(
            "dense no-caps unelevated toggle-color=primary"
        )
        range_toggle.tooltip("Toggle between exact amount and range mode")
        elements["range_toggle"] = range_toggle
        exact_row = ui.row().classes("items-center gap-1 no-wrap")
        elements["exact_input_row"] = exact_row
        exact_tips = {
            "subs": "Set the subscription month for this alert (1 for new sub, 2+ for resub months)",
            "streaks": "Watch streak count (consecutive streams) that triggers this alert",
        }
        with exact_row:
            _number_field(alert_type, "amount_input",
                          exact_tips.get(alert_type, "Set the exact amount for this alert to trigger"), wide=True)
            _unit(unit)
        range_row = ui.row().classes("items-center gap-1 no-wrap")
        range_row.visible = False
        elements["range_input_row"] = range_row
        noun = {"subs": "subscription month", "streaks": "streak count"}.get(alert_type, "amount")
        with range_row:
            _number_field(alert_type, "min_input", f"Minimum {noun} for this alert range")
            ui.label("to").classes("alr-dash")
            _number_field(alert_type, "max_input", f"Maximum {noun} for this alert range")
            _unit(unit)
        range_toggle.on_value_change(lambda e: toggle_range_inputs(e, exact_row, range_row))
    _view(alert_type)["trigger"] = trigger


def _build_detail_header(alert_type: str, save_handler) -> None:
    view = _view(alert_type)
    with ui.element("div").classes("alr-dhead"):
        with ui.element("div").classes("alr-dtitle"):
            view["title"] = ui.label("").classes("alr-h")
            if alert_type in _QUANTITY_ALERT_TYPES:
                _build_trigger(alert_type)
            elif alert_type == "follows":
                with ui.element("div").classes("alr-trigger"):
                    ui.label("Plays for every new follower").classes("alr-trig-label")
            elif alert_type == "points":
                with ui.element("div").classes("alr-trigger"):
                    with _row("Use as alert", "Enable this point reward as an alert in the app"):
                        _switch_field(alert_type, "enable_alert_switch")
        _mount_unsaved_label(alert_type)
        ui.element("div").classes("alr-spacer")
        _create_test_alert_button(alert_type).props("dense").classes("px-3")
        duplicate_btn = outline_button("", lambda at=alert_type: duplicate_current_alert(at), icon="content_copy")
        duplicate_btn.props("dense").classes("px-2").tooltip(
            "Copy this reward into a new unsaved reward" if alert_type == "points" else "Copy this alert into a new unsaved alert"
        )
        delete_btn = destructive_button("", lambda at=alert_type: show_delete_confirmation(at), icon="delete")
        delete_btn.props("dense").classes("px-2").tooltip("Delete this alert permanently")
        delete_btn.visible = False
        _el(alert_type)["delete_btn"] = delete_btn
        save_btn = primary_button("Save", save_handler, icon="check").props("dense").classes("px-4")
        save_btn.tooltip("Save your alert settings")
        if alert_type == "points":
            _el(alert_type)["save_btn"] = save_btn


def _build_alert_shell(alert_type: str, select, save_handler) -> None:
    """List + detail for one alert type (the selector itself stays hidden)."""
    from ..help_system.help_render import inject_page_stylesheet, promote_color_importance, scope_selectors

    inject_page_stylesheet("mycelian-alerts", promote_color_importance(scope_selectors(ALERTS_CSS, ".alr")))
    with ui.element("div").classes("alr-shell"):
        _build_alert_list(alert_type, select)
        with ui.element("div").classes("alr-detail"):
            _build_detail_header(alert_type, save_handler)
            if alert_type == "points":
                banner = ui.html(
                    f'{_icon_html("lock")}<span>Custom Channel Point rewards require Twitch Affiliate or Partner. '
                    "This account does not have Channel Points unlocked yet.</span>",
                    sanitize=False,
                ).classes("alr-banner")
                banner.visible = False
                _el(alert_type)["channel_points_locked_banner"] = banner
            with ui.element("div").classes("alr-dbody"):
                if alert_type == "points":
                    with ui.element("div").classes("alr-col"):
                        _build_playback_section(alert_type)
                        create_visual_settings_section(alert_type)
                        create_tts_settings_section(alert_type)
                    with ui.element("div").classes("alr-col"):
                        create_audio_settings_section(alert_type)
                        create_randomizer_settings_section(alert_type)
                    with ui.element("div").classes("alr-col"):
                        create_twitch_options_section(alert_type)
                else:
                    with ui.element("div").classes("alr-col"):
                        _build_playback_section(alert_type)
                        create_visual_settings_section(alert_type)
                    with ui.element("div").classes("alr-col"):
                        create_audio_settings_section(alert_type)
                        create_tts_settings_section(alert_type)
                    with ui.element("div").classes("alr-col"):
                        create_randomizer_settings_section(alert_type)
    _refresh_alert_dims(alert_type)
    _render_alert_list(alert_type)


def create_alert_settings_tab():
    """Create the alert settings tab content"""

    from ..help_system.help_render import inject_page_stylesheet

    inject_page_stylesheet("mycelian-alerts-tabs", ALERTS_TAB_CSS)

    with ui.element("div").classes("tab-surface w-full flex-1 min-h-0 flex flex-col p-4"):
        with ui.element("div").classes("mycelian-sub-tab-shell w-full flex-1 min-h-0 flex flex-col"):
            # Create tabs for different alert types
            with ui.tabs().classes("w-full settings-tabs mycelian-sub-tabs").props("inline-label") as alert_tabs:
                tab_refs = {}
                for tab_name, tab_type in ALERT_TAB_TO_TYPE.items():
                    tab = ui.tab(tab_name, icon=_ALERT_TAB_ICONS[tab_name])
                    with tab:
                        count_label = ui.label("").classes("alr-tab-count")
                        count_label.visible = False
                    _TAB_COUNT_LABELS[tab_name] = count_label
                    tab_refs[tab_name] = tab
                    if tab_type != "points":
                        _refresh_tab_count(tab_type, _initial_alert_count(tab_type))
                bits_tab = tab_refs["Bits"]
                subs_tab = tab_refs["Subscriptions"]
                streaks_tab = tab_refs["Streaks"]
                giftsubs_tab = tab_refs["Gift Subs"]
                donations_tab = tab_refs["Donations"]
                raids_tab = tab_refs["Raids"]
                follows_tab = tab_refs["Follows"]
                points_tab = tab_refs["Channel Points"]

            set_alerts_ui_references(alert_tabs)
            alert_settings_state.tabs_component = alert_tabs
            alert_settings_state.active_tab_name = "Bits"

            # Quasar tabs emit update:model-value, which NiceGUI delivers as
            # on_value_change. A DOM "change" listener does not fire here.
            def _on_alert_tab_change(e):
                tab_name = _resolve_alert_tab_name(e)
                if tab_name:
                    request_alert_tab_change(tab_name)

            alert_tabs.on_value_change(_on_alert_tab_change)

            def check_alert_tab_changes():
                if (
                    alert_settings_state.unsaved_prompt_open
                    or alert_settings_state.tab_switch_guard
                    or alert_settings_state.ignore_field_dirty
                ):
                    return
                current_alert_tab = _resolve_alert_tab_name(
                    SimpleNamespace(value=alert_tabs.value)
                )
                if (
                    not current_alert_tab
                    or current_alert_tab == alert_settings_state.active_tab_name
                ):
                    return
                request_alert_tab_change(current_alert_tab)

            layout_schedule(0.4, check_alert_tab_changes, active=True)

            # Main content area with tab panels
            with ui.tab_panels(alert_tabs, value=bits_tab).classes(
                "w-full flex-1 min-h-0 grow"
            ):
                # Bits Alerts Tab
                with ui.tab_panel(bits_tab).classes(
                    "transition-all duration-300 w-full h-full min-h-0 flex flex-col"
                ):
                    create_alert_type_panel("bits")

                # Subscription Alerts Tab
                def _defer_alert_panel(tab_name, builder):
                    holder = ui.column().classes(
                        "w-full h-full min-h-0 flex flex-col"
                    )

                    def build(holder=holder, builder=builder):
                        with holder:
                            builder()

                    _LAZY_ALERT_PANEL_BUILDERS[tab_name] = build

                with ui.tab_panel(subs_tab).classes(
                    "transition-all duration-300 w-full h-full min-h-0 flex flex-col"
                ):
                    _defer_alert_panel(
                        "Subscriptions", lambda: create_alert_type_panel("subs")
                    )

                with ui.tab_panel(streaks_tab).classes(
                    "transition-all duration-300 w-full h-full min-h-0 flex flex-col"
                ):
                    _defer_alert_panel(
                        "Streaks", lambda: create_alert_type_panel("streaks")
                    )

                # Gift Sub Alerts Tab
                with ui.tab_panel(giftsubs_tab).classes(
                    "transition-all duration-300 w-full h-full min-h-0 flex flex-col"
                ):
                    _defer_alert_panel(
                        "Gift Subs", lambda: create_alert_type_panel("giftsubs")
                    )

                # Donation Alerts Tab
                with ui.tab_panel(donations_tab).classes(
                    "transition-all duration-300 w-full h-full min-h-0 flex flex-col"
                ):
                    _defer_alert_panel(
                        "Donations", lambda: create_alert_type_panel("donations")
                    )

                # Raid Alerts Tab
                with ui.tab_panel(raids_tab).classes(
                    "transition-all duration-300 w-full h-full min-h-0 flex flex-col"
                ):
                    _defer_alert_panel(
                        "Raids", lambda: create_alert_type_panel("raids")
                    )

                # Follow Alerts Tab
                with ui.tab_panel(follows_tab).classes(
                    "transition-all duration-300 w-full h-full min-h-0 flex flex-col"
                ):
                    _defer_alert_panel(
                        "Follows", lambda: create_alert_type_panel("follows")
                    )

                # Channel Points Tab
                with ui.tab_panel(points_tab).classes(
                    "transition-all duration-300 w-full h-full min-h-0 flex flex-col"
                ):
                    _defer_alert_panel("Channel Points", create_points_alert_panel)

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
                    layout_schedule(0.5, load_points_when_visible, once=True)
                    layout_schedule(1.5, load_points_when_visible, once=True)
                    layout_schedule(3.0, load_points_when_visible, once=True)

            # Initialize first tab immediately
            layout_schedule(0.5, lambda: initialize_tab_values("Bits"), once=True)

    ui.run_javascript(
        "window.mycelianInitSubTabSeams && window.mycelianInitSubTabSeams()"
    )


def initialize_tab_values(tab_name):
    """Initialize values when a tab is selected"""
    with suppress_alert_selection_handler():
        try:
            # Map tab names to alert types
            tab_map = {
                "Bits": "bits",
                "Subscriptions": "subs",
                "Streaks": "streaks",
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

            refresh_alert_dropdowns()

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
                        logger.error(
                            f"Error loading Twitch point rewards: {str(load_err)}"
                        )

                # Simple delayed load
                layout_schedule(0.3, simple_load_rewards, once=True)

            # If alert_select has a valid value, try to reload that alert
            if (
                alert_settings_state.get_elements(alert_type).get("alert_select")
                and alert_settings_state.get_elements(alert_type)
                .get("alert_select")
                .value
                and alert_settings_state.get_elements(alert_type)
                .get("alert_select")
                .value
                not in ["new"]
            ):
                try:
                    selected_alert_id = (
                        alert_settings_state.get_elements(alert_type)
                        .get("alert_select")
                        .value
                    )
                    logger.debug(
                        f"Reloading currently selected alert: {selected_alert_id}"
                    )
                    load_alert_settings(alert_type, selected_alert_id, quiet=True)
                except Exception as reload_err:
                    logger.error(
                        f"Error reloading selected alert: {str(reload_err)}",
                        exc_info=True,
                    )

            # Update delete button visibility based on current selection
            update_delete_button_visibility(alert_type)

        except Exception as e:
            logger.error(f"Error initializing tab values: {str(e)}", exc_info=True)


def _initial_alert_count(alert_type: str) -> int:
    alerts = get_alerts_for_type(alert_type)
    count = len(alerts)
    if alert_type == "subs" and alertutils.AlertSettings.FALLBACK_ALERT_ID not in alerts:
        count += 1
    return count


def create_alert_type_panel(alert_type: str):
    """Build the list + detail form for one alert type (bits, subs, ...)."""
    # Set the current tab in the state manager
    alert_settings_state.set_current_tab(alert_type)

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

    with ui.element("div").classes("alr"):
        # The selector drives loading, the unsaved prompt and saving; the list on the
        # left sets its value, so it stays (hidden) as the single source of truth.
        select = form_select(
            tooltip="Choose an existing alert or create a new one",
            options=alert_options,
            classes="hidden",
            on_change=lambda e: handle_alert_selection(e, alert_type),
        )
        alert_settings_state.get_elements(alert_type)["alert_select"] = select
        _build_alert_shell(alert_type, select, lambda _e=None, at=alert_type: save_alert(at))

    # At the end of building the tab, trigger the default alert to load or store original values
    layout_schedule(0.5, lambda: initialize_alert_values(alert_type), once=True)

    # Register all UI elements for tracking
    layout_schedule(1.0, register_ui_elements, once=True)

    logger.debug(f"Created alert panel for {alert_type}")
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
        notify(
            "The list may look empty even though your alerts are still saved.",
            type="error",
            source="alerts",
            title="Couldn't load your alerts",
            hint="Check your database connection in Settings → Database, then reopen the Alerts tab.",
            details=f"{alert_type}: {type(e).__name__}: {e}",
            actions=nav_actions_settings("Database"),
            dedupe_key="alerts:list_failed",
            dedupe_cooldown_sec=600.0,
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


_QUANTITY_ALERT_TYPES = {
    "bits",
    "subs",
    "streaks",
    "giftsubs",
    "donations",
    "raids",
}


def _shift_duplicate_amount(alert_type: str) -> None:
    """Move the copied trigger so saving does not replace the original alert."""
    if alert_type not in _QUANTITY_ALERT_TYPES:
        return
    elements = alert_settings_state.get_elements(alert_type)
    existing = set(get_alerts_for_type(alert_type).keys())
    range_toggle = elements.get("range_toggle")
    if range_toggle is not None and getattr(range_toggle, "value", False):
        try:
            min_v = int(elements["min_input"].value or 0)
            max_v = int(elements["max_input"].value or 0)
        except (TypeError, ValueError):
            return
        span = max(0, max_v - min_v)
        for _ in range(1000):
            min_v = max_v + 1
            max_v = min_v + span
            if f"{alert_type}{min_v}-{max_v}" not in existing:
                break
        elements["min_input"].value = min_v
        elements["max_input"].value = max_v
        return
    amount_input = elements.get("amount_input")
    if amount_input is None:
        return
    try:
        candidate = int(amount_input.value or 0) + 1
    except (TypeError, ValueError):
        candidate = 1
    for _ in range(10000):
        if f"{alert_type}{candidate}" not in existing:
            break
        candidate += 1
    amount_input.value = candidate


def duplicate_current_alert(alert_type: str) -> None:
    """Copy the open alert into an unsaved new alert."""
    elements = alert_settings_state.get_elements(alert_type)
    select = elements.get("alert_select")
    selected = getattr(select, "value", None) if select is not None else None
    if (
        not selected
        or selected == "new"
        or selected in POINTS_REWARD_SELECT_PLACEHOLDERS
    ):
        notify(
            "",
            type="warning",
            source="alerts",
            title="Choose an alert to duplicate",
            hint="Pick an existing alert in the list, then click Duplicate.",
        )
        return

    snapshot = {}
    for name, element in elements.items():
        if name in _ALERT_DIRTY_SKIP or not hasattr(element, "value"):
            continue
        if element.__class__.__name__ in {"Button", "Label", "Icon", "Card"}:
            continue
        snapshot[name] = element.value

    with suppress_alert_selection_handler():
        select.value = "new"
        if alert_type == "points":
            set_default_values_for_new_point_reward(alert_type)
        else:
            set_default_values_for_new_alert(alert_type)
        store_original_values(alert_type)
        for name, value in snapshot.items():
            element = elements.get(name)
            if element is None or not hasattr(element, "value"):
                continue
            if (
                alert_type == "points"
                and name == "twitch_title_input"
                and isinstance(value, str)
                and value.strip()
            ):
                value = f"{value} copy"
            try:
                element.value = value
            except Exception:
                logger.debug("Could not copy alert field %s", name, exc_info=True)
        _shift_duplicate_amount(alert_type)
        range_toggle = elements.get("range_toggle")
        exact_row = elements.get("exact_input_row")
        range_row = elements.get("range_input_row")
        if range_toggle is not None and exact_row is not None and range_row is not None:
            toggle_range_inputs(
                SimpleNamespace(value=bool(range_toggle.value)),
                exact_row,
                range_row,
            )
        update_delete_button_visibility(alert_type)
        _refresh_alert_dirty_label(alert_type)

    if alert_type == "follows":
        notify(
            "Saving replaces the existing follow alert, because follows use one slot.",
            type="info",
            source="alerts",
            title="Copied into a new follow alert",
        )
    elif alert_type == "points":
        notify(
            "Review the title and cost, then click Save.",
            type="info",
            source="alerts",
            title="Copied into a new reward",
        )
    else:
        notify(
            "The amount was shifted so saving doesn't replace the original.",
            type="info",
            source="alerts",
            title="Copied into a new alert",
        )


def handle_alert_selection(e, alert_type: str):
    """Handle when an alert is selected from the dropdown

    Args:
        e: The selection event
        alert_type (str): The type of alert (bits, subs, etc.)
    """
    if alert_settings_state.suppress_selection_handler:
        return
    new_value = getattr(e, "value", None)
    if _guard_alert_selection(alert_type, new_value):
        _prompt_unsaved_alert(
            alert_type,
            "You have unsaved changes to this alert. Save them before switching?",
            on_discard=lambda: _discard_then_select(
                alert_type, new_value, handle_alert_selection
            ),
            on_saved=lambda: _select_alert_programmatically(
                alert_type, new_value, handle_alert_selection
            ),
        )
        return
    try:
        fallback_id = alertutils.AlertSettings.FALLBACK_ALERT_ID
        is_fallback = alert_type == "subs" and e.value == fallback_id

        if e.value == "new":
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
                load_alert_settings(alert_type, fallback_id)
            else:
                set_default_values_for_new_alert(alert_type)
                store_original_values(alert_type)
        else:
            # Load selected alert settings
            load_alert_settings(alert_type, e.value)

        # Update delete button visibility based on selection
        update_delete_button_visibility(alert_type)

        # Hide/show amount and range inputs when the fallback alert is selected
        _update_amount_inputs_visibility(alert_type, is_fallback)

    except Exception as e:
        logger.error(f"Error handling alert selection: {str(e)}", exc_info=True)
        _notify_load_failed(alert_type, e)


def _notify_load_failed(alert_type: str, exc: Exception) -> None:
    notify(
        "",
        type="error",
        source="alerts",
        title="Couldn't open this alert",
        hint="Select it again. If it keeps failing, copy the details and check the log.",
        details=f"{type(exc).__name__}: {exc}",
        actions=_alerts_nav(alert_type),
        dedupe_key="alerts:load_failed",
    )


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

    trigger = _view(alert_type).get("trigger")
    if trigger is not None:
        trigger.visible = not hide


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
    alert_settings_state.get_elements(alert_type)[
        "tts_source_select"
    ].value = "alert_message"
    alert_settings_state.get_elements(alert_type)["tts_custom_message_input"].value = ""
    update_tts_custom_message_visibility(alert_type)

    # Set appropriate default values for amount-based alerts
    if alert_type in [
        "bits",
        "subs",
        "streaks",
        "giftsubs",
        "donations",
        "raids",
    ]:
        # Default to exact mode for simplicity
        alert_settings_state.get_elements(alert_type)["range_toggle"].value = False

        # Set reasonable default values based on alert type
        if alert_type == "bits":
            alert_settings_state.get_elements(alert_type)["amount_input"].value = 100
        elif alert_type == "subs":
            alert_settings_state.get_elements(alert_type)["amount_input"].value = 1
        elif alert_type == "streaks":
            alert_settings_state.get_elements(alert_type)["amount_input"].value = 2
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
        elif alert_type == "streaks":
            alert_settings_state.get_elements(alert_type)["min_input"].value = 2
            alert_settings_state.get_elements(alert_type)["max_input"].value = 5
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
    alert_settings_state.get_elements(tab_type)[
        "tts_source_select"
    ].value = "alert_message"
    alert_settings_state.get_elements(tab_type)["tts_custom_message_input"].value = ""
    update_tts_custom_message_visibility(tab_type)


def load_alert_settings(alert_type: str, alert_id: str, quiet: bool = False):
    """Load alert settings from AlertStateManager

    Args:
        alert_type (str): The type of alert (bits, subs, etc.)
        alert_id (str): The ID of the alert to load
        quiet (bool): Automatic reload (tab switch): log problems, don't toast.
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
            if not quiet:
                notify(
                    "It may have been deleted.",
                    type="warning",
                    source="alerts",
                    title="That alert no longer exists",
                    hint="Pick another alert in the list, or create a new one.",
                    details=f"{alert_type}/{alert_id}",
                    actions=_alerts_nav(alert_type),
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
            if not quiet:
                # The form is still being built: try once more shortly, silently.
                layout_schedule(
                    0.4,
                    lambda: load_alert_settings(alert_type, alert_id, quiet=True),
                    once=True,
                )
            return

        # Update UI with alert data - use get with defaults to handle missing fields
        elements["duration_input"].value = float(alert_data.get("duration", 3.0))
        elements["stackable_switch"].value = bool(alert_data.get("stackable", False))

        # Update audio settings
        elements["fade_in_input"].value = int(alert_data.get("fade_in", 0))
        elements["fade_out_input"].value = int(alert_data.get("fade_out", 0))
        update_volume_value(alert_type, int(alert_data.get("volume", 100)))
        elements["tts_enabled_switch"].value = bool(
            alert_data.get("tts_enabled", False)
        )
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
                for attempt in range(1):
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
        layout_schedule(0.3, force_ui_refresh_multiple, once=True)

        # Update range/exact inputs if applicable
        is_fallback = alert_id == alertutils.AlertSettings.FALLBACK_ALERT_ID
        if alert_type in [
            "bits",
            "subs",
            "streaks",
            "giftsubs",
            "donations",
            "raids",
        ]:
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
        if not quiet:
            _notify_load_failed(alert_type, e)


# Browse targets: (title, subtitle, kind, extensions, mode, dir field, file field)
_BROWSE_TARGETS = {
    "gif": ("Choose a GIF or image", "GIF, PNG, JPG or WebP from your assets folder", "image",
            (".gif", ".png", ".jpg", ".jpeg", ".webp"), "file", "gif_dir_input", "gif_file_input"),
    "primary": ("Choose a sound", "MP3, WAV, OGG, M4A or FLAC from your assets folder", "audio",
                (".mp3", ".wav", ".ogg", ".m4a", ".flac"), "file", "primary_dir_input", "primary_file_input"),
    "random": ("Choose a folder of sounds", "A random sound from this folder plays instead of the primary sound", "audio",
               (".mp3", ".wav", ".ogg", ".m4a", ".flac"), "folder", "random_dir_input", None),
    "extra": ("Choose a folder of extra sounds", "A random sound from this folder can play after the main sound", "audio",
              (".mp3", ".wav", ".ogg", ".m4a", ".flac"), "folder", "extra_dir_input", None),
}


def handle_browse(folder_type: str, alert_type: str = None):
    """Open the shared file picker for a GIF, sound or sound-folder field.

    Args:
        folder_type (str): Type of folder being browsed (primary, random, extra, gif)
        alert_type (str): Alert type tab to write into (falls back to current_tab)
    """
    try:
        from .file_picker import assets_root, open_file_picker

        current_alert_type = alert_type or alert_settings_state.current_tab
        if not current_alert_type:
            logger.error("Browse clicked with no alert type selected")
            return
        target = _BROWSE_TARGETS.get(folder_type)
        if target is None:
            logger.error("Unknown browse folder type: %s", folder_type)
            return
        title, subtitle, kind, extensions, mode, dir_field, file_field = target
        elements = alert_settings_state.get_elements(current_alert_type)
        missing = [f for f in (dir_field, file_field) if f and elements.get(f) is None]
        if missing:
            logger.error("Browse fields not found for %s: %s", current_alert_type, missing)
            _notify_tab_not_ready(current_alert_type)
            return

        directory = str(elements[dir_field].value or "")
        filename = str(elements[file_field].value or "") if file_field else ""
        start = directory.rstrip("/") + "/" + filename if filename else directory

        root = assets_root()
        tab_name = next((n for n, t in ALERT_TAB_TO_TYPE.items() if t == current_alert_type), current_alert_type)
        places = [
            ("campaign", "Alerts", root / "alerts"),
            (_ALERT_TAB_ICONS.get(tab_name, "folder_special"), tab_name, root / current_alert_type),
        ]

        def apply(result, at=current_alert_type) -> None:
            els = alert_settings_state.get_elements(at)
            if result.is_dir:
                els[dir_field].value = result.web_path
                track_field_change(dir_field, els[dir_field], result.web_path, at)
                logger.info(f"Selected directory: {result.web_path}")
                return
            els[dir_field].value = result.web_dir
            els[file_field].value = result.filename
            track_field_change(dir_field, els[dir_field], result.web_dir, at)
            track_field_change(file_field, els[file_field], result.filename, at)
            logger.info(f"Selected file: {result.web_dir}{result.filename}")

        # The overlay looks random-sound folders up under assets/alerts/ only.
        within = root / "alerts" if mode == "folder" else None
        open_file_picker(
            title=title,
            subtitle=subtitle,
            extensions=extensions,
            on_pick=apply,
            mode=mode,
            kind=kind,
            start=start or None,
            places=places,
            within=within,
            within_hint="Random sound folders must be inside assets/alerts so the overlay can find them.",
        )

    except Exception as e:
        logger.error(
            f"Error handling browse for {folder_type}: {str(e)}", exc_info=True
        )
        notify(
            "",
            type="error",
            source="files",
            title="Couldn't open the file picker",
            hint="Try again. If it keeps failing, switch to another alert tab and back.",
            details=f"{folder_type}: {type(e).__name__}: {e}",
        )


def _collect_test_alert_data(alert_type: str, elements: dict) -> dict:
    """Build alert payload from the current Alerts tab form values."""
    runtime_type = alertutils.ui_tab_to_runtime_alert_type(alert_type)
    alert_data = {
        "alert_type": runtime_type,
        "duration": float(_element_value(elements, "duration_input", 3.0)),
        "stackable": True,
        "fade_in": int(_element_value(elements, "fade_in_input", 0)),
        "fade_out": int(_element_value(elements, "fade_out_input", 0)),
        "volume": int(_element_value(elements, "volume_input", 100)),
        "audio_only": bool(_element_value(elements, "audio_only_switch", False)),
        "single_audio_dir": str(_element_value(elements, "primary_dir_input", "") or ""),
        "single_audio_name": str(
            _element_value(elements, "primary_file_input", "") or ""
        ),
        "randomized": bool(_element_value(elements, "randomized_switch", False)),
        "randomized_dir": str(_element_value(elements, "random_dir_input", "") or ""),
        "randomized_chance": int(_element_value(elements, "random_chance_input", 0)),
        "randomized_extra": bool(
            _element_value(elements, "randomized_switch", False)
            and _element_value(elements, "randomized_extra_switch", False)
        ),
        "randomized_extra_dir": str(
            _element_value(elements, "extra_dir_input", "") or ""
        ),
        "randomized_extra_chance": int(
            _element_value(elements, "extra_chance_input", 0)
        ),
        "gif_dir": str(_element_value(elements, "gif_dir_input", "") or ""),
        "gif_name": str(_element_value(elements, "gif_file_input", "") or ""),
        "tts_enabled": bool(_element_value(elements, "tts_enabled_switch", False)),
        "tts_source": str(
            _element_value(elements, "tts_source_select", "alert_message") or ""
        ),
        "tts_custom_message": str(
            _element_value(elements, "tts_custom_message_input", "") or ""
        ),
        "alert_id": f"TestAlert{round(time.time())}",
        "timestamp": time.time(),
        "alert_name": "Test Alert",
        "username": "TestUser",
    }

    if alert_type == "points" and "enable_alert_switch" in elements:
        alert_data["enable_alert"] = bool(
            _element_value(elements, "enable_alert_switch", False)
        )

    use_range = bool(_element_value(elements, "range_toggle", False))
    amount = _element_value(elements, "amount_input", None)
    min_amount = _element_value(elements, "min_input", None)

    if alert_type == "bits":
        if use_range and min_amount is not None:
            alert_data["amt_cheered"] = int(min_amount)
        elif amount is not None:
            alert_data["amt_cheered"] = int(amount)
        else:
            alert_data["amt_cheered"] = 100
    elif alert_type == "subs":
        if use_range and min_amount is not None:
            alert_data["resub_month"] = int(min_amount)
        elif amount is not None:
            alert_data["resub_month"] = int(amount)
        else:
            alert_data["resub_month"] = 1
        alert_data["tier"] = 1
    elif alert_type == "streaks":
        if use_range and min_amount is not None:
            alert_data["streak_count"] = int(min_amount)
        elif amount is not None:
            alert_data["streak_count"] = int(amount)
        else:
            alert_data["streak_count"] = 2
        alert_data["channel_points_awarded"] = 0
        alert_data["message"] = "Test watch streak message"
    elif alert_type == "giftsubs":
        if use_range and min_amount is not None:
            alert_data["gift_qty"] = int(min_amount)
        elif amount is not None:
            alert_data["gift_qty"] = int(amount)
        else:
            alert_data["gift_qty"] = 1
        alert_data["tier"] = 1
        if int(alert_data.get("gift_qty") or 0) == 1:
            alert_data["recipient"] = "TestRecipient"
    elif alert_type == "donations":
        if use_range and min_amount is not None:
            alert_data["donation_amount"] = float(min_amount)
        elif amount is not None:
            alert_data["donation_amount"] = float(amount)
        else:
            alert_data["donation_amount"] = 5.0
    elif alert_type == "raids":
        if use_range and min_amount is not None:
            alert_data["raider_count"] = int(min_amount)
        elif amount is not None:
            alert_data["raider_count"] = int(amount)
        else:
            alert_data["raider_count"] = 10
    elif alert_type == "points":
        reward_id = _element_value(elements, "alert_select", "test_reward")
        if reward_id in POINTS_REWARD_SELECT_PLACEHOLDERS or reward_id == "new":
            reward_id = "test_reward"
        alert_data["twitch_reward_id"] = str(reward_id)
        alert_data["point_cost"] = 1000
        alert_data["title"] = "Test Point Redemption"

    return alert_data


def test_alert(alert_type: str):
    """Test the current alert settings from the form and enqueue for playback.

    Args:
        alert_type (str): The type of alert being tested (UI tab id)
    """
    try:
        logger.info("test_alert invoked for tab=%s", alert_type)
        elements = alert_settings_state.get_elements(alert_type)
        if not elements:
            logger.warning("test_alert: no form elements yet for %s", alert_type)
            _notify_tab_not_ready(alert_type)
            return

        missing_elements = [
            elem
            for elem in _TEST_ALERT_REQUIRED_ELEMENTS
            if elem not in elements or elements[elem] is None
        ]
        if missing_elements:
            logger.warning(
                "test_alert missing elements for %s: %s", alert_type, missing_elements
            )
            _notify_tab_not_ready(alert_type)
            return

        alert_data = _collect_test_alert_data(alert_type, elements)
        runtime_type = alert_data["alert_type"]

        filtered = alertutils.alert_state_manager._filter_alert_obj_fields(alert_data)
        alert = alertutils.AlertObj(**filtered)
        alert.is_test = True
        alert_processor.enqueue_alert(alert)

        if runtime_type == "follow":
            try:
                from .. import web_engine

                if (
                    hasattr(web_engine, "web_engine_instance")
                    and web_engine.web_engine_instance
                ):
                    web_engine.web_engine_instance.instant_alert(
                        {
                            "type": "follow",
                            "username": alert.username,
                            "alert_id": alert.alert_id,
                            "timestamp": alert.timestamp,
                        }
                    )
            except Exception as instant_err:
                logger.error(
                    "Error sending instant alert for follow test: %s",
                    instant_err,
                    exc_info=True,
                )
                notify(
                    "The follow test couldn't reach the overlay.",
                    type="error",
                    source="alerts",
                    title="Couldn't send the follow test",
                    hint="Check that the overlay server is running (see the footer), then try again.",
                    details=f"{type(instant_err).__name__}: {instant_err}",
                )
                return

        logger.info(
            "Queued test alert tab=%s runtime=%s id=%s",
            alert_type,
            runtime_type,
            alert.alert_id,
        )
        tab_label = _ALERT_TYPE_TO_TAB.get(alert_type, "alert")
        if _alerts_paused():
            notify(
                type="warning",
                source="alerts",
                title="Alerts are paused",
                message=f"The {tab_label} test alert will play when you resume alerts.",
                hint="Resume alerts with the pause button in the Activity Feed.",
                actions=nav_actions_main_tab("Activity Feed"),
            )
        elif _overlay_running():
            # (If the overlay server is down, the alert queue reports the skipped alert.)
            notify("", type="info", source="alerts", title=f"Test alert sent ({tab_label})")
    except Exception as e:
        logger.error(f"Error testing alert: {str(e)}", exc_info=True)
        notify(
            "",
            type="error",
            source="alerts",
            title="Couldn't send the test alert",
            hint="Check the alert's settings and that the overlay server is running (see the footer), then try again.",
            details=f"{type(e).__name__}: {e}",
            actions=_alerts_nav(alert_type),
        )


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
            alert_settings_state.get_elements(alert_type)[
                "tts_custom_message_input"
            ].value
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
        randomized_extra = bool(randomized and alert_settings_state.get_elements(alert_type)[
            "randomized_extra_switch"
        ].value)
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

        elif alert_type in [
            "bits",
            "subs",
            "streaks",
            "giftsubs",
            "donations",
            "raids",
        ]:
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
                elif alert_type == "streaks":
                    alert_data["streak_count"] = min_val
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
                elif alert_type == "streaks":
                    alert_data["streak_count"] = quantity
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
            alert_type, alert_id, alert_data, quiet_db_notices=True
        )

        if success:
            notify(
                "",
                type="success",
                source="alerts",
                title=f"Saved “{_alert_name(alert_type, alert_id, alert_data)}”",
            )

            # Ensure we update the dropdown to show all alerts including the new one
            try:
                with suppress_alert_selection_handler():
                    # If we just created a new alert, update the dropdown and select it
                    if (
                        alert_settings_state.get_elements(alert_type)["alert_select"].value
                        == "new"
                        or selected_value == alertutils.AlertSettings.FALLBACK_ALERT_ID
                    ):
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
                            _render_alert_list(alert_type)
            except Exception as dropdown_err:
                logger.error(
                    f"Error updating alert dropdown: {str(dropdown_err)}", exc_info=True
                )
                # The alert is saved; only the list is stale.
                notify(
                    "The alert is saved, but the list didn't refresh.",
                    type="warning",
                    source="alerts",
                    title="Alert list out of date",
                    hint="Switch to another alert tab and back to see it.",
                    details=f"{type(dropdown_err).__name__}: {dropdown_err}",
                    actions=_alerts_nav(alert_type),
                )

            # Store the new values as original values
            store_original_values(alert_type)
            # Clear changed styling
            clear_changed_styling(alert_type)
        else:
            _notify_write_failed(
                "Couldn't save the alert",
                "Your changes are still in the form.",
                alert_type,
            )

    except (TypeError, ValueError) as e:
        # int("") / int(None): an empty or non-numeric amount field.
        logger.warning(f"Alert not saved, invalid amount: {e}")
        notify(
            "Nothing was saved.",
            type="warning",
            source="alerts",
            title="Check the amount",
            hint="Enter a whole number for the amount (or for both ends of the range), then click Save.",
            details=f"{type(e).__name__}: {e}",
        )
    except Exception as e:
        logger.error(f"Error saving alert: {str(e)}", exc_info=True)
        notify(
            "Your changes are still in the form.",
            type="error",
            source="alerts",
            title="Couldn't save the alert",
            hint="Try again. If it keeps failing, copy the details and check the log.",
            details=f"{type(e).__name__}: {e}",
            actions=_alerts_nav(alert_type),
        )


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
        current_tab = tab_type or alert_settings_state.current_tab
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
        incoming = new_value
        if hasattr(new_value, "value") and not isinstance(
            new_value, (str, bytes, int, float, bool, list, dict)
        ):
            incoming = new_value.value

        if getattr(alert_settings_state, "ignore_field_dirty", False):
            return

        # Check if the value has changed from the original
        if _alert_values_differ(original_value, incoming) or (
            hasattr(element, "value")
            and _alert_values_differ(original_value, element.value)
        ):
            alert_settings_state.dirty_tabs.add(current_tab)
            logger.debug(
                f"Change detected in {field_name}: {original_value} → {incoming}"
            )
        else:
            alert_settings_state.dirty_tabs.discard(current_tab)
            if _alert_form_values_differ(current_tab):
                alert_settings_state.dirty_tabs.add(current_tab)
            logger.debug(f"No change in {field_name}")
        _refresh_alert_dirty_label(current_tab)
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
    _refresh_alert_dirty_label(tab_type)


def clear_changed_styling(tab_type: str):
    """Clear all changed styling"""
    logger.debug("Clearing all changed styling")

    # Loop through all tracked elements and remove styling
    for field_name, element in alert_settings_state.get_elements(tab_type).items():
        if not element:
            continue
    _refresh_alert_dirty_label(tab_type)


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
        alert_settings_state.dirty_tabs.discard(tab_type)
        _remember_loaded_alert(tab_type)
        _refresh_alert_dirty_label(tab_type)
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
    """Build the list + detail form for Channel Points (Twitch rewards)."""
    alert_type = "points"

    # Set the current tab in the state manager
    alert_settings_state.set_current_tab(alert_type)

    with ui.element("div").classes("alr"):
        select = form_select(
            tooltip="Choose a Twitch point reward to configure as an alert",
            options={"loading": "Loading Twitch rewards..."},
            classes="hidden",
            on_change=lambda e: handle_point_reward_selection(e, alert_type),
        )
        alert_settings_state.get_elements(alert_type)["alert_select"] = select
        _build_alert_shell(alert_type, select, lambda _e=None: save_point_alert())

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
    layout_schedule(1.0, auto_load_rewards, once=True)


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
    if alert_settings_state.suppress_selection_handler:
        return
    new_value = getattr(e, "value", None)
    if _guard_alert_selection(alert_type, new_value):
        _prompt_unsaved_alert(
            alert_type,
            "You have unsaved changes to this reward. Save them before switching?",
            on_discard=lambda: _discard_then_select(
                alert_type, new_value, handle_point_reward_selection
            ),
            on_saved=lambda: _select_alert_programmatically(
                alert_type, new_value, handle_point_reward_selection
            ),
        )
        return
    try:
        # Validate the selection value - handle None and empty values gracefully
        if e.value is None:
            logger.debug("Selection value is None, ignoring")
            return
        if not isinstance(e.value, str) or not e.value.strip():
            logger.warning(f"Invalid selection value: {e.value}")
            return

        if e.value == "new":
            set_default_values_for_new_point_reward(alert_type)
            store_original_values(alert_type)
        elif e.value in POINTS_REWARD_SELECT_PLACEHOLDERS:
            pass
        else:
            # Validate that it's a valid reward ID (should be a UUID-like string)
            if len(e.value) > 5:  # Basic validation - real reward IDs are much longer
                # Load selected point reward settings
                load_point_reward_settings(alert_type, e.value)
            else:
                logger.warning(f"Invalid reward ID format: {e.value}")

        # Update delete button visibility based on selection
        update_delete_button_visibility(alert_type)

    except Exception as e:
        logger.error(f"Error handling point reward selection: {str(e)}", exc_info=True)
        _notify_reward_load_failed(e)


def _notify_reward_load_failed(exc: Exception) -> None:
    notify(
        "",
        type="error",
        source="alerts",
        title="Couldn't load this reward from Twitch",
        hint="Check your Twitch connection in Settings → Twitch, then pick the reward again.",
        details=f"{type(exc).__name__}: {exc}",
        actions=nav_actions_settings("Twitch") + nav_alerts("Channel Points"),
        dedupe_key="alerts:points:reward_load_failed",
    )


def _schedule_point_reward_select_refresh(select_element):
    """Best-effort UI refresh for the point reward combobox after options change."""

    if not select_element:
        return
    _render_alert_list("points")

    def force_refresh():
        try:
            if select_element and hasattr(select_element, "update"):
                select_element.update()
                logger.debug("Forced refresh of point rewards select dropdown")
        except Exception as refresh_err:
            logger.error(f"Error in force refresh: {str(refresh_err)}")

    app_schedule(0.1, force_refresh, once=True)
    app_schedule(0.3, force_refresh, once=True)
    app_schedule(0.5, force_refresh, once=True)


# One reward fetch at a time: panel build and tab switches ask for it several times.
_points_load_state = {"inflight": False, "last_auto_error": None}
_POINTS_AUTO_ERROR_COOLDOWN_SEC = 600.0


def _notify_rewards_unavailable(status: str, user_initiated: bool) -> None:
    """Explain why the reward list is empty. Automatic loads stay quiet (the list
    already says so), except real errors, which show at most every 10 minutes."""
    twitch_nav = nav_actions_settings("Twitch")
    if status == "not_unlocked":
        if user_initiated:
            notify(
                "This Twitch account doesn't have Channel Points (custom rewards) unlocked.",
                type="warning",
                source="alerts",
                title="Channel Points aren't available",
                hint="Channel Points need Twitch Affiliate or Partner status.",
                actions=twitch_nav,
                dedupe_key="alerts:points:locked",
                dedupe_cooldown_sec=3600.0,
            )
        return
    if status == "not_connected":
        if user_initiated:
            notify(
                "Connect Twitch to manage Channel Points rewards.",
                type="warning",
                source="alerts",
                title="Twitch isn't connected",
                hint="Open Settings → Twitch and connect your account.",
                actions=twitch_nav,
                dedupe_key="alerts:points:not_connected",
                dedupe_cooldown_sec=600.0,
            )
        return
    if not user_initiated:
        last = _points_load_state["last_auto_error"]
        now = time.monotonic()
        if last is not None and now - last < _POINTS_AUTO_ERROR_COOLDOWN_SEC:
            return
        _points_load_state["last_auto_error"] = now
    notify(
        "",
        type="error",
        source="alerts",
        title="Couldn't load your Channel Points rewards",
        hint="Check your Twitch connection in Settings → Twitch, then click Refresh in the reward list.",
        details=f"status={status}",
        actions=twitch_nav,
        dedupe_key="alerts:points:load_failed",
        dedupe_cooldown_sec=600.0,
    )


def load_twitch_point_rewards(user_initiated: bool = False, force: bool = False):
    """Load Twitch point rewards from the API and update the dropdown.

    Helix fetch is done on a worker; dropdown updates hop back to the NiceGUI loop.
    ``user_initiated`` (the Refresh button) also explains an empty list in a toast.
    ``force`` fetches even while another load is running (after a save or delete).
    """
    alert_type = "points"
    if _points_load_state["inflight"] and not (user_initiated or force):
        logger.debug("Point rewards already loading; skipping duplicate request")
        return
    with suppress_alert_selection_handler():
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
            _render_alert_list(alert_type)

            def _apply_result(result) -> None:
                with suppress_alert_selection_handler():
                    status = result.get("status")
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
                        _notify_rewards_unavailable(status, user_initiated)
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
                        _notify_rewards_unavailable(status, user_initiated)
                        _schedule_point_reward_select_refresh(select_element)
                        return

                    if status == "error":
                        set_save(False)
                        select_element.options = {
                            "error": "Error loading rewards - Check connection or authentication"
                        }
                        if hasattr(select_element, "update"):
                            select_element.update()
                        _notify_rewards_unavailable("error", user_initiated)
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
                    else:
                        reward_options["no_rewards"] = (
                            "No rewards yet. Click New reward, fill in the Twitch reward section, then click Save."
                        )

                    select_element.options = reward_options
                    if hasattr(select_element, "update"):
                        select_element.update()
                    _schedule_point_reward_select_refresh(select_element)

            def _worker() -> None:
                try:
                    result = twitch.fetch_channel_point_rewards()
                except Exception as fetch_err:
                    logger.error(
                        "Error fetching Twitch point rewards: %s",
                        fetch_err,
                        exc_info=True,
                    )
                    result = {"status": "error", "rewards": None}
                finally:
                    _points_load_state["inflight"] = False
                run_on_ui_loop(lambda: _apply_result(result))

            _points_load_state["inflight"] = True
            try:
                threading.Thread(
                    target=_worker, daemon=True, name="LoadPointRewards"
                ).start()
            except Exception:
                _points_load_state["inflight"] = False
                raise

        except Exception as e:
            logger.error(
                f"Error loading Twitch point rewards: {str(e)}", exc_info=True
            )
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
            _notify_rewards_unavailable("error", user_initiated)


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
    alert_settings_state.get_elements(alert_type)[
        "tts_source_select"
    ].value = "alert_message"
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
    alert_settings_state.get_elements(alert_type)[
        "tts_source_select"
    ].value = "alert_message"
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
            notify(
                "It may have been deleted on Twitch, or Twitch couldn't be reached.",
                type="warning",
                source="alerts",
                title="Couldn't find this reward on Twitch",
                hint="Click Refresh in the reward list. If it's gone, create a new reward.",
                details=f"reward_id={reward_id}",
                actions=nav_alerts("Channel Points"),
                dedupe_key=f"alerts:points:missing:{reward_id}",
            )
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
                    alert_data.get("tts_source", "alert_message") or "alert_message"
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
        layout_schedule(0.1, force_ui_refresh, once=True)
        layout_schedule(0.3, force_ui_refresh, once=True)
        layout_schedule(0.5, force_ui_refresh, once=True)

        # Store original values
        store_original_values(alert_type)
        # Clear any changed styling
        clear_changed_styling(alert_type)

        logger.debug(f"Successfully loaded point reward {reward_id}")

        # Update delete button visibility
        update_delete_button_visibility(alert_type)

    except Exception as e:
        logger.error(f"Error loading point reward settings: {str(e)}", exc_info=True)
        _notify_reward_load_failed(e)


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


def save_point_alert(on_complete=None):
    """Save the current point alert settings (creates or updates Twitch reward when needed)."""
    completed = {"done": False}
    work = None  # progress notice while Twitch is contacted

    def finish(ok: bool) -> None:
        if completed["done"]:
            return
        completed["done"] = True
        if on_complete is None:
            return
        try:
            on_complete(bool(ok))
        except Exception:
            logger.error("Point alert save callback failed", exc_info=True)

    try:
        from .. import twitch

        alert_type = "points"
        elements = alert_settings_state.get_elements(alert_type)
        selected = elements["alert_select"].value

        if not twitch.twitch_api or not twitch.twitch_api.is_connected:
            notify(
                "Channel Points rewards are saved on Twitch, so Twitch must be connected. Nothing was saved.",
                type="warning",
                source="alerts",
                title="Connect Twitch to save this reward",
                hint="Open Settings → Twitch and connect your account, then click Save again.",
                actions=nav_actions_settings("Twitch"),
            )
            finish(False)
            return

        if selected in POINTS_REWARD_SELECT_PLACEHOLDERS and selected != "new":
            notify(
                "",
                type="warning",
                source="alerts",
                title="Pick a reward first",
                hint="Pick a reward in the list, or click New reward to create one.",
            )
            finish(False)
            return

        create_new = selected == "new"
        update_twitch = False
        reward_payload = None
        if create_new:
            title = (elements["twitch_title_input"].value or "").strip()
            if not title:
                notify(
                    "",
                    type="warning",
                    source="alerts",
                    title="Enter a reward title",
                    hint="Fill in the title in the Twitch reward section, then click Save.",
                )
                finish(False)
                return
            cost_val = elements["twitch_cost_input"].value
            try:
                cost_ok = cost_val is not None and int(cost_val) >= 1
            except (TypeError, ValueError):
                cost_ok = False
            if not cost_ok:
                notify(
                    "",
                    type="warning",
                    source="alerts",
                    title="Point cost must be at least 1",
                    hint="Enter a whole number of points in the Twitch reward section, then click Save.",
                )
                finish(False)
                return
            reward_payload = build_twitch_reward_payload_from_ui(alert_type)
        elif twitch_reward_fields_changed(alert_type):
            update_twitch = True
            reward_payload = build_twitch_reward_payload_from_ui(alert_type)

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
        randomized_extra = bool(randomized and elements["randomized_extra_switch"].value)
        extra_dir = elements["extra_dir_input"].value or ""
        extra_chance = elements["extra_chance_input"].value
        gif_dir = elements["gif_dir_input"].value or ""
        gif_file = elements["gif_file_input"].value or ""

        enable_alert = elements["enable_alert_switch"].value
        reward_title = (elements["twitch_title_input"].value or "").strip()
        point_cost = elements["twitch_cost_input"].value
        captured_reward_id = selected

        def _persist_local(selected_reward_id) -> None:
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
                "point_cost": point_cost,
                "title": reward_title,
                "alert_name": alertutils.alert_state_manager.get_display_name(
                    alert_type,
                    selected_reward_id,
                    {"title": reward_title},
                ),
                "timestamp": time.time(),
            }

            success = alertutils.alert_state_manager.save_alert(
                alert_type, selected_reward_id, alert_data, quiet_db_notices=True
            )

            if success:
                work.done(f"Saved “{alert_data['title'] or 'reward'}”")
                if on_complete is not None:
                    store_original_values(alert_type)
                    clear_changed_styling(alert_type)
                    finish(True)
                    return
                load_twitch_point_rewards(force=True)
                rid = selected_reward_id

                def after_list_refresh():
                    try:
                        with suppress_alert_selection_handler():
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
                        notify(
                            "The reward is saved, but the list didn't refresh.",
                            type="warning",
                            source="alerts",
                            title="Reward list out of date",
                            hint="Click Refresh in the reward list to see it.",
                            details=f"{type(refresh_err).__name__}: {refresh_err}",
                            actions=nav_alerts("Channel Points"),
                        )

                layout_schedule(0.85, after_list_refresh, once=True)
            else:
                reason = getattr(alertutils.alert_state_manager, "last_write_error", "") or ""
                message = (
                    "The reward was saved on Twitch, but the alert settings weren't saved in Mycelian."
                    if (create_new or update_twitch)
                    else "Your changes are still in the form."
                )
                if reason == "unreadable":
                    message += " Mycelian couldn't read your saved alerts from the database, so it didn't overwrite them."
                work.fail(
                    "Couldn't save the alert",
                    message,
                    hint="Check your database connection in Settings → Database, then click Save again.",
                    details=reason or None,
                    actions=nav_actions_settings("Database"),
                )
                finish(False)

        def _persist_local_safe(selected_reward_id) -> None:
            try:
                _persist_local(selected_reward_id)
            except Exception as persist_err:
                logger.error("Error saving point alert locally: %s", persist_err, exc_info=True)
                work.fail(
                    "Couldn't save the alert",
                    "Your changes are still in the form.",
                    hint="Try again. If it keeps failing, copy the details and check the log.",
                    details=f"{type(persist_err).__name__}: {persist_err}",
                )
                finish(False)

        twitch_hint = (
            "Reward titles must be unique on your channel. If the title is fine, reconnect "
            "Twitch in Settings → Twitch so Mycelian can manage rewards."
        )

        def _fail_on_ui(title: str, message: str, *, level: str = "error", **kwargs) -> None:
            def _do() -> None:
                if level == "error":
                    work.fail(title, message, **kwargs)
                else:
                    work.done(title, message, type=level, **kwargs)
                finish(False)

            run_on_ui_loop(_do)

        def _worker() -> None:
            try:
                fetch = twitch.fetch_channel_point_rewards()
                if fetch["status"] == "not_unlocked":
                    _fail_on_ui(
                        "Channel Points aren't available",
                        "This Twitch account doesn't have Channel Points (custom rewards) unlocked. Nothing was saved.",
                        level="warning",
                        hint="Channel Points need Twitch Affiliate or Partner status.",
                        actions=nav_actions_settings("Twitch"),
                    )
                    return
                if fetch["status"] not in ("ok",):
                    _fail_on_ui(
                        "Couldn't reach Twitch Channel Points",
                        "Nothing was saved.",
                        level="warning",
                        hint="Check your Twitch connection in Settings → Twitch, click Refresh in the reward list, then save again.",
                        details=f"status={fetch.get('status')}",
                        actions=nav_actions_settings("Twitch"),
                    )
                    return

                selected_reward_id = captured_reward_id
                if create_new:
                    work.update("Creating the reward on Twitch…")
                    new_reward = twitch.create_point_reward(reward_payload)
                    if not new_reward or not new_reward.get("id"):
                        _fail_on_ui(
                            "Twitch didn't create the reward",
                            "Nothing was saved.",
                            hint=twitch_hint,
                            details=f"title={reward_title!r} cost={point_cost!r}",
                            actions=nav_actions_settings("Twitch"),
                        )
                        return
                    selected_reward_id = new_reward["id"]
                elif update_twitch:
                    work.update("Updating the reward on Twitch…")
                    if not twitch.update_point_reward(selected_reward_id, reward_payload):
                        _fail_on_ui(
                            "Twitch didn't accept the reward changes",
                            "The alert settings weren't saved either.",
                            hint=twitch_hint,
                            details=f"reward_id={selected_reward_id}",
                            actions=nav_actions_settings("Twitch"),
                        )
                        return
                run_on_ui_loop(lambda rid=selected_reward_id: _persist_local_safe(rid))
            except Exception as worker_err:
                logger.error("Error saving point alert: %s", worker_err, exc_info=True)
                _fail_on_ui(
                    "Couldn't save the reward",
                    "Your changes are still in the form.",
                    hint="Try again. If it keeps failing, check your Twitch connection in Settings → Twitch.",
                    details=f"{type(worker_err).__name__}: {worker_err}",
                    actions=nav_actions_settings("Twitch"),
                )

        work = notify_progress(
            f"Saving “{reward_title or 'reward'}”",
            source="alerts",
            message="Checking Channel Points on Twitch…",
        )
        threading.Thread(target=_worker, daemon=True, name="SavePointAlert").start()

    except Exception as e:
        logger.error(f"Error saving point alert: {str(e)}", exc_info=True)
        kwargs = dict(
            hint="Check the reward's numbers (cost, limits, cooldown) and try again.",
            details=f"{type(e).__name__}: {e}",
        )
        if work is not None:
            work.fail("Couldn't save the reward", "Your changes are still in the form.", **kwargs)
        else:
            notify(
                "Your changes are still in the form.",
                type="error",
                source="alerts",
                title="Couldn't save the reward",
                **kwargs,
            )
        finish(False)


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
            notify(
                "",
                type="warning",
                source="alerts",
                title="Select an alert to delete",
                hint="Pick an alert in the list first.",
            )
            return

        # Get alert data to show in confirmation
        alert_data = alertutils.alert_state_manager.get_alert_by_id(
            alert_type, selected_alert_id
        )
        if not alert_data:
            notify(
                "It may already have been deleted.",
                type="info",
                source="alerts",
                title="That alert no longer exists",
            )
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
        notify(
            "Nothing was deleted.",
            type="error",
            source="alerts",
            title="Couldn't open the delete dialog",
            hint="Try again.",
            details=f"{type(e).__name__}: {e}",
        )


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

        alert_name = _alert_name(
            alert_type,
            alert_id,
            alertutils.alert_state_manager.get_alert_by_id(alert_type, alert_id),
        )

        # Delete the alert using AlertStateManager
        success = alertutils.alert_state_manager.delete_alert(
            alert_type, alert_id, quiet_db_notices=True
        )

        if success:
            notify(
                "The reward still exists on Twitch. Remove it there if you no longer need it."
                if alert_type == "points"
                else "",
                type="success",
                source="alerts",
                title=f"Deleted “{alert_name}”",
            )

            # Update the dropdown to remove the deleted alert
            try:
                with suppress_alert_selection_handler():
                    if alert_type == "points":
                        load_twitch_point_rewards(force=True)
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
                        _render_alert_list(alert_type)

            except Exception as dropdown_err:
                logger.error(
                    f"Error updating alert dropdown after deletion: {str(dropdown_err)}",
                    exc_info=True,
                )
                # This shouldn't prevent the deletion from being successful
        else:
            _notify_write_failed(
                "Couldn't delete the alert",
                f"“{alert_name}” is still saved.",
                alert_type,
            )

    except Exception as e:
        logger.error(f"Error confirming alert deletion: {str(e)}", exc_info=True)
        notify(
            "",
            type="error",
            source="alerts",
            title="Couldn't delete the alert",
            hint="Try again. If it keeps failing, check your database connection in Settings → Database.",
            details=f"{type(e).__name__}: {e}",
            actions=nav_actions_settings("Database"),
        )


def refresh_alert_dropdowns():
    """Refresh all alert dropdowns to show updated alert lists after data changes"""
    with suppress_alert_selection_handler():
        try:
            # Get all alert types that have dropdowns
            alert_types = [
                "bits",
                "subs",
                "streaks",
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
                    _render_alert_list(alert_type)

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

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Shared fakes for the modules/uiwindows/alertsettings.py tests (not a test file itself).

``install(monkeypatch)`` gives the Alerts tab a real AlertStateManager on an in-memory
database (see _alertutils_fakes.py), fresh module state, and recorders for toasts,
progress notices, timers, the alert queue, Twitch and the file picker. Nothing touches
the real database, network, overlay server or files.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "tests", Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import _alertutils_fakes as au_fakes  # noqa: E402
from modules import alert_processor, alertutils, twitch, web_engine  # noqa: E402
from modules.uiwindows import alertsettings as al  # noqa: E402
from modules.uiwindows import file_picker, media_preview  # noqa: E402


class FakeWork:
    """notify_progress() stand-in."""

    def __init__(self, title, **kw):
        self.title = title
        self.kw = kw
        self.updates = []
        self.done_calls = []
        self.fail_calls = []

    def update(self, message):
        self.updates.append(message)

    def done(self, title, message="", **kw):
        self.done_calls.append((title, message, kw))

    def fail(self, title, message="", **kw):
        self.fail_calls.append((title, message, kw))


class InlineThread:
    def __init__(self, target=None, daemon=None, name=None):
        self.target = target
        self.name = name

    def start(self):
        Env.current.threads.append(self.name)
        if Env.current.hold_threads:
            Env.current.held.append(self.target)
            return
        self.target()


class FakeTwitch:
    def __init__(self):
        self.fetch_result = {"status": "ok", "rewards": []}
        self.fetch_error = None
        self.rewards = {}
        self.created = []
        self.updated = []
        self.create_result = {"id": "new-reward-id"}
        self.update_result = True
        self.connected = True

    def fetch_channel_point_rewards(self):
        if self.fetch_error:
            raise self.fetch_error
        return self.fetch_result

    def get_point_reward_by_id(self, reward_id):
        r = self.rewards.get(reward_id)
        if isinstance(r, Exception):
            raise r
        return r

    def create_point_reward(self, payload):
        self.created.append(payload)
        if isinstance(self.create_result, Exception):
            raise self.create_result
        return self.create_result

    def update_point_reward(self, reward_id, payload):
        self.updated.append((reward_id, payload))
        return self.update_result


class Env:
    current = None

    def __init__(self):
        self.notices = []
        self.works = []
        self.scheduled = []
        self.queued = []
        self.instant = []
        self.threads = []
        self.hold_threads = False
        self.held = []
        self.picker_calls = []
        self.db = None
        self.mgr = None
        self.twitch = FakeTwitch()

    # ---------------------------------------------------------------- helpers
    def titles(self):
        return [k.get("title") for _a, k in self.notices]

    def last(self):
        return self.notices[-1][1] if self.notices else None

    def run_scheduled(self, *, rounds=3):
        """Fire the recorded layout/app timers (and any they schedule), oldest first."""
        for _ in range(rounds):
            pending, self.scheduled = self.scheduled, []
            if not pending:
                return
            for _delay, fn, _kw in pending:
                fn()

    def run_held_threads(self):
        held, self.held = self.held, []
        for target in held:
            target()

    @property
    def state(self):
        return al.alert_settings_state

    def el(self, alert_type):
        return al.alert_settings_state.get_elements(alert_type)

    def save(self, alert_type, alert_id, data):
        assert self.mgr.save_alert(alert_type, alert_id, data)


def install(monkeypatch, tmp_path=None):
    env = Env()
    Env.current = env
    db, mgr = au_fakes.install(monkeypatch)
    env.db, env.mgr = db, mgr

    monkeypatch.setattr(al, "alert_settings_state", al.AlertSettingsState())
    monkeypatch.setattr(al, "_ALERT_VIEW", {})
    monkeypatch.setattr(al, "_TAB_COUNT_LABELS", {})
    monkeypatch.setattr(al, "_LAZY_ALERT_PANEL_BUILDERS", {})
    monkeypatch.setattr(al, "_points_load_state", {"inflight": False, "last_auto_error": None})

    def notify(*args, **kwargs):
        if args:
            kwargs.setdefault("message", args[0])
        env.notices.append((args, kwargs))

    monkeypatch.setattr(al, "notify", notify)

    def notify_progress(title, **kw):
        work = FakeWork(title, **kw)
        env.works.append(work)
        return work

    monkeypatch.setattr(al, "notify_progress", notify_progress)

    def schedule(delay, fn, **kw):
        env.scheduled.append((delay, fn, kw))
        return SimpleNamespace(cancel=lambda: None, deactivate=lambda: None)

    monkeypatch.setattr(al, "layout_schedule", schedule)
    monkeypatch.setattr(al, "app_schedule", schedule)
    monkeypatch.setattr(al, "run_on_ui_loop", lambda fn: fn())
    monkeypatch.setattr(al, "set_alerts_ui_references", lambda tabs: None)
    monkeypatch.setattr(al, "threading", SimpleNamespace(Thread=InlineThread))

    monkeypatch.setattr(media_preview, "tts_voice_settings", lambda: {"rate": 1.0, "pitch": 1.0, "volume": 1.0})
    monkeypatch.setattr(alert_processor, "enqueue_alert", lambda a: env.queued.append(a))
    monkeypatch.setattr(web_engine, "web_engine_instance", None, raising=False)
    monkeypatch.setattr(web_engine, "ALERTS_PAUSED", False, raising=False)

    tw = env.twitch
    monkeypatch.setattr(twitch, "fetch_channel_point_rewards", tw.fetch_channel_point_rewards)
    monkeypatch.setattr(twitch, "get_point_reward_by_id", tw.get_point_reward_by_id)
    monkeypatch.setattr(twitch, "create_point_reward", tw.create_point_reward)
    monkeypatch.setattr(twitch, "update_point_reward", tw.update_point_reward)
    monkeypatch.setattr(twitch, "twitch_api", SimpleNamespace(is_connected=True))

    root = Path(tmp_path) if tmp_path is not None else Path("/nonexistent-mycelian-assets")
    monkeypatch.setattr(file_picker, "assets_root", lambda: root)
    monkeypatch.setattr(file_picker, "open_file_picker", lambda **kw: env.picker_calls.append(kw))
    return env


def restore():
    au_fakes.restore_collections()
    Env.current = None


FIELD_NAMES = (
    "duration_input", "stackable_switch", "fade_in_input", "fade_out_input", "volume_input",
    "gif_dir_input", "gif_file_input", "primary_dir_input", "primary_file_input", "randomized_switch",
    "random_dir_input", "random_chance_input", "randomized_extra_switch", "extra_dir_input",
    "extra_chance_input", "tts_enabled_switch", "tts_source_select", "tts_custom_message_input",
)


class FakeEl:
    """Minimal element stand-in for logic that only reads/writes .value and friends."""

    parent_slot = None

    def __init__(self, value=None, *, options=None, visible=True):
        self.value = value
        self.visible = visible
        self.options = dict(options or {})
        self.enabled = True
        self.text = None
        self.updates = 0
        self.css = set()
        self.handlers = []

    def set_text(self, text):
        self.text = text

    def update(self):
        self.updates += 1

    def set_enabled(self, enabled):
        self.enabled = enabled

    def classes(self, add=None, remove=None, replace=None):
        if add:
            self.css.update(add.split())
        if remove:
            self.css.difference_update(remove.split())
        return self

    def on_value_change(self, fn):
        self.handlers.append(fn)


def fake_class(name):
    return type(name, (FakeEl,), {})


Input = fake_class("Input")
Switch = fake_class("Switch")
Button = fake_class("Button")
Label = fake_class("Label")


TWITCH_FIELDS = {
    "twitch_title_input": "", "twitch_cost_input": 100, "twitch_enabled_switch": True,
    "twitch_user_input_switch": False, "twitch_user_input_prompt": "", "twitch_max_per_stream_switch": False,
    "twitch_max_per_stream_input": 1, "twitch_max_per_user_switch": False, "twitch_max_per_user_input": 1,
    "twitch_cooldown_switch": False, "twitch_cooldown_input": 60, "twitch_skip_queue_switch": True,
}


def make_form(alert_type, select_value="new", options=None):
    """Fake form elements for one alert type (no NiceGUI needed)."""
    els = al.alert_settings_state.get_elements(alert_type)
    defaults = {
        "duration_input": 3.0, "stackable_switch": False, "fade_in_input": 0, "fade_out_input": 0,
        "volume_input": 100, "gif_dir_input": "", "gif_file_input": "", "primary_dir_input": "",
        "primary_file_input": "", "randomized_switch": False, "random_dir_input": "", "random_chance_input": 0,
        "randomized_extra_switch": False, "extra_dir_input": "", "extra_chance_input": 0,
        "tts_enabled_switch": False, "tts_source_select": "alert_message", "tts_custom_message_input": "",
    }
    for name, value in defaults.items():
        cls = Switch if name.endswith("_switch") else Input if name.endswith(("_dir_input", "_file_input")) else FakeEl
        els[name] = cls(value)
    els["volume_value_label"] = Label("100%")
    els["unsaved_label"] = FakeEl(visible=False)
    els["delete_btn"] = Button(visible=False)
    els["alert_select"] = FakeEl(select_value, options=options or {"new": "+"})
    if alert_type in al._QUANTITY_ALERT_TYPES:
        els["range_toggle"] = FakeEl(False)
        els["amount_input"] = FakeEl(1)
        els["min_input"] = FakeEl(1)
        els["max_input"] = FakeEl(5)
        els["exact_input_row"] = FakeEl()
        els["range_input_row"] = FakeEl(visible=False)
    if alert_type == "points":
        els["audio_only_switch"] = Switch(False)
        els["enable_alert_switch"] = Switch(False)
        els["save_btn"] = Button()
        els["channel_points_locked_banner"] = FakeEl(visible=False)
        for name, value in TWITCH_FIELDS.items():
            els[name] = (Switch if name.endswith("_switch") else FakeEl)(value)
    return els

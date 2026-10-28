# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/alertsettings.py, part 2: building the Alerts tab and its panels,
file cards, dependent dimming, the alert list, sorting, duplicate and range toggles."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "tests"))

import _alertsettings_env as E  # noqa: E402
from nicegui import ui  # noqa: E402
from ui_sim import run_ui  # noqa: E402

from modules import web_engine  # noqa: E402
from modules.uiwindows import alertsettings as al  # noqa: E402
from modules.uiwindows import media_preview  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    e = E.install(monkeypatch, tmp_path)
    yield e
    E.restore()


def run(build, fn):
    async def check(user):
        with user:
            result = fn(user)
            if hasattr(result, "__await__"):
                await result

    run_ui(build, check)


def fire(element, kind="click"):
    for listener in list(element._event_listeners.values()):
        if listener.type == kind:
            listener.handler(SimpleNamespace(args=None, sender=element))


def panels(*types):
    def build():
        for t in types:
            if t == "points":
                al.create_points_alert_panel()
            else:
                al.create_alert_type_panel(t)

    return build


# ======================================================================= the whole tab

def test_create_alert_settings_tab(env, monkeypatch):
    env.save("bits", "bits100", {})
    env.save("subs", "subs3", {})
    inits = []
    loads = []
    monkeypatch.setattr(al, "load_twitch_point_rewards", lambda **kw: loads.append(kw))
    real_init = al.initialize_tab_values
    monkeypatch.setattr(al, "initialize_tab_values", lambda name: inits.append(name) or real_init(name))

    def fn(user):
        state = env.state
        assert state.tabs_component is not None and state.active_tab_name == "Bits"
        assert set(al._TAB_COUNT_LABELS) == set(al.ALERT_TAB_TO_TYPE)
        assert al._TAB_COUNT_LABELS["Bits"].text == "1" and al._TAB_COUNT_LABELS["Bits"].visible
        assert al._TAB_COUNT_LABELS["Subscriptions"].text == "2"  # + Resub Fallback
        assert al._TAB_COUNT_LABELS["Raids"].visible is False
        assert set(al._LAZY_ALERT_PANEL_BUILDERS) == {"Subscriptions", "Streaks", "Gift Subs", "Donations",
                                                     "Raids", "Follows", "Channel Points"}
        assert "alert_select" in env.el("bits")
        # recorded timers: the tab watcher, 3 points loaders, Bits init (+ panel timers)
        delays = [d for d, _fn, _kw in env.scheduled]
        assert 0.4 in delays and delays.count(1.5) == 1 and 3.0 in delays

        # Switching tabs builds the lazy panel and initializes it.
        state.tabs_component.value = "Raids"
        assert "Raids" not in al._LAZY_ALERT_PANEL_BUILDERS
        assert "alert_select" in env.el("raids") and inits[-1] == "Raids"
        assert state.active_tab_name == "Raids"

        # points loader only runs on the points tab with a select
        watcher = next(f for d, f, kw in env.scheduled if kw.get("active"))
        points_loaders = [f for d, f, kw in env.scheduled if d in (1.5, 3.0)]
        state.current_tab = "bits"
        points_loaders[0]()
        state.current_tab = "points"
        points_loaders[0]()  # select not built yet
        assert loads == []
        state.tabs_component.value = "Channel Points"
        assert "alert_select" in env.el("points")
        state.current_tab = "points"
        points_loaders[1]()
        assert loads

        # The watcher catches a tab change the event missed.
        state.tab_switch_guard = True
        state.tabs_component.value = "Follows"  # guarded: no switch
        state.tab_switch_guard = False
        assert state.active_tab_name == "Channel Points"
        for flag in ("unsaved_prompt_open", "tab_switch_guard", "ignore_field_dirty"):
            setattr(state, flag, True)
            watcher()
            setattr(state, flag, False)
        assert state.active_tab_name == "Channel Points"
        watcher()
        assert state.active_tab_name == "Follows"
        watcher()  # same tab: nothing
        assert state.active_tab_name == "Follows"

    run(al.create_alert_settings_tab, fn)


def test_points_loader_logs_errors(env, monkeypatch):
    def fn(user):
        loader = next(f for d, f, kw in env.scheduled if d == 3.0)
        env.state.current_tab = "points"
        monkeypatch.setattr(env.state, "get_elements", lambda t: (_ for _ in ()).throw(RuntimeError()))
        loader()  # error is logged

    run(al.create_alert_settings_tab, fn)


def test_tab_value_event_without_name_is_ignored(env, monkeypatch):
    calls = []
    monkeypatch.setattr(al, "request_alert_tab_change", calls.append)
    monkeypatch.setattr(al, "_resolve_alert_tab_name", lambda e: "")

    def fn(user):
        env.state.tabs_component.value = "Raids"
        assert calls == []

    run(al.create_alert_settings_tab, fn)


# ======================================================================= per-type panels

@pytest.mark.parametrize("alert_type", ["bits", "subs", "streaks", "giftsubs", "donations", "raids", "follows"])
def test_create_alert_type_panel(env, alert_type):
    def fn(user):
        els = env.el(alert_type)
        opts = els["alert_select"].options
        assert opts["new"] == "+ Create New Alert"
        if alert_type == "subs":
            assert opts["subs_fallback"] == "Resub Fallback" and "fallback_toggle" in els
        if alert_type in al._QUANTITY_ALERT_TYPES:
            assert {"range_toggle", "amount_input", "min_input", "max_input"} <= set(els)
        else:
            assert "range_toggle" not in els
        assert els["delete_btn"].visible is False
        assert env.state.current_tab == alert_type
        assert [d for d, *_ in env.scheduled][-2:] == [0.5, 1.0]
        env.run_scheduled()
        assert env.state.get_original_values(alert_type)

    run(panels(alert_type), fn)


def test_panel_lists_saved_alerts_sorted(env):
    for aid in ("bits500", "bits1-99", "bits100"):
        env.save("bits", aid, {"single_audio_name": "a.mp3"})

    async def fn(user):
        assert list(env.el("bits")["alert_select"].options) == ["new", "bits1-99", "bits100", "bits500"]
        await user.should_see("1-99 Bits")
        await user.should_see("Range")
        await user.should_see("Choose an alert")

    run(panels("bits"), fn)


def test_follows_and_points_headers(env):
    async def fn(user):
        await user.should_see("Plays for every new follower")
        await user.should_see("Use as alert")
        assert "enable_alert_switch" in env.el("points") and "save_btn" in env.el("points")
        assert "audio_only_switch" in env.el("points") and "twitch_title_input" in env.el("points")
        assert env.el("points")["channel_points_locked_banner"].visible is False
        assert "save_btn" not in env.el("follows")

    run(panels("follows", "points"), fn)


def test_register_ui_elements_is_harmless():
    al.register_ui_elements()


def test_initial_alert_count(env):
    assert al._initial_alert_count("subs") == 1
    env.save("subs", "subs_fallback", {})
    assert al._initial_alert_count("subs") == 1
    env.save("raids", "raids5", {})
    assert al._initial_alert_count("raids") == 1


# ======================================================================= file cards

def test_media_card_refresh_and_edit_toggle(env, monkeypatch, tmp_path):
    monkeypatch.setattr(web_engine, "web_engine_instance", SimpleNamespace(is_running=True, port=5555))

    async def fn(user):
        els = env.el("bits")
        await user.should_see("No GIF or image")
        els["gif_dir_input"].value = "/assets/gifs/"
        els["gif_file_input"].value = "party.gif"
        await user.should_see("party.gif")
        thumbs = [e for e in user.find("party.gif").elements]
        assert thumbs
        html = [e for e in ui.context.client.layout.descendants() if "alr-thumb" in e.classes]
        assert any("http://127.0.0.1:5555/assets/gifs/party.gif" in h.content for h in html)
        els["gif_file_input"].value = ""
        await user.should_see("No GIF or image")
        # random folder card title comes from the last folder part
        els["random_dir_input"].value = "/assets/alerts/bits/random/"
        await user.should_see("random folder")
        els["random_dir_input"].value = ""
        await user.should_see("No folder")
        # the pencil shows the hidden path inputs
        edits = [e for e in ui.context.client.layout.descendants() if "alr-edit" in e.classes]
        assert all(not e.visible for e in edits)
        pencil = next(e for e in ui.context.client.layout.descendants()
                      if getattr(e, "_props", {}).get("icon") == "edit")
        pencil.run_method  # noqa: B018 - exists
        for listener in pencil._event_listeners.values():
            listener.handler(SimpleNamespace())
        assert any(e.visible for e in edits)

    run(panels("bits"), fn)


def test_media_card_image_timing(env, tmp_path):
    from PIL import Image

    gifs = tmp_path / "gifs"
    gifs.mkdir()
    frames = [Image.new("RGB", (4, 4), (i * 40, 0, 0)) for i in range(3)]
    frames[0].save(gifs / "a.gif", save_all=True, append_images=frames[1:], duration=[100, 100, 100], loop=0)
    media_preview._timing_cache.clear()

    async def fn(user):
        els = env.el("bits")
        els["gif_dir_input"].value = "/assets/gifs/"
        els["gif_file_input"].value = "a.gif"
        await user.should_see("0.3 s")

    run(panels("bits"), fn)


def test_media_card_sound_preview(env, monkeypatch, tmp_path):
    sounds = tmp_path / "sounds"
    sounds.mkdir()
    (sounds / "ding.mp3").write_bytes(b"x")
    monkeypatch.setattr(media_preview, "media_url", lambda p: f"/media/{p.name}" if p else "")

    async def fake_duration(url, client=None):
        return 2.5

    monkeypatch.setattr(media_preview, "audio_duration", fake_duration)
    data = []
    real_set = media_preview.set_button_data
    monkeypatch.setattr(media_preview, "set_button_data", lambda b, **kw: data.append(kw) or real_set(b, **kw))

    async def fn(user):
        els = env.el("bits")
        els["primary_dir_input"].value = "/assets/sounds/"
        els["primary_file_input"].value = "ding.mp3"
        for _ in range(5):
            await asyncio.sleep(0)
        await user.should_see("2.5 s")
        assert {"src": "/media/ding.mp3"} in data
        els["primary_file_input"].value = "missing.mp3"
        assert {"src": ""} in data

    run(panels("bits"), fn)


def test_media_card_sound_task_error(env, monkeypatch, tmp_path):
    sounds = tmp_path / "s"
    sounds.mkdir()
    (sounds / "x.mp3").write_bytes(b"x")
    monkeypatch.setattr(media_preview, "media_url", lambda p: "/m/x.mp3" if p else "")
    def boom(coro, name=None):
        coro.close()
        raise RuntimeError("no loop")

    monkeypatch.setattr(al, "background_tasks", SimpleNamespace(create=boom))

    def fn(user):
        els = env.el("bits")
        els["primary_dir_input"].value = "/assets/s/"
        els["primary_file_input"].value = "x.mp3"  # error is logged

    run(panels("bits"), fn)


def test_media_card_random_folder(env, monkeypatch, tmp_path):
    folder = tmp_path / "alerts" / "rnd"
    folder.mkdir(parents=True)
    for name in ("a.mp3", "b.wav"):
        (folder / name).write_bytes(b"x")
    one = tmp_path / "alerts" / "one"
    one.mkdir()
    (one / "c.mp3").write_bytes(b"x")
    monkeypatch.setattr(media_preview, "folder_base_url", lambda f: "/m/" if f else "")

    async def fn(user):
        els = env.el("bits")
        els["random_dir_input"].value = "/assets/alerts/rnd/"
        await user.should_see("2 sounds")
        els["extra_dir_input"].value = "/assets/alerts/one/"
        await user.should_see("1 sound")
        # a random pick shows in the path line
        btn = al._view("bits")["preview_buttons"][1]
        assert btn is not None

    run(panels("bits"), fn)


def test_media_card_on_pick_updates_path(env, monkeypatch):
    picks = {}
    real = media_preview.preview_button

    def capture(tip, **kw):
        btn = real(tip, **kw)
        if kw.get("pv") == "random":
            picks.setdefault("fns", []).append(kw["on_pick"])
        return btn

    monkeypatch.setattr(media_preview, "preview_button", capture)

    async def fn(user):
        env.el("bits")["random_dir_input"].value = "/assets/alerts/x/"
        picks["fns"][0]("boom.mp3")
        await user.should_see("▶ boom.mp3 · /assets/alerts/x/")

    run(panels("bits"), fn)


# ======================================================================= dimming

def test_refresh_alert_dims(env):
    def fn(user):
        els = env.el("bits")
        view = al._view("bits")
        assert all("is-dim" in e.classes for e in view["dim_tts_source"])
        assert els["randomized_extra_switch"].enabled is False
        els["tts_enabled_switch"].value = True
        assert all("is-dim" not in e.classes for e in view["dim_tts_source"])
        assert all("is-dim" in e.classes for e in view["dim_tts_custom"])
        els["tts_source_select"].value = "custom_message"
        assert all("is-dim" not in e.classes for e in view["dim_tts_custom"])
        els["randomized_switch"].value = True
        assert els["randomized_extra_switch"].enabled is True
        assert all("is-dim" not in e.classes for e in view["dim_random"])
        assert all("is-dim" in e.classes for e in view["dim_extra"])
        els["randomized_extra_switch"].value = True
        assert all("is-dim" not in e.classes for e in view["dim_extra"])
        # turning random off also turns extra off and marks the form changed
        env.state.get_original_values("bits")["randomized_extra_switch"] = True
        els["randomized_switch"].value = False
        assert els["randomized_extra_switch"].value is False
        assert "bits" in env.state.dirty_tabs

    run(panels("bits"), fn)


def test_refresh_alert_dims_points_twitch_rows(env):
    def fn(user):
        els = env.el("points")
        view = al._view("points")
        for switch, key in (("twitch_max_per_stream_switch", "per_stream"), ("twitch_max_per_user_switch", "per_user"),
                            ("twitch_cooldown_switch", "cooldown"), ("twitch_user_input_switch", "prompt")):
            assert all("is-dim" in e.classes for e in view[f"dim_{key}"])
            els[switch].value = True
            assert all("is-dim" not in e.classes for e in view[f"dim_{key}"])

    run(panels("points"), fn)


def test_refresh_dims_without_elements(env):
    al._refresh_alert_dims("raids")  # nothing built: no errors
    env.el("raids")["randomized_extra_switch"] = object()
    al._refresh_alert_dims("raids")


def test_random_switch_changed(env):
    al._random_switch_changed("bits", True)
    al._random_switch_changed("bits", False)  # no extra switch
    extra = E.FakeEl(False)
    env.el("bits")["randomized_extra_switch"] = extra
    al._random_switch_changed("bits", False)
    assert extra.value is False and env.state.dirty_tabs == set()
    extra.value = True
    al._random_switch_changed("bits", False)
    assert extra.value is False


def test_watch_dims_skips_missing(env):
    al._watch_dims("bits", "nope")


def test_volume_slider_change_updates_label(env):
    def fn(user):
        els = env.el("bits")
        slider = els["volume_input"]
        slider.value = 40
        for listener in slider._event_listeners.values():
            if listener.type == "change":
                listener.handler(SimpleNamespace(args=40, sender=slider))
        assert els["volume_value_label"].text == "40%"
        slider.value = None
        for listener in slider._event_listeners.values():
            if listener.type == "change":
                listener.handler(SimpleNamespace(args=None, sender=slider))
        assert els["volume_value_label"].text == "100%"

    run(panels("bits"), fn)


def test_tts_source_change_handler(env, monkeypatch):
    refreshed = []
    monkeypatch.setattr(al, "update_tts_custom_message_visibility", refreshed.append)

    def fn(user):
        env.el("bits")["tts_source_select"].value = "custom_message"
        assert refreshed == ["bits"]

    run(panels("bits"), fn)


# ======================================================================= the alert list

def test_alert_list_entries(env):
    env.save("subs", "subs1-3", {"gif_name": "g.gif", "tts_enabled": True})
    env.save("subs", "subs_fallback", {"randomized": True})
    env.save("points", "rw1", {"enable_alert": True})
    env.save("points", "rw2", {})
    env.el("subs")["alert_select"] = E.FakeEl(options={"new": "+", "subs1-3": "1-3 Months",
                                                       "subs_fallback": "Resub Fallback", "subs5": "5 Months"})
    entries, options = al._alert_list_entries("subs")
    by_id = {e[0]: e for e in entries}
    assert set(by_id) == {"subs1-3", "subs_fallback", "subs5"}
    assert "Range" in by_id["subs1-3"][2] and "image" in by_id["subs1-3"][3]
    assert "record_voice_over" in by_id["subs1-3"][3]
    assert "Fallback" in by_id["subs_fallback"][2] and by_id["subs5"][2] == ""
    env.el("points")["alert_select"] = E.FakeEl(options={"loading": "L", "rw1": "Spin (2500 points)",
                                                         "rw2": "Plain"})
    entries, _ = al._alert_list_entries("points")
    by_id = {e[0]: e for e in entries}
    assert by_id["rw1"][1] == "Spin" and "2,500" in by_id["rw1"][2] and by_id["rw1"][4] is False
    assert by_id["rw2"][1] == "Plain" and by_id["rw2"][2] == "" and by_id["rw2"][4] is True
    assert "Not an alert" in by_id["rw2"][3]
    assert al._alert_list_entries("raids") == ([], {})


def test_placeholder_message():
    assert al._placeholder_message({"error": "E", "loading": "L"}) == "L"
    assert al._placeholder_message({"no_rewards": "N"}) == "N"
    assert al._placeholder_message({"rw": "x"}) == ""


def test_refresh_tab_count(env):
    al._refresh_tab_count("bits", 3)  # no label registered
    label = E.FakeEl()
    al._TAB_COUNT_LABELS["Bits"] = label
    al._refresh_tab_count("bits", 0)
    assert label.text == "0" and label.visible is False
    al._refresh_tab_count("bits", 2)
    assert label.visible is True


def test_render_alert_list_search_and_titles(env):
    env.save("bits", "bits100", {})
    env.save("bits", "bits200", {})

    async def fn(user):
        els = env.el("bits")
        sel = els["alert_select"]
        view = al._view("bits")
        search = next(e for e in ui.context.client.layout.descendants()
                      if getattr(e, "_props", {}).get("placeholder") == "Search")
        search.value = "200"
        assert view["query"] == "200"
        await user.should_not_see("100 Bits")
        search.value = "zzz"
        await user.should_see("No alerts match your search.")
        search.value = ""
        sel.value = "new"
        assert "is-active" in view["new"].classes
        assert view["title"].text == "New alert"
        with al.suppress_alert_selection_handler():
            sel.value = "bits200"
        assert "is-active" not in view["new"].classes
        assert view["title"].text == "200 Bits"
        with al.suppress_alert_selection_handler():
            sel.options = {"new": "+", "ghost": "Ghost"}
            sel.value = "ghost"
        al._render_alert_list("bits")
        assert view["title"].text == "Ghost"

    run(panels("bits"), fn)


def test_render_alert_list_empty_states(env):
    async def fn(user):
        await user.should_see("No alerts yet. Click New alert to make one.")
        pts = env.el("points")["alert_select"]
        with al.suppress_alert_selection_handler():
            pts.options = {"not_connected": "Twitch not connected"}
        al._render_alert_list("points")
        await user.should_see("Twitch not connected")
        assert al._view("points")["title"].text == "Choose a reward"
        with al.suppress_alert_selection_handler():
            pts.options = {"new": "+"}
            pts.value = "new"
        al._render_alert_list("points")
        assert al._view("points")["title"].text == "New reward"
        await user.should_see("No alerts yet")
        with al.suppress_alert_selection_handler():
            pts.options = {"new": "+", "x": ""}
            pts.value = "x"
        al._render_alert_list("points")
        assert al._view("points")["title"].text == "Channel Points"

    run(panels("raids", "points"), fn)


def test_render_alert_list_without_container(env):
    al._render_alert_list("bits")
    env.el("bits")["alert_select"] = E.FakeEl()
    al._render_alert_list("bits")


def test_pick_alert_and_list_click(env):
    env.save("raids", "raids10", {})

    async def fn(user):
        sel = env.el("raids")["alert_select"]
        al._pick_alert("follows", "x")  # no select
        al._pick_alert("raids", "new")
        assert sel.value == "new"
        al._pick_alert("raids", "new")  # unchanged
        item = next(e for e in ui.context.client.layout.descendants() if "alr-item" in e.classes)
        fire(item)
        assert sel.value == "raids10"
        assert env.el("raids")["delete_btn"].visible is True

    run(panels("raids"), fn)


def test_new_button_click(env):
    def fn(user):
        fire(al._view("raids")["new"])
        assert env.el("raids")["alert_select"].value == "new"

    run(panels("raids"), fn)


def test_points_refresh_button(env, monkeypatch):
    calls = []
    monkeypatch.setattr(al, "load_twitch_point_rewards", lambda **kw: calls.append(kw))

    def fn(user):
        btn = next(e for e in ui.context.client.layout.descendants()
                   if getattr(e, "_props", {}).get("icon") == "refresh")
        for listener in btn._event_listeners.values():
            listener.handler(SimpleNamespace())
        assert calls == [{"user_initiated": True}]

    run(panels("points"), fn)


def test_subs_fallback_toggle_saves(env):
    def fn(user):
        toggle = env.el("subs")["fallback_toggle"]
        assert toggle.value is False
        toggle.value = True
        assert env.db.store["Alerts/Settings/resub_fallback_enabled"] == {"enabled": True}

    run(panels("subs"), fn)


@pytest.mark.xfail(strict=True, reason="BUG: the Resub fallback switch saves at once but sits in the form "
                                       "fields, so flipping it marks the Subscriptions alert as unsaved")
def test_subs_fallback_toggle_does_not_mark_form_unsaved(env):
    def fn(user):
        env.run_scheduled()  # initialize_alert_values stores the originals
        assert al.alert_form_is_dirty("subs") is False
        env.el("subs")["fallback_toggle"].value = True
        assert al.alert_form_is_dirty("subs") is False

    run(panels("subs"), fn)


# ======================================================================= trigger / range

def test_toggle_range_inputs():
    exact, rng = E.FakeEl(), E.FakeEl()
    al.toggle_range_inputs(SimpleNamespace(value=True), exact, rng)
    assert (exact.visible, rng.visible) == (False, True)
    al.toggle_range_inputs(SimpleNamespace(value=False), exact, rng)
    assert (exact.visible, rng.visible) == (True, False)


def test_range_toggle_switches_rows(env):
    def fn(user):
        els = env.el("bits")
        els["range_toggle"].value = True
        assert els["range_input_row"].visible and not els["exact_input_row"].visible

    run(panels("bits"), fn)


# ======================================================================= sorting / loading

def test_sort_alert_ids(env):
    items = [("bits500", {}), ("bitsx-y", {}), ("subs_fallback", {}), ("bits100-200", {}), ("bits100", {}),
             ("bitsabc", {}), ("7", {})]
    out = [i for i, _ in al.sort_alert_ids("bits", items)]
    assert out == ["7", "bits100", "bits100-200", "bits500", "bitsx-y", "bitsabc", "subs_fallback"]
    pts = [("b", {"title": "zeta"}), ("a", {"title": "Alpha"})]
    assert [i for i, _ in al.sort_alert_ids("points", pts)] == ["a", "b"]


def test_sort_alert_ids_error_returns_input(env, monkeypatch):
    monkeypatch.setattr(env.mgr, "get_display_name", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    items = [("b", {}), ("a", {})]
    assert al.sort_alert_ids("points", items) is items


def test_get_alerts_for_type(env, monkeypatch):
    from modules import alertutils

    assert al.get_alerts_for_type("raids") == {}
    env.save("raids", "raids5", {})
    assert list(al.get_alerts_for_type("raids")) == ["raids5"]
    monkeypatch.setattr(alertutils, "initialize_alert_state", lambda: (_ for _ in ()).throw(RuntimeError("db")))
    assert al.get_alerts_for_type("raids") == {}
    assert env.last()["dedupe_key"] == "alerts:list_failed"
    assert "raids: RuntimeError: db" in env.last()["details"]


# ======================================================================= duplicate

def test_shift_duplicate_amount(env):
    al._shift_duplicate_amount("follows")  # not amount-based
    al._shift_duplicate_amount("bits")  # no inputs
    env.save("bits", "bits101", {})
    env.save("bits", "bits11-20", {})
    els = env.el("bits")
    els.update(range_toggle=E.FakeEl(False), amount_input=E.FakeEl(100), min_input=E.FakeEl(1),
               max_input=E.FakeEl(10))
    al._shift_duplicate_amount("bits")
    assert els["amount_input"].value == 102
    els["amount_input"].value = "bad"
    al._shift_duplicate_amount("bits")
    assert els["amount_input"].value == 1
    els["range_toggle"].value = True
    al._shift_duplicate_amount("bits")
    assert (els["min_input"].value, els["max_input"].value) == (21, 30)
    els["min_input"].value = "x"
    al._shift_duplicate_amount("bits")
    assert els["min_input"].value == "x"


def test_duplicate_requires_selection(env):
    al.duplicate_current_alert("bits")
    env.el("bits")["alert_select"] = E.FakeEl("new")
    al.duplicate_current_alert("bits")
    env.el("points")["alert_select"] = E.FakeEl("loading")
    al.duplicate_current_alert("points")
    assert env.titles() == ["Choose an alert to duplicate"] * 3


def test_duplicate_bits_alert(env):
    env.save("bits", "bits100", {"duration": 7, "gif_name": "g.gif"})

    def fn(user):
        els = env.el("bits")
        els["alert_select"].value = "bits100"
        assert els["duration_input"].value == 7
        al.duplicate_current_alert("bits")
        assert els["alert_select"].value == "new"
        assert els["duration_input"].value == 7 and els["gif_file_input"].value == "g.gif"
        assert els["amount_input"].value == 101
        assert els["exact_input_row"].visible is True
        assert els["delete_btn"].visible is False
        assert env.titles()[-1] == "Copied into a new alert"
        assert al.alert_form_is_dirty("bits") is True

    run(panels("bits"), fn)


def test_duplicate_follow_and_points(env, monkeypatch):
    env.save("follows", "follows1", {"duration": 4})
    env.twitch.rewards["reward-1"] = {"title": "Spin", "cost": 50}

    def fn(user):
        env.el("follows")["alert_select"].value = "follows1"
        al.duplicate_current_alert("follows")
        assert env.titles()[-1] == "Copied into a new follow alert"
        pts = env.el("points")
        with al.suppress_alert_selection_handler():
            pts["alert_select"].options = {"new": "+", "reward-1": "Spin (50 points)"}
            pts["alert_select"].value = "reward-1"
        al.load_point_reward_settings("points", "reward-1")
        al.duplicate_current_alert("points")
        assert pts["twitch_title_input"].value == "Spin copy"
        assert env.titles()[-1] == "Copied into a new reward"

    run(panels("follows", "points"), fn)


def test_duplicate_copy_error_is_logged(env):
    class Stubborn(E.FakeEl):
        def __setattr__(self, k, v):
            if k == "value" and getattr(self, "_locked", False):
                raise RuntimeError("read only")
            super().__setattr__(k, v)

    els = env.el("follows")
    els["alert_select"] = E.FakeEl("follows1")
    for name in E.FIELD_NAMES:
        els[name] = E.FakeEl(1)
    odd = Stubborn(5)
    els["odd_field"] = odd
    odd._locked = False
    import modules.uiwindows.alertsettings as mod

    real = mod.set_default_values_for_new_alert

    def defaults(t):
        real(t)
        odd._locked = True

    mod_set = pytest.MonkeyPatch()
    mod_set.setattr(mod, "set_default_values_for_new_alert", defaults)
    try:
        al.duplicate_current_alert("follows")
    finally:
        mod_set.undo()
    assert env.titles()[-1] == "Copied into a new follow alert"

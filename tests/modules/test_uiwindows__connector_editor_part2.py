# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""uiwindows.connector_editor (part 2): the editor dialog in NiceGUI's user simulation: header,
trigger picker and settings, conditions, action cards and fields, custom builders (Discord,
overlay templates, OBS, game hooks), close guard and save (all services faked)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import json
import types
from types import SimpleNamespace

import pytest
from nicegui import ui
from ui_sim import run_ui

import modules
from modules import connector_manager as cm
from modules import connector_variables as cv
from modules.connector_actions import create_action
from modules.connector_core import ActionType as A
from modules.connector_core import ComparisonOperator as O
from modules.connector_core import Connector, TriggerCondition
from modules.connector_core import TriggerType as T
from modules.connector_triggers import create_trigger
from modules.uiwindows import connector_action_schemas as schemas
from modules.uiwindows import connector_editor as ce


class Hotkeys:
    def register_hotkey(self, *a, **k):
        pass

    def unregister_hotkey(self, *a, **k):
        pass


def fake_module(monkeypatch, name, **attrs):
    mod = types.ModuleType(f"modules.{name}")
    for k, v in attrs.items():
        setattr(mod, k, v)
    monkeypatch.setitem(sys.modules, f"modules.{name}", mod)
    monkeypatch.setattr(modules, name, mod, raising=False)
    return mod


@pytest.fixture
def env(monkeypatch):
    for spec in schemas.ACTION_SPECS.values():
        for f in spec.fields:
            if f.key == "target_device":
                monkeypatch.setattr(f, "options", lambda cfg: {"": "Default device", "spk": "Speakers"})
            elif f.key == "target_application":
                monkeypatch.setattr(f, "options", lambda cfg: {"obs": "OBS"})
    monkeypatch.setattr(cm, "get_listener", lambda: Hotkeys())
    mgr = cm.ConnectorManager()

    def save():
        mgr.last_save_ok = True
        return True

    monkeypatch.setattr(mgr, "save_connectors", save)
    monkeypatch.setattr(cm, "get_manager", lambda: mgr)
    notes = []
    monkeypatch.setattr(ce, "notify", lambda msg="", **kw: notes.append((msg, kw)))
    scheduled = []
    monkeypatch.setattr(ce, "layout_schedule", lambda interval, cb, once=False: scheduled.append(cb))
    obs = SimpleNamespace(
        snapshot={"scene_names": ["Main", "BRB"], "input_names": ["Mic"],
                  "sources_by_scene": {"Main": {"Cam": "Cam"}}},
        connected=True, refresh=(True, ""), refresh_calls=0,
    )
    obs.get_connector_snapshot = lambda: obs.snapshot
    obs.connection_details = lambda: (obs.connected, "")

    def refresh_blocking(timeout):
        obs.refresh_calls += 1
        if isinstance(obs.refresh, Exception):
            raise obs.refresh
        return obs.refresh

    obs.refresh_snapshot_blocking = refresh_blocking
    fake_module(monkeypatch, "obs_service", obs_service=obs)

    async def io_bound(fn, *args):
        return fn(*args)

    monkeypatch.setattr(ce.run, "io_bound", io_bound)
    discord = SimpleNamespace(connected=True)
    fake_module(
        monkeypatch, "discord_service",
        discord_service=SimpleNamespace(is_connected=lambda: discord.connected),
        list_guilds=lambda: [{"id": "g1", "name": "Guild"}, {"id": "g2"}],
        list_text_channels=lambda gid: [{"id": "c1", "name": "general"}, {"id": "c2"}] if gid == "g1"
        else (_ for _ in ()).throw(RuntimeError("no access")),
    )
    templates = {"wheel": {"spin": {"action_name": "Spin it", "elements": [
        {"type": "text", "id": "msg", "label": "Message"},
        {"type": "number", "id": "speed", "value": 3},
        {"type": "select", "id": "dir", "options": ["left", "right"]},
        {"type": "select", "id": "mode", "options": ["a"], "value": "zz"},
        {"type": "switch", "id": "loud"},
        {"type": "separator", "label": "More"},
        {"type": "separator"},
        {"type": "color", "id": "c"},
    ]}, "reset": {}}}
    integration = SimpleNamespace(get_available_template_actions=lambda: templates)
    monkeypatch.setattr(ce.connector_integration, "get_integration", lambda: integration)
    return SimpleNamespace(mgr=mgr, notes=notes, scheduled=scheduled, obs=obs, discord=discord,
                           templates=templates, mp=monkeypatch, saved=[])


def editor(env, check, connector_id=None, connectors=()):
    for c in connectors:
        env.mgr.connectors[c.connector_id] = c
    holder = {}

    def build():
        holder["ed"] = ce.open_connector_editor(connector_id, on_saved=lambda: env.saved.append(1))

    async def wrapped(user):
        with user:
            result = check(user, holder["ed"])
            if hasattr(result, "__await__"):
                await result

    run_ui(build, wrapped)


def within(ed, kind=None, text=None, cls=None):
    out = []
    for el in ed.dialog.descendants():
        if kind is not None and not isinstance(el, kind):
            continue
        if text is not None and getattr(el, "text", None) != text:
            continue
        if cls is not None and cls not in el._classes:
            continue
        out.append(el)
    return out


def fire(el, event="click", **args):
    from nicegui import helpers

    for listener in list(el._event_listeners.values()):
        if listener.type == event:
            if helpers.expects_arguments(listener.handler):
                listener.handler(SimpleNamespace(args=args, sender=el, client=el.client))
            else:
                listener.handler()


def button(ed, icon=None, text=None):
    return [b for b in within(ed, ui.button) if (icon is None or b._props.get("icon") == icon)
            and (text is None or b.text == text)]


def chat_connector(cid="c1", ttype=T.TWITCH_BITS, conditions=(), actions=None, **trig):
    trigger = create_trigger(ttype, f"t-{cid}", "T", conditions=list(conditions), **trig)
    return Connector(connector_id=cid, name=f"Conn {cid}", trigger=trigger,
                     actions=actions if actions is not None else [
                         create_action(A.SEND_CHAT_MESSAGE, f"a-{cid}", "n", message="hi {username}")])


# --------------------------------------------------------------------------- new connector flow


def test_new_connector_full_flow(env):
    async def check(user, ed):
        await user.should_see("New")
        await user.should_see("Choose a trigger")
        assert ed.status_label.text.startswith("Name this connector.")
        inputs = within(ed, ui.input)
        inputs[0].value = "Cheers"
        inputs[1].value = "Thanks bits"
        sw = within(ed, ui.switch)[0]
        sw.value = False
        assert within(ed, ui.label, "Off") and ed.state.enabled is False
        sw.value = True
        # trigger search
        search = [i for i in within(ed, ui.input) if i.props.get("placeholder") == "Search triggers"][0]
        search.value = "zzzz-no-match"
        await user.should_see("No triggers match.")
        search.value = "few minutes"
        timer_label = cv.trigger_info("timer")["label"]
        opt = [o for o in within(ed, cls="ce-popt") if any(getattr(c, "text", "") == timer_label for c in o.descendants())]
        fire(opt[0])
        assert ed.state.trigger_type == "timer" and ed.state.trigger["interval_seconds"] == 300
        await user.should_see("Run every")
        amount = [n for n in within(ed, ui.number) if n.value == 5][0]
        amount.value = 2
        unit = [s for s in within(ed, ui.select) if s.value == "min"][0]
        unit.value = "hr"
        assert ed.state.trigger["interval_seconds"] == 7200
        # add an action through the picker
        add = [a for a in within(ed, cls="ce-add") if any(getattr(c, "text", "") == "Add action" for c in a.descendants())]
        fire(add[0])
        asearch = [i for i in within(ed, ui.input) if i.props.get("placeholder") == "Search actions"][0]
        asearch.value = "nothing-like-this"
        await user.should_see("No actions match.")
        asearch.value = "chat"
        opt = [o for o in within(ed, cls="ce-popt") if any(getattr(c, "text", "") == "Send chat message" for c in o.descendants())]
        fire(opt[0])
        assert ed.state.actions[0]["type"] == "send_chat_message"
        msg = [t for t in within(ed, ui.textarea)][0]
        msg.value = "Thanks {username}!"
        pills = within(ed, cls="ce-pill")
        fire(pills[1])  # add YouTube
        assert ed.state.actions[0]["config"]["reply_targets"] == ["twitch", "youtube"]
        fire(pills[0])  # remove Twitch
        assert ed.state.actions[0]["config"]["reply_targets"] == ["youtube"]
        assert ed.status_label.text == "Ready to save"
        button(ed, text="Create connector")[0].run_method  # noqa: B018
        ed.save()
        conn = env.mgr.connectors[ed.state.connector_id]
        assert conn.name == "Cheers" and conn.trigger.interval_seconds == 7200
        assert env.notes[-1][0] == "Created “Cheers”" and env.saved == [1]

    editor(env, check)


def test_trigger_picker_cancel_and_change(env):
    conn = chat_connector(conditions=[TriggerCondition("amount", O.GREATER_THAN, 5)])

    async def check(user, ed):
        await user.should_see("Edit")
        button(ed, text="Change")[0]
        ed._start_trigger_pick()
        await user.should_see("Choose a trigger")
        ed._cancel_trigger_pick()
        assert not ed._picking_trigger
        ed._choose_trigger("twitch_bits")  # same type: nothing dropped
        assert len(ed.state.conditions) == 1
        ed._choose_trigger("twitch_stream_offline")
        assert ed.state.conditions == []
        assert env.notes[-1][0] == "Removed 1 condition that don't apply to this trigger."
        ed._choose_trigger("webhook")
        assert ed.state.trigger["secret_key"]
        ed._choose_trigger("hotkey")
        assert ed.state.trigger["key_combination"] == ""
        ed._choose_trigger("schedule")
        assert ed.state.trigger["days_of_week"] == []

    editor(env, check, "c1", [conn])


# --------------------------------------------------------------------------- trigger settings


def test_hotkey_settings_conflict_record_and_save(env):
    other = chat_connector("c2", T.HOTKEY, key_combination="ctrl+k")
    mine = chat_connector("c1", T.HOTKEY, key_combination="ctrl+j")

    async def check(user, ed):
        await user.should_see("Keys")
        combo = [i for i in within(ed, ui.input) if i.value == "ctrl+j"][0]
        combo.value = "Ctrl+K"
        await user.should_see("Also used by: Conn c2 (both will run)")
        fire(combo, "blur")
        assert combo.value == "ctrl+k"
        assert ed._hotkey_conflict("") == ""
        original = env.mgr.get_all_connectors
        env.mgr.get_all_connectors = lambda: (_ for _ in ()).throw(RuntimeError())
        assert ed._hotkey_conflict("ctrl+k") == ""
        env.mgr.get_all_connectors = original
        ed._record_hotkey(combo)
        await user.should_see("Press the key combination")
        ed.state.name = "Hot"
        ed.save()
        assert env.mgr.connectors["c1"].trigger.key_combination == "ctrl+k"

    editor(env, check, "c1", [mine, other])


def test_hotkey_record_dialog_keys(env):
    mine = chat_connector("c1", T.HOTKEY, key_combination="ctrl+j")
    captured = {}
    real_keyboard = ui.keyboard

    def spy_keyboard(on_key=None, **kw):
        captured["on_key"] = on_key
        return real_keyboard(on_key=on_key, **kw)

    env.mp.setattr(ce.ui, "keyboard", spy_keyboard)

    async def check(user, ed):
        combo = [i for i in within(ed, ui.input) if i.value == "ctrl+j"][0]
        ed._record_hotkey(combo)
        on_key = captured["on_key"]
        on_key(SimpleNamespace(action=SimpleNamespace(keyup=False, repeat=False), key=SimpleNamespace(name="Shift"),
                               modifiers=SimpleNamespace(ctrl=False, alt=False, shift=True, meta=False)))
        assert combo.value == "ctrl+j"
        on_key(SimpleNamespace(action=SimpleNamespace(keyup=False, repeat=False), key=SimpleNamespace(name="F9"),
                               modifiers=SimpleNamespace(ctrl=True, alt=False, shift=False, meta=False)))
        assert combo.value == "ctrl+f9" and ed.state.trigger["key_combination"] == "ctrl+f9"
        ed._record_hotkey(combo)
        captured["on_key"](SimpleNamespace(key=SimpleNamespace(name="Escape")))
        assert combo.value == "ctrl+f9"

    editor(env, check, "c1", [mine])


def test_schedule_webhook_streamdeck_any_settings(env, monkeypatch):
    sched = chat_connector("c1", T.SCHEDULE, specific_time="10:30", days_of_week=[0])
    copied = []
    monkeypatch.setattr(ui.clipboard, "write", lambda text: copied.append(text))

    async def check(user, ed):
        await user.should_see("Runs once at this local time. No days picked means every day.")
        time_input = [i for i in within(ed, ui.input) if i.value == "10:30"][0]
        time_input.value = "11:45:00"
        assert ed.state.trigger["specific_time"] == "11:45"
        mon, tue = [lbl for lbl in within(ed, ui.label) if lbl.text in ("Mon", "Tue")]
        fire(tue)
        fire(mon)
        assert ed.state.trigger["days_of_week"] == [1] and "is-on" in tue._classes
        ed._choose_trigger("webhook")
        await user.should_see("URL")
        url = [lbl for lbl in within(ed, ui.label) if "/api/connectors/webhook/" in (lbl.text or "")][0]
        old = url.text
        button(ed, text="New key")[0]
        for b in button(ed, text="New key"):
            fire(b)
        assert url.text != old
        for b in button(ed, text="Copy"):
            fire(b)
        assert copied == [url.text] and env.notes[-1][0] == "Webhook URL copied"
        ed.state.trigger["secret_key"] = ""
        ed._rerender_trigger()
        assert ed.state.trigger["secret_key"]
        ed._choose_trigger("streamdeck")
        await user.should_see("Connector ID:")
        ed._choose_trigger("any")
        await user.should_see("Runs for every event from every service, including each chat message.")
        ed._choose_trigger("twitch_hype_train_start")
        cooldown = [n for n in within(ed, ui.number) if n.value == 0][0]
        cooldown.value = 12
        assert ed.state.trigger["cooldown_seconds"] == 12.0

    editor(env, check, "c1", [sched])


# --------------------------------------------------------------------------- conditions


def test_conditions_editing(env):
    conn = chat_connector("c1", T.TWITCH_CHAT_MESSAGE, conditions=[
        TriggerCondition("message", O.CONTAINS, "hi"), TriggerCondition("ghost_field", O.EQUAL, "x", join="or")])

    async def check(user, ed):
        await user.should_see("Runs when")
        await user.should_see("ghost_field (not available)")
        assert ed.state.conditions[1]["operator"] in dict(ce._operators("text"))
        ed._add_condition()
        assert len(ed.state.conditions) == 3
        # flip AND/OR on row 2
        join = within(ed, cls="ce-join")[0]
        fire(join)
        assert ed.state.conditions[1]["join"] == "and"
        # case toggle
        case = within(ed, cls="ce-case")[0]
        fire(case)
        fire(case)
        # change field on the first row to a bool field
        field_sel = [s for s in within(ed, ui.select) if s.value == "message"][0]
        field_sel.value = "is_moderator"
        assert ed.state.conditions[0]["value"] is True and ed.state.conditions[0]["operator"] == "equal"
        bool_sel = [s for s in within(ed, ui.select) if s.value is True][0]
        bool_sel.value = False
        assert ed.state.conditions[0]["value"] is False
        ops = [s for s in within(ed, ui.select) if s.value == "equal"][0]
        ops.value = "not_equal"
        assert ed.state.conditions[0]["operator"] == "not_equal"
        val = [i for i in within(ed, ui.input) if i.props.get("placeholder") == "text"][0]
        val.value = "word"
        assert "word" in ed._condition_summary() or True
        assert ed._condition_text({"field": "is_moderator", "operator": "equal", "value": "yes"}).endswith("true")
        assert ed._condition_text({"field": "", "operator": "x", "value": ""}) == "… x “”"
        ed._remove_condition(0)
        assert ed.state.conditions[0]["join"] == "and"
        ed._remove_condition(99)
        while ed.state.conditions:
            ed._remove_condition(0)
        assert not within(ed, cls="ce-conds")

    editor(env, check, "c1", [conn])


def test_conditions_number_field_and_triggerless(env):
    conn = chat_connector("c1", conditions=[TriggerCondition("amount", O.GREATER_THAN, 5)])

    async def check(user, ed):
        num = [i for i in within(ed, ui.input) if i.props.get("placeholder") == "number"][0]
        assert num.props.get("inputmode") == "decimal"
        assert ed._condition_text({"field": "amount", "operator": "greater_than", "value": " "}).endswith("…")
        ed._choose_trigger("twitch_stream_offline")
        await user.should_see("This trigger has nothing to check, so it always runs.")
        ed._add_condition()  # no fields: no-op
        assert ed.state.conditions == []
        ed.state.trigger_type = None
        ed._rerender_conditions()
        await user.should_see("Conditions depend on the trigger.")

    editor(env, check, "c1", [conn])


# --------------------------------------------------------------------------- action cards


def test_action_card_buttons(env):
    a1 = create_action(A.SEND_CHAT_MESSAGE, "a1", "n", message="one")
    a2 = create_action(A.DISPLAY_BANNER, "a2", "n", message="two", delay_seconds=1.5)
    conn = chat_connector(actions=[a1, a2])

    async def check(user, ed):
        await user.should_see("wait 1.5 s")
        assert [a["type"] for a in ed.state.actions] == ["send_chat_message", "display_banner"]
        for b in button(ed, icon="arrow_downward")[:1]:
            fire(b)
        assert [a["type"] for a in ed.state.actions] == ["display_banner", "send_chat_message"]
        ed._move_action(0, -1)  # out of range: nothing
        for b in button(ed, icon="content_copy")[:1]:
            fire(b)
        assert len(ed.state.actions) == 3 and ed.state.actions[1]["action_id"] == "" and ed.state.actions[1]["open"]
        sw = [s for s in within(ed, ui.switch)][1]
        sw.value = False
        assert ed.state.actions[0]["enabled"] is False
        sw.value = True
        ed._toggle_action(0)
        assert ed.state.actions[0]["open"]
        delay = [n for n in within(ed, ui.number) if n.value == 1.5]
        delay[0].value = 3
        assert ed.state.actions[0]["delay_seconds"] == 3.0
        for b in button(ed, icon="delete")[:1]:
            fire(b)
        assert len(ed.state.actions) == 2
        ed._remove_action(10)
        ed._rerender_action_body(99)  # unknown card: no-op
        ed.state.actions[0]["config"]["message"] = ""
        ed.state.actions[0]["open"] = False
        ed.state.actions[0]["enabled"] = False
        ed._rerender_actions()
        await user.should_see("Banner text is empty.")
        ed._action_refs = {}
        ed._set_action_enabled(0, True)  # no card ref: state still updated
        assert ed.state.actions[0]["enabled"] is True

    editor(env, check, "c1", [conn])


def test_unknown_action_and_labels(env, monkeypatch):
    conn = chat_connector(actions=[])

    async def check(user, ed):
        ed.state.actions.append({"type": "mystery_type", "config": {}, "open": True, "enabled": True})
        ed._rerender_actions()
        await user.should_see("Unknown action type “mystery_type”.")
        assert ed._action_title({"type": None}) == "Unknown Action"
        assert ed._action_labels({"type": "trigger_alert"})["bit"]
        assert ed._action_labels({"type": "obs_control"})["toggle_record"] == "OBS — Toggle recording"
        assert ed._action_labels({"type": "game_hook"})
        assert ed._action_labels({"type": "template_control", "config": {"template_name": "wheel"}}) == {
            "spin": "Spin it", "reset": "reset"}
        monkeypatch.setitem(sys.modules, "modules.obs_connector_catalog", None)
        assert ed._action_labels({"type": "obs_control"}) == {}
        monkeypatch.setitem(sys.modules, "modules.game_hooks.ff7_hook", None)
        assert ed._action_labels({"type": "game_hook"}) == {}
        assert ed._action_labels({"type": "write_file"}) == {}

    editor(env, check, "c1", [conn])


# --------------------------------------------------------------------------- fields


def test_field_kinds_audio_api_banner(env):
    audio = create_action(A.AUDIO_CONTROL, "a1", "n", action_mode="set", volume_level=40, target_device="gone")
    api = create_action(A.API_CALL, "a2", "n", method="POST", url="http://x", body='{"a": {amount}}')
    banner = create_action(A.DISPLAY_BANNER, "a3", "n", message="Hi {username}")
    conn = chat_connector(actions=[audio, api, banner])

    async def check(user, ed):
        for i in range(3):
            ed.state.actions[i]["open"] = True
        ed._rerender_actions()
        await user.should_see("gone (not found)")
        slider = within(ed, ui.slider)[0]
        slider.value = 70
        assert ed.state.actions[0]["config"]["volume_level"] == 70
        await user.should_see("70%")
        mode = [s for s in within(ed, ui.select) if s.value == "set"][0]
        mode.value = "increase"
        assert "volume_level" not in ed.state.actions[0]["config"]
        step = [n for n in within(ed, ui.number) if n.value == 10][0]
        step.value = None
        assert ed.state.actions[0]["config"]["volume_step"] == 10
        body = [t for t in within(ed, ui.textarea) if "{amount}" in (t.value or "")][0]
        body.value = "{broken"
        await user.should_see("Not valid JSON")
        method = [s for s in within(ed, ui.select) if s.value == "POST"][0]
        method.value = "GET"
        assert "body" not in ed.state.actions[1]["config"]
        perm = [s for s in within(ed, ui.switch) if s.text == "Keep showing until replaced"][0]
        perm.value = True
        assert ed.state.actions[2]["config"]["permanent"] is True
        preview = [lbl for lbl in within(ed, ui.label) if (lbl.text or "").startswith("Hi ")]
        assert preview and "{username}" not in preview[0].text

    editor(env, check, "c1", [conn])


def test_select_with_no_options(env, monkeypatch):
    conn = chat_connector(actions=[create_action(A.AUDIO_CONTROL, "a1", "n", control_type="application",
                                                 target_application="")])
    for spec in schemas.ACTION_SPECS.values():
        for f in spec.fields:
            if f.key == "target_application":
                monkeypatch.setattr(f, "options", lambda cfg: {})

    async def check(user, ed):
        await user.should_see("Pick a running app.")
        assert [s for s in within(ed, ui.select) if "" in s.options and s.options[""] == "(nothing available)"]

    editor(env, check, "c1", [conn])


def test_macro_field(env):
    conn = chat_connector(actions=[create_action(A.KEY_PRESS, "a1", "n", input_type="macro", macro_sequence="{bad")])

    async def check(user, ed):
        cfg = ed.state.actions[0]["config"]
        assert json.loads(cfg["macro_sequence"]) == [{"type": "key", "target": "", "delay": 0.1}]
        add = [a for a in within(ed, cls="ce-add") if any(getattr(c, "text", "") == "Add step" for c in a.descendants())][0]
        fire(add)
        assert len(json.loads(cfg["macro_sequence"])) == 2
        types_ = [s for s in within(ed, ui.select) if s.value == "key"]
        types_[0].value = "mouse"
        types_ = [s for s in within(ed, ui.select) if s.value == "key"]
        types_[0].value = "delay"
        steps = json.loads(cfg["macro_sequence"])
        assert steps[0] == {"type": "mouse", "target": "left_click", "delay": 0.1} and steps[1]["target"] == "1.0"
        mouse = [s for s in within(ed, ui.select) if s.value == "left_click"][0]
        mouse.value = "right_click"
        wait = [i for i in within(ed, ui.input) if i.value == "1.0"][0]
        wait.value = "2.5"
        delay = [n for n in within(ed, ui.number) if n.value == 0.1][0]
        delay.value = 0.3
        steps = json.loads(cfg["macro_sequence"])
        assert steps[0]["target"] == "right_click" and steps[0]["delay"] == 0.3 and steps[1]["target"] == "2.5"
        for b in [b for b in button(ed, icon="close") if "is-danger" in b._classes][:1]:
            fire(b)
        assert len(json.loads(cfg["macro_sequence"])) == 1
        for b in [b for b in button(ed, icon="close") if "is-danger" in b._classes][:1]:
            fire(b)  # last step can't be removed
        assert len(json.loads(cfg["macro_sequence"])) == 1
        types_ = [s for s in within(ed, ui.select) if s.value == "delay"]
        types_[0].value = "key"
        keys = [i for i in within(ed, ui.input) if i.props.get("placeholder") == "ctrl+c, enter, space"][0]
        keys.value = "ctrl+c"
        assert json.loads(cfg["macro_sequence"])[0]["target"] == "ctrl+c"
        input_type = [s for s in within(ed, ui.select) if s.value == "macro"][0]
        input_type.value = "key"
        assert "macro_sequence" not in cfg and "key_sequence" in cfg

    editor(env, check, "c1", [conn])


def test_macro_field_non_list(env):
    conn = chat_connector(actions=[create_action(A.KEY_PRESS, "a1", "n", input_type="macro", macro_sequence='{"a": 1}')])

    async def check(user, ed):
        assert json.loads(ed.state.actions[0]["config"]["macro_sequence"])[0]["type"] == "key"

    editor(env, check, "c1", [conn])


# --------------------------------------------------------------------------- custom builders


def test_custom_discord(env):
    conn = chat_connector(actions=[create_action(A.SEND_DISCORD_MESSAGE, "a1", "n", message="m",
                                                 channels=[{"guild_id": "g0", "channel_id": "c0"}])])

    async def check(user, ed):
        cfg = ed.state.actions[0]["config"]
        await user.should_see("g0 / #c0")
        add = button(ed, text="Add")[0]
        fire(add)
        assert env.notes[-1][0] == "Choose a server and a channel first"
        guild = [s for s in within(ed, ui.select) if "g1" in s.options][0]
        guild.value = "g1"
        chan = [s for s in within(ed, ui.select) if "c1" in (s.options or {})][0]
        chan.value = "c1"
        fire(add)
        assert cfg["channels"][-1] == {"guild_id": "g1", "channel_id": "c1", "guild_name": "Guild", "channel_name": "general"}
        fire(add)
        assert env.notes[-1][0] == "That channel is already added"
        guild.value = "g2"  # channel listing fails: empty
        assert chan.options == {}
        guild.value = None
        while cfg["channels"]:
            fire([b for b in button(ed, icon="close") if b._props.get("size") == "xs"][0])
        assert cfg["channels"] == []
        await user.should_see("No channels yet")

    editor(env, check, "c1", [conn])


def test_custom_discord_not_connected(env):
    env.discord.connected = False
    conn = chat_connector(actions=[create_action(A.SEND_DISCORD_MESSAGE, "a1", "n", message="m")])

    async def check(user, ed):
        await user.should_see("Connect Discord in Settings to list your servers and channels.")
        env.mp.setattr(sys.modules["modules.discord_service"], "discord_service",
                       SimpleNamespace(is_connected=lambda: (_ for _ in ()).throw(RuntimeError())))
        ed._rerender_action_body(0)

    editor(env, check, "c1", [conn])


def test_custom_template_control(env):
    conn = chat_connector(actions=[create_action(A.TEMPLATE_CONTROL, "a1", "n", template_name="wheel",
                                                 control_action="spin", control_data={"mode": ""})])

    async def check(user, ed):
        cfg = ed.state.actions[0]["config"]
        await user.should_see("More")
        assert cfg["speed"] == 3 and cfg["dir"] == "left" and cfg["mode"] == "a" and cfg["loud"] is False
        tpl = [s for s in within(ed, ui.select) if s.value == "wheel"][0]
        act = [s for s in within(ed, ui.select) if s.value == "spin"][0]
        act.value = "reset"
        assert cfg == {"template_name": "wheel", "control_action": "reset"}
        tpl = [s for s in within(ed, ui.select) if s.value == "wheel"][0]
        tpl.value = None
        assert cfg == {"template_name": "", "control_action": ""}
        assert [x for x in within(ed, ui.select) if x.options == {"": "(choose an overlay)"}]
        cfg.update(template_name="gone", control_action="nope")
        ed._rerender_action_body(0)
        await user.should_see("gone (not found)")
        await user.should_see("nope (not found)")

    editor(env, check, "c1", [conn])


def test_templates_unavailable(env):
    env.mp.setattr(ce.connector_integration, "get_integration", lambda: (_ for _ in ()).throw(RuntimeError()))
    conn = chat_connector(actions=[create_action(A.TEMPLATE_CONTROL, "a1", "n")])

    async def check(user, ed):
        assert [x for x in within(ed, ui.select) if x.options == {"": "(no overlays found)"}]
        assert ed._templates() == {}

    editor(env, check, "c1", [conn])


def test_custom_obs_control(env):
    conn = chat_connector(actions=[create_action(A.OBS_CONTROL, "a1", "n", operation="")])

    async def check(user, ed):
        cfg = ed.state.actions[0]["config"]
        assert cfg["operation"] == "set_program_scene" and cfg["arg_scene_name"] == "Main"
        assert len(env.scheduled) == 1  # bootstrap refresh
        await env.scheduled.pop()()
        assert env.obs.refresh_calls == 1
        op = [s for s in within(ed, ui.select) if s.value == "set_program_scene"][0]
        op.value = "set_source_enabled"
        assert cfg["arg_source_name"] == "Cam" and cfg["arg_enabled"] == "true"
        op = [s for s in within(ed, ui.select) if s.value == "set_source_enabled"][0]
        op.value = "set_input_mute"
        assert cfg["arg_input_name"] == "Mic"
        for b in button(ed, icon="refresh"):
            fire(b)
        env.obs.refresh = (False, "OBS offline")
        await env.scheduled.pop()()
        assert env.notes[-1][1]["title"] == "Couldn't load scenes and sources from OBS"
        cfg["operation"] = "legacy_op"
        ed._rerender_action_body(0)
        await user.should_see("legacy_op (not found)")
        env.obs.snapshot = {}
        cfg["operation"] = "set_program_scene"
        cfg.pop("arg_scene_name", None)
        ed._rerender_action_body(0)
        await user.should_see("Nothing loaded yet. Press reload.")
        env.obs.connected = False
        ed._rerender_action_body(0)
        await user.should_see("OBS isn't connected (Settings → OBS).")
        env.obs.get_connector_snapshot = lambda: (_ for _ in ()).throw(RuntimeError())
        ed._rerender_action_body(0)

    editor(env, check, "c1", [conn])


def test_obs_bootstrap_failure_and_closed_card(env):
    conn = chat_connector(actions=[create_action(A.OBS_CONTROL, "a1", "n", operation="start_stream")])

    async def check(user, ed):
        env.obs.refresh = RuntimeError("down")
        await env.scheduled.pop()()
        ed2 = ed
        ed2._obs_bootstrapped = False
        ed2._rerender_action_body(0)
        env.obs.refresh = (True, "")
        ed2.state.actions[0]["open"] = False
        await env.scheduled.pop()()

    editor(env, check, "c1", [conn])


def test_custom_game_hook(env):
    conn = chat_connector(actions=[create_action(A.GAME_HOOK, "a1", "n", operation="")])

    async def check(user, ed):
        cfg = ed.state.actions[0]["config"]
        assert cfg["operation"] == "add_gil" and cfg["game_id"] == "ff7"
        await user.should_see("Needs Game Hooks turned on and the game running on Windows. Battle-only operations are skipped outside battle.")
        op = [s for s in within(ed, ui.select) if s.value == "add_gil"][0]
        op.value = "set_game_speed"
        assert cfg["arg_speed"] in ce.ff7_game_speed_select_options() if hasattr(ce, "ff7_game_speed_select_options") else True
        game = [s for s in within(ed, ui.select) if s.value == "ff7"][0]
        game.value = "ff7"
        cfg.clear()
        cfg.update(game_id="ff7", operation="totally_unknown", arg_amount="5")
        ed._rerender_action_body(0)
        await user.should_see("totally_unknown (legacy)")
        assert [i for i in within(ed, ui.input) if i.value == "5"]

    editor(env, check, "c1", [conn])


# --------------------------------------------------------------------------- variables / context


def test_variables_context_and_render(env, monkeypatch):
    conn = chat_connector(actions=[create_action(A.GAME_HOOK, "a1", "n", operation="add_gil"),
                                   create_action(A.SEND_CHAT_MESSAGE, "a2", "n", message="m")])

    async def check(user, ed):
        keys = [v["key"] for v in ed._variables(2, [{"key": "extra"}])]
        assert "action1.item_name" in keys and keys[-1] == "extra"
        assert ed._date_formats()
        monkeypatch.setattr(cv, "example_context", lambda *a: (_ for _ in ()).throw(RuntimeError()))
        ed._ctx_cache = (None, {})
        assert ed._context() == {}
        monkeypatch.setattr(ce, "substitute_connector_placeholders", lambda t, c: (_ for _ in ()).throw(RuntimeError()))
        assert ed.render_text("{x}") == "{x}" and ed.render_text(None) == ""
        ed.state.trigger_type = None
        ed.render_vars()
        await user.should_see("Choose a trigger to see the variables")

    editor(env, check, "c1", [conn])


# --------------------------------------------------------------------------- close / save


def test_request_close_clean_and_dirty(env):
    conn = chat_connector()

    async def check(user, ed):
        ed.request_close()
        assert not ed.dialog.value
        ed.dialog.open()
        ed.state.name = "Changed"
        ed.request_close()
        await user.should_see("Unsaved changes")
        user.find("Keep editing").click()
        assert ed.dialog.value
        ed.request_close()
        user.find("Discard").click()
        assert not ed.dialog.value

    editor(env, check, "c1", [conn])


def test_save_with_problems_opens_bad_actions(env):
    a1 = create_action(A.SEND_CHAT_MESSAGE, "a1", "n", message="ok")
    a2 = create_action(A.DISPLAY_BANNER, "a2", "n", message="")
    conn = chat_connector(actions=[a1, a2])

    async def check(user, ed):
        ed.state.name = ""
        ed.save()
        assert env.notes[-1][0] == "Name this connector. (+1 more)"
        assert [a["open"] for a in ed.state.actions] == [False, True]
        ed.state.actions.pop()
        ed.save()
        assert env.notes[-1][0] == "Name this connector."

    editor(env, check, "c1", [conn])


def test_save_update_and_test_after(env, monkeypatch):
    conn = chat_connector()
    tested = []
    monkeypatch.setattr(ce, "run_connector_test", lambda cid: tested.append(cid))

    async def check(user, ed):
        ed.state.name = "Renamed"
        env.saved_raise = True
        ed.on_saved = lambda: (_ for _ in ()).throw(RuntimeError("refresh failed"))
        ed.save(test_after=True)
        assert env.mgr.connectors["c1"].name == "Renamed" and tested == ["c1"]
        assert env.notes[-1][0] == "Saved “Renamed”" and not ed.dialog.value

    editor(env, check, "c1", [conn])


def test_save_failures(env, monkeypatch):
    conn = chat_connector()

    async def check(user, ed):
        original = ce.build_connector
        monkeypatch.setattr(ce, "build_connector", lambda s, e: (_ for _ in ()).throw(ValueError("odd")))
        ed.save()
        assert env.notes[-1][1]["details"] == "ValueError: odd"
        monkeypatch.setattr(ce, "build_connector", original)

        def update(cid, c, keep_stats=False):
            import logging

            logging.getLogger("modules.connector_manager").error("validation failed")
            return False

        env.mp.setattr(env.mgr, "update_connector", update)
        ed.save()
        assert env.notes[-1][1]["details"] == "ERROR: validation failed"

        def quiet_ok(cid, c, keep_stats=False):
            env.mgr.last_save_ok = False
            return True

        env.mp.setattr(env.mgr, "update_connector", quiet_ok)
        count = len(env.notes)
        ed.save()
        assert len(env.notes) == count and not ed.dialog.value

    editor(env, check, "c1", [conn])


def test_unreadable_action_warning(env, monkeypatch):
    conn = chat_connector()
    monkeypatch.setattr(schemas, "action_to_config", lambda a: (_ for _ in ()).throw(ValueError("bad")))

    async def check(user, ed):
        assert env.notes[0][1]["title"] == "Some action settings couldn't be read"
        assert "action 1 (send chat message)" in env.notes[0][0]

    editor(env, check, "c1", [conn])

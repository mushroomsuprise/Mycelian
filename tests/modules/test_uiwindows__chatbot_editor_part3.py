# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/chatbot_editor.py (part 3): the lookup step, reply/outputs, the variables
panel and the custom variable builder. NiceGUI user simulation; Twitch, Discord and storage are fakes."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402

from _chatbot_editor_fakes import (  # noqa: E402,F401  (ed_env: fixture)
    buttons, click, cmd, ed_env, evt, htmls, inputs, label_click, set_value,
)
from modules import chatbot_custom_variables as ccv  # noqa: E402
from modules import chatbot_lookups as lk  # noqa: E402
from modules import chatbot_variables as cv  # noqa: E402
from modules import discord_service  # noqa: E402
from modules.uiwindows import chatbot_editor as ce  # noqa: E402
from ui_sim import run_ui  # noqa: E402


def run_editor(kind, item_id, check):
    holder = {}

    def build():
        holder["ed"] = ce.open_chatbot_editor(kind, item_id, lambda: None)

    async def wrapped(user):
        await check(user, holder["ed"])

    run_ui(build, wrapped)
    return holder["ed"]


def sel_with(user, key):
    return [s for s in user.find(kind=ui.select).elements if key in s.options][0]


# --------------------------------------------------------------------------- lookup picker


def test_lookup_picker_shared_warning_search_and_choose(ed_env, monkeypatch):
    monkeypatch.setattr(lk, "lookup_available", lambda entry: (False, "Connect Twitch."))

    async def check(user, ed):
        label_click(user, "Look up Twitch info")
        assert ed._picking_lookup
        assert "Connect Twitch. You can still set these up" in htmls(user)
        search = [i for i in inputs(user) if i.props.get("placeholder") == "Search lookups"][0]
        set_value(user, search, "zzzz")
        await user.should_see("No lookups match.")
        set_value(user, search, "")
        label_click(user, ce.lookup_info("follow_age")["label"])
        assert ed.state.lookup == {"id": "follow_age", "target": {"from": "chatter"}}
        assert not ed._picking_lookup

    run_editor("command", None, check)


def test_lookup_picker_per_option_reasons_and_cancel(ed_env, monkeypatch):
    monkeypatch.setattr(lk, "lookup_available", lambda e: (e["id"] != "uptime", "" if e["id"] != "uptime" else ""))

    async def check(user, ed):
        with user.client:
            ed._picking_lookup = True
            ed._rerender(ed.lookup_box, ed._build_lookup)
        await user.should_see("Not available")
        click(user, buttons(user, text="Cancel")[-1])
        assert not ed._picking_lookup

    run_editor("command", None, check)


def test_lookup_picker_without_catalog(ed_env, monkeypatch):
    monkeypatch.setattr(ce, "lookups_catalog", lambda: [])

    async def check(user, ed):
        with user.client:
            ed._picking_lookup = True
            ed._rerender(ed.lookup_box, ed._build_lookup)
        await user.should_see("Twitch lookups aren't available right now.")
        label_click(user, "Use any Twitch API request (advanced)")
        assert ed.state.lookup["id"] == "advanced"

    run_editor("command", None, check)


def test_choose_lookup_targets_by_kind(ed_env):
    async def check(user, ed):
        with user.client:
            ed.state.inputs = [{"name": "who", "position": 1, "default": ""}]
            ed._choose_lookup("follow_age")
        assert ed.state.lookup["target"] == {"from": "input", "input": "who"}
        with user.client:
            ed._choose_lookup("set_title")
        assert ed.state.lookup == {"id": "set_title", "text": "{command_message}"}
        with user.client:
            ed._choose_lookup("uptime")
        assert ed.state.lookup == {"id": "uptime"}
        with user.client:
            ed._choose_lookup("advanced")
            ed.state.lookup["path"] = "/users"
            ed._choose_lookup("advanced")  # keeps the existing request
        assert ed.state.lookup["path"] == "/users"

    run_editor("command", None, check)

    async def check_event(user, ed):
        with user.client:
            ed._choose_event("raid")
            ed._choose_lookup("follow_age")
        assert ed.state.lookup["target"] == {"from": "event_user"}
        assert ed._target_options() == {"event_user": "The viewer from the event", "text": "A name I type…"}
        with user.client:
            ed._choose_lookup("set_title")
        assert ed.state.lookup == {"id": "set_title", "text": ""}  # events have no {command_message}

    run_editor("event", None, check_event)


def test_choose_lookup_text_arg_without_default(ed_env, monkeypatch):
    monkeypatch.setattr(ce, "lookup_info", lambda lid: {"id": lid, "text_arg": {"label": "T"}})

    async def check(user, ed):
        with user.client:
            ed._choose_lookup("x")
        assert ed.state.lookup == {"id": "x", "text": "{command_message}"}

    run_editor("command", None, check)


# --------------------------------------------------------------------------- lookup card


def test_lookup_card_target_and_fallback(ed_env, monkeypatch):
    monkeypatch.setattr(lk, "lookup_available", lambda e: (True, ""))
    ed_env.mgr.commands = {"c1": cmd("c1", argument_mappings={"who": 1}, lookup={"id": "follow_age"},
                                     lookup_fallback="nope")}

    async def check(user, ed):
        assert "Uses your Twitch login" in htmls(user)
        await user.should_see("If they don't follow")
        target = sel_with(user, "chatter")
        assert target.value == "chatter" and "input:who" in target.options
        set_value(user, target, "input:who")
        assert ed.state.lookup["target"] == {"from": "input", "input": "who"}
        set_value(user, sel_with(user, "chatter"), "text")
        assert ed.state.lookup["target"] == {"from": "text", "text": ""}
        name = [i for i in inputs(user) if i.props.get("placeholder", "").startswith("friendlystreamer")][0]
        set_value(user, name, "otherstreamer")
        assert ed.state.lookup["target"]["text"] == "otherstreamer"
        set_value(user, sel_with(user, "chatter"), None)
        assert ed.state.lookup["target"] == {"from": "chatter"}
        fb = [i for i in inputs(user) if i.value == "nope"][0]
        set_value(user, fb, "fallback text")
        assert ed.state.lookup_fallback == "fallback text"
        assert "<code>{" in htmls(user)
        click(user, buttons(user, text="Change")[0])
        assert ed._picking_lookup

    run_editor("command", "c1", check)


def test_lookup_card_missing_input_target_and_unavailable(ed_env, monkeypatch):
    monkeypatch.setattr(lk, "lookup_available", lambda e: (False, ""))
    ed_env.mgr.commands = {"c1": cmd("c1", lookup={"id": "follow_age", "target": {"from": "input", "input": "gone"}})}

    async def check(user, ed):
        assert "Not available right now" in htmls(user)
        sel = sel_with(user, "chatter")
        assert sel.value == "input:gone" and sel.options["input:gone"] == "{gone}"
        remove = [b for b in buttons(user, icon="close") if "is-danger" in b.classes][0]
        click(user, remove)
        assert ed.state.lookup is None and ed.state.lookup_fallback == ""

    run_editor("command", "c1", check)


def test_lookup_card_options_and_text_arg(ed_env):
    ed_env.mgr.commands = {"c1": cmd("c1", lookup={"id": "top_cheerer"}), "c2": cmd("c2", "t", lookup={"id": "set_title", "text": "x"})}

    async def check(user, ed):
        period = [s for s in user.find(kind=ui.select).elements if s.value == ed.state.lookup.get("period")][0]
        other = [k for k in period.options if k != period.value][0]
        set_value(user, period, other)
        assert ed.state.lookup["period"] == other
        await user.should_see("If it fails")

    run_editor("command", "c1", check)

    async def check_text(user, ed):
        text = [i for i in inputs(user) if i.value == "x"][0]
        set_value(user, text, "New title")
        assert ed.state.lookup["text"] == "New title"

    run_editor("command", "c2", check_text)


def test_lookup_card_list_and_empty_choices(ed_env, monkeypatch):
    entry = {"id": "fake", "label": "Fake", "hint": "A hint", "target": "channel",
             "options": [{"key": "mode", "choices": ["a_b", "c"]}, {"key": "none", "choices": {}}],
             "variables": [{"key": "fake_value", "label": "Value"}]}
    monkeypatch.setattr(ce, "lookup_info", lambda lid: entry)
    monkeypatch.setattr(cv, "lookup_variables", lambda lookup: (_ for _ in ()).throw(RuntimeError("x")))
    ed_env.mgr.commands = {"c1": cmd("c1", lookup={"id": "fake", "mode": "zzz", "target": {"from": "text", "text": "t"}})}

    async def check(user, ed):
        mode = sel_with(user, "a_b")
        assert mode.options == {"a_b": "A b", "c": "C"} and ed.state.lookup["mode"] == "a_b"
        await user.should_see("Channel")
        await user.should_see("A hint")
        assert "{fake_value}" in htmls(user)
        assert [i for i in inputs(user) if i.value == "t"]

    run_editor("command", "c1", check)


def test_lookup_vars_fallbacks(ed_env, monkeypatch):
    async def check(user, ed):
        ed.state.lookup = {"id": "uptime"}
        assert ed._lookup_vars()
        monkeypatch.setattr(ce, "_cv", lambda: None)
        assert [v["key"] for v in ed._lookup_vars()] == [v["key"] for v in ce.lookup_info("uptime")["variables"]]
        ed.state.lookup = None
        assert ed._lookup_vars() == []

    run_editor("command", None, check)


# --------------------------------------------------------------------------- advanced request


def test_advanced_request_card(ed_env):
    ed_env.mgr.commands = {"c1": cmd("c1", lookup={"id": "advanced", "method": "get", "path": "/users",
                                                   "params": {"login": "x"}})}

    async def check(user, ed):
        await user.should_see("Twitch API request")
        path = [i for i in inputs(user) if i.value == "/users"][0]
        set_value(user, path, " /streams ")
        assert ed.state.lookup["path"] == "/streams"
        key = [i for i in inputs(user) if i.value == "login"][0]
        set_value(user, key, "user_login")
        val = [i for i in inputs(user) if i.value == "x"][0]
        set_value(user, val, "{chatter_login}")
        assert ed.state.lookup["params"] == {"user_login": "{chatter_login}"}
        label_click(user, "+ Add parameter")
        assert ed.state.lookup["params"] == {"user_login": "{chatter_login}"}  # blank rows are not stored
        method = sel_with(user, "PATCH")
        set_value(user, method, "POST")
        body = user.find(kind=ui.textarea).elements
        body = [b for b in body if b.props.get("placeholder", "").startswith('{"title"')][0]
        set_value(user, body, '{"a": 1}')
        assert ed.state.lookup["body"] == '{"a": 1}' and ed.state.lookup["method"] == "POST"
        removes = [b for b in buttons(user, icon="close") if "is-danger" in b.classes]
        click(user, removes[0])  # first param row
        assert ed.state.lookup["params"] == {}
        fb = [i for i in inputs(user) if i.props.get("placeholder") == "Leave empty to skip the reply"][0]
        set_value(user, fb, "failed")
        assert ed.state.lookup_fallback == "failed"
        click(user, buttons(user, text="Change")[0])
        assert ed._picking_lookup

    run_editor("command", "c1", check)


def test_advanced_body_as_dict(ed_env):
    ed_env.mgr.commands = {"c1": cmd("c1", lookup={"id": "advanced", "method": "PUT", "path": "/x", "body": {"k": 1}})}

    async def check(user, ed):
        body = [b for b in user.find(kind=ui.textarea).elements if "ui-monospace" in str(b.style)]
        assert body and '"k": 1' in body[0].value
        remove = [b for b in buttons(user, icon="close") if "is-danger" in b.classes][-1]
        click(user, remove)
        assert ed.state.lookup is None

    run_editor("command", "c1", check)


# --------------------------------------------------------------------------- reply / outputs


def test_reply_preview_long_text_and_unknown_tokens(ed_env):
    async def check(user, ed):
        area = [t for t in user.find(kind=ui.textarea).elements][0]
        set_value(user, area, "Hi {username} " + "x" * 520)
        limit = [e for e in user.find(kind=ui.label).elements if "ce-f-error" in e.classes][0]
        assert "Twitch allows 500 per message" in limit.text
        set_value(user, area, "Hi {user} {foo} {stats.followers} {command_word_3} {api_x} {custom_y} {username}")
        unknown = [e for e in user.find(kind=ui.html).elements if "ce-unknown" in e.classes][0]
        assert "<code>{user}</code> (did you mean <code>{username}</code>?)" in unknown.content
        assert "<code>{foo}</code>" in unknown.content and "stats" not in unknown.content
        set_value(user, area, "plain")
        assert unknown.content == ""

    run_editor("command", None, check)


def test_unknown_tokens_rules(ed_env, monkeypatch):
    async def check(user, ed):
        monkeypatch.setattr(ed, "_variables", lambda *a: [{"key": "word_N"}, {"key": "username"}, {"key": "viewer_count"}])
        assert ed._unknown_tokens("") == []
        assert ed._unknown_tokens("{word_3} {streamer} {viewers} {name:%d}") == [("streamer", ""), ("viewers", "viewer_count"),
                                                                              ("name", "username")]

    run_editor("command", None, check)


def test_output_pills_and_banner(ed_env):
    async def check(user, ed):
        label_click(user, "YouTube")
        assert ed.state.reply_targets == ["twitch", "youtube"]
        label_click(user, "Twitch")
        assert ed.state.reply_targets == ["youtube"]
        label_click(user, "YouTube")
        assert ed.state.reply_targets == []
        banner = sorted(user.find(kind=ui.switch).elements, key=lambda e: e.id)[-1]
        set_value(user, banner, True)
        assert ed.state.output_mode == "banner"
        assert not [e for e in user.find(kind=ui.label).elements if e.text == "Send to"]
        banner = sorted(user.find(kind=ui.switch).elements, key=lambda e: e.id)[-1]
        set_value(user, banner, False)
        assert ed.state.output_mode == "chat" and ed.state.reply_targets == ["twitch"]

    run_editor("command", None, check)


@pytest.fixture
def discord(monkeypatch):
    state = SimpleNamespace(connected=True, guilds=[{"id": "g1", "name": "Guild"}, {"id": "g2"}],
                            channels={"g1": [{"id": "ch1", "name": "general"}, {"id": "ch2"}]})

    def is_connected():
        if isinstance(state.connected, Exception):
            raise state.connected
        return state.connected

    monkeypatch.setattr(discord_service.discord_service, "is_connected", is_connected)
    monkeypatch.setattr(discord_service, "list_guilds", lambda: state.guilds)

    def channels(gid):
        if gid not in state.channels:
            raise RuntimeError("no access")
        return state.channels[gid]

    monkeypatch.setattr(discord_service, "list_text_channels", channels)
    return state


def test_discord_not_connected(ed_env, discord):
    discord.connected = False

    async def check(user, ed):
        await user.should_see("Connect Discord in Settings to list your servers.")

    run_editor("command", None, check)
    discord.connected = RuntimeError("bot crashed")
    run_editor("command", None, check)


def test_discord_add_duplicate_and_remove(ed_env, discord):
    async def check(user, ed):
        guild = sel_with(user, "g1")
        add = buttons(user, text="Add channel")[0]
        click(user, add)
        assert ed_env.notes.messages == ["Choose a server and a channel first"]
        set_value(user, guild, "g1")
        channel = sel_with(user, "ch1")
        assert channel.options == {"ch1": "#general", "ch2": "#ch2"}
        set_value(user, channel, "ch1")
        click(user, add)
        assert ed.state.discord_channels == [{"guild_id": "g1", "channel_id": "ch1", "guild_name": "Guild",
                                              "channel_name": "general"}]
        await user.should_see("Guild / #general")
        guild = sel_with(user, "g1")
        set_value(user, guild, "g1")
        set_value(user, sel_with(user, "ch1"), "ch1")
        click(user, buttons(user, text="Add channel")[0])
        assert ed_env.notes.messages[-1] == "That channel is already added"
        set_value(user, sel_with(user, "g2"), "g2")  # listing channels fails: empty choices
        chip = [e for e in user.find(kind=ui.label).elements if e.text == "Guild / #general"][0]
        close = [b for b in chip.parent_slot.parent.default_slot.children if isinstance(b, ui.button)][0]
        click(user, close)
        assert ed.state.discord_channels == []

    run_editor("command", None, check)


def test_discord_entry_without_names(ed_env, discord):
    ed_env.mgr.commands = {"c1": cmd("c1", discord_channels=[{"guild_id": "g9", "channel_id": "c9"}])}

    async def check(user, ed):
        await user.should_see("g9 / #c9")

    run_editor("command", "c1", check)


# --------------------------------------------------------------------------- render helpers


def test_render_text_variants(ed_env, monkeypatch):
    async def check(user, ed):
        assert ed.render_text(None) == ""
        assert ed.render_text("Hi {username}") == "Hi MossyMika"

        def boom(*a, **k):
            raise RuntimeError("preview broke")

        monkeypatch.setattr(cv, "render_preview", boom)
        assert ed.render_text("raw") == "raw"
        monkeypatch.setattr(cv, "render_preview", None)
        assert ed.render_text("raw2") == "raw2"
        monkeypatch.setattr(ce, "_cv", lambda: None)
        assert ed.render_text("raw3") == "raw3"

    run_editor("command", None, check)


def test_preview_item_falls_back_to_existing(ed_env, monkeypatch):
    ed_env.mgr.commands = {"c1": cmd("c1")}

    async def check(user, ed):
        monkeypatch.setattr(ce, "build_item", lambda s, e=None: (_ for _ in ()).throw(ValueError("x")))
        assert ed._preview_item() is ed.existing

    run_editor("command", "c1", check)


def test_variables_catalog_variants(ed_env, monkeypatch):
    async def check(user, ed):
        keys = [v["key"] for v in ed._variables(extra=[{"key": "extra_one"}, {"key": ""}, {"key": "username"}])]
        assert "extra_one" in keys and len(keys) == len(set(keys))
        monkeypatch.setattr(cv, "command_variables", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
        assert [v["key"] for v in ed._variables(extra=[{"key": "e"}])] == ["e"]
        monkeypatch.setattr(ce, "_cv", lambda: None)
        assert ed._variables(extra=[{"key": "z"}]) == [
            {"key": "username", "label": "Who triggered it", "example": "MossyMika", "group": "Viewer"}, {"key": "z"}]

    run_editor("command", None, check)


def test_event_variables_include_lookup(ed_env, monkeypatch):
    ed_env.mgr.events = {"e1": evt("e1", "raid", lookup={"id": "follow_age"})}
    seen = []
    real = cv.lookup_variables
    monkeypatch.setattr(cv, "lookup_variables", lambda lookup: seen.append(lookup) or real(lookup))

    async def check(user, ed):
        keys = [v["key"] for v in ed._variables()]
        assert "viewer_count" in keys and seen
        ed.state.event_type = None
        assert "viewer_count" not in [v["key"] for v in ed._variables()]

    run_editor("event", "e1", check)


def test_stale_live_updaters_are_dropped(ed_env):
    async def check(user, ed):
        count = len(ed._live)
        with user.client:
            ed.flow.clear()
            ed.refresh_live()
        assert ed._live == [] and count >= 2

    run_editor("command", None, check)


def test_vars_panel_custom_rows_and_footer(ed_env, monkeypatch):
    ed_env.defs["custom_points"] = {"kind": "text", "params": {"template": "5"}, "expression": "5", "description": "Points"}
    opened = []

    async def check(user, ed):
        monkeypatch.setattr(ed, "open_custom_variable", lambda name=None: opened.append(name))
        with user.client:
            ed.render_vars()
        label_click(user, "New custom variable")
        edit = [b for b in buttons(user, icon="edit")][0]
        from nicegui.testing.user_interaction import UserInteraction

        UserInteraction(user, {edit}, None).trigger("click.stop")
        assert opened == [None, "custom_points"]

    run_editor("command", None, check)


def test_open_custom_variable_wires_callbacks(ed_env, monkeypatch):
    calls = []

    def fake_builder(name, on_saved, render, variables):
        calls.append(name)
        on_saved()
        assert render("x") == "x" and variables()

    monkeypatch.setattr(ce, "open_custom_variable_builder", fake_builder)

    async def check(user, ed):
        with user.client:
            ed.open_custom_variable("custom_x")
        assert calls == ["custom_x"]

    run_editor("command", None, check)


# --------------------------------------------------------------------------- custom variable builder


@pytest.fixture
def cvb(ed_env, monkeypatch):
    st = SimpleNamespace(saved=[], deleted=[], save_ok=True, delete_ok=True, compile_error=None, preview=None)

    def compile_(kind, params):
        if st.compile_error:
            raise st.compile_error
        return f"{kind}:{sorted(params.items())}"

    def preview(definition, ctx):
        if isinstance(st.preview, Exception):
            raise st.preview
        return st.preview if st.preview is not None else "42"

    def save(name, definition):
        st.saved.append((name, definition))
        return st.save_ok

    def delete(name):
        st.deleted.append(name)
        return st.delete_ok

    monkeypatch.setattr(ccv, "compile_custom_variable", compile_)
    monkeypatch.setattr(ccv, "preview_custom_variable", preview)
    monkeypatch.setattr(ccv, "save_custom_definition", save)
    monkeypatch.setattr(ccv, "delete_custom_definition", delete)
    st.defs = ed_env.defs
    st.notes = ed_env.notes
    return st


VARS = [{"key": "username", "label": "User", "group": "Viewer"},
        {"key": "followed_since", "label": "Followed", "date": True, "group": "Lookup"},
        {"key": "time", "label": "Now", "date": True},
        {"key": "custom_self", "group": "Custom"}]


def run_builder(name, check, variables=VARS):
    done = []

    def build():
        ce.open_custom_variable_builder(name, on_saved=lambda: done.append(1), render=lambda t: f"<{t}>",
                                        variables=lambda: list(variables))

    run_ui(build, check)
    return done


def expr(user):
    return [e for e in user.find(kind=ui.html).elements if "cv-expr" in e.classes][0].content


def result(user):
    return [e for e in user.find(kind=ui.element).elements if "cv-result" in e.classes][0]


def test_builder_new_math_variable_and_save(cvb):
    async def check(user):
        await user.should_see("New custom variable")
        assert "Right now this gives <b>42</b>" in htmls(user)
        name = [i for i in inputs(user) if i.props.get("placeholder") == "hours_live"][0]
        user.find(kind=ui.button, content="Create variable").click()
        await user.should_see("Use a name with lowercase letters, digits and _ (starting with a letter).")
        set_value(user, name, "Hours Live")
        op = sel_with(user, "random")
        set_value(user, op, "random")
        await user.should_see("Between")
        left = [i for i in inputs(user) if i.props.get("placeholder") == "1"][0]
        set_value(user, left, "5")
        dec = sel_with(user, 2)
        set_value(user, dec, 2)
        assert "decimals" in expr(user)
        user.find(kind=ui.button, content="Create variable").click()

    done = run_builder(None, check)
    name, definition = cvb.saved[0]
    assert name == "custom_hours_live" and definition["kind"] == "math"
    assert definition["params"]["op"] == "random" and definition["params"]["left"] == "5"
    assert cvb.notes.messages == ["Saved {custom_hours_live}"] and done == [1]


def test_builder_name_taken_and_save_failure(cvb):
    cvb.defs["custom_taken"] = {"kind": "text", "params": {}}

    async def check(user):
        name = [i for i in inputs(user) if i.props.get("placeholder") == "hours_live"][0]
        set_value(user, name, "taken")
        user.find(kind=ui.button, content="Create variable").click()
        await user.should_see("{custom_taken} already exists.")
        set_value(user, name, "fresh")
        cvb.save_ok = False
        user.find(kind=ui.button, content="Create variable").click()
        await user.should_see("Couldn't save the variable. Check Settings → Database and try again.")

    assert run_builder(None, check) == []


def kind_tile(user, index):
    return sorted((e for e in user.find(kind=ui.element).elements if "cv-kind" in e.classes), key=lambda e: e.id)[index]


@pytest.mark.parametrize("index,kind,needle", [(1, "if", "Else"), (3, "text", "Text"), (2, "age", "Show as")])
def test_builder_kinds(cvb, index, kind, needle):
    async def check(user):
        click(user, kind_tile(user, index))
        await user.should_see(needle)
        assert expr(user).startswith(f"Expression: <b>{kind}:")

    run_builder(None, check)


def test_builder_age_dates(cvb):
    async def check(user):
        click(user, kind_tile(user, 2))
        since = sel_with(user, "__typed__")
        keys = list(since.options)
        assert keys[0] == "{followed_since}" and "{time}" not in keys
        assert since.value == "{followed_since}" and "followed_since" in expr(user)
        assert "·" in since.options["{account_created}"]  # dates from Twitch lookups are listed too
        style = sel_with(user, "hours")
        set_value(user, style, "days")
        assert "days" in expr(user)

    run_builder(None, check)


def test_builder_age_typed_date(cvb):
    cvb.defs["custom_bday"] = {"kind": "age", "params": {"date": "2020-01-02", "style": "years"}}

    async def check(user):
        since = sel_with(user, "__typed__")
        assert since.value == "__typed__"
        date = [i for i in inputs(user) if i.props.get("type") == "date"][0]
        set_value(user, date, "2021-03-04")
        assert "2021-03-04" in expr(user)
        set_value(user, sel_with(user, "__typed__"), "{followed_since}")
        assert "followed_since" in expr(user)

    run_builder("bday", check)


def test_builder_age_choose_typed_date(cvb):
    async def check(user):
        click(user, kind_tile(user, 2))
        set_value(user, sel_with(user, "__typed__"), "__typed__")
        assert sel_with(user, "__typed__").value == "__typed__"
        assert [i for i in inputs(user) if i.props.get("type") == "date"]

    run_builder(None, check)


def test_builder_edit_existing_expression_and_delete(cvb):
    cvb.defs["custom_old"] = {"kind": "expression", "expression": "math(1+1)", "description": "d"}

    async def check(user):
        await user.should_see("Edit custom variable")
        await user.should_see("Expression")
        name = [i for i in inputs(user) if i.value == "old"][0]
        assert "readonly" in name.props
        assert [i for i in inputs(user) if i.value == "math(1+1)"]
        cvb.delete_ok = False
        user.find(kind=ui.button, content="Delete").click()
        await user.should_see("Couldn't delete the variable. Check Settings → Database and try again.")
        cvb.delete_ok = True
        user.find(kind=ui.button, content="Delete").click()

    done = run_builder("old", check)
    assert cvb.deleted == ["custom_old", "custom_old"] and done == [1]
    assert cvb.notes.messages == ["Deleted {custom_old}"]


def test_builder_save_existing(cvb):
    cvb.defs["custom_pts"] = {"kind": "text", "params": {"template": "{username}"}}

    async def check(user):
        await user.should_see("Save variable")
        user.find(kind=ui.button, content="Save variable").click()

    done = run_builder("custom_pts", check)
    assert cvb.saved[0][0] == "custom_pts" and done == [1]


def test_builder_preview_errors(cvb):
    async def check(user):
        cvb.preview = "Error: division by zero"
        set_value(user, [i for i in inputs(user) if i.props.get("placeholder") == "3600"][0], "0")
        assert "Can't work this out yet: <b>division by zero</b>" in htmls(user)
        assert "is-error" in result(user).classes
        cvb.preview = RuntimeError("bad expr")
        set_value(user, [i for i in inputs(user) if i.props.get("placeholder") == "3600"][0], "1")
        assert "Can't work this out yet: <b>bad expr</b>" in htmls(user)
        cvb.preview = ""
        cvb.compile_error = ValueError("cannot compile")
        set_value(user, [i for i in inputs(user) if i.props.get("placeholder") == "3600"][0], "2")
        assert "⚠ cannot compile" in expr(user) and "(empty)" in htmls(user)

    run_builder(None, check)


def test_builder_without_module_helpers(cvb, monkeypatch):
    monkeypatch.setattr(ce, "_cvars", lambda: None)

    async def check(user):
        assert "Expression: <b>math({stats.session.session_duration} / 3600)</b>" not in expr(user)
        assert "math(" in expr(user)
        assert "&lt;math(" in htmls(user)  # rendered with the editor's render function
        name = [i for i in inputs(user) if i.props.get("placeholder") == "hours_live"][0]
        set_value(user, name, "abc")
        user.find(kind=ui.button, content="Create variable").click()
        await user.should_see("Couldn't save the variable. Check Settings → Database and try again.")

    run_builder(None, check)


def test_builder_load_failure(cvb, monkeypatch):
    monkeypatch.setattr(ccv, "load_custom_definitions", lambda: (_ for _ in ()).throw(RuntimeError("db")))

    async def check(user):
        await user.should_see("New custom variable")

    run_builder("x", check)


def test_builder_twitch_lookup_source(cvb):
    async def check(user):
        click(user, kind_tile(user, 4))
        await user.should_see("Get it from")
        look = sel_with(user, "follow_age")
        assert "set_title" not in look.options  # "Do something" lookups are left out
        set_value(user, look, "follow_age")
        about = sel_with(user, "chatter")
        set_value(user, about, "text")
        name = [i for i in inputs(user) if i.props.get("placeholder", "").startswith("friendlystreamer")][0]
        set_value(user, name, "someone")
        set_value(user, sel_with(user, "follow_age"), "top_cheerer")
        await user.should_see("Use")
        fb = [i for i in inputs(user) if i.props.get("placeholder") == "Leave empty for nothing"][0]
        set_value(user, fb, "n/a")
        assert "top_cheerer" in expr(user) and "n/a" in expr(user)

    run_builder(None, check)


def test_builder_twitch_request_source(cvb):
    async def check(user):
        click(user, kind_tile(user, 4))
        label_click(user, "An API request (advanced)")
        path = [i for i in inputs(user) if i.props.get("placeholder") == "/users"][0]
        set_value(user, path, " /users ")
        label_click(user, "+ Add parameter")
        key = [i for i in inputs(user) if i.props.get("placeholder") == "parameter"][0]
        set_value(user, key, "login")
        val = [i for i in inputs(user) if i.props.get("placeholder") == "value or {variable}"][0]
        set_value(user, val, "{chatter_login}")
        field = [i for i in inputs(user) if i.props.get("placeholder", "").startswith("total")][0]
        set_value(user, field, "created_at")
        e = expr(user)
        assert "&#x27;/users&#x27;" in e and "{chatter_login}" in e and "created_at" in e
        remove = [b for b in buttons(user, icon="close") if "is-danger" in b.classes][0]
        click(user, remove)
        assert "chatter_login" not in expr(user)

    run_builder(None, check)


def test_builder_twitch_without_lookups(cvb, monkeypatch):
    monkeypatch.setattr(ce, "lookups_catalog", lambda: [])

    async def check(user):
        click(user, kind_tile(user, 4))
        await user.should_see("Twitch lookups aren't available right now.")

    run_builder(None, check)


def test_builder_close_and_cancel(cvb):
    async def check(user):
        click(user, buttons(user, icon="close")[0])
        user.find(kind=ui.button, content="Cancel").click()
        click(user, kind_tile(user, 0))  # same kind: nothing changes
        assert "math:" in expr(user)

    assert run_builder(None, check) == []

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/chatbot_cards.py: tag / filter / trigger helpers and the
Commands / Events ItemsPanel (toolbar, search, status chips, cards, card actions, errors)."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402
from nicegui.testing.user_interaction import UserInteraction  # noqa: E402

from modules import chatbot_lookups, chatbot_variables  # noqa: E402
from modules.chatbot_core import ChatCommand, ChatEvent, CommandType, EventType  # noqa: E402
from modules.uiwindows import chatbot_cards as cards  # noqa: E402
from modules.uiwindows import connectors  # noqa: E402
from ui_sim import run_ui  # noqa: E402


def ns(**kw):
    return SimpleNamespace(**kw)


# --------------------------------------------------------------------------- small helpers


def test_icon_brand_esc_tag():
    assert cards._icon("x") == '<span class="cx-icon">x</span>'
    assert cards._brand("twitch").startswith('<span class="cx-brand is-twitch"><svg')
    assert cards._brand("timer") == cards._icon("timer")
    assert cards._esc(None) == "" and cards._esc("<b>") == "&lt;b&gt;"
    assert cards._tag("is-mod", "G", "a<b") == '<span class="cx-tg is-mod" title="a&lt;b">G<span>a&lt;b</span></span>'


def test_value_unwraps_enums():
    assert cards._value(ns(t=CommandType.COUNTER), "t") == "counter"
    assert cards._value(ns(t="raw"), "t") == "raw"
    assert cards._value(ns(), "t", "d") == "d"


def test_unfilled_tokens_dedupes():
    assert cards.unfilled_tokens("{a} {a} {b.c:x} {b}", "{a} {b.c:x}") == ["{a}", "{b.c:x}"]
    assert cards.unfilled_tokens(None, None) == []


def test_relative(monkeypatch):
    assert cards._relative(time.time()) == "just now"
    assert cards._relative(0) == ""

    def boom(ts):
        raise RuntimeError("x")

    monkeypatch.setattr(connectors, "_relative_time", boom)
    assert cards._relative(5) == ""


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, ""), (None, ""), (-5, ""), (3600, "Every 1 hour"), (7200, "Every 2 hours"), (60, "Every 1 minute"),
     (180, "Every 3 minutes"), (45, "Every 45 seconds")],
)
def test_duration(seconds, expected):
    assert cards._duration(seconds) == expected


def test_preview(monkeypatch):
    assert cards._preview("", "command") == ""
    calls = []
    monkeypatch.setattr(chatbot_variables, "render_preview",
                        lambda text, kind, event_type=None, item=None: calls.append((text, kind, event_type, item)) or "R")
    assert cards._preview("Hi", "event", "follow", "item") == "R"
    assert calls == [("Hi", "event", "follow", "item")]

    def boom(*_a, **_k):
        raise RuntimeError("bad")

    monkeypatch.setattr(chatbot_variables, "render_preview", boom)
    assert cards._preview("raw {x}", "command") == "raw {x}"


def test_preview_real_runtime():
    assert cards._preview("Hey {username}", "command", item=ChatCommand(command_name="hi")) == "Hey TestViewer"


def test_event_info_catalog_and_fallbacks(monkeypatch):
    assert cards.event_info("follow")["label"] == chatbot_variables.EVENT_INDEX["follow"]["label"]
    assert cards.event_info("youtube_new_thing") == {"value": "youtube_new_thing", "label": "Youtube new thing",
                                                     "group": "YouTube", "hint": ""}
    assert cards.event_info("mystery")["group"] == "Twitch"
    assert cards.event_info(None)["label"] == ""
    monkeypatch.setattr(chatbot_variables, "EVENTS", None)  # iteration fails -> fallback
    assert cards.event_info("interval")["group"] == "Time"


@pytest.mark.parametrize(
    "event_type,glyph",
    [("follow", "twitch"), ("youtube_superchat", "youtube"), ("donation", "volunteer_activism"),
     ("interval", "timer")],
)
def test_event_glyph_key(event_type, glyph):
    assert cards.event_glyph_key(event_type) == glyph


def test_event_glyph_key_unknown_group(monkeypatch):
    monkeypatch.setattr(cards, "event_info", lambda et: {"group": "Other"})
    assert cards.event_glyph_key("x") == "bolt"


def test_lookup_label(monkeypatch):
    assert cards.lookup_label(None) == ""
    assert cards.lookup_label({"id": ""}) == ""
    assert cards.lookup_label({"id": "advanced"}) == "Twitch API request"
    entry = chatbot_lookups.LOOKUPS[0]
    assert cards.lookup_label({"id": entry["id"]}) == f"Looks up {entry['label'].lower()}"
    assert cards.lookup_label({"id": "unknown"}) == "Twitch lookup"
    monkeypatch.setattr(chatbot_lookups, "LOOKUPS", None)
    assert cards.lookup_label({"id": "uptime"}) == "Twitch lookup"


def test_command_lookup():
    assert cards._command_lookup(ns(lookup={"id": "x"})) == {"id": "x"}
    assert cards._command_lookup(ns(lookup=None, api_enabled=True)) == {"id": "advanced"}
    assert cards._command_lookup(ns()) is None


def test_output_tags():
    banner = cards._output_tags(ns(output_mode="banner", discord_channels=[{"channel_id": "9"}, "junk"]))
    assert len(banner) == 2 and "Shows as banner" in banner[0] and "#9" in banner[1]
    chat = cards._output_tags(ns(reply_targets=["twitch", "youtube", "kick"],
                                 discord_channels=[{"channel_name": "general"}]))
    assert ["Twitch" in chat[0], "YouTube" in chat[1], "kick" in chat[2], "#general" in chat[3]] == [True] * 4
    assert cards._output_tags(ns()) == []


def test_command_tags_all_kinds():
    counter = "".join(cards.command_tags(ns(command_type=CommandType.COUNTER, counter_value=4, permission="vip",
                                            cooldown=30, usage_limit=1, lookup={"id": "advanced"})))
    assert "Counter · 4" in counter and "VIPs" in counter and "Twitch API request" in counter
    assert "30 s cooldown" in counter and "Limit 1 use<" in counter
    reset = "".join(cards.command_tags(ns(command_type="reset", reset_command="deaths", usage_limit=3)))
    assert "Resets !deaths" in reset and "Limit 3 uses" in reset
    assert "Resets a counter" in "".join(cards.command_tags(ns(command_type="reset")))
    mod = "".join(cards.command_tags(ns(mod_only=True)))
    assert "Mods only" in mod
    assert cards.command_tags(ns(permission="everyone")) == []


@pytest.mark.parametrize(
    "fields,expected",
    [
        ({"event_type": "bits", "bits_quantity": 1500}, "1,500+ bits"),
        ({"event_type": "bits"}, ""),
        ({"event_type": "subscription", "sub_tier": 2}, "Tier 2"),
        ({"event_type": "resubscription", "resub_months": 6, "resub_tier": 3}, "6+ months · Tier 3"),
        ({"event_type": "resubscription"}, ""),
        ({"event_type": "gift_subscription", "gift_sub_quantity": 5}, "5+ gifts"),
        ({"event_type": "gift_subscription", "gift_sub_tier": 1}, "Tier 1"),
        ({"event_type": "donation", "donation_amount": 2.5}, "2.5+ tips"),
        ({"event_type": "raid", "raid_viewer_count": 10, "raid_raider_name": "Bob"}, "10+ viewers · From Bob"),
        ({"event_type": "raid"}, ""),
        ({"event_type": "channel_point_redemption", "channel_point_reward_name": "Hydrate"}, "“Hydrate”"),
        ({"event_type": "channel_point_redemption"}, ""),
        ({"event_type": "hype_train_progress", "hype_train_level": 3}, "Level 3+"),
        ({"event_type": "hype_train_start"}, ""),
        ({"event_type": "hype_train_end", "hype_train_end_level": 4}, "Level 4+"),
        ({"event_type": "chat_message", "chat_message_text": "hi", "chat_message_match_type": "contains"},
         "Contains “hi”"),
        ({"event_type": "youtube_chat_message", "chat_message_text": "hi", "chat_message_match_type": None}, "Is “hi”"),
        ({"event_type": "chat_message"}, ""),
        ({"event_type": "follow"}, ""),
    ],
)
def test_event_filter_text(fields, expected):
    assert cards.event_filter_text(ns(**fields)) == expected


def test_event_trigger_text():
    assert cards.event_trigger_text(ns(event_type="interval", interval=0)) == "Every few minutes"
    assert cards.event_trigger_text(ns(event_type="interval", interval=120)) == "Every 2 minutes"
    assert cards.event_trigger_text(ns(event_type="specific_time", specific_time="18:00")) == "Daily at 18:00"
    assert cards.event_trigger_text(ns(event_type="specific_time")) == "At a set time"
    assert cards.event_trigger_text(ns(event_type=EventType.RAID)) == chatbot_variables.EVENT_INDEX["raid"]["label"]
    assert cards.event_trigger_text(ns(event_type="")) == ""


def test_event_tags():
    tags = "".join(cards.event_tags(ns(event_type="hype_train_progress", hype_train_level=2, lookup={"id": "advanced"},
                                       reply_targets=["twitch"])))
    assert "Level 2+" in tags and "Twitch API request" in tags and "May not fire" in tags and "Twitch" in tags
    assert cards.event_tags(ns(event_type="follow")) == []


def test_search_blob():
    cmd = ns(name="Lurk", description="Says bye", response_text="Bye {username}", command_name="lurk",
             aliases=["afk", "brb"])
    blob = cards.search_blob(cmd, "commands")
    assert "lurk" in blob and "afk" in blob and "bye {username}" in blob
    ev = ns(name=None, event_type="interval", interval=60)
    blob = cards.search_blob(ev, "events")
    assert "every 1 minute" in blob and "timer" in blob


# --------------------------------------------------------------------------- ItemsPanel


class Actions:
    def __init__(self, with_refresh=True, with_reset=True):
        self.calls = []
        self.action = cards.CardActions(
            new=lambda: self.calls.append(("new",)),
            edit=lambda i: self.calls.append(("edit", i)),
            test=lambda i: self.calls.append(("test", i)),
            toggle=lambda i, v: self.calls.append(("toggle", i, v)),
            duplicate=lambda i: self.calls.append(("duplicate", i)),
            delete=lambda i: self.calls.append(("delete", i)),
            reset_counter=(lambda i: self.calls.append(("reset", i))) if with_reset else None,
            refresh=(lambda: self.calls.append(("refresh",))) if with_refresh else None,
        )


@pytest.fixture
def env(monkeypatch):
    e = SimpleNamespace(notes=[])
    monkeypatch.setattr(cards, "notify", lambda msg, **kw: e.notes.append((msg, kw)))
    monkeypatch.setattr(chatbot_variables, "render_preview",
                        lambda text, kind, event_type=None, item=None: text.replace("{username}", "TestViewer"))
    return e


def walk(root, kind=ui.element):
    found = []

    def go(el):
        if isinstance(el, kind):
            found.append(el)
        for slot in el.slots.values():
            for child in slot.children:
                go(child)

    go(root)
    return found


def fire(user, element, event="click", args=None):
    UserInteraction(user, {element}, None).trigger(event, args)


def click(user, element):
    UserInteraction(user, {element}, None).click()


def cards_in(panel):
    return [el for el in walk(panel.grid) if "cx-card" in el.classes]


def card_named(panel, name):
    for card in cards_in(panel):
        if any(isinstance(el, ui.label) and el.text == name for el in walk(card)):
            return card
    raise AssertionError(name)


def html_of(root):
    return "\n".join(el.content for el in walk(root, ui.html))


def mount(kind, items, actions, check):
    holder = {}

    def build():
        panel = cards.ItemsPanel(kind, items, actions.action)
        holder["panel"] = panel
        panel.build(ui.column())

    async def go(user):
        await check(user, holder["panel"])

    run_ui(build, go)
    return holder["panel"]


def _commands():
    return {
        "c1": ChatCommand(command_id="c1", name="Discord", command_name="discord", response_text="Join {username}!",
                          aliases=["dc", " "], cooldown=5),
        "c2": ChatCommand(command_id="c2", name="deaths", command_name="deaths", command_type="counter",
                          response_text="Died {count} times {nope} {other}", enabled=False),
        "c3": ChatCommand(command_id="c3", name="Uptime", command_name="uptime", response_text="",
                          lookup={"id": "uptime"}),
    }


def test_empty_commands_panel(env):
    actions = Actions(with_refresh=False)

    async def check(user, panel):
        await user.should_see("No commands yet")
        await user.should_see("Create your first command, like !discord or !uptime.")
        user.find("Create a command").click()
        user.find("New command").click()
        assert actions.calls == [("new",), ("new",)]
        assert panel._sub.text == "Viewers type these in chat."
        assert [panel._badges[k].text for k in ("all", "on", "off")] == ["0", "0", "0"]
        assert not any(b.props.get("icon") == "refresh" for b in walk(panel.grid.parent_slot.parent.parent_slot.parent,
                                                                         ui.button))

    mount("commands", lambda: {}, actions, check)


def test_empty_events_panel_and_refresh_button(env):
    actions = Actions()

    async def check(user, panel):
        await user.should_see("No events yet")
        await user.should_see("Create an event")
        await user.should_see("New event")
        refresh = [b for b in user.find(ui.button).elements if b.props.get("icon") == "refresh"][0]
        click(user, refresh)
        assert actions.calls == [("refresh",)]

    mount("events", lambda: None, actions, check)


def test_command_cards_render(env):
    actions = Actions()
    items = _commands()
    items["c2"].counter_value = 7
    items["c1"].usage_count = 3
    items["c1"].last_used = time.time()

    async def check(user, panel):
        assert panel._sub.text == "Viewers type these in chat. 3 commands, 2 on."
        assert [panel._badges[k].text for k in ("all", "on", "off")] == ["3", "2", "1"]
        names = [next(el.text for el in walk(c, ui.label) if "cx-name" in el.classes) for c in cards_in(panel)]
        assert names == ["deaths", "Discord", "Uptime"]  # sorted by name, case-insensitive
        discord = card_named(panel, "Discord")
        text = html_of(discord)
        assert '<span class="cx-cmd">!discord</span><span class="cx-alias">!dc</span>' in text
        assert "<b>3</b>&nbsp;uses" in text and "just now" in text
        assert any(el.text == "Join TestViewer!" for el in walk(discord, ui.label))
        assert any(el.text == "No description" and "is-empty" in el.classes for el in walk(discord, ui.label))
        deaths = card_named(panel, "deaths")
        assert "is-off" in deaths.classes
        dtext = html_of(deaths)
        assert "Check {count} +2" in dtext and "Counter · 7" in dtext and "Never used" in dtext
        assert any(b.props.get("icon") == "restart_alt" for b in walk(deaths, ui.button))
        uptime = card_named(panel, "Uptime")
        assert "travel_explore" in html_of(uptime)
        assert any(el.text == "No reply yet" and "is-empty" in el.classes for el in walk(uptime, ui.label))
        assert not any(b.props.get("icon") == "restart_alt" for b in walk(uptime, ui.button))

    mount("commands", lambda: items, actions, check)


def test_card_actions(env):
    actions = Actions()
    items = _commands()

    async def check(user, panel):
        deaths = card_named(panel, "deaths")
        btn = {b.props.get("icon"): b for b in walk(deaths, ui.button)}
        click(user, btn["restart_alt"])
        click(user, btn["play_arrow"])
        click(user, btn["edit"])
        name = [el for el in walk(deaths, ui.label) if "cx-name" in el.classes][0]
        fire(user, name, "dblclick")
        for item in walk(deaths, ui.menu_item):
            click(user, item)
        sw = walk(deaths, ui.switch)[0]
        with user.client:
            sw.value = True
        assert "is-off" not in deaths.classes
        with user.client:
            sw.value = False
        assert "is-off" in deaths.classes
        assert actions.calls == [("reset", "c2"), ("test", "c2"), ("edit", "c2"), ("edit", "c2"),
                                 ("duplicate", "c2"), ("delete", "c2"), ("toggle", "c2", True),
                                 ("toggle", "c2", False)]

    mount("commands", lambda: items, actions, check)


def test_counter_without_reset_action(env):
    actions = Actions(with_reset=False)
    items = {"c2": _commands()["c2"]}

    async def check(user, panel):
        assert not any(b.props.get("icon") == "restart_alt" for b in walk(panel.grid, ui.button))

    mount("commands", lambda: items, actions, check)


def test_search_and_status_filters(env):
    actions = Actions()
    items = _commands()

    async def check(user, panel):
        search = [i for i in user.find(ui.input).elements if i.props.get("placeholder") == "Search name, command or reply"][0]
        with user.client:
            search.value = "  DC "
        assert panel.query == "dc"
        assert len(cards_in(panel)) == 1
        with user.client:
            search.value = "zzz"
        assert "Nothing matches your search or filter." in [el.text for el in walk(panel.grid, ui.label)]
        with user.client:
            search.value = None
        assert len(cards_in(panel)) == 3
        fire(user, panel._chips["off"])
        assert panel.status == "off" and "is-on" in panel._chips["off"].classes
        assert "is-on" not in panel._chips["all"].classes
        assert len(cards_in(panel)) == 1
        fire(user, panel._chips["on"])
        assert len(cards_in(panel)) == 2

    mount("commands", lambda: items, actions, check)


def test_load_failure_notifies(env):
    def broken():
        raise RuntimeError("db gone")

    async def check(user, panel):
        await user.should_see("No commands yet")
        msg, kw = env.notes[-1]
        assert kw["title"] == "Couldn't show your commands" and kw["type"] == "error"
        assert kw["details"] == "RuntimeError: db gone" and kw["dedupe_key"] == "chatbot:list_failed:commands"

    mount("commands", broken, Actions(), check)


def test_card_draw_failure_notifies_and_continues(env, monkeypatch):
    items = _commands()
    real = cards.ItemsPanel._command_card

    def flaky(self, iid, item):
        if iid == "c1":
            raise ValueError("bad card")
        return real(self, iid, item)

    monkeypatch.setattr(cards.ItemsPanel, "_command_card", flaky)

    async def check(user, panel):
        assert len(cards_in(panel)) == 2
        msg, kw = env.notes[-1]
        assert msg == "“Discord” is missing from the list but still works."
        assert kw["details"] == "c1: ValueError: bad card" and kw["dedupe_key"] == "chatbot:card_failed:c1"

    mount("commands", lambda: items, Actions(), check)


def test_card_draw_failure_unnamed_item(env, monkeypatch):
    monkeypatch.setattr(cards.ItemsPanel, "_event_card", lambda self, iid, item: (_ for _ in ()).throw(KeyError("k")))

    async def check(user, panel):
        assert env.notes[-1][0] == "“e9” is missing from the list but still works."

    mount("events", lambda: {"e9": ns(name="", enabled=True)}, Actions(), check)


def test_event_cards_render(env):
    items = {
        "e1": ChatEvent(event_id="e1", name="Thanks", event_type="follow", response_text="Thanks {username}"),
        "e2": ChatEvent(event_id="e2", name="Timer", event_type="interval", interval=600, response_text="Hydrate {mystery}",
                        trigger_count=1),
        "e3": ChatEvent(event_id="e3", name="Tip", event_type="donation", response_text="Ty"),
        "e4": ChatEvent(event_id="e4", name="Super", event_type="youtube_superchat", response_text="Wow"),
    }

    async def check(user, panel):
        assert panel._sub.text.endswith("4 events, 4 on.")
        thanks = html_of(card_named(panel, "Thanks"))
        assert "cx-tile is-twitch" in thanks and "New follower" in thanks
        timer = html_of(card_named(panel, "Timer"))
        assert "Every 10 minutes" in timer and "Check {mystery}" in timer and "<b>1</b>&nbsp;run<" in timer
        assert "cx-tile is-tips" in html_of(card_named(panel, "Tip"))
        assert "cx-tile is-youtube" in html_of(card_named(panel, "Super"))
        assert "Never run" in thanks
        assert not any(b.props.get("icon") == "restart_alt" for b in walk(panel.grid, ui.button))

    mount("events", lambda: items, Actions(), check)


def test_one_item_subtitle_and_refresh_guards(env):
    items = {"e1": ChatEvent(event_id="e1", name="Solo", event_type="raid")}

    async def check(user, panel):
        assert panel._sub.text.endswith("1 event, 1 on.")
        panel._sub = None
        with user.client:
            panel.refresh()
        with user.client:
            panel.grid.delete()
        panel.refresh()  # deleted grid: nothing

    mount("events", lambda: items, Actions(), check)
    cards.ItemsPanel("events", lambda: {}, Actions().action).refresh()  # never built: nothing


def test_card_footer_with_last_only(env, monkeypatch):
    monkeypatch.setattr(cards, "_relative", lambda ts: "")
    items = {"c1": ns(name="X", enabled=True, command_name="x", response_text="", last_used=time.time() - 10)}

    async def check(user, panel):
        text = html_of(card_named(panel, "X"))
        assert "<b>0</b>&nbsp;uses</span>" in text and "is-muted\">&nbsp;·" not in text

    mount("commands", lambda: items, Actions(), check)


def test_css_is_balanced():
    assert cards.CHATBOT_CARDS_CSS.count("{") == cards.CHATBOT_CARDS_CSS.count("}")

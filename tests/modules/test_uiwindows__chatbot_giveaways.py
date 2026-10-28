# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/chatbot_giveaways.py: helpers and the Giveaways tab (hero states,
entrants list and search, last draw, setup, rules, blocked viewers, overlay card, actions).
A real GiveawayManager with saving, overlay events and chat announcements faked backs the UI."""

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
from nicegui.client import Client  # noqa: E402
from nicegui.testing.user_interaction import UserInteraction  # noqa: E402

from modules import chat_banner, chatbot, database_manager, statistics_manager, web_engine  # noqa: E402
from modules import giveaway_manager as gmod  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.uiwindows import chatbot_giveaways as cg  # noqa: E402
from modules.uiwindows import chatbot_gp_styles as gp  # noqa: E402
from ui_sim import run_ui  # noqa: E402


class FakeSchedule:
    def __init__(self, interval, callback):
        self.interval, self.callback = interval, callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


@pytest.fixture
def env(monkeypatch):
    e = SimpleNamespace(notes=[], schedules=[], shown={}, hidden=False, opened=[], clip=[], sent=[],
                        announce_ok=True, banners=[])
    monkeypatch.setattr(database_manager, "get_data_checked", lambda path: ({}, True))
    monkeypatch.setattr(gmod.GiveawayManager, "_save_config", lambda self, pending: None)
    monkeypatch.setattr(gmod.GiveawayManager, "_emit_event", lambda self, ev, data: None)
    monkeypatch.setattr(chatbot, "send_chatbot_announcement",
                        lambda text, color="primary": e.sent.append(text) or e.announce_ok)
    monkeypatch.setattr(chat_banner, "emit_chat_banner", lambda text, source=None: e.banners.append(text))

    class Stats:
        def __getattr__(self, name):
            return lambda *a, **k: None

    monkeypatch.setattr(statistics_manager, "get_statistics_manager", lambda: Stats())
    manager = gmod.GiveawayManager()
    e.gm = manager
    monkeypatch.setattr(cg, "get_giveaway_manager", lambda: manager)
    monkeypatch.setattr(cg, "notify", lambda msg, **kw: e.notes.append((msg, kw)))
    monkeypatch.setattr(gp, "notify", lambda msg, **kw: e.notes.append((msg, kw)))
    monkeypatch.setattr(ui.clipboard, "write", lambda text: e.clip.append(text))

    def schedule(interval, callback):
        timer = FakeSchedule(interval, callback)
        e.schedules.append(timer)
        return timer

    monkeypatch.setattr(cg, "layout_schedule", schedule)
    monkeypatch.setattr(cg, "on_chatbot_subtab_shown", lambda tab, cb: e.shown.__setitem__(tab, cb))
    monkeypatch.setattr(cg, "chatbot_subtab_hidden", lambda tab: e.hidden)
    monkeypatch.setattr(cg, "open_in_spore_studio", lambda t: e.opened.append(t))
    monkeypatch.setattr(web_engine, "web_engine_instance", None, raising=False)
    monkeypatch.setattr(Client, "shared_head_html", Client.shared_head_html)
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr(gp.webbrowser, "open", lambda *a, **k: None)
    return e


def enter(gm, uid, name, when=None):
    gm._pool.append(gmod.GiveawayEntry(user_id=uid, display_name=name, entered_at=time.time() if when is None else when))


def fire(user, element, event="click", args=None):
    UserInteraction(user, {element}, None).trigger(event, args)


def click(user, element):
    UserInteraction(user, {element}, None).click()


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


def html_text(root):
    return "\n".join(el.content for el in walk(root, ui.html))


def label_texts(root):
    return [el.text for el in walk(root, ui.label)]


def button(root, text=None, icon_name=None):
    for b in walk(root, ui.button):
        if (text is None or b.text == text) and (icon_name is None or b.props.get("icon") == icon_name):
            return b
    raise AssertionError(f"no button {text!r} {icon_name!r}")


def has_button(root, text):
    return any(b.text == text for b in walk(root, ui.button))


def switch(root, label):
    for wrapper in walk(root):
        if "gp-toggle" not in wrapper.classes:
            continue
        kids = wrapper.default_slot.children
        texts = [c.text for c in kids[1].default_slot.children if isinstance(c, ui.label)]
        if texts and texts[0] == label:
            return kids[0]
    raise AssertionError(f"no toggle {label!r}")


def mount(env, check):
    holder = {}

    def build():
        panel = cg.GiveawaysPanel()
        holder["panel"] = panel
        panel.build(ui.column())

    async def go(user):
        await check(user, holder["panel"])

    run_ui(build, go)
    return holder["panel"]


# --------------------------------------------------------------------------- helpers


def test_winner_variables():
    out = cg.winner_variables(["A", "B"])
    assert [v["example"] for v in out] == ["A, B", "A, B", "2"]
    assert all(v["group"] == "Giveaway" for v in out)


def test_group_entrants_falls_back_to_name():
    rows = cg.group_entrants([{"name": "A", "user_id": ""}, {"name": "A"}, {"name": "B", "user_id": "2"}])
    assert [(r["name"], r["tickets"]) for r in rows] == [("A", 2), ("B", 1)]
    assert cg.group_entrants([]) == []


@pytest.mark.parametrize("cfg,expected", [({"exclude_mods": True}, "mods can't enter"),
                                          ({"exclude_vips": True}, "VIPs can't enter")])
def test_entry_rules_text_single(cfg, expected):
    assert cg.entry_rules_text(cfg) == expected


def test_names_text():
    assert cg.names_text(["a", "b", "c"]) == "a, b, c"
    assert cg.names_text(["a", "b"], limit=1) == "a and 1 more"
    assert cg.names_text([]) == ""
    assert cg.entry_rules_text({"exclude_mods": 1, "exclude_vips": 1}) == "mods and VIPs can't enter"


def test_warn_announce_failed(env):
    cg._warn_announce_failed("msg")
    msg, kw = env.notes[-1]
    assert msg == "msg" and kw["details"] is None and kw["title"] == "Giveaway announcement didn't send"
    assert kw["actions"] == cg.nav_actions_settings("Twitch")
    cg._warn_announce_failed("m", details="why")
    assert env.notes[-1][1]["details"] == "why"


# --------------------------------------------------------------------------- build / timers


def test_build_idle_state(env):
    async def check(user, panel):
        await user.should_see("Start giveaway")
        for title in ("Entrants", "Overlay", "Setup", "Rules"):
            await user.should_see(title)
        text = html_text(panel.root)
        assert "Not running" in text and "Set the entry keyword on the right" in text
        assert "No entrants yet. Start the giveaway" in text
        assert "Nobody blocked. Bots like nightbot are worth adding." in label_texts(panel.root)
        assert panel.count_badge.text == "0"
        assert env.shown["Giveaways"] == panel._tick
        assert env.schedules[-1].interval == 1.5
        assert panel.preview.text == "Congratulations MossyMika!"

    mount(env, check)


def test_rebuild_stops_old_timer_and_refresh(env):
    async def check(user, panel):
        old = panel._timer
        with user.client:
            panel.build(ui.column())
        assert old.cancelled and panel._timer is not old
        with user.client:
            panel.refresh()
        await user.should_see("Start giveaway")

    mount(env, check)


def test_stop_timer_swallow_and_no_root():
    panel = cg.GiveawaysPanel()

    class Bad:
        def cancel(self):
            raise RuntimeError("x")

    panel._timer = Bad()
    panel._stop_timer()
    assert panel._timer is None
    panel._render_all()  # no root: nothing
    panel._save_keyword()  # no input: nothing
    panel._render_hero({}, {})
    panel._render_alert({})
    panel._render_entrants([], {})
    panel._render_last_draw({}, {})
    panel._render_blocked()
    panel._update_stats(1, None)


def test_tick_hidden_and_deleted(env):
    async def check(user, panel):
        env.hidden = True
        panel._signature = "stale"
        with user.client:
            panel._tick()
        assert panel._signature == "stale"
        env.hidden = False
        with user.client:
            panel._tick()
        assert panel._signature != "stale"
        timer = panel._timer
        with user.client:
            panel.root.delete()
        panel._tick()
        assert timer.cancelled and panel._timer is None

    mount(env, check)


# --------------------------------------------------------------------------- start / keyword


def test_start_without_keyword_warns(env):
    async def check(user, panel):
        user.find("Start giveaway").click()
        msg, kw = env.notes[-1]
        assert msg == "Set an entry keyword before starting." and kw["title"] == "Couldn't start the giveaway"
        panel.keyword_input = None
        with user.client:
            panel._start()

    mount(env, check)


def test_start_with_keyword_and_live_hero(env):
    async def check(user, panel):
        with user.client:
            panel.keyword_input.value = " !join "
        user.find("Start giveaway").click()
        assert env.notes[-1] == ("Accepting entries: viewers type !join", {"type": "success", "source": "chatbot"})
        text = html_text(panel.root)
        assert "Accepting entries" in text and "<code>!join</code>" in text
        assert "Started just now · 1 winner per draw · everyone can enter" in text
        assert "Nobody yet. They appear here" in text
        assert button(panel.root, "Draw winner").enabled is False
        assert "<b>0</b><span>Entrants</span>" in panel.stats.content
        assert "Per draw" in panel.stats.content

    mount(env, check)


def test_live_hero_started_minutes_ago_and_open(env):
    env.gm.update_config({"keyword": "!go", "exclude_mods": True})
    env.gm.start_giveaway()
    env.gm._started_at = time.time() - 300

    async def check(user, panel):
        assert "Started 5 min ago" in html_text(panel.root)
        assert "mods can&#x27;t enter" in html_text(panel.root)
        env.gm._started_at = 0
        with user.client:
            panel._render_live(force=True)
        assert "<div class=\"gp-sub\">Open ·" in html_text(panel.root)

    mount(env, check)


def test_save_keyword_paths(env):
    async def check(user, panel):
        with user.client:
            panel.keyword_input.value = "!a"
        fire(user, panel.keyword_input, "blur")
        assert env.gm.get_config()["keyword"] == "!a"
        assert "Viewers will enter by typing <b>!a</b>" in html_text(panel.root)
        n = len(env.notes)
        fire(user, panel.keyword_input, "keydown.enter")  # unchanged
        assert len(env.notes) == n
        env.gm.start_giveaway()
        with user.client:
            panel.keyword_input.value = "!b"
        fire(user, panel.keyword_input, "blur")
        assert env.notes[-1][0] == "Keyword changed: viewers now type !b"
        assert env.gm.is_giveaway_active()
        with user.client:
            panel.keyword_input.value = ""
        fire(user, panel.keyword_input, "blur")
        assert env.notes[-1][1]["title"] == "Giveaway entries closed"
        assert not env.gm.is_giveaway_active()

    mount(env, check)


def test_reopen_with_new_keyword_does_not_double_notify(env):
    env.gm.update_config({"keyword": "!a"})
    env.gm.start_giveaway()
    enter(env.gm, "1", "Ann")
    env.gm.stop_accepting()

    async def check(user, panel):
        await user.should_see("Ready to draw")
        with user.client:
            panel.keyword_input.value = "!b"
        user.find("Reopen entries").click()
        assert [n[0] for n in env.notes] == ["Accepting entries: viewers type !b"]

    mount(env, check)


def test_keyword_change_when_restart_refused(env, monkeypatch):
    env.gm.update_config({"keyword": "!a"})
    env.gm.start_giveaway()

    async def check(user, panel):
        monkeypatch.setattr(env.gm, "start_giveaway", lambda: (False, "nope"))
        with user.client:
            panel.keyword_input.value = "!c"
            panel._save_keyword()
        assert env.notes == []

    mount(env, check)


# --------------------------------------------------------------------------- entrants


def test_entrants_list_search_and_badges(env):
    env.gm.update_config({"keyword": "!go", "no_duplicate_entries": False})
    env.gm.start_giveaway()
    now = time.time()
    enter(env.gm, "1", "Ann", now - 200)
    enter(env.gm, "2", "Bob", now)
    enter(env.gm, "1", "Ann", now - 130)
    enter(env.gm, "3", "Cy", 0)

    async def check(user, panel):
        assert panel.count_badge.text == "4"
        chips = [el for el in walk(panel.entrants) if "gp-entrant" in el.classes]
        assert len(chips) == 3
        texts = label_texts(panel.entrants)
        assert "×2" in texts and "2 min" in texts and "just now" in texts and "" in texts
        assert button(panel.root, "Draw winner").enabled is True
        search = [i for i in walk(panel.root, ui.input) if i.props.get("placeholder") == "Find a viewer"][0]
        with user.client:
            search.value = " BO "
        assert panel.query == "bo"
        assert len([el for el in walk(panel.entrants) if "gp-entrant" in el.classes]) == 1
        with user.client:
            search.value = "zzz"
        assert "No entrant matches “zzz”." in html_text(panel.entrants)
        with user.client:
            search.value = None
        assert panel.query == ""

    mount(env, check)


def test_entrants_cap_and_more_label(env, monkeypatch):
    monkeypatch.setattr(cg, "MAX_SHOWN", 2)
    for i in range(5):
        enter(env.gm, str(i), f"V{i}")

    async def check(user, panel):
        assert "…and 3 more. Search to find someone." in label_texts(panel.entrants)

    mount(env, check)


def test_remove_and_block_entrant(env):
    env.gm.update_config({"keyword": "!go"})
    env.gm.start_giveaway()
    enter(env.gm, "1", "Ann")
    enter(env.gm, "2", "Bob")

    async def check(user, panel):
        chips = [el for el in walk(panel.entrants) if "gp-entrant" in el.classes]
        bob = [c for c in chips if "Bob" in html_text(c)][0]
        click(user, button(bob, icon_name="close"))
        assert env.notes[-1][0] == "Bob removed"
        assert env.gm.get_pool_entries() == ["Ann"]
        ann = [el for el in walk(panel.entrants) if "gp-entrant" in el.classes][0]
        click(user, button(ann, icon_name="block"))
        assert env.notes[-1][0] == "Ann removed and blocked"
        assert "ann" in label_texts(panel.blocked_box)
        with user.client:
            panel._remove({"user_id": "zz", "name": "Ghost"}, block=False)
        assert env.notes[-1] == ("Ghost is no longer in the pool.", {"type": "info", "source": "chatbot"})

    mount(env, check)


def test_copy_entrants(env):
    async def check(user, panel):
        click(user, button(panel.root, icon_name="content_copy"))
        assert env.notes[-1][0] == "No entrants to copy yet"
        enter(env.gm, "1", "Ann")
        enter(env.gm, "2", "Bob")
        with user.client:
            panel._copy_entrants()
        assert env.clip == ["Bob\nAnn"]
        assert env.notes[-1][0] == "Copied 2 names"

    mount(env, check)


# --------------------------------------------------------------------------- draws


def test_draw_flow_and_last_draw_card(env, monkeypatch):
    env.gm.update_config({"keyword": "!go", "num_winners": 2, "announce_winners_as_banner": True})
    env.gm.start_giveaway()
    for i, name in enumerate(["Ann", "Bob", "Cy"]):
        enter(env.gm, str(i), name)
    monkeypatch.setattr(gmod.random, "choice", lambda seq: seq[0])

    async def check(user, panel):
        user.find("Draw 2 winners").click()
        assert env.notes[-1] == ("Drew 2 winners", {"type": "success", "source": "chatbot"})
        text = html_text(panel.root)
        assert "Last draw" in label_texts(panel.root)
        assert "announced in chat and the banner" in text
        assert panel.preview.text == "Congratulations Ann, Bob!"
        # still accepting: winners are shown as won entrants only when still in pool
        user.find("Stop entries").click()
        assert env.notes[-1] == ("Entries closed", {"type": "info", "source": "chatbot"})
        text = html_text(panel.root)
        assert "Entries closed · winners drawn" in text and "Ann, Bob won!" in text
        assert "1 entrant left in the pool" in text
        assert has_button(panel.root, "Draw 2 more")
        user.find("Announce again").click()
        assert env.notes[-1] == ("Announced again.", {"type": "success", "source": "chatbot"})
        user.find("Copy names").click()
        assert env.clip[-1] == "Ann, Bob"

    mount(env, check)


def test_draw_single_winner_again_and_won_marker(env, monkeypatch):
    env.gm.update_config({"keyword": "!go", "remove_winners_from_pool": False})
    enter(env.gm, "1", "Ann")
    enter(env.gm, "2", "Bob")
    monkeypatch.setattr(gmod.random, "choice", lambda seq: seq[-1])
    env.gm.draw_winners()

    async def check(user, panel):
        text = html_text(panel.root)
        assert "Entries closed · winner drawn" in text and "Bob won!" in text
        assert has_button(panel.root, "Draw again")
        chips = [el for el in walk(panel.entrants) if "gp-entrant" in el.classes]
        # winners are listed first even though Bob entered last
        assert "is-won" in chips[0].classes and "Bob" in html_text(chips[0])
        assert "is-won" not in chips[1].classes
        assert "gp-trophy" in html_text(chips[0])
        assert "announced in chat" in html_text(panel.root)

    mount(env, check)


def test_draw_failures(env):
    async def check(user, panel):
        with user.client:
            panel._draw()
        assert env.notes[-1] == ("Pool is empty.", {"type": "info", "source": "chatbot", "title": "No winners drawn"})
        await user.should_see("Pool is empty.")
        enter(env.gm, "1", "Ann")
        env.announce_ok = False
        panel.preview = None
        with user.client:
            panel._draw()
        msg, kw = env.notes[-1]
        assert msg == "Drew 1 winner, but the chat announcement didn't send."
        assert "Failed to send announcement" in kw["details"]
        text = html_text(panel.root)
        assert "the chat announcement didn&#x27;t send" in text
        assert "but the chat announcement failed" in text
        user.find("Announce again").click()
        assert env.notes[-1][1]["title"] == "Giveaway announcement didn't send"

    mount(env, check)


def test_announce_again_without_winners(env, monkeypatch):
    async def check(user, panel):
        with user.client:
            panel._announce_again()
        assert env.notes[-1] == ("No winners to announce yet.", {"type": "info", "source": "chatbot"})

    mount(env, check)


def test_closed_without_winners_and_new_giveaway_dialog(env):
    env.gm.update_config({"keyword": "!go"})
    env.gm.start_giveaway()
    enter(env.gm, "1", "Ann")
    env.gm.stop_accepting()

    async def check(user, panel):
        text = html_text(panel.root)
        assert "Entries closed" in text and "Ready to draw" in text and "1 entrant in the pool" in text
        assert "<b>1</b><span>Entrant</span>" in panel.stats.content and "Per draw" not in panel.stats.content
        user.find("New giveaway").click()
        await user.should_see("Start a new giveaway?")
        await user.should_see("This empties the pool (1 entrant), clears the keyword and forgets the last draw. "
                              "The overlay clears too.")
        user.find("Cancel").click()
        assert env.gm.get_pool_size() == 1
        user.find("New giveaway").click()
        # the dialog's button (the hero one is outlined); the cancelled dialog's copy is older
        confirm = max((b for b in user.find("New giveaway").elements if "unelevated" in b.props), key=lambda b: b.id)
        click(user, confirm)
        assert env.gm.get_pool_size() == 0 and env.gm.get_config()["keyword"] == ""
        assert env.notes[-1][0] == "Cleared. Set a keyword to start the next giveaway."
        await user.should_see("Start giveaway")

    mount(env, check)


def test_new_giveaway_dialog_with_empty_pool(env):
    env.gm._last_draw = {"winners": ["Ann"], "at": time.time()}

    async def check(user, panel):
        await user.should_see("Ann won!")
        user.find("New giveaway").click()
        await user.should_see("This clears the keyword and forgets the last draw. The overlay clears too.")
        panel.keyword_input = None
        confirm = [b for b in user.find("New giveaway").elements if "unelevated" in b.props]
        click(user, confirm[0])
        assert env.gm.get_status()["last_draw"] == {}

    mount(env, check)


# --------------------------------------------------------------------------- setup / rules


def test_winners_step_and_preview(env):
    async def check(user, panel):
        user.find("+").click()
        assert env.gm.get_config()["num_winners"] == 2 and panel.step_value.text == "2"
        assert panel.preview.text == "Congratulations MossyMika, SporeKid!"
        user.find("−").click()
        user.find("−").click()
        assert env.gm.get_config()["num_winners"] == 1
        env.gm._last_draw = {"winners": ["Real"]}
        with user.client:
            panel._step(1)
        assert panel.preview.text == "Congratulations MossyMika!"  # unchanged while winners exist
        panel.step_value = None
        panel.preview = None
        with user.client:
            panel._step(1)
        assert env.gm.get_config()["num_winners"] == 3

    mount(env, check)


def test_sample_caps_at_sample_list(env):
    env.gm.update_config({"num_winners": 50})
    panel = cg.GiveawaysPanel()
    assert panel._sample() == list(cg.SAMPLE_WINNERS)


def test_announcement_message_edit_and_restore(env):
    async def check(user, panel):
        area = walk(panel.root, ui.textarea)[0]
        with user.client:
            area.value = "GG {winners} ({count})"
        assert panel.preview.text == "GG MossyMika (1)"
        assert env.gm.get_config()["winning_message_template"] == "GG {winners} ({count})"
        with user.client:
            area.value = "  "
        assert env.gm.get_config()["winning_message_template"] == "GG {winners} ({count})"
        fire(user, area, "blur")
        assert area.value == gmod.DEFAULT_CONFIG["winning_message_template"]
        fire(user, area, "blur")
        panel.preview = None
        with user.client:
            area.value = "Yay {winner}"
        assert env.gm.get_config()["winning_message_template"] == "Yay {winner}"

    mount(env, check)


def test_banner_and_rule_toggles(env):
    async def check(user, panel):
        with user.client:
            switch(panel.root, "Also show on the chat banner").value = True
        assert env.gm.get_config()["announce_winners_as_banner"] is True
        for label, field in (("One entry per viewer", "no_duplicate_entries"),
                             ("Can't win twice in one draw", "unique_winners_per_draw"),
                             ("Remove winners after a draw", "remove_winners_from_pool")):
            with user.client:
                switch(panel.root, label).value = False
            assert env.gm.get_config()[field] is False
        with user.client:
            switch(panel.root, "Moderators can't enter").value = True
            switch(panel.root, "VIPs can't enter").value = True
        cfg = env.gm.get_config()
        assert cfg["exclude_mods"] and cfg["exclude_vips"]

    mount(env, check)


def test_blocked_viewers_add_and_unblock(env):
    async def check(user, panel):
        box = [i for i in walk(panel.root, ui.input) if i.props.get("placeholder") == "Add a name and press Enter"][0]
        with user.client:
            box.value = " @NightBot "
        fire(user, box, "keydown.enter")
        assert env.gm.get_config()["blocked_usernames"] == ["nightbot"] and box.value == ""
        with user.client:
            box.value = "nightbot"
        fire(user, box, "blur")
        assert env.gm.get_config()["blocked_usernames"] == ["nightbot"]
        fire(user, box, "blur")  # empty: nothing
        assert "nightbot" in label_texts(panel.blocked_box)
        click(user, button(panel.blocked_box, icon_name="close"))
        assert env.gm.get_config()["blocked_usernames"] == []
        assert "Nobody blocked. Bots like nightbot are worth adding." in label_texts(panel.blocked_box)

    mount(env, check)


def test_overlay_card(env):
    async def check(user, panel):
        await user.should_see("Shows the keyword, entrants and winners on stream, with confetti for the winner. "
                              "Change its look in Spore Studio.")
        user.find("Edit in Spore Studio").click()
        assert env.opened == ["giveaway"]
        assert "/giveaway" in html_text(panel.root)

    mount(env, check)


def test_alert_shows_last_error(env):
    env.gm.set_last_error("Something <bad>")

    async def check(user, panel):
        assert "Something &lt;bad&gt;" in html_text(panel.alert)
        env.gm.set_last_error("")
        with user.client:
            panel._render_live(force=True)
        assert html_text(panel.alert) == ""

    mount(env, check)


def test_live_counter_updates_without_hero_rebuild(env):
    env.gm.update_config({"keyword": "!go"})
    env.gm.start_giveaway()
    enter(env.gm, "1", "Ann")

    async def check(user, panel):
        hero_children = list(panel.hero.default_slot.children)
        enter(env.gm, "2", "Bob")
        with user.client:
            panel._tick()
        assert list(panel.hero.default_slot.children) == hero_children  # same buttons
        assert "<b>2</b><span>Entrants</span>" in panel.stats.content
        assert panel.count_badge.text == "2"
        before = panel.stats.content
        with user.client:
            panel._update_stats(2, 1)
        assert panel.stats.content == before

    mount(env, check)

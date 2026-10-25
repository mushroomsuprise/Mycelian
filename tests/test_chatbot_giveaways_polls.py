# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Giveaways / Polls tab helpers and the manager calls they rely on."""

from __future__ import annotations

import sys
import types

import pytest

from modules import giveaway_manager as gmod
from modules.help_system.help_render import promote_color_importance, scope_selectors
from modules.poll_manager import format_banner_message, poll_widget_info
from modules.uiwindows import chatbot_giveaways as cg
from modules.uiwindows import chatbot_gp_styles as gp
from modules.uiwindows import chatbot_polls as cp


@pytest.fixture
def gm(monkeypatch):
    store = {}
    monkeypatch.setattr("modules.database_manager.get_data", lambda path, default=None: store.get(path, default))
    monkeypatch.setattr("modules.database_manager.get_data_checked", lambda path, default=None: (store.get(path, default), True))
    monkeypatch.setattr("modules.database_manager.set_data", lambda path, value: store.__setitem__(path, value) or True)
    events = []
    monkeypatch.setattr(gmod.GiveawayManager, "_emit_event", lambda self, event, data: events.append((event, data)))
    sent = []
    fake_chatbot = types.ModuleType("modules.chatbot")
    fake_chatbot.send_chatbot_announcement = lambda text, color="primary": sent.append(text) or True
    monkeypatch.setitem(sys.modules, "modules.chatbot", fake_chatbot)
    manager = gmod.GiveawayManager()
    manager._save_config = lambda pending: None
    manager.events, manager.sent = events, sent
    return manager


def _enter(manager, uid, name, when):
    manager._pool.append(gmod.GiveawayEntry(user_id=uid, display_name=name, entered_at=when))


def test_format_winners_message():
    assert gmod.format_winners_message("GG {winners} ({count})", ["A", "B"]) == "GG A, B (2)"
    assert gmod.format_winners_message("Hi {winner}", ["Mika"]) == "Hi Mika"


def test_status_details_and_remove(gm):
    gm.update_config({"keyword": "!enter"})
    assert gm.start_giveaway() == (True, "")
    assert gm.get_status()["started_at"] > 0
    _enter(gm, "1", "Mika", 10)
    _enter(gm, "2", "Larry", 20)
    assert [d["name"] for d in gm.get_pool_details()] == ["Larry", "Mika"]
    assert gm.remove_entry("2", block=True)
    assert gm.get_config()["blocked_usernames"] == ["larry"]
    assert gm.events[-1][0] == "giveaway_sync" and gm.events[-1][1]["pool_entries"] == ["Mika"]
    assert not gm.remove_entry("nobody")
    status = gm.get_status()
    assert status["accepting"] and status["pool_size"] == 1 and status["keyword"] == "!enter"


def test_draw_records_last_draw_and_announce_again(gm):
    gm.update_config({"keyword": "!enter", "winning_message_template": "Yay {winners}"})
    assert gm.announce_last_draw()[0] is False
    _enter(gm, "1", "Mika", 10)
    ok, _msg, winners = gm.draw_winners()
    assert ok and winners == ["Mika"]
    draw = gm.get_status()["last_draw"]
    assert draw["winners"] == ["Mika"] and draw["announced"] is True
    assert gm.announce_last_draw() == (True, "Announced again.")
    assert gm.sent == ["Yay Mika", "Yay Mika"]
    gm.clear_giveaway()
    status = gm.get_status()
    assert status["last_draw"] == {} and status["keyword"] == "" and status["started_at"] == 0


def test_giveaway_ui_helpers():
    rows = cg.group_entrants([{"name": "A", "user_id": "1", "entered_at": 3}, {"name": "B", "user_id": "2", "entered_at": 2},
                              {"name": "A", "user_id": "1", "entered_at": 1}])
    assert [(r["name"], r["tickets"]) for r in rows] == [("A", 2), ("B", 1)]
    assert cg.entry_rules_text({"exclude_mods": True, "exclude_vips": True}) == "mods and VIPs can't enter"
    assert cg.entry_rules_text({}) == "everyone can enter"
    assert cg.names_text(["a", "b", "c", "d", "e"]) == "a, b, c and 2 more"
    assert [v["key"] for v in cg.winner_variables(["X"])] == ["winners", "winner", "count"]


def test_poll_banner_message():
    winners = [{"name": "Celeste", "keyword": "#celeste", "votes": 31}]
    assert format_banner_message("{option} {keyword} {votes}", winners) == "Celeste #celeste 31"
    tie = winners + [{"name": "Hades", "keyword": "#hades", "votes": 31}]
    assert format_banner_message("{option}!", tie) == "Celeste, Hades!"
    assert format_banner_message("No {option}", []) == "No {option}"


def test_poll_widget_info_defaults_for_unknown_template():
    info = poll_widget_info("__no_such_template__")
    assert info["id"] == "" and info["count"] == 0
    assert info["style"]["bar_color"] and info["style"]["winner_color"]


def test_poll_ui_helpers():
    assert cp.split_duration(180) == (3, 60)
    assert cp.split_duration(7200) == (2, 3600)
    assert cp.split_duration(45) == (45, 1)
    snap = {"options": [{"name": "A", "votes": 3}, {"name": "B", "votes": 3}, {"name": "C", "votes": 0}]}
    assert cp.leader_text(snap) == "2-way tie"
    assert cp.leader_text({"options": [{"name": "A", "votes": 0}]}) == "—"
    assert cp.winners_text([{"name": "A", "votes": 1}]) == "A (1 vote)"
    style = poll_widget_info("__no_such_template__")["style"]
    html = cp.bars_html({"options": [{"name": "<b>", "keyword": "#b", "votes": 2, "percent": 100, "winner": True}]}, style, live=False)
    assert "&lt;b&gt;" in html and "🏆" in html and style["winner_color"] in html
    assert "Option 1" in cp.widget_html({"options": []}, style)


def test_shared_helpers_and_styles():
    assert gp.ago(30) == "just now" and gp.ago(150) == "2 min" and gp.ago(3600 * 2 + 300) == "2 h 5 min"
    assert gp.clock(102) == "1:42" and gp.clock(3725) == "1:02:05"
    assert gp.duration_text(180) == "3 minutes" and gp.duration_text(3600) == "1 hour" and gp.duration_text(45) == "45 seconds"
    scoped = promote_color_importance(scope_selectors(gp.GP_CSS, ".cnx"))
    assert scoped.count("{") == scoped.count("}")
    assert gp.overlay_url("giveaway").endswith("/giveaway")


def test_failed_announcement_marks_last_draw(gm, monkeypatch):
    def boom(text, color="primary"):
        raise RuntimeError("offline")

    sys.modules["modules.chatbot"].send_chatbot_announcement = boom
    shown = []
    monkeypatch.setitem(sys.modules, "modules.notification_engine", types.SimpleNamespace(
        nav_actions_main_tab=lambda tab: [], nav_chatbot=lambda tab: [],
        notify_critical=lambda *a, **k: shown.append(a), notify=lambda *a, **k: shown.append(a)))
    gm.update_config({"keyword": "!enter", "announce_winners_as_banner": False})
    _enter(gm, "1", "Mika", 10)
    ok, msg, _winners = gm.draw_winners()
    draw = gm.get_status()["last_draw"]
    assert draw["announced"] is False and draw["banner"] is False
    # The Giveaways tab shows the single warning; the manager adds no second one.
    assert ok and "didn't send" in msg and shown == []
    assert "offline" in gm.get_last_error()

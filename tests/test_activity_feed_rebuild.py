# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Activity Feed rebuild: settings, row/queue mapping, shared view (search), dock payloads."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.uiwindows import activity_feed as feed  # noqa: E402
from modules.uiwindows import activity_feed_model as model  # noqa: E402


@pytest.fixture
def fresh_view(monkeypatch):
    """A view manager that never touches the database."""
    state = feed.activity_feed_state
    before = (dict(state.filter_state), state.condense_list, state.current_tab, state.current_page, state.search_text)
    vm = feed.ActivityFeedViewManager()
    vm._loaded = True
    monkeypatch.setattr(vm, "save", lambda: True)
    monkeypatch.setattr(feed, "view_manager", vm)
    monkeypatch.setattr(feed, "_run_on_ui_loop", lambda fn: None)
    sent = []
    monkeypatch.setattr(feed, "_emit_overlay_event", lambda event, payload, to=None: sent.append((event, payload, to)) or True)
    yield vm, sent
    state.filter_state, state.condense_list, state.current_tab, state.current_page, state.search_text = before


# --------------------------------------------------------------------------- settings


def test_feed_settings_defaults_match_the_prototype():
    s = model.normalize_feed_settings(None, legacy_person_hours=None)
    assert s["layout"] == "rows"
    assert s["tier"] == "icon"  # tier badge on the icon corner by default
    assert s["timer"] == "strip" and s["up_next"] == 1 and s["earlier_minutes"] == 30
    assert s["dots"] is True and s["order_numbers"] is True and s["now_playing"] is True
    assert s["person_hours"] == 12


def test_feed_settings_reject_unknown_values_and_coerce_types():
    s = model.normalize_feed_settings(
        {"activity_feed_layout": "carousel", "activity_feed_up_next": "3", "activity_feed_wrap_text": "true",
         "activity_feed_earlier_after_minutes": 45, "timer": "ITEM"}
    )
    assert s["layout"] == "rows"
    assert s["up_next"] == 3
    assert s["wrap"] is True
    assert s["earlier_minutes"] == 30  # 45 is not an offered choice
    assert s["timer"] == "item"  # short keys work too, case-insensitive


def test_person_hours_migrates_the_old_dock_setting_once_unset():
    assert model.normalize_feed_settings({"activity_feed_person_hours": 0}, legacy_person_hours=6)["person_hours"] == 6
    assert model.normalize_feed_settings({"activity_feed_person_hours": 24}, legacy_person_hours=6)["person_hours"] == 24
    assert model.normalize_feed_settings({}, legacy_person_hours=None)["person_hours"] == 12


def test_feed_setting_value_validates_keys():
    assert model.feed_setting_value("layout", "cards") == "cards"
    assert model.feed_setting_value("layout", "nope") == "rows"
    with pytest.raises(ValueError):
        model.feed_setting_value("not_a_setting", 1)


def test_every_feed_setting_is_an_app_settings_field():
    from modules.dataobjects import AppSettings

    fields = set(AppSettings.__dataclass_fields__)
    for key, (field, default, _allowed) in model.FEED_SETTINGS.items():
        assert field in fields, key
        assert getattr(AppSettings(), field) == default, key


def test_set_feed_setting_saves_and_broadcasts(monkeypatch):
    saved = {}
    fake_sm = SimpleNamespace(update_app_setting=lambda f, v: saved.__setitem__(f, v) or True, save_changes=lambda: True)
    monkeypatch.setattr("modules.dataobjects.state_manager", fake_sm)
    applied = []
    monkeypatch.setattr(feed, "apply_feed_settings_changed", lambda keys=None: applied.append(keys))
    assert feed.set_feed_setting("layout", "ticker") is True
    assert saved == {"activity_feed_layout": "ticker"}
    assert applied == [{"layout"}]


# --------------------------------------------------------------------------- rows


def _payload(*args, **kwargs):
    with (
        patch.object(feed.alertutils.alert_state_manager, "initialize"),
        patch.object(feed.alertutils.alert_state_manager, "get_stored_alert_by_id", return_value=None),
        patch.object(feed.alertutils, "resolve_chat_alert_media", return_value=None),
    ):
        return feed.build_activity_feed_alert_payload(*args, **kwargs)


def test_raid_row_keeps_counts_over_999_and_the_game():
    p = _payload("Raid", "Big raided with 1,234 viewers, they were last playing Hades II.", "raid",
                 raider_count=1234, game_name="Hades II")
    row = p["row"]
    assert p["raider_count"] == 1234
    assert row["amount"] == "1,234 viewers"
    assert row["what"] == "raided · last playing Hades II"
    # fallback parsing (no structured count) keeps the thousands too
    assert model.feed_row_model({"type": "Raid", "badge_type": "raid", "message": "X raided with 2,500 viewers!"})["amount"] == "2,500 viewers"


def test_youtube_rows_and_username_kwarg():
    p = _payload("Membership", "Ann Lee became a channel member!", "membership", username="Ann Lee", tier=1)
    assert p["username"] == "Ann Lee"
    row = p["row"]
    assert (row["kind"], row["platform"], row["user"], row["tier"]) == ("member", "youtube", "Ann Lee", None)
    sc = model.feed_row_model({"type": "Super Chat", "badge_type": "superchat", "username": "Bo", "display_amount": "£5.00"})
    assert (sc["kind"], sc["amount"], sc["filter"]) == ("superchat", "£5.00", "donations")


def test_rows_keep_raw_text_for_escaping_by_the_renderer():
    row = model.feed_row_model({"type": "Follow", "badge_type": "follow", "username": "cool_guy_99",
                                "user_message": "*hi* <img src=x> [x](y)"})
    assert row["user"] == "cool_guy_99"  # no markdown: underscores stay underscores
    assert row["msg"] == "*hi* <img src=x> [x](y)"


def test_donation_row_uses_the_currency():
    eur = model.feed_row_model({"type": "Donation", "badge_type": "donation", "username": "E", "donation_amount": 5, "currency": "EUR"})
    assert eur["amount"] == "€5.00" and eur["platform"] == ""
    text = model.feed_row_model({"type": "Donation", "badge_type": "donation", "username": "E", "donation_text": "€5.00 (≈ $5.40)"})
    assert text["amount"] == "€5.00 (≈ $5.40)"


def test_gift_and_sub_tiers():
    gift = model.feed_row_model({"type": "Giftsub", "badge_type": "giftsub", "username": "G", "gift_qty": 5, "tier": 2})
    assert (gift["what"], gift["amount"], gift["tier"]) == ("gifted 5 Tier 2 subs", "×5", None)
    sub = model.feed_row_model({"type": "Resub", "badge_type": "resub", "username": "R", "tier": 3, "resub_month": 14})
    assert (sub["what"], sub["tier"]) == ("resubscribed · 14 months", "T3")


def test_row_filter_and_search_rule():
    row = model.feed_row_model({"type": "Bits", "badge_type": "bits", "username": "Fungi", "amt_cheered": 100, "user_message": "vibes"})
    on = feed.default_filter_state()
    assert model.row_matches(row, on, "VIBE")
    assert model.row_matches(row, on, "fungi")
    assert not model.row_matches(row, on, "nope")
    off = dict(on, all=False, bits=False)
    assert not model.row_matches(row, off)


# --------------------------------------------------------------------------- queue state


def test_queue_state_normalisation_and_tags():
    state = model.normalize_queue_state({
        "paused": False, "muted": True, "server_now_ms": 1000, "revision": 4,
        "playing": {"alert_id": "a1", "type": "bit", "username": "x", "started_at_ms": 500, "duration_ms": 0, "queue_seq": 3},
        "queue": [{"alert_id": "a2", "type": "follow", "username": "y"}, {"alert_id": "a3", "type": "raid", "is_replay": True}, "junk"],
    })
    assert state["muted"] is True and state["revision"] == 4 and state["contract"] is True
    assert state["playing"]["duration_ms"] is None  # unknown run time
    assert [q["alert_id"] for q in state["queue"]] == ["a2", "a3"]
    tags = model.queue_tags(state)
    assert tags == {"a1": {"state": "playing", "pos": 0}, "a2": {"state": "queued", "pos": 1}, "a3": {"state": "queued", "pos": 2}}
    assert model.queue_tag_text(tags["a3"], paused=False) == "Up next · 2nd"
    assert model.queue_tag_text(tags["a2"], paused=True) == "Waiting · 1st"
    assert [model.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 101)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "101st"]


def test_queue_state_falls_back_without_the_api(monkeypatch):
    from modules import web_engine

    monkeypatch.delattr(web_engine, "get_alert_queue_state", raising=False)
    monkeypatch.setattr(web_engine, "ALERTS_PAUSED", True)
    monkeypatch.setattr(web_engine, "ALERT_PLAYING", False)
    state = feed.current_queue_state()
    assert state["paused"] is True and state["contract"] is False and state["playing"] is None


def test_queue_state_uses_the_contract_api(monkeypatch):
    from modules import web_engine

    monkeypatch.setattr(web_engine, "get_alert_queue_state", lambda: {
        "paused": False, "muted": False, "playing": None, "queue": [{"alert_id": "q1", "type": "sub", "username": "s"}],
        "server_now_ms": 5, "revision": 9})
    state = feed.current_queue_state()
    assert state["contract"] is True and state["revision"] == 9 and state["queue"][0]["alert_id"] == "q1"


# --------------------------------------------------------------------------- shared view


def test_view_modes_map_to_tab_and_condensed():
    assert model.view_mode({"tab": "previous", "condensed": True}) == "history"
    assert model.view_mode({"tab": "current", "condensed": True}) == "person"
    assert model.view_mode({"tab": "current"}) == "live"
    assert model.mode_patch("person") == {"tab": "current", "condensed": True}


def test_search_is_shared_but_never_saved(fresh_view, monkeypatch):
    vm, sent = fresh_view
    saves = []
    monkeypatch.setattr(vm, "save", lambda: saves.append(1) or True)
    snap = vm.update({"search": "  spore   lord "})
    assert snap["search"] == "spore lord"
    assert feed.activity_feed_state.search_text == "spore lord"
    assert saves == []  # search alone is not persisted
    event, payload, _to = sent[-1]
    assert event == "activity_feed_view" and payload["search"] == "spore lord" and payload["mode"] == "live"
    # an echo with the same value is not a change
    rev = vm.revision
    vm.update({"search": "spore lord"})
    assert vm.revision == rev


def test_mode_patch_and_dock_echo(fresh_view):
    vm, sent = fresh_view
    snap = vm.update({"mode": "person"})
    assert (snap["tab"], snap["condensed"], snap["mode"]) == ("current", True, "person")
    snap = vm.update({"mode": "history"})
    assert snap["mode"] == "history" and snap["page"] == 1
    # an older dock echo cannot roll the view back
    assert vm.apply_snapshot(dict(snap, revision=snap["revision"] - 1, tab="current")) is False


def test_page_is_clamped_to_max_pages(fresh_view, monkeypatch):
    vm, _ = fresh_view
    monkeypatch.setattr(feed, "_max_history_pages", lambda: 5)
    assert vm.update({"page": 99})["page"] == 5
    assert vm.update({"page": -3})["page"] == 1


def test_save_never_writes_the_search(monkeypatch):
    vm = feed.ActivityFeedViewManager()
    vm._loaded = True
    vm._view = feed.normalize_view({"search": "x", "tab": "previous"})
    with patch("modules.database_manager.set_data", return_value=True) as set_data:
        vm.save()
    assert "search" not in set_data.call_args.args[1]


def test_tab_actions_reach_the_shared_view(fresh_view):
    vm, _ = fresh_view
    feed.handle_feed_action("search", {"text": "abc"})
    feed.handle_feed_action("filter", {"key": "bits", "value": False})
    feed.handle_feed_action("mode", {"mode": "person"})
    snap = vm.snapshot()
    assert snap["search"] == "abc" and snap["filters"]["bits"] is False and snap["filters"]["all"] is False
    assert snap["mode"] == "person"
    feed.handle_feed_action("filter", {"key": "not_a_filter", "value": False})  # ignored
    assert vm.snapshot()["revision"] == snap["revision"]


def test_pause_action_only_toggles_when_needed(monkeypatch):
    from modules import mainuiwindow, web_engine

    calls = []
    monkeypatch.setattr(mainuiwindow, "toggle_alerts", lambda: calls.append("toggle"))
    monkeypatch.setattr(feed, "_poll_queue", lambda force=False: None)
    monkeypatch.setattr(web_engine, "ALERTS_PAUSED", True)
    feed.handle_feed_action("pause", {"paused": True})
    assert calls == []
    feed.handle_feed_action("pause", {"paused": False})
    assert calls == ["toggle"]


def test_view_change_to_person_rebuilds_groups(fresh_view, monkeypatch):
    vm, _ = fresh_view
    scheduled = []
    monkeypatch.setattr(feed, "schedule_condensed_view_update", lambda reason: scheduled.append(reason))
    monkeypatch.setattr(feed, "_push_slot", lambda name, value: None)
    vm.update({"mode": "person"})
    feed._apply_view_to_desktop({"tab", "condensed"})
    feed._apply_view_to_desktop({"search"})
    assert scheduled == ["view_change", "view_change"]


# --------------------------------------------------------------------------- live list and payloads


def test_add_alert_to_feed_accepts_username_and_extra_kwargs(monkeypatch):
    order = []
    monkeypatch.setattr(feed.alert_event_handler, "process_alert_immediately",
                        lambda data: order.append(("insert", data["seq"])))
    engine = MagicMock()
    engine.activity_feed_alert.side_effect = lambda data: order.append(("emit", data["seq"]))
    monkeypatch.setattr("modules.web_engine.web_engine_instance", engine)
    with (
        patch.object(feed.alertutils.alert_state_manager, "initialize"),
        patch.object(feed.alertutils.alert_state_manager, "get_stored_alert_by_id", return_value=None),
        patch.object(feed.alertutils, "resolve_chat_alert_media", return_value=None),
    ):
        feed.add_alert_to_feed("Raid", "R raided with 2,000 viewers!", "raid", alert_id="A1", username="R",
                               raider_count=2000, game_name="Factorio", some_future_field=object())
        feed.add_alert_to_feed("Follow", "F just followed!", "follow")
    # inserted into the shared list before the dock is told, in arrival order
    assert [o[0] for o in order] == ["insert", "emit", "insert", "emit"]
    assert order[2][1] == order[0][1] + 1
    payload = engine.activity_feed_alert.call_args_list[0].args[0]
    assert payload["alert_id"] == "A1" and payload["username"] == "R"
    assert payload["row"]["amount"] == "2,000 viewers" and payload["row"]["seq"] == payload["seq"]
    assert "some_future_field" not in payload
    json.dumps(payload)  # dock payloads are JSON-safe


def test_live_insert_is_capped_and_newest_first(monkeypatch):
    monkeypatch.setattr(feed.activity_feed_state, "live_alerts", [])
    monkeypatch.setattr(feed, "_schedule_live_push", lambda: None)
    handler = feed.AlertEventHandler()
    for i in range(feed.MAX_LIVE_ALERTS + 3):
        handler.process_alert_immediately({"type": "Follow", "badge_type": "follow", "message": f"u{i} just followed!", "seq": i})
    alerts = feed.live_alerts_snapshot()
    assert len(alerts) == feed.MAX_LIVE_ALERTS
    assert alerts[0]["seq"] == feed.MAX_LIVE_ALERTS + 2
    assert alerts[0]["row"]["kind"] == "follow"


# --------------------------------------------------------------------------- history


def test_stored_alerts_map_to_live_type_keys():
    bits = feed.convert_stored_alert_to_feed_format({"alert_type": "bit", "username": "b", "amt_cheered": 1500, "timestamp": 1})
    assert bits["badge_type"] == "bits" and bits["row"]["kind"] == "bits" and bits["row"]["amount"] == "1,500 bits"
    point = feed.convert_stored_alert_to_feed_format({"alert_type": "point", "username": "p", "alert_name": "Hydrate", "timestamp": 1})
    assert point["badge_type"] == "points" and point["row"]["filter"] == "points"
    hype = feed.convert_stored_alert_to_feed_format({"alert_type": "hype_train_end", "hype_train_level": 3, "timestamp": 1})
    assert hype["badge_type"] == "hype_train" and hype["row"]["kind"] == "hype"
    resub = feed.convert_stored_alert_to_feed_format({"alert_type": "sub", "username": "s", "resub_month": 5, "timestamp": 1})
    assert (resub["type"], resub["badge_type"]) == ("Resub", "resub")
    eur = feed.convert_stored_alert_to_feed_format({"alert_type": "donation", "username": "d", "donation_amount": 5.0, "currency": "EUR", "timestamp": 1})
    assert eur["message"] == "d donated €5.00!"
    raid = feed.convert_stored_alert_to_feed_format({"alert_type": "raid", "username": "r", "raider_count": "1,500", "timestamp": 1})
    assert raid["raider_count"] == 1500 and raid["row"]["amount"] == "1,500 viewers"
    assert bits["row"]["restored"] is True


def test_history_page_is_clamped_when_history_shrank(monkeypatch):
    calls = []

    def page(page, limit, max_total_alerts):
        calls.append(page)
        alerts = [{"alert_id": f"a{i}", "alert_type": "follow", "username": f"u{i}", "timestamp": 100 - i} for i in range(2)]
        return {"alerts": alerts if page <= 2 else [], "total_count": 12, "total_pages": 2, "page": page}

    manager = SimpleNamespace(initialize=lambda: None, get_limited_stored_alerts_paginated=page)
    monkeypatch.setattr(feed.alertutils, "alert_state_manager", manager)
    settings = SimpleNamespace(activity_feed_limit=10, activity_feed_max_pages=5)
    monkeypatch.setattr("modules.dataobjects.state_manager", SimpleNamespace(get_app_settings=lambda: settings))
    rows, info = feed.load_restored_alerts(4)
    assert calls == [4, 2]
    assert info["page"] == 2 and info["has_prev"] and not info["has_next"]
    assert [r["row"]["seq"] for r in rows] == [2, 1]  # position in the stored history


def test_person_window_reads_all_storage_not_just_feed_pages(monkeypatch):
    now = time.time()
    rows = [{"alert_id": "x", "alert_type": "follow", "username": "x", "timestamp": now - 10},
            {"alert_id": "old", "alert_type": "follow", "username": "o", "timestamp": now - 99999}]
    manager = SimpleNamespace(initialize=lambda: None, get_stored_alerts_since=lambda cutoff, limit=None: rows)
    monkeypatch.setattr(feed.alertutils, "alert_state_manager", manager)
    alerts, count = feed.load_restored_alerts_for_time_window(now - 3600)
    assert count == 1 and alerts[0]["alert_id"] == "x"


# --------------------------------------------------------------------------- by person


def test_person_cards_have_items_totals_and_events():
    now = time.time()
    alerts = [
        {"type": "Bits", "username": "alice", "message": "alice cheered 100 bits!", "stored_alert_data": {"amt_cheered": 100}, "created_at": now - 50},
        {"type": "Bits", "username": "alice", "message": "alice cheered 1,200 bits!", "created_at": now - 10},
        {"type": "Sub", "username": "alice", "tier": 1, "message": "alice subscribed", "created_at": now - 30},
    ]
    users, _ex, _unk = feed._group_alerts_for_condensed(alerts, filter_state={"all": True}, search="")
    group = feed.serialize_condensed_groups(users)[0]
    assert group["events"] == 3 and abs(group["last_ts"] - (now - 10)) < 1
    texts = {(i["kind"], i["text"], i["count"]) for i in group["items"]}
    assert ("bits", "Cheered 1,300 bits", 2) in texts and ("sub", "Subscribed (Tier 1)", 1) in texts
    assert {t["text"] for t in group["totals"]} == {"1,300 bits", "T1"}
    assert "Gave 1,300 bits!" in group["lines"]  # older docks keep their wording


def test_person_truncation_is_reported(monkeypatch):
    many = [{"type": "Follow", "username": f"u{i}", "message": f"u{i} just followed!", "alert_id": str(i)} for i in range(feed.MAX_CONDENSED_GROUPS + 5)]
    monkeypatch.setattr(feed, "collect_alerts_for_condensed_view", lambda cutoff: (many, len(many), 0))
    payload = feed.build_condensed_overlay_payload(hours=12, filter_state={"all": True}, search="")
    assert len(payload["groups"]) == feed.MAX_CONDENSED_GROUPS
    assert payload["truncation"] == f"Showing first {feed.MAX_CONDENSED_GROUPS} of {len(many)} people"


def test_person_payload_uses_the_shared_search(monkeypatch, fresh_view):
    vm, _ = fresh_view
    vm.update({"search": "bob"})
    sample = [{"type": "Follow", "username": "alice", "message": "alice followed"},
              {"type": "Follow", "username": "bob", "message": "bob followed"}]
    monkeypatch.setattr(feed, "collect_alerts_for_condensed_view", lambda cutoff: (sample, 2, 0))
    payload = feed.build_condensed_overlay_payload(hours=12)
    assert [g["username"] for g in payload["groups"]] == ["bob"]
    assert payload["search"] == "bob"


# --------------------------------------------------------------------------- fail-open + assets


def test_health_decisions():
    assert feed._feed_health_action({"ok": True}, 3) == ("ok", 0)
    assert feed._feed_health_action({"ok": False, "hosts": 0}, 1) == ("skip", 1)
    assert feed._feed_health_action({"ok": False, "hosts": 1, "visible": 0}, 1) == ("skip", 1)
    assert feed._feed_health_action({"ok": False, "reason": "no_renderer", "hosts": 1, "visible": 1}, 0) == ("reinject", 1)
    assert feed._feed_health_action({"ok": False, "hosts": 1, "visible": 1}, 1) == ("fallback", 2)
    assert feed._feed_health_action(None, 2) == ("skip", 2)


def test_fallback_list_escapes_everything():
    html = feed.render_fallback_html([{"user": "<b>x</b>", "what": "cheered", "amount": "5", "msg": "<script>", "ts": time.time()}])
    assert "<script>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html


def test_app_stylesheet_is_scoped_and_dock_css_exists():
    css = feed.feed_app_stylesheet()
    assert ".afx-host .afx-row" in css
    assert "@keyframes afeq" in css
    assert "color: var(--color-text-primary) !important" in css
    template = (ROOT / "templates" / "activity_feed.html").read_text(encoding="utf-8")
    assert "/assets/default_assets/activity_feed.css" in template and "/assets/default_assets/activity_feed.js" in template
    assert "ui.markdown" not in (ROOT / "modules" / "uiwindows" / "activity_feed.py").read_text(encoding="utf-8")


_NODE_HARNESS = r"""
global.window = global;
global.document = { querySelectorAll: function () { return []; }, addEventListener: function () {} };
require(process.argv[2]);
var F = window.MycelianFeed;
var out = {
  settings: F.normSettings({ layout: 'cards', up_next: '3', tier: 'weird', wrap: 1 }),
  match: F.filterMatch({ user: 'Fungi', what: 'cheered', msg: 'vibes', filter: 'bits' }, { all: false, bits: true }, 'VIBE'),
  blocked: F.filterMatch({ user: 'Fungi', filter: 'bits' }, { all: false, bits: false }, ''),
  row: F.rowFromAlert({ type: 'Follow', badge_type: 'follow', username: 'x', message: 'x just followed!', alert_id: 'generated_1' }),
  pre: F.rowFromAlert({ row: { kind: 'bits', user: 'p' } }),
  slim: F.rowFromAlert({ kind: 'raid', icon: 'groups', user: 'r' }),
  queue: F.normQueue({ paused: true, revision: 2, server_now_ms: 1, playing: { alert_id: 'a', duration_ms: 0 }, queue: [{ alert_id: 'b' }, null] }),
  ord: [1, 2, 3, 11, 22].map(F.ordinal)
};
console.log(JSON.stringify(out));
process.exit(0);
"""


def test_shared_renderer_helpers_in_node(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    harness = tmp_path / "h.js"
    harness.write_text(_NODE_HARNESS, encoding="utf-8")
    js = ROOT / "assets" / "default_assets" / "activity_feed.js"
    done = subprocess.run([node, str(harness), str(js)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    assert out["settings"]["layout"] == "cards" and out["settings"]["up_next"] == 3
    assert out["settings"]["tier"] == "icon" and out["settings"]["wrap"] is True
    assert out["match"] is True and out["blocked"] is False
    assert out["row"]["kind"] == "follow" and out["row"]["alert_id"] is None  # invented ids are not replayable
    assert out["pre"]["user"] == "p" and out["slim"]["kind"] == "raid"
    assert out["queue"]["paused"] is True and out["queue"]["playing"]["duration_ms"] is None
    assert len(out["queue"]["queue"]) == 1
    assert out["ord"] == ["1st", "2nd", "3rd", "11th", "22nd"]

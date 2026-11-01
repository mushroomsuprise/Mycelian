# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/prediction_manager.py with fake Twitch calls, storage and overlay emits."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import prediction_manager as pm  # noqa: E402


class FakeTwitch:
    def __init__(self):
        self.ready = "ok"
        self.create_result = (True, {"id": "p1", "title": "Win?", "outcomes": [
            {"id": "t1", "title": "Yes", "color": "BLUE"}, {"id": "t2", "title": "No", "color": "PINK"}],
            "created_at": "2026-01-01T00:00:00Z", "prediction_window": 120})
        self.end_result = (True, "")
        self.ends = []
        self.current = None

    def install(self, monkeypatch):
        tp = pm.tp
        monkeypatch.setattr(tp, "readiness", lambda: self.ready)
        monkeypatch.setattr(tp, "create_prediction", lambda title, outcomes, window: self.create_result)
        monkeypatch.setattr(tp, "end_prediction", lambda pid, status, winner=None: self.ends.append((pid, status, winner)) or self.end_result)
        monkeypatch.setattr(tp, "get_current_prediction", lambda: self.current)


@pytest.fixture
def env(monkeypatch):
    import modules.database_manager as dbm

    store = {"data": None, "ok": True, "writes": []}
    monkeypatch.setattr(dbm, "get_data_checked", lambda path: (store["data"], store["ok"]))
    monkeypatch.setattr(dbm, "set_data", lambda path, value: store["writes"].append((path, json.loads(json.dumps(value)))))
    twitch = FakeTwitch()
    twitch.install(monkeypatch)
    emitted, dispatched, chat, banners = [], [], [], []
    monkeypatch.setattr(pm.PredictionManager, "_emit", lambda self, e, d: emitted.append((e, d)))
    monkeypatch.setattr(pm.PredictionManager, "_dispatch", lambda self, k, d: dispatched.append((k, d["status"])))
    import modules.chatbot as chatbot
    import modules.chat_banner as chat_banner

    monkeypatch.setattr(chatbot, "dispatch_chatbot_response", lambda msg, platforms: chat.append((msg, platforms)))
    monkeypatch.setattr(chat_banner, "emit_chat_banner", lambda msg, duration, source: banners.append((msg, duration, source)))
    monkeypatch.setattr(pm, "list_prediction_template_names", lambda: ["predictions", "other"])
    return SimpleNamespace(store=store, twitch=twitch, emitted=emitted, dispatched=dispatched, chat=chat, banners=banners)


def update(status, *, points=(1000, 500), users=(3, 2), winner=None, pid="p1"):
    return {"id": pid, "title": "Win?", "status": status, "started_at": 1.0, "locks_at": None, "locked_at": None,
            "ended_at": 5.0 if status in ("resolved", "canceled") else None, "winning_outcome_id": winner,
            "outcomes": [
                {"id": "t1", "name": "Yes", "color": "blue", "users": users[0], "points": points[0],
                 "top_predictors": [{"name": "Ann", "points_used": 800, "points_won": 1200}]},
                {"id": "t2", "name": "No", "color": "pink", "users": users[1], "points": points[1], "top_predictors": []},
            ]}


def test_config_defaults_outcome_editing_and_persistence(env):
    m = pm.PredictionManager()
    cfg = m.get_config("predictions")
    assert [o["name"] for o in cfg["outcomes"]] == ["Yes", "No"] and cfg["window_seconds"] == 120
    m.update_config("predictions", title="  Will we win?  ", window_seconds=5, bogus=1, last_result={"x": 1})
    cfg = m.get_config("predictions")
    assert cfg["title"] == "Will we win?" and cfg["window_seconds"] == 30 and "bogus" not in cfg and cfg["last_result"] is None
    assert m.add_outcome("predictions") == (True, "")
    third = m.get_config("predictions")["outcomes"][2]
    m.rename_outcome("predictions", third["id"], "Maybe " * 10)
    assert m.get_config("predictions")["outcomes"][2]["name"] == ("Maybe " * 10)[:25].rstrip()
    assert m.remove_outcome("predictions", third["id"]) == (True, "")
    first = m.get_config("predictions")["outcomes"][0]["id"]
    assert m.remove_outcome("predictions", first)[0] is False  # at least two
    for _ in range(8):
        m.add_outcome("predictions")
    assert m.add_outcome("predictions")[0] is False  # at most ten
    path, payload = env.store["writes"][-1]
    assert path == pm.DATA_PATH and len(payload["templates"]["predictions"]["config"]["outcomes"]) == 10
    # Stored setups load back.
    env.store["data"] = payload
    assert len(pm.PredictionManager().get_config("predictions")["outcomes"]) == 10


def test_failed_load_never_overwrites(env):
    env.store["ok"] = False
    m = pm.PredictionManager()
    m.update_config("predictions", title="New")
    assert env.store["writes"] == []  # still unreadable: nothing written
    env.store["ok"] = True
    env.store["data"] = {"templates": {"stored": {"config": {"title": "Kept"}}}, "last_template": "stored"}
    m.update_config("predictions", title="New 2")
    templates = env.store["writes"][-1][1]["templates"]
    assert templates["stored"]["config"]["title"] == "Kept" and templates["predictions"]["config"]["title"] == "New 2"


def test_start_lock_resolve_lifecycle(env):
    m = pm.PredictionManager()
    m.update_config("predictions", title="Win?")
    ok, msg = m.start_prediction("predictions")
    assert ok and m.live_template() == "predictions"
    event, snap = env.emitted[-1]
    assert event == "prediction_state" and snap["status"] == "active" and snap["prediction_id"] == "p1"
    assert snap["locks_at"] - m.live()["started_at"] == 120  # created_at + prediction_window
    assert env.dispatched[-1] == ("begin", "active")
    assert m.start_prediction("other") == (False, "A prediction is already running (“predictions”). End it first.")
    assert m.get_last_error("other").startswith("A prediction is already running")

    m.apply_twitch_update(update("active"), kind="progress")
    snap = m.get_overlay_state("predictions")
    yes, no = snap["outcomes"]
    assert (snap["total_points"], snap["total_users"]) == (1500, 5)
    assert (yes["percent"], no["percent"], yes["ratio"], no["ratio"], yes["user_percent"]) == (66.7, 33.3, 1.5, 3.0, 60.0)
    assert snap["title"] == "Win?"

    assert m.lock_prediction() == (True, "Bets locked.")
    assert env.twitch.ends[-1] == ("p1", "LOCKED", None) and m.get_overlay_state("predictions")["status"] == "locked"
    assert m.lock_prediction()[1] == "Bets are already locked."
    assert m.resolve_prediction("nope") == (False, "No outcome matches “nope”.")
    assert m.resolve_prediction("no") == (True, "“No” won.") and env.twitch.ends[-1] == ("p1", "RESOLVED", "t2")
    assert m.resolve_prediction("1")[1] == "“Yes” won." and m.resolve_prediction("t1")[0]

    m.apply_twitch_update(update("resolved", winner="t1"), kind="end")
    events = [e for e, _ in env.emitted]
    assert events[-2:] == ["prediction_state", "prediction_ended"]
    final = env.emitted[-1][1]
    assert final["status"] == "resolved" and [o["winner"] for o in final["outcomes"]] == [True, False]
    assert env.chat == [("Yes won! 1,500 points go to 3 predictors.", ["twitch"])] and env.banners == []
    last = m.get_config("predictions")["last_result"]
    assert (last["winner"], last["points"], last["users"], last["status"]) == ("Yes", 1500, 5, "resolved")
    assert env.dispatched[-1] == ("end", "resolved")
    assert m.live_template() == "" and m.lock_prediction() == (False, "No prediction is running.")
    # A repeated end event doesn't announce twice.
    m.apply_twitch_update(update("resolved", winner="t1"), kind="end")
    assert len(env.chat) == 1


def test_cancel_announces_the_refund_on_the_banner(env):
    m = pm.PredictionManager()
    m.update_config("predictions", title="Win?", announce_chat=False, announce_banner=True, banner_duration=7)
    assert m.start_prediction("predictions")[0]
    assert m.cancel_prediction(template="other") == (False, "This overlay isn't running the prediction.")
    assert m.cancel_prediction() == (True, "Prediction canceled; points refunded.")
    m.apply_twitch_update(update("canceled"), kind="end")
    assert env.banners == [("The prediction “Win?” was canceled and everyone got their points back.", 7.0, "prediction")]


def test_start_refusals(env):
    m = pm.PredictionManager()
    assert m.start_prediction("predictions")[1] == "Give the prediction a question."
    m.update_config("predictions", title="Win?")
    env.twitch.ready = "missing"
    assert "sign in" in m.start_prediction("predictions")[1].lower()
    env.twitch.ready = "ok"
    env.twitch.create_result = (False, "Twitch said: nope")
    assert m.start_prediction("predictions") == (False, "Twitch said: nope")
    env.twitch.create_result = (True, {"id": "p1", "outcomes": []})
    env.twitch.end_result = (False, "Twitch said: no")
    assert m.start_prediction("predictions")[0]
    assert m.lock_prediction() == (False, "Twitch said: no")
    assert m.get_overlay_state("predictions")["status"] == "active"


def test_dashboard_prediction_lands_on_the_last_used_overlay_and_sync(env):
    m = pm.PredictionManager()
    m.apply_twitch_update(update("active", pid="dash"), kind="begin")
    assert m.live_template() == "predictions"  # first overlay with a widget
    assert m.get_overlay_state("other")["status"] == "idle"
    m.apply_twitch_update({"id": ""})  # ignored
    env.twitch.current = {"id": "dash2", "status": "LOCKED", "title": "T", "outcomes": []}
    assert m.sync_from_twitch() is True and m.live()["id"] == "dash2" and m.live()["status"] == "locked"
    env.twitch.current = {"id": "old", "status": "RESOLVED", "outcomes": []}
    assert m.sync_from_twitch() is False
    env.twitch.current = None
    assert m.sync_from_twitch() is False


def test_lock_event_keeps_known_times(env):
    m = pm.PredictionManager()
    m.update_config("predictions", title="Win?")
    m.start_prediction("predictions")
    locks_at = m.live()["locks_at"]
    m.apply_twitch_update({**update("locked"), "locks_at": None, "started_at": None}, kind="lock")
    assert m.live()["locks_at"] == locks_at and m.get_overlay_state("predictions")["remaining_seconds"] is None


def test_format_message():
    snap = {"title": "Win?", "total_points": 12345, "total_users": 9, "outcomes": [
        {"name": "Yes", "users": 4, "winner": True, "top_predictors": [{"name": "Ann", "points_used": 10, "points_won": 50},
                                                                       {"name": "Bo", "points_used": 99, "points_won": 20}]}]}
    assert pm.format_message("{winner}|{points}|{winners}|{users}|{top}|{title}", snap) == "Yes|12,345|4|9|Ann|Win?"
    assert pm.format_message("{winner} {top}", {"outcomes": []}) == ""


def test_widget_scan(monkeypatch, tmp_path):
    import modules.path_utils as pu
    import modules.spore_studio.template_parser_back as tpb

    (tmp_path / "a.spore.json").write_text("{}")
    (tmp_path / "b.spore.json").write_text("{}")
    (tmp_path / "_hidden.spore.json").write_text("{}")
    models = {"a": {"elements": [{"type": "container", "children": [{"type": "Prediction"}]}]}, "b": {"elements": [{"type": "poll"}]}}
    monkeypatch.setattr(pu, "get_template_path", lambda rel: str(tmp_path))
    monkeypatch.setattr(tpb, "load_sidecar", lambda stem: models.get(stem))
    assert pm.list_prediction_template_names() == ["a"]
    assert pm.has_prediction_widget(None) is False


def test_side_effect_failures_never_raise(env, monkeypatch):
    import modules.chatbot as chatbot

    m = pm.PredictionManager(persist=False)
    m.update_config("predictions", title="Win?")
    monkeypatch.setattr(chatbot, "dispatch_chatbot_response", lambda *a: 1 / 0)
    m.start_prediction("predictions")
    m.apply_twitch_update(update("resolved", winner="t2"), kind="end")
    assert env.store["writes"] == []  # persist=False


def test_real_emit_and_dispatch(monkeypatch):
    import modules.connector_integration as ci
    import modules.web_engine as we

    sent, hooks = [], []
    monkeypatch.setattr(we, "web_engine_instance", SimpleNamespace(safe_emit=lambda e, d: sent.append(e)))
    monkeypatch.setattr(ci, "handle_twitch_prediction", lambda kind, snap: hooks.append(kind), raising=False)
    m = pm.PredictionManager(persist=False)
    m._emit("prediction_state", {})
    m._dispatch("begin", {})
    assert sent == ["prediction_state"] and hooks == ["begin"]
    monkeypatch.setattr(we, "web_engine_instance", SimpleNamespace(safe_emit=lambda e, d: 1 / 0))
    m._emit("x", {})


def test_singleton(monkeypatch):
    monkeypatch.setattr(pm, "_manager", None)
    monkeypatch.setattr(pm, "PredictionManager", lambda: "made")
    assert pm.get_prediction_manager() == "made" and pm.get_prediction_manager() == "made"

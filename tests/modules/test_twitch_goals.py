# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch_goals.py: normalising goals, EventSub/Helix state, overlay emits and goal-bar templates."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import modules  # noqa: E402
from modules import path_utils, template_config_parser, twitch_goals as tg  # noqa: E402
from modules.spore_studio import containers, save_pipeline as sp, template_outputs, template_parser_back, template_reverse_parser  # noqa: E402


class FakeApi:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.is_connected = True
        self.user_id = "42"
        self.auth_token = "tok"

    async def generic_api_call(self, url, method, params=None, json_data=None):
        if self.error:
            raise self.error
        return self.response


@pytest.fixture
def env(monkeypatch):
    import modules.twitch as twitch
    import modules.web_engine as we

    tg.reset_for_tests()
    sent = []
    api = FakeApi()
    monkeypatch.setattr(twitch, "get_twitch_api", lambda: api)
    monkeypatch.setattr(we, "web_engine_instance", SimpleNamespace(safe_emit=lambda e, d, to=None: sent.append((e, d.get("id"), to))))
    yield SimpleNamespace(api=api, sent=sent)
    tg.reset_for_tests()


ROW = {"id": "g1", "type": "follower", "description": "Road to 500", "current_amount": 125, "target_amount": 500,
       "created_at": "2026-01-01T00:00:00Z"}


def test_normalize():
    goal = tg.normalize(ROW)
    assert goal == {"id": "g1", "type": "follower", "type_label": "Followers", "description": "Road to 500", "current_amount": 125,
                    "target_amount": 500, "percent": 25.0, "is_achieved": False, "status": "active",
                    "started_at": goal["started_at"], "ended_at": None}
    event = SimpleNamespace(id="g2", type=SimpleNamespace(value="new_subscription_count"), description="", current_amount=12,
                            target_amount=10, is_achieved=True, started_at="2026-01-01T00:00:00Z", ended_at="2026-01-02T00:00:00Z")
    ended = tg.normalize(event, ended=True)
    assert (ended["percent"], ended["is_achieved"], ended["status"], ended["type_label"]) == (100.0, True, "ended", "New subscribers")
    assert ended["ended_at"] > ended["started_at"]
    assert tg.normalize({"id": "x", "type": "mystery_goal", "target_amount": 0})["type_label"] == "Mystery Goal"


def test_events_update_state_and_overlays(env):
    tg.handle_event("begin", ROW)
    tg.handle_event("progress", {**ROW, "current_amount": 200})
    assert tg.active_goals()[0]["current_amount"] == 200
    tg.handle_event("end", {**ROW, "is_achieved": False})
    assert tg.active_goals() == []
    assert [e for e, _id, _to in env.sent] == ["twitch_goal_begin", "twitch_goal_progress", "twitch_goal_end"]
    tg.handle_event("progress", {"type": "follower"})  # no id: ignored
    assert len(env.sent) == 3


def test_refresh_and_send_state(env):
    env.api.response = {"data": [ROW, {**ROW, "id": "g2", "type": "subscription"}, "junk"]}
    assert tg.refresh_from_twitch() == (True, "")
    assert {g["id"] for g in tg.active_goals()} == {"g1", "g2"}
    assert [e for e, _i, _t in env.sent] == ["twitch_goal_sync", "twitch_goal_sync"]
    env.sent.clear()
    tg.send_state("sid-1")
    assert {(e, to) for e, _i, to in env.sent} == {("twitch_goal_sync", "sid-1")}
    env.api.error = RuntimeError("401 Unauthorized: missing scope")
    ok, msg = tg.refresh_from_twitch()
    assert ok is False and "sign in to twitch again" in msg.lower()
    env.api.error = RuntimeError("boom")
    assert tg.refresh_from_twitch() == (False, "Couldn't read your Twitch goals: boom")
    assert {g["id"] for g in tg.active_goals()} == {"g1", "g2"}  # a failed read keeps what was known


def test_scope_status(monkeypatch, env):
    import modules.twitch_token_auth as tta

    scopes = {"value": frozenset({tg.GOAL_SCOPE})}
    monkeypatch.setattr(tta, "token_scopes", lambda token: scopes["value"])
    assert tg.scope_status() == "ok"
    scopes["value"] = frozenset()
    assert tg.scope_status() == "missing"
    checks = []
    monkeypatch.setattr(tg, "check_scopes_in_background", lambda: checks.append(1))
    scopes["value"] = None
    assert tg.scope_status() == "unknown" and checks == [1]
    env.api.is_connected = False
    assert tg.scope_status() == "offline"


def test_scope_notice(monkeypatch):
    import modules.notification_engine as ne

    notes = []
    monkeypatch.setattr(ne, "notify", lambda *a, **k: notes.append(k))
    tg.notify_scope_missing()
    assert notes[0]["dedupe_key"] == "twitch:goal_scope_missing"
    monkeypatch.setattr(ne, "notify", lambda *a, **k: 1 / 0)
    tg.notify_scope_missing()


def test_emit_failures_are_quiet(monkeypatch):
    import modules.web_engine as we

    monkeypatch.setattr(we, "web_engine_instance", SimpleNamespace(safe_emit=lambda *a, **k: 1 / 0))
    tg._emit("twitch_goal_sync", {})
    monkeypatch.setattr(we, "web_engine_instance", None)
    tg._emit("twitch_goal_sync", {})


def test_template_model_feeds_follow_one_goal_type():
    model = tg.goal_template_model("goal_bar", "subscription_count", "Sub goal")
    by_id = {el["id"]: el for el in model["elements"]}
    assert set(by_id) == {"goal_label", "goal_amount", "goal_bar"} and model["alert_system"] == "instant"
    bar_feeds = by_id["goal_bar"]["data_feed"]
    assert [(f["target"]["id"], f["mapping"]["value_field"]) for f in bar_feeds] == [("set_value", "current_amount"),
                                                                                       ("set_max", "target_amount")]
    for el in model["elements"]:
        for feed in el["data_feed"]:
            assert feed["source"]["events"] == list(tg.GOAL_EVENTS)
            assert feed["filters"]["rules"] == [{"field": "type", "op": "eq", "value": "subscription_count"}]
    assert by_id["goal_label"]["props"]["text"] == "Sub goal"
    assert by_id["goal_amount"]["data_feed"][0]["mapping"]["template"] == "{current_amount|number} / {target_amount|number}"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    templates = tmp_path / "templates"
    assets = tmp_path / "assets"
    (templates / "template_configs").mkdir(parents=True)
    assets.mkdir()

    def tpl_path(rel=""):
        return os.path.join(str(templates), rel) if rel else str(templates)

    def assets_path(rel=""):
        return os.path.join(str(assets), rel) if rel else str(assets)

    for mod in (sp, template_parser_back, template_reverse_parser, containers):
        monkeypatch.setattr(mod, "get_template_path", tpl_path)
    monkeypatch.setattr(path_utils, "get_template_path", tpl_path)
    monkeypatch.setattr(sp, "get_assets_path", assets_path)
    monkeypatch.setattr(path_utils, "get_assets_path", assets_path)
    monkeypatch.setattr(template_config_parser, "get_data_path", lambda rel: str(tmp_path / rel))
    monkeypatch.setattr(modules, "web_engine", SimpleNamespace(web_engine_instance=None), raising=False)
    template_outputs.invalidate_cache()
    yield templates
    template_outputs.invalidate_cache()


def test_create_goal_template_writes_a_spore_template(sandbox, env):
    tg.handle_event("begin", ROW)
    env.sent.clear()
    ok, name = tg.create_goal_template("follower")
    assert (ok, name) == (True, "twitch_goal_followers")
    html = (sandbox / "twitch_goal_followers.html").read_text()
    sidecar = json.loads((sandbox / "_spore" / "twitch_goal_followers.spore.json").read_text())
    assert "goal_bar" in html and "twitch_goal_progress" in html
    assert sidecar["elements"][2]["data_feed"][1]["target"]["id"] == "set_max"
    assert (sandbox / "template_configs" / "twitch_goal_followers.json").exists()
    assert env.sent == [("twitch_goal_sync", "g1", None)]  # the new overlay is filled at once
    ok, second = tg.create_goal_template("follower", label="Follow goal")
    assert (ok, second) == (True, "twitch_goal_followers_2")
    assert tg.create_goal_template("nope") == (False, "Unknown goal type 'nope'.")
    ok, msg = tg.create_goal_template("follower", name="bad name!")
    assert ok is False and msg

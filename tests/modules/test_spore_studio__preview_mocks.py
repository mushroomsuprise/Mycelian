# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/preview_mocks.py (preview toolbar mock payloads)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

import modules
from modules.spore_studio import event_registry, template_outputs
from modules.spore_studio import preview_mocks as pm
from modules.uiwindows import activity_feed

POOLS = {
    "usernames": ("Alice", "Bob", "Cara", "Dan", "Eve", "Fay", "Gus", "Hal", "Ivy"),
    "chat": ("hi", "gg"),
    "colors": ("#111111",),
    "alerts": ({"alert_type": "sub", "tier": "1000"},),
    "titles": ("Celeste",),
}


@pytest.fixture(autouse=True)
def fixed_pools(monkeypatch):
    monkeypatch.setattr(pm, "_demo_pools", lambda: POOLS)
    monkeypatch.setattr(pm, "_preview_queue_seq", 0)


class Rng:
    """Deterministic stand-in for the ``random`` module calls preview_mocks uses."""

    def __init__(self, randoms=(0.9,), pick=0):
        self._randoms = list(randoms)
        self.pick = pick

    def random(self):
        return self._randoms.pop(0) if len(self._randoms) > 1 else self._randoms[0]

    def choice(self, seq):
        return list(seq)[self.pick % len(seq)]

    def randint(self, a, b):
        return a

    def uniform(self, a, b):
        return a

    def sample(self, seq, k):
        return list(seq)[:k]


def _rng(monkeypatch, **kw):
    rng = Rng(**kw)
    monkeypatch.setattr(pm, "random", rng)
    return rng


# ---------------------------------------------------------------- pools

def test_demo_pools_reads_web_engine(monkeypatch):
    monkeypatch.undo()
    fake = SimpleNamespace(_DEMO_USERNAMES=("X",), _DEMO_CHAT_MESSAGES=("m",))
    monkeypatch.setattr(modules, "web_engine", fake, raising=False)
    pools = pm._demo_pools()
    assert pools["usernames"] == ("X",) and pools["chat"] == ("m",)
    assert pools["colors"] == pm._FALLBACK_POOLS["colors"]
    assert pools["alerts"] == pm._FALLBACK_POOLS["alerts"]
    assert pools["titles"] == pm._FALLBACK_POOLS["titles"]


def test_queue_seq_counts_down():
    assert pm._assign_preview_alert_queue_seq() == -1
    assert pm._assign_preview_alert_queue_seq() == -2


# ---------------------------------------------------------------- simple payloads

def test_alert_payload(monkeypatch):
    _rng(monkeypatch, pick=1)
    monkeypatch.setattr(pm.time, "time", lambda: 100.0)
    p = pm._alert_payload({"alerts": ({"alert_type": "a"}, {"alert_type": "b", "duration": 9}),
                           "usernames": ("u1", "u2")})
    assert p == {"alert_type": "b", "duration": 9, "username": "u2", "timestamp": 100.0}


@pytest.mark.parametrize("randoms,expected", [((0.1, 0.9), "/me waves to chat"),
                                              ((0.5, 0.1, 0.9), "/notacommand hello"),
                                              ((0.5, 0.5, 0.5), "hi")])
def test_chat_payload_message_variants(monkeypatch, randoms, expected):
    _rng(monkeypatch, randoms=randoms)
    p = pm._chat_payload(POOLS)
    assert p["message"] == expected
    assert p["type"] == "chat" and p["username"] == "Alice" and p["userid"] == "10000000"
    assert "reply" not in p


def test_chat_payload_with_reply(monkeypatch):
    _rng(monkeypatch, randoms=(0.5, 0.5, 0.1))
    p = pm._chat_payload(POOLS)
    assert p["reply"]["parent_user_login"] == "alice"
    assert p["reply"]["parent_message_body"] == "@Alice hi"


def test_chat_reply_payload(monkeypatch):
    _rng(monkeypatch)
    p = pm._chat_reply_payload(POOLS)
    assert p["message"] == "@utbsb oh yeah"
    assert p["reply"]["parent_user_login"] == "utbsb"
    assert p["id"] == p["twmsgid"]


def test_connector_chat_payload(monkeypatch):
    _rng(monkeypatch, randoms=(0.1,))
    p = pm._connector_chat_payload(POOLS)
    assert p == {"username": "Alice", "message_text": "hi", "message": "hi", "text": "hi",
                 "is_moderator": True}


@pytest.mark.parametrize(
    "atype,keys",
    [("follow", set()), (None, set()), (" SUB ", {"tier"}),
     ("resub", {"tier", "cumulative_months", "message"}), ("giftsub", {"tier", "gift_qty"}),
     ("bit", {"amt_cheered", "message"}), ("raid", {"raider_count"}),
     ("donation", {"amount", "currency", "message"}), ("point", {"alert_name", "message"}),
     ("streak", {"streak_count", "channel_points_awarded"}), ("hype_train", {"level", "total"}),
     ("unknown", set())],
)
def test_typed_alert_fields(atype, keys):
    assert set(pm._typed_alert_fields(atype)) == keys


def test_random_tier():
    assert pm._random_tier() in ("1000", "2000", "3000")


def test_build_typed_alert_payload_queue_and_instant():
    q = pm.build_typed_alert_payload("queue", "Raid", POOLS)
    assert q["alert_type"] == "raid" and q["queue_seq"] == -1 and "raider_count" in q
    i = pm.build_typed_alert_payload(" Instant ", None)
    assert i["alert_type"] == "follow" and "queue_seq" not in i
    assert i["username"] in POOLS["usernames"]


def test_next_alert_queue_payload_skips_follows(monkeypatch):
    calls = iter([{"alert_type": "follow"}, {"alert_type": "follow"}, {"alert_type": "raid"}])
    monkeypatch.setattr(pm, "_alert_payload", lambda pools: dict(next(calls)))
    p = pm._next_alert_queue_payload(POOLS)
    assert p == {"alert_type": "raid", "queue_seq": -1}


def test_next_alert_queue_payload_gives_up_after_ten(monkeypatch):
    n = []
    monkeypatch.setattr(pm, "_alert_payload", lambda pools: n.append(1) or {"alert_type": "follow"})
    p = pm._next_alert_queue_payload(POOLS)
    assert len(n) == 10 and p["alert_type"] == "follow"


def test_activity_feed_alert_payload(monkeypatch):
    monkeypatch.setattr(activity_feed, "iter_activity_feed_preview_payloads", lambda: iter([{"a": 1}]))
    assert pm._activity_feed_alert_payload(POOLS) == {"a": 1}
    monkeypatch.setattr(activity_feed, "iter_activity_feed_preview_payloads", lambda: iter([]))
    assert pm._activity_feed_alert_payload(POOLS) == {"message": "Preview activity"}


def test_giveaway_payloads(monkeypatch):
    _rng(monkeypatch)
    assert pm._giveaway_keywords() == ("!join", "!enter", "!giveaway", "!raffle")
    assert pm._giveaway_start_payload(POOLS) == {"keyword": "!join"}
    entry = pm._giveaway_entry_payload(POOLS)
    assert entry["username"] == "Alice" and entry["pool_entries"][0] == "Alice"
    assert entry["pool_size"] == 7 and "Alice" not in entry["pool_entries"][1:]
    sync = pm._giveaway_sync_payload(POOLS)
    assert sync["pool_size"] == 8 and sync["accepting"] is True


def test_giveaway_winner_payload(monkeypatch):
    _rng(monkeypatch)
    w = pm._giveaway_winner_payload(POOLS)
    assert w["pool_size"] == 5 and w["winners"] == ["Alice"]
    small = pm._giveaway_winner_payload({"usernames": ("Solo",)})
    assert small["pool_entries"] == ["Solo", "PreviewUser", "MockViewer", "SampleChat"]


def test_poll_payload_open_and_ended(monkeypatch):
    monkeypatch.setattr(pm.time, "time", lambda: 1000.0)
    open_poll = pm._poll_payload(False)
    assert open_poll["status"] == "open" and open_poll["ends_at"] == 1075.0
    assert open_poll["winners"] == [] and len(open_poll["options"]) == 4
    votes = [o["votes"] for o in open_poll["options"]]
    assert votes == sorted(votes, reverse=True) and open_poll["total_votes"] == sum(votes)
    ended = pm._poll_payload(True)
    assert ended["status"] == "ended" and ended["remaining_seconds"] is None
    if ended["options"][0]["votes"]:
        assert ended["winners"] == [ended["options"][0]]


def test_poll_payload_zero_votes(monkeypatch):
    _rng(monkeypatch)  # randint -> 0
    p = pm._poll_payload(True)
    assert p["total_votes"] == 0 and all(o["percent"] == 0 for o in p["options"])
    assert p["winners"] == []


def test_misc_payloads(monkeypatch):
    _rng(monkeypatch)
    assert pm._streamer_info_payload(POOLS)["streamer_name"] == "PreviewStreamer"
    assert pm._twitch_category_payload(POOLS) == {"current_category": "Celeste"}
    assert pm._bitbar_add_payload(POOLS) == {"amount": 50}
    assert pm._counter_tick_payload(POOLS) == {"message": "0"}
    assert pm._moderation_payload()["action"] == "delete"
    api = pm._twitch_api_response_payload()
    assert api["success"] is True and api["data"]["data"][0]["login"] == "previewuser"


@pytest.mark.parametrize("pick,expected_type,extra", [(0, "sub", None), (2, "giftsub", "quantity"),
                                                      (3, "resub", "months")])
def test_subbar_instant_payload(monkeypatch, pick, expected_type, extra):
    _rng(monkeypatch, pick=pick)
    p = pm._subbar_instant_payload(POOLS)
    assert p["alert_type"] == expected_type and p["tier"] == "1000" and p["is_replay"] is False
    if extra:
        assert extra in p


# ---------------------------------------------------------------- build_mock_payload

def test_every_builder_produces_a_payload(monkeypatch):
    monkeypatch.setattr(activity_feed, "iter_activity_feed_preview_payloads", lambda: iter([{"x": 1}]))
    for name in pm.supported_events():
        out = pm.build_mock_payload(name)
        assert out is not None, name
        socket_event, payload = out
        assert socket_event == pm._BUILDERS[name][0]
        assert isinstance(payload, dict)
    assert pm.build_mock_payload("subbar_instant_alert")[0] == "instant_alert"
    assert pm.build_mock_payload("pause_status_on") == ("pause_status_update", {"paused": True})


def test_build_mock_payload_typed_variants(monkeypatch):
    monkeypatch.setattr(activity_feed, "build_typed_activity_feed_preview_payload",
                        lambda at: {"typed": at})
    assert pm.build_mock_payload("activity_feed_alert", alert_type="raid") == (
        "activity_feed_alert", {"typed": "raid"})
    ev, body = pm.build_mock_payload(" new-message ", alert_type="reply")
    assert ev == "new-message" and body["reply"]["parent_user_login"] == "utbsb"
    ev, body = pm.build_mock_payload("next_alert", alert_type="bit")
    assert ev == "next_alert" and "amt_cheered" in body and body["queue_seq"] < 0
    ev, body = pm.build_mock_payload("instant_alert", alert_type="sub")
    assert ev == "instant_alert" and "queue_seq" not in body


def test_build_mock_payload_template_output_and_unknown(monkeypatch):
    monkeypatch.setattr(template_outputs, "sample_envelope",
                        lambda ev: ("template_output", {"event": ev}) if ev == "wheel_result" else None)
    assert pm.build_mock_payload("wheel_result") == ("template_output", {"event": "wheel_result"})
    assert pm.build_mock_payload("nothing") is None
    assert pm.build_mock_payload(None) is None


def test_template_output_mock_delegates(monkeypatch):
    monkeypatch.setattr(template_outputs, "sample_envelope", lambda ev: ("x", ev))
    assert pm._template_output_mock("e") == ("x", "e")


# ---------------------------------------------------------------- bound events / labels

def test_collect_bound_events():
    model = {"elements": [
        "junk",
        {"bindings": [{"event": "chat"}, "bad", {"event": " chat "}, {"event": ""}, {"event": 5}],
         "counter": {"rules": [{"event": "bits"}, "bad"]},
         "data_display": {"refresh_on": ["refresh_a", None]},
         "data_feed": [
             {"enabled": False, "source": {"kind": "event", "events": ["disabled_ev"]}},
             "bad",
             {"source": {"kind": "event", "events": ["feed_ev"]}},
             {"source": {"kind": "template_output", "template_output": {"event": "wheel_result"}}},
             {"source": {"kind": "template_output", "template_output": "bad"}},
             {"source": {"kind": "value", "refresh_on": ["val_ev"]}},
             {"source": "not-a-dict"},
         ]},
        {"counter": "bad", "data_display": "bad", "data_feed": {"not": "list"}},
    ]}
    assert pm._collect_bound_events(model) == [
        "chat", "bits", "refresh_a", "feed_ev", "wheel_result", "val_ev"]
    assert pm._collect_bound_events({}) == []


def test_registry_event_label(monkeypatch):
    assert pm._registry_event_label("next_alert") == event_registry.get_event("next_alert")["label"]
    assert pm._registry_event_label("my_cool-event") == "My Cool Event"

    def boom(name):
        raise RuntimeError

    monkeypatch.setattr(event_registry, "get_event", boom)
    assert pm._registry_event_label("x_y") == "X Y"


def test_derive_preview_mocks():
    assert pm.derive_preview_mocks("x") == []
    model = {"alert_system": "weird", "elements": [{"bindings": [
        {"event": "next_alert"}, {"event": "subbar_instant_alert"}, {"event": "poll_state"}]}]}
    actions = pm.derive_preview_mocks(model)
    assert actions[0] == {"event": "next_alert", "label": "Random queue alert"}
    typed = actions[1:1 + len(pm._PREVIEW_ALERT_TYPES)]
    assert [a["alert_type"] for a in typed] == [k for k, _ in pm._PREVIEW_ALERT_TYPES]
    assert all(a["event"] == "next_alert" for a in typed)
    assert [a["event"] for a in actions[1 + len(pm._PREVIEW_ALERT_TYPES):]] == ["poll_state"]
    instant = pm.derive_preview_mocks({"alert_system": "INSTANT"})
    assert instant[0] == {"event": "instant_alert", "label": "Random instant alert"}
    assert len(instant) == 1 + len(pm._PREVIEW_ALERT_TYPES)


def test_supported_events_matches_builders():
    assert pm.supported_events() == tuple(pm._BUILDERS)

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch.py, part 2: Helix session checks, message caches, fragment
serialization, cheer cache, power-ups (gigantified emotes) and hype train type parsing."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

import pytest  # noqa: E402

from modules import twitch as tw  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    monkeypatch.setattr(tw, "_cheer_bits_cache", {})
    monkeypatch.setattr(tw, "_alert_user_message_cache", {})


class _Enum:
    def __init__(self, value):
        self.value = value


# --------------------------------------------------------------------------- Helix session


def test_ephemeral_client_session_opens_and_closes():
    async def go():
        async with tw._ephemeral_client_session() as session:
            assert not session.closed
            inner = session
        return inner

    session = asyncio.run(go())
    assert session.closed


def test_helix_session_usable_basic_cases():
    assert tw._helix_session_usable(None) is False
    assert tw._helix_session_usable(SimpleNamespace(closed=True)) is False

    class BadClosed:
        @property
        def closed(self):
            raise RuntimeError("x")

    assert tw._helix_session_usable(BadClosed()) is False
    # No running loop.
    assert tw._helix_session_usable(SimpleNamespace(closed=False)) is False


def test_helix_session_usable_loop_checks():
    async def go():
        running = asyncio.get_running_loop()
        other = asyncio.new_event_loop()
        try:
            same = tw._helix_session_usable(SimpleNamespace(closed=False, _loop=running))
            via_connector = tw._helix_session_usable(
                SimpleNamespace(closed=False, _loop=None, connector=SimpleNamespace(_loop=running)))
            different = tw._helix_session_usable(SimpleNamespace(closed=False, _loop=other))
            no_loop = tw._helix_session_usable(SimpleNamespace(closed=False, _loop=None, connector=None))
        finally:
            other.close()
        closed = tw._helix_session_usable(SimpleNamespace(closed=False, _loop=other))
        return same, via_connector, different, no_loop, closed

    assert asyncio.run(go()) == (True, True, False, True, False)


# --------------------------------------------------------------------------- alert user messages


def test_alert_user_message_cache(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(tw.time, "time", lambda: now[0])
    tw.register_alert_user_message("", "x")
    tw.register_alert_user_message("1", "  ")
    assert tw._alert_user_message_cache == {}
    tw.register_alert_user_message("1", " hello ")
    assert tw.is_alert_user_message("1", "hello") is True
    assert tw.is_alert_user_message("1", "other") is False
    now[0] += 31
    assert tw.is_alert_user_message("1", "hello") is False
    assert tw._alert_user_message_cache == {}


def test_subscription_emotes_to_json():
    assert tw.subscription_emotes_to_json(None) is None
    out = tw.subscription_emotes_to_json([
        {"begin": "0", "end": 3, "id": 25},
        SimpleNamespace(begin=5, end=8, id="x"),
        {"begin": None, "end": 1, "id": 1},
    ])
    assert out == [{"begin": 0, "end": 3, "id": "25"}, {"begin": 5, "end": 8, "id": "x"}]
    assert tw.subscription_emotes_to_json([{"begin": 1}]) is None


def test_fragment_and_nested_attr():
    assert tw._fragment_attr({"a": 1}, "a") == 1
    assert tw._fragment_attr(SimpleNamespace(a=2), "a") == 2
    assert tw._fragment_attr(SimpleNamespace(), "a", "d") == "d"
    assert tw._nested_attr(None, "x") is None
    assert tw._nested_attr({"a": "", "b": "B"}, "a", "b") == "B"
    assert tw._nested_attr(SimpleNamespace(a=None, b=3), "a", "b") == 3
    assert tw._nested_attr({}, "a") is None


# --------------------------------------------------------------------------- chat fragments


def test_serialize_chat_message_fragments_all_types():
    frags = [
        {"type": "text", "text": "hi "},
        SimpleNamespace(type="emote", text="Kappa", emote=SimpleNamespace(id="25", name="Kappa")),
        {"type": "cheermote", "text": "cheer100", "cheermote": {"bits": 100, "tier": None}},
        {"type": "mention", "text": "@bob", "mention": {"user_id": "7", "user_name": "bob"}},
        {"type": "text", "text": "pic", "url": "https://x/y.gif"},
        {"type": None, "text": None, "gif": SimpleNamespace(url=5, gif_id=None, id="g1")},
    ]
    result, gif_url = tw.serialize_chat_message_fragments(frags)
    assert result[0] == {"text": "hi ", "type": "text"}
    assert result[1] == {"text": "Kappa", "type": "emote", "emote_id": "25", "emote_name": "Kappa"}
    assert result[2] == {"text": "cheer100", "type": "cheermote", "bits": 100, "tier": 1}
    assert result[3] == {"text": "@bob", "type": "mention", "user_id": "7", "user_name": "bob"}
    assert result[4] == {"text": "pic", "type": "gif", "url": "https://x/y.gif"}
    assert result[5] == {"text": "", "type": "gif", "url": "5", "gif_id": "g1"}
    assert gif_url == "https://x/y.gif"


def test_serialize_chat_fragments_detects_by_payload_without_type():
    frags = [
        {"text": "a", "type": "text", "emote": {"id": "1", "name": "E"}},
        {"text": "b", "type": "text", "cheermote": {"bits": None, "tier": 2}},
        {"text": "c", "type": "text", "mention": {"user_id": "u"}},
        {"text": "d", "type": "gif"},
    ]
    result, gif_url = tw.serialize_chat_message_fragments(frags)
    assert [r["type"] for r in result] == ["emote", "cheermote", "mention", "gif"]
    assert result[1]["bits"] == 0 and result[1]["tier"] == 2
    assert result[2]["user_name"] is None
    assert "url" not in result[3] and gif_url is None


# --------------------------------------------------------------------------- bits fragments


def test_serialize_bits_message_fragments():
    assert tw.serialize_bits_message_fragments(None) == []
    frags = [
        SimpleNamespace(type="text", text="hey", cheermote=None, emote=None),
        SimpleNamespace(type="cheermote", text="Cheer5", cheermote=SimpleNamespace(prefix="Cheer", bits=5, tier=None)),
        SimpleNamespace(type="cheermote", text="x", cheermote=None),
        SimpleNamespace(type="emote", text="E", emote=SimpleNamespace(id=1, emote_set_id=None)),
        SimpleNamespace(type="emote", text="F", emote=None),
        SimpleNamespace(type=None, text=None, emote=SimpleNamespace(id="2")),
    ]
    out = tw.serialize_bits_message_fragments(frags)
    assert out[0] == {"type": "text", "text": "hey"}
    assert out[1] == {"type": "cheermote", "text": "Cheer5", "prefix": "Cheer", "bits": 5, "tier": 1}
    assert out[2] == {"type": "cheermote", "text": "x", "prefix": "", "bits": 0, "tier": 1}
    assert out[3] == {"type": "emote", "text": "E", "emote_id": "1", "emote_set_id": ""}
    assert out[4] == {"type": "emote", "text": "F", "emote_id": ""}
    assert out[5] == {"type": "emote", "text": "", "emote_id": "2"}


def test_extract_emotes_positions_from_fragments():
    assert tw.extract_emotes_positions_from_fragments("", [1]) is None
    assert tw.extract_emotes_positions_from_fragments("x", None) is None
    frags = [
        SimpleNamespace(type="text", text="hi ", emote=None),
        SimpleNamespace(type="emote", text="Kappa", emote=SimpleNamespace(id=25)),
        SimpleNamespace(type="emote", text="??", emote=None),
        SimpleNamespace(type="text", text=" ", emote=SimpleNamespace(id=None)),
    ]
    assert tw.extract_emotes_positions_from_fragments("hi Kappa?? ", frags) == [
        {"begin": 3, "end": 7, "id": "25"}]
    assert tw.extract_emotes_positions_from_fragments("t", [SimpleNamespace(type="text", text="t", emote=None)]) is None


# --------------------------------------------------------------------------- cheer cache


def test_cheer_bits_cache(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(tw.time, "time", lambda: now[0])
    assert tw._cheer_cache_key("u", 5) == "u:5"
    tw._store_cheer_bits_message("u", 5, "hi", [{"t": 1}], None)
    assert tw.is_alert_user_message("u", "hi") is True
    got = tw._pop_cheer_bits_message("u", 5)
    assert got["text"] == "hi" and got["fragments"] == [{"t": 1}]
    assert tw._pop_cheer_bits_message("u", 5) is None
    tw._store_cheer_bits_message("u", 6, "x", None, None)
    now[0] += 11
    assert tw._pop_cheer_bits_message("u", 6) is None


def test_subscription_message_emotes():
    assert tw._subscription_message_emotes(None) is None
    msg = SimpleNamespace(emotes=[{"begin": 0, "end": 1, "id": 2}])
    assert tw._subscription_message_emotes(msg) == [{"begin": 0, "end": 1, "id": "2"}]


def test_apply_bits_message_to_alert():
    alert = SimpleNamespace(message="old", fragments=["x"], emotes=["y"])
    tw._apply_bits_message_to_alert(alert, None)
    assert alert.message == "old"
    tw._apply_bits_message_to_alert(alert, SimpleNamespace(text="plain", fragments=None))
    assert (alert.message, alert.fragments, alert.emotes) == ("plain", None, None)
    frags = [SimpleNamespace(type="emote", text="K", emote=SimpleNamespace(id=1))]
    tw._apply_bits_message_to_alert(alert, SimpleNamespace(text=None, fragments=frags))
    assert alert.message == "" and alert.fragments[0]["type"] == "emote" and alert.emotes is None
    tw._apply_bits_message_to_alert(alert, SimpleNamespace(text="K", fragments=frags))
    assert alert.emotes == [{"begin": 0, "end": 0, "id": "1"}]


# --------------------------------------------------------------------------- power-ups


@pytest.mark.parametrize(
    "power_up, expected",
    [
        (None, ""),
        ({"type": "Gigantify-An Emote"}, "gigantify_an_emote"),
        ({}, ""),
        (SimpleNamespace(type=_Enum("message_effect")), "message_effect"),
        (SimpleNamespace(type=_Enum("")), "<"),  # blank enum value falls back to str(raw)
        (SimpleNamespace(type="PowerUpType.CELEBRATION"), "celebration"),
    ],
)
def test_power_up_type_token(power_up, expected):
    token = tw._power_up_type_token(power_up)
    if expected == "<":
        assert token.endswith(">")
    else:
        assert token == expected


def test_power_up_emote_identity():
    assert tw._power_up_emote_identity(None) == ("", "")
    assert tw._power_up_emote_identity({"emote": {"id": 1, "name": None}}) == ("1", "")
    assert tw._power_up_emote_identity({"emote": None}) == ("", "")
    assert tw._power_up_emote_identity(SimpleNamespace(emote=SimpleNamespace(id="9", name="E"))) == ("9", "E")
    assert tw._power_up_emote_identity(SimpleNamespace()) == ("", "")


def test_fragments_include_emote_id():
    assert tw._fragments_include_emote_id([{"emote_id": "1"}], "") is False
    assert tw._fragments_include_emote_id("bad", "1") is False
    assert tw._fragments_include_emote_id(["x", {"emote_id": 1}], "1") is True
    assert tw._fragments_include_emote_id([{"emote_id": "2"}], "1") is False


def test_apply_gigantified_without_emote_id():
    alert = SimpleNamespace(message="m", fragments=None, emotes=None)
    tw._apply_gigantified_emote_to_alert(alert, {"type": "gigantify_an_emote", "emote": {}})
    assert alert.is_gigantified_emote is True and alert.gigantified_emote_id == "" and alert.message == "m"


def test_apply_gigantified_uses_id_when_name_missing():
    alert = SimpleNamespace(message="", fragments=None, emotes=None)
    tw._apply_gigantified_emote_to_alert(alert, {"type": "gigantify_an_emote", "emote": {"id": "77"}})
    assert alert.message == "77"
    assert alert.fragments == [{"type": "emote", "text": "77", "emote_id": "77"}]
    assert alert.emotes == [{"begin": 0, "end": 1, "id": "77"}]


# --------------------------------------------------------------------------- hype train type


@pytest.mark.parametrize(
    "event, expected",
    [
        (SimpleNamespace(type=None, is_golden_kappa_train=True), "golden_kappa"),
        (SimpleNamespace(), None),
        (SimpleNamespace(type=_Enum("Treasure")), "treasure"),
        (SimpleNamespace(type=None, type_=" Regular "), "regular"),
        (SimpleNamespace(type=_Enum("  ")), None),
    ],
)
def test_hype_train_type_from_event(event, expected):
    assert tw._hype_train_type_from_event(event) == expected


def test_exceptions_are_exceptions():
    assert issubclass(tw.TwitchSessionNotReadyError, Exception)
    assert issubclass(tw.TwitchPermissionError, Exception)
    assert isinstance(tw.TwitchPermissionError("403"), Exception)
    assert MagicMock  # imported for parity with other parts


def test_serialize_chat_fragments_empty_and_non_str_falsy_url():
    assert tw.serialize_chat_message_fragments([]) == (None, None)
    result, gif_url = tw.serialize_chat_message_fragments([{"type": "text", "text": "a", "url": 0}])
    assert result == [{"text": "a", "type": "text"}] and gif_url is None


def test_apply_gigantified_other_power_up_and_existing_fragment():
    alert = SimpleNamespace(message="m", fragments=[{"emote_id": "5"}], emotes=None)
    tw._apply_gigantified_emote_to_alert(alert, {"type": "message_effect"})
    assert alert.is_gigantified_emote is False and alert.gigantified_emote_name == ""
    tw._apply_gigantified_emote_to_alert(alert, {"type": "gigantify_an_emote", "emote": {"id": "5", "name": "E"}})
    assert alert.is_gigantified_emote is True and alert.message == "m" and alert.fragments == [{"emote_id": "5"}]

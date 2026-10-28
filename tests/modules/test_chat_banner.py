# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/chat_banner.py (banner payload normalization and broadcast)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from types import SimpleNamespace  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

import pytest  # noqa: E402

import modules  # noqa: E402
from modules import chat_banner as cb  # noqa: E402
from modules import template_config_parser  # noqa: E402


def _parser_returning(monkeypatch, config=None, error=None):
    class P:
        def load_config(self, name):
            assert name == "chat"
            if error:
                raise error
            return config

    monkeypatch.setattr(template_config_parser, "TemplateConfigParser", P)


@pytest.fixture
def engine(monkeypatch):
    eng = MagicMock()
    eng.safe_emit.return_value = True
    fake = SimpleNamespace(web_engine_instance=eng)
    monkeypatch.setitem(sys.modules, "modules.web_engine", fake)
    monkeypatch.setattr(modules, "web_engine", fake, raising=False)
    return eng


# ------------------------------------------------------------------ default duration


def test_default_duration_parser_error(monkeypatch):
    _parser_returning(monkeypatch, error=RuntimeError("x"))
    assert cb.chat_banner_default_duration() == 10.0


@pytest.mark.parametrize(
    "config, expected",
    [
        (None, 10.0),
        ({"elements": "bad"}, 10.0),
        ({"elements": []}, 10.0),
        ({"elements": ["junk", {"id": "Other", "value": 3}, {"id": "BannerDefaultDuration", "value": "7.5"}]}, 7.5),
        ({"elements": [{"id": "BannerDefaultDuration", "value": -3}]}, 0.0),
        ({"elements": [{"id": "BannerDefaultDuration", "value": "abc"}]}, 10.0),
        ({"elements": [{"id": "BannerDefaultDuration", "value": None}]}, 10.0),
    ],
)
def test_default_duration_from_config(monkeypatch, config, expected):
    _parser_returning(monkeypatch, config=config)
    assert cb.chat_banner_default_duration() == expected


# ------------------------------------------------------------------ cleaners


@pytest.mark.parametrize(
    "value, expected",
    [(None, ""), ("  ", ""), ("my source", "my_source"), ("a<b>!c:d.e-f", "abc:d.e-f"), ("x" * 100, "x" * 64)],
)
def test_clean_source(value, expected):
    assert cb._clean_source(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("#fff", "#fff"), ("#ff00ff80", "#ff00ff80"), ("red", "red"), ("transparent", "transparent"),
        ("rgba(1, 2, 3, 0.5)", "rgba(1, 2, 3, 0.5)"), ("rgb(10%,20%,30%)", "rgb(10%,20%,30%)"),
        ("#ggg", ""), ("url(x)", ""), ("", ""), (None, ""), ("a" * 65, ""), ("red; x", ""),
    ],
)
def test_clean_color(value, expected):
    assert cb._clean_color(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [("Roboto", "Roboto"), ("Open_Sans-Bold.1", "Open_Sans-Bold.1"), ("", ""), ("a/b", ""), ("a\\b", ""),
     ("a..b", ""), ("_lead", ""), ("has space", "")],
)
def test_clean_font(value, expected):
    assert cb._clean_font(value) == expected


@pytest.mark.parametrize("value, expected", [("12", 12.0), (8, 8.0), (200, 200.0), (7.9, None), (201, None),
                                             ("x", None), (None, None)])
def test_clean_font_size(value, expected):
    assert cb._clean_font_size(value) == expected


@pytest.mark.parametrize("value, expected", [("fadeIn", "fadeIn"), (" bounceIn ", "bounceIn"), ("spin", ""),
                                             (None, "")])
def test_clean_animation(value, expected):
    assert cb._clean_animation(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        ("https://x.com/a.png", "https://x.com/a.png"),
        ("/static/a.png", "/static/a.png"),
        ("images/a.png", "images/a.png"),
        # Protocol-relative URLs skip the site-path branch but match the plain-path pattern;
        # harmless since absolute http(s) URLs are allowed anyway.
        ("//cdn.example.com/a.png", "//cdn.example.com/a.png"),
        ("javascript:alert", ""),
        ("data:image/png;base64", ""),
        ("a b", ""),
        ("a\"b", ""),
        ("../etc", ""),
        ("x" * 2049, ""),
        ("", ""),
        ("ftp:thing?x=1", ""),
    ],
)
def test_clean_background_image(value, expected):
    assert cb._clean_background_image(value) == expected


def test_normalize_style():
    assert cb._normalize_style("nope") == {}
    assert cb._normalize_style({}) == {}
    full = cb._normalize_style({
        "background": "#000", "background_image": "/a.png", "color": "white", "font": "Inter",
        "font_size": "20", "animation": "slideIn", "animation_duration": "0.5", "extra": "ignored",
    })
    assert full == {"background": "#000", "background_image": "/a.png", "color": "white", "font": "Inter",
                    "font_size": 20.0, "animation": "slideIn", "animation_duration": 0.5}


@pytest.mark.parametrize("dur", ["abc", 0.01, 6, None, ""])
def test_normalize_style_drops_bad_animation_duration(dur):
    assert "animation_duration" not in cb._normalize_style({"animation_duration": dur})


def test_normalize_fragments():
    assert cb._normalize_fragments(None) is None
    assert cb._normalize_fragments([]) is None
    assert cb._normalize_fragments(["x", 1]) is None
    frags = [{"text": str(i)} for i in range(100)]
    assert len(cb._normalize_fragments(frags)) == 80


# ------------------------------------------------------------------ payload


def test_normalize_payload_non_dict_and_clear():
    p = cb.normalize_chat_banner_payload("bad")
    assert p == {"action": "show", "message": "", "duration": 10.0, "permanent": False}
    assert cb.normalize_chat_banner_payload({"action": " CLEAR ", "source": "s 1"}) == {"action": "clear",
                                                                                         "source": "s_1"}
    assert cb.normalize_chat_banner_payload({"action": "clear"}) == {"action": "clear"}


def test_normalize_payload_text_fallback_and_limits():
    p = cb.normalize_chat_banner_payload({"text": "  hi  ", "duration": "abc"}, default_duration=4)
    assert p["message"] == "hi" and p["duration"] == 4.0
    long = cb.normalize_chat_banner_payload({"message": "x" * 3000, "duration": 2})
    assert len(long["message"]) == 2000 and long["duration"] == 2.0


def test_normalize_payload_bad_default_duration():
    p = cb.normalize_chat_banner_payload({"message": "m"}, default_duration="nope")
    assert p["duration"] == 10.0


def test_normalize_payload_full():
    p = cb.normalize_chat_banner_payload({
        "message": "m", "source": "src", "style": {"color": "red"}, "fragments": [{"text": "m"}], "duration": 5})
    assert p == {"action": "show", "message": "m", "duration": 5.0, "permanent": False, "source": "src",
                 "style": {"color": "red"}, "fragments": [{"text": "m"}]}


def test_normalize_payload_negative_duration_is_permanent():
    p = cb.normalize_chat_banner_payload({"message": "m", "duration": -1})
    assert p["permanent"] is True and p["duration"] == 0.0


# ------------------------------------------------------------------ emit


def test_emit_builds_payload_and_emits(engine, monkeypatch):
    monkeypatch.setattr(cb, "chat_banner_default_duration", MagicMock(return_value=99.0))
    ok = cb.emit_chat_banner("hello", duration=3, permanent=False, source="s", style={"color": "red"},
                             fragments=[{"text": "hello"}])
    assert ok is True
    event, payload = engine.safe_emit.call_args.args
    assert event == cb.CHAT_BANNER_EVENT
    assert payload["duration"] == 3.0 and payload["source"] == "s" and payload["style"] == {"color": "red"}
    cb.chat_banner_default_duration.assert_not_called()


def test_emit_uses_template_default_when_no_duration(engine, monkeypatch):
    monkeypatch.setattr(cb, "chat_banner_default_duration", MagicMock(return_value=6.0))
    assert cb.emit_chat_banner(payload={"message": "x"}) is True
    assert engine.safe_emit.call_args.args[1]["duration"] == 6.0


def test_emit_permanent_flag(engine, monkeypatch):
    monkeypatch.setattr(cb, "chat_banner_default_duration", MagicMock(return_value=6.0))
    cb.emit_chat_banner("x", permanent=True)
    assert engine.safe_emit.call_args.args[1]["permanent"] is True


def test_emit_drops_empty_show(engine, monkeypatch):
    monkeypatch.setattr(cb, "chat_banner_default_duration", MagicMock(return_value=6.0))
    assert cb.emit_chat_banner("") is False
    engine.safe_emit.assert_not_called()


def test_emit_clear_without_message(engine):
    assert cb.emit_chat_banner(action="clear", duration=1) is True
    assert engine.safe_emit.call_args.args[1] == {"action": "clear"}


def test_emit_empty_action_defaults_to_show(engine):
    assert cb.emit_chat_banner("m", duration=1, action="") is True
    assert engine.safe_emit.call_args.args[1]["action"] == "show"


def test_emit_without_engine(monkeypatch):
    fake = SimpleNamespace(web_engine_instance=None)
    monkeypatch.setitem(sys.modules, "modules.web_engine", fake)
    monkeypatch.setattr(modules, "web_engine", fake, raising=False)
    assert cb.emit_chat_banner("m", duration=1) is False


def test_emit_engine_error(engine):
    engine.safe_emit.side_effect = RuntimeError("socket")
    assert cb.emit_chat_banner("m", duration=1) is False


def test_emit_returns_engine_result(engine):
    engine.safe_emit.return_value = None
    assert cb.emit_chat_banner("m", duration=1) is False

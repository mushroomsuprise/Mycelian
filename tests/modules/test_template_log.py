# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/template_log.py (client template log validation, rate limits, logger bridge)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import template_log as tl  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_transient(monkeypatch):
    monkeypatch.setattr(tl, "_transient_socket_warn_at", {})


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(tl.time, "time", c)
    return c


# ---------------------------------------------------------------- rate limiter

def test_limiter_defaults():
    lim = tl.TemplateLogRateLimiter()
    assert lim.max_events == tl.RATE_LIMIT_MAX
    assert lim.window_sec == tl.RATE_LIMIT_WINDOW_SEC


def test_limiter_window_slides(clock):
    lim = tl.TemplateLogRateLimiter(max_events=2, window_sec=10)
    assert lim.allow("a") and lim.allow("a")
    assert not lim.allow("a")
    clock.t += 11
    assert lim.allow("a")


def test_limiter_partial_slide_keeps_newer_events(clock):
    lim = tl.TemplateLogRateLimiter(max_events=2, window_sec=10)
    lim._last_evict = clock.t + 100  # suppress global evict pass
    assert lim.allow("a")
    clock.t += 6
    assert lim.allow("a")
    clock.t += 6  # first one expired, second still live
    assert lim.allow("a")
    assert not lim.allow("a")
    assert len(lim._buckets["a"]) == 2


def test_evict_skips_when_recent_unless_forced(clock):
    lim = tl.TemplateLogRateLimiter(max_events=5, window_sec=10)
    lim.allow("old")
    clock.t += 20
    lim._last_evict = clock.t
    lim._evict_idle_buckets(clock.t)
    assert "old" in lim._buckets
    lim._evict_idle_buckets(clock.t, force=True)
    assert "old" not in lim._buckets
    assert lim._last_evict == clock.t


def test_evict_keeps_active_buckets(clock):
    lim = tl.TemplateLogRateLimiter(max_events=5, window_sec=10)
    lim.allow("a")
    clock.t += 5
    lim._evict_idle_buckets(clock.t, force=True)
    assert len(lim._buckets["a"]) == 1


def test_reset(clock):
    lim = tl.TemplateLogRateLimiter(max_events=1, window_sec=10)
    lim.allow("a")
    lim._last_evict = 5.0
    lim.reset()
    assert lim._buckets == {}
    assert lim._last_evict == 0.0
    assert lim.allow("a")


# ---------------------------------------------------------------- transient detection

@pytest.mark.parametrize(
    "message,source,expected",
    [
        ("xhr poll error", "", True),
        ("WebSocket Error", "", True),
        ("transport close", "", True),
        ("timeout", "connect_error", True),
        ("socket connection error: timeout", "", True),
        ("timeout loading image", "", False),
        ("connect_error refused", "", False),
        ("Config load failed", "console.error", False),
    ],
)
def test_is_transient(message, source, expected):
    assert tl.is_transient_socket_message(message, source) is expected


# ---------------------------------------------------------------- normalize

def test_truncate_handles_none_and_non_strings():
    assert tl._truncate(None, 5) == ""
    assert tl._truncate(123456, 3) == "123"
    assert tl._truncate("ab", 5) == "ab"


@pytest.mark.parametrize(
    "data,err",
    [
        ("str", "Invalid data format: must be a dictionary"),
        (None, "Invalid data format: must be a dictionary"),
        ({}, "template_name is required"),
        ({"template_name": "  "}, "template_name is required"),
        ({"template_name": 5}, "template_name is required"),
        ({"template_name": "t", "level": 3, "message": "m"}, "level must be a string"),
        ({"template_name": "t", "message": "   "}, "message is required"),
        ({"template_name": "t", "message": 5}, "message is required"),
    ],
)
def test_normalize_errors(data, err):
    assert tl.normalize_template_log_payload(data) == (None, err)


def test_normalize_defaults_level_and_trims_everything():
    payload, err = tl.normalize_template_log_payload({
        "template_name": "  " + "n" * 200 + " ",
        "level": " INFO ",
        "message": " hi ",
        "stack": "s" * 5000,
        "url": " " + "u" * 600,
        "source": "x" * 100,
    })
    assert err is None
    assert payload["template_name"] == "n" * tl.MAX_TEMPLATE_NAME_LEN
    assert payload["level"] == "info"
    assert payload["message"] == "hi"
    assert len(payload["stack"]) == tl.MAX_STACK_LEN
    assert payload["url"] == "u" * tl.MAX_URL_LEN
    assert len(payload["source"]) == tl.MAX_SOURCE_LEN


def test_normalize_omits_blank_optional_fields():
    payload, err = tl.normalize_template_log_payload(
        {"template_name": "t", "message": "m", "stack": " ", "url": 5, "source": ""})
    assert err is None
    assert payload == {"template_name": "t", "level": "error", "message": "m"}


# ---------------------------------------------------------------- write

def _capture(monkeypatch):
    calls = []
    for name in ("error", "warning", "info", "debug"):
        monkeypatch.setattr(tl.logger, name, lambda msg, _n=name: calls.append((_n, msg)))
    return calls


def test_write_error_includes_prefix_url_source_and_stack(monkeypatch):
    calls = _capture(monkeypatch)
    tl.write_template_log_entry({"template_name": "t", "level": "error", "message": "boom",
                                 "source": "window.onerror", "url": "/t", "stack": "at x"})
    assert calls == [("error", "[template:t] (window.onerror) url=/t boom\nat x")]


def test_write_warn_ignores_stack(monkeypatch):
    calls = _capture(monkeypatch)
    tl.write_template_log_entry({"template_name": "t", "level": "warn", "message": "m", "stack": "s"})
    assert calls == [("warning", "[template:t] m")]


def test_write_info(monkeypatch):
    calls = _capture(monkeypatch)
    tl.write_template_log_entry({"template_name": "t", "level": "info", "message": "m"})
    assert calls == [("info", "[template:t] m")]


def test_write_transient_cooldown_per_template(monkeypatch, clock):
    calls = _capture(monkeypatch)
    entry = {"template_name": "a", "level": "error", "message": "websocket error"}
    tl.write_template_log_entry(entry)
    tl.write_template_log_entry(entry)
    tl.write_template_log_entry({**entry, "template_name": "b"})
    clock.t += tl._TRANSIENT_SOCKET_WARN_COOLDOWN_SEC
    tl.write_template_log_entry(entry)
    assert [c[0] for c in calls] == ["warning", "debug", "warning", "warning"]


# ---------------------------------------------------------------- process

def test_process_invalid_payload_returns_error(monkeypatch):
    called = []
    monkeypatch.setattr(tl, "write_template_log_entry", lambda n: called.append(n))
    lim = tl.TemplateLogRateLimiter()
    assert tl.process_template_log({"template_name": "t"}, "c", lim) == (False, "message is required")
    assert called == []


# ---------------------------------------------------------------- inject

def test_inject_before_head():
    out = tl.inject_template_logger("<html><head></head><body></body></html>")
    assert out == f"<html><head>    {tl.TEMPLATE_LOGGER_SCRIPT}\n</head><body></body></html>"


def test_inject_before_body_when_no_head():
    out = tl.inject_template_logger("<body>x</body>")
    assert out == f"<body>x    {tl.TEMPLATE_LOGGER_SCRIPT}\n</body>"


def test_inject_appends_when_no_markers():
    assert tl.inject_template_logger("<div/>") == "<div/>" + tl.TEMPLATE_LOGGER_SCRIPT


def test_inject_only_first_overlay_tag():
    tag = '<script src="/assets/default_assets/overlay_recovery.js"></script>'
    out = tl.inject_template_logger(tag + tag)
    assert out.count("template_logger.js") == 1


def test_normalize_rejects_unknown_level():
    payload, err = tl.normalize_template_log_payload({"template_name": "t", "level": "fatal", "message": "m"})
    assert payload is None
    assert err == "level must be one of: error, info, warn"


def test_process_success_writes_normalized(monkeypatch):
    written = []
    monkeypatch.setattr(tl, "write_template_log_entry", written.append)
    lim = tl.TemplateLogRateLimiter(max_events=1)
    assert tl.process_template_log({"template_name": "t", "message": " m "}, "c", lim) == (True, None)
    assert written == [{"template_name": "t", "level": "error", "message": "m"}]
    assert tl.process_template_log({"template_name": "t", "message": "m"}, "c", lim) == (False, "rate_limited")


def test_inject_after_overlay_recovery_and_noop():
    tag = '<script src="/assets/default_assets/overlay_recovery.js"></script>'
    out = tl.inject_template_logger(tag + "</head>")
    assert out == tag + "\n    " + tl.TEMPLATE_LOGGER_SCRIPT + "</head>"
    assert tl.inject_template_logger(out) == out

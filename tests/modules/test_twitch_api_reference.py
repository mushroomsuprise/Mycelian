# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/twitch_api_reference.py (static Helix endpoint catalogue and helpers)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import runpy  # noqa: E402
import warnings  # noqa: E402

import pytest  # noqa: E402

from modules import twitch_api_reference as tar  # noqa: E402


@pytest.fixture(scope="module")
def ref():
    return tar.TwitchAPIReference()


def test_all_categories_are_dicts_of_endpoints(ref):
    all_eps = ref.get_all_endpoints()
    assert "chat" in all_eps and "ads" in all_eps and "whispers" in all_eps
    for cat, eps in all_eps.items():
        assert isinstance(eps, dict)
        for key, ep in eps.items():
            assert isinstance(ep, tar.APIEndpoint)
            assert ep.method in {"GET", "POST", "PATCH", "PUT", "DELETE"}
            assert ep.endpoint.startswith("/") or ep.endpoint.startswith("http"), (cat, key)


def test_get_endpoint_by_name(ref):
    ep = ref.get_endpoint_by_name("Chat", "get_chat_settings")
    assert ep is not None and ep.method == "GET"
    assert ref.get_endpoint_by_name("chat", "nope") is None
    assert ref.get_endpoint_by_name("no such", "x") is None
    assert ref.get_endpoint_by_name("Channel Points", next(iter(ref.channel_points))) is not None


def test_get_endpoints_by_method(ref):
    posts = ref.get_endpoints_by_method("post")
    assert posts and all(e.method == "POST" for e in posts)
    assert ref.get_endpoints_by_method("TRACE") == []


def test_get_endpoints_requiring_auth(ref):
    every = ref.get_endpoints_requiring_auth()
    assert every and all(e.authorization for e in every)
    scoped = ref.get_endpoints_requiring_auth("channel:edit:commercial")
    assert any(e.name for e in scoped)
    assert all(any("channel:edit:commercial" in a for a in e.authorization) for e in scoped)
    assert len(scoped) < len(every)
    assert ref.get_endpoints_requiring_auth("no:such:scope") == []


def test_base_url(ref):
    assert ref.get_base_url() == "https://api.twitch.tv/helix"


def test_print_endpoint_summary(ref, capsys):
    tar.print_endpoint_summary(ref, "chat")
    out = capsys.readouterr().out
    assert "=== CHAT ENDPOINTS ===" in out and "GET" in out
    tar.print_endpoint_summary(ref)
    out = capsys.readouterr().out
    assert "=== ADS ENDPOINTS ===" in out and "=== WHISPERS ENDPOINTS ===" in out
    tar.print_endpoint_summary(ref, "unknown")
    assert capsys.readouterr().out.strip() == "=== UNKNOWN ENDPOINTS ==="


def test_get_endpoint_details(ref, capsys):
    tar.get_endpoint_details(ref, "chat", "get_chat_settings")
    out = capsys.readouterr().out
    assert "Method: GET" in out and "Required Params:" in out and "Rate Limits" not in out
    tar.get_endpoint_details(ref, "chat", "missing")
    assert "Endpoint missing not found in category chat" in capsys.readouterr().out


def test_get_endpoint_details_prints_rate_limits(ref, capsys, monkeypatch):
    ep = tar.APIEndpoint("N", "/x", "GET", "d", [], {}, {}, [], rate_limits="5/min")
    monkeypatch.setattr(ref, "get_endpoint_by_name", lambda c, n: ep)
    tar.get_endpoint_details(ref, "c", "n")
    assert "Rate Limits: 5/min" in capsys.readouterr().out


def test_module_main_block(capsys):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # module already imported
        runpy.run_module("modules.twitch_api_reference", run_name="__main__")
    out = capsys.readouterr().out
    assert "=== CHAT ENDPOINTS ===" in out and "Method:" in out

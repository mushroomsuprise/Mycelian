# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/obs_browser_source_match.py (OBS browser-source URL to template route matching)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import obs_browser_source_match as m  # noqa: E402


@pytest.mark.parametrize("raw, expected", [
    ("", "/"), (None, "/"), ("   ", "/"), ("/", "/"),
    ("ff7", "/ff7"), ("/ff7/", "/ff7"), ("/ff7///", "/ff7"),
    ("/ff7.html", "/ff7"), ("/FF7.HTM", "/FF7"), ("/a/.html", "/a"),
    ("/.html", "/"),
])
def test_normalize_overlay_path(raw, expected):
    assert m.normalize_overlay_path(raw) == expected


@pytest.mark.parametrize("url, port", [
    ("http://x:5000/a", 5000), ("https://x/a", 443), ("http://x/a", 80), ("//x/a", 80), ("ftp://x/a", None),
])
def test_browser_url_port(url, port):
    from urllib.parse import urlparse

    assert m._browser_url_port(urlparse(url)) == port


@pytest.mark.parametrize("url, scheme, host, path", [
    ("http://127.0.0.1:5000/ff7?x=1", "http", "127.0.0.1", "/ff7"),
    ("127.0.0.1:5000/ff7", "http", "127.0.0.1", "/ff7"),
    ("127.0.0.1:5000", "http", "127.0.0.1", ""),
])
def test_parse_browser_source_url(url, scheme, host, path):
    p = m.parse_browser_source_url(url)
    assert (p.scheme, p.hostname, p.path) == (scheme, host, path)


def test_parse_browser_source_url_edge_cases(monkeypatch):
    assert m.parse_browser_source_url("") is None
    assert m.parse_browser_source_url(None) is None
    # No scheme and no port: kept as a relative path.
    p = m.parse_browser_source_url("overlay/ff7")
    assert p.scheme == "" and p.path == "overlay/ff7"

    calls = []

    def flaky(raw):
        calls.append(raw)
        raise ValueError("bad")

    monkeypatch.setattr(m, "urlparse", flaky)
    assert m.parse_browser_source_url("http://x") is None

    real = __import__("urllib.parse", fromlist=["urlparse"]).urlparse

    def second_fails(raw):
        if raw.startswith("http://"):
            raise ValueError("bad")
        return real(raw)

    monkeypatch.setattr(m, "urlparse", second_fails)
    assert m.parse_browser_source_url("127.0.0.1:5000/a") is None


@pytest.mark.parametrize("host, local", [
    ("127.0.0.1", True), ("LOCALHOST", True), ("[::1]", True), ("0.0.0.0", True), (" ::1 ", True),
    ("192.168.1.2", False), ("", False), (None, False),
])
def test_is_local_overlay_host(host, local):
    assert m.is_local_overlay_host(host) is local


@pytest.mark.parametrize("a, b, same", [
    ("http://localhost:5000/ff7", "http://127.0.0.1:5000/FF7.html?x=1", True),
    ("127.0.0.1:5000/ff7/", "http://127.0.0.1:5000/ff7", True),
    ("http://host:5000/ff7", "http://host:5000/ff7", True),
    ("http://host:5000/ff7", "http://other:5000/ff7", False),
    ("http://localhost:5000/ff7", "http://localhost:5001/ff7", False),
    ("http://localhost:5000/ff7", "http://localhost:5000/ff8", False),
    ("http://localhost/ff7", "https://localhost/ff7", False),
    ("", "http://localhost/ff7", False),
    ("http://localhost/ff7", None, False),
    ("/ff7", "/ff7", False),  # no host
])
def test_browser_urls_same_template(a, b, same):
    assert m.browser_urls_same_template(a, b) is same


def test_scene_has_template_url():
    url = "http://127.0.0.1:5000/alerts"
    assert m.scene_has_template_url(["http://x/y", "127.0.0.1:5000/alerts"], url) is True
    assert m.scene_has_template_url(["http://x/y"], url) is False
    assert m.scene_has_template_url(None, url) is False


@pytest.mark.parametrize("url, route, kw, ok", [
    ("http://127.0.0.1:5000/ff7", "ff7", {"overlay_port": 5000}, True),
    ("127.0.0.1:5000/FF7.html#x", "/ff7/", {"overlay_port": 5000}, True),
    ("http://127.0.0.1:5000/ff7", "ff7", {"overlay_port": 5001}, False),
    ("http://127.0.0.1:5000/ff7", "ff7", {"overlay_port": 5001, "require_port": False}, True),
    ("http://127.0.0.1/ff7", "ff7", {"overlay_port": 80}, True),
    ("https://127.0.0.1/ff7", "ff7", {"overlay_port": 443}, True),
    ("http://127.0.0.1:5000/ff7", "ff7", {"overlay_port": "bad"}, False),
    ("http://127.0.0.1:5000/ff7", "ff7", {}, True),
    ("http://127.0.0.1:5000/ff7", "", {}, False),
    ("http://127.0.0.1:5000/ff7", "  ", {}, False),
    ("", "ff7", {}, False),
    ("ftp://127.0.0.1:5000/ff7", "ff7", {}, False),
    ("http://127.0.0.1:5000/other", "ff7", {}, False),
    ("/ff7", "ff7", {}, False),  # no host
])
def test_browser_url_matches_route(url, route, kw, ok):
    assert m.browser_url_matches_route(url, route, **kw) is ok


@pytest.mark.parametrize("path, reserved", [
    ("/api", True), ("/api/x", True), ("/API/x", True), ("/assets/a.png", True), ("/static", True),
    ("/socket.io/", True), ("/apiary", False), ("/ff7", False), ("/", False),
])
def test_overlay_path_is_reserved(path, reserved):
    assert m.overlay_path_is_reserved(path) is reserved


@pytest.mark.parametrize("url, port, routes, ok", [
    ("http://127.0.0.1:5000/ff7", 5000, ["ff7"], True),
    ("127.0.0.1:5000/ff7", 5000, ["other", " ff7 "], True),
    ("http://localhost:5000/ff7", 5000, ["ff7"], True),
    ("http://127.0.0.1:5000/ff7", 5000, [], False),
    ("http://127.0.0.1:5000/ff7", 5000, None, False),
    ("http://127.0.0.1:5000/ff7", 5000, ["", "  "], False),
    ("", 5000, ["ff7"], False),
    ("ftp://127.0.0.1:5000/ff7", 5000, ["ff7"], False),
    ("http://example.com:5000/ff7", 5000, ["ff7"], False),
    ("http://127.0.0.1:5000/api", 5000, ["api"], False),
    ("http://127.0.0.1:5000/ff7", None, ["ff7"], False),
    ("http://127.0.0.1:5001/ff7", 5000, ["ff7"], False),
])
def test_is_mycelian_overlay_url(url, port, routes, ok):
    assert m.is_mycelian_overlay_url(url, port, routes) is ok


@pytest.mark.parametrize("path, route", [
    ("/ff7", "ff7"), ("/my%20overlay.html", "my overlay"), ("/", None), ("", None), ("/api/x", None), (None, None),
])
def test_overlay_template_route_from_path(path, route):
    assert m.overlay_template_route_from_path(path) == route


def test_overlay_template_route_from_path_unquote_failure(monkeypatch):
    def boom(_):
        raise ValueError("x")

    monkeypatch.setattr(m, "unquote", boom)
    assert m.overlay_template_route_from_path("/ff7") == "ff7"


def test_overlay_template_route_from_url(monkeypatch):
    assert m.overlay_template_route_from_url("http://127.0.0.1:5000/ff7.html", 5000, ["ff7"]) == "ff7"
    assert m.overlay_template_route_from_url("http://127.0.0.1:5000/zz", 5000, ["ff7"]) is None
    # Parse failing after the match (defensive branch).
    monkeypatch.setattr(m, "is_mycelian_overlay_url", lambda *a: True)
    assert m.overlay_template_route_from_url("", 5000, ["ff7"]) is None


@pytest.mark.parametrize("settings, defaults, wh", [
    ({"width": 1920, "height": 1080}, None, (1920, 1080)),
    ({"width": "800.7"}, {"width": 1, "height": "600"}, (800, 600)),
    ({}, {"width": 10, "height": 20}, (10, 20)),
    ({"width": 0, "height": 10}, None, None),
    ({"width": 10, "height": -1}, None, None),
    ({"width": "x", "height": 1}, None, None),
    ({}, None, None),
    ("junk", "junk", None),
])
def test_coerce_browser_wh(settings, defaults, wh):
    assert m.coerce_browser_wh(settings, defaults) == wh


def _src(name, w, h):
    return {"source_name": name, "width": w, "height": h}


def test_pick_browser_source_size():
    a, b, c = _src("A", 100, 100), _src("B", 200, 200), _src("C", 300, 300)
    assert m.pick_browser_source_size([]) is None
    assert m.pick_browser_source_size([a, _src("A2", "100", 100.0)]) is a
    assert m.pick_browser_source_size([a, b]) is None
    assert m.pick_browser_source_size([a, b], program_source_names={"B"}) is b
    assert m.pick_browser_source_size([a, b], program_source_names={"Z"}) is None
    same_on_prog = [a, b, _src("B2", 200, 200)]
    assert m.pick_browser_source_size(same_on_prog, program_source_names={"B", "B2"}) is same_on_prog[1]
    assert m.pick_browser_source_size([a, b, c], program_source_names={"A", "B"}) is None


@pytest.mark.parametrize("kind, ok", [
    ("browser_source", True), ("BROWSER_SOURCE_v2", True), (" browser_source ", True),
    ("image_source", False), ("", False), (None, False),
])
def test_is_browser_input_kind(kind, ok):
    assert m.is_browser_input_kind(kind) is ok


def test_schemeless_localhost_url_is_treated_as_http():
    # The docstrings promise schemeless OBS URLs are read as http://, and localhost is a
    # local overlay host, but urlparse treats "localhost:" as a URL scheme.
    assert m.is_mycelian_overlay_url("localhost:5000/ff7", 5000, ["ff7"]) is True

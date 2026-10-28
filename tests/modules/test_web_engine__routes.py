# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/web_engine.py from ``register_routes`` on (part 1).

Covers template/container/fallback routes served through Flask's test client, the
Spore Studio / Custom Sources preview session helpers, and the first block of
Socket.IO handlers (audio files, connect/disconnect, preview register, dock
notices, theme, overlay hello, alert handshake, pause/resume). Templates and
assets live under tmp_path; background tasks, heartbeats and pollers are faked.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import web_engine  # noqa: E402
from modules.spore_studio import assets_watcher  # noqa: E402
from modules.spore_studio import containers as containers_mod  # noqa: E402
from modules.template_config_parser import TemplateConfigParser  # noqa: E402

WE = web_engine.WebEngine


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    tdir = tmp_path / "templates"
    tdir.mkdir()
    adir = tmp_path / "assets"
    adir.mkdir()
    monkeypatch.setattr(web_engine, "get_template_path", lambda rel="": str(tdir / rel) if rel else str(tdir))
    monkeypatch.setattr(web_engine, "get_assets_path", lambda rel="": str(adir / rel) if rel else str(adir))
    return SimpleNamespace(templates=tdir, assets=adir, configs=tdir / "template_configs")


@pytest.fixture
def make_engine(dirs, monkeypatch):
    for name in ("ALERT_PLAYING", "ALERTS_PAUSED", "ALERTS_MUTED", "web_engine_instance", "web_engine_running",
                 "EXPECTED_ALERT_COMPLETE_SEQ"):
        monkeypatch.setattr(web_engine, name, getattr(web_engine, name))
    for meth in ("ensure_web_engine_heartbeat", "ensure_template_control_emit_worker", "_refresh_cached_listener"):
        monkeypatch.setattr(WE, meth, lambda self: None)
    monkeypatch.setattr(assets_watcher, "ensure_background_poller", lambda sio: None)
    monkeypatch.setattr(containers_mod, "list_enabled_routes", lambda: [])
    made = []

    def make(port=5996):
        eng = WE(template_dir=str(dirs.templates), port=port)
        eng.template_config_parser = TemplateConfigParser(config_dir=str(dirs.configs))
        eng.bg_tasks = []
        monkeypatch.setattr(eng.socketio, "start_background_task",
                            lambda fn, *a, **k: eng.bg_tasks.append((fn, a, k)))
        made.append(eng)
        return eng

    yield make
    for eng in made:
        eng._close_emit_wake_pipe()


@pytest.fixture
def engine(make_engine):
    return make_engine()


@pytest.fixture
def client(engine):
    return engine.app.test_client()


@pytest.fixture
def sio(engine):
    c = engine.socketio.test_client(engine.app, flask_test_client=engine.app.test_client())
    assert c.is_connected()
    c.get_received()
    engine.bg_tasks.clear()
    yield c
    if c.is_connected():
        c.disconnect()


def _sid(engine, client):
    """Server-side Socket.IO sid of a test client."""
    return engine.socketio.server.manager.sid_from_eio_sid(client.eio_sid, "/")


def flush(engine):
    """Deliver everything safe_emit queued from this (non-hub) thread."""
    return engine._drain_safe_emit_queue()


def received(client, name=None):
    events = client.get_received()
    if name is None:
        return events
    return [e["args"][0] if e["args"] else None for e in events if e["name"] == name]


def _write_template(dirs, name, html, config=None):
    (dirs.templates / f"{name}.html").write_text(html, encoding="utf-8")
    if config is not None:
        dirs.configs.mkdir(exist_ok=True)
        (dirs.configs / f"{name}.json").write_text(json.dumps(config), encoding="utf-8")


@pytest.fixture
def notices(monkeypatch):
    from modules import notification_engine

    calls = []
    monkeypatch.setattr(notification_engine, "notify", lambda msg, **kw: calls.append((msg, kw)))
    monkeypatch.setattr(notification_engine, "nav_spore_template", lambda n: [("spore", n)])
    monkeypatch.setattr(notification_engine, "nav_source_template", lambda n: [("source", n)])
    return calls


PAGE = "<html><head></head><body>{{ greeting }}|{{ mycelian_html_stem }}|{{ mycelian_preview_mode }}</body></html>"
CFG = {"elements": [{"id": "greeting", "value": "hi"}]}


# ------------------------------------------------------------------ register_routes / container routes


def test_register_routes_tracks_templates(make_engine, dirs):
    _write_template(dirs, "alpha", PAGE, CFG)
    eng = make_engine()
    assert eng.get_registered_template_routes is not None
    assert eng._registered_template_routes == {"alpha"}
    assert (dirs.assets / "alpha").is_dir()
    # A removed template is dropped from tracking; a new one is added.
    (dirs.templates / "alpha.html").unlink()
    _write_template(dirs, "beta", PAGE)
    eng.register_routes()
    assert eng._registered_template_routes == {"beta"}


def test_register_routes_missing_directory(engine, tmp_path):
    engine._registered_template_routes = {"keep"}
    engine.template_dir = str(tmp_path / "gone")
    engine.register_routes()
    assert engine._registered_template_routes == {"keep"}


def test_overlay_source_routes(engine, monkeypatch):
    engine._registered_template_routes = {"b", "a"}
    monkeypatch.setattr(containers_mod, "list_enabled_routes", lambda: ["c", "a"])
    assert engine.overlay_source_routes() == ["a", "b", "c"]

    def boom():
        raise RuntimeError("disk")

    monkeypatch.setattr(containers_mod, "list_enabled_routes", boom)
    assert engine.overlay_source_routes() == ["a", "b"]


def test_register_container_routes(make_engine, dirs, monkeypatch):
    _write_template(dirs, "tpl", PAGE)
    monkeypatch.setattr(containers_mod, "list_enabled_routes", lambda: ["tpl", "box", "box"])
    monkeypatch.setattr(containers_mod, "load_container", lambda r: {"enabled": True, "route": r})
    monkeypatch.setattr(containers_mod, "render_shell", lambda m: f"<shell {m['route']}>")
    eng = make_engine()
    assert eng._registered_container_routes == {"box"}
    del eng._registered_container_routes
    eng.register_container_routes()  # recreates the set; "box" is a new rule name
    res = eng.app.test_client().get("/box")
    assert res.status_code == 200
    assert res.data == b"<shell box>"
    assert res.headers["Cache-Control"] == "no-store"
    assert res.headers["Content-Type"] == "text/html; charset=utf-8"


def test_register_container_routes_import_failure(engine, monkeypatch):
    monkeypatch.setitem(sys.modules, "modules.spore_studio.containers", None)
    engine._registered_container_routes = set()
    engine.register_container_routes()
    assert engine._registered_container_routes == set()


def test_container_route_falls_back_when_container_gone(make_engine, dirs, monkeypatch):
    state = {"model": {"enabled": True}}
    monkeypatch.setattr(containers_mod, "list_enabled_routes", lambda: ["box"])
    monkeypatch.setattr(containers_mod, "load_container", lambda r: state["model"])
    monkeypatch.setattr(containers_mod, "render_shell", lambda m: "<shell>")
    eng = make_engine()
    client = eng.app.test_client()
    state["model"] = None
    assert client.get("/box").status_code == 404  # fallback: no template file either
    _write_template(dirs, "box", PAGE, CFG)
    res = client.get("/box")
    assert res.status_code == 200 and b"hi|box|False" in res.data
    eng._template_fallback = None
    assert client.get("/box").status_code == 404


def test_register_container_route_after_first_request_is_skipped(engine, client):
    client.get("/nothing-here")
    engine._register_container_route("late")
    assert "late" not in engine._registered_container_routes


def test_register_container_route_unexpected_error(engine, monkeypatch):
    def boom(*a, **k):
        raise ValueError("bad rule")

    monkeypatch.setattr(engine.app, "add_url_rule", boom)
    engine._register_container_route("weird")
    assert "weird" not in engine._registered_container_routes


def test_container_shell_response_variants(engine, monkeypatch):
    with engine.app.test_request_context("/"):
        monkeypatch.setattr(containers_mod, "load_container", lambda r: None)
        assert engine._container_shell_response("x") is None
        monkeypatch.setattr(containers_mod, "load_container", lambda r: {"enabled": False})
        assert engine._container_shell_response("x") is None

        def bad_render(model):
            raise RuntimeError("broken")

        monkeypatch.setattr(containers_mod, "load_container", lambda r: {"route": r})
        monkeypatch.setattr(containers_mod, "render_shell", bad_render)
        assert engine._container_shell_response("x") == ("Error loading container x: broken", 500)
        monkeypatch.setitem(sys.modules, "modules.spore_studio.containers", None)
        assert engine._container_shell_response("x") is None


# ------------------------------------------------------------------ render-failure notice


def test_notify_template_render_failed(engine, dirs, notices):
    engine._notify_template_render_failed("t", ValueError("x"), preview=True)
    assert notices == []
    engine._notify_template_render_failed("t", ValueError("bad line"))
    msg, kw = notices[-1]
    assert kw["actions"] == [("source", "t")]
    assert kw["details"] == "t.html: ValueError: bad line"
    assert kw["dedupe_key"] == "overlay:render:t" and kw["type"] == "error"
    (dirs.templates / "_spore").mkdir()
    (dirs.templates / "_spore" / "t.spore.json").write_text("{}")
    engine._notify_template_render_failed("t", ValueError("x"))
    assert notices[-1][1]["actions"] == [("spore", "t")]
    assert "Spore Studio" in notices[-1][1]["hint"]


def test_notify_template_render_failed_swallows_errors(engine, monkeypatch):
    from modules import notification_engine

    monkeypatch.setattr(notification_engine, "notify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    engine._notify_template_render_failed("t", ValueError("x"))


# ------------------------------------------------------------------ explicit template routes


@pytest.fixture
def served(make_engine, dirs):
    _write_template(dirs, "alpha", PAGE, CFG)
    eng = make_engine()
    return eng, eng.app.test_client()


def test_template_route_renders_and_caches(served, monkeypatch):
    eng, client = served
    res = client.get("/alpha")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "hi|alpha|False" in body
    assert "overlay_recovery.js" in body  # served overlay scripts injected
    assert "alpha" in eng._overlay_html_cache
    # Second hit is served from the cache without rendering.
    monkeypatch.setattr(web_engine, "render_template", lambda *a, **k: pytest.fail("cached"))
    assert client.get("/alpha").get_data(as_text=True) == body


def test_template_route_embed_audio(served, monkeypatch):
    eng, client = served
    seen = {}

    def inject(html, *, volume, muted):
        seen.update(volume=volume, muted=muted)
        return html + "<!--embed-->"

    monkeypatch.setattr(containers_mod, "inject_embed_audio", inject)
    res = client.get("/alpha?mycelian_embed=1&mycelian_muted=1&mycelian_volume=40")
    assert res.get_data(as_text=True).endswith("<!--embed-->")
    assert seen == {"volume": 40.0, "muted": True}


def test_template_route_preview_overrides(served):
    eng, client = served
    eng.push_preview_overrides("tok", "alpha", {"greeting": "override"})
    res = client.get("/alpha?__preview_token=tok")
    body = res.get_data(as_text=True)
    assert "override|alpha|True" in body
    assert web_engine.MYCELIAN_PREVIEW_HELPER_HTML.strip()[:30] in body
    assert body.index(web_engine.MYCELIAN_PREVIEW_HELPER_HTML.strip()[:30]) < body.index("</body>")
    assert "mycelian_preview_token=tok" in res.headers["Set-Cookie"]
    # Preview renders never land in the cache.
    assert "alpha" not in eng._overlay_html_cache


def test_template_route_preview_without_body_tag(make_engine, dirs):
    _write_template(dirs, "nobody", "{{ greeting }}", CFG)
    eng = make_engine()
    eng.push_preview_overrides("tok", "nobody", "not a dict")
    res = eng.app.test_client().get("/nobody?__preview_token=tok")
    assert res.get_data(as_text=True).rstrip().endswith(web_engine.MYCELIAN_PREVIEW_HELPER_HTML.rstrip())


def test_template_route_token_for_other_template_is_ignored(served):
    eng, client = served
    eng.push_preview_overrides("tok", "other", {"greeting": "x"})
    body = client.get("/alpha?__preview_token=tok").get_data(as_text=True)
    assert "hi|alpha|False" in body


def test_template_route_draft_html(served):
    eng, client = served
    with eng._preview_sessions_lock:
        eng._preview_sessions["tok"] = {
            "template": "alpha", "overrides": [], "draft_html": "<body>DRAFT {{ greeting }}</body>",
            "draft_config": {"elements": [{"id": "greeting", "value": "draft"}]},
        }
    body = client.get("/alpha?__preview_token=tok").get_data(as_text=True)
    assert "DRAFT draft" in body


def test_template_route_render_error(served, notices, monkeypatch):
    eng, client = served

    def boom(*a, **k):
        raise RuntimeError("jinja broke")

    monkeypatch.setattr(web_engine, "render_template", boom)
    res = client.get("/alpha")
    assert res.status_code == 500
    assert b"Error loading template alpha: jinja broke" in res.data
    assert notices and notices[0][1]["dedupe_key"] == "overlay:render:alpha"
    client.get("/alpha?__preview_token=x")
    assert len(notices) == 1  # preview errors are not announced


def test_template_route_deleted_file_uses_fallback(served, monkeypatch):
    eng, client = served
    (Path(eng.template_dir) / "alpha.html").unlink()
    monkeypatch.setattr(containers_mod, "load_container", lambda r: {"enabled": True})
    monkeypatch.setattr(containers_mod, "render_shell", lambda m: "<container>")
    assert client.get("/alpha").data == b"<container>"


def test_template_route_deleted_file_without_fallback(served, notices):
    eng, client = served
    (Path(eng.template_dir) / "alpha.html").unlink()
    eng._template_fallback = None
    assert client.get("/alpha").status_code == 500
    assert notices[0][1]["dedupe_key"] == "overlay:render:alpha"


def test_register_template_route_after_first_request(served, dirs):
    eng, client = served
    client.get("/alpha")
    eng._register_template_route("late")
    assert (dirs.assets / "late").is_dir()
    assert all(r.endpoint.split("_")[1] != "late" for r in eng.app.url_map.iter_rules() if r.endpoint.startswith("route_"))


def test_register_template_route_unexpected_error(engine, notices, monkeypatch):
    monkeypatch.setattr(engine.app, "add_url_rule", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    engine._register_template_route("odd name")
    msg, kw = notices[0]
    assert kw["title"] == "Couldn’t publish overlay “odd name”" or "odd name" in kw["title"]
    assert kw["dedupe_key"] == "overlay:route:odd name"


def test_register_template_route_notice_failure_swallowed(engine, monkeypatch):
    from modules import notification_engine

    monkeypatch.setattr(engine.app, "add_url_rule", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    monkeypatch.setattr(notification_engine, "notify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    engine._register_template_route("odd")


# ------------------------------------------------------------------ fallback route


def test_fallback_serves_runtime_templates(engine, client, dirs):
    assert client.get("/fresh").status_code == 404
    _write_template(dirs, "fresh", PAGE, CFG)
    res = client.get("/fresh")
    assert res.status_code == 200 and "hi|fresh|False" in res.get_data(as_text=True)


def test_fallback_rejects_bad_names(engine):
    with engine.app.test_request_context("/"):
        assert engine._template_fallback("") == ("Not found", 404)
        assert engine._template_fallback("a/b") == ("Not found", 404)


def test_fallback_container(engine, client, monkeypatch):
    monkeypatch.setattr(containers_mod, "load_container", lambda r: {"enabled": True})
    monkeypatch.setattr(containers_mod, "render_shell", lambda m: "<c>")
    assert client.get("/boxy").data == b"<c>"


def test_fallback_preview_modes(engine, client, dirs):
    _write_template(dirs, "fresh", PAGE, CFG)
    engine.push_preview_overrides("tok", "fresh", {"greeting": "ov"})
    res = client.get("/fresh?__preview_token=tok")
    assert "ov|fresh|True" in res.get_data(as_text=True)
    assert "mycelian_preview_token=tok" in res.headers["Set-Cookie"]
    with engine._preview_sessions_lock:
        engine._preview_sessions["d"] = {"template": "fresh", "overrides": None,
                                         "draft_html": "DRAFT {{ greeting }}", "draft_config": {"elements": []}}
    body = client.get("/fresh?__preview_token=d").get_data(as_text=True)
    assert body.startswith("DRAFT ") and body.rstrip().endswith(web_engine.MYCELIAN_PREVIEW_HELPER_HTML.rstrip())


def test_fallback_render_error(engine, client, dirs, notices, monkeypatch):
    _write_template(dirs, "fresh", PAGE, CFG)
    monkeypatch.setattr(web_engine, "render_template", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad")))
    res = client.get("/fresh")
    assert res.status_code == 500 and b"Error loading template fresh: bad" in res.data
    assert notices[0][1]["dedupe_key"] == "overlay:render:fresh"


def test_fallback_registration_after_first_request(engine, client):
    client.get("/")
    engine._register_template_fallback_route()  # logged, not raised
    assert callable(engine._template_fallback)


def test_themed_dock_template_gets_theme(make_engine, dirs, monkeypatch):
    _write_template(dirs, "activity_feed", "<html><head></head><body>feed</body></html>", {"elements": []})
    monkeypatch.setattr(web_engine, "get_theme_manager",
                        lambda: SimpleNamespace(get_theme=lambda: SimpleNamespace(name="Dark", theme_type="dark")))
    monkeypatch.setattr(web_engine, "generate_css_variables", lambda theme: ":root{--x:1}")
    eng = make_engine()
    body = eng.app.test_client().get("/activity_feed").get_data(as_text=True)
    assert '<style id="mycelian-theme-vars">:root{--x:1}</style>' in body
    assert 'data-theme-type="dark"' in body


# ------------------------------------------------------------------ assets folder


def test_create_template_assets_folder(engine, dirs, monkeypatch, tmp_path):
    engine._create_template_assets_folder("one")
    assert (dirs.assets / "one").is_dir()
    engine._create_template_assets_folder("one")  # already there
    fresh = tmp_path / "new_assets"
    monkeypatch.setattr(web_engine, "get_assets_path", lambda rel="": str(fresh))
    engine._create_template_assets_folder("two")
    assert (fresh / "two").is_dir()
    monkeypatch.setattr(web_engine, "get_assets_path", lambda rel="": (_ for _ in ()).throw(OSError("ro")))
    engine._create_template_assets_folder("three")  # logged


# ------------------------------------------------------------------ preview iframe registry


def test_preview_sid_registry(engine):
    engine._register_preview_iframe_sid("", "s")
    engine._register_preview_iframe_sid("t", "")
    assert engine._preview_iframe_sids == {}
    engine._register_preview_iframe_sid("t", "s1")
    engine._register_preview_iframe_sid("t", "s2")
    assert sorted(engine._preview_iframe_sid_list("t")) == ["s1", "s2"]
    assert engine._preview_iframe_sid_list("none") == []
    assert engine._preview_iframe_has_sid("s1") and engine._preview_token_for_sid("s1") == "t"
    engine._unregister_preview_iframe_sid("unknown")
    engine._unregister_preview_iframe_sid("s1")
    assert engine._preview_iframe_sid_list("t") == ["s2"]
    engine._unregister_preview_iframe_sid("s2")
    assert "t" not in engine._preview_iframe_sids
    assert engine._preview_token_for_sid("s2") is None
    # Token mapping with an already-empty bucket.
    engine._preview_iframe_tokens["s3"] = "gone"
    engine._unregister_preview_iframe_sid("s3")
    assert "s3" not in engine._preview_iframe_tokens


def test_push_preview_overrides(engine):
    rev = engine._preview_sessions_rev
    engine.push_preview_overrides("", "t", {})
    engine.push_preview_overrides("tok", "", {})
    assert engine._preview_sessions == {}
    src = {"a": 1}
    engine.push_preview_overrides("tok", "t", src)
    sess = engine._preview_sessions["tok"]
    assert sess["template"] == "t" and sess["overrides"] == {"a": 1} and sess["overrides"] is not src
    assert engine._preview_sessions_rev == rev + 1


def _capture_emits(engine, monkeypatch, fail=False):
    sent = []

    def emit(event, data=None, to=None):
        if fail:
            raise RuntimeError("emit broke")
        sent.append((event, data, to))
        return True

    monkeypatch.setattr(engine, "safe_emit", emit)
    return sent


def test_emit_preview_mock(engine, monkeypatch):
    sent = _capture_emits(engine, monkeypatch)
    ok, err, ev = engine.emit_preview_mock("tok", "x")
    assert (ok, ev) == (False, None) and "open preview first" in err
    engine._register_preview_iframe_sid("tok", "s1")
    assert engine.emit_preview_mock("tok", "custom_ev", {"v": 1}) == (True, None, "custom_ev")
    assert sent == [("custom_ev", {"v": 1}, "s1")]

    from modules.spore_studio import preview_mocks

    calls = []
    monkeypatch.setattr(preview_mocks, "build_mock_payload",
                        lambda name, alert_type=None: calls.append((name, alert_type)) or ("next_alert", {"a": 1}))
    assert engine.emit_preview_mock("tok", "alert", alert_type=" follow ") == (True, None, "next_alert")
    assert calls == [("alert", "follow")]
    assert sent[-1] == ("next_alert", {"a": 1}, "s1")
    monkeypatch.setattr(preview_mocks, "build_mock_payload", lambda name, alert_type=None: None)
    assert engine.emit_preview_mock("tok", "nope") == (False, "No mock payload defined for 'nope'.", None)
    monkeypatch.setattr(preview_mocks, "build_mock_payload",
                        lambda name, alert_type=None: (_ for _ in ()).throw(RuntimeError("boom")))
    assert engine.emit_preview_mock("tok", "x") == (False, "boom", None)


def test_emit_preview_settings_and_refresh(engine, monkeypatch):
    from modules import template_preview_settings as tps

    sent = _capture_emits(engine, monkeypatch)
    monkeypatch.setattr(tps, "load_template_preview_settings", lambda: {"sounds": True})
    engine.emit_preview_settings_to_iframe("tok")
    engine.emit_preview_config_refresh("tok")
    assert sent == []
    engine._register_preview_iframe_sid("tok", "s1")
    engine.emit_preview_settings_to_iframe("tok")
    engine.emit_preview_config_refresh("tok")
    assert sent == [("mycelian_preview_settings", {"sounds": True}, "s1"),
                    ("mycelian_preview_config_refresh", {}, "s1")]
    _capture_emits(engine, monkeypatch, fail=True)
    engine.emit_preview_settings_to_iframe("tok")
    engine.emit_preview_config_refresh("tok")


def test_register_spore_preview_session_keep_draft(engine):
    engine._register_spore_preview_session_keep_draft("", "t")
    engine._register_spore_preview_session_keep_draft("tok", "")
    assert engine._preview_sessions == {}
    engine._register_spore_preview_session_keep_draft("tok", "t")
    assert engine._preview_sessions["tok"]["draft_html"] is None
    engine._preview_sessions["tok"].update(overrides={"a": 1}, draft_html="<b>", draft_config={"x": 1})
    engine._register_spore_preview_session_keep_draft("tok", "t")
    sess = engine._preview_sessions["tok"]
    assert (sess["overrides"], sess["draft_html"], sess["draft_config"]) == ({"a": 1}, "<b>", {"x": 1})
    # Switching templates keeps overrides but drops the other template's draft.
    engine._register_spore_preview_session_keep_draft("tok", "other")
    sess = engine._preview_sessions["tok"]
    assert sess["template"] == "other" and sess["draft_html"] is None and sess["overrides"] == {"a": 1}
    engine._preview_sessions["tok"].update(overrides="junk", draft_html="  ", draft_config={})
    engine._register_spore_preview_session_keep_draft("tok", "other")
    assert engine._preview_sessions["tok"]["overrides"] == {} and engine._preview_sessions["tok"]["draft_html"] is None


# ------------------------------------------------------------------ config layering helpers


def test_apply_preview_config_layers():
    cfg = {"elements": [{"id": "a", "value": 1}, {"id": "b", "value": 2}, "junk", {"id": "c", "value": 3}],
           "previewState": {"mock_values": {"b": 20, "a": 10, "c": 30}}}
    WE._apply_preview_config_layers(cfg, {"a": "ov"}, preview_mode=True)
    assert [e["value"] for e in cfg["elements"] if isinstance(e, dict)] == ["ov", 20, 30]
    cfg2 = {"elements": [{"id": "a", "value": 1}], "previewState": {"mock_values": {"a": 5}}}
    WE._apply_preview_config_layers(cfg2, {}, preview_mode=False)
    assert cfg2["elements"][0]["value"] == 1
    for state in ({"previewState": "x"}, {"previewState": {"mock_values": []}}, {}):
        cfg3 = dict(state, elements=[{"id": "a", "value": 1}])
        WE._apply_preview_config_layers(cfg3, {}, preview_mode=True)
        assert cfg3["elements"][0]["value"] == 1


@pytest.mark.parametrize("color, opacity, out", [
    ("#fff", 1, "#fff"),
    (None, 1, "transparent"),
    ("#fff", "x", "#fff"),
    ("#fff", 0, "transparent"),
    ("#fff", -2, "transparent"),
    ("", 0.5, "transparent"),
    ("Transparent", 0.5, "transparent"),
    ("#abc", 0.5, "rgba(170,187,204,0.5)"),
    ("#102030", 0.25, "rgba(16,32,48,0.25)"),
    ("rgb(1, 2, 3)", 0.5, "rgba(1,2,3,0.5)"),
    ("rgba(1,2,3,0.5)", 0.5, "rgba(1,2,3,0.25)"),
    ("red", 0.5, "red"),
])
def test_color_with_opacity(color, opacity, out):
    assert WE._color_with_opacity(color, opacity) == out


def test_template_variable_map(monkeypatch):
    from modules.spore_studio import fonts_registry

    monkeypatch.setattr(fonts_registry, "resolve_font_filename", lambda v: "Renogare.ttf" if v == "Renogare" else None)
    cfg = {"elements": [
        {"id": "title_font_family", "value": "Renogare"},
        {"id": "body_font_family", "value": "Unknown"},
        {"id": "empty_font_family", "value": ""},
        {"id": "BGColor", "value": "#000"},
        {"id": "BGOpacity", "value": 0.5},
        {"id": 7, "value": "n"},
        {"id": "novalue"},
        "junk",
    ]}
    out = WE._template_variable_map(cfg)
    assert out["title_font_family"] == "Renogare.ttf"
    assert out["body_font_family"] == "Unknown"
    assert out["empty_font_family"] == ""
    assert out["7"] == "n" and "novalue" not in out
    assert out["BGColorWithOpacity"] == "rgba(0,0,0,0.5)"
    assert "BGColorWithOpacity" not in WE._template_variable_map({"elements": [{"id": "BGColor", "value": "#000"}]})
    assert WE._template_variable_map({}) == {}


def test_merge_preview_session_into_config(engine):
    cfg = {"elements": [{"id": "a", "value": 1}, "junk", {"id": "b", "value": 2}]}
    engine._merge_preview_session_into_config(None, "t", cfg)
    engine._merge_preview_session_into_config("tok", "t", "not a dict")
    engine._merge_preview_session_into_config("missing", "t", cfg)
    engine._preview_sessions["tok"] = {"template": "t", "overrides": "junk"}
    engine._merge_preview_session_into_config("tok", "t", cfg)
    engine._preview_sessions["tok"] = {"template": "other", "overrides": {"a": 9}}
    engine._merge_preview_session_into_config("tok", "t", cfg)
    assert cfg["elements"][0]["value"] == 1
    engine._preview_sessions["tok"] = {"template": "t", "overrides": {"a": 9}}
    engine._merge_preview_session_into_config("tok", "t", cfg)
    assert cfg["elements"][0]["value"] == 9 and cfg["elements"][2]["value"] == 2


def test_maybe_start_preview_demo(engine, monkeypatch):
    with engine.app.test_request_context("/?__preview_token=q"):
        engine._maybe_start_preview_demo("s1")
    with engine.app.test_request_context("/", headers={"Cookie": "mycelian_preview_token=c"}):
        engine._maybe_start_preview_demo("s2")
    with engine.app.test_request_context("/"):
        engine._maybe_start_preview_demo("s3")
    assert engine._preview_token_for_sid("s1") == "q"
    assert engine._preview_token_for_sid("s2") == "c"
    assert engine._preview_token_for_sid("s3") is None
    monkeypatch.setattr(engine, "_register_preview_iframe_sid", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    with engine.app.test_request_context("/?__preview_token=q"):
        engine._maybe_start_preview_demo("s4")  # logged


def test_rebuild_url_map_keeps_the_old_map_when_rebuild_fails(served):
    eng, client = served
    old = eng.app.url_map
    eng._rebuild_url_map()  # werkzeug refuses rules bound to another map; logged
    assert eng.app.url_map is old
    assert client.get("/alpha").status_code == 200


@pytest.mark.xfail(strict=True, reason="BUG: _rebuild_url_map re-adds rules still bound to the old Map, so it always fails")
def test_rebuild_url_map_replaces_the_map(served):
    eng, _client = served
    old = eng.app.url_map
    eng._rebuild_url_map()
    assert eng.app.url_map is not old


def test_rebuild_url_map_with_rebindable_rules(served, monkeypatch):
    # With a Map that accepts the old rules, the rest of the rebuild runs: system
    # routes are kept, template rules dropped and re-registered from disk.
    import werkzeug.routing as routing

    class RebindMap(routing.Map):
        def add(self, rulefactory):
            rulefactory.map = None
            super().add(rulefactory)

    monkeypatch.setattr(routing, "Map", RebindMap)
    eng, _client = served
    eng._rebuild_url_map()
    assert isinstance(eng.app.url_map, RebindMap)
    endpoints = {r.endpoint for r in eng.app.url_map.iter_rules()}
    assert "template_fallback" in endpoints
    assert any(e.startswith("route_alpha_") for e in endpoints)
    assert eng._registered_template_routes == {"alpha"}


# ------------------------------------------------------------------ socket: get_audio_files


def _audio(sio, data):
    sio.emit("get_audio_files", data)
    return received(sio, "audio_files_response")


def test_audio_files_lists_sorted_audio(engine, sio, dirs):
    folder = dirs.assets / "alerts" / "sounds" / "sub"
    folder.mkdir(parents=True)
    for name in ("b.MP3", "a.wav", "notes.txt", "c.flac"):
        (folder / name).write_text("x")
    (folder / "dir.mp3").mkdir()
    (resp,) = _audio(sio, {"folder": "\\sounds//sub/", "request_id": "r1"})
    assert resp == {"success": True, "request_id": "r1", "files": ["a.wav", "b.MP3", "c.flac"], "folder": "sounds/sub"}


def test_audio_files_rejects_bad_requests(engine, sio, dirs):
    (resp,) = _audio(sio, {"folder": "x"})
    assert resp["success"] is False and resp["request_id"] == "unknown" and "required" in resp["error"]
    (resp,) = _audio(sio, {"folder": "../../etc", "request_id": "r"})
    assert resp["success"] is False and "not found" in resp["error"]
    (resp,) = _audio(sio, {"folder": "missing", "request_id": "r"})
    assert resp == {"success": False, "error": "Audio folder not found: missing", "request_id": "r", "files": []}


def test_audio_files_listing_error(engine, sio, dirs, monkeypatch):
    (dirs.assets / "alerts" / "x").mkdir(parents=True)
    real = os.listdir

    def listdir(path):
        if str(path).endswith(os.path.join("alerts", "x")):
            raise PermissionError("denied")
        return real(path)

    monkeypatch.setattr(web_engine.os, "listdir", listdir)
    (resp,) = _audio(sio, {"folder": "x", "request_id": "r"})
    assert resp["success"] is False and resp["error"].startswith("Error reading folder:")


@pytest.mark.xfail(strict=True, reason="BUG: get_audio_files calls data.get() on a non-dict payload inside its own error path")
def test_audio_files_non_dict_payload_gets_an_error_response(engine, sio):
    sio.emit("get_audio_files", ["x"])
    (resp,) = received(sio, "audio_files_response")
    assert resp["success"] is False


def test_audio_files_unexpected_error(engine, sio, monkeypatch):
    monkeypatch.setattr(web_engine, "get_assets_path", lambda rel="": (_ for _ in ()).throw(RuntimeError("boom")))
    (resp,) = _audio(sio, {"folder": "x", "request_id": "r9"})
    assert resp == {"success": False, "error": "boom", "request_id": "r9", "files": []}


# ------------------------------------------------------------------ socket: connect / disconnect


def test_connect_rejects_non_local(engine, monkeypatch):
    seen = []
    monkeypatch.setattr(web_engine, "is_local_client_addr", lambda addr: seen.append(addr) or False)
    c = engine.socketio.test_client(engine.app, flask_test_client=engine.app.test_client())
    assert not c.is_connected()
    assert seen == ["127.0.0.1"] and engine._socket_connected_count == 0


def _run_bg(engine):
    tasks, engine.bg_tasks = engine.bg_tasks, []
    for fn, a, k in tasks:
        fn(*a, **k)


@pytest.fixture
def no_sleep(engine, monkeypatch):
    monkeypatch.setattr(engine.socketio, "sleep", lambda s=0: None)


@pytest.fixture
def theme(monkeypatch):
    state = {"theme": SimpleNamespace(name="Forest", theme_type=None)}
    monkeypatch.setattr(web_engine, "get_theme_manager", lambda: SimpleNamespace(get_theme=lambda: state["theme"]))
    monkeypatch.setattr(web_engine, "generate_css_variables", lambda t: f"--{t.name}")
    return state


@pytest.mark.parametrize("paused", [False, True])
def test_connect_deferred_broadcast(engine, no_sleep, theme, monkeypatch, paused):
    monkeypatch.setattr(web_engine, "ALERTS_PAUSED", paused)
    monkeypatch.setattr(web_engine, "ALERTS_MUTED", True)
    monkeypatch.setattr(web_engine, "get_alert_queue_state", lambda: {"q": 1})
    c = engine.socketio.test_client(engine.app, flask_test_client=engine.app.test_client())
    assert [fn.__name__ for fn, _, _ in engine.bg_tasks] == ["deferred_initial_client_broadcast"]
    assert engine._socket_connected_count == 1
    _run_bg(engine)
    names = [e["name"] for e in c.get_received()]
    assert names == ["pause_status_update", "alerts_paused" if paused else "alerts_resumed",
                     "mute_status_update", web_engine.ALERT_QUEUE_STATE_EVENT, "theme_update"]
    c.disconnect()


def test_connect_deferred_broadcast_skips_preview_iframes(engine, no_sleep, theme):
    fc = engine.app.test_client()
    c = engine.socketio.test_client(engine.app, flask_test_client=fc, query_string="__preview_token=tok")
    assert engine._preview_iframe_sid_list("tok") == [_sid(engine, c)]
    _run_bg(engine)
    assert c.get_received() == []
    c.disconnect()
    assert engine._preview_iframe_sid_list("tok") == []


def test_connect_deferred_broadcast_without_theme_and_with_error(engine, no_sleep, theme, monkeypatch):
    theme["theme"] = None
    monkeypatch.setattr(web_engine, "get_alert_queue_state", lambda: {})
    c = engine.socketio.test_client(engine.app, flask_test_client=engine.app.test_client())
    _run_bg(engine)
    assert "theme_update" not in [e["name"] for e in c.get_received()]
    # A failure inside the deferred broadcast is logged, not raised.
    c2 = engine.socketio.test_client(engine.app, flask_test_client=engine.app.test_client())
    monkeypatch.setattr(web_engine, "get_alert_queue_state", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    _run_bg(engine)
    c.disconnect()
    c2.disconnect()


def test_connect_survives_hook_failures(engine, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("hook")

    monkeypatch.setattr(WE, "ensure_web_engine_heartbeat", boom)
    monkeypatch.setattr(WE, "ensure_template_control_emit_worker", boom)
    monkeypatch.setattr(assets_watcher, "ensure_background_poller", boom)
    monkeypatch.setattr(engine, "_maybe_start_preview_demo", boom)
    monkeypatch.setattr(engine.socketio, "start_background_task", boom)
    c = engine.socketio.test_client(engine.app, flask_test_client=engine.app.test_client())
    assert c.is_connected()
    c.disconnect()


def test_disconnect_cleans_up_and_notes_session(engine, sio, monkeypatch):
    from modules import alert_playback_session as aps

    sid = _sid(engine, sio)
    engine._preview_demo_stop[sid] = True
    engine._register_preview_iframe_sid("tok", sid)
    engine._overlay_hello_routes[sid] = "ff7"
    noted, finished = [], []
    session = SimpleNamespace(finished=False, note_disconnect=lambda s: noted.append(s) or True)
    monkeypatch.setattr(aps, "get_session", lambda: session)
    monkeypatch.setattr(web_engine, "_apply_playback_session_finished", finished.append)
    sio.disconnect()
    assert noted == [sid] and finished == [True]
    assert sid not in engine._preview_demo_stop
    assert engine._preview_iframe_sid_list("tok") == []
    assert sid not in engine._overlay_hello_routes
    assert engine._socket_connected_count == 0


def test_disconnect_session_errors_are_ignored(engine, sio, monkeypatch):
    from modules import alert_playback_session as aps

    monkeypatch.setattr(aps, "get_session", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    sio.disconnect()
    assert engine._socket_connected_count == 0


# ------------------------------------------------------------------ socket: preview register / ready


def test_preview_register(engine, sio, monkeypatch):
    from modules import template_preview_settings as tps

    monkeypatch.setattr(tps, "load_template_preview_settings", lambda: {"toolbar": False})
    sio.emit("mycelian_preview_register", {"token": ""})
    sio.emit("mycelian_preview_register", "junk")
    assert sio.get_received() == []
    engine.push_preview_overrides("tok", "alpha", {})
    sio.emit("mycelian_preview_register", {"token": "tok"})
    assert received(sio, "mycelian_preview_settings") == [{"toolbar": False}]
    assert engine._preview_token_for_sid(_sid(engine, sio)) == "tok"
    assert "alpha" in engine._assets_watch_templates
    sio.emit("mycelian_preview_client_ready")
    assert received(sio, "mycelian_preview_settings") == [{"toolbar": False}]
    monkeypatch.setattr(tps, "load_template_preview_settings", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    sio.emit("mycelian_preview_register", {"token": "tok2"})
    sio.emit("mycelian_preview_client_ready")
    assert sio.get_received() == []


def test_preview_client_ready_without_token(engine, sio):
    sio.emit("mycelian_preview_client_ready")
    assert sio.get_received() == []


# ------------------------------------------------------------------ socket: dock notices / settings / theme


def test_dock_notify(engine, sio, notices):
    sio.emit("dock_notify", {"level": "info", "message": "fine"})
    sio.emit("dock_notify", "junk")
    assert notices == []
    sio.emit("dock_notify", {"level": "Error", "source": "activity_feed", "message": "m" * 700, "title": "T",
                             "hint": "h"})
    msg, kw = notices[-1]
    assert len(msg) == 600 and kw["source"] == "activity_feed" and kw["title"] == "T" and kw["hint"] == "h"
    assert kw["skip_toast"] is True and kw["dedupe_key"] == "dock:activity_feed:T"
    sio.emit("dock_notify", {"level": "negative", "source": "evil", "message": "boom"})
    msg, kw = notices[-1]
    assert kw["source"] == "app" and kw["title"] is None and kw["hint"] is None
    assert kw["dedupe_key"] == "dock:app:boom"


def test_dock_notify_rejects_remote_and_swallows_errors(engine, sio, notices, monkeypatch):
    from modules import notification_engine

    monkeypatch.setattr(web_engine, "_reject_non_localhost_socket", lambda: True)
    sio.emit("dock_notify", {"level": "error", "message": "x"})
    assert notices == []
    monkeypatch.setattr(web_engine, "_reject_non_localhost_socket", lambda: False)
    monkeypatch.setattr(notification_engine, "notify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    sio.emit("dock_notify", {"level": "error", "message": "x"})


def test_get_notify_settings(engine, sio, monkeypatch):
    from modules import notify_settings

    monkeypatch.setattr(notify_settings, "current_prefs", lambda: SimpleNamespace(for_client=lambda: {"pos": "top"}))
    sio.emit("get_notify_settings")
    assert received(sio, "notify_settings") == [{"pos": "top"}]
    monkeypatch.setattr(notify_settings, "current_prefs", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    sio.emit("get_notify_settings")
    assert sio.get_received() == []


def test_get_current_theme(engine, sio, theme, monkeypatch):
    sio.emit("get_current_theme")
    assert received(sio, "theme_update") == [{"css": "--Forest", "theme_name": "Forest", "theme_type": "dark"}]
    theme["theme"] = None
    sio.emit("get_current_theme")
    assert sio.get_received() == []
    monkeypatch.setattr(web_engine, "get_theme_manager", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    sio.emit("get_current_theme")
    assert sio.get_received() == []


# ------------------------------------------------------------------ socket: overlay hello / game hooks


def test_overlay_hello(engine, sio, monkeypatch):
    sid = _sid(engine, sio)
    sio.emit("overlay_hello", {"path": "/ff7.html"})
    assert engine._overlay_hello_routes[sid] == "ff7"
    sio.emit("overlay_hello", {"route": "/alerts"})
    assert engine._overlay_hello_routes[sid] == "alerts"
    sio.emit("overlay_hello", "junk")  # no path: route unchanged
    assert engine._overlay_hello_routes[sid] == "alerts"
    monkeypatch.setattr(engine, "_register_overlay_hello", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    sio.emit("overlay_hello", {"path": "/x"})


def test_game_hook_command(engine, sio, monkeypatch):
    from modules import game_hooks_service

    seen = []
    monkeypatch.setattr(game_hooks_service, "handle_game_hook_command", seen.append)
    sio.emit("game_hook_command", {"cmd": "clear"})
    assert seen == [{"cmd": "clear"}]
    monkeypatch.setattr(game_hooks_service, "handle_game_hook_command",
                        lambda d: (_ for _ in ()).throw(RuntimeError("x")))
    sio.emit("game_hook_command", {"cmd": "clear"})


# ------------------------------------------------------------------ socket: alert handshake


class FakeSession:
    def __init__(self, seq, finished=False, complete=(True, True)):
        self.seq, self.finished, self._complete = seq, finished, complete
        self.calls = []

    def note_playing(self, sid, route, is_preview=False):
        self.calls.append(("playing", route, is_preview))

    def note_progress(self, sid, is_preview=False):
        self.calls.append(("progress", is_preview))

    def note_complete(self, sid, route, is_preview=False):
        self.calls.append(("complete", route, is_preview))
        return self._complete

    def tick(self):
        self.calls.append(("tick",))
        return False


@pytest.fixture
def handshake(engine, monkeypatch):
    from modules import alert_playback_session as aps

    state = SimpleNamespace(session=None, finished=[])
    monkeypatch.setattr(aps, "get_session", lambda: state.session)
    monkeypatch.setattr(web_engine, "_apply_playback_session_finished", state.finished.append)
    monkeypatch.setattr(web_engine, "EXPECTED_ALERT_COMPLETE_SEQ", None)
    return state


@pytest.mark.parametrize("payload", [None, {}, {"queue_seq": "x"}, {"queue_seq": -1}, {"queue_seq": 3}])
def test_alert_playing_ignored(engine, sio, handshake, payload):
    handshake.session = FakeSession(5)
    sio.emit("alert_playing", payload)
    sio.emit("alert_progress", payload)
    assert handshake.session.calls == []


def test_alert_playing_and_progress(engine, sio, handshake, monkeypatch):
    handshake.session = FakeSession(5)
    monkeypatch.setattr(web_engine, "EXPECTED_ALERT_COMPLETE_SEQ", 5)
    sio.emit("alert_playing", {"queue_seq": "5", "route": "/ff7"})
    sio.emit("alert_progress", {"queue_seq": 5})
    assert handshake.session.calls == [("playing", "ff7", False), ("tick",), ("progress", False), ("tick",)]
    assert handshake.finished == [False, False]


def test_alert_handshake_rejects_mismatches(engine, sio, handshake, monkeypatch):
    handshake.session = FakeSession(5, finished=True)
    sio.emit("alert_playing", {"queue_seq": 5})
    handshake.session = FakeSession(5)
    monkeypatch.setattr(web_engine, "EXPECTED_ALERT_COMPLETE_SEQ", 6)
    sio.emit("alert_playing", {"queue_seq": 5})
    monkeypatch.setattr(web_engine, "EXPECTED_ALERT_COMPLETE_SEQ", None)
    handshake.session = None
    sio.emit("alert_progress", {"queue_seq": 5})
    assert handshake.finished == []


def test_alert_complete(engine, sio, handshake):
    sid = _sid(engine, sio)
    engine._register_preview_iframe_sid("tok", sid)
    handshake.session = FakeSession(7)
    sio.emit("alert_complete", {"queue_seq": 7, "path": "/alerts"})
    assert received(sio, "alert_complete_ack") == [{"queue_seq": 7}]
    assert handshake.session.calls == [("complete", "alerts", True)]
    assert handshake.finished == [True]


def test_alert_complete_not_accepted(engine, sio, handshake):
    handshake.session = FakeSession(7, complete=(False, False))
    sio.emit("alert_complete", {"queue_seq": 7})
    assert received(sio, "alert_complete_ack") == []
    assert handshake.finished == [False]


def test_alert_complete_after_finish_just_acks(engine, sio, handshake):
    handshake.session = FakeSession(7, finished=True)
    sio.emit("alert_complete", {"queue_seq": 7})
    assert received(sio, "alert_complete_ack") == [{"queue_seq": 7}]
    assert handshake.session.calls == []


def test_alert_complete_ignored(engine, sio, handshake, monkeypatch):
    sio.emit("alert_complete", {"queue_seq": None})
    sio.emit("alert_complete", {"queue_seq": -2})
    handshake.session = FakeSession(9)
    sio.emit("alert_complete", {"queue_seq": 7})
    assert sio.get_received() == [] and handshake.finished == []


def test_alert_complete_ack_emit_failure(engine, sio, handshake, monkeypatch):
    handshake.session = FakeSession(7, finished=True)
    real = engine.socketio.emit

    def emit(event, *a, **k):
        if event == "alert_complete_ack":
            raise RuntimeError("socket gone")
        return real(event, *a, **k)

    monkeypatch.setattr(engine.socketio, "emit", emit)
    sio.emit("alert_complete", {"queue_seq": 7})  # logged, not raised


# ------------------------------------------------------------------ socket: pause / resume / toggle


def test_pause_resume_toggle(engine, sio, monkeypatch):
    toggles = []
    monkeypatch.setattr(engine, "toggle_alerts", lambda: toggles.append(web_engine.ALERTS_PAUSED))
    monkeypatch.setattr(web_engine, "ALERTS_PAUSED", False)
    sio.emit("resume_alerts")
    assert toggles == []
    sio.emit("pause_alerts")
    assert toggles == [False]
    monkeypatch.setattr(web_engine, "ALERTS_PAUSED", True)
    sio.emit("pause_alerts")
    assert toggles == [False]
    sio.emit("resume_alerts")
    sio.emit("toggle_alerts")
    assert toggles == [False, True, True]

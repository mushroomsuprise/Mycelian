# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""HTTP routes registered inside ``WebEngine.__init__`` (modules/web_engine.py).

Request hooks (latency timing, cross-site write guard, CORS), static assets, config
endpoints, Spore Studio editor/container/asset/preview endpoints, preview resolution,
notification proxy, template log, and the Stream Deck HTTP API. Every collaborator is
faked; requests go through Flask's test client (no server, no ports).
"""

from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import web_engine as we  # noqa: E402


# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module", autouse=True)
def _collect_engines():
    """Each WebEngine is a large reference cycle (routes close over self); free them
    when this file is done so a later collection pause doesn't land in other tests."""
    yield
    import gc

    gc.collect()


@pytest.fixture
def engine(tmp_path, monkeypatch):
    for name in ("ALERTS_PAUSED", "ALERT_PLAYING", "web_engine_running", "web_engine_instance"):
        monkeypatch.setattr(we, name, getattr(we, name))
    tdir = tmp_path / "templates"
    tdir.mkdir()
    eng = we.WebEngine(template_dir=str(tdir), port=5999)
    # Skip the throttled maintenance hook (it would arm the heartbeat/watchdog).
    eng._maintenance_hooks_last_run = float("inf")
    return eng


@pytest.fixture
def client(engine):
    return engine.app.test_client()


def _json(res):
    return json.loads(res.get_data(as_text=True))


class FakeParser:
    def __init__(self, configs=None, raise_for=()):
        self.configs = configs or {}
        self.raise_for = set(raise_for)
        self.calls = []

    def get_config_files(self):
        return list(self.configs)

    def config_file_exists(self, name):
        return name in self.configs

    def load_config(self, name, **kw):
        self.calls.append((name, kw))
        if name in self.raise_for:
            raise RuntimeError("broken config")
        return json.loads(json.dumps(self.configs[name]))


# --------------------------------------------------------------------------- request hooks


def test_maintenance_hook_runs_workers_then_throttles(engine, monkeypatch):
    engine._maintenance_hooks_last_run = 0.0
    calls = []
    engine._refresh_cached_listener = lambda: calls.append("listener")
    engine.ensure_web_engine_heartbeat = lambda: calls.append("heartbeat")
    engine.ensure_template_control_emit_worker = lambda: calls.append("tc")
    c = engine.app.test_client()
    c.get("/api/dock-boot.js")
    assert calls == ["listener", "heartbeat", "tc"]
    c.get("/api/dock-boot.js")
    assert calls == ["listener", "heartbeat", "tc"]  # throttled (5s)


def test_maintenance_hook_failure_is_swallowed(engine):
    engine._maintenance_hooks_last_run = 0.0
    engine._refresh_cached_listener = mock.Mock(side_effect=RuntimeError("boom"))
    res = engine.app.test_client().get("/api/dock-boot.js")
    assert res.status_code == 200


def test_request_latency_recorded_except_assets_and_options(engine, client, monkeypatch, tmp_path):
    rec = mock.Mock()
    engine._record_http_latency = rec
    client.get("/api/dock-boot.js")
    assert rec.call_count == 1 and rec.call_args[0][1] == "/api/dock-boot.js"
    monkeypatch.setattr(we, "get_assets_path", lambda: str(tmp_path))
    monkeypatch.setattr(we, "_ASSETS_RESOLVE_CACHE", {"root": None, "real": None, "files": {}})
    client.get("/assets/none.png")
    res = client.options("/api/whatever")
    assert res.status_code == 200
    assert res.headers["Access-Control-Allow-Methods"] == "GET, POST, PUT, DELETE, OPTIONS"
    assert rec.call_count == 1


def test_slow_route_prints_bottleneck_line(engine, client, monkeypatch):
    printed = []
    monkeypatch.setattr(we, "_bottleneck_print", printed.append)
    ticks = iter([0.0, 1.0])
    import time as _time

    monkeypatch.setattr(we, "time", types.SimpleNamespace(perf_counter=lambda: next(ticks, 1.0),
                                                          monotonic=_time.monotonic, time=_time.time))
    engine._record_http_latency = mock.Mock()
    client.get("/api/dock-boot.js")
    assert printed and printed[0].startswith("slow_route path=/api/dock-boot.js")


def test_cors_headers_and_debug_log(engine, client, monkeypatch):
    monkeypatch.setattr(we.logger, "isEnabledFor", lambda level: True)
    res = client.get("/api/dock-boot.js")
    assert res.headers["Access-Control-Allow-Origin"] == "*"
    assert res.headers["Access-Control-Allow-Headers"] == "Content-Type, Authorization"
    assert res.get_data(as_text=True) == "/* mycelian-dock-boot */\n"
    assert res.headers["Cache-Control"] == "no-store"


def test_cross_site_write_guard_only_applies_to_editor_writes(engine, client):
    evil = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    res = client.post("/api/spore-studio/preview/release", json={}, headers=evil)
    assert res.status_code == 403
    assert _json(res) == {"error": "Only Spore Studio itself can change templates."}
    # Non-editor write routes are not guarded
    res = client.post("/api/streamdeck/template_action", json={}, headers=evil)
    assert res.status_code != 403
    # Same-origin passes
    res = client.post("/api/spore-studio/preview/release", json={},
                      headers={"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"})
    assert res.status_code == 200


# --------------------------------------------------------------------------- static assets


@pytest.fixture
def assets(tmp_path, monkeypatch):
    root = tmp_path / "assets"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "ding.wav").write_bytes(b"RIFF")
    (root / "img.png").write_bytes(b"\x89PNG")
    monkeypatch.setattr(we, "get_assets_path", lambda *a: str(root) if not a else str(root / a[0]))
    monkeypatch.setattr(we, "_ASSETS_RESOLVE_CACHE", {"root": None, "real": None, "files": {}})
    return root


def test_assets_route_serves_with_media_mimetype(client, assets, monkeypatch):
    monkeypatch.setattr(we.logger, "isEnabledFor", lambda level: True)
    res = client.get("/assets/sub/ding.wav")
    assert res.status_code == 200 and res.mimetype == "audio/wav"
    assert res.data == b"RIFF"
    res.close()
    res = client.get("/assets/img.png")
    assert res.status_code == 200 and res.data == b"\x89PNG"
    res.close()
    res = client.get("/assets/missing.png")
    assert res.status_code == 404
    assert res.get_data(as_text=True) == "File not found: missing.png"


def test_assets_route_error_returns_500(client, assets, monkeypatch):
    monkeypatch.setattr(we, "send_from_directory", mock.Mock(side_effect=RuntimeError("io")))
    res = client.get("/assets/img.png")
    assert res.status_code == 500
    assert "Error serving file" in res.get_data(as_text=True)


def test_static_url_serves_assets(client, assets):
    res = client.get("/static/img.png")
    assert res.status_code == 200 and res.data == b"\x89PNG"


def _call_view(engine, endpoint, path, **kwargs):
    with engine.app.test_request_context(path):
        rv = engine.app.view_functions[endpoint](**kwargs)
        return engine.app.make_response(rv)


def test_static_standard_view_body(engine, assets, monkeypatch):
    """The /static view (called directly) mirrors /assets."""
    res = _call_view(engine, "serve_static_file_standard", "/static/sub/ding.wav", filename="sub/ding.wav")
    res.direct_passthrough = False
    assert res.status_code == 200 and res.mimetype == "audio/wav" and res.get_data() == b"RIFF"
    res.close()
    res = _call_view(engine, "serve_static_file_standard", "/static/img.png", filename="img.png")
    res.direct_passthrough = False
    assert res.status_code == 200 and res.get_data() == b"\x89PNG"
    res.close()
    res = _call_view(engine, "serve_static_file_standard", "/static/x", filename="x")
    assert res.status_code == 404
    monkeypatch.setattr(we, "send_from_directory", mock.Mock(side_effect=RuntimeError("io")))
    res = _call_view(engine, "serve_static_file_standard", "/static/img.png", filename="img.png")
    assert res.status_code == 500


def test_debug_static_lists_files_or_404(client, assets, monkeypatch):
    monkeypatch.setattr(we, "_dev_debug_endpoints_enabled", lambda: True)
    body = _json(client.get("/debug/static"))
    assert body["files"] == ["img.png", "sub/ding.wav"] or body["files"] == ["img.png", "sub\\ding.wav"]
    assert body["assets_exists"] is True and "working_directory" in body
    monkeypatch.setattr(we, "_dev_debug_endpoints_enabled", lambda: False)
    assert client.get("/debug/static").status_code == 404


def test_debug_static_missing_assets_dir(client, tmp_path, monkeypatch):
    monkeypatch.setattr(we, "_dev_debug_endpoints_enabled", lambda: True)
    monkeypatch.setattr(we, "get_assets_path", lambda: str(tmp_path / "nope"))
    body = _json(client.get("/debug/static"))
    assert body["files"] == [] and body["assets_exists"] is False


# --------------------------------------------------------------------------- config endpoints


def test_template_configs_uses_preview_cookie_and_handles_error(engine, client):
    engine._get_dynamic_controls_configs_cached = mock.Mock(return_value=(b'{"a":1}', True))
    client.set_cookie("mycelian_preview_token", "tok")
    res = client.get("/api/template-configs")
    assert res.status_code == 200 and _json(res) == {"a": 1}
    engine._get_dynamic_controls_configs_cached.assert_called_once_with("tok")
    engine._get_dynamic_controls_configs_cached.side_effect = RuntimeError("x")
    res = client.get("/api/template-configs")
    assert res.status_code == 500 and "error" in _json(res)


@pytest.mark.parametrize("state, code", [
    (we.WEBENGINE_STATE_RUNNING, 200), (we.WEBENGINE_STATE_OVERLOADED, 200),
    (we.WEBENGINE_STATE_STARTING, 200), (we.WEBENGINE_STATE_RESTARTING, 200),
    (we.WEBENGINE_STATE_CRASHED, 503), (we.WEBENGINE_STATE_STALLED, 503),
])
def test_health_route_status_codes(engine, client, state, code):
    engine.get_health = lambda: {"state": state}
    res = client.get("/api/health")
    assert res.status_code == code and _json(res) == {"state": state}


def test_all_template_configs_cache_hit_bypasses_rate_limit(engine, client):
    engine._peek_all_template_configs_cache = mock.Mock(return_value=b'{"x":{}}')
    engine._all_template_configs_rate_limited = mock.Mock(return_value=True)
    res = client.get("/api/all-template-configs")
    assert res.status_code == 200 and _json(res) == {"x": {}}
    engine._all_template_configs_rate_limited.assert_not_called()


def test_all_template_configs_rate_limited(engine, client):
    engine._peek_all_template_configs_cache = mock.Mock(return_value=None)
    engine._all_template_configs_rate_limited = mock.Mock(return_value=True)
    engine._all_template_configs_rate_log_at = 0.0
    res = client.get("/api/all-template-configs")
    assert res.status_code == 429 and res.headers["Retry-After"] == "3"
    first_log_at = engine._all_template_configs_rate_log_at
    assert first_log_at > 0
    client.get("/api/all-template-configs")  # within log cooldown
    assert engine._all_template_configs_rate_log_at == first_log_at


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def perf_counter(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_all_template_configs_build_and_slow_log(engine, client, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(we, "time", clock)
    engine._peek_all_template_configs_cache = mock.Mock(return_value=None)
    engine._all_template_configs_rate_limited = mock.Mock(return_value=False)
    engine._record_slow_request = mock.Mock()

    def slow_build(tok):
        clock.t += 2.0
        return b'{"a":{},"b":{}}', False

    engine._get_all_template_configs_cached = slow_build
    res = client.get("/api/all-template-configs")
    assert res.status_code == 200 and set(_json(res)) == {"a", "b"}
    engine._record_slow_request.assert_called_once()
    assert engine._record_slow_request.call_args[0][0] == "all-template-configs"
    assert engine._all_template_configs_slow_log_at == clock.t + 30.0

    # Undecodable payload while slow -> template_count fallback, still 200
    engine._all_template_configs_slow_log_at = 0.0
    engine._get_all_template_configs_cached = lambda tok: (clock.sleep(2.0), (b"\xff", True))[1]
    client.set_cookie("mycelian_preview_token", "abcdefghijk")
    assert client.get("/api/all-template-configs").status_code == 200


def test_all_template_configs_build_error(engine, client):
    engine._peek_all_template_configs_cache = mock.Mock(return_value=None)
    engine._all_template_configs_rate_limited = mock.Mock(return_value=False)
    engine._get_all_template_configs_cached = mock.Mock(side_effect=RuntimeError("disk"))
    res = client.get("/api/all-template-configs")
    assert res.status_code == 500


def test_single_template_config(engine, client):
    parser = FakeParser({"alerts": {"x": 1}})
    engine.template_config_parser = parser
    merged = []
    engine._merge_preview_session_into_config = lambda tok, name, cfg: merged.append((tok, name))
    client.set_cookie("mycelian_preview_token", "t1")
    res = client.get("/api/template-config/alerts")
    assert res.status_code == 200 and _json(res) == {"x": 1}
    assert merged == [("t1", "alerts")]
    assert parser.calls == [("alerts", {"include_dynamic_controls": False, "create": False})]
    assert client.get("/api/template-config/nope").status_code == 404
    assert client.get("/api/template-config/a%5Cb").status_code == 400
    assert client.get("/api/template-config/%20").status_code == 400
    parser.raise_for.add("alerts")
    assert client.get("/api/template-config/alerts").status_code == 500


def test_factorio_icon(client, monkeypatch):
    import modules.game_hooks.factorio_icons as fi

    monkeypatch.setattr(fi, "cropped_png_bytes", lambda name: b"PNG" if name == "iron" else b"")
    res = client.get("/api/factorio-icon/iron.png")
    assert res.status_code == 200 and res.data == b"PNG"
    assert res.headers["Content-Type"] == "image/png"
    assert res.headers["Cache-Control"] == "public, max-age=86400"
    assert client.get("/api/factorio-icon/none.png").status_code == 404
    monkeypatch.setattr(fi, "cropped_png_bytes", mock.Mock(side_effect=RuntimeError))
    assert client.get("/api/factorio-icon/iron.png").status_code == 404


def test_template_queue_metadata(engine, client):
    engine._get_template_queue_metadata_cached = mock.Mock(return_value=(b'{"q":1}', False))
    assert _json(client.get("/api/template-queue-metadata")) == {"q": 1}
    engine._get_template_queue_metadata_cached.side_effect = RuntimeError
    assert client.get("/api/template-queue-metadata").status_code == 500


# --------------------------------------------------------------------------- Spore Studio editor


def test_spore_studio_editor_served_themed(client, tmp_path, monkeypatch):
    editor = tmp_path / "editor.html"
    editor.write_text("<html><head></head></html>", encoding="utf-8")
    monkeypatch.setattr(we, "get_assets_path", lambda *a: str(editor))
    monkeypatch.setattr(we, "_inject_theme_vars", lambda h: h + "<!--themed-->")
    res = client.get("/_spore_studio_editor")
    assert res.status_code == 200
    assert res.headers["Content-Type"] == "text/html; charset=utf-8"
    assert res.get_data(as_text=True).endswith("<!--themed-->")
    monkeypatch.setattr(we, "_inject_theme_vars", mock.Mock(side_effect=RuntimeError("t")))
    res = client.get("/_spore_studio_editor")
    assert res.status_code == 500 and "Error serving editor" in res.get_data(as_text=True)


def test_spore_studio_editor_missing_bundle(client, tmp_path, monkeypatch):
    monkeypatch.setattr(we, "get_assets_path", lambda *a: str(tmp_path / "none.html"))
    res = client.get("/_spore_studio_editor")
    assert res.status_code == 500 and "bundle missing" in res.get_data(as_text=True)


REGISTRY_ROUTES = [
    ("/api/spore-studio/events", "modules.spore_studio.event_registry", "get_event_registry",
     {"events": [1]}, {"events": [1]}),
    ("/api/spore-studio/data-sources", "modules.spore_studio.data_source_registry",
     "get_data_source_registry", {"s": 1}, {"s": 1}),
    ("/api/spore-studio/data-catalog", "modules.spore_studio.data_feed_registry",
     "get_data_catalog", {"c": 1}, {"c": 1}),
    ("/api/spore-studio/control-actions", "modules.spore_studio.control_action_registry",
     "get_control_action_registry", {"a": 1}, {"a": 1}),
    ("/api/spore-studio/templates", "modules.spore_studio.save_pipeline", "list_spore_templates",
     (["s1"], ["l1"]), {"spore_templates": ["s1"], "legacy_templates": ["l1"]}),
    ("/api/spore-studio/container-templates", "modules.spore_studio.containers",
     "list_member_templates", {"m": []}, {"m": []}),
    ("/api/spore-studio/containers", "modules.spore_studio.containers", "list_containers",
     [{"route": "r"}], {"containers": [{"route": "r"}]}),
    ("/api/spore-studio/fonts", "modules.spore_studio.fonts_registry", "get_font_registry",
     {"fonts": []}, {"fonts": []}),
]


@pytest.mark.parametrize("path, modname, attr, value, expected", REGISTRY_ROUTES)
def test_registry_routes_success_and_error(client, monkeypatch, path, modname, attr, value, expected):
    import importlib

    mod = importlib.import_module(modname)
    monkeypatch.setattr(mod, attr, lambda: value)
    res = client.get(path)
    assert res.status_code == 200 and _json(res) == expected
    monkeypatch.setattr(mod, attr, mock.Mock(side_effect=RuntimeError("kaput")))
    res = client.get(path)
    assert res.status_code == 500 and _json(res) == {"error": "kaput"}


@pytest.fixture
def containers(monkeypatch):
    import modules.spore_studio.containers as c

    return c


def test_container_load(client, containers, monkeypatch):
    monkeypatch.setattr(containers, "load_container", lambda r: {"route": r} if r == "main" else None)
    assert _json(client.get("/api/spore-studio/containers/main")) == {"route": "main"}
    assert client.get("/api/spore-studio/containers/zzz").status_code == 404
    monkeypatch.setattr(containers, "load_container", mock.Mock(side_effect=RuntimeError("x")))
    assert client.get("/api/spore-studio/containers/main").status_code == 500


def test_container_create(client, containers, monkeypatch):
    create = mock.Mock(return_value={"route": "r"})
    monkeypatch.setattr(containers, "create_container", create)
    res = client.post("/api/spore-studio/containers/create", json={"name": "N", "width": "640"})
    assert _json(res) == {"ok": True, "container": {"route": "r"}}
    create.assert_called_once_with("N", None, width=640, height=1080)
    res = client.post("/api/spore-studio/containers/create", json={"width": "wide"})
    assert res.status_code == 400
    create.side_effect = containers.ContainerError("bad name")
    res = client.post("/api/spore-studio/containers/create", json={})
    assert res.status_code == 400 and _json(res) == {"error": "bad name"}
    create.side_effect = RuntimeError("io")
    assert client.post("/api/spore-studio/containers/create", json={}).status_code == 500


def test_container_preview_drafts(engine, client, containers, monkeypatch):
    monkeypatch.setattr(containers, "render_shell", lambda m: "<html>%s</html>" % m.get("route"))
    assert client.post("/api/spore-studio/containers/preview", json={}).status_code == 400
    res = client.post("/api/spore-studio/containers/preview", json={"container": {"route": "r1"}})
    url = _json(res)["url"]
    assert url.startswith("/_spore_container_preview/")
    page = client.get(url)
    assert page.status_code == 200 and page.get_data(as_text=True) == "<html>r1</html>"
    assert client.get("/_spore_container_preview/expired").status_code == 404
    # Draft store stays bounded (oldest evicted past 20)
    engine._container_drafts.clear()
    for i in range(22):
        engine._container_drafts[f"k{i}"] = "x"
    client.post("/api/spore-studio/containers/preview", json={"container": {"route": "r"}})
    assert "k0" not in engine._container_drafts and len(engine._container_drafts) == 22
    monkeypatch.setattr(containers, "render_shell", mock.Mock(side_effect=containers.ContainerError("slot")))
    res = client.post("/api/spore-studio/containers/preview", json={"container": {}})
    assert res.status_code == 400 and _json(res) == {"error": "slot"}
    monkeypatch.setattr(containers, "render_shell", mock.Mock(side_effect=RuntimeError("x")))
    assert client.post("/api/spore-studio/containers/preview", json={"container": {}}).status_code == 500


def test_container_save(client, containers, monkeypatch):
    save = mock.Mock(return_value={"route": "s"})
    monkeypatch.setattr(containers, "save_container", save)
    assert client.post("/api/spore-studio/containers/save", json={"container": 1}).status_code == 400
    res = client.post("/api/spore-studio/containers/save",
                      json={"container": {"a": 1}, "previous_route": "old"})
    assert _json(res) == {"ok": True, "container": {"route": "s"}}
    save.assert_called_once_with({"a": 1}, previous_route="old")
    save.side_effect = containers.ContainerError("taken")
    assert client.post("/api/spore-studio/containers/save", json={"container": {}}).status_code == 400
    save.side_effect = RuntimeError
    assert client.post("/api/spore-studio/containers/save", json={"container": {}}).status_code == 500


def test_container_delete(client, containers, monkeypatch):
    delete = mock.Mock()
    monkeypatch.setattr(containers, "delete_container", delete)
    assert _json(client.post("/api/spore-studio/containers/delete", json={"route": "r"})) == {"ok": True}
    delete.assert_called_once_with("r")
    delete.side_effect = containers.ContainerError("missing")
    assert client.post("/api/spore-studio/containers/delete", json={}).status_code == 400
    delete.side_effect = RuntimeError
    assert client.post("/api/spore-studio/containers/delete", json={}).status_code == 500


def test_container_duplicate(client, containers, monkeypatch):
    dup = mock.Mock(return_value={"route": "copy"})
    monkeypatch.setattr(containers, "duplicate_container", dup)
    res = client.post("/api/spore-studio/containers/duplicate",
                      json={"route": "a", "name": "B", "new_route": "b"})
    assert _json(res) == {"ok": True, "container": {"route": "copy"}}
    dup.assert_called_once_with("a", "B", "b")
    dup.side_effect = containers.ContainerError("x")
    assert client.post("/api/spore-studio/containers/duplicate", json={}).status_code == 400
    dup.side_effect = RuntimeError
    assert client.post("/api/spore-studio/containers/duplicate", json={}).status_code == 500


def test_poll_state(client, monkeypatch):
    import modules.poll_manager as pm

    mgr = types.SimpleNamespace(get_overlay_state=lambda name: {"template": name})
    monkeypatch.setattr(pm, "get_poll_manager", lambda: mgr)
    assert _json(client.get("/api/spore-studio/poll-state/poll1")) == {"template": "poll1"}
    monkeypatch.setattr(pm, "get_poll_manager", mock.Mock(side_effect=RuntimeError))
    assert client.get("/api/spore-studio/poll-state/poll1").status_code == 500


def test_model_route_adds_preview_mocks_for_spore_models(client, monkeypatch):
    import modules.spore_studio.preview_mocks as pmocks
    import modules.spore_studio.template_parser_back as tpb

    monkeypatch.setattr(pmocks, "derive_preview_mocks", lambda m: ["mock"])
    monkeypatch.setattr(tpb, "parse_existing", lambda n: {"template_name": n})
    assert _json(client.get("/api/spore-studio/model/t")) == {"template_name": "t", "preview_mocks": ["mock"]}
    monkeypatch.setattr(tpb, "parse_existing", lambda n: {"legacy": True})
    assert _json(client.get("/api/spore-studio/model/t")) == {"legacy": True}
    monkeypatch.setattr(tpb, "parse_existing", mock.Mock(side_effect=RuntimeError))
    assert client.get("/api/spore-studio/model/t").status_code == 500


def test_ui_prefs_get_post_error(client, monkeypatch):
    import modules.spore_studio.ui_prefs as up

    monkeypatch.setattr(up, "load_prefs", lambda: {"grid": True})
    monkeypatch.setattr(up, "update_prefs", lambda p: {"updated": p})
    assert _json(client.get("/api/spore-studio/ui-prefs")) == {"grid": True}
    assert _json(client.post("/api/spore-studio/ui-prefs", json={"x": 1})) == {"updated": {"x": 1}}
    monkeypatch.setattr(up, "load_prefs", mock.Mock(side_effect=RuntimeError))
    assert client.get("/api/spore-studio/ui-prefs").status_code == 500


@pytest.fixture
def aw(monkeypatch):
    import modules.spore_studio.assets_watcher as aw

    return aw


def test_asset_upload(client, aw, monkeypatch):
    save = mock.Mock(return_value={"files": ["a.png"]})
    monkeypatch.setattr(aw, "save_uploaded_asset", save)
    url = "/api/spore-studio/assets/tpl/upload"
    assert client.post(url, data={}).status_code == 400
    res = client.post(url, data={"file": (io.BytesIO(b"data"), "a.png")},
                      content_type="multipart/form-data")
    assert _json(res) == {"files": ["a.png"]}
    save.assert_called_once_with("tpl", "a.png", b"data")
    save.side_effect = ValueError("bad type")
    res = client.post(url, data={"file": (io.BytesIO(b"x"), "a.exe")}, content_type="multipart/form-data")
    assert res.status_code == 400 and _json(res) == {"error": "bad type"}
    save.side_effect = RuntimeError("disk")
    res = client.post(url, data={"file": (io.BytesIO(b"x"), "a.png")}, content_type="multipart/form-data")
    assert res.status_code == 500


@pytest.mark.parametrize("suffix, attr, method, body, result, expected", [
    ("/delete", "delete_asset", "post", {"rel_path": "a.png"}, {"files": []}, {"files": []}),
    ("/reveal", "reveal_assets_folder", "post", None, "/folder", {"ok": True, "folder": "/folder"}),
    ("", "request_snapshot", "get", None, {"files": [1]}, {"files": [1]}),
])
def test_asset_delete_reveal_snapshot(client, aw, monkeypatch, suffix, attr, method, body, result, expected):
    fn = mock.Mock(return_value=result)
    monkeypatch.setattr(aw, attr, fn)
    url = f"/api/spore-studio/assets/tpl{suffix}"
    call = getattr(client, method)
    kwargs = {"json": body} if body is not None else {}
    res = call(url, **kwargs)
    assert res.status_code == 200 and _json(res) == expected
    assert fn.call_args[0][0] == "tpl"
    fn.side_effect = ValueError("nope")
    assert call(url, **kwargs).status_code == 400
    fn.side_effect = RuntimeError("io")
    assert call(url, **kwargs).status_code == 500


@pytest.fixture
def sp(monkeypatch):
    import modules.spore_studio.save_pipeline as sp

    return sp


def test_spore_save(client, sp, monkeypatch):
    save = mock.Mock(return_value={"template_name": "t"})
    monkeypatch.setattr(sp, "save_template", save)
    assert client.post("/api/spore-studio/save", json={"model": []}).status_code == 400
    res = client.post("/api/spore-studio/save", json={"model": {"template_name": "t"}})
    assert _json(res) == {"ok": True, "model": {"template_name": "t"}}
    save.side_effect = sp.SporeStudioError("invalid")
    res = client.post("/api/spore-studio/save", json={"model": {}})
    assert res.status_code == 400 and _json(res) == {"error": "invalid"}
    save.side_effect = RuntimeError("io")
    assert client.post("/api/spore-studio/save", json={"model": {}}).status_code == 500


def test_preview_register(engine, client):
    keep = mock.Mock()
    engine._register_spore_preview_session_keep_draft = keep
    assert client.post("/api/spore-studio/preview/register", json={"token": "t"}).status_code == 400
    res = client.post("/api/spore-studio/preview/register", json={"token": 5, "template": "alerts"})
    assert _json(res) == {"ok": True}
    keep.assert_called_once_with("5", "alerts")
    assert engine.get_assets_watch_templates() == ["alerts"]
    keep.side_effect = RuntimeError("x")
    assert client.post("/api/spore-studio/preview/register",
                       json={"token": "t", "template": "a"}).status_code == 500


def test_preview_draft(engine, client, sp, monkeypatch):
    monkeypatch.setattr(sp, "compile_preview_draft", lambda m: ("<html/>", {"cfg": 1}))
    url = "/api/spore-studio/preview/draft"
    assert client.post(url, json={"token": "t"}).status_code == 400
    # session not registered
    res = client.post(url, json={"token": "t", "model": {"template_name": "a"}})
    assert res.status_code == 400 and "not registered" in _json(res)["error"]
    engine._preview_sessions["t"] = {"template": "a"}
    rev = engine._preview_sessions_rev
    res = client.post(url, json={"token": "t", "model": {"template_name": " a "}})
    assert _json(res) == {"ok": True}
    sess = engine._preview_sessions["t"]
    assert sess["draft_html"] == "<html/>" and sess["draft_config"] == {"cfg": 1} and "ts" in sess
    assert engine._preview_sessions_rev == rev + 1
    monkeypatch.setattr(sp, "compile_preview_draft", mock.Mock(side_effect=sp.SporeStudioError("bad")))
    assert client.post(url, json={"token": "t", "model": {}}).status_code == 400
    monkeypatch.setattr(sp, "compile_preview_draft", mock.Mock(side_effect=RuntimeError))
    assert client.post(url, json={"token": "t", "model": {}}).status_code == 500


def test_preview_release(engine, client):
    engine._preview_sessions["t"] = {"template": "a"}
    engine._preview_iframe_sids["t"] = {"sid"}
    assert _json(client.post("/api/spore-studio/preview/release", json={})) == {"ok": True}
    assert "t" in engine._preview_sessions
    assert _json(client.post("/api/spore-studio/preview/release", json={"token": "t"})) == {"ok": True}
    assert "t" not in engine._preview_sessions and "t" not in engine._preview_iframe_sids
    engine._bump_preview_sessions_rev = mock.Mock(side_effect=RuntimeError)
    assert client.post("/api/spore-studio/preview/release", json={"token": "t"}).status_code == 500


# --------------------------------------------------------------------------- preview resolution


@pytest.fixture
def tps(monkeypatch):
    import modules.obs_service as obs_mod
    import modules.template_preview_settings as tps

    store = {}
    monkeypatch.setattr(tps, "get_template_preview_resolution", lambda k: store.get(k))

    def _set(k, w, h):
        try:
            store[k] = (int(w), int(h))
            return True
        except (TypeError, ValueError):
            return False

    monkeypatch.setattr(tps, "set_template_preview_resolution", _set)
    monkeypatch.setattr(tps, "clear_template_preview_resolution", lambda k: store.pop(k, None))
    lookup = mock.Mock(return_value=None)
    monkeypatch.setattr(obs_mod.obs_service, "lookup_browser_source_size", lookup)
    return types.SimpleNamespace(store=store, lookup=lookup)


URL_RES = "/api/template-preview-settings/resolution"


def test_resolution_get_requires_key(client, tps):
    assert client.get(URL_RES).status_code == 400


def test_resolution_get_from_obs(engine, client, tps):
    tps.lookup.return_value = {"width": "1280", "height": 720, "source_name": "Alerts"}
    body = _json(client.get(URL_RES + "?key=alerts&route=/alerts"))
    assert body == {"ok": True, "key": "alerts", "width": 1280, "height": 720,
                    "source": "obs", "obs_source_name": "Alerts"}
    tps.lookup.assert_called_once_with("/alerts", 5999)


def test_resolution_get_stored_then_estimate(client, tps):
    tps.store["alerts"] = (640, 360)
    body = _json(client.get(URL_RES + "?key=alerts"))
    assert (body["width"], body["height"], body["source"]) == (640, 360, "stored")
    body = _json(client.get(URL_RES + "?key=other&design_width=300.5&design_height=100"))
    assert (body["width"], body["height"], body["source"]) == (300, 100, "estimate")
    body = _json(client.get(URL_RES + "?key=other"))
    assert (body["width"], body["height"]) == (800, 200)
    body = _json(client.get(URL_RES + "?key=other&design_width=abc"))
    assert (body["width"], body["height"]) == (800, 200)


def test_resolution_get_obs_lookup_error_falls_back(client, tps):
    tps.lookup.side_effect = RuntimeError("obs down")
    body = _json(client.get(URL_RES + "?key=k"))
    assert body["source"] == "estimate"


def test_resolution_post_set_clear_invalid(client, tps):
    assert client.post(URL_RES, json={}).status_code == 400
    body = _json(client.post(URL_RES, json={"key": "a", "width": 500, "height": 300}))
    assert body == {"ok": True, "key": "a", "width": 500, "height": 300, "source": "stored"}
    assert _json(client.post(URL_RES, json={"key": "a", "clear": True})) == {"ok": True, "cleared": True}
    assert "a" not in tps.store
    assert client.post(URL_RES, json={"key": "a", "width": "x"}).status_code == 400


def test_resolution_post_set_but_not_readable(client, tps, monkeypatch):
    import modules.template_preview_settings as m

    monkeypatch.setattr(m, "set_template_preview_resolution", lambda *a: True)
    body = _json(client.post(URL_RES, json={"key": "z", "width": 1, "height": 1}))
    assert body["width"] is None and body["height"] is None


def test_resolution_unexpected_error(client, tps, monkeypatch):
    import modules.template_preview_settings as m

    monkeypatch.setattr(m, "clear_template_preview_resolution", mock.Mock(side_effect=RuntimeError("x")))
    assert client.post(URL_RES, json={"key": "a", "clear": 1}).status_code == 500


# --------------------------------------------------------------------------- notify proxy / template log


def test_notify_proxy(client, monkeypatch):
    import modules.notification_engine as ne

    notify = mock.Mock()
    monkeypatch.setattr(ne, "notify", notify)
    monkeypatch.setattr(ne, "nav_spore_template", lambda name: [{"open": name}])
    url = "/api/spore-studio/notify"
    assert client.post(url, json={"message": "  "}).status_code == 400
    res = client.post(url, json={"message": "Saved " + "x" * 700, "type": "success", "template": "t",
                                 "dedupe": "k", "hint": 5, "title": "T"})
    assert _json(res) == {"ok": True}
    args, kw = notify.call_args
    assert len(args[0]) == 600
    assert kw["type"] == "success" and kw["source"] == "spore_studio" and kw["title"] == "T"
    assert kw["hint"] is None and kw["dedupe_key"] == "spore:k"
    assert kw["actions"] == [{"open": "t"}]
    client.post(url, json={"title": "Only title"})
    kw = notify.call_args[1]
    assert kw["type"] == "info" and kw["actions"] is None and kw["dedupe_key"] is None
    notify.side_effect = RuntimeError("x")
    assert client.post(url, json={"message": "m"}).status_code == 500


def test_notify_proxy_rejects_lan_peers(client):
    res = client.post("/api/spore-studio/notify", json={"message": "m"},
                      environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert res.status_code == 403


@pytest.mark.parametrize("result, code", [
    ((True, None), 204), ((False, "rate_limited"), 429), ((False, "bad level"), 400), ((False, None), 400),
])
def test_template_log(engine, client, monkeypatch, result, code):
    seen = []
    monkeypatch.setattr(we, "process_template_log", lambda p, k, lim: seen.append((p, k, lim)) or result)
    res = client.post("/api/template-log", json={"level": "info"})
    assert res.status_code == code
    assert seen[0][0] == {"level": "info"} and seen[0][1] == "127.0.0.1"
    assert seen[0][2] is engine._template_log_rate_limiter
    if code == 400:
        assert _json(res) == {"error": result[1] or "invalid payload"}


def test_preview_emit(engine, client):
    emit = mock.Mock(return_value=(True, None, "follow_alert"))
    engine.emit_preview_mock = emit
    url = "/api/spore-studio/preview/emit"
    assert client.post(url, json={"token": "t"}).status_code == 400
    res = client.post(url, json={"token": "t", "event": "follow", "payload": {"u": 1}, "alert_type": "f"})
    assert _json(res) == {"ok": True, "event": "follow_alert"}
    emit.assert_called_with("t", "follow", {"u": 1}, alert_type="f")
    client.post(url, json={"token": "t", "event": "e", "data": {"d": 1}, "payload": {"p": 1}})
    assert emit.call_args[0][2] == {"d": 1}
    emit.return_value = (True, None, None)
    assert _json(client.post(url, json={"token": "t", "event": "e"}))["event"] == "e"
    emit.return_value = (False, "No preview iframe for token", None)
    assert client.post(url, json={"token": "t", "event": "e"}).status_code == 404
    emit.return_value = (False, "bad event", None)
    assert client.post(url, json={"token": "t", "event": "e"}).status_code == 400
    emit.return_value = (False, None, None)
    res = client.post(url, json={"token": "t", "event": "e"})
    assert res.status_code == 400 and _json(res) == {"error": "emit failed"}
    emit.side_effect = RuntimeError
    assert client.post(url, json={"token": "t", "event": "e"}).status_code == 500


def test_preview_mocks(client, monkeypatch):
    import modules.spore_studio.preview_mocks as pm

    monkeypatch.setattr(pm, "derive_preview_mocks", lambda m: [m["x"]])
    url = "/api/spore-studio/preview/mocks"
    assert client.post(url, json={}).status_code == 400
    assert _json(client.post(url, json={"model": {"x": 3}})) == {"preview_mocks": [3]}
    assert client.post(url, json={"model": {}}).status_code == 500


def test_spore_create(client, sp, monkeypatch):
    create = mock.Mock(return_value={"template_name": "n"})
    monkeypatch.setattr(sp, "create_template", create)
    res = client.post("/api/spore-studio/create",
                      json={"name": "n", "copy_from": "", "design_width": "1920", "design_height": "-1"})
    assert _json(res) == {"ok": True, "model": {"template_name": "n"}}
    create.assert_called_once_with(name="n", alert_system="queue", copy_from=None,
                                   design_width=1920, design_height=200)
    create.side_effect = sp.SporeStudioError("exists")
    assert client.post("/api/spore-studio/create", json={}).status_code == 400
    create.side_effect = RuntimeError
    assert client.post("/api/spore-studio/create", json={}).status_code == 500


def test_spore_delete(client, sp, monkeypatch):
    delete = mock.Mock()
    monkeypatch.setattr(sp, "delete_template", delete)
    assert _json(client.post("/api/spore-studio/delete", json={"name": "n"})) == {"ok": True}
    delete.assert_called_once_with("n")
    delete.side_effect = sp.SporeStudioError("protected")
    assert client.post("/api/spore-studio/delete", json={}).status_code == 400
    delete.side_effect = RuntimeError
    assert client.post("/api/spore-studio/delete", json={}).status_code == 500


# --------------------------------------------------------------------------- Stream Deck HTTP API


@pytest.fixture
def paused_hooks(engine, monkeypatch):
    """Real set_alerts_paused, with its side-channels faked; socketio.emit recorded."""
    monkeypatch.setattr(we, "_notify_alert_processor", lambda **kw: None)
    monkeypatch.setattr(we, "_sync_pause_button_state", lambda: None)
    monkeypatch.setattr(we, "broadcast_alert_queue_state", lambda force=False: None)
    engine.socketio.emit = mock.Mock()
    we.ALERTS_PAUSED = False
    return engine.socketio.emit


@pytest.mark.parametrize("url, method", [
    ("/api/streamdeck/toggle_alerts", "post"),
    ("/api/streamdeck/deeplink/toggle_alerts", "get"),
])
def test_toggle_alerts_flips_and_emits(client, paused_hooks, url, method):
    body = _json(getattr(client, method)(url))
    assert body["success"] is True and body["paused"] is True and body["message"] == "Alerts paused"
    assert we.ALERTS_PAUSED is True
    assert [c[0][0] for c in paused_hooks.call_args_list] == ["alerts_paused", "pause_status_update"]
    paused_hooks.reset_mock()
    body = _json(getattr(client, method)(url))
    assert body["paused"] is False and body["message"] == "Alerts resumed"
    assert [c[0] for c in paused_hooks.call_args_list] == [
        ("alerts_resumed", {"paused": False}), ("pause_status_update", {"paused": False})]


@pytest.mark.parametrize("url, method", [
    ("/api/streamdeck/toggle_alerts", "post"),
    ("/api/streamdeck/deeplink/toggle_alerts", "get"),
])
def test_toggle_alerts_error(client, monkeypatch, url, method):
    monkeypatch.setattr(we, "set_alerts_paused", mock.Mock(side_effect=RuntimeError("x")))
    res = getattr(client, method)(url)
    assert res.status_code == 500 and _json(res)["success"] is False


def test_deeplink_template_action(engine, client):
    dispatch = mock.Mock(return_value=("key", "evt"))
    engine._streamdeck_http_dispatch_emits = dispatch
    url = "/api/streamdeck/deeplink/template_action"
    body = _json(client.post(url, json={"templateName": "t", "actionName": "a", "actionData": {"v": 1}}))
    assert body == {"success": True, "templateName": "t", "actionName": "key", "eventName": "evt",
                    "message": "Executed t.key (event: evt)"}
    dispatch.assert_called_with("t", "a", "a", {"v": 1}, use_client_event_name=False)
    client.get(url + "?templateName=t&actionName=a&eventName=%20custom%20")
    dispatch.assert_called_with("t", "a", "custom", {}, use_client_event_name=True)
    assert client.post(url, json={"templateName": "t"}).status_code == 400
    dispatch.side_effect = RuntimeError
    assert client.post(url, json={"templateName": "t", "actionName": "a"}).status_code == 500


def test_streamdeck_template_action(engine, client):
    dispatch = mock.Mock(return_value=("k", "e"))
    engine._streamdeck_http_dispatch_emits = dispatch
    url = "/api/streamdeck/template_action"
    res = client.post(url, data="null", content_type="application/json")
    assert res.status_code == 400 and _json(res)["error"] == "No JSON data provided"
    assert client.post(url, json={"actionName": "a"}).status_code == 400
    body = _json(client.post(url, json={"templateName": "t", "actionName": "a", "eventName": "ev"}))
    assert body["actionName"] == "k" and body["eventName"] == "e"
    dispatch.assert_called_with("t", "a", "ev", {}, use_client_event_name=True)
    client.post(url, json={"templateName": "t", "actionName": "a", "eventName": "  "})
    dispatch.assert_called_with("t", "a", "a", {}, use_client_event_name=False)
    dispatch.side_effect = RuntimeError("x")
    assert client.post(url, json={"templateName": "t", "actionName": "a"}).status_code == 500
    # Non-JSON body raises inside get_json -> 500
    assert client.post(url, data="x", content_type="text/plain").status_code in (400, 415, 500)


ACTION_CONFIGS = {
    "deck": {
        "streamdeck_options": {
            "description": "Deck template",
            "actions": {
                "go": {"name": "Go", "description": "d", "event": "deck_go", "default_data": {"a": 1}},
                "plain": {},
                "junk": "not a dict",
            },
        },
    },
    "noactions": {"streamdeck_options": {"description": "x"}},
    "dyn": {
        "dynamic_controls": {
            "elements": [
                {"type": "counter_control", "action": "deaths_increment", "label": "Deaths"},
                {"type": "number_input", "action": "set_n", "value": 4},
                {"type": "number_input", "action": "set_n2"},
                {"type": "text_input", "action": "set_t", "value": "hi", "description": "txt"},
                {"type": "button", "action": "press"},
                {"type": "slider", "action": "slide"},
                {"type": "label"},
                "not a dict",
            ],
        },
    },
    "dyn_noelements": {"dynamic_controls": {}},
    "broken": {},
    "listcfg": [],
}


def _check_actions(body):
    assert body["success"] is True and body["templates_count"] == 6
    by_key = {a["action_key"]: a for a in body["actions"]}
    assert body["count"] == len(body["actions"]) == 8
    assert by_key["go"] == {"template_name": "deck", "action_key": "go", "action_name": "Go",
                            "description": "d", "event": "deck_go", "default_data": {"a": 1},
                            "template_description": "Deck template", "category": "streamdeck_action"}
    assert by_key["plain"]["event"] == "deck_plain" and by_key["plain"]["action_name"] == "plain"
    assert by_key["dyn_deaths_increment"]["default_data"] == {"action": "increment"}
    assert by_key["dyn_deaths_increment"]["action_name"] == "Deaths"
    assert by_key["dyn_set_n"]["default_data"] == {"value": 4}
    assert by_key["dyn_set_n2"]["default_data"] == {}
    assert by_key["dyn_set_t"]["default_data"] == {"text": "hi"}
    assert by_key["dyn_press"]["category"] == "dynamic_control"
    assert by_key["dyn_slide"]["element_type"] == "slider"


@pytest.mark.parametrize("url", [
    "/api/streamdeck/get_template_actions",
    "/api/streamdeck/deeplink/get_template_actions",
])
def test_get_template_actions(engine, client, url):
    engine.template_config_parser = FakeParser(ACTION_CONFIGS, raise_for={"broken"})
    _check_actions(_json(client.get(url)))
    assert engine.template_config_parser.calls[0][1] == {"include_dynamic_controls": True,
                                                         "include_streamdeck_options": True}


@pytest.mark.parametrize("url", [
    "/api/streamdeck/get_template_actions",
    "/api/streamdeck/deeplink/get_template_actions",
])
def test_get_template_actions_error(engine, client, url):
    engine.template_config_parser = mock.Mock(get_config_files=mock.Mock(side_effect=RuntimeError("x")))
    res = client.get(url)
    assert res.status_code == 500 and _json(res)["actions"] == []


@pytest.mark.parametrize("url", [
    "/api/streamdeck/check_connection",
    "/api/streamdeck/deeplink/check_connection",
])
def test_check_connection(client, url, monkeypatch):
    we.web_engine_running = True
    we.ALERTS_PAUSED = True
    body = _json(client.get(url))
    assert body["success"] is True and body["server_running"] is True and body["alerts_paused"] is True
    assert "timestamp" in body
    monkeypatch.setattr(we, "datetime", mock.Mock(now=mock.Mock(side_effect=RuntimeError("clock"))))
    res = client.get(url)
    assert res.status_code == 500 and _json(res)["connected"] is False


def test_deeplink_unknown_action_and_outer_error(client, monkeypatch):
    res = client.get("/api/streamdeck/deeplink/explode")
    assert res.status_code == 400 and _json(res)["error"] == "Unknown action"
    monkeypatch.setattr(we, "logger", mock.Mock(info=mock.Mock(side_effect=RuntimeError("log"))))
    res = client.get("/api/streamdeck/deeplink/explode")
    assert res.status_code == 500 and _json(res)["message"] == "Failed to handle deep link request"


def test_get_pause_status(client, monkeypatch):
    we.ALERTS_PAUSED = False
    body = _json(client.get("/api/streamdeck/get_pause_status"))
    assert body == {"success": True, "paused": False, "message": "Alerts are currently active"}
    we.ALERTS_PAUSED = True
    assert _json(client.get("/api/streamdeck/get_pause_status"))["message"] == "Alerts are currently paused"
    monkeypatch.setattr(we, "logger", mock.Mock(debug=mock.Mock(side_effect=RuntimeError("log"))))
    assert client.get("/api/streamdeck/get_pause_status").status_code == 500


def _connector(cid, name, enabled=True, desc=None, trigger_type="streamdeck"):
    from modules.connector_core import TriggerType

    tt = TriggerType.STREAMDECK if trigger_type == "streamdeck" else trigger_type
    trigger = types.SimpleNamespace(trigger_type=tt) if trigger_type else None
    return types.SimpleNamespace(connector_id=cid, name=name, enabled=enabled, description=desc,
                                 trigger=trigger)


@pytest.fixture
def connectors(monkeypatch):
    import modules.connector_manager as cm

    items = {
        "c1": _connector("c1", "beta"),
        "c2": _connector("c2", "Alpha", desc="first"),
        "c3": _connector("c3", "off", enabled=False),
        "c4": _connector("c4", "chat", trigger_type="chat_message"),
        "c5": _connector("c5", "none", trigger_type=None),
    }
    mgr = types.SimpleNamespace(
        get_connectors_by_trigger_type=lambda t: [items["c1"], items["c2"], items["c3"]],
        get_connector=lambda cid: items.get(cid),
    )
    monkeypatch.setattr(cm, "get_manager", lambda: mgr)
    return cm


def test_get_connectors(client, connectors, monkeypatch):
    body = _json(client.get("/api/streamdeck/get_connectors"))
    assert body["count"] == 2
    assert body["connectors"] == [{"connector_id": "c2", "name": "Alpha", "description": "first"},
                                  {"connector_id": "c1", "name": "beta", "description": ""}]
    monkeypatch.setattr(connectors, "get_manager", mock.Mock(side_effect=RuntimeError("db")))
    res = client.get("/api/streamdeck/get_connectors")
    assert res.status_code == 500 and _json(res)["connectors"] == []


def test_trigger_connector(client, connectors, monkeypatch):
    queued = []
    monkeypatch.setattr(we, "enqueue_streamdeck_connector_event", lambda e: queued.append(e) or True)
    url = "/api/streamdeck/trigger_connector"
    assert client.post(url, json={}).status_code == 400
    assert client.post(url, json={"connectorId": "zz"}).status_code == 404
    assert _json(client.post(url, json={"connectorId": "c3"}))["message"] == "Connector is disabled"
    assert "not a Stream Deck" in _json(client.post(url, json={"connectorId": "c4"}))["message"]
    assert client.post(url, json={"connector_id": "c5"}).status_code == 400
    body = _json(client.post(url, json={"connector_id": "c1"}))
    assert body == {"success": True, "connector_id": "c1", "message": "Queued connector 'beta'"}
    assert queued[0]["event_type"] == "streamdeck" and queued[0]["connector_id"] == "c1"
    monkeypatch.setattr(we, "enqueue_streamdeck_connector_event", lambda e: False)
    assert client.post(url, json={"connectorId": "c1"}).status_code == 503
    monkeypatch.setattr(connectors, "get_manager", mock.Mock(side_effect=RuntimeError))
    assert client.post(url, json={"connectorId": "c1"}).status_code == 500


# --------------------------------------------------------------------------- construction


def test_constructor_state_and_wiring(engine, tmp_path):
    assert engine.port == 5999 and engine.host == "0.0.0.0"
    assert engine.template_dir.endswith("templates")
    assert isinstance(engine.app.wsgi_app, we.LocalOnlyMiddleware)
    assert engine.app.config["SEND_FILE_MAX_AGE_DEFAULT"] == 0
    assert engine.is_running is False and engine.server_thread is None
    assert engine._restart_attempts == 0 and engine._preview_sessions == {}
    assert engine._safe_emit_queue.maxsize == we._SAFE_EMIT_QUEUE_MAX
    rules = {r.rule for r in engine.app.url_map.iter_rules()}
    assert {"/api/health", "/api/streamdeck/check_connection", "/api/connectors/webhook/<connector_id>"} <= rules


def test_constructor_default_template_dir_and_frozen_reload(monkeypatch, tmp_path):
    monkeypatch.setattr(we, "get_template_path", lambda: str(tmp_path))
    monkeypatch.delenv("MYCELIAN_DEV", raising=False)
    monkeypatch.setattr(we.sys, "frozen", True, raising=False)
    eng = we.WebEngine(port=5998)
    assert eng.template_dir == str(tmp_path)
    assert eng.app.config["TEMPLATES_AUTO_RELOAD"] is False

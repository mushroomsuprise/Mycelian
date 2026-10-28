# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Shared fakes for the customsources test files (not a test module itself).

``make_env(monkeypatch, tmp_path)`` isolates modules/uiwindows/customsources.py: module
globals are reset, templates/assets point at tmp_path, preview settings live in memory,
JavaScript / timers / notifications / threads are recorded instead of run.
"""

from __future__ import annotations

import json
import os
import threading
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from nicegui import ui
from nicegui.testing.user_interaction import UserInteraction

from modules import notification_engine
from modules import template_copy_progress as tcp
from modules.spore_studio import save_pipeline, template_parser_back
from modules.uiwindows import customsources as cs

_RealThread = threading.Thread
_INTERCEPTED_THREADS = {"CsPreviewObsSize", "TemplateDuplicate", "TemplateDelete"}


class FakeEngine:
    def __init__(self, running: bool = True, port: int = 5000):
        self.is_running = running
        self.port = port
        self.pushed: List[tuple] = []
        self.refreshed: List[str] = []
        self.mocks: List[tuple] = []
        self.mock_result = (True, None, None)
        self.push_error: Optional[Exception] = None
        self.invalidated = 0
        self.routes = 0
        self.settings_emits: List[str] = []

    def push_preview_overrides(self, token, route, fd):
        if self.push_error:
            raise self.push_error
        self.pushed.append((token, route, dict(fd)))

    def emit_preview_config_refresh(self, token):
        self.refreshed.append(token)

    def emit_preview_mock(self, token, event, payload, alert_type=None):
        self.mocks.append((event, alert_type))
        return self.mock_result

    def invalidate_all_template_configs_cache(self):
        self.invalidated += 1

    def register_routes(self):
        self.routes += 1

    def emit_preview_settings_to_iframe(self, token):
        self.settings_emits.append(token)


class FakeParser:
    """Stand-in for TemplateConfigParser (in-memory configs)."""

    def __init__(self, configs: Optional[Dict[str, dict]] = None, hidden=(), config_dir: str = ""):
        self.configs = configs or {}
        self.hidden = set(hidden)
        self.saved: List[tuple] = []
        self.save_ok = True
        self.last_error = ""
        self.invalidations = 0
        self.config_dir = config_dir
        self.load_error: Optional[Exception] = None

    def get_non_hidden_config_files(self):
        return [n for n in self.configs if n not in self.hidden]

    def invalidate_cache(self, name=None):
        self.invalidations += 1

    def load_config(self, name, include_dynamic_controls=False, **kw):
        if self.load_error:
            raise self.load_error
        return json.loads(json.dumps(self.configs.get(name, {"elements": []})))

    def save_config(self, name, config):
        self.saved.append((name, json.loads(json.dumps(config))))
        if self.save_ok:
            self.configs[name] = json.loads(json.dumps(config))
        return self.save_ok

    def get_config_path(self, name):
        if "/" in name or ".." in name:
            raise ValueError(name)
        return os.path.join(self.config_dir, f"{name}.json")


class Handle:
    def __init__(self, delay, cb):
        self.delay = delay
        self.cb = cb
        self.active = True


class Env(SimpleNamespace):
    def run_scheduled(self, limit: int = 50) -> int:
        """Run (and drop) every queued layout/app callback, including ones they queue."""
        n = 0
        while self.scheduled and n < limit:
            h = self.scheduled.pop(0)
            h.cb()
            n += 1
        return n

    def titles(self) -> List[str]:
        return [kw.get("title") for _msg, kw in self.notes]


def make_env(monkeypatch, tmp_path, *, engine: Any = "default") -> Env:
    templates = tmp_path / "templates"
    assets = tmp_path / "assets"
    (templates / "template_configs").mkdir(parents=True)
    (templates / "_spore").mkdir()
    assets.mkdir()

    def tpl(rel=""):
        return os.path.join(str(templates), rel) if rel else str(templates)

    def ast(rel=""):
        return os.path.join(str(assets), rel) if rel else str(assets)

    monkeypatch.setattr(cs, "get_template_path", tpl)
    monkeypatch.setattr(cs, "get_assets_path", ast)
    monkeypatch.setattr(template_parser_back, "get_template_path", tpl)
    from modules import custom_sources_preview_mocks as mocks
    from modules import path_utils

    monkeypatch.setattr(mocks, "get_template_path", tpl)
    monkeypatch.setattr(path_utils, "get_assets_path", ast)

    env = Env(js=[], scheduled=[], app_scheduled=[], notes=[], settings={
        "enable_preview_sounds": True, "show_mock_toolbar": True, "show_inline_preview": True,
        "split_editor_pct": 56.0, "resolutions": {}}, saves=[], save_ok=True, threads=[],
        templates=templates, assets=assets, footer=[], ui_loop=[])

    monkeypatch.setattr(ui, "run_javascript", lambda code, *a, **k: env.js.append(code))

    def layout_schedule(delay, cb, once=False):
        h = Handle(delay, cb)
        env.scheduled.append(h)
        return h

    def app_schedule(delay, cb, once=False, **kw):
        h = Handle(delay, cb)
        env.app_scheduled.append(h)
        env.scheduled.append(h)
        return h

    def run_on_ui_loop(fn):
        env.ui_loop.append(fn)
        fn()

    monkeypatch.setattr(cs, "layout_schedule", layout_schedule)
    monkeypatch.setattr(cs, "app_schedule", app_schedule)
    monkeypatch.setattr(cs, "run_on_ui_loop", run_on_ui_loop)
    monkeypatch.setattr(cs, "notify", lambda msg="", **kw: env.notes.append((msg, kw)))

    # preview settings (in memory)
    def load_settings():
        data = dict(env.settings)
        data["resolutions"] = dict(env.settings.get("resolutions") or {})
        return data

    def save_settings(updates):
        env.saves.append(dict(updates))
        if not env.save_ok:
            return False
        if "resolutions" in updates:
            res = dict(env.settings.get("resolutions") or {})
            for k, v in updates["resolutions"].items():
                if v is None:
                    res.pop(k, None)
                else:
                    res[k] = v
            env.settings["resolutions"] = res
            updates = {k: v for k, v in updates.items() if k != "resolutions"}
        env.settings.update(updates)
        return True

    def get_res(key):
        entry = (env.settings.get("resolutions") or {}).get(key)
        return (entry["width"], entry["height"]) if entry else None

    def set_res(key, w, h):
        return save_settings({"resolutions": {key: {"width": int(w), "height": int(h)}}})

    def clear_res(key):
        return save_settings({"resolutions": {key: None}})

    monkeypatch.setattr(cs, "load_template_preview_settings", load_settings)
    monkeypatch.setattr(cs, "save_template_preview_settings", save_settings)
    monkeypatch.setattr(cs, "get_template_preview_resolution", get_res)
    monkeypatch.setattr(cs, "set_template_preview_resolution", set_res)
    monkeypatch.setattr(cs, "clear_template_preview_resolution", clear_res)

    # engine
    eng = FakeEngine() if engine == "default" else engine
    monkeypatch.setattr(cs.web_engine_module, "web_engine_instance", eng, raising=False)
    env.engine = eng

    # module globals
    monkeypatch.setattr(cs, "form_data_store", {})
    monkeypatch.setattr(cs, "_disk_baselines", {})
    monkeypatch.setattr(cs, "_active_config_name", None)
    monkeypatch.setattr(cs, "_suppress_config_change", False)
    monkeypatch.setattr(cs, "_custom_sources_ctx", {})
    monkeypatch.setattr(cs, "_popout_preview_is_open", False)
    monkeypatch.setattr(cs, "_active_preview_size", {
        "width": 1920, "height": 1080, "config": "", "source": "estimate", "obs_source_name": ""})
    monkeypatch.setattr(cs, "_preview_size_resolve_gen", [0])
    monkeypatch.setattr(cs, "_preview_route_cache", {})
    monkeypatch.setattr(cs, "_custom_sources_preview_gen", [0])
    monkeypatch.setattr(cs, "_form_state", {"search": "", "changed_only": False})
    monkeypatch.setattr(cs, "_search_render_generation", 0)
    monkeypatch.setattr(cs, "_color_field_params_cache", cs._color_field_params_cache)

    # template copy progress + footer
    monkeypatch.setattr(tcp, "_state", dict(tcp._state))
    monkeypatch.setattr(notification_engine, "start_alert_trim_footer_poll", lambda: None)
    monkeypatch.setattr(notification_engine, "refresh_template_copy_footer", lambda: env.footer.append(1))

    # background threads used by the module run synchronously
    class FakeThread:
        def __new__(cls, *args, target=None, name=None, daemon=None, **kw):
            if name not in _INTERCEPTED_THREADS:
                return _RealThread(*args, target=target, name=name, daemon=daemon, **kw)
            obj = object.__new__(cls)
            obj.target = target
            obj.name = name
            return obj

        def start(self):
            env.threads.append(self.name)
            self.target()

    monkeypatch.setattr(threading, "Thread", FakeThread)
    return env


def fire(user, el, event="click", args=None):
    UserInteraction(user, {el}, None).trigger(event, args)


class FakeSelect:
    def __init__(self, value=None, options=None):
        self.value = value
        self.options = list(options or [])
        self.is_deleted = False


class FakeEl:
    """Minimal element: visibility, content, text, classes, enabled."""

    def __init__(self):
        self.visible = True
        self.content = None
        self.text = ""
        self.class_ops: List[tuple] = []
        self.enabled = None
        self.cleared = 0
        self.value = None

    def set_visibility(self, v):
        self.visible = v

    def set_content(self, c):
        self.content = c

    def set_text(self, t):
        self.text = t

    def classes(self, add=None, remove=None):
        self.class_ops.append((add, remove))
        return self

    def set_enabled(self, v):
        self.enabled = v

    def clear(self):
        self.cleared += 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Pass 3 web engine items: Spore Studio work off the hub and its caches (WE-40)."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import web_engine as we  # noqa: E402
from modules.spore_studio import save_pipeline as sp  # noqa: E402


def _engine():
    eng = object.__new__(we.WebEngine)
    eng._template_config_generation = 0
    return eng


def test_compile_draft_is_reused_for_the_same_model(monkeypatch):
    calls = []
    monkeypatch.setattr(sp, "compile_preview_draft", lambda m: calls.append(1) or ("<html>", {"elements": [1]}))
    eng = _engine()
    model = {"template_name": "t", "elements": [{"id": "a"}]}
    html, config = eng._spore_compile_draft(model)
    config["elements"].append("mutated by caller")
    html2, config2 = eng._spore_compile_draft(dict(model))
    assert calls == [1] and html2 == "<html>" and config2 == {"elements": [1]}  # copies, not shared
    eng._spore_compile_draft({"template_name": "t", "elements": []})
    assert len(calls) == 2
    eng._template_config_generation += 1  # a saved config change: compile again
    eng._spore_compile_draft(model)
    assert len(calls) == 3


def test_compile_draft_cache_expires_and_is_bounded(monkeypatch):
    calls = []
    monkeypatch.setattr(sp, "compile_preview_draft", lambda m: calls.append(1) or ("h", {}))
    clock = [100.0]
    monkeypatch.setattr(we.time, "monotonic", lambda: clock[0])
    eng = _engine()
    eng._spore_compile_draft({"a": 1})
    clock[0] += we.WebEngine._SPORE_DRAFT_CACHE_TTL_SEC
    eng._spore_compile_draft({"a": 1})
    assert len(calls) == 2
    for i in range(we.WebEngine._SPORE_DRAFT_CACHE_SIZE + 3):
        eng._spore_compile_draft({"n": i})
    assert len(eng._spore_draft_cache) == we.WebEngine._SPORE_DRAFT_CACHE_SIZE


def test_compile_draft_errors_are_not_cached(monkeypatch):
    def boom(model):
        raise sp.SporeStudioError("bad element")

    monkeypatch.setattr(sp, "compile_preview_draft", boom)
    eng = _engine()
    with pytest.raises(sp.SporeStudioError):
        eng._spore_compile_draft({"a": 1})
    assert len(getattr(eng, "_spore_draft_cache", {})) == 0


def test_spore_writes_run_one_at_a_time():
    eng = _engine()
    inside, overlap = [0], []
    gate = threading.Event()

    def job(n):
        inside[0] += 1
        overlap.append(inside[0])
        gate.wait(0.05)
        inside[0] -= 1
        return n

    threads = [threading.Thread(target=eng._spore_write, args=(job, i)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(2)
    assert max(overlap) == 1
    assert eng._spore_write(lambda a, b: a + b, 2, 3) == 5


def test_draft_templates_compile_once(monkeypatch):
    from flask import Flask

    eng = _engine()
    eng.app = Flask(__name__)
    compiled = []
    real = eng.app.jinja_env.from_string
    monkeypatch.setattr(eng.app.jinja_env, "from_string", lambda src: compiled.append(src) or real(src))
    t1 = eng._compiled_draft_template("{{ a }}")
    t2 = eng._compiled_draft_template("{{ a }}")
    assert t1 is t2 and compiled == ["{{ a }}"]
    for i in range(we.WebEngine._SPORE_DRAFT_CACHE_SIZE + 2):
        eng._compiled_draft_template(f"{{{{ a }}}}{i}")
    assert len(eng._draft_template_cache) == we.WebEngine._SPORE_DRAFT_CACHE_SIZE


def test_preview_and_obs_config_bodies_use_separate_slots():
    assert we.WebEngine._template_configs_cache_slot("") == "_all_template_configs_cache"
    assert we.WebEngine._template_configs_cache_slot(None) == "_all_template_configs_cache"
    assert we.WebEngine._template_configs_cache_slot("tok") == "_all_template_configs_preview_cache"


def _preview_engine():
    eng = _engine()
    eng._preview_sessions_lock = threading.Lock()
    eng._preview_sessions = {}
    eng._preview_iframe_sids = {}
    eng._preview_iframe_tokens = {}
    eng._preview_sessions_rev = 0
    eng._assets_watch_templates_lock = threading.Lock()
    eng._assets_watch_templates = set()
    return eng


def test_sweep_stops_watching_assets_of_closed_previews():
    eng = _preview_engine()
    now = 10_000.0
    eng._preview_sessions = {
        "live": {"template": "alerts", "ts": now - 5000},  # old, but its iframe is connected
        "fresh": {"template": "chat", "ts": now - 10},  # just registered, iframe not yet up
        "closed": {"template": "title", "ts": now - 300},  # nobody watching for 5 min
    }
    eng._preview_iframe_sids = {"live": {"sid1"}}
    eng._assets_watch_templates = {"alerts", "chat", "title", "never-previewed"}
    eng._sweep_preview_sessions(now)
    assert eng.get_assets_watch_templates() == ["alerts", "chat"]
    assert set(eng._preview_sessions) == {"live", "fresh", "closed"}  # not idle long enough to drop


def test_sweep_drops_idle_sessions():
    eng = _preview_engine()
    now = 10_000.0
    eng._preview_sessions = {
        "idle": {"template": "a", "ts": now - we.WebEngine._PREVIEW_SESSION_IDLE_SEC - 1, "draft_html": "x" * 1000},
        "live": {"template": "b", "ts": 0},
        "broken": {"template": "c", "ts": "not a time"},
    }
    eng._preview_iframe_sids = {"live": {"sid"}}
    eng._sweep_preview_sessions(now)
    assert set(eng._preview_sessions) == {"live"} and eng._preview_sessions_rev == 1


def test_iframe_disconnect_and_release_sweep(monkeypatch):
    eng = _preview_engine()
    swept = []
    monkeypatch.setattr(eng, "_sweep_preview_sessions", lambda now=None: swept.append(1))
    eng._register_preview_iframe_sid("tok", "sid")
    eng._unregister_preview_iframe_sid("sid")
    assert swept == [1] and eng._preview_iframe_sids == {}
    eng._unregister_preview_iframe_sid("unknown")  # nothing to do, no sweep
    assert swept == [1]


def test_sweep_without_preview_state_is_a_no_op():
    eng = _engine()
    eng._sweep_preview_sessions()  # engines built in tests may lack the locks

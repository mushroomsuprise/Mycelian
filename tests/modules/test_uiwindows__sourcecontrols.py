# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/sourcecontrols.py (Source Controls tab: helpers, live values, send paths).

UI parts live in test_uiwindows__sourcecontrols_part2.py.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules.uiwindows import sourcecontrols as sc  # noqa: E402


class FakeEngine:
    def __init__(self, configs=None, *, running=True, port=5123, host="127.0.0.1", generation=None):
        self.configs = configs
        self.is_running = running
        self.port = port
        self.host = host
        self._template_config_generation = generation
        self.emitted = []
        self.registered = 0
        self.handlers = []

    def _local_connect_host(self):
        if isinstance(self.host, Exception):
            raise self.host
        return self.host

    def get_source_controls_configs(self):
        if isinstance(self.configs, Exception):
            raise self.configs
        return self.configs

    def emit_template_control_event(self, template, action, data):
        self.emitted.append((template, action, data))

    def register_additional_control_handlers(self):
        self.registered += 1

    def get_registered_dynamic_handlers(self):
        return self.handlers


class FakeParser:
    def __init__(self, files=None, configs=None, hidden=None):
        self.files = files or []
        self.configs = configs or {}
        self.hidden = hidden or {}

    def get_config_files(self):
        return list(self.files)

    def load_config(self, name, include_dynamic_controls=False):
        value = self.configs[name]
        if isinstance(value, Exception):
            raise value
        return value

    def is_config_hidden(self, name):
        value = self.hidden.get(name, False)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(sc, "_dirty_names", set())
    monkeypatch.setattr(sc, "_dirty_state", {"all": False})
    monkeypatch.setattr(sc, "_emit_fail_last", {"t": None})
    monkeypatch.setattr(sc, "_view", None)
    monkeypatch.setattr(sc, "source_controls_container", None)
    monkeypatch.setattr(sc.web_engine, "web_engine_instance", None, raising=False)


@pytest.fixture
def notes(monkeypatch):
    calls = []
    monkeypatch.setattr(sc, "notify", lambda msg, **kw: calls.append((msg, kw)))
    return calls


def use_parser(monkeypatch, parser):
    monkeypatch.setattr(sc.template_config_parser, "TemplateConfigParser", lambda *a, **k: parser)


# ---------------------------------------------------------------- small helpers

@pytest.mark.parametrize(
    "element,text",
    [
        ({"button_text": "Go", "label": "L", "action": "a"}, "Go"),
        ({"label": "Label", "action": "a"}, "Label"),
        ({"action": "  reset_bar "}, "Reset Bar"),
        ({"action": ""}, "Run"),
        ({}, "Run"),
    ],
)
def test_resolve_button_text(element, text):
    assert sc._resolve_button_text(element) == text


def test_is_spin_and_button_label():
    assert sc._is_spin({"type": "spin_control"})
    assert sc._is_spin({"action": "spin"})
    assert not sc._is_spin({"type": "button", "action": "go"})
    assert sc._button_label({"type": "spin_control", "button_text": "Whirl"}) == "Whirl"
    assert sc._button_label({"action": "spin", "label": "Wheel"}) == "Spin"
    assert sc._button_label({"type": "button", "label": "Hit"}) == "Hit"


def test_control_elements():
    assert sc._control_elements(None) == []
    assert sc._control_elements({"elements": "x"}) == []
    assert sc._control_elements({"elements": [{"a": 1}, "x", 3]}) == [{"a": 1}]


def test_search_text_includes_labels_and_button_text():
    text = sc._search_text("Wheel", [{"type": "spin_control"}, {"label": "Speed", "button_text": "B"},
                                     {"type": "toggle"}])
    assert text.split("\n") == ["wheel", "spin", "speed", "b"]


@pytest.mark.parametrize(
    "raw,num",
    [(None, None), (True, None), (False, None), ("", None), ("  ", None), ("abc", None), ([1], None),
     ("3", 3.0), (" 2.5 ", 2.5), (7, 7.0)],
)
def test_to_number(raw, num):
    assert sc._to_number(raw) == num


def test_fmt_number_and_slider_value():
    assert sc._fmt_number(None) == ""
    assert sc._fmt_number("4.0") == "4"
    assert sc._fmt_number(2.5) == "2.5"
    assert sc._slider_value("x") is None
    assert sc._slider_value("4.0") == 4 and isinstance(sc._slider_value("4.0"), int)
    assert sc._slider_value(1.5) == 1.5


@pytest.mark.parametrize(
    "value,lo,hi,pct",
    [(50, 0, 100, 50.0), (None, 10, 20, 0.0), (5, None, None, 5.0), (200, 0, 100, 100.0),
     (-5, 0, 100, 0.0), (3, 3, 3, 0.0), (15, 10, 20, 50.0)],
)
def test_percent(value, lo, hi, pct):
    assert sc._percent(value, lo, hi) == pct


@pytest.mark.parametrize(
    "element,step",
    [({"step": 5}, 5), ({"value": 3}, 3), ({}, 1), ({"step": 0}, 1), ({"step": "x"}, 1), ({"step": None}, 1)],
)
def test_counter_step_value(element, step):
    assert sc._counter_step_value(element) == step


def test_spore_counter_payload_decrement():
    assert sc._spore_counter_payload({}, "decrement", 4) == {
        "target_counter_id": "", "operation": "decrement", "delta": {"kind": "fixed", "value": 4}}


def test_attr_escapes():
    assert sc._attr('a "b" & c') == "a &quot;b&quot; &amp; c"
    assert sc._attr(5) == "5"


def test_template_sort_key():
    assert sc._template_sort_key("B") == ("b", "B")


# ---------------------------------------------------------------- dock url

def test_dock_url_defaults_without_engine():
    assert sc.dock_url() == "http://127.0.0.1:5000/source_controls"


def test_dock_url_with_engine(monkeypatch):
    monkeypatch.setattr(sc.web_engine, "web_engine_instance", FakeEngine(port=6000, host="localhost"))
    assert sc.dock_url() == "http://localhost:6000/source_controls"


def test_dock_url_engine_port_zero_and_host_error(monkeypatch):
    monkeypatch.setattr(sc.web_engine, "web_engine_instance", FakeEngine(port=0, host=RuntimeError("x")))
    assert sc.dock_url() == "http://127.0.0.1:5000/source_controls"


# ---------------------------------------------------------------- loading configs

def test_load_configs_from_files_branches(monkeypatch):
    parser = FakeParser(
        files=["z_dyn", "a_plain", "b_noactions", "c_nonlist", "d_list", "e_err", "f_resolve_err", "g_empty_dc"],
        configs={
            "z_dyn": {"dynamic_controls": {"elements": [{"type": "button", "action": "go", "value": 0}]}},
            "a_plain": {"elements": [
                {"type": "counter_control", "action_increment": "inc"},
                {"type": "text", "action": "x"},
                {"type": "button"},
                "junk",
                {"type": "slider", "action_reset": "r"},
                {"type": "toggle", "action_decrement": "d"},
            ]},
            "b_noactions": {"elements": [{"type": "button"}]},
            "c_nonlist": {"elements": "x"},
            "d_list": [1, 2],
            "e_err": ValueError("bad json"),
            "f_resolve_err": {"dynamic_controls": {"elements": [{"type": "button", "action": "q"}]}, "boom": True},
            "g_empty_dc": {"dynamic_controls": {}},
        },
    )

    def resolve(config):
        if config.get("boom"):
            raise RuntimeError("no db")
        dc = dict(config["dynamic_controls"])
        if dc.get("elements"):
            dc["resolved"] = True
        return dc

    monkeypatch.setattr(sc.template_config_parser, "resolve_dynamic_control_values_from_elements", resolve)
    configs, errors = sc._load_configs_from_files(parser)
    assert set(configs) == {"z_dyn", "a_plain", "f_resolve_err"}
    assert configs["z_dyn"]["resolved"] is True
    assert "resolved" not in configs["f_resolve_err"]
    assert [e["type"] for e in configs["a_plain"]["elements"]] == ["counter_control", "slider", "toggle"]
    assert [(n, type(e)) for n, e in errors] == [("e_err", ValueError)]


def test_load_configs_resolve_returns_falsy_keeps_original(monkeypatch):
    parser = FakeParser(files=["x"], configs={"x": {"dynamic_controls": {"elements": [{"type": "button"}]}}})
    monkeypatch.setattr(sc.template_config_parser, "resolve_dynamic_control_values_from_elements", lambda c: {})
    configs, _ = sc._load_configs_from_files(parser)
    assert configs == {"x": {"elements": [{"type": "button"}]}}


def test_load_configs_without_resolver(monkeypatch):
    parser = FakeParser(files=None, configs={})
    parser.get_config_files = lambda: None
    monkeypatch.delattr(sc.template_config_parser, "resolve_dynamic_control_values_from_elements")
    assert sc._load_configs_from_files(parser) == ({}, [])
    parser2 = FakeParser(files=["x"], configs={"x": {"dynamic_controls": {"elements": [{"type": "button"}]}}})
    assert sc._load_configs_from_files(parser2)[0] == {"x": {"elements": [{"type": "button"}]}}


def test_fetch_engine_error_falls_back_to_files(monkeypatch):
    monkeypatch.setattr(sc.web_engine, "web_engine_instance", FakeEngine(configs=RuntimeError("down")))
    parser = FakeParser(files=["t"], configs={"t": {"dynamic_controls": {"elements": [{"type": "button"}]}}},
                        hidden={"t": RuntimeError("x")})
    use_parser(monkeypatch, parser)
    configs, hidden, errors = sc._fetch_source_controls()
    assert list(configs) == ["t"] and hidden == set() and errors == []


def test_fetch_engine_non_dict_result_falls_back(monkeypatch):
    monkeypatch.setattr(sc.web_engine, "web_engine_instance", FakeEngine(configs=["x"]))
    parser = FakeParser(files=["t"], configs={"t": {"dynamic_controls": {"elements": [{"type": "button"}]}}},
                        hidden={"t": True})
    use_parser(monkeypatch, parser)
    configs, hidden, _ = sc._fetch_source_controls()
    assert hidden == {"t"}


def test_fetch_engine_without_getter(monkeypatch):
    monkeypatch.setattr(sc.web_engine, "web_engine_instance", SimpleNamespace())
    use_parser(monkeypatch, FakeParser())
    assert sc._fetch_source_controls() == ({}, set(), [])


def test_fetch_hidden_flag_false_in_config_skips_parser(monkeypatch):
    monkeypatch.setattr(sc.web_engine, "web_engine_instance", FakeEngine(configs={
        "a": {"elements": [{"type": "button"}], "hidden": False}}))
    use_parser(monkeypatch, FakeParser(hidden={"a": True}))
    assert sc._fetch_source_controls()[1] == set()


def test_notify_config_errors(notes):
    sc._notify_config_errors([("chat", ValueError("bad"))])
    msg, kw = notes[0]
    assert kw["title"] == "Couldn't read the controls for chat"
    assert kw["details"] == "ValueError: bad"
    assert kw["dedupe_key"] == "source_controls:config:chat"
    assert kw["type"] == "warning"
    sc._notify_config_errors([])
    assert len(notes) == 1


def test_engine_generation(monkeypatch):
    assert sc._engine_generation() is None
    monkeypatch.setattr(sc.web_engine, "web_engine_instance", FakeEngine(generation=7))
    assert sc._engine_generation() == 7


# ---------------------------------------------------------------- dirty bookkeeping

def test_mark_dirty_variants():
    sc.mark_source_controls_dirty({"template_names": ("a", "", None, 3)})
    sc.mark_source_controls_dirty({"template_names": {"b"}})
    assert sc._take_dirty() == ({"a", "3", "b"}, False)
    sc.mark_source_controls_dirty({"template_names": "notalist"})
    assert sc._take_dirty() == (set(), True)
    sc.mark_source_controls_dirty("junk")
    assert sc._take_dirty() == (set(), True)


# ---------------------------------------------------------------- _LiveControl

class FakeEl:
    def __init__(self, deleted=False, js_error=False):
        self._props = {}
        self._style = {}
        self.is_deleted = deleted
        self.updates = 0
        self.html_id = "c42"
        self.js = []
        self.class_set = set()
        self.prop_calls = []
        js_list = self.js

        def run_javascript(code):
            if js_error:
                raise RuntimeError("no client")
            js_list.append(code)

        self.client = SimpleNamespace(run_javascript=run_javascript)

    def update(self):
        self.updates += 1

    def classes(self, add=None, remove=None):
        if add:
            self.class_set.add(add)
        if remove:
            self.class_set.discard(remove)

    def props(self, text):
        self.prop_calls.append(text)


class FakeChip:
    def __init__(self):
        self.text = None

    def set_text(self, t):
        self.text = t


@pytest.fixture
def clock(monkeypatch):
    c = SimpleNamespace(t=1000.0)
    monkeypatch.setattr(sc.time, "monotonic", lambda: c.t)
    return c


def test_live_control_init_and_recent(clock):
    live = sc._LiveControl("number", FakeEl(), 3)
    assert (live.kind, live.value, live.chip, live.lo, live.hi, live.needs_sync) == ("number", 3, None, 0, 100, False)
    assert live.recently_edited() is False or clock.t < sc._USER_EDIT_GRACE_SEC
    live.last_edit = clock.t
    assert live.recently_edited()
    clock.t += sc._USER_EDIT_GRACE_SEC
    assert not live.recently_edited()


def test_user_changed_toggle_only_records(clock):
    el = FakeEl()
    live = sc._LiveControl("toggle", el, False)
    live.user_changed(True)
    assert live.value is True and live.last_edit == clock.t
    assert el._props == {} and live.needs_sync is False


def test_user_changed_number_text_slider(clock):
    el = FakeEl()
    live = sc._LiveControl("number", el, 0)
    live.user_changed(4.0)
    assert el._props["defaultValue"] == "4" and live.needs_sync

    tel = FakeEl()
    tlive = sc._LiveControl("text", tel, "")
    tlive.user_changed(None)
    assert tel._props["defaultValue"] == ""
    tlive.user_changed("hi")
    assert tel._props["defaultValue"] == "hi"

    sel, chip = FakeEl(), FakeChip()
    slive = sc._LiveControl("slider", sel, 0, chip=chip, lo=0, hi=200)
    slive.user_changed(50)
    assert sel._style["--p"] == "25.00%" and chip.text == "50" and sel._props["defaultValue"] == "50"
    nochip = sc._LiveControl("slider", FakeEl(), 0)
    nochip.user_changed(10)
    assert nochip.el._style["--p"] == "10.00%"


def test_sync_if_settled(clock):
    el = FakeEl()
    live = sc._LiveControl("number", el, 0)
    live.sync_if_settled()
    assert el.updates == 0
    live.user_changed(1)
    live.sync_if_settled()  # still recent
    assert el.updates == 0
    clock.t += 1.5
    live.sync_if_settled()
    assert el.updates == 1 and live.needs_sync is False
    gone = sc._LiveControl("number", FakeEl(deleted=True), 0)
    gone.needs_sync = True
    gone.sync_if_settled()
    assert gone.needs_sync is True


def test_apply_live_skips_deleted_and_recent(clock):
    gone = sc._LiveControl("number", FakeEl(deleted=True), 0)
    gone.apply_live(5)
    assert gone.value == 0
    el = FakeEl()
    live = sc._LiveControl("number", el, 0)
    live.user_changed(1)
    live.apply_live(9)
    assert live.value == 1


def test_apply_live_toggle(clock):
    el = FakeEl()
    live = sc._LiveControl("toggle", el, False)
    live.apply_live(0)
    assert el.prop_calls == []
    live.apply_live(1)
    assert live.value is True and "is-on" in el.class_set and el.prop_calls[-1] == 'aria-checked="true"'
    live.apply_live(False)
    assert "is-on" not in el.class_set and el.prop_calls[-1] == 'aria-checked="false"'


def test_apply_live_text(clock):
    el = FakeEl()
    live = sc._LiveControl("text", el, "a")
    live.apply_live("a")
    assert el.updates == 0
    live.apply_live("b<\"")
    assert live.value == 'b<"' and el._props["defaultValue"] == 'b<"' and el.updates == 1
    assert '"c42"' in el.js[-1] and 'e.value = "b<\\""' in el.js[-1]
    assert "--p" not in el.js[-1]
    live.apply_live(None)
    assert live.value == ""


def test_apply_live_number_and_slider(clock):
    el = FakeEl()
    live = sc._LiveControl("number", el, 2.0)
    live.apply_live("x")
    live.apply_live("2")
    assert el.updates == 0
    live.apply_live("3.5")
    assert live.value == 3.5 and el._props["defaultValue"] == "3.5" and el.updates == 1

    sel, chip = FakeEl(), FakeChip()
    slive = sc._LiveControl("slider", sel, 0, chip=chip, lo=0, hi=10)
    slive.apply_live("5")
    assert slive.value == 5 and isinstance(slive.value, int)
    assert sel._style["--p"] == "50.00%" and chip.text == "5"
    assert "e.style.setProperty('--p', \"50.00%\")" in sel.js[-1]
    nochip = sc._LiveControl("slider", FakeEl(), 0)
    nochip.apply_live(4)
    assert nochip.value == 4


def test_set_dom_value_error_swallowed(clock):
    el = FakeEl(js_error=True)
    live = sc._LiveControl("text", el, "")
    live.apply_live("x")  # no exception
    assert el.updates == 1


def test_card_holds_state():
    card = sc._Card("n", "el", "s", "sig")
    assert (card.name, card.element, card.search, card.signature, card.controls) == ("n", "el", "s", "sig", {})


# ---------------------------------------------------------------- view logic (no NiceGUI)

class FakeVisEl:
    def __init__(self, deleted=False, fail=False):
        self.visible = None
        self.is_deleted = deleted
        self.fail = fail

    def set_visibility(self, v):
        if self.fail:
            raise RuntimeError("gone")
        self.visible = v


def test_view_defaults():
    v = sc._SourceControlsView()
    assert (v.grid, v.pill, v.empty_search, v.cards, v.query, v.generation, v.inflight) == (
        None, None, None, {}, "", None, False)


def test_render_without_grid_or_deleted_grid_does_nothing():
    v = sc._SourceControlsView()
    v.render({"a": {}}, set())
    v.grid = FakeVisEl(deleted=True)
    v.render({"a": {}}, set())
    assert v.cards == {}


def test_apply_search(monkeypatch):
    v = sc._SourceControlsView()
    a, b, bad = FakeVisEl(), FakeVisEl(), FakeVisEl(fail=True)
    v.cards = {"a": sc._Card("a", a, "alpha\nspin", ""), "b": sc._Card("b", b, "beta", ""),
               "c": sc._Card("c", bad, "gamma", "")}
    v.empty_search = FakeVisEl()
    v.query = " SPIN "
    v.apply_search()
    assert a.visible is True and b.visible is False and v.empty_search.visible is False
    v.query = "zzz"
    v.apply_search()
    assert v.empty_search.visible is True
    v.query = ""
    v.apply_search()
    assert a.visible and b.visible and v.empty_search.visible is False
    v.empty_search = FakeVisEl(deleted=True)
    v.apply_search()
    assert v.empty_search.visible is None
    v.cards = {}
    v.empty_search = FakeVisEl()
    v.query = "x"
    v.apply_search()
    assert v.empty_search.visible is False


def test_apply_rerenders_on_structure_change(monkeypatch):
    v = sc._SourceControlsView()
    rendered = []
    monkeypatch.setattr(v, "render", lambda c, h: rendered.append((c, h)))
    configs = {"a": {"elements": [{"type": "toggle", "value": True}]}}
    v.apply(configs, set(), None)
    assert rendered == [(configs, set())]


def test_apply_updates_live_values_for_selected_names():
    v = sc._SourceControlsView()
    applied = []

    class Ctl:
        def __init__(self, name):
            self.name = name

        def apply_live(self, value):
            applied.append((self.name, value))

    els_a = [{"type": "toggle", "value": True}, {"type": "slider"}, {"type": "number_input", "value": 3}]
    els_b = [{"type": "toggle", "value": False}]
    v.cards = {
        "a": sc._Card("a", None, "", sc._structure_signature("a", els_a, False)),
        "b": sc._Card("b", None, "", sc._structure_signature("b", els_b, False)),
    }
    v.cards["a"].controls = {0: Ctl("a0"), 1: Ctl("a1"), 2: Ctl("a2"), 5: Ctl("a5")}
    v.cards["b"].controls = {0: Ctl("b0")}
    configs = {"a": {"elements": els_a}, "b": {"elements": els_b}}
    v.apply(configs, set(), {"a"})
    assert applied == [("a0", True), ("a2", 3)]
    applied.clear()
    v.apply(configs, set(), None)
    assert applied == [("a0", True), ("a2", 3), ("b0", False)]


def _run_tick(view, monkeypatch, fetch_result=None, fetch_exc=None):
    calls = []

    async def io_bound(fn):
        calls.append(fn)
        if fetch_exc:
            raise fetch_exc
        return fetch_result

    monkeypatch.setattr(sc.run, "io_bound", io_bound)
    asyncio.run(view.tick())
    return calls


def test_tick_syncs_controls_and_returns_without_grid(monkeypatch):
    v = sc._SourceControlsView()
    synced = []
    v.cards = {"a": sc._Card("a", None, "", "")}
    v.cards["a"].controls = {0: SimpleNamespace(sync_if_settled=lambda: synced.append(1))}
    sc.mark_source_controls_dirty(None)
    assert _run_tick(v, monkeypatch) == []
    assert synced == [1]
    v.grid = FakeVisEl(deleted=True)
    assert _run_tick(v, monkeypatch) == []
    v.grid = FakeVisEl()
    v.inflight = True
    assert _run_tick(v, monkeypatch) == []


def test_tick_nothing_dirty_does_nothing(monkeypatch):
    v = sc._SourceControlsView()
    v.grid = FakeVisEl()
    assert _run_tick(v, monkeypatch) == []


def test_tick_fetches_and_applies(monkeypatch, notes):
    v = sc._SourceControlsView()
    v.grid = FakeVisEl()
    applied = []
    monkeypatch.setattr(v, "apply", lambda c, h, n: applied.append((c, h, n)))
    sc.mark_source_controls_dirty({"template_names": ["a"]})
    calls = _run_tick(v, monkeypatch, fetch_result=({"a": 1}, {"h"}, [("bad", ValueError("x"))]))
    assert calls == [sc._fetch_source_controls]
    assert applied == [({"a": 1}, {"h"}, {"a"})]
    assert v.inflight is False
    assert notes and notes[0][1]["title"] == "Couldn't read the controls for bad"


def test_tick_generation_change_applies_everything(monkeypatch):
    engine = FakeEngine(generation=5)
    monkeypatch.setattr(sc.web_engine, "web_engine_instance", engine)
    v = sc._SourceControlsView()
    v.grid = FakeVisEl()
    v.generation = 4
    applied = []
    monkeypatch.setattr(v, "apply", lambda c, h, n: applied.append(n))
    _run_tick(v, monkeypatch, fetch_result=({}, set(), []))
    assert applied == [None] and v.generation == 5
    # same generation, nothing dirty -> no fetch
    assert _run_tick(v, monkeypatch, fetch_result=({}, set(), [])) == []


def test_tick_fetch_error_and_grid_deleted_after_fetch(monkeypatch):
    v = sc._SourceControlsView()
    v.grid = FakeVisEl()
    sc.mark_source_controls_dirty(None)
    _run_tick(v, monkeypatch, fetch_exc=RuntimeError("io"))
    assert v.inflight is False

    applied = []
    monkeypatch.setattr(v, "apply", lambda *a: applied.append(a))

    async def io_bound(fn):
        v.grid.is_deleted = True
        return ({}, set(), [])

    monkeypatch.setattr(sc.run, "io_bound", io_bound)
    sc.mark_source_controls_dirty(None)
    asyncio.run(v.tick())
    assert applied == []


# ---------------------------------------------------------------- send paths

def test_send_websocket_event_ok(monkeypatch, notes):
    engine = FakeEngine()
    monkeypatch.setattr(sc.web_engine, "web_engine_instance", engine)
    sc.send_websocket_event("chat", "clear", {"x": 1})
    assert engine.emitted == [("chat", "clear", {"x": 1})]
    assert notes == []


def test_send_websocket_event_engine_missing_or_stopped(monkeypatch, notes, clock):
    sc.send_websocket_event("chat", "clear_all", {})
    assert notes[0][0] == "The overlay server isn't running, so the overlay didn't change."
    assert notes[0][1]["title"] == "“clear all” wasn't sent to chat"
    assert notes[0][1]["details"] is None
    # throttled
    monkeypatch.setattr(sc.web_engine, "web_engine_instance", FakeEngine(running=False))
    sc.send_websocket_event("chat", "x", {})
    assert len(notes) == 1
    clock.t += sc._EMIT_FAIL_NOTIFY_INTERVAL_SEC
    sc.send_websocket_event("chat", None, {})
    assert len(notes) == 2 and notes[1][1]["title"] == "“control” wasn't sent to chat"


def test_send_websocket_event_emit_error(monkeypatch, notes):
    engine = FakeEngine()

    def boom(*a):
        raise ConnectionError("socket")

    engine.emit_template_control_event = boom
    monkeypatch.setattr(sc.web_engine, "web_engine_instance", engine)
    sc.send_websocket_event("t", "go", {})
    assert notes[0][0] == "The overlay didn't change."
    assert notes[0][1]["details"] == "t_go: ConnectionError: socket"
    assert notes[0][1]["dedupe_key"] == "source_controls:emit_failed"


def test_spore_counter_payload_reset():
    assert sc._spore_counter_payload({"target_counter_id": "c"}, "reset", 9) == {
        "target_counter_id": "c", "operation": "reset"}

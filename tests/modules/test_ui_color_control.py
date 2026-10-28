# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/ui_color_control.py: colour parsing/formatting, swatch CSS, the picker event
bridge, open_color_picker and the swatch + text colour field."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402
from nicegui.testing.user_interaction import UserInteraction  # noqa: E402

from modules import path_utils  # noqa: E402
from modules import ui_color_control as cc  # noqa: E402
from ui_sim import run_ui  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_bridges(monkeypatch):
    import weakref

    monkeypatch.setattr(cc, "_bridges", weakref.WeakKeyDictionary())


# ----------------------------------------------------------------- parsing

@pytest.mark.parametrize(
    "value, expected",
    [
        (None, (255, 255, 255, 0.0, "transparent", "transparent")),
        ("", (255, 255, 255, 0.0, "transparent", "transparent")),
        ("  TRANSPARENT ", (255, 255, 255, 0.0, "transparent", "transparent")),
        ("#fff", (255, 255, 255, 1.0, "#fff", "hex")),
        ("#0f08", (0, 255, 0, 0.533, "#0f08", "rgba")),
        ("#102030", (16, 32, 48, 1.0, "#102030", "hex")),
        ("#102030ff", (16, 32, 48, 1.0, "#102030ff", "hex")),
        ("#10203080", (16, 32, 48, 0.502, "#10203080", "rgba")),
        ("rgb(1, 2, 3)", (1, 2, 3, 1.0, "rgb(1, 2, 3)", "rgb")),
        ("rgb(100% 0% 50%)", (255, 0, 127, 1.0, "rgb(100% 0% 50%)", "rgb")),
        ("rgba(300, -0, 4, 0.5)", None),  # "-0" is not matched by the regex
        ("rgba(300, 0, 4, 0.5)", (255, 0, 4, 0.5, "rgba(300, 0, 4, 0.5)", "rgba")),
        ("rgba(1,2,3 / 50%)", (1, 2, 3, 0.5, "rgba(1,2,3 / 50%)", "rgba")),
        ("rgba(1,2,3,7)", (1, 2, 3, 1.0, "rgba(1,2,3,7)", "rgba")),
    ],
)
def test_parse_color_string(value, expected):
    s = cc.parse_color_string(value)
    if expected is None:
        assert (s.r, s.g, s.b, s.a) == (255, 255, 255, 1.0)
        assert s.prefer == "rgba"
        return
    assert (s.r, s.g, s.b, s.a, s.raw, s.prefer) == expected


@pytest.mark.parametrize(
    "value, prefer",
    [("#ggg", "hex"), ("#12345", "hex"), ("rgb(1.2.3, 0, 0)", "rgb"), ("rgba(1.2.3, 0, 0, 1)", "rgba"),
     ("red", "auto"), ("linear-gradient(red, blue)", "auto"), ("inherit", "auto")],
)
def test_parse_unparseable_keeps_raw(value, prefer):
    s = cc.parse_color_string(value)
    assert (s.r, s.g, s.b, s.a) == (255, 255, 255, 1.0)
    assert s.raw == value and s.prefer == prefer


@pytest.mark.parametrize(
    "value, state, prefer",
    [
        ("", cc.ColorState(), "hex"),
        (None, cc.ColorState(), "hex"),
        ("Transparent", cc.ColorState(), "transparent"),
        ("#abc", cc.ColorState(), "hex"),
        ("RGBA(x)", cc.ColorState(), "rgba"),
        ("rgb(x)", cc.ColorState(), "rgb"),
        ("blue", cc.ColorState(a=0), "transparent"),
        ("blue", cc.ColorState(a=0.5), "auto"),
    ],
)
def test_detect_prefer(value, state, prefer):
    assert cc._detect_prefer(value, state) == prefer


def test_clamps_and_tokens():
    assert cc._clamp_channel(-5) == 0 and cc._clamp_channel(256.4) == 255 and cc._clamp_channel(12.6) == 13
    assert cc._clamp_alpha(-1) == 0.0 and cc._clamp_alpha(2) == 1.0 and cc._clamp_alpha("0.25") == 0.25
    assert cc._channel("50%") == pytest.approx(127.5) and cc._channel("7") == 7.0
    assert cc._alpha("25%") == 0.25 and cc._alpha("0.3") == 0.3


@pytest.mark.parametrize(
    "state, prefer, out",
    [
        (cc.ColorState(1, 2, 3, 0.0, prefer="rgba"), None, "rgba(1, 2, 3, 0)"),
        (cc.ColorState(1, 2, 3, 0.0, prefer="hex"), None, "transparent"),
        (cc.ColorState(1, 2, 3, -1.0), "rgba", "rgba(1, 2, 3, 0)"),
        (cc.ColorState(255, 0, 16, 1.0, prefer="hex"), None, "#ff0010"),
        (cc.ColorState(255, 0, 16, 1.0, prefer="rgb"), None, "rgb(255, 0, 16)"),
        (cc.ColorState(255, 0, 16, 1.0, prefer="auto"), None, "rgb(255, 0, 16)"),
        (cc.ColorState(255, 0, 16, 1.0, prefer=""), None, "rgb(255, 0, 16)"),
        (cc.ColorState(255, 0, 16, 1.0, prefer="rgb"), "hex", "#ff0010"),
        (cc.ColorState(1, 2, 3, 0.12345, prefer="hex"), None, "rgba(1, 2, 3, 0.123)"),
    ],
)
def test_format_color_string(state, prefer, out):
    assert cc.format_color_string(state, prefer) == out


def test_parse_format_round_trip():
    for text in ("#a1b2c3", "rgb(1, 2, 3)", "rgba(1, 2, 3, 0.5)", "transparent"):
        assert cc.format_color_string(cc.parse_color_string(text)) == text


@pytest.mark.parametrize(
    "value, result",
    [("linear-gradient(red, blue)", True), (" repeating-radial-gradient(red, blue)", True),
     ("CONIC-GRADIENT(red, blue)", True), ("red", False), (None, False), ("url(linear-gradient()", False)],
)
def test_is_gradient(value, result):
    assert cc.is_gradient(value) is result


@pytest.mark.parametrize(
    "raw, result",
    [("transparent", True), ("#abc", True), ("#abcd", True), ("#abcdef", True), ("#abcdef12", True),
     ("#abcde", False), ("rgb(1,2,3)", True), ("red", False)],
)
def test_is_solid_syntax(raw, result):
    assert cc._is_solid_syntax(raw) is result


@pytest.mark.parametrize(
    "value, result",
    [
        ("transparent", True),
        ("#ffffff", False),
        ("#ffffff80", True),
        ("rgba(0,0,0,0.5)", True),
        ("rgb(0,0,0)", False),
        ("", True),
        ("linear-gradient(red, transparent)", True),
        ("linear-gradient(#fff, rgba(0,0,0,0.2))", True),
        ("linear-gradient(#fff, #000)", False),
        ("linear-gradient(hsla(0, 10%, 10%, 0.5), red)", True),
        ("linear-gradient(hsl(0 10% 10% / 50%), red)", True),
        ("linear-gradient(hsl(0, 10%, 10%), #000)", False),
        ("linear-gradient(hsla(0, 10%, 10%, x), #000)", False),
    ],
)
def test_is_color_transparent(value, result):
    assert cc.is_color_transparent(value) is result


@pytest.mark.xfail(strict=True, reason="BUG: plain hsla() with alpha < 1 is reported opaque (only gradient stops check hsl alpha)")
def test_is_color_transparent_plain_hsla():
    assert cc.is_color_transparent("hsla(0, 10%, 10%, 0.5)") is True


@pytest.mark.parametrize(
    "value, bg",
    [
        ("", "transparent"),
        (None, "transparent"),
        ("transparent", "transparent"),
        ("#ff000000", "transparent"),
        ("#ff0000", "rgba(255, 0, 0, 1.0)"),
        ("rgba(1,2,3,0.25)", "rgba(1, 2, 3, 0.25)"),
        ("red", "red"),
        ("hsl(10, 20%, 30%)", "hsl(10, 20%, 30%)"),
        ("linear-gradient(red, blue)", "linear-gradient(red, blue)"),
        ("red; background: url(x)", "transparent"),
        ('url("evil")', "transparent"),
        ("expression(alert(1))", "transparent"),
        ("not a colour", "transparent"),
    ],
)
def test_swatch_background(value, bg):
    assert cc.swatch_background(value) == bg


def test_swatch_fill_style():
    assert cc.swatch_fill_style(cc.ColorState(a=0)) == "background-color: transparent;"
    assert cc.swatch_fill_style(cc.ColorState(1, 2, 3, 0.5)) == "background-color: rgba(1, 2, 3, 0.5);"


def test_css_constants():
    assert cc.COLOR_CONTROL_CSS.count("{") == cc.COLOR_CONTROL_CSS.count("}")
    assert cc.CHECKERBOARD_BG.count("linear-gradient(") == 4
    assert cc.PICKER_JS_ASSET.endswith(".js") and cc.PICKER_CSS_ASSET.endswith(".css")


# ----------------------------------------------------------------- bridge

def test_bridge_dispatch_routes_events():
    seen = []
    bridge = cc._PickerBridge()
    bridge.sessions["t"] = {"input": lambda v: seen.append(("input", v)), "done": lambda v: seen.append(("done", v)),
                            "cancel": lambda: seen.append(("cancel",))}
    bridge.dispatch(SimpleNamespace(args={"token": "t", "kind": "input", "value": 5}))
    bridge.dispatch(SimpleNamespace(args=[{"token": "t", "kind": "input", "value": None}]))
    assert seen == [("input", "5"), ("input", "")]
    bridge.dispatch(SimpleNamespace(args={"token": "t", "kind": "done", "value": "#fff"}))
    assert seen[-1] == ("done", "#fff") and "t" not in bridge.sessions
    bridge.dispatch(SimpleNamespace(args={"token": "t", "kind": "done", "value": "#000"}))  # session gone
    assert len(seen) == 3


def test_bridge_cancel_and_bad_payloads(caplog):
    seen = []
    bridge = cc._PickerBridge()
    bridge.sessions["t"] = {"cancel": lambda: seen.append("cancel"), "input": None}
    for args in (None, [], ["x"], "str", {"token": "other"}):
        bridge.dispatch(SimpleNamespace(args=args))
    bridge.dispatch(SimpleNamespace())
    bridge.dispatch(SimpleNamespace(args={"token": "t", "kind": "input"}))  # no callback
    bridge.dispatch(SimpleNamespace(args={"token": "t", "kind": "cancel"}))
    assert seen == ["cancel"] and bridge.sessions == {}

    def boom(v):
        raise RuntimeError("handler")

    bridge.sessions["u"] = {"done": boom}
    bridge.dispatch(SimpleNamespace(args={"token": "u", "kind": "done", "value": "x"}))
    assert "Colour picker done handler failed" in caplog.text


def test_read_asset(monkeypatch, tmp_path, caplog):
    f = tmp_path / "a.js"
    f.write_text("JS!", encoding="utf-8")
    monkeypatch.setattr(path_utils, "get_assets_path", lambda rel: str(tmp_path / rel))
    assert cc._read_asset("a.js") == "JS!"
    assert cc._read_asset("missing.js") == ""
    assert "Could not load missing.js" in caplog.text


def test_real_picker_assets_exist():
    assert "MycelianColorPicker" in cc._read_asset(cc.PICKER_JS_ASSET)
    assert cc._read_asset(cc.PICKER_CSS_ASSET).strip()


class _Layout:
    def __init__(self):
        self.listeners = []

    def on(self, event, handler):
        self.listeners.append((event, handler))


class _Client:
    def __init__(self):
        self.layout = _Layout()
        self.js = []

    def run_javascript(self, code):
        self.js.append(code)


def test_bridge_for_client_injects_once(monkeypatch):
    monkeypatch.setattr(cc, "_read_asset", lambda rel: f"/*{rel}*/")
    client = _Client()
    b1 = cc._bridge_for_client(client)
    b2 = cc._bridge_for_client(client)
    assert b1 is b2 and isinstance(b1, cc._PickerBridge)
    assert client.layout.listeners == [(cc._PICKER_EVENT, b1.dispatch)]
    assert len(client.js) == 1
    js = client.js[0]
    assert "mycelian-color-picker-css" in js
    assert js.endswith(f"/*{cc.PICKER_JS_ASSET}*/")
    assert json.dumps(f"/*{cc.PICKER_CSS_ASSET}*/\n{cc.COLOR_CONTROL_CSS}") in js


def test_bridge_for_client_uses_context(monkeypatch):
    client = _Client()
    monkeypatch.setattr(cc, "_read_asset", lambda rel: "")
    monkeypatch.setattr(cc, "context", SimpleNamespace(client=client))
    assert cc._bridge_for_client() is cc._bridges[client]


def test_bridge_for_client_without_context(monkeypatch):
    class _Ctx:
        @property
        def client(self):
            raise RuntimeError("no slot")

    monkeypatch.setattr(cc, "context", _Ctx())
    assert cc._bridge_for_client() is None
    cc.ensure_color_control_assets()  # no error


def test_ensure_assets_swallows_errors(monkeypatch):
    def boom(client=None):
        raise RuntimeError("x")

    monkeypatch.setattr(cc, "_bridge_for_client", boom)
    cc.ensure_color_control_assets()


# ----------------------------------------------------------------- open_color_picker

def _cfg(js: str) -> dict:
    m = re.search(r"var cfg=(\{.*?\}), n=0;", js, re.S)
    return json.loads(m.group(1))


def test_open_color_picker_config(monkeypatch):
    monkeypatch.setattr(cc, "_read_asset", lambda rel: "")
    client = _Client()
    anchor = SimpleNamespace(client=client, id=42)
    done = []
    cc.open_color_picker(anchor, "#fff", on_done=done.append, presets=["#000", None, " ", 5],
                         allow_alpha=False, allow_gradient=True, preferred_format="hex", title="T",
                         fill_selector=".f")
    cfg = _cfg(client.js[-1])
    assert cfg["anchor"] == "c42" and cfg["event"] == cc._PICKER_EVENT
    assert cfg["value"] == "#fff" and cfg["presets"] == ["#000", "5"]
    assert cfg["allowAlpha"] is False and cfg["allowGradient"] is True
    assert cfg["preferredFormat"] == "hex" and cfg["title"] == "T"
    assert cfg["note"] == "Enter or Done applies the colour"
    assert cfg["live"] is False and cfg["fill"] == ".f"
    bridge = cc._bridges[client]
    assert bridge.sessions[cfg["token"]]["done"] == done.append
    bridge.dispatch(SimpleNamespace(args={"token": cfg["token"], "kind": "done", "value": "#123"}))
    assert done == ["#123"]


def test_open_color_picker_live_and_none_value(monkeypatch):
    monkeypatch.setattr(cc, "_read_asset", lambda rel: "")
    client = _Client()
    anchor = SimpleNamespace(client=client, id=1)
    cc.open_color_picker(anchor, None, on_input=lambda v: None)
    cfg = _cfg(client.js[-1])
    assert cfg["value"] == "" and cfg["live"] is True and cfg["note"] is None and cfg["presets"] == []


def test_open_color_picker_clears_old_sessions(monkeypatch):
    monkeypatch.setattr(cc, "_read_asset", lambda rel: "")
    client = _Client()
    anchor = SimpleNamespace(client=client, id=1)
    bridge = cc._bridge_for_client(client)
    bridge.sessions.update({str(i): {} for i in range(33)})
    cc.open_color_picker(anchor, "#000", note="custom")
    assert len(bridge.sessions) == 1
    assert _cfg(client.js[-1])["note"] == "custom"


def test_open_color_picker_without_client(monkeypatch):
    class _Ctx:
        @property
        def client(self):
            raise RuntimeError("no slot")

    monkeypatch.setattr(cc, "context", _Ctx())
    cc.open_color_picker(SimpleNamespace(id=1), "#000")  # anchor without client: nothing happens


# ----------------------------------------------------------------- render_color_field

@pytest.fixture
def picker(monkeypatch):
    """Capture open_color_picker calls from the field."""
    calls = []
    monkeypatch.setattr(cc, "open_color_picker", lambda anchor, value, **kw: calls.append((anchor, value, kw)))
    return calls


def _field(**kwargs):
    made = {}

    def build():
        made["text"] = cc.render_color_field(**kwargs)

    return made, build


def _fill_bg(text_input):
    swatch = next(e for e in text_input.client.elements.values() if "mycelian-color-swatch" in e.classes)
    fill = next(e for e in swatch.descendants() if "mycelian-color-swatch__fill" in e.classes)
    return swatch, fill


def test_field_renders_and_opens_picker(picker):
    changes, live = [], []
    made, build = _field(element_id="bg", value="#ff0000", tooltip="Background", preset_options=["#000"],
                         on_change=changes.append, on_input=live.append)

    async def check(user):
        text = made["text"]
        swatch, fill = _fill_bg(text)
        assert swatch.props.get("data-color-field") == "bg"
        assert swatch.props.get("tabindex") == "0" or swatch.props.get("tabindex") == 0
        assert fill.style["background"] == "rgba(255, 0, 0, 1.0)"
        assert text.value == "#ff0000"
        assert "gradient" not in text.props.get("placeholder")

        UserInteraction(user, {swatch}, None).trigger("click")
        anchor, start, kw = picker[-1]
        assert anchor is swatch and start == "#ff0000"
        assert kw["presets"] == ["#000"] and kw["allow_gradient"] is False
        assert kw["fill_selector"] == ".mycelian-color-swatch__fill"

        kw["on_input"]("#00ff00")
        assert text.value == "#00ff00" and live == ["#00ff00"]
        assert fill.style["background"] == "rgba(0, 255, 0, 1.0)"
        kw["on_done"]("#0000ff")
        assert changes == ["#0000ff"] and text.value == "#0000ff"

        # keyboard opens too; cancel after live input restores and re-reports the start value
        UserInteraction(user, {swatch}, None).trigger("keydown.enter")
        kw = picker[-1][2]
        kw["on_input"]("#111111")
        kw["on_cancel"]()
        assert text.value == "#0000ff" and live[-1] == "#0000ff"
        assert changes == ["#0000ff"]

        # cancel without touching: no live callback
        UserInteraction(user, {swatch}, None).trigger("click")
        n = len(live)
        picker[-1][2]["on_cancel"]()
        assert len(live) == n

    run_ui(build, check)


def test_field_done_with_unchanged_value(picker):
    changes, live = [], []
    made, build = _field(element_id="c", value="#abcdef", tooltip="t", on_change=changes.append, on_input=live.append)

    async def check(user):
        text = made["text"]
        swatch, _ = _fill_bg(text)
        # touched then done with the original value -> live gets the start value back, no change
        UserInteraction(user, {swatch}, None).trigger("click")
        kw = picker[-1][2]
        kw["on_input"]("#000000")
        kw["on_done"]("#abcdef")
        assert changes == [] and live == ["#000000", "#abcdef"]
        # untouched done: nothing
        UserInteraction(user, {swatch}, None).trigger("click")
        picker[-1][2]["on_done"]("#abcdef")
        assert changes == [] and len(live) == 2
        # text edited but not committed; picker done with that start value commits it
        text.value = "#123456"
        UserInteraction(user, {swatch}, None).trigger("click")
        assert picker[-1][1] == "#123456"
        picker[-1][2]["on_done"]("#123456")
        assert changes == ["#123456"]

    run_ui(build, check)


def test_field_without_live_and_gradient(picker):
    changes = []
    made, build = _field(element_id="g", value="linear-gradient(red, blue)", tooltip="t",
                         on_change=changes.append, allow_gradient=True)

    async def check(user):
        text = made["text"]
        swatch, fill = _fill_bg(text)
        assert fill.style["background"] == "linear-gradient(red, blue)"
        assert text.props.get("placeholder").endswith(", gradient")
        UserInteraction(user, {swatch}, None).trigger("click")
        kw = picker[-1][2]
        assert kw["on_input"] is None and kw["allow_gradient"] is True
        kw["on_cancel"]()
        assert text.value == "linear-gradient(red, blue)"

    run_ui(build, check)


def test_field_gradient_value_enables_gradient(picker):
    made, build = _field(element_id="g", value="radial-gradient(red, blue)", tooltip="t", on_change=lambda v: None)

    async def check(user):
        swatch, _ = _fill_bg(made["text"])
        UserInteraction(user, {swatch}, None).trigger("click")
        assert picker[-1][2]["allow_gradient"] is True

    run_ui(build, check)


def test_field_text_typing_and_commit(picker):
    changes = []
    made, build = _field(element_id="t", value=None, tooltip="tip", on_change=changes.append)

    async def check(user):
        text = made["text"]
        _, fill = _fill_bg(text)
        assert text.value == "" and fill.style["background"] == "transparent"
        text.value = "  #00ff00  "  # on_change -> live swatch update
        assert fill.style["background"] == "rgba(0, 255, 0, 1.0)"  # live swatch while typing
        assert changes == []  # not committed until blur / Enter
        UserInteraction(user, {text}, None).trigger("blur")
        assert text.value == "#00ff00" and changes == ["#00ff00"]
        assert fill.style["background"] == "rgba(0, 255, 0, 1.0)"
        UserInteraction(user, {text}, None).trigger("keydown.enter")  # same value: no new change
        assert changes == ["#00ff00"]
        text.value = None
        UserInteraction(user, {text}, None).trigger("blur")
        assert changes == ["#00ff00", ""]

    run_ui(build, check)


def test_field_text_input_handler_reads_element_value(picker, monkeypatch):
    """on_text_input falls back to the element's value when the event has none."""
    captured = {}
    real_form_input = cc.form_input

    def spy(**kw):
        captured["on_change"] = kw["on_change"]
        return real_form_input(**kw)

    monkeypatch.setattr(cc, "form_input", spy)
    made, build = _field(element_id="x", value="#000", tooltip="t", on_change=lambda v: None)

    async def check(user):
        text = made["text"]
        _, fill = _fill_bg(text)
        text.set_value("#ffffff")
        captured["on_change"](SimpleNamespace())  # no .value attribute
        assert fill.style["background"] == "rgba(255, 255, 255, 1.0)"
        captured["on_change"](SimpleNamespace(value=None))
        assert fill.style["background"] == "rgba(255, 255, 255, 1.0)"

    run_ui(build, check)


def test_field_injects_picker_assets(monkeypatch):
    monkeypatch.setattr(cc, "_read_asset", lambda rel: "")
    made, build = _field(element_id="a", value="#000", tooltip="t", on_change=lambda v: None)

    async def check(user):
        assert user.client in cc._bridges

    run_ui(build, check)


def test_open_color_picker_real_element(monkeypatch):
    monkeypatch.setattr(cc, "_read_asset", lambda rel: "")
    js = []

    def build():
        el = ui.element("div")
        monkeypatch.setattr(el.client, "run_javascript", lambda code, *a, **k: js.append(code))
        cc.open_color_picker(el, "#123")

    run_ui(build)
    assert any("MycelianColorPicker" in c for c in js)

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/obs_service.py, part 2: snapshot, browser-source operations, dispatcher.

All OBS requests go to an in-memory fake ReqClient.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import concurrent.futures  # noqa: E402
import time as _time  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any, Dict, List, Optional  # noqa: E402

import pytest  # noqa: E402

from modules import obs_browser_docks  # noqa: E402
from modules import obs_service as obs  # noqa: E402


@pytest.fixture(autouse=True)
def _no_dock_migrate(monkeypatch):
    monkeypatch.setattr(obs_browser_docks, "schedule_dock_boot_migrate_if_obs_exits", lambda: None)


@pytest.fixture
def sleeps(monkeypatch):
    """Replace obs_service's ``time`` module view so sleeps are recorded, not taken."""
    out: List[float] = []
    fake_time = SimpleNamespace(sleep=lambda s: out.append(s), monotonic=_time.monotonic, time=_time.time)
    monkeypatch.setattr(obs, "time", fake_time)
    return out


def done_future(value=None, exc=None):
    fut: concurrent.futures.Future = concurrent.futures.Future()
    if exc is not None:
        fut.set_exception(exc)
    else:
        fut.set_result(value)
    return fut


class FakeOBS:
    """In-memory ReqClient. Any configured value that is an exception is raised."""

    def __init__(
        self,
        *,
        inputs: Any = None,
        browser_inputs: Any = None,
        settings: Optional[Dict[str, Any]] = None,
        scenes: Any = None,
        items: Optional[Dict[str, Any]] = None,
        program: Any = None,
        defaults: Any = None,
    ) -> None:
        self.inputs = inputs if inputs is not None else []
        self.browser_inputs = browser_inputs
        self.settings = settings or {}
        self.scenes = scenes if scenes is not None else []
        self.items = items or {}
        self.program = program
        self.defaults = defaults
        self.calls: List[tuple] = []

    @staticmethod
    def _maybe_raise(v):
        if isinstance(v, BaseException):
            raise v
        return v

    def get_input_list(self, kind=None):
        self.calls.append(("get_input_list", kind))
        if kind is not None:
            v = self._maybe_raise(self.browser_inputs)
            return {"inputs": v or []}
        return {"inputs": self._maybe_raise(self.inputs)}

    def get_input_settings(self, name):
        self.calls.append(("get_input_settings", name))
        v = self._maybe_raise(self.settings.get(name))
        return {"inputSettings": v}

    def get_input_default_settings(self, kind):
        self.calls.append(("get_input_default_settings", kind))
        return {"defaultInputSettings": self._maybe_raise(self.defaults)}

    def get_scene_list(self):
        self.calls.append(("get_scene_list",))
        v = self._maybe_raise(self.scenes)
        return {"scenes": [{"sceneName": s} for s in v]}

    def get_scene_item_list(self, scene):
        self.calls.append(("get_scene_item_list", scene))
        v = self._maybe_raise(self.items.get(scene, []))
        if v == "notalist":
            return {"sceneItems": "notalist"}
        return {"sceneItems": [{"sourceName": s, "sceneItemId": i} for i, s in enumerate(v)]}

    def get_current_program_scene(self):
        self.calls.append(("get_current_program_scene",))
        return self._maybe_raise(self.program)

    def get_stream_status(self):
        return {"outputActive": True, "outputState": "STARTED"}

    def get_record_status(self):
        return {"outputActive": False, "outputState": "STOPPED"}


def svc_with(client):
    svc = obs.ObsServiceImpl()
    svc._req_client = client
    return svc


def connected_svc(fut):
    svc = obs.ObsServiceImpl()
    svc._req_client = object()
    svc._set_phase("connected")
    seen: List[tuple] = []

    def enqueue(op, args=None):
        seen.append((op, args))
        return fut

    svc.enqueue_obs_request = enqueue
    svc.seen = seen
    return svc


# --------------------------------------------------------------------------- output flags + snapshot


def test_update_output_flags_only():
    svc = obs.ObsServiceImpl()
    svc._update_output_flags_only()  # no client: no-op
    assert svc._snapshot["stream_output_active"] is False
    timeouts = []
    svc._req_client = FakeOBS()
    svc._apply_ws_timeout = lambda cl, t: timeouts.append(t)
    svc._update_output_flags_only()
    svc._update_output_flags_only(use_health_timeout=True)
    assert timeouts == [obs._RPC_TIMEOUT_SEC, obs._HEALTH_TIMEOUT_SEC]
    assert svc._snapshot["stream_output_active"] is True
    assert svc._snapshot["stream_output_state"] == "STARTED"
    assert svc._snapshot["record_output_active"] is False
    assert svc._snapshot["record_output_state"] == "STOPPED"


def test_scene_names_from_resp_variants():
    svc = obs.ObsServiceImpl()
    assert svc._scene_names_from_resp({"scenes": [{"sceneName": "A"}, {"sceneName": ""}, {"scene_name": "B"}]}) == ["A", "B"]
    assert svc._scene_names_from_resp({}) == []
    assert svc._scene_names_from_resp({"scenes": "junk"}) == []
    assert svc._scene_names_from_resp(SimpleNamespace(scenes=[SimpleNamespace(scene_name="C")])) == ["C"]
    # falsy attribute: falls back to __dict__ lookup
    assert svc._scene_names_from_resp(SimpleNamespace(scenes=None)) == []


def test_scene_names_from_resp_object_without_scenes_attribute():
    assert obs.ObsServiceImpl()._scene_names_from_resp(SimpleNamespace()) == []


def test_sources_for_scene():
    svc = obs.ObsServiceImpl()
    assert svc._sources_for_scene("A") == {}
    svc._req_client = FakeOBS(items={"A": ["cam", "", "cam", "mic"], "B": "notalist", "C": RuntimeError("x")})
    assert svc._sources_for_scene("A") == {"cam": "cam", "cam (#2)": "cam", "mic": "mic"}
    assert svc._sources_for_scene("B") == {}
    assert svc._sources_for_scene("C") == {}


def test_refresh_snapshot_locked(sleeps):
    client = FakeOBS(
        scenes=["b", "A", "c", "d", "A"],
        inputs=[{"inputName": "Mic"}, {"inputName": ""}, {"inputName": "aux"}, {"inputName": "Mic"}],
        items={"A": ["cam"], "b": [], "c": ["x"]},
        program={"currentProgramSceneName": "A"},
    )
    svc = svc_with(client)
    svc._apply_ws_timeout = lambda cl, t: None
    svc._refresh_snapshot_locked()
    snap = svc.get_connector_snapshot()
    assert snap["scene_names"] == ["A", "b", "c", "d"]
    assert snap["input_names"] == ["aux", "Mic"]
    assert snap["sources_by_scene"] == {"A": {"cam": "cam"}, "c": {"x": "x"}}
    assert snap["current_program_scene_name"] == "A"
    assert snap["stream_output_active"] is True
    assert sleeps == [0]  # yields once every 3 scenes (5 scenes -> once)


def test_refresh_snapshot_locked_no_client_and_inputs_not_list(sleeps):
    svc = obs.ObsServiceImpl()
    svc._refresh_snapshot_locked()  # no client
    client = FakeOBS(scenes=[], inputs="junk")
    svc = svc_with(client)

    def flags_boom(**kw):
        raise RuntimeError("flaky")

    svc._update_output_flags_only = flags_boom
    svc._refresh_snapshot_locked()
    assert svc._snapshot["input_names"] == []


def test_refresh_snapshot_locked_aborts_mid_scan(sleeps):
    client = FakeOBS(scenes=["A", "B"], inputs=[])
    svc = svc_with(client)
    svc._snapshot["scene_names"] = ["old"]
    svc._reconnect_wakeup.set()
    svc._refresh_snapshot_locked()
    assert svc._snapshot["scene_names"] == ["old"]


# --------------------------------------------------------------------------- blocking wrappers


def test_lookup_browser_source_size_guards():
    svc = obs.ObsServiceImpl()
    assert svc.lookup_browser_source_size("alerts") is None  # not connected
    csvc = connected_svc(done_future((True, None, {"width": 1})))
    assert csvc.lookup_browser_source_size("  ") is None
    assert csvc.lookup_browser_source_size(None) is None
    assert csvc.seen == []


@pytest.mark.parametrize(
    "fut,expected",
    [
        (done_future((True, None, {"width": 1920, "height": 1080})), {"width": 1920, "height": 1080}),
        (done_future((True, None, "junk")), None),
        (done_future((True, None)), None),
        (done_future((False, "x", None)), None),
        (done_future(()), None),
        (done_future("junk"), None),
        (done_future(exc=TimeoutError()), None),
    ],
)
def test_lookup_browser_source_size_results(fut, expected):
    svc = connected_svc(fut)
    assert svc.lookup_browser_source_size(" alerts ", 5000, timeout_s=0.01) == expected
    assert svc.seen == [("__browser_source_size__", {"route": "alerts", "port": 5000})]


def test_create_browser_source_blocking_not_connected():
    assert obs.ObsServiceImpl().create_browser_source_blocking(
        scene_name="S", input_name="I", url="u", width=1, height=1
    ) == (False, "OBS is not connected")


@pytest.mark.parametrize(
    "fut,expected",
    [
        (done_future((True, None, {"input_name": "I 2", "scene_name": "Main"})), (True, 'Added "I 2" to "Main".')),
        (done_future((True, None, None)), (True, 'Added "I" to "S".')),
        (done_future((True,)), (True, 'Added "I" to "S".')),
        (done_future((False, "boom", None)), (False, "boom")),
        (done_future((False, "", None)), (False, "Could not add the browser source")),
        (done_future("junk"), (False, "Could not add the browser source")),
        (done_future(exc=TimeoutError("slow")), (False, "slow")),
        (done_future(exc=TimeoutError()), (False, "Could not add the browser source")),
    ],
)
def test_create_browser_source_blocking_results(fut, expected):
    svc = connected_svc(fut)
    assert svc.create_browser_source_blocking(
        scene_name="S", input_name="I", url="u", width=10, height=20, fps_custom=True, fps=30, timeout_s=0.01
    ) == expected
    op, args = svc.seen[0]
    assert op == "__create_browser_source__"
    assert args == {
        "scene_name": "S", "input_name": "I", "url": "u", "width": 10, "height": 20, "reroute_audio": False,
        "fps_custom": True, "fps": 30, "shutdown": True, "restart_when_active": False,
    }


def test_list_scene_browser_urls_blocking():
    empty = {"taken_names": [], "urls_by_scene": {}}
    assert obs.ObsServiceImpl().list_scene_browser_urls_blocking() == (False, empty)
    payload = {"taken_names": ["a"], "urls_by_scene": {"S": ["u"]}}
    assert connected_svc(done_future((True, None, payload))).list_scene_browser_urls_blocking(0.01) == (True, payload)
    assert connected_svc(done_future((False, "x", payload))).list_scene_browser_urls_blocking(0.01) == (False, empty)
    assert connected_svc(done_future((True, None, "junk"))).list_scene_browser_urls_blocking(0.01) == (False, empty)
    assert connected_svc(done_future((True,))).list_scene_browser_urls_blocking(0.01) == (False, empty)
    assert connected_svc(done_future(exc=TimeoutError())).list_scene_browser_urls_blocking(0.01) == (False, empty)


def test_enqueue_refresh_mycelian_browser_sources():
    svc = obs.ObsServiceImpl()
    assert svc.enqueue_refresh_mycelian_browser_sources(5000, None).result(0) == (
        False, "No template routes", {"refreshed": [], "skipped": []}
    )
    assert svc.enqueue_refresh_mycelian_browser_sources(5000, ["  ", ""]).result(0)[1] == "No template routes"
    assert svc.enqueue_refresh_mycelian_browser_sources(5000, ["alerts"]).result(0) == (False, "OBS is not connected", None)
    marker = done_future("queued")
    csvc = connected_svc(marker)
    assert csvc.enqueue_refresh_mycelian_browser_sources(5000, [" alerts ", "", "chat"]) is marker
    assert csvc.seen == [("__refresh_mycelian_browser_sources__", {"port": 5000, "template_routes": ["alerts", "chat"]})]


# --------------------------------------------------------------------------- press refresh + create input


def test_press_browser_refresh_positional():
    got = []
    cl = SimpleNamespace(press_input_properties_button=lambda name, prop: got.append((name, prop)))
    obs.ObsServiceImpl._press_browser_refresh_nocache(cl, 5)
    assert got == [("5", "refreshnocache")]


def test_press_browser_refresh_keyword_fallback():
    got = []

    def press(*args, **kw):
        if args:
            raise TypeError("keywords only")
        got.append(kw)

    obs.ObsServiceImpl._press_browser_refresh_nocache(SimpleNamespace(press_input_properties_button=press), "src")
    assert got == [{"input_name": "src", "prop_name": "refreshnocache"}]


def test_press_browser_refresh_send_fallback():
    sent = []

    def press(*a, **k):
        raise TypeError("nope")

    cl = SimpleNamespace(press_input_properties_button=press, send=lambda req, data: sent.append((req, data)))
    obs.ObsServiceImpl._press_browser_refresh_nocache(cl, "src")
    assert sent == [("PressInputPropertiesButton", {"inputName": "src", "propertyName": "refreshnocache"})]
    sent.clear()
    obs.ObsServiceImpl._press_browser_refresh_nocache(SimpleNamespace(send=lambda r, d: sent.append(r)), "s")
    assert sent == ["PressInputPropertiesButton"]


def test_press_browser_refresh_impossible():
    with pytest.raises(RuntimeError, match="PressInputPropertiesButton"):
        obs.ObsServiceImpl._press_browser_refresh_nocache(SimpleNamespace(), "s")


def test_call_create_input_variants():
    got = []
    obs.ObsServiceImpl._call_create_input(SimpleNamespace(create_input=lambda *a: got.append(a)), "S", "I", {"u": 1})
    assert got == [("S", "I", "browser_source", {"u": 1}, True)]

    def kw_only(*args, **kw):
        if args:
            raise TypeError("kw")
        got.append(kw)

    obs.ObsServiceImpl._call_create_input(SimpleNamespace(create_input=kw_only), "S", "I", {})
    assert got[-1] == {"sceneName": "S", "inputName": "I", "inputKind": "browser_source", "inputSettings": {},
                       "sceneItemEnabled": True}

    def never(*a, **k):
        raise TypeError("x")

    sent = []
    obs.ObsServiceImpl._call_create_input(SimpleNamespace(create_input=never, send=lambda r, d: sent.append((r, d))),
                                          "S", "I", {"a": 1})
    assert sent == [("CreateInput", {"sceneName": "S", "inputName": "I", "inputKind": "browser_source",
                                     "inputSettings": {"a": 1}, "sceneItemEnabled": True})]
    with pytest.raises(RuntimeError, match="CreateInput"):
        obs.ObsServiceImpl._call_create_input(SimpleNamespace(), "S", "I", {})


# --------------------------------------------------------------------------- input listing helpers


def test_list_browser_source_input_rows_locked():
    assert obs.ObsServiceImpl()._list_browser_source_input_rows_locked() == []
    rows = [{"inputName": "B1", "inputKind": "browser_source"}]
    assert svc_with(FakeOBS(browser_inputs=rows))._list_browser_source_input_rows_locked() == rows
    mixed = [
        {"inputName": "B1", "inputKind": "browser_source_v2"},
        {"inputName": "Mic", "inputKind": "coreaudio_input_capture"},
        {"inputName": "B2", "unversionedInputKind": "browser_source"},
    ]
    client = FakeOBS(browser_inputs=RuntimeError("kind filter unsupported"), inputs=mixed)
    assert [r["inputName"] for r in svc_with(client)._list_browser_source_input_rows_locked()] == ["B1", "B2"]
    assert svc_with(FakeOBS(browser_inputs=[], inputs="junk"))._list_browser_source_input_rows_locked() == []
    assert svc_with(FakeOBS(browser_inputs=[], inputs=RuntimeError("x")))._list_browser_source_input_rows_locked() == []


def test_current_program_scene_name_locked():
    assert obs.ObsServiceImpl()._current_program_scene_name_locked() == ""
    assert svc_with(SimpleNamespace())._current_program_scene_name_locked() == ""
    assert svc_with(FakeOBS(program=RuntimeError("no scene")))._current_program_scene_name_locked() == ""
    assert svc_with(FakeOBS(program={"sceneName": "S"}))._current_program_scene_name_locked() == "S"
    assert svc_with(FakeOBS(program={"current_program_scene_name": "P"}))._current_program_scene_name_locked() == "P"
    assert svc_with(FakeOBS(program={}))._current_program_scene_name_locked() == ""


def test_all_input_names_locked():
    with pytest.raises(RuntimeError, match="not connected"):
        obs.ObsServiceImpl()._all_input_names_locked()
    client = FakeOBS(inputs=[{"inputName": "A"}, {"inputName": None}, {"input_name": "B"}])
    assert svc_with(client)._all_input_names_locked() == ["A", "B"]
    assert svc_with(FakeOBS(inputs="junk"))._all_input_names_locked() == []


# --------------------------------------------------------------------------- create browser source


class CreatingOBS(FakeOBS):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.created: List[tuple] = []

    def create_input(self, scene, name, kind, settings, enabled):
        self.created.append((scene, name, kind, settings, enabled))


def _valid_kw(**over):
    kw = {"scene_name": " Main ", "input_name": "Alerts", "url": " http://127.0.0.1:5000/alerts ",
          "width": "1920", "height": 1080}
    kw.update(over)
    return kw


@pytest.mark.parametrize(
    "over,message",
    [
        ({"scene_name": " "}, "scene_name is required"),
        ({"url": ""}, "url is required"),
        ({"width": "wide"}, "width and height must be positive"),
        ({"height": None}, "width and height must be positive"),
        ({"width": 0}, "width and height must be positive"),
        ({"fps_custom": True, "fps": 29.97}, "fps must be between 1 and 1000"),
        ({"fps_custom": True, "fps": "fast"}, "fps must be between 1 and 1000"),
        ({"fps_custom": True, "fps": 0}, "fps must be between 1 and 1000"),
        ({"fps_custom": True, "fps": 1001}, "fps must be between 1 and 1000"),
    ],
)
def test_create_browser_source_locked_validation(over, message):
    client = CreatingOBS()
    with pytest.raises(ValueError, match=message):
        svc_with(client)._create_browser_source_locked(_valid_kw(**over))
    assert client.created == []


def test_create_browser_source_locked_requires_client():
    with pytest.raises(RuntimeError, match="not connected"):
        obs.ObsServiceImpl()._create_browser_source_locked(_valid_kw())


def test_create_browser_source_locked_success_and_dedupes_name():
    client = CreatingOBS(inputs=[{"inputName": "Alerts"}], scenes=["Alerts 2"])
    out = svc_with(client)._create_browser_source_locked(_valid_kw(fps_custom=True, fps=None, reroute_audio=1,
                                                                    shutdown=False, restart_when_active=True))
    assert out == {"input_name": "Alerts 3", "scene_name": "Main"}
    scene, name, kind, settings, enabled = client.created[0]
    assert (scene, name, kind, enabled) == ("Main", "Alerts 3", "browser_source", True)
    assert settings == {"url": "http://127.0.0.1:5000/alerts", "width": 1920, "height": 1080, "css": "",
                        "reroute_audio": True, "fps_custom": True, "shutdown": False,
                        "restart_when_active": True, "fps": 60}


def test_create_browser_source_locked_fps_float_integer_and_scene_list_error():
    client = CreatingOBS(scenes=RuntimeError("scene list down"))
    out = svc_with(client)._create_browser_source_locked(_valid_kw(fps_custom=True, fps=30.0, input_name=""))
    assert out["input_name"] == "Mycelian"
    assert client.created[0][3]["fps"] == 30
    assert "fps" in client.created[0][3]


def test_create_browser_source_locked_without_scene_list_method():
    class NoSceneList(CreatingOBS):
        get_scene_list = None

    client = NoSceneList()
    out = svc_with(client)._create_browser_source_locked(_valid_kw())
    assert out["input_name"] == "Alerts"
    assert "fps" not in client.created[0][3]


# --------------------------------------------------------------------------- scene browser urls


def test_scene_browser_urls_locked():
    with pytest.raises(RuntimeError):
        obs.ObsServiceImpl()._scene_browser_urls_locked()
    client = FakeOBS(
        inputs=[{"inputName": "Alerts"}, {"inputName": "Chat"}, {"inputName": "Mic"}],
        browser_inputs=[
            {"inputName": "Alerts"},
            {"inputName": "Chat"},
            {"inputName": ""},
            {"inputName": "Broken"},
            {"inputName": "NoSettings"},
            {"inputName": "Blank"},
        ],
        settings={
            "Alerts": {"url": "http://127.0.0.1:5000/alerts"},
            "Chat": {"url": "http://127.0.0.1:5000/chat"},
            "Broken": RuntimeError("gone"),
            "NoSettings": None,
            "Blank": {"url": ""},
        },
        scenes=["Main", "BRB", "Bad", "Weird", "Alerts"],
        items={"Main": ["Alerts", "Mic", "Chat"], "BRB": ["Chat"], "Bad": RuntimeError("x"), "Weird": "notalist"},
    )
    out = svc_with(client)._scene_browser_urls_locked()
    assert out["taken_names"] == ["Alerts", "Chat", "Mic", "Main", "BRB", "Bad", "Weird"]
    assert out["urls_by_scene"] == {
        "Main": ["http://127.0.0.1:5000/alerts", "http://127.0.0.1:5000/chat"],
        "BRB": ["http://127.0.0.1:5000/chat"],
        "Bad": [],
        "Weird": [],
        "Alerts": [],
    }


def test_scene_browser_urls_locked_scene_list_error():
    client = FakeOBS(inputs=[{"inputName": "A"}], browser_inputs=[], scenes=RuntimeError("down"))
    assert svc_with(client)._scene_browser_urls_locked() == {"taken_names": ["A"], "urls_by_scene": {}}


# --------------------------------------------------------------------------- refresh Mycelian sources


def test_refresh_mycelian_sources_skips_without_client_routes_or_port(sleeps):
    empty = {"refreshed": [], "skipped": []}
    assert obs.ObsServiceImpl()._refresh_mycelian_browser_sources_locked(5000, ["a"]) == empty
    client = FakeOBS()
    svc = svc_with(client)
    assert svc._refresh_mycelian_browser_sources_locked(5000, [" ", ""]) == empty
    assert svc._refresh_mycelian_browser_sources_locked(None, ["a"]) == empty
    assert svc._refresh_mycelian_browser_sources_locked("", ["a"]) == empty
    assert svc._refresh_mycelian_browser_sources_locked("port", ["a"]) == empty
    assert client.calls == []
    # no browser inputs at all
    assert svc._refresh_mycelian_browser_sources_locked("5000", ["a"]) == empty


def test_refresh_mycelian_sources_full(sleeps):
    pressed = []

    class PressOBS(FakeOBS):
        def press_input_properties_button(self, name, prop):
            if name == "Fails":
                raise RuntimeError("cef busy")
            pressed.append((name, prop))

    client = PressOBS(
        browser_inputs=[
            {"inputName": "Alerts"},
            {"inputName": ""},
            {"inputName": "NoSettings"},
            {"inputName": "Broken"},
            {"inputName": "LocalOther"},
            {"inputName": "Remote"},
            {"inputName": "Chat"},
            {"inputName": "Fails"},
        ],
        settings={
            "Alerts": {"url": "http://127.0.0.1:5000/alerts"},
            "NoSettings": None,
            "Broken": RuntimeError("gone"),
            "LocalOther": {"url": "http://localhost:5000/unknown"},
            "Remote": {"url": "https://streamelements.com/overlay/x"},
            "Chat": {"url": "127.0.0.1:5000/chat.html"},
            "Fails": {"url": "http://localhost:5000/alerts"},
        },
    )
    out = svc_with(client)._refresh_mycelian_browser_sources_locked(5000, ["alerts", "chat"])
    assert out["refreshed"] == ["Alerts", "Chat"]
    assert pressed == [("Alerts", "refreshnocache"), ("Chat", "refreshnocache")]
    assert out["skipped"] == [
        {"source_name": "NoSettings", "reason": "not_mycelian"},
        {"source_name": "Broken", "reason": "settings"},
        {"source_name": "LocalOther", "reason": "not_mycelian"},
        {"source_name": "Remote", "reason": "not_mycelian"},
        {"source_name": "Fails", "reason": "refresh_failed"},
    ]
    assert sleeps == [obs._BROWSER_REFRESH_STAGGER_SEC, obs._BROWSER_REFRESH_STAGGER_SEC]


# --------------------------------------------------------------------------- browser source size


def test_lookup_size_locked_guards():
    assert obs.ObsServiceImpl()._lookup_browser_source_size_locked("alerts", 5000) is None
    client = FakeOBS()
    assert svc_with(client)._lookup_browser_source_size_locked("  ", 5000) is None
    assert client.calls == []


def test_lookup_size_locked_no_inputs():
    client = FakeOBS(defaults=RuntimeError("x"), browser_inputs=RuntimeError("y"), inputs=RuntimeError("z"))
    assert svc_with(client)._lookup_browser_source_size_locked("alerts", 5000) is None
    client2 = FakeOBS(browser_inputs=[], inputs="junk")
    assert svc_with(client2)._lookup_browser_source_size_locked("alerts", None) is None


def test_lookup_size_locked_exact_port_match_with_defaults():
    client = FakeOBS(
        defaults={"width": 800, "height": 600},
        browser_inputs=[],
        inputs=[
            {"inputName": "Alerts", "inputKind": "browser_source"},
            {"inputName": "Mic", "inputKind": "wasapi_input_capture"},
        ],
        settings={"Alerts": {"url": "http://127.0.0.1:5000/alerts"}},
    )
    out = svc_with(client)._lookup_browser_source_size_locked(" alerts ", "5000")
    assert out == {"source_name": "Alerts", "width": 800, "height": 600, "url": "http://127.0.0.1:5000/alerts"}
    # An unusable port is ignored: any port matches the path.
    assert svc_with(client)._lookup_browser_source_size_locked("alerts", "http")["source_name"] == "Alerts"
    assert svc_with(client)._lookup_browser_source_size_locked("alerts", "")["source_name"] == "Alerts"


def test_lookup_size_locked_skips_bad_rows_and_falls_back_to_path_only():
    client = FakeOBS(
        defaults="notadict",
        browser_inputs=[
            {"inputName": ""},
            {"inputName": "Broken"},
            {"inputName": "NoSettings"},
            {"inputName": "Other"},
            {"inputName": "ZeroSize"},
            {"inputName": "WrongPort"},
        ],
        settings={
            "Broken": RuntimeError("gone"),
            "NoSettings": None,
            "Other": {"url": "http://127.0.0.1:5000/chat", "width": 10, "height": 10},
            "ZeroSize": {"url": "http://127.0.0.1:5000/alerts", "width": 0, "height": 10},
            "WrongPort": {"url": "http://127.0.0.1:6000/alerts", "width": 1280, "height": 720},
        },
        program=RuntimeError("no program"),
    )
    out = svc_with(client)._lookup_browser_source_size_locked("alerts", 5000)
    assert out == {"source_name": "WrongPort", "width": 1280, "height": 720, "url": "http://127.0.0.1:6000/alerts"}


def test_lookup_size_locked_no_match_samples_urls():
    rows = [{"inputName": ""}, {"inputName": "Broken"}, {"inputName": "NoUrl"}, {"inputName": "Chat"}]
    client = FakeOBS(
        browser_inputs=rows,
        settings={"Broken": RuntimeError("x"), "NoUrl": {}, "Chat": {"url": "http://127.0.0.1:5000/chat"}},
    )
    assert svc_with(client)._lookup_browser_source_size_locked("alerts", None) is None
    sampled = [c for c in client.calls if c[0] == "get_input_settings"]
    # 3 named rows in _collect + 3 again while sampling URLs
    assert len(sampled) == 6


def test_lookup_size_locked_uses_program_scene_to_break_ties():
    rows = [{"inputName": "A1"}, {"inputName": "A2"}]
    settings = {
        "A1": {"url": "http://127.0.0.1:5000/alerts", "width": 1920, "height": 1080},
        "A2": {"url": "http://127.0.0.1:5000/alerts", "width": 1280, "height": 720},
    }
    client = FakeOBS(browser_inputs=rows, settings=settings, program={"currentProgramSceneName": "Main"},
                     items={"Main": ["A2", ""]})
    out = svc_with(client)._lookup_browser_source_size_locked("alerts", 5000)
    assert out["source_name"] == "A2"


def test_lookup_size_locked_ambiguous_returns_none():
    rows = [{"inputName": "A1"}, {"inputName": "A2"}]
    settings = {
        "A1": {"url": "http://127.0.0.1:5000/alerts", "width": 1920, "height": 1080},
        "A2": {"url": "http://127.0.0.1:5000/alerts", "width": 1280, "height": 720},
    }
    for program, items in [({"sceneName": ""}, {}), ({"scene_name": "Main"}, {"Main": "notalist"})]:
        client = FakeOBS(browser_inputs=rows, settings=settings, program=program, items=items)
        assert svc_with(client)._lookup_browser_source_size_locked("alerts", 5000) is None


# --------------------------------------------------------------------------- dispatcher


class DispatchOBS:
    def __init__(self, item_id: Any = 7, enabled: Any = True, transform: Any = None):
        self.calls: List[tuple] = []
        self.item_id = item_id
        self.enabled = enabled
        self.transform = transform

    def __getattr__(self, name):
        def method(*args, **kw):
            self.calls.append((name, args, kw))
            if name == "get_scene_item_id":
                return {"sceneItemId": self.item_id}
            if name == "get_scene_item_enabled":
                return {"sceneItemEnabled": self.enabled}
            if name == "send":
                return self.transform
            return None

        return method


def dispatch(op, args, client=None):
    client = client or DispatchOBS()
    svc_with(client)._dispatch_connector_operation(op, args)
    return client.calls


def test_dispatch_requires_client():
    with pytest.raises(RuntimeError, match="not connected"):
        obs.ObsServiceImpl()._dispatch_connector_operation("start_stream", {})


@pytest.mark.parametrize("op,method", [("set_program_scene", "set_current_program_scene"),
                                       ("set_preview_scene", "set_current_preview_scene")])
def test_dispatch_scene_ops(op, method):
    assert dispatch(op, {"scene_name": "Main"}) == [(method, ("Main",), {})]
    assert dispatch(op, {"name": "Alt"}) == [(method, ("Alt",), {})]
    with pytest.raises(ValueError, match="scene_name is required"):
        dispatch(op, {})


@pytest.mark.parametrize("op", ["set_source_enabled", "toggle_source", "set_source_transform"])
def test_dispatch_source_ops_validation(op):
    with pytest.raises(ValueError, match="scene_name and source_name are required"):
        dispatch(op, {"scene_name": "S"})
    with pytest.raises(RuntimeError, match="Could not resolve scene item id"):
        dispatch(op, {"scene_name": "S", "source_name": "cam"}, DispatchOBS(item_id=None))


def test_dispatch_set_source_enabled():
    calls = dispatch("set_source_enabled", {"scene_name": "S", "source_name": "cam", "search_offset": "2"})
    assert calls == [("get_scene_item_id", ("S", "cam", 2), {}), ("set_scene_item_enabled", ("S", 7, True), {})]
    calls = dispatch("set_source_enabled", {"scene_name": "S", "source_name": "cam", "enabled": "off", "search_offset": ""})
    assert calls == [("get_scene_item_id", ("S", "cam", None), {}), ("set_scene_item_enabled", ("S", 7, False), {})]
    calls = dispatch("set_source_enabled", {"scene_name": "S", "source_name": "cam", "enabled": False})
    assert calls[-1] == ("set_scene_item_enabled", ("S", 7, False), {})


def test_dispatch_toggle_source():
    calls = dispatch("toggle_source", {"scene_name": "S", "source_name": "cam"}, DispatchOBS(enabled=True))
    assert calls[-1] == ("set_scene_item_enabled", ("S", 7, False), {})
    calls = dispatch("toggle_source", {"scene_name": "S", "source_name": "cam", "search_offset": 1}, DispatchOBS(enabled=None))
    assert calls[0] == ("get_scene_item_id", ("S", "cam", 1), {})
    assert calls[-1] == ("set_scene_item_enabled", ("S", 7, True), {})


def test_dispatch_set_source_transform_merge():
    client = DispatchOBS(transform={"sceneItemTransform": {"positionX": 0, "rotation": 5}})
    calls = dispatch("set_source_transform",
                     {"scene_name": "S", "source_name": "cam", "position_x": "10", "search_offset": None}, client)
    assert calls[1] == ("send", ("GetSceneItemTransform", {"sceneName": "S", "sceneItemId": 7}), {"raw": True})
    assert calls[2] == ("set_scene_item_transform", ("S", 7, {"positionX": 10, "rotation": 5}), {})


def test_dispatch_set_source_transform_merge_without_current():
    for transform in (None, {"sceneItemTransform": "junk"}):
        calls = dispatch("set_source_transform", {"scene_name": "S", "source_name": "cam", "scale_x": 2},
                         DispatchOBS(transform=transform))
        assert calls[-1] == ("set_scene_item_transform", ("S", 7, {"scaleX": 2}), {})


def test_dispatch_set_source_transform_replace():
    calls = dispatch("set_source_transform", {"scene_name": "S", "source_name": "cam",
                                              "merge_with_current_transform": "no", "rotation": "90"})
    assert [c[0] for c in calls] == ["get_scene_item_id", "set_scene_item_transform"]
    assert calls[-1][1] == ("S", 7, {"rotation": 90})
    with pytest.raises(ValueError, match="No transform fields supplied"):
        dispatch("set_source_transform", {"scene_name": "S", "source_name": "cam",
                                          "merge_with_current_transform": "0"})


def test_dispatch_input_mute_ops():
    assert dispatch("set_input_mute", {"input_name": "Mic"}) == [("set_input_mute", ("Mic", True), {})]
    assert dispatch("set_input_mute", {"input_name": "Mic", "muted": "false"}) == [("set_input_mute", ("Mic", False), {})]
    assert dispatch("toggle_input_mute", {"input_name": "Mic"}) == [("toggle_input_mute", ("Mic",), {})]
    for op in ("set_input_mute", "toggle_input_mute"):
        with pytest.raises(ValueError, match="input_name is required"):
            dispatch(op, {})


@pytest.mark.parametrize("op", ["start_stream", "stop_stream", "toggle_stream", "start_record", "stop_record",
                                "toggle_record"])
def test_dispatch_output_ops(op):
    assert dispatch(op, {}) == [(op, (), {})]


def test_dispatch_unlisted_op_is_noop():
    assert dispatch("something_else", {}) == []


def test_connector_ops_all_dispatchable():
    for op in obs._CONNECTOR_OPS:
        client = DispatchOBS(transform={"sceneItemTransform": {}})
        args = {"scene_name": "S", "name": "S", "source_name": "cam", "input_name": "Mic", "position_x": 1}
        svc_with(client)._dispatch_connector_operation(op, args)
        assert client.calls, op

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules.nicegui_outbox_patch: version parsing, patch application and the patched loop."""

from __future__ import annotations

import asyncio
import gc
import inspect
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from modules import nicegui_outbox_patch as nop


# --------------------------------------------------------------------------- version helpers

@pytest.mark.parametrize(
    "version, expected",
    [
        (None, None),
        ("", None),
        ("3", (3, 0, 0)),
        ("3.13", (3, 13, 0)),
        ("3.13.0", (3, 13, 0)),
        ("3.13.0.4", (3, 13, 0)),
        ("3.13.0rc1", None),
        ("abc", None),
    ],
)
def test_parse_nicegui_version(version, expected):
    assert nop._parse_nicegui_version(version) == expected


@pytest.mark.parametrize(
    "version, ok",
    [
        ("3.12.1", True),
        ("3.12.0", False),
        ("3.13", True),
        ("3.11.9", False),
        ("2.20.0", False),
        ("4.0.0", False),
        (None, False),
        ("x.y", False),
    ],
)
def test_nicegui_version_supported(version, ok):
    assert nop._nicegui_version_supported(version) is ok


# --------------------------------------------------------------------------- patch application

class FakeDeleted:
    pass


class FakeJsComponent:
    def __init__(self, name, key=None, tag=None):
        self.name = name
        self.key = key or f"{name}-key"
        self.tag = tag or f"{name}-tag"


@pytest.fixture
def fake_nicegui(monkeypatch):
    nicegui_mod = ModuleType("nicegui")
    nicegui_mod.__version__ = "3.13.0"

    class Outbox:
        loop = "original"

    outbox_mod = SimpleNamespace(Outbox=Outbox, Deleted=FakeDeleted, deleted=FakeDeleted())
    core_mod = ModuleType("nicegui.core")
    core_mod.app = SimpleNamespace(handle_exception=MagicMock())
    deps_mod = ModuleType("nicegui.dependencies")
    deps_mod.JsComponent = FakeJsComponent
    for name, mod in {
        "nicegui": nicegui_mod,
        "nicegui.core": core_mod,
        "nicegui.outbox": outbox_mod,
        "nicegui.dependencies": deps_mod,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return SimpleNamespace(nicegui=nicegui_mod, outbox=outbox_mod, core=core_mod, Outbox=Outbox)


def test_patch_applied_and_logged(fake_nicegui, caplog):
    with caplog.at_level("WARNING", logger=nop.logger.name):
        nop.ensure_outbox_snapshot_patch()
    assert getattr(fake_nicegui.Outbox, nop._PATCH_ATTR) is True
    assert inspect.iscoroutinefunction(fake_nicegui.Outbox.loop)
    assert "Patched NiceGUI 3.13.0" in caplog.text


def test_patch_is_idempotent(fake_nicegui):
    nop.ensure_outbox_snapshot_patch()
    first = fake_nicegui.Outbox.loop
    nop.ensure_outbox_snapshot_patch()
    assert fake_nicegui.Outbox.loop is first


def test_unsupported_version_skips(fake_nicegui, caplog):
    fake_nicegui.nicegui.__version__ = "2.0.0"
    with caplog.at_level("WARNING", logger=nop.logger.name):
        nop.ensure_outbox_snapshot_patch()
    assert fake_nicegui.Outbox.loop == "original"
    assert "outside supported range (>= 3.12.1)" in caplog.text


def test_missing_version_attribute_skips(fake_nicegui):
    del fake_nicegui.nicegui.__version__
    nop.ensure_outbox_snapshot_patch()
    assert fake_nicegui.Outbox.loop == "original"


def test_import_failure_is_swallowed(fake_nicegui, monkeypatch, caplog):
    monkeypatch.delitem(sys.modules, "nicegui.dependencies")
    monkeypatch.setitem(sys.modules, "nicegui.dependencies", None)  # import raises ImportError
    with caplog.at_level("WARNING", logger=nop.logger.name):
        nop.ensure_outbox_snapshot_patch()
    assert fake_nicegui.Outbox.loop == "original"
    assert "Could not apply NiceGUI outbox snapshot patch" in caplog.text


# --------------------------------------------------------------------------- patched loop behavior

class FakeElement:
    def __init__(self, ident, component=None):
        self.ident = ident
        self.component = component

    def _to_dict(self):
        return {"id": self.ident}


class FakeOutboxSelf:
    """Stands in for an Outbox instance. Stops the loop after ``iterations`` passes."""

    def __init__(self, client, iterations=1, emit=None):
        self.client = client
        self.updates = {}
        self.messages = []
        self._loaded_components = set()
        self._enqueue_event = None
        self._checks = 0
        self._iterations = iterations
        self.emitted = []
        self._emit_impl = emit

    @property
    def _should_stop(self):
        self._checks += 1
        return self._checks > self._iterations

    async def _emit(self, message):
        self.emitted.append(message)
        if self._emit_impl:
            self._emit_impl(message)


@pytest.fixture
def patched(fake_nicegui, monkeypatch):
    nop.ensure_outbox_snapshot_patch()
    sleeps = []

    async def fast_sleep(seconds):
        sleeps.append(seconds)

    fake_asyncio = SimpleNamespace(
        Event=asyncio.Event,
        wait_for=asyncio.wait_for,
        TimeoutError=asyncio.TimeoutError,
        CancelledError=asyncio.CancelledError,
        sleep=fast_sleep,
    )
    monkeypatch.setattr(nop, "asyncio", fake_asyncio)
    return SimpleNamespace(loop=fake_nicegui.Outbox.loop, ns=fake_nicegui, sleeps=sleeps, asyncio=fake_asyncio)


def run(coro):
    return asyncio.run(coro)


def test_loop_sends_updates_and_js_components(patched):
    client = SimpleNamespace(id="c1", has_socket_connection=True)
    box = FakeOutboxSelf(client)
    deleted = patched.ns.outbox.deleted
    new_comp = FakeJsComponent("chart")
    loaded_comp = FakeJsComponent("old")
    box._loaded_components.add("old")
    box.updates = {
        1: FakeElement(1, new_comp),
        2: FakeElement(2, loaded_comp),
        3: FakeElement(3, component="not-js"),
        4: deleted,
    }
    box.messages = [("c1", "run_js", {"code": "1"})]
    run(patched.loop(box))

    assert box.emitted[0] == ("c1", "load_js_components",
                              {"components": [{"key": "chart-key", "tag": "chart-tag"}]})
    assert box.emitted[1] == ("c1", "update", {1: {"id": 1}, 2: {"id": 2}, 3: {"id": 3}, 4: None})
    assert box.emitted[2] == ("c1", "run_js", {"code": "1"})
    assert box._loaded_components == {"old", "chart"}
    assert box.updates == {} and box.messages == []
    assert not box._enqueue_event.is_set()
    assert gc.isenabled()


def test_loop_without_new_js_components_sends_only_update(patched):
    client = SimpleNamespace(id="c1", has_socket_connection=True)
    box = FakeOutboxSelf(client)
    box.updates = {7: FakeElement(7, component=None)}
    run(patched.loop(box))
    assert box.emitted == [("c1", "update", {7: {"id": 7}})]


def test_loop_keeps_gc_disabled_if_it_was(patched):
    client = SimpleNamespace(id="c1", has_socket_connection=True)
    box = FakeOutboxSelf(client)
    box.updates = {1: FakeElement(1)}
    gc.disable()
    try:
        run(patched.loop(box))
        assert not gc.isenabled()
    finally:
        gc.enable()


@pytest.mark.parametrize("client", [None, SimpleNamespace(id="c", has_socket_connection=False)])
def test_loop_waits_when_not_connected(patched, client):
    box = FakeOutboxSelf(client)
    box.updates = {1: FakeElement(1)}
    run(patched.loop(box))
    assert patched.sleeps == [0.1]
    assert box.emitted == []
    assert box._enqueue_event.is_set()  # not cleared, updates kept
    assert 1 in box.updates


def test_loop_waits_for_event_and_times_out(patched):
    client = SimpleNamespace(id="c1", has_socket_connection=True)
    box = FakeOutboxSelf(client, iterations=2)
    waits = []

    async def fake_wait_for(aw, timeout):
        waits.append(timeout)
        aw.close()
        raise TimeoutError

    patched.asyncio.wait_for = fake_wait_for
    run(patched.loop(box))
    # first pass: event is set, nothing to do, event cleared; second pass: waits and times out
    assert waits == [1.0]
    assert box.emitted == []


def test_loop_proceeds_when_event_fires(patched):
    client = SimpleNamespace(id="c1", has_socket_connection=True)
    box = FakeOutboxSelf(client, iterations=2)

    async def fake_wait_for(aw, timeout):
        aw.close()
        box.messages.append(("c1", "msg", {}))

    patched.asyncio.wait_for = fake_wait_for
    run(patched.loop(box))
    assert box.emitted == [("c1", "msg", {})]


def test_emit_failure_is_reported_and_others_still_sent(patched):
    client = SimpleNamespace(id="c1", has_socket_connection=True)
    err = RuntimeError("emit failed")

    def emit(message):
        if message[1] == "bad":
            raise err

    box = FakeOutboxSelf(client, emit=emit)
    box.messages = [("c1", "bad", {}), ("c1", "good", {})]
    run(patched.loop(box))
    assert [m[1] for m in box.emitted] == ["bad", "good"]
    patched.ns.core.app.handle_exception.assert_called_once_with(err)


def test_body_exception_is_reported_and_backs_off(patched):
    class BadClient:
        id = "c1"

        @property
        def has_socket_connection(self):
            raise ValueError("boom")

    box = FakeOutboxSelf(BadClient())
    run(patched.loop(box))
    (exc,), _ = patched.ns.core.app.handle_exception.call_args
    assert isinstance(exc, ValueError)
    assert patched.sleeps == [0.1]


def test_cancelled_error_breaks_loop(patched):
    class CancelClient:
        id = "c1"

        @property
        def has_socket_connection(self):
            raise asyncio.CancelledError

    box = FakeOutboxSelf(CancelClient(), iterations=5)
    run(patched.loop(box))
    assert box._checks == 1  # broke out without re-checking _should_stop
    patched.ns.core.app.handle_exception.assert_not_called()

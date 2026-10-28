# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/media_preview.py (alert media preview buttons, lengths, TTS samples)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402
from nicegui.testing.user_interaction import UserInteraction  # noqa: E402

from modules import path_utils  # noqa: E402
from modules import template_config_parser as tcp  # noqa: E402
from modules.uiwindows import file_picker  # noqa: E402
from modules.uiwindows import media_preview as mp  # noqa: E402
from ui_sim import run_ui  # noqa: E402


class _Awaitable:
    def __init__(self, value):
        self.value = value

    def __await__(self):
        if False:
            yield
        return self.value


class FakeClient:
    def __init__(self, result=None, exc=None):
        self.scripts = []
        self.result = result
        self.exc = exc

    def run_javascript(self, code, timeout=None):
        self.scripts.append(code)
        if self.exc:
            raise self.exc
        return _Awaitable(self.result)

    def __hash__(self):
        return id(self)


@pytest.fixture(autouse=True)
def _fresh_clients(monkeypatch):
    import weakref

    monkeypatch.setattr(mp, "_loaded_clients", weakref.WeakSet())
    monkeypatch.setattr(mp, "_timing_cache", {})


@pytest.fixture
def assets(tmp_path, monkeypatch):
    root = tmp_path / "assets"
    root.mkdir()
    monkeypatch.setattr(path_utils, "get_assets_path", lambda rel="": os.path.join(str(root), rel) if rel else str(root))
    return root


# ---------------------------------------------------------------- script loading

def test_read_asset_reads_file(assets):
    (assets / "default_assets").mkdir()
    (assets / "default_assets" / "x.js").write_text("hello();", encoding="utf-8")
    assert mp._read_asset("default_assets/x.js") == "hello();"


def test_read_asset_missing_logs(assets, caplog):
    assert mp._read_asset("nope.js") == ""
    assert "Could not load nope.js" in caplog.text


def test_ensure_preview_script_once_per_client(monkeypatch):
    monkeypatch.setattr(mp, "_read_asset", lambda rel: f"/*{rel}*/")
    c = FakeClient()
    mp.ensure_preview_script(c)
    mp.ensure_preview_script(c)
    assert c.scripts == [f"/*{mp.PREVIEW_JS_ASSET}*/"]


def test_ensure_preview_script_empty_script_not_run(monkeypatch):
    monkeypatch.setattr(mp, "_read_asset", lambda rel: "")
    c = FakeClient()
    mp.ensure_preview_script(c)
    assert c.scripts == []
    assert c in mp._loaded_clients


def test_ensure_preview_script_without_context_returns(monkeypatch):
    class NoContext:
        @property
        def client(self):
            raise RuntimeError("no slot stack")

    monkeypatch.setattr(mp, "context", NoContext())
    mp.ensure_preview_script()
    assert len(mp._loaded_clients) == 0


def test_ensure_preview_script_uses_context_client(monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(mp, "context", SimpleNamespace(client=c))
    monkeypatch.setattr(mp, "_read_asset", lambda rel: "js")
    mp.ensure_preview_script()
    assert c.scripts == ["js"]


# ---------------------------------------------------------------- buttons

def test_set_button_data_only_updates_on_change():
    btn = SimpleNamespace(_props={}, updates=0)
    btn.update = lambda: setattr(btn, "updates", btn.updates + 1)
    mp.set_button_data(btn, sound_url="/a.mp3", files=["a", "b"], volume=0.5, gone=None, pair=("x",))
    assert btn._props == {"data-sound-url": "/a.mp3", "data-files": '["a", "b"]', "data-volume": "0.5",
                          "data-gone": "", "data-pair": '["x"]'}
    assert btn.updates == 1
    mp.set_button_data(btn, sound_url="/a.mp3")
    assert btn.updates == 1
    mp.set_button_data(btn)
    assert btn.updates == 1


def test_preview_button_builds_and_handles_pick(monkeypatch):
    monkeypatch.setattr(mp, "_read_asset", lambda rel: "")
    picks = []
    holder = {}

    def build():
        holder["b"] = mp.preview_button("Play", icon="volume_up", on_pick=picks.append, sound_url="/s.mp3")
        holder["plain"] = mp.preview_button("Plain")

        def bad(_name):
            raise RuntimeError("boom")

        holder["bad"] = mp.preview_button("Bad", on_pick=bad)

    async def check(user):
        b = holder["b"]
        assert b.props["icon"] == "volume_up"
        assert b.props["data-icon"] == "volume_up"
        assert b.props["data-sound-url"] == "/s.mp3"
        assert "mpv-btn" in b.classes
        listener = next(iter(b._event_listeners.values()))
        assert "MycelianPreview" in listener.js_handler
        UserInteraction(user, {b}, None).trigger("click", "picked.mp3")
        UserInteraction(user, {b}, None).trigger("click", "")
        assert picks == ["picked.mp3"]
        assert holder["plain"].props["data-icon"] == "play_arrow"
        UserInteraction(user, {holder["plain"]}, None).trigger("click", "x.mp3")  # no handler: fine
        UserInteraction(user, {holder["bad"]}, None).trigger("click", "x.mp3")  # handler error swallowed
        assert len(mp._loaded_clients) == 1

    run_ui(build, check)


# ---------------------------------------------------------------- duration

def test_audio_duration_values(monkeypatch):
    monkeypatch.setattr(mp, "_read_asset", lambda rel: "")
    assert asyncio.run(mp.audio_duration("")) is None
    c = FakeClient(result=3.5)
    assert asyncio.run(mp.audio_duration("/a b.mp3", c)) == 3.5
    assert c.scripts[-1] == 'window.MycelianPreview.duration("/a b.mp3")'
    assert asyncio.run(mp.audio_duration("/x", FakeClient(result=None))) is None
    assert asyncio.run(mp.audio_duration("/x", FakeClient(result=0))) is None
    assert asyncio.run(mp.audio_duration("/x", FakeClient(result="nan?"))) is None
    assert asyncio.run(mp.audio_duration("/x", FakeClient(exc=TimeoutError()))) is None


def test_audio_duration_uses_context_client(monkeypatch):
    monkeypatch.setattr(mp, "_read_asset", lambda rel: "")
    monkeypatch.setattr(mp, "context", SimpleNamespace(client=FakeClient(result="2")))
    assert asyncio.run(mp.audio_duration("/x.mp3")) == 2.0


# ---------------------------------------------------------------- files

def test_asset_file_and_folder(assets):
    (assets / "sounds").mkdir()
    (assets / "sounds" / "a.mp3").write_bytes(b"x")
    assert mp.asset_file("") is None
    assert mp.asset_file("   ") is None
    assert mp.asset_file("/assets/sounds/", "a.mp3") == assets.resolve() / "sounds" / "a.mp3"
    assert mp.asset_file("/assets/sounds", "missing.mp3") is None
    assert mp.asset_file("/assets/sounds") == assets.resolve() / "sounds"
    assert mp.asset_file("/assets/sounds/a.mp3") is None  # a file is not a folder
    assert mp.asset_file("/other/x", "a.mp3") is None
    assert mp.asset_file("/assets/../../etc", "passwd") is None


def test_media_url_and_folder_base(monkeypatch, tmp_path):
    assert mp.media_url(None) == ""
    assert mp.folder_base_url(None) == ""
    monkeypatch.setattr(file_picker, "assets_root", lambda: tmp_path)
    monkeypatch.setattr(file_picker, "_media_url", lambda p, root: f"/_media/{p.name}?v=3")
    assert mp.media_url(tmp_path / "a.mp3") == "/_media/a.mp3?v=3"
    assert mp.folder_base_url(tmp_path / "dir") == "/_media/dir/"

    def boom(p, root):
        raise RuntimeError("no route")

    monkeypatch.setattr(file_picker, "_media_url", boom)
    assert mp.media_url(tmp_path / "a.mp3") == ""
    assert mp.folder_base_url(tmp_path / "dir") == ""


def test_random_sound_files(tmp_path, monkeypatch):
    assert mp.random_sound_files(None) == []
    assert mp.random_sound_files(tmp_path / "missing") == []
    for name in ("b.MP3", "a.wav", "c.txt", "d.flac"):
        (tmp_path / name).write_bytes(b"")
    (tmp_path / "sub.mp3").mkdir()
    assert mp.random_sound_files(tmp_path) == ["a.wav", "b.MP3", "d.flac"]
    monkeypatch.setattr(mp, "_RANDOM_LIST_LIMIT", 2)
    assert len(mp.random_sound_files(tmp_path)) == 2


# ---------------------------------------------------------------- animation timing

def _gif(path, delays, loop=0):
    from PIL import Image

    frames = [Image.new("RGB", (4, 4), (i * 40 % 255, 0, 0)) for i in range(len(delays))]
    kwargs = {"save_all": True, "append_images": frames[1:], "duration": list(delays)}
    if loop is not None:
        kwargs["loop"] = loop
    frames[0].save(path, **kwargs)
    return path


def test_animation_timing_none_missing_and_static(tmp_path):
    from PIL import Image

    assert mp.animation_timing(None) is None
    assert mp.animation_timing(tmp_path / "missing.gif") is None
    Image.new("RGB", (4, 4)).save(tmp_path / "still.png")
    assert mp.animation_timing(tmp_path / "still.png") is None


def test_animation_timing_cached(tmp_path, monkeypatch):
    path = _gif(tmp_path / "a.gif", [100, 100])
    calls = []
    real = mp._read_animation_timing
    monkeypatch.setattr(mp, "_read_animation_timing", lambda p: calls.append(p) or real(p))
    first = mp.animation_timing(path)
    assert mp.animation_timing(path) is first
    assert len(calls) == 1


def test_animation_timing_loops_count(tmp_path):
    t = mp.animation_timing(_gif(tmp_path / "a.gif", [50, 50], loop=3))
    assert t.frames == 2 and t.loops == 3
    assert mp.describe_animation(t) == "2 frames · 20 fps · loops 3 times"


def test_animation_timing_cache_cleared_when_big(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "_timing_cache", {(str(i), 0, 0): None for i in range(257)})
    path = _gif(tmp_path / "a.gif", [100, 100])
    assert mp.animation_timing(path).frames == 2
    assert len(mp._timing_cache) == 1


def test_read_animation_timing_bad_file(tmp_path):
    p = tmp_path / "bad.gif"
    p.write_bytes(b"not an image")
    assert mp._read_animation_timing(p) is None


def test_read_animation_timing_zero_seconds(monkeypatch, tmp_path):
    class Img:
        n_frames = 2
        info = {"duration": 0}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def seek(self, i):
            pass

    import PIL.Image

    monkeypatch.setattr(mp, "_SHORT_FRAME_MS", 0)
    monkeypatch.setattr(PIL.Image, "open", lambda p: Img())
    t = mp._read_animation_timing(tmp_path / "x.gif")
    assert t.seconds == 0 and t.fps == 0.0 and t.loops is None


def test_describe_animation_rounding():
    t = mp.AnimationTiming(seconds=3.0, frames=36, fps=12.0, loops=None)
    assert mp.describe_animation(t) == "36 frames · 12 fps · plays once"
    t2 = mp.AnimationTiming(seconds=1.0, frames=7, fps=7.25, loops=0)
    assert mp.describe_animation(t2, frames=False) == "7.2 fps · loops forever"


@pytest.mark.parametrize("value,text", [(-1, ""), (59.96, "60 s"), (60, "1:00"), (3600, "60:00"), (0.05, "0.1 s")])
def test_format_seconds_edges(value, text):
    assert mp.format_seconds(value) == text


# ---------------------------------------------------------------- TTS

def test_mock_tts_messages():
    bits = mp.mock_tts_messages("bits")
    assert bits[:2] == mp.MOCK_TTS_MESSAGES["bits"]
    assert bits[2:] == mp.MOCK_TTS_MESSAGES["_"]
    assert mp.mock_tts_messages("unknown") == mp.MOCK_TTS_MESSAGES["_"]


def test_tts_preview_texts():
    assert mp.tts_preview_texts("bits", "custom_message", "  Hi there ") == ["Hi there"]
    assert mp.tts_preview_texts("bits", "custom_message", "  ") == []
    assert mp.tts_preview_texts("bits", "custom_message", None) == []
    assert mp.tts_preview_texts("subs", "alert_message", "ignored") == mp.mock_tts_messages("subs")


class FakeParser:
    def __init__(self, cfg=None, exc=None):
        self.cfg, self.exc, self.calls = cfg, exc, []

    def load_config(self, name, **kw):
        self.calls.append((name, kw))
        if self.exc:
            raise self.exc
        return self.cfg


def test_tts_voice_settings_from_alerts_config(monkeypatch):
    p = FakeParser({"elements": [{"id": "TtsRate", "value": "1.5"}, {"id": "TtsPitch", "value": ""},
                                 {"id": "TtsVolume", "value": 0.25}]})
    monkeypatch.setattr(tcp, "_shared_parser", p)
    assert mp.tts_voice_settings() == {"rate": 1.5, "pitch": 1.0, "volume": 0.25}
    assert p.calls == [("alerts", {"include_dynamic_controls": False, "create": False})]


def test_tts_voice_settings_defaults_on_error(monkeypatch):
    monkeypatch.setattr(tcp, "_shared_parser", FakeParser(exc=RuntimeError("x")))
    assert mp.tts_voice_settings() == {"rate": 1.0, "pitch": 1.0, "volume": 1.0}
    monkeypatch.setattr(tcp, "_shared_parser", FakeParser({"elements": [{"id": "TtsRate", "value": "fast"}]}))
    assert mp.tts_voice_settings() == {"rate": 1.0, "pitch": 1.0, "volume": 1.0}

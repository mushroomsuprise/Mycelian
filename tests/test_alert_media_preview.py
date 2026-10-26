# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Alert media previews: sound / GIF lengths, random picks, TTS samples and the picker volume."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.uiwindows import file_picker  # noqa: E402
from modules.uiwindows import media_preview as mp  # noqa: E402


def _gif(path: Path, delays, loop=0):
    from PIL import Image

    frames = [Image.new("RGB", (8, 8), (i * 20 % 255, 0, 0)) for i in range(len(delays))]
    kwargs = {"save_all": True, "append_images": frames[1:], "duration": list(delays)}
    if loop is not None:
        kwargs["loop"] = loop
    frames[0].save(path, **kwargs)
    return path


# --------------------------------------------------------------------------- formatting


def test_format_seconds():
    assert mp.format_seconds(3.4) == "3.4 s"
    assert mp.format_seconds(2.0) == "2 s"
    assert mp.format_seconds(0.5) == "0.5 s"
    assert mp.format_seconds(65) == "1:05"
    assert mp.format_seconds(None) == "" and mp.format_seconds(0) == ""


# --------------------------------------------------------------------------- animation timing


def test_gif_timing_matches_browsers(tmp_path):
    timing = mp.animation_timing(_gif(tmp_path / "a.gif", [100, 200, 100]))
    assert (round(timing.seconds, 2), timing.frames, timing.loops) == (0.4, 3, 0)
    assert round(timing.fps, 1) == 7.5
    assert mp.describe_animation(timing) == "3 frames · 7.5 fps · loops forever"
    assert mp.describe_animation(timing, frames=False) == "7.5 fps · loops forever"
    # Frames of 10 ms or less show for 100 ms in browsers.
    fast = mp.animation_timing(_gif(tmp_path / "b.gif", [10, 10], loop=None))
    assert round(fast.seconds, 2) == 0.2 and fast.loops is None
    assert mp.describe_animation(fast).endswith("plays once")


def test_static_or_missing_images_have_no_timing(tmp_path):
    from PIL import Image

    Image.new("RGB", (4, 4)).save(tmp_path / "still.png")
    assert mp.animation_timing(tmp_path / "still.png") is None
    assert mp.animation_timing(tmp_path / "nope.gif") is None
    assert mp.animation_timing(None) is None


def test_timing_is_cached_per_file_version(tmp_path, monkeypatch):
    path = _gif(tmp_path / "c.gif", [100, 100])
    calls = []
    real = mp._read_animation_timing
    monkeypatch.setattr(mp, "_read_animation_timing", lambda p: calls.append(p) or real(p))
    mp._timing_cache.clear()
    mp.animation_timing(path)
    mp.animation_timing(path)
    assert len(calls) == 1


# --------------------------------------------------------------------------- files


def test_asset_file_and_random_list(tmp_path, monkeypatch):
    monkeypatch.setattr(file_picker, "assets_root", lambda: tmp_path)
    folder = tmp_path / "alerts" / "bits" / "random"
    folder.mkdir(parents=True)
    for name in ("b.mp3", "a.wav", "c.OGG", "notes.txt", "clip.opus"):
        (folder / name).write_bytes(b"x")
    (folder / "sub").mkdir()
    (folder / "sub" / "deep.mp3").write_bytes(b"x")

    found = mp.asset_file("/assets/alerts/bits/random/")
    assert found == folder
    # Same file types as the overlay's random lookup, sorted, sub-folders ignored.
    assert mp.random_sound_files(found) == ["a.wav", "b.mp3", "c.OGG"]
    assert mp.asset_file("/assets/alerts/bits/random", "a.wav") == folder / "a.wav"
    assert mp.asset_file("/assets/alerts/bits/random", "missing.wav") is None
    assert mp.asset_file("/assets/../../etc/") is None
    assert mp.asset_file("") is None and mp.random_sound_files(None) == []


def test_folder_base_url_drops_the_cache_query(monkeypatch, tmp_path):
    monkeypatch.setattr(file_picker, "_media_url", lambda path, root: "/_mycelian_picker_assets/alerts/bits?v=12")
    assert mp.folder_base_url(tmp_path) == "/_mycelian_picker_assets/alerts/bits/"
    assert mp.folder_base_url(None) == ""


# --------------------------------------------------------------------------- text-to-speech


def test_tts_preview_texts():
    assert mp.tts_preview_texts("bits", "custom_message", "  Thanks!  ") == ["Thanks!"]
    assert mp.tts_preview_texts("bits", "custom_message", "   ") == []
    samples = mp.tts_preview_texts("bits", "alert_message", "ignored")
    assert samples[0] in mp.MOCK_TTS_MESSAGES["bits"] and set(mp.MOCK_TTS_MESSAGES["_"]) <= set(samples)
    assert mp.tts_preview_texts("follows", "alert_message", "") == mp.MOCK_TTS_MESSAGES["_"]


def test_tts_voice_settings_follow_the_alerts_template(monkeypatch):
    from modules import template_config_parser as tcp

    config = {"elements": [{"id": "TtsRate", "value": 1.4}, {"id": "TtsPitch", "value": "0.8"}, {"id": "TtsVolume", "value": None}]}
    parser = SimpleNamespace(load_config=lambda name, **kw: config if name == "alerts" else {})
    monkeypatch.setattr(tcp, "get_shared_parser", lambda: parser)
    assert mp.tts_voice_settings() == {"rate": 1.4, "pitch": 0.8, "volume": 1.0}
    monkeypatch.setattr(tcp, "get_shared_parser", lambda: (_ for _ in ()).throw(RuntimeError("no config")))
    assert mp.tts_voice_settings() == {"rate": 1.0, "pitch": 1.0, "volume": 1.0}


# --------------------------------------------------------------------------- file picker volume


def test_picker_takes_a_preview_volume(monkeypatch):
    seen = {}

    class FakePicker:
        def __init__(self, config, start):
            seen["config"] = config

        def open(self):
            seen["opened"] = True

    monkeypatch.setattr(file_picker, "_FilePicker", FakePicker)
    file_picker.open_file_picker(title="t", subtitle="s", extensions=[".mp3"], on_pick=lambda r: None, kind="audio", volume=65)
    assert seen["config"].volume == 65 and seen["opened"]
    file_picker.open_file_picker(title="t", subtitle="s", extensions=[".mp3"], on_pick=lambda r: None)
    assert seen["config"].volume is None


# --------------------------------------------------------------------------- page script


_NODE_HARNESS = r"""
var played = [], spoken = [];
function FakeAudio() { this.volume = 1; var self = this;
  this.play = function () { played.push({ src: self.src, volume: self.volume }); return Promise.resolve(); };
  this.pause = function () {}; }
function el(attrs) {
  var icon = { textContent: 'play_arrow' };
  var classes = {};
  return { attrs: attrs, icon: icon,
    getAttribute: function (n) { return n in attrs ? attrs[n] : null; },
    setAttribute: function (n, v) { attrs[n] = v; },
    querySelector: function () { return icon; },
    closest: function () { return this; },
    classList: { toggle: function (c, on) { classes[c] = on; }, has: function (c) { return !!classes[c]; } } };
}
global.window = global;
global.Audio = FakeAudio;
global.SpeechSynthesisUtterance = function (t) { this.text = t; };
window.speechSynthesis = { cancel: function () {}, speak: function (u) { spoken.push({ text: u.text, rate: u.rate, pitch: u.pitch, volume: u.volume }); } };
var media = [{ tagName: 'VIDEO', muted: true, volume: 1, setAttribute: function () {} }, { tagName: 'AUDIO', volume: 1, setAttribute: function () {} }];
global.document = { querySelectorAll: function () { return media; } };
require(process.argv[2]);
var P = window.MycelianPreview;
var sound = el({ 'data-pv': 'sound', 'data-src': '/m/cheer.wav', 'data-vol': '80' });
var r1 = P.toggle({ target: sound });
var playingIcon = sound.icon.textContent, playingClass = sound.classList.has('is-playing');
P.toggle({ target: sound });  // second click stops
var stoppedIcon = sound.icon.textContent;
var rnd = el({ 'data-pv': 'random', 'data-base': '/m/random/', 'data-files': '["boom 1.wav"]', 'data-vol': '50' });
var picked = P.toggle({ target: rnd });
var empty = P.toggle({ target: el({ 'data-pv': 'random', 'data-files': '[]' }) });
var tts = el({ 'data-pv': 'tts', 'data-texts': '["Hello there"]', 'data-rate': '1.5', 'data-pitch': '0.9', 'data-tts-vol': '0.7' });
P.toggle({ target: tts });
P.setPickerVolume(40);
var v40 = [media[0].volume, media[0].muted, media[1].volume];
P.setPickerVolume(0);
console.log(JSON.stringify({ r1: r1, playingIcon: playingIcon, playingClass: playingClass, stoppedIcon: stoppedIcon,
  played: played, picked: picked, empty: empty, spoken: spoken, v40: v40, muted0: media[0].muted,
  hasDuration: typeof P.duration === 'function' }));
process.exit(0);
"""


def test_preview_script(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    harness = tmp_path / "h.js"
    harness.write_text(_NODE_HARNESS, encoding="utf-8")
    js = ROOT / "assets" / "default_assets" / "mycelian_media_preview.js"
    done = subprocess.run([node, str(harness), str(js)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    assert out["r1"] is None and out["playingIcon"] == "stop" and out["playingClass"] is True
    assert out["stoppedIcon"] == "play_arrow"
    assert out["played"][0] == {"src": "/m/cheer.wav", "volume": 0.8}
    # A random button plays a file from its folder (name URL-encoded) at the alert volume and reports the pick.
    assert out["picked"] == "boom 1.wav" and out["played"][1] == {"src": "/m/random/boom%201.wav", "volume": 0.5}
    assert out["empty"] is None and len(out["played"]) == 2
    assert out["spoken"] == [{"text": "Hello there", "rate": 1.5, "pitch": 0.9, "volume": 0.7}]
    assert out["v40"] == [0.4, False, 0.4] and out["muted0"] is True
    assert out["hasDuration"] is True


def test_alert_cards_use_the_previews():
    source = (ROOT / "modules" / "uiwindows" / "alertsettings.py").read_text(encoding="utf-8")
    assert source.count('preview="random"') == 2
    assert 'preview="sound"' in source and 'preview="image"' in source
    assert "_sync_preview_volume" in source and "_refresh_tts_preview" in source
    # Browse passes the alert's volume so the picker preview starts there.
    assert "volume=volume," in source

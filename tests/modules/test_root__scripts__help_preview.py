# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for scripts/help_preview.py.

Pages are rendered from the real help content and themes (read-only) and written under
tmp_path. The macOS WebKit snapshot path runs against fake PyObjC modules, so no web view
or window is ever created.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import help_preview as hp  # noqa: E402

FIRST_TOPIC = next(iter(hp.HELP_TOPICS))


# --------------------------------------------------------------------------- fake PyObjC


class _Clock:
    now = 0.0


class FakeDate:
    def __init__(self, t):
        self.t = t

    @classmethod
    def dateWithTimeIntervalSinceNow_(cls, secs):  # noqa: N802
        return cls(_Clock.now + secs)

    @classmethod
    def date(cls):
        return cls(_Clock.now)

    def compare_(self, other):  # noqa: N802
        return -1 if self.t < other.t else (0 if self.t == other.t else 1)


class FakeRunLoop:
    runs = 0

    @classmethod
    def currentRunLoop(cls):  # noqa: N802
        return cls()

    def runUntilDate_(self, date):  # noqa: N802
        FakeRunLoop.runs += 1
        _Clock.now = date.t


class FakeNSObject:
    @classmethod
    def alloc(cls):
        return cls.__new__(cls)

    def init(self):
        return self


class FakeConfig(FakeNSObject):
    rect = None

    def setRect_(self, rect):  # noqa: N802
        self.rect = rect


class FakeImage:
    def TIFFRepresentation(self):  # noqa: N802
        return b"tiff-bytes"


class FakeData:
    def __init__(self, payload):
        self.payload = payload

    def writeToFile_atomically_(self, path, atomic):  # noqa: N802
        Path(path).write_bytes(self.payload)
        return True


class FakeBitmapRep:
    @classmethod
    def imageRepWithData_(cls, data):  # noqa: N802
        rep = cls()
        rep.data = data
        return rep

    def representationUsingType_properties_(self, kind, props):  # noqa: N802
        assert kind == "PNG"
        return FakeData(b"PNG:" + self.data)


class FakeWebView(FakeNSObject):
    behaviour: dict = {}
    last = None

    def initWithFrame_configuration_(self, frame, config):  # noqa: N802
        self.frames = [frame]
        self.config = config
        FakeWebView.last = self
        return self

    def setNavigationDelegate_(self, delegate):  # noqa: N802
        self.delegate = delegate

    def loadFileURL_allowingReadAccessToURL_(self, url, access):  # noqa: N802
        self.loaded = (url, access)
        if self.behaviour.get("navigate", True):
            self.delegate.webView_didFinishNavigation_(self, None)

    def evaluateJavaScript_completionHandler_(self, js, handler):  # noqa: N802
        self.js = js
        handler(self.behaviour.get("height", 1234), None)

    def setFrame_(self, frame):  # noqa: N802
        self.frames.append(frame)

    def takeSnapshotWithConfiguration_completionHandler_(self, cfg, handler):  # noqa: N802
        self.snapshot_cfg = cfg
        handler(self.behaviour.get("image", FakeImage()), None)


class FakeURL:
    @staticmethod
    def fileURLWithPath_(path):  # noqa: N802
        return ("file", path)


@pytest.fixture
def fake_objc(monkeypatch):
    _Clock.now = 0.0
    FakeRunLoop.runs = 0
    FakeWebView.behaviour = {}
    FakeWebView.last = None
    shared = {"calls": 0}

    def shared_app():
        shared["calls"] += 1

    appkit = types.ModuleType("AppKit")
    appkit.NSApplication = types.SimpleNamespace(sharedApplication=shared_app)
    appkit.NSBitmapImageRep = FakeBitmapRep
    appkit.NSPNGFileType = "PNG"
    foundation = types.ModuleType("Foundation")
    foundation.NSURL = FakeURL
    foundation.NSDate = FakeDate
    foundation.NSMakeRect = lambda x, y, w, h: (x, y, w, h)
    foundation.NSObject = FakeNSObject
    foundation.NSRunLoop = FakeRunLoop
    webkit = types.ModuleType("WebKit")
    webkit.WKSnapshotConfiguration = FakeConfig
    webkit.WKWebView = FakeWebView
    webkit.WKWebViewConfiguration = FakeConfig
    for name, mod in (
        ("objc", types.ModuleType("objc")),
        ("AppKit", appkit),
        ("Foundation", foundation),
        ("WebKit", webkit),
    ):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(hp, "_NavDelegate", None)
    return shared


# --------------------------------------------------------------------------- build_page


def test_nicegui_static_points_at_package_static():
    import nicegui

    static = hp._nicegui_static()
    assert static == Path(nicegui.__file__).resolve().parent / "static"
    assert (static / "fonts.css").exists()


@pytest.mark.parametrize("theme,dark", [("dark", True), ("light", False)])
def test_build_page_renders_topic(theme, dark):
    topic = hp.HELP_TOPICS[FIRST_TOPIC]
    page = hp.build_page(FIRST_TOPIC, theme, 900)
    assert page.startswith("<!doctype html>")
    assert "width: 900px" in page
    assert f"· {theme}</div>" in page
    assert topic.category.value.replace("_", " ").title() in page
    assert ('<body class="body--dark">' in page) is dark
    assert ('<body class="">' in page) is (not dark)
    assert '<div class="help-doc">' in page
    assert "window.MycelianHelp.enhance" in page
    assert (hp._nicegui_static() / "fonts.css").as_uri() in page
    # Table of contents entries are emitted per heading level
    assert 'class="pv-toc-h2"' in page


def test_build_page_escapes_text(monkeypatch):
    topic = types.SimpleNamespace(
        category=types.SimpleNamespace(value="getting_started"),
        title="A <b> & B",
        summary='Say "hi" <now>',
    )
    monkeypatch.setitem(hp.HELP_TOPICS, "fake_topic", topic)
    monkeypatch.setattr(hp, "render_topic_html", lambda tid: ("<p>body</p>", [("h3", "<toc>", "id")]))
    page = hp.build_page("fake_topic", "dark", 500)
    assert "A &lt;b&gt; &amp; B" in page
    assert "Say &quot;hi&quot; &lt;now&gt;" in page
    assert '<div class="pv-toc-h3">&lt;toc&gt;</div>' in page
    assert "<p>body</p>" in page
    assert "Getting Started · dark" in page


def test_build_page_unknown_topic_raises():
    with pytest.raises(KeyError):
        hp.build_page("no_such_topic", "dark", 100)


def test_build_page_unknown_theme_raises():
    with pytest.raises(FileNotFoundError):
        hp.build_page(FIRST_TOPIC, "no_such_theme", 100)


# --------------------------------------------------------------------------- snapshot


def test_snapshot_writes_png(tmp_path, fake_objc):
    html_path = tmp_path / "page.html"
    png_path = tmp_path / "page.png"
    hp.snapshot(html_path, png_path, 640)
    assert png_path.read_bytes() == b"PNG:tiff-bytes"
    view = FakeWebView.last
    assert fake_objc["calls"] == 1
    assert view.frames == [(0, 0, 640, 900), (0, 0, 640, 1234)]
    assert view.loaded == (("file", str(html_path)), ("file", "/"))
    assert view.js == "document.documentElement.scrollHeight"
    assert view.snapshot_cfg.rect == (0, 0, 640, 1234)
    assert FakeRunLoop.runs > 0  # the fixed waits pumped the run loop


def test_snapshot_reuses_registered_delegate_class(tmp_path, fake_objc):
    hp.snapshot(tmp_path / "a.html", tmp_path / "a.png", 100)
    first = hp._NavDelegate
    assert first is not None and first.__name__ == "HelpPreviewNavDelegate"
    hp.snapshot(tmp_path / "b.html", tmp_path / "b.png", 100)
    assert hp._NavDelegate is first


def test_snapshot_caps_and_defaults_height(tmp_path, fake_objc):
    FakeWebView.behaviour = {"height": 50000}
    hp.snapshot(tmp_path / "a.html", tmp_path / "a.png", 100)
    assert FakeWebView.last.frames[-1] == (0, 0, 100, 16000)
    FakeWebView.behaviour = {"height": None}
    hp.snapshot(tmp_path / "b.html", tmp_path / "b.png", 100)
    assert FakeWebView.last.frames[-1] == (0, 0, 100, 900)


def test_snapshot_load_timeout_still_snapshots(tmp_path, fake_objc):
    FakeWebView.behaviour = {"navigate": False}
    hp.snapshot(tmp_path / "a.html", tmp_path / "a.png", 100)
    assert (tmp_path / "a.png").exists()
    assert _Clock.now >= 15.0  # waited out the load timeout on the fake clock


def test_snapshot_failure_raises(tmp_path, fake_objc):
    FakeWebView.behaviour = {"image": None}
    with pytest.raises(RuntimeError, match="snapshot failed"):
        hp.snapshot(tmp_path / "a.html", tmp_path / "a.png", 100)
    assert not (tmp_path / "a.png").exists()


# --------------------------------------------------------------------------- slice_png


def test_slice_png_splits_at_2x(tmp_path):
    from PIL import Image

    png = tmp_path / "shot.png"
    Image.new("RGB", (10, 250), "red").save(png)
    parts = hp.slice_png(png, 50)  # 100px per chunk at @2x
    assert [p.name for p in parts] == ["shot.part1.png", "shot.part2.png", "shot.part3.png"]
    sizes = []
    for part in parts:
        with Image.open(part) as im:
            sizes.append(im.size)
    assert sizes == [(10, 100), (10, 100), (10, 50)]


# --------------------------------------------------------------------------- main


@pytest.fixture
def fast_pages(monkeypatch):
    calls = []
    monkeypatch.setattr(hp, "build_page", lambda tid, theme, width: calls.append((tid, theme, width)) or f"<{tid}>")
    monkeypatch.setattr(hp, "clear_caches", lambda: calls.append("clear"))
    return calls


def _main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["help_preview.py", *map(str, argv)])
    return hp.main()


def test_main_single_topic(monkeypatch, tmp_path, fast_pages, capsys):
    rc = _main(monkeypatch, FIRST_TOPIC, "--theme", "light", "--width", "800", "--out", tmp_path / "out")
    assert rc == 0
    page = tmp_path / "out" / f"{FIRST_TOPIC}.light.html"
    assert page.read_text(encoding="utf-8") == f"<{FIRST_TOPIC}>"
    assert fast_pages == ["clear", (FIRST_TOPIC, "light", 800)]
    assert capsys.readouterr().out.strip() == str(page)


def test_main_defaults_to_all_topics(monkeypatch, tmp_path, fast_pages):
    assert _main(monkeypatch, "--out", tmp_path) == 0
    assert [c[0] for c in fast_pages[1:]] == list(hp.HELP_TOPICS)
    assert all(c[1:] == ("dark", 1180) for c in fast_pages[1:])


def test_main_all_flag_overrides_topics(monkeypatch, tmp_path, fast_pages):
    assert _main(monkeypatch, FIRST_TOPIC, "--all", "--out", tmp_path) == 0
    assert len(fast_pages) - 1 == len(hp.HELP_TOPICS)


def test_main_unknown_topic_errors(monkeypatch, tmp_path, fast_pages, capsys):
    with pytest.raises(SystemExit) as exc:
        _main(monkeypatch, "nope", "also_nope", "--out", tmp_path / "out")
    assert exc.value.code == 2
    assert "unknown topic(s): nope, also_nope" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_main_default_out_under_root_logs(monkeypatch, tmp_path, fast_pages):
    monkeypatch.setattr(hp, "ROOT", tmp_path)
    assert _main(monkeypatch, FIRST_TOPIC) == 0
    assert (tmp_path / "logs" / "help_preview" / f"{FIRST_TOPIC}.dark.html").exists()


def test_main_png_and_slices(monkeypatch, tmp_path, fast_pages, capsys):
    snaps = []
    monkeypatch.setattr(hp, "snapshot", lambda page, png, width: snaps.append((page, png, width)))
    monkeypatch.setattr(hp, "slice_png", lambda png, h: [png.with_name(f"x.part{i}.png") for i in (1, 2)])
    assert _main(monkeypatch, FIRST_TOPIC, "--png", "--slice", "700", "--out", tmp_path) == 0
    page = tmp_path / f"{FIRST_TOPIC}.dark.html"
    png = tmp_path / f"{FIRST_TOPIC}.dark.png"
    assert snaps == [(page, png, 1180)]
    out = capsys.readouterr().out
    assert f"  -> {png}" in out
    assert f"  -> {tmp_path / 'x.part1.png'}" in out and f"  -> {tmp_path / 'x.part2.png'}" in out


def test_main_png_without_slice(monkeypatch, tmp_path, fast_pages, capsys):
    monkeypatch.setattr(hp, "snapshot", lambda *a: None)
    monkeypatch.setattr(hp, "slice_png", lambda *a: pytest.fail("slice_png should not run"))
    assert _main(monkeypatch, FIRST_TOPIC, "--png", "--out", tmp_path) == 0
    assert capsys.readouterr().out.count("  -> ") == 1


def test_main_end_to_end_real_page(monkeypatch, tmp_path):
    assert _main(monkeypatch, FIRST_TOPIC, "--out", tmp_path) == 0
    text = (tmp_path / f"{FIRST_TOPIC}.dark.html").read_text(encoding="utf-8")
    assert hp.HELP_TOPICS[FIRST_TOPIC].title in text


def test_script_entry_point(monkeypatch, tmp_path):
    real = ROOT / "scripts" / "help_preview.py"
    code = compile(real.read_text(encoding="utf-8"), str(real), "exec")
    monkeypatch.setattr(sys, "argv", ["help_preview.py", "nope", "--out", str(tmp_path)])
    path_before = list(sys.path)
    try:
        with pytest.raises(SystemExit) as exc:
            exec(code, {"__name__": "__main__", "__file__": str(real), "__builtins__": __builtins__})
    finally:
        sys.path[:] = path_before
    assert exc.value.code == 2

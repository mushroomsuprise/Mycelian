# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/help_system/help_render.py: page loading, token expansion, TOC, CSS helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules.help_system import help_render as hr  # noqa: E402
from modules.uiwindows.service_brand_icons import SERVICE_BRAND_SVG  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_caches():
    hr.clear_caches()
    yield
    hr.clear_caches()


@pytest.fixture
def content(tmp_path, monkeypatch):
    """Point content_dir() at a temporary folder."""
    folder = tmp_path / "content"
    folder.mkdir()
    monkeypatch.setattr(hr, "content_dir", lambda: folder)
    return folder


# ----------------------------------------------------------------- paths

def test_package_path_inside_package():
    assert hr._package_path("content") == hr._PACKAGE_DIR / "content"
    assert hr.content_dir().is_dir()


def test_package_path_falls_back_to_resource_path(monkeypatch, tmp_path):
    from modules import path_utils

    monkeypatch.setattr(hr, "_PACKAGE_DIR", tmp_path / "missing")
    seen = []
    monkeypatch.setattr(path_utils, "get_resource_path", lambda rel: seen.append(rel) or str(tmp_path / "bundle" / rel))
    result = hr._package_path("assets", "help.js")
    assert seen == [str(Path("modules", "help_system", "assets", "help.js"))]
    assert result == tmp_path / "bundle" / Path("modules", "help_system", "assets", "help.js")


# ----------------------------------------------------------------- load_raw_html

def test_load_raw_html_reads_and_caches(content):
    (content / "t1.html").write_text("<p>Hi</p>", encoding="utf-8")
    assert hr.load_raw_html("t1") == "<p>Hi</p>"
    (content / "t1.html").write_text("<p>Changed</p>", encoding="utf-8")
    assert hr.load_raw_html("t1") == "<p>Hi</p>"  # cached
    hr.clear_caches()
    assert hr.load_raw_html("t1") == "<p>Changed</p>"


def test_load_raw_html_missing_file_warns(content, caplog):
    assert hr.load_raw_html("nope") == ""
    assert "Help content file missing" in caplog.text


def test_load_raw_html_read_error_logs(content, caplog):
    (content / "dir.html").mkdir()  # reading a directory raises IsADirectoryError
    assert hr.load_raw_html("dir") == ""
    assert "Error reading help content" in caplog.text


# ----------------------------------------------------------------- tokens

def test_expand_icon_and_brand_tokens():
    out = hr.expand_tokens("a {{icon:settings}} b {{ brand:twitch }} c")
    assert '<span class="hx-mi" aria-hidden="true">settings</span>' in out
    assert '<span class="hx-brand">' + SERVICE_BRAND_SVG["twitch"] + "</span>" in out
    assert "{{" not in out


def test_unknown_brand_expands_to_nothing():
    assert hr._brand_svg("no_such_brand") == ""
    assert hr.expand_tokens("x{{brand:no_such_brand}}y") == "xy"


def test_non_token_braces_are_left_alone():
    text = "Use {{user}} and {{icon:Bad-Name}}"
    assert hr.expand_tokens(text) == text


# ----------------------------------------------------------------- text helpers

@pytest.mark.parametrize(
    "text, slug",
    [
        ("Hello World", "hello-world"),
        ("<b>Step&nbsp;1:</b> Connect &amp; go!", "step-1-connect-go"),
        ("!!!", "section"),
        ("", "section"),
        ("  Already-slug  ", "already-slug"),
    ],
)
def test_slugify(text, slug):
    assert hr.slugify(text) == slug


def test_html_to_text_strips_tags_scripts_styles_and_tokens():
    frag = "<h2>Title {{icon:x}}</h2><script>var a=1;</script><STYLE>p{}</STYLE><p>A &amp; B\n\n  C</p>"
    assert hr.html_to_text(frag) == "Title A & B C"


def test_heading_text():
    assert hr._heading_text(" {{icon:star}} <b>Bold</b>&amp;  more\n") == "Bold& more"


# ----------------------------------------------------------------- render_topic_html

def test_render_topic_html_assigns_ids_and_toc(content):
    (content / "page.html").write_text(
        '<h2>Intro</h2><p>x</p><h3 class="c">Intro</h3><h2 id="custom">Own id</h2>'
        "<h2>Intro</h2><h4>Skip me</h4><p>{{icon:info}}</p>",
        encoding="utf-8",
    )
    html, toc = hr.render_topic_html("page")
    assert toc == (
        ("h2", "Intro", "hx-intro"),
        ("h3", "Intro", "hx-intro-2"),
        ("h2", "Own id", "custom"),
        ("h2", "Intro", "hx-intro-3"),
    )
    assert '<h2 id="hx-intro">Intro</h2>' in html
    assert '<h3 class="c" id="hx-intro-2">Intro</h3>' in html
    assert '<h2 id="custom">Own id</h2>' in html
    assert "<h4>Skip me</h4>" in html
    assert 'class="hx-mi"' in html
    assert isinstance(toc, tuple)


def test_render_missing_topic_is_empty(content):
    assert hr.render_topic_html("missing") == ("", ())


def test_topic_plain_text(content):
    (content / "p.html").write_text("<p>Hello <b>there</b></p>", encoding="utf-8")
    assert hr.topic_plain_text("p") == "Hello there"


# ----------------------------------------------------------------- assets

def test_read_asset_ok_and_error(monkeypatch, tmp_path, caplog):
    assert "MycelianHelp" in hr._read_asset("help.js")
    monkeypatch.setattr(hr, "_package_path", lambda *parts: tmp_path / "nope" / Path(*parts))
    assert hr._read_asset("missing.js") == ""
    assert "Error reading help asset missing.js" in caplog.text


def test_help_page_js_cached():
    first = hr.help_page_js()
    assert first and first is hr.help_page_js()


def test_help_page_css_concatenates_and_promotes(monkeypatch, tmp_path):
    css_dir = tmp_path / "assets" / "css"
    css_dir.mkdir(parents=True)
    (css_dir / "b.css").write_text(".b { color: red; }", encoding="utf-8")
    (css_dir / "a.css").write_text(".a { background-color: blue; color: var(--x) }", encoding="utf-8")
    monkeypatch.setattr(hr, "_package_path", lambda *parts: tmp_path.joinpath(*parts))
    css = hr.help_page_css()
    assert css.index(".a") < css.index(".b")
    assert "color: red !important;" in css
    assert "color: var(--x) !important }" in css
    assert "background-color: blue;" in css  # not promoted
    assert css.endswith(hr._LIGHT_MODE_INHERIT)


def test_help_page_css_glob_error(monkeypatch):
    class _Bad:
        def glob(self, pattern):
            raise OSError("boom")

    monkeypatch.setattr(hr, "_package_path", lambda *parts: _Bad())
    assert hr.help_page_css() == hr._LIGHT_MODE_INHERIT


def test_real_help_page_css_has_doc_rules():
    css = hr.help_page_css()
    assert ".help-doc" in css
    assert css.count("{") == css.count("}")


@pytest.mark.parametrize(
    "css, expected",
    [
        ("a{color:red}", "a{color:red !important}"),
        ("a { color : #fff ; }", "a { color : #fff !important ; }"),
        ("a{border-color:red; color: blue !important;}", "a{border-color:red; color: blue !important;}"),
        ("a{background-color:red}", "a{background-color:red}"),
        ("a{xcolor:red}", "a{xcolor:red}"),
    ],
)
def test_promote_color_importance(css, expected):
    assert hr.promote_color_importance(css) == expected
    assert hr._promote_colors is hr.promote_color_importance


# ----------------------------------------------------------------- scope_selectors

def test_scope_selectors_basic_and_comments():
    out = hr.scope_selectors("/* note */ .a, .b > p { color: red; }", ".abt")
    assert out.strip() == ".abt .a, .abt .b > p { color: red; }"


def test_scope_selectors_keeps_already_scoped():
    out = hr.scope_selectors(".abt { x: 1; } .abt .y { x: 2; } .abtx { x: 3; } .abt-z{x:4}", ".abt")
    assert ".abt { x: 1; }" in out
    assert ".abt .y { x: 2; }" in out
    assert ".abt .abtx { x: 3; }" in out
    assert ".abt .abt-z {x:4}" in out


def test_scope_selectors_body_rules():
    out = hr.scope_selectors(
        "body:not(.body--dark) .a { c: 1; } body { c: 2; } body .abt .x { c: 3; } body .abt { c: 4; }", ".abt"
    )
    assert "body:not(.body--dark) .abt .a { c: 1; }" in out
    assert "body .abt { c: 2; }" in out
    assert "body .abt .x { c: 3; }" in out
    assert out.count("body .abt { c: 4; }") == 1


def test_scope_selectors_media_supports_and_keyframes():
    css = (
        "@media (max-width: 600px) { .a { c: 1; } }"
        "@supports (display:grid) { .g { d: 1; } }"
        "@keyframes spin { from { t: 0; } to { t: 1; } }"
    )
    out = hr.scope_selectors(css, ".abt")
    assert "@media (max-width: 600px) { .abt .a { c: 1; } }" in out
    assert ".abt .g { d: 1; }" in out
    assert "@keyframes spin { from { t: 0; } to { t: 1; } }" in out


def test_scope_selectors_trailing_text_and_empty_selector():
    out = hr.scope_selectors(".a, { c: 1; } trailing", ".abt")
    assert out.startswith(".abt .a { c: 1; }")
    assert out.endswith(" trailing")


# ----------------------------------------------------------------- inject_page_stylesheet

class _FakeUI:
    def __init__(self):
        self.head = []

    def add_head_html(self, code, shared=False):
        self.head.append((code, shared))


@pytest.fixture
def fake_nicegui(monkeypatch):
    import nicegui

    fake_ui = _FakeUI()
    monkeypatch.setattr(nicegui, "ui", fake_ui)
    monkeypatch.setattr(hr, "_shared_stylesheets", set())
    return fake_ui


def test_inject_page_stylesheet_adds_once_and_pushes_to_open_page(monkeypatch, fake_nicegui):
    import nicegui

    js = []
    client = SimpleNamespace(has_socket_connection=True, run_javascript=lambda code: js.append(code))
    monkeypatch.setattr(nicegui, "context", SimpleNamespace(client=client))
    hr.inject_page_stylesheet("my-style", ".x{color:red}")
    hr.inject_page_stylesheet("my-style", ".x{color:red}")
    assert fake_nicegui.head == [("<style id='my-style'>.x{color:red}</style>", True)]
    assert len(js) == 2
    assert 'document.getElementById("my-style")' in js[0]
    assert 's.textContent = ".x{color:red}"' in js[0]


def test_inject_page_stylesheet_without_socket(monkeypatch, fake_nicegui):
    import nicegui

    js = []
    client = SimpleNamespace(has_socket_connection=False, run_javascript=lambda code: js.append(code))
    monkeypatch.setattr(nicegui, "context", SimpleNamespace(client=client))
    hr.inject_page_stylesheet("s2", "p{}")
    assert js == []
    assert len(fake_nicegui.head) == 1


def test_inject_page_stylesheet_without_client(monkeypatch, fake_nicegui):
    import nicegui

    class _Ctx:
        @property
        def client(self):
            raise RuntimeError("no slot")

    monkeypatch.setattr(nicegui, "context", _Ctx())
    hr.inject_page_stylesheet("s3", "p{}")
    assert "s3" in hr._shared_stylesheets


def test_clear_caches_clears_all(content):
    (content / "c.html").write_text("<h2>A</h2>", encoding="utf-8")
    hr.render_topic_html("c")
    hr.topic_plain_text("c")
    hr.help_page_js()
    hr.help_page_css()
    hr.clear_caches()
    for fn in (hr.load_raw_html, hr.render_topic_html, hr.topic_plain_text, hr.help_page_css, hr.help_page_js):
        assert fn.cache_info().currsize == 0

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/spore_studio/js_context.py (Advanced JS checker context and pop-out page)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules.spore_studio import js_context as jc  # noqa: E402


@pytest.mark.parametrize(
    "channel, ok",
    [
        ("abcdef12", True),
        ("A-b_" * 16, True),
        ("short", False),
        ("x" * 65, False),
        ("bad channel!", False),
        ("../../etc", False),
        (None, False),
        (12345678, False),
    ],
)
def test_valid_channel(channel, ok):
    assert jc.valid_channel(channel) is ok


def test_template_scripts_lists_inline_and_same_origin_scripts(tmp_path):
    lib = tmp_path / "default_assets" / "lib.js"
    lib.parent.mkdir()
    lib.write_text("window.Lib = {};", encoding="utf-8")
    html = """
    <script src="https://cdn.example/socket.io.js"></script>
    <script src="/assets/default_assets/lib.js?v=2"></script>
    <script src="/assets/default_assets/missing.js"></script>
    <script>MycelianLog.init({});</script>
    <script type="application/json">{"not": "js"}</script>
    <script type="text/javascript">   </script>
    <SCRIPT>
        var socket = io();
        // SPORE_STUDIO:user-begin
        // SPORE_STUDIO:user-end
    </SCRIPT>
    """
    scripts = jc.template_scripts(html, assets_root=str(tmp_path))
    assert [s["kind"] for s in scripts] == ["external", "inline", "inline"]
    assert scripts[0] == {
        "kind": "external", "src": "/assets/default_assets/lib.js?v=2", "code": "window.Lib = {};"
    }
    assert scripts[1]["main"] is False
    assert scripts[2]["main"] is True and "var socket" in scripts[2]["code"]


def test_template_scripts_refuses_paths_outside_assets(tmp_path):
    secret = tmp_path / "secret.js"
    secret.write_text("SECRET", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    html = '<script src="/assets/../secret.js"></script><script src="/assets/%2e%2e/secret.js"></script>'
    assert jc.template_scripts(html, assets_root=str(assets)) == []
    assert jc.template_scripts(None, assets_root=str(assets)) == []


def test_template_scripts_skips_oversized_files_and_uses_custom_reader(tmp_path, monkeypatch):
    big = tmp_path / "big.js"
    big.write_text("x", encoding="utf-8")
    monkeypatch.setattr(jc, "MAX_EXTERNAL_BYTES", 0)
    assert jc.template_scripts('<script src="/assets/big.js"></script>', assets_root=str(tmp_path)) == []
    seen = []

    def reader(src, root):
        seen.append((src, root))
        return "code"

    out = jc.template_scripts('<script src="/x.js"></script>', assets_root="R", read_asset=reader)
    assert out == [{"kind": "external", "src": "/x.js", "code": "code"}] and seen == [("/x.js", "R")]


def test_read_asset_script_handles_unreadable_files(tmp_path, monkeypatch):
    (tmp_path / "a.js").write_text("ok", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("nope")

    monkeypatch.setattr("builtins.open", boom)
    assert jc._read_asset_script("/assets/a.js", str(tmp_path)) is None
    assert jc._read_asset_script("/elsewhere/a.js", str(tmp_path)) is None


def test_popout_page_uses_the_real_editor_dialog():
    editor = (ROOT / "assets" / "default_assets" / "spore_studio" / "editor.html").read_text(encoding="utf-8")
    page = jc.popout_page_html(editor)
    assert page is not None
    assert '<body data-ss-js-popout="1">' in page
    assert 'id="ss-js-input"' in page and 'id="ss-js-ref-search"' in page and 'id="ss-js-popout"' in page
    assert 'class="ss-js-dialog" hidden' not in page, "the pop-out shows the dialog at once"
    assert 'id="ss-root"' not in page, "only the dialog, not the whole Studio"
    order = [page.index(f"/assets/default_assets/{p}") for p in jc._POPOUT_SCRIPTS]
    assert order == sorted(order)
    assert page.index("socket.io.js") < order[0]
    assert page.rstrip().endswith("</html>")


def test_popout_page_without_markers():
    assert jc.popout_page_html("<html></html>") is None
    assert jc.popout_page_html(None) is None

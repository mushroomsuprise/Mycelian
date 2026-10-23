# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Settings → About: CSS scoping, log details, update status tracking and helpers."""

from __future__ import annotations

import time

from modules.help_system.help_render import promote_color_importance, scope_selectors


def test_scope_selectors_prefixes_and_keeps_special_blocks():
    css = """
/* comment before a rule */
.a, .abt .b, .abt-c { color: red; }
.abt { gap: 1px; }
body:not(.body--dark) .d { color: blue; }
body:not(.body--dark) .abt .e { color: blue; }
details.abt-err > summary { list-style: none; }
@media (max-width: 900px) { .f { display: none; } }
@keyframes spin { to { transform: rotate(360deg); } }
"""
    out = scope_selectors(css, ".abt")
    assert ".abt .a, .abt .b, .abt .abt-c {" in out
    assert ".abt { gap: 1px; }" in out
    assert "body:not(.body--dark) .abt .d" in out
    assert "body:not(.body--dark) .abt .e" in out and ".abt .abt .e" not in out
    assert ".abt details.abt-err > summary" in out
    assert "@media (max-width: 900px) { .abt .f" in out
    assert "@keyframes spin { to { transform" in out
    assert "comment" not in out


def test_scoped_and_promoted_stylesheets_are_well_formed():
    from modules.uiwindows.tabs.about_styles import ABOUT_CSS
    from modules.uiwindows.tabs.statistics_styles import STATISTICS_CSS

    for css, scope in ((ABOUT_CSS, ".abt"), (STATISTICS_CSS, ".mst")):
        out = promote_color_importance(scope_selectors(css, scope))
        assert out.count("{") == out.count("}")
        assert "!important !important" not in out
        assert f"{scope} {scope} " not in out


def test_log_parser_keeps_source_level_and_details(tmp_path):
    from modules.log_parser import get_actionable_errors

    log = tmp_path / "mycelian.log"
    log.write_text(
        "2026-10-22 18:01:02,100 - modules.twitch - ERROR - Boom\n"
        "Traceback (most recent call last):\n  File \"x.py\", line 1\nValueError: nope\n"
        "2026-10-22 18:05:02,100 - modules.twitch - CRITICAL - Boom\n"
    )
    summary = get_actionable_errors(log)
    item = summary.unique_errors[0]
    assert summary.total_count == 2 and item.count == 2
    assert item.source == "modules.twitch"
    assert item.level == "CRITICAL"  # escalates if any occurrence was critical
    assert item.details == []  # details follow the latest occurrence


def test_update_manager_records_check_results():
    from modules.updater import UpdateManager

    mgr = UpdateManager()
    assert mgr.last_check_result is None and not mgr.is_checking
    mgr._record_check("available", {"latest_version": "9.9.9"})
    assert mgr.last_check_result == "available"
    assert mgr.available_update["latest_version"] == "9.9.9"
    assert mgr.last_check_at and time.time() - mgr.last_check_at < 5
    mgr._record_check("up_to_date")
    assert mgr.available_update is None
    mgr._record_check("error", error="offline")
    assert mgr.last_check_error == "offline"


def test_about_helpers():
    from modules.uiwindows.tabs.about_tab import _ago, _friendly_date, _log_ts

    assert _ago(time.time()) == "just now"
    assert _ago(time.time() - 600) == "10m ago"
    assert _ago(None) == ""
    assert _friendly_date("2026-10-20") == "Oct 20, 2026"
    assert _friendly_date("") == "unknown"
    assert _log_ts("2026-10-22 18:01:02,100") is not None
    assert _log_ts("garbage") is None


def test_page_stylesheet_reaches_an_already_open_page(monkeypatch):
    """Tabs built inside an open window must still get their CSS (shared head only
    reaches pages loaded later)."""
    from types import SimpleNamespace

    import nicegui
    from modules.help_system import help_render

    shared, scripts = [], []
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr(nicegui.ui, "add_head_html", lambda code, **kw: shared.append((code, kw)))
    client = SimpleNamespace(has_socket_connection=True, run_javascript=scripts.append)
    monkeypatch.setattr(type(nicegui.context), "client", property(lambda self: client))

    help_render.inject_page_stylesheet("x-style", ".a{color:red}")
    help_render.inject_page_stylesheet("x-style", ".a{color:red}")
    assert len(shared) == 1 and shared[0][1] == {"shared": True}
    assert len(scripts) == 2 and all("x-style" in s and "getElementById" in s for s in scripts)

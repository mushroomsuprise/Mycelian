# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/database_tab.py, part 1: helpers and the Database Explorer.

The explorer runs against a real DatabaseManager on a fresh SQLite file under tmp_path, so
saves and deletes really happen (in that copy only). UI is built with tests/ui_sim.py.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import pytest
from nicegui import ui

from ui_sim import run_ui
from modules import database_manager as dbm
from modules.database_manager import DatabaseConfig
from modules.uiwindows.tabs import database_tab as dt


# ======================================================================= fixtures

@pytest.fixture(autouse=True)
def _tls():
    dbm._write_tls.last_write = None
    dbm._write_tls.quiet = 0
    yield
    dbm._write_tls.last_write = None
    dbm._write_tls.quiet = 0


@pytest.fixture
def notes(monkeypatch):
    calls = []
    monkeypatch.setattr(dt, "notify", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(dbm, "notify", lambda *a, **k: None)
    monkeypatch.setattr(dbm, "notify_critical", lambda *a, **k: None)
    return calls


@pytest.fixture
def sched(monkeypatch):
    """layout_schedule recorder; tests run the callbacks themselves."""
    calls = []

    class Timer:
        def __init__(self):
            self.active = True

    def fake(interval, callback, *, once=False, active=True):
        calls.append((interval, callback, once))
        return Timer()

    monkeypatch.setattr(dt, "layout_schedule", fake)
    return calls


@pytest.fixture
def io(monkeypatch):
    async def io_bound(fn, *a, **k):
        return fn(*a, **k)

    monkeypatch.setattr(dt.run, "io_bound", io_bound)


@pytest.fixture
def mgr(tmp_path, monkeypatch, notes, io):
    m = dbm.DatabaseManager()
    assert m.initialize(DatabaseConfig(database_type="sql", sql_database_path=str(tmp_path / "explorer.db")))
    monkeypatch.setattr(dt, "database_manager", m)
    yield m
    db = m._database
    for c in db._connection_pool:
        c.close()
    db._connection.close()


@pytest.fixture
def viewer(mgr, sched, monkeypatch):
    v = dt.DatabaseViewer()

    async def theme():
        v._cm_theme = "vscodeDark"

    monkeypatch.setattr(v, "_detect_editor_theme", theme)
    return v


def _show(viewer, check):
    async def go(user):
        with user:
            await viewer.show()
        await check(user)

    run_ui(lambda: ui.label("host page"), go)


def _seed(mgr):
    mgr.set_data("AppSettings", {"theme": "dark"})
    mgr.set_data("Alerts/BitAlerts", {"min": 1})
    mgr.set_data("Alerts/SubAlerts", {"min": 2})


async def _until(cond, tries=100):
    import asyncio

    for _ in range(tries):
        if cond():
            return
        await asyncio.sleep(0.01)
    assert cond()


class Ev:
    def __init__(self, args):
        self.args = args


# ======================================================================= helpers

class TestReportedWrite:
    def test_ok(self):
        assert dt._reported_write(lambda: True) == (True, "")

    def test_quiet_while_running(self):
        seen = []
        dt._reported_write(lambda: seen.append(dbm._quiet()) or True)
        assert seen == [True] and not dbm._quiet()

    @pytest.mark.parametrize("problem,expected", [
        (("superseded", ""), (True, "")),
        (("timeout", "no answer after 5 s"), (False, "The database didn't answer in time (no answer after 5 s).")),
        (("down", ""), (False, "The database isn't connected.")),
        (("failed", "disk full"), (False, "disk full")),
        (("failed", ""), (False, "The database rejected the change (see the log).")),
        (None, (False, "The database rejected the change (see the log).")),
    ])
    def test_failures(self, problem, expected):
        def write(path):
            assert path == "p"
            dbm._write_tls.last_write = problem
            return False

        assert dt._reported_write(write, "p") == expected


def test_notify_helpers(notes):
    dt._notify_write_failed("Couldn't save X", "why")
    a, k = notes[-1]
    assert a == ("why",) and k["type"] == "error" and k["title"] == "Couldn't save X" and k["actions"]
    try:
        json.loads('{"a": }')
    except json.JSONDecodeError as e:
        dt._notify_invalid_json(e)
    a, k = notes[-1]
    assert a[0].startswith("Line 1, column 7:") and k["title"] == "That isn't valid JSON"


@pytest.mark.parametrize("size,text", [
    (0, "0 B"), (1023, "1023 B"), (1024, "1.0 KB"), (1536 * 1024, "1.5 MB"), (5 * 1024 ** 3, "5.0 GB"),
    (3 * 1024 ** 4, "3072.0 GB"),
])
def test_format_bytes(size, text):
    assert dt._format_bytes(size) == text


class TestAgo:
    def test_values(self):
        now = datetime.now()
        assert dt._ago(now.isoformat()) == "just now"
        assert dt._ago((now - timedelta(seconds=20)).isoformat()) == "20 s ago"
        assert dt._ago((now - timedelta(minutes=3, seconds=5)).isoformat()) == "3 min ago"
        assert dt._ago((now - timedelta(hours=5, minutes=1)).isoformat()) == "5 h ago"
        old = now - timedelta(days=3)
        assert dt._ago(old.isoformat()) == old.strftime("%Y-%m-%d %H:%M")
        assert dt._ago((now + timedelta(hours=1)).isoformat()) == "just now"

    def test_timezone_and_invalid(self):
        z = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert dt._ago(z) in ("30 s ago", "31 s ago", "29 s ago")
        assert dt._ago("not a date") == ""
        assert dt._ago(None) == ""


def test_live_type(monkeypatch):
    class M:
        cfg = None

        def get_config(self):
            return self.cfg

    m = M()
    monkeypatch.setattr(dt, "database_manager", m)
    assert dt._live_type() == "sql"
    m.cfg = DatabaseConfig(database_type="")
    assert dt._live_type() == "sql"
    m.cfg = DatabaseConfig(database_type="mongodb")
    assert dt._live_type() == "mongodb"


def test_resolved_sql_path(monkeypatch, tmp_path):
    monkeypatch.setenv("MYCELIAN_DATA_DIR", str(tmp_path))
    assert dt._resolved_sql_path("") == ""
    absolute = str(tmp_path / "abs" / "x.db")  # "/abs/x.db" has no drive, so it isn't absolute on Windows
    assert dt._resolved_sql_path(absolute) == absolute
    assert dt._resolved_sql_path("rel.db") == str(tmp_path / "rel.db")


def test_live_where():
    assert dt._live_where(None) == ""
    assert dt._live_where(DatabaseConfig(database_type="sql", sql_database_path="/a/b/c.db")) == "c.db"
    assert dt._live_where(DatabaseConfig(database_type="sql", sql_database_path="")) == ""
    assert dt._live_where(DatabaseConfig(database_type="firebase",
                                         firebase_database_url="https://p.firebaseio.com/")) == "p.firebaseio.com"
    assert dt._live_where(DatabaseConfig(database_type="firebase", firebase_database_url="nohost")) == "nohost"
    assert dt._live_where(DatabaseConfig(database_type="firebase")) == ""
    cfg = DatabaseConfig(database_type="mongodb", mongodb_connection_string="mongodb://u:pw@h", mongodb_database_name="m")
    assert dt._live_where(cfg) == "m"
    assert dt._live_where(DatabaseConfig(database_type="mongodb")) == ""


def test_text_and_value_text():
    assert dt._text(None) == "" and dt._text(5) == "5"
    assert dt._value_text(None) == "{}"
    assert dt._value_text({"a": 1}) == '{\n  "a": 1\n}'
    assert dt._value_text(datetime(2020, 1, 1)) == '"2020-01-01 00:00:00"'


@pytest.mark.parametrize("value,text", [
    ({"a": 1}, "Object · 1 key"), ({}, "Object · 0 keys"), ([1, 2], "Array · 2 items"), ([1], "Array · 1 item"),
    ("x", "Text · 1 character"), ("xy", "Text · 2 characters"), (True, "True / false"), (3, "Number"),
    (2.5, "Number"), (None, "Empty"),
])
def test_describe_value(value, text):
    assert dt._describe_value(value) == text


def test_chips_html():
    html = dt._chips_html({"a": "<b>"})
    assert "Object · 1 key" in html and "B</span>" in html and "<b>" not in html
    circular = {}
    circular["self"] = circular
    html = dt._chips_html(circular)
    assert html.count("dbx-chip") == 1


# ======================================================================= explorer: open / load

class TestExplorerOpen:
    def test_empty_database(self, viewer, notes):
        async def check(user):
            await user.should_see("Database Explorer")
            await user.should_see("Nothing stored yet")
            assert "Local file · explorer.db" in viewer._pill.content
            assert viewer._tree_foot.text == "0 items · 0 folders"
            assert viewer.dialog.value is True

        _show(viewer, check)
        assert notes == []

    def test_with_data(self, viewer, mgr):
        _seed(mgr)

        async def check(user):
            await user.should_see("Pick an item on the left")
            await user.should_see("3 items in 1 folder")
            assert viewer._tree_foot.text == "3 items · 1 folder"
            assert 'data-k="t" data-p="Alerts"' in viewer._tree_html.content
            assert 'data-p="AppSettings"' in viewer._tree_html.content
            assert "BitAlerts" not in viewer._tree_html.content  # folder closed

        _show(viewer, check)

    def test_single_item_wording(self, viewer, mgr):
        mgr.set_data("Only", {})

        async def check(user):
            await user.should_see("1 item in 0 folders")
            assert viewer._tree_foot.text == "1 item · 0 folders"

        _show(viewer, check)

    def test_read_failure(self, viewer, mgr, notes, monkeypatch):
        monkeypatch.setattr(mgr, "get_snapshot_checked", lambda: ({}, "disk I/O error"))

        async def check(user):
            await user.should_see("Couldn't read the database")
            assert "Couldn't read the database" in viewer._tree_html.content
            assert "error" in viewer._pill.content or "is-error" in viewer._pill.content

        _show(viewer, check)
        k = notes[-1][1]
        assert k["title"] == "Couldn't read the database" and k["details"] == "disk I/O error"
        assert viewer._loaded_ok is False

    def test_io_exception(self, viewer, mgr, notes, monkeypatch):
        async def boom(fn, *a, **k):
            raise RuntimeError("worker died")

        monkeypatch.setattr(dt.run, "io_bound", boom)

        async def check(user):
            await user.should_see("Couldn't read the database")

        _show(viewer, check)
        assert notes[-1][1]["details"] == "worker died"

    def test_close(self, viewer):
        async def check(user):
            viewer._close_dialog()
            assert viewer.dialog.value is False

        _show(viewer, check)
        dt.DatabaseViewer()._close_dialog()  # no dialog: no error


class TestDetectTheme:
    def _run(self, coro_fn):
        import asyncio

        asyncio.run(coro_fn())

    def test_from_page(self, monkeypatch):
        v = dt.DatabaseViewer()

        async def js(code, timeout=None):
            return False

        monkeypatch.setattr(dt.ui, "run_javascript", js)
        self._run(v._detect_editor_theme)
        assert v._cm_theme == "githubLight"

    def test_from_theme_manager(self, monkeypatch):
        from modules import theme_manager

        async def js(code, timeout=None):
            raise TimeoutError()

        monkeypatch.setattr(dt.ui, "run_javascript", js)
        monkeypatch.setattr(theme_manager, "get_theme_manager", lambda: type("TM", (), {
            "get_theme": lambda self: type("T", (), {"theme_type": "light"})()})())
        v = dt.DatabaseViewer()
        self._run(v._detect_editor_theme)
        assert v._cm_theme == "githubLight"

    def test_fallback_dark(self, monkeypatch):
        from modules import theme_manager

        async def js(code, timeout=None):
            raise TimeoutError()

        monkeypatch.setattr(dt.ui, "run_javascript", js)
        monkeypatch.setattr(theme_manager, "get_theme_manager", lambda: (_ for _ in ()).throw(RuntimeError()))
        v = dt.DatabaseViewer()
        v._cm_theme = "x"
        self._run(v._detect_editor_theme)
        assert v._cm_theme == "vscodeDark"


# ======================================================================= explorer: tree

class TestTree:
    def test_structure_and_counts(self, viewer):
        viewer.all_paths = ["A", "A/B", "A/B/C", "D/E", "D/F"]
        tree = viewer._tree_structure()
        assert tree["A"]["__is_leaf__"] and tree["A"]["__path__"] == "A"
        assert tree["A"]["__count__"] == 2
        assert tree["D"]["__count__"] == 2 and not tree["D"]["__is_leaf__"]
        assert viewer._folder_count == 3
        assert viewer._tree_structure() is tree  # cached

    def test_click_routes(self, viewer, monkeypatch):
        seen = []
        monkeypatch.setattr(viewer, "_toggle_folder", lambda p: seen.append(("t", p)))
        monkeypatch.setattr(viewer, "_select_path", lambda p: seen.append(("s", p)))
        monkeypatch.setattr(viewer, "_build_tree", lambda *a: seen.append(("build",)))
        viewer._on_tree_click(Ev({"k": "t", "p": "A"}))
        viewer._on_tree_click(Ev({"k": "s", "p": "A/B"}))
        viewer._on_tree_click(Ev({"k": "m", "p": "D"}))
        viewer._on_tree_click(Ev({"k": "x", "p": "D"}))
        viewer._on_tree_click(Ev({"k": "s"}))
        viewer._on_tree_click(Ev(["not", "a", "dict"]))
        assert seen == [("t", "A"), ("s", "A/B"), ("build",)]
        assert viewer._show_all == {"D"}

    def test_build_tree_without_widgets(self, viewer):
        viewer._build_tree("abc")
        assert viewer._filter_text == "abc"

    def test_render_open_folders_selection_and_folder_items(self, viewer, mgr):
        mgr.set_data("A", {"x": 1})
        mgr.set_data("A/B", {"y": 2})
        mgr.set_data("A/C/D", {"z": 3})

        async def check(user):
            viewer._toggle_folder("A")
            html = viewer._tree_html.content
            # "A" is both a folder and an item: the row selects it, the chevron toggles it.
            assert 'data-k="s" data-p="A"' in html and 'data-k="t" data-p="A"' in html
            assert 'aria-expanded="true"' in html and "folder_open" in html
            assert 'data-p="A/B"' in html and 'data-p="A/C/D"' not in html
            viewer._select_path("A/C/D")
            html = viewer._tree_html.content
            assert 'class="dbx-node is-on" role="treeitem" data-k="s" data-p="A/C/D"' in html
            viewer._toggle_folder("A")
            assert 'data-p="A/B"' not in viewer._tree_html.content

        _show(viewer, check)

    def test_folder_limit_and_show_more(self, viewer, mgr, monkeypatch):
        monkeypatch.setattr(dt, "_TREE_FOLDER_LIMIT", 2)
        for i in range(5):
            mgr.set_data(f"F/i{i}", {})

        async def check(user):
            viewer._toggle_folder("F")
            html = viewer._tree_html.content
            assert "Show 3 more…" in html and 'data-k="m" data-p="F"' in html
            viewer._on_tree_click(Ev({"k": "m", "p": "F"}))
            assert "Show 3 more" not in viewer._tree_html.content
            assert 'data-p="F/i4"' in viewer._tree_html.content

        _show(viewer, check)

    def test_row_budget(self, viewer, mgr, monkeypatch):
        monkeypatch.setattr(dt, "_TREE_ROW_LIMIT", 3)
        for i in range(5):
            mgr.set_data(f"p{i}", {})

        async def check(user):
            viewer._build_tree()
            html = viewer._tree_html.content
            assert "Showing the first 3 rows" in html
            assert 'data-p="p3"' not in html

        _show(viewer, check)

    def test_row_budget_inside_folder(self, viewer, mgr, monkeypatch):
        monkeypatch.setattr(dt, "_TREE_ROW_LIMIT", 2)
        for i in range(3):
            mgr.set_data(f"F/c{i}", {})

        async def check(user):
            viewer._toggle_folder("F")
            assert "Showing the first 2 rows" in viewer._tree_html.content

        _show(viewer, check)

    def test_search(self, viewer, mgr, sched):
        _seed(mgr)

        async def check(user):
            viewer._filter_tree(Ev("bit"))
            interval, cb, once = sched[-1]
            assert interval == 0.2 and once
            cb()
            html = viewer._tree_html.content
            assert 'data-p="Alerts/BitAlerts"' in html and "SubAlerts" not in html and "AppSettings" not in html
            # A click while searching folds the open match instead of expanding.
            viewer._toggle_folder("Alerts")
            assert "BitAlerts" not in viewer._tree_html.content
            assert viewer._search_collapsed == {"Alerts"}
            # A new query resets folds.
            viewer._filter_tree(Ev(["alerts"]))
            sched[-1][1]()
            assert viewer._search_collapsed == set()
            assert "SubAlerts" in viewer._tree_html.content
            viewer._filter_tree(Ev(["nomatch<>"]))
            sched[-1][1]()
            assert "Nothing matches “nomatch&lt;&gt;”." in viewer._tree_html.content

        _show(viewer, check)

    def test_search_stale_generation_skipped(self, viewer, sched, monkeypatch):
        built = []
        monkeypatch.setattr(viewer, "_build_tree", lambda f=None: built.append(f))
        viewer._filter_tree(Ev("a"))
        first = sched[-1][1]
        viewer._filter_tree(Ev(None))
        first()
        assert built == []
        sched[-1][1]()
        assert built == [""]
        viewer._filter_tree(Ev([]))
        sched[-1][1]()
        assert built == ["", ""]


# ======================================================================= explorer: items and editing

class TestItems:
    def test_select_shows_editor_and_breadcrumb(self, viewer, mgr):
        _seed(mgr)

        async def check(user):
            viewer._select_path("Alerts/BitAlerts")
            await user.should_see("Save item")
            assert viewer._json_editor.value == '{\n  "min": 1\n}'
            assert "Object · 1 key" in viewer._meta_html.content
            assert viewer._path_label.text == "Alerts/BitAlerts"
            assert "Valid JSON" in viewer._valid_html.content
            assert viewer._save_btn.enabled is False
            assert viewer._expanded == {"Alerts"}
            labels = [el.text for el in viewer._breadcrumb_container.descendants() if hasattr(el, "text")]
            assert labels[:4] == ["Root", "/", "Alerts", "/"] and labels[-1] == "BitAlerts"
            # Breadcrumb to the root goes back to the welcome view.
            viewer._select_path("")
            await user.should_see("Pick an item on the left")
            assert viewer._json_editor is None

        _show(viewer, check)

    def test_breadcrumb_clicks(self, viewer, mgr):
        _seed(mgr)

        async def check(user):
            viewer._select_path("Alerts/BitAlerts")
            crumbs = {el.text: el for el in viewer._breadcrumb_container.descendants() if isinstance(el, ui.label)}
            crumbs["Alerts"].mark("crumb-alerts")
            user.find(marker="crumb-alerts").click()
            assert viewer.current_path == "Alerts"
            crumbs = {el.text: el for el in viewer._breadcrumb_container.descendants() if isinstance(el, ui.label)}
            crumbs["Root"].mark("crumb-root")
            user.find(marker="crumb-root").click()
            assert viewer.current_path == ""

        _show(viewer, check)

    def test_editor_validity_and_dirty(self, viewer, mgr, sched):
        mgr.set_data("A", {"x": 1})

        async def check(user):
            viewer._select_path("A")
            viewer._json_editor.value = '{"x": '
            interval, cb, once = sched[-1]
            assert interval == 0.25 and once
            cb()
            assert "Invalid JSON" in viewer._valid_html.content
            assert "is-bad" in viewer._valid_html.classes
            assert viewer._save_btn.enabled is False
            assert "Unsaved changes" in viewer._dirty_html.content
            viewer._json_editor.value = '{"x": 2}'
            stale = sched[-2][1]
            stale()  # an older change does not update the state
            assert "Invalid JSON" in viewer._valid_html.content
            sched[-1][1]()
            assert "Valid JSON" in viewer._valid_html.content and "is-bad" not in viewer._valid_html.classes
            assert viewer._save_btn.enabled is True

        _show(viewer, check)

    def test_format_and_reset(self, viewer, mgr, notes):
        mgr.set_data("A", {"x": 1})

        async def check(user):
            viewer._select_path("A")
            viewer._json_editor.value = '{"x":1,"y":[1,2]}'
            viewer._format_json()
            assert viewer._json_editor.value == '{\n  "x": 1,\n  "y": [\n    1,\n    2\n  ]\n}'
            viewer._json_editor.value = "{bad"
            viewer._format_json()
            assert notes[-1][1]["title"] == "That isn't valid JSON"
            assert viewer._json_editor.value == "{bad"
            user.find("Undo changes").click()
            assert viewer._json_editor.value == '{\n  "x": 1\n}'

        _show(viewer, check)

    def test_no_editor_noops(self, viewer):
        import asyncio

        viewer._format_json()
        viewer._reset_editor()
        viewer._update_editor_state()
        viewer._load_path_content()
        viewer._show_welcome_content()
        viewer._update_breadcrumb()
        viewer._update_pill()
        asyncio.run(viewer._save_current_path())
        asyncio.run(viewer._on_save_key())

    def test_save_sql(self, viewer, mgr, notes):
        mgr.set_data("A", {"x": 1})

        async def check(user):
            viewer._select_path("A")
            viewer._json_editor.value = '{"x": 5}'
            await viewer._save_current_path()
            assert mgr.get_data("A") == {"x": 5}
            assert viewer._original_text == '{"x": 5}'
            assert viewer._save_btn.enabled is False
            assert "Object · 1 key" in viewer._meta_html.content

        _show(viewer, check)
        assert notes[-1][1]["title"] == "Saved A" and notes[-1][1]["type"] == "success"

    def test_save_key(self, viewer, mgr):
        mgr.set_data("A", {"x": 1})

        async def check(user):
            viewer._select_path("A")
            await viewer._on_save_key()  # unchanged: nothing written
            assert mgr.get_data("A") == {"x": 1}
            viewer._json_editor.value = '{"x": 9}'
            await viewer._on_save_key()
            assert mgr.get_data("A") == {"x": 9}

        _show(viewer, check)

    def test_save_invalid_json(self, viewer, mgr, notes):
        mgr.set_data("A", {"x": 1})

        async def check(user):
            viewer._select_path("A")
            viewer._json_editor.value = "{nope"
            await viewer._save_current_path()
            assert mgr.get_data("A") == {"x": 1}

        _show(viewer, check)
        assert notes[-1][1]["title"] == "That isn't valid JSON"

    def test_save_failure(self, viewer, mgr, notes, monkeypatch):
        mgr.set_data("A", {"x": 1})
        monkeypatch.setattr(mgr._database, "set_data", lambda p, d: False)

        async def check(user):
            viewer._select_path("A")
            viewer._json_editor.value = '{"x": 2}'
            await viewer._save_current_path()
            assert viewer._original_text != '{"x": 2}'

        _show(viewer, check)
        k = notes[-1][1]
        assert k["title"] == "Couldn't save A" and k["type"] == "error"
        assert notes[-1][0][0] == "The database rejected the change (see the log)."

    def test_save_firebase_updates_snapshot(self, viewer, mgr, monkeypatch):
        mgr.set_data("A", {"x": 1})

        async def check(user):
            viewer._select_path("A")
            mgr._config.database_type = "firebase"
            loads = []
            monkeypatch.setattr(viewer, "_load_snapshot", lambda: loads.append(1))
            viewer._json_editor.value = '{"x": 3}'
            await viewer._save_current_path()
            assert viewer.snapshot["A"] == {"x": 3} and loads == []
            mgr._config.database_type = "sql"

        _show(viewer, check)

    def test_get_data_from_snapshot(self, viewer, mgr):
        mgr.set_data("A/B", {"v": 1})
        viewer.pending_changes["P"] = {"pending": True}
        assert viewer._get_data_from_snapshot("P") == {"pending": True}
        assert viewer._get_data_from_snapshot("A/B") == {"v": 1}
        mgr._config.database_type = "firebase"
        try:
            viewer.snapshot = {"X": {"Y": {"z": 1}}}
            assert viewer._get_data_from_snapshot("X/Y") == {"z": 1}
            calls = []
            mgr.get_data = lambda p: calls.append(p) or {"from": "db"}
            assert viewer._get_data_from_snapshot("X/missing") == {"from": "db"}
            assert calls == ["X/missing"]
        finally:
            mgr._config.database_type = "sql"

    def test_set_data_in_snapshot(self, viewer):
        viewer.snapshot = {"A": {"keep": 1}}
        viewer._set_data_in_snapshot("A/B/C", 5)
        viewer._set_data_in_snapshot("Top", [1])
        assert viewer.snapshot == {"A": {"keep": 1, "B": {"C": 5}}, "Top": [1]}

    def test_refresh(self, viewer, mgr):
        mgr.set_data("A", {"x": 1})

        async def check(user):
            await viewer._refresh_snapshot()
            await user.should_see("Pick an item on the left")
            viewer._select_path("A")
            mgr.set_data("A", {"x": 2})
            await viewer._refresh_snapshot()
            assert viewer._json_editor.value == '{\n  "x": 2\n}'

        _show(viewer, check)


class TestDeleteCreate:
    def test_delete_flow(self, viewer, mgr, notes):
        _seed(mgr)

        async def check(user):
            viewer._select_path("AppSettings")
            user.find("Delete").click()
            await user.should_see("Delete “AppSettings”?")
            await user.should_see("This can't be undone.", )
            dialogs = [el for el in user.current_layout.descendants() if isinstance(el, ui.dialog)]
            confirm = dialogs[-1]
            await viewer._execute_delete("AppSettings", confirm)
            assert mgr.get_data("AppSettings") == {}
            assert viewer.current_path == "" and confirm.value is False
            await user.should_see("2 items in 1 folder")

        _show(viewer, check)
        assert notes[-1][1]["title"] == "Deleted AppSettings"

    def test_delete_cancel_button(self, viewer, mgr):
        mgr.set_data("A", {})

        async def check(user):
            with user:
                viewer._confirm_delete("A")
            await user.should_see("Delete “A”?")
            user.find(kind=ui.button, content="Cancel").click()
            assert mgr.get_data("A") == {}

        _show(viewer, check)

    def test_delete_failure(self, viewer, mgr, notes, monkeypatch):
        mgr.set_data("A", {})
        monkeypatch.setattr(mgr._database, "delete_data", lambda p: False)

        class D:
            closed = False

            def close(self):
                self.closed = True

        async def check(user):
            d = D()
            await viewer._execute_delete("A", d)
            assert d.closed and mgr.get_data("A") == {}

        _show(viewer, check)
        assert notes[-1][1]["title"] == "Couldn't delete A"

    def test_delete_removes_children_as_promised(self, viewer, mgr):
        mgr.set_data("A", {"x": 1})
        mgr.set_data("A/B", {"y": 2})

        async def check(user):
            with user:
                viewer._confirm_delete("A")
            await user.should_see("A and everything in it is removed from the database.")
            await viewer._execute_delete("A", viewer)

        viewer.close = lambda: None
        _show(viewer, check)
        assert mgr.get_all_paths() == []

    def test_delete_with_children_keeps_siblings(self, mgr):
        for p in ("A", "A/B", "A/B/C", "AB", "B"):
            mgr.set_data(p, {"v": p})
        assert dt._delete_with_children("A") is True
        assert mgr.get_all_paths() == ["AB", "B"]

    def test_delete_with_children_stops_on_failure(self, mgr, monkeypatch):
        mgr.set_data("A", {"x": 1})
        mgr.set_data("A/B", {"y": 2})
        monkeypatch.setattr(mgr._database, "delete_data", lambda p: False)
        assert dt._delete_with_children("A") is False

    def test_delete_with_children_firebase_deletes_node_only(self, monkeypatch):
        deleted = []
        fake = SimpleNamespace(get_config=lambda: SimpleNamespace(database_type="firebase"),
                               get_all_paths=lambda: pytest.fail("Firebase removes children itself"),
                               delete_data=lambda p: deleted.append(p) or True)
        monkeypatch.setattr(dt, "database_manager", fake)
        assert dt._delete_with_children("A") is True
        assert deleted == ["A"]

    def test_create_dialog(self, viewer, mgr, notes):
        async def check(user):
            user.find("New item").click()
            await user.should_see("Creates a new path. Folders are made automatically from the slashes.")
            inputs = [el for el in user.current_layout.descendants()
                      if isinstance(el, ui.input) and el.props.get("placeholder") == "e.g. Settings/MyData"]
            inputs[0].value = "/Settings/Mine"
            user.find(kind=ui.button, content="Create").click()
            await _until(lambda: viewer.current_path == "Settings/Mine")
            assert mgr.get_data("Settings/Mine") == {}
            assert viewer.current_path == "Settings/Mine"

        _show(viewer, check)
        assert notes[-1][1]["title"] == "Created Settings/Mine"

    def test_create_validation(self, viewer, mgr, notes):
        class D:
            closed = False

            def close(self):
                self.closed = True

        async def check(user):
            d = D()
            await viewer._execute_create("   ", "{}", d)
            assert notes[-1][1]["title"] == "Enter a path"
            await viewer._execute_create("X", "{bad", d)
            assert notes[-1][1]["title"] == "That isn't valid JSON"
            assert mgr.get_all_paths() == [] and not d.closed
            await viewer._execute_create(" Y ", "   ", d)
            assert mgr.get_data("Y") == {} and d.closed
            await viewer._execute_create("Z", '[1, 2]', d)
            assert mgr.get_data("Z") == [1, 2]

        _show(viewer, check)

    def test_create_failure(self, viewer, mgr, notes, monkeypatch):
        monkeypatch.setattr(mgr._database, "set_data", lambda p, d: False)

        class D:
            closed = False

            def close(self):
                self.closed = True

        async def check(user):
            d = D()
            await viewer._execute_create("X", "{}", d)
            assert not d.closed

        _show(viewer, check)
        assert notes[-1][1]["title"] == "Couldn't create X"

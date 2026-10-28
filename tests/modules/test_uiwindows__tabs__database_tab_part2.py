# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/database_tab.py, part 2: the Database settings tab.

The database manager and config manager are fakes (nothing connects, nothing writes
config.json); migration runs its worker inline.
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from pathlib import Path

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


class FakeMgr:
    def __init__(self, live_path):
        self.cfg = DatabaseConfig(database_type="sql", sql_database_path=live_path)
        self.status = {"status": "Connected", "is_connected": True, "database_type": "SQLite"}
        self.status_exc = None
        self.last_init_error = ""
        self.available = ["sql", "firebase", "mongodb"]
        self.available_exc = None
        self.test_result = True
        self.test_exc = None
        self.migrate_result = True
        self.migrate_exc = None
        self.migrations = []
        self.last_migration_result = {}
        self.update_result = True
        self.updates = []
        self.inits = []

    def get_config(self):
        return self.cfg

    def get_connection_status(self):
        if self.status_exc:
            raise self.status_exc
        return None if self.status is None else dict(self.status)

    def test_connection(self):
        if self.test_exc:
            raise self.test_exc
        return self.test_result

    def get_available_databases(self):
        if self.available_exc:
            raise self.available_exc
        return list(self.available)

    def migrate_data(self, src, dst):
        if self.migrate_exc:
            raise self.migrate_exc
        self.migrations.append((src, dst))
        return self.migrate_result

    def update_config(self, **kw):
        self.updates.append((kw, dbm._quiet()))
        if self.update_result:
            for k, v in kw.items():
                setattr(self.cfg, k, v)
        return self.update_result

    def initialize(self, cfg):
        self.inits.append((cfg, dbm._quiet()))
        return True


class FakeConfig:
    def __init__(self, data):
        self.data = dict(data)
        self.updates = []
        self.ok = True

    def get_database_config(self):
        return dict(self.data)

    def update_database_config(self, **kw):
        self.updates.append(kw)
        if self.ok:
            self.data.update(kw)
        return self.ok


class Work:
    def __init__(self, title, **kw):
        self.title = title
        self.kw = kw
        self.events = []

    def done(self, *a, **k):
        self.events.append(("done", a, k))

    def fail(self, *a, **k):
        self.events.append(("fail", a, k))

    def update(self, *a, **k):
        self.events.append(("update", a, k))


class Timer:
    def __init__(self):
        self.active = True


@pytest.fixture
def env(tmp_path, monkeypatch):
    live = tmp_path / "live.db"
    live.write_bytes(b"x" * 2048)
    mgr = FakeMgr(str(live))
    cfg = FakeConfig({"database_type": "sql", "sql_database_path": str(live), "connection_timeout": 30,
                      "retry_attempts": 3, "firebase_service_account_path": "key.json",
                      "firebase_database_url": "https://p.firebaseio.com/",
                      "mongodb_connection_string": "mongodb://u:pw@h/", "mongodb_database_name": "mdb"})
    notes, works, sched = [], [], []
    monkeypatch.setattr(dt, "database_manager", mgr)
    monkeypatch.setattr(dt, "config_manager", cfg)
    monkeypatch.setattr(dt, "notify", lambda *a, **k: notes.append((a, k)))

    def progress(title, **kw):
        w = Work(title, **kw)
        works.append(w)
        return w

    monkeypatch.setattr(dt, "notify_progress", progress)

    def schedule(interval, callback, *, once=False, active=True):
        sched.append((interval, callback, once))
        return Timer()

    monkeypatch.setattr(dt, "layout_schedule", schedule)

    async def io_bound(fn, *a, **k):
        return fn(*a, **k)

    monkeypatch.setattr(dt.run, "io_bound", io_bound)
    dbm._write_tls.quiet = 0
    return types.SimpleNamespace(mgr=mgr, cfg=cfg, notes=notes, works=works, sched=sched, live=live)


@pytest.fixture
def tab(env):
    t = dt.DatabaseTab()
    t._load_from_config()
    return t


def _built(tab, check):
    run_ui(lambda: tab.build(ui.column()), check)


# ======================================================================= build / layout

class TestBuild:
    def test_layout(self, env):
        tab = dt.DatabaseTab()

        async def check(user):
            for text in ("Where Mycelian keeps your settings, alerts and history", "Connection", "Storage",
                         "Database file", "Connection settings", "Timeout", "Retry attempts", "Your data",
                         "Database Explorer", "Move my data", "Good to know"):
                await user.should_see(text)
            assert tab.buffer.sql_database_path == str(env.live)
            assert tab._type_boxes["sql"].visible and not tab._type_boxes["firebase"].visible
            assert not tab._type_boxes["mongodb"].visible
            assert not tab._switch_hint.visible
            card, inner = tab._choices["sql"]
            assert "is-on" in card.classes and "In use" in inner.content
            assert "is-on" not in tab._choices["firebase"][0].classes
            assert card.props["aria-checked"] == "true"
            assert "Checking" in tab._page._pills.content
            assert "Local file (SQLite)" in tab._card._who.content
            assert "live.db" in tab._card._meta.content and "2.0 KB" in tab._card._meta.content
            assert tab.ui_elements["mongodb_connection_string"].props.get("type") == "password"

        _built(tab, check)
        interval, cb, once = env.sched[-1]
        assert interval == 3.0 and not once and cb == tab._refresh_status_async
        assert tab._status_timer.active

    def test_choose_type_by_click(self, env):
        tab = dt.DatabaseTab()

        async def check(user):
            tab._choices["firebase"][0].mark("choice-firebase")
            user.find(marker="choice-firebase").click()
            assert tab.buffer.database_type == "firebase" and tab.dirty
            assert tab._type_boxes["firebase"].visible and not tab._type_boxes["sql"].visible
            assert tab._switch_hint.visible
            assert "is-on" in tab._choices["firebase"][0].classes
            assert "is-on" not in tab._choices["sql"][0].classes
            assert "In use" in tab._choices["sql"][1].content
            await user.should_see("Realtime Database URL")

        _built(tab, check)

    def test_field_changes(self, env):
        tab = dt.DatabaseTab()

        async def check(user):
            el = tab.ui_elements
            el["sql_database_path"].value = ""
            assert tab.buffer.sql_database_path == "mycelian.db" and tab.dirty
            el["firebase_service_account_path"].value = "k2.json"
            el["firebase_database_url"].value = "https://q.firebaseio.com/"
            el["mongodb_connection_string"].value = "mongodb://x/"
            el["mongodb_database_name"].value = ""
            el["connection_timeout"].value = 60
            el["retry_attempts"].value = None
            b = tab.buffer
            assert (b.firebase_service_account_path, b.firebase_database_url) == ("k2.json", "https://q.firebaseio.com/")
            assert (b.mongodb_connection_string, b.mongodb_database_name) == ("mongodb://x/", "mycelian")
            assert b.connection_timeout == 60 and b.retry_attempts == 3

        _built(tab, check)

    def test_set_unchanged_not_dirty(self, tab):
        tab._set("retry_attempts", 3)
        assert tab.dirty is False
        tab._set("retry_attempts", 4)
        assert tab.dirty is True

    def test_browse_buttons(self, env, monkeypatch, tmp_path):
        from modules.uiwindows import file_picker

        picks = []
        monkeypatch.setattr(file_picker, "open_file_picker", lambda **kw: picks.append(kw))
        monkeypatch.setenv("MYCELIAN_DATA_DIR", str(tmp_path))
        tab = dt.DatabaseTab()

        async def check(user):
            tab._type_boxes["firebase"].set_visibility(True)  # hidden fields can't be clicked
            for field_name in ("sql_database_path", "firebase_service_account_path"):
                btn = tab.ui_elements[field_name].slots["after"].children[0]
                btn.mark(f"browse-{field_name}")
                user.find(marker=f"browse-{field_name}").click()
            sql_pick, fb_pick = picks
            assert sql_pick["start"] == str(env.live) and sql_pick["extensions"] == (".db", ".sqlite", ".sqlite3")
            assert sql_pick["scope"] == "system"
            assert fb_pick["start"] == str(tmp_path / "key.json") and fb_pick["extensions"] == (".json",)
            sql_pick["on_pick"](types.SimpleNamespace(path=tmp_path / "picked.db"))
            assert tab.ui_elements["sql_database_path"].value == str(tmp_path / "picked.db")
            assert tab.buffer.sql_database_path == str(tmp_path / "picked.db") and tab.dirty
            tab.ui_elements["firebase_service_account_path"].value = ""
            picks.clear()
            user.find(marker="browse-firebase_service_account_path").click()
            assert picks[0]["start"] is None

        _built(tab, check)

    def test_on_enter_exit(self, env):
        tab = dt.DatabaseTab()
        tab.on_enter()  # nothing built yet
        assert env.sched[-1][1] == tab._refresh_status_async and env.sched[-1][2]
        tab.on_exit()

        async def check(user):
            tab.on_exit()
            assert tab._status_timer.active is False and tab._page._timer.active is False
            tab.on_enter()
            assert tab._status_timer.active is True and tab._page._timer.active is True

        _built(tab, check)


# ======================================================================= status

class TestStatus:
    def test_connected_and_errors(self, env):
        tab = dt.DatabaseTab()

        async def check(user):
            tab._apply_status_info({"status": "Connected", "is_connected": True, "last_check": "bad"})
            assert "Connected" in tab._card._who.content and "last checked" not in tab._card._who.content
            assert "Connected · Local file" in tab._page._pills.content
            from datetime import datetime

            tab._apply_status_info({"is_connected": False, "status": "Configuration issues: x",
                                    "last_check": datetime.now().isoformat()})
            assert "Not set up correctly · last checked just now" in tab._card._who.content
            assert "Can&#x27;t connect · Local file" in tab._page._pills.content or \
                "Can't connect · Local file" in tab._page._pills.content
            tab._apply_status_info({"is_connected": False, "status": "x" * 120})
            assert ("x" * 89 + "…") in tab._card._who.content
            tab._apply_status_info({"is_connected": False})
            assert "Not connected" in tab._card._who.content

        _built(tab, check)

    def test_firebase_issues(self, env):
        tab = dt.DatabaseTab()
        env.mgr.cfg = DatabaseConfig(database_type="firebase", firebase_database_url="https://p.firebaseio.com/")

        async def check(user):
            issues = {"database_type": "Firebase", "is_connected": False,
                      "status": "Configuration issues: a", "config_issues": ["Key missing", "<b>URL</b>"]}
            tab._apply_status_info(issues)
            assert tab._firebase_issues.visible
            assert "Key missing; &lt;b&gt;URL&lt;/b&gt;." in tab._firebase_issues.content
            assert len(env.notes) == 1
            k = env.notes[0][1]
            assert k["title"] == "Firebase isn't set up correctly" and k["dedupe_key"].endswith("Key missing|<b>URL</b>")
            tab._apply_status_info(issues)
            assert len(env.notes) == 1  # same issues: not repeated
            tab._apply_status_info(dict(issues, config_issues=["Other"]))
            assert len(env.notes) == 2
            tab._apply_status_info(dict(issues, config_issues=[]))
            assert tab._last_firebase_issues_signature == "" and not tab._firebase_issues.visible
            tab._apply_status_info({"database_type": "Firebase", "is_connected": True})
            assert tab._last_firebase_issues_signature is None
            assert "Realtime Database" in tab._card._meta.content
            assert "p.firebaseio.com" in tab._card._meta.content

        _built(tab, check)

    def test_refresh_status_sync(self, tab, env):
        tab._refresh_status()
        assert tab._last_status["status"] == "Connected"
        env.mgr.status_exc = RuntimeError("probe")
        label = types.SimpleNamespace(text=None, set_text=lambda t: setattr(label, "text", t))
        tab.ui_elements["status_label"] = label
        tab._refresh_status()
        assert tab._last_status == {"status": "Couldn't check the connection", "is_connected": False}
        assert label.text == "Status Error"

    def test_refresh_status_async(self, tab, env):
        asyncio.run(tab._refresh_status_async())
        assert tab._last_status["is_connected"] is True and tab._status_refresh_inflight is False
        tab._status_refresh_inflight = True
        env.mgr.status = {"status": "Other", "is_connected": False}
        asyncio.run(tab._refresh_status_async())
        assert tab._last_status["is_connected"] is True  # skipped while in flight
        asyncio.run(tab._refresh_status_async(force=True))
        assert tab._last_status["status"] == "Other"
        env.mgr.status = None
        asyncio.run(tab._refresh_status_async())
        assert tab._last_status["status"] == "Other" and not tab._status_refresh_inflight
        env.mgr.status_exc = RuntimeError("x")
        asyncio.run(tab._refresh_status_async())
        assert tab._last_status["status"] == "Couldn't check the connection"

    def test_meta_rows(self, env, tmp_path, monkeypatch):
        monkeypatch.setenv("MYCELIAN_DATA_DIR", str(tmp_path))
        (tmp_path / "rel.db").write_bytes(b"1234")
        assert dt.DatabaseTab._meta_rows(None, "sql") == []
        rows = dt.DatabaseTab._meta_rows(DatabaseConfig(sql_database_path="rel.db"), "sql")
        assert rows == [("Type", "SQLite", "", ""), ("Location", "rel.db", "data folder", ""), ("Size", "4 B", "", "")]
        rows = dt.DatabaseTab._meta_rows(DatabaseConfig(sql_database_path=str(tmp_path / "none.db")), "sql")
        assert rows[1] == ("Location", "none.db", str(tmp_path), "") and len(rows) == 2
        rows = dt.DatabaseTab._meta_rows(DatabaseConfig(sql_database_path=""), "sql")
        assert rows[1][1] == "—"
        assert dt.DatabaseTab._meta_rows(DatabaseConfig(database_type="firebase"), "firebase")[1] == (
            "Location", "—", "", "")
        mongo = DatabaseConfig(mongodb_connection_string="mongodb://u:secret@h", mongodb_database_name="")
        rows = dt.DatabaseTab._meta_rows(mongo, "mongodb")
        assert rows == [("Type", "MongoDB", "", ""), ("Location", "—", "database name", "")]
        assert "secret" not in repr(rows)


class TestTestConnection:
    def test_ok(self, tab, env):
        asyncio.run(tab._test_connection())
        w = env.works[-1]
        assert w.title == "Testing the local database connection"
        assert w.events[0] == ("done", ("Connected to the local database",), {})
        assert tab._last_status["is_connected"]

    def test_failure(self, tab, env):
        env.mgr.test_result = False
        env.mgr.last_init_error = "refused"
        asyncio.run(tab._test_connection())
        kind, a, k = env.works[-1].events[0]
        assert kind == "fail" and a == ("Couldn't connect to the local database",)
        assert k["details"] == "refused" and k["actions"]

    def test_failure_without_reason(self, tab, env):
        env.mgr.test_result = False
        asyncio.run(tab._test_connection())
        assert env.works[-1].events[0][2]["details"] is None

    def test_exception(self, tab, env):
        env.mgr.test_exc = RuntimeError("boom")
        asyncio.run(tab._test_connection())
        kind, a, k = env.works[-1].events[0]
        assert kind == "fail" and a == ("Couldn't test the local database connection",) and k["details"] == "boom"


def test_event_select_value(tab):
    S = types.SimpleNamespace
    assert tab._event_select_value(S(value="firebase")) == "firebase"
    assert tab._event_select_value(S(value=None, args=["mongodb"])) == "mongodb"
    assert tab._event_select_value(S(value=None, args="sql")) == "sql"
    assert tab._event_select_value(S(value=None, args=[])) is None
    assert tab._event_select_value(S(value=None, args=None)) is None
    assert tab._event_select_value(S()) is None


@pytest.mark.parametrize("path,ok", [
    ("Settings/Mine", True), ("", False), ("  ", False), ("/a", False), ("a/", False), ("a//b", False),
    ("a/ /b", False), ("category_x", False), ("x/category_y", True),
])
def test_validate_path_name(tab, path, ok):
    assert tab._validate_path_name(path) is ok


@pytest.mark.parametrize("value,ok", [("", True), ("  ", True), ('{"a": 1}', True), ("[1]", True), ("{bad", False)])
def test_validate_json(tab, value, ok):
    assert tab._validate_json(value) is ok


# ======================================================================= choices helpers

class TestChoices:
    def test_choice_html(self, tab):
        html = tab._choice_html("firebase", "<sub>", "firebase")
        assert "In use" in html and "Firebase" in html and "&lt;sub&gt;" in html
        assert "In use" not in tab._choice_html("sql", "x", "firebase")

    def test_sync_noops(self, env):
        t = dt.DatabaseTab()
        t._sync_choices()  # no buffer
        t._update_config_visibility()  # no buffer
        t._load_from_config()
        t._sync_choices()  # no choices built
        assert t._choice_state is None

    def test_sync_skips_unchanged_state(self, env):
        tab = dt.DatabaseTab()

        async def check(user):
            state = tab._choice_state
            card, inner = tab._choices["sql"]
            inner.set_content("tampered")
            tab._sync_choices()
            assert inner.content == "tampered" and tab._choice_state == state

        _built(tab, check)

    def test_keyboard_handler_registered(self, env):
        tab = dt.DatabaseTab()

        async def check(user):
            card = tab._choices["mongodb"][0]
            listeners = [l for l in card._event_listeners.values() if l.type == "keydown"]
            assert listeners and listeners[0].js_handler == dt._ACTIVATE_KEY_JS
            listeners[0].handler(types.SimpleNamespace(args={}))
            assert tab.buffer.database_type == "mongodb"

        _built(tab, check)


# ======================================================================= migration dialog

class TestMigrationDialog:
    def test_dialog_and_start(self, env, monkeypatch):
        tab = dt.DatabaseTab()
        started = []

        async def check(user):
            monkeypatch.setattr(tab, "_start_migration", lambda s, t, d: started.append((s, t, d)))
            with user:
                tab._show_migration_dialog()
            await user.should_see("Copies every stored item to another database, then switches to it.")
            selects = [el for el in user.current_layout.descendants() if isinstance(el, ui.select)]
            src, dst = selects[-2], selects[-1]
            assert src.value == "sql" and dst.value == "firebase"
            assert src.options["sql"] == "Local file (in use)" and dst.options["mongodb"] == "MongoDB"
            dst.value = "mongodb"
            user.find(kind=ui.button, content="Start moving").click()
            await asyncio.sleep(0)
            assert started and started[0][:2] == ("sql", "mongodb")
            src.value = None
            dst.value = None
            user.find(kind=ui.button, content="Start moving").click()
            assert started[-1][:2] == ("sql", "sql")

        _built(tab, check)

    def test_single_database_and_live_not_available(self, env):
        tab = dt.DatabaseTab()
        env.mgr.available = ["sql"]
        env.mgr.cfg.database_type = "firebase"

        async def check(user):
            with user:
                tab._show_migration_dialog()
            selects = [el for el in user.current_layout.descendants() if isinstance(el, ui.select)]
            assert selects[-2].value == "sql" and selects[-1].value == "sql"

        _built(tab, check)

    def test_no_databases(self, tab, env):
        env.mgr.available = []
        tab._show_migration_dialog()
        assert env.notes[-1][1]["title"] == "No other database is available to migrate to"

    def test_error(self, tab, env):
        env.mgr.available_exc = RuntimeError("probe failed")
        tab._show_migration_dialog()
        k = env.notes[-1][1]
        assert k["title"] == "Couldn't open the migration window" and k["details"] == "probe failed"


# ======================================================================= migration run

class _InlineThreading:
    def __init__(self):
        self.started = []

    def Thread(self, target=None, daemon=None):
        outer = self

        class T:
            def start(self_inner):
                outer.started.append(target)
                target()

        return T()


class Dialog:
    def __init__(self, exc=None):
        self.closed = False
        self.exc = exc

    def close(self):
        if self.exc:
            raise self.exc
        self.closed = True


@pytest.fixture
def inline(monkeypatch):
    t = _InlineThreading()
    monkeypatch.setattr(dt, "threading", t)
    return t


def _poll(env):
    """Run the scheduled poll callbacks until none is pending."""
    for _ in range(10):
        pending = [c for c in env.sched if c[0] == 0.5]
        if not pending:
            return
        env.sched.clear()
        pending[-1][1]()


class TestStartMigration:
    def test_same_database(self, tab, env, inline):
        d = Dialog()
        tab._start_migration("sql", "sql", d)
        assert env.notes[-1][1]["title"] == "Pick two different databases" and not d.closed
        assert inline.started == []

    def test_success_from_live_sql_to_firebase(self, tab, env, inline):
        el = types.SimpleNamespace(value="old")
        tab.ui_elements = {"database_type": el, "other": types.SimpleNamespace()}
        tab.dirty = True
        d = Dialog()
        tab._start_migration("sql", "firebase", d)
        assert d.closed
        w = env.works[-1]
        assert w.title == "Copying your data to the Firebase database"
        src, dst = env.mgr.migrations[0]
        assert src.database_type == "sql" and src.sql_database_path == str(env.live) and src is not env.mgr.cfg
        assert dst.database_type == "firebase" and dst.firebase_service_account_path == "key.json"
        kw, quiet = env.mgr.updates[0]
        assert kw["database_type"] == "firebase" and kw["streamer_name"] == "mycelian" and quiet
        assert env.cfg.updates[-1]["database_type"] == "firebase"
        _poll(env)
        assert w.events[-1] == ("done", ("Moved your data to the Firebase database", "Mycelian now uses it."), {})
        assert tab.buffer.database_type == "firebase" and el.value == "firebase" and tab.dirty is False

    def test_success_config_write_fails(self, tab, env, inline):
        env.cfg.ok = False
        tab._start_migration("sql", "mongodb", Dialog())
        _poll(env)
        assert env.works[-1].events[-1][0] == "done"
        dst = env.mgr.migrations[0][1]
        assert dst.database_type == "mongodb" and dst.mongodb_database_name == "mdb"

    @pytest.mark.parametrize("source,attr,value", [
        ("firebase", "firebase_database_url", "https://p.firebaseio.com/"),
        ("mongodb", "mongodb_connection_string", "mongodb://u:pw@h/"),
    ])
    def test_sources_from_form(self, tab, env, inline, source, attr, value):
        tab._start_migration(source, "sql", Dialog())
        src, dst = env.mgr.migrations[0]
        assert src.database_type == source and getattr(src, attr) == value
        assert dst.database_type == "sql" and dst.sql_database_path == str(env.live)

    def test_sql_source_when_live_is_remote(self, tab, env, inline):
        env.mgr.cfg = DatabaseConfig(database_type="firebase")
        tab._start_migration("sql", "firebase", Dialog())
        src, dst = env.mgr.migrations[0]
        assert src.database_type == "sql" and src.sql_database_path == str(env.live)

    def test_switch_fails_restores(self, tab, env, inline):
        env.mgr.update_result = False
        env.mgr.last_init_error = "auth failed"
        tab._start_migration("sql", "firebase", Dialog())
        assert tab.buffer.database_type == "sql"
        cfg, quiet = env.mgr.inits[0]
        assert cfg.database_type == "sql" and quiet
        _poll(env)
        kind, a, k = env.works[-1].events[-1]
        assert kind == "fail" and a[0] == "Copied your data, but couldn't switch to the Firebase database"
        assert a[1] == "Mycelian is still using the local database." and k["details"] == "auth failed"
        assert k["actions"]

    @pytest.mark.parametrize("stage,which", [("source", "local"), ("target", "MongoDB")])
    def test_open_failures(self, tab, env, inline, stage, which):
        env.mgr.migrate_result = False
        env.mgr.last_migration_result = {"ok": False, "stage": stage, "error": "nope"}
        tab._start_migration("sql", "mongodb", Dialog())
        _poll(env)
        kind, a, k = env.works[-1].events[-1]
        assert a[0] == f"Couldn't open the {which} database" and k["details"] == "nope"
        assert env.mgr.updates == []

    def test_partial_copy(self, tab, env, inline):
        env.mgr.migrate_result = False
        env.mgr.last_migration_result = {"failed": 2, "migrated": 3, "total": 5, "failed_paths": ["A", "B (x)"]}
        tab._start_migration("sql", "firebase", Dialog())
        _poll(env)
        kind, a, k = env.works[-1].events[-1]
        assert a[0] == "Some data couldn't be copied"
        assert a[1].startswith("Copied 3 of 5 items. 2 couldn't be written to the Firebase database")
        assert k["details"] == "Not copied:\nA\nB (x)"

    def test_unknown_failure(self, tab, env, inline):
        env.mgr.migrate_result = False
        env.mgr.last_migration_result = None
        tab._start_migration("sql", "firebase", Dialog())
        _poll(env)
        kind, a, k = env.works[-1].events[-1]
        assert a[0] == "The migration stopped before it finished" and k["details"] is None

    def test_thread_exception(self, tab, env, inline):
        env.mgr.migrate_exc = RuntimeError("crashed")
        tab._start_migration("sql", "firebase", Dialog())
        _poll(env)
        kind, a, k = env.works[-1].events[-1]
        assert kind == "fail" and k["details"] == "crashed"

    def test_poll_waits_and_reports_slow(self, tab, env, monkeypatch):
        holder = {}

        class LaterThreading:
            def Thread(self, target=None, daemon=None):
                holder["target"] = target
                return types.SimpleNamespace(start=lambda: None)

        monkeypatch.setattr(dt, "threading", LaterThreading())
        tab._start_migration("sql", "firebase", Dialog())
        w = env.works[-1]
        poll = env.sched[-1][1]
        for _ in range(239):
            env.sched.clear()
            poll()
            assert env.sched and env.sched[-1][1] is poll
        env.sched.clear()
        poll()
        assert w.events[-1][0] == "update" and "Still copying" in w.events[-1][1][0]
        holder["target"]()
        env.sched.clear()
        poll()
        assert w.events[-1][0] == "done" and env.sched == []
        poll()  # already reported: nothing more
        assert sum(1 for e in w.events if e[0] == "done") == 1

    def test_error_before_progress(self, tab, env, inline):
        tab._start_migration("sql", "firebase", Dialog(exc=RuntimeError("dialog gone")))
        k = env.notes[-1][1]
        assert k["title"] == "Couldn't start the migration" and k["details"] == "dialog gone"
        assert env.works == []

    def test_error_after_progress(self, tab, env, monkeypatch):
        class Broken:
            def Thread(self, **kw):
                raise RuntimeError("no threads")

        monkeypatch.setattr(dt, "threading", Broken())
        tab._start_migration("sql", "firebase", Dialog())
        kind, a, k = env.works[-1].events[-1]
        assert kind == "fail" and a == ("Couldn't start the migration",) and k["details"] == "no threads"


# ======================================================================= explorer launcher

class TestViewerLauncher:
    def test_opens(self, tab, env, monkeypatch):
        shown = []

        class V:
            async def show(self):
                shown.append(1)

        monkeypatch.setattr(dt, "DatabaseViewer", V)
        asyncio.run(tab._show_data_viewer_dialog())
        assert shown == [1] and env.notes == []

    def test_error(self, tab, env, monkeypatch):
        class V:
            async def show(self):
                raise RuntimeError("no ui")

        monkeypatch.setattr(dt, "DatabaseViewer", V)
        asyncio.run(tab._show_data_viewer_dialog())
        assert env.notes[-1][1]["title"] == "Couldn't open the Database Explorer"


# ======================================================================= save / discard

class TestSaveDiscard:
    def test_no_buffer(self, env):
        dt.DatabaseTab().save()
        assert env.cfg.updates == []

    def test_config_write_fails(self, tab, env):
        env.cfg.ok = False
        tab._set("retry_attempts", 5)
        tab.save()
        assert env.mgr.updates == [] and tab.dirty is True

    def test_saved_type_change(self, tab, env):
        tab._set("database_type", "firebase")
        tab.save()
        assert env.cfg.updates[-1]["database_type"] == "firebase"
        kw, quiet = env.mgr.updates[-1]
        assert kw["database_type"] == "firebase" and kw["streamer_name"] == "mycelian" and quiet
        a, k = env.notes[-1]
        assert a == ("",) and k["title"] == "Database settings saved" and tab.dirty is False

    def test_saved_same_type_new_location_needs_restart(self, tab, env, tmp_path):
        tab._set("sql_database_path", str(tmp_path / "other.db"))
        tab.save()
        assert env.notes[-1][0] == ("Restart Mycelian to use the new connection details.",)

    def test_saved_timeout_only_no_restart(self, tab, env):
        env.mgr.cfg = DatabaseConfig(**{k: v for k, v in env.cfg.data.items()})
        tab._set("connection_timeout", 45)
        tab.save()
        assert env.notes[-1][0] == ("",)

    def test_cannot_connect_reverts(self, tab, env):
        before = dict(env.cfg.data)
        env.mgr.update_result = False
        env.mgr.last_init_error = "refused"
        tab._set("database_type", "mongodb")
        tab.save()
        cfg, quiet = env.mgr.inits[-1]
        assert cfg.database_type == "sql" and quiet
        assert env.cfg.updates[-1] == before
        a, k = env.notes[-1]
        assert k["title"] == "Couldn't connect to the MongoDB database" and k["type"] == "error"
        assert a[0].startswith("Mycelian kept using the local database.") and k["details"] == "refused"
        assert tab.dirty is True and tab.buffer.database_type == "mongodb"

    def test_cannot_connect_without_previous_file(self, tab, env):
        env.cfg.data = {}
        tab._set("database_type", "mongodb")
        env.mgr.update_result = False
        tab.save()
        assert len(env.cfg.updates) == 1  # only the attempted save, no restore
        assert env.notes[-1][1]["details"] is None

    def test_load_from_config_defaults(self, env):
        env.cfg.data = {}
        t = dt.DatabaseTab()
        t._load_from_config()
        b = t.buffer
        assert (b.database_type, b.sql_database_path, b.mongodb_database_name) == ("sql", "mycelian.db", "mycelian")
        assert b.firebase_service_account_path == "ServiceAccountKey.json"
        assert b.connection_timeout == 30 and b.retry_attempts == 3 and t.dirty is False

    def test_discard_resets_form(self, env):
        tab = dt.DatabaseTab()

        async def check(user):
            tab.ui_elements["mongodb_database_name"].value = "changed"
            tab._on_database_type_change("mongodb")
            assert tab.dirty
            tab.discard()
            assert tab.buffer.database_type == "sql" and tab.buffer.mongodb_database_name == "mdb"
            assert tab.ui_elements["mongodb_database_name"].value == "mdb"
            assert tab._type_boxes["sql"].visible and not tab._type_boxes["mongodb"].visible
            assert tab.dirty is False

        _built(tab, check)

    def test_save_button_on_page(self, env):
        tab = dt.DatabaseTab()

        async def check(user):
            tab.ui_elements["retry_attempts"].value = 7
            user.find(kind=ui.button, content="Save").click()
            assert env.cfg.updates[-1]["retry_attempts"] == 7
            assert tab.dirty is False

        _built(tab, check)

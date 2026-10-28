# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/uiwindows/tabs/about_tab.py: helpers, the About tab UI (hero, update status,
log health check, clear-log dialog, diagnostics report) and the browser source URL cards.
No update checks reach the network: the update manager is faked."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import pytest  # noqa: E402
from nicegui import ui  # noqa: E402
from nicegui.client import Client  # noqa: E402
from nicegui.testing.user_interaction import UserInteraction  # noqa: E402

from modules import alert_processor, log_parser, updater, web_engine  # noqa: E402
from modules.help_system import help_render  # noqa: E402
from modules.log_parser import ActionableLogSummary, UniqueError  # noqa: E402
from modules.uiwindows.tabs import about_tab as at  # noqa: E402
from ui_sim import run_ui  # noqa: E402


# ----------------------------------------------------------------- fakes

class Host:
    def __init__(self, **settings):
        base = dict(version="1.2.3", build_number="abc123", build_date="2026-10-20", auto_update=True)
        base.update(settings)
        self.app_settings = SimpleNamespace(**base)
        self.copied = []
        self.calls = []

    def _copy_url_to_clipboard(self, text, message=None):
        self.copied.append((text, message))

    def show_changelog_modal(self):
        self.calls.append("changelog")

    def check_for_updates_manual(self):
        self.calls.append("check")

    def open_logs_folder(self):
        self.calls.append("logs")

    def _format_source_url_title(self, name):
        return f"Title:{name}"

    def _prompt_add_source_to_obs(self, info):
        self.calls.append(("obs", info["name"]))


class UpdateMgr:
    def __init__(self, result=None, checking=False, at=None, available=None, error=None, show=True):
        self.is_checking = checking
        self.last_check_result = result
        self.last_check_at = at
        self.available_update = available
        self.last_check_error = error
        self.show = show
        self.shown = 0

    def show_available_update(self):
        self.shown += 1
        return self.show


class Engine:
    def __init__(self, urls):
        self.urls = urls

    def get_available_source_urls(self):
        if isinstance(self.urls, Exception):
            raise self.urls
        return self.urls


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(at, "resolve_build_number", lambda stamped="dev": f"B-{stamped}")
    monkeypatch.setattr(help_render, "_shared_stylesheets", set())
    monkeypatch.setattr(Client, "shared_head_html", Client.shared_head_html)
    monkeypatch.setattr(updater, "update_manager", UpdateMgr())
    monkeypatch.setattr(web_engine, "web_engine_instance", None)
    monkeypatch.setattr(alert_processor, "web_engine_instance", None, raising=False)


@pytest.fixture
def timers(monkeypatch):
    calls = []

    class _Timer:
        active = True

    def fake(delay, fn, once=False, active=True):
        t = _Timer()
        calls.append((delay, fn, once, t))
        return t

    monkeypatch.setattr(at, "layout_schedule", fake)
    return calls


@pytest.fixture
def notes(monkeypatch):
    calls = []
    monkeypatch.setattr(at, "notify", lambda msg, **k: calls.append((msg, k)))
    return calls


def fire(user, el, event="click", args=None):
    UserInteraction(user, {el}, None).trigger(event, args)


def by_class(client, cls):
    return sorted((e for e in client.elements.values() if cls in e.classes), key=lambda e: e.id)


def icon_button(client, icon):
    return [e for e in client.elements.values() if isinstance(e, ui.button) and e.props.get("icon") == icon]


def html_text(container):
    return " ".join(e.content for e in container.descendants() if isinstance(e, ui.html))


def label_text(container):
    return [e.text for e in container.descendants() if isinstance(e, ui.label)]


def summary(*errors):
    return ActionableLogSummary(total_count=sum(e.count for e in errors), unique_errors=list(errors))


def build_tab(host, check, summary_value=None, **_):
    made = {}

    def build():
        tab = at.AboutTab(host)
        if summary_value is not None:
            tab._summary = (lambda: summary_value) if not isinstance(summary_value, Exception) else _raiser(summary_value)
        tab.build(ui.column())
        made["tab"] = tab

    async def run(user):
        with user.client:
            await check(user, made["tab"])

    run_ui(build, run)
    return made["tab"]


def _raiser(exc):
    def f():
        raise exc
    return f


# ----------------------------------------------------------------- helpers

def test_icon_escapes():
    assert at._icon("a<b", "x") == '<span class="abt-icon x" aria-hidden="true">a&lt;b</span>'


def test_ago(monkeypatch):
    now = 1_800_000_000.0
    monkeypatch.setattr(at.time, "time", lambda: now)
    assert at._ago(0) == ""
    assert at._ago(now + 100) == "just now"  # future clamps to 0
    assert at._ago(now - 50) == "1m ago"
    assert at._ago(now - 3599) == "59m ago"
    assert at._ago(now - 7200) == "2h ago"
    from datetime import datetime

    assert at._ago(now - 90000) == datetime.fromtimestamp(now - 90000).strftime("%b %d, %H:%M")


@pytest.mark.parametrize(
    "value, out",
    [("2026-01-05", "Jan 05, 2026"), ("2026-01-05T10:11:12", "Jan 05, 2026"), ("2026-01-05 10:11:12.123", "Jan 05, 2026"),
     (None, "unknown"), ("weird", "weird")],
)
def test_friendly_date(value, out):
    assert at._friendly_date(value) == out


def test_log_ts_none():
    assert at._log_ts(None) is None


@pytest.mark.parametrize("plat, name", [("darwin", "macOS"), ("win32", "Windows"), ("linux", None)])
def test_system_line(monkeypatch, plat, name):
    monkeypatch.setattr(at.sys, "platform", plat)
    monkeypatch.setattr(at.platform, "mac_ver", lambda: ("15.1", ("", "", ""), "arm64"))
    monkeypatch.setattr(at.platform, "release", lambda: "6.1")
    monkeypatch.setattr(at.platform, "system", lambda: "Linux")
    monkeypatch.setattr(at.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(at.platform, "python_version", lambda: "3.14.0")
    line = at._system_line()
    version = "15.1" if plat == "darwin" else "6.1"
    assert line == f"{name or 'Linux'} {version} (x86_64) · Python 3.14.0"


def test_system_line_unknown_platform(monkeypatch):
    monkeypatch.setattr(at.sys, "platform", "sunos5")
    monkeypatch.setattr(at.platform, "system", lambda: "")
    assert at._system_line().startswith("sunos5 ")


def test_logo_data_uri_real_and_failure(monkeypatch):
    at._logo_data_uri.cache_clear()
    try:
        uri = at._logo_data_uri()
        assert uri and uri.startswith("data:image/png;base64,")
        at._logo_data_uri.cache_clear()
        from modules import path_utils

        monkeypatch.setattr(path_utils, "get_assets_path", lambda rel: "/nonexistent/x.png")
        assert at._logo_data_uri() is None
    finally:
        at._logo_data_uri.cache_clear()


def test_manager_lookup(monkeypatch):
    mgr = UpdateMgr()
    monkeypatch.setattr(updater, "update_manager", mgr)
    assert at.AboutTab._manager() is mgr
    monkeypatch.delattr(updater, "update_manager")
    assert at.AboutTab._manager() is None


# ----------------------------------------------------------------- update state

@pytest.mark.parametrize(
    "mgr, auto, expected",
    [
        (None, True, ("", "help_outline", "Update status unavailable", "Automatic checks are on", None)),
        (UpdateMgr(checking=True), False, ("is-checking", "autorenew", "Checking for updates…", "Contacting GitHub", None)),
        (UpdateMgr(result="available", available={"latest_version": "2.0"}), True,
         ("is-available", "new_releases", "Mycelian 2.0 is available", "Found  · Automatic checks are on", "2.0")),
        (UpdateMgr(result="available", available=None), False,
         ("is-available", "new_releases", "Mycelian  is available", "Found  · Automatic checks are off (App Settings)", "")),
        (UpdateMgr(result="up_to_date"), True,
         ("is-ok", "check_circle", "You're on the latest version", "Checked  · Automatic checks are on", None)),
        (UpdateMgr(result="error", error="  x" * 100), True, None),
        (UpdateMgr(result="error", error=None), True, ("is-error", "cloud_off", "Couldn't check for updates", "", None)),
        (UpdateMgr(result=None), False, ("", "schedule", "Not checked yet this session", "Automatic checks are off (App Settings)", None)),
    ],
)
def test_update_state(monkeypatch, mgr, auto, expected):
    monkeypatch.setattr(at.AboutTab, "_manager", staticmethod(lambda: mgr))
    tab = at.AboutTab(Host(auto_update=auto))
    state = tab._update_state()
    if expected is None:
        assert state[0] == "is-error" and state[3].endswith(" · ")
        assert len(state[3]) == 120 + 3
    else:
        assert state == expected


def test_render_update_without_box():
    tab = at.AboutTab(Host())
    tab._render_update()  # nothing to render into
    tab._tick_update()  # no box and no timer
    assert tab._update_sig is None


# ----------------------------------------------------------------- build

def test_build_hero_and_actions(monkeypatch, timers, notes):
    import webbrowser

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url))
    host = Host()

    async def check(user, tab):
        await user.should_see("Mycelian")
        await user.should_see("Alerts, overlays, chatbot and automation for your stream")
        hero = by_class(user.client, "abt-hero")[0]
        text = html_text(hero)
        assert "Version <b>1.2.3</b>" in text
        assert "Build <code>B-abc123</code>" in text
        assert "Built Oct 20, 2026" in text
        assert "abt-logo" in text
        # copy build chip
        chip = [e for e in by_class(user.client, "abt-chip") if e.tag == "button"][0]
        fire(user, chip)
        assert host.copied[-1] == ("B-abc123", "Build number copied")
        # update box shows "not checked yet"
        await user.should_see("Not checked yet this session")
        # buttons
        fire(user, [e for e in user.client.elements.values() if isinstance(e, ui.button) and e.text == "What's new"][0])
        fire(user, icon_button(user.client, "open_in_new")[0])
        fire(user, [e for e in user.client.elements.values() if isinstance(e, ui.button) and e.text == "Check for updates"][0])

    build_tab(host, check)
    assert opened == [at.GITHUB_URL]
    assert host.calls == ["changelog", "check"]
    delays = [(d, once) for d, _, once, _ in timers]
    assert delays[:3] == [(0.1, True), (0.1, True), (1.5, False)]
    assert delays[3] == (0.2, True)  # re-render after manual check


def test_build_without_logo_and_dev_version(monkeypatch, timers):
    monkeypatch.setattr(at, "_logo_data_uri", lambda: None)
    host = Host(version="", build_number="dev", build_date="")

    async def check(user, tab):
        text = html_text(by_class(user.client, "abt-hero")[0])
        assert "abt-logo-fallback" in text
        assert "Version <b>dev</b>" in text and "Built unknown" in text

    build_tab(host, check)


def test_build_schedules_refreshes(monkeypatch, timers):
    async def check(user, tab):
        fns = [fn for _, fn, _, _ in timers]
        assert fns[0] == tab.refresh_logs_async and fns[1] == tab.refresh_sources
        assert tab._update_timer is timers[2][3]

    build_tab(Host(), check)


def test_update_rendering_and_tick(monkeypatch, timers):
    mgr = UpdateMgr(result="available", available={"latest_version": "3.0"}, at=time.time())
    monkeypatch.setattr(updater, "update_manager", mgr)
    host = Host()

    async def check(user, tab):
        await user.should_see("Mycelian 3.0 is available")
        assert "is-available" in tab.update_box.classes
        btn = [e for e in tab.update_box.descendants() if isinstance(e, ui.button)]
        assert [b.text for b in btn] == ["Update now"]
        fire(user, btn[0])
        assert mgr.shown == 1 and host.calls == []
        # update shows again only on change
        before = list(tab.update_box.descendants())
        tab._tick_update()
        assert list(tab.update_box.descendants()) == before
        mgr.last_check_result = "up_to_date"
        tab._tick_update()
        assert "is-ok" in tab.update_box.classes
        await user.should_see("You're on the latest version")
        # Update now falls back to a manual check when the prompt can't be shown
        mgr.show = False
        tab._open_update()
        assert host.calls == ["check"]
        # deleted box deactivates the timer
        tab.update_box.delete()
        tab._tick_update()
        assert tab._update_timer.active is False

    build_tab(host, check)


def test_open_update_without_manager(monkeypatch, timers):
    monkeypatch.setattr(at.AboutTab, "_manager", staticmethod(lambda: None))
    host = Host()
    tab = at.AboutTab(host)
    tab._check_updates = lambda: host.calls.append("check")
    tab._open_update()
    assert host.calls == ["check"]


def test_tick_without_box_and_timer():
    tab = at.AboutTab(Host())
    timer = SimpleNamespace(active=True)
    tab._update_timer = timer
    tab._tick_update()
    assert timer.active is False


# ----------------------------------------------------------------- health check

def _err(msg="Boom", count=1, level="ERROR", source="modules.twitch", details=None, last_seen="2026-10-22 18:01:02,100"):
    return UniqueError(message=msg, count=count, last_seen=last_seen, level=level, source=source, details=details or [])


def test_refresh_logs_clean(timers):
    async def check(user, tab):
        tab.refresh_logs()
        await user.should_see("No problems found")

    build_tab(Host(), check, summary_value=summary())


def test_refresh_logs_errors_rows_and_copy(monkeypatch, timers):
    host = Host()
    crit = _err("Crit <thing>", count=3, level="CRITICAL", source="modules.obs.client",
                details=["Traceback", "  line 1"], last_seen="not a date")
    plain = _err("Plain", count=1, source="")
    s = ActionableLogSummary(total_count=4, unique_errors=[crit, plain])

    async def check(user, tab):
        tab.refresh_logs()
        text = html_text(tab.health_box)
        assert "4 errors in the log" in text
        await user.should_see("2 different messages, newest first")
        rows = by_class(user.client, "abt-err")
        assert len(rows) == 2 and "is-critical" in rows[0].classes and "is-critical" not in rows[1].classes
        assert "Crit &lt;thing&gt;" in html_text(rows[0])
        assert "client" in html_text(rows[0]) and "modules.obs" not in html_text(rows[0])
        assert "Critical" in html_text(rows[0]) and "×3" in html_text(rows[0])
        assert "not a date" in html_text(rows[0])
        assert "<pre>Traceback\n  line 1</pre>" in html_text(rows[0])
        assert "<pre>" not in html_text(rows[1]) and "×" not in html_text(rows[1])
        copy = [e for e in rows[0].descendants() if isinstance(e, ui.button)][0]
        fire(user, copy)
        assert host.copied[-1] == ("Crit <thing>\nTraceback\n  line 1", "Error copied")

    build_tab(host, check, summary_value=s)


def test_refresh_logs_single_error(timers):
    async def check(user, tab):
        tab.refresh_logs()
        assert "1 error in the log" in html_text(tab.health_box)
        assert not any("different message" in t for t in label_text(tab.health_box))

    build_tab(Host(), check, summary_value=summary(_err()))


def test_refresh_logs_one_unique_many_count(timers):
    async def check(user, tab):
        tab.refresh_logs()
        await user.should_see("1 different message, newest first")

    build_tab(Host(), check, summary_value=summary(_err(count=5)))


def test_refresh_logs_error(timers, caplog):
    async def check(user, tab):
        tab.refresh_logs()
        assert "Could not read the log file" in html_text(tab.health_box)

    build_tab(Host(), check, summary_value=OSError("locked"))
    assert "Error reading log file" in caplog.text


def test_refresh_logs_without_box():
    tab = at.AboutTab(Host())
    tab.refresh_logs()


def test_refresh_logs_async(timers):
    async def check(user, tab):
        await tab.refresh_logs_async()
        await user.should_see("No problems found")

    build_tab(Host(), check, summary_value=summary())


def test_refresh_logs_async_error_and_none(timers):
    async def check(user, tab):
        tab._summary = _raiser(RuntimeError("x"))
        await tab.refresh_logs_async()
        assert "Could not read the log file" in html_text(tab.health_box)
        tab._summary = lambda: None
        await tab.refresh_logs_async()
        assert "Could not read the log file" in html_text(tab.health_box)  # unchanged
        tab._summary = lambda: summary()
        tab.health_box.delete()
        await tab.refresh_logs_async()  # deleted box: no render

    build_tab(Host(), check)


def test_refresh_logs_async_without_box():
    import asyncio

    asyncio.run(at.AboutTab(Host()).refresh_logs_async())


def test_summary_uses_settings_module(monkeypatch):
    from modules.uiwindows import settings as settings_module

    monkeypatch.setattr(settings_module, "get_actionable_errors", lambda: "SUMMARY")
    assert at.AboutTab(Host())._summary() == "SUMMARY"


def test_health_buttons(timers):
    host = Host()

    async def check(user, tab):
        fire(user, icon_button(user.client, "folder_open")[0])
        fire(user, icon_button(user.client, "content_paste")[0])
        assert host.copied[-1][1] == "Diagnostics report copied"

    build_tab(host, check, summary_value=summary())
    assert host.calls == ["logs"]


# ----------------------------------------------------------------- clear log dialog

@pytest.mark.parametrize("size, text", [(5 * 1024 * 1024, "5.0 MB"), (2048, "2 KB"), (-1, "0 KB")])
def test_clear_log_dialog_size_and_cancel(monkeypatch, timers, size, text):
    monkeypatch.setattr(log_parser, "get_log_file_size", lambda: size)
    cleared = []
    monkeypatch.setattr(log_parser, "clear_log_file", lambda: cleared.append(1) or True)

    async def check(user, tab):
        fire(user, icon_button(user.client, "delete_sweep")[0])
        await user.should_see("Clear the log file?")
        await user.should_see(f"mycelian.log ({text})")
        fire(user, [e for e in user.client.elements.values() if isinstance(e, ui.button) and e.text == "Cancel"][0])

    build_tab(Host(), check, summary_value=summary())
    assert cleared == []


@pytest.mark.parametrize("ok", [True, False])
def test_clear_log_confirm(monkeypatch, timers, notes, ok):
    monkeypatch.setattr(log_parser, "get_log_file_size", lambda: 0)
    monkeypatch.setattr(log_parser, "clear_log_file", lambda: ok)
    refreshed = []

    async def check(user, tab):
        async def fake_refresh():
            refreshed.append(1)

        tab.refresh_logs_async = fake_refresh
        tab._confirm_clear_log()
        btn = [e for e in user.client.elements.values() if isinstance(e, ui.button) and e.text == "Clear log"][0]
        btn.props["label"] = "Clear log"
        user.find("Clear log").click()
        import asyncio

        for _ in range(5):
            await asyncio.sleep(0)

    build_tab(Host(), check, summary_value=summary())
    assert refreshed == [1]
    title = notes[-1][1]["title"]
    assert title == ("Log file cleared" if ok else "Couldn't clear the log file")
    assert notes[-1][1]["type"] == ("success" if ok else "error")


# ----------------------------------------------------------------- diagnostics report

def test_copy_report_full(monkeypatch):
    monkeypatch.setattr(at, "_system_line", lambda: "SYSTEM")
    monkeypatch.setattr(updater, "update_manager", UpdateMgr(result="up_to_date"))
    host = Host()
    tab = at.AboutTab(host)
    tab._summary = lambda: summary(
        _err("A", count=2, details=[f"d{i}" for i in range(20)]),
        UniqueError(message="B", count=1, last_seen="t"),
    )
    tab._copy_report()
    text, msg = host.copied[0]
    lines = text.split("\n")
    assert lines[0] == "Mycelian 1.2.3 (build B-abc123, built 2026-10-20)"
    assert lines[1] == "SYSTEM"
    assert lines[2] == "Update check: up_to_date"
    assert lines[3] == "Log errors: 3"
    assert lines[4] == "- [2026-10-22 18:01:02,100] ERROR modules.twitch: A (x2)"
    assert lines[5:17] == [f"    d{i}" for i in range(12)]
    assert lines[17] == "- [t] ERROR : B"
    assert msg == "Diagnostics report copied"


def test_copy_report_without_update_and_log_error(monkeypatch):
    monkeypatch.setattr(at, "_system_line", lambda: "SYSTEM")
    monkeypatch.setattr(at.AboutTab, "_manager", staticmethod(lambda: None))
    host = Host()
    host.app_settings = SimpleNamespace()
    tab = at.AboutTab(host)
    tab._summary = _raiser(RuntimeError("locked"))
    tab._copy_report()
    lines = host.copied[0][0].split("\n")
    assert lines[0] == "Mycelian ? (build B-dev, built ?)"
    assert lines[2] == "Log errors: unavailable (locked)"


# ----------------------------------------------------------------- source URLs

URLS = [
    {"name": "alerts", "title": "Alerts", "url": "http://127.0.0.1:5000/alerts", "type": "template", "control_count": 2,
     "description": "Alert overlay"},
    {"name": "_hidden", "url": "http://127.0.0.1:5000/h", "type": "template"},
    {"name": "sys", "url": "http://127.0.0.1:5000/sys", "type": "system_info"},
    {"name": "bundle", "title": "Bundle", "url": "http://127.0.0.1:5000/c/bundle", "type": "container", "template_count": 1},
    {"name": "chat", "url": "http://127.0.0.1:5000", "type": "template", "control_count": 1},
    {"name": "big", "title": "Big box", "url": "http://127.0.0.1:5000/c/big", "type": "container", "template_count": 3},
]


def test_source_urls_sorting_and_filtering(monkeypatch):
    monkeypatch.setattr(web_engine, "web_engine_instance", Engine(URLS))
    names = [u["name"] for u in at.AboutTab._source_urls()]
    assert names == ["big", "bundle", "alerts", "chat"]


def test_source_urls_fallback_engine_and_none(monkeypatch):
    assert at.AboutTab._source_urls() is None
    monkeypatch.setattr(alert_processor, "web_engine_instance", Engine(None), raising=False)
    assert at.AboutTab._source_urls() == []


def test_sources_cards(monkeypatch, timers):
    monkeypatch.setattr(web_engine, "web_engine_instance", Engine(URLS))
    host = Host()

    async def check(user, tab):
        tab.refresh_sources()
        cards = by_class(user.client, "source-url-card")
        assert len(cards) == 4
        assert tab.sources_count.text == "4 sources" and tab.sources_count.visible
        big, bundle, alerts, chat = cards
        assert "is-container" in big.classes
        assert "3 templates" in html_text(big) and "Container" in html_text(big)
        assert "1 template<" in html_text(bundle)
        assert "2 controls" in html_text(alerts)
        assert "1 control<" in html_text(chat)
        assert "Title:chat" in label_text(chat)  # title from host formatter
        # url split into host + path; bare host shows "/"
        assert '<span class="abt-url-host">http://127.0.0.1:5000</span><span class="abt-url-path">/alerts</span>' in html_text(alerts)
        assert '<span class="abt-url-path">/</span>' in html_text(chat)

        # copy via button and via the URL row
        copy_btn = [e for e in alerts.descendants() if isinstance(e, ui.button) and e.props.get("icon") == "content_copy"][0]
        fire(user, copy_btn)
        assert host.copied[-1] == ("http://127.0.0.1:5000/alerts", None)
        assert copy_btn.props.get("icon") == "check" and "is-copied" in copy_btn.classes
        restore = timers[-1][1]
        restore()
        assert copy_btn.props.get("icon") == "content_copy" and "is-copied" not in copy_btn.classes
        row = [e for e in alerts.descendants() if "source-url-card-url" in e.classes][0]
        fire(user, row)
        assert host.copied[-1][0] == "http://127.0.0.1:5000/alerts"
        # restore after the button was deleted is a no-op
        copy_btn.delete()
        timers[-1][1]()

        # Add to OBS
        obs = [e for e in chat.descendants() if isinstance(e, ui.button) and not e.props.get("icon")][0]
        fire(user, obs)
        assert host.calls[-1] == ("obs", "chat")

        # filter
        tab._apply_filter("  BUNDLE ")
        assert [c.visible for c in by_class(user.client, "source-url-card")] == [False, True, False, False]
        search = [e for e in user.client.elements.values() if isinstance(e, ui.input)][0]
        search.set_value("zzz-no-match")
        assert not any(c.visible for c in by_class(user.client, "source-url-card"))
        search.set_value(None)
        assert all(c.visible for c in by_class(user.client, "source-url-card"))
        search.set_value("127.0.0.1:5000/c/")
        assert [c.visible for c in by_class(user.client, "source-url-card")] == [True, True, False, False]
        # refresh keeps the filter
        tab.refresh_sources()
        assert [c.visible for c in by_class(user.client, "source-url-card")] == [True, True, False, False]
        # reload button
        fire(user, icon_button(user.client, "refresh")[-1])

    build_tab(host, check, summary_value=summary())


def test_sources_empty_states(monkeypatch, timers):
    async def check(user, tab):
        tab.refresh_sources()
        assert "overlay server is not running" in html_text(tab.sources_box)
        assert tab.sources_count.text == "0 sources" and not tab.sources_count.visible
        monkeypatch.setattr(web_engine, "web_engine_instance", Engine([]))
        tab.refresh_sources()
        assert "No browser sources available." in html_text(tab.sources_box)
        monkeypatch.setattr(web_engine, "web_engine_instance", Engine(RuntimeError("boom")))
        tab.refresh_sources()
        assert "Couldn&#x27;t list the source URLs" in html_text(tab.sources_box)
        monkeypatch.setattr(web_engine, "web_engine_instance", Engine(URLS[:1]))
        tab.refresh_sources()
        assert tab.sources_count.text == "1 source"

    build_tab(Host(), check, summary_value=summary())


def test_sources_without_boxes():
    tab = at.AboutTab(Host())
    tab.refresh_sources()
    tab._set_count(3)
    tab._apply_filter("x")
    assert tab._filter == "x"


def test_copy_source_error_ignored(monkeypatch):
    host = Host()
    tab = at.AboutTab(host)

    class _Btn:
        def props(self, *a):
            raise RuntimeError("deleted")

    tab._copy_source("http://x", _Btn())
    assert host.copied == [("http://x", None)]

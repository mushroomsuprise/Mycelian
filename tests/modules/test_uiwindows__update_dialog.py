# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for the update / older-versions window (modules.uiwindows.update_dialog).

The dialog is built in NiceGUI's simulated user; nothing is downloaded.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import pytest  # noqa: E402

from modules import update_sync  # noqa: E402
from modules.uiwindows import update_dialog as ud  # noqa: E402
from ui_sim import run_ui  # noqa: E402

INFO = {
    "latest_version": "1.15.0",
    "release_notes": "## New\n- Older versions\n- **Bold** `code`",
    "release_url": "https://github.com/x/releases/1.15.0",
    "published_at": "2026-11-03T10:00:00Z",
}


def report(phase, fraction, **extra):
    state = dict(extra)
    update_sync._report(state, phase, fraction, extra.get("speed_bps", 0.0), extra.get("label", ""))
    state.update(extra)
    return state


# --------------------------------------------------------------------------- formatting

@pytest.mark.parametrize("seconds, expected", [
    (None, ("Estimating time left…", "")),
    (2.4, ("A few seconds left", "0:02")),
    (12, ("About 12 s left", "0:12")),
    (75, ("About 1 min left", "1:15")),
    (-3, ("A few seconds left", "0:00")),
])
def test_format_eta(seconds, expected):
    assert ud.format_eta(seconds) == expected


@pytest.mark.parametrize("count, expected", [
    (0, "0 B"), (900, "900 B"), (2048, "2 KB"), (19293847, "18.4 MB"), (3 * 1024 ** 3, "3.0 GB"), ("x", "0 B"),
])
def test_format_bytes(count, expected):
    assert ud.format_bytes(count) == expected


def test_format_speed_and_date():
    assert ud.format_speed(6501171) == "6.2 MB/s"
    assert ud.format_release_date("2026-11-03T10:00:00Z") == "Nov 3, 2026"
    assert ud.format_release_date("") == "" and ud.format_release_date(None) == ""


def test_release_notes_html_is_safe():
    out = ud.release_notes_html(
        "# Title\n- one\n* two\n\nplain <script>x</script>\n"
        "[ok](https://a.b/c) [bad](javascript:alert(1)) **b** `c`"
    )
    assert out.startswith("<h4>Title</h4><ul><li>one</li><li>two</li></ul>")
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert '<a href="https://a.b/c" target="_blank" rel="noopener">ok</a>' in out
    assert "javascript:" in out and 'href="javascript' not in out
    assert "<strong>b</strong>" in out and "<code>c</code>" in out
    assert ud.release_notes_html("") == ""


# --------------------------------------------------------------------------- views

def test_offer_shows_release_and_buttons():
    calls = []

    def build():
        d = ud.UpdateDialog(on_dismiss=lambda: calls.append("dismiss"))
        d.show_offer(INFO, "1.14.0", on_update=lambda: calls.append("update"), on_later=lambda: calls.append("later"))
        d.open()

    async def check(user):
        await user.should_see("Mycelian 1.15.0 is available")
        await user.should_see("Released Nov 3, 2026")
        await user.should_see("Installed <b>1.14.0</b>")
        await user.should_see("<li>Older versions</li>")
        await user.should_see("View on GitHub")
        user.find("Update to 1.15.0").click()
        user.find("Not now").click()
        user.find(marker="upd-close").click()

    run_ui(build, check)
    assert calls == ["update", "later", "later"]  # the close button on an offer means "Not now"


def test_offer_without_notes_or_link():
    def build():
        d = ud.UpdateDialog(on_dismiss=lambda: None)
        d.show_offer({"latest_version": "2.0", "release_url": "javascript:x"}, "", on_update=lambda: None,
                     on_later=lambda: None)
        d.open()

    async def check(user):
        await user.should_see("This release has no notes.")
        await user.should_see("A new version of Mycelian is ready to install.")
        await user.should_not_see("View on GitHub")
        await user.should_not_see("Installed")

    run_ui(build, check)


def test_picker_selects_a_version_and_installs_it():
    picked = []
    releases = [
        {"latest_version": "1.15.0", "release_notes": "- newer", "published_at": "2026-11-03"},
        {"latest_version": "1.14.0", "release_notes": "", "published_at": "2026-10-29"},
    ]

    def build():
        d = ud.UpdateDialog(on_dismiss=lambda: None)
        d.show_picker("1.16.0", releases, on_install=picked.append, on_cancel=lambda: picked.append("cancel"))
        d.open()

    async def check(user):
        await user.should_see("Install an older version")
        await user.should_see("You're on 1.16.0")
        await user.should_see("Previous")
        await user.should_see("oldest version you can go back to")
        await user.should_see("Install 1.15.0")
        user.find(marker="upd-version-1.14.0").click()
        await user.should_see("Install 1.14.0")
        user.find("Install 1.14.0").click()
        user.find("Cancel").click()

    run_ui(build, check)
    assert picked == [releases[1], "cancel"]


def test_picker_with_nothing_to_install():
    def build():
        d = ud.UpdateDialog(on_dismiss=lambda: None)
        d.show_picker("1.14.0", [], on_install=lambda r: pytest.fail("nothing to install"), on_cancel=lambda: None)
        d.open()

    async def check(user):
        await user.should_see("No older versions can be installed")
        await user.should_see("1.14.0 is the oldest release this updater supports")

    run_ui(build, check)


def test_loading_view():
    cancelled = []

    def build():
        d = ud.UpdateDialog(on_dismiss=lambda: None)
        d.show_loading("Older versions", "Loading releases from GitHub…", on_cancel=lambda: cancelled.append(1))
        d.open()

    async def check(user):
        await user.should_see("Loading releases from GitHub…")
        await user.should_see("Mycelian 1.14.0 and newer can be installed from here.")
        user.find("Cancel").click()

    run_ui(build, check)
    assert cancelled == [1]


def test_progress_shows_steps_speed_and_eta():
    holder = {}

    def build():
        d = ud.UpdateDialog(on_dismiss=lambda: None)
        d.show_progress("1.15.0", downgrade=False, on_cancel=lambda: holder.setdefault("cancel", True))
        d.render_progress(report(
            "downloading", 0.5, label="Downloading Mycelian.exe (2 of 4)", speed_bps=2 * 1024 * 1024,
            bytes_total=8 * 1024 * 1024, bytes_done=4 * 1024 * 1024, files_total=4, files_checked=300,
        ))
        holder["dialog"] = d
        d.open()

    async def check(user):
        await user.should_see("Updating to 1.15.0")
        await user.should_see("Step 3 of 5")
        await user.should_see("49")  # 0.03 + 0.07 + 0.78 * 0.5
        await user.should_see("50%")
        await user.should_see("4 of 300 files changed")
        await user.should_see("Downloading Mycelian.exe (2 of 4)")
        await user.should_see("2.0 MB/s")
        await user.should_see("4.0 MB</b> of 8.0 MB")
        await user.should_see("About 5 s left")
        user.find("Cancel update").click()

    run_ui(build, check)
    assert holder["cancel"] is True
    rendered = holder["dialog"]._rendered
    assert rendered["step3.cls"] == "upd-step is-active" and rendered["step2.cls"] == "upd-step is-done"
    assert rendered["step4.cls"] == "upd-step" and rendered["step4.open"] is False


def test_progress_hides_speed_outside_downloads_and_skips_unchanged_updates():
    holder = {}

    def build():
        d = ud.UpdateDialog(on_dismiss=lambda: None)
        d.show_progress("1.15.0", downgrade=True, on_cancel=lambda: None)
        state = report("merging", 0.5, label="Merging alerts.json · 2 of 4 templates", speed_bps=999.0,
                       bytes_total=100, bytes_done=100, files_total=3, files_checked=9)
        d.render_progress(state)
        sent = []
        d._p["pct"].set_text = lambda v: sent.append(v)
        d.render_progress(state)  # nothing changed: nothing is sent again
        holder.update(dialog=d, sent=sent)
        d.open()

    async def check(user):
        await user.should_see("Going back to 1.15.0")
        await user.should_see("Merging alerts.json · 2 of 4 templates")
        await user.should_see("100 B downloaded")
        await user.should_see("Cancel")

    run_ui(build, check)
    assert holder["sent"] == []
    assert holder["dialog"]._rendered["step4.meta"] == ""  # no speed outside the download step


def test_failed_view_keeps_steps_and_offers_retry():
    calls = []

    def build():
        d = ud.UpdateDialog(on_dismiss=lambda: calls.append("dismiss"))
        d.show_progress("1.15.0", downgrade=False, on_cancel=lambda: None)
        state = report("downloading", 0.2, files_total=2, files_checked=5)
        d.show_failed(state, "GitHub stopped answering.", "1.14.0", on_retry=lambda: calls.append("retry"),
                      details="trace")
        d.open()

    async def check(user):
        await user.should_see("The update didn't finish")
        await user.should_see("Mycelian 1.14.0 is unchanged.")
        await user.should_see("stopped at <b>Step 3 of 5</b>")
        await user.should_see("GitHub stopped answering.")
        await user.should_see("Copy details")
        user.find("Try again").click()
        user.find("Close").click()

    run_ui(build, check)
    assert calls == ["retry", "dismiss"]


def test_failed_view_without_progress_or_retry():
    def build():
        d = ud.UpdateDialog(on_dismiss=lambda: None)
        d.show_failed(report("listing", 0.0), "", "", on_retry=None)
        d.open()

    async def check(user):
        await user.should_see("Mycelian is unchanged.")
        await user.should_see("Something went wrong.")
        await user.should_not_see("Try again")
        await user.should_not_see("Copy details")

    run_ui(build, check)


def test_message_and_restarting_views():
    holder = {}

    def build():
        d = ud.UpdateDialog(on_dismiss=lambda: holder.setdefault("dismissed", True))
        d.show_message("Already up to date", "Your installed files already match this release.", kind="is-ok",
                       art="check_circle")
        holder["dialog"] = d
        d.open()

    async def check(user):
        await user.should_see("Already up to date")
        user.find("Close").click()
        user.find(marker="upd-close").click()

    run_ui(build, check)
    assert holder["dismissed"] is True

    def build_restart():
        d = ud.UpdateDialog(on_dismiss=lambda: None)
        d.show_progress("1.15.0", downgrade=False, on_cancel=lambda: None)
        d.render_progress(report("restarting", 1.0, label="Restarting Mycelian..."))
        d.show_restarting()
        d.open()

    async def check_restart(user):
        await user.should_see("Restarting Mycelian")
        await user.should_see("It opens again in a moment.")
        await user.should_see("Step 5 of 5")

    run_ui(build_restart, check_restart)


def test_is_gone_and_restarting_before_progress():
    holder = {}

    def build():
        d = ud.UpdateDialog(on_dismiss=lambda: None)
        d.show_restarting()  # no progress view yet: nothing to hide, no error
        holder["dialog"] = d

    run_ui(build)
    d = holder["dialog"]
    d.card = None
    assert d.is_gone

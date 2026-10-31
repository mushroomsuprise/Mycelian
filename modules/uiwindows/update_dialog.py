# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""The update window: offer a release, pick an older one, then show the install.

``UpdateDialog`` only draws. ``modules.updater.UpdateManager`` decides what to
show and runs the download. One window moves through its views in place:
offer or older-versions list → progress → failed / cancelled / restarting.
"""

from __future__ import annotations

import html
import logging
import re
import webbrowser
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from nicegui import context, ui

from .. import update_sync
from ..ui_buttons import outline_button, primary_button
from .update_dialog_styles import UPDATE_CSS

logger = logging.getLogger(__name__)

STYLE_ID = "mycelian-update-dialog"


# --------------------------------------------------------------------------- formatting

def icon(name: str, extra: str = "") -> str:
    classes = f"upd-icon {extra}".strip()
    return f'<span class="{classes}" aria-hidden="true">{html.escape(name)}</span>'


def format_bytes(count: Any) -> str:
    try:
        value = float(count)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} GB"


def format_speed(bytes_per_second: Any) -> str:
    return f"{format_bytes(bytes_per_second)}/s"


def format_eta(seconds: Optional[float]) -> tuple[str, str]:
    """(sentence, clock) for the time left; the clock is empty while estimating."""
    if seconds is None:
        return "Estimating time left…", ""
    seconds = max(0, int(round(seconds)))
    clock = f"{seconds // 60}:{seconds % 60:02d}"
    if seconds < 5:
        return "A few seconds left", clock
    if seconds < 60:
        return f"About {seconds} s left", clock
    minutes = round(seconds / 60)
    return f"About {minutes} min left", clock


def format_release_date(value: Any) -> str:
    text = str(value or "")[:10]
    try:
        stamp = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return ""
    return f"{stamp:%b} {stamp.day}, {stamp.year}"


_SAFE_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def release_notes_html(notes: str) -> str:
    """GitHub release notes (Markdown) as small, safe HTML."""
    lines = html.escape(str(notes or "")).replace("\r\n", "\n").split("\n")
    out: List[str] = []
    in_list = False

    def inline(text: str) -> str:
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)
        return _SAFE_LINK_RE.sub(r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)

    for raw in lines:
        line = raw.strip()
        bullet = re.match(r"^[-*+]\s+(.*)$", line)
        if bullet:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(bullet.group(1))}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        heading = re.match(r"^#{1,6}\s+(.*)$", line)
        if heading:
            out.append(f"<h4>{inline(heading.group(1))}</h4>")
        elif line:
            out.append(f"<p>{inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def _chip(name: str, body: str, accent: bool = False) -> str:
    return f'<span class="upd-chip{" is-accent" if accent else ""}">{icon(name)}{body}</span>'


def _logo_html() -> str:
    try:
        from .tabs.about_tab import _logo_data_uri

        logo = _logo_data_uri()
    except Exception:
        logo = None
    if logo:
        return f'<img class="upd-logo" src="{logo}" alt="">'
    return f'<div class="upd-badge">{icon("system_update")}</div>'


def _badge_html(name: str, kind: str = "") -> str:
    classes = f"upd-badge {kind}".strip()
    return f'<div class="{classes}">{icon(name)}</div>'


def _step_layout() -> List[tuple[str, str, float, float]]:
    """(phase, title, start, end) of each step on the overall bar."""
    out = []
    start = 0.0
    for phase, title, weight in update_sync.UPDATE_STEPS:
        out.append((phase, title, start, start + weight))
        start += weight
    return out


# --------------------------------------------------------------------------- dialog

class UpdateDialog:
    """A themed, persistent dialog with header, body and footer slots."""

    def __init__(self, on_dismiss: Callable[[], None]):
        from ..help_system.help_render import inject_page_stylesheet, promote_color_importance, scope_selectors

        inject_page_stylesheet(STYLE_ID, promote_color_importance(scope_selectors(UPDATE_CSS, ".upd")))
        self._on_dismiss = on_dismiss
        self.client = context.client
        self.dialog = ui.dialog().props("persistent")
        with self.dialog:
            self.card = ui.element("div").classes("upd")
            with self.card:
                self.head = ui.element("div").classes("upd-head")
                self.body = ui.element("div").classes("upd-body")
                self.foot = ui.element("div").classes("upd-foot")
        self.view = ""
        self._on_close: Optional[Callable[[], None]] = None
        self._p: Dict[str, Any] = {}
        self._rendered: Dict[str, Any] = {}

    # ----- lifecycle --------------------------------------------------------

    @property
    def is_gone(self) -> bool:
        for el in (self.dialog, self.card):
            if el is None or getattr(el, "is_deleted", False):
                return True
        return False

    def open(self) -> None:
        self.dialog.open()

    def dismiss(self) -> None:
        try:
            self.dialog.close()
        finally:
            self._on_dismiss()

    def _close_clicked(self) -> None:
        if self._on_close is not None:
            self._on_close()
        else:
            self.dismiss()

    def _reset(self, view: str) -> None:
        self.view = view
        self._on_close = None
        self._p = {}
        self._rendered = {}
        for slot in (self.head, self.body, self.foot):
            slot.clear()
        self.body.set_visibility(True)

    def _set_head(self, art: str, title: str, sub: str, chips: str = "", closable: bool = True) -> None:
        with self.head:
            ui.html(art, sanitize=False)
            with ui.element("div").classes("min-w-0"):
                self.title_label = ui.label(title).classes("upd-title")
                self.sub_label = ui.label(sub).classes("upd-sub")
                if chips:
                    ui.html(f'<div class="upd-chips">{chips}</div>', sanitize=False)
            if closable:
                close = ui.element("button").classes("upd-x").props('type=button title=Close aria-label=Close').mark("upd-close")
                with close:
                    ui.html(icon("close"), sanitize=False).classes("contents")
                close.on("click", lambda _e: self._close_clicked())
            else:
                ui.element("div")

    # ----- update available -------------------------------------------------

    def show_offer(
        self,
        info: Dict[str, Any],
        current_version: str,
        *,
        on_update: Callable[[], None],
        on_later: Callable[[], None],
    ) -> None:
        self._reset("offer")
        self._on_close = on_later  # closing the offer is the same as "Not now"
        version = str(info.get("latest_version") or "?")
        released = format_release_date(info.get("published_at"))
        chips = ""
        if current_version:
            chips += _chip("sell", f"Installed <b>{html.escape(current_version)}</b>")
        chips += _chip("arrow_forward", f"New <b>{html.escape(version)}</b>", accent=True)
        self._set_head(
            _logo_html(),
            f"Mycelian {version} is available",
            f"Released {released}" if released else "A new version of Mycelian is ready to install.",
            chips,
        )
        with self.body:
            ui.label("What's new").classes("upd-sect")
            notes = str(info.get("release_notes") or "").strip()
            if notes:
                ui.html(release_notes_html(notes), sanitize=False).classes("upd-notes")
            else:
                ui.label("This release has no notes.").classes("upd-empty-notes")
            ui.html(
                '<div class="upd-facts">'
                f'<div class="upd-fact">{icon("difference")}<div><b>Changed files only</b>'
                "Files you already have aren't downloaded again</div></div>"
                f'<div class="upd-fact">{icon("tune")}<div><b>Settings kept</b>Your template values carry over</div></div>'
                f'<div class="upd-fact">{icon("restart_alt")}<div><b>Quick restart</b>Mycelian reopens when it\'s done</div></div>'
                "</div>",
                sanitize=False,
            )
        with self.foot:
            self._github_link(info.get("release_url"))
            outline_button("Not now", lambda: on_later())
            primary_button(f"Update to {version}", lambda: on_update(), icon="system_update")

    def _github_link(self, url: Any) -> None:
        url = str(url or "")
        if url.startswith("https://"):
            ui.button("View on GitHub", icon="open_in_new", on_click=lambda: webbrowser.open(url)).props(
                "flat no-caps dense size=sm"
            ).classes("upd-link")
        else:
            ui.element("span").classes("upd-note")

    # ----- older versions ---------------------------------------------------

    def show_loading(self, title: str, message: str, *, on_cancel: Callable[[], None]) -> None:
        self._reset("loading")
        self._set_head(
            _badge_html("history"), title, f"Mycelian {update_sync_min_version()} and newer can be installed from here."
        )
        with self.body:
            ui.html(f'<div class="upd-loading">{icon("autorenew", "upd-spin")}<span>{html.escape(message)}</span></div>', sanitize=False)
        with self.foot:
            ui.element("span").classes("upd-note")
            outline_button("Cancel", lambda: on_cancel())

    def show_picker(
        self,
        current_version: str,
        releases: List[Dict[str, Any]],
        *,
        on_install: Callable[[Dict[str, Any]], None],
        on_cancel: Callable[[], None],
    ) -> None:
        self._reset("picker")
        self._set_head(
            _badge_html("history"),
            "Install an older version",
            f"Pick the release to go back to. You're on {current_version}." if releases
            else f"You're on {current_version}.",
        )
        selected: Dict[str, Any] = {"release": releases[0] if releases else None}
        rows: List[tuple[Any, Dict[str, Any]]] = []

        def select(release: Dict[str, Any]) -> None:
            selected["release"] = release
            for row, rel in rows:
                row.classes(replace="upd-ver is-on" if rel is release else "upd-ver")
            install_btn.set_text(f"Install {release['latest_version']}")

        with self.body:
            if not releases:
                ui.html(
                    f'<div class="upd-empty">{icon("inventory_2")}No older versions can be installed. '
                    f"{html.escape(update_sync_min_version())} is the oldest release this updater supports.</div>",
                    sanitize=False,
                )
            else:
                with ui.element("div").classes("upd-vlist"):
                    with ui.element("div").classes("upd-ver is-current"):
                        ui.html('<div class="upd-radio"></div>', sanitize=False)
                        ui.html(
                            f'<div><div class="upd-ver-name">{html.escape(current_version)} '
                            '<span class="upd-tag is-accent">Installed</span></div>'
                            '<div class="upd-ver-meta">The version you\'re using now</div></div>',
                            sanitize=False,
                        )
                        ui.element("span")
                    for index, release in enumerate(releases):
                        row = ui.element("div").classes("upd-ver is-on" if index == 0 else "upd-ver").mark(
                            f"upd-version-{release['latest_version']}"
                        )
                        rows.append((row, release))
                        with row:
                            ui.html('<div class="upd-radio"></div>', sanitize=False)
                            released = format_release_date(release.get("published_at"))
                            meta = f"Released {released}" if released else "Release date unknown"
                            if index == len(releases) - 1:
                                meta += " · oldest version you can go back to"
                            tag = '<span class="upd-tag">Previous</span>' if index == 0 else ""
                            ui.html(
                                f'<div><div class="upd-ver-name">{html.escape(release["latest_version"])} {tag}</div>'
                                f'<div class="upd-ver-meta">{html.escape(meta)}</div></div>',
                                sanitize=False,
                            )
                            ui.element("span")
                            notes = str(release.get("release_notes") or "").strip()
                            if notes:
                                ui.html(
                                    f'<details class="upd-ver-notes"><summary>Release notes {icon("expand_more")}</summary>'
                                    f'<div class="upd-notes">{release_notes_html(notes)}</div></details>',
                                    sanitize=False,
                                ).classes("contents")
                        row.on("click", lambda _e, r=release: select(r))
                ui.html(
                    f'<div class="upd-callout">{icon("info_outline")}<div><b>Before you go back</b><ul>'
                    "<li>Your template settings are merged into the older version. Settings it doesn't have are removed.</li>"
                    "<li>A copy of your current settings is saved in <code>data/update_backups</code>.</li>"
                    "<li>Later update checks will offer the newest version again.</li></ul></div></div>",
                    sanitize=False,
                )
        with self.foot:
            ui.html(f'<span class="upd-note">{icon("info")}Only changed files are downloaded</span>', sanitize=False)
            outline_button("Cancel", lambda: on_cancel())
            first = releases[0]["latest_version"] if releases else ""
            install_btn = primary_button(
                f"Install {first}" if first else "Install",
                lambda: on_install(selected["release"]) if selected["release"] else None,
                icon="history",
            )
            if not releases:
                install_btn.set_visibility(False)

    # ----- progress -----------------------------------------------------------

    def show_progress(self, version: str, *, downgrade: bool, on_cancel: Callable[[], None]) -> None:
        self._reset("progress")
        if downgrade:
            self._set_head(
                _badge_html("history"),
                f"Going back to {version}",
                f"Keep Mycelian open. It restarts by itself when {version} is ready.",
                closable=False,
            )
        else:
            self._set_head(
                _logo_html(),
                f"Updating to {version}",
                "Keep Mycelian open. It restarts by itself when the update is ready.",
                closable=False,
            )
        layout = _step_layout()
        p = self._p
        with self.body:
            with ui.element("div").classes("upd-overall"):
                with ui.element("div").classes("upd-overall-top"):
                    with ui.element("div").classes("upd-pct-wrap"):
                        p["pct"] = ui.label("0").classes("upd-pct")
                        ui.label("%").classes("upd-pct-sign")
                    p["overall"] = ui.html("", sanitize=False).classes("upd-overall-label")
                    with ui.element("div").classes("upd-eta") as eta:
                        p["eta_box"] = eta
                        p["eta"] = ui.label("Estimating time left…")
                        p["clock"] = ui.label("").classes("upd-eta-clock")
                segs = "".join(f'<i class="upd-seg" style="left:{end * 100:.2f}%"></i>' for *_, end in layout[:-1])
                with ui.element("div").classes("upd-bar") as bar:
                    p["bar"] = bar
                    p["fill"] = ui.element("div").classes("upd-bar-fill").style("width: 0%")
                    ui.html(segs, sanitize=False).classes("contents")
            p["steps"] = []
            with ui.element("div").classes("upd-steps"):
                for number, (_phase, title, _s, _e) in enumerate(layout, start=1):
                    row = ui.element("div").classes("upd-step")
                    with row:
                        dot = ui.html(f'<div class="upd-dot">{number}</div>', sanitize=False)
                        ui.label(title)
                        right = ui.label("").classes("upd-step-right")
                        detail = ui.element("div").classes("upd-step-detail")
                        with detail:
                            with ui.element("div").classes("upd-bar is-thin") as thin:
                                step_fill = ui.element("div").classes("upd-bar-fill").style("width: 0%")
                            file_label = ui.html("", sanitize=False).classes("upd-step-file")
                            meta = ui.html("", sanitize=False).classes("upd-step-meta")
                        detail.set_visibility(False)
                    p["steps"].append(
                        {"row": row, "dot": dot, "right": right, "detail": detail, "thin": thin,
                         "fill": step_fill, "file": file_label, "meta": meta, "number": number}
                    )
            p["extra"] = ui.element("div").classes("contents")
        with self.foot:
            ui.html(f'<span class="upd-note">{icon("lock_outline")}Nothing is changed until the restart</span>', sanitize=False)
            p["cancel"] = outline_button("Cancel update" if not downgrade else "Cancel", lambda: on_cancel(), icon="close")

    def _put(self, key: str, value: Any, apply: Callable[[Any], None]) -> None:
        """Send a change to the browser only when it differs from what is shown."""
        if self._rendered.get(key) == value:
            return
        self._rendered[key] = value
        apply(value)

    def render_progress(self, state: Dict[str, Any], *, error: str = "") -> None:
        p = self._p
        if not p:
            return
        count = int(state.get("step_count") or len(update_sync.UPDATE_STEPS))
        current = int(state.get("step_index") or 1)
        total = float(state.get("total_progress") or 0.0)
        step = float(state.get("progress") or 0.0)
        pct = int(total * 100) if total < 1.0 else 100

        self._put("pct", str(pct), p["pct"].set_text)
        lead = "stopped at" if error else "complete ·"
        self._put("overall", f"{lead} <b>Step {current} of {count}</b>", p["overall"].set_content)
        self._put("fill", f"width: {total * 100:.1f}%", lambda v: p["fill"].style(replace=v))
        self._put("bar", "upd-bar is-error" if error else "upd-bar", lambda v: p["bar"].classes(replace=v))
        if error:
            self._put("eta", "Failed", p["eta"].set_text)
            self._put("clock", "", p["clock"].set_text)
            self._put("eta_box", "upd-eta is-error", lambda v: p["eta_box"].classes(replace=v))
        else:
            sentence, clock = format_eta(state.get("eta_seconds"))
            self._put("eta", sentence, p["eta"].set_text)
            self._put("clock", clock, p["clock"].set_text)

        for row in p["steps"]:
            number = row["number"]
            key = f"step{number}"
            if number < current:
                kind, dot, right = "is-done", icon("check"), self._done_note(number, state)
            elif number == current and error:
                kind, dot, right = "is-error", icon("priority_high"), "Failed"
            elif number == current:
                kind, dot, right = "is-active", icon("autorenew", "upd-spin"), f"{int(step * 100)}%"
            else:
                kind, dot, right = "", str(number), ""
            self._put(f"{key}.cls", f"upd-step {kind}".strip(), lambda v, r=row: r["row"].classes(replace=v))
            self._put(f"{key}.dot", dot, lambda v, r=row: r["dot"].set_content(f'<div class="upd-dot">{v}</div>'))
            self._put(f"{key}.right", right, lambda v, r=row: r["right"].set_text(v))
            active = number == current
            self._put(f"{key}.open", active, lambda v, r=row: r["detail"].set_visibility(v))
            if not active:
                continue
            self._put(f"{key}.thin", not error, lambda v, r=row: r["thin"].set_visibility(v))
            self._put(f"{key}.fill", f"width: {step * 100:.1f}%", lambda v, r=row: r["fill"].style(replace=v))
            file_text = html.escape(error or str(state.get("label") or ""))
            self._put(f"{key}.file", file_text, lambda v, r=row: r["file"].set_content(v))
            self._put(f"{key}.meta", "" if error else self._download_meta(state), lambda v, r=row: r["meta"].set_content(v))

    @staticmethod
    def _done_note(number: int, state: Dict[str, Any]) -> str:
        phase = update_sync.UPDATE_STEPS[number - 1][0]
        if phase == "comparing" and state.get("files_checked"):
            changed = int(state.get("files_total") or 0)
            return f"{changed} of {int(state['files_checked'])} files changed"
        if phase == "downloading" and state.get("bytes_total") and state.get("files_total"):
            return f"{format_bytes(state.get('bytes_done') or state['bytes_total'])} downloaded"
        if phase == "downloading" and not state.get("files_total"):
            return "Nothing to download"
        return "Done"

    @staticmethod
    def _download_meta(state: Dict[str, Any]) -> str:
        if state.get("phase") != "downloading":
            return ""
        parts = []
        speed = float(state.get("speed_bps") or 0.0)
        if speed > 0:
            parts.append(f'<span>{icon("speed")}<b>{format_speed(speed)}</b></span>')
        total = int(state.get("bytes_total") or 0)
        if total > 1:
            parts.append(
                f'<span>{icon("downloading")}<b>{format_bytes(state.get("bytes_done") or 0)}</b> of {format_bytes(total)}</span>'
            )
        return "".join(parts)

    # ----- outcomes -----------------------------------------------------------

    def show_failed(
        self,
        state: Dict[str, Any],
        message: str,
        current_version: str,
        *,
        on_retry: Optional[Callable[[], None]],
        details: str = "",
    ) -> None:
        """Keep the step list, mark the failed step and offer a retry."""
        if self.view != "progress":
            self.show_progress("", downgrade=False, on_cancel=lambda: None)
        self.view = "failed"
        self.head.clear()
        unchanged = f"Mycelian {current_version} is unchanged." if current_version else "Mycelian is unchanged."
        self._set_head(_badge_html("error_outline", "is-error"), "The update didn't finish", unchanged)
        self.render_progress(state, error=message or "Something went wrong.")
        with self._p["extra"]:
            ui.html(
                f'<div class="upd-callout">{icon("info_outline")}<div>Check your internet connection and try again. '
                "Nothing was installed, so you can keep using this version.</div></div>",
                sanitize=False,
            )
        self.foot.clear()
        with self.foot:
            text = details or message
            if text:
                ui.button("Copy details", icon="content_copy", on_click=lambda: ui.clipboard.write(text)).props(
                    "flat no-caps dense size=sm"
                ).classes("upd-link")
            else:
                ui.element("span").classes("upd-note")
            outline_button("Close", self.dismiss)
            if on_retry is not None:
                primary_button("Try again", lambda: on_retry(), icon="refresh")

    def show_message(self, title: str, message: str, *, kind: str = "", art: str = "info_outline") -> None:
        """A finished state with one Close button (cancelled, already current, load errors)."""
        self._reset("message")
        self._set_head(_badge_html(art, kind), title, message)
        self.body.set_visibility(False)
        with self.foot:
            ui.element("span").classes("upd-note")
            outline_button("Close", self.dismiss)

    def show_restarting(self) -> None:
        try:
            self.title_label.set_text("Restarting Mycelian")
            self.sub_label.set_text("Closing Mycelian to finish installing. It opens again in a moment.")
            cancel = self._p.get("cancel")
            if cancel is not None:
                cancel.set_visibility(False)
        except Exception as e:
            logger.debug("UpdateDialog: could not show the restart state: %s", e)


def update_sync_min_version() -> str:
    from ..updater import MIN_DOWNGRADE_VERSION

    return MIN_DOWNGRADE_VERSION

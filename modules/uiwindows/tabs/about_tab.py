# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Settings → About.

Three blocks: the app header (version, build, update status and actions), a log
health check (actionable errors from mycelian.log), and the browser source URL
cards. Actions that already exist on ``SettingsUI`` (update check, changelog,
copy, Add to OBS, open logs folder) are called through ``self.host``.
"""

from __future__ import annotations

import base64
import html
import io
import logging
import platform
import sys
import time
import webbrowser
from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from nicegui import run, ui

from ...build_info import resolve_build_number
from ...ui_buttons import outline_button, primary_button
from ...ui_timer import layout_schedule
from ..service_brand_icons import SERVICE_BRAND_SVG
from .about_styles import ABOUT_CSS

logger = logging.getLogger(__name__)

GITHUB_URL = "https://github.com/mushroomsuprise/mycelian"


def _icon(name: str, extra: str = "") -> str:
    return f'<span class="abt-icon {extra}" aria-hidden="true">{html.escape(name)}</span>'


def _ago(ts: Optional[float]) -> str:
    if not ts:
        return ""
    delta = max(0, time.time() - ts)
    if delta < 45:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60) or 1}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return datetime.fromtimestamp(ts).strftime("%b %d, %H:%M")


def _log_ts(stamp: str) -> Optional[float]:
    try:
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S,%f").timestamp()
    except (TypeError, ValueError):
        return None


def _friendly_date(value: str) -> str:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value)[:19], fmt).strftime("%b %d, %Y")
        except (TypeError, ValueError):
            continue
    return str(value or "unknown")


def _system_line() -> str:
    os_name = {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, platform.system() or sys.platform)
    os_version = platform.mac_ver()[0] if sys.platform == "darwin" else platform.release()
    return f"{os_name} {os_version} ({platform.machine()}) · Python {platform.python_version()}"


@lru_cache(maxsize=1)
def _logo_data_uri() -> Optional[str]:
    try:
        from PIL import Image

        from ...path_utils import get_assets_path

        with Image.open(get_assets_path("default_assets/images/Mycelian.png")) as im:
            im = im.convert("RGBA")
            im.thumbnail((144, 144))
            buf = io.BytesIO()
            im.save(buf, "PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.debug("About: could not load app logo: %s", e)
        return None


class AboutTab:
    def __init__(self, host: Any):
        self.host = host
        self.update_box = None
        self._update_sig: Optional[Tuple] = None
        self._update_timer = None
        self.health_box = None
        self.sources_box = None
        self.sources_count = None
        self._cards: List[Tuple[Any, str]] = []
        self._filter = ""

    # ----- build ----------------------------------------------------------

    def build(self, container) -> None:
        from ...help_system.help_render import inject_page_stylesheet, promote_color_importance, scope_selectors

        inject_page_stylesheet("mycelian-about", promote_color_importance(scope_selectors(ABOUT_CSS, ".abt")))
        with container:
            with ui.element("div").classes("abt"):
                self._build_hero()
                self._build_health()
                self._build_sources()
        layout_schedule(0.1, self.refresh_logs_async, once=True)
        layout_schedule(0.1, self.refresh_sources, once=True)
        self._update_timer = layout_schedule(1.5, self._tick_update, active=True)

    def _build_hero(self) -> None:
        s = self.host.app_settings
        version = str(getattr(s, "version", "") or "dev")
        build = resolve_build_number(getattr(s, "build_number", "dev"))
        built = _friendly_date(getattr(s, "build_date", ""))
        logo = _logo_data_uri()

        with ui.element("div").classes("abt-hero"):
            if logo:
                ui.html(f'<img class="abt-logo" src="{logo}" alt="">', sanitize=False)
            else:
                ui.html('<div class="abt-logo abt-logo-fallback">M</div>', sanitize=False)

            with ui.element("div").classes("min-w-0"):
                ui.label("Mycelian").classes("abt-name")
                ui.label("Alerts, overlays, chatbot and automation for your stream").classes("abt-tagline")
                with ui.element("div").classes("abt-chips"):
                    ui.html(f'<span class="abt-chip">{_icon("sell")}Version <b>{html.escape(version)}</b></span>', sanitize=False)
                    chip = ui.element("button").classes("abt-chip is-copy").props(f'type=button title="Copy build {html.escape(build)}"')
                    with chip:
                        ui.html(f'{_icon("tag")}Build <code>{html.escape(build)}</code>{_icon("content_copy")}', sanitize=False).classes("contents")
                    chip.on("click", lambda _e: self.host._copy_url_to_clipboard(build, "Build number copied"))
                    ui.html(f'<span class="abt-chip">{_icon("event")}Built {html.escape(built)}</span>', sanitize=False)

            with ui.element("div").classes("abt-hero-actions"):
                primary_button("Check for updates", self._check_updates, icon="system_update")
                outline_button("What's new", self.host.show_changelog_modal, icon="history")
                ui.button(icon="open_in_new", on_click=lambda: webbrowser.open(GITHUB_URL)).props(
                    "flat round dense"
                ).tooltip("Mycelian on GitHub")

            self.update_box = ui.element("div").classes("abt-update")
        self._render_update(force=True)

    # ----- update status --------------------------------------------------

    @staticmethod
    def _manager():
        try:
            from ... import updater

            return updater.update_manager
        except Exception:
            return None

    def _check_updates(self) -> None:
        self.host.check_for_updates_manual()
        layout_schedule(0.2, lambda: self._render_update(force=True), once=True)

    def _update_state(self) -> Tuple[str, str, str, str, Optional[str]]:
        """(css state, icon, title, subtitle, available version)."""
        mgr = self._manager()
        auto = bool(getattr(self.host.app_settings, "auto_update", False))
        auto_note = "Automatic checks are on" if auto else "Automatic checks are off (App Settings)"
        if mgr is None:
            return "", "help_outline", "Update status unavailable", auto_note, None
        if mgr.is_checking:
            return "is-checking", "autorenew", "Checking for updates…", "Contacting GitHub", None
        result = mgr.last_check_result
        when = _ago(mgr.last_check_at)
        if result == "available":
            info = mgr.available_update or {}
            ver = str(info.get("latest_version") or "")
            return "is-available", "new_releases", f"Mycelian {ver} is available", f"Found {when} · {auto_note}", ver
        if result == "up_to_date":
            return "is-ok", "check_circle", "You're on the latest version", f"Checked {when} · {auto_note}", None
        if result == "error":
            err = (mgr.last_check_error or "").strip()
            return "is-error", "cloud_off", "Couldn't check for updates", f"{err[:120]} · {when}" if err else when, None
        return "", "schedule", "Not checked yet this session", auto_note, None

    def _render_update(self, force: bool = False) -> None:
        box = self.update_box
        if box is None:
            return
        state = self._update_state()
        # Re-render when the state or its relative time text changes.
        if not force and state == self._update_sig:
            return
        self._update_sig = state
        css, icon, title, sub, available = state
        box.classes(replace=f"abt-update {css}".strip())
        box.clear()
        with box:
            ui.html(f'<div class="abt-update-icon">{_icon(icon)}</div>', sanitize=False)
            with ui.column().classes("gap-0 grow min-w-0"):
                ui.label(title).classes("abt-update-title")
                ui.label(sub).classes("abt-update-sub")
            if available:
                primary_button("Update now", self._open_update, icon="download")

    def _open_update(self) -> None:
        mgr = self._manager()
        if mgr is None or not mgr.show_available_update():
            self._check_updates()

    def _tick_update(self) -> None:
        if self.update_box is None or getattr(self.update_box, "is_deleted", False):
            if self._update_timer is not None:
                self._update_timer.active = False
            return
        self._render_update()

    # ----- log health -----------------------------------------------------

    def _build_health(self) -> None:
        with ui.element("div").classes("abt-card"):
            with ui.element("div").classes("abt-card-head"):
                with ui.column().classes("gap-0"):
                    ui.label("Health check").classes("abt-card-title")
                    ui.label("Recent errors from mycelian.log. Known harmless messages, like expired tokens, are skipped.").classes(
                        "abt-card-sub"
                    )
                ui.element("div").classes("abt-spacer")
                with ui.row().classes("items-center gap-1 no-wrap"):
                    ui.button(icon="content_paste", on_click=self._copy_report).props("flat round dense").tooltip(
                        "Copy a diagnostics report (version, system and these errors) for a bug report"
                    )
                    ui.button(icon="folder_open", on_click=self.host.open_logs_folder).props("flat round dense").tooltip(
                        "Open the logs folder"
                    )
                    ui.button(icon="refresh", on_click=self.refresh_logs_async).props("flat round dense").tooltip("Check the log again")
            self.health_box = ui.element("div")

    def _summary(self):
        from .. import settings as settings_module  # get_actionable_errors is imported there

        return settings_module.get_actionable_errors()

    def refresh_logs(self) -> None:
        box = self.health_box
        if box is None:
            return
        box.clear()
        try:
            summary = self._summary()
        except Exception as e:
            self._render_log_error(box, e)
            return
        self._render_logs(box, summary)

    async def refresh_logs_async(self) -> None:
        """Same as :meth:`refresh_logs`, reading and parsing the log tail on a worker
        thread; the card is cleared and rebuilt once the result is ready."""
        box = self.health_box
        if box is None:
            return
        try:
            summary = await run.io_bound(self._summary)
        except Exception as e:
            box.clear()
            self._render_log_error(box, e)
            return
        if summary is None or getattr(box, "is_deleted", False):
            return
        box.clear()
        self._render_logs(box, summary)

    @staticmethod
    def _render_log_error(box, e: Exception) -> None:
        logger.error("Error reading log file: %s", e, exc_info=True)
        with box:
            ui.html(f'<span class="abt-pill is-error">{_icon("error")}Could not read the log file</span>', sanitize=False)

    def _render_logs(self, box, summary) -> None:
        with box:
            if summary.total_count == 0:
                with ui.element("div").classes("abt-clean"):
                    ui.html(_icon("verified"), sanitize=False)
                    with ui.column().classes("gap-0"):
                        ui.label("No problems found").classes("font-semibold")
                        ui.label(f"Checked {datetime.now():%H:%M}. Nothing needs your attention.").classes("abt-card-sub")
                return

            noun = "error" if summary.total_count == 1 else "errors"
            unique = len(summary.unique_errors)
            with ui.row().classes("items-center gap-3 mb-2"):
                ui.html(f'<span class="abt-pill is-warn">{_icon("report")}{summary.total_count} {noun} in the log</span>', sanitize=False)
                if summary.total_count > unique:
                    ui.label(f"{unique} different message{'s' if unique != 1 else ''}, newest first").classes("abt-card-sub")
            with ui.element("div").classes("abt-errors"):
                for item in summary.unique_errors:
                    self._error_row(item)
            ui.label("Click an error to see the full message. Copy it into a bug report with the clipboard button above.").classes(
                "abt-foot-note"
            )

    def _error_row(self, item) -> None:
        critical = getattr(item, "level", "ERROR") == "CRITICAL"
        source = str(getattr(item, "source", "") or "")
        source = source.split(".")[-1] if source.startswith("modules.") else source
        seen = _ago(_log_ts(item.last_seen)) or item.last_seen
        details = list(getattr(item, "details", []) or [])
        meta = []
        if source:
            meta.append(f"<span>{_icon('code')}{html.escape(source)}</span>")
        meta.append(f"<span title=\"{html.escape(item.last_seen)}\">{_icon('schedule')}{html.escape(seen)}</span>")
        if critical:
            meta.append(f"<span>{_icon('priority_high')}Critical</span>")
        count = f'<span class="abt-count">×{item.count:,}</span>' if item.count > 1 else ""
        full = item.message + ("\n" + "\n".join(details) if details else "")

        el = ui.element("details").classes("abt-err" + (" is-critical" if critical else ""))
        with el:
            with ui.element("summary"):
                ui.html(
                    f'{_icon("error" if critical else "warning", "abt-err-icon")}'
                    f'<div class="min-w-0"><div class="abt-err-msg">{html.escape(item.message)}</div>'
                    f'<div class="abt-err-meta">{"".join(meta)}</div></div>'
                    f"{count}",
                    sanitize=False,
                ).classes("contents")
            with ui.element("div").classes("abt-err-body"):
                if details:
                    ui.html(f"<pre>{html.escape(chr(10).join(details))}</pre>", sanitize=False)
                outline_button("Copy error", lambda t=full: self.host._copy_url_to_clipboard(t, "Error copied"), icon="content_copy").props(
                    "dense"
                ).classes("self-start")

    def _copy_report(self) -> None:
        s = self.host.app_settings
        lines = [
            f"Mycelian {getattr(s, 'version', '?')} (build {resolve_build_number(getattr(s, 'build_number', 'dev'))}, "
            f"built {getattr(s, 'build_date', '?')})",
            _system_line(),
        ]
        mgr = self._manager()
        if mgr is not None and mgr.last_check_result:
            lines.append(f"Update check: {mgr.last_check_result}")
        try:
            summary = self._summary()
            lines.append(f"Log errors: {summary.total_count}")
            for item in summary.unique_errors:
                lines.append(f"- [{item.last_seen}] {getattr(item, 'level', 'ERROR')} {getattr(item, 'source', '')}: {item.message}"
                             + (f" (x{item.count})" if item.count > 1 else ""))
                lines.extend("    " + d for d in (getattr(item, "details", []) or [])[:12])
        except Exception as e:
            lines.append(f"Log errors: unavailable ({e})")
        self.host._copy_url_to_clipboard("\n".join(lines), "Diagnostics report copied")

    # ----- source URLs ----------------------------------------------------

    def _build_sources(self) -> None:
        with ui.element("div").classes("abt-card"):
            with ui.element("div").classes("abt-card-head"):
                with ui.column().classes("gap-0"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label("Browser source URLs").classes("abt-card-title")
                        self.sources_count = ui.label("").classes("abt-count-chip")
                    ui.label("Add these to OBS or any streaming app as Browser Sources. Click a URL to copy it.").classes("abt-card-sub")
                ui.element("div").classes("abt-spacer")
                search = ui.input(placeholder="Filter sources").props("outlined dense clearable").classes("abt-search")
                with search.add_slot("prepend"):
                    ui.icon("search").classes("text-sm")
                search.on_value_change(lambda e: self._apply_filter(str(e.value or "")))
                ui.button(icon="refresh", on_click=self.refresh_sources).props("flat round dense").tooltip("Reload the source list")
            self.sources_box = ui.element("div")

    @staticmethod
    def _source_urls() -> Optional[List[Dict[str, Any]]]:
        from ... import alert_processor, web_engine

        engine = web_engine.web_engine_instance or getattr(alert_processor, "web_engine_instance", None)
        if engine is None:
            return None
        urls = engine.get_available_source_urls() or []
        shown = [u for u in urls if u.get("type") != "system_info" and not str(u.get("name", "")).startswith("_")]
        # Containers first (they bundle several overlays), then alphabetical.
        return sorted(shown, key=lambda u: (u.get("type") != "container", str(u.get("title") or u.get("name")).lower()))

    def refresh_sources(self) -> None:
        box = self.sources_box
        if box is None:
            return
        box.clear()
        self._cards = []
        try:
            urls = self._source_urls()
        except Exception as e:
            logger.error("Error loading source URLs: %s", e, exc_info=True)
            urls = None
            error = str(e)
        else:
            error = ""
        with box:
            if urls is None:
                ui.html(
                    f'<div class="abt-empty">{_icon("cloud_off")}The overlay server is not running, so there are no URLs to show yet.'
                    + (f"<br>{html.escape(error)}" if error else "")
                    + "</div>",
                    sanitize=False,
                )
                self._set_count(0)
                return
            if not urls:
                ui.html(f'<div class="abt-empty">{_icon("web_asset_off")}No browser sources available.</div>', sanitize=False)
                self._set_count(0)
                return
            with ui.element("div").classes("source-url-cards-grid"):
                for info in urls:
                    self._source_card(info)
        self._set_count(len(urls))
        if self._filter:
            self._apply_filter(self._filter)

    def _set_count(self, n: int) -> None:
        if self.sources_count is not None:
            self.sources_count.set_text(f"{n} source{'s' if n != 1 else ''}")
            self.sources_count.set_visibility(n > 0)

    def _source_card(self, info: Dict[str, Any]) -> None:
        kind = info.get("type")
        title = info.get("title") or self.host._format_source_url_title(info["name"])
        url = info["url"]
        parts = urlsplit(url)
        host_part = f"{parts.scheme}://{parts.netloc}"
        path_part = url[len(host_part):] or "/"

        badges = []
        if kind == "container":
            n = int(info.get("template_count") or 0)
            badges.append(f'<span class="source-url-card-badge is-accent">{_icon("layers")}Container</span>')
            badges.append(f'<span class="source-url-card-badge">{n} template{"s" if n != 1 else ""}</span>')
        else:
            n = int(info.get("control_count") or 0)
            if n:
                badges.append(f'<span class="source-url-card-badge">{_icon("tune")}{n} control{"s" if n != 1 else ""}</span>')
        icon = "dashboard_customize" if kind == "container" else ("tune" if info.get("control_count") else "web")

        card = ui.element("div").classes("source-url-card" + (" is-container" if kind == "container" else ""))
        with card:
            with ui.element("div").classes("source-url-card-header"):
                ui.html(f'<div class="source-url-card-type">{_icon(icon)}</div>', sanitize=False)
                with ui.element("div").classes("source-url-card-heading"):
                    ui.label(title).classes("source-url-card-title").tooltip(info.get("description") or title)
                    if badges:
                        ui.html(f'<div class="source-url-card-badges">{"".join(badges)}</div>', sanitize=False)
                with ui.element("div").classes("source-url-card-actions"):
                    copy_btn = ui.button(icon="content_copy").props("outline color=primary").tooltip("Copy URL")
                    copy_btn.on("click", lambda _e, b=copy_btn: self._copy_source(url, b))
                    obs_btn = ui.button(on_click=lambda _=None, i=info: self.host._prompt_add_source_to_obs(i)).props("outline color=primary")
                    with obs_btn:
                        ui.html(SERVICE_BRAND_SVG["obs"], tag="span", sanitize=False).classes("source-url-card-obs-icon")
                    obs_btn.tooltip("Add to OBS")
            row = ui.element("div").classes("source-url-card-url").props(f'title="Copy {html.escape(url)}"')
            with row:
                ui.html(
                    f'<span class="abt-url-text"><span class="abt-url-host">{html.escape(host_part)}</span>'
                    f'<span class="abt-url-path">{html.escape(path_part)}</span></span>{_icon("content_copy")}',
                    sanitize=False,
                ).classes("contents")
            row.on("click", lambda _e, b=copy_btn: self._copy_source(url, b))
        self._cards.append((card, f"{title} {info.get('name', '')} {url}".lower()))

    def _copy_source(self, url: str, button) -> None:
        self.host._copy_url_to_clipboard(url)
        try:
            button.props("icon=check").classes(add="is-copied")

            def restore():
                if not getattr(button, "is_deleted", False):
                    button.props("icon=content_copy").classes(remove="is-copied")

            layout_schedule(1.5, restore, once=True)
        except Exception:
            pass

    def _apply_filter(self, text: str) -> None:
        self._filter = text.strip().lower()
        for card, haystack in self._cards:
            card.set_visibility(not self._filter or self._filter in haystack)

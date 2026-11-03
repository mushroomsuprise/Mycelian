# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Settings tab: Games Tracker (PSN and Steam libraries, trophy/achievement progress, export image)."""

from __future__ import annotations

import base64
import contextlib
import io
import logging
import os
import time
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence

from nicegui import run, ui

from ...game_tracker import covers
from ...game_tracker.models import PSN, STEAM, GameEvent, TrackedGame, TrackerSettings, clean_milestones, grade_counts
from ...game_tracker.service import get_service
from ...notification_engine import nav_actions_settings, notify
from ...ui_buttons import outline_button, primary_button
from ...ui_timer import layout_schedule
from .integration_ui import (
    IntegrationPage,
    brand_svg,
    esc,
    field,
    fields,
    hint,
    icon_html,
    section,
    switch_row,
    tool_row,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 60
_STYLE_ID = "mycelian-games-tracker"
_HOOKS_STYLE_ID = "mycelian-game-hooks"
_MEDIA_ROUTE = "/_mycelian_game_covers"
_media_route_dir: Optional[str] = None

# Trophy grade colours are fixed (they mean the grade, like brand colours mean the service).
GAMES_TRACKER_CSS = """
.gtr-bar-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.gtr-bar-row .itg-sec-head { margin-bottom: 0; }
.gtr-bar-row .q-btn { min-height: 30px; font-size: .78rem; }
.gtr-sub { font-size: .7rem; color: var(--color-text-muted); white-space: nowrap; }
.gtr-meta { border: 1px solid var(--color-border-subtle); border-radius: 10px; background: var(--color-bg-base); border-top: 1px solid var(--color-border-subtle); }
.gtr-meta .v { font-size: 1rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.gtr-sync { display: flex; gap: 12px; align-items: flex-start; margin-bottom: 12px; padding: 11px 12px; border-radius: 9px; font-size: .78rem;
  color: var(--color-text-secondary); background: color-mix(in srgb, var(--color-info) 9%, transparent);
  border: 1px solid color-mix(in srgb, var(--color-info) 32%, transparent); }
.gtr-sync > .itg-icon { font-size: 20px; color: var(--color-info); }
.gtr-sync b { color: var(--color-text-primary); }
.gtr-tools { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
.gtr-search { flex: 1 1 240px; max-width: 340px; }
.gtr-select { min-width: 160px; }
.gtr-seg { display: inline-flex; padding: 2px; border-radius: 9px; background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); }
.gtr-seg .q-btn { min-height: 30px; padding: 0 10px; border-radius: 7px; font-size: .76rem; font-weight: 600; color: var(--color-text-secondary); }
.gtr-seg .q-btn.is-on { background: var(--color-primary-light); color: var(--color-text-primary); box-shadow: 0 0 0 1px var(--color-border-accent) inset; }
.gtr-seg.is-full { display: flex; width: 100%; }
.gtr-seg.is-full .q-btn { flex: 1 1 0; }
.gtr-cover { position: relative; width: 60px; height: 60px; border-radius: 9px; flex: 0 0 auto; display: flex; align-items: center; justify-content: center;
  font-weight: 800; font-size: .95rem; color: #fff; box-shadow: 0 0 0 1px var(--color-border-subtle); }
.gtr-cover-art { position: absolute; inset: 0; overflow: hidden; border-radius: inherit; background: var(--color-bg-base); }
.gtr-cover-art img { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
.gtr-cover-art img.bg { object-fit: cover; filter: blur(6px) brightness(.55); transform: scale(1.2); }
.gtr-cover-art img.fg { object-fit: contain; }
.gtr-cover.is-lg { width: 76px; height: 76px; font-size: 1.15rem; border-radius: 11px; }
.gtr-pb { position: absolute; right: -5px; bottom: -5px; width: 21px; height: 21px; border-radius: 6px; display: flex; align-items: center;
  justify-content: center; background: var(--color-bg-elevated); box-shadow: 0 0 0 1px var(--color-border-default); }
.gtr-pb svg { width: 13px; height: 13px; }
.gtr-bar { height: 5px; border-radius: 999px; background: var(--color-bg-surface); overflow: hidden; margin-top: 9px; }
.gtr-bar > span { display: block; height: 100%; border-radius: 999px; background: var(--color-primary); }
.is-success .gtr-bar > span, .gtr-bar.is-done > span { background: var(--color-success); }
.gtr-bar.is-info > span { background: var(--color-info); }
.gtr-pct { flex: 0 0 auto; min-width: 54px; text-align: right; font-size: 1.2rem; font-weight: 750; font-variant-numeric: tabular-nums; color: var(--color-text-primary); }
.gtr-pct small { font-size: .66rem; font-weight: 600; color: var(--color-text-muted); margin-left: 1px; }
.gtr-grades { display: flex; align-items: center; gap: 12px; flex: 1 1 auto; min-width: 0; font-variant-numeric: tabular-nums; }
.gtr-grade { display: inline-flex; align-items: center; gap: 5px; font-size: .74rem; font-weight: 600; color: var(--color-text-secondary); }
.gtr-grade .gtr-trophy { width: 17px; height: 17px; flex: 0 0 auto; object-fit: contain; filter: drop-shadow(0 1px 1px rgba(0, 0, 0, .35)); }
.gtr-boxes .k .gtr-trophy { width: 15px; height: 15px; }
.gtr-grade .itg-icon { font-size: 15px; color: var(--color-text-muted); }
.gtr-mini.itg-pill { height: 20px; padding: 0 7px; font-size: .64rem; }
.itg-hook.is-hidden .itg-hook-top { opacity: .5; }
.gtr-card .itg-hook-top { gap: 13px; padding: 12px; }
.gtr-card .gtr-text { flex: 1 1 auto; min-width: 0; }
.gtr-more { display: flex; justify-content: center; margin-top: 12px; }
.gtr-none { padding: 26px 12px; text-align: center; font-size: .8rem; color: var(--color-text-muted); border: 1px dashed var(--color-border-default); border-radius: 10px; }
.gtr-empty { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 48px 16px; text-align: center; }
.gtr-empty > .itg-icon { font-size: 46px; color: var(--color-text-muted); opacity: .7; }
.gtr-empty .t { font-size: 1rem; font-weight: 650; color: var(--color-text-secondary); }
.gtr-empty .s { font-size: .78rem; color: var(--color-text-muted); max-width: 420px; line-height: 1.45; }
.gtr-optgrid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 22px; }
@media (max-width: 900px) { .gtr-optgrid { grid-template-columns: 1fr; } }
.gtr-unit { display: flex; align-items: center; gap: 8px; font-size: .74rem; color: var(--color-text-muted); }
.gtr-num { width: 96px; }
.gtr-token { display: inline-flex; align-items: center; gap: 2px; height: 26px; padding: 0 2px 0 9px; border-radius: 6px; font-size: .74rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--color-text-secondary); background: var(--color-bg-base);
  border: 1px solid var(--color-border-subtle); }
.gtr-tokens { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
/* Drawer */
.gtr-drawer { width: 460px; max-width: 100vw; height: 100%; display: flex; flex-direction: column; background: var(--color-bg-elevated);
  border-left: 1px solid var(--color-border-default); color: var(--color-text-primary); }
.gtr-dhead { display: flex; gap: 14px; align-items: flex-start; padding: 16px 14px 14px; border-bottom: 1px solid var(--color-border-subtle); }
.gtr-dtitle { font-size: 1.08rem; font-weight: 700; line-height: 1.25; }
.gtr-dbody { flex: 1 1 auto; overflow-y: auto; }
.gtr-dbody > .itg-sec:first-child { border-top: none; }
.gtr-dfoot { display: flex; gap: 8px; align-items: center; padding: 10px 14px; border-top: 1px solid var(--color-border-subtle); background: var(--color-bg-surface); }
.gtr-dfoot .q-btn { min-height: 30px; font-size: .78rem; }
.gtr-danger.q-btn, .gtr-danger.q-btn .q-icon, .gtr-danger.q-btn span { color: var(--color-error); }
.gtr-big { display: flex; align-items: baseline; gap: 8px; }
.gtr-big b { font-size: 1.9rem; font-weight: 750; font-variant-numeric: tabular-nums; }
.gtr-boxes { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; margin-top: 12px; }
.gtr-boxes.is-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.gtr-boxes > div { padding: 7px 9px; border-radius: 8px; background: var(--color-bg-base); border: 1px solid var(--color-border-subtle); min-width: 0; }
.gtr-boxes .k { display: flex; align-items: center; gap: 5px; font-size: .6rem; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; color: var(--color-text-muted); }
.gtr-boxes .v { margin-top: 2px; font-size: .92rem; font-weight: 700; font-variant-numeric: tabular-nums; white-space: nowrap; }
.gtr-boxes .v small { font-size: .7rem; font-weight: 500; color: var(--color-text-muted); }
.gtr-minibar { width: 70px; height: 4px; border-radius: 999px; background: var(--color-bg-surface); overflow: hidden; flex: 0 0 auto; }
.gtr-minibar > span { display: block; height: 100%; background: var(--color-primary); }
.gtr-mono .q-field__native { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .78rem; }
/* Export dialog */
.gtr-modal { width: min(1280px, calc(100vw - 32px)); max-width: none !important; margin: 0 auto; height: min(880px, calc(100vh - 32px)); display: flex; flex-direction: column;
  padding: 0 !important; background: var(--color-bg-elevated); border: 1px solid var(--color-border-default); border-radius: 14px; overflow: hidden; }
.gtr-mhead { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-bottom: 1px solid var(--color-border-subtle); }
.gtr-mbody { flex: 1 1 auto; display: grid; grid-template-columns: 360px minmax(0, 1fr); min-height: 0; }
.gtr-mopts { overflow-y: auto; padding: 0 16px 16px; border-right: 1px solid var(--color-border-subtle); }
.gtr-mprev { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; padding: 18px; background: var(--color-bg-base); overflow: auto; min-width: 0; }
.gtr-mprev img { max-width: 100%; max-height: calc(100% - 28px); border-radius: 6px; box-shadow: 0 0 0 1px var(--color-border-default), 0 14px 34px rgba(0, 0, 0, .3); }
.gtr-mfoot { display: flex; align-items: center; gap: 8px; padding: 10px 14px; border-top: 1px solid var(--color-border-subtle); background: var(--color-bg-surface); }
.gtr-mfoot .q-btn { min-height: 30px; font-size: .78rem; }
@media (max-width: 900px) { .gtr-mbody { grid-template-columns: 1fr; } .gtr-mopts { border-right: none; border-bottom: 1px solid var(--color-border-subtle); max-height: 45vh; } }
.gtr-sheet { width: min(100%, 860px); border: 1px solid var(--color-border-default); border-radius: 8px; overflow: hidden;
  background: var(--color-bg-elevated); box-shadow: 0 14px 34px rgba(0, 0, 0, .3); }
.gtr-sheet-scroll { overflow: auto; max-height: 520px; }
.gtr-sheet table { width: 100%; border-collapse: collapse; font-size: .76rem; font-variant-numeric: tabular-nums; }
.gtr-sheet th { position: sticky; top: 0; text-align: left; padding: 7px 10px; font-weight: 700; color: var(--color-text-primary);
  background: color-mix(in srgb, var(--color-primary) 28%, var(--color-bg-surface)); white-space: nowrap; }
.gtr-sheet td { padding: 6px 10px; color: var(--color-text-secondary); border-top: 1px solid var(--color-border-subtle); white-space: nowrap;
  max-width: 280px; overflow: hidden; text-overflow: ellipsis; }
.gtr-sheet tr:nth-child(even) td { background: color-mix(in srgb, var(--color-text-primary) 3%, transparent); }
.gtr-sheet-more { padding: 6px 10px; font-size: .72rem; color: var(--color-text-muted); border-top: 1px solid var(--color-border-subtle); }
.gtr-sheet-tabs { display: flex; gap: 2px; padding: 0 8px; background: var(--color-bg-surface); border-top: 1px solid var(--color-border-subtle); }
.gtr-sheet-tabs span { padding: 5px 12px; font-size: .7rem; color: var(--color-text-muted); }
.gtr-sheet-tabs span.is-on { color: var(--color-text-primary); background: var(--color-bg-elevated); font-weight: 650;
  box-shadow: 0 2px 0 var(--color-primary) inset; }
.gtr-olabel { margin: 16px 0 6px; font-size: .66rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--color-text-muted); }
.gtr-osub { margin: -2px 0 8px; font-size: .72rem; line-height: 1.35; color: var(--color-text-muted); }
.gtr-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.gtr-chip.q-btn { min-height: 28px; padding: 0 10px; border-radius: 999px; border: 1px solid var(--color-border-default); font-size: .74rem; font-weight: 600;
  color: var(--color-text-secondary); }
.gtr-chip.q-btn.is-on { background: var(--color-primary-light); border-color: var(--color-border-accent); color: var(--color-text-primary); }
.gtr-picklist { display: flex; flex-direction: column; max-height: 168px; overflow: auto; margin-top: 8px; padding: 4px 10px;
  border: 1px solid var(--color-border-subtle); border-radius: 8px; background: var(--color-bg-base); }
"""

_COVER_TONES = ("#5b4a2e", "#7a2e2e", "#2d5a8c", "#4a3a5c", "#3a5560", "#2e5a46", "#2e4a6b", "#6b4a2e", "#5c5a2e", "#3d2b2b")
_GRADE_ORDER = (("platinum", "Platinum"), ("gold", "Gold"), ("silver", "Silver"), ("bronze", "Bronze"))
STATUS_OPTIONS = {"all": "All games", "progress": "In progress", "done": "Completed", "hidden": "Hidden"}
SORT_OPTIONS = {"recent": "Recently played", "progress": "Most complete", "name": "Name A–Z"}
PLATFORM_FILTERS = (("all", "All"), (PSN, "PlayStation"), (STEAM, "Steam"))


# ----- pure helpers (tested directly) -----


def ago(ts: float, now: Optional[float] = None) -> str:
    """'just now', '5 minutes ago', 'yesterday', '3 weeks ago'..."""
    if not ts:
        return "never"
    secs = max(0.0, (now if now is not None else time.time()) - float(ts))
    if secs < 90:
        return "just now"
    steps = (("minute", 60, 60), ("hour", 3600, 24), ("day", 86400, 7), ("week", 7 * 86400, 5), ("month", 30 * 86400, 12))
    for unit, size, limit in steps:
        n = int(secs // size)
        if n < limit:
            if unit == "day" and n == 1:
                return "yesterday"
            return f"{max(1, n)} {unit}{'s' if max(1, n) != 1 else ''} ago"
    years = max(1, int(secs // (365 * 86400)))
    return f"{years} year{'s' if years != 1 else ''} ago"


def initials(title: str) -> str:
    words = [w for w in str(title or "").replace(":", " ").split() if w[:1].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def cover_tone(key: str) -> str:
    return _COVER_TONES[sum(ord(c) for c in key) % len(_COVER_TONES)]


def filter_games(games: Sequence[TrackedGame], *, q: str = "", platform: str = "all", status: str = "all", sort: str = "recent") -> List[TrackedGame]:
    q = (q or "").strip().casefold()
    out = []
    for g in games:
        if platform != "all" and g.platform != platform:
            continue
        if q and q not in g.display_title.casefold() and q not in g.title.casefold() and q != g.game_id.casefold():
            continue
        if status == "hidden":
            if not g.hidden:
                continue
        elif g.hidden:
            continue
        elif status == "progress" and g.is_complete:
            continue
        elif status == "done" and not g.is_complete:
            continue
        out.append(g)
    if sort == "name":
        out.sort(key=lambda g: g.display_title.casefold())
    elif sort == "progress":
        out.sort(key=lambda g: (-g.progress, g.display_title.casefold()))
    else:
        out.sort(key=lambda g: -g.last_played)
    return out


def cover_src(platform: str, game_id: str) -> Optional[str]:
    """URL the settings window loads a cached cover from (None when there's no cover yet)."""
    global _media_route_dir
    path = covers.cached_cover(platform, game_id)
    if not path:
        return None
    directory = covers.cover_dir()
    if _media_route_dir != directory:
        try:
            from nicegui import app

            os.makedirs(directory, exist_ok=True)
            app.add_media_files(_MEDIA_ROUTE, directory)
            _media_route_dir = directory
        except Exception:
            logger.debug("Games Tracker: couldn't register the cover route", exc_info=True)
            return None
    try:
        version = int(os.path.getmtime(path))
    except OSError:
        version = 0
    return f"{_MEDIA_ROUTE}/{os.path.basename(path)}?v={version}"


def cover_html(game: TrackedGame, *, large: bool = False) -> str:
    src = cover_src(game.platform, game.game_id)
    # The art scales down to fit (never stretched or cropped); a blurred copy fills the rest of the tile.
    img = (f'<span class="gtr-cover-art"><img class="bg" src="{esc(src)}" alt="" onerror="this.parentNode.remove()">'
           f'<img class="fg" src="{esc(src)}" alt=""></span>') if src else ""
    badge = f'<span class="gtr-pb">{brand_svg("psn" if game.platform == PSN else "steam")}</span>'
    return (
        f'<div class="gtr-cover{" is-lg" if large else ""}" style="background:{cover_tone(game.key)}">'
        f"{esc(initials(game.display_title))}{img}{badge}</div>"
    )


# Trophy cups: (light, mid, dark) per grade. Official grade art, if you add it, replaces these (see trophy_icon).
_TROPHY_TONES = {
    "bronze": ("#F2C29A", "#C47A45", "#6E3C1C"),
    "silver": ("#FFFFFF", "#BAC4CD", "#68737E"),
    "gold": ("#FFF1B0", "#E2B23A", "#83600E"),
    "platinum": ("#EEF8FF", "#9CC7F0", "#466FA6"),
}
_OFFICIAL_TROPHY_FILES = "trophies/{grade}.png"


def _trophy_svg(grade: str) -> str:
    grade = grade if grade in _TROPHY_TONES else "bronze"
    light, mid, dark = _TROPHY_TONES[grade]
    gid = f"gtr-tg-{grade}"
    fill = f"url(#{gid})"
    return (
        f'<svg class="gtr-trophy" viewBox="0 0 24 24" aria-hidden="true"><defs><linearGradient id="{gid}" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{light}"/><stop offset=".55" stop-color="{mid}"/><stop offset="1" stop-color="{dark}"/>'
        "</linearGradient></defs>"
        f'<path d="M7.2 5.2H4.4v1.3c0 2.1 1.4 3.6 3.4 4" fill="none" stroke="{fill}" stroke-width="1.6" stroke-linecap="round"/>'
        f'<path d="M16.8 5.2h2.8v1.3c0 2.1-1.4 3.6-3.4 4" fill="none" stroke="{fill}" stroke-width="1.6" stroke-linecap="round"/>'
        f'<path d="M6.8 3h10.4v4.2c0 3.3-2 5.7-4.4 6.3V16h2.6l.9 2.2H7.7l.9-2.2h2.6v-2.5C8.8 12.9 6.8 10.5 6.8 7.2V3z" fill="{fill}" '
        f'stroke="{dark}" stroke-width=".6" stroke-linejoin="round"/>'
        f'<rect x="6.2" y="18.6" width="11.6" height="2.6" rx=".9" fill="{fill}" stroke="{dark}" stroke-width=".6"/>'
        '<path d="M9.1 4.6v2.5c0 1.8.8 3.2 2 4" fill="none" stroke="#fff" stroke-opacity=".6" stroke-width=".9" stroke-linecap="round"/>'
        "</svg>"
    )


@lru_cache(maxsize=1)
def _official_trophies() -> Optional[Dict[str, str]]:
    """Data URIs of assets/trophies/{bronze,silver,gold,platinum}.png when all four are there (else None)."""
    try:
        from PIL import Image

        from ...path_utils import get_assets_path

        out = {}
        for grade in ("bronze", "silver", "gold", "platinum"):
            path = get_assets_path(_OFFICIAL_TROPHY_FILES.format(grade=grade))
            if not os.path.isfile(path):
                return None
            with Image.open(path) as im:
                im = im.convert("RGBA")
                im.thumbnail((48, 48))
                buf = io.BytesIO()
                im.save(buf, "PNG")
            out[grade] = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        return out
    except Exception as e:
        logger.debug("Games Tracker: official trophy art not used: %s", e)
        return None


def trophy_icon(grade: str) -> str:
    """A trophy icon for a grade: the official art when installed, else a drawn trophy cup."""
    official = _official_trophies()
    if official and grade in official:
        return f'<img class="gtr-trophy" src="{official[grade]}" alt="">'
    return _trophy_svg(grade)


def grades_html(game: TrackedGame) -> str:
    earned = grade_counts(game.earned)
    return '<div class="gtr-grades">' + "".join(
        f'<span class="gtr-grade" title="{label}">{trophy_icon(g)}{earned[g]}</span>' for g, label in _GRADE_ORDER
    ) + "</div>"


def card_parts(game: TrackedGame, now: Optional[float] = None) -> Dict[str, str]:
    """(top HTML, footer HTML, footer detail, card classes) for one game card."""
    done = game.is_complete
    chips = f'<span class="itg-os-chip">{esc(game.platform_label or ("Steam" if game.platform == STEAM else "PlayStation"))}</span>'
    if done:
        chips += f'<span class="itg-pill is-ok gtr-mini">{icon_html("workspace_premium")}{"Platinum" if game.platform == PSN else "Perfect"}</span>'
    if game.hidden:
        chips += '<span class="itg-os-chip">Hidden</span>'
    chips += f'<span class="gtr-sub">Played {esc(ago(game.last_played, now))}</span>'
    top = (
        f'{cover_html(game)}<div class="gtr-text"><div class="itg-hook-name">{esc(game.display_title)}</div>'
        f'<div class="itg-os">{chips}</div><div class="gtr-bar"><span style="width:{game.progress}%"></span></div></div>'
        f'<div class="gtr-pct">{game.progress}<small>%</small></div>'
    )
    if game.platform == PSN:
        foot = grades_html(game)
        detail = f"{game.earned_total} / {game.defined_total} trophies"
    else:
        if game.extra.get("private"):
            line = "Achievements private"
        elif not game.baselined:
            line = "Achievements not read yet"
        else:
            line = f"{game.earned_total} / {game.defined_total} achievements" if game.defined_total else "No achievements"
        foot = f'<div class="gtr-grades"><span class="gtr-grade">{icon_html("military_tech")}{esc(line)}</span></div>'
        hours = game.playtime_min / 60
        detail = f"{hours:.0f} h played" if hours >= 1 else f"{game.playtime_min} min played"
    cls = "itg-hook gtr-card" + (" is-success" if done else "") + (" is-hidden" if game.hidden else "")
    return {"top": top, "foot": foot, "detail": detail, "cls": cls}


def overview_html(summary: Dict[str, Any], deltas: Dict[str, int]) -> str:
    cells = [
        ("Games", summary.get("games", 0), f"{summary.get('psn_games', 0)} PlayStation · {summary.get('steam_games', 0)} Steam"),
        ("Platinums", summary.get("platinums", 0), _delta(deltas.get("platinum"), "PlayStation")),
        ("Perfect games", summary.get("perfect", 0), _delta(deltas.get("perfect"), "Steam 100%")),
        ("Trophies", summary.get("trophies", 0), _delta(deltas.get("trophies"), "PlayStation")),
        ("Achievements", summary.get("achievements", 0), _delta(deltas.get("achievements"), "Steam")),
        ("Avg completion", f"{summary.get('avg_completion', 0)}%", "Across started games"),
    ]
    body = "".join(
        f'<div><div class="k">{esc(k)}</div><div class="v">{esc(f"{v:,}" if isinstance(v, int) else v)}</div><div class="s">{esc(s)}</div></div>'
        for k, v, s in cells
    )
    return f'<div class="itg-meta gtr-meta">{body}</div>'


def _delta(n: Optional[int], fallback: str) -> str:
    return f"+{n:,} this week" if n else fallback


def week_deltas(counts: Dict[str, int]) -> Dict[str, int]:
    """Overview sub-lines from ``store.event_counts(since=a week ago)``."""
    return {
        "platinum": counts.get("grade:platinum", 0),
        "trophies": sum(counts.get(f"grade:{g}", 0) for g in ("bronze", "silver", "gold", "platinum")),
        "achievements": counts.get("grade:achievement", 0),
        "perfect": 0,
    }


def status_pills(status: Dict[str, Dict[str, Any]], summary: Dict[str, Any]) -> List[tuple]:
    pills = []
    for platform, label, count_key in ((PSN, "PSN", "psn_games"), (STEAM, "Steam", "steam_games")):
        s = status.get(platform) or {}
        if not s.get("enabled", True):
            pills.append(("off", f"{label} off"))
        elif s.get("state") == "syncing":
            done, total = s.get("done") or 0, s.get("total") or 0
            word = "importing" if s.get("first_import") else "syncing"
            pills.append(("busy", f"{label} · {word} {done} / {total}" if total else f"{label} · {word}…"))
        elif s.get("state") == "waiting" and not s.get("baselined"):
            pills.append(("warn", f"{label} not connected"))
        elif s.get("state") == "error":
            pills.append(("error", f"{label} · sync problem"))
        else:
            pills.append(("ok", f"{label} · {summary.get(count_key, 0):,} games"))
    return pills


def steam_private_banner(games: Sequence[TrackedGame]) -> str:
    """Warning when Steam refused to share achievements (game details are private)."""
    if not any(g.platform == STEAM and g.extra.get("private") for g in games):
        return ""
    return (
        f'<div class="itg-banner" style="margin:0 0 12px">{icon_html("lock")}<div><b>Steam achievements are private.</b> '
        "Steam won't share achievements for this profile, so Steam games show no progress. In Steam open "
        "<b>Profile → Edit Profile → Privacy Settings</b> and set <b>Game details</b> to <b>Public</b> (Friends Only isn't "
        "enough; your profile being public isn't either). Steam can take a few minutes to apply it; then click <b>Sync now</b>. "
        "Achievements are read without counting anything already unlocked.</div></div>"
    )


def sheet_preview_html(data: Dict[str, Any]) -> str:
    """The first rows of the Games sheet, drawn as a table in the export dialog."""
    head = "".join(f"<th>{esc(h)}</th>" for h in data["headers"])
    body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>" for row in data["rows"])
    more = data["games"] - len(data["rows"])
    foot = f'<div class="gtr-sheet-more">+ {more:,} more row{"s" if more != 1 else ""}</div>' if more > 0 else ""
    tabs = "".join(f'<span class="{"is-on" if i == 0 else ""}">{esc(n)}</span>' for i, n in enumerate(data["sheets"]))
    return (f'<div class="gtr-sheet"><div class="gtr-sheet-scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
            f'</div>{foot}<div class="gtr-sheet-tabs">{tabs}</div></div>')


class GamesTrackerTab:
    name = "Games Tracker"

    def __init__(self) -> None:
        self.dirty: bool = False
        self.ui_elements: Dict[str, Any] = {}
        self._page: Optional[IntegrationPage] = None
        self._loaded: TrackerSettings = TrackerSettings()
        self._buffer: TrackerSettings = TrackerSettings()
        self._games: List[TrackedGame] = []
        self._summary: Dict[str, Any] = {}
        self._status: Dict[str, Dict[str, Any]] = {}
        self._deltas: Dict[str, int] = {}
        self._q = ""
        self._platform = "all"
        self._status_filter = "all"
        self._sort = "recent"
        self._limit = PAGE_SIZE
        self._timer: Optional[Any] = None
        self._needs_refresh = True
        self._refreshing = False
        self._last_status_sig: Any = None
        self._drawer_game: Optional[TrackedGame] = None
        self._drawer_edits: Dict[str, Any] = {}
        self._export_opts: Any = None
        self._export_format = "image"  # or "spreadsheet"
        self._sheet_opts: Any = None
        self._export_render_seq = 0

    # ----- lifecycle -----

    @property
    def service(self):
        return get_service()

    def has_unsaved_changes(self) -> bool:
        return self.dirty

    def _settings_dict(self, s: TrackerSettings) -> Dict[str, Any]:
        d = s.to_dict()
        d.pop("export", None)
        return d

    def _update_dirty(self) -> None:
        self.dirty = self._settings_dict(self._buffer) != self._settings_dict(self._loaded)

    def build(self, parent_container) -> None:
        from ...help_system.help_render import inject_page_stylesheet, promote_color_importance, scope_selectors
        from .game_hooks_tab import GAME_HOOKS_CSS  # the shared .itg-hook card styles

        inject_page_stylesheet(_HOOKS_STYLE_ID, promote_color_importance(scope_selectors(GAME_HOOKS_CSS, ".itg")))
        inject_page_stylesheet(_STYLE_ID, promote_color_importance(scope_selectors(GAMES_TRACKER_CSS, ".itg")))
        self._loaded = self.service.settings
        self._buffer = TrackerSettings.from_dict(self._loaded.to_dict())
        self.dirty = False
        self._page = page = IntegrationPage(
            parent_container,
            "games_tracker",
            "Games Tracker",
            "PlayStation and Steam libraries, trophies and achievements",
            icon="emoji_events",
            on_save=self.save,
            on_discard=self.discard,
            is_dirty=lambda: self.dirty,
            help_topic="games_tracker",
            help_tooltip="How the Games Tracker works",
        )
        with page.wide(top=True):
            with ui.element("div").classes("itg-sec"):
                with ui.element("div").classes("gtr-bar-row"):
                    ui.html(f'{icon_html("insights")}Overview', sanitize=False).classes("itg-sec-head")
                    ui.element("div").classes("itg-spacer")
                    self.ui_elements["synced"] = ui.label("").classes("gtr-sub")
                    self.ui_elements["export_btn"] = outline_button("Export", self.open_export, icon="ios_share")
                    self.ui_elements["sync_btn"] = outline_button("Sync now", self._sync_now, icon="sync")
                self.ui_elements["banner"] = ui.html("", sanitize=False).classes("w-full")
                self.ui_elements["overview"] = ui.html("", sanitize=False).classes("w-full")
                self.ui_elements["empty"] = ui.element("div").classes("w-full")
            self.ui_elements["library"] = lib = section("sports_esports", "Library")
            with lib:
                self._build_toolbar()
                self.ui_elements["grid"] = ui.element("div").classes("itg-hooks")
                self.ui_elements["none"] = ui.label("No games match these filters.").classes("gtr-none")
                with ui.element("div").classes("gtr-more") as more:
                    self.ui_elements["more_btn"] = outline_button("Show more", self._show_more, icon="expand_more")
                self.ui_elements["more"] = more
            self._build_options()
        self._build_drawer()
        self._build_export_dialog()
        self.service.add_listener(self._mark_changed)
        covers.add_listener(self._on_cover)
        self._timer = layout_schedule(1.0, self._tick, active=True)
        layout_schedule(0.05, self._refresh, once=True)

    def on_enter(self) -> None:
        self._needs_refresh = True
        if self._timer is not None:
            self._timer.active = True
        if self._page is not None:
            self._page.set_active(True)

    def on_exit(self) -> None:
        if self._timer is not None:
            self._timer.active = False
        if self._page is not None:
            self._page.set_active(False)

    def _mark_changed(self) -> None:
        self._needs_refresh = True  # worker thread: the UI timer picks it up

    def _on_cover(self, _key: str) -> None:
        self._needs_refresh = True

    # ----- data refresh -----

    def _load_data(self) -> Dict[str, Any]:
        svc = self.service
        return {
            "games": svc.store.list_games(),
            "summary": svc.summary(),
            "status": svc.status(),
            "deltas": week_deltas(svc.store.event_counts(since=time.time() - 7 * 86400)),
        }

    async def _tick(self) -> None:
        if self._refreshing:
            return
        try:
            status = await run.io_bound(self.service.status)
        except Exception:
            return
        sig = {p: (s.get("state"), s.get("done"), s.get("enabled"), s.get("baselined")) for p, s in (status or {}).items()}
        if self._needs_refresh or sig != self._last_status_sig:
            await self._refresh()

    async def _refresh(self) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        self._needs_refresh = False
        try:
            data = await run.io_bound(self._load_data)
            if not data:
                return
            self._games = data["games"]
            self._summary = data["summary"]
            self._status = data["status"]
            self._deltas = data["deltas"]
            self._last_status_sig = {p: (s.get("state"), s.get("done"), s.get("enabled"), s.get("baselined")) for p, s in self._status.items()}
            self._render_header()
            self._render_grid()
        except Exception:
            logger.debug("Games Tracker tab refresh failed", exc_info=True)
        finally:
            self._refreshing = False

    def _render_header(self) -> None:
        if self._page is not None:
            self._page.set_status(status_pills(self._status, self._summary))
        last = max((s.get("last_sync") or 0) for s in self._status.values()) if self._status else 0
        self.ui_elements["synced"].set_text(f"Synced {ago(last)}" if last else "")
        self.ui_elements["banner"].set_content(self._banner_html())
        has_games = bool(self._games)
        self.ui_elements["overview"].set_content(overview_html(self._summary, self._deltas) if has_games else "")
        self.ui_elements["export_btn"].set_visibility(has_games)
        empty = self.ui_elements["empty"]
        empty.clear()
        if not has_games and not any(s.get("state") == "syncing" for s in self._status.values()):
            with empty:
                self._build_empty()
        self.ui_elements["library"].set_visibility(has_games)

    def _banner_html(self) -> str:
        return self._import_banner() + steam_private_banner(self._games)

    def _import_banner(self) -> str:
        importing = [(p, s) for p, s in self._status.items() if s.get("state") == "syncing" and s.get("first_import")]
        if not importing:
            return ""
        parts, pct = [], 0
        for p, s in importing:
            done, total = s.get("done") or 0, s.get("total") or 0
            label = "PlayStation" if p == PSN else "Steam"
            parts.append(f"{label} {done:,} of {total:,} titles" if total else f"{label} starting")
            pct = max(pct, round(done * 100 / total) if total else 0)
        return (
            f'<div class="gtr-sync">{icon_html("cloud_download")}<div style="flex:1 1 auto;min-width:0">'
            f'<div><b>First import running</b> · {esc(" · ".join(parts))}</div>'
            f'<div class="gtr-bar is-info"><span style="width:{pct}%"></span></div>'
            '<div style="margin-top:6px;font-size:.72rem;color:var(--color-text-muted)">Runs on its own background threads, so the app stays '
            "responsive. Nothing from this import counts toward Statistics, chatbot events or connectors. Counting starts with the first "
            "change after it finishes.</div></div></div>"
        )

    def _build_empty(self) -> None:
        with ui.element("div").classes("gtr-empty"):
            ui.html(icon_html("sports_esports"), sanitize=False)
            ui.label("No games yet").classes("t")
            ui.label(
                "Connect PlayStation Network or Steam and your played games show up here. The first import runs in the background."
            ).classes("s")
            with ui.row().classes("gap-2 mt-2"):
                outline_button("Set up PSN", lambda: self._goto("PSN"))
                outline_button("Set up Steam", lambda: self._goto("Steam"))

    @staticmethod
    def _goto(subtab: str) -> None:
        from ...help_system.contextual_help import navigate_to_settings_subtab

        navigate_to_settings_subtab(subtab)

    # ----- library -----

    def _build_toolbar(self) -> None:
        with ui.element("div").classes("gtr-tools"):
            search = ui.input(placeholder="Search games", on_change=lambda e: self._set_filter(q=e.value or "")).props(
                'outlined dense clearable debounce=250 aria-label="Search games"'
            ).classes("gtr-search")
            with search.add_slot("prepend"):
                ui.icon("search").classes("text-[18px]")
            with ui.element("div").classes("gtr-seg").props('role=group aria-label="Platform"'):
                self.ui_elements["plat_btns"] = {
                    key: ui.button(label, on_click=lambda k=key: self._set_filter(platform=k)).props("flat no-caps dense")
                    for key, label in PLATFORM_FILTERS
                }
            ui.select(STATUS_OPTIONS, value="all", on_change=lambda e: self._set_filter(status=e.value)).props(
                'outlined dense options-dense aria-label="Status"'
            ).classes("gtr-select")
            ui.select(SORT_OPTIONS, value="recent", on_change=lambda e: self._set_filter(sort=e.value)).props(
                'outlined dense options-dense aria-label="Sort"'
            ).classes("gtr-select")
        self._paint_platform_buttons()

    def _paint_platform_buttons(self) -> None:
        for key, btn in (self.ui_elements.get("plat_btns") or {}).items():
            on = key == self._platform
            btn.classes(add="is-on") if on else btn.classes(remove="is-on")
            btn.props(f"aria-pressed={'true' if on else 'false'}")

    def _set_filter(self, **changes: Any) -> None:
        if "q" in changes:
            self._q = changes["q"]
        if "platform" in changes:
            self._platform = changes["platform"]
            self._paint_platform_buttons()
        if "status" in changes:
            self._status_filter = changes["status"] or "all"
        if "sort" in changes:
            self._sort = changes["sort"] or "recent"
        self._limit = PAGE_SIZE
        self._render_grid()

    def _show_more(self) -> None:
        self._limit += PAGE_SIZE
        self._render_grid()

    def visible_games(self) -> List[TrackedGame]:
        return filter_games(self._games, q=self._q, platform=self._platform, status=self._status_filter, sort=self._sort)

    def _render_grid(self) -> None:
        grid = self.ui_elements.get("grid")
        if grid is None:
            return
        games = self.visible_games()
        shown = games[: self._limit]
        grid.clear()
        now = time.time()
        with grid:
            for g in shown:
                self._build_card(g, now)
        self.ui_elements["none"].set_visibility(bool(self._games) and not games)
        remaining = len(games) - len(shown)
        self.ui_elements["more"].set_visibility(remaining > 0)
        self.ui_elements["more_btn"].set_text(f"Show more ({min(PAGE_SIZE, remaining)})")
        lib = self.ui_elements.get("library")
        if lib is not None:
            heads = [c for c in lib.default_slot.children if "itg-sec-head" in c.classes]
            if heads:
                heads[0].set_content(f'{icon_html("sports_esports")}Library<span class="itg-sec-right">Showing {len(shown):,} of {len(games):,}</span>')

    def _build_card(self, game: TrackedGame, now: float) -> None:
        parts = card_parts(game, now)
        with ui.element("div").classes(parts["cls"]):
            ui.html(parts["top"], sanitize=False).classes("itg-hook-top")
            with ui.element("div").classes("itg-hook-state"):
                ui.html(parts["foot"], sanitize=False).classes("gtr-grades")
                with ui.element("div").classes("itg-hook-acts"):
                    ui.label(parts["detail"]).classes("itg-hook-detail")
                    ui.button(icon="settings", on_click=lambda g=game: self.open_drawer(g)).props(
                        f'flat round dense size=sm aria-label="Settings for {esc(game.display_title)}"'
                    ).classes("itg-ibtn").tooltip("Game settings")

    def _sync_now(self) -> None:
        covers.forget_failures()  # covers that failed are tried again after this sync
        self.service.sync_now()
        notify("", type="info", source="game_tracker", title="Games Tracker sync started", hint="It runs in the background.")
        self._needs_refresh = True

    # ----- options -----

    def _build_options(self) -> None:
        b = self._buffer
        exp = ui.expansion().classes("itg-fold").props("dense")
        with exp.add_slot("header"):
            self.ui_elements["opts_head"] = ui.html(self._options_head(), sanitize=False).classes("itg-fold-head")
        with exp:
            with ui.element("div").classes("itg-fold-body"):
                with ui.element("div").classes("gtr-optgrid"):
                    with ui.element("div"):
                        self.ui_elements["opt_psn"] = switch_row(
                            "Track PlayStation library", "Imports every title that has a trophy list and resolves its NPWR ID",
                            b.psn_enabled, lambda v: self._set_opt(psn_enabled=v))
                        self.ui_elements["opt_steam"] = switch_row(
                            "Track Steam library", "Games you have played (playtime above zero)",
                            b.steam_enabled, lambda v: self._set_opt(steam_enabled=v))
                        self.ui_elements["opt_stats"] = switch_row(
                            "Count changes in Statistics", "Unlocks, platinums, 100% games, new games and milestones after the first import",
                            b.record_stats, lambda v: self._set_opt(record_stats=v))
                        self.ui_elements["opt_events"] = switch_row(
                            "Send chatbot and connector events", "Game unlock, completed, started and milestone triggers",
                            b.send_events, lambda v: self._set_opt(send_events=v))
                    with ui.element("div"):
                        with fields():
                            self.ui_elements["opt_sync"] = self._number("Library sync", b.sync_minutes, "minutes", 5, 120, "sync_minutes")
                            self.ui_elements["opt_ttl"] = self._number("Cover art cache", b.cover_ttl_days, "days", 1, 90, "cover_ttl_days")
                        with fields():
                            self.ui_elements["opt_missing"] = self._number(
                                "Retry missing covers", b.cover_missing_hours, "hours", 1, 72, "cover_missing_hours")
                            with ui.element("div"):
                                ui.html("Progress milestones", sanitize=False).classes("itg-field-label")
                                self.ui_elements["milestones"] = ui.element("div").classes("gtr-tokens")
                        hint("The first import of each account is silent. Only changes after it count toward Statistics, chatbot events and "
                             "connectors. Switching account starts a new silent import.")
                with ui.element("div").classes("mt-3"):
                    with tool_row("image", "Cover art cache", "Covers are kept on disk. Clearing re-downloads them in the background."):
                        outline_button("Clear covers", self._clear_covers, icon="delete_sweep")
                    with tool_row("restart_alt", "Reset baseline", "Treat current progress as the new starting point. Nothing already earned gets counted."):
                        ui.button("Reset baseline", icon="restart_alt", on_click=self._confirm_reset).props("outline no-caps dense").classes("gtr-danger")
        self._render_milestones()

    def _options_head(self) -> str:
        b = self._buffer
        ms = " / ".join(str(m) for m in b.milestones) or "none"
        return (
            f'{icon_html("tune")}<span class="itg-fold-title">Tracker options</span>'
            f'<span class="gtr-sub">Sync every {b.sync_minutes} min · covers kept {b.cover_ttl_days} days · milestones {esc(ms)}%</span>'
        )

    def _number(self, label: str, value: int, unit: str, lo: int, hi: int, key: str) -> Any:
        with ui.element("div"):
            ui.html(esc(label), sanitize=False).classes("itg-field-label")
            with ui.element("div").classes("gtr-unit"):
                num = ui.number(value=value, min=lo, max=hi, step=1, format="%d").props("outlined dense").classes("gtr-num")
                num.on_value_change(lambda e, k=key, lo=lo, hi=hi: self._set_opt(**{k: max(lo, min(hi, int(e.value or lo)))}))
                ui.label(unit)
        return num

    def _render_milestones(self) -> None:
        box = self.ui_elements.get("milestones")
        if box is None:
            return
        box.clear()
        with box:
            for m in self._buffer.milestones:
                with ui.element("span").classes("gtr-token"):
                    ui.label(f"{m}%")
                    ui.button(icon="close", on_click=lambda m=m: self._remove_milestone(m)).props(
                        f'flat round dense size=xs aria-label="Remove {m}%"').classes("itg-ibtn")
            add = ui.number(placeholder="Add %", min=1, max=99, step=1, format="%d").props("outlined dense").classes("gtr-num")
            add.on("keydown.enter", lambda _e, a=add: self._add_milestone(a.value))
            add.on("blur", lambda _e, a=add: self._add_milestone(a.value))

    def _add_milestone(self, value: Any) -> None:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return
        if n in self._buffer.milestones or not 1 <= n <= 99:
            return
        self._set_opt(milestones=clean_milestones(self._buffer.milestones + [n]))
        self._render_milestones()

    def _remove_milestone(self, value: int) -> None:
        self._set_opt(milestones=[m for m in self._buffer.milestones if m != value])
        self._render_milestones()

    def _set_opt(self, **changes: Any) -> None:
        for k, v in changes.items():
            setattr(self._buffer, k, v)
        self._update_dirty()
        head = self.ui_elements.get("opts_head")
        if head is not None:
            head.set_content(self._options_head())

    def save(self) -> None:
        try:
            buf = TrackerSettings.from_dict({**self._buffer.to_dict(), "export": self.service.settings.export})
            self._loaded = self.service.save_settings(buf)
            self._buffer = TrackerSettings.from_dict(self._loaded.to_dict())
            self.dirty = False
            notify("", type="success", source="game_tracker", title="Games Tracker settings saved")
        except Exception as e:
            logger.error("Games Tracker settings save failed: %s", e, exc_info=True)
            notify("", type="error", source="game_tracker", title="Couldn't save the Games Tracker settings",
                   hint="Check the Mycelian data folder is writable, then try again.", details=f"{type(e).__name__}: {e}")

    def discard(self) -> None:
        self._buffer = TrackerSettings.from_dict(self._loaded.to_dict())
        self.dirty = False
        b = self._buffer
        for key, value in (("opt_psn", b.psn_enabled), ("opt_steam", b.steam_enabled), ("opt_stats", b.record_stats),
                           ("opt_events", b.send_events), ("opt_sync", b.sync_minutes), ("opt_ttl", b.cover_ttl_days),
                           ("opt_missing", b.cover_missing_hours)):
            el = self.ui_elements.get(key)
            if el is not None:
                el.value = value
        self._buffer = TrackerSettings.from_dict(self._loaded.to_dict())  # the value setters above re-ran _set_opt
        self.dirty = False
        self._render_milestones()
        head = self.ui_elements.get("opts_head")
        if head is not None:
            head.set_content(self._options_head())

    async def _clear_covers(self) -> None:
        removed = await run.io_bound(covers.clear_covers)
        self.service.sync_now()
        notify(f"{removed:,} cover{'s' if removed != 1 else ''} removed. They download again in the background.", type="success",
               source="game_tracker", title="Cover art cleared")
        self._needs_refresh = True

    def _dialog_parent(self) -> Any:
        """Small dialogs live in the page (also when opened from code, outside a click handler)."""
        return self._page.root if self._page is not None else contextlib.nullcontext()

    def _confirm_reset(self) -> None:
        with self._dialog_parent(), ui.dialog() as dialog, ui.card().classes("itg-dialog is-danger"):
            ui.html(f'{icon_html("restart_alt")}Reset the baseline?', sanitize=False).classes("itg-dialog-title")
            ui.label("The next sync reads every game silently and treats that as the new starting point. Trophies and achievements "
                     "already earned won't reach Statistics, chat or connectors.").classes("itg-dialog-sub")
            with ui.element("div").classes("itg-actions-end"):
                outline_button("Cancel", dialog.close)
                ui.button("Reset baseline", on_click=lambda: (self._reset_baseline(), dialog.close())).props("unelevated no-caps color=negative")
        dialog.open()

    def _reset_baseline(self) -> None:
        self.service.reset_baseline()
        self.service.sync_now()
        notify("", type="success", source="game_tracker", title="Baseline reset", hint="The next sync sets the new starting point.")

    # ----- drawer -----

    def _build_drawer(self) -> None:
        with ui.dialog().props("position=right maximized") as dialog:
            self.ui_elements["drawer"] = dialog
            with ui.element("div").classes("itg").style("width:auto;height:100%"):
                self.ui_elements["drawer_body"] = ui.element("aside").classes("gtr-drawer").props('aria-label="Game settings"')

    def open_drawer(self, game: TrackedGame) -> None:
        self._drawer_game = game
        self._drawer_edits = {}
        body = self.ui_elements["drawer_body"]
        body.clear()
        with body:
            self._build_drawer_content(game)
        self.ui_elements["drawer"].open()
        if game.platform == PSN:
            layout_schedule(0.05, lambda g=game: self._load_groups(g), once=True)

    def _build_drawer_content(self, g: TrackedGame) -> None:
        psn = g.platform == PSN
        with ui.element("div").classes("gtr-dhead"):
            ident = g.game_id if psn else f"App {g.game_id}"
            done = (f'<span class="itg-pill is-ok gtr-mini">{icon_html("workspace_premium")}{"Platinum" if psn else "Perfect"}</span>'
                    if g.is_complete else "")
            ui.html(
                f'{cover_html(g, large=True)}<div style="flex:1 1 auto;min-width:0"><div class="gtr-dtitle">{esc(g.display_title)}</div>'
                f'<div class="itg-os"><span class="itg-os-chip">{esc(g.platform_label or "Steam")}</span>'
                f'<span class="itg-os-chip" style="font-family:ui-monospace,Menlo,monospace">{esc(ident)}</span>{done}</div>'
                f'<div class="gtr-sub" style="margin-top:6px">Last played {esc(ago(g.last_played))}</div></div>',
                sanitize=False,
            ).classes("flex gap-3 items-start").style("flex:1 1 auto;min-width:0;gap:14px")
            ui.button(icon="close", on_click=self.ui_elements["drawer"].close).props('flat round dense aria-label="Close"').classes("itg-ibtn")
        with ui.element("div").classes("gtr-dbody"):
            with section("trending_up", "Progress", esc(f"{g.earned_total} of {g.defined_total} {'trophies' if psn else 'achievements'}")):
                ui.html(self._progress_html(g), sanitize=False).classes("w-full")
            if psn:
                with section("account_tree", "Trophy groups"):
                    self.ui_elements["groups"] = ui.html('<div class="itg-empty">Loading…</div>', sanitize=False).classes("w-full")
            with section("history", "Recent unlocks"):
                ui.html(self._unlocks_html(g), sanitize=False).classes("w-full")
            with section("edit", "Game settings"):
                with fields(one=True):
                    self.ui_elements["d_title"] = field("Display title", value=g.title_override or g.display_title,
                                                        placeholder=g.title, on_change=lambda e: self._edit("title", e.value))
                if psn:
                    with fields():
                        self.ui_elements["d_npwr"] = field("NPWR ID", value=g.game_id, on_change=lambda e: self._edit("np_communication_id", e.value))
                        self.ui_elements["d_npwr"].classes("gtr-mono")
                        self.ui_elements["d_ntid"] = field("NP Title ID", value=g.np_title_id, placeholder="PPSA01234_00",
                                                           on_change=lambda e: self._edit("np_title_id", e.value))
                        self.ui_elements["d_ntid"].classes("gtr-mono")
                    with ui.row().classes("mt-2"):
                        outline_button("Re-resolve NPWR from Title ID", self._resolve_npwr, icon="manage_search")
                    hint("The display title is what overlays, chat and connectors show. The NPWR ID and NP Title ID decide which trophy "
                         "list this game's presence maps to.")
                else:
                    with fields():
                        field("App ID", value=g.game_id, readonly=True)
                        field("Last played", value=ago(g.last_played), readonly=True)
                    hint("The display title is what overlays, chat and connectors show. Steam IDs come from your library and can't be edited.")
                switch_row("Hide from tracker", "Keeps syncing, but leaves the grid and the totals", g.hidden,
                           lambda v: self._edit("hidden", v))
                ui.html(self._kv_html(g), sanitize=False).classes("w-full")
        with ui.element("div").classes("gtr-dfoot"):
            ui.button("Remove from cache" if psn else "Remove", icon="delete", on_click=self._confirm_remove).props(
                "outline no-caps dense").classes("gtr-danger")
            outline_button("Export card", lambda gg=g: self.open_export(gg), icon="photo_camera")
            ui.element("div").classes("itg-spacer")
            outline_button("Close", self.ui_elements["drawer"].close)
            self.ui_elements["d_save"] = primary_button("Save", self._save_drawer, icon="check")
            self.ui_elements["d_save"].props("disable")

    def _progress_html(self, g: TrackedGame) -> str:
        psn = g.platform == PSN
        label = "weighted by trophy grade" if psn else "of achievements unlocked"
        head = (f'<div class="gtr-big"><b>{g.progress}%</b><span class="gtr-sub">{label}</span></div>'
                f'<div class="gtr-bar{" is-done" if g.is_complete else ""}"><span style="width:{g.progress}%"></span></div>')
        if psn:
            earned, defined = grade_counts(g.earned), grade_counts(g.defined)
            boxes = "".join(
                f'<div><div class="k gtr-grade">{trophy_icon(k)}{label}</div><div class="v">{earned[k]} <small>/ {defined[k]}</small></div></div>'
                for k, label in _GRADE_ORDER
            )
            return head + f'<div class="gtr-boxes">{boxes}</div>'
        hours = g.playtime_min / 60
        return head + (
            '<div class="gtr-boxes is-3">'
            f'<div><div class="k">Achievements</div><div class="v">{g.earned_total} <small>/ {g.defined_total}</small></div></div>'
            f'<div><div class="k">Playtime</div><div class="v">{hours:.0f} h</div></div>'
            f'<div><div class="k">Last played</div><div class="v">{esc(ago(g.last_played))}</div></div></div>'
        )

    def _unlocks_html(self, g: TrackedGame) -> str:
        try:
            events: List[GameEvent] = self.service.store.recent_events(5, platform=g.platform, game_id=g.game_id)
        except Exception:
            events = []
        if not events:
            return '<div class="itg-list"><div class="itg-empty">Nothing new since the first import.</div></div>'
        rows = []
        for e in events:
            mark = (f'<span class="gtr-grade">{trophy_icon(e.unlock_grade)}</span>' if g.platform == PSN
                    else icon_html("military_tech"))
            rows.append(f'<div class="itg-li">{mark}<span class="itg-li-main">{esc(e.unlock_name or e.unlock_grade.title())}</span>'
                        f'<span class="itg-li-muted">{esc(ago(e.ts))}</span></div>')
        return '<div class="itg-list">' + "".join(rows) + "</div>"

    def _kv_html(self, g: TrackedGame) -> str:
        info = covers.cover_info(g.platform, g.game_id)
        left = info["expires_at"] - time.time()
        if info["cached"]:
            cover = f"Cached {ago(info['fetched_at'])} · refreshes in {max(1, round(left / 86400))} days"
        elif info["fetched_at"]:
            hours = max(1, round(left / 3600))
            cover = f"Couldn't download · tries again in {hours} hour{'s' if hours != 1 else ''} (or click Sync now)"
        else:
            cover = "Not downloaded yet"
        last_sync = (self._status.get(g.platform) or {}).get("last_sync") or 0
        rows = [("Trophy list name", g.title)] if g.platform == PSN else [("Playtime", f"{g.playtime_min / 60:.1f} h")]
        rows += [("Platform", g.platform_label or "Steam"), ("Cover art", cover), ("Last synced", ago(last_sync)),
                 ("First seen", time.strftime("%b %d, %Y", time.localtime(g.first_seen)) if g.first_seen else "—")]
        return '<div class="itg-kv" style="margin-top:12px">' + "".join(
            f'<span class="k">{esc(k)}</span><span class="v">{esc(v)}</span>' for k, v in rows) + "</div>"

    async def _load_groups(self, g: TrackedGame) -> None:
        groups = await run.io_bound(self.service.group_progress, g.game_id)
        el = self.ui_elements.get("groups")
        if el is None or self._drawer_game is not g:
            return
        if not groups:
            el.set_content('<div class="itg-list"><div class="itg-empty">Group progress needs PSN to be connected.</div></div>')
            return
        rows = "".join(
            f'<div class="itg-li"><span class="itg-li-main">{esc(x["name"])}</span><span class="itg-li-muted">{x["earned"]} / {x["defined"]}</span>'
            f'<span class="gtr-minibar"><span style="width:{x["progress"]}%"></span></span>'
            f'<span class="itg-li-muted" style="min-width:34px;text-align:right">{x["progress"]}%</span></div>'
            for x in groups
        )
        el.set_content(f'<div class="itg-list">{rows}</div>')

    def drawer_changes(self) -> Dict[str, Any]:
        """Edits that differ from the game as it was opened."""
        g = self._drawer_game
        if g is None:
            return {}
        current = {"title": g.title_override or g.display_title, "np_communication_id": g.game_id,
                   "np_title_id": g.np_title_id, "hidden": g.hidden}
        out = {}
        for k, v in self._drawer_edits.items():
            norm = v.strip() if isinstance(v, str) else v
            if k in ("np_communication_id", "np_title_id") and isinstance(norm, str):
                norm = norm.upper()
            if norm != current.get(k):
                out[k] = norm
        if "title" in out and out["title"] == g.display_title and not g.title_override:
            out.pop("title")
        return out

    def _edit(self, key: str, value: Any) -> None:
        self._drawer_edits[key] = value
        btn = self.ui_elements.get("d_save")
        if btn is not None:
            btn.props(remove="disable") if self.drawer_changes() else btn.props("disable")

    async def _save_drawer(self) -> None:
        g = self._drawer_game
        changes = self.drawer_changes()
        if g is None or not changes:
            return
        game, error = await run.io_bound(lambda: self.service.update_game(g.platform, g.game_id, **changes))
        if error or game is None:
            notify(error or "That game is no longer in the tracker.", type="warning", source="game_tracker", title="Couldn't save the game")
            return
        notify("", type="success", source="game_tracker", title=f"Saved {game.display_title}")
        self.ui_elements["drawer"].close()
        self._needs_refresh = True

    async def _resolve_npwr(self) -> None:
        g = self._drawer_game
        ntid = str(self._drawer_edits.get("np_title_id", g.np_title_id if g else "") or "").strip()
        if not ntid:
            notify("Enter the game's NP Title ID first (for example PPSA01234_00).", type="info", source="game_tracker",
                   title="No NP Title ID")
            return
        npwr = await run.io_bound(self.service.resolve_npwr, ntid)
        if not npwr:
            notify("PlayStation didn't return a trophy list for that title ID.", type="warning", source="game_tracker",
                   title="Couldn't resolve the NPWR ID", hint="Check the NP Title ID and that PSN is connected.",
                   actions=nav_actions_settings("PSN"))
            return
        el = self.ui_elements.get("d_npwr")
        if el is not None:
            el.value = npwr
        self._edit("np_communication_id", npwr)
        notify(f"{ntid.upper()} maps to {npwr}. Click Save to keep it.", type="success", source="game_tracker", title="NPWR ID found")

    def _confirm_remove(self) -> None:
        g = self._drawer_game
        if g is None:
            return
        with self._dialog_parent(), ui.dialog() as dialog, ui.card().classes("itg-dialog is-danger"):
            ui.html(f'{icon_html("delete")}Remove {esc(g.display_title)}?', sanitize=False).classes("itg-dialog-title")
            ui.label("It comes back on the next sync while it's still on your account. Your display title and IDs for it are lost.").classes(
                "itg-dialog-sub")
            with ui.element("div").classes("itg-actions-end"):
                outline_button("Cancel", dialog.close)
                ui.button("Remove", on_click=lambda: self._remove(g, dialog)).props("unelevated no-caps color=negative")
        dialog.open()

    async def _remove(self, g: TrackedGame, dialog) -> None:
        dialog.close()
        await run.io_bound(self.service.remove_game, g.platform, g.game_id)
        self.ui_elements["drawer"].close()
        notify("", type="success", source="game_tracker", title=f"Removed {g.display_title}")
        self._needs_refresh = True

    # ----- export image -----

    def _build_export_dialog(self) -> None:
        # full-width: Quasar caps dialog content at 560px otherwise; the modal sizes and centres itself.
        with ui.dialog().props("full-width") as dialog:
            self.ui_elements["export"] = dialog
            with ui.element("div").classes("itg").style("width:100%;display:flex;justify-content:center"):
                with ui.element("div").classes("gtr-modal").props('role=dialog aria-label="Export"'):
                    with ui.element("div").classes("gtr-mhead"):
                        ui.html(f'<span class="itg-tile">{icon_html("ios_share")}</span>'
                                '<span><div class="itg-title-name">Export</div>'
                                '<div class="itg-title-sub">A PNG image or an XLSX spreadsheet of your library. Images follow your theme.'
                                '</div></span>',
                                sanitize=False).classes("itg-title")
                        ui.element("div").classes("itg-spacer")
                        ui.button(icon="close", on_click=self._close_export).props('flat round dense aria-label="Close"').classes("itg-ibtn")
                    with ui.element("div").classes("gtr-mbody"):
                        self.ui_elements["exp_opts"] = ui.element("div").classes("gtr-mopts")
                        with ui.element("div").classes("gtr-mprev"):
                            self.ui_elements["exp_img"] = ui.html("", sanitize=False).classes("contents")
                            self.ui_elements["exp_size"] = ui.label("").classes("gtr-sub")
                    with ui.element("div").classes("gtr-mfoot"):
                        self.ui_elements["exp_saved"] = ui.label("Options are remembered for next time.").classes("gtr-sub truncate")
                        ui.element("div").classes("itg-spacer")
                        ui.button(icon="folder_open", on_click=self._open_exports_folder).props(
                            'flat round dense aria-label="Open the exports folder"').classes("itg-ibtn").tooltip("Open the exports folder")
                        self.ui_elements["exp_copy"] = outline_button("Copy image", self._copy_export, icon="content_copy")
                        self.ui_elements["exp_save_png"] = primary_button("Save PNG", self._save_export, icon="download")
                        self.ui_elements["exp_save_xlsx"] = primary_button("Save XLSX", self._save_spreadsheet, icon="download")

    def open_export(self, game: Optional[TrackedGame] = None) -> None:
        from ...game_tracker.export import ExportOptions
        from ...game_tracker.spreadsheet import SpreadsheetOptions

        saved = dict(self.service.settings.export or {})
        opts = ExportOptions.from_dict(saved)
        self._sheet_opts = SpreadsheetOptions.from_dict(saved.get("spreadsheet"))
        self._export_format = "spreadsheet" if saved.get("format") == "spreadsheet" else "image"
        if isinstance(game, TrackedGame):
            opts.template = "game"
            opts.game_key = game.key
            self._export_format = "image"
            self.ui_elements["drawer"].close()
        self._export_opts = opts
        self._render_export_options()
        self.ui_elements["exp_saved"].set_text("Options are remembered for next time.")
        self.ui_elements["export"].open()
        self._schedule_preview()

    def _close_export(self) -> None:
        self._remember_export()
        self.ui_elements["export"].close()

    def _remember_export(self) -> None:
        if self._export_opts is None:
            return
        try:
            s = self.service.settings
            wanted = {**self._export_opts.to_dict(), "format": self._export_format}
            if self._sheet_opts is not None:
                wanted["spreadsheet"] = self._sheet_opts.to_dict()
            if s.export != wanted:
                self.service.save_settings(TrackerSettings.from_dict({**s.to_dict(), "export": wanted}))
                self._loaded.export = wanted
        except Exception:
            logger.debug("Games Tracker: export options not saved", exc_info=True)

    def _set_export(self, rebuild: bool = False, **changes: Any) -> None:
        for k, v in changes.items():
            setattr(self._export_opts, k, v)
        if rebuild:
            self._render_export_options()
        self._schedule_preview()

    def _render_export_options(self) -> None:
        from ...game_tracker import export as ex

        o = self._export_opts
        box = self.ui_elements["exp_opts"]
        box.clear()
        lib = o.template == "library"

        def label(text: str, sub: str = "") -> None:
            ui.label(text).classes("gtr-olabel")
            if sub:
                ui.label(sub).classes("gtr-osub")

        def seg(items, current, setter) -> None:
            with ui.element("div").classes("gtr-seg is-full"):
                for key, text, icon in items:
                    btn = ui.button(text, icon=icon or None, on_click=lambda k=key: setter(k)).props("flat no-caps dense")
                    if key == current:
                        btn.classes("is-on")
                    btn.props(f"aria-pressed={'true' if key == current else 'false'}")

        def chips(items, hidden: List[str], attr: str) -> None:
            with ui.element("div").classes("gtr-chips"):
                for key, text in items:
                    on = key not in hidden
                    btn = ui.button(text, icon="check" if on else None, on_click=lambda k=key: toggle(attr, k)).props("flat no-caps dense")
                    btn.classes("gtr-chip" + (" is-on" if on else ""))
                    btn.props(f"aria-pressed={'true' if on else 'false'}")

        def toggle(attr: str, key: str) -> None:
            current = list(getattr(o, attr))
            current.remove(key) if key in current else current.append(key)
            self._set_export(rebuild=True, **{attr: current})

        spreadsheet = self._export_format == "spreadsheet"
        self.ui_elements["exp_copy"].set_visibility(not spreadsheet)
        self.ui_elements["exp_save_png"].set_visibility(not spreadsheet)
        self.ui_elements["exp_save_xlsx"].set_visibility(spreadsheet)
        with box:
            label("Format")
            seg([("image", "Image (PNG)", "image"), ("spreadsheet", "Spreadsheet (XLSX)", "table_view")], self._export_format,
                self._set_export_format)
            if spreadsheet:
                self._build_spreadsheet_options(label, seg)
                return
            label("Image")
            seg([(k, t, i) for k, t, i in ex.TEMPLATES], o.template,
                lambda k: self._set_export(rebuild=True, template=k, hidden_sections=[], title=""))
            if not lib:
                games = [g for g in self._games if not g.hidden] or self._games
                options = {g.key: f"{g.display_title} · {g.platform_label or 'Steam'}" for g in filter_games(games, sort="recent")}
                if o.game_key not in options and options:
                    o.game_key = next(iter(options))
                label("Game")
                ui.select(options, value=o.game_key or None, with_input=True,
                          on_change=lambda e: self._set_export(game_key=e.value or "")).props("outlined dense options-dense").classes("w-full")
            label("Layout")
            seg(ex.LAYOUT_LABELS, o.layout, lambda k: self._set_export(rebuild=True, layout=k))
            label("Title", "Blank uses your channel name." if lib else "Blank uses the game's display title.")
            ui.input(value=o.title, placeholder=ex.default_channel_name() if lib else "Game title",
                     on_change=lambda e: self._set_export(title=e.value or "")).props("outlined dense debounce=400").classes("w-full")
            if lib:
                label("Platforms")
                seg([("all", "All", ""), (PSN, "PlayStation", ""), (STEAM, "Steam", "")], o.platform,
                    lambda k: self._set_export(rebuild=True, platform=k))
                label("Period", "Tiles show what changed in this period next to the totals.")
                ui.select(dict(ex.PERIODS), value=o.period, on_change=lambda e: self._set_export(period=e.value)).props(
                    "outlined dense options-dense").classes("w-full")
            note = ""
            if lib and o.layout == "square":
                note = "Square images show the first three tiles, up to 8 games, and no cabinet or unlocks."
            elif lib and o.layout == "portrait":
                note = "Portrait images show up to 8 games."
            label("Show", note)
            chips(ex.LIBRARY_SECTIONS if lib else ex.GAME_SECTIONS, o.hidden_sections, "hidden_sections")
            if lib and "tiles" not in o.hidden_sections:
                label("Headline tiles")
                chips(ex.TILES, o.hidden_tiles, "hidden_tiles")
            if lib and "grid" not in o.hidden_sections:
                label("Game grid")
                with fields():
                    with ui.element("div"):
                        ui.html("Pick games by", sanitize=False).classes("itg-field-label")
                        ui.select(dict(ex.PICKS), value=o.pick, on_change=lambda e: self._set_export(rebuild=True, pick=e.value)).props(
                            "outlined dense options-dense").classes("w-full")
                    with ui.element("div"):
                        ui.html("How many", sanitize=False).classes("itg-field-label")
                        seg([(str(n), str(n), "") for n in ex.COUNTS], str(o.count), lambda k: self._set_export(rebuild=True, count=int(k)))
                if o.pick == "picked":
                    with ui.element("div").classes("gtr-picklist"):
                        for g in filter_games(self._games, platform=o.platform, sort="name"):
                            ui.checkbox(g.display_title, value=g.key in o.picked,
                                        on_change=lambda e, k=g.key: self._toggle_pick(k, e.value)).props("dense")
                switch_row("Show percentages", "", o.show_pct, lambda v: self._set_export(show_pct=v))
                switch_row("Show platform badges", "", o.show_badges, lambda v: self._set_export(show_badges=v))
            elif not lib:
                switch_row("Show platform badge", "", o.show_badges, lambda v: self._set_export(show_badges=v))
            switch_row("Cover art background", "Blurred art from the first game in the grid" if lib else "Blurred art from this game",
                       o.cover_background, lambda v: self._set_export(cover_background=v))
        w, h = ex.LAYOUTS[o.layout]
        self.ui_elements["exp_size"].set_text(f"{w} × {h} PNG · preview updates as you change options")

    def _set_export_format(self, fmt: str) -> None:
        self._export_format = "spreadsheet" if fmt == "spreadsheet" else "image"
        self.ui_elements["exp_img"].set_content("")
        self._render_export_options()
        self._schedule_preview()

    def _set_sheet(self, **changes: Any) -> None:
        for k, v in changes.items():
            setattr(self._sheet_opts, k, v)
        self._render_export_options()
        self._schedule_preview()

    def _build_spreadsheet_options(self, label, seg) -> None:
        o = self._sheet_opts
        label("Platforms")
        seg([("all", "All", ""), (PSN, "PlayStation", ""), (STEAM, "Steam", "")], o.platform, lambda k: self._set_sheet(platform=k))
        label("Include")
        switch_row("Hidden games", "Listed with a Hidden column; never counted in the summary", o.include_hidden,
                   lambda v: self._set_sheet(include_hidden=v))
        switch_row("Unlock history sheet", "Every unlock, completion, new game and milestone since the first import",
                   o.include_history, lambda v: self._set_sheet(include_history=v))
        hint("Games lists every game with completion, trophies or achievements, playtime and dates. Summary has the totals. "
             "Numbers and dates stay real values, so the file sorts and filters in Excel, Numbers, LibreOffice or Google Sheets.")
        self.ui_elements["exp_size"].set_text("XLSX spreadsheet · preview shows the first rows of the Games sheet")

    def _toggle_pick(self, key: str, on: bool) -> None:
        picked = [k for k in self._export_opts.picked if k != key] + ([key] if on else [])
        self._set_export(picked=picked)

    def _schedule_preview(self) -> None:
        self._export_render_seq += 1
        seq = self._export_render_seq
        layout_schedule(0.25, lambda: self._render_preview(seq), once=True)

    def _render_export(self, as_png: bool = False):
        from ...game_tracker.export import ExportOptions, render_games_image

        image = render_games_image(ExportOptions.from_dict(self._export_opts.to_dict()), store=self.service.store)
        if image is None:
            return None
        buf = io.BytesIO()
        if as_png:
            image.convert("RGB").save(buf, "PNG")
            return buf.getvalue()
        preview = image.convert("RGB")
        preview.thumbnail((1280, 1280))
        preview.save(buf, "JPEG", quality=86)
        return buf.getvalue()

    async def _render_preview(self, seq: int) -> None:
        if seq != self._export_render_seq or self._export_opts is None:
            return
        if self._export_format == "spreadsheet":
            await self._render_sheet_preview(seq)
            return
        try:
            data = await run.io_bound(self._render_export)
        except Exception as e:
            logger.error("Games Tracker export preview failed: %s", e, exc_info=True)
            data = None
            failed = True
        else:
            failed = False
        if seq != self._export_render_seq:
            return
        if data is None:
            text = "Couldn't draw the preview. Details are in the log." if failed else "Nothing to export with these options yet."
            self.ui_elements["exp_img"].set_content(f'<div class="itg-empty">{icon_html("image_not_supported")} {esc(text)}</div>')
            return
        self.ui_elements["exp_img"].set_content(
            f'<img alt="Export preview" src="data:image/jpeg;base64,{base64.b64encode(data).decode()}">')

    async def _render_sheet_preview(self, seq: int) -> None:
        from ...game_tracker.spreadsheet import SpreadsheetOptions, preview_rows

        opts = SpreadsheetOptions.from_dict(self._sheet_opts.to_dict())
        try:
            data = await run.io_bound(preview_rows, self.service.store, opts)
        except Exception as e:
            logger.error("Games Tracker spreadsheet preview failed: %s", e, exc_info=True)
            data = None
        if seq != self._export_render_seq:
            return
        if not data or not data["games"]:
            text = "No games to list with these options." if data else "Couldn't build the preview. Details are in the log."
            self.ui_elements["exp_img"].set_content(f'<div class="itg-empty">{icon_html("table_view")} {esc(text)}</div>')
            return
        self.ui_elements["exp_img"].set_content(sheet_preview_html(data))
        events = f" · {data['events']:,} history row{'s' if data['events'] != 1 else ''}" if "Unlock history" in data["sheets"] else ""
        self.ui_elements["exp_size"].set_text(
            f"{data['games']:,} game{'s' if data['games'] != 1 else ''}{events} · sheets: {', '.join(data['sheets'])}")

    async def _save_spreadsheet(self) -> None:
        from ...game_tracker.spreadsheet import SpreadsheetOptions, save_spreadsheet

        self._remember_export()
        opts = SpreadsheetOptions.from_dict(self._sheet_opts.to_dict())
        try:
            path = await run.io_bound(save_spreadsheet, self.service.store, opts)
        except Exception as e:
            logger.error("Games Tracker spreadsheet export failed: %s", e, exc_info=True)
            notify("", type="error", source="game_tracker", title="Couldn't save the spreadsheet",
                   hint="Check there's free space, the Mycelian data folder is writable and the file isn't open in Excel, then try again.",
                   details=f"{type(e).__name__}: {e}")
            return
        if not path:
            notify("Change the platforms or include hidden games so there's something to list.", type="info", source="game_tracker",
                   title="Nothing to export")
            return
        self.ui_elements["exp_saved"].set_text(f"Saved to {path}")
        notify(f"Saved to {path}.", type="success", source="game_tracker", title="Games spreadsheet saved",
               actions=[{"kind": "call", "label": "Open folder", "icon": "folder_open", "fn": self._open_exports_folder},
                        {"kind": "copy", "text": path, "label": "Copy path"}])

    async def _save_export(self) -> None:
        from ...game_tracker.export import ExportOptions, render_games_image, save_export_image

        self._remember_export()
        opts = ExportOptions.from_dict(self._export_opts.to_dict())

        def work():
            image = render_games_image(opts, store=self.service.store)
            return save_export_image(image, opts) if image is not None else None

        try:
            path = await run.io_bound(work)
        except Exception as e:
            logger.error("Games Tracker export failed: %s", e, exc_info=True)
            notify("", type="error", source="game_tracker", title="Couldn't save the image",
                   hint="Check there's free space and the Mycelian data folder is writable, then try again.",
                   details=f"{type(e).__name__}: {e}")
            return
        if not path:
            notify("Change the options so there's something to draw.", type="info", source="game_tracker", title="Nothing to export")
            return
        self.ui_elements["exp_saved"].set_text(f"Saved to {path}")
        notify(f"Saved to {path}.", type="success", source="game_tracker", title="Games image saved",
               actions=[{"kind": "call", "label": "Open folder", "icon": "folder_open", "fn": self._open_exports_folder},
                        {"kind": "copy", "text": path, "label": "Copy path"}])

    async def _copy_export(self) -> None:
        data = await run.io_bound(lambda: self._render_export(as_png=True))
        if not data:
            notify("Change the options so there's something to draw.", type="info", source="game_tracker", title="Nothing to copy")
            return
        b64 = base64.b64encode(data).decode()
        ok = await ui.run_javascript(
            "(async () => { try { const r = await fetch('data:image/png;base64," + b64 + "'); const b = await r.blob();"
            " await navigator.clipboard.write([new ClipboardItem({'image/png': b})]); return true; } catch (e) { return false; } })()",
            timeout=10,
        )
        if ok:
            notify("", type="success", source="game_tracker", title="Image copied")
        else:
            notify("This window can't put images on the clipboard. Use Save PNG instead.", type="warning", source="game_tracker",
                   title="Couldn't copy the image")

    @staticmethod
    def _open_exports_folder() -> None:
        from pathlib import Path

        from ...path_utils import get_data_path, reveal_in_file_manager

        folder = Path(get_data_path("exports"))
        folder.mkdir(parents=True, exist_ok=True)
        reveal_in_file_manager(folder)

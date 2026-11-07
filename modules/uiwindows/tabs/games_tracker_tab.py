# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Games Tracker main tab page (PSN and Steam libraries, trophy/achievement progress, export image)."""

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
from ...game_tracker import goals as goal_kinds
from ...game_tracker.goals import GameGoal
from ...game_tracker.speedrun import TIMING_LABELS, speedruns_public
from ...game_tracker.models import PSN, STEAM, GameEvent, TrackedGame, TrackerSettings, grade_counts
from ...game_tracker.service import get_service
from ...notification_engine import nav_actions_settings, notify
from ...ui_buttons import outline_button, primary_button
from ...ui_timer import layout_schedule
from .integration_ui import (
    IntegrationPage,
    brand_svg,
    esc,
    field,
    field_label,
    fields,
    hint,
    icon_html,
    section,
    switch_row,
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
/* Goals */
.gtr-goals { display: flex; flex-direction: column; gap: 4px; margin-top: 9px; }
.gtr-goal { display: flex; align-items: center; gap: 7px; min-width: 0; font-size: .72rem; color: var(--color-text-secondary); font-variant-numeric: tabular-nums; }
.gtr-goal .itg-icon { font-size: 14px; color: var(--color-text-muted); flex: 0 0 auto; }
.gtr-goal .n { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gtr-goal .c { flex: 0 0 auto; color: var(--color-text-muted); font-size: .66rem; }
.gtr-goal .p { flex: 0 0 auto; min-width: 32px; text-align: right; font-weight: 650; }
.gtr-goal .gtr-minibar { width: 56px; }
.gtr-goal.is-done .itg-icon, .gtr-goal.is-done .p { color: var(--color-success); }
.gtr-goal.is-done .gtr-minibar > span, .gtr-dgoal.is-done .gtr-minibar > span { background: var(--color-success); }
.gtr-srs { display: flex; flex-direction: column; gap: 3px; margin-top: 8px; }
.gtr-sr { display: flex; align-items: center; gap: 7px; min-width: 0; font-size: .72rem; color: var(--color-text-secondary);
  font-variant-numeric: tabular-nums; }
.gtr-sr .itg-icon { font-size: 14px; color: var(--color-text-muted); flex: 0 0 auto; }
.gtr-sr .c { font-weight: 650; color: var(--color-text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.gtr-sr .b { white-space: nowrap; }
.gtr-sr .b b { color: var(--color-text-primary); }
.gtr-sr .r { margin-left: auto; color: var(--color-text-muted); font-size: .66rem; white-space: nowrap; }
.gtr-sr-err .itg-icon { color: var(--color-warning); }
.gtr-dsr { display: flex; flex-direction: column; gap: 4px; padding: 8px 9px; border-radius: 8px; background: var(--color-bg-base);
  border: 1px solid var(--color-border-subtle); margin-bottom: 6px; }
.gtr-dsr-top { display: flex; align-items: center; gap: 6px; min-width: 0; }
.gtr-dsr-top .gtr-sr { flex: 1 1 auto; margin: 0; }
.gtr-dsr-top .q-btn { min-height: 26px; min-width: 26px; }
.gtr-dsr-file { display: flex; gap: 8px; align-items: center; font-size: .66rem; color: var(--color-text-muted); min-width: 0; }
.gtr-dsr-file .p { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; min-width: 0; flex: 1 1 auto; }
.gtr-dsr-file .e { color: var(--color-warning); }
.gtr-goal-more { font-size: .66rem; color: var(--color-text-muted); padding-left: 21px; }
.gtr-dgoal { display: flex; align-items: center; gap: 6px; padding: 7px 8px; border-radius: 8px; background: var(--color-bg-base);
  border: 1px solid var(--color-border-subtle); margin-bottom: 6px; }
.gtr-dgoal .gtr-goal { flex: 1 1 auto; }
.gtr-dgoal .q-btn { min-height: 26px; min-width: 26px; }
.gtr-dgoal.is-done { border-color: color-mix(in srgb, var(--color-success) 40%, transparent); }
.gtr-goalform { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; padding: 10px; border-radius: 8px; border: 1px dashed var(--color-border-default); }
.gtr-goalform .gtr-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.gtr-goalform .gtr-row > * { flex: 1 1 140px; min-width: 0; }
.gtr-goalform-title { font-size: .8rem; font-weight: 700; color: var(--color-text-primary); }
/* Drag the drawer's left edge to resize it (sized by mycelianGtrDrawerResize). */
.gtr-drawer { position: relative; }
.gtr-dresize { position: absolute; left: -4px; top: 0; bottom: 0; width: 9px; z-index: 5; cursor: col-resize; }
.gtr-dresize::after { content: ''; position: absolute; left: 3px; top: 0; bottom: 0; width: 3px; border-radius: 2px;
  background: transparent; transition: background .15s ease; }
.gtr-dresize:hover::after, .gtr-dresize.is-dragging::after { background: var(--color-primary); }
.gtr-pick-item { display: flex; align-items: center; gap: 8px; }
.gtr-pick-item img { width: 22px; height: 22px; border-radius: 4px; object-fit: cover; }
.gtr-pick-item .e { margin-left: auto; font-size: .66rem; color: var(--color-success); }
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
STATUS_OPTIONS = {"all": "All games", "progress": "In progress", "not_started": "Not started", "done": "Completed",
                  "goals": "Has goals", "no_goals": "No goals", "hidden": "Hidden"}
SORT_OPTIONS = {"recent": "Recently played", "progress": "Most complete", "least": "Least complete", "name": "Name A–Z",
                "name_desc": "Name Z–A", "earned": "Most trophies / achievements", "playtime": "Most playtime",
                "added": "Recently added"}
BACKLOG_STATUS_OPTIONS = {"all": "All goals", "active": "Goals open", "complete": "Goals complete"}
BACKLOG_SORT_OPTIONS = {"closest": "Closest to done", "least": "Least progress", "recent": "Recently played", "name": "Name A–Z",
                        "most_goals": "Most goals", "newest": "Newest goal"}
PLATFORM_FILTERS = (("all", "All"), (PSN, "PlayStation"), (STEAM, "Steam"))
CARD_GOAL_ROWS = 3
CARD_SPEEDRUN_ROWS = 2
GOAL_TYPE_OPTIONS = {
    goal_kinds.MANUAL: "Manual (count it yourself)",
    goal_kinds.COMPLETE: "Platinum / 100%",
    goal_kinds.COUNT: "Trophies / achievements",
    goal_kinds.PERCENT: "Completion %",
    goal_kinds.UNLOCK: "Specific trophy / achievement",
}
_GRADE_OPTIONS = {"": "Any grade", "bronze": "Bronze", "silver": "Silver", "gold": "Gold", "platinum": "Platinum"}
_TARGET_LABELS = {goal_kinds.MANUAL: "Count to reach", goal_kinds.COUNT: "How many", goal_kinds.PERCENT: "Percent"}


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


def _matches(g: TrackedGame, q: str, platform: str) -> bool:
    if platform != "all" and g.platform != platform:
        return False
    return not q or q in g.display_title.casefold() or q in g.title.casefold() or q == g.game_id.casefold()


def filter_games(
    games: Sequence[TrackedGame],
    *,
    q: str = "",
    platform: str = "all",
    status: str = "all",
    sort: str = "recent",
    goals: Optional[Dict[str, List[GameGoal]]] = None,
    exclude_goal_games: bool = False,
) -> List[TrackedGame]:
    """The Library list. ``goals`` (by game key) drives the goal filters; ``exclude_goal_games`` leaves out games that
    have goals (they're in the Backlog section then), except under the Hidden filter."""
    q = (q or "").strip().casefold()
    goals = goals or {}
    out = []
    for g in games:
        if not _matches(g, q, platform):
            continue
        has_goals = bool(goals.get(g.key))
        if status == "hidden":
            if not g.hidden:
                continue
        elif g.hidden:
            continue
        elif exclude_goal_games and has_goals:
            continue
        elif status == "progress" and g.is_complete:
            continue
        elif status == "not_started" and g.earned_total > 0:
            continue
        elif status == "done" and not g.is_complete:
            continue
        elif status == "goals" and not has_goals:
            continue
        elif status == "no_goals" and has_goals:
            continue
        out.append(g)
    name = lambda g: g.display_title.casefold()  # noqa: E731
    if sort == "name":
        out.sort(key=name)
    elif sort == "name_desc":
        out.sort(key=name, reverse=True)
    elif sort == "progress":
        out.sort(key=lambda g: (-g.progress, name(g)))
    elif sort == "least":
        out.sort(key=lambda g: (g.progress, name(g)))
    elif sort == "earned":
        out.sort(key=lambda g: (-g.earned_total, name(g)))
    elif sort == "playtime":
        out.sort(key=lambda g: (-g.playtime_min, -g.last_played))
    elif sort == "added":
        out.sort(key=lambda g: (-g.first_seen, name(g)))
    else:
        out.sort(key=lambda g: -g.last_played)
    return out


def filter_backlog(
    games: Sequence[TrackedGame],
    goals: Dict[str, List[GameGoal]],
    *,
    q: str = "",
    platform: str = "all",
    status: str = "all",
    sort: str = "closest",
) -> List[TrackedGame]:
    """The Backlog list: visible games that have goals, filtered by whether their goals are all done."""
    q = (q or "").strip().casefold()
    rows = []
    for g in games:
        game_goals = goals.get(g.key) or []
        if not game_goals or g.hidden or not _matches(g, q, platform):
            continue
        summary = goal_kinds.goals_summary(game_goals, g)
        if status == "active" and summary["goals_complete"]:
            continue
        if status == "complete" and not summary["goals_complete"]:
            continue
        rows.append((g, summary, max(x.created_at for x in game_goals)))
    name = lambda r: r[0].display_title.casefold()  # noqa: E731
    if sort == "least":
        rows.sort(key=lambda r: (r[1]["goals_progress"], name(r)))
    elif sort == "recent":
        rows.sort(key=lambda r: -r[0].last_played)
    elif sort == "name":
        rows.sort(key=name)
    elif sort == "most_goals":
        rows.sort(key=lambda r: (-r[1]["goals_total"], name(r)))
    elif sort == "newest":
        rows.sort(key=lambda r: -r[2])
    else:  # closest to done: open sets first, then the furthest along
        rows.sort(key=lambda r: (r[1]["goals_complete"], -r[1]["goals_progress"], name(r)))
    return [r[0] for r in rows]


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


def goal_rows_html(rows: Sequence[Dict[str, Any]], limit: Optional[int] = None) -> str:
    """Goal rows (name, bar, percent) from ``goals.goal_public`` dicts; ``limit`` adds "+N more goals"."""
    shown = list(rows if limit is None else rows[:limit])
    body = "".join(
        f'<div class="gtr-goal{" is-done" if r["completed"] else ""}">'
        f'{icon_html("check_circle" if r["completed"] else ("flag" if r["auto"] else "radio_button_unchecked"))}'
        f'<span class="n" title="{esc(r["name"])}">{esc(r["name"])}</span>'
        f'<span class="c">{esc(_goal_count(r))}</span>'
        f'<span class="gtr-minibar"><span style="width:{r["progress"]}%"></span></span>'
        f'<span class="p">{r["progress"]}%</span></div>'
        for r in shown
    )
    more = len(rows) - len(shown)
    if more > 0:
        body += f'<div class="gtr-goal-more">+{more} more goal{"s" if more != 1 else ""}</div>'
    return f'<div class="gtr-goals">{body}</div>'


def achievement_options(items: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    """Picker options for a "specific trophy / achievement" goal: ``{id: "Name · Gold · earned"}``."""
    out = {}
    for a in items:
        grade = str(a.get("grade") or "")
        parts = [str(a.get("name") or a.get("id"))]
        if grade and grade != "achievement":
            parts.append(grade.title())
        if a.get("earned"):
            parts.append("earned")
        out[str(a["id"])] = " · ".join(parts)
    return out


def _goal_count(r: Dict[str, Any]) -> str:
    if r["kind"] in (goal_kinds.MANUAL, goal_kinds.COUNT):
        return f'{r["current"]:,} / {r["target"]:,}'
    if r["kind"] == goal_kinds.PERCENT:
        return f'{r["current"]}% / {r["target"]}%'
    return ""


def speedrun_rows_html(rows: Sequence[Dict[str, Any]], limit: Optional[int] = None) -> str:
    """One line per split file (``speedrun.speedruns_public`` rows): category, personal best, attempts."""
    shown = list(rows if limit is None else rows[:limit])
    body = ""
    for r in shown:
        if r.get("read"):
            best = f'PB <b>{esc(r["pb"])}</b>' if r.get("pb") else "No finished run yet"
            runs = f'{r["attempts"]:,} attempt{"s" if r["attempts"] != 1 else ""} · {r["finished"]:,} finished'
        else:
            best, runs = "Not read yet", ""
        warn = f'<span class="gtr-sr-err" title="{esc(r["error"])}">{icon_html("error_outline")}</span>' if r.get("error") else ""
        body += (f'<div class="gtr-sr">{icon_html("timer")}<span class="c" title="{esc(r["file"])}">{esc(r["category"])}</span>'
                 f'<span class="b">{best}</span><span class="r">{runs}</span>{warn}</div>')
    more = len(rows) - len(shown)
    if more > 0:
        body += f'<div class="gtr-goal-more">+{more} more categor{"ies" if more != 1 else "y"}</div>'
    return f'<div class="gtr-srs">{body}</div>' if body else ""


def card_parts(game: TrackedGame, now: Optional[float] = None, goals: Sequence[GameGoal] = (),
               speedruns: Sequence[Dict[str, Any]] = ()) -> Dict[str, str]:
    """(top HTML, footer HTML, footer detail, card classes) for one game card."""
    done = game.is_complete
    summary = goal_kinds.goals_summary(goals, game) if goals else None
    chips = f'<span class="itg-os-chip">{esc(game.platform_label or ("Steam" if game.platform == STEAM else "PlayStation"))}</span>'
    if done:
        chips += f'<span class="itg-pill is-ok gtr-mini">{icon_html("workspace_premium")}{"Platinum" if game.platform == PSN else "Perfect"}</span>'
    if summary and summary["goals_complete"]:
        chips += f'<span class="itg-pill is-ok gtr-mini">{icon_html("flag")}Goals done</span>'
    elif summary:
        chips += f'<span class="itg-os-chip">{summary["goals_done"]} / {summary["goals_total"]} goals</span>'
    if game.hidden:
        chips += '<span class="itg-os-chip">Hidden</span>'
    chips += f'<span class="gtr-sub">Played {esc(ago(game.last_played, now))}</span>'
    top = (
        f'{cover_html(game)}<div class="gtr-text"><div class="itg-hook-name">{esc(game.display_title)}</div>'
        f'<div class="itg-os">{chips}</div><div class="gtr-bar"><span style="width:{game.progress}%"></span></div>'
        f'{goal_rows_html(summary["goals"], CARD_GOAL_ROWS) if summary else ""}'
        f'{speedrun_rows_html(speedruns, CARD_SPEEDRUN_ROWS)}</div>'
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


DRAWER_MIN_WIDTH = 380
DRAWER_MAX_WIDTH = 1400
DRAWER_DEFAULT_WIDTH = 460
_DRAWER_WIDTH_EVENT = "mycelian-gtr-drawer-width"

# Drag the drawer's left edge (.gtr-dresize) to resize it; the width is reported once on release.
_DRAWER_RESIZE_JS = r"""
(function () {
  if (window.mycelianGtrDrawerResize) { return; }
  window.mycelianGtrDrawerResize = true;
  var drag = null;
  document.addEventListener("mousedown", function (ev) {
    var handle = ev.button === 0 && ev.target && ev.target.closest ? ev.target.closest(".gtr-dresize") : null;
    var aside = handle ? handle.closest(".gtr-drawer") : null;
    if (!aside) { return; }
    drag = { aside: aside, handle: handle, x: ev.clientX, w: aside.getBoundingClientRect().width, last: null };
    handle.classList.add("is-dragging");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    ev.preventDefault();
    ev.stopPropagation();
  }, true);
  document.addEventListener("mousemove", function (ev) {
    if (!drag) { return; }
    var max = Math.min(__MAX__, window.innerWidth - 40);
    var w = Math.round(Math.max(__MIN__, Math.min(max, drag.w + (drag.x - ev.clientX))));
    drag.aside.style.width = w + "px";
    drag.last = w;
  });
  document.addEventListener("mouseup", function () {
    if (!drag) { return; }
    drag.handle.classList.remove("is-dragging");
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    if (drag.last !== null && typeof emitEvent === "function") { emitEvent("__EVENT__", drag.last); }
    drag = null;
  });
})();
""".replace("__MIN__", str(DRAWER_MIN_WIDTH)).replace("__MAX__", str(DRAWER_MAX_WIDTH)).replace("__EVENT__", _DRAWER_WIDTH_EVENT)

# Clients already listening for the drawer width (one page-level listener each; adding it again
# on every rebuild makes NiceGUI re-render the page, as customsources found with its splitter).
_drawer_width_clients: set = set()


def clamp_drawer_width(value: Any) -> int:
    try:
        width = int(float(value))
    except (TypeError, ValueError):
        return DRAWER_DEFAULT_WIDTH
    return max(DRAWER_MIN_WIDTH, min(DRAWER_MAX_WIDTH, width))


def save_drawer_width(value: Any) -> int:
    """Remember the drawer width in the tracker settings (``view.drawer_width``)."""
    width = clamp_drawer_width(value)
    service = get_service()
    s = service.settings
    if s.view.get("drawer_width") != width:
        service.save_settings(TrackerSettings.from_dict({**s.to_dict(), "view": {**s.view, "drawer_width": width}}))
    return width


def _on_drawer_width(e: Any) -> None:
    try:
        save_drawer_width(getattr(e, "args", None))
    except Exception:
        logger.debug("Games Tracker: drawer width not saved", exc_info=True)


def _install_drawer_resize() -> None:
    """Load the resize script into the page and listen for the width once per client."""
    try:
        from nicegui import context

        client = context.client
        if client.id not in _drawer_width_clients:
            _drawer_width_clients.add(client.id)
            client.layout.on(_DRAWER_WIDTH_EVENT, _on_drawer_width)
            client.on_delete(lambda: _drawer_width_clients.discard(client.id))
        ui.run_javascript(_DRAWER_RESIZE_JS)
    except Exception:
        logger.debug("Games Tracker: drawer resize unavailable", exc_info=True)


class GamesTrackerTab:
    name = "Games Tracker"

    def __init__(self) -> None:
        self.ui_elements: Dict[str, Any] = {}
        self._page: Optional[IntegrationPage] = None
        self._games: List[TrackedGame] = []
        self._summary: Dict[str, Any] = {}
        self._status: Dict[str, Dict[str, Any]] = {}
        self._deltas: Dict[str, int] = {}
        self._goals: Dict[str, List[GameGoal]] = {}
        self._speedruns: Dict[str, List[Dict[str, Any]]] = {}  # game key -> speedrun.speedruns_public rows
        self._views: Dict[str, Dict[str, str]] = {
            "library": {"q": "", "platform": "all", "status": "all", "sort": "recent"},
            "backlog": {"q": "", "platform": "all", "status": "all", "sort": "closest"},
        }
        self._limit = PAGE_SIZE
        self._goal_form: Dict[str, Any] = {}
        self._achievements: List[Dict[str, Any]] = []
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
        """The options are saved in Settings → Games Tracker; per-game edits save in their drawer."""
        return False

    def build(self, parent_container) -> None:
        from ...help_system.help_render import inject_page_stylesheet, promote_color_importance, scope_selectors
        from .game_hooks_tab import GAME_HOOKS_CSS  # the shared .itg-hook card styles

        inject_page_stylesheet(_HOOKS_STYLE_ID, promote_color_importance(scope_selectors(GAME_HOOKS_CSS, ".itg")))
        inject_page_stylesheet(_STYLE_ID, promote_color_importance(scope_selectors(GAMES_TRACKER_CSS, ".itg")))
        self._restore_views(self.service.settings.view)
        self._page = page = IntegrationPage(
            parent_container,
            "games_tracker",
            "Games Tracker",
            "PlayStation and Steam libraries, trophies and achievements",
            icon="emoji_events",
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
                    self.ui_elements["options_btn"] = outline_button("Options", self._open_options, icon="tune")
                self.ui_elements["banner"] = ui.html("", sanitize=False).classes("w-full")
                self.ui_elements["overview"] = ui.html("", sanitize=False).classes("w-full")
                self.ui_elements["empty"] = ui.element("div").classes("w-full")
            self.ui_elements["backlog"] = backlog = section("flag", "Backlog")
            with backlog:
                self._build_toolbar("backlog")
                self.ui_elements["bl_grid"] = ui.element("div").classes("itg-hooks")
                self.ui_elements["bl_none"] = ui.label("").classes("gtr-none")
            self.ui_elements["library"] = lib = section("sports_esports", "Library")
            with lib:
                self._build_toolbar("library")
                self.ui_elements["grid"] = ui.element("div").classes("itg-hooks")
                self.ui_elements["none"] = ui.label("No games match these filters.").classes("gtr-none")
                with ui.element("div").classes("gtr-more") as more:
                    self.ui_elements["more_btn"] = outline_button("Show more", self._show_more, icon="expand_more")
                self.ui_elements["more"] = more
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
            "goals": svc.goals_by_game(),
            "speedruns": {k: speedruns_public(v) for k, v in svc.speedruns_by_game().items()},
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
            self._goals = data.get("goals") or {}
            self._speedruns = data.get("speedruns") or {}
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
        backlog = self.ui_elements.get("backlog")
        if backlog is not None:
            backlog.set_visibility(has_games and self._backlog_on)

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
    def _open_options() -> None:
        from ...help_system.contextual_help import navigate_to_settings_subtab

        navigate_to_settings_subtab("Games Tracker")

    @staticmethod
    def _goto(subtab: str) -> None:
        from ...help_system.contextual_help import navigate_to_settings_subtab

        navigate_to_settings_subtab(subtab)

    # ----- library -----

    def _build_toolbar(self, which: str = "library") -> None:
        backlog = which == "backlog"
        view = self._views[which]
        statuses, sorts = (BACKLOG_STATUS_OPTIONS, BACKLOG_SORT_OPTIONS) if backlog else (STATUS_OPTIONS, SORT_OPTIONS)
        what = "Search the backlog" if backlog else "Search games"
        with ui.element("div").classes("gtr-tools"):
            search = ui.input(placeholder=what, value=view["q"], on_change=lambda e: self._set_filter(which, q=e.value or "")).props(
                f'outlined dense clearable debounce=250 aria-label="{what}"'
            ).classes("gtr-search")
            with search.add_slot("prepend"):
                ui.icon("search").classes("text-[18px]")
            with ui.element("div").classes("gtr-seg").props('role=group aria-label="Platform"'):
                self.ui_elements["bl_plat_btns" if backlog else "plat_btns"] = {
                    key: ui.button(label, on_click=lambda k=key: self._set_filter(which, platform=k)).props("flat no-caps dense")
                    for key, label in PLATFORM_FILTERS
                }
            ui.select(statuses, value=view["status"], on_change=lambda e: self._set_filter(which, status=e.value)).props(
                'outlined dense options-dense aria-label="Status"'
            ).classes("gtr-select")
            ui.select(sorts, value=view["sort"], on_change=lambda e: self._set_filter(which, sort=e.value)).props(
                'outlined dense options-dense aria-label="Sort"'
            ).classes("gtr-select")
        self._paint_platform_buttons(which)

    def _paint_platform_buttons(self, which: str = "library") -> None:
        current = self._views[which]["platform"]
        for key, btn in (self.ui_elements.get("bl_plat_btns" if which == "backlog" else "plat_btns") or {}).items():
            on = key == current
            btn.classes(add="is-on") if on else btn.classes(remove="is-on")
            btn.props(f"aria-pressed={'true' if on else 'false'}")

    def _restore_views(self, saved: Dict[str, Any]) -> None:
        """The filters and sorts of the last session (the search box always starts empty)."""
        for which, opts in (("library", (STATUS_OPTIONS, SORT_OPTIONS)), ("backlog", (BACKLOG_STATUS_OPTIONS, BACKLOG_SORT_OPTIONS))):
            raw = (saved or {}).get(which) or {}
            view = self._views[which]
            if raw.get("platform") in dict(PLATFORM_FILTERS):
                view["platform"] = raw["platform"]
            if raw.get("status") in opts[0]:
                view["status"] = raw["status"]
            if raw.get("sort") in opts[1]:
                view["sort"] = raw["sort"]

    def _remember_views(self) -> None:
        try:
            s = self.service.settings
            # Merge: the view dict also holds the drawer width.
            wanted = {**s.view, **{w: {k: v for k, v in view.items() if k != "q"} for w, view in self._views.items()}}
            if s.view != wanted:
                self.service.save_settings(TrackerSettings.from_dict({**s.to_dict(), "view": wanted}))
        except Exception:
            logger.debug("Games Tracker: list filters not saved", exc_info=True)

    def _set_filter(self, which: str = "library", **changes: Any) -> None:
        view = self._views[which]
        defaults = {"platform": "all", "status": "all", "sort": "closest" if which == "backlog" else "recent"}
        for key in ("q", "platform", "status", "sort"):
            if key in changes:
                view[key] = changes[key] or defaults.get(key, "")
        if "platform" in changes:
            self._paint_platform_buttons(which)
        if any(k in changes for k in ("platform", "status", "sort")):
            self._remember_views()
        if which == "library":
            self._limit = PAGE_SIZE
        self._render_grid()

    def _show_more(self) -> None:
        self._limit += PAGE_SIZE
        self._render_grid()

    @property
    def _backlog_on(self) -> bool:
        return bool(self.service.settings.backlog_section)

    def visible_games(self) -> List[TrackedGame]:
        v = self._views["library"]
        return filter_games(self._games, q=v["q"], platform=v["platform"], status=v["status"], sort=v["sort"],
                            goals=self._goals, exclude_goal_games=self._backlog_on)

    def visible_backlog(self) -> List[TrackedGame]:
        v = self._views["backlog"]
        return filter_backlog(self._games, self._goals, q=v["q"], platform=v["platform"], status=v["status"], sort=v["sort"])

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
        self._set_head("library", "sports_esports", "Library", f"Showing {len(shown):,} of {len(games):,}")
        self._render_backlog(now)

    def _render_backlog(self, now: float) -> None:
        grid = self.ui_elements.get("bl_grid")
        if grid is None:
            return
        grid.clear()
        backlog = self.ui_elements.get("backlog")
        if backlog is not None:
            backlog.set_visibility(bool(self._games) and self._backlog_on)
        if not self._backlog_on:
            return
        games = self.visible_backlog()
        with grid:
            for g in games:
                self._build_card(g, now)
        total = sum(1 for g in self._games if self._goals.get(g.key) and not g.hidden)
        none = self.ui_elements["bl_none"]
        none.set_text("No games in the backlog match these filters." if total else
                      "No goals yet. Open a game's settings (the gear on its card) and add a goal to put it here.")
        none.set_visibility(not games)
        self._set_head("backlog", "flag", "Backlog", f"{len(games):,} of {total:,} game{'s' if total != 1 else ''}")

    def _set_head(self, key: str, icon: str, title: str, right: str) -> None:
        sec = self.ui_elements.get(key)
        if sec is None:
            return
        heads = [c for c in sec.default_slot.children if "itg-sec-head" in c.classes]
        if heads:
            heads[0].set_content(f'{icon_html(icon)}{esc(title)}<span class="itg-sec-right">{esc(right)}</span>')

    def _build_card(self, game: TrackedGame, now: float) -> None:
        parts = card_parts(game, now, self._goals.get(game.key) or (), self._speedruns.get(game.key) or ())
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

    def _dialog_parent(self) -> Any:
        """Small dialogs live in the page (also when opened from code, outside a click handler)."""
        return self._page.root if self._page is not None else contextlib.nullcontext()

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
        body.style(f"width:{clamp_drawer_width(self.service.settings.view.get('drawer_width'))}px")
        with body:
            ui.element("div").classes("gtr-dresize").props('role=separator aria-orientation=vertical aria-label="Resize"').tooltip(
                "Drag to resize")
            self._build_drawer_content(game)
        _install_drawer_resize()
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
            with section("flag", "Goals"):
                self.ui_elements["d_goals"] = ui.element("div").classes("w-full")
                self._render_drawer_goals(self._goals.get(g.key) or [])
                self._build_goal_form(g)
            with section("timer", "Speedrun info"):
                self.ui_elements["d_speedruns"] = ui.element("div").classes("w-full")
                self._render_drawer_speedruns(self._speedruns.get(g.key) or [])
                self._build_speedrun_form(g)
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

    # ----- drawer: speedrun info (LiveSplit split files) -----

    def _render_drawer_speedruns(self, rows: Sequence[Dict[str, Any]]) -> None:
        box = self.ui_elements.get("d_speedruns")
        if box is None or self._drawer_game is None:
            return
        box.clear()
        with box:
            if not rows:
                ui.html('<div class="itg-list"><div class="itg-empty">No split files yet. Add a LiveSplit file (.lss) for each '
                        "category you run: its attempts and personal best show here and on the Game panel, and update "
                        "whenever LiveSplit saves.</div></div>", sanitize=False).classes("w-full")
            for n, r in enumerate(rows):
                with ui.element("div").classes("gtr-dsr"):
                    with ui.element("div").classes("gtr-dsr-top"):
                        ui.html(speedrun_rows_html([r]), sanitize=False).style("flex:1 1 auto;min-width:0")
                        self._goal_btn("edit", f"Edit {r['category']}", lambda row=r: self._edit_speedrun_dialog(row))
                        self._goal_btn("arrow_upward", "Move up", lambda lid=r["id"]: self._speedrun_op("move", lid, -1), disabled=n == 0)
                        self._goal_btn("arrow_downward", "Move down", lambda lid=r["id"]: self._speedrun_op("move", lid, 1),
                                       disabled=n == len(rows) - 1)
                        self._goal_btn("link_off", f"Remove {r['category']}", lambda lid=r["id"]: self._speedrun_op("remove", lid))
                    if r.get("error"):
                        status = f'<span class="e">{esc(r["error"])}</span>'
                    elif r.get("read"):
                        status = f'Read {esc(ago(r["read_at"]))} · {esc(TIMING_LABELS.get(r["timing"], r["timing"]))}'
                    else:
                        status = "Waiting to read"
                    ui.html(f'<div class="gtr-dsr-file">{icon_html("description")}<span class="p" title="{esc(r["file"])}">'
                            f'{esc(r["file_name"])}</span><span>{status}</span></div>', sanitize=False).classes("w-full")

    def _build_speedrun_form(self, g: TrackedGame) -> None:
        with ui.element("div").classes("gtr-goalform"):
            ui.html("Add a split file", sanitize=False).classes("gtr-goalform-title")
            with ui.element("div"):
                field_label("Category", "Shown with the times, for example Any% or 100%. Leave empty to use the category saved in the file.")
                self.ui_elements["sr_category"] = ui.input(placeholder="From the file (e.g. Any%)").props("outlined dense").classes("w-full")
            with ui.row().classes("justify-end w-full"):
                primary_button("Choose split file…", self._pick_split_file, icon="folder_open")
            hint("Mycelian only reads the file, without locking it, so LiveSplit can keep it open and save it as usual.")

    def _split_file_start(self) -> Optional[str]:
        g = self._drawer_game
        rows = (self._speedruns.get(g.key) if g else None) or [r for rows in self._speedruns.values() for r in rows]
        return os.path.dirname(rows[0]["file"]) if rows else None

    def _pick_split_file(self, *, link_id: str = "") -> None:
        from ..file_picker import open_file_picker

        g = self._drawer_game
        if g is None:
            return

        def picked(result: Any) -> None:
            path = str(result.path)
            category = "" if link_id else str(self.ui_elements["sr_category"].value or "")
            layout_schedule(0.01, lambda: self._link_split_file(path, category, link_id), once=True)

        open_file_picker(title="Choose a LiveSplit split file", subtitle=f"Speedrun info for {g.display_title}",
                         extensions=(".lss",), on_pick=picked, scope="system", icon="timer", start=self._split_file_start(),
                         confirm_label="Use this file")

    async def _link_split_file(self, path: str, category: str = "", link_id: str = "") -> None:
        g = self._drawer_game
        if g is None:
            return
        if link_id:
            link, error = await run.io_bound(lambda: self.service.update_speedrun(link_id, path=path))
        else:
            link, error = await run.io_bound(lambda: self.service.add_speedrun_file(g.platform, g.game_id, path, category))
        if error or link is None:
            notify(error or "Couldn't use that file.", type="warning", source="game_tracker", title="Couldn't link the split file")
            return
        el = self.ui_elements.get("sr_category")
        if el is not None and not link_id:
            el.value = ""
        if link.error:
            notify(link.error, type="warning", source="game_tracker", title="Linked, but couldn't read it yet",
                   hint="Mycelian tries again whenever the file changes.")
        await self._reload_drawer_speedruns()

    async def _speedrun_op(self, op: str, link_id: str, arg: Any = None) -> None:
        if op == "move":
            await run.io_bound(self.service.move_speedrun, link_id, arg)
        elif op == "remove":
            await run.io_bound(self.service.remove_speedrun, link_id)
        await self._reload_drawer_speedruns()

    async def _reload_drawer_speedruns(self) -> None:
        g = self._drawer_game
        if g is None:
            return
        rows = speedruns_public(await run.io_bound(self.service.speedruns_for, g.platform, g.game_id))
        if rows:
            self._speedruns[g.key] = rows
        else:
            self._speedruns.pop(g.key, None)
        if self._drawer_game is g:
            self._render_drawer_speedruns(rows)
        self._needs_refresh = True

    def _edit_speedrun_dialog(self, row: Dict[str, Any]) -> None:
        values: Dict[str, Any] = {"category": row["category"] if row.get("category_set") else "", "timing": row["timing_choice"]}
        with self._dialog_parent(), ui.dialog() as dialog, ui.card().classes("itg-dialog"):
            ui.html(f'{icon_html("timer")}Edit split file', sanitize=False).classes("itg-dialog-title")
            with ui.element("div").classes("w-full"):
                field_label("Category")
                ui.input(value=values["category"], placeholder=row.get("file_category") or "Any%",
                         on_change=lambda e: values.update(category=e.value or "")).props("outlined dense").classes("w-full")
            with ui.element("div").classes("w-full"):
                field_label("Times", "Automatic uses real time, or game time when the file has no real times.")
                ui.select(dict(TIMING_LABELS), value=values["timing"],
                          on_change=lambda e: values.update(timing=e.value)).props("outlined dense options-dense").classes("w-full")
            ui.html(f'<div class="gtr-dsr-file">{icon_html("description")}<span class="p" title="{esc(row["file"])}">'
                    f'{esc(row["file"])}</span></div>', sanitize=False).classes("w-full")
            with ui.element("div").classes("itg-actions-end"):
                outline_button("Change file…", lambda: (dialog.close(), self._pick_split_file(link_id=row["id"])), icon="folder_open")
                outline_button("Cancel", dialog.close)

                async def _save() -> None:
                    dialog.close()
                    _link, error = await run.io_bound(lambda: self.service.update_speedrun(row["id"], **values))
                    if error:
                        notify(error, type="warning", source="game_tracker", title="Couldn't save the split file")
                    await self._reload_drawer_speedruns()

                primary_button("Save", _save, icon="check")
        dialog.open()

    # ----- drawer: goals -----

    def _render_drawer_goals(self, goals: Sequence[GameGoal]) -> None:
        box = self.ui_elements.get("d_goals")
        g = self._drawer_game
        if box is None or g is None:
            return
        box.clear()
        ordered = goal_kinds.sorted_goals(goals)
        with box:
            if not ordered:
                ui.html('<div class="itg-list"><div class="itg-empty">No goals yet. Add one below: a goal of your own that you '
                        "count yourself, or one that follows this game's trophies or achievements.</div></div>",
                        sanitize=False).classes("w-full")
            for n, goal in enumerate(ordered):
                row = goal_kinds.goal_public(goal, g)
                with ui.element("div").classes("gtr-dgoal" + (" is-done" if row["completed"] else "")):
                    ui.html(goal_rows_html([row]), sanitize=False).style("flex:1 1 auto;min-width:0")
                    if goal.kind == goal_kinds.MANUAL:
                        self._goal_btn("remove", f"Count down {row['name']}", lambda gid=goal.id: self._goal_op("adjust", gid, "subtract"))
                        self._goal_btn("add", f"Count up {row['name']}", lambda gid=goal.id: self._goal_op("adjust", gid, "add"))
                    self._goal_btn("edit", f"Edit {row['name']}", lambda gl=goal: self._edit_goal_dialog(gl))
                    self._goal_btn("arrow_upward", "Move up", lambda gid=goal.id: self._goal_op("move", gid, -1), disabled=n == 0)
                    self._goal_btn("arrow_downward", "Move down", lambda gid=goal.id: self._goal_op("move", gid, 1),
                                   disabled=n == len(ordered) - 1)
                    self._goal_btn("delete", f"Delete {row['name']}", lambda gid=goal.id: self._goal_op("remove", gid))

    @staticmethod
    def _goal_btn(icon: str, label: str, handler: Any, disabled: bool = False) -> Any:
        btn = ui.button(icon=icon, on_click=handler).props(f'flat round dense size=sm aria-label="{esc(label)}"').classes("itg-ibtn")
        btn.tooltip(label)
        if disabled:
            btn.props("disable")
        return btn

    async def _goal_op(self, op: str, goal_id: str, arg: Any = None) -> None:
        svc = self.service
        error = ""
        if op == "adjust":
            _goal, error = await run.io_bound(svc.adjust_goal_by_id, goal_id, arg, 1)
        elif op == "move":
            await run.io_bound(svc.move_goal, goal_id, arg)
        elif op == "remove":
            error = "" if await run.io_bound(svc.remove_goal, goal_id) else "That goal no longer exists."
        else:
            return
        if error:
            notify(error, type="warning", source="game_tracker", title="Couldn't change the goal")
        await self._reload_drawer_goals()

    async def _reload_drawer_goals(self) -> None:
        g = self._drawer_game
        if g is None:
            return
        goals = await run.io_bound(self.service.goals_for, g.platform, g.game_id)
        self._goals[g.key] = goals
        if not goals:
            self._goals.pop(g.key, None)
        if self._drawer_game is g:
            self._render_drawer_goals(goals)
        self._needs_refresh = True

    def _build_goal_form(self, g: TrackedGame) -> None:
        psn = g.platform == PSN
        self._goal_form = {"kind": goal_kinds.MANUAL, "name": "", "target": 1, "grade": "", "unlock": ""}
        self._achievements = []
        # Labels sit above the controls (field_label), like every other field in the tab.
        with ui.element("div").classes("gtr-goalform"):
            ui.html("Add a goal", sanitize=False).classes("gtr-goalform-title")
            with ui.element("div"):
                field_label("Type")
                self.ui_elements["gf_kind"] = ui.select(GOAL_TYPE_OPTIONS, value=goal_kinds.MANUAL,
                                                        on_change=lambda e: self._set_goal_form(kind=e.value)).props(
                    "outlined dense options-dense").classes("w-full")
            with ui.element("div").classes("gtr-row"):
                with ui.element("div"):
                    field_label("Goal name")
                    self.ui_elements["gf_name"] = ui.input(placeholder="Get all endings",
                                                           on_change=lambda e: self._set_goal_form(name=e.value or "")).props(
                        "outlined dense").classes("w-full")
                with ui.element("div") as target_box:
                    self.ui_elements["gf_target_label"] = field_label(_TARGET_LABELS[goal_kinds.MANUAL])
                    self.ui_elements["gf_target"] = ui.number(value=1, min=1, max=goal_kinds.MAX_TARGET, step=1, format="%d",
                                                              on_change=lambda e: self._set_goal_form(target=e.value)).props(
                        "outlined dense").classes("w-full")
                self.ui_elements["gf_target_box"] = target_box
                with ui.element("div") as grade_box:
                    field_label("Grade")
                    self.ui_elements["gf_grade"] = ui.select(_GRADE_OPTIONS, value="",
                                                             on_change=lambda e: self._set_goal_form(grade=e.value or "")).props(
                        "outlined dense options-dense").classes("w-full")
                self.ui_elements["gf_grade_box"] = grade_box
            with ui.element("div") as unlock_box:
                field_label("Trophy" if psn else "Achievement")
                self.ui_elements["gf_unlock"] = ui.select({}, with_input=True,
                                                          on_change=lambda e: self._set_goal_form(unlock=e.value or "")).props(
                    "outlined dense options-dense use-input input-debounce=0").classes("w-full")
            self.ui_elements["gf_unlock_box"] = unlock_box
            self.ui_elements["gf_hint"] = ui.label("").classes("gtr-sub").style("white-space:normal")
            with ui.row().classes("justify-end w-full"):
                self.ui_elements["gf_add"] = primary_button("Add goal", self._add_goal, icon="add")
        self._paint_goal_form()

    def _set_goal_form(self, **changes: Any) -> None:
        kind_changed = "kind" in changes and changes["kind"] != self._goal_form.get("kind")
        self._goal_form.update(changes)
        if kind_changed:
            defaults = {goal_kinds.MANUAL: 1, goal_kinds.COUNT: 10, goal_kinds.PERCENT: 50}
            self._goal_form["target"] = defaults.get(self._goal_form["kind"], 1)
            el = self.ui_elements.get("gf_target")
            if el is not None:
                el.value = self._goal_form["target"]
            if self._goal_form["kind"] == goal_kinds.UNLOCK and not self._achievements:
                layout_schedule(0.01, self._load_achievements, once=True)
        self._paint_goal_form()

    def _paint_goal_form(self) -> None:
        g = self._drawer_game
        if g is None or "gf_kind" not in self.ui_elements:
            return
        kind = self._goal_form.get("kind")
        psn = g.platform == PSN
        self.ui_elements["gf_target_box"].set_visibility(kind in _TARGET_LABELS)
        self.ui_elements["gf_target_label"].set_content(esc(_TARGET_LABELS.get(kind, "")))
        self.ui_elements["gf_grade_box"].set_visibility(kind == goal_kinds.COUNT and psn)
        self.ui_elements["gf_unlock_box"].set_visibility(kind == goal_kinds.UNLOCK)
        self.ui_elements["gf_name"].props(f'placeholder="{esc(self._default_goal_name())}"')
        hints = {
            goal_kinds.MANUAL: "You count this one yourself with the + and − buttons (or a connector). A count of 1 is a simple checkbox.",
            goal_kinds.COMPLETE: "Done when you earn the platinum." if psn else "Done when every achievement is unlocked.",
            goal_kinds.COUNT: "Counts trophies earned in this game, of one grade if you pick it." if psn
            else "Counts achievements unlocked in this game.",
            goal_kinds.PERCENT: "Done when the game's completion reaches this percent.",
            goal_kinds.UNLOCK: "Done when this one unlocks. The name is optional: the trophy's name is used.",
        }
        self.ui_elements["gf_hint"].set_text(hints.get(kind, ""))
        ready = kind != goal_kinds.UNLOCK or bool(self._goal_form.get("unlock"))
        add = self.ui_elements["gf_add"]
        add.props(remove="disable") if ready else add.props("disable")

    def _default_goal_name(self) -> str:
        g = self._drawer_game
        f = self._goal_form
        if f.get("kind") == goal_kinds.MANUAL:
            return "Get all endings"
        picked = next((a for a in self._achievements if a["id"] == f.get("unlock")), None)
        probe = goal_kinds.GameGoal(platform=g.platform if g else "", kind=f.get("kind") or goal_kinds.MANUAL,
                                    target=f.get("target") or 1, grade=f.get("grade") or "",
                                    unlock_name=picked["name"] if picked else "")
        return goal_kinds.default_name(goal_kinds.clean_goal(probe))

    async def _load_achievements(self) -> None:
        g = self._drawer_game
        el = self.ui_elements.get("gf_unlock")
        if g is None or el is None:
            return
        el.props('hint="Loading…"')
        items = await run.io_bound(self.service.achievement_list, g.platform, g.game_id)
        if self._drawer_game is not g:
            return
        if items is None:
            el.props(f'hint="Couldn\'t load the list. Check {"PSN" if g.platform == PSN else "Steam"} is connected."')
            return
        self._achievements = items
        el.set_options(achievement_options(items))
        el.props(f'hint="{len(items):,} to pick from"' if items else 'hint="This game has none."')

    async def _add_goal(self) -> None:
        g = self._drawer_game
        if g is None:
            return
        f = dict(self._goal_form)
        values: Dict[str, Any] = {"kind": f["kind"], "name": (f.get("name") or "").strip(), "target": f.get("target") or 1,
                                  "grade": f.get("grade") or ""}
        if f["kind"] == goal_kinds.MANUAL and not values["name"]:
            notify("Type what the goal is, for example Get all endings.", type="info", source="game_tracker", title="Name the goal")
            return
        if f["kind"] == goal_kinds.UNLOCK:
            picked = next((a for a in self._achievements if a["id"] == f.get("unlock")), None)
            if picked is None:
                return
            values.update(unlock_id=picked["id"], unlock_name=picked["name"], unlock_icon=picked.get("icon") or "",
                          unlocked=bool(picked.get("earned")))
        goal, error = await run.io_bound(lambda: self.service.add_goal(g.platform, g.game_id, **values))
        if error or goal is None:
            notify(error or "That game is no longer in the tracker.", type="warning", source="game_tracker", title="Couldn't add the goal")
            return
        for key in ("gf_name",):
            el = self.ui_elements.get(key)
            if el is not None:
                el.value = ""
        self._goal_form["name"] = ""
        await self._reload_drawer_goals()

    def _edit_goal_dialog(self, goal: GameGoal) -> None:
        values = {"name": goal.name, "target": goal.target, "current": goal.current}
        counted = goal.kind in (goal_kinds.MANUAL, goal_kinds.COUNT, goal_kinds.PERCENT)
        with self._dialog_parent(), ui.dialog() as dialog, ui.card().classes("itg-dialog"):
            ui.html(f'{icon_html("flag")}Edit goal', sanitize=False).classes("itg-dialog-title")
            with ui.element("div").classes("w-full"):
                field_label("Goal name")
                ui.input(value=goal.name, placeholder=goal_kinds.default_name(goal),
                         on_change=lambda e: values.update(name=e.value or "")).props("outlined dense").classes("w-full")
            if counted:
                with fields():
                    with ui.element("div"):
                        field_label("Percent" if goal.kind == goal_kinds.PERCENT else "Target")
                        ui.number(value=goal.target, min=1, step=1, format="%d",
                                  on_change=lambda e: values.update(target=e.value)).props("outlined dense").classes("w-full")
                    if goal.kind == goal_kinds.MANUAL:
                        with ui.element("div"):
                            field_label("Done so far")
                            ui.number(value=goal.current, min=0, step=1, format="%d",
                                      on_change=lambda e: values.update(current=e.value)).props("outlined dense").classes("w-full")
            with ui.element("div").classes("itg-actions-end"):
                outline_button("Cancel", dialog.close)

                async def _save() -> None:
                    dialog.close()
                    _goal, error = await run.io_bound(lambda: self.service.update_goal(goal.id, **values))
                    if error:
                        notify(error, type="warning", source="game_tracker", title="Couldn't save the goal")
                    await self._reload_drawer_goals()

                primary_button("Save", _save, icon="check")
        dialog.open()

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

# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Games Tracker library as an Excel workbook (.xlsx): Games, Summary and Unlock history sheets.

Numbers stay numbers (completion is a real percentage, dates are real dates) so the
sheet sorts, filters and charts properly in Excel, Numbers, LibreOffice or Google Sheets.
"""

from __future__ import annotations

import io
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import EVENT_KINDS, GRADES, PLATFORM_LABELS, PSN, STEAM, GameEvent, TrackedGame
from .store import EVENT_LOG_MAX, GameTrackerStore

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PLATFORM_CHOICES = ("all", PSN, STEAM)
SHEETS = ("Games", "Summary", "Unlock history")
_KIND_LABELS = {"unlock": "Unlock", "completed": "Completed", "started": "Started", "milestone": "Milestone"}
_DATE_FORMAT = "yyyy-mm-dd hh:mm"

GAME_COLUMNS = (
    # (header, width)
    ("Game", 38), ("Platform", 12), ("Console", 10), ("Completion", 12), ("Earned", 9), ("Total", 9),
    ("Platinum", 9), ("Gold", 7), ("Silver", 7), ("Bronze", 8), ("Completed", 11), ("Playtime (hours)", 15),
    ("Last played", 17), ("First seen", 17), ("Hidden", 8), ("ID", 16), ("NP Title ID", 15), ("Name on platform", 38),
)
HISTORY_COLUMNS = (
    ("Date", 17), ("Game", 38), ("Platform", 12), ("Event", 11), ("Trophy / achievement", 34), ("Grade", 12),
    ("Rarity (% of players)", 19), ("Milestone (%)", 13), ("Completion at the time", 21),
)


@dataclass
class SpreadsheetOptions:
    platform: str = "all"
    include_hidden: bool = False
    include_history: bool = True

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "SpreadsheetOptions":
        raw = raw if isinstance(raw, dict) else {}
        o = cls()
        platform = str(raw.get("platform") or "all").lower()
        o.platform = platform if platform in PLATFORM_CHOICES else "all"
        if "include_hidden" in raw:
            o.include_hidden = bool(raw["include_hidden"])
        if "include_history" in raw:
            o.include_history = bool(raw["include_history"])
        return o

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def select_games(games: List[TrackedGame], options: SpreadsheetOptions) -> List[TrackedGame]:
    """The games a sheet lists, most recently played first."""
    out = [g for g in games if (options.platform == "all" or g.platform == options.platform)
           and (options.include_hidden or not g.hidden)]
    out.sort(key=lambda g: (-(g.last_played or 0), g.display_title.casefold()))
    return out


def _when(ts: float) -> Optional[datetime]:
    return datetime.fromtimestamp(float(ts)) if ts else None


def game_row(g: TrackedGame, include_hidden: bool) -> List[Any]:
    psn = g.platform == PSN
    grades = {k: int((g.earned or {}).get(k, 0) or 0) for k in GRADES} if psn else {}
    row = [
        g.display_title,
        PLATFORM_LABELS.get(g.platform, g.platform),
        g.platform_label or ("Steam" if not psn else ""),
        (g.progress or 0) / 100,
        g.earned_total,
        g.defined_total,
        grades.get("platinum") if psn else None,
        grades.get("gold") if psn else None,
        grades.get("silver") if psn else None,
        grades.get("bronze") if psn else None,
        "Yes" if g.is_complete else "No",
        None if psn else round((g.playtime_min or 0) / 60, 1),
        _when(g.last_played),
        _when(g.first_seen),
        "Yes" if g.hidden else "No",
        g.game_id,
        g.np_title_id or None,
        g.title,
    ]
    if not include_hidden:
        del row[14]
    return row


def history_row(e: GameEvent) -> List[Any]:
    return [
        _when(e.ts),
        e.game_title,
        PLATFORM_LABELS.get(e.platform, e.platform),
        _KIND_LABELS.get(e.kind, e.kind),
        e.unlock_name or None,
        (e.unlock_grade or "").capitalize() or None,
        e.unlock_rarity,
        e.milestone,
        (e.progress or 0) / 100,
    ]


def summary_rows(games: List[TrackedGame], options: SpreadsheetOptions, events: int) -> List[List[Any]]:
    from .service import summarize

    s = summarize([g for g in games if not g.hidden])
    by_grade = s["trophies_by_grade"]
    platform = {"all": "PlayStation and Steam", PSN: "PlayStation", STEAM: "Steam"}[options.platform]
    rows: List[List[Any]] = [
        ["Exported", datetime.now()],
        ["Platforms", platform],
        ["Games", s["games"]],
    ]
    if options.platform != STEAM:
        rows += [["PlayStation games", s["psn_games"]], ["Platinums", s["platinums"]], ["Trophies", s["trophies"]]]
        rows += [[f"  {g.capitalize()}", by_grade[g]] for g in ("platinum", "gold", "silver", "bronze")]
    if options.platform != PSN:
        rows += [["Steam games", s["steam_games"]], ["Perfect games", s["perfect"]], ["Achievements", s["achievements"]]]
    rows += [["Completed games", s["completed"]], ["Average completion (started games)", s["avg_completion"] / 100]]
    if options.include_hidden:
        rows.append(["Hidden games (listed, not counted here)", sum(1 for g in games if g.hidden)])
    if options.include_history:
        rows.append(["Changes since the first import", events])
    return rows


def select_events(store: GameTrackerStore, options: SpreadsheetOptions) -> List[GameEvent]:
    """History rows: the chosen platforms, newest first, leaving out hidden games unless they're included."""
    if not options.include_history:
        return []
    platform = None if options.platform == "all" else options.platform
    events = store.recent_events(EVENT_LOG_MAX, platform=platform, kinds=EVENT_KINDS)
    if events and not options.include_hidden:
        hidden = {(g.platform, g.game_id) for g in store.list_games() if g.hidden}
        events = [e for e in events if (e.platform, e.game_id) not in hidden]
    return events


def build_workbook(store: GameTrackerStore, options: SpreadsheetOptions):
    """An openpyxl Workbook, or None when there are no games to list."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    games = select_games(store.list_games(), options)
    if not games:
        return None
    events = select_events(store, options)

    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="3B2A6B")

    def table(ws, columns, rows, formats: Dict[str, str]) -> None:
        ws.append([c for c, _w in columns])
        for i, (_c, width) in enumerate(columns, 1):
            ws.column_dimensions[get_column_letter(i)].width = width
            cell = ws.cell(row=1, column=i)
            cell.font, cell.fill = head_font, head_fill
            cell.alignment = Alignment(vertical="center")
        for r in rows:
            ws.append(r)
        headers = [c for c, _w in columns]
        for header, fmt in formats.items():
            if header not in headers:
                continue
            letter = get_column_letter(headers.index(header) + 1)
            for cell in ws[letter][1:]:
                cell.number_format = fmt
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{max(1, ws.max_row)}"

    wb = Workbook()
    ws = wb.active
    ws.title = SHEETS[0]
    columns = [c for c in GAME_COLUMNS if options.include_hidden or c[0] != "Hidden"]
    table(ws, columns, [game_row(g, options.include_hidden) for g in games],
          {"Completion": "0%", "Playtime (hours)": "0.0", "Last played": _DATE_FORMAT, "First seen": _DATE_FORMAT})

    summary = wb.create_sheet(SHEETS[1])
    summary.column_dimensions["A"].width = 38
    summary.column_dimensions["B"].width = 22
    for label, value in summary_rows(games, options, len(events)):
        summary.append([label, value])
        cell = summary.cell(row=summary.max_row, column=2)
        if isinstance(value, datetime):
            cell.number_format = _DATE_FORMAT
        elif label.startswith("Average"):
            cell.number_format = "0%"
        cell.alignment = Alignment(horizontal="left")
    for row in summary.iter_rows(min_col=1, max_col=1):
        row[0].font = Font(bold=not str(row[0].value).startswith("  "))

    if options.include_history:
        hist = wb.create_sheet(SHEETS[2])
        if events:
            table(hist, HISTORY_COLUMNS, [history_row(e) for e in events],
                  {"Date": _DATE_FORMAT, "Rarity (% of players)": "0.0", "Completion at the time": "0%"})
        else:
            hist.append(["No changes since the first import yet. Unlocks, completions, new games and milestones show up here."])
            hist.column_dimensions["A"].width = 100
    return wb


def workbook_bytes(store: GameTrackerStore, options: SpreadsheetOptions) -> Optional[bytes]:
    wb = build_workbook(store, options)
    if wb is None:
        return None
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def export_filename(options: SpreadsheetOptions, when: Optional[datetime] = None) -> str:
    scope = "library" if options.platform == "all" else options.platform
    return f"games_{scope}_{(when or datetime.now()):%Y%m%d}.xlsx"


def save_spreadsheet(store: GameTrackerStore, options: SpreadsheetOptions) -> Optional[str]:
    """Write data/exports/games_<library|psn|steam>_<date>.xlsx; None when there are no games."""
    from ..path_utils import get_data_path

    data = workbook_bytes(store, options)
    if data is None:
        return None
    folder = get_data_path("exports")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, export_filename(options))
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return path


def preview_rows(store: GameTrackerStore, options: SpreadsheetOptions, limit: int = 12) -> Dict[str, Any]:
    """What the dialog shows: the Games sheet's first rows as text, plus counts."""
    games = select_games(store.list_games(), options)
    rows = []
    for g in games[:limit]:
        r = game_row(g, False)
        rows.append([r[0], r[1], f"{round(r[3] * 100)}%", f"{r[4]} / {r[5]}", r[10],
                     r[12].strftime("%Y-%m-%d") if r[12] else ""])
    return {"games": len(games), "events": len(select_events(store, options)), "rows": rows,
            "headers": ["Game", "Platform", "Completion", "Earned", "Completed", "Last played"],
            "sheets": [s for s in SHEETS if options.include_history or s != "Unlock history"]}

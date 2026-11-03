# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Games Tracker spreadsheet export: sheets, columns, real numbers and dates, filters, file naming."""

import io
import time
from datetime import datetime

import pytest
from openpyxl import load_workbook

from modules.game_tracker import spreadsheet as sp
from modules.game_tracker.models import PSN, STEAM, GameEvent, TrackedGame
from modules.game_tracker.store import GameTrackerStore

NOW = time.time()


@pytest.fixture
def store(tmp_path):
    s = GameTrackerStore(db_path=str(tmp_path / "gt.db"))
    s.upsert_games([
        TrackedGame(PSN, "NPWR00001_00", title="ELDEN RING", platform_label="PS5", np_title_id="PPSA04609_00",
                    earned={"bronze": 22, "silver": 9, "gold": 3, "platinum": 1}, defined={"bronze": 28, "silver": 9, "gold": 4, "platinum": 1},
                    progress=100, last_played=NOW - 100, first_seen=NOW - 9000, extra={"presence_name": "Elden Ring"}),
        TrackedGame(STEAM, "1086940", title="Baldur's Gate 3", platform_label="Steam", unlocked_ids=["a"] * 19,
                    total_achievements=54, progress=35, playtime_min=142 * 60 + 30, last_played=NOW),
        TrackedGame(STEAM, "480", title="Spacewar", hidden=True, last_played=NOW + 10),
    ])
    s.record_events([
        GameEvent("unlock", PSN, "NPWR00001_00", "Elden Ring", unlock_name="Elden Lord", unlock_grade="gold", unlock_rarity=12.5,
                  progress=99, ts=NOW - 50),
        GameEvent("milestone", STEAM, "1086940", "Baldur's Gate 3", milestone=25, progress=25, ts=NOW - 40),
        GameEvent("unlock", STEAM, "480", "Spacewar", unlock_name="Hidden one", unlock_grade="achievement", ts=NOW - 30),
    ])
    return s


def workbook(store, **opts):
    return load_workbook(io.BytesIO(sp.workbook_bytes(store, sp.SpreadsheetOptions.from_dict(opts))))


def rows(ws):
    return list(ws.iter_rows(values_only=True))


def test_options_from_dict_cleans_junk():
    o = sp.SpreadsheetOptions.from_dict({"platform": "XBOX", "include_hidden": 1, "nope": 2})
    assert (o.platform, o.include_hidden, o.include_history) == ("all", True, True)
    assert sp.SpreadsheetOptions.from_dict(None) == sp.SpreadsheetOptions()
    assert sp.SpreadsheetOptions.from_dict({"platform": "Steam", "include_history": False}).to_dict() == {
        "platform": "steam", "include_hidden": False, "include_history": False}


def test_games_sheet_has_real_values(store):
    wb = workbook(store)
    assert wb.sheetnames == ["Games", "Summary", "Unlock history"]
    games = wb["Games"]
    header, steam, psn = rows(games)
    assert header[:6] == ("Game", "Platform", "Console", "Completion", "Earned", "Total") and "Hidden" not in header
    assert psn[:12] == ("Elden Ring", "PlayStation", "PS5", 1, 35, 42, 1, 3, 9, 22, "Yes", None)
    assert steam[:12] == ("Baldur's Gate 3", "Steam", "Steam", 0.35, 19, 54, None, None, None, None, "No", 142.5)
    assert isinstance(psn[header.index("Last played")], datetime)
    assert psn[header.index("ID")] == "NPWR00001_00" and psn[header.index("NP Title ID")] == "PPSA04609_00"
    assert psn[header.index("Name on platform")] == "ELDEN RING"
    assert games["D2"].number_format == "0%" and games["M2"].number_format == "yyyy-mm-dd hh:mm"
    assert games.freeze_panes == "A2" and games.auto_filter.ref == "A1:Q3"
    assert games["A1"].font.bold


def test_hidden_games_and_platform_filter(store):
    header, *body = rows(workbook(store, include_hidden=True)["Games"])
    assert "Hidden" in header and [r[0] for r in body] == ["Spacewar", "Baldur's Gate 3", "Elden Ring"]
    assert body[0][header.index("Hidden")] == "Yes"
    only_psn = workbook(store, platform="psn")
    assert [r[0] for r in rows(only_psn["Games"])[1:]] == ["Elden Ring"]
    assert [r[2] for r in rows(only_psn["Unlock history"])[1:]] == ["PlayStation"]


def test_history_sheet(store):
    header, *body = rows(workbook(store)["Unlock history"])
    assert header[:4] == ("Date", "Game", "Platform", "Event")
    assert [(r[1], r[3], r[4], r[5], r[6], r[7]) for r in body] == [
        ("Baldur's Gate 3", "Milestone", None, None, None, 25),
        ("Elden Ring", "Unlock", "Elden Lord", "Gold", 12.5, None),
    ]  # the hidden game's history is left out too
    assert len(rows(workbook(store, include_hidden=True)["Unlock history"])) == 4
    assert "Unlock history" not in workbook(store, include_history=False).sheetnames


def test_history_sheet_says_when_empty(tmp_path):
    s = GameTrackerStore(db_path=str(tmp_path / "e.db"))
    s.upsert_games([TrackedGame(PSN, "N", title="A")])
    assert rows(workbook(s)["Unlock history"])[0][0].startswith("No changes since the first import")


def test_summary_sheet(store):
    summary = dict(rows(workbook(store, include_hidden=True)["Summary"]))
    assert isinstance(summary["Exported"], datetime) and summary["Platforms"] == "PlayStation and Steam"
    assert summary["Games"] == 2 and summary["Platinums"] == 1 and summary["Trophies"] == 35 and summary["  Gold"] == 3
    assert summary["Perfect games"] == 0 and summary["Achievements"] == 19
    assert summary["Average completion (started games)"] == pytest.approx(0.68)
    assert summary["Hidden games (listed, not counted here)"] == 1 and summary["Changes since the first import"] == 3
    steam_only = dict(rows(workbook(store, platform="steam")["Summary"]))
    assert "Platinums" not in steam_only and steam_only["Platforms"] == "Steam"


def test_nothing_to_list(store, tmp_path):
    assert sp.workbook_bytes(store, sp.SpreadsheetOptions(platform="steam", include_hidden=False)) is not None
    empty = GameTrackerStore(db_path=str(tmp_path / "none.db"))
    assert sp.build_workbook(empty, sp.SpreadsheetOptions()) is None
    assert sp.save_spreadsheet(empty, sp.SpreadsheetOptions()) is None


def test_save_and_name(store, tmp_path, monkeypatch):
    monkeypatch.setenv("MYCELIAN_DATA_DIR", str(tmp_path))
    path = sp.save_spreadsheet(store, sp.SpreadsheetOptions(platform="psn"))
    assert path.startswith(str(tmp_path / "exports")) and path.endswith(f"games_psn_{datetime.now():%Y%m%d}.xlsx")
    assert load_workbook(path).sheetnames[0] == "Games"
    assert sp.export_filename(sp.SpreadsheetOptions(), datetime(2026, 1, 2)) == "games_library_20260102.xlsx"


def test_preview_rows(store):
    data = sp.preview_rows(store, sp.SpreadsheetOptions(), limit=1)
    assert sp.preview_rows(store, sp.SpreadsheetOptions(include_hidden=True))["events"] == 3
    assert data["games"] == 2 and data["events"] == 2 and data["sheets"] == ["Games", "Summary", "Unlock history"]
    assert data["rows"] == [["Baldur's Gate 3", "Steam", "35%", "19 / 54", "No", datetime.fromtimestamp(NOW).strftime("%Y-%m-%d")]]
    assert sp.preview_rows(store, sp.SpreadsheetOptions(include_history=False))["sheets"] == ["Games", "Summary"]

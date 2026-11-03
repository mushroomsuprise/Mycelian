# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/game_tracker/export.py: the library recap / game spotlight image."""

from __future__ import annotations

import os
import time
from datetime import datetime

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from PIL import Image

from modules.game_tracker import covers
from modules.game_tracker import export as ex
from modules.game_tracker.models import GameEvent, TrackedGame
from modules.game_tracker.store import GameTrackerStore
from modules.theme_manager import ThemeColors

BG = (18, 18, 24)


def _theme() -> ThemeColors:
    return ThemeColors(
        name="t", primary="rgb(115, 0, 255)", bg_base="rgb(18, 18, 24)", bg_elevated="rgb(25, 25, 35)",
        bg_surface="rgb(35, 35, 45)", text_primary="rgb(240, 240, 255)", text_secondary="rgba(240, 240, 255, 0.8)",
        text_muted="rgba(200, 200, 220, 0.6)", success="rgb(34, 197, 94)", error="rgb(239, 68, 68)",
    )


@pytest.fixture
def store(tmp_path):
    st = GameTrackerStore(db_path=str(tmp_path / "gt.db"))
    now = time.time()
    st.upsert_games(
        [
            TrackedGame("psn", f"NPWR0000{i}_00", title=f"Game {i}", platform_label="PS5",
                        earned={"bronze": 5 + i, "platinum": 1 if i == 0 else 0}, defined={"bronze": 20, "platinum": 1},
                        progress=30 + i * 10, last_played=now - i * 60,
                        extra={"trophy_groups": [{"group_name": "Base game", "defined_trophies": {"bronze": 20}}]})
            for i in range(5)
        ]
        + [
            TrackedGame("steam", str(10 + i), title=f"Steam {i}", platform_label="Steam",
                        unlocked_ids=[f"a{j}" for j in range(i)], total_achievements=5, progress=i * 20,
                        playtime_min=90 * i, last_played=now - 1000 - i)
            for i in range(5)
        ]
        + [TrackedGame("steam", "999", title="Hidden one", hidden=True, last_played=now + 10)]
    )
    st.record_events([
        GameEvent("unlock", "psn", "NPWR00000_00", "Game 0", unlock_name="Gold one", unlock_grade="gold", ts=now - 30),
        GameEvent("unlock", "steam", "11", "Steam 1", unlock_name="Ach", unlock_grade="achievement", unlock_rarity=2.5, ts=now - 40),
    ])
    return st


def _render(store, **opts):
    return ex.render_games_image(ex.ExportOptions.from_dict(opts), store=store, theme=_theme(), channel_name="Tester")


@pytest.mark.parametrize("template", ["library", "game"])
@pytest.mark.parametrize("layout", list(ex.LAYOUTS))
def test_every_template_and_layout_renders_at_its_size(store, template, layout):
    img = _render(store, template=template, layout=layout, period="7d", count=12, cover_background=True)
    assert img is not None
    assert img.size == ex.LAYOUTS[layout]


@pytest.mark.parametrize("platform", ["psn", "steam"])
def test_platform_filter_and_spotlight_per_platform(store, platform):
    key = "psn:NPWR00001_00" if platform == "psn" else "steam:11"
    assert _render(store, platform=platform).size == (1920, 1080)
    assert _render(store, template="game", game_key=key, layout="portrait").size == (1080, 1350)


def test_background_follows_theme(store):
    img = _render(store, layout="wide")
    # Bottom-middle under the footer line, away from both glows.
    px = img.getpixel((960, 1075))
    assert all(abs(a - b) <= 12 for a, b in zip(px, BG))


def test_hidden_sections_and_tiles_change_the_image(store):
    full = _render(store).tobytes()
    assert _render(store, hidden_sections=["grid"]).tobytes() != full
    assert _render(store, hidden_tiles=["games", "avg"]).tobytes() != full
    every = [k for k, _ in ex.LIBRARY_SECTIONS]
    bare = _render(store, hidden_sections=every)
    assert bare is not None and bare.tobytes() != full


def test_game_sections_can_be_hidden(store):
    full = _render(store, template="game", game_key="psn:NPWR00000_00").tobytes()
    assert _render(store, template="game", game_key="psn:NPWR00000_00", hidden_sections=["breakdown", "groups"]).tobytes() != full


def test_empty_library_draws_nothing(tmp_path):
    empty = GameTrackerStore(db_path=str(tmp_path / "empty.db"))
    assert _render(empty) is None
    empty.upsert_games([TrackedGame("psn", "NPWR00001_00", title="x", hidden=True)])
    assert _render(empty) is None


def test_library_with_nothing_on_the_chosen_platform_draws_nothing(tmp_path):
    only_psn = GameTrackerStore(db_path=str(tmp_path / "psn.db"))
    only_psn.upsert_games([TrackedGame("psn", "NPWR00001_00", title="x")])
    assert _render(only_psn, platform="steam") is None
    assert _render(only_psn, platform="psn") is not None


def test_unknown_game_draws_nothing(store):
    assert _render(store, template="game", game_key="psn:NPWR99999_00") is None
    assert _render(store, template="game", game_key="xbox:1") is None


def test_spotlight_without_key_uses_most_recent_visible_game(store):
    assert _render(store, template="game").size == (1920, 1080)


def test_cached_cover_is_used_and_bad_cover_is_ignored(store, tmp_path, monkeypatch):
    good = tmp_path / "good.jpg"
    Image.new("RGB", (460, 215), (200, 10, 10)).save(good, "JPEG")
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    paths = {("psn", "NPWR00000_00"): str(good), ("psn", "NPWR00001_00"): str(bad)}
    monkeypatch.setattr(covers, "cached_cover", lambda p, g: paths.get((p, g)))
    img = _render(store, template="game", game_key="psn:NPWR00000_00", show_badges=False)
    r, g, b = img.getpixel((300, 500))  # inside the big cover
    assert r > 150 and g < 60 and b < 60
    assert _render(store, template="game", game_key="psn:NPWR00001_00") is not None
    assert _render(store, cover_background=True) is not None


def test_options_from_dict_cleans_junk():
    o = ex.ExportOptions.from_dict({
        "template": "nope", "layout": "tall", "platform": "xbox", "period": "decade", "count": 7,
        "hidden_sections": ["grid", "bogus", 5], "hidden_tiles": ["avg", "nope"], "pick": "?", "title": "  x" * 100,
        "picked": "notalist", "show_pct": 0, "unknown": 1,
    })
    assert (o.template, o.layout, o.platform, o.period, o.count, o.pick) == ("library", "wide", "all", "all", 8, "recent")
    assert o.hidden_sections == ["grid"] and o.hidden_tiles == ["avg"]
    assert len(o.title) <= 80 and o.picked == [] and o.show_pct is False
    assert ex.ExportOptions.from_dict(None) == ex.ExportOptions()
    assert ex.ExportOptions.from_dict(o.to_dict()) == o


def test_picked_games_follow_the_chosen_order(store):
    games = store.list_games()
    o = ex.ExportOptions.from_dict({"pick": "picked", "picked": ["steam:12", "psn:NPWR00003_00", "steam:999"]})
    picked = ex._pick_games([g for g in games if not g.hidden], o)
    assert [g.key for g in picked] == ["steam:12", "psn:NPWR00003_00"]
    done = ex._pick_games(games, ex.ExportOptions.from_dict({"pick": "done"}))
    assert all(g.is_complete for g in done)
    assert len(ex._pick_games(games, ex.ExportOptions.from_dict({"layout": "square", "count": 12}))) <= 8


def test_period_since():
    now = datetime(2026, 6, 15, 12).timestamp()
    assert ex._since("all", now) is None
    assert ex._since("7d", now) == now - 7 * 86400
    assert ex._since("year", now) == datetime(2026, 1, 1).timestamp()


def test_tiles_respect_platform_and_square(store):
    o = ex.ExportOptions.from_dict({"platform": "steam", "period": "year"})
    labels = [t[0] for t in ex._tile_specs(store, [g for g in store.list_games() if g.platform == "steam"], o)]
    assert "Platinums" not in labels and "Trophies" not in labels and "Achievements" in labels
    sq = ex._tile_specs(store, store.list_games(), ex.ExportOptions.from_dict({"layout": "square"}))
    assert len(sq) == 3


def test_save_path_naming(store, tmp_path, monkeypatch):
    monkeypatch.setenv("MYCELIAN_DATA_DIR", str(tmp_path))
    img = Image.new("RGB", (10, 10))
    stamp = datetime.now().strftime("%Y%m%d")
    path = ex.save_export_image(img, ex.ExportOptions())
    assert path == os.path.join(str(tmp_path), "exports", f"games_library_{stamp}.png") and os.path.isfile(path)
    o = ex.ExportOptions.from_dict({"template": "game", "game_key": "psn:NPWR00001_00", "layout": "portrait"})
    assert os.path.basename(ex.save_export_image(img, o)) == f"games_psn_NPWR00001_00_{stamp}_portrait.png"


def test_default_channel_name_falls_back(monkeypatch):
    from modules import twitch

    monkeypatch.setattr(twitch, "get_twitch_connection_status", lambda: {"user_name": "Streamer"})
    assert ex.default_channel_name() == "Streamer"
    monkeypatch.setattr(twitch, "get_twitch_connection_status", lambda: {"user_name": "N/A"})
    assert ex.default_channel_name()  # streamer name or Mycelian


def test_relative_times():
    now = 1_000_000.0
    assert ex._ago(now - 30, now) == "1m ago"
    assert ex._ago(now - 7200, now) == "2h ago"
    assert ex._ago(now - 100000, now) == "Yesterday"
    assert ex._ago(now - 3 * 86400, now) == "3d ago"
    assert ex._ago(now - 14 * 86400, now) == "2w ago"


def test_stack_segments_keep_tiny_grades_visible():
    from modules.game_tracker.export import _STACK_GAP, _STACK_MIN_SEG, _stack_segments

    segs = _stack_segments(0, 400, {"platinum": 1, "gold": 0, "silver": 3, "bronze": 5000})
    assert [g for _a, _b, g in segs] == ["platinum", "silver", "bronze"]
    for a, b, _g in segs:
        assert b >= a and b - a + 1 >= _STACK_MIN_SEG - 0.001
    assert segs[-1][1] == 399 and segs[1][0] - segs[0][1] - 1 == pytest.approx(_STACK_GAP)
    assert _stack_segments(0, 400, {}) == [] and _stack_segments(0, 10, {"gold": 1, "silver": 1, "bronze": 1}) == []


def test_library_renders_with_one_platinum_among_many_bronzes(tmp_path):
    lopsided = GameTrackerStore(db_path=str(tmp_path / "lop.db"))
    lopsided.upsert_games([
        TrackedGame("psn", "NPWR00001_00", title="A", earned={"bronze": 4000, "platinum": 1}, defined={"bronze": 4000, "platinum": 1}, progress=100),
        TrackedGame("psn", "NPWR00002_00", title="B", earned={"bronze": 900}, defined={"bronze": 2000}, progress=40),
    ])
    for layout in ("wide", "portrait"):
        assert _render(lopsided, layout=layout) is not None

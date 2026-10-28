# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""statistics_export: CSS color parsing, the theme palette, font loading, the drawing
canvas, every card block and both highlight image layouts, and PNG export."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from PIL import Image, ImageFont  # noqa: E402

import modules.statistics_export as ex  # noqa: E402

THEME = SimpleNamespace(
    bg_base="rgb(10,10,20)", bg_surface="rgba(255,255,255,0.5)", bg_elevated="#202030",
    text_primary="rgb(240,240,240)", text_secondary="rgba(240,240,240,0.8)", text_muted="rgb(150,150,160)",
    primary="rgb(120,0,255)", success="rgb(0,200,0)", error="rgb(220,0,0)",
)


@pytest.fixture
def pal():
    return ex.Palette.from_theme(THEME)


@pytest.fixture
def cv(pal):
    return ex.Canvas((800, 600), pal)


def colors_in(img, box):
    part = img.crop(box)
    flat = getattr(part, "get_flattened_data", None) or part.getdata
    return set(flat())


# --------------------------------------------------------------------------- colors


@pytest.mark.parametrize("value,expected", [
    ("rgb(1, 2, 3)", (1, 2, 3, 255)),
    ("RGBA(10,20,30,0.5)", (10, 20, 30, 128)),
    ("rgba(10,20,30,0)", (10, 20, 30, 0)),
    ("  #FF8000 ", (255, 128, 0, 255)),
    ("#fff", (128, 128, 128, 255)),
    ("hsl(0, 0%, 0%)", (128, 128, 128, 255)),
    ("", (128, 128, 128, 255)),
    (None, (128, 128, 128, 255)),
    (42, (128, 128, 128, 255)),
])
def test_parse_css_color(value, expected):
    assert ex.parse_css_color(value) == expected


def test_color_helpers():
    assert ex._rgb("rgba(1,2,3,0.1)") == (1, 2, 3)
    assert ex._mix((0, 0, 0), (100, 200, 50), 0.5) == (50, 100, 25)
    assert ex._over((255, 255, 255, 128), (0, 0, 0)) == (128, 128, 128)
    assert ex._over((9, 9, 9, 255), (0, 0, 0)) == (9, 9, 9)


def test_palette_flattens_translucent_theme_colors(pal):
    assert pal.bg == (10, 10, 20)
    assert pal.surface == ex._mix((10, 10, 20), (255, 255, 255), 128 / 255) == (133, 133, 138)  # half-white over the base
    assert pal.raised == (32, 32, 48)
    assert pal.text == (240, 240, 240)
    assert pal.text2 == ex._mix(pal.surface, (240, 240, 240), 204 / 255)
    assert pal.muted == (150, 150, 160)
    assert pal.primary == (120, 0, 255)
    assert pal.hairline == ex._mix(pal.surface, pal.text, 0.10)
    assert pal.track == ex._mix(pal.surface, pal.text, 0.06)
    assert (pal.up, pal.down) == ((0, 200, 0), (220, 0, 0))


def test_layouts():
    assert ex.LAYOUTS == {"wide": (1920, 1080), "portrait": (1080, 1350)}


# --------------------------------------------------------------------------- fonts and numbers


def test_fonts_are_cached(monkeypatch):
    monkeypatch.setattr(ex, "_FONT_CACHE", {})
    a = ex.BOLD(20)
    assert ex.BOLD(20) is a
    assert ex.REGULAR(20) is not a
    assert ex.DISPLAY(30) is ex._FONT_CACHE[("Anton-Regular.ttf", 30)]


def test_fonts_fall_back_through_candidates(monkeypatch):
    monkeypatch.setattr(ex, "_FONT_CACHE", {})
    tried = []
    sentinel = object()

    def truetype(path, size):
        tried.append(Path(path).name)
        if path == "DejaVuSans.ttf":
            return sentinel
        raise OSError("missing")

    monkeypatch.setattr(ex.ImageFont, "truetype", truetype)
    assert ex._font("Nope.ttf", 12) is sentinel
    assert tried == ["Nope.ttf", "Arial.ttf", "arial.ttf", "DejaVuSans.ttf"]


def test_fonts_fall_back_to_the_pillow_default(monkeypatch):
    monkeypatch.setattr(ex, "_FONT_CACHE", {})
    monkeypatch.setattr(ex.ImageFont, "truetype", Mock(side_effect=OSError("none")))
    default = Mock(name="default font")
    monkeypatch.setattr(ex.ImageFont, "load_default", lambda: default)
    assert ex._font("X.ttf", 10) is default


@pytest.mark.parametrize("n,text", [(None, "0"), (1234.6, "1,235"), (0, "0")])
def test_format_number(n, text):
    assert ex._format_number(n) == text


@pytest.mark.parametrize("n,text", [(None, "0"), (99_999, "99,999"), (100_000, "100K"), (250_400, "250K"),
                                    (1_000_000, "1M"), (2_340_000, "2.3M"), (-3_000_000, "-3M")])
def test_compact(n, text):
    assert ex._compact(n) == text


# --------------------------------------------------------------------------- canvas primitives


def test_canvas_starts_filled_with_the_background(cv, pal):
    assert cv.img.size == (800, 600) and (cv.w, cv.h) == (800, 600)
    assert cv.img.getpixel((5, 5)) == pal.bg


def test_fit_truncates_with_an_ellipsis(cv):
    font = ex.REGULAR(20)
    assert cv.fit("short", font, 500) == "short"
    cut = cv.fit("a fairly long streamer name", font, 80)
    assert cut.endswith("…") and cv.text_w(cut, font) <= 80
    assert cv.fit("wide", font, 1) == ""


def test_text_honours_max_width(cv, pal):
    cv.text((10, 10), "W" * 50, ex.BOLD(20), pal.text, max_w=60)
    drawn = [x for x in range(800) if cv.img.getpixel((x, 20)) != pal.bg]
    assert drawn and max(drawn) < 80


def test_tracked_text_is_wider_than_plain(cv, pal):
    font = ex.BOLD(18)
    width = cv.tracked((10, 10), "MVP", font, pal.primary, 3)
    assert width == sum(cv.text_w(c, font) + 3 for c in "MVP")
    assert pal.primary in colors_in(cv.img, (10, 10, 10 + width, 40))


def test_card_and_outline(cv, pal):
    cv.card((10, 10, 110, 110))
    assert cv.img.getpixel((60, 60)) == pal.surface
    cv.card((200, 10, 300, 110), radius=4, fill=(1, 2, 3), outline=(9, 9, 9))
    assert cv.img.getpixel((250, 60)) == (1, 2, 3) and cv.img.getpixel((250, 10)) == (9, 9, 9)


def test_glow_tints_the_area_and_keeps_drawing(cv, pal):
    before = cv.img.getpixel((400, 300))
    cv.glow((400, 300), 100, pal.primary, 120)
    assert cv.img.getpixel((400, 300)) != before
    cv.card((0, 0, 20, 20), fill=(5, 5, 5))  # the draw handle follows the new image
    assert cv.img.getpixel((10, 10)) == (5, 5, 5)


def test_delta_markers(cv, pal):
    font = ex.BOLD(20)
    cv.delta((10, 10), None, font)
    assert colors_in(cv.img, (0, 0, 800, 600)) == {pal.bg}
    cv.delta((10, 10), 0.2, font)
    assert pal.muted in colors_in(cv.img, (10, 10, 120, 40))
    cv.delta((10, 100), 25.0, font)
    assert pal.up in colors_in(cv.img, (10, 100, 30, 130))
    cv.delta((10, 200), -40.0, font, " vs last")
    assert pal.down in colors_in(cv.img, (10, 200, 30, 230))
    assert pal.muted in colors_in(cv.img, (60, 200, 300, 230))  # the label
    cv.delta((10, 300), 5000.0, font)  # 999%+
    assert pal.up in colors_in(cv.img, (40, 300, 140, 330))


def test_avatar_uses_the_initial(cv, pal):
    cv.avatar((100, 100), 40, "  zed")
    assert pal.primary in colors_in(cv.img, (55, 95, 65, 105))  # the ring
    cv.avatar((300, 100), 40, "   ")  # "?" for blank names
    assert pal.text in colors_in(cv.img, (280, 80, 320, 120))


# --------------------------------------------------------------------------- blocks


def test_header_layouts(cv, pal):
    start, end = datetime(2026, 10, 1), datetime(2026, 10, 1, 23)
    ex._header(cv, (20, 20, 780, 200), "Streamer", start, end, True)
    assert pal.primary in colors_in(cv.img, (20, 20, 300, 45))
    wide = ex.Canvas((1200, 300), pal)
    ex._header(wide, (20, 20, 1180, 200), "Streamer " * 20, datetime(2026, 10, 1), datetime(2026, 10, 30), False)
    assert pal.text in colors_in(wide.img, (900, 60, 1180, 100))  # right-aligned date


@pytest.mark.parametrize("pct", [None, 0.1, 12.0, -50.0, 2000.0])
def test_kpi_tiles(cv, pal, pct):
    ex._kpi(cv, (20, 20, 320, 180), "Chat messages with a long label", 12_345, pct, 60)
    assert cv.img.getpixel((60, 44)) == pal.primary  # accent bar
    assert pal.text in colors_in(cv.img, (20, 100, 320, 180))
    tile = colors_in(cv.img, (200, 40, 320, 80))
    if pct is None or abs(pct) < 0.5:
        assert pal.up not in tile and pal.down not in tile
    else:
        assert (pal.up if pct > 0 else pal.down) in tile


def test_activity_chart_without_data(cv, pal):
    ex._activity_chart(cv, (20, 20, 780, 400), [], [], "day")
    assert pal.muted in colors_in(cv.img, (200, 190, 600, 230))
    other = ex.Canvas((800, 600), pal)
    ex._activity_chart(other, (20, 20, 780, 400), ["a", "b"], [0, 0], "unknown-bucket")
    assert pal.primary not in colors_in(other.img, (20, 20, 780, 400))


@pytest.mark.parametrize("bucket", ["hour", "day", "week"])
def test_activity_chart_highlights_the_peak(cv, pal, bucket):
    labels = [f"L{i}" for i in range(9)]
    values = [1, 0, 3, 9, 2, 0, 1, 4, 5]
    ex._activity_chart(cv, (20, 20, 780, 500), labels, values, bucket)
    slot = (780 - 28 - 48) / 9
    peak_x = int(48 + slot * 3.5)
    assert cv.img.getpixel((peak_x, 440)) == pal.primary
    other_x = int(48 + slot * 0.5)
    assert cv.img.getpixel((other_x, 440)) == ex._mix(pal.surface, pal.primary, 0.62)
    assert pal.raised in colors_in(cv.img, (20, 90, 780, 120))  # the peak pill


def test_activity_chart_with_one_bucket(cv, pal):
    ex._activity_chart(cv, (20, 20, 780, 500), ["Only"], [3], "day")
    assert pal.primary in colors_in(cv.img, (20, 130, 780, 450))


FULL = {
    "most_active_user": {"username": "Alice", "event_count": 222},
    "biggest_bit_donation": {"username": "Bob", "amount": 1000},
    "top_gift_subs": [{"username": "Cara", "total": 10}],
    "biggest_watch_streak": {"username": "Dan", "amount": 33},
    "top_chatter": {"username": "Eve", "event_count": 150},
}


def test_spotlight_with_mvp_and_three_rows(cv, pal):
    ex._spotlight(cv, (20, 20, 520, 420), FULL)
    assert pal.primary in colors_in(cv.img, (45, 45, 120, 70))
    assert pal.hairline in colors_in(cv.img, (48, 178, 500, 182))


def test_spotlight_adds_top_chatter_when_room(cv, pal):
    h = {"top_chatter": {"username": "Eve", "event_count": 150}, "top_gift_subs": [None]}
    ex._spotlight(cv, (20, 20, 520, 420), h)
    assert pal.muted in colors_in(cv.img, (40, 90, 400, 130))  # "No standout viewer yet"
    assert pal.hairline in colors_in(cv.img, (48, 178, 500, 182))


def test_spotlight_stops_when_rows_do_not_fit(cv, pal):
    ex._spotlight(cv, (20, 20, 520, 215), FULL)
    assert pal.hairline not in colors_in(cv.img, (48, 230, 500, 600))


def test_spotlight_without_anything(cv, pal):
    ex._spotlight(cv, (20, 20, 520, 420), {"top_gift_subs": []})
    assert pal.hairline not in colors_in(cv.img, (20, 170, 520, 420))


def test_mvp_strip(cv, pal):
    ex._mvp_strip(cv, (20, 20, 780, 112), FULL)
    assert pal.primary in colors_in(cv.img, (40, 30, 120, 110))
    empty = ex.Canvas((800, 200), pal)
    ex._mvp_strip(empty, (20, 20, 780, 112), {})
    assert pal.primary not in colors_in(empty.img, (0, 0, 800, 200))
    assert pal.muted in colors_in(empty.img, (40, 50, 400, 80))


def test_leaderboard(cv, pal):
    entries = [{"username": "A", "count": 10}, {"username": "B", "count": 5}, {"username": None, "count": 2},
               {"username": "Zero", "count": 0}]
    ex._leaderboard(cv, (20, 20, 400, 400), "Top chatters", entries, "count")
    assert cv.img.getpixel((80, 20 + 66 + 28)) == pal.primary  # first bar
    second_bar = cv.img.getpixel((80, 20 + 66 + 44 + 28))
    assert second_bar == ex._mix(pal.surface, pal.primary, 0.55)


def test_leaderboard_empty_and_cramped(cv, pal):
    ex._leaderboard(cv, (20, 20, 400, 400), "Nobody", [{"username": "x", "total": 0}], "total")
    assert pal.muted in colors_in(cv.img, (40, 85, 200, 115))
    tight = ex.Canvas((500, 300), pal)
    ex._leaderboard(tight, (20, 20, 400, 200), "Tight", [{"username": "A", "total": 3}], "total", rows=5)
    assert pal.track not in colors_in(tight.img, (0, 0, 500, 300))  # rows too short for bars


def test_footer_lists_totals_until_space_runs_out(cv, pal):
    h = {"total_follows": 12, "total_raids": 0, "total_channel_points": 50_000, "total_donations": 3,
         "total_giveaway_entries": 400}
    ex._footer(cv, (20, 560, 780, 590), h, "lifetime")
    assert pal.text in colors_in(cv.img, (20, 560, 80, 590))
    assert pal.muted in colors_in(cv.img, (600, 560, 780, 590))
    narrow = ex.Canvas((300, 100), pal)
    ex._footer(narrow, (10, 50, 290, 80), h, "event_log")  # nothing fits beside the note
    assert pal.text not in colors_in(narrow.img, (0, 0, 300, 100))


def test_spec_builders():
    h = {"unique_users": 3, "total_chat_messages": 9, "total_bits": None, "total_new_subs": 2, "total_resubs": 1,
         "total_gift_subs": 4, "top_bits": [{"username": "a", "total": 1}]}
    specs = ex._kpi_specs(h, {"chat": 5.0})
    assert specs == [("Active viewers", 3.0, None), ("Chat messages", 9.0, 5.0), ("Bits cheered", 0.0, None),
                     ("Subs", 3.0, None), ("Gifted subs", 4.0, None)]
    assert ex._kpi_specs({"total_subs": 7}, {})[3] == ("Subs", 7.0, None)
    boards = ex._leader_specs(h)
    assert [b[0] for b in boards] == ["Top chatters", "Top cheerers", "Top gifters", "Channel points"]
    assert boards[1] == ("Top cheerers", [{"username": "a", "total": 1}], "total")
    assert boards[0][1] == []


# --------------------------------------------------------------------------- full images


def _theme(name="dark"):
    from modules.theme_manager import ThemeColors

    return ThemeColors(**json.loads((ROOT / "themes" / f"{name}_theme.json").read_text()))


def test_render_uses_the_active_theme_when_none_given(monkeypatch):
    tm = Mock()
    tm.get_theme.return_value = _theme("light")
    monkeypatch.setattr(ex, "get_theme_manager", lambda: tm)
    img = ex.render_highlights_image({"total_events": 1}, datetime(2026, 1, 1), datetime(2026, 1, 1),
                                     streamer_name="", layout="square")
    assert img.size == (1920, 1080)  # unknown layout: wide
    tm.load_themes_from_directory.assert_called_once()
    assert img.getpixel((415, 300)) == ex._rgb(_theme("light").bg_base)  # gap between KPI tiles


def test_render_portrait_with_lifetime_source():
    img = ex.render_highlights_image(
        {"total_events": 5, "most_active_user": {"username": "Al", "event_count": 5},
         "top_chat_messages": [{"username": "Al", "count": 5}], "total_follows": 2},
        datetime(2026, 1, 1), datetime(2026, 1, 7), theme=_theme(), layout="portrait",
        extra={"activity_labels": ["a", "b"], "activity_values": [1, 2], "deltas": {"viewers": 10.0}},
        source="lifetime",
    )
    assert img.size == (1080, 1350) and img.mode == "RGB"


def test_generate_writes_a_png(tmp_path):
    out = tmp_path / "nested" / "dir" / "out.png"
    assert ex.generate_highlights_image({"total_events": 1}, datetime(2026, 1, 1), datetime(2026, 1, 2), str(out),
                                        theme=_theme(), layout="portrait")
    with Image.open(out) as img:
        assert img.format == "PNG" and img.size == (1080, 1350)


def test_generate_failure_reports_and_returns_false(tmp_path, monkeypatch):
    import modules.notification_engine as ne

    blocker = tmp_path / "file"
    blocker.write_text("x")
    critical = Mock()
    monkeypatch.setattr(ne, "notify_critical", critical)
    monkeypatch.setattr(ex, "render_highlights_image", Mock(return_value=Image.new("RGB", (4, 4))))
    assert ex.generate_highlights_image({}, datetime(2026, 1, 1), datetime(2026, 1, 1), str(blocker / "out.png")) is False
    critical.assert_called_once()
    assert critical.call_args.kwargs["dedupe_key"] == "stats:highlights_export"
    assert critical.call_args.kwargs["actions"] == ne.nav_actions_settings("Statistics")
    critical.side_effect = RuntimeError("toast down")
    assert ex.generate_highlights_image({}, datetime(2026, 1, 1), datetime(2026, 1, 1), str(blocker / "out.png")) is False

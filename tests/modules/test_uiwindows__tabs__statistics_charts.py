# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""ECharts option builders for the Statistics dashboard: theme colors, time columns,
the weekly heatmap and hype-train level columns."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

import modules.uiwindows.tabs.statistics_charts as ch  # noqa: E402


@pytest.fixture
def colors():
    return ch.ChartColors.from_theme(None)


@pytest.mark.parametrize("value,expected", [
    ("rgb(1, 2, 3)", (1, 2, 3)),
    ("rgba(10,20,30,0.5)", (10, 20, 30)),
    ("#ff8000", (255, 128, 0)),
    ("ff8000", (255, 128, 0)),
    ("#fff", (128, 128, 128)),
    ("transparent", (128, 128, 128)),
    ("", (128, 128, 128)),
    (None, (128, 128, 128)),
])
def test_rgb_parsing(value, expected):
    assert ch._rgb(value) == expected


def test_rgb_custom_fallback():
    assert ch._rgb("nonsense", fallback=(1, 1, 1)) == (1, 1, 1)


def test_mix_blends_between_colors():
    assert ch.mix("rgb(0,0,0)", "rgb(200,100,50)", 0) == "rgb(0,0,0)"
    assert ch.mix("rgb(0,0,0)", "rgb(200,100,50)", 1) == "rgb(200,100,50)"
    assert ch.mix("#000000", "#ffffff", 0.5) == "rgb(128,128,128)"


def test_chart_colors_defaults_without_a_theme(colors):
    assert colors.primary == "rgb(115,0,255)"
    assert colors.surface == "rgb(35,35,45)" and colors.base == "rgb(18,18,24)"
    assert colors.text == "rgb(240,240,255)" and colors.text_secondary == colors.text
    assert colors.text_muted == "rgb(160,160,180)"
    assert colors.grid == ch.mix(colors.surface, colors.text, 0.08)
    assert colors.axis == ch.mix(colors.surface, colors.text, 0.22)
    assert colors.ghost == ch.mix(colors.surface, colors.text_muted, 0.55)


def test_chart_colors_from_a_theme_with_gaps():
    theme = SimpleNamespace(primary="rgb(1,2,3)", bg_surface="rgb(10,10,10)", bg_base="", text_primary="rgb(200,200,200)",
                            text_secondary=None, text_muted="rgb(100,100,100)")
    c = ch.ChartColors.from_theme(theme)
    assert c.primary == "rgb(1,2,3)" and c.surface == "rgb(10,10,10)"
    assert c.base == "rgb(18,18,24)"  # empty value falls back
    assert c.text_secondary == "rgb(200,200,200)"  # falls back to the main text color
    assert c.grid == "rgb(25,25,25)"
    with pytest.raises(Exception):
        c.primary = "x"  # frozen


@pytest.mark.parametrize("value,label", [(2_500_000, "2.5M"), (15_000, "15.0K"), (1234, "1,234"), (10, "10"), (5, "5.0"), (0.25, "0.2")])
def test_compact_label(value, label):
    assert ch._compact_label(value) == label


def test_tooltips(colors):
    axis = ch._tooltip(colors)
    assert axis["trigger"] == "axis" and axis["axisPointer"]["lineStyle"]["color"] == colors.axis
    assert axis["backgroundColor"] == colors.base and axis["textStyle"]["color"] == colors.text
    item = ch._tooltip(colors, "item")
    assert item["trigger"] == "item" and "axisPointer" not in item


def test_value_axis(colors):
    axis = ch._value_axis(colors)
    assert axis["type"] == "value" and axis["minInterval"] == 1
    assert axis["splitLine"]["lineStyle"]["color"] == colors.grid
    assert axis["axisLabel"][":formatter"].startswith("v =>")


def test_time_columns_as_bars(colors):
    opts = ch.time_columns(["a", "b"], (1, 2), colors, series_name="Chat")
    assert opts["animation"] is ch.ANIMATE
    assert opts["xAxis"]["data"] == ["a", "b"]
    (series,) = opts["series"]
    assert series["type"] == "bar" and series["name"] == "Chat" and series["data"] == [1, 2]
    assert series["barMaxWidth"] == 24 and series["itemStyle"]["borderRadius"] == [4, 4, 0, 0]
    assert "markLine" not in series
    assert opts["tooltip"]["trigger"] == "axis"


def test_time_columns_as_line_with_previous_average(colors):
    labels = [str(i) for i in range(40)]
    opts = ch.time_columns(labels, [1] * 40, colors, series_name="Bits", previous=[10, 20, 30], as_line=True,
                           previous_name="Last time")
    series = opts["series"][0]
    assert series["type"] == "line" and series["showSymbol"] is False
    assert series["lineStyle"]["color"] == colors.primary
    mark = series["markLine"]
    assert mark["data"] == [{"yAxis": 20.0}]
    assert mark["label"]["formatter"] == "Last time: 20"
    assert mark["lineStyle"]["color"] == colors.ghost
    short = ch.time_columns(["x"], [1], colors, series_name="s", as_line=True)
    assert short["series"][0]["showSymbol"] is True


def test_time_columns_ignore_an_empty_previous_period(colors):
    opts = ch.time_columns(["a"], [1], colors, series_name="s", previous=[])
    assert "markLine" not in opts["series"][0]


def test_animation_follows_the_module_switch(colors, monkeypatch):
    monkeypatch.setattr(ch, "ANIMATE", False)
    assert ch.time_columns([], [], colors, series_name="s")["animation"] is False
    assert ch.level_columns([1], [1], colors)["animation"] is False


def test_weekly_heatmap(colors):
    grid = [[0] * 24 for _ in range(7)]
    grid[2][21] = 9
    grid[6][0] = 4
    opts = ch.weekly_heatmap(grid, colors, value_name="messages")
    data = opts["series"][0]["data"]
    assert len(data) == 168 and [21, 2, 9] in data and [0, 6, 4] in data
    assert opts["visualMap"]["max"] == 9
    assert opts["yAxis"]["data"] == ch.WEEKDAYS and opts["yAxis"]["inverse"] is True
    assert opts["xAxis"]["data"][0] == "00" and opts["xAxis"]["data"][-1] == "23"
    assert "messages" in opts["tooltip"][":formatter"]
    assert opts["visualMap"]["inRange"]["color"][-1] == colors.primary
    assert opts["animation"] is False


def test_weekly_heatmap_of_an_empty_grid_keeps_a_positive_scale(colors):
    opts = ch.weekly_heatmap([[0] * 24 for _ in range(7)], colors, value_name="events")
    assert opts["visualMap"]["max"] == 1


def test_level_columns(colors):
    opts = ch.level_columns([1, 2, 3], (4, 0, 1), colors)
    assert opts["xAxis"]["data"] == ["Level 1", "Level 2", "Level 3"]
    assert opts["series"][0]["data"] == [4, 0, 1]
    assert opts["series"][0]["label"]["show"] is True
    assert opts["yAxis"]["show"] is False and opts["yAxis"]["type"] == "value"
    assert opts["tooltip"]["trigger"] == "item"

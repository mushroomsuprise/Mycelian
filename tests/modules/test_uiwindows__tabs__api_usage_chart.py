# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""The live API usage graph: chart option, platform colors per theme, and redraws only on change."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

from modules import api_usage  # noqa: E402
from modules.uiwindows.tabs import api_usage_chart as chart  # noqa: E402
from modules.uiwindows.tabs.integration_ui import UsageRow  # noqa: E402
from modules.uiwindows.tabs.statistics_charts import ChartColors  # noqa: E402
from ui_sim import run_ui  # noqa: E402

DARK = ChartColors.from_theme(SimpleNamespace(bg_surface="rgb(35, 35, 45)"))
LIGHT = ChartColors.from_theme(SimpleNamespace(bg_surface="rgb(240, 240, 245)"))


@pytest.fixture(autouse=True)
def clean():
    api_usage.reset()
    yield
    api_usage.reset()


def data(psn=(5.0, 6.5, 75.0), steam=(0.1, 0.2, 0.3)):
    n = len(psn)
    return {"labels": [f"10:0{i}" for i in range(n)], "percent": {"PSN": list(psn), "Steam": list(steam)},
            "count": {"PSN": [15, 20, 225][:n], "Steam": [100, 200, 300][:n]},
            "limited": {"PSN": [0, 0, 1][:n], "Steam": [0, 0, 0][:n]}}


def test_one_platform_one_line_no_legend():
    opt = chart.usage_lines(data(), ["PSN"], DARK)
    (s,) = opt["series"]
    assert s["name"] == "PSN" and s["type"] == "line" and "legend" not in opt and "areaStyle" in s
    assert s["lineStyle"]["color"] == "#3987e5" and s["lineStyle"]["width"] == 2
    assert s["data"][2] == {"value": 75.0, "text": "75%", "detail": "225 of ~300 in 15 min", "limited": 1}
    assert opt["yAxis"]["max"] == 100 and opt["yAxis"]["min"] == 0
    assert opt["xAxis"]["data"] == ["10:00", "10:01", "10:02"]
    assert "rate-limited" in opt["tooltip"][":formatter"] and "%" in opt["yAxis"]["axisLabel"][":formatter"]


def test_two_platforms_with_a_legend_and_their_colors():
    opt = chart.usage_lines(data(), ["PSN", "Steam"], LIGHT)
    psn, steam = opt["series"]
    assert opt["legend"]["data"] == ["PSN", "Steam"] and "areaStyle" not in psn
    assert psn["lineStyle"]["color"] == "#2a78d6" and steam["lineStyle"]["color"] == "#eb6834"  # light steps
    assert steam["data"][0]["detail"] == "100 of 100,000 in 24 h"  # Steam's limit is published: no "~"
    assert chart.platform_color("Steam", DARK) == "#d95926" and chart.platform_color("Other", DARK) == DARK.primary


@pytest.mark.parametrize("values,top", [([], 10), ([0.5], 10), ([12], 25), ([49], 50), ([100], 100), ([101], 125)])
def test_scale_steps(values, top):
    assert chart.scale_max(values) == top


def test_limit_line_once_over_100_percent():
    opt = chart.usage_lines(data(psn=(90.0, 110.0, 130.0)), ["PSN"], DARK)
    assert opt["yAxis"]["max"] == 150
    assert opt["series"][0]["markLine"]["data"] == [{"yAxis": 100}]
    assert "markLine" not in chart.usage_lines(data(), ["PSN"], DARK)["series"][0]


def test_chart_colors_fall_back_without_a_theme(monkeypatch):
    from modules import theme_manager

    monkeypatch.setattr(theme_manager, "get_theme_manager", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert isinstance(chart.chart_colors(), ChartColors)


def test_usage_row_redraws_only_when_the_counts_change():
    rows: List[Any] = []

    async def check(user):
        row = rows[0]
        updates = []
        row.chart.update = lambda: updates.append(1)
        row.refresh()
        assert updates == []  # nothing new
        api_usage.record("PSN", "/x", 200)
        row.refresh()
        assert updates == [1] and row.chart.options["series"][0]["data"][-1]["detail"] == "1 of ~300 in 15 min"
        assert row.chart.props["aria-label"] == "PSN API use as a share of the limit, last hour"

    run_ui(lambda: rows.append(UsageRow(["PSN"])), check)


def test_usage_row_without_a_chart():
    rows: List[Any] = []

    async def check(user):
        assert rows[0].chart is None
        rows[0].refresh()

    run_ui(lambda: rows.append(UsageRow(["Steam"], chart=False)), check)


def test_usage_line_warns_near_the_limit():
    from modules.uiwindows.tabs.integration_ui import usage_html

    assert "is-warn" not in usage_html(["PSN"])
    for _ in range(250):
        api_usage.record("PSN", "/x", 200)
    html = usage_html(["PSN"])
    assert "is-warn" in html and "83% of the estimated limit" in html
    assert "community estimate" in html  # where the number comes from, on hover

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


def data(n=3):
    return {"labels": [f"10:0{i}" for i in range(n)], "counts": {"PSN": [1, 0, 4][:n], "Steam": [2, 2, 0][:n]},
            "limited": {"PSN": [0, 0, 1][:n], "Steam": [0, 0, 0][:n]}}


def test_one_platform_one_series_no_legend():
    opt = chart.usage_columns(data(), ["PSN"], DARK)
    (s,) = opt["series"]
    assert s["name"] == "PSN" and "stack" not in s and "legend" not in opt
    assert s["itemStyle"]["color"] == "#3987e5" and s["itemStyle"]["borderRadius"] == [4, 4, 0, 0]
    assert s["data"][2] == {"value": 4, "limited": 1}  # the tooltip shows 429s
    assert opt["xAxis"]["data"] == ["10:00", "10:01", "10:02"]
    assert ":formatter" in opt["tooltip"] and "rate-limited" in opt["tooltip"][":formatter"]


def test_two_platforms_stack_with_a_legend_and_keep_their_colors():
    opt = chart.usage_columns(data(), ["PSN", "Steam"], LIGHT)
    psn, steam = opt["series"]
    assert psn["stack"] == steam["stack"] == "requests"
    assert opt["legend"]["data"] == ["PSN", "Steam"]
    assert psn["itemStyle"]["color"] == "#2a78d6" and steam["itemStyle"]["color"] == "#eb6834"  # light steps
    assert psn["itemStyle"]["borderRadius"] == 0 and steam["itemStyle"]["borderRadius"] == [4, 4, 0, 0]
    assert psn["itemStyle"]["borderColor"] == LIGHT.surface  # the gap between segments
    assert chart.platform_color("Steam", DARK) == "#d95926" and chart.platform_color("Other", DARK) == DARK.primary


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
        assert updates == [1] and row.chart.options["series"][0]["data"][-1]["value"] == 1
        assert row.chart.props["aria-label"] == "PSN API requests per minute, last hour"

    run_ui(lambda: rows.append(UsageRow(["PSN"])), check)


def test_usage_row_without_a_chart():
    rows: List[Any] = []

    async def check(user):
        assert rows[0].chart is None
        rows[0].refresh()

    run_ui(lambda: rows.append(UsageRow(["Steam"], chart=False)), check)

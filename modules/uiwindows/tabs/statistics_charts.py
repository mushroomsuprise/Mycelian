# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""ECharts option builders for the Statistics dashboard.

ECharts draws on a canvas, so CSS variables cannot reach it: colors are resolved
from the active ThemeColors in Python and passed in as ``ChartColors``.
Marks follow one spec everywhere: columns capped at 24px with 4px rounded tops,
2px lines, hairline grid, recessive axes, crosshair/per-mark tooltips.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

# Entry animations. Screenshot tooling turns this off: hidden pages never run frames.
ANIMATE = True

_RGB_RE = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+)\s*)?\)")


def _rgb(value: str, fallback=(128, 128, 128)) -> tuple:
    m = _RGB_RE.search(value or "")
    if not m:
        h = (value or "").lstrip("#")
        if len(h) == 6:
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        return fallback
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def mix(a: str, b: str, t: float) -> str:
    """Blend color ``a`` toward ``b`` by ``t`` (0..1) as an rgb() string."""
    ra, rb = _rgb(a), _rgb(b)
    return "rgb({},{},{})".format(*(round(x + (y - x) * t) for x, y in zip(ra, rb)))


@dataclass(frozen=True)
class ChartColors:
    primary: str
    surface: str
    base: str
    text: str
    text_secondary: str
    text_muted: str
    grid: str
    axis: str
    ghost: str  # de-emphasis series (previous period)

    @classmethod
    def from_theme(cls, theme: Any) -> "ChartColors":
        def g(name: str, default: str) -> str:
            return (getattr(theme, name, "") or default) if theme is not None else default

        surface = g("bg_surface", "rgb(35,35,45)")
        text = g("text_primary", "rgb(240,240,255)")
        muted = g("text_muted", "rgb(160,160,180)")
        return cls(
            primary=g("primary", "rgb(115,0,255)"),
            surface=surface,
            base=g("bg_base", "rgb(18,18,24)"),
            text=text,
            text_secondary=g("text_secondary", text),
            text_muted=muted,
            # Concrete one-step-off-surface hairlines (the theme's are translucent).
            grid=mix(surface, text, 0.08),
            axis=mix(surface, text, 0.22),
            ghost=mix(surface, muted, 0.55),
        )


def _compact_label(v: float) -> str:
    if v >= 1e6:
        return f"{v / 1e6:.1f}M"
    if v >= 1e4:
        return f"{v / 1e3:.1f}K"
    return f"{v:,.0f}" if v >= 10 else f"{v:.1f}"


def _tooltip(c: ChartColors, trigger: str = "axis") -> Dict[str, Any]:
    tip: Dict[str, Any] = {
        "trigger": trigger,
        "backgroundColor": c.base,
        "borderColor": c.axis,
        "borderWidth": 1,
        "padding": [8, 10],
        "textStyle": {"color": c.text, "fontSize": 12},
        "extraCssText": "border-radius:8px;box-shadow:0 6px 18px rgba(0,0,0,.25);",
    }
    if trigger == "axis":
        tip["axisPointer"] = {
            "type": "line",
            "lineStyle": {"color": c.axis, "width": 1},
            "z": 0,
        }
    return tip


def _value_axis(c: ChartColors) -> Dict[str, Any]:
    return {
        "type": "value",
        "minInterval": 1,
        "splitNumber": 4,
        "axisLine": {"show": False},
        "axisTick": {"show": False},
        "splitLine": {"lineStyle": {"color": c.grid, "width": 1}},
        "axisLabel": {
            "color": c.text_muted,
            "fontSize": 11,
            # Compact ticks: 1K / 2.5K / 1M
            ":formatter": "v => v >= 1e6 ? (v/1e6).toFixed(v % 1e6 ? 1 : 0) + 'M' : v >= 1e3 ? (v/1e3).toFixed(v % 1e3 ? 1 : 0) + 'K' : v",
        },
    }


def time_columns(
    labels: Sequence[str],
    values: Sequence[float],
    c: ChartColors,
    *,
    series_name: str,
    previous: Optional[Sequence[float]] = None,
    previous_name: str = "Previous period avg",
    as_line: bool = False,
) -> Dict[str, Any]:
    """Change over time for one metric; optional previous period as a ghost line."""
    series: List[Dict[str, Any]] = []
    if as_line:
        series.append({
            "name": series_name,
            "type": "line",
            "data": list(values),
            "smooth": False,
            "showSymbol": len(labels) <= 31,
            "symbolSize": 8,
            "lineStyle": {"width": 2, "color": c.primary},
            "itemStyle": {"color": c.primary, "borderColor": c.surface, "borderWidth": 2},
            "areaStyle": {"color": c.primary, "opacity": 0.10},
            "emphasis": {"focus": "none"},
            "z": 3,
        })
    else:
        series.append({
            "name": series_name,
            "type": "bar",
            "data": list(values),
            "barMaxWidth": 24,
            "barCategoryGap": "28%",
            "itemStyle": {"color": c.primary, "borderRadius": [4, 4, 0, 0]},
            "emphasis": {"itemStyle": {"color": mix(c.primary, c.text, 0.18)}},
            "z": 2,
        })
    if previous is not None and len(previous):
        # The previous period as one reference line (its average per bucket): day-to-day
        # zigzags from off-stream days would bury the comparison.
        avg = sum(previous) / len(previous)
        series[0]["markLine"] = {
            "silent": True,
            "symbol": "none",
            "lineStyle": {"color": c.ghost, "width": 2, "type": "solid"},
            "label": {
                "position": "insideEndTop",
                "color": c.text_secondary,
                "fontSize": 11,
                "formatter": f"{previous_name}: {_compact_label(avg)}",
            },
            "data": [{"yAxis": round(avg, 1)}],
        }
    return {
        "animation": ANIMATE,
        "animationDuration": 350,
        "grid": {"left": 6, "right": 12, "top": 18, "bottom": 4, "containLabel": True},
        "tooltip": _tooltip(c),
        "xAxis": {
            "type": "category",
            "data": list(labels),
            "axisTick": {"show": False},
            "axisLine": {"lineStyle": {"color": c.axis}},
            "axisLabel": {"color": c.text_muted, "fontSize": 11, "hideOverlap": True},
        },
        "yAxis": _value_axis(c),
        "series": series,
    }


WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def weekly_heatmap(grid: List[List[int]], c: ChartColors, *, value_name: str) -> Dict[str, Any]:
    """Weekday × hour grid in one sequential hue (surface → primary)."""
    data = [[h, d, grid[d][h]] for d in range(7) for h in range(24)]
    peak = max((v for row in grid for v in row), default=0)
    return {
        "animation": False,
        "grid": {"left": 4, "right": 8, "top": 6, "bottom": 34, "containLabel": True},
        "tooltip": {
            **_tooltip(c, "item"),
            ":formatter": (
                "p => `<b>${p.value[2].toLocaleString()}</b> " + value_name
                + "<br><span style='opacity:.7'>${['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][p.value[1]]} "
                "${String(p.value[0]).padStart(2,'0')}:00–${String((p.value[0]+1)%24).padStart(2,'0')}:00</span>`"
            ),
        },
        "xAxis": {
            "type": "category",
            "data": [f"{h:02d}" for h in range(24)],
            "splitArea": {"show": False},
            "axisTick": {"show": False},
            "axisLine": {"show": False},
            "axisLabel": {"color": c.text_muted, "fontSize": 10, "interval": 2},
        },
        "yAxis": {
            "type": "category",
            "data": WEEKDAYS,
            "inverse": True,
            "axisTick": {"show": False},
            "axisLine": {"show": False},
            "axisLabel": {"color": c.text_muted, "fontSize": 11},
        },
        "visualMap": {
            "min": 0,
            "max": max(peak, 1),
            "calculable": False,
            "orient": "horizontal",
            "left": "center",
            "bottom": 0,
            "itemHeight": 120,
            "itemWidth": 10,
            "text": ["More", "Less"],
            "textStyle": {"color": c.text_muted, "fontSize": 10},
            "inRange": {"color": [mix(c.surface, c.text, 0.06), mix(c.surface, c.primary, 0.45), c.primary]},
        },
        "series": [{
            "type": "heatmap",
            "data": data,
            "itemStyle": {"borderColor": c.surface, "borderWidth": 2, "borderRadius": 3},
            "emphasis": {"itemStyle": {"borderColor": c.text, "borderWidth": 1}},
        }],
    }


def level_columns(levels: Sequence[int], counts: Sequence[int], c: ChartColors) -> Dict[str, Any]:
    """Hype train completions per level (magnitude, one hue)."""
    return {
        "animation": ANIMATE,
        "animationDuration": 300,
        "grid": {"left": 4, "right": 8, "top": 18, "bottom": 2, "containLabel": True},
        "tooltip": {**_tooltip(c, "item"), ":formatter": "p => `<b>${p.value.toLocaleString()}</b> completed<br><span style='opacity:.7'>${p.name}</span>`"},
        "xAxis": {
            "type": "category",
            "data": [f"Level {lvl}" for lvl in levels],
            "axisTick": {"show": False},
            "axisLine": {"lineStyle": {"color": c.axis}},
            "axisLabel": {"color": c.text_muted, "fontSize": 11},
        },
        "yAxis": {**_value_axis(c), "show": False},
        "series": [{
            "type": "bar",
            "data": list(counts),
            "barMaxWidth": 24,
            "itemStyle": {"color": c.primary, "borderRadius": [4, 4, 0, 0]},
            "label": {"show": True, "position": "top", "color": c.text_secondary, "fontSize": 11},
        }],
    }

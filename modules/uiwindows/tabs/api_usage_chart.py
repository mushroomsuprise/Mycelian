# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""ECharts option for the live API usage graph in Settings → PSN, Steam and Games Tracker.

Requests per minute over the last hour, as columns (stacked when both platforms are shown).
Each platform keeps one color everywhere: categorical slot 1 (blue) for PSN and slot 2
(orange) for Steam, stepped for the light or dark surface. The two brand blues are too
close to tell apart, so they aren't used here. Validated with the dataviz palette checker
on the app's surfaces (#23232D dark, #F0F0F5 light): all hard checks pass; light orange is
under 3:1, so the legend, the exact numbers in the line above the graph and the tooltip
carry the values too.
"""

from __future__ import annotations

from typing import Any, Dict, Sequence

from .statistics_charts import ANIMATE, ChartColors, _rgb, _tooltip, _value_axis, mix

# platform -> (light step, dark step)
PLATFORM_COLORS = {
    "PSN": ("#2a78d6", "#3987e5"),
    "Steam": ("#eb6834", "#d95926"),
}

# Axis tooltip: the minute, each platform's count (and its 429s), and the total for two platforms.
_TOOLTIP_FORMATTER = (
    "ps => { let h = ps[0].axisValueLabel; let t = 0;"
    " for (const p of ps) { const v = p.value || 0; t += v;"
    " h += '<br>' + p.marker + p.seriesName + ': <b>' + v + '</b>'"
    " + (p.data && p.data.limited ? ' · ' + p.data.limited + ' rate-limited' : ''); }"
    " if (ps.length > 1) h += '<br>Total: <b>' + t + '</b>';"
    " return h; }"
)


def _is_dark(c: ChartColors) -> bool:
    r, g, b = _rgb(c.surface)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) < 128


def platform_color(platform: str, c: ChartColors) -> str:
    light, dark = PLATFORM_COLORS.get(platform, (c.primary, c.primary))
    return dark if _is_dark(c) else light


def usage_columns(data: Dict[str, Any], platforms: Sequence[str], c: ChartColors) -> Dict[str, Any]:
    """Columns of requests per minute; ``data`` is ``api_usage.timeline(platforms)``."""
    platforms = list(platforms)
    stacked = len(platforms) > 1
    series = []
    for i, platform in enumerate(platforms):
        color = platform_color(platform, c)
        top = i == len(platforms) - 1
        counts = data["counts"].get(platform) or []
        limited = data["limited"].get(platform) or []
        entry = {
            "name": platform,
            "type": "bar",
            "data": [{"value": n, "limited": limited[j] if j < len(limited) else 0} for j, n in enumerate(counts)],
            "barMaxWidth": 10,
            "barCategoryGap": "30%",
            # A surface-colored edge keeps a gap between stacked segments; the top one gets the rounded end.
            "itemStyle": {
                "color": color,
                "borderRadius": [4, 4, 0, 0] if top else 0,
                "borderColor": c.surface if stacked else color,
                "borderWidth": 1 if stacked else 0,
            },
            "emphasis": {"itemStyle": {"color": mix(color, c.text, 0.18)}},
        }
        if stacked:
            entry["stack"] = "requests"
        series.append(entry)
    option: Dict[str, Any] = {
        "animation": ANIMATE,
        "animationDuration": 300,
        "animationDurationUpdate": 200,
        "grid": {"left": 4, "right": 8, "top": 26 if stacked else 10, "bottom": 2, "containLabel": True},
        "tooltip": {**_tooltip(c), ":formatter": _TOOLTIP_FORMATTER},
        "xAxis": {
            "type": "category",
            "data": list(data["labels"]),
            "axisTick": {"show": False},
            "axisLine": {"lineStyle": {"color": c.axis}},
            "axisLabel": {"color": c.text_muted, "fontSize": 10, "interval": 14},
        },
        "yAxis": {**_value_axis(c), "splitNumber": 3},
        "series": series,
    }
    if stacked:
        option["legend"] = {
            "top": 0,
            "right": 4,
            "itemWidth": 10,
            "itemHeight": 10,
            "icon": "roundRect",
            "textStyle": {"color": c.text_secondary, "fontSize": 11},
            "data": platforms,
        }
    return option


def chart_colors() -> ChartColors:
    try:
        from ...theme_manager import get_theme_manager

        return ChartColors.from_theme(get_theme_manager().get_theme())
    except Exception:
        return ChartColors.from_theme(None)

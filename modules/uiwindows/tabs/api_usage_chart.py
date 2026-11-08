# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""ECharts option for the live API usage graph in Settings → PSN, Steam and Games Tracker.

The share of each platform's request limit in use (``api_usage.LIMITS``: PSN's estimated
300 per 15 minutes, Steam's 100,000 per day), once a minute over the last hour, one line per
platform. The scale steps 10 / 25 / 50 / 100%, so low use is still readable; past 100% it grows
and a "Limit" line marks 100%.

Each platform keeps one color everywhere: categorical slot 1 (blue) for PSN and slot 2
(orange) for Steam, stepped for the light or dark surface. The two brand blues are too
close to tell apart, so they aren't used here. Validated with the dataviz palette checker
on the app's surfaces (#23232D dark, #F0F0F5 light): all hard checks pass; light orange is
under 3:1, so the legend, the exact numbers in the line above the graph and the tooltip
carry the values too.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Sequence

from .statistics_charts import ANIMATE, ChartColors, _rgb, _tooltip, _value_axis

# platform -> (light step, dark step)
# Fixed categorical slots per platform (a platform keeps its color on every page). Only PSN and
# Steam ever share a graph (Games Tracker), and that pair passes the palette checker in both themes.
PLATFORM_COLORS = {
    "PSN": ("#2a78d6", "#3987e5"),  # slot 1 blue
    "Steam": ("#eb6834", "#d95926"),  # slot 2 orange
    "Spotify": ("#1baf7a", "#199e70"),  # slot 3 aqua
    "Streamlabs": ("#008300", "#008300"),  # slot 6 green
    "StreamElements": ("#e87ba4", "#d55181"),  # slot 5 magenta
    "Twitch": ("#6250d6", "#9085e9"),  # slot 7 violet
    "YouTube": ("#e34948", "#e66767"),  # slot 8 red
}

# Axis tooltip: the minute, then each platform's share of its limit, the requests behind it and its 429s.
_TOOLTIP_FORMATTER = (
    "ps => { let h = ps[0].axisValueLabel;"
    " for (const p of ps) { const d = p.data || {};"
    " h += '<br>' + p.marker + p.seriesName + ': <b>' + (d.text || '0%') + '</b>'"
    " + (d.detail ? ' · ' + d.detail : '')"
    " + (d.limited ? ' · ' + d.limited + ' rate-limited' : ''); }"
    " return h; }"
)
SCALE_STEPS = (10, 25, 50, 100)


def _is_dark(c: ChartColors) -> bool:
    r, g, b = _rgb(c.surface)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) < 128


def platform_color(platform: str, c: ChartColors) -> str:
    light, dark = PLATFORM_COLORS.get(platform, (c.primary, c.primary))
    return dark if _is_dark(c) else light


def scale_max(values: Sequence[float]) -> float:
    """The y-axis top: the first of 10 / 25 / 50 / 100% that fits, else the next 25% above."""
    top = max(values, default=0.0)
    for step in SCALE_STEPS:
        if top <= step:
            return float(step)
    return float(math.ceil(top / 25.0) * 25)


def usage_chart(data: Dict[str, Any], platforms: Sequence[str], c: ChartColors) -> Dict[str, Any]:
    """The graph for ``api_usage.series(platforms)``: the share of the limit in use when every
    platform has a known limit, else requests per step as columns."""
    if all(data["percent"].get(p) is not None for p in platforms):
        return usage_lines(data, platforms, c)
    return usage_columns(data, platforms, c)


def _x_axis(data: Dict[str, Any], c: ChartColors, *, boundary: bool) -> Dict[str, Any]:
    labels = list(data["labels"])
    return {
        "type": "category",
        "boundaryGap": boundary,
        "data": labels,
        "axisTick": {"show": False},
        "axisLine": {"lineStyle": {"color": c.axis}},
        "axisLabel": {"color": c.text_muted, "fontSize": 10, "interval": max(0, len(labels) // 4 - 1)},
    }


_COLUMN_TOOLTIP = (
    "ps => { let h = ps[0].axisValueLabel;"
    " for (const p of ps) { const d = p.data || {};"
    " h += '<br>' + p.marker + p.seriesName + ': <b>' + (d.value || 0) + '</b> ' + (d.per || 'requests')"
    " + (d.limited ? ' · ' + d.limited + ' rate-limited' : ''); }"
    " return h; }"
)


def usage_columns(data: Dict[str, Any], platforms: Sequence[str], c: ChartColors) -> Dict[str, Any]:
    """Requests per step (a minute, 15 minutes or 2 hours) for platforms without a published limit."""
    step = int(data.get("step_minutes") or 1)
    per = "requests" if step == 1 else f"requests in {step // 60} h" if step >= 60 else f"requests in {step} min"
    series = []
    for platform in platforms:
        color = platform_color(platform, c)
        counts = data["requests"].get(platform) or []
        limited = data["limited"].get(platform) or []
        series.append({
            "name": platform,
            "type": "bar",
            "data": [{"value": n, "per": per, "limited": limited[j] if j < len(limited) else 0}
                     for j, n in enumerate(counts)],
            "barMaxWidth": 10,
            "barCategoryGap": "30%",
            "itemStyle": {"color": color, "borderRadius": [4, 4, 0, 0]},
        })
    return {
        "animation": ANIMATE,
        "animationDuration": 300,
        "animationDurationUpdate": 200,
        "grid": {"left": 4, "right": 8, "top": 10, "bottom": 2, "containLabel": True},
        "tooltip": {**_tooltip(c), ":formatter": _COLUMN_TOOLTIP},
        "xAxis": _x_axis(data, c, boundary=True),
        "yAxis": {**_value_axis(c), "splitNumber": 3},
        "series": series,
    }


def usage_lines(data: Dict[str, Any], platforms: Sequence[str], c: ChartColors) -> Dict[str, Any]:
    """Share of the limit in use over time; ``data`` is ``api_usage.series(platforms)``."""
    from ...api_usage import LIMITS, percent_text

    platforms = list(platforms)
    several = len(platforms) > 1
    series = []
    every = []
    for platform in platforms:
        color = platform_color(platform, c)
        limit = LIMITS.get(platform)
        percents = data["percent"].get(platform) or []
        counts = data["count"].get(platform) or []
        limited = data["limited"].get(platform) or []
        every.extend(percents)
        approx = "~" if limit is not None and limit.estimated else ""
        unit = "" if limit is None or limit.unit == "requests" else f" {limit.unit}"
        within = "that day" if limit is not None and limit.window_text == "today" else (
            f"in {limit.window_text}" if limit is not None else "")
        points = []
        for j, pct in enumerate(percents):
            n = counts[j] if j < len(counts) else 0
            detail = f"{n:,} of {approx}{limit.requests:,}{unit} {within}" if limit else f"{n:,} requests"
            points.append({"value": pct, "text": percent_text(pct), "detail": detail,
                           "limited": limited[j] if j < len(limited) else 0})
        entry: Dict[str, Any] = {
            "name": platform,
            "type": "line",
            "data": points,
            "smooth": False,
            "showSymbol": False,
            "symbolSize": 8,
            "lineStyle": {"width": 2, "color": color},
            "itemStyle": {"color": color, "borderColor": c.surface, "borderWidth": 2},
            "emphasis": {"focus": "none"},
        }
        if not several:
            entry["areaStyle"] = {"color": color, "opacity": 0.10}
        series.append(entry)
    top = scale_max(every)
    if top > 100 and series:
        series[0]["markLine"] = {
            "silent": True,
            "symbol": "none",
            "lineStyle": {"color": c.axis, "width": 1, "type": "dashed"},
            "label": {"position": "insideEndTop", "color": c.text_secondary, "fontSize": 11, "formatter": "Limit"},
            "data": [{"yAxis": 100}],
        }
    option: Dict[str, Any] = {
        "animation": ANIMATE,
        "animationDuration": 300,
        "animationDurationUpdate": 200,
        "grid": {"left": 4, "right": 8, "top": 26 if several else 10, "bottom": 2, "containLabel": True},
        "tooltip": {**_tooltip(c), ":formatter": _TOOLTIP_FORMATTER},
        "xAxis": _x_axis(data, c, boundary=False),
        "yAxis": {
            **_value_axis(c),
            "min": 0,
            "max": top,
            "splitNumber": 2,
            "minInterval": 0,
            "axisLabel": {"color": c.text_muted, "fontSize": 11, ":formatter": "v => v + '%'"},
        },
        "series": series,
    }
    if several:
        option["legend"] = {
            "top": 0,
            "right": 4,
            "itemWidth": 14,
            "itemHeight": 3,
            "icon": "rect",
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

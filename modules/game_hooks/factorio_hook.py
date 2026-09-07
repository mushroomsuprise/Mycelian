# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Read-only Factorio hook: poll script-output JSON written by the companion mod."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .base import HookUiMetadata, runtime_os_key
from .memory.base import find_process_pid

logger = logging.getLogger(__name__)

HOOK_ID = "factorio"
_WRITE_PATH_RE = re.compile(r"Write data path:\s*(.+?)(?:\s*\[\d+/\d+MB\])?\s*$")
_READ_PATH_RE = re.compile(r"Read data path:\s*(.+?)\s*$")
_DEFAULT_STALENESS_SEC = 5.0
_PRECISION_KEYS = (
    "five_seconds",
    "one_minute",
    "ten_minutes",
    "one_hour",
    "ten_hours",
    "fifty_hours",
    "two_hundred_fifty_hours",
    "one_thousand_hours",
)
_MOD_MISSING_ERROR = (
    "Mycelian stats mod not detected — install it from Settings > Game Hooks"
)
SERIES_POINTS = 60
FLOW_WINDOW_SAMPLES = 300


def default_data_dirs(platform: Optional[str] = None) -> List[str]:
    """User-data directories Factorio uses on each OS, in search order."""
    plat = platform if platform is not None else sys.platform
    home = os.path.expanduser("~")
    out: List[str] = []
    if plat == "win32":
        appdata = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        out.append(os.path.join(appdata, "Factorio"))
    elif plat == "darwin":
        out.append(
            os.path.join(home, "Library", "Application Support", "factorio")
        )
    else:
        out.append(os.path.join(home, ".factorio"))
    return out


def parse_write_data_path(log_text: str) -> Optional[str]:
    """Extract the user-data directory from a factorio-current.log snippet."""
    for line in (log_text or "").splitlines():
        match = _WRITE_PATH_RE.search(line)
        if match:
            path = match.group(1).strip().strip('"')
            if path:
                return path
    return None


def parse_read_data_path(log_text: str) -> Optional[str]:
    """Extract the game install ``data/`` directory from factorio-current.log."""
    for line in (log_text or "").splitlines():
        match = _READ_PATH_RE.search(line)
        if match:
            path = match.group(1).strip().strip('"')
            if path:
                return path
    return None


def _looks_like_factorio_data_dir(path: str) -> bool:
    if not path or not os.path.isdir(path):
        return False
    return any(
        os.path.exists(os.path.join(path, name))
        for name in (
            "factorio-current.log",
            "player-data.json",
            "config",
            "saves",
            "mods",
            "script-output",
        )
    )


def portable_data_dir_from_exe(exe_path: Optional[str]) -> Optional[str]:
    """Guess a portable user-data dir sitting next to the Factorio executable."""
    if not exe_path:
        return None
    exe_dir = os.path.dirname(os.path.abspath(exe_path))
    candidates = [
        os.path.join(exe_dir, "factorio"),
        os.path.join(exe_dir, "..", ".."),
        os.path.join(exe_dir, ".."),
        exe_dir,
    ]
    for raw in candidates:
        path = os.path.abspath(raw)
        if _looks_like_factorio_data_dir(path):
            return path
    return None


def find_factorio_pid() -> Optional[int]:
    """PID of a running Factorio process (exact names, then exe-path fallback)."""
    pid = find_process_pid(FactorioGameHook.process_names)
    if pid:
        return pid
    try:
        import psutil
    except Exception:
        return None
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = (proc.info.get("name") or "").lower()
            exe = (proc.info.get("exe") or "").lower()
            if "factorio" not in name and "factorio" not in exe:
                continue
            if "crash" in name or "crash" in exe:
                continue
            return int(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def process_exe_path(pid: Optional[int] = None) -> Optional[str]:
    if pid is None:
        pid = find_factorio_pid()
    if not pid:
        return None
    try:
        import psutil

        proc = psutil.Process(int(pid))
        exe = proc.exe()
        return exe if exe else None
    except Exception:
        return None


def resolve_data_dir(
    override: Optional[str] = None,
    *,
    platform: Optional[str] = None,
    exe_path: Optional[str] = None,
    read_log: bool = True,
) -> Optional[str]:
    """Resolve Factorio's write-data directory.

    Order: explicit override, OS default, portable dir next to the exe,
    ``Write data path:`` in factorio-current.log.
    """
    if override:
        expanded = os.path.abspath(os.path.expanduser(str(override).strip()))
        if _looks_like_factorio_data_dir(expanded) or os.path.isdir(expanded):
            return expanded

    candidates: List[str] = []
    candidates.extend(default_data_dirs(platform))
    portable = portable_data_dir_from_exe(exe_path)
    if portable:
        candidates.append(portable)

    seen: set[str] = set()
    unique: List[str] = []
    for path in candidates:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        unique.append(os.path.abspath(path))

    if read_log:
        for path in unique:
            log_path = os.path.join(path, "factorio-current.log")
            if not os.path.isfile(log_path):
                continue
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                    snippet = fh.read(8192)
            except OSError:
                continue
            parsed = parse_write_data_path(snippet)
            if parsed and os.path.isdir(parsed):
                return parsed

    for path in unique:
        if _looks_like_factorio_data_dir(path):
            return path
    return unique[0] if unique else None


def stats_json_path(data_dir: str) -> str:
    return os.path.join(data_dir, "script-output", "mycelian", "stats.json")


def flat_template_config(cfg: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not isinstance(cfg, dict):
        return out
    for element in cfg.get("elements") or []:
        if isinstance(element, dict) and "id" in element and "value" in element:
            out[str(element["id"])] = element["value"]
    return out


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_item_list(raw: Any) -> List[str]:
    """Split a textarea / comma list of prototype names."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw).replace(",", "\n")
    return [line.strip() for line in text.splitlines() if line.strip()]


def downsample_sample_indices(
    points: int = SERIES_POINTS,
    window: int = FLOW_WINDOW_SAMPLES,
) -> List[int]:
    """Factorio ``sample_index`` 1 is newest. Return oldest-first (left → now)."""
    pts = max(1, int(points))
    win = max(pts, int(window))
    step = max(1, win // pts)
    newest_first = [1 + i * step for i in range(pts)]
    return list(reversed(newest_first))


def _as_float_list(raw: Any) -> Optional[List[float]]:
    if not isinstance(raw, list) or not raw:
        return None
    out: List[float] = []
    for x in raw:
        try:
            out.append(float(x or 0.0))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def _series_values(
    series: Any,
    precision: str,
    inner_key: Optional[str] = None,
) -> Optional[List[float]]:
    """Pick a 60-sample array from nested or flat series payloads."""
    if isinstance(series, list):
        return _as_float_list(series)
    if not isinstance(series, dict):
        return None
    nested_precisions = any(k in series for k in _PRECISION_KEYS)
    if inner_key and not nested_precisions:
        picked = _as_float_list(series.get(inner_key))
        if picked:
            return picked
    block = series.get(precision) if precision in series else None
    if block is None:
        block = series.get("one_minute")
    if inner_key:
        if isinstance(block, dict):
            return _as_float_list(block.get(inner_key))
        if isinstance(block, list):
            return _as_float_list(block)
        return _as_float_list(series.get(inner_key))
    if isinstance(block, list):
        return _as_float_list(block)
    if isinstance(block, dict):
        return _as_float_list(
            block.get("produced")
            or block.get("generation")
            or block.get("spm")
            or block.get("consumption")
        )
    return _as_float_list(series.get("produced") or series.get("generation"))


def _reshape_power_rows(rows: Any, precision: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        arr = _series_values(row.get("series"), precision)
        if arr:
            item["series"] = arr
        else:
            item.pop("series", None)
        out.append(item)
    return out


def _reshape_power_block(power: Any, precision: str) -> Dict[str, Any]:
    if not isinstance(power, dict):
        power = {}
    power_series = power.get("series") if isinstance(power.get("series"), dict) else {}
    gen_series = _series_values(power_series, precision, "generation")
    cons_series = _series_values(power_series, precision, "consumption")
    power_series_out: Dict[str, List[float]] = {}
    if gen_series:
        power_series_out["generation"] = gen_series
    if cons_series:
        power_series_out["consumption"] = cons_series
    return {
        "network_id": power.get("network_id"),
        "surface": power.get("surface"),
        "generation_w": float(power.get("generation_w") or 0),
        "capacity_w": float(power.get("capacity_w") or 0),
        "consumption_w": float(power.get("consumption_w") or 0),
        "satisfaction": float(power.get("satisfaction") or 0),
        "accumulator_j": float(power.get("accumulator_j") or 0),
        "accumulator_capacity_j": float(power.get("accumulator_capacity_j") or 0),
        "producers": _reshape_power_rows(power.get("producers"), precision),
        "consumers": _reshape_power_rows(power.get("consumers"), precision),
        "series": power_series_out,
    }


def cycled_planet_name(
    visited: Sequence[str],
    current: Any,
    *,
    cycle: bool,
    now_ms: int,
    period_ms: int,
) -> str:
    """Pick the planet shown when cycle mode is on; same inputs → same planet."""
    names = [str(n).strip() for n in visited if str(n or "").strip()]
    cur = str(current or "").strip()
    if cur.lower().startswith("platform:"):
        fallback = names[0] if names else cur
    else:
        fallback = cur or (names[0] if names else "")
    if not cycle or len(names) < 2:
        return fallback
    period = max(1, int(period_ms))
    idx = (max(0, int(now_ms)) // period) % len(names)
    return names[idx]


def _rate_for(row: Dict[str, Any], precision: str, kind: str) -> float:
    rates = row.get("rates")
    if isinstance(rates, dict):
        bucket = rates.get(precision) or rates.get("one_minute") or {}
        if isinstance(bucket, dict):
            try:
                return float(bucket.get(kind) or 0.0)
            except (TypeError, ValueError):
                return 0.0
    # Flat fallback used by older snapshots / tests.
    key = "per_min" if kind == "produced" else "consumed_per_min"
    try:
        return float(row.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _normalize_flow_row(row: Any, precision: str) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict):
        return None
    name = str(row.get("name") or "").strip()
    if not name:
        return None
    produced = _rate_for(row, precision, "produced")
    consumed = _rate_for(row, precision, "consumed")
    try:
        total_produced = float(row.get("produced") or row.get("total") or 0.0)
    except (TypeError, ValueError):
        total_produced = 0.0
    try:
        total_consumed = float(row.get("consumed") or 0.0)
    except (TypeError, ValueError):
        total_consumed = 0.0
    produced_series = _series_values(row.get("series"), precision, "produced")
    consumed_series = _series_values(row.get("series"), precision, "consumed")
    out: Dict[str, Any] = {
        "name": name,
        "per_min": produced,
        "consumed_per_min": consumed,
        "total": total_produced,
        "total_consumed": total_consumed,
        "count": _as_int(row.get("count"), 0),
        "_series_produced": produced_series,
        "_series_consumed": consumed_series,
    }
    return out


def _filter_flow_rows(
    rows: Any,
    *,
    precision: str,
    names: Sequence[str],
    top_n: int,
    rate_key: str,
) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for row in rows or []:
        item = _normalize_flow_row(row, precision)
        if item is not None:
            normalized.append(item)
    if names:
        wanted = {n.lower() for n in names}
        selected = [r for r in normalized if r["name"].lower() in wanted]
        # Preserve user order when an explicit list is given.
        order = {n.lower(): i for i, n in enumerate(names)}
        selected.sort(key=lambda r: order.get(r["name"].lower(), 10_000))
        return [_finalize_flow_row(r, rate_key) for r in selected]
    limit = max(1, int(top_n or 12))
    normalized.sort(key=lambda r: float(r.get(rate_key) or 0.0), reverse=True)
    return [_finalize_flow_row(r, rate_key) for r in normalized[:limit]]


def _finalize_flow_row(row: Dict[str, Any], rate_key: str) -> Dict[str, Any]:
    item = dict(row)
    produced = item.pop("_series_produced", None)
    consumed = item.pop("_series_consumed", None)
    series = consumed if rate_key == "consumed_per_min" else produced
    if series:
        item["series"] = series
    else:
        item.pop("series", None)
    return item


def classify_file_state(
    *,
    exists: bool,
    age_sec: Optional[float],
    staleness_sec: float,
    has_last: bool,
) -> str:
    """Return waiting_for_mod | paused | attached."""
    if not exists and not has_last:
        return "waiting_for_mod"
    if age_sec is None:
        return "paused" if has_last else "waiting_for_mod"
    if age_sec > max(0.5, float(staleness_sec)):
        return "paused" if has_last else "waiting_for_mod"
    return "attached"


def si_format(value: Any, unit: str = "", *, precision: int = 1) -> str:
    """Format a number with SI prefixes (1.4k, 525 MW, 12.2 GJ)."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        amount = 0.0
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    suffixes = (
        (1e18, "E"),
        (1e15, "P"),
        (1e12, "T"),
        (1e9, "G"),
        (1e6, "M"),
        (1e3, "k"),
    )
    suffix = ""
    scaled = amount
    for threshold, label in suffixes:
        if amount >= threshold:
            scaled = amount / threshold
            suffix = label
            break
    if scaled >= 100 or abs(scaled - round(scaled)) < 1e-9:
        number = str(int(round(scaled)))
    else:
        number = f"{scaled:.{precision}f}".rstrip("0").rstrip(".")
    return f"{sign}{number}{suffix}{unit}"


def format_playtime_text(ticks: Any) -> str:
    """Format map ticks the way Factorio does, with days once past 24h."""
    try:
        tick_n = int(float(ticks))
    except (TypeError, ValueError):
        return "--:--:--"
    sec = max(0, tick_n // 60)
    seconds = sec % 60
    minutes = (sec // 60) % 60
    hours = sec // 3600
    if hours >= 24:
        days = hours // 24
        hours = hours % 24
        return f"{days}d {hours}:{minutes:02d}:{seconds:02d}"
    return f"{hours}:{minutes:02d}:{seconds:02d}"


_PLATFORM_STATE_LABELS = {
    "0": "Waiting for pack",
    "1": "Pack inbound",
    "2": "In transit",
    "3": "Departing",
    "4": "No schedule",
    "5": "No path",
    "6": "Docked",
    "7": "Paused",
    "8": "Pack requested",
    "on_the_path": "In transit",
    "waiting_at_station": "Docked",
    "waiting_for_departure": "Departing",
    "no_schedule": "No schedule",
    "no_path": "No path",
    "paused": "Paused",
    "waiting_for_starter_pack": "Waiting for pack",
    "starter_pack_on_the_way": "Pack inbound",
    "starter_pack_requested": "Pack requested",
}


def _pretty_proto(name: str) -> str:
    text = str(name or "").replace("_", "-").strip()
    if not text:
        return ""
    return " ".join(part.capitalize() for part in text.split("-") if part)


def platform_display(plat: Any) -> str:
    """Human label for a space platform; never a raw enum or 'in transit'."""
    if not isinstance(plat, dict):
        return "—"
    loc = str(plat.get("location") or "").strip()
    origin = str(plat.get("origin") or "").strip()
    dest = str(plat.get("destination") or "").strip()
    state = str(plat.get("state") or "").strip()
    label = str(plat.get("label") or "").strip()
    in_transit = bool(plat.get("in_transit")) or state in {"2", "on_the_path"}

    def _clean(name: str) -> str:
        text = str(name or "").strip()
        if not text or text.isdigit():
            return ""
        lower = text.lower()
        if lower.startswith("in-transit:"):
            text = text.split(":", 1)[1]
        elif lower in {"in-transit", "in transit"}:
            return ""
        return _pretty_proto(text)

    def _arrow(left: str, right: str) -> str:
        if left and right and left.lower() != right.lower():
            return f"{left} -> {right}"
        return right or left

    label_origin = ""
    label_dest = ""
    if "->" in label or "→" in label:
        parts = [p.strip() for p in label.replace("→", "->").split("->", 1)]
        if len(parts) == 2:
            label_origin, label_dest = _clean(parts[0]), _clean(parts[1])
    elif label.lower().startswith("in-transit:"):
        label_dest = _clean(label)

    if in_transit:
        left = _clean(origin) or label_origin
        right = _clean(dest) or label_dest
        return _arrow(left, right) or "—"

    if loc and not loc.isdigit():
        return _pretty_proto(loc)
    mapped = _PLATFORM_STATE_LABELS.get(state)
    if mapped and mapped != "In transit":
        return mapped
    cleaned_label = _clean(label)
    if cleaned_label:
        if label_origin or label_dest:
            return _arrow(label_origin, label_dest)
        return cleaned_label
    return "—"


def reshape_stats(raw: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Turn the mod JSON into the overlay snapshot slice."""
    cfg = config or {}
    precision = str(cfg.get("ProductionPrecision") or "one_minute").strip()
    if precision not in _PRECISION_KEYS:
        precision = "one_minute"
    top_n = max(1, _as_int(cfg.get("ProductionTopN"), 12))
    item_names = parse_item_list(cfg.get("ProductionItems"))
    production_block = raw.get("production") if isinstance(raw.get("production"), dict) else {}
    items = _filter_flow_rows(
        production_block.get("items") if isinstance(production_block, dict) else [],
        precision=precision,
        names=item_names,
        top_n=top_n,
        rate_key="per_min",
    )
    consumption = _filter_flow_rows(
        production_block.get("items") if isinstance(production_block, dict) else [],
        precision=precision,
        names=item_names,
        top_n=top_n,
        rate_key="consumed_per_min",
    )
    fluids = _filter_flow_rows(
        production_block.get("fluids") if isinstance(production_block, dict) else [],
        precision=precision,
        names=parse_item_list(cfg.get("FluidItems")),
        top_n=top_n,
        rate_key="per_min",
    )
    power = raw.get("power") if isinstance(raw.get("power"), dict) else {}
    science = raw.get("science") if isinstance(raw.get("science"), dict) else {}
    planets = raw.get("planets") if isinstance(raw.get("planets"), dict) else {}
    misc = raw.get("misc") if isinstance(raw.get("misc"), dict) else {}
    ticks = raw.get("ticks_played") if raw.get("ticks_played") is not None else raw.get("tick")
    playtime = format_playtime_text(ticks) if ticks is not None else (
        str(raw.get("playtime_text") or "--:--:--")
    )
    platforms_out: List[Dict[str, Any]] = []
    for plat in planets.get("platforms") or []:
        if not isinstance(plat, dict):
            continue
        row = dict(plat)
        row["display"] = platform_display(plat)
        platforms_out.append(row)
    packs_out: List[Dict[str, Any]] = []
    for pack in science.get("packs") or []:
        if not isinstance(pack, dict):
            continue
        item = dict(pack)
        arr = _series_values(pack.get("series"), precision)
        if arr:
            item["series"] = arr
        else:
            item.pop("series", None)
        packs_out.append(item)
    spm_series = _series_values(science.get("series"), precision)
    science_out: Dict[str, Any] = {
        "spm": float(science.get("spm") or 0),
        "packs": packs_out,
        "research": science.get("research"),
        "queue_length": _as_int(science.get("queue_length"), 0),
    }
    if spm_series:
        science_out["series"] = spm_series
    by_surface: Dict[str, Any] = {}
    raw_surfaces = raw.get("by_surface") if isinstance(raw.get("by_surface"), dict) else {}
    for name, block in raw_surfaces.items():
        key = str(name or "").strip()
        if not key or not isinstance(block, dict):
            continue
        prod_rows = block.get("items")
        if prod_rows is None and isinstance(block.get("production"), dict):
            prod_rows = block["production"].get("items")
        fluid_rows = block.get("fluids")
        if fluid_rows is None and isinstance(block.get("production"), dict):
            fluid_rows = block["production"].get("fluids")
        surf_items = _filter_flow_rows(
            prod_rows,
            precision=precision,
            names=item_names,
            top_n=top_n,
            rate_key="per_min",
        )
        surf_cons = _filter_flow_rows(
            prod_rows,
            precision=precision,
            names=item_names,
            top_n=top_n,
            rate_key="consumed_per_min",
        )
        surf_fluids = _filter_flow_rows(
            fluid_rows,
            precision=precision,
            names=parse_item_list(cfg.get("FluidItems")),
            top_n=top_n,
            rate_key="per_min",
        )
        by_surface[key] = {
            "power": _reshape_power_block(block.get("power"), precision),
            "production": {"precision": precision, "items": surf_items, "fluids": surf_fluids},
            "consumption": {"items": surf_cons},
        }
    return {
        "hook": HOOK_ID,
        "tick": raw.get("tick"),
        "ticks_played": raw.get("ticks_played"),
        "game_version": raw.get("game_version"),
        "mod_version": raw.get("mod_version"),
        "playtime_text": playtime,
        "speed": raw.get("speed") or 1,
        "surface": raw.get("surface"),
        "space_age": bool(raw.get("space_age")),
        "precision": precision,
        "power": _reshape_power_block(power, precision),
        "production": {
            "precision": precision,
            "items": items,
            "fluids": fluids,
        },
        "consumption": {"items": consumption},
        "by_surface": by_surface,
        "science": science_out,
        "planets": {
            "current": planets.get("current"),
            "visited": list(planets.get("visited") or []),
            "platforms": platforms_out,
        },
        "misc": {
            "rockets_launched": _as_int(misc.get("rockets_launched"), 0),
            "evolution": float(misc.get("evolution") or 0),
            "pollution": float(misc.get("pollution") or 0),
            "kills": _as_int(misc.get("kills"), 0),
        },
    }


class FactorioGameHook:
    """Poll the companion mod's JSON export and reshape it for the overlay."""

    hook_id = HOOK_ID
    ui = HookUiMetadata(
        hook_id=HOOK_ID,
        title="Factorio",
        supported_platforms=frozenset({"windows", "darwin", "linux"}),
    )
    process_names = ("factorio.exe", "factorio")

    def __init__(self) -> None:
        self._enqueue_write: Any = None
        self._cfg: Dict[str, Any] = {}
        self._data_dir: Optional[str] = None
        self._last_mtime: Optional[float] = None
        self._last_raw: Optional[Dict[str, Any]] = None
        self._last_snapshot: Optional[Dict[str, Any]] = None
        self._reload_config()

    def set_write_enqueue(self, enqueue: Any) -> None:
        self._enqueue_write = enqueue

    def is_platform_supported(self) -> bool:
        return runtime_os_key() in self.ui.supported_platforms

    def is_process_running(self) -> bool:
        return find_factorio_pid() is not None

    def _reload_config(self) -> None:
        try:
            from ..template_config_parser import get_shared_parser

            cfg = get_shared_parser().load_config("factorio", copy_result=False)
            self._cfg = flat_template_config(cfg)
        except Exception as e:
            logger.debug("factorio config load: %s", e)
            self._cfg = {}
        override = str(self._cfg.get("FactorioDataDir") or "").strip()
        exe = process_exe_path()
        self._data_dir = resolve_data_dir(override or None, exe_path=exe)

    def on_config_reloaded(self) -> None:
        self._reload_config()

    def handle_command(self, action: str, data: Dict[str, Any]) -> None:
        if action == "resync":
            return

    def execute_operation(
        self, op: str, kwargs: Dict[str, Any]
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, str]]]:
        return False, "read-only hook", None

    def close(self) -> None:
        self._last_mtime = None

    def on_attached(self) -> None:
        return

    def on_detached(self) -> None:
        return

    def drain_timed_jobs(self, enqueue_write: Any) -> None:
        return

    def idle_snapshot(self, *, disabled: bool = False) -> Dict[str, Any]:
        base: Dict[str, Any] = {
            "hook": HOOK_ID,
            "attached": False,
            "paused": False,
            "error": None,
            "playtime_text": "--:--:--",
            "power": {},
            "production": {"items": [], "fluids": []},
            "consumption": {"items": []},
            "science": {"spm": 0, "packs": [], "research": None, "queue_length": 0},
            "planets": {"current": None, "visited": [], "platforms": []},
            "by_surface": {},
            "misc": {
                "rockets_launched": 0,
                "evolution": 0,
                "pollution": 0,
                "kills": 0,
            },
        }
        if disabled:
            base["disabled"] = True
            base["debug"] = {"stage": "factorio_disabled_in_settings"}
        elif not self.is_platform_supported():
            base["error"] = "Factorio hook is not supported on this OS"
            base["debug"] = {"stage": "unsupported_os", "platform": sys.platform}
        else:
            base["debug"] = {"stage": "process_not_running"}
        return base

    def _staleness_sec(self) -> float:
        return max(0.5, _as_float(self._cfg.get("StalenessSeconds"), _DEFAULT_STALENESS_SEC))

    def _read_stats_file(self, path: str) -> Optional[Dict[str, Any]]:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        if self._last_mtime is not None and mtime == self._last_mtime and self._last_raw:
            return self._last_raw
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("factorio stats parse: %s", e)
            return self._last_raw
        if not isinstance(raw, dict):
            return self._last_raw
        self._last_mtime = mtime
        self._last_raw = raw
        return raw

    @classmethod
    def ui_actions(cls) -> List[Dict[str, Any]]:
        from .factorio_mod_installer import detect_state, install_mod, version_label

        state = detect_state()
        detail, tone = version_label()
        if state == "current":
            label = "Mod installed"
            tooltip = "mycelian-stats is installed and up to date. Restart Factorio if it is already running."
            enabled = False
        elif state == "outdated":
            label = "Update mod"
            tooltip = "A newer mycelian-stats mod is bundled with Mycelian. Factorio must be restarted after updating."
            enabled = True
        else:
            label = "Install mod"
            tooltip = "Copy mycelian-stats into the Factorio mods folder and enable it. Restart Factorio afterwards."
            enabled = True

        def _run() -> Tuple[bool, str]:
            ok, message = install_mod()
            return ok, message

        return [
            {
                "id": "install_mod",
                "label": label,
                "tooltip": tooltip,
                "enabled": enabled,
                "detail": detail,
                "detail_tone": tone,
                "run": _run,
            }
        ]

    def tick(self) -> Dict[str, Any]:
        if not self.is_process_running():
            return self.idle_snapshot(disabled=False)

        if not self._data_dir:
            self._reload_config()
        data_dir = self._data_dir
        if not data_dir:
            snap = self.idle_snapshot(disabled=False)
            snap["error"] = "Factorio user-data folder not found"
            snap["debug"] = {"stage": "no_data_dir"}
            return snap

        path = stats_json_path(data_dir)
        exists = os.path.isfile(path)
        age_sec: Optional[float] = None
        if exists:
            try:
                age_sec = max(0.0, time.time() - os.path.getmtime(path))
            except OSError:
                age_sec = None

        raw = self._read_stats_file(path) if exists else self._last_raw
        has_last = raw is not None
        stage = classify_file_state(
            exists=exists or has_last,
            age_sec=age_sec,
            staleness_sec=self._staleness_sec(),
            has_last=has_last,
        )
        if stage == "waiting_for_mod":
            snap = self.idle_snapshot(disabled=False)
            snap["error"] = _MOD_MISSING_ERROR
            snap["debug"] = {
                "stage": "waiting_for_mod",
                "data_dir": data_dir,
                "stats_path": path,
            }
            return snap

        if not isinstance(raw, dict):
            snap = self.idle_snapshot(disabled=False)
            snap["error"] = _MOD_MISSING_ERROR
            snap["debug"] = {"stage": "waiting_for_mod"}
            return snap

        if raw.get("error"):
            snap = reshape_stats(raw, self._cfg)
            snap["attached"] = True
            snap["paused"] = stage == "paused"
            snap["error"] = str(raw.get("error"))
            snap["debug"] = {"stage": stage, "data_dir": data_dir}
            self._last_snapshot = snap
            return snap

        snap = reshape_stats(raw, self._cfg)
        snap["attached"] = True
        snap["paused"] = stage == "paused"
        snap["error"] = None
        snap["disabled"] = False
        snap["debug"] = {
            "stage": stage,
            "data_dir": data_dir,
            "stats_path": path,
            "age_sec": age_sec,
        }
        self._last_snapshot = snap
        return snap

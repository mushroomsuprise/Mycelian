# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/game_hooks/factorio_hook.py (data-dir discovery, PID cache, reshape, hook tick)."""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.game_hooks import factorio_hook as fh  # noqa: E402


# --------------------------------------------------------------------------- helpers


class _NoSuch(Exception):
    pass


class _Denied(Exception):
    pass


class FakeProc:
    def __init__(self, pid=11, name="factorio", exe="/games/factorio", running=True, raise_on=None):
        self.pid = pid
        self._name, self._exe, self._running, self._raise = name, exe, running, raise_on

    def is_running(self):
        if self._raise:
            raise self._raise
        return self._running

    def name(self):
        return self._name

    def exe(self):
        return self._exe


class _InfoProc:
    def __init__(self, info):
        self.info = info


class _BadInfo(dict):
    def __init__(self, exc):
        super().__init__()
        self.exc = exc

    def get(self, *a, **k):
        raise self.exc


def fake_psutil(procs=(), process_factory=None):
    def Process(pid):
        if process_factory is None:
            return FakeProc(pid=pid)
        return process_factory(pid)

    return types.SimpleNamespace(
        process_iter=lambda attrs=None: iter(list(procs)),
        Process=Process,
        NoSuchProcess=_NoSuch,
        AccessDenied=_Denied,
    )


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(fh, "_cached_factorio_proc", None)


def _mk_data_dir(path: Path, marker="mods") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / marker).mkdir(exist_ok=True)
    return path


# --------------------------------------------------------------------------- default dirs & log parsing


def test_default_data_dirs_windows_appdata(monkeypatch):
    monkeypatch.setenv("APPDATA", "/x/Roaming")
    assert fh.default_data_dirs("win32") == [os.path.join("/x/Roaming", "Factorio")]
    monkeypatch.delenv("APPDATA")
    assert fh.default_data_dirs("win32")[0].endswith(os.path.join("AppData", "Roaming", "Factorio"))


def test_default_data_dirs_darwin():
    assert fh.default_data_dirs("darwin")[0].endswith(os.path.join("Library", "Application Support", "factorio"))


def test_default_data_dirs_uses_sys_platform(monkeypatch):
    monkeypatch.setattr(fh.sys, "platform", "linux")
    assert fh.default_data_dirs()[0].endswith(".factorio")


def test_parse_paths():
    log = '0.1 Write data path: "/u/f" [100/200MB]\n'
    assert fh.parse_write_data_path(log) == "/u/f"
    assert fh.parse_write_data_path('Write data path: ""\nfoo') is None
    assert fh.parse_write_data_path(None) is None
    assert fh.parse_read_data_path("x\n  Read data path: /g/data  \n") == "/g/data"
    assert fh.parse_read_data_path('Read data path: ""') is None
    assert fh.parse_read_data_path("") is None


def test_looks_like_data_dir(tmp_path):
    assert not fh._looks_like_factorio_data_dir("")
    assert not fh._looks_like_factorio_data_dir(str(tmp_path / "missing"))
    assert not fh._looks_like_factorio_data_dir(str(tmp_path))
    (tmp_path / "player-data.json").write_text("{}")
    assert fh._looks_like_factorio_data_dir(str(tmp_path))


def test_portable_data_dir_from_exe(tmp_path):
    assert fh.portable_data_dir_from_exe(None) is None
    exe_dir = tmp_path / "game" / "bin" / "x64"
    exe_dir.mkdir(parents=True)
    exe = str(exe_dir / "factorio.exe")
    assert fh.portable_data_dir_from_exe(exe) is None
    _mk_data_dir(tmp_path / "game", "config")
    assert fh.portable_data_dir_from_exe(exe) == str(tmp_path / "game")
    _mk_data_dir(exe_dir / "factorio", "saves")
    assert fh.portable_data_dir_from_exe(exe) == str(exe_dir / "factorio")


@pytest.mark.parametrize(
    "name,exe,expected",
    [("factorio", "", True), ("", "/a/factorio/bin", True), ("factorio-crash", "", False),
     ("factorio", "/crash/x", False), ("python", "/bin/python", False)],
)
def test_is_fallback_match(name, exe, expected):
    assert fh._is_fallback_match(name, exe) is expected


# --------------------------------------------------------------------------- PID cache


def test_cached_pid_none():
    assert fh._cached_factorio_pid() is None


def test_cached_pid_by_name(monkeypatch):
    monkeypatch.setattr(fh, "_cached_factorio_proc", (FakeProc(pid=5, name="Factorio.exe"), True))
    assert fh._cached_factorio_pid() == 5
    monkeypatch.setattr(fh, "_cached_factorio_proc", (FakeProc(pid=5, name="other"), True))
    assert fh._cached_factorio_pid() is None
    assert fh._cached_factorio_proc is None


def test_cached_pid_fallback(monkeypatch):
    monkeypatch.setattr(fh, "_cached_factorio_proc", (FakeProc(pid=6, name="x", exe="/f/factorio/bin"), False))
    assert fh._cached_factorio_pid() == 6
    monkeypatch.setattr(fh, "_cached_factorio_proc", (FakeProc(pid=6, name="x", exe=None), False))
    assert fh._cached_factorio_pid() is None


def test_cached_pid_not_running_or_error(monkeypatch):
    monkeypatch.setattr(fh, "_cached_factorio_proc", (FakeProc(running=False), True))
    assert fh._cached_factorio_pid() is None
    monkeypatch.setattr(fh, "_cached_factorio_proc", (FakeProc(raise_on=RuntimeError("x")), True))
    assert fh._cached_factorio_pid() is None
    assert fh._cached_factorio_proc is None


def test_remember_pid(monkeypatch):
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil())
    fh._remember_factorio_pid("12", False)
    proc, by_name = fh._cached_factorio_proc
    assert proc.pid == 12 and by_name is False

    def boom(pid):
        raise _NoSuch()

    monkeypatch.setitem(sys.modules, "psutil", fake_psutil(process_factory=boom))
    fh._remember_factorio_pid(12, True)
    assert fh._cached_factorio_proc is None


def test_find_pid_uses_cache(monkeypatch):
    monkeypatch.setattr(fh, "_cached_factorio_pid", lambda: 77)
    monkeypatch.setattr(fh, "find_process_pid", lambda n: pytest.fail("scanned"))
    assert fh.find_factorio_pid() == 77


def test_find_pid_by_name(monkeypatch):
    remembered = []
    monkeypatch.setattr(fh, "find_process_pid", lambda n: 9 if n == fh.FactorioGameHook.process_names else None)
    monkeypatch.setattr(fh, "_remember_factorio_pid", lambda pid, by: remembered.append((pid, by)))
    assert fh.find_factorio_pid() == 9
    assert remembered == [(9, True)]


def test_find_pid_fallback_scan(monkeypatch):
    remembered = []
    monkeypatch.setattr(fh, "find_process_pid", lambda n: None)
    monkeypatch.setattr(fh, "_remember_factorio_pid", lambda pid, by: remembered.append((pid, by)))
    procs = [
        _InfoProc(_BadInfo(_NoSuch())),
        _InfoProc(_BadInfo(_Denied())),
        _InfoProc({"pid": 1, "name": "python", "exe": "/bin/python"}),
        _InfoProc({"pid": 2, "name": None, "exe": "/opt/Factorio/bin/x64/factorio-bin"}),
    ]
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil(procs))
    assert fh.find_factorio_pid() == 2
    assert remembered == [(2, False)]


def test_find_pid_none(monkeypatch):
    monkeypatch.setattr(fh, "find_process_pid", lambda n: None)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil([]))
    assert fh.find_factorio_pid() is None
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert fh.find_factorio_pid() is None


def test_process_exe_path(monkeypatch):
    monkeypatch.setattr(fh, "find_factorio_pid", lambda: None)
    assert fh.process_exe_path() is None
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil(process_factory=lambda pid: FakeProc(pid, exe="/g/factorio")))
    assert fh.process_exe_path(4) == "/g/factorio"
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil(process_factory=lambda pid: FakeProc(pid, exe="")))
    assert fh.process_exe_path(4) is None

    def boom(pid):
        raise _Denied()

    monkeypatch.setitem(sys.modules, "psutil", fake_psutil(process_factory=boom))
    assert fh.process_exe_path(4) is None


# --------------------------------------------------------------------------- resolve_data_dir


@pytest.fixture
def no_defaults(monkeypatch, tmp_path):
    """Point the OS default dir into tmp_path."""
    default = tmp_path / "default"
    monkeypatch.setattr(fh, "default_data_dirs", lambda platform=None: [str(default)])
    return default


def test_resolve_override(tmp_path, no_defaults):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert fh.resolve_data_dir(str(plain) + "  ") == str(plain)
    # missing override falls back to default (first unique)
    assert fh.resolve_data_dir(str(tmp_path / "nope")) == str(no_defaults)


def test_resolve_from_log(tmp_path, no_defaults):
    no_defaults.mkdir()
    real = tmp_path / "real"
    real.mkdir()
    (no_defaults / "factorio-current.log").write_text(f"Write data path: {real} [1/2MB]\n")
    assert fh.resolve_data_dir() == str(real)
    # read_log disabled -> default dir that looks like data dir
    assert fh.resolve_data_dir(read_log=False) == str(no_defaults)


def test_resolve_log_points_nowhere(tmp_path, no_defaults):
    no_defaults.mkdir()
    (no_defaults / "factorio-current.log").write_text("Write data path: /does/not/exist\n")
    assert fh.resolve_data_dir() == str(no_defaults)


def test_resolve_log_unreadable(tmp_path, no_defaults, monkeypatch):
    no_defaults.mkdir()
    (no_defaults / "factorio-current.log").write_text("x")
    real_open = open

    def fake_open(path, *a, **k):
        if str(path).endswith("factorio-current.log"):
            raise OSError("locked")
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", fake_open)
    assert fh.resolve_data_dir() == str(no_defaults)


def test_resolve_portable_and_dedupe(tmp_path, monkeypatch):
    exe_dir = tmp_path / "g" / "bin" / "x64"
    exe_dir.mkdir(parents=True)
    _mk_data_dir(tmp_path / "g", "saves")
    monkeypatch.setattr(fh, "default_data_dirs", lambda platform=None: [str(tmp_path / "g"), str(tmp_path / "g")])
    assert fh.resolve_data_dir(exe_path=str(exe_dir / "factorio")) == str(tmp_path / "g")
    monkeypatch.setattr(fh, "default_data_dirs", lambda platform=None: [str(tmp_path / "none")])
    assert fh.resolve_data_dir(exe_path=str(exe_dir / "factorio")) == str(tmp_path / "g")


def test_resolve_no_candidates(monkeypatch):
    monkeypatch.setattr(fh, "default_data_dirs", lambda platform=None: [])
    assert fh.resolve_data_dir() is None


def test_stats_json_path():
    assert fh.stats_json_path("/d") == os.path.join("/d", "script-output", "mycelian", "stats.json")


# --------------------------------------------------------------------------- small parsers


def test_flat_template_config():
    assert fh.flat_template_config(None) == {}
    cfg = {"elements": [{"id": "A", "value": 1}, {"id": "B"}, "x", {"value": 3}, {"id": 5, "value": "v"}]}
    assert fh.flat_template_config(cfg) == {"A": 1, "5": "v"}
    assert fh.flat_template_config({"elements": None}) == {}


@pytest.mark.parametrize(
    "value,default,expected",
    [(None, True, True), (True, False, True), (0, True, False), (2.5, False, True), ("YES", False, True),
     (" off ", True, False), ("", True, False), ("maybe", True, True), ("maybe", False, False)],
)
def test_as_bool(value, default, expected):
    assert fh._as_bool(value, default) is expected


def test_as_int_and_float():
    assert fh._as_int("3.9", 0) == 3
    assert fh._as_int(None, 7) == 7
    assert fh._as_int("x", 7) == 7
    assert fh._as_float("2.5", 0.0) == 2.5
    assert fh._as_float(None, 1.5) == 1.5
    assert fh._as_float("x", 1.5) == 1.5


def test_parse_item_list():
    assert fh.parse_item_list(None) == []
    assert fh.parse_item_list(["a ", "", " b"]) == ["a", "b"]
    assert fh.parse_item_list(("x",)) == ["x"]
    assert fh.parse_item_list("a, b\n\nc") == ["a", "b", "c"]


def test_downsample_indices():
    assert fh.downsample_sample_indices(3, 9) == [7, 4, 1]
    assert fh.downsample_sample_indices(0, 0) == [1]
    assert fh.downsample_sample_indices(5, 2) == [5, 4, 3, 2, 1]
    assert len(fh.downsample_sample_indices()) == fh.SERIES_POINTS


def test_as_float_list():
    assert fh._as_float_list(None) is None
    assert fh._as_float_list([]) is None
    assert fh._as_float_list([1, None, "2", "x", [1]]) == [1.0, 0.0, 2.0, 0.0, 0.0]


# --------------------------------------------------------------------------- series picking


def test_series_values_list_and_bad():
    assert fh._series_values([1, 2], "one_minute") == [1.0, 2.0]
    assert fh._series_values("x", "one_minute") is None


def test_series_values_flat_inner_key():
    assert fh._series_values({"produced": [1]}, "one_minute", "produced") == [1.0]
    # flat but inner missing -> falls through to block lookup (none) then series.get(inner)
    assert fh._series_values({"other": [1]}, "one_minute", "produced") is None


def test_series_values_nested_inner_key():
    s = {"one_minute": {"produced": [1], "consumed": [2]}, "one_hour": [9]}
    assert fh._series_values(s, "one_minute", "consumed") == [2.0]
    assert fh._series_values(s, "one_hour", "consumed") == [9.0]
    # precision missing -> one_minute fallback
    assert fh._series_values(s, "ten_hours", "produced") == [1.0]
    # nested precision present but block is neither dict nor list
    assert fh._series_values({"one_minute": 5, "consumed": [4]}, "one_minute", "consumed") == [4.0]


def test_series_values_no_inner_key():
    assert fh._series_values({"one_minute": [3]}, "one_minute") == [3.0]
    assert fh._series_values({"one_minute": {"generation": [4]}}, "one_minute") == [4.0]
    assert fh._series_values({"one_minute": {"spm": [5]}}, "one_minute") == [5.0]
    assert fh._series_values({"one_minute": {"consumption": [6]}}, "one_minute") == [6.0]
    assert fh._series_values({"produced": [7]}, "one_minute") == [7.0]
    assert fh._series_values({"generation": [8]}, "five_seconds") == [8.0]
    assert fh._series_values({}, "one_minute") is None


def test_reshape_power_rows_and_block():
    rows = [{"name": "steam", "series": [1, 2]}, {"name": "solar", "series": None}, "junk"]
    out = fh._reshape_power_rows(rows, "one_minute")
    assert out == [{"name": "steam", "series": [1.0, 2.0]}, {"name": "solar"}]
    assert fh._reshape_power_rows(None, "one_minute") == []

    block = fh._reshape_power_block(None, "one_minute")
    assert block["generation_w"] == 0.0 and block["series"] == {} and block["producers"] == []
    block = fh._reshape_power_block(
        {"network_id": 3, "surface": "nauvis", "generation_w": "5", "capacity_w": 6, "consumption_w": 7,
         "satisfaction": 1, "accumulator_j": 2, "accumulator_capacity_j": 3,
         "series": {"generation": [1], "consumption": [2]}, "producers": [{"n": 1}], "consumers": []},
        "one_minute",
    )
    assert block["network_id"] == 3 and block["generation_w"] == 5.0
    assert block["series"] == {"generation": [1.0], "consumption": [2.0]}
    assert fh._reshape_power_block({"series": "bad"}, "x")["series"] == {}


# --------------------------------------------------------------------------- planets & flow rows


def test_cycled_planet_name():
    kw = dict(now_ms=0, period_ms=1000)
    assert fh.cycled_planet_name(["Nauvis", "Vulcanus"], "platform:1", cycle=False, **kw) == "Nauvis"
    assert fh.cycled_planet_name([], "platform:1", cycle=False, **kw) == "platform:1"
    assert fh.cycled_planet_name(["A"], "", cycle=False, **kw) == "A"
    assert fh.cycled_planet_name([], None, cycle=True, **kw) == ""
    assert fh.cycled_planet_name(["A", "", None, "B"], "X", cycle=True, now_ms=1500, period_ms=1000) == "B"
    assert fh.cycled_planet_name(["A", "B"], "X", cycle=True, now_ms=-5, period_ms=0) == "A"


def test_rate_for():
    row = {"rates": {"one_hour": {"produced": "3"}, "one_minute": {"produced": 1}}}
    assert fh._rate_for(row, "one_hour", "produced") == 3.0
    assert fh._rate_for(row, "ten_hours", "produced") == 1.0
    assert fh._rate_for({"rates": {"one_minute": {"produced": "x"}}}, "one_minute", "produced") == 0.0
    # bucket not a dict -> flat fallback
    assert fh._rate_for({"rates": {"one_minute": [1]}, "per_min": 4}, "one_minute", "produced") == 4.0
    assert fh._rate_for({"consumed_per_min": "2"}, "one_minute", "consumed") == 2.0
    assert fh._rate_for({"per_min": "bad"}, "one_minute", "produced") == 0.0


def test_normalize_flow_row():
    assert fh._normalize_flow_row("x", "one_minute") is None
    assert fh._normalize_flow_row({"name": " "}, "one_minute") is None
    r = fh._normalize_flow_row({"name": "iron", "per_min": 5, "total": 9, "consumed": "x", "count": "2"}, "one_minute")
    assert r["per_min"] == 5.0 and r["total"] == 9.0 and r["total_consumed"] == 0.0 and r["count"] == 2
    r = fh._normalize_flow_row({"name": "iron", "produced": "bad", "consumed": 4}, "one_minute")
    assert r["total"] == 0.0 and r["total_consumed"] == 4.0
    assert r["_series_produced"] is None


def test_filter_and_finalize_rows():
    rows = [
        {"name": "a", "per_min": 1, "consumed_per_min": 9, "series": {"produced": [1], "consumed": [2]}},
        {"name": "B", "per_min": 5, "consumed_per_min": 1},
        {"name": ""},
    ]
    top = fh._filter_flow_rows(rows, precision="one_minute", names=[], top_n=0, rate_key="per_min")
    assert [r["name"] for r in top] == ["B", "a"]
    assert top[1]["series"] == [1.0] and "series" not in top[0]
    cons = fh._filter_flow_rows(rows, precision="one_minute", names=[], top_n=1, rate_key="consumed_per_min")
    assert [r["name"] for r in cons] == ["a"] and cons[0]["series"] == [2.0]
    picked = fh._filter_flow_rows(rows, precision="one_minute", names=["b", "A", "zz"], top_n=1, rate_key="per_min")
    assert [r["name"] for r in picked] == ["B", "a"]
    assert fh._filter_flow_rows(None, precision="x", names=[], top_n=3, rate_key="per_min") == []
    assert fh._finalize_flow_row({"series": [1], "_series_produced": None}, "per_min") == {}


@pytest.mark.parametrize(
    "kw,expected",
    [
        (dict(exists=False, age_sec=None, staleness_sec=5, has_last=False), "waiting_for_mod"),
        (dict(exists=True, age_sec=None, staleness_sec=5, has_last=True), "paused"),
        (dict(exists=True, age_sec=None, staleness_sec=5, has_last=False), "waiting_for_mod"),
        (dict(exists=True, age_sec=10, staleness_sec=5, has_last=False), "waiting_for_mod"),
        (dict(exists=True, age_sec=0.4, staleness_sec=0, has_last=False), "attached"),
        (dict(exists=True, age_sec=0.6, staleness_sec=0, has_last=True), "paused"),
    ],
)
def test_classify_file_state(kw, expected):
    assert fh.classify_file_state(**kw) == expected


def test_si_format_more():
    assert fh.si_format("x") == "0"
    assert fh.si_format(-1500, "W") == "-1.5kW"
    assert fh.si_format(150e3, "") == "150k"
    assert fh.si_format(1.25e18, "J", precision=2) == "1.25EJ"
    assert fh.si_format(2e15) == "2P"
    assert fh.si_format(3.5e12) == "3.5T"


def test_format_playtime_text():
    assert fh.format_playtime_text("x") == "--:--:--"
    assert fh.format_playtime_text(None) == "--:--:--"
    assert fh.format_playtime_text(-60) == "0:00:00"
    assert fh.format_playtime_text(60 * 3661) == "1:01:01"
    assert fh.format_playtime_text(60 * (86400 + 61)) == "1d 0:01:01"


def test_pretty_proto():
    assert fh._pretty_proto("") == ""
    assert fh._pretty_proto(None) == ""
    assert fh._pretty_proto("space_platform--hub") == "Space Platform Hub"


@pytest.mark.parametrize(
    "plat,expected",
    [
        (None, "—"),
        ({"in_transit": True, "origin": "nauvis", "destination": "vulcanus"}, "Nauvis -> Vulcanus"),
        ({"state": "2", "origin": "nauvis", "destination": "nauvis"}, "Nauvis"),
        ({"state": "on_the_path", "label": "fulgora -> gleba"}, "Fulgora -> Gleba"),
        ({"state": "on_the_path", "label": "in-transit:aquilo"}, "Aquilo"),
        ({"in_transit": True, "origin": "12", "destination": "in transit"}, "—"),
        ({"location": "gleba"}, "Gleba"),
        ({"location": "3", "state": "6"}, "Docked"),
        ({"state": "on_the_path_x", "label": "my-platform"}, "My Platform"),
        ({"state": "x", "label": "nauvis→fulgora"}, "Nauvis -> Fulgora"),
        ({"state": "x", "label": "in-transit"}, "—"),
        ({"state": "x", "label": "a -> "}, "A"),
        ({}, "—"),
    ],
)
def test_platform_display(plat, expected):
    assert fh.platform_display(plat) == expected


# --------------------------------------------------------------------------- reshape_stats


def test_reshape_stats_full():
    raw = {
        "tick": 100, "ticks_played": None, "game_version": "2.0", "mod_version": "1.0.4", "surface": "nauvis",
        "space_age": 1, "speed": 0,
        "production": {"items": [{"name": "iron", "per_min": 3}], "fluids": [{"name": "water", "per_min": 9}]},
        "power": {"generation_w": 1},
        "science": {"spm": "2", "packs": [{"name": "red", "series": [1]}, {"name": "green"}, "bad"],
                    "research": "auto", "queue_length": "3", "series": {"one_minute": [4]}},
        "planets": {"current": "nauvis", "visited": ["nauvis"], "platforms": [{"location": "gleba"}, "bad"]},
        "misc": {"rockets_launched": "2", "evolution": 0.5, "pollution": None, "kills": 4},
        "by_surface": {
            "": {}, "bad": 3,
            "nauvis": {"items": [{"name": "iron", "per_min": 3}], "fluids": [], "power": {}},
            "gleba": {"production": {"items": [{"name": "fruit", "per_min": 1}], "fluids": [{"name": "w"}]}},
        },
    }
    cfg = {"ProductionPrecision": "bogus", "ProductionTopN": "x", "FluidItems": ""}
    snap = fh.reshape_stats(raw, cfg)
    assert snap["hook"] == "factorio" and snap["precision"] == "one_minute"
    assert snap["playtime_text"] == "0:00:01"
    assert snap["speed"] == 1 and snap["space_age"] is True
    assert [i["name"] for i in snap["production"]["items"]] == ["iron"]
    assert snap["production"]["fluids"][0]["name"] == "water"
    assert snap["science"]["series"] == [4.0] and snap["science"]["queue_length"] == 3
    assert [p.get("series") for p in snap["science"]["packs"]] == [[1.0], None]
    assert snap["planets"]["platforms"] == [{"location": "gleba", "display": "Gleba"}]
    assert snap["misc"] == {"rockets_launched": 2, "evolution": 0.5, "pollution": 0.0, "kills": 4}
    assert set(snap["by_surface"]) == {"nauvis", "gleba"}
    assert snap["by_surface"]["gleba"]["production"]["items"][0]["name"] == "fruit"
    assert snap["by_surface"]["gleba"]["production"]["fluids"][0]["name"] == "w"


def test_reshape_stats_empty_defaults():
    snap = fh.reshape_stats({"playtime_text": "1:00:00", "production": "bad"})
    assert snap["playtime_text"] == "1:00:00"
    assert snap["production"]["items"] == [] and snap["science"]["spm"] == 0.0
    assert "series" not in snap["science"]
    assert snap["planets"] == {"current": None, "visited": [], "platforms": []}
    assert fh.reshape_stats({})["playtime_text"] == "--:--:--"
    assert fh.reshape_stats({"ticks_played": 120, "tick": 5})["playtime_text"] == "0:00:02"


# --------------------------------------------------------------------------- hook class


@pytest.fixture
def hook_env(monkeypatch, tmp_path):
    """Isolate FactorioGameHook from config files, process scans and the real data dir."""
    cfg = {"elements": []}
    calls = {"load": 0}

    class Parser:
        def load_config(self, name, copy_result=True):
            calls["load"] += 1
            assert name == "factorio"
            if isinstance(cfg, Exception):
                raise cfg
            return cfg

    import modules.template_config_parser as tcp

    monkeypatch.setattr(tcp, "get_shared_parser", lambda: Parser())
    monkeypatch.setattr(fh, "process_exe_path", lambda pid=None: None)
    data_dir = tmp_path / "fdata"
    state = {"dir": str(data_dir), "running": True}
    resolved = []

    def fake_resolve(override=None, **kw):
        resolved.append(override)
        return state["dir"]

    monkeypatch.setattr(fh, "resolve_data_dir", fake_resolve)
    monkeypatch.setattr(fh, "find_factorio_pid", lambda: 1 if state["running"] else None)
    return types.SimpleNamespace(cfg=cfg, calls=calls, state=state, resolved=resolved, dir=data_dir,
                                 set_cfg=lambda c: cfg.update(c))


def test_hook_init_reads_config_override(hook_env):
    hook_env.cfg["elements"] = [{"id": "FactorioDataDir", "value": "  /my/dir "}, {"id": "StalenessSeconds", "value": "0.1"}]
    h = fh.FactorioGameHook()
    assert h._cfg["FactorioDataDir"] == "  /my/dir "
    assert hook_env.resolved == ["/my/dir"]
    assert h._data_dir == hook_env.state["dir"]
    assert h._staleness_sec() == 0.5


def test_hook_config_failure(monkeypatch, hook_env):
    import modules.template_config_parser as tcp

    def boom():
        raise RuntimeError("no parser")

    monkeypatch.setattr(tcp, "get_shared_parser", boom)
    h = fh.FactorioGameHook()
    assert h._cfg == {} and hook_env.resolved == [None]
    assert h._staleness_sec() == fh._DEFAULT_STALENESS_SEC
    h.on_config_reloaded()
    assert hook_env.resolved == [None, None]


def test_hook_simple_methods(hook_env, monkeypatch):
    h = fh.FactorioGameHook()
    enq = object()
    h.set_write_enqueue(enq)
    assert h._enqueue_write is enq
    assert h.handle_command("resync", {}) is None
    assert h.handle_command("other", {}) is None
    assert h.execute_operation("x", {}) == (False, "read-only hook", None)
    h._last_mtime = 5
    h.close()
    assert h._last_mtime is None
    assert h.on_attached() is None and h.on_detached() is None and h.drain_timed_jobs(None) is None
    assert h.is_process_running() is True
    hook_env.state["running"] = False
    assert h.is_process_running() is False
    for plat, ok in (("win32", True), ("darwin", True), ("linux", True), ("freebsd", False)):
        monkeypatch.setattr(sys, "platform", plat)
        assert h.is_platform_supported() is ok


def test_idle_snapshot_variants(hook_env, monkeypatch):
    h = fh.FactorioGameHook()
    d = h.idle_snapshot(disabled=True)
    assert d["disabled"] is True and d["debug"]["stage"] == "factorio_disabled_in_settings"
    monkeypatch.setattr(h, "is_platform_supported", lambda: False)
    d = h.idle_snapshot()
    assert "not supported" in d["error"] and d["debug"]["stage"] == "unsupported_os"
    monkeypatch.setattr(h, "is_platform_supported", lambda: True)
    d = h.idle_snapshot()
    assert d["debug"] == {"stage": "process_not_running"} and d["attached"] is False


def _write_stats(data_dir: Path, payload) -> Path:
    path = Path(fh.stats_json_path(str(data_dir)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
    return path


def test_read_stats_file(hook_env):
    h = fh.FactorioGameHook()
    assert h._read_stats_file(str(hook_env.dir / "missing.json")) is None
    p = _write_stats(hook_env.dir, {"tick": 1})
    assert h._read_stats_file(str(p)) == {"tick": 1}
    first = h._last_raw
    assert h._read_stats_file(str(p)) is first  # same mtime -> cached object
    os.utime(p, (1, 1))
    p.write_text("{bad", encoding="utf-8")
    os.utime(p, (2, 2))
    assert h._read_stats_file(str(p)) is first  # parse error keeps last
    p.write_text("[1]", encoding="utf-8")
    os.utime(p, (3, 3))
    assert h._read_stats_file(str(p)) is first  # non-dict keeps last


def test_tick_not_running(hook_env):
    hook_env.state["running"] = False
    snap = fh.FactorioGameHook().tick()
    assert snap["debug"]["stage"] == "process_not_running"


def test_tick_no_data_dir_reloads(hook_env):
    hook_env.state["dir"] = None
    h = fh.FactorioGameHook()
    n = hook_env.calls["load"]
    snap = h.tick()
    assert hook_env.calls["load"] == n + 1
    assert snap["error"] == "Factorio user-data folder not found" and snap["debug"] == {"stage": "no_data_dir"}


def test_tick_waiting_for_mod(hook_env):
    snap = fh.FactorioGameHook().tick()
    assert snap["error"] == fh._MOD_MISSING_ERROR
    assert snap["debug"]["stage"] == "waiting_for_mod" and snap["debug"]["stats_path"].endswith("stats.json")


def test_tick_attached_and_paused(hook_env, monkeypatch):
    p = _write_stats(hook_env.dir, {"tick": 60, "production": {"items": []}})
    mtime = os.path.getmtime(p)
    monkeypatch.setattr(fh.time, "time", lambda: mtime + 1)
    h = fh.FactorioGameHook()
    snap = h.tick()
    assert snap["attached"] is True and snap["paused"] is False and snap["error"] is None
    assert snap["disabled"] is False and snap["debug"]["stage"] == "attached" and snap["debug"]["age_sec"] == 1
    assert h._last_snapshot is snap
    monkeypatch.setattr(fh.time, "time", lambda: mtime + 100)
    snap = h.tick()
    assert snap["paused"] is True and snap["debug"]["stage"] == "paused"
    # file disappears: last raw still shown as paused
    p.unlink()
    snap = h.tick()
    assert snap["paused"] is True and snap["attached"] is True and snap["debug"]["age_sec"] is None


def test_tick_mod_error_payload(hook_env, monkeypatch):
    p = _write_stats(hook_env.dir, {"error": "no player"})
    monkeypatch.setattr(fh.time, "time", lambda: os.path.getmtime(p))
    h = fh.FactorioGameHook()
    snap = h.tick()
    assert snap["error"] == "no player" and snap["attached"] is True
    assert snap["debug"] == {"stage": "attached", "data_dir": hook_env.state["dir"]}
    assert h._last_snapshot is snap


def test_tick_unparseable_file_no_last(hook_env, monkeypatch):
    p = _write_stats(hook_env.dir, "not json")
    monkeypatch.setattr(fh.time, "time", lambda: os.path.getmtime(p))
    snap = fh.FactorioGameHook().tick()
    assert snap["error"] == fh._MOD_MISSING_ERROR and snap["debug"]["stage"] == "waiting_for_mod"


def test_tick_raw_not_dict_after_classify(hook_env, monkeypatch):
    # Stage says attached (file exists, fresh) but the reader produced no dict.
    p = _write_stats(hook_env.dir, "{}")
    monkeypatch.setattr(fh.time, "time", lambda: os.path.getmtime(p))
    h = fh.FactorioGameHook()
    monkeypatch.setattr(h, "_read_stats_file", lambda path: "oops")
    snap = h.tick()
    assert snap["debug"] == {"stage": "waiting_for_mod"}


def test_tick_getmtime_error(hook_env, monkeypatch):
    p = _write_stats(hook_env.dir, {"tick": 1})
    h = fh.FactorioGameHook()
    h._last_raw = {"tick": 1}
    real = os.path.getmtime

    def bad(path):
        if str(path) == str(p):
            raise OSError("gone")
        return real(path)

    monkeypatch.setattr(fh.os.path, "getmtime", bad)
    snap = h.tick()
    # stat fails in both tick and the reader: the reader reports nothing, so the mod looks missing
    assert snap["error"] == fh._MOD_MISSING_ERROR and snap["debug"]["stage"] == "waiting_for_mod"


def test_ui_actions_states(monkeypatch):
    from modules.game_hooks import factorio_mod_installer as inst

    calls = []
    monkeypatch.setattr(inst, "install_mod", lambda: calls.append(1) or (True, "done"))
    for state, label, enabled in (("current", "Mod installed", False), ("outdated", "Update mod", True),
                                  ("not_installed", "Install mod", True)):
        monkeypatch.setattr(inst, "install_status", lambda s=state: (s, "detail", "tone"))
        (action,) = fh.FactorioGameHook.ui_actions()
        assert action["label"] == label and action["enabled"] is enabled
        assert action["id"] == "install_mod" and action["detail"] == "detail" and action["detail_tone"] == "tone"
        assert action["run"]() == (True, "done")
    assert len(calls) == 3

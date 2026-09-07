# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Pure-logic tests for the Factorio hook, installer, and snapshot reshape."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.game_hooks.factorio_hook import (  # noqa: E402
    classify_file_state,
    cycled_planet_name,
    default_data_dirs,
    downsample_sample_indices,
    parse_item_list,
    parse_write_data_path,
    platform_display,
    reshape_stats,
    si_format,
)
from modules.game_hooks.factorio_mod_installer import (  # noqa: E402
    detect_state,
    merge_mod_list,
    version_label,
)


class SiFormatTests(unittest.TestCase):
    def test_item_rate(self) -> None:
        self.assertEqual(si_format(1400, "/m"), "1.4k/m")
        self.assertEqual(si_format(888, "/m"), "888/m")

    def test_watts(self) -> None:
        self.assertEqual(si_format(525e6, "W"), "525MW")
        self.assertEqual(si_format(2.7e9, "W"), "2.7GW")

    def test_joules(self) -> None:
        self.assertEqual(si_format(12.2e9, "J"), "12.2GJ")

    def test_zero(self) -> None:
        self.assertEqual(si_format(0, "W"), "0W")


class DataDirTests(unittest.TestCase):
    def test_darwin_default(self) -> None:
        dirs = default_data_dirs("darwin")
        self.assertTrue(dirs[0].endswith("Library/Application Support/factorio"))

    def test_linux_default(self) -> None:
        dirs = default_data_dirs("linux")
        self.assertTrue(dirs[0].endswith(".factorio"))

    def test_windows_default(self) -> None:
        with patch.dict("os.environ", {"APPDATA": r"C:\Users\test\AppData\Roaming"}):
            dirs = default_data_dirs("win32")
        self.assertTrue(dirs[0].replace("\\", "/").endswith("Factorio"))

    def test_parse_write_data_path(self) -> None:
        log = (
            "   0.001 Write data path: /Users/steve/Library/Application Support/factorio "
            "[67556/233752MB]\n"
        )
        self.assertEqual(
            parse_write_data_path(log),
            "/Users/steve/Library/Application Support/factorio",
        )

    def test_parse_read_data_path(self) -> None:
        from modules.game_hooks.factorio_hook import parse_read_data_path

        log = (
            "   0.001 Read data path: /Apps/Factorio/factorio.app/Contents/data\n"
        )
        self.assertEqual(
            parse_read_data_path(log),
            "/Apps/Factorio/factorio.app/Contents/data",
        )


class FileStateTests(unittest.TestCase):
    def test_missing_without_cache(self) -> None:
        self.assertEqual(
            classify_file_state(
                exists=False, age_sec=None, staleness_sec=5, has_last=False
            ),
            "waiting_for_mod",
        )

    def test_stale_with_cache_is_paused(self) -> None:
        self.assertEqual(
            classify_file_state(
                exists=True, age_sec=30, staleness_sec=5, has_last=True
            ),
            "paused",
        )

    def test_fresh_is_attached(self) -> None:
        self.assertEqual(
            classify_file_state(
                exists=True, age_sec=0.4, staleness_sec=5, has_last=True
            ),
            "attached",
        )


class ReshapeTests(unittest.TestCase):
    def _raw(self) -> dict:
        return {
            "v": 1,
            "tick": 123,
            "playtime_text": "1:00:00",
            "surface": "nauvis",
            "space_age": True,
            "power": {
                "generation_w": 525e6,
                "capacity_w": 2.7e9,
                "consumption_w": 525e6,
                "satisfaction": 1,
                "producers": [{
                    "name": "solar-panel",
                    "count": 100,
                    "watts": 6e4,
                    "series": {"one_minute": [1, 2, 3], "five_seconds": [9, 8, 7]},
                }],
                "consumers": [],
                "series": {
                    "one_minute": {
                        "generation": [5, 6, 7],
                        "consumption": [8, 9, 10],
                    }
                },
            },
            "production": {
                "items": [
                    {
                        "name": "iron-plate",
                        "produced": 118000,
                        "consumed": 110000,
                        "rates": {
                            "one_minute": {"produced": 1400, "consumed": 1300},
                            "five_seconds": {"produced": 2000, "consumed": 100},
                        },
                        "series": {
                            "one_minute": {
                                "produced": [10, 20, 30],
                                "consumed": [1, 2, 3],
                            },
                            "five_seconds": {
                                "produced": [40, 50, 60],
                                "consumed": [7, 8, 9],
                            },
                        },
                    },
                    {
                        "name": "copper-plate",
                        "produced": 50,
                        "consumed": 9000,
                        "rates": {
                            "one_minute": {"produced": 10, "consumed": 888},
                            "five_seconds": {"produced": 5, "consumed": 900},
                        },
                    },
                    {
                        "name": "automation-science-pack",
                        "produced": 10,
                        "consumed": 10,
                        "rates": {
                            "one_minute": {"produced": 60, "consumed": 60},
                        },
                    },
                ],
                "fluids": [
                    {
                        "name": "water",
                        "produced": 1,
                        "consumed": 1,
                        "rates": {"one_minute": {"produced": 1200, "consumed": 100}},
                    }
                ],
            },
            "science": {
                "spm": 60,
                "packs": [{
                    "name": "automation-science-pack",
                    "per_min": 60,
                    "series": {"one_minute": [11, 12, 13]},
                }],
                "series": {"one_minute": [21, 22, 23]},
                "research": {"name": "steel-processing", "progress": 0.5},
                "queue_length": 2,
            },
            "planets": {"current": "nauvis", "visited": ["nauvis"], "platforms": []},
            "misc": {"rockets_launched": 3, "evolution": 0.2, "pollution": 10, "kills": 4},
        }

    def test_top_n_production_order(self) -> None:
        snap = reshape_stats(self._raw(), {"ProductionTopN": 1})
        self.assertEqual(len(snap["production"]["items"]), 1)
        self.assertEqual(snap["production"]["items"][0]["name"], "iron-plate")
        self.assertEqual(snap["production"]["items"][0]["per_min"], 1400)

    def test_explicit_item_list(self) -> None:
        snap = reshape_stats(
            self._raw(),
            {"ProductionItems": "copper-plate\niron-plate", "ProductionTopN": 1},
        )
        names = [r["name"] for r in snap["production"]["items"]]
        self.assertEqual(names, ["copper-plate", "iron-plate"])

    def test_precision_switch(self) -> None:
        snap = reshape_stats(self._raw(), {"ProductionPrecision": "five_seconds"})
        iron = snap["production"]["items"][0]
        self.assertEqual(iron["name"], "iron-plate")
        self.assertEqual(iron["per_min"], 2000)

    def test_consumption_sorts_by_consumed_rate(self) -> None:
        snap = reshape_stats(self._raw(), {"ProductionTopN": 1})
        self.assertEqual(snap["consumption"]["items"][0]["name"], "iron-plate")
        snap = reshape_stats(
            self._raw(),
            {"ProductionTopN": 1, "ProductionPrecision": "five_seconds"},
        )
        self.assertEqual(snap["consumption"]["items"][0]["name"], "copper-plate")

    def test_parse_item_list(self) -> None:
        self.assertEqual(parse_item_list("iron-plate, copper-plate"), ["iron-plate", "copper-plate"])
        self.assertEqual(parse_item_list(""), [])

    def test_series_passthrough(self) -> None:
        snap = reshape_stats(self._raw())
        iron = snap["production"]["items"][0]
        self.assertEqual(iron["series"], [10.0, 20.0, 30.0])
        consumed = next(
            r for r in snap["consumption"]["items"] if r["name"] == "iron-plate"
        )
        self.assertEqual(consumed["series"], [1.0, 2.0, 3.0])
        self.assertEqual(snap["power"]["series"]["generation"], [5.0, 6.0, 7.0])
        self.assertEqual(snap["power"]["producers"][0]["series"], [1.0, 2.0, 3.0])
        self.assertEqual(snap["science"]["series"], [21.0, 22.0, 23.0])
        self.assertEqual(snap["science"]["packs"][0]["series"], [11.0, 12.0, 13.0])
        five = reshape_stats(self._raw(), {"ProductionPrecision": "five_seconds"})
        self.assertEqual(five["production"]["items"][0]["series"], [40.0, 50.0, 60.0])

    def test_by_surface_reshape(self) -> None:
        raw = self._raw()
        raw["by_surface"] = {
            "nauvis": {
                "power": {"generation_w": 1, "producers": [], "consumers": []},
                "items": [
                    {
                        "name": "iron-plate",
                        "produced": 1,
                        "consumed": 1,
                        "rates": {"one_minute": {"produced": 9, "consumed": 4}},
                    }
                ],
            },
            "gleba": {
                "power": {"generation_w": 2, "producers": [], "consumers": []},
                "items": [
                    {
                        "name": "iron-plate",
                        "produced": 1,
                        "consumed": 1,
                        "rates": {"one_minute": {"produced": 3, "consumed": 8}},
                    }
                ],
                "fluids": [
                    {
                        "name": "water",
                        "produced": 1,
                        "consumed": 1,
                        "rates": {"one_minute": {"produced": 500, "consumed": 10}},
                    }
                ],
            },
        }
        snap = reshape_stats(raw)
        self.assertEqual(snap["by_surface"]["nauvis"]["production"]["items"][0]["per_min"], 9)
        self.assertEqual(snap["by_surface"]["gleba"]["consumption"]["items"][0]["consumed_per_min"], 8)
        self.assertEqual(snap["by_surface"]["gleba"]["power"]["generation_w"], 2.0)
        self.assertEqual(snap["by_surface"]["gleba"]["production"]["fluids"][0]["per_min"], 500)

    def test_cycled_planet_stays_in_sync(self) -> None:
        visited = ["nauvis", "vulcanus", "gleba"]
        now = 8_000
        period = 8_000
        a = cycled_planet_name(visited, "nauvis", cycle=True, now_ms=now, period_ms=period)
        b = cycled_planet_name(visited, "nauvis", cycle=True, now_ms=now, period_ms=period)
        self.assertEqual(a, b)
        self.assertEqual(a, "vulcanus")
        self.assertEqual(
            cycled_planet_name(visited, "nauvis", cycle=False, now_ms=now, period_ms=period),
            "nauvis",
        )

    def test_playtime_uses_days_after_24h(self) -> None:
        from modules.game_hooks.factorio_hook import format_playtime_text

        # 340 hours, 59 minutes, 2 seconds at 60 UPS
        ticks = ((340 * 3600) + (59 * 60) + 2) * 60
        self.assertEqual(format_playtime_text(ticks), "14d 4:59:02")

    def test_platform_transit_is_origin_to_destination(self) -> None:
        self.assertEqual(
            platform_display(
                {
                    "name": "Scrap Dump",
                    "state": "on_the_path",
                    "origin": "nauvis",
                    "destination": "gleba",
                    "in_transit": True,
                }
            ),
            "Nauvis -> Gleba",
        )
        self.assertEqual(
            platform_display(
                {"name": "Spore", "location": "gleba", "state": "waiting_at_station"}
            ),
            "Gleba",
        )
        self.assertEqual(
            platform_display({"name": "Spore", "location": "nauvis", "state": "7"}),
            "Nauvis",
        )
        self.assertEqual(
            platform_display({"name": "Science Shuttle", "state": "2"}),
            "—",
        )
        self.assertEqual(
            platform_display(
                {"name": "Old Json", "state": "2", "destination": "fulgora"}
            ),
            "Fulgora",
        )
        self.assertEqual(
            platform_display(
                {
                    "name": "Legacy",
                    "state": "2",
                    "origin": "nauvis",
                    "destination": "vulcanus",
                }
            ),
            "Nauvis -> Vulcanus",
        )
        self.assertEqual(
            platform_display(
                {"name": "Label", "state": "on_the_path", "label": "nauvis -> gleba"}
            ),
            "Nauvis -> Gleba",
        )
        snap = reshape_stats(
            {
                "ticks_played": ((340 * 3600) + (59 * 60) + 2) * 60,
                "planets": {
                    "current": "nauvis",
                    "visited": ["nauvis"],
                    "platforms": [
                        {
                            "name": "Bio Dump",
                            "state": "2",
                            "origin": "nauvis",
                            "destination": "gleba",
                        }
                    ],
                },
            }
        )
        self.assertEqual(snap["playtime_text"], "14d 4:59:02")
        self.assertEqual(snap["planets"]["platforms"][0]["display"], "Nauvis -> Gleba")
        self.assertNotIn("transit", snap["planets"]["platforms"][0]["display"].lower())


class DownsampleTests(unittest.TestCase):
    def test_oldest_left_newest_right(self) -> None:
        idx = downsample_sample_indices(60, 300)
        self.assertEqual(len(idx), 60)
        self.assertEqual(idx[0], 296)
        self.assertEqual(idx[-1], 1)
        self.assertEqual(idx, list(reversed([1 + i * 5 for i in range(60)])))


class ModListMergeTests(unittest.TestCase):
    def test_preserves_existing_and_enables(self) -> None:
        existing = {
            "mods": [
                {"name": "base", "enabled": True},
                {"name": "space-age", "enabled": True},
                {"name": "mycelian-stats", "enabled": False},
            ]
        }
        merged = merge_mod_list(existing, "mycelian-stats", True)
        names = [m["name"] for m in merged["mods"]]
        self.assertEqual(names, ["base", "space-age", "mycelian-stats"])
        self.assertTrue(merged["mods"][2]["enabled"])

    def test_appends_when_missing(self) -> None:
        existing = {"mods": [{"name": "base", "enabled": True}]}
        merged = merge_mod_list(existing, "mycelian-stats", True)
        self.assertEqual(len(merged["mods"]), 2)
        self.assertEqual(merged["mods"][1]["name"], "mycelian-stats")
        self.assertTrue(merged["mods"][1]["enabled"])

    def test_round_trip_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mod-list.json"
            original = {
                "mods": [
                    {"name": "base", "enabled": True},
                    {"name": "far-reach", "enabled": True},
                ]
            }
            path.write_text(json.dumps(original, indent=2), encoding="utf-8")
            loaded = json.loads(path.read_text(encoding="utf-8"))
            merged = merge_mod_list(loaded, "mycelian-stats", True)
            path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
            again = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                [m["name"] for m in again["mods"]],
                ["base", "far-reach", "mycelian-stats"],
            )

    def test_bundled_mod_present(self) -> None:
        from modules.game_hooks.factorio_mod_installer import (
            bundled_mod_dir,
            bundled_version,
        )

        bundled = bundled_mod_dir()
        self.assertIsNotNone(bundled)
        self.assertTrue((bundled / "control.lua").is_file())
        self.assertEqual(bundled_version(), "1.0.4")

    def test_version_label_strings(self) -> None:
        text, tone = version_label("", "1.0.2", "not_installed")
        self.assertIn("not installed", text)
        self.assertIn("1.0.2", text)
        self.assertEqual(tone, "muted")
        text, tone = version_label("1.0.1", "1.0.2", "outdated")
        self.assertEqual(text, "v1.0.1  ·  new version 1.0.2")
        self.assertEqual(tone, "warning")
        text, tone = version_label("1.0.2", "1.0.2", "current")
        self.assertEqual(text, "v1.0.2")
        self.assertEqual(tone, "ok")

    def test_detect_state_from_mods_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(detect_state(tmp), "not_installed")
            dest = Path(tmp) / "mods" / "mycelian-stats_1.0.1"
            dest.mkdir(parents=True)
            (dest / "info.json").write_text(
                json.dumps({"name": "mycelian-stats", "version": "1.0.1"}),
                encoding="utf-8",
            )
            self.assertEqual(detect_state(tmp), "outdated")
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "mods" / "mycelian-stats_1.0.4"
            dest.mkdir(parents=True)
            (dest / "info.json").write_text(
                json.dumps({"name": "mycelian-stats", "version": "1.0.4"}),
                encoding="utf-8",
            )
            self.assertEqual(detect_state(tmp), "current")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Hub offload keeps other greenlets running; config caches stay coherent."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.template_config_parser import TemplateConfigParser  # noqa: E402
from modules.web_engine import WebEngine  # noqa: E402


class RunOffHubTests(unittest.TestCase):
    def test_blocking_call_on_the_hub_does_not_stall_other_greenlets(self) -> None:
        import gevent

        eng = object.__new__(WebEngine)
        out = {}

        def slow(x):
            time.sleep(0.4)  # native block, like a GQL round trip
            return {"url_1x": f"icon-{x}"}

        def hub_thread() -> None:
            eng._server_thread_ident = threading.get_ident()
            ticks = []
            stop = []

            def ticker() -> None:
                while not stop:
                    ticks.append(time.monotonic())
                    gevent.sleep(0.02)

            def caller() -> None:
                out["result"] = eng._run_off_hub(slow, "123")
                stop.append(True)

            gevent.joinall([gevent.spawn(ticker), gevent.spawn(caller)])
            out["max_gap"] = max(b - a for a, b in zip(ticks, ticks[1:]))

        t = threading.Thread(target=hub_thread, daemon=True)
        t.start()
        t.join(5)
        self.assertEqual(out["result"], {"url_1x": "icon-123"})
        self.assertLess(out["max_gap"], 0.2)

    def test_exceptions_propagate_and_off_hub_is_a_plain_call(self) -> None:
        import gevent

        eng = object.__new__(WebEngine)
        eng._server_thread_ident = None
        self.assertEqual(eng._run_off_hub(lambda a, b: a + b, 2, 3), 5)
        caught = {}

        def boom():
            raise ValueError("nope")

        def hub_thread() -> None:
            eng._server_thread_ident = threading.get_ident()

            def caller() -> None:
                try:
                    eng._run_off_hub(boom)
                except ValueError as exc:
                    caught["exc"] = str(exc)

            gevent.spawn(caller).join()

        t = threading.Thread(target=hub_thread, daemon=True)
        t.start()
        t.join(5)
        self.assertEqual(caught.get("exc"), "nope")


def _cache_engine(config_dir: str) -> WebEngine:
    eng = object.__new__(WebEngine)
    eng.template_config_parser = TemplateConfigParser(config_dir=config_dir)
    eng._preview_sessions_lock = threading.Lock()
    eng._preview_sessions = {}
    eng._preview_sessions_rev = 0
    eng._template_config_generation = 0
    eng._all_template_configs_cache = None
    eng._all_template_configs_cache_lock = threading.Lock()
    eng._all_template_configs_building = False
    eng._dynamic_controls_cache = None
    eng._dynamic_controls_cache_lock = threading.Lock()
    eng._dynamic_controls_building = False
    eng._template_queue_metadata_cache_lock = threading.Lock()
    eng._template_queue_metadata_cache = None
    eng._overlay_html_cache = {}
    eng.socketio = MagicMock()
    return eng


def _write_config(config_dir: str, name: str, value: str, mtime: float) -> None:
    path = os.path.join(config_dir, f"{name}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"template_name": name, "elements": [{"id": "Msg", "value": value}]}, fh)
    os.utime(path, (mtime, mtime))


def _value(payload: bytes, name: str = "follow") -> str:
    cfg = json.loads(payload.decode("utf-8"))[name]
    return next(e["value"] for e in cfg["elements"] if e["id"] == "Msg")


class AllTemplateConfigsCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        _write_config(self.dir, "follow", "disk", 1_000_000.0)
        _write_config(self.dir, "raids", "r", 1_000_000.0)
        self.eng = _cache_engine(self.dir)

    def test_second_preview_push_is_served_fresh(self) -> None:
        eng = self.eng
        eng.push_preview_overrides("tok", "follow", {"Msg": "VALUE_ONE"})
        body1, _ = eng._get_all_template_configs_cached("tok")
        eng.push_preview_overrides("tok", "follow", {"Msg": "VALUE_TWO"})
        self.assertIsNone(eng._peek_all_template_configs_cache("tok"))
        body2, hit = eng._get_all_template_configs_cached("tok")
        self.assertEqual(_value(body1), "VALUE_ONE")
        self.assertEqual(_value(body2), "VALUE_TWO")
        self.assertFalse(hit)

    def test_unchanged_files_keep_the_cache_hit(self) -> None:
        eng = self.eng
        body1, hit1 = eng._get_all_template_configs_cached("")
        body2, hit2 = eng._get_all_template_configs_cached("")
        self.assertFalse(hit1)
        self.assertTrue(hit2)
        self.assertEqual(body1, body2)
        # Past the verify window, an unchanged fingerprint still hits.
        eng._TEMPLATE_CONFIGS_VERIFY_SEC = 0.0
        self.assertEqual(eng._peek_all_template_configs_cache(""), body1)

    def test_direct_file_write_is_seen_after_the_old_3s_window(self) -> None:
        eng = self.eng
        body1, _ = eng._get_all_template_configs_cached("")
        # Spore Studio save writes the JSON directly (no invalidate call).
        _write_config(self.dir, "follow", "spore-save", 1_000_100.0)
        self.assertEqual(eng._peek_all_template_configs_cache(""), body1)
        eng._TEMPLATE_CONFIGS_VERIFY_SEC = 0.0
        self.assertIsNone(eng._peek_all_template_configs_cache(""))
        body2, hit = eng._get_all_template_configs_cached("")
        self.assertFalse(hit)
        self.assertEqual(_value(body2), "spore-save")

    def test_build_racing_an_invalidate_is_not_cached(self) -> None:
        eng = self.eng
        real_build = eng._build_all_template_configs

        def racing_build(tok):
            configs = real_build(tok)
            eng.invalidate_all_template_configs_cache()  # save lands mid-build
            return configs

        eng._build_all_template_configs = racing_build
        eng._get_all_template_configs_cached("")
        self.assertIsNone(eng._all_template_configs_cache)


class DynamicControlsCacheTests(unittest.TestCase):
    def test_runtime_db_write_drops_the_dynamic_controls_cache(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            eng = _cache_engine(d)
            eng._dynamic_controls_cache = ("", time.time() + 100, b"{}")
            eng._source_control_templates_for_runtime_path = lambda _p: ["counter"]
            eng._emit_source_controls_state_update = MagicMock()
            eng._broadcast_source_control_runtime_path_update("Counter/state", {"v": 1})
            self.assertIsNone(eng._dynamic_controls_cache)
            eng._emit_source_controls_state_update.assert_called_once()

    def test_unrelated_runtime_write_keeps_the_cache(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            eng = _cache_engine(d)
            entry = ("", time.time() + 100, b"{}")
            eng._dynamic_controls_cache = entry
            eng._source_control_templates_for_runtime_path = lambda _p: []
            eng._broadcast_source_control_runtime_path_update("Other/state", {})
            self.assertIs(eng._dynamic_controls_cache, entry)

    def test_dynamic_controls_keep_their_original_3s_ttl(self) -> None:
        self.assertEqual(WebEngine._DYNAMIC_CONTROLS_CACHE_TTL, 3.0)

    def test_only_hidden_templates_carry_the_hidden_flag(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            controls = {"elements": [{"type": "button", "action": "go"}]}
            for name, hidden in (("shown", False), ("secret", True)):
                cfg = {"template_name": name, "elements": [], "dynamic_controls": controls}
                if hidden:
                    cfg["hidden"] = True
                with open(os.path.join(d, f"{name}.json"), "w", encoding="utf-8") as fh:
                    json.dump(cfg, fh)
            configs = _cache_engine(d)._build_dynamic_controls_configs(None)
            self.assertNotIn("hidden", configs["shown"])
            self.assertIs(configs["secret"]["hidden"], True)
            self.assertEqual(configs["secret"]["elements"], controls["elements"])


class DockThemeInjectTests(unittest.TestCase):
    """OBS docks get the theme on first paint; overlays stay unthemed."""

    HTML = '<!DOCTYPE html>\n<html lang="en">\n<head><title>x</title></head><body></body></html>'

    def test_docks_get_theme_vars_and_theme_type(self) -> None:
        from modules.web_engine import _theme_dock_html

        for template in ("source_controls", "activity_feed"):
            html = _theme_dock_html(template, self.HTML)
            self.assertIn('<style id="mycelian-theme-vars">', html)
            self.assertIn("--color-bg-base", html)
            self.assertRegex(html, r'<html data-theme-type="(dark|light)" lang="en">')
            self.assertLess(html.index("mycelian-theme-vars"), html.index("</head>"))

    def test_overlays_are_untouched(self) -> None:
        from modules.web_engine import _theme_dock_html

        self.assertEqual(_theme_dock_html("alerts", self.HTML), self.HTML)


class OverlayHtmlCacheKeyTests(unittest.TestCase):
    def test_key_taken_before_render_is_stored(self) -> None:
        eng = object.__new__(WebEngine)
        eng._overlay_html_cache = {}
        keys = iter([("pre",), ("post",)])
        eng._overlay_html_cache_key = lambda _t: next(keys)
        eng._store_overlay_html("alerts", "<html>", ("pre",))
        # A save during the render changed the files: the stored entry is stale.
        self.assertIsNone(eng._cached_overlay_html("alerts", ("post",)))
        self.assertEqual(eng._cached_overlay_html("alerts", ("pre",)), "<html>")


class HubAlertStateGuardTests(unittest.TestCase):
    def test_second_hub_greenlet_waits_for_the_first_to_leave(self) -> None:
        import gevent

        eng = object.__new__(WebEngine)
        manager_lock = threading.RLock()  # stands in for AlertStateManager._lock
        trace = []

        def handler(name):
            with manager_lock:
                trace.append(f"{name} in")
                gevent.sleep(0.05)  # remote-DB poll yields while holding it
                trace.append(f"{name} out")
            return name

        wrapped = eng._hub_alert_state_serialized(handler)
        results = []

        def hub_thread() -> None:
            eng._server_thread_ident = threading.get_ident()
            jobs = [gevent.spawn(lambda n=n: results.append(wrapped(n))) for n in "AB"]
            gevent.joinall(jobs)

        t = threading.Thread(target=hub_thread, daemon=True)
        t.start()
        t.join(5)
        self.assertEqual(trace, ["A in", "A out", "B in", "B out"])
        self.assertEqual(results, ["A", "B"])

    def test_off_hub_calls_are_not_wrapped_in_a_lock(self) -> None:
        eng = object.__new__(WebEngine)
        eng._server_thread_ident = None

        def handle(data=None):
            return {"ok": data}

        wrapped = eng._hub_alert_state_serialized(handle)
        self.assertEqual(wrapped(3), {"ok": 3})
        self.assertEqual(wrapped.__name__, "handle")


class ParserCacheCopyTests(unittest.TestCase):
    def test_miss_and_hit_return_independent_copies(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            _write_config(d, "follow", "disk", 1_000_000.0)
            parser = TemplateConfigParser(config_dir=d)
            first = parser.load_config("follow")
            first["elements"][0]["value"] = "mutated"
            second = parser.load_config("follow")
            self.assertEqual(second["elements"][0]["value"], "disk")
            shared = parser.load_config("follow", copy_result=False)
            self.assertIs(shared, parser.load_config("follow", copy_result=False))
            self.assertIsNot(shared, second)


if __name__ == "__main__":
    unittest.main()

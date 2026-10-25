#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Spore Studio, shell and Stream Deck notifications: false results and repeats."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from modules.spore_studio import containers, save_pipeline, template_parser_back
from modules import streamdeck_plugin_utils as sdp


def _tmp_template_path(root: str):
    def _get(rel: str = "") -> str:
        return os.path.join(root, rel) if rel else root

    return _get


class SaveSidecarResultTests(unittest.TestCase):
    def test_save_reports_failure_when_sidecar_not_written(self):
        model = {"template_name": "sidecar_fail", "elements": [], "design": {"width": 100, "height": 100}}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            save_pipeline.template_codegen, "compile_model", return_value=("<html></html>", {"elements": []})
        ), mock.patch.object(
            save_pipeline, "get_template_path", side_effect=_tmp_template_path(tmp)
        ), mock.patch.object(
            save_pipeline.TemplateConfigParser, "get_config_path", return_value=os.path.join(tmp, "cfg.json")
        ), mock.patch.object(
            save_pipeline.template_parser_back, "save_sidecar", return_value=False
        ), mock.patch.object(
            save_pipeline, "ensure_template_assets_folder"
        ), mock.patch.object(
            save_pipeline, "_refresh_web_engine_routes", return_value=True
        ) as refresh, mock.patch.object(
            save_pipeline, "_validate_name", side_effect=lambda n: n
        ):
            with self.assertRaises(save_pipeline.SporeStudioError) as ctx:
                save_pipeline.save_template(model)
            # The overlay files were still written and the route refreshed.
            self.assertTrue(Path(tmp, "sidecar_fail.html").is_file())
            refresh.assert_called_once_with("sidecar_fail")
        self.assertIn("may not reopen correctly", str(ctx.exception))

    def test_route_refresh_failure_warns_once_and_returns_false(self):
        inst = mock.Mock()
        inst.register_routes.side_effect = RuntimeError("boom")
        fake_engine = mock.Mock(web_engine_instance=inst)
        with mock.patch.dict("sys.modules", {"modules.web_engine": fake_engine}), mock.patch(
            "modules.notification_engine.notify"
        ) as notify:
            import modules

            with mock.patch.object(modules, "web_engine", fake_engine, create=True):
                ok = save_pipeline._refresh_web_engine_routes("demo")
        self.assertFalse(ok)
        notify.assert_called_once()
        kwargs = notify.call_args.kwargs
        self.assertEqual(kwargs["source"], "overlay_server")
        self.assertEqual(kwargs["dedupe_key"], "spore:route_refresh")
        self.assertIn("boom", kwargs["details"])
        self.assertNotIn("boom", notify.call_args.args[0])


class ParseExistingTests(unittest.TestCase):
    def test_missing_template_raises_instead_of_blank_model(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            template_parser_back, "get_template_path", side_effect=_tmp_template_path(tmp)
        ), mock.patch.object(template_parser_back, "_has_public_config", return_value=False):
            with self.assertRaises(FileNotFoundError):
                template_parser_back.parse_existing("does_not_exist")

    def test_save_sidecar_returns_false_when_folder_cannot_be_made(self):
        with mock.patch.object(template_parser_back.os, "makedirs", side_effect=PermissionError("denied")):
            self.assertFalse(template_parser_back.save_sidecar("x", {"elements": []}))


class DamagedContainerTests(unittest.TestCase):
    def test_damaged_container_warns_once_per_file_version(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            containers, "get_template_path", side_effect=_tmp_template_path(tmp)
        ), mock.patch("modules.notification_engine.notify") as notify:
            folder = Path(tmp, "_containers")
            folder.mkdir()
            Path(folder, "broken.json").write_text("{not json", encoding="utf-8")
            containers._warned_bad_containers.clear()
            self.assertIsNone(containers.load_container("broken"))
            self.assertIsNone(containers.load_container("broken"))
        notify.assert_called_once()
        self.assertEqual(notify.call_args.kwargs["source"], "spore_studio")


class StreamDeckOutdatedTests(unittest.TestCase):
    def test_not_repeated_while_same_notice_is_in_history(self):
        status = sdp.PluginStatus(sdp.PluginInstallState.OUTDATED, bundled_version="2.0", installed_version="1.0")
        details = "installed: v1.0, bundled: v2.0"
        history = [{"dedupe_key": sdp.OUTDATED_NOTIFY_DEDUPE_KEY, "details": details}]
        with mock.patch.object(sdp, "get_plugin_status", return_value=status), mock.patch.object(
            sdp, "get_history", return_value=history
        ), mock.patch.object(sdp, "notify") as notify:
            self.assertFalse(sdp.maybe_notify_streamdeck_plugin_outdated())
        notify.assert_not_called()

    def test_shown_once_for_a_new_version(self):
        status = sdp.PluginStatus(sdp.PluginInstallState.OUTDATED, bundled_version="2.0", installed_version="1.0")
        with mock.patch.object(sdp, "get_plugin_status", return_value=status), mock.patch.object(
            sdp, "get_history", return_value=[]
        ), mock.patch.object(sdp, "notify", return_value="id") as notify:
            self.assertTrue(sdp.maybe_notify_streamdeck_plugin_outdated())
        kwargs = notify.call_args.kwargs
        self.assertEqual(kwargs["source"], "app")
        self.assertNotIn("timeout", kwargs)


if __name__ == "__main__":
    unittest.main()

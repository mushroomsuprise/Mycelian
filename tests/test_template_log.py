#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for client template log validation and rate limiting."""

from __future__ import annotations

import logging
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.template_log import (  # noqa: E402
    TemplateLogRateLimiter,
    inject_template_logger,
    normalize_template_log_payload,
    process_template_log,
    write_template_log_entry,
)


class TemplateLogPayloadTests(unittest.TestCase):
    def test_normalize_valid_error_payload(self) -> None:
        payload, err = normalize_template_log_payload(
            {
                "template_name": "counter",
                "level": "error",
                "message": "Config load failed",
                "stack": "Error: boom",
                "url": "/counter",
                "source": "console.error",
            }
        )
        self.assertIsNone(err)
        assert payload is not None
        self.assertEqual(payload["template_name"], "counter")
        self.assertEqual(payload["level"], "error")
        self.assertEqual(payload["message"], "Config load failed")
        self.assertEqual(payload["stack"], "Error: boom")

    def test_normalize_rejects_missing_message(self) -> None:
        payload, err = normalize_template_log_payload(
            {"template_name": "counter", "level": "error"}
        )
        self.assertIsNone(payload)
        self.assertEqual(err, "message is required")

    def test_normalize_rejects_invalid_level(self) -> None:
        payload, err = normalize_template_log_payload(
            {
                "template_name": "counter",
                "level": "critical",
                "message": "oops",
            }
        )
        self.assertIsNone(payload)
        self.assertIn("level must be one of", err or "")

    def test_normalize_truncates_long_message(self) -> None:
        payload, err = normalize_template_log_payload(
            {
                "template_name": "counter",
                "level": "warn",
                "message": "x" * 3000,
            }
        )
        self.assertIsNone(err)
        assert payload is not None
        self.assertEqual(len(payload["message"]), 2048)


class TemplateLogRateLimiterTests(unittest.TestCase):
    def test_rate_limit_blocks_after_max_events(self) -> None:
        limiter = TemplateLogRateLimiter(max_events=2, window_sec=60.0)
        self.assertTrue(limiter.allow("client-a"))
        self.assertTrue(limiter.allow("client-a"))
        self.assertFalse(limiter.allow("client-a"))
        self.assertTrue(limiter.allow("client-b"))

    def test_evicts_idle_buckets(self) -> None:
        limiter = TemplateLogRateLimiter(max_events=5, window_sec=0.05)
        self.assertTrue(limiter.allow("idle-client"))
        self.assertIn("idle-client", limiter._buckets)
        limiter._last_evict = 0.0
        time.sleep(0.08)
        self.assertTrue(limiter.allow("fresh-client"))
        self.assertNotIn("idle-client", limiter._buckets)


class TemplateLogWriteTests(unittest.TestCase):
    def test_write_maps_levels_to_python_logger(self) -> None:
        with patch.object(logging.getLogger("modules.template_log"), "error") as mock_error:
            write_template_log_entry(
                {
                    "template_name": "alerts",
                    "level": "error",
                    "message": "socket failed",
                    "source": "connect_error",
                }
            )
            mock_error.assert_called_once()
            self.assertIn("[template:alerts]", mock_error.call_args[0][0])

        with patch.object(logging.getLogger("modules.template_log"), "warning") as mock_warn:
            write_template_log_entry(
                {
                    "template_name": "chat",
                    "level": "warn",
                    "message": "emote API slow",
                }
            )
            mock_warn.assert_called_once()


class TemplateLogProcessTests(unittest.TestCase):
    def test_process_template_log_success(self) -> None:
        limiter = TemplateLogRateLimiter(max_events=5, window_sec=60.0)
        with patch(
            "modules.template_log.write_template_log_entry"
        ) as mock_write:
            ok, err = process_template_log(
                {
                    "template_name": "title",
                    "level": "error",
                    "message": "failed",
                },
                "test-client",
                limiter,
            )
            self.assertTrue(ok)
            self.assertIsNone(err)
            mock_write.assert_called_once()

    def test_process_template_log_rate_limited(self) -> None:
        limiter = TemplateLogRateLimiter(max_events=1, window_sec=60.0)
        with patch("modules.template_log.write_template_log_entry"):
            process_template_log(
                {
                    "template_name": "title",
                    "level": "error",
                    "message": "first",
                },
                "client",
                limiter,
            )
            ok, err = process_template_log(
                {
                    "template_name": "title",
                    "level": "error",
                    "message": "second",
                },
                "client",
                limiter,
            )
            self.assertFalse(ok)
            self.assertEqual(err, "rate_limited")


class TemplateLogInjectTests(unittest.TestCase):
    def test_inject_after_overlay_recovery(self) -> None:
        html = (
            '<script src="/assets/default_assets/overlay_recovery.js"></script>'
            "</head><body></body>"
        )
        out = inject_template_logger(html)
        self.assertIn("template_logger.js", out)
        self.assertLess(
            out.index("overlay_recovery.js"),
            out.index("template_logger.js"),
        )

    def test_inject_noop_when_present(self) -> None:
        html = '<script src="/assets/default_assets/template_logger.js"></script>'
        self.assertEqual(inject_template_logger(html), html)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for the overlay server's local-network client filter."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.web_engine import (  # noqa: E402
    LocalOnlyMiddleware,
    is_local_client_addr,
)


class IsLocalClientAddrTests(unittest.TestCase):
    def test_loopback_allowed(self) -> None:
        for addr in ("127.0.0.1", "127.0.0.2", "::1", " ::1 "):
            self.assertTrue(is_local_client_addr(addr), addr)

    def test_private_ranges_allowed(self) -> None:
        for addr in (
            "10.0.0.1",
            "10.255.255.254",
            "172.16.0.1",
            "172.31.255.255",
            "192.168.0.1",
            "192.168.1.20",
        ):
            self.assertTrue(is_local_client_addr(addr), addr)

    def test_link_local_and_ula_allowed(self) -> None:
        for addr in (
            "169.254.1.1",
            "fe80::1",
            "fe80::1%en0",
            "[fe80::1%en0]",
            "fd00::1",
            "fc00::1",
        ):
            self.assertTrue(is_local_client_addr(addr), addr)

    def test_shared_space_allowed(self) -> None:
        self.assertTrue(is_local_client_addr("100.64.0.1"))
        self.assertTrue(is_local_client_addr("100.127.255.255"))

    def test_ipv4_mapped_unwrapped(self) -> None:
        self.assertTrue(is_local_client_addr("::ffff:192.168.1.5"))
        self.assertTrue(is_local_client_addr("::ffff:127.0.0.1"))
        self.assertFalse(is_local_client_addr("::ffff:8.8.8.8"))

    def test_public_and_special_rejected(self) -> None:
        for addr in (
            "8.8.8.8",
            "1.1.1.1",
            "203.0.113.1",
            "192.0.2.1",
            "198.51.100.1",
            "224.0.0.1",
            "0.0.0.0",
            "::",
            "2001:4860:4860::8888",
            "172.15.255.255",
            "172.32.0.1",
            "100.128.0.1",
            "11.0.0.1",
        ):
            self.assertFalse(is_local_client_addr(addr), addr)

    def test_empty_and_garbage_rejected(self) -> None:
        for addr in (None, "", "   ", "localhost", "not-an-ip", "192.168.1.1.1"):
            self.assertFalse(is_local_client_addr(addr), repr(addr))


class LocalOnlyMiddlewareTests(unittest.TestCase):
    def _call(self, remote_addr: str, extra=None):
        called = {"n": 0}

        def app(environ, start_response):
            called["n"] += 1
            body = b"ok"
            start_response(
                "200 OK",
                [
                    ("Content-Type", "text/plain"),
                    ("Content-Length", str(len(body))),
                    ("Access-Control-Allow-Origin", "*"),
                ],
            )
            return [body]

        environ = {
            "REMOTE_ADDR": remote_addr,
            "PATH_INFO": "/overlay",
        }
        if extra:
            environ.update(extra)
        captured = {}

        def start_response(status, headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = dict(headers)

        body = b"".join(LocalOnlyMiddleware(app)(environ, start_response))
        return captured["status"], captured["headers"], body, called["n"]

    def test_lan_peer_passes_through(self) -> None:
        status, headers, body, calls = self._call("192.168.1.20")
        self.assertEqual(status, "200 OK")
        self.assertEqual(body, b"ok")
        self.assertEqual(calls, 1)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "*")

    def test_public_peer_forbidden_without_cors(self) -> None:
        status, headers, body, calls = self._call(
            "8.8.8.8",
            {"HTTP_X_FORWARDED_FOR": "127.0.0.1"},
        )
        self.assertEqual(status, "403 Forbidden")
        self.assertEqual(body, b"Forbidden")
        self.assertEqual(calls, 0)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(headers.get("Content-Type"), "text/plain; charset=utf-8")


if __name__ == "__main__":
    unittest.main()

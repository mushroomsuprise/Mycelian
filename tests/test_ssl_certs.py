#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for SSL CA bootstrap via certifi."""

from __future__ import annotations

import os
import ssl
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import ssl_certs  # noqa: E402


class SslCertsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._bundle = ssl_certs._applied_bundle
        ssl_certs._applied_bundle = None
        self._env = {
            key: os.environ.get(key)
            for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
        }

    def tearDown(self) -> None:
        ssl_certs._applied_bundle = self._bundle
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_ensure_leaves_store_with_cas(self) -> None:
        ssl_certs.ensure_ssl_certificates()
        stats = ssl.create_default_context().cert_store_stats()
        self.assertGreater(
            (stats.get("x509") or 0) + (stats.get("x509_ca") or 0),
            0,
        )

    def test_ensure_is_idempotent(self) -> None:
        first = ssl_certs.ensure_ssl_certificates()
        second = ssl_certs.ensure_ssl_certificates()
        self.assertEqual(first, second)

    def test_applies_certifi_when_default_store_empty(self) -> None:
        ssl_certs._applied_bundle = None
        with mock.patch.object(ssl_certs, "default_ca_store_present", return_value=False):
            applied = ssl_certs.ensure_ssl_certificates()
        self.assertIsNotNone(applied)
        self.assertTrue(os.path.isfile(applied))
        self.assertEqual(os.environ.get("SSL_CERT_FILE"), applied)


if __name__ == "__main__":
    unittest.main()

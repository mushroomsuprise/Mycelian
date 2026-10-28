# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/ssl_certs.py: CA store detection, certifi fallback, aiohttp reload."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from modules import ssl_certs  # noqa: E402

_ENV_KEYS = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    import os

    # ensure_ssl_certificates() sets these with os.environ.setdefault, which
    # monkeypatch cannot track for variables that were absent: restore by hand.
    saved = {key: os.environ.get(key) for key in _ENV_KEYS}
    monkeypatch.setattr(ssl_certs, "_applied_bundle", None)
    for key in _ENV_KEYS:
        os.environ.pop(key, None)
    monkeypatch.delitem(sys.modules, "aiohttp.connector", raising=False)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _paths(cafile=None, openssl_cafile=None, capath=None, openssl_capath=None):
    return SimpleNamespace(cafile=cafile, openssl_cafile=openssl_cafile, capath=capath, openssl_capath=openssl_capath)


def _empty_store(monkeypatch, stats=None, ctx_error=None):
    monkeypatch.setattr(ssl_certs.ssl, "get_default_verify_paths", lambda: _paths())

    class Ctx:
        def cert_store_stats(self):
            if ctx_error:
                raise ctx_error
            return stats if stats is not None else {"x509": 0, "x509_ca": 0}

    monkeypatch.setattr(ssl_certs.ssl, "create_default_context", lambda: Ctx())


# ------------------------------------------------------------------ small helpers


def test_file_is_usable(tmp_path):
    f = tmp_path / "ca.pem"
    f.write_text("x")
    assert ssl_certs._file_is_usable(str(f)) is True
    assert ssl_certs._file_is_usable(str(tmp_path)) is False
    assert ssl_certs._file_is_usable(None) is False
    assert ssl_certs._file_is_usable("") is False


def test_dir_has_entries(tmp_path, monkeypatch):
    assert ssl_certs._dir_has_entries(None) is False
    assert ssl_certs._dir_has_entries(str(tmp_path / "missing")) is False
    assert ssl_certs._dir_has_entries(str(tmp_path)) is False
    (tmp_path / "a").write_text("x")
    assert ssl_certs._dir_has_entries(str(tmp_path)) is True

    def boom(path):
        raise OSError("denied")

    monkeypatch.setattr(ssl_certs.os, "scandir", boom)
    assert ssl_certs._dir_has_entries(str(tmp_path)) is False


# ------------------------------------------------------------------ default_ca_store_present


@pytest.mark.parametrize("key", _ENV_KEYS)
def test_env_bundle_counts_as_present(tmp_path, monkeypatch, key):
    f = tmp_path / "b.pem"
    f.write_text("x")
    monkeypatch.setenv(key, str(f))
    monkeypatch.setattr(ssl_certs.ssl, "get_default_verify_paths", lambda: pytest.fail("should short-circuit"))
    assert ssl_certs.default_ca_store_present() is True


def test_env_pointing_at_missing_file_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "nope.pem"))
    _empty_store(monkeypatch)
    assert ssl_certs.default_ca_store_present() is False


@pytest.mark.parametrize("field", ["cafile", "openssl_cafile"])
def test_default_cafile_present(tmp_path, monkeypatch, field):
    f = tmp_path / "c.pem"
    f.write_text("x")
    monkeypatch.setattr(ssl_certs.ssl, "get_default_verify_paths", lambda: _paths(**{field: str(f)}))
    assert ssl_certs.default_ca_store_present() is True


@pytest.mark.parametrize("field", ["capath", "openssl_capath"])
def test_default_capath_with_entries(tmp_path, monkeypatch, field):
    (tmp_path / "hash.0").write_text("x")
    monkeypatch.setattr(ssl_certs.ssl, "get_default_verify_paths", lambda: _paths(**{field: str(tmp_path)}))
    assert ssl_certs.default_ca_store_present() is True


@pytest.mark.parametrize("stats, expected", [
    ({"x509": 3}, True),
    ({"x509_ca": 1}, True),
    ({"x509": None, "x509_ca": None}, False),
    ({}, False),
])
def test_loaded_store_stats(monkeypatch, stats, expected):
    _empty_store(monkeypatch, stats=stats)
    assert ssl_certs.default_ca_store_present() is expected


def test_context_failure_means_absent(monkeypatch):
    _empty_store(monkeypatch, ctx_error=ssl_certs.ssl.SSLError("bad"))
    assert ssl_certs.default_ca_store_present() is False


# ------------------------------------------------------------------ aiohttp reload


def test_reload_without_aiohttp_is_noop():
    ssl_certs._reload_aiohttp_ssl_contexts()  # no module: nothing to do, no error


def test_reload_without_factory(monkeypatch):
    mod = types.ModuleType("aiohttp.connector")
    monkeypatch.setitem(sys.modules, "aiohttp.connector", mod)
    ssl_certs._reload_aiohttp_ssl_contexts()
    assert not hasattr(mod, "_SSL_CONTEXT_VERIFIED")


def test_reload_rebuilds_contexts(monkeypatch):
    mod = types.ModuleType("aiohttp.connector")
    mod._make_ssl_context = lambda verified: f"ctx-{verified}"
    monkeypatch.setitem(sys.modules, "aiohttp.connector", mod)
    ssl_certs._reload_aiohttp_ssl_contexts()
    assert mod._SSL_CONTEXT_VERIFIED == "ctx-True"
    assert mod._SSL_CONTEXT_UNVERIFIED == "ctx-False"


def test_reload_swallows_factory_errors(monkeypatch):
    mod = types.ModuleType("aiohttp.connector")

    def boom(verified):
        raise RuntimeError("no")

    mod._make_ssl_context = boom
    mod._SSL_CONTEXT_VERIFIED = "old"
    monkeypatch.setitem(sys.modules, "aiohttp.connector", mod)
    ssl_certs._reload_aiohttp_ssl_contexts()
    assert mod._SSL_CONTEXT_VERIFIED == "old"


# ------------------------------------------------------------------ ensure_ssl_certificates


def test_ensure_returns_none_when_store_present(monkeypatch):
    monkeypatch.setattr(ssl_certs, "default_ca_store_present", lambda: True)
    assert ssl_certs.ensure_ssl_certificates() is None
    assert ssl_certs._applied_bundle is None


def test_ensure_applies_certifi_and_sets_env(tmp_path, monkeypatch):
    bundle = tmp_path / "cacert.pem"
    bundle.write_text("x")
    monkeypatch.setattr(ssl_certs, "default_ca_store_present", lambda: False)
    monkeypatch.setitem(sys.modules, "certifi", SimpleNamespace(where=lambda: str(bundle)))
    reloads = []
    monkeypatch.setattr(ssl_certs, "_reload_aiohttp_ssl_contexts", lambda: reloads.append(1))
    monkeypatch.setenv("CURL_CA_BUNDLE", "/keep/me")
    assert ssl_certs.ensure_ssl_certificates() == str(bundle)
    import os

    assert os.environ["SSL_CERT_FILE"] == str(bundle)
    assert os.environ["REQUESTS_CA_BUNDLE"] == str(bundle)
    assert os.environ["CURL_CA_BUNDLE"] == "/keep/me"  # setdefault leaves existing values
    assert reloads == [1]
    # Second call is idempotent and only refreshes aiohttp.
    monkeypatch.setattr(ssl_certs, "default_ca_store_present", lambda: pytest.fail("not re-checked"))
    assert ssl_certs.ensure_ssl_certificates() == str(bundle)
    assert reloads == [1, 1]


def test_ensure_without_certifi(monkeypatch):
    monkeypatch.setattr(ssl_certs, "default_ca_store_present", lambda: False)
    monkeypatch.setitem(sys.modules, "certifi", None)  # import raises ImportError
    assert ssl_certs.ensure_ssl_certificates() is None
    assert ssl_certs._applied_bundle is None


def test_ensure_with_missing_certifi_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(ssl_certs, "default_ca_store_present", lambda: False)
    monkeypatch.setitem(sys.modules, "certifi", SimpleNamespace(where=lambda: str(tmp_path / "gone.pem")))
    assert ssl_certs.ensure_ssl_certificates() is None
    import os

    assert "SSL_CERT_FILE" not in os.environ

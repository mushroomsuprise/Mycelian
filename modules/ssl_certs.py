#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Point TLS at certifi when the interpreter has no default CA bundle.

python.org macOS installs compile OpenSSL against
``<framework>/etc/openssl/cert.pem``. That file is only created if the user
runs ``Install Certificates.command``. Python 3.14 often never gets that
step, so ``ssl.create_default_context()`` loads zero CAs.

aiohttp (Twitch, Discord, GitHub updater) builds its verified SSLContext at
**import time**. If that happens before a CA file exists, every HTTPS call
fails with ``CERTIFICATE_VERIFY_FAILED``. certifi already ships in the venv
(and is bundled by PyInstaller); this module just uses it.
"""

from __future__ import annotations

import logging
import os
import ssl
import sys
from typing import Optional

logger = logging.getLogger(__name__)

_applied_bundle: Optional[str] = None


def _file_is_usable(path: Optional[str]) -> bool:
    return bool(path) and os.path.isfile(path)


def _dir_has_entries(path: Optional[str]) -> bool:
    if not path or not os.path.isdir(path):
        return False
    try:
        with os.scandir(path) as entries:
            return next(entries, None) is not None
    except OSError:
        return False


def default_ca_store_present() -> bool:
    """True when OpenSSL already has a CA file/dir or a loaded default store."""
    for env_key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        if _file_is_usable(os.environ.get(env_key)):
            return True
    paths = ssl.get_default_verify_paths()
    if _file_is_usable(paths.cafile) or _file_is_usable(paths.openssl_cafile):
        return True
    if _dir_has_entries(paths.capath) or _dir_has_entries(paths.openssl_capath):
        return True
    try:
        stats = ssl.create_default_context().cert_store_stats()
        if (stats.get("x509") or 0) + (stats.get("x509_ca") or 0) > 0:
            return True
    except Exception:
        pass
    return False


def _reload_aiohttp_ssl_contexts() -> None:
    """Rebuild aiohttp's import-time SSLContext cache (empty if imported too soon)."""
    connector = sys.modules.get("aiohttp.connector")
    if connector is None:
        return
    make = getattr(connector, "_make_ssl_context", None)
    if not callable(make):
        return
    try:
        connector._SSL_CONTEXT_VERIFIED = make(True)
        connector._SSL_CONTEXT_UNVERIFIED = make(False)
    except Exception as e:
        logger.debug("Could not rebuild aiohttp SSL contexts: %s", e)


def ensure_ssl_certificates() -> Optional[str]:
    """Use certifi when the default CA store is empty. Idempotent.

    Returns the bundle path when certifi was applied, otherwise None.
    """
    global _applied_bundle
    if _applied_bundle:
        _reload_aiohttp_ssl_contexts()
        return _applied_bundle
    if default_ca_store_present():
        return None
    try:
        import certifi
    except Exception as e:
        logger.warning(
            "No default SSL CA bundle and certifi is unavailable: %s", e
        )
        return None
    bundle = certifi.where()
    if not _file_is_usable(bundle):
        logger.warning("certifi CA bundle missing: %s", bundle)
        return None
    os.environ.setdefault("SSL_CERT_FILE", bundle)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", bundle)
    os.environ.setdefault("CURL_CA_BUNDLE", bundle)
    _applied_bundle = bundle
    _reload_aiohttp_ssl_contexts()
    logger.info("SSL CA bundle was empty; using certifi (%s)", bundle)
    return bundle

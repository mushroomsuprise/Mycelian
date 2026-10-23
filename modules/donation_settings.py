# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Persisted Streamlabs and StreamElements donation settings."""

from __future__ import annotations

import copy
import logging
import threading
from dataclasses import asdict, dataclass, fields

from .encryption_utils import ensure_decrypted, ensure_encrypted

logger = logging.getLogger(__name__)

PATH = "DonationIntegrations"
_SECRET_FIELDS = (
    "streamlabs_access_token",
    "streamlabs_refresh_token",
    "streamelements_jwt",
)


@dataclass
class DonationSettings:
    alert_currency: str = "USD"
    streamlabs_access_token: str = ""
    streamlabs_refresh_token: str = ""
    streamlabs_connection_status: str = "Disconnected"
    streamlabs_last_error: str = ""
    streamelements_jwt: str = ""
    streamelements_channel_id: str = ""
    streamelements_connection_status: str = "Disconnected"
    streamelements_last_error: str = ""


_lock = threading.RLock()
_cache: DonationSettings | None = None
# A failed read must not look like "no settings": writing the whole blob back
# from defaults would wipe stored tokens. Until a read succeeds, remember which
# fields were changed so they can be applied on top of the stored copy.
_load_ok = True
_changed_since_failed_load: set[str] = set()


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not str(value).strip())


def _from_raw(raw: dict) -> DonationSettings:
    valid = {field.name for field in fields(DonationSettings)}
    data = {key: value for key, value in (raw or {}).items() if key in valid}
    for name in _SECRET_FIELDS:
        if data.get(name):
            data[name] = ensure_decrypted(str(data[name]))
    return DonationSettings(**data)


def _read_raw() -> tuple[dict, bool]:
    try:
        from . import database_manager

        raw, ok = database_manager.get_data_checked(PATH)
        if not isinstance(raw, dict):
            raw = {}
        return raw, bool(ok)
    except Exception:
        logger.debug("Donation settings load failed", exc_info=True)
        return {}, False


def _load() -> DonationSettings:
    global _load_ok
    raw, ok = _read_raw()
    _load_ok = ok
    if not ok:
        _changed_since_failed_load.clear()
    return _from_raw(raw)


def _recover_before_write(current: DonationSettings) -> DonationSettings | None:
    """After a failed load, re-read and apply this session's changes on the stored copy.

    Returns the settings to write, or None while the stored copy is still unreadable.
    Call with ``_lock`` held.
    """
    global _load_ok
    if _load_ok:
        return current
    raw, ok = _read_raw()
    if not ok:
        logger.warning("Donation settings still unreadable; keeping changes pending instead of overwriting")
        return None
    stored = _from_raw(raw)
    for name in _changed_since_failed_load:
        setattr(stored, name, getattr(current, name))
    _changed_since_failed_load.clear()
    _load_ok = True
    return stored


def _persist(settings: DonationSettings) -> bool:
    payload = asdict(settings)
    for name in _SECRET_FIELDS:
        value = payload.get(name) or ""
        payload[name] = ensure_encrypted(value) if value else ""
    try:
        from . import database_manager

        return bool(database_manager.set_data(PATH, payload))
    except Exception:
        logger.error("Donation settings save failed", exc_info=True)
        return False


def get_settings() -> DonationSettings:
    global _cache
    with _lock:
        if _cache is None:
            _cache = _load()
        return copy.deepcopy(_cache)


def update_settings(**changes: object) -> bool:
    """Merge fields and write them. Blank secrets do not wipe a stored secret."""
    global _cache
    with _lock:
        current = _cache if _cache is not None else _load()
        for key, value in changes.items():
            if not hasattr(current, key):
                continue
            if key in _SECRET_FIELDS and _blank(value) and getattr(current, key):
                continue
            setattr(current, key, value if value is not None else "")
            if not _load_ok:
                _changed_since_failed_load.add(key)
        current = _recover_before_write(current) or current
        _cache = current
        if not _load_ok:
            return False
        snapshot = copy.deepcopy(current)
    return _persist(snapshot)


def clear_secret(field: str) -> bool:
    """Clear a stored token or JWT, including when the current value is non-empty."""
    global _cache
    if field not in _SECRET_FIELDS and field not in {
        "streamelements_channel_id",
        "streamlabs_last_error",
        "streamelements_last_error",
    }:
        return False
    with _lock:
        current = _cache if _cache is not None else _load()
        if hasattr(current, field):
            setattr(current, field, "")
            if not _load_ok:
                _changed_since_failed_load.add(field)
        current = _recover_before_write(current) or current
        _cache = current
        if not _load_ok:
            return False
        snapshot = copy.deepcopy(current)
    return _persist(snapshot)


def set_status(service: str, status: str, error: str = "") -> None:
    """Update a service status line. Skips the write when nothing changed."""
    status_field = f"{service}_connection_status"
    error_field = f"{service}_last_error"
    current = get_settings()
    if not hasattr(current, status_field):
        return
    error_text = (error or "")[:240]
    if getattr(current, status_field) == status and getattr(current, error_field) == error_text:
        return
    update_settings(**{status_field: status, error_field: error_text})

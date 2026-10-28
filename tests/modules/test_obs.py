# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for modules/obs.py (legacy import path re-exporting obs_service)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import obs, obs_service  # noqa: E402


def test_reexports_are_the_service_objects():
    assert obs.ObsServiceImpl is obs_service.ObsServiceImpl
    assert obs.obs_service is obs_service.obs_service
    assert obs.start_obs_service is obs_service.start_obs_service


def test_all_lists_exactly_the_public_names():
    assert obs.__all__ == ["ObsServiceImpl", "obs_service", "start_obs_service"]
    for name in obs.__all__:
        assert hasattr(obs, name)

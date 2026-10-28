# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Tests for the top-level ``modules`` package."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib

import modules


def test_package_imports_and_has_docstring():
    mod = importlib.reload(modules)
    assert mod.__name__ == "modules"
    assert "MIT License" in (mod.__doc__ or "")


def test_package_is_a_package_with_path():
    assert hasattr(modules, "__path__")
    assert Path(modules.__file__).name == "__init__.py"

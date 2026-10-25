# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Clearing mycelian.log through an open logging handler."""

from __future__ import annotations

import logging

from modules.log_parser import clear_log_file, get_log_file_size


def test_clear_log_file_truncates_and_handler_keeps_writing(tmp_path):
    path = tmp_path / "mycelian.log"
    handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    log = logging.getLogger("test_log_clear")
    log.addHandler(handler)
    log.setLevel(logging.WARNING)
    try:
        for i in range(200):
            log.warning("old line %d %s", i, "x" * 50)
        assert get_log_file_size(path) > 10_000
        assert clear_log_file(path)
        text = path.read_text(encoding="utf-8")
        assert "old line" not in text and "Log cleared" in text
        log.warning("new line after clear")
        handler.flush()
        text = path.read_text(encoding="utf-8")
        assert text.splitlines()[-1].endswith("new line after clear")
        assert text.startswith(text.splitlines()[0])  # no NUL padding from a stale write offset
        assert "\x00" not in text
    finally:
        log.removeHandler(handler)
        handler.close()


def test_clear_log_file_without_handler(tmp_path):
    path = tmp_path / "mycelian.log"
    path.write_text("stuff\n" * 100, encoding="utf-8")
    assert clear_log_file(path)
    assert "stuff" not in path.read_text(encoding="utf-8")
    assert get_log_file_size(tmp_path / "missing.log") == 0

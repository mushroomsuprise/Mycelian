# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Byte-safe capped log file trimming."""

from __future__ import annotations

from pathlib import Path


def trim_log_file(path: Path, max_bytes: int, keep_bytes: int | None = None) -> None:
    """Once a log file exceeds max_bytes, keep only its newest keep_bytes.

    keep_bytes defaults to max_bytes. A lower value leaves headroom so a log that
    sits at its cap is rewritten once per (max_bytes - keep_bytes) of new output
    instead of on every record. Line-aligned and byte-safe.
    """
    if not path.is_file():
        return
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= max_bytes:
        return
    if keep_bytes is not None:
        max_bytes = min(max_bytes, keep_bytes)

    slack = 4096
    read_size = min(size, max_bytes + slack)
    try:
        with open(path, "rb") as f:
            f.seek(size - read_size)
            chunk = f.read(read_size)
    except OSError:
        return

    # Drop partial first line after binary seek.
    nl = chunk.find(b"\n")
    if nl != -1:
        chunk = chunk[nl + 1 :]

    if len(chunk) > max_bytes:
        cut = len(chunk) - max_bytes
        nl = chunk.find(b"\n", cut)
        if nl != -1:
            chunk = chunk[nl + 1 :]
        else:
            chunk = chunk[cut:]

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp_path, "wb") as out:
            out.write(chunk)
        tmp_path.replace(path)
    except OSError:
        if tmp_path.is_file():
            try:
                tmp_path.unlink()
            except OSError:
                pass

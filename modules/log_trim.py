"""Byte-safe capped log file trimming."""

from __future__ import annotations

from pathlib import Path


def trim_log_file(path: Path, max_bytes: int) -> None:
    """Keep only the newest max_bytes of a log file (line-aligned, byte-safe)."""
    if not path.is_file():
        return
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= max_bytes:
        return

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

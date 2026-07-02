#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Parse mycelian.log and surface actionable errors for the About tab."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .path_utils import get_data_path

_LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - (.+?) - (\w+) - (.*)$"
)
_TIMESTAMP_PREFIX_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - .+? - \w+ - "
)

_ACTIONABLE_LEVELS = frozenset({"ERROR", "CRITICAL"})

_NOISE_SUBSTRINGS = (
    "NPSSO token expired",
    "npsso code has expired",
    "Event listeners changed after initial definition",
    "dictionary changed size during iteration",
    "Benign websocket close",
    "ConnectionState.CLOSED",
    "cannot be sent in state",
)

DEFAULT_TAIL_BYTES = 512 * 1024
DEFAULT_MAX_UNIQUE = 5


@dataclass
class LogEntry:
    timestamp: str
    logger: str
    level: str
    message: str
    raw_lines: List[str] = field(default_factory=list)


@dataclass
class UniqueError:
    message: str
    count: int
    last_seen: str


@dataclass
class ActionableLogSummary:
    total_count: int
    unique_errors: List[UniqueError]


def get_log_file_path() -> Path:
    return Path(get_data_path("logs")) / "mycelian.log"


def get_log_dir() -> Path:
    return Path(get_data_path("logs"))


def _read_log_tail(path: Path, tail_bytes: int) -> str:
    if not path.is_file():
        return ""
    size = path.stat().st_size
    if size == 0:
        return ""
    with path.open("rb") as fh:
        if size > tail_bytes:
            fh.seek(size - tail_bytes)
            chunk = fh.read()
            nl = chunk.find(b"\n")
            if nl != -1:
                chunk = chunk[nl + 1 :]
            else:
                chunk = b""
        else:
            chunk = fh.read()
    return chunk.decode("utf-8", errors="replace")


def _parse_log_text(text: str) -> List[LogEntry]:
    entries: List[LogEntry] = []
    current: Optional[LogEntry] = None

    for line in text.splitlines():
        match = _LOG_LINE_RE.match(line)
        if match:
            if current is not None:
                entries.append(current)
            timestamp, logger, level, message = match.groups()
            current = LogEntry(
                timestamp=timestamp,
                logger=logger,
                level=level,
                message=message,
                raw_lines=[line],
            )
        elif current is not None:
            current.raw_lines.append(line)
        elif line.strip():
            continue

    if current is not None:
        entries.append(current)

    return entries


def _normalize_message(message: str) -> str:
    text = _TIMESTAMP_PREFIX_RE.sub("", message)
    return " ".join(text.split())


def is_actionable_error(entry: LogEntry) -> bool:
    if entry.level not in _ACTIONABLE_LEVELS:
        return False
    haystack = "\n".join(entry.raw_lines)
    for needle in _NOISE_SUBSTRINGS:
        if needle in haystack:
            return False
    return True


def _dedupe_errors(entries: List[LogEntry]) -> List[UniqueError]:
    buckets: dict[str, UniqueError] = {}
    order: List[str] = []

    for entry in entries:
        key = _normalize_message(entry.message)
        if key in buckets:
            buckets[key].count += 1
            buckets[key].last_seen = entry.timestamp
        else:
            buckets[key] = UniqueError(
                message=entry.message,
                count=1,
                last_seen=entry.timestamp,
            )
            order.append(key)

    unique = [buckets[key] for key in order]
    unique.sort(
        key=lambda item: datetime.strptime(item.last_seen, "%Y-%m-%d %H:%M:%S,%f"),
        reverse=True,
    )
    return unique


def get_actionable_errors(
    path: Optional[Path] = None,
    *,
    tail_bytes: int = DEFAULT_TAIL_BYTES,
    max_unique: int = DEFAULT_MAX_UNIQUE,
) -> ActionableLogSummary:
    log_path = path or get_log_file_path()
    text = _read_log_tail(log_path, tail_bytes)
    if not text:
        return ActionableLogSummary(total_count=0, unique_errors=[])

    actionable = [entry for entry in _parse_log_text(text) if is_actionable_error(entry)]
    unique_errors = _dedupe_errors(actionable)
    total_count = len(actionable)

    if max_unique > 0:
        unique_errors = unique_errors[:max_unique]

    return ActionableLogSummary(total_count=total_count, unique_errors=unique_errors)

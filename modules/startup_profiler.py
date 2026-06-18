# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Startup profiling utilities for measuring initialization performance.
"""

import functools
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_PROFILE_OWNER_ENV = "MYCELIAN_STARTUP_PROFILE_OWNER_PID"

# Global timing storage
_startup_timings: Dict[str, float] = {}
_total_startup_time: float = 0.0
_startup_profiling_enabled: bool = False

# Baseline for cumulative startup prints (set when this module is first imported)
_process_start = time.perf_counter()
# Cumulative offset from _process_start (must be 0.0, not an absolute perf_counter value)
_last_import_mark: float = 0.0


def configure_startup_profiling(enabled: bool) -> None:
    """Enable or disable [startup] timing output (call from main.py before profiling)."""
    global _startup_profiling_enabled
    _startup_profiling_enabled = enabled


def is_startup_profiling_enabled() -> bool:
    """True when startup profiling was enabled via configure_startup_profiling."""
    return _startup_profiling_enabled


def _is_profiling_owner() -> bool:
    """Only the process that first claimed profiling should emit startup prints."""
    owner = os.environ.get(_PROFILE_OWNER_ENV)
    if owner is None:
        return True
    return str(os.getpid()) == owner


def _should_emit_startup_profile() -> bool:
    return is_startup_profiling_enabled() and _is_profiling_owner()


def claim_profiling_process() -> None:
    """Claim startup profiling for the current process (first caller wins)."""
    if os.environ.get(_PROFILE_OWNER_ENV) is None:
        os.environ[_PROFILE_OWNER_ENV] = str(os.getpid())


def mark_process_start() -> None:
    """Reset cumulative baseline (call at the beginning of application startup)."""
    global _process_start, _last_import_mark
    _process_start = time.perf_counter()
    _last_import_mark = 0.0


def get_elapsed_since_baseline() -> float:
    """Seconds since mark_process_start() (or initial module import if not reset)."""
    return time.perf_counter() - _process_start


def print_startup_message(message: str) -> None:
    """Print a single [startup] line when profiling is enabled."""
    if not _should_emit_startup_profile():
        return
    print(f"[startup] {message}")


def print_timing(label: str, elapsed: float) -> None:
    """Print a timing line to stdout when startup profiling is enabled."""
    if not _should_emit_startup_profile():
        return
    cumulative = time.perf_counter() - _process_start
    print(f"[startup] +{cumulative:.3f}s (+{elapsed:.3f}s) {label}")


def print_import_timing(label: str) -> None:
    """Record and print elapsed time since the previous import milestone."""
    if not _should_emit_startup_profile():
        return
    global _last_import_mark
    cumulative = time.perf_counter() - _process_start
    if label == "process start":
        segment = 0.0
    else:
        segment = cumulative - _last_import_mark
    _last_import_mark = cumulative
    key = f"import:{label}"
    _startup_timings[key] = max(0.0, segment)
    print_timing(label, segment)


def timed(func: Callable) -> Callable:
    """Decorator that times function execution and logs the result."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        start_time = time.perf_counter()
        func_name = func.__name__

        try:
            result = func(*args, **kwargs)
            elapsed = time.perf_counter() - start_time

            if _should_emit_startup_profile():
                _startup_timings[func_name] = elapsed
                print_timing(func_name, elapsed)
            logger.info(f"{func_name} completed in {elapsed:.3f}s")
            return result
        except Exception as e:
            elapsed = time.perf_counter() - start_time
            logger.error(f"{func_name} failed after {elapsed:.3f}s: {e}")
            raise

    return wrapper


def get_startup_timings() -> Dict[str, float]:
    """Get all recorded startup timings."""
    return _startup_timings.copy()


def get_total_startup_time() -> float:
    """Get total startup time."""
    return _total_startup_time


def _filtered_timings(
    prefix: Optional[str] = None,
) -> List[tuple[str, float]]:
    items = []
    for name, duration in _startup_timings.items():
        if duration < 0:
            continue
        if prefix is not None and not name.startswith(prefix):
            continue
        items.append((name, duration))
    return sorted(items, key=lambda x: x[1], reverse=True)


def log_startup_summary(
    title: str = "STARTUP TIMING SUMMARY",
    prefix: Optional[str] = None,
) -> None:
    """Print a summary of recorded startup timings."""
    if not _should_emit_startup_profile():
        return

    sorted_timings = _filtered_timings(prefix=prefix)
    if not sorted_timings:
        print(f"\n=== {title} ===\n  (no timings recorded)\n")
        return

    print(f"\n=== {title} ===")

    total_time = sum(duration for _, duration in sorted_timings)

    for func_name, duration in sorted_timings:
        percentage = (duration / total_time) * 100 if total_time > 0 else 0
        print(f"  {func_name}: {duration:.3f}s ({percentage:.1f}%)")

    if prefix is None and _total_startup_time > 0:
        print(f"  Critical path (__main__ only): {_total_startup_time:.3f}s")
    print(f"  Sum of timed blocks: {total_time:.3f}s")
    print(f"=== END {title} ===\n")


class StartupTimer:
    """Context manager for timing code blocks."""

    def __init__(self, name: str, log_start: bool = False):
        self.name = name
        self.log_start = log_start
        self.start_time: Optional[float] = None

    def __enter__(self):
        self.start_time = time.perf_counter()
        if self.log_start:
            logger.info(f"Starting {self.name}...")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time is None:
            return
        elapsed = time.perf_counter() - self.start_time
        if _should_emit_startup_profile():
            _startup_timings[self.name] = elapsed
            if exc_type is None:
                print_timing(self.name, elapsed)
            else:
                print_timing(f"{self.name} (failed)", elapsed)
        if exc_type is None:
            logger.info(f"{self.name} completed in {elapsed:.3f}s")
        else:
            logger.error(f"{self.name} failed after {elapsed:.3f}s: {exc_val}")


def set_total_startup_time(total_time: float):
    """Set the total startup time for reporting."""
    global _total_startup_time
    _total_startup_time = total_time

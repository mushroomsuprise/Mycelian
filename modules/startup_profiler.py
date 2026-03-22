"""
Startup profiling utilities for measuring initialization performance.
"""

import functools
import logging
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Global timing storage
_startup_timings: Dict[str, float] = {}
_total_startup_time: float = 0.0


def timed(func: Callable) -> Callable:
    """Decorator that times function execution and logs the result."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        start_time = time.time()
        func_name = func.__name__

        try:
            result = func(*args, **kwargs)
            elapsed = time.time() - start_time

            # Store timing
            _startup_timings[func_name] = elapsed

            logger.info(f"{func_name} completed in {elapsed:.3f}s")
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"{func_name} failed after {elapsed:.3f}s: {e}")
            raise

    return wrapper


def get_startup_timings() -> Dict[str, float]:
    """Get all recorded startup timings."""
    return _startup_timings.copy()


def get_total_startup_time() -> float:
    """Get total startup time."""
    return _total_startup_time


def log_startup_summary():
    """Log a summary of all startup timings."""
    if not _startup_timings:
        logger.warning("No startup timings recorded")
        return

    print("\n=== STARTUP TIMING SUMMARY ===")

    # Sort by duration (longest first)
    sorted_timings = sorted(_startup_timings.items(), key=lambda x: x[1], reverse=True)

    total_time = sum(_startup_timings.values())

    for func_name, duration in sorted_timings:
        percentage = (duration / total_time) * 100 if total_time > 0 else 0
        print(f"  {func_name}: {duration:.3f}s ({percentage:.1f}%)")

    print(f"  Total startup time: {total_time:.3f}s")
    print("=== END STARTUP TIMING SUMMARY ===\n")


class StartupTimer:
    """Context manager for timing code blocks."""

    def __init__(self, name: str, log_start: bool = False):
        self.name = name
        self.log_start = log_start
        self.start_time: Optional[float] = None

    def __enter__(self):
        self.start_time = time.time()
        if self.log_start:
            logger.info(f"Starting {self.name}...")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time is not None:
            elapsed = time.time() - self.start_time
            _startup_timings[self.name] = elapsed

            if exc_type is None:
                logger.info(f"{self.name} completed in {elapsed:.3f}s")
                # print(f"{self.name} completed in {elapsed:.3f}s")
            else:
                logger.error(f"{self.name} failed after {elapsed:.3f}s: {exc_val}")
                # print(f"{self.name} failed after {elapsed:.3f}s: {exc_val}")


def set_total_startup_time(total_time: float):
    """Set the total startup time for reporting."""
    global _total_startup_time
    _total_startup_time = total_time

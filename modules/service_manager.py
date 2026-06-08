"""
Deferred service manager for background initialization of non-critical services.
"""

import threading
import time
from typing import Callable, Dict
import logging

from .notification_engine import notify_critical, nav_actions_settings
from .startup_profiler import StartupTimer, log_startup_summary

logger = logging.getLogger(__name__)


class DeferredServiceManager:
    """Manages deferred initialization of non-critical services"""

    def __init__(self, progress_callback=None):
        self.services: Dict[str, Dict] = {}
        self.initialized: Dict[str, bool] = {}
        self._thread = None
        self._shutdown = False
        self._progress_callback = progress_callback

    def register(self, name: str, init_func: Callable, priority: int = 5):
        """Register a service for deferred initialization

        Args:
            name: Unique name for the service
            init_func: Function to call for initialization
            priority: Priority (lower = higher priority, default 5)
        """
        self.services[name] = {
            "init": init_func,
            "priority": priority,
        }
        self.initialized[name] = False
        logger.debug(f"Registered deferred service: {name} (priority {priority})")

    def start_deferred_init(self, delay_seconds: float = 2.0):
        """Start deferred initialization after delay

        Args:
            delay_seconds: Seconds to wait before starting initialization
        """
        if self._shutdown:
            logger.warning("Cannot start deferred init - manager is shutting down")
            return

        def init_worker():
            logger.info(f"Deferred initialization starting in {delay_seconds}s...")
            time.sleep(delay_seconds)

            if self._shutdown:
                logger.info("Deferred initialization cancelled due to shutdown")
                return

            # Sort by priority (lower priority number = higher priority)
            sorted_services = sorted(
                self.services.items(), key=lambda x: x[1]["priority"]
            )

            logger.info(
                f"Starting deferred initialization of {len(sorted_services)} services"
            )

            total_services = len(sorted_services)
            completed_services = 0
            failed_services: list[str] = []

            for name, service in sorted_services:
                if self._shutdown:
                    logger.info("Deferred initialization interrupted by shutdown")
                    break

                if not self.initialized[name]:
                    try:
                        logger.info(f"Deferred init: {name}")
                        with StartupTimer(f"deferred:{name}"):
                            service["init"]()
                        self.initialized[name] = True
                        completed_services += 1
                        logger.info(f"Deferred init completed: {name}")

                        # Update progress (from 10% to 90% during service loading)
                        if self._progress_callback:
                            progress = 0.1 + (completed_services / total_services) * 0.8
                            self._progress_callback(progress, f"Loading {name}...")

                    except Exception as e:
                        logger.error(f"Deferred init failed for {name}: {e}")
                        failed_services.append(name)
                        # Continue with other services even if one fails

            # Retry any that failed (transient errors: DB busy, network, a
            # dependency not yet ready) before bothering the user.
            if failed_services and not self._shutdown:
                self._retry_failed_services(failed_services)

            if not self._shutdown:
                logger.info("Deferred initialization completed for all services")
                # Final progress update
                if self._progress_callback:
                    self._progress_callback(1.0, "Ready!")
                log_startup_summary(
                    title="DEFERRED STARTUP TIMING SUMMARY",
                    prefix="deferred:",
                )

        self._thread = threading.Thread(
            target=init_worker, daemon=True, name="DeferredInit"
        )
        self._thread.start()

    def _retry_failed_services(self, failed: list) -> None:
        """Retry services that failed to initialize, with backoff.

        Many deferred-init failures are transient (database briefly busy, a
        network blip, or a dependency that was not ready yet). Retrying avoids a
        whole integration staying dead for the session over a one-off error.
        """
        max_attempts = 3

        def _worker() -> None:
            attempts = {name: 0 for name in failed}
            pending = list(failed)
            delay = 10.0
            while pending and not self._shutdown:
                time.sleep(delay)
                if self._shutdown:
                    return
                still_pending = []
                for name in pending:
                    if self.initialized.get(name):
                        continue
                    attempts[name] += 1
                    try:
                        logger.info(
                            "Retrying deferred init (attempt %s/%s): %s",
                            attempts[name],
                            max_attempts,
                            name,
                        )
                        self.services[name]["init"]()
                        self.initialized[name] = True
                        logger.info("Deferred init recovered on retry: %s", name)
                    except Exception as e:
                        logger.error(
                            "Deferred init retry failed for %s: %s", name, e
                        )
                        if attempts[name] < max_attempts:
                            still_pending.append(name)
                        else:
                            logger.error(
                                "Giving up on deferred init for %s after %s attempts",
                                name,
                                attempts[name],
                            )
                            notify_critical(
                                f"A background service failed to start ({name}). "
                                "Check logs.",
                                dedupe_key=f"deferred_init:{name}",
                                actions=nav_actions_settings("App Settings"),
                            )
                pending = still_pending
                delay = min(delay * 2, 60.0)

        threading.Thread(
            target=_worker, daemon=True, name="DeferredInitRetry"
        ).start()

    def is_initialized(self, name: str) -> bool:
        """Check if a service has been initialized"""
        return self.initialized.get(name, False)

    def shutdown(self):
        """Shutdown the deferred service manager"""
        logger.info("Shutting down deferred service manager")
        self._shutdown = True

        if self._thread and self._thread.is_alive():
            # Wait a short time for the thread to finish gracefully
            self._thread.join(timeout=1.0)
            if self._thread.is_alive():
                logger.warning("Deferred init thread did not finish gracefully")

    def get_status(self) -> Dict[str, Dict]:
        """Get status of all registered services"""
        status = {}
        for name, service in self.services.items():
            status[name] = {
                "priority": service["priority"],
                "initialized": self.initialized[name],
                "thread_alive": self._thread.is_alive() if self._thread else False,
            }
        return status


# Singleton instance
_service_manager = DeferredServiceManager()


def get_service_manager() -> DeferredServiceManager:
    """Get the global deferred service manager instance"""
    return _service_manager

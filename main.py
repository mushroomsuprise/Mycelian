#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024-2026 Mycelian

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import atexit
import logging
import multiprocessing
import os
import signal
import sys
import threading
import time
from pathlib import Path

_NPSSO_HELPER_FLAG = "--mycelian-npsso-capture"

if __name__ == "__main__" and _NPSSO_HELPER_FLAG in sys.argv:
    from modules.npsso_webview_capture import main as _npsso_capture_main

    raise SystemExit(_npsso_capture_main())

from modules.log_trim import trim_log_file as _trim_log_file

# Set True to print [startup] timing lines and summaries to the console.
ENABLE_STARTUP_PROFILING = True

# Claim profiling for this OS process before other modules import startup_profiler
if os.environ.get("MYCELIAN_STARTUP_PROFILE_OWNER_PID") is None:
    os.environ["MYCELIAN_STARTUP_PROFILE_OWNER_PID"] = str(os.getpid())

from modules.startup_profiler import (
    StartupTimer,
    configure_startup_profiling,
    get_elapsed_since_baseline,
    log_startup_summary,
    mark_process_start,
    print_import_timing,
    print_startup_message,
    set_total_startup_time,
)

configure_startup_profiling(ENABLE_STARTUP_PROFILING)

mark_process_start()
print_import_timing("process start")

from modules import native_window_bridge  # noqa: F401 — native webview subprocess window_args

print_import_timing("after import native_window_bridge")

from modules import chatbot

print_import_timing("after import chatbot")

from modules import mainuiwindow

print_import_timing("after import mainuiwindow")

from modules import twitch

print_import_timing("after import twitch")


def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS  # type: ignore
    except Exception:
        # In development, use the current working directory
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


def get_data_path(relative_path):
    """Get absolute path to data files, works for dev and for PyInstaller"""
    if getattr(sys, "frozen", False):
        # Running in a PyInstaller bundle
        # Data files should be relative to the executable location
        base_path = os.path.dirname(sys.executable)
    else:
        # Running in development
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, relative_path)


LOG_MAX_BYTES = 15 * 1024 * 1024  # 15 MiB cap for mycelian.log


class CappedFileHandler(logging.FileHandler):
    """File handler that trims oldest log content when the file exceeds max_bytes."""

    def __init__(self, filename, max_bytes: int = LOG_MAX_BYTES, **kwargs):
        self.max_bytes = max_bytes
        path = Path(filename)
        _trim_log_file(path, max_bytes)
        super().__init__(filename, mode="a", **kwargs)

    def emit(self, record):
        if self.baseFilename is None:
            return
        try:
            self.acquire()
            try:
                if self.stream is None:
                    self.stream = self._open()
                msg = self.format(record)
                stream = self.stream
                stream.write(msg + self.terminator)
                self.flush()
                if os.path.getsize(self.baseFilename) > self.max_bytes:
                    if self.stream:
                        self.stream.close()
                        self.stream = None
                    _trim_log_file(Path(self.baseFilename), self.max_bytes)
                    if not self.delay:
                        self.stream = self._open()
            except RecursionError:
                raise
            except Exception:
                self.handleError(record)
            finally:
                self.release()
        except Exception:
            self.handleError(record)


# Set up logging configuration
def setup_logging():
    # Prefer UTF-8 console output on Windows (matches frozen build behavior)
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # Create logs directory if it doesn't exist - use data path for exe
    log_dir = Path(get_data_path("logs"))
    log_dir.mkdir(exist_ok=True)

    # Single log file capped at 15MB; oldest entries trimmed in place on startup and rollover
    logging.basicConfig(
        level=logging.WARNING,  # Changed to WARNING to reduce log noise while keeping errors
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            CappedFileHandler(
                log_dir / "mycelian.log",
                max_bytes=LOG_MAX_BYTES,
                encoding="utf-8",
            ),
            logging.StreamHandler(),  # Also log to console
        ],
    )

    # Set logging level for specific modules
    logging.getLogger("twitchAPI").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("google.auth").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    # Set engineio and socketio to WARNING to reduce noise but capture "Too many packets" errors
    logging.getLogger("engineio.server").setLevel(logging.WARNING)
    logging.getLogger("socketio.server").setLevel(logging.WARNING)
    logging.getLogger("pyrate_limiter").setLevel(logging.CRITICAL)
    logging.getLogger("modules.template_log").setLevel(logging.INFO)


# Set up logging first (timed for startup diagnosis)
with StartupTimer("setup_logging"):
    setup_logging()
logger = logging.getLogger(__name__)


# Signal handlers for graceful shutdown
def signal_handler(sig, frame):
    """Handle termination signals"""
    from modules.shutdown import (
        cleanup_shared_memory,
        is_shutdown_in_progress,
        shutdown_application,
    )

    if is_shutdown_in_progress():
        # Shutdown is already underway. Returning (instead of sys.exit) avoids the
        # "Exception ignored in atexit callback" noise that occurs when a second
        # signal fires while urllib3/atexit handlers are still closing sockets.
        return

    # Use print instead of logger to avoid reentrant logging issues
    print(f"Received signal {sig}, shutting down...")

    try:
        shutdown_application(reason=f"signal_{sig}", force=False)
        cleanup_shared_memory()
        print("Cleanup completed, exiting...")
        from modules.updater import _force_application_exit

        _force_application_exit()
    except Exception as e:
        print(f"Error during cleanup: {str(e)}")
        sys.exit(1)


# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# Register atexit handler as final fallback for statistics saving
def emergency_stats_save():
    """Emergency statistics save as final fallback"""
    from modules.shutdown import is_shutdown_in_progress, mark_shutdown_in_progress

    if is_shutdown_in_progress():
        return
    if not mark_shutdown_in_progress():
        return

    try:
        from modules import statistics_manager

        print("Emergency statistics save (atexit fallback)...")
        statistics_manager.shutdown_statistics()
        print("Emergency statistics save completed")
    except Exception as e:
        print(f"Emergency statistics save failed: {str(e)}")


atexit.register(emergency_stats_save)

if __name__ == "__main__":
    # Enable multiprocessing support for frozen executables
    multiprocessing.freeze_support()

    from modules.app_startup import critical_startup_done, mark_critical_startup_done

    # NiceGUI native mode re-executes this script on some HTTP 404 paths while its
    # asyncio loop is already running. Skip ALL blocking work on re-entry —
    # including Phase 4 — otherwise start_ui()/ui.run returns immediately and
    # we call sys.exit(0), tearing down the live app.
    if critical_startup_done():
        logger.debug("NiceGUI script re-entry — skipping startup phases and UI start")
    else:
        startup_start = time.perf_counter()

        # Set working directory for exe files - important for resource access
        if getattr(sys, "frozen", False):
            # Running as exe - change to the directory containing the executable
            exe_dir = os.path.dirname(sys.executable)
            os.chdir(exe_dir)
            logger.info(f"Running as executable, set working directory to: {exe_dir}")

        # =========================================
        # Phase 1: Critical Path (Blocking)
        # =========================================

        # Database must be ready first
        logger.info("Phase 1: Initializing critical components...")
        try:
            from modules.database_init import ensure_database_initialized

            with StartupTimer("ensure_database_initialized"):
                if not ensure_database_initialized():
                    logger.error("Failed to initialize database system")
                    sys.exit(1)
            logger.info("Database system initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing database system: {str(e)}", exc_info=True)
            sys.exit(1)

        # Load all data in parallel (instead of sequential database calls)
        logger.info("Loading all startup data in parallel...")
        try:
            import asyncio
            from modules import database_manager

            with StartupTimer("load_all_initial_data"):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                all_data = loop.run_until_complete(
                    database_manager.load_all_initial_data()
                )
                loop.close()
            logger.info(f"Loaded data for {len(all_data)} paths")
        except Exception as e:
            logger.error(f"Error loading startup data: {str(e)}", exc_info=True)
            sys.exit(1)

        # Initialize core modules with pre-loaded data
        try:
            from modules import (
                dataobjects,
                alertutils,
                statistics_manager,
                alert_processor,
            )

            with StartupTimer("dataobjects.initialize_with_data"):
                dataobjects.initialize_with_data(all_data)

            with StartupTimer("alertutils.initialize_alert_state_with_data"):
                alertutils.initialize_alert_state_with_data(all_data)

            with StartupTimer("statistics_manager.initialize_statistics_with_data"):
                statistics_manager.initialize_statistics_with_data(all_data)

            # Alert processor needs to be ready
            with StartupTimer("alert_processor.initialize"):
                alert_processor.initialize()

            logger.info("Core modules initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing core modules: {str(e)}", exc_info=True)
            sys.exit(1)

        # =========================================
        # Phase 2: UI Initialization
        # =========================================

        logger.info("Phase 2: Initializing UI shell...")
        try:
            with StartupTimer("mainuiwindow.initialize_ui_shell"):
                mainuiwindow.initialize_ui_shell()

            from modules.theme_manager import get_theme_manager

            with StartupTimer("theme_manager.load_themes_from_directory"):
                theme_manager = get_theme_manager()
                theme_manager.load_themes_from_directory()
                logger.info(
                    f"Theme manager initialized with {len(theme_manager._loaded_themes)} theme(s)"
                )

            logger.info("UI shell ready")
        except Exception as e:
            logger.error(f"Error initializing UI shell: {str(e)}", exc_info=True)
            sys.exit(1)

        # =========================================
        # Phase 3: Deferred Initialization (Background)
        # =========================================

        logger.info("Phase 3: Setting up deferred services...")
        try:
            from modules.service_manager import get_service_manager
            from modules.mainuiwindow import update_splash_progress, close_splash_screen
            from modules import connector_integration, connector_manager

            # Create service manager with progress callback
            def progress_callback(progress, message):
                update_splash_progress(progress, message)

            service_manager = get_service_manager(progress_callback=progress_callback)

            # Register services by priority (lower = higher priority)
            service_manager.register(
                "statistics_saving",
                statistics_manager.start_statistics_saving,
                priority=1,
            )
            service_manager.register("twitch", twitch.initialize, priority=2)
            service_manager.register("chatbot", chatbot.initialize, priority=3)
            service_manager.register(
                "connectors",
                lambda: (
                    connector_manager.initialize(),
                    connector_integration.initialize_integration(),
                ),
                priority=4,
            )

            # 3rd party services
            from modules import spotify, psn_service, youtube

            service_manager.register(
                "spotify", spotify.start_spotify_service, priority=5
            )
            service_manager.register(
                "psn", psn_service.initialize_psn_module, priority=6
            )
            service_manager.register(
                "youtube", youtube.start_youtube_service, priority=7
            )

            from modules import discord_service as discord_svc

            service_manager.register(
                "discord",
                discord_svc.start_discord_service,
                priority=7,
                background=True,
            )

            from modules.obs_service import start_obs_service as _start_obs_ws

            service_manager.register("obs", _start_obs_ws, priority=5)

            from modules.connection_monitor import start as start_connection_monitor

            service_manager.register(
                "connection_monitor", start_connection_monitor, priority=8
            )

            # Connector processing and the update manager used to be started from
            # build_root_ui, which never runs when the app boots straight into the
            # tray. They are UI-independent, so they belong with the other services.
            service_manager.register(
                "background_ui_services",
                mainuiwindow.initialize_background_ui_services,
                priority=8,
            )

            from modules import tray_controller

            service_manager.register("tray", tray_controller.initialize, priority=9)

            # Start deferred init after UI is responsive
            service_manager.start_deferred_init(delay_seconds=1.0)

            # Close splash screen after a short delay to show "Ready!" message
            def close_splash():
                time.sleep(2.0)  # Show "Ready!" for 2 seconds
                close_splash_screen()

            import threading

            splash_thread = threading.Thread(target=close_splash, daemon=True)
            splash_thread.start()

            logger.info("Deferred services registered and started")
        except Exception as e:
            logger.error(f"Error setting up deferred services: {str(e)}", exc_info=True)
            # Continue anyway - deferred services are not critical

        total_startup_time = time.perf_counter() - startup_start
        set_total_startup_time(total_startup_time)
        print_startup_message(
            f"critical path (__main__ only) ready in {total_startup_time:.3f}s "
            "(UI server not started yet)"
        )
        print_startup_message(
            f"total since import baseline: {get_elapsed_since_baseline():.3f}s "
            "(includes module imports above)"
        )
        log_startup_summary()

        mark_critical_startup_done()

        # =========================================
        # Phase 4: Start UI Server (Blocking)
        # =========================================
        try:
            logger.info("Starting NiceGUI server...")
            print_startup_message(
                "starting UI server (ui.run will block until shutdown)..."
            )
            mainuiwindow.start_ui()
            print_startup_message("ui.run returned (application shutdown)")
            logger.warning("ui.run returned — finalizing shutdown and exiting process")

            from modules.shutdown import shutdown_application

            shutdown_application(reason="ui_run_returned", force=False)
            sys.exit(0)
        except Exception as e:
            from modules.shutdown import is_shutdown_in_progress, shutdown_application
            from modules.mainuiwindow import _is_benign_shutdown_websocket_error

            if is_shutdown_in_progress() or _is_benign_shutdown_websocket_error(e):
                logger.warning("UI server stopped during shutdown: %s", e)
                shutdown_application(reason="ui_run_returned", force=False)
                sys.exit(0)
            logger.error(f"Error starting UI server: {str(e)}", exc_info=True)
            sys.exit(1)

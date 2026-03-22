#!/usr/bin/env python3
"""
MIT License

Copyright (c) 2024 Mycelian

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
import logging.handlers
import multiprocessing
import os
import signal
import sys
import threading
import time
from pathlib import Path

from modules import chatbot, mainuiwindow, twitch
from modules.startup_profiler import (
    timed,
    StartupTimer,
    log_startup_summary,
    set_total_startup_time,
)


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


# Set up logging configuration
def setup_logging():
    # Create logs directory if it doesn't exist - use data path for exe
    log_dir = Path(get_data_path("logs"))
    log_dir.mkdir(exist_ok=True)

    # Configure logging with simpler file handling to avoid rotation conflicts
    logging.basicConfig(
        level=logging.WARNING,  # Changed to WARNING to reduce log noise while keeping errors
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(
                log_dir / "mycelian.log",
                mode="a",  # Append mode, no rotation to avoid conflicts
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


# Set up logging first
setup_logging()
logger = logging.getLogger(__name__)

# Global shutdown flag to prevent multiple shutdown attempts
_shutdown_in_progress = False
_shutdown_lock = threading.Lock()


# Signal handlers for graceful shutdown
def signal_handler(sig, frame):
    """Handle termination signals"""
    global _shutdown_in_progress

    with _shutdown_lock:
        if _shutdown_in_progress:
            # Already shutting down, just exit
            sys.exit(0)
        _shutdown_in_progress = True

    # Use print instead of logger to avoid reentrant logging issues
    print(f"Received signal {sig}, shutting down...")

    try:
        # Clean up resources
        from modules import alert_processor

        alert_processor.cleanup()
        print("Alert processor cleaned up")

        # Clean up statistics manager
        try:
            from modules import statistics_manager

            print("Saving statistics data before application shutdown...")
            statistics_manager.shutdown_statistics()
            print("Statistics data saved successfully before shutdown")
        except Exception as e:
            print(f"Error saving statistics during shutdown: {str(e)}")

        # Clean up Streamlabs token monitor
        try:
            from modules.streamlabs import stop_streamlabs_token_monitor

            print("Stopping Streamlabs token monitor...")
            stop_streamlabs_token_monitor()
            print("Streamlabs token monitor stopped")
        except Exception as e:
            print(f"Error stopping Streamlabs token monitor: {str(e)}")

        # Clean up shared memory
        try:
            from multiprocessing import shared_memory  # type: ignore

            # Check if shared memory exists before trying to access it
            try:
                status_shm = shared_memory.SharedMemory(
                    name="status_flags", create=False
                )
                status_shm.close()
                status_shm.unlink()
                print("Shared memory cleaned up")
            except FileNotFoundError:
                print("Shared memory 'status_flags' not found, skipping cleanup")
        except Exception as e:
            print(f"Error cleaning up shared memory: {str(e)}")

        print("Cleanup completed, exiting...")
    except Exception as e:
        print(f"Error during cleanup: {str(e)}")

    sys.exit(0)


# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# Register atexit handler as final fallback for statistics saving
def emergency_stats_save():
    """Emergency statistics save as final fallback"""
    global _shutdown_in_progress

    with _shutdown_lock:
        if _shutdown_in_progress:
            # Already shutting down, skip emergency save
            return
        _shutdown_in_progress = True

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

    startup_start = time.time()

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
            all_data = loop.run_until_complete(database_manager.load_all_initial_data())
            loop.close()
        logger.info(f"Loaded data for {len(all_data)} paths")
    except Exception as e:
        logger.error(f"Error loading startup data: {str(e)}", exc_info=True)
        sys.exit(1)

    # Initialize core modules with pre-loaded data
    try:
        from modules import dataobjects, alertutils, statistics_manager, alert_processor

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
            # Create minimal UI shell (tabs without content)
            mainuiwindow.initialize_ui_shell()

            # Load themes from directory
            from modules.theme_manager import get_theme_manager

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
        from modules.service_manager import DeferredServiceManager
        from modules.mainuiwindow import update_splash_progress, close_splash_screen
        from modules import connector_integration, connector_manager

        # Create service manager with progress callback
        def progress_callback(progress, message):
            update_splash_progress(progress, message)

        service_manager = DeferredServiceManager(progress_callback=progress_callback)

        # Register services by priority (lower = higher priority)
        service_manager.register(
            "statistics_saving", statistics_manager.start_statistics_saving, priority=1
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
        from modules import spotify, streamlabs, psn_service, youtube

        service_manager.register("spotify", spotify.start_spotify_service, priority=5)
        service_manager.register(
            "streamlabs", streamlabs.start_streamlabs_service, priority=6
        )
        service_manager.register("psn", psn_service.initialize_psn_module, priority=7)
        service_manager.register("youtube", youtube.start_youtube_service, priority=8)

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

    # total_startup_time = time.time() - startup_start
    # set_total_startup_time(total_startup_time)
    # logger.info(f"Application ready in {total_startup_time:.2f}s")

    # Log detailed startup timing summary
    # log_startup_summary()

    # =========================================
    # Phase 4: Start UI Server (Blocking)
    # =========================================
    try:
        logger.info("Starting NiceGUI server...")
        mainuiwindow.start_ui()
    except Exception as e:
        logger.error(f"Error starting UI server: {str(e)}", exc_info=True)
        sys.exit(1)

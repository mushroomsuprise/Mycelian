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

import asyncio
import copy
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

import aiohttp

from .notification_engine import notify
from .ui_timer import layout_schedule
from packaging.version import \
    parse as parse_version  # For robust version comparison

# Platform-specific imports
if sys.platform != "win32":
    # Unix-like systems have setsid
    import os
    setsid_available = hasattr(os, 'setsid')
else:
    setsid_available = False

logger = logging.getLogger(__name__)

# GitHub Configuration - Update these variables when the repo is created
GITHUB_OWNER = "mushroomsuprise"  # Replace with your GitHub username
GITHUB_REPO = "mycelian"        # Replace with your repository name
GITHUB_API_BASE = "https://api.github.com"

# Shared GitHub releases/latest cache (avoids duplicate startup fetches)
_GITHUB_RELEASE_CACHE_TTL_SEC = 60.0
_github_release_cache_lock = threading.Lock()
_github_release_cache: Optional[Tuple[float, Optional[Dict[str, Any]]]] = None

# Ensure 'packaging' and 'aiohttp' libraries are installed: pip install packaging aiohttp

def _compare_versions(current_v_str: str, new_v_str: str) -> bool:
    """
    Compares two version strings using packaging.version.
    Returns True if new_v_str is greater than current_v_str, False otherwise.
    Handles potential errors during parsing.
    """
    try:
        # packaging.version.parse can handle various version formats like 1.0.0, 1.0.1-beta, etc.
        return parse_version(new_v_str) > parse_version(current_v_str)
    except Exception as e: # More specific: packaging.version.InvalidVersion
        logger.error(f"Error comparing versions '{current_v_str}' and '{new_v_str}': {e}", exc_info=True)
        return False

def _select_os_appropriate_asset(assets: list) -> str:
    """
    Select the most appropriate asset for the current operating system.
    
    Args:
        assets (list): List of GitHub release assets
        
    Returns:
        str: Download URL for the most appropriate asset, or empty string if none found
    """
    try:
        current_os = sys.platform.lower()
        
        # Define OS-specific file extensions in priority order
        if current_os == "win32":
            # Windows: prefer .exe, then .msi, then .zip
            preferred_extensions = [".exe", ".msi", ".zip"]
        elif current_os == "darwin":
            # macOS: prefer .dmg, then .pkg, then .zip
            preferred_extensions = [".dmg", ".pkg", ".zip"]
        else:
            # Linux and others: prefer .deb, .rpm, .AppImage, then .tar.gz, then .zip
            preferred_extensions = [".deb", ".rpm", ".appimage", ".tar.gz", ".zip"]
        
        # Try to find assets matching the preferred extensions in order
        for extension in preferred_extensions:
            for asset in assets:
                asset_name = asset.get("name", "").lower()
                if asset_name.endswith(extension):
                    download_url = asset.get("browser_download_url", "")
                    if download_url:
                        logger.info(f"Selected OS-appropriate asset for {current_os}: {asset.get('name')} ({extension})")
                        return download_url
        
        # If no OS-specific asset found, try to find any executable format
        fallback_extensions = [".exe", ".dmg", ".pkg", ".deb", ".rpm", ".appimage", ".msi"]
        for asset in assets:
            asset_name = asset.get("name", "").lower()
            if any(asset_name.endswith(ext) for ext in fallback_extensions):
                download_url = asset.get("browser_download_url", "")
                if download_url:
                    logger.warning(f"No OS-specific asset found for {current_os}, using fallback: {asset.get('name')}")
                    return download_url
        
        logger.warning(f"No appropriate installer asset found for OS: {current_os}")
        return ""
        
    except Exception as e:
        logger.error(f"Error selecting OS-appropriate asset: {e}", exc_info=True)
        return ""

async def fetch_latest_update_info_from_github(*, force_refresh: bool = False):
    """
    Fetches the latest update information from GitHub releases API asynchronously.
    
    Returns:
        dict: A dictionary with 'latest_version', 'download_url', 'release_notes',
              or None if data is not found, invalid, or an error occurs.
    """
    global _github_release_cache

    if not force_refresh:
        now = time.monotonic()
        with _github_release_cache_lock:
            if _github_release_cache is not None:
                cached_at, cached_value = _github_release_cache
                if now - cached_at < _GITHUB_RELEASE_CACHE_TTL_SEC:
                    if cached_value is None:
                        return None
                    return copy.deepcopy(cached_value)

    api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

    cached_fallback: Optional[Dict[str, Any]] = None
    with _github_release_cache_lock:
        if _github_release_cache is not None:
            _, cached_value = _github_release_cache
            if cached_value is not None:
                cached_fallback = copy.deepcopy(cached_value)

    result: Optional[Dict[str, Any]] = None
    try:
        # Set a reasonable timeout to prevent hanging
        timeout = aiohttp.ClientTimeout(total=15.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Extract version from tag_name (remove 'v' prefix if present)
                    tag_name = data.get("tag_name", "")
                    latest_version = tag_name.lstrip("v")
                    
                    # Get download URL from assets based on current OS
                    download_url = ""
                    assets = data.get("assets", [])
                    if assets:
                        download_url = _select_os_appropriate_asset(assets)
                        
                        # If no OS-specific asset found, use first asset as fallback
                        if not download_url and assets:
                            download_url = assets[0].get("browser_download_url", "")
                    
                    # If no assets, use the release page URL
                    if not download_url:
                        download_url = data.get("html_url", "")
                    
                    # Get release notes and release page URL
                    release_notes = data.get("body", "")
                    release_page_url = data.get("html_url", "")
                    
                    if latest_version:
                        logger.info(f"Successfully fetched update info from GitHub: version {latest_version}")
                        result = {
                            "latest_version": latest_version,
                            "download_url": download_url,
                            "release_notes": release_notes,
                            "release_url": release_page_url,
                        }
                    else:
                        logger.warning(f"No valid version found in GitHub release data. Tag name: {tag_name}")
                        
                elif response.status == 404:
                    logger.warning(f"GitHub repository '{GITHUB_OWNER}/{GITHUB_REPO}' not found or has no releases")
                else:
                    logger.error(f"GitHub API request failed with status {response.status}: {await response.text()}")
                    
    except (asyncio.TimeoutError, aiohttp.ServerTimeoutError) as e:
        logger.warning(
            "Timed out fetching update info from GitHub after 15s: %s", e
        )
        if cached_fallback is not None:
            return cached_fallback
    except aiohttp.ClientError as e:
        logger.error(f"Network error while fetching update info from GitHub: {e}", exc_info=True)
    except Exception as e:
        if isinstance(e, asyncio.CancelledError):
            logger.warning("GitHub update check cancelled or timed out")
            if cached_fallback is not None:
                return cached_fallback
        else:
            logger.error(f"Unexpected error fetching update info from GitHub: {e}", exc_info=True)
    finally:
        with _github_release_cache_lock:
            _github_release_cache = (time.monotonic(), result)

    if result is None:
        return None
    return copy.deepcopy(result)

async def check_for_updates(current_app_version: str, *, force_refresh: bool = False):
    """
    Checks if a newer version of the application is available on GitHub.

    Args:
        current_app_version (str): The current version of the running application.

    Returns:
        dict: A dictionary containing 'latest_version', 'download_url', 
              and 'release_notes' if an update is available. Otherwise, returns None.
    """
    logger.info(f"Checking for updates on GitHub. Current application version: {current_app_version}")
    update_info = await fetch_latest_update_info_from_github(force_refresh=force_refresh)

    if update_info:
        latest_version = update_info.get("latest_version")
        # Ensure latest_version is not None or empty before comparison
        if latest_version and _compare_versions(current_app_version, latest_version):
            logger.info(f"A new version '{latest_version}' is available (current: '{current_app_version}').")
            return update_info
        else:
            if latest_version:
                logger.info(f"Current version '{current_app_version}' is up to date. Latest available: '{latest_version}'.")

    return None

async def download_update(download_url: str, progress_callback=None):
    """
    Download the update file from the given URL.
    
    Args:
        download_url (str): URL to download the update from
        progress_callback (callable): Optional callback function to report download progress
        
    Returns:
        str: Path to the downloaded file, or None if download failed
    """
    try:
        logger.info(f"Starting download from: {download_url}")
        
        # Create a temporary directory for the download
        temp_dir = tempfile.mkdtemp(prefix="mycelian_update_")
        
        # Extract filename from URL or use an OS-appropriate default name
        filename = download_url.split("/")[-1]
        if not filename or "." not in filename:
            # Determine extension based on platform
            current_os = sys.platform.lower()
            if current_os == "win32":
                filename = "mycelian_update.exe"
            elif current_os == "darwin":
                filename = "mycelian_update.dmg"
            else:
                # Linux/Unix - prefer .deb for wider compatibility
                filename = "mycelian_update.deb"
        
        file_path = os.path.join(temp_dir, filename)
        
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as response:
                if response.status == 200:
                    total_size = int(response.headers.get('content-length', 0))
                    downloaded_size = 0
                    
                    with open(file_path, 'wb') as file:
                        async for chunk in response.content.iter_chunked(8192):
                            file.write(chunk)
                            downloaded_size += len(chunk)
                            
                            # Report progress if callback provided (0..1 for compatibility)
                            if progress_callback and total_size > 0:
                                progress_fraction = downloaded_size / total_size
                                try:
                                    progress_callback(progress_fraction)
                                except TypeError:
                                    # Backward compatibility if callback expects percent
                                    progress_callback(progress_fraction * 100.0)
                    
                    logger.info(f"Download completed: {file_path}")
                    return file_path
                else:
                    logger.error(f"Download failed with status {response.status}")
                    return None
                    
    except Exception as e:
        logger.error(f"Error downloading update: {e}", exc_info=True)
        return None

def run_installer_and_exit(installer_path: str):
    """
    Run the installer and exit the current application.
    This function creates a detached installer process and schedules app termination.
    
    Args:
        installer_path (str): Path to the installer file
    """
    try:
        logger.info(f"Running installer: {installer_path}")
        
        if sys.platform == "win32":
            # Windows: Create a batch script to delay installer execution
            _run_installer_windows_detached(installer_path)
        elif sys.platform == "darwin":
            # macOS: Use nohup to detach the process
            _run_installer_macos_detached(installer_path)
        else:
            # Linux: Use nohup and background execution
            _run_installer_linux_detached(installer_path)
        
        logger.info("Installer scheduled successfully. Exiting application.")
        
        # Force immediate exit without delay
        _force_application_exit()
        
    except Exception as e:
        logger.error(f"Error running installer: {e}", exc_info=True)
        raise

def _run_installer_windows_detached(installer_path: str):
    """
    Run installer on Windows with proper process detachment.
    Uses VBScript to avoid showing any console windows and properly wait for parent process exit.
    """
    import tempfile
    import time

    # Get current process ID
    current_pid = os.getpid()
    
    # Create a VBScript that waits for parent process to exit
    vbscript_content = f'''
Dim WshShell, oExec, parentPID, installerPath
Set WshShell = CreateObject("WScript.Shell")
parentPID = {current_pid}
installerPath = "{installer_path.replace('"', '""')}"

' Wait for parent process to exit
Do While ProcessExists(parentPID)
    WScript.Sleep 1000
Loop

' Additional delay for cleanup
WScript.Sleep 2000

' Run the installer
WshShell.Run """" & installerPath & """", 1, False

' Clean up this script file
Set fso = CreateObject("Scripting.FileSystemObject")
On Error Resume Next
fso.DeleteFile WScript.ScriptFullName
On Error GoTo 0

Function ProcessExists(pid)
    Dim objWMIService, colProcesses, objProcess
    Set objWMIService = GetObject("winmgmts:\\\\localhost\\root\\cimv2")
    Set colProcesses = objWMIService.ExecQuery("SELECT * FROM Win32_Process WHERE ProcessId = " & pid)
    ProcessExists = (colProcesses.Count > 0)
End Function
'''
    
    # Write VBScript to temp file
    temp_dir = tempfile.gettempdir()
    vbs_path = os.path.join(temp_dir, f"mycelian_updater_{int(time.time())}.vbs")
    
    with open(vbs_path, 'w') as f:
        f.write(vbscript_content)
    
    # Launch VBScript with wscript.exe (no console window)
    # IMPORTANT: When a PyInstaller-packed app launches another PyInstaller-packed
    # executable (directly or indirectly through the installer), the special
    # environment variable `_MEIPASS2` may leak to the child process. This causes
    # the newly launched app to look for Python DLLs in the parent's temp
    # extraction directory, which no longer exists, resulting in
    # "Failed to load Python DLL ... _MEIxxxx/python3xx.dll" errors.
    # To prevent this, launch the helper (wscript.exe) with a sanitized
    # environment that omits PyInstaller-related variables.
    sanitized_env = os.environ.copy()
    for var_name in ["_MEIPASS2", "PYTHONHOME", "PYTHONPATH", "_PYI_BOOTSTRAP"]:
        sanitized_env.pop(var_name, None)

    subprocess.Popen(
        ["wscript.exe", vbs_path],
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        env=sanitized_env,
    )

def _run_installer_macos_detached(installer_path: str):
    """
    Run installer on macOS with proper process detachment.
    """
    current_pid = os.getpid()
    
    # Create a shell script that waits for the parent process to exit
    script_content = f"""#!/bin/bash
# Wait for parent process to exit
while kill -0 {current_pid} 2>/dev/null; do
    sleep 1
done

# Additional delay for cleanup
sleep 2

# Open the installer
open "{installer_path}"

# Remove this script
rm "$0"
"""
    
    import tempfile
    import time

    # Write script to temp file
    temp_dir = tempfile.gettempdir()
    script_path = os.path.join(temp_dir, f"mycelian_updater_{int(time.time())}.sh")
    
    with open(script_path, 'w') as f:
        f.write(script_content)
    
    # Make script executable
    os.chmod(script_path, 0o755)
    
    # Run script detached
    try:
        # Sanitize environment to prevent PyInstaller bootstrap variables from
        # leaking into the installer and subsequently launched application.
        sanitized_env = os.environ.copy()
        for var_name in ["_MEIPASS2", "PYTHONHOME", "PYTHONPATH", "_PYI_BOOTSTRAP"]:
            sanitized_env.pop(var_name, None)

        subprocess.Popen(
            ['nohup', script_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid if setsid_available else None,  # type: ignore
            env=sanitized_env,
        )
    except AttributeError:
        # Fallback if setsid is not available
        subprocess.Popen(
            ['nohup', script_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=sanitized_env,
        )

def _run_installer_linux_detached(installer_path: str):
    """
    Run installer on Linux with proper process detachment.
    """
    current_pid = os.getpid()
    
    # Create a shell script that waits for the parent process to exit
    script_content = f"""#!/bin/bash
# Wait for parent process to exit
while kill -0 {current_pid} 2>/dev/null; do
    sleep 1
done

# Additional delay for cleanup
sleep 2

# Make installer executable if needed
chmod +x "{installer_path}"

# Try different ways to run the installer
if [[ "{installer_path}" == *.deb ]]; then
    # Debian package
    sudo dpkg -i "{installer_path}" 2>/dev/null || xdg-open "{installer_path}"
elif [[ "{installer_path}" == *.rpm ]]; then
    # RPM package
    sudo rpm -i "{installer_path}" 2>/dev/null || xdg-open "{installer_path}"
elif [[ "{installer_path}" == *.appimage ]]; then
    # AppImage
    "{installer_path}"
else
    # Try to run directly or open with default application
    "{installer_path}" 2>/dev/null || xdg-open "{installer_path}"
fi

# Remove this script
rm "$0"
"""
    
    import tempfile
    import time

    # Write script to temp file
    temp_dir = tempfile.gettempdir()
    script_path = os.path.join(temp_dir, f"mycelian_updater_{int(time.time())}.sh")
    
    with open(script_path, 'w') as f:
        f.write(script_content)
    
    # Make script executable
    os.chmod(script_path, 0o755)
    
    # Run script detached
    try:
        # Sanitize environment to prevent PyInstaller bootstrap variables from
        # leaking into the installer and subsequently launched application.
        sanitized_env = os.environ.copy()
        for var_name in ["_MEIPASS2", "PYTHONHOME", "PYTHONPATH", "_PYI_BOOTSTRAP"]:
            sanitized_env.pop(var_name, None)

        subprocess.Popen(
            ['nohup', script_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid if setsid_available else None,  # type: ignore
            env=sanitized_env,
        )
    except AttributeError:
        # Fallback if setsid is not available
        subprocess.Popen(
            ['nohup', script_path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=sanitized_env,
        )

def _force_application_exit():
    """
    Force immediate application exit with proper cleanup for NiceGUI/pywebview apps.
    """
    try:
        # Special handling for NiceGUI with pywebview
        try:
            # Try to get NiceGUI app instance and close it properly
            from nicegui import app
            if hasattr(app, 'native') and hasattr(app.native, 'main_window'):
                # Close the native window if it exists
                if app.native.main_window:
                    app.native.main_window.destroy()
                    logger.info("Closed NiceGUI native window")
        except Exception as e:
            logger.debug(f"Could not close NiceGUI window: {e}")
        
        # Try to stop pywebview windows
        try:
            import webview

            # Get all active windows and close them
            for window in webview.windows:
                if hasattr(window, 'destroy'):
                    window.destroy()
                    logger.info("Closed pywebview window")
        except Exception as e:
            logger.debug(f"Could not close pywebview windows: {e}")
        
        # Try to cleanup asyncio event loop if it exists
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule the exit in the event loop
                loop.call_soon_threadsafe(lambda: loop.stop())
                # Give it a moment to stop
                import time
                time.sleep(0.5)
        except RuntimeError:
            pass  # No event loop running
        
        # Clean up any remaining NiceGUI resources
        try:
            from nicegui import app
            if hasattr(app, 'shutdown'):
                app.shutdown()
                logger.info("Executed NiceGUI app shutdown")
        except Exception as e:
            logger.debug(f"Could not shutdown NiceGUI app: {e}")
        
        # Force immediate exit
        logger.info("Forcing application exit")
        os._exit(0)
        
    except Exception as e:
        logger.error(f"Error during application exit: {e}")
        # Fallback to hard exit
        os._exit(1) 


# -----------------------------
# Enhanced utilities for UpdateManager
# -----------------------------

def _format_speed(bytes_per_second: float) -> str:
    try:
        if bytes_per_second <= 0:
            return "0 B/s"
        units = ["B/s", "KB/s", "MB/s", "GB/s"]
        index = 0
        value = float(bytes_per_second)
        while value >= 1024.0 and index < len(units) - 1:
            value /= 1024.0
            index += 1
        if index <= 1:
            return f"{value:.0f} {units[index]}"
        return f"{value:.2f} {units[index]}"
    except Exception:
        return "N/A"


def is_valid_installer_url(url: str) -> bool:
    """
    Check if the URL appears to point to a valid installer file before downloading.
    Uses OS-specific validation for better accuracy.
    """
    try:
        if not url:
            return False

        # Reject GitHub tag/release page URLs (not asset downloads)
        if "github.com" in url and "/releases/tag/" in url:
            return False

        current_os = sys.platform.lower()
        if current_os == "win32":
            valid_extensions = [".exe", ".msi", ".zip"]
        elif current_os == "darwin":
            valid_extensions = [".dmg", ".pkg", ".zip"]
        else:
            valid_extensions = [".deb", ".rpm", ".appimage", ".tar.gz", ".zip"]

        url_lower = url.lower()
        if any(url_lower.endswith(ext) for ext in valid_extensions):
            return True

        # GitHub asset direct downloads
        if "github.com" in url and "/releases/download/" in url:
            return True

        return False
    except Exception as e:
        logger.error(f"Error validating installer URL: {e}")
        return False


def cleanup_temp_files(temp_file_paths: list) -> None:
    try:
        import shutil

        for file_path in temp_file_paths:
            if not file_path:
                continue
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    logger.debug(f"Cleaned up temp file: {file_path}")
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path, ignore_errors=True)
                    logger.debug(f"Cleaned up temp directory: {file_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp file {file_path}: {e}")
    except Exception as e:
        logger.error(f"Error during temp file cleanup: {e}")


def validate_installer_file(installer_path: str) -> bool:
    try:
        if not installer_path or not os.path.exists(installer_path):
            return False
        installer_lower = installer_path.lower()
        is_executable = (
            installer_lower.endswith(".exe")
            or installer_lower.endswith(".dmg")
            or installer_lower.endswith(".pkg")
            or installer_lower.endswith(".msi")
        )
        if not is_executable:
            try:
                with open(installer_path, "r", encoding="utf-8") as f:
                    content = f.read(100)
                    if any(tag in content.lower() for tag in ["<html", "<!doctype", "<title"]):
                        return False
            except Exception:
                pass
            return False
        return True
    except Exception as e:
        logger.error(f"Error validating installer file: {e}")
        return False


async def download_update_with_metrics(
    download_url: str,
    progress_callback: Optional[Callable[[float, float], None]] = None,
) -> Optional[str]:
    """
    Download the update and report progress fraction (0..1) and instantaneous speed (bytes/sec).
    Returns the downloaded file path or None.
    """
    try:
        logger.info(f"Starting download (metrics) from: {download_url}")
        temp_dir = tempfile.mkdtemp(prefix="mycelian_update_")
        filename = download_url.split("/")[-1]
        if not filename or "." not in filename:
            current_os = sys.platform.lower()
            if current_os == "win32":
                filename = "mycelian_update.exe"
            elif current_os == "darwin":
                filename = "mycelian_update.dmg"
            else:
                filename = "mycelian_update.deb"
        file_path = os.path.join(temp_dir, filename)

        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as response:
                if response.status != 200:
                    logger.error(f"Download failed with status {response.status}")
                    return None

                total_size = int(response.headers.get("content-length", 0))
                downloaded_size = 0
                download_start = time.perf_counter()
                window_start = download_start
                window_bytes = 0

                with open(file_path, "wb") as file:
                    async for chunk in response.content.iter_chunked(8192):
                        file.write(chunk)
                        chunk_len = len(chunk)
                        downloaded_size += chunk_len
                        window_bytes += chunk_len

                        if progress_callback and total_size > 0:
                            now = time.perf_counter()
                            window_elapsed = now - window_start
                            speed = 0.0
                            
                            # Calculate instantaneous speed over a reasonable window
                            if window_elapsed >= 0.5:  # update speed every 500ms for more stable readings
                                speed = window_bytes / window_elapsed
                                window_start = now
                                window_bytes = 0
                            elif downloaded_size > 0:
                                # For the first few chunks, use overall average speed
                                total_elapsed = now - download_start
                                if total_elapsed > 0:
                                    speed = downloaded_size / total_elapsed
                            
                            fraction = downloaded_size / total_size
                            # Always call with (fraction, speed)
                            progress_callback(fraction, speed)

        logger.info(f"Download (metrics) completed: {file_path}")
        return file_path
    except Exception as e:
        logger.error(f"Error downloading update with metrics: {e}", exc_info=True)
        return None


# -----------------------------
# Update Manager (central coordinator)
# -----------------------------

# First automatic check: settle after UI is up, then a short pause before hitting GitHub.
STARTUP_SETTLE_SECONDS = 10.0
PRE_CHECK_DELAY_SECONDS = 3.0
# Start periodic timer scheduling shortly after on_ui_ready (reads settings from state).
PERIODIC_SCHEDULE_DELAY_SECONDS = 3.0


class UpdateManager:
    """
    Centralized manager for update checks, UI prompts, and installation flow.
    """

    def __init__(self) -> None:
        self._initial_timer = None
        self._periodic_timer = None
        self._periodic_schedule_timer = None
        self._check_running = False
        self._dialog_open = False
        self._session_suppressed = False  # set when user declines an automatic update (session only)
        self._ui_ready_scheduled = False
        self._automatic_startup_check_done = False

    # ---------------- Scheduling ----------------
    def on_ui_ready(self) -> None:
        """Schedule initial and periodic checks once the UI is up."""
        if self._ui_ready_scheduled:
            logger.debug("UpdateManager.on_ui_ready already scheduled; skipping")
            return
        self._ui_ready_scheduled = True

        try:
            from nicegui import ui

            def _after_settle_schedule_pre_check() -> None:
                layout_schedule(PRE_CHECK_DELAY_SECONDS, self._run_initial_check, once=True)

            self._initial_timer = layout_schedule(
                STARTUP_SETTLE_SECONDS,
                _after_settle_schedule_pre_check,
                once=True,
            )
            if self._periodic_schedule_timer is None:
                self._periodic_schedule_timer = layout_schedule(
                    PERIODIC_SCHEDULE_DELAY_SECONDS,
                    self.reschedule_periodic_timer,
                    once=True,
                )
        except Exception as e:
            logger.error(f"UpdateManager.on_ui_ready error: {e}", exc_info=True)
            self._ui_ready_scheduled = False

    def _run_initial_check(self) -> None:
        if self._session_suppressed:
            logger.info("UpdateManager: session suppressed; skipping initial check")
            return
        self.trigger_check_and_prompt()

    def reschedule_periodic_timer(self) -> None:
        try:
            from nicegui import app, ui

            from . import dataobjects

            settings = dataobjects.state_manager.get_app_settings()
            if not getattr(settings, "auto_update", False) or self._session_suppressed:
                logger.info("UpdateManager: auto-update disabled; stopping periodic checks")
                if self._periodic_timer:
                    try:
                        self._periodic_timer.deactivate()
                    except Exception:
                        pass
                    self._periodic_timer = None
                return

            interval_minutes = max(5, min(120, int(getattr(settings, "update_check_interval_minutes", 30))))
            interval_seconds = float(interval_minutes) * 60.0

            if self._periodic_timer:
                try:
                    self._periodic_timer.deactivate()
                except Exception:
                    pass
                self._periodic_timer = None

            self._periodic_timer = layout_schedule(interval_seconds, self._periodic_check)
            setattr(app, "update_check_timer", self._periodic_timer)
            logger.info(f"UpdateManager: scheduled periodic checks every {interval_minutes} minutes")
        except Exception as e:
            logger.error(f"UpdateManager.reschedule_periodic_timer failed: {e}", exc_info=True)

    def _periodic_check(self) -> None:
        if self._session_suppressed:
            logger.info("UpdateManager: session suppressed; skipping periodic check")
            return
        if not self._automatic_startup_check_done:
            logger.debug(
                "UpdateManager: skipping periodic check until startup check completes"
            )
            return
        self.trigger_check_and_prompt()

    # ---------------- Public triggers ----------------
    def trigger_manual_check(self) -> None:
        """Manual check initiated by user via settings button."""
        self.trigger_check_and_prompt(manual=True)

    def trigger_check_and_prompt(self, manual: bool = False) -> None:
        logger.info("UpdateManager: update check requested (manual=%s)", manual)
        if self._check_running:
            logger.info("UpdateManager: check already running; skipping")
            return
        if self._dialog_open:
            logger.info("UpdateManager: update dialog already open; skipping")
            return
        try:
            from nicegui import ui

            from . import database_manager, dataobjects

            if not database_manager.database_manager._initialized:
                logger.warning("UpdateManager: database not initialized; skipping check")
                return

            settings = dataobjects.state_manager.get_app_settings()
            if not manual and not getattr(settings, "auto_update", False):
                logger.info("UpdateManager: auto-update disabled; skipping automatic check")
                return

            current_app_version = settings.version
            if not current_app_version:
                logger.warning("UpdateManager: unknown current version; skipping check")
                return

            self._check_running = True

            if manual:
                notify("Checking for updates...", type="info", timeout=2000)

            result_holder: Dict[str, Any] = {"completed": False, "update_info": None, "error": None}

            def worker():
                try:
                    result_holder["update_info"] = asyncio.run(
                        check_for_updates(
                            current_app_version, force_refresh=manual
                        )
                    )
                except Exception as e:
                    logger.error(f"UpdateManager: error in async check: {e}", exc_info=True)
                    result_holder["error"] = str(e)
                finally:
                    result_holder["completed"] = True

            import threading

            t = threading.Thread(target=worker, daemon=True)
            t.start()

            poll_counter = {"count": 0}
            max_polls = 150  # ~30s at 0.2s interval

            def poll_result():
                try:
                    poll_counter["count"] += 1
                    if poll_counter["count"] >= max_polls:
                        logger.warning("UpdateManager: update check timed out")
                        if not manual:
                            self._automatic_startup_check_done = True
                        self._check_running = False
                        return False

                    if not result_holder["completed"]:
                        return True

                    if result_holder["error"]:
                        if manual:
                            notify(f"Error checking for updates: {result_holder['error']}", type="negative")
                        elif not manual:
                            self._automatic_startup_check_done = True
                        self._check_running = False
                        return False

                    update_info = result_holder["update_info"]
                    if update_info and (manual or not self._session_suppressed):
                        self._show_update_modal(update_info, manual=manual)
                    else:
                        if manual:
                            notify("You are running the latest version!", type="positive", timeout=3000)
                    if not manual:
                        self._automatic_startup_check_done = True
                    self._check_running = False
                    return False
                except Exception as e:
                    logger.error(f"UpdateManager: poll_result error: {e}", exc_info=True)
                    self._check_running = False
                    return False

            # Create a timer that will keep checking until poll_result returns False
            update_timer = layout_schedule(0.2, poll_result)
            
            # Add a safety mechanism to ensure the timer stops after completion
            def ensure_timer_stops():
                if result_holder["completed"] and update_timer:
                    try:
                        update_timer.deactivate()
                        logger.debug("UpdateManager: Ensured update check timer is stopped")
                    except Exception as e:
                        logger.debug(f"UpdateManager: Error stopping timer: {e}")
                    return False
                return not result_holder["completed"]
            
            # Safety timer that runs for longer intervals to ensure cleanup
            layout_schedule(1.0, ensure_timer_stops)
        except Exception as e:
            logger.error(f"UpdateManager.trigger_check_and_prompt error: {e}", exc_info=True)
            self._check_running = False

    # ---------------- Modal and download flow ----------------
    def _show_update_modal(self, update_info: Dict[str, Any], *, manual: bool = False) -> None:
        try:
            from nicegui import ui

            if self._dialog_open:
                logger.info("UpdateManager: update dialog already open; skipping")
                return

            version = update_info.get("latest_version", "?")
            download_url = update_info.get("download_url", "")
            release_notes = update_info.get("release_notes", "")
            release_url = update_info.get("release_url", "")

            self._dialog_open = True

            update_dialog = ui.dialog().props("persistent")
            with update_dialog:
                card = ui.card().classes("w-[500px] p-6").style(
                    "background: linear-gradient(135deg, #2a1d4a 0%, #1a1a2e 100%);"
                    "color: white; border-radius: 12px; border: 1px solid rgba(115, 0, 255, 0.3);"
                    "box-shadow: 0 8px 32px rgba(115, 0, 255, 0.2);"
                )
                with card:
                    title_label = ui.label(f"🚀 New Version Available: {version}").classes("text-h5 font-bold mb-4").style("color: #b980ff;")
                    content_area = ui.column().classes("w-full gap-3")
                    with content_area:
                        if release_url:
                            ui.link("View release on GitHub", release_url).classes("text-sm underline").style("color: #7c3aed;")
                        if release_notes:
                            ui.label("📋 What's new:").classes("text-sm font-semibold mt-2").style("color: #e2e8f0;")
                            # Create scrollable area for release notes
                            with ui.scroll_area().classes("w-full").style("max-height: 150px; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; padding: 8px; background: rgba(0,0,0,0.2);"):
                                ui.html(release_notes.replace("\n", "<br>")).classes("text-sm").style("color: #cbd5e1; line-height: 1.4;")

                    progress_area = ui.column().classes("w-full gap-3").style("display: none;")
                    with progress_area:
                        progress_label = ui.label("Preparing download...").classes("text-sm").style("color: #e2e8f0;")
                        progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full").style("height: 8px;")
                        speed_label = ui.label("").classes("text-xs").style("color: #94a3b8;")

                    button_row = ui.row().classes("w-full justify-end gap-2 mt-4")

                    def on_decline():
                        try:
                            if not manual:
                                self._session_suppressed = True
                                self._cancel_periodic()
                        finally:
                            self._dialog_open = False
                            update_dialog.close()

                    def on_accept():
                        try:
                            # Validate URL first
                            if not is_valid_installer_url(download_url):
                                notify("No valid installer asset found for this release yet.", type="warning")
                                return
                            # Switch UI to progress mode
                            content_area.style("display: none;")
                            progress_area.style("display: block;")
                            for btn in list(button_row.default_slot.children):
                                try:
                                    btn.style("display: none;")
                                except Exception:
                                    pass

                            self._start_download_flow(
                                download_url,
                                title_label,
                                progress_label,
                                progress_bar,
                                speed_label,
                                update_dialog,
                            )
                        except Exception as e:
                            logger.error(f"Error starting download flow: {e}", exc_info=True)

                    with button_row:
                        ui.button("Decline", on_click=on_decline).props("flat").classes("secondary-text").style("border: 1px solid rgba(255,255,255,0.2);")
                        ui.button("Update", on_click=on_accept).props("unelevated").classes("font-semibold").style("background: linear-gradient(135deg, #7c3aed 0%, #b980ff 100%); color: white; border: none;")

            update_dialog.open()
        except Exception as e:
            logger.error(f"UpdateManager._show_update_modal error: {e}", exc_info=True)

    def _start_download_flow(
        self,
        download_url: str,
        title_label,
        progress_label,
        progress_bar,
        speed_label,
        dialog,
    ) -> None:
        try:
            import threading

            from nicegui import ui

            temp_files: list[str] = []
            download_state: Dict[str, Any] = {
                "status": "downloading",
                "progress": 0.0,
                "speed_bps": 0.0,
                "installer_path": None,
                "error": None,
                "done": False,
            }

            async def _download():
                def progress_cb(fraction: float, speed_bps: float) -> None:
                    download_state["progress"] = max(0.0, min(1.0, float(fraction)))
                    download_state["speed_bps"] = float(speed_bps or 0.0)

                return await download_update_with_metrics(download_url, progress_cb)

            def worker():
                try:
                    path = asyncio.run(_download())
                    if path:
                        temp_files.append(path)
                        temp_dir = os.path.dirname(path)
                        if temp_dir not in temp_files:
                            temp_files.append(temp_dir)
                        if validate_installer_file(path):
                            download_state["installer_path"] = path
                            download_state["status"] = "valid"
                        else:
                            download_state["status"] = "failed"
                            download_state["error"] = f"Downloaded file '{os.path.basename(path)}' is not a valid installer"
                    else:
                        download_state["status"] = "failed"
                        download_state["error"] = "Download failed"
                except Exception as e:
                    logger.error(f"UpdateManager download worker error: {e}", exc_info=True)
                    download_state["status"] = "failed"
                    download_state["error"] = str(e)
                finally:
                    download_state["done"] = True

            def poll_update():
                try:
                    if download_state["status"] == "downloading":
                        # Ensure progress is between 0 and 1 for the progress bar
                        progress_value = max(0.0, min(1.0, download_state["progress"]))
                        progress_bar.set_value(progress_value)
                        
                        # Display percentage with proper formatting
                        percent = progress_value * 100.0
                        progress_label.set_text(f"Downloading... {percent:.1f}%")
                        
                        # Format and display download speed
                        speed_bps = download_state.get("speed_bps", 0.0)
                        if speed_bps > 0:
                            speed_text = _format_speed(speed_bps)
                            speed_label.set_text(f"Speed: {speed_text}")
                        else:
                            speed_label.set_text("Calculating speed...")
                        return True
                    if download_state["status"] == "failed":
                        cleanup_temp_files(temp_files)
                        title_label.set_text("❌ Update Failed")
                        progress_label.set_text(download_state.get("error") or "Unknown error")
                        self._dialog_open = False
                        return False
                    if download_state["status"] == "valid":
                        title_label.set_text("🚀 Launching Installer")
                        progress_label.set_text("Starting installer and closing Mycelian...")

                        def launch():
                            try:
                                self._dialog_open = False
                                run_installer_and_exit(download_state["installer_path"])  # type: ignore[arg-type]
                            except Exception as e:
                                logger.error(f"Error launching installer: {e}", exc_info=True)
                                cleanup_temp_files(temp_files)
                                self._dialog_open = False

                        layout_schedule(2.0, launch, once=True)
                        return False
                    return not bool(download_state.get("done", False))
                except Exception as e:
                    logger.error(f"UpdateManager.poll_update error: {e}")
                    return False

            t = threading.Thread(target=worker, daemon=True)
            t.start()
            layout_schedule(0.2, poll_update)
        except Exception as e:
            logger.error(f"UpdateManager._start_download_flow error: {e}", exc_info=True)

    def _cancel_periodic(self) -> None:
        if self._periodic_timer:
            try:
                self._periodic_timer.deactivate()
            except Exception:
                pass
            self._periodic_timer = None


# Shared singleton instance
update_manager = UpdateManager()

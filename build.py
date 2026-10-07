#!/usr/bin/env python3
# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""
Improved PyInstaller build script for Mycelian
Balances size optimization with proper NiceGUI and web engine support.

After a successful compile, copies the binary into the project folder and
builds the installer for the OS it is running on.
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import time
from datetime import datetime
from pathlib import Path


class BuildProgress:
    """Simple progress tracking for the build process"""

    def __init__(self, total_steps=5):
        self.current_step = 0
        self.total_steps = total_steps
        self.start_time = time.time()

    def next_step(self, message):
        """Move to next step and display progress"""
        self.current_step += 1
        elapsed = time.time() - self.start_time
        print(f"\n[{self.current_step}/{self.total_steps}] {message}")

    def update(self, message):
        """Update current step message"""
        print(f"  → {message}")

    def success(self, message="✓ Complete"):
        """Mark current step as successful"""
        print(f"  {message}")

    def error(self, message):
        """Display error message"""
        print(f"  ✗ ERROR: {message}")

    def summary(self):
        """Display build summary"""
        elapsed = time.time() - self.start_time
        print(f"\n{'='*50}")
        print(f"BUILD COMPLETE - {elapsed:.1f}s")
        print(f"{'='*50}")


# Global progress tracker
progress = BuildProgress(total_steps=7)


def get_project_root():
    """Get the project root directory"""
    return Path(__file__).parent


def detect_os():
    """Detect the current operating system"""
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    elif system == "linux":
        return "linux"
    elif system == "darwin":
        return "macos"
    else:
        raise ValueError(
            f"Unsupported operating system: {system}. Only Windows, Linux, and macOS are supported."
        )


def get_os_specific_icon_path(os_name):
    """Get the appropriate icon path for the current OS"""
    from pathlib import Path

    project_root = get_project_root()
    icon_paths = {
        "windows": "assets/default_assets/icons/Mycelian.ico",
        "linux": None,  # No specific icon for Linux, will use default
        "macos": None,  # No specific icon for macOS, will use default
    }

    icon_path = icon_paths.get(os_name)
    if icon_path and Path(project_root / icon_path).exists():
        return icon_path
    else:
        return None


# ============================================================================
# EASY EDIT SECTION - Modify these values as needed
# ============================================================================

# Version and Build Date - Update these for new releases
VERSION = "1.13.3"
BUILD_DATE = "October 7th 2026"
BUILD_NUMBER = "dev"

# Stream Deck plugin version (manifest.json "Version"; Elgato semver, e.g. 0.2.2.0)
STREAMDECK_PLUGIN_VERSION = "1.0.0.0"

# Version file path (set to None if no version file, or provide path like 'version.txt')
VERSION_FILE = None

# ============================================================================
# OS DETECTION AND BUILD CONFIGURATION
# ============================================================================

# Detect current OS
CURRENT_OS = detect_os()
progress.update(f"Target OS: {CURRENT_OS}")

# Set output directory to 'builds' folder in project root
OUTPUT_PATH = "builds"

# Get OS-specific icon path
ICON_PATH = get_os_specific_icon_path(CURRENT_OS)

# ============================================================================


def ensure_dependencies():
    """Ensure PyInstaller is available via the uv dev group (not pip)."""
    try:
        import PyInstaller

        progress.update(f"PyInstaller v{PyInstaller.__version__} ready")
        return
    except ImportError:
        pass

    progress.update("Installing PyInstaller via uv (dev group)...")
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError(
            "PyInstaller is not installed and 'uv' was not found. "
            "Install dependencies with: uv sync"
        )

    subprocess.run([uv, "sync", "--group", "dev"], check=True)
    import importlib

    importlib.invalidate_caches()
    import PyInstaller

    progress.update(f"PyInstaller v{PyInstaller.__version__} ready")


def deploy_streamdeck_plugin_to_sd_plugin(project_root: Path) -> Path:
    """
    Copy the built Stream Deck plugin into ``sd_plugin/`` for Mycelian.iss.

    Overwrites matching files and leaves any extra bundled assets (e.g. legacy
    action icons) that are not present in the TypeScript build output.
    """
    src = (
        project_root
        / "streamdeck-plugin"
        / "mycelian"
        / "com.mushroomsuprise.mycelian.sdPlugin"
    )
    dest = project_root / "sd_plugin" / "com.mushroomsuprise.mycelian.sdPlugin"
    if not src.is_dir():
        raise FileNotFoundError(f"Stream Deck build output missing: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return dest


def update_streamdeck_plugin_manifest_version(project_root: Path) -> bool:
    """
    Write ``STREAMDECK_PLUGIN_VERSION`` into the plugin ``manifest.json`` before
    rollup/deploy so ``sd_plugin/`` and the installer bundle the new version.
    """
    import json

    manifest_path = (
        project_root
        / "streamdeck-plugin"
        / "mycelian"
        / "com.mushroomsuprise.mycelian.sdPlugin"
        / "manifest.json"
    )
    if not manifest_path.is_file():
        progress.update("Stream Deck manifest.json not found; skipping version stamp")
        return True

    version = str(STREAMDECK_PLUGIN_VERSION).strip()
    if not version:
        progress.error("STREAMDECK_PLUGIN_VERSION is empty")
        return False

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["Version"] = version
        with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent="\t", ensure_ascii=False)
            f.write("\n")
    except (OSError, json.JSONDecodeError, TypeError) as e:
        progress.error(f"Could not update Stream Deck manifest version: {e}")
        return False

    progress.update(f"Stream Deck plugin version set to {version}")
    return True


def build_streamdeck_plugin(project_root: Path) -> bool:
    """
    Build the Stream Deck plugin and stage it under ``sd_plugin/`` for the
    Inno Setup installer (``Mycelian.iss`` → ``Source: "sd_plugin\\*"``).
    """
    plugin_dir = project_root / "streamdeck-plugin" / "mycelian"
    if not plugin_dir.is_dir():
        progress.update("Stream Deck plugin source not found; skipping")
        return True

    package_json = plugin_dir / "package.json"
    if not package_json.exists():
        progress.update("Stream Deck plugin package.json missing; skipping")
        return True

    if not update_streamdeck_plugin_manifest_version(project_root):
        return False

    npm_cmd = "npm.cmd" if CURRENT_OS == "windows" else "npm"
    try:
        subprocess.run(
            [npm_cmd, "--version"],
            cwd=plugin_dir,
            capture_output=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        progress.error("npm is required to build the Stream Deck plugin")
        return False

    if not (plugin_dir / "node_modules").is_dir():
        progress.update("Installing Stream Deck plugin npm dependencies...")
        install = subprocess.run(
            [npm_cmd, "ci"],
            cwd=plugin_dir,
            capture_output=True,
            text=True,
        )
        if install.returncode != 0:
            progress.update("npm ci failed; trying npm install...")
            install = subprocess.run(
                [npm_cmd, "install"],
                cwd=plugin_dir,
                capture_output=True,
                text=True,
            )
            if install.returncode != 0:
                progress.error("Failed to install Stream Deck plugin dependencies")
                if install.stderr:
                    progress.update(install.stderr.strip())
                return False

    deploy_script = (
        "build:deploy:win" if CURRENT_OS == "windows" else "build:deploy"
    )
    progress.update(f"Running npm run {deploy_script}...")
    result = subprocess.run(
        [npm_cmd, "run", deploy_script],
        cwd=plugin_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        progress.update(
            f"npm run {deploy_script} failed; falling back to build + deploy"
        )
        build = subprocess.run(
            [npm_cmd, "run", "build"],
            cwd=plugin_dir,
            capture_output=True,
            text=True,
        )
        if build.returncode != 0:
            progress.error("Stream Deck plugin build failed")
            if build.stderr:
                progress.update(build.stderr.strip())
            return False
        try:
            dest = deploy_streamdeck_plugin_to_sd_plugin(project_root)
        except FileNotFoundError as e:
            progress.error(str(e))
            return False
    else:
        dest = project_root / "sd_plugin" / "com.mushroomsuprise.mycelian.sdPlugin"

    if not dest.is_dir():
        progress.error(f"Stream Deck plugin staging folder missing: {dest}")
        return False

    progress.update(f"Staged Stream Deck plugin for installer: {dest}")
    return True


def clean_build():
    """Clean previous build artifacts"""
    project_root = get_project_root()

    # Remove build directories with more aggressive cleanup
    for dir_name in ["build", "dist", "__pycache__"]:
        dir_path = project_root / dir_name
        if dir_path.exists():
            try:
                shutil.rmtree(dir_path, ignore_errors=True)
                progress.update(f"Cleaned {dir_path.name}")
            except Exception as e:
                progress.update(f"Could not remove {dir_path.name}")

    # Remove spec files
    spec_count = 0
    for spec_file in project_root.glob("*.spec"):
        try:
            spec_file.unlink()
            spec_count += 1
        except Exception:
            pass

    # Remove runtime hook files
    hook_count = 0
    for hook_file in project_root.glob("pyi_rth_*.py"):
        try:
            hook_file.unlink()
            hook_count += 1
        except Exception:
            pass

    if spec_count > 0 or hook_count > 0:
        progress.update(f"Removed {spec_count} spec files, {hook_count} hook files")


def get_hidden_imports(current_os):
    """Get list of hidden imports that PyInstaller might miss, filtered by OS"""
    hidden_imports = []

    # ============================================================================
    # CROSS-PLATFORM IMPORTS (work on all supported OSes)
    # ============================================================================

    # Core NiceGUI (essential only)
    hidden_imports.extend(
        [
            "nicegui",
            "nicegui.run",
            "nicegui.ui",
            "nicegui.app",
            "nicegui.events",
            "nicegui.native",
            "nicegui.functions",
            "nicegui.storage",
            "nicegui.version",
            "nicegui.binding",
            "nicegui.client",
            "nicegui.context",
            "nicegui.helpers",
            "nicegui.json",
            "nicegui.language",
            "nicegui.logging",
            "nicegui.observables",
            "nicegui.page",
            "nicegui.server",
            "nicegui.slot",
            "nicegui.timer",
            "nicegui.favicon",
            "nicegui.error",
            "nicegui.nicegui",
            # NiceGUI 3.x core modules (replace removed globals/tailwind/lifecycle)
            "nicegui.core",
            "nicegui.event",
            "nicegui.dataclasses",
            "nicegui.defaults",
            "nicegui.middlewares",
            "nicegui.dependencies",
            "nicegui.background_tasks",
            "nicegui.optional_features",
            "nicegui.air",
        ]
    )

    # Essential NiceGUI elements only
    hidden_imports.extend(
        [
            "nicegui.elements",
            "nicegui.elements.button",
            "nicegui.elements.card",
            "nicegui.elements.input",
            "nicegui.elements.label",
            "nicegui.elements.select",
            "nicegui.elements.checkbox",
            "nicegui.elements.radio",
            "nicegui.elements.slider",
            "nicegui.elements.switch",
            "nicegui.elements.textarea",
            "nicegui.elements.number",
            "nicegui.elements.html",
            "nicegui.elements.markdown",
            "nicegui.elements.image",
            "nicegui.elements.icon",
            "nicegui.elements.avatar",
            "nicegui.elements.link",
            # NiceGUI 3.x merged tab/menu/progress/notify into single modules.
            "nicegui.elements.tabs",
            "nicegui.elements.tree",
            "nicegui.elements.table",
            "nicegui.elements.separator",
            "nicegui.elements.space",
            "nicegui.elements.spinner",
            "nicegui.elements.menu",
            "nicegui.elements.context_menu",
            "nicegui.elements.tooltip",
            "nicegui.elements.notification",
            "nicegui.elements.dialog",
            "nicegui.elements.knob",
            "nicegui.elements.joystick",
            "nicegui.elements.progress",
            "nicegui.elements.keyboard",
            "nicegui.elements.json_editor",
            "nicegui.elements.code",
            "nicegui.elements.log",
            "nicegui.elements.timer",
        ]
    )

    # CRITICAL: NiceGUI infrastructure
    hidden_imports.extend(
        [
            "nicegui.elements.mixins",
            "nicegui.elements.mixins.color_elements",
            "nicegui.elements.mixins.content_element",
            "nicegui.elements.mixins.disableable_element",
            "nicegui.elements.mixins.source_element",
            "nicegui.elements.mixins.text_element",
            "nicegui.elements.mixins.validation_element",
            "nicegui.elements.mixins.value_element",
            "nicegui.staticfiles",
            "nicegui.element",
            "nicegui.element_filter",
            "nicegui.outbox",
            "nicegui.ui_run",
            "nicegui.ui_run_with",
        ]
    )

    # Docutils (required by NiceGUI)
    hidden_imports.extend(
        [
            "docutils",
            "docutils.core",
            "docutils.frontend",
            "docutils.io",
            "docutils.nodes",
            "docutils.parsers",
            "docutils.parsers.rst",
            "docutils.transforms",
            "docutils.utils",
            "docutils.writers",
            "docutils.writers.html4css1",
        ]
    )

    # TwitchAPI dependencies
    hidden_imports.extend(
        [
            "twitchAPI",
            "twitchAPI.twitch",
            "twitchAPI.oauth",
            "twitchAPI.helper",
            "twitchAPI.eventsub",
            "twitchAPI.eventsub.websocket",
            "twitchAPI.object.eventsub",
            "twitchAPI.type",
        ]
    )

    # Flask and SocketIO (CRITICAL for web engine)
    hidden_imports.extend(
        [
            "flask",
            "flask_socketio",
            "socketio",
            "engineio",
            "python_socketio",
            "python_engineio",
            "werkzeug",
            "werkzeug.serving",
            "werkzeug.middleware.proxy_fix",
            "werkzeug.routing",
            "werkzeug.exceptions",
            "werkzeug.wrappers",
        ]
    )

    # EngineIO and SocketIO async drivers
    hidden_imports.extend(
        [
            "engineio.async_drivers",
            "engineio.async_drivers.threading",
            "engineio.async_drivers.gevent",
            "socketio.async_handlers",
            "socketio.async_namespace",
            "socketio.server",
            "socketio.client",
            "socketio.namespace",
            "socketio.base_manager",
            "socketio.pubsub_manager",
            "engineio.server",
            "engineio.socket",
            "engineio.packet",
            "engineio.payload",
            "engineio.client",
        ]
    )

    # Networking and async libraries (SocketIO async_mode is gevent)
    hidden_imports.extend(
        [
            "gevent",
            "gevent.socket",
            "gevent.threading",
            "gevent.pywsgi",
        ]
    )

    # NiceGUI 3.x lazy-loads elements via ui.__getattr__; collect all submodules for frozen builds.
    try:
        from PyInstaller.utils.hooks import collect_submodules

        hidden_imports.extend(collect_submodules("nicegui"))
    except ImportError:
        pass

    # DNS resolution (dnspython dynamically imports submodules)
    try:
        from PyInstaller.utils.hooks import collect_submodules

        hidden_imports.extend(collect_submodules("dns"))
    except ImportError:
        hidden_imports.extend(
            [
                "dns",
                "dns.asyncbackend",
                "dns.asyncquery",
                "dns.asyncresolver",
                "dns.btree",
                "dns.btreezone",
                "dns.e164",
                "dns.namedict",
                "dns.tsigkeyring",
                "dns.versioned",
                "dns.dnssec",
            ]
        )

    # HTTP and networking
    hidden_imports.extend(
        [
            "aiohttp",
            "aiohttp.client",
            "aiohttp.web",
            "aiohttp.connector",
            "aiohttp.client_exceptions",
            "aiohttp.client_reqrep",
            "aiohttp.helpers",
            "aiohttp.http",
            "requests",
            "urllib3",
            "urllib3.exceptions",
            "urllib3.response",
            "urllib3.util",
            "urllib3.util.retry",
            "websockets",
            "websocket",
            "websocket_client",
            "ssl",
            "certifi",
        ]
    )

    # Database drivers
    hidden_imports.extend(
        [
            "sqlite3",
            "firebase_admin",
            "firebase_admin.db",
            "firebase_admin.credentials",
            "google.auth",
            "google.auth.transport",
            "google.auth.transport.requests",
            "pymongo",
            "bson",
        ]
    )

    # Audio and multimedia
    hidden_imports.extend(
        [
            "obsws_python",
        ]
    )

    # PSN API
    hidden_imports.extend(
        [
            "psnawp",
            "psnawp_api",
        ]
    )

    # Crypto and security
    hidden_imports.extend(
        [
            "cryptography",
            "cryptography.fernet",
            "cryptography.hazmat",
            "cryptography.hazmat.primitives",
            "cryptography.hazmat.backends",
        ]
    )

    # Utilities
    hidden_imports.extend(
        [
            "pyperclip",
            "psutil",
            "packaging",
            "dataclasses",
        ]
    )

    # Threading and multiprocessing
    hidden_imports.extend(
        [
            "multiprocessing",
            "multiprocessing.shared_memory",
            "threading",
            "asyncio",
            "concurrent.futures",
        ]
    )

    # Standard library modules
    hidden_imports.extend(
        [
            "json",
            "pathlib",
            "logging",
            "logging.handlers",
            "signal",
            "time",
            "datetime",
            "uuid",
            "webbrowser",
            "http.server",
            "socketserver",
            "urllib.parse",
            "typing",
            "enum",
            "copy",
            "glob",
            "os",
            "sys",
        ]
    )

    # Encoding modules (CRITICAL for Windows emoji support)
    if current_os == "windows":
        hidden_imports.extend(
            [
                "encodings",
                "encodings.utf_8",
                "encodings.cp1252",
                "codecs",
            ]
        )

    # ============================================================================
    # PLATFORM-SPECIFIC IMPORTS
    # ============================================================================

    if current_os == "windows":
        # Windows-specific audio control
        hidden_imports.extend(
            [
                "pycaw",
                "pycaw.pycaw",
                "pycaw.constants",
                "pycaw.utils",
                "comtypes",
                "comtypes.automation",
                "comtypes.client",
                "comtypes.server",
                "comtypes.typeinfo",
                "comtypes.connectionpoints",
                "comtypes.persist",
                "comtypes.shell",
                "comtypes.messageloop",
            ]
        )

    elif current_os == "linux":
        # Linux-specific audio control
        hidden_imports.extend(
            [
                "pulsectl",
                "pulsectl_asyncio",
            ]
        )

    elif current_os == "macos":
        # macOS-specific imports (none currently)
        pass

    # System tray. pystray picks its backend at import time by probing the desktop,
    # so PyInstaller cannot see which submodule is used and every candidate for this
    # platform has to be named explicitly.
    hidden_imports.extend(
        ["pystray", "pystray._base", "pystray._info", "pystray._util", "pystray._dummy"]
    )
    if current_os == "windows":
        hidden_imports.extend(["pystray._win32", "pystray._util.win32"])
    elif current_os == "macos":
        hidden_imports.append("pystray._darwin")
    elif current_os == "linux":
        hidden_imports.extend(
            [
                "pystray._appindicator",
                "pystray._gtk",
                "pystray._xorg",
                "pystray._util.gtk",
                "pystray._util.notify_dbus",
                "gi",
                "gi.repository",
            ]
        )

    # ============================================================================
    # MYCELIAN MODULE IMPORTS (all platforms)
    # ============================================================================

    hidden_imports.extend(
        [
            "modules.alertutils",
            "modules.alert_processor",
            "modules.database_manager",
            "modules.database_init",
            "modules.dataobjects",
            "modules.mainuiwindow",
            "modules.psnapi",
            "modules.psn_service",
            "modules.spotify",
            "modules.template_config_parser",
            "modules.twitch",
            "modules.web_engine",
            "modules.encryption_utils",
            "modules.config_manager",
            "modules.api_credentials_manager",
            "modules.status_manager",
            "modules.updater",
            "modules.update_sync",
            "merge_template_configs",
            "modules.uiwindows.activity_feed",
            "modules.uiwindows.alertsettings",
            "modules.uiwindows.customsources",
            "modules.uiwindows.settings",
            "modules.uiwindows.sourcecontrols",
            "modules.alerts_parser",
            "modules.autostart",
            "modules.system_notify",
            "modules.tray_controller",
            # Spawn target for the tray child; nothing imports it at module scope.
            "modules.tray_process",
            # Footer reads copy progress without importing customsources.
            "modules.template_copy_progress",
        ]
    )

    # Cross-platform input control
    hidden_imports.extend(
        [
            "pynput",
            "pynput.keyboard",
            "pynput.mouse",
        ]
    )

    return list(dict.fromkeys(hidden_imports))


def get_data_files():
    """Get list of data files to include"""
    import nicegui

    data_files = []

    # Include COMPREHENSIVE NiceGUI files (required for UI to work)
    nicegui_path = os.path.dirname(nicegui.__file__)

    # Include NiceGUI static files (CSS, JS, fonts, UnoCSS utilities, Quasar, etc.)
    nicegui_static = os.path.join(nicegui_path, "static")
    if os.path.exists(nicegui_static):
        data_files.append((nicegui_static, "nicegui/static"))

    # Include NiceGUI templates
    nicegui_templates = os.path.join(nicegui_path, "templates")
    if os.path.exists(nicegui_templates):
        data_files.append((nicegui_templates, "nicegui/templates"))

    # CRITICAL: Include NiceGUI elements directory (contains JS files for select, input, etc.)
    nicegui_elements = os.path.join(nicegui_path, "elements")
    if os.path.exists(nicegui_elements):
        data_files.append((nicegui_elements, "nicegui/elements"))

    # CRITICAL: Include NiceGUI functions directory
    nicegui_functions = os.path.join(nicegui_path, "functions")
    if os.path.exists(nicegui_functions):
        data_files.append((nicegui_functions, "nicegui/functions"))

    # Include NiceGUI app directory
    nicegui_app = os.path.join(nicegui_path, "app")
    if os.path.exists(nicegui_app):
        data_files.append((nicegui_app, "nicegui/app"))

    # Include NiceGUI native directory
    nicegui_native = os.path.join(nicegui_path, "native")
    if os.path.exists(nicegui_native):
        data_files.append((nicegui_native, "nicegui/native"))

    # # CRITICAL: Include project templates (needed for web engine)
    # project_root = get_project_root()
    # project_templates = project_root / 'templates'
    # if project_templates.exists():
    #     data_files.append((str(project_templates), 'templates'))
    #     print(f"Including project templates: {project_templates}")

    # # CRITICAL: Include project static assets (needed for web engine)
    # project_static = project_root / 'static'
    # if project_static.exists():
    #     data_files.append((str(project_static), 'static'))
    #     print(f"Including project static: {project_static}")

    # # Include project assets directory
    # project_assets = project_root / 'assets'
    # if project_assets.exists():
    #     data_files.append((str(project_assets), 'assets'))
    #     print(f"Including project assets: {project_assets}")

    # Assets normally ship beside the executable, but the tray icon has to resolve even
    # if that folder is missing or the app was moved on its own; without an image there
    # is no tray icon and no way back from a minimized window. get_assets_path falls
    # back to _MEIPASS, so bundling just the icons covers it.
    project_icons = get_project_root() / "assets" / "default_assets" / "icons"
    if project_icons.is_dir():
        data_files.append((str(project_icons), "assets/default_assets/icons"))
        print(f"Including application icons: {project_icons}")

    factorio_mod = get_project_root() / "factorio_mod"
    if factorio_mod.is_dir():
        data_files.append((str(factorio_mod), "factorio_mod"))
        print(f"Including Factorio companion mod: {factorio_mod}")

    return data_files


def get_excluded_modules():
    """Get list of modules to exclude from build (conservative exclusions only)"""
    excludes = [
        # Development tools only (safe to exclude)
        "pytest",
        "black",
        "flake8",
        "mypy",
        "pylint",
        "ruff",
        # Jupyter/IPython (safe to exclude)
        "IPython",
        "jupyter",
        "notebook",
        # Documentation tools (but keep docutils!)
        "sphinx",
        # Testing frameworks (safe to exclude)
        "nose",
        "coverage",
        # Version control (safe to exclude)
        "git",
        "mercurial",
        "tkinter",  # We use NiceGUI, not tkinter
        # Large scientific libraries we don't use (safe to exclude)
        "tensorflow",
        "torch",
        "sklearn",
        "matplotlib",  # Not used in this project
        "numpy",  # Not used directly
        "pandas",  # Not used in this project
        "scipy",  # Not used in this project
        # Game development (safe to exclude)
        "pygame",
        "pygame.mixer",
        # Specific PIL components we don't need
        "PIL.ImageQt",  # Qt-specific PIL components
    ]

    # Windows/macOS use WebView2/Cocoa; Qt is unused there. On Linux, pywebview
    # needs PyQt6 (+ WebEngine) for the native window — do not exclude it.
    if CURRENT_OS in ("windows", "macos"):
        excludes.extend(
            [
                "PyQt5",
                "PyQt6",
                "PySide2",
                "PySide6",
            ]
        )

    return excludes


def create_runtime_hook():
    """Create a runtime hook to help with DLL loading and encoding"""
    project_root = get_project_root()
    hook_content = '''
# Runtime hook to help with DLL loading and encoding issues
import os
import sys

def ensure_dll_path():
    """Ensure DLL paths are properly set"""
    if hasattr(sys, '_MEIPASS'):
        # Running as PyInstaller bundle
        bundle_dir = sys._MEIPASS

        # Add bundle directory to DLL search path
        if hasattr(os, 'add_dll_directory'):
            try:
                os.add_dll_directory(bundle_dir)
            except (OSError, AttributeError):
                pass

        # Also try adding to PATH
        current_path = os.environ.get('PATH', '')
        if bundle_dir not in current_path:
            os.environ['PATH'] = bundle_dir + os.pathsep + current_path

def ensure_utf8_encoding():
    """Ensure UTF-8 encoding for console output"""
    if hasattr(sys, '_MEIPASS'):
        # Force UTF-8 encoding for console output in PyInstaller bundle
        import io
        try:
            # Set stdout and stderr to use UTF-8
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except (AttributeError, io.UnsupportedOperation):
            # Fallback for older Python versions
            pass

        # Set environment variable for subprocesses
        os.environ['PYTHONIOENCODING'] = 'utf-8'

# Call the functions
ensure_dll_path()
ensure_utf8_encoding()
'''

    hook_path = project_root / "pyi_rth_dll_fix.py"
    with open(hook_path, "w", encoding="utf-8") as f:
        f.write(hook_content)

    return str(hook_path)


def create_spec_file():
    """Create custom spec file for PyInstaller"""
    project_root = get_project_root()

    # Create runtime hook
    runtime_hook_path = create_runtime_hook()

    # Get data files with proper paths
    data_files = get_data_files()

    # Prepare macOS info_plist if needed
    macos_info_plist = ""
    if CURRENT_OS == "macos":
        macos_info_plist = f""",
    info_plist={{
        'CFBundleDisplayName': 'Mycelian',
        'CFBundleIdentifier': 'com.mycelian.app',
        'CFBundleVersion': '{VERSION}',
        'CFBundleShortVersionString': '{VERSION}',
        'LSBackgroundOnly': False,
        'LSUIElement': False,
        'NSHighResolutionCapable': True,
    }}"""

    # Prepare binaries list for platform-specific requirements
    python_dll_binaries = []
    if CURRENT_OS == "windows":
        major, minor = sys.version_info[:2]
        versioned_dll_name = f"python{major}{minor}.dll"
        stable_dll_name = f"python{major}.dll"
        python_dll_names = (versioned_dll_name, stable_dll_name)
        vcruntime_dll_names = (
            "VCRUNTIME140.dll",
            "VCRUNTIME140_1.dll",
            "msvcp140.dll",
        )

        search_roots = []
        seen_roots = set()
        for raw in (
            getattr(sys, "base_prefix", None),
            getattr(sys, "base_exec_prefix", None),
            sysconfig.get_config_var("installed_base"),
            sys.prefix,
            Path(sys.executable).parent,
        ):
            if not raw:
                continue
            root = Path(raw)
            try:
                key = str(root.resolve()).lower()
            except OSError:
                key = str(root).lower()
            if key in seen_roots:
                continue
            seen_roots.add(key)
            search_roots.append(root)

        candidate_dirs = []
        seen_dirs = set()
        for root in search_roots:
            for directory in (root, root / "DLLs"):
                if not directory.is_dir():
                    continue
                try:
                    key = str(directory.resolve()).lower()
                except OSError:
                    key = str(directory).lower()
                if key in seen_dirs:
                    continue
                seen_dirs.add(key)
                candidate_dirs.append(directory)

        collected = set()
        versioned_dll_found = False

        def add_binary(dll_path: Path) -> bool:
            try:
                key = str(dll_path.resolve()).lower()
            except OSError:
                key = str(dll_path).lower()
            if key in collected:
                return False
            collected.add(key)
            python_dll_binaries.append((str(dll_path), "."))
            return True

        for directory in candidate_dirs:
            for dll_name in python_dll_names:
                dll_path = directory / dll_name
                if dll_path.is_file() and add_binary(dll_path):
                    progress.update(f"Including Python DLL: {dll_path}")
                    if dll_name.lower() == versioned_dll_name.lower():
                        versioned_dll_found = True
            for dll_name in vcruntime_dll_names:
                dll_path = directory / dll_name
                if dll_path.is_file() and add_binary(dll_path):
                    progress.update(f"Including runtime DLL: {dll_path}")

        if not versioned_dll_found:
            progress.update("Warning: Python DLL not found - may cause runtime issues")

    spec_content = f"""# -*- mode: python ; coding: utf-8 -*-

# Mycelian Improved PyInstaller spec file
# Auto-generated by build_improved.py

import sys
from pathlib import Path

# Project root
PROJECT_ROOT = Path(r'{project_root}')

# Current OS for conditional settings
CURRENT_OS = '{CURRENT_OS}'

# Data files (includes NiceGUI assets only)
datas = {data_files!r}

# Hidden imports (includes all necessary web engine dependencies)
hiddenimports = {get_hidden_imports(CURRENT_OS)!r}

# Excluded modules (conservative exclusions)
excludes = {get_excluded_modules()!r}

# Binary includes (platform specific) - ensure Python DLL is included
binaries = {python_dll_binaries!r}

# Runtime hooks (helps with DLL loading)
runtime_hooks = [r'{runtime_hook_path}']

# Analysis
a = Analysis(
    ['main.py'],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=runtime_hooks,
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

# Remove duplicate entries
a.datas = list(set(a.datas))

# PYZ archive
pyz = PYZ(a.pure, a.zipped_data, cipher=None)

# Executable - macOS uses COLLECT + BUNDLE pattern for proper .app creation
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # Exclude binaries from EXE, they'll be in COLLECT
    name='Mycelian',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    console=False,  # Hide terminal/console on all platforms
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon={repr(str(project_root / ICON_PATH) if ICON_PATH else None)},
    version={repr(str(project_root / VERSION_FILE) if VERSION_FILE and CURRENT_OS == "windows" else None)}{macos_info_plist},
)

# For macOS: Use COLLECT + BUNDLE pattern for proper .app bundle creation
if CURRENT_OS == "macos":
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name='Mycelian'
    )

    app = BUNDLE(
        coll,
        name='Mycelian.app',
        icon=None,
        bundle_identifier='com.mycelian.app'
    )
else:
    # For Windows/Linux: Use EXE with onefile=True for single executable
    exe_final = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        exclude_binaries=False,
        name='Mycelian',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon={repr(str(project_root / ICON_PATH) if ICON_PATH else None)},
        version={repr(str(project_root / VERSION_FILE) if VERSION_FILE and CURRENT_OS == "windows" else None)},
        manifest=None,
        onefile=True,
    )
"""

    spec_path = project_root / "mycelian_improved.spec"
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write(spec_content)

    return spec_path


def build_executable():
    """Build the executable using PyInstaller"""
    project_root = get_project_root()

    # Create spec file
    spec_path = create_spec_file()

    # Build command
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",  # Clean PyInstaller cache
        "--noconfirm",  # Overwrite output directory
        str(
            spec_path
        ),  # Spec file contains all necessary settings (console=False, onefile=False for macOS)
    ]

    # Add custom output path if specified
    if OUTPUT_PATH:
        output_path = Path(OUTPUT_PATH)
        if not output_path.is_absolute():
            output_path = project_root / output_path
        # Create the builds directory if it doesn't exist
        output_path.mkdir(parents=True, exist_ok=True)
        cmd.extend(["--distpath", str(output_path)])
        dist_dir = output_path
    else:
        dist_dir = project_root / "dist"

    # Run PyInstaller
    result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True)

    if result.returncode == 0:
        progress.success("Build completed successfully")
        if dist_dir.exists():
            # OS-specific executable naming
            exe_names = {
                "windows": "Mycelian.exe",
                "linux": "Mycelian",
                "macos": "Mycelian.app",  # macOS creates .app bundle
            }
            exe_name = exe_names.get(CURRENT_OS, "Mycelian")
            exe_path = dist_dir / exe_name
            if exe_path.exists():
                if CURRENT_OS == "macos" and exe_path.is_dir():
                    # macOS .app bundle - calculate total size of all files
                    total_size = sum(
                        f.stat().st_size for f in exe_path.rglob("*") if f.is_file()
                    ) / (1024 * 1024)
                    progress.update(f"Compiled {exe_name} ({total_size:.1f} MB)")
                    progress.update(
                        "Note: Double-click in Finder to run without terminal"
                    )
                else:
                    # Single file executable
                    size_mb = exe_path.stat().st_size / (1024 * 1024)
                    progress.update(f"Compiled {exe_name} ({size_mb:.1f} MB)")
                    if CURRENT_OS == "macos":
                        progress.update(
                            "Note: Double-click in Finder to run without terminal"
                        )
        return True, dist_dir
    else:
        progress.error("Build failed")
        if result.stderr:
            progress.update(f"Error: {result.stderr.strip()}")
        return False, None

    return result.returncode == 0, dist_dir


def create_build_info(dist_dir):
    """Create build information file"""
    from datetime import datetime

    build_info = {
        "build_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "python_version": sys.version,
        "platform": sys.platform,
        "build_method": "improved_pyinstaller",
        "pyinstaller_version": None,
    }

    try:
        import PyInstaller

        build_info["pyinstaller_version"] = PyInstaller.__version__
    except ImportError:
        pass

    # Write build info
    build_info_path = dist_dir / "build_info.txt"
    if build_info_path.parent.exists():
        with open(build_info_path, "w") as f:
            for key, value in build_info.items():
                f.write(f"{key}: {value}\n")


def post_build_tasks(dist_dir):
    """Perform post-build tasks"""
    if not dist_dir or not dist_dir.exists():
        progress.update("No output directory found")
        return

    # Calculate total size
    if dist_dir.exists():
        total_size = sum(
            f.stat().st_size for f in dist_dir.rglob("*") if f.is_file()
        ) / (1024 * 1024)
        progress.update(f"Total build size: {total_size:.1f} MB")


def update_version_across_files():
    """Update version and build date across all relevant project files"""
    global BUILD_NUMBER
    project_root = get_project_root()

    sys.path.insert(0, str(project_root))
    from modules.build_info import get_git_commit

    BUILD_NUMBER = get_git_commit(project_root) or "dev"

    files_updated = []

    try:
        # 1. Update pyproject.toml
        pyproject_path = project_root / "pyproject.toml"
        if pyproject_path.exists():
            with open(pyproject_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Update version line
            import re

            content = re.sub(
                r'^version = ".*"',
                f'version = "{VERSION}"',
                content,
                flags=re.MULTILINE,
            )

            with open(pyproject_path, "w", encoding="utf-8") as f:
                f.write(content)
            files_updated.append("pyproject.toml")

        # 2. Update uv.lock
        uv_lock_path = project_root / "uv.lock"
        if uv_lock_path.exists():
            with open(uv_lock_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Find and update the mycelian package version line
            for i, line in enumerate(lines):
                if 'name = "mycelian"' in line:
                    # Look for version line in the next few lines
                    for j in range(i + 1, min(i + 10, len(lines))):
                        if lines[j].strip().startswith('version = "'):
                            lines[j] = f'version = "{VERSION}"\n'
                            break
                    break

            with open(uv_lock_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            files_updated.append("uv.lock")

        # 3. Update modules/database_init.py
        db_init_path = project_root / "modules" / "database_init.py"
        if db_init_path.exists():
            with open(db_init_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Update version and build_date lines
            content = re.sub(r"'version': '.*',", f"'version': '{VERSION}',", content)
            content = re.sub(
                r"'build_date': '.*',", f"'build_date': '{BUILD_DATE}',", content
            )
            content = re.sub(
                r"'build_number': '.*',", f"'build_number': '{BUILD_NUMBER}',", content
            )

            with open(db_init_path, "w", encoding="utf-8") as f:
                f.write(content)
            files_updated.append("modules/database_init.py")

        # 4. Update modules/dataobjects.py
        dataobjects_path = project_root / "modules" / "dataobjects.py"
        if dataobjects_path.exists():
            with open(dataobjects_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Update version and build_date lines in AppSettings class
            content = re.sub(
                r'version: str = ".*"', f'version: str = "{VERSION}"', content
            )
            content = re.sub(
                r'build_date: str = ".*"', f'build_date: str = "{BUILD_DATE}"', content
            )
            content = re.sub(
                r'build_number: str = ".*"',
                f'build_number: str = "{BUILD_NUMBER}"',
                content,
            )

            with open(dataobjects_path, "w", encoding="utf-8") as f:
                f.write(content)
            files_updated.append("modules/dataobjects.py")

        # 5. Update version.txt (Windows version info file)
        if VERSION_FILE:
            version_file_path = project_root / VERSION_FILE
            if version_file_path.exists():
                with open(version_file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # Convert version string (e.g. "1.0.6") to tuple format (1,0,6,0)
                version_parts = VERSION.split(".")
                # Pad with zeros to ensure we have 4 parts
                while len(version_parts) < 4:
                    version_parts.append("0")
                version_tuple = f"({','.join(version_parts[:4])})"
                version_string = ".".join(version_parts[:4])

                # Update filevers and prodvers tuples
                content = re.sub(
                    r"filevers=\([^)]+\)", f"filevers={version_tuple}", content
                )
                content = re.sub(
                    r"prodvers=\([^)]+\)", f"prodvers={version_tuple}", content
                )

                # Update StringStruct version entries
                content = re.sub(
                    r"StringStruct\(u'FileVersion', u'[^']+'\)",
                    f"StringStruct(u'FileVersion', u'{version_string}')",
                    content,
                )
                content = re.sub(
                    r"StringStruct\(u'ProductVersion', u'[^']+'\)",
                    f"StringStruct(u'ProductVersion', u'{version_string}')",
                    content,
                )

                with open(version_file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                files_updated.append(VERSION_FILE)

        # 6. Update Inno Setup script (Mycelian.iss)
        iss_path = project_root / "Mycelian.iss"
        if iss_path.exists():
            with open(iss_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Update MyAppVersion definition
            content = re.sub(
                r'#define MyAppVersion ".*"',
                f'#define MyAppVersion "{VERSION}"',
                content,
            )

            with open(iss_path, "w", encoding="utf-8") as f:
                f.write(content)
            files_updated.append("Mycelian.iss")

        return True

    except Exception as e:
        print(f"Error updating version across files: {e}")
        import traceback

        traceback.print_exc()
        return False


def app_binary_name(os_name=None):
    """PyInstaller output name for the Mycelian application."""
    os_name = os_name or CURRENT_OS
    if os_name == "windows":
        return "Mycelian.exe"
    if os_name == "macos":
        return "Mycelian.app"
    return "Mycelian"


def merge_helper_name(os_name=None):
    """PyInstaller output name for the template config merge helper."""
    os_name = os_name or CURRENT_OS
    if os_name == "windows":
        return "merge_template_configs.exe"
    return "merge_template_configs"


def _load_payload_module():
    """Load packaging/payload.py without importing the third-party packaging package."""
    import importlib.util

    path = get_project_root() / "packaging" / "payload.py"
    spec = importlib.util.spec_from_file_location("mycelian_install_payload", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load installer payload helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _copy_build_artifact(src: Path, dest: Path) -> None:
    """Overwrite dest with a file or directory produced by PyInstaller."""
    if not src.exists():
        raise FileNotFoundError(f"Build output missing: {src}")
    if src.is_dir():
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    if CURRENT_OS != "windows":
        dest.chmod(dest.stat().st_mode | 0o755)


def installer_output_dir(project_root: Path) -> Path:
    """Folder Inno Setup already uses for finished installers."""
    output_dir = project_root / "Output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def copy_binaries_to_project_root(project_root: Path, dist_dir: Path):
    """Copy the app and merge helper into the project root, then remove the dist copies."""
    app_dest = project_root / app_binary_name()
    helper_dest = project_root / merge_helper_name()
    _copy_build_artifact(dist_dir / app_binary_name(), app_dest)
    _copy_build_artifact(dist_dir / merge_helper_name(), helper_dest)
    for name in (app_binary_name(), merge_helper_name()):
        src = dist_dir / name
        dest = project_root / name
        if not src.exists():
            continue
        try:
            same_file = src.resolve() == dest.resolve()
        except OSError:
            same_file = False
        if same_file:
            continue
        if src.is_dir():
            shutil.rmtree(src)
        else:
            src.unlink()
    return app_dest, helper_dest


# GitHub rejects blobs at or above 100 MB.
GITHUB_MAX_FILE_BYTES = 100 * 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_entry(
    src: Path,
    repo_path: str,
    install_path: str,
    action: str,
    *,
    os_name: str | None = None,
    bundle: bool = False,
) -> dict:
    size = src.stat().st_size
    if size >= GITHUB_MAX_FILE_BYTES:
        raise RuntimeError(
            f"{src} is {size / (1024 * 1024):.1f} MB. "
            "GitHub rejects files over 100 MB, so this build cannot be "
            "published for the file updater."
        )
    entry = {
        "path": repo_path.replace("\\", "/"),
        "install": install_path.replace("\\", "/"),
        "sha256": _sha256_file(src),
        "size": size,
        "action": action,
    }
    if os_name:
        entry["os"] = os_name
    if bundle:
        entry["bundle"] = True
    return entry


def _binary_manifest_entries(project_root: Path) -> list:
    """Hash the compiled app just copied under ``packaged/<os>/``."""
    os_dir = project_root / "packaged" / CURRENT_OS
    if CURRENT_OS == "macos":
        base = os_dir / "Mycelian.app"
        repo_prefix = "packaged/macos/Mycelian.app"
        bundle = True
    else:
        base = os_dir
        repo_prefix = f"packaged/{CURRENT_OS}"
        bundle = False
    if not base.exists():
        raise FileNotFoundError(f"Packaged binary missing: {base}")

    entries = []
    files = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file())
    for path in files:
        if path.name == ".DS_Store" or "__pycache__" in path.parts:
            continue
        rel = path.name if base.is_file() else path.relative_to(base).as_posix()
        repo_path = f"{repo_prefix}/{rel}"
        entries.append(
            _manifest_entry(
                path,
                repo_path,
                rel,
                "replace",
                os_name=CURRENT_OS,
                bundle=bundle,
            )
        )
    if not entries:
        raise FileNotFoundError(f"Packaged binary directory is empty: {base}")
    return entries


def _load_manifest_files(manifest_path: Path) -> list:
    if not manifest_path.is_file():
        return []
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    files = data.get("files") if isinstance(data, dict) else None
    return files if isinstance(files, list) else []


def write_update_manifest(project_root: Path) -> Path:
    """Rewrite ``packaged/manifest.json`` for this OS without dropping other platforms."""
    payload = _load_payload_module()
    entries = []
    for relative, action in payload.iter_payload_files(project_root):
        src = project_root / Path(relative)
        entries.append(_manifest_entry(src, relative, relative, action))
    entries.extend(_binary_manifest_entries(project_root))

    manifest_path = project_root / "packaged" / "manifest.json"
    kept = []
    for entry in _load_manifest_files(manifest_path):
        if not isinstance(entry, dict):
            continue
        entry_os = entry.get("os")
        if entry_os and entry_os != CURRENT_OS:
            kept.append(entry)
    files = entries + kept
    files.sort(key=lambda item: (str(item.get("os") or ""), str(item.get("path") or "")))
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"version": VERSION, "files": files}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest_path


def publish_packaged_release(project_root: Path, app_path: Path) -> Path:
    """Copy this OS binary into ``packaged/`` and refresh the updater manifest."""
    os_dir = project_root / "packaged" / CURRENT_OS
    if os_dir.exists():
        shutil.rmtree(os_dir)
    os_dir.mkdir(parents=True, exist_ok=True)
    if CURRENT_OS == "macos":
        dest = os_dir / "Mycelian.app"
    else:
        dest = os_dir / app_binary_name()
    _copy_build_artifact(app_path, dest)
    manifest_path = write_update_manifest(project_root)
    progress.update(f"Updater manifest: {manifest_path}")
    return manifest_path


def build_merge_helper(project_root: Path, dist_dir: Path) -> bool:
    """Build the template config merge tool as a one-file executable."""
    work = project_root / "build" / "merge_template_configs"
    work.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "merge_template_configs",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work),
        "--specpath",
        str(work),
        str(project_root / "merge_template_configs.py"),
    ]
    result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True)
    helper = dist_dir / merge_helper_name()
    if result.returncode != 0 or not helper.exists():
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            progress.update(detail[-2000:])
        return False
    return True


def find_iscc() -> Path:
    """Locate the Inno Setup command-line compiler."""
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return Path(found)
    candidates = []
    for env_name in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(env_name)
        if root:
            candidates.append(Path(root) / "Inno Setup 6" / "ISCC.exe")
    candidates.extend(
        [
            Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
            Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        ]
    )
    seen = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "Inno Setup compiler (ISCC.exe) was not found. "
        "Install Inno Setup 6 and add ISCC.exe to PATH, "
        "or install it in Program Files\\Inno Setup 6."
    )


def build_inno_setup(project_root: Path) -> Path:
    """Compile Mycelian.iss. The script already runs the config merge helper."""
    iscc = find_iscc()
    output_dir = installer_output_dir(project_root)
    iss_path = project_root / "Mycelian.iss"
    if not iss_path.is_file():
        raise FileNotFoundError(f"Inno Setup script not found: {iss_path}")
    progress.update(f"Running {iscc}")
    result = subprocess.run(
        [str(iscc), f"/O{output_dir}", str(iss_path)],
        cwd=project_root,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Inno Setup failed with exit code {result.returncode}")
    installer = output_dir / f"Mycelian v{VERSION}.exe"
    if not installer.is_file():
        matches = sorted(output_dir.glob("Mycelian v*.exe"))
        if not matches:
            raise FileNotFoundError(
                f"Inno Setup finished but no installer was found in {output_dir}"
            )
        installer = matches[-1]
    return installer


def _linux_is_arch() -> bool:
    """True for Arch Linux and derivatives such as CachyOS."""
    os_release = Path("/etc/os-release")
    if not os_release.is_file():
        return False
    values = {}
    for line in os_release.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    distro_id = values.get("ID", "")
    like = values.get("ID_LIKE", "").split()
    return distro_id in {"arch", "cachyos"} or "arch" in like


def _arch_package_arch() -> str:
    machine = platform.machine().lower()
    return {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
        "i686": "i686",
    }.get(machine, machine)


def _replace_pkgbuild_line(text: str, key: str, new_line: str) -> str:
    lines = []
    found = False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(key):
            ending = "\n" if line.endswith("\n") else ""
            lines.append(new_line + ending)
            found = True
        else:
            lines.append(line)
    if not found:
        raise RuntimeError(f"PKGBUILD is missing a {key!r} line")
    return "".join(lines)


def _normalize_lf(path: Path) -> None:
    data = path.read_bytes()
    normalized = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if normalized != data:
        path.write_bytes(normalized)


def build_arch_package(project_root: Path) -> Path:
    """Build a pacman package. Install it with ``sudo pacman -U``."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise RuntimeError(
            "makepkg cannot be run as root. Re-run build.py as a normal user."
        )
    if not _linux_is_arch():
        raise RuntimeError(
            "Linux installers are built as an Arch pacman package. "
            "This machine does not look like Arch or an Arch derivative."
        )
    makepkg = shutil.which("makepkg")
    if makepkg is None:
        raise RuntimeError(
            "makepkg was not found. Install the base-devel package group."
        )

    arch_dir = project_root / "packaging" / "arch"
    pkgbuild = arch_dir / "PKGBUILD"
    text = pkgbuild.read_text(encoding="utf-8")
    text = _replace_pkgbuild_line(text, "pkgver=", f"pkgver={VERSION}")
    text = _replace_pkgbuild_line(
        text, "arch=", f"arch=('{_arch_package_arch()}')"
    )
    pkgbuild.write_text(text, encoding="utf-8", newline="\n")
    for script_name in ("PKGBUILD", "mycelian.install", "mycelian.sh"):
        _normalize_lf(arch_dir / script_name)

    payload = arch_dir / "payload"
    if payload.exists():
        shutil.rmtree(payload)
    _load_payload_module().stage_install_payload(payload, project_root)
    for name in (app_binary_name(), merge_helper_name()):
        _copy_build_artifact(project_root / name, payload / name)

    output_dir = installer_output_dir(project_root)
    env = os.environ.copy()
    env["PKGDEST"] = str(output_dir)
    progress.update("Running makepkg")
    result = subprocess.run(
        [makepkg, "-f", "--noconfirm"],
        cwd=arch_dir,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"makepkg failed with exit code {result.returncode}")

    packages = sorted(
        output_dir.glob(f"mycelian-{VERSION}-*.pkg.tar.*"),
        key=lambda path: path.stat().st_mtime,
    )
    if not packages:
        raise FileNotFoundError(
            f"makepkg finished but no package was found in {output_dir}"
        )
    return packages[-1]


def _macos_host_arch() -> str:
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return "x86_64"


def build_macos_pkg(project_root: Path) -> Path:
    """Build a .pkg that installs Mycelian.app to /Applications."""
    pkgbuild = shutil.which("pkgbuild")
    productbuild = shutil.which("productbuild")
    if pkgbuild is None or productbuild is None:
        raise RuntimeError(
            "pkgbuild and productbuild were not found. "
            "Install the Xcode command line tools."
        )

    builds_dir = project_root / "builds"
    staging = builds_dir / "pkg-staging"
    if staging.exists():
        shutil.rmtree(staging)

    app_dest = staging / "root" / "Applications" / "Mycelian.app"
    shutil.copytree(project_root / "Mycelian.app", app_dest)
    macos_dir = app_dest / "Contents" / "MacOS"
    if not macos_dir.is_dir():
        raise FileNotFoundError(f"App bundle is missing Contents/MacOS: {app_dest}")

    _load_payload_module().stage_install_payload(macos_dir, project_root)
    _copy_build_artifact(
        project_root / merge_helper_name(),
        macos_dir / merge_helper_name(),
    )

    scripts = staging / "scripts"
    shutil.copytree(project_root / "packaging" / "macos" / "scripts", scripts)
    postinstall = scripts / "postinstall"
    _normalize_lf(postinstall)
    postinstall.chmod(0o755)

    host_arch = _macos_host_arch()
    distribution = staging / "distribution.xml"
    distribution.write_text(
        "\n".join(
            [
                '<?xml version="1.0" encoding="utf-8"?>',
                '<installer-gui-script minSpecVersion="2">',
                "    <title>Mycelian</title>",
                (
                    "    <options customize=\"never\" require-scripts=\"false\" "
                    f'hostArchitectures="{host_arch}"/>'
                ),
                "    <choices-outline>",
                '        <line choice="mycelian"/>',
                "    </choices-outline>",
                '    <choice id="mycelian" title="Mycelian" '
                'description="Install Mycelian">',
                '        <pkg-ref id="com.mycelian.app"/>',
                "    </choice>",
                f'    <pkg-ref id="com.mycelian.app" version="{VERSION}" '
                'auth="root">Mycelian-component.pkg</pkg-ref>',
                "</installer-gui-script>",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )

    component = staging / "Mycelian-component.pkg"
    progress.update("Running pkgbuild")
    component_result = subprocess.run(
        [
            pkgbuild,
            "--root",
            str(staging / "root"),
            "--identifier",
            "com.mycelian.app",
            "--version",
            VERSION,
            "--install-location",
            "/",
            "--scripts",
            str(scripts),
            str(component),
        ],
        cwd=staging,
    )
    if component_result.returncode != 0:
        raise RuntimeError(
            f"pkgbuild failed with exit code {component_result.returncode}"
        )

    installer = installer_output_dir(project_root) / f"Mycelian-{VERSION}.pkg"
    progress.update("Running productbuild")
    product_result = subprocess.run(
        [
            productbuild,
            "--distribution",
            str(distribution),
            "--package-path",
            str(staging),
            str(installer),
        ],
        cwd=staging,
    )
    if product_result.returncode != 0:
        raise RuntimeError(
            f"productbuild failed with exit code {product_result.returncode}"
        )
    shutil.rmtree(staging, ignore_errors=True)
    return installer


def build_installer(project_root: Path) -> Path:
    """Build the installer for the OS this script is running on."""
    if CURRENT_OS == "windows":
        return build_inno_setup(project_root)
    if CURRENT_OS == "linux":
        return build_arch_package(project_root)
    if CURRENT_OS == "macos":
        return build_macos_pkg(project_root)
    raise RuntimeError(f"No installer is configured for {CURRENT_OS}")


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Build Mycelian and the installer for this operating system"
    )
    parser.add_argument(
        "--skip-installer",
        action="store_true",
        help="Copy binaries into the project folder and skip the installer",
    )
    return parser.parse_args(argv)


def main():
    """Main build function"""
    args = _parse_args()
    progress.total_steps = 6 if args.skip_installer else 7

    print(f"Mycelian Build Script v{VERSION} - {CURRENT_OS.upper()}")
    print("=" * 50)

    # Check if we're in the right directory
    project_root = get_project_root()
    main_py = project_root / "main.py"
    if not main_py.exists():
        progress.error(f"main.py not found in {project_root}")
        progress.update("Please run this script from the project root directory")
        sys.exit(1)

    # macOS-specific requirements check
    if CURRENT_OS == "macos":
        progress.update("Checking macOS requirements...")
        try:
            import subprocess

            result = subprocess.run(
                ["xcode-select", "-p"], capture_output=True, text=True
            )
            if result.returncode != 0:
                progress.error("Xcode command line tools not found!")
                progress.update("Install with: xcode-select --install")
                progress.update("Then agree to license with: sudo xcodebuild -license")
                response = input("Continue anyway? (y/N): ").strip().lower()
                if response != "y":
                    progress.update("Build cancelled.")
                    sys.exit(1)
        except FileNotFoundError:
            progress.error("xcode-select command not found")
            progress.update("Please install Xcode command line tools")
            sys.exit(1)

    try:
        # Step 1: Ensure dependencies
        progress.next_step("Checking dependencies")
        ensure_dependencies()
        progress.success()

        # Step 2: Clean previous builds
        progress.next_step("Cleaning build artifacts")
        clean_build()
        progress.success()

        # Step 3: Update version across files
        progress.next_step("Updating version information")
        update_version_across_files()
        progress.success()

        # Step 4: Build Stream Deck plugin into sd_plugin for the installer
        progress.next_step("Building Stream Deck plugin for installer")
        if not build_streamdeck_plugin(project_root):
            progress.error("Stream Deck plugin build failed")
            sys.exit(1)
        progress.success()

        # Step 5: Build executable and the template config merge helper
        progress.next_step("Building executable")
        success, dist_dir = build_executable()
        if not success:
            progress.error("Build failed")
            sys.exit(1)
        progress.update("Building template config merge helper")
        if not build_merge_helper(project_root, dist_dir):
            progress.error("merge_template_configs build failed")
            sys.exit(1)
        progress.success()

        # Step 6: Copy binaries where the installers expect them
        progress.next_step("Copying binaries into the project folder")
        app_path, helper_path = copy_binaries_to_project_root(project_root, dist_dir)
        progress.update(f"App: {app_path}")
        progress.update(f"Merge helper: {helper_path}")
        progress.update("Publishing packaged binary for the updater")
        publish_packaged_release(project_root, app_path)
        post_build_tasks(dist_dir)
        progress.success()

        # Step 7: OS installer
        if args.skip_installer:
            progress.update("Installer skipped (--skip-installer)")
        else:
            progress.next_step("Building installer")
            installer_path = build_installer(project_root)
            progress.update(f"Installer: {installer_path}")
            progress.success()

        progress.summary()

    except KeyboardInterrupt:
        progress.error("Build interrupted by user")
        sys.exit(1)
    except Exception as e:
        progress.error(f"Build failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        # Clean up temporary files
        cleanup_count = 0
        for hook_file in project_root.glob("pyi_rth_*.py"):
            try:
                hook_file.unlink()
                cleanup_count += 1
            except Exception:
                pass
        if cleanup_count > 0:
            progress.update(f"Cleaned up {cleanup_count} temporary files")


if __name__ == "__main__":
    main()

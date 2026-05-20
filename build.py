#!/usr/bin/env python3
"""
Improved PyInstaller build script for Mycelian
Balances size optimization with proper NiceGUI and web engine support
"""

import os
import platform
import shutil
import subprocess
import sys
import time
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
progress = BuildProgress()


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
VERSION = "1.9.6"
BUILD_DATE = "May 20th 2026"

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
    """Ensure all required packages are installed"""
    try:
        import PyInstaller

        progress.update(f"PyInstaller v{PyInstaller.__version__} ready")
    except ImportError:
        progress.update("Installing PyInstaller...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"], check=True
        )
        progress.update("PyInstaller installed")


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
            "nicegui.globals",
            "nicegui.storage",
            "nicegui.version",
            "nicegui.binding",
            "nicegui.client",
            "nicegui.context",
            "nicegui.helpers",
            "nicegui.json",
            "nicegui.language",
            "nicegui.lifecycle",
            "nicegui.logging",
            "nicegui.observables",
            "nicegui.page",
            "nicegui.response",
            "nicegui.server",
            "nicegui.slot",
            "nicegui.tailwind",
            "nicegui.timer",
            "nicegui.favicon",
            "nicegui.error",
            "nicegui.nicegui",
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
            "nicegui.elements.tabs",
            "nicegui.elements.tab_panels",
            "nicegui.elements.tab",
            "nicegui.elements.tab_panel",
            "nicegui.elements.tree",
            "nicegui.elements.table",
            "nicegui.elements.separator",
            "nicegui.elements.space",
            "nicegui.elements.spinner",
            "nicegui.elements.menu",
            "nicegui.elements.menu_item",
            "nicegui.elements.menu_separator",
            "nicegui.elements.tooltip",
            "nicegui.elements.notify",
            "nicegui.elements.dialog",
            "nicegui.elements.banner",
            "nicegui.elements.knob",
            "nicegui.elements.joystick",
            "nicegui.elements.circular_progress",
            "nicegui.elements.linear_progress",
            "nicegui.elements.keyboard",
            "nicegui.elements.json_editor",
            "nicegui.elements.code",
            "nicegui.elements.log",
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
            "nicegui.page_layout",
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
            "engineio.async_drivers.eventlet",
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

    # Networking and async libraries
    hidden_imports.extend(
        [
            "eventlet",
            "eventlet.hubs",
            "eventlet.hubs.epolls",
            "eventlet.hubs.kqueue",
            "eventlet.hubs.selects",
            "eventlet.wsgi",
            "eventlet.green",
            "eventlet.green.threading",
            "eventlet.green.socket",
            "gevent",
            "gevent.socket",
            "gevent.threading",
            "gevent.pywsgi",
        ]
    )

    # DNS resolution
    hidden_imports.extend(
        [
            "dns",
            "dns.asyncbackend",
            "dns.asyncquery",
            "dns.asyncresolver",
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
            "requests_oauthlib",
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

    # Spotify
    hidden_imports.extend(
        [
            "spotipy",
            "spotipy.oauth2",
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
            "modules.uiwindows.activity_feed",
            "modules.uiwindows.alertsettings",
            "modules.uiwindows.customsources",
            "modules.uiwindows.settings",
            "modules.uiwindows.sourcecontrols",
            "modules.alerts_parser",
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

    return hidden_imports


def get_data_files():
    """Get list of data files to include"""
    import nicegui

    data_files = []

    # Include COMPREHENSIVE NiceGUI files (required for UI to work)
    nicegui_path = os.path.dirname(nicegui.__file__)

    # Include NiceGUI static files (CSS, JS, fonts, etc.)
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

    return data_files


def get_excluded_modules():
    """Get list of modules to exclude from build (conservative exclusions only)"""
    return [
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
        # Large unused GUI frameworks (safe to exclude)
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
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
        import glob

        python_install_path = Path(sys.executable).parent

        # Try multiple potential DLL locations
        dll_locations = [
            python_install_path / "python3*.dll",
            python_install_path / "python313.dll",
            python_install_path / "DLLs" / "python3*.dll",
            python_install_path.parent
            / "python3*.dll",  # Sometimes in parent directory
        ]

        python_dll_found = False
        for location_pattern in dll_locations:
            for dll_path in glob.glob(str(location_pattern)):
                if os.path.exists(dll_path):
                    python_dll_binaries.append((dll_path, "."))
                    python_dll_found = True
                    break
            if python_dll_found:
                break

        # Also add essential Windows DLLs that might be needed
        vcruntime_dlls = [
            python_install_path / "VCRUNTIME140.dll",
            python_install_path / "VCRUNTIME140_1.dll",
            python_install_path / "msvcp140.dll",
        ]

        for vcruntime_dll in vcruntime_dlls:
            if vcruntime_dll.exists():
                python_dll_binaries.append((str(vcruntime_dll), "."))

        if not python_dll_found:
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
                    progress.update(f"Output: {exe_path} ({total_size:.1f} MB)")
                    progress.update(
                        "Note: Double-click in Finder to run without terminal"
                    )
                else:
                    # Single file executable
                    size_mb = exe_path.stat().st_size / (1024 * 1024)
                    progress.update(f"Output: {exe_path} ({size_mb:.1f} MB)")
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
        progress.update(f"Output location: {dist_dir}")


def update_version_across_files():
    """Update version and build date across all relevant project files"""

    project_root = get_project_root()
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

        # 3. Update docs/conf.py
        conf_path = project_root / "docs" / "conf.py"
        if conf_path.exists():
            with open(conf_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Update release line
            content = re.sub(
                r"^release = '.*'",
                f"release = '{VERSION}'",
                content,
                flags=re.MULTILINE,
            )

            with open(conf_path, "w", encoding="utf-8") as f:
                f.write(content)
            files_updated.append("docs/conf.py")

        # 4. Update modules/database_init.py
        db_init_path = project_root / "modules" / "database_init.py"
        if db_init_path.exists():
            with open(db_init_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Update version and build_date lines
            content = re.sub(r"'version': '.*',", f"'version': '{VERSION}',", content)
            content = re.sub(
                r"'build_date': '.*',", f"'build_date': '{BUILD_DATE}',", content
            )

            with open(db_init_path, "w", encoding="utf-8") as f:
                f.write(content)
            files_updated.append("modules/database_init.py")

        # 5. Update modules/dataobjects.py
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

            with open(dataobjects_path, "w", encoding="utf-8") as f:
                f.write(content)
            files_updated.append("modules/dataobjects.py")

        # 6. Update version.txt (Windows version info file)
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

        # 7. Update Inno Setup script (Mycelian.iss)
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


def main():
    """Main build function"""
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

        # Step 4: Build executable
        progress.next_step("Building executable")
        success, dist_dir = build_executable()
        if success:
            # Step 5: Post-build tasks
            progress.next_step("Finalizing build")
            post_build_tasks(dist_dir)
            progress.success()

            progress.summary()
        else:
            progress.error("Build failed")
            sys.exit(1)

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

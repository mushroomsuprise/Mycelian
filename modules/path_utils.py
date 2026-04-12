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

import os
import sys
from pathlib import Path


def get_resource_path(relative_path):
    """
    Get absolute path to resource, works for both development and PyInstaller.
    
    Args:
        relative_path (str): Relative path to the resource file
        
    Returns:
        str: Absolute path to the resource
    """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        # In development, use the current working directory
        base_path = os.path.abspath(".")
    
    return os.path.join(base_path, relative_path)


def get_data_path(relative_path):
    """
    Get absolute path to data files, works for both development and PyInstaller.
    Data files are those that should persist and be writable (configs, databases, logs).
    
    Args:
        relative_path (str): Relative path to the data file
        
    Returns:
        str: Absolute path to the data file
    """
    if getattr(sys, 'frozen', False):
        # Running in a PyInstaller bundle
        # Data files should be relative to the executable location
        base_path = os.path.dirname(sys.executable)
    else:
        # Running in development
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    return os.path.join(base_path, relative_path)


def get_template_path(relative_path=""):
    """
    Get absolute path to template directory/files.
    
    Args:
        relative_path (str): Relative path within templates directory
        
    Returns:
        str: Absolute path to the template file/directory
    """
    if relative_path:
        return get_data_path(os.path.join("templates", relative_path))
    else:
        return get_data_path("templates")


def get_assets_path(relative_path=""):
    """
    Get absolute path to assets directory/files.
    
    Args:
        relative_path (str): Relative path within assets directory
        
    Returns:
        str: Absolute path to the assets file/directory
    """
    if getattr(sys, "frozen", False):
        exe_assets = os.path.join(os.path.dirname(sys.executable), "assets")
        meipass = getattr(sys, "_MEIPASS", None)
        bundle_assets = os.path.join(meipass, "assets") if meipass else None
        # Prefer assets next to the exe (overrides); else PyInstaller bundle under _MEIPASS
        for base in (exe_assets, bundle_assets):
            if not base or not os.path.isdir(base):
                continue
            full = os.path.join(base, relative_path) if relative_path else base
            if os.path.exists(full):
                return full
    if relative_path:
        return get_data_path(os.path.join("assets", relative_path))
    else:
        return get_data_path("assets")


def get_static_path(relative_path=""):
    """
    Get absolute path to static directory/files.
    
    Args:
        relative_path (str): Relative path within static directory
        
    Returns:
        str: Absolute path to the static file/directory
    """
    if relative_path:
        return get_data_path(os.path.join("static", relative_path))
    else:
        return get_data_path("static")


def ensure_directory_exists(path):
    """
    Ensure that a directory exists, creating it if necessary.
    
    Args:
        path (str): Path to the directory
        
    Returns:
        bool: True if directory exists or was created successfully
    """
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        return False


def is_frozen():
    """
    Check if the application is running as a frozen executable.
    
    Returns:
        bool: True if running as executable, False if running as script
    """
    return getattr(sys, 'frozen', False)


def get_executable_dir():
    """
    Get the directory containing the executable or script.
    
    Returns:
        str: Directory path
    """
    if is_frozen():
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_working_directory():
    """
    Get the appropriate working directory for the application.
    
    Returns:
        str: Working directory path
    """
    if is_frozen():
        return get_executable_dir()
    else:
        return os.getcwd() 
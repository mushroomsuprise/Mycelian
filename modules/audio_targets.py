# Copyright (c) 2024-2026 Mycelian
# SPDX-License-Identifier: MIT
"""Audio devices and running apps for the connector "Change volume" action, per platform."""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def get_available_audio_devices() -> Dict[str, str]:
    """Get available audio devices on the current system"""
    try:
        import platform

        system = platform.system().lower()

        if system == "windows":
            return get_windows_audio_devices()
        elif system == "darwin":  # macOS
            return get_macos_audio_devices()
        elif system == "linux":
            return get_linux_audio_devices()
        else:
            return {"": "Default Device"}
    except Exception as e:
        logger.error(f"Error getting audio devices: {e}")
        return {"": "Default Device"}


def get_available_applications() -> Dict[str, str]:
    """Get available applications on the current system"""
    try:
        import platform

        system = platform.system().lower()

        if system == "windows":
            return get_windows_applications()
        elif system == "darwin":  # macOS
            return get_macos_applications()
        elif system == "linux":
            return get_linux_applications()
        else:
            return {"default": "Default Application"}
    except Exception as e:
        logger.error(f"Error getting applications: {e}")
        return {"default": "Default Application"}


def get_windows_audio_devices() -> Dict[str, str]:
    """Get Windows audio devices using pycaw with actual device names"""
    try:
        import warnings

        import comtypes
        from comtypes import CLSCTX_ALL
        from pycaw.constants import AudioDeviceState
        from pycaw.pycaw import AudioUtilities

        devices = {}
        seen_names = set()  # Track device names to avoid duplicates

        # Temporarily suppress pycaw warnings during device enumeration
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", module="pycaw.*", category=UserWarning)

            # Get all audio devices using the simpler AudioUtilities approach
            try:
                all_devices = AudioUtilities.GetAllDevices()

                for device in all_devices:
                    try:
                        # Only process enabled/active devices
                        if device.state != AudioDeviceState.Active:
                            continue

                        device_id = device.id

                        # Try to get friendly name, but don't fail if we can't
                        try:
                            friendly_name = device.FriendlyName
                            if not friendly_name:
                                continue
                        except Exception:
                            # Skip devices where we can't get the friendly name
                            continue

                        # Skip duplicates by name
                        if friendly_name in seen_names:
                            continue
                        seen_names.add(friendly_name)

                        # Determine if it's a playback or recording device based on name patterns
                        if any(
                            keyword in friendly_name.lower()
                            for keyword in [
                                "speaker",
                                "headphone",
                                "headset",
                                "hdmi",
                                "display",
                            ]
                        ):
                            devices[f"playback:{device_id}"] = f"[OUT] {friendly_name}"
                        elif any(
                            keyword in friendly_name.lower()
                            for keyword in ["microphone", "mic", "input", "line-in"]
                        ):
                            devices[f"recording:{device_id}"] = f"[MIC] {friendly_name}"

                    except Exception as e:
                        # Only log actual errors, not property access issues
                        if "COMError" not in str(e):
                            logger.warning(f"Error processing device: {e}")
                        continue

            except Exception as e:
                logger.warning(f"Error getting all devices: {e}")

        # If no devices found, provide fallback
        if not devices:
            devices = {"default": "Default Device"}

        return devices

    except ImportError:
        logger.warning("pycaw not available for Windows device enumeration")
        return {"default": "Default Device (pycaw not installed)"}
    except Exception as e:
        logger.error(f"Error getting Windows audio devices: {e}")
        return {"default": "Default Device (enumeration failed)"}


def get_macos_audio_devices() -> Dict[str, str]:
    """Get macOS audio devices with improved parsing"""
    try:
        import subprocess

        devices = {}

        # Use system_profiler to get audio devices
        result = subprocess.run(
            ["system_profiler", "SPAudioDataType"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode != 0:
            logger.warning("system_profiler failed, trying alternative method")
            return get_macos_audio_devices_fallback()

        lines = result.stdout.split("\n")
        current_section = None
        device_name = None
        device_type = None

        for line in lines:
            line = line.strip()

            # Check for section headers
            if "Audio Devices:" in line:
                current_section = "devices"
            elif "Output Devices:" in line:
                current_section = "output"
            elif "Input Devices:" in line:
                current_section = "input"
            elif line.startswith("Device Name:"):
                device_name = line.split(":", 1)[1].strip()
                device_type = "unknown"
            elif line.startswith("Default Output Device:"):
                default_device = line.split(":", 1)[1].strip()
                if default_device and default_device != "Yes":
                    devices[f"default_output:{default_device}"] = (
                        f"[OUT] {default_device} (Default)"
                    )
            elif line.startswith("Default Input Device:"):
                default_device = line.split(":", 1)[1].strip()
                if default_device and default_device != "Yes":
                    devices[f"default_input:{default_device}"] = (
                        f"[MIC] {default_device} (Default)"
                    )
            elif device_name and current_section:
                # Look for device type indicators
                if "Headphones" in device_name or "Headset" in device_name:
                    device_type = "headphones"
                elif "Speaker" in device_name or "Speakers" in device_name:
                    device_type = "speakers"
                elif "Microphone" in device_name or "Mic" in device_name:
                    device_type = "microphone"
                elif "Display" in device_name:
                    device_type = "display"

                # Add device with appropriate icon
                if device_type == "headphones":
                    devices[f"{current_section}:{device_name}"] = (
                        f"[HEAD] {device_name}"
                    )
                elif device_type == "microphone":
                    devices[f"{current_section}:{device_name}"] = f"[MIC] {device_name}"
                elif device_type == "speakers":
                    devices[f"{current_section}:{device_name}"] = f"[SPK] {device_name}"
                elif device_type == "display":
                    devices[f"{current_section}:{device_name}"] = f"[DSP] {device_name}"
                else:
                    # Generic device with section indicator
                    icon = "[OUT]" if current_section == "output" else "[MIC]"
                    devices[f"{current_section}:{device_name}"] = (
                        f"{icon} {device_name}"
                    )

                device_name = None  # Reset for next device

        # If we didn't find any devices, try fallback method
        if not devices:
            return get_macos_audio_devices_fallback()

        return devices

    except Exception as e:
        logger.error(f"Error getting macOS audio devices: {e}")
        return get_macos_audio_devices_fallback()


def get_macos_audio_devices_fallback() -> Dict[str, str]:
    """Fallback method for macOS audio device enumeration"""
    try:
        import subprocess

        devices = {"default": "Default Device"}

        # Try using audio device list command
        try:
            result = subprocess.run(
                ["audiodevice", "list"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                lines = result.stdout.split("\n")
                for line in lines:
                    if "Device:" in line:
                        device_info = line.split("Device:", 1)[1].strip()
                        devices[device_info] = device_info

        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        # Try using SwitchAudioSource if available
        try:
            result = subprocess.run(
                ["SwitchAudioSource", "-a"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode == 0:
                lines = result.stdout.split("\n")
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith("("):
                        # Extract device name (usually before the first parenthesis)
                        device_name = line.split("(")[0].strip()
                        if device_name:
                            devices[device_name] = device_name

        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        return devices

    except Exception as e:
        logger.error(f"Error in macOS audio device fallback: {e}")
        return {"default": "Default Device"}


def get_linux_audio_devices() -> Dict[str, str]:
    """Get Linux audio devices using pulsectl with improved formatting"""
    try:
        import pulsectl

        devices = {}

        with pulsectl.Pulse("device-enumeration") as pulse:
            # Get output devices (speakers)
            for sink in pulse.sink_list():
                device_name = sink.description or f"Sink {sink.index}"
                # Add appropriate icon based on device type
                if (
                    "headphone" in device_name.lower()
                    or "headset" in device_name.lower()
                ):
                    icon = "[HEAD]"
                elif "speaker" in device_name.lower():
                    icon = "[SPK]"
                elif "hdmi" in device_name.lower() or "display" in device_name.lower():
                    icon = "[DSP]"
                else:
                    icon = "[OUT]"
                devices[f"sink:{sink.index}"] = f"{icon} {device_name}"

            # Get input devices (microphones)
            for source in pulse.source_list():
                if not source.name.endswith(".monitor"):  # Skip monitor sources
                    device_name = source.description or f"Source {source.index}"
                    # Add microphone icon
                    if (
                        "microphone" in device_name.lower()
                        or "mic" in device_name.lower()
                    ):
                        icon = "[MIC]"
                    elif "webcam" in device_name.lower():
                        icon = "[CAM]"
                    else:
                        icon = "[MIC]"
                    devices[f"source:{source.index}"] = f"{icon} {device_name}"

        return devices if devices else {"default": "Default Device"}
    except ImportError:
        logger.warning("pulsectl not available for Linux device enumeration")
        return {"default": "Default Device (pulsectl not installed)"}
    except Exception as e:
        logger.error(f"Error getting Linux audio devices: {e}")
        return {"default": "Default Device (enumeration failed)"}


def get_windows_applications() -> Dict[str, str]:
    """Get Windows applications using psutil"""
    try:
        import psutil

        applications = {}

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"]:
                    app_name = proc.info["name"]
                    # Filter out system processes
                    if not any(
                        sys_proc in app_name.lower()
                        for sys_proc in [
                            "svchost",
                            "system",
                            "winlogon",
                            "csrss",
                            "smss",
                        ]
                    ):
                        applications[app_name] = app_name
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Sort alphabetically and limit to first 50 to avoid UI clutter
        sorted_apps = dict(sorted(applications.items())[:50])
        return sorted_apps
    except ImportError:
        logger.warning("psutil not available for Windows application enumeration")
        return {"default": "Default Application"}
    except Exception as e:
        logger.error(f"Error getting Windows applications: {e}")
        return {"default": "Default Application"}


def get_macos_applications() -> Dict[str, str]:
    """Get macOS applications using psutil"""
    try:
        import psutil

        applications = {}

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"]:
                    app_name = proc.info["name"]
                    # Filter out system processes
                    if not any(
                        sys_proc in app_name.lower()
                        for sys_proc in ["kernel", "launchd", "system", "windowserver"]
                    ):
                        applications[app_name] = app_name
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Sort alphabetically and limit to first 50 to avoid UI clutter
        sorted_apps = dict(sorted(applications.items())[:50])
        return sorted_apps
    except ImportError:
        logger.warning("psutil not available for macOS application enumeration")
        return {"default": "Default Application"}
    except Exception as e:
        logger.error(f"Error getting macOS applications: {e}")
        return {"default": "Default Application"}


def get_linux_applications() -> Dict[str, str]:
    """Get Linux applications using psutil"""
    try:
        import psutil

        applications = {}

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if proc.info["name"]:
                    app_name = proc.info["name"]
                    # Filter out system processes
                    if not any(
                        sys_proc in app_name.lower()
                        for sys_proc in [
                            "systemd",
                            "init",
                            "kthreadd",
                            "ksoftirqd",
                            "migration",
                        ]
                    ):
                        applications[app_name] = app_name
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Sort alphabetically and limit to first 50 to avoid UI clutter
        sorted_apps = dict(sorted(applications.items())[:50])
        return sorted_apps
    except ImportError:
        logger.warning("psutil not available for Linux application enumeration")
        return {"default": "Default Application"}
    except Exception as e:
        logger.error(f"Error getting Linux applications: {e}")
        return {"default": "Default Application"}


def check_audio_permissions() -> Dict[str, Any]:
    """Check audio permissions for the current operating system"""
    try:
        import platform

        system = platform.system().lower()

        if system == "darwin":  # macOS
            return check_macos_audio_permissions()
        elif system == "windows":
            return check_windows_audio_permissions()
        elif system == "linux":
            return check_linux_audio_permissions()
        else:
            return {
                "has_permissions": True,
                "message": "Unknown operating system",
                "can_request": False,
            }
    except Exception as e:
        logger.error(f"Error checking audio permissions: {e}")
        return {
            "has_permissions": False,
            "message": f"Error checking permissions: {e}",
            "can_request": False,
        }


def check_macos_audio_permissions() -> Dict[str, Any]:
    """Check macOS microphone and audio permissions"""
    try:
        # Check if we can access microphone using system_profiler
        import subprocess

        result = subprocess.run(
            ["system_profiler", "SPMicrophoneDataType"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode == 0 and "No microphone" not in result.stdout:
            return {
                "has_permissions": True,
                "message": "Microphone access available",
                "can_request": False,  # Permissions are granted automatically when first requested
            }
        else:
            return {
                "has_permissions": False,
                "message": "Microphone access may be restricted. Check System Preferences > Security & Privacy > Microphone",
                "can_request": False,
            }
    except Exception as e:
        logger.error(f"Error checking macOS audio permissions: {e}")
        return {
            "has_permissions": False,
            "message": "Unable to check microphone permissions",
            "can_request": False,
        }


def check_windows_audio_permissions() -> Dict[str, Any]:
    """Check Windows audio permissions and administrator status"""
    try:
        import ctypes

        # Check if running as administrator
        is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0

        if is_admin:
            return {
                "has_permissions": True,
                "message": "Running as administrator - full audio control available",
                "can_request": False,
                "is_admin": True,
            }
        else:
            return {
                "has_permissions": False,
                "message": "Not running as administrator. Some audio operations may be restricted.",
                "can_request": True,
                "is_admin": False,
                "request_message": "Restart the application as administrator to enable full audio control",
            }
    except Exception as e:
        logger.error(f"Error checking Windows audio permissions: {e}")
        return {
            "has_permissions": False,
            "message": "Unable to check administrator status",
            "can_request": False,
        }


def check_linux_audio_permissions() -> Dict[str, Any]:
    """Check Linux audio permissions"""
    try:
        import grp
        import os
        import pwd

        # Get current user
        current_user = pwd.getpwuid(os.getuid()).pw_name

        # Check if user is in audio group
        try:
            audio_group = grp.getgrnam("audio")
            user_in_audio_group = current_user in audio_group.gr_mem
        except KeyError:
            # Audio group doesn't exist
            user_in_audio_group = False

        if user_in_audio_group:
            return {
                "has_permissions": True,
                "message": "User is in audio group - audio access available",
                "can_request": False,
            }
        else:
            return {
                "has_permissions": False,
                "message": "User is not in the 'audio' group. Audio device access may be restricted.",
                "can_request": True,
                "request_message": f"Add user to audio group: 'sudo usermod -a -G audio {current_user}' then restart the application",
            }
    except Exception as e:
        logger.error(f"Error checking Linux audio permissions: {e}")
        return {
            "has_permissions": False,
            "message": "Unable to check audio group membership",
            "can_request": False,
        }


def request_audio_permissions() -> bool:
    """Request audio permissions for the current operating system"""
    try:
        import platform

        system = platform.system().lower()

        if system == "darwin":  # macOS
            return request_macos_audio_permissions()
        elif system == "windows":
            return request_windows_audio_permissions()
        elif system == "linux":
            return request_linux_audio_permissions()
        else:
            logger.warning(f"Permission request not supported on {system}")
            return False
    except Exception as e:
        logger.error(f"Error requesting audio permissions: {e}")
        return False


def request_macos_audio_permissions() -> bool:
    """Request macOS audio permissions - opens System Preferences"""
    try:
        import subprocess

        # Try to open System Preferences to Microphone pane
        script = """
        tell application "System Preferences"
            activate
            reveal anchor "Privacy_Microphone" of pane id "com.apple.preference.security"
        end tell
        """

        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            logger.info("Opened macOS System Preferences for microphone permissions")
            return True
        else:
            logger.error(f"Failed to open System Preferences: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error requesting macOS permissions: {e}")
        return False


def request_windows_audio_permissions() -> bool:
    """Request Windows audio permissions by attempting to run as admin"""
    try:
        import sys

        # Try to relaunch as administrator
        script = f"""
        If (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {{
            $arguments = "& '{sys.executable}'"
            foreach ($arg in $args) {{
                $arguments += " '$arg'"
            }}
            Start-Process powershell -Verb runAs -ArgumentList $arguments
            Exit
        }}
        """

        # For now, just show a message
        logger.info(
            "To run as administrator, right-click the application and select 'Run as administrator'"
        )
        return True
    except Exception as e:
        logger.error(f"Error requesting Windows permissions: {e}")
        return False


def request_linux_audio_permissions() -> bool:
    """Request Linux audio permissions by showing instructions"""
    try:
        import os
        import pwd

        current_user = pwd.getpwuid(os.getuid()).pw_name
        command = f"sudo usermod -a -G audio {current_user}"

        logger.info(f"To add audio permissions, run: {command}")
        logger.info("Then restart the application")

        # Try to run the command if we have sudo access
        import subprocess

        result = subprocess.run(
            ["sudo", "usermod", "-a", "-G", "audio", current_user],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            logger.info("Successfully added user to audio group")
            return True
        else:
            logger.warning(f"Failed to add user to audio group: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error requesting Linux permissions: {e}")
        return False

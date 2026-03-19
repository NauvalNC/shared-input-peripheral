"""macOS-specific helpers — Accessibility permission checks and CGEventTap."""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import sys

logger = logging.getLogger(__name__)


def is_macos() -> bool:
    return sys.platform == "darwin"


def check_accessibility_permission() -> bool:
    """Check if the app has macOS Accessibility permission.

    Returns True if granted, False otherwise.
    """
    if not is_macos():
        return True

    try:
        app_services = ctypes.cdll.LoadLibrary(
            ctypes.util.find_library("ApplicationServices")
        )
        trusted = app_services.AXIsProcessTrusted()
        return bool(trusted)
    except (OSError, AttributeError):
        logger.warning("Could not check Accessibility permission — assuming not granted")
        return False


def request_accessibility_permission() -> None:
    """Prompt the user to grant Accessibility permission.

    On macOS, this opens System Settings → Privacy → Accessibility.
    """
    if not is_macos():
        return

    import subprocess
    subprocess.Popen([
        "open",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    ])


def ensure_accessibility() -> None:
    """Check Accessibility permission and show instructions if not granted."""
    if not is_macos():
        return

    if check_accessibility_permission():
        logger.info("Accessibility permission granted")
        return

    logger.error(
        "Accessibility permission is required for SharedInput to capture input.\n"
        "Please grant permission in System Settings → Privacy & Security → Accessibility.\n"
        "Opening System Settings..."
    )
    request_accessibility_permission()
    sys.exit(1)

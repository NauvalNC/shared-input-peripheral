"""Windows-specific helpers — admin checks and SendInput utilities."""

from __future__ import annotations

import ctypes
import logging
import sys

logger = logging.getLogger(__name__)


def is_windows() -> bool:
    return sys.platform == "win32"


def is_admin() -> bool:
    """Check if the process is running with administrator privileges."""
    if not is_windows():
        return False
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except (AttributeError, OSError):
        return False


def warn_if_not_admin() -> None:
    """Log a warning if not running as admin.

    Admin is not strictly required but may be needed to inject input
    into elevated (UAC) windows.
    """
    if not is_windows():
        return

    if not is_admin():
        logger.warning(
            "SharedInput is not running as administrator. "
            "Input injection may not work in elevated windows. "
            "Consider running as administrator if you experience issues."
        )

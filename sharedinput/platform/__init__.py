"""Platform-specific helpers."""

from __future__ import annotations

import sys
import logging

logger = logging.getLogger(__name__)


def get_screen_resolution() -> tuple[int, int]:
    """Return (width, height) of the primary display in pixels."""
    if sys.platform == "darwin":
        try:
            import Quartz
            main_display = Quartz.CGMainDisplayID()
            w = Quartz.CGDisplayPixelsWide(main_display)
            h = Quartz.CGDisplayPixelsHigh(main_display)
            return (w, h)
        except Exception:
            pass
    elif sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            user32.SetProcessDPIAware()
            w = user32.GetSystemMetrics(0)
            h = user32.GetSystemMetrics(1)
            return (w, h)
        except Exception:
            pass

    # Fallback: try tkinter
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        w = root.winfo_screenwidth()
        h = root.winfo_screenheight()
        root.destroy()
        return (w, h)
    except Exception:
        pass

    logger.warning("Could not detect screen resolution, defaulting to 1920x1080")
    return (1920, 1080)

"""macOS-specific helpers — Accessibility permission checks and CGEventTap capture.

On macOS Tahoe+, pynput's keyboard listener crashes because it calls
TSMGetInputSourceProperty from a background thread (which requires the
main dispatch queue).  This module provides a CGEventTap-based capture
that hooks into the main thread's CFRunLoop, avoiding the crash.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import queue
import sys
import threading
from typing import Callable

logger = logging.getLogger(__name__)


def is_macos() -> bool:
    return sys.platform == "darwin"


def check_accessibility_permission(prompt: bool = False) -> bool:
    """Check if the app has macOS Accessibility permission.

    Args:
        prompt: If True, use AXIsProcessTrustedWithOptions to automatically
                register the app in System Settings and show the enable prompt.
    """
    if not is_macos():
        return True

    try:
        import objc
        from ApplicationServices import AXIsProcessTrustedWithOptions
        from CoreFoundation import (
            CFDictionaryCreate,
            kCFAllocatorDefault,
            kCFBooleanTrue,
            kCFBooleanFalse,
        )

        # kAXTrustedCheckOptionPrompt — when True, macOS automatically adds
        # the app to the Accessibility list and shows the system prompt.
        key = "AXTrustedCheckOptionPrompt"
        options = {key: prompt}
        trusted = AXIsProcessTrustedWithOptions(options)
        logger.info("AXIsProcessTrustedWithOptions(prompt=%s) = %s", prompt, trusted)
        return bool(trusted)
    except ImportError:
        logger.debug("PyObjC ApplicationServices not available, falling back to ctypes")

    # Fallback to ctypes (no prompt support)
    try:
        app_services = ctypes.cdll.LoadLibrary(
            ctypes.util.find_library("ApplicationServices")
        )
        trusted = app_services.AXIsProcessTrusted()
        return bool(trusted)
    except (OSError, AttributeError):
        logger.warning("Could not check Accessibility permission — assuming not granted")
        return False


def ensure_accessibility(exit_on_fail: bool = True) -> bool:
    """Check Accessibility permission; prompt if not granted.

    Uses AXIsProcessTrustedWithOptions which automatically registers the app
    in System Settings → Accessibility so the user just needs to flip the toggle.

    Args:
        exit_on_fail: If True (CLI mode), call sys.exit(1).
                      If False (tray mode), return False instead.

    Returns:
        True if permission is granted.
    """
    if not is_macos():
        return True

    # First check without prompting
    if check_accessibility_permission(prompt=False):
        logger.info("Accessibility permission granted")
        return True

    # Not granted — prompt (this registers the app in System Settings)
    logger.warning(
        "Accessibility permission not granted. "
        "Prompting user via system dialog..."
    )
    check_accessibility_permission(prompt=True)

    if exit_on_fail:
        logger.error(
            "Please enable SharedInput in System Settings → "
            "Privacy & Security → Accessibility, then restart."
        )
        sys.exit(1)
    return False


# ---------------------------------------------------------------------------
# macOS CGEvent keycode → platform-independent key name mapping
# ---------------------------------------------------------------------------

# Maps macOS CGEvent keycodes to pynput-compatible key names.
# Printable keys are handled via CGEventKeyboardGetUnicodeString instead.
_CG_KEYCODE_TO_NAME: dict[int, str] = {
    # Modifiers
    56: "shift", 60: "shift_r",
    59: "ctrl_l", 62: "ctrl_r",
    58: "alt_l", 61: "alt_r",
    55: "cmd", 54: "cmd_r",
    57: "caps_lock",
    63: "fn",
    # Navigation
    36: "enter", 76: "enter",  # Return + numpad Enter
    48: "tab",
    49: "space",
    51: "backspace",
    117: "delete",
    53: "escape",
    # Arrow keys
    123: "left", 124: "right", 125: "down", 126: "up",
    # Home/End/Page
    115: "home", 119: "end",
    116: "page_up", 121: "page_down",
    # Function keys
    122: "f1", 120: "f2", 99: "f3", 118: "f4",
    96: "f5", 97: "f6", 98: "f7", 100: "f8",
    101: "f9", 109: "f10", 103: "f11", 111: "f12",
    105: "f13", 107: "f14", 113: "f15", 106: "f16",
    64: "f17", 79: "f18", 80: "f19", 90: "f20",
    # Media keys
    114: "insert",
    # Numpad
    82: "num_0", 83: "num_1", 84: "num_2", 85: "num_3",
    86: "num_4", 87: "num_5", 88: "num_6", 89: "num_7",
    91: "num_8", 92: "num_9",
    65: "num_decimal", 67: "num_multiply", 69: "num_add",
    75: "num_divide", 78: "num_subtract", 81: "num_equal",
    71: "num_lock",
}


def _cg_keycode_to_key_name(keycode: int, char: str) -> str:
    """Convert a CGEvent keycode + character to a platform-independent key name.

    Returns the character for printable keys, or a name like "ctrl_l" for
    special/modifier keys.
    """
    # Check special key map first
    name = _CG_KEYCODE_TO_NAME.get(keycode)
    if name:
        return name
    # Fall back to the printable character
    return char


# ---------------------------------------------------------------------------
# CGEventTap-based input capture (must run on main thread)
# ---------------------------------------------------------------------------

try:
    import Quartz
    _HAS_QUARTZ = True
except ImportError:
    _HAS_QUARTZ = False


def has_quartz() -> bool:
    return _HAS_QUARTZ


if _HAS_QUARTZ:
    from sharedinput.protocol import (
        InputEvent,
        KeyPressEvent,
        KeyReleaseEvent,
        MouseButton,
        MouseClickEvent,
        MouseMoveEvent,
        MouseScrollEvent,
        monotonic_ns,
    )

    # Event masks
    _MOUSE_EVENTS = (
        Quartz.CGEventMaskBit(Quartz.kCGEventMouseMoved)
        | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDragged)
        | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDragged)
        | Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseDragged)
        | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDown)
        | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseUp)
        | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDown)
        | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseUp)
        | Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseDown)
        | Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseUp)
        | Quartz.CGEventMaskBit(Quartz.kCGEventScrollWheel)
    )

    _KEY_EVENTS = (
        Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
        | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
    )

    _ALL_INPUT_EVENTS = _MOUSE_EVENTS | _KEY_EVENTS

    def _cg_button_to_mouse_button(event_type: int, cg_event) -> MouseButton:
        if event_type in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            return MouseButton.LEFT
        elif event_type in (Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp):
            return MouseButton.RIGHT
        else:
            return MouseButton.MIDDLE

    class MacOSCapture:
        """CGEventTap-based input capture for macOS.

        IMPORTANT: The ``install_tap`` method MUST be called from the main
        thread because CGEventTapCreate (and downstream TSM APIs) require
        the main dispatch queue on macOS Tahoe.

        Events are delivered to *event_callback* from the main run-loop,
        so the callback should be fast (e.g. put into a queue).
        """

        def __init__(self, event_callback: Callable[[InputEvent], None]) -> None:
            self._callback = event_callback
            self._tap = None
            self._run_loop_source = None
            self._running = False
            self._suppressing = False  # when True, block local input

        def install_tap(self) -> bool:
            """Create the CGEventTap and add it to the CURRENT thread's run loop.

            Must be called from the main thread.  Returns True on success.
            """
            self._tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionDefault,  # can suppress events
                _ALL_INPUT_EVENTS,
                self._tap_callback,
                None,
            )

            if self._tap is None:
                logger.error(
                    "Failed to create CGEventTap — Accessibility permission may not be granted"
                )
                return False

            self._run_loop_source = Quartz.CFMachPortCreateRunLoopSource(
                None, self._tap, 0
            )
            Quartz.CFRunLoopAddSource(
                Quartz.CFRunLoopGetMain(),
                self._run_loop_source,
                Quartz.kCFRunLoopCommonModes,
            )
            Quartz.CGEventTapEnable(self._tap, True)
            self._running = True
            logger.info("macOS CGEventTap installed on main run loop")
            return True

        def stop(self) -> None:
            """Disable and remove the event tap."""
            self._running = False
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, False)
            if self._run_loop_source is not None:
                Quartz.CFRunLoopRemoveSource(
                    Quartz.CFRunLoopGetMain(),
                    self._run_loop_source,
                    Quartz.kCFRunLoopCommonModes,
                )
            self._tap = None
            self._run_loop_source = None
            logger.info("macOS CGEventTap removed")

        def set_suppressing(self, suppressing: bool) -> None:
            """Toggle input suppression. When True, local input is blocked."""
            self._suppressing = suppressing
            logger.info("Input suppression: %s", "ON" if suppressing else "OFF")

        def _tap_callback(self, proxy, event_type, cg_event, refcon):
            """CGEventTap callback — runs on the main thread."""
            if not self._running:
                return cg_event

            try:
                event = self._translate(event_type, cg_event)
                if event is not None:
                    self._callback(event)
            except Exception:
                logger.warning("Error in CGEventTap callback", exc_info=True)

            # Suppress local input when forwarding to a client
            if self._suppressing:
                return None
            return cg_event

        def _translate(self, event_type: int, cg_event) -> InputEvent | None:
            ts = monotonic_ns()

            # Mouse move / drag — use raw hardware deltas (not absolute position)
            # This avoids the screen-edge boundary problem where absolute
            # position stops changing but the user is still moving the mouse.
            if event_type in (
                Quartz.kCGEventMouseMoved,
                Quartz.kCGEventLeftMouseDragged,
                Quartz.kCGEventRightMouseDragged,
                Quartz.kCGEventOtherMouseDragged,
            ):
                dx = Quartz.CGEventGetIntegerValueField(
                    cg_event, Quartz.kCGMouseEventDeltaX
                )
                dy = Quartz.CGEventGetIntegerValueField(
                    cg_event, Quartz.kCGMouseEventDeltaY
                )
                if dx != 0 or dy != 0:
                    return MouseMoveEvent(
                        dx=max(-32768, min(32767, int(dx))),
                        dy=max(-32768, min(32767, int(dy))),
                        timestamp=ts,
                    )
                return None

            # Mouse button press/release
            if event_type in (
                Quartz.kCGEventLeftMouseDown, Quartz.kCGEventRightMouseDown,
                Quartz.kCGEventOtherMouseDown,
            ):
                return MouseClickEvent(
                    button=_cg_button_to_mouse_button(event_type, cg_event),
                    pressed=True, timestamp=ts,
                )
            if event_type in (
                Quartz.kCGEventLeftMouseUp, Quartz.kCGEventRightMouseUp,
                Quartz.kCGEventOtherMouseUp,
            ):
                return MouseClickEvent(
                    button=_cg_button_to_mouse_button(event_type, cg_event),
                    pressed=False, timestamp=ts,
                )

            # Scroll
            if event_type == Quartz.kCGEventScrollWheel:
                dy = Quartz.CGEventGetIntegerValueField(
                    cg_event, Quartz.kCGScrollWheelEventDeltaAxis1
                )
                dx = Quartz.CGEventGetIntegerValueField(
                    cg_event, Quartz.kCGScrollWheelEventDeltaAxis2
                )
                return MouseScrollEvent(
                    dx=max(-32768, min(32767, dx)),
                    dy=max(-32768, min(32767, dy)),
                    timestamp=ts,
                )

            # Key down
            if event_type == Quartz.kCGEventKeyDown:
                keycode = Quartz.CGEventGetIntegerValueField(
                    cg_event, Quartz.kCGKeyboardEventKeycode
                )
                chars = Quartz.CGEventKeyboardGetUnicodeString(
                    cg_event, 4, None, None
                )
                char = ""
                if isinstance(chars, tuple) and len(chars) >= 2 and chars[0] > 0:
                    char = chars[1][:1] if chars[1] else ""
                name = _cg_keycode_to_key_name(keycode, char)
                return KeyPressEvent(
                    keycode=keycode, char=char, key_name=name, timestamp=ts,
                )

            # Key up
            if event_type == Quartz.kCGEventKeyUp:
                keycode = Quartz.CGEventGetIntegerValueField(
                    cg_event, Quartz.kCGKeyboardEventKeycode
                )
                chars = Quartz.CGEventKeyboardGetUnicodeString(
                    cg_event, 4, None, None
                )
                char = ""
                if isinstance(chars, tuple) and len(chars) >= 2 and chars[0] > 0:
                    char = chars[1][:1] if chars[1] else ""
                name = _cg_keycode_to_key_name(keycode, char)
                return KeyReleaseEvent(
                    keycode=keycode, char=char, key_name=name, timestamp=ts,
                )

            # Modifier key (Ctrl, Alt, Shift, Cmd) press/release
            if event_type == Quartz.kCGEventFlagsChanged:
                keycode = Quartz.CGEventGetIntegerValueField(
                    cg_event, Quartz.kCGKeyboardEventKeycode
                )
                flags = Quartz.CGEventGetFlags(cg_event)
                pressed = self._is_modifier_pressed(keycode, flags)
                name = _cg_keycode_to_key_name(keycode, "")
                if pressed:
                    return KeyPressEvent(
                        keycode=keycode, char="", key_name=name, timestamp=ts,
                    )
                else:
                    return KeyReleaseEvent(
                        keycode=keycode, char="", key_name=name, timestamp=ts,
                    )

            return None

        @staticmethod
        def _is_modifier_pressed(keycode: int, flags: int) -> bool:
            """Determine if a modifier key is pressed based on flags.

            Uses the actual Quartz CGEventFlags constants:
              kCGEventFlagMaskShift     = 0x20000
              kCGEventFlagMaskControl   = 0x40000
              kCGEventFlagMaskAlternate = 0x80000
              kCGEventFlagMaskCommand   = 0x100000
            """
            _MODIFIER_FLAG_MAP = {
                56: 0x20000,   # Shift L
                60: 0x20000,   # Shift R
                59: 0x40000,   # Ctrl L
                62: 0x40000,   # Ctrl R
                58: 0x80000,   # Alt/Opt L
                61: 0x80000,   # Alt/Opt R
                55: 0x100000,  # Cmd L
                54: 0x100000,  # Cmd R
            }
            mask = _MODIFIER_FLAG_MAP.get(keycode)
            if mask is None:
                return False
            return bool(flags & mask)

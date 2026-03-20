"""Input injection — replays input events on the client machine.

Uses pynput controllers to inject mouse and keyboard events received
from the server.  On macOS, uses Quartz CGEvent API directly for
mouse injection to avoid snap-back issues with pynput.
"""

from __future__ import annotations

import logging
import sys

from pynput import keyboard, mouse
from pynput.keyboard import Key, KeyCode

from sharedinput.protocol import (
    InputEvent,
    KeyPressEvent,
    KeyReleaseEvent,
    MouseButton,
    MouseClickEvent,
    MouseMoveEvent,
    MouseScrollEvent,
)

logger = logging.getLogger(__name__)

# Map MouseButton enum to pynput mouse buttons
_BUTTON_MAP = {
    MouseButton.LEFT: mouse.Button.left,
    MouseButton.RIGHT: mouse.Button.right,
    MouseButton.MIDDLE: mouse.Button.middle,
}

# macOS Quartz imports for direct mouse control
_HAS_QUARTZ = False
if sys.platform == "darwin":
    try:
        import Quartz
        _HAS_QUARTZ = True
    except ImportError:
        pass


class InputInjector:
    """Injects input events into the local system."""

    def __init__(self) -> None:
        self._mouse = mouse.Controller()
        self._keyboard = keyboard.Controller()
        self._mac_mouse_x: float = 0.0
        self._mac_mouse_y: float = 0.0
        if _HAS_QUARTZ:
            # Initialize to current cursor position
            pos = Quartz.NSEvent.mouseLocation()
            screen_h = Quartz.CGDisplayPixelsHigh(Quartz.CGMainDisplayID())
            self._mac_mouse_x = pos.x
            self._mac_mouse_y = screen_h - pos.y  # flip Y (Quartz uses top-left)

    def inject(self, event: InputEvent) -> None:
        """Inject a single input event."""
        if isinstance(event, MouseMoveEvent):
            self._inject_mouse_move(event)
        elif isinstance(event, MouseClickEvent):
            self._inject_mouse_click(event)
        elif isinstance(event, MouseScrollEvent):
            self._inject_mouse_scroll(event)
        elif isinstance(event, KeyPressEvent):
            self._inject_key_press(event)
        elif isinstance(event, KeyReleaseEvent):
            self._inject_key_release(event)
        else:
            logger.warning("Unknown event type: %s", type(event))

    def _inject_mouse_move(self, event: MouseMoveEvent) -> None:
        if _HAS_QUARTZ:
            # Use Quartz directly to avoid snap-back issues on macOS
            self._mac_mouse_x += event.dx
            self._mac_mouse_y += event.dy
            # Clamp to screen bounds
            screen_w = Quartz.CGDisplayPixelsWide(Quartz.CGMainDisplayID())
            screen_h = Quartz.CGDisplayPixelsHigh(Quartz.CGMainDisplayID())
            self._mac_mouse_x = max(0, min(screen_w - 1, self._mac_mouse_x))
            self._mac_mouse_y = max(0, min(screen_h - 1, self._mac_mouse_y))
            point = Quartz.CGPointMake(self._mac_mouse_x, self._mac_mouse_y)
            move_event = Quartz.CGEventCreateMouseEvent(
                None, Quartz.kCGEventMouseMoved, point, Quartz.kCGMouseButtonLeft
            )
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, move_event)
        else:
            self._mouse.move(event.dx, event.dy)

    def _inject_mouse_click(self, event: MouseClickEvent) -> None:
        if _HAS_QUARTZ:
            point = Quartz.CGPointMake(self._mac_mouse_x, self._mac_mouse_y)
            if event.button == MouseButton.LEFT:
                etype = Quartz.kCGEventLeftMouseDown if event.pressed else Quartz.kCGEventLeftMouseUp
                btn = Quartz.kCGMouseButtonLeft
            elif event.button == MouseButton.RIGHT:
                etype = Quartz.kCGEventRightMouseDown if event.pressed else Quartz.kCGEventRightMouseUp
                btn = Quartz.kCGMouseButtonRight
            else:
                etype = Quartz.kCGEventOtherMouseDown if event.pressed else Quartz.kCGEventOtherMouseUp
                btn = Quartz.kCGMouseButtonCenter
            click_event = Quartz.CGEventCreateMouseEvent(None, etype, point, btn)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, click_event)
        else:
            button = _BUTTON_MAP.get(event.button, mouse.Button.left)
            if event.pressed:
                self._mouse.press(button)
            else:
                self._mouse.release(button)

    def _inject_mouse_scroll(self, event: MouseScrollEvent) -> None:
        self._mouse.scroll(event.dx, event.dy)

    def _inject_key_press(self, event: KeyPressEvent) -> None:
        key = self._resolve_key(event)
        if key is not None:
            try:
                self._keyboard.press(key)
            except Exception:
                logger.debug("Failed to press key: keycode=%d char=%r", event.keycode, event.char)

    def _inject_key_release(self, event: KeyReleaseEvent) -> None:
        key = self._resolve_key(event)
        if key is not None:
            try:
                self._keyboard.release(key)
            except Exception:
                logger.debug("Failed to release key: keycode=%d char=%r", event.keycode, event.char)

    def _resolve_key(self, event: KeyPressEvent | KeyReleaseEvent) -> Key | KeyCode | None:
        """Resolve an event to a pynput key object.

        Uses key_name for platform-independent resolution:
        - Single char → KeyCode.from_char() (printable keys)
        - Multi-char name → Key enum lookup (e.g. "ctrl_l" → Key.ctrl_l)
        """
        name = event.key_name
        if not name:
            return None

        # Printable character (single char)
        if len(name) == 1:
            return KeyCode.from_char(name)

        # Map names that differ between macOS CGEventTap and pynput
        _NAME_MAP = {
            # macOS modifier names → pynput Key names
            "ctrl_l": "ctrl_l", "ctrl_r": "ctrl_r",
            "alt_l": "alt_l", "alt_r": "alt_r",
            "shift": "shift", "shift_r": "shift_r",
            "cmd": "cmd", "cmd_r": "cmd_r",
            # Common aliases
            "escape": "esc",
            "caps_lock": "caps_lock",
            "num_lock": "num_lock",
            # These map directly in pynput
            "enter": "enter",
            "backspace": "backspace",
            "delete": "delete",
            "tab": "tab",
            "space": "space",
            "insert": "insert",
            "home": "home", "end": "end",
            "page_up": "page_up", "page_down": "page_down",
            "left": "left", "right": "right", "up": "up", "down": "down",
        }

        resolved_name = _NAME_MAP.get(name, name)

        # Direct lookup in Key enum
        try:
            return Key[resolved_name]
        except KeyError:
            pass

        # Try stripping _l / _r suffix (e.g. "ctrl_l" → "ctrl")
        if resolved_name.endswith("_l") or resolved_name.endswith("_r"):
            base = resolved_name[:-2]
            try:
                return Key[base]
            except KeyError:
                pass

        logger.debug("Unknown key name: %r (keycode=%d)", name, event.keycode)
        return None

"""Input injection — replays input events on the client machine.

Uses pynput controllers to inject mouse and keyboard events received
from the server.
"""

from __future__ import annotations

import logging

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


class InputInjector:
    """Injects input events into the local system."""

    def __init__(self) -> None:
        self._mouse = mouse.Controller()
        self._keyboard = keyboard.Controller()

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
        self._mouse.move(event.dx, event.dy)

    def _inject_mouse_click(self, event: MouseClickEvent) -> None:
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

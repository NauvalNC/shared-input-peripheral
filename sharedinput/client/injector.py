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

        Prefers character for printable keys, falls back to virtual keycode.
        """
        if event.char:
            return KeyCode.from_char(event.char)
        if event.keycode:
            return KeyCode.from_vk(event.keycode)
        return None

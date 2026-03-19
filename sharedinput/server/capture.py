"""Input capture — wraps pynput listeners to emit protocol events.

Captures mouse and keyboard events and puts them into an event queue
for the network layer to consume and forward.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

from pynput import keyboard, mouse

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

logger = logging.getLogger(__name__)


def _pynput_button_to_mouse_button(button: mouse.Button) -> MouseButton:
    mapping = {
        mouse.Button.left: MouseButton.LEFT,
        mouse.Button.right: MouseButton.RIGHT,
        mouse.Button.middle: MouseButton.MIDDLE,
    }
    return mapping.get(button, MouseButton.LEFT)


def _key_to_keycode_and_char(key: keyboard.Key | keyboard.KeyCode) -> tuple[int, str]:
    """Extract a numeric keycode and character string from a pynput key."""
    if isinstance(key, keyboard.Key):
        return (key.value.vk if key.value and hasattr(key.value, "vk") else hash(key) & 0xFFFFFFFF, "")
    elif isinstance(key, keyboard.KeyCode):
        vk = key.vk if key.vk is not None else 0
        char = key.char if key.char else ""
        return (vk, char)
    return (0, "")


class InputCapture:
    """Captures mouse and keyboard input, emitting InputEvent objects."""

    def __init__(self, event_callback: Callable[[InputEvent], None]) -> None:
        self._callback = event_callback
        self._last_mouse_x: int | None = None
        self._last_mouse_y: int | None = None
        self._mouse_listener: mouse.Listener | None = None
        self._keyboard_listener: keyboard.Listener | None = None
        self._running = False

    def start(self) -> None:
        """Start capturing input events."""
        if self._running:
            return

        self._running = True
        self._mouse_listener = mouse.Listener(
            on_move=self._on_mouse_move,
            on_click=self._on_mouse_click,
            on_scroll=self._on_mouse_scroll,
        )
        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._mouse_listener.start()
        self._keyboard_listener.start()
        logger.info("Input capture started")

    def stop(self) -> None:
        """Stop capturing input events."""
        self._running = False
        if self._mouse_listener:
            self._mouse_listener.stop()
        if self._keyboard_listener:
            self._keyboard_listener.stop()
        logger.info("Input capture stopped")

    def _on_mouse_move(self, x: int, y: int) -> None:
        if not self._running:
            return

        if self._last_mouse_x is not None and self._last_mouse_y is not None:
            dx = x - self._last_mouse_x
            dy = y - self._last_mouse_y
            if dx != 0 or dy != 0:
                self._callback(MouseMoveEvent(
                    dx=max(-32768, min(32767, dx)),
                    dy=max(-32768, min(32767, dy)),
                    timestamp=monotonic_ns(),
                ))

        self._last_mouse_x = x
        self._last_mouse_y = y

    def _on_mouse_click(self, x: int, y: int, button: mouse.Button, pressed: bool) -> None:
        if not self._running:
            return

        self._callback(MouseClickEvent(
            button=_pynput_button_to_mouse_button(button),
            pressed=pressed,
            timestamp=monotonic_ns(),
        ))

    def _on_mouse_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        if not self._running:
            return

        self._callback(MouseScrollEvent(
            dx=max(-32768, min(32767, dx)),
            dy=max(-32768, min(32767, dy)),
            timestamp=monotonic_ns(),
        ))

    def _on_key_press(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        if not self._running or key is None:
            return

        keycode, char = _key_to_keycode_and_char(key)
        self._callback(KeyPressEvent(
            keycode=keycode,
            char=char,
            timestamp=monotonic_ns(),
        ))

    def _on_key_release(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        if not self._running or key is None:
            return

        keycode, char = _key_to_keycode_and_char(key)
        self._callback(KeyReleaseEvent(
            keycode=keycode,
            char=char,
            timestamp=monotonic_ns(),
        ))


class QueuedCapture:
    """Convenience wrapper that captures events into a thread-safe queue."""

    def __init__(self, maxsize: int = 4096) -> None:
        self.queue: queue.Queue[InputEvent] = queue.Queue(maxsize=maxsize)
        self._capture = InputCapture(event_callback=self._enqueue)

    def _enqueue(self, event: InputEvent) -> None:
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            # Drop oldest event to make room
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            self.queue.put_nowait(event)

    def start(self) -> None:
        self._capture.start()

    def stop(self) -> None:
        self._capture.stop()

    def get_event(self, timeout: float = 0.1) -> InputEvent | None:
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None

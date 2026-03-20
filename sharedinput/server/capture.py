"""Input capture — wraps pynput listeners (or macOS CGEventTap) to emit events.

On macOS Tahoe+, pynput's keyboard listener crashes because it calls
TSM APIs from a background thread.  When running on macOS we use the
CGEventTap backend from ``platform.macos`` instead, which hooks into the
main CFRunLoop and avoids the crash.

The CGEventTap is installed separately (via ``install_macos_tap``) because
it MUST run on the main thread.  The pynput fallback (``InputCapture``)
works on Windows and Linux, and can be started from any thread.
"""

from __future__ import annotations

import logging
import queue
import sys
from typing import Callable

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

# ---------------------------------------------------------------------------
# macOS CGEventTap capture (main-thread safe)
# ---------------------------------------------------------------------------

_macos_capture = None  # singleton MacOSCapture instance


def use_macos_backend() -> bool:
    """Return True if we should use the CGEventTap backend."""
    if sys.platform != "darwin":
        return False
    try:
        from sharedinput.platform.macos import has_quartz
        return has_quartz()
    except ImportError:
        return False


def install_macos_tap(event_callback: Callable[[InputEvent], None]) -> bool:
    """Install a CGEventTap on the main thread's run loop.

    MUST be called from the main thread.  Returns True on success.
    Call this before the NSApplication / pystray run loop starts
    (e.g. in pystray's setup callback).
    """
    global _macos_capture
    from sharedinput.platform.macos import MacOSCapture

    _macos_capture = MacOSCapture(event_callback)
    return _macos_capture.install_tap()


def stop_macos_tap() -> None:
    """Stop and remove the CGEventTap."""
    global _macos_capture
    if _macos_capture is not None:
        _macos_capture.stop()
        _macos_capture = None


def set_macos_tap_suppressing(suppressing: bool) -> None:
    """Toggle input suppression on the macOS CGEventTap."""
    if _macos_capture is not None:
        _macos_capture.set_suppressing(suppressing)


# ---------------------------------------------------------------------------
# pynput-based capture (Windows / Linux / fallback)
# ---------------------------------------------------------------------------

def _pynput_button_to_mouse_button(button) -> MouseButton:
    from pynput import mouse
    mapping = {
        mouse.Button.left: MouseButton.LEFT,
        mouse.Button.right: MouseButton.RIGHT,
        mouse.Button.middle: MouseButton.MIDDLE,
    }
    return mapping.get(button, MouseButton.LEFT)


def _key_to_event_fields(key) -> tuple[int, str, str]:
    """Convert a pynput key to (keycode, char, key_name).

    Returns platform-independent key_name for all keys:
    - Special keys (Key.ctrl_l, Key.f1): key_name = "ctrl_l", "f1"
    - Printable keys (KeyCode): key_name = the character itself
    """
    from pynput import keyboard
    if isinstance(key, keyboard.Key):
        vk = key.value.vk if key.value and hasattr(key.value, "vk") else 0
        return (vk, "", key.name)  # key.name is consistent across platforms
    elif isinstance(key, keyboard.KeyCode):
        vk = key.vk if key.vk is not None else 0
        char = key.char if key.char else ""
        return (vk, char, char)  # for printable keys, key_name = char
    return (0, "", "")


class InputCapture:
    """Captures mouse and keyboard input using pynput (non-macOS).

    Supports toggling input suppression via ``set_suppressing()``.
    When suppressing, listeners are restarted with ``suppress=True``
    so that captured input is blocked from reaching the local system.
    """

    def __init__(self, event_callback: Callable[[InputEvent], None]) -> None:
        self._callback = event_callback
        self._last_mouse_x: int | None = None
        self._last_mouse_y: int | None = None
        self._mouse_listener = None
        self._keyboard_listener = None
        self._running = False
        self._suppressing = False

    def set_suppressing(self, suppressing: bool) -> None:
        """Toggle input suppression. Restarts listeners with new mode."""
        if suppressing == self._suppressing:
            return
        self._suppressing = suppressing
        logger.info("Input suppression: %s", "ON" if suppressing else "OFF")
        if self._running:
            self._stop_listeners()
            self._start_listeners()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._start_listeners()

    def _start_listeners(self) -> None:
        from pynput import keyboard, mouse

        self._mouse_listener = mouse.Listener(
            on_move=self._on_mouse_move,
            on_click=self._on_mouse_click,
            on_scroll=self._on_mouse_scroll,
            suppress=self._suppressing,
        )
        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
            suppress=self._suppressing,
        )
        self._mouse_listener.start()
        self._keyboard_listener.start()
        logger.info("Input capture started (pynput)")

    def _stop_listeners(self) -> None:
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if self._keyboard_listener:
            self._keyboard_listener.stop()
            self._keyboard_listener = None

    def stop(self) -> None:
        self._running = False
        self._stop_listeners()
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

    def _on_mouse_click(self, x, y, button, pressed) -> None:
        if not self._running:
            return
        self._callback(MouseClickEvent(
            button=_pynput_button_to_mouse_button(button),
            pressed=pressed,
            timestamp=monotonic_ns(),
        ))

    def _on_mouse_scroll(self, x, y, dx, dy) -> None:
        if not self._running:
            return
        self._callback(MouseScrollEvent(
            dx=max(-32768, min(32767, dx)),
            dy=max(-32768, min(32767, dy)),
            timestamp=monotonic_ns(),
        ))

    def _on_key_press(self, key) -> None:
        if not self._running or key is None:
            return
        keycode, char, key_name = _key_to_event_fields(key)
        self._callback(KeyPressEvent(
            keycode=keycode, char=char, key_name=key_name, timestamp=monotonic_ns(),
        ))

    def _on_key_release(self, key) -> None:
        if not self._running or key is None:
            return
        keycode, char, key_name = _key_to_event_fields(key)
        self._callback(KeyReleaseEvent(
            keycode=keycode, char=char, key_name=key_name, timestamp=monotonic_ns(),
        ))


class QueuedCapture:
    """Convenience wrapper that captures events into a thread-safe queue."""

    def __init__(self, maxsize: int = 4096) -> None:
        self.queue: queue.Queue[InputEvent] = queue.Queue(maxsize=maxsize)
        self._capture: InputCapture | None = None

    def _enqueue(self, event: InputEvent) -> None:
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            self.queue.put_nowait(event)

    def start(self) -> None:
        if use_macos_backend():
            # macOS: tap is installed on the main thread separately;
            # just wire the callback here.
            install_macos_tap(self._enqueue)
        else:
            self._capture = InputCapture(event_callback=self._enqueue)
            self._capture.start()

    def stop(self) -> None:
        if use_macos_backend():
            stop_macos_tap()
        elif self._capture:
            self._capture.stop()

    def get_event(self, timeout: float = 0.1) -> InputEvent | None:
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None

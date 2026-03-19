"""Event protocol — defines input event types and binary serialization.

Binary format (little-endian):
    type (1B) | flags (1B) | timestamp (8B) | payload (variable)

All multi-byte fields are little-endian.  Mouse deltas are signed int16.
Key events carry both a platform keycode and a UTF-8 character (up to 4 bytes).
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Union


class EventType(IntEnum):
    MOUSE_MOVE = 0x01
    MOUSE_CLICK = 0x02
    MOUSE_SCROLL = 0x03
    KEY_PRESS = 0x04
    KEY_RELEASE = 0x05


class MouseButton(IntEnum):
    LEFT = 1
    RIGHT = 2
    MIDDLE = 3


# Header: type(1B) + flags(1B) + timestamp(8B) = 10 bytes
_HEADER_FMT = "<BBQ"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)

# Payload formats
_MOUSE_MOVE_FMT = "<hh"       # dx, dy (signed int16)
_MOUSE_CLICK_FMT = "<BB"      # button, pressed
_MOUSE_SCROLL_FMT = "<hh"     # dx, dy (signed int16)
_KEY_FMT = "<I4s"             # keycode (uint32), char (4 bytes UTF-8 padded)


@dataclass(slots=True)
class MouseMoveEvent:
    dx: int
    dy: int
    timestamp: int = 0

    @property
    def event_type(self) -> EventType:
        return EventType.MOUSE_MOVE


@dataclass(slots=True)
class MouseClickEvent:
    button: MouseButton
    pressed: bool
    timestamp: int = 0

    @property
    def event_type(self) -> EventType:
        return EventType.MOUSE_CLICK


@dataclass(slots=True)
class MouseScrollEvent:
    dx: int
    dy: int
    timestamp: int = 0

    @property
    def event_type(self) -> EventType:
        return EventType.MOUSE_SCROLL


@dataclass(slots=True)
class KeyPressEvent:
    keycode: int
    char: str
    timestamp: int = 0

    @property
    def event_type(self) -> EventType:
        return EventType.KEY_PRESS


@dataclass(slots=True)
class KeyReleaseEvent:
    keycode: int
    char: str
    timestamp: int = 0

    @property
    def event_type(self) -> EventType:
        return EventType.KEY_RELEASE


InputEvent = Union[MouseMoveEvent, MouseClickEvent, MouseScrollEvent, KeyPressEvent, KeyReleaseEvent]


def _encode_char(char: str) -> bytes:
    """Encode a character as exactly 4 bytes (UTF-8, zero-padded)."""
    encoded = char.encode("utf-8")[:4] if char else b""
    return encoded.ljust(4, b"\x00")


def _decode_char(data: bytes) -> str:
    """Decode a 4-byte zero-padded UTF-8 character."""
    return data.rstrip(b"\x00").decode("utf-8", errors="replace")


def monotonic_ns() -> int:
    """Return monotonic time in microseconds (fits in uint64)."""
    return int(time.monotonic() * 1_000_000)


def serialize(event: InputEvent) -> bytes:
    """Serialize an input event to bytes."""
    ts = event.timestamp or monotonic_ns()
    flags = 0

    if isinstance(event, MouseMoveEvent):
        payload = struct.pack(_MOUSE_MOVE_FMT, event.dx, event.dy)
        header = struct.pack(_HEADER_FMT, EventType.MOUSE_MOVE, flags, ts)

    elif isinstance(event, MouseClickEvent):
        payload = struct.pack(_MOUSE_CLICK_FMT, event.button, int(event.pressed))
        header = struct.pack(_HEADER_FMT, EventType.MOUSE_CLICK, flags, ts)

    elif isinstance(event, MouseScrollEvent):
        payload = struct.pack(_MOUSE_SCROLL_FMT, event.dx, event.dy)
        header = struct.pack(_HEADER_FMT, EventType.MOUSE_SCROLL, flags, ts)

    elif isinstance(event, KeyPressEvent):
        payload = struct.pack(_KEY_FMT, event.keycode, _encode_char(event.char))
        header = struct.pack(_HEADER_FMT, EventType.KEY_PRESS, flags, ts)

    elif isinstance(event, KeyReleaseEvent):
        payload = struct.pack(_KEY_FMT, event.keycode, _encode_char(event.char))
        header = struct.pack(_HEADER_FMT, EventType.KEY_RELEASE, flags, ts)

    else:
        raise ValueError(f"Unknown event type: {type(event)}")

    return header + payload


def deserialize(data: bytes) -> InputEvent:
    """Deserialize bytes into an input event."""
    if len(data) < _HEADER_SIZE:
        raise ValueError(f"Data too short: {len(data)} bytes (need at least {_HEADER_SIZE})")

    event_type, _flags, timestamp = struct.unpack_from(_HEADER_FMT, data)
    payload = data[_HEADER_SIZE:]

    if event_type == EventType.MOUSE_MOVE:
        dx, dy = struct.unpack(_MOUSE_MOVE_FMT, payload)
        return MouseMoveEvent(dx=dx, dy=dy, timestamp=timestamp)

    elif event_type == EventType.MOUSE_CLICK:
        button, pressed = struct.unpack(_MOUSE_CLICK_FMT, payload)
        return MouseClickEvent(button=MouseButton(button), pressed=bool(pressed), timestamp=timestamp)

    elif event_type == EventType.MOUSE_SCROLL:
        dx, dy = struct.unpack(_MOUSE_SCROLL_FMT, payload)
        return MouseScrollEvent(dx=dx, dy=dy, timestamp=timestamp)

    elif event_type == EventType.KEY_PRESS:
        keycode, char_bytes = struct.unpack(_KEY_FMT, payload)
        return KeyPressEvent(keycode=keycode, char=_decode_char(char_bytes), timestamp=timestamp)

    elif event_type == EventType.KEY_RELEASE:
        keycode, char_bytes = struct.unpack(_KEY_FMT, payload)
        return KeyReleaseEvent(keycode=keycode, char=_decode_char(char_bytes), timestamp=timestamp)

    else:
        raise ValueError(f"Unknown event type: {event_type:#x}")

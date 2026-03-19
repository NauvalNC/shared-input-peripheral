"""Tests for the event protocol — serialization round-trips."""

import struct

import pytest

from sharedinput.protocol import (
    EventType,
    KeyPressEvent,
    KeyReleaseEvent,
    MouseButton,
    MouseClickEvent,
    MouseMoveEvent,
    MouseScrollEvent,
    deserialize,
    serialize,
)


class TestMouseMoveEvent:
    def test_round_trip(self):
        event = MouseMoveEvent(dx=10, dy=-20, timestamp=12345)
        data = serialize(event)
        result = deserialize(data)
        assert isinstance(result, MouseMoveEvent)
        assert result.dx == 10
        assert result.dy == -20
        assert result.timestamp == 12345

    def test_zero_delta(self):
        event = MouseMoveEvent(dx=0, dy=0, timestamp=1)
        result = deserialize(serialize(event))
        assert result.dx == 0
        assert result.dy == 0

    def test_max_delta(self):
        event = MouseMoveEvent(dx=32767, dy=-32768, timestamp=1)
        result = deserialize(serialize(event))
        assert result.dx == 32767
        assert result.dy == -32768


class TestMouseClickEvent:
    def test_left_press(self):
        event = MouseClickEvent(button=MouseButton.LEFT, pressed=True, timestamp=100)
        result = deserialize(serialize(event))
        assert isinstance(result, MouseClickEvent)
        assert result.button == MouseButton.LEFT
        assert result.pressed is True

    def test_right_release(self):
        event = MouseClickEvent(button=MouseButton.RIGHT, pressed=False, timestamp=200)
        result = deserialize(serialize(event))
        assert result.button == MouseButton.RIGHT
        assert result.pressed is False

    def test_middle_click(self):
        event = MouseClickEvent(button=MouseButton.MIDDLE, pressed=True, timestamp=300)
        result = deserialize(serialize(event))
        assert result.button == MouseButton.MIDDLE


class TestMouseScrollEvent:
    def test_round_trip(self):
        event = MouseScrollEvent(dx=0, dy=3, timestamp=500)
        result = deserialize(serialize(event))
        assert isinstance(result, MouseScrollEvent)
        assert result.dx == 0
        assert result.dy == 3

    def test_negative_scroll(self):
        event = MouseScrollEvent(dx=-1, dy=-5, timestamp=600)
        result = deserialize(serialize(event))
        assert result.dx == -1
        assert result.dy == -5


class TestKeyPressEvent:
    def test_ascii_char(self):
        event = KeyPressEvent(keycode=65, char="a", timestamp=1000)
        result = deserialize(serialize(event))
        assert isinstance(result, KeyPressEvent)
        assert result.keycode == 65
        assert result.char == "a"
        assert result.timestamp == 1000

    def test_empty_char(self):
        """Modifier keys have no character."""
        event = KeyPressEvent(keycode=162, char="", timestamp=2000)
        result = deserialize(serialize(event))
        assert result.keycode == 162
        assert result.char == ""

    def test_unicode_char(self):
        event = KeyPressEvent(keycode=0, char="\u00e9", timestamp=3000)
        result = deserialize(serialize(event))
        assert result.char == "\u00e9"

    def test_multibyte_utf8(self):
        """CJK character (3 bytes in UTF-8)."""
        event = KeyPressEvent(keycode=0, char="\u4e16", timestamp=4000)
        result = deserialize(serialize(event))
        assert result.char == "\u4e16"


class TestKeyReleaseEvent:
    def test_round_trip(self):
        event = KeyReleaseEvent(keycode=65, char="a", timestamp=5000)
        result = deserialize(serialize(event))
        assert isinstance(result, KeyReleaseEvent)
        assert result.keycode == 65
        assert result.char == "a"


class TestDeserializeErrors:
    def test_too_short(self):
        with pytest.raises(ValueError, match="too short"):
            deserialize(b"\x00\x00")

    def test_unknown_type(self):
        # Craft a header with unknown type 0xFF
        data = struct.pack("<BBQ", 0xFF, 0, 0) + b"\x00\x00\x00\x00"
        with pytest.raises(ValueError, match="Unknown event type"):
            deserialize(data)


class TestSerializeSize:
    """Ensure serialized events are compact."""

    def test_mouse_move_size(self):
        data = serialize(MouseMoveEvent(dx=1, dy=1, timestamp=1))
        assert len(data) == 14  # 10 header + 4 payload

    def test_mouse_click_size(self):
        data = serialize(MouseClickEvent(button=MouseButton.LEFT, pressed=True, timestamp=1))
        assert len(data) == 12  # 10 header + 2 payload

    def test_key_press_size(self):
        data = serialize(KeyPressEvent(keycode=65, char="a", timestamp=1))
        assert len(data) == 18  # 10 header + 8 payload

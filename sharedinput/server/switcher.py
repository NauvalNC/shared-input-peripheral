"""Hotkey switcher — detects switch hotkey and manages active client.

The switcher sits between the capture layer and the network sender.
When a switch hotkey is detected, it cycles through connected clients
or returns to local mode.

On macOS Tahoe+, pynput's keyboard.Listener crashes because it calls
TSMGetInputSourceProperty from a background thread.  The switcher can
operate in "event-fed" mode where it receives key events directly from
the CGEventTap capture instead of running its own pynput listener.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Callable

from sharedinput.server.network import ClientInfo

logger = logging.getLogger(__name__)

# Virtual keycodes used for platform-independent hotkey matching.
# On macOS these match CGEvent keycodes; on Windows/Linux they come
# from pynput's Key enum.  The switcher normalises both to strings.
_CTRL_NAMES = frozenset({"ctrl_l", "ctrl_r", "ctrl"})
_ALT_NAMES = frozenset({"alt_l", "alt_r", "alt", "alt_gr"})
_RIGHT_NAMES = frozenset({"right"})
_LEFT_NAMES = frozenset({"left"})

# macOS CGEvent keycodes for modifier / arrow keys
_MACOS_KEYCODE_MAP: dict[int, str] = {
    59: "ctrl_l", 62: "ctrl_r",
    58: "alt_l", 61: "alt_r",
    123: "left", 124: "right", 125: "down", 126: "up",
}


@dataclass
class SwitchState:
    """Tracks the current switching state."""
    active_client_id: str | None = None  # None = local mode
    client_order: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.client_order is None:
            self.client_order = []


class HotkeySwitcher:
    """Monitors for hotkey combos and manages which client is active.

    Default hotkeys:
        Ctrl+Alt+Right → next client
        Ctrl+Alt+Left  → previous client / back to local

    Two modes:
        1. **pynput mode** (Windows / Linux): ``start()`` creates a
           pynput keyboard.Listener on a background thread.
        2. **event-fed mode** (macOS): call ``feed_key_press`` /
           ``feed_key_release`` from the CGEventTap callback.
    """

    def __init__(
        self,
        on_switch: Callable[[str | None], None],
    ) -> None:
        self._on_switch = on_switch
        self._state = SwitchState()
        self._current_keys: set[str] = set()  # normalised key names
        self._listener = None  # pynput listener (non-macOS only)

    @property
    def active_client_id(self) -> str | None:
        return self._state.active_client_id

    @property
    def is_forwarding(self) -> bool:
        return self._state.active_client_id is not None

    def update_clients(self, clients: dict[str, ClientInfo]) -> None:
        """Update the list of available clients."""
        self._state.client_order = list(clients.keys())

        # If active client disconnected, switch back to local
        if (
            self._state.active_client_id
            and self._state.active_client_id not in clients
        ):
            logger.info("Active client disconnected — switching to local")
            self._switch_to(None)

    def start(self) -> None:
        """Start listening for hotkeys via pynput (non-macOS only).

        On macOS, hotkeys are detected via ``feed_key_press`` /
        ``feed_key_release`` driven by the CGEventTap, so this is
        a no-op.
        """
        if sys.platform == "darwin":
            logger.info("Hotkey switcher started in event-fed mode (Ctrl+Alt+Arrow)")
            return

        from pynput import keyboard

        self._listener = keyboard.Listener(
            on_press=self._on_pynput_press,
            on_release=self._on_pynput_release,
        )
        self._listener.start()
        logger.info("Hotkey switcher started (Ctrl+Alt+Arrow to switch)")

    def stop(self) -> None:
        """Stop listening for hotkeys."""
        if self._listener:
            self._listener.stop()

    # -- event-fed API (macOS CGEventTap) ------------------------------------

    def feed_key_press(self, keycode: int) -> None:
        """Feed a raw macOS keycode press into the hotkey detector."""
        name = _MACOS_KEYCODE_MAP.get(keycode)
        if name:
            self._current_keys.add(name)
            self._check_hotkeys()

    def feed_key_release(self, keycode: int) -> None:
        """Feed a raw macOS keycode release into the hotkey detector."""
        name = _MACOS_KEYCODE_MAP.get(keycode)
        if name:
            self._current_keys.discard(name)

    # -- pynput callbacks (Windows / Linux) ----------------------------------

    def _on_pynput_press(self, key) -> None:
        name = self._pynput_key_name(key)
        if name:
            self._current_keys.add(name)
            self._check_hotkeys()

    def _on_pynput_release(self, key) -> None:
        name = self._pynput_key_name(key)
        if name:
            self._current_keys.discard(name)

    @staticmethod
    def _pynput_key_name(key) -> str | None:
        from pynput import keyboard
        if isinstance(key, keyboard.Key):
            return key.name
        return None

    def _check_hotkeys(self) -> None:
        has_ctrl = bool(self._current_keys & _CTRL_NAMES)
        has_alt = bool(self._current_keys & _ALT_NAMES)
        has_right = bool(self._current_keys & _RIGHT_NAMES)
        has_left = bool(self._current_keys & _LEFT_NAMES)

        if has_ctrl and has_alt and has_right:
            self._switch_next()
            self._current_keys.clear()
        elif has_ctrl and has_alt and has_left:
            self._switch_prev()
            self._current_keys.clear()

    def _switch_next(self) -> None:
        """Switch to the next client, or wrap to local."""
        order = self._state.client_order
        if not order:
            return

        current = self._state.active_client_id
        if current is None:
            # Switch from local to first client
            self._switch_to(order[0])
        else:
            idx = order.index(current) if current in order else -1
            next_idx = idx + 1
            if next_idx >= len(order):
                # Wrap back to local
                self._switch_to(None)
            else:
                self._switch_to(order[next_idx])

    def _switch_prev(self) -> None:
        """Switch to the previous client, or wrap to local."""
        order = self._state.client_order
        if not order:
            return

        current = self._state.active_client_id
        if current is None:
            # Switch from local to last client
            self._switch_to(order[-1])
        else:
            idx = order.index(current) if current in order else 0
            prev_idx = idx - 1
            if prev_idx < 0:
                # Wrap back to local
                self._switch_to(None)
            else:
                self._switch_to(order[prev_idx])

    def switch_to(self, client_id: str | None) -> None:
        """Public API: switch to a specific client or local mode.

        Args:
            client_id: The client ID to switch to, or None for local/server.
        """
        if client_id is not None and client_id not in self._state.client_order:
            logger.warning("Cannot switch to unknown client: %s", client_id)
            return
        self._switch_to(client_id)

    def _switch_to(self, client_id: str | None) -> None:
        """Switch to a specific client or local mode."""
        self._state.active_client_id = client_id
        if client_id:
            logger.info("Switched to client: %s", client_id)
        else:
            logger.info("Switched to LOCAL mode")
        self._on_switch(client_id)

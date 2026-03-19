"""Hotkey switcher — detects switch hotkey and manages active client.

The switcher sits between the capture layer and the network sender.
When a switch hotkey is detected, it cycles through connected clients
or returns to local mode.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from pynput import keyboard

from sharedinput.server.network import ClientInfo

logger = logging.getLogger(__name__)


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
    """

    def __init__(
        self,
        on_switch: Callable[[str | None], None],
        next_keys: frozenset | None = None,
        prev_keys: frozenset | None = None,
    ) -> None:
        self._on_switch = on_switch
        self._state = SwitchState()
        self._current_keys: set = set()

        # Default hotkeys
        self._next_keys = next_keys or frozenset({
            keyboard.Key.ctrl_l, keyboard.Key.alt_l, keyboard.Key.right
        })
        self._prev_keys = prev_keys or frozenset({
            keyboard.Key.ctrl_l, keyboard.Key.alt_l, keyboard.Key.left
        })

        self._listener: keyboard.Listener | None = None

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
        """Start listening for hotkeys."""
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.start()
        logger.info("Hotkey switcher started (Ctrl+Alt+Arrow to switch)")

    def stop(self) -> None:
        """Stop listening for hotkeys."""
        if self._listener:
            self._listener.stop()

    def _on_press(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        if key is None:
            return
        self._current_keys.add(key)
        self._check_hotkeys()

    def _on_release(self, key: keyboard.Key | keyboard.KeyCode | None) -> None:
        if key is None:
            return
        self._current_keys.discard(key)

    def _check_hotkeys(self) -> None:
        if self._next_keys.issubset(self._current_keys):
            self._switch_next()
            self._current_keys.clear()
        elif self._prev_keys.issubset(self._current_keys):
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

    def _switch_to(self, client_id: str | None) -> None:
        """Switch to a specific client or local mode."""
        self._state.active_client_id = client_id
        if client_id:
            logger.info("Switched to client: %s", client_id)
        else:
            logger.info("Switched to LOCAL mode")
        self._on_switch(client_id)

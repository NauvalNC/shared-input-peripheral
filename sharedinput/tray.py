"""System tray UI — runs SharedInput as a menu bar / system tray app.

Provides a tray icon with a menu to:
- See connected devices
- Switch active device
- Start/stop server or client mode
- Quit the app
"""

from __future__ import annotations

import asyncio
import logging
import platform
import socket
import sys
import threading
from typing import Callable

import pystray
from PIL import Image

from sharedinput.config import Config, load_config
from sharedinput.icons import create_active_icon, create_default_icon, create_disabled_icon

logger = logging.getLogger(__name__)


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


class TrayApp:
    """System tray application for SharedInput."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or load_config()
        self._icon: pystray.Icon | None = None
        self._server = None
        self._client = None
        self._server_thread: threading.Thread | None = None
        self._client_thread: threading.Thread | None = None
        self._running_role: str | None = None  # "server" | "client" | None

        # Icons
        self._icon_default = create_default_icon(64)
        self._icon_active = create_active_icon(64)
        self._icon_disabled = create_disabled_icon(64)

    def run(self) -> None:
        """Start the tray app (blocking)."""
        self._icon = pystray.Icon(
            name="SharedInput",
            icon=self._icon_default,
            title="SharedInput",
            menu=self._build_menu(),
        )
        logger.info("Starting SharedInput tray app")
        self._icon.run(setup=self._on_setup)

    def _on_setup(self, icon: pystray.Icon) -> None:
        """Called when the tray icon is ready."""
        icon.visible = True
        icon.notify("SharedInput is running.\nRight-click the tray icon to get started.", "SharedInput")

    def _build_menu(self) -> pystray.Menu:
        """Build the tray context menu."""
        return pystray.Menu(
            pystray.MenuItem(
                "SharedInput",
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            # Role section
            pystray.MenuItem(
                lambda item: self._role_label("server"),
                self._on_start_server,
                checked=lambda item: self._running_role == "server",
                radio=True,
            ),
            pystray.MenuItem(
                lambda item: self._role_label("client"),
                self._on_start_client,
                checked=lambda item: self._running_role == "client",
                radio=True,
            ),
            pystray.MenuItem(
                lambda item: "Stop" if self._running_role else "Stopped",
                self._on_stop,
                enabled=lambda item: self._running_role is not None,
            ),
            pystray.Menu.SEPARATOR,
            # Status
            pystray.MenuItem(
                lambda item: self._status_text(),
                None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit),
        )

    def _role_label(self, role: str) -> str:
        if role == "server":
            ip = _get_local_ip()
            if self._running_role == "server":
                return f"Server (running on {ip})"
            return f"Start as Server ({ip})"
        else:
            host = self._config.server_host or "not set"
            if self._running_role == "client":
                return f"Client (connected to {host})"
            return f"Start as Client (→ {host})"

    def _status_text(self) -> str:
        if self._running_role == "server":
            if self._server:
                clients = self._server._control_server.clients
                n = len(clients)
                if n == 0:
                    return "No clients connected"
                names = [c.hostname for c in clients.values()]
                active_id = self._server._switcher.active_client_id
                if active_id and active_id in clients:
                    active_name = clients[active_id].hostname
                    return f"Controlling: {active_name} ({n} connected)"
                return f"Local mode ({n} client{'s' if n > 1 else ''} connected)"
            return "Server starting..."
        elif self._running_role == "client":
            return "Waiting for server to switch to this device..."
        return "Not running — choose a role above"

    def _on_start_server(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        if self._running_role == "server":
            return
        self._stop_current()
        self._start_server()

    def _on_start_client(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        if self._running_role == "client":
            return

        if not self._config.server_host:
            icon.notify(
                "Server host not configured.\n"
                "Set server_host in sharedinput.toml\n"
                "or run from CLI: sharedinput client --host <IP>",
                "SharedInput — Error",
            )
            return

        self._stop_current()
        self._start_client()

    def _on_stop(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._stop_current()
        self._update_icon_state()

    def _on_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._stop_current()
        if self._icon:
            self._icon.stop()

    def _start_server(self) -> None:
        from sharedinput.server.main import Server

        self._config.role = "server"
        self._server = Server(self._config)
        self._running_role = "server"

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._server.run())
            except Exception:
                logger.exception("Server error")
            finally:
                loop.close()

        self._server_thread = threading.Thread(target=run, daemon=True)
        self._server_thread.start()
        self._update_icon_state()
        logger.info("Server started in background")
        if self._icon:
            self._icon.notify("Server started.\nWaiting for clients...", "SharedInput")

    def _start_client(self) -> None:
        from sharedinput.client.main import Client

        self._config.role = "client"
        self._client = Client(self._config)
        self._running_role = "client"

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._client.run())
            except Exception:
                logger.exception("Client error")
            finally:
                loop.close()

        self._client_thread = threading.Thread(target=run, daemon=True)
        self._client_thread.start()
        self._update_icon_state()
        logger.info("Client started in background")
        if self._icon:
            self._icon.notify(
                f"Connected to {self._config.server_host}.\nWaiting for input...",
                "SharedInput",
            )

    def _stop_current(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._client:
            self._client.shutdown()
            self._client = None
        self._running_role = None
        self._update_icon_state()

    def _update_icon_state(self) -> None:
        if not self._icon:
            return

        if self._running_role is None:
            self._icon.icon = self._icon_disabled
        elif self._running_role == "server" and self._server and self._server._forwarding:
            self._icon.icon = self._icon_active
        else:
            self._icon.icon = self._icon_default


def run_tray(config: Config | None = None) -> None:
    """Entry point — launch the system tray app."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    app = TrayApp(config)
    app.run()

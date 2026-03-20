"""System tray UI — runs SharedInput as a menu bar / system tray app.

Menu structure:
- Idle: Start as Server, Start as Client > (discovered servers), Quit
- Server: status label, Switch Input > (devices), Disconnect, Quit
- Client: status label, Disconnect, Quit
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
from sharedinput.discovery import DiscoveryListener
from sharedinput.icons import create_active_icon, create_default_icon, create_disabled_icon

logger = logging.getLogger(__name__)

# Max slots for dynamic menu items (discovered servers / connected clients)
_MAX_DYNAMIC_ITEMS = 10


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

        # Discovery
        self._discovery = DiscoveryListener(
            discovery_port=self._config.network.discovery_port
        )
        # Snapshot of discovered servers / connected clients for menu
        self._discovered_servers: list[tuple[str, str, str, int]] = []  # (id, hostname, ip, port)
        self._connected_clients: list[tuple[str, str]] = []  # (client_id, hostname)

        # Icons
        self._icon_default = create_default_icon(64)
        self._icon_active = create_active_icon(64)
        self._icon_disabled = create_disabled_icon(64)

        # Menu refresh timer
        self._refresh_timer: threading.Timer | None = None

    def run(self) -> None:
        """Start the tray app (blocking)."""
        # Start discovery listener to find servers
        self._discovery.start()
        self._start_refresh_timer()

        self._icon = pystray.Icon(
            name="SharedInput",
            icon=self._icon_disabled,
            title="SharedInput",
            menu=self._build_menu(),
        )
        logger.info("Starting SharedInput tray app")
        self._icon.run(setup=self._on_setup)

    def _on_setup(self, icon: pystray.Icon) -> None:
        icon.visible = True
        icon.notify(
            "SharedInput is running.\nRight-click the tray icon to get started.",
            "SharedInput",
        )

    # ── Menu building ────────────────────────────────────────────────────

    def _build_menu(self) -> pystray.Menu:
        """Build the complete tray menu based on current state."""
        items: list[pystray.MenuItem] = []

        # Title
        items.append(pystray.MenuItem("SharedInput", None, enabled=False))

        if self._running_role is None:
            items.extend(self._build_idle_menu())
        elif self._running_role == "server":
            items.extend(self._build_server_menu())
        elif self._running_role == "client":
            items.extend(self._build_client_menu())

        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Quit", self._on_quit))

        return pystray.Menu(*items)

    def _build_idle_menu(self) -> list:
        """Menu items when idle (not server or client)."""
        items = []
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Start as Server", self._on_start_server))

        # "Start as Client" submenu with discovered servers
        server_items = []
        servers = self._discovered_servers
        if not servers:
            server_items.append(
                pystray.MenuItem("Scanning...", None, enabled=False)
            )
        else:
            for sid, hostname, ip, port in servers:
                label = f"{hostname} ({ip})"
                # Use default args to capture loop variables
                action = lambda icon, item, _ip=ip, _port=port: self._on_connect_to_server(_ip, _port)
                server_items.append(pystray.MenuItem(label, action))

        items.append(
            pystray.MenuItem(
                "Start as Client",
                pystray.Menu(*server_items),
            )
        )
        return items

    def _build_server_menu(self) -> list:
        """Menu items when running as server."""
        items = []
        ip = _get_local_ip()
        items.append(pystray.MenuItem(f"Server on {ip}", None, enabled=False))
        items.append(pystray.Menu.SEPARATOR)

        # "Switch Input" submenu — only if clients connected
        clients = self._connected_clients
        if clients:
            switch_items = []

            # "This Computer (Server)" — checked when no client is active
            active_id = None
            if self._server:
                active_id = self._server._switcher.active_client_id

            switch_items.append(
                pystray.MenuItem(
                    "This Computer (Server)",
                    lambda icon, item: self._on_switch_to(None),
                    checked=lambda item: (
                        self._server is not None
                        and self._server._switcher.active_client_id is None
                    ),
                )
            )

            for cid, hostname in clients:
                switch_items.append(
                    pystray.MenuItem(
                        hostname,
                        lambda icon, item, _cid=cid: self._on_switch_to(_cid),
                        checked=lambda item, _cid=cid: (
                            self._server is not None
                            and self._server._switcher.active_client_id == _cid
                        ),
                    )
                )

            items.append(
                pystray.MenuItem("Switch Input", pystray.Menu(*switch_items))
            )
        else:
            items.append(
                pystray.MenuItem("No clients connected", None, enabled=False)
            )

        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Disconnect", self._on_disconnect))
        return items

    def _build_client_menu(self) -> list:
        """Menu items when running as client."""
        items = []
        server_name = ""
        if self._client and hasattr(self._client, '_control') and self._client._control:
            server_name = self._client._control.server_hostname
        if not server_name:
            server_name = self._config.server_host
        items.append(
            pystray.MenuItem(f"Connected to {server_name}", None, enabled=False)
        )
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Disconnect", self._on_disconnect))
        return items

    # ── Menu refresh ─────────────────────────────────────────────────────

    def _start_refresh_timer(self) -> None:
        """Start periodic menu refresh (every 2 seconds)."""
        self._stop_refresh_timer()
        self._refresh_timer = threading.Timer(2.0, self._on_refresh_tick)
        self._refresh_timer.daemon = True
        self._refresh_timer.start()

    def _stop_refresh_timer(self) -> None:
        if self._refresh_timer:
            self._refresh_timer.cancel()
            self._refresh_timer = None

    def _on_refresh_tick(self) -> None:
        """Refresh menu data and schedule next tick."""
        self._update_snapshots()
        if self._icon:
            self._icon.menu = self._build_menu()
        self._update_icon_state()
        # Schedule next refresh
        self._start_refresh_timer()

    def _update_snapshots(self) -> None:
        """Update cached snapshots of servers/clients for menu building."""
        # Discovered servers (when idle)
        if self._running_role is None:
            servers = self._discovery.servers
            self._discovered_servers = [
                (s.server_id, s.hostname, s.ip, s.tcp_port)
                for s in servers.values()
            ]
        else:
            self._discovered_servers = []

        # Connected clients (when server)
        if self._running_role == "server" and self._server:
            clients = self._server._control_server.clients
            self._connected_clients = [
                (cid, info.hostname) for cid, info in clients.items()
            ]
        else:
            self._connected_clients = []

    # ── Actions ──────────────────────────────────────────────────────────

    def _on_start_server(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        if self._running_role == "server":
            return
        self._stop_current()
        self._start_server()

    def _on_connect_to_server(self, ip: str, tcp_port: int) -> None:
        """Connect to a discovered server."""
        if self._running_role is not None:
            return
        self._config.server_host = ip
        self._config.network.tcp_port = tcp_port
        self._stop_current()
        self._start_client()

    def _on_switch_to(self, client_id: str | None) -> None:
        """Switch input to a specific client or back to server."""
        if self._server:
            self._server.switch_to_client(client_id)

    def _on_disconnect(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._stop_current()
        # Restart discovery
        self._discovery.start()
        self._update_snapshots()
        if self._icon:
            self._icon.menu = self._build_menu()
        self._update_icon_state()

    def _on_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._stop_refresh_timer()
        self._stop_current()
        self._discovery.stop()
        if self._icon:
            self._icon.stop()

    # ── Server / Client lifecycle ────────────────────────────────────────

    def _start_server(self) -> None:
        from sharedinput.server.main import Server

        # Check accessibility on macOS before doing anything
        if sys.platform == "darwin":
            from sharedinput.platform.macos import ensure_accessibility
            if not ensure_accessibility(exit_on_fail=False):
                if self._icon:
                    self._icon.notify(
                        "Accessibility permission required.\n"
                        "Grant it in System Settings → Privacy & Security → Accessibility,\n"
                        "then try again.",
                        "SharedInput — Permission Needed",
                    )
                return

        # Stop discovery — we're the server now
        self._discovery.stop()

        self._config.role = "server"
        self._server = Server(self._config)
        self._running_role = "server"

        # On macOS, install the CGEventTap on the main thread
        self._server.install_capture_on_main_thread()

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
        self._update_snapshots()
        if self._icon:
            self._icon.menu = self._build_menu()
            self._icon.notify("Server started.\nWaiting for clients...", "SharedInput")
        logger.info("Server started in background")

    def _start_client(self) -> None:
        from sharedinput.client.main import Client

        # Stop discovery — we're connecting
        self._discovery.stop()

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
        if self._icon:
            self._icon.menu = self._build_menu()
            self._icon.notify(
                f"Connecting to {self._config.server_host}...",
                "SharedInput",
            )
        logger.info("Client started in background")

    def _stop_current(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._client:
            self._client.shutdown()
            self._client = None
        self._running_role = None
        self._connected_clients = []
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
    from pathlib import Path

    # Log to file so errors are visible even without a terminal
    log_dir = Path.home() / ".sharedinput"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "sharedinput.log"

    handlers: list[logging.Handler] = [
        logging.FileHandler(log_file, mode="w"),
    ]
    # Also log to stderr if available (e.g. launched from terminal)
    try:
        handlers.append(logging.StreamHandler())
    except Exception:
        pass

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )
    logger.info("SharedInput tray app starting — log file: %s", log_file)

    app = TrayApp(config)
    app.run()

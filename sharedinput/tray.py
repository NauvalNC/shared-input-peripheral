"""System tray UI — runs SharedInput as a menu bar / system tray app.

All devices broadcast AVAILABLE on launch. One device clicks
"Start as Server" — it discovers other devices and auto-connects
to them. Clients are passive (no manual action needed).

Menu:
- Idle: broadcasting presence, "Start as Server", Quit
- Server: Switch Input > (devices), Disconnect, Quit
- Client: "Controlled by [server]", Disconnect, Quit
"""

from __future__ import annotations

import asyncio
import logging
import socket
import sys
import threading

import pystray
from PIL import Image

from sharedinput.config import Config, load_config
from sharedinput.discovery import DeviceBroadcaster
from sharedinput.icons import create_active_icon, create_default_icon, create_disabled_icon

logger = logging.getLogger(__name__)

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
        self._running_role: str | None = None

        # ALL devices broadcast their presence
        self._broadcaster = DeviceBroadcaster(
            tcp_port=self._config.network.tcp_port,
            discovery_port=self._config.network.discovery_port,
        )

        # Snapshots for menu
        self._connected_clients: list[tuple[str, str]] = []  # server-side
        self._client_device_list: list[tuple[str | None, str]] = []  # client-side (id, hostname)
        self._client_active_device_id: str | None = None  # client-side active device

        # Icons
        self._icon_default = create_default_icon(64)
        self._icon_active = create_active_icon(64)
        self._icon_disabled = create_disabled_icon(64)

        # Menu refresh timer
        self._refresh_timer: threading.Timer | None = None

    def run(self) -> None:
        """Start the tray app (blocking)."""
        # Start broadcasting — all devices do this
        self._broadcaster.start()

        # Also start as passive client (TCP server waiting for connections)
        self._start_passive_client()

        self._start_refresh_timer()

        self._icon = pystray.Icon(
            name="SharedInput",
            icon=self._icon_default,
            title="SharedInput",
            menu=self._build_menu(),
        )
        logger.info("Starting SharedInput tray app")
        self._icon.run(setup=self._on_setup)

    def _on_setup(self, icon: pystray.Icon) -> None:
        icon.visible = True
        icon.notify(
            "SharedInput is ready.\nThis device is visible on the network.",
            "SharedInput",
        )

    # ── Menu building ────────────────────────────────────────────────────

    def _build_menu(self) -> pystray.Menu:
        items: list[pystray.MenuItem] = []

        if self._running_role is None or self._running_role == "passive_client":
            items.extend(self._build_idle_menu())
        elif self._running_role == "server":
            items.extend(self._build_server_menu())

        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Quit", self._on_quit))
        return pystray.Menu(*items)

    def _make_device_item(self, idx: int) -> pystray.MenuItem:
        """Pre-allocated menu slot for a device in the client-side Switch Input."""
        def on_click(icon, item):
            if idx < len(self._client_device_list):
                target_id = self._client_device_list[idx][0]  # may be None for server
                self._on_client_switch_to(target_id)

        return pystray.MenuItem(
            lambda item, i=idx: (
                self._client_device_list[i][1]
                if i < len(self._client_device_list) else ""
            ),
            on_click,
            checked=lambda item, i=idx: (
                i < len(self._client_device_list)
                and self._client_active_device_id == self._client_device_list[i][0]
            ),
            visible=lambda item, i=idx: i < len(self._client_device_list),
        )

    @property
    def _is_client_connected(self) -> bool:
        return bool(self._client and self._client._control.is_connected)

    def _build_idle_menu(self) -> list:
        items = []
        ip = _get_local_ip()

        # "Connected to [server]" — visible only when connected
        items.append(pystray.MenuItem(
            lambda item: (
                f"Connected to {self._client._control.server_hostname or 'server'}"
                if self._is_client_connected else f"Ready on {ip}"
            ),
            None,
            enabled=False,
        ))

        items.append(pystray.Menu.SEPARATOR)

        # Switch Input — visible only when connected AND has devices
        switch_items = []
        for i in range(_MAX_DYNAMIC_ITEMS):
            switch_items.append(self._make_device_item(i))
        items.append(
            pystray.MenuItem(
                "Switch Input",
                pystray.Menu(*switch_items),
                visible=lambda item: (
                    self._is_client_connected
                    and len(self._client_device_list) > 1
                ),
            )
        )

        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem(
            "Start as Server",
            self._on_start_server,
            visible=lambda item: not self._is_client_connected,
        ))
        return items

    def _make_client_item(self, idx: int) -> pystray.MenuItem:
        def on_click(icon, item):
            if idx < len(self._connected_clients):
                cid = self._connected_clients[idx][0]
                self._on_switch_to(cid)

        return pystray.MenuItem(
            lambda item, i=idx: (
                self._connected_clients[i][1]
                if i < len(self._connected_clients) else ""
            ),
            on_click,
            checked=lambda item, i=idx: (
                self._server is not None
                and i < len(self._connected_clients)
                and self._server._switcher.active_client_id == self._connected_clients[i][0]
            ),
            visible=lambda item, i=idx: i < len(self._connected_clients),
        )

    def _build_server_menu(self) -> list:
        items = []
        ip = _get_local_ip()
        items.append(pystray.MenuItem(f"Server on {ip}", None, enabled=False))
        items.append(pystray.Menu.SEPARATOR)

        # Switch Input submenu
        switch_items = [
            pystray.MenuItem(
                "This Computer (Server)",
                self._on_switch_to_local,
                checked=lambda item: (
                    self._server is not None
                    and self._server._switcher.active_client_id is None
                ),
            ),
        ]
        for i in range(_MAX_DYNAMIC_ITEMS):
            switch_items.append(self._make_client_item(i))

        items.append(
            pystray.MenuItem(
                "Switch Input",
                pystray.Menu(*switch_items),
                visible=lambda item: len(self._connected_clients) > 0,
            )
        )
        items.append(
            pystray.MenuItem(
                "Searching for devices...",
                None,
                enabled=False,
                visible=lambda item: len(self._connected_clients) == 0,
            )
        )

        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Disconnect", self._on_disconnect))
        return items

    # ── Menu refresh ─────────────────────────────────────────────────────

    def _start_refresh_timer(self) -> None:
        self._stop_refresh_timer()
        self._refresh_timer = threading.Timer(2.0, self._on_refresh_tick)
        self._refresh_timer.daemon = True
        self._refresh_timer.start()

    def _stop_refresh_timer(self) -> None:
        if self._refresh_timer:
            self._refresh_timer.cancel()
            self._refresh_timer = None

    def _on_refresh_tick(self) -> None:
        self._update_snapshots()
        if self._icon:
            try:
                self._icon.menu = self._build_menu()
                if hasattr(self._icon, 'update_menu'):
                    self._icon.update_menu()
            except Exception:
                logger.debug("Menu refresh error", exc_info=True)
        self._update_icon_state()
        self._start_refresh_timer()

    def _update_snapshots(self) -> None:
        if self._running_role == "server" and self._server:
            clients = self._server._connector.clients
            self._connected_clients = [
                (cid, info.hostname) for cid, info in clients.items()
            ]
        else:
            self._connected_clients = []

        # Client-side device list from server
        if self._client and self._client._control.is_connected:
            self._client_device_list = [
                (d.get("id"), d.get("hostname", "unknown"))
                for d in self._client._control.device_list
            ]
            self._client_active_device_id = self._client._control.active_device_id
        else:
            self._client_device_list = []
            self._client_active_device_id = None

    # ── Actions ──────────────────────────────────────────────────────────

    def _on_start_server(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        if self._running_role == "server":
            return
        self._stop_passive_client()
        self._start_server()

    def _on_switch_to_local(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._on_switch_to(None)

    def _on_switch_to(self, client_id: str | None) -> None:
        if self._server:
            self._server.switch_to_client(client_id)

    def _on_client_switch_to(self, target_id: str | None) -> None:
        """Client-side: send SWITCH_REQUEST to server."""
        if self._client and self._client._control.is_connected:
            import asyncio
            # Send async from main thread
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._client._control.send_switch_request(target_id))
            except Exception:
                logger.debug("Failed to send switch request", exc_info=True)
            finally:
                loop.close()

    def _on_disconnect(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._stop_server()
        # Go back to passive client mode
        self._start_passive_client()
        self._update_snapshots()
        if self._icon:
            self._icon.menu = self._build_menu()
        self._update_icon_state()

    def _on_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._stop_refresh_timer()
        self._stop_server()
        self._stop_passive_client()
        self._broadcaster.stop()
        if self._icon:
            self._icon.stop()

    # ── Passive client (always running when not server) ──────────────────

    def _start_passive_client(self) -> None:
        """Start as passive client — TCP server waiting for connections."""
        from sharedinput.client.main import Client

        self._client = Client(self._config)
        self._running_role = "passive_client"

        def run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._client.run())
            except Exception:
                logger.exception("Passive client error")
            finally:
                loop.close()

        self._client_thread = threading.Thread(target=run, daemon=True)
        self._client_thread.start()
        logger.info("Passive client started — waiting for server connections")

    def _stop_passive_client(self) -> None:
        if self._client:
            self._client.shutdown()
            self._client = None
        if self._running_role == "passive_client":
            self._running_role = None

    # ── Server lifecycle ─────────────────────────────────────────────────

    def _start_server(self) -> None:
        from sharedinput.server.main import Server

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
                # Restart passive client
                self._start_passive_client()
                return

        self._config.role = "server"
        self._server = Server(self._config)
        self._server.set_broadcaster(self._broadcaster)
        self._running_role = "server"
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
            self._icon.notify("Server started.\nDiscovering devices...", "SharedInput")
        logger.info("Server started in background")

    def _stop_server(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._running_role == "server":
            self._running_role = None
        self._connected_clients = []
        self._update_icon_state()

    def _update_icon_state(self) -> None:
        if not self._icon:
            return
        if self._running_role == "server" and self._server and self._server._forwarding:
            self._icon.icon = self._icon_active
        elif self._running_role == "server":
            self._icon.icon = self._icon_default
        else:
            self._icon.icon = self._icon_default


def run_tray(config: Config | None = None) -> None:
    """Entry point — launch the system tray app."""
    from pathlib import Path

    log_dir = Path.home() / ".sharedinput"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "sharedinput.log"

    handlers: list[logging.Handler] = [
        logging.FileHandler(log_file, mode="w"),
    ]
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

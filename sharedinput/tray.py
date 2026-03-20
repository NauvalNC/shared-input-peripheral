"""System tray UI — runs SharedInput as a menu bar / system tray app.

All devices broadcast AVAILABLE on launch. One device clicks
"Start as Server" — it discovers other devices and auto-connects
to them. Clients are passive (no manual action needed).

Menu is rebuilt each time it's opened via pystray's callable menu.
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

        self._broadcaster = DeviceBroadcaster(
            tcp_port=self._config.network.tcp_port,
            discovery_port=self._config.network.discovery_port,
        )

        # Icons
        self._icon_default = create_default_icon(64)
        self._icon_active = create_active_icon(64)
        self._icon_disabled = create_disabled_icon(64)

    def run(self) -> None:
        self._broadcaster.start()
        self._start_passive_client()

        self._icon = pystray.Icon(
            name="SharedInput",
            icon=self._icon_default,
            title="SharedInput",
            menu=pystray.Menu(lambda: self._menu_items()),
        )
        logger.info("Starting SharedInput tray app")
        self._icon.run(setup=self._on_setup)

    def _on_setup(self, icon: pystray.Icon) -> None:
        icon.visible = True
        icon.notify("SharedInput is ready.\nThis device is visible on the network.", "SharedInput")

    # ── Menu (rebuilt each time it's opened) ─────────────────────────────

    def _menu_items(self) -> list[pystray.MenuItem]:
        """Called by pystray each time the menu is about to be shown."""
        self._update_icon_state()

        if self._running_role == "server":
            return self._server_menu_items()
        else:
            return self._idle_menu_items()

    def _idle_menu_items(self) -> list[pystray.MenuItem]:
        items = []
        ip = _get_local_ip()

        # Connection status
        client_connected = self._client and self._client._control.is_connected
        if client_connected:
            server_name = self._client._control.server_hostname or "server"
            items.append(pystray.MenuItem(f"Connected to {server_name}", None, enabled=False))
        else:
            items.append(pystray.MenuItem(f"Ready on {ip}", None, enabled=False))

        items.append(pystray.Menu.SEPARATOR)

        # Client-side Switch Input
        if client_connected:
            device_list = [
                (d.get("id"), d.get("hostname", "unknown"))
                for d in self._client._control.device_list
            ]
            if len(device_list) > 1:
                active_id = self._client._control.active_device_id
                switch_items = []
                for did, hostname in device_list:
                    switch_items.append(pystray.MenuItem(
                        hostname,
                        self._make_client_switch_action(did),
                        checked=active_id == did,
                    ))
                items.append(pystray.MenuItem("Switch Input", pystray.Menu(*switch_items)))
                items.append(pystray.Menu.SEPARATOR)

        items.append(pystray.MenuItem("Start as Server", self._on_start_server))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Quit", self._on_quit))
        return items

    def _server_menu_items(self) -> list[pystray.MenuItem]:
        items = []
        ip = _get_local_ip()
        items.append(pystray.MenuItem(f"Server on {ip}", None, enabled=False))
        items.append(pystray.Menu.SEPARATOR)

        # Server-side Switch Input
        if self._server:
            clients = self._server._connector.clients
            if clients:
                active_id = self._server._switcher.active_client_id
                switch_items = [
                    pystray.MenuItem(
                        "This Computer (Server)",
                        self._make_server_switch_action(None),
                        checked=active_id is None,
                    ),
                ]
                for cid, info in clients.items():
                    switch_items.append(pystray.MenuItem(
                        info.hostname,
                        self._make_server_switch_action(cid),
                        checked=active_id == cid,
                    ))
                items.append(pystray.MenuItem("Switch Input", pystray.Menu(*switch_items)))
            else:
                items.append(pystray.MenuItem("Searching for devices...", None, enabled=False))

        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Disconnect", self._on_disconnect))
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("Quit", self._on_quit))
        return items

    def _make_server_switch_action(self, client_id: str | None):
        def action(icon, item):
            if self._server:
                self._server.switch_to_client(client_id)
        return action

    def _make_client_switch_action(self, target_id: str | None):
        def action(icon, item):
            if self._client and self._client._control.is_connected:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(self._client._control.send_switch_request(target_id))
                except Exception:
                    logger.debug("Failed to send switch request", exc_info=True)
                finally:
                    loop.close()
        return action

    # ── Actions ──────────────────────────────────────────────────────────

    def _on_start_server(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        if self._running_role == "server":
            return
        self._stop_passive_client()
        self._start_server()

    def _on_disconnect(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._stop_server()
        self._start_passive_client()
        self._update_icon_state()

    def _on_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._stop_server()
        self._stop_passive_client()
        self._broadcaster.stop()
        if self._icon:
            self._icon.stop()

    # ── Passive client ───────────────────────────────────────────────────

    def _start_passive_client(self) -> None:
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
        logger.info("Passive client started")

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
                self._start_passive_client()
                return

        self._config.role = "server"
        self._broadcaster.set_role("server")
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
        if self._icon:
            self._icon.notify("Server started.\nDiscovering devices...", "SharedInput")
        logger.info("Server started in background")

    def _stop_server(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._running_role == "server":
            self._running_role = None
        self._broadcaster.set_role("idle")
        self._update_icon_state()

    def _update_icon_state(self) -> None:
        if not self._icon:
            return
        if self._running_role == "server" and self._server and self._server._forwarding:
            self._icon.icon = self._icon_active
        else:
            self._icon.icon = self._icon_default


def run_tray(config: Config | None = None) -> None:
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

"""Server orchestration — wires capture, network, and switcher together."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import threading

from sharedinput.config import Config
from sharedinput.server.capture import InputCapture
from sharedinput.server.network import ControlServer, UDPSender
from sharedinput.server.switcher import HotkeySwitcher

logger = logging.getLogger(__name__)


class Server:
    """Main server — captures input and forwards to active client."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._udp_sender = UDPSender(port=config.network.udp_port)
        self._control_server = ControlServer(port=config.network.tcp_port)
        self._switcher = HotkeySwitcher(on_switch=self._on_switch)
        self._capture: InputCapture | None = None
        self._forwarding = False
        self._shutdown_event = asyncio.Event()

    def _on_event(self, event) -> None:
        """Callback from input capture — forward event if active."""
        if self._forwarding:
            self._udp_sender.send(event)

    def _on_switch(self, client_id: str | None) -> None:
        """Callback from switcher — update forwarding target."""
        if client_id is None:
            self._udp_sender.clear_target()
            self._forwarding = False
            logger.info("Now controlling: LOCAL")
        else:
            clients = self._control_server.clients
            if client_id in clients:
                client = clients[client_id]
                self._udp_sender.set_target(*client.address)
                self._forwarding = True
                logger.info("Now controlling: %s (%s)", client.hostname, client.address[0])

    async def _monitor_clients(self) -> None:
        """Periodically update the switcher with the current client list."""
        while not self._shutdown_event.is_set():
            self._switcher.update_clients(self._control_server.clients)
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass

    async def run(self) -> None:
        """Run the server."""
        # Platform checks
        if sys.platform == "darwin":
            from sharedinput.platform.macos import ensure_accessibility
            ensure_accessibility()
        elif sys.platform == "win32":
            from sharedinput.platform.windows import warn_if_not_admin
            warn_if_not_admin()

        # Start control server
        await self._control_server.start()

        # Start hotkey switcher
        self._switcher.start()

        # Start input capture
        self._capture = InputCapture(event_callback=self._on_event)
        self._capture.start()

        local_ip = _get_local_ip()
        logger.info("Server started — IP: %s, TCP: %d, UDP: %d",
                     local_ip, self._config.network.tcp_port, self._config.network.udp_port)
        logger.info("Waiting for clients to connect...")
        logger.info("Hotkey: Ctrl+Alt+Arrow to switch devices")

        # Run until shutdown
        try:
            await self._monitor_clients()
        except asyncio.CancelledError:
            pass
        finally:
            self._cleanup()

    def _cleanup(self) -> None:
        if self._capture:
            self._capture.stop()
        self._switcher.stop()
        self._udp_sender.close()

    def shutdown(self) -> None:
        self._shutdown_event.set()


def _get_local_ip() -> str:
    """Get the machine's local network IP address."""
    try:
        s = __import__("socket").socket(__import__("socket").AF_INET, __import__("socket").SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def run_server(config: Config) -> None:
    """Entry point to run the server."""
    server = Server(config)

    loop = asyncio.new_event_loop()

    def signal_handler():
        server.shutdown()

    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, signal_handler)
        loop.add_signal_handler(signal.SIGTERM, signal_handler)

    try:
        loop.run_until_complete(server.run())
    except KeyboardInterrupt:
        server.shutdown()
    finally:
        loop.close()

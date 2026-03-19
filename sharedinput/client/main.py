"""Client orchestration — receives events from server and injects them."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import threading

from sharedinput.client.injector import InputInjector
from sharedinput.client.network import ControlClient, UDPReceiver
from sharedinput.config import Config

logger = logging.getLogger(__name__)


class Client:
    """Main client — receives input events and injects them locally."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._receiver = UDPReceiver(port=config.network.udp_port)
        self._control = ControlClient(
            server_host=config.server_host,
            server_port=config.network.tcp_port,
            udp_port=config.network.udp_port,
        )
        self._injector = InputInjector()
        self._running = False

    def _receive_loop(self) -> None:
        """Blocking loop that receives and injects input events."""
        self._receiver.start()
        while self._running:
            event = self._receiver.receive()
            if event is not None:
                self._injector.inject(event)
        self._receiver.stop()

    async def run(self) -> None:
        """Run the client."""
        # Platform checks
        if sys.platform == "darwin":
            from sharedinput.platform.macos import ensure_accessibility
            ensure_accessibility()
        elif sys.platform == "win32":
            from sharedinput.platform.windows import warn_if_not_admin
            warn_if_not_admin()

        # Connect to server control plane
        await self._control.connect()
        logger.info("Client %s registered with server", self._control.client_id)

        # Start UDP receiver in a separate thread
        self._running = True
        recv_thread = threading.Thread(target=self._receive_loop, daemon=True)
        recv_thread.start()

        logger.info("Listening for input events on UDP port %d", self._config.network.udp_port)
        logger.info("Waiting for server to switch to this device...")

        # Run control tasks
        try:
            await asyncio.gather(
                self._control.heartbeat_loop(),
                self._control.listen_for_commands(),
            )
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            await self._control.disconnect()

    def shutdown(self) -> None:
        self._running = False


def run_client(config: Config) -> None:
    """Entry point to run the client."""
    client = Client(config)

    loop = asyncio.new_event_loop()

    def signal_handler():
        client.shutdown()

    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, signal_handler)
        loop.add_signal_handler(signal.SIGTERM, signal_handler)

    try:
        loop.run_until_complete(client.run())
    except KeyboardInterrupt:
        client.shutdown()
    finally:
        loop.close()

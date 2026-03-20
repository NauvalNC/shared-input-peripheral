"""Client orchestration — receives events from server and injects them.

The client is PASSIVE: it runs a TCP control server and waits for the
server to connect.  Once connected, it receives input via UDP.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import threading

from sharedinput.client.injector import InputInjector
from sharedinput.client.network import ClientControlServer, UDPReceiver
from sharedinput.config import Config

logger = logging.getLogger(__name__)


class Client:
    """Main client — waits for server connection, receives and injects input."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._receiver = UDPReceiver(port=config.network.udp_port)
        self._control = ClientControlServer(
            port=config.network.tcp_port,
            udp_port=config.network.udp_port,
        )
        self._injector = InputInjector()
        self._running = False
        self._shutdown_flag = threading.Event()

    def _receive_loop(self) -> None:
        """Blocking loop that receives and injects input events."""
        self._receiver.start()
        while self._running:
            event = self._receiver.receive()
            if event is not None:
                self._injector.inject(event)
        self._receiver.stop()

    async def run(self) -> None:
        """Run the client (passive — waits for server to connect)."""
        # Start TCP control server (server will connect TO us)
        await self._control.start()

        # Start UDP receiver in a separate thread
        self._running = True
        recv_thread = threading.Thread(target=self._receive_loop, daemon=True)
        recv_thread.start()

        logger.info("Client ready — waiting for server to connect (TCP %d, UDP %d)",
                     self._config.network.tcp_port, self._config.network.udp_port)

        # Wait until shutdown
        while not self._shutdown_flag.is_set():
            await asyncio.sleep(1.0)

        # Cleanup
        self._running = False
        await self._control.stop()

    def shutdown(self) -> None:
        self._shutdown_flag.set()
        self._running = False


def run_client(config: Config) -> None:
    """Entry point to run the client (CLI mode)."""
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

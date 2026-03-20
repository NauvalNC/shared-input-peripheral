"""Client networking — UDP receiver for input events + TCP control client.

The UDP receiver listens for serialized input events from the server and
passes them to the injector.  The TCP control client handles registration
and heartbeats.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import socket
import struct
import time
import uuid

from sharedinput.protocol import InputEvent, deserialize

logger = logging.getLogger(__name__)

DEFAULT_UDP_PORT = 9876
DEFAULT_TCP_PORT = 9877


class UDPReceiver:
    """Receives serialized input events from the server over UDP."""

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_UDP_PORT) -> None:
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._running = False

    def start(self) -> None:
        """Bind the UDP socket and start listening."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self._host, self._port))
        self._sock.settimeout(0.1)  # Non-blocking with short timeout
        self._running = True
        logger.info("UDP receiver listening on %s:%d", self._host, self._port)

    def stop(self) -> None:
        """Stop listening and close the socket."""
        self._running = False
        if self._sock:
            self._sock.close()
            self._sock = None

    def receive(self) -> InputEvent | None:
        """Try to receive and deserialize one event. Returns None on timeout."""
        if not self._sock or not self._running:
            return None
        try:
            data, addr = self._sock.recvfrom(1024)
            return deserialize(data)
        except socket.timeout:
            return None
        except (OSError, ValueError, struct.error) as e:
            logger.debug("UDP receive error: %s", e)
            return None


class ControlClient:
    """TCP control client — registers with the server and sends heartbeats."""

    def __init__(
        self,
        server_host: str,
        server_port: int = DEFAULT_TCP_PORT,
        udp_port: int = DEFAULT_UDP_PORT,
    ) -> None:
        self._server_host = server_host
        self._server_port = server_port
        self._udp_port = udp_port
        self._client_id = str(uuid.uuid4())[:8]
        self._hostname = platform.node()
        self._platform = platform.system()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def client_id(self) -> str:
        return self._client_id

    async def connect(self) -> None:
        """Connect to the server control plane."""
        self._reader, self._writer = await asyncio.open_connection(
            self._server_host, self._server_port
        )
        logger.info("Connected to control server at %s:%d", self._server_host, self._server_port)

        # Register
        msg = {
            "type": "REGISTER",
            "client_id": self._client_id,
            "hostname": self._hostname,
            "platform": self._platform,
            "udp_port": self._udp_port,
        }
        self._writer.write(json.dumps(msg).encode() + b"\n")
        await self._writer.drain()

        # Wait for registration response
        line = await self._reader.readline()
        if line:
            try:
                response = json.loads(line.decode())
                logger.info("Registration response: %s", response)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning("Invalid registration response: %s", e)

    async def heartbeat_loop(self) -> None:
        """Send periodic heartbeats to the server."""
        while self._writer and not self._writer.is_closing():
            try:
                msg = {"type": "HEARTBEAT", "client_id": self._client_id}
                self._writer.write(json.dumps(msg).encode() + b"\n")
                await self._writer.drain()
                await asyncio.sleep(2.0)
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError,
                    OSError, asyncio.CancelledError):
                break

    async def listen_for_commands(self) -> None:
        """Listen for control commands from the server (e.g., SWITCH)."""
        if not self._reader:
            return

        while True:
            try:
                line = await self._reader.readline()
                if not line:
                    break
                msg = json.loads(line.decode())
                if msg.get("type") == "SWITCH":
                    self._active = msg.get("active", False)
                    status = "ACTIVE" if self._active else "INACTIVE"
                    logger.info("Switch notification: now %s", status)
                elif msg.get("type") == "HEARTBEAT_ACK":
                    pass
            except (json.JSONDecodeError, UnicodeDecodeError, ConnectionResetError,
                    BrokenPipeError, OSError, asyncio.CancelledError):
                break

    async def disconnect(self) -> None:
        """Disconnect from the server."""
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
            self._writer = None
            self._reader = None

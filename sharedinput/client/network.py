"""Client networking — UDP receiver for input events + TCP control server.

The client is PASSIVE: it runs a TCP control server and waits for the
server to connect to it (the server discovers devices via UDP broadcast
and initiates TCP connections).  Once connected, the client receives
input events via UDP and injects them locally.
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
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self._host, self._port))
        self._sock.settimeout(0.1)
        self._running = True
        logger.info("UDP receiver listening on %s:%d", self._host, self._port)

    def stop(self) -> None:
        self._running = False
        if self._sock:
            self._sock.close()
            self._sock = None

    def receive(self) -> InputEvent | None:
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


class ClientControlServer:
    """TCP control server on the CLIENT side.

    Accepts incoming connections from the SharedInput server.
    The server connects to us (we don't connect to the server).
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = DEFAULT_TCP_PORT,
        udp_port: int = DEFAULT_UDP_PORT,
    ) -> None:
        self._host = host
        self._port = port
        self._udp_port = udp_port
        self._hostname = platform.node()
        self._platform_name = platform.system()
        self._tcp_server: asyncio.Server | None = None
        self._server_hostname: str = ""
        self._connected = False
        self._writer: asyncio.StreamWriter | None = None

    @property
    def server_hostname(self) -> str:
        return self._server_hostname

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        """Start TCP server, waiting for the server to connect to us."""
        self._tcp_server = await asyncio.start_server(
            self._handle_server_connection, self._host, self._port
        )
        logger.info("Client control server listening on %s:%d", self._host, self._port)

    async def stop(self) -> None:
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
            self._writer = None
        if self._tcp_server:
            self._tcp_server.close()
            await self._tcp_server.wait_closed()
        self._connected = False

    async def _handle_server_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle incoming TCP connection from the server."""
        peer = writer.get_extra_info("peername")
        logger.info("Server connected from %s", peer)
        self._writer = writer
        self._connected = True

        try:
            # Read the SERVER_HELLO
            line = await reader.readline()
            if line:
                try:
                    msg = json.loads(line.decode())
                    if msg.get("type") == "SERVER_HELLO":
                        self._server_hostname = msg.get("hostname", "")
                        logger.info("Server identified: %s", self._server_hostname)

                        # Send our info back
                        from sharedinput.platform import get_screen_resolution
                        screen_w, screen_h = get_screen_resolution()

                        response = {
                            "type": "CLIENT_INFO",
                            "hostname": self._hostname,
                            "platform": self._platform_name,
                            "udp_port": self._udp_port,
                            "screen_width": screen_w,
                            "screen_height": screen_h,
                        }
                        writer.write(json.dumps(response).encode() + b"\n")
                        await writer.drain()
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    logger.warning("Invalid server message: %s", e)

            # Listen for commands (SWITCH, etc.)
            while True:
                try:
                    line = await reader.readline()
                    if not line:
                        break
                    msg = json.loads(line.decode())
                    msg_type = msg.get("type")
                    if msg_type == "HEARTBEAT":
                        writer.write(json.dumps({"type": "HEARTBEAT_ACK"}).encode() + b"\n")
                        await writer.drain()
                except (json.JSONDecodeError, UnicodeDecodeError, ConnectionResetError,
                        BrokenPipeError, OSError, asyncio.CancelledError):
                    break

        finally:
            self._connected = False
            self._server_hostname = ""
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            logger.info("Server disconnected")

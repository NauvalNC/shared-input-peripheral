"""Server networking — UDP sender for input events + TCP client connector.

The server discovers devices on the LAN and initiates TCP connections TO them.
Each connected device becomes a client that can receive forwarded input.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import socket
import time
from dataclasses import dataclass, field
from typing import Callable

from sharedinput.protocol import InputEvent, serialize

logger = logging.getLogger(__name__)

DEFAULT_UDP_PORT = 9876
DEFAULT_TCP_PORT = 9877


@dataclass
class ClientInfo:
    """Represents a connected client device."""
    client_id: str  # device_id from discovery
    hostname: str
    platform: str
    address: tuple[str, int]  # (ip, udp_port)
    screen_width: int = 1920
    screen_height: int = 1080
    writer: asyncio.StreamWriter | None = field(default=None, repr=False)
    last_heartbeat: float = field(default_factory=time.monotonic)


class UDPSender:
    """Sends serialized input events to the active client over UDP."""

    def __init__(self, port: int = DEFAULT_UDP_PORT) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._target: tuple[str, int] | None = None

    def set_target(self, address: str, port: int) -> None:
        self._target = (address, port)
        logger.info("UDP target set to %s:%d", address, port)

    def clear_target(self) -> None:
        self._target = None
        logger.info("UDP target cleared (local mode)")

    def send(self, event: InputEvent) -> None:
        if self._target is None:
            return
        data = serialize(event)
        try:
            self._sock.sendto(data, self._target)
        except OSError as e:
            logger.debug("UDP send error: %s", e)

    def close(self) -> None:
        self._sock.close()


class ClientConnector:
    """Manages outbound TCP connections to client devices.

    The server discovers devices via UDP broadcast and connects TO them.
    Each connected device can receive forwarded input events.
    """

    def __init__(self, on_switch_request: Callable[[str | None], None] | None = None) -> None:
        self._clients: dict[str, ClientInfo] = {}
        self._connecting: set[str] = set()
        self._on_switch_request = on_switch_request

    @property
    def clients(self) -> dict[str, ClientInfo]:
        return self._clients

    async def connect_to_device(self, device_id: str, ip: str, tcp_port: int) -> bool:
        """Initiate TCP connection to a discovered device.

        Sends SERVER_HELLO, receives CLIENT_INFO response.
        Returns True on success.
        """
        if device_id in self._clients or device_id in self._connecting:
            return False  # already connected or connecting

        self._connecting.add(device_id)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, tcp_port),
                timeout=5.0,
            )
        except (OSError, asyncio.TimeoutError) as e:
            logger.debug("Failed to connect to %s:%d: %s", ip, tcp_port, e)
            self._connecting.discard(device_id)
            return False

        try:
            # Send SERVER_HELLO
            hello = {
                "type": "SERVER_HELLO",
                "hostname": platform.node(),
            }
            writer.write(json.dumps(hello).encode() + b"\n")
            await writer.drain()

            # Wait for CLIENT_INFO response
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not line:
                writer.close()
                self._connecting.discard(device_id)
                return False

            msg = json.loads(line.decode())
            hostname = msg.get("hostname", "unknown")
            plat = msg.get("platform", "unknown")
            udp_port = msg.get("udp_port", DEFAULT_UDP_PORT)
            screen_w = msg.get("screen_width", 1920)
            screen_h = msg.get("screen_height", 1080)

            self._clients[device_id] = ClientInfo(
                client_id=device_id,
                hostname=hostname,
                platform=plat,
                address=(ip, udp_port),
                screen_width=screen_w,
                screen_height=screen_h,
                writer=writer,
            )
            logger.info("Connected to client: %s (%s) at %s:%d [%dx%d]",
                         hostname, plat, ip, udp_port, screen_w, screen_h)

            # Start heartbeat for this client
            asyncio.ensure_future(self._heartbeat_loop(device_id, reader, writer))
            return True

        except (json.JSONDecodeError, asyncio.TimeoutError, OSError) as e:
            logger.debug("Handshake failed with %s:%d: %s", ip, tcp_port, e)
            writer.close()
            return False
        finally:
            self._connecting.discard(device_id)

    async def _heartbeat_loop(
        self, device_id: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Send periodic heartbeats and listen for client messages."""
        # Start reader task for SWITCH_REQUEST etc.
        reader_task = asyncio.ensure_future(self._client_reader(device_id, reader))
        try:
            while device_id in self._clients and not writer.is_closing():
                try:
                    msg = {"type": "HEARTBEAT"}
                    writer.write(json.dumps(msg).encode() + b"\n")
                    await writer.drain()
                    await asyncio.sleep(2.0)
                except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError,
                        OSError, asyncio.CancelledError):
                    break
        finally:
            reader_task.cancel()
            self._disconnect_client(device_id)

    async def _client_reader(
        self, device_id: str, reader: asyncio.StreamReader
    ) -> None:
        """Read messages from a client (e.g. SWITCH_REQUEST)."""
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode())
                    msg_type = msg.get("type")
                    if msg_type == "SWITCH_REQUEST" and self._on_switch_request:
                        target_id = msg.get("target_id")  # None = server
                        self._on_switch_request(target_id)
                    elif msg_type == "HEARTBEAT_ACK":
                        if device_id in self._clients:
                            self._clients[device_id].last_heartbeat = time.monotonic()
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
        except (ConnectionResetError, BrokenPipeError, OSError, asyncio.CancelledError):
            pass

    async def broadcast_device_list(self, active_id: str | None, server_hostname: str) -> None:
        """Send the current device list and active device to ALL clients."""
        devices = [
            {"id": None, "hostname": server_hostname},  # server is always first
        ]
        for cid, info in self._clients.items():
            devices.append({"id": cid, "hostname": info.hostname})

        msg = {
            "type": "DEVICE_LIST",
            "devices": devices,
            "active_id": active_id,
            "server_hostname": server_hostname,
        }
        data = json.dumps(msg).encode() + b"\n"

        for client in list(self._clients.values()):
            if client.writer and not client.writer.is_closing():
                try:
                    client.writer.write(data)
                    await client.writer.drain()
                except (OSError, ConnectionResetError):
                    pass

    def _disconnect_client(self, device_id: str) -> None:
        """Remove a disconnected client."""
        client = self._clients.pop(device_id, None)
        if client:
            logger.info("Client disconnected: %s (%s)", client.hostname, device_id)
            if client.writer:
                client.writer.close()

    async def disconnect_all(self) -> None:
        """Disconnect from all clients."""
        for device_id, client in list(self._clients.items()):
            if client.writer:
                client.writer.close()
                try:
                    await client.writer.wait_closed()
                except OSError:
                    pass
        self._clients.clear()

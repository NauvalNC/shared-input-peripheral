"""Server networking — UDP sender for input events + TCP control server.

The UDP sender serializes input events and sends them to the active client.
The TCP control server handles client registration, heartbeats, and switching.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import ssl
import time
from dataclasses import dataclass, field

from sharedinput.protocol import InputEvent, serialize

logger = logging.getLogger(__name__)

DEFAULT_UDP_PORT = 9876
DEFAULT_TCP_PORT = 9877


@dataclass
class ClientInfo:
    """Represents a connected client device."""
    client_id: str
    hostname: str
    platform: str
    address: tuple[str, int]  # (ip, udp_port)
    last_heartbeat: float = field(default_factory=time.monotonic)


class UDPSender:
    """Sends serialized input events to the active client over UDP."""

    def __init__(self, port: int = DEFAULT_UDP_PORT) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._target: tuple[str, int] | None = None

    def set_target(self, address: str, port: int) -> None:
        """Set the target client to send events to."""
        self._target = (address, port)
        logger.info("UDP target set to %s:%d", address, port)

    def clear_target(self) -> None:
        """Clear the target — stop sending events."""
        self._target = None
        logger.info("UDP target cleared (local mode)")

    def send(self, event: InputEvent) -> None:
        """Serialize and send an event to the active client."""
        if self._target is None:
            return
        data = serialize(event)
        try:
            self._sock.sendto(data, self._target)
        except OSError as e:
            logger.debug("UDP send error: %s", e)

    def close(self) -> None:
        self._sock.close()


class ControlServer:
    """TCP control server for client registration and management."""

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_TCP_PORT) -> None:
        self._host = host
        self._port = port
        self._clients: dict[str, ClientInfo] = {}
        self._server: asyncio.Server | None = None
        self._on_client_connected: asyncio.Event = asyncio.Event()

    @property
    def clients(self) -> dict[str, ClientInfo]:
        return self._clients

    async def start(self) -> None:
        """Start the TCP control server."""
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port
        )
        addrs = [s.getsockname() for s in self._server.sockets]
        logger.info("Control server listening on %s", addrs)

    async def stop(self) -> None:
        """Stop the control server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a client TCP connection."""
        peer = writer.get_extra_info("peername")
        logger.info("Control connection from %s", peer)

        try:
            while True:
                line = await reader.readline()
                if not line:
                    break

                try:
                    msg = json.loads(line.decode())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                response = self._process_message(msg, peer)
                if response:
                    writer.write(json.dumps(response).encode() + b"\n")
                    await writer.drain()
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            # Remove client on disconnect
            client_id = None
            for cid, info in list(self._clients.items()):
                if info.address[0] == peer[0]:
                    client_id = cid
                    break
            if client_id:
                del self._clients[client_id]
                logger.info("Client disconnected: %s", client_id)
            writer.close()

    def _process_message(
        self, msg: dict, peer: tuple[str, int]
    ) -> dict | None:
        """Process a control message from a client."""
        msg_type = msg.get("type")

        if msg_type == "REGISTER":
            client_id = msg.get("client_id", "")
            hostname = msg.get("hostname", "unknown")
            platform = msg.get("platform", "unknown")
            udp_port = msg.get("udp_port", DEFAULT_UDP_PORT)

            self._clients[client_id] = ClientInfo(
                client_id=client_id,
                hostname=hostname,
                platform=platform,
                address=(peer[0], udp_port),
            )
            logger.info("Client registered: %s (%s) at %s:%d", hostname, platform, peer[0], udp_port)
            self._on_client_connected.set()
            return {"type": "REGISTERED", "status": "ok"}

        elif msg_type == "HEARTBEAT":
            client_id = msg.get("client_id", "")
            if client_id in self._clients:
                self._clients[client_id].last_heartbeat = time.monotonic()
            return {"type": "HEARTBEAT_ACK"}

        return None

    async def notify_switch(
        self, writer: asyncio.StreamWriter, active: bool
    ) -> None:
        """Notify a client that it is now active or inactive."""
        msg = {"type": "SWITCH", "active": active}
        writer.write(json.dumps(msg).encode() + b"\n")
        await writer.drain()

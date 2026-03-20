"""LAN server discovery via UDP broadcast.

The server periodically broadcasts its presence on the local network.
Clients (or idle tray apps) listen for these announcements to populate
the "Start as Client" submenu with available servers.

Uses only stdlib — no external dependencies.
"""

from __future__ import annotations

import json
import logging
import platform
import socket
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_DISCOVERY_PORT = 9878
_BROADCAST_INTERVAL = 2.0  # seconds between announcements
_SERVER_TTL = 6.0  # seconds before a server is considered gone


def _get_local_ip() -> str:
    """Get the machine's local network IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


@dataclass
class ServerInfo:
    """A discovered server on the LAN."""
    server_id: str
    hostname: str
    ip: str
    tcp_port: int
    last_seen: float = field(default_factory=time.monotonic)


class DiscoveryBroadcaster:
    """Broadcasts server presence on the LAN via UDP.

    Used by the server to announce itself so that clients can discover
    it without manual IP configuration.
    """

    def __init__(
        self,
        tcp_port: int = 9877,
        discovery_port: int = DEFAULT_DISCOVERY_PORT,
    ) -> None:
        self._tcp_port = tcp_port
        self._discovery_port = discovery_port
        self._server_id = str(uuid.uuid4())[:8]
        self._hostname = platform.node()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start broadcasting in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self._thread.start()
        logger.info("Discovery broadcaster started on port %d", self._discovery_port)

    def stop(self) -> None:
        """Stop broadcasting."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        logger.info("Discovery broadcaster stopped")

    def _broadcast_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(1.0)

        local_ip = _get_local_ip()
        payload = json.dumps({
            "type": "ANNOUNCE",
            "server_id": self._server_id,
            "hostname": self._hostname,
            "ip": local_ip,
            "tcp_port": self._tcp_port,
            "version": 1,
        }).encode()

        try:
            while self._running:
                try:
                    sock.sendto(payload, ("255.255.255.255", self._discovery_port))
                except OSError as e:
                    logger.debug("Broadcast send error: %s", e)
                time.sleep(_BROADCAST_INTERVAL)
        finally:
            sock.close()


class DiscoveryListener:
    """Listens for server announcements on the LAN.

    Used by the tray app (when idle) to discover available servers
    for the "Start as Client" submenu.
    """

    def __init__(self, discovery_port: int = DEFAULT_DISCOVERY_PORT) -> None:
        self._discovery_port = discovery_port
        self._servers: dict[str, ServerInfo] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def servers(self) -> dict[str, ServerInfo]:
        """Return a snapshot of discovered servers (thread-safe)."""
        with self._lock:
            # Prune expired servers
            now = time.monotonic()
            expired = [
                sid for sid, info in self._servers.items()
                if now - info.last_seen > _SERVER_TTL
            ]
            for sid in expired:
                del self._servers[sid]
            return dict(self._servers)

    def start(self) -> None:
        """Start listening in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info("Discovery listener started on port %d", self._discovery_port)

    def stop(self) -> None:
        """Stop listening."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        with self._lock:
            self._servers.clear()
        logger.info("Discovery listener stopped")

    def _listen_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # SO_REUSEPORT allows multiple listeners on macOS/Linux
        if sys.platform != "win32" and hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.bind(("", self._discovery_port))
        sock.settimeout(1.0)

        try:
            while self._running:
                try:
                    data, addr = sock.recvfrom(1024)
                    self._process_announcement(data, addr)
                except socket.timeout:
                    continue
                except OSError as e:
                    if self._running:
                        logger.debug("Discovery listen error: %s", e)
        finally:
            sock.close()

    def _process_announcement(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            msg = json.loads(data.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        if msg.get("type") != "ANNOUNCE":
            return

        server_id = msg.get("server_id", "")
        hostname = msg.get("hostname", "unknown")
        ip = msg.get("ip", addr[0])
        tcp_port = msg.get("tcp_port", 9877)

        # Ignore our own broadcasts
        local_ip = _get_local_ip()
        if ip == local_ip:
            return

        with self._lock:
            self._servers[server_id] = ServerInfo(
                server_id=server_id,
                hostname=hostname,
                ip=ip,
                tcp_port=tcp_port,
                last_seen=time.monotonic(),
            )

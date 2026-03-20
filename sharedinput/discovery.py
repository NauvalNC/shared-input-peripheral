"""LAN device discovery via UDP broadcast.

Every device broadcasts AVAILABLE when the app is running.
When a device becomes the server, it listens for AVAILABLE broadcasts
and auto-connects to discovered devices.

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
from typing import Callable

logger = logging.getLogger(__name__)

DEFAULT_DISCOVERY_PORT = 9878
_BROADCAST_INTERVAL = 2.0  # seconds between announcements
_DEVICE_TTL = 6.0  # seconds before a device is considered gone


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


def _get_subnet_broadcast(local_ip: str) -> str | None:
    """Derive the subnet broadcast address (assumes /24 subnet)."""
    try:
        parts = local_ip.split(".")
        if len(parts) == 4:
            return f"{parts[0]}.{parts[1]}.{parts[2]}.255"
    except Exception:
        pass
    return None


@dataclass
class DeviceInfo:
    """A discovered device on the LAN."""
    device_id: str
    hostname: str
    ip: str
    tcp_port: int
    last_seen: float = field(default_factory=time.monotonic)


class DeviceBroadcaster:
    """Broadcasts this device's presence on the LAN via UDP.

    ALL devices run this when the app is open — not just servers.
    This allows servers to discover available devices to connect to.
    """

    def __init__(
        self,
        tcp_port: int = 9877,
        discovery_port: int = DEFAULT_DISCOVERY_PORT,
    ) -> None:
        self._tcp_port = tcp_port
        self._discovery_port = discovery_port
        self._device_id = str(uuid.uuid4())[:8]
        self._hostname = platform.node()
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def device_id(self) -> str:
        return self._device_id

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._broadcast_loop, daemon=True)
        self._thread.start()
        logger.info("Device broadcaster started (port %d, id=%s)", self._discovery_port, self._device_id)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        logger.info("Device broadcaster stopped")

    def _broadcast_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(1.0)

        local_ip = _get_local_ip()
        payload = json.dumps({
            "type": "AVAILABLE",
            "device_id": self._device_id,
            "hostname": self._hostname,
            "ip": local_ip,
            "tcp_port": self._tcp_port,
        }).encode()

        targets = [("255.255.255.255", self._discovery_port)]
        subnet_broadcast = _get_subnet_broadcast(local_ip)
        if subnet_broadcast and subnet_broadcast != "255.255.255.255":
            targets.append((subnet_broadcast, self._discovery_port))

        try:
            while self._running:
                for target in targets:
                    try:
                        sock.sendto(payload, target)
                    except OSError as e:
                        logger.debug("Broadcast send error to %s: %s", target, e)
                time.sleep(_BROADCAST_INTERVAL)
        finally:
            sock.close()


class DeviceListener:
    """Listens for AVAILABLE device broadcasts on the LAN.

    Used by the server to discover devices it can connect to.
    Fires ``on_device_found`` for each newly discovered device.
    """

    def __init__(
        self,
        discovery_port: int = DEFAULT_DISCOVERY_PORT,
        on_device_found: Callable[[DeviceInfo], None] | None = None,
        ignore_device_id: str = "",
    ) -> None:
        self._discovery_port = discovery_port
        self._devices: dict[str, DeviceInfo] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._on_device_found = on_device_found
        self._ignore_device_id = ignore_device_id  # skip own broadcasts

    @property
    def devices(self) -> dict[str, DeviceInfo]:
        """Return a snapshot of discovered devices (thread-safe)."""
        with self._lock:
            now = time.monotonic()
            expired = [
                did for did, info in self._devices.items()
                if now - info.last_seen > _DEVICE_TTL
            ]
            for did in expired:
                del self._devices[did]
            return dict(self._devices)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info("Device listener started on port %d", self._discovery_port)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        with self._lock:
            self._devices.clear()
        logger.info("Device listener stopped")

    def _listen_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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
                        logger.debug("Device listen error: %s", e)
        finally:
            sock.close()

    def _process_announcement(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            msg = json.loads(data.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        if msg.get("type") != "AVAILABLE":
            return

        device_id = msg.get("device_id", "")
        if device_id == self._ignore_device_id:
            return  # skip own broadcasts

        hostname = msg.get("hostname", "unknown")
        ip = msg.get("ip", addr[0])
        tcp_port = msg.get("tcp_port", 9877)

        info = DeviceInfo(
            device_id=device_id,
            hostname=hostname,
            ip=ip,
            tcp_port=tcp_port,
            last_seen=time.monotonic(),
        )

        with self._lock:
            is_new = device_id not in self._devices
            self._devices[device_id] = info

        if is_new and self._on_device_found:
            logger.info("New device discovered: %s (%s)", hostname, ip)
            self._on_device_found(info)

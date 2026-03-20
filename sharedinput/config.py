"""Configuration — loads settings from TOML config files."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Python 3.11+ has tomllib in stdlib
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class HotkeyConfig:
    next_device: str = "ctrl+alt+right"
    prev_device: str = "ctrl+alt+left"
    back_to_local: str = "ctrl+alt+home"


@dataclass
class NetworkConfig:
    udp_port: int = 9876
    tcp_port: int = 9877
    discovery_port: int = 9878
    encryption: bool = True


@dataclass
class Config:
    role: str = "server"  # "server" or "client"
    server_host: str = ""  # only used when role=client
    start_on_login: bool = False
    hotkeys: HotkeyConfig = field(default_factory=HotkeyConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    device_order: list[str] = field(default_factory=list)


def _find_config_file() -> Path | None:
    """Search for config file in standard locations."""
    candidates = [
        Path("sharedinput.toml"),
        Path.home() / ".config" / "sharedinput" / "config.toml",
        Path.home() / ".sharedinput.toml",
    ]

    # Also check next to the executable
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        candidates.insert(0, exe_dir / "sharedinput.toml")

    for path in candidates:
        if path.exists():
            return path
    return None


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from a TOML file.

    Falls back to defaults if no config file is found.
    """
    config = Config()

    if path is None:
        path = _find_config_file()

    if path is None:
        logger.info("No config file found — using defaults")
        return config

    path = Path(path)
    logger.info("Loading config from %s", path)

    with open(path, "rb") as f:
        data = tomllib.load(f)

    # General
    general = data.get("general", {})
    config.role = general.get("role", config.role)
    config.server_host = general.get("server_host", config.server_host)
    config.start_on_login = general.get("start_on_login", config.start_on_login)

    # Hotkeys
    hotkeys = data.get("hotkeys", {})
    config.hotkeys.next_device = hotkeys.get("next_device", config.hotkeys.next_device)
    config.hotkeys.prev_device = hotkeys.get("prev_device", config.hotkeys.prev_device)
    config.hotkeys.back_to_local = hotkeys.get("back_to_local", config.hotkeys.back_to_local)

    # Network
    network = data.get("network", {})
    config.network.udp_port = network.get("udp_port", config.network.udp_port)
    config.network.tcp_port = network.get("tcp_port", config.network.tcp_port)
    config.network.discovery_port = network.get("discovery_port", config.network.discovery_port)
    config.network.encryption = network.get("encryption", config.network.encryption)

    # Devices
    devices = data.get("devices", {})
    config.device_order = devices.get("order", config.device_order)

    return config

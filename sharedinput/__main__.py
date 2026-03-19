"""CLI entry point — run as `python -m sharedinput [server|client]`.

When launched with no arguments (e.g. from the .app bundle), opens the
system tray UI.  With a role argument, runs headless in the terminal.
"""

from __future__ import annotations

import argparse
import logging
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="sharedinput",
        description="SharedInput — share mouse and keyboard across devices on your local network",
    )
    parser.add_argument(
        "role",
        nargs="?",
        choices=["server", "client"],
        default=None,
        help="Run as 'server' (device with peripherals) or 'client' (remote device). "
             "Omit to launch the system tray GUI.",
    )
    parser.add_argument(
        "--host",
        default="",
        help="Server IP address to connect to (client mode only)",
    )
    parser.add_argument(
        "--udp-port",
        type=int,
        default=9876,
        help="UDP port for input events (default: 9876)",
    )
    parser.add_argument(
        "--tcp-port",
        type=int,
        default=9877,
        help="TCP port for control plane (default: 9877)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config file (TOML)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose/debug logging",
    )

    args = parser.parse_args()

    # No role argument → launch system tray GUI
    if args.role is None:
        from sharedinput.config import load_config
        from sharedinput.tray import run_tray

        config = load_config(args.config)
        if args.host:
            config.server_host = args.host
        run_tray(config)
        return

    # CLI mode — set up logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load config
    from sharedinput.config import load_config

    config = load_config(args.config)
    config.role = args.role

    # Override config with CLI args
    if args.host:
        config.server_host = args.host
    if args.udp_port != 9876:
        config.network.udp_port = args.udp_port
    if args.tcp_port != 9877:
        config.network.tcp_port = args.tcp_port

    # Validate
    if args.role == "client" and not config.server_host:
        print("Error: --host is required in client mode (server IP address)")
        print("Usage: sharedinput client --host 192.168.1.10")
        sys.exit(1)

    # Run
    if args.role == "server":
        from sharedinput.server.main import run_server
        run_server(config)
    else:
        from sharedinput.client.main import run_client
        run_client(config)


if __name__ == "__main__":
    main()

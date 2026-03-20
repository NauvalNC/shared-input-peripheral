"""Server orchestration — wires capture, network, and switcher together."""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
import sys
import threading

from sharedinput.config import Config
from sharedinput.server.capture import InputCapture, install_macos_tap, stop_macos_tap, use_macos_backend
from sharedinput.server.network import ControlServer, UDPSender
from sharedinput.server.switcher import HotkeySwitcher

logger = logging.getLogger(__name__)


class Server:
    """Main server — captures input and forwards to active client."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._udp_sender = UDPSender(port=config.network.udp_port)
        self._control_server = ControlServer(port=config.network.tcp_port)
        self._switcher = HotkeySwitcher(on_switch=self._on_switch)
        self._capture: InputCapture | None = None
        self._forwarding = False
        self._shutdown_event: asyncio.Event | None = None  # created in run()
        self._shutdown_flag = threading.Event()  # thread-safe for shutdown()
        self._macos_tap_installed = False

    def _on_event(self, event) -> None:
        """Callback from input capture — forward event and detect hotkeys."""
        try:
            from sharedinput.protocol import KeyPressEvent, KeyReleaseEvent

            # Feed key events to the switcher for hotkey detection (macOS event-fed mode)
            if isinstance(event, KeyPressEvent):
                self._switcher.feed_key_press(event.keycode)
            elif isinstance(event, KeyReleaseEvent):
                self._switcher.feed_key_release(event.keycode)

            if self._forwarding:
                self._udp_sender.send(event)
        except Exception:
            logger.warning("Error processing input event", exc_info=True)

    def _on_switch(self, client_id: str | None) -> None:
        """Callback from switcher — update forwarding target."""
        if client_id is None:
            self._udp_sender.clear_target()
            self._forwarding = False
            logger.info("Now controlling: LOCAL")
        else:
            clients = self._control_server.clients
            if client_id in clients:
                client = clients[client_id]
                self._udp_sender.set_target(*client.address)
                self._forwarding = True
                logger.info("Now controlling: %s (%s)", client.hostname, client.address[0])

    def install_capture_on_main_thread(self) -> None:
        """Install the macOS CGEventTap on the main thread.

        Call this from the main thread BEFORE the run loop starts
        (e.g. in pystray's setup callback).  On non-macOS platforms
        this is a no-op — capture is started in ``run()`` instead.
        """
        if use_macos_backend():
            ok = install_macos_tap(self._on_event)
            self._macos_tap_installed = ok
            if ok:
                logger.info("macOS capture installed on main thread")

    async def _monitor_clients(self) -> None:
        """Periodically update the switcher with the current client list."""
        while not self._shutdown_flag.is_set():
            self._switcher.update_clients(self._control_server.clients)
            await asyncio.sleep(1.0)

    async def run(self) -> None:
        """Run the server."""
        # Platform checks (skip if tray mode already handled it)
        if not self._macos_tap_installed:
            if sys.platform == "darwin":
                from sharedinput.platform.macos import ensure_accessibility
                ensure_accessibility()
            elif sys.platform == "win32":
                from sharedinput.platform.windows import warn_if_not_admin
                warn_if_not_admin()

        # Start control server
        await self._control_server.start()

        # Start hotkey switcher
        self._switcher.start()

        # Start input capture (non-macOS, or macOS CLI mode)
        if not self._macos_tap_installed:
            if use_macos_backend():
                # CLI mode on macOS — install tap here (we ARE the main thread)
                install_macos_tap(self._on_event)
                self._macos_tap_installed = True
            else:
                self._capture = InputCapture(event_callback=self._on_event)
                self._capture.start()

        local_ip = _get_local_ip()
        logger.info("Server started — IP: %s, TCP: %d, UDP: %d",
                     local_ip, self._config.network.tcp_port, self._config.network.udp_port)
        logger.info("Waiting for clients to connect...")
        logger.info("Hotkey: Ctrl+Alt+Arrow to switch devices")

        # Run until shutdown
        try:
            await self._monitor_clients()
        except asyncio.CancelledError:
            pass
        finally:
            self._cleanup()

    def _cleanup(self) -> None:
        if self._capture:
            self._capture.stop()
        if self._macos_tap_installed:
            stop_macos_tap()
        self._switcher.stop()
        self._udp_sender.close()

    def shutdown(self) -> None:
        self._shutdown_flag.set()


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


def run_server(config: Config) -> None:
    """Entry point to run the server (CLI mode).

    On macOS, the main thread must run a CFRunLoop for CGEventTap.
    We run asyncio in a background thread and use CFRunLoopRun on main.
    """
    server = Server(config)

    if use_macos_backend():
        _run_server_macos(server)
    else:
        _run_server_default(server)


def _run_server_default(server: Server) -> None:
    """Run server with asyncio on the main thread (Windows/Linux)."""
    loop = asyncio.new_event_loop()

    def signal_handler():
        server.shutdown()

    if sys.platform != "win32":
        loop.add_signal_handler(signal.SIGINT, signal_handler)
        loop.add_signal_handler(signal.SIGTERM, signal_handler)

    try:
        loop.run_until_complete(server.run())
    except KeyboardInterrupt:
        server.shutdown()
    finally:
        loop.close()


def _run_server_macos(server: Server) -> None:
    """Run server on macOS: CGEventTap on main thread, asyncio in background."""
    import Quartz

    # Install the CGEventTap on the main thread
    server.install_capture_on_main_thread()

    # Run asyncio in a background thread
    loop = asyncio.new_event_loop()

    def bg_run():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.run())
        except Exception:
            logger.exception("Server error in background thread")
        finally:
            loop.close()
            # Stop the main CFRunLoop when server shuts down
            Quartz.CFRunLoopStop(Quartz.CFRunLoopGetMain())

    bg_thread = threading.Thread(target=bg_run, daemon=True)
    bg_thread.start()

    # Run the main CFRunLoop (needed for CGEventTap callbacks)
    try:
        Quartz.CFRunLoopRun()
    except KeyboardInterrupt:
        server.shutdown()

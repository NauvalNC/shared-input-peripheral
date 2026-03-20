"""Server orchestration — wires capture, network, and switcher together.

The server discovers devices on the LAN, connects to them as clients,
captures input from peripherals, and forwards to the active client.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
import sys
import threading

from sharedinput.config import Config
from sharedinput.discovery import DeviceBroadcaster, DeviceInfo, DeviceListener
from sharedinput.platform import get_screen_resolution
from sharedinput.protocol import MouseMoveEvent
from sharedinput.server.capture import InputCapture, install_macos_tap, stop_macos_tap, use_macos_backend
from sharedinput.server.network import ClientConnector, UDPSender
from sharedinput.server.switcher import HotkeySwitcher

logger = logging.getLogger(__name__)


class Server:
    """Main server — captures input and forwards to active client."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._udp_sender = UDPSender(port=config.network.udp_port)
        self._connector = ClientConnector()
        self._switcher = HotkeySwitcher(on_switch=self._on_switch)
        self._broadcaster: DeviceBroadcaster | None = None  # set by tray
        self._listener: DeviceListener | None = None
        self._capture: InputCapture | None = None
        self._forwarding = False
        self._shutdown_flag = threading.Event()
        self._macos_tap_installed = False
        self._server_screen_w, self._server_screen_h = get_screen_resolution()
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def _control_server(self):
        """Backwards compat — tray accesses this for client list."""
        return self._connector

    def _on_event(self, event) -> None:
        """Callback from input capture — forward event and detect hotkeys."""
        try:
            from sharedinput.protocol import KeyPressEvent, KeyReleaseEvent

            if isinstance(event, KeyPressEvent):
                self._switcher.feed_key_press(event.keycode)
            elif isinstance(event, KeyReleaseEvent):
                self._switcher.feed_key_release(event.keycode)

            if self._forwarding:
                if isinstance(event, MouseMoveEvent):
                    event = self._scale_mouse_event(event)
                self._udp_sender.send(event)
        except Exception:
            logger.warning("Error processing input event", exc_info=True)

    def _scale_mouse_event(self, event: MouseMoveEvent) -> MouseMoveEvent:
        active_id = self._switcher.active_client_id
        if not active_id:
            return event
        client = self._connector.clients.get(active_id)
        if not client:
            return event
        if self._server_screen_w and client.screen_width:
            scale_x = client.screen_width / self._server_screen_w
            scale_y = client.screen_height / self._server_screen_h
            dx = int(event.dx * scale_x)
            dy = int(event.dy * scale_y)
            return MouseMoveEvent(
                dx=max(-32768, min(32767, dx)),
                dy=max(-32768, min(32767, dy)),
                timestamp=event.timestamp,
            )
        return event

    def _on_switch(self, client_id: str | None) -> None:
        if client_id is None:
            self._udp_sender.clear_target()
            self._forwarding = False
            logger.info("Now controlling: LOCAL")
        else:
            clients = self._connector.clients
            if client_id in clients:
                client = clients[client_id]
                self._udp_sender.set_target(*client.address)
                self._forwarding = True
                logger.info("Now controlling: %s (%s)", client.hostname, client.address[0])

    def _on_device_found(self, device: DeviceInfo) -> None:
        """Called when a new device is discovered — auto-connect to it."""
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._connector.connect_to_device(device.device_id, device.ip, device.tcp_port),
                self._loop,
            )

    def install_capture_on_main_thread(self) -> None:
        if use_macos_backend():
            ok = install_macos_tap(self._on_event)
            self._macos_tap_installed = ok
            if ok:
                logger.info("macOS capture installed on main thread")

    def set_broadcaster(self, broadcaster: DeviceBroadcaster) -> None:
        """Set the broadcaster so we can use its device_id to filter."""
        self._broadcaster = broadcaster

    async def _monitor_clients(self) -> None:
        while not self._shutdown_flag.is_set():
            self._switcher.update_clients(self._connector.clients)
            await asyncio.sleep(1.0)

    async def run(self) -> None:
        """Run the server."""
        self._loop = asyncio.get_event_loop()

        # Platform checks (skip if tray mode already handled it)
        if not self._macos_tap_installed:
            if sys.platform == "darwin":
                from sharedinput.platform.macos import ensure_accessibility
                ensure_accessibility()
            elif sys.platform == "win32":
                from sharedinput.platform.windows import warn_if_not_admin
                warn_if_not_admin()

        # Start device listener to discover clients
        ignore_id = self._broadcaster.device_id if self._broadcaster else ""
        self._listener = DeviceListener(
            discovery_port=self._config.network.discovery_port,
            on_device_found=self._on_device_found,
            ignore_device_id=ignore_id,
        )
        self._listener.start()

        # Start hotkey switcher
        self._switcher.start()

        # Start input capture (non-macOS, or macOS CLI mode)
        if not self._macos_tap_installed:
            if use_macos_backend():
                install_macos_tap(self._on_event)
                self._macos_tap_installed = True
            else:
                self._capture = InputCapture(event_callback=self._on_event)
                self._capture.start()

        local_ip = _get_local_ip()
        logger.info("Server started — IP: %s, UDP: %d",
                     local_ip, self._config.network.udp_port)
        logger.info("Listening for devices on LAN...")
        logger.info("Hotkey: Ctrl+Alt+Arrow to switch devices")

        try:
            await self._monitor_clients()
        except asyncio.CancelledError:
            pass
        finally:
            self._cleanup()

    def switch_to_client(self, client_id: str | None) -> None:
        self._switcher.switch_to(client_id)

    def _cleanup(self) -> None:
        if self._listener:
            self._listener.stop()
        if self._capture:
            self._capture.stop()
        if self._macos_tap_installed:
            stop_macos_tap()
        self._switcher.stop()
        self._udp_sender.close()

    def shutdown(self) -> None:
        self._shutdown_flag.set()


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def run_server(config: Config) -> None:
    """Entry point to run the server (CLI mode)."""
    server = Server(config)

    if use_macos_backend():
        _run_server_macos(server)
    else:
        _run_server_default(server)


def _run_server_default(server: Server) -> None:
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
    import Quartz

    server.install_capture_on_main_thread()

    loop = asyncio.new_event_loop()

    def bg_run():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.run())
        except Exception:
            logger.exception("Server error in background thread")
        finally:
            loop.close()
            Quartz.CFRunLoopStop(Quartz.CFRunLoopGetMain())

    bg_thread = threading.Thread(target=bg_run, daemon=True)
    bg_thread.start()

    try:
        Quartz.CFRunLoopRun()
    except KeyboardInterrupt:
        server.shutdown()

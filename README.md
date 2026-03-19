# SharedInput

A cross-platform software KVM switch that lets you share one mouse and keyboard across multiple devices on your local network. No USB switching needed — just press a hotkey to control a different machine.

## The Problem

You have multiple devices (PC, Mac Mini, Laptop) on the same desk, each with its own monitor, but only one set of peripherals. Every time you want to control a different machine, you have to unplug and replug USB cables.

## The Solution

SharedInput runs on each device. The machine your peripherals are plugged into acts as the **server**, and your other machines run as **clients**. Press `Ctrl+Alt+Arrow` to instantly switch which device your mouse and keyboard control — no cable swapping required.

## Features

- **Hotkey switching** — `Ctrl+Alt+Right/Left` to cycle through devices
- **Cross-platform** — works on Windows 11 and macOS (Tahoe)
- **Low latency** — input events sent over UDP for minimal delay
- **System tray app** — runs in the background with a tray/menu bar icon
- **Auto-discovery** — devices find each other on the LAN via mDNS (planned)
- **Encrypted** — input events encrypted with AES-256-GCM over the network (planned)

## Requirements

- Python 3.12+
- All devices connected to the same local network

## Installation

```bash
# Clone the repo
git clone https://github.com/NauvalNC/shared-input-peripheral.git
cd shared-input-peripheral

# Install dependencies
pip install -e .

# For development (includes pytest and PyInstaller)
pip install -e ".[dev]"
```

### macOS Additional Setup

On macOS, SharedInput requires **Accessibility permission** to capture and inject input events. When you first run the app, it will prompt you to grant permission:

1. Open **System Settings** > **Privacy & Security** > **Accessibility**
2. Enable SharedInput (or your terminal app if running from the command line)

## Usage

### GUI Mode (System Tray)

Double-click the app or run without arguments:

```bash
python -m sharedinput
```

A tray icon appears in your system tray (Windows) or menu bar (macOS). Right-click to:
- **Start as Server** — on the device with your peripherals plugged in
- **Start as Client** — on remote devices you want to control
- **See connected devices** and their status
- **Quit** the app

### CLI Mode (Headless)

For running without a GUI, or on headless machines:

```bash
# On the device with mouse and keyboard plugged in:
python -m sharedinput server

# On each remote device (replace with your server's IP):
python -m sharedinput client --host 192.168.1.10
```

### Switching Devices

Once the server is running and at least one client is connected:

| Hotkey | Action |
|---|---|
| `Ctrl+Alt+Right` | Switch to the next device |
| `Ctrl+Alt+Left` | Switch to the previous device |

When switched to a client, all mouse and keyboard input is forwarded to that device. Press the hotkey again to cycle back to local control.

### CLI Options

```
python -m sharedinput [server|client] [options]

positional arguments:
  {server,client}      Run as server or client. Omit to launch tray GUI.

options:
  --host HOST          Server IP to connect to (client mode only)
  --udp-port PORT      UDP port for input events (default: 9876)
  --tcp-port PORT      TCP port for control plane (default: 9877)
  --config PATH        Path to TOML config file
  -v, --verbose        Enable debug logging
```

## Configuration

Settings can be customized via a TOML config file. The app looks for config files in this order:

1. `./sharedinput.toml` (current directory)
2. `~/.config/sharedinput/config.toml`
3. `~/.sharedinput.toml`

See [`config/default.toml`](config/default.toml) for all available options:

```toml
[general]
role = "server"
# server_host = "192.168.1.10"  # for client mode
start_on_login = false

[hotkeys]
next_device = "ctrl+alt+right"
prev_device = "ctrl+alt+left"
back_to_local = "ctrl+alt+home"

[network]
udp_port = 9876
tcp_port = 9877
encryption = true

[devices]
# order = ["Mac-Mini", "Laptop"]
```

## Packaging

Build a standalone distributable for the current platform:

```bash
# Build
python scripts/build.py

# Build with clean (removes previous build artifacts)
python scripts/build.py --clean
```

| Platform | Output | Notes |
|---|---|---|
| macOS | `dist/SharedInput.app` | Double-click to run, or drag to Applications |
| Windows | `dist/SharedInput.exe` | Single portable executable |

> **Note:** PyInstaller cannot cross-compile. You must build on each target platform.

## Architecture

```
SERVER (peripherals plugged in)              CLIENT(s) (controlled remotely)
┌──────────────┐                             ┌──────────────┐
│ Input Capture │──serialize──► UDP ────────► │ Input Inject  │
│ (pynput)      │              port 9876     │ (pynput)      │
└──────────────┘                             └──────────────┘
┌──────────────┐                             ┌──────────────┐
│ Control Plane │◄──────── TCP ─────────────►│ Control Plane │
│ (registration,│              port 9877     │ (heartbeat,   │
│  switching)   │                            │  commands)    │
└──────────────┘                             └──────────────┘
```

- **Data plane (UDP):** Mouse and keyboard events serialized in a compact binary format (~14-18 bytes per event) for minimal latency.
- **Control plane (TCP):** Device registration, heartbeats, and switch notifications using JSON messages.

## Project Structure

```
sharedinput/
├── __main__.py          # CLI entry point; no-arg launches tray GUI
├── protocol.py          # Binary event serialization
├── config.py            # TOML config loading
├── icons.py             # Programmatic tray icon generation
├── tray.py              # System tray UI
├── server/
│   ├── capture.py       # Mouse/keyboard input capture
│   ├── network.py       # UDP sender + TCP control server
│   ├── switcher.py      # Hotkey detection and device switching
│   └── main.py          # Server orchestration
├── client/
│   ├── injector.py      # Mouse/keyboard input injection
│   ├── network.py       # UDP receiver + TCP control client
│   └── main.py          # Client orchestration
└── platform/
    ├── macos.py          # macOS Accessibility permission checks
    └── windows.py        # Windows admin/UAC checks
```

## Running Tests

```bash
python -m pytest tests/ -v
```

## License

See [LICENSE](LICENSE) for details.

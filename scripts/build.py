"""Build script — packages SharedInput with PyInstaller for macOS and Windows.

Usage:
    python scripts/build.py          # Build for the current platform
    python scripts/build.py --clean  # Clean build artifacts first
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ASSETS = ROOT / "assets"
ENTRY = ROOT / "sharedinput" / "__main__.py"


def generate_icons() -> None:
    """Ensure icon assets exist."""
    if not (ASSETS / "icon.png").exists():
        print("Generating icon assets...")
        subprocess.check_call([sys.executable, "-m", "sharedinput.icons"], cwd=ROOT)


def clean() -> None:
    """Remove previous build artifacts."""
    for d in [DIST, BUILD]:
        if d.exists():
            shutil.rmtree(d)
            print(f"Removed {d}")


def build_macos() -> None:
    """Build macOS .app bundle."""
    generate_icons()

    icon_path = ASSETS / "icon_512.png"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "SharedInput",
        "--windowed",              # .app bundle, no terminal window
        "--onedir",                # directory bundle (more reliable on macOS)
        "--icon", str(icon_path),
        "--add-data", f"{ASSETS}:assets",
        "--add-data", f"{ROOT / 'config' / 'default.toml'}:config",
        "--hidden-import", "pynput.keyboard._darwin",
        "--hidden-import", "pynput.mouse._darwin",
        "--hidden-import", "pystray._darwin",
        "--noconfirm",
        "--clean",
        str(ENTRY),
    ]

    print("Building macOS .app bundle...")
    print(f"  Command: {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=ROOT)

    app_path = DIST / "SharedInput.app"
    if app_path.exists():
        print(f"\nBuild successful: {app_path}")
        print(f"  To run: open {app_path}")
    else:
        # onedir mode puts it in a folder
        app_path = DIST / "SharedInput" / "SharedInput.app"
        if app_path.exists():
            print(f"\nBuild successful: {app_path}")
        else:
            print(f"\nBuild output in: {DIST}")


def build_windows() -> None:
    """Build Windows .exe."""
    generate_icons()

    icon_path = ASSETS / "icon.ico"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "SharedInput",
        "--onefile",               # Single .exe
        "--icon", str(icon_path),
        "--add-data", f"{ASSETS};assets",
        "--add-data", f"{ROOT / 'config' / 'default.toml'};config",
        "--hidden-import", "pynput.keyboard._win32",
        "--hidden-import", "pynput.mouse._win32",
        "--hidden-import", "pystray._win32",
        "--uac-admin",             # Request admin (for SendInput)
        "--noconfirm",
        "--clean",
        str(ENTRY),
    ]

    print("Building Windows .exe...")
    print(f"  Command: {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=ROOT)

    exe_path = DIST / "SharedInput.exe"
    if exe_path.exists():
        print(f"\nBuild successful: {exe_path}")
    else:
        print(f"\nBuild output in: {DIST}")


def main() -> None:
    if "--clean" in sys.argv:
        clean()

    system = platform.system()
    if system == "Darwin":
        build_macos()
    elif system == "Windows":
        build_windows()
    else:
        print(f"Unsupported platform: {system}")
        sys.exit(1)


if __name__ == "__main__":
    main()

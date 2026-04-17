#!/usr/bin/env python3
"""
easyplay_watcher.py — BLE watcher that launches EasyPlay on remote button press.

Runs as a lightweight background service. Connects to the EasyPlay BLE remote,
waits for the On/Off button ('O'), then:
  1. Disconnects from the remote (frees the BLE link)
  2. Launches EasyPlay
  3. Waits for EasyPlay to exit
  4. Reconnects to the remote, back to listening

Only one BLE client can connect to the remote at a time, so the watcher
hands off the connection to EasyPlay by disconnecting first.

Usage:
    python3 tools/easyplay_watcher.py                    # uses config
    python3 tools/easyplay_watcher.py AC:EB:E6:4B:63:CE  # explicit MAC

Install as systemd service via install.sh (enabled by default).
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = REPO_DIR / "easyplay_config.json"
TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"

# Find the latest easyplay version
def find_easyplay_script():
    candidates = sorted(REPO_DIR.glob("easyplay[0-9]*.py"), reverse=True)
    return str(candidates[0]) if candidates else str(REPO_DIR / "easyplay59.py")

EASYPLAY_SCRIPT = find_easyplay_script()


def ts():
    return datetime.now().strftime("[%H:%M:%S]")


def log(msg):
    print(f"{ts()} [watcher] {msg}", flush=True)


def get_remote_mac():
    """Get MAC from argv or config file."""
    if len(sys.argv) > 1:
        return sys.argv[1]
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
            return cfg.get("bluetooth_remote_addr", "")
        except Exception:
            pass
    return ""


def is_easyplay_running():
    """Check if an easyplay process is already running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "easyplay[0-9].*\\.py"],
            capture_output=True, timeout=3
        )
        return result.returncode == 0
    except Exception:
        return False


def launch_easyplay():
    """Launch EasyPlay and wait for it to exit."""
    script = find_easyplay_script()
    log(f"Launching {Path(script).name}...")
    env = dict(os.environ,
               DISPLAY=":0",
               WAYLAND_DISPLAY="wayland-0",
               XDG_RUNTIME_DIR="/run/user/1000")
    try:
        proc = subprocess.Popen(
            [sys.executable, script],
            cwd=str(REPO_DIR),
            env=env,
            stdin=subprocess.DEVNULL,
        )
        log(f"EasyPlay started (PID {proc.pid})")
        proc.wait()
        log(f"EasyPlay exited (code {proc.returncode})")
    except Exception as e:
        log(f"Failed to launch EasyPlay: {e}")


async def main():
    from bleak import BleakClient, BleakScanner
    from bleak.exc import BleakError

    mac = get_remote_mac()
    if not mac:
        sys.exit("No remote MAC. Set bluetooth_remote_addr in easyplay_config.json or pass as argument.")

    log(f"EasyPlay Watcher starting")
    log(f"Remote MAC: {mac}")
    log(f"EasyPlay script: {Path(EASYPLAY_SCRIPT).name}")
    log(f"Waiting for On/Off button press to launch...")

    while True:
        # Skip if EasyPlay is already running
        if is_easyplay_running():
            log("EasyPlay is already running, waiting for it to exit...")
            while is_easyplay_running():
                await asyncio.sleep(2)
            log("EasyPlay exited, resuming watcher")
            await asyncio.sleep(2)  # let BLE settle
            continue

        # Scan for remote
        log("Scanning for remote...")
        try:
            device = await BleakScanner.find_device_by_address(mac, timeout=15)
        except BleakError as e:
            log(f"Scan error: {e}")
            await asyncio.sleep(3)
            continue

        if not device:
            await asyncio.sleep(5)
            continue

        log(f"Found remote (RSSI visible), connecting...")

        # Connect and listen for On/Off
        try:
            launch_requested = asyncio.Event()
            disconnect_event = asyncio.Event()

            def on_disconnect(client):
                disconnect_event.set()

            def on_notify(sender, data):
                if len(data) == 1:
                    char = chr(data[0])
                    if char == 'O':  # On/Off press
                        log("On/Off button pressed!")
                        launch_requested.set()

            async with BleakClient(device, disconnected_callback=on_disconnect, timeout=10) as client:
                log("Connected to remote, listening for On/Off...")
                await client.start_notify(TX_UUID, on_notify)

                # Wait for either On/Off press or disconnect
                done = asyncio.gather(
                    launch_requested.wait(),
                    disconnect_event.wait(),
                )
                finished, _ = await asyncio.wait(
                    [asyncio.create_task(launch_requested.wait()),
                     asyncio.create_task(disconnect_event.wait())],
                    return_when=asyncio.FIRST_COMPLETED,
                )

            # Connection is now closed (exited async with)

            if launch_requested.is_set():
                log("Disconnected from remote, handing off BLE link...")
                await asyncio.sleep(1)  # let BlueZ clean up
                launch_easyplay()
                log("EasyPlay finished, reconnecting to remote...")
                await asyncio.sleep(2)  # settle before reconnect
            else:
                log("Remote disconnected, will reconnect...")
                await asyncio.sleep(3)

        except BleakError as e:
            log(f"BLE error: {e}")
            await asyncio.sleep(3)
        except Exception as e:
            log(f"Error: {e}")
            await asyncio.sleep(3)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Ctrl+C, exiting")
        sys.exit(0)

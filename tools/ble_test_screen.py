#!/usr/bin/env python3
"""
ble_test_screen.py — Visual BLE test tool for the Pi's screen.

Shows a fullscreen pygame window with live status of each BLE phase:
  Phase 1: Scanning for remote
  Phase 2: Connecting
  Phase 3: Subscribing to notifications
  Phase 4: Listening — shows button events as they arrive

Each phase goes green on success, red on failure.
Button presses flash large on screen with the button name.

Usage:
    python3 tools/ble_test_screen.py                  # uses MAC from easyplay_config.json
    python3 tools/ble_test_screen.py AC:EB:E6:4B:63:CE  # explicit MAC
"""

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("SDL_NOMOUSE", "1")

import pygame

# ── Config ───────────────────────────────────────────────────────────────────

REMOTE_MAC = None  # set from argv or config
TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"
CONFIG_FILE = Path(__file__).resolve().parent.parent / "easyplay_config.json"

BUTTON_NAMES = {
    'L': 'LEFT', 'l': 'LEFT',
    'R': 'RIGHT', 'r': 'RIGHT',
    'U': 'UP', 'u': 'UP',
    'D': 'DOWN', 'd': 'DOWN',
    'O': 'ON/OFF', 'o': 'ON/OFF',
}

# Colors
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GRAY = (80, 80, 80)
GREEN = (0, 200, 0)
RED = (200, 0, 0)
YELLOW = (200, 200, 0)
BLUE = (40, 120, 255)
DARK_GREEN = (0, 80, 0)

# ── Shared state (BLE thread → pygame main thread) ──────────────────────────

class State:
    def __init__(self):
        self.phase = 0          # 0=init, 1=scanning, 2=connecting, 3=subscribing, 4=listening
        self.phase_status = {}  # phase_num -> "ok" | "fail" | "active"
        self.status_text = "Starting..."
        self.last_button = ""
        self.last_button_time = 0
        self.button_count = 0
        self.events = []        # list of (time_str, char, name)
        self.error = ""
        self.connected = False
        self.rssi = None
        self.running = True

state = State()


# ── BLE thread ───────────────────────────────────────────────────────────────

def ble_thread_func(mac):
    from bleak import BleakClient, BleakScanner

    async def run():
        # Phase 1: Scan
        state.phase = 1
        state.phase_status[1] = "active"
        state.status_text = f"Scanning for {mac}..."

        device = None
        for attempt in range(3):
            try:
                found_event = asyncio.Event()
                found_device = None
                found_rssi = None

                def on_detected(dev, adv_data):
                    nonlocal found_device, found_rssi
                    if dev.address.upper() == mac.upper():
                        found_device = dev
                        found_rssi = adv_data.rssi
                        found_event.set()

                async with BleakScanner(detection_callback=on_detected):
                    try:
                        await asyncio.wait_for(found_event.wait(), timeout=10.0)
                    except asyncio.TimeoutError:
                        pass

                if found_device:
                    device = found_device
                    state.rssi = found_rssi
                    break
                state.status_text = f"Not found (attempt {attempt+1}/3)..."
            except Exception as e:
                state.status_text = f"Scan error: {e}"
                await asyncio.sleep(1)

        if not device:
            state.phase_status[1] = "fail"
            state.error = "Remote not found after 3 scan attempts"
            state.status_text = state.error
            return

        state.phase_status[1] = "ok"
        state.status_text = f"Found! RSSI={state.rssi}dBm"
        await asyncio.sleep(0.5)

        # Phase 2: Connect
        state.phase = 2
        state.phase_status[2] = "active"
        state.status_text = "Connecting..."

        try:
            disconnect_event = asyncio.Event()

            def on_disconnect(client):
                state.connected = False
                state.status_text = "Disconnected"
                disconnect_event.set()

            async with BleakClient(device, disconnected_callback=on_disconnect, timeout=10.0) as client:
                state.connected = True
                state.phase_status[2] = "ok"
                state.status_text = f"Connected (MTU={client.mtu_size})"
                await asyncio.sleep(0.3)

                # Phase 3: Subscribe
                state.phase = 3
                state.phase_status[3] = "active"
                state.status_text = "Subscribing to notifications..."

                def on_notify(sender, data):
                    if len(data) == 1:
                        char = chr(data[0])
                        name = BUTTON_NAMES.get(char, f"?({char})")
                        is_press = char.isupper()
                        t = time.strftime("%H:%M:%S")
                        state.events.append((t, char, name))
                        if is_press:
                            state.last_button = name
                            state.last_button_time = time.time()
                            state.button_count += 1
                        state.status_text = f"Button: {name} {'PRESS' if is_press else 'release'}"

                await client.start_notify(TX_UUID, on_notify)
                state.phase_status[3] = "ok"
                state.phase = 4
                state.phase_status[4] = "active"
                state.status_text = "Listening for buttons..."

                # Stay connected until disconnect or quit
                while state.running and not disconnect_event.is_set():
                    await asyncio.sleep(0.1)

                if disconnect_event.is_set() and state.running:
                    state.phase_status[4] = "fail"
                    state.error = "Connection lost"

        except Exception as e:
            state.phase_status[2] = "fail"
            state.error = f"Connect failed: {e}"
            state.status_text = state.error

    asyncio.run(run())


# ── Pygame rendering ─────────────────────────────────────────────────────────

def main():
    global REMOTE_MAC

    # Get MAC from argv or config
    if len(sys.argv) > 1:
        REMOTE_MAC = sys.argv[1]
    elif CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
            REMOTE_MAC = cfg.get("bluetooth_remote_addr", "")
        except Exception:
            pass

    if not REMOTE_MAC:
        print("No remote MAC. Pass as argument or set in easyplay_config.json")
        sys.exit(1)

    print(f"BLE Test Screen — remote MAC: {REMOTE_MAC}")

    # Start pygame
    pygame.init()
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    W, H = screen.get_size()
    pygame.display.set_caption("BLE Test")
    pygame.mouse.set_visible(False)
    clock = pygame.time.Clock()

    font_big = pygame.font.SysFont("monospace", H // 6, bold=True)
    font_mid = pygame.font.SysFont("monospace", H // 16, bold=True)
    font_small = pygame.font.SysFont("monospace", H // 24)

    # Start BLE thread
    t = threading.Thread(target=ble_thread_func, args=(REMOTE_MAC,), daemon=True)
    t.start()

    phase_labels = {
        1: "Scan",
        2: "Connect",
        3: "Subscribe",
        4: "Listen",
    }

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False

        screen.fill(BLACK)

        # Title
        title = font_mid.render("BLE Remote Test", True, WHITE)
        screen.blit(title, (W // 2 - title.get_width() // 2, 20))

        mac_text = font_small.render(f"MAC: {REMOTE_MAC}", True, GRAY)
        screen.blit(mac_text, (W // 2 - mac_text.get_width() // 2, 20 + title.get_height() + 5))

        # Phase indicators
        y = 20 + title.get_height() + mac_text.get_height() + 30
        for phase_num in [1, 2, 3, 4]:
            status = state.phase_status.get(phase_num, "")
            if status == "ok":
                color = GREEN
                indicator = "[OK]"
            elif status == "fail":
                color = RED
                indicator = "[FAIL]"
            elif status == "active":
                color = YELLOW
                dots = "." * (1 + int(time.time() * 2) % 3)
                indicator = f"[{dots}]"
            else:
                color = GRAY
                indicator = "[  ]"

            label = phase_labels.get(phase_num, "?")
            line = font_mid.render(f"  {indicator}  Phase {phase_num}: {label}", True, color)
            screen.blit(line, (40, y))
            y += line.get_height() + 8

        # Status text
        y += 10
        status_surf = font_small.render(state.status_text, True, BLUE)
        screen.blit(status_surf, (60, y))
        y += status_surf.get_height() + 5

        if state.error:
            err_surf = font_small.render(state.error, True, RED)
            screen.blit(err_surf, (60, y))
            y += err_surf.get_height() + 5

        # Big button flash
        if state.last_button and (time.time() - state.last_button_time) < 1.5:
            alpha = max(0, 1.0 - (time.time() - state.last_button_time) / 1.5)
            btn_color = (int(40 + 215 * alpha), int(200 * alpha), int(40 * alpha))
            btn_surf = font_big.render(state.last_button, True, btn_color)
            screen.blit(btn_surf, (W // 2 - btn_surf.get_width() // 2,
                                   H // 2 - btn_surf.get_height() // 2))

        # Event log (last 8)
        y = H - (font_small.get_height() + 4) * 9 - 10
        log_title = font_small.render(f"Events ({state.button_count} presses):", True, GRAY)
        screen.blit(log_title, (40, y))
        y += log_title.get_height() + 4
        for t_str, char, name in state.events[-8:]:
            is_press = char.isupper()
            color = GREEN if is_press else DARK_GREEN
            line = font_small.render(f"  {t_str}  {char}  {name} {'PRESS' if is_press else 'release'}", True, color)
            screen.blit(line, (40, y))
            y += line.get_height() + 2

        # Footer
        footer = font_small.render("Press ESC or Q to quit", True, GRAY)
        screen.blit(footer, (W // 2 - footer.get_width() // 2, H - footer.get_height() - 10))

        pygame.display.flip()
        clock.tick(30)

    state.running = False
    pygame.quit()


if __name__ == "__main__":
    main()

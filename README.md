# EasyPlay

Accessible media player for Raspberry Pi 5, built for stroke recovery.

- Horizontal carousel with DVD-stack effect for series
- 5-button BLE remote (ESP32-C3 SuperMini + NimBLE)
- HDMI-CEC TV control
- Auto-resume, crash recovery via systemd
- BLE watcher: press On/Off on the remote to launch EasyPlay (no keyboard needed)

## Contents

| Path                              | What it is                                      |
| --------------------------------- | ----------------------------------------------- |
| `easyplay60.py`                   | The Pi-side player (pygame + VLC/mpv + bleak)    |
| `remote_c3_v6/`                   | ESP32-C3 Arduino firmware for the remote         |
| `install.sh`                      | Fresh-Pi setup script (Bookworm/Trixie 64-bit)   |
| `setup-usb-media.sh`              | Optional: point media folder at a USB drive       |
| `tools/fetch_covers.py`           | Interactive TMDB poster fetcher (runs on Mac)     |
| `tools/easyplay_watcher.py`       | BLE watcher daemon (launches EasyPlay on button press) |
| `tools/ble_test_screen.py`        | Visual BLE connection test (fullscreen on Pi)     |
| `systemd/easyplay.service`        | systemd unit for EasyPlay (installed disabled)    |
| `systemd/easyplay-watcher.service`| systemd unit for the BLE watcher (installed enabled) |
| `easyplay.desktop`                | Desktop icon for the Pi                           |
| `icon.svg`                        | App icon (purple play button)                     |
| `docs/ble_protocol_map.md`        | Full BLE protocol reference with debug checklist  |

## Fresh Pi install

Starting from a fresh Raspberry Pi OS Bookworm/Trixie 64-bit image:

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/davideo71/EasyPlay.git ~/Desktop/EasyPlay
cd ~/Desktop/EasyPlay

# Normal install:
./install.sh

# Or, if you're using a USB Bluetooth dongle for better range,
# also disable the internal BT (reboot required):
./install.sh --external-bt
```

This installs all dependencies (pygame, vlc, mpv, bleak, cec-utils, etc.),
sets up passwordless `hciconfig` for BLE recovery, shrinks the swap file,
disables wifi power save, installs the desktop icon, and sets up two
systemd services:

- **easyplay.service** — launches the player (installed disabled; enable
  manually when ready, or use the watcher instead)
- **easyplay-watcher.service** — lightweight BLE daemon that launches
  EasyPlay when you press the remote's On/Off button (installed enabled)

## USB media drive

If your videos live on a USB drive, plug it in and run:

```bash
./setup-usb-media.sh
```

This mounts the drive at `/mnt/media` via `/etc/fstab` (by UUID,
with `nofail` + `x-systemd.automount` so it auto-mounts on access
and doesn't hang boot if the drive is missing). Creates a symlink
from `~/Desktop/codevideos` to `/mnt/media/codevideos`.

If the drive is not plugged in when EasyPlay starts, it shows a
"USB drive not connected" screen and waits until the drive appears.

## BLE remote setup

### Flashing the ESP32-C3 SuperMini

```bash
cd ~/Desktop/EasyPlay
arduino-cli compile --fqbn esp32:esp32:esp32c3:CDCOnBoot=cdc remote_c3_v6/remote_c3_v6.ino
arduino-cli upload  --fqbn esp32:esp32:esp32c3:CDCOnBoot=cdc --port /dev/cu.usbmodem101 remote_c3_v6/remote_c3_v6.ino
```

Note the MAC address from the Serial monitor output — you'll need it for the Pi.

### Configuring the Pi

Edit `~/Desktop/EasyPlay/easyplay_config.json`:

```json
{
  "bluetooth_remote_addr": "AC:EB:E6:4B:63:CE",
  "bluetooth_remote_name": "EasyPlay"
}
```

### How the remote works

The BLE watcher service runs in the background after boot. When you press
the On/Off button on the remote:

1. Watcher detects the button press
2. Watcher disconnects from the remote (only one BLE client can connect at a time)
3. Watcher launches EasyPlay
4. EasyPlay connects to the remote and takes over
5. When EasyPlay exits, the watcher reconnects and waits for the next press

### Testing the BLE connection

Use the visual test tool to verify each phase of the BLE protocol:

```bash
python3 tools/ble_test_screen.py
```

Shows a fullscreen display with live status of scan → connect → subscribe →
button events. See `docs/ble_protocol_map.md` for the full protocol reference.

### Hardware

- **Pi side:** TP-Link UB500 USB Bluetooth 5.0 dongle recommended (disable
  internal BT with `./install.sh --external-bt`). Dedicated antenna avoids
  WiFi/BT radio contention on the Pi 5's shared chip.
- **Remote side:** ESP32-C3 SuperMini. External antenna version recommended
  for better range. Same firmware either way.
- **Buttons:** GPIO 0–4 (active LOW, INPUT_PULLUP, wired to GND):
  - GPIO 0 = Left, GPIO 1 = Down, GPIO 2 = Right, GPIO 3 = Up, GPIO 4 = On/Off
- **NeoPixel LED on GPIO 8:** blue flash = boot, green = connected

## Fetching movie posters

Each media folder needs a `cover.jpg` for the carousel. The helper tool
queries TMDB, shows the top matches, and downloads the poster you pick.
It also organizes loose video files into folders automatically.

```bash
# one-time setup: get a free TMDB read access token at
# https://www.themoviedb.org/settings/api
mkdir -p ~/.config/easyplay
echo 'YOUR_TOKEN_HERE' > ~/.config/easyplay/tmdb_token
pip install requests

# interactive run (folder picker pops up if no path given)
python3 tools/fetch_covers.py

# or with a path + auto mode
python3 tools/fetch_covers.py /Volumes/BIGF/codevideos --auto
```

Options:
- `--auto` — non-interactive, picks the top TMDB match per folder
- `--skip-existing` — skip folders that already have a `cover.jpg`
- `--only 'Jojo*'` — process a single folder matching the glob

Existing covers are backed up to `cover.jpg.bak` on first fetch.

## BLE protocol

- Device name: `EasyPlay`
- Service: Nordic UART Service (NUS) `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`
- TX (remote → Pi, notify): `6E400003-B5A3-F393-E0A9-E50E24DCCA9E`
- RX (Pi → remote, write): `6E400002-B5A3-F393-E0A9-E50E24DCCA9E`
- Button codes: uppercase = press, lowercase = release
  - `L/l` Left, `R/r` Right, `U/u` Up, `D/d` Down, `O/o` On/Off

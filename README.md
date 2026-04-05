# EasyPlay

Accessible media player for Raspberry Pi 5, built for stroke recovery.

- Horizontal carousel with DVD-stack effect for series
- 4-button BLE remote (ESP32-C3 SuperMini + NimBLE)
- HDMI-CEC TV control
- Auto-resume, crash recovery via systemd

## Contents

| Path                     | What it is                                   |
| ------------------------ | -------------------------------------------- |
| `easyplay55.py`          | The Pi-side player (pygame + VLC/mpv + bleak)|
| `remote_c3_v6/`          | ESP32-C3 Arduino firmware for the remote     |
| `install.sh`             | Fresh-Pi setup script (Bookworm/Trixie 64-bit)|
| `setup-usb-media.sh`     | Optional: point media folder at a USB drive  |
| `systemd/easyplay.service` | systemd unit installed by `install.sh`     |
| `tools/fetch_covers.py`  | Interactive TMDB poster fetcher (runs on Mac)|

## Fresh Pi install

Starting from a fresh Raspberry Pi OS Bookworm 64-bit image:

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

If your videos live on a USB drive, plug it in and run:

```bash
./setup-usb-media.sh
```

This mounts the drive at `/mnt/media` via `/etc/fstab` (by UUID,
`nofail` so a missing drive won't hang boot) and points
`~/Desktop/codevideos` at `/mnt/media/codevideos`.

Then log out and back in (the `bluetooth` group needs a fresh session),
drop videos into `~/Desktop/codevideos/`, and test:

```bash
python3 ~/Desktop/EasyPlay/easyplay55.py
```

When it works, enable autostart:

```bash
sudo systemctl enable --now easyplay.service
```

## Fetching movie posters

Each media folder needs a `cover.jpg` (or `poster.jpg` / `folder.jpg`) for
the carousel. The helper tool queries TMDB, shows you the top matches, and
downloads whichever poster you pick.

```bash
# one-time setup: get a free TMDB read access token at
# https://www.themoviedb.org/settings/api
export TMDB_TOKEN='eyJhbGciOiJIUzI1NiJ9...'
pip install requests

# interactive run
python3 tools/fetch_covers.py /Volumes/BIGF/codevideos
```

Existing cover files are backed up to `cover.jpg.bak` on first fetch so
originals are always recoverable. `--skip-existing` skips folders that
already have a `cover.jpg`, `--only 'Jojo*'` processes a single folder.

## BLE protocol

- Device name: `EasyPlay-Remote`
- Uses Nordic UART (NUS) characteristic
- Button codes: `L/l R/r U/u D/d O/o` (upper = press, lower = release)

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

## BLE protocol

- Device name: `EasyPlay-Remote`
- Uses Nordic UART (NUS) characteristic
- Button codes: `L/l R/r U/u D/d O/o` (upper = press, lower = release)

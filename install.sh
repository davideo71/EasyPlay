#!/usr/bin/env bash
#
# EasyPlay installer for Raspberry Pi OS Bookworm/Trixie (64-bit).
#
# What it does:
#   1. Installs apt packages (pygame, vlc, mpv, cec-utils, bluez, unclutter, ...)
#   2. Installs Python deps via apt where possible, pip (--break-system-packages
#      into ~/.local) for bleak which isn't in Debian.
#   3. Adds the user to the `bluetooth` group.
#   4. Drops a sudoers.d rule so the app can run `hciconfig hci0 {reset,up,down}`
#      without a password (EasyPlay calls this on BLE disconnect).
#   5. Installs a systemd service `easyplay.service`, left DISABLED by default.
#   6. Shrinks the rpi-swap file to 512 MiB (Pi OS defaults to 2 GiB).
#   7. Optionally disables the internal Pi Bluetooth so a USB BT dongle
#      becomes hci0, via --external-bt.  Requires a reboot.
#   8. Cleans the apt cache.
#
# For media storage on a USB drive, run `./setup-usb-media.sh` after this.
#
# Run from inside the cloned repo:
#     cd ~/Desktop/EasyPlay
#     ./install.sh                  # normal install
#     ./install.sh --external-bt    # also disable internal BT (reboot needed)
#
# Re-running is safe — every step is idempotent.

set -euo pipefail

EXTERNAL_BT=0
for arg in "$@"; do
    case "$arg" in
        --external-bt) EXTERNAL_BT=1 ;;
        -h|--help)
            sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="${SUDO_USER:-$USER}"
USER_HOME="$(eval echo "~${USER_NAME}")"

log()  { printf '\033[1;32m[easyplay]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[easyplay]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[easyplay]\033[0m %s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Linux" ]] || die "This installer is for Raspberry Pi OS, not $(uname -s)."

if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    log "Detected: ${PRETTY_NAME:-unknown}"
    case "${VERSION_CODENAME:-}" in
        bookworm|trixie) ;;
        *) warn "Tested on Bookworm/Trixie; continuing anyway." ;;
    esac
fi

# ── 1. apt packages ──────────────────────────────────────────────────────────
log "Updating apt and installing system packages…"
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    git \
    python3 \
    python3-pip \
    python3-venv \
    python3-pygame \
    python3-pil \
    python3-numpy \
    python3-opencv \
    python3-vlc \
    vlc \
    mpv \
    ffmpeg \
    cec-utils \
    bluez \
    bluetooth \
    rfkill \
    unclutter \
    fonts-dejavu

# ── 2. Python packages not in Debian ─────────────────────────────────────────
# bleak is the only one. Install for the invoking user, not root.
log "Installing bleak via pip (user site-packages)…"
sudo -u "$USER_NAME" pip3 install --user --break-system-packages --upgrade bleak

# ── 3. Bluetooth group ───────────────────────────────────────────────────────
if ! id -nG "$USER_NAME" | grep -qw bluetooth; then
    log "Adding $USER_NAME to the bluetooth group (takes effect next login)."
    sudo usermod -aG bluetooth "$USER_NAME"
else
    log "$USER_NAME already in bluetooth group."
fi

# ── 4. Passwordless hciconfig for BLE recovery ───────────────────────────────
SUDOERS_FILE="/etc/sudoers.d/easyplay-hciconfig"
SUDOERS_LINE="$USER_NAME ALL=(root) NOPASSWD: /usr/bin/hciconfig hci0 reset, /usr/bin/hciconfig hci0 up, /usr/bin/hciconfig hci0 down"
if [[ ! -f "$SUDOERS_FILE" ]] || ! sudo grep -qF "$SUDOERS_LINE" "$SUDOERS_FILE"; then
    log "Installing sudoers rule for passwordless hciconfig…"
    echo "$SUDOERS_LINE" | sudo tee "$SUDOERS_FILE" >/dev/null
    sudo chmod 440 "$SUDOERS_FILE"
    sudo visudo -cf "$SUDOERS_FILE" >/dev/null || {
        sudo rm -f "$SUDOERS_FILE"
        die "sudoers rule failed validation; aborting."
    }
else
    log "Sudoers rule already in place."
fi

# ── 5. systemd service (installed, left disabled) ────────────────────────────
SERVICE_SRC="$REPO_DIR/systemd/easyplay.service"
SERVICE_DST="/etc/systemd/system/easyplay.service"
if [[ -f "$SERVICE_SRC" ]]; then
    log "Installing systemd unit (disabled by default)…"
    sudo install -m 0644 "$SERVICE_SRC" "$SERVICE_DST"
    # Substitute %USER% / %HOME% / %REPO% placeholders.
    sudo sed -i \
        -e "s|%USER%|$USER_NAME|g" \
        -e "s|%HOME%|$USER_HOME|g" \
        -e "s|%REPO%|$REPO_DIR|g" \
        "$SERVICE_DST"
    sudo systemctl daemon-reload
    log "Service installed at $SERVICE_DST (not enabled)."
    log "Enable with: sudo systemctl enable --now easyplay.service"
else
    warn "No systemd/easyplay.service found in repo — skipping."
fi

# ── 6. Shrink rpi-swap file from 2 GiB to 512 MiB ────────────────────────────
# Pi OS defaults to ~2 GiB swap. EasyPlay (pygame + VLC) doesn't need that much.
# Drop-in lives in /etc/rpi/swap.conf.d/, resize runs immediately.
if [[ -d /etc/rpi ]]; then
    SWAP_DROPIN="/etc/rpi/swap.conf.d/10-easyplay.conf"
    if [[ ! -f "$SWAP_DROPIN" ]]; then
        log "Shrinking rpi-swap file to 512 MiB…"
        sudo mkdir -p /etc/rpi/swap.conf.d
        printf '[File]\nFixedSizeMiB=512\n' | sudo tee "$SWAP_DROPIN" >/dev/null
        if command -v /usr/lib/rpi-swap/bin/rpi-resize-swap-file >/dev/null 2>&1; then
            sudo /usr/lib/rpi-swap/bin/rpi-resize-swap-file 2>&1 | tail -3 || warn "swap resize had issues, check manually"
        fi
    else
        log "Swap drop-in already present."
    fi
fi

# ── 7. Optional: disable internal Bluetooth so USB dongle is hci0 ────────────
if [[ "$EXTERNAL_BT" -eq 1 ]]; then
    CONFIG_TXT="/boot/firmware/config.txt"
    [[ -f "$CONFIG_TXT" ]] || CONFIG_TXT="/boot/config.txt"
    if [[ -f "$CONFIG_TXT" ]]; then
        if ! grep -q "^dtoverlay=disable-bt" "$CONFIG_TXT"; then
            log "Disabling internal Bluetooth (dtoverlay=disable-bt in $CONFIG_TXT)…"
            sudo cp "$CONFIG_TXT" "${CONFIG_TXT}.bak-$(date +%Y%m%d-%H%M)"
            {
                echo ""
                echo "# EasyPlay: disable internal Bluetooth so USB dongle becomes hci0"
                echo "dtoverlay=disable-bt"
            } | sudo tee -a "$CONFIG_TXT" >/dev/null
            warn "Reboot required for internal BT to be disabled."
        else
            log "Internal BT already disabled in $CONFIG_TXT."
        fi
    else
        warn "Could not find config.txt; skipping --external-bt."
    fi
fi

# ── 8. Media folder ──────────────────────────────────────────────────────────
MEDIA_DIR="$USER_HOME/Desktop/codevideos"
if [[ ! -e "$MEDIA_DIR" ]]; then
    log "Creating media folder at $MEDIA_DIR"
    sudo -u "$USER_NAME" mkdir -p "$MEDIA_DIR"
fi

# ── 9. apt cache cleanup ─────────────────────────────────────────────────────
log "Cleaning apt cache…"
sudo apt-get clean
sudo apt-get autoremove -y >/dev/null 2>&1 || true

log "Done."
echo
echo "Next steps:"
echo "  1. Log out and back in (or reboot) so the bluetooth group takes effect."
if [[ "$EXTERNAL_BT" -eq 1 ]]; then
    echo "     (Reboot is required to activate the USB BT dongle as hci0.)"
fi
echo "  2. For media on a USB drive:  ./setup-usb-media.sh"
echo "     Otherwise drop videos into ~/Desktop/codevideos/"
echo "  3. Test run:    python3 $REPO_DIR/easyplay55.py"
echo "  4. When happy:  sudo systemctl enable --now easyplay.service"

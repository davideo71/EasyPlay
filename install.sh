#!/usr/bin/env bash
#
# EasyPlay installer for Raspberry Pi OS Bookworm (64-bit).
#
# What it does:
#   1. Installs apt packages (pygame, vlc, mpv, cec-utils, bluez, unclutter, ...)
#   2. Installs Python deps via apt where possible, pip (--break-system-packages
#      into ~/.local) for bleak which isn't in Debian.
#   3. Adds the user to the `bluetooth` group.
#   4. Drops a sudoers.d rule so the app can run `hciconfig hci0 {reset,up,down}`
#      without a password (EasyPlay calls this on BLE disconnect).
#   5. Installs a systemd user service `easyplay.service`, left DISABLED by default.
#
# Run from inside the cloned repo:
#     cd ~/Desktop/EasyPlay
#     ./install.sh
#
# Re-running is safe — every step is idempotent.

set -euo pipefail

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
    if [[ "${VERSION_CODENAME:-}" != "bookworm" ]]; then
        warn "Tested on Bookworm only; continuing anyway."
    fi
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

# ── 6. Media folder ──────────────────────────────────────────────────────────
MEDIA_DIR="$USER_HOME/Desktop/codevideos"
if [[ ! -d "$MEDIA_DIR" ]]; then
    log "Creating media folder at $MEDIA_DIR"
    sudo -u "$USER_NAME" mkdir -p "$MEDIA_DIR"
fi

log "Done."
echo
echo "Next steps:"
echo "  1. Log out and back in (or reboot) so the bluetooth group takes effect."
echo "  2. Drop videos into ~/Desktop/codevideos/"
echo "  3. Test run:    python3 $REPO_DIR/easyplay55.py"
echo "  4. When happy:  sudo systemctl enable --now easyplay.service"

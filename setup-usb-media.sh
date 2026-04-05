#!/usr/bin/env bash
#
# setup-usb-media.sh — point EasyPlay's media folder at a USB drive.
#
# What it does:
#   1. Lists removable USB block devices (not the SD card).
#   2. Asks which one to use (if more than one).
#   3. Adds a UUID-based /etc/fstab entry mounting it at /mnt/media
#      with nofail, noatime, user ownership. Survives reboots, and
#      won't hang boot if the drive is missing.
#   4. Ensures /mnt/media/codevideos exists on the drive.
#   5. Replaces ~/Desktop/codevideos with a symlink to /mnt/media/codevideos.
#
# Safe to re-run: if fstab entry already exists, it just remounts.

set -euo pipefail

USER_NAME="${SUDO_USER:-$USER}"
USER_HOME="$(eval echo "~${USER_NAME}")"
USER_UID="$(id -u "$USER_NAME")"
USER_GID="$(id -g "$USER_NAME")"
MOUNT_POINT="/mnt/media"
SYMLINK_TARGET="$USER_HOME/Desktop/codevideos"

log()  { printf '\033[1;32m[usb-media]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[usb-media]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[usb-media]\033[0m %s\n' "$*" >&2; exit 1; }

# List removable USB drives.  We filter by TRAN=usb so the SD card (mmc)
# and NVMe are excluded.
mapfile -t CANDIDATES < <(
    lsblk -rn -o NAME,TYPE,SIZE,TRAN,FSTYPE,UUID,LABEL,MOUNTPOINT |
    awk '$2=="part" && $4=="usb" && $5!="" {print}'
)

if [[ ${#CANDIDATES[@]} -eq 0 ]]; then
    die "No USB drive with a filesystem found. Plug one in and re-run."
fi

echo
echo "Found USB partitions:"
printf '  %-4s %-10s %-8s %-8s %-38s %s\n' "#" "DEV" "SIZE" "FS" "UUID" "LABEL"
i=0
for line in "${CANDIDATES[@]}"; do
    read -r name type size tran fstype uuid label mnt <<< "$line"
    printf '  %-4s %-10s %-8s %-8s %-38s %s\n' "$i" "/dev/$name" "$size" "$fstype" "$uuid" "${label:--}"
    ((i++))
done
echo

if [[ ${#CANDIDATES[@]} -eq 1 ]]; then
    CHOICE=0
    log "Using the only candidate: ${CANDIDATES[0]%% *}"
else
    read -r -p "Pick one by number: " CHOICE
    [[ "$CHOICE" =~ ^[0-9]+$ ]] || die "Not a number."
    [[ "$CHOICE" -lt "${#CANDIDATES[@]}" ]] || die "Out of range."
fi

read -r DEV TYPE SIZE TRAN FSTYPE UUID LABEL MNT <<< "${CANDIDATES[$CHOICE]}"
DEV="/dev/$DEV"
[[ -n "$UUID" ]] || die "$DEV has no UUID; can't create a stable fstab entry."

log "Selected: $DEV  ($FSTYPE, $SIZE, UUID=$UUID)"

# Unmount from any desktop-auto-mount location first
if [[ -n "${MNT:-}" && "$MNT" != "$MOUNT_POINT" ]]; then
    log "Unmounting from auto-mount location: $MNT"
    sudo umount "$MNT" 2>&1 || warn "umount failed (maybe already unmounted)"
fi

# Create mount point
sudo mkdir -p "$MOUNT_POINT"

# Build fstab options by fs type
case "$FSTYPE" in
    vfat|exfat)
        FSTAB_OPTS="defaults,nofail,uid=${USER_UID},gid=${USER_GID},umask=022,noatime,x-systemd.device-timeout=5s"
        ;;
    ext4|ext3|ext2|btrfs|xfs)
        FSTAB_OPTS="defaults,nofail,noatime,x-systemd.device-timeout=5s"
        ;;
    ntfs|ntfs3)
        FSTYPE="ntfs3"
        FSTAB_OPTS="defaults,nofail,uid=${USER_UID},gid=${USER_GID},umask=022,noatime,x-systemd.device-timeout=5s"
        ;;
    *)
        die "Unsupported fs type: $FSTYPE"
        ;;
esac

FSTAB_LINE="UUID=$UUID  $MOUNT_POINT  $FSTYPE  $FSTAB_OPTS  0  0"

if grep -q "$MOUNT_POINT" /etc/fstab; then
    if grep -q "UUID=$UUID" /etc/fstab; then
        log "fstab entry already exists for this drive."
    else
        die "/etc/fstab already has a $MOUNT_POINT entry with a different UUID; edit manually."
    fi
else
    log "Adding fstab entry…"
    sudo cp /etc/fstab "/etc/fstab.bak-$(date +%Y%m%d-%H%M)"
    echo "$FSTAB_LINE" | sudo tee -a /etc/fstab >/dev/null
    sudo systemctl daemon-reload
fi

# Mount (idempotent)
if ! mountpoint -q "$MOUNT_POINT"; then
    log "Mounting $MOUNT_POINT…"
    sudo mount "$MOUNT_POINT"
fi

# Ensure codevideos dir exists on the drive
if [[ ! -d "$MOUNT_POINT/codevideos" ]]; then
    log "Creating $MOUNT_POINT/codevideos"
    sudo mkdir -p "$MOUNT_POINT/codevideos"
fi

# Replace ~/Desktop/codevideos with a symlink to the mount
if [[ -L "$SYMLINK_TARGET" ]]; then
    CURRENT_TARGET="$(readlink "$SYMLINK_TARGET")"
    if [[ "$CURRENT_TARGET" == "$MOUNT_POINT/codevideos" ]]; then
        log "Symlink already in place."
    else
        log "Updating existing symlink."
        sudo -u "$USER_NAME" ln -sfn "$MOUNT_POINT/codevideos" "$SYMLINK_TARGET"
    fi
elif [[ -d "$SYMLINK_TARGET" ]]; then
    if [[ -n "$(ls -A "$SYMLINK_TARGET" 2>/dev/null || true)" ]]; then
        warn "$SYMLINK_TARGET is a non-empty directory; leaving it alone."
        warn "Move its contents to $MOUNT_POINT/codevideos manually, then remove it and re-run."
        exit 1
    fi
    log "Replacing empty directory with symlink."
    rmdir "$SYMLINK_TARGET"
    sudo -u "$USER_NAME" ln -sfn "$MOUNT_POINT/codevideos" "$SYMLINK_TARGET"
else
    log "Creating symlink."
    sudo -u "$USER_NAME" ln -sfn "$MOUNT_POINT/codevideos" "$SYMLINK_TARGET"
fi

echo
log "Done."
echo "  Drive mounted at:  $MOUNT_POINT"
echo "  Symlink:           $SYMLINK_TARGET -> $MOUNT_POINT/codevideos"
echo "  Contents:"
ls "$SYMLINK_TARGET/" 2>/dev/null | sed 's/^/    /' | head -20

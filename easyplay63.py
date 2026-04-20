"""
EasyPlay v63 - Accessible Media Player for Raspberry Pi 5.

Changes from v62 (CEC return-to-tuner):
  - cec_tv_to_normal() now uses the brand profile's inactive_source
    steps instead of a hardcoded 1F:82:00:00. Samsung TVs need
    4F:82:00:00 (source logical address 4) for the Anynet+ stream-path
    switch to actually route the TV back to its tuner; the hardcoded
    1F address was silently ignored by some sets.
  - cec_shutdown() now calls cec_tv_to_normal() synchronously instead
    of just sending "inactive source". So exiting via ESC/Q, the
    watcher, or any normal termination path also returns the TV to
    its tuner — not just the On/Off button path.

v62 changes from v61 (display resolution fix):
  - Force pygame render surface to 1920x1080 instead of asking the
    compositor for screen size.

    On a 4K monitor with the kernel cmdline pinning HDMI to 1080p
    (video=HDMI-A-1:1920x1080@60), labwc/Wayland still reports 3840x2160
    to pygame. Pygame then created a 4K fullscreen surface, but only a
    1080p-sized portion of it landed on the HDMI signal — the interface
    appeared cramped in a corner while VLC videos (which output directly
    via DRM/KMS at 1080p) played fullscreen fine.

    Hardcoding 1920x1080 keeps pygame's surface in lockstep with the
    HDMI output mode. Monitor hardware handles the upscale to 4K cheaply
    and losslessly for the viewer. Video playback stays smooth because
    the Pi 5 isn't pushing 4× the pixel bandwidth per frame.

    If the HDMI output is ever changed from 1080p (via cmdline.txt), this
    value will need to match the new resolution — or we pull the size
    from a config file.

v61 changes from v60 (startup responsiveness):
  - Cover thumbnails now load on a background thread. The carousel
    renders immediately on launch with calm, deterministic gradient
    placeholder tiles (one muted color per item, golden-ratio hue
    distribution so neighbors look distinct). Real covers swap in
    as the background loader finishes each JPG.

    Before: first frame was delayed until every cover was decoded
    and converted — ~1-3 seconds of blank screen on a 70+ item
    library.
    After:  first frame appears in ~50ms, covers drift in over
    the next 1-2 seconds.

    Trade-off: the async loader skips .convert(screen) (which must
    run on the main thread because it touches the display surface).
    Each cover gets converted lazily on first blit instead — slightly
    slower per frame but not user-visible.

v60 changes from v59:
  - "USB drive not connected" wait screen: Q key and remote On/Off
    button now quit EasyPlay (previously only ESC worked). BLE key
    queue is polled during the wait loop so the remote works there too.

v59 changes from v58 (video-playback CPU + library polish):
  - Title cleaning: _CLEAN_PATTERNS upgraded so sticky-digit audio codec
    tags ("OPUS51", "AC35 1", "DTS-HD MA 5 1") are fully consumed, and
    H.264 / x264 now match a space separator as well as a dot. Added a
    trailing scene-release group stripper (\B-XXX) that also eats the
    leftover dash from "-GROUP-[bracket]" patterns, so labels like
    "E02 - The Secret" display cleanly instead of "E02 - The Secret -playWEB".
    The \B anchor keeps hyphenated titles like "Spider-Man" safe.
  - Episode listing: natural sort for video filenames, so unpadded
    "E1..E13" folders (e.g. Mad Men) order 1,2,...,10,11,12,13 instead
    of the lexicographic 1,10,11,12,13,2,3,... bug.
  - Video playback loop: only redraws the frame when VLC has actually
    produced a new frame OR when the overlay state changes (pause icon,
    seek bar). Previously we ran screen.fill + blit + display.flip every
    single tick at 30fps even when VLC hadn't pushed a new frame —
    roughly ~500 MB/s of pixel writes for no visual change. This cuts
    the pygame share of video-playback CPU considerably without any
    behavior change.
  - Adaptive tick rate: instead of the hardcoded 30fps tick, the video
    loop now queries player.get_fps() once after playback starts and
    ticks at the source rate (clamped 24–60). 24fps movies tick at 24
    (cheaper, no wasted ticks). 50/60fps content ticks at 50/60 and is
    no longer half-dropped as it was in v58. Falls back to 30 if VLC
    can't report fps. Pause branch still drops to 10 Hz.
  - Context: Pi 5 has NO hardware H.264 decoder (only HEVC). H.264
    content is always software-decoded. The remaining CPU overhead
    is VLC's software decode + YUV→BGRA conversion + audio. The only
    bigger wins from here are transcoding content to HEVC (free
    hardware decode) or handing the display off to VLC directly and
    losing the custom overlays.

Changes from v57 (CPU / heat reduction — behavior-preserving):
  - Video playback: skip the per-frame ~8MB bytes(_frame_buf) memcpy by
    wrapping the VLC ctypes buffer with pygame.image.frombuffer directly,
    and cache the scaled frame so we only rescale when VLC delivers a new
    frame. This was ~250 MB/s of memcpy at 30fps that is now eliminated.
  - Video playback: while paused, the frame never changes — drop the
    render rate to 10fps instead of 30fps.
  - Main loop: add render_dirty flag. On the idle carousel with no
    animation, no open menus, no fades, no key events — we skip the full
    render + flip entirely, which avoids redrawing the whole carousel
    (DVD stacks, glows, progress bars, text) 15x per second for nothing.
  - Main loop: drop idle carousel FPS from 15 to 10.
  - Picker: cache per-slot scaled thumbnails so we don't rescale the
    same thumbs every frame while idle.

Changes from v56:
  - BLE wake-from-sleep support: when the ESP32-C3 remote wakes from deep
    sleep, the first button press happens before BLE connects. The Pi now
    detects orphan release events (release without a preceding press in the
    current connection) and promotes them to press + synthetic release.
    This means the wake button is never lost.
  - Pi sends "R" (ready) signal to remote after subscribing to notifications,
    so the remote knows the Pi is listening.
  - BLE reconnect loop is more resilient to remote sleep/wake cycles.

v56 original notes below.



Built for stroke recovery / accessibility:
- Horizontal carousel with DVD stack effect for series
- BLE 4-button remote: LEFT/RIGHT scroll, UP confirm/play, DOWN back/exit
- Steady soft glow selection (optional pulsing mode)
- Playback progress tracking with auto-resume, progress bars on icons
- HDMI-CEC TV control
- Hidden setup menu (hold DOWN)
- Crash recovery via faulthandler + systemd
- All heavy visual effects (glow, DVD stacks) are pre-cached for 60fps

Spec: EasyPlay_Specification_v2.docx
Target: 1920x1080 fullscreen, Raspberry Pi 5.

Changes from v54:
  - BLE reconnect rewritten to match pi_reconnect_test_v12 pattern:
      * hciconfig hci0 reset instead of systemctl restart bluetooth
      * Live BleakScanner detection callback (avoids BlueZ device cache bug)
      * needs_reset=False at loop start, True only after disconnect
      * 0.5s settle delay before reset after disconnect
      * Passes device object to BleakClient (as v12 does)
      * Explicit adapter="hci0" throughout
      * Default BlueZ scan window (no aggressive scan params)
  - Reverted to simple KEYDOWN/KEYUP mapping (uppercase=press, lowercase=release)
    remote_main.py sends events immediately on press/release like a real keyboard —
    synthesised events were wrong and caused sluggish double-firing
  - BLE thread prints to stdout for Thonny visibility (connected, subscribed, buttons)
"""

from __future__ import annotations

import ctypes
import faulthandler
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

os.environ.setdefault("SDL_NOMOUSE", "1")
os.environ.setdefault("SDL_VIDEO_X11_DGAMOUSE", "0")

import pygame

APP_DIR = Path(__file__).resolve().parent
CRASH_LOG = APP_DIR / "easyplay_crash.log"
try:
    with open(CRASH_LOG, "a") as _fh:
        faulthandler.enable(file=_fh, all_threads=True)
except Exception:
    faulthandler.enable()


def _log(msg, exc=None):
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
            if exc:
                import traceback
                traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
    except Exception:
        pass


try:
    import cv2
except ImportError:
    cv2 = None

import numpy as np

try:
    import vlc as _vlc_mod
    VLC_AVAILABLE = True
except ImportError:
    _vlc_mod = None
    VLC_AVAILABLE = False

try:
    import asyncio as _asyncio
    from bleak import BleakClient, BleakScanner
    BLEAK_AVAILABLE = True
except ImportError:
    BLEAK_AVAILABLE = False

from PIL import Image, ImageDraw, ImageFilter

DEFAULT_MEDIA_DIR = Path.home() / "Desktop" / "codevideos"
THUMB_CACHE_DIR = APP_DIR / "video_thumb_cache"
THUMB_CACHE_SIZE = (400, 225)
CONFIG_FILE = APP_DIR / "easyplay_config.json"
PROGRESS_FILE = APP_DIR / "easyplay_progress.json"

HD_WIDTH, HD_HEIGHT = 1920, 1080
FRAME_ASPECT = 2 / 3
PICKER_FRAME_ASPECT = 16 / 9
GAP = 12
N_SLOTS = 11
CENTER_SLOT = 5
PICKER_THUMBS_MAX = 12
PICKER_THUMB_LOAD_INTERVAL = 0.4

ANIM_MS = 420
ANIM_MS_AUTOSCROLL = 300
DEBOUNCE_MS = 180

VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".webm")
COVER_IMAGE_EXTS = (".jpg", ".jpeg", ".png")
IGNORED_PATTERNS = ("sample", "trailer", ".part", ".!ut")
HIDDEN_NAMES = (".ds_store",)

DVD_ANGLES = (-7, -3.5, 0, 3.5, 7)
DVD_BRIGHT_MIN, DVD_BRIGHT_MAX = 0.6, 1.0
DVD_OUTLINE_W = 2
DVD_OUTLINE_BLUR = 1
DVD_EDGE_PAD = 10
DVD_OUTLINE_PAD = 14
DVD_NON_CENTER_Y_OFFSET = 28
DVD_CENTER_Y_RAISE = 10  # Raise center stack to avoid label overlap

GLOW_PAD = 14
GLOW_BLUR_SPOTLIGHT = 7
GLOW_BLUR_DIM = 4

SEEK_INTERVAL_S = 0.25
PROGRESS_SAVE_INTERVAL = 3.0
COMPLETION_THRESHOLD = 5.0

PROGRESS_BAR_H = 60
PROGRESS_BAR_BG = (80, 80, 80)
PROGRESS_BAR_BG_ALPHA = 102  # 40% opacity
PROGRESS_BAR_COLOR = (255, 255, 255)

SCROLL_LEFT_KEYS = (pygame.K_LEFT,)
SCROLL_RIGHT_KEYS = (pygame.K_RIGHT,)

def _init_scroll_keys():
    left = [pygame.K_LEFT]
    right = [pygame.K_RIGHT]
    for name, lst in (("audioprev", left), ("audionext", right),
                      ("ac back", left), ("ac forward", right)):
        try: lst.append(pygame.key.key_code(name))
        except ValueError: pass
    return tuple(left), tuple(right)

def _rebuild_keys_from_map(km):
    """Rebuild global SCROLL_LEFT/RIGHT_KEYS and return confirm/back/power key tuples from key map."""
    global SCROLL_LEFT_KEYS, SCROLL_RIGHT_KEYS
    base_left, base_right = _init_scroll_keys()
    # Merge mapped keys with hardware media keys
    left_set = set(base_left) | set(km.get("left", []))
    right_set = set(base_right) | set(km.get("right", []))
    SCROLL_LEFT_KEYS = tuple(left_set)
    SCROLL_RIGHT_KEYS = tuple(right_set)
    return (tuple(km.get("confirm", [pygame.K_UP, pygame.K_RETURN])),
            tuple(km.get("back", [pygame.K_DOWN, pygame.K_ESCAPE])),
            tuple(km.get("power", [pygame.K_q])))

def _hide_mouse():
    try: pygame.mouse.set_visible(False)
    except Exception: pass
    try:
        # Pygame 2.x: set a fully transparent 8x8 cursor
        surf = pygame.Surface((8, 8), pygame.SRCALPHA)
        surf.fill((0, 0, 0, 0))
        cursor = pygame.cursors.Cursor((0, 0), surf)
        pygame.mouse.set_cursor(cursor)
    except Exception: pass
    try: pygame.mouse.set_pos((-9999, -9999))
    except Exception: pass

def _launch_unclutter():
    """Try to launch unclutter to hide mouse system-wide (Linux)."""
    try:
        if sys.platform.startswith("linux") and shutil.which("unclutter"):
            subprocess.Popen(["unclutter", "-idle", "0", "-root"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    except Exception: pass

# === Configuration ===
def _load_config():
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, encoding="utf-8") as f: return json.load(f)
    except Exception: pass
    return {}

def _save_config(data):
    try:
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
        os.replace(tmp, CONFIG_FILE)
    except Exception as e: _log(f"Config save error: {e}", e)

def _cfg_get(key, default): return _load_config().get(key, default)
def _cfg_set(key, value):
    d = _load_config(); d[key] = value; _save_config(d)

def get_media_folder():
    p = _cfg_get("video_folder", str(DEFAULT_MEDIA_DIR))
    return Path(p).expanduser() if p else DEFAULT_MEDIA_DIR

def get_scroll_speed():
    try: return max(5, min(30, int(_cfg_get("scroll_speed", 10))))
    except: return 10

def get_setup_hold_sec():
    try: return max(1.0, min(15.0, float(_cfg_get("setup_hold_sec", 5.0))))
    except: return 5.0

def get_autoscroll(): return bool(_cfg_get("autoscroll", False))
def get_volume(): 
    try: return max(0, min(100, int(_cfg_get("volume", 100))))
    except: return 100
def get_seek_overlay(): return bool(_cfg_get("seek_overlay", True))

# Key mapping: each action maps to a list of pygame key codes
DEFAULT_KEY_MAP = {
    "left": [pygame.K_LEFT],
    "right": [pygame.K_RIGHT],
    "confirm": [pygame.K_UP, pygame.K_RETURN],
    "back": [pygame.K_DOWN, pygame.K_ESCAPE],
    "power": [pygame.K_q],
}
KEY_ACTION_NAMES = ["left", "right", "confirm", "back", "power"]
KEY_ACTION_LABELS = {"left": "Left / Rewind", "right": "Right / Forward",
                     "confirm": "Up / Confirm", "back": "Down / Back", "power": "Power / Quit"}

def get_key_map():
    """Load key mapping from config. Returns dict of action -> list of key codes."""
    raw = _cfg_get("key_map", None)
    if not raw or not isinstance(raw, dict): return dict(DEFAULT_KEY_MAP)
    result = {}
    for action in KEY_ACTION_NAMES:
        codes = raw.get(action)
        if isinstance(codes, list) and all(isinstance(c, int) for c in codes):
            result[action] = codes
        else:
            result[action] = list(DEFAULT_KEY_MAP[action])
    return result

def save_key_map(km):
    _cfg_set("key_map", {k: list(v) for k, v in km.items()})

def key_name_safe(code):
    """Get human-readable name for a pygame key code."""
    try: return pygame.key.name(code).upper()
    except: return f"KEY_{code}"

def get_display_index():
    try: return max(0, int(_cfg_get("display_index", 0)))
    except: return 0

def get_bt_remote():
    cfg = _load_config()
    addr = str(cfg.get("bluetooth_remote_addr", "")).strip()
    name = str(cfg.get("bluetooth_remote_name", "")).strip()
    return (addr, name) if addr else ("", "")

def save_bt_remote(addr, name):
    d = _load_config()
    d["bluetooth_remote_addr"] = addr.strip()
    d["bluetooth_remote_name"] = name.strip()
    _save_config(d)

# === Progress Tracking ===
_progress_cache = None  # None = not yet loaded from disk
_last_progress_write = 0.0

def _norm_path(p):
    try: return str(Path(p).resolve())
    except: return p

def _ensure_progress_file():
    try:
        if not PROGRESS_FILE.exists():
            with open(PROGRESS_FILE, "w", encoding="utf-8") as f: json.dump({}, f)
    except Exception as e: _log(f"Progress init error: {e}", e)

def _load_progress():
    global _progress_cache
    if _progress_cache is not None:
        return _progress_cache
    try:
        if PROGRESS_FILE.exists():
            with open(PROGRESS_FILE, encoding="utf-8") as f: data = json.load(f)
            result = {}
            for k, v in (data or {}).items():
                if isinstance(v, dict) and "position_sec" in v and "duration_sec" in v:
                    result[_norm_path(k)] = v
            _progress_cache = result
        else: _progress_cache = {}
    except Exception: _progress_cache = {}
    return _progress_cache

def get_progress_entry(path_str):
    return _load_progress().get(_norm_path(path_str))

def get_progress_completed(path_str):
    entry = get_progress_entry(path_str)
    return bool(entry.get("completed")) if entry else False

def get_progress_ratio(path_str):
    entry = get_progress_entry(path_str)
    if not entry: return 0.0
    pos = float(entry.get("position_sec", 0))
    dur = float(entry.get("duration_sec", 0))
    if dur <= 0 or pos <= 0: return 0.0
    if entry.get("completed"): return 1.0
    return max(0.0, min(1.0, pos / dur))

def get_resume_position(path_str):
    entry = get_progress_entry(path_str)
    if not entry or entry.get("completed"): return 0.0
    pos = float(entry.get("position_sec", 0))
    dur = float(entry.get("duration_sec", 0))
    if pos > 0 and dur > 0 and pos < dur - COMPLETION_THRESHOLD: return pos
    return 0.0

def save_progress(path_str, pos_sec, dur_sec, completed=False, *, force=False):
    global _last_progress_write
    now = time.monotonic()
    if not force and now - _last_progress_write < PROGRESS_SAVE_INTERVAL: return
    try:
        data = _load_progress()
        key = _norm_path(path_str)
        entry = {"path": key, "name": clean_media_name(Path(path_str).stem) if path_str else "",
                 "position_sec": round(pos_sec, 1), "duration_sec": round(dur_sec, 1),
                 "completed": bool(completed), "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S")}
        data[key] = entry  # update RAM cache immediately
        tmp = PROGRESS_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
        os.replace(tmp, PROGRESS_FILE)
        _last_progress_write = now
    except Exception as e: _log(f"Progress save error: {e}", e)

def clear_all_progress():
    global _progress_cache
    _progress_cache = {}
    try:
        if PROGRESS_FILE.exists(): PROGRESS_FILE.unlink()
    except Exception: pass

# === Filename Cleaning ===
_CLEAN_PATTERNS = [
    # Bracket and parenthesis groups — consume whole regardless of content
    # (v55 required all-uppercase inside, which missed [i_c], [eztv.re], [TGx])
    r"\[[^\]]*\]",
    r"\([^)]*\)",

    # Resolutions / bit depth / file sizes
    r"\b\d{3,4}p\b",
    r"\b\d+[\._ ]?bit\b",
    r"\b\d+(?:\.\d+)?\s?[MG]B\b",
    r"\b(?:4K|8K|UHD|HDR|DV|SDR)\b",

    # Sources
    r"\bWEB[\- \.]?RIP\b", r"\bWEB[\- \.]?DL\b", r"\bWEB\b",
    r"\bBRRip\b", r"\bBDRip\b",
    r"\bBluRay\b", r"\bBlu[\- ]Ray\b",
    r"\bHDRip\b", r"\bHDTV\b", r"\bSDTV\b",
    r"\bHDCAM\b", r"\bDLRip\b", r"\baWEBRip\b", r"\bDVDRip\b", r"\bDVDScr\b", r"\bDVD\b",

    # Codecs
    r"\bHEVC\b", r"\bx[\s\.]?26[45]\b", r"\bh[\s\.]?26[45]\b",
    r"\bH264\b", r"\bH265\b",
    r"\bXviD\b", r"\bDivX\b", r"\bAV1\b", r"\bVP9\b",

    # Audio — order matters so the longer chunk is matched first and we
    # don't leave an orphan digit behind (was a v55 bug on "DDP 5 1").
    # v59: audio codec names can be followed by sticky channel digits with
    # no separator (e.g. "OPUS51", "AC35 1") — consume any trailing
    # (space/dot/digit)* so we don't leave orphan numbers behind.
    r"\bDDP?[\s\.]?\d[\s\.]\d\b",
    r"\bDDP?[\s\.]?\d\b",
    r"\bDDP?\b",
    r"\b[257][\.\s]?[01]\b",
    r"\bAAC\d*(?:\.\d)?(?:[\s\.]?\d)*\b",
    r"\bAC3(?:[\s\.]?\d)*\b",
    r"\bDTS(?:[\s\.\-]?HD)?(?:[\s\.]?MA)?(?:[\s\.]?\d)*\b",
    r"\bMA(?:[\s\.]?\d)*\b",  # stray "MA" left behind by DTS-HD MA variants
    r"\bTrueHD(?:[\s\.]?\d)*\b",
    r"\bAtmos\b",
    r"\bFLAC(?:[\s\.]?\d)*\b",
    r"\bMP3\b",
    r"\bEAC3(?:[\s\.]?\d)*\b", r"\bE-AC-3(?:[\s\.]?\d)*\b",
    r"\bOPUS(?:[\s\.]?\d)*\b", r"\bOGG\b",

    # Networks / providers
    r"\bAMZN\b", r"\bNF\b", r"\bHMAX\b",

    # Scene release groups (explicit known tags)
    r"\bYTS\.?\w*\b", r"\bYIFY\b", r"\bRARBG\b",
    r"\bGalaxyRG\b", r"\bGalaxyTV\b",
    r"\bEZTVx?\b", r"\beztv(?:\.re)?\b",
    r"\bION265\b", r"\bNeoNoir\b", r"\bFENiX\b", r"\bMeGusta\b",
    r"\bi_c\b", r"\bTGx\b", r"\bBONE\b", r"\bUKB\b",
    r"\bSUCCESSFULCRAB\b", r"\bSuccessfulCrab\b",
    r"\bExKinoRay\b", r"\bFeranki\d*\b", r"\bscarabey\b",

    # Edition tags
    r"\bPROPER\b", r"\bREPACK\b", r"\bCriterion\b",
    r"\bESub\b", r"\bEng\b", r"\bFHC\b", r"\biTA\b", r"\bINTERNAL\b", r"\bLIMITED\b",
    r"\bUNRATED\b", r"\bEXTENDED\b", r"\bDIRECTORS?[\s\.]?CUT\b",
    r"\bREMASTERED\b", r"\bCOMPLETE\b",

    # Containers / other
    r"\b(?:MP4|MKV|AVI|MOV|M4V)\b",
    r"\bIMAX\b",

    # Trailing scene-release group stripper — catches unknown groups like
    # "-EMPATHY", "-SPARKS", "-NTb" at the very end of the string.
    # Uses \B so the dash must be preceded by a non-word char (e.g. a space
    # left behind after dot-normalization) — this keeps hyphenated titles
    # like "Spider-Man" safe because there the dash has a word boundary
    # before it. Minimum 4 chars after the dash to avoid eating real words.
    # Allow trailing whitespace AND dashes so we also strip the leftover
    # hyphen from "-playWEB-[Feranki1980]" once the bracket is blanked.
    r"\B-[A-Za-z][A-Za-z0-9]{2,}[\s\-]*$",
]

def clean_media_name(name):
    """Return a human-readable display name from a scene-release folder name.

    v56: normalize dots/underscores to spaces FIRST so \b-anchored patterns
    match consistently regardless of separator style, then apply the noise
    patterns, then strip standalone years, then trim edges.
    """
    original = name
    # Normalize separators so \b patterns work on dot-separated names too
    cleaned = re.sub(r"[._]+", " ", name)
    # Apply all noise patterns case-insensitively
    for pat in _CLEAN_PATTERNS:
        cleaned = re.sub(pat, " ", cleaned, flags=re.IGNORECASE)
    # Note: we do NOT strip bare 4-digit years here. Years inside brackets
    # like (2019) or [2019] are already consumed by the bracket patterns
    # above. Remaining bare year-like numbers are more likely to be actual
    # titles ("1917", "2001 A Space Odyssey") than release metadata.
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Trim leftover punctuation at edges
    cleaned = re.sub(r"^[\s\-\.\[\]\(\)]+|[\s\-\.\[\]\(\)]+$", "", cleaned)
    return cleaned if cleaned and len(cleaned) >= 2 else original

def parse_series_title(folder_name):
    """Extract series name and season from folder name like 'Breaking.Bad.S02' → ('Breaking Bad', 'Season 2')."""
    cleaned = clean_media_name(folder_name)
    # Try to find S01, Season 1, etc.
    m = re.search(r'[Ss](?:eason\s*)?(\d{1,2})', cleaned)
    if m:
        season_num = int(m.group(1))
        series_name = cleaned[:m.start()].strip().rstrip("- ")
        return series_name, f"Season {season_num}"
    return cleaned, ""

def parse_episode_label(filename):
    """Extract episode number and name from filename like 'S02E03.The.One.Where.mp4' → 'E03 - The One Where'."""
    stem = Path(filename).stem if not isinstance(filename, str) else Path(filename).stem
    cleaned = clean_media_name(stem)
    # Try to find E03, Episode 3, etc.
    m = re.search(r'[Ee](?:pisode\s*)?(\d{1,3})', cleaned)
    if m:
        ep_num = int(m.group(1))
        ep_name = cleaned[m.end():].strip().lstrip("- .")
        if ep_name:
            return f"E{ep_num:02d} - {ep_name}"
        return f"Episode {ep_num}"
    # No episode pattern found - just return cleaned name
    return cleaned

# === Media Scanning ===
def _is_hidden(p): return p.name.startswith(".") or p.name.startswith("._") or p.name.lower() in HIDDEN_NAMES
def _is_junk_video(p): return any(pat in p.name.lower() for pat in IGNORED_PATTERNS)

_NATSORT_SPLIT = re.compile(r"(\d+)")

def _natural_sort_key(name):
    """Natural sort key: 'E2' sorts before 'E10'.

    Splits on digit runs and converts numeric parts to int, so that
    mixed-width episode numbers (E1..E9, E10..E13) order correctly.
    Case-insensitive for stability.
    """
    return [int(p) if p.isdigit() else p.lower()
            for p in _NATSORT_SPLIT.split(name)]

def find_videos_in_folder(folder):
    if not folder or not folder.is_dir(): return []
    videos = [f for f in folder.iterdir()
              if f.is_file() and not _is_hidden(f)
              and f.suffix.lower() in VIDEO_EXTS and not _is_junk_video(f)]
    videos.sort(key=lambda f: _natural_sort_key(f.name))
    return videos

@dataclass
class MediaItem:
    name: str; cover_path: Path | None; video_path: Path | None
    all_videos: list[Path]; is_series: bool = False

def _pick_cover(subdir):
    """Deterministically pick a cover image from a media folder.

    Preference order:
      1. cover.jpg / cover.jpeg / cover.png
      2. poster.jpg / poster.jpeg / poster.png
      3. folder.jpg / folder.jpeg / folder.png (Kodi/Plex convention)
      4. Any other image, picked in sorted filename order (stable)
    """
    images = [f for f in subdir.iterdir()
              if f.is_file() and not _is_hidden(f)
              and f.suffix.lower() in COVER_IMAGE_EXTS]
    if not images:
        return None
    for preferred in ("cover", "poster", "folder"):
        for f in images:
            if f.stem.lower() == preferred:
                return f
    return sorted(images, key=lambda f: f.name.lower())[0]

def check_media_folder():
    """Check if the media folder is accessible. Returns (ok, reason) tuple."""
    root = get_media_folder()
    try:
        if root.is_dir():
            return True, ""
    except OSError:
        pass
    if root.is_symlink():
        return False, "USB drive not connected"
    return False, f"Media folder not found:\n{root}"

def scan_media_library(folder=None):
    root = folder or get_media_folder(); items = []
    try:
        if not root.is_dir(): return items
    except OSError:
        _log(f"Media folder not accessible: {root} (drive not mounted?)")
        return items
    for subdir in sorted(root.iterdir()):
        if not subdir.is_dir() or _is_hidden(subdir): continue
        name = clean_media_name(subdir.name)
        cover = _pick_cover(subdir)
        videos = find_videos_in_folder(subdir)
        if not videos and not cover: continue
        items.append(MediaItem(name=name, cover_path=cover, video_path=videos[0] if videos else None,
                               all_videos=videos, is_series=len(videos) > 1))
    return items

# === Thumbnails ===
def _thumb_cache_path(video_path, seek_ratio=0.5):
    try:
        sr = round(seek_ratio, 2)
        key_str = f"{video_path.resolve()}:{video_path.stat().st_mtime}:{sr}"
        h = hashlib.sha256(key_str.encode()).hexdigest()[:24]
        THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return THUMB_CACHE_DIR / f"{h}.jpg"
    except Exception: return None

def _extract_thumb_subprocess(path, max_w, max_h, seek_ratio=0.5):
    if cv2 is None: return None
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False); tmp.close(); out = Path(tmp.name)
    script = f"""
import sys, numpy as np
try:
    import cv2; from PIL import Image
    cap = cv2.VideoCapture({repr(str(path))})
    if not cap.isOpened(): sys.exit(1)
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(total * {seek_ratio})))
    ret, frame = cap.read(); cap.release()
    if not ret or frame is None: sys.exit(1)
    h, w = frame.shape[:2]
    if w <= 0 or h <= 0: sys.exit(1)
    scale = min({max_w}/w, {max_h}/h)
    nw, nh = max(1, int(w*scale)), max(1, int(h*scale))
    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(frame).resize((nw,nh), Image.Resampling.LANCZOS)
    pil.save({repr(str(out))})
except Exception: sys.exit(1)
"""
    try:
        r = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=10)
        if r.returncode == 0 and out.exists(): return out
    except Exception: pass
    out.unlink(missing_ok=True); return None

def get_video_thumbnail(path, max_w, max_h, screen, seek_ratio=0.5):
    cache = _thumb_cache_path(path, seek_ratio)
    try:
        if cache and cache.exists():
            pil = Image.open(cache).convert("RGB")
            surf = pygame.image.frombytes(pil.tobytes(), pil.size, "RGB").convert(screen)
            if pil.size != (max_w, max_h): surf = pygame.transform.scale(surf, (max_w, max_h))
            return surf
        out = _extract_thumb_subprocess(path, THUMB_CACHE_SIZE[0], THUMB_CACHE_SIZE[1], seek_ratio)
        if not out: return None
        pil = Image.open(out).convert("RGB")
        if cache: pil.save(cache, "JPEG", quality=85)
        out.unlink(missing_ok=True)
        surf = pygame.image.frombytes(pil.tobytes(), pil.size, "RGB").convert(screen)
        if pil.size != (max_w, max_h): surf = pygame.transform.scale(surf, (max_w, max_h))
        return surf
    except Exception as e: _log(f"Thumbnail error: {path}: {e}", e); return None

# === Bluetooth ===
def _bt_strip_ansi(s):
    """Remove ANSI escape sequences from bluetoothctl output."""
    return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', s)

def _bt_is_unnamed(name, addr):
    """Check if a BLE device 'name' is really just a reformatted MAC address."""
    if not name: return True
    clean = name.replace("-", ":").replace("_", ":").upper().strip()
    return clean == addr.upper()

def bt_scan():
    """Scan for nearby Bluetooth devices using bluetoothctl.
    Returns list of (addr, name) tuples sorted by name, with named devices first."""
    names = {}  # addr -> best known name
    try:
        if not sys.platform.startswith("linux") or not shutil.which("bluetoothctl"): return []
        # Patterns for the two line types that indicate a device:
        #   [NEW] Device AA:BB:CC:DD:EE:FF SomeName
        #   [CHG] Device AA:BB:CC:DD:EE:FF Name: SomeName
        new_re = re.compile(r"Device\s+([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\s+(.*)")
        chg_re = re.compile(r"Device\s+([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\s+Name:\s+(.*)")
        # Phase 1: Scan for 12 seconds, collecting names from live output
        proc = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        proc.stdin.write("power on\nmenu scan\ntransport le\nback\nscan on\n"); proc.stdin.flush()
        start = time.monotonic()
        while time.monotonic() - start < 12 and proc.poll() is None:
            import select as _sel
            ready, _, _ = _sel.select([proc.stdout], [], [], 0.3)
            if not ready: continue
            raw = proc.stdout.readline()
            if not raw: continue
            line = _bt_strip_ansi(raw)
            # Only process [NEW] Device and [CHG] Device Name: lines
            if "Device" not in line: continue
            # Name change — always update
            mc = chg_re.search(line)
            if mc:
                addr = mc.group(1); resolved = mc.group(2).strip()
                if resolved and not _bt_is_unnamed(resolved, addr):
                    names[addr] = resolved
                continue
            # New device discovered
            mn = new_re.search(line)
            if mn:
                addr = mn.group(1); after = mn.group(2).strip()
                if after and not _bt_is_unnamed(after, addr):
                    names[addr] = after
                elif addr not in names:
                    names[addr] = ""  # seen but no name yet
        proc.stdin.write("scan off\nquit\n"); proc.stdin.flush()
        try: proc.wait(timeout=3)
        except: proc.kill()
        # Phase 2: Query bluetoothctl info for each device that has no name yet
        unnamed = [a for a, n in names.items() if not n]
        for addr in unnamed:
            try:
                result = subprocess.run(["bluetoothctl", "info", addr],
                                        capture_output=True, text=True, timeout=3)
                for info_line in result.stdout.splitlines():
                    info_line = info_line.strip()
                    if info_line.startswith("Name:"):
                        resolved = info_line.split(":", 1)[1].strip()
                        if resolved and not _bt_is_unnamed(resolved, addr):
                            names[addr] = resolved
                        break
                    elif info_line.startswith("Alias:"):
                        resolved = info_line.split(":", 1)[1].strip()
                        if resolved and not _bt_is_unnamed(resolved, addr):
                            names[addr] = resolved
                        # Don't break — prefer Name over Alias if both exist
            except Exception: pass
        # Build sorted list: named devices first
        devices = [(addr, name) for addr, name in names.items()]
        devices.sort(key=lambda d: (0, d[1].lower()) if d[1] else (1, d[0]))
    except Exception:
        devices = []
    return devices

def bt_pair(addr, name):
    try:
        if not shutil.which("bluetoothctl"): return False, "bluetoothctl not found"
        # Use interactive bluetoothctl with auto-confirm agent to avoid OS popups
        proc = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        # Set up default agent that auto-confirms (NoInputNoOutput avoids passkey dialogs)
        commands = f"agent NoInputNoOutput\ndefault-agent\ntrust {addr}\npair {addr}\n"
        try:
            proc.stdin.write(commands); proc.stdin.flush()
            start = time.monotonic()
            output = []
            while time.monotonic() - start < 20:
                import select
                ready, _, _ = select.select([proc.stdout], [], [], 0.5)
                if ready:
                    line = proc.stdout.readline()
                    if not line: break
                    output.append(line.strip())
                    low = line.lower()
                    if "pairing successful" in low or "already exists" in low:
                        break
                    if "failed" in low and "pair" in low:
                        break
                if proc.poll() is not None: break
            # Now connect
            try:
                proc.stdin.write(f"connect {addr}\n"); proc.stdin.flush()
                time.sleep(3)
            except: pass
            try:
                proc.stdin.write("quit\n"); proc.stdin.flush(); proc.wait(timeout=3)
            except: proc.kill()
            out_text = "\n".join(output)
            if "pairing successful" in out_text.lower() or "already exists" in out_text.lower():
                save_bt_remote(addr, name); return True, f"Paired: {name}"
            elif "failed" in out_text.lower():
                # Extract error message
                for line in output:
                    if "failed" in line.lower(): return False, line.strip()[:80]
                return False, "Pairing failed"
            else:
                # Assume success if no explicit failure
                save_bt_remote(addr, name); return True, f"Paired: {name}"
        except Exception as e:
            try: proc.kill()
            except: pass
            return False, str(e)[:80]
    except subprocess.TimeoutExpired: return False, "Pairing timed out"
    except Exception as e: return False, str(e)[:80]

def bt_reconnect_saved():
    """Legacy stub — kept so startup call doesn't error. BLE now handled by bleak thread."""
    pass

# ── BLE UART constants (Nordic UART Service) ────────────────────────────────
_UART_TX_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # notifications from ESP32
_UART_RX_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # write to ESP32

# ── Shared queue: BLE thread posts key chars, main loop reads them ──────────
import queue as _queue
_ble_key_queue = _queue.Queue()
_ble_thread_running = False
_ble_thread = None
_ble_loop = None

def _ble_reset_adapter():
    """Reset hci0 to clear stale scan/connection state. Plain sync — fine to call from async."""
    try:
        subprocess.run(["sudo", "hciconfig", "hci0", "reset"],
                       capture_output=True, timeout=3)
        time.sleep(0.15)
    except Exception:
        try:
            subprocess.run(["sudo", "hciconfig", "hci0", "down"],
                           capture_output=True, timeout=3)
            time.sleep(0.2)
            subprocess.run(["sudo", "hciconfig", "hci0", "up"],
                           capture_output=True, timeout=3)
            time.sleep(0.3)
        except Exception: pass

def _ble_uart_thread(addr):
    """Background thread: BLE reconnect matching pi_reconnect_test_v12 pattern."""
    global _ble_thread_running, _ble_loop
    if not BLEAK_AVAILABLE:
        _log("bleak not available — BLE remote disabled")
        return

    import asyncio
    from bleak import BleakScanner, BleakClient
    from bleak.exc import BleakError, BleakDeviceNotFoundError

    # Track which buttons have been pressed in the current connection.
    # If a release arrives for a button never pressed, it's a wake-from-sleep
    # event — promote to press and schedule a synthetic release.
    pressed_buttons = set()
    _deferred_releases = []  # list of (monotonic_time, char)

    _BUTTON_NAMES = {
        'L': 'LEFT', 'R': 'RIGHT', 'U': 'UP', 'D': 'DOWN', 'O': 'ON/OFF',
    }

    def _put_event(char):
        """Put a BLE key char onto the queue."""
        print(f"[BLE] button: {char!r}", flush=True)
        _ble_key_queue.put(char)

    def notification_handler(sender, data):
        try:
            char = data.decode().strip()
            if not char:
                return
            name = _BUTTON_NAMES.get(char.upper(), char)
            is_press = char.isupper()

            # Orphan release? Remote woke from sleep — press happened before BLE.
            if not is_press and name not in pressed_buttons:
                print(f"[BLE] wake-from-sleep: promoting '{char}' to press+release", flush=True)
                _put_event(char.upper())  # synthetic press
                _deferred_releases.append((time.monotonic() + 0.2, char))
                pressed_buttons.add(name)
                return

            if is_press:
                pressed_buttons.add(name)
            else:
                pressed_buttons.discard(name)

            _put_event(char)
        except Exception as e:
            print(f"[BLE] notification error: {e}", flush=True)

    async def scan_for_remote():
        """Scan by MAC using live detection callback — avoids BlueZ cache issues."""
        for attempt in range(1, 11):
            found_event = asyncio.Event()
            found_device = None
            def on_detected(device, adv_data):
                nonlocal found_device
                if not found_event.is_set() and device.address.upper() == addr.upper():
                    found_device = device
                    found_event.set()
            try:
                async with BleakScanner(detection_callback=on_detected, adapter="hci0"):
                    try:
                        await asyncio.wait_for(found_event.wait(), timeout=10.0)
                    except asyncio.TimeoutError:
                        pass
                return found_device
            except BleakError as e:
                if "InProgress" in str(e) or "NotReady" in str(e):
                    _log(f"BLE: BlueZ busy during scan (attempt {attempt}/10) — resetting")
                    await asyncio.sleep(0.5)
                    _ble_reset_adapter()
                    found_event = asyncio.Event()
                    found_device = None
                else:
                    raise
        return None

    async def run():
        needs_reset = False  # initial reset done before entering loop
        _ble_reset_adapter()

        while _ble_thread_running:
            try:
                if needs_reset:
                    await asyncio.sleep(0.5)  # let BlueZ finish disconnect cleanup
                    _ble_reset_adapter()
                    needs_reset = False

                device = await scan_for_remote()
                if device is None:
                    if _ble_thread_running:
                        await asyncio.sleep(1.0)
                    continue

                disconnect_event = asyncio.Event()
                def on_disconnect(client):
                    disconnect_event.set()

                # Reset per-connection state
                pressed_buttons.clear()
                _deferred_releases.clear()

                async with BleakClient(device,
                                       disconnected_callback=on_disconnect,
                                       timeout=10.0,
                                       adapter="hci0") as client:
                    print("[BLE] connected to EasyPlay Remote", flush=True)
                    _log("BLE: connected to EasyPlay Remote")
                    await client.start_notify(_UART_TX_UUID, notification_handler)
                    print(f"[BLE] subscribed to notifications on {_UART_TX_UUID}", flush=True)

                    # Tell remote we're ready — triggers deferred wake button
                    try:
                        await client.write_gatt_char(_UART_RX_UUID, b"R", response=False)
                        print("[BLE] sent ready signal", flush=True)
                    except Exception as e:
                        print(f"[BLE] ready signal failed (non-fatal): {e}", flush=True)

                    # Stay connected, process deferred releases
                    while not disconnect_event.is_set() and _ble_thread_running:
                        # Fire any deferred synthetic releases
                        now = time.monotonic()
                        due = [r for r in _deferred_releases if now >= r[0]]
                        for entry in due:
                            _put_event(entry[1])  # lowercase release char
                            _deferred_releases.remove(entry)
                        await asyncio.sleep(0.05)

                    if _ble_thread_running:
                        print("[BLE] disconnected — will reconnect", flush=True)
                        _log("BLE: connection dropped — will reconnect")
                        needs_reset = True

            except BleakDeviceNotFoundError:
                await asyncio.sleep(1.0)

            except BleakError as e:
                err = str(e)
                if "InProgress" in err or "NotReady" in err:
                    needs_reset = True
                    await asyncio.sleep(0.5)
                elif any(x in err for x in ("cancelled", "failed to be established",
                                             "le-connection-abort")):
                    await asyncio.sleep(0.5)
                else:
                    _log(f"BLE: error: {e}")
                    await asyncio.sleep(2.0)

            except Exception as e:
                _log(f"BLE: unexpected error: {e}")
                await asyncio.sleep(2.0)

    loop = asyncio.new_event_loop()
    _ble_loop = loop
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run())
    except Exception as e:
        _log(f"BLE thread fatal: {e}")
    finally:
        loop.close()
        _ble_loop = None
        _log("BLE thread exited")

def start_ble_listener():
    global _ble_thread_running, _ble_thread
    if not BLEAK_AVAILABLE: return
    addr, _ = get_bt_remote()
    if not addr: return
    if _ble_thread and _ble_thread.is_alive():
        _ble_thread_running = False
        _ble_thread.join(timeout=5)
    _ble_thread_running = True
    _ble_thread = threading.Thread(target=_ble_uart_thread, args=(addr,), daemon=True)
    _ble_thread.start()
    _log(f"BLE listener started for {addr}")

def stop_ble_listener(wait=False):
    global _ble_thread_running, _ble_loop
    _ble_thread_running = False
    if _ble_loop and not _ble_loop.is_closed():
        try:
            _ble_loop.call_soon_threadsafe(_ble_loop.stop)
        except Exception: pass
    if wait and _ble_thread and _ble_thread.is_alive():
        _ble_thread.join(timeout=6)

# === CEC Brand Profiles ===
TV_BRAND_NAMES = ["auto", "generic", "samsung", "lg", "sony", "philips", "panasonic"]

TV_BRANDS = {
    "generic": {
        "vendor_ids": [],
        "power_on": [("cmd", "on 0")],
        "active_source": [("cmd", "as")],
        "power_off": [("cmd", "standby 0")],
        "inactive_source": [("cmd", "is"), ("tx", "4F:82:00:00")],
        "delay_ms": 500,
    },
    "samsung": {
        "vendor_ids": [0x0000F0],
        "power_on": [("tx", "10:04")],               # Image View On - most reliable for Samsung
        "active_source": [("cmd", "as")],
        "power_off": [("tx", "10:36")],               # Standby (requires Auto Turn Off enabled)
        "inactive_source": [("cmd", "is"), ("tx", "4F:82:00:00")],
        "delay_ms": 300,                              # Samsung drops messages sent too fast
        "handshake": "10:A0:00:00:F0:24:00:80",       # Anynet+ handshake
        "post_wake_reassert": True,                    # Re-assert active source after wake
    },
    "lg": {
        "vendor_ids": [0x00E091],
        "power_on": [("cmd", "on 0")],
        "active_source": [("cmd", "as")],
        "power_off": [("cmd", "standby 0")],
        "inactive_source": [("cmd", "is"), ("tx", "4F:82:00:00")],
        "delay_ms": 500,
    },
    "sony": {
        "vendor_ids": [0x080046],
        "power_on": [("cmd", "on 0")],
        "active_source": [("cmd", "as")],
        "power_off": [("cmd", "standby 0")],
        "inactive_source": [("cmd", "is"), ("tx", "4F:82:00:00")],
        "delay_ms": 500,
    },
    "philips": {
        "vendor_ids": [0x00903E],
        "power_on": [("cmd", "on 0")],
        "active_source": [("cmd", "as")],
        "power_off": [("cmd", "standby 0")],
        "inactive_source": [("cmd", "is"), ("tx", "4F:82:00:00")],
        "delay_ms": 400,
    },
    "panasonic": {
        "vendor_ids": [0x008045],
        "power_on": [("cmd", "on 0")],
        "active_source": [("cmd", "as")],
        "power_off": [("cmd", "standby 0")],
        "inactive_source": [("cmd", "is"), ("tx", "4F:82:00:00")],
        "delay_ms": 500,
    },
}

def get_tv_brand():
    return str(_cfg_get("tv_brand", "auto"))

# === CEC ===
_cec_busy = threading.Event()  # set() while a CEC switch operation is running

def cec_is_busy():
    """Returns True while a CEC switching operation is in progress."""
    return _cec_busy.is_set()

def cec_send(command):
    try:
        if not shutil.which("cec-client"): return False
        subprocess.run(f"echo '{command}' | cec-client -s -d 1", shell=True, timeout=5,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e: _log(f"CEC error: {command}: {e}", e); return False

def cec_raw_send(hex_frame):
    """Send a raw CEC frame, e.g. '10:04'."""
    try:
        if not shutil.which("cec-client"): return False
        subprocess.run(f"echo 'tx {hex_frame}' | cec-client -s -d 1", shell=True, timeout=5,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception as e: _log(f"CEC raw tx error: {hex_frame}: {e}", e); return False

def cec_detect_brand():
    """Query CEC bus for TV vendor ID and return matching brand name, or 'generic'."""
    try:
        if not shutil.which("cec-client"): return "generic"
        result = subprocess.run(
            "echo 'scan' | cec-client -s -d 1",
            shell=True, timeout=10, capture_output=True, text=True)
        output = result.stdout or ""
        for line in output.splitlines():
            if "vendor" not in line.lower(): continue
            match = re.search(r'0x([0-9a-fA-F]{4,6})', line)
            if match:
                vid = int(match.group(1), 16)
                for brand_name, profile in TV_BRANDS.items():
                    if vid in profile.get("vendor_ids", []):
                        _log(f"CEC detected brand: {brand_name} (vendor 0x{vid:06X})")
                        return brand_name
        _log("CEC detect: no vendor match, using generic")
        return "generic"
    except Exception as e:
        _log(f"CEC detect error: {e}", e)
        return "generic"

def _resolve_brand(configured_brand):
    """Given a config value (possibly 'auto'), return a concrete brand key."""
    if configured_brand == "auto":
        cached = _cfg_get("_cec_resolved_brand", None)
        if cached and cached in TV_BRANDS: return cached
        detected = cec_detect_brand()
        _cfg_set("_cec_resolved_brand", detected)
        _log(f"CEC auto-detect resolved to: {detected}")
        return detected
    if configured_brand in TV_BRANDS: return configured_brand
    return "generic"

def _cec_execute_steps(steps):
    """Execute a list of CEC command steps: [('cmd', 'on 0'), ('tx', '10:04'), ...]"""
    for kind, value in steps:
        if kind == "cmd": cec_send(value)
        elif kind == "tx": cec_raw_send(value)

def cec_tv_on_and_select_pi():
    """Turn TV on if off, then set Pi as active HDMI source. Runs in background thread.
    Sends 'as' FIRST for fastest possible switch (works when TV is already on),
    then follows up with handshake + power-on as safety net for cold-boot cases."""
    if not _cfg_get("cec_enabled", True): return
    def _worker():
        _cec_busy.set()
        try:
            brand_key = _resolve_brand(get_tv_brand())
            profile = TV_BRANDS.get(brand_key, TV_BRANDS["generic"])
            delay = profile["delay_ms"] / 1000.0
            # FAST PATH: send Active Source immediately — if TV is already on this
            # is all that's needed and the switch happens in ~1-2 seconds
            cec_send("as")
            # SAFETY NET: handshake + power-on for when TV was fully off
            hs = profile.get("handshake")
            if hs:
                time.sleep(delay)
                cec_raw_send(hs)
            time.sleep(delay)
            _cec_execute_steps(profile["power_on"])
            # Final reassert — ensures Pi is active source even if TV just woke up
            time.sleep(delay)
            cec_send("as")
        except Exception as e:
            _log(f"CEC activate error: {e}", e)
        finally:
            _cec_busy.clear()
    threading.Thread(target=_worker, daemon=True).start()

def _cec_is_still_active():
    """Check if Pi is still the active CEC source (returns True if still active)."""
    try:
        if not shutil.which("cec-client"): return False
        result = subprocess.run(
            "echo 'self' | cec-client -s -d 1",
            shell=True, timeout=5, capture_output=True, text=True)
        out = (result.stdout or "").lower()
        return "active source: yes" in out or "is active source" in out
    except Exception:
        return False

def cec_tv_to_normal():
    """Release TV: try tuner first (if enabled), otherwise standby. Runs in background thread."""
    if not _cfg_get("cec_enabled", True): return
    def _worker():
        _cec_busy.set()
        try:
            brand_key = _resolve_brand(get_tv_brand())
            profile = TV_BRANDS.get(brand_key, TV_BRANDS["generic"])
            delay = profile["delay_ms"] / 1000.0
            has_tuner = _cfg_get("tv_has_tuner", False)

            # Step 1: Release Pi as active source
            cec_send("is")
            time.sleep(delay)

            if has_tuner:
                # Step 2a: Try to switch to internal tuner.
                # v63: use the brand profile's inactive_source steps if defined,
                # because the CEC source-address byte in Set Stream Path
                # (1F:82 vs 4F:82) matters on Samsung Anynet+. Fall back to
                # the generic broadcast if the profile doesn't define it.
                inactive_steps = profile.get("inactive_source",
                                             [("tx", "1F:82:00:00")])
                _cec_execute_steps(inactive_steps)
                time.sleep(1.5)
                # Step 3a: Check if Pi is still active — if so, tuner switch failed
                if _cec_is_still_active():
                    _log("CEC: tuner switch failed, Pi still active — sending standby")
                    time.sleep(delay)
                    _cec_execute_steps(profile.get("power_off", [("cmd", "standby 0")]))
                else:
                    _log("CEC: tuner switch succeeded")
            else:
                # Step 2b: No tuner — go straight to standby (fast path)
                _log("CEC: no tuner configured — sending standby directly")
                _cec_execute_steps(profile.get("power_off", [("cmd", "standby 0")]))
        except Exception as e:
            _log(f"CEC release error: {e}", e)
        finally:
            _cec_busy.clear()
    threading.Thread(target=_worker, daemon=True).start()

def cec_startup():
    """Resolve brand and activate Pi on startup. Runs in background thread.
    At startup we do the full sequence (brand detect first, since we might not have it cached yet)."""
    if not _cfg_get("cec_enabled", True): return
    def _worker():
        try:
            # Resolve brand once at startup (triggers auto-detect if configured as 'auto')
            configured = get_tv_brand()
            if configured == "auto": _resolve_brand(configured)
            # At startup, do the same fast-then-safe sequence
            brand_key = _resolve_brand(get_tv_brand())
            profile = TV_BRANDS.get(brand_key, TV_BRANDS["generic"])
            delay = profile["delay_ms"] / 1000.0
            # Fast path: try switching immediately
            cec_send("as")
            # Safety net: full handshake + power-on
            hs = profile.get("handshake")
            if hs:
                time.sleep(delay)
                cec_raw_send(hs)
            time.sleep(delay)
            _cec_execute_steps(profile["power_on"])
            time.sleep(delay)
            cec_send("as")
        except Exception as e:
            _log(f"CEC startup error: {e}", e)
    threading.Thread(target=_worker, daemon=True).start()

def cec_shutdown():
    """Return TV to tuner on exit. Never powers off the TV.

    v63: run the full tuner-switch sequence (same as On/Off button path)
    synchronously during shutdown so any exit — ESC, Q, watcher kill,
    crash recovery — restores live TV instead of leaving the display
    on a dead HDMI input.
    """
    if not _cfg_get("cec_enabled", True): return
    try:
        brand_key = _resolve_brand(get_tv_brand())
        profile = TV_BRANDS.get(brand_key, TV_BRANDS["generic"])
        delay = profile["delay_ms"] / 1000.0
        has_tuner = _cfg_get("tv_has_tuner", False)

        cec_send("is")
        time.sleep(delay)

        if has_tuner:
            inactive_steps = profile.get("inactive_source",
                                         [("tx", "1F:82:00:00")])
            _cec_execute_steps(inactive_steps)
            time.sleep(1.5)
            if _cec_is_still_active():
                _log("CEC shutdown: tuner switch failed — sending standby")
                time.sleep(delay)
                _cec_execute_steps(profile.get("power_off", [("cmd", "standby 0")]))
            else:
                _log("CEC shutdown: tuner switch succeeded")
    except Exception as e:
        _log(f"CEC shutdown error: {e}", e)

# === Easing & Layout ===
def ease_smooth(t):
    t = max(0.0, min(1.0, t)); return t * t * (3.0 - 2.0 * t)

@dataclass
class WheelLayout:
    slot_xs: list[int]; slot_ys: list[int]; slot_sizes: list[tuple[int, int]]
    center_y: int; start_x: int; total_w: int

def compute_layout(w, h, aspect=FRAME_ASPECT, center_scale=1.063, outer_scale=0.65, height_mult=1.0):
    wheel_h = int(h * 0.40); cy = h // 2
    base_h = int(max(40, wheel_h - 24) * 1.1 * 1.35 * 1.30 * height_mult)
    base_w = max(10, int(base_h * aspect))
    scales = [outer_scale] * CENTER_SLOT + [center_scale] + [outer_scale] * (N_SLOTS - CENTER_SLOT - 1)
    sizes = [(max(8, int(base_w * s)), max(8, int(base_h * s))) for s in scales]
    total = sum(s[0] for s in sizes) + (N_SLOTS - 1) * GAP
    sx = (w - total) // 2; xs = []; cursor = sx
    for i in range(N_SLOTS): xs.append(cursor); cursor += sizes[i][0] + GAP
    ys = [max(0, cy - sizes[i][1] // 2) for i in range(N_SLOTS)]
    return WheelLayout(xs, ys, sizes, cy, sx, total)

# === Surface Cache (pre-render expensive PIL effects ONCE) ===
_glow_cache = {}
_dvd_cache = {}
_dvd_glow_surf_cache = {}  # item_id -> {(fw,fh): glow_surface}

def get_cached_glow(rw, rh, blur_radius=GLOW_BLUR_SPOTLIGHT):
    key = (rw, rh, blur_radius)
    if key in _glow_cache: return _glow_cache[key]
    try:
        tw, th = rw + GLOW_PAD * 2, rh + GLOW_PAD * 2
        alpha = Image.new("L", (tw, th), 0)
        draw = ImageDraw.Draw(alpha)
        draw.rectangle([(GLOW_PAD, GLOW_PAD), (GLOW_PAD + rw - 1, GLOW_PAD + rh - 1)], fill=255)
        blurred = alpha.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        a_arr = np.array(alpha, dtype=np.float32)
        b_arr = np.array(blurred, dtype=np.float32)
        out_arr = np.clip((b_arr - a_arr) * 2.0, 0, 255).astype(np.uint8)
        if out_arr.max() == 0: return None
        img = Image.new("RGBA", (tw, th), (255, 255, 255, 255))
        img.putalpha(Image.fromarray(out_arr, mode="L"))
        surf = pygame.image.frombytes(img.tobytes(), (tw, th), "RGBA").convert_alpha()
        _glow_cache[key] = surf; return surf
    except Exception: return None

def draw_glow_rect(target, rect, intensity=1.0):
    if intensity <= 0.01: return
    glow = get_cached_glow(rect.width, rect.height)
    if glow is None: return
    if intensity < 0.99:
        glow = glow.copy(); glow.set_alpha(int(255 * intensity))
    target.blit(glow, (rect.x - GLOW_PAD, rect.y - GLOW_PAD))

def prerender_dvd_stack(thumb, fw, fh, item_id):
    """Pre-render DVD stack at canonical (fw, fh). Only called once per item at startup."""
    global _dvd_cache
    if item_id not in _dvd_cache: _dvd_cache[item_id] = {}
    cache = _dvd_cache[item_id]
    key = (fw, fh)
    if key in cache: return cache[key]
    try:
        k = min(fw / 2.352, fh / 3.223)
        cw = max(4, int(2 * k)); ch = max(6, int(3 * k))
        cover = pygame.transform.smoothscale(thumb, (cw, ch))
        w, h = cover.get_size()
        pil = Image.frombytes("RGB", (w, h), pygame.image.tobytes(cover, "RGB", False)).convert("RGBA")
        ep = DVD_EDGE_PAD; ow, oh = w + 2 * ep, h + 2 * ep
        # Clean sharp per-card outline (drawn at full res, anti-aliased by PIL)
        ol_layer = Image.new("RGBA", (ow, oh), (0, 0, 0, 0))
        draw = ImageDraw.Draw(ol_layer)
        draw.rectangle([(ep - 1, ep - 1), (ep + w, ep + h)],
                        outline=(0, 0, 0, 200), width=DVD_OUTLINE_W)
        padded = Image.new("RGBA", (ow, oh), (0, 0, 0, 0)); padded.paste(pil, (ep, ep))
        base = Image.alpha_composite(padded, ol_layer)
        sw, sh = 0, 0; frames = []; n = len(DVD_ANGLES)
        for idx, angle in enumerate(DVD_ANGLES):
            brightness = DVD_BRIGHT_MIN + (DVD_BRIGHT_MAX - DVD_BRIGHT_MIN) * (idx / max(1, n - 1))
            arr = np.array(base, dtype=np.float32)
            arr[:, :, :3] = np.clip(arr[:, :, :3] * brightness, 0, 255)
            frame_img = Image.fromarray(arr.astype(np.uint8), mode="RGBA")
            rotated = frame_img.rotate(angle, expand=True, resample=Image.BICUBIC,
                                        fillcolor=(0, 0, 0, 0))
            frames.append(rotated)
            sw = max(sw, rotated.size[0]); sh = max(sh, rotated.size[1])
        pad = DVD_OUTLINE_PAD; tw, th = sw + pad * 2, sh + pad * 2
        canvas = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        ccx, ccy = tw // 2, th // 2
        for fr in frames:
            canvas.paste(fr, (ccx - fr.size[0] // 2, ccy - fr.size[1] // 2), fr)
        # Smooth silhouette outline that follows the stack shape
        alpha_ch = canvas.split()[3]
        a_np = np.array(alpha_ch, dtype=np.float32)
        # Gaussian blur with larger radius for smoother edges
        blurred = alpha_ch.filter(ImageFilter.GaussianBlur(radius=4))
        b_np = np.array(blurred, dtype=np.float32)
        # Soft threshold for smooth anti-aliased edge
        expanded = np.clip(b_np * 4.0, 0, 255)
        original = np.where(a_np > 8, 255.0, a_np * 32.0)
        border_mask = np.clip(expanded - original, 0, 255).astype(np.uint8)
        if border_mask.max() > 0:
            border_img = Image.new("RGBA", (tw, th), (200, 200, 200, 255))
            border_img.putalpha(Image.fromarray(border_mask, mode="L"))
            canvas = Image.alpha_composite(border_img, canvas)
        out = pygame.image.frombytes(canvas.tobytes(), (tw, th), "RGBA").convert_alpha()
        cache[key] = out
        # Pre-render contour-following glow from alpha channel
        try:
            glow_pad = GLOW_PAD
            glow_w, glow_h = tw + glow_pad * 2, th + glow_pad * 2
            # Place alpha in larger canvas for glow bleed room
            alpha_full = Image.new("L", (glow_w, glow_h), 0)
            alpha_full.paste(canvas.split()[3], (glow_pad, glow_pad))
            # Gaussian blur to create glow
            glow_blurred = alpha_full.filter(ImageFilter.GaussianBlur(radius=GLOW_BLUR_SPOTLIGHT))
            g_arr = np.array(glow_blurred, dtype=np.float32)
            a_arr = np.array(alpha_full, dtype=np.float32)
            # Glow = blurred minus original (outer halo only)
            glow_alpha = np.clip((g_arr - a_arr) * 2.0, 0, 255).astype(np.uint8)
            if glow_alpha.max() > 0:
                glow_img = Image.new("RGBA", (glow_w, glow_h), (255, 255, 255, 255))
                glow_img.putalpha(Image.fromarray(glow_alpha, mode="L"))
                glow_surf = pygame.image.frombytes(glow_img.tobytes(), (glow_w, glow_h), "RGBA").convert_alpha()
                if item_id not in _dvd_glow_surf_cache: _dvd_glow_surf_cache[item_id] = {}
                _dvd_glow_surf_cache[item_id][key] = glow_surf
        except Exception as e:
            _log(f"DVD glow render error: {e}", e)
        return out
    except Exception as e:
        _log(f"DVD stack render error: {e}", e); return None

def draw_dvd_stack(target, thumb, rect, item_id, is_center=False, y_offset=0, layout=None, glow_intensity=0.0):
    """Draw DVD stack. Always renders from CENTER canonical size, fast-scales to current rect.
    Uses pygame.transform.scale (NOT smoothscale) for 60fps animation."""
    if layout is None: return
    canon_w, canon_h = layout.slot_sizes[CENTER_SLOT]
    surf = prerender_dvd_stack(thumb, canon_w, canon_h, item_id)
    if surf is None: return
    sw, sh = surf.get_size()
    scale = rect.width / max(1, canon_w)
    draw_w = max(1, int(sw * scale)); draw_h = max(1, int(sh * scale))
    if (draw_w, draw_h) != (sw, sh):
        scaled = pygame.transform.scale(surf, (draw_w, draw_h))
    else:
        scaled = surf
    dw, dh = scaled.get_size()
    dx = rect.x + (rect.width - dw) // 2
    # dy calculated from rect center
    dy = rect.y - int(y_offset) + (rect.height - dh) // 2
    # Draw contour-following glow behind the stack if intensity > 0
    if glow_intensity > 0.01:
        glow_cache = _dvd_glow_surf_cache.get(item_id, {})
        glow_key = (canon_w, canon_h)
        glow_surf = glow_cache.get(glow_key)
        if glow_surf is not None:
            gw, gh = glow_surf.get_size()
            g_draw_w = max(1, int(gw * scale)); g_draw_h = max(1, int(gh * scale))
            if (g_draw_w, g_draw_h) != (gw, gh):
                g_scaled = pygame.transform.scale(glow_surf, (g_draw_w, g_draw_h))
            else:
                g_scaled = glow_surf
            if glow_intensity < 0.99:
                g_scaled = g_scaled.copy(); g_scaled.set_alpha(int(255 * glow_intensity))
            # Glow surface is larger than stack surface by GLOW_PAD on each side
            glow_pad_scaled = int(GLOW_PAD * scale)
            target.blit(g_scaled, (dx - glow_pad_scaled, dy - glow_pad_scaled))
    target.blit(scaled, (dx, dy))

# === VLC vmem frame callbacks ===
# VLC renders into a shared memory buffer instead of directly to X11 window.
# This lets Pygame draw overlays on top of the video frame.

_frame_lock = __import__("threading").Lock()
_frame_buf: ctypes.Array | None = None
_frame_ready = False
_vmem_w, _vmem_h = HD_WIDTH, HD_HEIGHT
_vmem_pitch = _vmem_w * 4  # BGRA = 4 bytes per pixel


@ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
def _vlc_lock(opaque, planes):
    """Called by VLC before it writes a frame."""
    global _frame_buf
    if _frame_buf is None:
        _frame_buf = (ctypes.c_ubyte * (_vmem_w * _vmem_h * 4))()
    planes[0] = ctypes.cast(_frame_buf, ctypes.c_void_p)
    return None


@ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p,
                  ctypes.POINTER(ctypes.c_void_p))
def _vlc_unlock(opaque, picture, planes):
    """Called by VLC after it finishes writing a frame."""
    global _frame_ready
    _frame_ready = True


@ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)
def _vlc_display(opaque, picture):
    """Called by VLC when a frame is ready to display (we handle this ourselves)."""
    pass


# === Drawing Helpers ===
OVERLAY_CORNER_R = 12  # gentle corner radius for overlays

def _rounded_rect_alpha(w, h, color_rgba, radius):
    """Create an SRCALPHA surface with a rounded rect."""
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(surf, color_rgba, (0, 0, w, h), border_radius=radius)
    return surf

def draw_pause_icon(screen, cx, cy, size, icon_font=None):
    try:
        if icon_font is not None:
            # New style: match pill size to the seek arrow icons
            dummy = icon_font.render("\u25B6\u25B6", True, (255, 255, 255))
            pill_w = dummy.get_width() + 40
            pill_h = dummy.get_height() + 20
            bg = _rounded_rect_alpha(pill_w, pill_h, (0, 0, 0, 140), OVERLAY_CORNER_R)
            screen.blit(bg, (cx - pill_w // 2, cy - pill_h // 2))
            bh = int(pill_h * 0.55)
            bw = max(4, int(bh * 0.35))
            gap = max(4, bw)
            r_bar = max(4, bw // 4)
            pygame.draw.rect(screen, (255, 255, 255),
                             (cx - bw - gap // 2, cy - bh // 2, bw, bh), border_radius=r_bar)
            pygame.draw.rect(screen, (255, 255, 255),
                             (cx + gap // 2, cy - bh // 2, bw, bh), border_radius=r_bar)
        else:
            # Legacy fallback
            bw = max(4, size // 6); bh = size; gap = max(4, size // 8); pad = size // 4
            tw = bw * 2 + gap + pad * 2
            bg = pygame.Surface((tw, bh + pad * 2)); bg.fill((0, 0, 0)); bg.set_alpha(140)
            screen.blit(bg, (cx - tw // 2, cy - bh // 2 - pad))
            pygame.draw.rect(screen, (255, 255, 255), (cx - bw - gap // 2, cy - bh // 2, bw, bh))
            pygame.draw.rect(screen, (255, 255, 255), (cx + gap // 2, cy - bh // 2, bw, bh))
    except Exception: pass

_text_cache = {}
def render_outlined_text(text, size=34):
    key = (text, size)
    if key in _text_cache: return _text_cache[key]
    try:
        f = pygame.font.SysFont("Helvetica", size, bold=True)
        ol = f.render(text, True, (0, 0, 0)); tx = f.render(text, True, (255, 255, 255))
        w, h = tx.get_size(); final = pygame.Surface((w + 4, h + 4), pygame.SRCALPHA)
        for dx, dy in [(-1,-1),(-1,1),(1,-1),(1,1),(-2,0),(2,0),(0,-2),(0,2)]:
            final.blit(ol, (2+dx, 2+dy))
        final.blit(tx, (2, 2)); _text_cache[key] = final; return final
    except Exception: return pygame.Surface((1, 1))

def draw_progress_bar(target, x, y, w, ratio):
    """Draw chunky progress bar: 40% transparent grey bg, fully opaque red fill."""
    if ratio <= 0.0 or w < 4: return
    bg = pygame.Surface((w, PROGRESS_BAR_H), pygame.SRCALPHA)
    bg.fill((*PROGRESS_BAR_BG, PROGRESS_BAR_BG_ALPHA))
    target.blit(bg, (x, y))
    fill_w = max(1, int(w * min(1.0, ratio)))
    pygame.draw.rect(target, PROGRESS_BAR_COLOR, (x, y, fill_w, PROGRESS_BAR_H))

def _fmt_time(sec):
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600); m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def draw_seek_overlay(screen, pos_ms, length_ms, seeking_dir, icon_font=None):
    """Draw seek indicator: full-width rounded progress bar + large arrow icon."""
    try:
        sw, sh = screen.get_size()
        pos_s = max(0, pos_ms / 1000.0) if pos_ms and pos_ms >= 0 else 0
        dur_s = max(0, length_ms / 1000.0) if length_ms and length_ms > 0 else 0
        ratio = min(1.0, pos_s / dur_s) if dur_s > 0 else 0

        # ── Progress bar: full width minus 25px each side, 60px tall, 40% opacity ──
        margin = 25
        bar_w = sw - margin * 2
        bar_h = 60
        bar_x = margin
        bar_y = sh - bar_h - 120

        bg = _rounded_rect_alpha(bar_w, bar_h, (128, 128, 128, 102), OVERLAY_CORNER_R)
        screen.blit(bg, (bar_x, bar_y))

        # White progress fill, fully opaque, clipped to rounded shape
        fill_w = max(1, int(bar_w * ratio))
        fill_surf = pygame.Surface((bar_w, bar_h), pygame.SRCALPHA)
        pygame.draw.rect(fill_surf, (255, 255, 255, 255), (0, 0, bar_w, bar_h),
                         border_radius=OVERLAY_CORNER_R)
        if fill_w < bar_w:
            erase = pygame.Surface((bar_w - fill_w, bar_h), pygame.SRCALPHA)
            erase.fill((0, 0, 0, 0))
            fill_surf.blit(erase, (fill_w, 0), special_flags=pygame.BLEND_RGBA_MIN)
        screen.blit(fill_surf, (bar_x, bar_y))

        # ── Arrow icon centered on screen ──
        if icon_font is not None:
            arrow = "\u25B6\u25B6" if seeking_dir > 0 else "\u25C0\u25C0"
            txt_surf = icon_font.render(arrow, True, (255, 255, 255))
            tw, th = txt_surf.get_size()
            pill_w = tw + 40; pill_h = th + 20
            pill = _rounded_rect_alpha(pill_w, pill_h, (0, 0, 0, 140), OVERLAY_CORNER_R)
            screen.blit(pill, (sw // 2 - pill_w // 2, sh // 2 - pill_h // 2))
            screen.blit(txt_surf, (sw // 2 - tw // 2, sh // 2 - th // 2))
    except Exception: pass

FADE_FRAMES = 8       # number of frames for fade transition (~270ms at 30fps)
FADE_FPS = 30

def _fade_to_black(screen):
    """Fade whatever is currently on screen to black over FADE_FRAMES frames."""
    try:
        snapshot = screen.copy()
        w, h = screen.get_size()
        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        clock = pygame.time.Clock()
        for i in range(1, FADE_FRAMES + 1):
            alpha = int(255 * i / FADE_FRAMES)
            screen.blit(snapshot, (0, 0))
            overlay.fill((0, 0, 0, alpha))
            screen.blit(overlay, (0, 0))
            pygame.display.flip()
            clock.tick(FADE_FPS)
        screen.fill((0, 0, 0))
        pygame.display.flip()
    except Exception:
        screen.fill((0, 0, 0))
        pygame.display.flip()


def _fade_from_black(screen, target_surface):
    """Fade from black to a target surface over FADE_FRAMES frames."""
    try:
        w, h = screen.get_size()
        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        clock = pygame.time.Clock()
        for i in range(1, FADE_FRAMES + 1):
            alpha = int(255 * i / FADE_FRAMES)
            screen.fill((0, 0, 0))
            target_surface.set_alpha(alpha)
            screen.blit(target_surface, (0, 0))
            pygame.display.flip()
            clock.tick(FADE_FPS)
        target_surface.set_alpha(None)
    except Exception:
        pass


# === Video Playback ===
def play_video_embedded(screen, path, start_sec, show_seek_overlay=True,
                        confirm_keys=None, back_keys=None):
    """Play video using VLC vmem rendering so Pygame can draw overlays on top."""
    global _frame_ready, _frame_buf
    if not VLC_AVAILABLE or _vlc_mod is None: return False
    path_abs = os.path.abspath(path)
    if not os.path.isfile(path_abs): return False
    try:
        w, h = screen.get_size()
        # Create icon font for overlays
        try: icon_font = pygame.font.SysFont("Helvetica,DejaVu Sans,Liberation Sans", 128, bold=True)
        except: icon_font = None
        # VLC setup with vmem (renders to memory buffer, not to X11 window)
        vlc_args = ["--no-audio-time-stretch", "--avcodec-hw=drm"]
        if sys.platform.startswith("linux"): vlc_args.append("--no-xlib")
        instance = _vlc_mod.Instance(vlc_args)
        media = instance.media_new(path_abs)
        player = instance.media_player_new(); player.set_media(media)
        # vmem: VLC renders frames into our callback buffer at fixed resolution
        player.video_set_format("RV32", _vmem_w, _vmem_h, _vmem_pitch)
        player.video_set_callbacks(_vlc_lock, _vlc_unlock, _vlc_display, None)
        try: player.audio_set_volume(get_volume())
        except: pass
        _frame_ready = False; _frame_buf = None
        pygame.mixer.quit(); _hide_mouse()
        # Mute and start playback; keep screen black until seek completes
        resuming = start_sec > 0
        if resuming:
            try: player.audio_set_volume(0)
            except: pass
        player.play()
        _back = back_keys or (pygame.K_DOWN, pygame.K_ESCAPE)
        _confirm = confirm_keys or (pygame.K_UP, pygame.K_RETURN)
        # Wait for Playing state THEN seek to resume position
        if resuming:
            wait_start = time.monotonic()
            while time.monotonic() - wait_start < 5.0:
                st = player.get_state()
                if st == _vlc_mod.State.Playing:
                    # Discard any frames rendered from the start
                    _frame_ready = False
                    player.set_time(int(start_sec * 1000))
                    # Wait for the seek to land (new frame at correct position)
                    time.sleep(0.15)
                    _frame_ready = False
                    # Restore configured volume
                    try: player.audio_set_volume(get_volume())
                    except: pass
                    break
                elif st in (_vlc_mod.State.Error, _vlc_mod.State.Ended, _vlc_mod.State.Stopped): break
                time.sleep(0.05)
        _hide_mouse()
        clock = pygame.time.Clock(); running = True; keys_held = set()
        last_seek = 0.0; last_save = 0.0; scroll_speed = get_scroll_speed()
        seeking_now = 0
        was_seeking_vol = False
        _mouse_hide_frames = 30
        # Cached scaled video frame — only re-scaled when VLC delivers a new frame,
        # avoiding a full ~8MB memcpy (bytes(_frame_buf)) + rescale on every tick.
        scaled_frame = None
        dst_rect = None  # (x, y, w, h) cached letterbox destination
        # Frame-dirty tracking: only redraw (fill + blit + flip) when a new VLC
        # frame arrived OR the overlay state changed. Skipping idle ticks saves
        # ~500 MB/s of pixel writes when VLC hasn't produced a new frame.
        last_overlay_key = "__init__"  # sentinel, never matches a real key
        needs_initial_clear = True     # force one black clear on entry
        # Adaptive tick rate: query VLC for source fps and tick at that rate so
        # the loop matches content cadence exactly. 24 fps content ticks at 24
        # (cheaper than 30 + no waste); 60 fps content ticks at 60 (no more
        # half-frame dropping as v58 did). Clamped: floor 24 for UI response,
        # ceiling 60 to protect CPU budget for very high-fps content.
        # Resolved lazily — get_fps() returns 0 before VLC has a real track.
        tick_hz = 30  # fallback until we can query VLC
        tick_hz_resolved = False
        # Query native video size for correct aspect ratio letterboxing
        video_aspect = None  # will be resolved once VLC reports it
        try:
            vsz = player.video_get_size()
            if vsz and vsz[0] > 0 and vsz[1] > 0:
                video_aspect = vsz[0] / vsz[1]
        except: pass
        while running:
            now = time.monotonic()
            seeking_now = 0
            if _mouse_hide_frames > 0:
                _hide_mouse(); _mouse_hide_frames -= 1
            # ── Drain BLE remote queue ──
            # Uppercase = key down, lowercase = key up
            try:
                while True:
                    char = _ble_key_queue.get_nowait()
                    char_up = char.upper()
                    if char_up == 'L':     key = SCROLL_LEFT_KEYS[0]
                    elif char_up == 'R':   key = SCROLL_RIGHT_KEYS[0]
                    elif char_up == 'U':   key = _confirm[0]
                    elif char_up == 'D':   key = _back[0]
                    elif char_up == 'O':   key = pygame.K_o
                    else: continue
                    etype = pygame.KEYDOWN if char.isupper() else pygame.KEYUP
                    pygame.event.post(pygame.event.Event(etype, key=key, mod=0, unicode=''))
            except _queue.Empty:
                pass
            for event in pygame.event.get():
                if event.type == pygame.QUIT: running = False
                elif event.type == pygame.MOUSEMOTION:
                    pygame.mouse.set_visible(False)
                elif event.type == pygame.KEYDOWN:
                    keys_held.add(event.key)
                    if event.key in _back: running = False
                    elif event.key in (pygame.K_q, pygame.K_o):
                        running = False
                        # Re-post so the main loop handles quit/standby
                        try: pygame.event.post(event)
                        except: pass
                    elif event.key in _confirm:
                        try:
                            s = player.get_state()
                            if s == _vlc_mod.State.Playing: player.pause()
                            elif s == _vlc_mod.State.Paused: player.play()
                        except: pass
                elif event.type == pygame.KEYUP: keys_held.discard(event.key)
            st = player.get_state()
            if running and st in (_vlc_mod.State.Playing, _vlc_mod.State.Paused):
                if now - last_seek >= SEEK_INTERVAL_S:
                    step_ms = int(scroll_speed * 1000)
                    try:
                        pos_ms = player.get_time()
                        if pos_ms is not None and pos_ms >= 0:
                            if any(k in keys_held for k in SCROLL_RIGHT_KEYS):
                                length = player.get_length()
                                cap = length if length and length > 0 else pos_ms + 999999
                                player.set_time(min(pos_ms + step_ms, cap)); last_seek = now
                                seeking_now = 1
                            elif any(k in keys_held for k in SCROLL_LEFT_KEYS):
                                player.set_time(max(0, pos_ms - step_ms)); last_seek = now
                                seeking_now = -1
                    except: pass
                else:
                    if any(k in keys_held for k in SCROLL_RIGHT_KEYS): seeking_now = 1
                    elif any(k in keys_held for k in SCROLL_LEFT_KEYS): seeking_now = -1
            # Volume: 50% during seeking, configured level otherwise
            if seeking_now != 0 and not was_seeking_vol:
                try: player.audio_set_volume(max(0, get_volume() // 2))
                except: pass
                was_seeking_vol = True
            elif seeking_now == 0 and was_seeking_vol:
                try: player.audio_set_volume(get_volume())
                except: pass
                was_seeking_vol = False
            if running and now - last_save >= PROGRESS_SAVE_INTERVAL:
                try:
                    pos_ms = player.get_time(); length_ms = player.get_length()
                    if pos_ms is not None and pos_ms >= 0:
                        save_progress(path_abs, pos_ms / 1000.0,
                                      (length_ms / 1000.0) if length_ms and length_ms > 0 else 0)
                        last_save = now
                except: pass
            # ── Check for end of media ──
            if st in (_vlc_mod.State.Ended, _vlc_mod.State.Stopped, _vlc_mod.State.Error):
                running = False; continue
            # ── Resolve aspect ratio + letterbox dst rect (lazy, cached) ──
            if video_aspect is None:
                try:
                    vsz = player.video_get_size()
                    if vsz and vsz[0] > 0 and vsz[1] > 0:
                        video_aspect = vsz[0] / vsz[1]
                except: pass
            # ── Resolve source fps for adaptive tick rate (lazy, cached) ──
            if not tick_hz_resolved:
                try:
                    fps = player.get_fps()
                    if fps and fps > 0.1:
                        tick_hz = max(24, min(60, int(round(fps))))
                        tick_hz_resolved = True
                except: pass
            if dst_rect is None and video_aspect is not None:
                screen_aspect = w / h
                if video_aspect > screen_aspect:
                    dst_w = w
                    dst_h = int(w / video_aspect)
                else:
                    dst_h = h
                    dst_w = int(h * video_aspect)
                dst_rect = ((w - dst_w) // 2, (h - dst_h) // 2, dst_w, dst_h)

            # ── On new VLC frame: wrap buffer (no copy) + scale once into cache ──
            # pygame.image.frombuffer supports the ctypes buffer protocol directly,
            # so we skip the ~8MB bytes() copy. scale() then produces an independent
            # surface so subsequent ticks don't touch the VLC buffer at all.
            new_frame_this_tick = False
            if _frame_ready and _frame_buf is not None:
                _frame_ready = False
                try:
                    src = pygame.image.frombuffer(_frame_buf, (_vmem_w, _vmem_h), "BGRA")
                    if dst_rect is not None:
                        scaled_frame = pygame.transform.scale(src, (dst_rect[2], dst_rect[3]))
                    else:
                        scaled_frame = pygame.transform.scale(src, (w, h))
                    new_frame_this_tick = True
                except Exception: pass

            # ── Compute overlay state for frame-dirty comparison ──
            # None when nothing to overlay, "seek" while actively seeking,
            # "paused" while paused without seeking. Transitions between
            # these states force a one-shot redraw.
            if running and seeking_now != 0 and show_seek_overlay:
                overlay_state = "seek"
            elif running and st == _vlc_mod.State.Paused:
                overlay_state = "paused"
            else:
                overlay_state = None

            # ── Decide whether this tick actually needs a full redraw ──
            # Skip fill + blit + overlay + flip entirely when:
            #   - VLC didn't deliver a new frame this tick, AND
            #   - overlay state hasn't changed, AND
            #   - we're not in the animating seek overlay.
            # The double-buffered front surface keeps showing the last flipped
            # frame, which is correct because nothing has actually changed.
            # Saves the ~24 MB/tick of pixel writes (fill 8 MB + blit 8 MB +
            # GPU flip 8 MB) whenever the render loop is ticking faster than
            # the source's real frame rate (24 fps content at 30 tick) or
            # sitting on a paused frame.
            overlay_changed = (overlay_state != last_overlay_key)
            seek_active = (overlay_state == "seek")  # seek overlay animates
            redraw_needed = (new_frame_this_tick or overlay_changed
                             or seek_active or needs_initial_clear)

            if redraw_needed:
                screen.fill((0, 0, 0))
                if scaled_frame is not None:
                    if dst_rect is not None:
                        screen.blit(scaled_frame, (dst_rect[0], dst_rect[1]))
                    else:
                        screen.blit(scaled_frame, (0, 0))
                # ── Draw overlays on top of the video ──
                if overlay_state == "seek":
                    try:
                        cur_pos = player.get_time(); cur_len = player.get_length()
                        draw_seek_overlay(screen, cur_pos, cur_len, seeking_now, icon_font)
                    except: pass
                elif overlay_state == "paused":
                    try:
                        sw, sh_ = screen.get_size()
                        draw_pause_icon(screen, sw // 2, sh_ // 2, 0, icon_font=icon_font)
                    except: pass
                pygame.display.flip()
                last_overlay_key = overlay_state
                needs_initial_clear = False
            # When paused, the frame never changes — drop to 10fps to save CPU.
            # While seeking, keep source fps so the overlay feels responsive.
            if st == _vlc_mod.State.Paused and seeking_now == 0:
                clock.tick(10)
            else:
                clock.tick(tick_hz)
        try:
            pos_ms = player.get_time(); length_ms = player.get_length()
            pos_s = (pos_ms / 1000.0) if pos_ms is not None and pos_ms >= 0 else 0.0
            dur_s = (length_ms / 1000.0) if length_ms is not None and length_ms > 0 else 0.0
            completed = dur_s > 0 and pos_s >= max(0, dur_s - COMPLETION_THRESHOLD)
            save_progress(path_abs, pos_s, dur_s, completed=completed, force=True)
        except: pass
        # Fade last video frame to black before returning to UI
        _fade_to_black(screen)
        player.stop(); del player, media, instance
        _frame_buf = None; _frame_ready = False
        try: pygame.mixer.init()
        except: pass
        _hide_mouse(); return True
    except Exception as e:
        _log(f"Embedded VLC error: {e}", e)
        _frame_buf = None; _frame_ready = False
        try: pygame.mixer.init()
        except: pass
        return False

def play_video_external(path, start_sec):
    path_abs = os.path.abspath(path)
    if not os.path.isfile(path_abs): return False
    cmd = None
    if shutil.which("mpv"):
        cmd = ["mpv", "--fullscreen", "--no-terminal", "--volume=100"]
        if start_sec > 0: cmd.extend(["--start", str(start_sec)])
        cmd.append(path_abs)
    else:
        vlc_path = shutil.which("vlc")
        if vlc_path:
            cmd = [vlc_path, "--fullscreen", "--play-and-exit", "--no-volume-save"]
            if start_sec > 0: cmd.append(f"--start-time={start_sec}")
            cmd.append(path_abs)
    if not cmd: return False
    try:
        subprocess.run(cmd, check=False, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _hide_mouse(); return True
    except Exception as e: _log(f"External player error: {e}", e); return False

def start_playback(path_str, screen, show_seek_overlay=True,
                   confirm_keys=None, back_keys=None):
    _fade_to_black(screen)
    start_sec = get_resume_position(path_str)
    if play_video_embedded(screen, path_str, start_sec, show_seek_overlay=show_seek_overlay,
                           confirm_keys=confirm_keys, back_keys=back_keys): return True, False
    if play_video_external(path_str, start_sec): return True, True
    return False, False

# === Folder Picker ===
def pick_media_folder():
    import tempfile
    out_file = Path(tempfile.gettempdir()) / f"easyplay_folder_{os.getpid()}.txt"
    try: out_file.unlink(missing_ok=True)
    except: pass
    init_dir = str(get_media_folder())
    script = f"""
import sys; from pathlib import Path
try:
    import tkinter as tk; from tkinter import filedialog
    root = tk.Tk(); root.withdraw()
    path = filedialog.askdirectory(title="Select media folder", initialdir={repr(init_dir)})
    root.destroy()
    if path: Path({repr(str(out_file))}).write_text(path, encoding="utf-8")
except: pass
sys.exit(0)
"""
    try:
        subprocess.run([sys.executable, "-c", script], timeout=120,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if out_file.exists():
            p = out_file.read_text(encoding="utf-8").strip(); out_file.unlink(missing_ok=True)
            if p: return Path(p)
    except: pass
    for cmd in [["zenity", "--file-selection", "--directory", f"--filename={init_dir}"],
                ["kdialog", "--getexistingdirectory", init_dir]]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and r.stdout.strip(): return Path(r.stdout.strip())
        except: pass
    return None

# === App State ===
@dataclass
class AppState:
    screen: pygame.Surface; w: int; h: int; font: pygame.font.Font
    items: list[MediaItem]; main_layout: WheelLayout; picker_layout: WheelLayout
    cover_cache: dict = field(default_factory=dict)
    picker_thumbs: dict = field(default_factory=dict)
    selected: int = 0; animating: bool = False; anim_dir: int = 0
    anim_start: float = 0.0; anim_ms: float = ANIM_MS; debounce_until: float = 0.0
    anim_start_xs: list = field(default_factory=list); anim_end_xs: list = field(default_factory=list)
    anim_start_ws: list = field(default_factory=list); anim_end_ws: list = field(default_factory=list)
    anim_start_hs: list = field(default_factory=list); anim_end_hs: list = field(default_factory=list)
    in_picker: bool = False; picker_bg: pygame.Surface | None = None
    picker_videos: list = field(default_factory=list); picker_sel: int = 0
    picker_anim: bool = False; picker_dir: int = 0; picker_anim_start: float = 0.0
    picker_anim_ms: float = ANIM_MS; picker_debounce: float = 0.0; picker_last_load: float = 0.0
    picker_start_xs: list = field(default_factory=list); picker_end_xs: list = field(default_factory=list)
    picker_start_ws: list = field(default_factory=list); picker_end_ws: list = field(default_factory=list)
    picker_start_hs: list = field(default_factory=list); picker_end_hs: list = field(default_factory=list)
    picker_series_title: str = ""  # "Series Name - Season X" displayed in picker
    playing: bool = False; video_last_frame: pygame.Surface | None = None; video_exit_fade: int = 0
    show_setup: bool = False; setup_sel: int = 0
    show_bt_menu: bool = False; bt_devices: list = field(default_factory=list)
    bt_sel: int = 0; bt_msg: str = ""; bt_busy: bool = False
    scroll_speed: int = 10; setup_hold_sec: float = 5.0
    autoscroll: bool = False; volume: int = 100; display_index: int = 0
    seek_overlay: bool = True; cec_enabled: bool = True; tv_brand: str = "auto"; tv_has_tuner: bool = False
    key_map: dict = field(default_factory=dict)
    show_keymap_menu: bool = False; keymap_sel: int = 0; keymap_waiting: bool = False
    keys_held: set = field(default_factory=set); down_pressed_at: float | None = None
    autoscroll_last: float = 0.0; restore_fade: int = 0; last_media_pick: float = 0.0
    running: bool = True; standby: bool = False; cec_pending: str | None = None
    dvd_glow: dict = field(default_factory=dict)  # item_id -> glow intensity 0..1
    setup_last_input: float = 0.0  # monotonic time of last setup/bt/keymap keypress
    # CPU optimization: skip render+flip entirely when nothing has changed.
    # Set True by any event, state transition, or background worker that
    # needs to refresh the screen. Cleared after a render. Animations and
    # fades force a render via their own flags regardless.
    render_dirty: bool = True

def load_cover_thumbs(items, cache, screen):
    cache.clear(); _dvd_cache.clear(); _dvd_glow_surf_cache.clear()
    for item in items:
        if item.cover_path and item.cover_path.exists():
            try:
                key = str(item.cover_path.resolve())
                pil = Image.open(key).convert("RGB")
                surf = pygame.image.frombytes(pil.tobytes(), pil.size, "RGB").convert(screen)
                cache[key] = surf
            except: pass

def load_cover_thumbs_async(items, cache, on_each=None):
    """v61: load cover thumbnails on a background thread.

    The caller renders immediately with placeholders; covers swap in as
    each one finishes loading. on_each(cover_key) is called after each
    successful load so the UI can flag itself dirty for a redraw.

    NOTE: We skip .convert(screen) because it must happen on the main
    (SDL) thread. pygame will convert lazily on first blit. A slight
    per-frame cost until all covers are touched, but no user-visible
    difference on a Pi 5.
    """
    cache.clear(); _dvd_cache.clear(); _dvd_glow_surf_cache.clear()
    def worker():
        for item in items:
            if item.cover_path and item.cover_path.exists():
                try:
                    key = str(item.cover_path.resolve())
                    if key in cache: continue
                    pil = Image.open(key).convert("RGB")
                    surf = pygame.image.frombytes(pil.tobytes(), pil.size, "RGB")
                    cache[key] = surf
                    if on_each:
                        try: on_each(key)
                        except Exception: pass
                except Exception: pass
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t

def placeholder_tile(w, h, seed_int):
    """Calm gradient surface for a cover slot while real thumb is loading.

    Each slot gets a deterministic muted color based on its index, so
    neighboring slots look distinct but nothing shouts for attention.
    Golden-ratio hue distribution spreads colors evenly around the wheel.
    """
    import colorsys
    w = max(1, w); h = max(1, h)
    surf = pygame.Surface((w, h))
    hue = (seed_int * 0.6180339887) % 1.0
    r, g, b = colorsys.hls_to_rgb(hue, 0.14, 0.35)
    top = (int(r * 255), int(g * 255), int(b * 255))
    bot = (max(0, top[0] - 30), max(0, top[1] - 30), max(0, top[2] - 30))
    for y in range(h):
        t = y / max(1, h - 1)
        c = (int(top[0] + (bot[0] - top[0]) * t),
             int(top[1] + (bot[1] - top[1]) * t),
             int(top[2] + (bot[2] - top[2]) * t))
        pygame.draw.line(surf, c, (0, y), (w, y))
    return surf

def load_picker_thumbs(videos, cache, sizes, screen):
    cw, ch = sizes[CENTER_SLOT]
    for vid in videos[:PICKER_THUMBS_MAX]:
        try: key = str(vid.resolve())
        except: key = str(vid)
        if key not in cache:
            seek = 0.5; pos = get_resume_position(key)
            if pos > 0:
                entry = get_progress_entry(key)
                if entry:
                    dur = float(entry.get("duration_sec", 0))
                    if dur > 0: seek = max(0.01, min(0.99, pos / dur))
            thumb = get_video_thumbnail(vid, cw, ch, screen, seek_ratio=seek)
            if thumb: cache[key] = thumb

def generate_all_thumbnails(items, screen, progress_callback=None):
    """Pre-generate cached thumbnails for all videos in series (multi-video) folders only."""
    all_vids = []
    for item in items:
        if item.is_series and item.all_videos:
            for v in item.all_videos: all_vids.append(v)
    total = len(all_vids)
    for i, vid in enumerate(all_vids):
        try: key = str(vid.resolve())
        except: key = str(vid)
        seek = 0.5; pos = get_resume_position(key)
        if pos > 0:
            entry = get_progress_entry(key)
            if entry:
                dur = float(entry.get("duration_sec", 0))
                if dur > 0: seek = min(0.99, max(0.01, pos / dur))
        cache_path = _thumb_cache_path(vid, seek)
        if not (cache_path and cache_path.exists()):
            get_video_thumbnail(vid, THUMB_CACHE_SIZE[0], THUMB_CACHE_SIZE[1], screen, seek_ratio=seek)
        if progress_callback: progress_callback(i + 1, total)

# === Main ===
def main():
    global SCROLL_LEFT_KEYS, SCROLL_RIGHT_KEYS
    _log(f"EasyPlay starting, platform={sys.platform}"); _ensure_progress_file()
    pygame.init()
    km = get_key_map()
    CONFIRM_KEYS, BACK_KEYS, POWER_KEYS = _rebuild_keys_from_map(km)
    _hide_mouse(); _launch_unclutter(); pygame.display.set_caption("EasyPlay")
    disp_idx = get_display_index()
    try:
        if disp_idx >= pygame.display.get_num_displays(): disp_idx = 0
    except: disp_idx = 0
    # v62: hardcode pygame render surface to match the kernel HDMI mode
    # (1920x1080) rather than asking the compositor. Prevents the cramped-
    # corner bug on 4K displays where Wayland reports 4K but HDMI signal
    # is pinned to 1080p. See module docstring for full rationale.
    w, h = HD_WIDTH, HD_HEIGHT
    try: screen = pygame.display.set_mode((w, h), pygame.FULLSCREEN, display=disp_idx)
    except TypeError: screen = pygame.display.set_mode((w, h), pygame.FULLSCREEN)
    _hide_mouse(); cec_startup(); start_ble_listener()
    sl = compute_layout(w, h)
    pl = compute_layout(w, h, aspect=PICKER_FRAME_ASPECT, center_scale=1.063*0.75, height_mult=0.70*0.90)

    # ── Wait for media drive if needed ───────────────────────────────────────
    wait_font = pygame.font.SysFont("Helvetica", 48, bold=True)
    wait_small = pygame.font.SysFont("Helvetica", 28)
    media_ok, media_reason = check_media_folder()
    while not media_ok:
        screen.fill((10, 10, 15))
        # Title
        title_surf = wait_font.render("EasyPlay", True, (102, 126, 234))
        screen.blit(title_surf, (w // 2 - title_surf.get_width() // 2, h // 3 - 60))
        # Reason
        for i, line in enumerate(media_reason.split("\n")):
            reason_surf = wait_small.render(line, True, (200, 200, 200))
            screen.blit(reason_surf, (w // 2 - reason_surf.get_width() // 2, h // 2 - 20 + i * 40))
        # Hint
        dots = "." * (1 + int(time.monotonic() * 2) % 3)
        hint_surf = wait_small.render(f"Waiting for drive{dots}   (press Q or ESC to quit)", True, (100, 100, 100))
        screen.blit(hint_surf, (w // 2 - hint_surf.get_width() // 2, h * 2 // 3))
        pygame.display.flip()
        # Check for quit — keyboard (ESC, Q) or remote On/Off via BLE queue
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                stop_ble_listener(); pygame.quit(); sys.exit(0)
            if event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_q):
                stop_ble_listener(); pygame.quit(); sys.exit(0)
        # Drain BLE key queue so it doesn't pile up while waiting
        try:
            while not _ble_key_queue.empty():
                _ble_key_queue.get_nowait()
        except Exception:
            pass
        time.sleep(0.5)
        media_ok, media_reason = check_media_folder()

    items = scan_media_library()
    if not items:
        items = [MediaItem(name=f"Item {i+1}", cover_path=None, video_path=None,
                           all_videos=[], is_series=False) for i in range(N_SLOTS)]
    font = pygame.font.SysFont("Helvetica", 36, bold=True)
    small_font = pygame.font.SysFont("Helvetica", 18)
    state = AppState(screen=screen, w=w, h=h, font=font, items=items,
        main_layout=sl, picker_layout=pl,
        anim_start_xs=list(sl.slot_xs), anim_end_xs=list(sl.slot_xs),
        anim_start_ws=[sl.slot_sizes[i][0] for i in range(N_SLOTS)],
        anim_end_ws=[sl.slot_sizes[i][0] for i in range(N_SLOTS)],
        anim_start_hs=[sl.slot_sizes[i][1] for i in range(N_SLOTS)],
        anim_end_hs=[sl.slot_sizes[i][1] for i in range(N_SLOTS)],
        picker_start_xs=list(pl.slot_xs), picker_end_xs=list(pl.slot_xs),
        picker_start_ws=[pl.slot_sizes[i][0] for i in range(N_SLOTS)],
        picker_end_ws=[pl.slot_sizes[i][0] for i in range(N_SLOTS)],
        picker_start_hs=[pl.slot_sizes[i][1] for i in range(N_SLOTS)],
        picker_end_hs=[pl.slot_sizes[i][1] for i in range(N_SLOTS)],
        scroll_speed=get_scroll_speed(), setup_hold_sec=get_setup_hold_sec(),
        autoscroll=get_autoscroll(), volume=get_volume(),
        seek_overlay=get_seek_overlay(), cec_enabled=bool(_cfg_get("cec_enabled", True)),
        tv_brand=get_tv_brand(), tv_has_tuner=bool(_cfg_get("tv_has_tuner", False)),
        key_map=km, display_index=disp_idx)
    # v61: async cover loading so the carousel renders immediately.
    # Each cover marks state dirty as it arrives for a refresh.
    def _on_cover_loaded(_key):
        state.render_dirty = True
    load_cover_thumbs_async(items, state.cover_cache, on_each=_on_cover_loaded)
    _log(f"Loading {len(items)} items (covers arrive async)")
    # Pre-warm glow cache for canonical sizes
    get_cached_glow(sl.slot_sizes[CENTER_SLOT][0], sl.slot_sizes[CENTER_SLOT][1])
    get_cached_glow(sl.slot_sizes[0][0], sl.slot_sizes[0][1])
    get_cached_glow(pl.slot_sizes[CENTER_SLOT][0], pl.slot_sizes[CENTER_SLOT][1])
    # Pre-warm DVD stack cache for series items at center canonical size only
    for item in items:
        if item.is_series and item.cover_path:
            cover_key = str(item.cover_path.resolve()) if item.cover_path else None
            if cover_key and cover_key in state.cover_cache:
                thumb = state.cover_cache[cover_key]
                prerender_dvd_stack(thumb, sl.slot_sizes[CENTER_SLOT][0], sl.slot_sizes[CENTER_SLOT][1], id(item))
    clock = pygame.time.Clock(); OFF = 100
    SETUP_OPTS = ["Reload library", "Media folder...", "Video Scrollspeed: {ss}x",
                  "Menu hold: {mh}s", "[{asc}] GUI Autoscroll", "Volume: {vol}%",
                  "[{so}] Seek overlay", "[{cec}] CEC TV control",
                  "TV Brand: {tb}", "[{tun}] TV has tuner",
                  "Create all thumbnails", "Pair remote", "Remap buttons",
                  "Clear watch progress", "Close"]
    N_SETUP = len(SETUP_OPTS)

    def start_main_anim(direction, auto=False):
        if state.animating: return
        now = time.monotonic()
        if now < state.debounce_until: return
        state.debounce_until = now + DEBOUNCE_MS / 1000.0
        state.animating = True; state.anim_dir = direction; state.anim_start = now
        state.anim_ms = ANIM_MS_AUTOSCROLL if auto else ANIM_MS
        xs, sizes = sl.slot_xs, sl.slot_sizes
        state.anim_start_xs[:] = list(xs)
        state.anim_start_ws[:] = [sizes[i][0] for i in range(N_SLOTS)]
        state.anim_start_hs[:] = [sizes[i][1] for i in range(N_SLOTS)]
        if direction > 0:
            state.anim_end_xs[:] = [xs[0] - OFF] + xs[:N_SLOTS - 1]
            state.anim_end_ws[:] = [sizes[0][0]] + [sizes[i][0] for i in range(N_SLOTS - 1)]
            state.anim_end_hs[:] = [sizes[0][1]] + [sizes[i][1] for i in range(N_SLOTS - 1)]
        else:
            state.anim_end_xs[:] = xs[1:] + [xs[-1] + OFF]
            state.anim_end_ws[:] = [sizes[i][0] for i in range(1, N_SLOTS)] + [sizes[-1][0]]
            state.anim_end_hs[:] = [sizes[i][1] for i in range(1, N_SLOTS)] + [sizes[-1][1]]

    def start_picker_anim(direction, auto=False):
        if state.picker_anim: return
        n = len(state.picker_videos)
        if n == 0: return
        if direction > 0 and state.picker_sel >= n - 1: return
        if direction < 0 and state.picker_sel <= 0: return
        now = time.monotonic()
        if now < state.picker_debounce: return
        state.picker_debounce = now + DEBOUNCE_MS / 1000.0
        state.picker_anim = True; state.picker_dir = direction; state.picker_anim_start = now
        state.picker_anim_ms = ANIM_MS_AUTOSCROLL if auto else ANIM_MS
        xs, sizes = pl.slot_xs, pl.slot_sizes
        state.picker_start_xs[:] = list(xs)
        state.picker_start_ws[:] = [sizes[i][0] for i in range(N_SLOTS)]
        state.picker_start_hs[:] = [sizes[i][1] for i in range(N_SLOTS)]
        if direction > 0:
            state.picker_end_xs[:] = [xs[0] - OFF] + xs[:N_SLOTS - 1]
            state.picker_end_ws[:] = [sizes[0][0]] + [sizes[i][0] for i in range(N_SLOTS - 1)]
            state.picker_end_hs[:] = [sizes[0][1]] + [sizes[i][1] for i in range(N_SLOTS - 1)]
        else:
            state.picker_end_xs[:] = xs[1:] + [xs[-1] + OFF]
            state.picker_end_ws[:] = [sizes[i][0] for i in range(1, N_SLOTS)] + [sizes[-1][0]]
            state.picker_end_hs[:] = [sizes[i][1] for i in range(1, N_SLOTS)] + [sizes[-1][1]]

    def enter_picker(item):
        state.in_picker = True; state.picker_videos = list(item.all_videos)
        state.picker_sel = 0; state.picker_anim = False; state.picker_last_load = 0.0
        state.picker_thumbs.clear(); state.picker_bg = None
        state.picker_debounce = time.monotonic() + 0.15
        # Parse series title from folder name
        folder_name = ""
        if item.all_videos:
            folder_name = item.all_videos[0].parent.name
        elif item.cover_path:
            folder_name = item.cover_path.parent.name
        series_name, season = parse_series_title(folder_name)
        state.picker_series_title = f"{series_name} - {season}" if season else series_name
        load_picker_thumbs(state.picker_videos, state.picker_thumbs, pl.slot_sizes, screen)
        if item.cover_path:
            try:
                pil = Image.open(str(item.cover_path)).convert("RGB")
                iw, ih = pil.size
                if iw > 1920 or ih > 1920:
                    r = min(1920/iw, 1920/ih)
                    pil = pil.resize((max(1, int(iw*r)), max(1, int(ih*r))), Image.Resampling.LANCZOS)
                    iw, ih = pil.size
                sc = min(w/iw, h/ih) if iw and ih else 1.0
                nw, nh = max(1, int(iw*sc)), max(1, int(ih*sc))
                surf = pygame.image.frombytes(pil.tobytes(), pil.size, "RGB").convert(screen)
                surf = pygame.transform.scale(surf, (nw, nh))
                state.picker_bg = pygame.Surface((w, h)); state.picker_bg.fill((0, 0, 0))
                state.picker_bg.blit(surf, ((w-nw)//2, (h-nh)//2))
                dark = pygame.Surface((nw, nh)); dark.fill((0, 0, 0)); dark.set_alpha(80)
                state.picker_bg.blit(dark, ((w-nw)//2, (h-nh)//2))
            except: state.picker_bg = None

    def do_setup_action():
        nonlocal screen
        sel = state.setup_sel
        if sel == 0:
            state.items[:] = scan_media_library()
            if not state.items:
                state.items[:] = [MediaItem(name=f"Item {i+1}", cover_path=None,
                    video_path=None, all_videos=[], is_series=False) for i in range(N_SLOTS)]
            state.selected = min(state.selected, max(0, len(state.items) - 1))
            load_cover_thumbs(state.items, state.cover_cache, screen)
        elif sel == 1:
            pygame.mouse.set_visible(True)  # Show cursor for folder dialog
            new_path = pick_media_folder()
            # Restore pygame fullscreen after external dialog destroyed the surface
            try:
                screen = pygame.display.set_mode((w, h), pygame.FULLSCREEN, display=state.display_index)
            except TypeError:
                screen = pygame.display.set_mode((w, h), pygame.FULLSCREEN)
            state.screen = screen; _hide_mouse()
            if new_path and new_path.is_dir():
                _cfg_set("video_folder", str(new_path.resolve()))
                state.items[:] = scan_media_library()
                if not state.items:
                    state.items[:] = [MediaItem(name=f"Item {i+1}", cover_path=None,
                        video_path=None, all_videos=[], is_series=False) for i in range(N_SLOTS)]
                state.selected = 0; load_cover_thumbs(state.items, state.cover_cache, screen)
        elif sel == 4: state.autoscroll = not state.autoscroll; _cfg_set("autoscroll", state.autoscroll)
        elif sel == 5: state.volume = min(100, state.volume + 5); _cfg_set("volume", state.volume)  # confirm = nudge up
        elif sel == 6: state.seek_overlay = not state.seek_overlay; _cfg_set("seek_overlay", state.seek_overlay)
        elif sel == 7:
            state.cec_enabled = not state.cec_enabled; _cfg_set("cec_enabled", state.cec_enabled)
        elif sel == 8:
            # TV Brand: handled by left/right cycling in key handler, confirm also cycles right
            idx = TV_BRAND_NAMES.index(state.tv_brand) if state.tv_brand in TV_BRAND_NAMES else 0
            state.tv_brand = TV_BRAND_NAMES[(idx + 1) % len(TV_BRAND_NAMES)]
            _cfg_set("tv_brand", state.tv_brand); _cfg_set("_cec_resolved_brand", None)
        elif sel == 9:
            state.tv_has_tuner = not state.tv_has_tuner; _cfg_set("tv_has_tuner", state.tv_has_tuner)
        elif sel == 10:
            # Create all thumbnails with progress display
            state.setup_last_input = time.monotonic() + 600  # keep menu alive during generation
            def _thumb_progress(done, total):
                try:
                    screen.fill((0, 0, 0))
                    pf = pygame.font.SysFont("Helvetica", 48, bold=True)
                    msg = pf.render(f"Creating thumbnails: {done}/{total}", True, (255, 255, 255))
                    mw_t, mh_t = msg.get_size()
                    screen.blit(msg, ((w - mw_t) // 2, (h - mh_t) // 2 - 30))
                    bar_pw = int(w * 0.6); bar_ph = 20
                    bx = (w - bar_pw) // 2; by_p = (h + mh_t) // 2
                    pygame.draw.rect(screen, (60, 60, 60), (bx, by_p, bar_pw, bar_ph))
                    if total > 0:
                        fill = max(1, int(bar_pw * done / total))
                        pygame.draw.rect(screen, (80, 200, 80), (bx, by_p, fill, bar_ph))
                    pygame.draw.rect(screen, (150, 150, 150), (bx, by_p, bar_pw, bar_ph), 1)
                    pygame.display.flip()
                except: pass
            generate_all_thumbnails(state.items, screen, progress_callback=_thumb_progress)
            state.setup_last_input = time.monotonic()
        elif sel == 11:
            if sys.platform.startswith("linux") and shutil.which("bluetoothctl"):
                state.show_bt_menu = True; state.bt_devices = []; state.bt_sel = 0; state.bt_msg = ""
                stop_ble_listener(wait=True)  # free up BlueZ for scanning
                addr, name = get_bt_remote()
                if addr: state.bt_msg = f"Saved: {name or addr}"
        elif sel == 12:
            state.show_keymap_menu = True; state.keymap_sel = 0; state.keymap_waiting = False
        elif sel == 13: clear_all_progress()
        elif sel == 14: state.show_setup = False

    # === MAIN LOOP ===
    while state.running:
        now = time.monotonic()
        # ── Drain BLE UART key queue from background thread ──
        # Uppercase = key down, lowercase = key up
        try:
            while True:
                char = _ble_key_queue.get_nowait()
                char_up = char.upper()
                if char_up == 'L':     key = SCROLL_LEFT_KEYS[0]
                elif char_up == 'R':   key = SCROLL_RIGHT_KEYS[0]
                elif char_up == 'U':   key = CONFIRM_KEYS[0]
                elif char_up == 'D':   key = BACK_KEYS[0]
                elif char_up == 'O':   key = pygame.K_o
                else: continue
                etype = pygame.KEYDOWN if char.isupper() else pygame.KEYUP
                pygame.event.post(pygame.event.Event(etype, key=key, mod=0, unicode=''))
        except _queue.Empty:
            pass
        try:
            events = pygame.event.get()
            if events: state.render_dirty = True  # any input → redraw
            for event in events:
                if event.type == pygame.QUIT: state.running = False
                elif event.type == pygame.MOUSEMOTION:
                    pygame.mouse.set_visible(False)
                elif event.type == pygame.KEYDOWN:
                    state.keys_held.add(event.key)
                    # Only block the standby toggle (O key) while CEC is switching
                    # — prevents double-toggle but allows all other navigation
                    if cec_is_busy() and event.key == pygame.K_o:
                        continue
                    # O key: toggle standby on/off
                    if event.key == pygame.K_o:
                        state.standby = not state.standby
                        if state.standby:
                            # FAKE STANDBY: close menus, schedule CEC for after render
                            state.show_setup = False; state.show_bt_menu = False
                            state.show_keymap_menu = False; state.in_picker = False
                            state.cec_pending = "release"
                        else:
                            # WAKE: show UI immediately, schedule CEC for after render
                            state.restore_fade = 8
                            state.cec_pending = "activate"
                    elif state.standby:
                        # In standby only Q (power) works
                        if event.key in POWER_KEYS: state.running = False
                    elif event.key in POWER_KEYS and not state.show_keymap_menu: state.running = False
                    elif state.show_keymap_menu:
                        state.setup_last_input = now
                        if state.keymap_waiting:
                            # User pressed a key to assign to the selected action
                            action = KEY_ACTION_NAMES[state.keymap_sel]
                            state.key_map[action] = [event.key]
                            save_key_map(state.key_map)
                            CONFIRM_KEYS, BACK_KEYS, POWER_KEYS = _rebuild_keys_from_map(state.key_map)
                            state.keymap_waiting = False
                        else:
                            n_km = len(KEY_ACTION_NAMES) + 2  # actions + Reset + Back
                            if event.key == pygame.K_UP: state.keymap_sel = (state.keymap_sel - 1) % n_km
                            elif event.key == pygame.K_DOWN: state.keymap_sel = (state.keymap_sel + 1) % n_km
                            elif event.key in SCROLL_RIGHT_KEYS or event.key == pygame.K_RETURN:
                                if state.keymap_sel < len(KEY_ACTION_NAMES):
                                    state.keymap_waiting = True  # wait for key press
                                elif state.keymap_sel == len(KEY_ACTION_NAMES):
                                    # Reset to defaults
                                    state.key_map = dict(DEFAULT_KEY_MAP)
                                    save_key_map(state.key_map)
                                    CONFIRM_KEYS, BACK_KEYS, POWER_KEYS = _rebuild_keys_from_map(state.key_map)
                                else:
                                    state.show_keymap_menu = False
                            elif event.key in (pygame.K_ESCAPE,) or event.key in SCROLL_LEFT_KEYS:
                                if state.keymap_waiting: state.keymap_waiting = False
                                else: state.show_keymap_menu = False
                    elif state.show_bt_menu:
                        state.setup_last_input = now
                        # While BT operation is running, only allow escape/back
                        if state.bt_busy:
                            if event.key in (pygame.K_ESCAPE,) or event.key in SCROLL_LEFT_KEYS:
                                state.show_bt_menu = False; state.bt_msg = ""; state.bt_busy = False
                                start_ble_listener()
                            # else ignore — don't queue actions during scan/pair
                        else:
                            n_bt = 1 + len(state.bt_devices) + 1
                            if event.key in (pygame.K_ESCAPE,) or event.key in SCROLL_LEFT_KEYS:
                                state.show_bt_menu = False; state.bt_msg = ""
                                start_ble_listener()
                            elif event.key == pygame.K_UP: state.bt_sel = (state.bt_sel - 1) % n_bt
                            elif event.key == pygame.K_DOWN: state.bt_sel = (state.bt_sel + 1) % n_bt
                            elif event.key in SCROLL_RIGHT_KEYS or event.key == pygame.K_RETURN:
                                if state.bt_sel == 0:
                                    state.bt_msg = "Scanning..."; state.bt_busy = True
                                    state.setup_last_input = now + 30
                                    def _bt_scan_worker():
                                        import re
                                        ansi_escape = re.compile(r'\x1b\[[0-9;]*m|\x1b\[[0-9;]*[A-Za-z]|\r')
                                        try:
                                            stop_ble_listener(wait=True)
                                            time.sleep(2)
                                            proc = subprocess.Popen(
                                                ["bluetoothctl"],
                                                stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE,
                                                stderr=subprocess.DEVNULL,
                                                text=True
                                            )
                                            proc.stdin.write("scan on\n")
                                            proc.stdin.flush()
                                            time.sleep(10)
                                            proc.stdin.write("scan off\n")
                                            proc.stdin.write("devices\n")
                                            proc.stdin.write("quit\n")
                                            proc.stdin.flush()
                                            out, _ = proc.communicate(timeout=5)
                                            found = {}
                                            for line in out.splitlines():
                                                # Strip ANSI color codes and control chars
                                                clean = ansi_escape.sub('', line).strip()
                                                parts = clean.split(" ", 2)
                                                if len(parts) == 3 and parts[0] == "Device":
                                                    found[parts[1]] = parts[2]
                                            devs = list(found.items())
                                            saved_addr, saved_name = get_bt_remote()
                                            if saved_addr:
                                                devs = [(a, n) for a, n in devs if a != saved_addr]
                                                devs.insert(0, (saved_addr, f"{saved_name or saved_addr} (saved)"))
                                            state.bt_devices = devs
                                            state.bt_msg = f"Found {len(devs)} device(s)" if devs else "No devices found"
                                        except Exception as e:
                                            state.bt_msg = f"Scan error: {str(e)[:50]}"
                                        finally:
                                            start_ble_listener()
                                            state.bt_busy = False
                                            state.setup_last_input = time.monotonic()
                                            state.render_dirty = True
                                    threading.Thread(target=_bt_scan_worker, daemon=True).start()
                                elif state.bt_devices and 1 <= state.bt_sel <= len(state.bt_devices):
                                    addr, name = state.bt_devices[state.bt_sel - 1]
                                    state.bt_msg = f"Saving {name or addr}..."; state.bt_busy = True
                                    state.setup_last_input = now + 30
                                    def _bt_pair_worker(a=addr, n=name):
                                        try:
                                            # Strip any accumulated (saved) tags before storing
                                            clean_name = n.replace(" (saved)", "").strip() if n else n
                                            save_bt_remote(a, clean_name)
                                            stop_ble_listener(wait=True)
                                            time.sleep(0.5)
                                            start_ble_listener()
                                            state.bt_msg = f"Saved: {clean_name or a}"
                                        except Exception as e:
                                            state.bt_msg = f"Pair error: {str(e)[:50]}"
                                        finally:
                                            state.bt_busy = False
                                            state.setup_last_input = time.monotonic()
                                            state.render_dirty = True
                                    threading.Thread(target=_bt_pair_worker, daemon=True).start()
                                else: state.show_bt_menu = False
                    elif state.show_setup:
                        state.setup_last_input = now
                        if event.key in (pygame.K_ESCAPE,) or event.key in SCROLL_LEFT_KEYS:
                            if state.setup_sel == 2: state.scroll_speed = max(5, state.scroll_speed - 1); _cfg_set("scroll_speed", state.scroll_speed)
                            elif state.setup_sel == 3: state.setup_hold_sec = max(1.0, state.setup_hold_sec - 1.0); _cfg_set("setup_hold_sec", state.setup_hold_sec)
                            elif state.setup_sel == 5: state.volume = max(0, state.volume - 5); _cfg_set("volume", state.volume)
                            elif state.setup_sel == 8:
                                idx = TV_BRAND_NAMES.index(state.tv_brand) if state.tv_brand in TV_BRAND_NAMES else 0
                                state.tv_brand = TV_BRAND_NAMES[(idx - 1) % len(TV_BRAND_NAMES)]
                                _cfg_set("tv_brand", state.tv_brand); _cfg_set("_cec_resolved_brand", None)
                            else: state.show_setup = False
                        elif event.key == pygame.K_UP: state.setup_sel = (state.setup_sel - 1) % N_SETUP
                        elif event.key == pygame.K_DOWN: state.setup_sel = (state.setup_sel + 1) % N_SETUP
                        elif event.key in SCROLL_RIGHT_KEYS or event.key == pygame.K_RETURN:
                            if state.setup_sel == 2: state.scroll_speed = min(30, state.scroll_speed + 1); _cfg_set("scroll_speed", state.scroll_speed)
                            elif state.setup_sel == 3: state.setup_hold_sec = min(15.0, state.setup_hold_sec + 1.0); _cfg_set("setup_hold_sec", state.setup_hold_sec)
                            elif state.setup_sel == 5: state.volume = min(100, state.volume + 5); _cfg_set("volume", state.volume)
                            elif state.setup_sel == 8:
                                idx = TV_BRAND_NAMES.index(state.tv_brand) if state.tv_brand in TV_BRAND_NAMES else 0
                                state.tv_brand = TV_BRAND_NAMES[(idx + 1) % len(TV_BRAND_NAMES)]
                                _cfg_set("tv_brand", state.tv_brand); _cfg_set("_cec_resolved_brand", None)
                            else: do_setup_action()
                    elif state.in_picker:
                        if event.key in BACK_KEYS:
                            state.in_picker = False; state.picker_thumbs.clear()
                        elif event.key in SCROLL_LEFT_KEYS: start_picker_anim(-1)
                        elif event.key in SCROLL_RIGHT_KEYS: start_picker_anim(1)
                        elif event.key in CONFIRM_KEYS:
                            if now >= state.picker_debounce and state.picker_videos:
                                idx = max(0, min(len(state.picker_videos) - 1, state.picker_sel))
                                vid = state.picker_videos[idx]
                                try: path_str = str(vid.resolve())
                                except: path_str = str(vid)
                                started, was_ext = start_playback(path_str, screen, state.seek_overlay,
                                    confirm_keys=CONFIRM_KEYS, back_keys=BACK_KEYS)
                                if started:
                                    state.picker_last_load = 0.0
                                    # Clear this video's thumb so it regenerates at new watched position
                                    state.picker_thumbs.pop(path_str, None)
                                    if was_ext:
                                        try: screen = pygame.display.set_mode((w, h), pygame.FULLSCREEN)
                                        except: pass
                                        state.restore_fade = 8
                                    state.screen = pygame.display.get_surface() or screen
                                    _hide_mouse()
                                    load_picker_thumbs(state.picker_videos, state.picker_thumbs, pl.slot_sizes, screen)
                    elif not state.playing and not state.show_setup:
                        if event.key in SCROLL_LEFT_KEYS: start_main_anim(-1)
                        elif event.key in SCROLL_RIGHT_KEYS: start_main_anim(1)
                        elif event.key in CONFIRM_KEYS:
                            if not state.animating and state.items:
                                idx = state.selected % len(state.items); item = state.items[idx]
                                if item.is_series and item.cover_path: enter_picker(item)
                                elif item.video_path:
                                    try: path_str = str(item.video_path.resolve())
                                    except: path_str = str(item.video_path)
                                    started, was_ext = start_playback(path_str, screen, state.seek_overlay,
                                        confirm_keys=CONFIRM_KEYS, back_keys=BACK_KEYS)
                                    if started:
                                        if was_ext:
                                            try: screen = pygame.display.set_mode((w, h), pygame.FULLSCREEN)
                                            except: pass
                                            state.restore_fade = 8
                                        state.screen = pygame.display.get_surface() or screen; _hide_mouse()
                        elif event.key in BACK_KEYS:
                            if state.down_pressed_at is None: state.down_pressed_at = now
                elif event.type == pygame.KEYUP:
                    state.keys_held.discard(event.key)
                    if event.key in BACK_KEYS: state.down_pressed_at = None
        except Exception as e: _log(f"Event error: {e}", e)

        # Animation updates
        if state.animating:
            t = min(1.0, (now - state.anim_start) * 1000 / state.anim_ms)
            if t >= 1.0:
                state.animating = False
                state.selected = (state.selected + state.anim_dir) % len(state.items)
                state.render_dirty = True  # final frame at rest
        if state.picker_anim:
            t = min(1.0, (now - state.picker_anim_start) * 1000 / state.picker_anim_ms)
            if t >= 1.0:
                state.picker_anim = False
                n = len(state.picker_videos)
                if n > 0: state.picker_sel = max(0, min(n - 1, state.picker_sel + state.picker_dir))
                state.render_dirty = True
        # Setup hold
        if (not state.playing and not state.show_setup and not state.in_picker
                and any(k in state.keys_held for k in BACK_KEYS) and state.down_pressed_at is not None):
            if now - state.down_pressed_at >= state.setup_hold_sec:
                state.show_setup = True; state.down_pressed_at = None; state.setup_sel = 0
                state.setup_last_input = now
                state.render_dirty = True
        # Autoscroll
        # Auto-close settings menu after timeout (2 min for BT pairing, 30s otherwise)
        if (state.show_setup or state.show_bt_menu or state.show_keymap_menu):
            timeout = 120.0 if state.show_bt_menu else 30.0
            if state.setup_last_input > 0 and now - state.setup_last_input > timeout and not state.bt_busy:
                state.show_setup = False; state.show_bt_menu = False
                state.show_keymap_menu = False; state.keymap_waiting = False
                state.render_dirty = True

        if state.autoscroll and not state.standby and not state.playing and not state.show_setup and not state.show_bt_menu and not state.show_keymap_menu:
            interval = ANIM_MS_AUTOSCROLL / 1000.0
            if state.in_picker:
                if not state.picker_anim and now - state.autoscroll_last >= interval:
                    if any(k in state.keys_held for k in SCROLL_RIGHT_KEYS):
                        start_picker_anim(1, auto=True); state.autoscroll_last = now
                    elif any(k in state.keys_held for k in SCROLL_LEFT_KEYS):
                        start_picker_anim(-1, auto=True); state.autoscroll_last = now
            else:
                if not state.animating and now - state.autoscroll_last >= interval:
                    if any(k in state.keys_held for k in SCROLL_RIGHT_KEYS):
                        start_main_anim(1, auto=True); state.autoscroll_last = now
                    elif any(k in state.keys_held for k in SCROLL_LEFT_KEYS):
                        start_main_anim(-1, auto=True); state.autoscroll_last = now

        # === RENDERING ===
        # Skip the entire render+flip when nothing has changed. This is the
        # single biggest idle-CPU win: on a static carousel at 10fps we'd
        # otherwise redraw every DVD stack, glow, progress bar and label
        # every tick for no reason. We render whenever:
        #   - render_dirty (any input event or state transition)
        #   - any animation or fade is in progress
        #   - bt_busy (scanning dots animation)
        # Menus do not render continuously: they redraw on keypress.
        render_needed = (
            state.render_dirty
            or state.animating
            or state.picker_anim
            or state.video_exit_fade > 0
            or state.restore_fade > 0
            or state.bt_busy
        )
        if not render_needed:
            # Nothing to draw — just tick the clock and loop
            clock.tick(10)
            continue
        try:
            if state.standby:
                screen.fill((30, 30, 30))  # Dark grey screen in standby
                try:
                    sf = pygame.font.SysFont("Helvetica", 72, bold=False)
                    msg = sf.render("Use the remote to turn on the media player", True, (180, 180, 180))
                    mw_s, mh_s = msg.get_size()
                    screen.blit(msg, ((w - mw_s) // 2, (h - mh_s) // 2))
                except: pass
                pygame.mouse.set_visible(False)
                pygame.display.flip()
                state.render_dirty = False  # standby is static — one render is enough
                # Run deferred CEC AFTER standby screen is visible
                if state.cec_pending == "release":
                    state.cec_pending = None
                    cec_tv_to_normal()
                clock.tick(10)  # Low FPS in standby to save resources
                continue

            if state.video_exit_fade > 0 and state.video_last_frame:
                screen.blit(state.video_last_frame, (0, 0))
            else: screen.fill((0, 0, 0))

            if state.in_picker:
                target = screen
                if state.video_exit_fade > 0 and state.video_last_frame:
                    target = pygame.Surface((w, h))
                if state.picker_bg: target.blit(state.picker_bg, (0, 0))
                else: target.fill((20, 20, 20))
                n_p = len(state.picker_videos)
                if state.picker_anim:
                    el = (now - state.picker_anim_start) * 1000
                    pt = min(1.0, el / state.picker_anim_ms); pe = ease_smooth(pt); p_glow_t = pt * pt
                else: pe = 1.0; p_glow_t = 1.0
                for i in range(N_SLOTS):
                    vid_idx = state.picker_sel - CENTER_SLOT + i
                    if vid_idx < 0 or vid_idx >= n_p: continue
                    try:
                        if state.picker_anim:
                            x = int(state.picker_start_xs[i] + (state.picker_end_xs[i] - state.picker_start_xs[i]) * pe)
                            fw = max(1, int(state.picker_start_ws[i] + (state.picker_end_ws[i] - state.picker_start_ws[i]) * pe))
                            fh = max(1, int(state.picker_start_hs[i] + (state.picker_end_hs[i] - state.picker_start_hs[i]) * pe))
                            y = pl.center_y - fh // 2
                        else:
                            x, y = pl.slot_xs[i], pl.slot_ys[i]; fw, fh = pl.slot_sizes[i]
                        rect = pygame.Rect(x, y, fw, fh)
                        pygame.draw.rect(target, (34, 34, 34), rect)
                        vid = state.picker_videos[vid_idx]
                        key = str(vid.resolve()) if hasattr(vid, 'resolve') else str(vid)
                        if key in state.picker_thumbs and fw > 1 and fh > 1:
                            target.blit(pygame.transform.scale(state.picker_thumbs[key], (fw, fh)), (x, y))
                        if get_progress_completed(key):
                            ov = pygame.Surface((fw, fh)); ov.fill((80,80,80)); ov.set_alpha(160); target.blit(ov, (x, y))
                        label = parse_episode_label(vid.name)[:28]
                        if len(parse_episode_label(vid.name)) > 28: label += "..."
                        txt = render_outlined_text(label)
                        bar_h = max(38, font.get_height() + 12)
                        pygame.draw.rect(target, (0,0,0), (rect.left, rect.bottom - bar_h, rect.width, bar_h))
                        # Progress bar centered vertically on the image area (above label bar)
                        ratio = get_progress_ratio(key)
                        if ratio > 0:
                            img_mid_y = rect.top + (rect.height - bar_h) // 2 - PROGRESS_BAR_H // 2
                            draw_progress_bar(target, rect.left, img_mid_y, fw, ratio)
                        target.set_clip(rect)
                        target.blit(txt, txt.get_rect(centerx=rect.centerx, bottom=rect.bottom - 4))
                        target.set_clip(None)
                        if i == CENTER_SLOT:
                            draw_glow_rect(target, rect, p_glow_t)
                        else: pygame.draw.rect(target, (200, 200, 200), rect, 2)
                    except: pass
                # Series title at 25% from bottom
                if state.picker_series_title:
                    title_y = int(h * 0.75)
                    st_surf = render_outlined_text(state.picker_series_title, size=38)
                    stw = st_surf.get_width()
                    target.blit(st_surf, ((w - stw) // 2, title_y))
                if state.video_exit_fade > 0 and target is not screen:
                    alpha = int(255 * (1 - state.video_exit_fade / 8))
                    target.set_alpha(alpha); screen.blit(target, (0, 0))
                    state.video_exit_fade -= 1
                    if state.video_exit_fade <= 0: state.video_last_frame = None

            elif not state.playing:
                n = len(state.items)
                if state.animating:
                    el = (now - state.anim_start) * 1000
                    t = min(1.0, el / state.anim_ms); ae = ease_smooth(t); anim_glow = t * t
                else:
                    ae = 1.0
                    anim_glow = 1.0
                for i in range(N_SLOTS):
                    if state.animating:
                        x = int(state.anim_start_xs[i] + (state.anim_end_xs[i] - state.anim_start_xs[i]) * ae)
                        fw = max(1, int(state.anim_start_ws[i] + (state.anim_end_ws[i] - state.anim_start_ws[i]) * ae))
                        fh = max(1, int(state.anim_start_hs[i] + (state.anim_end_hs[i] - state.anim_start_hs[i]) * ae))
                        y = sl.center_y - fh // 2
                    else:
                        x, y = sl.slot_xs[i], sl.slot_ys[i]; fw, fh = sl.slot_sizes[i]
                    rect = pygame.Rect(x, y, fw, fh)
                    idx = (state.selected - CENTER_SLOT + i) % n; item = state.items[idx]
                    cover_key = str(item.cover_path.resolve()) if item.cover_path else None
                    is_series = item.is_series
                    if not is_series: pygame.draw.rect(screen, (34, 34, 34), rect)
                    # v61: gradient placeholder while real cover is loading
                    if not is_series and cover_key and cover_key not in state.cover_cache and fw > 1 and fh > 1:
                        screen.blit(placeholder_tile(fw, fh, idx), (x, y))
                    if cover_key and cover_key in state.cover_cache and fw > 1 and fh > 1:
                        thumb = state.cover_cache[cover_key]
                        if is_series:
                            is_ctr = (i == CENTER_SLOT)
                            if state.animating:
                                ctr_end = CENTER_SLOT + state.anim_dir
                                off_s = DVD_CENTER_Y_RAISE if is_ctr else DVD_NON_CENTER_Y_OFFSET
                                off_e = DVD_CENTER_Y_RAISE if (i == ctr_end) else DVD_NON_CENTER_Y_OFFSET
                                y_off = off_s + (off_e - off_s) * ae
                            else: y_off = DVD_CENTER_Y_RAISE if is_ctr else DVD_NON_CENTER_Y_OFFSET
                            # Manage DVD glow: fade in when center, fade out in 5 frames when leaving
                            iid = id(item)
                            if is_ctr:
                                state.dvd_glow[iid] = anim_glow
                            else:
                                cur_g = state.dvd_glow.get(iid, 0.0)
                                if cur_g > 0:
                                    state.dvd_glow[iid] = max(0.0, cur_g - 0.2)
                            draw_dvd_stack(screen, thumb, rect, item_id=iid, is_center=is_ctr,
                                           y_offset=y_off, layout=sl,
                                           glow_intensity=state.dvd_glow.get(iid, 0.0))
                        else:
                            screen.blit(pygame.transform.scale(thumb, (fw, fh)), (x, y))
                        if item.video_path and not is_series:
                            path_str = str(item.video_path.resolve())
                            if get_progress_completed(path_str):
                                ov = pygame.Surface((fw, fh)); ov.fill((80,80,80)); ov.set_alpha(160); screen.blit(ov, (x, y))
                    if i == CENTER_SLOT:
                        if not is_series: draw_glow_rect(screen, rect, anim_glow)
                    else:
                        if not is_series: pygame.draw.rect(screen, (200, 200, 200), rect, 2)
                    bar_h = max(40, font.get_height() + 14)
                    pygame.draw.rect(screen, (0,0,0), (rect.left, rect.bottom - bar_h, rect.width, bar_h))
                    # Progress bar centered vertically on the image area (above label bar) - NOT for series
                    if item.video_path and not is_series:
                        path_str = str(item.video_path.resolve())
                        ratio = get_progress_ratio(path_str)
                        if ratio > 0:
                            img_mid_y = rect.top + (rect.height - bar_h) // 2 - PROGRESS_BAR_H // 2
                            draw_progress_bar(screen, rect.left, img_mid_y, fw, ratio)
                    txt = render_outlined_text(item.name)
                    screen.set_clip(rect)
                    screen.blit(txt, txt.get_rect(centerx=rect.centerx, bottom=rect.bottom - 4))
                    screen.set_clip(None)

            # Setup overlay (strict 4-button: L/R navigate, UP confirm, DOWN exit)
            if state.show_setup:
                ov = pygame.Surface((w, h)); ov.fill((0,0,0)); ov.set_alpha(200); screen.blit(ov, (0,0))
                mw, mh = 460, 934; mx = (w - mw) // 2; my = (h - mh) // 2
                pygame.draw.rect(screen, (50, 50, 50), (mx, my, mw, mh))
                pygame.draw.rect(screen, (120, 120, 120), (mx, my, mw, mh), 2)
                screen.blit(font.render("Setup", True, (255, 255, 255)), (mx + 20, my + 15))
                line_h = 48
                for i in range(N_SETUP):
                    text = SETUP_OPTS[i].format(ss=state.scroll_speed, mh=int(state.setup_hold_sec),
                                                asc="x" if state.autoscroll else " ",
                                                vol=state.volume,
                                                so="x" if state.seek_overlay else " ",
                                                cec="x" if state.cec_enabled else " ",
                                                tb=state.tv_brand.title(),
                                                tun="x" if state.tv_has_tuner else " ")
                    col = (255, 255, 100) if i == state.setup_sel else (220, 220, 220)
                    screen.blit(font.render(text, True, col), (mx + 30, my + 50 + i * line_h))
                bar_w, bar_hs = 280, 12
                if state.setup_sel == 2:
                    bx, by = mx + 30, my + 50 + 2 * line_h + 30
                    pygame.draw.rect(screen, (60,60,60), (bx, by, bar_w, bar_hs))
                    pygame.draw.rect(screen, (80,160,80), (bx, by, int(bar_w * (state.scroll_speed - 5) / 25), bar_hs))
                    pygame.draw.rect(screen, (150,150,150), (bx, by, bar_w, bar_hs), 1)
                if state.setup_sel == 3:
                    bx, by = mx + 30, my + 50 + 3 * line_h + 30
                    pygame.draw.rect(screen, (60,60,60), (bx, by, bar_w, bar_hs))
                    pygame.draw.rect(screen, (80,120,160), (bx, by, int(bar_w * (state.setup_hold_sec - 1) / 14), bar_hs))
                    pygame.draw.rect(screen, (150,150,150), (bx, by, bar_w, bar_hs), 1)
                if state.setup_sel == 5:
                    bx, by = mx + 30, my + 50 + 5 * line_h + 30
                    pygame.draw.rect(screen, (60,60,60), (bx, by, bar_w, bar_hs))
                    pygame.draw.rect(screen, (200,120,60), (bx, by, int(bar_w * state.volume / 100), bar_hs))
                    pygame.draw.rect(screen, (150,150,150), (bx, by, bar_w, bar_hs), 1)
                screen.blit(small_font.render("Up/Down: navigate  Right: select  Left: back",
                                              True, (150, 150, 150)), (mx + 20, my + mh - 28))

            if state.show_bt_menu:
                ov = pygame.Surface((w, h)); ov.fill((0,0,0)); ov.set_alpha(220); screen.blit(ov, (0,0))
                bt_lh = 42  # taller rows for name + address
                # Build display list: each entry is a display string
                bt_display = []
                for addr, nm in state.bt_devices:
                    bt_display.append(nm if nm else addr)
                bt_opts = ["Scan for remotes"] + bt_display + ["Back"]
                bw_m = 580; bh_m = max(400, 100 + len(bt_opts) * bt_lh + 60)
                bx = (w - bw_m) // 2; by = (h - bh_m) // 2
                pygame.draw.rect(screen, (50, 50, 50), (bx, by, bw_m, bh_m))
                pygame.draw.rect(screen, (120, 120, 120), (bx, by, bw_m, bh_m), 2)
                screen.blit(font.render("Pair remote", True, (255, 255, 255)), (bx + 20, by + 15))
                # Status message below title
                msg_y = by + 55
                if state.bt_msg:
                    display_msg = state.bt_msg
                    if state.bt_busy:
                        dots = "." * (1 + int(time.monotonic() * 2) % 3)
                        display_msg = state.bt_msg.rstrip(".") + dots
                    if state.bt_msg.startswith("Paired:") or state.bt_msg.startswith("Saved:"):
                        mc = (100, 255, 100)
                    elif state.bt_busy:
                        mc = (100, 200, 255)
                    else:
                        mc = (255, 200, 100)
                    screen.blit(font.render(display_msg[:50], True, mc), (bx + 20, msg_y))
                list_top = msg_y + 44
                if state.bt_busy:
                    # Dim the device list while busy, show hint to press back
                    for i, text in enumerate(bt_opts):
                        col = (100, 100, 100)  # dimmed
                        screen.blit(font.render(text[:50], True, col), (bx + 30, list_top + i * bt_lh))
                    screen.blit(small_font.render("Left: cancel",
                                                  True, (150, 150, 150)), (bx + 20, by + bh_m - 28))
                else:
                    for i, text in enumerate(bt_opts):
                        is_device = (1 <= i <= len(state.bt_devices))
                        y_pos = list_top + i * bt_lh
                        if is_device:
                            dev_idx = i - 1
                            addr, nm = state.bt_devices[dev_idx]
                            # Name in large font (or address if no name)
                            name_col = (255, 255, 100) if i == state.bt_sel else (220, 220, 220)
                            screen.blit(font.render((nm or addr)[:40], True, name_col), (bx + 30, y_pos))
                            # Address in small font underneath
                            addr_col = (180, 180, 100) if i == state.bt_sel else (140, 140, 140)
                            screen.blit(small_font.render(addr, True, addr_col), (bx + 32, y_pos + 22))
                        else:
                            col = (255, 255, 100) if i == state.bt_sel else (220, 220, 220)
                            screen.blit(font.render(text[:50], True, col), (bx + 30, y_pos))
                    screen.blit(small_font.render("Up/Down: navigate  Right: select  Left: back",
                                                  True, (150, 150, 150)), (bx + 20, by + bh_m - 28))

            if state.show_keymap_menu:
                ov = pygame.Surface((w, h)); ov.fill((0,0,0)); ov.set_alpha(220); screen.blit(ov, (0,0))
                km_w, km_h = 520, 420; kx = (w - km_w) // 2; ky = (h - km_h) // 2
                pygame.draw.rect(screen, (50, 50, 50), (kx, ky, km_w, km_h))
                pygame.draw.rect(screen, (120, 120, 120), (kx, ky, km_w, km_h), 2)
                screen.blit(font.render("Remap Buttons", True, (255, 255, 255)), (kx + 20, ky + 15))
                km_lh = 40
                km_items = []
                for act in KEY_ACTION_NAMES:
                    label = KEY_ACTION_LABELS[act]
                    keys = state.key_map.get(act, DEFAULT_KEY_MAP[act])
                    knames = ", ".join(key_name_safe(k) for k in keys)
                    km_items.append(f"{label}: [{knames}]")
                km_items.append("Reset to defaults")
                km_items.append("Back")
                for i, text in enumerate(km_items):
                    if state.keymap_waiting and i == state.keymap_sel:
                        col = (100, 255, 100)
                        text = KEY_ACTION_LABELS[KEY_ACTION_NAMES[i]] + ": Press a key..."
                    elif i == state.keymap_sel: col = (255, 255, 100)
                    else: col = (220, 220, 220)
                    screen.blit(small_font.render(text[:60], True, col), (kx + 30, ky + 55 + i * km_lh))
                hint = "Press key to assign" if state.keymap_waiting else "Up/Down: navigate  Right: assign  Left: back"
                screen.blit(small_font.render(hint, True, (150, 150, 150)), (kx + 20, ky + km_h - 28))

            if state.restore_fade > 0:
                ov = pygame.Surface((w, h)); ov.fill((0,0,0))
                ov.set_alpha(int(255 * state.restore_fade / 8)); screen.blit(ov, (0,0))
                state.restore_fade -= 1

            pygame.mouse.set_visible(False)
            pygame.display.flip()
            state.render_dirty = False  # consumed
            # Run deferred CEC AFTER UI is visible on screen
            if state.cec_pending == "activate":
                state.cec_pending = None
                cec_tv_on_and_select_pi()
        except Exception as e: _log(f"Render error: {e}", e)
        # Run at 60fps during animation/transitions, drop to 10fps when idle to save CPU
        if state.animating or state.picker_anim or state.video_exit_fade > 0:
            clock.tick(60)
        else:
            clock.tick(10)

    _ble_thread_running = False  # signal BLE thread to stop — daemon, dies with process
    try: cec_shutdown()
    except Exception: pass
    try: pygame.quit()
    except Exception: pass
    _log("EasyPlay shutdown complete")

def _excepthook(typ, val, tb):
    import traceback; traceback.print_exception(typ, val, tb)
    try:
        with open(CRASH_LOG, "a") as f:
            f.write(f"\n--- Python crash {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            traceback.print_exception(typ, val, tb, file=f)
    except: pass
    sys.__excepthook__(typ, val, tb)

if __name__ == "__main__":
    sys.excepthook = _excepthook
    try: main()
    except Exception as e:
        import traceback; traceback.print_exc(); _log(f"Fatal: {e}", e); sys.exit(1)
    sys.exit(0)
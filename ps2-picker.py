#!/usr/bin/env python3
"""PS2 Picker — A controller-driven PS2 game launcher.

Features:
    - User profiles with independent memory card save management
    - Automatic game extraction from ZIP/7z archives with LRU cache
    - Customizable theme colors (presets + per-channel HSV editing)
    - Remappable controller buttons with safety fallbacks
    - Resolution-aware UI scaling (reference: 480p)
    - Procedural sound effects (no external audio files needed)
    - First-time setup wizard with auto-detection of RetroArch/cores
    - On-screen keyboard and file browser for headless/handheld use

Config:  ~/.ps2-picker/config.json   (global settings, theme, button map)
Users:   ~/ps2-users/<name>/          (per-profile cards + meta)
Cache:   ~/ps2-cache/                 (extracted game files + manifest)

Usage:
    python3 ps2-picker.py              Launch normally
    python3 ps2-picker.py --check-deps Run dependency checker first
"""

VERSION = '0.1.22'

# ─── Update Channel ─────────────────────────────────────────────
# 0 = stable (pulls from main branch)
# 1 = testing (pulls from testing branch)
UPDATE_CHANNEL = 0

# ─── Standard Library Imports ───────────────────────────────────
import os, sys, subprocess, glob, shutil, time, json, warnings, struct, math, platform, zipfile, datetime, unicodedata
import re
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# Suppress pygame welcome banner and warnings before import
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
IS_WINDOWS = platform.system() == 'Windows'
if not IS_WINDOWS:
    # Ensure X11 display is set for headless Linux (e.g. Moonlight streaming)
    os.environ.setdefault('DISPLAY', ':0')
warnings.filterwarnings('ignore')

import pygame

# ═══ Application Paths ══════════════════════════════════════════
# All user data lives under these directories. APP_DIR stores global
# config (theme, button map, settings). USERS_DIR holds per-profile
# memory card snapshots and metadata.
APP_DIR = os.path.expanduser("~/.ps2-picker")
GLOBAL_CONFIG_PATH = os.path.join(APP_DIR, "config.json")
USERS_DIR = os.path.expanduser("~/ps2-users")

# Recognised ROM archive extensions (browsed in the file picker)
EXTS = ('.zip', '.7z', '.iso', '.chd')
# Playable disc image extensions (found inside extracted archives)
GAME_EXTS = ('iso', 'bin', 'chd', 'cue')
# PCSX2 memory card filenames (slot 1 and slot 2)
MEMCARD_FILES = ('Mcd001.ps2', 'Mcd002.ps2')

# ═══ Default Configuration ══════════════════════════════════════
# These defaults are merged with any saved config on load, so new
# keys added in future versions automatically get sensible values.
DEFAULT_CONFIG = {
    "rom_dir": "",                                      # Path to PS2 ROM archives
    "local_cache_dir": os.path.expanduser("~/ps2-cache"),# Extracted game cache directory
    "max_cached_games": 3,                               # LRU cache size limit
    "core_path": "",                                     # RetroArch PS2 core (.so/.dll)
    "volume": 0.5,                                       # UI sound volume (0.0–1.0)
    "muted": False,                                      # Global mute toggle
    "setup_complete": False,                              # First-time wizard completed?
}

# ═══ Configuration System ══════════════════════════════════════
# active_cfg is the runtime config dict — a merge of global config
# and any per-user overrides. It's the single source of truth for
# all settings while the app is running.
active_cfg = dict(DEFAULT_CONFIG)
games = []  # Current game list (sorted: cached first, then alpha)


def load_global_config():
    """Load global config, merging with defaults for any missing keys."""
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(GLOBAL_CONFIG_PATH):
        try:
            with open(GLOBAL_CONFIG_PATH) as f:
                saved = json.load(f)
            cfg.update(saved)
        except Exception:
            pass
    return cfg


def save_global_config(cfg):
    """Save global config to disk."""
    os.makedirs(APP_DIR, exist_ok=True)
    with open(GLOBAL_CONFIG_PATH, 'w') as f:
        json.dump(cfg, f, indent=2)


def load_user_settings(username):
    """Load per-user settings overrides from user meta."""
    meta = get_user_meta(username)
    return meta.get("settings", {})


def save_user_settings(username, settings):
    """Save per-user settings overrides to user meta."""
    meta = get_user_meta(username)
    meta["settings"] = settings
    save_user_meta(username, meta)


def apply_user_settings(username):
    """Merge global config with user overrides into active_cfg."""
    global active_cfg
    active_cfg = load_global_config()
    user_settings = load_user_settings(username)
    active_cfg.update(user_settings)
    apply_volume()
    reload_games()


def apply_volume():
    """Apply current volume/mute to all SFX objects."""
    vol = 0.0 if active_cfg.get("muted", False) else active_cfg.get("volume", 0.5)
    for s in SFX.values():
        s.set_volume(vol)


def needs_first_time_setup():
    """Check if first-time setup is needed."""
    cfg = load_global_config()
    if not cfg.get("setup_complete", False):
        return True
    return False


def reload_games():
    """Refresh the game list from active config rom_dir. Cached games sort to top.
    Also purges stale manifest entries whose cache dirs no longer exist."""
    global games
    # Purge stale cache entries first
    manifest = load_cache_manifest()
    stale = [k for k, v in manifest.items()
             if not os.path.isdir(v.get("path", ""))]
    if stale:
        for k in stale:
            del manifest[k]
        save_cache_manifest(manifest)

    rom_dir = active_cfg.get("rom_dir", "")
    if rom_dir and os.path.isdir(rom_dir):
        all_games = [f for f in os.listdir(rom_dir) if f.lower().endswith(EXTS)]
        cached_keys = set(manifest.keys())
        games = sorted(
            all_games,
            key=lambda f: (0 if strip_ext(f) in cached_keys else 1, f.lower())
        )
    else:
        games = []


# ═══ Memcard Directory Detection ════════════════════════════════
# PCSX2 (via RetroArch) stores memory cards in a system-specific
# location. We check common paths and create the fallback if needed.

def find_memcard_dir():
    """Locate RetroArch's PCSX2 memcard directory, creating fallback if needed."""
    if IS_WINDOWS:
        candidates = [
            os.path.join(os.environ.get('APPDATA', ''), 'RetroArch', 'system', 'pcsx2', 'memcards'),
            os.path.join(os.environ.get('APPDATA', ''), 'RetroArch', 'saves', 'pcsx2', 'memcards'),
        ]
        fallback = candidates[0]
    else:
        candidates = [
            os.path.expanduser("~/.config/retroarch/system/pcsx2/memcards"),
            os.path.expanduser("~/.config/retroarch/saves/pcsx2/memcards"),
        ]
        fallback = candidates[0]
    for d in candidates:
        if os.path.isdir(d):
            return d
    os.makedirs(fallback, exist_ok=True)
    return fallback


MEMCARD_ACTIVE = find_memcard_dir()

# ═══ Theme System ═══════════════════════════════════════════════
# The theme is defined by 4 base colors: bg, accent, highlight, text.
# All 15 UI colors are derived automatically via apply_theme().
# Theme is saved in config.json under the "theme" key and shared
# with the pre-launcher (ps2-checker.py) so both apps match.
import colorsys

# Default theme (Royal Purple & Gold)
DEFAULT_THEME = {
    "bg":        [18, 8, 32],
    "accent":    [147, 51, 234],
    "highlight":  [255, 200, 50],
    "text":      [220, 210, 190],
}

# Built-in presets
THEME_PRESETS = {
    "Royal Purple & Gold": {"bg": [18, 8, 32], "accent": [147, 51, 234], "highlight": [255, 200, 50], "text": [220, 210, 190]},
    "Ocean Blue":          {"bg": [8, 16, 36], "accent": [40, 120, 220], "highlight": [80, 220, 240], "text": [200, 215, 230]},
    "Forest Green":        {"bg": [10, 26, 16], "accent": [40, 160, 80], "highlight": [180, 220, 60], "text": [200, 220, 200]},
    "Crimson Red":         {"bg": [32, 8, 12], "accent": [200, 40, 60], "highlight": [255, 180, 50], "text": [220, 200, 195]},
    "Midnight Teal":       {"bg": [8, 20, 28], "accent": [0, 180, 170], "highlight": [255, 220, 100], "text": [210, 225, 220]},
    "Retro Amber":         {"bg": [28, 20, 8], "accent": [200, 140, 30], "highlight": [255, 200, 80], "text": [230, 215, 180]},
    "Slate Gray":          {"bg": [24, 26, 30], "accent": [100, 120, 160], "highlight": [200, 210, 230], "text": [200, 200, 210]},
}


def _clamp(v):
    """Clamp a value to the 0–255 RGB range."""
    return max(0, min(255, int(v)))

def _blend(c1, c2, t):
    """Blend two RGB tuples by factor t (0.0=c1, 1.0=c2)."""
    return tuple(_clamp(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))

def _lighten(c, amt=40):
    """Lighten an RGB color by adding a flat amount to each channel."""
    return tuple(_clamp(v + amt) for v in c)

def _darken(c, amt=40):
    """Darken an RGB color by subtracting a flat amount from each channel."""
    return tuple(_clamp(v - amt) for v in c)

def _dim(c, factor=0.6):
    """Dim an RGB color by multiplying each channel by a factor (0.0–1.0)."""
    return tuple(_clamp(v * factor) for v in c)


def apply_theme(theme_dict=None):
    """Derive all UI colors from 4 base colors and set globals."""
    global BG, SEL_BG, TXT, TXT_SEL, TXT_DIM, HDR, HINT
    global BAR_BG, BAR_FG, BAR_BORDER, ACCENT, DANGER, KEY_BG, KEY_SEL, SUCCESS

    t = dict(DEFAULT_THEME)
    if theme_dict:
        t.update(theme_dict)

    bg   = tuple(t["bg"])
    acc  = tuple(t["accent"])
    hi   = tuple(t["highlight"])
    txt  = tuple(t["text"])

    BG         = bg
    ACCENT     = acc
    HDR        = hi
    TXT_SEL    = hi
    TXT        = txt
    TXT_DIM    = _dim(txt, 0.62)
    HINT       = _blend(hi, txt, 0.45)
    SEL_BG     = _blend(bg, acc, 0.45)
    BAR_BG     = _lighten(bg, 22)
    BAR_FG     = _dim(hi, 0.75)
    BAR_BORDER = _blend(acc, bg, 0.4)
    KEY_BG     = _lighten(bg, 20)
    KEY_SEL    = _lighten(acc, 30)
    DANGER     = (200, 60, 60)   # Safety colors stay fixed
    SUCCESS    = (50, 180, 80)


def load_theme_from_config():
    """Load theme from global config and apply it."""
    cfg = load_global_config() if os.path.exists(GLOBAL_CONFIG_PATH) else {}
    theme = cfg.get("theme", None)
    apply_theme(theme)


def save_theme_to_config(theme_dict):
    """Save theme to global config."""
    cfg = load_global_config()
    cfg["theme"] = theme_dict
    save_global_config(cfg)


def get_current_theme():
    """Get the currently active theme dict (4 base colors)."""
    cfg = load_global_config() if os.path.exists(GLOBAL_CONFIG_PATH) else {}
    t = dict(DEFAULT_THEME)
    saved = cfg.get("theme")
    if saved:
        t.update(saved)
    return t


# Apply theme on import (uses defaults if no config exists yet)
load_theme_from_config()

# ═══ Input Mapping System ═══════════════════════════════════════
# Maps logical actions ("confirm", "back", etc.) to joystick button
# numbers. Users can remap these via Settings > Controller Mapping.
#
# SAFETY: Keyboard keys are NEVER remapped. Even if all controller
# binds are broken, Escape/Enter/arrows/Space always work. This
# prevents users from locking themselves out on headless devices.

DEFAULT_BUTTON_MAP = {
    "confirm":    0,   # A — select, enter, launch
    "back":       1,   # B — cancel, go back
    "extra":      2,   # X — contextual extra action
    "alt":        3,   # Y — alternative action
    "shoulder_l": 4,   # L1 — page left, toggle hidden files
    "shoulder_r": 5,   # R1 — page right
    "select":     6,   # Select — search
    "start":      7,   # Start — settings, confirm
}

# Friendly display names
BTN_LABELS = {
    "confirm":    "Confirm / Select (A)",
    "back":       "Back / Cancel (B)",
    "extra":      "Extra Action (X)",
    "alt":        "Alt Action (Y)",
    "shoulder_l": "Left Shoulder (L1)",
    "shoulder_r": "Right Shoulder (R1)",
    "select":     "Menu Select",
    "start":      "Menu Start",
}

# Standard SDL button name lookup
SDL_BUTTON_NAMES = {
    0: "A", 1: "B", 2: "X", 3: "Y",
    4: "L1", 5: "R1", 6: "Select", 7: "Start",
    8: "L3", 9: "R3", 10: "Guide", 11: "Misc",
}

# Active mapping (mutable at runtime)
BTN = dict(DEFAULT_BUTTON_MAP)


def load_button_map():
    """Load button mappings from global config."""
    global BTN
    BTN = dict(DEFAULT_BUTTON_MAP)
    if os.path.exists(GLOBAL_CONFIG_PATH):
        try:
            with open(GLOBAL_CONFIG_PATH) as f:
                cfg = json.load(f)
            saved = cfg.get("button_map", {})
            for k in BTN:
                if k in saved and isinstance(saved[k], int):
                    BTN[k] = saved[k]
        except Exception:
            pass


def save_button_map():
    """Save current button mappings to global config."""
    cfg = load_global_config()
    cfg["button_map"] = dict(BTN)
    save_global_config(cfg)


def btn_name(button_num):
    """Get a friendly name for a joystick button number."""
    return SDL_BUTTON_NAMES.get(button_num, f"Btn {button_num}")


# Load mappings on import
load_button_map()

os.makedirs(APP_DIR, exist_ok=True)
os.makedirs(USERS_DIR, exist_ok=True)


# ═══ Color & Animation Helpers ══════════════════════════════════

def lerp_color(c1, c2, t):
    """Linearly interpolate between two RGB colors."""
    t = max(0.0, min(1.0, t))
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


# ═══ Procedural Sound Effects ═══════════════════════════════════
# All UI sounds are generated at runtime from sine waves — no external
# audio files required. Each sound is a short pygame.mixer.Sound object.
SFX = {}


def _gen_tone(freq, duration_ms, volume=0.3, sample_rate=22050):
    """Generate a sine-wave tone as a pygame Sound."""
    n = int(sample_rate * duration_ms / 1000)
    buf = b''
    for i in range(n):
        t = i / sample_rate
        env = 1.0
        fade = int(sample_rate * 0.005)
        if i < fade:
            env = i / fade
        elif i > n - fade:
            env = (n - i) / fade
        val = int(volume * env * 32767 * math.sin(2 * math.pi * freq * t))
        buf += struct.pack('<h', max(-32768, min(32767, val)))
    return pygame.mixer.Sound(buffer=buf)


def _gen_sweep(f0, f1, duration_ms, volume=0.3, sample_rate=22050):
    """Generate a frequency sweep as a pygame Sound."""
    n = int(sample_rate * duration_ms / 1000)
    buf = b''
    for i in range(n):
        t = i / sample_rate
        frac = i / n
        freq = f0 + (f1 - f0) * frac
        env = 1.0
        fade = int(sample_rate * 0.005)
        if i < fade:
            env = i / fade
        elif i > n - fade:
            env = (n - i) / fade
        val = int(volume * env * 32767 * math.sin(2 * math.pi * freq * t))
        buf += struct.pack('<h', max(-32768, min(32767, val)))
    return pygame.mixer.Sound(buffer=buf)


def init_sounds():
    """Create all UI sounds procedurally. Call after pygame.mixer.init()."""
    global SFX
    try:
        pygame.mixer.init(22050, -16, 1, 512)
    except Exception:
        return
    SFX = {
        'navigate': _gen_tone(800, 50, 0.12),
        'select':   _gen_tone(440, 60, 0.10),        # Warm A4, short and soft
        'back':     _gen_sweep(600, 350, 90, 0.18),
        'error':    _gen_tone(280, 180, 0.25),
        'launch':   _gen_sweep(500, 900, 250, 0.20),  # Lowered peak from 1400
        'type':     _gen_tone(600, 25, 0.08),          # Lowered from 1000
    }
    apply_volume()


def play_sfx(name):
    """Play a named sound effect (respects volume/mute)."""
    if active_cfg.get("muted", False):
        return
    s = SFX.get(name)
    if s:
        s.play()


# ═══ User & Memory Card Management ═════════════════════════════
# Each user profile lives in ~/ps2-users/<name>/ with:
#   cards/<card_name>/   — memory card snapshots (Mcd001.ps2, Mcd002.ps2)
#   backup/              — automatic backup of last loaded card
#   meta.json            — last-used card, per-user settings overrides
#
# Before launching a game, the selected card's files are copied into
# RetroArch's active memcard directory. After the game exits, the
# active memcards are saved back to the card slot.

def get_users():
    """Return sorted list of user profile directory names."""
    if not os.path.isdir(USERS_DIR):
        return []
    return sorted([d for d in os.listdir(USERS_DIR)
                   if os.path.isdir(os.path.join(USERS_DIR, d))],
                  key=str.lower)


def create_user(name):
    """Create a new user profile directory with a default card slot."""
    udir = os.path.join(USERS_DIR, name)
    os.makedirs(os.path.join(udir, "cards", "Card 1"), exist_ok=True)
    os.makedirs(os.path.join(udir, "backup"), exist_ok=True)
    save_user_meta(name, {"last_card": "Card 1"})


def get_user_meta(name):
    """Load a user's metadata (last card, settings overrides) from meta.json."""
    p = os.path.join(USERS_DIR, name, "meta.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_user_meta(name, meta):
    """Persist a user's metadata dict to meta.json."""
    p = os.path.join(USERS_DIR, name, "meta.json")
    with open(p, 'w') as f:
        json.dump(meta, f, indent=2)


def get_cards(user):
    """Return sorted list of memory card slot names for a user (last-used first)."""
    cards_dir = os.path.join(USERS_DIR, user, "cards")
    if not os.path.isdir(cards_dir):
        return []
    cards = [d for d in os.listdir(cards_dir)
             if os.path.isdir(os.path.join(cards_dir, d))]
    meta = get_user_meta(user)
    last = meta.get("last_card", "")
    cards.sort(key=lambda c: (0 if c == last else 1, c.lower()))
    return cards


def create_card(user, name):
    """Create an empty memory card slot directory for a user."""
    os.makedirs(os.path.join(USERS_DIR, user, "cards", name), exist_ok=True)


def delete_card(user, name):
    """Permanently remove a memory card slot and its save data."""
    d = os.path.join(USERS_DIR, user, "cards", name)
    if os.path.isdir(d):
        shutil.rmtree(d)


def load_memcard(user, card):
    """Copy a user's saved memcard files into RetroArch's active directory.

    Also creates a backup of whatever is currently active before overwriting.
    """
    card_dir = os.path.join(USERS_DIR, user, "cards", card)
    backup_dir = os.path.join(USERS_DIR, user, "backup")
    os.makedirs(backup_dir, exist_ok=True)
    os.makedirs(MEMCARD_ACTIVE, exist_ok=True)
    for f in MEMCARD_FILES:
        src = os.path.join(card_dir, f)
        dst = os.path.join(MEMCARD_ACTIVE, f)
        bak = os.path.join(backup_dir, f)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            shutil.copy2(src, bak)
        else:
            for p in (dst, bak):
                if os.path.exists(p):
                    os.remove(p)
    meta = get_user_meta(user)
    meta["last_card"] = card
    save_user_meta(user, meta)


def save_memcard(user, card):
    """Copy RetroArch's active memcard files back into the user's card slot."""
    card_dir = os.path.join(USERS_DIR, user, "cards", card)
    os.makedirs(card_dir, exist_ok=True)
    for f in MEMCARD_FILES:
        src = os.path.join(MEMCARD_ACTIVE, f)
        dst = os.path.join(card_dir, f)
        if os.path.exists(src):
            shutil.copy2(src, dst)


# ═══ PS2 Memory Card Filesystem Parser ══════════════════════════
# Pure-Python parser for .ps2 memory card images. Reads the FAT-based
# filesystem to list game saves, extract display titles from icon.sys,
# and report file sizes and modification dates.

PS2MC_MAGIC = b"Sony PS2 Memory Card Format "
ICON_SYS_MAGIC = b"PS2D"

_SB_PAGE_LEN    = 0x28
_SB_PPC         = 0x2A
_SB_ALLOC_OFF   = 0x34
_SB_ROOTDIR_CL  = 0x3C
_SB_IFC_LIST    = 0x50

_MODE_EXISTS = 0x8000
_MODE_DIR    = 0x0020
_MODE_FILE   = 0x0010

DIRENT_SIZE = 512


class PS2Save:
    """One game save directory on the memory card."""
    __slots__ = ('name', 'title', 'size', 'file_count', 'modified', 'files', 'icon_pixels')
    def __init__(self, name, title, size, file_count, modified, files, icon_pixels=None):
        self.name = name; self.title = title; self.size = size
        self.file_count = file_count; self.modified = modified
        self.files = files; self.icon_pixels = icon_pixels


def _mc_u16(data, off):
    return struct.unpack_from('<H', data, off)[0]

def _mc_u32(data, off):
    return struct.unpack_from('<I', data, off)[0]

def _mc_tod(data, off):
    """Parse a PS2 Time-of-Day timestamp (8 bytes) into datetime."""
    try:
        sec, minute, hour = data[off+1], data[off+2], data[off+3]
        day, month = data[off+4], data[off+5]
        year = _mc_u16(data, off+6)
        if year < 1970 or month < 1 or month > 12 or day < 1 or day > 31:
            return None
        return datetime.datetime(year, month, day, hour, minute, sec)
    except (ValueError, IndexError):
        return None

def _mc_name(data, off, length=32):
    """Read a zero-terminated ASCII name from a directory entry."""
    raw = data[off:off + length]
    end = raw.find(b'\x00')
    if end >= 0: raw = raw[:end]
    try: return raw.decode('ascii', errors='replace')
    except Exception: return ""


class _PS2Memcard:
    """Low-level parser for a raw PS2 memory card image.

    Handles both standard (8,388,608 bytes = 512-byte pages) and
    ECC/raw format (8,650,752 bytes = 528-byte pages with 16-byte
    spare data appended after every 512-byte page). RetroArch's
    PCSX2 core uses the ECC format by default.

    IFC and FAT clusters are addressed by ABSOLUTE cluster index
    (they live in the reserved area before alloc_offset). Data
    clusters (root dir, save dirs, files) are addressed RELATIVE
    to alloc_offset.
    """

    def __init__(self, data):
        self._data = data
        self._page_len = _mc_u16(data, _SB_PAGE_LEN) or 512
        self._ppc = _mc_u16(data, _SB_PPC) or 2
        self._cluster_size = self._page_len * self._ppc  # logical (1024)
        self._alloc_offset = _mc_u32(data, _SB_ALLOC_OFF)
        self._rootdir_cluster = _mc_u32(data, _SB_ROOTDIR_CL)
        self._ifc_list = [_mc_u32(data, _SB_IFC_LIST + i*4) for i in range(32)]
        self._fat = {}

        # Detect ECC format: 16384 pages × 528 bytes = 8,650,752
        ecc_page = self._page_len + 16
        total_ecc = len(data) // ecc_page
        if total_ecc * ecc_page == len(data) and len(data) != total_ecc * self._page_len:
            self._phys_page = ecc_page   # 528 on disk per page
        else:
            self._phys_page = self._page_len  # 512

    def _read_page(self, page_index):
        """Read one logical page, stripping ECC spare bytes if present."""
        off = page_index * self._phys_page
        return self._data[off:off + self._page_len]

    def _read_cl_abs(self, abs_cl):
        """Read one cluster by ABSOLUTE cluster index (for IFC/FAT)."""
        first_page = abs_cl * self._ppc
        buf = bytearray()
        for p in range(self._ppc):
            buf.extend(self._read_page(first_page + p))
        return bytes(buf)

    def _read_cl(self, cl):
        """Read one data cluster RELATIVE to alloc_offset."""
        return self._read_cl_abs(self._alloc_offset + cl)

    def _fat_val(self, cl):
        if cl in self._fat: return self._fat[cl]
        epc = self._cluster_size // 4
        ifc_i = cl // (epc * epc)
        if ifc_i >= 32: return 0xFFFFFFFF
        ifc_cl = self._ifc_list[ifc_i]
        if ifc_cl == 0xFFFFFFFF: return 0xFFFFFFFF
        ifc_data = self._read_cl_abs(ifc_cl)          # absolute
        fat_cl = _mc_u32(ifc_data, ((cl // epc) % epc) * 4)
        if fat_cl == 0xFFFFFFFF: return 0xFFFFFFFF
        fat_data = self._read_cl_abs(fat_cl)           # absolute
        if len(fat_data) < 4: return 0xFFFFFFFF        # safety
        val = _mc_u32(fat_data, (cl % epc) * 4)
        self._fat[cl] = val
        return val

    def _chain(self, start, limit=4096):
        chain, cur, seen = [], start, set()
        while len(chain) < limit:
            if cur == 0xFFFFFFFF or cur in seen: break
            seen.add(cur); chain.append(cur)
            raw = self._fat_val(cur)
            if raw == 0xFFFFFFFF: break          # end of chain
            cur = raw & 0x7FFFFFFF               # strip "used" bit
        return chain

    def _chain_data(self, start, length=None):
        buf = bytearray()
        for cl in self._chain(start):
            buf.extend(self._read_cl(cl))
        return bytes(buf[:length]) if length else bytes(buf)

    def _dirents(self, start, count):
        raw = self._chain_data(start)
        entries = []
        for i in range(count):
            o = i * DIRENT_SIZE
            if o + DIRENT_SIZE > len(raw): break
            entries.append({
                'mode': _mc_u16(raw, o),
                'length': _mc_u32(raw, o+4),
                'created': _mc_tod(raw, o+8),
                'cluster': _mc_u32(raw, o+0x10),
                'modified': _mc_tod(raw, o+0x18),
                'name': _mc_name(raw, o+0x40),
            })
        return entries

    def _parse_icon_sys(self, data):
        """Extract display title and icon filename from icon.sys.

        icon.sys layout:
          0x00    4   Magic 'PS2D'
          0x06    2   Line-break position (character offset)
          0xC0   68   Title (Shift-JIS, may contain two null-separated lines)
          0x104  64   Normal icon filename (ASCII, null-terminated)

        Returns (title_str, icon_filename_str) or (None, None).
        """
        if len(data) < 0x148 or data[:4] != ICON_SYS_MAGIC:
            return None, None

        # ── Title: read 68 bytes at 0xC0, split on nulls for multi-line ──
        title_raw = data[0xC0:0xC0 + 68]
        # Split on null bytes to get all title line segments
        segments = title_raw.split(b'\x00')
        parts = []
        for seg in segments:
            if not seg: continue
            # Try Shift-JIS first, fall back to latin-1
            for enc in ('shift_jis', 'cp1252', 'latin-1'):
                try:
                    text = seg.decode(enc, errors='strict')
                    break
                except (UnicodeDecodeError, LookupError):
                    continue
            else:
                text = seg.decode('latin-1', errors='replace')
            # Normalize full-width Latin/digits to ASCII equivalents
            text = unicodedata.normalize('NFKC', text).strip()
            # Keep only characters the pygame font can render:
            # ASCII printable + Latin-1 Supplement + Latin Extended A/B
            text = ''.join(c for c in text if c.isprintable() and ord(c) < 0x250)
            if text:
                parts.append(text)
        title = ' - '.join(parts) if parts else None

        # If title ended up empty or too short, discard
        if not title or len(title.replace(' ', '').replace('-', '')) < 2:
            title = None

        # ── Icon filename at 0x104 (64 bytes, null-terminated ASCII) ──
        icon_fn_raw = data[0x104:0x104 + 64]
        end = icon_fn_raw.find(b'\x00')
        if end >= 0: icon_fn_raw = icon_fn_raw[:end]
        try:
            icon_fn = icon_fn_raw.decode('ascii', errors='ignore').strip()
        except Exception:
            icon_fn = None

        return title, icon_fn

    @staticmethod
    def _extract_icon_texture(ico_data):
        """Extract 128x128 texture from a PS2 .ico file as RGBA bytes.

        PS2 icon files contain a header, vertex/animation data, then a
        128x128 16-bit ABGR-1555 texture at the end of the file.
        Returns a bytes object (128*128*4 RGBA) or None.
        """
        TEX_BYTES = 128 * 128 * 2  # 32768
        if len(ico_data) < TEX_BYTES + 20:
            return None
        # Check PS2 icon magic
        magic = _mc_u32(ico_data, 0)
        if magic != 0x00010000:
            return None
        # Texture type at offset 0x08 (0 = no texture)
        tex_type = _mc_u32(ico_data, 0x08)
        if tex_type == 0:
            return None
        # Texture is the last 32768 bytes
        tex_raw = ico_data[-TEX_BYTES:]
        # Batch-read all uint16 pixels
        pixels_u16 = struct.unpack(f'<{128*128}H', tex_raw)
        # Convert ABGR-1555 to RGBA-8888
        rgba = bytearray(128 * 128 * 4)
        for i, p in enumerate(pixels_u16):
            j = i * 4
            rgba[j]     = (p & 0x1F) << 3           # R
            rgba[j + 1] = ((p >> 5) & 0x1F) << 3    # G
            rgba[j + 2] = ((p >> 10) & 0x1F) << 3   # B
            rgba[j + 3] = 255 if (p >> 15) else 0    # A
        return bytes(rgba)

    # PS2 system directories that are not game saves
    _SYSTEM_DIRS = {'badata-system', 'baexec-system', 'biexec-system',
                    'bxdata-system', 'bwexec-system'}

    def list_saves(self):
        root = self._dirents(self._rootdir_cluster, 512)
        saves = []
        for e in root:
            if e['name'] in ('.', '..', ''): continue
            if not (e['mode'] & _MODE_EXISTS) or not (e['mode'] & _MODE_DIR): continue
            # Skip known PS2 system directories
            if e['name'].lower() in self._SYSTEM_DIRS: continue
            subs = self._dirents(e['cluster'], e['length'])
            files, total, icon_sys_data = [], 0, None
            # Map filenames to their cluster/length for icon lookup
            file_map = {}
            for s in subs:
                if s['name'] in ('.', '..', ''): continue
                if not (s['mode'] & _MODE_EXISTS) or not (s['mode'] & _MODE_FILE): continue
                files.append((s['name'], s['length'], s['modified'] or s['created']))
                total += s['length']
                file_map[s['name'].lower()] = s
                if s['name'].lower() == 'icon.sys' and icon_sys_data is None:
                    try: icon_sys_data = self._chain_data(s['cluster'], min(s['length'], 2048))
                    except Exception: pass

            title, icon_fn = (None, None)
            if icon_sys_data:
                title, icon_fn = self._parse_icon_sys(icon_sys_data)

            # Try to extract the icon texture
            icon_pixels = None
            if icon_fn:
                ico_entry = file_map.get(icon_fn.lower())
                if ico_entry:
                    try:
                        ico_raw = self._chain_data(ico_entry['cluster'],
                                                   min(ico_entry['length'], 200000))
                        icon_pixels = self._extract_icon_texture(ico_raw)
                    except Exception:
                        pass

            # Only show entries with a readable title from icon.sys
            if not title:
                continue
            saves.append(PS2Save(e['name'], title, total,
                                 len(files), e['modified'] or e['created'],
                                 files, icon_pixels))
        return saves


def parse_memcard(path):
    """Parse a .ps2 memory card file. Returns list of PS2Save objects."""
    if not os.path.isfile(path) or os.path.getsize(path) < 0x200:
        return []
    try:
        with open(path, 'rb') as f:
            data = f.read()
        if not data[:20].startswith(PS2MC_MAGIC[:20]):
            return []
        return _PS2Memcard(data).list_saves()
    except Exception:
        return []


# ═══ ROM List & Archive Helpers ═════════════════════════════════════

def strip_ext(name):
    """Remove a recognized archive extension (.zip, .7z, etc.) from a filename."""
    for e in EXTS:
        if name.lower().endswith(e):
            return name[:-len(e)]
    return name


def _find_7z():
    """Locate 7z binary across platforms."""
    if IS_WINDOWS:
        for p in [
            os.path.join(os.environ.get('PROGRAMFILES', ''), '7-Zip', '7z.exe'),
            os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), '7-Zip', '7z.exe'),
            '7z.exe', '7z',
        ]:
            if shutil.which(p) or os.path.isfile(p):
                return p
    return shutil.which('7z') or '7z'


def get_archive_size(path):
    """Return uncompressed size in bytes of a .zip or .7z archive."""
    lower = path.lower()
    try:
        if lower.endswith('.zip'):
            # Use Python zipfile (cross-platform)
            with zipfile.ZipFile(path, 'r') as zf:
                return sum(info.file_size for info in zf.infolist())
        elif lower.endswith('.7z'):
            cmd_7z = _find_7z()
            r = subprocess.run([cmd_7z, "l", path], capture_output=True, text=True)
            for line in reversed(r.stdout.splitlines()):
                parts = line.split()
                if parts and parts[0].isdigit() and len(parts) >= 3:
                    return int(parts[0])
    except Exception:
        pass
    return 0


def get_dir_size(d):
    """Recursively sum the size of all files in a directory tree."""
    total = 0
    try:
        for dp, _, fns in os.walk(d):
            for f in fns:
                try:
                    total += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
    except Exception:
        pass
    return total


# ═══ Display Initialization & Scaling ═══════════════════════════
# Reference resolution is 480p. Everything scales relative to that.
REF_H = 480
UI_SCALE = 1.0  # Updated at init_display()


def scaled(val):
    """Scale a pixel value by the current UI scale factor."""
    return max(1, int(val * UI_SCALE))


def init_display():
    """Initialize or reinitialize pygame and return (screen, fonts, joy)."""
    global UI_SCALE, LINE_H, VISIBLE
    pygame.init()
    pygame.joystick.init()

    scr = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    w, h = scr.get_size()

    # Calculate scale factor from actual height vs reference 480p
    UI_SCALE = h / REF_H

    # Pick a font that exists on this platform
    font_name = None
    for candidate in ["DejaVu Sans", "Segoe UI", "Arial", "Helvetica", None]:
        if candidate is None:
            font_name = None  # pygame default
            break
        if candidate.lower().replace(' ', '') in [f.lower().replace(' ', '') for f in pygame.font.get_fonts()]:
            font_name = candidate
            break

    fonts = {
        'sm': pygame.font.SysFont(font_name, scaled(11)),
        'md': pygame.font.SysFont(font_name, scaled(16)),
        'md_b': pygame.font.SysFont(font_name, scaled(16), bold=True),
        'lg': pygame.font.SysFont(font_name, scaled(20), bold=True),
        'xl': pygame.font.SysFont(font_name, scaled(22), bold=True),
    }

    # Scale layout constants
    LINE_H = scaled(24)
    VISIBLE = (h - scaled(70)) // LINE_H

    joy = None
    if pygame.joystick.get_count() > 0:
        joy = pygame.joystick.Joystick(0)
        joy.init()

    return scr, w, h, fonts, joy


screen, W, H, F, joy = init_display()
clock = pygame.time.Clock()

LINE_H = scaled(24)
VISIBLE = (H - scaled(70)) // LINE_H
DPAD_DELAY = 180


# ═══ Drawing helpers ════════════════════════════════════════════

def truncate(text, font, max_w):
    """Shorten text with '...' suffix so it fits within max_w pixels."""
    if font.size(text)[0] <= max_w:
        return text
    while len(text) > 0 and font.size(text + "...")[0] > max_w:
        text = text[:-1]
    return text + "..."


def draw_hint_bar(text):
    """Draw a visible hint bar at the bottom of the screen."""
    bar_h = scaled(32)
    bar_rect = pygame.Rect(0, H - bar_h, W, bar_h)
    pygame.draw.rect(screen, BAR_BG, bar_rect)
    pygame.draw.line(screen, ACCENT, (0, H - bar_h), (W, H - bar_h), 1)
    hs = F['md'].render(text, True, HINT)
    screen.blit(hs, hs.get_rect(center=(W // 2, H - bar_h // 2)))


def draw_header(title, hint_text=None, count_text=None):
    """Draw the screen header bar with title, optional hint, and item count."""
    t = F['lg'].render(title, True, HDR)
    screen.blit(t, (scaled(12), scaled(6)))
    if count_text:
        cs = F['sm'].render(count_text, True, HINT)
        screen.blit(cs, (W - cs.get_width() - scaled(12), scaled(12)))
    if hint_text:
        h = F['sm'].render(hint_text, True, HINT)
        screen.blit(h, (scaled(12), scaled(30)))
    pygame.draw.line(screen, ACCENT, (scaled(10), scaled(46)), (W - scaled(10), scaled(46)), 1)


def draw_list(items, sel_idx, scroll, colors=None, smooth_offset=0.0):
    """Draw a scrollable list with optional smooth scroll offset.
    items = list of (text, is_special) tuples.
    smooth_offset = fractional row offset for lerp scrolling (0.0 = aligned)."""
    top = scaled(50)
    vis = VISIBLE
    total = len(items)
    icon_w = scaled(20)  # reserve space for ⚡ icon
    text_max = W - scaled(40) - icon_w  # text area excludes icon column
    pixel_offset = int(smooth_offset * LINE_H)

    # Clip to list area
    list_rect = pygame.Rect(0, top, W, vis * LINE_H)
    screen.set_clip(list_rect)

    # Draw one extra row above and below for smooth scrolling
    start = max(0, scroll - 1)
    end = min(total, scroll + vis + 2)
    for i in range(start, end):
        y = top + (i - scroll) * LINE_H - pixel_offset
        if y + LINE_H < top or y > top + vis * LINE_H:
            continue
        text, special = items[i]
        display = truncate(text, F['md'], text_max)
        if i == sel_idx:
            bg = DANGER if (colors and colors.get(i) == 'danger') else SEL_BG
            pygame.draw.rect(screen, bg, (scaled(6), y, W - scaled(12), LINE_H - 2), border_radius=scaled(4))
            color = TXT_SEL
            label = F['md_b'].render(display, True, color)
        else:
            color = HDR if special else TXT
            label = F['md'].render(display, True, color)
        screen.blit(label, (scaled(14), y + scaled(3)))
        # Cached indicator
        if special:
            tag = F['sm'].render("\u26A1", True, SUCCESS)
            screen.blit(tag, (W - scaled(26), y + scaled(3)))

    screen.set_clip(None)

    # Scroll indicator (right edge)
    if total > vis and vis > 0:
        scroll_frac = (scroll + smooth_offset) / max(1, total - vis)
        track_top = top
        track_h = vis * LINE_H
        thumb_h = max(scaled(12), int(track_h * vis / total))
        thumb_y = track_top + int((track_h - thumb_h) * scroll_frac)
        bar_x = W - scaled(4)
        pygame.draw.rect(screen, BAR_BG, (bar_x, track_top, scaled(3), track_h), border_radius=1)
        pygame.draw.rect(screen, ACCENT, (bar_x, thumb_y, scaled(3), thumb_h), border_radius=1)


def draw_center_msg(top_text, mid_text="", bot_text=""):
    """Draw a centered message screen (used for loading, empty states, etc.)."""
    screen.fill(BG)
    if top_text:
        s = F['xl'].render(top_text, True, HDR)
        screen.blit(s, s.get_rect(center=(W // 2, H // 5)))
    if mid_text:
        s = F['md'].render(mid_text, True, TXT)
        screen.blit(s, s.get_rect(center=(W // 2, H // 2)))
    if bot_text:
        draw_hint_bar(bot_text)
    pygame.display.flip()


def draw_progress(game_name, progress, status="Extracting"):
    """Draw a full-screen progress bar (used during game extraction)."""
    screen.fill(BG)
    s = F['xl'].render(status, True, HDR)
    screen.blit(s, s.get_rect(center=(W // 2, H // 6)))
    bw = int(W * 0.7)
    bh = scaled(22)
    bx = (W - bw) // 2
    by = H // 2 - bh // 2
    pygame.draw.rect(screen, BAR_BG, (bx, by, bw, bh), border_radius=scaled(6))
    pygame.draw.rect(screen, BAR_BORDER, (bx, by, bw, bh), 2, border_radius=scaled(6))
    if progress > 0:
        fw = max(4, int((bw - 4) * min(progress, 1.0)))
        pygame.draw.rect(screen, BAR_FG, (bx + 2, by + 2, fw, bh - 4), border_radius=scaled(4))
    pt = F['md_b'].render(f"{int(progress * 100)}%", True, TXT_SEL)
    screen.blit(pt, pt.get_rect(center=(W // 2, by + bh + scaled(16))))
    dn = truncate(strip_ext(game_name), F['md'], W - scaled(60))
    n = F['md'].render(dn, True, TXT)
    screen.blit(n, n.get_rect(center=(W // 2, int(H * 0.78))))
    draw_hint_bar("B: Cancel")
    pygame.display.flip()


def draw_toast(msg, color=SUCCESS, duration=1.2):
    """Show a brief toast notification at the bottom of the screen."""
    toast_h = scaled(40)
    start = time.time()
    while time.time() - start < duration:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return
        t = (time.time() - start) / duration
        alpha = int(255 * (1 - t) if t > 0.7 else 255)
        overlay = pygame.Surface((W, toast_h))
        overlay.fill(color)
        overlay.set_alpha(alpha)
        screen.blit(overlay, (0, H - toast_h))
        ts = F['md_b'].render(msg, True, BG)
        ts.set_alpha(alpha)
        screen.blit(ts, ts.get_rect(center=(W // 2, H - toast_h // 2)))
        pygame.display.flip()
        clock.tick(30)


# ─── Screen transition system ───────────────────────────────────
# Crossfade: old screen blends smoothly into the new one.
# fade_to_black()   = capture the current screen (instant, no animation)
# fade_from_black() = crossfade from captured snapshot to current content
_transition_snapshot = None

def fade_to_black(duration=None):
    """Capture the current screen for a later crossfade. Instant."""
    global _transition_snapshot
    _transition_snapshot = screen.copy()


def fade_from_black(duration=0.18):
    """Crossfade from the stored snapshot to the current screen content."""
    global _transition_snapshot
    if _transition_snapshot is None:
        return
    new_frame = screen.copy()
    steps = max(1, int(duration * 60))
    for i in range(1, steps + 1):
        t = i / steps
        # Blend: old * (1-t) + new * t
        screen.blit(_transition_snapshot, (0, 0))
        new_frame.set_alpha(int(255 * t))
        screen.blit(new_frame, (0, 0))
        pygame.display.flip()
        clock.tick(60)
    new_frame.set_alpha(255)
    _transition_snapshot = None


def _wait_any_button():
    """Block until any button/key is pressed."""
    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type in (pygame.JOYBUTTONDOWN, pygame.KEYDOWN):
                return
        clock.tick(30)



# ═══ On-Screen Keyboard ════════════════════════════════════════

def on_screen_keyboard(prompt="Enter Text"):
    """Full on-screen keyboard with controller support.
       Returns entered text or None if cancelled."""
    kb_rows = [
        list("ABCDEFGHIJ"),
        list("KLMNOPQRST"),
        list("UVWXYZ0123"),
        list("456789.-_ "),
        list("/!@#\u2713\u2190    "),
    ]
    KB_COLS = 10
    KB_NROWS = len(kb_rows)
    kx, ky = 0, 0
    text = ""
    last_joy = 0

    while True:
        now = time.time()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    play_sfx('back'); return None
                if ev.key == pygame.K_RETURN:
                    if text.strip():
                        play_sfx('select'); return text.strip()
                if ev.key == pygame.K_BACKSPACE:
                    text = text[:-1]; play_sfx('type')
                elif ev.unicode and ev.unicode.isprintable():
                    if len(text) < 40:
                        text += ev.unicode; play_sfx('type')
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"]:  # A = type selected char
                    ch = kb_rows[ky][kx]
                    if ch == "\u2713":
                        if text.strip():
                            play_sfx('select'); return text.strip()
                    elif ch == "\u2190":
                        text = text[:-1]; play_sfx('type')
                    elif ch == " ":
                        if len(text) < 40:
                            text += " "; play_sfx('type')
                    else:
                        if len(text) < 40:
                            text += ch; play_sfx('type')
                if ev.button == BTN["back"]:  # B = cancel
                    play_sfx('back'); return None
                if ev.button == BTN["start"]:  # Start = confirm
                    if text.strip():
                        play_sfx('select'); return text.strip()
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hy == 1:
                    ky = (ky - 1) % KB_NROWS; play_sfx('navigate')
                if hy == -1:
                    ky = (ky + 1) % KB_NROWS; play_sfx('navigate')
                if hx == -1:
                    kx = (kx - 1) % KB_COLS; play_sfx('navigate')
                if hx == 1:
                    kx = (kx + 1) % KB_COLS; play_sfx('navigate')

        # Analog stick
        if joy is not None and now - last_joy > DPAD_DELAY / 1000:
            moved = False
            xa = joy.get_axis(0)
            ya = joy.get_axis(1)
            if abs(xa) > 0.5:
                kx = (kx + (1 if xa > 0 else -1)) % KB_COLS; moved = True
            if abs(ya) > 0.5:
                ky = (ky + (1 if ya > 0 else -1)) % KB_NROWS; moved = True
            if moved:
                play_sfx('navigate'); last_joy = now

        screen.fill(BG)
        # Prompt
        ps = F['lg'].render(prompt, True, HDR)
        screen.blit(ps, ps.get_rect(center=(W // 2, scaled(20))))
        # Text field
        tf_rect = pygame.Rect(scaled(20), scaled(44), W - scaled(40), scaled(28))
        pygame.draw.rect(screen, KEY_BG, tf_rect, border_radius=scaled(6))
        pygame.draw.rect(screen, ACCENT, tf_rect, 1, border_radius=scaled(6))
        display_text = text + "|"
        ts = F['md'].render(display_text, True, TXT_SEL)
        screen.blit(ts, (tf_rect.x + scaled(8), tf_rect.y + scaled(5)))
        # Keyboard grid
        kw = (W - scaled(40)) // KB_COLS
        kh = scaled(32)
        ky_start = scaled(80)
        for r in range(KB_NROWS):
            for c in range(KB_COLS):
                rx = 20 + c * kw
                ry = ky_start + r * (kh + scaled(4))
                rect = pygame.Rect(rx, ry, kw - scaled(4), kh)
                is_sel = (r == ky and c == kx)
                bg_c = KEY_SEL if is_sel else KEY_BG
                pygame.draw.rect(screen, bg_c, rect, border_radius=scaled(4))
                if is_sel:
                    pygame.draw.rect(screen, ACCENT, rect, 2, border_radius=scaled(4))
                ch = kb_rows[r][c]
                color = TXT_SEL if is_sel else TXT
                if ch == "\u2713":
                    color = SUCCESS if is_sel else (50, 140, 60)
                elif ch == "\u2190":
                    color = DANGER if is_sel else (160, 50, 50)
                cs = F['md_b'].render(ch, True, color)
                screen.blit(cs, cs.get_rect(center=rect.center))
        # Hints
        draw_hint_bar("[A] Type   [B] Cancel   [Start] Confirm   Physical keyboard works too")
        pygame.display.flip()
        clock.tick(30)


# ═══ Confirm Dialog ═════════════════════════════════════════════

def confirm_dialog(message):
    """Yes/No confirm dialog. Returns True if user selected Yes."""
    sel = 0  # 0 = Yes, 1 = No
    last_joy = 0

    while True:
        now = time.time()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    play_sfx('back'); return False
                if ev.key in (pygame.K_LEFT, pygame.K_RIGHT):
                    sel = 1 - sel; play_sfx('navigate')
                if ev.key == pygame.K_RETURN:
                    play_sfx('select' if sel == 0 else 'back'); return sel == 0
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["back"]:
                    play_sfx('back'); return False
                if ev.button == BTN["confirm"]:
                    play_sfx('select' if sel == 0 else 'back'); return sel == 0
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hx != 0:
                    sel = 1 - sel; play_sfx('navigate')
        if joy is not None and now - last_joy > DPAD_DELAY / 1000:
            xa = joy.get_axis(0)
            if abs(xa) > 0.5:
                sel = 1 - sel; play_sfx('navigate')
                last_joy = now

        screen.fill(BG)
        # Truncate long messages to fit screen width
        display_msg = truncate(message, F['lg'], W - scaled(40))
        ms = F['lg'].render(display_msg, True, HDR)
        screen.blit(ms, ms.get_rect(center=(W // 2, H // 3)))
        for i, label in enumerate(["Yes", "No"]):
            bx = W // 2 + (i * scaled(120) - scaled(60))
            rect = pygame.Rect(bx - scaled(40), H // 2, scaled(80), scaled(32))
            if i == sel:
                pygame.draw.rect(screen, SEL_BG, rect, border_radius=scaled(6))
                pygame.draw.rect(screen, ACCENT, rect, 2, border_radius=scaled(6))
            else:
                pygame.draw.rect(screen, KEY_BG, rect, border_radius=scaled(6))
            color = TXT_SEL if i == sel else TXT
            ls = F['md_b'].render(label, True, color)
            screen.blit(ls, ls.get_rect(center=rect.center))
        draw_hint_bar("[A] Select   [B] Cancel")
        pygame.display.flip()
        clock.tick(30)


# ═══ Number Input ═══════════════════════════════════════════════

def number_input(prompt, current_val, min_val=1, max_val=99, step=1):
    """Controller-friendly number input. Returns int or None."""
    val = int(current_val)
    last_joy = 0

    while True:
        now = time.time()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    play_sfx('back'); return None
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    play_sfx('select'); return val
                if ev.key == pygame.K_UP or ev.key == pygame.K_RIGHT:
                    val = min(max_val, val + step); play_sfx('navigate')
                if ev.key == pygame.K_DOWN or ev.key == pygame.K_LEFT:
                    val = max(min_val, val - step); play_sfx('navigate')
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"] or ev.button == BTN["start"]:
                    play_sfx('select'); return val
                if ev.button == BTN["back"]:
                    play_sfx('back'); return None
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hy == 1 or hx == 1:
                    val = min(max_val, val + step); play_sfx('navigate')
                if hy == -1 or hx == -1:
                    val = max(min_val, val - step); play_sfx('navigate')

        if joy is not None and now - last_joy > DPAD_DELAY / 1000:
            moved = False
            ya = joy.get_axis(1)
            xa = joy.get_axis(0)
            if ya < -0.5 or xa > 0.5:
                val = min(max_val, val + step); moved = True
            elif ya > 0.5 or xa < -0.5:
                val = max(min_val, val - step); moved = True
            if moved:
                play_sfx('navigate'); last_joy = now

        screen.fill(BG)
        ps = F['lg'].render(prompt, True, HDR)
        screen.blit(ps, ps.get_rect(center=(W // 2, H // 3)))

        # Value display with arrows
        arrow_l = F['xl'].render("<", True, ACCENT)
        arrow_r = F['xl'].render(">", True, ACCENT)
        vs = F['xl'].render(str(val), True, TXT_SEL)
        cx = W // 2
        cy = H // 2
        screen.blit(arrow_l, arrow_l.get_rect(center=(cx - scaled(60), cy)))
        screen.blit(vs, vs.get_rect(center=(cx, cy)))
        screen.blit(arrow_r, arrow_r.get_rect(center=(cx + scaled(60), cy)))

        rng = F['sm'].render(f"Range: {min_val} - {max_val}", True, TXT_DIM)
        screen.blit(rng, rng.get_rect(center=(W // 2, cy + scaled(40))))

        draw_hint_bar("[D-Pad] Adjust   [A] Confirm   [B] Cancel")
        pygame.display.flip()
        clock.tick(30)


# ═══ Volume Slider ══════════════════════════════════════════════

def volume_slider(prompt="Volume", current_vol=0.5, muted=False):
    """Interactive volume slider. Returns (volume_float, muted_bool) or None."""
    vol = current_vol
    is_muted = muted
    last_joy = 0
    STEP = 0.05

    while True:
        now = time.time()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    play_sfx('back'); return None
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    play_sfx('select'); return (vol, is_muted)
                if ev.key == pygame.K_RIGHT or ev.key == pygame.K_UP:
                    vol = min(1.0, vol + STEP); is_muted = False; play_sfx('navigate')
                if ev.key == pygame.K_LEFT or ev.key == pygame.K_DOWN:
                    vol = max(0.0, vol - STEP); play_sfx('navigate')
                if ev.key == pygame.K_m:
                    is_muted = not is_muted; play_sfx('navigate')
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"] or ev.button == BTN["start"]:
                    play_sfx('select'); return (vol, is_muted)
                if ev.button == BTN["back"]:
                    play_sfx('back'); return None
                if ev.button == BTN["extra"]:  # X = toggle mute
                    is_muted = not is_muted; play_sfx('navigate')
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hx == 1 or hy == 1:
                    vol = min(1.0, vol + STEP); is_muted = False; play_sfx('navigate')
                if hx == -1 or hy == -1:
                    vol = max(0.0, vol - STEP); play_sfx('navigate')

        if joy is not None and now - last_joy > DPAD_DELAY / 1000:
            moved = False
            xa = joy.get_axis(0)
            if xa > 0.5:
                vol = min(1.0, vol + STEP); is_muted = False; moved = True
            elif xa < -0.5:
                vol = max(0.0, vol - STEP); moved = True
            if moved:
                play_sfx('navigate'); last_joy = now

        # Live preview volume
        preview_vol = 0.0 if is_muted else vol
        for s in SFX.values():
            s.set_volume(preview_vol)

        screen.fill(BG)
        ps = F['lg'].render(prompt, True, HDR)
        screen.blit(ps, ps.get_rect(center=(W // 2, H // 4)))

        # Mute status
        if is_muted:
            ms = F['md_b'].render("MUTED", True, DANGER)
        else:
            pct = int(vol * 100)
            ms = F['md_b'].render(f"Volume: {pct}%", True, TXT_SEL)
        screen.blit(ms, ms.get_rect(center=(W // 2, H // 3)))

        # Slider bar
        bw = int(W * 0.6)
        bh = scaled(16)
        bx = (W - bw) // 2
        by = H // 2
        pygame.draw.rect(screen, BAR_BG, (bx, by, bw, bh), border_radius=scaled(8))
        pygame.draw.rect(screen, BAR_BORDER, (bx, by, bw, bh), 1, border_radius=scaled(8))
        if not is_muted and vol > 0:
            fw = max(4, int((bw - 4) * vol))
            pygame.draw.rect(screen, ACCENT, (bx + 2, by + 2, fw, bh - 4), border_radius=scaled(6))
        # Slider knob
        knob_x = bx + 2 + int((bw - 4) * vol)
        pygame.draw.circle(screen, TXT_SEL, (knob_x, by + bh // 2), scaled(10))

        draw_hint_bar("[D-Pad] Adjust   [X] Mute/Unmute   [A] Confirm   [B] Cancel")
        pygame.display.flip()
        clock.tick(30)



# ═══ File Browser ═══════════════════════════════════════════════

def _platform_root():
    """Return filesystem root for the current platform."""
    if IS_WINDOWS:
        return os.environ.get('SystemDrive', 'C:') + os.sep
    return "/"


def _get_windows_drives():
    """Return list of available Windows drive roots like ['C:\\', 'D:\\', ...]."""
    if not IS_WINDOWS:
        return []
    drives = []
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        drive = f"{letter}:\\"
        if os.path.exists(drive):
            drives.append(drive)
    return drives


def _is_drive_root(path):
    """Check if a path is a Windows drive root (e.g. C:\\)."""
    if not IS_WINDOWS:
        return path == '/'
    return os.path.splitdrive(path)[1] in ('\\', '/', '')


def _drive_selector():
    """Windows drive picker. Returns selected drive root or None."""
    drives = _get_windows_drives()
    if not drives:
        return None
    sel = 0
    last_joy = 0

    while True:
        now = time.time()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    play_sfx('back'); return None
                if ev.key == pygame.K_UP:
                    sel = max(0, sel - 1); play_sfx('navigate')
                if ev.key == pygame.K_DOWN:
                    sel = min(len(drives) - 1, sel + 1); play_sfx('navigate')
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    play_sfx('select'); return drives[sel]
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"]:
                    play_sfx('select'); return drives[sel]
                if ev.button == BTN["back"]:
                    play_sfx('back'); return None
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hy == 1:
                    sel = max(0, sel - 1); play_sfx('navigate')
                if hy == -1:
                    sel = min(len(drives) - 1, sel + 1); play_sfx('navigate')

        if joy is not None and now - last_joy > DPAD_DELAY / 1000:
            moved = False
            ya = joy.get_axis(1)
            if ya < -0.5:
                sel = max(0, sel - 1); moved = True
            elif ya > 0.5:
                sel = min(len(drives) - 1, sel + 1); moved = True
            if moved:
                play_sfx('navigate'); last_joy = now

        screen.fill(BG)
        draw_header("Select Drive", None, f"{len(drives)} drives")

        items = [(d, False) for d in drives]
        draw_list(items, sel, 0)

        draw_hint_bar("[A] Select drive   [B] Cancel")
        pygame.display.flip()
        clock.tick(30)


def file_browser(prompt="Select a folder", start_path=None, mode="folder"):
    """Controller-driven file/folder browser.
       mode='folder' = select folders, mode='file' = select files.
       A = enter folder (or select file in file mode)
       B = go up one level (stays at root, never exits)
       X = select this folder (folder mode) or select file (file mode)
       Y = type a path manually (works anytime)
       L1 = toggle hidden files/folders
       Returns selected path string or None if cancelled."""
    if start_path is None:
        start_path = os.path.expanduser("~")
    current = os.path.expanduser(start_path)
    if not os.path.isdir(current):
        current = os.path.expanduser("~")
    sel = 0
    scroll = 0
    last_joy = 0
    show_hidden = False
    VIS = (H - scaled(100)) // LINE_H

    while True:
        # Build directory listing
        try:
            raw = sorted(os.listdir(current), key=str.lower)
        except OSError:
            raw = []
        if show_hidden:
            dirs = [d for d in raw if os.path.isdir(os.path.join(current, d))]
        else:
            dirs = [d for d in raw if os.path.isdir(os.path.join(current, d)) and not d.startswith('.')]
        files = []
        if mode == "file":
            if show_hidden:
                files = [f for f in raw if os.path.isfile(os.path.join(current, f))]
            else:
                files = [f for f in raw if os.path.isfile(os.path.join(current, f)) and not f.startswith('.')]
        entries = dirs + files
        n_dirs = len(dirs)
        sel = max(0, min(sel, len(entries) - 1)) if entries else 0

        now = time.time()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    play_sfx('back')
                    parent = os.path.dirname(current)
                    if parent != current:
                        current = parent; sel = 0; scroll = 0
                    elif IS_WINDOWS:
                        # Show drive selector
                        drive = _drive_selector()
                        if drive:
                            current = drive; sel = 0; scroll = 0
                    else:
                        play_sfx('error')  # Already at Linux root
                if ev.key == pygame.K_UP:
                    sel = max(0, sel - 1); play_sfx('navigate')
                if ev.key == pygame.K_DOWN and entries:
                    sel = min(len(entries) - 1, sel + 1); play_sfx('navigate')
                if ev.key == pygame.K_LEFT or ev.key == pygame.K_PAGEUP:
                    sel = max(0, sel - VIS); play_sfx('navigate')
                if (ev.key == pygame.K_RIGHT or ev.key == pygame.K_PAGEDOWN) and entries:
                    sel = min(len(entries) - 1, sel + VIS); play_sfx('navigate')
                if ev.key == pygame.K_RETURN and entries:
                    full = os.path.join(current, entries[sel])
                    if sel < n_dirs:
                        current = full; sel = 0; scroll = 0; play_sfx('select')
                    elif mode == "file":
                        play_sfx('select'); return full
                if ev.key == pygame.K_x or ev.key == pygame.K_SPACE:
                    if mode == "folder":
                        play_sfx('select'); return current
                    elif mode == "file" and entries and sel >= n_dirs:
                        play_sfx('select'); return os.path.join(current, entries[sel])
                if ev.key == pygame.K_h:  # Toggle hidden
                    show_hidden = not show_hidden; sel = 0; scroll = 0; play_sfx('navigate')
                if ev.key == pygame.K_y:  # Manual path
                    play_sfx('select')
                    typed = on_screen_keyboard("Enter path manually")
                    if typed:
                        typed = os.path.expanduser(typed)
                        if os.path.isdir(typed):
                            if mode == "folder":
                                return typed
                            else:
                                current = typed; sel = 0; scroll = 0
                        elif os.path.isfile(typed) and mode == "file":
                            return typed
                        else:
                            play_sfx('error')
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"] and entries:  # A = enter folder / select file
                    full = os.path.join(current, entries[sel])
                    if sel < n_dirs:
                        current = full; sel = 0; scroll = 0; play_sfx('select')
                    elif mode == "file":
                        play_sfx('select'); return full
                if ev.button == BTN["back"]:  # B = go up one level (never exits)
                    play_sfx('back')
                    parent = os.path.dirname(current)
                    if parent != current:
                        current = parent; sel = 0; scroll = 0
                    elif IS_WINDOWS:
                        # Show drive selector
                        drive = _drive_selector()
                        if drive:
                            current = drive; sel = 0; scroll = 0
                    else:
                        play_sfx('error')  # Already at Linux root
                if ev.button == BTN["extra"]:  # X = select this folder/file
                    if mode == "folder":
                        play_sfx('select'); return current
                    elif mode == "file" and entries and sel >= n_dirs:
                        play_sfx('select'); return os.path.join(current, entries[sel])
                if ev.button == BTN["alt"]:  # Y = manual path input (anytime)
                    play_sfx('select')
                    typed = on_screen_keyboard("Enter path manually")
                    if typed:
                        typed = os.path.expanduser(typed)
                        if os.path.isdir(typed):
                            if mode == "folder":
                                return typed
                            else:
                                current = typed; sel = 0; scroll = 0
                        elif os.path.isfile(typed) and mode == "file":
                            return typed
                        else:
                            play_sfx('error')
                if ev.button == BTN["shoulder_l"]:  # L1 = toggle hidden files
                    show_hidden = not show_hidden; sel = 0; scroll = 0; play_sfx('navigate')
                if ev.button == BTN["start"]:  # Start = cancel browser, return None
                    play_sfx('back'); return None
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hy == 1:
                    sel = max(0, sel - 1); play_sfx('navigate')
                if hy == -1 and entries:
                    sel = min(len(entries) - 1, sel + 1); play_sfx('navigate')
                if hx == -1:
                    sel = max(0, sel - VIS); play_sfx('navigate')
                if hx == 1 and entries:
                    sel = min(len(entries) - 1, sel + VIS); play_sfx('navigate')

        # Analog stick
        if joy is not None and entries and now - last_joy > DPAD_DELAY / 1000:
            moved = False
            ya = joy.get_axis(1)
            xa = joy.get_axis(0)
            if ya < -0.5:
                sel = max(0, sel - 1); moved = True
            elif ya > 0.5:
                sel = min(len(entries) - 1, sel + 1); moved = True
            if xa < -0.5:
                sel = max(0, sel - VIS); moved = True
            elif xa > 0.5:
                sel = min(len(entries) - 1, sel + VIS); moved = True
            if moved:
                play_sfx('navigate'); last_joy = now

        # Scroll
        if sel < scroll:
            scroll = sel
        if sel >= scroll + VIS:
            scroll = sel - VIS + 1

        # Draw
        screen.fill(BG)
        ps = F['lg'].render(prompt, True, HDR)
        screen.blit(ps, (scaled(12), scaled(6)))
        # Current path + hidden indicator
        path_suffix = "  [showing hidden]" if show_hidden else ""
        display_path = truncate(current + path_suffix, F['sm'], W - scaled(24))
        pp = F['sm'].render(display_path, True, ACCENT)
        screen.blit(pp, (scaled(12), scaled(30)))
        pygame.draw.line(screen, ACCENT, (scaled(10), scaled(48)), (W - scaled(10), scaled(48)), 1)

        # Entries
        y = scaled(52)
        for i in range(scroll, min(scroll + VIS, len(entries))):
            ey = y + (i - scroll) * LINE_H
            name = entries[i]
            is_dir = i < n_dirs
            is_sel = (i == sel)
            is_hidden = name.startswith('.')

            if is_sel:
                pygame.draw.rect(screen, SEL_BG, (scaled(6), ey, W - scaled(12), LINE_H - 2), border_radius=scaled(4))

            prefix = "[DIR] " if is_dir else "      "
            display = truncate(prefix + name, F['md'], W - scaled(30))
            if is_hidden:
                color = TXT_DIM if not is_sel else TXT_SEL
            else:
                color = TXT_SEL if is_sel else (HDR if is_dir else TXT)
            font = F['md_b'] if is_sel else F['md']
            label = font.render(display, True, color)
            screen.blit(label, (scaled(14), ey + scaled(3)))

        if not entries:
            es = F['md'].render("(empty folder)", True, TXT_DIM)
            screen.blit(es, es.get_rect(center=(W // 2, H // 2)))

        # Bottom hints
        if mode == "folder":
            hint_text = "[A] Open   [B] Back   [X] Select folder   [Y] Type path   [L1] Hidden"
        else:
            hint_text = "[A] Open/Select   [B] Back   [X] Select   [Y] Type path   [L1] Hidden"
        draw_hint_bar(hint_text)
        pygame.display.flip()
        clock.tick(30)


# ═══ First-Time Setup Wizard ═══════════════════════════════════

def _load_checker_detected():
    """Load auto-detected paths from the pre-launcher's checker.json."""
    checker_path = os.path.join(APP_DIR, 'checker.json')
    if os.path.exists(checker_path):
        try:
            with open(checker_path) as f:
                data = json.load(f)
            return data.get('custom_paths', {})
        except Exception:
            pass
    return {}


def _find_ps2_core(retroarch_path=None):
    """Try to auto-detect a PS2 core from RetroArch's cores directory."""
    core_ext = '.dll' if IS_WINDOWS else '.so'
    ps2_core_names = [
        f'pcsx2_libretro{core_ext}',
        f'pcsx2_hw_libretro{core_ext}',
    ]
    search_dirs = []

    # From detected RetroArch path
    if retroarch_path and os.path.isfile(retroarch_path):
        ra_dir = os.path.dirname(retroarch_path)
        search_dirs.append(os.path.join(ra_dir, 'cores'))
        search_dirs.append(os.path.join(os.path.dirname(ra_dir), 'cores'))

    # Common default locations
    if IS_WINDOWS:
        pf = os.environ.get('PROGRAMFILES', '')
        pf86 = os.environ.get('PROGRAMFILES(X86)', '')
        search_dirs.extend([
            os.path.join(pf, 'RetroArch', 'cores'),
            os.path.join(pf86, 'RetroArch', 'cores'),
            os.path.join(pf, 'RetroArch-Win64', 'cores'),
            os.path.join(pf86, 'RetroArch-Win64', 'cores'),
        ])
    else:
        search_dirs.extend([
            os.path.expanduser('~/.config/retroarch/cores'),
            '/usr/lib/libretro',
            '/usr/lib64/libretro',
            '/usr/share/libretro/cores',
        ])

    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for name in ps2_core_names:
            full = os.path.join(d, name)
            if os.path.isfile(full):
                return full
    return None


def first_time_setup():
    """Guided first-time setup wizard. Populates global config."""
    global active_cfg
    cfg = load_global_config()

    # Check for auto-detected paths from pre-launcher
    detected = _load_checker_detected()
    ra_path = detected.get('retroarch')
    auto_core = _find_ps2_core(ra_path)
    can_auto = bool(auto_core)
    use_auto = False

    # -- Step 1: Welcome + Auto-config offer --
    sel = 0  # 0 = auto (if available), 1 = manual
    if not can_auto:
        sel = 0  # Only manual available
    waiting = True
    while waiting:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    play_sfx('select'); waiting = False
                    use_auto = can_auto and sel == 0
                if can_auto:
                    if ev.key == pygame.K_UP:
                        sel = max(0, sel - 1); play_sfx('navigate')
                    if ev.key == pygame.K_DOWN:
                        sel = min(1, sel + 1); play_sfx('navigate')
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"]:
                    play_sfx('select'); waiting = False
                    use_auto = can_auto and sel == 0
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if can_auto:
                    if hy == 1: sel = max(0, sel - 1); play_sfx('navigate')
                    if hy == -1: sel = min(1, sel + 1); play_sfx('navigate')

        screen.fill(BG)
        ts = F['xl'].render("Welcome to PS2 Picker", True, HDR)
        screen.blit(ts, ts.get_rect(center=(W // 2, H // 5)))
        ss = F['md'].render("Let's get everything set up.", True, TXT)
        screen.blit(ss, ss.get_rect(center=(W // 2, H // 5 + scaled(35))))

        if can_auto:
            # Show auto-config option
            auto_label = "Automatic Setup (recommended)"
            auto_desc = f"PS2 core detected at: {os.path.basename(auto_core)}"
            manual_label = "Manual Setup (5 steps)"
            manual_desc = "Configure each setting yourself"

            y_base = H // 2 - scaled(20)

            # Auto option
            if sel == 0:
                pygame.draw.rect(screen, SEL_BG, (scaled(30), y_base - scaled(4),
                                 W - scaled(60), scaled(44)), border_radius=scaled(6))
            opt0 = F['md_b'].render(auto_label, True, TXT_SEL if sel == 0 else TXT)
            screen.blit(opt0, (scaled(50), y_base))
            d0 = F['sm'].render(auto_desc, True, SUCCESS if sel == 0 else TXT_DIM)
            screen.blit(d0, (scaled(50), y_base + scaled(20)))

            y_base += scaled(56)

            # Manual option
            if sel == 1:
                pygame.draw.rect(screen, SEL_BG, (scaled(30), y_base - scaled(4),
                                 W - scaled(60), scaled(44)), border_radius=scaled(6))
            opt1 = F['md_b'].render(manual_label, True, TXT_SEL if sel == 1 else TXT)
            screen.blit(opt1, (scaled(50), y_base))
            d1 = F['sm'].render(manual_desc, True, HINT if sel == 1 else TXT_DIM)
            screen.blit(d1, (scaled(50), y_base + scaled(20)))

        else:
            # No auto available — show manual steps
            steps_info = [
                "1. Choose where your PS2 games are stored",
                "2. Locate your RetroArch PS2 core",
                "3. Set up game cache preferences",
                "4. Adjust audio volume",
                "5. Create your first profile",
            ]
            for i, step_text in enumerate(steps_info):
                color = HINT if i > 0 else TXT_SEL
                st = F['md'].render(step_text, True, color)
                screen.blit(st, (scaled(40), H // 2 + i * scaled(28)))

        hint = F['sm'].render("Press [A] to select", True, ACCENT)
        pulse = 0.4 + 0.6 * abs(math.sin(time.time() * 2.5))
        hint.set_alpha(int(255 * pulse))
        screen.blit(hint, hint.get_rect(center=(W // 2, H - scaled(25))))
        pygame.display.flip()
        clock.tick(30)

    if use_auto:
        # ─── Automatic Setup: only ROM dir + username ───
        cfg["core_path"] = auto_core
        cfg["local_cache_dir"] = os.path.expanduser("~/ps2-cache")
        cfg["max_cached_games"] = 3
        cfg["volume"] = 0.5
        cfg["muted"] = False

        # -- ROM directory (still required) --
        while True:
            draw_center_msg("Step 1 of 2", "Where are your PS2 games stored?",
                            "Browse to your ROM folder, or press [Y] to type the path")
            time.sleep(0.8)
            rom_path = file_browser("Select your ROM/Games folder", start_path=os.path.expanduser("~"))
            if rom_path and os.path.isdir(rom_path):
                cfg["rom_dir"] = rom_path
                break
            draw_center_msg("Step 1 of 2", "No folder selected.",
                            "Press any button to try again")
            _wait_any_button()

        # -- Create first user (required) --
        while True:
            draw_center_msg("Step 2 of 2", "Create your profile", "")
            time.sleep(0.6)
            name = on_screen_keyboard("Enter your username")
            if name and name.strip():
                name = name.strip()
                if name not in get_users():
                    create_user(name)
                break
            draw_center_msg("Step 2 of 2", "A username is required.",
                            "Press any button to try again")
            _wait_any_button()

    else:
        # ─── Manual Setup: full 5-step wizard ───

        # -- Step 1: ROM directory (required) --
        while True:
            draw_center_msg("Step 1 of 5", "Where are your PS2 games stored?",
                            "Browse to your ROM folder, or press [Y] to type the path")
            time.sleep(0.8)
            rom_path = file_browser("Select your ROM/Games folder", start_path=os.path.expanduser("~"))
            if rom_path and os.path.isdir(rom_path):
                cfg["rom_dir"] = rom_path
                break
            draw_center_msg("Step 1 of 5", "No folder selected.",
                            "Press any button to try again")
            _wait_any_button()

        # -- Step 2: Core path (required) --
        while True:
            if IS_WINDOWS:
                core_hint = "Locate your RetroArch PS2 core (.dll file)"
                tip_hint = "Tip: usually in Program Files/RetroArch/cores"
            else:
                core_hint = "Locate your RetroArch PS2 core (.so file)"
                tip_hint = "Tip: press [L1] to show hidden folders like .config"
            draw_center_msg("Step 2 of 5", core_hint, tip_hint)
            time.sleep(0.8)
            if IS_WINDOWS:
                default_cores = os.path.join(os.environ.get('PROGRAMFILES', ''), 'RetroArch', 'cores')
            else:
                default_cores = os.path.expanduser("~/.config/retroarch/cores")
            core_start = default_cores if os.path.isdir(default_cores) else os.path.expanduser("~")
            core_ext = ".dll" if IS_WINDOWS else ".so"
            core_path = file_browser(f"Select PS2 core file ({core_ext})", start_path=core_start, mode="file")
            if core_path and os.path.isfile(core_path):
                cfg["core_path"] = core_path
                break
            draw_center_msg("Step 2 of 5", "No core file selected.",
                            "Press any button to try again")
            _wait_any_button()

        # -- Step 3: Cache directory + max cached --
        draw_center_msg("Step 3 of 5", "Game cache settings",
                        "Games are extracted to a local cache for faster loading")
        time.sleep(1.0)

        default_cache = os.path.expanduser("~/ps2-cache")
        use_default = confirm_dialog(f"Use default cache: {default_cache}?")
        if use_default:
            cfg["local_cache_dir"] = default_cache
        else:
            while True:
                cache_path = file_browser("Select cache folder", start_path=os.path.expanduser("~"))
                if cache_path and os.path.isdir(cache_path):
                    cfg["local_cache_dir"] = cache_path
                    break
                draw_center_msg("Step 3 of 5", "No folder selected.",
                                "Press any button to try again")
                _wait_any_button()

        result = number_input("Max games to keep cached?",
                              cfg.get("max_cached_games", 3), min_val=1, max_val=20)
        if result is not None:
            cfg["max_cached_games"] = result

        # -- Step 4: Volume --
        draw_center_msg("Step 4 of 5", "Set your preferred audio level", "")
        time.sleep(0.6)
        vol_result = volume_slider("Navigation Sound Volume",
                                   cfg.get("volume", 0.5),
                                   cfg.get("muted", False))
        if vol_result is not None:
            cfg["volume"], cfg["muted"] = vol_result

        # -- Step 5: Create first user (required) --
        while True:
            draw_center_msg("Step 5 of 5", "Create your profile", "")
            time.sleep(0.6)
            name = on_screen_keyboard("Enter your username")
            if name and name.strip():
                name = name.strip()
                if name not in get_users():
                    create_user(name)
                break
            draw_center_msg("Step 5 of 5", "A username is required.",
                            "Press any button to try again")
            _wait_any_button()

    # -- Save --
    cfg["setup_complete"] = True
    save_global_config(cfg)
    active_cfg = cfg
    apply_volume()
    reload_games()

    # Confirmation
    mode_text = "(auto-configured)" if use_auto else "(manual setup)"
    draw_center_msg("Setup Complete!", f"Found {len(games)} games {mode_text}",
                    "Press any button to continue")
    _wait_any_button()


# ═══ Settings Menu ══════════════════════════════════════════════

def settings_menu(username=None):
    """Settings menu accessible from game picker. Edits per-user or global settings."""
    _need_fade = True
    items = [
        ("Volume", "volume"),
        ("ROM Folder", "rom_dir"),
        ("RetroArch Core", "core_path"),
        ("Cache Folder", "local_cache_dir"),
        ("Max Cached Games", "max_cached_games"),
        ("Manage Cache", "cache_manager"),
        ("Theme Colors", "theme"),
        ("Controller Mapping", "controller_map"),
        ("Update Channel", "update_channel"),
        ("Back", "back"),
    ]
    setting_hints = {
        "volume":          "Adjust UI sound effects volume or mute entirely",
        "rom_dir":         "Folder containing your PS2 game archives (.zip, .7z, .iso, .chd)",
        "core_path":       "Path to the PCSX2 RetroArch core (.so or .dll)",
        "local_cache_dir": "Where extracted games are stored for faster loading",
        "max_cached_games":"Number of extracted games to keep before oldest is removed (1-20)",
        "cache_manager":   "View and delete cached games to free up disk space",
        "theme":           "Choose a color preset or create your own custom theme",
        "controller_map":  "Remap controller buttons for all menu actions",
        "update_channel":  "Toggle between Stable and Testing update branches",
        "back":            "Return to the game list",
    }
    sel = 0
    last_joy = 0

    while True:
        now = time.time()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    play_sfx('back'); fade_to_black(); return
                if ev.key == pygame.K_UP:
                    sel = max(0, sel - 1); play_sfx('navigate')
                if ev.key == pygame.K_DOWN:
                    sel = min(len(items) - 1, sel + 1); play_sfx('navigate')
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    play_sfx('select')
                    _handle_setting(items[sel][1], username)
                    if items[sel][1] == "back":
                        return
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"]:
                    play_sfx('select')
                    _handle_setting(items[sel][1], username)
                    if items[sel][1] == "back":
                        fade_to_black(); return
                if ev.button == BTN["back"]:
                    play_sfx('back'); fade_to_black(); return
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hy == 1:
                    sel = max(0, sel - 1); play_sfx('navigate')
                if hy == -1:
                    sel = min(len(items) - 1, sel + 1); play_sfx('navigate')

        if joy is not None and now - last_joy > DPAD_DELAY / 1000:
            moved = False
            ya = joy.get_axis(1)
            if ya < -0.5:
                sel = max(0, sel - 1); moved = True
            elif ya > 0.5:
                sel = min(len(items) - 1, sel + 1); moved = True
            if moved:
                play_sfx('navigate'); last_joy = now

        screen.fill(BG)
        title = f"Settings - {username}" if username else "Global Settings"
        draw_header(title, "[A] Edit   [B] Back", f"v{VERSION}")

        y = scaled(60)
        for i, (label, key) in enumerate(items):
            is_sel = (i == sel)
            rect = pygame.Rect(scaled(20), y, W - scaled(40), scaled(38))
            if is_sel:
                pygame.draw.rect(screen, SEL_BG, rect, border_radius=scaled(6))
            pygame.draw.rect(screen, ACCENT if is_sel else BAR_BG, rect, 1, border_radius=scaled(6))

            lbl = F['md_b' if is_sel else 'md'].render(label, True, TXT_SEL if is_sel else TXT)
            screen.blit(lbl, (rect.x + scaled(12), rect.y + scaled(4)))

            # Show current value for editable settings (skip submenu-only items)
            if key not in ("back", "theme", "controller_map", "update_channel"):
                val = active_cfg.get(key, "")
                if key == "volume":
                    if active_cfg.get("muted", False):
                        val_str = "Muted"
                    else:
                        val_str = f"{int(active_cfg.get('volume', 0.5) * 100)}%"
                elif key == "max_cached_games":
                    val_str = str(val)
                else:
                    val_str = truncate(str(val), F['sm'], W // 2 - scaled(20)) if val else "(not set)"
                vs = F['sm'].render(val_str, True, ACCENT if is_sel else TXT_DIM)
                screen.blit(vs, (rect.right - vs.get_width() - scaled(12), rect.y + scaled(10)))
            elif key == "update_channel":
                ch_val = active_cfg.get("update_channel", 0)
                ch_label = "Testing" if ch_val == 1 else "Stable"
                ch_color = (220, 160, 50) if ch_val == 1 else SUCCESS
                ch_surf = F['sm'].render(ch_label, True, ch_color if is_sel else TXT_DIM)
                screen.blit(ch_surf, (rect.right - ch_surf.get_width() - scaled(12),
                                      rect.y + ROW_H_S // 2 - ch_surf.get_height() // 2))
            elif key in ("theme", "controller_map", "cache_manager"):
                # Show a chevron to indicate submenu
                if key == "cache_manager":
                    manifest = load_cache_manifest()
                    count = len(manifest)
                    info = F['sm'].render(f"{count} game{'s' if count != 1 else ''}", True, ACCENT if is_sel else TXT_DIM)
                    screen.blit(info, (rect.right - info.get_width() - scaled(28), rect.y + scaled(10)))
                chev = F['md'].render("\u203A", True, ACCENT if is_sel else TXT_DIM)
                screen.blit(chev, (rect.right - chev.get_width() - scaled(12), rect.y + scaled(4)))

            y += scaled(44)

        current_key = items[sel][1]
        hint = setting_hints.get(current_key, "")
        if username:
            hint += "  (per-user)"
        draw_hint_bar(hint)
        pygame.display.flip()
        if _need_fade:
            fade_from_black(); _need_fade = False
        clock.tick(30)


# ═══ Controller Mapping Submenu ══════════════════════════════

def _capture_button(action_label):
    """Wait for the user to hold a joystick button for 2 seconds.
    Returns the button number, or None if cancelled.
    Keyboard Escape always cancels (safety). Keyboard is never captured."""
    held_button = None
    hold_start = 0
    HOLD_DURATION = 2.0

    while True:
        now = time.time()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            # Keyboard Escape always cancels (safety fallback)
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                play_sfx('back'); return None
            # Joystick button pressed — start hold timer
            if ev.type == pygame.JOYBUTTONDOWN:
                held_button = ev.button
                hold_start = now
            # Joystick button released — reset if it was the held one
            if ev.type == pygame.JOYBUTTONUP:
                if ev.button == held_button:
                    held_button = None

        # Check if held long enough
        if held_button is not None:
            elapsed = now - hold_start
            if elapsed >= HOLD_DURATION:
                play_sfx('select')
                return held_button
            progress = elapsed / HOLD_DURATION
        else:
            progress = 0.0

        # Draw
        screen.fill(BG)
        draw_header(f"Remap: {action_label}", "")

        # Instruction
        inst = F['md'].render("Hold a button for 2 seconds to assign", True, TXT)
        screen.blit(inst, inst.get_rect(center=(W // 2, H // 3)))

        # Show which button is being held
        if held_button is not None:
            btn_text = f"Holding: {btn_name(held_button)}"
            bt = F['lg'].render(btn_text, True, HDR)
            screen.blit(bt, bt.get_rect(center=(W // 2, H // 2 - scaled(10))))

            # Progress bar
            bar_w = scaled(300)
            bar_h = scaled(20)
            bar_x = (W - bar_w) // 2
            bar_y = H // 2 + scaled(20)
            pygame.draw.rect(screen, BAR_BG, (bar_x, bar_y, bar_w, bar_h), border_radius=scaled(4))
            fill_w = int(bar_w * progress)
            if fill_w > 0:
                # Color transitions from accent to success
                fill_color = _blend(ACCENT, SUCCESS, progress)
                pygame.draw.rect(screen, fill_color, (bar_x, bar_y, fill_w, bar_h), border_radius=scaled(4))
            pygame.draw.rect(screen, ACCENT, (bar_x, bar_y, bar_w, bar_h), 2, border_radius=scaled(4))

            pct_text = f"{int(progress * 100)}%"
            pct = F['sm'].render(pct_text, True, TXT)
            screen.blit(pct, pct.get_rect(center=(W // 2, bar_y + bar_h + scaled(12))))
        else:
            wait_text = "Press and hold any button..."
            wt = F['md'].render(wait_text, True, HINT)
            pulse = 0.4 + 0.6 * abs(math.sin(now * 2.5))
            wt.set_alpha(int(255 * pulse))
            screen.blit(wt, wt.get_rect(center=(W // 2, H // 2)))

        draw_hint_bar("[Keyboard Esc] Cancel")
        pygame.display.flip()
        clock.tick(30)


def controller_mapping_submenu():
    """Controller remapping submenu with per-key and all-key reset."""
    actions = list(BTN_LABELS.keys())  # ordered action keys
    # Menu items: each action + Reset All + Back
    RESET_ALL_IDX = len(actions)
    BACK_IDX = len(actions) + 1
    total_items = len(actions) + 2

    sel = 0
    last_joy = 0
    scroll = 0
    # How many action rows visible (leave room for Reset All + Back + header/footer)
    max_visible = max(3, (H - scaled(130)) // scaled(44))

    while True:
        now = time.time()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    play_sfx('back'); return
                if ev.key == pygame.K_UP:
                    sel = max(0, sel - 1); play_sfx('navigate')
                if ev.key == pygame.K_DOWN:
                    sel = min(total_items - 1, sel + 1); play_sfx('navigate')
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    play_sfx('select')
                    _handle_mapping_action(sel, actions)
                if ev.key == pygame.K_DELETE or ev.key == pygame.K_BACKSPACE:
                    # Per-key reset via keyboard
                    if sel < len(actions):
                        _reset_single_mapping(actions[sel])
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"]:
                    play_sfx('select')
                    _handle_mapping_action(sel, actions)
                elif ev.button == BTN["back"]:
                    play_sfx('back'); return
                elif ev.button == BTN["extra"]:  # X = per-key reset
                    if sel < len(actions):
                        _reset_single_mapping(actions[sel])
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hy == 1: sel = max(0, sel - 1); play_sfx('navigate')
                if hy == -1: sel = min(total_items - 1, sel + 1); play_sfx('navigate')

        if joy is not None and now - last_joy > DPAD_DELAY / 1000:
            ya = joy.get_axis(1)
            if abs(ya) > 0.5:
                if ya < -0.5: sel = max(0, sel - 1)
                else: sel = min(total_items - 1, sel + 1)
                play_sfx('navigate'); last_joy = now

        # Auto-scroll
        if sel < scroll:
            scroll = sel
        if sel >= scroll + max_visible:
            scroll = sel - max_visible + 1

        # Draw
        screen.fill(BG)
        draw_header("Controller Mapping", "[A] Remap   [X] Reset Key   [B] Back")

        y = scaled(56)

        for vi in range(max_visible):
            idx = scroll + vi
            if idx >= total_items:
                break
            is_sel = (idx == sel)
            rect = pygame.Rect(scaled(16), y, W - scaled(32), scaled(38))

            if is_sel:
                pygame.draw.rect(screen, SEL_BG, rect, border_radius=scaled(6))
            pygame.draw.rect(screen, ACCENT if is_sel else BAR_BG, rect, 1, border_radius=scaled(6))

            if idx < len(actions):
                # Action row
                action = actions[idx]
                label = BTN_LABELS[action]
                current_btn = BTN[action]
                default_btn = DEFAULT_BUTTON_MAP[action]
                is_default = (current_btn == default_btn)

                # Action name
                lbl = F['md_b' if is_sel else 'md'].render(label, True, TXT_SEL if is_sel else TXT)
                screen.blit(lbl, (rect.x + scaled(10), rect.y + scaled(4)))

                # Current binding badge
                badge_text = btn_name(current_btn)
                badge_color = SUCCESS if is_default else HDR
                badge = F['md_b'].render(badge_text, True, badge_color)
                bx = rect.right - badge.get_width() - scaled(14)
                screen.blit(badge, (bx, rect.y + scaled(4)))

                # Modified indicator
                if not is_default:
                    mod = F['sm'].render("•", True, HINT)
                    screen.blit(mod, (bx - scaled(14), rect.y + scaled(3)))

            elif idx == RESET_ALL_IDX:
                lbl = F['md_b' if is_sel else 'md'].render("Reset All to Default", True, DANGER if is_sel else HINT)
                screen.blit(lbl, (rect.x + scaled(10), rect.y + scaled(4)))

            elif idx == BACK_IDX:
                lbl = F['md_b' if is_sel else 'md'].render("Back", True, TXT_SEL if is_sel else TXT)
                screen.blit(lbl, (rect.x + scaled(10), rect.y + scaled(4)))

            y += scaled(44)

        # Scroll indicators
        if scroll > 0:
            arr = F['sm'].render("▲ more", True, HINT)
            screen.blit(arr, arr.get_rect(center=(W // 2, scaled(50))))
        if scroll + max_visible < total_items:
            arr = F['sm'].render("▼ more", True, HINT)
            screen.blit(arr, arr.get_rect(center=(W // 2, y + scaled(4))))

        # Safety notice
        safety = F['sm'].render("Keyboard keys always work as fallback", True, TXT_DIM)
        screen.blit(safety, safety.get_rect(center=(W // 2, H - scaled(38))))

        draw_hint_bar("[A] Remap   [X] Reset This Key   [B] Back")
        pygame.display.flip()
        clock.tick(30)


def _handle_mapping_action(sel, actions):
    """Handle selection in the controller mapping submenu."""
    if sel < len(actions):
        # Remap this action
        action = actions[sel]
        label = BTN_LABELS[action]
        result = _capture_button(label)
        if result is not None:
            # Check for conflicts
            conflict_action = None
            for k, v in BTN.items():
                if v == result and k != action:
                    conflict_action = k
            if conflict_action:
                # Swap: give the conflicting action our old button
                old_btn = BTN[action]
                msg = (f"{btn_name(result)} is already used by "
                       f"{BTN_LABELS[conflict_action]}.\n"
                       f"Swap bindings?")
                if confirm_dialog(f"Swap {btn_name(result)}? "
                                  f"{BTN_LABELS[conflict_action]} will become {btn_name(old_btn)}"):
                    BTN[conflict_action] = old_btn
                    BTN[action] = result
                    save_button_map()
                # If declined, do nothing
            else:
                BTN[action] = result
                save_button_map()

    elif sel == len(actions):  # Reset All
        if confirm_dialog("Reset ALL controller buttons to default?"):
            for k in DEFAULT_BUTTON_MAP:
                BTN[k] = DEFAULT_BUTTON_MAP[k]
            save_button_map()

    else:  # Back
        return


def _reset_single_mapping(action):
    """Reset a single action to its default mapping with confirmation."""
    default = DEFAULT_BUTTON_MAP[action]
    current = BTN[action]
    if current == default:
        return  # Already default
    label = BTN_LABELS[action]
    if confirm_dialog(f"Reset {label} to {btn_name(default)}?"):
        BTN[action] = default
        save_button_map()


# ═══ Theme / Color Picker Submenu ═══════════════════════════════

def _rgb_to_hsv(r, g, b):
    """Convert RGB (0–255 each) to HSV (H:0–360, S:0–100, V:0–100)."""
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return int(h * 360), int(s * 100), int(v * 100)

def _hsv_to_rgb(h, s, v):
    """Convert HSV (H:0-360, S:0-100, V:0-100) to RGB (0-255 each)."""
    r, g, b = colorsys.hsv_to_rgb(h / 360, s / 100, v / 100)
    return _clamp(r * 255), _clamp(g * 255), _clamp(b * 255)


def color_slider(label, color_rgb):
    """HSV color slider for a single color. Returns new RGB tuple or None if cancelled."""
    h, s, v = _rgb_to_hsv(*color_rgb)
    sel = 0  # 0=Hue, 1=Sat, 2=Val
    channels = [h, s, v]
    maxes = [360, 100, 100]
    names = ["Hue", "Saturation", "Brightness"]
    last_joy = 0
    step = 1
    hold_time = 0
    held_dir = 0

    while True:
        now = time.time()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    play_sfx('back'); return None
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    play_sfx('select')
                    return _hsv_to_rgb(channels[0], channels[1], channels[2])
                if ev.key == pygame.K_UP:
                    sel = max(0, sel - 1); play_sfx('navigate')
                if ev.key == pygame.K_DOWN:
                    sel = min(2, sel + 1); play_sfx('navigate')
                if ev.key == pygame.K_LEFT:
                    channels[sel] = max(0, channels[sel] - 1)
                if ev.key == pygame.K_RIGHT:
                    channels[sel] = min(maxes[sel], channels[sel] + 1)
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"]:
                    play_sfx('select')
                    return _hsv_to_rgb(channels[0], channels[1], channels[2])
                if ev.button == BTN["back"]:
                    play_sfx('back'); return None
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hy == 1: sel = max(0, sel - 1); play_sfx('navigate')
                if hy == -1: sel = min(2, sel + 1); play_sfx('navigate')
                if hx == -1: channels[sel] = max(0, channels[sel] - 1)
                if hx == 1: channels[sel] = min(maxes[sel], channels[sel] + 1)

        # Analog stick for fine/fast control
        if joy is not None and now - last_joy > 0.04:
            xa = joy.get_axis(0)
            ya = joy.get_axis(1)
            if abs(xa) > 0.3:
                # Accelerate with hold
                if (xa > 0) == (held_dir > 0) and held_dir != 0:
                    hold_time += 0.04
                else:
                    hold_time = 0
                held_dir = 1 if xa > 0 else -1
                step = 1 + int(hold_time * 4)
                step = min(step, 10)
                delta = step if xa > 0 else -step
                channels[sel] = max(0, min(maxes[sel], channels[sel] + delta))
                last_joy = now
            else:
                held_dir = 0; hold_time = 0
            if abs(ya) > 0.5:
                if ya < -0.5: sel = max(0, sel - 1)
                elif ya > 0.5: sel = min(2, sel + 1)
                last_joy = now

        # Draw
        preview_rgb = _hsv_to_rgb(channels[0], channels[1], channels[2])
        screen.fill(BG)
        draw_header(f"Edit: {label}", "[A] Confirm   [B] Cancel")

        # Color preview swatch
        swatch_w, swatch_h = scaled(120), scaled(80)
        swatch_x = (W - swatch_w) // 2
        swatch_y = scaled(56)
        pygame.draw.rect(screen, preview_rgb, (swatch_x, swatch_y, swatch_w, swatch_h), border_radius=scaled(8))
        pygame.draw.rect(screen, ACCENT, (swatch_x, swatch_y, swatch_w, swatch_h), 2, border_radius=scaled(8))
        hex_str = f"#{preview_rgb[0]:02X}{preview_rgb[1]:02X}{preview_rgb[2]:02X}"
        hex_s = F['sm'].render(hex_str, True, TXT)
        screen.blit(hex_s, hex_s.get_rect(center=(W // 2, swatch_y + swatch_h + scaled(12))))

        # HSV sliders
        slider_y = swatch_y + swatch_h + scaled(30)
        for i in range(3):
            is_sel = (i == sel)
            y = slider_y + i * scaled(52)

            # Label
            lbl_color = HDR if is_sel else TXT
            lbl = F['md_b' if is_sel else 'md'].render(f"{names[i]}: {channels[i]}", True, lbl_color)
            screen.blit(lbl, (scaled(30), y))

            # Slider track
            track_x = scaled(30)
            track_w = W - scaled(60)
            track_y = y + scaled(22)
            track_h = scaled(16)

            if is_sel:
                pygame.draw.rect(screen, SEL_BG, (track_x - scaled(4), y - scaled(4),
                                 track_w + scaled(8), scaled(48)), border_radius=scaled(6))

            # Draw gradient track
            for px in range(track_w):
                t_val = px / track_w
                if i == 0:  # Hue rainbow
                    cr, cg, cb = colorsys.hsv_to_rgb(t_val, channels[1] / 100, channels[2] / 100)
                elif i == 1:  # Saturation
                    cr, cg, cb = colorsys.hsv_to_rgb(channels[0] / 360, t_val, channels[2] / 100)
                else:  # Value/brightness
                    cr, cg, cb = colorsys.hsv_to_rgb(channels[0] / 360, channels[1] / 100, t_val)
                pygame.draw.line(screen, (_clamp(cr * 255), _clamp(cg * 255), _clamp(cb * 255)),
                                 (track_x + px, track_y), (track_x + px, track_y + track_h))

            pygame.draw.rect(screen, ACCENT if is_sel else BAR_BG,
                             (track_x, track_y, track_w, track_h), 2, border_radius=scaled(3))

            # Handle/thumb
            thumb_x = track_x + int((channels[i] / maxes[i]) * track_w)
            pygame.draw.circle(screen, HDR if is_sel else TXT, (thumb_x, track_y + track_h // 2), scaled(8))
            pygame.draw.circle(screen, BG, (thumb_x, track_y + track_h // 2), scaled(5))

        draw_hint_bar("D-pad Left/Right to adjust, Up/Down to switch")
        pygame.display.flip()
        clock.tick(30)


def theme_submenu():
    """Theme color picker submenu. Presets + custom per-color editing."""
    items = [
        ("Preset Theme", "preset"),
        ("Background", "bg"),
        ("Accent", "accent"),
        ("Highlight", "highlight"),
        ("Text", "text"),
        ("Reset to Default", "reset"),
        ("Back", "back"),
    ]
    sel = 0
    last_joy = 0
    theme = get_current_theme()

    while True:
        now = time.time()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    play_sfx('back'); return
                if ev.key == pygame.K_UP:
                    sel = max(0, sel - 1); play_sfx('navigate')
                if ev.key == pygame.K_DOWN:
                    sel = min(len(items) - 1, sel + 1); play_sfx('navigate')
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    play_sfx('select')
                    key = items[sel][1]
                    if key == "back":
                        return
                    elif key == "reset":
                        theme = dict(DEFAULT_THEME)
                        apply_theme(theme)
                        save_theme_to_config(theme)
                    elif key == "preset":
                        result = preset_picker()
                        if result:
                            theme = result
                            apply_theme(theme)
                            save_theme_to_config(theme)
                    else:
                        result = color_slider(items[sel][0], tuple(theme[key]))
                        if result:
                            theme[key] = list(result)
                            apply_theme(theme)
                            save_theme_to_config(theme)
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"]:
                    play_sfx('select')
                    key = items[sel][1]
                    if key == "back":
                        return
                    elif key == "reset":
                        theme = dict(DEFAULT_THEME)
                        apply_theme(theme)
                        save_theme_to_config(theme)
                    elif key == "preset":
                        result = preset_picker()
                        if result:
                            theme = result
                            apply_theme(theme)
                            save_theme_to_config(theme)
                    else:
                        result = color_slider(items[sel][0], tuple(theme[key]))
                        if result:
                            theme[key] = list(result)
                            apply_theme(theme)
                            save_theme_to_config(theme)
                if ev.button == BTN["back"]:
                    play_sfx('back'); return
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hy == 1: sel = max(0, sel - 1); play_sfx('navigate')
                if hy == -1: sel = min(len(items) - 1, sel + 1); play_sfx('navigate')

        if joy is not None and now - last_joy > DPAD_DELAY / 1000:
            ya = joy.get_axis(1)
            if abs(ya) > 0.5:
                if ya < -0.5: sel = max(0, sel - 1)
                else: sel = min(len(items) - 1, sel + 1)
                play_sfx('navigate'); last_joy = now

        screen.fill(BG)
        draw_header("Theme Colors", "[A] Edit   [B] Back")

        y = scaled(56)
        for i, (label, key) in enumerate(items):
            is_sel = (i == sel)
            rect = pygame.Rect(scaled(20), y, W - scaled(40), scaled(38))
            if is_sel:
                pygame.draw.rect(screen, SEL_BG, rect, border_radius=scaled(6))
            pygame.draw.rect(screen, ACCENT if is_sel else BAR_BG, rect, 1, border_radius=scaled(6))

            lbl = F['md_b' if is_sel else 'md'].render(label, True, TXT_SEL if is_sel else TXT)
            screen.blit(lbl, (rect.x + scaled(12), rect.y + scaled(4)))

            # Color swatch preview for editable colors
            if key in ("bg", "accent", "highlight", "text"):
                swatch_size = scaled(22)
                sx = rect.right - swatch_size - scaled(12)
                sy = rect.y + (rect.height - swatch_size) // 2
                pygame.draw.rect(screen, tuple(theme[key]),
                                 (sx, sy, swatch_size, swatch_size), border_radius=scaled(4))
                pygame.draw.rect(screen, TXT,
                                 (sx, sy, swatch_size, swatch_size), 1, border_radius=scaled(4))

            y += scaled(44)

        # Live preview strip at bottom
        preview_y = H - scaled(60)
        pygame.draw.rect(screen, BG, (scaled(20), preview_y, W - scaled(40), scaled(32)), border_radius=scaled(6))
        pygame.draw.rect(screen, ACCENT, (scaled(20), preview_y, W - scaled(40), scaled(32)), 1, border_radius=scaled(6))
        prev_labels = [
            ("Sample", HDR), ("Text", TXT), ("Accent", ACCENT), ("Hint", HINT),
        ]
        px = scaled(35)
        for plbl, pcol in prev_labels:
            ps = F['sm'].render(plbl, True, pcol)
            screen.blit(ps, (px, preview_y + scaled(8)))
            px += ps.get_width() + scaled(20)

        draw_hint_bar("Changes apply instantly and are saved globally")
        pygame.display.flip()
        clock.tick(30)


def preset_picker():
    """Pick from built-in theme presets. Returns theme dict or None."""
    names = list(THEME_PRESETS.keys())
    sel = 0
    last_joy = 0

    while True:
        now = time.time()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    play_sfx('back')
                    load_theme_from_config()  # Restore previous theme
                    return None
                if ev.key == pygame.K_UP:
                    sel = max(0, sel - 1); play_sfx('navigate')
                    apply_theme(THEME_PRESETS[names[sel]])  # Live preview
                if ev.key == pygame.K_DOWN:
                    sel = min(len(names) - 1, sel + 1); play_sfx('navigate')
                    apply_theme(THEME_PRESETS[names[sel]])
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    play_sfx('select')
                    return dict(THEME_PRESETS[names[sel]])
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"]:
                    play_sfx('select')
                    return dict(THEME_PRESETS[names[sel]])
                if ev.button == BTN["back"]:
                    play_sfx('back')
                    # Restore previous theme before exiting
                    load_theme_from_config()
                    return None
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hy == 1:
                    sel = max(0, sel - 1); play_sfx('navigate')
                    apply_theme(THEME_PRESETS[names[sel]])
                if hy == -1:
                    sel = min(len(names) - 1, sel + 1); play_sfx('navigate')
                    apply_theme(THEME_PRESETS[names[sel]])

        if joy is not None and now - last_joy > DPAD_DELAY / 1000:
            ya = joy.get_axis(1)
            if abs(ya) > 0.5:
                if ya < -0.5: sel = max(0, sel - 1)
                else: sel = min(len(names) - 1, sel + 1)
                apply_theme(THEME_PRESETS[names[sel]])
                play_sfx('navigate'); last_joy = now

        screen.fill(BG)
        draw_header("Select Preset", "[A] Apply   [B] Cancel")

        y = scaled(56)
        for i, name in enumerate(names):
            is_sel = (i == sel)
            rect = pygame.Rect(scaled(20), y, W - scaled(40), scaled(38))
            if is_sel:
                pygame.draw.rect(screen, SEL_BG, rect, border_radius=scaled(6))
            pygame.draw.rect(screen, ACCENT if is_sel else BAR_BG, rect, 1, border_radius=scaled(6))

            lbl = F['md_b' if is_sel else 'md'].render(name, True, TXT_SEL if is_sel else TXT)
            screen.blit(lbl, (rect.x + scaled(12), rect.y + scaled(4)))

            # Mini swatches for preset colors
            preset = THEME_PRESETS[name]
            sx = rect.right - scaled(100)
            sy = rect.y + (rect.height - scaled(18)) // 2
            for j, ck in enumerate(("bg", "accent", "highlight", "text")):
                pygame.draw.rect(screen, tuple(preset[ck]),
                                 (sx + j * scaled(22), sy, scaled(18), scaled(18)),
                                 border_radius=scaled(3))
            y += scaled(44)

        # Live preview strip
        preview_y = H - scaled(60)
        pygame.draw.rect(screen, BG, (scaled(20), preview_y, W - scaled(40), scaled(32)), border_radius=scaled(6))
        pygame.draw.rect(screen, ACCENT, (scaled(20), preview_y, W - scaled(40), scaled(32)), 1, border_radius=scaled(6))
        prev_labels = [("Sample", HDR), ("Text", TXT), ("Accent", ACCENT), ("Hint", HINT)]
        px = scaled(35)
        for plbl, pcol in prev_labels:
            ps = F['sm'].render(plbl, True, pcol)
            screen.blit(ps, (px, preview_y + scaled(8)))
            px += ps.get_width() + scaled(20)

        draw_hint_bar("Navigate to preview, [A] to apply")
        pygame.display.flip()
        clock.tick(30)


def _handle_setting(key, username=None):
    """Handle editing a single setting."""
    if key == "back":
        return
    if key == "volume":
        result = volume_slider("Volume",
                               active_cfg.get("volume", 0.5),
                               active_cfg.get("muted", False))
        if result is not None:
            active_cfg["volume"], active_cfg["muted"] = result
            apply_volume()
    elif key == "rom_dir":
        start = active_cfg.get("rom_dir", "/")
        result = file_browser("Select ROM/Games folder", start_path=start)
        if result:
            active_cfg["rom_dir"] = result
            reload_games()
    elif key == "core_path":
        start = os.path.dirname(active_cfg.get("core_path", "")) or "/"
        core_ext = ".dll" if IS_WINDOWS else ".so"
        result = file_browser(f"Select PS2 core file ({core_ext})", start_path=start, mode="file")
        if result:
            active_cfg["core_path"] = result
    elif key == "local_cache_dir":
        start = active_cfg.get("local_cache_dir", os.path.expanduser("~"))
        result = file_browser("Select cache folder", start_path=start)
        if result:
            active_cfg["local_cache_dir"] = result
    elif key == "max_cached_games":
        result = number_input("Max cached games",
                              active_cfg.get("max_cached_games", 3),
                              min_val=1, max_val=20)
        if result is not None:
            active_cfg["max_cached_games"] = result
    elif key == "theme":
        theme_submenu()
        return  # Theme saves itself directly to global config
    elif key == "cache_manager":
        cache_manager_screen()
        return
    elif key == "controller_map":
        controller_mapping_submenu()
        return  # Mapping saves itself directly to global config
    elif key == "update_channel":
        cur = active_cfg.get("update_channel", 0)
        new_val = 1 if cur == 0 else 0
        new_label = "Testing" if new_val == 1 else "Stable"
        if confirm_dialog(f"Switch to {new_label} channel?\nThe app will restart to apply this change."):
            active_cfg["update_channel"] = new_val
            save_global_config(active_cfg)
            # Show restart message briefly
            screen.fill(BG)
            r1 = F['lg'].render(f"Switching to {new_label}", True, HDR)
            r2 = F['sm'].render("Restarting...", True, TXT_DIM)
            screen.blit(r1, r1.get_rect(center=(W // 2, H // 2 - scaled(10))))
            screen.blit(r2, r2.get_rect(center=(W // 2, H // 2 + scaled(14))))
            pygame.display.flip()
            time.sleep(1.5)
            pygame.quit()
            os.execv(sys.executable, [sys.executable] + sys.argv)
        return

    # Persist changes
    if username:
        gcfg = load_global_config()
        overrides = {}
        for k in ("volume", "muted", "rom_dir", "core_path", "local_cache_dir", "max_cached_games"):
            if active_cfg.get(k) != gcfg.get(k):
                overrides[k] = active_cfg[k]
        save_user_settings(username, overrides)
    else:
        save_global_config(active_cfg)



# ═══ Cache Management ══════════════════════════════════════════

def load_cache_manifest():
    """Load the JSON manifest tracking which games are currently cached."""
    cache_dir = active_cfg.get("local_cache_dir", os.path.expanduser("~/ps2-cache"))
    p = os.path.join(cache_dir, "manifest.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_cache_manifest(manifest):
    """Write the cache manifest dict to disk."""
    cache_dir = active_cfg.get("local_cache_dir", os.path.expanduser("~/ps2-cache"))
    os.makedirs(cache_dir, exist_ok=True)
    with open(os.path.join(cache_dir, "manifest.json"), 'w') as f:
        json.dump(manifest, f, indent=2)


def cache_manager_screen():
    """Themed cache manager — view and delete cached games.

    Visual style matches the rest of the app: themed header, animated
    selection, usage bar, fade transitions, 60fps.
    """
    manifest = load_cache_manifest()

    # Purge stale entries
    stale = [k for k, v in manifest.items()
             if not os.path.isdir(v.get("path", ""))]
    for k in stale:
        del manifest[k]
    if stale:
        save_cache_manifest(manifest)

    sel = 0
    scroll_y = 0.0
    target_scroll = 0.0
    last_joy = 0
    _need_fade = True
    start_time = time.time()

    HEADER_H = scaled(42)
    HINT_H = scaled(32)
    USAGE_H = scaled(22)        # space for usage bar below header
    ROW_H = scaled(56)
    ROW_GAP = scaled(4)
    LEFT_PAD = scaled(14)
    SCROLL_SPEED = 10.0

    def _entry_size(entry):
        """Calculate total size of a cache entry on disk."""
        entry_path = entry.get("path", "")
        total = 0
        if os.path.isdir(entry_path):
            for dp, _, fns in os.walk(entry_path):
                for fn in fns:
                    try:
                        total += os.path.getsize(os.path.join(dp, fn))
                    except OSError:
                        pass
        return total

    def _age_str(last_used):
        """Human-readable age string from a timestamp."""
        if last_used <= 0:
            return "never"
        age = time.time() - last_used
        if age < 3600:
            return f"{max(1, int(age / 60))}m ago"
        elif age < 86400:
            return f"{int(age / 3600)}h ago"
        else:
            return f"{int(age / 86400)}d ago"

    while True:
        now = time.time()
        t = now - start_time
        dt = clock.get_time() / 1000.0

        # Rebuild sorted entries each frame (list may shrink after deletion)
        entries = sorted(manifest.keys(),
                         key=lambda k: manifest[k].get("last_used", 0), reverse=True)
        total = len(entries)
        max_cached = active_cfg.get("max_cached_games", 3)

        # Calculate total cache size
        total_bytes = sum(_entry_size(v) for v in manifest.values())

        _dirty = False
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    play_sfx('back'); fade_to_black(); return
                if ev.key == pygame.K_UP and total:
                    sel = max(0, sel - 1); play_sfx('navigate')
                if ev.key == pygame.K_DOWN and total:
                    sel = min(total - 1, sel + 1); play_sfx('navigate')
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE) and total:
                    play_sfx('select')
                    name = entries[sel]
                    if confirm_dialog(f"Delete {name}?"):
                        cache_path = manifest[name].get("path", "")
                        if os.path.isdir(cache_path):
                            shutil.rmtree(cache_path, ignore_errors=True)
                        del manifest[name]
                        save_cache_manifest(manifest)
                        reload_games()
                        play_sfx('select')
                        sel = min(sel, max(0, len(manifest) - 1))
                    _dirty = True; break
                if ev.key == pygame.K_DELETE and total:
                    if confirm_dialog("Delete ALL cached games?"):
                        for name in list(manifest.keys()):
                            cache_path = manifest[name].get("path", "")
                            if os.path.isdir(cache_path):
                                shutil.rmtree(cache_path, ignore_errors=True)
                        manifest.clear()
                        save_cache_manifest(manifest)
                        reload_games()
                        sel = 0
                        play_sfx('select')
                    _dirty = True; break
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"] and total:
                    play_sfx('select')
                    name = entries[sel]
                    if confirm_dialog(f"Delete {name}?"):
                        cache_path = manifest[name].get("path", "")
                        if os.path.isdir(cache_path):
                            shutil.rmtree(cache_path, ignore_errors=True)
                        del manifest[name]
                        save_cache_manifest(manifest)
                        reload_games()
                        play_sfx('select')
                        sel = min(sel, max(0, len(manifest) - 1))
                    _dirty = True; break
                if ev.button == BTN["back"]:
                    play_sfx('back'); fade_to_black(); return
                if ev.button == BTN["alt"] and total:  # Y = delete all
                    if confirm_dialog("Delete ALL cached games?"):
                        for name in list(manifest.keys()):
                            cache_path = manifest[name].get("path", "")
                            if os.path.isdir(cache_path):
                                shutil.rmtree(cache_path, ignore_errors=True)
                        manifest.clear()
                        save_cache_manifest(manifest)
                        reload_games()
                        sel = 0
                        play_sfx('select')
                    _dirty = True; break
            if ev.type == pygame.JOYHATMOTION and total:
                hx, hy = ev.value
                if hy == 1:
                    sel = max(0, sel - 1); play_sfx('navigate')
                if hy == -1:
                    sel = min(total - 1, sel + 1); play_sfx('navigate')

        if _dirty:
            continue

        if joy is not None and total and now - last_joy > DPAD_DELAY / 1000:
            moved = False
            try:
                ya = joy.get_axis(1)
                if ya < -0.5:
                    sel = max(0, sel - 1); moved = True
                elif ya > 0.5:
                    sel = min(total - 1, sel + 1); moved = True
            except Exception:
                pass
            if moved:
                play_sfx('navigate'); last_joy = now

        # Smooth scroll tracking
        view_h = H - HEADER_H - USAGE_H - HINT_H
        content_h = total * (ROW_H + ROW_GAP) if total else 0
        sel_y = sel * (ROW_H + ROW_GAP)
        if sel_y < target_scroll:
            target_scroll = sel_y
        if sel_y + ROW_H > target_scroll + view_h:
            target_scroll = sel_y + ROW_H - view_h
        target_scroll = max(0, min(target_scroll, max(0, content_h - view_h)))
        scroll_y += (target_scroll - scroll_y) * min(1.0, dt * SCROLL_SPEED)

        # ─── Draw ───
        screen.fill(BG)

        # Themed header
        title_surf = F['lg'].render("Cache Manager", True, HDR)
        screen.blit(title_surf, (scaled(12), scaled(10)))
        sub_str = f"{total}/{max_cached} slots  \u2022  {_format_size(total_bytes)}"
        sub = F['sm'].render(sub_str, True, TXT_DIM)
        screen.blit(sub, (W - sub.get_width() - scaled(12), scaled(16)))
        pygame.draw.line(screen, _blend(BG, ACCENT, 0.5),
                         (scaled(8), HEADER_H - 1), (W - scaled(8), HEADER_H - 1), 1)
        pygame.draw.line(screen, _blend(BG, ACCENT, 0.25),
                         (scaled(8), HEADER_H + 1), (W - scaled(8), HEADER_H + 1), 1)

        # Cache usage bar (slots used)
        bar_left = scaled(12)
        bar_right = W - scaled(12)
        bar_w = bar_right - bar_left
        bar_h = scaled(5)
        bar_y = HEADER_H + scaled(3)
        pygame.draw.rect(screen, _blend(BG, BAR_BG, 0.4),
                         (bar_left, bar_y, bar_w, bar_h), border_radius=scaled(2))
        slot_pct = min(1.0, total / max(1, max_cached))
        used_w = max(scaled(2), int(bar_w * slot_pct)) if total > 0 else 0
        bar_color = ACCENT if slot_pct < 0.9 else (220, 80, 60)
        if used_w > 0:
            pygame.draw.rect(screen, bar_color,
                             (bar_left, bar_y, used_w, bar_h), border_radius=scaled(2))
        usage_label = f"{total} of {max_cached} slots used  \u2022  {_format_size(total_bytes)} on disk"
        usage_surf = F['sm'].render(usage_label, True, TXT_DIM)
        screen.blit(usage_surf, (bar_left, bar_y + bar_h + scaled(1)))

        content_top = HEADER_H + USAGE_H

        if total == 0:
            # Empty state
            msg = F['md'].render("No cached games", True, TXT_DIM)
            screen.blit(msg, msg.get_rect(center=(W // 2, H // 2 - scaled(10))))
            hint = F['sm'].render("Games are cached when extracted from archives", True, TXT_DIM)
            screen.blit(hint, hint.get_rect(center=(W // 2, H // 2 + scaled(14))))
        else:
            content_rect = pygame.Rect(0, content_top, W, view_h)
            screen.set_clip(content_rect)

            draw_y = content_top - int(scroll_y)
            for i, name in enumerate(entries):
                is_sel = (i == sel)
                ry = draw_y

                if ry + ROW_H > content_top and ry < H - HINT_H:
                    row_rect = pygame.Rect(LEFT_PAD, ry, W - LEFT_PAD * 2, ROW_H)

                    if is_sel:
                        # Animated selection glow
                        glow_alpha = int(80 + 40 * math.sin(t * 3))
                        glow_color = _blend(BG, ACCENT, glow_alpha / 255)
                        pygame.draw.rect(screen, glow_color,
                                         row_rect.inflate(scaled(4), scaled(2)),
                                         border_radius=scaled(6))
                        pygame.draw.rect(screen, SEL_BG, row_rect, border_radius=scaled(5))
                        # Accent side bar
                        bar_anim_h = int(ROW_H * (0.5 + 0.15 * math.sin(t * 4)))
                        accent_rect = pygame.Rect(row_rect.x, ry + (ROW_H - bar_anim_h) // 2,
                                                  scaled(3), bar_anim_h)
                        pygame.draw.rect(screen, ACCENT, accent_rect, border_radius=scaled(1))
                    else:
                        if i % 2 == 0:
                            pygame.draw.rect(screen, _blend(BG, BAR_BG, 0.3),
                                             row_rect, border_radius=scaled(4))
                        pygame.draw.rect(screen, _blend(BG, ACCENT, 0.12),
                                         row_rect, 1, border_radius=scaled(4))

                    # Disc icon
                    icon_cx = LEFT_PAD + scaled(24)
                    icon_cy = ry + ROW_H // 2
                    icon_r = scaled(12)
                    _icon_cache(screen, icon_cx, icon_cy, icon_r, t, is_sel)

                    # Game name
                    text_x = LEFT_PAD + scaled(46)
                    max_text_w = W - text_x - scaled(80)
                    title_font = F['md_b'] if is_sel else F['md']
                    title_color = HDR if is_sel else TXT
                    label_text = truncate(name, title_font, max_text_w)
                    label_surf = title_font.render(label_text, True, title_color)
                    screen.blit(label_surf, (text_x, ry + scaled(6)))

                    # Size + last used
                    entry = manifest[name]
                    es = _format_size(_entry_size(entry))
                    age = _age_str(entry.get("last_used", 0))
                    detail_text = f"{es}  \u2022  Last played: {age}"
                    detail_color = ACCENT if is_sel else TXT_DIM
                    detail_surf = F['sm'].render(detail_text, True, detail_color)
                    screen.blit(detail_surf, (text_x, ry + scaled(28)))

                    # Delete hint on selected row
                    if is_sel:
                        x_surf = F['sm'].render("\u2716", True, (200, 80, 70))
                        screen.blit(x_surf, (W - LEFT_PAD - scaled(18),
                                             ry + ROW_H // 2 - x_surf.get_height() // 2))

                # Separator line
                sep_y = ry + ROW_H + ROW_GAP // 2
                if ry + ROW_H > content_top and sep_y < H - HINT_H:
                    pygame.draw.line(screen, _blend(BG, ACCENT, 0.1),
                                     (LEFT_PAD + scaled(8), sep_y),
                                     (W - LEFT_PAD - scaled(8), sep_y), 1)

                draw_y += ROW_H + ROW_GAP

            screen.set_clip(None)

            # Scroll indicator
            if content_h > view_h:
                track_x = W - scaled(4)
                track_top = content_top
                track_h = view_h
                thumb_h = max(scaled(20), int(track_h * view_h / content_h))
                thumb_y = track_top + int((track_h - thumb_h) * scroll_y /
                                          max(1, content_h - view_h))
                pygame.draw.rect(screen, _blend(BG, ACCENT, 0.15),
                                 (track_x, track_top, scaled(3), track_h),
                                 border_radius=scaled(1))
                pygame.draw.rect(screen, _blend(BG, ACCENT, 0.5),
                                 (track_x, thumb_y, scaled(3), thumb_h),
                                 border_radius=scaled(1))

        hints = "[A] Delete   [Y] Clear All   [B] Back" if total else "[B] Back"
        draw_hint_bar(hints)

        pygame.display.flip()
        if _need_fade:
            fade_from_black(); _need_fade = False
        clock.tick(60)


def evict_cached_picker():
    """Interactive picker to choose which cached game to remove when cache is full."""
    max_cached = active_cfg.get("max_cached_games", 3)
    manifest = load_cache_manifest()

    # Purge stale entries (cache dir deleted from disk)
    stale = [k for k, v in manifest.items()
             if not os.path.isdir(v.get("path", ""))]
    for k in stale:
        del manifest[k]
    if stale:
        save_cache_manifest(manifest)

    if len(manifest) < max_cached:
        return True

    entries = sorted(manifest.keys(),
                     key=lambda k: manifest[k].get("last_used", 0))
    sel = 0
    scroll = 0
    VIS = 6
    last_joy = 0

    while True:
        now = time.time()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    play_sfx('back'); return False
                if ev.key == pygame.K_UP:
                    sel = max(0, sel - 1); play_sfx('navigate')
                if ev.key == pygame.K_DOWN:
                    sel = min(len(entries) - 1, sel + 1); play_sfx('navigate')
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    play_sfx('select')
                    name = entries[sel]
                    if confirm_dialog(f"Delete {name}?"):
                        cache_path = manifest[name].get("path", "")
                        if os.path.isdir(cache_path):
                            shutil.rmtree(cache_path, ignore_errors=True)
                        del manifest[name]
                        save_cache_manifest(manifest)
                        return True
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"]:
                    play_sfx('select')
                    name = entries[sel]
                    if confirm_dialog(f"Delete {name}?"):
                        cache_path = manifest[name].get("path", "")
                        if os.path.isdir(cache_path):
                            shutil.rmtree(cache_path, ignore_errors=True)
                        del manifest[name]
                        save_cache_manifest(manifest)
                        return True
                if ev.button == BTN["back"]:
                    play_sfx('back'); return False
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hy == 1:
                    sel = max(0, sel - 1); play_sfx('navigate')
                if hy == -1:
                    sel = min(len(entries) - 1, sel + 1); play_sfx('navigate')

        if joy is not None and now - last_joy > DPAD_DELAY / 1000:
            moved = False
            ya = joy.get_axis(1)
            if ya < -0.5:
                sel = max(0, sel - 1); moved = True
            elif ya > 0.5:
                sel = min(len(entries) - 1, sel + 1); moved = True
            if moved:
                play_sfx('navigate'); last_joy = now

        if sel < scroll:
            scroll = sel
        if sel >= scroll + VIS:
            scroll = sel - VIS + 1

        screen.fill(BG)
        draw_header("Cache Full", f"Select a game to remove ({len(entries)}/{max_cached})")

        y = scaled(60)
        for i in range(scroll, min(scroll + VIS, len(entries))):
            name = entries[i]
            is_sel = (i == sel)
            rect = pygame.Rect(scaled(20), y, W - scaled(40), scaled(44))
            if is_sel:
                pygame.draw.rect(screen, SEL_BG, rect, border_radius=scaled(6))
            pygame.draw.rect(screen, ACCENT if is_sel else BAR_BG, rect, 1, border_radius=scaled(6))
            label = F['md'].render(truncate(name, F['md'], W - scaled(120)), True, TXT_SEL if is_sel else TXT)
            screen.blit(label, (rect.x + scaled(12), rect.y + scaled(6)))
            last_used = manifest[name].get("last_used", 0)
            if last_used > 0:
                age = time.time() - last_used
                if age < 3600:
                    age_str = f"{int(age / 60)}m ago"
                elif age < 86400:
                    age_str = f"{int(age / 3600)}h ago"
                else:
                    age_str = f"{int(age / 86400)}d ago"
                age_s = F['sm'].render(age_str, True, TXT_DIM)
                screen.blit(age_s, (rect.right - age_s.get_width() - scaled(12), rect.y + scaled(14)))
            y += scaled(50)

        draw_hint_bar("[A] Delete   [B] Cancel")
        pygame.display.flip()
        clock.tick(30)


# ═══ Extract & Launch ══════════════════════════════════════════

def extract_and_launch(game_file, user, card):
    """Extract a game archive and launch via RetroArch."""
    rom_dir = active_cfg.get("rom_dir", "")
    cache_dir = active_cfg.get("local_cache_dir", os.path.expanduser("~/ps2-cache"))
    core = active_cfg.get("core_path", "")
    os.makedirs(cache_dir, exist_ok=True)

    manifest = load_cache_manifest()
    game_key = strip_ext(game_file)
    full_path = os.path.join(rom_dir, game_file)

    # Check if already cached
    if game_key in manifest:
        cached = manifest[game_key]
        game_path = cached.get("path", "")
        if os.path.isdir(game_path):
            for ext in GAME_EXTS:
                found = glob.glob(os.path.join(game_path, f"*.{ext}"))
                if found:
                    manifest[game_key]["last_used"] = time.time()
                    save_cache_manifest(manifest)
                    load_memcard(user, card)
                    _launch_retroarch(found[0], core)
                    save_memcard(user, card)
                    return True
        del manifest[game_key]
        save_cache_manifest(manifest)

    # Not cached - evict if needed
    if not evict_cached_picker():
        return False

    # Extract
    dest = os.path.join(cache_dir, game_key)
    os.makedirs(dest, exist_ok=True)
    lower = game_file.lower()

    if lower.endswith(('.zip', '.7z')):
        archive_size = get_archive_size(full_path)

        if lower.endswith('.zip'):
            # Use Python zipfile for cross-platform extraction
            try:
                with zipfile.ZipFile(full_path, 'r') as zf:
                    members = zf.infolist()
                    total = len(members)
                    for idx, member in enumerate(members):
                        zf.extract(member, dest)
                        now = time.time()
                        pct = (idx + 1) / total if total > 0 else 0
                        draw_progress(game_file, min(pct, 0.99), "Extracting")
                        for ev in pygame.event.get():
                            if ev.type == pygame.QUIT:
                                pygame.quit(); sys.exit()
                            if ev.type == pygame.JOYBUTTONDOWN and ev.button == BTN["back"]:
                                shutil.rmtree(dest, ignore_errors=True)
                                return False
            except Exception:
                play_sfx('error')
                draw_progress(game_file, 0, "Zip Error")
                time.sleep(2)
                shutil.rmtree(dest, ignore_errors=True)
                return False
        else:
            # .7z — use external 7z command
            cmd_7z = _find_7z()
            cmd = [cmd_7z, "x", full_path, f"-o{dest}", "-y"]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            last_update = 0
            while True:
                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break
                now = time.time()
                if now - last_update > 0.2:
                    cur_size = get_dir_size(dest)
                    pct = cur_size / archive_size if archive_size > 0 else 0
                    draw_progress(game_file, min(pct, 0.99), "Extracting")
                    last_update = now
                    for ev in pygame.event.get():
                        if ev.type == pygame.QUIT:
                            proc.kill(); pygame.quit(); sys.exit()
                        if ev.type == pygame.JOYBUTTONDOWN and ev.button == BTN["back"]:
                            proc.kill()
                            shutil.rmtree(dest, ignore_errors=True)
                            return False

            if proc.returncode != 0:
                play_sfx('error')
                draw_progress(game_file, 0, "Error")
                time.sleep(2)
                shutil.rmtree(dest, ignore_errors=True)
                return False
    elif lower.endswith(('.iso', '.chd')):
        dest = os.path.dirname(full_path)

    # Find game file
    game_path = None
    for ext in GAME_EXTS:
        found = glob.glob(os.path.join(dest, f"*.{ext}"))
        if found:
            game_path = found[0]
            break

    if not game_path:
        play_sfx('error')
        draw_progress(game_file, 0, "No ISO found")
        time.sleep(2)
        return False

    manifest[game_key] = {
        "path": dest,
        "last_used": time.time(),
        "source": game_file
    }
    save_cache_manifest(manifest)

    draw_progress(game_file, 1.0, "Launching...")
    time.sleep(0.5)

    load_memcard(user, card)
    _launch_retroarch(game_path, core)
    save_memcard(user, card)
    return True


def _find_retroarch():
    """Locate retroarch binary across platforms."""
    if IS_WINDOWS:
        for p in [
            os.path.join(os.environ.get('PROGRAMFILES', ''), 'RetroArch', 'retroarch.exe'),
            os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), 'RetroArch', 'retroarch.exe'),
            'retroarch.exe', 'retroarch',
        ]:
            if shutil.which(p) or os.path.isfile(p):
                return p
    return shutil.which('retroarch') or 'retroarch'


def _launch_retroarch(game_path, core):
    """Launch RetroArch with the given game and core."""
    if not core or not os.path.exists(core):
        play_sfx('error')
        draw_center_msg("Error", "RetroArch core not found!", "Check Settings > RetroArch Core")
        time.sleep(2)
        return
    ra = _find_retroarch()
    cmd = [ra, "-L", core, game_path]
    try:
        subprocess.run(cmd)
    except Exception as e:
        play_sfx('error')
        draw_center_msg("Launch Error", str(e)[:60], "Press any button")
        _wait_any_button()



# ═══ Procedural Icons (drawn with pygame primitives) ═══════════

def _icon_games(surf, cx, cy, r, t, selected):
    """Game disc icon — spinning disc with inner ring."""
    speed = 2.0 if selected else 0.5
    angle = t * speed
    pulse = 1.0 + 0.06 * math.sin(t * 3) if selected else 1.0
    pr = int(r * pulse)
    # Outer disc
    pygame.draw.circle(surf, ACCENT, (cx, cy), pr, 2)
    # Inner ring
    inner = int(pr * 0.4)
    pygame.draw.circle(surf, HDR, (cx, cy), inner, 1)
    # Center dot
    pygame.draw.circle(surf, HDR, (cx, cy), max(2, int(pr * 0.1)))
    # Shine line rotating around disc
    sx = cx + int(pr * 0.7 * math.cos(angle))
    sy = cy + int(pr * 0.7 * math.sin(angle))
    ex = cx + int(pr * 0.95 * math.cos(angle))
    ey = cy + int(pr * 0.95 * math.sin(angle))
    pygame.draw.line(surf, HDR, (sx, sy), (ex, ey), 2)
    # Second shine opposite
    sx2 = cx + int(pr * 0.7 * math.cos(angle + math.pi))
    sy2 = cy + int(pr * 0.7 * math.sin(angle + math.pi))
    ex2 = cx + int(pr * 0.95 * math.cos(angle + math.pi))
    ey2 = cy + int(pr * 0.95 * math.sin(angle + math.pi))
    pygame.draw.line(surf, HDR, (sx2, sy2), (ex2, ey2), 2)


def _icon_memcards(surf, cx, cy, r, t, selected):
    """Memory card icon — PS2 card shape with chip."""
    pulse = 1.0 + 0.05 * math.sin(t * 2.5) if selected else 1.0
    pr = int(r * pulse)
    # Card body (rounded rect)
    cw = int(pr * 1.2)
    ch = int(pr * 1.5)
    card_rect = pygame.Rect(cx - cw // 2, cy - ch // 2, cw, ch)
    pygame.draw.rect(surf, ACCENT, card_rect, 2, border_radius=max(1, int(pr * 0.15)))
    # Notch at top-left corner
    notch = int(pr * 0.3)
    pygame.draw.rect(surf, BG, (card_rect.x, card_rect.y, notch, notch))
    pygame.draw.line(surf, ACCENT, (card_rect.x + notch, card_rect.y), (card_rect.x + notch, card_rect.y + notch), 2)
    pygame.draw.line(surf, ACCENT, (card_rect.x, card_rect.y + notch), (card_rect.x + notch, card_rect.y + notch), 2)
    # Chip (small filled rect in center)
    chip_w = int(pr * 0.5)
    chip_h = int(pr * 0.35)
    chip_rect = pygame.Rect(cx - chip_w // 2, cy - chip_h // 2 + int(pr * 0.15), chip_w, chip_h)
    pygame.draw.rect(surf, HDR if selected else TXT_DIM, chip_rect, 0, border_radius=max(1, int(pr * 0.05)))
    # Chip lines
    for i in range(3):
        ly = chip_rect.y + int(chip_h * (i + 1) / 4)
        pygame.draw.line(surf, BG, (chip_rect.x + 2, ly), (chip_rect.right - 2, ly), 1)
    # Animated label dots at bottom
    if selected:
        for i in range(3):
            dx = cx - int(pr * 0.3) + int(pr * 0.3 * i)
            dy = cy + int(pr * 0.55)
            alpha = int(128 + 127 * math.sin(t * 4 + i * 1.2))
            c = _blend(BG, HDR, alpha / 255)
            pygame.draw.circle(surf, c, (dx, dy), max(1, int(pr * 0.06)))


def _icon_cache(surf, cx, cy, r, t, selected):
    """Cache/lightning bolt icon — \u26a1 shape."""
    pulse = 1.0 + 0.07 * math.sin(t * 3.5) if selected else 1.0
    pr = int(r * pulse)
    # Lightning bolt as polygon
    pts = [
        (cx + int(pr * 0.1), cy - int(pr * 0.8)),
        (cx - int(pr * 0.35), cy + int(pr * 0.05)),
        (cx + int(pr * 0.05), cy + int(pr * 0.05)),
        (cx - int(pr * 0.1), cy + int(pr * 0.8)),
        (cx + int(pr * 0.35), cy - int(pr * 0.05)),
        (cx - int(pr * 0.05), cy - int(pr * 0.05)),
    ]
    color = HDR if selected else ACCENT
    pygame.draw.polygon(surf, color, pts, 0 if selected else 2)
    # Glow particles when selected
    if selected:
        for i in range(4):
            angle = t * 2 + i * math.pi / 2
            dist = pr * (0.9 + 0.2 * math.sin(t * 5 + i))
            px = cx + int(dist * math.cos(angle))
            py = cy + int(dist * math.sin(angle))
            sz = max(1, int(pr * 0.06 * (1 + 0.5 * math.sin(t * 6 + i * 2))))
            pygame.draw.circle(surf, HDR, (px, py), sz)


def _icon_settings(surf, cx, cy, r, t, selected):
    """Settings gear icon — toothed circle that rotates."""
    speed = 0.8 if selected else 0.2
    angle = t * speed
    pulse = 1.0 + 0.04 * math.sin(t * 2) if selected else 1.0
    pr = int(r * pulse)
    teeth = 8
    outer_r = pr * 0.85
    inner_r = pr * 0.6
    # Build gear polygon
    pts = []
    for i in range(teeth):
        a1 = angle + (2 * math.pi * i / teeth)
        a2 = a1 + math.pi / teeth * 0.5
        a3 = a1 + math.pi / teeth
        a4 = a3 + math.pi / teeth * 0.5
        pts.append((cx + int(outer_r * math.cos(a1)), cy + int(outer_r * math.sin(a1))))
        pts.append((cx + int(outer_r * math.cos(a2)), cy + int(outer_r * math.sin(a2))))
        pts.append((cx + int(inner_r * math.cos(a3)), cy + int(inner_r * math.sin(a3))))
        pts.append((cx + int(inner_r * math.cos(a4)), cy + int(inner_r * math.sin(a4))))
    color = HDR if selected else ACCENT
    pygame.draw.polygon(surf, color, pts, 2)
    # Center hole
    pygame.draw.circle(surf, BG, (cx, cy), int(pr * 0.25))
    pygame.draw.circle(surf, color, (cx, cy), int(pr * 0.25), 2)


def _icon_retroarch(surf, cx, cy, r, t, selected):
    """RetroArch icon — play triangle inside circle."""
    pulse = 1.0 + 0.05 * math.sin(t * 2.5) if selected else 1.0
    pr = int(r * pulse)
    # Outer circle
    pygame.draw.circle(surf, ACCENT, (cx, cy), pr, 2)
    # Inner circle (ring)
    pygame.draw.circle(surf, ACCENT, (cx, cy), int(pr * 0.75), 1)
    # Play triangle offset slightly right for visual centering
    tri_r = int(pr * 0.45)
    offset = int(tri_r * 0.15)
    pts = [
        (cx + tri_r + offset, cy),
        (cx - int(tri_r * 0.5) + offset, cy - int(tri_r * 0.8)),
        (cx - int(tri_r * 0.5) + offset, cy + int(tri_r * 0.8)),
    ]
    color = HDR if selected else TXT
    pygame.draw.polygon(surf, color, pts, 0 if selected else 2)
    # Rotating dot around circle when selected
    if selected:
        dot_a = t * 2.5
        dx = cx + int(pr * 0.88 * math.cos(dot_a))
        dy = cy + int(pr * 0.88 * math.sin(dot_a))
        pygame.draw.circle(surf, HDR, (dx, dy), max(2, int(pr * 0.08)))


def _icon_exit(surf, cx, cy, r, t, selected):
    """Exit/power icon — circle with gap and line at top."""
    pulse = 1.0 + 0.05 * math.sin(t * 2) if selected else 1.0
    pr = int(r * pulse)
    color = DANGER if selected else ACCENT
    # Power circle arc (with gap at top)
    start_angle = math.radians(50)
    end_angle = math.radians(310)
    arc_r = int(pr * 0.7)
    pts = []
    steps = 24
    for i in range(steps + 1):
        a = start_angle + (end_angle - start_angle) * i / steps
        pts.append((cx + int(arc_r * math.cos(a)), cy + int(arc_r * math.sin(a))))
    if len(pts) > 1:
        pygame.draw.lines(surf, color, False, pts, 2)
    # Vertical power line
    line_h = int(pr * 0.55)
    pygame.draw.line(surf, color, (cx, cy - int(pr * 0.75)), (cx, cy - int(pr * 0.15)), 3 if selected else 2)
    # Fade animation on selected
    if selected:
        glow_alpha = int(60 + 40 * math.sin(t * 4))
        glow_surf = pygame.Surface((pr * 2, pr * 2), pygame.SRCALPHA)
        pygame.draw.circle(glow_surf, (*DANGER, glow_alpha), (pr, pr), int(pr * 0.9))
        surf.blit(glow_surf, (cx - pr, cy - pr))


MENU_ICONS = [
    ("Games",        _icon_games,     "Browse and launch PS2 games"),
    ("Memory Cards", _icon_memcards,  "Manage per-user memory cards"),
    ("Cache",        _icon_cache,     "View and manage cached games"),
    ("Settings",     _icon_settings,  "Configure app and controls"),
    ("RetroArch",    _icon_retroarch, "Launch RetroArch directly"),
    ("Exit",         _icon_exit,      "Quit PS2 Picker"),
]


# ═══ Screen: Main Menu ═════════════════════════════════════════

def screen_main_menu():
    """Main menu with animated 3x2 grid. Returns action string or None."""
    sel_row, sel_col = 0, 0
    last_joy = 0
    _need_fade = True
    start_time = time.time()

    # Selection glow animation state
    glow_phase = 0.0

    COLS, ROWS = 3, 2
    HEADER_H = scaled(42)
    HINT_H = scaled(32)

    while True:
        now = time.time()
        t = now - start_time  # animation clock
        dt = clock.get_time() / 1000.0  # delta time
        glow_phase += dt * 3.0

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_UP:
                    sel_row = max(0, sel_row - 1); play_sfx('navigate')
                elif ev.key == pygame.K_DOWN:
                    sel_row = min(ROWS - 1, sel_row + 1); play_sfx('navigate')
                elif ev.key == pygame.K_LEFT:
                    sel_col = max(0, sel_col - 1); play_sfx('navigate')
                elif ev.key == pygame.K_RIGHT:
                    sel_col = min(COLS - 1, sel_col + 1); play_sfx('navigate')
                elif ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    idx = sel_row * COLS + sel_col
                    play_sfx('select')
                    fade_to_black()
                    return MENU_ICONS[idx][0]
                elif ev.key == pygame.K_ESCAPE:
                    play_sfx('back')
                    fade_to_black()
                    return "Exit"
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"]:
                    idx = sel_row * COLS + sel_col
                    play_sfx('select')
                    fade_to_black()
                    return MENU_ICONS[idx][0]
                elif ev.button == BTN["back"]:
                    play_sfx('back')
                    fade_to_black()
                    return "Exit"
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hx == -1:
                    sel_col = max(0, sel_col - 1); play_sfx('navigate')
                elif hx == 1:
                    sel_col = min(COLS - 1, sel_col + 1); play_sfx('navigate')
                if hy == 1:
                    sel_row = max(0, sel_row - 1); play_sfx('navigate')
                elif hy == -1:
                    sel_row = min(ROWS - 1, sel_row + 1); play_sfx('navigate')

        # Analog stick navigation
        if joy is not None and now - last_joy > DPAD_DELAY / 1000:
            moved = False
            try:
                ax = joy.get_axis(0)
                ay = joy.get_axis(1)
                if ax < -0.5:
                    sel_col = max(0, sel_col - 1); moved = True
                elif ax > 0.5:
                    sel_col = min(COLS - 1, sel_col + 1); moved = True
                if ay < -0.5:
                    sel_row = max(0, sel_row - 1); moved = True
                elif ay > 0.5:
                    sel_row = min(ROWS - 1, sel_row + 1); moved = True
            except Exception:
                pass
            if moved:
                play_sfx('navigate'); last_joy = now

        # ─── Draw ───
        screen.fill(BG)

        # Header: title left, clock + version right
        title_surf = F['lg'].render("PS2 Picker", True, HDR)
        screen.blit(title_surf, (scaled(12), scaled(10)))

        clock_str = datetime.datetime.now().strftime("%I:%M %p").lstrip('0')
        ver_str = f"v{VERSION}"
        clock_surf = F['sm'].render(clock_str, True, TXT_DIM)
        ver_surf = F['sm'].render(ver_str, True, TXT_DIM)
        screen.blit(clock_surf, (W - clock_surf.get_width() - scaled(12), scaled(8)))
        screen.blit(ver_surf, (W - ver_surf.get_width() - scaled(12), scaled(22)))

        # Subtle header divider line
        pygame.draw.line(screen, _blend(BG, ACCENT, 0.3), (scaled(12), HEADER_H), (W - scaled(12), HEADER_H), 1)

        # Grid layout
        grid_top = HEADER_H + scaled(10)
        grid_bot = H - HINT_H - scaled(8)
        grid_left = scaled(16)
        grid_right = W - scaled(16)
        grid_w = grid_right - grid_left
        grid_h = grid_bot - grid_top
        gap = scaled(8)
        cell_w = (grid_w - gap * (COLS - 1)) // COLS
        cell_h = (grid_h - gap * (ROWS - 1)) // ROWS
        icon_r = min(cell_w, cell_h) // 4  # icon radius

        for row in range(ROWS):
            for col in range(COLS):
                idx = row * COLS + col
                name, icon_fn, desc = MENU_ICONS[idx]
                is_sel = (row == sel_row and col == sel_col)

                x = grid_left + col * (cell_w + gap)
                y = grid_top + row * (cell_h + gap)
                rect = pygame.Rect(x, y, cell_w, cell_h)

                # Cell background
                if is_sel:
                    # Animated glow border
                    glow_alpha = int(100 + 60 * math.sin(glow_phase))
                    glow_color = _blend(BG, ACCENT, glow_alpha / 255)
                    # Outer glow rect
                    glow_rect = rect.inflate(scaled(6), scaled(6))
                    pygame.draw.rect(screen, glow_color, glow_rect, border_radius=scaled(10))
                    # Filled bg
                    pygame.draw.rect(screen, SEL_BG, rect, border_radius=scaled(8))
                    # Accent border
                    pygame.draw.rect(screen, ACCENT, rect, 2, border_radius=scaled(8))
                else:
                    # Subtle card background
                    pygame.draw.rect(screen, BAR_BG, rect, border_radius=scaled(8))
                    pygame.draw.rect(screen, _blend(BAR_BG, ACCENT, 0.2), rect, 1, border_radius=scaled(8))

                # Draw icon centered in upper portion of cell
                icon_cx = rect.centerx
                icon_cy = rect.y + int(cell_h * 0.4)
                icon_fn(screen, icon_cx, icon_cy, icon_r, t, is_sel)

                # Label below icon
                label_font = F['md_b'] if is_sel else F['md']
                label_color = HDR if is_sel else TXT
                label = label_font.render(name, True, label_color)
                label_rect = label.get_rect(centerx=rect.centerx, y=rect.y + int(cell_h * 0.72))
                screen.blit(label, label_rect)

                # Subtle hint for Memory Cards cell
                if name == "Memory Cards" and is_sel:
                    hint_surf = F['sm'].render("Hold L2 for BIOS", True, TXT_DIM)
                    hint_rect = hint_surf.get_rect(centerx=rect.centerx,
                                                   y=rect.y + int(cell_h * 0.88))
                    screen.blit(hint_surf, hint_rect)

        # Description hint for selected item
        sel_idx = sel_row * COLS + sel_col
        desc = MENU_ICONS[sel_idx][2]
        draw_hint_bar(f"{desc}   [A] Select   [B] Exit")

        pygame.display.flip()
        if _need_fade:
            fade_from_black(); _need_fade = False
        clock.tick(60)  # 60fps for smooth icon animations


# ═══ Screen: User Picker (animated card UI) ════════════════════

def _icon_user(surf, cx, cy, r, t, selected):
    """User/person icon — head circle + body arc."""
    pulse = 1.0 + 0.05 * math.sin(t * 2.5) if selected else 1.0
    pr = int(r * pulse)
    color = HDR if selected else ACCENT
    # Head
    head_r = int(pr * 0.3)
    head_y = cy - int(pr * 0.25)
    pygame.draw.circle(surf, color, (cx, head_y), head_r, 2)
    # Body arc
    body_top = cy + int(pr * 0.1)
    body_w = int(pr * 0.7)
    body_h = int(pr * 0.55)
    body_rect = pygame.Rect(cx - body_w, body_top, body_w * 2, body_h * 2)
    pygame.draw.arc(surf, color, body_rect, math.radians(15), math.radians(165), 2)


def _icon_add_user(surf, cx, cy, r, t, selected):
    """Plus icon for creating a new profile."""
    pulse = 1.0 + 0.06 * math.sin(t * 3) if selected else 1.0
    pr = int(r * pulse)
    color = SUCCESS if selected else ACCENT
    arm = int(pr * 0.5)
    w = 3 if selected else 2
    pygame.draw.line(surf, color, (cx - arm, cy), (cx + arm, cy), w)
    pygame.draw.line(surf, color, (cx, cy - arm), (cx, cy + arm), w)
    # Circle around it
    pygame.draw.circle(surf, color, (cx, cy), int(pr * 0.7), 2)


def _icon_card(surf, cx, cy, r, t, selected):
    """Memory card icon (smaller version for card picker)."""
    _icon_memcards(surf, cx, cy, r, t, selected)


def _icon_add_card(surf, cx, cy, r, t, selected):
    """Plus icon for creating a new card."""
    _icon_add_user(surf, cx, cy, r, t, selected)


def _draw_card_grid(items, sel, start_time, title, subtitle, hint_text, cols=3, mounted_index=-1):
    """Shared animated card grid drawer for picker screens.
    items = [(label, icon_fn, subtitle_text), ...]
    mounted_index = index of the currently mounted/active card (-1 for none).
    Returns nothing — draws one frame."""
    t = time.time() - start_time
    HEADER_H = scaled(42)
    HINT_H = scaled(32)

    screen.fill(BG)

    # Header
    t_surf = F['lg'].render(title, True, HDR)
    screen.blit(t_surf, (scaled(12), scaled(10)))
    if subtitle:
        sub_surf = F['sm'].render(subtitle, True, TXT_DIM)
        screen.blit(sub_surf, (W - sub_surf.get_width() - scaled(12), scaled(16)))
    pygame.draw.line(screen, _blend(BG, ACCENT, 0.3),
                     (scaled(12), HEADER_H), (W - scaled(12), HEADER_H), 1)

    total = len(items)
    rows = max(1, (total + cols - 1) // cols)

    # Grid area
    grid_top = HEADER_H + scaled(10)
    grid_bot = H - HINT_H - scaled(8)
    grid_left = scaled(16)
    grid_right = W - scaled(16)
    grid_w = grid_right - grid_left
    grid_h = grid_bot - grid_top
    gap = scaled(8)
    cell_w = (grid_w - gap * (cols - 1)) // cols
    cell_h = min((grid_h - gap * (rows - 1)) // max(1, rows), scaled(140))

    # Center grid vertically if fewer rows than space allows
    total_grid_h = rows * cell_h + (rows - 1) * gap
    grid_y_offset = grid_top + max(0, (grid_h - total_grid_h) // 2)

    icon_r = min(cell_w, cell_h) // 4

    for i, (label, icon_fn, sub) in enumerate(items):
        row = i // cols
        col = i % cols
        is_sel = (i == sel)

        x = grid_left + col * (cell_w + gap)
        y = grid_y_offset + row * (cell_h + gap)
        rect = pygame.Rect(x, y, cell_w, cell_h)

        # Animated glow for selected cell
        if is_sel:
            glow_alpha = int(100 + 60 * math.sin(t * 3))
            glow_color = _blend(BG, ACCENT, glow_alpha / 255)
            glow_rect = rect.inflate(scaled(6), scaled(6))
            pygame.draw.rect(screen, glow_color, glow_rect, border_radius=scaled(10))
            pygame.draw.rect(screen, SEL_BG, rect, border_radius=scaled(8))
            pygame.draw.rect(screen, ACCENT, rect, 2, border_radius=scaled(8))
        else:
            pygame.draw.rect(screen, BAR_BG, rect, border_radius=scaled(8))
            pygame.draw.rect(screen, _blend(BAR_BG, ACCENT, 0.2), rect, 1, border_radius=scaled(8))

        # Icon
        icon_cx = rect.centerx
        icon_cy = rect.y + int(cell_h * 0.38)
        icon_fn(screen, icon_cx, icon_cy, icon_r, t, is_sel)

        # Label
        lf = F['md_b'] if is_sel else F['md']
        lc = HDR if is_sel else TXT
        lab = lf.render(truncate(label, F['md'], cell_w - scaled(8)), True, lc)
        lab_rect = lab.get_rect(centerx=rect.centerx, y=rect.y + int(cell_h * 0.68))
        screen.blit(lab, lab_rect)

        # Subtitle (small, under label)
        if sub:
            sf = F['sm'].render(sub, True, ACCENT if is_sel else TXT_DIM)
            sf_rect = sf.get_rect(centerx=rect.centerx, y=rect.y + int(cell_h * 0.84))
            screen.blit(sf, sf_rect)

        # Mounted badge
        if i == mounted_index:
            badge_text = "MOUNTED"
            badge_font = F['sm']
            badge_surf = badge_font.render(badge_text, True, BG)
            bw = badge_surf.get_width() + scaled(8)
            bh = badge_surf.get_height() + scaled(2)
            bx = rect.right - bw - scaled(3)
            by = rect.top + scaled(3)
            badge_rect = pygame.Rect(bx, by, bw, bh)
            # Pulsing badge glow
            badge_alpha = int(200 + 55 * math.sin(t * 2))
            badge_color = _blend(ACCENT, HDR, badge_alpha / 255)
            pygame.draw.rect(screen, badge_color, badge_rect, border_radius=scaled(3))
            screen.blit(badge_surf, badge_surf.get_rect(center=badge_rect.center))

    draw_hint_bar(hint_text)
    pygame.display.flip()


def _grid_nav(ev, sel, total, cols, joy_ref, last_joy_ref):
    """Handle navigation input for card grids. Returns (new_sel, handled)."""
    rows = max(1, (total + cols - 1) // cols)
    row = sel // cols
    col = sel % cols
    handled = False

    if ev.type == pygame.KEYDOWN:
        if ev.key == pygame.K_LEFT:
            sel = max(0, sel - 1); handled = True
        elif ev.key == pygame.K_RIGHT:
            sel = min(total - 1, sel + 1); handled = True
        elif ev.key == pygame.K_UP:
            new = sel - cols
            if new >= 0: sel = new; handled = True
        elif ev.key == pygame.K_DOWN:
            new = sel + cols
            if new < total: sel = new; handled = True
    elif ev.type == pygame.JOYHATMOTION:
        hx, hy = ev.value
        if hx == -1: sel = max(0, sel - 1); handled = True
        elif hx == 1: sel = min(total - 1, sel + 1); handled = True
        if hy == 1:
            new = sel - cols
            if new >= 0: sel = new; handled = True
        elif hy == -1:
            new = sel + cols
            if new < total: sel = new; handled = True

    if handled:
        play_sfx('navigate')
    return sel, handled


def screen_user_picker():
    """Pick or create a user profile. Returns username or None."""
    sel = 0
    last_joy = 0
    _need_fade = True
    start_time = time.time()
    _mount_toast_time = 0    # timestamp of last mount action (for toast)
    _mount_toast_name = ""   # name of card that was just mounted
    COLS = 3

    while True:
        users = get_users()
        # Build items: each user + "New Profile" at the end
        items = []
        for u in users:
            cards = get_cards(u)
            sub = f"{len(cards)} card{'s' if len(cards) != 1 else ''}"
            items.append((u, _icon_user, sub))
        items.append(("+ New Profile", _icon_add_user, "Create a profile"))
        total = len(items)
        sel = max(0, min(sel, total - 1))
        now = time.time()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()

            # Navigation
            new_sel, handled = _grid_nav(ev, sel, total, COLS, joy, last_joy)
            if handled:
                sel = new_sel
                continue

            # Confirm
            confirmed = False
            if ev.type == pygame.KEYDOWN and ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                confirmed = True
            if ev.type == pygame.JOYBUTTONDOWN and ev.button == BTN["confirm"]:
                confirmed = True

            if confirmed:
                if sel == total - 1:  # New profile
                    play_sfx('select')
                    name = on_screen_keyboard("Enter Username")
                    if name and name.strip():
                        name = name.strip()
                        if name not in users:
                            create_user(name)
                        fade_to_black(); return name
                else:
                    play_sfx('select'); fade_to_black(); return users[sel]

            # Back / Exit
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                play_sfx('back'); fade_to_black(); return None
            if ev.type == pygame.JOYBUTTONDOWN and ev.button == BTN["back"]:
                play_sfx('back'); fade_to_black(); return None

        # Analog stick
        if joy is not None and now - last_joy > DPAD_DELAY / 1000:
            moved = False
            try:
                ax = joy.get_axis(0)
                ay = joy.get_axis(1)
                if ax < -0.5: sel = max(0, sel - 1); moved = True
                elif ax > 0.5: sel = min(total - 1, sel + 1); moved = True
                if ay < -0.5:
                    new = sel - COLS
                    if new >= 0: sel = new; moved = True
                elif ay > 0.5:
                    new = sel + COLS
                    if new < total: sel = new; moved = True
            except Exception:
                pass
            if moved:
                play_sfx('navigate'); last_joy = now

        # Draw
        _draw_card_grid(items, sel, start_time,
                        "Select Profile",
                        f"{len(users)} profile{'s' if len(users) != 1 else ''}",
                        "[A] Select   [B] Exit",
                        cols=COLS)
        if _need_fade:
            fade_from_black(); _need_fade = False
        clock.tick(60)


# ═══ Screen: Memory Card Picker (animated card UI) ═════════════

def screen_memcard_picker(user):
    """Pick or create a memory card. Returns card name or None."""
    sel = 0
    last_joy = 0
    _need_fade = True
    start_time = time.time()
    _mount_toast_time = 0    # timestamp of last mount action (for toast)
    _mount_toast_name = ""   # name of card that was just mounted
    COLS = 3

    while True:
        cards = get_cards(user)
        items = []
        for c in cards:
            # Show memcard file count as subtitle
            card_dir = os.path.join(USERS_DIR, user, "cards", c)
            files = [f for f in os.listdir(card_dir) if f.endswith('.ps2')] if os.path.isdir(card_dir) else []
            sub = f"{len(files)} save{'s' if len(files) != 1 else ''}" if files else "Empty"
            items.append((c, _icon_card, sub))
        items.append(("+ New Card", _icon_add_card, "Create a card"))
        total = len(items)
        sel = max(0, min(sel, total - 1))
        now = time.time()

        _dirty = False
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()

            # Navigation
            new_sel, handled = _grid_nav(ev, sel, total, COLS, joy, last_joy)
            if handled:
                sel = new_sel
                continue

            # Confirm
            confirmed = False
            if ev.type == pygame.KEYDOWN and ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                confirmed = True
            if ev.type == pygame.JOYBUTTONDOWN and ev.button == BTN["confirm"]:
                confirmed = True

            if confirmed:
                if sel == total - 1:  # New card
                    play_sfx('select')
                    name = on_screen_keyboard("Card Name")
                    if name and name.strip():
                        create_card(user, name.strip())
                    _dirty = True; break
                elif sel < len(cards):
                    # A = enter/browse the card's saves
                    play_sfx('select')
                    screen_save_browser(user, cards[sel])
                    _dirty = True; break

            # Mount card (Y button)
            do_mount = False
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_v:
                do_mount = True
            if ev.type == pygame.JOYBUTTONDOWN and ev.button == BTN["alt"]:
                do_mount = True
            if do_mount and sel < len(cards):
                # Save mounted card to meta immediately — stay on screen
                meta = get_user_meta(user)
                meta["last_card"] = cards[sel]
                save_user_meta(user, meta)
                play_sfx('select')
                _mount_toast_name = cards[sel]
                _mount_toast_time = time.time()

            # Delete card (X button)
            do_delete = False
            if ev.type == pygame.KEYDOWN and (ev.key == pygame.K_DELETE or ev.key == pygame.K_x):
                do_delete = True
            if ev.type == pygame.JOYBUTTONDOWN and ev.button == BTN["extra"]:
                do_delete = True
            if do_delete and sel < len(cards) and len(cards) > 1:
                if confirm_dialog(f"Delete {cards[sel]}?"):
                    play_sfx('select')
                    delete_card(user, cards[sel])
                    sel = min(sel, max(0, len(cards) - 2))
                _dirty = True; break

            # Back
            if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                play_sfx('back'); fade_to_black(); return None
            if ev.type == pygame.JOYBUTTONDOWN and ev.button == BTN["back"]:
                play_sfx('back'); fade_to_black(); return None

        if _dirty:
            continue

        # Analog stick
        if joy is not None and now - last_joy > DPAD_DELAY / 1000:
            moved = False
            try:
                ax = joy.get_axis(0)
                ay = joy.get_axis(1)
                if ax < -0.5: sel = max(0, sel - 1); moved = True
                elif ax > 0.5: sel = min(total - 1, sel + 1); moved = True
                if ay < -0.5:
                    new = sel - COLS
                    if new >= 0: sel = new; moved = True
                elif ay > 0.5:
                    new = sel + COLS
                    if new < total: sel = new; moved = True
            except Exception:
                pass
            if moved:
                play_sfx('navigate'); last_joy = now

        # Determine which card is currently mounted
        meta = get_user_meta(user)
        mounted_name = meta.get("last_card", "")
        mounted_idx = -1
        for ci, cn in enumerate(cards):
            if cn == mounted_name:
                mounted_idx = ci; break

        # Draw
        _draw_card_grid(items, sel, start_time,
                        f"{user}'s Cards",
                        f"{len(cards)} card{'s' if len(cards) != 1 else ''}",
                        "[A] Browse   [Y] Mount   [X] Delete   [B] Back",
                        cols=COLS, mounted_index=mounted_idx)

        # Mount confirmation toast (fades out over 2 seconds)
        toast_elapsed = now - _mount_toast_time
        if _mount_toast_time > 0 and toast_elapsed < 2.0:
            toast_alpha = max(0, min(255, int(255 * (1.0 - toast_elapsed / 2.0))))
            toast_text = f"\u2713  {_mount_toast_name} mounted"
            toast_font = F['md']
            toast_surf = toast_font.render(toast_text, True, BG)
            tw = toast_surf.get_width() + scaled(24)
            th = toast_surf.get_height() + scaled(10)
            tx = (W - tw) // 2
            ty = H // 2 - th // 2
            toast_bg = pygame.Surface((tw, th), pygame.SRCALPHA)
            toast_bg.fill((*ACCENT, toast_alpha))
            toast_bg_rect = pygame.Rect(0, 0, tw, th)
            pygame.draw.rect(toast_bg, (*ACCENT, toast_alpha), toast_bg_rect,
                             border_radius=scaled(6))
            screen.blit(toast_bg, (tx, ty))
            toast_surf.set_alpha(toast_alpha)
            screen.blit(toast_surf, toast_surf.get_rect(center=(W // 2, ty + th // 2)))

        pygame.display.flip()
        if _need_fade:
            fade_from_black(); _need_fade = False
        clock.tick(60)


# ═══ Screen: Memory Card Save Browser (PS2-style) ════════════

def _icon_save_slot(surf, cx, cy, r, t, selected):
    """Procedural PS2 save slot icon — disc with sparkle."""
    pulse = 1.0 + 0.05 * math.sin(t * 2.5) if selected else 1.0
    pr = int(r * pulse)
    color = HDR if selected else ACCENT
    pygame.draw.circle(surf, color, (cx, cy), pr, 2)
    pygame.draw.circle(surf, color, (cx, cy), int(pr * 0.5), 1)
    pygame.draw.circle(surf, color, (cx, cy), max(2, int(pr * 0.15)))
    if selected:
        for i in range(3):
            a = t * 1.8 + i * (2 * math.pi / 3)
            sx = cx + int(pr * 0.7 * math.cos(a))
            sy = cy + int(pr * 0.7 * math.sin(a))
            sz = max(1, int(pr * 0.08 * (1 + 0.5 * math.sin(t * 4 + i))))
            pygame.draw.circle(surf, HDR, (sx, sy), sz)


def _icon_file_item(surf, cx, cy, r, t, selected):
    """Small file icon — page with folded corner."""
    pr = int(r * 0.7)
    color = HDR if selected else TXT_DIM
    pw, ph = int(pr * 0.9), int(pr * 1.2)
    rect = pygame.Rect(cx - pw // 2, cy - ph // 2, pw, ph)
    pygame.draw.rect(surf, color, rect, 1)
    fold = int(pr * 0.3)
    fx, fy = rect.right - fold, rect.top
    pygame.draw.line(surf, color, (fx, fy), (fx, fy + fold), 1)
    pygame.draw.line(surf, color, (fx, fy + fold), (rect.right, fy + fold), 1)
    for i in range(2):
        ly = rect.top + int(ph * (0.45 + i * 0.22))
        lx1 = rect.left + int(pw * 0.15)
        lx2 = rect.right - int(pw * 0.15)
        pygame.draw.line(surf, color, (lx1, ly), (lx2, ly), 1)


def _format_size(size_bytes):
    """Format byte count as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def screen_save_browser(user, card):
    """PS2-style memory card save browser — 4x3 grid layout.

    Shows all saves on both Mcd001.ps2 and Mcd002.ps2 in the card directory
    as a 4-column by 3-row icon grid with animated selection and detail panel.
    """
    _need_fade = True
    start_time = time.time()
    sel = 0            # Flat index into all_saves
    page = 0           # Current page (12 saves per page)
    detail_open = False  # Whether the detail overlay is showing
    detail_scroll = 0    # Scroll offset for file list in detail panel
    last_joy = 0

    COLS, ROWS = 4, 3
    PER_PAGE = COLS * ROWS

    # Parse both memory card slots
    card_dir = os.path.join(USERS_DIR, user, "cards", card)
    all_saves = []
    slot_labels = []
    for mc_file in MEMCARD_FILES:
        mc_path = os.path.join(card_dir, mc_file)
        saves = parse_memcard(mc_path)
        slot_name = "Slot 1" if "001" in mc_file else "Slot 2"
        for s in saves:
            all_saves.append(s)
            slot_labels.append(slot_name)

    total = len(all_saves)
    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)

    HEADER_H = scaled(42)
    HINT_H = scaled(32)
    GRID_PAD = scaled(10)  # Padding around grid area
    CELL_GAP = scaled(6)   # Gap between cells

    # Compute cell dimensions from available space
    grid_top = HEADER_H + GRID_PAD + scaled(18)  # room for usage bar
    grid_bottom = H - HINT_H - GRID_PAD
    grid_left = GRID_PAD
    grid_right = W - GRID_PAD
    grid_w = grid_right - grid_left
    grid_h = grid_bottom - grid_top
    cell_w = (grid_w - (COLS - 1) * CELL_GAP) // COLS
    cell_h = (grid_h - (ROWS - 1) * CELL_GAP) // ROWS

    # Icon size — fill most of the cell, leaving room for title text below
    title_line_h = scaled(14)
    icon_area_h = cell_h - title_line_h - scaled(4)
    icon_size = min(cell_w - scaled(8), icon_area_h - scaled(4))
    icon_size = max(scaled(24), icon_size)

    # Build icon surface cache from extracted pixel data, scaled to grid size
    # Pre-render icon surfaces and breathing animation frames
    BREATH_FRAMES = 60          # total frames in one breathing cycle
    BREATH_AMP = 0.05           # 5% max scale increase
    icon_surfs = {}             # idx -> base surface
    icon_breath_frames = {}     # idx -> list of pre-scaled surfaces

    for idx, save in enumerate(all_saves):
        if save.icon_pixels and len(save.icon_pixels) == 128 * 128 * 4:
            try:
                ico_surf = pygame.image.frombuffer(save.icon_pixels, (128, 128), 'RGBA')
                ico_surf = pygame.transform.flip(ico_surf, False, True)  # flip vertically
                ico_base = pygame.transform.smoothscale(ico_surf, (icon_size, icon_size))
                icon_surfs[idx] = ico_base
                # Pre-compute breathing frames for buttery-smooth animation
                frames = []
                for fi in range(BREATH_FRAMES):
                    phase = fi / BREATH_FRAMES  # 0..1
                    # Smooth ease-in-out via cosine
                    breath = (1.0 - math.cos(phase * 2.0 * math.pi)) / 2.0
                    sf = 1.0 + BREATH_AMP * breath
                    fw = int(icon_size * sf)
                    fh = int(icon_size * sf)
                    frames.append(pygame.transform.smoothscale(ico_base, (fw, fh)))
                icon_breath_frames[idx] = frames
            except Exception:
                pass

    # Scrolling title state: tracks pixel offset per save for marquee effect
    _title_scroll_offsets = {}   # idx -> current pixel offset
    _title_scroll_pause = {}    # idx -> pause timer (seconds remaining)
    TITLE_SCROLL_SPEED = 30     # pixels per second
    TITLE_SCROLL_PAUSE = 1.5    # seconds to pause at each end

    # Card usage calculation
    card_used_bytes = sum(s.size for s in all_saves)
    CARD_CAPACITY = 8 * 1024 * 1024  # 8 MB
    card_used_pct = min(1.0, card_used_bytes / CARD_CAPACITY) if CARD_CAPACITY > 0 else 0

    def _cell_rect(col, row):
        """Return the pygame.Rect for a grid cell at (col, row)."""
        cx = grid_left + col * (cell_w + CELL_GAP)
        cy = grid_top + row * (cell_h + CELL_GAP)
        return pygame.Rect(cx, cy, cell_w, cell_h)

    def _sel_to_grid(index):
        """Convert flat save index to (page, col, row)."""
        p = index // PER_PAGE
        local = index % PER_PAGE
        return p, local % COLS, local // COLS

    def _grid_to_sel(p, col, row):
        """Convert (page, col, row) to flat save index."""
        return p * PER_PAGE + row * COLS + col

    def _draw_detail_panel(save, slot_label, t):
        """Draw the save detail overlay panel."""
        # Dimmed background
        overlay = pygame.Surface((W, H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 160))
        screen.blit(overlay, (0, 0))

        # Panel dimensions
        pw = min(W - scaled(40), scaled(420))
        ph = min(H - scaled(60), scaled(340))
        px = (W - pw) // 2
        py = (H - ph) // 2
        panel_rect = pygame.Rect(px, py, pw, ph)

        # Panel background with border
        pygame.draw.rect(screen, BG, panel_rect, border_radius=scaled(6))
        border_alpha = int(180 + 50 * math.sin(t * 2.5))
        border_color = _blend(BG, ACCENT, border_alpha / 255)
        pygame.draw.rect(screen, border_color, panel_rect, 2, border_radius=scaled(6))

        inner_x = px + scaled(14)
        inner_w = pw - scaled(28)
        cy = py + scaled(12)

        # Icon + Title header area
        header_icon_size = min(scaled(64), icon_size)
        if all_saves.index(save) in icon_surfs:
            ico = icon_surfs[all_saves.index(save)]
            ico_scaled = pygame.transform.smoothscale(ico, (header_icon_size, header_icon_size))
            screen.blit(ico_scaled, (inner_x, cy))
        else:
            _icon_save_slot(screen, inner_x + header_icon_size // 2,
                            cy + header_icon_size // 2,
                            header_icon_size // 3, t, True)

        title_x = inner_x + header_icon_size + scaled(10)
        title_w = inner_w - header_icon_size - scaled(10)
        title_text = truncate(save.title, F['md_b'], title_w)
        ts = F['md_b'].render(title_text, True, HDR)
        screen.blit(ts, (title_x, cy + scaled(4)))

        dir_text = truncate(save.name, F['sm'], title_w)
        ds = F['sm'].render(dir_text, True, TXT_DIM)
        screen.blit(ds, (title_x, cy + scaled(22)))

        slot_surf = F['sm'].render(slot_label, True, ACCENT)
        screen.blit(slot_surf, (title_x, cy + scaled(36)))

        cy += max(header_icon_size, scaled(52)) + scaled(10)

        # Separator line
        pygame.draw.line(screen, _blend(BG, ACCENT, 0.3),
                         (inner_x, cy), (inner_x + inner_w, cy), 1)
        cy += scaled(8)

        # Info fields
        info_items = []
        if save.modified:
            info_items.append(("Date", save.modified.strftime('%Y-%m-%d %H:%M')))
        info_items.append(("Size", _format_size(save.size)))
        info_items.append(("Files", str(save.file_count)))

        for label, value in info_items:
            if cy + scaled(16) > py + ph - scaled(30):
                break
            ls = F['sm'].render(f"{label}:", True, TXT_DIM)
            vs = F['sm'].render(value, True, TXT)
            screen.blit(ls, (inner_x, cy))
            screen.blit(vs, (inner_x + scaled(52), cy))
            cy += scaled(16)

        cy += scaled(6)

        # File listing with scroll support
        if save.files:
            pygame.draw.line(screen, _blend(BG, ACCENT, 0.2),
                             (inner_x, cy), (inner_x + inner_w, cy), 1)
            cy += scaled(6)
            file_label = F['sm'].render("Files:", True, ACCENT)
            screen.blit(file_label, (inner_x, cy))
            cy += scaled(14)

            file_area_top = cy
            file_area_bottom = py + ph - scaled(28)
            file_area_h = file_area_bottom - file_area_top
            file_line_h = scaled(14)
            max_visible = max(1, file_area_h // file_line_h)

            visible_files = save.files[detail_scroll:detail_scroll + max_visible]
            clip_rect = pygame.Rect(inner_x, file_area_top, inner_w, file_area_h)
            screen.set_clip(clip_rect)
            fy = file_area_top
            for fname, fsize, fmod in visible_files:
                _icon_file_item(screen, inner_x + scaled(6), fy + file_line_h // 2,
                                scaled(5), t, False)
                fn = truncate(fname, F['sm'], inner_w - scaled(80))
                screen.blit(F['sm'].render(fn, True, TXT_DIM),
                            (inner_x + scaled(16), fy))
                screen.blit(F['sm'].render(_format_size(fsize), True,
                            _blend(TXT_DIM, BG, 0.3)),
                            (inner_x + inner_w - scaled(50), fy))
                fy += file_line_h
            screen.set_clip(None)

            # Scroll indicators
            if detail_scroll > 0:
                arr = F['sm'].render("\u25b2", True, ACCENT)
                screen.blit(arr, (inner_x + inner_w - scaled(12), file_area_top - scaled(12)))
            if detail_scroll + max_visible < len(save.files):
                arr = F['sm'].render("\u25bc", True, ACCENT)
                screen.blit(arr, (inner_x + inner_w - scaled(12), file_area_bottom - scaled(2)))

        # Hint text at bottom of panel
        hint_text = "[B] Close"
        if save.files and len(save.files) > 0:
            hint_text = "[\u2191\u2193] Scroll Files   [B] Close"
        hs = F['sm'].render(hint_text, True, TXT_DIM)
        screen.blit(hs, hs.get_rect(centerx=px + pw // 2, bottom=py + ph - scaled(6)))

    while True:
        now = time.time()
        t = now - start_time

        if total == 0:
            # Empty card screen
            screen.fill(BG)
            title_surf = F['lg'].render(f"{card}", True, HDR)
            screen.blit(title_surf, (scaled(12), scaled(10)))
            sub = F['sm'].render("Memory Card Browser", True, TXT_DIM)
            screen.blit(sub, (W - sub.get_width() - scaled(12), scaled(16)))
            pygame.draw.line(screen, _blend(BG, ACCENT, 0.3),
                             (scaled(12), HEADER_H), (W - scaled(12), HEADER_H), 1)
            msg = F['md'].render("No saves found on this card", True, TXT_DIM)
            screen.blit(msg, msg.get_rect(center=(W // 2, H // 2 - scaled(10))))
            hint = F['sm'].render("Card may be empty or unformatted", True, TXT_DIM)
            screen.blit(hint, hint.get_rect(center=(W // 2, H // 2 + scaled(14))))
            draw_hint_bar("[B] Back")
            pygame.display.flip()
            if _need_fade:
                fade_from_black(); _need_fade = False
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    pygame.quit(); sys.exit()
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                    play_sfx('back'); fade_to_black(); return
                if ev.type == pygame.JOYBUTTONDOWN and ev.button == BTN["back"]:
                    play_sfx('back'); fade_to_black(); return
            clock.tick(30)
            continue

        # ── Input handling ──
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()

            if ev.type == pygame.KEYDOWN:
                if detail_open:
                    # Detail panel input
                    if ev.key == pygame.K_ESCAPE:
                        play_sfx('back'); detail_open = False; detail_scroll = 0
                    elif ev.key == pygame.K_UP:
                        if detail_scroll > 0:
                            detail_scroll -= 1; play_sfx('navigate')
                    elif ev.key == pygame.K_DOWN:
                        save = all_saves[sel]
                        file_area_h = min(H - scaled(60), scaled(340)) - scaled(180)
                        max_vis = max(1, file_area_h // scaled(14))
                        if detail_scroll + max_vis < len(save.files):
                            detail_scroll += 1; play_sfx('navigate')
                else:
                    # Grid navigation
                    if ev.key == pygame.K_ESCAPE:
                        play_sfx('back'); fade_to_black(); return
                    elif ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                        if sel < total:
                            play_sfx('select'); detail_open = True; detail_scroll = 0
                    elif ev.key == pygame.K_LEFT:
                        if sel % COLS > 0:
                            sel -= 1; play_sfx('navigate')
                        elif page > 0:
                            page -= 1; sel = page * PER_PAGE + COLS - 1
                            sel = min(sel, total - 1); play_sfx('navigate')
                    elif ev.key == pygame.K_RIGHT:
                        if sel % COLS < COLS - 1 and sel + 1 < total:
                            sel += 1; play_sfx('navigate')
                        elif page < total_pages - 1:
                            page += 1; sel = page * PER_PAGE
                            sel = min(sel, total - 1); play_sfx('navigate')
                    elif ev.key == pygame.K_UP:
                        if sel - COLS >= page * PER_PAGE:
                            sel -= COLS; play_sfx('navigate')
                    elif ev.key == pygame.K_DOWN:
                        if sel + COLS < min((page + 1) * PER_PAGE, total):
                            sel += COLS; play_sfx('navigate')
                    elif ev.key == pygame.K_PAGEUP:
                        if page > 0:
                            page -= 1; sel = page * PER_PAGE; play_sfx('navigate')
                    elif ev.key == pygame.K_PAGEDOWN:
                        if page < total_pages - 1:
                            page += 1
                            sel = min(page * PER_PAGE, total - 1)
                            play_sfx('navigate')

            if ev.type == pygame.JOYBUTTONDOWN:
                if detail_open:
                    if ev.button == BTN["back"]:
                        play_sfx('back'); detail_open = False; detail_scroll = 0
                else:
                    if ev.button == BTN["back"]:
                        play_sfx('back'); fade_to_black(); return
                    elif ev.button == BTN["confirm"]:
                        if sel < total:
                            play_sfx('select'); detail_open = True; detail_scroll = 0
                    elif ev.button == BTN.get("shoulder_l", -1):
                        if page > 0:
                            page -= 1; sel = page * PER_PAGE
                            sel = min(sel, total - 1); play_sfx('navigate')
                    elif ev.button == BTN.get("shoulder_r", -1):
                        if page < total_pages - 1:
                            page += 1; sel = page * PER_PAGE
                            sel = min(sel, total - 1); play_sfx('navigate')

            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if detail_open:
                    save = all_saves[sel]
                    file_area_h = min(H - scaled(60), scaled(340)) - scaled(180)
                    max_vis = max(1, file_area_h // scaled(14))
                    if hy == 1 and detail_scroll > 0:
                        detail_scroll -= 1; play_sfx('navigate')
                    elif hy == -1 and detail_scroll + max_vis < len(save.files):
                        detail_scroll += 1; play_sfx('navigate')
                else:
                    if hx == -1:
                        if sel % COLS > 0:
                            sel -= 1; play_sfx('navigate')
                        elif page > 0:
                            page -= 1; sel = page * PER_PAGE + COLS - 1
                            sel = min(sel, total - 1); play_sfx('navigate')
                    elif hx == 1:
                        if sel % COLS < COLS - 1 and sel + 1 < total:
                            sel += 1; play_sfx('navigate')
                        elif page < total_pages - 1:
                            page += 1; sel = page * PER_PAGE
                            sel = min(sel, total - 1); play_sfx('navigate')
                    elif hy == 1:   # dpad up
                        if sel - COLS >= page * PER_PAGE:
                            sel -= COLS; play_sfx('navigate')
                    elif hy == -1:  # dpad down
                        if sel + COLS < min((page + 1) * PER_PAGE, total):
                            sel += COLS; play_sfx('navigate')

        # Analog stick navigation (grid only, not detail panel)
        if not detail_open and joy is not None and now - last_joy > DPAD_DELAY / 1000:
            moved = False
            try:
                ax = joy.get_axis(0)
                ay = joy.get_axis(1)
                if ax < -0.5:
                    if sel % COLS > 0:
                        sel -= 1; moved = True
                    elif page > 0:
                        page -= 1; sel = page * PER_PAGE + COLS - 1
                        sel = min(sel, total - 1); moved = True
                elif ax > 0.5:
                    if sel % COLS < COLS - 1 and sel + 1 < total:
                        sel += 1; moved = True
                    elif page < total_pages - 1:
                        page += 1; sel = page * PER_PAGE
                        sel = min(sel, total - 1); moved = True
                elif ay < -0.5:
                    if sel - COLS >= page * PER_PAGE:
                        sel -= COLS; moved = True
                elif ay > 0.5:
                    if sel + COLS < min((page + 1) * PER_PAGE, total):
                        sel += COLS; moved = True
            except Exception:
                pass
            if moved:
                play_sfx('navigate'); last_joy = now

        # Keep sel and page in sync
        sel = max(0, min(sel, total - 1))
        page = sel // PER_PAGE

        # ── Draw ──
        screen.fill(BG)

        # Header
        title_surf = F['lg'].render(f"{card}", True, HDR)
        screen.blit(title_surf, (scaled(12), scaled(10)))
        page_str = f"Page {page + 1}/{total_pages}" if total_pages > 1 else ""
        sub_str = f"{total} save{'s' if total != 1 else ''}"
        if page_str:
            sub_str += f"  \u2022  {page_str}"
        sub_str += "  \u2022  Memory Card Browser"
        sub = F['sm'].render(sub_str, True, TXT_DIM)
        screen.blit(sub, (W - sub.get_width() - scaled(12), scaled(16)))
        pygame.draw.line(screen, _blend(BG, ACCENT, 0.5),
                         (scaled(8), HEADER_H - 1), (W - scaled(8), HEADER_H - 1), 1)
        pygame.draw.line(screen, _blend(BG, ACCENT, 0.25),
                         (scaled(8), HEADER_H + 1), (W - scaled(8), HEADER_H + 1), 1)

        # Card usage bar
        bar_left = scaled(12)
        bar_right = W - scaled(12)
        bar_w = bar_right - bar_left
        bar_h = scaled(5)
        bar_y = HEADER_H + scaled(3)
        # Track background
        pygame.draw.rect(screen, _blend(BG, BAR_BG, 0.4),
                         (bar_left, bar_y, bar_w, bar_h), border_radius=scaled(2))
        # Used portion
        used_w = max(scaled(2), int(bar_w * card_used_pct))
        bar_color = ACCENT if card_used_pct < 0.85 else (220, 80, 60)
        pygame.draw.rect(screen, bar_color,
                         (bar_left, bar_y, used_w, bar_h), border_radius=scaled(2))
        # Usage label
        free_bytes = max(0, CARD_CAPACITY - card_used_bytes)
        usage_text = f"{_format_size(card_used_bytes)} / 8 MB used  \u2022  {_format_size(free_bytes)} free"
        usage_surf = F['sm'].render(usage_text, True, TXT_DIM)
        screen.blit(usage_surf, (bar_left, bar_y + bar_h + scaled(1)))

        # Draw grid cells for current page
        page_start = page * PER_PAGE
        page_end = min(page_start + PER_PAGE, total)

        for idx in range(page_start, page_end):
            local = idx - page_start
            col = local % COLS
            row = local // COLS
            rect = _cell_rect(col, row)
            save = all_saves[idx]
            is_sel = (idx == sel)

            # Cell background
            if is_sel:
                glow_alpha = int(80 + 40 * math.sin(t * 3))
                glow_color = _blend(BG, ACCENT, glow_alpha / 255)
                pygame.draw.rect(screen, glow_color, rect.inflate(scaled(4), scaled(4)),
                                 border_radius=scaled(6))
                pygame.draw.rect(screen, SEL_BG, rect, border_radius=scaled(5))
                # Animated selection border
                bdr_alpha = int(200 + 55 * math.sin(t * 3.5))
                bdr_color = _blend(ACCENT, HDR, bdr_alpha / 255)
                pygame.draw.rect(screen, bdr_color, rect, 2, border_radius=scaled(5))
            else:
                cell_bg = _blend(BG, BAR_BG, 0.25)
                pygame.draw.rect(screen, cell_bg, rect, border_radius=scaled(4))
                pygame.draw.rect(screen, _blend(BG, ACCENT, 0.12), rect, 1,
                                 border_radius=scaled(4))

            # Icon centered in upper portion of cell
            icon_cx = rect.centerx
            icon_cy = rect.top + scaled(4) + icon_area_h // 2

            if idx in icon_surfs:
                if is_sel and idx in icon_breath_frames:
                    # Pick pre-computed frame for buttery-smooth breathing
                    cycle_pos = (t * 0.8) % 1.0   # 0.8 = ~1.25s per full cycle
                    frame_idx = int(cycle_pos * BREATH_FRAMES) % BREATH_FRAMES
                    ico_disp = icon_breath_frames[idx][frame_idx]
                else:
                    ico_disp = icon_surfs[idx]
                disp_w, disp_h = ico_disp.get_size()
                screen.blit(ico_disp, (icon_cx - disp_w // 2, icon_cy - disp_h // 2))
            else:
                # Procedural fallback disc icon
                fallback_r = min(icon_size // 2, int(icon_area_h * 0.35))
                _icon_save_slot(screen, icon_cx, icon_cy, fallback_r, t, is_sel)

            # Title text below icon — scrolls horizontally if too long
            title_y = rect.bottom - title_line_h - scaled(2)
            title_font = F['sm']
            title_color = HDR if is_sel else TXT
            max_title_w = cell_w - scaled(8)
            full_title_surf = title_font.render(save.title, True, title_color)
            ftw = full_title_surf.get_width()

            title_clip = pygame.Rect(rect.x + scaled(4), title_y,
                                     max_title_w, title_line_h)
            if ftw <= max_title_w:
                # Fits — just center it
                ts_rect = full_title_surf.get_rect(centerx=rect.centerx, top=title_y)
                screen.set_clip(title_clip)
                screen.blit(full_title_surf, ts_rect)
                screen.set_clip(None)
                _title_scroll_offsets.pop(idx, None)
                _title_scroll_pause.pop(idx, None)
            else:
                # Scrolling marquee
                overflow = ftw - max_title_w
                if idx not in _title_scroll_offsets:
                    _title_scroll_offsets[idx] = 0.0
                    _title_scroll_pause[idx] = TITLE_SCROLL_PAUSE

                pause = _title_scroll_pause.get(idx, 0)
                scroll_off = _title_scroll_offsets.get(idx, 0)
                dt_frame = clock.get_time() / 1000.0

                if pause > 0:
                    _title_scroll_pause[idx] = pause - dt_frame
                else:
                    scroll_off += TITLE_SCROLL_SPEED * dt_frame
                    if scroll_off >= overflow:
                        scroll_off = overflow
                        _title_scroll_pause[idx] = TITLE_SCROLL_PAUSE
                        # Reverse direction next cycle
                    _title_scroll_offsets[idx] = scroll_off

                    # Check if we reached the end and need to bounce back
                    if scroll_off >= overflow and _title_scroll_pause.get(idx, 0) <= 0:
                        _title_scroll_offsets[idx] = overflow
                        _title_scroll_pause[idx] = TITLE_SCROLL_PAUSE

                # Bounce: if offset hit overflow, next cycle goes back to 0
                # Use a ping-pong based on a cycle counter
                blit_x = rect.x + scaled(4) - int(scroll_off)
                screen.set_clip(title_clip)
                screen.blit(full_title_surf, (blit_x, title_y))
                screen.set_clip(None)

                # Reset to bounce back after reaching end
                if scroll_off >= overflow and _title_scroll_pause.get(idx, 0) <= 0:
                    _title_scroll_offsets[idx] = 0.0
                    _title_scroll_pause[idx] = TITLE_SCROLL_PAUSE

        # Page dots indicator (if multiple pages)
        if total_pages > 1:
            dot_y = grid_bottom + scaled(4)
            dot_total_w = total_pages * scaled(8) + (total_pages - 1) * scaled(4)
            dot_start_x = (W - dot_total_w) // 2
            for pi in range(total_pages):
                dx = dot_start_x + pi * (scaled(8) + scaled(4))
                color = ACCENT if pi == page else _blend(BG, ACCENT, 0.3)
                r = scaled(3) if pi == page else scaled(2)
                pygame.draw.circle(screen, color, (dx + scaled(4), dot_y), r)

        # Hint bar
        hint_parts = ["[A] Details", "[B] Back"]
        if total_pages > 1:
            hint_parts = ["[A] Details", "[L1] Prev", "[R1] Next", "[B] Back"]
        draw_hint_bar("   ".join(hint_parts))

        # Detail panel overlay (drawn on top of everything)
        if detail_open and sel < total:
            _draw_detail_panel(all_saves[sel], slot_labels[sel], t)

        pygame.display.flip()
        if _need_fade:
            fade_from_black(); _need_fade = False
        clock.tick(60)


# ═══ Screen: Game Picker ═══════════════════════════════════════

def screen_game_picker(user, card):
    """Pick and launch a game. Returns True if game ran, False to go back."""
    global joy
    sel = 0
    scroll = 0
    smooth_scroll = 0.0  # visual scroll offset for lerp
    last_joy_time = 0
    search_text = ""
    _need_fade = True
    # Cache the manifest lookup so we don't hit disk every frame
    cached_keys = set(load_cache_manifest().keys())

    while True:
        # Filter games
        if search_text:
            filtered = [g for g in games if search_text.lower() in g.lower()]
        else:
            filtered = list(games)

        if not filtered and not search_text:
            screen.fill(BG)
            draw_header("Games", None)
            rom_dir = active_cfg.get('rom_dir', '(not set)')
            msg1 = F['md'].render("No games found", True, TXT)
            msg2 = F['sm'].render(f"ROM folder: {rom_dir}", True, TXT_DIM)
            screen.blit(msg1, msg1.get_rect(center=(W // 2, H // 2 - scaled(15))))
            screen.blit(msg2, msg2.get_rect(center=(W // 2, H // 2 + scaled(15))))
            draw_hint_bar("[B] Back   [Start] Settings")
            pygame.display.flip()
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    pygame.quit(); sys.exit()
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                    play_sfx('back'); fade_to_black(); return False
                if ev.type == pygame.JOYBUTTONDOWN:
                    if ev.button == BTN["back"]:
                        play_sfx('back'); fade_to_black(); return False
                    if ev.button == BTN["start"]:
                        play_sfx('select'); fade_to_black(); settings_menu(user); reload_games(); _need_fade = True
            clock.tick(30)
            continue

        sel = max(0, min(sel, len(filtered) - 1)) if filtered else 0
        now = time.time()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    play_sfx('back'); fade_to_black(); return False
                if ev.key == pygame.K_UP:
                    sel = max(0, sel - 1); play_sfx('navigate')
                if ev.key == pygame.K_DOWN and filtered:
                    sel = min(len(filtered) - 1, sel + 1); play_sfx('navigate')
                if ev.key == pygame.K_LEFT or ev.key == pygame.K_PAGEUP:
                    sel = max(0, sel - VISIBLE); play_sfx('navigate')
                if ev.key == pygame.K_RIGHT or ev.key == pygame.K_PAGEDOWN:
                    sel = min(len(filtered) - 1, sel + VISIBLE); play_sfx('navigate')
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE) and filtered:
                    play_sfx('launch')
                    result = extract_and_launch(filtered[sel], user, card)
                    reload_games(); cached_keys = set(load_cache_manifest().keys())
                    sel = min(sel, max(0, len(games) - 1))
                    if result:
                        return True
                if ev.key == pygame.K_F1:
                    play_sfx('select'); fade_to_black(); settings_menu(user); reload_games()
                    cached_keys = set(load_cache_manifest().keys()); _need_fade = True
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"] and filtered:  # A = launch
                    play_sfx('launch')
                    result = extract_and_launch(filtered[sel], user, card)
                    reload_games(); cached_keys = set(load_cache_manifest().keys())
                    sel = min(sel, max(0, len(games) - 1))
                    if result:
                        return True
                if ev.button == BTN["back"]:  # B = back
                    play_sfx('back'); fade_to_black(); return False
                if ev.button == BTN["start"]:  # Start = settings
                    play_sfx('select'); fade_to_black(); settings_menu(user); reload_games()
                    cached_keys = set(load_cache_manifest().keys()); _need_fade = True
                if ev.button == BTN["select"]:  # Select = search
                    play_sfx('select')
                    q = on_screen_keyboard("Search Games")
                    search_text = q if q else ""
                    sel = 0; scroll = 0
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hy == 1:
                    sel = max(0, sel - 1); play_sfx('navigate')
                if hy == -1 and filtered:
                    sel = min(len(filtered) - 1, sel + 1); play_sfx('navigate')
                if hx == -1:
                    sel = max(0, sel - VISIBLE); play_sfx('navigate')
                if hx == 1 and filtered:
                    sel = min(len(filtered) - 1, sel + VISIBLE); play_sfx('navigate')

        if joy is not None and filtered and now - last_joy_time > DPAD_DELAY / 1000:
            moved = False
            ya = joy.get_axis(1)
            xa = joy.get_axis(0)
            if ya < -0.5:
                sel = max(0, sel - 1); moved = True
            elif ya > 0.5:
                sel = min(len(filtered) - 1, sel + 1); moved = True
            if xa < -0.5:
                sel = max(0, sel - VISIBLE); moved = True
            elif xa > 0.5:
                sel = min(len(filtered) - 1, sel + VISIBLE); moved = True
            if moved:
                play_sfx('navigate'); last_joy_time = now

        if sel < scroll:
            scroll = sel
        if sel >= scroll + VISIBLE:
            scroll = sel - VISIBLE + 1

        # Smooth scroll lerp — visual offset chases logical scroll
        target = float(scroll)
        if abs(smooth_scroll - target) > 0.01:
            smooth_scroll += (target - smooth_scroll) * 0.25
            if abs(smooth_scroll - target) < 0.01:
                smooth_scroll = target
        else:
            smooth_scroll = target
        frac = smooth_scroll - int(smooth_scroll)
        vis_scroll = int(smooth_scroll)

        screen.fill(BG)
        title = f"Games - {user} / {card}"
        search_hint = f" (filter: {search_text})" if search_text else ""
        draw_header(title + search_hint, None,
                     f"{len(filtered)} game{'s' if len(filtered) != 1 else ''}")

        if filtered:
            items_display = [(strip_ext(g),
                              strip_ext(g) in cached_keys) for g in filtered]
            draw_list(items_display, sel, vis_scroll, smooth_offset=frac)

        draw_hint_bar("[A] Launch   [B] Back   [Start] Settings   [Select] Search")
        pygame.display.flip()
        if _need_fade:
            fade_from_black(); _need_fade = False
        clock.tick(30)


# ═══ Splash Screen ═════════════════════════════════════════════

def show_splash():
    """Animated boot splash that flows into controller detection and start prompt."""
    global joy
    ANIM_DUR = 2.5
    start = time.time()

    while True:
        elapsed = time.time() - start
        t = min(elapsed / ANIM_DUR, 1.0)
        animating = elapsed < ANIM_DUR

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if not animating and ev.type in (pygame.JOYBUTTONDOWN, pygame.KEYDOWN):
                play_sfx('select')
                return

        # Keep polling for controller
        if not animating and joy is None:
            pygame.joystick.quit()
            pygame.joystick.init()
            if pygame.joystick.get_count() > 0:
                joy = pygame.joystick.Joystick(0)
                joy.init()

        screen.fill(BG)

        # Title fade-in (0.0 -> 0.4)
        title_t = min(t / 0.4, 1.0)
        title_color = lerp_color(BG, HDR, title_t)
        ts = F['xl'].render("PS2 Picker", True, title_color)
        screen.blit(ts, ts.get_rect(center=(W // 2, H // 2 - scaled(30))))

        # Subtitle fade-in (0.2 -> 0.6)
        sub_t = max(0.0, min((t - 0.2) / 0.4, 1.0))
        sub_color = lerp_color(BG, HINT, sub_t)
        ss = F['md'].render("Goon Squad Canada", True, sub_color)
        screen.blit(ss, ss.get_rect(center=(W // 2, H // 2 + scaled(10))))

        # Accent line sweep (0.3 -> 0.7)
        line_t = max(0.0, min((t - 0.3) / 0.4, 1.0))
        line_w = int((W - scaled(100)) * line_t)
        if line_w > 0:
            lx = (W - line_w) // 2
            pygame.draw.line(screen, ACCENT, (lx, H // 2 + scaled(35)),
                             (lx + line_w, H // 2 + scaled(35)), 2)

        # Phase 2: after animation, show prompt
        if not animating:
            pulse = 0.4 + 0.6 * abs(math.sin(elapsed * 2.5))

            if joy is None:
                prompt = "Connect a controller and press any button"
                prompt_color = lerp_color(BG, HINT, pulse)
            else:
                prompt = "Press any button to start"
                prompt_color = lerp_color(BG, TXT, pulse)

            ps = F['md'].render(prompt, True, prompt_color)
            screen.blit(ps, ps.get_rect(center=(W // 2, H // 2 + scaled(65))))

        pygame.display.flip()
        clock.tick(60)


# ═══ Welcome Back Splash ═══════════════════════════════════════

def show_welcome_back(user):
    """Quick welcome-back splash after RetroArch exits."""
    duration = 1.5
    start = time.time()

    while True:
        elapsed = time.time() - start
        if elapsed >= duration:
            break

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type in (pygame.JOYBUTTONDOWN, pygame.KEYDOWN):
                return

        t = elapsed / duration
        screen.fill(BG)

        wb_t = min(t / 0.3, 1.0)
        wb_color = lerp_color(BG, HDR, wb_t)
        ws = F['xl'].render("Welcome Back", True, wb_color)
        screen.blit(ws, ws.get_rect(center=(W // 2, H // 2 - scaled(20))))

        name_t = max(0.0, min((t - 0.15) / 0.3, 1.0))
        name_color = lerp_color(BG, TXT_SEL, name_t)
        ns = F['lg'].render(user, True, name_color)
        screen.blit(ns, ns.get_rect(center=(W // 2, H // 2 + scaled(15))))

        if t > 0.75:
            fade_t = (t - 0.75) / 0.25
            overlay = pygame.Surface((W, H))
            overlay.fill(BG)
            overlay.set_alpha(int(255 * fade_t))
            screen.blit(overlay, (0, 0))

        pygame.display.flip()
        clock.tick(60)


# ═══ Main Loop ═════════════════════════════════════════════════

def _launch_retroarch_standalone():
    """Launch RetroArch without a game for configuration."""
    global screen, W, H, F, joy
    ra = _find_retroarch()
    if not ra:
        play_sfx('error')
        draw_center_msg("Error", "RetroArch not found!", "Check your PATH or install RetroArch")
        time.sleep(2)
        return
    # Don't quit pygame — matches the working game launch pattern
    try:
        subprocess.run([ra])
    except Exception as e:
        pass
    # Reinit display after RetroArch exits
    screen, W, H, F, joy = init_display()
    init_sounds()
    apply_volume()
    pygame.event.clear()


def main():
    """Main application loop — profile first, then hub menu."""
    global screen, W, H, F, joy, active_cfg

    # Load global config
    active_cfg = load_global_config()
    apply_volume()

    # First-time setup check
    if needs_first_time_setup():
        first_time_setup()

    reload_games()

    # ─── Profile selection (before main menu) ──────────────────
    while True:
        user = screen_user_picker()
        if user is None:
            # User pressed back on profile picker = exit app
            if confirm_dialog("Exit PS2 Picker?"):
                pygame.quit(); sys.exit()
            continue
        break

    apply_user_settings(user)

    # ─── Main menu loop ────────────────────────────────────────
    while True:
        choice = screen_main_menu()

        if choice == "Games":
            # Card select → game picker
            while True:
                card = screen_memcard_picker(user)
                if card is None:
                    break  # back to main menu

                while True:
                    launched = screen_game_picker(user, card)
                    if launched:
                        screen, W, H, F, joy = init_display()
                        init_sounds()
                        apply_volume()
                        show_welcome_back(user)
                        pygame.event.clear()
                    else:
                        break  # back to card picker

        elif choice == "Memory Cards":
            # Card management directly
            screen_memcard_picker(user)

        elif choice == "Cache":
            cache_manager_screen()

        elif choice == "Settings":
            fade_to_black()
            settings_menu(user)
            reload_games()

        elif choice == "RetroArch":
            _launch_retroarch_standalone()

        elif choice == "Exit":
            if confirm_dialog("Exit PS2 Picker?"):
                pygame.quit(); sys.exit()


# ═══ Self-Updater ═══════════════════════════════════════════════

_UPDATE_REPO = "ryangjpriorx/ps2-picker"
_UPDATE_BRANCHES = {0: "main", 1: "testing"}
_UPDATE_FILES = ["ps2_picker.py", "ps2_checker.py"]
_UPDATE_TIMEOUT = 10  # seconds


def _parse_version(text):
    """Extract VERSION = 'x.y.z' from source text and return as tuple."""
    m = re.search(r'VERSION\s*=\s*[\x27"](\d+(?:\.\d+)*)[\x27"]', text)
    if not m:
        return None
    parts = m.group(1).split('.')
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def _download_raw(branch, filename):
    """Download a file from GitHub raw and return its content as string."""
    url = f"https://raw.githubusercontent.com/{_UPDATE_REPO}/{branch}/{filename}"
    req = Request(url, headers={"User-Agent": "PS2Picker-Updater"})
    resp = urlopen(req, timeout=_UPDATE_TIMEOUT)
    return resp.read().decode('utf-8', errors='replace')


def _wait_any_button():
    """Block until any key or joystick button is pressed."""
    pygame.event.clear()
    while True:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type in (pygame.KEYDOWN, pygame.JOYBUTTONDOWN):
                return
        clock.tick(30)


def _self_update():
    """Check for updates and replace files if a newer version is available.

    Reads UPDATE_CHANNEL to pick the branch. Downloads each file from GitHub,
    compares VERSION strings. If remote is newer, overwrites local files.
    If versions match, continues silently. On any failure, notifies the user
    but allows them to proceed.

    Returns True if files were updated (caller should re-exec), False otherwise.
    """
    branch = _UPDATE_BRANCHES.get(UPDATE_CHANNEL, "main")
    channel_label = "testing" if UPDATE_CHANNEL == 1 else "stable"

    # Show a brief status message while checking
    screen.fill(BG)
    msg = F['md'].render(f"Checking for updates ({channel_label})...", True, TXT)
    screen.blit(msg, msg.get_rect(center=(W // 2, H // 2)))
    pygame.display.flip()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    updated_any = False
    newest_ver = None

    for filename in _UPDATE_FILES:
        local_path = os.path.join(script_dir, filename)

        # Read local version (if file exists)
        local_ver = None
        if os.path.isfile(local_path):
            try:
                with open(local_path, 'r', encoding='utf-8', errors='replace') as fh:
                    local_ver = _parse_version(fh.read())
            except Exception:
                local_ver = None

        # Download remote version
        try:
            remote_text = _download_raw(branch, filename)
        except Exception as e:
            # Network failure — notify but allow proceeding
            screen.fill(BG)
            err1 = F['md'].render(f"Update check failed for {filename}", True, HDR)
            err2 = F['sm'].render(str(e)[:80], True, TXT_DIM)
            err3 = F['sm'].render("Press any button to continue", True, ACCENT)
            screen.blit(err1, err1.get_rect(center=(W // 2, H // 2 - scaled(20))))
            screen.blit(err2, err2.get_rect(center=(W // 2, H // 2 + scaled(4))))
            screen.blit(err3, err3.get_rect(center=(W // 2, H // 2 + scaled(28))))
            pygame.display.flip()
            _wait_any_button()
            continue

        remote_ver = _parse_version(remote_text)

        # Compare versions
        if remote_ver is None:
            continue  # Can't parse remote version — skip

        if local_ver is not None and remote_ver <= local_ver:
            continue  # Already up to date

        # Remote is newer (or local file missing/unparseable) — replace
        try:
            tmp_path = local_path + ".update_tmp"
            with open(tmp_path, 'w', encoding='utf-8') as fh:
                fh.write(remote_text)
            if IS_WINDOWS and os.path.exists(local_path):
                os.remove(local_path)
            os.rename(tmp_path, local_path)
            updated_any = True
            newest_ver = remote_ver
        except Exception as e:
            screen.fill(BG)
            err1 = F['md'].render(f"Failed to update {filename}", True, HDR)
            err2 = F['sm'].render(str(e)[:80], True, TXT_DIM)
            err3 = F['sm'].render("Press any button to continue", True, ACCENT)
            screen.blit(err1, err1.get_rect(center=(W // 2, H // 2 - scaled(20))))
            screen.blit(err2, err2.get_rect(center=(W // 2, H // 2 + scaled(4))))
            screen.blit(err3, err3.get_rect(center=(W // 2, H // 2 + scaled(28))))
            pygame.display.flip()
            _wait_any_button()

    if updated_any:
        ver_str = '.'.join(str(p) for p in newest_ver) if newest_ver else '?'
        screen.fill(BG)
        u1 = F['lg'].render("Update Applied!", True, HDR)
        u2 = F['md'].render(f"Updated to v{ver_str} ({channel_label})", True, ACCENT)
        u3 = F['sm'].render("Restarting...", True, TXT_DIM)
        screen.blit(u1, u1.get_rect(center=(W // 2, H // 2 - scaled(20))))
        screen.blit(u2, u2.get_rect(center=(W // 2, H // 2 + scaled(6))))
        screen.blit(u3, u3.get_rect(center=(W // 2, H // 2 + scaled(28))))
        pygame.display.flip()
        time.sleep(2)
        return True

    return False


# ═══ Entry Point ═══════════════════════════════════════════════

init_sounds()

# Self-update check (skip with --skip-update)
if '--skip-update' not in sys.argv:
    if _self_update():
        # Files were replaced — re-exec to load the new version
        pygame.quit()
        os.execv(sys.executable, [sys.executable] + sys.argv + ['--skip-update'])
else:
    # Remove the flag so it doesn't persist into future re-execs
    sys.argv = [a for a in sys.argv if a != '--skip-update']

show_splash()
main()

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

VERSION = '0.0.5'

# ─── Standard Library Imports ───────────────────────────────────
import os, sys, subprocess, glob, shutil, time, json, warnings, struct, math, platform, zipfile

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
    """Refresh the game list from active config rom_dir. Cached games sort to top."""
    global games
    rom_dir = active_cfg.get("rom_dir", "")
    if rom_dir and os.path.isdir(rom_dir):
        all_games = [f for f in os.listdir(rom_dir) if f.lower().endswith(EXTS)]
        cached_keys = set(load_cache_manifest().keys())
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
    "shoulder_l": 4,   # L1 — toggle hidden files, etc.
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
    bar_h = scaled(22)
    bar_rect = pygame.Rect(0, H - bar_h, W, bar_h)
    pygame.draw.rect(screen, BAR_BG, bar_rect)
    pygame.draw.line(screen, ACCENT, (0, H - bar_h), (W, H - bar_h), 1)
    hs = F['sm'].render(text, True, HINT)
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


def draw_list(items, sel_idx, scroll, colors=None):
    """Draw a scrollable list. items = list of (text, is_special) tuples."""
    top = scaled(50)
    vis = VISIBLE
    for i in range(scroll, min(scroll + vis, len(items))):
        y = top + (i - scroll) * LINE_H
        text, special = items[i]
        display = truncate(text, F['md'], W - scaled(30))
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
            screen.blit(tag, (W - scaled(22), y + scaled(3)))


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
    items = [
        ("Volume", "volume"),
        ("ROM Folder", "rom_dir"),
        ("RetroArch Core", "core_path"),
        ("Cache Folder", "local_cache_dir"),
        ("Max Cached Games", "max_cached_games"),
        ("Theme Colors", "theme"),
        ("Controller Mapping", "controller_map"),
        ("Back", "back"),
    ]
    setting_hints = {
        "volume":          "Adjust UI sound effects volume or mute entirely",
        "rom_dir":         "Folder containing your PS2 game archives (.zip, .7z, .iso, .chd)",
        "core_path":       "Path to the PCSX2 RetroArch core (.so or .dll)",
        "local_cache_dir": "Where extracted games are stored for faster loading",
        "max_cached_games":"Number of extracted games to keep before oldest is removed (1-20)",
        "theme":           "Choose a color preset or create your own custom theme",
        "controller_map":  "Remap controller buttons for all menu actions",
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
                    play_sfx('back'); return
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
                        return
                if ev.button == BTN["back"]:
                    play_sfx('back'); return
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
            if key not in ("back", "theme", "controller_map"):
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
            elif key in ("theme", "controller_map"):
                # Show a chevron to indicate submenu
                chev = F['md'].render("\u203A", True, ACCENT if is_sel else TXT_DIM)
                screen.blit(chev, (rect.right - chev.get_width() - scaled(12), rect.y + scaled(4)))

            y += scaled(44)

        current_key = items[sel][1]
        hint = setting_hints.get(current_key, "")
        if username:
            hint += "  (per-user)"
        draw_hint_bar(hint)
        pygame.display.flip()
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
    elif key == "controller_map":
        controller_mapping_submenu()
        return  # Mapping saves itself directly to global config

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


def evict_cached_picker():
    """Interactive picker to choose which cached game to remove when cache is full."""
    max_cached = active_cfg.get("max_cached_games", 3)
    manifest = load_cache_manifest()
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



# ═══ Screen: User Picker ═══════════════════════════════════════

def screen_user_picker():
    """Pick or create a user profile. Returns username or None."""
    sel = 0
    scroll = 0
    last_joy = 0

    while True:
        users = get_users()
        items = [(u, False) for u in users] + [("+ New Profile", True)]
        sel = max(0, min(sel, len(items) - 1))
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
                    sel = min(len(items) - 1, sel + 1); play_sfx('navigate')
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    if sel == len(items) - 1:
                        play_sfx('select')
                        name = on_screen_keyboard("Enter Username")
                        if name and name.strip():
                            name = name.strip()
                            if name not in users:
                                create_user(name)
                            return name
                    else:
                        play_sfx('select'); return users[sel]
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"]:
                    if sel == len(items) - 1:
                        play_sfx('select')
                        name = on_screen_keyboard("Enter Username")
                        if name and name.strip():
                            name = name.strip()
                            if name not in users:
                                create_user(name)
                            return name
                    else:
                        play_sfx('select'); return users[sel]
                if ev.button == BTN["back"]:
                    play_sfx('back'); return None
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

        if sel < scroll:
            scroll = sel
        if sel >= scroll + VISIBLE:
            scroll = sel - VISIBLE + 1

        screen.fill(BG)
        draw_header("Select Profile", None,
                     f"{len(users)} profile{'s' if len(users) != 1 else ''}")
        draw_list(items, sel, scroll)

        draw_hint_bar("[A] Select   [B] Exit")
        pygame.display.flip()
        clock.tick(30)


# ═══ Screen: Memory Card Picker ════════════════════════════════

def screen_memcard_picker(user):
    """Pick or create a memory card. Returns card name or None."""
    sel = 0
    scroll = 0
    last_joy = 0

    while True:
        cards = get_cards(user)
        items = [(c, False) for c in cards] + [("+ New Card", True)]
        sel = max(0, min(sel, len(items) - 1))
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
                    sel = min(len(items) - 1, sel + 1); play_sfx('navigate')
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    if sel == len(items) - 1:
                        play_sfx('select')
                        name = on_screen_keyboard("Card Name")
                        if name:
                            create_card(user, name)
                    elif sel < len(cards):
                        play_sfx('select'); return cards[sel]
                if ev.key == pygame.K_DELETE or ev.key == pygame.K_x:
                    if sel < len(cards) and len(cards) > 1:
                        if confirm_dialog(f"Delete {cards[sel]}?"):
                            play_sfx('select')
                            delete_card(user, cards[sel])
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"]:
                    if sel == len(items) - 1:
                        play_sfx('select')
                        name = on_screen_keyboard("Card Name")
                        if name:
                            create_card(user, name)
                    elif sel < len(cards):
                        play_sfx('select'); return cards[sel]
                if ev.button == BTN["back"]:
                    play_sfx('back'); return None
                if ev.button == BTN["extra"] and sel < len(cards) and len(cards) > 1:
                    if confirm_dialog(f"Delete {cards[sel]}?"):
                        play_sfx('select')
                        delete_card(user, cards[sel])
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

        if sel < scroll:
            scroll = sel
        if sel >= scroll + VISIBLE:
            scroll = sel - VISIBLE + 1

        screen.fill(BG)
        draw_header(f"{user}'s Cards", None,
                     f"{len(cards)} card{'s' if len(cards) != 1 else ''}")
        draw_list(items, sel, scroll)

        draw_hint_bar("[A] Select   [B] Back   [X] Delete card")
        pygame.display.flip()
        clock.tick(30)


# ═══ Screen: Game Picker ═══════════════════════════════════════

def screen_game_picker(user, card):
    """Pick and launch a game. Returns True if game ran, False to go back."""
    global joy
    sel = 0
    scroll = 0
    last_joy_time = 0
    search_text = ""
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
                    play_sfx('back'); return False
                if ev.type == pygame.JOYBUTTONDOWN:
                    if ev.button == BTN["back"]:
                        play_sfx('back'); return False
                    if ev.button == BTN["start"]:
                        play_sfx('select'); settings_menu(user); reload_games()
            clock.tick(30)
            continue

        sel = max(0, min(sel, len(filtered) - 1)) if filtered else 0
        now = time.time()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    play_sfx('back'); return False
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
                    cached_keys = set(load_cache_manifest().keys())
                    if result:
                        return True
                if ev.key == pygame.K_F1:
                    play_sfx('select'); settings_menu(user); reload_games()
                    cached_keys = set(load_cache_manifest().keys())
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == BTN["confirm"] and filtered:  # A = launch
                    play_sfx('launch')
                    result = extract_and_launch(filtered[sel], user, card)
                    cached_keys = set(load_cache_manifest().keys())
                    if result:
                        return True
                if ev.button == BTN["back"]:  # B = back
                    play_sfx('back'); return False
                if ev.button == BTN["start"]:  # Start = settings
                    play_sfx('select'); settings_menu(user); reload_games()
                    cached_keys = set(load_cache_manifest().keys())
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

        screen.fill(BG)
        title = f"Games - {user} / {card}"
        search_hint = f" (filter: {search_text})" if search_text else ""
        draw_header(title + search_hint, None,
                     f"{len(filtered)} game{'s' if len(filtered) != 1 else ''}")

        if filtered:
            items_display = [(truncate(strip_ext(g), F['md'], W - scaled(60)),
                              strip_ext(g) in cached_keys) for g in filtered]
            draw_list(items_display, sel, scroll)

        draw_hint_bar("[A] Launch   [B] Back   [Start] Settings   [Select] Search")
        pygame.display.flip()
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

def main():
    """Main application loop."""
    global screen, W, H, F, joy, active_cfg

    # Load global config
    active_cfg = load_global_config()
    apply_volume()

    # First-time setup check
    if needs_first_time_setup():
        first_time_setup()

    reload_games()

    while True:
        # User picker
        user = screen_user_picker()
        if user is None:
            if confirm_dialog("Exit PS2 Picker?"):
                pygame.quit(); sys.exit()
            continue

        # Apply per-user settings
        apply_user_settings(user)

        # Memory card picker loop
        while True:
            card = screen_memcard_picker(user)
            if card is None:
                break  # Back to user picker

            # Game picker loop
            while True:
                launched = screen_game_picker(user, card)
                if launched:
                    # Game was played, reinit display
                    screen, W, H, F, joy = init_display()
                    init_sounds()
                    apply_volume()

                    # Welcome back
                    show_welcome_back(user)

                    # Flush stale input
                    pygame.event.clear()
                    # Continue inner loop -> back to game picker
                else:
                    break  # Back to memcard picker


# ═══ Entry Point ═══════════════════════════════════════════════

init_sounds()

# Dependency check runs before splash — --check-deps forces it even when all OK

show_splash()
main()

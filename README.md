> **Disclaimer:** This project's codebase was created with the assistance of **Microsoft Copilot AI**. All logic, structure, and design decisions were reviewed and directed by the project author.

# PS2 Picker

A controller‑first PS2 game launcher designed specifically for **RG35XX‑H**, **RG35XX‑Plus**, and similar handhelds using **Moonlight/Sunshine** to stream from a PC.

Designed for **640×480** but features auto-scaling, uses a **high‑contrast handheld‑friendly UI**, and provides **fast game launching**, **per‑user memory cards**, and a **local cache** to reduce load times over streaming.

---

## ✨ Key Features

### 🎮 Built for Handheld Streaming (RG35XX‑H, RG35XX‑Plus, etc.)
- UI scaled and tuned for 640×480 displays (auto-scales to any resolution)
- Large hit‑targets, readable fonts, and high‑contrast color palette
- Fully navigable with D‑pad + ABXY + Start/Select
- Works seamlessly through Sunshine's virtual Xbox controller
- Smooth crossfade transitions between all screens
- Lerp-based smooth scrolling with scroll indicators

### 🚀 Fast PS2 Game Launching
- Scans your PS2 ROM directory and builds a clean, sorted list
- Launches games through RetroArch's **PCSX2 libretro core**
- Automatically reinitializes pygame after RetroArch exits
- Cached games sort to the top with a ⚡ indicator for instant access

### 👤 User Profiles
Each user gets:
- Their own profile folder
- Their own memory card files
- Their own last‑played tracking

Perfect for shared handhelds or family setups.

### 💾 Memory Card Management
- Auto‑detects RetroArch memcard directories
- Ensures required `.ps2` files exist
- Per‑user memcard switching without touching RetroArch menus
- Create and delete memory cards from the on-screen UI

### ⚡ Local Cache System
- Keeps up to **3 recently played games** (configurable) in a fast local cache
- Reduces load times when streaming over Sunshine
- Automatic LRU eviction when cache is full with interactive picker
- **Cache Manager** in settings — view per-game sizes, last-used times, delete individually or clear all
- Stale cache entries automatically purged on every reload

### 🎨 Theme System
- 10 built-in color presets (Royal Purple, Ocean Blue, Forest Green, etc.)
- Per-channel HSV color editing for fully custom themes
- Live preview while editing
- Theme shared across picker and checker

### 🔄 Self-Updater
- Automatic update checking on launch via HTTP (no git dependency required)
- Configurable update channel (`main` or `testing`) in config
- One-button update from the checker GUI or terminal prompt
- Auto-restarts after successful update
- Background update check — UI stays responsive during fetch

### 🎛️ Controller Remapping
- Remap all menu actions (confirm, back, scroll, settings, search, etc.)
- On-screen guided remap flow with safety fallbacks
- Stored in config — survives updates
- Shared between picker and checker

### 🛠️ Dependency Checker (Pre-Launcher)
- Pre-launch checker verifies all dependencies before starting
- Themed GUI matching the main app (with terminal fallback)
- Auto-detects RetroArch, cores, 7z, and Python packages
- Guided path input for missing dependencies using on-screen keyboard and file browser
- **Instant auto-launch** — skips directly to the picker when all deps are satisfied
- HTTP-only version checking with configurable update channels

### ⌨️ On-Screen Keyboard & File Browser
- Full on-screen keyboard for text input (usernames, card names, paths)
- File browser for navigating and selecting directories on headless setups
- No physical keyboard required — everything works with a controller

### 🗂 ROM & File Support
- ROM formats: `.zip`, `.7z`, `.iso`, `.chd`
- Game formats: `iso`, `bin`, `chd`, `cue`
- Auto‑scans and filters invalid entries
- Real-time search/filter with Select button

---

## 📁 Directory Layout

| Purpose | Path |
|---------|------|
| Global config | `~/.ps2-picker/config.json` |
| Checker config | `~/.ps2-picker/checker.json` |
| User profiles | `~/ps2-users/<name>/` |
| Local cache | `~/ps2-cache/` |
| Cache manifest | `~/ps2-cache/manifest.json` |
| RetroArch memcards | Auto‑detected |

---

## 🛠 Requirements

- Python 3
- Pygame
- RetroArch with **PCSX2 libretro core**
- 7z (p7zip-full) — for `.7z` archive extraction
- Sunshine (for handheld streaming)
- Moonlight on RG35XX‑H or similar device

---

## ▶️ Running

```bash
# Run the dependency checker first (recommended)
python3 ps2-checker.py

# Or launch directly
python3 ps2-picker.py
```

The script automatically forces `DISPLAY=:0` for headless/streaming setups.

---

## 📦 Installation

```bash
git clone https://github.com/ryangjpriorx/ps2-picker
cd ps2-picker
python3 ps2-checker.py
```

The checker will guide you through any missing dependencies on first run.

---

## ⚙️ Configuration

All settings are stored in `~/.ps2-picker/config.json`:

```json
{
  "rom_dir": "/path/to/ps2/roms",
  "core_path": "/path/to/pcsx2_libretro.so",
  "local_cache_dir": "~/ps2-cache",
  "max_cached_games": 3,
  "volume": 70,
  "update_channel": "main",
  "theme": {
    "bg": [18, 8, 32],
    "accent": [147, 51, 234],
    "highlight": [255, 200, 50],
    "text": [220, 210, 190]
  },
  "button_map": {
    "confirm": 0,
    "back": 1,
    "extra": 2,
    "alt": 3,
    "shoulder_l": 4,
    "select": 6,
    "start": 7
  }
}
```

| Setting | Description |
|---------|-------------|
| `rom_dir` | Folder containing PS2 game archives |
| `core_path` | Path to PCSX2 RetroArch core (.so or .dll) |
| `local_cache_dir` | Where extracted games are cached |
| `max_cached_games` | Number of cached games before LRU eviction (1–20) |
| `volume` | UI sound effects volume (0 = mute) |
| `update_channel` | `"main"` for stable, `"testing"` for latest |
| `theme` | Custom RGBA color overrides (4 base colors) |
| `button_map` | Controller button assignments (Xbox button indices) |

---

## 🎮 Controls

| Button | Game List | User/Card Picker | Settings | Cache Manager |
|--------|-----------|-------------------|----------|---------------|
| **D-Pad / Left Stick** | Navigate | Navigate | Navigate | Navigate |
| **A** | Launch game | Select | Confirm | Delete item |
| **B** | Back | Back / Exit | Back | Back |
| **X** | — | Delete card | — | — |
| **Y** | — | — | — | Clear all |
| **Start** | Open settings | — | — | — |
| **Select** | Search / Filter | — | — | — |

All buttons are remappable from Settings → Controller Mapping.

---

## 📄 License

[MIT License](LICENSE) — free to use, modify, and distribute.

> **Disclaimer:** This project's codebase was created with the assistance of **Microsoft Copilot AI**. All logic, structure, and design decisions were reviewed and directed by the project author.

# PS2 Picker

A controller‑first PS2 game launcher designed specifically for **RG35XX‑H**, **RG35XX‑Plus**, and similar handhelds using **Moonlight/Sunshine** to stream from a PC. Designed for **640×480** but features auto-scaling, uses a **high‑contrast handheld‑friendly UI**, and provides **fast game launching**, **per‑user memory cards**, and a **local cache** to reduce load times over streaming.

## ✨ Key Features

### 🎮 Built for Handheld Streaming (RG35XX‑H, RG35XX‑Plus, etc.)
- UI scaled and tuned for 640×480 displays (but scales to your resolution)
- Large hit‑targets, readable fonts, and high‑contrast color palette
- Fully navigable with D‑pad + ABXY + Start/Select
- Works seamlessly through Sunshine's virtual Xbox controller

### 🚀 Fast PS2 Game Launching
- Scans your PS2 ROM directory and builds a clean, sorted list
- Launches games through RetroArch's **PCSX2 libretro core**
- Automatically reinitializes pygame after RetroArch exits

### 👤 User Profiles
Each user gets:
- Their own profile folder
- Their own memory card files
- Their own recent‑games cache
- Their own last‑played tracking

Perfect for shared handhelds or family setups.

### 💾 Memory Card Management
- Auto‑detects RetroArch memcard directories
- Ensures required `.ps2` files exist
- Per‑user memcard switching without touching RetroArch menus

### ⚡ Local Cache System
- Keeps up to **3 recently played games** (configurable) in a fast local cache
- Reduces load times when streaming over Sunshine
- Automatically manages a manifest file with LRU eviction

### 🎨 Theme System
- 10 built-in color presets (Royal Purple, Ocean Blue, Forest Green, etc.)
- Per-channel HSV color editing for fully custom themes
- Live preview while editing
- Theme shared across picker and checker

### 🔄 Self-Updater
- Automatic update checking on launch via semantic versioning
- Configurable update channel (`main` or `testing`) in config
- One-button update from the checker GUI or terminal prompt
- Auto-restarts after successful update

### 🎛️ Controller Remapping
- Remap all menu actions (confirm, back, scroll, settings, etc.)
- On-screen guided remap flow with safety fallbacks
- Stored in config — survives updates

### 🛠️ Dependency Checker
- Pre-launch checker verifies all dependencies before starting
- Themed GUI matching the main app (with terminal fallback)
- Auto-detects RetroArch, cores, 7z, and Python packages
- Guided path input for missing dependencies

### 🗂 ROM & File Support
- ROM formats: `.zip`, `.7z`, `.iso`, `.chd`
- Game formats: `iso`, `bin`, `chd`, `cue`
- Auto‑scans and filters invalid entries

## 📁 Directory Layout

| Purpose | Path |
|---------|------|
| Global config | `~/.ps2-picker/config.json` |
| User profiles | `~/ps2-users/<name>/` |
| Local cache | `~/ps2-cache/` |
| RetroArch memcards | Auto‑detected |

## 🛠 Requirements
- Python 3
- Pygame
- RetroArch with **PCSX2 libretro core**
- 7z (p7zip-full) — for `.7z` archive extraction
- Sunshine (for handheld streaming)
- Moonlight on RG35XX‑H or similar device

## ▶️ Running

```bash
# Run the dependency checker first (recommended)
python3 ps2-checker.py

# Or launch directly
python3 ps2-picker.py
```

The script automatically forces `DISPLAY=:0` for headless/streaming setups.

## 📦 Installation

```bash
git clone https://github.com/ryangjpriorx/ps2-picker
cd ps2-picker
python3 ps2-checker.py
```

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
  "theme": { "bg": [18,8,32], "accent": [147,51,234], "highlight": [255,200,50], "text": [220,210,190] }
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
| `theme` | Custom RGBA color overrides |

## 🎮 Controls

| Button | Action |
|--------|--------|
| **D-Pad Up/Down** | Navigate menus |
| **A** | Confirm / Launch game |
| **B** | Back / Cancel |
| **X** | Delete user (user picker) |
| **Y** | Update (checker) |
| **Start** | Open settings |
| **Select** | Switch sort order (game list) |

All buttons are remappable from Settings → Controller Mapping.

## 📄 License

[MIT License](LICENSE) — free to use, modify, and distribute.

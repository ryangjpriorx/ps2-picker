> **Disclaimer:**  
> This project’s codebase was created with the assistance of **Microsoft Copilot AI**.  
> All logic, structure, and design decisions were reviewed and directed by the project author.

# 🎮 PS2 Picker

A controller-driven PS2 game launcher built with Python and Pygame — designed for handhelds, streaming setups, and headless devices running RetroArch.

No mouse or keyboard required. Everything is navigable with a gamepad.

---

## ✨ Features

### 🕹️ Game Management
- **Archive extraction** — Launch games directly from `.zip` and `.7z` archives; PS2 Picker extracts them on the fly
- **Smart LRU cache** — Extracted games are cached locally with configurable size limits, so frequently played titles launch instantly
- **Cached games first** — Cached (ready-to-play) games are sorted to the top of the list with a ⚡ indicator
- **Supported formats** — `.zip`, `.7z`, `.iso`, `.chd` archives containing `.iso`, `.bin`, `.chd`, or `.cue` disc images

### 👤 User Profiles
- **Multiple users** — Each player gets their own profile with independent save data
- **Memory card management** — Create, switch, and delete memory card slots per user
- **Automatic card loading** — The selected card is swapped into RetroArch before launch and saved back after
- **Backup on swap** — Active memory cards are backed up before being overwritten

### 🎨 Customization
- **7 built-in theme presets** — Royal Purple & Gold, Ocean Blue, Forest Green, Crimson Red, Midnight Teal, Retro Amber, Slate Gray
- **Custom colors** — HSV color sliders with gradient tracks and live preview for per-channel editing (background, accent, highlight, text)
- **Controller remapping** — Remap all 7 button actions through an intuitive submenu with 2-second hold-to-confirm and conflict detection
- **Safety first** — Keyboard always works as a fallback, so you can never lock yourself out of the UI

### 🖥️ Display & Audio
- **Resolution-aware scaling** — Reference design is 480p; all UI elements scale proportionally to any resolution
- **Procedural sound effects** — All UI sounds are generated from sine waves at runtime — zero external audio files needed
- **On-screen keyboard** — Full text input without a physical keyboard (for naming profiles, cards, manual paths)

### ⚙️ Setup & Configuration
- **First-time wizard** — Auto-detects RetroArch installation and PS2 cores; walks you through ROM folder selection
- **Per-user settings** — Override global settings (ROM dir, core path, cache size) on a per-profile basis
- **File browser** — Full filesystem navigation with drive selection (Windows), hidden file toggle, and manual path input

---

## 📦 Installation

### Prerequisites

| Dependency | Required | Purpose |
|---|---|---|
| Python 3.8+ | ✅ | Runtime |
| pygame | ✅ | GUI framework |
| RetroArch | ✅ | PS2 emulation backend |
| PCSX2 core | ✅ | RetroArch core for PS2 (`pcsx2_libretro.so` / `.dll`) |
| 7-Zip (`7z`) | ⚠️ | Required only for `.7z` archives |

### Quick Start

```bash
# Clone the repo
git clone https://github.com/ryangjpriorx/ps2-picker.git
cd ps2-picker

# Run the dependency checker (installs missing deps interactively)
python3 ps2-checker.py

# Or launch directly
python3 ps2-picker.py
```

### Install Dependencies Manually

<details>
<summary><b>Debian / Ubuntu / Raspberry Pi OS</b></summary>

```bash
sudo apt install python3-pygame p7zip-full retroarch
```
</details>

<details>
<summary><b>Fedora</b></summary>

```bash
sudo dnf install python3-pygame p7zip p7zip-plugins retroarch
```
</details>

<details>
<summary><b>Arch Linux</b></summary>

```bash
sudo pacman -S python-pygame p7zip retroarch
```
</details>

<details>
<summary><b>Windows</b></summary>

```powershell
pip install pygame
winget install 7zip.7zip
winget install Libretro.RetroArch
```
</details>

---

## 🚀 Usage

### Pre-Launcher (Dependency Checker)

```bash
python3 ps2-checker.py              # Check deps, launch if all OK
python3 ps2-checker.py --check-only  # Check deps, don't launch
python3 ps2-checker.py --terminal    # Force terminal mode (no GUI)
```

The pre-launcher validates all dependencies before launching the main app. It provides platform-specific install commands and can auto-install missing packages. It shares the same theme as the main app.

### Main App

```bash
python3 ps2-picker.py               # Launch normally
```

On first run, PS2 Picker walks you through a setup wizard to configure your ROM folder, RetroArch path, and PS2 core.

---

## 🎮 Controls

All navigation is controller-driven. Keyboard keys always work as a safety fallback.

| Action | Default Button | Keyboard Fallback |
|---|---|---|
| Confirm / Select | A (Button 0) | Enter / Space |
| Back / Cancel | B (Button 1) | Escape |
| Extra Action | X (Button 2) | — |
| Alt Action | Y (Button 3) | — |
| Left Shoulder | L1 (Button 4) | — |
| Search | Select (Button 6) | — |
| Settings | Start (Button 7) | F1 |
| Navigate | D-pad / Left Stick | Arrow Keys |

All button assignments can be remapped in **Settings → Controller Mapping**. Remapping uses a 2-second hold to confirm, with automatic conflict detection and swap.

---

## 🗂️ File Structure

```
~/.ps2-picker/
├── config.json          # Global settings, theme, button map

~/ps2-users/
├── <username>/
│   ├── meta.json        # Last-used card, per-user settings
│   ├── cards/
│   │   ├── Card 1/
│   │   │   ├── Mcd001.ps2
│   │   │   └── Mcd002.ps2
│   │   └── Card 2/
│   │       └── ...
│   └── backup/          # Auto-backup of last loaded card

~/ps2-cache/
├── manifest.json        # Tracks cached games (LRU order)
├── <game_name>/
│   └── game.iso         # Extracted disc image
└── ...
```

---

## 🎨 Themes

PS2 Picker ships with 7 built-in theme presets. You can also create fully custom themes by editing individual color channels with HSV sliders.

The theme system uses 4 base colors to derive all 15 UI colors automatically:

| Base Color | Controls |
|---|---|
| **Background** | Main background, key/bar backgrounds |
| **Accent** | Selection highlights, borders, progress bars |
| **Highlight** | Headers, selected text, slider thumbs |
| **Text** | Body text, hints, dimmed labels |

Themes are saved globally and shared between the main app and the pre-launcher.

### Built-in Presets

- 🟣 Royal Purple & Gold (default)
- 🔵 Ocean Blue
- 🟢 Forest Green
- 🔴 Crimson Red
- 🟢 Midnight Teal
- 🟠 Retro Amber
- ⚪ Slate Gray

---

## ⚙️ Settings

Access settings by pressing **Start** during the game browser.

| Setting | Description |
|---|---|
| Volume | UI sound effects volume with mute toggle |
| ROM Folder | Path to your PS2 ROM archive directory |
| RetroArch Core | Path to the PCSX2 RetroArch core |
| Cache Folder | Where extracted games are stored |
| Max Cached Games | LRU cache size (1–20 games) |
| Theme Colors | Color presets and custom HSV editing |
| Controller Mapping | Remap all 7 button actions |

---

## 🛡️ Safety Features

- **Keyboard fallback** — Arrow keys, Enter, Escape, and Space are never remapped and always work
- **Memory card backups** — Active cards are backed up before every swap
- **Controller remap safety** — 2-second hold to confirm new bindings; conflict detection with swap; per-key and full reset with confirmation dialogs
- **Confirmation dialogs** — Destructive actions (delete card, reset bindings) always require confirmation

---


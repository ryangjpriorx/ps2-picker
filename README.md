> **Disclaimer:** This project's codebase was created with the assistance of **Microsoft Copilot AI**. All logic, structure, and design decisions were reviewed and directed by the project author.

# PS2 Picker

A controller‑first PS2 game launcher designed specifically for **RG35XX‑H**, **RG35XX‑Plus**, and similar handhelds using **Moonlight/Sunshine** to stream from a PC.

Designed for **640×480** but features auto-scaling, uses a **high‑contrast handheld‑friendly UI**, and provides **fast game launching**, **per‑user memory cards**, and a **local cache** to reduce load times over streaming.

---

# PS2 Picker

A controller-driven PS2 game launcher with profile management, memory card browsing, and a fully themed UI — built for couch setups, handhelds, and headless devices.

![Python 3.6+](https://img.shields.io/badge/python-3.6%2B-blue) ![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-lightgrey)

---

## Features

### Game Management
- **Archive extraction** — Launch games directly from `.zip` and `.7z` archives; PS2 Picker extracts them automatically with a real-time progress bar
- **LRU game cache** — Extracted games are cached locally with a configurable size limit (default 3). Least-recently-used entries are evicted first
- **Cache manager** — View cached games with size and last-used info, delete individual entries or clear everything
- **In-app search** — Press Select to filter your game list by name
- **Supported formats** — `.zip`, `.7z`, `.iso`, `.chd` archives containing `.iso`, `.bin`, `.chd`, or `.cue` disc images

### User Profiles
- **Multiple profiles** — Each user gets their own directory under `~/ps2-users/` with independent settings
- **Memory card management** — Create, load, and switch between named memory card snapshots per profile. Cards are copied to/from the PCSX2 memcard directory on launch
- **PS2 save browser** — Reads actual PS2 memory card filesystem images (`.ps2`), displaying individual game saves with titles, sizes, file counts, timestamps, and extracted save icons
- **Welcome back splash** — Personalized animated greeting when switching to a returning profile

### Theming
- **7 built-in presets** — Royal Purple & Gold (default), Ocean Blue, Forest Green, Crimson Red, Midnight Teal, Retro Amber, Slate Gray
- **Custom themes** — Per-channel HSV color sliders for background, accent, highlight, and text with live preview
- **Consistent styling** — Both `ps2-picker` and `ps2-checker` share the same theme system, so the pre-launcher matches your chosen colors

### Input
- **Full controller support** — Navigate everything with a gamepad: menus, file browser, on-screen keyboard, color pickers, and dialogs
- **Remappable buttons** — Remap all 8 actions (Confirm, Back, Extra, Alt, L1, R1, Select, Start) via Settings > Controller Mapping
- **Keyboard fallback** — Arrow keys, Enter, Escape, and Space always work regardless of controller mapping — no risk of locking yourself out
- **Analog + D-pad + hat** — Stick, D-pad, and hat inputs all work for navigation
- **Hot-plug detection** — Controllers are detected at the splash screen and re-polled if disconnected

### UI & Audio
- **Resolution-aware scaling** — All UI elements scale from a 480p reference, so the interface looks correct at any resolution
- **Procedural sound effects** — Navigate, select, back, error, and success sounds are generated at runtime — no external audio files needed
- **Animated transitions** — Fade-to-black and fade-from-black screen transitions throughout
- **On-screen keyboard** — Full text input without a physical keyboard, for naming profiles, cards, and search queries
- **PS2 BIOS boot** — Hold L2 on the main menu to launch RetroArch with the PS2 core in BIOS mode (no ROM)

### Updates
- **Two update channels** — Stable (main branch) and Testing, switchable from the settings menu
- **Self-updating** — Both `ps2-picker.py` and `ps2-checker.py` pull updates directly from GitHub with version comparison and changelog display

---

## Requirements

| Dependency | Required | Purpose |
|------------|----------|---------|
| **Python 3.6+** | Yes | Runtime |
| **pygame** | Yes | Display, input, audio |
| **RetroArch** | Yes | Emulator backend (launches games via PCSX2 core) |
| **7z** (p7zip) | No | Extraction of `.7z` archives (`.zip` is handled natively) |

### Running the Dependency Checker

`ps2-checker.py` is a standalone pre-launcher that verifies all dependencies, offers install commands for your distro, and handles self-updates — all before launching the main app.

```bash
# Full check → update → launch
python3 ps2-checker.py

# Check dependencies only (don't launch)
python3 ps2-checker.py --check-only

# Force terminal mode (no GUI)
python3 ps2-checker.py --terminal

# Skip the update check
python3 ps2-checker.py --skip-update
```

The checker has both a themed GUI mode (when pygame is available) and a colored terminal fallback. It auto-detects your distro and shows the correct install commands.

---

## Supported Platforms

The dependency checker provides tailored install commands for:

| Platform | pygame | 7z | RetroArch |
|----------|--------|----|-----------|
| **Debian / Ubuntu** | `apt install python3-pygame` | `apt install p7zip-full` | `apt install retroarch` |
| **Arch Linux** | `pacman -S python-pygame` | `pacman -S p7zip` | `pacman -S retroarch` |
| **Fedora** | `dnf install python3-pygame` | `dnf install p7zip p7zip-plugins` | `dnf install retroarch` |
| **SteamOS (Steam Deck)** | `pip install --user pygame` | `pacman -S p7zip` | Flatpak |
| **Batocera** | Pre-installed | Pre-installed | Pre-installed |
| **openSUSE** | `zypper install python3-pygame` | `zypper install p7zip-full` | `zypper install retroarch` |
| **Gentoo** | `emerge dev-python/pygame` | `emerge app-arch/p7zip` | `emerge games-emulation/retroarch` |
| **Void Linux** | `xbps-install python3-pygame` | `xbps-install p7zip` | `xbps-install retroarch` |
| **Alpine** | `apk add py3-pygame` | `apk add p7zip` | `apk add retroarch` |
| **NixOS** | `nix-env -iA nixpkgs.python3Packages.pygame` | `nix-env -iA nixpkgs.p7zip` | `nix-env -iA nixpkgs.retroarch` |
| **Windows** | `pip install pygame` | `winget install 7zip.7zip` | `winget install Libretro.RetroArch` |

---

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/ryangjpriorx/ps2-picker.git
cd ps2-picker

# 2. Run the checker to verify dependencies and launch
python3 ps2-checker.py
```

On first launch, PS2 Picker runs a setup wizard that:
1. Auto-detects your RetroArch installation and PCSX2 core
2. Asks you to pick your ROM folder (via the built-in file browser)
3. Creates your first user profile

If auto-detection finds everything, setup is just two steps: choose your ROM folder and enter a username.

---

## Usage

```bash
# Launch normally
python3 ps2-picker.py

# Run dependency checker first
python3 ps2-picker.py --check-deps
```

### Default Controls

| Button | Action |
|--------|--------|
| **A** | Confirm / Select / Launch |
| **B** | Back / Cancel |
| **X** | Extra action (context-dependent) |
| **Y** | Alt action (context-dependent) |
| **L1 / R1** | Page left / right, toggle hidden files |
| **Select** | Search games |
| **Start** | Open settings |
| **L2 (hold)** | Boot PS2 BIOS (main menu only) |

All buttons are remappable via **Settings > Controller Mapping**. Keyboard navigation (arrows, Enter, Escape, Space) always works as a safety fallback.

---

## Settings

Accessible from the main menu or in-game via Start:

| Setting | Description |
|---------|-------------|
| **Volume** | UI sound effects volume with mute toggle |
| **ROM Folder** | Path to your PS2 game archives |
| **RetroArch Core** | Path to the PCSX2 RetroArch core (`.so` / `.dll`) |
| **Cache Folder** | Where extracted games are stored (default: `~/ps2-cache/`) |
| **Max Cached Games** | LRU cache size limit, 1–20 (default: 3) |
| **Manage Cache** | Browse and delete cached extractions |
| **Theme Colors** | Preset picker or custom HSV color editor |
| **Controller Mapping** | Remap all gamepad buttons |
| **Update Channel** | Switch between Stable and Testing branches |

---

## File Structure

```
~/.ps2-picker/
  └── config.json          # Global settings, theme, button mappings

~/ps2-users/
  └── <username>/
      ├── meta.json         # Profile metadata (last card, play stats)
      └── cards/
          └── <cardname>/
              ├── Mcd001.ps2  # Memory card slot 1 snapshot
              └── Mcd002.ps2  # Memory card slot 2 snapshot

~/ps2-cache/
  ├── manifest.json         # Cache index (game name, size, timestamps)
  └── <game-hash>/          # Extracted disc images
```

---

## License

This project is provided as-is for personal use.


> **Disclaimer:**  
> This project’s codebase was created with the assistance of **Microsoft Copilot AI**.  
> All logic, structure, and design decisions were reviewed and directed by the project author.

# PS2 Picker
A controller‑first PS2 game launcher designed specifically for **RG35XX‑H**, **RG35XX‑Plus**, and similar handhelds using **Moonlight/Sunshine** to stream from a PC.  
Runs at **640×480**, uses a **high‑contrast handheld‑friendly UI**, and provides **fast game launching**, **per‑user memory cards**, and a **local cache** to reduce load times over streaming.

---

## ✨ Key Features

### 🎮 Built for Handheld Streaming (RG35XX‑H, RG35XX‑Plus, etc.)
- UI scaled and tuned for 640×480 displays  
- Large hit‑targets, readable fonts, and high‑contrast color palette  
- Fully navigable with D‑pad + ABXY + Start/Select  
- Works seamlessly through Sunshine’s virtual Xbox controller  

### 🚀 Fast PS2 Game Launching
- Scans your PS2 ROM directory and builds a clean, sorted list  
- Launches games through RetroArch’s **PCSX2 libretro core**  
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
- Keeps up to **3 recently played games** in a fast local cache  
- Reduces load times when streaming over Sunshine  
- Automatically manages a manifest file  

### 🎨 Handheld‑Optimized Theme
- Royal Purple & Gold color scheme  
- Clear focus states  
- Hint bar with controller prompts  
- Consistent UI across:  
  - User picker  
  - Game list  
  - Memory card manager  
  - On‑screen keyboard  

### 🗂 ROM & File Support
- ROM formats: `.zip`, `.7z`, `.iso`, `.chd`  
- Game formats: `iso`, `bin`, `chd`, `cue`  
- Auto‑scans and filters invalid entries  

---

## 📁 Directory Layout

| Purpose | Path |
|--------|------|
| PS2 ROMs | `/mnt/romm/romm/library/roms/ps2` |
| Remote cache | `/mnt/romm/romm/cache` |
| Local cache | `~/ps2-cache` |
| User profiles | `~/ps2-users` |
| RetroArch memcards | Auto‑detected |

---

## 🛠 Requirements
- Python 3  
- Pygame  
- RetroArch with **PCSX2 libretro core**  
- Sunshine (for handheld streaming)  
- Moonlight on RG35XX‑H or similar device  

---

## ▶️ Running
```bash
python3 ps2-picker.py
```

The script automatically forces `DISPLAY=:0` for headless/streaming setups.

---

## 📦 Installation
```bash
git clone https://github.com/ryangjpriorx/ps2-picker
cd ps2-picker
python3 ps2-picker.py
```


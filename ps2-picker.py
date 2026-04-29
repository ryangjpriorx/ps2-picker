#!/usr/bin/env python3
"""PS2 Games Launcher with User Profiles and Memory Card Management
   Purple/Gold themed, 640x480 optimized, controller-driven"""

import os, sys, subprocess, glob, shutil, time, json, warnings

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
os.environ['DISPLAY'] = ':0'
warnings.filterwarnings('ignore')
import pygame

# ═══ Configuration ══════════════════════════════════════════════
ROM_DIR = "/mnt/romm/romm/library/roms/ps2"
CACHE_DIR = "/mnt/romm/romm/cache"
CORE = os.path.expanduser("~/.config/retroarch/cores/pcsx2_libretro.so")
USERS_DIR = os.path.expanduser("~/ps2-users")
EXTS = ('.zip', '.7z', '.iso', '.chd')
GAME_EXTS = ('iso', 'bin', 'chd', 'cue')
MEMCARD_FILES = ('Mcd001.ps2', 'Mcd002.ps2')


def find_memcard_dir():
    for d in [
        os.path.expanduser("~/.config/retroarch/system/pcsx2/memcards"),
        os.path.expanduser("~/.config/retroarch/saves/pcsx2/memcards"),
    ]:
        if os.path.isdir(d):
            return d
    default = os.path.expanduser("~/.config/retroarch/system/pcsx2/memcards")
    os.makedirs(default, exist_ok=True)
    return default


MEMCARD_ACTIVE = find_memcard_dir()

# ═══ Theme (Royal Purple & Gold) ════════════════════════════════
BG       = (18, 8, 32)
SEL_BG   = (88, 28, 135)
TXT      = (220, 210, 190)
TXT_SEL  = (255, 215, 0)
TXT_DIM  = (140, 130, 120)
HDR      = (255, 200, 50)
HINT     = (140, 100, 170)
BAR_BG   = (40, 20, 65)
BAR_FG   = (180, 140, 50)
BAR_BORDER = (120, 80, 180)
ACCENT   = (147, 51, 234)
DANGER   = (200, 60, 60)
KEY_BG   = (40, 18, 65)
KEY_SEL  = (120, 50, 180)

os.makedirs(USERS_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)


# ═══ User & Memory Card Management ═════════════════════════════

def get_users():
    if not os.path.isdir(USERS_DIR):
        return []
    return sorted([d for d in os.listdir(USERS_DIR)
                   if os.path.isdir(os.path.join(USERS_DIR, d))],
                  key=str.lower)


def create_user(name):
    udir = os.path.join(USERS_DIR, name)
    os.makedirs(os.path.join(udir, "cards", "Card 1"), exist_ok=True)
    os.makedirs(os.path.join(udir, "backup"), exist_ok=True)
    save_user_meta(name, {"last_card": "Card 1"})


def get_user_meta(name):
    p = os.path.join(USERS_DIR, name, "meta.json")
    if os.path.exists(p):
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_user_meta(name, meta):
    p = os.path.join(USERS_DIR, name, "meta.json")
    with open(p, 'w') as f:
        json.dump(meta, f, indent=2)


def get_cards(user):
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
    os.makedirs(os.path.join(USERS_DIR, user, "cards", name), exist_ok=True)


def delete_card(user, name):
    d = os.path.join(USERS_DIR, user, "cards", name)
    if os.path.isdir(d):
        shutil.rmtree(d)


def load_memcard(user, card):
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
    card_dir = os.path.join(USERS_DIR, user, "cards", card)
    os.makedirs(card_dir, exist_ok=True)
    for f in MEMCARD_FILES:
        src = os.path.join(MEMCARD_ACTIVE, f)
        dst = os.path.join(card_dir, f)
        if os.path.exists(src):
            shutil.copy2(src, dst)


# ═══ ROM list ═══════════════════════════════════════════════════
games = sorted(
    [f for f in os.listdir(ROM_DIR) if f.lower().endswith(EXTS)],
    key=str.lower
) if os.path.isdir(ROM_DIR) else []


def strip_ext(name):
    for e in EXTS:
        if name.lower().endswith(e):
            return name[:-len(e)]
    return name


# ═══ Archive helpers ════════════════════════════════════════════

def get_archive_size(path):
    lower = path.lower()
    try:
        if lower.endswith('.zip'):
            r = subprocess.run(["unzip", "-l", path], capture_output=True, text=True)
            for line in reversed(r.stdout.splitlines()):
                parts = line.split()
                if parts and parts[0].isdigit():
                    return int(parts[0])
        elif lower.endswith('.7z'):
            r = subprocess.run(["7z", "l", path], capture_output=True, text=True)
            for line in reversed(r.stdout.splitlines()):
                parts = line.split()
                if parts and parts[0].isdigit() and len(parts) >= 3:
                    return int(parts[0])
    except Exception:
        pass
    return 0


def get_dir_size(d):
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


# ═══ Pygame setup ═══════════════════════════════════════════════

def init_display():
    """Initialize or reinitialize pygame and return (screen, fonts, joy)."""
    pygame.init()
    pygame.joystick.init()

    scr = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    w, h = scr.get_size()

    fonts = {
        'sm': pygame.font.SysFont("DejaVu Sans", 11),
        'md': pygame.font.SysFont("DejaVu Sans", 16),
        'md_b': pygame.font.SysFont("DejaVu Sans", 16, bold=True),
        'lg': pygame.font.SysFont("DejaVu Sans", 20, bold=True),
        'xl': pygame.font.SysFont("DejaVu Sans", 22, bold=True),
    }

    joy = None
    deadline = time.time() + 5
    clk = pygame.time.Clock()
    while time.time() < deadline:
        pygame.joystick.quit()
        pygame.joystick.init()
        if pygame.joystick.get_count() > 0:
            joy = pygame.joystick.Joystick(0)
            joy.init()
            break
        for ev in pygame.event.get():
            pass
        scr.fill(BG)
        msg = fonts['lg'].render("Waiting for controller...", True, HDR)
        scr.blit(msg, msg.get_rect(center=(w // 2, h // 2)))
        pygame.display.flip()
        clk.tick(10)

    return scr, w, h, fonts, joy


screen, W, H, F, joy = init_display()
clock = pygame.time.Clock()

LINE_H = 24
VISIBLE = (H - 70) // LINE_H
DPAD_DELAY = 180


# ═══ Drawing helpers ════════════════════════════════════════════

def truncate(text, font, max_w):
    if font.size(text)[0] <= max_w:
        return text
    while len(text) > 0 and font.size(text + "...")[0] > max_w:
        text = text[:-1]
    return text + "..."


def draw_header(title, hint_text=None, count_text=None):
    t = F['lg'].render(title, True, HDR)
    screen.blit(t, (12, 6))
    if count_text:
        cs = F['sm'].render(count_text, True, HINT)
        screen.blit(cs, (W - cs.get_width() - 12, 12))
    if hint_text:
        h = F['sm'].render(hint_text, True, HINT)
        screen.blit(h, (12, 30))
    pygame.draw.line(screen, ACCENT, (10, 46), (W - 10, 46), 1)


def draw_list(items, sel_idx, scroll, colors=None):
    """Draw a scrollable list. items = list of (text, is_special) tuples."""
    top = 50
    vis = VISIBLE
    for i in range(scroll, min(scroll + vis, len(items))):
        y = top + (i - scroll) * LINE_H
        text, special = items[i]
        display = truncate(text, F['md'], W - 30)
        if i == sel_idx:
            bg = DANGER if (colors and colors.get(i) == 'danger') else SEL_BG
            pygame.draw.rect(screen, bg, (6, y, W - 12, LINE_H - 2), border_radius=4)
            color = TXT_SEL
            label = F['md_b'].render(display, True, color)
        else:
            color = HDR if special else TXT
            label = F['md'].render(display, True, color)
        screen.blit(label, (14, y + 3))


def draw_center_msg(top_text, mid_text="", bot_text=""):
    screen.fill(BG)
    if top_text:
        s = F['xl'].render(top_text, True, HDR)
        screen.blit(s, s.get_rect(center=(W // 2, H // 5)))
    if mid_text:
        s = F['md'].render(mid_text, True, TXT)
        screen.blit(s, s.get_rect(center=(W // 2, H // 2)))
    if bot_text:
        s = F['sm'].render(bot_text, True, HINT)
        screen.blit(s, s.get_rect(center=(W // 2, H - 25)))
    pygame.display.flip()


def draw_progress(game_name, progress, status="Extracting"):
    screen.fill(BG)
    s = F['xl'].render(status, True, HDR)
    screen.blit(s, s.get_rect(center=(W // 2, H // 6)))
    bw = int(W * 0.7)
    bh = 22
    bx = (W - bw) // 2
    by = H // 2 - bh // 2
    pygame.draw.rect(screen, BAR_BG, (bx, by, bw, bh), border_radius=6)
    pygame.draw.rect(screen, BAR_BORDER, (bx, by, bw, bh), 2, border_radius=6)
    if progress > 0:
        fw = max(4, int((bw - 4) * min(progress, 1.0)))
        pygame.draw.rect(screen, BAR_FG, (bx + 2, by + 2, fw, bh - 4), border_radius=4)
    pt = F['md_b'].render(f"{int(progress * 100)}%", True, TXT_SEL)
    screen.blit(pt, pt.get_rect(center=(W // 2, by + bh + 16)))
    dn = truncate(strip_ext(game_name), F['md'], W - 60)
    n = F['md'].render(dn, True, TXT)
    screen.blit(n, n.get_rect(center=(W // 2, int(H * 0.78))))
    ch = F['sm'].render("B: Cancel", True, HINT)
    screen.blit(ch, ch.get_rect(center=(W // 2, H - 20)))
    pygame.display.flip()


# ═══ On-screen keyboard ════════════════════════════════════════

KB_ROWS = [
    list("ABCDEFGHIJ"),
    list("KLMNOPQRST"),
    list("UVWXYZ0123"),
    list("456789_- ") + ["\u2190", "\u2713"],
]
KB_COLS = 10
KB_NROWS = len(KB_ROWS)


def on_screen_keyboard(prompt):
    """Blocking on-screen keyboard. Returns string or None if cancelled."""
    text = ""
    kx, ky = 0, 0
    last_joy = 0

    while True:
        now = pygame.time.get_ticks()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return None
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    return None
                if ev.key == pygame.K_RETURN:
                    if text.strip():
                        return text.strip()
                if ev.key == pygame.K_BACKSPACE:
                    text = text[:-1]
                elif ev.unicode and ev.unicode.isprintable():
                    if len(text) < 20:
                        text += ev.unicode
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == 1:
                    return None
                if ev.button in (7, 9):
                    if text.strip():
                        return text.strip()
                if ev.button == 0:
                    ch = KB_ROWS[ky][kx]
                    if ch == "\u2713":
                        if text.strip():
                            return text.strip()
                    elif ch == "\u2190":
                        text = text[:-1]
                    elif ch == " ":
                        if len(text) < 20:
                            text += " "
                    else:
                        if len(text) < 20:
                            text += ch
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hy == 1:
                    ky = (ky - 1) % KB_NROWS
                if hy == -1:
                    ky = (ky + 1) % KB_NROWS
                if hx == -1:
                    kx = (kx - 1) % KB_COLS
                if hx == 1:
                    kx = (kx + 1) % KB_COLS

        # Analog stick
        if joy is not None and now - last_joy > DPAD_DELAY:
            ya = joy.get_axis(1)
            xa = joy.get_axis(0)
            moved = False
            if ya < -0.5:
                ky = (ky - 1) % KB_NROWS; moved = True
            elif ya > 0.5:
                ky = (ky + 1) % KB_NROWS; moved = True
            if xa < -0.5:
                kx = (kx - 1) % KB_COLS; moved = True
            elif xa > 0.5:
                kx = (kx + 1) % KB_COLS; moved = True
            if moved:
                last_joy = now

        # Draw
        screen.fill(BG)
        # Prompt
        ps = F['lg'].render(prompt, True, HDR)
        screen.blit(ps, ps.get_rect(center=(W // 2, 30)))
        # Text field
        field_w = int(W * 0.7)
        field_x = (W - field_w) // 2
        pygame.draw.rect(screen, BAR_BG, (field_x, 55, field_w, 30), border_radius=6)
        pygame.draw.rect(screen, BAR_BORDER, (field_x, 55, field_w, 30), 2, border_radius=6)
        cursor = text + "|" if (now // 500) % 2 == 0 else text
        ts = F['md_b'].render(cursor, True, TXT_SEL)
        screen.blit(ts, (field_x + 8, 61))

        # Keyboard grid
        cell_w = 52
        cell_h = 32
        grid_w = KB_COLS * cell_w
        gx_start = (W - grid_w) // 2
        gy_start = 105

        for ry in range(KB_NROWS):
            for rx in range(KB_COLS):
                x = gx_start + rx * cell_w
                y = gy_start + ry * cell_h
                ch = KB_ROWS[ry][rx]
                selected = (rx == kx and ry == ky)
                bg = KEY_SEL if selected else KEY_BG
                pygame.draw.rect(screen, bg, (x + 2, y + 2, cell_w - 4, cell_h - 4), border_radius=4)
                if selected:
                    pygame.draw.rect(screen, TXT_SEL, (x + 2, y + 2, cell_w - 4, cell_h - 4), 2, border_radius=4)
                # Display label
                if ch == "\u2190":
                    label = "DEL"
                elif ch == "\u2713":
                    label = "OK"
                elif ch == " ":
                    label = "SPC"
                else:
                    label = ch
                cs = F['md_b' if selected else 'md'].render(label, True, TXT_SEL if selected else TXT)
                screen.blit(cs, cs.get_rect(center=(x + cell_w // 2, y + cell_h // 2)))

        # Hints
        hs = F['sm'].render("A: Type  |  Start: Confirm  |  B: Cancel  |  D-pad: Navigate", True, HINT)
        screen.blit(hs, hs.get_rect(center=(W // 2, H - 20)))

        pygame.display.flip()
        clock.tick(30)


# ═══ Input helpers ══════════════════════════════════════════════

def check_cancel():
    pygame.event.pump()
    for ev in pygame.event.get():
        if ev.type == pygame.JOYBUTTONDOWN and ev.button == 1:
            return True
        if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
            return True
    return False


def confirm_dialog(msg):
    """Show Yes/No dialog. Returns True if confirmed."""
    sel = 1  # default No
    last_joy = 0
    while True:
        now = pygame.time.get_ticks()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return False
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    return False
                if ev.key in (pygame.K_LEFT, pygame.K_RIGHT):
                    sel = 1 - sel
                if ev.key == pygame.K_RETURN:
                    return sel == 0
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == 1:
                    return False
                if ev.button == 0:
                    return sel == 0
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hx != 0:
                    sel = 1 - sel
        if joy is not None and now - last_joy > DPAD_DELAY:
            xa = joy.get_axis(0)
            if abs(xa) > 0.5:
                sel = 1 - sel
                last_joy = now

        screen.fill(BG)
        ms = F['lg'].render(msg, True, HDR)
        screen.blit(ms, ms.get_rect(center=(W // 2, H // 3)))
        btn_w, btn_h = 100, 36
        gap = 40
        y = H // 2 + 10
        for i, label in enumerate(["Yes", "No"]):
            x = W // 2 - btn_w - gap // 2 + i * (btn_w + gap)
            bg = DANGER if (i == 0 and sel == 0) else (SEL_BG if sel == i else KEY_BG)
            pygame.draw.rect(screen, bg, (x, y, btn_w, btn_h), border_radius=6)
            if sel == i:
                pygame.draw.rect(screen, TXT_SEL, (x, y, btn_w, btn_h), 2, border_radius=6)
            ls = F['md_b'].render(label, True, TXT_SEL if sel == i else TXT)
            screen.blit(ls, ls.get_rect(center=(x + btn_w // 2, y + btn_h // 2)))
        hs = F['sm'].render("A: Select  |  B: Cancel", True, HINT)
        screen.blit(hs, hs.get_rect(center=(W // 2, H - 25)))
        pygame.display.flip()
        clock.tick(30)


# ═══ Game launch ════════════════════════════════════════════════

def extract_and_launch(game_file, user, card):
    """Extract if needed, launch RetroArch, save memcard on exit.
       Returns True to exit app, False to return to game menu."""
    name = strip_ext(game_file)
    path = os.path.join(ROM_DIR, game_file)
    lower = game_file.lower()

    if lower.endswith(('.iso', '.chd')):
        draw_progress(game_file, 1.0, "Launching")
        pygame.time.wait(400)
        pygame.quit()
        subprocess.run(["retroarch", "-L", CORE, "--fullscreen", path])
        save_memcard(user, card)
        return True  # signal reinit needed

    extract_dir = os.path.join(CACHE_DIR, name)
    os.makedirs(extract_dir, exist_ok=True)

    draw_progress(game_file, 0, "Reading archive...")
    total_bytes = get_archive_size(path)

    proc = None
    try:
        if lower.endswith('.zip'):
            proc = subprocess.Popen(
                ["unzip", "-o", path, "-d", extract_dir],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        elif lower.endswith('.7z'):
            proc = subprocess.Popen(
                ["7z", "x", f"-o{extract_dir}", "-y", path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
    except Exception as e:
        print(f"Extract error: {e}")
        shutil.rmtree(extract_dir, ignore_errors=True)
        return False

    if proc is None:
        shutil.rmtree(extract_dir, ignore_errors=True)
        return False

    while proc.poll() is None:
        if check_cancel():
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            shutil.rmtree(extract_dir, ignore_errors=True)
            return False

        progress = 0.0
        if total_bytes > 0:
            progress = min(get_dir_size(extract_dir) / total_bytes, 0.99)
        draw_progress(game_file, progress, "Extracting")
        clock.tick(5)

    if proc.returncode != 0:
        draw_progress(game_file, 0, "Error")
        pygame.time.wait(2000)
        shutil.rmtree(extract_dir, ignore_errors=True)
        return False

    draw_progress(game_file, 1.0, "Complete")
    pygame.time.wait(300)

    game_path = None
    for ext in GAME_EXTS:
        matches = glob.glob(os.path.join(extract_dir, "**", f"*.{ext}"), recursive=True)
        if matches:
            game_path = matches[0]
            break

    if not game_path:
        draw_progress(game_file, 0, "No ISO found")
        pygame.time.wait(2000)
        shutil.rmtree(extract_dir, ignore_errors=True)
        return False

    draw_progress(game_file, 1.0, "Launching")
    pygame.time.wait(400)
    pygame.quit()
    subprocess.run(["retroarch", "-L", CORE, "--fullscreen", game_path])
    save_memcard(user, card)
    shutil.rmtree(extract_dir, ignore_errors=True)
    return True  # signal reinit needed


# ═══ Screen: User Picker ════════════════════════════════════════

def screen_user_picker():
    """Returns selected username or None to exit."""
    sel = 0
    scroll = 0
    last_joy = 0

    while True:
        users = get_users()
        items = [(u, False) for u in users] + [("+ New User", True)]
        sel = min(sel, len(items) - 1)
        now = pygame.time.get_ticks()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return None
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    return None
                if ev.key == pygame.K_UP:
                    sel = max(0, sel - 1)
                if ev.key == pygame.K_DOWN:
                    sel = min(len(items) - 1, sel + 1)
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    if sel == len(items) - 1:
                        name = on_screen_keyboard("Enter Username")
                        if name:
                            create_user(name)
                    else:
                        return users[sel]
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == 0:
                    if sel == len(items) - 1:
                        name = on_screen_keyboard("Enter Username")
                        if name:
                            create_user(name)
                    else:
                        return users[sel]
                if ev.button == 1:
                    return None
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hy == 1:
                    sel = max(0, sel - 1)
                if hy == -1:
                    sel = min(len(items) - 1, sel + 1)

        if joy is not None and now - last_joy > DPAD_DELAY:
            ya = joy.get_axis(1)
            moved = False
            if ya < -0.5:
                sel = max(0, sel - 1); moved = True
            elif ya > 0.5:
                sel = min(len(items) - 1, sel + 1); moved = True
            if moved:
                last_joy = now

        if sel < scroll:
            scroll = sel
        if sel >= scroll + VISIBLE:
            scroll = sel - VISIBLE + 1

        screen.fill(BG)
        draw_header("Select Profile",
                     "A: Select  |  B: Exit",
                     f"{len(users)} profile{'s' if len(users) != 1 else ''}")
        draw_list(items, sel, scroll)
        pygame.display.flip()
        clock.tick(30)


# ═══ Screen: Memory Card Manager ════════════════════════════════

def screen_memcard_manager(user):
    """Returns selected card name, or None to go back."""
    sel = 0
    scroll = 0
    last_joy = 0

    while True:
        cards = get_cards(user)
        meta = get_user_meta(user)
        last_card = meta.get("last_card", "")
        items = []
        for c in cards:
            tag = "  (last used)" if c == last_card else ""
            items.append((c + tag, False))
        items.append(("+ New Card", True))
        sel = min(sel, len(items) - 1)
        now = pygame.time.get_ticks()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return None
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    return None
                if ev.key == pygame.K_UP:
                    sel = max(0, sel - 1)
                if ev.key == pygame.K_DOWN:
                    sel = min(len(items) - 1, sel + 1)
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    if sel == len(items) - 1:
                        name = on_screen_keyboard("Card Name")
                        if name:
                            create_card(user, name)
                    elif sel < len(cards):
                        return cards[sel]
                if ev.key == pygame.K_DELETE or ev.key == pygame.K_x:
                    if sel < len(cards) and len(cards) > 1:
                        if confirm_dialog(f"Delete {cards[sel]}?"):
                            delete_card(user, cards[sel])
                            sel = min(sel, len(get_cards(user)))
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == 0:
                    if sel == len(items) - 1:
                        name = on_screen_keyboard("Card Name")
                        if name:
                            create_card(user, name)
                    elif sel < len(cards):
                        return cards[sel]
                if ev.button == 1:
                    return None
                if ev.button == 2 and sel < len(cards) and len(cards) > 1:
                    if confirm_dialog(f"Delete {cards[sel]}?"):
                        delete_card(user, cards[sel])
                        sel = min(sel, len(get_cards(user)))
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hy == 1:
                    sel = max(0, sel - 1)
                if hy == -1:
                    sel = min(len(items) - 1, sel + 1)

        if joy is not None and now - last_joy > DPAD_DELAY:
            ya = joy.get_axis(1)
            moved = False
            if ya < -0.5:
                sel = max(0, sel - 1); moved = True
            elif ya > 0.5:
                sel = min(len(items) - 1, sel + 1); moved = True
            if moved:
                last_joy = now

        if sel < scroll:
            scroll = sel
        if sel >= scroll + VISIBLE:
            scroll = sel - VISIBLE + 1

        screen.fill(BG)
        draw_header(f"{user}'s Cards",
                     "A: Select  |  X: Delete  |  B: Back",
                     f"{len(cards)} card{'s' if len(cards) != 1 else ''}")
        draw_list(items, sel, scroll)
        pygame.display.flip()
        clock.tick(30)


# ═══ Screen: Game Picker ════════════════════════════════════════

def screen_game_picker(user, card):
    """Game selection screen. Returns True if reinit needed, False to go back."""
    sel = 0
    scroll = 0
    last_joy = 0

    while True:
        now = pygame.time.get_ticks()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return False
            if ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    return False
                if ev.key == pygame.K_UP:
                    sel = max(0, sel - 1)
                if ev.key == pygame.K_DOWN:
                    sel = min(len(games) - 1, sel + 1)
                if ev.key == pygame.K_LEFT or ev.key == pygame.K_PAGEUP:
                    sel = max(0, sel - VISIBLE)
                if ev.key == pygame.K_RIGHT or ev.key == pygame.K_PAGEDOWN:
                    sel = min(len(games) - 1, sel + VISIBLE)
                if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                    result = extract_and_launch(games[sel], user, card)
                    if result:
                        return True
            if ev.type == pygame.JOYBUTTONDOWN:
                if ev.button == 0:
                    result = extract_and_launch(games[sel], user, card)
                    if result:
                        return True
                if ev.button == 1:
                    return False
            if ev.type == pygame.JOYHATMOTION:
                hx, hy = ev.value
                if hy == 1: sel = max(0, sel - 1)
                if hy == -1: sel = min(len(games) - 1, sel + 1)
                if hx == -1: sel = max(0, sel - VISIBLE)
                if hx == 1: sel = min(len(games) - 1, sel + VISIBLE)

        if joy is not None and now - last_joy > DPAD_DELAY:
            ya = joy.get_axis(1)
            xa = joy.get_axis(0)
            moved = False
            if ya < -0.5: sel = max(0, sel - 1); moved = True
            elif ya > 0.5: sel = min(len(games) - 1, sel + 1); moved = True
            if xa < -0.5: sel = max(0, sel - VISIBLE); moved = True
            elif xa > 0.5: sel = min(len(games) - 1, sel + VISIBLE); moved = True
            if moved:
                last_joy = now

        if sel < scroll: scroll = sel
        if sel >= scroll + VISIBLE: scroll = sel - VISIBLE + 1

        items = [(strip_ext(g), False) for g in games]

        screen.fill(BG)
        draw_header("PS2 Games",
                     "Up/Down: Scroll  |  Left/Right: Page  |  A: Launch  |  B: Back",
                     f"{sel + 1} / {len(games)}")
        draw_list(items, sel, scroll)
        pygame.display.flip()
        clock.tick(30)


# ═══ Main loop ══════════════════════════════════════════════════

def main():
    global screen, W, H, F, joy

    while True:
        # Step 1: Pick user
        user = screen_user_picker()
        if user is None:
            break

        while True:
            # Step 2: Pick memory card
            card = screen_memcard_manager(user)
            if card is None:
                break  # back to user picker

            # Step 3: Load memory card
            draw_center_msg("Loading Memory Card...", card, user)
            load_memcard(user, card)
            time.sleep(0.5)

            # Step 4: Game picker (loops until back or launch)
            needs_reinit = screen_game_picker(user, card)

            if needs_reinit:
                # RetroArch ran, pygame was quit, reinitialize
                screen, W, H, F, joy = init_display()
                # After reinit, go back to memcard screen for this user

    pygame.quit()


if __name__ == "__main__":
    if not games:
        print(f"No ROMs found in {ROM_DIR}")
        sys.exit(1)
    main()

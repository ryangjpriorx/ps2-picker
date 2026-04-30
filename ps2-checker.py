#!/usr/bin/env python3
"""PS2 Picker Pre-Launcher — Dependency checker and installer helper.
   Themed GUI (matching main app) with terminal fallback.
   Zero external dependencies (stdlib only — pygame optional for GUI).

   Usage:
     python3 ps2-checker.py              Check deps, launch if all OK
     python3 ps2-checker.py --check-only  Check deps, don't launch
     python3 ps2-checker.py --terminal    Force terminal mode (no GUI)
"""

import os, sys, platform, shutil, subprocess, importlib.util, json, string, time, math

# ═══ Platform Detection ═════════════════════════════════════════

IS_WINDOWS = platform.system() == 'Windows'
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_APP = os.path.join(SCRIPT_DIR, 'ps2-picker.py')
APP_DIR = os.path.join(os.path.expanduser('~'), '.ps2-picker')
CHECKER_CONFIG = os.path.join(APP_DIR, 'checker.json')
HAS_PYGAME = importlib.util.find_spec('pygame') is not None

# ═══ Theme (matches main app) ══════════════════════════════════

BG       = (18, 8, 32)
SEL_BG   = (88, 28, 135)
TXT      = (220, 210, 190)
TXT_DIM  = (140, 130, 120)
HDR      = (255, 200, 50)
HINT     = (210, 170, 100)
BAR_BG   = (40, 20, 65)
ACCENT   = (147, 51, 234)
DANGER   = (200, 60, 60)
SUCCESS  = (50, 180, 80)
KEY_BG   = (40, 18, 65)

REF_W, REF_H = 640, 480


# ═══ Checker Config Persistence ═════════════════════════════════

def load_checker_config():
    """Load saved checker config (custom paths, etc)."""
    if os.path.exists(CHECKER_CONFIG):
        try:
            with open(CHECKER_CONFIG) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_checker_config(cfg):
    """Save checker config to disk."""
    os.makedirs(os.path.dirname(CHECKER_CONFIG), exist_ok=True)
    with open(CHECKER_CONFIG, 'w') as f:
        json.dump(cfg, f, indent=2)


# ═══ Distro Detection ══════════════════════════════════════════

def detect_distro():
    """Detect Linux distro family from /etc/os-release."""
    if IS_WINDOWS:
        return 'windows'
    info = {}
    for path in ['/etc/os-release', '/usr/lib/os-release']:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if '=' in line:
                            k, v = line.split('=', 1)
                            info[k] = v.strip('"')
            except Exception:
                pass
            break
    dist_id = info.get('ID', '').lower()
    id_like = info.get('ID_LIKE', '').lower()

    if dist_id in ('debian', 'ubuntu', 'linuxmint', 'pop', 'zorin', 'elementary',
                    'kali', 'raspbian', 'neon', 'mx'):
        return 'debian'
    if 'debian' in id_like or 'ubuntu' in id_like:
        return 'debian'
    if dist_id in ('fedora', 'rhel', 'centos', 'rocky', 'alma', 'nobara'):
        return 'fedora'
    if 'fedora' in id_like or 'rhel' in id_like:
        return 'fedora'
    if dist_id in ('arch', 'manjaro', 'endeavouros', 'garuda', 'cachyos'):
        return 'arch'
    if 'arch' in id_like:
        return 'arch'
    if dist_id == 'steamos':
        return 'steamos'
    if dist_id == 'batocera':
        return 'batocera'
    if dist_id in ('opensuse', 'suse', 'opensuse-leap', 'opensuse-tumbleweed'):
        return 'suse'
    if 'suse' in id_like:
        return 'suse'
    if dist_id in ('gentoo', 'funtoo'):
        return 'gentoo'
    if dist_id == 'void':
        return 'void'
    if dist_id == 'alpine':
        return 'alpine'
    if dist_id == 'nixos':
        return 'nixos'
    return 'unknown'


def get_pretty_name():
    """Get human-readable OS name."""
    if IS_WINDOWS:
        return f"Windows {platform.version()}"
    for path in ['/etc/os-release', '/usr/lib/os-release']:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    for line in f:
                        if line.startswith('PRETTY_NAME='):
                            return line.split('=', 1)[1].strip().strip('"')
            except Exception:
                pass
    return 'Linux'


# ═══ Dependency Checks ══════════════════════════════════════════

def check_python_module(module_name):
    """Check if a Python module is importable without importing it."""
    return importlib.util.find_spec(module_name) is not None


def _get_windows_drives():
    """Get list of available drive letters on Windows."""
    drives = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if os.path.exists(drive):
            drives.append(drive)
    return drives


def _scan_for_exe(exe_name, scan_folders):
    """Scan common locations for an executable on Windows. Returns path or None."""
    if not IS_WINDOWS:
        return None
    pf = os.environ.get('PROGRAMFILES', '')
    pf86 = os.environ.get('PROGRAMFILES(X86)', '')
    home = os.path.expanduser('~')
    appdata = os.environ.get('APPDATA', '')
    localappdata = os.environ.get('LOCALAPPDATA', '')

    # Build candidate list from scan_folders template
    candidates = []
    bases = [pf, pf86, appdata, localappdata, home,
             os.path.join(home, 'Downloads'), os.path.join(home, 'Desktop')]
    for base in bases:
        if not base:
            continue
        for folder in scan_folders:
            candidates.append(os.path.join(base, folder, exe_name))

    for p in candidates:
        if os.path.isfile(p):
            return p

    # Scan drive roots
    for drive in _get_windows_drives():
        for folder in scan_folders:
            p = os.path.join(drive, folder, exe_name)
            if os.path.isfile(p):
                return p
    return None


def check_command(cmd):
    """Check if a command is available on PATH, saved config, or common locations."""
    # Check saved custom path first
    cfg = load_checker_config()
    custom_paths = cfg.get('custom_paths', {})
    if cmd in custom_paths:
        saved = custom_paths[cmd]
        if os.path.isfile(saved):
            return True
        # Stale path — remove it
        del custom_paths[cmd]
        cfg['custom_paths'] = custom_paths
        save_checker_config(cfg)

    if shutil.which(cmd):
        return True

    if IS_WINDOWS:
        found = None
        if cmd == '7z':
            found = _scan_for_exe('7z.exe', ['7-Zip'])
        elif cmd == 'retroarch':
            found = _scan_for_exe('retroarch.exe', [
                'RetroArch', 'RetroArch-Win64',
                'Emulators\\RetroArch', 'Games\\RetroArch',
                'Emulation\\RetroArch',
            ])
        if found:
            custom_paths[cmd] = found
            cfg['custom_paths'] = custom_paths
            save_checker_config(cfg)
            return True
    return False


def get_saved_path(cmd):
    """Get the saved/discovered path for a command, if any."""
    cfg = load_checker_config()
    return cfg.get('custom_paths', {}).get(cmd)


# ═══ Install Commands Database ══════════════════════════════════

INSTALL_COMMANDS = {
    'windows': {
        'pygame':    'pip install pygame',
        '7z':        'winget install 7zip.7zip',
        'retroarch': 'winget install Libretro.RetroArch',
    },
    'debian': {
        'pygame':    'sudo apt install python3-pygame',
        '7z':        'sudo apt install p7zip-full',
        'retroarch': 'sudo apt install retroarch',
    },
    'fedora': {
        'pygame':    'sudo dnf install python3-pygame',
        '7z':        'sudo dnf install p7zip p7zip-plugins',
        'retroarch': 'sudo dnf install retroarch  (or Flatpak)',
    },
    'arch': {
        'pygame':    'sudo pacman -S python-pygame',
        '7z':        'sudo pacman -S p7zip',
        'retroarch': 'sudo pacman -S retroarch',
    },
    'steamos': {
        'pygame':    'pip install --user pygame  (after: steamos-readonly disable)',
        '7z':        'sudo pacman -S p7zip  (after: steamos-readonly disable)',
        'retroarch': 'flatpak install org.libretro.RetroArch',
    },
    'batocera': {
        'pygame':    'Pre-installed on Batocera',
        '7z':        'Pre-installed on Batocera',
        'retroarch': 'Pre-installed on Batocera',
    },
    'suse': {
        'pygame':    'sudo zypper install python3-pygame',
        '7z':        'sudo zypper install p7zip-full',
        'retroarch': 'sudo zypper install retroarch  (or Flatpak)',
    },
    'gentoo': {
        'pygame':    'sudo emerge dev-python/pygame',
        '7z':        'sudo emerge app-arch/p7zip',
        'retroarch': 'sudo emerge games-emulation/retroarch',
    },
    'void': {
        'pygame':    'sudo xbps-install python3-pygame',
        '7z':        'sudo xbps-install p7zip',
        'retroarch': 'sudo xbps-install retroarch',
    },
    'alpine': {
        'pygame':    'sudo apk add py3-pygame',
        '7z':        'sudo apk add p7zip',
        'retroarch': 'sudo apk add retroarch',
    },
    'nixos': {
        'pygame':    'nix-env -iA nixpkgs.python3Packages.pygame',
        '7z':        'nix-env -iA nixpkgs.p7zip',
        'retroarch': 'nix-env -iA nixpkgs.retroarch',
    },
}

FALLBACK_COMMANDS = {
    'pygame':    'pip install pygame  (or your package manager)',
    '7z':        'Install p7zip via your package manager',
    'retroarch': 'Install from https://retroarch.com',
}


# ═══ Core Check Logic ══════════════════════════════════════════

def run_checks():
    """Run all dependency checks and return results."""
    distro = detect_distro()
    pretty = get_pretty_name()
    cmds = INSTALL_COMMANDS.get(distro, FALLBACK_COMMANDS)

    deps = [
        {
            'name': 'pygame',
            'description': 'Python game library (display, input, audio)',
            'installed': check_python_module('pygame'),
            'install_cmd': cmds.get('pygame', FALLBACK_COMMANDS['pygame']),
            'required': True,
            'found_path': None,
        },
        {
            'name': '7z',
            'description': '7-Zip archive extraction (for .7z archives)',
            'installed': check_command('7z'),
            'install_cmd': cmds.get('7z', FALLBACK_COMMANDS['7z']),
            'required': False,
            'found_path': get_saved_path('7z') or shutil.which('7z'),
        },
        {
            'name': 'retroarch',
            'description': 'RetroArch emulator (launches PS2 games)',
            'installed': check_command('retroarch'),
            'install_cmd': cmds.get('retroarch', FALLBACK_COMMANDS['retroarch']),
            'required': True,
            'found_path': get_saved_path('retroarch') or shutil.which('retroarch'),
        },
    ]

    return deps, distro, pretty


# ═══ Terminal Mode ══════════════════════════════════════════════

def supports_color():
    """Check if terminal supports ANSI colors."""
    if IS_WINDOWS:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            return True
        except Exception:
            return os.environ.get('WT_SESSION') is not None
    return hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()


USE_COLOR = supports_color()

def _c(text, code):
    if USE_COLOR:
        return f"\033[{code}m{text}\033[0m"
    return text

def _green(t):  return _c(t, '32')
def _red(t):    return _c(t, '31')
def _yellow(t): return _c(t, '33')
def _cyan(t):   return _c(t, '36')
def _bold(t):   return _c(t, '1')
def _dim(t):    return _c(t, '90')


def terminal_prompt_path(dep_name):
    """Ask the user for a custom path in terminal mode."""
    print()
    print(f"  {_yellow('?')}  {_bold(dep_name)} was not found automatically.")
    print(f"     Enter the full path to the executable (or Enter to skip):")
    print()
    try:
        path = input(f"     Path: ").strip().strip('"').strip("'")
    except (EOFError, KeyboardInterrupt):
        return None
    if not path:
        return None
    if os.path.isdir(path):
        exe = os.path.join(path, f"{dep_name}.exe" if IS_WINDOWS else dep_name)
        if os.path.isfile(exe):
            path = exe
        else:
            print(f"     {_red('x')}  Could not find {dep_name} in that folder.")
            return None
    if os.path.isfile(path):
        cfg = load_checker_config()
        custom = cfg.get('custom_paths', {})
        custom[dep_name] = path
        cfg['custom_paths'] = custom
        save_checker_config(cfg)
        print(f"     {_green('OK')}  Path saved for future runs.")
        return path
    else:
        print(f"     {_red('x')}  File not found: {path}")
        return None


def run_terminal_mode(check_only=False):
    """Full terminal-mode dependency checker."""
    deps, distro, pretty = run_checks()

    print()
    print(_bold("=" * 60))
    print(_bold("  PS2 Picker - Dependency Checker"))
    print(_bold("=" * 60))
    print()
    print(f"  {_cyan('OS Detected:')}  {pretty}")
    print(f"  {_cyan('Distro Family:')}  {distro}")
    print(f"  {_cyan('Python:')}  {platform.python_version()}")
    print()
    print(_bold("-" * 60))

    for dep in deps:
        status = _green("OK INSTALLED") if dep['installed'] else _red("X  MISSING")
        print(f"  {status}  {_bold(dep['name'])}")
        print(f"           {_dim(dep['description'])}")
        if dep['found_path']:
            print(f"           {_dim('Found: ' + dep['found_path'])}")
        if not dep['installed']:
            print(f"           {_yellow('Install:')}  {dep['install_cmd']}")
        print()

    missing = [d for d in deps if not d['installed']]

    # Prompt for custom paths
    promptable = ('retroarch', '7z')
    for d in missing[:]:
        if d['name'] in promptable:
            result = terminal_prompt_path(d['name'])
            if result:
                d['installed'] = True
                d['found_path'] = result
                missing = [x for x in deps if not x['installed']]

    print(_bold("-" * 60))
    if not missing:
        print(f"\n  {_green('OK')}  {_bold(f'All {len(deps)} dependencies satisfied!')}\n")
    else:
        req_missing = [d for d in missing if d['required']]
        print(f"\n  {_red('X')}  {_bold(f'{len(missing)} of {len(deps)} dependencies missing')}\n")
        if req_missing:
            print(f"  {_red('Cannot launch:')} required dependencies are missing.\n")

    print(_bold("=" * 60))

    if not os.path.exists(MAIN_APP):
        print(f"\n  {_red('X')}  Main app not found: {MAIN_APP}")
        print(f"     Place ps2-picker.py in the same folder as this script.\n")
        _wait_exit()
        sys.exit(1)

    req_missing = [d for d in deps if d['required'] and not d['installed']]
    if req_missing:
        _wait_exit()
        sys.exit(1)

    if check_only:
        print(f"\n  {_dim('--check-only mode: not launching.')}\n")
        _wait_exit()
        sys.exit(0)

    print(f"\n  {_cyan('->')}  Launching PS2 Picker...\n")
    _launch()


def _wait_exit():
    try:
        input("  Press Enter to exit...")
    except (EOFError, KeyboardInterrupt):
        pass


def _launch():
    python = sys.executable
    try:
        os.execv(python, [python, MAIN_APP])
    except Exception:
        try:
            subprocess.run([python, MAIN_APP])
        except Exception as e:
            print(f"  {_red('Launch failed:')} {e}")
            _wait_exit()
            sys.exit(1)


# ═══ GUI Mode (pygame) ═════════════════════════════════════════

def run_gui_mode(check_only=False):
    """Themed pygame GUI dependency checker."""
    import pygame

    os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
    if not IS_WINDOWS:
        os.environ.setdefault('DISPLAY', ':0')

    pygame.init()
    pygame.joystick.init()

    # Detect native resolution — fullscreen on 720p or below (handhelds)
    disp_info = pygame.display.Info()
    native_h = disp_info.current_h
    is_handheld = native_h <= 720

    if is_handheld:
        scr = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    else:
        scr = pygame.display.set_mode((REF_W, REF_H), pygame.RESIZABLE)
    pygame.display.set_caption("PS2 Picker - Dependency Check")
    w, h = scr.get_size()
    scale = h / REF_H

    def sc(val):
        return max(1, int(val * scale))

    # Pick a cross-platform font
    font_name = None
    for candidate in ["DejaVu Sans", "Segoe UI", "Arial", "Helvetica", None]:
        if candidate is None:
            break
        if candidate.lower().replace(' ', '') in [f.lower().replace(' ', '') for f in pygame.font.get_fonts()]:
            font_name = candidate
            break

    fonts = {
        'sm': pygame.font.SysFont(font_name, sc(11)),
        'md': pygame.font.SysFont(font_name, sc(14)),
        'md_b': pygame.font.SysFont(font_name, sc(14), bold=True),
        'lg': pygame.font.SysFont(font_name, sc(18), bold=True),
        'xl': pygame.font.SysFont(font_name, sc(22), bold=True),
    }

    clock = pygame.time.Clock()

    # Initialize joystick
    joy = None
    if pygame.joystick.get_count() > 0:
        joy = pygame.joystick.Joystick(0)
        joy.init()

    # Run checks
    deps, distro, pretty = run_checks()
    missing = [d for d in deps if not d['installed']]
    req_missing = [d for d in missing if d['required']]
    all_ok = len(missing) == 0

    # State for custom path input
    path_input_mode = False
    path_input_target = None
    path_input_text = ""
    path_input_error = ""
    path_prompt_idx = 0  # which missing dep to prompt for

    # Find promptable missing deps
    promptable_missing = [d for d in missing if d['name'] in ('retroarch', '7z')]

    # Scroll state
    scroll = 0

    # Animation
    start_time = time.time()

    # Check main app exists
    main_app_exists = os.path.exists(MAIN_APP)

    def refresh_status():
        nonlocal missing, req_missing, all_ok, promptable_missing
        missing = [d for d in deps if not d['installed']]
        req_missing = [d for d in missing if d['required']]
        all_ok = len(missing) == 0
        promptable_missing = [d for d in missing if d['name'] in ('retroarch', '7z')]

    def try_save_path(dep_name, path_str):
        """Validate and save a user-provided path. Returns (success, error_msg)."""
        path_str = path_str.strip().strip('"').strip("'")
        if not path_str:
            return False, "No path entered"
        if os.path.isdir(path_str):
            exe = os.path.join(path_str, f"{dep_name}.exe" if IS_WINDOWS else dep_name)
            if os.path.isfile(exe):
                path_str = exe
            else:
                return False, f"{dep_name} not found in that folder"
        if os.path.isfile(path_str):
            cfg = load_checker_config()
            custom = cfg.get('custom_paths', {})
            custom[dep_name] = path_str
            cfg['custom_paths'] = custom
            save_checker_config(cfg)
            # Update dep status
            for d in deps:
                if d['name'] == dep_name:
                    d['installed'] = True
                    d['found_path'] = path_str
            refresh_status()
            return True, ""
        return False, f"File not found: {path_str}"

    running = True
    while running:
        now = time.time()
        elapsed = now - start_time

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()

            if ev.type == pygame.VIDEORESIZE:
                w, h = ev.w, ev.h
                scale = h / REF_H
                fonts = {
                    'sm': pygame.font.SysFont(font_name, sc(11)),
                    'md': pygame.font.SysFont(font_name, sc(14)),
                    'md_b': pygame.font.SysFont(font_name, sc(14), bold=True),
                    'lg': pygame.font.SysFont(font_name, sc(18), bold=True),
                    'xl': pygame.font.SysFont(font_name, sc(22), bold=True),
                }

            if path_input_mode:
                # Text input for custom path
                if ev.type == pygame.KEYDOWN:
                    if ev.key == pygame.K_RETURN:
                        ok, err = try_save_path(path_input_target, path_input_text)
                        if ok:
                            path_input_mode = False
                            path_input_text = ""
                            path_input_error = ""
                        else:
                            path_input_error = err
                    elif ev.key == pygame.K_ESCAPE:
                        path_input_mode = False
                        path_input_text = ""
                        path_input_error = ""
                    elif ev.key == pygame.K_BACKSPACE:
                        path_input_text = path_input_text[:-1]
                        path_input_error = ""
                    elif ev.key == pygame.K_v and (ev.mod & pygame.KMOD_CTRL):
                        # Paste from clipboard
                        try:
                            clip = pygame.scrap.get(pygame.SCRAP_TEXT)
                            if clip:
                                path_input_text += clip.decode('utf-8').rstrip('\x00')
                        except Exception:
                            pass
                    else:
                        if ev.unicode and ev.unicode.isprintable():
                            path_input_text += ev.unicode
            else:
                # Normal mode
                if ev.type == pygame.KEYDOWN:
                    if ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                        if all_ok and main_app_exists:
                            if not check_only:
                                pygame.quit()
                                _launch()
                                return
                            else:
                                pygame.quit(); sys.exit(0)
                        elif not all_ok and promptable_missing:
                            # Enter path input for first promptable
                            d = promptable_missing[0]
                            path_input_mode = True
                            path_input_target = d['name']
                            path_input_text = ""
                            path_input_error = ""
                    elif ev.key == pygame.K_ESCAPE:
                        pygame.quit(); sys.exit(0)
                    elif ev.key == pygame.K_UP:
                        scroll = max(0, scroll - 1)
                    elif ev.key == pygame.K_DOWN:
                        scroll += 1

                if ev.type == pygame.JOYBUTTONDOWN:
                    if ev.button == 0:  # A
                        if all_ok and main_app_exists and not check_only:
                            pygame.quit()
                            _launch()
                            return
                        elif not all_ok and promptable_missing:
                            d = promptable_missing[0]
                            path_input_mode = True
                            path_input_target = d['name']
                            path_input_text = ""
                            path_input_error = ""
                    elif ev.button == 1:  # B
                        pygame.quit(); sys.exit(0)

        # ─── Draw ───────────────────────────────────────────

        scr.fill(BG)

        # Title
        title_surf = fonts['xl'].render("PS2 Picker - Dependency Check", True, HDR)
        scr.blit(title_surf, title_surf.get_rect(center=(w // 2, sc(22))))

        # OS info
        os_text = f"Detected: {pretty}  ({distro})  |  Python {platform.python_version()}"
        os_surf = fonts['sm'].render(os_text, True, ACCENT)
        scr.blit(os_surf, os_surf.get_rect(center=(w // 2, sc(44))))

        pygame.draw.line(scr, ACCENT, (sc(10), sc(56)), (w - sc(10), sc(56)), 1)

        # Dependency list
        y = sc(68)
        line_h = sc(20)

        for dep in deps:
            if dep['installed']:
                # Checkmark + name
                check = fonts['md_b'].render("\u2713", True, SUCCESS)
                scr.blit(check, (sc(14), y))
                name_s = fonts['md_b'].render(dep['name'], True, SUCCESS)
                scr.blit(name_s, (sc(32), y))
                # Description
                desc_s = fonts['sm'].render(dep['description'], True, TXT_DIM)
                scr.blit(desc_s, (sc(130), y + sc(2)))
                y += line_h
                # Found path
                if dep['found_path']:
                    path_s = fonts['sm'].render(f"Found: {dep['found_path']}", True, TXT_DIM)
                    scr.blit(path_s, (sc(32), y))
                    y += line_h
            else:
                # X mark + name
                xmark = fonts['md_b'].render("\u2717", True, DANGER)
                scr.blit(xmark, (sc(14), y))
                name_s = fonts['md_b'].render(dep['name'], True, DANGER)
                scr.blit(name_s, (sc(32), y))
                # Description
                desc_s = fonts['sm'].render(dep['description'], True, TXT_DIM)
                scr.blit(desc_s, (sc(130), y + sc(2)))
                y += line_h
                # Install command
                cmd_label = fonts['sm'].render("Install:", True, HINT)
                scr.blit(cmd_label, (sc(32), y))
                cmd_text = fonts['sm'].render(dep['install_cmd'], True, HDR)
                scr.blit(cmd_text, (sc(90), y))
                y += line_h
                # Required badge
                if dep['required']:
                    req_s = fonts['sm'].render("(required)", True, DANGER)
                    scr.blit(req_s, (sc(32), y))
                else:
                    req_s = fonts['sm'].render("(optional)", True, TXT_DIM)
                    scr.blit(req_s, (sc(32), y))
                y += line_h

            y += sc(4)  # Spacing between deps

        # Divider
        pygame.draw.line(scr, ACCENT, (sc(10), y), (w - sc(10), y), 1)
        y += sc(10)

        # Summary
        if all_ok:
            summary = fonts['lg'].render(f"\u2713  All {len(deps)} dependencies satisfied!", True, SUCCESS)
            scr.blit(summary, summary.get_rect(center=(w // 2, y + sc(6))))
        else:
            summary = fonts['lg'].render(f"\u2717  {len(missing)} of {len(deps)} missing", True, DANGER)
            scr.blit(summary, summary.get_rect(center=(w // 2, y + sc(6))))

        # Path input overlay
        if path_input_mode:
            # Semi-transparent overlay
            overlay = pygame.Surface((w, h), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 180))
            scr.blit(overlay, (0, 0))

            box_w, box_h = sc(500), sc(160)
            box_x = (w - box_w) // 2
            box_y = (h - box_h) // 2
            pygame.draw.rect(scr, BAR_BG, (box_x, box_y, box_w, box_h), border_radius=sc(8))
            pygame.draw.rect(scr, ACCENT, (box_x, box_y, box_w, box_h), 2, border_radius=sc(8))

            prompt = fonts['lg'].render(f"Locate {path_input_target}", True, HDR)
            scr.blit(prompt, prompt.get_rect(center=(w // 2, box_y + sc(24))))

            hint = fonts['sm'].render("Paste or type the full path to the executable:", True, TXT)
            scr.blit(hint, hint.get_rect(center=(w // 2, box_y + sc(50))))

            # Input field
            field_x = box_x + sc(16)
            field_w = box_w - sc(32)
            field_y = box_y + sc(68)
            field_h = sc(28)
            pygame.draw.rect(scr, KEY_BG, (field_x, field_y, field_w, field_h), border_radius=sc(4))
            pygame.draw.rect(scr, ACCENT, (field_x, field_y, field_w, field_h), 1, border_radius=sc(4))

            # Cursor blink
            cursor = "|" if int(elapsed * 2) % 2 == 0 else ""
            display_text = path_input_text + cursor
            # Truncate display if too long
            max_chars = field_w // max(1, fonts['md'].size("W")[0])
            if len(display_text) > max_chars:
                display_text = "..." + display_text[-(max_chars - 3):]
            input_surf = fonts['md'].render(display_text, True, TXT)
            scr.blit(input_surf, (field_x + sc(6), field_y + sc(5)))

            # Error message
            if path_input_error:
                err_s = fonts['sm'].render(path_input_error, True, DANGER)
                scr.blit(err_s, err_s.get_rect(center=(w // 2, box_y + sc(110))))

            # Hints
            hints = fonts['sm'].render("[Enter] Confirm    [Esc] Cancel    [Ctrl+V] Paste", True, HINT)
            scr.blit(hints, hints.get_rect(center=(w // 2, box_y + sc(138))))

        else:
            # Hint bar at bottom
            bar_h = sc(24)
            pygame.draw.rect(scr, BAR_BG, (0, h - bar_h, w, bar_h))
            pygame.draw.line(scr, ACCENT, (0, h - bar_h), (w, h - bar_h), 1)

            if not main_app_exists:
                hint_text = "ps2-picker.py not found in this folder  |  [Esc] Exit"
            elif all_ok:
                if check_only:
                    hint_text = "All OK!  |  [Esc] Exit  (--check-only mode)"
                else:
                    hint_text = "[A] Launch PS2 Picker    [B] Exit"
            elif promptable_missing:
                hint_text = "[A] Locate missing  |  [B] Exit"
            else:
                hint_text = "Install missing dependencies and try again  |  [B] Exit"

            # Pulsing hint
            pulse = 0.5 + 0.5 * abs(math.sin(elapsed * 2.0))
            hint_surf = fonts['sm'].render(hint_text, True, HINT)
            hint_surf.set_alpha(int(200 + 55 * pulse))
            scr.blit(hint_surf, hint_surf.get_rect(center=(w // 2, h - bar_h // 2)))

        pygame.display.flip()
        clock.tick(30)


# ═══ Entry Point ════════════════════════════════════════════════

def main():
    check_only = '--check-only' in sys.argv
    force_terminal = '--terminal' in sys.argv

    if HAS_PYGAME and not force_terminal:
        run_gui_mode(check_only)
    else:
        run_terminal_mode(check_only)


if __name__ == '__main__':
    main()

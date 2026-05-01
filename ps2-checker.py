#!/usr/bin/env python3
"""PS2 Picker Pre-Launcher — Dependency checker, installer helper, and self-updater.
   Themed GUI (matching main app) with terminal fallback.
   Zero external dependencies (stdlib only — pygame optional for GUI).

   Usage:
     python3 ps2-checker.py              Check for updates, check deps, launch if all OK
     python3 ps2-checker.py --check-only  Check deps, don't launch
     python3 ps2-checker.py --terminal    Force terminal mode (no GUI)
     python3 ps2-checker.py --skip-update Skip the update check
"""

VERSION = '0.1.33'

import os, sys, platform, shutil, subprocess, importlib.util, json, string, time, math, re
try:
    import urllib.request, urllib.error
    HAS_URLLIB = True
except ImportError:
    HAS_URLLIB = False

# ═══ Platform Detection ═════════════════════════════════════════

IS_WINDOWS = platform.system() == 'Windows'
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_APP = os.path.join(SCRIPT_DIR, 'ps2-picker.py')
APP_DIR = os.path.join(os.path.expanduser('~'), '.ps2-picker')
GLOBAL_CONFIG_PATH = os.path.join(APP_DIR, 'config.json')
CHECKER_CONFIG = os.path.join(APP_DIR, 'checker.json')
HAS_PYGAME = importlib.util.find_spec('pygame') is not None

# ═══ Update Constants ═══════════════════════════════════════════

GITHUB_RAW_URL = 'https://raw.githubusercontent.com/ryangjpriorx/ps2-picker'
UPDATE_BRANCHES = {0: 'main', 1: 'testing'}
UPDATE_FILES = ['ps2-picker.py', 'ps2-checker.py']
UPDATE_TIMEOUT = 10  # seconds

# ═══ Theme (matches main app) ══════════════════════════════════

import colorsys

_DEFAULT_THEME = {
    "bg":        [18, 8, 32],
    "accent":    [147, 51, 234],
    "highlight": [255, 200, 50],
    "text":      [220, 210, 190],
}

def _clamp(v): return max(0, min(255, int(v)))
def _blend(c1, c2, t): return tuple(_clamp(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
def _lighten(c, amt=40): return tuple(_clamp(v + amt) for v in c)
def _dim_color(c, factor=0.6): return tuple(_clamp(v * factor) for v in c)

def _apply_theme(theme_dict=None):
    """Derive all UI colors from 4 base colors. Mirrors main app logic."""
    global BG, SEL_BG, TXT, TXT_DIM, HDR, HINT, BAR_BG, ACCENT, DANGER, SUCCESS, KEY_BG
    t = dict(_DEFAULT_THEME)
    if theme_dict:
        t.update(theme_dict)
    bg  = tuple(t["bg"])
    acc = tuple(t["accent"])
    hi  = tuple(t["highlight"])
    txt = tuple(t["text"])
    BG      = bg
    ACCENT  = acc
    HDR     = hi
    TXT     = txt
    TXT_DIM = _dim_color(txt, 0.62)
    HINT    = _blend(hi, txt, 0.45)
    SEL_BG  = _blend(bg, acc, 0.45)
    BAR_BG  = _lighten(bg, 22)
    KEY_BG  = _lighten(bg, 20)
    DANGER  = (200, 60, 60)
    SUCCESS = (50, 180, 80)

def _load_theme():
    """Load theme from the shared global config (same file main app uses)."""
    theme = None
    if os.path.exists(GLOBAL_CONFIG_PATH):
        try:
            with open(GLOBAL_CONFIG_PATH) as f:
                cfg = json.load(f)
            theme = cfg.get('theme')
        except Exception:
            pass
    _apply_theme(theme)

_load_theme()

REF_W, REF_H = 640, 480


# ═══ Update Channel Config ══════════════════════════════════════

def get_update_channel():
    """Read update_channel from global config.
    Returns branch name string ('main' or 'testing').
    Config may store int (0=main, 1=testing) or string ('main'/'testing').
    """
    if os.path.exists(GLOBAL_CONFIG_PATH):
        try:
            with open(GLOBAL_CONFIG_PATH) as f:
                cfg = json.load(f)
            channel_val = cfg.get('update_channel', 0)
            # Accept both int and string values
            if isinstance(channel_val, str):
                if channel_val in ('main', 'testing'):
                    return channel_val
                # Try converting string '0'/'1' to int
                try:
                    channel_val = int(channel_val)
                except (ValueError, TypeError):
                    return 'main'
            return UPDATE_BRANCHES.get(channel_val, 'main')
        except Exception:
            pass
    return 'main'


# ═══ Self-Updater (HTTP only — no git required) ════════════════

def _parse_version(v):
    """Parse a version string like '0.1.0' into a comparable tuple (0, 1, 0)."""
    try:
        return tuple(int(x) for x in v.strip().split('.'))
    except (ValueError, AttributeError):
        return (0, 0, 0)


def _extract_version_from_text(text):
    """Extract VERSION = '...' from a block of Python source text."""
    m = re.search(r"VERSION\s*=\s*['\"](\d+(?:\.\d+)*)['\"]", text)
    return m.group(1) if m else None


def _download_file(branch, filename):
    """Download a file from GitHub raw and return its content as string."""
    if not HAS_URLLIB:
        raise RuntimeError("urllib not available")
    url = f'{GITHUB_RAW_URL}/{branch}/{filename}'
    req = urllib.request.Request(url, headers={'User-Agent': 'PS2Picker-Updater'})
    with urllib.request.urlopen(req, timeout=UPDATE_TIMEOUT) as resp:
        return resp.read().decode('utf-8', errors='replace')


def check_for_updates():
    """Check if newer versions are available on the configured channel via HTTP.

    Downloads just the first 2KB of each file to read VERSION.

    Returns:
        dict with keys:
            'available'      (bool) - True if any remote file is newer
            'channel'        (str)  - The branch checked
            'local_version'  (str)  - Local VERSION string
            'remote_version' (str)  - Remote VERSION string (or None)
            'files_to_update'(list) - Filenames that need updating
            'error'          (str)  - Error message if check failed, else None
    """
    channel = get_update_channel()
    files_to_update = []
    remote_ver_str = None

    for filename in UPDATE_FILES:
        local_path = os.path.join(SCRIPT_DIR, filename)

        # Read local version
        local_ver = None
        if os.path.isfile(local_path):
            try:
                with open(local_path, 'r', encoding='utf-8', errors='replace') as fh:
                    # Only read first 2KB — VERSION is near the top
                    head = fh.read(2048)
                local_ver_str = _extract_version_from_text(head)
                if local_ver_str:
                    local_ver = _parse_version(local_ver_str)
            except Exception:
                pass

        # Fetch remote version via HTTP
        try:
            url = f'{GITHUB_RAW_URL}/{channel}/{filename}'
            req = urllib.request.Request(url, headers={
                'User-Agent': 'PS2Picker-Updater',
            })
            with urllib.request.urlopen(req, timeout=UPDATE_TIMEOUT) as resp:
                # Read only first 2KB from response body (VERSION is near top)
                head = resp.read(2048).decode('utf-8', errors='ignore')
            rv_str = _extract_version_from_text(head)
            if rv_str:
                remote_ver = _parse_version(rv_str)
                if remote_ver_str is None or _parse_version(rv_str) > _parse_version(remote_ver_str):
                    remote_ver_str = rv_str
                if local_ver is None or remote_ver > local_ver:
                    files_to_update.append(filename)
        except Exception as e:
            return {
                'available': False, 'channel': channel,
                'local_version': VERSION, 'remote_version': None,
                'files_to_update': [],
                'error': f'Could not reach GitHub: {e}'
            }

    return {
        'available': len(files_to_update) > 0,
        'channel': channel,
        'local_version': VERSION,
        'remote_version': remote_ver_str,
        'files_to_update': files_to_update,
        'error': None,
    }


def perform_update(files_to_update=None):
    """Download and replace files that need updating.

    Uses pure HTTP — no git required. Downloads full file content,
    writes to a temp file, then renames atomically.

    Returns:
        (success: bool, message: str)
    """
    channel = get_update_channel()
    old_version = VERSION

    if not files_to_update:
        files_to_update = UPDATE_FILES

    updated = []
    newest_ver = None

    for filename in files_to_update:
        local_path = os.path.join(SCRIPT_DIR, filename)

        try:
            remote_text = _download_file(channel, filename)
        except Exception as e:
            return False, f'Download failed for {filename}: {e}'

        # Extract version from downloaded content
        rv = _extract_version_from_text(remote_text)
        if rv and (newest_ver is None or _parse_version(rv) > _parse_version(newest_ver)):
            newest_ver = rv

        # Write to temp file, then rename for atomic-ish update
        try:
            tmp_path = local_path + '.update_tmp'
            with open(tmp_path, 'w', encoding='utf-8') as fh:
                fh.write(remote_text)
            # On Windows, can't rename over existing — remove first
            if IS_WINDOWS and os.path.exists(local_path):
                os.remove(local_path)
            os.rename(tmp_path, local_path)
            updated.append(filename)
        except Exception as e:
            # Clean up temp file
            try:
                os.remove(tmp_path)
            except Exception:
                pass
            return False, f'Failed to write {filename}: {e}'

    if not updated:
        return False, 'No files were updated'

    new_ver = newest_ver or '???'
    return True, f'Updated {old_version} \u2192 {new_ver} ({channel})'


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

    for drive in _get_windows_drives():
        for folder in scan_folders:
            p = os.path.join(drive, folder, exe_name)
            if os.path.isfile(p):
                return p
    return None


def check_command(cmd):
    """Check if a command is available on PATH, saved config, or common locations."""
    cfg = load_checker_config()
    custom_paths = cfg.get('custom_paths', {})
    if cmd in custom_paths:
        saved = custom_paths[cmd]
        if os.path.isfile(saved):
            return True
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


def run_terminal_mode(check_only=False, skip_update=False):
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

    # ─── Update check ───────────────────────────────────
    channel = get_update_channel()
    print(f"  {_cyan('Version:')}  {VERSION}")
    print(f"  {_cyan('Update Channel:')}  {channel}")

    if skip_update:
        print(f"  {_dim('Update check skipped (--skip-update)')}")
    elif HAS_URLLIB:
        print(f"  {_dim('Checking for updates...')}")
        update_info = check_for_updates()
        if update_info['error']:
            print(f"  {_yellow('!')}  Update check: {update_info['error']}")
        elif update_info['available']:
            local_v = update_info.get('local_version', VERSION)
            remote_v = update_info.get('remote_version', '?')
            files = update_info.get('files_to_update', [])
            print(f"  {_yellow('!')}  {_bold('Update available:')} {local_v} \u2192 {remote_v} ({channel})")
            print(f"     Files: {', '.join(files)}")
            print()
            try:
                ans = input(f"  {_cyan('?')}  Update now? [Y/n]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = 'n'
            if ans in ('', 'y', 'yes'):
                print(f"  {_dim('Downloading updates...')}")
                ok, msg = perform_update(files)
                if ok:
                    print(f"  {_green('OK')}  {msg}")
                    print(f"  {_cyan('->')}  Restarting...")
                    print()
                    os.execv(sys.executable, [sys.executable] + sys.argv + ['--skip-update'])
                else:
                    print(f"  {_red('X')}  {msg}")
            else:
                print(f"  {_dim('Update skipped.')}")
        else:
            print(f"  {_green('OK')}  Up to date (v{VERSION})")
    else:
        print(f"  {_dim('Update check skipped: urllib not available')}")

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


# ═══ Button Map (shared with picker via global config) ═════════

DEFAULT_BUTTON_MAP = {
    "confirm":    0,
    "back":       1,
    "extra":      2,
    "alt":        3,
    "shoulder_l": 4,
    "shoulder_r": 5,
    "select":     6,
    "start":      7,
}

def _load_button_map():
    """Load button mappings from the shared global config."""
    btn = dict(DEFAULT_BUTTON_MAP)
    if os.path.exists(GLOBAL_CONFIG_PATH):
        try:
            with open(GLOBAL_CONFIG_PATH) as f:
                cfg = json.load(f)
            saved = cfg.get("button_map", {})
            for k in btn:
                if k in saved and isinstance(saved[k], int):
                    btn[k] = saved[k]
        except Exception:
            pass
    return btn


# ═══ GUI Mode (pygame) ═════════════════════════════════════════

def run_gui_mode(check_only=False, skip_update=False):
    """Themed pygame GUI dependency checker with update support."""
    import pygame

    os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
    if not IS_WINDOWS:
        os.environ.setdefault('DISPLAY', ':0')

    pygame.init()
    pygame.joystick.init()

    disp_info = pygame.display.Info()
    native_h = disp_info.current_h

    if native_h > 720:
        scr = pygame.display.set_mode((REF_W, REF_H), pygame.RESIZABLE)
    else:
        scr = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    pygame.display.set_caption("PS2 Picker - Dependency Check")
    w, h = scr.get_size()
    scale = h / REF_H

    def sc(val):
        return max(1, int(val * scale))

    font_name = None
    for candidate in ["DejaVu Sans", "Segoe UI", "Arial", "Helvetica", None]:
        if candidate is None:
            break
        if candidate.lower().replace(' ', '') in [f.lower().replace(' ', '') for f in pygame.font.get_fonts()]:
            font_name = candidate
            break

    def _build_fonts():
        return {
            'sm': pygame.font.SysFont(font_name, sc(11)),
            'md': pygame.font.SysFont(font_name, sc(14)),
            'md_b': pygame.font.SysFont(font_name, sc(14), bold=True),
            'lg': pygame.font.SysFont(font_name, sc(18), bold=True),
            'xl': pygame.font.SysFont(font_name, sc(22), bold=True),
        }

    fonts = _build_fonts()
    clock = pygame.time.Clock()
    btn = _load_button_map()

    joy = None
    if pygame.joystick.get_count() > 0:
        joy = pygame.joystick.Joystick(0)
        joy.init()

    # Run dependency checks
    deps, distro, pretty = run_checks()
    missing = [d for d in deps if not d['installed']]
    all_ok = len(missing) == 0
    main_app_exists = os.path.exists(MAIN_APP)
    promptable_missing = [d for d in missing if d['name'] in ('retroarch', '7z')]

    # Update state
    update_info = None
    update_status = 'idle'
    update_msg = ''
    channel = get_update_channel()

    # Path input state
    path_input_mode = False
    path_input_target = None
    path_input_text = ""
    path_input_error = ""

    scroll = 0
    last_joy = 0
    DPAD_DELAY = 0.18
    start_time = time.time()

    # If all OK and not check-only, check for updates first then launch
    if not skip_update and HAS_URLLIB:
        # Show checking message
        scr.fill(BG)
        msg_s = fonts['md'].render(f"Checking for updates ({channel})...", True, TXT)
        scr.blit(msg_s, msg_s.get_rect(center=(w // 2, h // 2)))
        pygame.display.flip()

        update_info = check_for_updates()

        if update_info and update_info.get('available'):
            update_status = 'result'
            # Don't auto-launch — show the update prompt
        elif all_ok and main_app_exists and not check_only:
            # Up to date and deps OK — launch immediately
            pygame.quit()
            _launch()
            return
        else:
            update_status = 'result'
    elif skip_update:
        update_status = 'skipped'
        if all_ok and main_app_exists and not check_only:
            pygame.quit()
            _launch()
            return
    else:
        if all_ok and main_app_exists and not check_only:
            pygame.quit()
            _launch()
            return

    def refresh_status():
        nonlocal missing, all_ok, promptable_missing
        missing = [d for d in deps if not d['installed']]
        all_ok = len(missing) == 0
        promptable_missing = [d for d in missing if d['name'] in ('retroarch', '7z')]

    def try_save_path(dep_name, path_str):
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
            for d in deps:
                if d['name'] == dep_name:
                    d['installed'] = True
                    d['found_path'] = path_str
            refresh_status()
            return True, ""
        return False, f"File not found: {path_str}"

    def _do_update():
        nonlocal update_status, update_msg
        files = update_info.get('files_to_update', UPDATE_FILES) if update_info else UPDATE_FILES
        update_status = 'updating'
        scr.fill(BG)
        upd_surf = fonts['lg'].render('Downloading updates...', True, HINT)
        scr.blit(upd_surf, upd_surf.get_rect(center=(w // 2, h // 2)))
        pygame.display.flip()
        ok, msg = perform_update(files)
        update_msg = msg
        if ok:
            update_status = 'done'
            # Show success briefly then restart
            scr.fill(BG)
            s1 = fonts['lg'].render('Update Applied!', True, HDR)
            s2 = fonts['md'].render(msg, True, ACCENT)
            s3 = fonts['sm'].render('Restarting...', True, TXT_DIM)
            scr.blit(s1, s1.get_rect(center=(w // 2, h // 2 - sc(20))))
            scr.blit(s2, s2.get_rect(center=(w // 2, h // 2 + sc(6))))
            scr.blit(s3, s3.get_rect(center=(w // 2, h // 2 + sc(28))))
            pygame.display.flip()
            time.sleep(2)
            pygame.quit()
            os.execv(sys.executable, [sys.executable] + sys.argv + ['--skip-update'])
        else:
            update_status = 'failed'

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
                fonts = _build_fonts()

            if path_input_mode:
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
                            d = promptable_missing[0]
                            path_input_mode = True
                            path_input_target = d['name']
                            path_input_text = ""
                            path_input_error = ""
                    elif ev.key == pygame.K_u or ev.key == pygame.K_y:
                        if (update_info and update_info.get('available')
                                and update_status == 'result'):
                            _do_update()
                    elif ev.key == pygame.K_ESCAPE:
                        pygame.quit(); sys.exit(0)
                    elif ev.key == pygame.K_UP:
                        scroll = max(0, scroll - 1)
                    elif ev.key == pygame.K_DOWN:
                        scroll += 1

                if hasattr(pygame, 'JOYDEVICEADDED') and ev.type == pygame.JOYDEVICEADDED:
                    pygame.joystick.quit()
                    pygame.joystick.init()
                    if pygame.joystick.get_count() > 0:
                        joy = pygame.joystick.Joystick(0)
                        joy.init()

                if ev.type == pygame.JOYHATMOTION:
                    hx, hy = ev.value
                    if hy == 1:
                        scroll = max(0, scroll - 1); last_joy = now
                    elif hy == -1:
                        scroll += 1; last_joy = now

                if ev.type == pygame.JOYBUTTONDOWN:
                    if ev.button in (btn['confirm'], btn['start']):
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
                    elif ev.button == btn['back']:
                        pygame.quit(); sys.exit(0)
                    elif ev.button == btn['alt']:  # Y = update
                        if (update_info and update_info.get('available')
                                and update_status == 'result'):
                            _do_update()

        # Analog stick
        if joy is not None and now - last_joy > DPAD_DELAY:
            try:
                ya = joy.get_axis(1)
                if ya < -0.5:
                    scroll = max(0, scroll - 1); last_joy = now
                elif ya > 0.5:
                    scroll += 1; last_joy = now
            except Exception:
                pass

        # ─── Draw ───────────────────────────────────────────
        scr.fill(BG)

        header_h = sc(58)
        footer_h = sc(72)

        # Header
        title_surf = fonts['xl'].render(f"PS2 Picker v{VERSION}", True, HDR)
        scr.blit(title_surf, title_surf.get_rect(center=(w // 2, sc(20))))

        os_text = f"Detected: {pretty}  |  Python {platform.python_version()}"
        os_surf = fonts['sm'].render(os_text, True, ACCENT)
        scr.blit(os_surf, os_surf.get_rect(center=(w // 2, sc(42))))

        pygame.draw.line(scr, ACCENT, (sc(10), header_h), (w - sc(10), header_h), 1)

        # Dependency list
        content_top = header_h + sc(6)
        content_bottom = h - footer_h
        line_h = sc(18)
        y = content_top - scroll * line_h

        for dep in deps:
            if dep['installed']:
                if content_top - line_h < y < content_bottom:
                    check = fonts['md_b'].render("\u2713", True, SUCCESS)
                    scr.blit(check, (sc(14), y))
                    name_s = fonts['md_b'].render(dep['name'], True, SUCCESS)
                    scr.blit(name_s, (sc(32), y))
                    desc_s = fonts['sm'].render(dep['description'], True, TXT_DIM)
                    scr.blit(desc_s, (sc(130), y + sc(1)))
                y += line_h
                if dep.get('found_path'):
                    if content_top - line_h < y < content_bottom:
                        path_s = fonts['sm'].render(f"Found: {dep['found_path']}", True, TXT_DIM)
                        scr.blit(path_s, (sc(32), y))
                    y += line_h
            else:
                if content_top - line_h < y < content_bottom:
                    xmark = fonts['md_b'].render("\u2717", True, DANGER)
                    scr.blit(xmark, (sc(14), y))
                    name_s = fonts['md_b'].render(dep['name'], True, DANGER)
                    scr.blit(name_s, (sc(32), y))
                    desc_s = fonts['sm'].render(dep['description'], True, TXT_DIM)
                    scr.blit(desc_s, (sc(130), y + sc(1)))
                y += line_h
                if content_top - line_h < y < content_bottom:
                    cmd_label = fonts['sm'].render("Install:", True, HINT)
                    scr.blit(cmd_label, (sc(32), y))
                    cmd_text = fonts['sm'].render(dep['install_cmd'], True, HDR)
                    scr.blit(cmd_text, (sc(90), y))
                y += line_h
                if content_top - line_h < y < content_bottom:
                    badge_text = "(required)" if dep['required'] else "(optional)"
                    badge_col = DANGER if dep['required'] else TXT_DIM
                    scr.blit(fonts['sm'].render(badge_text, True, badge_col), (sc(32), y))
                y += line_h
            y += sc(3)

        # Footer
        fy = h - footer_h
        pygame.draw.line(scr, ACCENT, (sc(10), fy), (w - sc(10), fy), 1)
        fy += sc(8)

        if all_ok:
            summary = fonts['lg'].render(f"\u2713  All {len(deps)} dependencies satisfied!", True, SUCCESS)
        else:
            summary = fonts['lg'].render(f"\u2717  {len(missing)} of {len(deps)} missing", True, DANGER)
        scr.blit(summary, summary.get_rect(center=(w // 2, fy + sc(4))))
        fy += sc(20)

        # Update status line
        if update_status == 'skipped':
            upd_s = fonts['sm'].render("Update check skipped (--skip-update)", True, TXT_DIM)
            scr.blit(upd_s, upd_s.get_rect(center=(w // 2, fy)))
        elif update_info and update_info.get('error'):
            upd_s = fonts['sm'].render(f"Update check: {update_info['error']}", True, TXT_DIM)
            scr.blit(upd_s, upd_s.get_rect(center=(w // 2, fy)))
        elif update_status == 'result' and update_info:
            if update_info['available']:
                local_v = update_info.get('local_version', VERSION)
                remote_v = update_info.get('remote_version', '?')
                upd_label = f"\u2b06 v{local_v} \u2192 v{remote_v} ({channel})  [Y] Update"
                upd_s = fonts['sm'].render(upd_label, True, HDR)
                scr.blit(upd_s, upd_s.get_rect(center=(w // 2, fy)))
            else:
                upd_s = fonts['sm'].render(f"\u2713  Up to date (v{VERSION} on {channel})", True, SUCCESS)
                scr.blit(upd_s, upd_s.get_rect(center=(w // 2, fy)))
        elif update_status == 'done':
            upd_s = fonts['sm'].render(f"\u2713  {update_msg}", True, SUCCESS)
            scr.blit(upd_s, upd_s.get_rect(center=(w // 2, fy)))
        elif update_status == 'failed':
            upd_s = fonts['sm'].render(f"\u2717  {update_msg}", True, DANGER)
            scr.blit(upd_s, upd_s.get_rect(center=(w // 2, fy)))

        # Path input overlay
        if path_input_mode:
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

            field_x = box_x + sc(16)
            field_w = box_w - sc(32)
            field_y = box_y + sc(68)
            field_h = sc(28)
            pygame.draw.rect(scr, KEY_BG, (field_x, field_y, field_w, field_h), border_radius=sc(4))
            pygame.draw.rect(scr, ACCENT, (field_x, field_y, field_w, field_h), 1, border_radius=sc(4))

            cursor = "|" if int(elapsed * 2) % 2 == 0 else ""
            display_text = path_input_text + cursor
            max_chars = field_w // max(1, fonts['md'].size("W")[0])
            if len(display_text) > max_chars:
                display_text = "..." + display_text[-(max_chars - 3):]
            input_surf = fonts['md'].render(display_text, True, TXT)
            scr.blit(input_surf, (field_x + sc(6), field_y + sc(5)))

            if path_input_error:
                err_s = fonts['sm'].render(path_input_error, True, DANGER)
                scr.blit(err_s, err_s.get_rect(center=(w // 2, box_y + sc(110))))

            hints = fonts['sm'].render("[Enter] Confirm    [Esc] Cancel    [Ctrl+V] Paste", True, HINT)
            scr.blit(hints, hints.get_rect(center=(w // 2, box_y + sc(138))))
        else:
            # Hint bar
            bar_h = sc(32)
            pygame.draw.rect(scr, BAR_BG, (0, h - bar_h, w, bar_h))
            pygame.draw.line(scr, ACCENT, (0, h - bar_h), (w, h - bar_h), 1)

            upd_hint = ""
            if update_info and update_info.get('available') and update_status == 'result':
                upd_hint = "  |  [Y] Update"

            if not main_app_exists:
                hint_text = "ps2-picker.py not found  |  [Esc] Exit"
            elif all_ok:
                if check_only:
                    hint_text = "All OK!  |  [Esc] Exit  (--check-only)" + upd_hint
                else:
                    hint_text = "[A] Launch PS2 Picker  |  [B] Exit" + upd_hint
            elif promptable_missing:
                hint_text = "[A] Locate missing  |  [B] Exit" + upd_hint
            else:
                hint_text = "Install missing deps and try again  |  [B] Exit" + upd_hint

            pulse = 0.5 + 0.5 * abs(math.sin(elapsed * 2.0))
            hint_surf = fonts['md'].render(hint_text, True, HINT)
            hint_surf.set_alpha(int(200 + 55 * pulse))
            scr.blit(hint_surf, hint_surf.get_rect(center=(w // 2, h - bar_h // 2)))

        pygame.display.flip()
        clock.tick(30)


# ═══ Entry Point ════════════════════════════════════════════════

def main():
    check_only = '--check-only' in sys.argv
    force_terminal = '--terminal' in sys.argv
    skip_update = '--skip-update' in sys.argv

    # Strip --skip-update so it doesn't persist across re-execs
    sys.argv = [a for a in sys.argv if a != '--skip-update']

    if HAS_PYGAME and not force_terminal:
        run_gui_mode(check_only, skip_update)
    else:
        run_terminal_mode(check_only, skip_update)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        import traceback
        crash_log = os.path.join(APP_DIR, 'crash.log')
        os.makedirs(APP_DIR, exist_ok=True)
        with open(crash_log, 'w') as f:
            f.write(f'PS2 Checker v{VERSION} crash at {time.strftime("%Y-%m-%d %H:%M:%S")}\n')
            f.write(f'Platform: {platform.platform()}\n')
            f.write(f'Python: {platform.python_version()}\n\n')
            traceback.print_exc(file=f)
        traceback.print_exc()
        print(f'\nCrash log saved to: {crash_log}')
        sys.exit(1)

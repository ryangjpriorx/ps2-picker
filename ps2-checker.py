#!/usr/bin/env python3
"""PS2 Picker Pre-Launcher — Dependency checker and installer helper.
   Runs before the main app to verify everything is in place.
   Zero external dependencies (stdlib only).

   Usage:
     python3 ps2-checker.py              Check deps, launch if all OK
     python3 ps2-checker.py --check-only  Check deps, don't launch
"""

import os, sys, platform, shutil, subprocess, importlib.util

# ═══ Platform Detection ═════════════════════════════════════════

IS_WINDOWS = platform.system() == 'Windows'
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_APP = os.path.join(SCRIPT_DIR, 'ps2-picker.py')


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


def check_command(cmd):
    """Check if a command is available on PATH or common locations."""
    if shutil.which(cmd):
        return True
    if IS_WINDOWS:
        pf = os.environ.get('PROGRAMFILES', '')
        pf86 = os.environ.get('PROGRAMFILES(X86)', '')
        extras = []
        if cmd == '7z':
            extras = [
                os.path.join(pf, '7-Zip', '7z.exe'),
                os.path.join(pf86, '7-Zip', '7z.exe'),
            ]
        elif cmd == 'retroarch':
            extras = [
                os.path.join(pf, 'RetroArch', 'retroarch.exe'),
                os.path.join(pf86, 'RetroArch', 'retroarch.exe'),
                os.path.join(pf, 'RetroArch-Win64', 'retroarch.exe'),
            ]
        for p in extras:
            if p and os.path.isfile(p):
                return True
    return False


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
        'retroarch': 'sudo dnf install retroarch  (or use Flatpak)',
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
        'pygame':    'Pre-installed on Batocera (update your image if missing)',
        '7z':        'Pre-installed on Batocera (update your image if missing)',
        'retroarch': 'Pre-installed on Batocera (built-in)',
    },
    'suse': {
        'pygame':    'sudo zypper install python3-pygame',
        '7z':        'sudo zypper install p7zip-full',
        'retroarch': 'sudo zypper install retroarch  (or use Flatpak)',
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
    'pygame':    'pip install pygame  (or use your package manager)',
    '7z':        'Install p7zip via your package manager',
    'retroarch': 'Install RetroArch from https://retroarch.com or your package manager',
}


# ═══ Terminal Colors ════════════════════════════════════════════

def supports_color():
    """Check if terminal supports ANSI colors."""
    if IS_WINDOWS:
        # Enable ANSI on Windows 10+ terminals
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # Enable virtual terminal processing
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            return True
        except Exception:
            return os.environ.get('WT_SESSION') is not None  # Windows Terminal
    return hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()


USE_COLOR = supports_color()


def c(text, code):
    """Wrap text in ANSI color code if supported."""
    if USE_COLOR:
        return f"\033[{code}m{text}\033[0m"
    return text


def green(t):  return c(t, '32')
def red(t):    return c(t, '31')
def yellow(t): return c(t, '33')
def cyan(t):   return c(t, '36')
def bold(t):   return c(t, '1')
def dim(t):    return c(t, '90')


# ═══ Display Results ════════════════════════════════════════════

def print_header(pretty_name, distro):
    """Print the checker header."""
    print()
    print(bold("=" * 60))
    print(bold("  PS2 Picker — Dependency Checker"))
    print(bold("=" * 60))
    print()
    print(f"  {cyan('OS Detected:')}  {pretty_name}")
    print(f"  {cyan('Distro Family:')}  {distro}")
    print(f"  {cyan('Python:')}  {platform.python_version()}")
    print()
    print(bold("-" * 60))


def print_dep_result(name, description, installed, install_cmd):
    """Print a single dependency result."""
    if installed:
        status = green("✓ INSTALLED")
    else:
        status = red("✗ MISSING")

    print(f"  {status}  {bold(name)}")
    print(f"           {dim(description)}")
    if not installed:
        print(f"           {yellow('Install:')}  {install_cmd}")
    print()


def print_footer(missing_count, total_count):
    """Print the checker footer."""
    print(bold("-" * 60))
    if missing_count == 0:
        print()
        print(f"  {green('✓')}  {bold(f'All {total_count} dependencies satisfied!')}")
        print()
    else:
        print()
        print(f"  {red('✗')}  {bold(f'{missing_count} of {total_count} dependencies missing')}")
        print()
        print(f"  Install the missing dependencies above and run this again.")
        print()
    print(bold("=" * 60))


def print_launch_info():
    """Print info about what's happening next."""
    print()
    print(f"  {cyan('→')}  Launching PS2 Picker...")
    print()


# ═══ Main Logic ═════════════════════════════════════════════════

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
        },
        {
            'name': '7z',
            'description': '7-Zip archive extraction (for .7z game archives)',
            'installed': check_command('7z'),
            'install_cmd': cmds.get('7z', FALLBACK_COMMANDS['7z']),
            'required': False,  # Only needed if user has .7z files
        },
        {
            'name': 'retroarch',
            'description': 'RetroArch emulator (launches PS2 games)',
            'installed': check_command('retroarch'),
            'install_cmd': cmds.get('retroarch', FALLBACK_COMMANDS['retroarch']),
            'required': True,
        },
    ]

    return deps, distro, pretty


def main():
    check_only = '--check-only' in sys.argv

    deps, distro, pretty = run_checks()

    # Display results
    print_header(pretty, distro)
    for dep in deps:
        print_dep_result(dep['name'], dep['description'],
                         dep['installed'], dep['install_cmd'])

    missing = [d for d in deps if not d['installed']]
    required_missing = [d for d in missing if d['required']]
    optional_missing = [d for d in missing if not d['required']]

    print_footer(len(missing), len(deps))

    # Show optional warnings
    if optional_missing and not required_missing:
        for d in optional_missing:
            print(f"  {yellow('⚠')}  {d['name']} is optional but recommended.")
            print(f"     You can still launch, but some features won't work.")
            print()

    # Check if main app file exists
    if not os.path.exists(MAIN_APP):
        print(f"  {red('✗')}  Main app not found: {MAIN_APP}")
        print(f"     Place ps2-picker.py in the same folder as this script.")
        print()
        wait_for_exit()
        sys.exit(1)

    # Decision logic
    if required_missing:
        print(f"  {red('Cannot launch:')} required dependencies are missing.")
        print()
        wait_for_exit()
        sys.exit(1)

    if check_only:
        print(f"  {dim('--check-only mode: not launching.')}")
        print()
        wait_for_exit()
        sys.exit(0)

    # All required deps present — launch the app
    print_launch_info()
    launch_main_app()


def wait_for_exit():
    """Wait for user input before closing (prevents terminal from vanishing on Windows)."""
    try:
        input("  Press Enter to exit...")
    except (EOFError, KeyboardInterrupt):
        pass


def launch_main_app():
    """Launch the main PS2 Picker app."""
    python = sys.executable
    try:
        os.execv(python, [python, MAIN_APP])
    except Exception:
        # Fallback if execv fails
        try:
            subprocess.run([python, MAIN_APP])
        except Exception as e:
            print(f"  {red('Launch failed:')} {e}")
            wait_for_exit()
            sys.exit(1)


if __name__ == '__main__':
    main()

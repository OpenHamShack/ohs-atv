"""What differs between the systems OpenHamShack ATV runs on, kept in one place.

The program was written on Debian and assumes a Linux desktop wherever it
touches the outside world: where config lives, where ffmpeg and libhackrf
are found, which camera interface ffmpeg reads, which directory holds the
radio lock, how a process is asked whether it is alive, and which sentence
tells the operator what to install. Windows (a PyInstaller build,
packaging/windows/) differs in exactly those places and nowhere in the
radio, so each difference is answered here rather than as an `if
sys.platform` scattered through the engine. On Linux every function returns
precisely what the code did before this module existed - the .deb must not
notice it.

Copied in shape from OpenHamShack DATV's ohsdatv/host.py (itself NB's
ohscore/host.py made for DATV), whose Windows build proved each answer it
shares with this one: the bundle's subfolders, the radio lock's folder,
boot id and process liveness, libiio's DLL search and the console
children. New here: libhackrf's DLL and the DirectShow camera list
(ohsatv/sources.py asks for it). No code is shared at run time, as with
everything else copied from the siblings: the programs ship separately.

`WINDOWS` is read through `windows()` everywhere below, not captured at
import, so a test can turn it on for one call (mock.patch.object) and see
the Windows answer on Linux - the branch exercised, not its source read.
Standard library only, and nothing Windows-only is imported at module
level, so the module imports everywhere.
"""
from __future__ import annotations

import contextlib
import ctypes
import os
import shutil
import sys
from pathlib import Path

NAME = "OpenHamShack ATV"
APP = "openhamshack-atv"
PACKAGE = "ohsatv"

WINDOWS = sys.platform == "win32"
# macOS differs from Linux, for now, only in the radio lock's folder; the
# rest of the macOS port waits (HANDOVER: paused by Brian).
MACOS = sys.platform == "darwin"

# Subfolders of a Windows bundle (PyInstaller's _internal), each a set of
# files shipped as upstream built them, kept apart so nothing searching the
# program's own folder picks up another set's DLLs by accident:
#   ffmpeg\   ffmpeg.exe, ffprobe.exe (gyan.dev's static build, DATV's pin)
#   libiio\   libiio 0.26 and the DLLs it links against (NB's and DATV's)
#   hackrf\   hackrf-0.dll and the two DLLs it imports (conda-forge's build)
FFMPEG_SUBDIR = "ffmpeg"
LIBIIO_SUBDIR = "libiio"
HACKRF_SUBDIR = "hackrf"


def windows() -> bool:
    return WINDOWS


def macos() -> bool:
    return MACOS


# ------------------------------------------------------------------ bundle
def bundle_dir() -> Path | None:
    """The folder a frozen build runs from (PyInstaller's sys._MEIPASS, the
    _internal folder beside the .exe for a onedir build), or None when
    running from source or from the .deb."""
    if not getattr(sys, "frozen", False):
        return None
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(sys.executable).resolve().parent


def _bundle_dirs() -> list[Path]:
    """Every folder a bundled file may sit in: the unpack folder and the
    folder holding the .exe (different from PyInstaller 6 on), so a file
    dropped beside the .exe by hand is found too."""
    b = bundle_dir()
    if b is None:
        return []
    out = [b]
    exe = Path(sys.executable).resolve().parent
    if exe != b:
        out.append(exe)
    return out


def bundled(subdir: str, marker: str | None = None, dirs: list[Path] | None = None) -> Path | None:
    """A subfolder of a Windows bundle (holding `marker`, when given), or
    None: not Windows, not frozen, or the bundle lacks it. `dirs` replaces
    the bundle's own folders (the tests)."""
    if not windows():
        return None
    for d in (dirs if dirs is not None else _bundle_dirs()):
        cand = Path(d) / subdir
        if cand.is_dir() and (marker is None or (cand / marker).is_file()):
            return cand
    return None


def self_command() -> list[str]:
    """The argv that starts this program again (what an administrator is
    told to run to clear a stale lock): `python -m ohsatv` from source and
    the .deb; the frozen .exe itself on Windows, which has no -m."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", PACKAGE]


# ------------------------------------------------------------------ places
def _appdata() -> Path:
    env = os.environ.get("APPDATA")
    return Path(env) if env else Path.home() / "AppData" / "Roaming"


def conf_dir() -> Path:
    """Where config.json and ui.json live when $OHSATV_CONF does not say:
    ~/.config/openhamshack-atv, or %APPDATA%\\openhamshack-atv."""
    if windows():
        return _appdata() / APP
    return Path.home() / ".config" / APP


def data_dir() -> Path:
    """Recordings, captures and the transmit log when $OHSATV_DATA does not
    say. On Windows a "data" folder under the config folder rather than the
    folder itself (NB's and DATV's choice): the operator who opens
    %APPDATA%\\openhamshack-atv to find a recording does not have to pick it out
    from between config files."""
    if windows():
        return _appdata() / APP / "data"
    return Path.home() / ".local" / "share" / APP


# ------------------------------------------------------------------ the radio lock
def lock_dir() -> Path:
    """The radio lock's directory on this computer (OpenHamShack DATV's
    docs/radio-lock.md): every OpenHamShack program on the machine must use the
    same one, or they cannot see each other's markers.

    Linux: /run/lock - RAM, root's, world-writable with the sticky bit,
    cleared at boot. Windows: %ProgramData%\\openhamshack-radio, the folder NB,
    HAM, DATV and Desktop use, made with its ACL by the installer
    (packaging/windows/radio-lock-folder.cmd, the same file in each) and
    refused without it (lock_dir_problem).

    macOS: /private/tmp. A Mac has no /run/lock (asking for it was the
    "does not exist" warning that refused every shared radio there), and
    /var/run is root's and not world-writable. /private/tmp is what
    /run/lock is on Linux - root's, mode 1777, sticky, emptied at boot - so
    it passes the same root-and-sticky rule unchanged and every account on
    the Mac sees one set of markers. A per-user folder (~/Library/Caches,
    $TMPDIR under /var/folders) was the obvious alternative and is the one
    thing the lock must not use: each account would see only its own
    markers (lockacl's docstring). The real path, not /tmp: /tmp is a
    symlink there, and folder_problems refuses a link. Any OpenHamShack
    program that takes the radio lock on a Mac must name this same folder."""
    if windows():
        base = os.environ.get("ProgramData") or os.environ.get("ALLUSERSPROFILE") or r"C:\ProgramData"
        return Path(base) / "openhamshack-radio"
    if macos():
        return Path("/private/tmp")
    return Path("/run/lock")


LOCK_REPAIR_SCRIPT = "radio-lock-folder.cmd"


def lock_repair_script() -> Path | None:
    """The script that gives the Windows lock folder its ACL: the
    installer's copy beside the .exe, or the repository's when running from
    source. None if neither is there."""
    exe_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None
    for cand in ([exe_dir / LOCK_REPAIR_SCRIPT] if exe_dir else []) + \
            [Path(__file__).resolve().parent.parent / "packaging" / "windows" / LOCK_REPAIR_SCRIPT]:
        if cand.is_file():
            return cand
    return None


def lock_dir_problem(path: Path | None = None, problems=None) -> str:
    """'' when the radio lock's folder keeps the rule that only a marker's
    owner (or an administrator) may remove it; else the sentence saying why
    not and how to repair it. `problems` is ohsatv.lockacl.folder_problems,
    replaceable in tests."""
    from . import lockacl  # noqa: PLC0415 - ctypes on Windows; only wanted here
    path = Path(path) if path is not None else lock_dir()
    why = (problems or lockacl.folder_problems)(path)
    if not why:
        return ""
    head = (f"The radio lock's folder {path} cannot be trusted: " + "; ".join(why) + ". "
            f"Until it is repaired {NAME} takes no shared radio, because another account "
            f"could remove this program's lock while it transmits")
    if windows():
        script = lock_repair_script()
        fix = (f'Repair it by running the {NAME} installer again, or from an administrator '
               f'command prompt: "{script}"' if script else
               f"Repair it by running the {NAME} installer again (it sets the folder's permissions)")
    elif macos():
        fix = (f"macOS makes {path} root's with mode 1777; restore that "
               f"(sudo chown root:wheel {path} && sudo chmod 1777 {path})")
    else:
        fix = ("Debian and Ubuntu make /run/lock root's with mode 1777 at boot; "
               "restore that (sudo chown root:root /run/lock && sudo chmod 1777 /run/lock)")
    return f"{head}. {fix}"


def boot_id() -> str:
    """Something that changes at every boot of this computer and nothing
    else (the radio lock's bootId).

    Linux: the kernel's boot_id. Windows: the BootId counter the kernel
    keeps in the registry, as "win-<n>" - DATV's reading, so a marker one
    OpenHamShack program writes is judged the same way by another; '' if it
    cannot be read, which the lock treats as unknown (age alone decides)."""
    if windows():
        try:
            import winreg  # noqa: PLC0415 - Windows only
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management"
                                r"\PrefetchParameters") as k:
                value, _ = winreg.QueryValueEx(k, "BootId")
            return f"win-{int(value)}"
        except (OSError, ValueError, TypeError, ImportError):
            return ""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def pid_alive(pid) -> bool:
    """Whether a process of this number is running here. Unknown (not ours
    to judge, or not a number) counts as alive: the lock then waits for the
    marker to age out instead of taking a radio someone may still hold.

    Never os.kill(pid, 0) on Windows: there it is TerminateProcess with exit
    code 0, so asking whether a lock holder is alive would kill it."""
    try:
        pid = int(pid)
    except (ValueError, TypeError, OverflowError):
        return True
    if pid <= 0:
        return True
    if windows():
        return _win_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError):
        return True
    return True


def _win_pid_alive(pid: int) -> bool:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        # ERROR_INVALID_PARAMETER: no such process. Anything else (access
        # denied: another user's) is a process we cannot judge.
        return ctypes.get_last_error() != 87
    try:
        code = ctypes.c_uint32()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return True
        return code.value == 259                       # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


# ------------------------------------------------------------------ programs and libraries
def find_tool(name: str, dirs: list[Path] | None = None) -> str | None:
    """ffmpeg or ffprobe: on Windows the copy the installer carries
    (_internal\\ffmpeg) before PATH, so a different ffmpeg the operator has
    installed - one without dshow, or an old one - is never the one used by
    accident; elsewhere PATH alone, as before. None when there is none."""
    if windows():
        exe = f"{name}.exe"
        d = bundled(FFMPEG_SUBDIR, exe, dirs)
        if d is not None:
            return str(d / exe)
    return shutil.which(name)


@contextlib.contextmanager
def dll_search(subdir: str, marker: str, dirs: list[Path] | None = None):
    """Make a library whose Python binding finds its DLL by walking PATH
    (ctypes.util.find_library on Windows does nothing else) load the copy
    in the bundle's `subdir`: the folder goes in front of PATH for the
    length of the with-block - the import - and is taken off again.

    libiio's iio.py finds libiio.dll this way. With the folder first, the
    bundle's 0.26 wins over one in System32 (ADI's libiio 1.0 installer puts
    a 1.0 DLL there that the 0.2x bindings cannot use), and Windows resolves
    that DLL's own imports from the folder it was loaded from first, so the
    whole set comes from the bundle. PATH is put back afterwards so children
    (ffmpeg) never inherit our DLL folders.

    Yields the folder, or None when there is no bundled copy (Linux, a run
    from source) - then nothing changes and the system's library is used."""
    d = bundled(subdir, marker, dirs)
    if d is None:
        yield None
        return
    old = os.environ.get("PATH")
    os.environ["PATH"] = str(d) + (os.pathsep + old if old else "")
    try:
        yield d
    finally:
        if old is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = old


def bundled_libiio(dirs: list[Path] | None = None):
    return dll_search(LIBIIO_SUBDIR, "libiio.dll", dirs)


# The names libhackrf goes by on Windows: conda-forge's (and so radioconda's)
# hackrf-0.dll, the one the installer carries; hackrf.dll, PothosSDR's and
# a CMake build's own; libhackrf.dll, a MinGW build's.
HACKRF_DLLS = ("hackrf-0.dll", "hackrf.dll", "libhackrf.dll")


def hackrf_library(dirs: list[Path] | None = None, find=None) -> list[str]:
    """What to hand ctypes for libhackrf, in the order to try.

    Linux: exactly what the driver always did - the system's own
    (find_library) or the Debian soname. Windows: the bundle's
    _internal\\hackrf\\hackrf-0.dll by full path first; Python's loader
    (3.8+) then resolves its imports - libusb-1.0.dll and
    libwinpthread-1.dll - from that same folder. Then each name on PATH,
    for a run from source with radioconda or PothosSDR installed.

    A full path matters for one more reason: the libiio set carries a
    libusb-1.0.dll too (1.0.24, in _internal\\libiio). Windows binds an
    import by name to a DLL already loaded under that name, so whichever of
    the two libraries loads first, both use its libusb; every call
    hackrf-0.dll imports exists in both versions (checked against its
    import table when it was pinned), so either order works."""
    find = find if find is not None else _find_library
    if not windows():
        return [find("hackrf") or "libhackrf.so.0"]
    out = []
    for name in HACKRF_DLLS:
        d = bundled(HACKRF_SUBDIR, name, dirs)
        if d is not None:
            out.append(str(d / name))
    for name in HACKRF_DLLS:
        found = find(name[:-4])
        if found and found not in out:
            out.append(found)
    return out


def _find_library(name: str) -> str | None:
    import ctypes.util  # noqa: PLC0415
    return ctypes.util.find_library(name)


def no_console() -> dict:
    """subprocess keyword arguments that stop a console window flashing up
    for each child (ffmpeg per source, ffprobe per file): the Windows
    build's window is a GUI program, so every console child would get a
    window of its own. Nothing on Linux."""
    if windows():
        return {"creationflags": 0x08000000}        # CREATE_NO_WINDOW
    return {}


# ------------------------------------------------------------------ sentences
# One sentence per optional piece, in the vocabulary of the system it is
# read on: an apt command means nothing on Windows. The Windows installer
# carries ffmpeg, libiio 0.26 and libhackrf itself, so reaching one of these
# there means a damaged install or a run from source.
_HINTS = {
    "ffmpeg": ("The webcam and file sources need ffmpeg (sudo apt install ffmpeg)",
               f"The webcam and file sources need ffmpeg, which is missing from this installation: reinstall "
               f"{NAME} (running from source, put ffmpeg.exe on PATH)"),
    "libiio": ("Pluto support needs the libiio Python bindings: sudo apt install python3-libiio",
               f"Pluto support needs libiio 0.26, which the Windows installer carries: reinstall {NAME} "
               "(running from source, install ADI's libiio 0.26, not 1.0)"),
    "libhackrf": ("The HackRF needs libhackrf: sudo apt install libhackrf0",
                  f"The HackRF needs libhackrf (hackrf-0.dll), which is missing from this installation: "
                  f"reinstall {NAME} (running from source, install radioconda or put hackrf-0.dll on PATH)"),
    "libhackrf-scan": ("libhackrf is not installed (sudo apt install libhackrf0)",
                       f"libhackrf (hackrf-0.dll) is missing from this installation; reinstall {NAME}"),
    "hackrf-none": ("is it plugged in, and does hackrf_info see it?",
                    "is it plugged in? Device Manager should show it as HackRF One under Universal Serial Bus "
                    "devices; under Other devices it has no driver, which a PortaPack's firmware may need "
                    "installed with Zadig (WinUSB)"),
    "avahi": ("network discovery needs avahi-daemon (sudo apt install avahi-daemon)",
              "network discovery by name (mDNS) did not answer"),
    "iio_info": ("iio_info -s lists what is found", "Find in Settings > Radio lists what is found"),
    "pluto-usb": ("", " On Windows, a Pluto on USB needs ADI's PlutoSDR drivers: run the installer again "
                      "with \"Install the PlutoSDR USB drivers\" ticked."),
    "webcam-none": ("No V4L2 capture device found (is a webcam plugged in?).",
                    "No camera found (is a webcam plugged in, and allowed in Settings > Privacy > Camera?)."),
}


def hint(what: str) -> str:
    linux, win = _HINTS[what]
    return win if windows() else linux

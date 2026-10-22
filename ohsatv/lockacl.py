"""Whether the radio lock's folder on this computer can be trusted
(docs/radio-lock.md, "The folder must be the system's").

Copied unchanged from openhamshack-datv d3c62d6 (ohsdatv/lockacl.py), as the spec
asks of every OpenHamShack program: the folder is shared by all of them, so all
of them must judge it by the same rules. A change belongs in DATV's copy
first, then here. ("docs/radio-lock.md" below is OpenHamShack DATV's.)

The lock's promise on one computer is that every user may create a marker
but only its owner (or an administrator) may remove or change it - so a
stale marker another account left is reported, never quietly taken. Linux
gets that from /run/lock: root's, mode 1777, the sticky bit. Windows has no
sticky bit; the same rule is an ACL on %ProgramData%\\openhamshack-radio, and it
holds only if the folder carries that ACL. OpenHamShack Desktop showed on VM 130
(6 October 2026) that a folder a standard user created inherits
ProgramData's defaults - that user its owner, with Full Control through
CREATOR OWNER - and that user then deleted an administrator's marker. So
the lock reads the folder's security before it trusts it, and refuses to
take a shared radio from a folder that fails, saying how to repair it. It
never falls back to a folder of its own: a per-user folder is invisible to
the other users, which is the failure the lock exists to prevent.

The judging is pure (judge_sddl, judge_posix) so it is tested on Linux
with fabricated descriptors; reading a real one (folder_sddl, account) is
ctypes on advapi32, because the Windows build does not carry pywin32 and
the lock is meant to be copied into the other OpenHamShack programs stdlib-only.

Rules for the Windows folder, each a way an ordinary user could remove or
rewrite someone else's marker:
- the owner is Administrators or SYSTEM (an owner can always rewrite the
  ACL, whatever it says);
- it is not a link (a junction would make "the folder" somebody's choice;
  folder_problems checks this, the descriptor cannot say it);
- there is a DACL (none at all means everyone has everything);
- nobody but SYSTEM, Administrators and TrustedInstaller is allowed, on the
  folder itself, delete-child, delete, change-permissions or take-ownership
  (Full Control includes all four);
- nobody but those, and CREATOR OWNER / OWNER RIGHTS (the marker's own
  maker), gets by inheritance on the files in it delete, change
  permissions, take ownership, or write data or attributes.
Deny entries are ignored: they can only take rights away, so ignoring them
can make the check refuse a folder that is in fact safe, never trust one
that is not.
"""
from __future__ import annotations

import ctypes
import os
import stat
from dataclasses import dataclass, field

# --------------------------------------------------------------- SIDs
SYSTEM = "S-1-5-18"
ADMINISTRATORS = "S-1-5-32-544"
USERS = "S-1-5-32-545"
TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
CREATOR_OWNER = "S-1-3-0"
OWNER_RIGHTS = "S-1-3-4"

TRUSTED = {SYSTEM, ADMINISTRATORS, TRUSTED_INSTALLER}
OWNERS_OK = {SYSTEM, ADMINISTRATORS}
# Stand-ins for "whoever made this file": allowed anything on the files.
MAKER = {CREATOR_OWNER, OWNER_RIGHTS}

# SDDL's two-letter account aliases (the ones Windows writes for a local
# folder; an unknown alias is kept as written and counts as untrusted).
ALIASES = {
    "SY": SYSTEM, "BA": ADMINISTRATORS, "BU": USERS, "CO": CREATOR_OWNER, "OW": OWNER_RIGHTS,
    "CG": "S-1-3-1", "WD": "S-1-1-0", "AU": "S-1-5-11", "IU": "S-1-5-4", "NU": "S-1-5-2", "AN": "S-1-5-7",
    "BG": "S-1-5-32-546", "PU": "S-1-5-32-547", "AO": "S-1-5-32-548", "SO": "S-1-5-32-549",
    "PO": "S-1-5-32-550", "BO": "S-1-5-32-551", "RD": "S-1-5-32-555", "NO": "S-1-5-32-556",
    "LS": "S-1-5-19", "NS": "S-1-5-20", "SU": "S-1-5-6", "RC": "S-1-5-12", "WR": "S-1-5-33",
    "AC": "S-1-15-2-1", "PS": "S-1-5-10", "ED": "S-1-5-9", "SS": "S-1-18-2", "AS": "S-1-18-1",
}
NAMES = {SYSTEM: "SYSTEM", ADMINISTRATORS: "Administrators", USERS: "Users", CREATOR_OWNER: "CREATOR OWNER",
         OWNER_RIGHTS: "OWNER RIGHTS", "S-1-1-0": "Everyone", "S-1-5-11": "Authenticated Users",
         "S-1-5-4": "INTERACTIVE", TRUSTED_INSTALLER: "TrustedInstaller"}

# --------------------------------------------------------------- rights
DELETE, READ_CONTROL, WRITE_DAC, WRITE_OWNER = 0x10000, 0x20000, 0x40000, 0x80000
FILE_WRITE_DATA, FILE_APPEND_DATA, FILE_DELETE_CHILD, FILE_WRITE_ATTRIBUTES = 0x2, 0x4, 0x40, 0x100
GENERIC_ALL, GENERIC_EXECUTE, GENERIC_WRITE, GENERIC_READ = 0x10000000, 0x20000000, 0x40000000, 0x80000000
FILE_ALL_ACCESS, FILE_GENERIC_READ, FILE_GENERIC_WRITE, FILE_GENERIC_EXECUTE = 0x1F01FF, 0x120089, 0x120116, 0x1200A0

RIGHTS = {
    "GA": GENERIC_ALL, "GR": GENERIC_READ, "GW": GENERIC_WRITE, "GX": GENERIC_EXECUTE,
    "RC": READ_CONTROL, "SD": DELETE, "WD": WRITE_DAC, "WO": WRITE_OWNER,
    "RP": 0x10, "WP": 0x20, "CC": 0x1, "DC": 0x2, "LC": 0x4, "SW": 0x8, "LO": 0x80, "DT": 0x40, "CR": 0x100,
    "FA": FILE_ALL_ACCESS, "FR": FILE_GENERIC_READ, "FW": FILE_GENERIC_WRITE, "FX": FILE_GENERIC_EXECUTE,
    "KA": 0xF003F, "KR": 0x20019, "KW": 0x20006, "KX": 0x20019,
}

# ACE flags
OI, CI, NP, IO, ID = 0x1, 0x2, 0x4, 0x8, 0x10
FLAGS = {"OI": OI, "CI": CI, "NP": NP, "IO": IO, "ID": ID, "SA": 0x40, "FA": 0x80}

# What an untrusted account must not have on the folder, and on the files
# in it.
FOLDER_BAD = {FILE_DELETE_CHILD: "delete anyone's file in it", DELETE: "delete or rename the folder",
              WRITE_DAC: "change its permissions", WRITE_OWNER: "take ownership"}
FILE_BAD = {DELETE: "delete", WRITE_DAC: "change the permissions of", WRITE_OWNER: "take ownership of",
            FILE_WRITE_DATA | FILE_APPEND_DATA: "rewrite", FILE_WRITE_ATTRIBUTES: "touch"}


def generic_to_file(mask: int) -> int:
    """GENERIC_* bits as they mean on a file or folder."""
    out = mask & 0x0FFFFFFF
    if mask & GENERIC_ALL:
        out |= FILE_ALL_ACCESS
    if mask & GENERIC_READ:
        out |= FILE_GENERIC_READ
    if mask & GENERIC_WRITE:
        out |= FILE_GENERIC_WRITE
    if mask & GENERIC_EXECUTE:
        out |= FILE_GENERIC_EXECUTE
    return out


@dataclass
class Ace:
    kind: str           # "A" allow, "D" deny, ...
    flags: int
    mask: int
    sid: str


@dataclass
class Descriptor:
    owner: str = ""
    dacl_present: bool = False
    protected: bool = False
    aces: list = field(default_factory=list)


def _sid(text: str) -> str:
    text = text.strip()
    return ALIASES.get(text, text) if not text.upper().startswith("S-") else text.upper()


def _rights(text: str) -> int:
    text = text.strip()
    if text.lower().startswith("0x"):
        return int(text, 16)
    if text.isdigit():
        return int(text)
    mask = 0
    for i in range(0, len(text), 2):
        tok = text[i:i + 2]
        if tok not in RIGHTS:
            raise ValueError(f"unknown right {tok!r} in SDDL")
        mask |= RIGHTS[tok]
    return mask


def _flags(text: str) -> int:
    out = 0
    for i in range(0, len(text), 2):
        tok = text[i:i + 2]
        if tok not in FLAGS:
            raise ValueError(f"unknown ACE flag {tok!r} in SDDL")
        out |= FLAGS[tok]
    return out


def parse_sddl(sddl: str) -> Descriptor:
    """The owner and DACL of a security descriptor in SDDL (what
    ConvertSecurityDescriptorToStringSecurityDescriptor writes). The SACL
    and the group are read past, not kept."""
    d = Descriptor()
    s = sddl.strip()
    i = 0
    while i < len(s):
        part, i = s[i:i + 2], i + 2
        end = len(s)
        for tag in ("O:", "G:", "D:", "S:"):
            j = s.find(tag, i)
            # A tag starts a new part only outside an ACE's parentheses.
            while j != -1 and s.count("(", i, j) != s.count(")", i, j):
                j = s.find(tag, j + 1)
            if j != -1:
                end = min(end, j)
        body, i = s[i:end], end
        if part == "O:":
            d.owner = _sid(body)
        elif part == "D:":
            d.dacl_present = not body.startswith("NO_ACCESS_CONTROL")
            head = body.split("(", 1)[0]
            d.protected = "P" in head.replace("AI", "").replace("AR", "")
            for ace in body[len(head):].split(")"):
                ace = ace.lstrip("(")
                if not ace:
                    continue
                f = ace.split(";")
                if len(f) < 6:
                    raise ValueError(f"malformed ACE ({ace}) in SDDL")
                d.aces.append(Ace(f[0], _flags(f[1]), _rights(f[2]), _sid(f[5])))
        elif part not in ("G:", "S:"):
            raise ValueError(f"malformed SDDL near {part!r}")
    return d


def name(sid: str, lookup=None) -> str:
    if sid in NAMES:
        return NAMES[sid]
    if lookup is not None:
        try:
            got = lookup(sid)
            if got:
                return got
        except OSError:
            pass
    return sid


def judge_sddl(sddl: str, lookup=None) -> list[str]:
    """[] when a folder with this descriptor keeps the sticky-bit rule,
    else each reason it does not, as phrases for a sentence."""
    d = parse_sddl(sddl)
    why: list[str] = []
    if d.owner not in OWNERS_OK:
        why.append(f"it is owned by {name(d.owner, lookup)}, not Administrators or SYSTEM, so that account can "
                   f"rewrite its permissions")
    if not d.dacl_present:
        why.append("it has no access list at all, so everyone may do anything in it")
    folder: dict[str, list[str]] = {}
    files: dict[str, list[str]] = {}
    for ace in d.aces:
        if ace.kind not in ("A", "XA", "ZA") or ace.sid in TRUSTED or ace.sid in MAKER:
            continue
        mask = generic_to_file(ace.mask)
        who = name(ace.sid, lookup)
        if not ace.flags & IO:
            got = folder.setdefault(who, [])
            got += [what for bit, what in FOLDER_BAD.items() if mask & bit and what not in got]
        if ace.flags & OI:
            got = files.setdefault(who, [])
            got += [what for bit, what in FILE_BAD.items() if mask & bit and what not in got]
    for who, whats in folder.items():
        if whats:
            why.append(f"{who} may {_and(whats)}")
    for who, whats in files.items():
        if whats:
            why.append(f"{who} may {_and(whats)} other accounts' markers")
    return why


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def judge_posix(uid: int, mode: int) -> list[str]:
    """The same rule on Linux: /run/lock is root's and, being world
    writable, sticky (Debian: root, 1777). A directory others may write
    without the sticky bit lets any of them delete any marker."""
    why = []
    if uid != 0:
        why.append(f"it is owned by uid {uid}, not root")
    if mode & (stat.S_IWOTH | stat.S_IWGRP) and not mode & stat.S_ISVTX:
        why.append(f"it is writable by others without the sticky bit (mode {stat.S_IMODE(mode):04o})")
    return why


# ------------------------------------------------------------ Windows reads
def _advapi():
    a = ctypes.WinDLL("advapi32", use_last_error=True)
    a.GetNamedSecurityInfoW.restype = ctypes.c_uint32
    a.GetNamedSecurityInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_uint32,
                                        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.POINTER(ctypes.c_void_p)]
    a.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(ctypes.c_uint32)]
    a.ConvertStringSidToSidW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)]
    a.LookupAccountSidW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_wchar_p,
                                    ctypes.POINTER(ctypes.c_uint32), ctypes.c_wchar_p,
                                    ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_int)]
    return a


def _local_free(p) -> None:
    k32 = ctypes.WinDLL("kernel32")
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    k32.LocalFree(p)


def path_sddl(path: str | os.PathLike) -> str:
    """The owner and DACL of a file or folder, as SDDL (Windows)."""
    a = _advapi()
    sd = ctypes.c_void_p()
    info = 0x1 | 0x4                      # OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION
    rc = a.GetNamedSecurityInfoW(str(path), 1, info, None, None, None, None, ctypes.byref(sd))  # SE_FILE_OBJECT
    if rc:
        raise ctypes.WinError(rc)
    try:
        out = ctypes.c_wchar_p()
        if not a.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, info, ctypes.byref(out), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return out.value or ""
        finally:
            _local_free(out)
    finally:
        _local_free(sd)


def account(sid: str) -> str:
    """DOMAIN\\name for a SID string, or the SID itself (Windows)."""
    a = _advapi()
    p = ctypes.c_void_p()
    if not a.ConvertStringSidToSidW(sid, ctypes.byref(p)):
        return sid
    try:
        n, dn = ctypes.c_uint32(256), ctypes.c_uint32(256)
        nb, db, use = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256), ctypes.c_int()
        if not a.LookupAccountSidW(None, p, nb, ctypes.byref(n), db, ctypes.byref(dn), ctypes.byref(use)):
            return sid
        return f"{db.value}\\{nb.value}" if db.value else nb.value
    finally:
        _local_free(p)


def owner_of(path: str | os.PathLike) -> str:
    """Who owns a file, as an operator would name them: the account on
    Windows, the login on Linux; '' when it cannot be told."""
    try:
        if os.name == "nt":
            return name(parse_sddl(path_sddl(path)).owner, account)
        import pwd  # noqa: PLC0415 - POSIX only
        uid = os.stat(path).st_uid
        try:
            return pwd.getpwuid(uid).pw_name
        except KeyError:
            return f"uid {uid}"
    except (OSError, ValueError):
        return ""


def folder_problems(path: str | os.PathLike) -> list[str]:
    """The reasons this computer's lock folder cannot be trusted, [] when
    it can: the ACL rules on Windows, root-and-sticky on Linux."""
    p = os.fspath(path)
    # lstat, not isdir: a junction must be named as one, and Windows'
    # redirection guard makes isdir() of a user-made junction say False
    # (WinError 448), which would read as "does not exist".
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return ["it does not exist"]
    except OSError as e:
        return [f"it could not be examined ({e})"]
    if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:   # FILE_ATTRIBUTE_REPARSE_POINT
        return ["it is a link to another folder, not a folder"]
    if not stat.S_ISDIR(st.st_mode):
        return ["it is not a folder"]
    if os.name == "nt":
        try:
            sddl = path_sddl(p)
        except OSError as e:
            return [f"its permissions could not be read ({e})"]
        try:
            return judge_sddl(sddl, lookup=account)
        except ValueError as e:
            return [f"its permissions could not be understood ({e}): {sddl}"]
    return judge_posix(st.st_uid, st.st_mode)

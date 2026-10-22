"""The licence texts of the Python packages a Windows bundle carries, and
the check that licences/ and THIRD-PARTY.txt agree.

Copied from OpenHamShack NB (~/openhamshack-nb/packaging/windows/bundle_licences.py,
GPLv3, as of 08/10/2026), where the HAM session found the gap it closes.
Changed here: one program, so no "OpenHamShack NB only" / "OpenHamShack HAM only"
paragraphs - `references` takes the app name only to keep NB's signature,
and every name THIRD-PARTY.txt gives is required.

Why. The C libraries' licences have always been fetched into licences/ by
tools/build-windows.ps1, but the packages pip installs into the build
virtualenv - NumPy, PyQt6 (and the Qt and sip wheels under it), PyAudio,
pySerial, cryptography and cffi, and for HAM Paramiko with bcrypt, PyNaCl and
pycparser - went into the bundle as code only. THIRD-PARTY.txt said "the
texts are in licences/" and for those they were not, while their BSD, MIT,
Apache and LGPL licences all require the text to travel with the binaries.
Found by the HAM session, 06/10/2026.

How. After Analysis, each spec asks which installed distributions own the
top-level modules and extension modules PyInstaller actually collected
(a.pure, a.binaries) - so a dependency that arrives by a hook, or a new one
pip resolves, is found without anyone keeping a list - plus PyInstaller
itself, whose bootloader is in every .exe and is in no TOC. Each one's
licence files (LICENSE*, LICENCE*, COPYING*, NOTICE*, AUTHORS*: those in
its .dist-info when it has any there, as wheels following PEP 639 put them;
otherwise wherever its RECORD has them) go into licences/ as
<distribution>-<file>, a path inside .dist-info/licenses/ flattened with
dashes. Python's own LICENSE.txt goes in as python-LICENSE.txt. A
distribution that has no licence file at all (pySerial 3.5's wheel has
none) must have one fetched into the vendor licences folder under its name
instead (build-windows.ps1 does that for pySerial); otherwise the build
stops.

Then licences/ and THIRD-PARTY.txt must agree, both ways: every
"licences/<name>" THIRD-PARTY.txt names (a trailing * matches any rest of
a name) must be in the bundle, and every file in the bundle's licences/
must be named by THIRD-PARTY.txt. The first catches a text promised and not
shipped; the second, a package that arrived without an entry saying what
it is and under which licence.

A plain module beside the specs, as bundle_prune.py is, so the tests run
the same code the build runs, against stand-in distributions.
"""
from __future__ import annotations

import importlib.metadata as md
import re
from pathlib import Path, PurePosixPath

LICENCE_NAME = re.compile(r"^(licen[cs]e|copying|notice|authors)", re.IGNORECASE)
REFERENCE = re.compile(r"licences/([A-Za-z0-9._+*-]+[A-Za-z0-9_+*-])")
# In every bundle whatever Analysis found: its bootloader is each .exe.
ALWAYS = ("pyinstaller",)


class LicenceError(Exception):
    """The bundle's licence texts are incomplete; the message says what to do."""


def _norm(dest: str) -> str:
    return dest.replace("\\", "/")


def _top_levels(dist: md.Distribution) -> set[str]:
    """The importable top-level names a distribution installs, as
    importlib.metadata.packages_distributions works them out."""
    text = dist.read_text("top_level.txt")
    if text:
        return {line.strip() for line in text.splitlines() if line.strip()}
    names = set()
    for f in dist.files or ():
        first = f.parts[0] if f.parts else ""
        if not first or first.endswith((".dist-info", ".egg-info", ".data")) or first in ("..", "__pycache__"):
            continue
        names.add(first.split(".")[0] if len(f.parts) == 1 else first)
    return names


def collected_top_levels(pure, binaries) -> set[str]:
    """Top-level module names in the bundle: every Python module, and every
    file collected from inside a package (an extension module, a Qt DLL)."""
    names = {entry[0].split(".")[0] for entry in pure}
    for dest, _src, _typecode in binaries:
        first = _norm(dest).split("/")[0]
        names.add(first.split(".")[0])
    return names


def bundled_distributions(pure, binaries, path=None) -> list[md.Distribution]:
    """The installed distributions (searched on `path`, default sys.path)
    whose code is in the bundle, plus ALWAYS; sorted by name, one each."""
    wanted = collected_top_levels(pure, binaries)
    found: dict[str, md.Distribution] = {}
    for dist in md.distributions(**({"path": path} if path is not None else {})):
        name = dist.metadata["Name"]
        if not name or name.lower() in found:
            continue
        if name.lower() in ALWAYS or _top_levels(dist) & wanted:
            found[name.lower()] = dist
    return [found[k] for k in sorted(found)]


def licence_files(dist: md.Distribution) -> list[tuple[str, Path]]:
    """(name in licences/, source path) for each licence text the
    distribution installed."""
    hits = [f for f in dist.files or () if LICENCE_NAME.match(f.name)]
    in_info = [f for f in hits if f.parts and f.parts[0].endswith(".dist-info")]
    name = dist.metadata["Name"]
    out = []
    for f in in_info or hits:
        rel = PurePosixPath(*f.parts[1:]) if in_info else PurePosixPath(*f.parts)
        if in_info and rel.parts and rel.parts[0] == "licenses" and len(rel.parts) > 1:
            rel = PurePosixPath(*rel.parts[1:])
        out.append((f"{name}-{'-'.join(rel.parts)}", Path(dist.locate_file(f))))
    return sorted(set(out))


def licence_datas(pure, binaries, datas, python_licence: Path, path=None) -> list[tuple[str, str, str]]:
    """TOC entries (dest, source, "DATA") putting each bundled
    distribution's licence texts, and Python's, into licences/. `datas` is
    the TOC so far, to see which texts the vendor folder already supplies."""
    present = {_norm(e[0]).split("/", 1)[1] for e in datas if _norm(e[0]).startswith("licences/")}
    if not python_licence.is_file():
        raise LicenceError(f"Python's licence is not at {python_licence}")
    out = [("licences/python-LICENSE.txt", str(python_licence), "DATA")]
    missing = []
    for dist in bundled_distributions(pure, binaries, path):
        files = licence_files(dist)
        name = dist.metadata["Name"]
        if not files and not any(p.startswith(f"{name}-") for p in present):
            missing.append(f"{name} {dist.version}")
        out += [(f"licences/{dest}", str(src), "DATA") for dest, src in files]
    if missing:
        raise LicenceError(
            "no licence text for " + ", ".join(missing) + ": the wheel carries none, so fetch the text from that "
            "release (pinned, in tools/build-windows.ps1) into the vendor licences folder as <name>-<file>, "
            "and name it in THIRD-PARTY.txt")
    return out


def references(third_party: str, app: str = "atv") -> tuple[list[str], list[str]]:
    """(required, allowed): the licences/ names THIRD-PARTY.txt gives. One
    program, so the two are the same list: every text promised must ship."""
    refs = REFERENCE.findall(third_party)
    return refs, list(refs)


def disagreements(third_party: str, app: str, toc) -> list[str]:
    """What is wrong between THIRD-PARTY.txt and the TOC's licences/
    folder, as sentences; empty when they agree."""
    shipped = sorted({_norm(e[0]).split("/", 1)[1] for e in toc if _norm(e[0]).startswith("licences/")})
    required, allowed = references(third_party, app)

    def matches(pattern: str, name: str) -> bool:
        return name == pattern or (pattern.endswith("*") and name.startswith(pattern[:-1]))

    problems = [f"THIRD-PARTY.txt names licences/{r}, which is not in the bundle"
                for r in required if not any(matches(r, s) for s in shipped)]
    problems += [f"licences/{s} is in the bundle but THIRD-PARTY.txt does not name it - add an entry for what it "
                 f"covers (name, version, licence, URL)" for s in shipped if not any(matches(a, s) for a in allowed)]
    return problems


def with_licences(a, app: str, third_party: Path, python_licence: Path, path=None) -> list:
    """For the specs: a.datas plus the packages' licence texts, after
    checking the result against THIRD-PARTY.txt. Raises LicenceError,
    which stops PyInstaller, when they disagree."""
    datas = list(a.datas) + licence_datas(a.pure, a.binaries, a.datas, python_licence, path)
    problems = disagreements(third_party.read_text(encoding="utf-8"), app, datas)
    if problems:
        raise LicenceError("licences/ and THIRD-PARTY.txt disagree:\n  " + "\n  ".join(problems))
    return datas

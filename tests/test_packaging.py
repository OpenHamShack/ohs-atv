"""Packaging tests: build the real .deb with tools/build-deb.sh and check
what it actually does, never what its scripts say. The .deb is a build
artefact of a shell script, not ohsatv, so these drive dpkg-deb and the
installed launcher as a user would -- no assertion here reads this repo's
own source text."""
import os
import sys
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

# The .deb is built with dpkg-deb on Debian. Elsewhere (the Windows build
# runs this suite under Windows Python) there is nothing to build it with,
# which says nothing about the package, so the module is skipped, not failed.
if shutil.which("dpkg-deb") is None:
    raise unittest.SkipTest("dpkg-deb not installed: the .deb is built and checked on Debian")

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = REPO_ROOT / "tools" / "build-deb.sh"
VERSION = (REPO_ROOT / "ohsatv" / "VERSION").read_text().strip()


def build(tmpdir, version=None):
    """Run build-deb.sh with tmpdir as cwd, so the .deb lands there and
    never in the repo. Returns the built .deb's path."""
    args = [str(BUILD_SCRIPT)]
    if version:
        args.append(version)
    subprocess.run(args, cwd=tmpdir, check=True, capture_output=True, text=True)
    v = version or VERSION
    deb = Path(tmpdir) / f"openhamshack-atv_{v}_all.deb"
    assert deb.is_file(), f"build-deb.sh did not produce {deb}"
    return deb


def field(deb, name):
    return subprocess.run(["dpkg-deb", "-f", str(deb), name],
                           check=True, capture_output=True, text=True).stdout.strip()


def contents(deb):
    out = subprocess.run(["dpkg-deb", "-c", str(deb)],
                          check=True, capture_output=True, text=True).stdout
    return [line.split()[-1] for line in out.splitlines()]


class BuildsCleanly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_version_comes_from_ohsatv_version_file(self):
        deb = build(self.tmp.name)
        self.assertEqual(field(deb, "Version"), VERSION)

    def test_explicit_version_argument_overrides_the_file(self):
        deb = build(self.tmp.name, version="9.9.9")
        self.assertEqual(deb.name, "openhamshack-atv_9.9.9_all.deb")
        self.assertEqual(field(deb, "Version"), "9.9.9")

    def test_leaves_no_artefact_in_the_repo(self):
        build(self.tmp.name)
        self.assertEqual(list(REPO_ROOT.glob("*.deb")), [])
        self.assertEqual(list((REPO_ROOT / "tools").glob("*.deb")), [])

    def test_rebuilding_does_not_fail(self):
        # dpkg-deb --build must not choke on a pre-existing output file,
        # and the script's own temp workdir must not collide with itself.
        build(self.tmp.name)
        build(self.tmp.name)


class ControlFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.deb = build(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_identity(self):
        self.assertEqual(field(self.deb, "Package"), "openhamshack-atv")
        self.assertEqual(field(self.deb, "Architecture"), "all")

    def test_installed_size_is_a_plausible_positive_number(self):
        # The payload is a Python package plus a handful of small assets;
        # bound it loosely so a future ohsatv/ growth spurt doesn't need
        # this test touched, but a build that forgot to copy ohsatv/ (or
        # one that accidentally included a stray venv) would trip it.
        kib = int(field(self.deb, "Installed-Size"))
        self.assertGreater(kib, 50)
        self.assertLess(kib, 50_000)

    def test_depends_names_the_hard_requirements(self):
        depends = field(self.deb, "Depends")
        for pkg in ("python3 (>= 3.11)", "python3-numpy", "python3-pyqt6", "python3-pyaudio", "python3-pil"):
            self.assertIn(pkg, depends)

    def test_recommends_are_optional_extras_not_depends(self):
        depends = field(self.deb, "Depends")
        recommends = field(self.deb, "Recommends")
        for pkg in ("python3-libiio", "avahi-daemon", "ffmpeg", "libhackrf0"):
            self.assertIn(pkg, recommends)
            self.assertNotIn(pkg, depends)

    def test_maintainer_scripts_are_present_and_executable(self):
        info = subprocess.run(["dpkg-deb", "-I", str(self.deb)],
                               check=True, capture_output=True, text=True).stdout
        self.assertIn("postinst", info)
        self.assertIn("postrm", info)


class PackageContents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.deb = build(cls.tmp.name)
        cls.listing = contents(cls.deb)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_ships_the_launcher_and_the_engine_package(self):
        self.assertIn("./usr/bin/openhamshack-atv", self.listing)
        self.assertIn("./usr/lib/openhamshack-atv/ohsatv/radio.py", self.listing)
        self.assertIn("./usr/lib/openhamshack-atv/ohsatv/__main__.py", self.listing)

    def test_ships_the_teletext_editor_with_its_catalog(self):
        self.assertIn("./usr/lib/openhamshack-atv/ohstxt/__main__.py", self.listing)
        self.assertIn("./usr/lib/openhamshack-atv/ohstxt/ui/mainwindow.py", self.listing)
        self.assertIn("./usr/lib/openhamshack-atv/ohstxt/i18n/ohstxt_en.qm", self.listing)

    def test_ships_desktop_icon_metainfo_and_man_page(self):
        self.assertIn("./usr/share/applications/openhamshack-atv.desktop", self.listing)
        self.assertIn("./usr/share/icons/hicolor/scalable/apps/openhamshack-atv.svg", self.listing)
        self.assertIn("./usr/share/metainfo/openhamshack-atv.metainfo.xml", self.listing)
        self.assertIn("./usr/share/man/man1/openhamshack-atv.1.gz", self.listing)
        self.assertIn("./usr/share/doc/openhamshack-atv/copyright", self.listing)

    def test_does_not_duplicate_the_udev_rule_libiio_already_ships(self):
        # libiio0 already installs /usr/lib/udev/rules.d/90-libiio0.rules
        # on this distro family ; shipping a second
        # one here would be redundant at best and a conflicting-rule bug
        # at worst, so the package must carry none of its own.
        self.assertFalse(any("udev" in p for p in self.listing))

    def test_ships_no_compiled_or_cache_artefacts(self):
        self.assertFalse(any("__pycache__" in p for p in self.listing))
        self.assertFalse(any(p.endswith((".pyc", ".pyo")) for p in self.listing))

    def test_payload_files_are_world_readable(self):
        out = subprocess.run(["dpkg-deb", "-c", str(self.deb)],
                              check=True, capture_output=True, text=True).stdout
        for line in out.splitlines():
            perms = line.split()[0]
            if perms.startswith("-"):  # regular file, not a directory
                self.assertEqual(perms[7], "r", f"not world-readable: {line}")


class ExtractedPackageRuns(unittest.TestCase):
    """The real point of the launcher: it must find ohsatv/ by its own
    location, not by anything baked in at build time, because dpkg can
    extract this package to any root (and because this is exactly what
    an install under a different prefix, or this test, does)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.deb = build(cls.tmp.name)
        cls.extracted = Path(cls.tmp.name) / "root"
        subprocess.run(["dpkg-deb", "-x", str(cls.deb), str(cls.extracted)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _run(self, *extra_args, env_override=None):
        launcher = self.extracted / "usr" / "bin" / "openhamshack-atv"
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        if env_override:
            env.update(env_override)
        # cwd is a bare scratch dir, not the repo and not the extracted
        # root, so nothing but the launcher's own path math can find ohsatv/.
        with tempfile.TemporaryDirectory() as cwd:
            return subprocess.run([str(launcher), *extra_args], cwd=cwd, env=env,
                                   capture_output=True, text=True, timeout=30)

    def test_headless_engine_starts_and_stops_cleanly(self):
        res = self._run("--headless", "--no-audio", "--seconds", "2")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("MHz @", res.stdout)            # a status line
        self.assertIn("stopped", res.stdout)

    def test_never_transmits_in_headless_mode(self):
        # Safety rule #1 (DESIGN.md): never transmits on its own. Headless
        # mode has no PTT source at all, so the keyed marker must never
        # appear in its own status lines.
        res = self._run("--headless", "--no-audio", "--seconds", "2")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("ON AIR", res.stdout)

    def test_the_teletext_editor_runs_from_the_launcher(self):
        # `openhamshack-atv --teletext` is the editor alone (the desktop entry's
        # action): its headless conversion makes a service into packets, so
        # the editor's package is found and runs with no window and no radio.
        with tempfile.TemporaryDirectory() as d:
            svc, out = Path(d) / "svc", Path(d) / "out.t42"
            svc.mkdir()
            (svc / "P100.tti").write_text("PN,10000\nPS,8000\nOL,1,HELLO FROM THE PACKAGE\n")
            res = self._run("--teletext", "--t42", str(out), str(svc))
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(out.stat().st_size % 42, 0)
            self.assertGreater(out.stat().st_size, 0)

    def test_the_shared_libraries_are_found_in_the_installed_tree(self):
        # An installed system has no tools/vendor; ohsatv.shacklibs must find
        # the copies the .deb unpacked beside ohsatv, data and all.
        libdir = self.extracted / "usr" / "lib" / "openhamshack-atv"
        code = ("from ohsatv.shacklibs import shack_lib\n"
                "for n in ('shack_bandplan', 'shack_client', 'shack_pad'):\n"
                "    print(shack_lib(n).__file__)\n")
        env = dict(os.environ, PYTHONPATH=str(libdir), XDG_CACHE_HOME=str(Path(self.tmp.name) / "cache"))
        with tempfile.TemporaryDirectory() as cwd:
            res = subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env,
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        files = res.stdout.split()
        self.assertEqual(len(files), 3)
        for f in files:
            self.assertTrue(Path(f).is_relative_to(libdir), f)
        self.assertTrue((libdir / "shack_bandplan" / "data" / "bandplan" / "manifest.json").is_file())
        self.assertFalse((libdir / "shack_i18n").exists())

    def test_honours_an_unrelated_preexisting_pythonpath(self):
        # The launcher prepends, it must not clobber a PYTHONPATH the
        # environment already set for some other reason.
        marker = str(Path(self.tmp.name) / "not-ohsatv")
        os.makedirs(marker, exist_ok=True)
        res = self._run("--headless", "--no-audio", "--seconds", "1", env_override={"PYTHONPATH": marker})
        self.assertEqual(res.returncode, 0, res.stderr)


class MaintainerScriptsAreOptionalToolSafe(unittest.TestCase):
    """postinst/postrm must exit 0 even when update-desktop-database and
    gtk-update-icon-cache do not exist, which is normal on a minimal or
    container install. Run them for real with a PATH that only has the
    coreutils they need to find out, rather than trusting the script."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.deb = build(cls.tmp.name)
        cls.extracted = Path(cls.tmp.name) / "root"
        cls.extracted.mkdir()
        subprocess.run(["dpkg-deb", "-e", str(cls.deb), str(cls.extracted / "DEBIAN")], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _bare_path(self):
        # A PATH with just enough coreutils for `command -v`, sh and env
        # to work, and deliberately missing update-desktop-database and
        # gtk-update-icon-cache.
        bare = Path(self.tmp.name) / "bare-bin"
        bare.mkdir(exist_ok=True)
        for tool in ("sh", "env", "command"):
            real = shutil.which(tool)
            if real and not (bare / tool).exists():
                os.symlink(real, bare / tool)
        return str(bare)

    def test_postinst_configure_exits_zero_without_optional_tools(self):
        script = self.extracted / "DEBIAN" / "postinst"
        res = subprocess.run([str(script), "configure"],
                              env={"PATH": self._bare_path()}, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_postrm_remove_exits_zero_without_optional_tools(self):
        script = self.extracted / "DEBIAN" / "postrm"
        res = subprocess.run([str(script), "remove"],
                              env={"PATH": self._bare_path()}, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)


if __name__ == "__main__":
    unittest.main()

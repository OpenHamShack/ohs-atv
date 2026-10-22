"""The radio lock: one program on a shared radio at a time (OpenHamShack DATV's
docs/radio-lock.md, version 1; DESIGN.md "Shared radios" for how this program
uses it).

Copied from openhamshack-datv d3c62d6 (ohsdatv/radiolock.py), replacing the copy
of 7daa15e this program had: that one knew only /run/lock, /proc's boot id
and os.kill(pid, 0), and Windows has none of them (os.kill there would
terminate the lock's holder rather than ask about it). DATV's has since
learnt the Windows folder (%ProgramData%\\openhamshack-radio with its ACL,
ohsatv/lockacl.py), the registry's boot id, OpenProcess and paramiko for the
radio's shell - and all the OpenHamShack programs on one PC must agree on those,
or they cannot see each other's markers. Changed here, and only here: the
program's name and package (ohsatv.host stands in for DATV's identity
module) and the environment switch for the over-ssh place
(OHSATV_RADIO_SSH, so the two programs' tests cannot turn each other's off).
A change to the protocol belongs in the spec and in OpenHamShack DATV's copy
first, then here. "DESIGN.md section 17" below is DATV's.

The protocol is the spec's, not this module's: a one-line JSON marker made
by link(2) so exactly one taker wins, kept fresh by touching it every 15 s,
judged stale on the clock of the machine it is on (60 s), or at once when
its holder is a dead process on this very computer. It lives in up to two
places and a program takes every one that applies:

- **on the radio** (`/tmp/openhamshack-radio.lock`, through its own shell over
  ssh) for an AD936x on any of the firmwares, which is what makes the lock
  visible from shack and the laptop alike;
- **on this computer** (`/run/lock/openhamshack-radio.<id>.lock`) for every
  radio, the only place a USB-only tuner has.

Why the over-ssh side is a shell script and not Python on the radio: the
radios have busybox or Debian's sh, and nothing else can be counted on. The
same script runs locally under `sh -c` in the tests, so what the tests
exercise is the protocol the radio sees, not a Python imitation of it.

Stdlib only, and written to be copied (with a "Copied from" line) into the
other OpenHamShack programs, as the spec asks.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path

from . import lockacl
from . import host as hostinfo   # `host` is the radio's address in this module

VERSION = 1
TTL_S = 60.0                     # four missed heartbeats
BEAT_S = 15.0
RADIO_PATH = "/tmp/openhamshack-radio.lock"
HOST_DIR = str(hostinfo.lock_dir())  # /run/lock; macOS /private/tmp; Windows %ProgramData%\openhamshack-radio
SSH_TIMEOUT_S = 6.0


class RadioBusy(Exception):
    """Another program holds the radio; the message is the operator's sentence."""


class FolderUntrusted(RadioBusy):
    """This computer's lock folder does not keep the sticky-bit rule
    (ohsatv/lockacl.py), so no radio is taken until it is repaired."""


class StaleNotOurs(RadioBusy):
    """A stale marker another account on this computer left, which this
    account may not remove (HostPlace.not_ours says whose and what to do)."""


def folder_problem() -> str:
    """'' when this computer's lock folder can be trusted, else the
    sentence (for the engine to say once at start-up, before any radio is
    wanted). '' too when $OHSRADIO_LOCK_DIR names another folder: that is a
    choice made on purpose (the tests' own folder), not a fallback."""
    if os.environ.get("OHSRADIO_LOCK_DIR"):
        return ""
    return hostinfo.lock_dir_problem(Path(HOST_DIR))


def safe_id(text: str) -> str:
    out = "".join(c if c.isalnum() or c in "._-" else "-" for c in str(text).strip().lower())
    return out.strip("-") or "unknown"


def radio_id(kind: str, serial: str | None = None, address: str | None = None) -> str:
    """`<kind>-<serial>` when the serial is known (the nano is ip: to one
    program and usb: to another on the same laptop; only the serial makes
    them one radio), else `net-<address>`."""
    if serial:
        return safe_id(f"{kind}-{serial}")
    if address:
        addr = str(address)
        for prefix in ("ip:", "tcp://", "http://"):
            if addr.startswith(prefix):
                addr = addr[len(prefix):]
        return safe_id(f"net-{addr.split(':')[0] if addr.count(':') == 1 else addr}")
    return safe_id(kind)


# Both answered by ohsatv.host, which knows how each is asked on Windows
# (a registry boot counter; OpenProcess, because os.kill(pid, 0) there
# terminates the process it was meant to ask about).
boot_id = hostinfo.boot_id
pid_alive = hostinfo.pid_alive


def stale_why(marker: dict | None, age_s: float | None, ttl: float = TTL_S,
              host: str | None = None, boot: str | None = None, alive=pid_alive) -> str:
    """'' when the marker is fresh (the radio is busy), else why it is stale
    (the spec's three rules, and an unreadable marker older than the TTL)."""
    host = host if host is not None else socket.gethostname()
    boot = boot if boot is not None else boot_id()
    if marker is None:
        if age_s is not None and age_s > ttl:
            return f"an unreadable marker last touched {age_s:.0f} s ago"
        return ""
    if age_s is not None and age_s > ttl:
        return f"last heard {_ago(age_s)}"
    if marker.get("host") == host:
        if marker.get("bootId") and boot and marker.get("bootId") != boot:
            return "left from before this computer restarted"
        if marker.get("bootId") == boot and not alive(marker.get("pid")):
            return f"its program (pid {marker.get('pid')}) is no longer running"
    return ""


def _ago(s: float) -> str:
    if s < 90:
        return f"{s:.0f} s ago"
    if s < 5400:
        return f"{s / 60:.0f} minutes ago"
    return f"{s / 3600:.1f} hours ago"


def busy_sentence(label: str, marker: dict | None, age_s: float | None, app: str) -> str:
    """The spec's message: who has it, doing what, since when, how fresh,
    and that we touched nothing."""
    if marker is None:
        return (f"{label} is being locked by another program right now (its marker is not readable yet); "
                f"{app} has not touched the radio")
    use = {"tx": "transmitting", "rx": "receiving"}.get(marker.get("use"), marker.get("use") or "using it")
    since = str(marker.get("since") or "")
    when = f", since {since[11:16]} UTC" if len(since) >= 16 else ""
    heard = f", last heard {_ago(age_s)}" if age_s is not None else ""
    note = f": \"{marker['note']}\"" if marker.get("note") else ""
    newer = " (a newer version of the lock than this program knows)" if int(marker.get("v") or 1) > VERSION else ""
    return (f"{label} is in use by {marker.get('app') or 'another program'} on {marker.get('host') or 'another computer'}"
            f"{newer} ({use}{when}{heard}{note}). Close it there first; {app} has not touched the radio")


def _parse(text: str) -> dict | None:
    try:
        m = json.loads(text)
        return m if isinstance(m, dict) else None
    except ValueError:
        return None


def _dump(marker: dict) -> str:
    # The default separators matter: the shell side finds the token as
    # '"token": "<hex>"', so the spacing is part of the protocol.
    return json.dumps(marker) + "\n"


# --------------------------------------------------------------- places
class HostPlace:
    """/run/lock/openhamshack-radio.<id>.lock on this computer.

    `check(dir) -> sentence` judges the folder before every take; by default
    the system's folder is judged (ohsatv.host.lock_dir_problem) and a
    folder named on purpose (`directory`, $OHSRADIO_LOCK_DIR) is not."""

    where = "this computer"

    def __init__(self, rid: str, directory: str | os.PathLike | None = None, check=None):
        self.rid = safe_id(rid)
        chosen = directory if directory is not None else os.environ.get("OHSRADIO_LOCK_DIR")
        self.dir = Path(chosen if chosen else HOST_DIR)
        self.path = self.dir / f"openhamshack-radio.{self.rid}.lock"
        self.check = check if check is not None else (None if chosen else hostinfo.lock_dir_problem)

    def read(self) -> tuple[dict | None, float | None, bool]:
        """(marker, age seconds, exists)."""
        try:
            st = self.path.stat()
            text = self.path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return None, None, False
        return _parse(text), max(0.0, time.time() - st.st_mtime), True

    def trust(self) -> None:
        """Refuse (FolderUntrusted) a folder in which another account could
        remove our marker. Never a fallback to a folder of our own: the
        other users would not see it, which is what the lock is for."""
        if self.check is not None:
            why = self.check(self.dir)
            if why:
                raise FolderUntrusted(why)

    def take(self, marker: dict) -> bool:
        self.trust()
        tmp = self.path.with_name(self.path.name + "." + marker["token"])
        if self.check is None and not self.dir.is_dir():
            # A folder named on purpose (the tests'). The system's folder is
            # never made here: on Windows the installer makes it with its ACL,
            # and one made by an ordinary user would be that user's.
            self.dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(_dump(marker), encoding="utf-8")
        try:
            os.chmod(tmp, 0o644)
            os.link(tmp, self.path)
            return True
        except FileExistsError:
            return False
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def take_over(self, old_token: str | None, marker: dict, ttl: float) -> bool:
        self.trust()
        cur, age, exists = self.read()
        if exists:
            if old_token is not None and (cur or {}).get("token") != old_token:
                return False
            if old_token is None and cur is not None and (age or 0) <= ttl:
                return False
            try:
                os.rename(self.path, self.path.with_name(self.path.name + ".stale." + marker["token"]))
            except FileNotFoundError:
                pass
            except PermissionError:
                raise StaleNotOurs(self.not_ours(cur, age)) from None
            try:
                os.unlink(self.path.with_name(self.path.name + ".stale." + marker["token"]))
            except OSError:
                pass
        return self.take(marker)

    def not_ours(self, cur: dict | None, age: float | None) -> str:
        """A stale marker another account on this computer left: the folder
        (sticky on Linux, its ACL on Windows) lets only that account or an
        administrator remove it. Say whose it is, since when, and the two
        ways out; never work around it."""
        m = cur or {}
        # The marker's `user` is the login; the file's owner says the same
        # thing unless an elevated program made it (Windows then records
        # the Administrators group as the owner), which is worth saying.
        owner = lockacl.owner_of(self.path)
        acct = str(m.get("user") or "") or owner or "another account"
        if owner and owner.split("\\")[-1].lower() != acct.lower():
            acct += f", file owned by {owner}"
        since = str(m.get("since") or "")
        bits = [f"account {acct}"]
        if len(since) >= 16:
            bits.append(f"since {since[:10]} {since[11:16]} UTC")
        if age is not None:
            bits.append(f"last heard {_ago(age)}")
        who = f"{m.get('app') or 'a program'} on {m.get('host') or 'this computer'}" if cur else "a program"
        cmd = " ".join(f'"{c}"' if " " in c else c for c in hostinfo.self_command()) + f" --clear-stale-lock {self.rid}"
        admin = (f'from an administrator command prompt: {cmd}' if hostinfo.WINDOWS else f"sudo {cmd}")
        return (f"a stale lock left by {who} ({', '.join(bits)}) belongs to another account on this computer, "
                f"and only that account or an administrator may remove it. Ask {m.get('user') or owner or 'them'} to open an OpenHamShack program "
                f"on this radio (it clears its own stale lock), or an administrator to clear it ({admin}; "
                f"the file is {self.path})")

    def rewrite(self, marker: dict) -> bool:
        cur, _, _ = self.read()
        if (cur or {}).get("token") != marker["token"]:
            return False
        tmp = self.path.with_name(self.path.name + "." + marker["token"])
        tmp.write_text(_dump(marker), encoding="utf-8")
        os.replace(tmp, self.path)
        return True

    def touch(self, token: str) -> bool:
        cur, _, _ = self.read()
        if (cur or {}).get("token") != token:
            return False
        os.utime(self.path)
        return True

    def release(self, token: str) -> None:
        cur, _, _ = self.read()
        if (cur or {}).get("token") == token:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def ssh_runner(host: str, user: str = "root", password: str = "", port: int = 22,
               timeout: float = SSH_TIMEOUT_S):
    """A runner(script, stdin) -> (rc, stdout, stderr) that runs a POSIX sh
    script on the radio. The radios regenerate their host keys with each
    firmware flash, so known_hosts is not consulted (the password, analog
    by default, is no secret either); sshpass takes the password from the
    environment, never the command line.

    Windows has no sshpass, and its OpenSSH client knows nothing of
    /dev/null for a known_hosts file: there the same script goes over
    paramiko (paramiko_runner), the SSH library the Windows installer
    carries, with the same rules - any host key accepted, none recorded."""
    if hostinfo.WINDOWS:
        return paramiko_runner(host, user, password, port, timeout)

    def run(script: str, data: str = "") -> tuple[int, str, str]:
        cmd = ["ssh", "-p", str(port), "-o", f"ConnectTimeout={max(1, int(timeout) - 1)}",
               "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"]
        env = dict(os.environ)
        if password:
            cmd = ["sshpass", "-e"] + cmd + ["-o", "PubkeyAuthentication=no"]
            env["SSHPASS"] = password
        else:
            cmd += ["-o", "BatchMode=yes"]
        cmd += [f"{user}@{host}", script]
        try:
            r = subprocess.run(cmd, input=data, capture_output=True, text=True, timeout=timeout, env=env)
        except FileNotFoundError as e:
            return 255, "", f"{e.filename} is not installed"
        except subprocess.TimeoutExpired:
            return 255, "", f"no answer within {timeout:g} s"
        return r.returncode, r.stdout, r.stderr
    return run


def paramiko_runner(host: str, user: str = "root", password: str = "", port: int = 22,
                    timeout: float = SSH_TIMEOUT_S, client_factory=None):
    """ssh_runner's runner over paramiko (Windows). One connection per call,
    as ssh_runner makes one process per call: the lock is touched every
    15 s, and a held connection would be one more thing to find dead."""
    def run(script: str, data: str = "") -> tuple[int, str, str]:
        try:
            if client_factory is None:
                import paramiko  # noqa: PLC0415 - the Windows build carries it
                factory = paramiko.SSHClient
                policy = paramiko.AutoAddPolicy()
            else:
                factory, policy = client_factory, None
        except ImportError:
            return 255, "", f"paramiko is not installed (reinstall {hostinfo.NAME})"
        client = factory()
        try:
            if policy is not None:
                client.set_missing_host_key_policy(policy)     # the radios regenerate keys at each flash
            client.connect(host, port=port, username=user, password=password or None, timeout=timeout,
                           banner_timeout=timeout, auth_timeout=timeout, look_for_keys=not password,
                           allow_agent=not password)
            stdin, stdout, stderr = client.exec_command(script, timeout=timeout)
            if data:
                stdin.write(data)
            stdin.channel.shutdown_write()
            out = stdout.read().decode(errors="replace")
            err = stderr.read().decode(errors="replace")
            return stdout.channel.recv_exit_status(), out, err
        except Exception as e:  # noqa: BLE001 - paramiko's, socket's and timeouts alike mean "no answer"
            return 255, "", f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
    return run


def local_runner(script: str, data: str = "") -> tuple[int, str, str]:
    """The same scripts on this computer (the tests: the radio's side of the
    protocol, run for real)."""
    r = subprocess.run(["sh", "-c", script], input=data, capture_output=True, text=True, timeout=10)
    return r.returncode, r.stdout, r.stderr


def _q(s: str) -> str:
    return "'" + str(s).replace("'", "'\\''") + "'"


class ShellPlace:
    """The marker on the radio itself, through its shell. Ages are the
    radio's own: `date +%s` against `stat -c %Y`, in one command, because a
    Pluto with no network time thinks it is 1970 and this computer's clock
    says nothing about it."""

    def __init__(self, runner, where: str, path: str = RADIO_PATH):
        self.run = runner
        self.where = where
        self.path = path

    _READ = ('echo "now $(date +%s)"; if [ -e "$F" ]; then echo "mtime $(stat -c %Y "$F")"; '
             'echo "---"; cat "$F"; fi')

    def _script(self, body: str, **env) -> str:
        head = f"F={_q(self.path)}; " + "".join(f"{k}={_q(v)}; " for k, v in env.items())
        return head + body

    def _call(self, body: str, data: str = "", **env) -> tuple[int, str]:
        rc, out, err = self.run(self._script(body, **env), data)
        if rc == 255 or (rc != 0 and rc not in (1, 3)):
            raise OSError((err or out or f"exit {rc}").strip().splitlines()[-1] if (err or out) else f"exit {rc}")
        return rc, out

    @staticmethod
    def _parse_read(out: str) -> tuple[dict | None, float | None, bool]:
        now = mtime = None
        head, _, body = out.partition("---\n")
        for line in head.splitlines():
            k, _, v = line.partition(" ")
            try:
                if k == "now":
                    now = float(v)
                elif k == "mtime":
                    mtime = float(v)
            except ValueError:
                pass
        if mtime is None:
            return None, None, False
        age = max(0.0, now - mtime) if now is not None else None
        return _parse(body), age, True

    def read(self):
        _, out = self._call(self._READ)
        return self._parse_read(out)

    def take(self, marker: dict) -> bool:
        rc, _ = self._call('cat > "$F.$T" || exit 4; if ln "$F.$T" "$F" 2>/dev/null; then rm -f "$F.$T"; '
                           'chmod 644 "$F"; exit 0; fi; rm -f "$F.$T"; exit 3', _dump(marker), T=marker["token"])
        return rc == 0

    def take_over(self, old_token: str | None, marker: dict, ttl: float) -> bool:
        if old_token is not None:
            guard = 'grep -q "\\"token\\": \\"$O\\"" "$F" || exit 3; '
        else:
            # An unreadable marker: only if it is still older than the TTL by the radio's clock.
            guard = 'if [ -e "$F" ] && [ $(( $(date +%s) - $(stat -c %Y "$F") )) -le $TTL ]; then exit 3; fi; '
        body = (guard + 'if [ -e "$F" ]; then mv "$F" "$F.stale.$T" && rm -f "$F.stale.$T"; fi; '
                'cat > "$F.$T" || exit 4; if ln "$F.$T" "$F" 2>/dev/null; then rm -f "$F.$T"; chmod 644 "$F"; '
                'exit 0; fi; rm -f "$F.$T"; exit 3')
        rc, _ = self._call(body, _dump(marker), T=marker["token"], O=old_token or "", TTL=str(int(ttl)))
        return rc == 0

    def rewrite(self, marker: dict) -> bool:
        rc, _ = self._call('grep -q "\\"token\\": \\"$T\\"" "$F" || exit 3; cat > "$F.$T" && mv "$F.$T" "$F"',
                           _dump(marker), T=marker["token"])
        return rc == 0

    def touch(self, token: str) -> bool:
        rc, _ = self._call('grep -q "\\"token\\": \\"$T\\"" "$F" || exit 3; touch "$F"', T=token)
        return rc == 0

    def release(self, token: str) -> None:
        self._call('grep -q "\\"token\\": \\"$T\\"" "$F" 2>/dev/null || exit 0; rm -f "$F"', T=token)


# ------------------------------------------------------------------ lock
class RadioLock:
    """Take every place that applies, or none.

        lock = RadioLock(rid, "libre-2 (198.51.100.74)", places)
        notes = lock.take("tx", "437.000 MHz DVB-S2 ...")   # raises RadioBusy(sentence)
        lock.set_use("rx", note)                            # the same holder, now receiving
        lock.release()

    A place that cannot be reached at all (a radio whose shell does not
    answer) is dropped with a note saying the lock covers less than it
    should; the host place is never dropped. `on_lost(sentence)` is called
    from the heartbeat thread if a marker stops being ours.
    """

    def __init__(self, rid: str, label: str, places: list, *, app: str = hostinfo.NAME, version: str = "",
                 ttl: float = TTL_S, beat: float = BEAT_S, on_lost=None):
        self.rid = rid
        self.label = label
        self.places = list(places)
        self.app = app
        self.version = version
        self.ttl = ttl
        self.beat_s = beat
        self.on_lost = on_lost
        self.marker: dict | None = None
        self.held: list = []
        self.notes: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mu = threading.Lock()

    def _marker(self, use: str, note: str) -> dict:
        return {"v": VERSION, "radio": self.rid, "use": use, "app": self.app, "version": self.version,
                "host": socket.gethostname(), "bootId": boot_id(), "pid": os.getpid(),
                # USERNAME: a Windows desktop session sets neither of the
                # others (Desktop's VM 130 test found the field empty there).
                "user": os.environ.get("USER") or os.environ.get("LOGNAME") or os.environ.get("USERNAME") or "",
                "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "token": secrets.token_hex(8),
                "note": note}

    def take(self, use: str, note: str = "") -> list[str]:
        with self._mu:
            if self.marker is not None:
                self._set_use(use, note)
                return []
            marker = self._marker(use, note)
            held, notes = [], []
            try:
                for place in self.places:
                    try:
                        got = self._take_one(place, marker, notes)
                    except OSError as e:
                        if isinstance(place, HostPlace):
                            raise RadioBusy(f"{self.label}: the lock file {place.path} could not be made ({e})") \
                                from None
                        notes.append(f"The lock on {self.label} covers this computer only: {place.where} did not "
                                     f"answer ({e}). Programs on other computers will not see it")
                        continue
                    if got:
                        held.append(place)
            except RadioBusy:
                for place in held:
                    try:
                        place.release(marker["token"])
                    except OSError:
                        pass
                raise
            self.marker, self.held, self.notes = marker, held, notes
            self._stop.clear()
            self._thread = threading.Thread(target=self._beat, name="ohsatv-radiolock", daemon=True)
            self._thread.start()
            return notes

    def _take_one(self, place, marker: dict, notes: list) -> bool:
        for _ in range(3):
            if place.take(marker):
                return True
            cur, age, exists = place.read()
            if not exists:
                continue                                  # gone between the two: try again
            why = stale_why(cur, age, self.ttl)
            if not why:
                raise RadioBusy(busy_sentence(self.label, cur, age, self.app))
            try:
                got = place.take_over((cur or {}).get("token") if cur else None, marker, self.ttl)
            except StaleNotOurs as x:
                raise StaleNotOurs(f"{self.label}: {x}. {self.app} has not touched the radio") from None
            if got:
                who = f"{cur.get('app', 'another program')} on {cur.get('host', '?')}" if cur else "a program"
                notes.append(f"Took over a stale lock on {self.label} left by {who} ({why})")
                return True
        cur, age, _ = place.read()
        raise RadioBusy(busy_sentence(self.label, cur, age, self.app))

    def _set_use(self, use: str, note: str) -> None:
        if self.marker["use"] == use and self.marker["note"] == note:
            return
        self.marker = dict(self.marker, use=use, note=note)
        for place in self.held:
            try:
                place.rewrite(self.marker)
            except OSError:
                pass

    def set_use(self, use: str, note: str = "") -> None:
        with self._mu:
            if self.marker is not None:
                self._set_use(use, note)

    def beat(self) -> str:
        """Touch every marker held; '' or why the radio is no longer ours."""
        with self._mu:
            if self.marker is None:
                return ""
            for place in self.held:
                try:
                    ok = place.touch(self.marker["token"])
                except OSError:
                    continue          # an unreachable radio for a beat or two: the TTL is four beats
                if not ok:
                    cur, age, _ = place.read()
                    return (f"the lock on {self.label} ({place.where}) is no longer this program's: "
                            + (busy_sentence(self.label, cur, age, self.app) if cur else "it was removed"))
            return ""

    def _beat(self) -> None:
        while not self._stop.wait(self.beat_s):
            why = self.beat()
            if why and self.on_lost is not None:
                try:
                    self.on_lost(why)
                except Exception:  # noqa: BLE001 - the heartbeat must outlive a bad callback
                    pass

    def release(self) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2)
        with self._mu:
            if self.marker is None:
                return
            for place in self.held:
                try:
                    place.release(self.marker["token"])
                except OSError:
                    pass
            self.marker, self.held = None, []

    @property
    def taken(self) -> bool:
        return self.marker is not None

    def describe(self) -> dict:
        m = self.marker or {}
        return {"radio": self.rid, "label": self.label, "use": m.get("use", ""), "since": m.get("since", ""),
                "where": [p.where for p in self.held], "notes": list(self.notes)} if m else {}


def clear_stale(rid: str, directory: str | os.PathLike | None = None) -> tuple[bool, str]:
    """`--clear-stale-lock <radio id>`: what an administrator runs when a
    stale marker belongs to another account (HostPlace.not_ours). Removes
    it only if it is still stale by the spec's rules - an administrator
    could remove any marker by hand, a live one included, and this is the
    way that cannot. (cleared, sentence)."""
    place = HostPlace(rid, directory)
    cur, age, exists = place.read()
    if not exists:
        return False, f"there is no lock on {place.rid} on this computer ({place.path}); nothing to clear"
    why = stale_why(cur, age)
    if not why:
        who = f"{cur.get('app') or 'a program'} on {cur.get('host') or '?'}" if cur else "a program writing it now"
        heard = f", last heard {_ago(age)}" if age is not None else ""
        return False, f"the lock on {place.rid} is live, not stale (held by {who}{heard}); left alone"
    try:
        place.path.unlink()
    except FileNotFoundError:
        return False, f"{place.path} went while being cleared; nothing to do"
    except PermissionError as e:
        return False, (f"{place.path} is stale ({why}) but this account may not remove it ({e.strerror}): "
                       f"run this as an administrator" + ("" if hostinfo.WINDOWS else " (sudo)"))
    who = f"{cur.get('app', 'a program')} on {cur.get('host', '?')}" if cur else "a program"
    return True, f"cleared the stale lock on {place.rid} left by {who} ({why}): {place.path}"


def places_for(rid: str, ssh_host: str | None, cfg: dict) -> list:
    """The places the spec says apply: the radio's shell when it has one
    (and the setting has not turned it off), then this computer."""
    out: list = []
    if ssh_host and cfg.get("radioLockSsh", True) and os.environ.get("OHSATV_RADIO_SSH", "1") != "0":
        runner = ssh_runner(ssh_host, str(cfg.get("radioSshUser") or "root"), str(cfg.get("radioSshPassword") or ""))
        out.append(ShellPlace(runner, f"the radio's shell (ssh {cfg.get('radioSshUser') or 'root'}@{ssh_host})"))
    out.append(HostPlace(rid))
    return out

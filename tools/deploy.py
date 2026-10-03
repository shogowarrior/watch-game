#!/usr/bin/env python3
"""Copy the game onto the watch with mpremote (CPython host tool).

    python3 tools/deploy.py --port /dev/cu.usbserial-XXXX
    python3 tools/deploy.py -n                 # dry run: list files + command
    python3 tools/deploy.py --noapp            # also create /noapp (safe boot)
    python3 tools/deploy.py --app              # remove /noapp again
    python3 tools/deploy.py --tele A           # field test: log to /log/<n>_A.jsonl
    python3 tools/deploy.py --no-tele          # stop logging (remove /tele)
    python3 tools/deploy.py --debug A          # debug mode: watch A shows up on this laptop
    python3 tools/deploy.py --debug B --debug-host 192.168.1.23   # ... at this address
    python3 tools/deploy.py --no-debug         # debug mode off (removes /debug, secrets.py)
    python3 tools/deploy.py --secrets          # ALSO copy secrets.py (Wi-Fi name and password)

Copies app/, finder/, hal/, ui/, bma423conf.bin (optional, BMA423
feature-engine blob) and then boot.py and main.py into the watch root
(boot.py replaces any old one that joined WiFi / started webrepl), creating
directories first. The entry points go last, so an interrupted deploy keeps
the old ones. Only .py/.json/.bin files are copied; __pycache__ and dotfiles
are skipped.

The watch is hard-reset first (a separate mpremote call, then a 3 s wait): a
game started on battery runs the ESP32 hardware watchdog, which cannot be
stopped and would reboot the watch 8 s into the copy. After the reset the
game starts on USB with the stoppable watchdog (app/runtime.py). The copy
then runs in ONE mpremote session and ends with another hard reset, so
main.py starts the game (or stops at /noapp; mpremote's soft-reset would
leave the watch at the raw REPL). Normal play is ESP-NOW only and never joins
a Wi-Fi network, so secrets.py is NOT copied unless ``--debug`` (or
``--secrets``) is given. Needs ``pip install mpremote``.

Debug mode (docs/design/debug-mode.md): ``--debug A`` copies secrets.py
(the Wi-Fi name and password, from secrets.example.py) and writes /debug
(``{"dev": "A", "host": <this laptop's address>, "port": 47268}``); on boot
the watch joins that Wi-Fi and sends what it does to
``tools/debug_server.py`` on this laptop. The address is found by asking the
OS which one it would use to reach another network (no packet is sent);
``--debug-host`` sets it instead. Without one the watch broadcasts to the
whole Wi-Fi network, which is less reliable. Both watches must use the same
Wi-Fi. ``--no-debug`` removes /debug and secrets.py from the watch.
"""

import argparse
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from hal.debuglink import DEBUG_PORT  # noqa: E402  (after the path fix: run as a script)

FILES = ("boot.py", "main.py")
DIRS = ("app", "finder", "hal", "ui")
OPTIONAL = ("bma423conf.bin",)
SECRETS = "secrets.py"           # Wi-Fi credentials: copied only with --debug or --secrets
EXTS = (".py", ".json", ".bin")
RESET_WAIT_S = 3                 # safe-boot window (1 s) + Board init; with /debug the watch may still be joining the Wi-Fi, which mpremote's Ctrl-C stops safely


def collect(root=ROOT, secrets=False, debug=False):
    """-> (dirs to create, [(local, remote)], notes). ``secrets`` also
    copies secrets.py (with a warning note); so does ``debug`` (debug mode
    needs it)."""
    dirs, files, notes = [], [], []
    for d in DIRS:
        top = os.path.join(root, d)
        if not os.path.isdir(top):
            notes.append("missing %s/" % d)
            continue
        for cur, subdirs, names in os.walk(top):
            subdirs[:] = sorted(s for s in subdirs if s != "__pycache__" and not s.startswith("."))
            rel = os.path.relpath(cur, root).replace(os.sep, "/")
            dirs.append(rel)
            for n in sorted(names):
                if n.endswith(EXTS) and not n.startswith("."):
                    files.append((os.path.join(cur, n), rel + "/" + n))
    for f in OPTIONAL:
        p = os.path.join(root, f)
        if os.path.isfile(p):
            files.append((p, f))
        else:
            notes.append("optional %s not found, skipped" % f)
    p = os.path.join(root, SECRETS)
    if secrets or debug:
        if not os.path.isfile(p):
            notes.append("%s not found, not copied" % SECRETS)
        elif debug:
            files.append((p, SECRETS))
            notes.append("copying %s: debug mode joins the Wi-Fi with it" % SECRETS)
        else:
            files.append((p, SECRETS))
            notes.append("WARNING: copying %s (WiFi credentials) to the watch; "
                         "only debug mode (--debug) needs it" % SECRETS)
    elif os.path.isfile(p):
        notes.append("%s NOT copied (--debug copies it)" % SECRETS)
    for f in FILES:                   # last: an interrupted copy keeps the old ones
        p = os.path.join(root, f)
        if os.path.isfile(p):
            files.append((p, f))
        else:
            notes.append("missing %s (the watch will not start the game)" % f)
    return dirs, files, notes


def mkdirs_code(dirs):
    return ("import os\nfor d in %r:\n try:\n  os.mkdir(d)\n except OSError:\n  pass\n"
            % (list(dirs),))


def _connect(mpremote, port):
    return list(mpremote) + (["connect", port] if port else [])


def reset_cmd(mpremote, port):
    """Hard reset (its own mpremote call: nothing can follow ``reset``)."""
    return _connect(mpremote, port) + ["reset"]


def _rm_code(path):
    return "import os\ntry:\n os.remove(%r)\nexcept OSError:\n pass" % path


def _write_code(path, text):
    return "f=open(%r,'w')\nf.write(%r)\nf.close()" % (path, text)


def debug_config(dev, host, port=DEBUG_PORT):
    """The /debug file's contents (hal/debuglink.read_config reads it); no
    ``host`` makes the watch broadcast."""
    d = {"dev": dev, "port": port}
    if host:
        d["host"] = host
    return json.dumps(d)


def build_cmd(mpremote, port, dirs, files, noapp=None, reset=True, tele=None, debug=None):
    """``noapp``/``tele``/``debug``: None leaves /noapp, /tele and /debug as
    they are, False removes them (``debug`` False also removes secrets.py);
    ``noapp`` True creates /noapp, a ``tele`` name writes /tele, a ``debug``
    string (``debug_config``) writes /debug."""
    cmd = _connect(mpremote, port)
    cmd += ["exec", mkdirs_code(dirs)]
    for local, remote in files:
        cmd += ["+", "fs", "cp", local, ":" + remote]
    if noapp is True:
        cmd += ["+", "exec", "open('/noapp','w').close()"]
    elif noapp is False:
        cmd += ["+", "exec", _rm_code("/noapp")]
    if tele:
        cmd += ["+", "exec", _write_code("/tele", tele)]
    elif tele is False:
        cmd += ["+", "exec", _rm_code("/tele")]
    if debug:
        cmd += ["+", "exec", _write_code("/debug", debug)]
    elif debug is False:
        cmd += ["+", "exec", _rm_code("/debug"), "+", "exec", _rm_code("/" + SECRETS)]
    if reset:
        cmd += ["+", "reset"]         # hard: main.py runs (a soft-reset stays in the raw REPL)
    return cmd


def lan_ip():
    """This laptop's address on the local network, or None. A UDP
    ``connect`` only asks the OS which address it would send from (to a
    documentation-only address): no packet leaves."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return None if ip.startswith("127.") or ip == "0.0.0.0" else ip


def is_ipv4(s):
    """True for a dotted IPv4 address (the watch sends to numbers, not names).
    The standard library's check: ASCII digits only, no leading zeros."""
    try:
        ipaddress.IPv4Address(s)
    except ValueError:
        return False
    return True


def find_mpremote():
    exe = shutil.which("mpremote")
    return [exe] if exe else [sys.executable, "-m", "mpremote"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", "-p", help="serial port (default: mpremote auto-detect)")
    ap.add_argument("--dry-run", "-n", action="store_true", help="print, do not copy")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--noapp", action="store_true", help="create /noapp so main.py skips the game")
    g.add_argument("--app", action="store_true", help="remove /noapp")
    t = ap.add_mutually_exclusive_group()
    t.add_argument("--tele", metavar="DEV",
                   help="log telemetry to /log/<n>_DEV.jsonl on every boot (field tests)")
    t.add_argument("--no-tele", action="store_true", help="remove /tele (no telemetry)")
    ap.add_argument("--no-reset", action="store_true",
                    help="no hard reset after the copy (the one before it still runs)")
    ap.add_argument("--secrets", action="store_true",
                    help="also copy secrets.py (WiFi credentials; only debug mode needs it)")
    d = ap.add_mutually_exclusive_group()
    d.add_argument("--debug", metavar="DEV", choices=("A", "B"),
                   help="debug mode: the watch joins the Wi-Fi in secrets.py and sends what it "
                        "does to tools/debug_server.py on this laptop; DEV (A or B) is its "
                        "name on the page")
    d.add_argument("--no-debug", action="store_true",
                   help="debug mode off: remove /debug and secrets.py from the watch")
    ap.add_argument("--debug-host", metavar="IP",
                    help="with --debug: this laptop's address (default: found automatically)")
    a = ap.parse_args(argv)

    if a.debug_host and not a.debug:
        ap.error("--debug-host needs --debug")
    if a.no_debug and a.secrets:
        ap.error("--no-debug removes secrets.py: do not pass --secrets with it")
    debug = False if a.no_debug else None
    if a.debug:
        if not os.path.isfile(os.path.join(ROOT, SECRETS)):
            print("--debug needs %s: copy secrets.example.py to %s and put your Wi-Fi "
                  "name and password in it" % (SECRETS, SECRETS))
            return 2
        host = a.debug_host or lan_ip()
        if host and not is_ipv4(host):
            ap.error("--debug-host must be an address like 192.168.1.23, not a name")
        debug = debug_config(a.debug, host)
        if host:
            print("debug mode: watch %s will send to %s:%d" % (a.debug, host, DEBUG_PORT))
        else:
            print("debug mode: could not find this laptop's address, so watch %s will "
                  "broadcast to the whole Wi-Fi (less reliable; --debug-host IP fixes it)"
                  % a.debug)

    dirs, files, notes = collect(ROOT, a.secrets, bool(a.debug))
    for n in notes:
        print("note:", n)
    total = sum(os.path.getsize(p) for p, _ in files)
    print("%d files, %d dirs, %.1f KB" % (len(files), len(dirs), total / 1024))
    for _, remote in files:
        print("  :" + remote)
    noapp = True if a.noapp else False if a.app else None
    tele = False if a.no_tele else a.tele
    mp = find_mpremote()
    first = reset_cmd(mp, a.port)
    cmd = build_cmd(mp, a.port, dirs, files, noapp, not a.no_reset, tele, debug)
    if a.dry_run:
        shown = [c if "\n" not in c else repr(c) for c in cmd]
        print("\ndry run, would run:\n  %s\n  (wait %d s)\n  %s"
              % (" ".join(first), RESET_WAIT_S, " ".join(shown)))
        return 0
    rc = subprocess.call(first)
    if rc:
        return rc
    time.sleep(RESET_WAIT_S)
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())

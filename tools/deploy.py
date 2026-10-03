#!/usr/bin/env python3
"""Copy the game onto the watch with mpremote (CPython host tool).

    python3 tools/deploy.py --port /dev/cu.usbserial-XXXX
    python3 tools/deploy.py -n                 # dry run: list files + command
    python3 tools/deploy.py --noapp            # also create /noapp (safe boot)
    python3 tools/deploy.py --app              # remove /noapp again
    python3 tools/deploy.py --tele A           # field test: log to /log/<n>_A.jsonl
    python3 tools/deploy.py --no-tele          # stop logging (remove /tele)
    python3 tools/deploy.py --secrets          # ALSO copy secrets.py (WiFi creds)

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
leave the watch at the raw REPL). The game is ESP-NOW only and never joins
an AP, so secrets.py is NOT copied unless ``--secrets`` is given. Needs
``pip install mpremote``.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = ("boot.py", "main.py")
DIRS = ("app", "finder", "hal", "ui")
OPTIONAL = ("bma423conf.bin",)
SECRETS = "secrets.py"           # WiFi credentials: opt-in only (--secrets)
EXTS = (".py", ".json", ".bin")
RESET_WAIT_S = 3                 # safe-boot window (1 s) + Board init, then the game runs


def collect(root=ROOT, secrets=False):
    """-> (dirs to create, [(local, remote)], notes). ``secrets`` also
    copies secrets.py (with a warning note)."""
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
    if secrets:
        if os.path.isfile(p):
            files.append((p, SECRETS))
            notes.append("WARNING: copying %s (WiFi credentials) to the watch; "
                         "the game does not need it" % SECRETS)
        else:
            notes.append("--secrets given but %s not found" % SECRETS)
    elif os.path.isfile(p):
        notes.append("%s NOT copied (pass --secrets to copy it)" % SECRETS)
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


def build_cmd(mpremote, port, dirs, files, noapp=None, reset=True, tele=None):
    """``noapp``/``tele``: None leaves /noapp and /tele as they are, False
    removes them; ``noapp`` True creates /noapp, a ``tele`` name writes /tele."""
    cmd = _connect(mpremote, port)
    cmd += ["exec", mkdirs_code(dirs)]
    for local, remote in files:
        cmd += ["+", "fs", "cp", local, ":" + remote]
    if noapp is True:
        cmd += ["+", "exec", "open('/noapp','w').close()"]
    elif noapp is False:
        cmd += ["+", "exec", _rm_code("/noapp")]
    if tele:
        cmd += ["+", "exec", "f=open('/tele','w')\nf.write(%r)\nf.close()" % tele]
    elif tele is False:
        cmd += ["+", "exec", _rm_code("/tele")]
    if reset:
        cmd += ["+", "reset"]         # hard: main.py runs (a soft-reset stays in the raw REPL)
    return cmd


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
                    help="also copy secrets.py (WiFi credentials; not needed by the game)")
    a = ap.parse_args(argv)

    dirs, files, notes = collect(secrets=a.secrets)
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
    cmd = build_cmd(mp, a.port, dirs, files, noapp, not a.no_reset, tele)
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

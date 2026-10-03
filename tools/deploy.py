#!/usr/bin/env python3
"""Copy the game onto the watch with mpremote (CPython host tool).

    python3 tools/deploy.py --port /dev/cu.usbserial-XXXX
    python3 tools/deploy.py -n                 # dry run: list files + command
    python3 tools/deploy.py --noapp            # also create /noapp (safe boot)
    python3 tools/deploy.py --app              # remove /noapp again
    python3 tools/deploy.py --secrets          # ALSO copy secrets.py (WiFi creds)

Copies boot.py, main.py, app/, finder/, hal/, ui/ (if present) and
bma423conf.bin (optional, BMA423 feature-engine blob) into the watch root
(boot.py replaces any old one that joined WiFi / started webrepl),
creating directories first. Only .py/.json/.bin files are copied;
__pycache__ and dotfiles are skipped. Everything runs in ONE mpremote
session. The game is ESP-NOW only and never joins an AP, so secrets.py
is NOT copied unless ``--secrets`` is given. Needs ``pip install mpremote``.
"""

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = ("boot.py", "main.py")
DIRS = ("app", "finder", "hal", "ui")
OPTIONAL = ("bma423conf.bin",)
SECRETS = "secrets.py"           # WiFi credentials: opt-in only (--secrets)
EXTS = (".py", ".json", ".bin")


def collect(root=ROOT, secrets=False):
    """-> (dirs to create, [(local, remote)], notes). ``secrets`` also
    copies secrets.py (with a warning note)."""
    dirs, files, notes = [], [], []
    for f in FILES:
        p = os.path.join(root, f)
        if os.path.isfile(p):
            files.append((p, f))
        else:
            notes.append("missing %s (the watch will not start the game)" % f)
    for d in DIRS:
        top = os.path.join(root, d)
        if not os.path.isdir(top):
            if d != "ui":
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
    return dirs, files, notes


def mkdirs_code(dirs):
    return ("import os\nfor d in %r:\n try:\n  os.mkdir(d)\n except OSError:\n  pass\n"
            % (list(dirs),))


def build_cmd(mpremote, port, dirs, files, noapp=None, reset=True):
    cmd = list(mpremote)
    if port:
        cmd += ["connect", port]
    cmd += ["exec", mkdirs_code(dirs)]
    for local, remote in files:
        cmd += ["+", "fs", "cp", local, ":" + remote]
    if noapp is True:
        cmd += ["+", "exec", "open('/noapp','w').close()"]
    elif noapp is False:
        cmd += ["+", "exec", "import os\ntry:\n os.remove('/noapp')\nexcept OSError:\n pass"]
    if reset:
        cmd += ["+", "soft-reset"]
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
    ap.add_argument("--no-reset", action="store_true", help="do not soft-reset afterwards")
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
    cmd = build_cmd(find_mpremote(), a.port, dirs, files, noapp, not a.no_reset)
    if a.dry_run:
        shown = [c if "\n" not in c else repr(c) for c in cmd]
        print("\ndry run, would run:\n  " + " ".join(shown))
        return 0
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())

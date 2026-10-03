#!/usr/bin/env python3
"""Save the Wi-Fi name and password for debug mode on this laptop (CPython host tool).

    python3 tools/wifi_setup.py            # asks for them and saves them
    python3 tools/wifi_setup.py --check    # is the saved file there, private and readable?
    python3 tools/wifi_setup.py --forget   # deletes the saved file

Debug mode over Wi-Fi (``tools/deploy.py --debug A --wifi``,
docs/design/debug-mode.md decision 6) copies the Wi-Fi name and password to
the watch. They are typed here, on the laptop (the password is hidden and
typed twice), and saved outside the repo in ``~/.config/watch-game/wifi.py``
(``$WATCH_GAME_WIFI`` names another file), so no commit, search or chat can
pick them up. A new folder is made readable only by the owner (0700); the
file (0600) is written to a temporary file in the same folder and then
renamed, so it is saved whole or not at all. It holds ``WIFI_SSID`` and
``WIFI_PASSWORD`` in the format of secrets.example.py, which the watch reads
(hal/debuglink.read_secrets). The tool prints where it saved them, never the
values.

Shared with tools/deploy.py and tests/test_secrets_guard.py: ``wifi_path``
(the saved file), ``find_wifi`` (the file to copy: the saved one, else an
older secrets.py in the repo) and ``read_wifi`` (the two values, read
without running the file or printing them).
"""

import argparse
import ast
import os
import string
import sys
import tempfile
from getpass import getpass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = "WATCH_GAME_WIFI"
DEFAULT = os.path.join("~", ".config", "watch-game", "wifi.py")
LEGACY = "secrets.py"            # the old place: the repo root (gitignored)
NAMES = ("WIFI_SSID", "WIFI_PASSWORD")
SETUP = "python3 tools/wifi_setup.py"
HEADER = "# Wi-Fi for debug mode, saved by %s. Private: never share or commit it.\n" % SETUP
LEGACY_HINT = ("secrets.py in the repo is the old place for the Wi-Fi name and password: "
               "save them with %s instead, then delete secrets.py" % SETUP)
UNREADABLE = "This terminal sent a character it could not read. Please type it again."


def wifi_path():
    """The saved Wi-Fi file: $WATCH_GAME_WIFI, else ~/.config/watch-game/wifi.py."""
    return os.path.abspath(os.path.expanduser(os.environ.get(ENV) or DEFAULT))


def wifi_files(root=ROOT):
    """Every Wi-Fi file there is, the one deploy.py copies first: the saved
    file, then an older secrets.py in the repo ``root``."""
    return [p for p in (wifi_path(), os.path.join(root, LEGACY)) if os.path.isfile(p)]


def find_wifi(root=ROOT):
    """The Wi-Fi file deploy.py copies -> (path, hint): the saved file (no
    hint), else the repo's secrets.py with a hint to move it; (None, None)
    when there is neither."""
    found = wifi_files(root)
    if not found:
        return None, None
    return found[0], (None if found[0] == wifi_path() else LEGACY_HINT)


def read_wifi(path):
    """(name, password) from a Wi-Fi file, by the watch's rules: the name is
    text, the password text or missing (an open network). The file is parsed,
    never run, and nothing is printed; ValueError with a plain reason that
    never quotes it."""
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except OSError as e:
        raise ValueError("it cannot be opened (%s)" % e.strerror)
    except (SyntaxError, ValueError):        # ValueError: not UTF-8, or a NUL byte
        raise ValueError("it is not valid Python")
    found = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in NAMES):
            v = node.value                   # anything but a literal is not text
            found[node.targets[0].id] = v.value if isinstance(v, ast.Constant) else v
    ssid, password = found.get("WIFI_SSID"), found.get("WIFI_PASSWORD")
    if not isinstance(ssid, str) or not ssid:
        raise ValueError("it has no Wi-Fi name in quotes (WIFI_SSID)")
    if password is not None and not isinstance(password, str):
        raise ValueError("its password is not in quotes (WIFI_PASSWORD)")
    return ssid, password or ""


def save(path, ssid, password):
    """Write the Wi-Fi file whole or not at all: a temporary file in the same
    folder (mkstemp makes it 0600), then ``os.replace``."""
    folder = os.path.dirname(path)
    os.makedirs(folder, 0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".wifi-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(HEADER + "WIFI_SSID = %r\nWIFI_PASSWORD = %r\n" % (ssid, password))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        os.remove(tmp)
        raise


def _size(text):
    """UTF-8 bytes in ``text`` (what the watch sends), or None for a character
    the terminal could not decode."""
    try:
        return len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return None


def check_name(name):
    """None when ``name`` can be a Wi-Fi name (1 to 32 UTF-8 bytes), else why not."""
    n = _size(name)
    if n is None:
        return UNREADABLE
    if n == 0:
        return "The name is empty. Type your Wi-Fi's name as your phone shows it."
    if n > 32:
        return ("That is too long for a Wi-Fi name: at most 32 characters "
                "(an accented letter counts as 2, an emoji as 4).")
    return None


def check_password(password):
    """None when ``password`` can be a Wi-Fi password (empty for an open
    network, 8 to 63 UTF-8 bytes, or 64 hex digits), else why not."""
    n = _size(password)
    if n is None:
        return UNREADABLE
    if n == 0 or 8 <= n <= 63 or (n == 64 and set(password) <= set(string.hexdigits)):
        return None
    return "A Wi-Fi password has 8 to 63 characters, or is 64 hex digits (0-9, a-f)."


def _yes(prompt):
    return input(prompt).strip().lower() in ("y", "yes")


def ask_name():
    while True:
        name = input("Wi-Fi name: ")
        why = check_name(name)
        if why is None:
            return name
        print(why)


def ask_password():
    """Hidden and typed twice; an empty one needs a yes (an open network)."""
    while True:
        password = getpass("Wi-Fi password (hidden as you type; press Enter if there is none): ")
        if getpass("The same password again: ") != password:
            print("The two passwords are not the same. Please type them again.")
            continue
        why = check_password(password)
        if why:
            print(why)
        elif password or _yes("No password: is it an open network (no password at all)? [y/N] "):
            return password


def setup(path):
    print("Debug mode over Wi-Fi needs your Wi-Fi name and password. They are saved on this\n"
          "computer only, outside the repo, and only you can read them. Use a 2.4 GHz Wi-Fi\n"
          "that both watches and this laptop are on.")
    try:
        ssid = ask_name()
        password = ask_password()
    except (EOFError, KeyboardInterrupt):
        print("\nNothing saved.")
        return 1
    save(path, ssid, password)
    print("Saved in %s (only you can read it)." % path)
    print("python3 tools/deploy.py --port <port> --debug A --wifi copies it to a watch.")
    return 0


def check(path):
    """Say whether ``path`` is there, private and readable, never showing the
    values -> 0 when all three hold, else 1."""
    if not os.path.isfile(path):
        print("No Wi-Fi saved yet (looked for %s). Run %s to save it." % (path, SETUP))
        return 1
    print("Wi-Fi file: %s" % path)
    private = not os.stat(path).st_mode & 0o077
    if private:
        print("private: yes, only you can read it")
    else:
        print("private: NO, other people on this computer can read it (chmod 600 %s fixes that)"
              % path)
    try:
        _, password = read_wifi(path)
    except ValueError as e:
        print("readable: NO, %s. Run %s to save it again." % (e, SETUP))
        return 1
    print("readable: yes, it has a Wi-Fi name and %s"
          % ("a password" if password else "no password (an open network)"))
    return 0 if private else 1


def forget(path):
    if not os.path.isfile(path):
        print("Nothing to delete: no Wi-Fi saved in %s." % path)
        return 0
    os.remove(path)
    print("Deleted %s." % path)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true",
                   help="say whether the saved file is there, private and readable "
                        "(never shows the name or password)")
    g.add_argument("--forget", action="store_true", help="delete the saved file")
    a = ap.parse_args(argv)
    path = wifi_path()
    rc = check(path) if a.check else forget(path) if a.forget else setup(path)
    if os.path.isfile(os.path.join(ROOT, LEGACY)):
        print("Note: " + LEGACY_HINT + ".")
    return rc


if __name__ == "__main__":
    sys.exit(main())

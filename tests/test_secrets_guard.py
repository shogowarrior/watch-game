"""Wi-Fi credentials never reach git (docs/design/debug-mode.md decision 6).

The Wi-Fi name and password are read from every Wi-Fi file there is (the one
tools/wifi_setup.py saved, and an older secrets.py in the repo) with its
shared reader (never run, never printed), and no file git would commit
(tracked, or new and not ignored) may contain one; a failure names the
variable and the files only. secrets.py, webrepl_cfg.py and logs/ must be
ignored. Needs git and subprocess: skipped under MicroPython and outside a
git checkout."""

from tests import Skip

TEMPLATE = "secrets.example.py"
MIN_LEN = 6                # shorter strings would match ordinary words


def _wifi_setup():
    try:
        from tools import wifi_setup as w      # CPython host tool only
    except ImportError:
        raise Skip("tools/wifi_setup.py is a CPython host tool")
    return w


def _git(*args):
    """``git args`` in the repo root -> CompletedProcess; Skip without git."""
    try:
        import subprocess
    except ImportError:
        raise Skip("needs subprocess (CPython)")
    try:
        r = subprocess.run(("git",) + args, capture_output=True)
    except OSError:
        raise Skip("git is not installed")
    if r.returncode == 128:              # fatal: not a git checkout
        raise Skip("not a git checkout")
    return r


def _secret_values(w, path, template):
    """[(name, value)] worth guarding in the Wi-Fi file ``path``: long
    enough, and not one of the template's placeholders."""
    skip = set(w.read_wifi(template))
    values = zip(w.NAMES, w.read_wifi(path))
    return [(n, v) for n, v in values if len(v) >= MIN_LEN and v not in skip]


def _leaks(secrets, paths):
    """[(name, path)] for each file in ``paths`` holding one of ``secrets``."""
    found = []
    for p in paths:
        try:
            with open(p, "rb") as f:
                data = f.read()
        except OSError:
            continue                     # deleted since listed, or not a file
        for name, v in secrets:
            if v.encode("utf-8") in data:
                found.append((name, p))
    return found


def _committable():
    """Files ``git add -A`` would commit: tracked, or new and not ignored."""
    r = _git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    assert r.returncode == 0, r.stderr
    return sorted(set(p for p in r.stdout.decode("utf-8").split("\0") if p))


def test_no_secret_in_committable_files():
    w = _wifi_setup()
    wifi = w.wifi_files(w.ROOT)
    if not wifi:
        raise Skip("no Wi-Fi file (tools/wifi_setup.py saves one)")
    files = _committable()
    for path in wifi:
        try:
            secrets = _secret_values(w, path, TEMPLATE)
        except ValueError as e:
            raise AssertionError("cannot check %s: %s" % (path, e))
        leaks = _leaks(secrets, files)
        assert not leaks, "a value from %s is in a file git would commit: %s" % (
            path, "; ".join("%s in %s" % x for x in leaks))


def test_secret_files_and_logs_are_ignored():
    for p in ("secrets.py", "webrepl_cfg.py", "logs/debug-20260101-120000.jsonl"):
        assert _git("check-ignore", "-q", p).returncode == 0, p + " is not ignored by git"


def test_guard_finds_a_planted_value():
    w = _wifi_setup()
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        def put(name, text):
            p = os.path.join(tmp, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            return p
        # the template's placeholder password is not guarded
        fake = put("wifi.py", 'WIFI_SSID = "FakeNet-42"\nWIFI_PASSWORD = "your-password"\n')
        assert _secret_values(w, fake, TEMPLATE) == [("WIFI_SSID", "FakeNet-42")]
        # nor is a name too short to tell from ordinary words
        fake = put("wifi.py", 'WIFI_SSID = "Cafe"\nWIFI_PASSWORD = "not-a-real-pass-7"\n')
        found = _secret_values(w, fake, TEMPLATE)
        assert found == [("WIFI_PASSWORD", "not-a-real-pass-7")], [n for n, _ in found]
        clean = put("clean.md", "Join your-password Wi-Fi at the Cafe with not-a-real-pass\n")
        leaky = put("notes.ipynb", '{"out": "connected, pw=not-a-real-pass-7"}')
        gone = os.path.join(tmp, "gone.py")
        assert _leaks(found, [clean, leaky, gone]) == [("WIFI_PASSWORD", leaky)]

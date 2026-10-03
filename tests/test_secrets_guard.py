"""Wi-Fi credentials never reach git (docs/design/debug-mode.md decision 6).

If a local secrets.py exists, its string values are read with ``ast`` (never
run, never printed) and no file git would commit (tracked, or new and not
ignored) may contain one; a failure names the variable and the file only.
secrets.py, webrepl_cfg.py and logs/ must be ignored. Needs git and
subprocess: skipped under MicroPython and outside a git checkout."""

import os

from tests import Skip

SECRETS = "secrets.py"
TEMPLATE = "secrets.example.py"
MIN_LEN = 6                # shorter strings would match ordinary words


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


def _strings(path):
    """[(name, value)]: every string in each top-level assignment of ``path``."""
    import ast
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    out = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        name = ",".join(t.id for t in targets if isinstance(t, ast.Name)) or "?"
        for n in ast.walk(node.value):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                out.append((name, n.value))
    return out


def _secret_values(path, template=None):
    """[(name, value)] worth guarding in ``path``: long enough, and not one of
    the template's placeholders."""
    skip = set(v for _, v in _strings(template)) if template and os.path.isfile(template) else set()
    return [(n, v) for n, v in _strings(path) if len(v) >= MIN_LEN and v not in skip]


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
    if not os.path.isfile(SECRETS):
        raise Skip("no local secrets.py")
    files = _committable()
    secrets = _secret_values(SECRETS, TEMPLATE)
    if not secrets:
        raise Skip("secrets.py holds no value to guard")
    leaks = _leaks(secrets, files)
    assert not leaks, "a value from secrets.py is in a file git would commit: " + \
        "; ".join("%s in %s" % x for x in leaks)


def test_secret_files_and_logs_are_ignored():
    for p in (SECRETS, "webrepl_cfg.py", "logs/debug-20260101-120000.jsonl"):
        assert _git("check-ignore", "-q", p).returncode == 0, p + " is not ignored by git"


def test_guard_finds_a_planted_value():
    try:
        import tempfile
    except ImportError:
        raise Skip("needs tempfile (CPython)")
    with tempfile.TemporaryDirectory() as tmp:
        def put(name, text):
            p = os.path.join(tmp, name)
            with open(p, "w") as f:
                f.write(text)
            return p
        tpl = put("example.py", 'WIFI_SSID = "your-ssid"\nWIFI_PASSWORD = "your-password"\n')
        fake = put("secrets.py", '"""Doc."""\nWIFI_SSID = "FakeNet-42"\nWIFI_PASSWORD = "your-password"\n'
                   'EXTRA: list = [("Cafe-Guest", "not-a-real-pass-7")]\nN = 3\nSHORT = "abc"\n')
        found = _secret_values(fake, tpl)
        assert sorted(found) == [("EXTRA", "Cafe-Guest"), ("EXTRA", "not-a-real-pass-7"),
                                 ("WIFI_SSID", "FakeNet-42")], [n for n, _ in found]
        clean = put("clean.md", "Join your-password Wi-Fi with FakeNet-4 (not the same)\n")
        leaky = put("notes.ipynb", '{"out": "connected, pw=not-a-real-pass-7"}')
        assert _leaks(found, [clean, leaky, os.path.join(tmp, "gone.py")]) == [("EXTRA", leaky)]

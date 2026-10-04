"""Wi-Fi credentials never reach git (docs/design/debug-mode.md decision 6).

The Wi-Fi name and password are read from every Wi-Fi file there is (the one
tools/wifi_setup.py saved, and an older secrets.py in the repo) with its
shared reader (never run, never printed), and no file git would commit
(tracked, or new and not ignored) may contain one; a failure names the
variable and the files only. Nor may any commit git knows (every branch,
fetched ones too): a pushed commit stays readable after a later one removes
the value, so that failure says to change the password on the router. Without
a Wi-Fi file both checks skip, so they also run on made-up ones
(test_guard_checks_every_wifi_file, test_history_guard_finds_a_removed_value).
secrets.py, webrepl_cfg.py and logs/ must be ignored. Needs git and
subprocess: skipped under MicroPython and outside a git checkout."""

from tests import Skip

TEMPLATE = "secrets.example.py"
MIN_LEN = 6                # shorter strings would match ordinary words


def _wifi_setup():
    try:
        from tools import wifi_setup as w      # CPython host tool only
    except ImportError:
        raise Skip("tools/wifi_setup.py is a CPython host tool")
    return w


def _git(*args, cwd=None):
    """``git args`` in the repo root (or ``cwd``) -> CompletedProcess; Skip
    without git."""
    try:
        import subprocess
    except ImportError:
        raise Skip("needs subprocess (CPython)")
    try:
        r = subprocess.run(("git",) + args, capture_output=True, cwd=cwd)
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


def _history_leaks(secrets, cwd=None):
    """[(name, commit, path)] for each commit reachable from any ref (local
    and fetched branches, tags) whose files hold one of ``secrets``."""
    r = _git("rev-list", "--all", cwd=cwd)
    assert r.returncode == 0, r.stderr
    revs = r.stdout.decode().split()
    found = []
    for name, v in secrets:
        if not revs:
            break
        r = _git("grep", "-l", "-F", "-e", v, *revs, cwd=cwd)
        assert r.returncode in (0, 1), r.stderr          # 1: no match
        for line in r.stdout.decode("utf-8", "replace").splitlines():
            commit, path = line.split(":", 1)
            found.append((name, commit[:9], path))
    return found


def _wifi_secrets():
    """[(Wi-Fi file, its guarded values)]; Skip without a Wi-Fi file."""
    w = _wifi_setup()
    wifi = w.wifi_files(w.ROOT)
    if not wifi:
        raise Skip("no Wi-Fi file (tools/wifi_setup.py saves one)")
    out = []
    for path in wifi:
        try:
            out.append((path, _secret_values(w, path, TEMPLATE)))
        except ValueError as e:
            raise AssertionError("cannot check %s: %s" % (path, e))
    return out


def test_no_secret_in_committable_files():
    files = _committable()
    for path, secrets in _wifi_secrets():
        leaks = _leaks(secrets, files)
        assert not leaks, "a value from %s is in a file git would commit: %s" % (
            path, "; ".join("%s in %s" % x for x in leaks))


def test_no_secret_in_git_history():
    for path, secrets in _wifi_secrets():
        leaks = _history_leaks(secrets)
        assert not leaks, (
            "a value from %s is in git history: %s. Anyone who can read the repo can "
            "read a pushed commit, so change it on the router, then run "
            "python3 tools/wifi_setup.py again" % (
                path, "; ".join("%s in %s %s" % x for x in leaks)))


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


def _guard_fails():
    """The AssertionError message of test_no_secret_in_committable_files, or None."""
    try:
        test_no_secret_in_committable_files()
    except AssertionError as e:
        return str(e)
    return None


def test_guard_checks_every_wifi_file():
    w = _wifi_setup()
    import os
    import tempfile
    g = globals()
    pw = "made-up-" + str(os.getpid()) + "-pw"      # built at run time: in no committed file
    old_env, old_files = os.environ.get(w.ENV), g["_committable"]
    with tempfile.TemporaryDirectory() as tmp:
        fake = os.path.join(tmp, "wifi.py")
        w.save(fake, "MadeUpNet-" + str(os.getpid()), pw)
        os.environ[w.ENV] = fake
        try:
            assert _guard_fails() is None                # nothing committed holds them
            leaky = os.path.join(tmp, "notes.md")
            with open(leaky, "w") as f:
                f.write("pw=" + pw)
            g["_committable"] = lambda: [leaky]
            msg = _guard_fails()
            assert msg and "WIFI_PASSWORD" in msg and pw not in msg, "guard missed it"
            with open(fake, "w") as f:
                f.write("WIFI_PASSWORD = " + repr(pw) + " +\n")
            msg = _guard_fails()
            assert msg and "cannot check" in msg and pw not in msg, "unreadable file passed"
        finally:
            g["_committable"] = old_files
            if old_env is None:
                os.environ.pop(w.ENV, None)
            else:
                os.environ[w.ENV] = old_env


def test_history_guard_finds_a_removed_value():
    """A value committed once and removed by the next commit is still found,
    in the commit that held it."""
    _git("--version")                    # Skip first without subprocess or git
    import os
    import tempfile
    pw = "made-up-" + str(os.getpid()) + "-hist"     # built at run time: in no committed file
    with tempfile.TemporaryDirectory() as tmp:
        def commit(text, msg):
            with open(os.path.join(tmp, "boot.py"), "w") as f:
                f.write(text)
            assert _git("add", "boot.py", cwd=tmp).returncode == 0
            r = _git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
                     "-c", "commit.gpgsign=false", "commit", "-q", "-m", msg, cwd=tmp)
            assert r.returncode == 0, r.stderr
            return _git("rev-parse", "HEAD", cwd=tmp).stdout.decode().strip()
        assert _git("init", "-q", cwd=tmp).returncode == 0
        secrets = [("WIFI_PASSWORD", pw)]
        assert _history_leaks(secrets, cwd=tmp) == []                # no commits yet
        first = commit("wlan.connect('net', %r)\n" % pw, "first")
        commit("import secrets\n", "stop tracking it")
        assert _history_leaks(secrets, cwd=tmp) == [("WIFI_PASSWORD", first[:9], "boot.py")]
        assert _history_leaks([("WIFI_PASSWORD", pw + "x")], cwd=tmp) == []

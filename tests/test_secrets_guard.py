"""Wi-Fi credentials never reach git (docs/design/debug-mode.md decision 6).

The Wi-Fi name and password are read from every Wi-Fi file there is (the one
tools/wifi_setup.py saved, and an older secrets.py in the repo) with its
shared reader (never run, never printed), and no file git would commit
(tracked, or new and not ignored) may contain one; a failure names the
variable and the files only. Nor may the password be in any commit git knows
(every branch, fetched ones too; the name is broadcast, so it is checked in
committable files only): a pushed commit stays readable after a later one
removes the value, so that failure says to change the password on the router.
Without a Wi-Fi file both checks skip, so they also run on made-up ones
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


def _git(*args, cwd=None, input=None):
    """``git args`` in the repo root (or ``cwd``) -> CompletedProcess; Skip
    without git. ``input`` bytes go to git's stdin."""
    try:
        import subprocess
    except ImportError:
        raise Skip("needs subprocess (CPython)")
    try:
        r = subprocess.run(("git",) + args, capture_output=True, cwd=cwd, input=input)
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
        # pattern on stdin: never in argv (ps, /proc)
        r = _git("grep", "-l", "-F", "-f", "-", *revs, cwd=cwd, input=v.encode("utf-8"))
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
    """Only the password: the Wi-Fi name is broadcast by the access point, so
    an old name in history exposes nothing once the password has been changed
    (handoff item 3: change the password, no scrub)."""
    for path, secrets in _wifi_secrets():
        leaks = _history_leaks([s for s in secrets if s[0] == "WIFI_PASSWORD"])
        assert not leaks, (
            "a value from %s is in git history: %s. Anyone who can read the repo can "
            "read a pushed commit, so change the password on the router, then run "
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


def _guard_fails(test, wifi, **stubs):
    """The AssertionError message of guard ``test`` run with ``wifi`` as the
    saved Wi-Fi file and this module's globals ``stubs`` swapped in, or None."""
    import os
    w = _wifi_setup()
    g = globals()
    old_env, old = os.environ.get(w.ENV), dict((k, g[k]) for k in stubs)
    os.environ[w.ENV] = wifi
    g.update(stubs)
    try:
        test()
    except AssertionError as e:
        return str(e)
    finally:
        g.update(old)
        if old_env is None:
            os.environ.pop(w.ENV, None)
        else:
            os.environ[w.ENV] = old_env
    return None


def test_guard_checks_every_wifi_file():
    w = _wifi_setup()
    import os
    import tempfile
    guard = test_no_secret_in_committable_files
    pw = "made-up-" + str(os.getpid()) + "-pw"      # built at run time: in no committed file
    with tempfile.TemporaryDirectory() as tmp:
        fake = os.path.join(tmp, "wifi.py")
        w.save(fake, "MadeUpNet-" + str(os.getpid()), pw)
        assert _guard_fails(guard, fake) is None         # nothing committed holds them
        leaky = os.path.join(tmp, "notes.md")
        with open(leaky, "w") as f:
            f.write("pw=" + pw)
        msg = _guard_fails(guard, fake, _committable=lambda: [leaky])
        assert msg and "WIFI_PASSWORD" in msg and pw not in msg, "guard missed it"
        with open(fake, "w") as f:
            f.write("WIFI_PASSWORD = " + repr(pw) + " +\n")
        msg = _guard_fails(guard, fake, _committable=lambda: [leaky])
        assert msg and "cannot check" in msg and pw not in msg, "unreadable file passed"


def test_history_guard_finds_a_removed_value():
    """A value committed once and removed by the next commit is still found,
    in the commit that held it. The guard looks for the password only (the
    Wi-Fi name may stay in history once the password has been changed), and
    never puts it on git's command line, where ps shows it."""
    _git("--version")                    # Skip first without subprocess or git
    w = _wifi_setup()
    import os
    import tempfile
    net = "MadeUpNet-" + str(os.getpid())
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
        first = commit("wlan.connect(%r, %r)\n" % (net, pw), "first")
        commit("import secrets\n", "stop tracking it")
        assert _history_leaks(secrets, cwd=tmp) == [("WIFI_PASSWORD", first[:9], "boot.py")]
        assert _history_leaks([("WIFI_PASSWORD", pw + "x")], cwd=tmp) == []
        guard = test_no_secret_in_git_history
        here = lambda s, f=_history_leaks: f(s, cwd=tmp)
        argv = []
        spy = lambda *a, f=_git, **k: (argv.append(a), f(*a, **k))[1]
        fake = os.path.join(tmp, "wifi.py")
        w.save(fake, net, pw + "-new")                 # the password changed, the name kept
        assert _guard_fails(guard, fake, _history_leaks=here) is None
        w.save(fake, net, pw)
        msg = _guard_fails(guard, fake, _history_leaks=here, _git=spy)
        assert msg and "WIFI_PASSWORD in " + first[:9] in msg, msg
        assert "WIFI_SSID" not in msg and pw not in msg and net not in msg, msg
        assert argv and not [x for a in argv for x in a if pw in x], "password in git's argv"

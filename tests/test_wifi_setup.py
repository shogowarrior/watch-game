"""tools/wifi_setup.py: the Wi-Fi name and password for debug mode, saved
outside the repo (docs/design/debug-mode.md decision 6).

Every test uses made-up values and a temporary file (``WATCH_GAME_WIFI`` or
a temporary HOME), never a real Wi-Fi file. The tool is a CPython host
tool, so its tests skip under MicroPython; the watch reading the saved
format runs on both."""

from tests import Skip, fakes

# Made up, with quotes, a backslash and non-ASCII characters (the no-break
# space is saved as \xa0, which the watch must read back).
NAME = 'Café "Ü" \\ it\'s'
PASSWORD = 'p\\ss "wörd" it\'s \U0001f511'
SAVED = ("# Wi-Fi for debug mode, saved by python3 tools/wifi_setup.py. "
         "Private: never share or commit it.\n"
         "WIFI_SSID = 'Café \"Ü\" \\\\ it\\'s'\n"
         "WIFI_PASSWORD = 'p\\\\ss \"wörd\"\\xa0it\\'s \U0001f511'\n")


def _setup():
    try:
        from tools import wifi_setup as w      # CPython host tool only
    except ImportError:
        raise Skip("tools/wifi_setup.py is a CPython host tool")
    return w


def _setenv(key, value):
    import os
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


class _Env:
    """``with _Env(KEY=value)``: those environment variables (None: unset)
    inside the block only."""

    def __init__(self, **kv):
        self.kv = kv

    def __enter__(self):
        import os
        self.old = dict((k, os.environ.get(k)) for k in self.kv)
        for k, v in self.kv.items():
            _setenv(k, v)

    def __exit__(self, *exc):
        for k, v in self.old.items():
            _setenv(k, v)


def _hidden(text):
    """True when ``text`` shows neither made-up value, as typed or as saved."""
    return not any(v in text for v in (NAME, PASSWORD, repr(NAME)[1:-1], repr(PASSWORD)[1:-1],
                                       "Café", "wörd"))


def _run(w, argv, answers=None, passwords=None):
    """``w.main(argv)`` with ``answers`` typed at ``input`` and ``passwords``
    at ``getpass`` (each list is used up in place; EOFError when empty) ->
    (exit code, everything shown: output and prompts)."""
    import io
    import sys
    shown = io.StringIO()

    def typing(queue):
        def read(prompt=""):
            shown.write(prompt)
            if not queue:
                raise EOFError
            return queue.pop(0)
        return read
    old = (sys.stdout, sys.stderr, w.getpass)
    sys.stdout = sys.stderr = shown
    w.input = typing([] if answers is None else answers)       # shadows the builtin
    w.getpass = typing([] if passwords is None else passwords)
    try:
        rc = w.main(argv)
    except SystemExit as e:
        rc = e.code
    finally:
        sys.stdout, sys.stderr, w.getpass = old
        del w.input
    return rc, shown.getvalue()


def _tmpdir():
    try:
        import tempfile
        return tempfile.mkdtemp()          # CPython
    except ImportError:
        return "."                         # MicroPython: in-memory filesystem


def _write(path, text):
    with open(path, "wb") as f:
        f.write(text.encode("utf-8"))


def _read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8")


def test_saves_the_exact_file_privately():
    w = _setup()
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "new", "wifi.py")
        with _Env(WATCH_GAME_WIFI=path):
            rc, shown = _run(w, [], [NAME], [PASSWORD, PASSWORD])
        assert rc == 0, shown
        assert _read(path) == SAVED
        assert os.stat(os.path.dirname(path)).st_mode & 0o777 == 0o700
        assert os.stat(path).st_mode & 0o777 == 0o600
        assert os.listdir(os.path.dirname(path)) == ["wifi.py"]     # no temporary file left
        assert "Saved in %s" % path in shown and _hidden(shown), shown
        assert w.read_wifi(path) == (NAME, PASSWORD)
        os.chmod(path, 0o644)                       # saved again: replaced, private again
        with _Env(WATCH_GAME_WIFI=path):
            rc, _ = _run(w, [], ["Other-Net"], ["other-pass-123", "other-pass-123"])
        assert rc == 0 and w.read_wifi(path) == ("Other-Net", "other-pass-123")
        assert os.stat(path).st_mode & 0o777 == 0o600


def test_watch_reads_the_saved_file():
    """hal/debuglink.read_secrets (exec on the watch) gets the same values
    back from the saved format, on both runtimes."""
    import os
    fakes.install()
    from hal import debuglink as dl
    d = _tmpdir()
    p = d + "/_wifi.py"
    _write(p, SAVED)
    try:
        assert dl.read_secrets(p) == (NAME, PASSWORD)
    finally:
        os.remove(p)
        if d != ".":
            os.rmdir(d)


def test_default_place_and_watch_game_wifi():
    w = _setup()
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        home = os.path.join(tmp, "home")
        default = os.path.join(home, ".config", "watch-game", "wifi.py")
        for env in (None, ""):
            with _Env(HOME=home, WATCH_GAME_WIFI=env):
                assert w.wifi_path() == default, env
        with _Env(HOME=home, WATCH_GAME_WIFI=os.path.join(tmp, "elsewhere.py")):
            assert w.wifi_path() == os.path.join(tmp, "elsewhere.py")
        with _Env(HOME=home, WATCH_GAME_WIFI="~/mine.py"):
            assert w.wifi_path() == os.path.join(home, "mine.py")
        with _Env(HOME=home, WATCH_GAME_WIFI=None):
            rc, shown = _run(w, [], [NAME], [PASSWORD, PASSWORD])
        assert rc == 0 and _read(default) == SAVED, shown
        assert os.stat(os.path.dirname(default)).st_mode & 0o777 == 0o700


def test_mismatched_passwords_ask_again():
    w = _setup()
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "wifi.py")
        passwords = [PASSWORD, "made-up-typo-1", PASSWORD, PASSWORD]
        with _Env(WATCH_GAME_WIFI=path):
            rc, shown = _run(w, [], [NAME], passwords)
        assert rc == 0 and passwords == [], shown
        assert "not the same" in shown and w.read_wifi(path) == (NAME, PASSWORD)
        assert "made-up-typo-1" not in shown and _hidden(shown)


def test_unusable_name_or_password_asks_again():
    w = _setup()
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "wifi.py")
        answers = ["", "N" * 33, NAME]
        passwords = ["short12", "short12", "x" * 64, "x" * 64, PASSWORD, PASSWORD]
        with _Env(WATCH_GAME_WIFI=path):
            rc, shown = _run(w, [], answers, passwords)
        assert rc == 0 and answers == [] and passwords == [], shown
        assert "empty" in shown and "too long" in shown and "8 to 63" in shown
        assert "short12" not in shown and w.read_wifi(path) == (NAME, PASSWORD)


def test_open_network_needs_a_yes():
    w = _setup()
    import os
    import tempfile
    fakes.install()
    from hal import debuglink as dl
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "wifi.py")
        answers = [NAME, "n", "yes"]
        with _Env(WATCH_GAME_WIFI=path):
            rc, shown = _run(w, [], answers, ["", "", "", ""])
        assert rc == 0 and answers == [] and "open network" in shown, shown
        assert w.read_wifi(path) == (NAME, "") and dl.read_secrets(path) == (NAME, "")


def test_name_and_password_rules():
    w = _setup()
    for name in ("N", "N" * 32, "é" * 16, NAME):
        assert w.check_name(name) is None, name
    for name in ("", "N" * 33, "é" * 16 + "N", "\udce9"):   # 33 bytes; undecodable byte
        assert w.check_name(name), name
    for pw in ("", "p" * 8, "p" * 63, "ab" * 32, "AB09" * 16, "é" * 31 + "p", PASSWORD):
        assert w.check_password(pw) is None, pw
    for pw in ("p" * 7, "p" * 64, "ab" * 32 + "c", "é" * 32, "\udce9" * 8):
        assert w.check_password(pw), pw


def test_ctrl_d_saves_nothing():
    w = _setup()
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "wifi.py")
        with _Env(WATCH_GAME_WIFI=path):
            rc, shown = _run(w, [], [NAME], [PASSWORD])       # input ends at the second prompt
        assert rc == 1 and "Nothing saved" in shown and not os.path.exists(path), shown
        assert os.listdir(tmp) == []


def test_forget_deletes_the_file():
    w = _setup()
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "wifi.py")
        _write(path, SAVED)
        with _Env(WATCH_GAME_WIFI=path):
            rc, shown = _run(w, ["--forget"])
            assert rc == 0 and "Deleted %s" % path in shown and not os.path.exists(path)
            rc, shown = _run(w, ["--forget"])
            assert rc == 0 and "Nothing to delete" in shown


def test_check_says_saved_private_readable():
    w = _setup()
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "wifi.py")
        with _Env(WATCH_GAME_WIFI=path):
            rc, shown = _run(w, ["--check"])
            assert rc == 1 and "No Wi-Fi saved yet" in shown and w.SETUP in shown, shown
            _run(w, [], [NAME], [PASSWORD, PASSWORD])
            rc, shown = _run(w, ["--check"])
            assert rc == 0 and "private: yes" in shown and "a password" in shown, shown
            assert _hidden(shown)
            os.chmod(path, 0o644)
            rc, shown = _run(w, ["--check"])
            assert rc == 1 and "private: NO" in shown and "chmod 600" in shown, shown
            os.chmod(path, 0o600)
            _write(path, "WIFI_SSID = 'made-up-net'\nWIFI_PASSWORD = 12345678\n")
            rc, shown = _run(w, ["--check"])
            assert rc == 1 and "readable: NO" in shown and "quotes" in shown, shown
            assert "made-up-net" not in shown and "12345678" not in shown
            _write(path, "WIFI_SSID = 'made-up-net'\nWIFI_PASSWORD = ''\n")
            rc, shown = _run(w, ["--check"])
            assert rc == 0 and "no password (an open network)" in shown, shown


def test_lookup_order_and_legacy_hint():
    """deploy.py copies the saved file, else the repo's older secrets.py
    (with a hint to move it); the guard checks both."""
    w = _setup()
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        saved, legacy = os.path.join(tmp, "wifi.py"), os.path.join(tmp, "secrets.py")
        with _Env(WATCH_GAME_WIFI=saved):
            assert w.find_wifi(tmp) == (None, None) and w.wifi_files(tmp) == []
            _write(legacy, SAVED)
            path, hint = w.find_wifi(tmp)
            assert path == legacy and "secrets.py" in hint and w.SETUP in hint, hint
            assert w.wifi_files(tmp) == [legacy]
            _write(saved, SAVED)
            assert w.find_wifi(tmp) == (saved, None)
            assert w.wifi_files(tmp) == [saved, legacy]
            root = w.ROOT
            w.ROOT = tmp
            try:
                _, shown = _run(w, ["--check"])
            finally:
                w.ROOT = root
            assert "Note: " + w.LEGACY_HINT in shown, shown


def test_read_wifi_never_runs_the_file_or_quotes_it():
    w = _setup()
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path, ran = os.path.join(tmp, "wifi.py"), os.path.join(tmp, "ran")
        _write(path, "open(%r, 'w').close()\nWIFI_SSID = 'made-up-net'\n" % ran)
        assert w.read_wifi(path) == ("made-up-net", "") and not os.path.exists(ran)
        _write(path, "WIFI_SSID = 'made-up-net'\nWIFI_PASSWORD = None\n")
        assert w.read_wifi(path) == ("made-up-net", "")
        for text, why in (("WIFI_SSID = 'made-up-net'\nWIFI_PASSWORD = 12345678\n", "quotes"),
                          ("WIFI_SSID = 'made' + '-up-net'\n", "no Wi-Fi name"),
                          ("WIFI_SSID = ''\n", "no Wi-Fi name"),
                          ("WIFI_PASSWORD = 'made-up-pass'\n", "no Wi-Fi name"),
                          ("WIFI_SSID = 'made-up-net'\nWIFI_PASSWORD = 'made-up-pass' +\n",
                           "not valid Python")):
            _write(path, text)
            try:
                w.read_wifi(path)
                assert False, text
            except ValueError as e:
                m = str(e)
                assert why in m and "made" not in m and "12345678" not in m, m
        os.remove(path)
        try:
            w.read_wifi(path)
            assert False, "a missing file was read"
        except ValueError as e:
            assert "cannot be opened" in str(e)


def test_read_wifi_shows_no_warning_from_the_file():
    w = _setup()
    import os
    import tempfile
    import warnings
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "wifi.py")
        # a hand-written password with a bad escape: its warning would quote it
        _write(path, 'WIFI_SSID = "made-up-net"\nWIFI_PASSWORD = "x\\dy-made-up"\n')
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            assert w.read_wifi(path) == ("made-up-net", "x\\dy-made-up")
        assert not seen, "%d warnings" % len(seen)     # the count only, never the text

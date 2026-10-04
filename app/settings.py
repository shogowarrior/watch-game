"""Settings kept on the watch's flash across reboots (ui-spec §4A Choosing).

Only the field theme so far: its name, alone in the file ``/theme``. The
runtime reads it before the game loop and its watchdog start (the saved
theme loads whole at boot) and writes it when the MENU closes with another
theme chosen (app/runtime.py ``_save_theme``).
"""

from finder import tuning as T

THEME_FILE = "/theme"


def load_theme(path=THEME_FILE):
    """The saved theme name (a tuning.THEME_NAMES constant); the default
    when the file is missing or unreadable, or names no theme."""
    try:
        with open(path) as f:
            name = f.read(32).strip()
    except (OSError, ValueError):       # missing; not text (CPython: UnicodeDecodeError)
        return T.THEME_DEFAULT
    for n in T.THEME_NAMES:
        if n == name:
            return n
    return T.THEME_DEFAULT


def save_theme(name, path=THEME_FILE):
    """Write ``name``; False when the file could not be written."""
    try:
        with open(path, "w") as f:
            f.write(name)
    except OSError:
        return False
    return True

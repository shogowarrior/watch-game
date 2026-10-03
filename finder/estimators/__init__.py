"""Range (distance) estimators fed by RSSI + IMU motion hints.

All estimators implement the interface in ``base.py`` so the bake-off
(tools/bakeoff.py) and the game can swap them. ``make()`` builds the game's
default; see docs/estimation/bakeoff.md for why it was chosen.
"""

DEFAULT = "kalman2"
NAMES = ("ema", "median_ema", "kalman1d", "kalman2", "particle")


def make(name=None, **kw):
    """New ``finder.estimators.<name>.Estimator(**kw)``; ``name`` defaults to DEFAULT."""
    name = name or DEFAULT
    if "." in name or name.startswith("_") or name == "base":
        raise ValueError("bad estimator name: " + name)
    full = "finder.estimators." + name
    mod = __import__(full)
    for part in full.split(".")[1:]:
        mod = getattr(mod, part)
    return mod.Estimator(**kw)

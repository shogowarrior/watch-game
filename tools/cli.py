"""Tiny ``--key value`` / ``--key=value`` parser shared by host tools.

No game imports, so a tool can parse its options without loading the bake-off.
Runs on CPython and on MicroPython (tools/mpy/run.mjs).
"""


def parse_args(args, o, flags=()):
    """``--key value`` / ``--key=value`` into a copy of the defaults ``o``;
    keys in ``flags`` take no value and become True. Raises ValueError on an
    unknown option or a missing value."""
    o = dict(o)
    i = 0
    while i < len(args):
        a = args[i]
        k, v = a[2:], None
        if "=" in k:
            k, v = k.split("=", 1)
        if not a.startswith("--") or k not in o:
            raise ValueError("unknown option " + a)
        if k in flags:
            o[k] = True
        else:
            if v is None:
                i += 1
                if i >= len(args):
                    raise ValueError("missing value for " + a)
                v = args[i]
            o[k] = v
        i += 1
    return o

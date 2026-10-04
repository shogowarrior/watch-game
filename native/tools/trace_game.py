"""Record the Python game's calls for the C++ port to replay (CPython only).

    python3 native/tools/trace_game.py OUTDIR [--tests test_link,test_game,...]

Runs Python tests (TESTS, or --tests) with every class in TRACED wrapped, and
writes one JSON-lines file per class, OUTDIR/<module>.<Class>.jsonl:

    {"new": 3, "a": [...]}                     # object 3 constructed with these arguments
    {"o": 3, "m": "update", "a": [...], "r": ..., "s": {...}}   # a call, its result, then state
    {"o": 3, "cb": "blank_fn", "a": [...], "r": ...}           # it called a function it was given
    {"o": 3, "set": {...}}                                     # its state changed between calls

Arguments are bound to the signature and defaults are filled in, so every call
lists every parameter in order. Only a method's outermost call on an object is
recorded (one it makes on itself runs inside it). A function passed to a traced
object is recorded each time the object calls it, before the line of the call
it happened in, so a replay can answer it. A "set" line comes before a call
when the object's state changed since its previous line: other code wrote its
attributes, as the game does to the beacon it sends (objects in it are written
with their fields, as arguments are). "s" holds the public attributes and
properties that changed since the object's previous line (all of them after
the constructor). Values: JSON numbers (a float is always
written with a "." or an exponent, so ints and floats stay apart; NaN and
Infinity as such), {"b": "hex"} for bytes, {"@": "Class", ...fields} for
other objects (namedtuples by field, the rest by public attribute), and
{"@ref": "module.Class#id"} for a traced object; in "s", an array or bytearray
is {"t": typecode, "n": length, "crc": CRC-32 of its bytes}. A test that fails
under the recorder fails the run.

native/test/run.py runs this into a temporary folder and the C++ trace tests
replay it, so the check always follows the current Python.
"""

import array
import functools
import importlib
import inspect
import json
import os
import sys
import types
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from cli import parse_args  # noqa: E402

# (module, class) pairs recorded, leaves first.
TRACED = (
    ("finder.proto", "Beacon"),
    ("finder.link", "Xorshift16"),
    ("finder.link", "TxScheduler"),
    ("finder.link", "LinkMonitor"),
    ("finder.estimators.base", "PathLoss"),
    ("finder.estimators.base", "MotionInfo"),
    ("finder.estimators.kalman2", "Estimator"),
    ("finder.motion", "MotionTracker"),
    ("finder.gestures", "GestureRecognizer"),
    ("finder.haptic_patterns", "BlankWindow"),
    ("finder.haptic_patterns", "HapticPlayer"),
    ("finder.menu", "Menu"),
    ("finder.proximity", "ZoneTracker"),
    ("finder.proximity", "DeliveryMeter"),
    ("finder.proximity", "TrendGate"),
    ("finder.proximity", "Proximity"),
    ("finder.session", "MotionSnap"),
    ("finder.session", "PeerView"),
    ("finder.session", "LiveMirror"),
    ("finder.pairing", "Calibrator"),
    ("finder.pairing", "Pairing"),
    ("finder.arrow", "Arrow"),
    ("finder.scan", "ScanSession"),
    ("finder.game", "Game"),
)

MISSING = object()
_ids = {}          # id(obj) -> "module.Class#n"
_keep = []         # every traced object, so no id is reused
_count = {}
_depth = {}        # id(obj) -> nesting of traced calls on it
_last = {}         # id(obj) -> its last recorded state
_out = {}          # "module.Class" -> open file


def enc(v, depth=0, deep=False):
    """A Python value as JSON-ready data (see the module docstring). Objects
    nested deeper than two levels keep only their class name. ``deep``
    (arguments and results) writes a traced object's fields after its
    "@ref" too, so a replay sees what the call saw; otherwise (state) an
    array or bytearray is written as its typecode, length and CRC-32."""
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if not deep and isinstance(v, (array.array, bytearray)):
        return {"t": getattr(v, "typecode", "B"), "n": len(v), "crc": zlib.crc32(v)}
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"b": bytes(v).hex()}
    if isinstance(v, (list, tuple, array.array)) and not hasattr(v, "_fields"):
        return [enc(x, depth, deep) for x in v]
    if isinstance(v, dict):
        return {str(k): enc(x, depth, deep) for k, x in v.items()}
    ref = _ids.get(id(v))
    if ref is not None and not deep:
        return {"@ref": ref}
    if _routine(v):
        return {"@fn": getattr(v, "__qualname__", "?")}
    out = {"@": type(v).__name__}
    if ref is not None:
        out["@ref"] = ref
    if depth < 2:
        for k in (v._fields if hasattr(v, "_fields") else _public(v)):
            out[k] = enc(getattr(v, k), depth + 1)
    return out


_class_names = {}   # type -> its public slots and properties
_sigs = {}          # function -> its signature
_ROUTINES = (types.FunctionType, types.MethodType, types.BuiltinFunctionType, types.BuiltinMethodType)


def _routine(v):
    return type(v) in _ROUTINES


def _public(obj):
    """Public attribute and property names of ``obj``, sorted."""
    t = type(obj)
    fixed = _class_names.get(t)
    if fixed is None:
        fixed = _class_names[t] = set(
            k for c in t.__mro__ for k in list(getattr(c, "__slots__", ()))
            + [k for k, a in c.__dict__.items() if isinstance(a, property)] if not k.startswith("_"))
    names = set(k for k in fixed if hasattr(obj, k))
    d = getattr(obj, "__dict__", None)
    if d:
        names.update(k for k in d if not k.startswith("_"))
    return sorted(names)


def _state(obj):
    s = {}
    for k in _public(obj):
        try:
            v = getattr(obj, k)
        except Exception:  # noqa: BLE001 - a property that needs more state
            continue
        if not _routine(v):
            s[k] = enc(v)
    return s


def _write(cls_key, rec):
    _out[cls_key].write(json.dumps(rec, separators=(",", ":")) + "\n")


def _changed(obj):
    now = _state(obj)
    before = _last.get(id(obj), {})
    _last[id(obj)] = now
    return {k: v for k, v in now.items() if before.get(k, MISSING) != v}


def _num(obj):
    return int(_ids[id(obj)].split("#")[1])


def _callback(cls_key, obj, name, f):
    """``f`` as passed to ``obj``: each call is recorded on obj's trace."""
    @functools.wraps(f)
    def call(*args, **kw):
        r = f(*args, **kw)
        _write(cls_key, {"o": _num(obj), "cb": name, "a": [enc(x, deep=True) for x in args], "r": enc(r, deep=True)})
        return r
    return call


def _bind(cls_key, fn, obj, args, kw):
    """The call bound to fn's signature, with functions wrapped by _callback,
    and its arguments encoded."""
    sig = _sigs.get(fn)
    if sig is None:
        sig = _sigs[fn] = inspect.signature(fn)
    b = sig.bind(obj, *args, **kw)
    b.apply_defaults()
    for k, v in list(b.arguments.items())[1:]:
        if _routine(v):
            b.arguments[k] = _callback(cls_key, obj, k, v)
    return b, [enc(x, deep=True) for x in list(b.arguments.values())[1:]]


def _wrap_init(cls_key, fn):
    @functools.wraps(fn)
    def init(self, *args, **kw):
        k = id(self)
        if k in _ids or _depth.get(k, 0):        # a base class's __init__, inside the outer one
            return fn(self, *args, **kw)
        n = _count[cls_key] = _count.get(cls_key, 0) + 1
        _ids[k] = "%s#%d" % (cls_key, n)
        _keep.append(self)
        b, a = _bind(cls_key, fn, self, args, kw)
        _depth[k] = 1
        try:
            fn(*b.args, **b.kwargs)
        finally:
            _depth[k] = 0
        _write(cls_key, {"new": n, "a": a, "s": _changed(self)})
    return init


def _wrap(cls_key, name, fn):
    @functools.wraps(fn)
    def method(self, *args, **kw):
        k = id(self)
        if _depth.get(k, 0) or k not in _ids:
            return fn(self, *args, **kw)
        written = _changed(self)
        if written:   # deep, so a replay can rebuild an object put in an attribute
            _write(cls_key, {"o": _num(self), "set": {f: enc(getattr(self, f), deep=True) for f in written}})
        b, a = _bind(cls_key, fn, self, args, kw)
        _depth[k] = 1
        try:
            r = fn(*b.args, **b.kwargs)
        finally:
            _depth[k] = 0
        _write(cls_key, {"o": _num(self), "m": name, "a": a, "r": enc(r, deep=True), "s": _changed(self)})
        return r
    return method


def install(outdir):
    """Wrap every TRACED class; their lines go to files in ``outdir``."""
    for mod, name in TRACED:
        cls = getattr(importlib.import_module(mod), name)
        key = "%s.%s" % (mod, name)
        _out[key] = open(os.path.join(outdir, key + ".jsonl"), "w")
        for attr, fn in inspect.getmembers(cls, inspect.isfunction):   # inherited ones too
            if attr == "__init__":
                setattr(cls, attr, _wrap_init(key, fn))
            elif not attr.startswith("_"):
                setattr(cls, attr, _wrap(key, attr, fn))


# The tests run under the recorder by default: those of the modules the C++
# port has reached (add a module's when its port lands). Recording costs about
# 50 us a call, so a porter runs the wider ones with --tests (test_game,
# test_episode, test_app_runtime, test_webhost: a minute or more each).
TESTS = ("test_proto", "test_link")


def main(argv):
    pos = [a for a in argv[:1] if not a.startswith("--")]
    if not pos:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    o = parse_args(argv[1:], {"tests": ",".join(TESTS)})
    os.makedirs(pos[0], exist_ok=True)
    install(pos[0])
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import runner
    failed = runner.run(o["tests"].split(","))
    for f in _out.values():
        f.close()
    print("trace_game: %d classes, %d objects in %s" % (len(_out), len(_ids), pos[0]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

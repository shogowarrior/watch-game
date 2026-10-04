"""Record the Python game's calls for the C++ port to replay (CPython only).

    python3 native/tools/trace_game.py OUTDIR [--tests test_link,test_game,...]
                                              [--classes finder.link.LinkMonitor,...]

Runs Python tests (TESTS, or --tests) with the classes in TRACED wrapped (or
only those named in --classes), and writes one JSON-lines file per class,
OUTDIR/<module>.<Class>.jsonl:

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
other objects (namedtuples by field, the rest by public attribute; a
test's stand-in class's own values too), and
{"@ref": "module.Class#id"} for an object of a TRACED class (recorded in
this test or not); in "s", an array or bytearray
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
    if type(v) in _PLAIN or isinstance(v, (bool, int, float, str)):
        return v
    if not deep and isinstance(v, (array.array, bytearray)):
        return {"t": getattr(v, "typecode", "B"), "n": len(v), "crc": zlib.crc32(v)}
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"b": bytes(v).hex()}
    if isinstance(v, (list, tuple, array.array)) and not hasattr(v, "_fields"):
        return [enc(x, depth, deep) for x in v]
    if isinstance(v, dict):
        return {str(k): enc(x, depth, deep) for k, x in v.items()}
    if hasattr(v, "_fields"):
        return _namedtuple(v, depth, deep)
    ref = _ids.get(id(v))
    if ref is not None and not deep:
        return {"@ref": ref}
    if _routine(v):
        return {"@fn": getattr(v, "__qualname__", "?")}
    out = {"@": type(v).__name__}
    if ref is not None:
        out["@ref"] = ref
    if depth < 2:
        for k, x in _items(v):
            out[k] = enc(x, depth + 1)
    return out


_tuples = {}   # (id, depth, deep) -> (namedtuple, its encoding): a frame's RenderParams is written 3 times


def _namedtuple(v, depth, deep):
    key = (id(v), depth, deep)
    hit = _tuples.get(key)
    if hit is not None and hit[0] is v:
        return hit[1]
    out = {"@": type(v).__name__}
    if depth < 2:
        for k in v._fields:
            out[k] = enc(getattr(v, k), depth + 1)
    try:
        hash(v)       # immutable all through: its encoding cannot change
    except TypeError:
        return out
    if len(_tuples) > 4096:
        _tuples.clear()
    _tuples[key] = (v, out)
    return out


_PLAIN = frozenset((type(None), bool, int, float, str))
_class_names = {}   # type -> its public slots and properties
_names = {}         # (type, instance attribute names) -> public names, sorted
_traced = set()     # the TRACED classes
_sigs = {}          # function -> its signature
_ROUTINES = (types.FunctionType, types.MethodType, types.BuiltinFunctionType, types.BuiltinMethodType)


def _routine(v):
    return type(v) in _ROUTINES


def _items(obj):
    """(name, value) of each public attribute and property of ``obj``, sorted
    by name, and for a test's stand-in (a class not in TRACED) the values its
    class holds too; one that cannot be read now (an unset slot, a property
    that needs more state) is left out."""
    t = type(obj)
    d = getattr(obj, "__dict__", None)
    key = (t, tuple(d) if d else ())
    names = _names.get(key)
    if names is None:
        fixed = _class_names.get(t)
        if fixed is None:
            fixed = _class_names[t] = set(
                k for c in t.__mro__ for k in list(getattr(c, "__slots__", ()))
                + [k for k, a in c.__dict__.items() if isinstance(a, property)
                   or t not in _traced and not callable(a) and not isinstance(a, (staticmethod, classmethod))]
                if not k.startswith("_"))
        names = _names[key] = sorted(fixed.union(k for k in key[1] if not k.startswith("_")))
    out = []
    for k in names:
        try:
            out.append((k, getattr(obj, k)))
        except Exception:  # noqa: BLE001
            pass
    return out


def _state(obj):
    return {k: enc(v) for k, v in _items(obj) if not _routine(v)}


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


def _new_id(cls_key, obj):
    n = _count[cls_key] = _count.get(cls_key, 0) + 1
    _ids[id(obj)] = "%s#%d" % (cls_key, n)
    _keep.append(obj)
    return n


def _wrap_init(cls_key, fn):
    @functools.wraps(fn)
    def init(self, *args, **kw):
        k = id(self)
        if k in _ids or _depth.get(k, 0):        # a base class's __init__, inside the outer one
            return fn(self, *args, **kw)
        n = _new_id(cls_key, self)
        b, a = _bind(cls_key, fn, self, args, kw)
        _depth[k] = 1
        try:
            fn(*b.args, **b.kwargs)
        finally:
            _depth[k] = 0
        _write(cls_key, {"new": n, "a": a, "s": _changed(self)})
    return init


def _wrap_id(cls_key, fn):
    """``__init__`` of a TRACED class this test does not record: the object
    gets an id all the same, so a recorded object holding it shows it as a
    ref, whichever classes are recorded."""
    @functools.wraps(fn)
    def init(self, *args, **kw):
        if id(self) not in _ids:
            _new_id(cls_key, self)
        return fn(self, *args, **kw)
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


_wrappers = {}   # "module.Class" -> (class, {attr: (function, its wrapper, when not recorded)})


def install(outdir):
    """Open one file per TRACED class in ``outdir`` and build its wrappers."""
    for mod, name in TRACED:
        cls = getattr(importlib.import_module(mod), name)
        _traced.add(cls)
        key = "%s.%s" % (mod, name)
        _out[key] = open(os.path.join(outdir, key + ".jsonl"), "w")
        w = {}
        for attr, fn in inspect.getmembers(cls, inspect.isfunction):   # inherited ones too
            if attr == "__init__":
                w[attr] = (fn, _wrap_init(key, fn), _wrap_id(key, fn))
            elif not attr.startswith("_"):
                w[attr] = (fn, _wrap(key, attr, fn), fn)
        _wrappers[key] = (cls, w)


def record(keys, unwrap=False):
    """Record the classes in ``keys`` (None: all) and no others; ``unwrap``:
    the classes as they were."""
    for key, (cls, w) in _wrappers.items():
        for attr, (fn, wrapper, off) in w.items():
            setattr(cls, attr, fn if unwrap else wrapper if keys is None or key in keys else off)


# The Python tests run under the recorder by default, each with the classes it
# records (None: all): those of the modules the C++ port has reached. Add a
# module's when its port lands. Recording costs about 50 us a call, so a wide
# test (test_game, test_episode: a minute or more with every class) records
# only the classes no unit test covers.
TESTS = (("test_proto", None), ("test_link", None), ("test_proximity", None),
         ("test_arrow", ("finder.arrow.Arrow",)),
         ("test_episode", ("finder.arrow.Arrow",)),    # real scan values: float order
         ("test_game", ("finder.pairing.Pairing", "finder.pairing.Calibrator")))


def main(argv):
    pos = [a for a in argv[:1] if not a.startswith("--")]
    if not pos:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    o = parse_args(argv[1:], {"tests": "", "classes": ""})
    plan = [(t, None) for t in o["tests"].split(",")] if o["tests"] else TESTS
    only = set(o["classes"].split(",")) if o["classes"] else None
    os.makedirs(pos[0], exist_ok=True)
    install(pos[0])
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import runner
    failed = 0
    for test, keys in plan:
        record(only if keys is None else set(keys) & only if only else set(keys))
        failed += runner.run([test])
    record((), unwrap=True)
    for f in _out.values():
        f.close()
    print("trace_game: %d classes, %d objects in %s" % (len(_out), len(_ids), pos[0]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

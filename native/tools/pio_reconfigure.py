"""PlatformIO pre-script for the ESP-IDF builds (native/idf, native/idf/game,
native/idf/lvgl): configure again when a shared component's sources change.

PlatformIO runs CMake again only when the project's own CMakeLists.txt or its
sdkconfig changed. A source file added to a component that lives outside the
project (native/core, native/esp32_shared, native/idf/components), or a
change to such a component's CMakeLists.txt, left an existing build linking
without it ("undefined reference to hm::..."). This script keeps a hash of
those components' .cpp names and CMakeLists.txt texts in the build directory
and, when it changes, removes CMakeCache.txt, which makes PlatformIO configure
again. Standard library only.
"""
import hashlib
import os

Import("env")  # noqa: F821 - SCons provides Import and env

COMPONENTS = ("native/core", "native/esp32_shared", "native/idf/components")


def _root(d):
    """The repo root: the first directory up from d that holds native/core."""
    while not os.path.isdir(os.path.join(d, "native", "core")):
        up = os.path.dirname(d)
        if up == d:
            raise RuntimeError("pio_reconfigure: no native/core above " + d)
        d = up
    return d


def _hash(root):
    h = hashlib.sha256()
    for c in COMPONENTS:
        for dp, dns, fns in os.walk(os.path.join(root, c)):
            dns.sort()
            for f in sorted(fns):
                p = os.path.join(dp, f)
                if f.endswith(".cpp"):
                    h.update(os.path.relpath(p, root).encode() + b"\n")
                elif f == "CMakeLists.txt":
                    with open(p, "rb") as fh:
                        h.update(fh.read())
    return h.hexdigest()


def main(env):
    build = env.subst("$BUILD_DIR")
    stamp = os.path.join(build, "hm_components.txt")
    now = _hash(_root(env.subst("$PROJECT_DIR")))
    try:
        with open(stamp) as f:
            was = f.read()
    except OSError:
        was = None
    if was == now:
        return
    cache = os.path.join(build, "CMakeCache.txt")
    if os.path.isfile(cache):
        os.remove(cache)
    os.makedirs(build, exist_ok=True)
    with open(stamp, "w") as f:
        f.write(now)


main(env)  # noqa: F821

"""Fixture for tests/test_runner.py: a script that leaves its work in
``__steps__`` for tools/mpy/run.mjs to resume, one runPython call per step.

The first step makes about 4 MB of garbage and asks for a collect, which on
the WebAssembly port only runs once the call returns. The second step prints
how many bytes the heap kept since the first began and exits with code 3.
"""
import gc
import sys


def _steps():
    gc.collect()
    base = gc.mem_alloc()
    junk = [bytearray(1000) for _ in range(4000)]
    junk = None
    gc.collect()
    yield
    print("steps: %d bytes kept" % (gc.mem_alloc() - base))
    sys.exit(3)


__steps__ = _steps()

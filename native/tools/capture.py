#!/usr/bin/env python3
"""Restart a watch over its USB serial port and save what it prints.

    python3 native/tools/capture.py PORT OUT [--seconds 150] [--until "SF done"] [--no-reset]

Standard library only (POSIX termios), so it runs on the Mac with nothing else
installed. It pulses RTS (wired to EN on the T-Watch, as esptool's hard reset)
so the log starts at boot, keeps every line until the --until text appears or
--seconds pass, and echoes the "SF " lines. Exit 0 if --until was seen, else 1.
"""
import fcntl
import os
import select
import struct
import sys
import termios
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
from cli import parse_args  # noqa: E402

# ioctl numbers when the termios module lacks them (Linux, macOS)
_MBIS = getattr(termios, "TIOCMBIS", 0x8004746C if sys.platform == "darwin" else 0x5416)
_MBIC = getattr(termios, "TIOCMBIC", 0x8004746B if sys.platform == "darwin" else 0x5417)
_RTS = getattr(termios, "TIOCM_RTS", 0x004)
_DTR = getattr(termios, "TIOCM_DTR", 0x002)


def open_port(path):
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    cc = termios.tcgetattr(fd)[6]
    cc[termios.VMIN] = 0
    cc[termios.VTIME] = 0
    cflag = termios.CS8 | termios.CREAD | termios.CLOCAL
    termios.tcsetattr(fd, termios.TCSANOW, [0, 0, cflag, 0, termios.B115200, termios.B115200, cc])
    return fd


def _line(fd, bit, on):
    fcntl.ioctl(fd, _MBIS if on else _MBIC, struct.pack("I", bit))


def reset(fd):
    """EN low for 100 ms with IO0 high: a normal boot from flash."""
    _line(fd, _DTR, False)
    _line(fd, _RTS, True)
    time.sleep(0.1)
    _line(fd, _RTS, False)


def capture(fd, out, seconds, until, echo=None):
    """Read lines into the open file ``out``; True once a line contains ``until``."""
    end = time.monotonic() + seconds
    buf = b""
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return False
        if not select.select([fd], [], [], min(left, 0.5))[0]:
            continue
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            continue
        except OSError:          # the port went away (unplugged, or a pty closed)
            return False
        if not chunk:
            continue
        buf += chunk
        *lines, buf = buf.split(b"\n")
        for raw in lines:
            line = raw.decode("utf-8", "replace").rstrip("\r")
            out.write(line + "\n")
            out.flush()
            if echo and line.startswith("SF "):
                echo(line)
            if until and until in line:
                return True


def main(argv):
    pos = [a for a in argv[:2] if not a.startswith("--")]
    if len(pos) != 2:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    o = parse_args(argv[2:], {"seconds": "150", "until": "SF done", "no-reset": False}, flags=("no-reset",))
    fd = open_port(pos[0])
    try:
        if not o["no-reset"]:
            reset(fd)
        if os.path.dirname(pos[1]):
            os.makedirs(os.path.dirname(pos[1]), exist_ok=True)
        with open(pos[1], "w") as out:
            ok = capture(fd, out, float(o["seconds"]), o["until"], print)
    finally:
        os.close(fd)
    print("saved %s (%s)" % (pos[1], "complete" if ok else "timed out"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

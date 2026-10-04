#!/usr/bin/env python3
"""Boot a PlatformIO ESP32 build in Espressif's QEMU and check its serial log.

    python3 native/tools/qemu_run.py DIR [--env E] [--until "HM hello"] [--seconds 60]
                                     [--out LOG] [--allow-error]
    python3 native/tools/qemu_run.py --install

DIR is a PlatformIO project (its .pio/build/<env>/) or a build directory holding
bootloader.bin, partitions.bin and firmware.bin. Before booting, it checks the
flash layout the way a flash would land: the bootloader ends before the
partition table, the app fits its partition, everything fits the flash size.
Then it boots the merged image headless and passes when a line contains
--until, and fails on a panic, a reboot after the first "HM " line, an
"HM error" line (unless --allow-error) or after --seconds.

QEMU models the ESP32 but not the watch: no screen, PMU or motion sensor, so
the shared bench stops at "HM error what=axp202" and the default --until is
"HM hello". Times it prints are not the watch's. --install fetches Espressif's
build (checked against ESP-IDF's tools.json) into ~/.espressif/tools/.
Standard library only.
"""
import glob
import hashlib
import json
import os
import platform
import select
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
from cli import parse_args  # noqa: E402

BOOTLOADER_AT = 0x1000      # ESP32 ROM loads the second-stage bootloader from here
TABLE_AT = 0x8000           # ESP-IDF's default CONFIG_PARTITION_TABLE_OFFSET
TABLE_MAX = 0xC00
FLASH_SIZES = {0: 1 << 20, 1: 2 << 20, 2: 4 << 20, 3: 8 << 20, 4: 16 << 20}
QEMU_SIZES = (2 << 20, 4 << 20, 8 << 20, 16 << 20)
CRASH = ("Guru Meditation Error", "abort() was called", "Backtrace:", "Stack canary watchpoint",
         "stack overflow", "assert failed:", "Brownout detector")
TOOLS = os.path.expanduser("~/.espressif/tools/qemu-xtensa")


def parse_table(data):
    """ESP-IDF partition table entries as (label, type, subtype, offset, size)."""
    out = []
    for i in range(0, min(len(data), TABLE_MAX), 32):
        e = data[i:i + 32]
        if e[:2] != b"\xaa\x50":
            break
        label = e[12:28].split(b"\0")[0].decode("ascii", "replace")
        out.append((label, e[2], e[3], int.from_bytes(e[4:8], "little"), int.from_bytes(e[8:12], "little")))
    return out


def layout(bootloader, table, app):
    """Flash size and the (offset, data, name) parts; ValueError if they would not boot."""
    for data, name in ((bootloader, "bootloader"), (app, "app")):
        if data[:1] != b"\xe9":
            raise ValueError("%s is not an ESP32 image (no 0xE9 magic)" % name)
    flash = FLASH_SIZES.get(bootloader[3] >> 4)
    if flash is None:
        raise ValueError("bootloader header has an unknown flash size code %d" % (bootloader[3] >> 4))
    if BOOTLOADER_AT + len(bootloader) > TABLE_AT:
        raise ValueError("bootloader is %d bytes and runs past the partition table at 0x%x (max %d)"
                         % (len(bootloader), TABLE_AT, TABLE_AT - BOOTLOADER_AT))
    entries = parse_table(table)
    if not entries:
        raise ValueError("partitions.bin has no partition entries")
    apps = [e for e in entries if e[1] == 0]
    if not apps:
        raise ValueError("partition table has no app partition")
    # The bootloader boots the factory app, else ota_0 while otadata is blank.
    label, _, _, at, size = ([e for e in apps if e[2] == 0x00] or [e for e in apps if e[2] == 0x10] or apps)[0]
    if min(e[3] for e in entries) < TABLE_AT + TABLE_MAX:
        raise ValueError("a partition starts inside the partition table")
    if len(app) > size:
        raise ValueError("app is %d bytes, partition %s holds %d" % (len(app), label, size))
    for e in entries:
        if e[3] + e[4] > flash:
            raise ValueError("partition %s ends past the %d MB flash" % (e[0], flash >> 20))
    return flash, [(BOOTLOADER_AT, bootloader, "bootloader"), (TABLE_AT, table, "partitions"), (at, app, label)]


def merge(flash, parts):
    img = bytearray(b"\xff" * max(flash, QEMU_SIZES[0]))
    for at, data, _ in parts:
        img[at:at + len(data)] = data
    return img


class Verdict:
    """Reads log lines; ``feed`` returns None, or (passed, reason) once decided."""

    def __init__(self, until, allow_error=False):
        self.until, self.allow_error, self.started = until, allow_error, False

    def feed(self, line):
        for c in CRASH:
            if c in line:
                return False, "crash: " + line.strip()
        if line.startswith("rst:") and self.started:
            return False, "rebooted after the app started: " + line.strip()
        if line.startswith("HM "):
            self.started = True
            if line.startswith("HM error") and not self.allow_error:
                return False, line.strip()
        if self.until in line:
            return True, "saw %r" % self.until
        return None


def watch(stream_fd, verdict, seconds, out=None, echo=None):
    """Read lines from ``stream_fd`` until the verdict decides or time runs out."""
    end = time.monotonic() + seconds
    buf = b""
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return False, "no %r within %g s" % (verdict.until, seconds)
        if not select.select([stream_fd], [], [], min(left, 0.5))[0]:
            continue
        chunk = os.read(stream_fd, 4096)
        if not chunk:
            return False, "QEMU exited"
        buf += chunk
        *lines, buf = buf.split(b"\n")
        for raw in lines:
            line = raw.decode("utf-8", "replace").rstrip("\r")
            if out:
                out.write(line + "\n")
            if echo and line.startswith("HM "):
                echo(line)
            v = verdict.feed(line)
            if v:
                return v


def find_qemu():
    if os.environ.get("QEMU"):
        return os.environ["QEMU"]
    found = shutil.which("qemu-system-xtensa")
    if found:
        return found
    cands = sorted(glob.glob(os.path.join(TOOLS, "*", "qemu", "bin", "qemu-system-xtensa")))
    return cands[-1] if cands else None


def _tools_json():
    for root in (os.environ.get("IDF_PATH"), os.path.expanduser("~/.platformio/packages/framework-espidf")):
        if root and os.path.exists(os.path.join(root, "tools", "tools.json")):
            return os.path.join(root, "tools", "tools.json")
    raise SystemExit("no ESP-IDF tools.json: build native/idf once (pio run -d native/idf) or set IDF_PATH")


def install():
    """Download and unpack the QEMU that ESP-IDF pins, after a sha256 check."""
    key = {("Linux", "x86_64"): "linux-amd64", ("Linux", "aarch64"): "linux-arm64",
           ("Darwin", "x86_64"): "macos", ("Darwin", "arm64"): "macos-arm64"}.get((platform.system(), platform.machine()))
    with open(_tools_json()) as f:
        tool = [t for t in json.load(f)["tools"] if t["name"] == "qemu-xtensa"][0]
    ver = tool["versions"][0]
    if key not in ver:
        raise SystemExit("no QEMU build for %s %s" % (platform.system(), platform.machine()))
    url, want = ver[key]["url"], ver[key]["sha256"]
    dest = os.path.join(TOOLS, ver["name"])
    os.makedirs(dest, exist_ok=True)
    tar = os.path.join(dest, os.path.basename(url))
    print("downloading", url)
    with urllib.request.urlopen(url, timeout=120) as r, open(tar, "wb") as f:
        shutil.copyfileobj(r, f)
    with open(tar, "rb") as f:
        got = hashlib.sha256(f.read()).hexdigest()
    if got != want:
        os.remove(tar)
        raise SystemExit("sha256 mismatch: got %s, want %s" % (got, want))
    with tarfile.open(tar) as t:
        for m in t.getmembers():   # stay inside dest whatever the archive says
            if os.path.isabs(m.name) or ".." in m.name.split("/"):
                raise SystemExit("unsafe path in archive: " + m.name)
        t.extractall(dest)
    os.remove(tar)
    qemu = find_qemu()
    r = subprocess.run([qemu, "--version"], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr.strip())
        print("missing libraries: on Linux apt-get install libslirp0; on macOS "
              "brew install libgcrypt glib pixman sdl2 libslirp")
        return 1
    print(r.stdout.splitlines()[0], "->", qemu)
    return 0


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def build_dir(path, env):
    if os.path.exists(os.path.join(path, "firmware.bin")):
        return path
    base = os.path.join(path, ".pio", "build")
    envs = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))) if os.path.isdir(base) else []
    if env:
        return os.path.join(base, env)
    if len(envs) != 1:
        raise SystemExit("pick a build with --env: %s" % (", ".join(envs) or "nothing built under " + base))
    return os.path.join(base, envs[0])


def main(argv):
    if argv[:1] == ["--install"]:
        return install()
    pos = [a for a in argv[:1] if not a.startswith("--")]
    if not pos:
        print(__doc__.strip().splitlines()[2].strip())
        return 2
    o = parse_args(argv[1:], {"env": "", "until": "HM hello", "seconds": "60", "out": "", "allow-error": False},
                   flags=("allow-error",))
    d = build_dir(pos[0], o["env"])
    try:
        flash, parts = layout(*(_read(os.path.join(d, n)) for n in ("bootloader.bin", "partitions.bin", "firmware.bin")))
    except ValueError as e:
        print("qemu_run: FAIL flash layout:", e)
        return 1
    for at, data, name in parts:
        print("qemu_run: 0x%06x %-10s %7d bytes" % (at, name, len(data)))
    qemu = find_qemu()
    if not qemu:
        print("qemu_run: no QEMU; run python3 native/tools/qemu_run.py --install (or set QEMU)")
        return 1
    img = os.path.join(d, "qemu_flash.bin")
    with open(img, "wb") as f:
        f.write(merge(flash, parts))
    cmd = [qemu, "-nographic", "-machine", "esp32", "-drive", "file=%s,if=mtd,format=raw" % img,
           "-serial", "file:/dev/stdout", "-monitor", "none"]   # "stdio" stays silent on a pipe
    out = open(o["out"], "w") if o["out"] else None
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    t0 = time.monotonic()
    try:
        ok, why = watch(p.stdout.fileno(), Verdict(o["until"], o["allow-error"]), float(o["seconds"]), out, print)
    finally:
        p.kill()
        p.wait()
        if out:
            out.close()
    print("qemu_run: %s %s (%.1f s)" % ("PASS" if ok else "FAIL", why, time.monotonic() - t0))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

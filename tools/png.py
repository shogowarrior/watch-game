"""Minimal PNG encoder (RGB8), pure Python for CPython and MicroPython.

Compression: ``zlib.compress`` (CPython and the 1.29 wasm port).
"""

import struct
import zlib
from binascii import crc32


def _chunk(tag, data):
    c = crc32(data, crc32(tag)) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)


def encode(w, h, rgb):
    """PNG bytes for ``rgb`` (w*h*3 bytes, rows top to bottom)."""
    raw = bytearray((w * 3 + 1) * h)
    stride = w * 3
    for y in range(h):
        o = y * (stride + 1)
        raw[o + 1:o + 1 + stride] = rgb[y * stride:(y + 1) * stride]
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(bytes(raw)))
            + _chunk(b"IEND", b""))


def rgb565sw_to_rgb(buf, w, h):
    """Byte-swapped RGB565 frame (framebuf/ST7789 byte order) -> RGB8 bytes."""
    out = bytearray(w * h * 3)
    j = 0
    for i in range(0, w * h * 2, 2):
        c = (buf[i] << 8) | buf[i + 1]
        r = c >> 11
        g = (c >> 5) & 0x3F
        b = c & 0x1F
        out[j] = (r << 3) | (r >> 2)
        out[j + 1] = (g << 2) | (g >> 4)
        out[j + 2] = (b << 3) | (b >> 2)
        j += 3
    return out


def decode_rgb(png):
    """Decode a PNG written by ``encode`` (tests): -> (w, h, rgb bytes)."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    i = 8
    idat = b""
    w = h = 0
    while i < len(png):
        n = struct.unpack(">I", png[i:i + 4])[0]
        tag = png[i + 4:i + 8]
        data = png[i + 8:i + 8 + n]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", data[:8])
        elif tag == b"IDAT":
            idat += data
        i += 12 + n
    raw = zlib.decompress(idat)
    out = bytearray()
    for y in range(h):
        o = y * (w * 3 + 1)
        assert raw[o] == 0
        out += raw[o + 1:o + 1 + w * 3]
    return w, h, bytes(out)

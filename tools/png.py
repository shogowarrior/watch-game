"""Minimal PNG encoder (RGB8), pure Python for CPython and MicroPython.

Compression: ``zlib.compress`` on CPython; ``deflate.DeflateIO(..., ZLIB)``
on MicroPython (present in the 1.29 wasm port); otherwise stored
(uncompressed) deflate blocks, which every PNG reader accepts.
"""

import struct

try:
    from binascii import crc32
except ImportError:  # pragma: no cover
    from zlib import crc32


def _adler32(data):
    a = 1
    b = 0
    for i in range(0, len(data), 3800):
        for c in data[i:i + 3800]:
            a += c
            b += a
        a %= 65521
        b %= 65521
    return (b << 16) | a


def _stored(data):
    out = bytearray(b"\x78\x01")
    n = len(data)
    i = 0
    while True:
        blk = data[i:i + 65535]
        last = 1 if i + 65535 >= n else 0
        ln = len(blk)
        out += struct.pack("<BHH", last, ln, ln ^ 0xFFFF)
        out += blk
        i += 65535
        if last:
            break
    out += struct.pack(">I", _adler32(data))
    return bytes(out)


def zcompress(data):
    try:
        import zlib
        if hasattr(zlib, "compress"):
            return zlib.compress(bytes(data))
    except ImportError:
        pass
    try:
        import deflate
        import io
        s = io.BytesIO()
        d = deflate.DeflateIO(s, deflate.ZLIB)
        d.write(data)
        d.close()
        return s.getvalue()
    except (ImportError, AttributeError, OSError):
        return _stored(bytes(data))


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
            + _chunk(b"IDAT", zcompress(raw))
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
    import zlib
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

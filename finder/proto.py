"""Beacon wire format: one 16-byte ESP-NOW broadcast, little-endian.

    off fmt field
      0 2s  magic        b"SK"
      2 B   version      VERSION
      3 B   game_id      both watches of a game use the same id
      4 H   seq          u16, +1 per beacon, wraps
      6 b   rssi_last    last RSSI I measured from my partner (RSSI_NONE if none)
      7 b   rssi_filt    my filtered RSSI of the partner (RSSI_NONE if none)
      8 H   steps        u16 step count, wraps (use ``steps_delta``)
     10 B   activity     ACT_* code (finder.estimators.base)
     11 B   battery      percent 0..100, BATT_UNKNOWN if unknown
     12 B   game_state   screen code + ST_* bits, see finder/session.py (bump VERSION if it changes)
     13 B   flags        bit0 sweeping, bits1-3 tap counter (mod 8), bit4 walking
     14 H   bump_ago_ms  ms since my last bump spike; BUMP_NONE = none / too old

Packing uses ``struct.pack_into`` into a caller-owned buffer; decoding reads
bytes directly so the receive path allocates nothing.
"""

import struct
from finder.compat import const, ticks_diff

FMT = "<2sBBHbbHBBBBH"
SIZE = const(16)
MAGIC = b"SK"
VERSION = const(3)         # 3: state screen code 9 = SC_PAIRED (PAIRING calibrate/split)

RSSI_NONE = const(-128)
BUMP_NONE = const(0xFFFF)
BUMP_MAX = const(0xFFFE)
BATT_UNKNOWN = const(255)

F_SWEEP = const(0x01)
F_TAPS = const(0x0E)
F_TAPS_SHIFT = const(1)
F_WALK = const(0x10)

_M0 = const(0x53)  # "S"
_M1 = const(0x4B)  # "K"


def clamp_i8(v):
    """RSSI-ish value -> i8; None -> RSSI_NONE, floats rounded."""
    if v is None:
        return RSSI_NONE
    if not isinstance(v, int):
        v = int(round(v))
    return -128 if v < -128 else 127 if v > 127 else v


def clamp_u8(v):
    v = int(v)
    return 0 if v < 0 else 255 if v > 255 else v


def clamp_u16(v):
    v = int(v)
    return 0 if v < 0 else 0xFFFF if v > 0xFFFF else v


def bump_ago(now_ms, bump_t):
    """Encode 'ms since my last bump' (``bump_t`` in ticks_ms, or None)."""
    if bump_t is None:
        return BUMP_NONE
    d = ticks_diff(now_ms, bump_t)
    if d < 0:
        return 0
    return BUMP_NONE if d > BUMP_MAX else d


def steps_delta(new, old):
    """Steps walked between two u16 step fields (wrap-aware)."""
    return (new - old) & 0xFFFF


def valid(buf, n, game_id=None):
    """True if ``buf[:n]`` is a beacon of this version (and game, if given)."""
    return (n >= SIZE and buf[0] == _M0 and buf[1] == _M1 and buf[2] == VERSION
            and (game_id is None or buf[3] == game_id))


def seq_of(buf):
    return buf[4] | (buf[5] << 8)


def _i8(v):
    return v - 256 if v > 127 else v


class Beacon:
    """Mutable beacon fields; reuse one instance for TX and one for RX."""

    __slots__ = ("game_id", "seq", "rssi_last", "rssi_filt", "steps",
                 "activity", "battery", "state", "flags", "bump_ago_ms")

    def __init__(self, game_id=0):
        self.game_id = game_id
        self.seq = 0
        self.rssi_last = RSSI_NONE
        self.rssi_filt = RSSI_NONE
        self.steps = 0
        self.activity = 0
        self.battery = BATT_UNKNOWN
        self.state = 0
        self.flags = 0
        self.bump_ago_ms = BUMP_NONE

    def next_seq(self):
        self.seq = (self.seq + 1) & 0xFFFF
        return self.seq

    def set_flags(self, sweeping=False, taps=0, walking=False):
        self.flags = ((F_SWEEP if sweeping else 0)
                      | ((taps & 7) << F_TAPS_SHIFT)
                      | (F_WALK if walking else 0))

    def set_bump(self, now_ms, bump_t):
        self.bump_ago_ms = bump_ago(now_ms, bump_t)

    @property
    def sweeping(self):
        return bool(self.flags & F_SWEEP)

    @property
    def walking(self):
        return bool(self.flags & F_WALK)

    @property
    def taps(self):
        return (self.flags & F_TAPS) >> F_TAPS_SHIFT

    def pack_into(self, buf, off=0):
        """Write the 16-byte beacon into ``buf`` at ``off``; returns ``buf``."""
        struct.pack_into(FMT, buf, off, MAGIC, VERSION, self.game_id & 0xFF,
                         self.seq & 0xFFFF, clamp_i8(self.rssi_last),
                         clamp_i8(self.rssi_filt), int(self.steps) & 0xFFFF,
                         clamp_u8(self.activity), clamp_u8(self.battery),
                         clamp_u8(self.state), self.flags & 0xFF,
                         clamp_u16(self.bump_ago_ms))
        return buf

    def unpack_from(self, buf, off=0):
        """Decode fields from ``buf`` (no validation, no allocation)."""
        b = buf
        o = off
        self.game_id = b[o + 3]
        self.seq = b[o + 4] | (b[o + 5] << 8)
        self.rssi_last = _i8(b[o + 6])
        self.rssi_filt = _i8(b[o + 7])
        self.steps = b[o + 8] | (b[o + 9] << 8)
        self.activity = b[o + 10]
        self.battery = b[o + 11]
        self.state = b[o + 12]
        self.flags = b[o + 13]
        self.bump_ago_ms = b[o + 14] | (b[o + 15] << 8)
        return self

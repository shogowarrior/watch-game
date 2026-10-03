"""Deterministic PRNG (xorshift32 + Box-Muller), identical on CPython and MicroPython."""

import math

_M32 = 0xFFFFFFFF
_TWO_PI = 2.0 * math.pi


def _mix(x):
    """32-bit integer hash (lowbias32) used for seeding and substreams."""
    x &= _M32
    x ^= x >> 16
    x = (x * 0x7FEB352D) & _M32
    x ^= x >> 15
    x = (x * 0x846CA68B) & _M32
    x ^= x >> 16
    return x


class Rng:
    """Seeded xorshift32 generator."""

    def __init__(self, seed=1):
        self.seed = seed & _M32
        s = _mix(self.seed ^ 0x9E3779B9)
        self.s = s or 0x6D2B79F5
        self._spare = None

    def u32(self):
        x = self.s
        x ^= (x << 13) & _M32
        x ^= x >> 17
        x ^= (x << 5) & _M32
        self.s = x
        return x

    def random(self):
        """Uniform float in [0, 1) with 24-bit resolution."""
        return (self.u32() >> 8) * (1.0 / 16777216.0)

    def uniform(self, a, b):
        return a + (b - a) * self.random()

    def randint(self, a, b):
        """Integer in [a, b] inclusive."""
        return a + int(self.random() * (b - a + 1))

    def choice(self, seq):
        return seq[int(self.random() * len(seq))]

    def chance(self, p):
        return self.random() < p

    def gauss(self, mu=0.0, sigma=1.0):
        z = self._spare
        if z is not None:
            self._spare = None
            return mu + sigma * z
        u1 = 1.0 - self.random()
        u2 = self.random()
        r = math.sqrt(-2.0 * math.log(u1))
        a = _TWO_PI * u2
        self._spare = r * math.sin(a)
        return mu + sigma * r * math.cos(a)

    def fork(self, tag):
        """Independent child stream keyed by an int tag (does not advance self)."""
        return Rng(_mix(self.seed * 0x01000193 + (tag & _M32) * 0x85EBCA6B + 0x632BE5AB))

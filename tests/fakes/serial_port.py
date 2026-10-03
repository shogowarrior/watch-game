"""Fake of the watch's USB serial port (the REPL's UART) for hal/debuglink's
``SerialLink``: keeps each write's bytes and the time it was made, and
replays them into a transmit FIFO to check that no write had to wait."""


class Port:
    """An unbuffered binary file; ``clock()`` (ms) stamps each write."""

    def __init__(self, clock=None):
        self.clock = clock
        self.writes = []          # (bytes, ms)

    def write(self, b):
        self.writes.append((bytes(b), 0 if self.clock is None else self.clock()))
        return len(b)

    def data(self):
        return b"".join([w[0] for w in self.writes])

    def overfill(self, fifo, rate):
        """The first write (bytes, ms) that a ``fifo``-byte FIFO emptying at
        ``rate`` bytes per ms had no room for, or None."""
        level = 0
        t0 = None
        for w in self.writes:
            if t0 is not None:
                level = max(0, level - rate * (w[1] - t0))
            t0 = w[1]
            level += len(w[0])
            if level > fifo:
                return w
        return None

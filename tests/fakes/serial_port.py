"""Fake of the watch's USB serial port (the REPL's UART) for hal/debuglink's
``SerialLink``: keeps each write's bytes and the time it was made, and
replays them into the line's transmit FIFO to check that no write had to
wait. The line is set here, not read from hal/debuglink, so a wrong
``SERIAL_FIFO`` or ``SERIAL_RATE`` there fails the tests."""

LINE_FIFO = 128           # bytes: the UART's transmit FIFO
LINE_RATE = 11.52         # bytes per ms it empties: 115200 baud, 10 bits a byte


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

    def overfill(self):
        """The first write (bytes, ms) that the line's FIFO had no room for,
        or None."""
        level = 0
        t0 = None
        for w in self.writes:
            if t0 is not None:
                level = max(0, level - LINE_RATE * (w[1] - t0))
            t0 = w[1]
            level += len(w[0])
            if level > LINE_FIFO:
                return w
        return None

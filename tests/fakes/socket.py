"""Fake of the ``socket`` module (enough for hal/debuglink's UDP sender).

Every socket is kept in ``sockets``; each records its options and the
datagrams it sent (``sent``: (bytes, addr)). Set ``fail[0]`` to an errno and
``sendto`` raises ``OSError(errno)`` (no Wi-Fi, no buffer). Installed only
for a test's duration (``tests.fakes.install_socket``): CPython's real
socket must stay for the bridge and fake-watches tests.
"""

AF_INET = 2
SOCK_DGRAM = 2
SOL_SOCKET = 0xFFF          # lwIP values, as on the ESP32
SO_BROADCAST = 0x20

sockets = []
fail = [None]


def reset_fakes():
    del sockets[:]
    fail[0] = None


class socket:
    def __init__(self, af=AF_INET, kind=SOCK_DGRAM, proto=0):
        self.af = af
        self.kind = kind
        self.opts = {}
        self.blocking = True
        self.sent = []
        self.closed = False
        sockets.append(self)

    def setsockopt(self, level, opt, value):
        self.opts[(level, opt)] = value

    def setblocking(self, flag):
        self.blocking = bool(flag)

    def sendto(self, data, addr):
        if self.closed:
            raise OSError(9)        # EBADF
        if fail[0]:
            raise OSError(fail[0])
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("a bytes-like object is required")
        self.sent.append((bytes(data), addr))
        return len(data)

    def close(self):
        self.closed = True

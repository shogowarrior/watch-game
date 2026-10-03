"""Watch UI: strip renderer for RenderParams (MicroPython framebuf).

Colour constants are the tokens.json v0.2.0 colours as *byte-swapped*
RGB565 ints, i.e. the values to put into framebuf RGB565 buffers so the
bytes go to the ST7789 big-endian (tokens ``rgb565_swapped``). tokens.json
is not shipped to the watch, so the values live here; tests/test_renderer.py
checks them against the JSON on CPython.
"""


def rgb565(rgb):
    """0xRRGGBB -> native RGB565 (truncating: token hexes are 565-exact)."""
    return ((rgb >> 8) & 0xF800) | ((rgb >> 5) & 0x07E0) | ((rgb & 0xFF) >> 3)


def swap16(c):
    return ((c & 0xFF) << 8) | ((c >> 8) & 0xFF)


def sw(rgb):
    """0xRRGGBB -> byte-swapped RGB565 for framebuf."""
    return swap16(rgb565(rgb))


# ---- tokens.color (hex) -----------------------------------------------------
HEX = {
    "bg.base": 0x000000, "bg.iris": 0x000C08, "surface.chip": 0x081410,
    "surface.toast": 0x182018, "line.subtle": 0x394542,
    "text.primary": 0xFFFFFF, "text.secondary": 0xC6D7CE,
    "text.tertiary": 0x7B8E84, "accent.cold": 0x6BAAC6,
    "status.warn": 0xFF9A21, "status.critical": 0xFF4939,
    "accent.found": 0xFFBA31,
}
RAMP_HEX = {
    "green": (0x081810, 0x082C18, 0x104921, 0x187139,
              0x219A4A, 0x31C363, 0x5AE78C, 0xB5FFCE),
    "gold": (0x181000, 0x392408, 0x6B4510, 0x9C6918,
             0xD69621, 0xFFBA31, 0xFFD373, 0xFFEFC6),
    "grey": (0x080C08, 0x101818, 0x212829, 0x313C39,
             0x4A5952, 0x6B7973, 0x94A69C, 0xCED7D6),
}

BG_BASE = sw(HEX["bg.base"])
BG_IRIS = sw(HEX["bg.iris"])
SURF_CHIP = sw(HEX["surface.chip"])
SURF_TOAST = sw(HEX["surface.toast"])
LINE_SUBTLE = sw(HEX["line.subtle"])
TEXT_PRI = sw(HEX["text.primary"])
TEXT_SEC = sw(HEX["text.secondary"])
TEXT_TER = sw(HEX["text.tertiary"])
ACC_COLD = sw(HEX["accent.cold"])
WARN = sw(HEX["status.warn"])
CRIT = sw(HEX["status.critical"])
ACC_FOUND = sw(HEX["accent.found"])
PROX = tuple(sw(h) for h in RAMP_HEX["green"])
GOLD = tuple(sw(h) for h in RAMP_HEX["gold"])
GREY = tuple(sw(h) for h in RAMP_HEX["grey"])

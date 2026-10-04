"""Every hal/ module imports under the fakes, and Board brings up the real drivers."""

import sys

from tests import fakes

try:
    import os
except ImportError:  # pragma: no cover
    import uos as os

API = {
    "pins": ("CPU_HZ", "TFT_BAUD", "TFT_BAUD_FAST", "TFT_MISO", "I2C0_SDA", "TOUCH_INT", "MOTOR"),
    "st7789": ("ST7789", "rgb565", "swap16", "MADCTL_ROT2", "ROW_OFFSET_ROT2"),
    "axp202": ("AXP202", "percent_from_mv", "BIT_DCDC3", "BIT_LDO2", "EV_SHORT", "EV_LONG"),
    "watchdog": ("Watchdog", "MODE_HW", "MODE_SOFT"),
    "bma423": ("BMA423", "decode_frames", "temperature_c", "int_map_values", "EV_STEP"),
    "ft6336": ("FT6336",),
    "haptics": ("Motor",),
    "radio": ("EspNowRadio", "SimRadio", "BCAST", "CHANNELS"),
    "board": ("Board", "safe_boot", "ORDER"),
    "debuglink": ("DebugLink", "SerialLink", "start", "read_config", "read_secrets",
                  "broadcast_addr", "DEBUG_PORT", "LINKS", "OFF_MSG", "USB_MSG", "WIFI_FIX",
                  "SERIAL_FIFO", "SERIAL_QMAX"),
}


def _hal_modules():
    return sorted(n[:-3] for n in os.listdir("hal") if n.endswith(".py") and n != "__init__.py")


def test_every_hal_module_imports_fresh():
    fakes.install()
    names = _hal_modules()
    for k in API:
        assert k in names, k
    old = {}
    for k in list(sys.modules):
        if k == "hal" or k.startswith("hal."):
            old[k] = sys.modules.pop(k)
    try:
        for n in names:
            __import__("hal." + n)
            mod = sys.modules["hal." + n]
            for attr in API.get(n, ()):
                assert hasattr(mod, attr), "hal.%s.%s" % (n, attr)
    finally:
        for k in list(sys.modules):
            if k == "hal" or k.startswith("hal."):
                del sys.modules[k]
        for k in old:
            sys.modules[k] = old[k]


def test_board_init_with_real_drivers():
    m = fakes.install()
    m.add_axp202()
    m.add_i2c_device(0, 0x19, {0x00: 0x13})     # BMA423 chip id
    m.add_i2c_device(1, 0x38, {0xA3: 0x64})     # FT6336U
    import hal.board as hb
    b = hb.Board().init()
    assert not b.errors, b.errors
    for name in hb.ORDER:
        if name != "backlight":
            assert b.has(name), name
    # pins, clocks, PMU outputs and touch rotation: test_hal_board / test_hal_st7789
    assert type(b.imu).__name__ == "BMA423" and b.imu.i2c is b.i2c0
    assert type(b.radio).__name__ == "EspNowRadio" and b.radio._e.active()

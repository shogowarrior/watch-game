"""GPIO map for the LILYGO T-Watch 2020 **V1** (ESP32, 16 MB flash, 8 MB PSRAM).

Only V1 is supported. Other revisions differ and must not reuse this map
blindly: V2 swaps the GPIO motor for an I2C haptic driver and adds GPS/SD,
V3 adds a PDM microphone. Input-only pins (34..39) have no pull-ups and
cannot drive outputs.
"""

from finder.compat import const

CPU_HZ = const(240_000_000)

# ST7789 240x240 display, SPI through the GPIO matrix (not IOMUX pins).
TFT_SPI_ID = const(1)
TFT_SCK = const(18)
TFT_MOSI = const(19)
TFT_MISO = None          # always pass miso=None: HSPI's default MISO is GPIO12 = backlight
TFT_CS = const(5)
TFT_DC = const(27)
TFT_RST = None           # no reset line: software reset (0x01) only
TFT_BL = const(12)       # backlight PWM; LDO2 feeds BL + panel (V1): keep on
TFT_BAUD = const(26_666_667)       # 80 MHz / 3: max over the GPIO matrix on stock firmware
TFT_BAUD_FAST = const(40_000_000)  # only on a custom build without the dummy-cycle limit

# I2C bus 0: AXP202 PMU 0x35, BMA423 0x19 (or 0x18), PCF8563 RTC 0x51.
# Create ONE machine.I2C for these pins and share it.
I2C0_ID = const(0)
I2C0_SDA = const(21)
I2C0_SCL = const(22)
I2C0_FREQ = const(400_000)
AXP202_ADDR = const(0x35)
BMA423_ADDR = const(0x19)
BMA423_ADDR_ALT = const(0x18)
PCF8563_ADDR = const(0x51)

AXP202_IRQ = const(35)   # input only, active low (also carries the PEK side button)
BMA423_INT1 = const(39)  # input only
RTC_INT = const(37)      # input only, PCF8563 alarm/timer

# FT6336 touch on I2C bus 1.
I2C1_ID = const(1)
TOUCH_SDA = const(23)
TOUCH_SCL = const(32)
TOUCH_INT = const(38)    # input only
TOUCH_ADDR = const(0x38)

MOTOR = const(4)         # vibration motor, plain GPIO on V1 (PWM for strength)

# Unused by the game, listed so nothing else claims them.
I2S_BCK = const(26)      # MAX98357A amplifier (powered by AXP202 LDO3)
I2S_WS = const(25)
I2S_DOUT = const(33)
IR_TX = const(13)

# User C modules for a custom MicroPython build (native/README.md):
#   make -C ports/esp32 BOARD=ESP32_GENERIC BOARD_VARIANT=SPIRAM USER_C_MODULES=<this file>
include(${CMAKE_CURRENT_LIST_DIR}/hmlcd/micropython.cmake)

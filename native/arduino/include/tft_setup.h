// TFT_eSPI settings for the T-Watch 2020 V1, read by TFT_eSPI.h from the include path.
#pragma once
#include <stdint.h>

#define ST7789_DRIVER
#define TFT_WIDTH 240
#define TFT_HEIGHT 240
#define USE_HSPI_PORT
#define TFT_SCLK 18
#define TFT_MOSI 19
// TFT_eSPI always attaches a MISO, and Arduino-ESP32 maps -1 to HSPI's default,
// GPIO12 (the backlight, a boot strapping pin): use GPIO36, input-only and unused.
#define TFT_MISO 36
#define TFT_CS -1    // the panel is the bus's only device: CS is held low
#define TFT_DC 27
#define TFT_RST -1   // no reset line: software reset
#define DISABLE_ALL_LIBRARY_WARNINGS

// The bench changes the clock at run time (env_tft_espi.cpp); TFT_eSPI reads it
// at each transaction and when it adds its DMA device.
extern uint32_t hm_tft_spi_hz;
#define SPI_FREQUENCY hm_tft_spi_hz

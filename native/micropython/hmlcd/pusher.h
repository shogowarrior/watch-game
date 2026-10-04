// The pusher behind the hmlcd module, as plain C so hmlcd.c (the MicroPython
// bindings) needs no C++. Every call comes from the MicroPython task; the
// pushes themselves run in a task on core 0.
#pragma once
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum { HMLCD_OK = 0, HMLCD_NOMEM, HMLCD_BUS, HMLCD_CLOCK, HMLCD_TASK, HMLCD_STOPPED };

int hmlcd_start(int host, int sck, int mosi, int cs, int dc, uint32_t hz);
void hmlcd_stop(void);
int hmlcd_started(void);
int hmlcd_set_clock(uint32_t hz);                         // waits for a running push
void hmlcd_command(uint8_t cmd, const uint8_t* data, size_t n);   // waits, then sends
void hmlcd_push(const uint16_t* px, int y0, int rows);    // waits, then starts; returns at once
void hmlcd_wait(void);
int hmlcd_busy(void);
// pushes, last push us, longest push us, all pushes us, us the caller waited for a push
void hmlcd_stats(uint32_t out[5]);

#ifdef __cplusplus
}
#endif

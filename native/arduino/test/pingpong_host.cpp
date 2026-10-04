// Drives the C++ pinger (src/pingpong.h) from stdin so pingpong_host.py can run
// it beside tools/radio_pingpong.py's own classes:
//   t MS               -> "ping HEX" when a ping is due at MS, else "-"
//   rx MS RSSI HEX     -> hands the pinger one received frame
//   done MS            -> "done 0|1"
//   report             -> the HM report lines
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "hm/hal.h"
#include "pingpong.h"

void hm::logf(const char* fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  vprintf(fmt, ap);
  va_end(ap);
  putchar('\n');
}

int main(int argc, char** argv) {
  static pingpong::Pinger p(argc > 1 ? atoi(argv[1]) : 1000);
  char cmd[16], hex[2 * pingpong::SIZE + 1];
  unsigned long ms;
  while (scanf("%15s", cmd) == 1) {
    if (!strcmp(cmd, "t") && scanf("%lu", &ms) == 1) {
      uint8_t b[pingpong::SIZE];
      if (!p.due((hm::ticks_t)ms, b)) {
        puts("-");
      } else {
        printf("ping ");
        for (uint8_t v : b) printf("%02x", v);
        putchar('\n');
      }
    } else if (!strcmp(cmd, "rx") && scanf("%lu", &ms) == 1) {
      int rssi;
      uint8_t b[pingpong::SIZE];
      if (scanf("%d %32s", &rssi, hex) != 2) return 2;
      const size_t n = strlen(hex) / 2;
      for (size_t i = 0; i < n && i < sizeof b; i++) sscanf(hex + 2 * i, "%2hhx", &b[i]);
      p.on_rx(b, n, (int8_t)rssi, (hm::ticks_t)ms);
    } else if (!strcmp(cmd, "done") && scanf("%lu", &ms) == 1) {
      printf("done %d\n", p.done((hm::ticks_t)ms) ? 1 : 0);
    } else if (!strcmp(cmd, "report")) {
      p.report(6);
    } else {
      return 2;
    }
    fflush(stdout);
  }
  return 0;
}

// hm::push_bounced against a bus that reads each burst as late as DMA may:
// just before the next call returns.
#include <vector>

#include "check.h"
#include "fakes.h"
#include "hm/bounce_push.h"

namespace {

struct DmaLcd : hm::LcdBus {
  std::vector<uint8_t> cmds;
  std::vector<uint16_t> wire;           // every pixel in the order it left
  const uint16_t* pending = nullptr;
  size_t pending_n = 0;
  void land() {                         // the burst still in flight reads its buffer now
    if (pending) wire.insert(wire.end(), pending, pending + pending_n);
    pending = nullptr;
  }
  bool set_clock(uint32_t) override { return true; }
  void command(uint8_t cmd, const uint8_t*, size_t) override {
    land();
    cmds.push_back(cmd);
  }
  void pixels(uint8_t cmd, const uint16_t* px, size_t n) override {
    land();
    cmds.push_back(cmd);
    pending = px;
    pending_n = n;
  }
  void wait() override { land(); }
};

}  // namespace

TEST(test_push_bounced_sends_every_row_through_two_buffers) {
  hmt::FakeClock clock;
  DmaLcd bus;
  hm::St7789 panel(bus, clock);
  std::vector<uint16_t> frame(240 * 60);
  for (size_t i = 0; i < frame.size(); i++) frame[i] = (uint16_t)(i * 7 + 1);
  std::vector<uint16_t> a(240 * 25), b(240 * 25);
  uint16_t* const bounce[2] = {a.data(), b.data()};
  hm::push_bounced(panel, frame.data(), 100, 60, bounce, 25);   // chunks of 25, 25, 10 rows
  panel.end_frame();
  CHECK(bus.wire == frame);
  CHECK(bus.cmds == std::vector<uint8_t>({0x2A, 0x2B, 0x2C, 0x3C, 0x3C}));
  // With one buffer, each copy overwrites the chunk still on the wire.
  uint16_t* const same[2] = {a.data(), a.data()};
  DmaLcd bus1;
  hm::St7789 panel1(bus1, clock);
  hm::push_bounced(panel1, frame.data(), 0, 60, same, 25);
  panel1.end_frame();
  CHECK(bus1.wire != frame);
}

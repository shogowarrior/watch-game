// The fakes and the replay loop of hal_replay.h.
#include "hal_replay.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>

#include <vector>

#include "hm/bma423.h"
#include "hm/st7789.h"

namespace hmt {

namespace {

std::string fmt(const char* f, ...) {
  char buf[256];
  va_list ap;
  va_start(ap, f);
  vsnprintf(buf, sizeof buf, f, ap);
  va_end(ap);
  return buf;
}

std::string hex(const uint8_t* p, size_t n) {
  static const char digits[] = "0123456789abcdef";
  std::string s;
  for (size_t k = 0; k < n; k++) s += digits[p[k] >> 4], s += digits[p[k] & 15];
  return s;
}

uint32_t crc32(uint32_t crc, const uint8_t* p, size_t n) {   // zlib's, continued from crc
  crc = ~crc;
  for (size_t k = 0; k < n; k++) {
    crc ^= p[k];
    for (int b = 0; b < 8; b++) crc = crc >> 1 ^ (0xEDB88320 & -(crc & 1));
  }
  return ~crc;
}

std::vector<uint8_t> unhex(const std::string& h) {
  std::vector<uint8_t> out;
  for (size_t k = 0; k + 1 < h.size(); k += 2) out.push_back((uint8_t)strtoul(h.substr(k, 2).c_str(), nullptr, 16));
  return out;
}

bool has(const Json& ev, const char* key) { return ev.k == Json::OBJ && ev.get(key); }

std::string reg_of(const Json& a) {
  return fmt("%d:0x%02x:0x%02x", (int)a[0].in(), (int)a[1].in(), (int)a[2].in());
}

size_t read_size(const Json& ev) { return has(ev, "d") ? ev["d"].str().size() / 2 : (size_t)ev["n"].in(); }

bool ok(const Json& ev) { return !has(ev, "ok") || ev["ok"].flag(); }

// A recorded event as the request it was, in the words the fakes use for theirs.
std::string request(const Json& ev) {
  if (has(ev, "r")) return "read " + reg_of(ev["r"]) + fmt(", %zu bytes", read_size(ev));
  if (has(ev, "w")) return "write " + reg_of(ev["w"]) + " <- " + ev["d"].str();
  if (has(ev, "scan")) return fmt("scan of I2C%d", (int)ev["scan"][0].in());
  if (has(ev, "cmd")) {
    if (!has(ev, "d")) return "RAMWR";
    const std::string& d = ev["d"].str();
    return fmt("command 0x%02x", (int)ev["cmd"].in()) + (d.empty() ? "" : " " + d);
  }
  if (has(ev, "px")) return fmt("%lld pixel bytes", (long long)ev["px"].in());
  if (has(ev, "pwm")) return fmt("backlight duty %lld/65535", (long long)ev["pwm"].in());
  if (has(ev, "line")) return "IRQ line read";
  if (has(ev, "sleep")) return fmt("sleep %lld ms", (long long)ev["sleep"].in());
  if (has(ev, "sleep_us")) return fmt("sleep %lld us", (long long)ev["sleep_us"].in());
  if (has(ev, "wait")) return fmt("wait %lld ms", (long long)ev["wait"].in());
  return dump(ev, 120);
}

// ... and what came of it.
std::string describe(const Json& ev) {
  std::string s = request(ev);
  if (!ok(ev)) return s + " (NACK)";
  if (has(ev, "r")) return s + " -> " + dump(ev["d"], 60);
  if (has(ev, "line")) return s + (ev["line"].in() ? " (high)" : " (low)");
  if (has(ev, "px")) return s + fmt(", CRC-32 %08llx", (long long)ev["crc"].in());
  return s;
}

bool is(const Json& v, const char* key) { return v.k == Json::OBJ && v.o.size() == 1 && v.o[0].first == key; }

}  // namespace

Json raised() { return Jobj(nullptr, {{"@error", J(true)}}); }
Json voided() { return Jobj(nullptr, {{"@void", J(true)}}); }

void pattern(int seed, uint8_t* out, size_t n) {
  for (size_t i = 0; i < n; i++) out[i] = (uint8_t)(i * 131 + (size_t)seed * 7 + (i >> 9));
}

void bma423_allowances(HalWorld& w) {
  w.streams.insert(0 << 16 | hm::Bma423::ADDR << 8 | hm::Bma423::REG_FIFO_DATA);
  w.skip = [](const Json& ev) {
    return has(ev, "scan") || (has(ev, "r") && ok(ev) && ev["r"][1].in() == hm::Bma423::ADDR && ev["r"][2].in() == 0x2A);
  };
}

// -- the world ------------------------------------------------------------------

void HalWorld::start(const Json& io) {
  io_ = &io;
  next_ = part_ = 0;
}

const Json& HalWorld::expect(const std::string& what) {
  while (!part_ && next_ < io_->a.size() && skip && skip(io_->a[next_])) next_++;
  if (next_ >= io_->a.size()) throw Mismatch(fmt("io[%zu]: the port: ", next_) + what + "; Python: nothing more");
  return io_->a[next_];
}

void HalWorld::differ(const std::string& what, const Json& ev) {
  throw Mismatch(fmt("io[%zu]: the port: ", next_) + what + "; Python: " + describe(ev) +
                 (part_ ? fmt(" (after %zu of its bytes)", part_) : ""));
}

void HalWorld::finish() {
  while (!part_ && next_ < io_->a.size() && skip && skip(io_->a[next_])) next_++;
  if (part_) throw Mismatch(fmt("io[%zu]: the port stopped after %zu bytes of ", next_, part_) + describe(io_->a[next_]));
  if (next_ < io_->a.size())
    throw Mismatch(fmt("io[%zu]: the port: nothing more; Python: ", next_) + describe(io_->a[next_]));
}

bool HalWorld::Bus::write(uint8_t addr, uint8_t reg, const uint8_t* d, size_t n) {
  const std::string what = fmt("write %d:0x%02x:0x%02x <- ", id, addr, reg) + hex(d, n);
  const Json& ev = w.expect(what);
  if (w.part_ || request(ev) != what) w.differ(what, ev);
  w.next_++;
  return ok(ev);
}

bool HalWorld::Bus::read(uint8_t addr, uint8_t reg, uint8_t* d, size_t n) {
  const std::string what = fmt("read %d:0x%02x:0x%02x, %zu bytes", id, addr, reg, n);
  const Json& ev = w.expect(what);
  const bool stream = w.streams.count(id << 16 | addr << 8 | reg) != 0;
  if (!has(ev, "r") || ev["r"][0].in() != id || ev["r"][1].in() != addr || ev["r"][2].in() != reg)
    w.differ(what, ev);
  const size_t total = read_size(ev);
  if (stream ? w.part_ + n > total || (!ok(ev) && w.part_) : n != total) w.differ(what, ev);
  if (!ok(ev)) {
    w.next_++;
    return false;
  }
  const std::vector<uint8_t> b = unhex(ev["d"].str());
  for (size_t k = 0; k < n; k++) d[k] = b[w.part_ + k];
  w.part_ += n;
  if (w.part_ == total) w.part_ = 0, w.next_++;
  return true;
}

bool HalWorld::Lcd::set_clock(uint32_t hz) {
  throw Mismatch(fmt("the port set the SPI clock (%u Hz): the shell's business, hal's drivers never do", hz));
}

void HalWorld::Lcd::command(uint8_t cmd, const uint8_t* d, size_t n) {
  in_ramwr = false;
  const std::string what = fmt("command 0x%02x", cmd) + (n ? " " + hex(d, n) : "");
  const Json& ev = w.expect(what);
  if (w.part_ || request(ev) != what) w.differ(what, ev);
  w.next_++;
}

void HalWorld::Lcd::pixels(uint8_t cmd, const uint16_t* px, size_t count) {
  if (cmd == hm::St7789::RAMWR) {
    const Json& ev = w.expect("RAMWR");
    if (w.part_ || request(ev) != "RAMWR") w.differ("RAMWR", ev);
    w.next_++;
    in_ramwr = true;
  } else if (cmd != hm::St7789::RAMWRC || !in_ramwr) {   // RAMWRC right after pixels: the window goes on
    const std::string what = fmt("command 0x%02x with pixels", cmd);
    w.differ(what, w.expect(what));
  }
  w.pixel_bytes((const uint8_t*)px, count * 2);
}

void HalWorld::pixel_bytes(const uint8_t* p, size_t n) {
  const std::string what = fmt("%zu pixel bytes", n);
  const Json& ev = expect(what);
  if (!has(ev, "px") || part_ + n > (size_t)ev["px"].in()) differ(what, ev);
  if (!part_) crc_ = 0;
  crc_ = crc32(crc_, p, n);
  part_ += n;
  if (part_ < (size_t)ev["px"].in()) return;
  if (crc_ != (uint32_t)ev["crc"].in())
    throw Mismatch(fmt("io[%zu]: the port's %zu pixel bytes have CRC-32 %08x; Python's %08x", next_, part_, crc_,
                       (uint32_t)ev["crc"].in()));
  part_ = 0;
  next_++;
}

void HalWorld::Backlight::set(double level) {
  const long long d = llround(level * 65535);
  const std::string what = d / 65535.0 == level ? fmt("backlight duty %lld/65535", d)
                                                 : fmt("backlight duty %.17g (not a whole 65535th)", level);
  const Json& ev = w.expect(what);
  if (w.part_ || request(ev) != what) w.differ(what, ev);
  w.next_++;
}

bool HalWorld::Irq::low() {
  const Json& ev = w.expect("IRQ line read");
  if (w.part_ || request(ev) != "IRQ line read") w.differ("IRQ line read", ev);
  w.next_++;
  return ev["line"].in() == 0;
}

void HalWorld::Clock::delay_ms(uint32_t ms) {
  const std::string what = fmt("sleep %u ms", ms);
  const Json& ev = w.expect(what);
  if (w.part_ || request(ev) != what) w.differ(what, ev);
  w.next_++;
  w.t_us_ += (uint64_t)ms * 1000;
}

void HalWorld::Clock::sleep_until_us(uint32_t t) {
  const uint32_t us = t - (uint32_t)w.t_us_;
  const std::string what = fmt("sleep %u us", us);
  const Json& ev = w.expect(what);
  if (w.part_ || request(ev) != what) w.differ(what, ev);
  w.next_++;
  w.t_us_ += us;
}

void HalWorld::wait(void* world, int32_t ms) {
  HalWorld& w = *(HalWorld*)world;
  const std::string what = fmt("wait %d ms", (int)ms);
  const Json& ev = w.expect(what);
  if (w.part_ || request(ev) != what) w.differ(what, ev);
  w.next_++;
  w.t_us_ += (uint64_t)ms * 1000;
}

// -- the replay -----------------------------------------------------------------

bool replay_hal(const char* driver, const std::function<std::unique_ptr<HalPort>(HalWorld&)>& make) {
  const char* dir = getenv("HM_TRACES");
  const std::string file = std::string("hal.") + driver + ".jsonl";
  if (!dir) {
    printf("  %s: HM_TRACES is not set (native/test/run.py records the traces and sets it)\n", file.c_str());
    return false;
  }
  FILE* f = fopen((std::string(dir) + "/" + file).c_str(), "r");
  if (!f) {
    printf("  %s: no trace in %s\n", file.c_str(), dir);
    return false;
  }
  std::unique_ptr<HalWorld> world;
  std::unique_ptr<HalPort> port;
  std::string line, scenario, err;
  int n = 0, calls = 0, scenarios = 0;
  bool good = true;
  for (char buf[65536]; good && fgets(buf, sizeof buf, f);) {
    line += buf;
    if (line.back() != '\n' && !feof(f)) continue;   // a line longer than buf
    n++;
    std::string what;
    try {
      Json rec;
      if (!parse(line, rec, &err)) throw Mismatch("unreadable line: " + err);
      line.clear();
      if (const Json* s = rec.get("scenario")) {
        scenario = s->str();
        world.reset(new HalWorld((uint64_t)rec["t"].in()));
        port = make(*world);
        scenarios++;
        continue;
      }
      const std::string& op = rec["op"].str();
      what = "[" + scenario + "] " + op + "(" + dump(rec["a"], 120) + ")";
      if (op == "advance") {
        world->advance((uint64_t)rec["a"][0].in());
        continue;
      }
      world->start(rec["io"]);
      const Json got = port->call(op, rec["a"]);
      world->finish();
      calls++;
      const Json& want = rec["r"];
      const bool threw = want.k == Json::OBJ && want.get("raise");
      std::string where;
      if (is(got, "@void")) {
      } else if (is(got, "@error") != threw) {
        throw Mismatch(threw ? "result: Python raised " + want["raise"].str() + ", the port returned " + dump(got)
                             : "result: the port failed, Python returned " + dump(want));
      } else if (!threw && !same(want, got, &where)) {
        throw Mismatch("result: " + where);
      }
      if (const Json* s = rec.get("s"))
        if (!same(*s, port->state(), &where)) throw Mismatch("state " + where);
    } catch (const std::exception& x) {
      printf("  %s:%d %s\n    %s\n", file.c_str(), n, what.c_str(), x.what());
      good = false;
    }
  }
  fclose(f);
  if (good && !calls) {
    printf("  %s: the trace is empty\n", file.c_str());
    good = false;
  }
  if (good) printf("  %s: %d calls in %d scenarios match\n", file.c_str(), calls, scenarios);
  return good;
}

}  // namespace hmt

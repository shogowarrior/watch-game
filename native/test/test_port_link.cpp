// finder/proto.py and finder/link.py against their Python traces.
#include "check.h"
#include "hm/link.h"
#include "hm/proto.h"
#include "port.h"

using namespace hm;
using namespace hmt;

TEST(trace_beacon) {
  Port<proto::Beacon> p;
  p.cls = "finder.proto.Beacon";
  p.make = [](const Json& a, Calls&) {
    auto b = std::make_unique<proto::Beacon>();
    b->game_id = (int32_t)a[0].in();
    return b;
  };
  p.call = [](proto::Beacon& b, const std::string& m, const Json& a) -> Json {
    if (m == "next_seq") return J(b.next_seq());
    if (m == "set_flags") return b.set_flags(a[0].flag(), (int)a[1].in(), a[2].flag(), a[3].flag()), J();
    if (m == "set_bump") return b.set_bump(tick(a[0]), opt_tick(a[1])), J();
    if (m == "pack_into") {
      std::vector<uint8_t> buf = a[0].bytes();
      if ((int)buf.size() < a[1].in() + proto::SIZE) throw Mismatch("buffer too short");
      b.pack(buf.data() + a[1].in());
      return Jbytes(buf.data(), buf.size());
    }
    if (m == "unpack_from") {
      b.unpack(a[0].bytes().data() + a[1].in());
      return Jobj("Beacon", b, beacon_state);
    }
    unported(m);
  };
  p.state = beacon_state;
  p.set = beacon_set;
  CHECK_REPLAY(p);
}

TEST(trace_xorshift16) {
  Port<link::Xorshift16> p;
  p.cls = "finder.link.Xorshift16";
  p.make = [](const Json& a, Calls&) { return std::make_unique<link::Xorshift16>((int)a[0].in()); };
  p.call = [](link::Xorshift16& x, const std::string& m, const Json&) -> Json {
    if (m == "next") return J(x.next());
    unported(m);
  };
  p.state = [](const link::Xorshift16& x, State& s) { s("x", J(x.x)); };
  CHECK_REPLAY(p);
}

TEST(trace_tx_scheduler) {
  Port<link::TxScheduler> p;
  p.cls = "finder.link.TxScheduler";
  p.make = [](const Json& a, Calls&) {
    return std::make_unique<link::TxScheduler>((int)a[0].in(), (int)a[1].in(), (int)a[2].in());
  };
  p.call = [](link::TxScheduler& t, const std::string& m, const Json& a) -> Json {
    if (m == "interval") return J(t.interval());
    if (m == "due") return J(t.due(tick(a[0])));
    if (m == "mark_sent") return t.mark_sent(tick(a[0])), J();
    unported(m);
  };
  p.state = [](const link::TxScheduler& t, State& s) {
    s("period", J(t.period));
    s("jitter", J(t.jitter));
    s("next_t", J(t.next_t));
  };
  p.set = [](link::TxScheduler& t, const std::string& f, const Json& v) {
    if (f == "period") return t.period = (int)v.in(), true;
    if (f == "jitter") return t.jitter = (int)v.in(), true;
    if (f == "rng") return t.rng.x = (int)v["x"].in(), true;
    return false;
  };
  CHECK_REPLAY(p);
}

TEST(trace_link_monitor) {
  Port<link::LinkMonitor> p;
  p.cls = "finder.link.LinkMonitor";
  p.make = [](const Json& a, Calls&) {
    return std::make_unique<link::LinkMonitor>((int)a[0].in(), (int)a[1].in(), (int)a[2].in());
  };
  p.call = [](link::LinkMonitor& l, const std::string& m, const Json& a) -> Json {
    if (m == "reset_link") return l.reset_link(), J();
    if (m == "lock") return l.lock(mac(a[0])), J();
    if (m == "unlock") return l.unlock(), J();
    if (m == "on_packet") {
      const std::vector<uint8_t> buf = a[1].bytes();
      if ((int)buf.size() < a[2].in()) throw Mismatch("n is past the buffer");
      return J(l.on_packet(mac(a[0]), buf.data(), (int)a[2].in(), tick(a[3])));
    }
    if (m == "loss_pct") return J(l.loss_pct());
    if (m == "age_ms") return J(l.age_ms(tick(a[0])));
    unported(m);
  };
  p.state = [](const link::LinkMonitor& l, State& s) {
    s("game_id", J(l.game_id));
    s("max_gap", J(l.max_gap));
    s("w", J(l.w));
    s("n_bad", J(l.n_bad));
    s("n_other", J(l.n_other));
    s("n_dup", J(l.n_dup));
    s("n_restart", J(l.n_restart));
    s("partner", l.partner ? J(*l.partner) : J());
    s("n_rx", J(l.n_rx));
    s("last_seq", J(l.last_seq));
    s("last_seen", J(l.last_seen));
  };
  CHECK_REPLAY(p);
}

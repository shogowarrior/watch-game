// Glue shared by the trace tests of the game port (test_port_*.cpp).
#pragma once
#include <vector>

#include "hm/py.h"
#include "trace.h"

namespace hmt {

inline hm::Mac mac(const Json& v) {
  const std::vector<uint8_t> b = v.bytes();
  if (b.size() != 6) throw Mismatch("not a MAC: " + dump(v));
  hm::Mac m;
  memcpy(m.b, b.data(), 6);
  return m;
}
inline Json J(const hm::Mac& m) { return Jbytes(m.b, 6); }
inline hm::ticks_t tick(const Json& v) { return (hm::ticks_t)v.in(); }
inline hm::opt_ticks opt_tick(const Json& v) {
  return v.null() ? hm::opt_ticks() : hm::opt_ticks(tick(v));
}

// An object as Python's deep encoding writes it: its class name and state.
template <class T>
Json Jobj(const char* cls, const T& o, void (*state)(const T&, State&)) {
  State s;
  state(o, s);
  return Jobj(cls, std::move(s.fields));
}

[[noreturn]] inline void unported(const std::string& method) { throw Mismatch("the port has no " + method + "()"); }

}  // namespace hmt

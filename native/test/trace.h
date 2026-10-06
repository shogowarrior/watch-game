// Replays the traces native/tools/trace_game.py records from the Python game:
// every call a Python object received is made on its C++ port, and the result
// and the object's public state must come out the same. run.py records them
// into a temporary folder and passes it as HM_TRACES.
//
// A port of one class is a Port<T>: how to construct it from the recorded
// arguments, how to make each recorded call, and which state it shows under
// the Python attribute names. Numbers compare as Python's == does (exactly:
// the port computes in double, as CPython does), objects field by field, and
// fields that refer to another traced object are left to that object's trace.
#pragma once
#include <stdint.h>

#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

#include "check.h"

namespace hmt {

// One JSON value as the recorder writes it.
struct Json {
  enum Kind : uint8_t { NUL, BOOL, INT, REAL, STR, ARR, OBJ };
  Kind k = NUL;
  bool b = false;
  int64_t i = 0;
  double d = 0;
  std::string s;
  std::vector<Json> a;
  std::vector<std::pair<std::string, Json>> o;

  bool null() const { return k == NUL; }
  const Json* get(const char* key) const;                      // nullptr if absent
  const Json& operator[](int n) const;                         // these throw Mismatch if absent
  const Json& operator[](const char* key) const;
  // Typed reads of a recorded argument; each throws Mismatch on another kind.
  int64_t in() const;                                          // int (or bool)
  double num() const;                                          // int or float
  bool flag() const;                                           // bool (or 0/1)
  const std::string& str() const;
  std::vector<uint8_t> bytes() const;                          // {"b": "hex"}
  std::optional<double> opt_num() const { return null() ? std::nullopt : std::optional<double>(num()); }
  std::optional<int64_t> opt_in() const { return null() ? std::nullopt : std::optional<int64_t>(in()); }
};

// A difference between the trace and the port, or a trace the port can't read.
struct Mismatch : std::runtime_error {
  using std::runtime_error::runtime_error;
};

bool parse(const std::string& text, Json& out, std::string* err);
std::string dump(const Json& v, size_t max = 300);   // compact, cut at max chars

// C++ values as Json, for results and state.
Json J();                                    // None
Json J(bool v);
Json J(double v);
Json J(const char* v);                       // nullptr: None
Json J(const std::string& v);
Json J(std::vector<Json> v);
template <class I, class = std::enable_if_t<std::is_integral<I>::value && !std::is_same<I, bool>::value>>
Json J(I v) {
  Json j;
  j.k = Json::INT;
  j.i = (int64_t)v;
  return j;
}
template <class T>
Json J(const std::optional<T>& v) {
  return v ? J(*v) : J();
}
template <class T>
Json Jarr(const T* p, size_t n) {            // a list or array
  std::vector<Json> v;
  for (size_t k = 0; k < n; k++) v.push_back(J(p[k]));
  return J(std::move(v));
}
Json Jbytes(const uint8_t* p, size_t n);
// An array or bytearray field as state shows it: {"t": typecode, "n": count,
// "crc": CRC-32 of its bytes}; item is the C++ element size ('f': float).
Json Jarray(char typecode, const void* data, size_t n, size_t item);
Json Jobj(const char* cls, std::vector<std::pair<std::string, Json>> fields);   // {"@": cls, ...}; no "@" if cls is null

// Python's == on recorded values; *where names the first difference.
bool same(const Json& want, const Json& got, std::string* where);

// The state a port shows: s("name", value) per Python attribute.
class State {
 public:
  void operator()(const char* name, Json v) { fields.emplace_back(name, std::move(v)); }
  std::vector<std::pair<std::string, Json>> fields;
};

// The calls an object made to a function it was given, as recorded, for the
// port's stand-in function to answer in order.
class Calls {
 public:
  // The recorded result of the next call, after checking it is `name` with `args`.
  Json take(const char* name, const Json& args);
  std::deque<Json> pending;
};

template <class T>
struct Port {
  const char* cls;                                                                // "finder.link.LinkMonitor"
  std::function<std::unique_ptr<T>(const Json& args, Calls& calls)> make;
  std::function<Json(T& o, const std::string& method, const Json& args)> call;   // throws Mismatch on an unknown method
  std::function<void(const T& o, State& s)> state;
  // Writes a Python attribute that other code set between calls; false if the
  // port has no such field to set (a property). Optional.
  std::function<bool(T& o, const std::string& field, const Json& v)> set;
  std::vector<std::string> ignore;                                                // Python fields the port leaves out, on purpose
};

// The type-erased replay loop behind replay().
struct Erased {
  std::function<void(int id, const Json& args, Calls& calls)> make;
  std::function<Json(int id, const std::string& method, const Json& args)> call;
  std::function<void(int id, State& s)> state;
  std::function<bool(int id, const std::string& field, const Json& v)> set;
};
bool replay_erased(const char* cls, const std::vector<std::string>& ignore, const Erased& e);

// True when the Python the traces come from changed since the ports last
// matched all of them (run.py compares native/test/traced.txt): a difference
// then means the port lags, and the test skips instead of failing.
bool python_changed();

// Replays HM_TRACES/<cls>.jsonl; prints the first difference and returns false on it.
template <class T>
bool replay(const Port<T>& p) {
  std::map<int, std::unique_ptr<T>> objs;
  auto at = [&objs](int id) -> T& {
    auto it = objs.find(id);
    if (it == objs.end()) throw Mismatch("call on object " + std::to_string(id) + " before it was made");
    return *it->second;
  };
  Erased e;
  e.make = [&](int id, const Json& args, Calls& calls) { objs[id] = p.make(args, calls); };
  e.call = [&](int id, const std::string& m, const Json& args) { return p.call(at(id), m, args); };
  e.state = [&](int id, State& s) { p.state(at(id), s); };
  e.set = [&](int id, const std::string& f, const Json& v) { return p.set && p.set(at(id), f, v); };
  return replay_erased(p.cls, p.ignore, e);
}

}  // namespace hmt

// The trace test of one Port: fails on a difference, or skips if python_changed().
#define CHECK_REPLAY(port)                                                         \
  do {                                                                             \
    if (!hmt::replay(port)) {                                                      \
      if (hmt::python_changed()) SKIP("the Python changed since the port matched it"); \
      CHECK(!"the port differs from its trace");                                   \
    }                                                                              \
  } while (0)

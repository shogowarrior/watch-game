// The trace replay of trace.h: a JSON reader for the recorder's lines, Python's
// == on them, and the loop that replays one class's file.
#include "trace.h"

#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace hmt {

namespace {

[[noreturn]] void wrong_kind(const Json& v, const char* want) {
  throw Mismatch(std::string("expected ") + want + ", the trace has " + dump(v, 80));
}

class Reader {
 public:
  Reader(const std::string& t) : p_(t.c_str()), end_(t.c_str() + t.size()) {}

  bool document(Json& out, std::string* err) {
    if (!value(out) || (ws(), p_ != end_)) {
      if (err) *err = err_.empty() ? "trailing text" : err_;
      return false;
    }
    return true;
  }

 private:
  const char* p_;
  const char* end_;
  std::string err_;

  bool fail(const char* what) {
    if (err_.empty()) err_ = what;
    return false;
  }
  void ws() {
    while (p_ < end_ && (*p_ == ' ' || *p_ == '\t' || *p_ == '\n' || *p_ == '\r')) p_++;
  }
  bool word(const char* w) {
    const size_t n = strlen(w);
    if ((size_t)(end_ - p_) < n || memcmp(p_, w, n) != 0) return false;
    p_ += n;
    return true;
  }

  bool value(Json& v) {
    ws();
    if (p_ == end_) return fail("unexpected end");
    switch (*p_) {
      case '{': return object(v);
      case '[': return array(v);
      case '"': v.k = Json::STR; return string(v.s);
    }
    if (word("null")) { v.k = Json::NUL; return true; }
    if (word("true")) { v.k = Json::BOOL; v.b = true; return true; }
    if (word("false")) { v.k = Json::BOOL; v.b = false; return true; }
    if (word("NaN")) { v.k = Json::REAL; v.d = NAN; return true; }
    if (word("Infinity")) { v.k = Json::REAL; v.d = INFINITY; return true; }
    if (word("-Infinity")) { v.k = Json::REAL; v.d = -INFINITY; return true; }
    return number(v);
  }

  bool number(Json& v) {
    const char* s = p_;
    while (p_ < end_ && strchr("+-0123456789.eE", *p_)) p_++;
    if (p_ == s) return fail("bad value");
    const std::string t(s, p_);
    char* stop = nullptr;
    errno = 0;
    if (t.find_first_of(".eE") == std::string::npos) {   // Python writes every float with one of them
      v.k = Json::INT;
      v.i = strtoll(t.c_str(), &stop, 10);
    } else {
      v.k = Json::REAL;
      v.d = strtod(t.c_str(), &stop);
    }
    return *stop || errno ? fail("bad number") : true;
  }

  static void utf8(std::string& out, uint32_t c) {
    if (c < 0x80) {
      out += (char)c;
    } else if (c < 0x800) {
      out += (char)(0xC0 | c >> 6);
      out += (char)(0x80 | (c & 0x3F));
    } else if (c < 0x10000) {
      out += (char)(0xE0 | c >> 12);
      out += (char)(0x80 | (c >> 6 & 0x3F));
      out += (char)(0x80 | (c & 0x3F));
    } else {
      out += (char)(0xF0 | c >> 18);
      out += (char)(0x80 | (c >> 12 & 0x3F));
      out += (char)(0x80 | (c >> 6 & 0x3F));
      out += (char)(0x80 | (c & 0x3F));
    }
  }

  bool hex4(uint32_t& c) {
    if (end_ - p_ < 4) return false;
    char buf[5] = {p_[0], p_[1], p_[2], p_[3], 0};
    char* stop;
    c = (uint32_t)strtoul(buf, &stop, 16);
    p_ += 4;
    return *stop == 0;
  }

  bool string(std::string& out) {
    p_++;   // the opening quote
    out.clear();
    while (p_ < end_ && *p_ != '"') {
      if (*p_ != '\\') {
        out += *p_++;
        continue;
      }
      if (++p_ == end_) break;
      const char e = *p_++;
      switch (e) {
        case '"': case '\\': case '/': out += e; break;
        case 'b': out += '\b'; break;
        case 'f': out += '\f'; break;
        case 'n': out += '\n'; break;
        case 'r': out += '\r'; break;
        case 't': out += '\t'; break;
        case 'u': {
          uint32_t c, lo;
          if (!hex4(c)) return fail("bad \\u escape");
          if (c >= 0xD800 && c < 0xDC00 && word("\\u") && hex4(lo)) c = 0x10000 + ((c - 0xD800) << 10) + (lo - 0xDC00);
          utf8(out, c);
          break;
        }
        default: return fail("bad escape");
      }
    }
    if (p_ == end_) return fail("unterminated string");
    p_++;
    return true;
  }

  bool array(Json& v) {
    v.k = Json::ARR;
    p_++;
    ws();
    if (p_ < end_ && *p_ == ']') return p_++, true;
    for (;;) {
      v.a.emplace_back();
      if (!value(v.a.back())) return false;
      ws();
      if (p_ < end_ && *p_ == ',') { p_++; continue; }
      if (p_ < end_ && *p_ == ']') return p_++, true;
      return fail("expected , or ]");
    }
  }

  bool object(Json& v) {
    v.k = Json::OBJ;
    p_++;
    ws();
    if (p_ < end_ && *p_ == '}') return p_++, true;
    for (;;) {
      ws();
      if (p_ == end_ || *p_ != '"') return fail("expected a key");
      v.o.emplace_back();
      if (!string(v.o.back().first)) return false;
      ws();
      if (p_ == end_ || *p_++ != ':') return fail("expected :");
      if (!value(v.o.back().second)) return false;
      ws();
      if (p_ < end_ && *p_ == ',') { p_++; continue; }
      if (p_ < end_ && *p_ == '}') return p_++, true;
      return fail("expected , or }");
    }
  }
};

void dump_to(std::string& out, const Json& v) {
  char buf[32];
  switch (v.k) {
    case Json::NUL: out += "null"; break;
    case Json::BOOL: out += v.b ? "true" : "false"; break;
    case Json::INT: snprintf(buf, sizeof buf, "%lld", (long long)v.i); out += buf; break;
    case Json::REAL:
      if (isnan(v.d)) out += "NaN";
      else if (isinf(v.d)) out += v.d > 0 ? "Infinity" : "-Infinity";
      else snprintf(buf, sizeof buf, "%.17g", v.d), out += buf;
      break;
    case Json::STR: out += '"' + v.s + '"'; break;
    case Json::ARR:
      out += '[';
      for (size_t n = 0; n < v.a.size(); n++) {
        if (n) out += ',';
        dump_to(out, v.a[n]);
      }
      out += ']';
      break;
    case Json::OBJ:
      out += '{';
      for (size_t n = 0; n < v.o.size(); n++) {
        if (n) out += ',';
        out += '"' + v.o[n].first + "\":";
        dump_to(out, v.o[n].second);
      }
      out += '}';
      break;
  }
}

bool numeric(const Json& v) { return v.k == Json::BOOL || v.k == Json::INT || v.k == Json::REAL; }
double as_double(const Json& v) { return v.k == Json::REAL ? v.d : v.k == Json::INT ? (double)v.i : (double)v.b; }
int64_t as_int(const Json& v) { return v.k == Json::INT ? v.i : (int64_t)v.b; }

// A traced object in another object's state: its own trace checks it.
bool ref_only(const Json& v) { return v.k == Json::OBJ && v.o.size() == 1 && v.o[0].first == "@ref"; }

Json ref_to(const Json& ref) {
  Json j;
  j.k = Json::OBJ;
  j.o.emplace_back("@ref", ref);
  return j;
}

// An object written with its class name only (nested too deep to show fields).
bool name_only(const Json& v) {
  for (auto& kv : v.o)
    if (kv.first != "@" && kv.first != "@ref") return false;
  return true;
}

bool differ(std::string* where, const std::string& at, const Json& want, const Json& got) {
  if (where) *where = (at.empty() ? "" : at + ": ") + "want " + dump(want) + ", got " + dump(got);
  return false;
}

bool same_at(const Json& want, const Json& got, const std::string& at, std::string* where) {
  if (ref_only(want)) return true;
  if (numeric(want) && numeric(got)) {
    if (want.k == Json::REAL || got.k == Json::REAL) {
      const double a = as_double(want), b = as_double(got);
      return a == b || (isnan(a) && isnan(b)) ? true : differ(where, at, want, got);
    }
    return as_int(want) == as_int(got) ? true : differ(where, at, want, got);
  }
  if (want.k != got.k) return differ(where, at, want, got);
  switch (want.k) {
    case Json::NUL: return true;
    case Json::STR: return want.s == got.s ? true : differ(where, at, want, got);
    case Json::ARR:
      if (want.a.size() != got.a.size()) return differ(where, at + " (length)", want, got);
      for (size_t n = 0; n < want.a.size(); n++)
        if (!same_at(want.a[n], got.a[n], at + "[" + std::to_string(n) + "]", where)) return false;
      return true;
    case Json::OBJ: {
      if (name_only(want)) {
        const Json* a = want.get("@");
        const Json* b = got.get("@");
        return !a || (b && same_at(*a, *b, at + ".@", where));
      }
      for (auto& kv : want.o) {
        if (kv.first == "@ref") continue;
        const Json* g = got.get(kv.first.c_str());
        if (!g) return differ(where, at + "." + kv.first + " (the port doesn't show it)", kv.second, J());
        if (!same_at(kv.second, *g, at + "." + kv.first, where)) return false;
      }
      for (auto& kv : got.o)
        if (kv.first != "@ref" && !want.get(kv.first.c_str()))
          return differ(where, at + "." + kv.first + " (Python has no such field)", J(), kv.second);
      return true;
    }
    default: return true;
  }
}

std::string arg_list(const Json& a) {
  std::string s = dump(a, 200);
  return s.size() >= 2 && s.front() == '[' ? s.substr(1, s.size() - 2) : s;
}

}  // namespace

const Json* Json::get(const char* key) const {
  for (auto& kv : o)
    if (kv.first == key) return &kv.second;
  return nullptr;
}

const Json& Json::operator[](int n) const {
  if (k != ARR) wrong_kind(*this, "a list");
  if (n < 0 || (size_t)n >= a.size()) throw Mismatch("no item " + std::to_string(n) + " in " + dump(*this, 80));
  return a[n];
}

const Json& Json::operator[](const char* key) const {
  const Json* v = k == OBJ ? get(key) : nullptr;
  if (!v) throw Mismatch(std::string("no field ") + key + " in " + dump(*this, 80));
  return *v;
}

int64_t Json::in() const {
  if (k != INT && k != BOOL) wrong_kind(*this, "an int");
  return as_int(*this);
}

double Json::num() const {
  if (!numeric(*this)) wrong_kind(*this, "a number");
  return as_double(*this);
}

bool Json::flag() const {
  if (k != BOOL && k != INT) wrong_kind(*this, "a bool");
  return as_int(*this) != 0;
}

const std::string& Json::str() const {
  if (k != STR) wrong_kind(*this, "a str");
  return s;
}

std::vector<uint8_t> Json::bytes() const {
  const std::string& h = (*this)["b"].str();
  std::vector<uint8_t> out;
  for (size_t n = 0; n + 1 < h.size(); n += 2) out.push_back((uint8_t)strtoul(h.substr(n, 2).c_str(), nullptr, 16));
  return out;
}

bool parse(const std::string& text, Json& out, std::string* err) {
  out = Json();
  return Reader(text).document(out, err);
}

std::string dump(const Json& v, size_t max) {
  std::string s;
  dump_to(s, v);
  if (s.size() > max) s = s.substr(0, max) + "...";
  return s;
}

Json J() { return Json(); }

Json J(bool v) {
  Json j;
  j.k = Json::BOOL;
  j.b = v;
  return j;
}

Json J(double v) {
  Json j;
  j.k = Json::REAL;
  j.d = v;
  return j;
}

Json J(const char* v) { return J(std::string(v)); }

Json J(const std::string& v) {
  Json j;
  j.k = Json::STR;
  j.s = v;
  return j;
}

Json J(std::vector<Json> v) {
  Json j;
  j.k = Json::ARR;
  j.a = std::move(v);
  return j;
}

Json Jbytes(const uint8_t* p, size_t n) {
  static const char hex[] = "0123456789abcdef";
  std::string h;
  for (size_t k = 0; k < n; k++) h += hex[p[k] >> 4], h += hex[p[k] & 15];
  Json j;
  j.k = Json::OBJ;
  j.o.emplace_back("b", J(h));
  return j;
}

Json Jobj(const char* cls, std::vector<std::pair<std::string, Json>> fields) {
  Json j;
  j.k = Json::OBJ;
  j.o.emplace_back("@", J(cls));
  for (auto& f : fields) j.o.push_back(std::move(f));
  return j;
}

bool same(const Json& want, const Json& got, std::string* where) { return same_at(want, got, "", where); }

Json Calls::take(const char* name, const Json& args) {
  if (pending.empty()) throw Mismatch(std::string("the port called ") + name + "(" + arg_list(args) + "), Python didn't");
  const Json c = pending.front();
  pending.pop_front();
  std::string where;
  if (c["cb"].str() != name || !same(c["a"], args, &where))
    throw Mismatch(std::string("the port called ") + name + "(" + arg_list(args) + "), Python called " +
                   c["cb"].str() + "(" + arg_list(c["a"]) + ")");
  return c["r"];
}

bool python_changed() {
  const char* v = getenv("HM_PYTHON_CHANGED");
  return v && *v == '1';
}

bool replay_erased(const char* cls, const std::vector<std::string>& ignore, const Erased& e) {
  const char* dir = getenv("HM_TRACES");
  if (!dir) {
    printf("  %s: HM_TRACES is not set (native/test/run.py records the traces and sets it)\n", cls);
    return false;
  }
  const std::string file = std::string(cls) + ".jsonl";
  FILE* f = fopen((std::string(dir) + "/" + file).c_str(), "r");
  if (!f) {
    printf("  %s: no trace %s/%s\n", cls, dir, file.c_str());
    return false;
  }
  struct Obj {
    std::map<std::string, Json> want;   // Python's state so far
    Calls calls;
  };
  std::map<int, Obj> objs;
  auto ignored = [&](const std::string& k) {
    for (auto& i : ignore)
      if (i == k) return true;
    return false;
  };
  std::string line, err;
  int n = 0, calls = 0;
  bool ok = true;
  for (char buf[65536]; ok && fgets(buf, sizeof buf, f);) {
    line += buf;
    if (line.back() != '\n' && !feof(f)) continue;   // a line longer than buf
    n++;
    Json rec;
    std::string what;   // the call being replayed, for the report
    try {
      if (!parse(line, rec, &err)) throw Mismatch("unreadable line: " + err);
      line.clear();
      if (const Json* cb = rec.get("cb")) {
        objs[(int)rec["o"].in()].calls.pending.push_back(rec);
        what = cb->str();
        continue;
      }
      const bool made = rec.get("new") != nullptr;
      const int id = (int)(made ? rec["new"] : rec["o"]).in();
      Obj& o = objs[id];
      std::string where;
      if (const Json* set = rec.get("set")) {
        what = "#" + std::to_string(id) + " attributes written: " + dump(*set, 200);
        for (auto& kv : set->o) {
          e.set(id, kv.first, kv.second);   // false for a property: the state check covers it
          const Json* ref = kv.second.k == Json::OBJ ? kv.second.get("@ref") : nullptr;
          o.want[kv.first] = ref ? ref_to(*ref) : kv.second;   // state shows a traced object as its ref
        }
      } else {
        what = "#" + std::to_string(id) + " " + (made ? std::string("new") : rec["m"].str()) + "(" + arg_list(rec["a"]) + ")";
        if (made) {
          e.make(id, rec["a"], o.calls);
        } else {
          const Json got = e.call(id, rec["m"].str(), rec["a"]);
          if (!same(rec["r"], got, &where)) throw Mismatch("result: " + where);
          calls++;
        }
        if (!o.calls.pending.empty())
          throw Mismatch("Python called " + o.calls.pending.front()["cb"].str() + "(" +
                         arg_list(o.calls.pending.front()["a"]) + "), the port didn't");
        for (auto& kv : rec["s"].o) o.want[kv.first] = kv.second;
      }
      State s;
      e.state(id, s);
      for (auto& kv : s.fields)
        if (!o.want.count(kv.first)) throw Mismatch("state: the port shows " + kv.first + ", Python has no such field");
      for (auto& kv : o.want) {
        if (ignored(kv.first)) continue;
        const Json* got = nullptr;
        for (auto& g : s.fields)
          if (g.first == kv.first) got = &g.second;
        if (!got && ref_only(kv.second)) continue;
        if (!got) throw Mismatch("state: the port doesn't show " + kv.first + " (Python: " + dump(kv.second, 80) + ")");
        if (!same(kv.second, *got, &where)) throw Mismatch("state " + kv.first + ": " + where);
      }
    } catch (const std::exception& x) {
      printf("  %s:%d %s\n    %s\n", file.c_str(), n, what.c_str(), x.what());
      ok = false;
    }
  }
  fclose(f);
  if (ok && !calls && objs.empty()) {
    printf("  %s: the trace is empty\n", file.c_str());
    ok = false;
  }
  return ok;
}

}  // namespace hmt

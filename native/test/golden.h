// native/test/golden/<name>.txt (native/tools/golden/run.py): one vector per
// line as whitespace-separated tokens, "#" lines skipped. Tests run from the
// repo root.
#pragma once
#include <math.h>
#include <stdlib.h>

#include <string>
#include <vector>

namespace hmt {

using Tokens = std::vector<std::string>;
std::vector<Tokens> golden(const char* name);
inline long num(const std::string& s) { return strtol(s.c_str(), nullptr, 0); }
inline double flt(const std::string& s) { return strtod(s.c_str(), nullptr); }
inline bool none(const std::string& s) { return s == "n"; }
// A C++ double against the Python float it ports: equal unless a test passes
// a tolerance (and says why); "n" (None) matches NAN only.
inline bool near(const std::string& want, double got, double rel = 0, double abs_tol = 0) {
  if (none(want)) return isnan(got);
  const double w = flt(want);
  return got == w || (!isnan(got) && fabs(got - w) <= abs_tol + rel * fabs(w));
}

}  // namespace hmt

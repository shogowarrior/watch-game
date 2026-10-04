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
inline float flt(const std::string& s) { return strtof(s.c_str(), nullptr); }
inline bool none(const std::string& s) { return s == "n"; }
// A C++ float against the Python double it ports, within single-precision
// drift; "n" (None) matches NAN only.
inline bool near(const std::string& want, float got, float rel = 1e-4f, float abs_tol = 1e-4f) {
  if (none(want)) return isnan(got);
  const float w = flt(want);
  return !isnan(got) && fabsf(got - w) <= abs_tol + rel * fabsf(w);
}

}  // namespace hmt

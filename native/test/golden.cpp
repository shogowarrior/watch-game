#include "golden.h"

#include <fstream>
#include <sstream>

namespace hmt {

std::vector<Tokens> golden(const char* name) {
  std::vector<Tokens> out;
  std::ifstream f(std::string("native/test/golden/") + name + ".txt");
  std::string line;
  while (std::getline(f, line)) {
    if (line.empty() || line[0] == '#') continue;
    std::istringstream s(line);
    Tokens t;
    for (std::string w; s >> w;) t.push_back(w);
    out.push_back(t);
  }
  return out;
}

}  // namespace hmt

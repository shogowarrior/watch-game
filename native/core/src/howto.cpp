// finder/howto.py (see hm/howto.h).
#include "hm/howto.h"

namespace hm {
namespace howto {

bool HowTo::flip(int32_t d) {
  int32_t c = card;
  if (c == 0) {
    card = 1;
    return true;
  }
  c += d;
  card = 1 <= c && c <= N ? c : 0;
  return false;
}

}  // namespace howto
}  // namespace hm

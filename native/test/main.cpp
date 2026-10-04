// Host test runner: native/test/run.py builds and runs it from the repo root.
#include <string.h>

#include "check.h"

namespace hmt {
Test* tests = nullptr;
int failures = 0;
int skips = 0;
}  // namespace hmt

int main(int argc, char** argv) {
  int passed = 0, skipped = 0, failed = 0;
  for (hmt::Test* t = hmt::tests; t; t = t->next) {
    if (argc > 1 && strstr(t->name, argv[1]) == nullptr) continue;
    const int before = hmt::failures, skips = hmt::skips;
    t->fn();
    if (hmt::failures != before) {
      failed++;
      printf("FAIL %s\n", t->name);
    } else if (hmt::skips != skips) {
      skipped++;
      printf("SKIP %s\n", t->name);
    } else {
      passed++;
    }
  }
  printf("[native] %d passed, %d skipped, %d failed\n", passed, skipped, failed);
  return failed || !passed ? 1 : 0;
}

// Host test runner: native/test/run.py builds and runs it from the repo root.
#include <string.h>

#include "check.h"

namespace hmt {
Test* tests = nullptr;
int failures = 0;
}  // namespace hmt

int main(int argc, char** argv) {
  int passed = 0, failed = 0;
  for (hmt::Test* t = hmt::tests; t; t = t->next) {
    if (argc > 1 && strstr(t->name, argv[1]) == nullptr) continue;
    const int before = hmt::failures;
    t->fn();
    if (hmt::failures == before) {
      passed++;
    } else {
      failed++;
      printf("FAIL %s\n", t->name);
    }
  }
  printf("[native] %d passed, 0 skipped, %d failed\n", passed, failed);
  return failed || !passed ? 1 : 0;
}

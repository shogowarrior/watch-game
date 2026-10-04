// Host test runner: native/test/run.py builds and runs it from the repo root.
#include <string.h>

#include "check.h"

namespace sft {
Test* tests = nullptr;
int failures = 0;
}  // namespace sft

int main(int argc, char** argv) {
  int passed = 0, failed = 0;
  for (sft::Test* t = sft::tests; t; t = t->next) {
    if (argc > 1 && strstr(t->name, argv[1]) == nullptr) continue;
    const int before = sft::failures;
    t->fn();
    if (sft::failures == before) {
      passed++;
    } else {
      failed++;
      printf("FAIL %s\n", t->name);
    }
  }
  printf("[native] %d passed, 0 skipped, %d failed\n", passed, failed);
  return failed || !passed ? 1 : 0;
}

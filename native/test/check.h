// Minimal host test harness: TEST(name) registers a function, CHECK fails it.
#pragma once
#include <stdio.h>

namespace sft {

struct Test {
  const char* name;
  void (*fn)();
  Test* next;
};
extern Test* tests;
extern int failures;

struct Reg {
  Test t;
  Reg(const char* name, void (*fn)()) : t{name, fn, tests} { tests = &t; }
};

}  // namespace sft

#define TEST(name)                                  \
  static void name();                               \
  static sft::Reg name##_reg(#name, name);          \
  static void name()

#define CHECK(cond)                                                       \
  do {                                                                    \
    if (!(cond)) {                                                        \
      printf("  %s:%d: CHECK(%s) failed\n", __FILE__, __LINE__, #cond);   \
      sft::failures++;                                                    \
      return;                                                             \
    }                                                                     \
  } while (0)

// Minimal host test harness: TEST(name) registers a function, CHECK fails it.
#pragma once
#include <stdio.h>

namespace hmt {

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

}  // namespace hmt

#define TEST(name)                                  \
  static void name();                               \
  static hmt::Reg name##_reg(#name, name);          \
  static void name()

#define CHECK(cond)                                                       \
  do {                                                                    \
    if (!(cond)) {                                                        \
      printf("  %s:%d: CHECK(%s) failed\n", __FILE__, __LINE__, #cond);   \
      hmt::failures++;                                                    \
      return;                                                             \
    }                                                                     \
  } while (0)

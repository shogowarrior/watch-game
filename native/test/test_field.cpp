// hm::RippleField against the MicroPython renderer (native/test/golden_field.txt).
#include <stdio.h>

#include <memory>
#include <vector>

#include "check.h"
#include "hm/bench_scene.h"

namespace {

// native/tools/golden_field.py SEQS and DTS
struct Seg {
  int fixture, frames;
};
const Seg SEQ0[] = {{0, 200}}, SEQ1[] = {{1, 200}}, SEQ2[] = {{2, 200}},
          SEQ3[] = {{0, 60}, {1, 60}, {2, 60}, {0, 60}};
struct Seq {
  const Seg* segs;
  int n;
};
const Seq SEQS[] = {{SEQ0, 1}, {SEQ1, 1}, {SEQ2, 1}, {SEQ3, 4}};
const int DTS[] = {33, 33, 34, 50, 17, 100, 33, 250, 1, 33};
const hm::ticks_t T0 = 10000;

uint32_t fnv1a(uint32_t h, const uint8_t* p, size_t n) {
  for (size_t i = 0; i < n; i++) h = (h ^ p[i]) * 0x01000193u;
  return h;
}

FILE* golden() { return fopen("native/test/golden_field.txt", "r"); }

uint8_t ring_map[240 * 240];

}  // namespace

TEST(test_ring_map_matches_python) {
  FILE* f = golden();
  CHECK(f);
  char line[128];
  unsigned want = 0;
  while (fgets(line, sizeof line, f))
    if (sscanf(line, "map %x", &want) == 1) break;
  fclose(f);
  hm::build_map(ring_map, 240);
  CHECK(fnv1a(0x811C9DC5u, ring_map, sizeof ring_map) == want);
  CHECK(ring_map[0] == 168 && ring_map[120 * 240 + 120] == 0);   // corner, centre
}

TEST(test_palettes_match_python) {
  struct Row {
    int seq, frame;
    unsigned t, hash;
  };
  std::vector<Row> rows;
  FILE* f = golden();
  CHECK(f);
  char line[128];
  Row r;
  while (fgets(line, sizeof line, f))
    if (sscanf(line, "%d %d %u %x", &r.seq, &r.frame, &r.t, &r.hash) == 4) rows.push_back(r);
  fclose(f);
  CHECK(rows.size() == 840);
  size_t i = 0;
  int bad = 0;
  for (int s = 0; s < 4; s++) {          // a fresh field per sequence, as a fresh Renderer
    std::unique_ptr<hm::RippleField> field(new hm::RippleField());
    hm::FieldScene scene(*field);
    hm::ticks_t t = T0;
    int k = 0;
    for (int g = 0; g < SEQS[s].n; g++) {
      const Seg& seg = SEQS[s].segs[g];
      for (int n = 0; n < seg.frames; n++, k++, i++) {
        CHECK(i < rows.size() && rows[i].seq == s && rows[i].frame == k && rows[i].t == t);
        scene.step(hm::BENCH_FIXTURES[seg.fixture], t);
        const uint32_t got = fnv1a(0x811C9DC5u, (const uint8_t*)field->pal, hm::N_IDX * 2);
        if (got != rows[i].hash && bad++ < 5)
          printf("  seq %d frame %d t %u: %08x != %08x\n", s, k, (unsigned)t, got, rows[i].hash);
        t += DTS[k % 10];
      }
    }
  }
  CHECK(i == rows.size());
  CHECK(bad == 0);
}

TEST(test_blit_maps_indices_through_palette) {
  uint16_t pal[256], dst[480];
  for (int i = 0; i < 256; i++) pal[i] = (uint16_t)(i * 4099 + 0x1235);
  hm::build_map(ring_map, 240);
  hm::blit(dst, ring_map, pal, 119 * 240, 480);
  for (int i = 0; i < 480; i++) CHECK(dst[i] == pal[ring_map[119 * 240 + i]]);
}

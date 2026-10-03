#!/usr/bin/env bash
# Fetch the BMA423 feature-engine config blob (bma423conf.bin) for hal/bma423.py.
#
# The blob is the `bma423_config_file[]` array (6144 bytes) from Bosch
# Sensortec's BMA423 Sensor API v2.14.13 (bma423.c, 2020-05-08, BSD-3-Clause,
# Copyright (c) 2020 Bosch Sensortec GmbH). Bosch's original GitHub repo
# (boschsensortec/BMA423_SensorDriver) is gone, so this pins the commit
# "Added generic examples" (authored by Bosch Sensortec, 2020-05-27) in the
# wasp-os mirror of that repo. Both the downloaded C file and the extracted
# blob are sha256-checked; nothing is written unless both match.
#
# NOT the LilyGO TTGO_TWatch_Library / lewisxhe blob (2017, also 6144 bytes,
# different FEATURES_IN layout); hal/bma423.py refuses that one.
#
# Usage:  tools/fetch_bma423_config.sh [OUT]        (default ./bma423conf.bin)
# Then:   mpremote cp bma423conf.bin :bma423conf.bin
# Needs:  curl, python3.
set -euo pipefail

SRC_URL="https://raw.githubusercontent.com/wasp-os/BMA423-Sensor-API/e65f82683cc2e0d2d4bd8dcfa14089c54bf8787d/bma423.c"
SRC_SHA256="3103cfa12beb2b31a57981362084c4d27c019c012fe3caf6c744e0537ce75611"
BLOB_SIZE=6144
BLOB_SHA256="112f81c8baba6d8abbf000c01e24fa56abd9f0c55e0415600d49109c189a2d3e"
OUT="${1:-bma423conf.bin}"

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "Downloading $SRC_URL"
curl -fsSL --retry 3 -o "$tmp/bma423.c" "$SRC_URL"

got="$(sha256 "$tmp/bma423.c")"
if [ "$got" != "$SRC_SHA256" ]; then
  echo "ERROR: bma423.c sha256 $got, expected $SRC_SHA256" >&2
  exit 1
fi

python3 - "$tmp/bma423.c" "$tmp/blob.bin" <<'PY'
import re, sys
src = open(sys.argv[1]).read()
if "V2.14.13" not in src:
    sys.exit("ERROR: not the v2.14.13 source")
i = src.index("bma423_config_file[] = {")
body = src[i:src.index("};", i)].split("{", 1)[1]
data = bytes(int(h, 16) for h in re.findall(r"0x([0-9A-Fa-f]{2})", body))
open(sys.argv[2], "wb").write(data)
PY

size="$(wc -c < "$tmp/blob.bin" | tr -d ' ')"
got="$(sha256 "$tmp/blob.bin")"
if [ "$size" != "$BLOB_SIZE" ] || [ "$got" != "$BLOB_SHA256" ]; then
  echo "ERROR: blob is $size bytes, sha256 $got; expected $BLOB_SIZE / $BLOB_SHA256" >&2
  exit 1
fi

cp "$tmp/blob.bin" "$OUT"
echo "Wrote $OUT ($size bytes, sha256 $got)"
echo "Copy to the watch:  mpremote cp $OUT :bma423conf.bin"

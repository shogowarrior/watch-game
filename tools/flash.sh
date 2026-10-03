#!/usr/bin/env bash
# Flash stock MicroPython v1.29.0 (ESP32_GENERIC-SPIRAM) onto a T-Watch 2020 V1.
#
# YOU run this; nothing in the repo calls it. It will:
#   1. download the firmware from micropython.org into <repo>/firmware/ (if missing),
#   2. check the file looks like an ESP32 image (size, 0xE9 magic, optional SHA256),
#   3. show the exact esptool commands and ask y/N,
#   4. erase the whole flash (this deletes every file on the watch!),
#   5. write the image at 0x1000 (ESP32 classic bootloader offset).
#
# Usage:
#   tools/flash.sh /dev/cu.usbserial-XXXX            # macOS
#   tools/flash.sh /dev/ttyUSB0                      # Linux
#   tools/flash.sh <port> path/to/other.bin          # use a local image instead
#
# Env:
#   BAUD=460800             write baud (drop to 115200 if writes fail)
#   FW_SHA256=<hex>         expected SHA256 of the image (checked if set)
#   FW_DIR=<repo>/firmware  where the download is kept
#
# Needs esptool (pip install esptool). After flashing: tools/deploy.py --port <port> --noapp
# (first deploy keeps the REPL free; docs/hardware-setup.md step 4).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

FW_NAME="ESP32_GENERIC-SPIRAM-20260824-v1.29.0.bin"
FW_URL="https://micropython.org/resources/firmware/${FW_NAME}"
BAUD="${BAUD:-460800}"
FW_DIR="${FW_DIR:-$ROOT/firmware}"

PORT="${1:-}"
if [[ -z "$PORT" || "$PORT" == "-h" || "$PORT" == "--help" ]]; then
  sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
fi
FW="${2:-$FW_DIR/$FW_NAME}"

# esptool: v5 installs `esptool`, v4 `esptool.py`; fall back to the module.
if command -v esptool >/dev/null 2>&1; then
  ESPTOOL=(esptool)
elif command -v esptool.py >/dev/null 2>&1; then
  ESPTOOL=(esptool.py)
elif python3 -c "import esptool" >/dev/null 2>&1; then
  ESPTOOL=(python3 -m esptool)
else
  echo "esptool not found: pip install esptool" >&2
  exit 1
fi

if [[ ! -e "$PORT" ]]; then
  echo "warning: $PORT does not exist (is the watch plugged in?)" >&2
fi

if [[ ! -f "$FW" ]]; then
  if [[ -n "${2:-}" ]]; then
    echo "firmware file not found: $FW" >&2
    exit 1
  fi
  echo "Firmware not found at $FW"
  echo "Download: $FW_URL"
  read -r -p "Download it now? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "aborted"; exit 1; }
  mkdir -p "$FW_DIR"
  curl -fL --retry 2 -o "$FW.part" "$FW_URL"
  mv "$FW.part" "$FW"
fi

# --- verify the image -------------------------------------------------------
size=$(wc -c < "$FW" | tr -d ' ')
if (( size < 1000000 || size > 4000000 )); then
  echo "unexpected size $size bytes for $FW (expected ~1.5-2.5 MB); not flashing" >&2
  exit 1
fi
magic=$(head -c 1 "$FW" | od -An -tx1 | tr -d ' \n')
if [[ "$magic" != "e9" ]]; then
  echo "$FW does not start with the ESP image magic 0xE9 (got 0x$magic); not flashing" >&2
  exit 1
fi
if command -v shasum >/dev/null 2>&1; then
  sha=$(shasum -a 256 "$FW" | cut -d' ' -f1)
else
  sha=$(sha256sum "$FW" | cut -d' ' -f1)
fi
if [[ -n "${FW_SHA256:-}" && "$sha" != "$FW_SHA256" ]]; then
  echo "SHA256 mismatch: got $sha, expected $FW_SHA256; not flashing" >&2
  exit 1
fi

cat <<EOF

About to flash the watch on $PORT:
  image : $FW
          $size bytes, sha256 $sha
  step 1: ${ESPTOOL[*]} --chip esp32 --port $PORT erase_flash
          (ERASES EVERYTHING on the watch, including all files you uploaded)
  step 2: ${ESPTOOL[*]} --chip esp32 --port $PORT --baud $BAUD write_flash -z 0x1000 $FW

EOF
read -r -p "Erase and flash now? [y/N] " ans
[[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "aborted, nothing changed"; exit 1; }

"${ESPTOOL[@]}" --chip esp32 --port "$PORT" erase_flash
"${ESPTOOL[@]}" --chip esp32 --port "$PORT" --baud "$BAUD" write_flash -z 0x1000 "$FW"

echo
echo "Done. Next (first time): python3 tools/deploy.py --port $PORT --noapp   (keeps the REPL free; docs/hardware-setup.md step 4)"

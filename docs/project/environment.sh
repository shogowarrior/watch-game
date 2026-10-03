set -eu
SUDO=; [ "$(id -u)" -eq 0 ] || SUDO=sudo
$SUDO apt-get update
$SUDO apt-get install -y git python3 curl ca-certificates
# Node 24 runs tools/mpy/run.mjs, the MicroPython 1.29 (WebAssembly) test runner.
if ! node --version 2>/dev/null | grep -q '^v24\.'; then
  curl -fsSL https://deb.nodesource.com/setup_24.x -o /tmp/nodesource_setup_24.sh
  $SUDO bash /tmp/nodesource_setup_24.sh
  $SUDO apt-get install -y nodejs
fi
# The runner's one package; a thread installs it with `cd tools/mpy && npm ci`.
npm cache add @micropython/micropython-webassembly-pyscript@1.29.0-6
# Last line: its status is the script's.
python3 --version && node --version | grep '^v24\.'

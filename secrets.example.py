# Copy to secrets.py (gitignored, never commit it) and fill in your Wi-Fi.
# Only debug mode uses it: `python3 tools/deploy.py --debug A` copies it to
# the watch, which joins this network to send what it does to the laptop
# (docs/design/debug-mode.md). Use a 2.4 GHz network; both watches and the
# laptop must be on the same one. `--no-debug` removes it from the watch.
WIFI_SSID = "your-ssid"
WIFI_PASSWORD = "your-password"

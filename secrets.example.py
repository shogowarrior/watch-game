# The format of the Wi-Fi file for debug mode over Wi-Fi; do not fill it in.
# To set your Wi-Fi, run `python3 tools/wifi_setup.py`: it asks for the name
# and password on this laptop and saves them outside the repo, readable only
# by you. `python3 tools/deploy.py --debug A --wifi` copies that file to the
# watch as /secrets.py, which the watch reads; `--no-debug` removes it
# (docs/design/debug-mode.md). Use a 2.4 GHz network; both watches and the
# laptop must be on the same one, and both watches on the same access point
# (a mesh or extender network can split them).
WIFI_SSID = "your-ssid"
WIFI_PASSWORD = "your-password"

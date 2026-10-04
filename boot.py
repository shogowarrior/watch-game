# boot.py: runs first on every boot. Kept minimal on purpose: no app code,
# no drivers, no network (normal play is ESP-NOW only; only debug mode, which main.py starts from /debug, joins Wi-Fi).
# main.py does the safe-boot check and starts the game.
try:
    import esp
    esp.osdebug(None)       # keep ESP-IDF log lines off the REPL UART
except ImportError:
    pass

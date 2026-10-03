# boot.py: runs first on every boot. Kept minimal on purpose: no app code,
# no drivers, no network (the game is ESP-NOW only and never joins an AP).
# main.py does the safe-boot check and starts the game.
try:
    import esp
    esp.osdebug(None)       # keep ESP-IDF log lines off the REPL UART
except ImportError:
    pass

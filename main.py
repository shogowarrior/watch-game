# main.py: runs after boot.py. Safe boot first, then the game.
#
# Skip the app (REPL stays free for mpremote / the notebook) by creating
# /noapp (tools/deploy.py --noapp) or by double-pressing, or holding, the side
# key within the first second after boot. Ctrl-C stops the game; then
# ``import app; app.rt.print_stats()`` shows the loop timing; every 10 s the game
# prints an ``fps ... lock ... jit ...`` line (frame lock and jitter, app/runtime.py). With /tele
# (tools/deploy.py --tele A) the game logs telemetry to /log. The watchdog
# (hal/watchdog.py) is the stoppable soft one while the game has only run on
# USB, and the ESP32 hardware WDT from the first battery reading off USB. A
# game started on battery keeps the hardware WDT after USB is plugged in, so
# Ctrl-C then reboots the watch within 8 s (tools/deploy.py hard-resets first
# for this reason).

from hal.board import Board, safe_boot

board = Board()
why = safe_boot(board)
if why:
    print("safe boot (%s): app skipped" % why)
else:
    try:
        board.init(strict=False)
        if board.errors:
            print("parts missing:", board.errors)
        try:
            import app
        except ImportError as e:  # app/ is a package with no imports of its own
            print("app/ not deployed (python3 tools/deploy.py copies it):", e)
        else:
            kw = {}
            try:                                # field-test switch: tools/deploy.py --tele A
                with open("/tele") as f:
                    dev = f.read().strip() or "A"
                from app.telemetry import session
                kw["telemetry"] = session(dev)  # appends to /log/<n>_<dev>.jsonl
            except OSError:
                pass                            # no /tele (or no /log): no telemetry
            # hal/watchdog.py reboots a hung loop; an fps line every 10 s (app/runtime.py)
            app.run(board, watchdog_ms=8000, fps_log_ms=10000, **kw)
    except KeyboardInterrupt:
        print("stopped: import app; app.rt.print_stats()")
    except Exception as e:  # noqa: BLE001 - keep the REPL reachable
        import sys
        sys.print_exception(e)
        print("app crashed: REPL")

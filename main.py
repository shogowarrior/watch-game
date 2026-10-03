# main.py: runs after boot.py. Safe boot first, then the game.
#
# Skip the app (REPL stays free for mpremote / the notebook) by creating
# /noapp (tools/deploy.py --noapp) or by double-pressing, or holding, the side
# key within the first second after boot. Ctrl-C stops the game (on battery the
# hardware watchdog then reboots within 8 s; on USB it is switched off); then
# ``import app; app.rt.print_stats()`` shows the loop timing.

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
            app.run(board, watchdog_ms=8000)   # hal/watchdog.py: reboots a hung loop
    except KeyboardInterrupt:
        print("stopped: import app; app.rt.print_stats()")
    except Exception as e:  # noqa: BLE001 - keep the REPL reachable
        import sys
        sys.print_exception(e)
        print("app crashed: REPL")

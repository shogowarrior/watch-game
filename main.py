# main.py: runs after boot.py. Safe boot first, then the game.
#
# Skip the app (REPL stays free for mpremote / the notebook) by creating
# /noapp (tools/deploy.py --noapp) or by double-pressing, or holding, the side
# key within the first second after boot. Ctrl-C stops the game; then
# ``import app; app.rt.print_stats()`` shows the loop timing; every 10 s the game
# prints an ``fps ... lock ... jit ...`` line (frame lock and jitter,
# app/runtime.py). With /tele (tools/deploy.py --tele A) the game logs
# telemetry to /log. With /debug the watch also sends its telemetry to the
# laptop (hal/debuglink.py): tools/deploy.py --debug A sends it over this USB
# port, playing exactly as normal (the fps line then rides the same link);
# --debug A --wifi first joins the Wi-Fi in /secrets.py (up to 10 s, screen
# dark) and sends over it, or prints why it cannot and plays normally. The
# watchdog (hal/watchdog.py) is the stoppable soft one while the game has only
# run on USB, and the ESP32 hardware WDT from the first battery reading off
# USB. A game started on battery keeps the hardware WDT after USB is plugged
# in, so Ctrl-C then reboots the watch within 8 s (tools/deploy.py hard-resets
# first for this reason).

from hal.board import Board, safe_boot

board = Board()
why = safe_boot(board)
if why:
    print("safe boot (%s): app skipped" % why)
else:
    try:
        from hal import debuglink
        try:                            # before the radio: on Wi-Fi, ESP-NOW uses its channel
            link, msg = debuglink.start()
        except Exception as e:  # noqa: BLE001 - debug mode never stops the game
            link, msg = None, debuglink.OFF_MSG % ("it could not start (%s)" % type(e).__name__)
        if msg:
            print(msg)
        board.debug = link
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
                if link is not None and dev != link.dev:    # one name for the file and records
                    print("telemetry: /tele says %s but /debug says %s; logging as %s"
                          % (dev, link.dev, link.dev))
                    dev = link.dev
                from app.telemetry import session
                kw["telemetry"] = session(dev)  # appends to /log/<n>_<dev>.jsonl
            except OSError:
                pass                            # no /tele (or no /log): no telemetry
            if link is not None:                # debug mode: the records go to the laptop too
                from app.telemetry import Telemetry
                # without /tele the ring only holds what waits for the next 5 Hz
                # send (the laptop keeps the log): small, so GC has less to scan
                tl = kw.get("telemetry") or Telemetry(cap=32)
                tl.dev = link.dev               # the page's label (A or B)
                tl.sink = link
                kw["telemetry"] = tl
            # hal/watchdog.py reboots a hung loop; an fps line every 10 s (app/runtime.py)
            app.run(board, watchdog_ms=8000, fps_log_ms=10000, **kw)
    except KeyboardInterrupt:
        print("stopped: import app; app.rt.print_stats()")
    except Exception as e:  # noqa: BLE001 - keep the REPL reachable
        import sys
        sys.print_exception(e)
        print("app crashed: REPL")

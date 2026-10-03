"""The watch app. ``app.run(board)`` starts the game loop (app/runtime.py).

After Ctrl-C the runtime stays reachable from the REPL::

    >>> import app
    >>> app.rt.print_stats()          # fps and ms per stage
    >>> app.rt.tele.dump()            # telemetry, if enabled
"""

rt = None


def run(board=None, **kw):
    """Build a ``Runtime`` on ``board`` (default ``hal.board.Board()``) and run it."""
    global rt
    from app.runtime import Runtime
    if board is None:
        from hal.board import Board
        board = Board()
    rt = Runtime(board, **kw)
    rt.run()
    return rt

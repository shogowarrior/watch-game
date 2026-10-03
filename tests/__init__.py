"""Test package: plain ``test_*`` modules run by tests/runner.py on both
runtimes; ``Skip`` lives here."""


class Skip(Exception):
    """Raise ``Skip("reason")`` in a test that cannot run on this runtime;
    the runner counts it as skipped, not passed."""

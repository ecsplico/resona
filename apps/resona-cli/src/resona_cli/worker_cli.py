"""`resona worker run|prepare|capabilities|install|uninstall|update`: the pull-worker commands.

The commands are resona-worker's own typer app (`resona-worker ...` is the same CLI),
so both entry points share one implementation.
"""

from resona_worker.cli import app as worker_app

__all__ = ["worker_app"]

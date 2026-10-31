"""``resona-worker`` command line: run one outbound pull worker against a hub."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from resona_worker.config import ConfigError, WorkerConfig, load_capabilities, load_token
from resona_worker.supervisor import Supervisor

app = typer.Typer(help="Outbound-only pull worker for the Resona hub.", no_args_is_help=True)

_EXIT_CONFIG = 2


@app.callback()
def _root() -> None:
    """Resona worker."""


@app.command()
def run(
    hub: Annotated[str, typer.Option("--hub", help="Hub base URL (https, or loopback http).")],
    token_file: Annotated[Path, typer.Option("--token-file", help="Worker credential, mode 0600.")],
    capabilities_file: Annotated[
        Path, typer.Option("--capabilities-file", help="JSON list of prepared Candidate tuples.")
    ],
    work_dir: Annotated[Path, typer.Option("--work-dir", help="Disposable per-job scratch directory.")],
    once: Annotated[bool, typer.Option("--once", help="Serve one claim cycle, then exit.")] = False,
) -> None:
    """Claim and execute jobs for this worker until stopped."""
    try:
        config = WorkerConfig(
            hub_url=hub,
            token=load_token(token_file),
            work_dir=work_dir,
            capabilities=load_capabilities(capabilities_file),
        )
    except (ConfigError, OSError) as exc:
        typer.echo(f"configuration error: {exc}", err=True)
        raise typer.Exit(_EXIT_CONFIG) from None
    code = Supervisor(config).run(once=once)
    sys.exit(code)


def main() -> None:
    app()


if __name__ == "__main__":
    main()

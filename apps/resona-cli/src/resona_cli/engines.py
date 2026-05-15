"""CLI commands for managing resona engines."""
from typing import Optional

import typer

from resona_client.config import EngineConfig, EngineEntry, is_reachable

engines_app = typer.Typer(no_args_is_help=True, help="Manage engine server addresses.")

BUILTIN_ENGINES = ("faster-whisper", "whisper", "voxtral")


@engines_app.command("list")
def list_engines():
    """List built-in local engines plus configured server/cloud engines."""
    cfg = EngineConfig.load()
    typer.echo(f"  {'NAME':<18}{'TYPE':<9}{'PRIVATE':<9}STATUS")
    for name in BUILTIN_ENGINES:
        typer.echo(f"  {name:<18}{'local':<9}{'yes':<9}built-in")
    for e in cfg.engines:
        if e.type == "cloud":
            kind = "cloud"
            private = "no"
            status = "key set" if e.is_usable() else "no key"
        else:
            kind = "server"
            private = "yes" if e.is_private() else "no"
            status = "reachable" if is_reachable(e) else "unreachable"
        typer.echo(f"  {e.name:<18}{kind:<9}{private:<9}{status}")


@engines_app.command("add")
def add_engine(
    name: str = typer.Argument(..., help="Unique name for this engine"),
    api_url: str = typer.Argument(..., help="resona-api base URL, e.g. http://localhost:7000"),
    api_key: str = typer.Option("", "--key", "-k", help="API key (if the server requires one)"),
    compose_dir: Optional[str] = typer.Option(
        None, "--compose-dir", "-c",
        help="Path to docker-compose project dir. When set, this engine can be auto-started.",
    ),
    ssh_host: Optional[str] = typer.Option(
        None, "--ssh", "-s",
        help=(
            "SSH host to tunnel through, e.g. user@myserver.com or user@myserver.com:2222. "
            "Opens a local port-forward (ssh -N -L) when the engine is not directly reachable."
        ),
    ),
    ssh_remote_port: Optional[int] = typer.Option(
        None, "--ssh-remote-port",
        help="Remote port on the SSH host (defaults to the port in api_url).",
    ),
):
    """Add an engine address.

    \b
    Examples:
      # Direct remote server on LAN
      resona engines add lan http://192.168.1.10:7000

      # Local docker-compose (auto-started when needed)
      resona engines add local http://localhost:7000 --compose-dir ~/resona

      # Remote server over SSH tunnel
      resona engines add remote http://localhost:7000 --ssh user@myserver.com

      # Remote server with different local/remote ports
      resona engines add remote http://localhost:17000 --ssh user@myserver.com --ssh-remote-port 7000
    """
    cfg = EngineConfig.load()
    entry = EngineEntry(
        name=name,
        api_url=api_url.rstrip("/"),
        api_key=api_key,
        compose_dir=compose_dir,
        ssh_host=ssh_host,
        ssh_remote_port=ssh_remote_port,
    )
    try:
        cfg.add(entry)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    ok = is_reachable(entry)
    status = typer.style("reachable", fg=typer.colors.GREEN) if ok else typer.style("not reachable", fg=typer.colors.YELLOW)
    typer.echo(f"Added '{name}' ({api_url}) — {status}")
    if compose_dir and not ok:
        typer.echo(f"  Auto-start enabled: will run `docker compose up -d` in {compose_dir} when needed.")
    if ssh_host and not ok:
        typer.echo(f"  SSH tunnel enabled: will open port-forward via {ssh_host} when needed.")


@engines_app.command("remove")
def remove_engine(
    name: str = typer.Argument(..., help="Name of the engine to remove"),
):
    """Remove an engine."""
    cfg = EngineConfig.load()
    try:
        cfg.remove(name)
        typer.echo(f"Removed '{name}'")
    except KeyError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@engines_app.command("test")
def test_engines(
    name: Optional[str] = typer.Argument(None, help="Engine name to test (tests all if omitted)"),
    timeout: float = typer.Option(3.0, "--timeout", "-t", help="Connection timeout in seconds"),
):
    """Test reachability of one or all engines."""
    cfg = EngineConfig.load()
    if not cfg.engines:
        typer.echo("No engines configured.")
        raise typer.Exit(1)

    if name:
        targets = [cfg.get(name)]
        if targets[0] is None:
            typer.echo(f"Engine '{name}' not found.", err=True)
            raise typer.Exit(1)
    else:
        targets = cfg.engines

    any_ok = False
    for e in targets:
        ok = is_reachable(e, timeout=timeout)
        icon = typer.style("✓", fg=typer.colors.GREEN) if ok else typer.style("✗", fg=typer.colors.RED)
        typer.echo(f"  {icon}  {e.name:<20} {e.api_url}")
        if ok:
            any_ok = True

    raise typer.Exit(0 if any_ok else 1)

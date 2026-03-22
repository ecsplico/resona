"""CLI commands for managing whisper-server backends."""
from typing import Optional

import typer

from ws_client.config import BackendConfig, BackendEntry, is_reachable

backends_app = typer.Typer(no_args_is_help=True, help="Manage backend server addresses.")


@backends_app.command("list")
def list_backends():
    """List all configured backends and their current reachability."""
    cfg = BackendConfig.load()
    if not cfg.backends:
        typer.echo("No backends configured.")
        typer.echo("  Add one with:  ws-cli backends add <name> <url>")
        return

    for b in cfg.backends:
        ok = is_reachable(b)
        icon = typer.style("✓", fg=typer.colors.GREEN) if ok else typer.style("✗", fg=typer.colors.RED)
        notes: list[str] = []
        if b.api_key:
            notes.append("[auth]")
        if b.compose_dir:
            notes.append(f"[compose: {b.compose_dir}]")
        if b.ssh_host:
            remote_port = b.ssh_remote_port or ""
            rport_str = f":{remote_port}" if remote_port else ""
            notes.append(f"[ssh: {b.ssh_host}{rport_str}]")
        note_str = "  " + "  ".join(notes) if notes else ""
        typer.echo(f"  {icon}  {b.name:<20} {b.api_url}{note_str}")


@backends_app.command("add")
def add_backend(
    name: str = typer.Argument(..., help="Unique name for this backend"),
    api_url: str = typer.Argument(..., help="ws-api base URL, e.g. http://localhost:7000"),
    api_key: str = typer.Option("", "--key", "-k", help="API key (if the server requires one)"),
    compose_dir: Optional[str] = typer.Option(
        None, "--compose-dir", "-c",
        help="Path to docker-compose project dir. When set, this backend can be auto-started.",
    ),
    ssh_host: Optional[str] = typer.Option(
        None, "--ssh", "-s",
        help=(
            "SSH host to tunnel through, e.g. user@myserver.com or user@myserver.com:2222. "
            "Opens a local port-forward (ssh -N -L) when the backend is not directly reachable."
        ),
    ),
    ssh_remote_port: Optional[int] = typer.Option(
        None, "--ssh-remote-port",
        help="Remote port on the SSH host (defaults to the port in api_url).",
    ),
):
    """Add a backend address.

    \b
    Examples:
      # Direct remote server on LAN
      ws-cli backends add lan http://192.168.1.10:7000

      # Local docker-compose (auto-started when needed)
      ws-cli backends add local http://localhost:7000 --compose-dir ~/whisper-server

      # Remote server over SSH tunnel
      ws-cli backends add remote http://localhost:7000 --ssh user@myserver.com

      # Remote server with different local/remote ports
      ws-cli backends add remote http://localhost:17000 --ssh user@myserver.com --ssh-remote-port 7000
    """
    cfg = BackendConfig.load()
    entry = BackendEntry(
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


@backends_app.command("remove")
def remove_backend(
    name: str = typer.Argument(..., help="Name of the backend to remove"),
):
    """Remove a backend."""
    cfg = BackendConfig.load()
    try:
        cfg.remove(name)
        typer.echo(f"Removed '{name}'")
    except KeyError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@backends_app.command("test")
def test_backends(
    name: Optional[str] = typer.Argument(None, help="Backend name to test (tests all if omitted)"),
    timeout: float = typer.Option(3.0, "--timeout", "-t", help="Connection timeout in seconds"),
):
    """Test reachability of one or all backends."""
    cfg = BackendConfig.load()
    if not cfg.backends:
        typer.echo("No backends configured.")
        raise typer.Exit(1)

    if name:
        targets = [cfg.get(name)]
        if targets[0] is None:
            typer.echo(f"Backend '{name}' not found.", err=True)
            raise typer.Exit(1)
    else:
        targets = cfg.backends

    any_ok = False
    for b in targets:
        ok = is_reachable(b, timeout=timeout)
        icon = typer.style("✓", fg=typer.colors.GREEN) if ok else typer.style("✗", fg=typer.colors.RED)
        typer.echo(f"  {icon}  {b.name:<20} {b.api_url}")
        if ok:
            any_ok = True

    raise typer.Exit(0 if any_ok else 1)

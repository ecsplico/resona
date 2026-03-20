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
        key_note = "  [auth]" if b.api_key else ""
        compose_note = f"  [compose: {b.compose_dir}]" if b.compose_dir else ""
        typer.echo(f"  {icon}  {b.name:<20} {b.api_url}{key_note}{compose_note}")


@backends_app.command("add")
def add_backend(
    name: str = typer.Argument(..., help="Unique name for this backend"),
    api_url: str = typer.Argument(..., help="ws-api base URL, e.g. http://192.168.1.10:7000"),
    api_key: str = typer.Option("", "--key", "-k", help="API key (if the server requires one)"),
    compose_dir: Optional[str] = typer.Option(
        None, "--compose-dir", "-c",
        help="Path to docker-compose project dir. When set, this backend can be auto-started.",
    ),
):
    """Add a backend address."""
    cfg = BackendConfig.load()
    entry = BackendEntry(name=name, api_url=api_url.rstrip("/"), api_key=api_key, compose_dir=compose_dir)
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

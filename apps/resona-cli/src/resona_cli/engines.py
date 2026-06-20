"""CLI commands for managing resona engines."""
import json
from typing import Optional

import typer

from resona_client.client import ResonaClient
from resona_client.config import EngineConfig, EngineEntry, is_reachable

engines_app = typer.Typer(no_args_is_help=True, help="Manage engine server addresses.")

BUILTIN_ENGINES = ("faster-whisper", "whisper", "voxtral", "mlx-whisper",
                   "whisper-cpp", "lightning-mlx", "parakeet")


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
    api_url: Optional[str] = typer.Argument(
        None, help="resona-api base URL (resona-api engines only)"),
    api_key: str = typer.Option("", "--key", "-k", help="API key (if the server requires one)"),
    compose_dir: Optional[str] = typer.Option(
        None, "--compose-dir", "-c",
        help="docker-compose project dir; enables auto-start (resona-api only)."),
    ssh_host: Optional[str] = typer.Option(
        None, "--ssh", "-s", help="SSH host to tunnel through (resona-api only)."),
    ssh_remote_port: Optional[int] = typer.Option(
        None, "--ssh-remote-port", help="Remote port on the SSH host."),
    engine_type: str = typer.Option(
        "resona-api", "--type", help="Engine type: resona-api or cloud."),
    provider: Optional[str] = typer.Option(
        None, "--provider", help="Cloud provider: deepgram, elevenlabs, openai."),
    model: Optional[str] = typer.Option(
        None, "--model", help="Provider model override (cloud engines)."),
    private: bool = typer.Option(
        False, "--private", help="Mark a resona-api engine as private."),
    option: list[str] = typer.Option(
        [], "--option", help="Provider option KEY=VALUE (repeatable; cloud engines)."),
):
    """Add a resona-api server engine or a cloud provider engine.

    \b
    Examples:
      # Direct remote server on LAN
      resona engines add lan http://192.168.1.10:7000

      # Local docker-compose (auto-started when needed)
      resona engines add local http://localhost:7000 --compose-dir ~/resona

      # Remote server over SSH tunnel
      resona engines add remote http://localhost:7000 --ssh user@myserver.com

      # Cloud provider
      resona engines add dg --type cloud --provider deepgram --model nova-3
    """
    if name in BUILTIN_ENGINES:
        typer.echo(
            f"Error: '{name}' is a built-in local engine name and cannot be "
            f"used for a config entry.",
            err=True,
        )
        raise typer.Exit(1)

    options: dict = {}
    for item in option:
        if "=" not in item:
            typer.echo(f"Error: --option must be KEY=VALUE, got '{item}'", err=True)
            raise typer.Exit(1)
        key, value = item.split("=", 1)
        options[key] = value

    entry = EngineEntry(
        name=name,
        api_url=(api_url or "").rstrip("/"),
        api_key=api_key,
        compose_dir=compose_dir,
        ssh_host=ssh_host,
        ssh_remote_port=ssh_remote_port,
        type=engine_type,
        provider=provider,
        model=model,
        private=private,
        options=options,
    )

    cfg = EngineConfig.load()
    try:
        cfg.add(entry)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if engine_type == "cloud":
        usable = entry.is_usable()
        status = "key set" if usable else "no API key in environment"
        typer.echo(f"Added cloud engine '{name}' ({provider}) — {status}")
    else:
        ok = is_reachable(entry)
        status = (typer.style("reachable", fg=typer.colors.GREEN)
                  if ok else typer.style("not reachable", fg=typer.colors.YELLOW))
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
        if e.type == "cloud":
            ok = e.is_usable()
            detail = f"{e.provider}  {'key set' if ok else 'no key'}"
        else:
            ok = is_reachable(e, timeout=timeout)
            detail = e.api_url
        icon = typer.style("✓", fg=typer.colors.GREEN) if ok else typer.style("✗", fg=typer.colors.RED)
        typer.echo(f"  {icon}  {e.name:<20} {detail}")
        if ok:
            any_ok = True

    raise typer.Exit(0 if any_ok else 1)


@engines_app.command("benchmark-select")
def benchmark_select(
    run: bool = typer.Option(
        False, "--run/--no-run",
        help="Run the benchmark first (uv run …); otherwise read existing results."),
    results: Optional[str] = typer.Option(
        None, "--results", "-r",
        help="Results file or directory (default: ./benchmarks/results)."),
    speed_floor: float = typer.Option(
        1.0, "--speed-floor",
        help="Minimum × realtime an engine must clear in every language."),
    backends: str = typer.Option(
        "all", "--backends", help="Backends to benchmark when --run (passed through)."),
    target_seconds: float = typer.Option(
        600.0, "--target-seconds", help="Approx audio length per language when --run."),
    apply: bool = typer.Option(
        True, "--apply/--dry-run",
        help="Write the winner to config.json default_engine (--dry-run just prints)."),
):
    """Pick the best local engine from a benchmark and pin it as default_engine.

    Selection rule: lowest average WER among engines that clear --speed-floor in
    every benchmarked language. The winner is written to ~/.resona/config.json
    and honoured by `resona transcribe`, `watch`, and `live`.
    """
    from pathlib import Path
    from resona_asr_core.registry import installed_engines
    from . import benchmark_select as bs

    if run:
        script = bs.find_benchmark_script()
        if script is None:
            typer.echo(
                "Could not find benchmarks/transcription_benchmark.py. Run from "
                "the repo root, or run the benchmark manually and pass --results.",
                err=True,
            )
            raise typer.Exit(2)
        typer.echo(f"Running benchmark: {script}", err=True)
        code = bs.run_benchmark(script, backends=backends, target_seconds=target_seconds)
        if code != 0:
            typer.echo(f"Benchmark exited with code {code}.", err=True)
            raise typer.Exit(code)

    results_path = Path(results) if results else Path.cwd() / "benchmarks" / "results"
    json_file = bs.latest_results_file(results_path)
    if json_file is None:
        typer.echo(
            f"No benchmark results found at {results_path}. Run with --run, or "
            f"point --results at a benchmark_*.json file or its directory.",
            err=True,
        )
        raise typer.Exit(2)

    try:
        payload = json.loads(json_file.read_text())
    except (OSError, json.JSONDecodeError) as e:
        typer.echo(f"Could not read {json_file}: {e}", err=True)
        raise typer.Exit(2)

    installed = set(installed_engines())
    winner, ranking = bs.select_best(
        payload.get("results", []), speed_floor=speed_floor, installed=installed,
    )

    typer.echo(f"Benchmark: {json_file}")
    typer.echo(f"Speed floor: {speed_floor:.2f}× realtime   Installed: {', '.join(sorted(installed))}")
    typer.echo(f"  {'ENGINE':<18}{'AVG WER':<10}{'MIN ×RT':<10}{'LANGS':<10}NOTE")
    for c in ranking:
        mark = "→ " if c is winner else "  "
        note = c.reason if not c.clears_floor else ("best" if c is winner else "")
        typer.echo(
            f"{mark}{c.backend:<18}{c.avg_wer:<10.4f}{c.min_x_realtime:<10.2f}"
            f"{','.join(c.languages):<10}{note}"
        )

    if winner is None:
        typer.echo(
            f"\nNo engine cleared the {speed_floor:.2f}× speed floor. "
            f"Lower --speed-floor and retry.",
            err=True,
        )
        raise typer.Exit(1)

    if not apply:
        typer.echo(f"\n[dry-run] Would set default_engine = {winner.backend}")
        return

    cfg = EngineConfig.load()
    previous = cfg.default_engine
    cfg.default_engine = winner.backend
    cfg.save()
    typer.echo(
        f"\nSet default_engine: {previous} → "
        + typer.style(winner.backend, fg=typer.colors.GREEN)
        + f"  (avg WER {winner.avg_wer:.4f}, ≥ {winner.min_x_realtime:.2f}× realtime)"
    )


@engines_app.command("status")
def engines_status():
    """Show the live gateway catalogue of available engines and their status."""
    try:
        client = ResonaClient.from_config(auto_start=False)
        data = client.list_engines()
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    engines = data.get("engines", [])
    default_name = data.get("default")

    if not engines:
        typer.echo("No engines in catalogue.")
        return

    try:
        from rich.table import Table
        from rich.console import Console

        table = Table(title="Engine Catalogue")
        table.add_column("Name")
        table.add_column("Kind")
        table.add_column("Capabilities")
        table.add_column("Available")
        table.add_column("Models")
        for e in engines:
            name = e["name"]
            if name == default_name:
                name = f"[bold]{name}[/bold] (default)"
            avail = "[green]✓[/green]" if e.get("available") else "[red]✗[/red]"
            caps = ", ".join(e.get("capabilities", []))
            models = ", ".join(e.get("models", [])) or "-"
            table.add_row(name, e.get("kind", ""), caps, avail, models)
        Console().print(table)
    except ImportError:
        typer.echo(f"  {'NAME':<22}{'KIND':<9}{'CAPS':<12}{'AVAIL':<8}MODELS")
        for e in engines:
            name = e["name"]
            if name == default_name:
                name += " (default)"
            avail = "✓" if e.get("available") else "✗"
            caps = ",".join(e.get("capabilities", []))
            models = ",".join(e.get("models", [])) or "-"
            typer.echo(f"  {name:<22}{e.get('kind', ''):<9}{caps:<12}{avail:<8}{models}")

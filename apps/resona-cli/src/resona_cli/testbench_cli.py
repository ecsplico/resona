"""``resona testbench``: contribute recordings and scans to the hub testbench.

Corrections, bench runs and the leaderboard live in the hub's admin UI (``/admin``,
Testbench tab); this command only feeds the testbench and shows what it holds.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import httpx
import typer
from resona_client.client import ResonaAPIError

from .hub import EXIT_ERROR, connect_hub, fail

testbench_app = typer.Typer(
    help="Contribute recordings and scans to the hub testbench. "
    "Correcting references, bench runs and the leaderboard are in the admin UI (/admin).",
    no_args_is_help=True,
)

_ENABLE_HINT = (
    "an org admin enables it with the 'Enable testbench' button on the admin UI Testbench tab (/admin), "
    "or from the CLI by setting testbench_enabled to true: "
    "`resona settings get` -> edit -> `resona settings set --file settings.json`"
)


def _api_fail(e: ResonaAPIError) -> None:
    if e.code == "testbench_disabled":
        fail(f"testbench_disabled: the testbench is off for this organization; {_ENABLE_HINT}")
    fail(f"{e.code or e.status}: {e.message}")


def _expand(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(c for c in p.iterdir() if c.is_file() and not c.name.startswith(".")))
        elif p.is_file():
            files.append(p)
        else:
            fail(f"not a file or directory: {p}")
    return files


@testbench_app.command("add")
def add(
    paths: list[Path] = typer.Argument(..., help="Audio/document files or directories (non-recursive)."),
    language: Optional[str] = typer.Option(None, "--language", "-l", help="Language code (default: organization language)."),
    label: Optional[str] = typer.Option(None, "--label", help="Free-text label (max 120 characters)."),
    as_json: bool = typer.Option(False, "--json", help="Print one JSON list of created items."),
):
    """Contribute files: the hub stores each original until deleted and queues a draft run.

    Needs the organization to have testbench_enabled=true.
    """
    files = _expand(paths)
    if not files:
        fail("no files to contribute")
    client = connect_hub()
    created: list[dict[str, Any]] = []
    errors = 0
    for f in files:
        try:
            item = client.testbench_contribute(f, language=language, label=label)
        except ResonaAPIError as e:
            if e.code == "testbench_disabled":
                _api_fail(e)
            typer.echo(f"{f}: {e.code or e.status}: {e.message}", err=True)
            errors += 1
            continue
        except httpx.HTTPError as e:
            fail(f"hub request failed: {e}")
        created.append(item)
        if not as_json:
            typer.echo(f"{item['id']}\t{item.get('status', '')}\t{item.get('kind', '')}\t{f}")
    if as_json:
        typer.echo(json.dumps(created, ensure_ascii=False, indent=2))
    if errors:
        raise typer.Exit(EXIT_ERROR)


@testbench_app.command("list")
def list_items(
    status: Optional[str] = typer.Option(None, "--status", help="awaiting_draft | needs_correction | corrected."),
    kind: Optional[str] = typer.Option(None, "--kind", help="audio | document."),
    limit: int = typer.Option(50, "--limit", min=1, max=100, help="Page size (max 100)."),
    cursor: Optional[str] = typer.Option(None, "--cursor", help="Cursor from a previous page."),
    as_json: bool = typer.Option(False, "--json", help="Print the page as JSON."),
):
    """List testbench items, newest first (organization admin)."""
    client = connect_hub()
    try:
        page = client.testbench_list_items(kind=kind, status=status, limit=limit, cursor=cursor)
    except ResonaAPIError as e:
        _api_fail(e)
    except httpx.HTTPError as e:
        fail(f"hub request failed: {e}")
    if as_json:
        typer.echo(json.dumps(page, ensure_ascii=False, indent=2))
        return
    for i in page["items"]:
        typer.echo(f"{i['id']}\t{i['kind']}\t{i['status']}\truns={i['run_count']}\t{i.get('label', '')}")
    if page.get("next_cursor"):
        typer.echo(f"next: resona testbench list --cursor {page['next_cursor']}", err=True)


@testbench_app.command("status")
def status():
    """Show whether the testbench is enabled and how many items, corrected items and runs exist."""
    client = connect_hub()
    try:
        s = client.testbench_status()
    except ResonaAPIError as e:
        _api_fail(e)
    except httpx.HTTPError as e:
        fail(f"hub request failed: {e}")
    typer.echo(f"enabled: {str(s['enabled']).lower()}")
    typer.echo(f"items: {s['items']}  corrected: {s['corrected']}  runs: {s['runs']}")
    if not s["enabled"]:
        typer.echo(_ENABLE_HINT, err=True)

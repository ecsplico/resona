"""``resona webhooks``: register job-notification URLs on the hub (organization admin).

The signing secret is printed exactly once, by ``add`` and ``rotate``, alone on
stdout (so ``resona webhooks add … > secret`` stores it); every other line goes to
stderr. ``list`` and ``deliveries`` never show it. A job names an endpoint with
``--webhook <name>`` on ``transcribe``, ``doc`` and ``postprocess``.
"""
from __future__ import annotations

import dataclasses
import json
from typing import Any, Callable, NoReturn, TypeVar

import httpx
import typer
from resona_client.client import ResonaAPIError, WebhookCreated

from .hub import connect_hub, fail

webhooks_app = typer.Typer(
    help="Register job webhook endpoints (organization admin). The secret is shown once.",
    no_args_is_help=True,
)

T = TypeVar("T")


def _api_fail(e: ResonaAPIError) -> NoReturn:
    fail(f"{e.code or e.status}: {e.message}")


def _call(fn: Callable[[], T]) -> T:
    try:
        return fn()
    except ResonaAPIError as e:
        _api_fail(e)
    except httpx.HTTPError as e:
        fail(f"hub request failed: {e}")


def _print_secret(created: WebhookCreated, action: str) -> None:
    typer.echo(f"{action} webhook {created.name} -> {created.url}", err=True)
    typer.echo("Signing secret (shown once; store it in your receiver's configuration):", err=True)
    typer.echo(created.secret)


@webhooks_app.command("add")
def add(
    name: str = typer.Argument(..., help="Endpoint name: 1-63 of a-z, 0-9, '-'; jobs refer to it."),
    url: str = typer.Argument(..., help="HTTPS URL the hub POSTs job events to."),
):
    """Register an endpoint and print its signing secret once (stdout)."""
    client = connect_hub()
    _print_secret(_call(lambda: client.create_webhook(name, url)), "Registered")


@webhooks_app.command("list")
def list_webhooks(
    as_json: bool = typer.Option(False, "--json", help="Print the endpoints as JSON."),
):
    """List the organization's active endpoints (no secrets)."""
    client = connect_hub()
    items = _call(client.list_webhooks)
    if as_json:
        typer.echo(json.dumps([dataclasses.asdict(w) for w in items], ensure_ascii=False, indent=2))
        return
    for w in items:
        typer.echo(f"{w.name}\t{w.url}\t{w.created_at}")


@webhooks_app.command("rotate")
def rotate(name: str = typer.Argument(..., help="Endpoint name.")):
    """Replace the signing secret; the old one stops signing at once. Prints the new one once."""
    client = connect_hub()
    _print_secret(_call(lambda: client.rotate_webhook(name)), "Rotated")


@webhooks_app.command("remove")
def remove(name: str = typer.Argument(..., help="Endpoint name.")):
    """Revoke an endpoint; its name becomes free for a new registration."""
    client = connect_hub()
    _call(lambda: client.delete_webhook(name))
    typer.echo(f"Removed webhook {name}", err=True)


@webhooks_app.command("deliveries")
def deliveries(
    name: str = typer.Argument(..., help="Endpoint name."),
    limit: int = typer.Option(50, "--limit", min=1, max=100, help="Number of deliveries (newest first)."),
    as_json: bool = typer.Option(False, "--json", help="Print the deliveries as JSON."),
):
    """Show an endpoint's recent deliveries, newest first: job, event, attempts, last HTTP status."""
    client = connect_hub()
    items = _call(lambda: client.list_webhook_deliveries(name, limit=limit))
    if as_json:
        rows: list[dict[str, Any]] = [dataclasses.asdict(d) for d in items]
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for d in items:
        status = "-" if d.last_status is None else str(d.last_status)
        state = f"delivered {d.delivered_at}" if d.delivered_at else (d.last_error or "pending")
        typer.echo(f"{d.id}\tjob {d.job_id}\t{d.event}\tattempts={d.attempts}\tstatus={status}\t{state}")

"""``resona settings``: the organization's revisioned hub settings.

``get``/``pull`` fetch the current revision and cache it (non-secret) under
``~/.resona/settings-cache/`` keyed by hub URL and organization. ``set`` replaces
the settings with ``If-Match`` set to the cached revision's ETag (or the current
one when nothing is cached); a concurrent change makes the hub answer 409 and
nothing is written. Writes need an ``org_admin`` credential.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import httpx
import typer
from pydantic import ValidationError
from resona_client.client import ResonaAPIError, ResonaClient, RevisionConflict, SettingsDoc
from resona_job_core import OrganizationSettings

from . import settings_cache
from .hub import connect_hub, fail

settings_app = typer.Typer(help="Organization settings stored on the hub.", no_args_is_help=True)


def _fetch(client: ResonaClient) -> SettingsDoc:
    try:
        return client.get_settings()
    except ResonaAPIError as e:
        fail(f"{e.code or e.status}: {e.message}")
    except httpx.HTTPError as e:
        fail(f"hub request failed: {e}")


def _document(doc: SettingsDoc | settings_cache.CachedSettings) -> dict[str, Any]:
    return {"organization_id": doc.organization_id, "revision": doc.revision, "settings": doc.settings}


@settings_app.command("get")
def get_settings():
    """Print the current settings revision as JSON (and refresh the cache)."""
    client = connect_hub()
    doc = _fetch(client)
    settings_cache.store(doc, hub_url=client.base_url, api_key=client.api_key)
    typer.echo(json.dumps(_document(doc), ensure_ascii=False, indent=2))


@settings_app.command("pull")
def pull_settings():
    """Cache the current settings revision for --local runs."""
    client = connect_hub()
    doc = _fetch(client)
    entry = settings_cache.store(doc, hub_url=client.base_url, api_key=client.api_key)
    typer.echo(f"cached revision {doc.revision} of organization {doc.organization_id} -> {entry.path}")


def _parse_assignment(raw: str) -> tuple[str, Any]:
    key, sep, value = raw.partition("=")
    key = key.strip()
    if not sep or not key:
        fail(f"expected key=value, got {raw!r}")
    if key not in OrganizationSettings.model_fields:
        fail(f"unknown settings key {key!r}; known: {', '.join(OrganizationSettings.model_fields)}")
    try:
        return key, json.loads(value)
    except ValueError:
        return key, value


@settings_app.command("set")
def set_settings(
    assignments: Optional[list[str]] = typer.Argument(
        None, help="Top-level key=value changes (values parsed as JSON when possible).",
        metavar="[KEY=VALUE]..."),
    file: Optional[Path] = typer.Option(None, "--file", help="JSON file with the complete settings."),
):
    """Replace the organization's settings (org_admin), checked against the cached revision.

    Examples: `resona settings set language=en default_execution_profile=cpu-private`,
    `resona settings set --file settings.json`.
    """
    if bool(file) == bool(assignments):
        fail("give either --file or key=value assignments")
    client = connect_hub()
    cached = settings_cache.find(client.base_url, client.api_key)
    base: SettingsDoc | settings_cache.CachedSettings = cached or _fetch(client)

    if file is not None:
        try:
            new_settings = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            fail(f"cannot read {file}: {e}")
        if not isinstance(new_settings, dict):
            fail(f"{file} must contain a JSON object")
    else:
        new_settings = dict(base.settings)
        for raw in assignments or []:
            key, value = _parse_assignment(raw)
            new_settings[key] = value
    try:
        validated = OrganizationSettings.model_validate(new_settings)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())
        fail(f"invalid settings: {problems}")

    try:
        doc = client.put_settings(validated.model_dump(mode="json"), if_match=base.etag)
    except RevisionConflict:
        fail(f"revision_conflict: the settings changed on the hub since revision {base.revision}; "
             "run `resona settings pull` and retry")
    except ResonaAPIError as e:
        fail(f"{e.code or e.status}: {e.message}")
    except httpx.HTTPError as e:
        fail(f"hub request failed: {e}")
    settings_cache.store(doc, hub_url=client.base_url, api_key=client.api_key)
    typer.echo(f"saved revision {doc.revision} of organization {doc.organization_id} "
               "(applies to new jobs only)")

import json
from pathlib import Path

import typer

from resona_client.client import ResonaClient

profiles_app = typer.Typer(no_args_is_help=True)


def _client() -> ResonaClient:
    return ResonaClient.from_config()


@profiles_app.command("list")
def list_profiles():
    """List profiles stored on the server."""
    try:
        items = _client().list_profiles()
    except Exception as e:
        print(f"Error listing profiles: {e}")
        raise typer.Exit(1)
    if not items:
        print("No profiles found.")
        return
    for p in items:
        print(f"  {p['name']:20s} {p.get('description', '')}")


@profiles_app.command("show")
def show_profile(name: str = typer.Argument(..., help="Profile name.")):
    """Print a profile's JSON."""
    try:
        print(json.dumps(_client().get_profile(name), indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"Error: {e}")
        raise typer.Exit(1)


@profiles_app.command("push")
def push_profile(
    name: str = typer.Argument(..., help="Profile name on the server."),
    path: Path = typer.Argument(..., help="Local profile JSON file to upload."),
):
    """Upload a local profile file to the server."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _client().put_profile(name, data)
        print(f"Pushed profile '{name}'")
    except Exception as e:
        print(f"Error pushing profile: {e}")
        raise typer.Exit(1)


@profiles_app.command("pull")
def pull_profile(
    name: str = typer.Argument(..., help="Profile name on the server."),
    path: Path = typer.Argument(None, help="Destination file (default: <name>.json)."),
):
    """Download a server profile to a local file."""
    try:
        data = _client().get_profile(name)
    except Exception as e:
        print(f"Error: {e}")
        raise typer.Exit(1)
    dest = path or Path(f"{name}.json")
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Pulled profile '{name}' -> {dest}")


@profiles_app.command("delete")
def delete_profile(name: str = typer.Argument(..., help="Profile name.")):
    """Delete a server profile."""
    try:
        _client().delete_profile(name)
        print(f"Deleted profile '{name}'")
    except Exception as e:
        print(f"Error deleting profile: {e}")
        raise typer.Exit(1)

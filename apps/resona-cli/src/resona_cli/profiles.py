import json
from pathlib import Path
from typing import Callable

import typer

from resona_client.client import ResonaAPIError, ResonaClient
from resona_postprocess.compose import bundled_block
from resona_postprocess.profile import Profile

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
def show_profile(
    name: str = typer.Argument(..., help="Profile name."),
    resolved: bool = typer.Option(
        False, "--resolved", help="Show the flattened profile the hub pins for jobs (includes resolved)."
    ),
):
    """Print a profile's JSON."""
    try:
        print(json.dumps(_client().get_profile(name, resolved=resolved), indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"Error: {e}")
        raise typer.Exit(1)


class PushError(Exception):
    """The profiles to push are unusable; nothing was uploaded."""


def _push_files(targets: list[str]) -> dict[str, Path]:
    """Map the hub name of each profile to push to its file.

    ``NAME FILE`` (two arguments, the first no path) stores FILE under NAME. Otherwise each
    target is a JSON file, stored under its file name, or a directory: its ``*.json`` and
    ``blocks/*.json``.
    """
    if (
        len(targets) == 2
        and not targets[0].endswith(".json")
        and not Path(targets[0]).exists()
        and Path(targets[1]).is_file()
    ):
        return {targets[0]: Path(targets[1])}
    files: dict[str, Path] = {}
    for target in targets:
        path = Path(target)
        if path.is_dir():
            found = sorted(path.glob("*.json")) + sorted((path / "blocks").glob("*.json"))
            if not found:
                raise PushError(f"{path}: no profile files (*.json, blocks/*.json)")
        elif path.is_file():
            found = [path]
        else:
            raise PushError(f"{path}: no such file or directory")
        for file in found:
            other = files.setdefault(file.stem, file)
            if other != file:
                raise PushError(f"two files for profile '{file.stem}': {other} and {file}")
    return files


def _push_order(docs: dict[str, dict], stored: Callable[[str], bool]) -> list[str]:
    """Names in ``docs`` ordered so that every profile comes after the profiles it includes.

    An include outside ``docs`` must be a bundled block or ``stored`` on the hub. Raises
    :class:`PushError` for a cycle among ``docs`` or a missing include.
    """
    order: list[str] = []
    state: dict[str, str] = {}

    def visit(name: str, chain: list[str]) -> None:
        if state.get(name) == "done":
            return
        if state.get(name) == "visiting":
            raise PushError(f"include cycle: {' -> '.join([*chain, name])}")
        state[name] = "visiting"
        for step in docs[name].get("steps", []):
            if step.get("type") != "include":
                continue
            target = step["profile"]
            if target in docs:
                visit(target, [*chain, name])
            elif bundled_block(target) is None and not stored(target):
                raise PushError(
                    f"profile '{name}' includes '{target}', which is neither pushed with it, "
                    "a bundled block nor stored on the hub"
                )
        state[name] = "done"
        order.append(name)

    for name in docs:
        visit(name, [])
    return order


def _stored_on(client: ResonaClient) -> Callable[[str], bool]:
    def stored(name: str) -> bool:
        try:
            client.get_profile(name)
        except ResonaAPIError as e:
            if e.status == 404:
                return False
            raise
        return True

    return stored


@profiles_app.command("push")
def push_profile(
    targets: list[str] = typer.Argument(
        ...,
        help="Profile JSON files or directories (a directory adds its *.json and blocks/*.json), "
        "each stored under its file name; or NAME FILE to store one file under NAME.",
    ),
):
    """Upload local profiles to the server, blocks before the profiles that include them.

    Include steps must name a profile pushed in the same call, a bundled block or a profile
    already stored on the server. A cycle, a missing include or an invalid file stops the
    push before anything is uploaded.
    """
    try:
        client = _client()
        files = _push_files(targets)
        docs: dict[str, dict] = {}
        for name, path in files.items():
            data = json.loads(path.read_text(encoding="utf-8"))
            Profile.from_dict(data)
            docs[name] = data
        order = _push_order(docs, _stored_on(client))
    except Exception as e:
        print(f"Error pushing profiles: {e}")
        raise typer.Exit(1)
    for name in order:
        try:
            client.put_profile(name, docs[name])
        except Exception as e:
            print(f"Error pushing profile '{name}': {e}")
            raise typer.Exit(1)
        print(f"Pushed profile '{name}'")


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

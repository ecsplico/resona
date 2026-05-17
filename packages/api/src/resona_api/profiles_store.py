"""Server-side profile-file storage under PROFILES_PATH."""

import json
import re
from pathlib import Path

from resona_postprocess.profile import Profile, ProfileError, list_profiles

from .paths import PROFILES_PATH

_NAME_RE = re.compile(r"^[\w-]{1,64}$")


class ProfileNameError(ValueError):
    """Raised when a profile name is not a safe filename stem."""


def _path(name: str) -> Path:
    if not _NAME_RE.match(name):
        raise ProfileNameError(f"Invalid profile name: {name!r}")
    return PROFILES_PATH / f"{name}.json"


def list_all() -> list[dict]:
    return list_profiles(PROFILES_PATH)


def read(name: str) -> dict | None:
    path = _path(name)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write(name: str, data: dict) -> dict:
    """Validate `data` as a Profile, then persist it. Returns the stored dict."""
    Profile.from_dict(data)  # raises ProfileError on invalid input
    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


def delete(name: str) -> bool:
    path = _path(name)
    if not path.exists():
        return False
    path.unlink()
    return True

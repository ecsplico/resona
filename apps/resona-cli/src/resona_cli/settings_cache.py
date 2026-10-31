"""Local cache of revisioned, non-secret hub settings.

One file per ``(hub URL, organization_id)`` under ``~/.resona/settings-cache/``:
``{hub_url, organization_id, credential, revision, etag, settings, fetched_at, stale}``.
``credential`` is a short SHA-256 prefix of the client token so that explicit
``--local`` runs can find their organization's cache without contacting the hub;
the token itself is never written. ``stale`` turns true once an offline local run
used the cache; the next ``resona settings pull``/``get`` clears it.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from resona_client.client import SettingsDoc

CACHE_DIR = Path.home() / ".resona" / "settings-cache"


def credential_fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def _cache_path(cache_dir: Path, hub_url: str, organization_id: str) -> Path:
    digest = hashlib.sha256(f"{hub_url.rstrip('/')}\n{organization_id}".encode()).hexdigest()[:32]
    return cache_dir / f"{digest}.json"


@dataclass(frozen=True)
class CachedSettings:
    path: Path
    hub_url: str
    organization_id: str
    revision: int
    etag: str
    settings: dict[str, Any]
    fetched_at: str
    stale: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "hub_url": self.hub_url,
            "organization_id": self.organization_id,
            "revision": self.revision,
            "settings": self.settings,
            "fetched_at": self.fetched_at,
            "stale": self.stale,
        }


def _write_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def store(doc: SettingsDoc, *, hub_url: str, api_key: str,
          cache_dir: Optional[Path] = None) -> CachedSettings:
    """Write a freshly fetched settings revision; clears ``stale``."""
    directory = cache_dir or CACHE_DIR
    path = _cache_path(directory, hub_url, doc.organization_id)
    data = {
        "hub_url": hub_url.rstrip("/"),
        "organization_id": doc.organization_id,
        "credential": credential_fingerprint(api_key),
        "revision": doc.revision,
        "etag": doc.etag,
        "settings": doc.settings,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "stale": False,
    }
    _write_atomic(path, data)
    return _parse(path, data)


def _parse(path: Path, data: dict[str, Any]) -> CachedSettings:
    return CachedSettings(
        path=path,
        hub_url=str(data["hub_url"]),
        organization_id=str(data["organization_id"]),
        revision=int(data["revision"]),
        etag=str(data["etag"]),
        settings=dict(data["settings"]),
        fetched_at=str(data.get("fetched_at", "")),
        stale=bool(data.get("stale", False)),
    )


def find(hub_url: str, api_key: str, *, cache_dir: Optional[Path] = None) -> Optional[CachedSettings]:
    """The cache entry for this hub and credential, without contacting the hub."""
    directory = cache_dir or CACHE_DIR
    if not directory.is_dir():
        return None
    wanted_url = hub_url.rstrip("/")
    wanted_credential = credential_fingerprint(api_key)
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("hub_url") == wanted_url and data.get("credential") == wanted_credential:
                return _parse(path, data)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def mark_stale(entry: CachedSettings) -> CachedSettings:
    """Record that an offline run used this cache without refreshing it from the hub."""
    data = json.loads(entry.path.read_text(encoding="utf-8"))
    data["stale"] = True
    _write_atomic(entry.path, data)
    return _parse(entry.path, data)

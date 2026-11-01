"""``eval/baselines.json``: the promoted baseline per (execution profile, platform).

The hub is authoritative for remote evaluations (``GET /v1/baselines``); this file
pins what was promoted (corpus hash, release, runtime and model revisions, per-fixture
metrics) in Git and is the only baseline source for local evaluations.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class BaselineFileError(Exception):
    pass


def baseline_key(execution_profile: str, platform: str) -> str:
    return f"{execution_profile}/{platform}"


def load_baselines(path: Path) -> dict[str, Any]:
    """Parsed file, or an empty document when it does not exist yet."""
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "baselines": {}}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BaselineFileError(f"cannot read {path}: {exc}") from None
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION or not isinstance(
        doc.get("baselines"), dict
    ):
        raise BaselineFileError(f"{path} is not a schema_version {SCHEMA_VERSION} baselines file")
    return doc


def find_baseline(path: Path, execution_profile: str, platform: str) -> dict[str, Any] | None:
    return load_baselines(path)["baselines"].get(baseline_key(execution_profile, platform))


def comparable(entry: Mapping[str, Any] | None, corpus_sha256: str) -> Mapping[str, Any] | None:
    """A baseline counts only for the same corpus; another corpus means ``baseline_required``."""
    if entry is None or entry.get("corpus_sha256") != corpus_sha256:
        return None
    return entry


def promoted_entry(
    report: Mapping[str, Any], *, evaluation_id: int, revision: int, promoted_at: str
) -> dict[str, Any]:
    """Baseline entry pinned from an evaluation report the hub just promoted."""
    return {
        "execution_profile": report["execution_profile"],
        "platform": report["platform"],
        "revision": revision,
        "evaluation_id": evaluation_id,
        "promoted_at": promoted_at,
        "corpus_sha256": report["corpus_sha256"],
        "release_id": report["release_id"],
        "runtime_versions": report["runtime_versions"],
        "normalization": report["normalization"],
        "models": report["models"],
        "fixtures": {fid: dict(entry["metrics"]) for fid, entry in report["fixtures"].items()},
    }


def upsert_baseline(path: Path, entry: Mapping[str, Any]) -> None:
    """Replace the entry for its (execution profile, platform), atomically."""
    doc = load_baselines(path)
    doc["baselines"][baseline_key(entry["execution_profile"], entry["platform"])] = dict(entry)
    doc["baselines"] = dict(sorted(doc["baselines"].items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".baselines-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

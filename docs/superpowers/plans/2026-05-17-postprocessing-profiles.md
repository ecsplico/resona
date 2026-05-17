# Postprocessing Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add named, use-case-specific postprocessing **profiles** (initial-prompt + an ordered chain of `replacements`/`llm`/`extract` steps) reachable from the async `/jobs` API, the sync `/v1/audio/transcriptions` route, and both CLI paths.

**Architecture:** Profiles are flat JSON files in one unified format. `resona-postprocess` owns the `Profile` abstraction and pipeline; `resona-api` and `resona-cli` consume it. The `Replacement` and `InitialPrompt` DB tables are removed; the API DB holds only `Job` rows. A job names a stored profile or submits one inline.

**Tech Stack:** Python 3.12, SQLModel/SQLite, FastAPI, typer, litellm, uv workspace, pytest + respx.

**Spec:** `docs/superpowers/specs/2026-05-17-postprocessing-profiles-design.md`

---

## File Structure

**resona-postprocess** (`packages/postprocess/src/resona_postprocess/`)
- `profile.py` — *new*. `Profile` dataclass, validation, `resolve_profile`, `list_profiles`, `bundled_default`.
- `pipeline.py` — *modify*. `PostprocessResult`, result-carrying `run()`, `build_pipeline(profile)`.
- `llm.py` — *modify*. Harden; add `llm_transform()` and `llm_extract()`.
- `replacements.py` — *modify*. Accept `pattern` and legacy `name` keys.
- `sources.py` — *delete*. Role folds into `profile.py`.
- `profiles/default.json` — *new*. Bundled `default` profile (package data).
- `default_replacements.json` — *unchanged*. Referenced by the bundled default.

**resona-api** (`packages/api/src/resona_api/`)
- `db/models.py` — *modify*. Drop `Replacement`/`InitialPrompt`; add 3 `Job` fields.
- `db/engine.py` — *modify*. Drop `populate_*`; add `Job` columns to existing DBs.
- `db/presets.py` — *modify*. Keep tuples only as migration-export source data.
- `db/utils.py` — *modify*. Drop replacement/prompt helpers; `register_job` gains `profile`.
- `migration.py` — *new*. One-shot export of old tables to `default.json`.
- `profiles_store.py` — *new*. Server-side profile-file read/write/list/delete.
- `profiles_routes.py` — *new*. `/profiles` CRUD router.
- `paths.py` — *modify*. Add `PROFILES_PATH`.
- `endpoints.py` — *modify*. `POST /jobs` `profile` field; remove `/replacements/*`, `/prompts/*`.
- `audio_routes.py` — *modify*. `profile` field, full pipeline, `structured` in response.
- `tasks_transcribe.py` — *modify*. Resolve profile, snapshot, run pipeline, sidecar.
- `app.py` — *modify*. Register profiles router; run migration; drop `populate_*`.

**resona-client** (`packages/client/src/resona_client/`)
- `client.py` — *modify*. Drop replacement/prompt methods; add profile methods; `profile` args.
- `config.py` — *modify*. `EngineConfig.default_profile`.

**resona-cli** (`apps/resona-cli/src/resona_cli/`)
- `profiles.py` — *new*. `resona profiles` subcommand.
- `replacements.py`, `prompts.py` — *delete*.
- `main.py` — *modify*. Register `profiles`; drop `replacements`/`prompts`.
- `transcribe.py` — *modify*. `--profile` option for both paths.

**Docs**
- `CLAUDE.md`, `docs/configuration/environment.md`, `docs/` postprocessing pages — *modify*.

---

## Conventions for every task

- TDD: write the failing test, run it, see it fail, implement, run, see it pass, commit.
- Run tests with `uv run pytest <path> -v` from the repo root.
- Commit messages: `feat:`/`refactor:`/`test:`/`docs:` prefix, imperative mood.
- Use `config()` from `python-decouple` for env vars in API code; never `os.environ[]`.

---

## Task 1: Replacements loader accepts `pattern` and legacy `name`

**Files:**
- Modify: `packages/postprocess/src/resona_postprocess/replacements.py`
- Test: `packages/postprocess/tests/test_replacements.py`

- [ ] **Step 1: Write the failing test**

Add to `test_replacements.py`:

```python
from resona_postprocess.replacements import apply_replacements


def test_apply_replacements_new_pattern_key():
    rules = [{"pattern": r"\bKomma\b", "replacement": ","}]
    assert apply_replacements("a Komma b", rules) == "a , b"


def test_apply_replacements_legacy_name_key():
    rules = [{"name": r"\bKomma\b", "replacement": ","}]
    assert apply_replacements("a Komma b", rules) == "a , b"


def test_apply_replacements_skips_invalid_pattern():
    rules = [{"pattern": "[", "replacement": "x"},
             {"pattern": r"\bok\b", "replacement": "OK"}]
    assert apply_replacements("ok", rules) == "OK"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/postprocess/tests/test_replacements.py -v`
Expected: `test_apply_replacements_new_pattern_key` FAILS (loader reads only `name`).

- [ ] **Step 3: Implement**

Replace the body of `apply_replacements` in `replacements.py`:

```python
def apply_replacements(text: str, replacements: list[dict[str, str]]) -> str:
    """Apply a list of regex replacements to text in order.

    Each rule provides the regex under ``pattern`` (preferred) or the legacy
    ``name`` key, plus ``replacement``. Invalid patterns are logged and skipped.
    """
    for r in replacements:
        pattern = r.get("pattern", r.get("name"))
        if pattern is None:
            log.warning("Replacement rule missing 'pattern'/'name': %r", r)
            continue
        try:
            new_text, n = re.compile(pattern, re.IGNORECASE).subn(
                r.get("replacement", ""), text
            )
            if n > 0:
                text = new_text
        except re.error as e:
            log.warning(f"Invalid replacement pattern '{pattern}': {e}")
    return text
```

- [ ] **Step 4: Run tests** — `uv run pytest packages/postprocess/tests/test_replacements.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/postprocess/src/resona_postprocess/replacements.py packages/postprocess/tests/test_replacements.py
git commit -m "feat(postprocess): accept pattern key in replacement rules"
```

---

## Task 2: `Profile` dataclass and validation

**Files:**
- Create: `packages/postprocess/src/resona_postprocess/profile.py`
- Test: `packages/postprocess/tests/test_profile.py`

- [ ] **Step 1: Write the failing test**

Create `packages/postprocess/tests/test_profile.py`:

```python
import pytest
from resona_postprocess.profile import Profile, ProfileError


def _ok_profile():
    return {
        "name": "p1",
        "description": "d",
        "initial_prompt": ["Befund"],
        "steps": [
            {"type": "replacements", "rules": [{"pattern": r"\bx\b", "replacement": "y"}]},
            {"type": "llm", "prompt": "format"},
            {"type": "extract", "name": "fields", "prompt": "extract"},
        ],
    }


def test_from_dict_valid():
    p = Profile.from_dict(_ok_profile())
    assert p.name == "p1"
    assert p.initial_prompt == ["Befund"]
    assert len(p.steps) == 3


def test_from_dict_requires_name():
    data = _ok_profile()
    del data["name"]
    with pytest.raises(ProfileError, match="name"):
        Profile.from_dict(data)


def test_from_dict_rejects_unknown_step_type():
    data = _ok_profile()
    data["steps"].append({"type": "magic"})
    with pytest.raises(ProfileError, match="step type"):
        Profile.from_dict(data)


def test_from_dict_rejects_uncompilable_regex():
    data = _ok_profile()
    data["steps"][0]["rules"][0]["pattern"] = "["
    with pytest.raises(ProfileError, match="regex"):
        Profile.from_dict(data)


def test_from_dict_rejects_duplicate_extract_names():
    data = _ok_profile()
    data["steps"].append({"type": "extract", "name": "fields", "prompt": "again"})
    with pytest.raises(ProfileError, match="extract"):
        Profile.from_dict(data)


def test_from_dict_rejects_llm_without_prompt():
    data = _ok_profile()
    data["steps"][1] = {"type": "llm"}
    with pytest.raises(ProfileError, match="prompt"):
        Profile.from_dict(data)


def test_to_dict_roundtrip():
    p = Profile.from_dict(_ok_profile())
    assert Profile.from_dict(p.to_dict()).steps == p.steps
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/postprocess/tests/test_profile.py -v`
Expected: FAIL — `No module named 'resona_postprocess.profile'`.

- [ ] **Step 3: Implement**

Create `packages/postprocess/src/resona_postprocess/profile.py`:

```python
"""Profile — a named postprocessing configuration bundle."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

VALID_STEP_TYPES = {"replacements", "llm", "extract"}


class ProfileError(ValueError):
    """Raised when a profile is malformed."""


def _validate_step(step: dict, idx: int, extract_names: set[str]) -> None:
    if not isinstance(step, dict):
        raise ProfileError(f"Step {idx} must be an object")
    stype = step.get("type")
    if stype not in VALID_STEP_TYPES:
        raise ProfileError(
            f"Step {idx}: unknown step type {stype!r} "
            f"(expected one of {sorted(VALID_STEP_TYPES)})"
        )
    if stype == "replacements":
        if "rules" not in step and "source" not in step:
            raise ProfileError(f"Step {idx}: replacements needs 'rules' or 'source'")
        for rule in step.get("rules", []):
            pattern = rule.get("pattern", rule.get("name"))
            if pattern is None:
                raise ProfileError(f"Step {idx}: a rule is missing 'pattern'")
            try:
                re.compile(pattern)
            except re.error as e:
                raise ProfileError(f"Step {idx}: invalid regex {pattern!r}: {e}") from e
    elif stype == "llm":
        if not step.get("prompt"):
            raise ProfileError(f"Step {idx}: llm step needs a 'prompt'")
    elif stype == "extract":
        if not step.get("prompt"):
            raise ProfileError(f"Step {idx}: extract step needs a 'prompt'")
        name = step.get("name", "extract")
        if name in extract_names:
            raise ProfileError(f"Step {idx}: duplicate extract name {name!r}")
        extract_names.add(name)


@dataclass
class Profile:
    """A named postprocessing configuration: initial prompt + ordered steps."""

    name: str
    description: str = ""
    initial_prompt: list[str] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    base_dir: Path | None = None  # for resolving relative `source` paths

    @classmethod
    def from_dict(cls, data: dict, *, base_dir: Path | None = None) -> "Profile":
        if not isinstance(data, dict):
            raise ProfileError("Profile must be a JSON object")
        name = data.get("name")
        if not name or not isinstance(name, str):
            raise ProfileError("Profile 'name' is required and must be a string")
        steps = data.get("steps", [])
        if not isinstance(steps, list):
            raise ProfileError("Profile 'steps' must be a list")
        extract_names: set[str] = set()
        for idx, step in enumerate(steps):
            _validate_step(step, idx, extract_names)
        ip = data.get("initial_prompt", [])
        if not isinstance(ip, list):
            raise ProfileError("Profile 'initial_prompt' must be a list of strings")
        return cls(
            name=name,
            description=data.get("description", ""),
            initial_prompt=[str(p) for p in ip],
            steps=steps,
            base_dir=base_dir,
        )

    @classmethod
    def from_file(cls, path: Path | str) -> "Profile":
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ProfileError(f"Cannot load profile {path}: {e}") from e
        return cls.from_dict(data, base_dir=path.parent)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "initial_prompt": self.initial_prompt,
            "steps": self.steps,
        }

    def initial_prompt_string(self) -> str:
        """Return initial-prompt phrases joined for the engine."""
        return ", ".join(self.initial_prompt)
```

- [ ] **Step 4: Run tests** — `uv run pytest packages/postprocess/tests/test_profile.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/postprocess/src/resona_postprocess/profile.py packages/postprocess/tests/test_profile.py
git commit -m "feat(postprocess): add Profile dataclass with validation"
```

---

## Task 3: Profile resolution, listing, and bundled default

**Files:**
- Modify: `packages/postprocess/src/resona_postprocess/profile.py`
- Create: `packages/postprocess/src/resona_postprocess/profiles/default.json`
- Test: `packages/postprocess/tests/test_profile.py`

- [ ] **Step 1: Write the failing test**

Append to `test_profile.py`:

```python
import json as _json
from resona_postprocess.profile import (
    resolve_profile, list_profiles, bundled_default,
)


def test_bundled_default_loads():
    p = bundled_default()
    assert p.name == "default"
    assert any(s["type"] == "replacements" for s in p.steps)


def test_resolve_profile_by_name(tmp_path):
    (tmp_path / "arzt.json").write_text(_json.dumps(
        {"name": "arzt", "steps": []}))
    p = resolve_profile("arzt", tmp_path)
    assert p.name == "arzt"


def test_resolve_profile_inline_json(tmp_path):
    p = resolve_profile('{"name": "inline", "steps": []}', tmp_path)
    assert p.name == "inline"


def test_resolve_profile_dict(tmp_path):
    p = resolve_profile({"name": "d", "steps": []}, tmp_path)
    assert p.name == "d"


def test_resolve_profile_default_falls_back_to_bundled(tmp_path):
    p = resolve_profile("default", tmp_path)
    assert p.name == "default"


def test_resolve_profile_file_shadows_bundled(tmp_path):
    (tmp_path / "default.json").write_text(_json.dumps(
        {"name": "default", "description": "user", "steps": []}))
    p = resolve_profile("default", tmp_path)
    assert p.description == "user"


def test_resolve_profile_unknown_raises(tmp_path):
    with pytest.raises(ProfileError, match="not found"):
        resolve_profile("nope", tmp_path)


def test_list_profiles(tmp_path):
    (tmp_path / "a.json").write_text(_json.dumps(
        {"name": "a", "description": "AA", "steps": []}))
    out = list_profiles(tmp_path)
    assert {"name": "a", "description": "AA"} in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/postprocess/tests/test_profile.py -v`
Expected: FAIL — `resolve_profile` etc. not defined.

- [ ] **Step 3: Create the bundled default profile**

Create `packages/postprocess/src/resona_postprocess/profiles/default.json`:

```json
{
  "name": "default",
  "description": "Default German dictation replacements, no LLM.",
  "initial_prompt": [],
  "steps": [
    {"type": "replacements", "name": "house-defaults", "source": "default_replacements.json"}
  ]
}
```

- [ ] **Step 4: Implement resolution helpers**

Append to `profile.py`:

```python
from importlib import resources


def bundled_default() -> "Profile":
    """Return the `default` profile bundled with resona-postprocess."""
    ref = resources.files("resona_postprocess").joinpath("profiles/default.json")
    data = json.loads(ref.read_text(encoding="utf-8"))
    # base_dir=None: a 'source' of 'default_replacements.json' resolves via the
    # bundled-resource fallback in pipeline._load_rules.
    return Profile.from_dict(data, base_dir=None)


def resolve_profile(ref, profiles_dir: Path | str) -> "Profile":
    """Resolve a profile reference to a Profile.

    `ref` may be a Profile, a parsed dict, an inline JSON string, a filesystem
    path to a .json file, or a profile name resolved against `profiles_dir`.
    A file named `<name>.json` in `profiles_dir` shadows the bundled profile.
    """
    profiles_dir = Path(profiles_dir)
    if isinstance(ref, Profile):
        return ref
    if isinstance(ref, dict):
        return Profile.from_dict(ref)
    if not isinstance(ref, str):
        raise ProfileError(f"Cannot resolve profile from {type(ref).__name__}")

    text = ref.strip()
    if text.startswith("{"):
        try:
            return Profile.from_dict(json.loads(text))
        except json.JSONDecodeError as e:
            raise ProfileError(f"Invalid inline profile JSON: {e}") from e

    path = Path(ref)
    if path.suffix == ".json" and path.exists():
        return Profile.from_file(path)

    candidate = profiles_dir / f"{ref}.json"
    if candidate.exists():
        return Profile.from_file(candidate)
    if ref == "default":
        return bundled_default()
    raise ProfileError(f"Profile not found: {ref}")


def list_profiles(profiles_dir: Path | str) -> list[dict]:
    """List `{name, description}` for every profile file in `profiles_dir`."""
    profiles_dir = Path(profiles_dir)
    out: list[dict] = []
    if not profiles_dir.is_dir():
        return out
    for path in sorted(profiles_dir.glob("*.json")):
        try:
            p = Profile.from_file(path)
            out.append({"name": p.name, "description": p.description})
        except ProfileError:
            continue
    return out
```

- [ ] **Step 5: Confirm package data ships**

`packages/postprocess/pyproject.toml` uses `[tool.hatch.build.targets.wheel]`
with `packages = ["src/resona_postprocess"]`, which includes **all** files under
the package (including non-`.py` data like `default_replacements.json` and the
new `profiles/default.json`) automatically. No `pyproject.toml` change is
needed. Verify by running step 6 — `test_bundled_default_loads` exercises the
bundled path. If that test cannot find the file, only then add `profiles/*.json`
to a package-data glob.

- [ ] **Step 6: Run tests** — `uv run pytest packages/postprocess/tests/test_profile.py -v` → PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/postprocess/src/resona_postprocess/profile.py \
        packages/postprocess/src/resona_postprocess/profiles/default.json \
        packages/postprocess/tests/test_profile.py
git commit -m "feat(postprocess): profile resolution, listing, bundled default"
```

---

## Task 4: Harden `llm.py` and add `llm_transform`

**Files:**
- Modify: `packages/postprocess/src/resona_postprocess/llm.py`
- Test: `packages/postprocess/tests/test_llm.py`

- [ ] **Step 1: Write the failing test**

Create/replace `packages/postprocess/tests/test_llm.py`:

```python
import pytest
import resona_postprocess.llm as llm_mod
from resona_postprocess.llm import llm_transform, LLMUnavailableError


class _Msg:
    def __init__(self, content): self.message = type("M", (), {"content": content})


class _Resp:
    def __init__(self, content): self.choices = [_Msg(content)]
    usage = None


def test_llm_transform_calls_model(monkeypatch):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _Resp("FORMATTED")

    monkeypatch.setattr(llm_mod, "litellm", type("L", (), {"completion": staticmethod(fake_completion)}))
    out = llm_transform("raw", prompt="format", model="gpt-x", temperature=0.3)
    assert out == "FORMATTED"
    assert captured["model"] == "gpt-x"
    assert captured["temperature"] == 0.3
    assert captured["messages"][0]["content"] == "format"
    assert captured["messages"][1]["content"] == "raw"


def test_llm_transform_raises_when_litellm_missing(monkeypatch):
    monkeypatch.setattr(llm_mod, "litellm", None)
    with pytest.raises(LLMUnavailableError):
        llm_transform("raw", prompt="format")


def test_llm_transform_retries_once(monkeypatch):
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return _Resp("OK")

    monkeypatch.setattr(llm_mod, "litellm", type("L", (), {"completion": staticmethod(flaky)}))
    assert llm_transform("raw", prompt="p") == "OK"
    assert calls["n"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/postprocess/tests/test_llm.py -v`
Expected: FAIL — `llm_transform` / `LLMUnavailableError` not defined.

- [ ] **Step 3: Implement**

Replace `packages/postprocess/src/resona_postprocess/llm.py` body (keep the
`LITELLM_LOG` env-default block at the top, and the `try/import litellm` block):

```python
"""LLM-based postprocessing via litellm."""

import logging
import os

os.environ.setdefault("LITELLM_LOG", "ERROR")

try:
    import litellm
except ImportError:  # pragma: no cover
    litellm = None  # type: ignore[assignment]

from decouple import config

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 120.0


class LLMUnavailableError(RuntimeError):
    """Raised when an LLM step is requested but litellm is not installed."""


def _completion(*, model, api_base, messages, temperature, max_tokens,
                response_format=None):
    """Call litellm.completion with one retry on transient failure."""
    if litellm is None:
        raise LLMUnavailableError(
            "LLM postprocessing requires the 'litellm' package. "
            "Install it: pip install litellm"
        )
    kwargs = {
        "model": model,
        "api_base": api_base,
        "messages": messages,
        "timeout": _DEFAULT_TIMEOUT,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if response_format is not None:
        kwargs["response_format"] = response_format

    last_exc = None
    for attempt in (1, 2):
        try:
            resp = litellm.completion(**kwargs)
            usage = getattr(resp, "usage", None)
            log.info("LLM call model=%s attempt=%d usage=%s", model, attempt, usage)
            return resp.choices[0].message.content
        except LLMUnavailableError:
            raise
        except Exception as e:  # noqa: BLE001 — litellm raises many error types
            last_exc = e
            log.warning("LLM call failed (attempt %d): %s", attempt, e)
    raise last_exc


def llm_transform(
    text: str,
    *,
    prompt: str,
    model: str | None = None,
    api_base: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Send transcript text through an LLM and return the transformed text."""
    model = model or config("RESONA_LLM_MODEL", default="gpt-4o-mini")
    api_base = api_base or config("RESONA_LLM_API_BASE", default="") or None
    return _completion(
        model=model,
        api_base=api_base,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )


# Backwards-compatible alias for the original entry point.
def llm_postprocess(text: str, *, prompt: str, model: str | None = None,
                    api_base: str | None = None) -> str:
    """Deprecated alias for :func:`llm_transform`."""
    return llm_transform(text, prompt=prompt, model=model, api_base=api_base)
```

- [ ] **Step 4: Run tests** — `uv run pytest packages/postprocess/tests/test_llm.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/postprocess/src/resona_postprocess/llm.py packages/postprocess/tests/test_llm.py
git commit -m "feat(postprocess): harden llm.py, add llm_transform with retry"
```

---

## Task 5: Add `llm_extract` for structured JSON

**Files:**
- Modify: `packages/postprocess/src/resona_postprocess/llm.py`
- Test: `packages/postprocess/tests/test_llm.py`

- [ ] **Step 1: Write the failing test**

Append to `test_llm.py`:

```python
from resona_postprocess.llm import llm_extract


def test_llm_extract_parses_json(monkeypatch):
    monkeypatch.setattr(
        llm_mod, "litellm",
        type("L", (), {"completion": staticmethod(lambda **k: _Resp('{"diagnose": "x"}'))}),
    )
    assert llm_extract("raw", prompt="extract") == {"diagnose": "x"}


def test_llm_extract_malformed_json_keeps_raw(monkeypatch):
    monkeypatch.setattr(
        llm_mod, "litellm",
        type("L", (), {"completion": staticmethod(lambda **k: _Resp("not json"))}),
    )
    out = llm_extract("raw", prompt="extract")
    assert out == {"_raw": "not json"}
```

- [ ] **Step 2: Run test** — `uv run pytest packages/postprocess/tests/test_llm.py -v` → FAIL (`llm_extract` undefined).

- [ ] **Step 3: Implement**

Append to `llm.py`:

```python
import json as _json


def llm_extract(
    text: str,
    *,
    prompt: str,
    model: str | None = None,
    api_base: str | None = None,
    temperature: float | None = None,
) -> dict:
    """Extract structured data from text. Returns a parsed JSON object.

    On a malformed LLM response, returns ``{"_raw": <response string>}`` so a
    job never hard-fails on a bad extraction.
    """
    model = model or config("RESONA_LLM_MODEL", default="gpt-4o-mini")
    api_base = api_base or config("RESONA_LLM_API_BASE", default="") or None
    raw = _completion(
        model=model,
        api_base=api_base,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        temperature=temperature,
        max_tokens=None,
        response_format={"type": "json_object"},
    )
    try:
        parsed = _json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"_raw": raw}
```

Note: `json` is already imported at module top via `import json as _json`; the
`except` clause references `_json` — use `_json.JSONDecodeError` consistently.

- [ ] **Step 4: Run tests** — `uv run pytest packages/postprocess/tests/test_llm.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/postprocess/src/resona_postprocess/llm.py packages/postprocess/tests/test_llm.py
git commit -m "feat(postprocess): add llm_extract for structured JSON output"
```

---

## Task 6: Result-carrying pipeline and `build_pipeline`

**Files:**
- Modify: `packages/postprocess/src/resona_postprocess/pipeline.py`
- Delete: `packages/postprocess/src/resona_postprocess/sources.py`
- Test: `packages/postprocess/tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Replace `packages/postprocess/tests/test_pipeline.py`:

```python
import resona_postprocess.llm as llm_mod
from resona_postprocess.pipeline import (
    PostprocessPipeline, PostprocessResult, build_pipeline,
)
from resona_postprocess.profile import Profile


def test_pipeline_runs_text_steps_in_order():
    pipe = PostprocessPipeline()
    pipe.add_text("up", str.upper)
    pipe.add_text("excl", lambda t: t + "!")
    result = pipe.run("hi")
    assert isinstance(result, PostprocessResult)
    assert result.text == "HI!"
    assert result.data == {}


def test_pipeline_extract_step_populates_data():
    pipe = PostprocessPipeline()
    pipe.add_extract("fields", lambda t: {"len": len(t)})
    result = pipe.run("abcd")
    assert result.text == "abcd"
    assert result.data == {"fields": {"len": 4}}


def test_pipeline_failing_llm_step_is_skipped():
    pipe = PostprocessPipeline()

    def boom(_): raise RuntimeError("llm down")

    pipe.add_text("bad", boom)
    pipe.add_text("ok", str.upper)
    result = pipe.run("hi")
    assert result.text == "HI"  # bad step skipped, ok step still ran


def test_build_pipeline_replacements_and_extract(monkeypatch):
    monkeypatch.setattr(
        llm_mod, "litellm",
        type("L", (), {"completion": staticmethod(
            lambda **k: type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": '{"k": 1}'})})()],
                "usage": None})())}),
    )
    profile = Profile.from_dict({
        "name": "p",
        "steps": [
            {"type": "replacements", "rules": [{"pattern": r"\bx\b", "replacement": "y"}]},
            {"type": "extract", "name": "f", "prompt": "extract"},
        ],
    })
    result = build_pipeline(profile).run("x x")
    assert result.text == "y y"
    assert result.data == {"f": {"k": 1}}


def test_build_pipeline_replacements_from_bundled_source():
    profile = Profile.from_dict({
        "name": "p",
        "steps": [{"type": "replacements", "source": "default_replacements.json"}],
    })
    # 'Komma' is a default rule; pipeline must resolve the bundled source file.
    assert "," in build_pipeline(profile).run("a Komma b").text
```

- [ ] **Step 2: Run test** — `uv run pytest packages/postprocess/tests/test_pipeline.py -v` → FAIL.

- [ ] **Step 3: Implement**

Replace `packages/postprocess/src/resona_postprocess/pipeline.py`:

```python
"""Composable postprocessing pipeline producing text + structured data."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Callable

from .llm import llm_extract, llm_transform
from .profile import Profile
from .replacements import apply_replacements

log = logging.getLogger(__name__)

TextStep = Callable[[str], str]
ExtractStep = Callable[[str], dict]


@dataclass
class PostprocessResult:
    """Outcome of a pipeline run."""

    text: str
    data: dict = field(default_factory=dict)


class PostprocessPipeline:
    """Ordered chain of text transforms and structured-extraction steps."""

    def __init__(self) -> None:
        self._steps: list[tuple[str, str, Callable]] = []

    def add_text(self, name: str, step: TextStep) -> "PostprocessPipeline":
        self._steps.append((name, "text", step))
        return self

    def add_extract(self, name: str, step: ExtractStep) -> "PostprocessPipeline":
        self._steps.append((name, "extract", step))
        return self

    def run(self, text: str) -> PostprocessResult:
        """Run every step. A failing llm/extract step is logged and skipped."""
        result = PostprocessResult(text=text)
        for name, kind, step in self._steps:
            try:
                if kind == "text":
                    result.text = step(result.text)
                else:
                    result.data[name] = step(result.text)
            except Exception as e:  # noqa: BLE001
                log.warning("Postprocess step %r failed, skipping: %s", name, e)
                result.data.setdefault("_skipped", []).append(name)
        return result


def _load_rules(step: dict, base_dir: Path | None) -> list[dict]:
    """Resolve a replacements step's rules from inline `rules` or `source`."""
    if "rules" in step:
        return step["rules"]
    source = step.get("source", "")
    if base_dir is not None:
        candidate = Path(source)
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        if candidate.exists():
            return json.loads(candidate.read_text(encoding="utf-8"))
    # Fall back to a file bundled in resona-postprocess (e.g. default_replacements.json).
    ref = resources.files("resona_postprocess").joinpath(source)
    if ref.is_file():
        return json.loads(ref.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"replacements source not found: {source}")


def build_pipeline(profile: Profile) -> PostprocessPipeline:
    """Build a runnable pipeline from a Profile."""
    pipe = PostprocessPipeline()
    for idx, step in enumerate(profile.steps):
        stype = step["type"]
        name = step.get("name", stype)
        if stype == "replacements":
            rules = _load_rules(step, profile.base_dir)
            pipe.add_text(name, lambda t, r=rules: apply_replacements(t, r))
        elif stype == "llm":
            pipe.add_text(name, lambda t, s=step: llm_transform(
                t, prompt=s["prompt"], model=s.get("model"),
                api_base=s.get("api_base"), temperature=s.get("temperature"),
                max_tokens=s.get("max_tokens"),
            ))
        elif stype == "extract":
            pipe.add_extract(name, lambda t, s=step: llm_extract(
                t, prompt=s["prompt"], model=s.get("model"),
                api_base=s.get("api_base"), temperature=s.get("temperature"),
            ))
    return pipe
```

- [ ] **Step 4: Delete `sources.py` and its now-broken tests**

```bash
git rm packages/postprocess/src/resona_postprocess/sources.py
git rm packages/postprocess/tests/test_sources.py
git rm packages/postprocess/tests/test_mixed_pipeline.py
```

Both `test_sources.py` and `test_mixed_pipeline.py` import
`from resona_postprocess.sources import build_pipeline_from_config` and will fail
to collect once `sources.py` is gone — they must be removed in this task so the
suite stays green. The new `test_pipeline.py` (Step 1) already covers the
replacements + LLM + extract combination that `test_mixed_pipeline.py` tested.

Then grep for any other importers:
`grep -rn "build_pipeline_from_config\|resona_postprocess.sources\|llm_postprocess" --include=*.py packages apps`
The remaining hits are `apps/resona-cli/src/resona_cli/transcribe.py` and
`watch.py` — both fixed in Task 15. Note any unexpected hit.

- [ ] **Step 5: Run tests** — `uv run pytest packages/postprocess/tests/ -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/postprocess/
git commit -m "feat(postprocess): result-carrying pipeline and build_pipeline"
```

---

## Task 7: API DB model — drop config tables, extend `Job`

**Files:**
- Modify: `packages/api/src/resona_api/db/models.py`
- Modify: `packages/api/src/resona_api/db/engine.py`
- Modify: `packages/api/src/resona_api/db/utils.py`
- Delete: `packages/api/src/resona_api/db/presets.py`
- Modify: `packages/api/tests/test_db_utils.py`
- Test: `packages/api/tests/test_db.py` (create if absent)

- [ ] **Step 1: Write the failing test**

Add to `packages/api/tests/test_db.py`:

```python
from resona_api.db.models import Job


def test_job_has_profile_fields():
    job = Job(filename="f.wav")
    assert hasattr(job, "profile")
    assert hasattr(job, "profile_config")
    assert hasattr(job, "structured")


def test_replacement_table_removed():
    import resona_api.db.models as m
    assert not hasattr(m, "Replacement")
    assert not hasattr(m, "InitialPrompt")
```

- [ ] **Step 2: Run test** — `uv run pytest packages/api/tests/test_db.py -v` → FAIL.

- [ ] **Step 3: Implement model changes**

In `db/models.py`:
- Delete the `Replacement` and `InitialPrompt` classes entirely.
- Add three fields to `Job` (after the `engine` field):

```python
    profile: Optional[str] = Field(default=None)
    profile_config: Optional[str] = Field(default=None)
    structured: Optional[str] = Field(default=None)
```

Update the `Job` docstring to document the new fields.

In `db/engine.py`:
- Change `from .models import Job, Replacement, InitialPrompt` to
  `from .models import Job`.
- Delete the two `from .presets import ...` lines.
- Delete the `populate_default_replacements` and `populate_initial_prompts`
  functions entirely.
- After deleting those functions, `select` and `Session` are no longer used —
  change the SQLModel import to `from sqlmodel import SQLModel, create_engine`.
- Extend `create_db_and_tables()`'s idempotent-migration block to add the new
  `Job` columns to pre-existing databases:

```python
def create_db_and_tables():
    """Create all tables defined by SQLModel metadata."""
    log.info("Creating database tables...")
    SQLModel.metadata.create_all(engine)
    from sqlalchemy import text
    with engine.connect() as conn:
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(job)"))]
        for col in ("engine", "profile", "profile_config", "structured"):
            if col not in cols:
                conn.execute(text(f"ALTER TABLE job ADD COLUMN {col} VARCHAR"))
        conn.commit()
    log.info("Database tables created successfully.")
```

Delete the now-dead presets module — the migration helper (Task 8) reads from
the legacy DB tables, not from `presets.py`, so nothing references it anymore:

```bash
git rm packages/api/src/resona_api/db/presets.py
```

In `db/utils.py`:
- Delete `get_active_replacements()` and `get_active_initial_prompts_string()`.
- Remove `Replacement, InitialPrompt` from the model import.
- Change `register_job` to accept and persist `profile`:

```python
def register_job(filename: str, upload_name: str, keep: bool = True,
                 translate: bool = False, engine: str | None = None,
                 profile: str | None = None) -> dict:
    """Register a new transcription job in the database."""
    with Session(_engine) as session:
        job = Job(
            filename=filename, upload_name=upload_name, keepfile=keep,
            translate=translate, engine=engine, profile=profile,
            status=JobStatus.PENDING,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return {"id": job.id, "file": f"/files/{job.filename}",
                "result": f"/job/{job.id}"}
```

- [ ] **Step 4: Update `test_db_utils.py`**

`packages/api/tests/test_db_utils.py` has tests for the now-deleted
`get_active_replacements()` and `get_active_initial_prompts_string()` helpers.
Delete those test functions. If a test exercises `register_job`, leave it; add a
case asserting a passed `profile` is persisted on the returned/fetched job.

- [ ] **Step 5: Run tests** — `uv run pytest packages/api/tests/test_db.py packages/api/tests/test_db_utils.py -v` → PASS.
  (Other API tests will fail until later tasks — that is expected.)

- [ ] **Step 6: Commit**

```bash
git add packages/api/src/resona_api/db/ packages/api/tests/test_db.py packages/api/tests/test_db_utils.py
git commit -m "refactor(api): drop config tables, add profile fields to Job"
```

---

## Task 8: One-shot migration helper

**Files:**
- Create: `packages/api/src/resona_api/migration.py`
- Test: `packages/api/tests/test_migration.py`

- [ ] **Step 1: Write the failing test**

Create `packages/api/tests/test_migration.py`:

```python
import json
from sqlalchemy import create_engine, text
from resona_api.migration import migrate_config_tables_to_profile


def _legacy_db(path):
    eng = create_engine(f"sqlite:///{path}")
    with eng.connect() as c:
        c.execute(text("CREATE TABLE replacement (id INTEGER PRIMARY KEY, "
                        "name TEXT, replacement TEXT, active BOOLEAN)"))
        c.execute(text("CREATE TABLE initialprompt (id INTEGER PRIMARY KEY, "
                        "phrase TEXT, active BOOLEAN)"))
        c.execute(text("INSERT INTO replacement (name, replacement, active) "
                        "VALUES ('Komma', ',', 1)"))
        c.execute(text("INSERT INTO initialprompt (phrase, active) "
                        "VALUES ('Befund', 1)"))
        c.commit()
    return eng


def test_migration_exports_and_drops(tmp_path):
    db = tmp_path / "jobs.sqlite"
    eng = _legacy_db(db)
    profiles_dir = tmp_path / "profiles"

    migrate_config_tables_to_profile(eng, profiles_dir)

    written = json.loads((profiles_dir / "default.json").read_text())
    assert written["initial_prompt"] == ["Befund"]
    rules = written["steps"][0]["rules"]
    assert {"pattern": "Komma", "replacement": ","} in rules

    with eng.connect() as c:
        tables = [r[0] for r in c.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table'"))]
    assert "replacement" not in tables
    assert "initialprompt" not in tables


def test_migration_noop_when_tables_absent(tmp_path):
    db = tmp_path / "jobs.sqlite"
    eng = create_engine(f"sqlite:///{db}")
    with eng.connect() as c:
        c.execute(text("CREATE TABLE job (id INTEGER PRIMARY KEY)"))
        c.commit()
    profiles_dir = tmp_path / "profiles"
    migrate_config_tables_to_profile(eng, profiles_dir)  # must not raise
    assert not (profiles_dir / "default.json").exists()


def test_migration_skips_if_default_exists(tmp_path):
    db = tmp_path / "jobs.sqlite"
    eng = _legacy_db(db)
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "default.json").write_text('{"name": "default", "steps": []}')
    migrate_config_tables_to_profile(eng, profiles_dir)
    # existing default.json is preserved, tables still dropped
    assert json.loads((profiles_dir / "default.json").read_text())["steps"] == []
```

- [ ] **Step 2: Run test** — `uv run pytest packages/api/tests/test_migration.py -v` → FAIL.

- [ ] **Step 3: Implement**

Create `packages/api/src/resona_api/migration.py`:

```python
"""One-shot migration: export legacy config tables into a profile file."""

import json
import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"),
        {"n": name},
    ).first()
    return row is not None


def migrate_config_tables_to_profile(engine: Engine, profiles_dir: Path) -> None:
    """Export `replacement` + `initialprompt` rows to `<profiles_dir>/default.json`,
    then drop both tables. No-op when neither table exists.
    """
    profiles_dir = Path(profiles_dir)
    with engine.connect() as conn:
        has_repl = _table_exists(conn, "replacement")
        has_prompt = _table_exists(conn, "initialprompt")
        if not has_repl and not has_prompt:
            return

        rules, prompts = [], []
        if has_repl:
            for row in conn.execute(text(
                "SELECT name, replacement FROM replacement WHERE active=1")):
                rules.append({"pattern": row[0], "replacement": row[1]})
        if has_prompt:
            for row in conn.execute(text(
                "SELECT phrase FROM initialprompt WHERE active=1")):
                prompts.append(row[0])

        default_path = profiles_dir / "default.json"
        if not default_path.exists():
            profiles_dir.mkdir(parents=True, exist_ok=True)
            profile = {
                "name": "default",
                "description": "Migrated from legacy replacement/prompt tables.",
                "initial_prompt": prompts,
                "steps": [{"type": "replacements", "name": "migrated", "rules": rules}],
            }
            default_path.write_text(
                json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
            log.info("Migrated %d replacements + %d prompts to %s",
                     len(rules), len(prompts), default_path)
        else:
            log.info("%s already exists; keeping it, dropping legacy tables", default_path)

        if has_repl:
            conn.execute(text("DROP TABLE replacement"))
        if has_prompt:
            conn.execute(text("DROP TABLE initialprompt"))
        conn.commit()
```

- [ ] **Step 4: Run tests** — `uv run pytest packages/api/tests/test_migration.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/api/src/resona_api/migration.py packages/api/tests/test_migration.py
git commit -m "feat(api): one-shot migration of config tables to profile file"
```

---

## Task 9: Server profiles store and `/profiles` CRUD router

**Files:**
- Modify: `packages/api/src/resona_api/paths.py`
- Create: `packages/api/src/resona_api/profiles_store.py`
- Create: `packages/api/src/resona_api/profiles_routes.py`
- Test: `packages/api/tests/test_profiles_routes.py`

- [ ] **Step 1: Add `PROFILES_PATH`**

In `paths.py`, after the `DB_PATH` line:

```python
PROFILES_PATH: Path = _resolve("RESONA_PROFILES_DIR", "profiles", DATA_PATH)
```

- [ ] **Step 2: Write the failing test**

Create `packages/api/tests/test_profiles_routes.py`:

```python
import resona_api.profiles_store as store
from fastapi.testclient import TestClient


def _app(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PROFILES_PATH", tmp_path)
    from fastapi import FastAPI
    from resona_api.profiles_routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_put_get_list_delete_profile(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    body = {"name": "arzt", "description": "AB", "steps": []}

    assert client.put("/profiles/arzt", json=body).status_code == 200
    assert client.get("/profiles/arzt").json()["description"] == "AB"

    listing = client.get("/profiles").json()
    assert {"name": "arzt", "description": "AB"} in listing["profiles"]

    assert client.delete("/profiles/arzt").status_code == 200
    assert client.get("/profiles/arzt").status_code == 404


def test_put_invalid_profile_returns_400(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    resp = client.put("/profiles/bad", json={"name": "bad",
        "steps": [{"type": "magic"}]})
    assert resp.status_code == 400
```

Note: the API uses `verify_api_key` on routers. The test app above includes the
router directly without auth wiring; `verify_api_key` returns the key (or "" when
`RESONA_API_KEY` is unset) so unauthenticated calls pass in tests, matching the
existing `test_*` suites.

- [ ] **Step 3: Run test** — `uv run pytest packages/api/tests/test_profiles_routes.py -v` → FAIL.

- [ ] **Step 4: Implement the store**

Create `packages/api/src/resona_api/profiles_store.py`:

```python
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
```

- [ ] **Step 5: Implement the router**

Create `packages/api/src/resona_api/profiles_routes.py`:

```python
"""REST CRUD for postprocessing profile files."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from resona_postprocess.profile import ProfileError

from . import profiles_store as store
from .auth import verify_api_key

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/profiles", summary="List profiles", tags=["Config"])
def list_profiles_route(api_key: str = Depends(verify_api_key)):
    """List every stored profile (name + description)."""
    return {"profiles": store.list_all()}


@router.get("/profiles/{name}", summary="Get a profile", tags=["Config"])
def get_profile_route(name: str, api_key: str = Depends(verify_api_key)):
    """Return one profile's JSON."""
    try:
        data = store.read(name)
    except store.ProfileNameError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if data is None:
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
    return data


@router.put("/profiles/{name}", summary="Create or replace a profile", tags=["Config"])
def put_profile_route(name: str, body: dict, api_key: str = Depends(verify_api_key)):
    """Validate and store a profile file."""
    try:
        return store.write(name, body)
    except store.ProfileNameError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ProfileError as e:
        raise HTTPException(status_code=400, detail=f"Invalid profile: {e}")


@router.delete("/profiles/{name}", summary="Delete a profile", tags=["Config"])
def delete_profile_route(name: str, api_key: str = Depends(verify_api_key)):
    """Delete a profile file by name."""
    try:
        deleted = store.delete(name)
    except store.ProfileNameError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
    return {"ok": True}
```

- [ ] **Step 6: Run tests** — `uv run pytest packages/api/tests/test_profiles_routes.py -v` → PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/api/src/resona_api/paths.py \
        packages/api/src/resona_api/profiles_store.py \
        packages/api/src/resona_api/profiles_routes.py \
        packages/api/tests/test_profiles_routes.py
git commit -m "feat(api): profile file store and /profiles CRUD router"
```

---

## Task 10: `POST /jobs` profile field; remove config routes; wire app

**Files:**
- Modify: `packages/api/src/resona_api/endpoints.py`
- Modify: `packages/api/src/resona_api/app.py`
- Modify: `packages/api/tests/test_endpoints.py`

- [ ] **Step 1: Write the failing test**

Add to `packages/api/tests/test_endpoints.py` (this is the existing module that
covers `/jobs`, `/replacements/`, and `/prompts/`):

```python
def test_submit_job_accepts_profile(client, tmp_audio):
    resp = client.post(
        "/jobs",
        files={"audio_files": ("a.wav", tmp_audio, "audio/wav")},
        data={"profile": "arztbrief"},
    )
    assert resp.status_code == 200
    job = resp.json()[0]
    # fetch the job and confirm the profile was persisted
    fetched = client.get(f"/job/{job['id']}").json()
    assert fetched["profile"] == "arztbrief"
```

Reuse whatever `client` / audio fixtures the existing API tests use; mirror their
setup (they mock the engine and use a temp DB).

- [ ] **Step 2: Run test** — FAIL (`profile` not accepted/persisted).

- [ ] **Step 3: Implement**

In `endpoints.py`:
- `submit_jobs`: add `profile: Optional[str] = Form(default=None)` and pass it to
  `register_job(..., profile=profile)`.
- Delete the entire **Replacement CRUD** block (`ReplacementCreate`,
  `list_replacements`, `add_replacement`, `delete_replacement`) and the entire
  **Prompt CRUD** block (`PromptCreate`, `list_prompts`, `add_prompt`,
  `activate_prompt`, `deactivate_prompt`, `delete_prompt`).
- Remove now-unused imports (`Replacement`, `InitialPrompt`, `BaseModel` if no
  longer referenced).

In `test_endpoints.py`:
- Delete every test that hits `/replacements/` or `/prompts/` — those routes no
  longer exist and the tests would fail. Keep the job-related tests.

In `app.py`:
- Change the import to `from .db.engine import create_db_and_tables` (drop the
  `populate_*` names).
- In `lifespan`, replace the `populate_*()` calls with the migration call:

```python
    log.info("Initializing database...")
    create_db_and_tables()
    from .db.engine import engine as _db_engine
    from .migration import migrate_config_tables_to_profile
    from .paths import PROFILES_PATH
    migrate_config_tables_to_profile(_db_engine, PROFILES_PATH)
```

- Register the profiles router next to the others:

```python
from .profiles_routes import router as profiles_router
app.include_router(profiles_router)
```

- [ ] **Step 4: Run tests** — `uv run pytest packages/api/tests/test_endpoints.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/api/src/resona_api/endpoints.py packages/api/src/resona_api/app.py packages/api/tests/test_endpoints.py
git commit -m "feat(api): job profile field, remove config routes, wire migration"
```

---

## Task 11: `tasks_transcribe.py` runs the profile pipeline

**Files:**
- Modify: `packages/api/src/resona_api/tasks_transcribe.py`
- Modify: `packages/api/tests/test_tasks.py` (existing worker tests)

- [ ] **Step 1: Update the existing worker tests**

`packages/api/tests/test_tasks.py` already tests `TranscribeTask._process_next_job`
against the **old** code path — it relies on `get_active_initial_prompts_string`,
the inline `apply_replacements` block, and DB replacements. Rewriting the worker
(Step 3) breaks those tests. In this step, update `test_tasks.py`:

- Remove or rewrite any test asserting DB-replacement behavior or referencing the
  removed helpers.
- Add a new test for the profile path. Mirror the engine-mocking approach the
  file already uses (`reg.run_stt` / `EngineClient`). Register a job with an
  inline profile that has a `replacements` step and an `extract` step, mock the
  engine to return fixed text, mock `resona_postprocess.llm.litellm`, run one
  `_process_next_job()` iteration, then assert:

```python
def test_job_runs_profile_pipeline(...):
    # inline profile submitted as the job's `profile` value
    inline = json.dumps({
        "name": "t", "initial_prompt": ["Befund"],
        "steps": [
            {"type": "replacements", "rules": [{"pattern": "Komma", "replacement": ","}]},
            {"type": "extract", "name": "f", "prompt": "extract"},
        ],
    })
    job = register_job("a.wav", "a.wav", profile=inline)
    # ... run TranscribeTask._process_next_job() once ...
    fetched = get_job(job["id"])
    assert "," in fetched.md
    assert json.loads(fetched.structured)["f"]   # extract output present
    assert fetched.profile_config               # snapshot stored
```

- [ ] **Step 2: Run test** — FAIL.

- [ ] **Step 3: Implement**

Rewrite the postprocessing portion of `_process_next_job` in `tasks_transcribe.py`.
Replace the imports and the block from `initial_prompt = ...` through the `job.md`
assignment:

```python
import json

from resona_postprocess.pipeline import build_pipeline
from resona_postprocess.profile import resolve_profile, ProfileError

from .paths import FILE_PATH, MD_PATH, PROFILES_PATH
```

Inside the `try:` body:

```python
                try:
                    profile = resolve_profile(job.profile or "default", PROFILES_PATH)
                except ProfileError as e:
                    log.warning("Job %s: profile %r invalid (%s); using default",
                                job.id, job.profile, e)
                    profile = resolve_profile("default", PROFILES_PATH)

                job.profile_config = json.dumps(profile.to_dict(), ensure_ascii=False)

                info = reg.resolve(job.engine or None, "stt", private=False)
                asr_result = reg.run_stt(
                    info, filepath, language="de",
                    prompt=profile.initial_prompt_string(),
                    task="translate" if job.translate else "transcribe",
                )
                update_job_attributes_from_result(job, asr_result)

                result = build_pipeline(profile).run(job.transcript)
                job.md = result.text
                job.structured = (
                    json.dumps(result.data, ensure_ascii=False) if result.data else None
                )
```

Then extend the MD-writing block to also write a `.json` sidecar when
`job.structured` is set. Inspect `formatting.write_md_file` first to match its
filename convention; write the sidecar next to the `.md` file:

```python
                try:
                    write_md_file(job.id, job.filename, job.md, job.keepfile)
                    if job.structured:
                        sidecar = MD_PATH / f"{Path(job.filename).stem}.json"
                        sidecar.write_text(job.structured, encoding="utf-8")
                    log.info(f"Job {job.id}: wrote MD file")
                except Exception as e_md:
                    log.error(f"Job {job.id}: failed to write MD file: {e_md}")
```

Add `from pathlib import Path` if not already imported. Remove the old
`get_active_initial_prompts_string` / `get_active_replacements` /
`PostprocessPipeline` / `apply_replacements` imports.

- [ ] **Step 4: Run tests** — `uv run pytest packages/api/tests/test_tasks.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/api/src/resona_api/tasks_transcribe.py packages/api/tests/test_tasks.py
git commit -m "feat(api): run profile pipeline in the transcribe worker"
```

---

## Task 12: `audio_routes.py` full profile support

**Files:**
- Modify: `packages/api/src/resona_api/audio_routes.py`
- Test: `packages/api/tests/test_audio_routes.py`

- [ ] **Step 1: Write the failing test**

Add to `test_audio_routes.py` (mirror its existing engine-mock fixtures):

```python
def test_transcription_applies_profile(client, tmp_audio, monkeypatch):
    # engine mocked to return "a Komma b"; inline profile replaces Komma -> ,
    inline = '{"name":"t","steps":[{"type":"replacements",' \
             '"rules":[{"pattern":"Komma","replacement":","}]}]}'
    resp = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", tmp_audio, "audio/wav")},
        data={"profile": inline},
    )
    assert resp.status_code == 200
    assert "," in resp.json()["text"]
```

- [ ] **Step 2: Run test** — FAIL.

- [ ] **Step 3: Implement**

In `audio_routes.py`:
- Replace the imports `from .db.utils import get_active_replacements` and
  `from resona_postprocess.replacements import apply_replacements` with:

```python
from resona_postprocess.pipeline import build_pipeline
from resona_postprocess.profile import resolve_profile, ProfileError
from .paths import PROFILES_PATH
```

- `create_transcription`: add `profile: str | None = Form(default=None)`.
- Replace the `replacements = get_active_replacements()` block with:

```python
    try:
        prof = resolve_profile(profile or "default", PROFILES_PATH)
    except ProfileError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid profile: {exc}")
    pp = build_pipeline(prof).run(result.get("text", ""))
    text = pp.text
```

- For `response_format == "verbose_json"` and the default JSON branch, include
  `"structured": pp.data` in the returned payload when `pp.data` is non-empty.

The `prompt` Form field already exists; leave it (callers may still pass an
explicit initial prompt). The profile's `initial_prompt` is not threaded into the
sync STT call in this task — `prompt` stays caller-controlled, consistent with
the OpenAI-compatible contract.

- [ ] **Step 4: Run tests** — `uv run pytest packages/api/tests/test_audio_routes.py -v` → PASS. Then run the whole API suite: `uv run pytest packages/api/tests/ -v`.

- [ ] **Step 5: Commit**

```bash
git add packages/api/src/resona_api/audio_routes.py packages/api/tests/test_audio_routes.py
git commit -m "feat(api): full profile support on /v1/audio/transcriptions"
```

---

## Task 13: `resona-client` profile methods

**Files:**
- Modify: `packages/client/src/resona_client/client.py`
- Modify: `packages/client/src/resona_client/config.py`
- Test: `packages/client/tests/test_client.py`

- [ ] **Step 1: Write the failing test**

Add to `test_client.py` (uses `respx`):

```python
import respx
from httpx import Response
from resona_client.client import ResonaClient


@respx.mock
def test_list_profiles():
    respx.get("http://t/profiles").mock(return_value=Response(
        200, json={"profiles": [{"name": "a", "description": "AA"}]}))
    c = ResonaClient(base_url="http://t")
    assert c.list_profiles() == [{"name": "a", "description": "AA"}]


@respx.mock
def test_put_profile():
    route = respx.put("http://t/profiles/a").mock(return_value=Response(200, json={}))
    ResonaClient(base_url="http://t").put_profile("a", {"name": "a", "steps": []})
    assert route.called


@respx.mock
def test_submit_job_sends_profile(tmp_path):
    f = tmp_path / "a.wav"; f.write_bytes(b"x")
    route = respx.post("http://t/jobs").mock(return_value=Response(200, json=[{"id": 1}]))
    ResonaClient(base_url="http://t").submit_job(f, profile="arzt")
    assert b"arzt" in route.calls[0].request.content
```

- [ ] **Step 2: Run test** — FAIL.

- [ ] **Step 3: Implement**

In `client.py`:
- `submit_job`: add `profile: Optional[str] = None`; when set, add
  `data["profile"] = profile`.
- `create_transcription`: add `profile: Optional[str] = None`; when set, add
  `data["profile"] = profile`.
- Delete `list_replacements`, `add_replacement`, `delete_replacement`,
  `list_prompts`, `add_prompt`, `activate_prompt`, `deactivate_prompt`,
  `remove_prompt`.
- Add a profile-CRUD section:

```python
    # ── Profile CRUD ──────────────────────────────────────────────────

    def list_profiles(self) -> list[dict]:
        """List stored profiles. GET /profiles"""
        resp = self._client.get(f"{self.base_url}/profiles")
        resp.raise_for_status()
        return resp.json()["profiles"]

    def get_profile(self, name: str) -> dict:
        """Fetch one profile. GET /profiles/{name}"""
        resp = self._client.get(f"{self.base_url}/profiles/{name}")
        resp.raise_for_status()
        return resp.json()

    def put_profile(self, name: str, profile: dict) -> dict:
        """Create or replace a profile. PUT /profiles/{name}"""
        resp = self._client.put(f"{self.base_url}/profiles/{name}", json=profile)
        resp.raise_for_status()
        return resp.json()

    def delete_profile(self, name: str) -> None:
        """Delete a profile. DELETE /profiles/{name}"""
        resp = self._client.delete(f"{self.base_url}/profiles/{name}")
        resp.raise_for_status()
```

In `config.py`: add an optional `default_profile: Optional[str] = None` field to
the `EngineConfig` dataclass/model and ensure it round-trips through
`load()`/save (match the existing pattern for `default_engine`).

- [ ] **Step 4: Run tests** — `uv run pytest packages/client/tests/ -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/client/ && git commit -m "feat(client): profile CRUD, drop replacement/prompt methods"
```

---

## Task 14: `resona profiles` CLI subcommand

**Files:**
- Create: `apps/resona-cli/src/resona_cli/profiles.py`
- Delete: `apps/resona-cli/src/resona_cli/replacements.py`, `apps/resona-cli/src/resona_cli/prompts.py`
- Modify: `apps/resona-cli/src/resona_cli/main.py`
- Test: `apps/resona-cli/tests/test_profiles_cli.py`

- [ ] **Step 1: Write the failing test**

Create `apps/resona-cli/tests/test_profiles_cli.py` using typer's `CliRunner`,
mocking `ResonaClient.from_config`. Mirror the patching style of the existing
`apps/resona-cli/tests/` suite:

```python
from typer.testing import CliRunner
from resona_cli.main import app


def test_profiles_list(monkeypatch):
    class FakeClient:
        def list_profiles(self): return [{"name": "a", "description": "AA"}]
    monkeypatch.setattr("resona_cli.profiles.ResonaClient.from_config",
                        classmethod(lambda cls, **k: FakeClient()))
    result = CliRunner().invoke(app, ["profiles", "list"])
    assert result.exit_code == 0
    assert "a" in result.stdout


def test_profiles_push(tmp_path, monkeypatch):
    f = tmp_path / "arzt.json"
    f.write_text('{"name": "arzt", "steps": []}')
    pushed = {}

    class FakeClient:
        def put_profile(self, name, profile): pushed["name"] = name
    monkeypatch.setattr("resona_cli.profiles.ResonaClient.from_config",
                        classmethod(lambda cls, **k: FakeClient()))
    result = CliRunner().invoke(app, ["profiles", "push", "arzt", str(f)])
    assert result.exit_code == 0
    assert pushed["name"] == "arzt"
```

- [ ] **Step 2: Run test** — FAIL.

- [ ] **Step 3: Implement**

Create `apps/resona-cli/src/resona_cli/profiles.py`:

```python
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
```

In `main.py`:
- Remove `from .replacements import replacements_app` and
  `from .prompts import prompts_app`; add `from .profiles import profiles_app`.
- Remove both `app.add_typer(... replacements ...)` and `... prompts ...` lines;
  add `app.add_typer(profiles_app, name="profiles", help="Manage postprocessing profiles.")`.

Delete the files:

```bash
git rm apps/resona-cli/src/resona_cli/replacements.py apps/resona-cli/src/resona_cli/prompts.py
```

If `apps/resona-cli/tests/` has `test_replacements*.py` / `test_prompts*.py`,
delete them too.

- [ ] **Step 4: Run tests** — `uv run pytest apps/resona-cli/tests/test_profiles_cli.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/resona-cli/ && git commit -m "feat(cli): resona profiles subcommand, drop replacements/prompts"
```

---

## Task 15: `transcribe` & `watch` profile support for both CLI paths

**Files:**
- Modify: `apps/resona-cli/src/resona_cli/transcribe.py`
- Modify: `apps/resona-cli/src/resona_cli/watch.py`
- Test: `apps/resona-cli/tests/test_transcribe.py`
- Test: `apps/resona-cli/tests/test_watch.py`

**Why `watch.py` is here:** `watch.py`'s `_watch_local_fallback` also imports
`build_pipeline_from_config` (deleted in Task 6) and calls `pipeline.run()`
expecting a `str`. After Task 6 it would `ModuleNotFoundError` at runtime. It is
fixed in this task alongside `transcribe.py`.

- [ ] **Step 1: Write the failing test**

Add to `test_transcribe.py` (match the existing mocking style):

```python
def test_transcribe_forwards_profile_name_to_gateway(monkeypatch, tmp_path):
    """A bare --profile name is forwarded to create_transcription."""
    audio = tmp_path / "a.wav"; audio.write_bytes(b"x")
    captured = {}

    class FakeClient:
        def create_transcription(self, fp, **kw):
            captured.update(kw)
            return {"text": "ok"}
    monkeypatch.setattr("resona_cli.transcribe.ResonaClient.from_config",
                        classmethod(lambda cls, **k: FakeClient()))
    from resona_cli.transcribe import _transcribe_via_gateway
    _transcribe_via_gateway(FakeClient(), [audio], tmp_path, None, "de",
                            None, False, "arztbrief")
    assert captured["profile"] == "arztbrief"


def test_local_fallback_loads_profile_file(monkeypatch, tmp_path):
    """A --profile path resolves to a Profile and drives the local pipeline."""
    prof = tmp_path / "p.json"
    prof.write_text('{"name":"p","steps":[{"type":"replacements",'
                     '"rules":[{"pattern":"Komma","replacement":","}]}]}')
    # ... assert _transcribe_local_fallback builds the pipeline from `prof`
```

Adapt signatures to whatever the final helper functions expose; the key
assertions are: gateway path passes `profile` through to the client, and the
local fallback resolves a `--profile` path/name into a `Profile` and runs
`build_pipeline`.

- [ ] **Step 2: Run test** — FAIL.

- [ ] **Step 3: Implement**

In `transcribe.py`:
- Add a `--profile` option to `transcribe_files`:

```python
    profile: Optional[str] = typer.Option(None, "--profile",
        help="Profile name or path to a profile JSON file."),
```

- Thread `profile` into both `_transcribe_via_gateway` and
  `_transcribe_local_fallback` (add a parameter to each).
- `_transcribe_via_gateway`: when `profile` is a path to an existing `.json`
  file, read it and forward the JSON string as `profile`; otherwise forward the
  name. Pass via `kwargs["profile"]` to `client.create_transcription`.
- `_transcribe_local_fallback`: replace
  `from resona_postprocess.sources import build_pipeline_from_config` and the
  `build_pipeline_from_config()` call with:

```python
from resona_postprocess.profile import resolve_profile, ProfileError
from resona_postprocess.pipeline import build_pipeline

_PROFILES_DIR = Path.home() / ".resona" / "profiles"

# inside _transcribe_local_fallback, before the file loop:
    ref = profile or cfg.default_profile or "default"
    try:
        prof = resolve_profile(ref, _PROFILES_DIR)
    except ProfileError as e:
        typer.echo(f"Profile '{ref}' not usable ({e}); using default.", err=True)
        prof = resolve_profile("default", _PROFILES_DIR)
    pipeline = build_pipeline(prof)
```

`cfg` (an `EngineConfig`) is already loaded in `transcribe_files`; pass its
`default_profile` down or re-load it in the fallback. After
`result = pipeline.run(raw_text)`, write `result.text` to the `.txt`/`.md` output
and, when `result.data` is non-empty, write a `<stem>.json` sidecar next to it.

- [ ] **Step 4: Fix `watch.py`**

Apply the same change to `watch.py`'s `_watch_local_fallback`:
- Add a `--profile` option to `watch_directory` (same `typer.Option` as above)
  and thread it into `_watch_local_fallback` as a parameter.
- Replace `from resona_postprocess.sources import build_pipeline_from_config`
  and `pipeline = build_pipeline_from_config()` with the same
  `resolve_profile` + `build_pipeline` block used in `transcribe.py` (resolving
  `profile or EngineConfig.load().default_profile or "default"` against
  `~/.resona/profiles/`).
- `pipeline.run()` now returns a `PostprocessResult` — change line ~97
  `out_path.write_text(transcript, ...)` to use `result.text`, and write a
  `<stem>.json` sidecar when `result.data` is non-empty.
- The gateway path of `watch` only calls `client.submit_job(f)` — pass
  `profile=profile` to that call too so watched uploads honour the profile.

- [ ] **Step 5: Run tests** — `uv run pytest apps/resona-cli/tests/ -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/resona-cli/ && git commit -m "feat(cli): transcribe & watch --profile for gateway and local fallback"
```

---

## Task 16: Full test sweep and documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/configuration/environment.md`
- Modify: postprocessing docs page(s) under `docs/`
- Modify: `docs/api-reference/` or changelog as applicable

- [ ] **Step 1: Full test sweep**

Run: `uv run pytest`
Expected: all packages PASS. Fix any cross-package fallout — a lingering import
of `resona_postprocess.sources`, `db.presets`, `Replacement`/`InitialPrompt`, or
a removed client method. Pay attention to `packages/api/tests/test_job_lifecycle.py`
(end-to-end job test): if it asserts replacement behavior, update it to register
a job with a profile and assert the profile pipeline ran.

This grep should return nothing in non-test source:
`grep -rn "build_pipeline_from_config\|get_active_replacements\|get_active_initial_prompts\|InitialPrompt\|Replacement\|db.presets\|add_replacement\|llm_postprocess" --include=*.py packages apps`
(`llm_postprocess` may still appear as the deprecated alias definition in
`llm.py` itself — that single hit is expected.)

- [ ] **Step 2: Update `CLAUDE.md`**

- `resona-postprocess` section: replace the `sources.py` bullet; document
  `profile.py`, the result-carrying `pipeline.py`, and `llm_transform`/`llm_extract`.
- `resona-api` section: remove `Replacement`/`InitialPrompt` from `db/models.py`;
  document `Job.profile/profile_config/structured`, `profiles_routes.py`,
  `profiles_store.py`, `migration.py`.
- `resona-cli` section: replace `replacements.py`/`prompts.py` with `profiles.py`;
  document `transcribe --profile`.
- Update the "Job flow" and "Postprocessing" sections to describe profiles.
- "How to add a new endpoint" / env var notes: add `RESONA_PROFILES_DIR`,
  `RESONA_LLM_MODEL`, `RESONA_LLM_API_BASE`.

- [ ] **Step 3: Update `docs/configuration/environment.md`**

Document `RESONA_PROFILES_DIR` (default `<DATA_PATH>/profiles` server-side,
`~/.resona/profiles/` for the CLI), `RESONA_LLM_MODEL`, `RESONA_LLM_API_BASE`.

- [ ] **Step 4: Update postprocessing docs**

Document the profile file format, the three step types, the bundled `default`
profile, named vs. inline submission, the `resona profiles` commands, and the
removal of the `/replacements/` `/prompts/` routes and `resona replacements` /
`resona prompts` commands (breaking change).

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/
git commit -m "docs: postprocessing profiles, remove replacement/prompt config"
```

---

## Done

All 16 tasks complete: profiles drive postprocessing through the async `/jobs`
worker, the sync `/v1/audio/transcriptions` route, and both CLI paths; config is
flat-file based; the legacy `Replacement`/`InitialPrompt` tables and their
REST/CLI surfaces are gone. Final check: `uv run pytest` is green.

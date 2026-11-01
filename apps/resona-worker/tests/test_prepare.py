"""``prepare``: weights verified offline, synthetic runs checked, tested tuples recorded."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from resona_job_core import Candidate, ExecutionProfile, OrganizationSettings

from resona_worker import prepare as prepare_module
from resona_worker.config import load_capabilities, load_manifest
from resona_worker.prepare import PrepareError, prepare, write_manifest

AUDIO = Candidate(engine="mlx-whisper", model="mlx-community/whisper-large-v3-mlx", accelerator="metal")
DOC = Candidate(engine="docling", model="docling-standard", accelerator="metal", ocr_engine="ocrmac")
SNAPSHOT_HASH = "49e6aa286ad60c14352c404340ded53710378a11"


def _settings(audio: list[Candidate], document: list[Candidate]) -> OrganizationSettings:
    return OrganizationSettings(execution_profiles={"mac-private": ExecutionProfile(audio=audio, document=document)})


@pytest.fixture
def cached_weights(tmp_path: Path, monkeypatch) -> list[tuple[str, str | None]]:
    """Pretend every hub repo is cached at ``SNAPSHOT_HASH``; records lookups."""
    lookups: list[tuple[str, str | None]] = []
    snapshot = tmp_path / "hub" / "snapshots" / SNAPSHOT_HASH
    snapshot.mkdir(parents=True)

    def fake_snapshot(repo: str, revision: str | None) -> Path | None:
        lookups.append((repo, revision))
        return None if "missing" in repo else snapshot

    monkeypatch.setattr(prepare_module, "local_snapshot", fake_snapshot)
    return lookups


def test_prepare_records_tested_tuples_with_revision_memory_and_stage_devices(
    tmp_path: Path, prepare_executor: Path, cached_weights
) -> None:
    manifest = prepare(
        _settings([AUDIO], [DOC]), "mac-private", executor=(sys.executable, str(prepare_executor))
    )
    out = tmp_path / "caps.json"
    write_manifest(manifest, out)

    assert load_capabilities(out) == (AUDIO, DOC)
    audio, document = load_manifest(out).tested
    assert audio.kind == "audio"
    assert [(w.repo, w.revision) for w in audio.weights] == [(AUDIO.model, SNAPSHOT_HASH)]
    assert audio.stage_accelerators == {"asr:mlx-whisper": "metal"}
    assert audio.peak_rss_bytes > 0
    assert audio.peak_accelerator_bytes == 123456
    assert audio.seconds > 0
    assert document.stage_accelerators == {"layout:docling-layout": "metal", "ocr:ocrmac": "metal"}
    assert cached_weights == [(AUDIO.model, None)]


def test_pinned_revision_missing_locally_fails_before_any_synthetic_run(
    tmp_path: Path, prepare_executor: Path, cached_weights
) -> None:
    pinned = Candidate(engine="mlx-whisper", model="mlx-community/missing-model", model_revision="abc123", accelerator="metal")

    with pytest.raises(PrepareError, match="abc123"):
        prepare(_settings([pinned], []), "mac-private", executor=(sys.executable, "-c", "raise SystemExit(99)"))

    assert cached_weights == [("mlx-community/missing-model", "abc123")]


def test_release_pin_must_match_the_cached_revision_before_any_synthetic_run(cached_weights) -> None:
    bogus = "0" * 40

    with pytest.raises(PrepareError, match=f"release pins {AUDIO.model} at {bogus}"):
        prepare(
            _settings([AUDIO], []), "mac-private",
            executor=(sys.executable, "-c", "raise SystemExit(99)"), model_pins={AUDIO.model: bogus},
        )

    assert cached_weights == [(AUDIO.model, bogus)]


@pytest.mark.parametrize(
    ("candidate", "pins", "reason"),
    [
        (AUDIO, {}, "does not pin a revision"),
        (AUDIO.model_copy(update={"model_revision": "1" * 40}), {AUDIO.model: SNAPSHOT_HASH}, "the release pins"),
    ],
)
def test_release_without_or_contradicting_a_pin_fails(cached_weights, candidate, pins, reason) -> None:
    with pytest.raises(PrepareError, match=reason):
        prepare(_settings([candidate], []), "mac-private",
                executor=(sys.executable, "-c", "raise SystemExit(99)"), model_pins=pins)


def test_release_pin_matching_the_cache_prepares(prepare_executor: Path, cached_weights) -> None:
    manifest = prepare(
        _settings([AUDIO], []), "mac-private",
        executor=(sys.executable, str(prepare_executor)), model_pins={AUDIO.model: SNAPSHOT_HASH},
    )

    assert [(w.repo, w.revision) for w in manifest.tested[0].weights] == [(AUDIO.model, SNAPSHOT_HASH)]


@pytest.mark.parametrize(
    ("model", "reason"),
    [
        ("org/broken-model", "engine_error"),
        ("org/garbage-model", "expected content"),
        ("org/wrongdevice-model", "did not run asr on metal"),
    ],
)
def test_failing_candidate_aborts_prepare(prepare_executor: Path, cached_weights, model: str, reason: str) -> None:
    bad = Candidate(engine="mlx-whisper", model=model, accelerator="metal")

    with pytest.raises(PrepareError, match=reason):
        prepare(_settings([AUDIO, bad], []), "mac-private", executor=(sys.executable, str(prepare_executor)))


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(PrepareError, match="not defined"):
        prepare(_settings([AUDIO], []), "linux-cuda")


def test_synthetic_runs_hang_are_killed_after_the_timeout(prepare_executor: Path, cached_weights, tmp_path: Path) -> None:
    hang = tmp_path / "hang.py"
    hang.write_text("import time; time.sleep(60)\n", encoding="utf-8")

    with pytest.raises(PrepareError, match="exit -9"):
        prepare(_settings([AUDIO], []), "mac-private", executor=(sys.executable, str(hang)), timeout=0.5)


CUDA_AUDIO = Candidate(engine="whisper", model="large-v3", accelerator="cuda")
CUDA_DOC = Candidate(engine="docling", model="docling-standard", accelerator="cuda", ocr_engine="rapidocr")


def _cuda_settings(audio: list[Candidate], document: list[Candidate]) -> OrganizationSettings:
    return OrganizationSettings(execution_profiles={"linux-cuda": ExecutionProfile(audio=audio, document=document)})


def test_whisper_checkpoint_must_hash_to_the_published_revision(
    tmp_path: Path, prepare_executor: Path, monkeypatch
) -> None:
    import hashlib

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    checkpoint = tmp_path / "cache" / "whisper" / "large-v3.pt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"tampered weights")
    executor = (sys.executable, str(prepare_executor))

    with pytest.raises(PrepareError, match="does not match its published SHA-256"):
        prepare(_cuda_settings([CUDA_AUDIO], []), "linux-cuda", executor=executor)

    published = hashlib.sha256(b"tampered weights").hexdigest()
    monkeypatch.setattr(prepare_module, "whisper_revision", lambda model: published)
    manifest = prepare(_cuda_settings([CUDA_AUDIO], []), "linux-cuda", executor=executor)
    assert [(w.repo, w.revision) for w in manifest.tested[0].weights] == [("large-v3", published)]


def test_release_pin_other_than_the_installed_whisper_checkpoint_fails(tmp_path: Path, prepare_executor: Path) -> None:
    with pytest.raises(PrepareError, match="pinned revision of whisper large-v3"):
        prepare(
            _cuda_settings([CUDA_AUDIO], []),
            "linux-cuda",
            executor=(sys.executable, str(prepare_executor)),
            model_pins={"large-v3": "0" * 64},
        )


def test_missing_whisper_checkpoint_names_the_download_flag(tmp_path: Path, prepare_executor: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "empty"))

    with pytest.raises(PrepareError, match="--download"):
        prepare(_cuda_settings([CUDA_AUDIO], []), "linux-cuda", executor=(sys.executable, str(prepare_executor)))


def test_download_fetches_candidate_and_docling_stage_weights_before_any_run(
    tmp_path: Path, prepare_executor: Path, monkeypatch
) -> None:
    fetched: list[tuple[str, str | None]] = []
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    def fake_whisper_download(model: str) -> Path:
        fetched.append(("whisper", model))
        raise prepare_module.WeightsError("download of whisper large-v3 failed: URLError")

    monkeypatch.setattr(prepare_module, "download_whisper_checkpoint", fake_whisper_download)
    monkeypatch.setattr(prepare_module, "download_snapshot", lambda repo, revision: fetched.append((repo, revision)))

    with pytest.raises(PrepareError, match="download of whisper large-v3 failed"):
        prepare(
            _cuda_settings([CUDA_AUDIO], []), "linux-cuda",
            executor=(sys.executable, str(prepare_executor)), download=True,
        )
    assert fetched == [("whisper", "large-v3")]

    fetched.clear()
    prepare_module.download_weights("document", CUDA_DOC)
    repos = [repo for repo, _ in fetched]
    assert repos == ["docling-project/docling-layout-heron", "docling-project/docling-models"]
    assert fetched[1] == ("docling-project/docling-models", "v2.3.0")

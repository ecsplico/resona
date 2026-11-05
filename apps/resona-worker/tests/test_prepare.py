"""``prepare``: weights pinned to the release, synthetic runs checked, tested tuples recorded."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from resona_job_core import Candidate, ExecutionProfile, OrganizationSettings

from resona_worker import prepare as prepare_module
from resona_worker import weights as weights_module
from resona_worker.config import load_capabilities, load_manifest
from resona_worker.prepare import PrepareError, prepare, write_manifest
from resona_worker.weights import WeightsError, release_model_pins

AUDIO = Candidate(engine="mlx-whisper", model="mlx-community/whisper-large-v3-mlx", accelerator="metal")
DOC = Candidate(engine="docling", model="docling-standard", accelerator="metal", ocr_engine="ocrmac")
SNAPSHOT_HASH = "49e6aa286ad60c14352c404340ded53710378a11"
LAYOUT = "docling-project/docling-layout-heron"
LAYOUT_PIN = "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8"
TABLE = "docling-project/docling-models"
TABLE_PIN = "fc0f2d45e2218ea24bce5045f58a389aed16dc23"
OTHER = "1" * 40
PINS = {AUDIO.model: SNAPSHOT_HASH, LAYOUT: LAYOUT_PIN, TABLE: TABLE_PIN}
NEVER_RUN = (sys.executable, "-c", "raise SystemExit(99)")


def _settings(audio: list[Candidate], document: list[Candidate]) -> OrganizationSettings:
    return OrganizationSettings(execution_profiles={"mac-private": ExecutionProfile(audio=audio, document=document)})


@pytest.fixture(autouse=True)
def docling_stages(monkeypatch) -> None:
    """Docling 2.129's stage weights (``stage_weights()``) without importing Docling."""
    monkeypatch.setattr(
        prepare_module, "_stage_weights", lambda kind: [(LAYOUT, "main"), (TABLE, "v2.3.0")] if kind == "document" else []
    )


@pytest.fixture
def downloads(monkeypatch, hf_cache) -> list[tuple[str, str]]:
    """Fake network: a download caches the requested commit (no refs), like ``snapshot_download``."""
    fetched: list[tuple[str, str]] = []

    def download(repo: str, revision: str) -> Path:
        fetched.append((repo, revision))
        return hf_cache.add(repo, revision)

    monkeypatch.setattr(weights_module, "download_snapshot", download)
    return fetched


@pytest.fixture
def cached(hf_cache, downloads):
    """Every pinned repo cached at its pin under the ref its engine loads."""
    hf_cache.add(AUDIO.model, SNAPSHOT_HASH, "main")
    hf_cache.add(LAYOUT, LAYOUT_PIN, "main")
    hf_cache.add(TABLE, TABLE_PIN, "v2.3.0")
    return hf_cache


def _ref(hf_cache, repo: str, ref: str) -> str:
    return (hf_cache.root / f"models--{repo.replace('/', '--')}" / "refs" / ref).read_text()


def test_prepare_records_tested_tuples_with_pinned_revisions_memory_and_stage_devices(
    tmp_path: Path, prepare_executor: Path, cached, downloads
) -> None:
    manifest = prepare(
        _settings([AUDIO], [DOC]), "mac-private", executor=(sys.executable, str(prepare_executor)), model_pins=PINS
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
    assert [(w.repo, w.revision) for w in document.weights] == [(LAYOUT, LAYOUT_PIN), (TABLE, TABLE_PIN)]
    assert document.stage_accelerators == {"layout:docling-layout": "metal", "ocr:ocrmac": "metal"}


def test_default_pins_are_the_installed_releases_packaged_pins(prepare_executor: Path, cached) -> None:
    pins = release_model_pins()
    assert {AUDIO.model, LAYOUT, TABLE} <= pins.keys()
    assert all(len(pins[repo]) == 40 for repo in (AUDIO.model, LAYOUT, TABLE))

    manifest = prepare(_settings([AUDIO], []), "mac-private", executor=(sys.executable, str(prepare_executor)))

    assert [(w.repo, w.revision) for w in manifest.tested[0].weights] == [(AUDIO.model, pins[AUDIO.model])]


def test_missing_pinned_weights_are_downloaded_at_the_pin_and_the_loaded_ref_points_at_it(
    prepare_executor: Path, hf_cache, downloads
) -> None:
    manifest = prepare(
        _settings([AUDIO], [DOC]), "mac-private", executor=(sys.executable, str(prepare_executor)), model_pins=PINS
    )

    assert downloads == [(AUDIO.model, SNAPSHOT_HASH), (LAYOUT, LAYOUT_PIN), (TABLE, TABLE_PIN)]
    assert _ref(hf_cache, AUDIO.model, "main") == SNAPSHOT_HASH
    assert _ref(hf_cache, LAYOUT, "main") == LAYOUT_PIN
    assert _ref(hf_cache, TABLE, "v2.3.0") == TABLE_PIN
    assert len(manifest.tested) == 2


def test_cached_main_on_another_commit_is_never_used(prepare_executor: Path, hf_cache, downloads) -> None:
    """An unpinned ``main`` snapshot is not accepted: the pinned commit is fetched and ``main`` re-pointed."""
    hf_cache.add(AUDIO.model, OTHER, "main")

    manifest = prepare(
        _settings([AUDIO], []), "mac-private", executor=(sys.executable, str(prepare_executor)), model_pins=PINS
    )

    assert downloads == [(AUDIO.model, SNAPSHOT_HASH)]
    assert _ref(hf_cache, AUDIO.model, "main") == SNAPSHOT_HASH
    assert [(w.repo, w.revision) for w in manifest.tested[0].weights] == [(AUDIO.model, SNAPSHOT_HASH)]


def test_cached_pinned_snapshot_is_still_verified_online(prepare_executor: Path, cached, downloads) -> None:
    """A cached snapshot may be one an interrupted download left without its weights file:
    online, every pin goes through ``snapshot_download``, which resumes or completes it."""
    prepare(_settings([AUDIO], [DOC]), "mac-private", executor=(sys.executable, str(prepare_executor)), model_pins=PINS)

    assert downloads == [(AUDIO.model, SNAPSHOT_HASH), (LAYOUT, LAYOUT_PIN), (TABLE, TABLE_PIN)]


def test_offline_uses_the_cached_pins_without_downloading(prepare_executor: Path, cached, downloads) -> None:
    manifest = prepare(
        _settings([AUDIO], [DOC]), "mac-private", executor=(sys.executable, str(prepare_executor)),
        model_pins=PINS, offline=True,
    )

    assert len(manifest.tested) == 2
    assert downloads == []



def test_offline_names_the_missing_repo_at_its_pinned_revision(hf_cache, downloads) -> None:
    hf_cache.add(AUDIO.model, OTHER, "main")

    with pytest.raises(PrepareError, match=f"{AUDIO.model}@{SNAPSHOT_HASH} is not in the local HuggingFace cache and --offline"):
        prepare(_settings([AUDIO], []), "mac-private", executor=NEVER_RUN, model_pins=PINS, offline=True)

    assert downloads == []


def test_failed_download_fails_before_any_synthetic_run(monkeypatch, hf_cache) -> None:
    def offline(repo: str, revision: str) -> Path:
        raise WeightsError(f"download of {repo}@{revision} failed: ConnectError")

    monkeypatch.setattr(weights_module, "download_snapshot", offline)

    with pytest.raises(PrepareError, match=f"download of {LAYOUT}@{LAYOUT_PIN} failed"):
        prepare(_settings([], [DOC]), "mac-private", executor=NEVER_RUN, model_pins=PINS)


@pytest.mark.parametrize(
    ("audio", "document", "pins", "reason"),
    [
        ([AUDIO], [], {}, f"does not pin a revision for {AUDIO.model}"),
        ([], [DOC], {TABLE: TABLE_PIN}, f"does not pin a revision for {LAYOUT}"),
        ([], [DOC], {LAYOUT: LAYOUT_PIN}, f"does not pin a revision for {TABLE}"),
        ([AUDIO.model_copy(update={"model_revision": OTHER})], [], PINS, "the release pins"),
        ([AUDIO], [], {AUDIO.model: "v1.0"}, "not a 40-hex commit"),
    ],
)
def test_release_without_or_contradicting_a_pin_fails_before_any_run(cached, audio, document, pins, reason) -> None:
    with pytest.raises(PrepareError, match=reason):
        prepare(_settings(audio, document), "mac-private", executor=NEVER_RUN, model_pins=pins)


def test_release_whose_layout_pin_differs_from_the_cache_fails_offline(cached) -> None:
    pins = {**PINS, LAYOUT: OTHER}

    with pytest.raises(PrepareError, match=f"{LAYOUT}@{OTHER} is not in the local HuggingFace cache"):
        prepare(_settings([], [DOC]), "mac-private", executor=NEVER_RUN, model_pins=pins, offline=True)


def test_document_run_that_loaded_another_layout_revision_is_rejected(
    prepare_executor: Path, cached, monkeypatch
) -> None:
    """Provenance is checked too: a run that loaded unpinned layout weights cannot be approved."""
    monkeypatch.setattr(prepare_module, "ensure_pinned_snapshot", lambda repo, ref, pin, *, offline: None)
    pins = {**PINS, LAYOUT: OTHER}

    with pytest.raises(PrepareError, match=f"loaded {LAYOUT}@{LAYOUT_PIN} \\(docling-layout\\), the release pins {OTHER}"):
        prepare(_settings([], [DOC]), "mac-private", executor=(sys.executable, str(prepare_executor)), model_pins=pins)


def test_document_run_that_loaded_an_unpinned_model_is_rejected(prepare_executor: Path, cached, monkeypatch) -> None:
    monkeypatch.setattr(prepare_module, "_stage_weights", lambda kind: [])
    pins = {AUDIO.model: SNAPSHOT_HASH}

    with pytest.raises(PrepareError, match=f"loaded {LAYOUT} \\(docling-layout\\), which the release does not pin"):
        prepare(_settings([], [DOC]), "mac-private", executor=(sys.executable, str(prepare_executor)), model_pins=pins)


@pytest.mark.parametrize(
    ("model", "reason"),
    [
        ("org/broken-model", "engine_error"),
        ("org/garbage-model", "expected content"),
        ("org/wrongdevice-model", "did not run asr on metal"),
    ],
)
def test_failing_candidate_aborts_prepare(prepare_executor: Path, cached, model: str, reason: str) -> None:
    cached.add(model, SNAPSHOT_HASH, "main")
    bad = Candidate(engine="mlx-whisper", model=model, accelerator="metal")

    with pytest.raises(PrepareError, match=reason):
        prepare(
            _settings([AUDIO, bad], []), "mac-private",
            executor=(sys.executable, str(prepare_executor)), model_pins={**PINS, model: SNAPSHOT_HASH},
        )


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(PrepareError, match="not defined"):
        prepare(_settings([AUDIO], []), "linux-cuda")


def test_synthetic_runs_hang_are_killed_after_the_timeout(cached, tmp_path: Path) -> None:
    hang = tmp_path / "hang.py"
    hang.write_text("import time; time.sleep(60)\n", encoding="utf-8")

    with pytest.raises(PrepareError, match="exit -9"):
        prepare(_settings([AUDIO], []), "mac-private", executor=(sys.executable, str(hang)), timeout=0.5, model_pins=PINS)


CUDA_AUDIO = Candidate(engine="whisper", model="large-v3", accelerator="cuda")


def _cuda_settings(audio: list[Candidate]) -> OrganizationSettings:
    return OrganizationSettings(execution_profiles={"linux-cuda": ExecutionProfile(audio=audio, document=[])})


def test_whisper_checkpoint_must_hash_to_the_published_revision(
    tmp_path: Path, prepare_executor: Path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    checkpoint = tmp_path / "cache" / "whisper" / "large-v3.pt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"tampered weights")
    executor = (sys.executable, str(prepare_executor))
    published = prepare_module.whisper_revision("large-v3")

    with pytest.raises(PrepareError, match="does not match its published SHA-256"):
        prepare(_cuda_settings([CUDA_AUDIO]), "linux-cuda", executor=executor, model_pins={"large-v3": published})

    tampered = hashlib.sha256(b"tampered weights").hexdigest()
    monkeypatch.setattr(prepare_module, "whisper_revision", lambda model: tampered)
    manifest = prepare(_cuda_settings([CUDA_AUDIO]), "linux-cuda", executor=executor, model_pins={"large-v3": tampered})
    assert [(w.repo, w.revision) for w in manifest.tested[0].weights] == [("large-v3", tampered)]


def test_release_pin_other_than_the_installed_whisper_checkpoint_fails() -> None:
    with pytest.raises(PrepareError, match="pinned revision of whisper large-v3"):
        prepare(_cuda_settings([CUDA_AUDIO]), "linux-cuda", executor=NEVER_RUN, model_pins={"large-v3": "0" * 64})


def test_missing_whisper_checkpoint_is_downloaded_unless_offline(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "empty"))
    pins = {"large-v3": prepare_module.whisper_revision("large-v3")}
    fetched: list[str] = []

    def fake_download(model: str) -> Path:
        fetched.append(model)
        raise WeightsError(f"download of whisper {model} failed: URLError")

    monkeypatch.setattr(prepare_module, "download_whisper_checkpoint", fake_download)

    with pytest.raises(PrepareError, match=f"large-v3@{pins['large-v3']} is not prepared and --offline"):
        prepare(_cuda_settings([CUDA_AUDIO]), "linux-cuda", executor=NEVER_RUN, model_pins=pins, offline=True)
    assert fetched == []

    with pytest.raises(PrepareError, match="download of whisper large-v3 failed"):
        prepare(_cuda_settings([CUDA_AUDIO]), "linux-cuda", executor=NEVER_RUN, model_pins=pins)
    assert fetched == ["large-v3"]

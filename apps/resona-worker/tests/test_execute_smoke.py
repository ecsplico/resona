"""Real execution smoke tests: ``resona_worker.execute`` as an actual subprocess.

No engine is mocked. Audio runs faster-whisper large-v3 on CPU (int8) from the
local HuggingFace cache; the run uses the private environment, so HF/telemetry
are offline and cloud keys are stripped. Document runs Docling on the digital
PDF fixture of ``resona-engine-docling``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from resona_job_core import JobResult

from resona_worker.execute import ExitCode, subprocess_env

TESTS = Path(__file__).parent
REPO_ROOT = Path(__file__).resolve().parents[3]
DE_SHORT = TESTS / "fixtures" / "de_short.wav"
DIGITAL_PDF = REPO_ROOT / "packages" / "engine-docling" / "tests" / "fixtures" / "digital.pdf"

WHISPER_MODEL = "Systran/faster-whisper-large-v3"
WHISPER_REVISION = "edaa852ec7e145841d8ffdb056a99866b5f0a478"


def _hf_hub_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _run_executor(tmp_path: Path, snapshot: dict, input_path: Path, extra_env: dict[str, str]) -> tuple[int, JobResult | None]:
    snapshot_path = tmp_path / "snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    out_path = tmp_path / "out.json"
    env = subprocess_env(private=True, base={**os.environ, **extra_env})
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "resona_worker.execute",
            "--snapshot",
            str(snapshot_path),
            "--input",
            str(input_path),
            "--out",
            str(out_path),
        ],
        env=env,
        capture_output=True,
        timeout=900,
        check=False,
    )
    if proc.returncode != 0 or not out_path.exists():
        return proc.returncode, None
    return proc.returncode, JobResult.model_validate_json(out_path.read_text(encoding="utf-8"))


def test_real_german_audio_transcribes_offline_on_cpu(tmp_path: Path) -> None:
    snapshot_dir = _hf_hub_dir() / "models--Systran--faster-whisper-large-v3" / "snapshots" / WHISPER_REVISION
    if not snapshot_dir.is_dir():
        pytest.skip(f"faster-whisper large-v3 not in the local HF cache at {snapshot_dir}")

    snapshot = {
        "kind": "audio",
        "language": "de",
        "options": {},
        "candidate": {
            "engine": "faster-whisper",
            "model": WHISPER_MODEL,
            "model_revision": WHISPER_REVISION,
            "accelerator": "cpu",
        },
    }

    rc, result = _run_executor(tmp_path, snapshot, DE_SHORT, {"FASTWHISPER_COMPUTE_TYPE": "int8"})

    assert rc == ExitCode.OK
    assert result is not None
    text = result.raw_text.lower()
    assert "guten tag" in text
    assert "test" in text
    asr = [stage for stage in result.provenance.stages if stage.stage == "asr"]
    assert len(asr) == 1
    assert asr[0].accelerator == "cpu"


def test_real_digital_pdf_converts_with_docling(tmp_path: Path) -> None:
    snapshot = {
        "kind": "document",
        "language": "de",
        "options": {"ocr": "off"},
        "candidate": {"engine": "docling", "model": "docling-project/docling-layout", "accelerator": "cpu"},
    }

    rc, result = _run_executor(tmp_path, snapshot, DIGITAL_PDF, {})

    assert rc == ExitCode.OK
    assert result is not None
    assert result.kind == "document"
    assert [page.number for page in result.pages] == [1, 2]
    assert "Dieses Dokument ist ein synthetisches Testdokument." in result.markdown

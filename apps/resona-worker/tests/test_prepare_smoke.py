"""Real ``prepare --profile mac-private`` on Apple Silicon: MLX Whisper on Metal, Docling + ocrmac.

Nothing is mocked: weights come from the local HuggingFace cache, the synthetic
runs go through ``python -m resona_worker.execute`` in the private offline env.
"""

from __future__ import annotations

import platform
import sys

import pytest
from resona_job_core import default_settings

from resona_worker.prepare import prepare
from resona_worker.weights import local_snapshot

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or platform.machine() != "arm64", reason="mac-private needs Apple Silicon"
)


def test_mac_private_prepares_mlx_whisper_and_ocrmac_on_metal() -> None:
    if local_snapshot("mlx-community/whisper-large-v3-mlx", None) is None:
        pytest.skip("mlx-community/whisper-large-v3-mlx is not in the local HuggingFace cache")

    manifest = prepare(default_settings(), "mac-private")

    audio, document = manifest.tested
    assert (audio.candidate.engine, audio.stage_accelerators) == ("mlx-whisper", {"asr:mlx-whisper": "metal"})
    assert audio.weights and len(audio.weights[0].revision) == 40
    assert audio.peak_accelerator_bytes and audio.peak_accelerator_bytes > 0
    assert document.candidate.ocr_engine == "ocrmac"
    assert document.stage_accelerators["layout:docling-layout"] == "metal"
    assert document.stage_accelerators["ocr:ocrmac"] == "auto"
    assert {w.repo for w in document.weights} >= {"docling-project/docling-layout-heron"}

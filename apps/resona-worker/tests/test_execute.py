"""Execution subprocess environment, OOM classification and exit-code contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from resona_worker import execute
from resona_worker.execute import ExitCode, main, subprocess_env

DE_SHORT = Path(execute.__file__).parent / "fixtures" / "de_short.wav"

BASE_ENV = {
    "PATH": "/usr/bin",
    "HOME": "/Users/worker",
    "LANG": "de_DE.UTF-8",
    "LC_ALL": "de_DE.UTF-8",
    "HF_HOME": "/models/hf",
    "FASTWHISPER_COMPUTE_TYPE": "int8",
    "OPENAI_API_KEY": "sk-openai",
    "OPENAI_BASE_URL": "https://api.openai.example",
    "DEEPGRAM_API_KEY": "dg-key",
    "ELEVENLABS_API_KEY": "el-key",
    "ANTHROPIC_API_KEY": "an-key",
    "MY_PROVIDER_API_KEY": "custom-key",
    "AWS_ACCESS_KEY_ID": "AKIA",
    "AZURE_OPENAI_ENDPOINT": "https://azure.example",
    "GOOGLE_APPLICATION_CREDENTIALS": "/secrets/gcp.json",
    "RESONA_API_KEY": "rsn-client",
    "HF_TOKEN": "hf-token",
    "CUDA_API_TOKEN": "credential-shaped despite allowed prefix",
    "SOME_UNRELATED_SETTING": "x",
}


def test_private_environment_is_an_allowlist_with_offline_and_telemetry_switches() -> None:
    env = subprocess_env(private=True, base=BASE_ENV)

    assert env == {
        "PATH": "/usr/bin",
        "HOME": "/Users/worker",
        "LANG": "de_DE.UTF-8",
        "LC_ALL": "de_DE.UTF-8",
        "HF_HOME": "/models/hf",
        "FASTWHISPER_COMPUTE_TYPE": "int8",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
        "DO_NOT_TRACK": "1",
        "ANONYMIZED_TELEMETRY": "False",
    }


def test_private_environment_cannot_be_switched_back_online() -> None:
    env = subprocess_env(private=True, base={**BASE_ENV, "HF_HUB_OFFLINE": "0", "DO_NOT_TRACK": "0"})

    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["DO_NOT_TRACK"] == "1"


def test_non_private_environment_keeps_provider_keys_but_never_the_hub_client_key() -> None:
    env = subprocess_env(private=False, base=BASE_ENV)

    assert env["OPENAI_API_KEY"] == "sk-openai"
    assert "RESONA_API_KEY" not in env
    assert "HF_HUB_OFFLINE" not in env


def test_executor_rejects_unknown_job_kind_with_invalid_snapshot_exit(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps({"kind": "video", "candidate": {"engine": "x", "model": "y", "accelerator": "cpu"}}),
        encoding="utf-8",
    )
    audio = tmp_path / "input.bin"
    audio.write_bytes(b"anything")

    code = main(["--snapshot", str(snapshot), "--input", str(audio), "--out", str(tmp_path / "out.json")])

    assert code == ExitCode.INVALID_SNAPSHOT
    assert not (tmp_path / "out.json").exists()


def _audio_snapshot(tmp_path: Path, model: str = "tiny") -> Path:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {"kind": "audio", "language": "de", "candidate": {"engine": "fake", "model": model, "accelerator": "metal"}}
        ),
        encoding="utf-8",
    )
    return snapshot


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("[metal::malloc] Attempting to allocate 68719476736 bytes"), ExitCode.OUT_OF_MEMORY),
        (RuntimeError("MPS backend out of memory (MPS allocated: 17 GB)"), ExitCode.OUT_OF_MEMORY),
        (MemoryError(), ExitCode.OUT_OF_MEMORY),
        (RuntimeError("decoder produced NaN"), ExitCode.ENGINE_ERROR),
    ],
)
def test_engine_failures_map_out_of_memory_separately(tmp_path: Path, monkeypatch, error, expected) -> None:
    class FailingEngine:
        def __init__(self, device: str, modelname: str) -> None:
            pass

        def transcribe(self, audio, **kwargs):
            raise error

    monkeypatch.setattr(execute, "_load_engine", lambda name: FailingEngine)
    out = tmp_path / "out.json"

    code = main(["--snapshot", str(_audio_snapshot(tmp_path)), "--input", str(DE_SHORT), "--out", str(out)])

    assert code == expected
    assert not out.exists()


def test_hub_hosted_model_without_local_weights_is_an_unsupported_capability(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(execute, "_load_engine", lambda name: object)
    monkeypatch.setattr(execute, "local_snapshot", lambda repo, revision: None)

    code = main(
        [
            "--snapshot", str(_audio_snapshot(tmp_path, model="mlx-community/not-prepared")),
            "--input", str(DE_SHORT),
            "--out", str(tmp_path / "out.json"),
        ]
    )

    assert code == ExitCode.UNSUPPORTED_CAPABILITY


def test_provenance_records_the_loaded_snapshot_revision(tmp_path: Path, monkeypatch) -> None:
    snapshot_dir = tmp_path / "snapshots" / "49e6aa286ad60c14352c404340ded53710378a11"
    snapshot_dir.mkdir(parents=True)
    loaded: list[str] = []

    class Engine:
        def __init__(self, device: str, modelname: str) -> None:
            loaded.append(modelname)

        def transcribe(self, audio, **kwargs):
            return {"text": "Guten Tag", "language": "de", "segments": []}

    monkeypatch.setattr(execute, "_load_engine", lambda name: Engine)
    monkeypatch.setattr(execute, "local_snapshot", lambda repo, revision: snapshot_dir)
    out = tmp_path / "out.json"

    code = main(
        [
            "--snapshot", str(_audio_snapshot(tmp_path, model="mlx-community/whisper-large-v3-mlx")),
            "--input", str(DE_SHORT),
            "--out", str(out),
        ]
    )

    assert code == ExitCode.OK
    assert loaded == [str(snapshot_dir)]
    stage = json.loads(out.read_text())["provenance"]["stages"][0]
    assert (stage["model"], stage["model_revision"], stage["accelerator"]) == (
        "mlx-community/whisper-large-v3-mlx",
        "49e6aa286ad60c14352c404340ded53710378a11",
        "metal",
    )

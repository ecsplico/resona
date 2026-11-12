"""Execution subprocess environment, OOM classification and exit-code contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from resona_job_core import JobResult, Provenance, Segment, StageProvenance

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


@pytest.mark.parametrize(
    "kind, options",
    [("document", {"ocr": "auto", "future_option": True}), ("audio", {"translate": False, "future_option": 1})],
)
def test_executor_exits_unknown_options_before_loading_anything(tmp_path: Path, kind, options) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({
        "kind": kind, "options": options,
        "candidate": {"engine": "not-installed", "model": "y", "accelerator": "cpu"},
    }), encoding="utf-8")
    source = tmp_path / "input.bin"
    source.write_bytes(b"anything")

    code = main(["--snapshot", str(snapshot), "--input", str(source), "--out", str(tmp_path / "out.json")])

    assert code == ExitCode.UNKNOWN_OPTIONS


def _audio_snapshot(tmp_path: Path, model: str = "tiny", *, engine: str = "fake", accelerator: str = "cpu") -> Path:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {"kind": "audio", "language": "de", "candidate": {"engine": engine, "model": model, "accelerator": accelerator}}
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
    monkeypatch.setattr(execute, "local_snapshot", lambda repo, revision, allow_patterns=None: None)

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
    monkeypatch.setattr(execute, "local_snapshot", lambda repo, revision, allow_patterns=None: snapshot_dir)
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
        "cpu",
    )


def _transcribe_with(tmp_path: Path, monkeypatch, out: dict) -> JobResult:
    class Engine:
        def __init__(self, device: str, modelname: str) -> None:
            pass

        def transcribe(self, audio, **kwargs):
            return out

    monkeypatch.setattr(execute, "_load_engine", lambda name: Engine)
    result = tmp_path / "out.json"

    code = main(["--snapshot", str(_audio_snapshot(tmp_path)), "--input", str(DE_SHORT), "--out", str(result)])

    assert code == ExitCode.OK
    return JobResult.model_validate_json(result.read_text())


def test_audio_markdown_breaks_paragraphs_at_pauses_and_keeps_raw_text(tmp_path: Path, monkeypatch) -> None:
    segments = [
        {"start": 0.0, "end": 2.0, "text": " Guten Tag."},
        {"start": 2.3, "end": 4.0, "text": " Wie geht es?"},
        {"start": 5.5, "end": 7.0, "text": " Danke, gut."},  # 1.5 s pause: new paragraph
        {"start": 7.2, "end": 7.4, "text": " "},
        {"start": 9.0, "end": 10.0, "text": " Tschüss."},  # pause measured from the last spoken segment
    ]
    text = "Guten Tag. Wie geht es? Danke, gut. Tschüss."

    result = _transcribe_with(tmp_path, monkeypatch, {"text": text, "language": "de", "segments": segments})

    assert result.markdown == "Guten Tag. Wie geht es?\n\nDanke, gut.\n\nTschüss."
    assert result.raw_text == text


def test_audio_markdown_ends_long_paragraphs_at_a_sentence_end(tmp_path: Path, monkeypatch) -> None:
    sentence = "x" * (execute.PARAGRAPH_MAX_CHARS // 2) + "."
    unfinished = "y" * (execute.PARAGRAPH_MAX_CHARS // 2) + ":"  # a colon does not end a sentence
    texts = [sentence, unfinished, sentence, sentence]
    segments = [{"start": float(i), "end": i + 0.9, "text": t} for i, t in enumerate(texts)]

    result = _transcribe_with(tmp_path, monkeypatch, {"text": " ".join(texts), "segments": segments})

    # Over the limit after the unfinished second segment, so the paragraph ends after the next sentence.
    assert result.markdown == f"{sentence} {unfinished} {sentence}\n\n{sentence}"


def test_audio_markdown_without_punctuation_breaks_at_twice_the_limit() -> None:
    texts = ["a" * execute.PARAGRAPH_MAX_CHARS, "b" * (execute.PARAGRAPH_MAX_CHARS - 1), "c", "d"]
    segments = [Segment(start=float(i), end=i + 0.9, text=t) for i, t in enumerate(texts)]

    # 1000 + " " + 999 = 2000 characters: the third segment opens a new paragraph.
    assert execute.audio_markdown(segments, "") == f"{texts[0]} {texts[1]}\n\nc d"


def test_audio_markdown_without_segments_is_the_plain_transcript(tmp_path: Path, monkeypatch) -> None:
    result = _transcribe_with(tmp_path, monkeypatch, {"text": " Guten Tag. ", "segments": []})

    assert result.markdown == result.raw_text == "Guten Tag."


def _diarized_run(tmp_path: Path, monkeypatch, options: dict, turns: list) -> tuple[int, Path]:
    class Engine:
        def __init__(self, device: str, modelname: str) -> None:
            pass

        def transcribe(self, audio, **kwargs):
            words = [
                {"start": 0.6, "end": 1.0, "word": " Good"},
                {"start": 1.1, "end": 1.8, "word": " morning."},
                {"start": 2.2, "end": 3.0, "word": " Hello."},
            ]
            return {"text": "Good morning. Hello.", "language": "de",
                    "segments": [{"start": 0.6, "end": 3.0, "text": " Good morning. Hello.", "words": words}]}

    monkeypatch.setattr(execute, "_load_engine", lambda name: Engine)
    monkeypatch.setattr(execute, "_diarize", lambda audio, candidate, snapshot: (turns, 0.1))
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({"kind": "audio", "language": "de", "options": options,
                                    "candidate": {"engine": "fake", "model": "tiny", "accelerator": "cpu"}}))
    out = tmp_path / "out.json"
    return main(["--snapshot", str(snapshot), "--input", str(DE_SHORT), "--out", str(out)]), out


def test_diarized_translation_is_labelled_in_english_and_starts_at_speaker_one(tmp_path: Path, monkeypatch) -> None:
    from resona_diarize import Turn

    weights = tmp_path / "abc123"
    monkeypatch.setattr(execute, "_diarizer_weights", lambda: ("nemotron-diarization", "nvidia/x", weights))
    # Diarizer speaker 1 has no word: the transcript still starts at 1 and has no gap.
    turns = [Turn(0.0, 0.4, 1), Turn(0.5, 2.0, 2), Turn(2.0, 4.0, 3)]

    code, out = _diarized_run(tmp_path, monkeypatch, {"diarize": True, "translate": True}, turns)

    assert code == ExitCode.OK
    result = JobResult.model_validate_json(out.read_text())
    assert result.markdown == "**Speaker 1:** Good morning.\n\n**Speaker 2:** Hello."
    assert [s.speaker for s in result.segments] == [1, 2]


def test_unprepared_diarizer_is_its_own_exit_code(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(execute, "release_model_pins", lambda: {})

    code, out = _diarized_run(tmp_path, monkeypatch, {"diarize": True}, [])

    assert code == ExitCode.DIARIZER_UNAVAILABLE
    assert not out.exists()


def test_cuda_candidate_on_a_host_without_cuda_is_an_unsupported_capability(tmp_path: Path, monkeypatch) -> None:
    # Provenance must never claim a device that did not run: no engine is constructed.
    constructed: list[str] = []

    class Engine:
        def __init__(self, device: str, modelname: str) -> None:
            constructed.append(device)

    monkeypatch.setattr(execute, "_load_engine", lambda name: Engine)
    monkeypatch.setattr(execute, "accelerator_available", lambda accelerator: accelerator != "cuda")
    out = tmp_path / "out.json"

    code = main(["--snapshot", str(_audio_snapshot(tmp_path, accelerator="cuda")), "--input", str(DE_SHORT), "--out", str(out)])

    assert code == ExitCode.UNSUPPORTED_CAPABILITY
    assert constructed == []
    assert not out.exists()


def test_probe_reports_cuda_unavailable_when_no_gpu_is_visible(tmp_path: Path) -> None:
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": ""}
    probe = [sys.executable, "-m", "resona_worker.execute", "--probe-accelerator"]

    hidden = subprocess.run([*probe, "cuda"], env=env, capture_output=True, check=False)
    cpu = subprocess.run([*probe, "cpu"], env=env, capture_output=True, check=False)
    unknown = subprocess.run([*probe, "tpu"], env=env, capture_output=True, check=False)

    assert (hidden.returncode, cpu.returncode, unknown.returncode) == (
        ExitCode.UNSUPPORTED_CAPABILITY,
        ExitCode.OK,
        ExitCode.UNSUPPORTED_CAPABILITY,
    )


def test_whisper_alias_loads_the_prepared_checkpoint_file_and_records_its_sha(tmp_path: Path, monkeypatch) -> None:
    import whisper

    published = whisper._MODELS["large-v3"].split("/")[-2]
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    loaded: list[str] = []

    class Engine:
        def __init__(self, device: str, modelname: str) -> None:
            loaded.append(modelname)

        def transcribe(self, audio, **kwargs):
            return {"text": "Guten Tag", "language": "de", "segments": []}

    monkeypatch.setattr(execute, "_load_engine", lambda name: Engine)
    snapshot = _audio_snapshot(tmp_path, model="large-v3", engine="whisper")
    out = tmp_path / "out.json"
    args = ["--snapshot", str(snapshot), "--input", str(DE_SHORT), "--out", str(out)]

    # Not prepared: the alias must not fall through to openai-whisper's own downloader.
    assert main(args) == ExitCode.UNSUPPORTED_CAPABILITY
    assert loaded == []

    checkpoint = tmp_path / "cache" / "whisper" / "large-v3.pt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"prepared")
    assert main(args) == ExitCode.OK
    assert loaded == [str(checkpoint)]
    stage = json.loads(out.read_text())["provenance"]["stages"][0]
    assert (stage["model"], stage["model_revision"]) == ("large-v3", published)


def test_whisper_alias_pinned_to_another_revision_is_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(execute, "_load_engine", lambda name: lambda **kwargs: pytest.fail("engine must not be built"))
    checkpoint = tmp_path / "cache" / "whisper" / "large-v3.pt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"prepared")
    snapshot = tmp_path / "snapshot.json"
    candidate = {"engine": "whisper", "model": "large-v3", "model_revision": "0" * 64, "accelerator": "cpu"}
    snapshot.write_text(json.dumps({"kind": "audio", "language": "de", "candidate": candidate}), encoding="utf-8")

    code = main(["--snapshot", str(snapshot), "--input", str(DE_SHORT), "--out", str(tmp_path / "out.json")])

    assert code == ExitCode.UNSUPPORTED_CAPABILITY


def test_device_peak_is_recorded_on_device_stages_only() -> None:
    result = JobResult(
        kind="document",
        raw_text="x",
        markdown="x",
        provenance=Provenance(
            stages=[
                StageProvenance(stage="layout", engine="docling-layout", model="m", accelerator="cuda", seconds=1.0),
                StageProvenance(stage="ocr", engine="rapidocr", model="r", accelerator="cpu", seconds=1.0),
            ]
        ),
    )

    stages = execute._with_stage_peaks(result, 3 * 2**30).provenance.stages

    assert [s.peak_accelerator_bytes for s in stages] == [3 * 2**30, None]
    assert execute._with_stage_peaks(result, None) == result

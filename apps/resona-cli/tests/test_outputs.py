"""`write_result`: the `<stem>.md` + `<stem>.result.json` pair is written all-or-nothing."""
from pathlib import Path

import pytest

from resona_cli import outputs
from resona_cli.outputs import OutputExists, output_paths, write_result
from resona_job_core import JobResult


def _result(text: str) -> JobResult:
    return JobResult(
        kind="audio", raw_text=text, markdown=text, language="de",
        provenance={"stages": [{"stage": "asr", "engine": "faster-whisper", "model": "m",
                                "accelerator": "cpu", "seconds": 1.0}]},
    )


def _fail_second_promote(monkeypatch):
    real = outputs._promote
    calls = []

    def promote(staged, path, *, force):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("disk full")
        real(staged, path, force=force)

    monkeypatch.setattr(outputs, "_promote", promote)
    return calls


def _leftovers(directory: Path) -> list[Path]:
    return [p for p in directory.iterdir() if p.name.startswith(".")]


def test_failed_second_promote_leaves_neither_file_and_retry_succeeds(tmp_path, monkeypatch):
    paths = output_paths(tmp_path / "memo.wav", tmp_path / "out")
    calls = _fail_second_promote(monkeypatch)

    with pytest.raises(OSError, match="disk full"):
        write_result(paths, _result("Hallo"), {"source": "memo.wav"}, force=False)

    assert len(calls) == 2
    assert not paths.markdown.exists() and not paths.result.exists()
    assert _leftovers(paths.markdown.parent) == []

    monkeypatch.undo()
    write_result(paths, _result("Hallo"), {"source": "memo.wav"}, force=False)
    assert paths.markdown.read_text(encoding="utf-8").rstrip().endswith("Hallo")
    assert JobResult.model_validate_json(paths.result.read_text(encoding="utf-8")).raw_text == "Hallo"


def test_failed_second_promote_with_force_restores_previous_files(tmp_path, monkeypatch):
    paths = output_paths(tmp_path / "memo.wav", None)
    paths.markdown.write_text("old md", encoding="utf-8")
    paths.result.write_text("old json", encoding="utf-8")
    _fail_second_promote(monkeypatch)

    with pytest.raises(OSError, match="disk full"):
        write_result(paths, _result("Neu"), {"source": "memo.wav"}, force=True)

    assert paths.markdown.read_text(encoding="utf-8") == "old md"
    assert paths.result.read_text(encoding="utf-8") == "old json"
    assert _leftovers(tmp_path) == []


def test_render_failure_writes_nothing(tmp_path, monkeypatch):
    paths = output_paths(tmp_path / "memo.wav", None)

    def boom(text, meta):
        raise ValueError("render failed")

    monkeypatch.setattr(outputs, "render_markdown", boom)
    with pytest.raises(ValueError):
        write_result(paths, _result("Hallo"), {"source": "memo.wav"}, force=False)

    assert not paths.markdown.exists() and not paths.result.exists()


def test_existing_target_without_force_touches_nothing(tmp_path):
    paths = output_paths(tmp_path / "memo.wav", None)
    paths.markdown.write_text("mine", encoding="utf-8")

    with pytest.raises(OutputExists):
        write_result(paths, _result("Hallo"), {"source": "memo.wav"}, force=False)

    assert paths.markdown.read_text(encoding="utf-8") == "mine"
    assert not paths.result.exists()

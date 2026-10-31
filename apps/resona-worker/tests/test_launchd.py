"""launchd user agent install/uninstall against a recorded ``launchctl``."""

from __future__ import annotations

import os
import plistlib
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from resona_job_core import Candidate

from resona_worker.launchd import InstallError, Launchd, install, uninstall

TOKEN = "wkr-launchd-secret-91c2"
CAND = Candidate(engine="mlx-whisper", model="mlx-community/whisper-large-v3-mlx", accelerator="metal")
LABEL = "de.ecsplico.resona-worker.test"


class FakeLaunchctl:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.loaded: set[str] = set()

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        args = list(args)
        self.calls.append(args)
        verb = args[0]
        if verb == "print":
            return subprocess.CompletedProcess(args, 0 if args[1] in self.loaded else 113, "", "")
        if verb == "bootstrap":
            label = plistlib.loads(Path(args[2]).read_bytes())["Label"]
            self.loaded.add(f"{args[1]}/{label}")
            return subprocess.CompletedProcess(args, 0, "", "")
        if verb == "bootout":
            self.loaded.discard(args[1])
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected launchctl call {args}")


@pytest.fixture
def launchd(tmp_path: Path) -> Launchd:
    return Launchd(
        agents_dir=tmp_path / "LaunchAgents",
        logs_dir=tmp_path / "Logs",
        cache_dir=tmp_path / "Caches",
        uid=501,
        launchctl=FakeLaunchctl(),
    )


@pytest.fixture
def files(tmp_path: Path, fakes) -> tuple[Path, Path]:
    token = tmp_path / "worker.token"
    token.write_text(TOKEN + "\n", encoding="utf-8")
    os.chmod(token, 0o600)
    caps = tmp_path / "caps.json"
    caps.write_text(fakes.manifest_json([CAND], profile="mac-private"), encoding="utf-8")
    return token, caps


def _install(launchd: Launchd, token: Path, caps: Path, **overrides):
    args = dict(
        profile="mac-private",
        hub="https://resona.example.de",
        token_file=token,
        capabilities_file=caps,
        label=LABEL,
        launchd=launchd,
        python="/opt/resona/bin/python",
        environ={"PATH": "/usr/bin:/bin", "HF_HOME": "/models/hf", "OPENAI_API_KEY": "sk-not-for-the-worker"},
        platform_name="darwin",
    )
    args.update(overrides)
    return install(**args)


def test_install_writes_agent_without_token_and_bootstraps_it(launchd: Launchd, files) -> None:
    token, caps = files

    path = _install(launchd, token, caps)

    raw = path.read_bytes()
    assert TOKEN.encode() not in raw
    assert b"sk-not-for-the-worker" not in raw
    assert oct(path.stat().st_mode & 0o777) == "0o644"
    plist = plistlib.loads(raw)
    assert plist["Label"] == LABEL
    assert plist["ProgramArguments"] == [
        "/opt/resona/bin/python", "-m", "resona_worker.cli", "run",
        "--hub", "https://resona.example.de",
        "--token-file", str(token),
        "--capabilities-file", str(caps),
        "--work-dir", str(launchd.cache_dir / LABEL),
    ]
    assert set(plist["EnvironmentVariables"]) == {"PATH", "PYTHONUNBUFFERED", "HF_HOME"}
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert (launchd.cache_dir / LABEL).is_dir()
    assert launchd.launchctl.calls[-1] == ["bootstrap", "gui/501", str(path)]
    assert launchd.is_loaded(LABEL)


def test_reinstall_boots_out_the_running_agent_first(launchd: Launchd, files) -> None:
    token, caps = files
    _install(launchd, token, caps)

    _install(launchd, token, caps)

    verbs = [call[0] for call in launchd.launchctl.calls]
    assert verbs == ["print", "bootstrap", "print", "bootout", "bootstrap"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"hub": "http://resona.example.de"}, "https"),
        ({"profile": "linux-cuda"}, "prepared for profile 'mac-private'"),
        ({"platform_name": "linux"}, "macOS-only"),
        ({"label": "../evil"}, "label"),
    ],
)
def test_install_refuses_unsafe_or_mismatched_configuration(launchd: Launchd, files, overrides, message) -> None:
    token, caps = files

    with pytest.raises(InstallError, match=message):
        _install(launchd, token, caps, **overrides)

    assert launchd.launchctl.calls == []
    assert not launchd.agents_dir.exists() or not any(launchd.agents_dir.iterdir())


@pytest.mark.parametrize("mode", [0o640, 0o644, 0o604])
def test_install_refuses_a_token_file_readable_by_others(launchd: Launchd, files, mode: int) -> None:
    token, caps = files
    os.chmod(token, mode)

    with pytest.raises(InstallError, match="0600") as raised:
        _install(launchd, token, caps)

    assert TOKEN not in str(raised.value)
    assert launchd.launchctl.calls == []


def test_uninstall_boots_out_and_removes_the_plist(launchd: Launchd, files) -> None:
    token, caps = files
    path = _install(launchd, token, caps)

    assert uninstall(label=LABEL, launchd=launchd) is True

    assert not path.exists()
    assert not launchd.is_loaded(LABEL)
    assert ["bootout", f"gui/501/{LABEL}"] in launchd.launchctl.calls


def test_uninstall_of_unknown_label_reports_nothing_removed(launchd: Launchd) -> None:
    assert uninstall(label=LABEL, launchd=launchd) is False

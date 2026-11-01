"""``resona-worker update``: hub check, verified wheels, drain ordering and rollback on failed prepare."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from resona_job_core.release import ReleaseManifest

from resona_worker import drain
from resona_worker.hub import ReleaseStatus
from resona_worker.update import Steps, UpdateError, UpdateRequest, current_release, run_update

RELEASE = "0.2.0"
MODEL = "mlx-community/whisper-large-v3-mlx"
BOGUS_REVISION = "0" * 40
WHEEL_BYTES = b"not really a wheel"


def _manifest(tmp_path: Path, *, revision: str = "49e6aa286ad60c14352c404340ded53710378a11",
              wheel_sha: str | None = None) -> tuple[Path, ReleaseManifest]:
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    (dist / f"resona_worker-{RELEASE}-py3-none-any.whl").write_bytes(WHEEL_BYTES)
    manifest = ReleaseManifest.model_validate({
        "release_id": RELEASE,
        "created_at": "2026-10-31T12:00:00Z",
        "wheels": [{
            "name": "resona-worker", "version": RELEASE,
            "sha256": wheel_sha or hashlib.sha256(WHEEL_BYTES).hexdigest(),
        }],
        "images": [],
        "models": [{"engine": "mlx-whisper", "model": MODEL, "revision": revision}],
        "worker_protocol": 1,
    })
    path = dist / "release-manifest.json"
    path.write_text(manifest.model_dump_json(), encoding="utf-8")
    return path, manifest


class FakeHub:
    def __init__(self, events: list[str], status: ReleaseStatus | None) -> None:
        self.events = events
        self.status = status

    def release(self, release_id: str) -> ReleaseStatus | None:
        self.events.append(f"hub:{release_id}")
        return self.status


def _status(manifest: ReleaseManifest, **changes) -> ReleaseStatus:
    body = {"release_id": RELEASE, "status": "staged", "manifest_sha256": manifest.digest(),
            "approved_tuples": [{"execution_profile": "mac-private", "platform": "mac-metal"}]}
    return ReleaseStatus.model_validate({**body, **changes})


def _caps_json(profile: str = "mac-private") -> str:
    return json.dumps({
        "schema_version": 1, "profile": profile, "prepared_at": "2026-10-31T12:00:00Z",
        "runtime_version": RELEASE, "platform": "darwin-arm64",
        "tested": [{"kind": "audio", "seconds": 1.0, "peak_rss_bytes": 1, "candidate": {
            "engine": "mlx-whisper", "model": MODEL, "model_revision": None, "accelerator": "metal",
        }}],
    })


@pytest.fixture
def layout(tmp_path: Path):
    releases = tmp_path / "releases"
    (releases / "0.1.0").mkdir(parents=True)
    (releases / "current").symlink_to("0.1.0")
    caps = tmp_path / "caps.json"
    caps.write_text("PREVIOUS", encoding="utf-8")
    return releases, caps


def _request(tmp_path: Path, manifest_path: Path, releases: Path, caps: Path, **changes) -> UpdateRequest:
    base = dict(
        release_id=RELEASE, manifest_source=str(manifest_path), profile="mac-private",
        capabilities_file=caps, work_dir=tmp_path / "work", releases_dir=releases, extras="apple,documents",
    )
    return UpdateRequest(**{**base, **changes})


def _recording_steps(events: list[str], *, prepare_code: int = 0, captured: dict | None = None) -> Steps:
    captured = captured if captured is not None else {}

    def create_env(env_dir: Path, requirement: str, constraints: Path) -> Path:
        events.append("install")
        captured["requirement"] = requirement
        captured["constraints"] = constraints.read_text().splitlines()
        return env_dir / "bin" / "python"

    def run_prepare(argv: list[str]) -> int:
        events.append("prepare")
        captured["prepare_argv"] = argv
        if prepare_code == 0:
            Path(argv[argv.index("--out") + 1]).write_text(_caps_json(), encoding="utf-8")
        return prepare_code

    return Steps(
        create_env=create_env,
        run_prepare=run_prepare,
        request_drain=lambda work: events.append("drain"),
        wait_drained=lambda work, timeout: events.append("drained"),
        release_drain=lambda work: events.append("resume"),
        supersede=lambda work: events.append("supersede"),
        restart=lambda python: events.append("restart"),
    )


def test_update_verifies_then_drains_installs_prepares_swaps_and_resumes(tmp_path, layout) -> None:
    releases, caps = layout
    manifest_path, manifest = _manifest(tmp_path)
    events: list[str] = []
    captured: dict = {}

    python = run_update(
        _request(tmp_path, manifest_path, releases, caps),
        FakeHub(events, _status(manifest)),
        _recording_steps(events, captured=captured),
    )

    assert events == [f"hub:{RELEASE}", "drain", "drained", "install", "prepare", "restart", "resume"]
    assert python == releases / RELEASE / "venv" / "bin" / "python"
    assert current_release(releases) == RELEASE
    assert json.loads(caps.read_text())["runtime_version"] == RELEASE
    assert captured["requirement"].startswith("resona-worker[apple,documents] @ file://")
    assert captured["constraints"] == []
    argv = captured["prepare_argv"]
    assert argv[argv.index("--release-manifest") + 1] == str(releases / RELEASE / "manifest.json")
    assert not list(releases.glob(".staging-*"))


def test_without_a_service_the_drained_worker_is_superseded_and_only_the_worker_is_installed(
    tmp_path, layout
) -> None:
    """No restart hook: the old supervisor is told to exit, not to resume on old code. Other
    pinned wheels are URL constraints, so unrelated packages (hub, CLI) are never installed."""
    releases, caps = layout
    manifest_path, manifest = _manifest(tmp_path)
    api_wheel = manifest_path.parent / f"resona_api-{RELEASE}-py3-none-any.whl"
    api_wheel.write_bytes(b"api wheel")
    manifest = ReleaseManifest.model_validate({**manifest.model_dump(mode="json"), "wheels": [
        *manifest.model_dump(mode="json")["wheels"],
        {"name": "resona-api", "version": RELEASE, "sha256": hashlib.sha256(b"api wheel").hexdigest()},
    ]})
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    events: list[str] = []
    captured: dict = {}
    steps = _recording_steps(events, captured=captured)
    steps.restart = None

    run_update(_request(tmp_path, manifest_path, releases, caps), FakeHub(events, _status(manifest)), steps)

    assert events == [f"hub:{RELEASE}", "drain", "drained", "install", "prepare", "supersede", "resume"]
    assert captured["requirement"].startswith("resona-worker[apple,documents] @ file://")
    assert captured["constraints"] == [
        f"resona-api @ {(releases / RELEASE / 'wheels' / api_wheel.name).as_uri()}"
    ]


def _with_locked_constraints(manifest_path: Path, manifest: ReleaseManifest, *, shipped: bytes, pinned: bytes) -> ReleaseManifest:
    (manifest_path.parent / "worker-constraints.txt").write_bytes(shipped)
    manifest = ReleaseManifest.model_validate({**manifest.model_dump(mode="json"), "constraints": {
        "name": "worker-constraints.txt", "sha256": hashlib.sha256(pinned).hexdigest(),
    }})
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    return manifest


def test_locked_third_party_constraints_are_verified_and_applied(tmp_path, layout) -> None:
    releases, caps = layout
    manifest_path, manifest = _manifest(tmp_path)
    locked = b"huggingface-hub==1.13.0 ; sys_platform != 'linux'\ntorch==2.12.0 ; sys_platform != 'linux'\n"
    manifest = _with_locked_constraints(manifest_path, manifest, shipped=locked, pinned=locked)
    events: list[str] = []
    captured: dict = {}

    run_update(_request(tmp_path, manifest_path, releases, caps), FakeHub(events, _status(manifest)),
               _recording_steps(events, captured=captured))

    assert captured["constraints"] == locked.decode().splitlines()


def test_tampered_constraints_file_is_refused_before_the_worker_is_drained(tmp_path, layout) -> None:
    releases, caps = layout
    manifest_path, manifest = _manifest(tmp_path)
    manifest = _with_locked_constraints(
        manifest_path, manifest, shipped=b"huggingface-hub==9.9.9\n", pinned=b"huggingface-hub==1.13.0\n"
    )
    events: list[str] = []

    with pytest.raises(UpdateError, match="worker-constraints.txt does not match"):
        run_update(_request(tmp_path, manifest_path, releases, caps), FakeHub(events, _status(manifest)),
                   _recording_steps(events))

    assert events == [f"hub:{RELEASE}"]
    assert caps.read_text() == "PREVIOUS"
    assert current_release(releases) == "0.1.0"


def test_failed_prepare_keeps_previous_release_and_capabilities(tmp_path, layout) -> None:
    releases, caps = layout
    manifest_path, manifest = _manifest(tmp_path)
    events: list[str] = []

    with pytest.raises(UpdateError, match="prepare failed"):
        run_update(
            _request(tmp_path, manifest_path, releases, caps),
            FakeHub(events, _status(manifest)),
            _recording_steps(events, prepare_code=1),
        )

    assert events == [f"hub:{RELEASE}", "drain", "drained", "install", "prepare", "resume"]
    assert caps.read_text() == "PREVIOUS"
    assert current_release(releases) == "0.1.0"
    assert not (releases / RELEASE).exists()


def test_bogus_model_revision_fails_the_real_prepare_without_replacing_the_release(tmp_path, layout) -> None:
    """The new environment's ``prepare`` (this interpreter) must find the manifest's exact revision."""
    releases, caps = layout
    manifest_path, manifest = _manifest(tmp_path, revision=BOGUS_REVISION)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"execution_profiles": {"mac-private": {
        "audio": [{"engine": "mlx-whisper", "model": MODEL, "accelerator": "metal"}], "document": [],
    }}}), encoding="utf-8")
    events: list[str] = []
    steps = _recording_steps(events)
    steps.create_env = lambda env_dir, requirement, constraints: Path(sys.executable)
    steps.run_prepare = Steps().run_prepare

    with pytest.raises(UpdateError, match="prepare failed"):
        run_update(
            _request(tmp_path, manifest_path, releases, caps, settings_file=settings),
            FakeHub(events, _status(manifest)),
            steps,
        )

    assert caps.read_text() == "PREVIOUS"
    assert current_release(releases) == "0.1.0"
    assert events[-1] == "resume"
    assert "restart" not in events


@pytest.mark.parametrize(
    ("status_changes", "wheel_sha", "match"),
    [
        (None, None, "not staged on the hub"),
        ({"manifest_sha256": "f" * 64}, None, "differs from the one staged"),
        ({}, "e" * 64, "does not match the manifest's sha256"),
    ],
)
def test_refusals_happen_before_the_worker_is_drained(tmp_path, layout, status_changes, wheel_sha, match) -> None:
    releases, caps = layout
    manifest_path, manifest = _manifest(tmp_path, wheel_sha=wheel_sha)
    status = None if status_changes is None else _status(manifest, **status_changes)
    events: list[str] = []

    with pytest.raises(UpdateError, match=match):
        run_update(_request(tmp_path, manifest_path, releases, caps), FakeHub(events, status), _recording_steps(events))

    assert events == [f"hub:{RELEASE}"]
    assert caps.read_text() == "PREVIOUS"
    assert current_release(releases) == "0.1.0"


def test_manifest_for_another_release_is_refused(tmp_path, layout) -> None:
    releases, caps = layout
    manifest_path, manifest = _manifest(tmp_path)
    events: list[str] = []

    with pytest.raises(UpdateError, match="not 0.3.0"):
        run_update(
            _request(tmp_path, manifest_path, releases, caps, release_id="0.3.0"),
            FakeHub(events, _status(manifest)), _recording_steps(events),
        )
    assert events == []


def test_drain_timeout_installs_nothing_and_resumes(tmp_path, layout) -> None:
    releases, caps = layout
    manifest_path, manifest = _manifest(tmp_path)
    events: list[str] = []
    steps = _recording_steps(events)

    def never_drains(work: Path, timeout: float) -> None:
        raise drain.DrainTimeout("worker did not drain")

    steps.wait_drained = never_drains

    with pytest.raises(UpdateError, match="nothing was installed"):
        run_update(_request(tmp_path, manifest_path, releases, caps), FakeHub(events, _status(manifest)), steps)

    assert events == [f"hub:{RELEASE}", "drain", "resume"]
    assert current_release(releases) == "0.1.0"
    assert not (releases / RELEASE).exists()

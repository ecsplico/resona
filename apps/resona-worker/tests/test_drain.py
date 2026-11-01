"""Drain handshake: the supervisor finishes its job, then reports idle and stops claiming."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
from resona_job_core import Candidate

from resona_worker import drain
from resona_worker.config import WorkerConfig
from resona_worker.hub import HubClient
from resona_worker.supervisor import Supervisor

CAND = Candidate(engine="faster-whisper", model="tiny", model_revision="rev1", accelerator="cpu")
HUB = "https://hub.test"
TOKEN = "wkr-test-credential-drain"


class _Stop(Exception):
    pass


def _supervisor(tmp_path: Path, executor_script: Path, hub, sleep) -> Supervisor:
    config = WorkerConfig(
        hub_url=HUB,
        token=TOKEN,
        work_dir=tmp_path / "work",
        capabilities=(CAND,),
        executor=(sys.executable, str(executor_script)),
        claim_wait_seconds=0,
        poll_interval_seconds=0.01,
    )
    client = HubClient(HUB, TOKEN, transport=hub.transport(), sleep=lambda seconds: None)
    return Supervisor(config, hub=client, sleep=sleep)


def test_drain_requested_while_idle_stops_claiming(tmp_path, executor_script, fakes) -> None:
    hub = fakes.FakeHub(CAND)
    work = tmp_path / "work"
    drain.request_drain(work)

    assert _supervisor(tmp_path, executor_script, hub, lambda s: None).run(once=True) == 0

    assert hub.calls == []
    assert (work / drain.DRAINED).exists()


def test_drain_requested_mid_job_lets_the_job_finish_first(tmp_path, executor_script, fakes) -> None:
    work = tmp_path / "work"

    class DrainOnInput(fakes.FakeHub):
        def _handle(self, request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/input"):
                drain.request_drain(work)
            return super()._handle(request)

    hub = DrainOnInput(CAND)
    order: list[str] = []

    def sleep(seconds: float) -> None:
        if (work / drain.DRAINED).exists():
            order.append("drained")
            raise KeyboardInterrupt

    assert _supervisor(tmp_path, executor_script, hub, sleep).run() == 130

    assert len(hub.paths("/result")) == 1
    assert hub.claim_count == 1
    assert order == ["drained"]
    paths = [path for _, path, _, _ in hub.calls]
    assert paths[-1].endswith("/result")


def test_released_drain_resumes_claiming_and_clears_the_marker(tmp_path, executor_script, fakes) -> None:
    hub = fakes.FakeHub(CAND)
    work = tmp_path / "work"
    drain.request_drain(work)
    supervisor = _supervisor(tmp_path, executor_script, hub, lambda s: None)
    supervisor.run(once=True)

    drain.release_drain(work)
    assert supervisor.run(once=True) == 0

    assert not (work / drain.DRAINED).exists()
    assert len(hub.paths("/result")) == 1
    assert all(headers["X-Resona-Worker-Protocol"] == "1" for _, _, headers, _ in hub.calls)


def test_superseded_worker_exits_without_claiming_again(tmp_path, executor_script, fakes) -> None:
    hub = fakes.FakeHub(CAND)
    work = tmp_path / "work"
    drain.request_drain(work)
    supervisor = _supervisor(tmp_path, executor_script, hub, lambda s: None)
    supervisor.run(once=True)
    claims_while_drained = hub.claim_count

    drain.mark_running(work)  # the in-process supervisor, between its two runs
    drain.supersede(work)
    drain.release_drain(work)
    assert supervisor.run() == 0

    assert hub.claim_count == claims_while_drained == 0
    assert not (work / drain.SUPERSEDED).exists()
    assert not (work / drain.DRAINED).exists()


def test_drain_wait_returns_when_the_supervisor_is_gone_and_times_out_while_it_lives(tmp_path) -> None:
    """A crashed or stopped worker cannot be claiming, so the update must not wait for a
    ``drained`` marker nobody will write; a live, busy one is waited for up to the timeout."""
    work = tmp_path / "work"
    drain.request_drain(work)
    clock = iter(range(100))

    drain.wait_drained(work, timeout=5, sleep=lambda s: None, monotonic=lambda: next(clock))

    drain.mark_running(work)
    try:
        drain.wait_drained(work, timeout=5, sleep=lambda s: None, monotonic=lambda: next(clock))
    except drain.DrainTimeout:
        pass
    else:
        raise AssertionError("a live supervisor that never drains must time out")
    assert (work / drain.DRAIN_REQUEST).exists()


def test_dead_supervisor_pid_counts_as_gone(tmp_path) -> None:
    import subprocess

    work = tmp_path / "work"
    work.mkdir()
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    (work / drain.SUPERVISOR).write_text(f"{proc.pid}\n")

    assert not drain.supervisor_alive(work)
    drain.supersede(work)
    assert not (work / drain.SUPERSEDED).exists()


def test_supervisor_records_itself_while_running_and_clears_on_exit(tmp_path, executor_script, fakes) -> None:
    hub = fakes.FakeHub(CAND)
    work = tmp_path / "work"
    seen: list[bool] = []

    def sleep(seconds: float) -> None:
        seen.append(drain.supervisor_alive(work))
        raise KeyboardInterrupt

    drain.request_drain(work)
    assert _supervisor(tmp_path, executor_script, hub, sleep).run() == 130
    assert seen == [True]
    assert not (work / drain.SUPERVISOR).exists()

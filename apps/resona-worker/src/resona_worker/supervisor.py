"""Worker supervisor: registers, claims, runs one job at a time, reports.

Lifecycle per job: download input (SHA-256 checked) -> run the execution
subprocess under the lease -> apply postprocess stages, each gated by a fresh
hub policy check -> upload the validated result or report a bounded failure.
Text jobs (postprocess-only) skip the subprocess: the UTF-8 input goes straight
into the postprocess stages.
A background ``LeaseKeeper`` renews the lease for the whole job, including
postprocess. The main thread checks it between steps and while the subprocess
runs; on loss or cancellation the subprocess group is killed and nothing is
uploaded.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import shutil
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError
from resona_job_core import (
    Candidate,
    Endpoint,
    ExecutionPolicy,
    JobResult,
    PolicyDenied,
    Provenance,
    StageProvenance,
)
from resona_postprocess.llm import PostprocessStepFailed
from resona_postprocess.pipeline import PostprocessResult, build_pipeline, step_endpoint_id
from resona_postprocess.profile import Profile, ProfileError

from .config import WorkerConfig, runtime_version
from .drain import (
    clear_drained,
    clear_running,
    drain_requested,
    mark_drained,
    mark_running,
    take_superseded,
)
from .execute import ExitCode, mentions_out_of_memory, subprocess_env
from .hub import (
    MAX_RESULT_BYTES,
    ClaimedJob,
    HubClient,
    HubError,
    HubRejected,
    HubUnavailable,
    InputTooLarge,
    InvalidCredentials,
    ProtocolIncompatible,
    StaleLease,
    lease_deadline_mono,
    sha256_file,
)

log = logging.getLogger("resona_worker.supervisor")

_NOT_APPROVED_RETRY_SECONDS = 30.0
_BACKOFF_MIN = 1.0
_BACKOFF_MAX = 30.0
_DRAIN_POLL_SECONDS = 2.0
_FAIL_MESSAGE_MAX = 500
_LEASE_RETRY_SECONDS = 5.0
_KILL_WAIT_SECONDS = 5.0
_STAGE_LLM_TYPES = frozenset({"llm", "extract"})
_OUT_OF_MEMORY_RC = frozenset({int(ExitCode.OUT_OF_MEMORY), -9})
_STDERR_SCAN_BYTES = 64 * 1024
_PROBE_TIMEOUT_SECONDS = 120.0
_UNLOAD_TIMEOUT_SECONDS = 10.0


class LeaseLost(Exception):
    """The lease can no longer be used: stale, expired locally, or cancelled."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class JobFailure(Exception):
    """A bounded, reportable failure of one job."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        withdraw: Candidate | None = None,
        message: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.withdraw = withdraw
        self.message = (message or code.replace("_", " "))[:_FAIL_MESSAGE_MAX]


class LeaseKeeper:
    """Renews one job's lease from a background thread.

    ``check()`` is called by the main thread; it raises ``LeaseLost`` when the
    hub fenced the lease, the job was cancelled, or the locally computed
    deadline has passed. Credential and protocol failures are re-raised as-is.
    """

    def __init__(
        self,
        hub: HubClient,
        job_id: int,
        lease_token: str,
        *,
        deadline: float,
        interval: float,
        monotonic: Callable[[], float],
        wall: Callable[[], float],
    ) -> None:
        self._hub = hub
        self._job_id = job_id
        self._token = lease_token
        self._interval = interval
        self._retry_interval = min(interval, _LEASE_RETRY_SECONDS)
        self._monotonic = monotonic
        self._wall = wall
        self._lock = threading.Lock()
        self._deadline = deadline
        self._reason: str | None = None
        self._error: Exception | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=f"lease-{job_id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=1.0)

    def check(self) -> None:
        with self._lock:
            error, reason, deadline = self._error, self._reason, self._deadline
        if error is not None:
            raise error
        if reason is None and self._monotonic() >= deadline:
            reason = "lease_expired"
        if reason is not None:
            raise LeaseLost(reason)

    def _set_reason(self, reason: str) -> None:
        with self._lock:
            self._reason = reason

    def _loop(self) -> None:
        delay = self._interval
        while not self._stop.wait(delay):
            started_mono, started_wall = self._monotonic(), self._wall()
            try:
                reply = self._hub.job_heartbeat(self._job_id, self._token, max_attempts=1)
            except HubUnavailable:
                delay = self._retry_interval
                continue
            except StaleLease:
                self._set_reason("stale_lease")
                return
            except HubError as exc:
                with self._lock:
                    self._error = exc
                return
            deadline = lease_deadline_mono(started_mono, started_wall, reply.lease_until)
            with self._lock:
                self._deadline = deadline
            if reply.cancel_requested:
                self._set_reason("cancel_requested")
                return
            delay = self._interval


class Supervisor:
    def __init__(
        self,
        config: WorkerConfig,
        *,
        hub: HubClient | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        probe: Callable[[str], bool] | None = None,
        endpoint_transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._hub = hub or HubClient(config.hub_url, config.token, sleep=sleep)
        self._monotonic = monotonic
        self._wall = wall
        self._sleep = sleep
        self._probe = probe or self._probe_with_executor
        self._endpoint_transport = endpoint_transport
        self._withdrawn: set[Candidate] = set()
        self._approved_private = False
        self._worker_id: str | None = None
        self._jobs_dir = config.work_dir / "jobs"

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Create a clean work directory, discarding leftovers from a previous run.

        Tuples whose accelerator this host cannot use right now (a CUDA container started
        without a GPU, a missing driver) are withdrawn before the first heartbeat, so the
        hub never schedules them here and their jobs stay eligible for other workers.
        A worker left without any tuple still claims text (postprocess-only) jobs and
        says so once here.
        """
        self._config.work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.rmtree(self._jobs_dir, ignore_errors=True)
        self._jobs_dir.mkdir(mode=0o700)
        mark_running(self._config.work_dir)
        for accelerator in sorted({c.accelerator for c in self._config.capabilities} - {"cpu"}):
            if self._probe(accelerator):
                continue
            unavailable = {c for c in self._config.capabilities if c.accelerator == accelerator}
            self._withdrawn |= unavailable
            log.warning("%s is not available on this host; not offering %d tuple(s)", accelerator, len(unavailable))
        if all(c in self._withdrawn for c in self._config.capabilities):
            log.warning("no usable capability tuple on this host; serving text (postprocess-only) jobs only")

    def _probe_with_executor(self, accelerator: str) -> bool:
        """Ask the execution environment (where the ML frameworks live) whether ``accelerator`` works."""
        try:
            completed = subprocess.run(
                [*self._config.executor, "--probe-accelerator", accelerator],
                env=subprocess_env(private=True, base=os.environ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_PROBE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == ExitCode.OK

    def run(self, *, once: bool = False) -> int:
        """Serve claims until interrupted. ``once`` returns after one claim cycle."""
        try:
            return self._loop(once=once)
        except KeyboardInterrupt:
            return 130
        except InvalidCredentials:
            log.error("hub rejected the worker credential; stopping")
            return 1
        except ProtocolIncompatible:
            log.error("hub speaks an incompatible worker protocol; stopping")
            return 2
        finally:
            clear_running(self._config.work_dir)

    def _loop(self, *, once: bool) -> int:
        self.start()
        backoff = _BACKOFF_MIN
        while True:
            # Checked between jobs only, so a running job always finishes before an update.
            if take_superseded(self._config.work_dir):
                clear_drained(self._config.work_dir)
                log.info("release replaced by resona-worker update; exiting so the new release can start")
                return 0
            if drain_requested(self._config.work_dir):
                mark_drained(self._config.work_dir)
                if once:
                    return 0
                self._sleep(_DRAIN_POLL_SECONDS)
                continue
            clear_drained(self._config.work_dir)
            claimable = [c for c in self._config.capabilities if c not in self._withdrawn]
            try:
                reply = self._hub.heartbeat(claimable, ready=bool(claimable), runtime=runtime_version())
            except HubUnavailable:
                if once:
                    return 1
                self._sleep(backoff)
                backoff = min(_BACKOFF_MAX, backoff * 2)
                continue
            backoff = _BACKOFF_MIN
            self._worker_id = str(reply.worker_id)
            self._approved_private = reply.approved_private
            # Text (postprocess-only) jobs need no tuple, so an approved worker without
            # eligible tuples still claims them.
            eligible = [c for c in claimable if c in reply.approved_capabilities]
            if not reply.approved:
                if once:
                    return 0
                self._sleep(_NOT_APPROVED_RETRY_SECONDS)
                continue

            try:
                claimed = self._claim(eligible)
            except HubUnavailable:
                if once:
                    return 1
                self._sleep(backoff)
                backoff = min(_BACKOFF_MAX, backoff * 2)
                continue
            if claimed is None:
                if once:
                    return 0
                continue
            job, deadline = claimed
            self._execute(job, deadline)
            if once:
                return 0

    def _claim(self, capabilities: list[Candidate]) -> tuple[ClaimedJob, float] | None:
        started_mono, started_wall = self._monotonic(), self._wall()
        job = self._hub.claim(capabilities, wait=self._config.claim_wait_seconds)
        if job is None:
            return None
        return job, lease_deadline_mono(started_mono, started_wall, job.lease_until)

    # -- one job ------------------------------------------------------------

    def _execute(self, job: ClaimedJob, deadline: float) -> None:
        job_dir = self._jobs_dir / f"{job.job_id}-{job.attempt}"
        job_dir.mkdir(mode=0o700)
        keeper = LeaseKeeper(
            self._hub,
            job.job_id,
            job.lease_token,
            deadline=deadline,
            interval=self._config.lease_heartbeat_seconds,
            monotonic=self._monotonic,
            wall=self._wall,
        )
        keeper.start()
        try:
            self._run_job(job, keeper, job_dir)
        except LeaseLost as lost:
            log.info("job %s attempt %s stopped: %s", job.job_id, job.attempt, lost.reason)
        except JobFailure as failure:
            self._report_failure(job, keeper, failure)
        except HubUnavailable:
            log.warning("job %s attempt %s: hub unreachable; lease will expire", job.job_id, job.attempt)
        except StaleLease:
            log.info("job %s attempt %s: lease fenced by hub", job.job_id, job.attempt)
        except HubRejected as exc:
            log.warning("job %s attempt %s: hub rejected request (%s)", job.job_id, job.attempt, exc.code)
        finally:
            keeper.stop()
            shutil.rmtree(job_dir, ignore_errors=True)

    def _run_job(self, job: ClaimedJob, keeper: LeaseKeeper, job_dir: Path) -> None:
        snapshot = job.execution_snapshot
        if snapshot.get("kind") != job.kind or job.kind not in ("audio", "document", "text"):
            raise JobFailure("invalid_snapshot", retryable=False)
        candidate = None if job.kind == "text" else _candidate_of(snapshot)
        policy_block = snapshot.get("policy") or {}
        private = bool(policy_block.get("private", True))
        if private and not self._approved_private:
            # The hub only claims private jobs to approved-private workers; a
            # mismatch means approval changed mid-flight. Let another worker take it.
            raise JobFailure("policy_denied", retryable=True, message="worker not approved for private jobs")

        input_path = job_dir / "input.bin"
        keeper.check()
        try:
            input_sha = self._hub.download_input(job.job_id, job.lease_token, input_path)
        except InputTooLarge:
            raise JobFailure("input_too_large", retryable=False) from None
        if input_sha != job.input_sha256:
            raise JobFailure("input_hash_mismatch", retryable=False)
        if job.kind == "document":
            # Docling selects its backend by file suffix; inputs only ever get generated names.
            input_path = input_path.rename(job_dir / f"input{_document_suffix(input_path)}")
        profile_dict = self._fetch_postprocess(job, snapshot, keeper)

        if candidate is None:
            # Text job: the input is the text; no execution subprocess, only the profile.
            if profile_dict is None:
                raise JobFailure("invalid_snapshot", retryable=False, message="text job without a profile")
            result = _text_result(input_path, snapshot.get("language"))
        else:
            keeper.check()
            snapshot_path = job_dir / "snapshot.json"
            out_path = job_dir / "out.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "kind": job.kind,
                        "candidate": candidate.model_dump(mode="json"),
                        "language": snapshot.get("language"),
                        "options": snapshot.get("options") or {},
                    }
                ),
                encoding="utf-8",
            )
            rc = self._run_executor(job_dir, snapshot_path, input_path, out_path, keeper, private=private)
            _raise_for_exit(rc, candidate, job_dir / "engine.stderr")
            result = _read_result(out_path, job.kind)

        result = self._postprocess(job, result, snapshot, profile_dict, keeper)
        provenance = result.provenance.model_copy(
            update={
                "worker_id": self._worker_id,
                "runtime_version": runtime_version(),
                "settings_revision": snapshot.get("settings_revision"),
                "input_sha256": job.input_sha256,
            }
        )
        self._upload(job, keeper, result.model_copy(update={"provenance": provenance}), job_dir)

    # -- subprocess ---------------------------------------------------------

    def _run_executor(
        self,
        job_dir: Path,
        snapshot_path: Path,
        input_path: Path,
        out_path: Path,
        keeper: LeaseKeeper,
        *,
        private: bool,
    ) -> int:
        cmd = [
            *self._config.executor,
            "--snapshot",
            str(snapshot_path),
            "--input",
            str(input_path),
            "--out",
            str(out_path),
        ]
        env = subprocess_env(private=private, base=os.environ)
        with (job_dir / "engine.stderr").open("wb") as stderr:
            proc = subprocess.Popen(
                cmd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                start_new_session=True,
            )
        try:
            while (rc := proc.poll()) is None:
                keeper.check()
                time.sleep(self._config.poll_interval_seconds)
        except BaseException:
            _kill_group(proc)
            raise
        _kill_group(proc)
        return rc

    # -- postprocess --------------------------------------------------------

    def _fetch_postprocess(
        self, job: ClaimedJob, snapshot: dict[str, Any], keeper: LeaseKeeper
    ) -> dict[str, Any] | None:
        """Download the profile named by the snapshot and verify it against its pinned SHA-256."""
        pp = snapshot.get("postprocess")
        if not pp:
            return None
        expected = pp.get("sha256") if isinstance(pp, dict) else None
        if not isinstance(expected, str) or not expected:
            raise JobFailure("invalid_snapshot", retryable=False)
        keeper.check()
        try:
            document = self._hub.download_postprocess(job.job_id, job.lease_token)
        except InputTooLarge:
            raise JobFailure("invalid_snapshot", retryable=False) from None
        if not hmac.compare_digest(hashlib.sha256(document).hexdigest(), expected):
            raise JobFailure("invalid_snapshot", retryable=False, message="postprocess profile hash mismatch")
        try:
            profile_dict = json.loads(document)
        except ValueError:
            raise JobFailure("invalid_snapshot", retryable=False) from None
        if not isinstance(profile_dict, dict):
            raise JobFailure("invalid_snapshot", retryable=False)
        return profile_dict

    def _postprocess(
        self,
        job: ClaimedJob,
        result: JobResult,
        snapshot: dict[str, Any],
        profile_dict: dict[str, Any] | None,
        keeper: LeaseKeeper,
    ) -> JobResult:
        if profile_dict is None:
            return result
        try:
            steps = profile_dict["steps"]
            policy = _policy_of(snapshot.get("policy") or {})
        except (KeyError, TypeError, ValueError, ValidationError):
            raise JobFailure("invalid_snapshot", retryable=False) from None

        text = result.markdown
        warnings = list(result.warnings)
        structured = dict(result.structured)
        stages = list(result.provenance.stages)
        # Index of the last step calling each endpoint: its model is unloaded after that step.
        last_use: dict[str, int] = {}
        for idx, step in enumerate(steps):
            if isinstance(step, dict) and step.get("type") in _STAGE_LLM_TYPES:
                try:
                    last_use[step_endpoint_id(step)] = idx
                except PolicyDenied:
                    raise JobFailure("policy_denied", retryable=False) from None
        # Endpoints called and not yet unloaded; a failing job unloads them on the way out.
        loaded: set[str] = set()
        try:
            for idx, step in enumerate(steps):
                if not isinstance(step, dict):
                    raise JobFailure("invalid_snapshot", retryable=False)
                stage_name = str(step.get("name") or step.get("type") or "step")
                try:
                    single = Profile.from_dict({**profile_dict, "steps": [step]})
                    pipeline = build_pipeline(single, policy=policy)
                except PolicyDenied:
                    raise JobFailure("policy_denied", retryable=False) from None
                except (ProfileError, ValueError, KeyError, TypeError):
                    raise JobFailure("invalid_snapshot", retryable=False) from None

                endpoint_id: str | None = None
                if step.get("type") in _STAGE_LLM_TYPES:
                    endpoint_id = step_endpoint_id(step)
                    self._guard_stage(job, keeper, stage_name, endpoint_id)
                    loaded.add(endpoint_id)

                started = time.perf_counter()
                try:
                    run: PostprocessResult = pipeline.run(text)
                except PolicyDenied:
                    raise JobFailure("policy_denied", retryable=False) from None
                except PostprocessStepFailed as exc:
                    raise JobFailure("postprocess_failed", retryable=True, message=str(exc)) from None
                seconds = time.perf_counter() - started
                if endpoint_id is not None and last_use.get(endpoint_id) == idx:
                    loaded.discard(endpoint_id)
                    self._unload(policy.endpoint(endpoint_id))
                text = run.text
                warnings.extend(run.warnings)
                skipped = run.data.get("_skipped", [])
                warnings.extend(f"postprocess_step_skipped:{name}" for name in skipped)
                structured.update({key: value for key, value in run.data.items() if key != "_skipped"})
                if endpoint_id is not None and not skipped:
                    endpoint = policy.endpoint(endpoint_id)
                    if endpoint is None:
                        raise JobFailure("policy_denied", retryable=False)
                    stages.append(
                        StageProvenance(
                            stage="llm",
                            engine="litellm",
                            model=endpoint.model,
                            accelerator="external",
                            endpoint_id=endpoint.id,
                            seconds=seconds,
                        )
                    )
        finally:
            for endpoint_id in sorted(loaded):
                self._unload(policy.endpoint(endpoint_id))

        return result.model_copy(
            update={
                "markdown": text,
                "raw_markdown": result.markdown if text != result.markdown else None,
                "warnings": warnings,
                "structured": structured,
                "provenance": result.provenance.model_copy(update={"stages": stages}),
            }
        )

    def _guard_stage(self, job: ClaimedJob, keeper: LeaseKeeper, stage: str, endpoint_id: str) -> None:
        keeper.check()
        try:
            self._hub.policy_check(job.job_id, job.lease_token, stage=stage, endpoint_id=endpoint_id)
        except PolicyDenied:
            raise JobFailure("policy_denied", retryable=False) from None
        except HubUnavailable:
            raise JobFailure("hub_unreachable", retryable=True) from None
        keeper.check()

    def _unload(self, endpoint: Endpoint | None) -> None:
        """Best effort: ask the endpoint's model server to free the GPU (``unload_url``).

        ``Endpoint`` admits only loopback ``unload_url`` values. The request carries no
        content; the outcome is logged by endpoint id and status code and never fails
        the job. A non-2xx answer (e.g. 404/405 from a server without that route) is a
        warning: the model may still hold the GPU.
        """
        if endpoint is None or endpoint.unload_url is None:
            return
        try:
            with httpx.Client(
                transport=self._endpoint_transport, timeout=_UNLOAD_TIMEOUT_SECONDS, trust_env=False
            ) as client:
                response = client.post(endpoint.unload_url)
        except httpx.HTTPError as exc:
            log.warning("endpoint %s: unload request failed (%s)", endpoint.id, type(exc).__name__)
            return
        if response.is_success:
            log.info("endpoint %s: unload accepted (HTTP %d)", endpoint.id, response.status_code)
        else:
            log.warning("endpoint %s: unload failed (HTTP %d)", endpoint.id, response.status_code)

    # -- reporting ----------------------------------------------------------

    def _upload(self, job: ClaimedJob, keeper: LeaseKeeper, result: JobResult, job_dir: Path) -> None:
        keeper.check()
        body_path = job_dir / "result.json"
        # An unset raw_markdown is left out so hubs that predate the field accept unpolished results.
        unset = {"raw_markdown"} if result.raw_markdown is None else None
        body_path.write_text(result.model_dump_json(exclude=unset), encoding="utf-8")
        sha, size = sha256_file(body_path)
        if size > MAX_RESULT_BYTES:
            raise JobFailure("result_too_large", retryable=False)
        try:
            self._hub.upload_result(job.job_id, job.lease_token, sha256=sha, size=size, body_path=body_path)
        except StaleLease:
            log.info("job %s attempt %s: result rejected as stale; discarded", job.job_id, job.attempt)

    def _report_failure(self, job: ClaimedJob, keeper: LeaseKeeper, failure: JobFailure) -> None:
        try:
            keeper.check()
        except LeaseLost:
            return
        if failure.withdraw is not None:
            self._withdrawn.add(failure.withdraw)
        try:
            self._hub.fail(
                job.job_id,
                job.lease_token,
                code=failure.code,
                message=failure.message,
                retryable=failure.retryable,
                withdraw_capability=failure.withdraw,
            )
        except StaleLease:
            log.info("job %s attempt %s: failure report rejected as stale", job.job_id, job.attempt)


def _document_suffix(path: Path) -> str:
    """Suffix for a document input from its magic bytes (the hub admitted only these types)."""
    try:
        with path.open("rb") as handle:
            head = handle.read(8)
    except OSError:
        raise JobFailure("unsupported_input", retryable=False, message="input could not be processed") from None
    if head.startswith(b"%PDF-"):
        return ".pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return ".tif"
    if head.startswith(b"PK\x03\x04"):
        return ".docx"  # the hub admits a ZIP document only after finding a Word package in it
    raise JobFailure("unsupported_input", retryable=False, message="input could not be processed")


def _text_result(path: Path, language: Any) -> JobResult:
    """The unprocessed result of a text job: its UTF-8 input as ``raw_text`` and ``markdown``."""
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        raise JobFailure("unsupported_input", retryable=False, message="input is not UTF-8 text") from None
    return JobResult(
        kind="text",
        raw_text=text,
        markdown=text,
        language=language if isinstance(language, str) else None,
        provenance=Provenance(),
    )


def _candidate_of(snapshot: dict[str, Any]) -> Candidate:
    try:
        return Candidate.model_validate(snapshot["candidate"])
    except (KeyError, TypeError, ValidationError):
        raise JobFailure("invalid_snapshot", retryable=False) from None


def _policy_of(block: dict[str, Any]) -> ExecutionPolicy:
    return ExecutionPolicy(
        private=bool(block.get("private", True)),
        cloud_enabled=bool(block.get("cloud_enabled", False)),
        approved_endpoint_ids=tuple(block.get("approved_endpoint_ids", ())),
        endpoints=tuple(Endpoint.model_validate(e) for e in block.get("endpoints", ())),
    )


def _read_result(out_path: Path, kind: str) -> JobResult:
    try:
        st = os.lstat(out_path)
    except OSError:
        raise JobFailure("invalid_result", retryable=True) from None
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_RESULT_BYTES:
        raise JobFailure("invalid_result", retryable=True)
    try:
        result = JobResult.model_validate_json(out_path.read_bytes())
    except (OSError, ValidationError, ValueError):
        raise JobFailure("invalid_result", retryable=True) from None
    if result.kind != kind:
        raise JobFailure("invalid_result", retryable=True)
    return result


def _raise_for_exit(rc: int, candidate: Candidate, stderr_path: Path) -> None:
    if rc == ExitCode.OK:
        return
    if rc == ExitCode.UNSUPPORTED_INPUT:
        raise JobFailure("unsupported_input", retryable=False, message="input could not be processed")
    if rc == ExitCode.UNSUPPORTED_CAPABILITY:
        raise JobFailure("unsupported_capability", retryable=True, withdraw=candidate, message="worker lacks this capability")
    if rc in _OUT_OF_MEMORY_RC or (rc != ExitCode.INVALID_SNAPSHOT and _stderr_reports_oom(stderr_path)):
        # SIGKILL (jetsam/OOM killer), MemoryError, or a native abort such as MLX's
        # ``[metal::malloc]``: requeue for another worker, stop offering the tuple here.
        raise JobFailure("out_of_memory", retryable=True, withdraw=candidate, message="out of memory")
    if rc == ExitCode.INVALID_SNAPSHOT:
        raise JobFailure("invalid_snapshot", retryable=False)
    raise JobFailure("engine_error", retryable=True, message="engine failed")


def _stderr_reports_oom(path: Path) -> bool:
    """Check the tail of the engine's stderr for out-of-memory markers. Never logged or uploaded."""
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, handle.seek(0, os.SEEK_END) - _STDERR_SCAN_BYTES))
            tail = handle.read()
    except OSError:
        return False
    return mentions_out_of_memory(tail.decode("utf-8", errors="replace"))


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        # macOS answers EPERM when the group still exists but holds only zombies (the
        # executor exited and was reaped): nothing is left to kill. Every member runs as
        # this user, so EPERM never means a live process we failed to stop.
        pass
    try:
        proc.wait(timeout=_KILL_WAIT_SECONDS)
    except subprocess.TimeoutExpired:
        log.error("execution subprocess did not exit after SIGKILL")


__all__ = ["JobFailure", "LeaseKeeper", "LeaseLost", "Supervisor"]
